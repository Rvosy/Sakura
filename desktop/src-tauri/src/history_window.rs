use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

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
    .title("Sakura 历史记录")
    .background_color(Color(248, 252, 254, 255))
    .inner_size(620.0, 680.0)
    .min_inner_size(480.0, 440.0)
    .resizable(true)
    .maximizable(true)
    .minimizable(true)
    .decorations(true)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .build()
    .map(|_| ())
    .map_err(|error| format!("HISTORY_WINDOW_CREATE_FAILED: {error}"))
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
