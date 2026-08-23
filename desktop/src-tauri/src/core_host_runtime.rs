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

#[cfg(any(test, debug_assertions))]
use std::path::Path;

#[cfg(unix)]
use std::io::Read;

use serde_json::{json, Value};

use crate::{
    character_presentation::CharacterPresentation,
    core_host_gateway::CoreHostGateway,
    core_host_protocol::{write_frame, FrameDecoder, PROTOCOL_MAJOR, PROTOCOL_MINOR},
    core_host_router::{CoreHostRouter, CoreHostRouterHandle},
    platform::{
        ManagedPipeReadOutcome, ManagedPipeReader, ManagedProcessRequest, ManagedProcessTree,
        ManagedProcessTreeBackend, NativeManagedProcessTreeBackend, ProcessExitStatus,
        ProcessStdio, ProcessTreeFinalizationResult, ProcessWaitOutcome, RuntimeLayout,
    },
    runtime_log::{
        CoreLogContext, Correlation, RuntimeLogEvent, RuntimeLogService, Severity,
        CORE_BRIDGE_PREFIX,
    },
};

const CONTROL_PRIORITY: &str = "control";
const DEADLINE_EXIT_CODE: u32 = 93;
const MIN_PROTOCOL_MINOR: u64 = 0;
const GENERATION_CREDENTIAL_BYTES: usize = 16;
const STDERR_READ_CHUNK_SIZE: usize = 4 * 1024;
const STDERR_READ_SLICE: Duration = Duration::from_millis(10);
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
const OPTIONAL_CAPABILITIES: [&str; 7] = [
    "transport.concurrent-router",
    "settings.provider-model",
    "assistant.tools-v1",
    "assistant.mcp-v1",
    "assistant.plugins-v1",
    "assistant.tts-v1",
    "assistant.screen-capture-v1",
];
const SNAPSHOT_READINESS: [&str; 6] = [
    "transport_ready",
    "initializing",
    "setup_required",
    "ready",
    "degraded",
    "failed",
];

#[cfg(test)]
static LIFECYCLE_TEST_LOCK: std::sync::OnceLock<Mutex<()>> = std::sync::OnceLock::new();

#[cfg(test)]
pub(crate) fn lifecycle_test_lock() -> std::sync::MutexGuard<'static, ()> {
    LIFECYCLE_TEST_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[derive(Clone, Copy)]
struct ShutdownPolicy {
    graceful: Duration,
    total: Duration,
}

const PRODUCTION_SHUTDOWN_POLICY: ShutdownPolicy = ShutdownPolicy {
    graceful: Duration::from_millis(3000),
    total: Duration::from_millis(5000),
};

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
        if let Some(presentation) = object
            .get("characterPresentation")
            .filter(|value| !value.is_null())
        {
            let parsed = CharacterPresentation::from_value(presentation, &self.generation_id)?;
            if let Some(summary) = object
                .get("currentCharacterSummary")
                .and_then(Value::as_object)
            {
                if summary.get("id").and_then(Value::as_str) != Some(parsed.character_id.as_str())
                    || summary.get("displayName").and_then(Value::as_str)
                        != Some(parsed.display_name.as_str())
                    || summary.get("initialMessage").and_then(Value::as_str)
                        != Some(parsed.initial_message.as_str())
                {
                    return Err(
                        "Core Snapshot character presentation conflicts with summary".to_string(),
                    );
                }
            }
        }
        validate_assistant_readiness(object, readiness)?;
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }

    pub fn store_minimal_python_snapshot(&mut self, snapshot: &Value) -> Result<(), String> {
        let object = snapshot
            .as_object()
            .ok_or_else(|| "Core Snapshot must be an object".to_string())?;
        let expected = [
            "generationId",
            "revision",
            "readiness",
            "currentCharacterSummary",
            "characterPresentation",
            "activeInteractionSummary",
        ];
        if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
            return Err("Core Snapshot fields do not match the WP-2-02 shape".to_string());
        }
        if object.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str()) {
            return Err("Core Snapshot belongs to another generation".to_string());
        }
        let revision = object
            .get("revision")
            .and_then(Value::as_u64)
            .ok_or_else(|| "Core Snapshot revision is invalid".to_string())?;
        if let Some(current) = self.snapshot.as_ref() {
            let current_revision = current
                .get("revision")
                .and_then(Value::as_u64)
                .unwrap_or_default();
            if revision < current_revision || (revision == current_revision && current != snapshot)
            {
                return Err("Core Snapshot revision is stale or reused".to_string());
            }
        }
        let readiness = object
            .get("readiness")
            .and_then(Value::as_str)
            .ok_or_else(|| "Core Snapshot readiness is invalid".to_string())?;
        if !SNAPSHOT_READINESS.contains(&readiness) {
            return Err("Core Snapshot readiness is unsupported".to_string());
        }
        validate_character_summary(object.get("currentCharacterSummary"))?;
        if let Some(presentation) = object
            .get("characterPresentation")
            .filter(|value| !value.is_null())
        {
            let parsed = CharacterPresentation::from_value(presentation, &self.generation_id)?;
            if let Some(summary) = object
                .get("currentCharacterSummary")
                .and_then(Value::as_object)
            {
                if summary.get("id").and_then(Value::as_str) != Some(parsed.character_id.as_str())
                    || summary.get("displayName").and_then(Value::as_str)
                        != Some(parsed.display_name.as_str())
                    || summary.get("initialMessage").and_then(Value::as_str)
                        != Some(parsed.initial_message.as_str())
                {
                    return Err(
                        "Core Snapshot character presentation conflicts with summary".to_string(),
                    );
                }
            }
        }
        validate_active_interaction_summary(object.get("activeInteractionSummary"))?;
        reject_sensitive_snapshot_fields(snapshot)?;
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }

    pub fn current(&self) -> Option<&Value> {
        self.snapshot.as_ref()
    }
}

fn validate_active_interaction_summary(summary: Option<&Value>) -> Result<(), String> {
    let Some(summary) = summary else {
        return Err("Core Snapshot activeInteractionSummary is missing".to_string());
    };
    if summary.is_null() {
        return Ok(());
    }
    let object = summary
        .as_object()
        .ok_or_else(|| "Core Snapshot activeInteractionSummary is invalid".to_string())?;
    if object.len() != 2
        || object
            .get("operationId")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        || !matches!(
            object.get("state").and_then(Value::as_str),
            Some("started" | "cancelling")
        )
    {
        return Err("Core Snapshot activeInteractionSummary fields are invalid".to_string());
    }
    Ok(())
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
                let approved_public_token_field = key == "themeTokens";
                if !approved_public_token_field
                    && [
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

pub struct CoreHostLifecycleFailure {
    diagnostic: String,
    recovery: Option<CoreHostRecovery>,
}

pub(crate) struct CoreHostRecovery {
    tree: Box<dyn ManagedProcessTree>,
}

impl CoreHostRecovery {
    pub(crate) fn finalize_until(self, deadline: Instant) -> ProcessTreeFinalizationResult {
        self.tree.finalize_until(deadline, DEADLINE_EXIT_CODE)
    }
}

impl CoreHostLifecycleFailure {
    fn without_recovery(diagnostic: impl Into<String>) -> Self {
        Self {
            diagnostic: diagnostic.into(),
            recovery: None,
        }
    }

    pub fn diagnostic(&self) -> &str {
        &self.diagnostic
    }

    pub(crate) fn into_recovery(self) -> Option<CoreHostRecovery> {
        self.recovery
    }

    pub(crate) fn into_terminal_diagnostic(self) -> String {
        self.diagnostic
    }
}

impl std::fmt::Debug for CoreHostLifecycleFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CoreHostLifecycleFailure")
            .field("diagnostic", &self.diagnostic)
            .field("has_recovery", &self.recovery.is_some())
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StderrDrainStats {
    pub generation_id: String,
    pub core_pid: u32,
    pub bytes_read: u64,
    pub dropped_bytes: u64,
    pub dropped_records: u64,
    pub truncated_records: u64,
    pub structured_records: u64,
    pub ordinary_records: u64,
    pub invalid_structured_records: u64,
    pub eof: bool,
    pub read_failed: bool,
}

#[derive(Debug)]
struct StderrDrainState {
    records: VecDeque<String>,
    buffered_bytes: usize,
    stats: StderrDrainStats,
    ordinary_warning_emitted: bool,
}

#[derive(Clone)]
struct StderrLogSink {
    runtime_log: RuntimeLogService,
    context: CoreLogContext,
    generation_credential: String,
}

struct StderrDrainer {
    state: Arc<Mutex<StderrDrainState>>,
    cancelled: Arc<AtomicBool>,
    completion: mpsc::Receiver<()>,
    reader: Option<thread::JoinHandle<()>>,
    log_sink: Option<StderrLogSink>,
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
        log_sink: Option<StderrLogSink>,
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
                structured_records: 0,
                ordinary_records: 0,
                invalid_structured_records: 0,
                eof: false,
                read_failed: false,
            },
            ordinary_warning_emitted: false,
        }));
        let reader_state = Arc::clone(&state);
        let cancelled = Arc::new(AtomicBool::new(false));
        let reader_cancelled = Arc::clone(&cancelled);
        let (completion_sender, completion) = mpsc::sync_channel(1);
        let redactor = StderrRedactor::new(generation_credential);
        let reader_log_sink = log_sink.clone();
        let reader = thread::Builder::new()
            .name(format!("sakura-core-stderr-{core_pid}"))
            .spawn(move || {
                drain_stderr(
                    pipe,
                    &reader_state,
                    &redactor,
                    &reader_cancelled,
                    reader_log_sink.as_ref(),
                );
                let _ = completion_sender.send(());
            })
            .expect("stderr reader thread creation must succeed");
        Self {
            state,
            cancelled,
            completion,
            reader: Some(reader),
            log_sink,
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
            let Some(reader_handle) = self.reader.take() else {
                return Err("STDERR_READ_FAILED: stderr reader ownership was missing".to_string());
            };
            reader_handle
                .join()
                .map_err(|_| "STDERR_READ_FAILED: stderr reader panicked".to_string())?;
        }
        let state = self
            .state
            .lock()
            .map_err(|_| "STDERR_READ_FAILED: stderr state lock was poisoned".to_string())?;
        let output = state.records.iter().cloned().collect::<String>();
        let stats = state.stats.clone();
        drop(state);
        if let Some(sink) = self.log_sink.as_ref() {
            let severity = if stats.read_failed || stats.invalid_structured_records > 0 {
                Severity::Warning
            } else {
                Severity::Info
            };
            let _ = sink.runtime_log.submit(
                RuntimeLogEvent::rust(
                    severity,
                    "core.stderr",
                    "core.stderr.summary",
                    "Core stderr drain completed",
                )
                .correlation(Correlation {
                    generation_id: Some(sink.context.generation_id.clone()),
                    generation_number: Some(sink.context.generation_number),
                    core_pid: Some(sink.context.core_pid),
                    ..Correlation::default()
                })
                .attributes(json!({
                    "bytes": stats.bytes_read,
                    "lines": stats.ordinary_records,
                    "count": stats.structured_records,
                    "failed": stats.invalid_structured_records,
                    "dropped_bytes": stats.dropped_bytes,
                    "dropped_records": stats.dropped_records,
                    "truncated_records": stats.truncated_records,
                    "eof": stats.eof,
                    "read_failed": stats.read_failed,
                })),
            );
        }
        Ok((output, stats))
    }
}

impl Drop for StderrDrainer {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
        if let Some(reader_handle) = self.reader.take() {
            // Managed readers freeze each native poll to STDERR_READ_SLICE.
            // Cancellation therefore bounds this insurance join without a
            // second lifecycle budget and preserves the unique thread owner.
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

fn stderr_diagnostic_summary(text: &str) -> String {
    text.split_whitespace()
        .map(|part| {
            let unquoted = part.trim_matches(['\'', '"', '(', ')', '[', ']', '{', '}', ',', ';']);
            let bytes = unquoted.as_bytes();
            let windows_path = bytes.windows(3).any(|window| {
                window[0].is_ascii_alphabetic()
                    && window[1] == b':'
                    && matches!(window[2], b'/' | b'\\')
            });
            if unquoted.contains("://") {
                "[URL]"
            } else if windows_path || unquoted.starts_with('/') || unquoted.starts_with("\\\\") {
                "[PATH]"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(320)
        .collect()
}

fn drain_stderr(
    mut reader: Box<dyn ManagedPipeReader>,
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    cancelled: &AtomicBool,
    log_sink: Option<&StderrLogSink>,
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
                        log_sink,
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
                    log_sink,
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
                    log_sink,
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
    log_sink: Option<&StderrLogSink>,
) {
    if !utf8_pending.is_empty() {
        drain_stderr_text(
            state,
            redactor,
            line_pending,
            dropping_long_line,
            &String::from_utf8_lossy(utf8_pending),
            log_sink,
        );
        utf8_pending.clear();
    }
    if !line_pending.is_empty() {
        push_stderr_text(state, redactor, line_pending, log_sink);
        line_pending.clear();
    }
}

struct ResponseFrameReader {
    decoder: FrameDecoder,
    header: [u8; 4],
    header_read: usize,
    payload_remaining: Option<usize>,
}

impl Default for ResponseFrameReader {
    fn default() -> Self {
        Self {
            decoder: FrameDecoder::default(),
            header: [0_u8; 4],
            header_read: 0,
            payload_remaining: None,
        }
    }
}

impl ResponseFrameReader {
    fn read_until(
        &mut self,
        reader: &mut dyn ManagedPipeReader,
        deadline: Instant,
        cancelled: &AtomicBool,
    ) -> Result<Option<Value>, String> {
        let mut chunk = [0_u8; 8192];
        loop {
            let reading_payload = self.payload_remaining.is_some();
            let outcome = if let Some(remaining) = self.payload_remaining {
                let read_limit = remaining.min(chunk.len());
                reader.read_until(&mut chunk[..read_limit], deadline, cancelled)
            } else {
                reader.read_until(&mut self.header[self.header_read..], deadline, cancelled)
            }
            .map_err(|error| error.to_string())?;

            match outcome {
                ManagedPipeReadOutcome::Read(0) => {
                    return Err("TRANSPORT_READ_FAILED: stdout returned an empty read".to_string())
                }
                ManagedPipeReadOutcome::Read(count) if reading_payload => {
                    let remaining = self
                        .payload_remaining
                        .expect("payload mode has a remaining byte count");
                    let frames = self
                        .decoder
                        .feed(&chunk[..count])
                        .map_err(|error| error.to_string())?;
                    let remaining = remaining - count;
                    self.payload_remaining = (remaining != 0).then_some(remaining);
                    if remaining == 0 {
                        if frames.len() != 1 {
                            return Err("TRANSPORT_READ_FAILED: response frame count was invalid"
                                .to_string());
                        }
                        return Ok(frames.into_iter().next());
                    }
                    if !frames.is_empty() {
                        return Err(
                            "TRANSPORT_READ_FAILED: response completed before its boundary"
                                .to_string(),
                        );
                    }
                }
                ManagedPipeReadOutcome::Read(count) => {
                    self.header_read += count;
                    if self.header_read == self.header.len() {
                        let payload_length = u32::from_be_bytes(self.header) as usize;
                        let frames = self
                            .decoder
                            .feed(&self.header)
                            .map_err(|error| error.to_string())?;
                        self.header_read = 0;
                        if !frames.is_empty() {
                            return Err(
                                "TRANSPORT_READ_FAILED: header completed a response".to_string()
                            );
                        }
                        self.payload_remaining = Some(payload_length);
                    }
                }
                ManagedPipeReadOutcome::Eof => {
                    if self.header_read != 0 {
                        self.decoder
                            .feed(&self.header[..self.header_read])
                            .map_err(|error| error.to_string())?;
                        self.header_read = 0;
                    }
                    self.decoder.finish().map_err(|error| error.to_string())?;
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

    fn has_incomplete_frame(&self) -> bool {
        self.header_read != 0 || self.payload_remaining.is_some() || self.decoder.finish().is_err()
    }
}

fn drain_stderr_text(
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    line_pending: &mut String,
    dropping_long_line: &mut bool,
    text: &str,
    log_sink: Option<&StderrLogSink>,
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
                state.stats.invalid_structured_records = state
                    .stats
                    .invalid_structured_records
                    .saturating_add(u64::from(line_pending.starts_with(CORE_BRIDGE_PREFIX)));
                state.stats.dropped_bytes = state
                    .stats
                    .dropped_bytes
                    .saturating_add(line_pending.len() as u64);
                state.stats.dropped_records = state.stats.dropped_records.saturating_add(1);
            }
            line_pending.clear();
            *dropping_long_line = !ends_line;
        } else if ends_line {
            push_stderr_text(state, redactor, line_pending, log_sink);
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

fn push_stderr_text(
    state: &Arc<Mutex<StderrDrainState>>,
    redactor: &StderrRedactor,
    text: &str,
    log_sink: Option<&StderrLogSink>,
) {
    let trimmed = text.trim_end_matches(['\r', '\n']);
    let mut rejected_structured_record = false;
    if let Some(payload) = trimmed.strip_prefix(CORE_BRIDGE_PREFIX) {
        if let Some(sink) = log_sink {
            if sink
                .runtime_log
                .submit_core_bridge_with_forbidden_secret(
                    payload,
                    &sink.context,
                    Some(&sink.generation_credential),
                )
                .is_ok()
            {
                if let Ok(mut state) = state.lock() {
                    state.stats.structured_records =
                        state.stats.structured_records.saturating_add(1);
                }
                return;
            }
        }
        if let Ok(mut state) = state.lock() {
            state.stats.invalid_structured_records =
                state.stats.invalid_structured_records.saturating_add(1);
        }
        rejected_structured_record = true;
    }
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
        state.stats.ordinary_records = state.stats.ordinary_records.saturating_add(1);
        if !state.ordinary_warning_emitted {
            state.ordinary_warning_emitted = true;
            if let Some(sink) = log_sink {
                let diagnostic = if rejected_structured_record {
                    "Core 日志桥收到无法验证的结构化诊断记录".to_string()
                } else {
                    stderr_diagnostic_summary(record.trim())
                };
                let _ = sink.runtime_log.submit(
                    RuntimeLogEvent::rust(
                        Severity::Warning,
                        "core.stderr",
                        "core.stderr.detected",
                        "Core wrote ordinary stderr output",
                    )
                    .correlation(Correlation {
                        generation_id: Some(sink.context.generation_id.clone()),
                        generation_number: Some(sink.context.generation_number),
                        core_pid: Some(sink.context.core_pid),
                        ..Correlation::default()
                    })
                    .attributes(json!({
                        "outcome": "detected",
                        "diagnostic": diagnostic,
                    })),
                );
            }
        }
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

struct RequestExpectation {
    id: String,
    name: String,
    protocol_minor: u64,
    is_hello: bool,
}

pub struct CoreHostRuntime {
    tree: Option<Box<dyn ManagedProcessTree>>,
    stdin: Option<File>,
    stdout: Option<Box<dyn ManagedPipeReader>>,
    router: Option<CoreHostRouter>,
    stdout_frames: ResponseFrameReader,
    stderr_drain: Option<StderrDrainer>,
    generation_id: String,
    generation_number: u64,
    core_pid: u32,
    runtime_log: Option<RuntimeLogService>,
    generation_credential: String,
    handshake: HandshakeState,
    negotiation: Option<ProtocolNegotiation>,
    deadline_forced: bool,
    router_failure_observed: bool,
    router_eof_expected: bool,
    snapshot_cache: CoreSnapshotCache,
    #[cfg(test)]
    cleanup_events: Option<Arc<Mutex<Vec<&'static str>>>>,
    #[cfg(test)]
    shutdown_written_at: Option<Arc<Mutex<Option<Instant>>>>,
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

#[derive(Clone)]
pub struct ConcurrentRequestHandle {
    router: CoreHostRouterHandle,
    generation_id: String,
    generation_credential: String,
    protocol_minor: u64,
    generation_number: u64,
    core_pid: u32,
    runtime_log: Option<RuntimeLogService>,
}

impl ConcurrentRequestHandle {
    pub fn request(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<Value, String> {
        self.request_with_scheduling(request_id, name, payload, deadline, "interactive")
    }

    pub(crate) fn request_with_scheduling(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
        scheduling: &'static str,
    ) -> Result<Value, String> {
        if request_id.trim().is_empty()
            || name.trim().is_empty()
            || deadline.is_zero()
            || !payload.is_object()
            || !matches!(scheduling, "control" | "interactive")
        {
            return Err("Core Host concurrent request is invalid".to_string());
        }
        let started = Instant::now();
        self.log_request(
            Severity::Debug,
            "ipc.request.started",
            "Core IPC request started",
            request_id,
            name,
            "started",
            None,
            0,
            None,
            Some(deadline.as_millis()),
        );
        let result = self.router.request(
            json!({
                "protocolMajor": PROTOCOL_MAJOR,
                "protocolMinor": self.protocol_minor,
                "kind": "request",
                "generationId": self.generation_id,
                "generationCredential": self.generation_credential,
                "id": request_id,
                "name": name,
                "payload": payload,
                "deadlineMs": deadline.as_millis().min(u64::MAX as u128) as u64,
                "priority": scheduling,
            }),
            deadline,
        );
        let elapsed_ms = started.elapsed().as_millis();
        match result.as_ref() {
            Ok(_) => self.log_request(
                Severity::Info,
                "ipc.request.completed",
                "Core IPC request completed",
                request_id,
                name,
                "completed",
                None,
                elapsed_ms,
                None,
                Some(deadline.as_millis()),
            ),
            Err(error) => self.log_request(
                if error.contains("CANCEL") {
                    Severity::Info
                } else {
                    Severity::Warning
                },
                if error.contains("CANCEL") {
                    "ipc.request.cancelled"
                } else {
                    "ipc.request.failed"
                },
                if error.contains("CANCEL") {
                    "Core IPC request was cancelled"
                } else {
                    "Core IPC request failed"
                },
                request_id,
                name,
                if error.contains("CANCEL") {
                    "cancelled"
                } else {
                    "failed"
                },
                Some(stable_error_code(error)),
                elapsed_ms,
                Some(stable_error_diagnostic(error)),
                Some(deadline.as_millis()),
            ),
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    fn log_request(
        &self,
        severity: Severity,
        event: &'static str,
        message: &'static str,
        request_id: &str,
        name: &str,
        outcome: &'static str,
        code: Option<&'static str>,
        elapsed_ms: u128,
        diagnostic: Option<&'static str>,
        deadline_ms: Option<u128>,
    ) {
        let Some(runtime_log) = self.runtime_log.as_ref() else {
            return;
        };
        let mut attributes = json!({
            "command": name,
            "outcome": outcome,
            "elapsed_ms": elapsed_ms,
        });
        if let (Some(target), Some(code)) = (attributes.as_object_mut(), code) {
            target.insert("code".to_string(), Value::String(code.to_string()));
        }
        if let (Some(target), Some(diagnostic)) = (attributes.as_object_mut(), diagnostic) {
            target.insert(
                "diagnostic".to_string(),
                Value::String(diagnostic.to_string()),
            );
        }
        if let (Some(target), Some(deadline_ms)) = (attributes.as_object_mut(), deadline_ms) {
            target.insert("deadline_ms".to_string(), Value::from(deadline_ms as u64));
        }
        let _ = runtime_log.submit(
            RuntimeLogEvent::rust(severity, "core.ipc", event, message)
                .correlation(Correlation {
                    generation_id: Some(self.generation_id.clone()),
                    generation_number: Some(self.generation_number),
                    core_pid: Some(self.core_pid),
                    request_id: Some(request_id.to_string()),
                    operation_id: (name == "chat.send").then(|| request_id.to_string()),
                    ..Correlation::default()
                })
                .attributes(attributes),
        );
    }
}

fn stable_error_code(error: &str) -> &'static str {
    if error.contains("CANCEL") {
        "REQUEST_CANCELLED"
    } else if error.contains("DEADLINE") || error.contains("TIMEOUT") {
        "REQUEST_DEADLINE_EXCEEDED"
    } else if error.contains("GENERATION") {
        "GENERATION_INVALIDATED"
    } else if error.contains("TRANSPORT") || error.contains("ROUTER") {
        "TRANSPORT_UNAVAILABLE"
    } else {
        "REQUEST_FAILED"
    }
}

fn stable_error_diagnostic(error: &str) -> &'static str {
    if error.contains("DEADLINE") || error.contains("TIMEOUT") {
        "等待 Core Host 响应超过请求期限；底层任务可能仍在结束"
    } else if error.contains("GENERATION") {
        "请求所属 Core generation 已失效，通常发生在设置保存或 Core 重启期间"
    } else if error.contains("TRANSPORT") || error.contains("ROUTER") {
        "Core Host 传输已关闭或不可用，请检查相邻的 Core 重启和异常记录"
    } else if error.contains("CANCEL") {
        "请求已由用户或上层生命周期取消"
    } else {
        "Core Host 请求失败；请结合相同 op/request 的相邻日志定位阶段"
    }
}

impl std::fmt::Debug for CoreHostRuntime {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CoreHostRuntime")
            .field("root_pid", &self.tree.as_ref().map(|tree| tree.root_pid()))
            .field("generation_id", &self.generation_id)
            .field("handshake", &self.handshake)
            .field("negotiation", &self.negotiation)
            .field("deadline_forced", &self.deadline_forced)
            .field("snapshot_cache", &self.snapshot_cache)
            .finish_non_exhaustive()
    }
}

impl CoreHostRuntime {
    pub fn root_pid(&self) -> u32 {
        self.core_pid
    }

    pub fn launch(
        layout: &RuntimeLayout,
        generation_id: &str,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        Self::launch_internal(layout, generation_id, 1, None)
    }

    pub fn launch_observed(
        layout: &RuntimeLayout,
        generation_id: &str,
        generation_number: u64,
        runtime_log: RuntimeLogService,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        Self::launch_internal(layout, generation_id, generation_number, Some(runtime_log))
    }

    fn launch_internal(
        layout: &RuntimeLayout,
        generation_id: &str,
        generation_number: u64,
        runtime_log: Option<RuntimeLogService>,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        validate_runtime_layout(layout).map_err(CoreHostLifecycleFailure::without_recovery)?;
        let request = core_host_process_request(layout, generation_id, generation_number)
            .map_err(CoreHostLifecycleFailure::without_recovery)?;
        Self::launch_with_backend_mode(
            &NativeManagedProcessTreeBackend,
            request,
            generation_id,
            true,
            generation_number,
            runtime_log,
        )
    }

    #[cfg(debug_assertions)]
    pub(crate) fn launch_acceptance_fault(
        layout: &RuntimeLayout,
        generation_id: &str,
        script: &Path,
        fault_mode: &str,
        fault_directory: &Path,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        validate_runtime_layout(layout).map_err(CoreHostLifecycleFailure::without_recovery)?;
        let script = fs::canonicalize(script).map_err(|error| {
            CoreHostLifecycleFailure::without_recovery(format!(
                "Phase 1C fault harness script could not be resolved: {error}"
            ))
        })?;
        if !script.starts_with(&layout.resource_root) || !fault_directory.is_absolute() {
            return Err(CoreHostLifecycleFailure::without_recovery(
                "Phase 1C fault harness paths escaped their approved roots",
            ));
        }
        let request = ManagedProcessRequest {
            program: layout.python_executable.clone(),
            args: vec![
                "-I".into(),
                "-B".into(),
                "-X".into(),
                "utf8".into(),
                script.into_os_string(),
                "--repo-root".into(),
                layout.resource_root.as_os_str().to_owned(),
                "--app-root".into(),
                layout.assistant_root.as_os_str().to_owned(),
                "--generation-id".into(),
                generation_id.into(),
                "--fault-mode".into(),
                fault_mode.into(),
                "--fault-directory".into(),
                fault_directory.as_os_str().to_owned(),
                "--python-path-entry".into(),
                layout.python_path_entries[0].as_os_str().to_owned(),
            ],
            current_directory: Some(layout.resource_root.clone()),
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        };
        Self::launch_with_router_backend(&NativeManagedProcessTreeBackend, request, generation_id)
    }

    fn launch_with_backend(
        backend: &dyn ManagedProcessTreeBackend,
        request: ManagedProcessRequest,
        generation_id: &str,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        Self::launch_with_backend_mode(backend, request, generation_id, false, 1, None)
    }

    fn launch_with_router_backend(
        backend: &dyn ManagedProcessTreeBackend,
        request: ManagedProcessRequest,
        generation_id: &str,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        Self::launch_with_backend_mode(backend, request, generation_id, true, 1, None)
    }

    fn launch_with_backend_mode(
        backend: &dyn ManagedProcessTreeBackend,
        request: ManagedProcessRequest,
        generation_id: &str,
        enable_router: bool,
        generation_number: u64,
        runtime_log: Option<RuntimeLogService>,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        if generation_id.trim().is_empty() {
            return Err(CoreHostLifecycleFailure::without_recovery(
                "Core Host generation ID must not be empty",
            ));
        }
        let (credential_bytes, generation_credential) =
            create_generation_credential().map_err(CoreHostLifecycleFailure::without_recovery)?;
        let snapshot_cache = CoreSnapshotCache::new(generation_id)
            .map_err(CoreHostLifecycleFailure::without_recovery)?;
        let spawned = backend.spawn(&request).map_err(|error| {
            CoreHostLifecycleFailure::without_recovery(format!(
                "Core Host managed spawn failed: {error}"
            ))
        })?;
        let core_pid = spawned.tree.root_pid();
        let Some(pipes) = spawned.pipes else {
            let runtime = Self {
                tree: Some(spawned.tree),
                stdin: None,
                stdout: None,
                router: None,
                stdout_frames: ResponseFrameReader::default(),
                stderr_drain: None,
                generation_id: generation_id.to_string(),
                generation_number,
                core_pid,
                runtime_log,
                generation_credential,
                handshake: HandshakeState::Pending,
                negotiation: None,
                deadline_forced: false,
                router_failure_observed: false,
                router_eof_expected: false,
                snapshot_cache,
                #[cfg(test)]
                cleanup_events: None,
                #[cfg(test)]
                shutdown_written_at: None,
            };
            return Err(
                runtime.fail_after_spawn("Core Host managed spawn returned no pipes".to_string())
            );
        };
        let stderr_drain = StderrDrainer::start(
            pipes.stderr,
            generation_id,
            core_pid,
            &generation_credential,
            runtime_log.as_ref().map(|runtime_log| StderrLogSink {
                runtime_log: runtime_log.clone(),
                context: CoreLogContext {
                    generation_id: generation_id.to_string(),
                    generation_number,
                    core_pid,
                },
                generation_credential: generation_credential.clone(),
            }),
        );
        let mut runtime = Self {
            tree: Some(spawned.tree),
            stdin: Some(pipes.stdin),
            stdout: Some(pipes.stdout),
            router: None,
            stdout_frames: ResponseFrameReader::default(),
            stderr_drain: Some(stderr_drain),
            generation_id: generation_id.to_string(),
            generation_number,
            core_pid,
            runtime_log,
            generation_credential,
            handshake: HandshakeState::Pending,
            negotiation: None,
            deadline_forced: false,
            router_failure_observed: false,
            router_eof_expected: false,
            snapshot_cache,
            #[cfg(test)]
            cleanup_events: None,
            #[cfg(test)]
            shutdown_written_at: None,
        };
        let credential_written = runtime.stdin.as_mut().is_some_and(|stdin| {
            stdin
                .write_all(&credential_bytes)
                .and_then(|_| stdin.flush())
                .is_ok()
        });
        if !credential_written {
            return Err(runtime.fail_after_spawn(
                "TRANSPORT_WRITE_FAILED: Core Host credential bootstrap failed".to_string(),
            ));
        }
        if enable_router {
            let Some(stdin) = runtime.stdin.take() else {
                return Err(
                    runtime.fail_after_spawn("Core Host stdin owner was unavailable".to_string())
                );
            };
            let Some(stdout) = runtime.stdout.take() else {
                return Err(
                    runtime.fail_after_spawn("Core Host stdout owner was unavailable".to_string())
                );
            };
            let router =
                CoreHostRouter::new(stdin, stdout, generation_id, &runtime.generation_credential);
            runtime.router = match router {
                Ok(router) => Some(router),
                Err(error) => return Err(runtime.fail_after_spawn(error)),
            };
        }
        Ok(runtime)
    }

    #[cfg(test)]
    fn launch_script_for_test(
        python: &Path,
        repo_root: &Path,
        script: &Path,
        generation_id: &str,
    ) -> Result<Self, CoreHostLifecycleFailure> {
        Self::launch_with_router_backend(
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

    #[cfg(test)]
    fn from_test_owners(
        tree: Box<dyn ManagedProcessTree>,
        stdin: File,
        stdout: Box<dyn ManagedPipeReader>,
        stderr_drain: StderrDrainer,
        generation_id: &str,
        generation_credential: &str,
        cleanup_events: Arc<Mutex<Vec<&'static str>>>,
        shutdown_written_at: Arc<Mutex<Option<Instant>>>,
    ) -> Self {
        Self {
            tree: Some(tree),
            stdin: Some(stdin),
            stdout: Some(stdout),
            router: None,
            stdout_frames: ResponseFrameReader::default(),
            stderr_drain: Some(stderr_drain),
            generation_id: generation_id.to_string(),
            generation_number: 1,
            core_pid: 42,
            runtime_log: None,
            generation_credential: generation_credential.to_string(),
            handshake: HandshakeState::Complete,
            negotiation: Some(ProtocolNegotiation {
                major: PROTOCOL_MAJOR,
                minor: 1,
                capabilities: REQUIRED_CAPABILITIES
                    .iter()
                    .map(|capability| (*capability).to_string())
                    .collect(),
            }),
            deadline_forced: false,
            router_failure_observed: false,
            router_eof_expected: false,
            snapshot_cache: CoreSnapshotCache::new(generation_id)
                .expect("test generation ID is valid"),
            cleanup_events: Some(cleanup_events),
            shutdown_written_at: Some(shutdown_written_at),
        }
    }

    #[cfg(test)]
    fn observe_shutdown_write_for_test(&mut self, observed: Arc<Mutex<Option<Instant>>>) {
        self.shutdown_written_at = Some(observed);
    }

    pub fn pid(&self) -> u32 {
        self.tree.as_ref().map_or(0, |tree| tree.root_pid())
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
        if self.router.is_some() {
            let (request, expectation, _) =
                self.build_request_frame(request_id, name, payload, deadline)?;
            let response = self
                .router
                .as_ref()
                .expect("router is present")
                .handle()
                .request(request, deadline)
                .map_err(|error| {
                    self.router_failure_observed = true;
                    if error.starts_with("GENERATION_CREDENTIAL_MISMATCH:") {
                        if let Some(tree) = self.tree.as_mut() {
                            let _ = tree.terminate_tree(DEADLINE_EXIT_CODE);
                        }
                        self.deadline_forced = true;
                    }
                    if error.starts_with("STDOUT_EOF:") || error.starts_with("CORE_CRASHED:") {
                        if let Some(tree) = self.tree.as_mut() {
                            if matches!(
                                tree.wait_root(Duration::from_millis(50)),
                                Ok(ProcessWaitOutcome::Exited(_))
                            ) {
                                return "CORE_CRASHED: Core Host exited before its response"
                                    .to_string();
                            }
                        }
                    }
                    error
                })?;
            return self.validate_response(response, expectation);
        }
        let (expectation, written_at) =
            self.write_request_frame(request_id, name, payload, deadline)?;
        let response_deadline = written_at
            .checked_add(deadline)
            .ok_or_else(|| "Core Host control request deadline overflowed".to_string())?;
        let response = self.read_response_until(response_deadline)?;
        self.validate_response(response, expectation)
    }

    #[cfg(debug_assertions)]
    pub(crate) fn request_with_acceptance_identity(
        &mut self,
        request_id: &str,
        name: &str,
        supplied_generation_id: &str,
        supplied_generation_credential: Option<&str>,
        deadline: Duration,
    ) -> Result<Value, String> {
        let supplied_generation_credential = supplied_generation_credential
            .unwrap_or(self.generation_credential.as_str())
            .to_string();
        let protocol_minor = self
            .negotiation
            .as_ref()
            .map_or(PROTOCOL_MINOR, |negotiation| negotiation.minor);
        let request = json!({
            "protocolMajor": PROTOCOL_MAJOR,
            "protocolMinor": protocol_minor,
            "kind": "request",
            "generationId": supplied_generation_id,
            "generationCredential": supplied_generation_credential,
            "id": request_id,
            "name": name,
            "payload": {},
            "deadlineMs": deadline.as_millis().min(u64::MAX as u128) as u64,
            "priority": CONTROL_PRIORITY,
        });
        if let Some(router) = self.router.as_ref() {
            let response = router.handle().request(request, deadline)?;
            return self.validate_response(
                response,
                RequestExpectation {
                    id: request_id.to_string(),
                    name: name.to_string(),
                    protocol_minor,
                    is_hello: false,
                },
            );
        }
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "TRANSPORT_WRITE_FAILED: Core Host stdin is closed".to_string())?;
        write_frame(stdin, &request).map_err(|error| error.to_string())?;
        stdin
            .flush()
            .map_err(|_| "TRANSPORT_WRITE_FAILED: Core Host stdin flush failed".to_string())?;
        let response = self.read_response_until(Instant::now() + deadline)?;
        self.validate_response(
            response,
            RequestExpectation {
                id: request_id.to_string(),
                name: name.to_string(),
                protocol_minor,
                is_hello: false,
            },
        )
    }

    fn write_request_frame(
        &mut self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<(RequestExpectation, Instant), String> {
        let (request, expectation, written_at) =
            self.build_request_frame(request_id, name, payload, deadline)?;
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "TRANSPORT_WRITE_FAILED: Core Host stdin is closed".to_string())?;
        write_frame(stdin, &request).map_err(|error| error.to_string())?;
        stdin
            .flush()
            .map_err(|_| "TRANSPORT_WRITE_FAILED: Core Host stdin flush failed".to_string())?;
        #[cfg(test)]
        if name == "system.shutdown" {
            if let Some(events) = &self.cleanup_events {
                events
                    .lock()
                    .expect("cleanup events")
                    .push("shutdown_written");
            }
            if let Some(observed) = &self.shutdown_written_at {
                *observed.lock().expect("shutdown write instant") = Some(written_at);
            }
        }
        Ok((expectation, written_at))
    }

    fn build_request_frame(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<(Value, RequestExpectation, Instant), String> {
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
        let written_at = Instant::now();
        Ok((
            request,
            RequestExpectation {
                id: request_id.to_string(),
                name: name.to_string(),
                protocol_minor,
                is_hello,
            },
            written_at,
        ))
    }

    fn validate_response(
        &mut self,
        response: Value,
        expectation: RequestExpectation,
    ) -> Result<Value, String> {
        if response.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str())
            || response.get("generationCredential").and_then(Value::as_str)
                != Some(self.generation_credential.as_str())
            || response.get("id").and_then(Value::as_str) != Some(expectation.id.as_str())
            || response.get("name").and_then(Value::as_str) != Some(expectation.name.as_str())
        {
            if let Some(tree) = self.tree.as_mut() {
                let _ = tree.terminate_tree(DEADLINE_EXIT_CODE);
            }
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
        if expectation.is_hello {
            if response.get("ok").and_then(Value::as_bool) == Some(true) {
                match parse_negotiation(&response) {
                    Ok(negotiation) => {
                        if let Some(router) = self.router.as_ref() {
                            router.enable_events(
                                negotiation.minor >= 2
                                    && negotiation.capabilities.iter().any(|capability| {
                                        capability == "transport.concurrent-router"
                                    }),
                            );
                        }
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
        } else if response.get("protocolMinor").and_then(Value::as_u64)
            != Some(expectation.protocol_minor)
        {
            return Err("INVALID_NEGOTIATION: response minor changed after handshake".to_string());
        }
        Ok(response)
    }

    pub fn negotiation(&self) -> Option<&ProtocolNegotiation> {
        self.negotiation.as_ref()
    }

    pub fn concurrent_request_handle(&self) -> Result<ConcurrentRequestHandle, String> {
        let negotiation = self
            .negotiation
            .as_ref()
            .filter(|negotiation| {
                negotiation.minor >= 2
                    && negotiation
                        .capabilities
                        .iter()
                        .any(|capability| capability == "transport.concurrent-router")
            })
            .ok_or_else(|| {
                "CAPABILITY_NEGOTIATION_FAILED: concurrent router was not negotiated".to_string()
            })?;
        let router = self
            .router
            .as_ref()
            .ok_or_else(|| "ROUTER_UNAVAILABLE: concurrent router is unavailable".to_string())?;
        Ok(ConcurrentRequestHandle {
            router: router.handle(),
            generation_id: self.generation_id.clone(),
            generation_credential: self.generation_credential.clone(),
            protocol_minor: negotiation.minor,
            generation_number: self.generation_number,
            core_pid: self.core_pid,
            runtime_log: self.runtime_log.clone(),
        })
    }

    pub fn chat_gateway(&self) -> Result<CoreHostGateway, String> {
        let handle = self.concurrent_request_handle()?;
        CoreHostGateway::new(self.generation_id.clone(), Arc::new(handle))
    }

    pub fn recv_event_timeout(&self, timeout: Duration) -> Result<Option<Value>, String> {
        let router = self
            .router
            .as_ref()
            .ok_or_else(|| "ROUTER_UNAVAILABLE: concurrent router is unavailable".to_string())?;
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            let Some(event) = router.recv_event_timeout(remaining)? else {
                return Ok(None);
            };
            return Ok(Some(event));
        }
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
        if self
            .negotiation
            .as_ref()
            .is_some_and(|negotiation| negotiation.minor >= 2)
        {
            self.snapshot_cache
                .store_minimal_python_snapshot(&snapshot)?;
        } else {
            self.snapshot_cache.store_python_snapshot(&snapshot)?;
        }
        Ok(snapshot)
    }

    pub fn cached_snapshot(&self) -> Option<&Value> {
        self.snapshot_cache.current()
    }

    #[cfg(test)]
    pub(crate) fn terminate_tree_for_test(&mut self) -> Result<(), String> {
        self.tree
            .as_mut()
            .ok_or_else(|| "Core Host process tree is unavailable".to_string())?
            .terminate_tree(95)
            .map_err(|error| error.to_string())
    }

    pub fn shutdown(self) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        self.shutdown_using_policy(PRODUCTION_SHUTDOWN_POLICY)
    }

    #[cfg(debug_assertions)]
    pub(crate) fn shutdown_with_acceptance_policy(
        self,
        graceful: Duration,
        total: Duration,
    ) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        self.shutdown_using_policy(ShutdownPolicy { graceful, total })
    }

    #[cfg(test)]
    fn shutdown_with_policy(
        self,
        policy: ShutdownPolicy,
    ) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        self.shutdown_using_policy(policy)
    }

    fn shutdown_using_policy(
        mut self,
        policy: ShutdownPolicy,
    ) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        if self.router.is_some() {
            let started = Instant::now();
            let request =
                self.build_request_frame("shutdown", "system.shutdown", json!({}), policy.graceful);
            let (primary, graceful_deadline, absolute_deadline) = match request {
                Ok((message, expectation, written_at)) => {
                    let absolute_deadline =
                        written_at.checked_add(policy.total).unwrap_or(written_at);
                    let graceful_deadline = written_at
                        .checked_add(policy.graceful)
                        .unwrap_or(absolute_deadline)
                        .min(absolute_deadline);
                    let response = self
                        .router
                        .as_ref()
                        .expect("router is present")
                        .handle()
                        .request(message, policy.graceful);
                    #[cfg(test)]
                    if let Some(observed) = &self.shutdown_written_at {
                        *observed.lock().expect("shutdown write instant") = Some(written_at);
                    }
                    let primary = match response
                        .and_then(|response| self.validate_response(response, expectation))
                    {
                        Ok(response)
                            if response.get("ok").and_then(Value::as_bool) == Some(true) =>
                        {
                            None
                        }
                        Ok(_) => Some("Core Host rejected system.shutdown".to_string()),
                        Err(error) => Some(error),
                    };
                    if primary.is_none() {
                        self.router_eof_expected = true;
                    }
                    (primary, graceful_deadline, absolute_deadline)
                }
                Err(error) => {
                    let absolute_deadline = started.checked_add(policy.total).unwrap_or(started);
                    let graceful_deadline = started
                        .checked_add(policy.graceful)
                        .unwrap_or(absolute_deadline)
                        .min(absolute_deadline);
                    (Some(error), graceful_deadline, absolute_deadline)
                }
            };
            return self.finish_exit_until(absolute_deadline, graceful_deadline, primary);
        }
        let write_result =
            self.write_request_frame("shutdown", "system.shutdown", json!({}), policy.graceful);
        let (primary, graceful_deadline, absolute_deadline) = match write_result {
            Ok((expectation, written_at)) => {
                let Some(absolute_deadline) = written_at.checked_add(policy.total) else {
                    return self.finish_exit_until(
                        written_at,
                        written_at,
                        Some("Core Host shutdown total deadline overflowed".to_string()),
                    );
                };
                let graceful_deadline = written_at
                    .checked_add(policy.graceful)
                    .unwrap_or(absolute_deadline)
                    .min(absolute_deadline);
                let response_result = self
                    .read_response_until(graceful_deadline)
                    .and_then(|response| self.validate_response(response, expectation));
                let primary = match response_result {
                    Ok(response) if response.get("ok").and_then(Value::as_bool) == Some(true) => {
                        None
                    }
                    Ok(_) => Some("Core Host rejected system.shutdown".to_string()),
                    Err(error) => Some(error),
                };
                (primary, graceful_deadline, absolute_deadline)
            }
            Err(error) => {
                let started = Instant::now();
                let absolute_deadline = started.checked_add(policy.total).unwrap_or(started);
                let graceful_deadline = started
                    .checked_add(policy.graceful)
                    .unwrap_or(absolute_deadline)
                    .min(absolute_deadline);
                (Some(error), graceful_deadline, absolute_deadline)
            }
        };
        self.finish_exit_until(absolute_deadline, graceful_deadline, primary)
    }

    pub fn close_stdin_and_wait(self) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        let started = Instant::now();
        let absolute_deadline = started
            .checked_add(PRODUCTION_SHUTDOWN_POLICY.total)
            .unwrap_or(started);
        let graceful_deadline = started
            .checked_add(PRODUCTION_SHUTDOWN_POLICY.graceful)
            .unwrap_or(absolute_deadline)
            .min(absolute_deadline);
        self.finish_exit_until(absolute_deadline, graceful_deadline, None)
    }

    fn read_response_until(&mut self, deadline: Instant) -> Result<Value, String> {
        let stdout = self
            .stdout
            .as_mut()
            .ok_or_else(|| "TRANSPORT_READ_FAILED: Core Host stdout is unavailable".to_string())?;
        let response =
            match self
                .stdout_frames
                .read_until(stdout.as_mut(), deadline, &AtomicBool::new(false))
            {
                Ok(response) => response,
                Err(error) if error.starts_with("REQUEST_DEADLINE_EXCEEDED:") => {
                    let tree = self.tree.as_mut().ok_or_else(|| {
                        "Core Host response timeout cleanup lost the tree owner".to_string()
                    })?;
                    tree.terminate_tree(DEADLINE_EXIT_CODE).map_err(|error| {
                        format!("Core Host response timeout cleanup failed: {error}")
                    })?;
                    self.deadline_forced = true;
                    return Err(error);
                }
                Err(error) => return Err(error),
            };
        response.ok_or_else(|| {
            let root_exit = self
                .tree
                .as_mut()
                .map(|tree| tree.wait_root(Duration::from_millis(50)));
            match root_exit {
                Some(Ok(ProcessWaitOutcome::Exited(_))) => {
                    "CORE_CRASHED: Core Host exited before its response".to_string()
                }
                _ => "STDOUT_EOF: Core Host stdout reached EOF before its response".to_string(),
            }
        })
    }

    fn fail_after_spawn(self, diagnostic: String) -> CoreHostLifecycleFailure {
        let started = Instant::now();
        let absolute_deadline = started
            .checked_add(PRODUCTION_SHUTDOWN_POLICY.total)
            .unwrap_or(started);
        let graceful_deadline = started
            .checked_add(PRODUCTION_SHUTDOWN_POLICY.graceful)
            .unwrap_or(absolute_deadline)
            .min(absolute_deadline);
        match self.finish_exit_until(
            absolute_deadline,
            graceful_deadline,
            Some(diagnostic.clone()),
        ) {
            Err(failure) => failure,
            Ok(_) => CoreHostLifecycleFailure::without_recovery(diagnostic),
        }
    }

    fn finish_exit_until(
        mut self,
        absolute_deadline: Instant,
        graceful_deadline: Instant,
        mut primary: Option<String>,
    ) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
        self.stdin.take();
        let mut router_result = self.router.as_mut().map(|router| router.close());
        #[cfg(test)]
        if let Some(events) = &self.cleanup_events {
            events.lock().expect("cleanup events").push("stdin_closed");
        }

        if let Some(tree) = self.tree.as_mut() {
            let root_wait_deadline = graceful_deadline.min(absolute_deadline);
            let root_wait =
                tree.wait_root(root_wait_deadline.saturating_duration_since(Instant::now()));
            if let Err(error) = root_wait {
                let note = format!("Core Host root observation failed: {error}");
                match &mut primary {
                    Some(primary) => {
                        primary.push_str("; cleanup note: ");
                        primary.push_str(&note);
                    }
                    None => primary = Some(note),
                }
            }
        }

        let tree_result = self
            .tree
            .take()
            .map(|tree| tree.finalize_until(absolute_deadline, DEADLINE_EXIT_CODE));
        if router_result.as_ref().is_some_and(Result::is_err) {
            if let Some(router) = self.router.as_mut() {
                let retry = router.close();
                if retry.is_ok() {
                    router_result = Some(Ok(()));
                }
            }
        }
        if self.router_failure_observed && router_result.as_ref().is_some_and(Result::is_err) {
            router_result = Some(Ok(()));
        }
        if self.router_eof_expected
            && router_result.as_ref().is_some_and(|result| {
                result
                    .as_ref()
                    .is_err_and(|error| error.starts_with("STDOUT_EOF:"))
            })
        {
            router_result = Some(Ok(()));
        }

        let stdout_result = self
            .stdout
            .as_deref_mut()
            .map(|stdout| drain_trailing_stdout_until(stdout, absolute_deadline));

        let stderr_result = self
            .stderr_drain
            .as_mut()
            .map(|stderr| stderr.finish_until(absolute_deadline));
        #[cfg(test)]
        if let Some(events) = &self.cleanup_events {
            events
                .lock()
                .expect("cleanup events")
                .push("stderr_finished");
        }

        self.stdout.take();
        self.stderr_drain.take();
        #[cfg(test)]
        if let Some(events) = &self.cleanup_events {
            events
                .lock()
                .expect("cleanup events")
                .push("readers_dropped");
        }

        let stdout_incomplete = self.stdout_frames.has_incomplete_frame();
        aggregate_exit_or_retain_recovery(
            primary,
            self.deadline_forced,
            tree_result,
            stdout_result,
            router_result,
            stdout_incomplete,
            stderr_result,
        )
    }
}

fn drain_trailing_stdout_until(
    stdout: &mut dyn ManagedPipeReader,
    absolute_deadline: Instant,
) -> Result<bool, String> {
    let cancelled = AtomicBool::new(false);
    let mut chunk = [0_u8; 8192];
    let mut saw_trailing_bytes = false;
    loop {
        match stdout
            .read_until(&mut chunk, absolute_deadline, &cancelled)
            .map_err(|error| format!("Core Host stdout drain failed: {error}"))?
        {
            ManagedPipeReadOutcome::Read(0) => {
                return Err("Core Host stdout drain returned an empty read".to_string())
            }
            ManagedPipeReadOutcome::Read(_) => saw_trailing_bytes = true,
            ManagedPipeReadOutcome::Eof => return Ok(saw_trailing_bytes),
            ManagedPipeReadOutcome::Cancelled => {
                return Err("Core Host stdout drain was cancelled".to_string())
            }
            ManagedPipeReadOutcome::TimedOut => {
                return Err("Core Host stdout drain exceeded its deadline".to_string())
            }
        }
    }
}

fn aggregate_exit_or_retain_recovery(
    primary: Option<String>,
    deadline_forced: bool,
    tree_result: Option<ProcessTreeFinalizationResult>,
    stdout_result: Option<Result<bool, String>>,
    router_result: Option<Result<(), String>>,
    stdout_incomplete: bool,
    stderr_result: Option<Result<(String, StderrDrainStats), String>>,
) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
    let mut diagnostics = Vec::new();
    if let Some(primary) = primary {
        diagnostics.push(primary);
    }

    let mut recovery = None;
    let mut finalization = None;
    match tree_result {
        Some(Ok(result)) => finalization = Some(result),
        Some(Err(failure)) => {
            let (error, tree) = failure.into_parts();
            diagnostics.push(format!("Core Host process tree cleanup failed: {error}"));
            recovery = Some(CoreHostRecovery { tree });
        }
        None => diagnostics.push("Core Host process tree owner was unavailable".to_string()),
    }

    let router_owned_stdout = router_result.is_some();
    if let Some(router_result) = router_result.as_ref() {
        if let Err(error) = router_result {
            if !(deadline_forced && error.starts_with("GENERATION_CREDENTIAL_MISMATCH:")) {
                diagnostics.push(error.clone());
            }
        }
    }
    match stdout_result {
        Some(Ok(saw_trailing_bytes)) => {
            if stdout_incomplete || saw_trailing_bytes {
                diagnostics.push(
                    "STDOUT_FRAMING_POLLUTION: Core Host wrote trailing stdout bytes".to_string(),
                );
            }
        }
        Some(Err(error)) => diagnostics.push(error),
        None if !router_owned_stdout => {
            diagnostics.push("TRANSPORT_READ_FAILED: Core Host stdout is unavailable".to_string())
        }
        None => {}
    }

    let mut stderr = None;
    match stderr_result {
        Some(Ok((output, stats))) => {
            if stats.read_failed {
                diagnostics.push("STDERR_READ_FAILED: stderr reader reported failure".to_string());
            }
            stderr = Some((output, stats));
        }
        Some(Err(error)) => diagnostics.push(error),
        None => diagnostics.push("STDERR_READ_FAILED: stderr reader is unavailable".to_string()),
    }

    if !diagnostics.is_empty() {
        return Err(CoreHostLifecycleFailure {
            diagnostic: diagnostics.join("; cleanup note: "),
            recovery,
        });
    }

    let Some(finalization) = finalization else {
        return Err(CoreHostLifecycleFailure {
            diagnostic: "Core Host process tree finalization result was unavailable".to_string(),
            recovery,
        });
    };
    let Some((stderr, stderr_stats)) = stderr else {
        return Err(CoreHostLifecycleFailure {
            diagnostic: "STDERR_READ_FAILED: stderr completion result was unavailable".to_string(),
            recovery,
        });
    };
    Ok(CoreHostExit {
        root_exit_code: process_exit_code(finalization.root_status),
        tree_empty: true,
        forced: deadline_forced || finalization.forced,
        stderr,
        stderr_stats,
    })
}

fn core_host_process_request(
    layout: &RuntimeLayout,
    generation_id: &str,
    generation_number: u64,
) -> Result<ManagedProcessRequest, String> {
    let resource_root_text = layout.resource_root.to_string_lossy().replace('\\', "/");
    let resource_root = serde_json::to_string(&resource_root_text)
        .map_err(|error| format!("Core Host resource root encoding failed: {error}"))?;
    let core_main = serde_json::to_string(&format!("{}.__main__", layout.core_module))
        .map_err(|error| format!("Core Host module encoding failed: {error}"))?;
    let python_path_entries = layout
        .python_path_entries
        .iter()
        .map(|path| serde_json::to_string(&path.to_string_lossy().replace('\\', "/")))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("Core Host Python path encoding failed: {error}"))?
        .join(",");
    // Official Windows embeddable Python runs with `isolated=1` and its
    // `_pth` file intentionally ignores PYTHONPATH/current-directory
    // discovery. Insert the RuntimeLocator-approved resource root
    // explicitly before importing the Qt-free Core Host module.
    let bootstrap = format!(
        "import runpy,sys;sys.path[:0]=[{resource_root},{python_path_entries}];sys.argv[0]={core_main};runpy.run_module({core_main},run_name='__main__')"
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
            generation_number.max(1).to_string().into(),
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
        "optionalCapabilities": OPTIONAL_CAPABILITIES,
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
    if capabilities
        .iter()
        .any(|capability| capability == "transport.concurrent-router")
        && minor < 2
    {
        return Err(
            "INVALID_NEGOTIATION: concurrent router capability requires protocol minor 2.2"
                .to_string(),
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
        collections::{BTreeSet, VecDeque},
        fs::{self, File},
        io::{self, Cursor, Read, Write},
        net::TcpListener,
        path::PathBuf,
        sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            mpsc, Arc, Mutex,
        },
        thread,
        time::{Duration, Instant, SystemTime, UNIX_EPOCH},
    };

    use serde_json::{json, Value};

    use crate::{
        core_host_protocol::encode_frame,
        platform::{
            FilesystemRuntimeLocator, InstanceLockAcquire, InstanceLockBackend,
            ManagedPipeReadOutcome, ManagedPipeReader, ManagedProcessPipes, ManagedProcessRequest,
            ManagedProcessTree, ManagedProcessTreeBackend, PlatformError, PlatformErrorCategory,
            PlatformResult, PlatformService, ProcessExitStatus, ProcessStdio,
            ProcessTreeFinalization, ProcessTreeFinalizationFailure, ProcessTreeFinalizationResult,
            ProcessWaitOutcome, RetryAdvice, RuntimeLocationRequest, RuntimeLocator, RuntimeMode,
            SpawnedProcessTree, SHARED_INSTANCE_ID,
        },
        runtime_log::{CoreLogContext, RuntimeLogService, CORE_BRIDGE_PREFIX},
        shared_instance::NativeInstanceLockBackend,
    };

    #[cfg(windows)]
    use crate::{
        core_host_protocol::read_frame,
        managed_process_tree::{
            ManagedProcessSpec, ManagedProcessTree as WindowsManagedProcessTree, WaitOutcome,
        },
    };

    use super::{
        core_host_process_request, drain_stderr, hello_payload, lifecycle_test_lock,
        stderr_diagnostic_summary, CoreHostRuntime, CoreSnapshotCache, ShutdownPolicy,
        StderrDrainState, StderrDrainStats, StderrDrainer, StderrLogSink, StderrRedactor,
        MIN_PROTOCOL_MINOR, OPTIONAL_CAPABILITIES, PRODUCTION_SHUTDOWN_POLICY, PROTOCOL_MAJOR,
        PROTOCOL_MINOR, REQUIRED_CAPABILITIES, STDERR_CACHE_LIMIT,
    };

    const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";
    const WP_1C_04_LIFECYCLE_GOLDEN: &str =
        include_str!("../../../tests/fixtures/runtime_v2/wp_1c_04/lifecycle-golden.json");

    fn predecessor_hello_payload() -> Value {
        json!({
            "protocol": {
                "major": PROTOCOL_MAJOR,
                "minMinor": MIN_PROTOCOL_MINOR,
                "maxMinor": PROTOCOL_MINOR,
            },
            "requiredCapabilities": REQUIRED_CAPABILITIES,
            "optionalCapabilities": [OPTIONAL_CAPABILITIES[0], OPTIONAL_CAPABILITIES[1]],
        })
    }

    fn request_predecessor_hello(
        host: &mut CoreHostRuntime,
        request_id: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        host.request_with_payload(
            request_id,
            "system.hello",
            predecessor_hello_payload(),
            deadline,
        )
    }

    #[test]
    fn default_hello_payload_contains_current_assistant_capabilities() {
        let payload = hello_payload();

        assert_eq!(
            payload["optionalCapabilities"],
            json!([
                "transport.concurrent-router",
                "settings.provider-model",
                "assistant.tools-v1",
                "assistant.mcp-v1",
                "assistant.plugins-v1",
                "assistant.tts-v1",
                "assistant.screen-capture-v1"
            ])
        );
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

    fn copy_fixture_tree(source: &std::path::Path, destination: &std::path::Path) {
        fs::create_dir_all(destination).expect("fixture directory should create");
        for entry in fs::read_dir(source).expect("fixture directory should read") {
            let entry = entry.expect("fixture entry should read");
            let source_path = entry.path();
            let destination_path = destination.join(entry.file_name());
            if entry.file_type().expect("fixture type").is_dir() {
                copy_fixture_tree(&source_path, &destination_path);
            } else {
                fs::copy(&source_path, &destination_path).expect("fixture file should copy");
            }
        }
    }

    fn wp_3_02_local_provider() -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("local Provider should bind");
        let address = listener.local_addr().expect("local Provider address");
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("local Provider request");
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .expect("Provider read timeout");
            let mut request = Vec::new();
            let mut chunk = [0_u8; 4096];
            let expected_length = loop {
                let read = stream
                    .read(&mut chunk)
                    .expect("Provider request should read");
                assert!(read > 0, "Provider request ended before headers");
                request.extend_from_slice(&chunk[..read]);
                let Some(header_end) = request.windows(4).position(|part| part == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = String::from_utf8_lossy(&request[..header_end]);
                let content_length = headers
                    .lines()
                    .find_map(|line| {
                        line.split_once(':').and_then(|(name, value)| {
                            name.eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse::<usize>().ok())
                                .flatten()
                        })
                    })
                    .expect("Provider Content-Length");
                break header_end + 4 + content_length;
            };
            while request.len() < expected_length {
                let read = stream.read(&mut chunk).expect("Provider body should read");
                assert!(read > 0, "Provider request ended before body");
                request.extend_from_slice(&chunk[..read]);
            }
            let request_text = String::from_utf8_lossy(&request);
            assert!(request_text.contains("chat/completions"));
            assert!(!request_text.contains("generationCredential"));
            let content = serde_json::to_string(&json!({
                "segments": [{
                    "ja": "おかえり。", "zh": "欢迎回来。", "tone": "中性",
                    "portrait": "neutral"
                }]
            }))
            .expect("Provider content JSON");
            let body = serde_json::to_vec(&json!({
                "choices": [{"message": {"role": "assistant", "content": content}}]
            }))
            .expect("Provider response JSON");
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .expect("Provider response headers");
            stream.write_all(&body).expect("Provider response body");
            stream.flush().expect("Provider response flush");
        });
        (format!("http://{address}/v1"), worker)
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
                structured_records: 0,
                ordinary_records: 0,
                invalid_structured_records: 0,
                eof: false,
                read_failed: false,
            },
            ordinary_warning_emitted: false,
        }))
    }

    struct InjectedTree;

    impl ManagedProcessTree for InjectedTree {
        fn root_pid(&self) -> u32 {
            42
        }

        fn wait_root(&mut self, _timeout: Duration) -> PlatformResult<ProcessWaitOutcome> {
            Ok(ProcessWaitOutcome::TimedOut)
        }

        fn terminate_tree(&mut self, _reason_code: u32) -> PlatformResult<()> {
            Ok(())
        }

        fn wait_tree_exited(&self, _timeout: Duration) -> PlatformResult<bool> {
            Ok(true)
        }

        fn release_exited(self: Box<Self>) -> PlatformResult<()> {
            Ok(())
        }

        fn finalize_until(
            self: Box<Self>,
            _deadline: Instant,
            _reason_code: u32,
        ) -> ProcessTreeFinalizationResult {
            Ok(ProcessTreeFinalization {
                root_status: ProcessExitStatus::Unknown,
                forced: false,
            })
        }
    }

    struct InjectedBackend {
        stdout: Mutex<Option<Box<dyn ManagedPipeReader>>>,
    }

    impl ManagedProcessTreeBackend for InjectedBackend {
        fn spawn(&self, _request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
            Ok(SpawnedProcessTree {
                tree: Box::new(InjectedTree),
                pipes: Some(ManagedProcessPipes {
                    stdin: null_file(),
                    stdout: self
                        .stdout
                        .lock()
                        .expect("injected stdout lock")
                        .take()
                        .expect("injected stdout is consumed once"),
                    stderr: Box::new(EofPipeReader),
                }),
            })
        }
    }

    struct EofPipeReader;

    impl ManagedPipeReader for EofPipeReader {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            Ok(ManagedPipeReadOutcome::Eof)
        }
    }

    struct ChunkedPipeReader {
        chunks: VecDeque<Vec<u8>>,
    }

    impl ManagedPipeReader for ChunkedPipeReader {
        fn read_until(
            &mut self,
            buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            let Some(mut chunk) = self.chunks.pop_front() else {
                return Ok(ManagedPipeReadOutcome::Eof);
            };
            let count = chunk.len().min(buffer.len());
            buffer[..count].copy_from_slice(&chunk[..count]);
            if count < chunk.len() {
                self.chunks.push_front(chunk.split_off(count));
            }
            Ok(ManagedPipeReadOutcome::Read(count))
        }
    }

    struct DelayedTimeoutReader {
        active_readers: Arc<AtomicUsize>,
        completed: Arc<AtomicBool>,
        delay: Duration,
    }

    impl ManagedPipeReader for DelayedTimeoutReader {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            self.active_readers.fetch_add(1, Ordering::SeqCst);
            thread::sleep(self.delay);
            self.active_readers.fetch_sub(1, Ordering::SeqCst);
            self.completed.store(true, Ordering::SeqCst);
            Ok(ManagedPipeReadOutcome::TimedOut)
        }
    }

    struct CancelAwareReader {
        active_readers: Arc<AtomicUsize>,
        completed: Arc<AtomicBool>,
        dropped: Arc<AtomicBool>,
    }

    impl ManagedPipeReader for CancelAwareReader {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            _deadline: Instant,
            cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            self.active_readers.fetch_add(1, Ordering::SeqCst);
            while !cancelled.load(Ordering::Acquire) {
                thread::sleep(Duration::from_millis(1));
            }
            self.active_readers.fetch_sub(1, Ordering::SeqCst);
            self.completed.store(true, Ordering::SeqCst);
            Ok(ManagedPipeReadOutcome::Cancelled)
        }
    }

    impl Drop for CancelAwareReader {
        fn drop(&mut self) {
            self.dropped.store(true, Ordering::SeqCst);
        }
    }

    struct DeadlinePollingReader {
        active_readers: Arc<AtomicUsize>,
        completed: Arc<AtomicBool>,
        dropped: Arc<AtomicBool>,
    }

    impl ManagedPipeReader for DeadlinePollingReader {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            deadline: Instant,
            cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            self.active_readers.fetch_add(1, Ordering::SeqCst);
            while Instant::now() < deadline {
                thread::yield_now();
            }
            self.active_readers.fetch_sub(1, Ordering::SeqCst);
            self.completed.store(true, Ordering::SeqCst);
            if cancelled.load(Ordering::Acquire) {
                Ok(ManagedPipeReadOutcome::Cancelled)
            } else {
                Ok(ManagedPipeReadOutcome::TimedOut)
            }
        }
    }

    impl Drop for DeadlinePollingReader {
        fn drop(&mut self) {
            self.dropped.store(true, Ordering::SeqCst);
        }
    }

    #[cfg(unix)]
    fn null_file() -> File {
        File::options()
            .read(true)
            .write(true)
            .open("/dev/null")
            .expect("POSIX null device should open")
    }

    #[cfg(windows)]
    fn null_file() -> File {
        File::options()
            .read(true)
            .write(true)
            .open("NUL")
            .expect("Windows null device should open")
    }

    #[cfg(unix)]
    fn read_only_null_file() -> File {
        File::open("/dev/null").expect("POSIX null device should open read-only")
    }

    #[cfg(windows)]
    fn read_only_null_file() -> File {
        File::open("NUL").expect("Windows null device should open read-only")
    }

    fn runtime_with_stdout(stdout: Box<dyn ManagedPipeReader>) -> CoreHostRuntime {
        let backend = InjectedBackend {
            stdout: Mutex::new(Some(stdout)),
        };
        CoreHostRuntime::launch_with_backend(
            &backend,
            ManagedProcessRequest {
                program: PathBuf::from("injected-core-host"),
                args: Vec::new(),
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            },
            GENERATION_ID,
        )
        .expect("injected Core Host should launch")
    }

    struct CleanupTree {
        events: Arc<Mutex<Vec<&'static str>>>,
        deadlines: Arc<Mutex<Vec<Instant>>>,
        finalize_calls: Arc<AtomicUsize>,
        fail_first: bool,
        forced: bool,
    }

    impl ManagedProcessTree for CleanupTree {
        fn root_pid(&self) -> u32 {
            42
        }

        fn wait_root(&mut self, _timeout: Duration) -> PlatformResult<ProcessWaitOutcome> {
            Ok(ProcessWaitOutcome::Exited(ProcessExitStatus::Code(0)))
        }

        fn terminate_tree(&mut self, _reason_code: u32) -> PlatformResult<()> {
            Ok(())
        }

        fn wait_tree_exited(&self, _timeout: Duration) -> PlatformResult<bool> {
            Ok(true)
        }

        fn release_exited(self: Box<Self>) -> PlatformResult<()> {
            Ok(())
        }

        fn finalize_until(
            self: Box<Self>,
            deadline: Instant,
            _reason_code: u32,
        ) -> ProcessTreeFinalizationResult {
            self.events
                .lock()
                .expect("cleanup events")
                .push("tree_finalized");
            self.deadlines
                .lock()
                .expect("cleanup deadlines")
                .push(deadline);
            let call = self.finalize_calls.fetch_add(1, Ordering::SeqCst);
            if self.fail_first && call == 0 {
                return Err(ProcessTreeFinalizationFailure::new(
                    PlatformError::new(
                        PlatformService::ManagedProcessTree,
                        PlatformErrorCategory::TimedOut,
                        "finalize_until",
                        RetryAdvice::Never,
                        "injected finalizer exhausted the shared cleanup deadline",
                    ),
                    self,
                ));
            }
            Ok(ProcessTreeFinalization {
                root_status: ProcessExitStatus::Code(0),
                forced: self.forced || call > 0,
            })
        }
    }

    struct CleanupStdout {
        chunks: VecDeque<Vec<u8>>,
        events: Arc<Mutex<Vec<&'static str>>>,
        deadlines: Arc<Mutex<Vec<Instant>>>,
    }

    impl ManagedPipeReader for CleanupStdout {
        fn read_until(
            &mut self,
            buffer: &mut [u8],
            deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            let Some(mut chunk) = self.chunks.pop_front() else {
                self.events
                    .lock()
                    .expect("cleanup events")
                    .push("stdout_drained");
                self.deadlines
                    .lock()
                    .expect("cleanup deadlines")
                    .push(deadline);
                return Ok(ManagedPipeReadOutcome::Eof);
            };
            let count = chunk.len().min(buffer.len());
            buffer[..count].copy_from_slice(&chunk[..count]);
            if count < chunk.len() {
                self.chunks.push_front(chunk.split_off(count));
            }
            Ok(ManagedPipeReadOutcome::Read(count))
        }
    }

    fn completed_stderr_drainer() -> StderrDrainer {
        let state = stderr_state();
        state.lock().expect("stderr state").stats.eof = true;
        let cancelled = Arc::new(AtomicBool::new(false));
        let (sender, completion) = mpsc::sync_channel(1);
        sender.send(()).expect("stderr completion marker");
        StderrDrainer {
            state,
            cancelled,
            completion,
            reader: Some(thread::spawn(|| {})),
            log_sink: None,
        }
    }

    fn shutdown_frame() -> Vec<u8> {
        encode_frame(&json!({
            "protocolMajor": 2,
            "protocolMinor": 1,
            "kind": "response",
            "generationId": GENERATION_ID,
            "generationCredential": "11111111111111111111111111111111",
            "id": "shutdown",
            "name": "system.shutdown",
            "payload": {"accepted": true},
            "ok": true
        }))
        .expect("shutdown response frame")
    }

    fn cleanup_runtime(
        tree: Box<dyn ManagedProcessTree>,
        events: Arc<Mutex<Vec<&'static str>>>,
        deadlines: Arc<Mutex<Vec<Instant>>>,
        shutdown_written_at: Arc<Mutex<Option<Instant>>>,
    ) -> CoreHostRuntime {
        CoreHostRuntime::from_test_owners(
            tree,
            null_file(),
            Box::new(CleanupStdout {
                chunks: VecDeque::from([shutdown_frame()]),
                events: Arc::clone(&events),
                deadlines,
            }),
            completed_stderr_drainer(),
            GENERATION_ID,
            "11111111111111111111111111111111",
            events,
            shutdown_written_at,
        )
    }

    struct CredentialWriteFailureBackend {
        tree: Mutex<Option<Box<dyn ManagedProcessTree>>>,
    }

    impl ManagedProcessTreeBackend for CredentialWriteFailureBackend {
        fn spawn(&self, _request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
            Ok(SpawnedProcessTree {
                tree: self
                    .tree
                    .lock()
                    .expect("credential failure tree")
                    .take()
                    .expect("credential failure backend spawns once"),
                pipes: Some(ManagedProcessPipes {
                    stdin: read_only_null_file(),
                    stdout: Box::new(EofPipeReader),
                    stderr: Box::new(EofPipeReader),
                }),
            })
        }
    }

    #[test]
    fn credential_bootstrap_failure_uses_the_same_typed_consuming_cleanup_tail() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let deadlines = Arc::new(Mutex::new(Vec::new()));
        let finalize_calls = Arc::new(AtomicUsize::new(0));
        let backend = CredentialWriteFailureBackend {
            tree: Mutex::new(Some(Box::new(CleanupTree {
                events,
                deadlines,
                finalize_calls: Arc::clone(&finalize_calls),
                fail_first: false,
                forced: false,
            }))),
        };

        let failure = CoreHostRuntime::launch_with_backend(
            &backend,
            ManagedProcessRequest {
                program: PathBuf::from("injected-core-host"),
                args: Vec::new(),
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            },
            GENERATION_ID,
        )
        .expect_err("read-only stdin must fail credential bootstrap");

        assert!(failure
            .diagnostic()
            .starts_with("TRANSPORT_WRITE_FAILED: Core Host credential bootstrap failed"));
        assert_eq!(finalize_calls.load(Ordering::SeqCst), 1);
        assert!(failure.into_recovery().is_none());
    }

    #[test]
    fn production_shutdown_policy_is_exactly_3000ms_graceful_and_5000ms_total() {
        assert_eq!(
            PRODUCTION_SHUTDOWN_POLICY.graceful,
            Duration::from_millis(3000)
        );
        assert_eq!(
            PRODUCTION_SHUTDOWN_POLICY.total,
            Duration::from_millis(5000)
        );
    }

    #[test]
    fn shutdown_freezes_one_total_deadline_and_consumes_owners_in_order() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let deadlines = Arc::new(Mutex::new(Vec::new()));
        let finalize_calls = Arc::new(AtomicUsize::new(0));
        let shutdown_written_at = Arc::new(Mutex::new(None));
        let policy = PRODUCTION_SHUTDOWN_POLICY;
        let host = cleanup_runtime(
            Box::new(CleanupTree {
                events: Arc::clone(&events),
                deadlines: Arc::clone(&deadlines),
                finalize_calls: Arc::clone(&finalize_calls),
                fail_first: false,
                forced: false,
            }),
            Arc::clone(&events),
            Arc::clone(&deadlines),
            Arc::clone(&shutdown_written_at),
        );

        let exit = host
            .shutdown_with_policy(policy)
            .expect("injected cleanup should consume every owner");

        assert!(!exit.forced);
        assert_eq!(finalize_calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            *events.lock().expect("cleanup events"),
            [
                "shutdown_written",
                "stdin_closed",
                "tree_finalized",
                "stdout_drained",
                "stderr_finished",
                "readers_dropped",
            ]
        );
        let deadlines = deadlines.lock().expect("cleanup deadlines");
        assert_eq!(deadlines.iter().copied().collect::<BTreeSet<_>>().len(), 1);
        let written_at = shutdown_written_at
            .lock()
            .expect("shutdown write instant")
            .expect("shutdown write must be sampled after flush");
        assert_eq!(deadlines[0].duration_since(written_at), policy.total);
    }

    #[test]
    fn finalizer_failure_returns_recovery_without_automatic_second_call() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let deadlines = Arc::new(Mutex::new(Vec::new()));
        let finalize_calls = Arc::new(AtomicUsize::new(0));
        let shutdown_written_at = Arc::new(Mutex::new(None));
        let host = cleanup_runtime(
            Box::new(CleanupTree {
                events: Arc::clone(&events),
                deadlines: Arc::clone(&deadlines),
                finalize_calls: Arc::clone(&finalize_calls),
                fail_first: true,
                forced: true,
            }),
            Arc::clone(&events),
            Arc::clone(&deadlines),
            shutdown_written_at,
        );

        let failure = host
            .shutdown_with_policy(ShutdownPolicy {
                graceful: Duration::from_millis(30),
                total: Duration::from_millis(50),
            })
            .expect_err("tree failure must retain a recovery capsule");

        assert_eq!(finalize_calls.load(Ordering::SeqCst), 1);
        assert!(failure.diagnostic().contains("finalize_until"));
        assert!(!format!("{failure:?}").contains("CleanupTree"));
        assert_eq!(
            *events.lock().expect("cleanup events"),
            [
                "shutdown_written",
                "stdin_closed",
                "tree_finalized",
                "stdout_drained",
                "stderr_finished",
                "readers_dropped",
            ]
        );
        let recovery = failure
            .into_recovery()
            .expect("tree finalizer failure must retain the owner");
        assert_eq!(finalize_calls.load(Ordering::SeqCst), 1);
        let recovered = recovery
            .finalize_until(Instant::now() + Duration::from_secs(1))
            .expect("explicit recovery with a new deadline should consume the owner");
        assert!(recovered.forced);
        assert_eq!(finalize_calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn root_first_exit_still_requires_the_consuming_tree_finalizer() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let deadlines = Arc::new(Mutex::new(Vec::new()));
        let finalize_calls = Arc::new(AtomicUsize::new(0));
        let host = cleanup_runtime(
            Box::new(CleanupTree {
                events: Arc::clone(&events),
                deadlines: Arc::clone(&deadlines),
                finalize_calls: Arc::clone(&finalize_calls),
                fail_first: false,
                forced: true,
            }),
            events,
            deadlines,
            Arc::new(Mutex::new(None)),
        );

        let exit = host
            .shutdown_with_policy(ShutdownPolicy {
                graceful: Duration::from_millis(30),
                total: Duration::from_millis(50),
            })
            .expect("root-first descendant cleanup should succeed");
        assert!(exit.forced);
        assert_eq!(finalize_calls.load(Ordering::SeqCst), 1);
    }

    fn framed_response(id: &str) -> (Value, Vec<u8>) {
        let response = json!({
            "protocolMajor": 2,
            "protocolMinor": 1,
            "kind": "response",
            "generationId": GENERATION_ID,
            "generationCredential": "11111111111111111111111111111111",
            "id": id,
            "name": "system.health",
            "payload": {},
            "ok": true
        });
        let frame = encode_frame(&response).expect("test response frame should encode");
        (response, frame)
    }

    #[test]
    fn response_read_preserves_a_partial_following_frame() {
        let (first, first_frame) = framed_response("first");
        let (second, second_frame) = framed_response("second");
        let split = 2;
        let mut first_chunk = first_frame;
        first_chunk.extend_from_slice(&second_frame[..split]);
        let mut host = runtime_with_stdout(Box::new(ChunkedPipeReader {
            chunks: VecDeque::from([first_chunk, second_frame[split..].to_vec()]),
        }));

        assert_eq!(
            host.read_response_until(Instant::now() + Duration::from_secs(1))
                .expect("first response should decode"),
            first
        );
        assert_eq!(
            host.read_response_until(Instant::now() + Duration::from_secs(1))
                .expect("partial second response must remain observable"),
            second
        );
    }

    #[test]
    fn response_read_preserves_a_trailing_byte_for_eof_validation() {
        let (response, mut frame) = framed_response("trailing-byte");
        frame.push(0xff);
        let mut host = runtime_with_stdout(Box::new(ChunkedPipeReader {
            chunks: VecDeque::from([frame]),
        }));

        assert_eq!(
            host.read_response_until(Instant::now() + Duration::from_secs(1))
                .expect("first response should decode"),
            response
        );
        let error = host
            .read_response_until(Instant::now() + Duration::from_secs(1))
            .expect_err("trailing byte must remain observable at EOF");
        assert!(error.starts_with("INCOMPLETE_FRAME:"), "{error}");
    }

    #[test]
    fn response_read_does_not_parse_following_pollution_before_returning_first_frame() {
        let (response, mut frame) = framed_response("pollution-after-frame");
        frame.extend_from_slice(b"junk");
        let mut host = runtime_with_stdout(Box::new(ChunkedPipeReader {
            chunks: VecDeque::from([frame]),
        }));

        assert_eq!(
            host.read_response_until(Instant::now() + Duration::from_secs(1))
                .expect("first response must return before following pollution is parsed"),
            response
        );
        let error = host
            .read_response_until(Instant::now() + Duration::from_secs(1))
            .expect_err("following pollution must remain observable by the next read");
        assert!(error.starts_with("STDOUT_FRAMING_POLLUTION:"), "{error}");
    }

    #[test]
    fn timed_out_runtime_response_read_leaves_no_active_reader() {
        let active_readers = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicBool::new(false));
        let delay = Duration::from_millis(75);
        let mut host = runtime_with_stdout(Box::new(DelayedTimeoutReader {
            active_readers: Arc::clone(&active_readers),
            completed: Arc::clone(&completed),
            delay,
        }));
        let started = Instant::now();
        let error = host
            .read_response_until(Instant::now() + Duration::from_millis(10))
            .expect_err("injected response timeout must fail closed");
        assert!(error.starts_with("REQUEST_DEADLINE_EXCEEDED:"));
        assert!(started.elapsed() >= delay);
        assert!(completed.load(Ordering::SeqCst));
        assert_eq!(active_readers.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn stderr_finish_timeout_cancels_and_drop_joins_the_reader() {
        let active_readers = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicBool::new(false));
        let dropped = Arc::new(AtomicBool::new(false));
        let mut drainer = StderrDrainer::start(
            Box::new(CancelAwareReader {
                active_readers: Arc::clone(&active_readers),
                completed: Arc::clone(&completed),
                dropped: Arc::clone(&dropped),
            }),
            GENERATION_ID,
            42,
            "11111111111111111111111111111111",
            None,
        );
        let active_deadline = Instant::now() + Duration::from_millis(500);
        while active_readers.load(Ordering::SeqCst) == 0 && Instant::now() < active_deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(active_readers.load(Ordering::SeqCst), 1);

        let error = drainer
            .finish_until(Instant::now())
            .expect_err("expired stderr completion deadline must cancel");
        assert!(error.starts_with("STDERR_READ_FAILED:"));
        assert!(drainer.cancelled.load(Ordering::Acquire));
        drop(drainer);

        assert!(completed.load(Ordering::SeqCst));
        assert!(dropped.load(Ordering::SeqCst));
        assert_eq!(active_readers.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn stderr_drop_insurance_is_bounded_by_one_pipe_poll_quantum() {
        let active_readers = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicBool::new(false));
        let dropped = Arc::new(AtomicBool::new(false));
        let mut drainer = StderrDrainer::start(
            Box::new(DeadlinePollingReader {
                active_readers: Arc::clone(&active_readers),
                completed: Arc::clone(&completed),
                dropped: Arc::clone(&dropped),
            }),
            GENERATION_ID,
            42,
            "11111111111111111111111111111111",
            None,
        );
        let active_deadline = Instant::now() + Duration::from_millis(500);
        while active_readers.load(Ordering::SeqCst) == 0 && Instant::now() < active_deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(active_readers.load(Ordering::SeqCst), 1);

        drainer
            .finish_until(Instant::now())
            .expect_err("expired completion deadline must cancel the reader");
        let drop_started = Instant::now();
        drop(drainer);
        let drop_elapsed = drop_started.elapsed();

        assert!(
            drop_elapsed < Duration::from_millis(50),
            "stderr Drop exceeded one 10ms pipe poll quantum: {drop_elapsed:?}"
        );
        assert!(completed.load(Ordering::SeqCst));
        assert!(dropped.load(Ordering::SeqCst));
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

    fn valid_character_presentation() -> Value {
        json!({
            "schemaVersion": 1,
            "generationId": GENERATION_ID,
            "characterId": "sakura",
            "displayName": "Sakura",
            "initialMessage": "hello",
            "themeTokens": {
                "primary": "#d55b91",
                "primaryHover": "#bd477c",
                "accent": "#7f67c9",
                "text": "#332631",
                "secondaryText": "#684e63",
                "mutedText": "#92768b",
                "pageBackground": "#fff5fa",
                "panelBackground": "#fdebf4",
                "inputBackground": "#ffffff",
                "bubbleBackground": "#fff0f7",
                "border": "#efbfd6"
            },
            "defaultPortraitKey": "__default__",
            "portraitKeys": ["__default__"],
            "portraitResourceIds": {
                "__default__": "character-v1-73616b757261-portrait-5f5f64656661756c745f5f"
            }
        })
    }

    #[test]
    fn wp_2_02_snapshot_is_exact_monotonic_and_generation_scoped() {
        let mut cache = CoreSnapshotCache::new(GENERATION_ID).expect("generation cache");
        let first = json!({
            "generationId": GENERATION_ID,
            "revision": 1,
            "readiness": "ready",
            "currentCharacterSummary": valid_character_summary(),
            "characterPresentation": valid_character_presentation(),
            "activeInteractionSummary": {
                "operationId": "chat-1",
                "state": "started"
            }
        });
        cache
            .store_minimal_python_snapshot(&first)
            .expect("six-field snapshot validates");
        let mut extra = first.clone();
        extra["schemaVersion"] = json!(1);
        assert!(cache.store_minimal_python_snapshot(&extra).is_err());
        let mut stale = first.clone();
        stale["revision"] = json!(0);
        assert!(cache.store_minimal_python_snapshot(&stale).is_err());
        let mut reused = first.clone();
        reused["activeInteractionSummary"] = Value::Null;
        assert!(cache.store_minimal_python_snapshot(&reused).is_err());
        cache
            .begin_generation("00000000-0000-4000-8000-000000002203")
            .expect("new generation clears cache");
        assert!(cache.current().is_none());
        assert!(cache.store_minimal_python_snapshot(&first).is_err());
    }

    #[test]
    fn launch_command_uses_only_the_runtime_locator_approved_assistant_root() {
        let mut layout = development_layout();
        layout.assistant_root = std::env::temp_dir().canonicalize().unwrap();
        let request = core_host_process_request(&layout, GENERATION_ID, 1)
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
            None,
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
    fn wp_4l_02_structured_stderr_is_reassembled_into_human_runtime_log() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sakura-wp-4l-01-stderr-{}-{nonce}",
            std::process::id()
        ));
        let path = root.join("sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(path.clone());
        let credential = "91919191919191919191919191919191";
        let sink = StderrLogSink {
            runtime_log: runtime_log.clone(),
            context: CoreLogContext {
                generation_id: "generation-old".to_string(),
                generation_number: 3,
                core_pid: 4242,
            },
            generation_credential: credential.to_string(),
        };
        let structured = format!(
            "{CORE_BRIDGE_PREFIX}{{\"severity\":\"info\",\"verbosity\":\"info\",\"channel\":\"agent\",\"event\":\"agent.turn.started\",\"message\":\"fixed\",\"operation_id\":\"operation-old\"}}\n"
        );
        let bytes = format!("ordinary one\n{structured}ordinary two\n").into_bytes();
        let state = stderr_state();
        drain_stderr(
            Box::new(TestPipeReader {
                inner: FragmentedReader {
                    bytes: Cursor::new(bytes),
                    chunk_size: 1,
                },
            }),
            &state,
            &StderrRedactor::new(credential),
            &AtomicBool::new(false),
            Some(&sink),
        );
        assert!(runtime_log.shutdown(Duration::from_millis(500)));

        let state = state.lock().expect("stderr state");
        assert_eq!(state.stats.structured_records, 1);
        assert_eq!(state.stats.ordinary_records, 2);
        assert_eq!(state.stats.invalid_structured_records, 0);
        let tail = state.records.iter().cloned().collect::<String>();
        assert!(!tail.contains(CORE_BRIDGE_PREFIX));
        drop(state);

        let contents = fs::read_to_string(&path).unwrap();
        let lines = contents.lines().collect::<Vec<_>>();
        assert_eq!(lines.len(), 2);
        assert!(lines[0]
            .contains("[CORE] Core 输出了异常诊断 │ outcome=detected diagnostic=ordinary one"));
        assert!(lines[1].contains("[AGENT] 开始处理用户消息"));
        assert!(!contents.contains(CORE_BRIDGE_PREFIX));
        assert!(!contents.contains(credential));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_stderr_summary_replaces_paths_and_urls_without_hiding_the_cause() {
        let summary = stderr_diagnostic_summary(
            "File C:\\private\\bridge.py failed while requesting https://user:pass@example.test/path",
        );
        assert_eq!(summary, "File [PATH] failed while requesting [URL]");
        assert!(!summary.contains("private"));
        assert!(!summary.contains("user:pass"));
    }

    #[test]
    fn wp_4l_01_structured_stderr_rejects_generation_credential_before_persistence() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sakura-wp-4l-01-credential-{}-{nonce}",
            std::process::id()
        ));
        let path = root.join("sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(path.clone());
        let credential = "92929292929292929292929292929292";
        let sink = StderrLogSink {
            runtime_log: runtime_log.clone(),
            context: CoreLogContext {
                generation_id: "generation-private".to_string(),
                generation_number: 4,
                core_pid: 4343,
            },
            generation_credential: credential.to_string(),
        };
        let line = format!(
            "{CORE_BRIDGE_PREFIX}{{\"severity\":\"info\",\"verbosity\":\"info\",\"channel\":\"agent\",\"event\":\"agent.turn.started\",\"message\":\"fixed\",\"attributes\":{{\"status\":\"{credential}\"}}}}\n"
        );
        let state = stderr_state();
        drain_stderr(
            Box::new(TestPipeReader {
                inner: Cursor::new(line.into_bytes()),
            }),
            &state,
            &StderrRedactor::new(credential),
            &AtomicBool::new(false),
            Some(&sink),
        );
        assert!(runtime_log.shutdown(Duration::from_millis(500)));

        let stats = &state.lock().expect("stderr state").stats;
        assert_eq!(stats.structured_records, 0);
        assert_eq!(stats.invalid_structured_records, 1);
        let contents = fs::read_to_string(path).unwrap();
        assert!(!contents.contains(credential));
        assert!(!contents.contains("agent.turn.started"));
        let _ = fs::remove_dir_all(root);
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
            None,
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
            None,
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
            log_sink: None,
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
        request_predecessor_hello(&mut host, "hello-flood", Duration::from_secs(3))
            .expect("stderr flood must not block hello");
        let exit = host.shutdown().expect("stderr flood fixture stops cleanly");
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
    fn slow_cooperative_shutdown_uses_graceful_time_inside_the_single_total_budget() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_3_01/slow_shutdown_host.py");
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("slow shutdown fixture launches");
        request_predecessor_hello(&mut host, "hello-slow", Duration::from_secs(3))
            .expect("slow fixture hello negotiates");
        let shutdown_written_at = Arc::new(Mutex::new(None));
        host.observe_shutdown_write_for_test(Arc::clone(&shutdown_written_at));

        let exit = host
            .shutdown_with_policy(ShutdownPolicy {
                // Preserve the 2.9s boundary fixture while leaving CI runner
                // scheduling outside the frozen 3s production contract.
                graceful: Duration::from_millis(3500),
                total: PRODUCTION_SHUTDOWN_POLICY.total,
            })
            .expect("slow cooperative shutdown should finish inside the total budget");
        let elapsed = shutdown_written_at
            .lock()
            .expect("shutdown write instant")
            .expect("shutdown flush must expose t0")
            .elapsed();

        assert!(
            elapsed >= Duration::from_millis(2850),
            "elapsed={elapsed:?}"
        );
        assert!(elapsed < Duration::from_millis(5500), "elapsed={elapsed:?}");
        assert!(!exit.forced);
        assert!(exit.tree_empty);
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
        let error = request_predecessor_hello(&mut host, "hello-crash", Duration::from_secs(3))
            .expect_err("crashed Core cannot answer hello");
        assert!(error.starts_with("CORE_CRASHED:"));
        let exit = host
            .close_stdin_and_wait()
            .expect("crashed Core resources are finalized");
        assert_eq!(exit.root_exit_code, 42);
        assert!(exit.stderr_stats.eof);
        assert!(!exit.stderr.contains("must-not-leak"));
    }

    #[test]
    fn managed_real_python_host_answers_control_and_releases_its_job_and_pipes() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let log_root = std::env::temp_dir().join(format!(
            "sakura-core-host-observed-{}-{nonce}",
            std::process::id()
        ));
        let log_path = log_root.join("sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(log_path.clone());
        let mut host =
            CoreHostRuntime::launch_observed(&layout, GENERATION_ID, 1, runtime_log.clone())
                .expect("real Core Host should launch in a managed Job");
        assert!(host.pid() > 0);

        let hello = request_predecessor_hello(&mut host, "hello", Duration::from_secs(3))
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
            .shutdown()
            .expect("protocol shutdown should reclaim the complete Job");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
        assert!(exit.stderr_stats.structured_records >= 2);
        assert_eq!(exit.stderr_stats.invalid_structured_records, 0);
        assert!(runtime_log.shutdown(Duration::from_millis(500)));
        let records = fs::read_to_string(log_path).expect("observed Core records are persisted");
        assert!(records.contains("[CORE] Core 日志桥已启动"));
        assert!(records.contains("[CORE] Core 日志桥正在停止"));
        assert!(records.contains("[CORE] Core 诊断输出已汇总"));
        let _ = fs::remove_dir_all(log_root);
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
        compatible.shutdown().expect("compatible Host stops");

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
            .close_stdin_and_wait()
            .expect("failed handshake Host stops on stdin EOF");
    }

    #[test]
    fn negotiated_router_supports_multiple_in_flight_control_requests() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        let hello = request_predecessor_hello(&mut host, "router-hello", Duration::from_secs(3))
            .expect("router hello should negotiate");
        assert_eq!(hello["protocolMinor"], 2);
        assert!(hello["payload"]["capabilities"]
            .as_array()
            .is_some_and(|capabilities| {
                capabilities
                    .iter()
                    .any(|capability| capability == "transport.concurrent-router")
            }));
        let handle = host
            .concurrent_request_handle()
            .expect("router capability should be available");
        let first = handle.clone();
        let second = handle;
        let first = thread::spawn(move || {
            first.request(
                "router-health-a",
                "system.health",
                json!({}),
                Duration::from_secs(3),
            )
        });
        let second = thread::spawn(move || {
            second.request(
                "router-health-b",
                "system.health",
                json!({}),
                Duration::from_secs(3),
            )
        });
        assert_eq!(
            first
                .join()
                .expect("first waiter thread")
                .expect("first waiter response")["id"],
            "router-health-a"
        );
        assert_eq!(
            second
                .join()
                .expect("second waiter thread")
                .expect("second waiter response")["id"],
            "router-health-b"
        );
        host.shutdown().expect("router host shutdown");
    }

    #[test]
    fn wp_3s_01_real_core_round_trips_redacted_provider_settings_atomically() {
        let _test_lock = lifecycle_test_lock();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("test clock")
            .as_nanos();
        let app_root =
            std::env::temp_dir().join(format!("sakura-wp-3s-01-{}-{unique}", std::process::id()));
        let source = repo_root().join("tests/fixtures/runtime_v2/wp_3_01/ready");
        copy_fixture_tree(&source, &app_root);
        let secret = "WP_3S_01_SECRET_MUST_NOT_ESCAPE";
        fs::write(
            app_root.join("data/config/api.yaml"),
            format!(
                "llm:\n  base_url: https://fixture.invalid/v1\n  api_key: {secret}\n  model: fixture-model\napi_profiles:\n  - id: fixture\n    alias: Fixture\n    base_url: https://fixture.invalid/v1\n    api_key: {secret}\n    preserve_me: true\n    models:\n      - name: fixture-model\nmodel_slots:\n  chat:\n    profile_id: fixture\n    model: fixture-model\ntts:\n  enabled: false\n"
            ),
        )
        .expect("provider fixture should write");
        let mut layout = development_layout();
        layout.assistant_root = app_root
            .canonicalize()
            .expect("provider fixture should resolve");
        let mut host = CoreHostRuntime::launch(&layout, GENERATION_ID)
            .expect("real provider settings Core should launch");
        let hello = request_predecessor_hello(&mut host, "settings-hello", Duration::from_secs(3))
            .expect("settings hello should negotiate");
        assert!(hello["payload"]["capabilities"]
            .as_array()
            .is_some_and(|items| items.iter().any(|item| item == "settings.provider-model")));
        let handle = host
            .concurrent_request_handle()
            .expect("settings router should be available");
        let get = handle
            .request(
                "settings-get",
                "settings.provider_model.get",
                json!({}),
                Duration::from_secs(3),
            )
            .expect("provider settings get should complete");
        assert_eq!(get["ok"], true);
        assert_eq!(get["payload"]["providers"][0]["configured"], true);
        assert!(!serde_json::to_string(&get)
            .expect("settings response should serialize")
            .contains(secret));

        let save = handle
            .request(
                "settings-save",
                "settings.provider_model.save",
                json!({
                    "draft": {
                        "providers": [{
                            "id": "fixture",
                            "alias": "Fixture edited",
                            "base_url": "https://fixture.invalid/v1",
                            "models": ["fixture-model"],
                            "credential": {"action": "keep", "value": ""}
                        }],
                        "model_slots": {
                            "chat": {"profile_id": "fixture", "model": "fixture-model"},
                            "vision_chat": {}
                        },
                        "settings": {
                            "timeout_seconds": 30,
                            "temperature": null,
                            "top_p": null,
                            "max_tokens": null
                        }
                    }
                }),
                Duration::from_secs(5),
            )
            .expect("provider settings save should complete");
        assert_eq!(
            save["payload"]["change_plan"], "applied",
            "unexpected provider save response: {save}"
        );
        host.shutdown().expect("provider settings host should stop");
        let saved = fs::read_to_string(app_root.join("data/config/api.yaml"))
            .expect("saved provider config should read");
        assert!(saved.contains(secret));
        assert!(saved.contains("preserve_me: true"));
        assert!(saved.contains("Fixture edited"));
        fs::remove_dir_all(&app_root).expect("isolated provider fixture should clean up");
    }

    #[test]
    fn wp_3_02_production_gateway_rejects_fixture_fields_before_core_write() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        request_predecessor_hello(&mut host, "chat-hello", Duration::from_secs(3))
            .expect("router hello should negotiate");
        let gateway = host
            .chat_gateway()
            .expect("chat Gateway should be available");
        let error = gateway
            .send(
                "main",
                json!({
                    "message": "hello",
                    "fixture": {"kind": "sleep", "delayMs": 10_000}
                }),
            )
            .expect_err("fixture-only fields must not reach the production Core");
        assert!(error.starts_with("INVALID_CHAT_PAYLOAD:"));
        assert_eq!(gateway.registry_len(), 0);
        let health = host
            .request(
                "post-rejection-health",
                "system.health",
                Duration::from_secs(3),
            )
            .expect("local rejection must not affect Core health");
        assert_eq!(health["ok"], true);
        host.shutdown().expect("chat host shutdown");
    }

    #[test]
    fn wp_3_02_rust_gateway_drives_real_core_local_provider_and_history() {
        let _test_lock = lifecycle_test_lock();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("test clock")
            .as_nanos();
        let app_root =
            std::env::temp_dir().join(format!("sakura-wp-3-02-{}-{unique}", std::process::id()));
        let source = repo_root().join("tests/fixtures/runtime_v2/wp_3_01/ready");
        copy_fixture_tree(&source, &app_root);
        let (provider_url, provider) = wp_3_02_local_provider();
        fs::write(
            app_root.join("data/config/api.yaml"),
            format!(
                "api_profiles:\n  - id: fixture\n    alias: Fixture Provider\n    base_url: {provider_url}\n    api_key: LOCAL_TEST_KEY\n    models:\n      - name: fixture-model\nmodel_slots:\n  chat:\n    profile_id: fixture\n    model: fixture-model\nconfig_version: 4\n"
            ),
        )
        .expect("local Provider config should write");
        let mut layout = development_layout();
        layout.assistant_root = app_root
            .canonicalize()
            .expect("Assistant fixture should resolve");
        let generation = "00000000-0000-4000-8000-000000003002";
        let mut host =
            CoreHostRuntime::launch(&layout, generation).expect("real Core should launch");
        request_predecessor_hello(&mut host, "real-chat-hello", Duration::from_secs(3))
            .expect("real chat hello");
        host.request_with_payload(
            "real-chat-initialize",
            "core.initialize",
            json!({}),
            Duration::from_secs(3),
        )
        .expect("real chat initialize");
        let ready_deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let snapshot = host
                .refresh_snapshot("real-chat-ready", Duration::from_secs(3))
                .expect("real chat readiness Snapshot");
            if matches!(snapshot["readiness"].as_str(), Some("ready" | "degraded")) {
                break;
            }
            assert!(
                Instant::now() < ready_deadline,
                "Assistant readiness timed out"
            );
            thread::sleep(Duration::from_millis(10));
        }

        let gateway = host.chat_gateway().expect("real chat Gateway");
        let submission = gateway
            .send("main", json!({"message": "ただいま"}))
            .expect("real chat should submit");
        let started = host
            .recv_event_timeout(Duration::from_secs(3))
            .expect("real chat started read")
            .expect("real chat started event");
        assert_eq!(started["name"], "chat.started");
        assert_eq!(
            gateway.observe_event(&started).expect("started validation"),
            crate::core_host_gateway::EventDisposition::Accepted
        );
        let terminal = host
            .recv_event_timeout(Duration::from_secs(10))
            .expect("real chat terminal read")
            .expect("real chat terminal event");
        assert_eq!(terminal["name"], "chat.completed");
        assert_eq!(terminal["payload"]["historyStatus"], "saved");
        assert_eq!(
            terminal["payload"]["reply"]["segments"][0]["text"],
            "おかえり。"
        );
        assert_eq!(
            gateway
                .observe_event(&terminal)
                .expect("terminal validation"),
            crate::core_host_gateway::EventDisposition::Accepted
        );
        let accepted = submission
            .completion
            .recv_timeout(Duration::from_secs(3))
            .expect("real chat response channel")
            .expect("real chat response");
        assert_eq!(accepted["payload"]["accepted"], true);
        let history = fs::read_to_string(app_root.join("data/chat_history/sakura.jsonl"))
            .expect("real chat history should exist");
        assert!(history.contains("ただいま"));
        assert!(history.contains("おかえり。"));
        assert!(!history.contains("LOCAL_TEST_KEY"));
        host.shutdown().expect("real chat shutdown");
        provider.join().expect("local Provider should stop");
        fs::remove_dir_all(&app_root).expect("isolated Assistant fixture should remove");
    }

    #[test]
    fn generation_credentials_are_unique_and_never_enter_debug_or_snapshot() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut first =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("first Host launches");
        let first_credential = first.generation_credential.clone();
        assert!(!format!("{first:?}").contains(&first_credential));
        request_predecessor_hello(&mut first, "hello-first", Duration::from_secs(3))
            .expect("first hello");
        let snapshot = first
            .refresh_snapshot("snapshot-first", Duration::from_secs(3))
            .expect("first snapshot");
        assert!(!snapshot.to_string().contains(&first_credential));
        first.shutdown().expect("first Host stops");

        let second = CoreHostRuntime::launch(&layout, GENERATION_ID).expect("second Host launches");
        let second_credential = second.generation_credential.clone();
        assert!(
            first_credential != second_credential,
            "each generation must receive a unique credential"
        );
        assert!(!format!("{second:?}").contains(&second_credential));
        second.close_stdin_and_wait().expect("second Host stops");
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
        let error = request_predecessor_hello(&mut host, "hello-stale", Duration::from_secs(3))
            .expect_err("stale credential response must fail");
        assert!(error.starts_with("GENERATION_CREDENTIAL_MISMATCH:"));
        assert!(!error.contains(&credential));
        let exit = host
            .close_stdin_and_wait()
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
        let hello = request_predecessor_hello(&mut host, "hello", golden_deadline(golden, "hello"))
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
            .shutdown()
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
        assert!(error.diagnostic().contains("architecture"));

        let mut inconsistent_entry = development_layout();
        inconsistent_entry.core_entry = inconsistent_entry.python_executable.clone();
        let error = CoreHostRuntime::launch(&inconsistent_entry, GENERATION_ID)
            .expect_err("inconsistent Core entry must fail before spawn");
        assert!(error.diagnostic().contains("Core entry"));

        let mut escaped_resources = development_layout();
        escaped_resources.runtime_root = escaped_resources
            .runtime_root
            .join("runtime")
            .canonicalize()
            .expect("development Runtime directory should resolve");
        let error = CoreHostRuntime::launch(&escaped_resources, GENERATION_ID)
            .expect_err("resources outside the Runtime root must fail before spawn");
        assert!(error.diagnostic().contains("resources"));
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
        request_predecessor_hello(
            &mut final_readiness,
            "hello-final",
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
            .shutdown()
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
        let error = request_predecessor_hello(
            &mut crashed,
            "hello-crash",
            golden_deadline(&golden, "hello"),
        )
        .expect_err("crashed bundled Core cannot answer hello");
        assert!(error.starts_with("CORE_CRASHED:"));
        let crash_exit = crashed
            .close_stdin_and_wait()
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
        let failure = ignoring
            .shutdown()
            .expect_err("ignored shutdown must preserve its protocol timeout diagnostic");
        assert!(failure
            .diagnostic()
            .starts_with("REQUEST_DEADLINE_EXCEEDED:"));
        let diagnostic = failure.diagnostic().to_string();
        assert!(
            failure.into_recovery().is_none(),
            "ignored shutdown retained a recovery owner: {diagnostic}"
        );
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
        request_predecessor_hello(&mut host, "hello-trailing", Duration::from_secs(3))
            .expect("fixture hello negotiates");
        let error = host
            .shutdown()
            .expect_err("trailing stdout must be transport fatal");
        assert!(
            error.diagnostic().starts_with("STDOUT_FRAMING_POLLUTION:"),
            "unexpected trailing stdout error: {}",
            error.diagnostic()
        );
    }

    #[test]
    fn managed_real_python_host_treats_clean_stdin_eof_as_orderly_exit() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let log_root = std::env::temp_dir().join(format!(
            "sakura-core-host-eof-observed-{}-{nonce}",
            std::process::id()
        ));
        let log_path = log_root.join("sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(log_path.clone());
        let host = CoreHostRuntime::launch_observed(&layout, GENERATION_ID, 1, runtime_log.clone())
            .expect("real Core Host should launch in a managed Job");
        let exit = host
            .close_stdin_and_wait()
            .expect("stdin EOF should stop and reclaim the Core Host");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
        assert!(exit.stderr_stats.structured_records >= 2);
        assert_eq!(exit.stderr_stats.invalid_structured_records, 0);
        assert!(runtime_log.shutdown(Duration::from_millis(500)));
        let records = fs::read_to_string(log_path).expect("observed Core records are persisted");
        assert!(records.contains("[CORE] Core 日志桥已启动"));
        assert!(records.contains("[CORE] Core 日志桥正在停止"));
        assert!(records.contains("[CORE] Core 诊断输出已汇总"));
        let _ = fs::remove_dir_all(log_root);
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
            WindowsManagedProcessTree::spawn_piped(&spec).expect("polluting fixture should launch");
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
        let mut host =
            CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
                .expect("ignoring fixture should launch");
        let shutdown_written_at = Arc::new(Mutex::new(None));
        host.observe_shutdown_write_for_test(Arc::clone(&shutdown_written_at));
        let failure = host
            .shutdown()
            .expect_err("ignored shutdown must preserve its protocol timeout diagnostic");
        let elapsed = shutdown_written_at
            .lock()
            .expect("shutdown write instant")
            .expect("shutdown flush must expose t0")
            .elapsed();
        assert!(
            elapsed < Duration::from_millis(5500),
            "ignored shutdown exceeded its one total budget: {:?}",
            elapsed
        );
        assert!(failure
            .diagnostic()
            .starts_with("REQUEST_DEADLINE_EXCEEDED:"));
        let diagnostic = failure.diagnostic().to_string();
        assert!(
            failure.into_recovery().is_none(),
            "ignored shutdown retained a recovery owner: {diagnostic}"
        );
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
        request_predecessor_hello(&mut host, "hello", Duration::from_secs(3))
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
            .shutdown()
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
        request_predecessor_hello(&mut host, "hello", Duration::from_secs(3))
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
            .shutdown()
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
    if layout.python_path_entries.is_empty()
        || layout.python_path_entries.iter().any(|path| {
            !path.is_absolute()
                || fs::canonicalize(path).ok().as_ref() != Some(path)
                || !path.is_file()
                || !path.starts_with(&layout.runtime_root)
        })
    {
        return Err("Core Host Python import artifacts are invalid".to_string());
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
