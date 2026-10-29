use serde_json::{json, Value};
use tauri::{State, WebviewWindow};

use crate::{
    product_shell::{self, assert_settings_identity},
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};

#[tauri::command]
pub(crate) async fn plugin_frontend_module(
    window: WebviewWindow,
    service_key: String,
    module_name: String,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    if !matches!(window.label(), "main" | "history" | "settings") {
        return Err("PLUGIN_FRONTEND_WINDOW_INVALID".into());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation = handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or("STALE_GENERATION")?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.frontend.get",
        json!({"serviceKey":service_key,"moduleName":module_name}),
    )
    .await?;
    if handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .as_deref()
        != Some(&generation)
    {
        return Err("STALE_GENERATION".into());
    }
    settings_response_payload(response)
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
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response =
        dispatch_settings_request(handle.clone(), None, "plugins.settings.get", json!({})).await?;
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
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.save",
        json!({"pluginId": plugin_id, "sectionId": section_id, "values": values}),
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
    install_id: String,
    enabled: bool,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.enabled.set",
        json!({"installId": install_id, "enabled": enabled}),
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
    mut values: Value,
    file_picker: Option<Value>,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    if let Some(picker) = file_picker {
        let field = picker
            .get("field")
            .and_then(Value::as_str)
            .ok_or("SETTINGS_DESCRIPTOR_INVALID")?;
        let extensions = picker
            .get("extensions")
            .and_then(Value::as_array)
            .ok_or("SETTINGS_DESCRIPTOR_INVALID")?;
        let extensions: Vec<&str> = extensions
            .iter()
            .map(|value| value.as_str().ok_or("SETTINGS_DESCRIPTOR_INVALID"))
            .collect::<Result<_, _>>()?;
        let selected = rfd::AsyncFileDialog::new()
            .set_title("选择文件")
            .add_filter("支持的文件", &extensions)
            .pick_file()
            .await;
        let Some(selected) = selected else {
            return Ok(json!({"cancelled": true}));
        };
        assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
        let path = selected.path().to_str().ok_or("SETTINGS_VALUES_INVALID")?;
        values
            .as_object_mut()
            .ok_or("SETTINGS_VALUES_INVALID")?
            .insert(field.to_string(), Value::String(path.to_string()));
    }
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.action",
        json!({"pluginId": plugin_id, "sectionId": section_id, "actionId": action_id, "values": values}),
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
            "sourceKind": source_kind,
            "sourcePath": source_path,
        }),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
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
        json!({"installId": install_id}),
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
        "transfer" => "plugins.collection.transfer",
        _ => return Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string()),
    };
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        request_name,
        Value::Object(request_payload),
    )
    .await?;
    let result = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    Ok(result)
}
