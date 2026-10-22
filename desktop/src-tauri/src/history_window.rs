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

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryEntry {
    pub entry_id: String,
    pub turn_id: String,
    pub kind: String,
    pub origin: String,
    pub created_at: String,
    pub payload: Value,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryPage {
    pub schema_version: u32,
    pub core_generation_id: String,
    pub character_id: String,
    pub total_count: u64,
    pub entries: Vec<HistoryEntry>,
    pub before_cursor: Option<String>,
    pub after_cursor: Option<String>,
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
    after_cursor: Option<String>,
    from_date: Option<String>,
    from_entry_id: Option<String>,
    query: Option<String>,
}

async fn request_history_page(
    handle: shell_lifecycle::ShellLifecycleHandle,
    request: &HistoryPageRequest,
) -> Result<HistoryPage, String> {
    let response = dispatch_settings_request(
        handle,
        None,
        "ui.history.page",
        json!({
            "expectedCharacterId": request.character_id,
            "beforeCursor": request.before_cursor,
            "afterCursor": request.after_cursor,
            "fromDate": request.from_date,
            "fromEntryId": request.from_entry_id,
            "query": request.query,
            "limit": HISTORY_PAGE_LIMIT,
        }),
    )
    .await?;
    serde_json::from_value(settings_response_payload(response)?).map_err(|source_error| {
        crate::runtime_log::diagnostic_error("HISTORY_RESPONSE_INVALID", source_error)
    })
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
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    if presentation.presentation.generation_id != request.core_generation_id
        || presentation.presentation.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    let page = request_history_page(
        settings_core_handle(&lifecycle)?,
        &request,
    )
    .await?;
    if page.core_generation_id != request.core_generation_id
        || page.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    Ok(page)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct HistoryRecordRequest {
    core_generation_id: String,
    character_id: String,
    entry_id: String,
}

#[tauri::command]
pub(crate) async fn history_open_record(
    window: WebviewWindow,
    request: HistoryRecordRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
) -> Result<Value, String> {
    validate_history_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    if presentation.presentation.generation_id != request.core_generation_id
        || presentation.presentation.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    let response = dispatch_settings_request(
        settings_core_handle(&lifecycle)?,
        None,
        "ui.history.open_record",
        json!({
            "expectedCharacterId": request.character_id, "entryId": request.entry_id,
        }),
    )
    .await?;
    settings_response_payload(response)
}

#[tauri::command]
pub(crate) fn close_history_window(window: WebviewWindow) -> Result<(), String> {
    validate_history_window(&window)?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) async fn history_dates(
    window: WebviewWindow,
    payload: Value,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    validate_history_window(&window)?;
    let response = dispatch_settings_request(
        settings_core_handle(&lifecycle)?,
        None,
        "ui.history.dates",
        payload,
    )
    .await?;
    settings_response_payload(response)
}

#[tauri::command]
pub(crate) async fn history_transfer(
    window: WebviewWindow,
    payload: Value,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    validate_history_window(&window)?;
    let response = dispatch_settings_request(
        settings_core_handle(&lifecycle)?,
        None,
        "ui.history.transfer",
        payload,
    )
    .await?;
    settings_response_payload(response)
}

#[tauri::command]
pub(crate) fn reveal_history_window(window: WebviewWindow) -> Result<(), String> {
    validate_history_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}
