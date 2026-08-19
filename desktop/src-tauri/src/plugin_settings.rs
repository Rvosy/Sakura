use serde_json::Value;

const SNAPSHOT_KEYS: [&str; 5] = [
    "schemaVersion",
    "revision",
    "state",
    "reasonCode",
    "plugins",
];
const PLUGIN_KEYS: [&str; 13] = [
    "pluginId",
    "name",
    "version",
    "author",
    "description",
    "enabled",
    "required",
    "supported",
    "state",
    "reasonCode",
    "permissions",
    "unavailable",
    "sections",
];

fn has_exact_keys(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    })
}

pub fn validate_draft(value: &Value) -> Result<(), String> {
    if !has_exact_keys(value, &["enabledById", "settingsById"])
        || !valid_enabled_map(&value["enabledById"])
        || !valid_settings_map(&value["settingsById"])
    {
        return Err("PLUGIN_SETTINGS_DRAFT_INVALID".to_string());
    }
    Ok(())
}

pub fn validate_snapshot(value: &Value, saved: bool) -> Result<(), String> {
    let keys = if saved {
        vec![
            "schemaVersion",
            "revision",
            "state",
            "reasonCode",
            "plugins",
            "saved",
            "changePlan",
            "applicationState",
            "applicationReasonCode",
        ]
    } else {
        SNAPSHOT_KEYS.to_vec()
    };
    if !has_exact_keys(value, &keys)
        || value.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || !valid_revision(value.get("revision"))
        || !valid_state(value.get("state"))
        || !valid_reason(value.get("reasonCode"))
    {
        return Err("PLUGIN_SETTINGS_RESPONSE_INVALID".to_string());
    }
    let plugins = value["plugins"]
        .as_array()
        .filter(|items| items.len() <= 64)
        .ok_or_else(|| "PLUGIN_SETTINGS_RESPONSE_INVALID".to_string())?;
    for plugin in plugins {
        validate_plugin(plugin)?;
    }
    if saved
        && (value.get("saved").and_then(Value::as_bool) != Some(true)
            || !matches!(
                value.get("changePlan").and_then(Value::as_str),
                Some("applied" | "plugin_reload_required" | "core_restart_required")
            )
            || !matches!(
                value.get("applicationState").and_then(Value::as_str),
                Some("applied" | "restart_required" | "error")
            )
            || !valid_reason(value.get("applicationReasonCode")))
    {
        return Err("PLUGIN_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

pub fn validate_action_result(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .filter(|object| object.len() <= 2)
        .ok_or_else(|| "PLUGIN_SETTINGS_ACTION_RESPONSE_INVALID".to_string())?;
    if !serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 64 * 1024)
        || object
            .keys()
            .any(|key| !matches!(key.as_str(), "values" | "message"))
        || object.get("values").is_some_and(|value| !value.is_object())
        || object
            .get("message")
            .is_some_and(|value| !bounded_text(Some(value), 0, 240))
    {
        return Err("PLUGIN_SETTINGS_ACTION_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn validate_plugin(value: &Value) -> Result<(), String> {
    if !has_exact_keys(value, &PLUGIN_KEYS)
        || !bounded_identifier(value.get("pluginId"), 64)
        || !bounded_text(value.get("name"), 1, 120)
        || !bounded_text(value.get("version"), 1, 64)
        || !bounded_text(value.get("author"), 0, 120)
        || !bounded_text(value.get("description"), 0, 500)
        || !value["enabled"].is_boolean()
        || !value["required"].is_boolean()
        || !value["supported"].is_boolean()
        || !valid_state(value.get("state"))
        || !valid_reason(value.get("reasonCode"))
        || !valid_identifiers(&value["permissions"], 32)
        || !valid_identifiers(&value["unavailable"], 16)
        || !valid_sections(&value["sections"])
    {
        return Err("PLUGIN_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn valid_sections(value: &Value) -> bool {
    let Some(sections) = value.as_array().filter(|items| items.len() <= 16) else {
        return false;
    };
    sections.iter().all(|section| {
        let Some(object) = section.as_object() else {
            return false;
        };
        let allowed = [
            "sectionId",
            "title",
            "reasonCode",
            "fields",
            "values",
            "actions",
        ];
        object.len() == allowed.len()
            && allowed.iter().all(|key| object.contains_key(*key))
            && bounded_identifier(object.get("sectionId"), 64)
            && bounded_text(object.get("title"), 1, 120)
            && valid_reason(object.get("reasonCode"))
            && object
                .get("fields")
                .and_then(Value::as_array)
                .is_some_and(|items| items.len() <= 32 && items.iter().all(valid_field))
            && object.get("values").is_some_and(Value::is_object)
            && object
                .get("actions")
                .and_then(Value::as_array)
                .is_some_and(|items| items.len() <= 16 && items.iter().all(valid_action))
    })
}

fn valid_field(value: &Value) -> bool {
    let keys = [
        "key",
        "label",
        "type",
        "default",
        "description",
        "options",
        "minimum",
        "maximum",
        "step",
        "required",
        "readonly",
        "copyable",
        "restartRequired",
        "value",
    ];
    has_exact_keys(value, &keys)
        && bounded_identifier(value.get("key"), 64)
        && bounded_text(value.get("label"), 1, 120)
        && matches!(
            value.get("type").and_then(Value::as_str),
            Some("string" | "password" | "boolean" | "integer" | "number" | "select" | "readonly")
        )
        && bounded_text(value.get("description"), 0, 240)
        && value
            .get("options")
            .and_then(Value::as_array)
            .is_some_and(|items| items.len() <= 64 && items.iter().all(valid_option))
        && ["required", "readonly", "copyable", "restartRequired"]
            .iter()
            .all(|key| value.get(*key).is_some_and(Value::is_boolean))
        && serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 16 * 1024)
}

fn valid_option(value: &Value) -> bool {
    has_exact_keys(value, &["label", "value"])
        && bounded_text(value.get("label"), 1, 120)
        && value.get("value").is_some_and(|item| {
            item.is_string() || item.is_boolean() || item.is_i64() || item.is_u64() || item.is_f64()
        })
}

fn valid_action(value: &Value) -> bool {
    let keys = ["actionId", "label", "description", "danger"];
    has_exact_keys(value, &keys)
        && bounded_identifier(value.get("actionId"), 64)
        && bounded_text(value.get("label"), 1, 120)
        && bounded_text(value.get("description"), 0, 240)
        && value.get("danger").and_then(Value::as_bool) == Some(false)
}

fn valid_enabled_map(value: &Value) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() <= 64
            && object
                .iter()
                .all(|(key, value)| valid_identifier_text(key, 64) && value.is_boolean())
    })
}

fn valid_settings_map(value: &Value) -> bool {
    value.as_object().is_some_and(|plugins| {
        plugins.len() <= 64
            && plugins.iter().all(|(plugin_id, sections)| {
                valid_identifier_text(plugin_id, 64)
                    && sections.as_object().is_some_and(|sections| {
                        sections.len() <= 16
                            && sections.iter().all(|(section_id, values)| {
                                valid_identifier_text(section_id, 64) && values.is_object()
                            })
                    })
            })
    }) && serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 64 * 1024)
}

fn valid_identifiers(value: &Value, maximum: usize) -> bool {
    value.as_array().is_some_and(|items| {
        items.len() <= maximum && items.iter().all(|item| bounded_identifier(Some(item), 64))
    })
}

fn valid_revision(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        text.len() == 16
            && text
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}

fn valid_state(value: Option<&Value>) -> bool {
    matches!(
        value.and_then(Value::as_str),
        Some(
            "disabled"
                | "starting"
                | "ready"
                | "degraded"
                | "stopping"
                | "stopped"
                | "waiting"
                | "active"
                | "failed"
                | "conflict"
        )
    )
}

fn valid_reason(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        !text.is_empty()
            && text.len() <= 64
            && text
                .bytes()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    })
}

fn bounded_text(value: Option<&Value>, minimum: usize, maximum: usize) -> bool {
    value
        .and_then(Value::as_str)
        .is_some_and(|text| (minimum..=maximum).contains(&text.len()))
}

fn bounded_identifier(value: Option<&Value>, maximum: usize) -> bool {
    value
        .and_then(Value::as_str)
        .is_some_and(|text| valid_identifier_text(text, maximum))
}

fn valid_identifier_text(text: &str, maximum: usize) -> bool {
    !text.is_empty()
        && text.len() <= maximum
        && text
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{validate_action_result, validate_draft, validate_snapshot};

    fn snapshot() -> serde_json::Value {
        json!({
            "schemaVersion": 1,
            "revision": "0123456789abcdef",
            "state": "ready",
            "reasonCode": "READY",
            "plugins": [{
                "pluginId": "fixture_plugin", "name": "Fixture", "version": "1.0.0",
                "author": "Tests", "description": "Fixture", "enabled": true,
                "required": false, "supported": true, "state": "ready", "reasonCode": "READY",
                "permissions": ["tool"], "unavailable": [], "sections": []
            }]
        })
    }

    #[test]
    fn wp_4_04_plugin_dto_rejects_private_fields_and_unbounded_drafts() {
        assert!(validate_snapshot(&snapshot(), false).is_ok());
        assert!(validate_draft(&json!({"enabledById": {}, "settingsById": {}})).is_ok());
        let mut private = snapshot();
        private["plugins"][0]["entry"] = json!("private.module:Plugin");
        assert!(validate_snapshot(&private, false).is_err());
        assert!(
            validate_draft(&json!({"enabledById": {"bad/id": true}, "settingsById": {}})).is_err()
        );
        assert!(validate_action_result(&json!({
            "values": {"private": "x".repeat(70_000)}
        }))
        .is_err());
    }

    #[test]
    fn plugin_v3_states_and_local_apply_results_are_bounded() {
        let mut active = snapshot();
        active["plugins"][0]["state"] = json!("active");
        active["plugins"][0]["reasonCode"] = json!("ACTIVE");
        assert!(validate_snapshot(&active, false).is_ok());

        active["saved"] = json!(true);
        active["changePlan"] = json!("applied");
        active["applicationState"] = json!("applied");
        active["applicationReasonCode"] = json!("READY");
        assert!(validate_snapshot(&active, true).is_ok());
        active["changePlan"] = json!("worker_magic");
        assert!(validate_snapshot(&active, true).is_err());
    }
}
