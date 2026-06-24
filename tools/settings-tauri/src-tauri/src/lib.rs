use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, Window, WindowEvent};

/// Lines on stdout that start with this marker carry a live layout preview for
/// the host (Python) to apply immediately. Anything else on stdout is ignored.
const PREVIEW_MARKER: &str = "@@SAKURA_LAYOUT_PREVIEW@@";
const RESULT_MARKER: &str = "@@SAKURA_SETTINGS_RESULT@@";
const RPC_MARKER: &str = "@@SAKURA_SETTINGS_RPC@@";
const RPC_RESULT_MARKER: &str = "@@SAKURA_SETTINGS_RPC_RESULT@@";
const CLOSE_REQUESTED_EVENT: &str = "sakura://settings-close-requested";
const PROTOCOL_VERSION: u8 = 2;
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

        match rx.recv_timeout(Duration::from_secs(30)) {
            Ok(response) if response.ok => Ok(response.result.unwrap_or(Value::Null)),
            Ok(response) => Err(response
                .error
                .unwrap_or_else(|| "host RPC returned an error".to_string())),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.remove_pending(&id);
                Err("host RPC timed out".to_string())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.remove_pending(&id);
                Err("host RPC channel disconnected".to_string())
            }
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
    state.rpc.call("settings.apply", json!({ "settings": payload }))
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
    let (request, rpc) = match read_request_and_spawn_rpc_reader() {
        Ok(state) => state,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState { request, rpc })
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

fn read_request_and_spawn_rpc_reader() -> Result<(Value, HostRpc), String> {
    let mut reader = BufReader::new(std::io::stdin());
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
    std::thread::spawn(move || {
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    if let Some(response) = parse_rpc_response_line(line.trim_end()) {
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
    });
    Ok((value, rpc))
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
}
