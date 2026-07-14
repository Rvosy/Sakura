use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::App;

const SHOW_ID: &str = "show";
const HIDE_ID: &str = "hide";
const INTERACT_ID: &str = "interact";
const SETTINGS_ID: &str = "settings";
const HISTORY_ID: &str = "history";
const DIAGNOSTICS_ID: &str = "diagnostics";
const STUDIO_ID: &str = "studio";
const QUIT_ID: &str = "quit";

pub fn build_tray(app: &mut App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, SHOW_ID, "显示 Sakura", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, HIDE_ID, "隐藏 Sakura", true, None::<&str>)?;
    let interact = MenuItem::with_id(app, INTERACT_ID, "恢复鼠标交互", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, SETTINGS_ID, "设置", true, None::<&str>)?;
    let history = MenuItem::with_id(app, HISTORY_ID, "对话历史", true, None::<&str>)?;
    let diagnostics =
        MenuItem::with_id(app, DIAGNOSTICS_ID, "运行日志 / 诊断", true, None::<&str>)?;
    let studio = MenuItem::with_id(app, STUDIO_ID, "角色工作室", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT_ID, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &show,
            &hide,
            &interact,
            &settings,
            &history,
            &diagnostics,
            &studio,
            &quit,
        ],
    )?;

    TrayIconBuilder::with_id("sakura-main-tray")
        .tooltip("Sakura Assistant")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if matches!(event, tauri::tray::TrayIconEvent::DoubleClick { .. }) {
                spawn_menu_action(crate::menu_actions::PetMenuAction::Show, tray.app_handle());
            }
        })
        .on_menu_event(|app, event| {
            if let Some(action) = crate::menu_actions::PetMenuAction::from_id(event.id().as_ref()) {
                spawn_menu_action(action, app);
            }
        })
        .build(app)?;

    Ok(())
}

fn spawn_menu_action(action: crate::menu_actions::PetMenuAction, app: &tauri::AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let _ = crate::menu_actions::dispatch(app, action).await;
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_command_ids_are_distinct() {
        let ids = [
            SHOW_ID,
            HIDE_ID,
            INTERACT_ID,
            SETTINGS_ID,
            HISTORY_ID,
            DIAGNOSTICS_ID,
            STUDIO_ID,
            QUIT_ID,
        ];
        for (index, id) in ids.iter().enumerate() {
            assert!(!ids[index + 1..].contains(id));
        }
    }
}
