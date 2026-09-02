//! Runtime v2 presentation timing slice stored in the shared `ui.json` document.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ui_config::UiConfigRepository;

pub const CHAT_TIMING_CHANGED_EVENT: &str = "sakura://chat-presentation-timing-changed";
pub const SUBTITLE_LANGUAGE_CHANGED_EVENT: &str = "sakura://subtitle-language-changed";
pub const BUBBLE_AUTO_HIDE_CHANGED_EVENT: &str = "sakura://bubble-auto-hide-changed";
const SCHEMA_VERSION: u64 = 1;
const DOMAIN: &str = "ui";
const TYPING_INTERVAL_MIN: u16 = 5;
const TYPING_INTERVAL_MAX: u16 = 200;
const SEGMENT_PAUSE_MIN: u16 = 0;
const SEGMENT_PAUSE_MAX: u16 = 3000;
const BUBBLE_AUTO_HIDE_DELAY_MIN: u16 = 1;
const BUBBLE_AUTO_HIDE_DELAY_MAX: u16 = 120;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ChatPresentationTiming {
    pub subtitle_typing_interval_ms: u16,
    pub reply_segment_pause_ms: u16,
}

impl Default for ChatPresentationTiming {
    fn default() -> Self {
        Self {
            subtitle_typing_interval_ms: 28,
            reply_segment_pause_ms: 160,
        }
    }
}

impl ChatPresentationTiming {
    pub fn validate(self) -> Result<Self, String> {
        if !(TYPING_INTERVAL_MIN..=TYPING_INTERVAL_MAX).contains(&self.subtitle_typing_interval_ms)
        {
            return Err("CHAT_TIMING_FIELD_INVALID:subtitleTypingIntervalMs".to_string());
        }
        if !(SEGMENT_PAUSE_MIN..=SEGMENT_PAUSE_MAX).contains(&self.reply_segment_pause_ms) {
            return Err("CHAT_TIMING_FIELD_INVALID:replySegmentPauseMs".to_string());
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ChatPresentationTimingLimits {
    pub subtitle_typing_interval_ms: [u16; 3],
    pub reply_segment_pause_ms: [u16; 3],
}

impl Default for ChatPresentationTimingLimits {
    fn default() -> Self {
        Self {
            subtitle_typing_interval_ms: [TYPING_INTERVAL_MIN, TYPING_INTERVAL_MAX, 28],
            reply_segment_pause_ms: [SEGMENT_PAUSE_MIN, SEGMENT_PAUSE_MAX, 160],
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ChatPresentationTimingSnapshot {
    pub schema_version: u32,
    pub window_generation: u64,
    pub values: ChatPresentationTiming,
    pub limits: ChatPresentationTimingLimits,
}

pub struct ChatPresentationTimingState {
    repository: UiConfigRepository,
}

impl ChatPresentationTimingState {
    pub fn new(repository: UiConfigRepository) -> Self {
        Self { repository }
    }

    pub fn get(&self) -> Result<ChatPresentationTiming, String> {
        timing_from_document(&self.repository.load("CHAT_TIMING")?)
    }

    pub fn snapshot(
        &self,
        window_generation: u64,
    ) -> Result<ChatPresentationTimingSnapshot, String> {
        Ok(ChatPresentationTimingSnapshot {
            schema_version: 1,
            window_generation,
            values: self.get()?,
            limits: ChatPresentationTimingLimits::default(),
        })
    }

    pub fn save(&self, values: ChatPresentationTiming) -> Result<ChatPresentationTiming, String> {
        let values = values.validate()?;
        self.repository.update("CHAT_TIMING", |document| {
            validate_document(document)?;
            let settings = document
                .get_mut("settings")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| "CHAT_TIMING_DOCUMENT_INVALID".to_string())?;
            settings.insert(
                "subtitle_typing_interval_ms".to_string(),
                Value::from(values.subtitle_typing_interval_ms),
            );
            settings.insert(
                "reply_segment_pause_ms".to_string(),
                Value::from(values.reply_segment_pause_ms),
            );
            validate_document(document)?;
            timing_from_document(document).map(|_| ())
        })?;
        Ok(values)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BubbleAutoHideSettings {
    pub auto_hide_enabled: bool,
    pub auto_hide_delay_seconds: u16,
}

impl Default for BubbleAutoHideSettings {
    fn default() -> Self {
        Self {
            auto_hide_enabled: true,
            auto_hide_delay_seconds: 5,
        }
    }
}

impl BubbleAutoHideSettings {
    pub fn validate(self) -> Result<Self, String> {
        if !(BUBBLE_AUTO_HIDE_DELAY_MIN..=BUBBLE_AUTO_HIDE_DELAY_MAX)
            .contains(&self.auto_hide_delay_seconds)
        {
            return Err("BUBBLE_AUTO_HIDE_FIELD_INVALID:autoHideDelaySeconds".to_string());
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BubbleAutoHideLimits {
    pub auto_hide_delay_seconds: [u16; 3],
}

impl Default for BubbleAutoHideLimits {
    fn default() -> Self {
        Self {
            auto_hide_delay_seconds: [
                BUBBLE_AUTO_HIDE_DELAY_MIN,
                BUBBLE_AUTO_HIDE_DELAY_MAX,
                BubbleAutoHideSettings::default().auto_hide_delay_seconds,
            ],
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BubbleAutoHideSnapshot {
    pub schema_version: u32,
    pub window_generation: u64,
    pub values: BubbleAutoHideSettings,
    pub limits: BubbleAutoHideLimits,
}

pub struct BubbleAutoHideState {
    repository: UiConfigRepository,
    legacy_system_config: PathBuf,
}

impl BubbleAutoHideState {
    pub fn new(repository: UiConfigRepository, legacy_system_config: PathBuf) -> Self {
        Self {
            repository,
            legacy_system_config,
        }
    }

    pub fn get(&self) -> Result<BubbleAutoHideSettings, String> {
        let document = self.repository.load("BUBBLE_AUTO_HIDE")?;
        match bubble_settings_from_document(&document)? {
            Some(values) => Ok(values),
            None => bubble_settings_from_legacy(&self.legacy_system_config),
        }
    }

    pub fn snapshot(&self, window_generation: u64) -> Result<BubbleAutoHideSnapshot, String> {
        Ok(BubbleAutoHideSnapshot {
            schema_version: 1,
            window_generation,
            values: self.get()?,
            limits: BubbleAutoHideLimits::default(),
        })
    }

    pub fn save(&self, values: BubbleAutoHideSettings) -> Result<BubbleAutoHideSettings, String> {
        let values = values.validate()?;
        self.repository.update("BUBBLE_AUTO_HIDE", |document| {
            validate_bubble_document(document)?;
            let settings = document
                .get_mut("settings")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| "BUBBLE_AUTO_HIDE_DOCUMENT_INVALID".to_string())?;
            settings.insert(
                "bubble_auto_hide_enabled".to_string(),
                Value::from(values.auto_hide_enabled),
            );
            settings.insert(
                "bubble_auto_hide_delay_seconds".to_string(),
                Value::from(values.auto_hide_delay_seconds),
            );
            bubble_settings_from_document(document).and_then(|saved| {
                saved
                    .map(|_| ())
                    .ok_or_else(|| "BUBBLE_AUTO_HIDE_DOCUMENT_INVALID".to_string())
            })
        })?;
        Ok(values)
    }
}

fn validate_bubble_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "BUBBLE_AUTO_HIDE_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return Err("BUBBLE_AUTO_HIDE_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(DOMAIN) {
        return Err("BUBBLE_AUTO_HIDE_DOMAIN_INVALID".to_string());
    }
    root.get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "BUBBLE_AUTO_HIDE_DOCUMENT_INVALID".to_string())?;
    Ok(())
}

fn bubble_settings_from_document(
    document: &Value,
) -> Result<Option<BubbleAutoHideSettings>, String> {
    validate_bubble_document(document)?;
    let settings = document["settings"]
        .as_object()
        .ok_or_else(|| "BUBBLE_AUTO_HIDE_DOCUMENT_INVALID".to_string())?;
    let has_enabled = settings.contains_key("bubble_auto_hide_enabled");
    let has_delay = settings.contains_key("bubble_auto_hide_delay_seconds");
    if !has_enabled && !has_delay {
        return Ok(None);
    }
    let defaults = BubbleAutoHideSettings::default();
    BubbleAutoHideSettings {
        auto_hide_enabled: settings
            .get("bubble_auto_hide_enabled")
            .map(|value| {
                value
                    .as_bool()
                    .ok_or_else(|| "BUBBLE_AUTO_HIDE_FIELD_INVALID:autoHideEnabled".to_string())
            })
            .transpose()?
            .unwrap_or(defaults.auto_hide_enabled),
        auto_hide_delay_seconds: settings
            .get("bubble_auto_hide_delay_seconds")
            .map(|value| {
                value
                    .as_u64()
                    .and_then(|number| u16::try_from(number).ok())
                    .ok_or_else(|| {
                        "BUBBLE_AUTO_HIDE_FIELD_INVALID:autoHideDelaySeconds".to_string()
                    })
            })
            .transpose()?
            .unwrap_or(defaults.auto_hide_delay_seconds),
    }
    .validate()
    .map(Some)
}

fn bubble_settings_from_legacy(path: &Path) -> Result<BubbleAutoHideSettings, String> {
    if !path.is_file() {
        return Ok(BubbleAutoHideSettings::default());
    }
    let source = std::fs::read_to_string(path)
        .map_err(|_| "BUBBLE_AUTO_HIDE_LEGACY_READ_FAILED".to_string())?;
    let document: serde_yaml::Value = serde_yaml::from_str(&source)
        .map_err(|_| "BUBBLE_AUTO_HIDE_LEGACY_DOCUMENT_INVALID".to_string())?;
    let ui = document.get("ui").and_then(serde_yaml::Value::as_mapping);
    let defaults = BubbleAutoHideSettings::default();
    let enabled = ui
        .and_then(|mapping| mapping.get("bubble_auto_hide_enabled"))
        .map(|value| {
            value
                .as_bool()
                .ok_or_else(|| "BUBBLE_AUTO_HIDE_FIELD_INVALID:autoHideEnabled".to_string())
        })
        .transpose()?
        .unwrap_or(defaults.auto_hide_enabled);
    let delay = ui
        .and_then(|mapping| mapping.get("bubble_auto_hide_delay_seconds"))
        .map(|value| {
            value
                .as_u64()
                .and_then(|number| u16::try_from(number).ok())
                .ok_or_else(|| "BUBBLE_AUTO_HIDE_FIELD_INVALID:autoHideDelaySeconds".to_string())
        })
        .transpose()?
        .unwrap_or(defaults.auto_hide_delay_seconds);
    BubbleAutoHideSettings {
        auto_hide_enabled: enabled,
        auto_hide_delay_seconds: delay,
    }
    .validate()
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SubtitleLanguage {
    #[default]
    Zh,
    Ja,
}

impl SubtitleLanguage {
    pub fn toggled(self) -> Self {
        match self {
            Self::Zh => Self::Ja,
            Self::Ja => Self::Zh,
        }
    }

    pub fn is_chinese(self) -> bool {
        self == Self::Zh
    }
}

pub struct SubtitleLanguageState {
    repository: UiConfigRepository,
}

impl SubtitleLanguageState {
    pub fn new(repository: UiConfigRepository) -> Self {
        Self { repository }
    }

    pub fn get(&self) -> Result<SubtitleLanguage, String> {
        subtitle_language_from_document(&self.repository.load("CHAT_SUBTITLE")?)
    }

    pub fn save(&self, language: SubtitleLanguage) -> Result<SubtitleLanguage, String> {
        self.repository.update("CHAT_SUBTITLE", |document| {
            validate_subtitle_document(document)?;
            let settings = document
                .get_mut("settings")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| "CHAT_SUBTITLE_DOCUMENT_INVALID".to_string())?;
            settings.insert(
                "subtitle_language".to_string(),
                Value::String(
                    match language {
                        SubtitleLanguage::Zh => "zh",
                        SubtitleLanguage::Ja => "ja",
                    }
                    .to_string(),
                ),
            );
            Ok(())
        })?;
        Ok(language)
    }

    pub fn toggle(&self) -> Result<SubtitleLanguage, String> {
        self.save(self.get()?.toggled())
    }
}

fn validate_subtitle_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "CHAT_SUBTITLE_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return Err("CHAT_SUBTITLE_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(DOMAIN) {
        return Err("CHAT_SUBTITLE_DOMAIN_INVALID".to_string());
    }
    root.get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "CHAT_SUBTITLE_DOCUMENT_INVALID".to_string())?;
    Ok(())
}

fn subtitle_language_from_document(document: &Value) -> Result<SubtitleLanguage, String> {
    validate_subtitle_document(document)?;
    Ok(match document["settings"]["subtitle_language"].as_str() {
        Some("ja") => SubtitleLanguage::Ja,
        _ => SubtitleLanguage::Zh,
    })
}

fn validate_document(document: &Value) -> Result<(), String> {
    let root = document
        .as_object()
        .ok_or_else(|| "CHAT_TIMING_DOCUMENT_INVALID".to_string())?;
    if root.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return Err("CHAT_TIMING_SCHEMA_UNSUPPORTED".to_string());
    }
    if root.get("domain").and_then(Value::as_str) != Some(DOMAIN) {
        return Err("CHAT_TIMING_DOMAIN_INVALID".to_string());
    }
    root.get("settings")
        .and_then(Value::as_object)
        .ok_or_else(|| "CHAT_TIMING_DOCUMENT_INVALID".to_string())?;
    Ok(())
}

fn timing_from_document(document: &Value) -> Result<ChatPresentationTiming, String> {
    validate_document(document)?;
    let settings = document["settings"]
        .as_object()
        .ok_or_else(|| "CHAT_TIMING_DOCUMENT_INVALID".to_string())?;
    let defaults = ChatPresentationTiming::default();
    let read = |name: &str, default: u16| -> Result<u16, String> {
        settings
            .get(name)
            .map(|value| {
                value
                    .as_u64()
                    .and_then(|number| u16::try_from(number).ok())
                    .ok_or_else(|| format!("CHAT_TIMING_FIELD_INVALID:{name}"))
            })
            .transpose()
            .map(|value| value.unwrap_or(default))
    };
    ChatPresentationTiming {
        subtitle_typing_interval_ms: read(
            "subtitle_typing_interval_ms",
            defaults.subtitle_typing_interval_ms,
        )?,
        reply_segment_pause_ms: read("reply_segment_pause_ms", defaults.reply_segment_pause_ms)?,
    }
    .validate()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        path::PathBuf,
        time::{SystemTime, UNIX_EPOCH},
    };

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "sakura-wp-3-04-timing-{}-{nonce}",
                std::process::id()
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn timing_round_trips_and_preserves_unknown_fields() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"future":true}}"#,
        )
        .unwrap();
        let state = ChatPresentationTimingState::new(UiConfigRepository::new(path.clone()));
        let values = ChatPresentationTiming {
            subtitle_typing_interval_ms: 41,
            reply_segment_pause_ms: 275,
        };
        assert_eq!(state.save(values).unwrap(), values);
        assert_eq!(state.get().unwrap(), values);
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["future"], true);
    }

    #[test]
    fn invalid_or_unwritable_documents_do_not_replace_the_previous_value() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        fs::write(&path, b"not json").unwrap();
        let state = ChatPresentationTimingState::new(UiConfigRepository::new(path.clone()));
        assert!(state.save(ChatPresentationTiming::default()).is_err());
        assert_eq!(fs::read(path).unwrap(), b"not json");
    }

    #[test]
    fn subtitle_language_defaults_to_chinese_and_normalizes_invalid_values() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        let state = SubtitleLanguageState::new(UiConfigRepository::new(path.clone()));
        assert_eq!(state.get().unwrap(), SubtitleLanguage::Zh);

        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"subtitle_language":"invalid"}}"#,
        )
        .unwrap();
        assert_eq!(state.get().unwrap(), SubtitleLanguage::Zh);
        assert_eq!(
            state.save(SubtitleLanguage::Ja).unwrap(),
            SubtitleLanguage::Ja
        );
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["subtitle_language"], "ja");
    }

    #[test]
    fn subtitle_language_round_trips_and_preserves_unknown_fields() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        fs::write(
            &path,
            br#"{"schema_version":1,"domain":"ui","settings":{"future":true,"subtitle_language":"zh"}}"#,
        )
        .unwrap();
        let state = SubtitleLanguageState::new(UiConfigRepository::new(path.clone()));
        assert_eq!(state.toggle().unwrap(), SubtitleLanguage::Ja);
        assert_eq!(state.get().unwrap(), SubtitleLanguage::Ja);
        let document: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(document["settings"]["future"], true);
    }

    #[test]
    fn subtitle_language_save_failure_keeps_the_previous_document() {
        let fixture = Fixture::new();
        let path = fixture.0.join("ui.json");
        fs::write(&path, b"not json").unwrap();
        let state = SubtitleLanguageState::new(UiConfigRepository::new(path.clone()));
        assert!(state.save(SubtitleLanguage::Ja).is_err());
        assert_eq!(fs::read(path).unwrap(), b"not json");
    }

    #[test]
    fn bubble_auto_hide_uses_legacy_values_until_runtime_v2_saves_its_own_slice() {
        let fixture = Fixture::new();
        let ui_path = fixture.0.join("ui.json");
        let legacy_path = fixture.0.join("system_config.yaml");
        fs::write(
            &legacy_path,
            "config_version: 1\nui:\n  bubble_auto_hide_enabled: false\n  bubble_auto_hide_delay_seconds: 11\n",
        )
        .unwrap();
        let state = BubbleAutoHideState::new(UiConfigRepository::new(ui_path.clone()), legacy_path);
        assert_eq!(
            state.get().unwrap(),
            BubbleAutoHideSettings {
                auto_hide_enabled: false,
                auto_hide_delay_seconds: 11,
            }
        );

        let saved = BubbleAutoHideSettings {
            auto_hide_enabled: true,
            auto_hide_delay_seconds: 8,
        };
        assert_eq!(state.save(saved).unwrap(), saved);
        assert_eq!(state.get().unwrap(), saved);
        let document: Value = serde_json::from_slice(&fs::read(ui_path).unwrap()).unwrap();
        assert_eq!(document["settings"]["bubble_auto_hide_enabled"], true);
        assert_eq!(document["settings"]["bubble_auto_hide_delay_seconds"], 8);
    }

    #[test]
    fn bubble_auto_hide_rejects_invalid_runtime_values_without_rewriting_ui_json() {
        let fixture = Fixture::new();
        let ui_path = fixture.0.join("ui.json");
        fs::write(
            &ui_path,
            br#"{"schema_version":1,"domain":"ui","settings":{"bubble_auto_hide_delay_seconds":0}}"#,
        )
        .unwrap();
        let before = fs::read(&ui_path).unwrap();
        let state = BubbleAutoHideState::new(
            UiConfigRepository::new(ui_path.clone()),
            fixture.0.join("system_config.yaml"),
        );
        assert!(state.get().is_err());
        assert_eq!(fs::read(ui_path).unwrap(), before);
    }
}
