use serde_json::{json, Value};
use tauri::{State, WebviewWindow};

use crate::{
    product_shell::{self, assert_settings_identity},
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};

const SNAPSHOT_KEYS: [&str; 2] = ["schemaVersion", "runtimeLimits"];
const LIMIT_KEYS: [&str; 3] = [
    "maxAgentStepsPerTurn",
    "maxToolCallsPerStep",
    "maxToolCallsPerTurn",
];

fn has_exact_keys(value: &Value, keys: &[&str]) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

fn bounded_integer(value: &Value, minimum: u64, maximum: u64) -> bool {
    value
        .as_u64()
        .is_some_and(|number| (minimum..=maximum).contains(&number))
}

fn validate_draft(value: &Value) -> Result<(), String> {
    if !has_exact_keys(value, &["runtimeLimits"]) {
        return Err("TOOLS_SETTINGS_DRAFT_INVALID".to_string());
    }
    validate_values(value)
}

fn validate_snapshot(value: &Value, saved: bool) -> Result<(), String> {
    let keys = if saved {
        vec!["schemaVersion", "runtimeLimits", "saved", "changePlan"]
    } else {
        SNAPSHOT_KEYS.to_vec()
    };
    if !has_exact_keys(value, &keys)
        || value.get("schemaVersion").and_then(Value::as_u64) != Some(1)
    {
        return Err("TOOLS_SETTINGS_RESPONSE_INVALID".to_string());
    }
    validate_values(value).map_err(|_| "TOOLS_SETTINGS_RESPONSE_INVALID".to_string())?;
    if saved
        && (value.get("saved").and_then(Value::as_bool) != Some(true)
            || value.get("changePlan").and_then(Value::as_str) != Some("applied"))
    {
        return Err("TOOLS_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

fn validate_values(value: &Value) -> Result<(), String> {
    let limits = value
        .get("runtimeLimits")
        .ok_or_else(|| "TOOLS_SETTINGS_DRAFT_INVALID".to_string())?;
    if !has_exact_keys(limits, &LIMIT_KEYS)
        || !bounded_integer(&limits["maxAgentStepsPerTurn"], 1, 12)
        || !bounded_integer(&limits["maxToolCallsPerStep"], 1, 10)
        || !bounded_integer(&limits["maxToolCallsPerTurn"], 1, 30)
        || limits["maxToolCallsPerTurn"].as_u64() < limits["maxToolCallsPerStep"].as_u64()
    {
        return Err("TOOLS_SETTINGS_DRAFT_INVALID".to_string());
    }
    Ok(())
}

#[tauri::command]
pub(crate) async fn settings_tools_get(
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
        "tools.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    validate_snapshot(&payload, false)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TOOLS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_tools_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    validate_draft(&settings)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tools.settings.save",
        json!({"settings": settings}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    validate_snapshot(&payload, true)?;
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{validate_draft, validate_snapshot};

    fn values() -> serde_json::Value {
        json!({
            "runtimeLimits": {
                "maxAgentStepsPerTurn": 4,
                "maxToolCallsPerStep": 3,
                "maxToolCallsPerTurn": 8
            }
        })
    }

    #[test]
    fn wp_4_02_tools_settings_are_exact_and_bounded() {
        assert!(validate_draft(&values()).is_ok());
        let mut extra = values();
        extra["arguments"] = json!({"forged": true});
        assert!(validate_draft(&extra).is_err());

        let mut inverted = values();
        inverted["runtimeLimits"]["maxToolCallsPerStep"] = json!(9);
        inverted["runtimeLimits"]["maxToolCallsPerTurn"] = json!(8);
        assert!(validate_draft(&inverted).is_err());
    }

    #[test]
    fn wp_4_02_tools_settings_response_has_no_private_identity_fields() {
        let mut snapshot = values();
        snapshot["schemaVersion"] = json!(1);
        assert!(validate_snapshot(&snapshot, false).is_ok());
        snapshot["generationCredential"] = json!("private");
        assert!(validate_snapshot(&snapshot, false).is_err());
    }
}
