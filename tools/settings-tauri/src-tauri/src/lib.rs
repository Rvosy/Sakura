use std::collections::HashMap;
use std::io::{BufRead, Write};
#[cfg(unix)]
use std::os::unix::io::FromRawFd;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, WebviewWindow, Window, WindowEvent};

/// Lines on stdout that start with this marker carry a live layout preview for
/// the host (Python) to apply immediately. Anything else on stdout is ignored.
const PREVIEW_MARKER: &str = "@@SAKURA_LAYOUT_PREVIEW@@";
const RESULT_MARKER: &str = "@@SAKURA_SETTINGS_RESULT@@";
const RPC_MARKER: &str = "@@SAKURA_SETTINGS_RPC@@";
const RPC_RESULT_MARKER: &str = "@@SAKURA_SETTINGS_RPC_RESULT@@";
const CONTROL_MARKER: &str = "@@SAKURA_SETTINGS_CONTROL@@";
const CLOSE_REQUESTED_EVENT: &str = "sakura://settings-close-requested";
const PROTOCOL_VERSION: u8 = 3;
const DEFAULT_HOST_RPC_TIMEOUT: Duration = Duration::from_secs(30);
const LONG_HOST_RPC_TIMEOUT: Duration = Duration::from_secs(30 * 60);
static RPC_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
struct AppState {
    request: Value,
    rpc: HostRpc,
}

#[derive(Clone)]
struct HostRpc {
    pending: Arc<Mutex<HashMap<String, mpsc::Sender<RpcResponse>>>>,
}

struct RpcResponse {
    id: String,
    ok: bool,
    result: Option<Value>,
    error: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
enum WindowControlCommand {
    Focus,
}

#[derive(Clone, Default)]
struct WindowControl {
    window: Arc<Mutex<Option<WebviewWindow>>>,
}

impl WindowControl {
    fn register(&self, window: WebviewWindow) {
        if let Ok(mut current) = self.window.lock() {
            *current = Some(window);
        }
    }

    fn execute(&self, command: WindowControlCommand) {
        let window = self.window.lock().ok().and_then(|current| current.clone());
        let Some(window) = window else {
            return;
        };
        match command {
            WindowControlCommand::Focus => {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
    }
}

impl HostRpc {
    fn new() -> Self {
        Self {
            pending: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = next_rpc_id();
        let (tx, rx) = mpsc::channel();
        self.pending
            .lock()
            .map_err(|_| "RPC pending map is poisoned".to_string())?
            .insert(id.clone(), tx);

        let payload = json!({
            "id": id,
            "method": method,
            "params": params,
        });
        let line = serde_json::to_string(&payload).map_err(|error| error.to_string())?;
        let write_result = (|| -> Result<(), String> {
            let mut out = std::io::stdout().lock();
            writeln!(out, "{RPC_MARKER}{line}").map_err(|error| error.to_string())?;
            out.flush().map_err(|error| error.to_string())?;
            Ok(())
        })();
        if let Err(error) = write_result {
            self.remove_pending(&id);
            return Err(error);
        }

        let response = match host_rpc_timeout(method) {
            Some(timeout) => match rx.recv_timeout(timeout) {
                Ok(response) => response,
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    self.remove_pending(&id);
                    return Err("host RPC timed out".to_string());
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    self.remove_pending(&id);
                    return Err("host RPC channel disconnected".to_string());
                }
            },
            None => match rx.recv() {
                Ok(response) => response,
                Err(_) => {
                    self.remove_pending(&id);
                    return Err("host RPC channel disconnected".to_string());
                }
            },
        };
        if response.ok {
            Ok(response.result.unwrap_or(Value::Null))
        } else {
            Err(response
                .error
                .unwrap_or_else(|| "host RPC returned an error".to_string()))
        }
    }

    fn remove_pending(&self, id: &str) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.remove(id);
        }
    }
}

/// Hand the request JSON to the frontend verbatim.
///
/// The request shape is defined and validated entirely on the Python side
/// (`app/ui/tauri_settings.py`). Re-typing it here only risks silently dropping
/// fields the frontend needs, so we pass the parsed JSON through untouched.
#[tauri::command]
fn load_request(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(state.request.clone())
}

/// Persist the settings the frontend collected.
///
/// We trust the frontend payload as-is and only stamp the protocol `version`
/// and the request `nonce` so the Python side can verify the round-trip. Python
/// (`parse_tauri_settings_result`) is the single source of truth for validation.
/// Write the collected settings to stdout for the host (Python) to parse.
///
/// `keep_open` distinguishes 应用 (apply: persist, window stays open) from
/// 保存 (save: persist, window closes). Python routes on the `keep_open` flag.
fn settings_result_payload(
    settings: Value,
    state: &AppState,
    keep_open: bool,
) -> Result<Value, String> {
    let nonce = state
        .request
        .get("nonce")
        .and_then(Value::as_str)
        .ok_or_else(|| "request is missing nonce".to_string())?;

    let mut payload = match settings {
        Value::Object(map) => map,
        _ => return Err("settings payload must be a JSON object".to_string()),
    };
    payload.insert("version".to_string(), Value::from(PROTOCOL_VERSION));
    payload.insert("nonce".to_string(), Value::from(nonce));
    payload.insert("keep_open".to_string(), Value::from(keep_open));
    Ok(Value::Object(payload))
}

fn host_rpc_timeout(method: &str) -> Option<Duration> {
    match method {
        "studio.launch" => None,
        "character.import_archive"
        | "character.import_voice_archive"
        | "character.export_archive" => Some(LONG_HOST_RPC_TIMEOUT),
        _ => Some(DEFAULT_HOST_RPC_TIMEOUT),
    }
}

fn clear_pending_rpcs(pending: &Arc<Mutex<HashMap<String, mpsc::Sender<RpcResponse>>>>) {
    if let Ok(mut pending) = pending.lock() {
        pending.clear();
    }
}

fn write_settings_result(settings: Value, state: &AppState, keep_open: bool) -> Result<(), String> {
    let payload = settings_result_payload(settings, state, keep_open)?;
    let line = serde_json::to_string(&payload).map_err(|error| error.to_string())?;
    let mut out = std::io::stdout().lock();
    writeln!(out, "{RESULT_MARKER}{line}").map_err(|error| error.to_string())?;
    out.flush().map_err(|error| error.to_string())
}

#[tauri::command]
fn save_settings(
    settings: Value,
    state: State<'_, AppState>,
    window: Window,
) -> Result<(), String> {
    write_settings_result(settings, &state, false)?;
    close_settings_window(window)
}

/// Persist the settings but keep the window open (「应用」按钮)。
#[tauri::command]
fn apply_settings(settings: Value, state: State<'_, AppState>) -> Result<Value, String> {
    let payload = settings_result_payload(settings, &state, true)?;
    state
        .rpc
        .call("settings.apply", json!({ "settings": payload }))
}

/// Stream a live layout preview to the host without closing the window.
///
/// Slider drags on the character page call this on every change so the running
/// desktop pet updates in real time; the value is only persisted later via
/// `save_settings`. stdout is block-buffered when piped, so flush every line.
#[tauri::command]
fn preview_layout(layout: Value) -> Result<(), String> {
    let line = serde_json::to_string(&layout).map_err(|error| error.to_string())?;
    let mut out = std::io::stdout().lock();
    writeln!(out, "{PREVIEW_MARKER}{line}").map_err(|error| error.to_string())?;
    out.flush().map_err(|error| error.to_string())
}

#[tauri::command]
fn host_call(method: String, params: Value, state: State<'_, AppState>) -> Result<Value, String> {
    state.rpc.call(&method, params)
}

#[tauri::command]
fn cancel_settings(window: Window) -> Result<(), String> {
    close_settings_window(window)
}

fn close_settings_window(window: Window) -> Result<(), String> {
    let app = window.app_handle().clone();
    window.destroy().map_err(|error| error.to_string())?;
    app.exit(0);
    Ok(())
}

pub fn run() {
    #[cfg(unix)]
    let rpc_response_fd = std::env::var("SAKURA_RPC_RESPONSE_FD")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);

    let (request, rpc, window_control) = match {
        #[cfg(unix)]
        { read_request_and_spawn_rpc_reader(rpc_response_fd) }
        #[cfg(not(unix))]
        { read_request_and_spawn_rpc_reader() }
    } {
        Ok(state) => state,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };

    let setup_window_control = window_control.clone();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState { request, rpc })
        .setup(move |app| {
            if let Some(window) = app.get_webview_window("main") {
                setup_window_control.register(window);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_request,
            save_settings,
            apply_settings,
            preview_layout,
            host_call,
            cancel_settings
        ])
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.emit(CLOSE_REQUESTED_EVENT, json!({}));
            }
            WindowEvent::Destroyed => {
                window.app_handle().exit(0);
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("failed to run Sakura settings window");
}

#[cfg(unix)]
fn read_request_and_spawn_rpc_reader(rpc_response_fd: std::os::unix::io::RawFd) -> Result<(Value, HostRpc, WindowControl), String> {
    if rpc_response_fd < 3 {
        return Err("SAKURA_RPC_RESPONSE_FD must refer to a non-standard fd".to_string());
    }
    let mut initial_line = String::new();
    {
        let stdin_guard = std::io::stdin().lock();
        let mut reader = std::io::BufReader::new(stdin_guard);
        let bytes = reader
            .read_line(&mut initial_line)
            .map_err(|error| error.to_string())?;
        if bytes == 0 {
            return Err("request payload is empty".to_string());
        }
    }
    let value: Value = serde_json::from_str(initial_line.trim_end()).map_err(|error| error.to_string())?;
    if !matches!(value, Value::Object(_)) {
        return Err("request payload must be a JSON object".to_string());
    }
    let rpc = HostRpc::new();
    let pending = rpc.pending.clone();
    let window_control = WindowControl::default();
    let reader_window_control = window_control.clone();
    std::thread::spawn(move || {
        // 从独立 pipe fd 读取 RPC 响应，绕过 std::io::stdin() 的全局内部 Mutex。
        let file = unsafe { std::fs::File::from_raw_fd(rpc_response_fd) };
        let mut reader = std::io::BufReader::new(file);
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let trimmed = line.trim_end();
                    if let Some(command) = parse_window_control_line(trimmed) {
                        reader_window_control.execute(command);
                        continue;
                    }
                    if let Some(response) = parse_rpc_response_line(trimmed) {
                        if let Ok(mut pending) = pending.lock() {
                            if let Some(sender) = pending.remove(&response.id) {
                                let _ = sender.send(response);
                            }
                        }
                    }
                }
                Err(_) => break,
            }
        }
        clear_pending_rpcs(&pending);
    });
    Ok((value, rpc, window_control))
}

#[cfg(not(unix))]
fn read_request_and_spawn_rpc_reader() -> Result<(Value, HostRpc, WindowControl), String> {
    let mut reader = std::io::BufReader::new(std::io::stdin());
    let mut data = String::new();
    let bytes = reader
        .read_line(&mut data)
        .map_err(|error| error.to_string())?;
    if bytes == 0 {
        return Err("request payload is empty".to_string());
    }
    let value: Value = serde_json::from_str(data.trim_end()).map_err(|error| error.to_string())?;
    if !matches!(value, Value::Object(_)) {
        return Err("request payload must be a JSON object".to_string());
    }
    let rpc = HostRpc::new();
    let pending = rpc.pending.clone();
    let window_control = WindowControl::default();
    let reader_window_control = window_control.clone();
    std::thread::spawn(move || {
        let mut reader = std::io::BufReader::new(std::io::stdin());
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let trimmed = line.trim_end();
                    if let Some(command) = parse_window_control_line(trimmed) {
                        reader_window_control.execute(command);
                        continue;
                    }
                    if let Some(response) = parse_rpc_response_line(trimmed) {
                        if let Ok(mut pending) = pending.lock() {
                            if let Some(sender) = pending.remove(&response.id) {
                                let _ = sender.send(response);
                            }
                        }
                    }
                }
                Err(_) => break,
            }
        }
        clear_pending_rpcs(&pending);
    });
    Ok((value, rpc, window_control))
}

fn parse_window_control_line(line: &str) -> Option<WindowControlCommand> {
    let payload = line.strip_prefix(CONTROL_MARKER)?;
    let value: Value = serde_json::from_str(payload).ok()?;
    match value.get("action")?.as_str()? {
        "focus" => Some(WindowControlCommand::Focus),
        _ => None,
    }
}

fn parse_rpc_response_line(line: &str) -> Option<RpcResponse> {
    let payload = line.strip_prefix(RPC_RESULT_MARKER)?;
    let value: Value = serde_json::from_str(payload).ok()?;
    let id = value.get("id")?.as_str()?.to_string();
    let ok = value.get("ok")?.as_bool()?;
    let result = value.get("result").cloned();
    let error = value
        .get("error")
        .and_then(Value::as_str)
        .map(ToString::to_string);
    Some(RpcResponse {
        id,
        ok,
        result,
        error,
    })
}

fn next_rpc_id() -> String {
    let counter = RPC_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("settings-{nanos}-{counter}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_jsonl_rpc_response_with_matching_id() {
        let line = r#"@@SAKURA_SETTINGS_RPC_RESULT@@{"id":"rpc-1","ok":true,"result":{"count":1}}"#;

        let response = parse_rpc_response_line(line).expect("response should parse");

        assert_eq!(response.id, "rpc-1");
        assert!(response.ok);
        assert_eq!(response.result.unwrap()["count"], 1);
    }

    #[test]
    fn ignores_invalid_rpc_response_lines() {
        assert!(parse_rpc_response_line("plain log").is_none());
        assert!(parse_rpc_response_line("@@SAKURA_SETTINGS_RPC_RESULT@@not-json").is_none());
        assert!(
            parse_rpc_response_line(r#"@@SAKURA_SETTINGS_RPC_RESULT@@{"id":"rpc-1"}"#).is_none()
        );
    }

    #[test]
    fn parses_jsonl_rpc_error_response() {
        let line = r#"@@SAKURA_SETTINGS_RPC_RESULT@@{"id":"rpc-2","ok":false,"error":"failed"}"#;

        let response = parse_rpc_response_line(line).expect("response should parse");

        assert_eq!(response.id, "rpc-2");
        assert!(!response.ok);
        assert_eq!(response.error.as_deref(), Some("failed"));
    }

    #[test]
    fn parses_focus_window_control_message() {
        let line = r#"@@SAKURA_SETTINGS_CONTROL@@{"action":"focus"}"#;

        assert_eq!(
            parse_window_control_line(line),
            Some(WindowControlCommand::Focus)
        );
        assert!(parse_window_control_line("plain log").is_none());
        assert!(
            parse_window_control_line(r#"@@SAKURA_SETTINGS_CONTROL@@{"action":"unknown"}"#)
                .is_none()
        );
    }

    #[test]
    fn uses_expected_timeout_for_host_rpc() {
        assert_eq!(
            host_rpc_timeout("character.import_archive"),
            Some(Duration::from_secs(30 * 60))
        );
        assert_eq!(
            host_rpc_timeout("character.import_voice_archive"),
            Some(Duration::from_secs(30 * 60))
        );
        assert_eq!(
            host_rpc_timeout("character.export_archive"),
            Some(Duration::from_secs(30 * 60))
        );
        assert_eq!(host_rpc_timeout("studio.launch"), None);
        assert_eq!(
            host_rpc_timeout("api.test_connection"),
            Some(Duration::from_secs(30))
        );
    }

    #[test]
    fn clearing_pending_rpcs_disconnects_waiters() {
        let pending = Arc::new(Mutex::new(HashMap::new()));
        let (tx, rx) = mpsc::channel();
        pending
            .lock()
            .expect("pending map should lock")
            .insert("rpc-1".to_string(), tx);

        clear_pending_rpcs(&pending);

        assert!(matches!(
            rx.recv_timeout(Duration::from_millis(10)),
            Err(mpsc::RecvTimeoutError::Disconnected)
        ));
    }
}
