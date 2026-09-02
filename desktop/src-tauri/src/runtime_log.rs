use std::{
    collections::{BTreeMap, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    sync::{mpsc, Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::telemetry::TelemetryService;

pub const CORE_BRIDGE_PREFIX: &str = "SAKURA_RUNTIME_LOG_V1\t";
pub const PRODUCTION_QUEUE_CAPACITY: usize = 1024;
pub const PRODUCTION_MAX_RECORD_BYTES: usize = 4 * 1024;
pub const PRODUCTION_MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
pub const PRODUCTION_BACKUP_COUNT: usize = 5;
pub const PRODUCTION_FLUSH_INTERVAL: Duration = Duration::from_millis(250);
pub const PRODUCTION_SHUTDOWN_TIMEOUT: Duration = Duration::from_millis(500);
pub const RUNTIME_LOG_VIEWER_CAPACITY: usize = 400;

const LOG_LEVEL_ENV: &str = "SAKURA_RUNTIME_V2_LOG_LEVEL";

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Trace,
    Debug,
    Info,
    Warning,
    Error,
}

impl Severity {
    fn as_str(self) -> &'static str {
        match self {
            Self::Trace => "trace",
            Self::Debug => "debug",
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Error => "error",
        }
    }

    fn from_wire(value: &str) -> Option<Self> {
        match value {
            "trace" => Some(Self::Trace),
            "debug" => Some(Self::Debug),
            "info" => Some(Self::Info),
            "warn" | "warning" => Some(Self::Warning),
            "error" => Some(Self::Error),
            _ => None,
        }
    }

    fn is_priority(self) -> bool {
        matches!(self, Self::Warning | Self::Error)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Verbosity {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
}

impl Verbosity {
    fn as_str(self) -> &'static str {
        match self {
            Self::Trace => "trace",
            Self::Debug => "debug",
            Self::Info => "info",
            Self::Warn => "warn",
            Self::Error => "error",
        }
    }

    fn from_wire(value: &str) -> Option<Self> {
        match value {
            "trace" => Some(Self::Trace),
            "debug" => Some(Self::Debug),
            "info" => Some(Self::Info),
            "warn" | "warning" => Some(Self::Warn),
            "error" => Some(Self::Error),
            _ => None,
        }
    }

    fn permits(self, severity: Severity) -> bool {
        let threshold = match self {
            Self::Trace => Severity::Trace,
            Self::Debug => Severity::Debug,
            Self::Info => Severity::Info,
            Self::Warn => Severity::Warning,
            Self::Error => Severity::Error,
        };
        severity >= threshold
    }

    fn from_environment() -> Self {
        std::env::var(LOG_LEVEL_ENV)
            .ok()
            .as_deref()
            .and_then(Self::from_wire)
            .unwrap_or(Self::Info)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LogSource {
    Rust,
    Core,
    Webview,
}

impl LogSource {
    fn as_str(self) -> &'static str {
        match self {
            Self::Rust => "rust",
            Self::Core => "core",
            Self::Webview => "webview",
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct Correlation {
    pub generation_id: Option<String>,
    pub generation_number: Option<u64>,
    pub core_pid: Option<u32>,
    pub request_id: Option<String>,
    pub operation_id: Option<String>,
    pub action_id: Option<String>,
    pub trace_id: Option<String>,
}

#[derive(Clone, Debug)]
pub struct RuntimeLogEvent {
    source: LogSource,
    pid: u32,
    severity: Severity,
    verbosity: Verbosity,
    channel: String,
    event: String,
    message: String,
    correlation: Correlation,
    attributes: Option<Value>,
}

impl RuntimeLogEvent {
    pub fn rust(
        severity: Severity,
        channel: &'static str,
        event: &'static str,
        message: &'static str,
    ) -> Self {
        Self {
            source: LogSource::Rust,
            pid: std::process::id(),
            severity,
            verbosity: verbosity_for_severity(severity),
            channel: channel.to_string(),
            event: event.to_string(),
            message: message.to_string(),
            correlation: Correlation::default(),
            attributes: None,
        }
    }

    pub fn verbosity(mut self, verbosity: Verbosity) -> Self {
        self.verbosity = verbosity;
        self
    }

    pub fn correlation(mut self, correlation: Correlation) -> Self {
        self.correlation = correlation;
        self
    }

    pub fn attributes(mut self, attributes: Value) -> Self {
        self.attributes = Some(attributes);
        self
    }
}

#[derive(Clone, Debug)]
pub struct CoreLogContext {
    pub generation_id: String,
    pub generation_number: u64,
    pub core_pid: u32,
}

#[derive(Clone, Debug)]
pub struct RuntimeLogConfig {
    pub path: PathBuf,
    pub queue_capacity: usize,
    pub max_record_bytes: usize,
    pub max_file_bytes: u64,
    pub backup_count: usize,
    pub flush_interval: Duration,
    pub level: Verbosity,
}

impl RuntimeLogConfig {
    pub fn production(path: PathBuf) -> Self {
        Self {
            path,
            queue_capacity: PRODUCTION_QUEUE_CAPACITY,
            max_record_bytes: PRODUCTION_MAX_RECORD_BYTES,
            max_file_bytes: PRODUCTION_MAX_FILE_BYTES,
            backup_count: PRODUCTION_BACKUP_COUNT,
            flush_interval: PRODUCTION_FLUSH_INTERVAL,
            level: Verbosity::from_environment(),
        }
    }
}

#[derive(Clone)]
pub struct RuntimeLogService {
    inner: Arc<RuntimeLogInner>,
}

impl std::fmt::Debug for RuntimeLogService {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RuntimeLogService")
            .field("run_id", &self.inner.run_id)
            .field("path", &self.inner.config.path)
            .finish_non_exhaustive()
    }
}

struct RuntimeLogInner {
    config: RuntimeLogConfig,
    run_id: String,
    secrets: Vec<String>,
    state: Mutex<QueueState>,
    wake: Condvar,
    completion: Mutex<Option<mpsc::Receiver<()>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
    telemetry: Mutex<Option<TelemetryService>>,
}

#[derive(Debug)]
struct QueueState {
    records: VecDeque<PendingRecord>,
    viewer_records: VecDeque<RuntimeLogViewerRecord>,
    viewer_last_evicted_sequence: Option<u64>,
    next_sequence: u64,
    dropped: BTreeMap<String, u64>,
    stopping: bool,
    shutdown_deadline: Option<Instant>,
}

#[derive(Debug)]
struct PendingRecord {
    record: RuntimeLogRecord,
    severity: Severity,
}

#[derive(Debug, Serialize)]
struct RuntimeLogRecord {
    schema_version: u8,
    timestamp: String,
    run_id: String,
    sequence: u64,
    source: String,
    pid: u32,
    severity: String,
    verbosity: String,
    channel: String,
    event: String,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    generation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    generation_number: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    core_pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    operation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    action_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    trace_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    attributes: Option<Value>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogViewerDetail {
    pub label: String,
    pub value: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogViewerRecord {
    pub sequence: u64,
    pub timestamp: String,
    pub scopes: Vec<String>,
    pub severity: String,
    pub category: String,
    pub event_code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub details: Vec<RuntimeLogViewerDetail>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogViewerSnapshot {
    pub schema_version: u32,
    pub run_id: String,
    pub latest_sequence: u64,
    pub reset_required: bool,
    pub records: Vec<RuntimeLogViewerRecord>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreBridgeRecord {
    severity: String,
    verbosity: String,
    channel: String,
    event: String,
    message: String,
    #[serde(default)]
    request_id: Option<String>,
    #[serde(default)]
    operation_id: Option<String>,
    #[serde(default)]
    action_id: Option<String>,
    #[serde(default)]
    trace_id: Option<String>,
    #[serde(default)]
    attributes: Option<Value>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WebviewDiagnosticEntry {
    level: String,
    event: String,
    #[serde(default)]
    command: Option<String>,
    #[serde(default)]
    outcome: Option<String>,
    #[serde(default)]
    code: Option<String>,
    #[serde(default)]
    diagnostic: Option<String>,
    #[serde(default)]
    elapsed_ms: Option<f64>,
    #[serde(default)]
    operation_id: Option<String>,
    #[serde(default)]
    revision: Option<u64>,
}

impl RuntimeLogService {
    pub fn start(path: PathBuf) -> Self {
        Self::start_with_config(RuntimeLogConfig::production(path))
    }

    pub fn start_with_config(mut config: RuntimeLogConfig) -> Self {
        config.queue_capacity = config.queue_capacity.max(1);
        config.max_record_bytes = config.max_record_bytes.max(512);
        config.flush_interval = config.flush_interval.max(Duration::from_millis(1));
        let run_id = create_run_id();
        let (completion_sender, completion) = mpsc::sync_channel(1);
        let inner = Arc::new(RuntimeLogInner {
            config,
            run_id,
            secrets: environment_secrets(),
            state: Mutex::new(QueueState {
                records: VecDeque::new(),
                viewer_records: VecDeque::new(),
                viewer_last_evicted_sequence: None,
                next_sequence: 1,
                dropped: BTreeMap::new(),
                stopping: false,
                shutdown_deadline: None,
            }),
            wake: Condvar::new(),
            completion: Mutex::new(Some(completion)),
            worker: Mutex::new(None),
            telemetry: Mutex::new(None),
        });
        let worker_inner = Arc::clone(&inner);
        let worker = thread::Builder::new()
            .name("sakura-runtime-log-writer".to_string())
            .spawn(move || {
                run_writer(&worker_inner);
                let _ = completion_sender.send(());
            });
        match worker {
            Ok(worker) => {
                if let Ok(mut target) = inner.worker.lock() {
                    *target = Some(worker);
                }
            }
            Err(_) => {
                if let Ok(mut state) = inner.state.lock() {
                    state.stopping = true;
                }
                eprintln!("SAKURA_RUNTIME_LOG_WRITE_FAILED");
            }
        }
        Self { inner }
    }

    pub fn submit(&self, event: RuntimeLogEvent) -> bool {
        if !self.inner.config.level.permits(event.severity) {
            return true;
        }
        let normalized = self.normalize_event(event);
        if let Ok(telemetry) = self.inner.telemetry.lock() {
            if let Some(telemetry) = telemetry.as_ref() {
                telemetry.observe_runtime_event(
                    &normalized.record.source,
                    &normalized.record.severity,
                    &normalized.record.channel,
                    &normalized.record.event,
                    normalized.record.operation_id.as_deref(),
                    normalized.record.attributes.as_ref(),
                );
            }
        }
        let Ok(mut state) = self.inner.state.lock() else {
            return false;
        };
        if state.stopping {
            return false;
        }

        if !state.dropped.is_empty()
            && state.records.len().saturating_add(2) <= self.inner.config.queue_capacity
        {
            enqueue_drop_summary(&self.inner, &mut state);
        }

        if state.records.len() >= self.inner.config.queue_capacity {
            if normalized.severity.is_priority() {
                if let Some(index) = state
                    .records
                    .iter()
                    .position(|pending| !pending.severity.is_priority())
                {
                    if let Some(evicted) = state.records.remove(index) {
                        note_dropped(&mut state, &evicted.record.source, evicted.severity);
                    }
                } else {
                    note_dropped(&mut state, &normalized.record.source, normalized.severity);
                    self.inner.wake.notify_one();
                    return false;
                }
            } else {
                note_dropped(&mut state, &normalized.record.source, normalized.severity);
                self.inner.wake.notify_one();
                return false;
            }
        }

        let normalized = with_sequence(&mut state, normalized);
        append_viewer_record(&mut state, &normalized);
        state.records.push_back(normalized);
        self.inner.wake.notify_one();
        true
    }

    pub fn run_id(&self) -> &str {
        &self.inner.run_id
    }

    pub fn attach_telemetry(&self, telemetry: TelemetryService) {
        if let Ok(mut target) = self.inner.telemetry.lock() {
            *target = Some(telemetry);
        }
    }

    pub fn activate_telemetry_generation(&self, generation_id: &str) {
        if let Ok(telemetry) = self.inner.telemetry.lock() {
            if let Some(telemetry) = telemetry.as_ref() {
                telemetry.activate_generation(generation_id);
            }
        }
    }

    pub fn submit_core_telemetry_bridge(
        &self,
        line: &str,
        context: &CoreLogContext,
        forbidden_secret: Option<&str>,
    ) -> Result<bool, ()> {
        let telemetry = self.inner.telemetry.lock().map_err(|_| ())?;
        let Some(telemetry) = telemetry.as_ref() else {
            return Ok(false);
        };
        telemetry.submit_core_bridge(line, context, forbidden_secret)
    }

    pub fn viewer_snapshot(
        &self,
        after_sequence: Option<u64>,
    ) -> Result<RuntimeLogViewerSnapshot, &'static str> {
        let state = self
            .inner
            .state
            .lock()
            .map_err(|_| "RUNTIME_LOG_VIEWER_UNAVAILABLE")?;
        let cursor = after_sequence.unwrap_or_default();
        let reset_required = cursor > 0
            && state
                .viewer_last_evicted_sequence
                .is_some_and(|evicted| cursor < evicted);
        let records = state
            .viewer_records
            .iter()
            .filter(|record| reset_required || record.sequence > cursor)
            .cloned()
            .collect::<Vec<_>>();
        let latest_sequence = state
            .viewer_records
            .back()
            .map(|record| record.sequence)
            .or(state.viewer_last_evicted_sequence)
            .unwrap_or_default();
        Ok(RuntimeLogViewerSnapshot {
            schema_version: 2,
            run_id: self.inner.run_id.clone(),
            latest_sequence,
            reset_required,
            records,
        })
    }

    #[cfg(test)]
    pub fn submit_core_bridge(&self, line: &str, context: &CoreLogContext) -> Result<bool, ()> {
        self.submit_core_bridge_with_forbidden_secret(line, context, None)
    }

    pub fn submit_core_bridge_with_forbidden_secret(
        &self,
        line: &str,
        context: &CoreLogContext,
        forbidden_secret: Option<&str>,
    ) -> Result<bool, ()> {
        if line.len() > PRODUCTION_MAX_RECORD_BYTES {
            return Err(());
        }
        if forbidden_secret.is_some_and(|secret| !secret.is_empty() && line.contains(secret)) {
            return Err(());
        }
        let parsed: CoreBridgeRecord = serde_json::from_str(line).map_err(|_| ())?;
        let severity = Severity::from_wire(&parsed.severity).ok_or(())?;
        let verbosity = Verbosity::from_wire(&parsed.verbosity).ok_or(())?;
        if normalize_token(&parsed.channel, 64).is_none()
            || normalize_token(&parsed.event, 96).is_none()
            || parsed.message.len() > 192
            || contains_secret(&parsed.channel, &self.inner.secrets, forbidden_secret)
            || contains_secret(&parsed.event, &self.inner.secrets, forbidden_secret)
        {
            return Err(());
        }
        let event_name = parsed.event.clone();
        let event = RuntimeLogEvent {
            source: LogSource::Core,
            pid: context.core_pid,
            severity,
            verbosity,
            channel: parsed.channel,
            event: event_name.clone(),
            message: core_message(&event_name).to_string(),
            correlation: Correlation {
                generation_id: Some(context.generation_id.clone()),
                generation_number: Some(context.generation_number),
                core_pid: Some(context.core_pid),
                request_id: sanitize_untrusted_id(
                    parsed.request_id,
                    &self.inner.secrets,
                    forbidden_secret,
                ),
                operation_id: sanitize_untrusted_id(
                    parsed.operation_id,
                    &self.inner.secrets,
                    forbidden_secret,
                ),
                action_id: sanitize_untrusted_id(
                    parsed.action_id,
                    &self.inner.secrets,
                    forbidden_secret,
                ),
                trace_id: sanitize_untrusted_id(
                    parsed.trace_id,
                    &self.inner.secrets,
                    forbidden_secret,
                ),
            },
            attributes: parsed.attributes,
        };
        Ok(self.submit(event))
    }

    pub fn prepare_webview(
        &self,
        window_label: &str,
        entry: WebviewDiagnosticEntry,
    ) -> Result<RuntimeLogEvent, &'static str> {
        if !matches!(window_label, "main" | "settings") {
            return Err("RUNTIME_DIAGNOSTIC_WINDOW_INVALID");
        }
        let submitted_severity = match entry.level.as_str() {
            "trace" => Severity::Trace,
            "debug" => Severity::Debug,
            "info" => Severity::Info,
            "warn" | "warning" => Severity::Warning,
            "error" => Severity::Error,
            _ => return Err("RUNTIME_DIAGNOSTIC_LEVEL_INVALID"),
        };
        if !allowed_webview_event(&entry.event) {
            return Err("RUNTIME_DIAGNOSTIC_EVENT_INVALID");
        }
        let severity = if matches!(
            entry.event.as_str(),
            "webview.command.started" | "webview.command.completed"
        ) {
            Severity::Debug
        } else {
            submitted_severity
        };
        if entry.command.as_deref().is_some_and(|value| {
            normalize_token(value, 96).is_none() || value == "record_runtime_diagnostics"
        }) || entry
            .outcome
            .as_deref()
            .is_some_and(|value| !matches!(value, "started" | "completed" | "failed" | "cancelled"))
            || entry.code.as_deref().is_some_and(|value| {
                value.len() > 64
                    || value.is_empty()
                    || !value.bytes().all(|byte| {
                        byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_'
                    })
            })
            || entry
                .elapsed_ms
                .is_some_and(|value| !value.is_finite() || !(0.0..=3_600_000.0).contains(&value))
            || entry
                .operation_id
                .as_deref()
                .is_some_and(|value| valid_id(value, 128).is_none())
            || entry.diagnostic.as_deref().is_some_and(|value| {
                value.is_empty()
                    || value.chars().count() > 240
                    || value.chars().any(char::is_control)
                    || value.contains("://")
                    || looks_secret_shaped(value)
                    || self
                        .inner
                        .secrets
                        .iter()
                        .any(|secret| value.contains(secret))
            })
        {
            return Err("RUNTIME_DIAGNOSTIC_FIELDS_INVALID");
        }
        let mut attributes = Map::new();
        attributes.insert(
            "window_label".to_string(),
            Value::String(window_label.to_string()),
        );
        if let Some(command) = entry.command {
            attributes.insert("command".to_string(), Value::String(command));
        }
        if let Some(outcome) = entry.outcome {
            attributes.insert("outcome".to_string(), Value::String(outcome));
        }
        if let Some(code) = entry.code {
            attributes.insert("code".to_string(), Value::String(code));
        }
        if let Some(diagnostic) = entry.diagnostic {
            attributes.insert("diagnostic".to_string(), Value::String(diagnostic));
        }
        if let Some(elapsed_ms) = entry.elapsed_ms {
            if let Some(value) = serde_json::Number::from_f64(elapsed_ms) {
                attributes.insert("elapsed_ms".to_string(), Value::Number(value));
            }
        }
        if let Some(revision) = entry.revision {
            attributes.insert("revision".to_string(), Value::from(revision));
        }
        let event_name = entry.event.clone();
        Ok(RuntimeLogEvent {
            source: LogSource::Webview,
            pid: std::process::id(),
            severity,
            verbosity: verbosity_for_severity(severity),
            channel: format!("webview.{window_label}"),
            event: event_name.clone(),
            message: webview_message(&event_name).to_string(),
            correlation: Correlation {
                operation_id: entry.operation_id,
                ..Correlation::default()
            },
            attributes: Some(Value::Object(attributes)),
        })
    }

    pub fn shutdown(&self, timeout: Duration) -> bool {
        let timeout = timeout.min(PRODUCTION_SHUTDOWN_TIMEOUT);
        if let Ok(mut state) = self.inner.state.lock() {
            if !state.stopping {
                state.stopping = true;
                state.shutdown_deadline = Some(Instant::now() + timeout);
            }
            self.inner.wake.notify_all();
        } else {
            return false;
        }

        let completion = self
            .inner
            .completion
            .lock()
            .ok()
            .and_then(|mut completion| completion.take());
        let completed =
            completion.is_some_and(|completion| completion.recv_timeout(timeout).is_ok());
        if completed {
            if let Ok(mut worker) = self.inner.worker.lock() {
                if let Some(worker) = worker.take() {
                    let _ = worker.join();
                }
            }
        }
        completed
    }

    fn normalize_event(&self, event: RuntimeLogEvent) -> PendingRecord {
        let channel = normalize_token(&event.channel, 64).unwrap_or_else(|| "runtime".to_string());
        let event_name = normalize_token(&event.event, 96)
            .unwrap_or_else(|| "runtime.event.invalid".to_string());
        let severity = if event_name == "core.lifecycle.stopped" {
            Severity::Debug
        } else {
            event.severity
        };
        let message = sanitize_fixed_message(&event.message);
        let correlation = sanitize_correlation(event.correlation, &self.inner.secrets);
        PendingRecord {
            severity,
            record: RuntimeLogRecord {
                schema_version: 1,
                timestamp: local_clock_timestamp(),
                run_id: self.inner.run_id.clone(),
                sequence: 0,
                source: event.source.as_str().to_string(),
                pid: event.pid,
                severity: severity.as_str().to_string(),
                verbosity: verbosity_for_severity(severity).as_str().to_string(),
                channel,
                event: event_name,
                message,
                generation_id: correlation.generation_id,
                generation_number: correlation.generation_number,
                core_pid: correlation.core_pid,
                request_id: correlation.request_id,
                operation_id: correlation.operation_id,
                action_id: correlation.action_id,
                trace_id: correlation.trace_id,
                attributes: event
                    .attributes
                    .as_ref()
                    .and_then(|value| sanitize_attributes(value, &self.inner.secrets)),
            },
        }
    }
}

fn with_sequence(state: &mut QueueState, mut pending: PendingRecord) -> PendingRecord {
    pending.record.sequence = state.next_sequence;
    state.next_sequence = state.next_sequence.saturating_add(1);
    pending
}

fn enqueue_drop_summary(inner: &RuntimeLogInner, state: &mut QueueState) {
    if state.dropped.is_empty() {
        return;
    }
    let counts = std::mem::take(&mut state.dropped);
    let total = counts.values().copied().sum::<u64>();
    let pending = PendingRecord {
        severity: Severity::Warning,
        record: RuntimeLogRecord {
            schema_version: 1,
            timestamp: local_clock_timestamp(),
            run_id: inner.run_id.clone(),
            sequence: 0,
            source: "rust".to_string(),
            pid: std::process::id(),
            severity: "warning".to_string(),
            verbosity: "warn".to_string(),
            channel: "runtime.log".to_string(),
            event: "runtime.log.records_dropped".to_string(),
            message: "运行日志拥塞，部分记录已丢弃".to_string(),
            generation_id: None,
            generation_number: None,
            core_pid: None,
            request_id: None,
            operation_id: None,
            action_id: None,
            trace_id: None,
            attributes: Some(json!({"dropped_count": total, "counts": counts})),
        },
    };
    let pending = with_sequence(state, pending);
    append_viewer_record(state, &pending);
    state.records.push_back(pending);
}

fn append_viewer_record(state: &mut QueueState, pending: &PendingRecord) {
    let Some(record) = project_viewer_record(&pending.record, pending.severity) else {
        return;
    };
    state.viewer_records.push_back(record);
    while state.viewer_records.len() > RUNTIME_LOG_VIEWER_CAPACITY {
        if let Some(evicted) = state.viewer_records.pop_front() {
            state.viewer_last_evicted_sequence = Some(evicted.sequence);
        }
    }
}

fn note_dropped(state: &mut QueueState, source: &str, severity: Severity) {
    let key = format!("{source}.{}", severity.as_str());
    *state.dropped.entry(key).or_default() = state
        .dropped
        .get(&key)
        .copied()
        .unwrap_or_default()
        .saturating_add(1);
}

fn run_writer(inner: &RuntimeLogInner) {
    let mut writer = FileWriter::new(&inner.config);
    let mut last_flush = Instant::now();
    loop {
        let (pending, flush_only, stop) = {
            let Ok(state) = inner.state.lock() else {
                break;
            };
            let mut state = state;
            loop {
                if state.stopping
                    && state
                        .shutdown_deadline
                        .is_some_and(|deadline| Instant::now() >= deadline)
                {
                    state.records.clear();
                    state.dropped.clear();
                    break (None, false, true);
                }
                if state.records.is_empty() && !state.dropped.is_empty() {
                    enqueue_drop_summary(inner, &mut state);
                }
                if let Some(pending) = state.records.pop_front() {
                    break (Some(pending), false, false);
                }
                if state.stopping {
                    break (None, false, true);
                }
                let waited = inner.wake.wait_timeout(state, inner.config.flush_interval);
                let Ok((next, outcome)) = waited else {
                    break (None, false, true);
                };
                state = next;
                if outcome.timed_out() {
                    break (None, true, false);
                }
            }
        };

        if flush_only {
            let _ = writer.flush();
            last_flush = Instant::now();
            continue;
        }
        if stop {
            break;
        }
        let Some(pending) = pending else {
            continue;
        };
        let priority = pending.severity.is_priority();
        let _ = writer.write_record(&pending.record);
        if priority || last_flush.elapsed() >= inner.config.flush_interval {
            let _ = writer.flush();
            last_flush = Instant::now();
        }
    }
    let _ = writer.flush();
}

struct FileWriter {
    path: PathBuf,
    handle: Option<BufWriter<File>>,
    current_bytes: u64,
    max_record_bytes: usize,
    max_file_bytes: u64,
    backup_count: usize,
    failed: bool,
    warned: bool,
}

impl FileWriter {
    fn new(config: &RuntimeLogConfig) -> Self {
        Self {
            path: config.path.clone(),
            handle: None,
            current_bytes: 0,
            max_record_bytes: config.max_record_bytes,
            max_file_bytes: config.max_file_bytes,
            backup_count: config.backup_count,
            failed: false,
            warned: false,
        }
    }

    fn write_record(&mut self, record: &RuntimeLogRecord) -> Result<(), ()> {
        if self.failed {
            return Err(());
        }
        let line = encode_record(record, self.max_record_bytes).ok_or(())?;
        if self.ensure_open().is_err()
            || (self.max_file_bytes > 0
                && self.current_bytes.saturating_add(line.len() as u64) > self.max_file_bytes
                && self.rotate().is_err())
            || self.handle.as_mut().ok_or(())?.write_all(&line).is_err()
        {
            self.fail_once();
            return Err(());
        }
        self.current_bytes = self.current_bytes.saturating_add(line.len() as u64);
        Ok(())
    }

    fn ensure_open(&mut self) -> Result<(), ()> {
        if self.handle.is_some() {
            return Ok(());
        }
        let parent = self.path.parent().ok_or(())?;
        fs::create_dir_all(parent).map_err(|_| ())?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|_| ())?;
        self.current_bytes = file.metadata().map(|metadata| metadata.len()).unwrap_or(0);
        self.handle = Some(BufWriter::new(file));
        Ok(())
    }

    fn rotate(&mut self) -> Result<(), ()> {
        if let Some(mut handle) = self.handle.take() {
            handle.flush().map_err(|_| ())?;
        }
        if self.backup_count == 0 {
            File::create(&self.path).map_err(|_| ())?;
        } else {
            for index in (1..self.backup_count).rev() {
                let source = backup_path(&self.path, index);
                if !source.exists() {
                    continue;
                }
                let target = backup_path(&self.path, index + 1);
                if target.exists() {
                    fs::remove_file(&target).map_err(|_| ())?;
                }
                fs::rename(source, target).map_err(|_| ())?;
            }
            let first = backup_path(&self.path, 1);
            if first.exists() {
                fs::remove_file(&first).map_err(|_| ())?;
            }
            if self.path.exists() {
                fs::rename(&self.path, first).map_err(|_| ())?;
            }
        }
        self.current_bytes = 0;
        self.ensure_open()
    }

    fn flush(&mut self) -> Result<(), ()> {
        if self.failed {
            return Err(());
        }
        if let Some(handle) = self.handle.as_mut() {
            if handle.flush().is_err() {
                self.fail_once();
                return Err(());
            }
        }
        Ok(())
    }

    fn fail_once(&mut self) {
        self.failed = true;
        self.handle = None;
        if !self.warned {
            self.warned = true;
            eprintln!("SAKURA_RUNTIME_LOG_WRITE_FAILED");
        }
    }
}

fn encode_record(record: &RuntimeLogRecord, max_bytes: usize) -> Option<Vec<u8>> {
    let channel = display_channel(&record.channel, &record.event);
    let message = human_message(&record.event, &record.message);
    let mut summary_parts = correlation_summary(record);
    let attribute_summary = format_human_summary(&record.event, record.attributes.as_ref());
    if !attribute_summary.is_empty() {
        summary_parts.push(attribute_summary);
    }
    let summary = summary_parts.join(" ");
    let mut text = format!("[{}] [{channel}] {message}", record.timestamp);
    if !summary.is_empty() {
        text.push_str(" │ ");
        text.push_str(&summary);
    }
    text.push('\n');
    Some(truncate_utf8_line(text, max_bytes.max(64)))
}

fn project_viewer_record(
    record: &RuntimeLogRecord,
    severity: Severity,
) -> Option<RuntimeLogViewerRecord> {
    if !viewer_event_is_visible(&record.event, severity) {
        return None;
    }
    let is_tts = record.event.starts_with("tts.")
        || record
            .channel
            .split('.')
            .next()
            .is_some_and(|channel| channel.eq_ignore_ascii_case("tts"));
    let scopes = if record.event.starts_with("tts.service.") {
        vec!["tts".to_string()]
    } else if is_tts {
        vec!["software".to_string(), "tts".to_string()]
    } else {
        vec!["software".to_string()]
    };
    Some(RuntimeLogViewerRecord {
        sequence: record.sequence,
        timestamp: record.timestamp.clone(),
        scopes,
        severity: severity.as_str().to_string(),
        category: display_channel(&record.channel, &record.event),
        event_code: record.event.clone(),
        message: viewer_record_message(record, severity),
        description: viewer_problem_description(record, severity).map(str::to_string),
        details: viewer_details(record),
        correlation_id: viewer_correlation(record),
    })
}

fn viewer_event_is_visible(event: &str, severity: Severity) -> bool {
    if event == "tts.service.warmup_queued" && severity == Severity::Info {
        return false;
    }
    if severity.is_priority() {
        return true;
    }
    severity == Severity::Info && business_message(event).is_some()
}

fn viewer_record_message(record: &RuntimeLogRecord, severity: Severity) -> String {
    if let Some(message) = viewer_ipc_request_message(record) {
        return message;
    }
    viewer_record_default_message(record, severity).to_string()
}

fn viewer_record_default_message(record: &RuntimeLogRecord, severity: Severity) -> &'static str {
    if viewer_is_gpt_sovits(record) {
        match record.event.as_str() {
            "tts.service.started" => return "正在启动 GPT-SoVITS 服务",
            "tts.service.waiting_ready" => return "GPT-SoVITS 进程已启动，正在等待服务就绪",
            "tts.service.ready" => return "GPT-SoVITS 服务已就绪",
            "tts.service.failed" | "tts.service.warmup_failed" => return "GPT-SoVITS 服务启动失败",
            "tts.weights.loading" => return "正在加载角色语音模型",
            "tts.weights.ready" => return "角色语音模型已就绪",
            "tts.weights.failed" => return "角色语音模型加载失败",
            _ => {}
        }
    }
    if viewer_has_code(record, &["TTS_DEVICE_PROBE_FAILED"]) {
        return "语音服务启动失败";
    }
    match record.event.as_str() {
        "appearance.input_visual_effect.degraded" => "输入栏视觉效果已降级",
        "appearance.input_visual_effect.limited" => "输入栏视觉效果受限",
        "core.spawn.failed" | "first_run.core_start.failed" => "后台程序启动失败",
        "core.error.unhandled" | "shell.error.unhandled" => "后台程序发生错误",
        "ipc.request.failed" => "后台请求失败",
        "mcp.server.failed" => "工具服务连接失败",
        "mcp.config.failed" => "工具配置读取失败",
        "mcp.tool.failed" => "工具调用失败",
        "tts.service.failed" | "tts.service.warmup_failed" => "语音服务启动失败",
        "tts.weights.failed" => "角色语音模型加载失败",
        "tts.service.probe.failed" => "语音服务尚未就绪",
        _ => viewer_message(&record.event, severity),
    }
}

fn viewer_ipc_request_message(record: &RuntimeLogRecord) -> Option<String> {
    let suffix = match record.event.as_str() {
        "ipc.request.started" => "中",
        "ipc.request.completed" => "完成",
        "ipc.request.cancelled" => "已取消",
        "ipc.request.failed" => "失败",
        _ => return None,
    };
    let command = viewer_attribute_strings(record, &["command"]).next()?;
    let action = match command {
        "system.hello" => "连接后台程序",
        "system.health" => "检查后台程序状态",
        "system.shutdown" => "停止后台程序",
        "core.initialize" => "初始化后台程序",
        "core.snapshot" => "读取运行状态",
        "chat.send" => "发送对话",
        "chat.cancel" => "取消对话",
        "settings.provider_model.get" => "读取模型设置",
        "settings.provider_model.save" => "保存模型设置",
        "settings.provider_model.list_models" => "获取模型列表",
        "settings.provider_model.test_connection" => "测试模型连接",
        "settings.provider_model.cancel" => "取消模型测试",
        "tools.settings.get" => "读取工具设置",
        "tools.settings.save" => "保存工具设置",
        "mcp.status.get" => "读取工具服务状态",
        "plugins.settings.get" => "读取插件设置",
        "plugins.settings.save" => "保存插件设置",
        "plugins.enabled.set" => "更改插件开关",
        "plugins.settings.action" => "执行插件操作",
        "plugins.install" => "安装插件",
        "plugins.uninstall" => "卸载插件",
        "plugins.collection.query" => "读取插件数据",
        "plugins.collection.create" => "新增插件数据",
        "plugins.collection.update" => "更新插件数据",
        "plugins.collection.delete" => "删除插件数据",
        "ui.composer_tools.get" => "读取输入栏工具",
        "ui.composer_tools.invoke" => "运行输入栏工具",
        "tts.synthesis.start" => "提交语音生成",
        "tts.synthesis.cancel" => "取消语音生成",
        "tts.settings.get" => "读取语音设置",
        "tts.settings.save" => "保存语音设置",
        "tts.status.get" => "读取语音状态",
        "tts.playback.observe" => "更新语音播放状态",
        "screen_awareness.settings.get" => "读取屏幕感知设置",
        "screen_awareness.settings.save" => "保存屏幕感知设置",
        "characters.settings.get" => "读取角色设置",
        "characters.settings.import" => "导入角色",
        "characters.settings.select" => "切换角色",
        "storage.settings.get" => "读取存储设置",
        "storage.settings.choose_tts_root" => "更改语音数据目录",
        "storage.settings.reset_tts_root" => "恢复默认语音目录",
        "ui.history.page" => "读取对话记录",
        _ => return None,
    };
    Some(if record.event == "ipc.request.started" {
        format!("正在{action}")
    } else {
        format!("{action}{suffix}")
    })
}

fn viewer_is_gpt_sovits(record: &RuntimeLogRecord) -> bool {
    viewer_attribute_strings(record, &["provider"])
        .any(|value| value.eq_ignore_ascii_case("sakura.tts.gpt-sovits"))
}

fn viewer_problem_description(
    record: &RuntimeLogRecord,
    severity: Severity,
) -> Option<&'static str> {
    if !severity.is_priority() {
        return None;
    }

    let event = record.event.as_str();

    if viewer_has_code(
        record,
        &[
            "AUTHENTICATION_FAILED",
            "CREDENTIAL_REQUIRED",
            "invalid_api_key",
        ],
    ) {
        return Some("模型服务没有接受当前凭据，这次回复无法生成。");
    }
    if viewer_has_code(
        record,
        &["INSUFFICIENT_QUOTA", "QUOTA_EXCEEDED", "insufficient_quota"],
    ) {
        return Some("模型服务暂时没有接受这次请求，回复未能生成。");
    }
    if viewer_has_code(record, &["MODEL_NOT_FOUND", "model_not_found"]) {
        return Some("模型服务找不到当前模型，这次回复无法生成。");
    }
    if event.starts_with("api.")
        && viewer_has_code(
            record,
            &[
                "NETWORK_UNAVAILABLE",
                "CONNECTION_INTERRUPTED",
                "PROVIDER_REQUEST_FAILED",
                "REQUEST_TIMEOUT",
            ],
        )
    {
        return Some("Sakura 没有收到模型服务的响应，这次回复未能生成。");
    }

    if viewer_has_code(record, &["TTS_DEVICE_PROBE_FAILED"]) {
        return Some("语音服务启动时没能确认可用设备，暂时不能生成语音。");
    }
    if viewer_has_code(record, &["TTS_RUNTIME_PYTHON_MISSING"]) {
        return Some("语音运行环境不完整，暂时不能生成语音。");
    }
    if viewer_has_code(record, &["TTS_ACCELERATOR_UNAVAILABLE"]) {
        return Some("没有检测到语音服务需要的运行设备，暂时不能生成语音。");
    }
    if viewer_has_code(record, &["TTS_RUNTIME_TIMEOUT"]) {
        return Some("等待 GPT-SoVITS 服务响应超时，语音暂时不可用。");
    }
    if viewer_has_code(record, &["TTS_RUNTIME_EXITED"]) {
        return Some("GPT-SoVITS 进程在启动期间提前退出，语音暂时不可用。");
    }
    if viewer_has_code(record, &["TTS_RUNTIME_INVALID", "TTS_RUNTIME_START_FAILED"]) {
        return Some("GPT-SoVITS 运行环境不完整或无法启动，语音暂时不可用。");
    }
    if viewer_has_code(
        record,
        &["TTS_PORT_OCCUPIED", "TTS_PORT_OCCUPIED_BY_OTHER_PROCESS"],
    ) {
        return Some("GPT-SoVITS 使用的端口已被占用，服务没有启动。");
    }
    if viewer_has_code(record, &["TTS_WEIGHTS_UNAVAILABLE"]) {
        if viewer_has_stage(record, "gpt_weights") {
            return Some("GPT 角色语音权重加载失败，文字回复仍可使用。");
        }
        if viewer_has_stage(record, "sovits_weights") {
            return Some("SoVITS 角色语音权重加载失败，文字回复仍可使用。");
        }
        return Some("角色语音模型加载失败，文字回复仍可使用。");
    }
    if viewer_has_code(
        record,
        &[
            "TTS_CONNECTION_FAILED",
            "TTS_REQUEST_TIMEOUT",
            "TTS_PROBE_TIMEOUT",
            "TTS_PROBE_UNAVAILABLE",
        ],
    ) {
        return Some("Sakura 没有收到语音服务的响应，这次语音没有生成。");
    }
    if viewer_has_code(record, &["TTS_PORT_OCCUPIED_BY_OTHER_PROCESS"]) {
        return Some("语音服务使用的端口已被其他程序占用，语音服务没有启动。");
    }

    if viewer_has_code(record, &["WINDOWS_ADVANCED_EFFECTS_DISABLED"]) {
        return Some("Windows 已关闭高级视觉效果，输入栏会改用普通背景。这不影响聊天和输入。");
    }
    if viewer_has_code(record, &["WINDOWS_ENERGY_SAVER_ACTIVE"]) {
        return Some("Windows 正在使用节能模式，输入栏会暂时改用普通背景。这不影响聊天和输入。");
    }
    if viewer_has_code(record, &["WINDOWS_HOST_BACKDROP_REQUIRES_BUILD_22000"]) {
        return Some("当前 Windows 版本不支持这项视觉效果，输入栏会使用普通背景。");
    }

    if event.starts_with("mcp.")
        && viewer_has_code(
            record,
            &["CONFIG_INVALID", "CONFIG_MISSING", "MCP_CONFIG_LOAD_FAILED"],
        )
    {
        return Some("工具配置无法读取，相关工具没有加载。");
    }
    if event.starts_with("mcp.") && viewer_has_code(record, &["NO_READY_SERVERS"]) {
        return Some("没有可用的 MCP 服务，相关工具没有加载。");
    }
    if (event.starts_with("mcp.") || event.starts_with("plugin."))
        && viewer_has_code(
            record,
            &[
                "CLOSE_TIMEOUT",
                "PLUGIN_CALL_TIMEOUT",
                "REGISTRATION_TIMEOUT",
            ],
        )
    {
        return Some("工具服务没有及时回应，本次操作没有完成。");
    }
    if viewer_has_code(
        record,
        &[
            "PLUGIN_DISABLED",
            "PLUGIN_PROCESS_EXITED",
            "API_VERSION_UNSUPPORTED",
            "DEPENDENCY_CYCLE",
            "SERVICE_CONFLICT",
            "MISSING_SERVICE",
        ],
    ) {
        return Some("插件没有正常运行，依赖它的功能暂时不可用。");
    }

    if event == "api.request.failed" {
        if matches!(viewer_http_status(record), Some(401 | 403)) {
            return Some("模型服务没有接受当前凭据，这次回复无法生成。");
        }
        if matches!(viewer_http_status(record), Some(404)) {
            return Some("模型服务找不到当前模型，这次回复无法生成。");
        }
        if matches!(viewer_http_status(record), Some(408 | 429 | 500..=599))
            || viewer_has_error_type(record, &["TimeoutError", "RemoteDisconnected"])
        {
            return Some("Sakura 没有收到模型服务的正常响应，这次回复未能生成。");
        }
    }

    let description = match event {
        "core.spawn.failed" | "first_run.core_start.failed" => {
            "Sakura 的后台程序没有启动，聊天和部分功能暂时不可用。"
        }
        "shell.error.unhandled" | "core.error.unhandled" | "ipc.request.failed" => {
            "Sakura 的后台功能遇到问题，相关操作可能无法完成。"
        }
        "chat.request.failed"
        | "api.request.failed"
        | "reply.processing.failed"
        | "reply.display.failed" => "这次回复没有正常完成。",
        "memory.recall.failed" | "memory.recall.unavailable" => {
            "这轮对话没有读到长期记忆，但仍会继续生成回复。"
        }
        "memory.curation.failed" | "memory.curation.request_fuse_opened" => {
            "后台记忆整理没有完成，不影响当前对话。"
        }
        "context.dependencies.degraded" => "这次对话没有使用到全部记忆或辅助信息。",
        "screen.capture.failed" => "这次请求没有附带屏幕画面，文字内容仍会正常发送。",
        "updater.signature.failed" => "更新包没有通过安全校验，本次更新已经停止。",
        value if value.starts_with("updater.") => "本次更新没有完成，当前版本仍可继续使用。",
        value if value.starts_with("legacy_import.") => "旧版本数据没有全部迁移完成。",
        value if value.starts_with("tts.") => "语音功能没有正常完成，文字回复仍可使用。",
        value if value.starts_with("appearance.") || value.starts_with("ui.") => {
            "界面效果已改用兼容模式，不影响聊天和输入。"
        }
        value
            if value.starts_with("tool.")
                || value.starts_with("mcp.")
                || value.starts_with("plugin.") =>
        {
            "相关工具没有正常完成，本次操作可能缺少对应结果。"
        }
        value if value.starts_with("memory.") || value.starts_with("context.") => {
            "这次对话没有使用到全部记忆或辅助信息。"
        }
        value if value.starts_with("screen.") => "这次请求没有附带屏幕画面。",
        value
            if value.starts_with("settings.")
                || value.starts_with("config.")
                || value.starts_with("storage.") =>
        {
            "相关设置或数据操作没有完成。"
        }
        value if value.starts_with("core.") || value.starts_with("ipc.") => {
            "Sakura 的后台功能遇到问题，相关操作可能无法完成。"
        }
        _ if severity == Severity::Warning => "这项功能没有按预期工作，Sakura 仍在运行。",
        _ => "这项操作没有正常完成。",
    };
    Some(description)
}

fn viewer_has_code(record: &RuntimeLogRecord, candidates: &[&str]) -> bool {
    viewer_attribute_strings(record, &["reason_code", "provider_error_code", "code"]).any(|value| {
        candidates
            .iter()
            .any(|candidate| value.eq_ignore_ascii_case(candidate))
    })
}

fn viewer_has_error_type(record: &RuntimeLogRecord, candidates: &[&str]) -> bool {
    viewer_attribute_strings(record, &["error_type", "provider_error_type", "cause_type"]).any(
        |value| {
            candidates
                .iter()
                .any(|candidate| value.eq_ignore_ascii_case(candidate))
        },
    )
}

fn viewer_has_stage(record: &RuntimeLogRecord, candidate: &str) -> bool {
    viewer_attribute_strings(record, &["stage"]).any(|value| value.eq_ignore_ascii_case(candidate))
}

fn viewer_attribute_strings<'a>(
    record: &'a RuntimeLogRecord,
    keys: &'a [&str],
) -> impl Iterator<Item = &'a str> {
    record
        .attributes
        .as_ref()
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|attributes| attributes.iter())
        .filter(move |(key, _)| keys.contains(&normalize_key(key).as_str()))
        .filter_map(|(_, value)| value.as_str())
}

fn viewer_http_status(record: &RuntimeLogRecord) -> Option<u16> {
    let attributes = record.attributes.as_ref()?.as_object()?;
    let (_, value) = attributes
        .iter()
        .find(|(key, _)| matches!(normalize_key(key).as_str(), "status" | "http_status"))?;
    value
        .as_u64()
        .and_then(|status| u16::try_from(status).ok())
        .or_else(|| value.as_str()?.parse::<u16>().ok())
}

fn viewer_details(record: &RuntimeLogRecord) -> Vec<RuntimeLogViewerDetail> {
    const PRIORITY: [&str; 52] = [
        "diagnostic",
        "code",
        "provider_error_code",
        "reason_code",
        "stage",
        "detail_stage",
        "copy_method",
        "return_code",
        "source_files",
        "source_bytes",
        "expected_files",
        "expected_bytes",
        "actual_files",
        "actual_bytes",
        "error_type",
        "provider_error_type",
        "cause_type",
        "exception_site",
        "failure_id",
        "command",
        "status",
        "http_status",
        "outcome",
        "elapsed_ms",
        "duration_ms",
        "trigger",
        "mode",
        "current_version",
        "version",
        "proxy_mode",
        "proxy_http_configured",
        "proxy_https_configured",
        "proxy_all_configured",
        "proxy_no_proxy_configured",
        "dependency",
        "tool_name",
        "provider",
        "model",
        "retryable",
        "attempt",
        "progress",
        "count",
        "dropped_count",
        "bytes",
        "lines",
        "items",
        "listed",
        "registered",
        "segment_count",
        "text_chars",
        "reply_chars",
        "resolution",
    ];
    let Some(attributes) = record.attributes.as_ref().and_then(Value::as_object) else {
        return Vec::new();
    };
    let mut details = Vec::new();
    let mut labels = Vec::new();
    for wanted in PRIORITY {
        let Some((_, value)) = attributes
            .iter()
            .find(|(key, value)| normalize_key(key) == wanted && is_human_scalar(value))
        else {
            continue;
        };
        let rendered = viewer_render_detail(record, wanted, value);
        if rendered.is_empty() || rendered == "null" {
            continue;
        }
        let label = viewer_detail_label(wanted);
        if labels.contains(&label) {
            continue;
        }
        labels.push(label);
        details.push(RuntimeLogViewerDetail {
            label: label.to_string(),
            value: if wanted.ends_with("_ms") && viewer_is_gpt_lifecycle(record) {
                rendered
            } else if wanted.ends_with("_ms") {
                format!("{rendered} ms")
            } else if wanted == "bytes" || wanted.ends_with("_bytes") {
                value.as_u64().map(format_bytes).unwrap_or(rendered)
            } else if wanted == "retryable" {
                match value.as_bool() {
                    Some(true) => "是".to_string(),
                    Some(false) => "否".to_string(),
                    None => rendered,
                }
            } else {
                rendered
            },
        });
        if details.len() >= 12 {
            break;
        }
    }
    details
}

fn viewer_is_gpt_lifecycle(record: &RuntimeLogRecord) -> bool {
    viewer_is_gpt_sovits(record)
        && matches!(
            record.event.as_str(),
            "tts.service.started"
                | "tts.service.waiting_ready"
                | "tts.service.ready"
                | "tts.service.failed"
                | "tts.weights.loading"
                | "tts.weights.ready"
                | "tts.weights.failed"
        )
}

fn viewer_render_detail(record: &RuntimeLogRecord, key: &str, value: &Value) -> String {
    let rendered = render_human_scalar(key, value);
    if !viewer_is_gpt_lifecycle(record) {
        return rendered;
    }
    match (key, rendered.as_str()) {
        ("provider", "sakura.tts.gpt-sovits") => "GPT-SoVITS".to_string(),
        ("stage", "runtime_start") => "启动服务".to_string(),
        ("stage", "weights") => "加载角色语音模型".to_string(),
        ("stage", "gpt_weights") => "GPT 权重".to_string(),
        ("stage", "sovits_weights") => "SoVITS 权重".to_string(),
        ("status", "starting") => "正在启动".to_string(),
        ("status", "waiting") => "等待就绪".to_string(),
        ("status", "ready") => "已就绪".to_string(),
        ("status", "loading") => "正在加载".to_string(),
        ("status", "failed") => "失败".to_string(),
        ("elapsed_ms" | "duration_ms", raw) => raw
            .parse::<f64>()
            .ok()
            .filter(|elapsed| elapsed.is_finite() && *elapsed >= 0.0)
            .map(|elapsed| format!("{:.1} 秒", elapsed / 1000.0))
            .unwrap_or(rendered),
        _ => rendered,
    }
}

fn viewer_detail_label(key: &str) -> &'static str {
    match key {
        "diagnostic" => "诊断",
        "code" | "provider_error_code" => "错误码",
        "reason_code" => "原因码",
        "stage" => "阶段",
        "detail_stage" => "阶段",
        "error_type" | "provider_error_type" => "类型",
        "cause_type" => "根因类型",
        "exception_site" => "代码位置",
        "failure_id" => "问题编号",
        "status" => "状态",
        "http_status" | "outcome" => "状态",
        "dependency" => "依赖",
        "command" => "请求",
        "tool_name" => "工具",
        "provider" => "服务",
        "model" => "模型",
        "elapsed_ms" | "duration_ms" => "耗时",
        "retryable" => "可重试",
        "attempt" => "尝试次数",
        "progress" => "进度",
        "count" => "数量",
        "dropped_count" => "丢弃数量",
        "bytes" => "数据量",
        "actual_bytes" => "实际数据量",
        "expected_bytes" => "预期数据量",
        "source_bytes" => "源数据量",
        "database_bytes" => "数据库大小",
        "snapshot_bytes" => "快照大小",
        "wal_bytes" => "WAL 大小",
        "shm_bytes" => "SHM 大小",
        "actual_files" => "实际文件数",
        "expected_files" => "预期文件数",
        "source_files" => "源文件数",
        "files" | "model_files" => "文件数",
        "model_bytes" => "模型大小",
        "return_code" => "返回码",
        "copy_method" => "复制方式",
        "detected_version" => "检测到的版本",
        "errno" | "winerror" | "sqlite_errorcode" => "系统错误码",
        "sqlite_errorname" => "SQLite 错误",
        "lines" => "行数",
        "items" => "项目数",
        "listed" => "发现数量",
        "registered" => "已注册",
        "segment_count" => "分段数",
        "text_chars" => "文本长度",
        "reply_chars" => "回复长度",
        "resolution" => "分辨率",
        "trigger" => "触发方式",
        "mode" => "更新模式",
        "current_version" => "当前版本",
        "version" => "目标版本",
        "proxy_mode" => "代理模式",
        "proxy_http_configured" => "HTTP 代理",
        "proxy_https_configured" => "HTTPS 代理",
        "proxy_all_configured" => "全局代理",
        "proxy_no_proxy_configured" => "代理排除规则",
        _ => "详情",
    }
}

fn business_message(event: &str) -> Option<&'static str> {
    Some(match event {
        "shell.started" => "Sakura 已启动",
        "shell.ready" => "Sakura 已就绪",
        "shell.stopping" => "Sakura 正在退出",
        "shell.stopped" => "Sakura 已退出",
        "shell.error.unhandled" => "桌面进程发生未处理错误",
        "core.spawn.started" => "正在启动 Core",
        "core.spawn.completed" => "Core 已启动",
        "core.spawn.failed" => "Core 启动失败",
        "core.hello.completed" => "Core 握手完成",
        "core.initialize.completed" => "Core 初始化完成",
        "core.readiness.reached" => "Core 已就绪",
        "core.restart.scheduled" => "Core 即将重启",
        "core.stop.started" => "正在停止 Core",
        "core.stop.completed" | "core.lifecycle.stopped" => "Core 已停止",
        "core.process.started" => "Core 日志桥已启动",
        "core.process.stopping" => "Core 日志桥正在停止",
        "core.error.unhandled" => "Core 发生未处理错误",
        "core.stderr.detected" => "Core 输出了异常诊断",
        "core.stderr.summary" => "Core 诊断输出已汇总",
        "core.log.records_dropped" | "runtime.log.records_dropped" => {
            "运行日志拥塞，部分记录未能保留"
        }
        "ipc.request.started" => "Core 请求开始",
        "ipc.request.completed" => "Core 请求完成",
        "ipc.request.cancelled" => "Core 请求已取消",
        "ipc.request.failed" => "Core 请求失败",
        "agent.turn.started" => "Assistant 开始处理请求",
        "agent.turn.finished" => "Assistant 已生成回复",
        "chat.request.received" => "已收到对话请求",
        "chat.request.completed" => "对话请求已完成",
        "chat.request.cancelled" => "对话请求已取消",
        "chat.request.failed" => "对话请求失败",
        "memory.recall.started" => "开始召回记忆",
        "memory.recall.finished" => "记忆召回完成",
        "memory.recall.failed" => "记忆召回失败",
        "memory.recall.unavailable" => "记忆尚未就绪，本轮未执行召回",
        "memory.initialization.stage" => "Memory 初始化阶段已更新",
        "memory.curation.triggered" => "已触发后台记忆整理",
        "memory.curation.request_fuse_opened" => "自动记忆整理请求保险丝已触发，本次运行不再重试",
        "memory.curation.started" => "开始后台记忆整理",
        "memory.curation.finished" => "后台记忆整理完成",
        "memory.curation.failed" => "后台记忆整理失败",
        "context.prompt.prepared" => "模型上下文已准备完成",
        "context.dependencies.ready" => "Prompt 依赖已就绪",
        "context.dependencies.degraded" => "Prompt 依赖未就绪，本轮降级继续",
        "api.request.started" => "正在请求模型回复",
        "api.request.finished" => "模型请求已完成",
        "api.request.failed" => "模型回复请求失败",
        "api.response.received" => "已收到模型回复",
        "reply.processing.finished" => "模型回复处理完成",
        "reply.processing.repair_started" => "模型回复格式异常，正在修复",
        "reply.processing.failed" => "模型回复处理失败",
        "reply.display.completed" => "回复已显示",
        "reply.display.failed" => "回复显示失败",
        "tool.execution.started" => "正在执行工具",
        "tool.execution.finished" => "工具执行完成",
        "tool.execution.waiting_confirmation" => "工具正在等待确认",
        "tool.execution.failed" => "工具执行失败",
        "screen.capture.started" => "正在获取截图",
        "screen.capture.attached" => "截图已加入本次请求",
        "screen.capture.cancelled" => "截图已取消",
        "screen.capture.failed" => "截图失败",
        "tts.service.started" => "TTS 服务正在启动",
        "tts.service.waiting_ready" => "TTS 进程已启动，正在等待服务就绪",
        "tts.service.ready" => "TTS 服务已就绪",
        "tts.service.failed" => "TTS 服务启动失败",
        "tts.service.http" => "TTS 服务请求已完成",
        "tts.service.warning" => "TTS 服务发出提醒",
        "tts.service.stderr" => "TTS 服务发生错误",
        "tts.service.probe.started" => "正在探测 TTS 服务",
        "tts.service.probe.failed" => "TTS 服务尚未就绪",
        "tts.service.synthesis.started" => "TTS 服务开始合成",
        "tts.service.text.received" => "TTS 服务已收到合成文本",
        "tts.service.info" => "TTS 服务状态已更新",
        "tts.service.warmup_queued" => "TTS 服务预热已排队",
        "tts.service.warmup_skipped" => "TTS 服务预热已跳过",
        "tts.service.warmup_failed" => "TTS 服务预热失败",
        "tts.endpoint.ready" => "TTS 端点已就绪",
        "tts.process.cleanup.started" => "开始清理遗留 TTS 进程",
        "tts.process.cleanup.finished" => "遗留 TTS 进程清理完成",
        "tts.process.cleanup.failed" => "遗留 TTS 进程清理失败",
        "tts.settings.saved" => "语音设置已保存",
        "tts.settings.partial" => "语音设置仅部分保存",
        "tts.synthesis.started" | "tts.request.started" => "正在生成语音",
        "tts.synthesis.ready" => "语音已准备完成",
        "tts.synthesis.finished" | "tts.request.finished" => "语音生成完成",
        "tts.synthesis.cancelled" => "语音生成已取消",
        "tts.synthesis.failed" | "tts.request.failed" => "语音生成失败",
        "tts.recording.committed" => "语音录制已保存",
        "tts.recording.failed" => "语音录制保存失败",
        "tts.playback.started" => "开始播放语音",
        "tts.playback.finished" => "语音播放完成",
        "tts.playback.stopped" => "语音播放已停止",
        "tts.playback.failed" => "语音播放失败",
        "tts.weights.loading" => "正在加载 TTS 角色权重",
        "tts.weights.ready" => "TTS 角色权重已就绪",
        "tts.weights.failed" => "TTS 角色权重加载失败",
        "mcp.server.connecting" => "正在连接 MCP 服务器",
        "mcp.server.ready" => "MCP 服务器已就绪",
        "mcp.ready" => "MCP 工具已就绪",
        "mcp.config.disabled" => "MCP 未启用",
        "mcp.server.failed" => "MCP 服务器连接失败，已跳过",
        "mcp.tool.skipped" => "MCP 工具已跳过",
        "mcp.config.failed" => "MCP 配置读取失败，已跳过",
        "mcp.tool.failed" => "MCP 工具调用失败",
        "mcp.close.failed" => "MCP 连接关闭失败",
        "mcp.close.timeout" => "MCP 连接清理超时",
        "plugin.loaded" => "插件已加载",
        "settings.provider_model.slot_save_failed" => "插件模型槽位保存失败",
        "settings.provider_model.slot_save_reconciled" => "插件模型槽位已通过回读确认保存",
        "startup.window_services.created" => "窗口服务已创建",
        "startup.background_services.created" => "后台服务已创建",
        "startup.background_services.injected" => "后台服务已注入窗口",
        "python.logging.info" => "Core 应用状态已更新",
        "python.logging.warning" => "Core 运行过程中出现提醒",
        "python.logging.error" => "Core 运行过程中发生错误",
        "first_run.state.loaded" => "首次配置状态已读取",
        "first_run.state.failed" => "首次配置状态读取失败",
        "first_run.onboarding.opened" => "首次启动欢迎页已打开",
        "first_run.core_start.started" => "首次配置正在启动 Core",
        "first_run.core_start.completed" => "首次配置 Core 已就绪",
        "first_run.core_start.failed" => "首次配置 Core 启动失败",
        "first_run.configuration.completed" => "首次配置已完成",
        "first_run.configuration.failed" => "首次配置保存失败",
        "legacy_import.recovery.completed" => "上次中断的旧版本迁移已回滚",
        "legacy_import.recovery.failed" => "旧版本迁移恢复失败",
        "updater.check.started" => "正在检查更新",
        "updater.check.completed" => "更新检查完成",
        "updater.check.failed" => "更新检查失败",
        "updater.download.started" => "正在下载更新",
        "updater.download.completed" => "更新下载完成",
        "updater.download.failed" => "更新下载失败",
        "updater.signature.failed" => "更新包签名验证失败",
        "updater.install.started" => "正在安装更新",
        "updater.install.completed" => "更新安装已启动",
        "updater.install.failed" => "更新安装失败",
        value if value.starts_with("legacy_import.") => legacy_import_business_message(value)?,
        _ => return None,
    })
}

fn legacy_import_business_message(event: &str) -> Option<&'static str> {
    Some(match event {
        "legacy_import.started" => "旧版本迁移开始",
        "legacy_import.staged" => "旧版本迁移已提交，等待 Core 校验",
        "legacy_import.failed" => "旧版本迁移失败",
        "legacy_import.worker_started" => "旧版本迁移任务已启动",
        "legacy_import.progress_received" => "旧版本迁移进度已更新",
        "legacy_import.core_validation_entered" => "旧版本迁移进入 Core 校验",
        "legacy_import.core_start_submitted" => "迁移后的 Core 启动已提交",
        "legacy_import.core_start_failed" => "迁移后的 Core 启动失败",
        "legacy_import.core_ready" => "迁移后的 Core 已就绪",
        "legacy_import.core_setup_required" => "迁移后仍需完成首次配置",
        "legacy_import.core_validation_failed" => "迁移后的 Core 校验失败",
        "legacy_import.core_readiness_changed" => "迁移后的 Core 就绪状态已变化",
        "legacy_import.python_started" => "迁移 Python 子进程已启动",
        "legacy_import.stdout_read_failed" => "读取迁移 Python 输出失败",
        "legacy_import.stdout_json_invalid" => "迁移 Python 输出格式无效",
        "legacy_import.result_received" => "桌面端收到迁移结果",
        "legacy_import.error_received" => "桌面端收到迁移错误",
        "legacy_import.stdout_closed" => "迁移 Python 输出流已关闭",
        "legacy_import.python_exited" => "迁移 Python 子进程已退出",
        "legacy_import.result_invalid" => "迁移结果无效",
        "legacy_import.result_recovered_from_journal" => "迁移结果已从事务状态恢复",
        "legacy_import.memory_copy_started" => "开始复制旧版本长期记忆",
        "legacy_import.memory_snapshot_started" => "开始创建长期记忆 SQLite 快照",
        "legacy_import.memory_snapshot_source_opened" => "旧版本长期记忆数据库已打开",
        "legacy_import.memory_snapshot_completed" => "长期记忆 SQLite 快照创建完成",
        "legacy_import.memory_snapshot_failed" => "长期记忆 SQLite 快照创建失败",
        "legacy_import.memory_completed" => "旧版本长期记忆迁移完成",
        "legacy_import.memory_model_reused" => "目标中的记忆模型已通过校验",
        "legacy_import.memory_model_copied" => "随旧版本迁移的记忆模型已通过校验",
        "legacy_import.memory_model_prepared" => "当前记忆模型已写入迁移事务并通过校验",
        "legacy_import.memory_model_failed" => "迁移所需的记忆模型准备失败",
        "legacy_import.tts_copy_started" => "TTS 资源复制开始",
        "legacy_import.tts_copy_completed" => "TTS 资源复制完成",
        "legacy_import.tts_copy_failed" => "TTS 资源复制失败",
        "legacy_import.tts_copy_preflight_completed" => "TTS 资源复制预扫描完成",
        "legacy_import.tts_copy_robocopy_started" => "TTS 系统复制开始",
        "legacy_import.tts_copy_robocopy_completed" => "TTS 系统复制完成",
        "legacy_import.tts_copy_robocopy_failed" => "TTS 系统复制失败",
        "legacy_import.tts_onnx_started" => "开始合并旧版 TTS ONNX 资源",
        "legacy_import.tts_profiles_adapted" => "旧版 TTS 托管配置已适配",
        "legacy_import.tts_runtime_paths_sanitized" => "旧版 TTS Python 路径已适配",
        "legacy_import.tts_completed" => "旧版本 TTS 资源迁移完成",
        "legacy_import.tts_skipped" => "TTS 资源迁移失败，已保留聊天和记忆",
        "legacy_import.tts_config_skipped" => "TTS 配置迁移失败，已保留聊天和记忆",
        "legacy_import.tts_onnx_binding_skipped" => "TTS ONNX 角色绑定失败，模型资源已保留",
        "legacy_import.characters_skipped" => "角色包迁移失败，已保留聊天和记忆",
        "legacy_import.character_validation_failed" => "迁移后的角色包校验失败",
        _ => return None,
    })
}

fn viewer_message(event: &str, severity: Severity) -> &'static str {
    if let Some(message) = business_message(event) {
        return message;
    }
    match event {
        "shell.started" => "Sakura 已启动",
        "shell.ready" => "Sakura 已就绪",
        "shell.stopping" => "Sakura 正在退出",
        "shell.stopped" => "Sakura 已退出",
        "shell.error.unhandled" => "桌面进程发生未处理错误",
        "updater.check.started" => "正在检查更新",
        "updater.check.completed" => "更新检查完成",
        "updater.check.failed" => "更新检查失败",
        "updater.download.started" => "正在下载更新",
        "updater.download.completed" => "更新下载完成",
        "updater.download.failed" => "更新下载失败",
        "updater.signature.failed" => "更新包签名验证失败",
        "updater.install.started" => "正在安装更新",
        "updater.install.completed" => "更新安装已启动",
        "updater.install.failed" => "更新安装失败",
        "core.spawn.started" => "正在启动 Core",
        "core.spawn.completed" => "Core 已启动",
        "core.spawn.failed" => "Core 启动失败",
        "core.initialize.completed" => "Core 初始化完成",
        "core.readiness.reached" => "Core 已就绪",
        "core.restart.scheduled" => "Core 即将重启",
        "core.error.unhandled" => "Core 发生未处理错误",
        "core.stderr.detected" => "Core 输出了异常诊断",
        "core.stderr.summary" => "Core 诊断输出已汇总",
        "core.log.records_dropped" | "runtime.log.records_dropped" => {
            "运行日志拥塞，部分记录未能保留"
        }
        "chat.request.received" => "已收到对话请求",
        "chat.request.completed" => "对话请求已完成",
        "chat.request.cancelled" => "对话请求已取消",
        "chat.request.failed" => "对话请求失败",
        "api.request.started" => "正在请求模型回复",
        "api.request.finished" => "模型请求已完成",
        "api.request.failed" => "模型回复请求失败",
        "api.response.received" => "已收到模型回复",
        "reply.processing.failed" => "模型回复处理失败",
        "reply.display.completed" => "回复已显示",
        "reply.display.failed" => "回复显示失败",
        "tool.execution.started" => "正在执行工具",
        "tool.execution.finished" => "工具执行完成",
        "tool.execution.waiting_confirmation" => "工具正在等待确认",
        "tool.execution.failed" => "工具执行失败",
        "screen.capture.started" => "正在获取截图",
        "screen.capture.attached" => "截图已加入本次请求",
        "screen.capture.cancelled" => "截图已取消",
        "screen.capture.failed" => "截图失败",
        "tts.service.started" => "TTS 服务正在启动",
        "tts.service.ready" => "TTS 服务已就绪",
        "tts.service.failed" => "TTS 服务启动失败",
        "tts.settings.saved" => "语音设置已保存",
        "tts.synthesis.started" | "tts.request.started" => "正在生成语音",
        "tts.synthesis.ready" => "语音已准备完成",
        "tts.synthesis.finished" | "tts.request.finished" => "语音生成完成",
        "tts.synthesis.cancelled" => "语音生成已取消",
        "tts.synthesis.failed" | "tts.request.failed" => "语音生成失败",
        "tts.playback.started" => "开始播放语音",
        "tts.playback.finished" => "语音播放完成",
        "tts.playback.stopped" => "语音播放已停止",
        "tts.playback.failed" => "语音播放失败",
        "tts.service.probe.failed" => "TTS 服务尚未就绪",
        "tts.service.warning" => "TTS 服务发出提醒",
        "tts.service.stderr" => "TTS 服务发生错误",
        "tts.process.cleanup.failed" => "TTS 服务清理失败",
        "tts.recording.failed" => "语音录制保存失败",
        "mcp.server.connecting" => "正在连接 MCP 服务器",
        "mcp.server.ready" => "MCP 服务器已就绪",
        "mcp.ready" => "MCP 工具已就绪",
        "mcp.config.disabled" => "MCP 未启用",
        "mcp.server.failed" => "MCP 服务器连接失败，已跳过",
        "mcp.tool.skipped" => "MCP 工具已跳过",
        "mcp.config.failed" => "MCP 配置读取失败，已跳过",
        "mcp.tool.failed" => "MCP 工具调用失败",
        "mcp.close.failed" => "MCP 连接关闭失败",
        "mcp.close.timeout" => "MCP 连接清理超时",
        "plugin.loaded" => "插件已加载",
        "python.logging.warning" => "Core 运行过程中出现提醒",
        "python.logging.error" => "Core 运行过程中发生错误",
        _ if severity == Severity::Error => "运行过程中发生错误",
        _ if severity == Severity::Warning => "运行过程中出现提醒",
        _ => "运行状态已更新",
    }
}

fn format_bytes(bytes: u64) -> String {
    if bytes < 1024 {
        return format!("{bytes} B");
    }
    if bytes < 1024 * 1024 {
        return format!("{:.1} KB", bytes as f64 / 1024.0);
    }
    format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
}

fn viewer_correlation(record: &RuntimeLogRecord) -> Option<String> {
    [
        ("op", record.operation_id.as_deref()),
        ("trace", record.trace_id.as_deref()),
        ("request", record.request_id.as_deref()),
        ("action", record.action_id.as_deref()),
    ]
    .into_iter()
    .find_map(|(kind, value)| value.map(|value| format!("{kind}:{}", short_correlation_id(value))))
}

fn display_channel(channel: &str, event: &str) -> String {
    if event.starts_with("webview.chat.") {
        return "CHAT".to_string();
    }
    if let Some(prefix) = event.split('.').next() {
        match prefix {
            "chat" => return "CHAT".to_string(),
            "context" => return "CONTEXT".to_string(),
            "reply" => return "REPLY".to_string(),
            "screen" => return "SCREEN".to_string(),
            _ => {}
        }
    }
    let root = channel
        .split('.')
        .next()
        .unwrap_or(channel)
        .to_ascii_lowercase();
    match root.as_str() {
        "api" => "API".to_string(),
        "agent" | "agentruntime" => "AGENT".to_string(),
        "app" | "shell" | "startup" => "APP".to_string(),
        "config" => "CONFIG".to_string(),
        "core" => "CORE".to_string(),
        "interaction" => "LATENCY".to_string(),
        "memory" => "MEMORY".to_string(),
        "mcp" => "MCP".to_string(),
        "plugin" => "PLUGIN".to_string(),
        "storage" => "STORAGE".to_string(),
        "tool" | "toolregistry" => "TOOL".to_string(),
        "tts" => "TTS".to_string(),
        "ui" | "webview" => "UI".to_string(),
        _ => root
            .chars()
            .take(16)
            .collect::<String>()
            .to_ascii_uppercase(),
    }
}

fn human_message<'a>(event: &str, fallback: &'a str) -> &'a str {
    let core = core_message(event);
    if core != "Core 运行事件" {
        return core;
    }
    if let Some(message) = business_message(event) {
        return message;
    }
    match event {
        "shell.started" => "Sakura 已启动",
        "shell.ready" => "Sakura 已就绪",
        "shell.stopping" => "Sakura 正在退出",
        "shell.stopped" => "Sakura 已退出",
        "shell.error.unhandled" => "桌面进程发生未处理错误",
        "core.spawn.started" => "正在启动 Core",
        "core.spawn.completed" => "Core 已启动",
        "core.spawn.failed" => "Core 启动失败",
        "core.hello.completed" => "Core 握手完成",
        "core.initialize.completed" => "Core 初始化完成",
        "core.readiness.reached" => "Core 已就绪",
        "core.restart.scheduled" => "Core 即将重启",
        "core.stop.started" => "正在停止 Core",
        "core.stop.completed" | "core.lifecycle.stopped" => "Core 已停止",
        "core.stderr.detected" => "Core 输出了异常诊断",
        "core.stderr.summary" => "Core 诊断输出已汇总",
        "ipc.request.started" => "Core 请求开始",
        "ipc.request.completed" => "Core 请求完成",
        "ipc.request.cancelled" => "Core 请求已取消",
        "ipc.request.failed" => "Core 请求失败",
        "interaction.latency.stage" => "交互阶段耗时",
        "runtime.log.records_dropped" => "运行日志拥塞，部分记录已丢弃",
        "tts.playback.started" => "开始播放语音",
        "tts.playback.finished" => "语音播放完成",
        "tts.playback.stopped" => "语音播放已停止",
        "tts.playback.failed" => "语音播放失败",
        _ => fallback,
    }
}

fn correlation_summary(record: &RuntimeLogRecord) -> Vec<String> {
    let mut parts = Vec::new();
    if let Some(operation_id) = record.operation_id.as_deref() {
        parts.push(format!("op={}", short_correlation_id(operation_id)));
    }
    if let Some(trace_id) = record.trace_id.as_deref() {
        parts.push(format!("trace={}", short_correlation_id(trace_id)));
    }
    parts
}

fn short_correlation_id(value: &str) -> String {
    value.chars().take(8).collect()
}

fn format_human_summary(event: &str, attributes: Option<&Value>) -> String {
    const DEFAULT_PRIORITY: [&str; 51] = [
        "dependency",
        "stage",
        "detail_stage",
        "status",
        "outcome",
        "code",
        "error_type",
        "diagnostic",
        "reason_code",
        "cause_type",
        "exception_site",
        "failure_id",
        "elapsed_ms",
        "command_elapsed_ms",
        "event_delay_ms",
        "count",
        "dropped_count",
        "bytes",
        "lines",
        "items",
        "listed",
        "filtered",
        "attempt",
        "tool_name",
        "command",
        "request",
        "server_id",
        "registered",
        "transport",
        "action",
        "category",
        "component",
        "source",
        "scope",
        "risk",
        "failed",
        "created",
        "updated",
        "archived",
        "ignored",
        "forced",
        "truncated",
        "read_failed",
        "process_alive",
        "tree_empty",
        "child_pid",
        "core_pid",
        "window_label",
        "window_generation",
        "revision",
        "wait",
    ];
    const CONTEXT_PRIORITY: [&str; 8] = [
        "model_call",
        "purpose",
        "history_messages",
        "memories",
        "tool_count",
        "estimated_tokens",
        "memory_estimated_tokens",
        "model",
    ];
    const API_STARTED_PRIORITY: [&str; 5] =
        ["model_call", "purpose", "provider", "model", "attempt"];
    const SHELL_STARTED_PRIORITY: [&str; 1] = ["current_version"];
    const MEMORY_PRIORITY: [&str; 6] = [
        "selected",
        "candidates",
        "status",
        "elapsed_ms",
        "code",
        "error_type",
    ];
    const API_FINISHED_PRIORITY: [&str; 9] = [
        "model_call",
        "status",
        "elapsed_ms",
        "attempt",
        "retryable",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "model",
    ];
    const API_FAILED_PRIORITY: [&str; 17] = [
        "model_call",
        "status",
        "provider_error_type",
        "provider_error_code",
        "error_type",
        "diagnostic",
        "reason_code",
        "stage",
        "cause_type",
        "exception_site",
        "failure_id",
        "elapsed_ms",
        "attempt",
        "retryable",
        "provider",
        "model",
        "purpose",
    ];
    const IPC_FAILED_PRIORITY: [&str; 10] = [
        "code",
        "diagnostic",
        "exception_site",
        "failure_id",
        "cause_type",
        "deadline_ms",
        "elapsed_ms",
        "command",
        "outcome",
        "category",
    ];
    const API_RESPONSE_PRIORITY: [&str; 8] = [
        "model_call",
        "parse_status",
        "tool_call_count",
        "reply_chars",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "model",
    ];
    const TOOL_PRIORITY: [&str; 7] = [
        "tool_name",
        "model_call",
        "status",
        "elapsed_ms",
        "attempt",
        "code",
        "retryable",
    ];
    const REPLY_PRIORITY: [&str; 9] = [
        "segment_count",
        "segments",
        "parse_status",
        "final_reply_elapsed_ms",
        "turn_elapsed_ms",
        "elapsed_ms",
        "status",
        "code",
        "model_call",
    ];
    const SCREEN_PRIORITY: [&str; 7] = [
        "count",
        "width",
        "height",
        "resolution",
        "elapsed_ms",
        "status",
        "code",
    ];
    const TTS_PRIORITY: [&str; 15] = [
        "provider",
        "provider_error_code",
        "segment_index",
        "segment_count",
        "recording_id",
        "playback_id",
        "port",
        "progress",
        "text_chars",
        "attempt",
        "bytes",
        "duration_ms",
        "elapsed_ms",
        "status",
        "code",
    ];
    const LEGACY_COPY_PRIORITY: [&str; 12] = [
        "detail_stage",
        "copy_method",
        "return_code",
        "source_files",
        "source_bytes",
        "expected_files",
        "expected_bytes",
        "actual_files",
        "actual_bytes",
        "code",
        "reason_code",
        "error_type",
    ];
    const FAILURE_DETAIL_PRIORITY: [&str; 8] = [
        "diagnostic",
        "code",
        "reason_code",
        "stage",
        "error_type",
        "cause_type",
        "exception_site",
        "failure_id",
    ];
    let Some(object) = attributes.and_then(Value::as_object) else {
        return String::new();
    };
    let priority: &[&str] = match event {
        "shell.started" => &SHELL_STARTED_PRIORITY,
        "context.prompt.prepared" => &CONTEXT_PRIORITY,
        value if value.starts_with("context.dependencies.") => &DEFAULT_PRIORITY,
        value if value.starts_with("memory.recall.") => &MEMORY_PRIORITY,
        "api.request.started" => &API_STARTED_PRIORITY,
        "api.request.finished" => &API_FINISHED_PRIORITY,
        "api.request.failed" => &API_FAILED_PRIORITY,
        "ipc.request.failed" => &IPC_FAILED_PRIORITY,
        "api.response.received" => &API_RESPONSE_PRIORITY,
        value if value.starts_with("tool.execution.") => &TOOL_PRIORITY,
        value if value.starts_with("reply.") => &REPLY_PRIORITY,
        value if value.starts_with("screen.capture.") => &SCREEN_PRIORITY,
        value if value.starts_with("tts.") => &TTS_PRIORITY,
        value if value.starts_with("legacy_import.tts_copy_") => &LEGACY_COPY_PRIORITY,
        _ => &DEFAULT_PRIORITY,
    };
    let mut parts = Vec::new();
    let mut seen = Vec::new();
    for wanted in priority
        .iter()
        .copied()
        .chain(FAILURE_DETAIL_PRIORITY.into_iter())
    {
        if seen.contains(&wanted) {
            continue;
        }
        seen.push(wanted);
        let Some((_, value)) = object
            .iter()
            .find(|(key, value)| normalize_key(key) == wanted && is_human_scalar(value))
        else {
            continue;
        };
        let rendered = render_human_scalar(wanted, value);
        let suffix = if wanted.ends_with("_ms") { "ms" } else { "" };
        let display_key = match wanted {
            "model_call" => "call",
            "history_messages" => "history",
            "tool_count" => "tools",
            "segment_count" => "segments",
            other => other,
        };
        parts.push(format!("{display_key}={rendered}{suffix}"));
        if parts.len() >= 12 {
            break;
        }
    }
    parts.join(" ")
}

fn render_human_scalar(key: &str, value: &Value) -> String {
    if key.ends_with("_ms") {
        if let Some(number) = value.as_f64() {
            let fixed = format!("{number:.2}");
            return fixed
                .trim_end_matches('0')
                .trim_end_matches('.')
                .to_string();
        }
    }
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}

fn is_human_scalar(value: &Value) -> bool {
    matches!(
        value,
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_)
    )
}

fn truncate_utf8_line(mut text: String, max_bytes: usize) -> Vec<u8> {
    if text.len() <= max_bytes {
        return text.into_bytes();
    }
    let suffix = "…\n";
    let limit = max_bytes.saturating_sub(suffix.len());
    let mut boundary = limit.min(text.len());
    while boundary > 0 && !text.is_char_boundary(boundary) {
        boundary -= 1;
    }
    text.truncate(boundary);
    while text.ends_with(['\r', '\n', ' ', '│']) {
        text.pop();
    }
    text.push_str(suffix);
    text.into_bytes()
}

fn backup_path(path: &Path, index: usize) -> PathBuf {
    path.with_file_name(format!(
        "{}.{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("sakura-runtime.log"),
        index
    ))
}

fn sanitize_correlation(correlation: Correlation, secrets: &[String]) -> Correlation {
    Correlation {
        generation_id: sanitize_untrusted_id(correlation.generation_id, secrets, None),
        generation_number: correlation.generation_number.filter(|number| *number > 0),
        core_pid: correlation.core_pid.filter(|pid| *pid > 0),
        request_id: sanitize_untrusted_id(correlation.request_id, secrets, None),
        operation_id: sanitize_untrusted_id(correlation.operation_id, secrets, None),
        action_id: sanitize_untrusted_id(correlation.action_id, secrets, None),
        trace_id: sanitize_untrusted_id(correlation.trace_id, secrets, None),
    }
}

fn sanitize_untrusted_id(
    value: Option<String>,
    secrets: &[String],
    forbidden_secret: Option<&str>,
) -> Option<String> {
    value.and_then(|value| {
        (!contains_secret(&value, secrets, forbidden_secret))
            .then(|| valid_id(&value, 128))
            .flatten()
    })
}

fn contains_secret(value: &str, secrets: &[String], forbidden_secret: Option<&str>) -> bool {
    forbidden_secret.is_some_and(|secret| !secret.is_empty() && value.contains(secret))
        || secrets.iter().any(|secret| value.contains(secret))
        || looks_secret_shaped(value)
}

fn sanitize_attributes(value: &Value, secrets: &[String]) -> Option<Value> {
    let object = value.as_object()?;
    let mut safe = Map::new();
    for (key, value) in object.iter().take(32) {
        let normalized = normalize_key(key);
        if forbidden_key(&normalized) || !allowed_attribute_key(&normalized) {
            continue;
        }
        let sanitized = match value {
            Value::Null | Value::Bool(_) | Value::Number(_) => value.clone(),
            Value::String(text) => sanitize_attribute_string(&normalized, text, secrets)
                .map(Value::String)
                .unwrap_or_else(|| json!({"type": "text", "chars": text.chars().count()})),
            Value::Array(values) => json!({"type": "list", "items": values.len()}),
            Value::Object(values) if normalized == "counts" => {
                let mut counts = Map::new();
                for (name, count) in values.iter().take(16) {
                    if normalize_token(name, 64).is_some() && count.as_u64().is_some() {
                        counts.insert(name.clone(), count.clone());
                    }
                }
                if counts.is_empty() {
                    json!({"type": "object", "keys": values.len()})
                } else {
                    Value::Object(counts)
                }
            }
            Value::Object(values) => json!({"type": "object", "keys": values.len()}),
        };
        safe.insert(key.chars().take(64).collect(), sanitized);
    }
    (!safe.is_empty()).then_some(Value::Object(safe))
}

fn sanitize_attribute_string(
    normalized_key: &str,
    value: &str,
    secrets: &[String],
) -> Option<String> {
    if matches!(normalized_key, "error" | "reason" | "message") {
        return None;
    }
    let stripped = strip_ansi(value);
    if normalized_key == "diagnostic" {
        if stripped.is_empty()
            || looks_absolute_path(&stripped)
            || stripped.contains("://")
            || looks_secret_shaped(&stripped)
            || secrets.iter().any(|secret| stripped.contains(secret))
        {
            return None;
        }
        return Some(stripped.chars().take(320).collect());
    }
    if stripped.is_empty()
        || looks_absolute_path(&stripped)
        || stripped.contains("://")
        || looks_secret_shaped(&stripped)
        || secrets.iter().any(|secret| stripped.contains(secret))
    {
        return None;
    }
    Some(stripped.chars().take(192).collect())
}

fn forbidden_key(key: &str) -> bool {
    if matches!(
        key,
        "diagnostic"
            | "cause_type"
            | "error_type"
            | "exception_site"
            | "failure_id"
            | "provider_error_code"
            | "provider_error_type"
            | "reason_code"
            | "prompt_tokens"
            | "completion_tokens"
            | "total_tokens"
            | "estimated_tokens"
            | "memory_estimated_tokens"
            | "request_estimated_tokens"
            | "tool_schema_estimated_tokens"
    ) {
        return false;
    }
    [
        "authorization",
        "cookie",
        "credential",
        "apikey",
        "token",
        "secret",
        "password",
        "prompt",
        "body",
        "content",
        "input",
        "output",
        "payload",
        "arguments",
        "query",
        "memory",
        "translation",
        "path",
    ]
    .iter()
    .any(|marker| key.contains(marker))
}

fn allowed_attribute_key(key: &str) -> bool {
    matches!(
        key,
        "action"
            | "actual_bytes"
            | "actual_files"
            | "attempt"
            | "bytes"
            | "byte_delta"
            | "candidates"
            | "category"
            | "dependency"
            | "child_pid"
            | "code"
            | "command"
            | "command_elapsed_ms"
            | "component"
            | "count"
            | "counts"
            | "client_epoch_ms"
            | "client_perf_ms"
            | "deadline_ms"
            | "database_bytes"
            | "detected_version"
            | "detail_stage"
            | "diagnostic"
            | "dropped_bytes"
            | "dropped_count"
            | "dropped_records"
            | "elapsed_ms"
            | "estimated_tokens"
            | "epoch_ms"
            | "event_delay_ms"
            | "event_perf_ms"
            | "eof"
            | "error_type"
            | "cause_type"
            | "exception_site"
            | "errno"
            | "expected_bytes"
            | "expected_files"
            | "failed"
            | "failure_id"
            | "created"
            | "updated"
            | "archived"
            | "ignored"
            | "final_reply_elapsed_ms"
            | "forced"
            | "gesture_id"
            | "height"
            | "history_messages"
            | "host_state"
            | "items"
            | "listed"
            | "filtered"
            | "lines"
            | "memories"
            | "memory_estimated_tokens"
            | "model"
            | "model_bytes"
            | "model_call"
            | "model_cached"
            | "model_files"
            | "name"
            | "operation"
            | "outcome"
            | "perf_ms"
            | "process_ms"
            | "process_alive"
            | "parse_status"
            | "prompt_tokens"
            | "completion_tokens"
            | "total_tokens"
            | "provider"
            | "recording_id"
            | "playback_id"
            | "port"
            | "progress"
            | "percent"
            | "profiles"
            | "pth_files"
            | "quick_check"
            | "journal_mode"
            | "page_count"
            | "remaining_pages"
            | "total_pages"
            | "return_code"
            | "duration_ms"
            | "http_status"
            | "retry_count"
            | "provider_error_code"
            | "provider_error_type"
            | "purpose"
            | "read_failed"
            | "readiness"
            | "record_bytes"
            | "record_truncated"
            | "request"
            | "reason_code"
            | "server_id"
            | "registered"
            | "received_epoch_ms"
            | "received_process_ms"
            | "revision"
            | "reply_chars"
            | "request_estimated_tokens"
            | "resolution"
            | "retryable"
            | "risk"
            | "selected"
            | "segment_count"
            | "segment_index"
            | "segments"
            | "scope"
            | "source"
            | "source_bytes"
            | "source_files"
            | "snapshot_bytes"
            | "sqlite_errorcode"
            | "sqlite_errorname"
            | "sqlite_version"
            | "stage"
            | "status"
            | "succeeded"
            | "step_index"
            | "text_chars"
            | "tool_call_count"
            | "tool_count"
            | "tool_schema_estimated_tokens"
            | "tool_name"
            | "trigger"
            | "transport"
            | "mode"
            | "current_version"
            | "version"
            | "proxy_mode"
            | "proxy_http_configured"
            | "proxy_https_configured"
            | "proxy_all_configured"
            | "proxy_no_proxy_configured"
            | "tree_empty"
            | "truncated"
            | "truncated_records"
            | "turn_elapsed_ms"
            | "window_generation"
            | "window_label"
            | "wait"
            | "wal_bytes"
            | "shm_bytes"
            | "winerror"
            | "width"
            | "copy_method"
            | "files"
    )
}

fn normalize_key(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect::<String>()
        .replace("actualbytes", "actual_bytes")
        .replace("actualfiles", "actual_files")
        .replace("bytedelta", "byte_delta")
        .replace("deadlinems", "deadline_ms")
        .replace("databasebytes", "database_bytes")
        .replace("detectedversion", "detected_version")
        .replace("commandelapsedms", "command_elapsed_ms")
        .replace("clientepochms", "client_epoch_ms")
        .replace("clientperfms", "client_perf_ms")
        .replace("detailstage", "detail_stage")
        .replace("droppedbytes", "dropped_bytes")
        .replace("droppedcount", "dropped_count")
        .replace("droppedrecords", "dropped_records")
        .replace("receivedepochms", "received_epoch_ms")
        .replace("receivedprocessms", "received_process_ms")
        .replace("elapsedms", "elapsed_ms")
        .replace("epochms", "epoch_ms")
        .replace("eventdelayms", "event_delay_ms")
        .replace("eventperfms", "event_perf_ms")
        .replace("errortype", "error_type")
        .replace("causetype", "cause_type")
        .replace("exceptionsite", "exception_site")
        .replace("expectedbytes", "expected_bytes")
        .replace("expectedfiles", "expected_files")
        .replace("finalreplyelapsedms", "final_reply_elapsed_ms")
        .replace("failureid", "failure_id")
        .replace("gestureid", "gesture_id")
        .replace("hoststate", "host_state")
        .replace("historymessages", "history_messages")
        .replace("childpid", "child_pid")
        .replace("memoryestimatedtokens", "memory_estimated_tokens")
        .replace("modelcall", "model_call")
        .replace("modelcached", "model_cached")
        .replace("modelbytes", "model_bytes")
        .replace("modelfiles", "model_files")
        .replace("journalmode", "journal_mode")
        .replace("pagecount", "page_count")
        .replace("operationid", "operation")
        .replace("perfms", "perf_ms")
        .replace("processms", "process_ms")
        .replace("processalive", "process_alive")
        .replace("currentversion", "current_version")
        .replace("proxymode", "proxy_mode")
        .replace("proxyhttpconfigured", "proxy_http_configured")
        .replace("proxyhttpsconfigured", "proxy_https_configured")
        .replace("proxyallconfigured", "proxy_all_configured")
        .replace("proxynoproxyconfigured", "proxy_no_proxy_configured")
        .replace("parsestatus", "parse_status")
        .replace("prompttokens", "prompt_tokens")
        .replace("providererrorcode", "provider_error_code")
        .replace("providererrortype", "provider_error_type")
        .replace("providererror_type", "provider_error_type")
        .replace("completiontokens", "completion_tokens")
        .replace("totaltokens", "total_tokens")
        .replace("readfailed", "read_failed")
        .replace("remainingpages", "remaining_pages")
        .replace("reasoncode", "reason_code")
        .replace("pthfiles", "pth_files")
        .replace("quickcheck", "quick_check")
        .replace("recordbytes", "record_bytes")
        .replace("recordtruncated", "record_truncated")
        .replace("replychars", "reply_chars")
        .replace("requestestimatedtokens", "request_estimated_tokens")
        .replace("segmentcount", "segment_count")
        .replace("segmentindex", "segment_index")
        .replace("serverid", "server_id")
        .replace("shmbytes", "shm_bytes")
        .replace("snapshotbytes", "snapshot_bytes")
        .replace("sourcebytes", "source_bytes")
        .replace("sourcefiles", "source_files")
        .replace("sqliteerrorcode", "sqlite_errorcode")
        .replace("sqliteerrorname", "sqlite_errorname")
        .replace("sqliteversion", "sqlite_version")
        .replace("stepindex", "step_index")
        .replace("textchars", "text_chars")
        .replace("toolcallcount", "tool_call_count")
        .replace("toolcount", "tool_count")
        .replace("toolschemaestimatedtokens", "tool_schema_estimated_tokens")
        .replace("estimatedtokens", "estimated_tokens")
        .replace("toolname", "tool_name")
        .replace("treeempty", "tree_empty")
        .replace("truncatedrecords", "truncated_records")
        .replace("totalpages", "total_pages")
        .replace("turnelapsedms", "turn_elapsed_ms")
        .replace("returncode", "return_code")
        .replace("copymethod", "copy_method")
        .replace("walbytes", "wal_bytes")
        .replace("windowgeneration", "window_generation")
        .replace("windowlabel", "window_label")
}

fn normalize_token(value: &str, maximum: usize) -> Option<String> {
    if value.is_empty()
        || value.len() > maximum
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
    {
        return None;
    }
    Some(value.to_string())
}

fn valid_id(value: &str, maximum: usize) -> Option<String> {
    normalize_token(value, maximum)
}

fn sanitize_fixed_message(value: &str) -> String {
    let stripped = strip_ansi(value);
    if stripped.is_empty() || looks_absolute_path(&stripped) || stripped.contains("://") {
        return "Runtime event".to_string();
    }
    stripped.chars().take(192).collect()
}

fn strip_ansi(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    let mut characters = value.chars();
    while let Some(character) = characters.next() {
        if character == '\u{1b}' {
            for nested in characters.by_ref() {
                if nested.is_ascii_alphabetic() {
                    break;
                }
            }
        } else if !character.is_control() || matches!(character, '\t' | '\n') {
            output.push(character);
        }
    }
    output
}

pub(crate) fn looks_absolute_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    let contains_windows_path = bytes.windows(3).any(|window| {
        window[0].is_ascii_alphabetic() && window[1] == b':' && matches!(window[2], b'/' | b'\\')
    });
    let contains_unc_path = value.contains("\\\\") || value.contains("//");
    let contains_posix_path = value.split_whitespace().any(|part| {
        part.trim_start_matches(['\'', '"', '(', '[', '{'])
            .starts_with('/')
    });
    contains_windows_path || contains_unc_path || contains_posix_path
}

fn looks_secret_shaped(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    lower.starts_with("bearer ")
        || lower.starts_with("sk-")
        || lower.contains("api_key=")
        || lower.contains("apikey=")
        || lower.contains("token=")
        || lower.contains("secret=")
        || lower.contains("password=")
}

fn verbosity_for_severity(severity: Severity) -> Verbosity {
    match severity {
        Severity::Trace => Verbosity::Trace,
        Severity::Debug => Verbosity::Debug,
        Severity::Info => Verbosity::Info,
        Severity::Warning => Verbosity::Warn,
        Severity::Error => Verbosity::Error,
    }
}

fn core_message(event: &str) -> &'static str {
    match event {
        "agent.turn.started" => "开始处理用户消息",
        "agent.turn.finished" => "模型回复已生成",
        "chat.request.received" => "对话请求已接收",
        "chat.request.completed" => "对话请求已完成",
        "chat.request.cancelled" => "对话请求已取消",
        "chat.request.failed" => "对话请求失败",
        "memory.recall.started" => "开始召回记忆",
        "memory.recall.finished" => "记忆召回完成",
        "memory.recall.failed" => "记忆召回失败",
        "memory.recall.unavailable" => "记忆未就绪，本轮未执行召回",
        "memory.curation.started" => "开始后台记忆整理",
        "memory.curation.finished" => "后台记忆整理完成",
        "memory.curation.failed" => "后台记忆整理失败，稍后将重试",
        "context.prompt.prepared" => "模型上下文已构建",
        "context.dependencies.ready" => "Prompt 依赖已就绪",
        "context.dependencies.degraded" => "Prompt 依赖未就绪，继续降级对话",
        "api.request.started" => "发送模型请求",
        "api.request.finished" => "模型请求成功",
        "api.request.failed" => "模型请求失败",
        "api.response.received" => "收到模型回复",
        "reply.processing.finished" => "回复处理完成",
        "reply.processing.repair_started" => "回复格式异常，尝试修复",
        "reply.processing.failed" => "回复处理失败，已使用安全兜底",
        "reply.display.completed" => "回复展示完成",
        "reply.display.failed" => "回复展示失败",
        "tool.execution.started" => "开始执行工具",
        "tool.execution.finished" => "工具执行完成",
        "tool.execution.failed" => "工具执行失败",
        "screen.capture.started" => "开始截图",
        "screen.capture.attached" => "截图已附加",
        "screen.capture.cancelled" => "截图已取消",
        "screen.capture.failed" => "截图失败",
        "tts.service.started" => "TTS 服务启动中",
        "tts.service.waiting_ready" => "TTS 进程已启动，正在等待服务就绪",
        "tts.service.ready" => "TTS 服务已就绪",
        "tts.service.failed" => "TTS 服务启动失败",
        "tts.process.cleanup.started" => "正在检查旧 TTS 进程",
        "tts.process.cleanup.finished" => "旧 TTS 进程检查完成",
        "tts.process.cleanup.failed" => "旧 TTS 进程清理失败",
        "tts.settings.saved" => "TTS 设置已保存",
        "tts.synthesis.started" => "开始合成语音",
        "tts.synthesis.ready" => "语音合成完成",
        "tts.synthesis.finished" => "语音合成完成",
        "tts.synthesis.failed" => "语音合成失败",
        "tts.synthesis.cancelled" => "语音合成已取消",
        "tts.recording.committed" => "语音记录已保存",
        "tts.recording.failed" => "语音记录保存失败",
        "tts.playback.started" => "开始播放语音",
        "tts.playback.finished" => "语音播放完成",
        "tts.playback.stopped" => "语音播放已停止",
        "tts.playback.failed" => "语音播放失败",
        "tts.request.started" => "开始合成语音",
        "tts.request.finished" => "语音合成完成",
        "tts.request.failed" => "语音合成失败",
        "tts.service.http" => "TTS 服务请求完成",
        "tts.service.warning" => "TTS 服务发出警告",
        "tts.service.stderr" => "TTS 服务发生错误",
        "tts.service.probe" => "TTS 服务探测未就绪",
        "tts.service.probe.started" => "正在探测 TTS 服务",
        "tts.service.probe.failed" => "TTS 服务探测未就绪",
        "tts.weights.loading" => "正在加载 TTS 角色权重",
        "tts.weights.ready" => "TTS 角色权重已就绪",
        "tts.weights.failed" => "TTS 角色权重加载失败",
        "mcp.server.ready" => "MCP 服务器工具已就绪",
        "mcp.ready" => "MCP 工具已就绪",
        "mcp.config.disabled" => "MCP 未启用",
        "mcp.server.connecting" => "正在连接 MCP 服务器",
        "mcp.server.failed" => "MCP 服务器连接失败，已跳过",
        "mcp.tool.skipped" => "MCP 工具名冲突，已跳过",
        "mcp.config.failed" => "MCP 配置读取失败，已跳过",
        "mcp.tool.failed" => "MCP 工具调用失败",
        "mcp.close.failed" => "MCP 连接关闭失败",
        "mcp.close.timeout" => "MCP 连接清理超时",
        "plugin.loaded" => "插件已加载",
        "startup.window_services.created" => "窗口服务已创建",
        "startup.background_services.created" => "后台服务已创建",
        "startup.background_services.injected" => "后台服务已接入窗口",
        "core.runtime.event" => "Core 内部诊断",
        "core.process.started" => "Core 日志桥已启动",
        "core.process.stopping" => "Core 日志桥正在停止",
        "core.error.unhandled" => "Core 发生未处理错误",
        "core.log.records_dropped" => "Core 日志拥塞，部分记录已丢弃",
        "memory.initialization.stage" => "记忆模型初始化阶段已更新",
        "python.logging.info" => "Python 运行事件",
        "python.logging.warning" => "Python 运行警告",
        "python.logging.error" => "Python 运行错误",
        _ => "Core 运行事件",
    }
}

fn allowed_webview_event(event: &str) -> bool {
    matches!(
        event,
        "webview.lifecycle.ready"
            | "webview.lifecycle.unloading"
            | "webview.error.unhandled"
            | "webview.command.started"
            | "webview.command.completed"
            | "webview.command.failed"
            | "webview.command.cancelled"
            | "webview.chat.send"
            | "webview.chat.terminal"
            | "webview.settings.opened"
            | "webview.settings.closed"
            | "webview.memory.request"
            | "webview.tools.request"
            | "webview.interaction.stage"
    )
}

fn webview_message(event: &str) -> &'static str {
    match event {
        "webview.lifecycle.ready" => "界面已就绪",
        "webview.lifecycle.unloading" => "界面正在卸载",
        "webview.error.unhandled" => "界面发生未处理错误",
        "webview.command.started" => "界面命令开始",
        "webview.command.completed" => "界面命令完成",
        "webview.command.failed" => "界面命令失败",
        "webview.command.cancelled" => "界面命令已取消",
        "webview.chat.send" => "对话请求已接收",
        "webview.chat.terminal" => "对话请求已结束",
        "webview.settings.opened" => "设置窗口已打开",
        "webview.settings.closed" => "设置窗口已关闭",
        "webview.memory.request" => "记忆设置请求",
        "webview.tools.request" => "工具设置请求",
        _ => "界面交互阶段",
    }
}

fn environment_secrets() -> Vec<String> {
    let mut values = std::env::vars_os()
        .map(|(_, value)| value.to_string_lossy().into_owned())
        .filter(|value| (8..=4096).contains(&value.len()))
        .collect::<Vec<_>>();
    values.sort_by_key(|value| std::cmp::Reverse(value.len()));
    values.dedup();
    values.truncate(512);
    values
}

fn create_run_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    format!("r-{:08x}-{nanos:032x}", std::process::id())
}

fn local_clock_timestamp() -> String {
    let now = time::OffsetDateTime::now_utc();
    let local = time::UtcOffset::current_local_offset()
        .map(|offset| now.to_offset(offset))
        .unwrap_or(now);
    format!(
        "{:02}:{:02}:{:02}",
        local.hour(),
        local.minute(),
        local.second()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "sakura-wp-4l-01-{label}-{}-{nonce}",
            std::process::id()
        ))
    }

    fn test_config(path: PathBuf) -> RuntimeLogConfig {
        RuntimeLogConfig {
            path,
            queue_capacity: 16,
            max_record_bytes: 4096,
            max_file_bytes: 1024 * 1024,
            backup_count: 2,
            flush_interval: Duration::from_millis(5),
            level: Verbosity::Trace,
        }
    }

    #[test]
    fn wp_4l_02_plain_text_log_is_appended_without_json_archive() {
        let root = temp_root("plain-text-existing");
        let path = root.join("data/logs/sakura-runtime.log");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "[20:00:00] [APP] 已有纯文本日志\n").unwrap();

        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Info,
                "shell",
                "shell.started",
                "Runtime shell started",
            )
            .attributes(json!({"current_version": "1.2.3"})),
        ));
        assert!(log.shutdown(Duration::from_millis(500)));

        let contents = fs::read_to_string(&path).unwrap();
        assert_eq!(contents.lines().count(), 2);
        assert!(contents.contains("已有纯文本日志"));
        assert!(contents.contains("[APP]"));
        assert!(contents.contains("current_version=1.2.3"));
        assert!(!fs::read_dir(path.parent().unwrap())
            .unwrap()
            .filter_map(Result::ok)
            .any(|entry| entry
                .file_name()
                .to_str()
                .is_some_and(|name| name.starts_with("sakura-runtime-jsonl-archive-"))));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_core_api_failure_uses_the_console_shape_and_safe_fields() {
        let root = temp_root("human-api-failure");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"error","verbosity":"error","channel":"api","event":"api.request.failed","message":"ignored","attributes":{"status":400,"elapsed_ms":2789}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(line.starts_with('['));
        assert!(line.contains("] [API]"));
        assert!(line.contains("status=400 elapsed_ms=2789ms\n"));
        assert!(!line.trim_start().starts_with('{'));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_api_failure_preserves_safe_provider_diagnostic() {
        let root = temp_root("human-api-diagnostic");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"warning","verbosity":"warn","channel":"api","event":"api.request.failed","message":"ignored","trace_id":"17","attributes":{"model_call":2,"status":401,"provider_error_type":"authentication_error","provider_error_code":"invalid_api_key","diagnostic":"Invalid authentication credentials","elapsed_ms":2789,"attempt":1,"retryable":false}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(line.contains("[API]"));
        assert!(line.contains("trace=17 call=2 status=401"));
        assert!(line.contains("provider_error_type=authentication_error"));
        assert!(line.contains("provider_error_code=invalid_api_key"));
        assert!(line.contains("diagnostic=Invalid authentication credentials"));
        assert!(line.contains("elapsed_ms=2789ms attempt=1 retryable=false"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_business_chain_renders_correlation_and_event_specific_fields() {
        let root = temp_root("business-chain");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"info","verbosity":"info","channel":"context","event":"context.prompt.prepared","message":"ignored","operation_id":"chat-1234567890","trace_id":"17","attributes":{"model_call":2,"purpose":"agent_step","history_messages":8,"memories":3,"tool_count":18,"estimated_tokens":11684,"model":"example-model"}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(line.contains("[CONTEXT]"));
        assert!(line.contains("op=chat-123 trace=17 call=2 purpose=agent_step"));
        assert!(line.contains("history=8 memories=3 tools=18 estimated_tokens=11684"));
        assert!(!line.contains("ignored"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_forwarded_tts_request_renders_safe_business_fields() {
        let root = temp_root("forwarded-tts-request");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"info","verbosity":"info","channel":"tts","event":"tts.request.started","message":"ignored","attributes":{"provider":"gpt_sovits","text_chars":41,"attempt":1}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(
            line.contains("] [TTS] 开始合成语音 │ provider=gpt_sovits text_chars=41 attempt=1\n")
        );
        assert!(!line.contains("ignored"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_forwarded_tts_failure_preserves_provider_diagnostic() {
        let root = temp_root("forwarded-tts-failure");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"warning","verbosity":"warn","channel":"tts","event":"tts.synthesis.failed","message":"ignored","attributes":{"provider":"sakura.tts.gpt-sovits","provider_error_code":"TTS_RUNTIME_PYTHON_MISSING","code":"TTS_SYNTHESIS_FAILED","stage":"python","error_type":"RuntimeConfigurationError"}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(line.contains("provider=sakura.tts.gpt-sovits"));
        assert!(line.contains("provider_error_code=TTS_RUNTIME_PYTHON_MISSING"));
        assert!(line.contains("stage=python error_type=RuntimeConfigurationError"));
        assert!(!line.contains("ignored"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_prompt_dependency_degradation_preserves_safe_root_cause() {
        let root = temp_root("prompt-dependency-degraded");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-17".to_string(),
            generation_number: 17,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"warning","verbosity":"warn","channel":"context","event":"context.dependencies.degraded","message":"ignored","operation_id":"chat-1234567890","attributes":{"dependency":"memory","status":"degraded","reason_code":"PROCESS_EXITED","stage":"process_exit","category":"process_exited","error_type":"ChildProcessExit","elapsed_ms":5021}}"#,
                &context,
            )
            .unwrap());
        assert!(log.shutdown(Duration::from_millis(500)));
        let line = fs::read_to_string(path).unwrap();
        assert!(line.contains("[CONTEXT]"));
        assert!(line.contains("op=chat-123 dependency=memory stage=process_exit status=degraded"));
        assert!(line.contains("reason_code=PROCESS_EXITED elapsed_ms=5021ms"));
        assert!(line.contains("category=process_exited"));
        assert!(line.contains("error_type=ChildProcessExit"));
        assert!(!line.contains("ignored"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_human_elapsed_time_hides_floating_point_noise() {
        assert_eq!(
            format_human_summary(
                "webview.command.completed",
                Some(&json!({
                    "elapsed_ms": 1.799999998882413,
                    "command_elapsed_ms": 12.3456,
                    "status": "ready",
                }))
            ),
            "status=ready elapsed_ms=1.8ms command_elapsed_ms=12.35ms"
        );
    }

    #[test]
    fn legacy_tts_copy_summary_preserves_post_scan_comparison() {
        assert_eq!(
            format_human_summary(
                "legacy_import.tts_copy_failed",
                Some(&json!({
                    "detail_stage": "post_scan",
                    "copy_method": "robocopy",
                    "expected_files": 120,
                    "expected_bytes": 4096,
                    "actual_files": 119,
                    "actual_bytes": 4000,
                }))
            ),
            "detail_stage=post_scan copy_method=robocopy expected_files=120 expected_bytes=4096 actual_files=119 actual_bytes=4000"
        );
    }

    #[test]
    fn wp_4l_02_generic_webview_success_is_debug_but_failure_stays_warning() {
        let root = temp_root("webview-noise");
        let log = RuntimeLogService::start_with_config(test_config(root.join("runtime.log")));
        let completed = serde_json::from_value(json!({
            "level": "info",
            "event": "webview.command.completed",
            "command": "runtime_lifecycle_snapshot",
            "outcome": "completed",
            "elapsedMs": 1.799999998882413,
        }))
        .unwrap();
        let failed = serde_json::from_value(json!({
            "level": "warn",
            "event": "webview.command.failed",
            "command": "runtime_lifecycle_snapshot",
            "outcome": "failed",
            "elapsedMs": 12.5,
        }))
        .unwrap();

        assert_eq!(
            log.prepare_webview("main", completed).unwrap().severity,
            Severity::Debug
        );
        assert_eq!(
            log.prepare_webview("main", failed).unwrap().severity,
            Severity::Warning
        );
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_rotation_preserves_two_backups_and_bounded_records() {
        let root = temp_root("rotation");
        let path = root.join("data/logs/sakura-runtime.log");
        let mut config = test_config(path.clone());
        config.max_file_bytes = 180;
        let log = RuntimeLogService::start_with_config(config);
        for revision in 0..12 {
            assert!(log.submit(
                RuntimeLogEvent::rust(
                    Severity::Info,
                    "shell.test",
                    "shell.test.rotation",
                    "Rotation test event",
                )
                .attributes(json!({"revision": revision}))
            ));
        }
        assert!(log.shutdown(Duration::from_millis(500)));
        assert!(path.exists());
        assert!(backup_path(&path, 1).exists());
        assert!(backup_path(&path, 2).exists());
        assert!(!backup_path(&path, 3).exists());
        for candidate in [&path, &backup_path(&path, 1), &backup_path(&path, 2)] {
            for line in fs::read_to_string(candidate).unwrap().lines() {
                assert!(line.len() + 1 <= 4096);
                assert!(line.starts_with('['));
                assert!(line.contains("[APP] Rotation test event"));
            }
        }
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_queue_prefers_warning_and_counts_evicted_info() {
        let mut config = test_config(PathBuf::from("unused"));
        config.queue_capacity = 2;
        let inner = Arc::new(RuntimeLogInner {
            config,
            run_id: "test-run".to_string(),
            secrets: Vec::new(),
            state: Mutex::new(QueueState {
                records: VecDeque::new(),
                viewer_records: VecDeque::new(),
                viewer_last_evicted_sequence: None,
                next_sequence: 1,
                dropped: BTreeMap::new(),
                stopping: false,
                shutdown_deadline: None,
            }),
            wake: Condvar::new(),
            completion: Mutex::new(None),
            worker: Mutex::new(None),
            telemetry: Mutex::new(None),
        });
        let log = RuntimeLogService {
            inner: Arc::clone(&inner),
        };
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "test",
            "test.info.first",
            "Test info event",
        )));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Debug,
            "test",
            "test.info.second",
            "Test debug event",
        )));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Error,
            "test",
            "test.error.priority",
            "Test error event",
        )));
        let mut state = inner.state.lock().unwrap();
        assert_eq!(state.records.len(), 2);
        assert!(state
            .records
            .iter()
            .any(|pending| pending.severity == Severity::Error));
        assert_eq!(state.dropped.get("rust.info"), Some(&1));
        state.records.clear();
        drop(state);
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "test",
            "test.info.after_overload",
            "Test info event",
        )));
        let state = inner.state.lock().unwrap();
        assert_eq!(state.records.len(), 2);
        assert_eq!(
            state.records.front().unwrap().record.event,
            "runtime.log.records_dropped"
        );
        assert_eq!(
            state.records.front().unwrap().record.attributes,
            Some(json!({"dropped_count": 1, "counts": {"rust.info": 1}}))
        );
    }

    #[test]
    fn wp_4l_01_concurrent_producers_receive_one_monotonic_sequence() {
        let root = temp_root("sequence");
        let path = root.join("data/logs/sakura-runtime.log");
        let mut config = test_config(path.clone());
        config.queue_capacity = 512;
        let log = RuntimeLogService::start_with_config(config);
        let mut producers = Vec::new();
        for producer in 0..8 {
            let producer_log = log.clone();
            producers.push(thread::spawn(move || {
                for revision in 0..25 {
                    assert!(producer_log.submit(
                        RuntimeLogEvent::rust(
                            Severity::Info,
                            "test.concurrent",
                            "test.concurrent.record",
                            "Concurrent test event",
                        )
                        .attributes(json!({"revision": producer * 25 + revision}))
                    ));
                }
            }));
        }
        for producer in producers {
            producer.join().unwrap();
        }
        assert!(log.shutdown(Duration::from_millis(500)));

        let records = fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(str::to_string)
            .collect::<Vec<_>>();
        assert_eq!(records.len(), 200);
        assert!(records.iter().all(|line| line.starts_with('[')));
        assert!(records
            .iter()
            .all(|line| line.contains("[TEST] Concurrent test event")));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_writer_open_failure_does_not_block_submit_or_shutdown() {
        let root = temp_root("write-failure");
        fs::create_dir_all(&root).unwrap();
        let blocker = root.join("blocked");
        fs::write(&blocker, b"not a directory").unwrap();
        let path = blocker.join("sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Error,
            "test.failure",
            "test.failure.write",
            "Writer failure test event",
        )));
        assert!(log.shutdown(Duration::from_millis(500)));
        assert_eq!(fs::read(&blocker).unwrap(), b"not a directory");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_persistence_sanitizer_removes_body_secret_and_absolute_path() {
        let root = temp_root("redaction");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let sentinel = "sk-WP4L01-SENTINEL-VERY-PRIVATE";
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Warning,
                "test",
                "test.redaction",
                "Redaction test event",
            )
            .correlation(Correlation {
                operation_id: Some(sentinel.to_string()),
                ..Correlation::default()
            })
            .attributes(json!({
                "content": "PRIVATE CHAT BODY",
                "api_key": sentinel,
                "status": "C:\\Users\\private\\secret.txt",
                "elapsedMs": 12,
                "gestureId": "gesture-1",
            }))
        ));
        assert!(log.shutdown(Duration::from_millis(500)));
        let contents = fs::read_to_string(path).unwrap();
        assert!(!contents.contains("PRIVATE CHAT BODY"));
        assert!(!contents.contains(sentinel));
        assert!(!contents.contains("Users"));
        assert!(contents.contains("elapsed_ms=12ms"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_core_bridge_rejects_unknown_fields_and_injects_generation() {
        let root = temp_root("bridge");
        let path = root.join("data/logs/sakura-runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let context = CoreLogContext {
            generation_id: "generation-2".to_string(),
            generation_number: 2,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"info","verbosity":"info","channel":"agent","event":"agent.turn.started","message":"ignored","operation_id":"operation-7"}"#,
                &context,
            )
            .unwrap());
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"info","verbosity":"info","channel":"agent","event":"agent.turn.started","message":"ignored","payload":"forbidden"}"#,
                &context,
            )
            .is_err());
        let credential = "81818181818181818181818181818181";
        assert!(log
            .submit_core_bridge_with_forbidden_secret(
                &format!(
                    r#"{{"severity":"info","verbosity":"info","channel":"agent","event":"agent.turn.started","message":"ignored","attributes":{{"status":"{credential}"}}}}"#
                ),
                &context,
                Some(credential),
            )
            .is_err());
        assert!(log.shutdown(Duration::from_millis(500)));
        let contents = fs::read_to_string(path).unwrap();
        assert!(contents.contains("[AGENT]"));
        assert!(!contents.contains("ignored"));
        assert!(!contents.contains(credential));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_webview_contract_rejects_extra_or_uncontrolled_data() {
        let valid: WebviewDiagnosticEntry = serde_json::from_value(json!({
            "level": "info",
            "event": "webview.command.completed",
            "command": "chat_send",
            "outcome": "completed",
            "elapsedMs": 12.5,
            "operationId": "operation-1",
            "revision": 3,
        }))
        .unwrap();
        assert_eq!(valid.command.as_deref(), Some("chat_send"));
        assert!(serde_json::from_value::<WebviewDiagnosticEntry>(json!({
            "level": "info",
            "event": "webview.command.completed",
            "arguments": {"message": "private"},
        }))
        .is_err());
    }

    #[test]
    fn wp_5_06_viewer_keeps_catalogued_info_and_every_warning() {
        let root = temp_root("viewer-visible");
        let log = RuntimeLogService::start_with_config(test_config(root.join("runtime.log")));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "shell",
            "shell.started",
            "ignored",
        )));
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Info,
                "ipc",
                "ipc.request.completed",
                "hidden transport event",
            )
            .attributes(json!({
                "command": "plugins.settings.get",
                "outcome": "completed",
                "elapsed_ms": 102,
            })),
        ));
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Warning,
                "plugin.internal",
                "plugin.private.warning",
                "用户刚刚输入了不应展示的正文",
            )
            .attributes(json!({"code": "PLUGIN_WARNING", "stage": "setup"}))
        ));

        let snapshot = log.viewer_snapshot(None).unwrap();
        assert_eq!(snapshot.schema_version, 2);
        assert_eq!(snapshot.records.len(), 3);
        assert_eq!(snapshot.records[0].event_code, "shell.started");
        assert_eq!(snapshot.records[0].message, "Sakura 已启动");
        assert_eq!(snapshot.records[0].description, None);
        assert_eq!(snapshot.records[1].event_code, "ipc.request.completed");
        assert_eq!(snapshot.records[1].message, "读取插件设置完成");
        assert_eq!(
            snapshot.records[1].details,
            vec![
                RuntimeLogViewerDetail {
                    label: "请求".to_string(),
                    value: "plugins.settings.get".to_string(),
                },
                RuntimeLogViewerDetail {
                    label: "状态".to_string(),
                    value: "completed".to_string(),
                },
                RuntimeLogViewerDetail {
                    label: "耗时".to_string(),
                    value: "102 ms".to_string(),
                },
            ]
        );
        assert_eq!(snapshot.records[2].event_code, "plugin.private.warning");
        assert_eq!(snapshot.records[2].severity, "warning");
        assert_eq!(snapshot.records[2].message, "运行过程中出现提醒");
        assert_eq!(
            snapshot.records[2].description.as_deref(),
            Some("相关工具没有正常完成，本次操作可能缺少对应结果。")
        );
        assert!(!serde_json::to_string(&snapshot)
            .unwrap()
            .contains("不应展示的正文"));
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_06_viewer_names_core_requests_in_plain_chinese() {
        let record = |event: &str, command: &str| RuntimeLogRecord {
            schema_version: 1,
            sequence: 1,
            timestamp: "12:34:56".to_string(),
            run_id: "run-test".to_string(),
            source: "rust".to_string(),
            pid: 1,
            severity: "info".to_string(),
            verbosity: "info".to_string(),
            channel: "core.ipc".to_string(),
            event: event.to_string(),
            message: "ignored".to_string(),
            generation_id: None,
            generation_number: None,
            core_pid: None,
            request_id: None,
            operation_id: None,
            action_id: None,
            trace_id: None,
            attributes: Some(json!({"command": command})),
        };

        assert_eq!(
            viewer_ipc_request_message(&record("ipc.request.started", "core.snapshot")).as_deref(),
            Some("正在读取运行状态")
        );
        assert_eq!(
            viewer_ipc_request_message(&record("ipc.request.completed", "core.snapshot"))
                .as_deref(),
            Some("读取运行状态完成")
        );
        assert_eq!(
            viewer_ipc_request_message(&record("ipc.request.cancelled", "chat.send")).as_deref(),
            Some("发送对话已取消")
        );
        assert_eq!(
            viewer_ipc_request_message(&record("ipc.request.failed", "plugins.install")).as_deref(),
            Some("安装插件失败")
        );
        assert_eq!(
            viewer_ipc_request_message(&record("ipc.request.completed", "future.command")),
            None
        );
    }

    #[test]
    fn wp_5_06_viewer_projects_safe_ordered_error_details() {
        let root = temp_root("viewer-error");
        let log = RuntimeLogService::start_with_config(test_config(root.join("runtime.log")));
        let context = CoreLogContext {
            generation_id: "generation-viewer".to_string(),
            generation_number: 1,
            core_pid: 4242,
        };
        assert!(log
            .submit_core_bridge(
                r#"{"severity":"error","verbosity":"error","channel":"api","event":"api.request.failed","message":"ignored","operation_id":"operation-1234567890","attributes":{"diagnostic":"模型服务拒绝了身份验证","code":"MODEL_REQUEST_FAILED","reason_code":"AUTHENTICATION_FAILED","stage":"request","error_type":"authentication_error","cause_type":"PermissionError","exception_site":"app.llm.api_client:request:752","failure_id":"A1B2C3D4E5","elapsed_ms":2789.25,"content":"PRIVATE CHAT BODY","path":"/private/runtime.log"}}"#,
                &context,
            )
            .unwrap());

        let record = log.viewer_snapshot(None).unwrap().records.pop().unwrap();
        assert_eq!(record.message, "模型回复请求失败");
        assert_eq!(
            record.description.as_deref(),
            Some("模型服务没有接受当前凭据，这次回复无法生成。")
        );
        assert_eq!(record.correlation_id.as_deref(), Some("op:operatio"));
        assert_eq!(
            record
                .details
                .iter()
                .map(|detail| detail.label.as_str())
                .collect::<Vec<_>>(),
            [
                "诊断",
                "错误码",
                "原因码",
                "阶段",
                "类型",
                "根因类型",
                "代码位置",
                "问题编号",
                "耗时"
            ]
        );
        let serialized = serde_json::to_string(&record).unwrap();
        assert!(!serialized.contains("PRIVATE CHAT BODY"));
        assert!(!serialized.contains("private/runtime"));
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_06_viewer_uses_specific_plain_language_before_safe_diagnostics() {
        let root = temp_root("viewer-descriptions");
        let log = RuntimeLogService::start_with_config(test_config(root.join("runtime.log")));
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Warning,
                "tts",
                "tts.service.warmup_failed",
                "ignored",
            )
            .attributes(json!({
                "reason_code": "TTS_DEVICE_PROBE_FAILED",
                "stage": "runtime_start",
                "error_type": "RuntimePreparationError",
                "provider": "sakura.tts.gpt-sovits",
            })),
        ));
        assert!(log.submit(
            RuntimeLogEvent::rust(
                Severity::Warning,
                "appearance",
                "appearance.input_visual_effect.degraded",
                "ignored",
            )
            .attributes(json!({
                "diagnostic": "os_build=22631 advanced_effects_enabled=false",
                "code": "WINDOWS_ADVANCED_EFFECTS_DISABLED",
                "reason_code": "WINDOWS_ADVANCED_EFFECTS_DISABLED",
                "stage": "windows_input_glass",
                "status": "degraded",
            })),
        ));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Warning,
            "custom",
            "custom.warning",
            "ignored",
        )));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Error,
            "custom",
            "custom.failure",
            "ignored",
        )));

        let records = log.viewer_snapshot(None).unwrap().records;
        assert_eq!(records[0].message, "GPT-SoVITS 服务启动失败");
        assert_eq!(
            records[0].description.as_deref(),
            Some("语音服务启动时没能确认可用设备，暂时不能生成语音。")
        );
        assert_eq!(records[1].message, "输入栏视觉效果已降级");
        assert_eq!(
            records[1].description.as_deref(),
            Some("Windows 已关闭高级视觉效果，输入栏会改用普通背景。这不影响聊天和输入。")
        );
        assert!(!records[1]
            .description
            .as_deref()
            .unwrap()
            .contains("os_build"));
        assert_eq!(
            records[2].description.as_deref(),
            Some("这项功能没有按预期工作，Sakura 仍在运行。")
        );
        assert_eq!(
            records[3].description.as_deref(),
            Some("这项操作没有正常完成。")
        );
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_06_gpt_sovits_lifecycle_is_plain_language_and_hides_warmup_queue() {
        let root = temp_root("viewer-gpt-lifecycle");
        let path = root.join("runtime.log");
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        let provider = "sakura.tts.gpt-sovits";
        assert!(!viewer_event_is_visible(
            "tts.service.warmup_queued",
            Severity::Info
        ));
        assert!(viewer_event_is_visible(
            "tts.service.warmup_queued",
            Severity::Warning
        ));
        for (event, attributes) in [
            (
                "tts.service.warmup_queued",
                json!({"provider": provider, "status": "queued"}),
            ),
            (
                "tts.service.started",
                json!({"provider": provider, "stage": "runtime_start", "status": "starting"}),
            ),
            (
                "tts.service.waiting_ready",
                json!({"provider": provider, "stage": "runtime_start", "status": "waiting"}),
            ),
            (
                "tts.service.ready",
                json!({"provider": provider, "stage": "runtime_start", "status": "ready", "elapsed_ms": "12400"}),
            ),
            (
                "tts.weights.loading",
                json!({"provider": provider, "stage": "weights", "status": "loading"}),
            ),
            (
                "tts.weights.ready",
                json!({"provider": provider, "stage": "weights", "status": "ready", "elapsed_ms": "4100"}),
            ),
        ] {
            assert!(log.submit(
                RuntimeLogEvent::rust(Severity::Info, "tts", event, "ignored")
                    .attributes(attributes),
            ));
        }

        let records = log.viewer_snapshot(None).unwrap().records;
        assert_eq!(records.len(), 5);
        assert_eq!(
            records
                .iter()
                .map(|record| record.message.as_str())
                .collect::<Vec<_>>(),
            [
                "正在启动 GPT-SoVITS 服务",
                "GPT-SoVITS 进程已启动，正在等待服务就绪",
                "GPT-SoVITS 服务已就绪",
                "正在加载角色语音模型",
                "角色语音模型已就绪",
            ]
        );
        assert_eq!(records[0].scopes, ["tts"]);
        assert_eq!(records[3].scopes, ["software", "tts"]);
        assert_eq!(
            records[2].details,
            [
                RuntimeLogViewerDetail {
                    label: "阶段".to_string(),
                    value: "启动服务".to_string(),
                },
                RuntimeLogViewerDetail {
                    label: "状态".to_string(),
                    value: "已就绪".to_string(),
                },
                RuntimeLogViewerDetail {
                    label: "耗时".to_string(),
                    value: "12.4 秒".to_string(),
                },
                RuntimeLogViewerDetail {
                    label: "服务".to_string(),
                    value: "GPT-SoVITS".to_string(),
                },
            ]
        );
        assert!(log.shutdown(Duration::from_millis(500)));
        assert!(fs::read_to_string(path)
            .unwrap()
            .contains("TTS 服务预热已排队"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_06_gpt_sovits_failures_have_specific_plain_language_reasons() {
        let root = temp_root("viewer-gpt-failures");
        let log = RuntimeLogService::start_with_config(test_config(root.join("runtime.log")));
        let provider = "sakura.tts.gpt-sovits";
        assert!(log.submit(
            RuntimeLogEvent::rust(Severity::Warning, "tts", "tts.service.failed", "ignored",)
                .attributes(json!({
                    "provider": provider,
                    "reason_code": "TTS_RUNTIME_TIMEOUT",
                    "stage": "runtime_start",
                    "status": "failed",
                    "elapsed_ms": "60200",
                })),
        ));
        assert!(log.submit(
            RuntimeLogEvent::rust(Severity::Warning, "tts", "tts.weights.failed", "ignored",)
                .attributes(json!({
                    "provider": provider,
                    "reason_code": "TTS_WEIGHTS_UNAVAILABLE",
                    "stage": "sovits_weights",
                    "status": "failed",
                    "elapsed_ms": "4100",
                })),
        ));

        let records = log.viewer_snapshot(None).unwrap().records;
        assert_eq!(records[0].message, "GPT-SoVITS 服务启动失败");
        assert_eq!(
            records[0].description.as_deref(),
            Some("等待 GPT-SoVITS 服务响应超时，语音暂时不可用。")
        );
        assert_eq!(records[1].message, "角色语音模型加载失败");
        assert_eq!(
            records[1].description.as_deref(),
            Some("SoVITS 角色语音权重加载失败，文字回复仍可使用。")
        );
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn release_business_catalog_projects_first_run_core_and_migration_events() {
        for event in [
            "first_run.state.loaded",
            "first_run.onboarding.opened",
            "first_run.configuration.completed",
            "first_run.configuration.failed",
            "first_run.core_start.started",
            "first_run.core_start.completed",
            "first_run.core_start.failed",
            "legacy_import.recovery.completed",
            "legacy_import.recovery.failed",
            "legacy_import.started",
            "legacy_import.memory_snapshot_failed",
            "legacy_import.tts_copy_failed",
            "settings.provider_model.slot_save_failed",
            "tts.settings.partial",
        ] {
            let message = business_message(event)
                .unwrap_or_else(|| panic!("missing business message for {event}"));
            assert!(message
                .chars()
                .any(|character| ('\u{4e00}'..='\u{9fff}').contains(&character)));
            assert!(viewer_event_is_visible(event, Severity::Info));
            assert_eq!(viewer_message(event, Severity::Info), message);
        }
    }

    #[test]
    fn wp_5_06_viewer_ring_reports_reset_and_preserves_tts_scopes() {
        let root = temp_root("viewer-ring");
        let mut config = test_config(root.join("runtime.log"));
        config.queue_capacity = 512;
        let log = RuntimeLogService::start_with_config(config);
        for index in 0..=RUNTIME_LOG_VIEWER_CAPACITY {
            assert!(log.submit(
                RuntimeLogEvent::rust(Severity::Info, "shell", "shell.started", "ignored",)
                    .attributes(json!({"revision": index}))
            ));
        }
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "tts",
            "tts.synthesis.finished",
            "ignored",
        )));

        let initial = log.viewer_snapshot(None).unwrap();
        assert_eq!(initial.records.len(), RUNTIME_LOG_VIEWER_CAPACITY);
        assert_eq!(initial.records.last().unwrap().scopes, ["software", "tts"]);
        let reset = log.viewer_snapshot(Some(1)).unwrap();
        assert!(reset.reset_required);
        assert_eq!(reset.records.len(), RUNTIME_LOG_VIEWER_CAPACITY);
        let incremental = log
            .viewer_snapshot(Some(initial.latest_sequence.saturating_sub(1)))
            .unwrap();
        assert!(!incremental.reset_required);
        assert_eq!(incremental.records.len(), 1);
        assert!(log.shutdown(Duration::from_millis(500)));
        let _ = fs::remove_dir_all(root);
    }
}
