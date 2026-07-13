use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::App;

const SHOW_ID: &str = "show";
const HIDE_ID: &str = "hide";
const INTERACT_ID: &str = "interact";
const QUIT_ID: &str = "quit";

pub fn build_tray(app: &mut App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, SHOW_ID, "显示 Sakura", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, HIDE_ID, "隐藏 Sakura", true, None::<&str>)?;
    let interact = MenuItem::with_id(app, INTERACT_ID, "恢复鼠标交互", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT_ID, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &hide, &interact, &quit])?;

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
            QUIT_ID => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_command_ids_are_distinct() {
        let ids = [SHOW_ID, HIDE_ID, INTERACT_ID, QUIT_ID];
        for (index, id) in ids.iter().enumerate() {
            assert!(!ids[index + 1..].contains(id));
        }
    }
}
