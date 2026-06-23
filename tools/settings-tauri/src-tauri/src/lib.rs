use std::io::{Read, Write};

use serde_json::Value;
use tauri::{State, Window};

/// Lines on stdout that start with this marker carry a live layout preview for
/// the host (Python) to apply immediately. Anything else on stdout is ignored.
const PREVIEW_MARKER: &str = "@@SAKURA_LAYOUT_PREVIEW@@";
const RESULT_MARKER: &str = "@@SAKURA_SETTINGS_RESULT@@";
const PROTOCOL_VERSION: u8 = 2;

#[derive(Clone)]
struct AppState {
    request: Value,
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
#[tauri::command]
fn save_settings(
    settings: Value,
    state: State<'_, AppState>,
    window: Window,
) -> Result<(), String> {
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

    let line = serde_json::to_string(&Value::Object(payload)).map_err(|error| error.to_string())?;
    let mut out = std::io::stdout().lock();
    writeln!(out, "{RESULT_MARKER}{line}").map_err(|error| error.to_string())?;
    out.flush().map_err(|error| error.to_string())?;
    window.close().map_err(|error| error.to_string())
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
fn cancel_settings(window: Window) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

pub fn run() {
    let request = match read_request_from_stdin() {
        Ok(request) => request,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };

    tauri::Builder::default()
        .manage(AppState { request })
        .invoke_handler(tauri::generate_handler![
            load_request,
            save_settings,
            preview_layout,
            cancel_settings
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Sakura settings window");
}

fn read_request_from_stdin() -> Result<Value, String> {
    let mut data = String::new();
    std::io::stdin()
        .read_to_string(&mut data)
        .map_err(|error| error.to_string())?;
    let value: Value = serde_json::from_str(&data).map_err(|error| error.to_string())?;
    if !matches!(value, Value::Object(_)) {
        return Err("request payload must be a JSON object".to_string());
    }
    Ok(value)
}
