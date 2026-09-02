use std::{
    collections::{BTreeSet, VecDeque},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;

use crate::{runtime_log::CoreLogContext, ui_config::UiConfigRepository};

pub const TELEMETRY_CORE_BRIDGE_PREFIX: &str = "SAKURA_TELEMETRY_V1\t";
pub const TELEMETRY_ENDPOINT: &str = "https://telemetry.cialloo.cn";
pub const TELEMETRY_DOCUMENTATION_URL: &str =
    "https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/REMOTE_DIAGNOSTICS_AND_TELEMETRY.md";
const TELEMETRY_NAMESPACE: &str = "TELEMETRY_SETTINGS";
const QUEUE_CAPACITY: usize = 128;
const HTTP_TIMEOUT: Duration = Duration::from_millis(2_500);
const ERROR_BODY_LIMIT: usize = 32 * 1024;
const EVENT_BODY_LIMIT: usize = 8 * 1024;
const MODEL_CALL_BODY_LIMIT: usize = 16 * 1024;

pub fn open_documentation() -> Result<(), String> {
    crate::update_settings::open_https_url(
        TELEMETRY_DOCUMENTATION_URL,
        "TELEMETRY_DOCUMENTATION_OPEN_FAILED",
    )
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TelemetrySettingsSnapshot {
    pub schema_version: u32,
    pub enabled: bool,
    pub installation_id: Option<String>,
}

#[derive(Clone)]
pub struct TelemetryService {
    inner: Arc<TelemetryInner>,
}

struct TelemetryInner {
    repository: UiConfigRepository,
    run_id: String,
    endpoint: String,
    http_timeout: Duration,
    enabled: AtomicBool,
    epoch: AtomicU64,
    stopping: AtomicBool,
    runtime: Mutex<TelemetryRuntimeState>,
    breadcrumbs: Mutex<VecDeque<BreadcrumbState>>,
    features: Mutex<BTreeSet<String>>,
    warning_reports: Mutex<BTreeSet<(String, String, String)>>,
    sender: mpsc::Sender<QueuedRecord>,
    control: watch::Sender<u64>,
    diagnostics: Mutex<SenderDiagnostics>,
    started_at: Instant,
}

#[derive(Debug)]
struct TelemetryRuntimeState {
    installation_id: Option<String>,
    settings_error: Option<String>,
    active_generation: Option<String>,
}

#[derive(Debug, Default)]
struct SenderDiagnostics {
    dropped: u64,
    failed: u64,
    rejected: u64,
}

#[derive(Clone, Debug)]
struct BreadcrumbState {
    elapsed_ms: u64,
    source: String,
    severity: String,
    channel: String,
    event: String,
    code: Option<String>,
    outcome: Option<String>,
    duration_ms: Option<u64>,
}

#[derive(Debug)]
struct QueuedRecord {
    epoch: u64,
    record: TelemetryRecord,
}

#[derive(Debug)]
enum TelemetryRecord {
    Error(ErrorReport),
    Event(RuntimeEventItem),
    ModelCall(ModelCallItem),
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorReport {
    schema: u8,
    report_id: String,
    installation_id: String,
    run_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    operation_id: Option<String>,
    app: ErrorApp,
    system: ErrorSystem,
    error: ErrorDescriptor,
    #[serde(skip_serializing_if = "Option::is_none")]
    context: Option<ErrorContext>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    stack: Vec<SafeStackFrame>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    breadcrumbs: Vec<Breadcrumb>,
}

#[derive(Clone, Debug, Serialize)]
struct ErrorApp {
    version: String,
    channel: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorSystem {
    platform: String,
    os_version: String,
    arch: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    webview_version: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorDescriptor {
    component: String,
    event: String,
    code: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    exception_type: Option<String>,
    fingerprint: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorContext {
    install_kind: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SafeStackFrame {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    module: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    function: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    line: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Breadcrumb {
    offset_ms: i64,
    source: String,
    severity: String,
    channel: String,
    event: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    outcome: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    elapsed_ms: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeEventItem {
    installation_id: String,
    run_id: String,
    app_version: String,
    platform: String,
    os_version: String,
    arch: String,
    event: String,
    feature: Option<String>,
    duration_ms: Option<u64>,
    from_version: Option<String>,
    to_version: Option<String>,
    error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CoreTelemetryEnvelope {
    kind: String,
    #[serde(default)]
    error: Option<TelemetryErrorCandidateV1>,
    #[serde(default)]
    model_call: Option<TelemetryModelCallMetricV1>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TelemetryErrorCandidateV1 {
    schema: u8,
    component: String,
    event: String,
    code: String,
    #[serde(default)]
    operation_id: Option<String>,
    #[serde(default)]
    exception_type: Option<String>,
    #[serde(default)]
    stack: Vec<SafeStackFrame>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TelemetryModelCallMetricV1 {
    schema: u8,
    operation_id: Option<String>,
    model_call: u64,
    purpose: String,
    model_family: String,
    outcome: String,
    error_code: Option<String>,
    latency_ms: u64,
    context_window_tokens: u64,
    context_window_source: String,
    usage: Option<TokenUsage>,
    estimate: Option<ContextEstimate>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TokenUsage {
    prompt_tokens: Option<u64>,
    completion_tokens: Option<u64>,
    total_tokens: Option<u64>,
    input_tokens: Option<u64>,
    output_tokens: Option<u64>,
    cached_input_tokens: Option<u64>,
    reasoning_tokens: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ContextEstimate {
    request_tokens: u64,
    history_tokens: u64,
    memory_tokens: u64,
    dynamic_context_tokens: u64,
    tool_schema_tokens: u64,
    history_messages: u64,
    memories: u64,
    tool_count: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelCallItem {
    installation_id: String,
    run_id: String,
    operation_id: Option<String>,
    app_version: String,
    model_call: u64,
    purpose: String,
    model_family: String,
    outcome: String,
    error_code: Option<String>,
    latency_ms: u64,
    context_window_tokens: u64,
    context_window_source: String,
    usage: Option<TokenUsage>,
    estimate: Option<ContextEstimate>,
}

impl TelemetryService {
    pub fn initialize(repository: UiConfigRepository, run_id: String) -> Self {
        Self::initialize_with_options(
            repository,
            run_id,
            TELEMETRY_ENDPOINT.to_string(),
            QUEUE_CAPACITY,
            HTTP_TIMEOUT,
        )
    }

    fn initialize_with_options(
        repository: UiConfigRepository,
        run_id: String,
        endpoint: String,
        queue_capacity: usize,
        http_timeout: Duration,
    ) -> Self {
        let (sender, receiver) = mpsc::channel(queue_capacity);
        let (control, control_receiver) = watch::channel(1_u64);
        let parsed = repository
            .load(TELEMETRY_NAMESPACE)
            .and_then(|document| telemetry_config(&document));
        let (enabled, installation_id, settings_error) = match parsed {
            Ok((false, installation_id)) => (false, installation_id, None),
            Ok((true, installation_id)) => {
                match ensure_installation_id(&repository, installation_id) {
                    Ok(id) => (true, Some(id), None),
                    Err(_) => (
                        false,
                        None,
                        Some("TELEMETRY_SETTINGS_SAVE_FAILED".to_string()),
                    ),
                }
            }
            Err(_) => (false, None, Some("TELEMETRY_SETTINGS_INVALID".to_string())),
        };
        let inner = Arc::new(TelemetryInner {
            repository,
            run_id,
            endpoint,
            http_timeout,
            enabled: AtomicBool::new(enabled),
            epoch: AtomicU64::new(1),
            stopping: AtomicBool::new(false),
            runtime: Mutex::new(TelemetryRuntimeState {
                installation_id,
                settings_error,
                active_generation: None,
            }),
            breadcrumbs: Mutex::new(VecDeque::with_capacity(40)),
            features: Mutex::new(BTreeSet::new()),
            warning_reports: Mutex::new(BTreeSet::new()),
            sender,
            control,
            diagnostics: Mutex::new(SenderDiagnostics::default()),
            started_at: Instant::now(),
        });
        spawn_sender(Arc::clone(&inner), receiver, control_receiver);
        Self { inner }
    }

    pub fn snapshot(&self) -> Result<TelemetrySettingsSnapshot, String> {
        let runtime = self
            .inner
            .runtime
            .lock()
            .map_err(|_| "TELEMETRY_SETTINGS_STATE_UNAVAILABLE".to_string())?;
        if let Some(error) = runtime.settings_error.as_ref() {
            return Err(error.clone());
        }
        Ok(TelemetrySettingsSnapshot {
            schema_version: 1,
            enabled: self.inner.enabled.load(Ordering::Acquire),
            installation_id: runtime.installation_id.clone(),
        })
    }

    pub fn set_enabled(&self, enabled: bool) -> Result<TelemetrySettingsSnapshot, String> {
        if !enabled {
            self.pause();
            let saved_id = persist_telemetry(&self.inner.repository, false, None)
                .map_err(|_| "TELEMETRY_SETTINGS_SAVE_FAILED".to_string())?;
            let mut runtime = self
                .inner
                .runtime
                .lock()
                .map_err(|_| "TELEMETRY_SETTINGS_STATE_UNAVAILABLE".to_string())?;
            runtime.installation_id = saved_id;
            runtime.settings_error = None;
            return Ok(TelemetrySettingsSnapshot {
                schema_version: 1,
                enabled: false,
                installation_id: runtime.installation_id.clone(),
            });
        }

        let id = persist_telemetry(&self.inner.repository, true, None)
            .map_err(|_| "TELEMETRY_SETTINGS_SAVE_FAILED".to_string())?
            .ok_or_else(|| "TELEMETRY_SETTINGS_SAVE_FAILED".to_string())?;
        {
            let mut runtime = self
                .inner
                .runtime
                .lock()
                .map_err(|_| "TELEMETRY_SETTINGS_STATE_UNAVAILABLE".to_string())?;
            runtime.installation_id = Some(id);
            runtime.settings_error = None;
        }
        self.inner.enabled.store(true, Ordering::Release);
        self.bump_epoch();
        self.snapshot()
    }

    pub fn regenerate_installation_id(&self) -> Result<TelemetrySettingsSnapshot, String> {
        let was_enabled = self.inner.enabled.swap(false, Ordering::AcqRel);
        self.bump_epoch();
        let new_id = Uuid::new_v4().hyphenated().to_string();
        if persist_telemetry(&self.inner.repository, was_enabled, Some(&new_id)).is_err() {
            self.inner.enabled.store(was_enabled, Ordering::Release);
            self.bump_epoch();
            return Err("TELEMETRY_SETTINGS_SAVE_FAILED".to_string());
        }
        let mut runtime = self
            .inner
            .runtime
            .lock()
            .map_err(|_| "TELEMETRY_SETTINGS_STATE_UNAVAILABLE".to_string())?;
        runtime.installation_id = Some(new_id);
        runtime.settings_error = None;
        drop(runtime);
        self.inner.enabled.store(was_enabled, Ordering::Release);
        self.bump_epoch();
        self.snapshot()
    }

    pub fn shutdown(&self) {
        self.inner.stopping.store(true, Ordering::Release);
        self.pause();
    }

    pub fn activate_generation(&self, generation_id: &str) {
        if let Ok(mut runtime) = self.inner.runtime.lock() {
            runtime.active_generation = valid_token(generation_id, 128).map(str::to_string);
        }
    }

    pub fn submit_core_bridge(
        &self,
        payload: &str,
        context: &CoreLogContext,
        forbidden_secret: Option<&str>,
    ) -> Result<bool, ()> {
        if payload.len() > 8 * 1024
            || forbidden_secret.is_some_and(|secret| !secret.is_empty() && payload.contains(secret))
        {
            return Err(());
        }
        let active = self
            .inner
            .runtime
            .lock()
            .map_err(|_| ())?
            .active_generation
            .clone();
        if active.as_deref() != Some(context.generation_id.as_str()) {
            return Ok(false);
        }
        let envelope: CoreTelemetryEnvelope = serde_json::from_str(payload).map_err(|_| ())?;
        match (envelope.kind.as_str(), envelope.error, envelope.model_call) {
            ("error", Some(candidate), None) => {
                validate_core_error_candidate(&candidate)?;
                self.submit_error_candidate(candidate)
            }
            ("modelCall", None, Some(candidate)) => self.submit_model_call(candidate),
            _ => Err(()),
        }
    }

    pub fn observe_runtime_event(
        &self,
        source: &str,
        severity: &str,
        channel: &str,
        event: &str,
        operation_id: Option<&str>,
        attributes: Option<&Value>,
    ) {
        if !self.inner.enabled.load(Ordering::Acquire) {
            return;
        }
        self.push_breadcrumb(source, severity, channel, event, attributes);
        if let Some(feature) = feature_for_event(event) {
            self.submit_feature_once(feature);
        }
        if event == "legacy_import.completed" {
            self.submit_runtime_event("migration.completed", None, None, None, None, None);
        } else if matches!(
            event,
            "legacy_import.failed"
                | "legacy_import.result_invalid"
                | "legacy_import.core_validation_failed"
                | "legacy_import.recovery.failed"
        ) {
            self.submit_runtime_event(
                "migration.failed",
                None,
                None,
                None,
                stable_attribute(attributes, "code"),
                None,
            );
        }
        let report = allowlisted_runtime_error(source, event, attributes)
            .or_else(|| allowlisted_runtime_warning(source, severity, event, attributes));
        if let Some((component, code)) = report {
            if severity == "warning" && !self.reserve_warning_report(component, event, &code) {
                return;
            }
            let candidate = TelemetryErrorCandidateV1 {
                schema: 1,
                component: component.to_string(),
                event: event.to_string(),
                code,
                operation_id: operation_id
                    .and_then(|value| valid_token(value, 128).map(str::to_string)),
                exception_type: stable_token_attribute(attributes, "error_type", 128),
                stack: Vec::new(),
            };
            let _ = self.submit_error_candidate(candidate);
        }
    }

    pub fn submit_app_started(&self) {
        self.submit_runtime_event("app.started", None, None, None, None, None);
    }

    pub fn submit_app_ready(&self) {
        self.submit_runtime_event(
            "app.ready",
            None,
            Some(self.inner.started_at.elapsed().as_millis() as u64),
            None,
            None,
            None,
        );
    }

    fn pause(&self) {
        self.inner.enabled.store(false, Ordering::Release);
        self.bump_epoch();
    }

    fn bump_epoch(&self) {
        let epoch = self.inner.epoch.fetch_add(1, Ordering::AcqRel) + 1;
        let _ = self.inner.control.send(epoch);
    }

    fn installation_context(&self) -> Option<(String, u64)> {
        if !self.inner.enabled.load(Ordering::Acquire)
            || self.inner.stopping.load(Ordering::Acquire)
        {
            return None;
        }
        let epoch = self.inner.epoch.load(Ordering::Acquire);
        let installation_id = self
            .inner
            .runtime
            .lock()
            .ok()
            .and_then(|runtime| runtime.installation_id.clone())?;
        (self.inner.enabled.load(Ordering::Acquire)
            && !self.inner.stopping.load(Ordering::Acquire)
            && self.inner.epoch.load(Ordering::Acquire) == epoch)
            .then_some((installation_id, epoch))
    }

    #[cfg(test)]
    fn installation_id(&self) -> Option<String> {
        self.installation_context().map(|(id, _)| id)
    }

    fn submit_feature_once(&self, feature: &str) {
        let Ok(mut features) = self.inner.features.lock() else {
            return;
        };
        if !features.insert(feature.to_string()) {
            return;
        }
        drop(features);
        self.submit_runtime_event("feature.used", Some(feature), None, None, None, None);
    }

    fn reserve_warning_report(&self, component: &str, event: &str, code: &str) -> bool {
        let Ok(mut reports) = self.inner.warning_reports.lock() else {
            return false;
        };
        reports.insert((component.to_string(), event.to_string(), code.to_string()))
    }

    fn submit_runtime_event(
        &self,
        event: &str,
        feature: Option<&str>,
        duration_ms: Option<u64>,
        from_version: Option<&str>,
        error_code: Option<String>,
        to_version: Option<&str>,
    ) {
        let Some((installation_id, epoch)) = self.installation_context() else {
            return;
        };
        let item = RuntimeEventItem {
            installation_id,
            run_id: self.inner.run_id.clone(),
            app_version: env!("CARGO_PKG_VERSION").to_string(),
            platform: platform_name().to_string(),
            os_version: os_version(),
            arch: std::env::consts::ARCH.to_string(),
            event: event.to_string(),
            feature: feature.map(str::to_string),
            duration_ms,
            from_version: from_version.map(str::to_string),
            to_version: to_version.map(str::to_string),
            error_code,
        };
        self.enqueue_at_epoch(TelemetryRecord::Event(item), epoch);
    }

    fn submit_error_candidate(&self, candidate: TelemetryErrorCandidateV1) -> Result<bool, ()> {
        validate_error_candidate(&candidate)?;
        let Some((installation_id, epoch)) = self.installation_context() else {
            return Ok(false);
        };
        let now_ms = self.inner.started_at.elapsed().as_millis() as u64;
        let breadcrumb_state = self.inner.breadcrumbs.lock().map_err(|_| ())?;
        let breadcrumbs = project_breadcrumbs(&breadcrumb_state, now_ms);
        drop(breadcrumb_state);
        let fingerprint = stable_fingerprint(
            &candidate.component,
            &candidate.event,
            &candidate.code,
            &candidate.stack,
        );
        let report = ErrorReport {
            schema: 1,
            report_id: Uuid::new_v4().hyphenated().to_string(),
            installation_id,
            run_id: self.inner.run_id.clone(),
            operation_id: candidate.operation_id,
            app: ErrorApp {
                version: env!("CARGO_PKG_VERSION").to_string(),
                channel: release_channel().to_string(),
            },
            system: ErrorSystem {
                platform: platform_name().to_string(),
                os_version: os_version(),
                arch: std::env::consts::ARCH.to_string(),
                webview_version: tauri::webview_version()
                    .ok()
                    .and_then(|value| valid_token(&value, 128).map(str::to_string)),
            },
            error: ErrorDescriptor {
                component: candidate.component,
                event: candidate.event,
                code: candidate.code,
                exception_type: candidate.exception_type,
                fingerprint,
            },
            context: Some(ErrorContext {
                install_kind: "unknown".to_string(),
            }),
            stack: candidate.stack,
            breadcrumbs,
        };
        let encoded = serde_json::to_vec(&report).map_err(|_| ())?;
        if encoded.len() > ERROR_BODY_LIMIT {
            return Err(());
        }
        Ok(self.enqueue_at_epoch(TelemetryRecord::Error(report), epoch))
    }

    fn submit_model_call(&self, candidate: TelemetryModelCallMetricV1) -> Result<bool, ()> {
        validate_model_call(&candidate)?;
        let Some((installation_id, epoch)) = self.installation_context() else {
            return Ok(false);
        };
        let item = ModelCallItem {
            installation_id,
            run_id: self.inner.run_id.clone(),
            operation_id: candidate.operation_id,
            app_version: env!("CARGO_PKG_VERSION").to_string(),
            model_call: candidate.model_call,
            purpose: candidate.purpose,
            model_family: candidate.model_family,
            outcome: candidate.outcome,
            error_code: candidate.error_code,
            latency_ms: candidate.latency_ms,
            context_window_tokens: candidate.context_window_tokens,
            context_window_source: candidate.context_window_source,
            usage: candidate.usage,
            estimate: candidate.estimate,
        };
        Ok(self.enqueue_at_epoch(TelemetryRecord::ModelCall(item), epoch))
    }

    fn push_breadcrumb(
        &self,
        source: &str,
        severity: &str,
        channel: &str,
        event: &str,
        attributes: Option<&Value>,
    ) {
        if !matches!(source, "rust" | "core" | "webview")
            || !matches!(severity, "trace" | "debug" | "info" | "warning" | "error")
            || !valid_event_name(channel, 32)
            || !valid_event_name(event, 96)
        {
            return;
        }
        let mut ring = match self.inner.breadcrumbs.lock() {
            Ok(ring) => ring,
            Err(_) => return,
        };
        if ring.len() == 40 {
            ring.pop_front();
        }
        ring.push_back(BreadcrumbState {
            elapsed_ms: self.inner.started_at.elapsed().as_millis() as u64,
            source: source.to_string(),
            severity: if severity == "trace" {
                "debug"
            } else {
                severity
            }
            .to_string(),
            channel: channel.to_string(),
            event: event.to_string(),
            code: stable_attribute(attributes, "code")
                .or_else(|| stable_attribute(attributes, "reason_code")),
            outcome: outcome_attribute(attributes),
            duration_ms: integer_attribute(attributes, "elapsed_ms")
                .filter(|value| *value <= 86_400_000),
        });
    }

    #[cfg(test)]
    fn enqueue(&self, record: TelemetryRecord) -> bool {
        self.enqueue_at_epoch(record, self.inner.epoch.load(Ordering::Acquire))
    }

    fn enqueue_at_epoch(&self, record: TelemetryRecord, epoch: u64) -> bool {
        if !self.inner.enabled.load(Ordering::Acquire)
            || self.inner.stopping.load(Ordering::Acquire)
            || self.inner.epoch.load(Ordering::Acquire) != epoch
        {
            return false;
        }
        let queued = QueuedRecord { epoch, record };
        match self.inner.sender.try_send(queued) {
            Ok(()) => true,
            Err(_) => {
                if let Ok(mut diagnostics) = self.inner.diagnostics.lock() {
                    diagnostics.dropped = diagnostics.dropped.saturating_add(1);
                }
                false
            }
        }
    }
}

fn spawn_sender(
    inner: Arc<TelemetryInner>,
    receiver: mpsc::Receiver<QueuedRecord>,
    control: watch::Receiver<u64>,
) {
    let _ = thread::Builder::new()
        .name("sakura-telemetry-sender".to_string())
        .spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build();
            if let Ok(runtime) = runtime {
                runtime.block_on(sender_loop(inner, receiver, control));
            }
        });
}

async fn sender_loop(
    inner: Arc<TelemetryInner>,
    mut receiver: mpsc::Receiver<QueuedRecord>,
    mut control: watch::Receiver<u64>,
) {
    let client = match reqwest::Client::builder()
        .timeout(inner.http_timeout)
        .build()
    {
        Ok(client) => client,
        Err(_) => return,
    };
    let mut deferred = VecDeque::new();
    while !inner.stopping.load(Ordering::Acquire) {
        let first = if let Some(item) = deferred.pop_front() {
            item
        } else {
            loop {
                if inner.stopping.load(Ordering::Acquire) {
                    return;
                }
                if control.has_changed().unwrap_or(false) {
                    let _ = control.borrow_and_update();
                    retain_current_epoch(&inner, &mut receiver, &mut deferred);
                    if let Some(item) = deferred.pop_front() {
                        break item;
                    }
                    continue;
                }
                match tokio::time::timeout(Duration::from_millis(25), receiver.recv()).await {
                    Ok(Some(item)) => break item,
                    Ok(None) => return,
                    Err(_) => continue,
                }
            }
        };
        let epoch = inner.epoch.load(Ordering::Acquire);
        if first.epoch != epoch || !inner.enabled.load(Ordering::Acquire) {
            continue;
        }
        let endpoint = record_endpoint(&first.record);
        let mut records = vec![first.record];
        if endpoint != "/v1/errors" {
            while records.len() < 10 {
                match receiver.try_recv() {
                    Ok(next)
                        if next.epoch == epoch && record_endpoint(&next.record) == endpoint =>
                    {
                        records.push(next.record)
                    }
                    Ok(next) => {
                        deferred.push_back(next);
                        break;
                    }
                    Err(_) => break,
                }
            }
        }
        let body = match encode_records(endpoint, &records) {
            Some(body) => body,
            None => {
                if let Ok(mut diagnostics) = inner.diagnostics.lock() {
                    diagnostics.rejected =
                        diagnostics.rejected.saturating_add(records.len() as u64);
                }
                continue;
            }
        };
        let request_client = client.clone();
        let request_url = format!("{}{endpoint}", inner.endpoint);
        let request = tokio::spawn(async move {
            request_client
                .post(request_url)
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(body)
                .send()
                .await
        });
        let result = loop {
            if control.has_changed().unwrap_or(false) {
                let control_epoch = *control.borrow_and_update();
                if control_epoch != epoch
                    || !inner.enabled.load(Ordering::Acquire)
                    || inner.stopping.load(Ordering::Acquire)
                {
                    request.abort();
                    break None;
                }
            }
            if request.is_finished() {
                break request.await.ok();
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        };
        let Some(result) = result else {
            continue;
        };
        if !result.is_ok_and(|response| response.status() == reqwest::StatusCode::ACCEPTED) {
            if let Ok(mut diagnostics) = inner.diagnostics.lock() {
                diagnostics.failed = diagnostics.failed.saturating_add(records.len() as u64);
            }
        }
    }
}

fn retain_current_epoch(
    inner: &TelemetryInner,
    receiver: &mut mpsc::Receiver<QueuedRecord>,
    deferred: &mut VecDeque<QueuedRecord>,
) {
    let epoch = inner.epoch.load(Ordering::Acquire);
    let enabled = inner.enabled.load(Ordering::Acquire);
    deferred.retain(|record| record.epoch == epoch && enabled);
    if !deferred.is_empty() {
        return;
    }
    while let Ok(record) = receiver.try_recv() {
        let current_epoch = inner.epoch.load(Ordering::Acquire);
        if record.epoch == current_epoch && inner.enabled.load(Ordering::Acquire) {
            deferred.push_back(record);
            break;
        }
    }
}

fn record_endpoint(record: &TelemetryRecord) -> &'static str {
    match record {
        TelemetryRecord::Error(_) => "/v1/errors",
        TelemetryRecord::Event(_) => "/v1/events",
        TelemetryRecord::ModelCall(_) => "/v1/model-calls",
    }
}

fn encode_records(endpoint: &str, records: &[TelemetryRecord]) -> Option<Vec<u8>> {
    let (value, limit) = match endpoint {
        "/v1/errors" => {
            if records.len() != 1 {
                return None;
            }
            let TelemetryRecord::Error(report) = records.first()? else {
                return None;
            };
            (serde_json::to_value(report).ok()?, ERROR_BODY_LIMIT)
        }
        "/v1/events" => {
            if records.is_empty() || records.len() > 10 {
                return None;
            }
            let items = records
                .iter()
                .map(|record| match record {
                    TelemetryRecord::Event(item) => serde_json::to_value(item).ok(),
                    _ => None,
                })
                .collect::<Option<Vec<_>>>()?;
            (json!({"schema": 1, "items": items}), EVENT_BODY_LIMIT)
        }
        "/v1/model-calls" => {
            if records.is_empty() || records.len() > 10 {
                return None;
            }
            let items = records
                .iter()
                .map(|record| match record {
                    TelemetryRecord::ModelCall(item) => serde_json::to_value(item).ok(),
                    _ => None,
                })
                .collect::<Option<Vec<_>>>()?;
            (json!({"schema": 1, "items": items}), MODEL_CALL_BODY_LIMIT)
        }
        _ => return None,
    };
    let bytes = serde_json::to_vec(&value).ok()?;
    (bytes.len() <= limit).then_some(bytes)
}

fn telemetry_config(document: &Value) -> Result<(bool, Option<String>), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "TELEMETRY_SETTINGS_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(1)
        || root.get("domain").and_then(Value::as_str) != Some("ui")
    {
        return Err("TELEMETRY_SETTINGS_INVALID".to_string());
    }
    let settings = root
        .get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "TELEMETRY_SETTINGS_INVALID".to_string())?;
    let Some(raw) = settings.get("telemetry") else {
        return Ok((true, None));
    };
    let telemetry = raw
        .as_object()
        .ok_or_else(|| "TELEMETRY_SETTINGS_INVALID".to_string())?;
    let enabled = match telemetry.get("enabled") {
        None => true,
        Some(Value::Bool(value)) => *value,
        _ => return Err("TELEMETRY_SETTINGS_INVALID".to_string()),
    };
    let installation_id = match telemetry.get("installation_id") {
        None => None,
        Some(Value::String(value)) if valid_uuid_v4(value) => Some(value.clone()),
        _ => return Err("TELEMETRY_SETTINGS_INVALID".to_string()),
    };
    Ok((enabled, installation_id))
}

fn ensure_installation_id(
    repository: &UiConfigRepository,
    existing: Option<String>,
) -> Result<String, String> {
    if let Some(id) = existing {
        return Ok(id);
    }
    let id = Uuid::new_v4().hyphenated().to_string();
    let _ = persist_telemetry(repository, true, Some(&id))?;
    Ok(id)
}

fn persist_telemetry(
    repository: &UiConfigRepository,
    enabled: bool,
    installation_id: Option<&str>,
) -> Result<Option<String>, String> {
    let mut saved_id = None;
    repository.update(TELEMETRY_NAMESPACE, |document| {
        let root = document
            .as_object_mut()
            .ok_or_else(|| "TELEMETRY_SETTINGS_INVALID".to_string())?;
        if root.get("schema_version").and_then(Value::as_u64) != Some(1)
            || root.get("domain").and_then(Value::as_str) != Some("ui")
        {
            return Err("TELEMETRY_SETTINGS_INVALID".to_string());
        }
        let settings = root
            .get_mut("settings")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "TELEMETRY_SETTINGS_INVALID".to_string())?;
        let mut telemetry = settings
            .get("telemetry")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        telemetry.insert("enabled".to_string(), Value::Bool(enabled));
        let effective_id = if let Some(id) = installation_id {
            if !valid_uuid_v4(id) {
                return Err("TELEMETRY_SETTINGS_INVALID".to_string());
            }
            Some(id.to_string())
        } else {
            settings
                .get("telemetry")
                .and_then(Value::as_object)
                .and_then(|item| item.get("installation_id"))
                .and_then(Value::as_str)
                .filter(|value| valid_uuid_v4(value))
                .map(str::to_string)
                .or_else(|| enabled.then(|| Uuid::new_v4().hyphenated().to_string()))
        };
        if let Some(id) = effective_id.as_ref() {
            telemetry.insert("installation_id".to_string(), Value::String(id.clone()));
        } else {
            telemetry.remove("installation_id");
        }
        saved_id = effective_id;
        settings.insert("telemetry".to_string(), Value::Object(telemetry));
        Ok(())
    })?;
    Ok(saved_id)
}

fn validate_core_error_candidate(candidate: &TelemetryErrorCandidateV1) -> Result<(), ()> {
    const CORE_CODES: &[&str] = &[
        "CORE_UNHANDLED_ERROR",
        "CORE_HOST_PROTOCOL_ERROR",
        "CORE_HOST_TRANSPORT_ERROR",
        "CORE_HOST_FATAL",
    ];
    if candidate.component != "core"
        || candidate.event != "core.error.unhandled"
        || !CORE_CODES.contains(&candidate.code.as_str())
    {
        return Err(());
    }
    validate_error_candidate(candidate)
}

fn validate_error_candidate(candidate: &TelemetryErrorCandidateV1) -> Result<(), ()> {
    let allowlisted = match (candidate.component.as_str(), candidate.event.as_str()) {
        ("core", "core.error.unhandled") => matches!(
            candidate.code.as_str(),
            "CORE_UNHANDLED_ERROR"
                | "CORE_HOST_PROTOCOL_ERROR"
                | "CORE_HOST_TRANSPORT_ERROR"
                | "CORE_HOST_FATAL"
        ),
        ("rust", "shell.error.unhandled") => candidate.code == "RUST_PANIC",
        ("webview", "webview.error.unhandled") => matches!(
            candidate.code.as_str(),
            "WEBVIEW_UNHANDLED_ERROR" | "WEBVIEW_UNHANDLED_REJECTION"
        ),
        ("tts", event) => selected_tts_code(event, &candidate.code),
        ("memory", "memory.recall.failed") => matches!(
            candidate.code.as_str(),
            "MEMORY_RECALL_FAILED" | "INVALID_RESULT"
        ),
        ("memory", "memory.recall.unavailable") => candidate.code == "MEMORY_NOT_READY",
        ("memory", "memory.curation.failed") => valid_code(&candidate.code),
        ("memory", "memory.curation.request_fuse_opened") => {
            candidate.code == "CURATION_REQUEST_FUSE_OPEN"
        }
        ("context", "context.dependencies.degraded") => valid_code(&candidate.code),
        ("reply", "reply.processing.failed") => matches!(
            candidate.code.as_str(),
            "REPLY_PROCESSING_FALLBACK" | "REPLY_REPAIR_REQUEST_FAILED"
        ),
        ("screen", "screen.capture.failed") => {
            candidate.code.starts_with("SCREEN_") && valid_code(&candidate.code)
        }
        ("mcp", "mcp.config.failed") => matches!(
            candidate.code.as_str(),
            "MCP_CONFIG_LOAD_FAILED" | "CONFIG_INVALID"
        ),
        ("mcp", "mcp.server.failed") => matches!(
            candidate.code.as_str(),
            "COMMAND_NOT_FOUND" | "COMMAND_NOT_EXECUTABLE" | "TIMEOUT" | "TRANSPORT_FAILED"
        ),
        ("mcp", "mcp.close.failed") => candidate.code == "CLOSE_FAILED",
        ("mcp", "mcp.close.timeout") => candidate.code == "CLOSE_TIMEOUT",
        ("rust", "legacy_import.recovery.failed") => {
            candidate.code == "LEGACY_IMPORT_RECOVERY_FAILED"
        }
        ("rust", "first_run.state.failed") => candidate.code == "FIRST_RUN_STATE_FAILED",
        ("rust", "core.spawn.failed") => candidate.code == "CORE_UNEXPECTED_EXIT",
        (
            "rust",
            "legacy_import.failed"
            | "legacy_import.core_validation_failed"
            | "legacy_import.result_invalid",
        ) => valid_code(&candidate.code),
        _ => false,
    };
    if candidate.schema != 1
        || !allowlisted
        || candidate
            .operation_id
            .as_deref()
            .is_some_and(|value| valid_token(value, 128).is_none())
        || candidate
            .exception_type
            .as_deref()
            .is_some_and(|value| valid_token(value, 128).is_none())
        || candidate.stack.len() > 16
        || (!candidate.stack.is_empty() && candidate.component != "core")
        || candidate
            .stack
            .iter()
            .any(|frame| !valid_stack_frame(frame))
    {
        return Err(());
    }
    Ok(())
}

fn validate_model_call(candidate: &TelemetryModelCallMetricV1) -> Result<(), ()> {
    const PURPOSES: &[&str] = &[
        "agent_step",
        "final_reply",
        "reply_repair",
        "screen_observation",
        "proactive_reply",
        "background_agent",
        "memory_curation",
        "memory_curation_repair",
    ];
    const FAMILIES: &[&str] = &[
        "openai",
        "anthropic",
        "gemini",
        "deepseek",
        "custom",
        "unknown",
    ];
    if candidate.schema != 1
        || candidate.model_call == 0
        || !PURPOSES.contains(&candidate.purpose.as_str())
        || !FAMILIES.contains(&candidate.model_family.as_str())
        || !matches!(
            candidate.outcome.as_str(),
            "success" | "failed" | "cancelled"
        )
        || !matches!(
            candidate.context_window_source.as_str(),
            "provider" | "configured" | "fallback" | "unknown"
        )
        || candidate
            .operation_id
            .as_deref()
            .is_some_and(|value| valid_token(value, 128).is_none())
        || candidate
            .error_code
            .as_deref()
            .is_some_and(|value| !valid_code(value))
        || (candidate.outcome == "success" && candidate.error_code.is_some())
    {
        return Err(());
    }
    Ok(())
}

fn valid_stack_frame(frame: &SafeStackFrame) -> bool {
    let any = frame.module.is_some()
        || frame.function.is_some()
        || frame.file.is_some()
        || frame.line.is_some();
    any && frame
        .module
        .as_deref()
        .is_none_or(|value| valid_token(value, 128).is_some())
        && frame
            .function
            .as_deref()
            .is_none_or(|value| valid_token(value, 128).is_some())
        && frame.file.as_deref().is_none_or(|value| {
            !value.starts_with('/')
                && !value.contains(':')
                && !value.contains("..")
                && value.len() <= 240
                && value.bytes().all(|byte| {
                    byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'_' | b'-' | b'.')
                })
        })
}

fn valid_uuid_v4(value: &str) -> bool {
    Uuid::parse_str(value)
        .is_ok_and(|uuid| uuid.get_version_num() == 4 && uuid.hyphenated().to_string() == value)
}

fn valid_token(value: &str, max: usize) -> Option<&str> {
    (!value.is_empty()
        && value.len() <= max
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-')))
    .then_some(value)
}

fn valid_event_name(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.len() <= max
        && value.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_lowercase()
            } else {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || matches!(byte, b'.' | b'_' | b'-')
            }
        })
}

fn valid_code(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_uppercase()
            } else {
                byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_'
            }
        })
}

fn stable_attribute(attributes: Option<&Value>, key: &str) -> Option<String> {
    attributes
        .and_then(Value::as_object)
        .and_then(|map| map.get(key))
        .and_then(Value::as_str)
        .filter(|value| valid_code(value))
        .map(str::to_string)
}

fn stable_token_attribute(attributes: Option<&Value>, key: &str, max: usize) -> Option<String> {
    attributes
        .and_then(Value::as_object)
        .and_then(|map| map.get(key))
        .and_then(Value::as_str)
        .filter(|value| valid_token(value, max).is_some())
        .map(str::to_string)
}

fn token_attribute(attributes: Option<&Value>, key: &str, allowed: &[&str]) -> Option<String> {
    attributes
        .and_then(Value::as_object)
        .and_then(|map| map.get(key))
        .and_then(Value::as_str)
        .filter(|value| allowed.contains(value))
        .map(str::to_string)
}

fn outcome_attribute(attributes: Option<&Value>) -> Option<String> {
    let value = attributes
        .and_then(Value::as_object)
        .and_then(|map| map.get("outcome"))
        .and_then(Value::as_str)?;
    match value {
        "completed" | "success" => Some("success".to_string()),
        "failed" | "cancelled" | "degraded" | "skipped" => Some(value.to_string()),
        _ => None,
    }
}

fn project_breadcrumbs(items: &VecDeque<BreadcrumbState>, now_ms: u64) -> Vec<Breadcrumb> {
    items
        .iter()
        .filter(|item| now_ms.saturating_sub(item.elapsed_ms) <= 86_400_000)
        .map(|item| Breadcrumb {
            offset_ms: item.elapsed_ms as i64 - now_ms as i64,
            source: item.source.clone(),
            severity: item.severity.clone(),
            channel: item.channel.clone(),
            event: item.event.clone(),
            code: item.code.clone(),
            outcome: item.outcome.clone(),
            elapsed_ms: item.duration_ms,
        })
        .collect()
}

fn integer_attribute(attributes: Option<&Value>, key: &str) -> Option<u64> {
    attributes
        .and_then(Value::as_object)
        .and_then(|map| map.get(key))
        .and_then(|value| {
            value.as_u64().or_else(|| {
                value
                    .as_f64()
                    .filter(|value| value.is_finite() && *value >= 0.0)
                    .map(|value| value as u64)
            })
        })
}

fn feature_for_event(event: &str) -> Option<&'static str> {
    if matches!(event, "webview.chat.send" | "agent.turn.started") {
        Some("chat")
    } else if matches!(
        event,
        "tts.synthesis.started" | "tts.request.started" | "tts.playback.started"
    ) {
        Some("tts")
    } else if event.starts_with("memory.recall.") || event.starts_with("memory.curation.") {
        Some("memory")
    } else if event == "tool.execution.started" || event == "mcp.tool.started" {
        Some("tools")
    } else if event == "plugin.loaded" || event == "plugin.execution.started" {
        Some("plugins")
    } else {
        None
    }
}

fn allowlisted_runtime_error(
    source: &str,
    event: &str,
    attributes: Option<&Value>,
) -> Option<(&'static str, String)> {
    match event {
        "shell.error.unhandled" => Some(("rust", "RUST_PANIC".to_string())),
        "webview.error.unhandled" => attributes
            .and_then(Value::as_object)
            .and_then(|map| map.get("code"))
            .and_then(Value::as_str)
            .filter(|code| {
                matches!(
                    *code,
                    "WEBVIEW_UNHANDLED_ERROR" | "WEBVIEW_UNHANDLED_REJECTION"
                )
            })
            .map(|code| ("webview", code.to_string())),
        "legacy_import.recovery.failed" => {
            Some(("rust", "LEGACY_IMPORT_RECOVERY_FAILED".to_string()))
        }
        "first_run.state.failed" => Some(("rust", "FIRST_RUN_STATE_FAILED".to_string())),
        "legacy_import.failed" => stable_attribute(attributes, "code").map(|code| ("rust", code)),
        "legacy_import.core_validation_failed" => {
            stable_attribute(attributes, "code").map(|code| ("rust", code))
        }
        "legacy_import.result_invalid" => {
            stable_attribute(attributes, "code").map(|code| ("rust", code))
        }
        "core.spawn.failed"
            if token_attribute(attributes, "category", &["unexpected_exit"]).is_some() =>
        {
            Some(("rust", "CORE_UNEXPECTED_EXIT".to_string()))
        }
        "tts.service.failed"
        | "tts.service.warmup_failed"
        | "tts.process.cleanup.failed"
        | "tts.synthesis.failed"
        | "tts.weights.failed"
        | "tts.settings.partial"
            if source == "core" =>
        {
            selected_tts_error_code(event, attributes).map(|code| ("tts", code))
        }
        "tts.playback.failed" if source == "rust" => {
            selected_tts_error_code(event, attributes).map(|code| ("tts", code))
        }
        _ => None,
    }
}

fn selected_tts_error_code(event: &str, attributes: Option<&Value>) -> Option<String> {
    let code = ["provider_error_code", "reason_code", "code"]
        .into_iter()
        .find_map(|key| stable_attribute(attributes, key))?;
    selected_tts_code(event, &code).then_some(code)
}

fn selected_tts_code(event: &str, code: &str) -> bool {
    match event {
        "tts.service.failed" => matches!(
            code,
            "TTS_ACCELERATOR_UNAVAILABLE"
                | "TTS_DEVICE_PROBE_FAILED"
                | "TTS_ENDPOINT_PROBE_FAILED"
                | "TTS_RUNTIME_EXITED"
                | "TTS_RUNTIME_INVALID"
                | "TTS_RUNTIME_PYTHON_MISSING"
                | "TTS_RUNTIME_START_FAILED"
                | "TTS_RUNTIME_TIMEOUT"
        ),
        "tts.service.warmup_failed" => matches!(
            code,
            "TTS_ACCELERATOR_UNAVAILABLE"
                | "TTS_DEVICE_PROBE_FAILED"
                | "TTS_ENDPOINT_PROBE_FAILED"
                | "TTS_RUNTIME_EXITED"
                | "TTS_RUNTIME_INVALID"
                | "TTS_RUNTIME_PYTHON_MISSING"
                | "TTS_RUNTIME_START_FAILED"
                | "TTS_RUNTIME_TIMEOUT"
                | "TTS_STORAGE_UNAVAILABLE"
                | "TTS_WARMUP_FAILED"
                | "TTS_WEIGHTS_UNAVAILABLE"
        ),
        "tts.process.cleanup.failed" => code == "TTS_STALE_PROCESS_KILL_FAILED",
        "tts.synthesis.failed" => matches!(
            code,
            "TTS_ARTIFACT_INVALID"
                | "TTS_CONNECTION_FAILED"
                | "TTS_JOB_RESULT_INVALID"
                | "TTS_PROVIDER_UNAVAILABLE"
                | "TTS_REQUEST_TIMEOUT"
                | "TTS_RUNTIME_EXITED"
                | "TTS_RUNTIME_PYTHON_MISSING"
                | "TTS_OUTPUT_READ_FAILED"
                | "TTS_PUBLICATION_FAILED"
                | "TTS_SERVICE_UNAVAILABLE"
                | "TTS_SYNTHESIS_FAILED"
                | "TTS_SYNTHESIS_TIMEOUT"
                | "TTS_SYNTHESIS_WORKER_FAILED"
                | "TTS_WEIGHTS_UNAVAILABLE"
        ),
        "tts.playback.failed" => matches!(
            code,
            "AUDIO_DEVICE_UNAVAILABLE" | "AUDIO_RECORDING_INVALID" | "AUDIO_FORMAT_UNSUPPORTED"
        ),
        "tts.weights.failed" => code == "TTS_WEIGHTS_UNAVAILABLE",
        "tts.settings.partial" => matches!(
            code,
            "TTS_PROVIDER_SETTINGS_SAVE_FAILED" | "TTS_SELECTION_SAVE_FAILED"
        ),
        _ => false,
    }
}

fn allowlisted_runtime_warning(
    source: &str,
    severity: &str,
    event: &str,
    attributes: Option<&Value>,
) -> Option<(&'static str, String)> {
    if severity != "warning" {
        return None;
    }
    let stable_code = || {
        ["reason_code", "provider_error_code", "code"]
            .into_iter()
            .find_map(|key| stable_attribute(attributes, key))
    };
    match (source, event) {
        ("core", "memory.recall.failed") => {
            let code = stable_code().unwrap_or_else(|| "MEMORY_RECALL_FAILED".to_string());
            matches!(code.as_str(), "MEMORY_RECALL_FAILED" | "INVALID_RESULT")
                .then_some(("memory", code))
        }
        ("core", "memory.recall.unavailable") => (stable_code().as_deref()
            == Some("MEMORY_NOT_READY"))
        .then(|| ("memory", "MEMORY_NOT_READY".to_string())),
        ("core", "memory.curation.failed") => stable_code().map(|code| ("memory", code)),
        ("core", "memory.curation.request_fuse_opened") => (stable_code().as_deref()
            == Some("CURATION_REQUEST_FUSE_OPEN"))
        .then(|| ("memory", "CURATION_REQUEST_FUSE_OPEN".to_string())),
        ("core", "context.dependencies.degraded") => stable_code().map(|code| ("context", code)),
        ("core", "reply.processing.failed") => {
            let code = stable_code()
                .filter(|code| code == "REPLY_REPAIR_REQUEST_FAILED")
                .unwrap_or_else(|| "REPLY_PROCESSING_FALLBACK".to_string());
            Some(("reply", code))
        }
        ("rust", "screen.capture.failed") => stable_code()
            .filter(|code| code.starts_with("SCREEN_"))
            .map(|code| ("screen", code)),
        ("core", "mcp.config.failed") => stable_code()
            .filter(|code| matches!(code.as_str(), "MCP_CONFIG_LOAD_FAILED" | "CONFIG_INVALID"))
            .map(|code| ("mcp", code)),
        ("core", "mcp.server.failed") => stable_code()
            .filter(|code| {
                matches!(
                    code.as_str(),
                    "COMMAND_NOT_FOUND" | "COMMAND_NOT_EXECUTABLE" | "TIMEOUT" | "TRANSPORT_FAILED"
                )
            })
            .map(|code| ("mcp", code)),
        ("core", "mcp.close.failed") => (stable_code().as_deref() == Some("CLOSE_FAILED"))
            .then(|| ("mcp", "CLOSE_FAILED".to_string())),
        ("core", "mcp.close.timeout") => (stable_code().as_deref() == Some("CLOSE_TIMEOUT"))
            .then(|| ("mcp", "CLOSE_TIMEOUT".to_string())),
        _ => None,
    }
}

fn stable_fingerprint(
    component: &str,
    event: &str,
    code: &str,
    stack: &[SafeStackFrame],
) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    let mut add = |value: &str| {
        for byte in value.bytes().chain(std::iter::once(0xff)) {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
    };
    add(component);
    add(event);
    add(code);
    for frame in stack {
        add(frame.module.as_deref().unwrap_or(""));
        add(frame.function.as_deref().unwrap_or(""));
        add(frame.file.as_deref().unwrap_or(""));
        add(&frame.line.map(|line| line.to_string()).unwrap_or_default());
    }
    format!("f-{hash:016x}")
}

fn platform_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

fn release_channel() -> &'static str {
    if cfg!(debug_assertions) {
        "development"
    } else if env!("CARGO_PKG_VERSION").contains('-') {
        "prerelease"
    } else {
        "stable"
    }
}

#[cfg(unix)]
fn os_version() -> String {
    let mut value = std::mem::MaybeUninit::<libc::utsname>::uninit();
    let result = unsafe { libc::uname(value.as_mut_ptr()) };
    if result != 0 {
        return std::env::consts::OS.to_string();
    }
    let value = unsafe { value.assume_init() };
    let bytes = unsafe { std::ffi::CStr::from_ptr(value.release.as_ptr()) }.to_bytes();
    std::str::from_utf8(bytes)
        .ok()
        .and_then(|text| valid_token(text, 128))
        .unwrap_or(std::env::consts::OS)
        .to_string()
}

#[cfg(windows)]
fn os_version() -> String {
    let version = windows_version::OsVersion::current();
    format!("{}.{}.{}", version.major, version.minor, version.build)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        io::{Read, Write},
        net::{TcpListener, TcpStream},
        path::PathBuf,
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc as std_mpsc, Arc,
        },
        thread::JoinHandle,
        time::Duration,
    };

    struct TestServer {
        endpoint: String,
        requests: std_mpsc::Receiver<(String, Vec<u8>)>,
        stopping: Arc<AtomicBool>,
        worker: Option<JoinHandle<()>>,
    }

    impl TestServer {
        fn start(status: u16, response_delay: Duration) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let (sender, requests) = std_mpsc::channel();
            let stopping = Arc::new(AtomicBool::new(false));
            let worker_stopping = Arc::clone(&stopping);
            let worker = std::thread::spawn(move || {
                while !worker_stopping.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            let sender = sender.clone();
                            std::thread::spawn(move || {
                                if let Some(request) = read_request(stream.try_clone().unwrap()) {
                                    let _ = sender.send(request);
                                }
                                std::thread::sleep(response_delay);
                                let mut stream = stream;
                                let reason = if status == 202 {
                                    "Accepted"
                                } else {
                                    "Rejected"
                                };
                                let body = if status == 202 {
                                    br#"{"ok":true,"accepted":1}"#.as_slice()
                                } else {
                                    br#"{"ok":false}"#.as_slice()
                                };
                                let response = format!(
                                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                    body.len()
                                );
                                let _ = stream.write_all(response.as_bytes());
                                let _ = stream.write_all(body);
                            });
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(_) => return,
                    }
                }
            });
            Self {
                endpoint: format!("http://{address}"),
                requests,
                stopping,
                worker: Some(worker),
            }
        }
    }

    impl Drop for TestServer {
        fn drop(&mut self) {
            self.stopping.store(true, Ordering::Release);
            if let Some(worker) = self.worker.take() {
                let _ = worker.join();
            }
        }
    }

    fn read_request(mut stream: TcpStream) -> Option<(String, Vec<u8>)> {
        stream.set_read_timeout(Some(Duration::from_secs(1))).ok()?;
        let mut received = Vec::new();
        let mut buffer = [0_u8; 2048];
        let header_end = loop {
            let count = stream.read(&mut buffer).ok()?;
            if count == 0 {
                return None;
            }
            received.extend_from_slice(&buffer[..count]);
            if let Some(index) = received.windows(4).position(|window| window == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let header = std::str::from_utf8(&received[..header_end]).ok()?;
        let path = header.split_whitespace().nth(1)?.to_string();
        let length = header
            .lines()
            .find_map(|line| {
                line.split_once(':').and_then(|(name, value)| {
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
            })
            .unwrap_or(0);
        while received.len() < header_end + length {
            let count = stream.read(&mut buffer).ok()?;
            if count == 0 {
                return None;
            }
            received.extend_from_slice(&buffer[..count]);
        }
        Some((path, received[header_end..header_end + length].to_vec()))
    }

    fn fixture(name: &str, body: &str) -> (PathBuf, UiConfigRepository) {
        let root = std::env::temp_dir().join(format!("sakura-telemetry-{name}-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("ui.json");
        fs::write(&path, body).unwrap();
        (root, UiConfigRepository::new(path))
    }

    fn service_for(
        server: &TestServer,
        name: &str,
        capacity: usize,
        timeout: Duration,
    ) -> (PathBuf, TelemetryService) {
        let (root, repository) = fixture(
            name,
            r#"{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":true,"installation_id":"550e8400-e29b-41d4-a716-446655440000"}}}"#,
        );
        let service = TelemetryService::initialize_with_options(
            repository,
            "r-test".to_string(),
            server.endpoint.clone(),
            capacity,
            timeout,
        );
        (root, service)
    }

    fn event_item(service: &TelemetryService, event: &str) -> RuntimeEventItem {
        RuntimeEventItem {
            installation_id: service.installation_id().unwrap(),
            run_id: "r-test".to_string(),
            app_version: "1.0.3".to_string(),
            platform: "macos".to_string(),
            os_version: "25.0".to_string(),
            arch: "aarch64".to_string(),
            event: event.to_string(),
            feature: None,
            duration_ms: (event == "app.ready").then_some(12),
            from_version: None,
            to_version: None,
            error_code: None,
        }
    }

    fn wait_for_diagnostic(
        service: &TelemetryService,
        select: impl Fn(&SenderDiagnostics) -> u64,
    ) -> u64 {
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            let value = service
                .inner
                .diagnostics
                .lock()
                .map(|item| select(&item))
                .unwrap_or(0);
            if value > 0 {
                return value;
            }
            if Instant::now() >= deadline {
                return 0;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_sender_exit(service: &TelemetryService) -> bool {
        for _ in 0..100 {
            if Arc::strong_count(&service.inner) == 1 {
                return true;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        false
    }

    #[test]
    fn config_defaults_enabled_and_generates_v4_without_losing_unknown_settings() {
        let (root, repository) = fixture(
            "default",
            r#"{"schema_version":1,"domain":"ui","settings":{"future":42}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        let snapshot = service.snapshot().unwrap();
        assert!(snapshot.enabled);
        assert!(valid_uuid_v4(snapshot.installation_id.as_deref().unwrap()));
        let value: Value =
            serde_json::from_slice(&fs::read(root.join("ui.json")).unwrap()).unwrap();
        assert_eq!(value["settings"]["future"], 42);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn explicit_false_does_not_generate_id_and_invalid_values_fail_closed() {
        let (root, repository) = fixture(
            "off",
            r#"{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":false}}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(service.snapshot().unwrap().installation_id, None);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let (root, repository) = fixture(
            "invalid",
            r#"{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":"yes"}}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(
            service.snapshot(),
            Err("TELEMETRY_SETTINGS_INVALID".to_string())
        );
        assert!(!service.inner.enabled.load(Ordering::Acquire));
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let (root, repository) = fixture(
            "null-id",
            r#"{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":false,"installation_id":null}}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(
            service.snapshot(),
            Err("TELEMETRY_SETTINGS_INVALID".to_string())
        );
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn explicit_true_preserves_id_and_invalid_id_can_be_repaired_by_disabling() {
        let valid_id = "550e8400-e29b-41d4-a716-446655440000";
        let (root, repository) = fixture(
            "on",
            &format!(
                r#"{{"schema_version":1,"domain":"ui","settings":{{"telemetry":{{"enabled":true,"installation_id":"{valid_id}"}}}}}}"#
            ),
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(
            service.snapshot().unwrap().installation_id.as_deref(),
            Some(valid_id)
        );
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let (root, repository) = fixture(
            "invalid-id",
            r#"{"schema_version":1,"domain":"ui","settings":{"future":7,"telemetry":{"enabled":true,"installation_id":"machine-id"}}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(
            service.snapshot(),
            Err("TELEMETRY_SETTINGS_INVALID".to_string())
        );
        let repaired = service.set_enabled(false).unwrap();
        assert!(!repaired.enabled);
        assert_eq!(repaired.installation_id, None);
        let value: Value =
            serde_json::from_slice(&fs::read(root.join("ui.json")).unwrap()).unwrap();
        assert_eq!(value["settings"]["future"], 7);
        assert!(value["settings"]["telemetry"]
            .get("installation_id")
            .is_none());
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let (root, repository) = fixture(
            "invalid-enabled-valid-id",
            &format!(
                r#"{{"schema_version":1,"domain":"ui","settings":{{"telemetry":{{"enabled":"yes","installation_id":"{valid_id}"}}}}}}"#
            ),
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        let repaired = service.set_enabled(false).unwrap();
        assert_eq!(repaired.installation_id.as_deref(), Some(valid_id));
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn installation_id_save_failure_disables_without_blocking_startup() {
        let root =
            std::env::temp_dir().join(format!("sakura-telemetry-blocked-{}", Uuid::new_v4()));
        fs::write(&root, b"not-a-directory").unwrap();
        let repository = UiConfigRepository::new(root.join("ui.json"));
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        assert_eq!(
            service.snapshot(),
            Err("TELEMETRY_SETTINGS_SAVE_FAILED".to_string())
        );
        assert!(!service.inner.enabled.load(Ordering::Acquire));
        service.shutdown();
        let _ = fs::remove_file(root);
    }

    #[test]
    fn shutdown_returns_immediately_and_stops_the_sender() {
        let server = TestServer::start(202, Duration::from_millis(300));
        let (root, service) = service_for(&server, "shutdown", 4, Duration::from_secs(1));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();

        let started = Instant::now();
        service.shutdown();
        assert!(started.elapsed() < Duration::from_millis(50));
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn regenerate_and_disable_save_failures_keep_safe_runtime_state() {
        let (root, repository) = fixture(
            "save-failure",
            r#"{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":true,"installation_id":"550e8400-e29b-41d4-a716-446655440000"}}}"#,
        );
        let service = TelemetryService::initialize(repository, "r-test".to_string());
        let original = service.snapshot().unwrap();
        let backup = root.with_extension("backup");
        fs::rename(&root, &backup).unwrap();
        fs::write(&root, b"not-a-directory").unwrap();

        assert_eq!(
            service.regenerate_installation_id(),
            Err("TELEMETRY_SETTINGS_SAVE_FAILED".to_string())
        );
        assert_eq!(service.snapshot().unwrap(), original);
        assert_eq!(
            service.set_enabled(false),
            Err("TELEMETRY_SETTINGS_SAVE_FAILED".to_string())
        );
        let runtime = service.snapshot().unwrap();
        assert!(!runtime.enabled);
        assert_eq!(runtime.installation_id, original.installation_id);

        service.shutdown();
        fs::remove_file(&root).unwrap();
        fs::rename(&backup, &root).unwrap();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn privacy_candidate_rejects_absolute_stack_paths_and_unknown_fields() {
        let absolute = r#"{"kind":"error","error":{"schema":1,"component":"core","event":"core.error.unhandled","code":"CORE_UNHANDLED_ERROR","operationId":null,"exceptionType":"RuntimeError","stack":[{"file":"/Users/private/chat.txt","line":2}]}}"#;
        let envelope: CoreTelemetryEnvelope = serde_json::from_str(absolute).unwrap();
        assert!(validate_error_candidate(&envelope.error.unwrap()).is_err());
        assert!(serde_json::from_str::<CoreTelemetryEnvelope>(
            r#"{"kind":"modelCall","prompt":"PRIVATE"}"#
        )
        .is_err());
        let arbitrary = serde_json::from_str::<CoreTelemetryEnvelope>(
            r#"{"kind":"error","error":{"schema":1,"component":"core","event":"api.request.failed","code":"MODEL_REQUEST_FAILED","operationId":null,"exceptionType":null,"stack":[]}}"#,
        )
        .unwrap();
        assert!(validate_error_candidate(&arbitrary.error.unwrap()).is_err());
    }

    #[test]
    fn internal_webview_error_is_reported_but_core_bridge_cannot_spoof_its_source() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "webview-error", 4, Duration::from_secs(1));
        service.observe_runtime_event(
            "webview",
            "error",
            "webview",
            "webview.error.unhandled",
            Some("operation-7"),
            Some(&json!({"code": "WEBVIEW_UNHANDLED_ERROR"})),
        );
        let request = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(request.0, "/v1/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["error"]["component"], "webview");
        assert_eq!(body["error"]["event"], "webview.error.unhandled");
        assert_eq!(body["error"]["code"], "WEBVIEW_UNHANDLED_ERROR");

        let spoofed = TelemetryErrorCandidateV1 {
            schema: 1,
            component: "webview".to_string(),
            event: "webview.error.unhandled".to_string(),
            code: "WEBVIEW_UNHANDLED_ERROR".to_string(),
            operation_id: None,
            exception_type: None,
            stack: Vec::new(),
        };
        assert!(validate_error_candidate(&spoofed).is_ok());
        assert!(validate_core_error_candidate(&spoofed).is_err());
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn selected_internal_tts_failures_are_reported_with_the_specific_safe_code() {
        let selected = [
            (
                "core",
                "tts.service.failed",
                json!({"reason_code": "TTS_RUNTIME_EXITED"}),
                "TTS_RUNTIME_EXITED",
            ),
            (
                "core",
                "tts.service.warmup_failed",
                json!({"code": "TTS_WARMUP_FAILED"}),
                "TTS_WARMUP_FAILED",
            ),
            (
                "core",
                "tts.process.cleanup.failed",
                json!({"code": "TTS_STALE_PROCESS_KILL_FAILED"}),
                "TTS_STALE_PROCESS_KILL_FAILED",
            ),
            (
                "core",
                "tts.synthesis.failed",
                json!({"provider_error_code": "TTS_JOB_RESULT_INVALID"}),
                "TTS_JOB_RESULT_INVALID",
            ),
            (
                "rust",
                "tts.playback.failed",
                json!({"code": "AUDIO_FORMAT_UNSUPPORTED"}),
                "AUDIO_FORMAT_UNSUPPORTED",
            ),
        ];
        for (source, event, attributes, expected_code) in selected {
            assert_eq!(
                allowlisted_runtime_error(source, event, Some(&attributes)),
                Some(("tts", expected_code.to_string()))
            );
        }

        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "tts-error", 4, Duration::from_secs(1));
        service.observe_runtime_event(
            "core",
            "warning",
            "tts",
            "tts.synthesis.failed",
            Some("operation-tts-7"),
            Some(&json!({
                "code": "TTS_SYNTHESIS_FAILED",
                "provider_error_code": "TTS_JOB_RESULT_INVALID",
                "error_type": "RuntimeError",
                "diagnostic": "PRIVATE PROVIDER RESPONSE"
            })),
        );

        let request = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(request.0, "/v1/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["operationId"], "operation-tts-7");
        assert_eq!(body["error"]["component"], "tts");
        assert_eq!(body["error"]["event"], "tts.synthesis.failed");
        assert_eq!(body["error"]["code"], "TTS_JOB_RESULT_INVALID");
        assert_eq!(body["error"]["exceptionType"], "RuntimeError");
        assert!(!String::from_utf8(request.1)
            .unwrap()
            .contains("PRIVATE PROVIDER RESPONSE"));

        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn selected_tts_environment_warnings_are_reported_but_noise_and_spoofing_are_rejected() {
        let cases = [
            (
                "core",
                "tts.synthesis.failed",
                json!({"provider_error_code": "TTS_PROVIDER_UNAVAILABLE"}),
                Some(("tts", "TTS_PROVIDER_UNAVAILABLE")),
            ),
            (
                "rust",
                "tts.playback.failed",
                json!({"code": "AUDIO_DEVICE_UNAVAILABLE"}),
                Some(("tts", "AUDIO_DEVICE_UNAVAILABLE")),
            ),
            (
                "core",
                "tts.process.cleanup.failed",
                json!({"code": "TTS_PORT_OCCUPIED_BY_OTHER_PROCESS"}),
                None,
            ),
            (
                "webview",
                "tts.synthesis.failed",
                json!({"provider_error_code": "TTS_JOB_RESULT_INVALID"}),
                None,
            ),
        ];

        for (source, event, attributes, expected) in cases {
            assert_eq!(
                allowlisted_runtime_error(source, event, Some(&attributes)),
                expected.map(|(component, code)| (component, code.to_string()))
            );
        }

        let rejected = TelemetryErrorCandidateV1 {
            schema: 1,
            component: "tts".to_string(),
            event: "tts.synthesis.failed".to_string(),
            code: "TTS_DISABLED".to_string(),
            operation_id: None,
            exception_type: None,
            stack: Vec::new(),
        };
        assert!(validate_error_candidate(&rejected).is_err());
    }

    #[test]
    fn high_signal_runtime_warnings_use_safe_codes_and_reject_noise() {
        let selected = [
            (
                "core",
                "warning",
                "memory.recall.failed",
                json!({"error_type": "RuntimeError"}),
                Some(("memory", "MEMORY_RECALL_FAILED")),
            ),
            (
                "core",
                "warning",
                "context.dependencies.degraded",
                json!({"reason_code": "PROCESS_EXITED"}),
                Some(("context", "PROCESS_EXITED")),
            ),
            (
                "core",
                "warning",
                "reply.processing.failed",
                json!({"reason_code": "invalid_reply_shape"}),
                Some(("reply", "REPLY_PROCESSING_FALLBACK")),
            ),
            (
                "rust",
                "warning",
                "screen.capture.failed",
                json!({"code": "SCREEN_CAPTURE_PLATFORM_DENIED"}),
                Some(("screen", "SCREEN_CAPTURE_PLATFORM_DENIED")),
            ),
            (
                "core",
                "warning",
                "mcp.server.failed",
                json!({"reason_code": "TRANSPORT_FAILED"}),
                Some(("mcp", "TRANSPORT_FAILED")),
            ),
        ];
        for (source, severity, event, attributes, expected) in selected {
            assert_eq!(
                allowlisted_runtime_warning(source, severity, event, Some(&attributes)),
                expected.map(|(component, code)| (component, code.to_string()))
            );
        }

        assert_eq!(
            allowlisted_runtime_warning(
                "core",
                "warning",
                "mcp.server.failed",
                Some(&json!({"reason_code": "CANCELLED"})),
            ),
            None
        );
        assert_eq!(
            allowlisted_runtime_warning(
                "webview",
                "warning",
                "context.dependencies.degraded",
                Some(&json!({"reason_code": "PROCESS_EXITED"})),
            ),
            None
        );
        assert_eq!(
            allowlisted_runtime_warning(
                "core",
                "info",
                "memory.recall.unavailable",
                Some(&json!({"reason_code": "MEMORY_NOT_READY"})),
            ),
            None
        );
    }

    #[test]
    fn repeated_runtime_warning_is_reported_only_once_per_run() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "warning-dedupe", 4, Duration::from_secs(1));
        let attributes = json!({
            "reason_code": "MEMORY_NOT_READY",
            "error_type": "MemoryUnavailable",
            "diagnostic": "PRIVATE MEMORY STATE"
        });

        for _ in 0..2 {
            service.observe_runtime_event(
                "core",
                "warning",
                "memory",
                "memory.recall.unavailable",
                Some("operation-memory-7"),
                Some(&attributes),
            );
        }

        let first = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(first.0, "/v1/events");
        let request = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(request.0, "/v1/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["error"]["component"], "memory");
        assert_eq!(body["error"]["event"], "memory.recall.unavailable");
        assert_eq!(body["error"]["code"], "MEMORY_NOT_READY");
        assert_eq!(body["error"]["exceptionType"], "MemoryUnavailable");
        assert!(!String::from_utf8(request.1)
            .unwrap()
            .contains("PRIVATE MEMORY STATE"));
        assert!(server
            .requests
            .recv_timeout(Duration::from_millis(100))
            .is_err());

        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn envelopes_keep_batches_bounded_and_body_free() {
        let event = RuntimeEventItem {
            installation_id: Uuid::new_v4().to_string(),
            run_id: "r-test".to_string(),
            app_version: "1.0.3".to_string(),
            platform: "macos".to_string(),
            os_version: "25.0".to_string(),
            arch: "aarch64".to_string(),
            event: "feature.used".to_string(),
            feature: Some("chat".to_string()),
            duration_ms: None,
            from_version: None,
            to_version: None,
            error_code: None,
        };
        let body = encode_records("/v1/events", &[TelemetryRecord::Event(event)]).unwrap();
        let text = String::from_utf8(body).unwrap();
        assert!(text.contains("\"items\""));
        for forbidden in [
            "prompt",
            "message",
            "authorization",
            "cookie",
            "apiKey",
            "toolArgs",
            "agentTrace",
        ] {
            assert!(!text.contains(forbidden));
        }
        assert!(encode_records("/v1/events", &[]).is_none());
        let ten = (0..10)
            .map(|_| TelemetryRecord::Event(event_item_for_encoding()))
            .collect::<Vec<_>>();
        assert!(encode_records("/v1/events", &ten).is_some());
        let eleven = (0..11)
            .map(|_| TelemetryRecord::Event(event_item_for_encoding()))
            .collect::<Vec<_>>();
        assert!(encode_records("/v1/events", &eleven).is_none());
        let mut oversized = event_item_for_encoding();
        oversized.app_version = "x".repeat(EVENT_BODY_LIMIT);
        assert!(encode_records("/v1/events", &[TelemetryRecord::Event(oversized)]).is_none());
    }

    fn event_item_for_encoding() -> RuntimeEventItem {
        RuntimeEventItem {
            installation_id: "550e8400-e29b-41d4-a716-446655440000".to_string(),
            run_id: "r-test".to_string(),
            app_version: "1.0.3".to_string(),
            platform: "macos".to_string(),
            os_version: "25.0".to_string(),
            arch: "aarch64".to_string(),
            event: "app.started".to_string(),
            feature: None,
            duration_ms: None,
            from_version: None,
            to_version: None,
            error_code: None,
        }
    }

    #[test]
    fn disable_cancels_inflight_drops_old_queue_and_accepts_no_new_records() {
        let server = TestServer::start(202, Duration::from_millis(300));
        let (root, service) = service_for(&server, "disable", 8, Duration::from_secs(1));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        let first = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(first.0, "/v1/events");
        for _ in 0..5 {
            assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        }
        service.set_enabled(false).unwrap();
        assert!(!service.enqueue(TelemetryRecord::Event(event_item_for_encoding())));
        service.set_enabled(true).unwrap();
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.ready"))));
        let second = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        let body: Value = serde_json::from_slice(&second.1).unwrap();
        assert_eq!(body["items"].as_array().unwrap().len(), 1);
        assert_eq!(body["items"][0]["event"], "app.ready");
        assert!(server
            .requests
            .recv_timeout(Duration::from_millis(400))
            .is_err());
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn regenerate_cancels_old_epoch_and_next_request_uses_new_id() {
        let server = TestServer::start(202, Duration::from_millis(300));
        let (root, service) = service_for(&server, "regenerate", 8, Duration::from_secs(1));
        let old_id = service.snapshot().unwrap().installation_id.unwrap();
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        let first = server
            .requests
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        let first_body: Value = serde_json::from_slice(&first.1).unwrap();
        assert_eq!(first_body["items"][0]["installationId"], old_id);
        let stale_epoch = service.inner.epoch.load(Ordering::Acquire);
        let new_id = service
            .regenerate_installation_id()
            .unwrap()
            .installation_id
            .unwrap();
        assert_ne!(new_id, old_id);
        let mut stale = event_item_for_encoding();
        stale.installation_id = old_id;
        assert!(!service.enqueue_at_epoch(TelemetryRecord::Event(stale), stale_epoch));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.ready"))));
        let second = server
            .requests
            .recv_timeout(Duration::from_secs(3))
            .unwrap();
        let second_body: Value = serde_json::from_slice(&second.1).unwrap();
        assert_eq!(second_body["items"][0]["installationId"], new_id);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn queue_overflow_timeout_and_http_rejection_are_isolated() {
        let slow = TestServer::start(202, Duration::from_millis(500));
        let (root, service) = service_for(&slow, "overflow", 2, Duration::from_secs(1));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        slow.requests.recv_timeout(Duration::from_secs(3)).unwrap();
        for _ in 0..20 {
            let _ = service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started")));
        }
        assert!(wait_for_diagnostic(&service, |item| item.dropped) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let offline = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&offline, "offline", 4, Duration::from_secs(1));
        drop(offline);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let rejected = TestServer::start(500, Duration::ZERO);
        let (root, service) = service_for(&rejected, "rejected", 4, Duration::from_secs(1));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let timeout = TestServer::start(202, Duration::from_millis(250));
        let (root, service) = service_for(&timeout, "timeout", 4, Duration::from_millis(50));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn core_generation_stack_breadcrumb_and_fingerprint_boundaries_are_stable() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "bounds", 4, Duration::from_secs(1));
        service.activate_generation("generation-a");
        let context = CoreLogContext {
            generation_id: "generation-b".to_string(),
            generation_number: 2,
            core_pid: 42,
        };
        let payload = r#"{"kind":"error","error":{"schema":1,"component":"core","event":"core.error.unhandled","code":"CORE_UNHANDLED_ERROR","operationId":null,"exceptionType":"RuntimeError","stack":[]}}"#;
        assert!(!service.submit_core_bridge(payload, &context, None).unwrap());

        for index in 0..45 {
            service.push_breadcrumb(
                "core",
                "info",
                "runtime",
                "agent.turn.started",
                Some(&json!({"elapsed_ms": index})),
            );
        }
        assert_eq!(service.inner.breadcrumbs.lock().unwrap().len(), 40);
        service.push_breadcrumb(
            "core",
            "trace",
            "runtime",
            "agent.turn.completed",
            Some(&json!({"outcome": "completed", "elapsed_ms": 86_400_001_u64})),
        );
        let ring = service.inner.breadcrumbs.lock().unwrap();
        let latest = ring.back().unwrap();
        assert_eq!(latest.severity, "debug");
        assert_eq!(latest.outcome.as_deref(), Some("success"));
        assert_eq!(latest.duration_ms, None);
        assert_eq!(
            outcome_attribute(Some(&json!({"outcome": "started"}))),
            None
        );
        assert_eq!(outcome_attribute(Some(&json!({"outcome": "ready"}))), None);

        let old = VecDeque::from([BreadcrumbState {
            elapsed_ms: 1,
            source: "core".to_string(),
            severity: "info".to_string(),
            channel: "runtime".to_string(),
            event: "agent.turn.started".to_string(),
            code: None,
            outcome: None,
            duration_ms: None,
        }]);
        assert!(project_breadcrumbs(&old, 86_400_002).is_empty());
        drop(ring);
        let frames = (0..16)
            .map(|line| SafeStackFrame {
                module: Some("app.core".to_string()),
                function: Some("run".to_string()),
                file: Some("app/core.py".to_string()),
                line: Some(line),
            })
            .collect::<Vec<_>>();
        let candidate = TelemetryErrorCandidateV1 {
            schema: 1,
            component: "core".to_string(),
            event: "core.error.unhandled".to_string(),
            code: "CORE_UNHANDLED_ERROR".to_string(),
            operation_id: None,
            exception_type: Some("RuntimeError".to_string()),
            stack: frames.clone(),
        };
        assert!(validate_error_candidate(&candidate).is_ok());
        let mut too_many = candidate;
        too_many.stack.push(frames[0].clone());
        assert!(validate_error_candidate(&too_many).is_err());
        assert_eq!(
            stable_fingerprint(
                "core",
                "core.error.unhandled",
                "CORE_UNHANDLED_ERROR",
                &frames
            ),
            "f-6475ba08b0cd1bf7"
        );
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }
}
