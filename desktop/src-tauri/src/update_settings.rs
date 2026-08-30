use std::{
    ffi::OsString,
    path::Path,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use serde::Serialize;
use serde_json::{json, Map, Value};
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

use crate::{
    chat_bridge::ChatEventPublication,
    runtime_log::{RuntimeLogEvent, RuntimeLogService, Severity},
    ui_config::UiConfigRepository,
};

pub const REPOSITORY_URL: &str = "https://github.com/Rvosy/Sakura";
pub const WEBSITE_URL: &str = "https://sakura.cialloo.cn/";
pub const CHANGELOG_URL: &str = "https://github.com/Rvosy/Sakura/blob/main/docs/CHANGELOG.md";
pub const SPONSOR_URL: &str = "https://ifdian.net/a/Rvosy";
pub const UPDATE_PREFERENCES_CHANGED_EVENT: &str = "sakura://update-preferences-changed";
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(10);
const UPDATE_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30 * 60);
const UPDATE_NOTES_LIMIT: usize = 4000;

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AboutSnapshot {
    schema_version: u32,
    version: String,
    repository_url: &'static str,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateSnapshot {
    schema_version: u32,
    current_version: String,
    mode: &'static str,
    available: bool,
    version: Option<String>,
    notes: Option<String>,
    pub_date: Option<String>,
    download_url: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdatePreferencesSnapshot {
    schema_version: u32,
    auto_check_enabled: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupUpdateSnapshot {
    schema_version: u32,
    status: String,
    version: Option<String>,
}

#[derive(Clone, Debug)]
struct UpdatePreferences {
    auto_check_enabled: bool,
    last_announced_version: Option<String>,
    last_announced_local_date: Option<String>,
}

#[derive(Clone, Debug)]
struct UpdateCandidate {
    snapshot: UpdateSnapshot,
}

impl UpdateCandidate {
    fn version(&self) -> &str {
        self.snapshot.version.as_deref().unwrap_or_default()
    }

    fn event(&self) -> Value {
        json!({
            "type": "update_available",
            "payload": {
                "currentVersion": self.snapshot.current_version,
                "version": self.snapshot.version,
                "notes": self.snapshot.notes,
                "pubDate": self.snapshot.pub_date,
                "mode": self.snapshot.mode,
            }
        })
    }
}

#[derive(Default)]
struct UpdateCoordinatorState {
    check_attempted: bool,
    checked_snapshot: Option<UpdateSnapshot>,
    candidate: Option<UpdateCandidate>,
    status: String,
}

#[derive(Clone)]
pub struct UpdateCoordinator {
    repository: UiConfigRepository,
    state: Arc<Mutex<UpdateCoordinatorState>>,
}

impl UpdateCoordinator {
    pub fn new(repository: UiConfigRepository) -> Self {
        Self {
            repository,
            state: Arc::new(Mutex::new(UpdateCoordinatorState::default())),
        }
    }

    pub fn preferences(&self) -> Result<UpdatePreferencesSnapshot, String> {
        let preferences = load_preferences(&self.repository)?;
        Ok(UpdatePreferencesSnapshot {
            schema_version: 1,
            auto_check_enabled: preferences.auto_check_enabled,
        })
    }

    pub fn checked_snapshot(&self) -> Result<Option<UpdateSnapshot>, String> {
        self.state
            .lock()
            .map_err(|_| "UPDATE_COORDINATOR_UNAVAILABLE".to_string())
            .map(|state| state.checked_snapshot.clone())
    }

    pub fn set_auto_check_enabled(
        &self,
        enabled: bool,
    ) -> Result<UpdatePreferencesSnapshot, String> {
        save_preferences(&self.repository, |preferences| {
            preferences.auto_check_enabled = enabled;
        })?;
        if !enabled {
            self.state
                .lock()
                .map_err(|_| "UPDATE_COORDINATOR_UNAVAILABLE".to_string())?
                .candidate = None;
        }
        self.preferences()
    }

    pub async fn startup_check(
        &self,
        app: &AppHandle,
        executable_directory: &Path,
        runtime_log: &RuntimeLogService,
    ) -> StartupUpdateSnapshot {
        let mut preferences = match load_preferences(&self.repository) {
            Ok(preferences) => preferences,
            Err(_) => return startup_snapshot("unavailable", None),
        };
        if !preferences.auto_check_enabled {
            if let Ok(mut state) = self.state.lock() {
                state.candidate = None;
                state.status = "disabled".to_string();
            }
            return startup_snapshot("disabled", None);
        }

        let cached = self.state.lock().ok().and_then(|state| {
            state
                .check_attempted
                .then(|| state.checked_snapshot.clone())
        });
        let checked = match cached {
            Some(snapshot) => snapshot,
            None => {
                if let Ok(mut state) = self.state.lock() {
                    if state.check_attempted {
                        return startup_snapshot(
                            if state.status.is_empty() {
                                "unavailable"
                            } else {
                                &state.status
                            },
                            state.candidate.as_ref().map(|value| value.version()),
                        );
                    }
                    state.check_attempted = true;
                }
                match check(app, executable_directory, runtime_log, "startup").await {
                    Ok(snapshot) => {
                        if let Ok(mut state) = self.state.lock() {
                            state.checked_snapshot = Some(snapshot.clone());
                        }
                        Some(snapshot)
                    }
                    Err(_) => {
                        if let Ok(mut state) = self.state.lock() {
                            state.status = "unavailable".to_string();
                        }
                        return startup_snapshot("unavailable", None);
                    }
                }
            }
        };
        let Some(snapshot) = checked else {
            return startup_snapshot("unavailable", None);
        };
        preferences = match load_preferences(&self.repository) {
            Ok(preferences) => preferences,
            Err(_) => return startup_snapshot("unavailable", None),
        };
        if !preferences.auto_check_enabled {
            if let Ok(mut state) = self.state.lock() {
                state.candidate = None;
                state.status = "disabled".to_string();
            }
            return startup_snapshot("disabled", None);
        }
        if !snapshot.available {
            if let Ok(mut state) = self.state.lock() {
                state.candidate = None;
                state.status = "up_to_date".to_string();
            }
            return startup_snapshot("up_to_date", None);
        }
        let version = snapshot.version.clone().unwrap_or_default();
        let today = local_date();
        if !announcement_due(&preferences, &version, &today) {
            if let Ok(mut state) = self.state.lock() {
                state.candidate = None;
                state.status = "already_announced".to_string();
            }
            return startup_snapshot("already_announced", Some(&version));
        }
        if let Ok(mut state) = self.state.lock() {
            state.candidate = Some(UpdateCandidate {
                snapshot: snapshot.clone(),
            });
            state.status = "pending".to_string();
        }
        startup_snapshot("pending", Some(&version))
    }

    pub fn pending_event(&self) -> Result<(Value, String), String> {
        let state = self
            .state
            .lock()
            .map_err(|_| "UPDATE_COORDINATOR_UNAVAILABLE".to_string())?;
        let candidate = state
            .candidate
            .as_ref()
            .ok_or_else(|| "UPDATE_ANNOUNCEMENT_NOT_PENDING".to_string())?;
        Ok((candidate.event(), candidate.version().to_string()))
    }

    pub fn observe_chat_event(&self, event: &ChatEventPublication) -> Result<(), String> {
        let Some(version) = event.update_version.as_deref() else {
            return Ok(());
        };
        if event.event_type != "chat.completed" {
            return Ok(());
        }
        save_preferences(&self.repository, |preferences| {
            preferences.last_announced_version = Some(version.to_string());
            preferences.last_announced_local_date = Some(local_date());
        })?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "UPDATE_COORDINATOR_UNAVAILABLE".to_string())?;
        if state
            .candidate
            .as_ref()
            .is_some_and(|candidate| candidate.version() == version)
        {
            state.candidate = None;
            state.status = "announced".to_string();
        }
        Ok(())
    }
}

pub fn about_snapshot() -> AboutSnapshot {
    AboutSnapshot {
        schema_version: 1,
        version: env!("CARGO_PKG_VERSION").to_string(),
        repository_url: REPOSITORY_URL,
    }
}

pub fn is_portable(executable_directory: &Path) -> bool {
    portable_mode(
        executable_directory.join("portable.flag").is_file(),
        cfg!(target_os = "windows"),
    )
}

fn portable_mode(has_marker: bool, is_windows: bool) -> bool {
    has_marker && is_windows
}

fn portable_download_url(raw: &Value) -> Result<String, String> {
    let value = raw
        .get("portable")
        .and_then(|value| value.get("windows-x86_64"))
        .and_then(|value| value.get("url"))
        .and_then(Value::as_str)
        .filter(|value| value.starts_with("https://") && !value.chars().any(char::is_control))
        .ok_or_else(|| "PORTABLE_UPDATE_URL_MISSING".to_string())?;
    Ok(value.to_string())
}

pub async fn check(
    app: &AppHandle,
    executable_directory: &Path,
    runtime_log: &RuntimeLogService,
    trigger: &'static str,
) -> Result<UpdateSnapshot, String> {
    let started = Instant::now();
    let portable = is_portable(executable_directory);
    let mode = update_mode(portable);
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.check.started",
        "Updater check started",
        check_started_attributes(trigger, mode),
    );
    let updater = app
        .updater_builder()
        .timeout(UPDATE_CHECK_TIMEOUT)
        .build()
        .map_err(|error| {
            log_updater_failure(
                runtime_log,
                "updater.check.failed",
                "configuration",
                trigger,
                mode,
                "UPDATE_CONFIGURATION_INVALID",
                &error.to_string(),
                started,
            );
            "UPDATE_CONFIGURATION_INVALID".to_string()
        })?;
    let update = updater.check().await.map_err(|error| {
        log_updater_failure(
            runtime_log,
            "updater.check.failed",
            "check",
            trigger,
            mode,
            "UPDATE_CHECK_FAILED",
            &error.to_string(),
            started,
        );
        "UPDATE_CHECK_FAILED".to_string()
    })?;
    let Some(update) = update else {
        let snapshot = no_update_snapshot(portable);
        log_check_completed(runtime_log, trigger, &snapshot, "up_to_date", started);
        return Ok(snapshot);
    };
    if !stable_release_version(&update.version) {
        let snapshot = no_update_snapshot(portable);
        log_check_completed(
            runtime_log,
            trigger,
            &snapshot,
            "prerelease_ignored",
            started,
        );
        return Ok(snapshot);
    }
    let download_url = if portable {
        Some(portable_download_url(&update.raw_json).map_err(|error| {
            log_updater_failure(
                runtime_log,
                "updater.check.failed",
                "manifest",
                trigger,
                mode,
                &error,
                &error,
                started,
            );
            error
        })?)
    } else {
        None
    };
    let snapshot = UpdateSnapshot {
        schema_version: 1,
        current_version: update.current_version,
        mode,
        available: true,
        version: Some(update.version),
        notes: bounded_optional_text(update.body, UPDATE_NOTES_LIMIT),
        pub_date: update.date.and_then(|value| value.format(&Rfc3339).ok()),
        download_url,
    };
    log_check_completed(runtime_log, trigger, &snapshot, "available", started);
    Ok(snapshot)
}

fn no_update_snapshot(portable: bool) -> UpdateSnapshot {
    UpdateSnapshot {
        schema_version: 1,
        current_version: env!("CARGO_PKG_VERSION").to_string(),
        mode: update_mode(portable),
        available: false,
        version: None,
        notes: None,
        pub_date: None,
        download_url: None,
    }
}

fn stable_release_version(version: &str) -> bool {
    !version.split('+').next().unwrap_or(version).contains('-')
}

fn bounded_optional_text(value: Option<String>, limit: usize) -> Option<String> {
    let value = value?;
    let clean = value
        .chars()
        .filter(|character| !matches!(character, '\0' | '\r'))
        .take(limit)
        .collect::<String>();
    (!clean.trim().is_empty()).then_some(clean)
}

fn startup_snapshot(status: &str, version: Option<&str>) -> StartupUpdateSnapshot {
    StartupUpdateSnapshot {
        schema_version: 1,
        status: status.to_string(),
        version: version.map(ToOwned::to_owned),
    }
}

fn local_date() -> String {
    let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    format!(
        "{:04}-{:02}-{:02}",
        now.year(),
        u8::from(now.month()),
        now.day()
    )
}

fn announcement_due(preferences: &UpdatePreferences, version: &str, today: &str) -> bool {
    preferences.last_announced_version.as_deref() != Some(version)
        || preferences.last_announced_local_date.as_deref() != Some(today)
}

fn load_preferences(repository: &UiConfigRepository) -> Result<UpdatePreferences, String> {
    let document = repository.load("UPDATE")?;
    preferences_from_document(&document)
}

fn preferences_from_document(document: &Value) -> Result<UpdatePreferences, String> {
    let settings = validated_settings(document)?;
    let Some(update) = settings.get("update") else {
        return Ok(UpdatePreferences {
            auto_check_enabled: true,
            last_announced_version: None,
            last_announced_local_date: None,
        });
    };
    let update = update
        .as_object()
        .ok_or_else(|| "UPDATE_SETTINGS_INVALID".to_string())?;
    let auto_check_enabled = match update.get("auto_check_enabled") {
        None => true,
        Some(Value::Bool(value)) => *value,
        Some(_) => return Err("UPDATE_SETTINGS_INVALID".to_string()),
    };
    Ok(UpdatePreferences {
        auto_check_enabled,
        last_announced_version: optional_bounded_setting(update, "last_announced_version", 64)?,
        last_announced_local_date: optional_bounded_setting(
            update,
            "last_announced_local_date",
            10,
        )?,
    })
}

fn save_preferences(
    repository: &UiConfigRepository,
    mutate: impl FnOnce(&mut UpdatePreferences),
) -> Result<(), String> {
    repository.update("UPDATE", |document| {
        let mut preferences = preferences_from_document(document)?;
        mutate(&mut preferences);
        let settings = validated_settings_mut(document)?;
        settings.insert(
            "update".to_string(),
            json!({
                "auto_check_enabled": preferences.auto_check_enabled,
                "last_announced_version": preferences.last_announced_version,
                "last_announced_local_date": preferences.last_announced_local_date,
            }),
        );
        Ok(())
    })
}

fn validated_settings(document: &Value) -> Result<&Map<String, Value>, String> {
    if document.get("schema_version").and_then(Value::as_u64) != Some(1)
        || document.get("domain").and_then(Value::as_str) != Some("ui")
    {
        return Err("UPDATE_SETTINGS_INVALID".to_string());
    }
    document
        .get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "UPDATE_SETTINGS_INVALID".to_string())
}

fn validated_settings_mut(document: &mut Value) -> Result<&mut Map<String, Value>, String> {
    if document.get("schema_version").and_then(Value::as_u64) != Some(1)
        || document.get("domain").and_then(Value::as_str) != Some("ui")
    {
        return Err("UPDATE_SETTINGS_INVALID".to_string());
    }
    document
        .get_mut("settings")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| "UPDATE_SETTINGS_INVALID".to_string())
}

fn optional_bounded_setting(
    mapping: &Map<String, Value>,
    key: &str,
    limit: usize,
) -> Result<Option<String>, String> {
    match mapping.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value))
            if !value.is_empty()
                && value.len() <= limit
                && !value.chars().any(char::is_control) =>
        {
            Ok(Some(value.clone()))
        }
        Some(_) => Err("UPDATE_SETTINGS_INVALID".to_string()),
    }
}

pub async fn install(
    app: &AppHandle,
    executable_directory: &Path,
    runtime_log: &RuntimeLogService,
    prepare_windows_exit: impl FnOnce() -> Result<(), String> + Send + 'static,
) -> Result<(), String> {
    let operation_started = Instant::now();
    if is_portable(executable_directory) {
        log_updater_failure(
            runtime_log,
            "updater.install.failed",
            "mode",
            "install",
            "portable",
            "PORTABLE_UPDATE_MANUAL_REQUIRED",
            "Portable updates require a manual download.",
            operation_started,
        );
        return Err("PORTABLE_UPDATE_MANUAL_REQUIRED".to_string());
    }
    let mode = "installed";
    let check_started = Instant::now();
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.check.started",
        "Updater check started",
        check_started_attributes("install", mode),
    );
    let updater = app
        .updater_builder()
        .timeout(UPDATE_CHECK_TIMEOUT)
        .build()
        .map_err(|error| {
            log_updater_failure(
                runtime_log,
                "updater.install.failed",
                "configuration",
                "install",
                mode,
                "UPDATE_CONFIGURATION_INVALID",
                &error.to_string(),
                operation_started,
            );
            "UPDATE_CONFIGURATION_INVALID".to_string()
        })?;
    let mut update = updater
        .check()
        .await
        .map_err(|error| {
            log_updater_failure(
                runtime_log,
                "updater.install.failed",
                "check",
                "install",
                mode,
                "UPDATE_CHECK_FAILED",
                &error.to_string(),
                operation_started,
            );
            "UPDATE_CHECK_FAILED".to_string()
        })?
        .ok_or_else(|| {
            log_updater_failure(
                runtime_log,
                "updater.install.failed",
                "check",
                "install",
                mode,
                "UPDATE_NOT_AVAILABLE",
                "No update is available.",
                operation_started,
            );
            "UPDATE_NOT_AVAILABLE".to_string()
        })?;
    if !stable_release_version(&update.version) {
        log_updater_failure(
            runtime_log,
            "updater.install.failed",
            "check",
            "install",
            mode,
            "UPDATE_NOT_AVAILABLE",
            "Only stable updater releases can be installed.",
            operation_started,
        );
        return Err("UPDATE_NOT_AVAILABLE".to_string());
    }
    // UpdaterBuilder propagates its request timeout to the returned Update.
    // Keep manifest checks bounded to ten seconds, but allow large signed
    // installers to finish on slower connections.
    update.timeout = Some(UPDATE_DOWNLOAD_TIMEOUT);
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.check.completed",
        "Updater check completed",
        json!({
            "stage": "check",
            "trigger": "install",
            "mode": mode,
            "status": "available",
            "current_version": update.current_version,
            "version": update.version,
            "elapsed_ms": elapsed_ms(check_started),
        }),
    );
    let version = update.version.clone();
    let download_started = Instant::now();
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.download.started",
        "Updater download started",
        json!({
            "stage": "download",
            "trigger": "install",
            "mode": mode,
            "version": version,
        }),
    );
    let bytes = update.download(|_, _| {}, || {}).await.map_err(|error| {
        let diagnostic = error.to_string();
        let reason_code = classify_updater_error(&diagnostic, "download");
        log_updater_failure_with_reason(
            runtime_log,
            if reason_code == "SIGNATURE" {
                "updater.signature.failed"
            } else {
                "updater.download.failed"
            },
            "download",
            "install",
            mode,
            "UPDATE_DOWNLOAD_FAILED",
            reason_code,
            &diagnostic,
            download_started,
        );
        "UPDATE_DOWNLOAD_FAILED".to_string()
    })?;
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.download.completed",
        "Updater download completed",
        json!({
            "stage": "download",
            "trigger": "install",
            "mode": mode,
            "version": version,
            "bytes": bytes.len(),
            "elapsed_ms": elapsed_ms(download_started),
        }),
    );
    let install_started = Instant::now();
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.install.started",
        "Updater install started",
        json!({
            "stage": "install",
            "trigger": "install",
            "mode": mode,
            "version": version,
            "bytes": bytes.len(),
        }),
    );
    prepare_installed_update_exit(cfg!(windows), prepare_windows_exit).map_err(|error| {
        log_updater_failure(
            runtime_log,
            "updater.install.failed",
            "prepare_exit",
            "install",
            mode,
            "UPDATE_INSTALL_PREPARE_FAILED",
            &error,
            install_started,
        );
        error
    })?;
    update.install(bytes).map_err(|error| {
        log_updater_failure(
            runtime_log,
            "updater.install.failed",
            "install",
            "install",
            mode,
            "UPDATE_INSTALL_FAILED",
            &error.to_string(),
            install_started,
        );
        "UPDATE_INSTALL_FAILED".to_string()
    })?;
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.install.completed",
        "Updater install completed",
        json!({
            "stage": "install",
            "trigger": "install",
            "mode": mode,
            "version": version,
            "status": "completed",
            "elapsed_ms": elapsed_ms(install_started),
        }),
    );
    Ok(())
}

fn update_mode(portable: bool) -> &'static str {
    if portable {
        "portable"
    } else {
        "installed"
    }
}

fn check_started_attributes(trigger: &'static str, mode: &'static str) -> Value {
    let mut attributes = proxy_environment_snapshot_with(|name| std::env::var_os(name));
    attributes.insert("stage".to_string(), json!("check"));
    attributes.insert("trigger".to_string(), json!(trigger));
    attributes.insert("mode".to_string(), json!(mode));
    attributes.insert(
        "current_version".to_string(),
        json!(env!("CARGO_PKG_VERSION")),
    );
    Value::Object(attributes)
}

fn proxy_environment_snapshot_with(read: impl Fn(&str) -> Option<OsString>) -> Map<String, Value> {
    let configured = |upper: &str, lower: &str| {
        read(upper).is_some_and(|value| !value.is_empty())
            || read(lower).is_some_and(|value| !value.is_empty())
    };
    Map::from_iter([
        ("proxy_mode".to_string(), json!("system_and_environment")),
        (
            "proxy_http_configured".to_string(),
            json!(configured("HTTP_PROXY", "http_proxy")),
        ),
        (
            "proxy_https_configured".to_string(),
            json!(configured("HTTPS_PROXY", "https_proxy")),
        ),
        (
            "proxy_all_configured".to_string(),
            json!(configured("ALL_PROXY", "all_proxy")),
        ),
        (
            "proxy_no_proxy_configured".to_string(),
            json!(configured("NO_PROXY", "no_proxy")),
        ),
    ])
}

fn log_check_completed(
    runtime_log: &RuntimeLogService,
    trigger: &'static str,
    snapshot: &UpdateSnapshot,
    status: &'static str,
    started: Instant,
) {
    submit_updater_event(
        runtime_log,
        Severity::Info,
        "updater.check.completed",
        "Updater check completed",
        json!({
            "stage": "check",
            "trigger": trigger,
            "mode": snapshot.mode,
            "status": status,
            "current_version": snapshot.current_version,
            "version": snapshot.version,
            "elapsed_ms": elapsed_ms(started),
        }),
    );
}

fn log_updater_failure(
    runtime_log: &RuntimeLogService,
    event: &'static str,
    stage: &'static str,
    trigger: &'static str,
    mode: &'static str,
    code: &str,
    diagnostic: &str,
    started: Instant,
) {
    log_updater_failure_with_reason(
        runtime_log,
        event,
        stage,
        trigger,
        mode,
        code,
        classify_updater_error(diagnostic, stage),
        diagnostic,
        started,
    );
}

#[allow(clippy::too_many_arguments)]
fn log_updater_failure_with_reason(
    runtime_log: &RuntimeLogService,
    event: &'static str,
    stage: &'static str,
    trigger: &'static str,
    mode: &'static str,
    code: &str,
    reason_code: &'static str,
    diagnostic: &str,
    started: Instant,
) {
    submit_updater_event(
        runtime_log,
        Severity::Error,
        event,
        "Updater operation failed",
        json!({
            "stage": stage,
            "trigger": trigger,
            "mode": mode,
            "code": code,
            "reason_code": reason_code,
            "error_type": "tauri_updater",
            "diagnostic": sanitize_updater_diagnostic(diagnostic),
            "elapsed_ms": elapsed_ms(started),
        }),
    );
}

fn submit_updater_event(
    runtime_log: &RuntimeLogService,
    severity: Severity,
    event: &'static str,
    message: &'static str,
    attributes: Value,
) {
    let _ = runtime_log
        .submit(RuntimeLogEvent::rust(severity, "updater", event, message).attributes(attributes));
}

fn classify_updater_error(diagnostic: &str, stage: &str) -> &'static str {
    let normalized = diagnostic.to_ascii_lowercase();
    if normalized.contains("timed out") || normalized.contains("timeout") {
        "TIMEOUT"
    } else if normalized.contains("signature")
        || normalized.contains("minisign")
        || normalized.contains("base64")
    {
        "SIGNATURE"
    } else if normalized.contains("status code") || normalized.contains("http status") {
        "HTTP"
    } else if normalized.contains("platform")
        || normalized.contains("architecture")
        || normalized.contains("unsupported os")
    {
        "TARGET"
    } else if normalized.contains("json")
        || normalized.contains("deserialize")
        || normalized.contains("release not found")
    {
        "MANIFEST"
    } else if normalized.contains("dns")
        || normalized.contains("connect")
        || normalized.contains("request")
        || normalized.contains("network")
        || normalized.contains("tcp")
        || normalized.contains("tls")
    {
        "NETWORK"
    } else {
        match stage {
            "configuration" => "CONFIGURATION",
            "manifest" => "MANIFEST",
            "download" => "DOWNLOAD_OR_SIGNATURE",
            "prepare_exit" => "SHUTDOWN",
            "install" => "INSTALL",
            "mode" => "MODE",
            _ => "UNKNOWN",
        }
    }
}

fn sanitize_updater_diagnostic(diagnostic: &str) -> String {
    let clean = diagnostic
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect::<String>();
    let mut redacted = String::with_capacity(clean.len().min(320));
    let mut remaining = clean.as_str();
    while let Some(index) = remaining.find("://") {
        let scheme_start = remaining[..index]
            .rfind(|character: char| {
                character.is_whitespace() || matches!(character, '(' | '[' | '{' | '\'' | '"')
            })
            .map_or(0, |value| value + 1);
        redacted.push_str(&remaining[..scheme_start]);
        redacted.push_str("[url]");
        remaining = &remaining[index + 3..];
        let end = remaining
            .find(|character: char| {
                character.is_whitespace() || matches!(character, ')' | ']' | '}' | '\'' | '"')
            })
            .unwrap_or(remaining.len());
        remaining = &remaining[end..];
    }
    redacted.push_str(remaining);
    redacted.chars().take(320).collect()
}

fn elapsed_ms(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u64::MAX as u128) as u64
}

fn prepare_installed_update_exit(
    is_windows: bool,
    prepare: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    if is_windows {
        prepare()?;
    }
    Ok(())
}

pub fn open_portable_download(url: &str) -> Result<(), String> {
    if !url.starts_with("https://") || url.chars().any(char::is_control) {
        return Err("PORTABLE_UPDATE_URL_INVALID".to_string());
    }
    open_https_url(url, "PORTABLE_UPDATE_OPEN_FAILED")
}

pub fn open_repository() -> Result<(), String> {
    open_https_url(REPOSITORY_URL, "ABOUT_REPOSITORY_OPEN_FAILED")
}

pub fn open_website() -> Result<(), String> {
    open_https_url(WEBSITE_URL, "ABOUT_WEBSITE_OPEN_FAILED")
}

pub fn open_changelog() -> Result<(), String> {
    open_https_url(CHANGELOG_URL, "ABOUT_CHANGELOG_OPEN_FAILED")
}

pub fn open_sponsor() -> Result<(), String> {
    open_https_url(SPONSOR_URL, "ABOUT_SPONSOR_OPEN_FAILED")
}

fn open_https_url(url: &str, error_code: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer.exe");
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = std::process::Command::new("xdg-open");
    command
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|_| error_code.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        path::PathBuf,
        sync::{
            atomic::{AtomicU64, Ordering},
            Arc, Barrier,
        },
        time::SystemTime,
    };

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "sakura-update-settings-{}-{nonce}-{}",
                std::process::id(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed),
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn config_path(&self) -> PathBuf {
            self.0.join("ui.json")
        }

        fn coordinator(&self) -> UpdateCoordinator {
            UpdateCoordinator::new(UiConfigRepository::new(self.config_path()))
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn available_snapshot(version: &str) -> UpdateSnapshot {
        UpdateSnapshot {
            schema_version: 1,
            current_version: "1.0.0".to_string(),
            mode: "installed",
            available: true,
            version: Some(version.to_string()),
            notes: Some("Release notes".to_string()),
            pub_date: Some("2026-08-29T08:00:00Z".to_string()),
            download_url: None,
        }
    }

    #[test]
    fn missing_update_preferences_default_to_enabled() {
        let fixture = Fixture::new();
        assert_eq!(
            fixture.coordinator().preferences().unwrap(),
            UpdatePreferencesSnapshot {
                schema_version: 1,
                auto_check_enabled: true,
            }
        );
    }

    #[test]
    fn checked_snapshot_exposes_the_startup_result_without_consuming_it() {
        let fixture = Fixture::new();
        let coordinator = fixture.coordinator();
        let snapshot = available_snapshot("1.2.0");
        coordinator.state.lock().unwrap().checked_snapshot = Some(snapshot.clone());

        assert_eq!(
            coordinator.checked_snapshot().unwrap(),
            Some(snapshot.clone())
        );
        assert_eq!(coordinator.checked_snapshot().unwrap(), Some(snapshot));
    }

    #[test]
    fn preference_write_is_atomic_and_preserves_other_ui_settings() {
        let fixture = Fixture::new();
        fs::write(
            fixture.config_path(),
            br#"{"schema_version":1,"domain":"ui","settings":{"future":{"kept":true}}}"#,
        )
        .unwrap();
        let coordinator = fixture.coordinator();

        assert!(
            !coordinator
                .set_auto_check_enabled(false)
                .unwrap()
                .auto_check_enabled
        );

        let document: Value =
            serde_json::from_slice(&fs::read(fixture.config_path()).unwrap()).unwrap();
        assert_eq!(document["settings"]["future"]["kept"], true);
        assert_eq!(
            document["settings"]["update"],
            json!({
                "auto_check_enabled": false,
                "last_announced_version": null,
                "last_announced_local_date": null,
            })
        );
        assert!(fs::read_dir(&fixture.0).unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));
    }

    #[test]
    fn preference_and_terminal_marker_writes_preserve_each_other_under_concurrency() {
        for _ in 0..32 {
            let fixture = Fixture::new();
            let coordinator = fixture.coordinator();
            let barrier = Arc::new(Barrier::new(3));
            let preference_coordinator = coordinator.clone();
            let preference_barrier = barrier.clone();
            let preference_write = std::thread::spawn(move || {
                preference_barrier.wait();
                preference_coordinator
                    .set_auto_check_enabled(false)
                    .unwrap();
            });
            let marker_coordinator = coordinator.clone();
            let marker_barrier = barrier.clone();
            let marker_write = std::thread::spawn(move || {
                marker_barrier.wait();
                marker_coordinator
                    .observe_chat_event(&ChatEventPublication {
                        event_type: "chat.completed".to_string(),
                        generation_id: "generation".to_string(),
                        generation_number: 1,
                        operation_id: "operation".to_string(),
                        reply: Some(json!({"segments": []})),
                        error: None,
                        update_version: Some("1.2.0".to_string()),
                    })
                    .unwrap();
            });
            barrier.wait();
            preference_write.join().unwrap();
            marker_write.join().unwrap();

            let preferences =
                load_preferences(&UiConfigRepository::new(fixture.config_path())).unwrap();
            assert!(!preferences.auto_check_enabled);
            assert_eq!(preferences.last_announced_version.as_deref(), Some("1.2.0"));
            assert_eq!(
                preferences.last_announced_local_date.as_deref(),
                Some(local_date().as_str())
            );
        }
    }

    #[test]
    fn daily_gate_is_scoped_to_both_version_and_local_date() {
        let preferences = UpdatePreferences {
            auto_check_enabled: true,
            last_announced_version: Some("1.2.0".to_string()),
            last_announced_local_date: Some("2026-08-29".to_string()),
        };
        assert!(!announcement_due(&preferences, "1.2.0", "2026-08-29"));
        assert!(announcement_due(&preferences, "1.3.0", "2026-08-29"));
        assert!(announcement_due(&preferences, "1.2.0", "2026-08-30"));
    }

    #[test]
    fn completed_update_terminal_marks_success_and_clears_the_candidate() {
        let fixture = Fixture::new();
        let coordinator = fixture.coordinator();
        coordinator.state.lock().unwrap().candidate = Some(UpdateCandidate {
            snapshot: available_snapshot("1.2.0"),
        });
        let event = ChatEventPublication {
            event_type: "chat.completed".to_string(),
            generation_id: "generation".to_string(),
            generation_number: 1,
            operation_id: "operation".to_string(),
            reply: Some(json!({"segments": []})),
            error: None,
            update_version: Some("1.2.0".to_string()),
        };

        coordinator.observe_chat_event(&event).unwrap();

        let preferences =
            load_preferences(&UiConfigRepository::new(fixture.config_path())).unwrap();
        assert_eq!(preferences.last_announced_version.as_deref(), Some("1.2.0"));
        assert_eq!(
            preferences.last_announced_local_date.as_deref(),
            Some(local_date().as_str())
        );
        let state = coordinator.state.lock().unwrap();
        assert!(state.candidate.is_none());
        assert_eq!(state.status, "announced");
    }

    #[test]
    fn failed_or_unrelated_terminals_never_write_the_daily_marker() {
        let fixture = Fixture::new();
        let coordinator = fixture.coordinator();
        for (event_type, update_version) in [
            ("chat.failed", Some("1.2.0".to_string())),
            ("chat.completed", None),
        ] {
            coordinator
                .observe_chat_event(&ChatEventPublication {
                    event_type: event_type.to_string(),
                    generation_id: "generation".to_string(),
                    generation_number: 1,
                    operation_id: "operation".to_string(),
                    reply: None,
                    error: None,
                    update_version,
                })
                .unwrap();
        }
        assert!(!fixture.config_path().exists());
    }

    #[test]
    fn candidate_event_contains_only_bounded_typed_updater_facts() {
        let mut snapshot = available_snapshot("1.2.0");
        snapshot.notes = bounded_optional_text(Some(format!("{}tail", "更".repeat(4000))), 4000);
        let candidate = UpdateCandidate { snapshot };
        let event = candidate.event();
        assert_eq!(event["type"], "update_available");
        assert_eq!(event["payload"]["version"], "1.2.0");
        assert_eq!(event["payload"]["pubDate"], "2026-08-29T08:00:00Z");
        assert_eq!(
            event["payload"]["notes"].as_str().unwrap().chars().count(),
            4000
        );
        assert_eq!(event["payload"].as_object().unwrap().len(), 5);
    }

    #[test]
    fn portable_manifest_url_is_explicit_and_https_only() {
        let raw = serde_json::json!({
            "portable": {"windows-x86_64": {"url": "https://example.test/Sakura.zip"}}
        });
        assert_eq!(
            portable_download_url(&raw).unwrap(),
            "https://example.test/Sakura.zip"
        );
        assert_eq!(
            portable_download_url(&serde_json::json!({
                "portable": {"windows-x86_64": {"url": "http://example.test/Sakura.zip"}}
            })),
            Err("PORTABLE_UPDATE_URL_MISSING".to_string())
        );
    }

    #[test]
    fn portable_flag_is_a_windows_only_contract() {
        assert!(portable_mode(true, true));
        assert!(!portable_mode(false, true));
        assert!(!portable_mode(true, false));
    }

    #[test]
    fn stable_channel_rejects_prerelease_versions() {
        assert!(stable_release_version("1.2.0"));
        assert!(stable_release_version("1.2.0+build.7"));
        assert!(!stable_release_version("1.2.0-rc.1"));
        assert!(!stable_release_version("1.2.0-beta.2+build.7"));
    }

    #[test]
    fn updater_download_timeout_is_not_the_manifest_check_timeout() {
        assert_eq!(UPDATE_CHECK_TIMEOUT, Duration::from_secs(10));
        assert_eq!(UPDATE_DOWNLOAD_TIMEOUT, Duration::from_secs(30 * 60));
        assert!(UPDATE_DOWNLOAD_TIMEOUT > UPDATE_CHECK_TIMEOUT);
    }

    #[test]
    fn updater_proxy_snapshot_records_presence_without_values() {
        let environment = std::collections::HashMap::from([
            (
                "HTTPS_PROXY",
                OsString::from("http://user:private@example.test:8080"),
            ),
            ("NO_PROXY", OsString::from("localhost")),
        ]);
        let snapshot = proxy_environment_snapshot_with(|name| environment.get(name).cloned());

        assert_eq!(snapshot["proxy_mode"], "system_and_environment");
        assert_eq!(snapshot["proxy_http_configured"], false);
        assert_eq!(snapshot["proxy_https_configured"], true);
        assert_eq!(snapshot["proxy_all_configured"], false);
        assert_eq!(snapshot["proxy_no_proxy_configured"], true);
        let encoded = serde_json::to_string(&snapshot).unwrap();
        assert!(!encoded.contains("private"));
        assert!(!encoded.contains("example.test"));
    }

    #[test]
    fn updater_diagnostics_are_bounded_and_redact_urls() {
        let diagnostic = format!(
            "request failed for https://user:private@example.test/file?token=secret\r\n{}",
            "x".repeat(500)
        );
        let sanitized = sanitize_updater_diagnostic(&diagnostic);

        assert!(sanitized.contains("request failed for [url]"));
        assert!(!sanitized.contains("private"));
        assert!(!sanitized.contains("token"));
        assert!(!sanitized.contains("://"));
        assert!(!sanitized.chars().any(char::is_control));
        assert_eq!(sanitized.chars().count(), 320);
    }

    #[test]
    fn updater_errors_keep_actionable_reason_codes() {
        assert_eq!(
            classify_updater_error("operation timed out", "check"),
            "TIMEOUT"
        );
        assert_eq!(
            classify_updater_error("error sending request", "check"),
            "NETWORK"
        );
        assert_eq!(
            classify_updater_error("signature verification failed", "download"),
            "SIGNATURE"
        );
        assert_eq!(classify_updater_error("unknown", "install"), "INSTALL");
    }

    #[test]
    fn installed_update_prepares_windows_exit_only_on_windows() {
        let called = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let callback_called = called.clone();
        prepare_installed_update_exit(true, move || {
            callback_called.store(true, Ordering::Relaxed);
            Ok(())
        })
        .unwrap();
        assert!(called.load(Ordering::Relaxed));

        prepare_installed_update_exit(false, || Err("must not run".to_string())).unwrap();
        assert_eq!(
            prepare_installed_update_exit(true, || Err("shutdown failed".to_string())),
            Err("shutdown failed".to_string())
        );
    }

    #[test]
    fn about_snapshot_and_product_links_are_fixed() {
        assert_eq!(
            about_snapshot(),
            AboutSnapshot {
                schema_version: 1,
                version: env!("CARGO_PKG_VERSION").to_string(),
                repository_url: "https://github.com/Rvosy/Sakura",
            }
        );
        assert_eq!(WEBSITE_URL, "https://sakura.cialloo.cn/");
        assert_eq!(
            CHANGELOG_URL,
            "https://github.com/Rvosy/Sakura/blob/main/docs/CHANGELOG.md"
        );
        assert_eq!(SPONSOR_URL, "https://ifdian.net/a/Rvosy");
    }
}
