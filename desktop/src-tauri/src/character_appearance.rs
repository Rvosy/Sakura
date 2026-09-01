use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Mutex,
};

#[cfg(test)]
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::{character_presentation::CharacterPresentation, ui_config::UiConfigRepository};

pub const APPEARANCE_CHANGED_EVENT: &str = "sakura://character-appearance-changed";
const SCHEMA_VERSION: u64 = 1;
const DOMAIN: &str = "ui";
const PORTRAIT_SCALE_MIN: u16 = 50;
const PORTRAIT_SCALE_MAX: u16 = 150;
const CONTROL_PANEL_WIDTH_MIN: u16 = 420;
const CONTROL_PANEL_WIDTH_MAX: u16 = 860;
const BUBBLE_MAX_HEIGHT_MIN: u16 = 96;
const BUBBLE_MAX_HEIGHT_MAX: u16 = 260;
const CONTROL_PANEL_VERTICAL_OFFSET_MIN: i16 = -200;
const CONTROL_PANEL_VERTICAL_OFFSET_MAX: i16 = 200;
const INPUT_BAR_OFFSET_MIN: u16 = 0;
const INPUT_BAR_OFFSET_MAX: u16 = 200;
const DEFAULT_VISUAL_EFFECT_MODE: InputVisualEffectMode = InputVisualEffectMode::GaussianBlur;
const THEME_TOKENS: [(&str, &str); 11] = [
    ("primary", "primary_color"),
    ("primaryHover", "primary_hover_color"),
    ("accent", "accent_color"),
    ("text", "text_color"),
    ("secondaryText", "secondary_text_color"),
    ("mutedText", "muted_text_color"),
    ("pageBackground", "page_background_color"),
    ("panelBackground", "panel_background_color"),
    ("inputBackground", "input_background_color"),
    ("bubbleBackground", "bubble_background_color"),
    ("border", "border_color"),
];

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InputVisualEffectMode {
    Solid,
    GaussianBlur,
    LiquidGlass,
}

impl InputVisualEffectMode {
    fn from_disk(value: &str) -> Result<Self, String> {
        match value {
            "solid" => Ok(Self::Solid),
            "gaussian_blur" => Ok(Self::GaussianBlur),
            "liquid_glass" => Ok(Self::LiquidGlass),
            _ => Err("APPEARANCE_FIELD_INVALID:visual_effect_mode".to_string()),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AppearanceValues {
    pub portrait_scale_percent: u16,
    pub control_panel_width: u16,
    pub bubble_max_height: u16,
    pub bubble_auto_expand: bool,
    pub control_panel_vertical_offset: i16,
    pub input_bar_offset: u16,
    pub speech_font_size: u16,
    pub name_font_size: u16,
    pub input_font_size: u16,
    pub visual_effect_mode: InputVisualEffectMode,
    pub theme_tokens: BTreeMap<String, String>,
}

impl AppearanceValues {
    pub fn validate(&self) -> Result<(), String> {
        bounded(
            self.portrait_scale_percent,
            PORTRAIT_SCALE_MIN,
            PORTRAIT_SCALE_MAX,
        )
        .then_some(())
        .ok_or_else(|| "APPEARANCE_FIELD_INVALID:portraitScalePercent".to_string())?;
        for (name, value, minimum, maximum) in [
            (
                "controlPanelWidth",
                self.control_panel_width,
                CONTROL_PANEL_WIDTH_MIN,
                CONTROL_PANEL_WIDTH_MAX,
            ),
            (
                "bubbleMaxHeight",
                self.bubble_max_height,
                BUBBLE_MAX_HEIGHT_MIN,
                BUBBLE_MAX_HEIGHT_MAX,
            ),
            (
                "inputBarOffset",
                self.input_bar_offset,
                INPUT_BAR_OFFSET_MIN,
                INPUT_BAR_OFFSET_MAX,
            ),
        ] {
            if !bounded(value, minimum, maximum) {
                return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
            }
        }
        if !(CONTROL_PANEL_VERTICAL_OFFSET_MIN..=CONTROL_PANEL_VERTICAL_OFFSET_MAX)
            .contains(&self.control_panel_vertical_offset)
        {
            return Err("APPEARANCE_FIELD_INVALID:controlPanelVerticalOffset".to_string());
        }
        for (name, value, minimum, maximum) in [
            ("speechFontSize", self.speech_font_size, 10, 24),
            ("nameFontSize", self.name_font_size, 10, 20),
            ("inputFontSize", self.input_font_size, 12, 20),
        ] {
            if !bounded(value, minimum, maximum) {
                return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
            }
        }
        let expected = THEME_TOKENS
            .iter()
            .map(|(public, _)| *public)
            .collect::<BTreeSet<_>>();
        if self.theme_tokens.len() != expected.len()
            || self
                .theme_tokens
                .iter()
                .any(|(key, value)| !expected.contains(key.as_str()) || !is_hex_color(value))
        {
            return Err("APPEARANCE_THEME_INVALID".to_string());
        }
        Ok(())
    }

    fn defaults(presentation: &CharacterPresentation) -> Self {
        Self {
            portrait_scale_percent: 100,
            control_panel_width: 640,
            bubble_max_height: 128,
            bubble_auto_expand: false,
            control_panel_vertical_offset: 0,
            input_bar_offset: 0,
            speech_font_size: 19,
            name_font_size: 13,
            input_font_size: 15,
            visual_effect_mode: DEFAULT_VISUAL_EFFECT_MODE,
            theme_tokens: presentation.theme_tokens.clone(),
        }
    }
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AppearancePublication {
    pub schema_version: u32,
    pub core_generation_id: String,
    pub character_id: String,
    pub values: AppearanceValues,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppearanceLimits {
    pub portrait_scale_percent: [u16; 3],
    pub control_panel_width: [u16; 3],
    pub bubble_max_height: [u16; 3],
    pub control_panel_vertical_offset: [i16; 3],
    pub input_bar_offset: [u16; 3],
    pub speech_font_size: [u16; 3],
    pub name_font_size: [u16; 3],
    pub input_font_size: [u16; 3],
}

impl Default for AppearanceLimits {
    fn default() -> Self {
        Self {
            portrait_scale_percent: [50, 150, 100],
            control_panel_width: [420, 860, 640],
            bubble_max_height: [96, 260, 128],
            control_panel_vertical_offset: [-200, 200, 0],
            input_bar_offset: [0, 200, 0],
            speech_font_size: [10, 24, 19],
            name_font_size: [10, 20, 13],
            input_font_size: [12, 20, 15],
        }
    }
}

#[derive(Clone, Debug)]
struct PreviewSession {
    window_generation: u64,
    core_generation_id: String,
    character_id: String,
    baseline: AppearanceValues,
    preview: Option<AppearanceValues>,
    settings_background: String,
}

pub struct CharacterAppearanceState {
    repository: AppearanceRepository,
    session: Mutex<Option<PreviewSession>>,
}

impl CharacterAppearanceState {
    #[cfg(test)]
    pub fn new(app_root: PathBuf) -> Self {
        Self::new_with_repository_path(app_root.join("config/ui.json"))
    }

    #[cfg(test)]
    pub(crate) fn new_with_repository_path(repository_path: PathBuf) -> Self {
        Self::new_with_repository(UiConfigRepository::new(repository_path))
    }

    pub(crate) fn new_with_repository(repository: UiConfigRepository) -> Self {
        Self {
            repository: AppearanceRepository::from_config(repository),
            session: Mutex::new(None),
        }
    }

    pub fn persisted(
        &self,
        presentation: &CharacterPresentation,
    ) -> Result<AppearancePublication, String> {
        let values = self.repository.load_for(presentation)?;
        publication(presentation, values)
    }

    pub fn current(
        &self,
        presentation: &CharacterPresentation,
    ) -> Result<AppearancePublication, String> {
        let session_values = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?
            .as_ref()
            .filter(|session| {
                session.core_generation_id == presentation.generation_id
                    && session.character_id == presentation.character_id
            })
            .map(|session| {
                session
                    .preview
                    .as_ref()
                    .unwrap_or(&session.baseline)
                    .clone()
            });
        match session_values {
            Some(values) => publication(presentation, values),
            None => self.persisted(presentation),
        }
    }

    pub fn open(
        &self,
        window_generation: u64,
        presentation: &CharacterPresentation,
    ) -> Result<(AppearancePublication, Option<AppearancePublication>), String> {
        let baseline = self.repository.load_for(presentation)?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        let cancelled = session
            .take()
            .filter(|existing| existing.preview.is_some())
            .map(|existing| publication_from_session(&existing, existing.baseline.clone()))
            .transpose()?;
        *session = Some(PreviewSession {
            window_generation,
            core_generation_id: presentation.generation_id.clone(),
            character_id: presentation.character_id.clone(),
            settings_background: baseline
                .theme_tokens
                .get("pageBackground")
                .expect("validated appearance theme")
                .clone(),
            baseline: baseline.clone(),
            preview: None,
        });
        Ok((publication(presentation, baseline)?, cancelled))
    }

    #[cfg(test)]
    pub fn preview(
        &self,
        window_generation: u64,
        presentation: &CharacterPresentation,
        values: AppearanceValues,
    ) -> Result<(AppearancePublication, bool), String> {
        values.validate()?;
        let mut session = self.checked_session(window_generation, presentation)?;
        apply_preview_to_session(
            session.as_mut().expect("checked appearance session"),
            values,
        )
    }

    pub fn preview_bound_session(
        &self,
        window_generation: u64,
        values: AppearanceValues,
    ) -> Result<(AppearancePublication, bool), String> {
        values.validate()?;
        let mut session = self.checked_window_session(window_generation)?;
        apply_preview_to_session(
            session.as_mut().expect("checked appearance session"),
            values,
        )
    }

    pub fn mark_settings_background_synced(&self, values: &AppearanceValues) -> Result<(), String> {
        let background = values
            .theme_tokens
            .get("pageBackground")
            .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        let Some(session) = session.as_mut() else {
            return Ok(());
        };
        let current_background = session
            .preview
            .as_ref()
            .unwrap_or(&session.baseline)
            .theme_tokens
            .get("pageBackground");
        if current_background == Some(background) {
            session.settings_background = background.clone();
        }
        Ok(())
    }

    pub fn save(
        &self,
        window_generation: u64,
        presentation: &CharacterPresentation,
        values: AppearanceValues,
    ) -> Result<AppearancePublication, String> {
        values.validate()?;
        let mut session = self.checked_session(window_generation, presentation)?;
        self.repository
            .save_for(&presentation.character_id, &values)?;
        let session = session.as_mut().expect("checked appearance session");
        session.baseline = values.clone();
        session.preview = None;
        publication(presentation, values)
    }

    pub fn cancel(&self) -> Result<Option<AppearancePublication>, String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        let Some(existing) = session.as_mut() else {
            return Ok(None);
        };
        if existing.preview.take().is_none() {
            return Ok(None);
        }
        publication_from_session(existing, existing.baseline.clone()).map(Some)
    }

    pub fn close_session(&self) -> Result<Option<AppearancePublication>, String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        session
            .take()
            .filter(|existing| existing.preview.is_some())
            .map(|existing| publication_from_session(&existing, existing.baseline.clone()))
            .transpose()
    }

    pub fn cancel_if_generation_changed(
        &self,
        current_generation_id: Option<&str>,
    ) -> Result<Option<AppearancePublication>, String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        let Some(current_generation_id) = current_generation_id else {
            // A Core generation transition temporarily has no available generation. Keep the preview
            // session bound to its last confirmed generation until a different generation is
            // actually published; close_session still handles application shutdown.
            return Ok(None);
        };
        if session
            .as_ref()
            .is_none_or(|existing| current_generation_id == existing.core_generation_id.as_str())
        {
            return Ok(None);
        }
        session
            .take()
            .filter(|existing| existing.preview.is_some())
            .map(|existing| publication_from_session(&existing, existing.baseline.clone()))
            .transpose()
    }

    fn checked_session<'a>(
        &'a self,
        window_generation: u64,
        presentation: &CharacterPresentation,
    ) -> Result<std::sync::MutexGuard<'a, Option<PreviewSession>>, String> {
        let session = self.checked_window_session(window_generation)?;
        let valid = session.as_ref().is_some_and(|existing| {
            existing.core_generation_id == presentation.generation_id
                && existing.character_id == presentation.character_id
        });
        if !valid {
            return Err("APPEARANCE_SESSION_STALE".to_string());
        }
        Ok(session)
    }

    fn checked_window_session(
        &self,
        window_generation: u64,
    ) -> Result<std::sync::MutexGuard<'_, Option<PreviewSession>>, String> {
        let session = self
            .session
            .lock()
            .map_err(|_| "APPEARANCE_STATE_UNAVAILABLE".to_string())?;
        if session
            .as_ref()
            .is_none_or(|existing| existing.window_generation != window_generation)
        {
            return Err("APPEARANCE_SESSION_STALE".to_string());
        }
        Ok(session)
    }
}

fn apply_preview_to_session(
    session: &mut PreviewSession,
    values: AppearanceValues,
) -> Result<(AppearancePublication, bool), String> {
    let settings_background_changed = values
        .theme_tokens
        .get("pageBackground")
        .is_none_or(|background| background != &session.settings_background);
    session.preview = Some(values.clone());
    Ok((
        publication_from_session(session, values)?,
        settings_background_changed,
    ))
}

fn publication(
    presentation: &CharacterPresentation,
    values: AppearanceValues,
) -> Result<AppearancePublication, String> {
    values.validate()?;
    Ok(AppearancePublication {
        schema_version: 1,
        core_generation_id: presentation.generation_id.clone(),
        character_id: presentation.character_id.clone(),
        values,
    })
}

fn publication_from_session(
    session: &PreviewSession,
    values: AppearanceValues,
) -> Result<AppearancePublication, String> {
    values.validate()?;
    Ok(AppearancePublication {
        schema_version: 1,
        core_generation_id: session.core_generation_id.clone(),
        character_id: session.character_id.clone(),
        values,
    })
}

struct AppearanceRepository {
    config: UiConfigRepository,
}

impl AppearanceRepository {
    #[cfg(test)]
    fn new(path: PathBuf) -> Self {
        Self {
            config: UiConfigRepository::new(path),
        }
    }

    fn from_config(config: UiConfigRepository) -> Self {
        Self { config }
    }

    fn load_document(&self) -> Result<Value, String> {
        let document = self.config.load("APPEARANCE")?;
        validate_document(&document)?;
        Ok(document)
    }

    fn load_for(&self, presentation: &CharacterPresentation) -> Result<AppearanceValues, String> {
        let document = self.load_document()?;
        values_from_document(&document, presentation)
    }

    fn save_for(&self, character_id: &str, values: &AppearanceValues) -> Result<(), String> {
        values.validate()?;
        self.config.update("APPEARANCE", |document| {
            validate_document(document)?;
            let settings = document
                .get_mut("settings")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| "APPEARANCE_DOCUMENT_INVALID".to_string())?;
            settings.insert(
                "portrait_scale_percent".to_string(),
                Value::from(values.portrait_scale_percent),
            );
            for (name, value) in [
                ("control_panel_width", values.control_panel_width),
                ("bubble_height", values.bubble_max_height),
                ("input_bar_offset", values.input_bar_offset),
            ] {
                settings.insert(name.to_string(), Value::from(value));
            }
            settings.insert(
                "bubble_auto_expand".to_string(),
                Value::from(values.bubble_auto_expand),
            );
            settings.insert(
                "visual_effect_mode".to_string(),
                Value::from(match values.visual_effect_mode {
                    InputVisualEffectMode::Solid => "solid",
                    InputVisualEffectMode::GaussianBlur => "gaussian_blur",
                    InputVisualEffectMode::LiquidGlass => "liquid_glass",
                }),
            );
            settings.insert(
                "control_panel_vertical_offset".to_string(),
                Value::from(values.control_panel_vertical_offset),
            );
            for (name, value) in [
                ("speech_font_size", values.speech_font_size),
                ("name_font_size", values.name_font_size),
                ("input_font_size", values.input_font_size),
            ] {
                settings.insert(name.to_string(), Value::from(value));
            }
            let overrides = settings
                .entry("character_theme_overrides")
                .or_insert_with(|| Value::Object(Map::new()))
                .as_object_mut()
                .ok_or_else(|| "APPEARANCE_THEME_OVERRIDES_INVALID".to_string())?;
            overrides.insert(character_id.to_string(), disk_theme(&values.theme_tokens)?);
            validate_document(document)
        })
    }
}

fn validate_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "APPEARANCE_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return Err("APPEARANCE_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(DOMAIN) {
        return Err("APPEARANCE_DOMAIN_INVALID".to_string());
    }
    let settings = root
        .get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "APPEARANCE_DOCUMENT_INVALID".to_string())?;
    validate_optional_number(settings, "portrait_scale_percent", 50, 150)?;
    validate_optional_number(settings, "control_panel_width", 420, 860)?;
    validate_optional_number(settings, "bubble_height", 96, 260)?;
    validate_optional_bool(settings, "bubble_auto_expand")?;
    validate_optional_signed_number(settings, "control_panel_vertical_offset", -200, 200)?;
    validate_optional_number(settings, "input_bar_offset", 0, 200)?;
    validate_optional_number(settings, "speech_font_size", 10, 24)?;
    validate_optional_number(settings, "name_font_size", 10, 20)?;
    validate_optional_number(settings, "input_font_size", 12, 20)?;
    if let Some(value) = settings.get("visual_effect_mode") {
        let mode = value
            .as_str()
            .ok_or_else(|| "APPEARANCE_FIELD_INVALID:visual_effect_mode".to_string())?;
        InputVisualEffectMode::from_disk(mode)?;
    }
    if let Some(value) = settings.get("character_theme_overrides") {
        let overrides = value
            .as_object()
            .ok_or_else(|| "APPEARANCE_THEME_OVERRIDES_INVALID".to_string())?;
        for (character_id, theme) in overrides {
            if !safe_character_id(character_id) {
                return Err("APPEARANCE_CHARACTER_ID_INVALID".to_string());
            }
            public_theme(theme)?;
        }
    }
    Ok(())
}

fn values_from_document(
    document: &Value,
    presentation: &CharacterPresentation,
) -> Result<AppearanceValues, String> {
    validate_document(document)?;
    let settings = document["settings"]
        .as_object()
        .ok_or_else(|| "APPEARANCE_DOCUMENT_INVALID".to_string())?;
    let mut values = AppearanceValues::defaults(presentation);
    values.portrait_scale_percent =
        optional_u16(settings, "portrait_scale_percent")?.unwrap_or(values.portrait_scale_percent);
    values.control_panel_width =
        optional_u16(settings, "control_panel_width")?.unwrap_or(values.control_panel_width);
    values.bubble_max_height =
        optional_u16(settings, "bubble_height")?.unwrap_or(values.bubble_max_height);
    values.bubble_auto_expand =
        optional_bool(settings, "bubble_auto_expand")?.unwrap_or(values.bubble_auto_expand);
    values.control_panel_vertical_offset = optional_i16(settings, "control_panel_vertical_offset")?
        .unwrap_or(values.control_panel_vertical_offset);
    values.input_bar_offset =
        optional_u16(settings, "input_bar_offset")?.unwrap_or(values.input_bar_offset);
    values.speech_font_size =
        optional_u16(settings, "speech_font_size")?.unwrap_or(values.speech_font_size);
    values.name_font_size =
        optional_u16(settings, "name_font_size")?.unwrap_or(values.name_font_size);
    values.input_font_size =
        optional_u16(settings, "input_font_size")?.unwrap_or(values.input_font_size);
    values.visual_effect_mode = settings
        .get("visual_effect_mode")
        .map(|value| {
            value
                .as_str()
                .ok_or_else(|| "APPEARANCE_FIELD_INVALID:visual_effect_mode".to_string())
                .and_then(InputVisualEffectMode::from_disk)
        })
        .transpose()?
        .unwrap_or(DEFAULT_VISUAL_EFFECT_MODE);
    if let Some(theme) = settings
        .get("character_theme_overrides")
        .and_then(Value::as_object)
        .and_then(|overrides| overrides.get(&presentation.character_id))
    {
        values.theme_tokens = public_theme(theme)?;
    }
    values.validate()?;
    Ok(values)
}

fn validate_optional_number(
    settings: &Map<String, Value>,
    name: &str,
    minimum: u64,
    maximum: u64,
) -> Result<(), String> {
    if let Some(value) = settings.get(name) {
        let Some(number) = value.as_u64() else {
            return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
        };
        if number < minimum || number > maximum {
            return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
        }
    }
    Ok(())
}

fn validate_optional_bool(settings: &Map<String, Value>, name: &str) -> Result<(), String> {
    if settings.get(name).is_some_and(|value| !value.is_boolean()) {
        return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
    }
    Ok(())
}

fn optional_bool(settings: &Map<String, Value>, name: &str) -> Result<Option<bool>, String> {
    settings
        .get(name)
        .map(|value| {
            value
                .as_bool()
                .ok_or_else(|| format!("APPEARANCE_FIELD_INVALID:{name}"))
        })
        .transpose()
}

fn validate_optional_signed_number(
    settings: &Map<String, Value>,
    name: &str,
    minimum: i64,
    maximum: i64,
) -> Result<(), String> {
    if let Some(value) = settings.get(name) {
        let Some(number) = value.as_i64() else {
            return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
        };
        if number < minimum || number > maximum {
            return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
        }
    }
    Ok(())
}

fn optional_u16(settings: &Map<String, Value>, name: &str) -> Result<Option<u16>, String> {
    settings
        .get(name)
        .map(|value| {
            value
                .as_u64()
                .and_then(|number| u16::try_from(number).ok())
                .ok_or_else(|| format!("APPEARANCE_FIELD_INVALID:{name}"))
        })
        .transpose()
}

fn optional_i16(settings: &Map<String, Value>, name: &str) -> Result<Option<i16>, String> {
    settings
        .get(name)
        .map(|value| {
            value
                .as_i64()
                .and_then(|number| i16::try_from(number).ok())
                .ok_or_else(|| format!("APPEARANCE_FIELD_INVALID:{name}"))
        })
        .transpose()
}

fn public_theme(value: &Value) -> Result<BTreeMap<String, String>, String> {
    let disk = value
        .as_object()
        .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
    if disk.len() != THEME_TOKENS.len() {
        return Err("APPEARANCE_THEME_INVALID".to_string());
    }
    THEME_TOKENS
        .iter()
        .map(|(public, stored)| {
            let color = disk
                .get(*stored)
                .and_then(Value::as_str)
                .filter(|color| is_hex_color(color))
                .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
            Ok(((*public).to_string(), color.to_ascii_lowercase()))
        })
        .collect()
}

fn disk_theme(theme: &BTreeMap<String, String>) -> Result<Value, String> {
    let mut disk = Map::new();
    for (public, stored) in THEME_TOKENS {
        let color = theme
            .get(public)
            .filter(|color| is_hex_color(color))
            .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
        disk.insert(
            stored.to_string(),
            Value::String(color.to_ascii_lowercase()),
        );
    }
    Ok(Value::Object(disk))
}

fn bounded(value: u16, minimum: u16, maximum: u16) -> bool {
    value >= minimum && value <= maximum
}

fn safe_character_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
        && !matches!(value, "." | "..")
}

fn is_hex_color(value: &str) -> bool {
    value.len() == 7
        && value.starts_with('#')
        && value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir()
                .join(format!("sakura-wp-3u-02-{}-{nonce}", std::process::id()));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn presentation(&self, generation: &str) -> CharacterPresentation {
            CharacterPresentation {
                schema_version: 1,
                generation_id: generation.to_string(),
                character_id: "Sakura".to_string(),
                display_name: "Sakura".to_string(),
                initial_message: "hello".to_string(),
                theme_tokens: THEME_TOKENS
                    .iter()
                    .map(|(key, _)| ((*key).to_string(), "#a1b2c3".to_string()))
                    .collect(),
                default_portrait_key: "__default__".to_string(),
                portrait_keys: vec!["__default__".to_string()],
                portrait_resource_ids: BTreeMap::from([(
                    "__default__".to_string(),
                    "character-v1-53616b757261-portrait-5f5f64656661756c745f5f".to_string(),
                )]),
            }
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn repository_preserves_unrelated_settings_and_atomically_reopens() {
        let fixture = Fixture::new();
        let path = fixture.0.join("config/ui.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"typewriter_cps":30,"control_panel_width":860,"bubble_height":260,"control_panel_vertical_offset":-200,"input_bar_offset":200,"button_font_size":19}}"#,
        )
        .unwrap();
        let repository = AppearanceRepository::new(path.clone());
        let presentation = fixture.presentation("generation-a");
        let mut values = repository.load_for(&presentation).unwrap();
        assert_eq!(values.control_panel_width, 860);
        assert_eq!(values.bubble_max_height, 260);
        assert!(!values.bubble_auto_expand);
        assert_eq!(values.control_panel_vertical_offset, -200);
        assert_eq!(values.input_bar_offset, 200);
        values.portrait_scale_percent = 125;
        values.bubble_auto_expand = true;
        values
            .theme_tokens
            .insert("accent".to_string(), "#112233".to_string());
        repository.save_for("Sakura", &values).unwrap();
        assert_eq!(repository.load_for(&presentation).unwrap(), values);
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["typewriter_cps"], 30);
        assert_eq!(document["settings"]["button_font_size"], 19);
        assert_eq!(document["settings"]["visual_effect_mode"], "gaussian_blur");
        assert_eq!(document["settings"]["bubble_auto_expand"], true);
    }

    #[test]
    fn visual_effect_mode_defaults_strictly_parses_and_round_trips_globally() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        fs::write(
            &path,
            br##"{"schema_version":1,"domain":"ui","settings":{"character_theme_overrides":{"Sakura":{"primary_color":"#a1b2c3","primary_hover_color":"#a1b2c3","accent_color":"#a1b2c3","text_color":"#a1b2c3","secondary_text_color":"#a1b2c3","muted_text_color":"#a1b2c3","page_background_color":"#a1b2c3","panel_background_color":"#a1b2c3","input_background_color":"#a1b2c3","bubble_background_color":"#a1b2c3","border_color":"#a1b2c3"}}}}"##,
        )
        .unwrap();
        let repository = AppearanceRepository::new(path.clone());
        let presentation = fixture.presentation("generation-a");
        let mut values = repository.load_for(&presentation).unwrap();
        assert_eq!(
            values.visual_effect_mode,
            InputVisualEffectMode::GaussianBlur
        );
        values.visual_effect_mode = InputVisualEffectMode::Solid;
        repository.save_for("Sakura", &values).unwrap();
        assert_eq!(
            repository
                .load_for(&presentation)
                .unwrap()
                .visual_effect_mode,
            InputVisualEffectMode::Solid
        );
        let document: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(document["settings"]["visual_effect_mode"], "solid");
        assert!(document["settings"]["character_theme_overrides"]["Sakura"]
            .get("visual_effect_mode")
            .is_none());

        values.visual_effect_mode = InputVisualEffectMode::LiquidGlass;
        repository.save_for("Sakura", &values).unwrap();
        assert_eq!(
            repository
                .load_for(&presentation)
                .unwrap()
                .visual_effect_mode,
            InputVisualEffectMode::LiquidGlass
        );
        let document: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(document["settings"]["visual_effect_mode"], "liquid_glass");

        let mut invalid = document;
        invalid["settings"]["visual_effect_mode"] = Value::from("blur");
        fs::write(&path, serde_json::to_vec(&invalid).unwrap()).unwrap();
        assert_eq!(
            repository.load_for(&presentation).unwrap_err(),
            "APPEARANCE_FIELD_INVALID:visual_effect_mode"
        );
    }

    #[test]
    fn frontend_publication_contains_no_path_selection_or_credential_shaped_fields() {
        let fixture = Fixture::new();
        let presentation = fixture.presentation("generation-a");
        let publication =
            publication(&presentation, AppearanceValues::defaults(&presentation)).unwrap();
        assert_eq!(publication.schema_version, 1);
        let json = serde_json::to_string(&publication)
            .unwrap()
            .to_ascii_lowercase();
        for forbidden in [
            "current_character_id",
            "characters/",
            "password",
            "api_key",
            "apikey",
            "credential",
            "secret",
            "accesstoken",
            "refreshtoken",
        ] {
            assert!(!json.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn future_schema_and_invalid_fields_never_replace_existing_file() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        let before = br#"{"schema_version":2,"domain":"ui","settings":{}}"#;
        fs::write(&path, before).unwrap();
        let repository = AppearanceRepository::new(path.clone());
        let presentation = fixture.presentation("generation-a");
        let values = AppearanceValues::defaults(&presentation);
        assert_eq!(
            repository.save_for("Sakura", &values).unwrap_err(),
            "APPEARANCE_SCHEMA_UNSUPPORTED"
        );
        assert_eq!(fs::read(path).unwrap(), before);
    }

    #[test]
    fn preview_is_in_memory_cancel_is_idempotent_and_generation_is_bound() {
        let fixture = Fixture::new();
        let state = CharacterAppearanceState::new(fixture.0.clone());
        let first = fixture.presentation("generation-a");
        let (baseline, _) = state.open(7, &first).unwrap();
        let mut preview = baseline.values.clone();
        preview.portrait_scale_percent = 75;
        let (publication, background_changed) = state.preview(7, &first, preview).unwrap();
        assert_eq!(publication.values.portrait_scale_percent, 75);
        assert!(!background_changed);

        let mut themed = publication.values.clone();
        themed
            .theme_tokens
            .insert("pageBackground".to_string(), "#112233".to_string());
        let (_, background_changed) = state.preview(7, &first, themed.clone()).unwrap();
        assert!(background_changed);
        themed.control_panel_vertical_offset = 40;
        let (_, background_changed) = state.preview(7, &first, themed.clone()).unwrap();
        assert!(
            background_changed,
            "failed native sync must remain retryable"
        );
        state.mark_settings_background_synced(&themed).unwrap();
        themed.control_panel_vertical_offset = 41;
        let (_, background_changed) = state.preview(7, &first, themed).unwrap();
        assert!(!background_changed);
        assert_eq!(state.cancel().unwrap().unwrap().values, baseline.values);
        assert!(state.cancel().unwrap().is_none());
        let stale = fixture.presentation("generation-b");
        assert_eq!(
            state.preview(7, &stale, baseline.values).unwrap_err(),
            "APPEARANCE_SESSION_STALE"
        );
    }

    #[test]
    fn core_generation_change_restores_preview_baseline_and_closes_session() {
        let fixture = Fixture::new();
        let state = CharacterAppearanceState::new(fixture.0.clone());
        let presentation = fixture.presentation("generation-a");
        let (baseline, _) = state.open(3, &presentation).unwrap();
        let mut preview = baseline.values.clone();
        preview.speech_font_size = 24;
        state.preview(3, &presentation, preview).unwrap();
        assert_eq!(
            state
                .cancel_if_generation_changed(Some("generation-b"))
                .unwrap()
                .unwrap()
                .values,
            baseline.values
        );
        assert_eq!(
            state
                .preview(3, &presentation, baseline.values)
                .unwrap_err(),
            "APPEARANCE_SESSION_STALE"
        );
    }

    #[test]
    fn bound_preview_survives_transient_generation_absence_until_replacement_is_confirmed() {
        let fixture = Fixture::new();
        let state = CharacterAppearanceState::new(fixture.0.clone());
        let presentation = fixture.presentation("generation-a");
        let (baseline, _) = state.open(3, &presentation).unwrap();
        let mut preview = baseline.values.clone();
        preview.control_panel_width = 700;

        assert!(state.cancel_if_generation_changed(None).unwrap().is_none());
        let (publication, _) = state.preview_bound_session(3, preview).unwrap();
        assert_eq!(publication.core_generation_id, "generation-a");
        assert_eq!(publication.values.control_panel_width, 700);
        assert_eq!(
            state
                .preview_bound_session(4, baseline.values.clone())
                .unwrap_err(),
            "APPEARANCE_SESSION_STALE"
        );

        assert_eq!(
            state
                .cancel_if_generation_changed(Some("generation-b"))
                .unwrap()
                .unwrap()
                .values,
            baseline.values
        );
        assert_eq!(
            state
                .preview_bound_session(3, publication.values)
                .unwrap_err(),
            "APPEARANCE_SESSION_STALE"
        );
    }

    #[test]
    fn write_failure_keeps_previous_document_and_removes_temp_file() {
        let fixture = Fixture::new();
        let target = fixture.0.join("ui.json");
        fs::create_dir(&target).unwrap();
        assert!(atomic_write(&target, b"replacement").is_err());
        assert!(target.is_dir());
        assert!(fs::read_dir(&fixture.0).unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));
    }

    #[cfg(windows)]
    #[test]
    fn atomic_write_retries_a_transient_windows_sharing_violation() {
        use std::os::windows::fs::OpenOptionsExt;
        use std::{thread, time::Duration};

        let fixture = Fixture::new();
        let target = fixture.0.join("ui.json");
        fs::write(&target, b"original").unwrap();
        let locked = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&target)
            .unwrap();
        let release = thread::spawn(move || {
            thread::sleep(Duration::from_millis(100));
            drop(locked);
        });

        atomic_write(&target, b"replacement").unwrap();
        release.join().unwrap();

        assert_eq!(fs::read(&target).unwrap(), b"replacement");
    }

    #[test]
    fn save_failure_leaves_preview_available_for_gateway_baseline_rollback() {
        let fixture = Fixture::new();
        let state = CharacterAppearanceState::new(fixture.0.clone());
        let presentation = fixture.presentation("generation-a");
        let (baseline, _) = state.open(5, &presentation).unwrap();
        let mut draft = baseline.values.clone();
        draft.bubble_max_height = 180;
        state.preview(5, &presentation, draft.clone()).unwrap();
        let path = fixture.0.join("config/ui.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(state.save(5, &presentation, draft).is_err());
        assert_eq!(state.cancel().unwrap().unwrap().values, baseline.values);
        assert!(path.is_dir());
    }

    #[test]
    fn parent_permission_shape_failure_does_not_create_data_or_temp_files() {
        let fixture = Fixture::new();
        let parent_as_file = fixture.0.join("blocked");
        fs::write(&parent_as_file, b"not a directory").unwrap();
        let target = parent_as_file.join("ui.json");
        assert_eq!(
            atomic_write(&target, b"replacement").unwrap_err(),
            "APPEARANCE_PERMISSION_DENIED"
        );
        assert_eq!(fs::read(parent_as_file).unwrap(), b"not a directory");
    }
}
#[cfg(test)]
fn atomic_write(path: &std::path::Path, bytes: &[u8]) -> Result<(), String> {
    crate::ui_config::atomic_write(path, bytes, "APPEARANCE")
}
