//! Runtime v2 presentation timing slice stored in the shared `ui.json` document.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ui_config::UiConfigRepository;

pub const CHAT_TIMING_CHANGED_EVENT: &str = "sakura://chat-presentation-timing-changed";
const SCHEMA_VERSION: u64 = 1;
const DOMAIN: &str = "ui";
const TYPING_INTERVAL_MIN: u16 = 5;
const TYPING_INTERVAL_MAX: u16 = 200;
const SEGMENT_PAUSE_MIN: u16 = 0;
const SEGMENT_PAUSE_MAX: u16 = 3000;

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
}
