//! Single owner of one main-window chat operation and its public event channel.

use std::{
    sync::{
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::ipc::Channel;

use crate::core_host_runtime::ConcurrentRequestHandle;

const CHAT_SEND_DEADLINE: Duration = Duration::from_secs(30);
const CHAT_CANCEL_DEADLINE: Duration = Duration::from_secs(1);

pub(crate) trait ChatTransport: Send + Sync {
    fn request(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
        scheduling: &'static str,
    ) -> Result<Value, String>;
}

impl ChatTransport for ConcurrentRequestHandle {
    fn request(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
        scheduling: &'static str,
    ) -> Result<Value, String> {
        self.request_with_scheduling(request_id, name, payload, deadline, scheduling)
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ChatSendRequest {
    pub message: String,
    pub attachment_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ChatCancelRequest {
    pub operation_id: String,
    pub cancel_handle: String,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChatEventPublication {
    #[serde(rename = "type")]
    pub event_type: String,
    pub generation_id: String,
    pub generation_number: u64,
    pub operation_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reply: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub presentation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cancel_handle: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub character_id: Option<String>,
    #[serde(skip)]
    pub(crate) update_version: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ChatSendPublication {
    pub accepted: bool,
    pub operation_id: String,
    pub cancel_handle: String,
    pub generation_id: String,
    pub generation_number: u64,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ChatCancelPublication {
    pub accepted: bool,
    pub operation_id: String,
}

#[derive(Clone)]
pub struct ChatBridge {
    state: Arc<Mutex<BridgeState>>,
    transport: Arc<dyn ChatTransport>,
}

struct BridgeState {
    generation_id: String,
    generation_number: u64,
    active: Option<ActiveChat>,
    host_channel: Option<Channel<ChatEventPublication>>,
    valid: bool,
}

struct ActiveChat {
    publication: ChatSendPublication,
    started: bool,
    cancel_in_flight: bool,
    update_version: Option<String>,
    on_event: Channel<ChatEventPublication>,
    acceptance: Option<SyncSender<Result<ChatSendPublication, String>>>,
}

impl ActiveChat {
    fn accept(&mut self) {
        if let Some(acceptance) = self.acceptance.take() {
            let _ = acceptance.send(Ok(self.publication.clone()));
        }
    }

    fn reject(mut self, error: String) {
        if let Some(acceptance) = self.acceptance.take() {
            let _ = acceptance.send(Err(error));
        }
    }
}

pub struct PendingChatSend {
    #[cfg(test)]
    publication: ChatSendPublication,
    completion: Receiver<Result<ChatSendPublication, String>>,
}

impl ChatBridge {
    pub(crate) fn new(
        transport: Arc<dyn ChatTransport>,
        generation_id: String,
        generation_number: u64,
    ) -> Result<Self, String> {
        if generation_id.trim().is_empty() || generation_number == 0 {
            return Err("CHAT_BRIDGE_GENERATION_INVALID".to_string());
        }
        Ok(Self {
            transport,
            state: Arc::new(Mutex::new(BridgeState {
                generation_id,
                generation_number,
                active: None,
                host_channel: None,
                valid: true,
            })),
        })
    }

    pub fn send_with_attachment(
        &self,
        window_label: &str,
        message: String,
        attachment_id: Option<String>,
        on_event: Channel<ChatEventPublication>,
    ) -> Result<PendingChatSend, String> {
        self.send_payload(
            window_label,
            match attachment_id {
                Some(attachment_id) => json!({"message": message, "attachmentId": attachment_id}),
                None => json!({"message": message}),
            },
            None,
            on_event,
        )
    }

    pub fn listen_host(
        &self,
        window_label: &str,
        channel: Channel<ChatEventPublication>,
    ) -> Result<(), String> {
        authorize_window(window_label)?;
        let mut state = self.state.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("CHAT_BRIDGE_UNAVAILABLE", source_error)
        })?;
        if !state.valid {
            return Err("CHAT_GENERATION_INVALIDATED".into());
        }
        state.host_channel = Some(channel);
        Ok(())
    }

    pub fn send_update_available(
        &self,
        window_label: &str,
        event: Value,
        version: String,
        on_event: Channel<ChatEventPublication>,
    ) -> Result<PendingChatSend, String> {
        self.send_payload(
            window_label,
            json!({"event": event}),
            Some(version),
            on_event,
        )
    }

    fn send_payload(
        &self,
        window_label: &str,
        mut payload: Value,
        update_version: Option<String>,
        on_event: Channel<ChatEventPublication>,
    ) -> Result<PendingChatSend, String> {
        authorize_window(window_label)?;
        validate_chat_payload(&payload)?;
        let mut state = self.state.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("CHAT_BRIDGE_UNAVAILABLE", source_error)
        })?;
        if !state.valid {
            return Err("CHAT_GENERATION_INVALIDATED".to_string());
        }
        if state.active.is_some() {
            return Err("CHAT_INTERACTION_ACTIVE".to_string());
        }
        let operation_id = format!("chat-{}", uuid::Uuid::new_v4());
        let publication = ChatSendPublication {
            accepted: true,
            operation_id: operation_id.clone(),
            cancel_handle: uuid::Uuid::new_v4().to_string(),
            generation_id: state.generation_id.clone(),
            generation_number: state.generation_number,
        };
        let (acceptance, completion) = mpsc::sync_channel(1);
        state.active = Some(ActiveChat {
            publication: publication.clone(),
            started: false,
            cancel_in_flight: false,
            update_version,
            on_event,
            acceptance: Some(acceptance),
        });
        payload
            .as_object_mut()
            .expect("validated chat payload")
            .insert("operationId".to_string(), json!(operation_id));
        let bridge = self.clone();
        let request_operation_id = operation_id.clone();
        let dispatch = thread::Builder::new()
            .name(format!("sakura-chat-request-{operation_id}"))
            .spawn(move || {
                let result = bridge.transport.request(
                    &request_operation_id,
                    "chat.send",
                    payload,
                    CHAT_SEND_DEADLINE,
                    "interactive",
                );
                bridge.dispatch_completed(&request_operation_id, result);
            });
        if let Err(error) = dispatch {
            let error = format!("CHAT_DISPATCH_FAILED: {error}");
            if let Some(active) = state.active.take() {
                active.reject(error.clone());
            }
            return Err(error);
        }
        Ok(PendingChatSend {
            #[cfg(test)]
            publication,
            completion,
        })
    }

    fn dispatch_completed(&self, operation_id: &str, response: Result<Value, String>) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        let Some(active) = state
            .active
            .as_mut()
            .filter(|active| active.publication.operation_id == operation_id)
        else {
            return;
        };
        match response.and_then(|response| validate_send_response(operation_id, response)) {
            Ok(()) => active.accept(),
            Err(error) => {
                if let Some(active) = state.active.take() {
                    if active.started {
                        // The request may already have committed. Report lost delivery,
                        // not cancellation or absence of side effects, and never replay it.
                        let publication = ChatEventPublication {
                            event_type: "chat.failed".into(),
                            generation_id: active.publication.generation_id.clone(),
                            generation_number: active.publication.generation_number,
                            operation_id: operation_id.to_string(),
                            reply: None,
                            character_id: None,
                            presentation: None,
                            cancel_handle: None,
                            error: Some(json!({
                                "code": "CHAT_DELIVERY_UNCONFIRMED",
                                "message": "回复状态无法确认，请检查连接。",
                                "retryable": false,
                            })),
                            update_version: active.update_version.clone(),
                        };
                        let _ = active.on_event.send(publication);
                    }
                    active.reject(error);
                }
            }
        }
    }

    pub fn cancel(
        &self,
        window_label: &str,
        operation_id: &str,
        cancel_handle: &str,
    ) -> Result<ChatCancelPublication, String> {
        authorize_window(window_label)?;
        {
            let mut state = self.state.lock().map_err(|source_error| {
                crate::runtime_log::diagnostic_error("CHAT_BRIDGE_UNAVAILABLE", source_error)
            })?;
            if !state.valid {
                return Err("CHAT_GENERATION_INVALIDATED".to_string());
            }
            let active = state
                .active
                .as_mut()
                .filter(|active| {
                    active.publication.operation_id == operation_id
                        && active.publication.cancel_handle == cancel_handle
                })
                .ok_or("STALE_CANCEL_HANDLE")?;
            if active.cancel_in_flight {
                return Ok(ChatCancelPublication {
                    accepted: false,
                    operation_id: operation_id.to_string(),
                });
            }
            active.cancel_in_flight = true;
        }
        // Never hold the operation lock while waiting for IPC: terminals and shutdown
        // must remain deliverable, even if the cancellation request fails.
        let result = self.transport.request(
            &format!("chat-cancel-{}", uuid::Uuid::new_v4()),
            "chat.cancel",
            json!({"operationId": operation_id}),
            CHAT_CANCEL_DEADLINE,
            "control",
        );
        if let Ok(mut state) = self.state.lock() {
            if let Some(active) = state
                .active
                .as_mut()
                .filter(|active| active.publication.operation_id == operation_id)
            {
                active.cancel_in_flight = false;
            }
        }
        let response = result?;
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err(response_error(&response, "CHAT_CANCEL_REJECTED"));
        }
        Ok(ChatCancelPublication {
            accepted: response
                .pointer("/payload/accepted")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            operation_id: operation_id.to_string(),
        })
    }

    pub fn observe_event(&self, message: &Value) -> Result<Option<ChatEventPublication>, String> {
        let mut state = self.state.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("CHAT_BRIDGE_UNAVAILABLE", source_error)
        })?;
        if !state.valid
            || message.get("generationId").and_then(Value::as_str) != Some(&state.generation_id)
        {
            return Ok(None);
        }
        let operation_id = message
            .get("id")
            .and_then(Value::as_str)
            .ok_or("INVALID_CHAT_EVENT")?;
        let generation_id = state.generation_id.clone();
        let generation_number = state.generation_number;
        let raw_name = message
            .get("name")
            .and_then(Value::as_str)
            .ok_or("INVALID_CHAT_EVENT")?;
        let host_event = raw_name.starts_with("host.chat.");
        let event_type = if host_event { &raw_name[5..] } else { raw_name };
        let payload = message
            .get("payload")
            .and_then(Value::as_object)
            .ok_or("INVALID_CHAT_EVENT")?;
        if payload.get("operationId").and_then(Value::as_str) != Some(operation_id) {
            return Err("INVALID_CHAT_EVENT: payload identity mismatch".into());
        }
        if host_event
            && event_type == "chat.started"
            && !state
                .active
                .as_ref()
                .is_some_and(|active| active.publication.operation_id == operation_id)
        {
            let channel = state
                .host_channel
                .clone()
                .ok_or("HOST_CHAT_LISTENER_UNAVAILABLE")?;
            if state.active.as_ref().is_some_and(|active| active.started) {
                return Err("CHAT_INTERACTION_ACTIVE".into());
            }
            // Core owns acceptance. An unacknowledged manual request may have
            // lost the reservation race to this accepted plugin interaction.
            if let Some(pending) = state.active.take() {
                pending.reject("CHAT_INTERACTION_ACTIVE".into());
            }
            state.active = Some(ActiveChat {
                publication: ChatSendPublication {
                    accepted: true,
                    operation_id: operation_id.to_owned(),
                    cancel_handle: uuid::Uuid::new_v4().to_string(),
                    generation_id: generation_id.clone(),
                    generation_number,
                },
                started: false,
                cancel_in_flight: false,
                update_version: None,
                on_event: channel,
                acceptance: None,
            });
        }
        let Some(active) = state
            .active
            .as_mut()
            .filter(|active| active.publication.operation_id == operation_id)
        else {
            return Ok(None);
        };
        let terminal = matches!(
            event_type,
            "chat.completed" | "chat.failed" | "chat.cancelled"
        );
        if event_type != "chat.started" && !terminal {
            return Err("INVALID_CHAT_EVENT".to_string());
        }
        if event_type == "chat.started" && active.started {
            return Ok(None);
        }
        if terminal && !active.started {
            return Err("INVALID_CHAT_EVENT: terminal preceded chat.started".to_string());
        }
        let publication = ChatEventPublication {
            event_type: event_type.to_string(),
            generation_id,
            generation_number,
            operation_id: operation_id.to_string(),
            character_id: host_event
                .then(|| {
                    payload
                        .get("characterId")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .flatten(),
            presentation: host_event.then(|| "silent".to_string()),
            cancel_handle: (host_event && event_type == "chat.started")
                .then(|| active.publication.cancel_handle.clone()),
            reply: (event_type == "chat.completed")
                .then(|| payload.get("reply").cloned())
                .flatten(),
            error: (event_type == "chat.failed")
                .then(|| project_error(payload.get("error")))
                .transpose()?,
            update_version: active.update_version.clone(),
        };
        // Channel::send queues into the WebView; it does not wait for a JS listener.
        // Send under the owner lock so a dispatch failure cannot overtake started.
        if let Err(error) = active.on_event.send(publication.clone()) {
            let error = format!("CHAT_EVENT_DELIVERY_FAILED: {error}");
            if let Some(active) = state.active.take() {
                active.reject(error.clone());
            }
            return Err(error);
        }
        active.started = true;
        active.accept();
        if terminal {
            state.active = None;
        }
        Ok(Some(publication))
    }

    pub fn invalidate(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.valid = false;
            if let Some(active) = state.active.take() {
                active.reject("CHAT_GENERATION_INVALIDATED".to_string());
            }
        }
    }
}

impl PendingChatSend {
    pub fn wait(self) -> Result<ChatSendPublication, String> {
        self.completion.recv().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("CHAT_DISPATCH_ABORTED", source_error)
        })?
    }
}

fn authorize_window(window_label: &str) -> Result<(), String> {
    (window_label == "main")
        .then_some(())
        .ok_or_else(|| "GATEWAY_WINDOW_DENIED: caller window is not authorized".to_string())
}

fn response_error(response: &Value, fallback: &str) -> String {
    match project_error(response.get("error")) {
        Ok(error) => format!(
            "{}: {}",
            error["code"].as_str().unwrap_or(fallback),
            error["message"].as_str().unwrap_or("")
        ),
        Err(_) => fallback.to_string(),
    }
}

fn validate_send_response(operation_id: &str, response: Value) -> Result<(), String> {
    if response.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(response_error(&response, "CHAT_DISPATCH_REJECTED"));
    }
    if response
        .pointer("/payload/accepted")
        .and_then(Value::as_bool)
        != Some(true)
        || response
            .pointer("/payload/operationId")
            .and_then(Value::as_str)
            != Some(operation_id)
    {
        return Err("CHAT_DISPATCH_REJECTED".to_string());
    }
    Ok(())
}

fn project_error(error: Option<&Value>) -> Result<Value, String> {
    let error = error
        .and_then(Value::as_object)
        .ok_or_else(|| "INVALID_CHAT_EVENT".to_string())?;
    let code = error
        .get("code")
        .and_then(Value::as_str)
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
        })
        .unwrap_or("CHAT_FAILED");
    let details = crate::runtime_log::error_details(&Value::Object(error.clone()));
    let message = if details.is_empty() {
        code.to_string()
    } else {
        details
    };
    Ok(json!({
        "code": code,
        "message": message,
        "retryable": error.get("retryable").and_then(Value::as_bool).unwrap_or(false),
    }))
}

fn validate_chat_payload(payload: &Value) -> Result<(), String> {
    let object = payload
        .as_object()
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: payload must be an object".to_string())?;
    if object.len() == 1 && object.contains_key("event") {
        validate_update_available_event(object.get("event"))?;
        return Ok(());
    }
    if object
        .keys()
        .any(|key| !matches!(key.as_str(), "message" | "attachmentId"))
    {
        return Err("INVALID_CHAT_PAYLOAD: payload contains forbidden fields".to_string());
    }
    object
        .get("message")
        .and_then(Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: message must be non-empty".to_string())?;
    if let Some(attachment_id) = object.get("attachmentId") {
        let valid = attachment_id
            .as_str()
            .is_some_and(|value| crate::capture::valid_attachment_id(value));
        if !valid {
            return Err("INVALID_CHAT_PAYLOAD: attachment identity is invalid".to_string());
        }
    }
    Ok(())
}

fn validate_update_available_event(event: Option<&Value>) -> Result<(), String> {
    let event = event
        .and_then(Value::as_object)
        .filter(|event| event.len() == 2)
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: update event is invalid".to_string())?;
    if event.get("type").and_then(Value::as_str) != Some("update_available") {
        return Err("INVALID_CHAT_PAYLOAD: update event type is invalid".to_string());
    }
    let payload = event
        .get("payload")
        .and_then(Value::as_object)
        .filter(|payload| {
            payload.len() == 5
                && ["currentVersion", "version", "notes", "pubDate", "mode"]
                    .iter()
                    .all(|key| payload.contains_key(*key))
        })
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: update event payload is invalid".to_string())?;
    for key in ["currentVersion", "version"] {
        if !payload
            .get(key)
            .and_then(Value::as_str)
            .is_some_and(|value| !value.is_empty() && value.len() <= 64)
        {
            return Err("INVALID_CHAT_PAYLOAD: update version is invalid".to_string());
        }
    }
    if !matches!(
        payload.get("mode").and_then(Value::as_str),
        Some("installed" | "portable")
    ) {
        return Err("INVALID_CHAT_PAYLOAD: update mode is invalid".to_string());
    }
    for (key, limit) in [("notes", 4_000), ("pubDate", 64)] {
        if !payload.get(key).is_some_and(|value| {
            value.is_null()
                || value
                    .as_str()
                    .is_some_and(|text| text.chars().count() <= limit)
        }) {
            return Err(format!("INVALID_CHAT_PAYLOAD: update {key} is invalid"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::Arc, time::Duration};

    const GENERATION: &str = "00000000-0000-4000-8000-000000003004";

    #[derive(Default)]
    struct FakeTransport;

    impl ChatTransport for FakeTransport {
        fn request(
            &self,
            request_id: &str,
            name: &str,
            _payload: Value,
            _deadline: Duration,
            _scheduling: &'static str,
        ) -> Result<Value, String> {
            Ok(match name {
                "chat.send" => json!({
                    "ok": true,
                    "payload": {"accepted": true, "operationId": request_id}
                }),
                "chat.cancel" => json!({
                    "ok": true,
                    "payload": {"accepted": true}
                }),
                _ => unreachable!(),
            })
        }
    }

    fn channel() -> Channel<ChatEventPublication> {
        Channel::new(|_| Ok(()))
    }

    fn bridge() -> ChatBridge {
        ChatBridge::new(Arc::new(FakeTransport), GENERATION.to_string(), 3).unwrap()
    }

    fn event(operation_id: &str, name: &str) -> Value {
        let payload = match name {
            "chat.started" => json!({"operationId": operation_id}),
            "chat.failed" => json!({
                "operationId": operation_id,
                "historyStatus": "degraded",
                "error": {
                    "code": "PROVIDER_FAILED",
                    "message": "Authorization: Bearer must-not-project C:\\private",
                    "retryable": true,
                    "details": {"apiKey": "must-not-project", "path": "C:\\private"}
                }
            }),
            "chat.completed" => json!({
                "operationId": operation_id,
                "historyStatus": "saved",
                "reply": {"segments": [{
                    "text": "done",
                    "translation": "",
                    "tone": "neutral",
                    "portrait": "",
                    "suppressTts": false,
                }]},
            }),
            _ => unreachable!(),
        };
        json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "event",
            "generationId": GENERATION,
            "generationCredential": "44444444444444444444444444444444",
            "id": operation_id,
            "name": name,
            "payload": payload,
        })
    }

    #[test]
    fn host_interaction_uses_the_existing_slot_and_cancel_handle() {
        let bridge = bridge();
        assert!(bridge.listen_host("settings", channel()).is_err());
        bridge.listen_host("main", channel()).unwrap();
        let mut started = event("plugin-turn", "chat.started");
        started["name"] = json!("host.chat.started");
        started["payload"]["characterId"] = json!("sakura");
        let publication = bridge.observe_event(&started).unwrap().unwrap();
        assert_eq!(publication.presentation.as_deref(), Some("silent"));
        assert_eq!(publication.character_id.as_deref(), Some("sakura"));
        assert!(bridge
            .send_with_attachment("main", "busy".into(), None, channel())
            .is_err());
        assert!(
            bridge
                .cancel(
                    "main",
                    "plugin-turn",
                    publication.cancel_handle.as_deref().unwrap()
                )
                .unwrap()
                .accepted
        );
        let mut terminal = event("plugin-turn", "chat.completed");
        terminal["name"] = json!("host.chat.completed");
        assert_eq!(
            bridge.observe_event(&terminal).unwrap().unwrap().event_type,
            "chat.completed"
        );
        assert!(bridge.observe_event(&terminal).unwrap().is_none());
        assert!(bridge
            .cancel(
                "main",
                "plugin-turn",
                publication.cancel_handle.as_deref().unwrap()
            )
            .is_err());
        assert!(bridge
            .send_with_attachment("main", "next".into(), None, channel())
            .is_ok());
    }

    #[test]
    fn accepted_host_interaction_replaces_only_an_unacknowledged_manual_send() {
        let (bridge, requests) = controlled_bridge();
        bridge.listen_host("main", channel()).unwrap();
        let pending = bridge
            .send_with_attachment("main", "manual".into(), None, channel())
            .unwrap();
        // Keep the manual request unacknowledged until the host event wins.
        let request = next_request(&requests);
        let mut started = event("plugin-turn", "chat.started");
        started["name"] = json!("host.chat.started");
        started["payload"]["characterId"] = json!("sakura");
        let mut stale = started.clone();
        stale["generationId"] = json!("old");
        assert!(bridge.observe_event(&stale).unwrap().is_none());
        assert!(bridge.observe_event(&started).unwrap().is_some());
        assert_eq!(pending.wait().unwrap_err(), "CHAT_INTERACTION_ACTIVE");
        request.reply.send(Err("CHAT_BUSY".into())).unwrap();
        let mut second = started.clone();
        second["id"] = json!("second");
        second["payload"]["operationId"] = json!("second");
        assert_eq!(
            bridge.observe_event(&second).unwrap_err(),
            "CHAT_INTERACTION_ACTIVE"
        );
    }

    #[test]
    fn one_active_turn_projects_only_public_fields_and_reopens_after_terminal() {
        let bridge = bridge();
        let pending = bridge
            .send_with_attachment("main", "hello".to_string(), None, channel())
            .unwrap();
        let operation_id = pending.publication.operation_id.clone();
        assert_eq!(
            bridge
                .send_with_attachment("main", "second".to_string(), None, channel())
                .err()
                .unwrap(),
            "CHAT_INTERACTION_ACTIVE"
        );
        pending.wait().unwrap();

        assert_eq!(
            bridge
                .observe_event(&event(&operation_id, "chat.started"))
                .unwrap()
                .unwrap()
                .event_type,
            "chat.started"
        );
        let failed = bridge
            .observe_event(&event(&operation_id, "chat.failed"))
            .unwrap()
            .unwrap();
        let serialized = serde_json::to_string(&failed).unwrap();
        assert!(!serialized.contains("must-not-project"));
        assert!(serialized.contains("private"));
        assert_eq!(
            failed.error.as_ref().unwrap()["message"],
            "Authorization: Bearer [REDACTED] C:\\private"
        );
        assert_eq!(failed.error.unwrap()["retryable"], true);
        assert!(bridge
            .send_with_attachment("main", "next".to_string(), None, channel())
            .is_ok());
    }

    #[test]
    fn non_main_send_and_forged_cancel_handle_are_rejected_before_core_cancel() {
        let bridge = bridge();
        assert!(bridge
            .send_with_attachment("settings", "hello".to_string(), None, channel())
            .is_err());
        let pending = bridge
            .send_with_attachment("main", "hello".to_string(), None, channel())
            .unwrap();
        let operation_id = pending.publication.operation_id.clone();
        assert_eq!(
            bridge.cancel("main", &operation_id, "forged").unwrap_err(),
            "STALE_CANCEL_HANDLE"
        );
        pending.wait().unwrap();
    }

    #[test]
    fn typed_update_identity_is_private_and_survives_until_the_terminal() {
        let bridge = bridge();
        let pending = bridge
            .send_update_available(
                "main",
                json!({
                    "type": "update_available",
                    "payload": {
                        "currentVersion": "1.0.0",
                        "version": "1.2.0",
                        "notes": null,
                        "pubDate": null,
                        "mode": "installed",
                    }
                }),
                "1.2.0".to_string(),
                channel(),
            )
            .unwrap();
        let operation_id = pending.publication.operation_id.clone();
        pending.wait().unwrap();
        let started = bridge
            .observe_event(&event(&operation_id, "chat.started"))
            .unwrap()
            .unwrap();
        assert_eq!(started.update_version.as_deref(), Some("1.2.0"));
        assert!(!serde_json::to_string(&started).unwrap().contains("1.2.0"));

        let completed = bridge
            .observe_event(&event(&operation_id, "chat.completed"))
            .unwrap()
            .unwrap();
        assert_eq!(completed.update_version.as_deref(), Some("1.2.0"));
        assert!(!serde_json::to_string(&completed).unwrap().contains("1.2.0"));
    }

    #[test]
    fn command_payloads_reject_extra_transport_and_secret_fields() {
        for field in ["operationId", "generationId", "apiKey", "history"] {
            let mut payload = json!({"message": "hello"});
            payload[field] = json!("forged");
            assert!(
                serde_json::from_value::<ChatSendRequest>(payload).is_err(),
                "{field}"
            );
        }
        assert!(serde_json::from_value::<ChatCancelRequest>(json!({
            "operationId": "op",
            "cancelHandle": "opaque",
            "reason": "forged"
        }))
        .is_err());
    }

    struct ControlledRequest {
        id: String,
        name: String,
        payload: Value,
        scheduling: &'static str,
        reply: SyncSender<Result<Value, String>>,
    }

    struct ControlledTransport(SyncSender<ControlledRequest>);

    impl ChatTransport for ControlledTransport {
        fn request(
            &self,
            request_id: &str,
            name: &str,
            payload: Value,
            _deadline: Duration,
            scheduling: &'static str,
        ) -> Result<Value, String> {
            let (reply, result) = mpsc::sync_channel(1);
            self.0
                .send(ControlledRequest {
                    id: request_id.to_string(),
                    name: name.to_string(),
                    payload,
                    scheduling,
                    reply,
                })
                .unwrap();
            result
                .recv_timeout(Duration::from_secs(10))
                .expect("test must answer the request")
        }
    }

    fn controlled_bridge() -> (ChatBridge, Receiver<ControlledRequest>) {
        let (requests, received) = mpsc::sync_channel(8);
        (
            ChatBridge::new(
                Arc::new(ControlledTransport(requests)),
                GENERATION.to_string(),
                3,
            )
            .unwrap(),
            received,
        )
    }

    fn next_request(requests: &Receiver<ControlledRequest>) -> ControlledRequest {
        requests
            .recv_timeout(Duration::from_secs(10))
            .expect("request should reach transport")
    }

    fn accept(request: ControlledRequest) {
        request
            .reply
            .send(Ok(
                json!({"ok": true, "payload": {"accepted": true, "operationId": request.id}}),
            ))
            .unwrap();
    }

    #[test]
    fn repeated_rejection_and_transport_failure_release_the_only_operation() {
        let (bridge, requests) = controlled_bridge();
        // Exceeds the old Gateway's registry capacity; no pruning or restart is needed.
        for index in 0..65 {
            let pending = bridge
                .send_with_attachment("main", "retry".into(), None, channel())
                .unwrap();
            let request = next_request(&requests);
            let error = if index % 2 == 0 {
                Ok(
                    json!({"ok": false, "error": {"code": "CHAT_BUSY", "message": "Still running", "retryable": true}}),
                )
            } else {
                Err("TRANSPORT_WRITE_FAILED: injected".to_string())
            };
            request.reply.send(error).unwrap();
            assert!(pending.wait().is_err());
            assert!(bridge.state.lock().unwrap().active.is_none());
        }
        let pending = bridge
            .send_with_attachment("main", "works".into(), None, channel())
            .unwrap();
        accept(next_request(&requests));
        assert!(pending.wait().unwrap().accepted);
    }

    #[test]
    fn early_terminal_and_late_dispatch_cannot_change_the_next_channel() {
        let (bridge, requests) = controlled_bridge();
        let (delivered, deliveries) = mpsc::sync_channel(4);
        let output = Channel::new(move |event| {
            delivered.send(event).unwrap();
            Ok(())
        });
        let pending = bridge
            .send_with_attachment("main", "first".into(), None, output)
            .unwrap();
        let first = next_request(&requests);
        assert_eq!(first.scheduling, "interactive");
        bridge
            .observe_event(&event(&first.id, "chat.started"))
            .unwrap()
            .unwrap();
        assert!(pending.wait().unwrap().accepted); // ACK is still held by the fixture.
        bridge
            .observe_event(&event(&first.id, "chat.completed"))
            .unwrap()
            .unwrap();
        assert!(deliveries.recv_timeout(Duration::from_secs(1)).is_ok());
        assert!(deliveries.recv_timeout(Duration::from_secs(1)).is_ok());
        let next = bridge
            .send_with_attachment("main", "second".into(), None, channel())
            .unwrap();
        let second = next_request(&requests);
        bridge.dispatch_completed(&first.id, Err("late response error".into()));
        assert!(bridge
            .observe_event(&event(&first.id, "chat.failed"))
            .unwrap()
            .is_none());
        assert_eq!(
            bridge
                .state
                .lock()
                .unwrap()
                .active
                .as_ref()
                .unwrap()
                .publication
                .operation_id,
            second.id
        );
        accept(first);
        accept(second);
        assert!(next.wait().unwrap().accepted);
    }

    #[test]
    fn cancel_failure_releases_in_flight_and_terminal_does_not_wait_for_cancel_ack() {
        let (bridge, requests) = controlled_bridge();
        let pending = bridge
            .send_with_attachment("main", "hello".into(), None, channel())
            .unwrap();
        let request = next_request(&requests);
        let operation_id = request.id.clone();
        accept(request);
        let accepted = pending.wait().unwrap();
        bridge
            .observe_event(&event(&operation_id, "chat.started"))
            .unwrap();
        let spawn_cancel = |bridge: ChatBridge, accepted: ChatSendPublication| {
            thread::spawn(move || {
                bridge.cancel("main", &accepted.operation_id, &accepted.cancel_handle)
            })
        };
        let first = spawn_cancel(bridge.clone(), accepted.clone());
        let cancellation = next_request(&requests);
        assert_eq!(cancellation.name, "chat.cancel");
        assert_eq!(cancellation.scheduling, "control");
        assert_eq!(cancellation.payload["operationId"], operation_id);
        cancellation
            .reply
            .send(Err("TRANSPORT_WRITE_FAILED: injected".into()))
            .unwrap();
        assert!(first.join().unwrap().is_err());
        let second = spawn_cancel(bridge.clone(), accepted);
        let cancellation = next_request(&requests);
        assert!(bridge
            .observe_event(&event(&operation_id, "chat.completed"))
            .unwrap()
            .is_some());
        assert!(bridge.state.lock().unwrap().active.is_none());
        accept(cancellation);
        assert!(second.join().unwrap().unwrap().accepted);
    }

    #[test]
    fn invalidation_releases_pending_acceptance_and_drops_the_channel_owner() {
        let (bridge, requests) = controlled_bridge();
        let owner = Arc::new(());
        let captured = owner.clone();
        let output = Channel::new(move |_| {
            let _ = &captured;
            Ok(())
        });
        let pending = bridge
            .send_with_attachment("main", "hello".into(), None, output)
            .unwrap();
        let request = next_request(&requests);
        assert_eq!(Arc::strong_count(&owner), 2);
        bridge.invalidate();
        assert_eq!(pending.wait().unwrap_err(), "CHAT_GENERATION_INVALIDATED");
        assert_eq!(Arc::strong_count(&owner), 1);
        assert!(bridge
            .observe_event(&event(&request.id, "chat.started"))
            .unwrap()
            .is_none());
        accept(request);
        assert!(bridge
            .send_with_attachment("main", "stale".into(), None, channel())
            .is_err());
    }

    #[test]
    fn lost_ack_after_started_closes_projection_without_claiming_cancellation() {
        let (bridge, requests) = controlled_bridge();
        let (sent, events) = mpsc::sync_channel(4);
        let output = Channel::new(move |body| {
            sent.send(body).unwrap();
            Ok(())
        });
        let pending = bridge
            .send_with_attachment("main", "hello".into(), None, output)
            .unwrap();
        let request = next_request(&requests);
        bridge
            .observe_event(&event(&request.id, "chat.started"))
            .unwrap();
        assert!(pending.wait().unwrap().accepted);
        request
            .reply
            .send(Err("REQUEST_TIMEOUT: injected lost ACK".into()))
            .unwrap();
        let parse = |body| match body {
            tauri::ipc::InvokeResponseBody::Json(value) => {
                serde_json::from_str::<Value>(&value).unwrap()
            }
            _ => panic!("chat channel must serialize public JSON"),
        };
        assert_eq!(
            parse(events.recv_timeout(Duration::from_secs(10)).unwrap())["type"],
            "chat.started"
        );
        let failed = parse(events.recv_timeout(Duration::from_secs(10)).unwrap());
        assert_eq!(failed["type"], "chat.failed");
        assert_eq!(failed["error"]["code"], "CHAT_DELIVERY_UNCONFIRMED");
        assert_eq!(failed["error"]["retryable"], false);
        assert!(bridge.state.lock().unwrap().active.is_none());
    }

    #[test]
    fn closed_webview_channel_releases_the_unaccepted_operation() {
        let (bridge, requests) = controlled_bridge();
        let output = Channel::new(|_| Err(tauri::Error::WebviewNotFound));
        let pending = bridge
            .send_with_attachment("main", "hello".into(), None, output)
            .unwrap();
        let request = next_request(&requests);
        assert!(bridge
            .observe_event(&event(&request.id, "chat.started"))
            .is_err());
        assert!(pending
            .wait()
            .unwrap_err()
            .starts_with("CHAT_EVENT_DELIVERY_FAILED"));
        assert!(bridge.state.lock().unwrap().active.is_none());
        accept(request);
    }
}
