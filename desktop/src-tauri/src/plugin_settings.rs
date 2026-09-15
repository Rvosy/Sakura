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
        || !value.is_object()
        || value.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || !valid_revision(value.get("revision"))
        || !value["state"].is_string()
        || !value["reasonCode"].is_string()
    {
        return Err("PLUGIN_SETTINGS_RESPONSE_INVALID".to_string());
    }
    let plugins = value["plugins"]
        .as_array()
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
            .is_some_and(|value| !value.is_string())
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

// Core owns plugin display semantics. The Shell checks the transport shape and
// identities used by commands; it does not reinterpret labels, fields or values.
fn validate_plugin(value: &Value) -> Result<(), String> {
    if !value.is_object()
        || !valid_install_id(value.get("installId"))
        || !valid_nullable_plugin_id(value.get("pluginId"))
        || ![
            "name",
            "version",
            "author",
            "description",
            "source",
            "state",
            "reasonCode",
        ]
        .iter()
        .all(|key| value[*key].is_string())
        || !["enabled", "required", "supported", "canUninstall"]
            .iter()
            .all(|key| value[*key].is_boolean())
        || !["provides", "requires", "missingServices"]
            .iter()
            .all(|key| {
                value[*key]
                    .as_array()
                    .is_some_and(|items| items.iter().all(Value::is_string))
            })
        || !value["sections"].as_array().is_some_and(|sections| {
            sections.iter().all(|section| {
                section.is_object()
                    && section["sectionId"].is_string()
                    && section["title"].is_string()
                    && section["values"].is_object()
                    && ["fields", "actions", "collections"].iter().all(|key| {
                        section[*key]
                            .as_array()
                            .is_some_and(|items| items.iter().all(Value::is_object))
                    })
            })
        })
    {
        return Err("PLUGIN_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn valid_install_id(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        let Some(directory) = text
            .strip_prefix("pi_user_")
            .or_else(|| text.strip_prefix("pi_bundled_"))
        else {
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

fn valid_reason(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        !text.is_empty()
            && text.len() <= 64
            && text
                .bytes()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    })
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
    fn producer_display_metadata_accepts_unicode_and_additive_fields() {
        let mut value = snapshot();
        value["plugins"][0]["name"] = json!("中".repeat(120));
        value["plugins"][0]["presentation"] =
            json!({"kind": "provider", "category": "future", "extra": true});
        value["plugins"][0]["futureDisplayField"] = json!("额外说明");
        assert!(validate_snapshot(&value).is_ok());
        assert!(validate_action_result(&json!({"message": "中".repeat(240)})).is_ok());
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
        value["plugins"][0]["installId"] =
            json!(format!("pi_bundled_{}", "e8a792e889b2".repeat(35)));
        assert!(validate_snapshot(&value).is_ok());
        for invalid in ["pi_user_", "pi_user_a", "pi_other_6162", "pi_user_../a"] {
            value["plugins"][0]["installId"] = json!(invalid);
            assert!(validate_snapshot(&value).is_err());
        }
        value["plugins"][0]["installId"] = json!(format!("pi_user_{}", "aa".repeat(1025)));
        assert!(validate_snapshot(&value).is_err());
    }

    #[test]
    fn plugin_transport_rejects_malformed_shapes_and_oversized_payloads() {
        let mut invalid = snapshot();
        invalid["plugins"][0]["sections"] = json!({});
        assert!(validate_snapshot(&invalid).is_err());
        invalid = snapshot();
        invalid["plugins"][0]["description"] = json!("x".repeat(512 * 1024));
        assert!(validate_snapshot(&invalid).is_err());
        assert!(validate_action_result(&json!({"values": {"large": "x".repeat(70_000)}})).is_err());
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
    fn local_plugin_management_results_keep_the_operation_envelope() {
        let mut installed = snapshot();
        installed["managementAction"] = json!("installed");
        installed["installId"] = json!("pi_bundled_666978747572655f706c7567696e");
        installed["pluginId"] = json!("fixture_plugin");
        assert!(validate_management_result(&installed).is_ok());
        installed["plugins"][0]["source"] = json!("user");
        installed["plugins"][0]["canUninstall"] = json!(true);
        assert!(validate_management_result(&installed).is_ok());
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
}
