use std::{
    collections::{hash_map::DefaultHasher, HashMap},
    fs::{self, OpenOptions},
    hash::{Hash, Hasher},
    io::Write,
    path::{Path, PathBuf},
    process::Command,
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender},
        Mutex, OnceLock,
    },
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::{json, Value};

pub const MEMORY_CAPABILITY: &str = "assistant.memory";
pub const SETTINGS_WINDOW: &str = "settings";
pub const MEMORY_DEADLINE_SECONDS: u64 = 5;
pub const MEMORY_MODEL_DEADLINE_SECONDS: u64 = 3;
pub const MEMORY_MODEL_TASK_DEADLINE_SECONDS: u64 = 30 * 60;
pub const MEMORY_MODEL_EVENT: &str = "sakura://memory-model-event";

const LAYERS: [&str; 5] = [
    "core_profile",
    "semantic",
    "episodic",
    "procedural",
    "session",
];
static NEXT_SELECTION: AtomicU64 = AtomicU64::new(1);
static NEXT_TASK: AtomicU64 = AtomicU64::new(1);
static MODEL_TASKS: OnceLock<Mutex<HashMap<String, ModelTask>>> = OnceLock::new();

struct ModelTask {
    generation_id: String,
    window_generation: u64,
    task_handle: String,
    sender: SyncSender<Value>,
    started: bool,
    terminal: bool,
    progress: u64,
}

pub struct ModelTaskRegistration {
    pub task_id: String,
    pub task_handle: String,
    pub receiver: Receiver<Value>,
}

pub fn begin_model_task(
    generation_id: &str,
    window_generation: u64,
) -> Result<ModelTaskRegistration, String> {
    if generation_id.trim().is_empty() || window_generation == 0 {
        return Err("MEMORY_TASK_IDENTITY_INVALID".to_string());
    }
    let nonce = NEXT_TASK.fetch_add(1, Ordering::Relaxed);
    let task_id = opaque_identity("memory-model", generation_id, window_generation, nonce);
    let task_handle = opaque_identity("memory-cancel", generation_id, window_generation, nonce);
    let (sender, receiver) = mpsc::sync_channel(32);
    let task = ModelTask {
        generation_id: generation_id.to_string(),
        window_generation,
        task_handle: task_handle.clone(),
        sender,
        started: false,
        terminal: false,
        progress: 0,
    };
    model_tasks()
        .lock()
        .map_err(|_| "MEMORY_TASK_REGISTRY_UNAVAILABLE".to_string())?
        .insert(task_id.clone(), task);
    Ok(ModelTaskRegistration {
        task_id,
        task_handle,
        receiver,
    })
}

pub fn observe_core_event(message: &Value) -> Result<bool, String> {
    let Some(name) = message.get("name").and_then(Value::as_str) else {
        return Ok(false);
    };
    if !name.starts_with("memory.model.") {
        return Ok(false);
    }
    if !matches!(
        name,
        "memory.model.started"
            | "memory.model.progress"
            | "memory.model.completed"
            | "memory.model.failed"
            | "memory.model.cancelled"
    ) {
        return Err("MEMORY_MODEL_EVENT_INVALID".to_string());
    }
    let task_id = message
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "MEMORY_MODEL_EVENT_INVALID".to_string())?;
    let generation_id = message
        .get("generationId")
        .and_then(Value::as_str)
        .ok_or_else(|| "MEMORY_MODEL_EVENT_INVALID".to_string())?;
    let payload = exact_object(
        message
            .get("payload")
            .ok_or_else(|| "MEMORY_MODEL_EVENT_INVALID".to_string())?,
        &["taskId", "stage", "progress"],
        &["error"],
    )?;
    if payload.get("taskId").and_then(Value::as_str) != Some(task_id) {
        return Err("MEMORY_MODEL_EVENT_INVALID".to_string());
    }
    let stage = bounded_text(payload.get("stage"), "stage", 64, false)?;
    let progress = payload
        .get("progress")
        .and_then(Value::as_u64)
        .filter(|value| *value <= 100)
        .ok_or_else(|| "MEMORY_MODEL_EVENT_INVALID".to_string())?;
    let terminal = matches!(
        name,
        "memory.model.completed" | "memory.model.failed" | "memory.model.cancelled"
    );
    let mut tasks = model_tasks()
        .lock()
        .map_err(|_| "MEMORY_TASK_REGISTRY_UNAVAILABLE".to_string())?;
    let Some(task) = tasks.get_mut(task_id) else {
        return Ok(true);
    };
    if task.generation_id != generation_id || task.terminal {
        return Ok(true);
    }
    if name == "memory.model.started" {
        if task.started {
            return Err("MEMORY_MODEL_EVENT_DUPLICATE".to_string());
        }
        task.started = true;
    } else if !task.started || progress < task.progress {
        return Err("MEMORY_MODEL_EVENT_ORDER_INVALID".to_string());
    }
    if name == "memory.model.failed" {
        validate_public_error(payload.get("error"))?;
    } else if payload.contains_key("error") {
        return Err("MEMORY_MODEL_EVENT_INVALID".to_string());
    }
    task.progress = progress;
    task.terminal = terminal;
    let mut publication = json!({
        "type": name,
        "generationId": generation_id,
        "windowGeneration": task.window_generation,
        "taskId": task_id,
        "stage": stage,
        "progress": progress,
    });
    if let Some(error) = payload.get("error") {
        publication
            .as_object_mut()
            .expect("Memory task publication")
            .insert("error".to_string(), error.clone());
    }
    task.sender
        .try_send(publication)
        .map_err(|_| "MEMORY_MODEL_EVENT_QUEUE_FULL".to_string())?;
    Ok(true)
}

pub fn fail_model_task(task_id: &str, code: &str, message: &str) {
    let Ok(mut tasks) = model_tasks().lock() else {
        return;
    };
    let Some(task) = tasks.get_mut(task_id) else {
        return;
    };
    if task.terminal {
        return;
    }
    task.terminal = true;
    let _ = task.sender.try_send(json!({
        "type": "memory.model.failed",
        "generationId": task.generation_id,
        "windowGeneration": task.window_generation,
        "taskId": task_id,
        "stage": "failed",
        "progress": task.progress,
        "error": {"code": code, "message": message, "retryable": true},
    }));
}

pub fn resolve_cancel_handle(
    task_handle: &str,
    generation_id: &str,
    window_generation: u64,
) -> Result<String, String> {
    model_tasks()
        .lock()
        .map_err(|_| "MEMORY_TASK_REGISTRY_UNAVAILABLE".to_string())?
        .iter()
        .find(|(_, task)| {
            task.task_handle == task_handle
                && task.generation_id == generation_id
                && task.window_generation == window_generation
                && !task.terminal
        })
        .map(|(task_id, _)| task_id.clone())
        .ok_or_else(|| "MEMORY_TASK_HANDLE_STALE".to_string())
}

pub fn remove_model_task(task_id: &str) {
    if let Ok(mut tasks) = model_tasks().lock() {
        tasks.remove(task_id);
    }
}

fn model_tasks() -> &'static Mutex<HashMap<String, ModelTask>> {
    MODEL_TASKS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn opaque_identity(prefix: &str, generation: &str, window: u64, nonce: u64) -> String {
    let time = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let mut hasher = DefaultHasher::new();
    (prefix, generation, window, nonce, time, std::process::id()).hash(&mut hasher);
    format!("{prefix}-{:016x}{nonce:016x}", hasher.finish())
}

fn validate_public_error(value: Option<&Value>) -> Result<(), String> {
    let error = exact_object(
        value.ok_or_else(|| "MEMORY_MODEL_EVENT_INVALID".to_string())?,
        &["code", "message", "retryable"],
        &[],
    )?;
    bounded_text(error.get("code"), "code", 64, false)?;
    bounded_text(error.get("message"), "message", 256, false)?;
    if error.get("retryable").and_then(Value::as_bool).is_none() {
        return Err("MEMORY_MODEL_EVENT_INVALID".to_string());
    }
    Ok(())
}

pub fn select_and_register_archive(generation_id: &str) -> Result<Option<String>, String> {
    let Some(path) = choose_archive()? else {
        return Ok(None);
    };
    register_archive_selection(generation_id, &path).map(Some)
}

pub fn remove_archive_selection(token: &str) {
    if token
        .chars()
        .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        let _ = selection_root()
            .join(format!("{token}.json"))
            .try_exists()
            .and_then(|exists| {
                if exists {
                    fs::remove_file(selection_root().join(format!("{token}.json")))
                } else {
                    Ok(())
                }
            });
        let _ = fs::remove_dir(selection_root());
    }
}

fn register_archive_selection(generation_id: &str, path: &Path) -> Result<String, String> {
    if generation_id.trim().is_empty()
        || !path.is_absolute()
        || !path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("zip"))
    {
        return Err("MEMORY_ARCHIVE_SELECTION_INVALID".to_string());
    }
    let metadata =
        fs::symlink_metadata(path).map_err(|_| "MEMORY_ARCHIVE_SELECTION_INVALID".to_string())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("MEMORY_ARCHIVE_SELECTION_INVALID".to_string());
    }
    let root = selection_root();
    fs::create_dir_all(&root).map_err(|_| "MEMORY_SELECTION_REGISTRY_UNAVAILABLE".to_string())?;
    let token = new_selection_token(generation_id, path);
    let target = root.join(format!("{token}.json"));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&target)
        .map_err(|_| "MEMORY_SELECTION_REGISTRY_UNAVAILABLE".to_string())?;
    let bytes = serde_json::to_vec(&json!({
        "generationId": generation_id,
        "path": path.to_string_lossy(),
    }))
    .map_err(|_| "MEMORY_SELECTION_REGISTRY_UNAVAILABLE".to_string())?;
    if file
        .write_all(&bytes)
        .and_then(|_| file.sync_all())
        .is_err()
    {
        let _ = fs::remove_file(&target);
        return Err("MEMORY_SELECTION_REGISTRY_UNAVAILABLE".to_string());
    }
    Ok(token)
}

fn selection_root() -> PathBuf {
    std::env::temp_dir().join("sakura-runtime-v2-memory-selections")
}

fn new_selection_token(generation_id: &str, path: &Path) -> String {
    let nonce = NEXT_SELECTION.fetch_add(1, Ordering::Relaxed);
    let time = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let mut first = DefaultHasher::new();
    (generation_id, path, nonce, time, std::process::id()).hash(&mut first);
    let mut second = DefaultHasher::new();
    (time.rotate_left(31), nonce.rotate_left(17), generation_id).hash(&mut second);
    format!("{:016x}{:016x}", first.finish(), second.finish())
}

#[cfg(target_os = "windows")]
fn choose_archive() -> Result<Option<PathBuf>, String> {
    let script = concat!(
        "Add-Type -AssemblyName System.Windows.Forms;",
        "$d=New-Object System.Windows.Forms.OpenFileDialog;",
        "$d.Title='选择记忆模型 ZIP';$d.Filter='ZIP (*.zip)|*.zip';",
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){",
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;[Console]::Write($d.FileName)}"
    );
    command_selection(Command::new("powershell.exe").args([
        "-NoProfile",
        "-STA",
        "-Command",
        script,
    ]))
}

#[cfg(target_os = "macos")]
fn choose_archive() -> Result<Option<PathBuf>, String> {
    command_selection(Command::new("osascript").args([
        "-e",
        "POSIX path of (choose file with prompt \"选择记忆模型 ZIP\" of type {\"zip\"})",
    ]))
}

#[cfg(target_os = "linux")]
fn choose_archive() -> Result<Option<PathBuf>, String> {
    command_selection(Command::new("zenity").args([
        "--file-selection",
        "--title=选择记忆模型 ZIP",
        "--file-filter=ZIP | *.zip",
    ]))
}

fn command_selection(command: &mut Command) -> Result<Option<PathBuf>, String> {
    let output = command
        .output()
        .map_err(|_| "MEMORY_ARCHIVE_SELECTOR_UNAVAILABLE".to_string())?;
    if !output.status.success() {
        return Ok(None);
    }
    let text = String::from_utf8(output.stdout)
        .map_err(|_| "MEMORY_ARCHIVE_SELECTION_INVALID".to_string())?;
    let path = text.trim();
    if path.is_empty() {
        Ok(None)
    } else {
        Ok(Some(PathBuf::from(path)))
    }
}

pub fn authorize_settings_window(label: &str) -> Result<(), String> {
    if label != SETTINGS_WINDOW {
        return Err("SETTINGS_WINDOW_REQUIRED".to_string());
    }
    Ok(())
}

pub fn validate_search(payload: &Value) -> Result<(), String> {
    let object = exact_object(payload, &["query", "limit"], &["layer"])?;
    bounded_text(object.get("query"), "query", 4_000, true)?;
    bounded_integer(object.get("limit"), "limit", 1, 120)?;
    if let Some(layer) = object.get("layer") {
        let layer = layer
            .as_str()
            .filter(|value| LAYERS.contains(value))
            .ok_or_else(|| "MEMORY_LAYER_INVALID".to_string())?;
        if layer.is_empty() {
            return Err("MEMORY_LAYER_INVALID".to_string());
        }
    }
    Ok(())
}

pub fn validate_upsert(payload: &Value) -> Result<(), String> {
    let object = exact_object(
        payload,
        &[
            "content",
            "layer",
            "category",
            "source",
            "importance",
            "confidence",
        ],
        &["id"],
    )?;
    bounded_text(object.get("content"), "content", 16_384, false)?;
    bounded_text(object.get("category"), "category", 256, true)?;
    bounded_text(object.get("source"), "source", 256, false)?;
    if let Some(id) = object.get("id") {
        bounded_text(Some(id), "id", 256, false)?;
    }
    let layer = object
        .get("layer")
        .and_then(Value::as_str)
        .filter(|value| LAYERS.contains(value))
        .ok_or_else(|| "MEMORY_LAYER_INVALID".to_string())?;
    if layer.is_empty() {
        return Err("MEMORY_LAYER_INVALID".to_string());
    }
    for field in ["importance", "confidence"] {
        let value = object
            .get(field)
            .and_then(Value::as_f64)
            .filter(|value| (0.0..=1.0).contains(value))
            .ok_or_else(|| format!("MEMORY_FIELD_INVALID:{field}"))?;
        if !value.is_finite() {
            return Err(format!("MEMORY_FIELD_INVALID:{field}"));
        }
    }
    Ok(())
}

pub fn validate_delete(payload: &Value) -> Result<(), String> {
    let object = exact_object(payload, &["id"], &[])?;
    bounded_text(object.get("id"), "id", 256, false).map(|_| ())
}

pub fn validate_settings_save(payload: &Value) -> Result<(), String> {
    let object = exact_object(payload, &["triggerTurns"], &["curationModelSlot"])?;
    bounded_integer(object.get("triggerTurns"), "triggerTurns", 1, 50)?;
    if let Some(slot) = object.get("curationModelSlot") {
        if slot.is_null() {
            return Ok(());
        }
        let slot = exact_object(slot, &["profileId", "model"], &[])?;
        let profile = bounded_text(slot.get("profileId"), "profileId", 64, true)?;
        let model = bounded_text(slot.get("model"), "model", 256, true)?;
        if profile.is_empty() != model.is_empty() {
            return Err("MEMORY_MODEL_SLOT_INCOMPLETE".to_string());
        }
    }
    Ok(())
}

pub fn validate_public_response(value: &Value) -> Result<(), String> {
    reject_sensitive_keys(value)
}

fn exact_object<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, String> {
    let object = value
        .as_object()
        .ok_or_else(|| "MEMORY_PAYLOAD_INVALID".to_string())?;
    if required.iter().any(|key| !object.contains_key(*key))
        || object
            .keys()
            .any(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
    {
        return Err("MEMORY_PAYLOAD_INVALID".to_string());
    }
    Ok(object)
}

fn bounded_text<'a>(
    value: Option<&'a Value>,
    field: &str,
    maximum: usize,
    empty: bool,
) -> Result<&'a str, String> {
    let text = value
        .and_then(Value::as_str)
        .filter(|text| !text.contains('\0') && text.chars().count() <= maximum)
        .ok_or_else(|| format!("MEMORY_FIELD_INVALID:{field}"))?;
    if !empty && text.trim().is_empty() {
        return Err(format!("MEMORY_FIELD_INVALID:{field}"));
    }
    Ok(text)
}

fn bounded_integer(value: Option<&Value>, field: &str, min: i64, max: i64) -> Result<(), String> {
    value
        .and_then(Value::as_i64)
        .filter(|value| (min..=max).contains(value))
        .map(|_| ())
        .ok_or_else(|| format!("MEMORY_FIELD_INVALID:{field}"))
}

fn reject_sensitive_keys(value: &Value) -> Result<(), String> {
    match value {
        Value::Object(object) => {
            for (key, child) in object {
                let normalized = key.to_ascii_lowercase();
                if normalized.contains("api_key")
                    || normalized.contains("password")
                    || normalized.contains("secret")
                    || normalized == "credential"
                    || normalized == "token"
                    || normalized.ends_with("_token")
                {
                    return Err("MEMORY_RESPONSE_SENSITIVE".to_string());
                }
                reject_sensitive_keys(child)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                reject_sensitive_keys(item)?;
            }
        }
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn memory_payloads_are_bounded_and_exact() {
        assert!(validate_search(&json!({"query": "桜", "limit": 12})).is_ok());
        assert!(validate_search(&json!({"query": "x", "limit": 121})).is_err());
        assert!(validate_upsert(&json!({
            "content": "中文と日本語",
            "layer": "semantic",
            "category": "preference",
            "source": "explicit",
            "importance": 0.8,
            "confidence": 0.9
        }))
        .is_ok());
        assert!(validate_delete(&json!({"id": "memory-id", "scope": "other"})).is_err());
    }

    #[test]
    fn public_memory_response_rejects_secret_shaped_fields() {
        assert!(validate_public_response(&json!({"status": "ready", "memories": []})).is_ok());
        assert!(validate_public_response(&json!({"api_key": "private"})).is_err());
    }

    #[test]
    fn archive_selection_registry_exposes_only_one_time_opaque_token() {
        let root = std::env::temp_dir().join(format!(
            "sakura-memory-gateway-test-{}-{}",
            std::process::id(),
            NEXT_SELECTION.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&root).unwrap();
        let archive = root.join("model.zip");
        fs::write(&archive, b"fixture").unwrap();
        let token = register_archive_selection("generation-test", &archive).unwrap();
        assert!(!token.contains("model"));
        let registry = selection_root().join(format!("{token}.json"));
        let document: Value = serde_json::from_slice(&fs::read(&registry).unwrap()).unwrap();
        assert_eq!(document["generationId"], "generation-test");
        remove_archive_selection(&token);
        assert!(!registry.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn model_task_registry_binds_cancel_and_accepts_one_ordered_terminal() {
        let registration = begin_model_task("generation-model", 7).unwrap();
        assert_eq!(
            resolve_cancel_handle(&registration.task_handle, "generation-model", 7).unwrap(),
            registration.task_id
        );
        let model_event = |name: &str, progress: u64| {
            json!({
                "kind": "event",
                "generationId": "generation-model",
                "id": registration.task_id,
                "name": name,
                "payload": {
                    "taskId": registration.task_id,
                    "stage": name.rsplit('.').next().unwrap(),
                    "progress": progress,
                }
            })
        };
        assert!(observe_core_event(&model_event("memory.model.started", 0)).unwrap());
        assert!(observe_core_event(&model_event("memory.model.progress", 50)).unwrap());
        assert!(observe_core_event(&model_event("memory.model.completed", 100)).unwrap());
        assert!(observe_core_event(&model_event("memory.model.completed", 100)).unwrap());
        let publications: Vec<Value> = registration.receiver.try_iter().collect();
        assert_eq!(publications.len(), 3);
        assert_eq!(publications[2]["type"], "memory.model.completed");
        assert!(resolve_cancel_handle(&registration.task_handle, "generation-model", 7).is_err());
        remove_model_task(&registration.task_id);
    }
}
