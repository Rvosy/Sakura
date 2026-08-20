use serde_json::Value;

const SNAPSHOT_KEYS: [&str; 5] = [
    "schemaVersion",
    "revision",
    "state",
    "reasonCode",
    "plugins",
];
const PLUGIN_KEYS: [&str; 15] = [
    "pluginId",
    "name",
    "version",
    "author",
    "description",
    "enabled",
    "required",
    "supported",
    "source",
    "canUninstall",
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
                Some("applied" | "plugin_reload_required")
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

pub fn validate_management_result(value: &Value) -> Result<(), String> {
    let mut keys = SNAPSHOT_KEYS.to_vec();
    keys.extend(["managementAction", "pluginId"]);
    if !has_exact_keys(value, &keys)
        || !matches!(
            value.get("managementAction").and_then(Value::as_str),
            Some("installed" | "uninstalled")
        )
        || !bounded_identifier(value.get("pluginId"), 64)
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let mut snapshot = value.clone();
    let object = snapshot
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.remove("managementAction");
    object.remove("pluginId");
    validate_snapshot(&snapshot, false)
        .map_err(|_| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())
}

pub fn validate_collection_request(
    operation: &str,
    plugin_id: &str,
    section_id: &str,
    collection_id: &str,
    payload: &Value,
) -> Result<(), String> {
    if ![plugin_id, section_id, collection_id]
        .iter()
        .all(|value| valid_identifier_text(value, 64))
        || !serde_json::to_vec(payload).is_ok_and(|bytes| bytes.len() <= 256 * 1024)
    {
        return Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string());
    }
    let valid = match operation {
        "query" => {
            has_exact_keys(payload, &["cursor", "limit", "search", "filters"])
                && payload
                    .get("cursor")
                    .is_some_and(|value| value.is_null() || bounded_text(Some(value), 0, 256))
                && payload
                    .get("limit")
                    .and_then(Value::as_u64)
                    .is_some_and(|value| (1..=100).contains(&value))
                && bounded_text(payload.get("search"), 0, 200)
                && payload.get("filters").is_some_and(|value| {
                    value.as_object().is_some_and(|items| {
                        items.len() <= 8
                            && items.iter().all(|(key, value)| {
                                valid_identifier_text(key, 64) && valid_scalar(value)
                            })
                    })
                })
        }
        "create" => {
            has_exact_keys(payload, &["values"])
                && valid_collection_values(&payload["values"], 16, 128 * 1024)
        }
        "update" => {
            has_exact_keys(payload, &["itemId", "values"])
                && bounded_text(payload.get("itemId"), 1, 200)
                && valid_collection_values(&payload["values"], 16, 128 * 1024)
        }
        "delete" => {
            has_exact_keys(payload, &["itemId"]) && bounded_text(payload.get("itemId"), 1, 200)
        }
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string())
    }
}

pub fn validate_collection_result(operation: &str, value: &Value) -> Result<(), String> {
    if !serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 256 * 1024) {
        return Err("PLUGIN_COLLECTION_RESPONSE_INVALID".to_string());
    }
    let valid = match operation {
        "query" => {
            has_exact_keys(value, &["items", "nextCursor", "total"])
                && value
                    .get("items")
                    .and_then(Value::as_array)
                    .is_some_and(|items| {
                        items.len() <= 100 && items.iter().all(valid_collection_item)
                    })
                && value
                    .get("nextCursor")
                    .is_some_and(|item| item.is_null() || bounded_text(Some(item), 0, 256))
                && value
                    .get("total")
                    .is_some_and(|item| item.is_null() || item.as_u64().is_some())
        }
        "create" | "update" => valid_collection_item(value),
        "delete" => {
            has_exact_keys(value, &["deleted"])
                && value.get("deleted").is_some_and(Value::is_boolean)
        }
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err("PLUGIN_COLLECTION_RESPONSE_INVALID".to_string())
    }
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
        || !matches!(
            value.get("source").and_then(Value::as_str),
            Some("bundled" | "user")
        )
        || !value["canUninstall"].is_boolean()
        || value["canUninstall"].as_bool()
            != Some(
                value["source"].as_str() == Some("user")
                    && !value["required"].as_bool().unwrap_or(true),
            )
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
            "collections",
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
            && object
                .get("collections")
                .and_then(Value::as_array)
                .is_some_and(|items| items.len() <= 4 && items.iter().all(valid_collection))
    })
}

fn valid_collection(value: &Value) -> bool {
    let keys = [
        "collectionId",
        "title",
        "description",
        "columns",
        "fields",
        "filters",
        "searchable",
        "pageSize",
        "canCreate",
        "canUpdate",
        "canDelete",
        "deleteConfirmation",
    ];
    has_exact_keys(value, &keys)
        && bounded_identifier(value.get("collectionId"), 64)
        && bounded_text(value.get("title"), 1, 120)
        && bounded_text(value.get("description"), 0, 240)
        && value
            .get("columns")
            .and_then(Value::as_array)
            .is_some_and(|items| {
                (1..=12).contains(&items.len()) && items.iter().all(valid_collection_column)
            })
        && value
            .get("fields")
            .and_then(Value::as_array)
            .is_some_and(|items| items.len() <= 16 && items.iter().all(valid_collection_field))
        && value
            .get("filters")
            .and_then(Value::as_array)
            .is_some_and(|items| items.len() <= 8 && items.iter().all(valid_collection_filter))
        && value.get("searchable").is_some_and(Value::is_boolean)
        && value
            .get("pageSize")
            .and_then(Value::as_u64)
            .is_some_and(|item| (1..=100).contains(&item))
        && ["canCreate", "canUpdate", "canDelete"]
            .iter()
            .all(|key| value.get(*key).is_some_and(Value::is_boolean))
        && bounded_text(value.get("deleteConfirmation"), 0, 240)
}

fn valid_collection_column(value: &Value) -> bool {
    has_exact_keys(value, &["key", "label", "type", "maxLength"])
        && bounded_identifier(value.get("key"), 64)
        && bounded_text(value.get("label"), 1, 120)
        && matches!(
            value.get("type").and_then(Value::as_str),
            Some("string" | "number" | "boolean" | "datetime")
        )
        && valid_max_length(value)
}

fn valid_collection_field(value: &Value) -> bool {
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
        "maxLength",
        "required",
        "readonly",
        "copyable",
        "restartRequired",
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
        && valid_max_length(value)
        && ["required", "readonly", "copyable", "restartRequired"]
            .iter()
            .all(|key| value.get(*key).is_some_and(Value::is_boolean))
        && serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 16 * 1024)
}

fn valid_collection_filter(value: &Value) -> bool {
    has_exact_keys(value, &["key", "label", "options"])
        && bounded_identifier(value.get("key"), 64)
        && bounded_text(value.get("label"), 1, 120)
        && value
            .get("options")
            .and_then(Value::as_array)
            .is_some_and(|items| {
                !items.is_empty() && items.len() <= 64 && items.iter().all(valid_option)
            })
}

fn valid_collection_item(value: &Value) -> bool {
    has_exact_keys(value, &["itemId", "values"])
        && bounded_text(value.get("itemId"), 1, 200)
        && valid_collection_values(&value["values"], 28, 128 * 1024)
}

fn valid_collection_values(value: &Value, maximum_items: usize, maximum_bytes: usize) -> bool {
    value.as_object().is_some_and(|items| {
        items.len() <= maximum_items
            && items
                .iter()
                .all(|(key, value)| valid_identifier_text(key, 64) && valid_scalar(value))
    }) && serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= maximum_bytes)
}

fn valid_scalar(value: &Value) -> bool {
    value.is_null()
        || value.is_boolean()
        || value.is_number()
        || value.as_str().is_some_and(|text| text.len() <= 64 * 1024)
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
        "maxLength",
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
        && valid_max_length(value)
        && ["required", "readonly", "copyable", "restartRequired"]
            .iter()
            .all(|key| value.get(*key).is_some_and(Value::is_boolean))
        && serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 16 * 1024)
}

fn valid_max_length(value: &Value) -> bool {
    match value.get("maxLength") {
        Some(item) if item.is_null() => true,
        Some(item) => {
            matches!(
                value.get("type").and_then(Value::as_str),
                Some("string" | "password" | "readonly")
            ) && item
                .as_u64()
                .is_some_and(|length| (1..=16_384).contains(&length))
        }
        None => false,
    }
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

    use super::{
        validate_action_result, validate_collection_request, validate_collection_result,
        validate_draft, validate_management_result, validate_snapshot,
    };

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
                "source": "bundled", "canUninstall": false,
                "permissions": [], "unavailable": [], "sections": []
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

    #[test]
    fn local_plugin_management_results_are_exact_and_source_bounded() {
        let mut installed = snapshot();
        installed["managementAction"] = json!("installed");
        installed["pluginId"] = json!("fixture_plugin");
        assert!(validate_management_result(&installed).is_ok());
        installed["plugins"][0]["source"] = json!("user");
        installed["plugins"][0]["canUninstall"] = json!(true);
        assert!(validate_management_result(&installed).is_ok());
        installed["plugins"][0]["canUninstall"] = json!(false);
        assert!(validate_management_result(&installed).is_err());
        installed["plugins"][0]["canUninstall"] = json!(true);
        installed["sourcePath"] = json!("/private/plugin.zip");
        assert!(validate_management_result(&installed).is_err());
    }

    #[test]
    fn plugin_collection_descriptor_and_crud_payloads_are_bounded() {
        let mut value = snapshot();
        value["plugins"][0]["sections"] = json!([{
            "sectionId": "data",
            "title": "Data",
            "reasonCode": "READY",
            "fields": [],
            "values": {},
            "actions": [],
            "collections": [{
                "collectionId": "entries",
                "title": "Entries",
                "description": "Fixture rows",
                "columns": [{"key": "content", "label": "Content", "type": "string", "maxLength": 16384}],
                "fields": [{
                    "key": "content", "label": "Content", "type": "string", "default": null,
                    "description": "", "options": [], "minimum": null, "maximum": null, "step": null,
                    "maxLength": 16384,
                    "required": true, "readonly": false, "copyable": false, "restartRequired": false
                }],
                "filters": [],
                "searchable": true,
                "pageSize": 25,
                "canCreate": true,
                "canUpdate": true,
                "canDelete": true,
                "deleteConfirmation": "Delete this row?"
            }]
        }]);
        assert!(validate_snapshot(&value, false).is_ok());
        assert!(validate_collection_request(
            "query",
            "fixture_plugin",
            "data",
            "entries",
            &json!({"cursor": null, "limit": 25, "search": "needle", "filters": {}}),
        )
        .is_ok());
        assert!(validate_collection_request(
            "query",
            "fixture_plugin",
            "data",
            "entries",
            &json!({"cursor": null, "limit": 101, "search": "", "filters": {}}),
        )
        .is_err());
        assert!(validate_collection_result(
            "query",
            &json!({
                "items": [{"itemId": "one", "values": {"content": "hello"}}],
                "nextCursor": null,
                "total": 1
            }),
        )
        .is_ok());
        assert!(validate_collection_result(
            "query",
            &json!({
                "items": [{"itemId": "one", "values": {"private": {"nested": true}}}],
                "nextCursor": null,
                "total": 1
            }),
        )
        .is_err());
    }
}
