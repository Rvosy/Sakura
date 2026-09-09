use serde_json::{json, Value};
use tauri::{State, WebviewWindow};

use crate::{
    product_shell::{self, assert_settings_identity},
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};

const SNAPSHOT_KEYS: [&str; 5] = [
    "schemaVersion",
    "revision",
    "state",
    "reasonCode",
    "plugins",
];
const PLUGIN_KEYS: [&str; 17] = [
    "installId",
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
    "provides",
    "requires",
    "missingServices",
    "state",
    "reasonCode",
    "sections",
];

fn has_exact_keys(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    })
}

fn validate_settings_save_request(
    plugin_id: &str,
    section_id: &str,
    values: &Value,
) -> Result<(), String> {
    if !valid_identifier_text(plugin_id, 64)
        || !valid_identifier_text(section_id, 64)
        || !values.is_object()
        || !serde_json::to_vec(values).is_ok_and(|bytes| bytes.len() <= 64 * 1024)
    {
        return Err("PLUGIN_SETTINGS_SAVE_REQUEST_INVALID".to_string());
    }
    Ok(())
}

fn validate_enabled_request(revision: &str, install_id: &str) -> Result<(), String> {
    if !valid_revision(Some(&Value::String(revision.to_string())))
        || !valid_install_id(Some(&Value::String(install_id.to_string())))
    {
        return Err("PLUGIN_ENABLED_REQUEST_INVALID".to_string());
    }
    Ok(())
}

fn validate_snapshot(value: &Value) -> Result<(), String> {
    if !serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 512 * 1024)
        || !has_exact_keys(value, &SNAPSHOT_KEYS)
        || value.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || !valid_revision(value.get("revision"))
        || !valid_worker_state(value.get("state"))
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
    Ok(())
}

fn validate_action_result(value: &Value) -> Result<(), String> {
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

fn validate_management_result(value: &Value) -> Result<(), String> {
    let mut keys = SNAPSHOT_KEYS.to_vec();
    let action = value.get("managementAction").and_then(Value::as_str);
    keys.extend(["managementAction", "installId", "pluginId"]);
    if action == Some("enabled_changed") {
        keys.extend(["desiredSaved", "applicationState", "applicationReasonCode"]);
    }
    if !has_exact_keys(value, &keys)
        || !matches!(
            action,
            Some("installed" | "uninstalled" | "enabled_changed")
        )
        || !valid_install_id(value.get("installId"))
        || !valid_nullable_plugin_id(value.get("pluginId"))
        || (action == Some("installed") && value.get("pluginId").is_some_and(Value::is_null))
        || (action == Some("enabled_changed")
            && (value.get("desiredSaved").and_then(Value::as_bool) != Some(true)
                || !matches!(
                    value.get("applicationState").and_then(Value::as_str),
                    Some("applied" | "error")
                )
                || !valid_reason(value.get("applicationReasonCode"))))
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let mut snapshot = value.clone();
    let object = snapshot
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.remove("managementAction");
    object.remove("installId");
    object.remove("pluginId");
    object.remove("desiredSaved");
    object.remove("applicationState");
    object.remove("applicationReasonCode");
    validate_snapshot(&snapshot).map_err(|_| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())
}

fn validate_settings_save_result(value: &Value) -> Result<(), String> {
    if !has_exact_keys(
        value,
        &[
            "saved",
            "pluginId",
            "sectionId",
            "changePlan",
            "applicationState",
            "applicationReasonCode",
        ],
    ) || value.get("saved").and_then(Value::as_bool) != Some(true)
        || !bounded_identifier(value.get("pluginId"), 64)
        || !bounded_identifier(value.get("sectionId"), 64)
        || !matches!(
            value.get("changePlan").and_then(Value::as_str),
            Some("applied")
        )
        || !matches!(
            value.get("applicationState").and_then(Value::as_str),
            Some("applied")
        )
        || !valid_reason(value.get("applicationReasonCode"))
    {
        return Err("PLUGIN_SETTINGS_SAVE_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn validate_collection_request(
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
    let valid =
        matches!(operation, "query" | "create" | "update" | "delete") && payload.is_object();
    if valid {
        Ok(())
    } else {
        Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string())
    }
}

fn validate_collection_result(operation: &str, value: &Value) -> Result<(), String> {
    if !serde_json::to_vec(value).is_ok_and(|bytes| bytes.len() <= 256 * 1024) {
        return Err("PLUGIN_COLLECTION_RESPONSE_INVALID".to_string());
    }
    let valid = matches!(operation, "query" | "create" | "update" | "delete") && value.is_object();
    if valid {
        Ok(())
    } else {
        Err("PLUGIN_COLLECTION_RESPONSE_INVALID".to_string())
    }
}

fn validate_plugin(value: &Value) -> Result<(), String> {
    let mut keys = PLUGIN_KEYS.to_vec();
    if value.get("presentation").is_some() {
        keys.push("presentation");
    }
    if !has_exact_keys(value, &keys)
        || value.get("presentation").is_some_and(|presentation| {
            let keys: &[&str] = if presentation.get("icon").is_some() {
                &["kind", "category", "icon"]
            } else {
                &["kind", "category"]
            };
            !has_exact_keys(presentation, keys)
                || !matches!(
                    presentation["kind"].as_str(),
                    Some("extension" | "provider" | "infrastructure")
                )
                || !matches!(
                    presentation["category"].as_str(),
                    Some("model" | "voice" | "memory" | "tools" | "connectivity" | "other")
                )
                || presentation.get("icon").is_some_and(|icon| {
                    !icon.as_str().is_some_and(|name| {
                        name.is_empty()
                            || (name.len() <= 64
                                && name.as_bytes()[0].is_ascii_lowercase()
                                && name.bytes().all(|ch| {
                                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == b'-'
                                }))
                    })
                })
        })
        || !valid_install_id(value.get("installId"))
        || !valid_nullable_plugin_id(value.get("pluginId"))
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
        || (value["source"].as_str() == Some("user") && value["required"].as_bool() != Some(false))
        || value["canUninstall"].as_bool() != Some(value["source"].as_str() == Some("user"))
        || !valid_identifier_list(value.get("provides"))
        || !valid_identifier_list(value.get("requires"))
        || !valid_identifier_list(value.get("missingServices"))
        || !valid_plugin_state(value.get("state"))
        || !valid_reason(value.get("reasonCode"))
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
    sections.iter().all(valid_section)
}

fn valid_section(section: &Value) -> bool {
    let keys = [
        "sectionId",
        "title",
        "surface",
        "reasonCode",
        "fields",
        "values",
        "actions",
        "collections",
    ];
    let Some(object) = section
        .as_object()
        .filter(|_| has_exact_keys(section, &keys))
    else {
        return false;
    };
    let Some(fields) = object
        .get("fields")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 32)
    else {
        return false;
    };
    let Some(actions) = object
        .get("actions")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 16)
    else {
        return false;
    };
    let action_ids = actions
        .iter()
        .filter_map(|action| action.get("actionId").and_then(Value::as_str))
        .collect::<std::collections::HashSet<_>>();
    let field_ids = fields
        .iter()
        .filter_map(|field| field.get("key").and_then(Value::as_str))
        .collect::<std::collections::HashSet<_>>();
    let Some(values) = object.get("values").and_then(Value::as_object) else {
        return false;
    };
    bounded_identifier(object.get("sectionId"), 64)
        && bounded_text(object.get("title"), 1, 120)
        && object
            .get("surface")
            .is_some_and(|value| value.is_null() || bounded_identifier(Some(value), 64))
        && valid_reason(object.get("reasonCode"))
        && actions.iter().all(valid_settings_action)
        && action_ids.len() == actions.len()
        && field_ids.len() == fields.len()
        && fields.iter().all(|field| {
            field.get("enabledWhen").is_none_or(|condition| {
                condition.is_null()
                    || condition
                        .get("field")
                        .and_then(Value::as_str)
                        .is_some_and(|key| field_ids.contains(key))
            })
        })
        && values.len() == fields.len()
        && fields.iter().all(|field| {
            field
                .get("key")
                .and_then(Value::as_str)
                .and_then(|key| values.get(key).map(|value| value == &field["value"]))
                == Some(true)
        })
        && fields
            .iter()
            .all(|field| valid_settings_field(field, &action_ids))
        && object
            .get("collections")
            .and_then(Value::as_array)
            .is_some_and(|items| items.len() <= 4)
        && serde_json::to_vec(section).is_ok_and(|bytes| bytes.len() <= 128 * 1024)
}

fn valid_settings_action(action: &Value) -> bool {
    has_exact_keys(action, &["actionId", "label", "description", "danger"])
        && bounded_identifier(action.get("actionId"), 64)
        && bounded_text(action.get("label"), 1, 120)
        && bounded_text(action.get("description"), 0, 240)
        && action.get("danger").and_then(Value::as_bool) == Some(false)
}

fn valid_settings_field(
    field: &Value,
    declared_action_ids: &std::collections::HashSet<&str>,
) -> bool {
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
        "placement",
        "actionIds",
        "enabledWhen",
        "required",
        "readonly",
        "copyable",
        "restartRequired",
        "value",
    ];
    let Some(object) = field.as_object().filter(|_| has_exact_keys(field, &keys)) else {
        return false;
    };
    let Some(kind) = object.get("type").and_then(Value::as_str) else {
        return false;
    };
    let Some(action_ids) = object
        .get("actionIds")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 8)
    else {
        return false;
    };
    let action_refs_valid = action_ids.iter().all(|action_id| {
        action_id.as_str().is_some_and(|text| {
            valid_identifier_text(text, 64) && declared_action_ids.contains(text)
        })
    });
    let unique_action_ids = action_ids
        .iter()
        .filter_map(Value::as_str)
        .collect::<std::collections::HashSet<_>>()
        .len()
        == action_ids.len();
    let required = object.get("required").and_then(Value::as_bool) == Some(true);
    bounded_identifier(object.get("key"), 64)
        && bounded_text(object.get("label"), 1, 120)
        && matches!(
            kind,
            "string"
                | "password"
                | "boolean"
                | "integer"
                | "number"
                | "select"
                | "readonly"
                | "status"
                | "resource"
        )
        && bounded_text(object.get("description"), 0, 240)
        && object
            .get("options")
            .and_then(Value::as_array)
            .is_some_and(|items| items.len() <= 64)
        && matches!(
            object.get("placement").and_then(Value::as_str),
            Some("row" | "advanced" | "section_header")
        )
        && (object.get("placement").and_then(Value::as_str) != Some("section_header")
            || kind == "status")
        && action_refs_valid
        && unique_action_ids
        && (kind == "resource" || action_ids.is_empty())
        && valid_enabled_when(object.get("enabledWhen"), object.get("key"))
        && ["required", "readonly", "copyable", "restartRequired"]
            .iter()
            .all(|key| object.get(*key).is_some_and(Value::is_boolean))
        && (!matches!(kind, "status" | "resource")
            || object.get("readonly").and_then(Value::as_bool) == Some(true))
        && (!required
            || (!object.get("default").is_some_and(Value::is_null)
                && !object.get("value").is_some_and(Value::is_null)))
        && valid_settings_display_value(kind, object.get("default"), action_ids)
        && valid_settings_display_value(kind, object.get("value"), action_ids)
        && serde_json::to_vec(field).is_ok_and(|bytes| bytes.len() <= 16 * 1024)
}

fn valid_enabled_when(condition: Option<&Value>, own_key: Option<&Value>) -> bool {
    let Some(condition) = condition else {
        return false;
    };
    if condition.is_null() {
        return true;
    }
    let Some(object) = condition
        .as_object()
        .filter(|_| has_exact_keys(condition, &["field", "equals"]))
    else {
        return false;
    };
    bounded_identifier(object.get("field"), 64)
        && object.get("field") != own_key
        && bounded_text(object.get("equals"), 0, 200)
}

fn valid_settings_display_value(kind: &str, value: Option<&Value>, action_ids: &[Value]) -> bool {
    if value.is_some_and(Value::is_null) {
        return true;
    }
    match kind {
        "status" => value.is_some_and(|value| {
            has_exact_keys(value, &["state", "label", "message"])
                && matches!(
                    value.get("state").and_then(Value::as_str),
                    Some("neutral" | "ready" | "working" | "warning" | "error")
                )
                && bounded_text(value.get("label"), 1, 120)
                && bounded_text(value.get("message"), 0, 240)
        }),
        "resource" => value.is_some_and(|value| {
            let keys = [
                "applicability",
                "subtitle",
                "ready",
                "taskState",
                "message",
                "detail",
                "progress",
                "availableActionIds",
            ];
            let allowed = action_ids
                .iter()
                .filter_map(Value::as_str)
                .collect::<std::collections::HashSet<_>>();
            has_exact_keys(value, &keys)
                && matches!(
                    value.get("applicability").and_then(Value::as_str),
                    Some("required" | "not_required" | "unsupported")
                )
                && bounded_text(value.get("subtitle"), 0, 512)
                && value.get("ready").is_some_and(Value::is_boolean)
                && matches!(
                    value.get("taskState").and_then(Value::as_str),
                    Some("idle" | "queued" | "running" | "succeeded" | "failed" | "cancelled")
                )
                && bounded_text(value.get("message"), 0, 240)
                && bounded_text(value.get("detail"), 0, 240)
                && value.get("progress").is_some_and(|progress| {
                    progress.is_null() || progress.as_u64().is_some_and(|number| number <= 100)
                })
                && value
                    .get("availableActionIds")
                    .and_then(Value::as_array)
                    .is_some_and(|items| {
                        let unique = items
                            .iter()
                            .filter_map(Value::as_str)
                            .collect::<std::collections::HashSet<_>>();
                        items.len() <= 8
                            && unique.len() == items.len()
                            && items.iter().all(|item| {
                                item.as_str().is_some_and(|text| allowed.contains(text))
                            })
                    })
        }),
        _ => true,
    }
}

fn valid_install_id(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        let Some(directory) = text.strip_prefix("pi_user_").or_else(|| text.strip_prefix("pi_bundled_")) else {
            return false;
        };
        (2..=2048).contains(&directory.len())
            && directory.len() % 2 == 0
            && directory
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}

fn valid_nullable_plugin_id(value: Option<&Value>) -> bool {
    value.is_some_and(|item| item.is_null() || bounded_identifier(Some(item), 64))
}

fn valid_revision(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        text.len() == 16
            && text
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}

fn valid_worker_state(value: Option<&Value>) -> bool {
    matches!(
        value.and_then(Value::as_str),
        Some(
            "disabled"
                | "starting"
                | "ready"
                | "degraded"
                | "stopping"
                | "stopped"
                | "active"
                | "failed"
        )
    )
}

fn valid_plugin_state(value: Option<&Value>) -> bool {
    matches!(
        value.and_then(Value::as_str),
        Some("disabled" | "active" | "failed")
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

fn valid_identifier_list(value: Option<&Value>) -> bool {
    value
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 64)
        .is_some_and(|items| items.iter().all(|item| bounded_identifier(Some(item), 200)))
}

fn valid_identifier_text(text: &str, maximum: usize) -> bool {
    !text.is_empty()
        && text.len() <= maximum
        && text
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
}

#[tauri::command]
pub(crate) async fn settings_plugins_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.get",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    validate_snapshot(&payload)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    plugin_id: String,
    section_id: String,
    values: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    validate_settings_save_request(&plugin_id, &section_id, &values)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.save",
        json!({"pluginId": plugin_id, "sectionId": section_id, "values": values}),
        std::time::Duration::from_secs(8),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_settings_save_result(&payload)?;
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_enabled_set(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    install_id: String,
    enabled: bool,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    validate_enabled_request(&revision, &install_id)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.enabled.set",
        json!({"revision": revision, "installId": install_id, "enabled": enabled}),
        std::time::Duration::from_secs(12),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("enabled_changed")
        || payload.get("installId").and_then(Value::as_str) != Some(install_id.as_str())
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_action(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    plugin_id: String,
    section_id: String,
    action_id: String,
    values: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.action",
        json!({"pluginId": plugin_id, "sectionId": section_id, "actionId": action_id, "values": values}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_action_result(&payload)?;
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_install(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    source_kind: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let selected = match source_kind.as_str() {
        "zip" => {
            rfd::AsyncFileDialog::new()
                .add_filter("Sakura 插件 ZIP", &["zip"])
                .pick_file()
                .await
        }
        "folder" => rfd::AsyncFileDialog::new().pick_folder().await,
        _ => return Err("PLUGIN_INSTALL_SOURCE_INVALID".to_string()),
    };
    let Some(selected) = selected else {
        return Ok(json!({"cancelled": true}));
    };
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let source_path = selected
        .path()
        .to_str()
        .filter(|value| !value.is_empty() && value.len() <= 4096)
        .ok_or_else(|| "PLUGIN_INSTALL_SOURCE_INVALID".to_string())?;
    if source_kind == "zip"
        && selected
            .path()
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("zip"))
    {
        return Err("PLUGIN_INSTALL_SOURCE_INVALID".to_string());
    }
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.install",
        json!({
            "revision": revision,
            "sourceKind": source_kind,
            "sourcePath": source_path,
        }),
        std::time::Duration::from_secs(30),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("installed") {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_uninstall(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    install_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.uninstall",
        json!({"revision": revision, "installId": install_id}),
        std::time::Duration::from_secs(30),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("uninstalled")
        || payload.get("installId").and_then(Value::as_str) != Some(install_id.as_str())
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_plugins_collection(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    operation: String,
    plugin_id: String,
    section_id: String,
    collection_id: String,
    payload: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    validate_collection_request(
        &operation,
        &plugin_id,
        &section_id,
        &collection_id,
        &payload,
    )?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut request_payload = payload
        .as_object()
        .cloned()
        .ok_or_else(|| "PLUGIN_COLLECTION_REQUEST_INVALID".to_string())?;
    request_payload.insert("pluginId".to_string(), json!(plugin_id));
    request_payload.insert("sectionId".to_string(), json!(section_id));
    request_payload.insert("collectionId".to_string(), json!(collection_id));
    let request_name = match operation.as_str() {
        "query" => "plugins.collection.query",
        "create" => "plugins.collection.create",
        "update" => "plugins.collection.update",
        "delete" => "plugins.collection.delete",
        _ => return Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string()),
    };
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        request_name,
        Value::Object(request_payload),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let result = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_collection_result(&operation, &result)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        validate_action_result, validate_collection_request, validate_collection_result,
        validate_management_result, validate_settings_save_result, validate_snapshot,
    };

    #[test]
    fn presentation_is_optional_bounded_display_metadata() {
        let mut value = snapshot();
        assert!(validate_snapshot(&value).is_ok());
        value["plugins"][0]["presentation"] = json!({"kind": "provider", "category": "voice"});
        assert!(validate_snapshot(&value).is_ok());
        for icon in ["", "brain", "future-icon"] {
            value["plugins"][0]["presentation"]["icon"] = json!(icon);
            assert!(validate_snapshot(&value).is_ok());
        }
        for icon in [
            json!(null),
            json!([]),
            json!("../brain.svg"),
            json!("<svg>"),
            json!("x".repeat(65)),
            json!("brain\n"),
        ] {
            value["plugins"][0]["presentation"]["icon"] = icon;
            assert!(validate_snapshot(&value).is_err());
        }
        value["plugins"][0]["presentation"]["icon"] = json!("brain");
        value["plugins"][0]["presentation"]["category"] = json!("unknown");
        assert!(validate_snapshot(&value).is_err());
        value["plugins"][0]["presentation"] =
            json!({"kind": "provider", "category": "voice", "path": "private"});
        assert!(validate_snapshot(&value).is_err());
    }

    fn snapshot() -> serde_json::Value {
        json!({
            "schemaVersion": 1,
            "revision": "0123456789abcdef",
            "state": "ready",
            "reasonCode": "READY",
            "plugins": [{
                "installId": "pi_bundled_666978747572655f706c7567696e",
                "pluginId": "fixture_plugin", "name": "Fixture", "version": "1.0.0",
                "author": "Tests", "description": "Fixture", "enabled": true,
                "required": false, "supported": true, "state": "active", "reasonCode": "ACTIVE",
                "source": "bundled", "canUninstall": false,
                "provides": ["fixture.service"], "requires": ["sakura.host.settings"],
                "missingServices": [],
                "sections": []
            }]
        })
    }

    #[test]
    fn encoded_install_ids_remain_bounded_across_the_desktop_boundary() {
        let mut value = snapshot();
        value["plugins"][0]["installId"] = json!(format!("pi_bundled_{}", "e8a792e889b2".repeat(35)));
        assert!(validate_snapshot(&value).is_ok());
        for invalid in ["pi_user_", "pi_user_a", "pi_other_6162", "pi_user_../a"] {
            value["plugins"][0]["installId"] = json!(invalid);
            assert!(validate_snapshot(&value).is_err());
        }
        value["plugins"][0]["installId"] = json!(format!("pi_user_{}", "aa".repeat(1025)));
        assert!(validate_snapshot(&value).is_err());
    }

    #[test]
    fn wp_4_04_plugin_dto_rejects_private_fields_and_unbounded_drafts() {
        assert!(validate_snapshot(&snapshot()).is_ok());
        let mut private = snapshot();
        private["plugins"][0]["entry"] = json!("private.module:Plugin");
        assert!(validate_snapshot(&private).is_err());
        let mut invalid_service = snapshot();
        invalid_service["plugins"][0]["missingServices"] = json!(["invalid/service"]);
        assert!(validate_snapshot(&invalid_service).is_err());
        assert!(validate_action_result(&json!({
            "values": {"private": "x".repeat(70_000)}
        }))
        .is_err());
    }

    #[test]
    fn plugin_settings_save_result_requires_the_current_applied_envelope() {
        let saved = json!({
            "saved": true,
            "pluginId": "fixture_plugin",
            "sectionId": "settings",
            "changePlan": "applied",
            "applicationState": "applied",
            "applicationReasonCode": "READY",
        });
        assert!(validate_settings_save_result(&saved).is_ok());

        for field in ["changePlan", "applicationState"] {
            let mut pending = saved.clone();
            pending[field] = json!("restart_required");
            assert!(validate_settings_save_result(&pending).is_err());
        }
        let mut private = saved;
        private["sourcePath"] = json!("/private/plugin.zip");
        assert!(validate_settings_save_result(&private).is_err());
    }

    #[test]
    fn failed_plugin_enable_preserves_the_committed_management_snapshot() {
        let mut result = snapshot();
        result["revision"] = json!("1111111111111111");
        result["plugins"][0]["state"] = json!("failed");
        result["plugins"][0]["reasonCode"] = json!("MISSING_SERVICE");
        result["managementAction"] = json!("enabled_changed");
        result["installId"] = result["plugins"][0]["installId"].clone();
        result["pluginId"] = result["plugins"][0]["pluginId"].clone();
        result["desiredSaved"] = json!(true);
        result["applicationState"] = json!("error");
        result["applicationReasonCode"] = json!("MISSING_SERVICE");
        assert!(validate_management_result(&result).is_ok());

        result["applicationState"] = json!("pending");
        assert!(validate_management_result(&result).is_err());
        result["applicationState"] = json!("error");
        result["desiredSaved"] = json!(false);
        assert!(validate_management_result(&result).is_err());
    }

    #[test]
    fn local_plugin_management_results_are_exact_and_source_bounded() {
        let mut installed = snapshot();
        installed["managementAction"] = json!("installed");
        installed["installId"] = json!("pi_bundled_666978747572655f706c7567696e");
        installed["pluginId"] = json!("fixture_plugin");
        assert!(validate_management_result(&installed).is_ok());
        installed["plugins"][0]["source"] = json!("user");
        installed["plugins"][0]["canUninstall"] = json!(true);
        assert!(validate_management_result(&installed).is_ok());
        installed["plugins"][0]["canUninstall"] = json!(false);
        assert!(validate_management_result(&installed).is_err());
        installed["plugins"][0]["required"] = json!(true);
        assert!(validate_management_result(&installed).is_err());
        installed["plugins"][0]["required"] = json!(false);
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
            "surface": null,
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
        assert!(validate_snapshot(&value).is_ok());
        assert!(validate_collection_request(
            "query",
            "fixture_plugin",
            "data",
            "entries",
            &json!({"cursor": null, "limit": 25, "search": "needle", "filters": {}}),
        )
        .is_ok());
        assert!(validate_collection_request(
            "unknown",
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
        assert!(validate_collection_result("query", &json!(["not-an-envelope"]),).is_err());
    }

    #[test]
    fn semantic_status_and_resource_fields_are_strictly_bounded() {
        let mut value = snapshot();
        value["plugins"][0]["sections"] = json!([{
            "sectionId": "memory",
            "title": "Memory",
            "surface": null,
            "reasonCode": "READY",
            "fields": [{
                "key": "status", "label": "Status", "type": "status",
                "default": {"state": "neutral", "label": "Unknown", "message": ""},
                "description": "", "options": [], "minimum": null, "maximum": null,
                "step": null, "maxLength": null, "placement": "section_header", "enabledWhen": null,
                "actionIds": [], "required": false, "readonly": true, "copyable": false,
                "restartRequired": false,
                "value": {"state": "ready", "label": "Running", "message": ""}
            }, {
                "key": "model", "label": "Model", "type": "resource",
                "default": null, "description": "", "options": [], "minimum": null,
                "maximum": null, "step": null, "maxLength": null, "placement": "advanced", "enabledWhen": null,
                "actionIds": ["cancel"], "required": false, "readonly": true,
                "copyable": false, "restartRequired": false,
                "value": {
                    "applicability": "required",
                    "subtitle": "all-MiniLM-L6-v2", "ready": false, "taskState": "running",
                    "message": "Downloading", "detail": "Model files", "progress": 55,
                    "availableActionIds": ["cancel"]
                }
            }],
            "values": {
                "status": {"state": "ready", "label": "Running", "message": ""},
                "model": {
                    "applicability": "required",
                    "subtitle": "all-MiniLM-L6-v2", "ready": false, "taskState": "running",
                    "message": "Downloading", "detail": "Model files", "progress": 55,
                    "availableActionIds": ["cancel"]
                }
            },
            "actions": [{"actionId": "cancel", "label": "Cancel", "description": "", "danger": false}],
            "collections": []
        }]);
        assert!(validate_snapshot(&value).is_ok());
        value["plugins"][0]["sections"][0]["fields"][1]["value"]["progress"] = json!(101);
        assert!(validate_snapshot(&value).is_err());

        let mut duplicate_action = snapshot();
        duplicate_action["plugins"][0]["sections"] = value["plugins"][0]["sections"].clone();
        duplicate_action["plugins"][0]["sections"][0]["fields"][1]["value"]["progress"] = json!(55);
        duplicate_action["plugins"][0]["sections"][0]["fields"][1]["value"]["availableActionIds"] =
            json!(["cancel", "cancel"]);
        duplicate_action["plugins"][0]["sections"][0]["values"]["model"]["availableActionIds"] =
            json!(["cancel", "cancel"]);
        assert!(validate_snapshot(&duplicate_action).is_err());
    }
}
