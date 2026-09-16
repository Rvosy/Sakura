use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::{
    character_appearance, character_presentation, chat_settings,
    shell_lifecycle::{
        self, dispatch_settings_request, load_current_character_presentation, settings_core_handle,
        settings_response_payload, ShellLifecycleState,
    },
};

pub const HISTORY_WINDOW_LABEL: &str = "history";
pub const HISTORY_REFRESH_REQUESTED_EVENT: &str = "sakura://history-refresh-requested";
pub const HISTORY_PAGE_LIMIT: u32 = 50;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HistoryEntry {
    pub entry_id: String,
    pub turn_id: String,
    pub kind: String,
    pub origin: String,
    pub created_at: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HistoryPage {
    pub schema_version: u32,
    pub core_generation_id: String,
    pub character_id: String,
    pub total_count: u64,
    pub entries: Vec<HistoryEntry>,
    pub before_cursor: Option<String>,
    pub has_more: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TextPayload {
    text: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct AssistantPayload {
    segments: Vec<AssistantSegment>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AssistantSegment {
    text: String,
    translation: String,
}

pub fn validate_page(value: Value) -> Result<HistoryPage, String> {
    let page: HistoryPage =
        serde_json::from_value(value).map_err(|_| "HISTORY_RESPONSE_INVALID".to_string())?;
    if page.schema_version != 1
        || page.core_generation_id.trim().is_empty()
        || page.character_id.trim().is_empty()
        || page.entries.len() > HISTORY_PAGE_LIMIT as usize
        || page.has_more != page.before_cursor.is_some()
    {
        return Err("HISTORY_RESPONSE_INVALID".to_string());
    }
    for entry in &page.entries {
        if entry.entry_id.trim().is_empty()
            || entry.turn_id.trim().is_empty()
            || entry.origin.trim().is_empty()
            || entry.created_at.trim().is_empty()
        {
            return Err("HISTORY_RESPONSE_INVALID".to_string());
        }
        match entry.kind.as_str() {
            "human" | "observation" | "system" => {
                let payload: TextPayload = serde_json::from_value(entry.payload.clone())
                    .map_err(|_| "HISTORY_RESPONSE_INVALID".to_string())?;
                if payload.text.trim().is_empty() {
                    return Err("HISTORY_RESPONSE_INVALID".to_string());
                }
            }
            "assistant" => {
                let payload: AssistantPayload = serde_json::from_value(entry.payload.clone())
                    .map_err(|_| "HISTORY_RESPONSE_INVALID".to_string())?;
                if payload.segments.is_empty()
                    || payload.segments.iter().any(|segment| {
                        segment.text.trim().is_empty() || segment.translation.len() > 64 * 1024
                    })
                {
                    return Err("HISTORY_RESPONSE_INVALID".to_string());
                }
            }
            _ => return Err("HISTORY_RESPONSE_INVALID".to_string()),
        }
    }
    Ok(page)
}

pub fn validate_history_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != HISTORY_WINDOW_LABEL {
        return Err("HISTORY_WINDOW_REQUIRED".to_string());
    }
    Ok(())
}

pub fn show_or_focus(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(HISTORY_WINDOW_LABEL) {
        // A newly-created window stays hidden until the WebView has applied the
        // current character theme. Repeated open requests must not expose the
        // default-theme frame while that bootstrap is still in flight.
        if !window.is_visible().map_err(|error| error.to_string())? {
            return Ok(());
        }
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        window
            .emit(HISTORY_REFRESH_REQUESTED_EVENT, ())
            .map_err(|error| format!("HISTORY_REFRESH_EVENT_FAILED: {error}"))?;
        return Ok(());
    }

    WebviewWindowBuilder::new(
        app,
        HISTORY_WINDOW_LABEL,
        WebviewUrl::App("history/index.html".into()),
    )
    .title("Sakura 聊天记录")
    .background_color(Color(248, 252, 254, 255))
    .visible(false)
    .inner_size(620.0, 680.0)
    .min_inner_size(480.0, 440.0)
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
    .map_err(|error| format!("HISTORY_WINDOW_CREATE_FAILED: {error}"))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct HistoryBootstrap {
    core_generation_id: String,
    character_id: String,
    assistant_name: String,
    subtitle_language: chat_settings::SubtitleLanguage,
    theme_tokens: std::collections::BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct HistoryPageRequest {
    core_generation_id: String,
    character_id: String,
    before_cursor: Option<String>,
}

async fn request_history_page(
    handle: shell_lifecycle::ShellLifecycleHandle,
    character_id: String,
    before_cursor: Option<String>,
) -> Result<HistoryPage, String> {
    let response = dispatch_settings_request(
        handle,
        None,
        "ui.history.page",
        json!({
            "expectedCharacterId": character_id,
            "beforeCursor": before_cursor,
            "limit": HISTORY_PAGE_LIMIT,
        }),
        std::time::Duration::from_secs(5),
    )
    .await?;
    validate_page(settings_response_payload(response)?)
}

#[tauri::command]
pub(crate) fn history_bootstrap(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
    subtitle: State<'_, chat_settings::SubtitleLanguageState>,
) -> Result<HistoryBootstrap, String> {
    validate_history_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let active_appearance = appearance.persisted(&presentation.presentation)?;
    if active_appearance.core_generation_id != presentation.presentation.generation_id
        || active_appearance.character_id != presentation.presentation.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    Ok(HistoryBootstrap {
        core_generation_id: presentation.presentation.generation_id,
        character_id: presentation.presentation.character_id,
        assistant_name: presentation.presentation.display_name,
        subtitle_language: subtitle.get()?,
        theme_tokens: active_appearance.values.theme_tokens,
    })
}

#[tauri::command]
pub(crate) async fn history_page(
    window: WebviewWindow,
    request: HistoryPageRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
) -> Result<HistoryPage, String> {
    validate_history_window(&window)?;
    if request.core_generation_id.trim().is_empty()
        || request.character_id.trim().is_empty()
        || request
            .before_cursor
            .as_deref()
            .is_some_and(|cursor| cursor.trim().is_empty())
    {
        return Err("HISTORY_REQUEST_INVALID".to_string());
    }
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    if presentation.presentation.generation_id != request.core_generation_id
        || presentation.presentation.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    let page = request_history_page(
        settings_core_handle(&lifecycle)?,
        request.character_id.clone(),
        request.before_cursor,
    )
    .await?;
    if page.core_generation_id != request.core_generation_id
        || page.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    Ok(page)
}

#[tauri::command]
pub(crate) fn close_history_window(window: WebviewWindow) -> Result<(), String> {
    validate_history_window(&window)?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn reveal_history_window(window: WebviewWindow) -> Result<(), String> {
    validate_history_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn page() -> Value {
        json!({
            "schemaVersion": 1,
            "coreGenerationId": "generation-a",
            "characterId": "sakura",
            "totalCount": 2,
            "entries": [
                {
                    "entryId": "entry-human",
                    "turnId": "turn-a",
                    "kind": "human",
                    "origin": "chat",
                    "createdAt": "2026-08-29T12:00:00+08:00",
                    "payload": {"text": "你好"}
                },
                {
                    "entryId": "entry-assistant",
                    "turnId": "turn-a",
                    "kind": "assistant",
                    "origin": "chat",
                    "createdAt": "2026-08-29T12:00:01+08:00",
                    "payload": {"segments": [{"text": "ただいま", "translation": "我回来了"}]}
                }
            ],
            "beforeCursor": null,
            "hasMore": false
        })
    }

    #[test]
    fn history_page_accepts_only_the_read_only_public_shape() {
        assert_eq!(validate_page(page()).unwrap().entries.len(), 2);

        let mut with_visual_id = page();
        with_visual_id["entries"][0]["payload"]["visualId"] = json!("private");
        assert_eq!(
            validate_page(with_visual_id),
            Err("HISTORY_RESPONSE_INVALID".to_string())
        );

        let mut mismatched_cursor = page();
        mismatched_cursor["hasMore"] = json!(true);
        assert_eq!(
            validate_page(mismatched_cursor),
            Err("HISTORY_RESPONSE_INVALID".to_string())
        );
    }
}
