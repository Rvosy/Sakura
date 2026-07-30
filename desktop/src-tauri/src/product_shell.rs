use std::collections::BTreeMap;
use std::sync::Mutex;

use serde::Serialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{App, AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const SETTINGS_WINDOW_LABEL: &str = "settings";
pub const SETTINGS_CLOSE_REQUESTED_EVENT: &str = "sakura://settings-close-requested";
pub const SETTINGS_EXIT_REQUESTED_EVENT: &str = "sakura://settings-exit-requested";
pub const SETTINGS_EXIT_TIMEOUT_EVENT: &str = "sakura://settings-exit-timeout";
pub const PRODUCT_MENU_ERROR_EVENT: &str = "sakura://product-menu-error";
pub const PRODUCT_TRAY_ID: &str = "sakura.product.tray";

const MENU_TOGGLE_PET: &str = "sakura.pet.visibility.toggle";
const MENU_OPEN_SETTINGS: &str = "sakura.settings.open";
const MENU_EXIT_APP: &str = "sakura.app.exit";
const PRODUCT_TRAY_ICON: &[u8] = include_bytes!("../icons/icon.png");
const PRODUCT_MENU_UNAVAILABLE_REASON: &str = "该功能尚未迁移到 Runtime v2";

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

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProductMenuCapabilityManifest {
    pub schema_version: u32,
    pub available_actions: Vec<String>,
    pub unavailable_reason: String,
}

pub fn product_menu_capability_manifest() -> ProductMenuCapabilityManifest {
    ProductMenuCapabilityManifest {
        schema_version: 1,
        available_actions: [MENU_TOGGLE_PET, MENU_OPEN_SETTINGS, MENU_EXIT_APP]
            .into_iter()
            .map(str::to_string)
            .collect(),
        unavailable_reason: PRODUCT_MENU_UNAVAILABLE_REASON.to_string(),
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
    tray_visibility: Mutex<Option<MenuItem<tauri::Wry>>>,
}

impl ProductShellState {
    fn install_tray_visibility(&self, item: MenuItem<tauri::Wry>) -> Result<(), String> {
        let mut visibility = self
            .tray_visibility
            .lock()
            .map_err(|_| "tray menu state is unavailable".to_string())?;
        *visibility = Some(item);
        Ok(())
    }

    fn sync_tray_visibility(&self, visible: bool) -> Result<(), String> {
        let item = self
            .tray_visibility
            .lock()
            .map_err(|_| "tray menu state is unavailable".to_string())?
            .as_ref()
            .cloned()
            .ok_or_else(|| "tray visibility action is unavailable".to_string())?;
        item.set_text(pet_visibility_action_text(visible))
            .map_err(|error| error.to_string())
    }

    fn next_generation(&self) -> Result<u64, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.generation = session.generation.saturating_add(1).max(1);
        session.close_authorized = false;
        Ok(session.generation)
    }

    pub fn generation(&self) -> Result<u64, String> {
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

fn pet_visibility_action_text(visible: bool) -> &'static str {
    if visible {
        "隐藏桌宠"
    } else {
        "显示桌宠"
    }
}

pub fn install_product_tray(app: &App, pet_visible: bool) -> Result<(), String> {
    let visibility = MenuItem::with_id(
        app,
        MENU_TOGGLE_PET,
        pet_visibility_action_text(pet_visible),
        true,
        None::<&str>,
    )
    .map_err(|error| error.to_string())?;
    let settings = MenuItem::with_id(app, MENU_OPEN_SETTINGS, "设置…", true, None::<&str>)
        .map_err(|error| error.to_string())?;
    let exit = MenuItem::with_id(app, MENU_EXIT_APP, "退出", true, None::<&str>)
        .map_err(|error| error.to_string())?;
    let first_separator = PredefinedMenuItem::separator(app).map_err(|error| error.to_string())?;
    let second_separator = PredefinedMenuItem::separator(app).map_err(|error| error.to_string())?;
    let menu = Menu::with_items(
        app,
        &[
            &visibility,
            &first_separator,
            &settings,
            &second_separator,
            &exit,
        ],
    )
    .map_err(|error| error.to_string())?;
    let icon = Image::from_bytes(PRODUCT_TRAY_ICON).map_err(|error| error.to_string())?;

    TrayIconBuilder::with_id(PRODUCT_TRAY_ID)
        .icon(icon)
        .tooltip("Sakura")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .build(app)
        .map_err(|error| error.to_string())?;
    app.state::<ProductShellState>()
        .install_tray_visibility(visibility)
}

pub fn sync_product_tray_visibility(app: &AppHandle, visible: bool) -> Result<(), String> {
    app.state::<ProductShellState>()
        .sync_tray_visibility(visible)
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsCapabilityManifest {
    pub schema_version: u32,
    pub window_generation: u64,
    pub sections: BTreeMap<String, SettingsSectionCapability>,
    pub unavailable_reasons: BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct SettingsSectionCapability {
    pub status: String,
    pub features: BTreeMap<String, String>,
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
            schema_version: 2,
            window_generation,
            sections: BTreeMap::new(),
            unavailable_reasons,
        }
    }

    fn character_appearance(window_generation: u64) -> Self {
        let mut manifest = Self::shell_only(window_generation);
        for section in ["character", "appearance"] {
            let feature = if section == "character" {
                "character.current"
            } else {
                "appearance.character"
            };
            manifest.sections.insert(
                section.to_string(),
                SettingsSectionCapability {
                    status: "available".to_string(),
                    features: BTreeMap::from([(feature.to_string(), "available".to_string())]),
                },
            );
            manifest.unavailable_reasons.remove(section);
        }
        manifest
    }

    fn provider_model(window_generation: u64) -> Self {
        let mut manifest = Self::character_appearance(window_generation);
        manifest.sections.insert(
            "providers".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([
                    ("providers.manage".to_string(), "available".to_string()),
                    ("providers.credentials".to_string(), "available".to_string()),
                    ("providers.list_models".to_string(), "available".to_string()),
                    (
                        "providers.test_connection".to_string(),
                        "available".to_string(),
                    ),
                ]),
            },
        );
        manifest.sections.insert(
            "model".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([
                    ("model.chat_slot".to_string(), "available".to_string()),
                    (
                        "model.vision_chat_slot".to_string(),
                        "available".to_string(),
                    ),
                    (
                        "model.memory_curation_slot".to_string(),
                        "unavailable".to_string(),
                    ),
                ]),
            },
        );
        for key in ["providers", "model"] {
            manifest.unavailable_reasons.remove(key);
        }
        manifest.unavailable_reasons.insert(
            "model.memory_curation_slot".to_string(),
            "记忆整理尚未迁移到 Runtime v2".to_string(),
        );
        manifest
    }
}

pub(crate) fn validate_settings_window(window: &WebviewWindow) -> Result<(), String> {
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
    Ok(SettingsCapabilityManifest::provider_model(
        state.generation()?,
    ))
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
    window.destroy().map_err(|error| error.to_string())
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
    fn product_menu_manifest_exposes_only_dispatchable_actions() {
        let manifest = product_menu_capability_manifest();
        assert_eq!(manifest.schema_version, 1);
        assert_eq!(
            manifest.available_actions,
            [MENU_TOGGLE_PET, MENU_OPEN_SETTINGS, MENU_EXIT_APP]
        );
        assert_eq!(manifest.unavailable_reason, PRODUCT_MENU_UNAVAILABLE_REASON);
        assert!(manifest
            .available_actions
            .iter()
            .all(|id| ProductMenuAction::from_id(id).is_some()));
    }

    #[test]
    fn pet_visibility_action_text_tracks_window_state() {
        assert_eq!(pet_visibility_action_text(true), "隐藏桌宠");
        assert_eq!(pet_visibility_action_text(false), "显示桌宠");
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
    fn capability_manifest_exposes_feature_scoped_settings_without_secrets() {
        let manifest = SettingsCapabilityManifest::provider_model(7);
        assert_eq!(manifest.schema_version, 2);
        assert_eq!(manifest.window_generation, 7);
        assert_eq!(
            manifest.sections["providers"].features["providers.credentials"],
            "available"
        );
        assert_eq!(
            manifest.sections["model"].features["model.memory_curation_slot"],
            "unavailable"
        );
        let json = serde_json::to_string(&manifest).unwrap().to_lowercase();
        for forbidden in ["password", "api_key", "apikey", "secret", "token"] {
            assert!(!json.contains(forbidden), "{forbidden}");
        }
    }
}
