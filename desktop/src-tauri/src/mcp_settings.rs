use serde_json::Value;

const SNAPSHOT_KEYS: [&str; 6] = [
    "schemaVersion",
    "desktop",
    "desktopEnabled",
    "configState",
    "reasonCode",
    "servers",
];
const SERVER_KEYS: [&str; 6] = [
    "serverId",
    "transport",
    "enabled",
    "state",
    "reasonCode",
    "toolCount",
];

fn has_exact_keys(value: &Value, keys: &[&str]) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

pub fn validate_draft(value: &Value) -> Result<(), String> {
    if !has_exact_keys(value, &["desktopEnabled"]) || !value["desktopEnabled"].is_boolean() {
        return Err("MCP_SETTINGS_DRAFT_INVALID".to_string());
    }
    Ok(())
}

pub fn validate_snapshot(value: &Value, saved: bool) -> Result<(), String> {
    let keys = if saved {
        vec![
            "schemaVersion",
            "desktop",
            "desktopEnabled",
            "configState",
            "reasonCode",
            "servers",
            "saved",
            "changePlan",
        ]
    } else {
        SNAPSHOT_KEYS.to_vec()
    };
    if !has_exact_keys(value, &keys)
        || value.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || !value["desktopEnabled"].is_boolean()
        || !matches!(
            value.get("configState").and_then(Value::as_str),
            Some("valid" | "missing" | "invalid")
        )
        || !valid_reason(value.get("reasonCode"))
    {
        return Err("MCP_SETTINGS_RESPONSE_INVALID".to_string());
    }
    validate_desktop(&value["desktop"])?;
    validate_servers(&value["servers"])?;
    if saved
        && (value.get("saved").and_then(Value::as_bool) != Some(true)
            || value.get("changePlan").and_then(Value::as_str) != Some("applied"))
    {
        return Err("MCP_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn validate_desktop(value: &Value) -> Result<(), String> {
    if !has_exact_keys(value, &["supported", "label", "experimentalText"])
        || !value["supported"].is_boolean()
        || !bounded_text(value.get("label"), 1, 80)
        || !bounded_text(value.get("experimentalText"), 0, 240)
    {
        return Err("MCP_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn validate_servers(value: &Value) -> Result<(), String> {
    let servers = value
        .as_array()
        .filter(|servers| servers.len() <= 16)
        .ok_or_else(|| "MCP_SETTINGS_RESPONSE_INVALID".to_string())?;
    for server in servers {
        if !has_exact_keys(server, &SERVER_KEYS)
            || !bounded_identifier(server.get("serverId"), 64)
            || !matches!(
                server.get("transport").and_then(Value::as_str),
                Some("stdio" | "sse")
            )
            || !server["enabled"].is_boolean()
            || !matches!(
                server.get("state").and_then(Value::as_str),
                Some("disabled" | "starting" | "ready" | "degraded" | "stopping" | "stopped")
            )
            || !valid_reason(server.get("reasonCode"))
            || server
                .get("toolCount")
                .and_then(Value::as_u64)
                .is_none_or(|count| count > 512)
        {
            return Err("MCP_SETTINGS_RESPONSE_INVALID".to_string());
        }
    }
    Ok(())
}

fn bounded_text(value: Option<&Value>, minimum: usize, maximum: usize) -> bool {
    value
        .and_then(Value::as_str)
        .is_some_and(|text| (minimum..=maximum).contains(&text.len()))
}

fn bounded_identifier(value: Option<&Value>, maximum: usize) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        !text.is_empty()
            && text.len() <= maximum
            && text
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    })
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

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::{validate_draft, validate_snapshot};

    fn snapshot() -> Value {
        json!({
            "schemaVersion": 1,
            "desktop": {
                "supported": true,
                "label": "macOS MCP",
                "experimentalText": "实验性功能"
            },
            "desktopEnabled": false,
            "configState": "valid",
            "reasonCode": "READY",
            "servers": [{
                "serverId": "macos",
                "transport": "stdio",
                "enabled": false,
                "state": "disabled",
                "reasonCode": "SERVER_DISABLED",
                "toolCount": 0
            }]
        })
    }

    #[test]
    fn wp_4_03_mcp_settings_are_exact_bounded_and_sanitized() {
        assert!(validate_draft(&json!({"desktopEnabled": true})).is_ok());
        assert!(validate_snapshot(&snapshot(), false).is_ok());

        let mut private = snapshot();
        private["servers"][0]["headers"] = json!({"Authorization": "private"});
        assert!(validate_snapshot(&private, false).is_err());

        let mut invalid = snapshot();
        invalid["servers"][0]["reasonCode"] = json!("contains private detail");
        assert!(validate_snapshot(&invalid, false).is_err());
    }
}
