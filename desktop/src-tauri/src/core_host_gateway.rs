//! Generation-scoped allowlisted Gateway for the headless real chat boundary.

use std::{
    collections::{HashMap, VecDeque},
    fmt,
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver},
        Arc, Mutex,
    },
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use serde_json::{json, Value};

use crate::{core_host_protocol::validate_envelope, core_host_runtime::ConcurrentRequestHandle};

pub const CHAT_REGISTRY_LIMIT: usize = 32;
pub const CHAT_PAYLOAD_LIMIT: usize = 64 * 1024;
pub const CHAT_SEND_DEADLINE: Duration = Duration::from_secs(30);
pub const CHAT_CANCEL_DEADLINE: Duration = Duration::from_secs(1);
pub const TOOL_DECISION_DEADLINE: Duration = Duration::from_secs(1);
const ALLOWED_WINDOW: &str = "main";
const CHAT_TERMINALS: [&str; 3] = ["chat.completed", "chat.failed", "chat.cancelled"];
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

pub(crate) trait GatewayTransport: Send + Sync {
    fn request(
        &self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
        scheduling: &'static str,
    ) -> Result<Value, String>;
}

impl GatewayTransport for ConcurrentRequestHandle {
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

#[derive(Clone, PartialEq, Eq)]
pub struct ChatCancelHandle {
    opaque: String,
}

impl fmt::Debug for ChatCancelHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChatCancelHandle")
            .field("opaque", &"[OPAQUE]")
            .finish()
    }
}

impl ChatCancelHandle {
    pub fn as_str(&self) -> &str {
        &self.opaque
    }
}

#[derive(Debug)]
pub struct ChatSubmission {
    pub operation_id: String,
    pub generation_id: String,
    pub cancel_handle: ChatCancelHandle,
    pub completion: Receiver<Result<Value, String>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventDisposition {
    Accepted,
    Ignored,
}

#[derive(Debug, Clone)]
struct ChatEntry {
    cancel_requested: bool,
    started: bool,
    terminal: Option<String>,
}

struct GatewayState {
    generation_id: String,
    valid: bool,
    entries: HashMap<String, ChatEntry>,
    handles: HashMap<String, String>,
    order: VecDeque<String>,
    pending_action: Option<PendingAction>,
}

#[derive(Debug, Clone)]
struct PendingAction {
    action_id: String,
    operation_id: String,
}

#[derive(Clone)]
pub struct CoreHostGateway {
    transport: Arc<dyn GatewayTransport>,
    state: Arc<Mutex<GatewayState>>,
}

impl fmt::Debug for CoreHostGateway {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let (generation_id, valid, len) = self.state.lock().map_or_else(
            |_| ("[UNAVAILABLE]".to_string(), false, 0),
            |state| {
                (
                    state.generation_id.clone(),
                    state.valid,
                    state.entries.len(),
                )
            },
        );
        formatter
            .debug_struct("CoreHostGateway")
            .field("generation_id", &generation_id)
            .field("valid", &valid)
            .field("registry_len", &len)
            .finish_non_exhaustive()
    }
}

impl CoreHostGateway {
    pub(crate) fn new(
        generation_id: impl Into<String>,
        transport: Arc<dyn GatewayTransport>,
    ) -> Result<Self, String> {
        let generation_id = generation_id.into();
        if generation_id.trim().is_empty() {
            return Err("INVALID_GENERATION: Gateway generation is empty".to_string());
        }
        Ok(Self {
            transport,
            state: Arc::new(Mutex::new(GatewayState {
                generation_id,
                valid: true,
                entries: HashMap::new(),
                handles: HashMap::new(),
                order: VecDeque::new(),
                pending_action: None,
            })),
        })
    }

    pub fn dispatch(
        &self,
        window_label: &str,
        command: &str,
        payload: Value,
        cancel_handle: Option<&ChatCancelHandle>,
    ) -> Result<Option<ChatSubmission>, String> {
        match command {
            "chat.send" => self.send(window_label, payload).map(Some),
            "chat.cancel" => {
                if !payload.as_object().is_some_and(|object| object.is_empty()) {
                    return Err("INVALID_CHAT_CANCEL: cancel payload must be empty".to_string());
                }
                let handle = cancel_handle.ok_or_else(|| {
                    "INVALID_CHAT_CANCEL: Rust-issued cancel handle required".to_string()
                })?;
                self.cancel(window_label, handle)?;
                Ok(None)
            }
            _ => Err("GATEWAY_COMMAND_DENIED: command is not allowlisted".to_string()),
        }
    }

    pub fn send(&self, window_label: &str, payload: Value) -> Result<ChatSubmission, String> {
        authorize_window(window_label)?;
        validate_chat_payload(&payload)?;
        let operation_id = new_identity("chat");
        let opaque = new_identity("chat-cancel");
        let generation_id = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| "CHAT_REGISTRY_UNAVAILABLE: registry lock failed".to_string())?;
            ensure_valid(&state)?;
            prune_terminal(&mut state);
            if state.entries.len() >= CHAT_REGISTRY_LIMIT {
                return Err("CHAT_REGISTRY_FULL: chat registry is full".to_string());
            }
            state.entries.insert(
                operation_id.clone(),
                ChatEntry {
                    cancel_requested: false,
                    started: false,
                    terminal: None,
                },
            );
            state.handles.insert(opaque.clone(), operation_id.clone());
            state.order.push_back(operation_id.clone());
            state.generation_id.clone()
        };
        let mut core_payload = payload;
        core_payload
            .as_object_mut()
            .expect("validated chat payload")
            .insert(
                "operationId".to_string(),
                Value::String(operation_id.clone()),
            );
        let transport = Arc::clone(&self.transport);
        let (completion_sender, completion) = mpsc::sync_channel(1);
        let request_operation_id = operation_id.clone();
        thread::Builder::new()
            .name(format!("sakura-chat-request-{operation_id}"))
            .spawn(move || {
                let result = transport.request(
                    &request_operation_id,
                    "chat.send",
                    core_payload,
                    CHAT_SEND_DEADLINE,
                    "interactive",
                );
                let _ = completion_sender.send(result);
            })
            .map_err(|error| format!("CHAT_DISPATCH_FAILED: {error}"))?;
        Ok(ChatSubmission {
            operation_id,
            generation_id,
            cancel_handle: ChatCancelHandle { opaque },
            completion,
        })
    }

    pub fn cancel(&self, window_label: &str, handle: &ChatCancelHandle) -> Result<Value, String> {
        authorize_window(window_label)?;
        let operation_id = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| "CHAT_REGISTRY_UNAVAILABLE: registry lock failed".to_string())?;
            ensure_valid(&state)?;
            let operation_id = state.handles.get(handle.as_str()).cloned().ok_or_else(|| {
                "STALE_CANCEL_HANDLE: cancel handle is unknown or stale".to_string()
            })?;
            let entry = state.entries.get_mut(&operation_id).ok_or_else(|| {
                "STALE_CANCEL_HANDLE: chat operation is not registered".to_string()
            })?;
            if entry.terminal.is_some() || entry.cancel_requested {
                return Ok(json!({"accepted": false, "operationId": operation_id}));
            }
            entry.cancel_requested = true;
            operation_id
        };
        self.transport.request(
            &new_identity("chat-cancel-request"),
            "chat.cancel",
            json!({"operationId": operation_id}),
            CHAT_CANCEL_DEADLINE,
            "control",
        )
    }

    pub fn observe_event(&self, message: &Value) -> Result<EventDisposition, String> {
        validate_envelope(message).map_err(|error| error.to_string())?;
        let object = message
            .as_object()
            .ok_or_else(|| "INVALID_CHAT_EVENT: event must be an object".to_string())?;
        if object.get("kind").and_then(Value::as_str) != Some("event") {
            return Err("INVALID_CHAT_EVENT: Gateway accepts events only".to_string());
        }
        let event_name = object
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_CHAT_EVENT: event name missing".to_string())?;
        if event_name != "chat.started"
            && event_name != "tool.confirmation.requested"
            && !CHAT_TERMINALS.contains(&event_name)
        {
            return Err("INVALID_CHAT_EVENT: event name is not allowlisted".to_string());
        }
        let operation_id = object
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| "INVALID_CHAT_EVENT: event identity missing".to_string())?;
        if event_name != "tool.confirmation.requested" {
            if object
                .get("payload")
                .and_then(Value::as_object)
                .and_then(|payload| payload.get("operationId"))
                .and_then(Value::as_str)
                != Some(operation_id)
            {
                return Err("INVALID_CHAT_EVENT: payload identity mismatch".to_string());
            }
        }
        validate_chat_event_payload(
            event_name,
            object.get("payload").expect("validated payload"),
        )?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "CHAT_REGISTRY_UNAVAILABLE: registry lock failed".to_string())?;
        ensure_valid(&state)?;
        if object.get("generationId").and_then(Value::as_str) != Some(state.generation_id.as_str())
        {
            return Err("GENERATION_MISMATCH: chat event is stale".to_string());
        }
        let Some(entry) = state.entries.get_mut(operation_id) else {
            return Err("UNKNOWN_CHAT_IDENTITY: event operation is unknown".to_string());
        };
        if event_name == "chat.started" {
            if entry.started || entry.terminal.is_some() {
                return Ok(EventDisposition::Ignored);
            }
            entry.started = true;
            return Ok(EventDisposition::Accepted);
        }
        if event_name == "tool.confirmation.requested" {
            if !entry.started || entry.terminal.is_some() {
                return Err("INVALID_TOOL_CONFIRMATION_EVENT: chat is not active".to_string());
            }
            let action_id = object
                .get("payload")
                .and_then(Value::as_object)
                .and_then(|payload| payload.get("actionId"))
                .and_then(Value::as_str)
                .ok_or_else(|| "INVALID_TOOL_CONFIRMATION_EVENT: action ID missing".to_string())?;
            if state.pending_action.is_some() {
                return Err("TOOL_CONFIRMATION_BUSY: another action is pending".to_string());
            }
            state.pending_action = Some(PendingAction {
                action_id: action_id.to_string(),
                operation_id: operation_id.to_string(),
            });
            return Ok(EventDisposition::Accepted);
        }
        if entry.terminal.is_some() {
            return Ok(EventDisposition::Ignored);
        }
        if !entry.started {
            return Err("INVALID_CHAT_EVENT: terminal preceded chat.started".to_string());
        }
        entry.terminal = Some(event_name.to_string());
        if state
            .pending_action
            .as_ref()
            .is_some_and(|pending| pending.operation_id == operation_id)
        {
            state.pending_action = None;
        }
        Ok(EventDisposition::Accepted)
    }

    pub fn decide_tool_action(&self, action_id: &str, confirm: bool) -> Result<Value, String> {
        validate_action_id(action_id)?;
        let pending = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| "CHAT_REGISTRY_UNAVAILABLE: registry lock failed".to_string())?;
            ensure_valid(&state)?;
            let pending = state
                .pending_action
                .as_ref()
                .filter(|pending| pending.action_id == action_id)
                .cloned()
                .ok_or_else(|| "ACTION_NOT_PENDING: action is unknown or stale".to_string())?;
            state.pending_action = None;
            pending
        };
        self.transport.request(
            &new_identity("tool-decision"),
            if confirm {
                "tool.confirm"
            } else {
                "tool.reject"
            },
            json!({"actionId": pending.action_id}),
            TOOL_DECISION_DEADLINE,
            "control",
        )
    }

    pub fn invalidate_generation(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.valid = false;
            state.entries.clear();
            state.handles.clear();
            state.order.clear();
            state.pending_action = None;
        }
    }

    pub fn registry_len(&self) -> usize {
        self.state.lock().map_or(0, |state| state.entries.len())
    }
}

fn authorize_window(window_label: &str) -> Result<(), String> {
    (window_label == ALLOWED_WINDOW)
        .then_some(())
        .ok_or_else(|| "GATEWAY_WINDOW_DENIED: caller window is not authorized".to_string())
}

fn ensure_valid(state: &GatewayState) -> Result<(), String> {
    state
        .valid
        .then_some(())
        .ok_or_else(|| "GENERATION_INVALIDATED: Gateway generation is closed".to_string())
}

fn prune_terminal(state: &mut GatewayState) {
    while state.entries.len() >= CHAT_REGISTRY_LIMIT {
        let Some(oldest) = state.order.front().cloned() else {
            break;
        };
        if !state
            .entries
            .get(&oldest)
            .is_some_and(|entry| entry.terminal.is_some())
        {
            break;
        }
        state.order.pop_front();
        state.entries.remove(&oldest);
        state
            .handles
            .retain(|_, operation_id| operation_id != &oldest);
    }
}

fn validate_chat_payload(payload: &Value) -> Result<(), String> {
    let object = payload
        .as_object()
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: payload must be an object".to_string())?;
    if object.keys().any(|key| key != "message") {
        return Err("INVALID_CHAT_PAYLOAD: payload contains forbidden fields".to_string());
    }
    let message = object
        .get("message")
        .and_then(Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .ok_or_else(|| "INVALID_CHAT_PAYLOAD: message must be non-empty".to_string())?;
    if message.len() > CHAT_PAYLOAD_LIMIT
        || serde_json::to_vec(payload).map_or(true, |encoded| encoded.len() > CHAT_PAYLOAD_LIMIT)
    {
        return Err("CHAT_PAYLOAD_TOO_LARGE: payload exceeds its limit".to_string());
    }
    Ok(())
}

fn validate_chat_event_payload(name: &str, payload: &Value) -> Result<(), String> {
    let payload = payload
        .as_object()
        .ok_or_else(|| "INVALID_CHAT_EVENT: payload must be an object".to_string())?;
    match name {
        "tool.confirmation.requested" => validate_tool_confirmation_payload(payload),
        "chat.started" if payload.len() == 1 => Ok(()),
        "chat.cancelled" if payload.len() == 2 => validate_history_status(payload),
        "chat.completed" if payload.len() == 3 => {
            validate_history_status(payload)?;
            validate_chat_reply(payload.get("reply"))
        }
        "chat.failed" if payload.len() == 3 => {
            validate_history_status(payload)?;
            let error = payload
                .get("error")
                .and_then(Value::as_object)
                .ok_or_else(|| "INVALID_CHAT_EVENT: failed event error is invalid".to_string())?;
            if error.len() == 4
                && error.get("code").is_some_and(Value::is_string)
                && error.get("message").is_some_and(Value::is_string)
                && error.get("retryable").is_some_and(Value::is_boolean)
                && error.get("details").is_some_and(Value::is_object)
            {
                Ok(())
            } else {
                Err("INVALID_CHAT_EVENT: failed event error shape is invalid".to_string())
            }
        }
        _ => Err("INVALID_CHAT_EVENT: event payload shape is invalid".to_string()),
    }
}

fn validate_tool_confirmation_payload(
    object: &serde_json::Map<String, Value>,
) -> Result<(), String> {
    let expected = ["actionId", "title", "summary", "risk", "expiresAt"];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err("INVALID_TOOL_CONFIRMATION_EVENT: payload fields are invalid".to_string());
    }
    let action_id = object
        .get("actionId")
        .and_then(Value::as_str)
        .ok_or_else(|| "INVALID_TOOL_CONFIRMATION_EVENT: action ID is invalid".to_string())?;
    validate_action_id(action_id)?;
    for (key, limit) in [("title", 80), ("summary", 320), ("expiresAt", 64)] {
        let value = object.get(key).and_then(Value::as_str).unwrap_or_default();
        if value.is_empty() || value.len() > limit {
            return Err(format!("INVALID_TOOL_CONFIRMATION_EVENT: {key} is invalid"));
        }
    }
    if !matches!(
        object.get("risk").and_then(Value::as_str),
        Some("write" | "destructive")
    ) {
        return Err("INVALID_TOOL_CONFIRMATION_EVENT: risk is invalid".to_string());
    }
    Ok(())
}

fn validate_action_id(action_id: &str) -> Result<(), String> {
    if action_id.len() != 32
        || !action_id
            .bytes()
            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
    {
        return Err("ACTION_ID_INVALID: action ID is invalid".to_string());
    }
    Ok(())
}

fn validate_history_status(payload: &serde_json::Map<String, Value>) -> Result<(), String> {
    if matches!(
        payload.get("historyStatus").and_then(Value::as_str),
        Some("saved" | "degraded")
    ) {
        Ok(())
    } else {
        Err("INVALID_CHAT_EVENT: history status is invalid".to_string())
    }
}

fn validate_chat_reply(reply: Option<&Value>) -> Result<(), String> {
    let reply = reply
        .and_then(Value::as_object)
        .ok_or_else(|| "INVALID_CHAT_EVENT: completed reply is invalid".to_string())?;
    if reply.len() != 1 {
        return Err("INVALID_CHAT_EVENT: completed reply shape is invalid".to_string());
    }
    let segments = reply
        .get("segments")
        .and_then(Value::as_array)
        .ok_or_else(|| "INVALID_CHAT_EVENT: completed segments are invalid".to_string())?;
    for segment in segments {
        let segment = segment
            .as_object()
            .ok_or_else(|| "INVALID_CHAT_EVENT: completed segment is invalid".to_string())?;
        if segment.len() != 5
            || !["text", "translation", "tone", "portrait"]
                .iter()
                .all(|key| segment.get(*key).is_some_and(Value::is_string))
            || !segment.get("suppressTts").is_some_and(Value::is_boolean)
        {
            return Err("INVALID_CHAT_EVENT: completed segment shape is invalid".to_string());
        }
    }
    Ok(())
}

fn new_identity(prefix: &str) -> String {
    let counter = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    format!("{prefix}-{nanos:032x}-{counter:016x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    const GENERATION: &str = "00000000-0000-4000-8000-000000002202";

    #[derive(Default)]
    struct FakeTransport {
        requests: Mutex<Vec<(String, String, Value, Duration, &'static str)>>,
    }

    impl GatewayTransport for FakeTransport {
        fn request(
            &self,
            request_id: &str,
            name: &str,
            payload: Value,
            deadline: Duration,
            scheduling: &'static str,
        ) -> Result<Value, String> {
            self.requests.lock().unwrap().push((
                request_id.into(),
                name.into(),
                payload,
                deadline,
                scheduling,
            ));
            Ok(json!({"ok": true, "payload": {"accepted": true}}))
        }
    }

    fn gateway() -> (CoreHostGateway, Arc<FakeTransport>) {
        let transport = Arc::new(FakeTransport::default());
        (
            CoreHostGateway::new(GENERATION, transport.clone()).unwrap(),
            transport,
        )
    }

    fn chat_event(operation_id: &str, name: &str) -> Value {
        let payload = if name == "chat.completed" {
            json!({
                "operationId": operation_id,
                "reply": {"segments": [{
                    "text": "hello", "translation": "", "tone": "neutral",
                    "portrait": "", "suppressTts": false
                }]},
                "historyStatus": "saved"
            })
        } else if name == "chat.cancelled" {
            json!({"operationId": operation_id, "historyStatus": "saved"})
        } else {
            json!({"operationId": operation_id})
        };
        json!({
            "protocolMajor": 2, "protocolMinor": 2, "kind": "event",
            "generationId": GENERATION,
            "generationCredential": "22222222222222222222222222222222",
            "id": operation_id, "name": name,
            "payload": payload
        })
    }

    fn tool_confirmation_event(operation_id: &str, action_id: &str) -> Value {
        json!({
            "protocolMajor": 2, "protocolMinor": 2, "kind": "event",
            "generationId": GENERATION,
            "generationCredential": "22222222222222222222222222222222",
            "id": operation_id, "name": "tool.confirmation.requested",
            "payload": {
                "actionId": action_id,
                "title": "删除长期记忆",
                "summary": "删除记忆 memory-1",
                "risk": "destructive",
                "expiresAt": "2026-08-10T00:00:00Z"
            }
        })
    }

    #[test]
    fn unknown_window_command_and_transport_fields_are_denied() {
        let (gateway, _) = gateway();
        assert!(gateway
            .dispatch("main", "future.command", json!({}), None)
            .unwrap_err()
            .starts_with("GATEWAY_COMMAND_DENIED:"));
        assert!(gateway.send("settings", json!({"message": "x"})).is_err());
        for field in [
            "protocolMajor",
            "generationId",
            "generationCredential",
            "id",
            "deadlineMs",
            "priority",
            "history",
            "messages",
            "model",
            "apiKey",
            "operationId",
        ] {
            let mut payload = json!({"message": "hello"});
            payload[field] = json!("forged");
            assert!(gateway.send("main", payload).is_err(), "{field}");
        }
    }

    #[test]
    fn completed_reply_and_history_status_require_the_exact_public_shape() {
        let operation_id = "chat-exact";
        let mut valid = chat_event(operation_id, "chat.completed");
        assert!(validate_chat_event_payload("chat.completed", &valid["payload"]).is_ok());
        for field in ["_debug", "actions", "prompt", "model", "apiKey"] {
            valid["payload"]["reply"][field] = json!("PRIVATE");
            assert!(validate_chat_event_payload("chat.completed", &valid["payload"]).is_err());
            valid["payload"]["reply"]
                .as_object_mut()
                .expect("reply object")
                .remove(field);
        }
        for invalid in [json!(null), json!("saved-ish"), json!(true)] {
            valid["payload"]["historyStatus"] = invalid;
            assert!(validate_chat_event_payload("chat.completed", &valid["payload"]).is_err());
        }
    }

    #[test]
    fn handle_is_opaque_and_cancel_uses_reserved_control_scheduling() {
        let (gateway, transport) = gateway();
        let submission = gateway.send("main", json!({"message": "hello"})).unwrap();
        assert!(!format!("{:?}", submission.cancel_handle).contains(GENERATION));
        submission
            .completion
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap();
        assert_eq!(
            gateway.cancel("main", &submission.cancel_handle).unwrap()["ok"],
            true
        );
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            (requests[0].1.as_str(), requests[0].4),
            ("chat.send", "interactive")
        );
        assert_eq!(
            (requests[1].1.as_str(), requests[1].3, requests[1].4),
            ("chat.cancel", CHAT_CANCEL_DEADLINE, "control")
        );
    }

    #[test]
    fn started_has_one_terminal_and_invalidation_clears_handles() {
        let (gateway, transport) = gateway();
        let submission = gateway.send("main", json!({"message": "hello"})).unwrap();
        submission
            .completion
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap();
        let operation_id = transport.requests.lock().unwrap()[0].0.clone();
        assert_eq!(
            gateway
                .observe_event(&chat_event(&operation_id, "chat.started"))
                .unwrap(),
            EventDisposition::Accepted
        );
        assert_eq!(
            gateway
                .observe_event(&chat_event(&operation_id, "chat.completed"))
                .unwrap(),
            EventDisposition::Accepted
        );
        assert_eq!(
            gateway
                .observe_event(&chat_event(&operation_id, "chat.cancelled"))
                .unwrap(),
            EventDisposition::Ignored
        );
        gateway.invalidate_generation();
        assert_eq!(gateway.registry_len(), 0);
        assert!(gateway.cancel("main", &submission.cancel_handle).is_err());
    }

    #[test]
    fn wp_4_02_action_id_is_validated_bound_and_consumed_once() {
        let (gateway, transport) = gateway();
        let submission = gateway.send("main", json!({"message": "forget"})).unwrap();
        submission
            .completion
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap();
        let operation_id = transport.requests.lock().unwrap()[0].0.clone();
        gateway
            .observe_event(&chat_event(&operation_id, "chat.started"))
            .unwrap();
        let action_id = "a".repeat(32);
        assert_eq!(
            gateway
                .observe_event(&tool_confirmation_event(&operation_id, &action_id))
                .unwrap(),
            EventDisposition::Accepted
        );

        assert_eq!(
            gateway.decide_tool_action(&action_id, true).unwrap()["ok"],
            true
        );
        assert!(gateway.decide_tool_action(&action_id, true).is_err());
        let requests = transport.requests.lock().unwrap();
        let decision = requests.last().unwrap();
        assert_eq!(
            (decision.1.as_str(), decision.4),
            ("tool.confirm", "control")
        );
        assert_eq!(decision.2, json!({"actionId": action_id}));
    }

    #[test]
    fn wp_4_02_tool_confirmation_rejects_arguments_and_concurrent_actions() {
        let (gateway, transport) = gateway();
        let submission = gateway.send("main", json!({"message": "forget"})).unwrap();
        submission
            .completion
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap();
        let operation_id = transport.requests.lock().unwrap()[0].0.clone();
        gateway
            .observe_event(&chat_event(&operation_id, "chat.started"))
            .unwrap();
        let action_id = "b".repeat(32);
        let mut forged = tool_confirmation_event(&operation_id, &action_id);
        forged["payload"]["arguments"] = json!({"memory_id": "forged"});
        assert!(gateway.observe_event(&forged).is_err());

        gateway
            .observe_event(&tool_confirmation_event(&operation_id, &action_id))
            .unwrap();
        assert!(gateway
            .observe_event(&tool_confirmation_event(&operation_id, &"c".repeat(32)))
            .is_err());
        assert!(gateway.decide_tool_action("short", false).is_err());
        assert_eq!(
            gateway.decide_tool_action(&action_id, false).unwrap()["ok"],
            true
        );
    }
}
