//! Authenticated Core requests for the existing desktop capture and rendering owners.
use crate::{
    capture::CaptureManager,
    chat_bridge::ChatEventPublication,
    shell_lifecycle::{
        settings_core_handle, settings_response_payload, ShellLifecycleHandle, ShellLifecycleState,
    },
};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tauri::{ipc::Channel, Emitter, Manager, State, WebviewWindow};

#[tauri::command]
pub fn host_chat_listen(
    window: WebviewWindow,
    on_event: Channel<ChatEventPublication>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<(), String> {
    settings_core_handle(&lifecycle)?
        .chat_bridge()?
        .listen_host(window.label(), on_event)
}

async fn request(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    name: &'static str,
    payload: Value,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".into());
    }
    let handle = settings_core_handle(&lifecycle)?;
    tauri::async_runtime::spawn_blocking(move || {
        settings_response_payload(handle.settings_request(
            None,
            name,
            payload,
            Duration::from_secs(5),
        )?)
    })
    .await
    .map_err(|source_error| {
        crate::runtime_log::diagnostic_error("HOST_INTERACTION_ABORTED", source_error)
    })?
}

#[tauri::command]
pub async fn host_interaction_current(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    generation_id: String,
) -> Result<Value, String> {
    request(
        window,
        lifecycle,
        "host.interaction.current",
        json!({"generationId":generation_id}),
    )
    .await
}

#[tauri::command]
pub async fn host_interaction_state(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    payload: Value,
) -> Result<Value, String> {
    request(window, lifecycle, "host.interaction.state", payload).await
}

#[tauri::command]
pub async fn host_visual_claim(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    payload: Value,
) -> Result<Value, String> {
    request(window, lifecycle, "host.visual.claim", payload).await
}

#[tauri::command]
pub async fn host_visual_result(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    payload: Value,
) -> Result<Value, String> {
    request(window, lifecycle, "host.visual.result", payload).await
}

pub fn detach(handle: &ShellLifecycleHandle) {
    let Ok(Some(generation_id)) = handle.available_generation_id() else {
        return;
    };
    let handle = handle.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _ = handle.settings_request(
            None,
            "host.interaction.detach",
            json!({"generationId":generation_id}),
            Duration::from_secs(5),
        );
    });
}

pub fn dispatch(app: &tauri::AppHandle, handle: &ShellLifecycleHandle, event: Value) {
    let Some(generation_id) = event
        .get("generationId")
        .and_then(Value::as_str)
        .map(str::to_owned)
    else {
        return;
    };
    if handle.available_generation_id().ok().flatten().as_deref() != Some(&generation_id) {
        return;
    }
    let name = event.get("name").and_then(Value::as_str).unwrap_or("");
    let Some(mut payload) = event.get("payload").cloned().filter(Value::is_object) else {
        return;
    };
    if matches!(name, "host.visual.apply" | "host.visual.cancel") {
        payload["generationId"] = json!(generation_id);
        payload["type"] = json!(name);
        let _ = app.emit_to("main", "sakura-host-visual", payload);
        return;
    }
    if name != "host.screen.capture" {
        return;
    }
    let app = app.clone();
    let handle = handle.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let Some(request_id) = payload.get("requestId").and_then(Value::as_str) else {
            return;
        };
        let Some(session_id) = payload.get("sessionId").and_then(Value::as_str) else {
            return;
        };
        let Some(resolution) = payload.get("resolution").and_then(Value::as_str) else {
            return;
        };
        let manager = app.state::<Arc<CaptureManager>>().inner().clone();
        let result = app
            .cursor_position()
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error(
                    "SCREEN_CAPTURE_CURSOR_UNAVAILABLE",
                    source_error,
                )
            })
            .and_then(|cursor| {
                manager.capture_host_frame(
                    &generation_id,
                    session_id,
                    cursor.x.round() as i32,
                    cursor.y.round() as i32,
                    resolution,
                )
            });
        let mut response =
            json!({"generationId":generation_id,"requestId":request_id,"sessionId":session_id});
        match &result {
            Ok(resource) => response["resource"] = json!(resource),
            Err(error) => {
                response["error"] = json!({"code":"SCREEN_CAPTURE_FAILED", "diagnostic":error})
            }
        }
        // The payload retains the original generation even if the live transport
        // changes before dispatch. Core rejects that result rather than adopting it.
        let _ =
            handle.settings_request(None, "host.screen.result", response, Duration::from_secs(5));
        if let Ok(resource) = result {
            manager.release_descriptors(&[resource], &generation_id);
        }
    });
}
