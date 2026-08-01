use std::{
    collections::VecDeque,
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::Emitter;

use crate::{
    chat_bridge::{ChatBridge, ChatEventPublication, CHAT_EVENT},
    core_host_protocol::{PROTOCOL_MAJOR, PROTOCOL_MINOR},
    core_host_runtime::{ConcurrentRequestHandle, CoreHostRuntime},
    core_supervisor::{
        CoreSupervisor, FailureReason, GenerationId, LifecycleAction, LifecycleIntent,
        SupervisorSnapshot, SupervisorState,
    },
    platform::{FilesystemRuntimeLocator, RuntimeLocationRequest, RuntimeLocator},
};

const HELLO_DEADLINE: Duration = Duration::from_secs(3);
const INITIALIZE_DEADLINE: Duration = Duration::from_secs(5);
const SNAPSHOT_DEADLINE: Duration = Duration::from_secs(3);
const READINESS_DEADLINE: Duration = Duration::from_secs(30);
const SNAPSHOT_POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SupervisorPublication {
    state: &'static str,
    generation_id: Option<String>,
    generation_number: u64,
    restart_pending: bool,
    app_shutdown: bool,
    last_failure: Option<&'static str>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotPublication {
    generation_id: String,
    revision: u64,
    readiness: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VersionPublication {
    desktop_version: &'static str,
    core_version: String,
    protocol_version: String,
    log_location: &'static str,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShellLifecyclePublication {
    supervisor: SupervisorPublication,
    snapshot: Option<SnapshotPublication>,
    character_presentation: Option<Value>,
    versions: VersionPublication,
}

#[derive(Clone, Copy)]
enum ShellCommand {
    Retry,
    Restart,
    Shutdown,
}

#[derive(Clone)]
pub struct ShellLifecycleHandle {
    command: Sender<ShellCommand>,
    publication: Arc<Mutex<ShellLifecyclePublication>>,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    settings_request_number: Arc<AtomicU64>,
    chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
}

impl ShellLifecycleHandle {
    pub fn snapshot(&self) -> Result<ShellLifecyclePublication, &'static str> {
        self.publication
            .lock()
            .map(|publication| publication.clone())
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn character_presentation(&self) -> Result<Option<Value>, &'static str> {
        self.publication
            .lock()
            .map(|publication| publication.character_presentation.clone())
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn current_generation_id(&self) -> Result<Option<String>, &'static str> {
        self.publication
            .lock()
            .map(|publication| publication.supervisor.generation_id.clone())
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn retry(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Retry)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    pub fn restart(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Restart)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    pub fn settings_request(
        &self,
        request_id: Option<&str>,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<Value, String> {
        let generated;
        let request_id = match request_id {
            Some(value) if !value.trim().is_empty() => value,
            Some(_) => return Err("SETTINGS_REQUEST_INVALID".to_string()),
            None => {
                generated = format!(
                    "settings-{}",
                    self.settings_request_number.fetch_add(1, Ordering::Relaxed) + 1
                );
                &generated
            }
        };
        let transport = self
            .settings_transport
            .lock()
            .map_err(|_| "SETTINGS_TRANSPORT_UNAVAILABLE".to_string())?
            .clone()
            .ok_or_else(|| "SETTINGS_TRANSPORT_UNAVAILABLE".to_string())?;
        transport.request(request_id, name, payload, deadline)
    }

    pub fn request_shutdown(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Shutdown)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    pub fn chat_bridge(&self) -> Result<ChatBridge, String> {
        self.chat_bridge
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?
            .clone()
            .ok_or_else(|| "CHAT_BRIDGE_UNAVAILABLE".to_string())
    }
}

pub struct ShellLifecycleSession {
    handle: ShellLifecycleHandle,
    worker: Option<JoinHandle<()>>,
    chat_events: Option<Receiver<ChatEventPublication>>,
    chat_projector: Option<JoinHandle<()>>,
}

impl ShellLifecycleSession {
    pub fn start(request: RuntimeLocationRequest) -> Self {
        let (command, commands) = mpsc::channel();
        let initial = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "stopped",
                generation_id: None,
                generation_number: 0,
                restart_pending: false,
                app_shutdown: false,
                last_failure: None,
            },
            snapshot: None,
            character_presentation: None,
            versions: VersionPublication {
                desktop_version: env!("CARGO_PKG_VERSION"),
                core_version: "unavailable".to_string(),
                protocol_version: format!("{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"),
                log_location: "Sakura application logs",
            },
        };
        let publication = Arc::new(Mutex::new(initial));
        let settings_transport = Arc::new(Mutex::new(None));
        let chat_bridge = Arc::new(Mutex::new(None));
        let (chat_event_sender, chat_events) = mpsc::channel();
        let worker_publication = publication.clone();
        let worker_settings_transport = settings_transport.clone();
        let worker_chat_bridge = chat_bridge.clone();
        let worker = thread::spawn(move || {
            run_worker(
                request,
                commands,
                worker_publication,
                worker_settings_transport,
                worker_chat_bridge,
                chat_event_sender,
            )
        });
        Self {
            handle: ShellLifecycleHandle {
                command,
                publication,
                settings_transport,
                settings_request_number: Arc::new(AtomicU64::new(0)),
                chat_bridge,
            },
            worker: Some(worker),
            chat_events: Some(chat_events),
            chat_projector: None,
        }
    }

    pub fn handle(&self) -> ShellLifecycleHandle {
        self.handle.clone()
    }

    pub fn bind_chat_projection(&mut self, app: tauri::AppHandle) -> Result<(), &'static str> {
        if self.chat_projector.is_some() {
            return Ok(());
        }
        let events = self
            .chat_events
            .take()
            .ok_or("CHAT_PROJECTOR_UNAVAILABLE")?;
        self.chat_projector = Some(thread::spawn(move || {
            while let Ok(event) = events.recv() {
                let _ = app.emit_to("main", CHAT_EVENT, event);
            }
        }));
        Ok(())
    }

    pub fn shutdown_and_join(mut self) -> Result<(), &'static str> {
        let _ = self.handle.request_shutdown();
        let Some(worker) = self.worker.take() else {
            return Ok(());
        };
        worker.join().map_err(|_| "LIFECYCLE_WORKER_FAILED")?;
        if let Some(projector) = self.chat_projector.take() {
            projector.join().map_err(|_| "CHAT_PROJECTOR_FAILED")?;
        }
        Ok(())
    }
}

impl Drop for ShellLifecycleSession {
    fn drop(&mut self) {
        let _ = self.handle.request_shutdown();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        if let Some(projector) = self.chat_projector.take() {
            let _ = projector.join();
        }
    }
}

struct WorkerState {
    request: RuntimeLocationRequest,
    supervisor: CoreSupervisor,
    host: Option<CoreHostRuntime>,
    chat_bridge: Option<ChatBridge>,
    snapshot: Option<Value>,
    identity: Option<(GenerationId, u64)>,
    core_version: String,
    request_number: u64,
    cleanup_blocked: bool,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    shared_chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
}

fn run_worker(
    request: RuntimeLocationRequest,
    commands: Receiver<ShellCommand>,
    publication: Arc<Mutex<ShellLifecyclePublication>>,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    shared_chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
    chat_events: Sender<ChatEventPublication>,
) {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos() as u64)
        ^ u64::from(std::process::id());
    let mut state = WorkerState {
        request,
        supervisor: CoreSupervisor::new(nonce),
        host: None,
        chat_bridge: None,
        snapshot: None,
        identity: None,
        core_version: "unavailable".to_string(),
        request_number: 0,
        cleanup_blocked: false,
        settings_transport,
        shared_chat_bridge,
    };
    let mut actions = VecDeque::from(state.supervisor.submit(LifecycleIntent::Start));
    publish(&state, &publication);

    loop {
        while let Some(action) = actions.pop_front() {
            match action {
                LifecycleAction::SpawnGeneration {
                    generation_id,
                    generation_number,
                    ..
                } => {
                    state.identity = Some((generation_id, generation_number));
                    state.chat_bridge = None;
                    clear_chat_bridge(&state);
                    clear_settings_transport(&state);
                    state.snapshot = None;
                    state.core_version = "unavailable".to_string();
                    publish(&state, &publication);
                    drain_commands(&commands, &mut state, &mut actions);
                    if state.supervisor.snapshot().state != SupervisorState::Spawning {
                        continue;
                    }
                    if state.cleanup_blocked {
                        continue;
                    }
                    if let Err(reason) = spawn_and_initialize(
                        &mut state,
                        generation_id,
                        &commands,
                        &mut actions,
                        &publication,
                    ) {
                        actions.extend(
                            state
                                .supervisor
                                .observe_generation_failed(generation_id, reason),
                        );
                        publish(&state, &publication);
                    }
                }
                LifecycleAction::StopGeneration { generation_id, .. } => {
                    publish(&state, &publication);
                    let cleaned = stop_generation(&mut state);
                    drain_commands(&commands, &mut state, &mut actions);
                    if cleaned && !state.cleanup_blocked {
                        state.snapshot = None;
                        actions.extend(state.supervisor.finalize_generation(generation_id).actions);
                    } else {
                        state.cleanup_blocked = true;
                    }
                    publish(&state, &publication);
                }
                LifecycleAction::ScheduleRestart { token, delay } => {
                    publish(&state, &publication);
                    match commands.recv_timeout(delay) {
                        Ok(command) => actions.extend(submit_command(&mut state, command)),
                        Err(RecvTimeoutError::Timeout) => {
                            actions.extend(state.supervisor.observe_restart_timer(token))
                        }
                        Err(RecvTimeoutError::Disconnected) => {
                            actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown))
                        }
                    }
                }
                LifecycleAction::CancelRestart { .. } => {}
            }
        }

        publish(&state, &publication);
        if state.supervisor.snapshot().app_shutdown && state.host.is_none() {
            break;
        }
        if state.cleanup_blocked {
            match commands.recv() {
                Ok(ShellCommand::Shutdown) | Err(_) => break,
                Ok(ShellCommand::Retry | ShellCommand::Restart) => continue,
            }
        }

        match commands.recv_timeout(SNAPSHOT_POLL_INTERVAL) {
            Ok(command) => actions.extend(submit_command(&mut state, command)),
            Err(RecvTimeoutError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown))
            }
            Err(RecvTimeoutError::Timeout) => {
                drain_chat_events(&mut state, &chat_events);
                if state.supervisor.snapshot().state == SupervisorState::Running {
                    match refresh_snapshot(&mut state) {
                        Ok(()) => publish(&state, &publication),
                        Err(()) => {
                            if let Some((generation_id, _)) = state.identity {
                                actions.extend(state.supervisor.observe_generation_failed(
                                    generation_id,
                                    FailureReason::UnexpectedExit,
                                ));
                                publish(&state, &publication);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn drain_chat_events(state: &mut WorkerState, events: &Sender<ChatEventPublication>) {
    let Some(host) = state.host.as_ref() else {
        return;
    };
    loop {
        let event = match host.recv_event_timeout(Duration::ZERO) {
            Ok(Some(event)) => event,
            Ok(None) | Err(_) => return,
        };
        if event
            .get("name")
            .and_then(Value::as_str)
            .is_some_and(|name| name.starts_with("chat."))
        {
            if let Some(bridge) = state.chat_bridge.as_ref() {
                if let Ok(Some(publication)) = bridge.observe_event(&event) {
                    let _ = events.send(publication);
                }
            }
        }
    }
}

fn submit_command(state: &mut WorkerState, command: ShellCommand) -> Vec<LifecycleAction> {
    match command {
        ShellCommand::Retry => state.supervisor.submit(LifecycleIntent::Retry),
        ShellCommand::Restart => state.supervisor.submit(LifecycleIntent::Restart),
        ShellCommand::Shutdown => state.supervisor.submit(LifecycleIntent::AppShutdown),
    }
}

fn drain_commands(
    commands: &Receiver<ShellCommand>,
    state: &mut WorkerState,
    actions: &mut VecDeque<LifecycleAction>,
) {
    loop {
        match commands.try_recv() {
            Ok(command) => actions.extend(submit_command(state, command)),
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown));
                return;
            }
        }
    }
}

fn spawn_and_initialize(
    state: &mut WorkerState,
    generation_id: GenerationId,
    commands: &Receiver<ShellCommand>,
    actions: &mut VecDeque<LifecycleAction>,
    publication: &Arc<Mutex<ShellLifecyclePublication>>,
) -> Result<(), FailureReason> {
    let layout = FilesystemRuntimeLocator
        .locate(&state.request)
        .map_err(|_| FailureReason::DeterministicRuntime)?;
    let generation_text = generation_text(generation_id);
    let host = match CoreHostRuntime::launch(&layout, &generation_text) {
        Ok(host) => host,
        Err(failure) => {
            if failure.into_recovery().is_some() {
                state.cleanup_blocked = true;
            }
            return Err(FailureReason::TemporarySpawnFailure);
        }
    };
    state.host = Some(host);
    state.supervisor.observe_spawn_succeeded(generation_id);
    publish(state, publication);

    let hello = state
        .host
        .as_mut()
        .expect("spawned host remains owned")
        .request("shell-hello", "system.hello", HELLO_DEADLINE)
        .map_err(|error| classify_control_failure(&error, FailureReason::HelloTimeout))?;
    if hello.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(FailureReason::ProtocolMajorIncompatible);
    }
    state.core_version = hello
        .pointer("/payload/coreVersion")
        .and_then(Value::as_str)
        .filter(|value| is_safe_version(value))
        .unwrap_or("unavailable")
        .to_string();

    let initialize = state
        .host
        .as_mut()
        .expect("initialized host remains owned")
        .request_with_payload(
            "shell-initialize",
            "core.initialize",
            json!({}),
            INITIALIZE_DEADLINE,
        )
        .map_err(|error| classify_control_failure(&error, FailureReason::InitializeTimeout))?;
    if initialize.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(FailureReason::DeterministicConfiguration);
    }

    let readiness_deadline = Instant::now() + READINESS_DEADLINE;
    state.chat_bridge = state
        .host
        .as_ref()
        .and_then(|host| host.chat_gateway().ok())
        .and_then(|gateway| {
            ChatBridge::new(
                gateway,
                generation_text.clone(),
                state.identity.map_or(0, |(_, number)| number),
            )
            .ok()
        });
    if let Ok(mut target) = state.shared_chat_bridge.lock() {
        *target = state.chat_bridge.clone();
    }
    let settings_handle = state.host.as_ref().and_then(|host| {
        let negotiated = host.negotiation().is_some_and(|value| {
            value
                .capabilities
                .iter()
                .any(|capability| capability == "settings.provider-model")
        });
        negotiated
            .then(|| host.concurrent_request_handle().ok())
            .flatten()
    });
    if let Ok(mut target) = state.settings_transport.lock() {
        *target = settings_handle;
    }
    loop {
        match commands.try_recv() {
            Ok(command) => {
                actions.extend(submit_command(state, command));
                return Ok(());
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown));
                return Ok(());
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        refresh_snapshot(state).map_err(|_| FailureReason::ConnectionLost)?;
        publish(state, publication);
        let readiness = state
            .snapshot
            .as_ref()
            .and_then(|snapshot| snapshot.get("readiness"))
            .and_then(Value::as_str);
        if matches!(
            readiness,
            Some("ready" | "setup_required" | "degraded" | "failed")
        ) {
            return Ok(());
        }
        if Instant::now() >= readiness_deadline {
            return Err(FailureReason::InitializeTimeout);
        }
        thread::sleep(SNAPSHOT_POLL_INTERVAL);
    }
}

fn refresh_snapshot(state: &mut WorkerState) -> Result<(), ()> {
    state.request_number += 1;
    let request_id = format!("shell-snapshot-{}", state.request_number);
    let snapshot = state
        .host
        .as_mut()
        .ok_or(())?
        .refresh_snapshot(&request_id, SNAPSHOT_DEADLINE)
        .map_err(|_| ())?;
    state.snapshot = Some(snapshot);
    Ok(())
}

fn stop_generation(state: &mut WorkerState) -> bool {
    clear_settings_transport(state);
    clear_chat_bridge(state);
    if let Some(bridge) = state.chat_bridge.take() {
        bridge.invalidate();
    }
    let Some(host) = state.host.take() else {
        return true;
    };
    match host.shutdown() {
        Ok(exit) => exit.tree_empty && exit.stderr_stats.eof && !exit.stderr_stats.read_failed,
        Err(_) => false,
    }
}

fn clear_chat_bridge(state: &WorkerState) {
    if let Ok(mut bridge) = state.shared_chat_bridge.lock() {
        if let Some(existing) = bridge.take() {
            existing.invalidate();
        }
    }
}

fn clear_settings_transport(state: &WorkerState) {
    if let Ok(mut transport) = state.settings_transport.lock() {
        *transport = None;
    }
}

fn classify_control_failure(error: &str, timeout: FailureReason) -> FailureReason {
    if error.starts_with("PROTOCOL_MAJOR_MISMATCH") {
        FailureReason::ProtocolMajorIncompatible
    } else if error.starts_with("MISSING_REQUIRED_CAPABILITY")
        || error.starts_with("INVALID_NEGOTIATION")
    {
        FailureReason::MissingRequiredCapability
    } else if error.starts_with("GENERATION_CREDENTIAL_MISMATCH") {
        FailureReason::SecurityBoundary
    } else {
        timeout
    }
}

fn publish(state: &WorkerState, target: &Arc<Mutex<ShellLifecyclePublication>>) {
    let supervisor = state.supervisor.snapshot();
    let identity = supervisor
        .current
        .map(|generation| (generation.id, generation.number))
        .or(state.identity);
    let snapshot = state.snapshot.as_ref().and_then(|snapshot| {
        let (generation_id, _) = identity?;
        let generation_id = generation_text(generation_id);
        if snapshot.get("generationId").and_then(Value::as_str) != Some(generation_id.as_str()) {
            return None;
        }
        Some(SnapshotPublication {
            generation_id,
            revision: snapshot.get("revision").and_then(Value::as_u64)?,
            readiness: snapshot
                .get("readiness")
                .and_then(Value::as_str)?
                .to_string(),
        })
    });
    let next = ShellLifecyclePublication {
        supervisor: supervisor_publication(supervisor, identity),
        snapshot,
        character_presentation: state.snapshot.as_ref().and_then(|snapshot| {
            let (generation_id, _) = identity?;
            let generation_id = generation_text(generation_id);
            let presentation = snapshot.get("characterPresentation")?;
            if presentation.get("generationId").and_then(Value::as_str)
                != Some(generation_id.as_str())
            {
                return None;
            }
            Some(presentation.clone())
        }),
        versions: VersionPublication {
            desktop_version: env!("CARGO_PKG_VERSION"),
            core_version: state.core_version.clone(),
            protocol_version: format!("{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"),
            log_location: "Sakura application logs",
        },
    };
    if let Ok(mut publication) = target.lock() {
        *publication = next;
    }
}

fn supervisor_publication(
    snapshot: SupervisorSnapshot,
    identity: Option<(GenerationId, u64)>,
) -> SupervisorPublication {
    SupervisorPublication {
        state: supervisor_state(snapshot.state),
        generation_id: identity.map(|(generation_id, _)| generation_text(generation_id)),
        generation_number: identity.map_or(0, |(_, number)| number),
        restart_pending: snapshot.restart_pending,
        app_shutdown: snapshot.app_shutdown,
        last_failure: snapshot.last_failure.map(failure_reason),
    }
}

fn supervisor_state(state: SupervisorState) -> &'static str {
    match state {
        SupervisorState::Stopped => "stopped",
        SupervisorState::Spawning => "spawning",
        SupervisorState::Running => "running",
        SupervisorState::Stopping => "stopping",
        SupervisorState::Exited => "exited",
        SupervisorState::Restarting => "restarting",
        SupervisorState::Failed => "failed",
    }
}

fn failure_reason(reason: FailureReason) -> &'static str {
    match reason {
        FailureReason::UnexpectedExit => "unexpected_exit",
        FailureReason::TemporarySpawnFailure => "temporary_spawn_failure",
        FailureReason::HelloTimeout => "hello_timeout",
        FailureReason::InitializeTimeout => "initialize_timeout",
        FailureReason::ConnectionLost => "connection_lost",
        FailureReason::ProtocolMajorIncompatible => "protocol_major_incompatible",
        FailureReason::MissingRequiredCapability => "missing_required_capability",
        FailureReason::SetupRequired => "setup_required",
        FailureReason::DeterministicConfiguration => "deterministic_configuration",
        FailureReason::DeterministicRuntime => "deterministic_runtime",
        FailureReason::SecurityBoundary => "security_boundary",
    }
}

fn generation_text(generation_id: GenerationId) -> String {
    format!("{:032x}", generation_id.as_u128())
}

fn is_safe_version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.as_bytes()[0].is_ascii_digit()
        && value.contains('.')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wait_for_failed(
        handle: &ShellLifecycleHandle,
        expected_generation: u64,
    ) -> ShellLifecyclePublication {
        // The default Windows suite intentionally runs process-heavy tests in
        // parallel.  This is observation headroom for scheduling the lifecycle
        // worker, not a product startup or shutdown deadline.
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let publication = handle.snapshot().expect("lifecycle publication");
            if publication.supervisor.state == "failed"
                && publication.supervisor.generation_number == expected_generation
            {
                return publication;
            }
            assert!(
                Instant::now() < deadline,
                "lifecycle failure was not bounded: state={}, generation={}, last_failure={:?}",
                publication.supervisor.state,
                publication.supervisor.generation_number,
                publication.supervisor.last_failure
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_stable_generation(
        handle: &ShellLifecycleHandle,
        expected_generation: u64,
    ) -> ShellLifecyclePublication {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let publication = handle.snapshot().expect("lifecycle publication");
            if publication.supervisor.state == "running"
                && publication.supervisor.generation_number == expected_generation
                && publication.snapshot.as_ref().is_some_and(|snapshot| {
                    matches!(
                        snapshot.readiness.as_str(),
                        "ready" | "setup_required" | "degraded" | "failed"
                    )
                })
            {
                return publication;
            }
            assert!(
                Instant::now() < deadline,
                "real Core generation did not reach stable readiness"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn missing_runtime_stays_visible_and_repeated_retry_exit_are_bounded() {
        let _test_lock = crate::core_host_runtime::lifecycle_test_lock();
        let root = std::env::temp_dir().join(format!(
            "sakura-runtime-v2-wp-1d-01-missing-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir(&root).expect("isolated missing Runtime root");
        let executable_directory = std::env::current_exe()
            .expect("test executable")
            .parent()
            .expect("test executable directory")
            .to_path_buf();
        let session = ShellLifecycleSession::start(RuntimeLocationRequest {
            mode: crate::platform::RuntimeMode::ExplicitDevelopment,
            target: crate::platform::current_platform_target().expect("formal test target"),
            executable_directory,
            resource_directory: root.clone(),
            explicit_development_root: Some(root.clone()),
            assistant_root: root.clone(),
        });
        let handle = session.handle();

        let first = wait_for_failed(&handle, 1);
        assert_eq!(first.supervisor.last_failure, Some("deterministic_runtime"));
        assert!(first.snapshot.is_none());

        handle.retry().expect("first retry enters Supervisor");
        handle
            .retry()
            .expect("duplicate retry enters the same channel");
        let second = wait_for_failed(&handle, 2);
        assert_eq!(second.supervisor.generation_number, 2);
        thread::sleep(Duration::from_millis(50));
        assert_eq!(
            handle
                .snapshot()
                .expect("settled lifecycle publication")
                .supervisor
                .generation_number,
            2
        );

        let shutdown_started = Instant::now();
        session
            .shutdown_and_join()
            .expect("missing Runtime lifecycle should exit cleanly");
        assert!(shutdown_started.elapsed() < Duration::from_secs(2));
        std::fs::remove_dir(&root).expect("isolated missing Runtime root should be empty");
    }

    #[test]
    fn real_core_retry_waits_for_cleanup_and_exit_releases_the_generation() {
        let _test_lock = crate::core_host_runtime::lifecycle_test_lock();
        let manifest_directory = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repository_root = manifest_directory
            .join("../..")
            .canonicalize()
            .expect("repository root");
        let assistant_root = repository_root
            .join("tests/fixtures/runtime_v2/wp_3_01/ready")
            .canonicalize()
            .expect("ready Assistant fixture");
        let executable_directory = std::env::current_exe()
            .expect("test executable")
            .parent()
            .expect("test executable directory")
            .to_path_buf();
        let session = ShellLifecycleSession::start(RuntimeLocationRequest {
            mode: crate::platform::RuntimeMode::ExplicitDevelopment,
            target: crate::platform::current_platform_target().expect("formal test target"),
            executable_directory,
            resource_directory: repository_root.clone(),
            explicit_development_root: Some(repository_root.clone()),
            assistant_root,
        });
        let handle = session.handle();

        let first = wait_for_stable_generation(&handle, 1);
        assert_eq!(
            first
                .snapshot
                .as_ref()
                .expect("first stable Snapshot")
                .generation_id,
            first
                .supervisor
                .generation_id
                .expect("first Supervisor generation")
        );

        handle.retry().expect("manual retry enters Supervisor");
        handle.retry().expect("duplicate manual retry is coalesced");
        let second = wait_for_stable_generation(&handle, 2);
        assert_eq!(second.supervisor.generation_number, 2);

        let shutdown_started = Instant::now();
        session
            .shutdown_and_join()
            .expect("real Core lifecycle should exit cleanly");
        assert!(shutdown_started.elapsed() < Duration::from_secs(6));
    }

    #[test]
    fn lifecycle_publication_serializes_only_the_approved_minimum() {
        let publication = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "failed",
                generation_id: Some("generation-safe".to_string()),
                generation_number: 2,
                restart_pending: false,
                app_shutdown: false,
                last_failure: Some("deterministic_runtime"),
            },
            snapshot: None,
            character_presentation: None,
            versions: VersionPublication {
                desktop_version: "0.1.0",
                core_version: "2.1.0".to_string(),
                protocol_version: "2.1".to_string(),
                log_location: "Sakura application logs",
            },
        };
        let encoded = serde_json::to_value(publication).expect("publication should serialize");
        let object = encoded.as_object().expect("publication is an object");
        assert_eq!(
            object.keys().map(String::as_str).collect::<Vec<_>>(),
            [
                "characterPresentation",
                "snapshot",
                "supervisor",
                "versions"
            ]
        );
        let text = encoded.to_string().to_ascii_lowercase();
        for forbidden in [
            "credential",
            "apikey",
            "prompt",
            "providerendpoint",
            "model",
            "config",
            "exception",
            "c:\\\\users",
        ] {
            assert!(!text.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn version_filter_rejects_paths_endpoints_and_exception_text() {
        for unsafe_value in [
            "C:\\Users\\private\\runtime.exe",
            "https://provider.invalid/v1",
            "RuntimeError('secret')",
            "sk-live-private",
        ] {
            assert!(!is_safe_version(unsafe_value));
        }
        for safe in ["2.1", "2.1.0", "2.1.0-dev+local"] {
            assert!(is_safe_version(safe));
        }
    }
}
