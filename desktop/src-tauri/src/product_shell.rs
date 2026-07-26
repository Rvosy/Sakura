use std::collections::BTreeMap;
use std::sync::Mutex;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::{
    AppHandle, Emitter, LogicalPosition, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

pub const SETTINGS_WINDOW_LABEL: &str = "settings";
pub const SETTINGS_CLOSE_REQUESTED_EVENT: &str = "sakura://settings-close-requested";
pub const SETTINGS_EXIT_REQUESTED_EVENT: &str = "sakura://settings-exit-requested";
pub const SETTINGS_EXIT_TIMEOUT_EVENT: &str = "sakura://settings-exit-timeout";
pub const PRODUCT_MENU_ERROR_EVENT: &str = "sakura://product-menu-error";

const MENU_TOGGLE_PET: &str = "sakura.pet.visibility.toggle";
const MENU_OPEN_SETTINGS: &str = "sakura.settings.open";
const MENU_EXIT_APP: &str = "sakura.app.exit";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProductMenuAction {
    TogglePet,
    OpenSettings,
    ExitApp,
}

impl ProductMenuAction {
    pub fn from_id(id: &str) -> Option<Self> {
        match id {
            MENU_TOGGLE_PET => Some(Self::TogglePet),
            MENU_OPEN_SETTINGS => Some(Self::OpenSettings),
            MENU_EXIT_APP => Some(Self::ExitApp),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct SettingsWindowSession {
    generation: u64,
    close_authorized: bool,
    exit_pending: bool,
    app_exit_authorized: bool,
}

#[derive(Default)]
pub struct ProductShellState {
    settings: Mutex<SettingsWindowSession>,
}

impl ProductShellState {
    fn next_generation(&self) -> Result<u64, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.generation = session.generation.saturating_add(1).max(1);
        session.close_authorized = false;
        Ok(session.generation)
    }

    fn generation(&self) -> Result<u64, String> {
        self.settings
            .lock()
            .map(|session| session.generation)
            .map_err(|_| "settings window state is unavailable".to_string())
    }

    pub fn authorize_close(&self) -> Result<(), String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.close_authorized = true;
        Ok(())
    }

    pub fn consume_close_authorization(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        let authorized = session.close_authorized;
        session.close_authorized = false;
        Ok(authorized)
    }

    pub fn begin_exit(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        if session.exit_pending {
            return Ok(false);
        }
        session.exit_pending = true;
        Ok(true)
    }

    pub fn resolve_exit(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        let pending = session.exit_pending;
        session.exit_pending = false;
        Ok(pending)
    }

    pub fn authorize_app_exit(&self) -> Result<(), String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.app_exit_authorized = true;
        Ok(())
    }

    pub fn consume_app_exit_authorization(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        let authorized = session.app_exit_authorized;
        session.app_exit_authorized = false;
        Ok(authorized)
    }

    pub fn exit_pending(&self) -> Result<bool, String> {
        self.settings
            .lock()
            .map(|session| session.exit_pending)
            .map_err(|_| "settings window state is unavailable".to_string())
    }

    pub fn window_destroyed(&self) -> Result<(), String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.close_authorized = false;
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsCapabilityManifest {
    pub schema_version: u32,
    pub window_generation: u64,
    pub available_sections: Vec<String>,
    pub read_only_sections: Vec<String>,
    pub unavailable_reasons: BTreeMap<String, String>,
}

const SETTINGS_SECTIONS: [&str; 10] = [
    "character",
    "appearance",
    "providers",
    "model",
    "voice",
    "memory",
    "interaction",
    "privacy",
    "tools",
    "plugins",
];

impl SettingsCapabilityManifest {
    fn shell_only(window_generation: u64) -> Self {
        let reason = "该设置能力尚未迁移到 Runtime v2";
        let mut unavailable_reasons = SETTINGS_SECTIONS
            .into_iter()
            .map(|section| (section.to_string(), reason.to_string()))
            .collect::<BTreeMap<_, _>>();
        unavailable_reasons.insert("system".to_string(), reason.to_string());
        Self {
            schema_version: 1,
            window_generation,
            available_sections: Vec::new(),
            read_only_sections: Vec::new(),
            unavailable_reasons,
        }
    }
}

fn validate_settings_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != SETTINGS_WINDOW_LABEL {
        return Err("SETTINGS_WINDOW_REQUIRED".to_string());
    }
    Ok(())
}

#[tauri::command]
pub fn settings_capability_manifest(
    window: WebviewWindow,
    state: tauri::State<'_, ProductShellState>,
) -> Result<SettingsCapabilityManifest, String> {
    validate_settings_window(&window)?;
    Ok(SettingsCapabilityManifest::shell_only(state.generation()?))
}

#[tauri::command]
pub fn resolve_settings_close(
    window: WebviewWindow,
    discard: bool,
    state: tauri::State<'_, ProductShellState>,
) -> Result<(), String> {
    validate_settings_window(&window)?;
    if !discard {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    state.authorize_close()?;
    window.close().map_err(|error| error.to_string())
}

pub fn show_or_focus_settings(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(SETTINGS_WINDOW_LABEL) {
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let state = app.state::<ProductShellState>();
    state.next_generation()?;
    WebviewWindowBuilder::new(
        app,
        SETTINGS_WINDOW_LABEL,
        WebviewUrl::App("settings/index.html".into()),
    )
    .title("Sakura 设置")
    .inner_size(1040.0, 760.0)
    .min_inner_size(900.0, 640.0)
    .resizable(true)
    .maximizable(true)
    .minimizable(true)
    .decorations(true)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .build()
    .map_err(|error| format!("SETTINGS_WINDOW_CREATE_FAILED: {error}"))?;
    Ok(())
}

pub fn show_product_menu(window: &WebviewWindow, popup_x: f64, popup_y: f64) -> Result<(), String> {
    if window.label() != "main"
        || !popup_x.is_finite()
        || !popup_y.is_finite()
        || popup_x < 0.0
        || popup_y < 0.0
    {
        return Err("PRODUCT_MENU_REQUEST_REJECTED".to_string());
    }
    let visible = window.is_visible().map_err(|error| error.to_string())?;
    let visibility = MenuItem::with_id(
        window,
        MENU_TOGGLE_PET,
        if visible {
            "隐藏桌宠"
        } else {
            "显示桌宠"
        },
        true,
        None::<&str>,
    )
    .map_err(|error| error.to_string())?;
    let settings = MenuItem::with_id(window, MENU_OPEN_SETTINGS, "设置…", true, None::<&str>)
        .map_err(|error| error.to_string())?;
    let exit = MenuItem::with_id(window, MENU_EXIT_APP, "退出", true, None::<&str>)
        .map_err(|error| error.to_string())?;
    let menu = Menu::with_items(window, &[&visibility, &settings, &exit])
        .map_err(|error| error.to_string())?;
    window
        .popup_menu_at(&menu, LogicalPosition::new(popup_x, popup_y))
        .map_err(|error| format!("PRODUCT_MENU_SHOW_FAILED: {error}"))
}

pub fn emit_product_menu_error(app: &AppHandle, error: impl ToString) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.emit(PRODUCT_MENU_ERROR_EVENT, error.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn product_menu_ids_are_a_closed_allowlist() {
        assert_eq!(
            ProductMenuAction::from_id(MENU_TOGGLE_PET),
            Some(ProductMenuAction::TogglePet)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_OPEN_SETTINGS),
            Some(ProductMenuAction::OpenSettings)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_EXIT_APP),
            Some(ProductMenuAction::ExitApp)
        );
        assert_eq!(ProductMenuAction::from_id("settings.open"), None);
        assert_eq!(ProductMenuAction::from_id("sakura.settings.save"), None);
    }

    #[test]
    fn settings_window_generation_is_monotonic_and_close_is_one_shot() {
        let state = ProductShellState::default();
        assert_eq!(state.next_generation().unwrap(), 1);
        assert!(!state.consume_close_authorization().unwrap());
        state.authorize_close().unwrap();
        assert!(state.consume_close_authorization().unwrap());
        assert!(!state.consume_close_authorization().unwrap());
        assert_eq!(state.next_generation().unwrap(), 2);
    }

    #[test]
    fn app_exit_coordination_deduplicates_and_can_be_cancelled() {
        let state = ProductShellState::default();
        assert!(state.begin_exit().unwrap());
        assert!(!state.begin_exit().unwrap());
        assert!(state.exit_pending().unwrap());
        assert!(state.resolve_exit().unwrap());
        assert!(!state.exit_pending().unwrap());
        assert!(!state.resolve_exit().unwrap());
        state.authorize_app_exit().unwrap();
        assert!(state.consume_app_exit_authorization().unwrap());
        assert!(!state.consume_app_exit_authorization().unwrap());
    }

    #[test]
    fn capability_shell_exposes_no_writable_section_or_secret_shaped_field() {
        let manifest = SettingsCapabilityManifest::shell_only(7);
        assert_eq!(manifest.schema_version, 1);
        assert_eq!(manifest.window_generation, 7);
        assert!(manifest.available_sections.is_empty());
        assert!(manifest.read_only_sections.is_empty());
        let json = serde_json::to_string(&manifest).unwrap().to_lowercase();
        for forbidden in [
            "password",
            "api_key",
            "apikey",
            "credential",
            "secret",
            "token",
        ] {
            assert!(!json.contains(forbidden), "{forbidden}");
        }
    }
}
