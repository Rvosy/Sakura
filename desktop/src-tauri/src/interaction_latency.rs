use std::{
    sync::{Mutex, MutexGuard},
    time::Instant,
};

use serde::{Deserialize, Serialize};

use crate::runtime_log::{RuntimeLogEvent, RuntimeLogService, Severity, Verbosity};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InteractionTraceContext {
    pub gesture_id: String,
    pub revision: u64,
    pub client_perf_ms: f64,
    pub client_epoch_ms: f64,
}

impl InteractionTraceContext {
    pub fn validate(&self) -> Result<(), String> {
        if !valid_gesture_id(&self.gesture_id)
            || !valid_milliseconds(self.client_perf_ms, 0.0, 10_000_000_000.0)
            || !valid_milliseconds(
                self.client_epoch_ms,
                1_000_000_000_000.0,
                10_000_000_000_000.0,
            )
        {
            return Err("INTERACTION_LATENCY_TRACE_INVALID".to_string());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FrontendTraceEntry {
    source: String,
    stage: String,
    gesture_id: String,
    revision: u64,
    perf_ms: f64,
    epoch_ms: f64,
    event_perf_ms: Option<f64>,
    event_delay_ms: Option<f64>,
    elapsed_ms: Option<f64>,
}

const ALLOWED_FRONTEND_STAGES: &[&str] = &[
    "appearance.preview.invoke-start",
    "appearance.preview.invoke-return",
    "appearance.preview.invoke-error",
    "appearance.raf-scheduled",
    "appearance.raf-callback",
    "portrait.pointerdown",
    "portrait.keydown",
    "portrait.pointerup",
    "portrait.gesture-end",
    "portrait.input",
    "portrait.value-committed",
    "portrait.raf-scheduled",
    "portrait.raf-callback",
    "portrait.frame.invoke-start",
    "portrait.frame.invoke-return",
    "portrait.frame.invoke-error",
    "portrait.gesture-start.invoke-start",
    "portrait.gesture-start.invoke-return",
    "portrait.gesture-start.invoke-error",
    "portrait.gesture-stop.invoke-start",
    "portrait.gesture-stop.invoke-return",
    "portrait.gesture-stop.invoke-error",
    "layout.pointerdown",
    "layout.keydown",
    "layout.pointerup",
    "layout.gesture-end",
    "layout.input",
    "layout.value-committed",
    "layout.raf-scheduled",
    "layout.raf-callback",
    "layout.frame.invoke-start",
    "layout.frame.invoke-return",
    "layout.frame.invoke-error",
    "layout.gesture-start.invoke-start",
    "layout.gesture-start.invoke-return",
    "layout.gesture-start.invoke-error",
    "layout.gesture-stop.invoke-start",
    "layout.gesture-stop.invoke-return",
    "layout.gesture-stop.invoke-error",
    "layout.frame-event-received",
    "layout.gesture-event-received",
    "layout.begin-preview.invoke-start",
    "layout.begin-preview.invoke-return",
    "layout.begin-preview.invoke-error",
    "layout.end-preview.invoke-start",
    "layout.end-preview.invoke-return",
    "layout.end-preview.invoke-error",
    "layout.css-commit",
    "layout.native-css-commit",
    "layout.paint-raf",
    "layout.paint-opportunity",
    "layout.apply-native.invoke-start",
    "layout.apply-native.invoke-return",
    "layout.apply-native.invoke-error",
    "portrait.frame-event-received",
    "portrait.gesture-event-received",
    "portrait.begin-preview.invoke-start",
    "portrait.begin-preview.invoke-return",
    "portrait.begin-preview.invoke-error",
    "portrait.activate-hit-test.invoke-start",
    "portrait.activate-hit-test.invoke-return",
    "portrait.activate-hit-test.invoke-error",
    "portrait.css-commit",
    "portrait.paint-raf",
    "portrait.paint-opportunity",
    "pet-drag.pointerdown",
    "pet-drag.start-native.invoke-start",
    "pet-drag.start-native.invoke-return",
    "pet-drag.start-native.invoke-error",
];

impl FrontendTraceEntry {
    fn validate(&self, window_label: &str) -> Result<(), String> {
        let source_matches_window = matches!(
            (self.source.as_str(), window_label),
            ("main", "main") | ("settings", "settings")
        );
        if !source_matches_window
            || !ALLOWED_FRONTEND_STAGES.contains(&self.stage.as_str())
            || !valid_gesture_id(&self.gesture_id)
            || !valid_milliseconds(self.perf_ms, 0.0, 10_000_000_000.0)
            || !valid_milliseconds(self.epoch_ms, 1_000_000_000_000.0, 10_000_000_000_000.0)
            || !valid_optional_milliseconds(self.event_perf_ms, 0.0, 10_000_000_000.0)
            || !valid_optional_milliseconds(self.event_delay_ms, 0.0, 3_600_000.0)
            || !valid_optional_milliseconds(self.elapsed_ms, 0.0, 3_600_000.0)
        {
            return Err("INTERACTION_LATENCY_TRACE_INVALID".to_string());
        }
        Ok(())
    }
}

fn valid_milliseconds(value: f64, minimum: f64, maximum: f64) -> bool {
    value.is_finite() && (minimum..=maximum).contains(&value)
}

fn valid_optional_milliseconds(value: Option<f64>, minimum: f64, maximum: f64) -> bool {
    value.is_none_or(|value| valid_milliseconds(value, minimum, maximum))
}

fn valid_gesture_id(value: &str) -> bool {
    [
        "settings-portrait-scale-",
        "settings-layout-control-panel-width-",
        "settings-layout-bubble-max-height-",
        "settings-layout-control-panel-vertical-offset-",
        "settings-layout-input-bar-offset-",
        "settings-layout-",
        "main-pet-drag-",
    ]
    .iter()
    .any(|prefix| {
        value.strip_prefix(prefix).is_some_and(|suffix| {
            !suffix.is_empty()
                && suffix.len() <= 20
                && suffix.bytes().all(|byte| byte.is_ascii_digit())
        })
    })
}

pub fn enabled() -> bool {
    cfg!(all(
        debug_assertions,
        feature = "interaction-latency-diagnostics"
    ))
}

#[derive(Clone)]
struct ActiveTrace {
    scope: &'static str,
    context: Option<InteractionTraceContext>,
    started: Instant,
}

thread_local! {
    static ACTIVE_TRACE: std::cell::RefCell<Option<ActiveTrace>> = const { std::cell::RefCell::new(None) };
}

struct ActiveTraceGuard(Option<ActiveTrace>);

impl Drop for ActiveTraceGuard {
    fn drop(&mut self) {
        let previous = self.0.take();
        ACTIVE_TRACE.with(|slot| slot.replace(previous));
    }
}

fn activate(scope: &'static str, context: Option<InteractionTraceContext>) -> ActiveTraceGuard {
    let active = ActiveTrace {
        scope,
        context,
        started: Instant::now(),
    };
    let previous = ACTIVE_TRACE.with(|slot| slot.replace(Some(active)));
    ActiveTraceGuard(previous)
}

pub fn command<T>(
    scope: &'static str,
    context: Option<InteractionTraceContext>,
    operation: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    if !enabled() {
        return operation();
    }
    if let Some(context) = context.as_ref() {
        context.validate()?;
    }
    let started = Instant::now();
    let _guard = activate(scope, context);
    stage("command-enter");
    let result = operation();
    if let Err(error) = result.as_ref() {
        stage(command_error_category(error));
    }
    stage_elapsed(
        if result.is_ok() {
            "command-return"
        } else {
            "command-error"
        },
        started,
    );
    result
}

fn command_error_category(error: &str) -> &'static str {
    if error.contains("failed to install native pet borderless subclass") {
        "command-error-native-subclass"
    } else if error.contains("CHARACTER_PRESENTATION_NOT_READY") {
        "command-error-character-not-ready"
    } else if error.contains("CHARACTER_PRESENTATION_UNAVAILABLE") {
        "command-error-character-unavailable"
    } else if error.contains("LIFECYCLE_STATE_UNAVAILABLE") {
        "command-error-lifecycle-state"
    } else if error.contains("APPEARANCE_SESSION_STALE") {
        "command-error-appearance-session-stale"
    } else if error.contains("failed to apply native pet hit region") {
        "command-error-native-region"
    } else if error.contains("PET_SURFACE_COMMIT_FAILED") {
        "command-error-surface-commit"
    } else if error.contains("PET_DRAG_COMMIT_") {
        "command-error-drag-commit-dispatch"
    } else {
        "command-error-other"
    }
}

pub fn stage(stage_name: &'static str) {
    if !enabled() {
        return;
    }
    write_active_stage(stage_name, None);
}

pub fn stage_elapsed(stage_name: &'static str, started: Instant) {
    if !enabled() {
        return;
    }
    write_active_stage(stage_name, Some(started.elapsed().as_secs_f64() * 1_000.0));
}

pub fn lock<'a, T>(
    mutex: &'a Mutex<T>,
    wait_stage: &'static str,
    acquired_stage: &'static str,
) -> Result<MutexGuard<'a, T>, String> {
    let started = Instant::now();
    stage(wait_stage);
    let guard = mutex
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    stage_elapsed(acquired_stage, started);
    Ok(guard)
}

pub fn record_frontend(window_label: &str, entries: Vec<FrontendTraceEntry>) -> Result<(), String> {
    if !enabled() {
        return Ok(());
    }
    if entries.is_empty() || entries.len() > 128 {
        return Err("INTERACTION_LATENCY_TRACE_BATCH_INVALID".to_string());
    }
    for entry in entries {
        entry.validate(window_label)?;
        let line = serde_json::json!({
            "schemaVersion": 1,
            "source": entry.source,
            "scope": "frontend",
            "stage": entry.stage,
            "gestureId": entry.gesture_id,
            "revision": entry.revision,
            "perfMs": entry.perf_ms,
            "epochMs": entry.epoch_ms,
            "eventPerfMs": entry.event_perf_ms,
            "eventDelayMs": entry.event_delay_ms,
            "elapsedMs": entry.elapsed_ms,
            "receivedEpochMs": epoch_ms(),
            "receivedProcessMs": process_ms(),
        });
        write_json_line(&line);
    }
    Ok(())
}

pub fn initialize(runtime_log: RuntimeLogService) {
    if !enabled() {
        return;
    }
    let _ = runtime_log_service().set(runtime_log);
    let line = serde_json::json!({
        "schemaVersion": 1,
        "source": "rust",
        "scope": "diagnostics",
        "stage": "process-start",
        "gestureId": serde_json::Value::Null,
        "revision": serde_json::Value::Null,
        "epochMs": epoch_ms(),
        "processMs": process_ms(),
    });
    write_json_line(&line);
}

fn write_active_stage(stage_name: &'static str, elapsed_ms: Option<f64>) {
    ACTIVE_TRACE.with(|slot| {
        let borrowed = slot.borrow();
        let Some(active) = borrowed.as_ref() else {
            let line = serde_json::json!({
                "schemaVersion": 1,
                "source": "rust",
                "scope": "native",
                "stage": stage_name,
                "gestureId": serde_json::Value::Null,
                "revision": serde_json::Value::Null,
                "epochMs": epoch_ms(),
                "processMs": process_ms(),
                "elapsedMs": elapsed_ms,
            });
            write_json_line(&line);
            return;
        };
        let context = active.context.as_ref();
        let line = serde_json::json!({
            "schemaVersion": 1,
            "source": "rust",
            "scope": active.scope,
            "stage": stage_name,
            "gestureId": context.map(|context| context.gesture_id.as_str()),
            "revision": context.map(|context| context.revision),
            "clientPerfMs": context.map(|context| context.client_perf_ms),
            "clientEpochMs": context.map(|context| context.client_epoch_ms),
            "epochMs": epoch_ms(),
            "processMs": process_ms(),
            "commandElapsedMs": active.started.elapsed().as_secs_f64() * 1_000.0,
            "elapsedMs": elapsed_ms,
        });
        write_json_line(&line);
    });
}

#[cfg(all(debug_assertions, feature = "interaction-latency-diagnostics"))]
fn process_ms() -> f64 {
    static ORIGIN: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    ORIGIN.get_or_init(Instant::now).elapsed().as_secs_f64() * 1_000.0
}

#[cfg(not(all(debug_assertions, feature = "interaction-latency-diagnostics")))]
fn process_ms() -> f64 {
    0.0
}

fn epoch_ms() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64() * 1_000.0)
        .unwrap_or(0.0)
}

fn write_json_line(value: &serde_json::Value) {
    if !enabled() {
        return;
    }
    let Some(runtime_log) = runtime_log_service().get() else {
        return;
    };
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(
            Severity::Debug,
            "interaction.latency",
            "interaction.latency.stage",
            "Interaction latency stage",
        )
        .verbosity(Verbosity::Debug)
        .attributes(value.clone()),
    );
}

fn runtime_log_service() -> &'static std::sync::OnceLock<RuntimeLogService> {
    static SERVICE: std::sync::OnceLock<RuntimeLogService> = std::sync::OnceLock::new();
    &SERVICE
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostic_context_accepts_only_generated_non_user_identifiers() {
        let context = InteractionTraceContext {
            gesture_id: "settings-layout-control-panel-width-12".to_string(),
            revision: 3,
            client_perf_ms: 12.0,
            client_epoch_ms: 1_800_000_000_000.0,
        };
        assert!(context.validate().is_ok());
        assert!(!valid_gesture_id("settings-layout-user-text"));
        assert!(!valid_gesture_id("main-pet-drag-1/path"));
    }

    #[test]
    fn diagnostic_feature_never_enables_release_builds() {
        if !cfg!(debug_assertions) {
            assert!(!enabled());
        }
    }

    #[test]
    fn diagnostic_error_categories_are_fixed_and_never_copy_error_text() {
        assert_eq!(
            command_error_category(
                "PET_SURFACE_COMMIT_FAILED_PREVIOUS_RESTORED: failed to install native pet borderless subclass"
            ),
            "command-error-native-subclass"
        );
        assert_eq!(
            command_error_category("CHARACTER_PRESENTATION_NOT_READY"),
            "command-error-character-not-ready"
        );
        assert_eq!(
            command_error_category("arbitrary user-bearing diagnostic text"),
            "command-error-other"
        );
    }
}
