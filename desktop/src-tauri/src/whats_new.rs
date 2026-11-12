use std::collections::BTreeMap;

use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::{
    character_appearance, character_presentation,
    runtime_log::{RuntimeLogEvent, RuntimeLogService, Severity},
    ui_config::UiConfigRepository,
};

pub const WINDOW_LABEL: &str = "whats-new";
const CURRENT: &str = env!("CARGO_PKG_VERSION");
const SEEN_FIELD: &str = "whats_new_seen_version";

#[derive(Clone, Deserialize, Serialize)]
pub struct Item {
    r#type: String,
    text: String,
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Entry {
    version: String,
    items: Vec<Item>,
}

#[derive(Deserialize)]
struct Notes {
    versions: Vec<Entry>,
}

fn version(value: &str) -> Result<Version, String> {
    Version::parse(value).map_err(|error| format!("无效的更新摘要版本 {value}: {error}"))
}

fn notes() -> Result<Vec<Entry>, String> {
    serde_json::from_str::<Notes>(include_str!("../../frontend/data/whats-new.json"))
        .map(|notes| notes.versions)
        .map_err(|error| error.to_string())
}

fn seen_version(document: &Value) -> Result<Option<Version>, String> {
    match document["settings"].get(SEEN_FIELD) {
        None => Ok(None),
        Some(Value::String(value)) => version(value).map(Some),
        Some(_) => Err("更新摘要的已读版本必须为版本号字符串".into()),
    }
}

pub(crate) fn mark_seen(document: &mut Value, current: &str) -> Result<(), String> {
    let next = version(current)?;
    if let Some(seen) = seen_version(document)? {
        if seen.cmp_precedence(&next).is_ge() {
            return Ok(());
        }
    }
    let settings = document
        .get_mut("settings")
        .and_then(Value::as_object_mut)
        .ok_or("更新摘要配置缺少 settings 对象")?;
    settings.insert(SEEN_FIELD.into(), Value::String(current.into()));
    Ok(())
}

fn select_entries(
    entries: Vec<Entry>,
    seen: Option<&Version>,
    current: &Version,
    startup: bool,
) -> Result<Vec<Entry>, String> {
    let mut selected = Vec::new();
    for entry in entries {
        let candidate = version(&entry.version)?;
        let in_range = !startup
            || match seen {
                Some(seen) => candidate.cmp_precedence(seen).is_gt(),
                None => candidate.cmp_precedence(current).is_eq(),
            };
        if !entry.items.is_empty() && candidate.cmp_precedence(current).is_le() && in_range {
            selected.push((candidate, entry));
        }
    }
    selected.sort_by(|a, b| b.0.cmp_precedence(&a.0));
    Ok(selected.into_iter().map(|(_, entry)| entry).collect())
}

fn validate_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == WINDOW_LABEL {
        Ok(())
    } else {
        Err("WHATS_NEW_WINDOW_REQUIRED".into())
    }
}

fn show_or_focus(app: &AppHandle, startup: bool) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        if window.is_visible().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
            window.set_focus().map_err(|error| error.to_string())?;
        }
        return Ok(());
    }
    let path = if startup {
        "whats-new/index.html?startup=1"
    } else {
        "whats-new/index.html"
    };
    WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::App(path.into()))
        .title("Sakura 更新摘要")
        .decorations(false)
        .visible(false)
        .inner_size(520.0, 380.0)
        .resizable(false)
        .maximizable(false)
        .devtools(false)
        .center()
        .build()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

// WebView2 window creation must run after the invoke callback returns.
pub(crate) async fn open(app: AppHandle, startup: bool) -> Result<(), String> {
    let action_app = app.clone();
    let (send, receive) = tokio::sync::oneshot::channel();
    app.run_on_main_thread(move || {
        let _ = send.send(show_or_focus(&action_app, startup));
    })
    .map_err(|error| error.to_string())?;
    receive.await.map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) async fn startup_whats_new(
    window: WebviewWindow,
    repository: State<'_, UiConfigRepository>,
    guide: State<'_, crate::product_shell::FirstRunGuideState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".into());
    }
    if !guide.snapshot()?.completed {
        return Ok(());
    }
    let document = repository.load("WHATS_NEW")?;
    let seen = seen_version(&document)?;
    let current = version(CURRENT)?;
    if seen
        .as_ref()
        .is_some_and(|seen| seen.cmp_precedence(&current).is_ge())
    {
        return Ok(());
    }
    if select_entries(notes()?, seen.as_ref(), &current, true)?.is_empty() {
        return repository.update("WHATS_NEW", |document| mark_seen(document, CURRENT));
    }
    open(window.app_handle().clone(), true).await
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Bootstrap {
    version: &'static str,
    seen_version: Option<String>,
    entries: Vec<Entry>,
    theme_tokens: BTreeMap<String, String>,
}

#[tauri::command]
pub(crate) fn whats_new_bootstrap(
    window: WebviewWindow,
    startup: bool,
    repository: State<'_, UiConfigRepository>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<Bootstrap, String> {
    validate_window(&window)?;
    let seen = if startup {
        seen_version(&repository.load("WHATS_NEW")?)?
    } else {
        None
    };
    let theme_tokens = resources
        .active_presentation()
        .ok()
        .flatten()
        .and_then(|presentation| appearance.current(&presentation).ok())
        .map(|publication| publication.values.theme_tokens)
        .unwrap_or_else(crate::runtime_log_window::fallback_theme_tokens);
    let entries = select_entries(notes()?, seen.as_ref(), &version(CURRENT)?, startup)?;
    Ok(Bootstrap {
        version: CURRENT,
        seen_version: seen.map(|value| value.to_string()),
        entries,
        theme_tokens,
    })
}

#[tauri::command]
pub(crate) fn resize_whats_new(window: WebviewWindow, height: f64) -> Result<(), String> {
    validate_window(&window)?;
    let mut height = height.min(620.0);
    if let Some(monitor) = window
        .current_monitor()
        .map_err(|error| error.to_string())?
    {
        let inner = window.inner_size().map_err(|error| error.to_string())?;
        let outer = window.outer_size().map_err(|error| error.to_string())?;
        let decorations = outer.height.saturating_sub(inner.height);
        height = height.min(
            monitor.work_area().size.height.saturating_sub(decorations) as f64
                / monitor.scale_factor(),
        );
    }
    window
        .set_size(tauri::LogicalSize::new(520.0, height))
        .map_err(|error| error.to_string())?;
    if !window.is_visible().map_err(|error| error.to_string())? {
        window.center().map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub(crate) fn reveal_whats_new(window: WebviewWindow) -> Result<(), String> {
    validate_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn close_whats_new(
    window: WebviewWindow,
    acknowledge: bool,
    repository: State<'_, UiConfigRepository>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<(), String> {
    validate_window(&window)?;
    if acknowledge {
        if let Err(error) = repository.update("WHATS_NEW", |document| mark_seen(document, CURRENT))
        {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Warning,
                    "app",
                    "whats_new.acknowledge.failed",
                    "更新摘要已读记录保存失败",
                )
                .attributes(serde_json::json!({"diagnostic": error})),
            );
        }
    }
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn whats_new_open_details(
    window: WebviewWindow,
    version: Option<String>,
) -> Result<(), String> {
    validate_window(&window)?;
    let base = format!("{}/releases", crate::update_settings::REPOSITORY_URL);
    let url = match version {
        Some(value) => {
            if !notes()?.iter().any(|entry| entry.version == value) {
                return Err("未知的更新摘要版本".into());
            }
            format!("{base}/tag/v{value}")
        }
        None => base,
    };
    crate::update_settings::open_https_url(&url, "WHATS_NEW_DETAILS_OPEN_FAILED")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn entries() -> Vec<Entry> {
        ["1.3.4", "1.3.5", "1.3.6-rc.1", "1.3.6", "1.3.10", "1.4.0"]
            .into_iter()
            .map(|v| Entry {
                version: v.into(),
                items: vec![Item {
                    r#type: "new".into(),
                    text: "更新".into(),
                }],
            })
            .collect()
    }
    fn selected(seen: Option<&str>, current: &str, startup: bool) -> Vec<String> {
        let seen = seen.map(|value| version(value).unwrap());
        select_entries(
            entries(),
            seen.as_ref(),
            &version(current).unwrap(),
            startup,
        )
        .unwrap()
        .into_iter()
        .map(|e| e.version)
        .collect()
    }
    #[test]
    fn upgrade_range_orders_semver_and_excludes_future_versions() {
        assert_eq!(
            selected(Some("1.3.5"), "1.3.10", true),
            ["1.3.10", "1.3.6", "1.3.6-rc.1"]
        );
        assert_eq!(selected(None, "1.3.6", true), ["1.3.6"]);
        assert!(selected(Some("1.3.10"), "1.3.6", true).is_empty());
        assert_eq!(selected(Some("1.3.10"), "1.3.5", false), ["1.3.5", "1.3.4"]);
    }
    #[test]
    fn empty_releases_do_not_display() {
        let mut entries = entries();
        entries[3].items.clear();
        assert!(select_entries(
            entries,
            Some(&version("1.3.6-rc.1").unwrap()),
            &version("1.3.6").unwrap(),
            true
        )
        .unwrap()
        .is_empty());
    }
    #[test]
    fn acknowledgement_preserves_other_settings_and_never_moves_backwards() {
        let mut document =
            json!({"settings": {"theme": "custom", "update": {"auto_check_enabled": false}}});
        mark_seen(&mut document, "1.3.6").unwrap();
        mark_seen(&mut document, "1.3.5").unwrap();
        assert_eq!(
            document,
            json!({"settings": {"theme": "custom", "update": {"auto_check_enabled": false}, "whats_new_seen_version": "1.3.6"}})
        );
        mark_seen(&mut document, "1.3.10").unwrap();
        assert_eq!(
            seen_version(&document).unwrap(),
            Some(version("1.3.10").unwrap())
        );
    }
}
