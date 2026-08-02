//! Generation-scoped concurrent stdio request router.
//!
//! The router owns exactly one writer and one stdout reader.  Callers only
//! register a bounded pending waiter and enqueue a frame; no caller performs
//! pipe I/O while holding the pending lock.

use std::{
    collections::HashMap,
    fs::File,
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde_json::{json, Value};

use crate::{
    core_host_protocol::{encode_frame, validate_envelope, FrameDecoder, EVENT_PROTOCOL_MINOR},
    platform::{ManagedPipeReadOutcome, ManagedPipeReader},
};

pub const PENDING_LIMIT: usize = 64;
pub const WRITER_QUEUE_LIMIT: usize = 32;
pub const EVENT_QUEUE_LIMIT: usize = 32;
pub const CRITICAL_EVENT_QUEUE_LIMIT: usize = 8;
const READ_SLICE: Duration = Duration::from_millis(25);
const CLOSE_TIMEOUT: Duration = Duration::from_millis(500);

enum WriterCommand {
    Frame(Value),
    Stop,
}

struct Pending {
    name: String,
    is_hello: bool,
    protocol_minor: u64,
    waiter: mpsc::Sender<Result<Value, String>>,
}

struct SequencedEvent {
    sequence: u64,
    critical: bool,
    message: Value,
}

struct Shared {
    generation_id: String,
    generation_credential: String,
    pending: Mutex<HashMap<String, Pending>>,
    writer: SyncSender<WriterCommand>,
    events: SyncSender<SequencedEvent>,
    critical_events: SyncSender<SequencedEvent>,
    next_event_sequence: AtomicU64,
    event_count: AtomicUsize,
    critical_event_count: AtomicUsize,
    stopped: AtomicBool,
    event_capable: AtomicBool,
    fatal: Mutex<Option<String>>,
}

pub struct CoreHostRouter {
    shared: Arc<Shared>,
    event_receiver: Receiver<SequencedEvent>,
    critical_event_receiver: Receiver<SequencedEvent>,
    event_head: Mutex<Option<SequencedEvent>>,
    critical_event_head: Mutex<Option<SequencedEvent>>,
    writer_thread: Option<JoinHandle<()>>,
    reader_thread: Option<JoinHandle<()>>,
}

#[derive(Clone)]
pub struct CoreHostRouterHandle {
    shared: Arc<Shared>,
}

impl CoreHostRouter {
    pub fn new(
        stdin: File,
        stdout: Box<dyn ManagedPipeReader>,
        generation_id: impl Into<String>,
        generation_credential: impl Into<String>,
    ) -> Result<Self, String> {
        let generation_id = generation_id.into();
        let generation_credential = generation_credential.into();
        if generation_id.trim().is_empty() || generation_credential.trim().is_empty() {
            return Err("Core Host Router identity must not be empty".to_string());
        }
        let (writer, writer_rx) = mpsc::sync_channel(WRITER_QUEUE_LIMIT);
        let (events, event_receiver) = mpsc::sync_channel(EVENT_QUEUE_LIMIT);
        let (critical_events, critical_event_receiver) =
            mpsc::sync_channel(CRITICAL_EVENT_QUEUE_LIMIT);
        let shared = Arc::new(Shared {
            generation_id,
            generation_credential,
            pending: Mutex::new(HashMap::new()),
            writer,
            events,
            critical_events,
            next_event_sequence: AtomicU64::new(1),
            event_count: AtomicUsize::new(0),
            critical_event_count: AtomicUsize::new(0),
            stopped: AtomicBool::new(false),
            event_capable: AtomicBool::new(false),
            fatal: Mutex::new(None),
        });
        let writer_shared = Arc::clone(&shared);
        let writer_thread = thread::Builder::new()
            .name("sakura-core-host-router-writer".to_string())
            .spawn(move || writer_loop(stdin, writer_rx, writer_shared))
            .map_err(|error| format!("router writer thread failed: {error}"))?;
        let reader_shared = Arc::clone(&shared);
        let reader_thread = thread::Builder::new()
            .name("sakura-core-host-router-reader".to_string())
            .spawn(move || reader_loop(stdout, reader_shared))
            .map_err(|error| format!("router reader thread failed: {error}"))?;
        Ok(Self {
            shared,
            event_receiver,
            critical_event_receiver,
            event_head: Mutex::new(None),
            critical_event_head: Mutex::new(None),
            writer_thread: Some(writer_thread),
            reader_thread: Some(reader_thread),
        })
    }

    pub fn handle(&self) -> CoreHostRouterHandle {
        CoreHostRouterHandle {
            shared: Arc::clone(&self.shared),
        }
    }

    pub fn enable_events(&self, enabled: bool) {
        self.shared.event_capable.store(enabled, Ordering::Release);
    }

    pub fn recv_event_timeout(&self, timeout: Duration) -> Result<Option<Value>, String> {
        let deadline = Instant::now() + timeout;
        loop {
            self.fill_event_heads()?;
            if let Some(event) = self.take_next_event()? {
                let count = if event.critical {
                    &self.shared.critical_event_count
                } else {
                    &self.shared.event_count
                };
                count.fetch_sub(1, Ordering::AcqRel);
                return Ok(Some(event.message));
            }
            match self.event_receiver.recv_timeout(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(READ_SLICE),
            ) {
                Ok(event) => {
                    *self
                        .event_head
                        .lock()
                        .map_err(|_| "EVENT_QUEUE_LOCK_FAILED: event head unavailable")? =
                        Some(event);
                }
                Err(RecvTimeoutError::Timeout) if Instant::now() < deadline => continue,
                Err(RecvTimeoutError::Timeout) => return Ok(None),
                Err(RecvTimeoutError::Disconnected) => {
                    if let Some(error) = self.fatal() {
                        return Err(error);
                    }
                    if Instant::now() >= deadline {
                        return Ok(None);
                    }
                }
            }
        }
    }

    fn fill_event_heads(&self) -> Result<(), String> {
        let mut event_head = self
            .event_head
            .lock()
            .map_err(|_| "EVENT_QUEUE_LOCK_FAILED: event head unavailable".to_string())?;
        if event_head.is_none() {
            if let Ok(event) = self.event_receiver.try_recv() {
                *event_head = Some(event);
            }
        }
        drop(event_head);

        let mut critical_head = self
            .critical_event_head
            .lock()
            .map_err(|_| "EVENT_QUEUE_LOCK_FAILED: critical event head unavailable".to_string())?;
        if critical_head.is_none() {
            if let Ok(event) = self.critical_event_receiver.try_recv() {
                *critical_head = Some(event);
            }
        }
        Ok(())
    }

    fn take_next_event(&self) -> Result<Option<SequencedEvent>, String> {
        let mut event_head = self
            .event_head
            .lock()
            .map_err(|_| "EVENT_QUEUE_LOCK_FAILED: event head unavailable".to_string())?;
        let mut critical_head = self
            .critical_event_head
            .lock()
            .map_err(|_| "EVENT_QUEUE_LOCK_FAILED: critical event head unavailable".to_string())?;
        let take_critical = match (event_head.as_ref(), critical_head.as_ref()) {
            (None, None) => return Ok(None),
            (None, Some(_)) => true,
            (Some(_), None) => false,
            (Some(event), Some(critical)) => critical.sequence < event.sequence,
        };
        Ok(if take_critical {
            critical_head.take()
        } else {
            event_head.take()
        })
    }

    pub fn fatal(&self) -> Option<String> {
        self.shared
            .fatal
            .lock()
            .ok()
            .and_then(|fatal| fatal.clone())
    }

    pub fn close(&mut self) -> Result<(), String> {
        if self.shared.stopped.swap(true, Ordering::AcqRel) {
            let _ = self.shared.writer.try_send(WriterCommand::Stop);
            return self.join_threads();
        }
        invalidate_all(&self.shared, "GENERATION_INVALIDATED: Router closed");
        let _ = self.shared.writer.try_send(WriterCommand::Stop);
        self.join_threads()
    }

    fn join_threads(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + CLOSE_TIMEOUT;
        let mut error = None;
        for thread in [&mut self.writer_thread, &mut self.reader_thread] {
            let Some(handle) = thread.take() else {
                continue;
            };
            while !handle.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if !handle.is_finished() {
                error = Some(format!(
                    "ROUTER_CLOSE_TIMEOUT: {} did not stop",
                    handle.thread().name().unwrap_or("router thread")
                ));
                *thread = Some(handle);
                continue;
            }
            if handle.join().is_err() && error.is_none() {
                error = Some("ROUTER_THREAD_FAILED: router thread panicked".to_string());
            }
        }
        error.or_else(|| self.fatal()).map_or(Ok(()), Err)
    }
}

impl Drop for CoreHostRouter {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl CoreHostRouterHandle {
    pub fn request(&self, message: Value, deadline: Duration) -> Result<Value, String> {
        validate_envelope(&message).map_err(|error| error.to_string())?;
        if deadline.is_zero() {
            return Err("Core Host request deadline must be positive".to_string());
        }
        let object = message
            .as_object()
            .ok_or_else(|| "INVALID_ENVELOPE: request must be an object".to_string())?;
        if object.get("kind").and_then(Value::as_str) != Some("request") {
            return Err("INVALID_ENVELOPE: Router accepts requests only".to_string());
        }
        if object.get("generationId").and_then(Value::as_str)
            != Some(self.shared.generation_id.as_str())
            || object.get("generationCredential").and_then(Value::as_str)
                != Some(self.shared.generation_credential.as_str())
        {
            return Err(
                "GENERATION_CREDENTIAL_MISMATCH: request identity is stale or invalid".to_string(),
            );
        }
        let id = object
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_ENVELOPE: request id is missing".to_string())?
            .to_string();
        let name = object
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_ENVELOPE: request name is missing".to_string())?
            .to_string();
        let protocol_minor = object
            .get("protocolMinor")
            .and_then(Value::as_u64)
            .ok_or_else(|| "INVALID_ENVELOPE: request protocol minor is missing".to_string())?;
        let (waiter, receiver) = mpsc::channel();
        {
            let mut pending = self.shared.pending.lock().map_err(|_| {
                "ROUTER_PENDING_LOCK_FAILED: pending registry unavailable".to_string()
            })?;
            if pending.len() >= PENDING_LIMIT {
                return Err("PENDING_LIMIT_EXCEEDED: pending request capacity is full".to_string());
            }
            if pending.contains_key(&id) {
                return Err("DUPLICATE_REQUEST_ID: request id is already pending".to_string());
            }
            pending.insert(
                id.clone(),
                Pending {
                    name,
                    is_hello: object.get("name").and_then(Value::as_str) == Some("system.hello"),
                    protocol_minor,
                    waiter,
                },
            );
        }
        match self.shared.writer.try_send(WriterCommand::Frame(message)) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                remove_pending(&self.shared, &id);
                return Err("WRITER_QUEUE_FULL: writer queue is full".to_string());
            }
            Err(TrySendError::Disconnected(_)) => {
                remove_pending(&self.shared, &id);
                return Err("TRANSPORT_WRITE_FAILED: writer is closed".to_string());
            }
        }
        match receiver.recv_timeout(deadline) {
            Ok(result) => result,
            Err(RecvTimeoutError::Timeout) => {
                remove_pending(&self.shared, &id);
                Err("REQUEST_DEADLINE_EXCEEDED: request exceeded its deadline".to_string())
            }
            Err(RecvTimeoutError::Disconnected) => Err(self
                .fatal()
                .unwrap_or_else(|| "GENERATION_INVALIDATED: request was cleared".to_string())),
        }
    }

    pub fn pending_len(&self) -> usize {
        self.shared
            .pending
            .lock()
            .map_or(0, |pending| pending.len())
    }

    fn fatal(&self) -> Option<String> {
        self.shared
            .fatal
            .lock()
            .ok()
            .and_then(|fatal| fatal.clone())
    }
}

fn writer_loop(mut stdin: File, receiver: mpsc::Receiver<WriterCommand>, shared: Arc<Shared>) {
    while let Ok(command) = receiver.recv() {
        if shared.stopped.load(Ordering::Acquire) {
            return;
        }
        match command {
            WriterCommand::Frame(message) => {
                let frame = match encode_frame(&message) {
                    Ok(frame) => frame,
                    Err(error) => {
                        fail_all(&shared, error.to_string());
                        return;
                    }
                };
                if let Err(error) = std::io::Write::write_all(&mut stdin, &frame)
                    .and_then(|_| std::io::Write::flush(&mut stdin))
                {
                    fail_all(&shared, format!("TRANSPORT_WRITE_FAILED: {error}"));
                    return;
                }
            }
            WriterCommand::Stop => return,
        }
    }
}

fn reader_loop(mut stdout: Box<dyn ManagedPipeReader>, shared: Arc<Shared>) {
    let mut decoder = FrameDecoder::default();
    let cancelled = AtomicBool::new(false);
    let mut buffer = [0_u8; 8192];
    loop {
        if shared.stopped.load(Ordering::Acquire) {
            return;
        }
        match stdout.read_until(&mut buffer, Instant::now() + READ_SLICE, &cancelled) {
            Ok(ManagedPipeReadOutcome::Read(count)) if count > 0 => {
                match decoder.feed(&buffer[..count]) {
                    Ok(messages) => {
                        for message in messages {
                            if let Err(error) = route_message(&shared, message) {
                                fail_all(&shared, error);
                                return;
                            }
                        }
                    }
                    Err(error) => {
                        fail_all(&shared, error.to_string());
                        return;
                    }
                }
            }
            Ok(ManagedPipeReadOutcome::Read(_)) => {
                fail_all(
                    &shared,
                    "TRANSPORT_READ_FAILED: stdout returned an empty read",
                );
                return;
            }
            Ok(ManagedPipeReadOutcome::TimedOut) => continue,
            Ok(ManagedPipeReadOutcome::Cancelled) => return,
            Ok(ManagedPipeReadOutcome::Eof) => {
                let error = decoder.finish().map_or_else(
                    |error| error.to_string(),
                    |_| "STDOUT_EOF: Core Host stdout reached EOF".to_string(),
                );
                fail_all(&shared, error);
                return;
            }
            Err(error) => {
                fail_all(&shared, format!("TRANSPORT_READ_FAILED: {error}"));
                return;
            }
        }
    }
}

fn route_message(shared: &Arc<Shared>, message: Value) -> Result<(), String> {
    let object = message
        .as_object()
        .ok_or_else(|| "INVALID_ENVELOPE: message must be an object".to_string())?;
    if object.get("generationId").and_then(Value::as_str) != Some(shared.generation_id.as_str())
        || object.get("generationCredential").and_then(Value::as_str)
            != Some(shared.generation_credential.as_str())
    {
        return Err(
            "GENERATION_CREDENTIAL_MISMATCH: response identity is stale or invalid".to_string(),
        );
    }
    match object.get("kind").and_then(Value::as_str) {
        Some("event") => {
            if !shared.event_capable.load(Ordering::Acquire) {
                return Err(
                    "CAPABILITY_NEGOTIATION_FAILED: event capability was not negotiated"
                        .to_string(),
                );
            }
            let id = object
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| "INVALID_ENVELOPE: event id is missing".to_string())?;
            if !shared
                .pending
                .lock()
                .map_err(|_| {
                    "ROUTER_PENDING_LOCK_FAILED: pending registry unavailable".to_string()
                })?
                .contains_key(id)
            {
                return Err("UNKNOWN_REQUEST_ID: event id is not pending".to_string());
            }
            let critical = matches!(
                object.get("name").and_then(Value::as_str),
                Some("chat.completed" | "chat.failed" | "chat.cancelled")
            );
            let target = if critical {
                &shared.critical_events
            } else {
                &shared.events
            };
            let (count, limit) = if critical {
                (&shared.critical_event_count, CRITICAL_EVENT_QUEUE_LIMIT)
            } else {
                (&shared.event_count, EVENT_QUEUE_LIMIT)
            };
            count
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
                    (current < limit).then_some(current + 1)
                })
                .map_err(|_| "EVENT_QUEUE_FULL: event queue is full".to_string())?;
            let event = SequencedEvent {
                sequence: shared.next_event_sequence.fetch_add(1, Ordering::Relaxed),
                critical,
                message,
            };
            if target.try_send(event).is_err() {
                count.fetch_sub(1, Ordering::AcqRel);
                return Err("EVENT_QUEUE_FULL: event queue is full".to_string());
            }
            Ok(())
        }
        Some("response") => {
            let id = object
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| "INVALID_ENVELOPE: response id is missing".to_string())?;
            let (expected_name, expected_minor, is_hello) = shared
                .pending
                .lock()
                .map_err(|_| {
                    "ROUTER_PENDING_LOCK_FAILED: pending registry unavailable".to_string()
                })?
                .get(id)
                .map(|pending| {
                    (
                        pending.name.clone(),
                        pending.protocol_minor,
                        pending.is_hello,
                    )
                })
                .ok_or_else(|| "UNKNOWN_REQUEST_ID: response id is not pending".to_string())?;
            if object.get("name").and_then(Value::as_str) != Some(expected_name.as_str()) {
                return Err(
                    "INVALID_RESPONSE_NAME: response name did not match request".to_string()
                );
            }
            if !is_hello
                && object.get("protocolMinor").and_then(Value::as_u64) != Some(expected_minor)
            {
                return Err(
                    "INVALID_NEGOTIATION: response minor changed after handshake".to_string(),
                );
            }
            let pending = remove_pending_entry(shared, id)
                .ok_or_else(|| "UNKNOWN_REQUEST_ID: response id is not pending".to_string())?;
            let _ = pending.waiter.send(Ok(message));
            Ok(())
        }
        _ => Err("INVALID_ENVELOPE: Router received an unsupported message kind".to_string()),
    }
}

fn remove_pending(shared: &Arc<Shared>, id: &str) {
    let _ = shared.pending.lock().map(|mut pending| pending.remove(id));
}

fn remove_pending_entry(shared: &Arc<Shared>, id: &str) -> Option<Pending> {
    shared
        .pending
        .lock()
        .ok()
        .and_then(|mut pending| pending.remove(id))
}

fn fail_all(shared: &Arc<Shared>, error: impl Into<String>) {
    shared.stopped.store(true, Ordering::Release);
    let _ = shared.writer.try_send(WriterCommand::Stop);
    let error = error.into();
    if let Ok(mut fatal) = shared.fatal.lock() {
        if fatal.is_none() {
            *fatal = Some(error.clone());
        }
    }
    let waiters = shared
        .pending
        .lock()
        .map(|mut pending| {
            pending
                .drain()
                .map(|(_, pending)| pending.waiter)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for waiter in waiters {
        let _ = waiter.send(Err(error.clone()));
    }
}

fn invalidate_all(shared: &Arc<Shared>, error: impl Into<String>) {
    let error = error.into();
    let waiters = shared
        .pending
        .lock()
        .map(|mut pending| {
            pending
                .drain()
                .map(|(_, pending)| pending.waiter)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for waiter in waiters {
        let _ = waiter.send(Err(error.clone()));
    }
}

#[allow(dead_code)]
fn _event_identity_example() -> Value {
    json!({"kind": "event", "protocolMinor": EVENT_PROTOCOL_MINOR})
}

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        sync::{atomic::AtomicBool, Arc, Mutex},
        thread,
        time::{Duration, Instant},
    };

    use serde_json::{json, Value};

    use super::{
        CoreHostRouter, CRITICAL_EVENT_QUEUE_LIMIT, EVENT_QUEUE_LIMIT, PENDING_LIMIT,
        WRITER_QUEUE_LIMIT,
    };
    use crate::{
        core_host_protocol::encode_frame,
        platform::{ManagedPipeReadOutcome, ManagedPipeReader, PlatformResult},
    };

    const GENERATION: &str = "00000000-0000-4000-8000-000000002201";
    const CREDENTIAL: &str = "22222222222222222222222222222222";

    struct ReleasedReader {
        released: Arc<AtomicBool>,
        chunks: Arc<Mutex<VecDeque<Vec<u8>>>>,
    }

    impl ManagedPipeReader for ReleasedReader {
        fn read_until(
            &mut self,
            buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            if !self.released.load(std::sync::atomic::Ordering::Acquire) {
                thread::sleep(Duration::from_millis(1));
                return Ok(ManagedPipeReadOutcome::TimedOut);
            }
            let Some(mut chunk) = self.chunks.lock().expect("chunks").pop_front() else {
                thread::sleep(Duration::from_millis(1));
                return Ok(ManagedPipeReadOutcome::TimedOut);
            };
            let count = chunk.len().min(buffer.len());
            buffer[..count].copy_from_slice(&chunk[..count]);
            if count < chunk.len() {
                self.chunks
                    .lock()
                    .expect("chunks")
                    .push_front(chunk.split_off(count));
            }
            Ok(ManagedPipeReadOutcome::Read(count))
        }
    }

    fn request(id: &str, name: &str) -> Value {
        json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "request",
            "generationId": GENERATION,
            "generationCredential": CREDENTIAL,
            "id": id,
            "name": name,
            "payload": {},
            "deadlineMs": 3000,
            "priority": "interactive"
        })
    }

    fn response(id: &str, name: &str) -> Value {
        json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "response",
            "generationId": GENERATION,
            "generationCredential": CREDENTIAL,
            "id": id,
            "name": name,
            "payload": {"id": id},
            "ok": true
        })
    }

    fn router_with_messages(messages: Vec<Value>) -> (CoreHostRouter, Arc<AtomicBool>) {
        let released = Arc::new(AtomicBool::new(false));
        let bytes = messages
            .into_iter()
            .flat_map(|message| encode_frame(&message).expect("frame"))
            .collect::<Vec<_>>();
        let router = CoreHostRouter::new(
            null_file(),
            Box::new(ReleasedReader {
                released: Arc::clone(&released),
                chunks: Arc::new(Mutex::new(VecDeque::from([bytes]))),
            }),
            GENERATION,
            CREDENTIAL,
        )
        .expect("router");
        (router, released)
    }

    #[cfg(unix)]
    fn null_file() -> std::fs::File {
        std::fs::File::options()
            .read(true)
            .write(true)
            .open("/dev/null")
            .expect("null stdin sink")
    }

    #[cfg(windows)]
    fn null_file() -> std::fs::File {
        std::fs::File::options()
            .read(true)
            .write(true)
            .open("NUL")
            .expect("null stdin sink")
    }

    #[test]
    fn reverse_responses_complete_the_matching_waiters() {
        let (mut router, released) = router_with_messages(vec![
            response("second", "fixture.blocking"),
            response("first", "fixture.blocking"),
        ]);
        let first_handle = router.handle();
        let second_handle = router.handle();
        let first = thread::spawn(move || {
            first_handle.request(request("first", "fixture.blocking"), Duration::from_secs(1))
        });
        let second = thread::spawn(move || {
            second_handle.request(
                request("second", "fixture.blocking"),
                Duration::from_secs(1),
            )
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while router.handle().pending_len() != 2 && Instant::now() < deadline {
            thread::yield_now();
        }
        released.store(true, std::sync::atomic::Ordering::Release);
        assert_eq!(first.join().unwrap().unwrap()["payload"]["id"], "first");
        assert_eq!(second.join().unwrap().unwrap()["payload"]["id"], "second");
        router.close().expect("clean router close");
    }

    #[test]
    fn event_is_delivered_without_completing_the_waiter() {
        let event = json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "event",
            "generationId": GENERATION,
            "generationCredential": CREDENTIAL,
            "id": "one",
            "name": "fixture.completed",
            "payload": {"state": "completed"}
        });
        let (mut router, released) =
            router_with_messages(vec![event.clone(), response("one", "fixture.blocking")]);
        router.enable_events(true);
        let handle = router.handle();
        let waiter = thread::spawn(move || {
            handle.request(request("one", "fixture.blocking"), Duration::from_secs(1))
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while router.handle().pending_len() != 1 && Instant::now() < deadline {
            thread::yield_now();
        }
        released.store(true, std::sync::atomic::Ordering::Release);
        assert_eq!(
            router.recv_event_timeout(Duration::from_secs(1)).unwrap(),
            Some(event)
        );
        assert_eq!(waiter.join().unwrap().unwrap()["id"], "one");
        router.close().expect("clean router close");
    }

    #[test]
    fn critical_chat_terminal_does_not_overtake_its_started_event() {
        let (mut router, _released) = router_with_messages(Vec::new());
        router.enable_events(true);
        let (waiter, _receiver) = std::sync::mpsc::channel();
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .insert(
                "chat-fast".to_string(),
                super::Pending {
                    name: "chat.send".to_string(),
                    is_hello: false,
                    protocol_minor: 2,
                    waiter,
                },
            );
        let event = |name: &str| {
            json!({
                "protocolMajor": 2,
                "protocolMinor": 2,
                "kind": "event",
                "generationId": GENERATION,
                "generationCredential": CREDENTIAL,
                "id": "chat-fast",
                "name": name,
                "payload": {"operationId": "chat-fast"}
            })
        };
        super::route_message(&router.shared, event("chat.started")).expect("started queued");
        super::route_message(&router.shared, event("chat.completed")).expect("terminal queued");

        assert_eq!(
            router
                .recv_event_timeout(Duration::ZERO)
                .unwrap()
                .and_then(|message| message["name"].as_str().map(str::to_string)),
            Some("chat.started".to_string())
        );
        assert_eq!(
            router
                .recv_event_timeout(Duration::ZERO)
                .unwrap()
                .and_then(|message| message["name"].as_str().map(str::to_string)),
            Some("chat.completed".to_string())
        );
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .clear();
        router.close().expect("clean router close");
    }

    #[test]
    fn held_critical_head_remains_inside_the_named_capacity() {
        let (mut router, _released) = router_with_messages(Vec::new());
        router.enable_events(true);
        let (waiter, _receiver) = std::sync::mpsc::channel();
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .insert(
                "chat-capacity".to_string(),
                super::Pending {
                    name: "chat.send".to_string(),
                    is_hello: false,
                    protocol_minor: 2,
                    waiter,
                },
            );
        let event = |name: &str| {
            json!({
                "protocolMajor": 2,
                "protocolMinor": 2,
                "kind": "event",
                "generationId": GENERATION,
                "generationCredential": CREDENTIAL,
                "id": "chat-capacity",
                "name": name,
                "payload": {"operationId": "chat-capacity"}
            })
        };
        super::route_message(&router.shared, event("chat.started")).expect("started queued");
        super::route_message(&router.shared, event("chat.completed")).expect("terminal queued");
        assert_eq!(
            router
                .recv_event_timeout(Duration::ZERO)
                .unwrap()
                .and_then(|message| message["name"].as_str().map(str::to_string)),
            Some("chat.started".to_string())
        );

        for _ in 1..CRITICAL_EVENT_QUEUE_LIMIT {
            super::route_message(&router.shared, event("chat.completed"))
                .expect("remaining reserved slot");
        }
        assert!(
            super::route_message(&router.shared, event("chat.completed"))
                .expect_err("held head must count toward the critical capacity")
                .starts_with("EVENT_QUEUE_FULL:")
        );
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .clear();
        router.close().expect("clean router close");
    }

    #[test]
    fn duplicate_and_stale_request_identity_are_rejected_before_write() {
        let (mut router, _released) = router_with_messages(Vec::new());
        let first_handle = router.handle();
        let first = thread::spawn(move || {
            first_handle.request(
                request("duplicate", "fixture.blocking"),
                Duration::from_millis(100),
            )
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while router.handle().pending_len() != 1 && Instant::now() < deadline {
            thread::yield_now();
        }
        let duplicate = router
            .handle()
            .request(
                request("duplicate", "fixture.blocking"),
                Duration::from_millis(10),
            )
            .expect_err("duplicate id");
        assert!(duplicate.starts_with("DUPLICATE_REQUEST_ID:"));
        let mut stale = request("stale", "fixture.blocking");
        stale["generationCredential"] = json!("33333333333333333333333333333333");
        let stale = router
            .handle()
            .request(stale, Duration::from_millis(10))
            .expect_err("stale credential");
        assert!(stale.starts_with("GENERATION_CREDENTIAL_MISMATCH:"));
        assert!(first
            .join()
            .unwrap()
            .unwrap_err()
            .starts_with("REQUEST_DEADLINE_EXCEEDED:"));
        router.close().expect("clean router close");
    }

    #[test]
    fn unknown_response_id_fails_all_pending_waiters() {
        let (mut router, released) =
            router_with_messages(vec![response("unknown", "fixture.blocking")]);
        let handle = router.handle();
        let waiter = thread::spawn(move || {
            handle.request(request("known", "fixture.blocking"), Duration::from_secs(1))
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while router.handle().pending_len() != 1 && Instant::now() < deadline {
            thread::yield_now();
        }
        released.store(true, std::sync::atomic::Ordering::Release);
        assert!(waiter
            .join()
            .unwrap()
            .unwrap_err()
            .starts_with("UNKNOWN_REQUEST_ID:"));
        assert!(router
            .close()
            .unwrap_err()
            .starts_with("UNKNOWN_REQUEST_ID:"));
    }

    #[test]
    fn wrong_response_name_fails_closed_without_completing_waiter() {
        let (mut router, released) = router_with_messages(vec![response("known", "fixture.wrong")]);
        let handle = router.handle();
        let waiter = thread::spawn(move || {
            handle.request(request("known", "fixture.blocking"), Duration::from_secs(1))
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while router.handle().pending_len() != 1 && Instant::now() < deadline {
            thread::yield_now();
        }
        released.store(true, std::sync::atomic::Ordering::Release);
        assert!(waiter
            .join()
            .unwrap()
            .unwrap_err()
            .starts_with("INVALID_RESPONSE_NAME:"));
        assert!(router
            .close()
            .unwrap_err()
            .starts_with("INVALID_RESPONSE_NAME:"));
    }

    #[test]
    fn all_router_capacities_are_named_and_finite() {
        assert!(PENDING_LIMIT > 0);
        assert!(WRITER_QUEUE_LIMIT > 0);
        assert!(EVENT_QUEUE_LIMIT > 0);
    }

    #[test]
    fn pending_limit_rejects_overload_without_leaving_an_orphan_waiter() {
        let (mut router, _released) = router_with_messages(Vec::new());
        {
            let mut pending = router.shared.pending.lock().expect("pending registry");
            for index in 0..PENDING_LIMIT {
                let (waiter, _receiver) = std::sync::mpsc::channel();
                pending.insert(
                    format!("occupied-{index}"),
                    super::Pending {
                        name: "fixture.blocking".to_string(),
                        is_hello: false,
                        protocol_minor: 2,
                        waiter,
                    },
                );
            }
        }
        let error = router
            .handle()
            .request(
                request("overflow", "fixture.blocking"),
                Duration::from_millis(10),
            )
            .expect_err("pending overload must fail");
        assert!(error.starts_with("PENDING_LIMIT_EXCEEDED:"));
        assert_eq!(router.handle().pending_len(), PENDING_LIMIT);
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .clear();
        router.close().expect("clean router close");
    }

    #[test]
    fn event_queue_saturation_fails_closed_instead_of_dropping_terminal_events() {
        let (mut router, _released) = router_with_messages(Vec::new());
        router.enable_events(true);
        let (waiter, _receiver) = std::sync::mpsc::channel();
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .insert(
                "event-source".to_string(),
                super::Pending {
                    name: "fixture.blocking".to_string(),
                    is_hello: false,
                    protocol_minor: 2,
                    waiter,
                },
            );
        let terminal = json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "event",
            "generationId": GENERATION,
            "generationCredential": CREDENTIAL,
            "id": "event-source",
            "name": "fixture.completed",
            "payload": {"state": "completed"}
        });
        for _ in 0..EVENT_QUEUE_LIMIT {
            super::route_message(&router.shared, terminal.clone()).expect("reserved event slot");
        }
        assert!(super::route_message(&router.shared, terminal)
            .expect_err("full event queue must fail")
            .starts_with("EVENT_QUEUE_FULL:"));
        router
            .shared
            .pending
            .lock()
            .expect("pending registry")
            .clear();
        router.close().expect("clean router close");
    }
}
