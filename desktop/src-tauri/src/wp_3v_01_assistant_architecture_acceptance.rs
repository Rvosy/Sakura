//! Debug-only composed acceptance driver for the real WP-3V-01 Assistant slice.

use std::{
    fs,
    path::{Path, PathBuf},
    sync::mpsc::{self, Receiver, RecvTimeoutError},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde_json::{json, Value};
use tauri::{AppHandle, Listener, Manager};

use crate::{
    chat_bridge::{ChatBridge, CHAT_EVENT},
    shell_lifecycle::ShellLifecycleHandle,
};

pub const DIRECTORY_ENV: &str = "SAKURA_WP_3V_01_ACCEPTANCE_DIRECTORY";
pub const MODE_ENV: &str = "SAKURA_WP_3V_01_ACCEPTANCE_MODE";
const SANITIZED_MARKER: &str = ".sakura-wp-3v-01-sanitized";
const APP_ROOT: &str = "app-root";
const COMPLETE_MESSAGE: &str = "[WP-3V-01-COMPLETE]";
const CANCEL_MESSAGE: &str = "[WP-3V-01-CANCEL]";
const RECOVERY_MESSAGE: &str = "[WP-3V-01-RECOVERY]";
const SHUTDOWN_MESSAGE: &str = "[WP-3V-01-SHUTDOWN]";
const COMPLETE_REPLY: &str = "[WP-3V-01-REPLY-1]";
const RECOVERY_REPLY: &str = "[WP-3V-01-REPLY-3]";

#[derive(Clone, Debug)]
pub struct AcceptanceRequest {
    pub directory: PathBuf,
    pub app_root: PathBuf,
}

pub fn request_from_environment() -> Result<Option<AcceptanceRequest>, String> {
    let directory = std::env::var_os(DIRECTORY_ENV);
    let mode = std::env::var_os(MODE_ENV);
    match (directory, mode) {
        (None, None) => Ok(None),
        (Some(directory), Some(mode)) if mode == "vertical" => {
            let request = validate_request(PathBuf::from(directory))?;
            write_marker(&request.directory, "tauri.request_parsed", "parsed")?;
            Ok(Some(request))
        }
        _ => Err("WP_3V_01_ACCEPTANCE_REQUEST_INVALID".to_string()),
    }
}

fn validate_request(directory: PathBuf) -> Result<AcceptanceRequest, String> {
    if !directory.is_absolute() {
        return Err("WP_3V_01_ACCEPTANCE_PATH_INVALID".to_string());
    }
    if fs::symlink_metadata(&directory)
        .map_err(|_| "WP_3V_01_ACCEPTANCE_PATH_INVALID".to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("WP_3V_01_ACCEPTANCE_PATH_INVALID".to_string());
    }
    let directory = directory
        .canonicalize()
        .map_err(|_| "WP_3V_01_ACCEPTANCE_PATH_INVALID".to_string())?;
    let temp = std::env::temp_dir()
        .canonicalize()
        .map_err(|_| "WP_3V_01_ACCEPTANCE_TEMP_UNAVAILABLE".to_string())?;
    let relative = directory
        .strip_prefix(&temp)
        .map_err(|_| "WP_3V_01_ACCEPTANCE_PATH_OUTSIDE_TEMP".to_string())?;
    if relative.components().count() != 1
        || !relative
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.starts_with("sakura-wp-3-06-"))
    {
        return Err("WP_3V_01_ACCEPTANCE_PATH_INVALID".to_string());
    }
    if !regular_non_link(&directory.join(SANITIZED_MARKER)) {
        return Err("WP_3V_01_ACCEPTANCE_MARKER_MISSING".to_string());
    }
    let app_root = directory.join(APP_ROOT);
    if fs::symlink_metadata(&app_root)
        .map_err(|_| "WP_3V_01_ACCEPTANCE_ROOT_INVALID".to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("WP_3V_01_ACCEPTANCE_ROOT_INVALID".to_string());
    }
    let app_root = app_root
        .canonicalize()
        .map_err(|_| "WP_3V_01_ACCEPTANCE_ROOT_INVALID".to_string())?;
    if app_root.parent() != Some(directory.as_path())
        || ![
            "data/config/system_config.yaml",
            "data/config/characters.yaml",
            "data/config/api.yaml",
            "data/chat_history/fixture.jsonl",
            "characters/fixture/character.json",
        ]
        .iter()
        .all(|relative| regular_non_link(&app_root.join(relative)))
    {
        return Err("WP_3V_01_ACCEPTANCE_FIXTURE_INCOMPLETE".to_string());
    }
    Ok(AcceptanceRequest {
        directory,
        app_root,
    })
}

pub fn record_lock_conflict(request: &AcceptanceRequest) -> Result<(), String> {
    write_marker(&request.directory, "tauri.lock_conflict", "already_running")
}

pub fn start_driver(
    request: Option<AcceptanceRequest>,
    app: AppHandle,
    lifecycle: Option<ShellLifecycleHandle>,
) -> Result<Option<JoinHandle<()>>, String> {
    let Some(request) = request else {
        return Ok(None);
    };
    let lifecycle = match lifecycle {
        Some(lifecycle) => lifecycle,
        None => {
            let error = "WP_3V_01_LIFECYCLE_UNAVAILABLE";
            let _ = write_marker(&request.directory, "tauri.start_error", error);
            return Err(error.to_string());
        }
    };
    // Resolve and subscribe to the configured window while the startup thread still owns the
    // freshly built app. Re-querying the manager from the acceptance worker races Windows'
    // WebView registration before the event loop starts and can report a false missing window.
    let window = match app.get_webview_window("main") {
        Some(window) => window,
        None => {
            let error = "WP_3V_01_MAIN_WINDOW_UNAVAILABLE";
            let _ = write_marker(&request.directory, "tauri.start_error", error);
            return Err(error.to_string());
        }
    };
    let (events, receiver) = mpsc::channel();
    let listener = window.listen(CHAT_EVENT, move |event| {
        let _ = events.send(event.payload().to_string());
    });
    if let Err(error) = write_marker(&request.directory, "tauri.driver_started", "started") {
        window.unlisten(listener);
        return Err(error);
    }
    Ok(Some(thread::spawn(move || {
        let result = run_vertical_slice(&request, &lifecycle, &receiver);
        window.unlisten(listener);
        let exit_code = match result {
            Ok(()) => 0,
            Err(error) => {
                let _ = write_marker(&request.directory, "tauri.error", &error);
                2
            }
        };
        let _ = lifecycle.request_shutdown();
        app.exit(exit_code);
    })))
}

fn run_vertical_slice(
    request: &AcceptanceRequest,
    lifecycle: &ShellLifecycleHandle,
    events: &Receiver<String>,
) -> Result<(), String> {
    let initial_generation = wait_for_generation(lifecycle, None, Duration::from_secs(35))?;
    wait_for_character_presentation(lifecycle, Duration::from_secs(10))?;

    let old_bridge = complete_chat(
        lifecycle,
        events,
        COMPLETE_MESSAGE,
        COMPLETE_REPLY,
        "initial",
    )?;
    cancel_chat(lifecycle, events)?;

    write_marker(&request.directory, "core.kill_requested", "kill")?;
    wait_for(Duration::from_secs(15), || {
        request.directory.join("core.killed").is_file()
    })
    .ok_or_else(|| "WP_3V_01_CORE_KILL_NOT_OBSERVED".to_string())?;
    let replacement_generation = wait_for_generation(
        lifecycle,
        Some(initial_generation.as_str()),
        Duration::from_secs(35),
    )?;
    let stale_error = match old_bridge.send("main", "[WP-3V-01-STALE]".to_string()) {
        Ok(_) => return Err("WP_3V_01_STALE_BRIDGE_ACCEPTED".to_string()),
        Err(error) => error,
    };
    if stale_error != "CHAT_GENERATION_INVALIDATED" {
        return Err(format!("WP_3V_01_STALE_BRIDGE_ACCEPTED:{stale_error}"));
    }
    wait_for_character_presentation(lifecycle, Duration::from_secs(10))?;
    complete_chat(
        lifecycle,
        events,
        RECOVERY_MESSAGE,
        RECOVERY_REPLY,
        "recovery",
    )?;

    let history = fs::read_to_string(request.app_root.join("data/chat_history/fixture.jsonl"))
        .map_err(|_| "WP_3V_01_HISTORY_UNAVAILABLE".to_string())?;
    for marker in [
        COMPLETE_MESSAGE,
        COMPLETE_REPLY,
        CANCEL_MESSAGE,
        RECOVERY_MESSAGE,
        RECOVERY_REPLY,
    ] {
        if !history.contains(marker) {
            return Err(format!("WP_3V_01_HISTORY_MARKER_MISSING:{marker}"));
        }
    }
    fs::write(
        request.directory.join("tauri.evidence.json"),
        serde_json::to_vec(&json!({
            "cancelTerminalCount": 1,
            "characterRestored": true,
            "initialGeneration": initial_generation,
            "replacementGeneration": replacement_generation,
            "staleGenerationRejected": true
        }))
        .map_err(|_| "WP_3V_01_EVIDENCE_SERIALIZE_FAILED".to_string())?,
    )
    .map_err(|_| "WP_3V_01_EVIDENCE_WRITE_FAILED".to_string())?;
    write_marker(&request.directory, "tauri.vertical_complete", "complete")?;

    let bridge = lifecycle.chat_bridge()?;
    let pending = bridge.send("main", SHUTDOWN_MESSAGE.to_string())?;
    let publication = pending.wait()?;
    lifecycle.settings_request(
        Some("wp-3v-01-health-shutdown"),
        "system.health",
        json!({}),
        Duration::from_secs(3),
    )?;
    write_marker(
        &request.directory,
        "tauri.shutdown_during_chat",
        &publication.operation_id,
    )?;
    lifecycle.request_shutdown()?;
    Ok(())
}

fn complete_chat(
    lifecycle: &ShellLifecycleHandle,
    events: &Receiver<String>,
    message: &str,
    reply_marker: &str,
    request_suffix: &str,
) -> Result<ChatBridge, String> {
    let bridge = lifecycle.chat_bridge()?;
    let pending = bridge.send("main", message.to_string())?;
    let publication = pending.wait()?;
    lifecycle.settings_request(
        Some(&format!("wp-3v-01-health-{request_suffix}")),
        "system.health",
        json!({}),
        Duration::from_secs(3),
    )?;
    let terminal = wait_for_terminal(
        events,
        &publication.operation_id,
        "chat.completed",
        Duration::from_secs(35),
    )?;
    if !terminal.to_string().contains(reply_marker) {
        return Err(format!("WP_3V_01_REPLY_MARKER_MISSING:{reply_marker}"));
    }
    Ok(bridge)
}

fn cancel_chat(lifecycle: &ShellLifecycleHandle, events: &Receiver<String>) -> Result<(), String> {
    let bridge = lifecycle.chat_bridge()?;
    let pending = bridge.send("main", CANCEL_MESSAGE.to_string())?;
    let publication = pending.wait()?;
    lifecycle.settings_request(
        Some("wp-3v-01-health-cancel"),
        "system.health",
        json!({}),
        Duration::from_secs(3),
    )?;
    let cancelled = bridge.cancel(
        "main",
        &publication.operation_id,
        &publication.cancel_handle,
    )?;
    if !cancelled.accepted {
        return Err("WP_3V_01_CANCEL_NOT_ACCEPTED".to_string());
    }
    wait_for_terminal(
        events,
        &publication.operation_id,
        "chat.cancelled",
        Duration::from_secs(15),
    )?;
    let duplicate_deadline = Instant::now() + Duration::from_millis(500);
    while Instant::now() < duplicate_deadline {
        match events.recv_timeout(Duration::from_millis(50)) {
            Ok(payload) => {
                let event = parse_chat_event(&payload)?;
                if event.get("operationId").and_then(Value::as_str)
                    == Some(publication.operation_id.as_str())
                    && event
                        .get("type")
                        .and_then(Value::as_str)
                        .is_some_and(is_terminal)
                {
                    return Err("WP_3V_01_CANCEL_DUPLICATE_TERMINAL".to_string());
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                return Err("WP_3V_01_CHAT_EVENTS_DISCONNECTED".to_string())
            }
        }
    }
    Ok(())
}

fn wait_for_terminal(
    events: &Receiver<String>,
    operation_id: &str,
    expected: &str,
    timeout: Duration,
) -> Result<Value, String> {
    let deadline = Instant::now() + timeout;
    let mut observed = Vec::new();
    while Instant::now() < deadline {
        let remaining = deadline.saturating_duration_since(Instant::now());
        match events.recv_timeout(remaining.min(Duration::from_millis(250))) {
            Ok(payload) => {
                let event = parse_chat_event(&payload)?;
                if observed.len() < 16 {
                    observed.push(event.to_string());
                }
                if event.get("operationId").and_then(Value::as_str) != Some(operation_id) {
                    continue;
                }
                let event_type = event
                    .get("type")
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("WP_3V_01_CHAT_EVENT_TYPE_MISSING:{}", event))?;
                if event_type == expected {
                    return Ok(event);
                }
                if is_terminal(event_type) {
                    return Err(format!(
                        "WP_3V_01_UNEXPECTED_TERMINAL:{expected}:{event_type}"
                    ));
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                return Err("WP_3V_01_CHAT_EVENTS_DISCONNECTED".to_string())
            }
        }
    }
    Err(format!(
        "WP_3V_01_CHAT_TERMINAL_TIMEOUT:{expected}:observed={}",
        Value::Array(observed.into_iter().map(Value::String).collect::<Vec<_>>())
    ))
}

fn parse_chat_event(payload: &str) -> Result<Value, String> {
    let parsed: Value =
        serde_json::from_str(payload).map_err(|_| "WP_3V_01_CHAT_EVENT_INVALID".to_string())?;
    match parsed {
        Value::String(inner) => {
            serde_json::from_str(&inner).map_err(|_| "WP_3V_01_CHAT_EVENT_INVALID".to_string())
        }
        value => Ok(value),
    }
}

fn wait_for_generation(
    lifecycle: &ShellLifecycleHandle,
    previous: Option<&str>,
    timeout: Duration,
) -> Result<String, String> {
    let mut generation = None;
    wait_for(timeout, || {
        generation = lifecycle.available_generation_id().ok().flatten();
        generation
            .as_deref()
            .is_some_and(|current| previous != Some(current))
    })
    .ok_or_else(|| "WP_3V_01_CORE_READY_TIMEOUT".to_string())?;
    generation.ok_or_else(|| "WP_3V_01_GENERATION_MISSING".to_string())
}

fn wait_for_character_presentation(
    lifecycle: &ShellLifecycleHandle,
    timeout: Duration,
) -> Result<Value, String> {
    let mut presentation = None;
    wait_for(timeout, || {
        presentation = lifecycle.character_presentation().ok().flatten();
        presentation.is_some()
    })
    .ok_or_else(|| "WP_3V_01_CHARACTER_PRESENTATION_TIMEOUT".to_string())?;
    presentation.ok_or_else(|| "WP_3V_01_CHARACTER_PRESENTATION_MISSING".to_string())
}

fn is_terminal(event_type: &str) -> bool {
    matches!(
        event_type,
        "chat.completed" | "chat.failed" | "chat.cancelled"
    )
}

fn wait_for(timeout: Duration, mut predicate: impl FnMut() -> bool) -> Option<()> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() {
            return Some(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    None
}

fn write_marker(directory: &Path, name: &str, value: &str) -> Result<(), String> {
    fs::write(directory.join(name), value.as_bytes())
        .map_err(|_| "WP_3V_01_ACCEPTANCE_MARKER_WRITE_FAILED".to_string())
}

fn regular_non_link(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.file_type().is_file() && !metadata.file_type().is_symlink())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!("sakura-wp-3-06-{nonce}"));
            let app_root = root.join(APP_ROOT);
            fs::create_dir_all(app_root.join("data/config")).unwrap();
            fs::create_dir_all(app_root.join("data/chat_history")).unwrap();
            fs::create_dir_all(app_root.join("characters/fixture")).unwrap();
            fs::write(root.join(SANITIZED_MARKER), b"sanitized").unwrap();
            fs::write(
                app_root.join("data/config/system_config.yaml"),
                b"config_version: 4\n",
            )
            .unwrap();
            fs::write(
                app_root.join("data/config/characters.yaml"),
                b"current_character_id: fixture\n",
            )
            .unwrap();
            fs::write(
                app_root.join("data/config/api.yaml"),
                b"config_version: 4\n",
            )
            .unwrap();
            fs::write(app_root.join("data/chat_history/fixture.jsonl"), b"").unwrap();
            fs::write(app_root.join("characters/fixture/character.json"), b"{}").unwrap();
            Self(root)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn request_accepts_only_the_sanitized_oracle_compatible_temp_root() {
        let fixture = Fixture::new();
        let request = validate_request(fixture.0.clone()).unwrap();
        assert_eq!(
            request.app_root,
            fixture.0.join(APP_ROOT).canonicalize().unwrap()
        );
        fs::remove_file(fixture.0.join(SANITIZED_MARKER)).unwrap();
        assert!(validate_request(fixture.0.clone()).is_err());
    }

    #[test]
    fn request_rejects_relative_and_non_fixture_roots() {
        assert!(validate_request(PathBuf::from(".")).is_err());
    }

    #[test]
    fn chat_event_parser_accepts_direct_and_tauri_window_wrapped_payloads() {
        let direct = r#"{"type":"chat.completed","operationId":"op"}"#;
        let wrapped = serde_json::to_string(direct).unwrap();
        assert_eq!(parse_chat_event(direct).unwrap()["type"], "chat.completed");
        assert_eq!(parse_chat_event(&wrapped).unwrap()["operationId"], "op");
    }
}
