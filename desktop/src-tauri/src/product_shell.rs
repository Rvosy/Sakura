use std::collections::BTreeMap;
use std::sync::Mutex;

use serde::Serialize;
use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::Color;
use tauri::{App, AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::{
    runtime_log::{RuntimeLogEvent, RuntimeLogService, Severity},
    ui_config::UiConfigRepository,
};

pub const SETTINGS_WINDOW_LABEL: &str = "settings";
pub const SETTINGS_CLOSE_REQUESTED_EVENT: &str = "sakura://settings-close-requested";
pub const SETTINGS_EXIT_REQUESTED_EVENT: &str = "sakura://settings-exit-requested";
pub const SETTINGS_EXIT_TIMEOUT_EVENT: &str = "sakura://settings-exit-timeout";
pub const PRODUCT_MENU_ERROR_EVENT: &str = "sakura://product-menu-error";
pub const PRODUCT_TRAY_ID: &str = "sakura.product.tray";

const MENU_TOGGLE_PET: &str = "sakura.pet.visibility.toggle";
const MENU_TOGGLE_SUBTITLE: &str = "sakura.chat.subtitle.toggle";
const MENU_TOGGLE_TOPMOST: &str = "sakura.pet.topmost.toggle";
const MENU_OPEN_HISTORY: &str = "sakura.history.open";
const MENU_OPEN_RUNTIME_LOG: &str = "sakura.runtime-log.open";
const MENU_OPEN_SETTINGS: &str = "sakura.settings.open";
const MENU_EXIT_APP: &str = "sakura.app.exit";
const PRODUCT_TRAY_ICON: &[u8] = include_bytes!("../icons/icon.png");
const PRODUCT_MENU_UNAVAILABLE_REASON: &str = "该功能尚未迁移到 Runtime v2";
const FIRST_RUN_GUIDE_NAMESPACE: &str = "FIRST_RUN_GUIDE";
const FIRST_RUN_GUIDE_FIELD: &str = "first_run_guide_completed";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProductMenuAction {
    TogglePet,
    ToggleSubtitle,
    ToggleTopmost,
    OpenHistory,
    OpenRuntimeLog,
    OpenSettings,
    ExitApp,
}

impl ProductMenuAction {
    pub fn from_id(id: &str) -> Option<Self> {
        match id {
            MENU_TOGGLE_PET => Some(Self::TogglePet),
            MENU_TOGGLE_SUBTITLE => Some(Self::ToggleSubtitle),
            MENU_TOGGLE_TOPMOST => Some(Self::ToggleTopmost),
            MENU_OPEN_HISTORY => Some(Self::OpenHistory),
            MENU_OPEN_RUNTIME_LOG => Some(Self::OpenRuntimeLog),
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
    pub checked_actions: Vec<String>,
    pub unavailable_reason: String,
}

pub fn product_menu_capability_manifest(
    chinese_subtitles: bool,
    pet_topmost: bool,
) -> ProductMenuCapabilityManifest {
    let mut checked_actions = Vec::new();
    if chinese_subtitles {
        checked_actions.push(MENU_TOGGLE_SUBTITLE.to_string());
    }
    if pet_topmost {
        checked_actions.push(MENU_TOGGLE_TOPMOST.to_string());
    }
    ProductMenuCapabilityManifest {
        schema_version: 1,
        available_actions: [
            MENU_TOGGLE_PET,
            MENU_TOGGLE_SUBTITLE,
            MENU_TOGGLE_TOPMOST,
            MENU_OPEN_HISTORY,
            MENU_OPEN_RUNTIME_LOG,
            MENU_OPEN_SETTINGS,
            MENU_EXIT_APP,
        ]
        .into_iter()
        .map(str::to_string)
        .collect(),
        checked_actions,
        unavailable_reason: PRODUCT_MENU_UNAVAILABLE_REASON.to_string(),
    }
}

const PET_TOPMOST_NAMESPACE: &str = "PET_TOPMOST";
const UI_SCHEMA_VERSION: u64 = 1;
const UI_DOMAIN: &str = "ui";

pub struct PetTopmostState {
    repository: UiConfigRepository,
    committed: Mutex<bool>,
}

impl PetTopmostState {
    pub fn new(repository: UiConfigRepository) -> Self {
        Self {
            repository,
            committed: Mutex::new(false),
        }
    }

    pub fn initialize(&self, window: &WebviewWindow) -> Result<bool, String> {
        self.initialize_with(|enabled| {
            window
                .set_always_on_top(enabled)
                .map_err(|_| "PET_TOPMOST_APPLY_FAILED".to_string())
        })
    }

    fn initialize_with(
        &self,
        mut set_native: impl FnMut(bool) -> Result<(), String>,
    ) -> Result<bool, String> {
        let enabled = topmost_from_document(&self.repository.load(PET_TOPMOST_NAMESPACE)?)?;
        set_native(enabled)?;
        *self
            .committed
            .lock()
            .map_err(|_| "PET_TOPMOST_STATE_UNAVAILABLE".to_string())? = enabled;
        Ok(enabled)
    }

    pub fn enabled(&self) -> Result<bool, String> {
        self.committed
            .lock()
            .map(|enabled| *enabled)
            .map_err(|_| "PET_TOPMOST_STATE_UNAVAILABLE".to_string())
    }

    pub fn toggle(&self, window: &WebviewWindow) -> Result<bool, String> {
        self.toggle_with(|enabled| {
            window
                .set_always_on_top(enabled)
                .map_err(|_| "PET_TOPMOST_APPLY_FAILED".to_string())
        })
    }

    fn toggle_with(
        &self,
        mut set_native: impl FnMut(bool) -> Result<(), String>,
    ) -> Result<bool, String> {
        let mut committed = self
            .committed
            .lock()
            .map_err(|_| "PET_TOPMOST_STATE_UNAVAILABLE".to_string())?;
        let previous = *committed;
        let next = !previous;
        set_native(next)?;
        if self.persist(next).is_err() {
            if set_native(previous).is_err() {
                // The requested native transition completed but its compensating transition did
                // not. Reflect the most likely visible state instead of showing a stale checkmark.
                *committed = next;
                return Err("PET_TOPMOST_ROLLBACK_FAILED".to_string());
            }
            return Err("PET_TOPMOST_SAVE_FAILED".to_string());
        }
        *committed = next;
        Ok(next)
    }

    fn persist(&self, enabled: bool) -> Result<(), String> {
        self.repository.update(PET_TOPMOST_NAMESPACE, |document| {
            validate_topmost_document(document)?;
            let settings = document
                .get_mut("settings")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| "PET_TOPMOST_DOCUMENT_INVALID".to_string())?;
            settings.insert("always_on_top".to_string(), Value::Bool(enabled));
            Ok(())
        })
    }
}

fn validate_topmost_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "PET_TOPMOST_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(UI_SCHEMA_VERSION) {
        return Err("PET_TOPMOST_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(UI_DOMAIN) {
        return Err("PET_TOPMOST_DOMAIN_INVALID".to_string());
    }
    root.get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "PET_TOPMOST_DOCUMENT_INVALID".to_string())?;
    Ok(())
}

fn topmost_from_document(document: &Value) -> Result<bool, String> {
    validate_topmost_document(document)?;
    match document["settings"].get("always_on_top") {
        None => Ok(false),
        Some(Value::Bool(enabled)) => Ok(*enabled),
        Some(_) => Err("PET_TOPMOST_FIELD_INVALID:always_on_top".to_string()),
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FirstRunGuideSnapshot {
    pub schema_version: u32,
    pub completed: bool,
}

pub struct FirstRunGuideState {
    repository: UiConfigRepository,
}

impl FirstRunGuideState {
    pub fn new(repository: UiConfigRepository) -> Self {
        Self { repository }
    }

    pub fn snapshot(&self) -> Result<FirstRunGuideSnapshot, String> {
        Ok(FirstRunGuideSnapshot {
            schema_version: 1,
            completed: first_run_guide_completed_from_document(
                &self.repository.load(FIRST_RUN_GUIDE_NAMESPACE)?,
            )?,
        })
    }

    pub fn complete(&self) -> Result<FirstRunGuideSnapshot, String> {
        self.repository
            .update(FIRST_RUN_GUIDE_NAMESPACE, |document| {
                validate_first_run_guide_document(document)?;
                let settings = document
                    .get_mut("settings")
                    .and_then(Value::as_object_mut)
                    .ok_or_else(|| "FIRST_RUN_GUIDE_DOCUMENT_INVALID".to_string())?;
                settings.insert(FIRST_RUN_GUIDE_FIELD.to_string(), Value::Bool(true));
                Ok(())
            })?;
        self.snapshot()
    }
}

fn validate_first_run_guide_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "FIRST_RUN_GUIDE_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(UI_SCHEMA_VERSION) {
        return Err("FIRST_RUN_GUIDE_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(UI_DOMAIN) {
        return Err("FIRST_RUN_GUIDE_DOMAIN_INVALID".to_string());
    }
    root.get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "FIRST_RUN_GUIDE_DOCUMENT_INVALID".to_string())?;
    Ok(())
}

fn first_run_guide_completed_from_document(document: &Value) -> Result<bool, String> {
    validate_first_run_guide_document(document)?;
    match document["settings"].get(FIRST_RUN_GUIDE_FIELD) {
        None => Ok(false),
        Some(Value::Bool(completed)) => Ok(*completed),
        Some(_) => Err(format!(
            "FIRST_RUN_GUIDE_FIELD_INVALID:{FIRST_RUN_GUIDE_FIELD}"
        )),
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct SettingsWindowSession {
    generation: u64,
    ready: bool,
    close_authorized: bool,
    closing: bool,
    reopen_after_close: bool,
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
        session.ready = false;
        session.close_authorized = false;
        session.closing = false;
        session.reopen_after_close = false;
        Ok(session.generation)
    }

    fn settings_ready(&self) -> Result<bool, String> {
        self.settings
            .lock()
            .map(|session| session.ready)
            .map_err(|_| "settings window state is unavailable".to_string())
    }

    fn mark_settings_ready(&self) -> Result<(), String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.ready = true;
        Ok(())
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
        session.closing = true;
        Ok(())
    }

    fn queue_reopen_if_closing(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        if !session.closing {
            return Ok(false);
        }
        session.reopen_after_close = true;
        Ok(true)
    }

    pub fn cancel_close(&self) -> Result<(), String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        session.close_authorized = false;
        session.closing = false;
        session.reopen_after_close = false;
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

    pub fn window_destroyed(&self) -> Result<bool, String> {
        let mut session = self
            .settings
            .lock()
            .map_err(|_| "settings window state is unavailable".to_string())?;
        let reopen = session.reopen_after_close && !session.exit_pending;
        session.ready = false;
        session.close_authorized = false;
        session.closing = false;
        session.reopen_after_close = false;
        Ok(reopen)
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
    let history = MenuItem::with_id(app, MENU_OPEN_HISTORY, "历史记录…", true, None::<&str>)
        .map_err(|error| error.to_string())?;
    let runtime_log =
        MenuItem::with_id(app, MENU_OPEN_RUNTIME_LOG, "运行日志…", true, None::<&str>)
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
            &history,
            &runtime_log,
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

const SETTINGS_SECTIONS: [&str; 11] = [
    "character",
    "appearance",
    "providers",
    "model",
    "voice",
    "memory",
    "interaction",
    "tools",
    "plugins",
    "system",
    "about",
];

impl SettingsCapabilityManifest {
    fn shell_only(window_generation: u64) -> Self {
        let reason = "该设置能力尚未迁移到 Runtime v2";
        let unavailable_reasons = SETTINGS_SECTIONS
            .into_iter()
            .map(|section| (section.to_string(), reason.to_string()))
            .collect::<BTreeMap<_, _>>();
        let mut manifest = Self {
            schema_version: 1,
            window_generation,
            sections: BTreeMap::new(),
            unavailable_reasons,
        };
        manifest.sections.insert(
            "about".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::new(),
            },
        );
        manifest.unavailable_reasons.remove("about");
        manifest
    }

    fn character_appearance(
        window_generation: u64,
        input_effect_support: crate::input_visual_effect::InputVisualEffectSupport,
    ) -> Self {
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
            .sections
            .get_mut("character")
            .expect("character capability was inserted")
            .features
            .insert("character.manage".to_string(), "available".to_string());
        let appearance = manifest
            .sections
            .get_mut("appearance")
            .expect("appearance capability was inserted");
        appearance.features.insert(
            "appearance.input_visual_effect".to_string(),
            "available".to_string(),
        );
        appearance.features.insert(
            "appearance.input_visual_effect.gaussian_blur".to_string(),
            if input_effect_support.gaussian_blur {
                "available".to_string()
            } else {
                "unavailable".to_string()
            },
        );
        appearance.features.insert(
            "appearance.input_visual_effect.liquid_glass".to_string(),
            if input_effect_support.liquid_glass {
                "available".to_string()
            } else {
                "unavailable".to_string()
            },
        );
        if !input_effect_support.gaussian_blur {
            manifest.unavailable_reasons.insert(
                "appearance.input_visual_effect.gaussian_blur".to_string(),
                if cfg!(windows) {
                    "当前 Windows 环境不支持高斯模糊；请右键桌宠打开“运行日志”查看原因".to_string()
                } else {
                    "实时桌面高斯仅支持 Windows 或 macOS".to_string()
                },
            );
        }
        if !input_effect_support.liquid_glass {
            manifest.unavailable_reasons.insert(
                "appearance.input_visual_effect.liquid_glass".to_string(),
                if cfg!(target_os = "macos") {
                    "需要 macOS 26 或更高版本".to_string()
                } else if cfg!(windows) {
                    "Windows 端液态玻璃暂未实现".to_string()
                } else {
                    "当前平台不支持液态玻璃".to_string()
                },
            );
        }
        manifest
    }

    fn provider_model(
        window_generation: u64,
        input_effect_support: crate::input_visual_effect::InputVisualEffectSupport,
    ) -> Self {
        let mut manifest = Self::character_appearance(window_generation, input_effect_support);
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
                features: BTreeMap::from([("model.slots".to_string(), "available".to_string())]),
            },
        );
        for key in ["providers", "model"] {
            manifest.unavailable_reasons.remove(key);
        }
        manifest.sections.insert(
            "memory".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([("memory.manage".to_string(), "available".to_string())]),
            },
        );
        manifest.unavailable_reasons.remove("memory");
        manifest.sections.insert(
            "tools".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([(
                    "tools.runtime_limits".to_string(),
                    "available".to_string(),
                )]),
            },
        );
        manifest.unavailable_reasons.remove("tools");
        manifest.sections.insert(
            "plugins".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([("plugins.manage".to_string(), "available".to_string())]),
            },
        );
        manifest.unavailable_reasons.remove("plugins");
        manifest.sections.insert(
            "voice".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([
                    ("voice.tts".to_string(), "available".to_string()),
                    ("voice.bundle".to_string(), "unavailable".to_string()),
                ]),
            },
        );
        manifest.unavailable_reasons.remove("voice");
        manifest.unavailable_reasons.insert(
            "voice.bundle".to_string(),
            "整合包安装将在 Provider 插件贡献迁移完成后重新开放".to_string(),
        );
        manifest.sections.insert(
            "interaction".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([
                    (
                        "chat.presentation_timing".to_string(),
                        "available".to_string(),
                    ),
                    (
                        "privacy.screen_awareness".to_string(),
                        "available".to_string(),
                    ),
                ]),
            },
        );
        manifest.unavailable_reasons.remove("interaction");
        manifest.sections.insert(
            "system".to_string(),
            SettingsSectionCapability {
                status: "available".to_string(),
                features: BTreeMap::from([
                    ("storage.tts_root".to_string(), "available".to_string()),
                    (
                        "storage.legacy_role_data_import".to_string(),
                        "available".to_string(),
                    ),
                ]),
            },
        );
        manifest.unavailable_reasons.remove("system");
        manifest.unavailable_reasons.insert(
            "chat.bubble_auto_hide".to_string(),
            "固定桌宠气泡必须保持常驻".to_string(),
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
    effects: tauri::State<'_, crate::input_visual_effect::InputVisualEffectState>,
) -> Result<SettingsCapabilityManifest, String> {
    validate_settings_window(&window)?;
    Ok(SettingsCapabilityManifest::provider_model(
        state.generation()?,
        effects.support(),
    ))
}

#[tauri::command]
pub fn first_run_guide_get(
    window: WebviewWindow,
    state: tauri::State<'_, FirstRunGuideState>,
) -> Result<FirstRunGuideSnapshot, String> {
    validate_settings_window(&window)?;
    state.snapshot()
}

#[tauri::command]
pub fn first_run_guide_complete(
    window: WebviewWindow,
    state: tauri::State<'_, FirstRunGuideState>,
    runtime_log: tauri::State<'_, RuntimeLogService>,
) -> Result<FirstRunGuideSnapshot, String> {
    validate_settings_window(&window)?;
    match state.complete() {
        Ok(snapshot) => {
            let _ = runtime_log.submit(RuntimeLogEvent::rust(
                Severity::Info,
                "first_run",
                "first_run.configuration.completed",
                "首次配置已完成",
            ));
            Ok(snapshot)
        }
        Err(error) => {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Error,
                    "first_run",
                    "first_run.configuration.failed",
                    "首次配置保存失败",
                )
                .attributes(serde_json::json!({
                    "code": "FIRST_RUN_CONFIGURATION_FAILED",
                    "diagnostic": error.clone(),
                    "error_type": "FirstRunConfigurationError",
                    "reason_code": "FIRST_RUN_CONFIGURATION_FAILED",
                    "stage": "configuration_save"
                })),
            );
            Err(error)
        }
    }
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
    if let Err(error) = window.destroy() {
        let _ = state.cancel_close();
        return Err(error.to_string());
    }
    Ok(())
}

pub fn show_or_focus_settings(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<ProductShellState>();
    if state.queue_reopen_if_closing()? {
        return Ok(());
    }
    if let Some(window) = app.get_webview_window(SETTINGS_WINDOW_LABEL) {
        if !state.settings_ready()? {
            return Ok(());
        }
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    state.next_generation()?;
    let completed = app.state::<FirstRunGuideState>().snapshot()?.completed;
    let window = WebviewWindowBuilder::new(
        app,
        SETTINGS_WINDOW_LABEL,
        WebviewUrl::App(settings_entrypoint(completed).into()),
    )
    .title("Sakura 设置")
    // WebView2 在交互式缩放时会落后一帧；用页面默认底色覆盖原生窗口，避免露出黑底。
    .background_color(Color(255, 246, 250, 255))
    // 主题快照应用完成前保持隐藏，避免默认粉色样式成为可见首帧。
    .visible(false)
    .inner_size(1200.0, 800.0)
    .min_inner_size(900.0, 640.0)
    .resizable(true)
    .maximizable(true)
    .minimizable(true)
    .decorations(true)
    .devtools(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .build()
    .map_err(|error| format!("SETTINGS_WINDOW_CREATE_FAILED: {error}"))?;
    if let Err(error) = bind_settings_webview_resize(&window) {
        let _ = window.destroy();
        return Err(error);
    }
    Ok(())
}

fn settings_entrypoint(first_run_guide_completed: bool) -> &'static str {
    if first_run_guide_completed {
        "settings/index.html"
    } else {
        "onboarding/index.html"
    }
}

#[tauri::command]
pub fn reveal_settings_window(
    window: WebviewWindow,
    state: tauri::State<'_, ProductShellState>,
) -> Result<(), String> {
    validate_settings_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    state.mark_settings_ready()
}

fn bind_settings_webview_resize(window: &WebviewWindow) -> Result<(), String> {
    let initial_size = window
        .inner_size()
        .map_err(|error| format!("SETTINGS_WINDOW_SIZE_FAILED: {error}"))?;
    window
        .as_ref()
        .set_size(initial_size)
        .map_err(|error| format!("SETTINGS_WEBVIEW_RESIZE_FAILED: {error}"))?;

    let webview = window.as_ref().clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::Resized(size) = event {
            // 事件属于该窗口自己的 WebView；窗口销毁期间的末尾事件可以安全忽略。
            let _ = webview.set_size(*size);
        }
    });
    Ok(())
}

pub(crate) fn set_settings_window_theme_background(
    window: &WebviewWindow,
    value: &str,
) -> Result<(), String> {
    window
        .set_background_color(Some(parse_theme_color(value)?))
        .map_err(|error| format!("SETTINGS_WINDOW_BACKGROUND_FAILED: {error}"))
}

fn parse_theme_color(value: &str) -> Result<Color, String> {
    let hex = value
        .strip_prefix('#')
        .filter(|hex| hex.len() == 6 && hex.bytes().all(|byte| byte.is_ascii_hexdigit()))
        .ok_or_else(|| "SETTINGS_WINDOW_BACKGROUND_INVALID".to_string())?;
    let channel = |range: std::ops::Range<usize>| {
        u8::from_str_radix(&hex[range], 16)
            .map_err(|_| "SETTINGS_WINDOW_BACKGROUND_INVALID".to_string())
    };
    Ok(Color(channel(0..2)?, channel(2..4)?, channel(4..6)?, 255))
}

pub fn emit_product_menu_error(app: &AppHandle, error: impl ToString) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.emit(PRODUCT_MENU_ERROR_EVENT, error.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct TopmostFixture(PathBuf);

    impl TopmostFixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "sakura-pet-topmost-{}-{nonce}-{}",
                std::process::id(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn config_path(&self) -> PathBuf {
            self.0.join("ui.json")
        }
    }

    impl Drop for TopmostFixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn settings_window_background_accepts_only_plain_hex_theme_colors() {
        assert_eq!(parse_theme_color("#caf2f2"), Ok(Color(202, 242, 242, 255)));
        assert_eq!(parse_theme_color("#E9FCF6"), Ok(Color(233, 252, 246, 255)));
        assert!(parse_theme_color("caf2f2").is_err());
        assert!(parse_theme_color("#12渐变").is_err());
    }

    #[test]
    fn product_menu_ids_are_a_closed_allowlist() {
        assert_eq!(
            ProductMenuAction::from_id(MENU_TOGGLE_PET),
            Some(ProductMenuAction::TogglePet)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_TOGGLE_SUBTITLE),
            Some(ProductMenuAction::ToggleSubtitle)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_TOGGLE_TOPMOST),
            Some(ProductMenuAction::ToggleTopmost)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_OPEN_HISTORY),
            Some(ProductMenuAction::OpenHistory)
        );
        assert_eq!(
            ProductMenuAction::from_id(MENU_OPEN_RUNTIME_LOG),
            Some(ProductMenuAction::OpenRuntimeLog)
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
        let manifest = product_menu_capability_manifest(true, true);
        assert_eq!(manifest.schema_version, 1);
        assert_eq!(
            manifest.available_actions,
            [
                MENU_TOGGLE_PET,
                MENU_TOGGLE_SUBTITLE,
                MENU_TOGGLE_TOPMOST,
                MENU_OPEN_HISTORY,
                MENU_OPEN_RUNTIME_LOG,
                MENU_OPEN_SETTINGS,
                MENU_EXIT_APP
            ]
        );
        assert_eq!(
            manifest.checked_actions,
            [MENU_TOGGLE_SUBTITLE, MENU_TOGGLE_TOPMOST]
        );
        assert_eq!(manifest.unavailable_reason, PRODUCT_MENU_UNAVAILABLE_REASON);
        assert!(manifest
            .available_actions
            .iter()
            .all(|id| ProductMenuAction::from_id(id).is_some()));
    }

    #[test]
    fn pet_topmost_restores_toggles_and_preserves_other_ui_settings() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"always_on_top":true,"future":42}}"#,
        )
        .unwrap();
        let state = PetTopmostState::new(UiConfigRepository::new(path.clone()));
        let mut native = Vec::new();

        assert_eq!(
            state.initialize_with(|enabled| {
                native.push(enabled);
                Ok(())
            }),
            Ok(true)
        );
        assert_eq!(state.enabled(), Ok(true));
        assert_eq!(
            state.toggle_with(|enabled| {
                native.push(enabled);
                Ok(())
            }),
            Ok(false)
        );
        assert_eq!(native, [true, false]);
        assert_eq!(state.enabled(), Ok(false));
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["always_on_top"], false);
        assert_eq!(document["settings"]["future"], 42);
    }

    #[test]
    fn pet_topmost_defaults_off_and_persists_the_first_toggle() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        let state = PetTopmostState::new(UiConfigRepository::new(path.clone()));
        let mut native = Vec::new();

        assert_eq!(
            state.initialize_with(|enabled| {
                native.push(enabled);
                Ok(())
            }),
            Ok(false)
        );
        assert_eq!(
            state.toggle_with(|enabled| {
                native.push(enabled);
                Ok(())
            }),
            Ok(true)
        );
        assert_eq!(native, [false, true]);
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["always_on_top"], true);
    }

    #[test]
    fn pet_topmost_save_failure_restores_native_and_committed_state() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        fs::write(&path, b"not json").unwrap();
        let state = PetTopmostState::new(UiConfigRepository::new(path.clone()));
        let mut native = Vec::new();

        assert_eq!(
            state.toggle_with(|enabled| {
                native.push(enabled);
                Ok(())
            }),
            Err("PET_TOPMOST_SAVE_FAILED".to_string())
        );
        assert_eq!(native, [true, false]);
        assert_eq!(state.enabled(), Ok(false));
        assert_eq!(fs::read(path).unwrap(), b"not json");
    }

    #[test]
    fn pet_topmost_native_failure_does_not_write_or_change_state() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        let state = PetTopmostState::new(UiConfigRepository::new(path.clone()));

        assert_eq!(
            state.toggle_with(|_| Err("PET_TOPMOST_APPLY_FAILED".to_string())),
            Err("PET_TOPMOST_APPLY_FAILED".to_string())
        );
        assert_eq!(state.enabled(), Ok(false));
        assert!(!path.exists());
    }

    #[test]
    fn first_run_guide_defaults_to_welcome_and_completion_preserves_ui_settings() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"future":{"kept":true}}}"#,
        )
        .unwrap();
        let state = FirstRunGuideState::new(UiConfigRepository::new(path.clone()));

        assert_eq!(
            state.snapshot().unwrap(),
            FirstRunGuideSnapshot {
                schema_version: 1,
                completed: false,
            }
        );
        assert_eq!(settings_entrypoint(false), "onboarding/index.html");

        assert_eq!(
            state.complete().unwrap(),
            FirstRunGuideSnapshot {
                schema_version: 1,
                completed: true,
            }
        );
        assert_eq!(settings_entrypoint(true), "settings/index.html");
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"][FIRST_RUN_GUIDE_FIELD], true);
        assert_eq!(document["settings"]["future"]["kept"], true);
    }

    #[test]
    fn first_run_guide_rejects_invalid_persisted_field_without_rewriting() {
        let fixture = TopmostFixture::new();
        let path = fixture.config_path();
        let before =
            br#"{"schema_version":1,"domain":"ui","settings":{"first_run_guide_completed":"yes"}}"#;
        fs::write(&path, before).unwrap();
        let state = FirstRunGuideState::new(UiConfigRepository::new(path.clone()));

        assert_eq!(
            state.snapshot().unwrap_err(),
            "FIRST_RUN_GUIDE_FIELD_INVALID:first_run_guide_completed"
        );
        assert_eq!(fs::read(path).unwrap(), before);
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
        assert!(!state.settings_ready().unwrap());
        state.mark_settings_ready().unwrap();
        assert!(state.settings_ready().unwrap());
        assert!(!state.queue_reopen_if_closing().unwrap());
        assert!(!state.consume_close_authorization().unwrap());
        state.authorize_close().unwrap();
        assert!(state.queue_reopen_if_closing().unwrap());
        assert!(state.consume_close_authorization().unwrap());
        assert!(!state.consume_close_authorization().unwrap());
        assert!(state.window_destroyed().unwrap());
        assert_eq!(state.next_generation().unwrap(), 2);
        assert!(!state.settings_ready().unwrap());
        assert!(!state.queue_reopen_if_closing().unwrap());
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
    fn app_exit_never_reopens_a_settings_window_queued_during_destruction() {
        let state = ProductShellState::default();
        assert_eq!(state.next_generation().unwrap(), 1);
        assert!(state.begin_exit().unwrap());
        state.authorize_close().unwrap();
        assert!(state.queue_reopen_if_closing().unwrap());
        assert!(!state.window_destroyed().unwrap());
    }

    #[test]
    fn capability_manifest_exposes_feature_scoped_settings_without_secrets() {
        let manifest = SettingsCapabilityManifest::provider_model(
            7,
            crate::input_visual_effect::InputVisualEffectSupport::new(true, true),
        );
        assert_eq!(manifest.schema_version, 1);
        assert_eq!(manifest.window_generation, 7);
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect"],
            "available"
        );
        assert_eq!(
            manifest.sections["appearance"].features
                ["appearance.input_visual_effect.gaussian_blur"],
            "available"
        );
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect.liquid_glass"],
            "available"
        );
        assert_eq!(
            manifest.sections["providers"].features["providers.credentials"],
            "available"
        );
        assert_eq!(
            manifest.sections["model"].features["model.slots"],
            "available"
        );
        assert!(!manifest.sections["model"]
            .features
            .contains_key("model.memory_curation_slot"));
        assert_eq!(
            manifest.sections["memory"].features["memory.manage"],
            "available"
        );
        assert_eq!(manifest.sections["memory"].status, "available");
        assert!(!manifest.unavailable_reasons.contains_key("memory"));
        assert_eq!(
            manifest.sections["system"].features["storage.tts_root"],
            "available"
        );
        assert_eq!(manifest.sections["about"].status, "available");
        assert!(!manifest.sections.contains_key("storage"));
        assert_eq!(
            manifest.sections["interaction"].features["chat.presentation_timing"],
            "available"
        );
        assert_eq!(
            manifest.sections["interaction"].features["privacy.screen_awareness"],
            "available"
        );
        assert!(!manifest.sections.contains_key("privacy"));
        assert_eq!(
            manifest.sections["tools"].features["tools.runtime_limits"],
            "available"
        );
        assert_eq!(manifest.sections["tools"].features.len(), 1);
        let json = serde_json::to_string(&manifest).unwrap().to_lowercase();
        for forbidden in ["password", "api_key", "apikey", "secret", "token"] {
            assert!(!json.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn unsupported_input_glass_capability_is_feature_scoped_and_explained() {
        let manifest = SettingsCapabilityManifest::provider_model(
            9,
            crate::input_visual_effect::InputVisualEffectSupport::new(false, false),
        );
        assert_eq!(manifest.sections["appearance"].status, "available");
        assert_eq!(
            manifest.sections["appearance"].features["appearance.character"],
            "available"
        );
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect"],
            "available"
        );
        assert!(!manifest
            .unavailable_reasons
            .contains_key("appearance.input_visual_effect"));
        assert_eq!(
            manifest.sections["appearance"].features
                ["appearance.input_visual_effect.gaussian_blur"],
            "unavailable"
        );
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect.liquid_glass"],
            "unavailable"
        );
    }

    #[test]
    fn gaussian_can_remain_available_when_liquid_is_locked() {
        let manifest = SettingsCapabilityManifest::provider_model(
            11,
            crate::input_visual_effect::InputVisualEffectSupport::new(true, false),
        );
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect"],
            "available"
        );
        assert_eq!(
            manifest.sections["appearance"].features
                ["appearance.input_visual_effect.gaussian_blur"],
            "available"
        );
        assert_eq!(
            manifest.sections["appearance"].features["appearance.input_visual_effect.liquid_glass"],
            "unavailable"
        );
        let expected_reason = if cfg!(target_os = "macos") {
            "需要 macOS 26 或更高版本"
        } else if cfg!(windows) {
            "Windows 端液态玻璃暂未实现"
        } else {
            "当前平台不支持液态玻璃"
        };
        assert_eq!(
            manifest.unavailable_reasons["appearance.input_visual_effect.liquid_glass"],
            expected_reason
        );
    }
}
