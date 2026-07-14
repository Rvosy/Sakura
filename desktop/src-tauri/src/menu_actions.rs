use tauri::AppHandle;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PetMenuAction {
    Show,
    Hide,
    Settings,
    History,
    Diagnostics,
    Studio,
    Quit,
}

impl PetMenuAction {
    pub fn from_id(value: &str) -> Option<Self> {
        match value {
            "show" | "interact" => Some(Self::Show),
            "hide" => Some(Self::Hide),
            "settings" => Some(Self::Settings),
            "history" => Some(Self::History),
            "diagnostics" => Some(Self::Diagnostics),
            "studio" => Some(Self::Studio),
            "quit" => Some(Self::Quit),
            _ => None,
        }
    }
}

#[tauri::command]
pub async fn pet_menu_action(app: AppHandle, action: String) -> Result<(), String> {
    let action = PetMenuAction::from_id(action.trim())
        .ok_or_else(|| format!("未知桌宠菜单操作：{action}"))?;
    dispatch(app, action).await
}

pub async fn dispatch(app: AppHandle, action: PetMenuAction) -> Result<(), String> {
    match action {
        PetMenuAction::Show => crate::app_state::show_application_window(&app),
        PetMenuAction::Hide => crate::windows::hide_main_window(&app),
        PetMenuAction::Settings => crate::windows::open_settings_window(app.clone()).await?,
        PetMenuAction::History => crate::windows::open_history_window(app.clone()).await?,
        PetMenuAction::Diagnostics => crate::windows::open_diagnostics_window(app.clone()).await?,
        PetMenuAction::Studio => crate::windows::open_studio_window(app.clone()).await?,
        PetMenuAction::Quit => request_application_exit(&app),
    }
    Ok(())
}

pub fn request_application_exit(app: &AppHandle) {
    // app.exit 会进入 lib.rs 的 RunEvent::Exit/ExitRequested 分支，确保 Brain Host 先 shutdown。
    app.exit(0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_menu_action_ids_cover_webview_and_tray_routes() {
        assert_eq!(PetMenuAction::from_id("show"), Some(PetMenuAction::Show));
        assert_eq!(
            PetMenuAction::from_id("interact"),
            Some(PetMenuAction::Show)
        );
        assert_eq!(PetMenuAction::from_id("hide"), Some(PetMenuAction::Hide));
        assert_eq!(
            PetMenuAction::from_id("diagnostics"),
            Some(PetMenuAction::Diagnostics)
        );
        assert_eq!(PetMenuAction::from_id("quit"), Some(PetMenuAction::Quit));
        assert_eq!(PetMenuAction::from_id("unknown"), None);
    }
}
