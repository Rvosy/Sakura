use serde_json::{json, Value};
use tauri::{State, WebviewWindow};

use crate::{
    product_shell::{self, assert_settings_identity},
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};

fn validate_settings_save_request(
    plugin_id: &str,
    section_id: &str,
    values: &Value,
) -> Result<(), String> {
    if !valid_identifier_text(plugin_id, 64)
        || !valid_identifier_text(section_id, 64)
        || !values.is_object()
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

// Core owns plugin display semantics. The Shell checks the transport shape and
// identities used by commands; it does not reinterpret labels, fields or values.
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

fn valid_revision(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|text| {
        text.len() == 16
            && text
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
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
    Ok(result)
}
