use std::{
    collections::VecDeque,
    fs::{self, File},
    io::Write,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

#[cfg(test)]
use std::path::Path;

#[cfg(unix)]
use std::io::Read;

use serde_json::{json, Value};

use crate::{
    core_host_protocol::{write_frame, FrameDecoder, PROTOCOL_MAJOR, PROTOCOL_MINOR},
    platform::{
        ManagedPipeReadOutcome, ManagedPipeReader, ManagedProcessRequest, ManagedProcessTree,
        ManagedProcessTreeBackend, NativeManagedProcessTreeBackend, ProcessExitStatus,
        ProcessStdio, ProcessWaitOutcome, RuntimeLayout,
    },
};

const CONTROL_PRIORITY: &str = "control";
const DEADLINE_EXIT_CODE: u32 = 93;
const MIN_PROTOCOL_MINOR: u64 = 0;
const GENERATION_CREDENTIAL_BYTES: usize = 16;
const STDERR_READ_CHUNK_SIZE: usize = 4 * 1024;
const STDERR_READ_SLICE: Duration = Duration::from_millis(100);
const STDERR_RECORD_LIMIT: usize = 4 * 1024;
const STDERR_CACHE_LIMIT: usize = 64 * 1024;
const CHARACTER_SUMMARY_KEYS: [&str; 5] = [
    "id",
    "displayName",
    "initialMessage",
    "replyTones",
    "portraitChoices",
];
const REQUIRED_CAPABILITIES: [&str; 5] = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
];
const SNAPSHOT_READINESS: [&str; 6] = [
    "transport_ready",
    "initializing",
    "setup_required",
    "ready",
    "degraded",
    "failed",
];

#[derive(Debug, Clone, PartialEq)]
pub struct CoreSnapshotCache {
    generation_id: String,
    snapshot: Option<Value>,
}

impl CoreSnapshotCache {
    pub fn new(generation_id: &str) -> Result<Self, String> {
        if generation_id.trim().is_empty() {
            return Err("Snapshot generation ID must not be empty".to_string());
        }
        Ok(Self {
            generation_id: generation_id.to_string(),
            snapshot: None,
        })
    }

    pub fn begin_generation(&mut self, generation_id: &str) -> Result<(), String> {
        if generation_id.trim().is_empty() {
            return Err("Snapshot generation ID must not be empty".to_string());
        }
        self.generation_id.clear();
        self.generation_id.push_str(generation_id);
        self.snapshot = None;
        Ok(())
    }

    pub fn store_python_snapshot(&mut self, snapshot: &Value) -> Result<(), String> {
        let object = snapshot
            .as_object()
            .ok_or_else(|| "Core Snapshot must be an object".to_string())?;
        if object.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
            return Err("Core Snapshot schemaVersion is unsupported".to_string());
        }
        if object.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str()) {
            return Err("Core Snapshot belongs to another generation".to_string());
        }
        if object
            .get("generationNumber")
            .and_then(Value::as_u64)
            .is_none_or(|number| number == 0)
            || object.get("revision").and_then(Value::as_u64).is_none()
            || object
                .get("coreConfigRevision")
                .and_then(Value::as_u64)
                .is_none()
            || !object.get("components").is_some_and(Value::is_object)
        {
            return Err("Core Snapshot counters or components are invalid".to_string());
        }
        let readiness = object
            .get("readiness")
            .and_then(Value::as_str)
            .ok_or_else(|| "Core Snapshot readiness is invalid".to_string())?;
        if !SNAPSHOT_READINESS.contains(&readiness) {
            return Err("Core Snapshot readiness is unsupported".to_string());
        }
        let capabilities = object
            .get("capabilities")
            .and_then(Value::as_array)
            .ok_or_else(|| "Core Snapshot capabilities are invalid".to_string())?;
        if capabilities.iter().any(|value| {
            value
                .as_str()
                .is_none_or(|capability| capability.trim().is_empty())
        }) {
            return Err("Core Snapshot capability is invalid".to_string());
        }
        for key in ["currentCharacterSummary", "activeInteractionSummary"] {
            if object
                .get(key)
                .is_none_or(|value| !(value.is_null() || value.is_object()))
            {
                return Err(format!("Core Snapshot {key} is invalid"));
            }
        }
        if object
            .get("activeInteractionSummary")
            .is_some_and(|value| !value.is_null())
        {
            return Err("Core Snapshot activeInteractionSummary is unsupported".to_string());
        }
        reject_sensitive_snapshot_fields(snapshot)?;
        validate_character_summary(object.get("currentCharacterSummary"))?;
        validate_assistant_readiness(object, readiness)?;
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }

    pub fn current(&self) -> Option<&Value> {
        self.snapshot.as_ref()
    }
}

fn reject_sensitive_snapshot_fields(value: &Value) -> Result<(), String> {
    match value {
        Value::Object(object) => {
            for (key, nested) in object {
                let normalized = key
                    .chars()
                    .filter(|character| character.is_ascii_alphanumeric())
                    .flat_map(char::to_lowercase)
                    .collect::<String>();
                if [
                    "apikey",
                    "authorization",
                    "cookie",
                    "credential",
                    "private",
                    "prompt",
                    "secret",
                    "token",
                ]
                .iter()
                .any(|forbidden| normalized.contains(forbidden))
                {
                    return Err("Core Snapshot contains a forbidden private field".to_string());
                }
                reject_sensitive_snapshot_fields(nested)?;
            }
        }
        Value::Array(values) => {
            for nested in values {
                reject_sensitive_snapshot_fields(nested)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn validate_character_summary(summary: Option<&Value>) -> Result<(), String> {
    let Some(summary) = summary else {
        return Err("Core Snapshot currentCharacterSummary is missing".to_string());
    };
    if summary.is_null() {
        return Ok(());
    }
    let object = summary
        .as_object()
        .ok_or_else(|| "Core Snapshot currentCharacterSummary is invalid".to_string())?;
    if object.len() != CHARACTER_SUMMARY_KEYS.len()
        || CHARACTER_SUMMARY_KEYS
            .iter()
            .any(|key| !object.contains_key(*key))
    {
        return Err("Core Snapshot currentCharacterSummary fields are invalid".to_string());
    }
    if CHARACTER_SUMMARY_KEYS[..3]
        .iter()
        .any(|key| object.get(*key).is_none_or(|value| !value.is_string()))
    {
        return Err("Core Snapshot currentCharacterSummary strings are invalid".to_string());
    }
    for key in &CHARACTER_SUMMARY_KEYS[3..] {
        let values = object.get(*key).and_then(Value::as_array).ok_or_else(|| {
            "Core Snapshot currentCharacterSummary arrays are invalid".to_string()
        })?;
        if values.iter().any(|value| !value.is_string()) {
            return Err("Core Snapshot currentCharacterSummary arrays are invalid".to_string());
        }
    }
    Ok(())
}

fn validate_assistant_readiness(
    snapshot: &serde_json::Map<String, Value>,
    readiness: &str,
) -> Result<(), String> {
    let components = snapshot
        .get("components")
        .and_then(Value::as_object)
        .expect("components were validated before Assistant readiness");
    let Some(assistant) = components.get("assistant") else {
        return if readiness == "transport_ready" {
            Ok(())
        } else {
            Err("Core Snapshot Assistant component is missing".to_string())
        };
    };
    let assistant = assistant
        .as_object()
        .ok_or_else(|| "Core Snapshot Assistant component is invalid".to_string())?;
    if assistant.len() != 3
        || !["state", "code", "retryable"]
            .iter()
            .all(|key| assistant.contains_key(*key))
    {
        return Err("Core Snapshot Assistant component fields are invalid".to_string());
    }
    let state = assistant
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| "Core Snapshot Assistant state is invalid".to_string())?;
    let code = assistant
        .get("code")
        .and_then(Value::as_str)
        .ok_or_else(|| "Core Snapshot Assistant code is invalid".to_string())?;
    if state != readiness || assistant.get("retryable").and_then(Value::as_bool) != Some(false) {
        return Err("Core Snapshot Assistant readiness is retryable or inconsistent".to_string());
    }
    let valid = matches!(
        (state, code),
        ("initializing", "INITIALIZING")
            | ("ready", "READY")
            | ("setup_required", "CORE_CONFIG_SETUP_REQUIRED")
            | ("failed", "CONFIG_DATA_INVALID")
            | ("failed", "CONFIG_VERSION_UNSUPPORTED")
            | ("setup_required", "PROVIDER_SETUP_REQUIRED")
            | ("setup_required", "CHARACTER_SETUP_REQUIRED")
            | ("failed", "ASSISTANT_INITIALIZATION_FAILED")
            | ("degraded", "CHARACTER_FALLBACK_APPLIED")
            | ("degraded", "OPTIONAL_CHARACTER_SKIPPED")
    );
    if !valid {
        return Err("Core Snapshot Assistant readiness is unsupported".to_string());
    }
    let has_summary = snapshot
        .get("currentCharacterSummary")
        .is_some_and(|summary| !summary.is_null());
    if matches!(state, "ready" | "degraded") != has_summary {
        return Err("Core Snapshot Assistant summary is inconsistent".to_string());
    }
    Ok(())
}

#[derive(Debug, PartialEq, Eq)]
pub struct CoreHostExit {
    pub root_exit_code: u32,
    pub tree_empty: bool,
    pub forced: bool,
    pub stderr: String,
    pub stderr_stats: StderrDrainStats,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StderrDrainStats {
    pub generation_id: String,
    pub core_pid: u32,
    pub bytes_read: u64,
    pub dropped_bytes: u64,
    pub dropped_records: u64,
    pub truncated_records: u64,
    pub eof: bool,
    pub read_failed: bool,
}

#[derive(Debug)]
struct StderrDrainState {
    records: VecDeque<String>,
    buffered_bytes: usize,
    stats: StderrDrainStats,
}

struct StderrDrainer {
    state: Arc<Mutex<StderrDrainState>>,
    cancelled: Arc<AtomicBool>,
    completion: mpsc::Receiver<()>,
    reader: Option<thread::JoinHandle<()>>,
}

impl std::fmt::Debug for StderrDrainer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("StderrDrainer")
            .field("reader_active", &self.reader.is_some())
            .finish_non_exhaustive()
    }
}

impl StderrDrainer {
    fn start(
        pipe: Box<dyn ManagedPipeReader>,
        generation_id: &str,
        core_pid: u32,
        generation_credential: &str,
    ) -> Self {
        let state = Arc::new(Mutex::new(StderrDrainState {
            records: VecDeque::new(),
            buffered_bytes: 0,
            stats: StderrDrainStats {
                generation_id: generation_id.to_string(),
                core_pid,
                bytes_read: 0,
                dropped_bytes: 0,
                dropped_records: 0,
                truncated_records: 0,
                eof: false,
                read_failed: false,
            },
        }));
        let reader_state = Arc::clone(&state);
        let cancelled = Arc::new(AtomicBool::new(false));
        let reader_cancelled = Arc::clone(&cancelled);
        let (completion_sender, completion) = mpsc::sync_channel(1);
        let redactor = StderrRedactor::new(generation_credential);
        let reader = thread::Builder::new()
            .name(format!("sakura-core-stderr-{core_pid}"))
            .spawn(move || {
                drain_stderr(pipe, &reader_state, &redactor, &reader_cancelled);
                let _ = completion_sender.send(());
            })
            .expect("stderr reader thread creation must succeed");
        Self {
            state,
            cancelled,
            completion,
            reader: Some(reader),
        }
    }

    fn finish_until(&mut self, deadline: Instant) -> Result<(String, StderrDrainStats), String> {
        if self.reader.is_some() {
            let remaining = deadline.saturating_duration_since(Instant::now());
            match self.completion.recv_timeout(remaining) {
                Ok(()) => {}
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    self.cancelled.store(true, Ordering::Release);
                    return Err(
                        "STDERR_READ_FAILED: stderr reader exceeded its completion deadline"
                            .to_string(),
                    );
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {}
            }
            self.reader
                .take()
                .expect("stderr reader exists after completion wait")
                .join()
                .map_err(|_| "STDERR_READ_FAILED: stderr reader panicked".to_string())?;
        }
        let state = self
            .state
            .lock()
            .map_err(|_| "STDERR_READ_FAILED: stderr state lock was poisoned".to_string())?;
        Ok((
            state.records.iter().cloned().collect::<String>(),
            state.stats.clone(),
        ))
    }
}

impl Drop for StderrDrainer {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
        if let Some(reader_handle) = self.reader.take() {
            let _ = reader_handle.join();
        }
    }
}

struct StderrRedactor {
    secrets: Vec<String>,
}

impl StderrRedactor {
    fn new(generation_credential: &str) -> Self {
        let mut secrets = vec![generation_credential.to_string()];
        for value in std::env::vars_os().map(|(_, value)| value) {
            let value = value.to_string_lossy();
            if (4..=4096).contains(&value.len())
                && !secrets.iter().any(|secret| secret == value.as_ref())
            {
                secrets.push(value.into_owned());
            }
        }
        secrets.sort_by_key(|secret| std::cmp::Reverse(secret.len()));
        Self { secrets }
    }

    fn redact(&self, text: &str) -> String {
        let mut redacted = text.to_string();
        for secret in &self.secrets {
            redacted = redacted.replace(secret, "[REDACTED]");
        }
        for key in [
            "authorization",
            "cookie",
            "credential",
            "api_key",
            "apikey",
            "token",
            "secret",
            "password",
            "prompt",
            "message",
            "content",
        ] {
            redacted = redact_key_values(&redacted, key);
        }
        redacted
    }
}

fn drain_stderr(
    mut reader: Box<dyn ManagedPipeReader>,
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    cancelled: &AtomicBool,
) {
    let mut buffer = [0_u8; STDERR_READ_CHUNK_SIZE];
    let mut utf8_pending = Vec::with_capacity(4);
    let mut line_pending = String::new();
    let mut dropping_long_line = false;
    loop {
        match reader.read_until(&mut buffer, Instant::now() + STDERR_READ_SLICE, cancelled) {
            Ok(ManagedPipeReadOutcome::Read(count)) => {
                if let Ok(mut state) = state.lock() {
                    state.stats.bytes_read = state.stats.bytes_read.saturating_add(count as u64);
                }
                utf8_pending.extend_from_slice(&buffer[..count]);
                drain_valid_utf8(&mut utf8_pending, |text| {
                    drain_stderr_text(
                        state,
                        redactor,
                        &mut line_pending,
                        &mut dropping_long_line,
                        text,
                    )
                });
            }
            Ok(ManagedPipeReadOutcome::Eof) => {
                flush_stderr_pending(
                    state,
                    redactor,
                    &mut utf8_pending,
                    &mut line_pending,
                    &mut dropping_long_line,
                );
                if let Ok(mut state) = state.lock() {
                    state.stats.eof = true;
                }
                return;
            }
            Ok(ManagedPipeReadOutcome::Cancelled) => {
                flush_stderr_pending(
                    state,
                    redactor,
                    &mut utf8_pending,
                    &mut line_pending,
                    &mut dropping_long_line,
                );
                return;
            }
            Ok(ManagedPipeReadOutcome::TimedOut) => continue,
            Err(_) => {
                if let Ok(mut state) = state.lock() {
                    state.stats.read_failed = true;
                }
                return;
            }
        }
    }
}

fn flush_stderr_pending(
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    utf8_pending: &mut Vec<u8>,
    line_pending: &mut String,
    dropping_long_line: &mut bool,
) {
    if !utf8_pending.is_empty() {
        drain_stderr_text(
            state,
            redactor,
            line_pending,
            dropping_long_line,
            &String::from_utf8_lossy(utf8_pending),
        );
        utf8_pending.clear();
    }
    if !line_pending.is_empty() {
        push_stderr_text(state, redactor, line_pending);
        line_pending.clear();
    }
}

fn read_frame_until(
    reader: &mut dyn ManagedPipeReader,
    deadline: Instant,
    cancelled: &AtomicBool,
) -> Result<Option<Value>, String> {
    let mut decoder = FrameDecoder::default();
    let mut chunk = [0_u8; 8192];
    loop {
        match reader
            .read_until(&mut chunk, deadline, cancelled)
            .map_err(|error| error.to_string())?
        {
            ManagedPipeReadOutcome::Read(count) => {
                let mut frames = decoder
                    .feed(&chunk[..count])
                    .map_err(|error| error.to_string())?;
                if let Some(frame) = frames.pop() {
                    if !frames.is_empty() {
                        return Err("TRANSPORT_READ_FAILED: unexpected response burst".to_string());
                    }
                    return Ok(Some(frame));
                }
            }
            ManagedPipeReadOutcome::Eof => {
                decoder.finish().map_err(|error| error.to_string())?;
                return Ok(None);
            }
            ManagedPipeReadOutcome::Cancelled => {
                return Err("TRANSPORT_READ_CANCELLED: stdout read was cancelled".to_string())
            }
            ManagedPipeReadOutcome::TimedOut => {
                return Err(
                    "REQUEST_DEADLINE_EXCEEDED: Core Host response exceeded its deadline"
                        .to_string(),
                )
            }
        }
    }
}

fn drain_stderr_text(
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    line_pending: &mut String,
    dropping_long_line: &mut bool,
    text: &str,
) {
    for segment in text.split_inclusive('\n') {
        let ends_line = segment.ends_with('\n');
        if *dropping_long_line {
            if let Ok(mut state) = state.lock() {
                state.stats.dropped_bytes = state
                    .stats
                    .dropped_bytes
                    .saturating_add(segment.len() as u64);
                state.stats.dropped_records = state.stats.dropped_records.saturating_add(1);
            }
            if ends_line {
                *dropping_long_line = false;
            }
            continue;
        }
        line_pending.push_str(segment);
        if line_pending.len() > STDERR_RECORD_LIMIT {
            if let Ok(mut state) = state.lock() {
                state.stats.truncated_records = state.stats.truncated_records.saturating_add(1);
                state.stats.dropped_bytes = state
                    .stats
                    .dropped_bytes
                    .saturating_add(line_pending.len() as u64);
                state.stats.dropped_records = state.stats.dropped_records.saturating_add(1);
            }
            line_pending.clear();
            *dropping_long_line = !ends_line;
        } else if ends_line {
            push_stderr_text(state, redactor, line_pending);
            line_pending.clear();
        }
    }
}

fn drain_valid_utf8(pending: &mut Vec<u8>, mut consume: impl FnMut(&str)) {
    loop {
        match std::str::from_utf8(pending) {
            Ok(text) => {
                if !text.is_empty() {
                    consume(text);
                }
                pending.clear();
                return;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                if valid > 0 {
                    let text = std::str::from_utf8(&pending[..valid])
                        .expect("UTF-8 validator supplied a valid prefix")
                        .to_string();
                    consume(&text);
                    pending.drain(..valid);
                    continue;
                }
                if let Some(invalid_length) = error.error_len() {
                    consume("\u{fffd}");
                    pending.drain(..invalid_length);
                    continue;
                }
                return;
            }
        }
    }
}

fn push_stderr_text(state: &Arc<Mutex<StderrDrainState>>, redactor: &StderrRedactor, text: &str) {
    let redacted = redactor.redact(text);
    let mut remaining = redacted.as_str();
    while !remaining.is_empty() {
        let mut end = remaining.len().min(STDERR_RECORD_LIMIT);
        while !remaining.is_char_boundary(end) {
            end -= 1;
        }
        let record = remaining[..end].to_string();
        remaining = &remaining[end..];
        let Ok(mut state) = state.lock() else {
            return;
        };
        while state.buffered_bytes.saturating_add(record.len()) > STDERR_CACHE_LIMIT {
            let Some(dropped) = state.records.pop_front() else {
                break;
            };
            state.buffered_bytes = state.buffered_bytes.saturating_sub(dropped.len());
            state.stats.dropped_bytes = state
                .stats
                .dropped_bytes
                .saturating_add(dropped.len() as u64);
            state.stats.dropped_records = state.stats.dropped_records.saturating_add(1);
        }
        state.buffered_bytes = state.buffered_bytes.saturating_add(record.len());
        state.records.push_back(record);
    }
}

fn redact_key_values(text: &str, key: &str) -> String {
    let mut output = String::with_capacity(text.len());
    let lower = text.to_ascii_lowercase();
    let mut cursor = 0;
    while let Some(relative) = lower[cursor..].find(key) {
        let key_start = cursor + relative;
        let key_end = key_start + key.len();
        output.push_str(&text[cursor..key_end]);
        let bytes = text.as_bytes();
        let mut separator = key_end;
        while separator < bytes.len() && bytes[separator].is_ascii_whitespace() {
            separator += 1;
        }
        if separator >= bytes.len() || !matches!(bytes[separator], b'=' | b':') {
            cursor = key_end;
            continue;
        }
        separator += 1;
        while separator < bytes.len() && bytes[separator].is_ascii_whitespace() {
            separator += 1;
        }
        output.push_str(&text[key_end..separator]);
        let quote = bytes
            .get(separator)
            .copied()
            .filter(|byte| matches!(byte, b'\'' | b'"'));
        if quote.is_some() {
            output.push(char::from(quote.expect("quote exists")));
            separator += 1;
        }
        output.push_str("[REDACTED]");
        let mut value_end = separator;
        let redact_to_line_end = matches!(
            key,
            "authorization" | "cookie" | "prompt" | "message" | "content"
        );
        while value_end < bytes.len() {
            if quote.is_some_and(|quote| bytes[value_end] == quote) {
                break;
            }
            if quote.is_none()
                && !redact_to_line_end
                && (bytes[value_end].is_ascii_whitespace()
                    || matches!(bytes[value_end], b',' | b';'))
            {
                break;
            }
            if quote.is_none() && matches!(bytes[value_end], b'\r' | b'\n') {
                break;
            }
            value_end += 1;
        }
        if quote.is_some() && value_end < bytes.len() {
            output.push(char::from(bytes[value_end]));
            value_end += 1;
        }
        cursor = value_end;
    }
    output.push_str(&text[cursor..]);
    output
}

pub struct CoreHostRuntime {
    tree: Box<dyn ManagedProcessTree>,
    stdin: Option<File>,
    stdout: Option<Box<dyn ManagedPipeReader>>,
    stderr_drain: Option<StderrDrainer>,
    generation_id: String,
    generation_credential: String,
    handshake: HandshakeState,
    negotiation: Option<ProtocolNegotiation>,
    deadline_forced: bool,
    snapshot_cache: CoreSnapshotCache,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HandshakeState {
    Pending,
    Complete,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolNegotiation {
    pub major: u64,
    pub minor: u64,
    pub capabilities: Vec<String>,
}

impl std::fmt::Debug for CoreHostRuntime {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CoreHostRuntime")
            .field("root_pid", &self.tree.root_pid())
            .field("generation_id", &self.generation_id)
            .field("handshake", &self.handshake)
            .field("negotiation", &self.negotiation)
            .field("deadline_forced", &self.deadline_forced)
            .field("snapshot_cache", &self.snapshot_cache)
            .finish_non_exhaustive()
    }
}

impl CoreHostRuntime {
    pub fn launch(layout: &RuntimeLayout, generation_id: &str) -> Result<Self, String> {
        validate_runtime_layout(layout)?;
        Self::launch_with_backend(
            &NativeManagedProcessTreeBackend,
            core_host_process_request(layout, generation_id)?,
            generation_id,
        )
    }

    fn launch_with_backend(
        backend: &dyn ManagedProcessTreeBackend,
        request: ManagedProcessRequest,
        generation_id: &str,
    ) -> Result<Self, String> {
        if generation_id.trim().is_empty() {
            return Err("Core Host generation ID must not be empty".to_string());
        }
        let (credential_bytes, generation_credential) = create_generation_credential()?;
        let spawned = backend
            .spawn(&request)
            .map_err(|error| format!("Core Host managed spawn failed: {error}"))?;
        let pipes = spawned
            .pipes
            .ok_or_else(|| "Core Host managed spawn returned no pipes".to_string())?;
        let core_pid = spawned.tree.root_pid();
        let mut stderr_drain = StderrDrainer::start(
            pipes.stderr,
            generation_id,
            core_pid,
            &generation_credential,
        );
        let mut stdin = pipes.stdin;
        if stdin
            .write_all(&credential_bytes)
            .and_then(|_| stdin.flush())
            .is_err()
        {
            let mut tree = spawned.tree;
            let _ = tree.terminate_tree(DEADLINE_EXIT_CODE);
            let _ = tree.wait_root(Duration::from_secs(5));
            let _ = tree.wait_tree_exited(Duration::from_secs(5));
            let _ = stderr_drain.finish_until(Instant::now() + Duration::from_secs(5));
            let _ = tree.release_exited();
            return Err(
                "TRANSPORT_WRITE_FAILED: Core Host credential bootstrap failed".to_string(),
            );
        }
        Ok(Self {
            tree: spawned.tree,
            stdin: Some(stdin),
            stdout: Some(pipes.stdout),
            stderr_drain: Some(stderr_drain),
            generation_id: generation_id.to_string(),
            generation_credential,
            handshake: HandshakeState::Pending,
            negotiation: None,
            deadline_forced: false,
            snapshot_cache: CoreSnapshotCache::new(generation_id)?,
        })
    }

    #[cfg(test)]
    fn launch_script_for_test(
        python: &Path,
        repo_root: &Path,
        script: &Path,
        generation_id: &str,
    ) -> Result<Self, String> {
        Self::launch_with_backend(
            &NativeManagedProcessTreeBackend,
            ManagedProcessRequest {
                program: python.to_path_buf(),
                args: vec![
                    "-I".into(),
                    "-B".into(),
                    "-X".into(),
                    "utf8".into(),
                    script.as_os_str().to_owned(),
                ],
                current_directory: Some(repo_root.to_path_buf()),
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            },
            generation_id,
        )
    }

    pub fn pid(&self) -> u32 {
        self.tree.root_pid()
    }

    pub fn request(
        &mut self,
        request_id: &str,
        name: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        self.request_with_payload(request_id, name, json!({}), deadline)
    }

    pub fn request_with_payload(
        &mut self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<Value, String> {
        if request_id.trim().is_empty() || name.trim().is_empty() || deadline.is_zero() {
            return Err("Core Host control request is invalid".to_string());
        }
        if !payload.is_object() {
            return Err("Core Host control payload must be an object".to_string());
        }
        if self.handshake == HandshakeState::Failed && name != "system.shutdown" {
            return Err("HANDSHAKE_FAILED: protocol negotiation already failed".to_string());
        }
        if self.handshake == HandshakeState::Pending
            && name != "system.hello"
            && name != "system.shutdown"
        {
            return Err("HANDSHAKE_REQUIRED: system.hello must complete first".to_string());
        }
        let is_hello = name == "system.hello";
        let payload = if is_hello && payload.as_object().is_some_and(|value| value.is_empty()) {
            hello_payload()
        } else {
            payload
        };
        let protocol_minor = self
            .negotiation
            .as_ref()
            .map_or(PROTOCOL_MINOR, |negotiation| negotiation.minor);
        let request = json!({
            "protocolMajor": PROTOCOL_MAJOR,
            "protocolMinor": protocol_minor,
            "kind": "request",
            "generationId": self.generation_id,
            "generationCredential": self.generation_credential,
            "id": request_id,
            "name": name,
            "payload": payload,
            "deadlineMs": deadline.as_millis().min(u64::MAX as u128) as u64,
            "priority": CONTROL_PRIORITY,
        });
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "TRANSPORT_WRITE_FAILED: Core Host stdin is closed".to_string())?;
        write_frame(stdin, &request).map_err(|error| error.to_string())?;
        stdin
            .flush()
            .map_err(|_| "TRANSPORT_WRITE_FAILED: Core Host stdin flush failed".to_string())?;

        let response = self.read_response_until(Instant::now() + deadline)?;
        if response.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str())
            || response.get("generationCredential").and_then(Value::as_str)
                != Some(self.generation_credential.as_str())
            || response.get("id").and_then(Value::as_str) != Some(request_id)
            || response.get("name").and_then(Value::as_str) != Some(name)
        {
            let _ = self.tree.terminate_tree(DEADLINE_EXIT_CODE);
            self.deadline_forced = true;
            return Err(
                "GENERATION_CREDENTIAL_MISMATCH: Core Host response identity was stale or invalid"
                    .to_string(),
            );
        }
        if response.get("protocolMajor").and_then(Value::as_u64) != Some(PROTOCOL_MAJOR) {
            self.handshake = HandshakeState::Failed;
            return Err("PROTOCOL_MAJOR_MISMATCH: response major is incompatible".to_string());
        }
        if is_hello {
            if response.get("ok").and_then(Value::as_bool) == Some(true) {
                match parse_negotiation(&response) {
                    Ok(negotiation) => {
                        self.handshake = HandshakeState::Complete;
                        self.negotiation = Some(negotiation);
                    }
                    Err(error) => {
                        self.handshake = HandshakeState::Failed;
                        return Err(error);
                    }
                }
            } else {
                self.handshake = HandshakeState::Failed;
            }
        } else if response.get("protocolMinor").and_then(Value::as_u64) != Some(protocol_minor) {
            return Err("INVALID_NEGOTIATION: response minor changed after handshake".to_string());
        }
        Ok(response)
    }

    pub fn negotiation(&self) -> Option<&ProtocolNegotiation> {
        self.negotiation.as_ref()
    }

    pub fn refresh_snapshot(
        &mut self,
        request_id: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        let response = self.request(request_id, "core.snapshot", deadline)?;
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err("Core Host rejected core.snapshot".to_string());
        }
        let snapshot = response
            .get("payload")
            .ok_or_else(|| "Core Host snapshot response has no payload".to_string())?
            .clone();
        self.snapshot_cache.store_python_snapshot(&snapshot)?;
        Ok(snapshot)
    }

    pub fn cached_snapshot(&self) -> Option<&Value> {
        self.snapshot_cache.current()
    }

    pub fn shutdown(
        mut self,
        protocol_deadline: Duration,
        stop_deadline: Duration,
    ) -> Result<CoreHostExit, String> {
        let response = match self.request("shutdown", "system.shutdown", protocol_deadline) {
            Ok(response) => response,
            Err(error) => {
                self.stdin.take();
                let exit = self.finish_exit(stop_deadline)?;
                if exit.forced {
                    return Ok(exit);
                }
                if error.starts_with("STDOUT_FRAMING_POLLUTION:") {
                    return Err(format!("{error}; cleanup result: {exit:?}"));
                }
                return Err(format!(
                    "Core Host shutdown response failed ({error}); cleanup result: {exit:?}"
                ));
            }
        };
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            self.stdin.take();
            return self.finish_exit(stop_deadline);
        }
        self.stdin.take();
        self.finish_exit(stop_deadline)
    }

    pub fn close_stdin_and_wait(mut self, stop_deadline: Duration) -> Result<CoreHostExit, String> {
        self.stdin.take();
        self.finish_exit(stop_deadline)
    }

    fn read_response_until(&mut self, deadline: Instant) -> Result<Value, String> {
        let stdout = self
            .stdout
            .as_mut()
            .ok_or_else(|| "TRANSPORT_READ_FAILED: Core Host stdout is unavailable".to_string())?;
        let response = match read_frame_until(stdout.as_mut(), deadline, &AtomicBool::new(false)) {
            Ok(response) => response,
            Err(error) if error.starts_with("REQUEST_DEADLINE_EXCEEDED:") => {
                self.tree
                    .terminate_tree(DEADLINE_EXIT_CODE)
                    .map_err(|error| {
                        format!("Core Host response timeout cleanup failed: {error}")
                    })?;
                self.deadline_forced = true;
                return Err(error);
            }
            Err(error) => return Err(error),
        };
        response.ok_or_else(|| match self.tree.wait_root(Duration::from_millis(50)) {
            Ok(ProcessWaitOutcome::Exited(_)) => {
                "CORE_CRASHED: Core Host exited before its response".to_string()
            }
            _ => "STDOUT_EOF: Core Host stdout reached EOF before its response".to_string(),
        })
    }

    fn finish_exit(mut self, stop_deadline: Duration) -> Result<CoreHostExit, String> {
        let mut forced = self.deadline_forced;
        let root_exit_code = match self
            .tree
            .wait_root(stop_deadline)
            .map_err(|error| format!("Core Host root wait failed: {error}"))?
        {
            ProcessWaitOutcome::Exited(status) => process_exit_code(status),
            ProcessWaitOutcome::TimedOut => {
                forced = true;
                self.tree
                    .terminate_tree(DEADLINE_EXIT_CODE)
                    .map_err(|error| format!("Core Host forced cleanup failed: {error}"))?;
                match self
                    .tree
                    .wait_root(Duration::from_secs(5))
                    .map_err(|error| format!("Core Host forced root wait failed: {error}"))?
                {
                    ProcessWaitOutcome::Exited(status) => process_exit_code(status),
                    ProcessWaitOutcome::TimedOut => {
                        return Err("Core Host root survived forced cleanup".to_string())
                    }
                }
            }
        };
        let tree_empty = self
            .tree
            .wait_tree_exited(Duration::from_secs(5))
            .map_err(|error| format!("Core Host Job verification failed: {error}"))?;
        if !tree_empty {
            return Err("Core Host Job retained active processes".to_string());
        }
        let mut trailing_stdout = Vec::new();
        if let Some(mut pipe) = self.stdout.take() {
            let cancelled = AtomicBool::new(false);
            let drain_deadline = Instant::now() + stop_deadline;
            let mut chunk = [0_u8; 8192];
            loop {
                match pipe
                    .read_until(&mut chunk, drain_deadline, &cancelled)
                    .map_err(|error| format!("Core Host stdout drain failed: {error}"))?
                {
                    ManagedPipeReadOutcome::Read(count) => {
                        trailing_stdout.extend_from_slice(&chunk[..count])
                    }
                    ManagedPipeReadOutcome::Eof => break,
                    ManagedPipeReadOutcome::Cancelled => {
                        return Err("Core Host stdout drain was cancelled".to_string())
                    }
                    ManagedPipeReadOutcome::TimedOut => {
                        return Err("Core Host stdout drain exceeded its deadline".to_string())
                    }
                }
            }
        }
        let (stderr, stderr_stats) = self
            .stderr_drain
            .as_mut()
            .ok_or_else(|| "STDERR_READ_FAILED: stderr reader is unavailable".to_string())?
            .finish_until(Instant::now() + stop_deadline)?;
        self.tree
            .release_exited()
            .map_err(|error| format!("Core Host handle release failed: {error}"))?;
        if !trailing_stdout.is_empty() {
            return Err(
                "STDOUT_FRAMING_POLLUTION: Core Host wrote trailing stdout bytes".to_string(),
            );
        }
        Ok(CoreHostExit {
            root_exit_code,
            tree_empty,
            forced,
            stderr,
            stderr_stats,
        })
    }
}

fn core_host_process_request(
    layout: &RuntimeLayout,
    generation_id: &str,
) -> Result<ManagedProcessRequest, String> {
    let resource_root_text = layout.resource_root.to_string_lossy().replace('\\', "/");
    let resource_root = serde_json::to_string(&resource_root_text)
        .map_err(|error| format!("Core Host resource root encoding failed: {error}"))?;
    let core_main = serde_json::to_string(&format!("{}.__main__", layout.core_module))
        .map_err(|error| format!("Core Host module encoding failed: {error}"))?;
    // Official Windows embeddable Python runs with `isolated=1` and its
    // `_pth` file intentionally ignores PYTHONPATH/current-directory
    // discovery. Insert the RuntimeLocator-approved resource root
    // explicitly before importing the Qt-free Core Host module.
    let bootstrap = format!(
        "import runpy,sys;sys.path.insert(0,{resource_root});sys.argv[0]={core_main};runpy.run_module({core_main},run_name='__main__')"
    );
    Ok(ManagedProcessRequest {
        program: layout.python_executable.clone(),
        args: vec![
            "-I".into(),
            "-B".into(),
            "-X".into(),
            "utf8".into(),
            "-c".into(),
            bootstrap.into(),
            "--app-root".into(),
            layout.assistant_root.as_os_str().to_owned(),
            "--generation-id".into(),
            generation_id.into(),
            "--generation-number".into(),
            "1".into(),
        ],
        current_directory: Some(layout.working_directory.clone()),
        environment_overrides: Vec::new(),
        stdio: ProcessStdio::Piped,
    })
}

fn hello_payload() -> Value {
    json!({
        "protocol": {
            "major": PROTOCOL_MAJOR,
            "minMinor": MIN_PROTOCOL_MINOR,
            "maxMinor": PROTOCOL_MINOR,
        },
        "requiredCapabilities": REQUIRED_CAPABILITIES,
        "optionalCapabilities": [],
    })
}

fn parse_negotiation(response: &Value) -> Result<ProtocolNegotiation, String> {
    let negotiated = response
        .get("payload")
        .and_then(|payload| payload.get("negotiated"))
        .and_then(Value::as_object)
        .ok_or_else(|| {
            "INVALID_NEGOTIATION: hello response omitted negotiated result".to_string()
        })?;
    let major = negotiated
        .get("major")
        .and_then(Value::as_u64)
        .ok_or_else(|| "INVALID_NEGOTIATION: negotiated major is invalid".to_string())?;
    let minor = negotiated
        .get("minor")
        .and_then(Value::as_u64)
        .ok_or_else(|| "INVALID_NEGOTIATION: negotiated minor is invalid".to_string())?;
    if major != PROTOCOL_MAJOR || !(MIN_PROTOCOL_MINOR..=PROTOCOL_MINOR).contains(&minor) {
        return Err("INVALID_NEGOTIATION: negotiated version is unsupported".to_string());
    }
    if response.get("protocolMinor").and_then(Value::as_u64) != Some(minor) {
        return Err("INVALID_NEGOTIATION: response envelope minor is inconsistent".to_string());
    }
    let capability_values = negotiated
        .get("capabilities")
        .and_then(Value::as_array)
        .ok_or_else(|| "INVALID_NEGOTIATION: negotiated capabilities are invalid".to_string())?;
    let mut capabilities = Vec::with_capacity(capability_values.len());
    for value in capability_values {
        let capability = value
            .as_str()
            .filter(|capability| !capability.is_empty() && *capability == capability.trim())
            .ok_or_else(|| "INVALID_NEGOTIATION: negotiated capability is invalid".to_string())?;
        if capabilities.iter().any(|existing| existing == capability) {
            return Err("INVALID_NEGOTIATION: negotiated capability is duplicated".to_string());
        }
        capabilities.push(capability.to_string());
    }
    if REQUIRED_CAPABILITIES
        .iter()
        .any(|required| !capabilities.iter().any(|capability| capability == required))
    {
        return Err(
            "CAPABILITY_NEGOTIATION_FAILED: Core Host omitted a required capability".to_string(),
        );
    }
    Ok(ProtocolNegotiation {
        major,
        minor,
        capabilities,
    })
}

fn create_generation_credential() -> Result<([u8; GENERATION_CREDENTIAL_BYTES], String), String> {
    let mut bytes = [0_u8; GENERATION_CREDENTIAL_BYTES];
    fill_os_random(&mut bytes)?;
    let mut encoded = String::with_capacity(GENERATION_CREDENTIAL_BYTES * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut encoded, "{byte:02x}").expect("writing to String cannot fail");
    }
    Ok((bytes, encoded))
}

#[cfg(unix)]
fn fill_os_random(bytes: &mut [u8]) -> Result<(), String> {
    File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(bytes))
        .map_err(|_| "GENERATION_CREDENTIAL_UNAVAILABLE: OS random source failed".to_string())
}

#[cfg(windows)]
fn fill_os_random(bytes: &mut [u8]) -> Result<(), String> {
    #[link(name = "advapi32")]
    unsafe extern "system" {
        #[link_name = "SystemFunction036"]
        fn rtl_gen_random(buffer: *mut std::ffi::c_void, length: u32) -> u8;
    }
    let generated = unsafe {
        rtl_gen_random(
            bytes.as_mut_ptr().cast(),
            u32::try_from(bytes.len()).expect("credential length fits u32"),
        )
    };
    if generated == 0 {
        Err("GENERATION_CREDENTIAL_UNAVAILABLE: OS random source failed".to_string())
    } else {
        Ok(())
    }
}

fn process_exit_code(status: ProcessExitStatus) -> u32 {
    match status {
        ProcessExitStatus::Code(code) => u32::try_from(code).unwrap_or(u32::MAX),
        ProcessExitStatus::Signal(signal) => {
            128_u32.saturating_add(u32::try_from(signal).unwrap_or_default())
        }
        ProcessExitStatus::Unknown => u32::MAX,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        io::{self, Cursor, Read},
        path::PathBuf,
        sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            mpsc, Arc, Mutex, OnceLock,
        },
        thread,
        time::{Duration, Instant},
    };

    use serde_json::{json, Value};

    use crate::{
        platform::{
            FilesystemRuntimeLocator, InstanceLockAcquire, InstanceLockBackend,
            ManagedPipeReadOutcome, ManagedPipeReader, PlatformResult, RuntimeLocationRequest,
            RuntimeLocator, RuntimeMode, SHARED_INSTANCE_ID,
        },
        shared_instance::NativeInstanceLockBackend,
    };

    #[cfg(windows)]
    use crate::{
        core_host_protocol::read_frame,
        managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
    };

    use super::{
        core_host_process_request, drain_stderr, read_frame_until, CoreHostRuntime,
        CoreSnapshotCache, StderrDrainState, StderrDrainStats, StderrDrainer, StderrRedactor,
        STDERR_CACHE_LIMIT,
    };

    const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";
    const WP_1C_04_LIFECYCLE_GOLDEN: &str =
        include_str!("../../../tests/fixtures/runtime_v2/wp_1c_04/lifecycle-golden.json");

    static LIFECYCLE_TEST_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn lifecycle_test_lock() -> std::sync::MutexGuard<'static, ()> {
        LIFECYCLE_TEST_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
    }

    fn development_layout() -> crate::platform::RuntimeLayout {
        let root = repo_root();
        FilesystemRuntimeLocator
            .locate(&RuntimeLocationRequest {
                mode: RuntimeMode::ExplicitDevelopment,
                target: crate::platform::current_platform_target()
                    .expect("tests run on a formal Runtime v2 target"),
                executable_directory: std::env::current_exe()
                    .unwrap()
                    .parent()
                    .unwrap()
                    .to_path_buf(),
                resource_directory: root.clone(),
                explicit_development_root: Some(root.clone()),
                assistant_root: root,
            })
            .expect("repository Runtime should resolve explicitly")
    }

    fn stderr_state() -> Arc<Mutex<StderrDrainState>> {
        Arc::new(Mutex::new(StderrDrainState {
            records: std::collections::VecDeque::new(),
            buffered_bytes: 0,
            stats: StderrDrainStats {
                generation_id: GENERATION_ID.to_string(),
                core_pid: 42,
                bytes_read: 0,
                dropped_bytes: 0,
                dropped_records: 0,
                truncated_records: 0,
                eof: false,
                read_failed: false,
            },
        }))
    }

    struct InjectedTimeoutReader {
        active_readers: Arc<AtomicUsize>,
    }

    impl ManagedPipeReader for InjectedTimeoutReader {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            self.active_readers.fetch_add(1, Ordering::SeqCst);
            self.active_readers.fetch_sub(1, Ordering::SeqCst);
            Ok(ManagedPipeReadOutcome::TimedOut)
        }
    }

    #[test]
    fn timed_out_response_read_leaves_no_active_reader() {
        let active_readers = Arc::new(AtomicUsize::new(0));
        let mut reader = InjectedTimeoutReader {
            active_readers: Arc::clone(&active_readers),
        };
        let error = read_frame_until(
            &mut reader,
            Instant::now() + Duration::from_millis(50),
            &AtomicBool::new(false),
        )
        .expect_err("injected response timeout must fail closed");
        assert!(error.starts_with("REQUEST_DEADLINE_EXCEEDED:"));
        assert_eq!(active_readers.load(Ordering::SeqCst), 0);
    }

    fn valid_assistant_snapshot(code: &str, state: &str, summary: Value) -> Value {
        json!({
            "schemaVersion": 1,
            "generationId": GENERATION_ID,
            "generationNumber": 1,
            "revision": 2,
            "readiness": state,
            "components": {
                "assistant": {"state": state, "code": code, "retryable": false}
            },
            "capabilities": ["core.snapshot"],
            "currentCharacterSummary": summary,
            "activeInteractionSummary": null,
            "coreConfigRevision": 0
        })
    }

    fn valid_character_summary() -> Value {
        json!({
            "id": "sakura",
            "displayName": "Sakura",
            "initialMessage": "hello",
            "replyTones": ["gentle"],
            "portraitChoices": ["default"]
        })
    }

    #[test]
    fn launch_command_uses_only_the_runtime_locator_approved_assistant_root() {
        let mut layout = development_layout();
        layout.assistant_root = std::env::temp_dir().canonicalize().unwrap();
        let request = core_host_process_request(&layout, GENERATION_ID)
            .expect("approved launch command should build");
        let app_root_index = request
            .args
            .iter()
            .position(|argument| argument == "--app-root")
            .expect("launch command must include --app-root");
        assert_eq!(
            request.args[app_root_index + 1].as_os_str(),
            layout.assistant_root.as_os_str()
        );
        assert_ne!(layout.assistant_root, layout.resource_root);
        assert_eq!(
            request
                .args
                .iter()
                .filter(|argument| *argument == "--app-root")
                .count(),
            1
        );
    }

    #[test]
    fn frozen_wp_3_01_assistant_states_are_all_non_retryable() {
        for (state, code, has_summary) in [
            ("ready", "READY", true),
            ("setup_required", "CORE_CONFIG_SETUP_REQUIRED", false),
            ("failed", "CONFIG_DATA_INVALID", false),
            ("failed", "CONFIG_VERSION_UNSUPPORTED", false),
            ("setup_required", "PROVIDER_SETUP_REQUIRED", false),
            ("setup_required", "CHARACTER_SETUP_REQUIRED", false),
            ("failed", "ASSISTANT_INITIALIZATION_FAILED", false),
            ("degraded", "CHARACTER_FALLBACK_APPLIED", true),
            ("degraded", "OPTIONAL_CHARACTER_SKIPPED", true),
        ] {
            let snapshot = valid_assistant_snapshot(
                code,
                state,
                if has_summary {
                    valid_character_summary()
                } else {
                    Value::Null
                },
            );
            let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
            cache
                .store_python_snapshot(&snapshot)
                .unwrap_or_else(|error| panic!("{state}/{code} must validate: {error}"));

            let mut retryable = snapshot.clone();
            retryable["components"]["assistant"]["retryable"] = json!(true);
            assert!(
                cache.store_python_snapshot(&retryable).is_err(),
                "{state}/{code} must never become automatically retryable"
            );
        }
    }

    #[test]
    fn character_summary_requires_the_exact_five_public_fields_and_types() {
        let valid = valid_assistant_snapshot("READY", "ready", valid_character_summary());
        let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
        cache
            .store_python_snapshot(&valid)
            .expect("exact public summary should validate");

        for missing in [
            "id",
            "displayName",
            "initialMessage",
            "replyTones",
            "portraitChoices",
        ] {
            let mut snapshot = valid.clone();
            snapshot["currentCharacterSummary"]
                .as_object_mut()
                .expect("summary object")
                .remove(missing);
            assert!(cache.store_python_snapshot(&snapshot).is_err(), "{missing}");
        }

        for (field, invalid) in [
            ("id", json!(7)),
            ("displayName", json!(false)),
            ("initialMessage", json!(["private"])),
            ("replyTones", json!(["gentle", 7])),
            ("portraitChoices", json!("default")),
        ] {
            let mut snapshot = valid.clone();
            snapshot["currentCharacterSummary"][field] = invalid;
            assert!(cache.store_python_snapshot(&snapshot).is_err(), "{field}");
        }
    }

    #[test]
    fn character_summary_rejects_extra_api_key_credential_and_private_fields() {
        let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
        for forbidden in [
            "apiKey",
            "generationCredential",
            "systemPrompt",
            "privateCharacterPath",
        ] {
            let mut snapshot =
                valid_assistant_snapshot("READY", "ready", valid_character_summary());
            snapshot["currentCharacterSummary"][forbidden] = json!("must-not-leak");
            assert!(
                cache.store_python_snapshot(&snapshot).is_err(),
                "private field {forbidden} must fail closed"
            );
        }
    }

    #[test]
    fn final_assistant_readiness_cannot_omit_its_state_code_and_retryability() {
        let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
        for readiness in ["ready", "setup_required", "degraded", "failed"] {
            let mut snapshot = valid_assistant_snapshot(
                "READY",
                readiness,
                if matches!(readiness, "ready" | "degraded") {
                    valid_character_summary()
                } else {
                    Value::Null
                },
            );
            snapshot["components"] = json!({});
            assert!(
                cache.store_python_snapshot(&snapshot).is_err(),
                "final readiness {readiness} requires the Assistant component"
            );
        }

        let mut transport = valid_assistant_snapshot("READY", "transport_ready", Value::Null);
        transport["components"] = json!({});
        cache
            .store_python_snapshot(&transport)
            .expect("transport readiness precedes Assistant construction");
    }

    #[test]
    fn snapshot_rejects_sensitive_fields_outside_the_character_summary() {
        let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
        for forbidden in ["api-key", "api key", "privateData", "generation_credential"] {
            let mut snapshot =
                valid_assistant_snapshot("READY", "ready", valid_character_summary());
            snapshot[forbidden] = json!("must-not-leak");
            assert!(
                cache.store_python_snapshot(&snapshot).is_err(),
                "{forbidden}"
            );
        }
    }

    struct FragmentedReader {
        bytes: Cursor<Vec<u8>>,
        chunk_size: usize,
    }

    impl Read for FragmentedReader {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let limit = buffer.len().min(self.chunk_size);
            self.bytes.read(&mut buffer[..limit])
        }
    }

    struct FailingReader;

    impl Read for FailingReader {
        fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
            Err(io::Error::other("injected pipe failure"))
        }
    }

    struct TestPipeReader<R> {
        inner: R,
    }

    impl<R: Read + Send> ManagedPipeReader for TestPipeReader<R> {
        fn read_until(
            &mut self,
            buffer: &mut [u8],
            deadline: Instant,
            cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            if cancelled.load(Ordering::Acquire) {
                return Ok(ManagedPipeReadOutcome::Cancelled);
            }
            if Instant::now() >= deadline {
                return Ok(ManagedPipeReadOutcome::TimedOut);
            }
            match self.inner.read(buffer) {
                Ok(0) => Ok(ManagedPipeReadOutcome::Eof),
                Ok(count) => Ok(ManagedPipeReadOutcome::Read(count)),
                Err(error) => Err(crate::platform::PlatformError::new(
                    crate::platform::PlatformService::ManagedProcessTree,
                    crate::platform::PlatformErrorCategory::NativeFailure,
                    "test_pipe_read",
                    crate::platform::RetryAdvice::Never,
                    error.to_string(),
                )),
            }
        }
    }

    #[test]
    fn stderr_drain_handles_lines_fragmented_utf8_invalid_bytes_and_eof() {
        let state = stderr_state();
        let split_secret = "fragmented-secret-value";
        let redactor = StderrRedactor {
            secrets: vec![split_secret.to_string()],
        };
        let mut bytes = format!("ordinary\n多行 UTF-8\nsecret={split_secret}\n").into_bytes();
        bytes.extend_from_slice(&[0xff, 0x00, b'\n']);
        drain_stderr(
            Box::new(TestPipeReader {
                inner: FragmentedReader {
                    bytes: Cursor::new(bytes),
                    chunk_size: 1,
                },
            }),
            &state,
            &redactor,
            &AtomicBool::new(false),
        );
        let state = state.lock().expect("stderr state");
        let output = state.records.iter().cloned().collect::<String>();
        assert!(output.contains("ordinary\n多行 UTF-8\n"));
        assert!(output.contains('\u{fffd}'));
        assert!(output.contains('\0'));
        assert!(!output.contains(split_secret));
        assert!(state.stats.eof);
        assert!(!state.stats.read_failed);
    }

    #[test]
    fn stderr_flood_is_bounded_truncated_counted_and_secret_free() {
        let credential = "88888888888888888888888888888888";
        let environment_value = "controlled-environment-value";
        let state = stderr_state();
        let redactor = StderrRedactor {
            secrets: vec![credential.to_string(), environment_value.to_string()],
        };
        let sensitive = format!(
            "token=plain Authorization: Bearer private cookie=session chat content=hello {credential} {environment_value} "
        );
        let flood = format!("{sensitive}\n{}", "x".repeat(256 * 1024));
        drain_stderr(
            Box::new(TestPipeReader {
                inner: Cursor::new(flood.into_bytes()),
            }),
            &state,
            &redactor,
            &AtomicBool::new(false),
        );
        let state = state.lock().expect("stderr state");
        let output = state.records.iter().cloned().collect::<String>();
        assert!(state.buffered_bytes <= STDERR_CACHE_LIMIT);
        assert!(state.stats.dropped_bytes > 0);
        assert!(state.stats.dropped_records > 0);
        assert!(state.stats.truncated_records > 0);
        assert!(!output.contains(credential));
        assert!(!output.contains(environment_value));
        assert!(!output.contains("Bearer private"));
        assert!(!output.contains("session"));
        assert!(!output.contains("hello"));
    }

    #[test]
    fn stderr_read_failure_and_repeated_finish_are_stable_and_idempotent() {
        let state = stderr_state();
        let redactor = StderrRedactor {
            secrets: Vec::new(),
        };
        drain_stderr(
            Box::new(TestPipeReader {
                inner: FailingReader,
            }),
            &state,
            &redactor,
            &AtomicBool::new(false),
        );
        assert!(state.lock().expect("stderr state").stats.read_failed);

        let cancelled = Arc::new(AtomicBool::new(false));
        let (completion_sender, completion) = mpsc::sync_channel(1);
        completion_sender
            .send(())
            .expect("completed reader notification should send");
        let mut drainer = StderrDrainer {
            state,
            cancelled,
            completion,
            reader: Some(thread::spawn(|| {})),
        };
        let first = drainer
            .finish_until(Instant::now() + Duration::from_secs(1))
            .expect("first finish");
        let second = drainer
            .finish_until(Instant::now() + Duration::from_secs(1))
            .expect("repeated finish");
        assert_eq!(first, second);
        assert!(first.1.read_failed);
    }

    #[test]
    fn real_stderr_flood_never_blocks_protocol_and_is_bounded_and_redacted() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_03/stderr_flood_host.py");
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("stderr flood fixture launches");
        let credential = host.generation_credential.clone();
        host.request("hello-flood", "system.hello", Duration::from_secs(3))
            .expect("stderr flood must not block hello");
        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("stderr flood fixture stops cleanly");
        assert!(exit.stderr_stats.eof);
        assert!(!exit.stderr_stats.read_failed);
        assert!(exit.stderr_stats.bytes_read > 1024 * 1024);
        assert!(exit.stderr_stats.dropped_bytes > 0);
        assert!(exit.stderr_stats.dropped_records > 0);
        assert!(exit.stderr_stats.truncated_records > 0);
        assert!(exit.stderr.len() <= STDERR_CACHE_LIMIT);
        assert!(!exit.stderr.contains(&credential));
        for secret in ["private", "Bearer", "session", "user-chat"] {
            assert!(!exit.stderr.contains(secret));
        }
    }

    #[test]
    fn core_crash_reclaims_stderr_reader_and_returns_redacted_diagnostics() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_03/stderr_crash_host.py");
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("stderr crash fixture launches");
        let error = host
            .request("hello-crash", "system.hello", Duration::from_secs(3))
            .expect_err("crashed Core cannot answer hello");
        assert!(error.starts_with("CORE_CRASHED:"));
        let exit = host
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("crashed Core resources are finalized");
        assert_eq!(exit.root_exit_code, 42);
        assert!(exit.stderr_stats.eof);
        assert!(!exit.stderr.contains("must-not-leak"));
    }

    #[test]
    fn managed_real_python_host_answers_control_and_releases_its_job_and_pipes() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host = CoreHostRuntime::launch(&layout, GENERATION_ID)
            .expect("real Core Host should launch in a managed Job");
        assert!(host.pid() > 0);

        let hello = host
            .request("hello", "system.hello", Duration::from_secs(3))
            .expect("hello should respond");
        assert_eq!(hello["ok"], true);
        assert_eq!(hello["payload"]["hostState"], "transport_ready");

        for index in 0..2 {
            let health = host
                .request(
                    &format!("health-{index}"),
                    "system.health",
                    Duration::from_secs(3),
                )
                .expect("health should respond");
            assert_eq!(health["payload"]["status"], "healthy");
        }

        let unknown = host
            .request("unknown", "system.unknown", Duration::from_secs(3))
            .expect("unknown control should return a framed error");
        assert_eq!(unknown["ok"], false);
        assert_eq!(unknown["error"]["code"], "UNKNOWN_CONTROL");

        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("protocol shutdown should reclaim the complete Job");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
    }

    #[test]
    fn protocol_minor_capabilities_and_major_failure_are_negotiated_before_initialize() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut compatible =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("compatible Host launches");
        let hello = compatible
            .request_with_payload(
                "hello-minor-zero",
                "system.hello",
                json!({
                    "protocol": {"major": 2, "minMinor": 0, "maxMinor": 0},
                    "requiredCapabilities": [
                        "system.hello", "system.health", "system.shutdown",
                        "core.initialize", "core.snapshot"
                    ],
                    "optionalCapabilities": ["future.optional"]
                }),
                Duration::from_secs(3),
            )
            .expect("minor zero should negotiate");
        assert_eq!(hello["protocolMinor"], 0);
        assert_eq!(compatible.negotiation().expect("negotiation").minor, 0);
        assert_eq!(
            compatible
                .negotiation()
                .expect("negotiation")
                .capabilities
                .len(),
            5
        );
        compatible
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("compatible Host stops");

        let mut incompatible =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("incompatible Host launches");
        let hello = incompatible
            .request_with_payload(
                "hello-major-three",
                "system.hello",
                json!({
                    "protocol": {"major": 3, "minMinor": 0, "maxMinor": 1},
                    "requiredCapabilities": ["core.initialize"],
                    "optionalCapabilities": []
                }),
                Duration::from_secs(3),
            )
            .expect("Core returns a framed incompatibility");
        assert_eq!(hello["error"]["code"], "PROTOCOL_MAJOR_MISMATCH");
        let error = incompatible
            .request_with_payload(
                "initialize-after-failure",
                "core.initialize",
                json!({}),
                Duration::from_secs(3),
            )
            .expect_err("initialize must not continue after failed hello");
        assert!(error.starts_with("HANDSHAKE_FAILED:"));
        incompatible
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("failed handshake Host stops on stdin EOF");
    }

    #[test]
    fn generation_credentials_are_unique_and_never_enter_debug_or_snapshot() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut first =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("first Host launches");
        let first_credential = first.generation_credential.clone();
        assert!(!format!("{first:?}").contains(&first_credential));
        first
            .request("hello-first", "system.hello", Duration::from_secs(3))
            .expect("first hello");
        let snapshot = first
            .refresh_snapshot("snapshot-first", Duration::from_secs(3))
            .expect("first snapshot");
        assert!(!snapshot.to_string().contains(&first_credential));
        first
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("first Host stops");

        let second = CoreHostRuntime::launch(&layout, GENERATION_ID).expect("second Host launches");
        let second_credential = second.generation_credential.clone();
        assert!(
            first_credential != second_credential,
            "each generation must receive a unique credential"
        );
        assert!(!format!("{second:?}").contains(&second_credential));
        second
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("second Host stops");
    }

    #[test]
    fn stale_generation_response_is_rejected_and_force_reclaimed_without_secret_echo() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_03/stale_credential_host.py");
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("stale response fixture launches");
        let credential = host.generation_credential.clone();
        let error = host
            .request("hello-stale", "system.hello", Duration::from_secs(3))
            .expect_err("stale credential response must fail");
        assert!(error.starts_with("GENERATION_CREDENTIAL_MISMATCH:"));
        assert!(!error.contains(&credential));
        let exit = host
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("stale generation tree is reclaimed");
        assert!(exit.tree_empty);
        assert!(exit.forced);
    }

    fn lifecycle_golden() -> Value {
        serde_json::from_str(WP_1C_04_LIFECYCLE_GOLDEN)
            .expect("WP-1C-04 lifecycle golden should parse")
    }

    fn golden_deadline(golden: &Value, name: &str) -> Duration {
        Duration::from_millis(
            golden["deadlinesMs"][name]
                .as_u64()
                .unwrap_or_else(|| panic!("missing WP-1C-04 {name} deadline")),
        )
    }

    fn packaged_layout() -> crate::platform::RuntimeLayout {
        let resource_directory = PathBuf::from(
            std::env::var_os("SAKURA_WP_1C_04_PACKAGED_RESOURCES")
                .expect("packaged resource fixture environment is required"),
        )
        .canonicalize()
        .expect("packaged resource fixture should resolve");
        FilesystemRuntimeLocator
            .locate(&RuntimeLocationRequest {
                mode: RuntimeMode::Packaged,
                target: crate::platform::current_platform_target()
                    .expect("tests run on a formal Runtime v2 target"),
                executable_directory: std::env::current_exe()
                    .unwrap()
                    .parent()
                    .unwrap()
                    .to_path_buf(),
                resource_directory,
                explicit_development_root: None,
                assistant_root: repo_root(),
            })
            .expect("staged packaged Runtime should resolve")
    }

    fn complete_assistant_lifecycle(
        layout: &crate::platform::RuntimeLayout,
        generation_id: &str,
        golden: &Value,
    ) -> (super::CoreHostExit, String) {
        let mut host = CoreHostRuntime::launch(layout, generation_id)
            .expect("bundled Python Core Host should launch");
        let credential = host.generation_credential.clone();
        let hello = host
            .request("hello", "system.hello", golden_deadline(golden, "hello"))
            .expect("hello should negotiate");
        assert_eq!(hello["ok"], true);
        let initialize = host
            .request_with_payload(
                "initialize",
                "core.initialize",
                json!({}),
                golden_deadline(golden, "initializeAcceptance"),
            )
            .expect("initialize should be accepted");
        assert_eq!(initialize["payload"]["readiness"], "initializing");
        let readiness_deadline =
            std::time::Instant::now() + golden_deadline(golden, "readinessWatchdog");
        loop {
            let snapshot = host
                .refresh_snapshot("snapshot", golden_deadline(golden, "request"))
                .expect("Snapshot should respond");
            if snapshot["readiness"] != "initializing" {
                break;
            }
            assert!(
                std::time::Instant::now() < readiness_deadline,
                "readiness watchdog expired"
            );
        }
        let health = host
            .request(
                "health",
                "system.health",
                golden_deadline(golden, "request"),
            )
            .expect("health should respond");
        assert_eq!(health["payload"]["status"], "healthy");
        let exit = host
            .shutdown(
                golden_deadline(golden, "shutdown"),
                golden_deadline(golden, "treeStop"),
            )
            .expect("protocol shutdown should clean the bundled Core tree");
        (exit, credential)
    }

    #[test]
    fn wp_1c_04_shared_golden_freezes_lifecycle_order_and_deadlines() {
        let golden = lifecycle_golden();
        assert_eq!(golden["schemaVersion"], 1);
        assert_eq!(
            golden["lifecycle"],
            json!([
                "system.hello",
                "core.initialize",
                "core.readiness",
                "core.snapshot",
                "system.health",
                "system.shutdown"
            ])
        );
        assert_eq!(golden_deadline(&golden, "hello"), Duration::from_secs(3));
        assert_eq!(
            golden_deadline(&golden, "initializeAcceptance"),
            Duration::from_secs(5)
        );
        assert_eq!(
            golden_deadline(&golden, "readinessWatchdog"),
            Duration::from_secs(30)
        );
        assert_eq!(golden_deadline(&golden, "shutdown"), Duration::from_secs(3));
        assert_eq!(golden_deadline(&golden, "treeStop"), Duration::from_secs(5));
    }

    #[test]
    fn core_host_launch_rejects_layouts_outside_the_locator_contract_before_spawn() {
        let mut wrong_architecture = development_layout();
        wrong_architecture.architecture = match wrong_architecture.architecture {
            crate::platform::RuntimeArchitecture::X64 => {
                crate::platform::RuntimeArchitecture::Arm64
            }
            crate::platform::RuntimeArchitecture::Arm64 => {
                crate::platform::RuntimeArchitecture::X64
            }
        };
        let error = CoreHostRuntime::launch(&wrong_architecture, GENERATION_ID)
            .expect_err("incompatible layout architecture must fail before spawn");
        assert!(error.contains("architecture"));

        let mut inconsistent_entry = development_layout();
        inconsistent_entry.core_entry = inconsistent_entry.python_executable.clone();
        let error = CoreHostRuntime::launch(&inconsistent_entry, GENERATION_ID)
            .expect_err("inconsistent Core entry must fail before spawn");
        assert!(error.contains("Core entry"));

        let mut escaped_resources = development_layout();
        escaped_resources.runtime_root = escaped_resources
            .runtime_root
            .join("runtime")
            .canonicalize()
            .expect("development Runtime directory should resolve");
        let error = CoreHostRuntime::launch(&escaped_resources, GENERATION_ID)
            .expect_err("resources outside the Runtime root must fail before spawn");
        assert!(error.contains("resources"));
    }

    #[test]
    #[ignore = "requires the exact packaged target Runtime staged by the platform CI job"]
    fn staged_packaged_runtime_runs_lifecycle_faults_and_clean_generations() {
        let _test_lock = lifecycle_test_lock();
        let golden = lifecycle_golden();
        let layout = packaged_layout();
        assert_eq!(layout.mode, RuntimeMode::Packaged);
        assert_eq!(layout.architecture, layout.target.architecture());
        assert!(layout.python_executable.starts_with(&layout.runtime_root));
        assert!(layout.resource_root.starts_with(&layout.runtime_root));
        assert_eq!(layout.working_directory, layout.resource_root);
        assert!(layout.core_entry.starts_with(&layout.resource_root));

        let first_generation = "00000000-0000-4000-8000-000000004001";
        let second_generation = "00000000-0000-4000-8000-000000004002";
        let (first_exit, first_credential) =
            complete_assistant_lifecycle(&layout, first_generation, &golden);
        assert_eq!(first_exit.root_exit_code, 0);
        assert!(first_exit.tree_empty);
        assert!(!first_exit.forced);

        let (second_exit, second_credential) =
            complete_assistant_lifecycle(&layout, second_generation, &golden);
        assert_eq!(second_exit.root_exit_code, 0);
        assert!(second_exit.tree_empty);
        assert!(!second_exit.forced);
        assert_ne!(first_credential, second_credential);

        let mut final_readiness =
            CoreHostRuntime::launch(&layout, "00000000-0000-4000-8000-000000004003")
                .expect("final-readiness generation launches");
        final_readiness
            .request(
                "hello-final",
                "system.hello",
                golden_deadline(&golden, "hello"),
            )
            .expect("final-readiness hello negotiates");
        final_readiness
            .request_with_payload(
                "initialize-final",
                "core.initialize",
                json!({}),
                golden_deadline(&golden, "initializeAcceptance"),
            )
            .expect("real initialization is accepted");
        let readiness_deadline =
            std::time::Instant::now() + golden_deadline(&golden, "readinessWatchdog");
        loop {
            let snapshot = final_readiness
                .refresh_snapshot("snapshot-final", golden_deadline(&golden, "request"))
                .expect("final readiness Snapshot responds");
            if snapshot["readiness"] != "initializing" {
                break;
            }
            assert!(std::time::Instant::now() < readiness_deadline);
        }
        let final_exit = final_readiness
            .shutdown(
                golden_deadline(&golden, "shutdown"),
                golden_deadline(&golden, "treeStop"),
            )
            .expect("final readiness generation cleans up");
        assert!(final_exit.tree_empty);

        let root = repo_root();
        let crash_fixture = root.join("tests/fixtures/runtime_v2/wp_1c_03/stderr_crash_host.py");
        let mut crashed = CoreHostRuntime::launch_script_for_test(
            &layout.python_executable,
            &layout.working_directory,
            &crash_fixture,
            "00000000-0000-4000-8000-000000004004",
        )
        .expect("bundled Python crash fixture launches");
        let error = crashed
            .request(
                "hello-crash",
                "system.hello",
                golden_deadline(&golden, "hello"),
            )
            .expect_err("crashed bundled Core cannot answer hello");
        assert!(error.starts_with("CORE_CRASHED:"));
        let crash_exit = crashed
            .close_stdin_and_wait(golden_deadline(&golden, "treeStop"))
            .expect("crashed bundled Core resources finalize");
        assert!(crash_exit.tree_empty);

        let ignoring_fixture =
            root.join("tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py");
        let ignoring = CoreHostRuntime::launch_script_for_test(
            &layout.python_executable,
            &layout.working_directory,
            &ignoring_fixture,
            "00000000-0000-4000-8000-000000004005",
        )
        .expect("bundled Python ignored-shutdown fixture launches");
        let forced = ignoring
            .shutdown(
                Duration::from_millis(250),
                golden_deadline(&golden, "treeStop"),
            )
            .expect("ignored shutdown force-cleans bundled Core tree");
        assert!(forced.forced);
        assert!(forced.tree_empty);
    }

    #[test]
    fn trailing_stdout_pollution_is_rejected_after_a_valid_shutdown_response() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_03/trailing_stdout_host.py");
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("trailing stdout fixture launches");
        host.request("hello-trailing", "system.hello", Duration::from_secs(3))
            .expect("fixture hello negotiates");
        let error = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect_err("trailing stdout must be transport fatal");
        assert!(
            error.starts_with("STDOUT_FRAMING_POLLUTION:"),
            "unexpected trailing stdout error: {error}"
        );
    }

    #[test]
    fn managed_real_python_host_treats_clean_stdin_eof_as_orderly_exit() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let host = CoreHostRuntime::launch(&layout, GENERATION_ID)
            .expect("real Core Host should launch in a managed Job");
        let exit = host
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("stdin EOF should stop and reclaim the Core Host");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
    }

    #[test]
    #[cfg(windows)]
    fn polluted_real_stdout_fails_framing_and_the_job_is_force_reclaimed() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/polluting_host.py");
        let mut spec = ManagedProcessSpec::new(python);
        spec.arg(fixture.as_os_str()).current_dir(&root);
        let (mut tree, pipes) =
            ManagedProcessTree::spawn_piped(&spec).expect("polluting fixture should launch");
        let (sender, receiver) = mpsc::sync_channel(1);
        let pollution_reader = thread::spawn(move || {
            let mut stdout = pipes.stdout;
            let result = read_frame(&mut stdout);
            let _ = sender.send((pipes.stdin, stdout, pipes.stderr, result));
        });
        let (stdin, stdout, stderr, result) = receiver
            .recv_timeout(Duration::from_secs(3))
            .expect("pollution should be observed before deadline");
        assert_eq!(
            result.expect_err("pollution must not decode").code,
            "STDOUT_FRAMING_POLLUTION"
        );
        tree.terminate_tree(97)
            .expect("polluting fixture Job should terminate");
        assert_eq!(
            tree.wait(Duration::from_secs(5)).expect("root wait"),
            WaitOutcome::Exited(97)
        );
        assert!(tree
            .verify_tree_exited(Duration::from_secs(5))
            .expect("Job query"));
        tree.release_exited_handles().expect("handle release");
        drop((stdin, stdout, stderr));
        pollution_reader
            .join()
            .expect("pollution reader should join");
    }

    #[test]
    fn ignored_control_deadline_force_reclaims_the_managed_python_job() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py");
        let host = CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
            .expect("ignoring fixture should launch");
        let exit = host
            .shutdown(Duration::from_millis(250), Duration::from_secs(5))
            .expect("ignored shutdown should force and finalize its Job");
        #[cfg(windows)]
        assert_eq!(exit.root_exit_code, 93);
        #[cfg(unix)]
        assert_eq!(exit.root_exit_code, 143);
        assert!(exit.tree_empty);
        assert!(exit.forced);
    }

    #[test]
    fn python_snapshot_cache_is_read_only_and_clears_on_new_generation() {
        let first_generation = "00000000-0000-4000-8000-000000001c02";
        let second_generation = "00000000-0000-4000-8000-000000002c02";
        let snapshot = json!({
            "schemaVersion": 1,
            "generationId": first_generation,
            "generationNumber": 1,
            "revision": 2,
            "readiness": "transport_ready",
            "components": {"fixture": {"state": "ready", "pythonOwned": [1, 2, 3]}},
            "capabilities": ["core.snapshot"],
            "currentCharacterSummary": null,
            "activeInteractionSummary": null,
            "coreConfigRevision": 0
        });
        let mut cache = CoreSnapshotCache::new(first_generation).expect("generation cache");
        cache
            .store_python_snapshot(&snapshot)
            .expect("Python snapshot should cache");
        assert_eq!(cache.current(), Some(&snapshot));

        cache
            .begin_generation(second_generation)
            .expect("new generation should start");
        assert_eq!(cache.current(), None);
        assert!(cache.store_python_snapshot(&snapshot).is_err());
    }

    #[test]
    fn managed_real_python_host_initializes_and_caches_its_snapshot() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        host.request("hello", "system.hello", Duration::from_secs(3))
            .expect("hello should negotiate");
        let initialize = host
            .request_with_payload(
                "initialize",
                "core.initialize",
                json!({}),
                Duration::from_secs(3),
            )
            .expect("initialize should be accepted");
        assert_eq!(initialize["payload"]["readiness"], "initializing");

        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            let snapshot = host
                .refresh_snapshot("snapshot", Duration::from_secs(3))
                .expect("snapshot should respond");
            if snapshot["readiness"] != "initializing" {
                assert_eq!(host.cached_snapshot(), Some(&snapshot));
                assert_eq!(snapshot["generationId"], GENERATION_ID);
                break;
            }
            assert!(std::time::Instant::now() < deadline);
        }

        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("initialized Host should stop cleanly");
        assert_eq!(exit.root_exit_code, 0);
        assert!(!exit.forced);
    }

    #[test]
    fn real_python_initialize_keeps_health_and_shutdown_responsive() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        host.request("hello", "system.hello", Duration::from_secs(3))
            .expect("hello should negotiate");
        host.request_with_payload(
            "initialize",
            "core.initialize",
            json!({}),
            Duration::from_secs(3),
        )
        .expect("real initialize should be accepted quickly");
        for index in 0..3 {
            let health = host
                .request(
                    &format!("health-hang-{index}"),
                    "system.health",
                    Duration::from_secs(3),
                )
                .expect("health should remain responsive");
            assert_eq!(health["payload"]["status"], "healthy");
            assert!(matches!(
                health["payload"]["hostState"].as_str(),
                Some("initializing" | "ready" | "setup_required" | "degraded" | "failed")
            ));
        }
        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("shutdown should cancel or close real initialize");
        assert_eq!(exit.root_exit_code, 0);
        assert!(!exit.forced);
    }

    #[test]
    fn minimum_lifecycle_releases_and_reacquires_the_shared_lock() {
        let lock_backend = NativeInstanceLockBackend;
        let first = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("shared lock should be acquirable");
        assert!(matches!(first, InstanceLockAcquire::Acquired(_)));
        let conflict = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("second lock attempt should be classified");
        assert!(matches!(conflict, InstanceLockAcquire::AlreadyRunning));
        drop(first);
        let reacquired = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("lock should be immediately reacquirable after release");
        assert!(matches!(reacquired, InstanceLockAcquire::Acquired(_)));
        drop(reacquired);
    }
}

fn validate_runtime_layout(layout: &RuntimeLayout) -> Result<(), String> {
    let current_target = crate::platform::current_platform_target()
        .ok_or_else(|| "Core Host requires a formal Runtime v2 target".to_string())?;
    if layout.target != current_target || layout.architecture != layout.target.architecture() {
        return Err("Core Host Runtime layout target or architecture is incompatible".to_string());
    }
    if layout.core_module != "app.core_host" || layout.source_id.trim().is_empty() {
        return Err("Core Host Runtime layout identity is invalid".to_string());
    }
    for path in [
        &layout.runtime_root,
        &layout.python_executable,
        &layout.resource_root,
        &layout.assistant_root,
        &layout.core_entry,
        &layout.working_directory,
    ] {
        if !path.is_absolute() {
            return Err("Core Host Runtime layout paths must be absolute".to_string());
        }
        if fs::canonicalize(path).ok().as_ref() != Some(path) {
            return Err("Core Host Runtime layout paths must be canonical".to_string());
        }
    }
    if !layout.runtime_root.is_dir()
        || !layout.python_executable.is_file()
        || !layout.resource_root.is_dir()
        || !layout.assistant_root.is_dir()
        || !layout.core_entry.is_file()
        || !layout.working_directory.is_dir()
        || !layout.python_executable.starts_with(&layout.runtime_root)
        || !layout.resource_root.starts_with(&layout.runtime_root)
        || !layout.core_entry.starts_with(&layout.resource_root)
        || !layout.working_directory.starts_with(&layout.resource_root)
    {
        return Err("Core Host Runtime layout resources are invalid".to_string());
    }
    let located_entry = layout
        .resource_root
        .join(layout.core_module.replace('.', "/"))
        .join("__main__.py");
    if fs::canonicalize(located_entry).ok().as_ref() != Some(&layout.core_entry) {
        return Err("Core Host Runtime layout Core entry is inconsistent".to_string());
    }
    Ok(())
}
