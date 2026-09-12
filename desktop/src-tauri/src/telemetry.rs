mod contract;
mod evidence;
mod outbox;
pub(crate) use contract::DiagnosticDetail;
use contract::{DiagnosticContext, RequestDiagnostic};
use evidence::ErrorEvidence;
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
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

use tauri::{State, WebviewWindow};

use crate::{product_shell, runtime_log::CoreLogContext, ui_config::UiConfigRepository};

pub const TELEMETRY_CORE_BRIDGE_PREFIX: &str = "SAKURA_TELEMETRY_V1\t";
pub const TELEMETRY_ENDPOINT: &str = "https://telemetry.cialloo.cn";
pub const TELEMETRY_DOCUMENTATION_URL: &str =
    "https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/REMOTE_DIAGNOSTICS_AND_TELEMETRY.md";
const TELEMETRY_NAMESPACE: &str = "TELEMETRY_SETTINGS";
const QUEUE_CAPACITY: usize = 128;
const HTTP_TIMEOUT: Duration = Duration::from_millis(2_500);
const ERROR_BODY_LIMIT: usize = 128 * 1024;
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
    outbox: Mutex<outbox::Outbox>,
    repository: UiConfigRepository,
    run_id: String,
    endpoint: String,
    http_timeout: Duration,
    enabled: AtomicBool,
    epoch: AtomicU64,
    stopping: AtomicBool,
    drain_until: AtomicU64,
    runtime: Mutex<TelemetryRuntimeState>,
    breadcrumbs: Mutex<VecDeque<BreadcrumbState>>,
    features: Mutex<BTreeSet<String>>,
    reports: Mutex<BTreeMap<String, ReportCount>>,
    summary_at: AtomicU64,
    summary_sent: Mutex<(u64, u64, u64)>,
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
    diagnostic: Option<String>,
    elapsed_ms: u64,
    source: String,
    severity: String,
    channel: String,
    event: String,
    code: Option<String>,
    outcome: Option<String>,
    duration_ms: Option<u64>,
}

#[derive(Clone, Debug)]
struct ReportCount {
    fingerprint: String,
    generation: Option<String>,
    count: u64,
    dirty: bool,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorReport {
    evidence: ErrorEvidence,
    fingerprint_version: u8,
    details: DiagnosticDetail,
    diagnostics: DiagnosticContext,
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    stack: Vec<SafeStackFrame>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    breadcrumbs: Vec<Breadcrumb>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ErrorApp {
    version: String,
    channel: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorSystem {
    platform: String,
    os_version: String,
    arch: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    webview_version: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorDescriptor {
    component: String,
    event: String,
    code: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    exception_type: Option<String>,
    fingerprint: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
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

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Breadcrumb {
    #[serde(skip_serializing_if = "Option::is_none")]
    diagnostic: Option<String>,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeEventItem {
    operation_id: Option<String>,
    details: DiagnosticDetail,
    diagnostics: DiagnosticContext,
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
    error: Option<TelemetryErrorCandidate>,
    #[serde(default)]
    model_call: Option<TelemetryModelCallMetricV1>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TelemetryErrorCandidate {
    #[serde(default)]
    evidence: ErrorEvidence,
    #[serde(default)]
    details: DiagnosticDetail,
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
    #[serde(default)]
    request: RequestDiagnostic,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelCallItem {
    request: RequestDiagnostic,
    diagnostics: DiagnosticContext,
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
        let outbox = outbox::Outbox::new(repository.path());
        if !enabled {
            outbox.clear();
        }
        let inner = Arc::new(TelemetryInner {
            outbox: Mutex::new(outbox),
            repository,
            run_id,
            endpoint,
            http_timeout,
            enabled: AtomicBool::new(enabled),
            epoch: AtomicU64::new(1),
            stopping: AtomicBool::new(false),
            drain_until: AtomicU64::new(0),
            runtime: Mutex::new(TelemetryRuntimeState {
                installation_id,
                settings_error,
                active_generation: None,
            }),
            breadcrumbs: Mutex::new(VecDeque::with_capacity(40)),
            features: Mutex::new(BTreeSet::new()),
            reports: Mutex::new(BTreeMap::new()),
            summary_at: AtomicU64::new(0),
            summary_sent: Mutex::new((0, 0, 0)),
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
        if let Ok(outbox) = self.inner.outbox.lock() {
            outbox.clear();
        }
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
        self.flush_summaries(true);
        self.inner.drain_until.store(
            self.inner.started_at.elapsed().as_millis() as u64 + 2500,
            Ordering::Release,
        );
        self.inner.stopping.store(true, Ordering::Release);
        let _ = self
            .inner
            .control
            .send(self.inner.epoch.load(Ordering::Acquire));
    }

    pub(crate) fn accepts_event_generation(&self, generation: Option<&str>) -> bool {
        let accepted = generation.is_none_or(|generation| {
            self.inner
                .runtime
                .lock()
                .ok()
                .is_some_and(|r| r.active_generation.as_deref() == Some(generation))
        });
        if !accepted {
            if let Ok(mut d) = self.inner.diagnostics.lock() {
                d.dropped = d.dropped.saturating_add(1);
            }
        }
        accepted
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
        let result = self.submit_core_bridge_inner(payload, context, forbidden_secret);
        if result.is_err() {
            if let Ok(mut d) = self.inner.diagnostics.lock() {
                d.rejected = d.rejected.saturating_add(1);
            }
        }
        result
    }

    fn submit_core_bridge_inner(
        &self,
        payload: &str,
        context: &CoreLogContext,
        forbidden_secret: Option<&str>,
    ) -> Result<bool, ()> {
        if payload.len() > ERROR_BODY_LIMIT {
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
        let cleaned = forbidden_secret
            .filter(|s| !s.is_empty())
            .map(|secret| payload.replace(secret, "[REDACTED]"));
        let envelope: CoreTelemetryEnvelope =
            serde_json::from_str(cleaned.as_deref().unwrap_or(payload)).map_err(|_| ())?;
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
        let runtime_event = match event {
            "core.initialize.completed" => Some("core.ready"),
            "core.readiness.reached"
                if attributes
                    .and_then(|a| a.get("host_state"))
                    .and_then(Value::as_str)
                    == Some("ready") =>
            {
                Some("chat.ready")
            }
            "chat.finished" => Some("chat.finished"),
            "tts.synthesis.finished"
            | "tts.synthesis.ready"
            | "tts.synthesis.failed"
            | "tts.synthesis.cancelled" => Some("tts.finished"),
            "reply.repair.finished" => Some("reply.repair.finished"),
            "legacy_import.recovery.failed" => Some("migration.recovery"),
            _ => None,
        };
        if let Some(name) = runtime_event {
            let mut details = details_from_attributes(severity, attributes);
            details.outcome = outcome_attribute(attributes).or_else(|| {
                Some(
                    if event.ends_with("failed") {
                        "failed"
                    } else if event.ends_with("cancelled") {
                        "cancelled"
                    } else {
                        "success"
                    }
                    .into(),
                )
            });
            self.submit_detailed_event(name, operation_id, details);
        }

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
            .or_else(|| allowlisted_runtime_warning(source, severity, event, attributes))
            .or_else(|| {
                (matches!(severity, "error" | "warning")
                    && !matches!(event, "core.error.unhandled" | "core.stderr.detected")
                    && attributes.is_some_and(|a| {
                        a.get("diagnostic").is_some() || a.get("exception_stack").is_some()
                    }))
                .then(|| {
                    (
                        source,
                        stable_attribute(attributes, "code")
                            .unwrap_or_else(|| "RUNTIME_ERROR".into()),
                    )
                })
            });
        if let Some((component, code)) = report {
            let candidate = TelemetryErrorCandidate {
                evidence: evidence::from_attributes(attributes),
                details: details_from_attributes(severity, attributes),
                schema: 2,
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

    fn submit_detailed_event(
        &self,
        name: &str,
        operation_id: Option<&str>,
        details: DiagnosticDetail,
    ) {
        let generation = self.diagnostic_context().generation;
        self.submit_detailed_event_generation(name, operation_id, details, generation);
    }

    fn submit_detailed_event_generation(
        &self,
        name: &str,
        operation_id: Option<&str>,
        details: DiagnosticDetail,
        generation: Option<String>,
    ) -> bool {
        let Some((installation_id, epoch)) = self.installation_context() else {
            return false;
        };
        let mut diagnostics = self.diagnostic_context();
        diagnostics.generation = generation;
        let item = RuntimeEventItem {
            installation_id,
            run_id: self.inner.run_id.clone(),
            app_version: env!("CARGO_PKG_VERSION").into(),
            platform: platform_name().into(),
            os_version: os_version(),
            arch: std::env::consts::ARCH.into(),
            event: name.into(),
            feature: None,
            duration_ms: details.elapsed_ms,
            from_version: None,
            to_version: None,
            error_code: None,
            operation_id: operation_id.and_then(|id| valid_token(id, 128).map(str::to_string)),
            details,
            diagnostics,
        };
        self.enqueue_at_epoch(TelemetryRecord::Event(item), epoch)
    }

    fn diagnostic_context(&self) -> DiagnosticContext {
        DiagnosticContext {
            build_id: env!("SAKURA_BUILD_ID").to_string(),
            environment: env!("SAKURA_TELEMETRY_ENV").to_string(),
            generation: self
                .inner
                .runtime
                .lock()
                .ok()
                .and_then(|r| r.active_generation.clone()),
            occurred_ms: self.inner.started_at.elapsed().as_millis() as u64,
        }
    }

    pub fn submit_app_started(&self) {
        self.submit_runtime_event("app.started", None, None, None, None, None);
    }

    pub fn submit_app_ready(&self) {
        self.submit_runtime_event(
            "shell.ready",
            None,
            Some(self.inner.started_at.elapsed().as_millis() as u64),
            None,
            None,
            None,
        );
    }

    fn pause(&self) {
        self.inner.enabled.store(false, Ordering::Release);
        if let Ok(outbox) = self.inner.outbox.lock() {
            outbox.clear();
        }
        self.bump_epoch();
    }

    fn bump_epoch(&self) {
        if let Ok(mut reports) = self.inner.reports.lock() {
            reports.clear();
        }
        if let Ok(mut d) = self.inner.diagnostics.lock() {
            *d = SenderDiagnostics::default();
        }
        if let Ok(mut last) = self.inner.summary_sent.lock() {
            *last = (0, 0, 0);
        }
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

    fn flush_summaries(&self, force: bool) {
        let now = self.inner.started_at.elapsed().as_millis() as u64;
        let previous = self.inner.summary_at.load(Ordering::Acquire);
        if !force && now.saturating_sub(previous) < 60_000 {
            return;
        }
        if self
            .inner
            .summary_at
            .compare_exchange(previous, now, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return;
        }
        let reports = self
            .inner
            .reports
            .lock()
            .map(|mut reports| {
                reports
                    .values_mut()
                    .filter_map(|r| {
                        if !r.dirty {
                            return None;
                        }
                        r.dirty = false;
                        Some(r.clone())
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for report in reports {
            let accepted = self.submit_detailed_event_generation(
                "error.repeated",
                None,
                DiagnosticDetail {
                    fingerprint: Some(report.fingerprint.clone()),
                    occurrence_count: Some(report.count),
                    ..Default::default()
                },
                report.generation.clone(),
            );
            if !accepted {
                if let Ok(mut reports) = self.inner.reports.lock() {
                    if let Some(item) = reports.get_mut(&format!(
                        "{}:{}",
                        report.generation.as_deref().unwrap_or(""),
                        report.fingerprint
                    )) {
                        item.dirty = true;
                    }
                }
            }
        }
        let details = self.inner.diagnostics.lock().ok().and_then(|d| {
            (d.dropped + d.failed + d.rejected > 0).then(|| DiagnosticDetail {
                dropped: Some(d.dropped),
                failed: Some(d.failed),
                rejected: Some(d.rejected),
                ..Default::default()
            })
        });
        if let Some(details) = details {
            let current = (
                details.dropped.unwrap_or(0),
                details.failed.unwrap_or(0),
                details.rejected.unwrap_or(0),
            );
            if let Ok(mut last) = self.inner.summary_sent.lock() {
                if *last != current
                    && self.submit_detailed_event_generation(
                        "diagnostics.summary",
                        None,
                        details,
                        self.diagnostic_context().generation,
                    )
                {
                    *last = current;
                }
            }
        }
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
            operation_id: None,
            details: DiagnosticDetail::default(),
            diagnostics: self.diagnostic_context(),
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

    fn submit_error_candidate(&self, mut candidate: TelemetryErrorCandidate) -> Result<bool, ()> {
        evidence::bound(&mut candidate.evidence);
        candidate
            .details
            .severity
            .get_or_insert_with(|| "error".into());
        candidate
            .details
            .impact
            .get_or_insert_with(|| "unavailable".into());
        validate_error_candidate(&candidate)?;
        let Some((installation_id, epoch)) = self.installation_context() else {
            return Ok(false);
        };
        let now_ms = self.inner.started_at.elapsed().as_millis() as u64;
        let breadcrumb_state = self.inner.breadcrumbs.lock().map_err(|_| ())?;
        let breadcrumbs = project_breadcrumbs(&breadcrumb_state, now_ms);
        drop(breadcrumb_state);
        // Compare the actual failure, not a lossy code or a home-grown digest.
        let generation = self.diagnostic_context().generation;
        let key = serde_json::to_string(&json!([
            generation,
            candidate.component,
            candidate.event,
            candidate.code,
            candidate.details.reason_code,
            candidate.details.stage,
            candidate.evidence.get("diagnostic"),
            candidate.evidence.get("exception_stack"),
            candidate.evidence.get("exception_chain"),
            candidate.stack,
        ]))
        .map_err(|_| ())?;
        let fingerprint = Uuid::new_v4().to_string();
        if let Ok(mut reports) = self.inner.reports.lock() {
            if let Some(report) = reports.get_mut(&key) {
                report.count = report.count.saturating_add(1);
                report.dirty = true;
                return Ok(false);
            }
            if reports.len() < 128 {
                reports.insert(
                    key.clone(),
                    ReportCount {
                        fingerprint: fingerprint.clone(),
                        generation,
                        count: 1,
                        dirty: false,
                    },
                );
            }
        }
        let mut report = ErrorReport {
            evidence: candidate.evidence,
            fingerprint_version: 3,
            details: candidate.details.clone(),
            diagnostics: self.diagnostic_context(),
            schema: 3,
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
        // Bound the transport by bytes, including UTF-8 and JSON escaping.
        // Trim individual large fields visibly, never discard an entire error.
        while serde_json::to_vec(&report).map_err(|_| ())?.len() > ERROR_BODY_LIMIT {
            let Some((_, largest)) = report
                .evidence
                .iter_mut()
                .filter(|(_, v)| v.as_str().is_some_and(|s| s.chars().count() > 128))
                .max_by_key(|(_, v)| v.as_str().map_or(0, str::len))
            else {
                if report.breadcrumbs.pop().is_none() {
                    return Err(());
                }
                continue;
            };
            let text = largest.as_str().unwrap();
            *largest = Value::String(crate::runtime_log::sanitize_diagnostic(
                text,
                &[],
                text.chars().count() / 2,
            ));
        }
        let accepted = self.enqueue_at_epoch(TelemetryRecord::Error(report), epoch);
        if !accepted {
            if let Ok(mut reports) = self.inner.reports.lock() {
                reports.remove(&key);
            }
        }
        Ok(accepted)
    }

    fn submit_model_call(&self, candidate: TelemetryModelCallMetricV1) -> Result<bool, ()> {
        validate_model_call(&candidate)?;
        let Some((installation_id, epoch)) = self.installation_context() else {
            return Ok(false);
        };
        let item = ModelCallItem {
            request: candidate.request,
            diagnostics: self.diagnostic_context(),
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
        if matches!(
            event,
            "webview.command.started"
                | "webview.command.completed"
                | "ipc.request.started"
                | "ipc.request.completed"
        ) {
            return;
        }
        if !matches!(source, "rust" | "core" | "webview" | "plugin")
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
            diagnostic: attributes
                .and_then(|a| a.get("diagnostic"))
                .and_then(Value::as_str)
                .map(|s| crate::runtime_log::sanitize_diagnostic(s, &[], 512)),
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
        let ordinary = match &record {
            TelemetryRecord::ModelCall(item) => item.outcome == "success",
            TelemetryRecord::Event(item) => !matches!(
                item.event.as_str(),
                "chat.finished"
                    | "tts.finished"
                    | "migration.failed"
                    | "migration.recovery"
                    | "diagnostics.summary"
                    | "error.repeated"
            ),
            TelemetryRecord::Error(_) => false,
        };
        if ordinary && self.inner.sender.max_capacity() >= 128 && self.inner.sender.capacity() <= 32
        {
            if let Ok(mut d) = self.inner.diagnostics.lock() {
                d.dropped += 1;
            }
            return false;
        }
        let mut durable = false;
        if let TelemetryRecord::Error(report) = &record {
            if let Ok(outbox) = self.inner.outbox.lock() {
                if !self.inner.enabled.load(Ordering::Acquire)
                    || self.inner.epoch.load(Ordering::Acquire) != epoch
                {
                    return false;
                }
                durable = outbox.save(report).is_ok();
                if !durable {
                    if let Ok(mut d) = self.inner.diagnostics.lock() {
                        d.failed += 1;
                    }
                }
            }
        }
        let queued = QueuedRecord { epoch, record };
        match self.inner.sender.try_send(queued) {
            Ok(()) => true,
            Err(_) => {
                if let Ok(mut diagnostics) = self.inner.diagnostics.lock() {
                    diagnostics.dropped = diagnostics.dropped.saturating_add(1);
                }
                durable
            }
        }
    }
}

fn spawn_sender(
    inner: Arc<TelemetryInner>,
    receiver: mpsc::Receiver<QueuedRecord>,
    control: watch::Receiver<u64>,
) {
    let id = inner
        .runtime
        .lock()
        .ok()
        .and_then(|r| r.installation_id.clone());
    let restored = inner
        .outbox
        .lock()
        .map(|o| o.pending(id.as_deref()))
        .unwrap_or_default();
    let _ = thread::Builder::new()
        .name("sakura-telemetry-sender".to_string())
        .spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build();
            if let Ok(runtime) = runtime {
                runtime.block_on(sender_loop(inner, receiver, control, restored));
            }
        });
}

async fn sender_loop(
    inner: Arc<TelemetryInner>,
    mut receiver: mpsc::Receiver<QueuedRecord>,
    mut control: watch::Receiver<u64>,
    restored: Vec<ErrorReport>,
) {
    let mut deferred: VecDeque<_> = restored
        .into_iter()
        .map(|report| QueuedRecord {
            epoch: inner.epoch.load(Ordering::Acquire),
            record: TelemetryRecord::Error(report),
        })
        .collect();
    let mut retry_at = Instant::now() + Duration::from_secs(60);
    while !inner.stopping.load(Ordering::Acquire)
        || (inner.started_at.elapsed().as_millis() as u64)
            < inner.drain_until.load(Ordering::Acquire)
    {
        TelemetryService {
            inner: Arc::clone(&inner),
        }
        .flush_summaries(false);
        let first = if let Some(item) = deferred.pop_front() {
            item
        } else {
            loop {
                if inner.stopping.load(Ordering::Acquire) && receiver.is_empty() {
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
                if Instant::now() >= retry_at
                    && inner.enabled.load(Ordering::Acquire)
                    && receiver.is_empty()
                {
                    retry_at = Instant::now() + Duration::from_secs(60);
                    if let Ok(outbox) = inner.outbox.lock() {
                        // Read identity and epoch together while reset cannot clear
                        // or replace the pending files. Old reports keep this epoch.
                        let service = TelemetryService {
                            inner: Arc::clone(&inner),
                        };
                        if let Some((id, epoch)) = service.installation_context() {
                            for report in outbox.pending(Some(&id)) {
                                deferred.push_back(QueuedRecord {
                                    epoch,
                                    record: TelemetryRecord::Error(report),
                                });
                            }
                        }
                    }
                    if let Some(item) = deferred.pop_front() {
                        break item;
                    }
                }
                match tokio::time::timeout(Duration::from_millis(25), receiver.recv()).await {
                    Ok(Some(item)) => break item,
                    Ok(None) => return,
                    Err(_) => {
                        TelemetryService {
                            inner: Arc::clone(&inner),
                        }
                        .flush_summaries(false);
                        continue;
                    }
                }
            }
        };
        let epoch = inner.epoch.load(Ordering::Acquire);
        if first.epoch != epoch || !inner.enabled.load(Ordering::Acquire) {
            continue;
        }
        let endpoint = record_endpoint(&first.record);
        let mut records = vec![first.record];
        if endpoint != "/v3/errors" {
            while records.len() < 10 {
                match receiver.try_recv() {
                    Ok(next)
                        if next.epoch == epoch && record_endpoint(&next.record) == endpoint =>
                    {
                        records.push(next.record);
                        if encode_records(endpoint, &records).is_none() {
                            let record = records.pop().expect("just added");
                            deferred.push_back(QueuedRecord { epoch, record });
                            break;
                        }
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
        // A new batch observes proxy changes without restarting the sender.
        let request_client = match reqwest::Client::builder()
            .timeout(inner.http_timeout)
            .build()
        {
            Ok(client) => client,
            Err(_) => {
                if let Ok(mut diagnostics) = inner.diagnostics.lock() {
                    diagnostics.failed = diagnostics.failed.saturating_add(records.len() as u64);
                }
                continue;
            }
        };
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
            if inner.stopping.load(Ordering::Acquire)
                && (inner.started_at.elapsed().as_millis() as u64)
                    >= inner.drain_until.load(Ordering::Acquire)
            {
                request.abort();
                return;
            }
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
        if result.is_ok_and(|response| response.status() == reqwest::StatusCode::ACCEPTED) {
            if let Ok(outbox) = inner.outbox.lock() {
                for record in &records {
                    if let TelemetryRecord::Error(report) = record {
                        outbox.remove(&report.report_id);
                    }
                }
            }
        } else if let Ok(mut diagnostics) = inner.diagnostics.lock() {
            diagnostics.failed = diagnostics.failed.saturating_add(records.len() as u64);
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
        TelemetryRecord::Error(_) => "/v3/errors",
        TelemetryRecord::Event(_) => "/v2/events",
        TelemetryRecord::ModelCall(_) => "/v2/model-calls",
    }
}

fn encode_records(endpoint: &str, records: &[TelemetryRecord]) -> Option<Vec<u8>> {
    let (value, limit) = match endpoint {
        "/v3/errors" => {
            if records.len() != 1 {
                return None;
            }
            let TelemetryRecord::Error(report) = records.first()? else {
                return None;
            };
            (serde_json::to_value(report).ok()?, ERROR_BODY_LIMIT)
        }
        "/v2/events" => {
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
            (json!({"schema": 2, "items": items}), EVENT_BODY_LIMIT)
        }
        "/v2/model-calls" => {
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
            (json!({"schema": 2, "items": items}), MODEL_CALL_BODY_LIMIT)
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

fn validate_core_error_candidate(candidate: &TelemetryErrorCandidate) -> Result<(), ()> {
    if candidate.component != "core" || candidate.event != "core.error.unhandled" {
        return Err(());
    }
    validate_error_candidate(candidate)
}

fn validate_error_candidate(candidate: &TelemetryErrorCandidate) -> Result<(), ()> {
    if !matches!(candidate.schema, 1 | 2 | 3)
        || !validate_detail(&candidate.details)
        || !valid_event_name(&candidate.component, 32)
        || !valid_event_name(&candidate.event, 96)
        || !valid_code(&candidate.code)
        || candidate.evidence.len() > 40
        || candidate
            .operation_id
            .as_deref()
            .is_some_and(|value| valid_token(value, 128).is_none())
        || candidate
            .exception_type
            .as_deref()
            .is_some_and(|value| valid_token(value, 128).is_none())
        || candidate.stack.len() > 16
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
    if !matches!(candidate.schema, 1 | 2)
        || !validate_request(&candidate.request)
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
        .line
        .is_none_or(|line| line > 0 && line <= 10_000_000 && frame.file.is_some())
        && frame
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

pub(crate) fn validate_detail(detail: &DiagnosticDetail) -> bool {
    let Ok(Value::Object(fields)) = serde_json::to_value(detail) else {
        return false;
    };
    for (key, value) in fields {
        if let Some(value) = value.as_str() {
            if key == "file" {
                if !valid_stack_frame(&SafeStackFrame {
                    module: None,
                    function: None,
                    file: Some(value.into()),
                    line: None,
                }) {
                    return false;
                }
            } else if valid_token(value, 128).is_none() {
                return false;
            }
            let choices: &[&str] = match key.as_str() {
                "severity" => &["warning", "error", "critical"],
                "impact" => &["unavailable", "degraded", "diagnostic"],
                "probeOutcome" => &[
                    "ready",
                    "timeout",
                    "spawn_failed",
                    "exited",
                    "invalid_output",
                    "unavailable",
                ],
                "recoveryOutcome" => &["success", "failed", "skipped", "unknown"],
                "outcome" => &["success", "failed", "cancelled", "degraded", "skipped"],
                "repairOutcome" => &["valid", "invalid", "request_failed", "cancelled"],
                _ => &[],
            };
            if !choices.is_empty() && !choices.contains(&value) {
                return false;
            }
            if key.ends_with("Code") && !valid_code(value) {
                return false;
            }
        } else if let Some(value) = value.as_u64() {
            if value > 9_007_199_254_740_991 {
                return false;
            }
        }
    }
    if detail.file.is_none() && (detail.line.is_some() || detail.column.is_some()) {
        return false;
    }
    if detail.line.is_some_and(|v| v == 0 || v > 10_000_000)
        || detail.column.is_some_and(|v| v == 0 || v > 10_000_000)
    {
        return false;
    }
    detail
        .exit_code
        .is_none_or(|v| (-2_147_483_648..=4_294_967_295).contains(&v))
}

fn validate_request(request: &RequestDiagnostic) -> bool {
    request.fault_domain.as_deref().is_none_or(|s| {
        [
            "authentication",
            "rate_limit",
            "provider",
            "transport",
            "protocol",
            "context",
            "compatibility",
            "cancelled",
            "unknown",
        ]
        .contains(&s)
    }) && request.reason_code.as_deref().is_none_or(valid_code)
        && request.http_status.is_none_or(|s| (100..=599).contains(&s))
        && request.stage.as_deref().is_none_or(|s| {
            [
                "connect", "read", "decode", "request", "response", "unknown",
            ]
            .contains(&s)
        })
        && request.attempt_count.is_none_or(|s| s <= 1_000_000)
        && request
            .compatibility_fallback
            .as_deref()
            .is_none_or(|s| ["response_format", "temperature", "runtime_context_role"].contains(&s))
}

fn details_from_attributes(severity: &str, attributes: Option<&Value>) -> DiagnosticDetail {
    let mut detail = attributes
        .and_then(|a| a.get("diagnostic_detail"))
        .and_then(|d| serde_json::from_value::<DiagnosticDetail>(d.clone()).ok())
        .filter(validate_detail)
        .unwrap_or_default();
    if matches!(severity, "warning" | "error" | "critical") {
        detail.severity = Some(severity.into());
        detail.impact = Some(
            if severity == "warning" {
                "degraded"
            } else {
                "unavailable"
            }
            .into(),
        );
    }
    macro_rules! token {
        ($field:ident, $key:expr) => {
            if detail.$field.is_none() {
                detail.$field = stable_token_attribute(attributes, $key, 128);
            }
        };
    }
    token!(stage, "stage");
    token!(cause_type, "cause_type");
    token!(primary_code, "primary_code");
    token!(recovery_code, "recovery_code");
    token!(recovery_outcome, "recovery_outcome");
    token!(probe_outcome, "probe_outcome");
    token!(repair_reason, "repair_reason");
    token!(repair_outcome, "repair_outcome");
    detail.reason_code = detail
        .reason_code
        .or_else(|| stable_attribute(attributes, "reason_code"))
        .or_else(|| stable_attribute(attributes, "provider_error_code"));
    detail.timeout_ms = integer_attribute(attributes, "timeout_ms")
        .or_else(|| integer_attribute(attributes, "deadline_ms"));
    detail.elapsed_ms = integer_attribute(attributes, "elapsed_ms");
    detail.exit_code = attributes
        .and_then(|a| a.get("exit_code").or_else(|| a.get("return_code")))
        .and_then(Value::as_i64);
    detail.child_exited = attributes
        .and_then(|a| a.get("child_exited"))
        .and_then(Value::as_bool);
    detail.source_exists = attributes
        .and_then(|a| a.get("source_exists"))
        .and_then(Value::as_bool);
    detail.staged_exists = attributes
        .and_then(|a| a.get("staged_exists"))
        .and_then(Value::as_bool);
    detail.backup_exists = attributes
        .and_then(|a| a.get("backup_exists"))
        .and_then(Value::as_bool);
    if detail.file.is_none() {
        detail.file = attributes
            .and_then(|a| a.get("source_file"))
            .and_then(Value::as_str)
            .map(str::to_string);
        detail.line =
            integer_attribute(attributes, "source_line").and_then(|v| u32::try_from(v).ok());
    }
    detail.surface = detail
        .surface
        .or_else(|| stable_token_attribute(attributes, "window_label", 64));
    if validate_detail(&detail) {
        detail
    } else {
        DiagnosticDetail {
            severity: matches!(severity, "warning" | "error" | "critical").then(|| severity.into()),
            impact: matches!(severity, "warning" | "error" | "critical")
                .then(|| "diagnostic".into()),
            outcome: outcome_attribute(attributes),
            ..Default::default()
        }
    }
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
            diagnostic: item.diagnostic.clone(),
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
        "core.spawn.failed" if source == "rust" => match token_attribute(
            attributes,
            "category",
            &["unexpected_exit", "hello_timeout"],
        )
        .as_deref()
        {
            Some("unexpected_exit") => Some(("rust", "CORE_UNEXPECTED_EXIT".to_string())),
            Some("hello_timeout") => Some(("rust", "CORE_HELLO_TIMEOUT".to_string())),
            _ => None,
        },
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
    let codes = ["provider_error_code", "reason_code", "code"]
        .into_iter()
        .filter_map(|key| stable_attribute(attributes, key))
        .collect::<Vec<_>>();
    if codes.iter().any(|code| {
        matches!(
            code.as_str(),
            "TTS_DISABLED" | "TTS_PROVIDER_NOT_SELECTED" | "REQUEST_CANCELLED" | "TTS_PORT_IN_USE"
        )
    }) {
        return None;
    }
    codes
        .into_iter()
        .find(|code| selected_tts_code(event, code))
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
                | "TTS_RUNTIME_UNAVAILABLE"
                | "TTS_AUDIO_INVALID"
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

#[tauri::command]
pub(crate) fn settings_telemetry_get(
    window: WebviewWindow,
    telemetry: State<'_, TelemetryService>,
) -> Result<TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.snapshot()
}

#[tauri::command]
pub(crate) fn settings_telemetry_set_enabled(
    window: WebviewWindow,
    telemetry: State<'_, TelemetryService>,
    enabled: bool,
) -> Result<TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.set_enabled(enabled)
}

#[tauri::command]
pub(crate) fn settings_telemetry_regenerate_installation_id(
    window: WebviewWindow,
    telemetry: State<'_, TelemetryService>,
) -> Result<TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.regenerate_installation_id()
}

#[tauri::command]
pub(crate) fn settings_telemetry_open_documentation(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    open_documentation()
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

    // These are deadlock guards, not latency requirements for shared CI runners.
    const TEST_WAIT: Duration = Duration::from_secs(10);
    const BLOCKED_HTTP_TIMEOUT: Duration = Duration::from_secs(60);

    struct TestServer {
        endpoint: String,
        requests: std_mpsc::Receiver<(String, Vec<u8>)>,
        cancellations: std_mpsc::Receiver<()>,
        stopping: Arc<AtomicBool>,
        worker: Option<JoinHandle<()>>,
    }

    impl TestServer {
        fn start(status: u16, response_delay: Duration) -> Self {
            Self::start_with_response(status, Some(response_delay))
        }

        fn blocked() -> Self {
            Self::start_with_response(202, None)
        }

        fn next_request(&self, service: &TelemetryService) -> (String, Vec<u8>) {
            self.requests
                .recv_timeout(TEST_WAIT)
                .unwrap_or_else(|error| {
                    panic!(
                        "loopback request not received: {error}; sender diagnostics: {:?}",
                        service.inner.diagnostics.lock().unwrap()
                    )
                })
        }

        fn start_with_response(status: u16, response_delay: Option<Duration>) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let (sender, requests) = std_mpsc::channel();
            let (cancelled, cancellations) = std_mpsc::channel();
            let stopping = Arc::new(AtomicBool::new(false));
            let worker_stopping = Arc::clone(&stopping);
            let worker = std::thread::spawn(move || {
                let mut connections = Vec::new();
                while !worker_stopping.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            let sender = sender.clone();
                            let cancelled = cancelled.clone();
                            let stopping = Arc::clone(&worker_stopping);
                            connections.push(std::thread::spawn(move || {
                                if let Some(request) = read_request(stream.try_clone().unwrap()) {
                                    let _ = sender.send(request);
                                } else {
                                    return;
                                }
                                let mut stream = stream;
                                if let Some(delay) = response_delay {
                                    std::thread::sleep(delay);
                                } else {
                                    // Hold the response until the client cancels. A fixed sleep
                                    // can finish before the test thread resumes on a busy runner.
                                    stream
                                        .set_read_timeout(Some(Duration::from_millis(100)))
                                        .unwrap();
                                    while !stopping.load(Ordering::Acquire) {
                                        match stream.read(&mut [0_u8; 1]) {
                                            Ok(0) => {
                                                let _ = cancelled.send(());
                                                return;
                                            }
                                            Err(error)
                                                if matches!(
                                                    error.kind(),
                                                    std::io::ErrorKind::ConnectionReset
                                                        | std::io::ErrorKind::ConnectionAborted
                                                ) =>
                                            {
                                                let _ = cancelled.send(());
                                                return;
                                            }
                                            Err(error)
                                                if !matches!(
                                                    error.kind(),
                                                    std::io::ErrorKind::WouldBlock
                                                        | std::io::ErrorKind::TimedOut
                                                ) => return,
                                            _ => {}
                                        }
                                    }
                                    return;
                                }
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
                            }));
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(_) => break,
                    }
                }
                for connection in connections {
                    connection.join().unwrap();
                }
            });
            Self {
                endpoint: format!("http://{address}"),
                requests,
                cancellations,
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
        // On Windows an accepted socket inherits the nonblocking listener mode.
        // A read before headers/body arrive would otherwise return WouldBlock
        // and silently discard a valid request instead of honoring the timeout.
        stream.set_nonblocking(false).ok()?;
        stream.set_read_timeout(Some(TEST_WAIT)).ok()?;
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

    #[test]
    fn loopback_reader_waits_for_headers_and_body_on_an_accepted_nonblocking_socket() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (stream, _) = listener.accept().unwrap();
        // Windows accepts inherit the listener's nonblocking mode. Set it
        // explicitly so this regression also exercises that condition on Unix.
        stream.set_nonblocking(true).unwrap();
        let (sender, received) = std_mpsc::channel();
        let reader = std::thread::spawn(move || {
            sender.send(read_request(stream)).unwrap();
        });
        assert!(matches!(
            received.recv_timeout(Duration::from_millis(100)),
            Err(std_mpsc::RecvTimeoutError::Timeout)
        ));
        client
            .write_all(b"POST /v2/events HTTP/1.1\r\nContent-Length: 2\r\n\r\n")
            .unwrap();
        assert!(matches!(
            received.recv_timeout(Duration::from_millis(100)),
            Err(std_mpsc::RecvTimeoutError::Timeout)
        ));
        client.write_all(b"{}").unwrap();
        assert_eq!(
            received.recv_timeout(TEST_WAIT).unwrap(),
            Some(("/v2/events".to_string(), b"{}".to_vec()))
        );
        reader.join().unwrap();
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
            operation_id: None,
            details: DiagnosticDetail::default(),
            diagnostics: service.diagnostic_context(),
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
        let deadline = Instant::now() + TEST_WAIT;
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
        let deadline = Instant::now() + TEST_WAIT;
        while Instant::now() < deadline {
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
        let server = TestServer::blocked();
        let (root, service) = service_for(&server, "shutdown", 4, BLOCKED_HTTP_TIMEOUT);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        server.next_request(&service);

        let started = Instant::now();
        service.shutdown();
        assert!(started.elapsed() < TEST_WAIT);
        assert!(wait_for_sender_exit(&service));
        server.cancellations.recv_timeout(TEST_WAIT).unwrap();
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
        assert!(validate_error_candidate(&arbitrary.error.unwrap()).is_ok());
    }

    #[test]
    fn internal_webview_error_is_reported_but_core_bridge_cannot_spoof_its_source() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "webview-error", 4, TEST_WAIT);
        service.observe_runtime_event(
            "webview",
            "error",
            "webview",
            "webview.error.unhandled",
            Some("operation-7"),
            Some(&json!({"code": "WEBVIEW_UNHANDLED_ERROR"})),
        );
        let request = server.next_request(&service);
        assert_eq!(request.0, "/v3/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["error"]["component"], "webview");
        assert_eq!(body["error"]["event"], "webview.error.unhandled");
        assert_eq!(body["error"]["code"], "WEBVIEW_UNHANDLED_ERROR");

        let spoofed = TelemetryErrorCandidate {
            evidence: ErrorEvidence::new(),
            details: DiagnosticDetail::default(),
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
    fn core_hello_timeout_is_reported_with_a_specific_safe_code() {
        assert_eq!(
            allowlisted_runtime_error(
                "rust",
                "core.spawn.failed",
                Some(&json!({"category": "hello_timeout"})),
            ),
            Some(("rust", "CORE_HELLO_TIMEOUT".to_string()))
        );
        assert_eq!(
            allowlisted_runtime_error(
                "rust",
                "core.spawn.failed",
                Some(&json!({"category": "unexpected_exit"})),
            ),
            Some(("rust", "CORE_UNEXPECTED_EXIT".to_string()))
        );
        assert_eq!(
            allowlisted_runtime_error(
                "webview",
                "core.spawn.failed",
                Some(&json!({"category": "hello_timeout"})),
            ),
            None
        );

        let candidate = TelemetryErrorCandidate {
            evidence: ErrorEvidence::new(),
            details: DiagnosticDetail::default(),
            schema: 1,
            component: "rust".to_string(),
            event: "core.spawn.failed".to_string(),
            code: "CORE_HELLO_TIMEOUT".to_string(),
            operation_id: None,
            exception_type: None,
            stack: Vec::new(),
        };
        assert!(validate_error_candidate(&candidate).is_ok());
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
        let (root, service) = service_for(&server, "tts-error", 4, TEST_WAIT);
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

        let terminal = server.next_request(&service);
        assert_eq!(terminal.0, "/v2/events");
        let request = server.next_request(&service);
        assert_eq!(request.0, "/v3/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["operationId"], "operation-tts-7");
        assert_eq!(body["error"]["component"], "tts");
        assert_eq!(body["error"]["event"], "tts.synthesis.failed");
        assert_eq!(body["error"]["code"], "TTS_JOB_RESULT_INVALID");
        assert_eq!(body["error"]["exceptionType"], "RuntimeError");
        assert_eq!(body["evidence"]["diagnostic"], "PRIVATE PROVIDER RESPONSE");

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

        let rejected = TelemetryErrorCandidate {
            evidence: ErrorEvidence::new(),
            details: DiagnosticDetail::default(),
            schema: 1,
            component: "tts".to_string(),
            event: "tts.synthesis.failed".to_string(),
            code: "TTS_DISABLED".to_string(),
            operation_id: None,
            exception_type: None,
            stack: Vec::new(),
        };
        assert!(validate_error_candidate(&rejected).is_ok());
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
        let (root, service) = service_for(&server, "warning-dedupe", 4, TEST_WAIT);
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

        let first = server.next_request(&service);
        assert_eq!(first.0, "/v2/events");
        let request = server.next_request(&service);
        assert_eq!(request.0, "/v3/errors");
        let body: Value = serde_json::from_slice(&request.1).unwrap();
        assert_eq!(body["error"]["component"], "memory");
        assert_eq!(body["error"]["event"], "memory.recall.unavailable");
        assert_eq!(body["error"]["code"], "MEMORY_NOT_READY");
        assert_eq!(body["error"]["exceptionType"], "MemoryUnavailable");
        assert_eq!(body["evidence"]["diagnostic"], "PRIVATE MEMORY STATE");
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
            operation_id: None,
            details: DiagnosticDetail::default(),
            diagnostics: DiagnosticContext {
                build_id: "test".into(),
                environment: "acceptance".into(),
                generation: None,
                occurred_ms: 0,
            },
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
        let body = encode_records("/v2/events", &[TelemetryRecord::Event(event)]).unwrap();
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
        assert!(encode_records("/v2/events", &[]).is_none());
        let ten = (0..10)
            .map(|_| TelemetryRecord::Event(event_item_for_encoding()))
            .collect::<Vec<_>>();
        assert!(encode_records("/v2/events", &ten).is_some());
        let eleven = (0..11)
            .map(|_| TelemetryRecord::Event(event_item_for_encoding()))
            .collect::<Vec<_>>();
        assert!(encode_records("/v2/events", &eleven).is_none());
        let mut oversized = event_item_for_encoding();
        oversized.app_version = "x".repeat(EVENT_BODY_LIMIT);
        assert!(encode_records("/v2/events", &[TelemetryRecord::Event(oversized)]).is_none());
    }

    fn event_item_for_encoding() -> RuntimeEventItem {
        RuntimeEventItem {
            operation_id: None,
            details: DiagnosticDetail::default(),
            diagnostics: DiagnosticContext {
                build_id: "test".into(),
                environment: "acceptance".into(),
                generation: None,
                occurred_ms: 0,
            },
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
        let server = TestServer::blocked();
        let (root, service) = service_for(&server, "disable", 8, BLOCKED_HTTP_TIMEOUT);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        let first = server.next_request(&service);
        assert_eq!(first.0, "/v2/events");
        for _ in 0..5 {
            assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        }
        service.set_enabled(false).unwrap();
        assert!(!service.enqueue(TelemetryRecord::Event(event_item_for_encoding())));
        service.set_enabled(true).unwrap();
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "shell.ready"))));
        let second = server.next_request(&service);
        let body: Value = serde_json::from_slice(&second.1).unwrap();
        assert_eq!(body["items"].as_array().unwrap().len(), 1);
        assert_eq!(body["items"][0]["event"], "shell.ready");
        server.cancellations.recv_timeout(TEST_WAIT).unwrap();
        assert!(server
            .requests
            .recv_timeout(Duration::from_millis(400))
            .is_err());
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn regenerate_cancels_old_epoch_and_next_request_uses_new_id() {
        let server = TestServer::blocked();
        let (root, service) = service_for(&server, "regenerate", 8, BLOCKED_HTTP_TIMEOUT);
        let old_id = service.snapshot().unwrap().installation_id.unwrap();
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        let first = server.next_request(&service);
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
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "shell.ready"))));
        let second = server.next_request(&service);
        let second_body: Value = serde_json::from_slice(&second.1).unwrap();
        assert_eq!(second_body["items"][0]["installationId"], new_id);
        assert_eq!(second_body["items"].as_array().unwrap().len(), 1);
        assert_eq!(second_body["items"][0]["event"], "shell.ready");
        server.cancellations.recv_timeout(TEST_WAIT).unwrap();
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn delayed_response_within_budget_preserves_order_without_failure() {
        // Reproduce a loopback response taking longer than the former one-second
        // test HTTP deadline. Waiting longer on the channel alone cannot fix it.
        let server = TestServer::start(202, Duration::from_millis(1_200));
        let (root, service) = service_for(&server, "slow-loopback", 4, TEST_WAIT);
        service.submit_app_started();
        server.next_request(&service);
        service.submit_app_ready();
        let second = server.next_request(&service);
        let body: Value = serde_json::from_slice(&second.1).unwrap();
        assert_eq!(body["items"][0]["event"], "shell.ready");
        // The sender processes responses serially: receiving the second request
        // proves it finished handling the first response.
        assert_eq!(service.inner.diagnostics.lock().unwrap().failed, 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn queue_overflow_timeout_and_http_rejection_are_isolated() {
        let slow = TestServer::blocked();
        let (root, service) = service_for(&slow, "overflow", 2, BLOCKED_HTTP_TIMEOUT);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        slow.next_request(&service);
        for _ in 0..20 {
            let _ = service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started")));
        }
        assert!(wait_for_diagnostic(&service, |item| item.dropped) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let offline = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&offline, "offline", 4, TEST_WAIT);
        drop(offline);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let rejected = TestServer::start(500, Duration::ZERO);
        let (root, service) = service_for(&rejected, "rejected", 4, TEST_WAIT);
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);

        let timeout = TestServer::blocked();
        let (root, service) = service_for(&timeout, "timeout", 4, Duration::from_millis(50));
        assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        assert!(wait_for_diagnostic(&service, |item| item.failed) > 0);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn core_generation_stack_breadcrumb_and_fingerprint_boundaries_are_stable() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "bounds", 4, TEST_WAIT);
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
            diagnostic: None,
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
        let frames = (1..17)
            .map(|line| SafeStackFrame {
                module: Some("app.core".to_string()),
                function: Some("run".to_string()),
                file: Some("app/core.py".to_string()),
                line: Some(line),
            })
            .collect::<Vec<_>>();
        let candidate = TelemetryErrorCandidate {
            evidence: ErrorEvidence::new(),
            details: DiagnosticDetail::default(),
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
        service.shutdown();
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn original_errors_cross_log_projection_and_keep_distinct_causes() {
        use crate::runtime_log::{
            RuntimeLogConfig, RuntimeLogEvent, RuntimeLogService, Severity, Verbosity,
        };
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "original-errors", 128, TEST_WAIT);
        let mut config = RuntimeLogConfig::production(root.join("runtime.log"));
        config.level = Verbosity::Error; // warning telemetry must not depend on local verbosity
        let log = RuntimeLogService::start_with_config(config);
        log.attach_telemetry(service.clone());
        for message in [
            "no such table: memories",
            "UNIQUE constraint failed: memories.id",
        ] {
            log.submit(
                RuntimeLogEvent::rust(
                    Severity::Warning,
                    "migration",
                    "migration.import.failed",
                    "Import failed",
                )
                .attributes(json!({
                    "code": "LEGACY_IMPORT_INTERNAL", "diagnostic": message,
                    "exception_chain": format!("RuntimeError: import failed\nCaused by: {message}"),
                    "exception_stack": "at migration::import_rows:42",
                    "source_file": "desktop/src-tauri/src/migration.rs", "source_line": 42,
                    "path": "C:\\Users\\测试 用户\\Sakura\\data\\memory.db",
                    "endpoint": "https://example.test/v1?token=test-secret&model=demo",
                    "repair_reason": "duplicate_rows", "repair_outcome": "invalid",
                })),
            );
            let (endpoint, bytes) = server.next_request(&service);
            assert_eq!(endpoint, "/v3/errors");
            let report: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(report["schema"], 3);
            assert_eq!(report["evidence"]["diagnostic"], message);
            assert_eq!(
                report["evidence"]["path"],
                "C:\\Users\\测试 用户\\Sakura\\data\\memory.db"
            );
            assert_eq!(
                report["evidence"]["endpoint"],
                "https://example.test/v1?token=[REDACTED]&model=demo"
            );
            assert_eq!(report["details"]["line"], 42);
            assert_eq!(report["details"]["repairReason"], "duplicate_rows");
        }
        assert!(log.shutdown(TEST_WAIT));
        drop(log);
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn failed_errors_survive_restart_and_disable_clears_pending_reports() {
        let failing = TestServer::start(503, Duration::ZERO);
        let (root, service) = service_for(&failing, "durable-errors", 128, TEST_WAIT);
        service.observe_runtime_event(
            "rust",
            "error",
            "runtime",
            "future.error",
            None,
            Some(&json!({"diagnostic": "cannot load dependency", "code": "UNKNOWN_FAILURE"})),
        );
        let (_, sent) = failing.next_request(&service);
        let sent: Value = serde_json::from_slice(&sent).unwrap();
        assert!(wait_for_diagnostic(&service, |d| d.failed) > 0);
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let successful = TestServer::start(202, Duration::ZERO);
        let resumed = TelemetryService::initialize_with_options(
            UiConfigRepository::new(root.join("ui.json")),
            "new-run".into(),
            successful.endpoint.clone(),
            128,
            TEST_WAIT,
        );
        let (_, replayed) = successful.next_request(&resumed);
        let replayed: Value = serde_json::from_slice(&replayed).unwrap();
        assert_eq!(replayed, sent); // including report ID and original run/generation
        resumed.set_enabled(false).unwrap();
        assert!(resumed
            .inner
            .outbox
            .lock()
            .unwrap()
            .pending(resumed.installation_id().as_deref())
            .is_empty());
        resumed.shutdown();
        assert!(wait_for_sender_exit(&resumed));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn successful_and_cancelled_chat_have_real_duration_and_no_error_severity() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "chat-outcomes", 128, TEST_WAIT);
        for outcome in ["success", "cancelled"] {
            service.observe_runtime_event(
                "core",
                "info",
                "chat",
                "chat.finished",
                Some("chat-1"),
                Some(&json!({"elapsed_ms": 120, "outcome": outcome})),
            );
            let (endpoint, bytes) = server.next_request(&service);
            assert_eq!(endpoint, "/v2/events");
            let batch: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(batch["items"][0]["details"]["outcome"], outcome);
            assert_eq!(batch["items"][0]["durationMs"], 120);
            assert!(batch["items"][0]["details"]["severity"].is_null());
        }
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn acceptance_wire_capture_preserves_core_location_and_terminal() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "acceptance-wire", 128, TEST_WAIT);
        service.activate_generation("acceptance-generation");
        let context = CoreLogContext {
            generation_id: "acceptance-generation".into(),
            generation_number: 1,
            core_pid: 42,
        };
        let payloads=std::env::var("SAKURA_ACCEPTANCE_CORE_WIRE").ok().map(|p|fs::read_to_string(p).unwrap())
            .unwrap_or_else(|| r#"{"kind":"error","error":{"schema":2,"component":"core","event":"core.error.unhandled","code":"CORE_HOST_TRANSPORT_ERROR","operationId":"op-acceptance","exceptionType":"WriterError","stack":[{"file":"app/core_host/server.py","function":"send","line":737}],"details":{"severity":"error","impact":"unavailable","stage":"process_boundary","reasonCode":"TRANSPORT_WRITE_FAILED"}}}"#.into());
        let mut captured = Vec::new();
        let mut operation = "op-acceptance".to_string();
        for payload in payloads.lines().filter(|p| !p.trim().is_empty()) {
            let parsed: Value = serde_json::from_str(payload).unwrap();
            if let Some(id) = parsed
                .pointer("/modelCall/operationId")
                .and_then(Value::as_str)
            {
                operation = id.into();
            }
            assert!(service.submit_core_bridge(payload, &context, None).unwrap());
            let (endpoint, body) = server.next_request(&service);
            captured.push(
                json!({"endpoint":endpoint,"body":serde_json::from_slice::<Value>(&body).unwrap()}),
            );
        }
        service.observe_runtime_event(
            "core",
            "info",
            "chat",
            "chat.finished",
            Some(&operation),
            Some(&json!({"outcome":"failed","stage":"final_reply"})),
        );
        let (endpoint, body) = server.next_request(&service);
        captured.push(
            json!({"endpoint":endpoint,"body":serde_json::from_slice::<Value>(&body).unwrap()}),
        );
        if let Ok(path) = std::env::var("SAKURA_ACCEPTANCE_WIRE_OUTPUT") {
            fs::write(path, serde_json::to_vec_pretty(&captured).unwrap()).unwrap();
        }
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn repeat_summary_is_cumulative_and_not_regrouped_by_line() {
        let server = TestServer::start(202, Duration::ZERO);
        let (root, service) = service_for(&server, "repeat-v2", 128, TEST_WAIT);
        service.activate_generation("generation-repeat");
        for line in [10, 11, 12] {
            service.observe_runtime_event("core","warning","tts","tts.service.failed",Some("op-repeat"),Some(&json!({"reason_code":"TTS_RUNTIME_TIMEOUT","source_file":"app/voice/tts.py","source_line":line})));
        }
        let (_, first) = server.next_request(&service);
        let first: Value = serde_json::from_slice(&first).unwrap();
        service.flush_summaries(true);
        let (_, summary) = server.next_request(&service);
        let summary: Value = serde_json::from_slice(&summary).unwrap();
        assert_eq!(summary["items"][0]["details"]["occurrenceCount"], 3);
        assert_eq!(
            summary["items"][0]["details"]["fingerprint"],
            first["error"]["fingerprint"]
        );
        assert_eq!(
            summary["items"][0]["diagnostics"]["generation"],
            "generation-repeat"
        );
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn event_batches_split_by_serialized_bytes_and_reserve_failure_capacity() {
        let server = TestServer::start(202, Duration::from_millis(150));
        let (root, service) = service_for(&server, "byte-split", 128, TEST_WAIT);
        service.submit_app_started();
        server.next_request(&service);
        for _ in 0..10 {
            let mut item = event_item(&service, "chat.finished");
            item.details = DiagnosticDetail {
                stage: Some("s".repeat(128)),
                cause_type: Some("C".repeat(128)),
                surface: Some("p".repeat(128)),
                repair_reason: Some("r".repeat(128)),
                file: Some(format!("app/{}.py", "x".repeat(200))),
                ..Default::default()
            };
            assert!(service.enqueue(TelemetryRecord::Event(item)));
        }
        let mut received = 0;
        let mut batches = 0;
        while received < 10 {
            let (_, body) = server.next_request(&service);
            assert!(body.len() <= EVENT_BODY_LIMIT);
            let body: Value = serde_json::from_slice(&body).unwrap();
            let count = body["items"].as_array().unwrap().len();
            assert!(count <= 10);
            received += count;
            batches += 1;
        }
        assert!(batches > 1);
        service.shutdown();
        let _ = fs::remove_dir_all(root);
        let blocked = TestServer::blocked();
        let (root, service) = service_for(&blocked, "reserved-capacity", 128, BLOCKED_HTTP_TIMEOUT);
        service.submit_app_started();
        blocked.next_request(&service);
        for _ in 0..96 {
            assert!(service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        }
        assert!(!service.enqueue(TelemetryRecord::Event(event_item(&service, "app.started"))));
        for _ in 0..32 {
            assert!(service.enqueue(TelemetryRecord::Event(event_item(
                &service,
                "chat.finished"
            ))));
        }
        service.set_enabled(false).unwrap();
        service.shutdown();
        assert!(wait_for_sender_exit(&service));
        let _ = fs::remove_dir_all(root);
    }
}
