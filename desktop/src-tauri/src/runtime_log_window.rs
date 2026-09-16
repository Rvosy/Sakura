use std::collections::BTreeMap;

use serde::Serialize;
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::{
    character_appearance, character_presentation,
    runtime_log::{self, RuntimeLogService, RuntimeLogViewerSnapshot},
};

pub const RUNTIME_LOG_WINDOW_LABEL: &str = "runtime-log";
pub const RUNTIME_LOG_REFRESH_REQUESTED_EVENT: &str = "sakura://runtime-log-refresh-requested";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogViewerBootstrap {
    pub schema_version: u32,
    pub theme_tokens: BTreeMap<String, String>,
    pub snapshot: RuntimeLogViewerSnapshot,
}

pub fn fallback_theme_tokens() -> BTreeMap<String, String> {
    [
        ("primary", "#4b9ac4"),
        ("primaryHover", "#3b83aa"),
        ("accent", "#e36c96"),
        ("text", "#27445a"),
        ("secondaryText", "#54768b"),
        ("mutedText", "#7d99a9"),
        ("pageBackground", "#f8fcfe"),
        ("panelBackground", "#eaf5fa"),
        ("inputBackground", "#ffffff"),
        ("bubbleBackground", "#e3f1f7"),
        ("border", "#accfde"),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_string(), value.to_string()))
    .collect()
}

pub fn validate_runtime_log_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != RUNTIME_LOG_WINDOW_LABEL {
        return Err("RUNTIME_LOG_WINDOW_REQUIRED".to_string());
    }
    Ok(())
}

pub fn show_or_focus(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(RUNTIME_LOG_WINDOW_LABEL) {
        // Keep an initializing window hidden until its character theme is the
        // first WebView frame, including when the user opens it twice quickly.
        if !window.is_visible().map_err(|error| error.to_string())? {
            return Ok(());
        }
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        window
            .emit(RUNTIME_LOG_REFRESH_REQUESTED_EVENT, ())
            .map_err(|error| format!("RUNTIME_LOG_REFRESH_EVENT_FAILED: {error}"))?;
        return Ok(());
    }

    WebviewWindowBuilder::new(
        app,
        RUNTIME_LOG_WINDOW_LABEL,
        WebviewUrl::App("runtime-log/index.html".into()),
    )
    .title("Sakura 运行日志")
    .background_color(Color(248, 252, 254, 255))
    .visible(false)
    .inner_size(920.0, 620.0)
    .min_inner_size(680.0, 460.0)
    .resizable(true)
    .maximizable(true)
    .minimizable(true)
    .decorations(true)
    .devtools(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .build()
    .map(|_| ())
    .map_err(|error| format!("RUNTIME_LOG_WINDOW_CREATE_FAILED: {error}"))
}

#[tauri::command]
pub(crate) fn runtime_log_viewer_bootstrap(
    window: WebviewWindow,
    runtime_log: State<'_, RuntimeLogService>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<RuntimeLogViewerBootstrap, String> {
    validate_runtime_log_window(&window)?;
    let theme_tokens = resources
        .active_presentation()
        .ok()
        .flatten()
        .and_then(|presentation| appearance.current(&presentation).ok())
        .map(|publication| publication.values.theme_tokens)
        .unwrap_or_else(fallback_theme_tokens);
    let snapshot = runtime_log.viewer_snapshot(None).map_err(str::to_string)?;
    Ok(RuntimeLogViewerBootstrap {
        schema_version: 3,
        theme_tokens,
        snapshot,
    })
}

#[tauri::command]
pub(crate) fn runtime_log_viewer_snapshot(
    window: WebviewWindow,
    after_sequence: Option<u64>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<runtime_log::RuntimeLogViewerSnapshot, String> {
    validate_runtime_log_window(&window)?;
    runtime_log
        .viewer_snapshot(after_sequence)
        .map_err(str::to_string)
}

#[tauri::command]
pub(crate) fn close_runtime_log_viewer(window: WebviewWindow) -> Result<(), String> {
    validate_runtime_log_window(&window)?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn reveal_runtime_log_viewer(window: WebviewWindow) -> Result<(), String> {
    validate_runtime_log_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wp_5_06_fallback_theme_uses_the_complete_public_token_shape() {
        let theme = fallback_theme_tokens();
        assert_eq!(theme.len(), 11);
        assert!(theme
            .values()
            .all(|value| value.len() == 7 && value.starts_with('#')));
    }
}
