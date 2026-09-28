use std::{collections::BTreeMap, sync::Mutex};

use serde::{Deserialize, Serialize};
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::{character_appearance, character_presentation, runtime_log_window};

const ERROR_DIALOG_WINDOW_LABEL: &str = "error-dialog";
const ERROR_DIALOG_UPDATED_EVENT: &str = "sakura://error-dialog-updated";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ErrorDialogPayload {
    title: String,
    message: String,
    details: String,
}

#[derive(Default)]
pub(crate) struct ErrorDialogState {
    payload: Mutex<Option<ErrorDialogPayload>>,
}

impl ErrorDialogState {
    fn update(&self, window_label: &str, payload: ErrorDialogPayload) -> Result<(), String> {
        if window_label != "main" {
            return Err("PET_WINDOW_REQUIRED".to_string());
        }
        *self.payload.lock().map_err(|error| {
            crate::runtime_log::diagnostic_error("ERROR_DIALOG_STATE_UNAVAILABLE", error)
        })? = Some(payload);
        Ok(())
    }

    fn snapshot(&self, window_label: &str) -> Result<ErrorDialogPayload, String> {
        validate_dialog_window(window_label)?;
        self.payload
            .lock()
            .map_err(|error| {
                crate::runtime_log::diagnostic_error("ERROR_DIALOG_STATE_UNAVAILABLE", error)
            })?
            .clone()
            .ok_or_else(|| "ERROR_DIALOG_UNAVAILABLE".to_string())
    }
}

fn validate_dialog_window(window_label: &str) -> Result<(), String> {
    if window_label == ERROR_DIALOG_WINDOW_LABEL {
        Ok(())
    } else {
        Err("ERROR_DIALOG_WINDOW_REQUIRED".to_string())
    }
}

fn show_or_focus(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(ERROR_DIALOG_WINDOW_LABEL) {
        window
            .emit(ERROR_DIALOG_UPDATED_EVENT, ())
            .map_err(|error| format!("ERROR_DIALOG_EVENT_FAILED: {error}"))?;
        // The WebView applies its theme before the first visible frame.
        if !window.is_visible().map_err(|error| error.to_string())? {
            return Ok(());
        }
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        return window.set_focus().map_err(|error| error.to_string());
    }

    WebviewWindowBuilder::new(
        app,
        ERROR_DIALOG_WINDOW_LABEL,
        WebviewUrl::App("error-dialog/index.html".into()),
    )
    .title("Sakura 错误详情")
    .background_color(Color(248, 252, 254, 255))
    .visible(false)
    .inner_size(560.0, 420.0)
    .min_inner_size(360.0, 260.0)
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
    .map_err(|error| format!("ERROR_DIALOG_WINDOW_CREATE_FAILED: {error}"))
}

#[tauri::command]
pub(crate) async fn show_error_dialog(
    window: WebviewWindow,
    payload: ErrorDialogPayload,
    state: State<'_, ErrorDialogState>,
) -> Result<(), String> {
    state.update(window.label(), payload)?;
    let app = window.app_handle().clone();
    let action_app = app.clone();
    let (send, receive) = tokio::sync::oneshot::channel();
    // The async command queues creation outside WebView2's invoke callback.
    // Creating and checking the window on the event loop also keeps it a singleton.
    app.run_on_main_thread(move || {
        let _ = send.send(show_or_focus(&action_app));
    })
    .map_err(|error| format!("ERROR_DIALOG_DISPATCH_FAILED: {error}"))?;
    receive
        .await
        .map_err(|error| format!("ERROR_DIALOG_DISPATCH_FAILED: {error}"))?
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ErrorDialogBootstrap {
    #[serde(flatten)]
    payload: ErrorDialogPayload,
    theme_tokens: BTreeMap<String, String>,
}

#[tauri::command]
pub(crate) fn error_dialog_bootstrap(
    window: WebviewWindow,
    state: State<'_, ErrorDialogState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<ErrorDialogBootstrap, String> {
    let payload = state.snapshot(window.label())?;
    let theme_tokens = resources
        .active_presentation()
        .ok()
        .flatten()
        .and_then(|presentation| appearance.current(&presentation).ok())
        .map(|publication| publication.values.theme_tokens)
        .unwrap_or_else(runtime_log_window::fallback_theme_tokens);
    Ok(ErrorDialogBootstrap {
        payload,
        theme_tokens,
    })
}

#[tauri::command]
pub(crate) fn reveal_error_dialog(window: WebviewWindow) -> Result<(), String> {
    validate_dialog_window(window.label())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn close_error_dialog(window: WebviewWindow) -> Result<(), String> {
    validate_dialog_window(window.label())?;
    window.destroy().map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn payload(details: &str) -> ErrorDialogPayload {
        ErrorDialogPayload {
            title: "朗读失败".to_string(),
            message: "语音未能播放。".to_string(),
            details: details.to_string(),
        }
    }

    #[test]
    fn only_main_can_replace_the_error_and_only_the_dialog_can_read_it() {
        let state = ErrorDialogState::default();
        for label in [
            "settings",
            "studio",
            "history",
            "runtime-log",
            "error-dialog",
        ] {
            assert_eq!(
                state.update(label, payload("must not be stored")),
                Err("PET_WINDOW_REQUIRED".to_string())
            );
        }
        assert_eq!(
            state.snapshot(ERROR_DIALOG_WINDOW_LABEL),
            Err("ERROR_DIALOG_UNAVAILABLE".to_string())
        );
        let original = payload("TTS_SYNTHESIS_FAILED\nTraceback: first failure");
        state.update("main", original.clone()).unwrap();
        for label in ["main", "settings", "studio", "history", "runtime-log"] {
            assert_eq!(
                state.snapshot(label),
                Err("ERROR_DIALOG_WINDOW_REQUIRED".to_string())
            );
            assert_eq!(
                validate_dialog_window(label),
                Err("ERROR_DIALOG_WINDOW_REQUIRED".to_string())
            );
        }
        assert_eq!(state.snapshot(ERROR_DIALOG_WINDOW_LABEL).unwrap(), original);
    }

    #[test]
    fn repeated_requests_replace_the_error_and_preserve_multiline_details() {
        let state = ErrorDialogState::default();
        state.update("main", payload("first error")).unwrap();
        let latest = payload("TTS_PROVIDER_FAILED\nsource.py:12\nCaused by: fixture failure");
        state.update("main", latest.clone()).unwrap();
        let snapshot = ErrorDialogBootstrap {
            payload: state.snapshot(ERROR_DIALOG_WINDOW_LABEL).unwrap(),
            theme_tokens: runtime_log_window::fallback_theme_tokens(),
        };
        let value = serde_json::to_value(snapshot).unwrap();
        assert_eq!(value["title"], latest.title);
        assert_eq!(value["message"], latest.message);
        assert_eq!(value["details"], latest.details);
        assert_eq!(value["themeTokens"]["primary"], "#4b9ac4");
        assert!(value.get("payload").is_none());
    }
}
