use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, WebviewWindow, Window, WindowEvent};

const RPC_MARKER: &str = "@@SAKURA_STUDIO_RPC@@";
const RPC_RESULT_MARKER: &str = "@@SAKURA_STUDIO_RPC_RESULT@@";
const CONTROL_MARKER: &str = "@@SAKURA_STUDIO_CONTROL@@";
const CLOSE_REQUESTED_EVENT: &str = "sakura://studio-close-requested";
const DEFAULT_HOST_RPC_TIMEOUT: Duration = Duration::from_secs(30);
const FILE_RPC_TIMEOUT: Duration = Duration::from_secs(30 * 60);
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

        match rx.recv_timeout(host_rpc_timeout(method)) {
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

#[tauri::command]
fn load_request(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(state.request.clone())
}

#[tauri::command]
fn show_studio(window: Window) -> Result<(), String> {
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn host_call(method: String, params: Value, state: State<'_, AppState>) -> Result<Value, String> {
    state.rpc.call(&method, params)
}

#[tauri::command]
fn close_studio(window: Window) -> Result<(), String> {
    let app = window.app_handle().clone();
    window.destroy().map_err(|error| error.to_string())?;
    app.exit(0);
    Ok(())
}

pub fn run() {
    let (request, rpc, window_control) = match read_request_and_spawn_rpc_reader() {
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
            show_studio,
            host_call,
            close_studio
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
        .expect("failed to run Sakura character studio");
}

fn read_request_and_spawn_rpc_reader() -> Result<(Value, HostRpc, WindowControl), String> {
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
    let window_control = WindowControl::default();
    let reader_window_control = window_control.clone();
    std::thread::spawn(move || {
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

fn host_rpc_timeout(method: &str) -> Duration {
    match method {
        "studio.open_character"
        | "studio.save_character"
        | "studio.import_portrait"
        | "studio.import_portrait_folder"
        | "studio.import_voice_model"
        | "studio.import_reference_audio"
        | "studio.import_reference_audio_folder"
        | "studio.load_reference_audio_preview"
        | "studio.export_archive"
        | "studio.pick_screen_color" => FILE_RPC_TIMEOUT,
        _ => DEFAULT_HOST_RPC_TIMEOUT,
    }
}

fn next_rpc_id() -> String {
    let counter = RPC_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("studio-{nanos}-{counter}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_jsonl_rpc_response_with_matching_id() {
        let line = r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-1","ok":true,"result":{"count":1}}"#;

        let response = parse_rpc_response_line(line).expect("response should parse");

        assert_eq!(response.id, "rpc-1");
        assert!(response.ok);
        assert_eq!(response.result.unwrap()["count"], 1);
    }

    #[test]
    fn ignores_invalid_rpc_response_lines() {
        assert!(parse_rpc_response_line("plain log").is_none());
        assert!(parse_rpc_response_line("@@SAKURA_STUDIO_RPC_RESULT@@not-json").is_none());
        assert!(parse_rpc_response_line(r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-1"}"#).is_none());
    }

    #[test]
    fn parses_jsonl_rpc_error_response() {
        let line = r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-2","ok":false,"error":"failed"}"#;

        let response = parse_rpc_response_line(line).expect("response should parse");

        assert_eq!(response.id, "rpc-2");
        assert!(!response.ok);
        assert_eq!(response.error.as_deref(), Some("failed"));
    }

    #[test]
    fn parses_focus_window_control_message() {
        let line = r#"@@SAKURA_STUDIO_CONTROL@@{"action":"focus"}"#;

        assert_eq!(
            parse_window_control_line(line),
            Some(WindowControlCommand::Focus)
        );
        assert!(parse_window_control_line("plain log").is_none());
        assert!(
            parse_window_control_line(r#"@@SAKURA_STUDIO_CONTROL@@{"action":"unknown"}"#).is_none()
        );
    }

    #[test]
    fn uses_long_timeout_for_file_rpc() {
        assert_eq!(
            host_rpc_timeout("studio.open_character"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.save_character"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.import_portrait"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.import_voice_model"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.import_reference_audio"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.import_portrait_folder"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.import_reference_audio_folder"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.load_reference_audio_preview"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.export_archive"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.pick_screen_color"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            host_rpc_timeout("studio.list_characters"),
            Duration::from_secs(30)
        );
    }
}
