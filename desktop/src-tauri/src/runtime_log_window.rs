use std::collections::BTreeMap;

use serde::Serialize;
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::runtime_log::RuntimeLogViewerSnapshot;

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
