use std::{
    collections::{BTreeMap, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufWriter, Read, Write},
    path::{Path, PathBuf},
    sync::{mpsc, Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

pub const CORE_BRIDGE_PREFIX: &str = "SAKURA_RUNTIME_LOG_V1\t";
pub const PRODUCTION_QUEUE_CAPACITY: usize = 1024;
pub const PRODUCTION_MAX_RECORD_BYTES: usize = 4 * 1024;
pub const PRODUCTION_MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
pub const PRODUCTION_BACKUP_COUNT: usize = 5;
pub const PRODUCTION_FLUSH_INTERVAL: Duration = Duration::from_millis(250);
pub const PRODUCTION_SHUTDOWN_TIMEOUT: Duration = Duration::from_millis(500);

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
}

#[derive(Debug)]
struct QueueState {
    records: VecDeque<PendingRecord>,
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
                next_sequence: 1,
                dropped: BTreeMap::new(),
                stopping: false,
                shutdown_deadline: None,
            }),
            wake: Condvar::new(),
            completion: Mutex::new(Some(completion)),
            worker: Mutex::new(None),
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
        state.records.push_back(normalized);
        self.inner.wake.notify_one();
        true
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
        let severity = match entry.level.as_str() {
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
        let message = sanitize_fixed_message(&event.message);
        let correlation = sanitize_correlation(event.correlation, &self.inner.secrets);
        PendingRecord {
            severity: event.severity,
            record: RuntimeLogRecord {
                schema_version: 2,
                timestamp: local_clock_timestamp(),
                run_id: self.inner.run_id.clone(),
                sequence: 0,
                source: event.source.as_str().to_string(),
                pid: event.pid,
                severity: event.severity.as_str().to_string(),
                verbosity: event.verbosity.as_str().to_string(),
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
            schema_version: 2,
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
    state.records.push_back(pending);
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
    legacy_archive_checked: bool,
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
            legacy_archive_checked: false,
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
        if !self.legacy_archive_checked {
            archive_legacy_jsonl_group(&self.path, self.backup_count)?;
            self.legacy_archive_checked = true;
        }
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
    let channel = display_channel(&record.channel);
    let message = human_message(&record.event, &record.message);
    let summary = format_human_summary(record.attributes.as_ref());
    let mut text = format!("[{}] [{channel}] {message}", record.timestamp);
    if !summary.is_empty() {
        text.push_str(" │ ");
        text.push_str(&summary);
    }
    text.push('\n');
    Some(truncate_utf8_line(text, max_bytes.max(64)))
}

fn display_channel(channel: &str) -> String {
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
        _ => fallback,
    }
}

fn format_human_summary(attributes: Option<&Value>) -> String {
    const PRIORITY: [&str; 35] = [
        "stage",
        "detail_stage",
        "status",
        "outcome",
        "code",
        "elapsed_ms",
        "command_elapsed_ms",
        "event_delay_ms",
        "count",
        "dropped_count",
        "bytes",
        "lines",
        "items",
        "attempt",
        "tool_name",
        "command",
        "request",
        "action",
        "category",
        "component",
        "source",
        "scope",
        "risk",
        "failed",
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
    let Some(object) = attributes.and_then(Value::as_object) else {
        return String::new();
    };
    let mut parts = Vec::new();
    for wanted in PRIORITY {
        let Some((_, value)) = object
            .iter()
            .find(|(key, value)| normalize_key(key) == wanted && is_human_scalar(value))
        else {
            continue;
        };
        let rendered = match value {
            Value::String(value) => value.clone(),
            _ => value.to_string(),
        };
        let suffix = if wanted.ends_with("_ms") { "ms" } else { "" };
        parts.push(format!("{wanted}={rendered}{suffix}"));
        if parts.len() >= 5 {
            break;
        }
    }
    parts.join(" ")
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

fn archive_legacy_jsonl_group(path: &Path, backup_count: usize) -> Result<(), ()> {
    let candidates = std::iter::once(path.to_path_buf())
        .chain((1..=backup_count).map(|index| backup_path(path, index)))
        .filter(|candidate| candidate.exists())
        .collect::<Vec<_>>();
    if !candidates
        .iter()
        .any(|candidate| looks_like_jsonl(candidate))
    {
        return Ok(());
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis());
    for (index, source) in candidates.into_iter().enumerate() {
        let suffix = if index == 0 {
            String::new()
        } else {
            format!(".{index}")
        };
        let mut collision = 0_u32;
        loop {
            let collision_suffix = (collision > 0)
                .then(|| format!("-{collision}"))
                .unwrap_or_default();
            let target = path.with_file_name(format!(
                "sakura-runtime-jsonl-archive-{nonce}{collision_suffix}.log{suffix}"
            ));
            if !target.exists() {
                fs::rename(&source, target).map_err(|_| ())?;
                break;
            }
            collision = collision.saturating_add(1);
        }
    }
    Ok(())
}

fn looks_like_jsonl(path: &Path) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let mut prefix = [0_u8; 4096];
    let Ok(size) = file.read(&mut prefix) else {
        return false;
    };
    prefix[..size]
        .iter()
        .copied()
        .find(|byte| !byte.is_ascii_whitespace())
        == Some(b'{')
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
            | "attempt"
            | "bytes"
            | "category"
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
            | "detail_stage"
            | "dropped_bytes"
            | "dropped_count"
            | "dropped_records"
            | "elapsed_ms"
            | "epoch_ms"
            | "event_delay_ms"
            | "event_perf_ms"
            | "eof"
            | "error_type"
            | "failed"
            | "forced"
            | "gesture_id"
            | "host_state"
            | "items"
            | "lines"
            | "model_cached"
            | "operation"
            | "outcome"
            | "perf_ms"
            | "process_ms"
            | "process_alive"
            | "read_failed"
            | "record_bytes"
            | "record_truncated"
            | "request"
            | "received_epoch_ms"
            | "received_process_ms"
            | "revision"
            | "risk"
            | "scope"
            | "source"
            | "stage"
            | "status"
            | "tool_name"
            | "tree_empty"
            | "truncated"
            | "truncated_records"
            | "window_generation"
            | "window_label"
            | "wait"
    )
}

fn normalize_key(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect::<String>()
        .replace("deadlinems", "deadline_ms")
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
        .replace("gestureid", "gesture_id")
        .replace("hoststate", "host_state")
        .replace("childpid", "child_pid")
        .replace("modelcached", "model_cached")
        .replace("operationid", "operation")
        .replace("perfms", "perf_ms")
        .replace("processms", "process_ms")
        .replace("processalive", "process_alive")
        .replace("readfailed", "read_failed")
        .replace("recordbytes", "record_bytes")
        .replace("recordtruncated", "record_truncated")
        .replace("toolname", "tool_name")
        .replace("treeempty", "tree_empty")
        .replace("truncatedrecords", "truncated_records")
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

fn looks_absolute_path(value: &str) -> bool {
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
        "api.request.started" => "发送模型请求",
        "api.request.finished" => "模型请求成功",
        "api.request.failed" => "模型请求失败",
        "api.response.received" => "收到模型回复",
        "tool.execution.waiting_confirmation" => "工具等待确认",
        "tool.execution.finished" => "工具执行完成",
        "tool.execution.failed" => "工具执行失败",
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
        "webview.chat.send" => "聊天请求已提交",
        "webview.chat.terminal" => "聊天请求已结束",
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
    fn wp_4l_02_legacy_jsonl_is_archived_before_plain_text_log_is_created() {
        let root = temp_root("mixed");
        let path = root.join("data/logs/sakura-runtime.log");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"{\"timestamp\":\"legacy\",\"event\":\"old\"}\n").unwrap();
        let log = RuntimeLogService::start_with_config(test_config(path.clone()));
        assert!(log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "shell",
            "shell.started",
            "Runtime shell started",
        )));
        assert!(log.shutdown(Duration::from_millis(500)));
        let contents = fs::read_to_string(&path).unwrap();
        assert_eq!(contents.lines().count(), 1);
        assert!(contents.contains("[APP] Sakura 已启动"));
        assert!(!contents.contains("legacy"));
        let archives = fs::read_dir(path.parent().unwrap())
            .unwrap()
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|candidate| {
                candidate
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("sakura-runtime-jsonl-archive-"))
            })
            .collect::<Vec<_>>();
        assert_eq!(archives.len(), 1);
        assert_eq!(
            fs::read_to_string(&archives[0]).unwrap(),
            "{\"timestamp\":\"legacy\",\"event\":\"old\"}\n"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_core_api_failure_uses_legacy_console_shape_and_chinese_message() {
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
        assert!(line.contains("] [API] 模型请求失败 │ status=400 elapsed_ms=2789ms\n"));
        assert!(!line.trim_start().starts_with('{'));
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
                next_sequence: 1,
                dropped: BTreeMap::new(),
                stopping: false,
                shutdown_deadline: None,
            }),
            wake: Condvar::new(),
            completion: Mutex::new(None),
            worker: Mutex::new(None),
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
        assert!(contents.contains("[AGENT] 开始处理用户消息"));
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
}
