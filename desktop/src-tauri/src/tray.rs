use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::App;

const SHOW_ID: &str = "show";
const HIDE_ID: &str = "hide";
const INTERACT_ID: &str = "interact";
const SETTINGS_ID: &str = "settings";
const HISTORY_ID: &str = "history";
const STUDIO_ID: &str = "studio";
const QUIT_ID: &str = "quit";

pub fn build_tray(app: &mut App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, SHOW_ID, "显示 Sakura", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, HIDE_ID, "隐藏 Sakura", true, None::<&str>)?;
    let interact = MenuItem::with_id(app, INTERACT_ID, "恢复鼠标交互", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, SETTINGS_ID, "设置", true, None::<&str>)?;
    let history = MenuItem::with_id(app, HISTORY_ID, "对话历史", true, None::<&str>)?;
    let studio = MenuItem::with_id(app, STUDIO_ID, "角色工作室", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT_ID, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&show, &hide, &interact, &settings, &history, &studio, &quit],
    )?;

    TrayIconBuilder::with_id("sakura-main-tray")
        .tooltip("Sakura Assistant")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if matches!(event, tauri::tray::TrayIconEvent::DoubleClick { .. }) {
                crate::windows::show_main_window(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            SHOW_ID | INTERACT_ID => crate::windows::show_main_window(app),
            HIDE_ID => crate::windows::hide_main_window(app),
            SETTINGS_ID => spawn_secondary_window(crate::windows::open_settings_window, app),
            HISTORY_ID => spawn_secondary_window(crate::windows::open_history_window, app),
            STUDIO_ID => spawn_secondary_window(crate::windows::open_studio_window, app),
            QUIT_ID => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}

fn spawn_secondary_window<F, Fut>(open: F, app: &tauri::AppHandle)
where
    F: FnOnce(tauri::AppHandle) -> Fut + Send + 'static,
    Fut: std::future::Future<Output = Result<(), String>> + Send + 'static,
{
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let _ = open(app).await;
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
            STUDIO_ID,
            QUIT_ID,
        ];
        for (index, id) in ids.iter().enumerate() {
            assert!(!ids[index + 1..].contains(id));
        }
    }
}
