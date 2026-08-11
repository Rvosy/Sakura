//! Strict local settings repository for the private Prompt/Agent trace.

use std::{fs, path::PathBuf, sync::Mutex};

use serde::{Deserialize, Serialize};
use serde_yaml::{Mapping, Value};

const NAMESPACE: &str = "AGENT_TRACE_SETTINGS";
const MAX_CONFIG_BYTES: u64 = 2 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AgentTraceSettings {
    pub enabled: bool,
}

impl Default for AgentTraceSettings {
    fn default() -> Self {
        Self { enabled: true }
    }
}

pub struct AgentTraceSettingsState {
    path: PathBuf,
    transaction: Mutex<()>,
}

impl AgentTraceSettingsState {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            transaction: Mutex::new(()),
        }
    }

    pub fn get(&self) -> Result<AgentTraceSettings, String> {
        let _guard = self
            .transaction
            .lock()
            .map_err(|_| code("STATE_UNAVAILABLE"))?;
        settings_from_document(&load_document(&self.path)?)
    }

    pub fn save(&self, settings: AgentTraceSettings) -> Result<AgentTraceSettings, String> {
        let _guard = self
            .transaction
            .lock()
            .map_err(|_| code("STATE_UNAVAILABLE"))?;
        let mut document = load_document(&self.path)?;
        let root = document
            .as_mapping_mut()
            .ok_or_else(|| code("DOCUMENT_INVALID"))?;
        let mut section = Mapping::new();
        section.insert(
            Value::String("enabled".to_string()),
            Value::Bool(settings.enabled),
        );
        root.insert(
            Value::String("agent_trace".to_string()),
            Value::Mapping(section),
        );
        let bytes = serde_yaml::to_string(&document)
            .map_err(|_| code("SERIALIZE_FAILED"))?
            .into_bytes();
        crate::ui_config::atomic_write(&self.path, &bytes, NAMESPACE)?;
        Ok(settings)
    }
}

fn load_document(path: &std::path::Path) -> Result<Value, String> {
    if !path.exists() {
        return Ok(Value::Mapping(Mapping::new()));
    }
    let metadata = fs::metadata(path).map_err(|_| code("READ_FAILED"))?;
    if metadata.len() == 0 || metadata.len() > MAX_CONFIG_BYTES {
        return Err(code("DOCUMENT_INVALID"));
    }
    let bytes = fs::read(path).map_err(|_| code("READ_FAILED"))?;
    let value: Value = serde_yaml::from_slice(&bytes).map_err(|_| code("DOCUMENT_INVALID"))?;
    if !value.is_mapping() {
        return Err(code("DOCUMENT_INVALID"));
    }
    Ok(value)
}

fn settings_from_document(document: &Value) -> Result<AgentTraceSettings, String> {
    let root = document
        .as_mapping()
        .ok_or_else(|| code("DOCUMENT_INVALID"))?;
    let Some(section) = root.get(Value::String("agent_trace".to_string())) else {
        return Ok(AgentTraceSettings::default());
    };
    let mapping = section.as_mapping().ok_or_else(|| code("FIELD_INVALID"))?;
    if mapping
        .keys()
        .any(|key| key.as_str().is_none_or(|name| name != "enabled"))
    {
        return Err(code("FIELD_INVALID"));
    }
    let enabled = match mapping.get(Value::String("enabled".to_string())) {
        Some(value) => value.as_bool().ok_or_else(|| code("FIELD_INVALID"))?,
        None => true,
    };
    Ok(AgentTraceSettings { enabled })
}

fn code(suffix: &str) -> String {
    format!("{NAMESPACE}_{suffix}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static FIXTURE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

    fn fixture() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let sequence = FIXTURE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "sakura-agent-trace-settings-{}-{nonce}-{sequence}",
            std::process::id()
        ))
    }

    #[test]
    fn wp_4l_02_defaults_enabled_and_preserves_unrelated_yaml() {
        let root = fixture();
        let path = root.join("system_config.yaml");
        let state = AgentTraceSettingsState::new(path.clone());
        assert_eq!(state.get().unwrap(), AgentTraceSettings { enabled: true });

        fs::create_dir_all(&root).unwrap();
        fs::write(&path, "debug:\n  enabled: false\n").unwrap();
        state.save(AgentTraceSettings { enabled: false }).unwrap();
        let saved = fs::read_to_string(&path).unwrap();
        assert!(saved.contains("debug:"));
        assert!(saved.contains("agent_trace:"));
        assert_eq!(state.get().unwrap(), AgentTraceSettings { enabled: false });
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_02_rejects_malformed_or_unknown_trace_fields() {
        let root = fixture();
        let path = root.join("system_config.yaml");
        fs::create_dir_all(&root).unwrap();
        fs::write(&path, "agent_trace: [broken\n").unwrap();
        let state = AgentTraceSettingsState::new(path.clone());
        assert_eq!(
            state.get().unwrap_err(),
            "AGENT_TRACE_SETTINGS_DOCUMENT_INVALID"
        );
        fs::write(&path, "agent_trace:\n  enabled: true\n  path: secret\n").unwrap();
        assert_eq!(
            state.get().unwrap_err(),
            "AGENT_TRACE_SETTINGS_FIELD_INVALID"
        );
        let _ = fs::remove_dir_all(root);
    }
}
