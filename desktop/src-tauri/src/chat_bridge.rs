//! Main-window-only real chat bridge and public event projection.

use std::{
    sync::{
        mpsc::{Receiver, TryRecvError},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::core_host_gateway::{ChatCancelHandle, CoreHostGateway, EventDisposition};

pub const CHAT_EVENT: &str = "sakura://chat-event";

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

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ToolConfirmationPublication {
    #[serde(skip)]
    pub generation_id: String,
    pub action_id: String,
    pub title: String,
    pub summary: String,
    pub risk: String,
    pub expires_at: String,
}

#[derive(Clone, Debug, PartialEq)]
pub enum RuntimeChatEvent {
    Chat(ChatEventPublication),
    ToolConfirmation(ToolConfirmationPublication),
}

#[derive(Clone)]
pub struct ChatBridge {
    state: Arc<Mutex<BridgeState>>,
}

struct BridgeState {
    gateway: CoreHostGateway,
    generation_id: String,
    generation_number: u64,
    active: Option<ActiveChat>,
    last_accepted_operation: Option<String>,
    valid: bool,
}

struct ActiveChat {
    operation_id: String,
    cancel_handle: ChatCancelHandle,
    started: bool,
}

pub struct PendingChatSend {
    bridge: ChatBridge,
    publication: ChatSendPublication,
    completion: Receiver<Result<Value, String>>,
}

impl ChatBridge {
    pub fn new(
        gateway: CoreHostGateway,
        generation_id: String,
        generation_number: u64,
    ) -> Result<Self, String> {
        if generation_id.trim().is_empty() || generation_number == 0 {
            return Err("CHAT_BRIDGE_GENERATION_INVALID".to_string());
        }
        Ok(Self {
            state: Arc::new(Mutex::new(BridgeState {
                gateway,
                generation_id,
                generation_number,
                active: None,
                last_accepted_operation: None,
                valid: true,
            })),
        })
    }

    pub fn send(&self, window_label: &str, message: String) -> Result<PendingChatSend, String> {
        self.send_with_attachment(window_label, message, None)
    }

    pub fn send_with_attachment(
        &self,
        window_label: &str,
        message: String,
        attachment_id: Option<String>,
    ) -> Result<PendingChatSend, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
        if !state.valid {
            return Err("CHAT_GENERATION_INVALIDATED".to_string());
        }
        if state.active.is_some() {
            return Err("CHAT_INTERACTION_ACTIVE".to_string());
        }
        let submission = state.gateway.send(
            window_label,
            match attachment_id {
                Some(attachment_id) => {
                    json!({"message": message, "attachmentId": attachment_id})
                }
                None => json!({"message": message}),
            },
        )?;
        if submission.generation_id != state.generation_id {
            return Err("CHAT_GENERATION_MISMATCH".to_string());
        }
        let publication = ChatSendPublication {
            accepted: true,
            operation_id: submission.operation_id.clone(),
            cancel_handle: submission.cancel_handle.as_str().to_string(),
            generation_id: state.generation_id.clone(),
            generation_number: state.generation_number,
        };
        state.active = Some(ActiveChat {
            operation_id: submission.operation_id,
            cancel_handle: submission.cancel_handle,
            started: false,
        });
        Ok(PendingChatSend {
            bridge: self.clone(),
            publication,
            completion: submission.completion,
        })
    }

    pub fn cancel(
        &self,
        window_label: &str,
        operation_id: &str,
        cancel_handle: &str,
    ) -> Result<ChatCancelPublication, String> {
        let state = self
            .state
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
        if !state.valid {
            return Err("CHAT_GENERATION_INVALIDATED".to_string());
        }
        let active = state
            .active
            .as_ref()
            .filter(|active| {
                active.operation_id == operation_id
                    && active.cancel_handle.as_str() == cancel_handle
            })
            .ok_or_else(|| "STALE_CANCEL_HANDLE".to_string())?;
        let response = state.gateway.cancel(window_label, &active.cancel_handle)?;
        Ok(ChatCancelPublication {
            accepted: response
                .pointer("/payload/accepted")
                .or_else(|| response.get("accepted"))
                .and_then(Value::as_bool)
                .unwrap_or(false),
            operation_id: operation_id.to_string(),
        })
    }

    #[cfg(test)]
    pub fn observe_event(&self, message: &Value) -> Result<Option<ChatEventPublication>, String> {
        match self.observe_runtime_event(message)? {
            Some(RuntimeChatEvent::Chat(event)) => Ok(Some(event)),
            Some(RuntimeChatEvent::ToolConfirmation(_)) | None => Ok(None),
        }
    }

    pub fn observe_runtime_event(
        &self,
        message: &Value,
    ) -> Result<Option<RuntimeChatEvent>, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
        if !state.valid {
            return Ok(None);
        }
        if state.gateway.observe_event(message)? == EventDisposition::Ignored {
            return Ok(None);
        }
        let event_type = message
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_CHAT_EVENT".to_string())?;
        let operation_id = message
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_CHAT_EVENT".to_string())?;
        if !state
            .active
            .as_ref()
            .is_some_and(|active| active.operation_id == operation_id)
        {
            return Err("UNKNOWN_CHAT_IDENTITY".to_string());
        }
        if event_type == "chat.started" {
            if let Some(active) = state.active.as_mut() {
                active.started = true;
            }
            state.last_accepted_operation = Some(operation_id.to_string());
        }
        let payload = message
            .get("payload")
            .and_then(Value::as_object)
            .ok_or_else(|| "INVALID_CHAT_EVENT".to_string())?;
        if event_type == "tool.confirmation.requested" {
            return Ok(Some(RuntimeChatEvent::ToolConfirmation(
                ToolConfirmationPublication {
                    generation_id: state.generation_id.clone(),
                    action_id: required_public_text(payload, "actionId")?,
                    title: required_public_text(payload, "title")?,
                    summary: required_public_text(payload, "summary")?,
                    risk: required_public_text(payload, "risk")?,
                    expires_at: required_public_text(payload, "expiresAt")?,
                },
            )));
        }
        let reply = (event_type == "chat.completed")
            .then(|| payload.get("reply").cloned())
            .flatten();
        let error = (event_type == "chat.failed")
            .then(|| project_error(payload.get("error")))
            .transpose()?;
        if matches!(
            event_type,
            "chat.completed" | "chat.failed" | "chat.cancelled"
        ) {
            state.active = None;
        }
        Ok(Some(RuntimeChatEvent::Chat(ChatEventPublication {
            event_type: event_type.to_string(),
            generation_id: state.generation_id.clone(),
            generation_number: state.generation_number,
            operation_id: operation_id.to_string(),
            reply,
            error,
        })))
    }

    pub fn decide_tool_action(&self, action_id: &str, confirm: bool) -> Result<Value, String> {
        let state = self
            .state
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
        if !state.valid {
            return Err("CHAT_GENERATION_INVALIDATED".to_string());
        }
        state.gateway.decide_tool_action(action_id, confirm)
    }

    pub fn invalidate(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.valid = false;
            state.active = None;
            state.last_accepted_operation = None;
            state.gateway.invalidate_generation();
        }
    }

    fn dispatch_failed(&self, operation_id: &str) {
        if let Ok(mut state) = self.state.lock() {
            if state
                .active
                .as_ref()
                .is_some_and(|active| active.operation_id == operation_id && !active.started)
            {
                state.active = None;
            }
        }
    }

    fn submission_accepted(&self, operation_id: &str) -> bool {
        self.state.lock().is_ok_and(|state| {
            state.last_accepted_operation.as_deref() == Some(operation_id)
                || state
                    .active
                    .as_ref()
                    .is_some_and(|active| active.operation_id == operation_id && active.started)
        })
    }
}

fn required_public_text(
    payload: &serde_json::Map<String, Value>,
    key: &str,
) -> Result<String, String> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| "INVALID_TOOL_CONFIRMATION_EVENT".to_string())
}

impl PendingChatSend {
    pub fn wait(self) -> Result<ChatSendPublication, String> {
        let operation_id = self.publication.operation_id.clone();
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if self.bridge.submission_accepted(&operation_id) {
                let bridge = self.bridge.clone();
                let completion = self.completion;
                thread::spawn(move || {
                    let failed = completion
                        .recv()
                        .map_err(|_| "CHAT_DISPATCH_ABORTED".to_string())
                        .and_then(|result| result)
                        .and_then(|response| validate_send_response(&operation_id, response))
                        .is_err();
                    if failed {
                        bridge.dispatch_failed(&operation_id);
                    }
                });
                return Ok(self.publication);
            }
            match self.completion.try_recv() {
                Ok(result) => {
                    if let Err(error) =
                        result.and_then(|response| validate_send_response(&operation_id, response))
                    {
                        self.bridge.dispatch_failed(&operation_id);
                        return Err(error);
                    }
                    return Ok(self.publication);
                }
                Err(TryRecvError::Disconnected) => {
                    self.bridge.dispatch_failed(&operation_id);
                    return Err("CHAT_DISPATCH_ABORTED".to_string());
                }
                Err(TryRecvError::Empty) if Instant::now() >= deadline => {
                    self.bridge.dispatch_failed(&operation_id);
                    return Err("CHAT_START_TIMEOUT".to_string());
                }
                Err(TryRecvError::Empty) => thread::sleep(Duration::from_millis(5)),
            }
        }
    }
}

fn validate_send_response(operation_id: &str, response: Value) -> Result<(), String> {
    if response.get("ok").and_then(Value::as_bool) != Some(true)
        || response
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
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .filter(|value| safe_public_error_message(value))
        .unwrap_or("暂时无法完成回复。");
    Ok(json!({
        "code": code,
        "message": message,
        "retryable": error.get("retryable").and_then(Value::as_bool).unwrap_or(false),
    }))
}

fn safe_public_error_message(value: &str) -> bool {
    if value.is_empty()
        || value.len() > 256
        || value
            .chars()
            .any(|character| matches!(character, '\r' | '\n' | '\\'))
    {
        return false;
    }
    let lower = value.to_ascii_lowercase();
    ![
        "authorization",
        "bearer ",
        "api_key",
        "apikey",
        "credential",
        "password",
        "secret",
        "token=",
        "://",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::Arc, time::Duration};

    use crate::core_host_gateway::GatewayTransport;

    const GENERATION: &str = "00000000-0000-4000-8000-000000003004";

    #[derive(Default)]
    struct FakeTransport;

    impl GatewayTransport for FakeTransport {
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

    fn bridge() -> ChatBridge {
        ChatBridge::new(
            CoreHostGateway::new(GENERATION, Arc::new(FakeTransport)).unwrap(),
            GENERATION.to_string(),
            3,
        )
        .unwrap()
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
    fn one_active_turn_projects_only_public_fields_and_reopens_after_terminal() {
        let bridge = bridge();
        let pending = bridge.send("main", "hello".to_string()).unwrap();
        let operation_id = pending.publication.operation_id.clone();
        assert_eq!(
            bridge.send("main", "second".to_string()).err().unwrap(),
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
        assert!(!serialized.contains("private"));
        assert_eq!(
            failed.error.as_ref().unwrap()["message"],
            "暂时无法完成回复。"
        );
        assert_eq!(failed.error.unwrap()["retryable"], true);
        assert!(bridge.send("main", "next".to_string()).is_ok());
    }

    #[test]
    fn non_main_send_and_forged_cancel_handle_are_rejected_before_core_cancel() {
        let bridge = bridge();
        assert!(bridge.send("settings", "hello".to_string()).is_err());
        let pending = bridge.send("main", "hello".to_string()).unwrap();
        let operation_id = pending.publication.operation_id.clone();
        assert_eq!(
            bridge.cancel("main", &operation_id, "forged").unwrap_err(),
            "STALE_CANCEL_HANDLE"
        );
        pending.wait().unwrap();
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
}
