//! Host microphone input. PCM and capture paths never cross the WebView bridge.
use std::{
    collections::VecDeque,
    fs::{self, OpenOptions},
    io::{BufWriter, Write},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime},
};

use crate::{
    audio::{AudioState, InputPlaybackPause},
    product_shell,
    runtime_log::{Correlation, RuntimeLogEvent, RuntimeLogService, Severity},
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleHandle, ShellLifecycleState,
    },
};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, WebviewWindow};

const MAX_SECONDS: usize = 60;
const OUTPUT_RATE: usize = 16_000;
const FRAME_INTERVAL: Duration = Duration::from_millis(50);

#[derive(Default)]
pub(crate) struct AsrState {
    active: Mutex<Option<Arc<CaptureSession>>>,
    input: Mutex<Option<ActiveInput>>,
    owners: Mutex<VecDeque<(String, String)>>,
    closing: AtomicBool,
}

struct ActiveInput {
    id: String,
    window_label: String,
    handle: ShellLifecycleHandle,
}

struct CaptureSession {
    id: String,
    window_label: String,
    cancelled: AtomicBool,
    failure: Mutex<Option<String>>,
    stop: AtomicBool,
    submitted: AtomicBool,
    result: Mutex<Option<Result<Value, String>>>,
    completed: tokio::sync::Notify,
    worker: Mutex<Option<JoinHandle<()>>>,
    log: Mutex<Option<CaptureLog>>,
}

struct CaptureLog {
    service: RuntimeLogService,
    generation: String,
    started: Option<Instant>,
    duration: Option<Duration>,
    finished: bool,
}

impl CaptureLog {
    fn record(&self, id: &str, state: &str, reason_code: &str) {
        let (event, message, severity) = match state {
            "started" => (
                "asr.capture.started",
                "Microphone capture started",
                Severity::Info,
            ),
            "finished" => (
                "asr.capture.finished",
                "Microphone capture finished",
                Severity::Info,
            ),
            "cancelled" => (
                "asr.capture.cancelled",
                "Microphone capture cancelled",
                Severity::Info,
            ),
            _ => (
                "asr.capture.failed",
                "Microphone capture failed",
                Severity::Warning,
            ),
        };
        let reason_code = reason_code
            .split(['|', ':'])
            .next()
            .filter(|code| {
                !code.is_empty()
                    && code.len() <= 64
                    && code.bytes().all(|byte| {
                        byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_'
                    })
            })
            .unwrap_or("ASR_CAPTURE_FAILED");
        let _ = self.service.submit(
            RuntimeLogEvent::rust(severity, "asr", event, message)
                .correlation(Correlation {
                    generation_id: Some(self.generation.clone()),
                    operation_id: Some(id.to_owned()),
                    ..Correlation::default()
                })
                .attributes(json!({
                    "recording_id": id,
                    "duration_ms": self.duration.unwrap_or_default().as_millis() as u64,
                    "reason_code": reason_code,
                })),
        );
    }
}

impl CaptureSession {
    fn new(id: String) -> Self {
        Self {
            id,
            window_label: "main".into(),
            cancelled: AtomicBool::new(false),
            failure: Mutex::new(None),
            stop: AtomicBool::new(false),
            submitted: AtomicBool::new(false),
            result: Mutex::new(None),
            completed: tokio::sync::Notify::new(),
            worker: Mutex::new(None),
            log: Mutex::new(None),
        }
    }
    fn observe(&self, service: RuntimeLogService, generation: String) {
        if let Ok(mut log) = self.log.lock() {
            *log = Some(CaptureLog {
                service,
                generation,
                started: None,
                duration: None,
                finished: false,
            });
        }
    }
    fn started_capture(&self) {
        if let Ok(mut log) = self.log.lock() {
            if let Some(log) = log
                .as_mut()
                .filter(|log| log.started.is_none() && !log.finished)
            {
                log.started = Some(Instant::now());
                log.record(&self.id, "started", "ASR_CAPTURE_STARTED");
            }
        }
    }
    fn ended_capture(&self) {
        if let Ok(mut log) = self.log.lock() {
            if let Some(log) = log.as_mut() {
                log.duration = log.started.map(|started| started.elapsed());
            }
        }
    }
    fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }
    fn fail(&self, code: String) {
        if let Ok(mut failure) = self.failure.lock() {
            *failure = Some(code);
        }
        self.cancel();
    }
    fn cancellation_error(&self) -> String {
        self.failure
            .lock()
            .ok()
            .and_then(|failure| failure.clone())
            .unwrap_or_else(|| "ASR_CANCELLED".into())
    }
    fn claim_submission(&self) -> Result<(), String> {
        self.submitted
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map(|_| ())
            .map_err(|_| "ASR_ALREADY_SUBMITTED".into())
    }
    fn finish(&self, result: Result<Value, String>) {
        if let Ok(mut log) = self.log.lock() {
            if let Some(log) = log.as_mut().filter(|log| !log.finished) {
                log.finished = true;
                log.duration = log
                    .duration
                    .or_else(|| log.started.map(|started| started.elapsed()));
                let (state, reason) = match result.as_ref() {
                    Ok(_) if self.stop.load(Ordering::SeqCst) => {
                        ("finished", "ASR_CAPTURE_STOPPED")
                    }
                    Ok(_) => ("finished", "ASR_RECORDING_LIMIT"),
                    Err(error) if error.split(['|', ':']).next() == Some("ASR_CANCELLED") => {
                        ("cancelled", "ASR_CANCELLED")
                    }
                    Err(error) => ("failed", error.as_str()),
                };
                log.record(&self.id, state, reason);
            }
        }
        if let Ok(mut stored) = self.result.lock() {
            *stored = Some(result);
        }
        self.completed.notify_waiters();
    }
    async fn wait(&self) -> Result<Value, String> {
        loop {
            let notification = self.completed.notified();
            if let Some(result) = self
                .result
                .lock()
                .map_err(|_| "ASR_CAPTURE_FAILED")?
                .clone()
            {
                return result;
            }
            notification.await;
        }
    }
}

impl AsrState {
    fn reserve(&self, id: &str, window_label: &str) -> Result<Arc<CaptureSession>, String> {
        let mut active = self.active.lock().map_err(|_| "ASR_CAPTURE_FAILED")?;
        if self.closing.load(Ordering::SeqCst) {
            return Err("ASR_CANCELLED".into());
        }
        if let Some(previous) = active.as_ref() {
            if previous
                .result
                .lock()
                .map_err(|_| "ASR_CAPTURE_FAILED")?
                .is_none()
            {
                return Err("ASR_BUSY".into());
            }
        }
        let mut session = CaptureSession::new(id.to_owned());
        session.window_label = window_label.to_owned();
        let session = Arc::new(session);
        *active = Some(session.clone());
        Ok(session)
    }
    fn current(&self, id: &str) -> Result<Arc<CaptureSession>, String> {
        self.active
            .lock()
            .map_err(|_| "ASR_CAPTURE_FAILED")?
            .as_ref()
            .filter(|session| session.id == id)
            .cloned()
            .ok_or_else(|| "ASR_RECORDING_STALE".into())
    }
    fn remember_owner(&self, id: &str, window_label: &str) -> Result<(), String> {
        let mut owners = self.owners.lock().map_err(|_| "ASR_CAPTURE_FAILED")?;
        if owners.iter().any(|(known, _)| known == id) {
            return Err("ASR_RECORDING_REUSED".into());
        }
        owners.push_back((id.to_owned(), window_label.to_owned()));
        while owners.len() > 64 {
            owners.pop_front();
        }
        Ok(())
    }
    fn assert_owner(&self, id: &str, window_label: &str) -> Result<(), String> {
        if self
            .owners
            .lock()
            .map_err(|_| "ASR_CAPTURE_FAILED")?
            .iter()
            .any(|(known, owner)| known == id && owner == window_label)
        {
            Ok(())
        } else {
            Err("ASR_INPUT_NOT_OWNED".into())
        }
    }
    fn clear_input(&self, id: &str) {
        if let Ok(mut input) = self.input.lock() {
            if input.as_ref().is_some_and(|active| active.id == id) {
                input.take();
            }
        }
    }
    pub(crate) fn cancel_window(&self, window_label: &str) {
        self.cancel_matching(Some(window_label));
    }
    pub(crate) fn cancel_active(&self) {
        self.cancel_matching(None);
    }
    fn cancel_matching(&self, window_label: Option<&str>) {
        if let Ok(active) = self.active.lock() {
            if let Some(session) = active.as_ref() {
                if window_label.is_none_or(|label| label == session.window_label) {
                    session.cancel();
                }
            }
        }
        let input = self.input.lock().ok().and_then(|mut input| {
            if input
                .as_ref()
                .is_some_and(|active| window_label.is_none_or(|label| label == active.window_label))
            {
                input.take()
            } else {
                None
            }
        });
        if let Some(ActiveInput { id, handle, .. }) = input {
            tauri::async_runtime::spawn_blocking(move || {
                let _ = core_call(&handle, "asr.input.cancel", json!({"recordingId": id}));
            });
        }
    }
    pub(crate) fn shutdown(&self) {
        self.closing.store(true, Ordering::SeqCst);
        self.cancel_active();
        let active = self.active.lock().ok().and_then(|mut active| active.take());
        if let Some(session) = active {
            session.cancel();
            if let Ok(mut worker) = session.worker.lock() {
                if let Some(worker) = worker.take() {
                    join_until(worker, Duration::from_secs(2));
                }
            };
        }
    }
}
impl Drop for AsrState {
    fn drop(&mut self) {
        self.shutdown();
    }
}

// Device initialization may be inside an OS permission dialog. It cannot be
// forcefully interrupted safely, and must not make application exit unbounded.
// The cancelled worker checks again before play/write when the OS returns.
fn join_until(worker: JoinHandle<()>, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while !worker.is_finished() {
        if Instant::now() >= deadline {
            return false;
        }
        thread::sleep(Duration::from_millis(5));
    }
    let _ = worker.join();
    true
}

fn capture_failure(result: &Result<Value, String>, recording_only: bool) -> Option<String> {
    let code = match result {
        Err(error) => error.split('|').next().unwrap_or("ASR_CORE_UNAVAILABLE"),
        Ok(value) => match value.get("state").and_then(Value::as_str) {
            Some("failed") => value
                .get("errorCode")
                .and_then(Value::as_str)
                .unwrap_or("ASR_PROVIDER_UNAVAILABLE"),
            Some("cancelled") => "ASR_CANCELLED",
            Some("ready" | "recording") => return None,
            _ if recording_only => "ASR_CAPTURE_STATE_INVALID",
            _ => return None,
        },
    };
    Some(
        if !code.is_empty()
            && code.len() <= 80
            && code
                .bytes()
                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
        {
            code.to_owned()
        } else {
            "ASR_CORE_UNAVAILABLE".into()
        },
    )
}

struct CaptureStatusWatch {
    stopped: Arc<AtomicBool>,
    wake: mpsc::Sender<()>,
    worker: Option<JoinHandle<()>>,
}

impl CaptureStatusWatch {
    fn start(
        session: Arc<CaptureSession>,
        query: impl Fn() -> Result<Value, String> + Send + 'static,
    ) -> Result<Self, String> {
        let stopped = Arc::new(AtomicBool::new(false));
        let worker_stopped = stopped.clone();
        let (wake, wait) = mpsc::channel();
        let worker = thread::Builder::new()
            .name("sakura-microphone-status".into())
            .spawn(move || {
                loop {
                    if worker_stopped.load(Ordering::SeqCst)
                        || session.cancelled.load(Ordering::SeqCst)
                    {
                        return;
                    }
                    let result = query();
                    // A late status must not cancel or consume a submitted result.
                    if worker_stopped.load(Ordering::SeqCst) {
                        return;
                    }
                    if let Some(code) = capture_failure(&result, true) {
                        session.fail(code);
                        return;
                    }
                    if !matches!(
                        wait.recv_timeout(Duration::from_millis(250)),
                        Err(mpsc::RecvTimeoutError::Timeout)
                    ) {
                        return;
                    }
                }
            })
            .map_err(|_| "ASR_CAPTURE_FAILED")?;
        Ok(Self {
            stopped,
            wake,
            worker: Some(worker),
        })
    }
}

impl Drop for CaptureStatusWatch {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::SeqCst);
        let _ = self.wake.send(());
        if let Some(worker) = self.worker.take() {
            join_until(worker, Duration::from_secs(1));
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct RecordingRequest {
    recording_id: String,
}

fn validate_id(id: &str) -> Result<(), String> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("ASR_REQUEST_INVALID".into());
    }
    Ok(())
}
fn input_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" {
        Ok(())
    } else {
        product_shell::validate_settings_window(window)
    }
}
fn validate_prepare_origin(window_label: &str, payload: &Value) -> Result<(), String> {
    let purpose = match payload.get("purpose") {
        None => "draft",
        Some(value) => value.as_str().ok_or("ASR_REQUEST_INVALID")?,
    };
    match (window_label, purpose) {
        ("main", "draft") => {
            if payload.get("providerId").is_some() || payload.get("inputDeviceId").is_some() {
                return Err("ASR_REQUEST_INVALID".into());
            }
        }
        (product_shell::SETTINGS_WINDOW_LABEL, "test") => {}
        _ => return Err("ASR_PURPOSE_NOT_ALLOWED".into()),
    }
    for (key, limit) in [("providerId", 256), ("inputDeviceId", 4096)] {
        if let Some(value) = payload.get(key) {
            let value = value.as_str().ok_or("ASR_REQUEST_INVALID")?;
            if value.len() > limit || value.chars().any(char::is_control) {
                return Err("ASR_REQUEST_INVALID".into());
            }
        }
    }
    Ok(())
}
fn core_call(handle: &ShellLifecycleHandle, name: &str, payload: Value) -> Result<Value, String> {
    settings_response_payload(handle.settings_request(
        None,
        name,
        payload,
        Duration::from_secs(5),
    )?)
}
async fn proxy(
    lifecycle: &State<'_, ShellLifecycleState>,
    name: &'static str,
    payload: Value,
) -> Result<Value, String> {
    let handle = settings_core_handle(lifecycle)?;
    settings_response_payload(
        dispatch_settings_request(handle, None, name, payload, Duration::from_secs(5)).await?,
    )
}

#[tauri::command]
pub(crate) async fn asr_prepare(
    window: WebviewWindow,
    payload: Value,
    lifecycle: State<'_, ShellLifecycleState>,
    state: State<'_, AsrState>,
) -> Result<Value, String> {
    input_window(&window)?;
    validate_prepare_origin(window.label(), &payload)?;
    if state.closing.load(Ordering::SeqCst) {
        return Err("ASR_CANCELLED".into());
    }
    let id = payload
        .get("recordingId")
        .and_then(Value::as_str)
        .ok_or("ASR_REQUEST_INVALID")?
        .to_owned();
    validate_id(&id)?;
    {
        let mut input = state.input.lock().map_err(|_| "ASR_CAPTURE_FAILED")?;
        if input.is_some() {
            return Err("ASR_BUSY".into());
        }
        state.remember_owner(&id, window.label())?;
        *input = Some(ActiveInput {
            id: id.clone(),
            window_label: window.label().to_owned(),
            handle: settings_core_handle(&lifecycle)?,
        });
    }
    let result = proxy(&lifecycle, "asr.input.prepare", payload).await;
    let still_active = state
        .input
        .lock()
        .map_err(|_| "ASR_CAPTURE_FAILED")?
        .as_ref()
        .is_some_and(|active| active.id == id);
    if !still_active {
        let _ = proxy(&lifecycle, "asr.input.cancel", json!({"recordingId": id})).await;
        return Err("ASR_CANCELLED".into());
    }
    if result.is_err() {
        state.clear_input(&id);
    }
    result
}
#[tauri::command]
pub(crate) async fn asr_poll(
    window: WebviewWindow,
    payload: RecordingRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    state: State<'_, AsrState>,
) -> Result<Value, String> {
    input_window(&window)?;
    validate_id(&payload.recording_id)?;
    state.assert_owner(&payload.recording_id, window.label())?;
    let result = proxy(
        &lifecycle,
        "asr.input.poll",
        json!({"recordingId": payload.recording_id}),
    )
    .await;
    if let Ok(session) = state.current(&payload.recording_id) {
        if let Some(code) = capture_failure(&result, false) {
            session.fail(code);
        }
    }
    if result.as_ref().is_ok_and(|value| {
        matches!(
            value.get("state").and_then(Value::as_str),
            Some("succeeded" | "failed" | "cancelled" | "consumed")
        )
    }) {
        state.clear_input(&payload.recording_id);
    }
    result
}
#[tauri::command]
pub(crate) async fn asr_capture_start(
    window: WebviewWindow,
    payload: RecordingRequest,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    state: State<'_, AsrState>,
) -> Result<Value, String> {
    input_window(&window)?;
    validate_id(&payload.recording_id)?;
    state.assert_owner(&payload.recording_id, window.label())?;
    let handle = settings_core_handle(&lifecycle)?;
    let generation = handle
        .available_generation_id()
        .map_err(str::to_owned)?
        .ok_or("STALE_GENERATION")?;
    let session = state.reserve(&payload.recording_id, window.label())?;
    session.observe(
        app_handle.state::<RuntimeLogService>().inner().clone(),
        generation.clone(),
    );
    let target = proxy(
        &lifecycle,
        "asr.input.capture_target",
        json!({"recordingId": session.id}),
    )
    .await;
    let (path, input_device_id) = match target.and_then(|value| {
        let path = value
            .get("path")
            .and_then(Value::as_str)
            .map(std::path::PathBuf::from)
            .ok_or_else(|| "ASR_CAPTURE_TARGET_INVALID".to_owned())?;
        let input_device_id = match value.get("inputDeviceId") {
            None | Some(Value::Null) => String::new(),
            Some(Value::String(id)) if id.len() <= 4096 && !id.chars().any(char::is_control) => {
                id.clone()
            }
            _ => return Err("ASR_CAPTURE_TARGET_INVALID".into()),
        };
        Ok((path, input_device_id))
    }) {
        Ok(path) => path,
        Err(error) => {
            let _ = proxy(
                &lifecycle,
                "asr.input.capture_discarded",
                json!({"recordingId": session.id}),
            )
            .await;
            session.finish(Err(error.clone()));
            return Err(error);
        }
    };
    if session.cancelled.load(Ordering::SeqCst) {
        let _ = proxy(
            &lifecycle,
            "asr.input.capture_discarded",
            json!({"recordingId": session.id}),
        )
        .await;
        session.finish(Err("ASR_CANCELLED".into()));
        return Err("ASR_CANCELLED".into());
    }
    // Synchronously stop and join playback before opening the input device.
    let playback_pause = app_handle.state::<AudioState>().pause_for_input();
    let (ready, opened) = tokio::sync::oneshot::channel();
    let worker_session = session.clone();
    let worker_app = app_handle.clone();
    let worker = thread::Builder::new()
        .name("sakura-microphone".into())
        .spawn(move || {
            let result = capture(
                &worker_app,
                &handle,
                &generation,
                &worker_session,
                &path,
                &input_device_id,
                ready,
                playback_pause,
            );
            if !worker_session.submitted.load(Ordering::SeqCst) {
                let _ = fs::remove_file(&path);
            }
            if result.is_err() {
                let code =
                    capture_failure(&result, false).unwrap_or_else(|| "ASR_CAPTURE_FAILED".into());
                let mut discarded = json!({"recordingId": worker_session.id});
                if code != "ASR_CANCELLED" {
                    discarded["errorCode"] = json!(code);
                }
                // Publish a pollable failure before notifying the UI. A racing poll
                // must not consume a silent cancellation and hide the device error.
                // Core also ends the producer reservation, retaining any reader lease
                // when a submit response was uncertain. Submitted files stay Core-owned.
                let _ = core_call(&handle, "asr.input.capture_discarded", discarded);
                let _ = worker_app.emit_to(
                    &worker_session.window_label,
                    "sakura://asr-capture",
                    json!({
                        "recordingId": worker_session.id, "state": "failed", "errorCode": code
                    }),
                );
            } else if !worker_session.submitted.load(Ordering::SeqCst) {
                let _ = core_call(
                    &handle,
                    "asr.input.capture_discarded",
                    json!({"recordingId": worker_session.id}),
                );
            }
            worker_session.finish(result);
        });
    match worker {
        Ok(worker) => {
            *session.worker.lock().map_err(|_| "ASR_CAPTURE_FAILED")? = Some(worker);
        }
        Err(_) => {
            session.finish(Err("ASR_CAPTURE_FAILED".into()));
            let _ = proxy(
                &lifecycle,
                "asr.input.cancel",
                json!({"recordingId": session.id}),
            )
            .await;
            let _ = proxy(
                &lifecycle,
                "asr.input.capture_discarded",
                json!({"recordingId": session.id}),
            )
            .await;
            return Err("ASR_CAPTURE_FAILED".into());
        }
    }
    opened
        .await
        .map_err(|_| "ASR_CAPTURE_FAILED".to_owned())??;
    Ok(json!({"recordingId": session.id, "state": "recording"}))
}
#[tauri::command]
pub(crate) async fn asr_capture_stop(
    window: WebviewWindow,
    payload: RecordingRequest,
    state: State<'_, AsrState>,
) -> Result<Value, String> {
    input_window(&window)?;
    validate_id(&payload.recording_id)?;
    state.assert_owner(&payload.recording_id, window.label())?;
    let session = state.current(&payload.recording_id)?;
    session.stop.store(true, Ordering::SeqCst);
    session.wait().await
}
#[tauri::command]
pub(crate) async fn asr_cancel(
    window: WebviewWindow,
    payload: RecordingRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    state: State<'_, AsrState>,
) -> Result<Value, String> {
    input_window(&window)?;
    validate_id(&payload.recording_id)?;
    state.assert_owner(&payload.recording_id, window.label())?;
    state.clear_input(&payload.recording_id);
    if let Ok(session) = state.current(&payload.recording_id) {
        session.cancel();
    }
    proxy(
        &lifecycle,
        "asr.input.cancel",
        json!({"recordingId": payload.recording_id}),
    )
    .await
}
#[tauri::command]
pub(crate) async fn asr_availability(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    input_window(&window)?;
    proxy(&lifecycle, "asr.input.availability", json!({})).await
}

#[tauri::command]
pub(crate) async fn settings_asr_devices(window: WebviewWindow) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    tauri::async_runtime::spawn_blocking(input_device_snapshot)
        .await
        .map_err(|_| "ASR_INPUT_DEVICES_UNAVAILABLE".to_owned())?
}

#[tauri::command]
pub(crate) async fn settings_asr_get(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    proxy(&lifecycle, "asr.settings.get", json!({})).await
}
#[tauri::command]
pub(crate) async fn settings_asr_save(
    window: WebviewWindow,
    payload: Value,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    proxy(&lifecycle, "asr.settings.save", payload).await
}
#[tauri::command]
pub(crate) async fn settings_asr_action(
    window: WebviewWindow,
    payload: Value,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    proxy(&lifecycle, "asr.settings.action", payload).await
}

struct Samples {
    mono: Vec<f32>,
    limit: usize,
    window_size: usize,
    window_count: usize,
    energy: f64,
    level: f32,
    sequence: u64,
    last_callback: Instant,
}
impl Samples {
    fn new(rate: usize) -> Self {
        Self {
            mono: Vec::with_capacity(rate * MAX_SECONDS),
            limit: rate * MAX_SECONDS,
            window_size: (rate / 20).max(1),
            window_count: 0,
            energy: 0.0,
            level: 0.0,
            sequence: 0,
            last_callback: Instant::now(),
        }
    }
    fn push(&mut self, sample: f32) {
        if self.mono.len() >= self.limit {
            return;
        }
        let sample = if sample.is_finite() {
            sample.clamp(-1.0, 1.0)
        } else {
            0.0
        };
        self.mono.push(sample);
        self.energy += f64::from(sample).powi(2);
        self.window_count += 1;
        if self.window_count >= self.window_size {
            let rms = (self.energy / self.window_count as f64).sqrt() as f32;
            // A decibel scale preserves useful feedback for a quiet microphone.
            let normalized = ((20.0 * rms.max(0.000_01).log10() + 60.0) / 60.0).clamp(0.0, 1.0);
            // Follow syllable attacks and pauses without smearing several audio windows together.
            let response = if normalized > self.level { 0.85 } else { 0.65 };
            self.level += response * (normalized - self.level);
            self.sequence += 1;
            self.energy = 0.0;
            self.window_count = 0;
        }
    }
}

fn input_stream<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    samples: Arc<Mutex<Samples>>,
    failed: Arc<AtomicBool>,
) -> Result<cpal::Stream, String>
where
    T: cpal::SizedSample,
    f32: cpal::FromSample<T>,
{
    let channels = usize::from(config.channels);
    device
        .build_input_stream(
            config,
            move |data: &[T], _: &cpal::InputCallbackInfo| {
                if let Ok(mut buffer) = samples.lock() {
                    buffer.last_callback = Instant::now();
                    for frame in data.chunks_exact(channels) {
                        let mono = frame
                            .iter()
                            .map(|sample| <f32 as cpal::Sample>::from_sample(*sample))
                            .sum::<f32>()
                            / channels as f32;
                        buffer.push(mono);
                    }
                }
            },
            move |_| {
                failed.store(true, Ordering::SeqCst);
            },
            None,
        )
        .map_err(|_| "ASR_MICROPHONE_UNAVAILABLE".into())
}

struct OpenInput {
    stream: cpal::Stream,
    samples: Arc<Mutex<Samples>>,
    failed: Arc<AtomicBool>,
    rate: usize,
}

fn input_device_snapshot() -> Result<Value, String> {
    let host = cpal::default_host();
    let default_id = host
        .default_input_device()
        .and_then(|device| device.id().ok())
        .map(|id| id.to_string());
    let mut devices = Vec::new();
    for device in host
        .input_devices()
        .map_err(|_| "ASR_INPUT_DEVICES_UNAVAILABLE")?
    {
        let Ok(id) = device.id() else {
            continue;
        };
        let label = device
            .description()
            .map(|description| description.name().to_owned())
            .unwrap_or_else(|_| "麦克风".into());
        devices.push(json!({"id": id.to_string(), "label": label}));
    }
    Ok(json!({"devices": devices, "defaultDeviceId": default_id}))
}

fn select_input_device(input_device_id: &str) -> Result<cpal::Device, String> {
    let host = cpal::default_host();
    if input_device_id.is_empty() {
        return host
            .default_input_device()
            .ok_or_else(|| "ASR_MICROPHONE_UNAVAILABLE".into());
    }
    let id: cpal::DeviceId = input_device_id
        .parse()
        .map_err(|_| "ASR_INPUT_DEVICE_NOT_FOUND")?;
    host.input_devices()
        .map_err(|_| "ASR_INPUT_DEVICES_UNAVAILABLE")?
        .find(|device| device.id().ok().as_ref() == Some(&id))
        .ok_or_else(|| "ASR_INPUT_DEVICE_NOT_FOUND".into())
}

fn open_input(input_device_id: &str) -> Result<OpenInput, String> {
    let device = select_input_device(input_device_id)?;
    let supported = device
        .default_input_config()
        .map_err(|_| "ASR_MICROPHONE_UNAVAILABLE")?;
    let config: cpal::StreamConfig = supported.clone().into();
    let rate = config.sample_rate as usize;
    if !(8_000..=192_000).contains(&rate) || config.channels == 0 || config.channels > 32 {
        return Err("ASR_AUDIO_FORMAT_UNSUPPORTED".into());
    }
    let samples = Arc::new(Mutex::new(Samples::new(rate)));
    let failed = Arc::new(AtomicBool::new(false));
    macro_rules! build {
        ($kind:ty) => {
            input_stream::<$kind>(&device, &config, samples.clone(), failed.clone())
        };
    }
    let stream = match supported.sample_format() {
        cpal::SampleFormat::I8 => build!(i8),
        cpal::SampleFormat::I16 => build!(i16),
        cpal::SampleFormat::I32 => build!(i32),
        cpal::SampleFormat::I64 => build!(i64),
        cpal::SampleFormat::U8 => build!(u8),
        cpal::SampleFormat::U16 => build!(u16),
        cpal::SampleFormat::U32 => build!(u32),
        cpal::SampleFormat::U64 => build!(u64),
        cpal::SampleFormat::F32 => build!(f32),
        cpal::SampleFormat::F64 => build!(f64),
        _ => Err("ASR_AUDIO_FORMAT_UNSUPPORTED".into()),
    }?;
    Ok(OpenInput {
        stream,
        samples,
        failed,
        rate,
    })
}

fn capture(
    app: &tauri::AppHandle,
    handle: &ShellLifecycleHandle,
    generation: &str,
    session: &Arc<CaptureSession>,
    path: &Path,
    input_device_id: &str,
    ready: tokio::sync::oneshot::Sender<Result<(), String>>,
    mut playback_pause: InputPlaybackPause,
) -> Result<Value, String> {
    let opened = (|| {
        if session.cancelled.load(Ordering::SeqCst) {
            return Err("ASR_CANCELLED".to_owned());
        }
        let OpenInput {
            stream,
            samples,
            failed,
            rate,
        } = open_input(input_device_id)?;
        if session.cancelled.load(Ordering::SeqCst) {
            return Err("ASR_CANCELLED".into());
        }
        core_call(
            handle,
            "asr.input.capture_ready",
            json!({"recordingId": session.id}),
        )?;
        if session.cancelled.load(Ordering::SeqCst) {
            return Err(session.cancellation_error());
        }
        stream.play().map_err(|_| "ASR_MICROPHONE_UNAVAILABLE")?;
        Ok((stream, samples, failed, rate))
    })();
    let (stream, samples, failed, rate) = match opened {
        Ok(result) => {
            session.started_capture();
            let _ = ready.send(Ok(()));
            result
        }
        Err(error) => {
            let _ = ready.send(Err(error.clone()));
            return Err(error);
        }
    };
    let status_handle = handle.clone();
    let status_id = session.id.clone();
    let status_watch = CaptureStatusWatch::start(session.clone(), move || {
        settings_response_payload(status_handle.settings_request(
            None,
            "asr.input.capture_status",
            json!({"recordingId": status_id}),
            Duration::from_millis(500),
        )?)
    })?;
    let start = Instant::now();
    let mut last_tick = SystemTime::now();
    let mut last_sequence = 0;
    let outcome = loop {
        thread::sleep(FRAME_INTERVAL);
        let now = SystemTime::now();
        // A resumed process must never submit the incomplete pre-sleep recording.
        if now.duration_since(last_tick).unwrap_or_default() > Duration::from_secs(2) {
            break Err("ASR_CAPTURE_INTERRUPTED".to_owned());
        }
        last_tick = now;
        if session.cancelled.load(Ordering::SeqCst) {
            break Err(session.cancellation_error());
        }
        if handle.available_generation_id().ok().flatten().as_deref() != Some(generation) {
            break Err("STALE_GENERATION".into());
        }
        let visible = app
            .get_webview_window(&session.window_label)
            .and_then(|window| window.is_visible().ok())
            .unwrap_or(false);
        if !visible {
            break Err("ASR_CANCELLED".into());
        }
        let input_visible = session.window_label != "main"
            || app
                .state::<Mutex<crate::WindowGeometrySession>>()
                .lock()
                .ok()
                .and_then(|geometry| {
                    geometry
                        .control_surface
                        .as_ref()
                        .map(|surface| surface.input_visible)
                })
                .unwrap_or(true);
        if !input_visible {
            break Err("ASR_CANCELLED".into());
        }
        if failed.load(Ordering::SeqCst) {
            break Err("ASR_MICROPHONE_DISCONNECTED".into());
        }
        let (sequence, level, full, stalled) = {
            let buffer = samples.lock().map_err(|_| "ASR_CAPTURE_FAILED")?;
            (
                buffer.sequence,
                buffer.level,
                buffer.mono.len() >= buffer.limit,
                buffer.last_callback.elapsed() > Duration::from_secs(2),
            )
        };
        if stalled {
            break Err("ASR_MICROPHONE_DISCONNECTED".into());
        }
        if sequence != last_sequence {
            last_sequence = sequence;
            let _ = app.emit_to(
                &session.window_label,
                "sakura://asr-level",
                json!({"recordingId":session.id,"sequence":sequence,"level":level}),
            );
        }
        if session.stop.load(Ordering::SeqCst)
            || full
            || start.elapsed() >= Duration::from_secs(MAX_SECONDS as u64)
        {
            break Ok(());
        }
    };
    drop(stream);
    session.ended_capture();
    // Resume eligibility immediately when capture ends. Skipped playback is never queued.
    playback_pause.release();
    drop(status_watch);
    outcome?;
    let mono = std::mem::take(&mut samples.lock().map_err(|_| "ASR_CAPTURE_FAILED")?.mono);
    if mono.is_empty() {
        return Err("ASR_NO_SPEECH".into());
    }
    if session.cancelled.load(Ordering::SeqCst) {
        return Err("ASR_CANCELLED".into());
    }
    let write_result = write_wav(path, &mono, rate);
    if write_result.is_err() || session.cancelled.load(Ordering::SeqCst) {
        let _ = fs::remove_file(path);
        write_result?;
        return Err("ASR_CANCELLED".into());
    }
    // Core owns the file after this point, including uncertain/timeout responses.
    session.claim_submission()?;
    let result = core_call(
        handle,
        "asr.input.submit",
        json!({"recordingId":session.id}),
    );
    if result.is_ok() {
        let _ = app.emit_to(
            &session.window_label,
            "sakura://asr-capture",
            json!({"recordingId":session.id,"state":"recognizing"}),
        );
    }
    result
}

fn write_wav(path: &Path, samples: &[f32], rate: usize) -> Result<(), String> {
    let pcm = resample(samples, rate);
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| "ASR_AUDIO_WRITE_FAILED")?;
    let mut writer = BufWriter::new(file);
    let bytes = (pcm.len() * 2) as u32;
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&(36 + bytes).to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16_u32.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&(OUTPUT_RATE as u32).to_le_bytes());
    header.extend_from_slice(&32_000_u32.to_le_bytes());
    header.extend_from_slice(&2_u16.to_le_bytes());
    header.extend_from_slice(&16_u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&bytes.to_le_bytes());
    writer
        .write_all(&header)
        .map_err(|_| "ASR_AUDIO_WRITE_FAILED")?;
    for sample in pcm {
        writer
            .write_all(&sample.to_le_bytes())
            .map_err(|_| "ASR_AUDIO_WRITE_FAILED")?;
    }
    writer
        .flush()
        .map_err(|_| "ASR_AUDIO_WRITE_FAILED".to_owned())
}

// Windowed sinc low-pass before decimation avoids aliasing the microphone's
// high-frequency content into the recognizer's 16 kHz input band.
fn resample(samples: &[f32], rate: usize) -> Vec<i16> {
    let count = (samples.len() * OUTPUT_RATE / rate).min(OUTPUT_RATE * MAX_SECONDS);
    let quantize = |value: f64| (value.clamp(-1.0, 1.0) * i16::MAX as f64).round() as i16;
    if rate == OUTPUT_RATE {
        return samples
            .iter()
            .take(count)
            .map(|s| quantize(f64::from(*s)))
            .collect();
    }
    let cutoff = (OUTPUT_RATE as f64 / rate as f64).min(1.0) * 0.94;
    let radius = (24.0 / cutoff).ceil() as isize;
    (0..count)
        .map(|output| {
            let position = output as f64 * rate as f64 / OUTPUT_RATE as f64;
            let center = position.floor() as isize;
            let mut value = 0.0;
            let mut weight_sum = 0.0;
            for input in (center - radius)..=(center + radius) {
                if input < 0 || input >= samples.len() as isize {
                    continue;
                }
                let distance = input as f64 - position;
                let argument = std::f64::consts::PI * distance * cutoff;
                let sinc = if argument.abs() < 1e-8 {
                    1.0
                } else {
                    argument.sin() / argument
                };
                let window = 0.5 + 0.5 * (std::f64::consts::PI * distance / radius as f64).cos();
                let weight = cutoff * sinc * window;
                value += f64::from(samples[input as usize]) * weight;
                weight_sum += weight;
            }
            quantize(if weight_sum.abs() > 1e-8 {
                value / weight_sum
            } else {
                0.0
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_logs_keep_bounded_stages_and_safe_correlated_diagnostics() {
        use crate::runtime_log::{RuntimeLogConfig, Verbosity};
        let root =
            std::env::temp_dir().join(format!("sakura-asr-capture-log-{}", uuid::Uuid::new_v4()));
        let path = root.join("runtime.log");
        let mut config = RuntimeLogConfig::production(path.clone());
        config.level = Verbosity::Info;
        let log = RuntimeLogService::start_with_config(config);
        for (id, error) in [
            ("capture-stop", None),
            ("capture-cancel", Some("ASR_CANCELLED|Cancelled by user")),
            (
                "capture-device-fault",
                Some("ASR_MICROPHONE_DISCONNECTED|C:\\private\\device-id"),
            ),
        ] {
            let session = CaptureSession::new(id.into());
            session.observe(log.clone(), "test-generation".into());
            session.started_capture();
            session.started_capture();
            session.ended_capture();
            session.stop.store(true, Ordering::SeqCst);
            session.finish(error.map_or_else(|| Ok(json!({})), |error| Err(error.into())));
            session.finish(Err("ASR_CANCELLED".into()));
        }
        let records = log.viewer_snapshot(None).unwrap().records;
        assert_eq!(records.len(), 6);
        for pair in records.chunks_exact(2) {
            assert_eq!(pair[0].event_code, "asr.capture.started");
            assert_eq!(pair[0].correlation_id, pair[1].correlation_id);
            assert!(pair.iter().all(|record| record.source == "rust"
                && record.plugin_id.is_none()
                && record.scopes == ["software"]));
            assert!(pair[1]
                .details
                .iter()
                .any(|detail| detail.label == "录音编号"));
            assert!(pair[1].details.iter().any(|detail| detail.label == "耗时"));
        }
        assert_eq!(records[1].event_code, "asr.capture.finished");
        assert_eq!(records[3].event_code, "asr.capture.cancelled");
        assert_eq!(records[5].event_code, "asr.capture.failed");
        assert_eq!(records[5].severity, "warning");
        log.drain_and_shutdown_for_test();
        let text = fs::read_to_string(path).unwrap();
        assert_eq!(text.lines().count(), 6);
        assert!(text.contains("recording_id=capture-device-fault"));
        assert!(text.contains("duration_ms="));
        assert!(text.contains("reason_code=ASR_MICROPHONE_DISCONNECTED"));
        assert!(!text.contains("private"));
        assert!(!text.contains("device-id"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[ignore = "Opens the real system microphone; run explicitly on a device-enabled host"]
    fn real_microphone_captures_pcm_and_releases_device() {
        let inventory = input_device_snapshot().unwrap();
        let selected = inventory["defaultDeviceId"]
            .as_str()
            .expect("No default microphone ID");
        let refreshed = input_device_snapshot().unwrap();
        assert!(refreshed["devices"]
            .as_array()
            .unwrap()
            .iter()
            .any(|device| device["id"].as_str() == Some(selected)));
        let missing = format!(
            "{}:sakura-test-missing-{}",
            cpal::default_host().id(),
            uuid::Uuid::new_v4()
        );
        assert!(
            matches!(select_input_device(&missing), Err(error) if error == "ASR_INPUT_DEVICE_NOT_FOUND")
        );
        let OpenInput {
            stream,
            samples,
            failed,
            rate,
        } = open_input(selected).unwrap();
        stream.play().unwrap();
        thread::sleep(Duration::from_millis(600));
        drop(stream);
        assert!(!failed.load(Ordering::SeqCst));
        let buffer = samples.lock().unwrap();
        assert!(
            buffer.mono.len() > rate / 10,
            "The OS returned no microphone frames"
        );
        assert!(buffer.sequence > 0);
        let path =
            std::env::temp_dir().join(format!("sakura-asr-device-{}.wav", uuid::Uuid::new_v4()));
        write_wav(&path, &buffer.mono, rate).unwrap();
        let bytes = fs::metadata(&path).unwrap().len();
        fs::remove_file(&path).unwrap();
        eprintln!("Selected microphone capture verified: devices={}, native_rate={rate}, frames={}, rms_summaries={}, wav_bytes={bytes}", inventory["devices"].as_array().unwrap().len(), buffer.mono.len(), buffer.sequence);
        // A subsequent open proves the first stream released the input handle.
        drop(open_input(selected).unwrap());
    }
    #[test]
    fn microphone_level_follows_syllables_and_pauses() {
        let mut samples = Samples::new(16_000);
        for _ in 0..800 {
            samples.push(0.1);
        }
        let attack = samples.level;
        assert!(
            attack > 0.5,
            "one syllable should register without a long fade-in"
        );
        for _ in 0..800 {
            samples.push(0.0);
        }
        assert!(
            samples.level < attack * 0.4,
            "a pause should separate syllables"
        );
        for _ in 0..800 {
            samples.push(0.03);
        }
        assert!(samples.level > 0.35 && samples.level < attack);
    }

    #[test]
    fn microphone_rms_is_real_bounded_and_monotonic() {
        let mut samples = Samples::new(16_000);
        for _ in 0..800 {
            samples.push(0.0);
        }
        assert_eq!(samples.level, 0.0);
        assert_eq!(samples.sequence, 1);
        for _ in 0..800 {
            samples.push(0.1);
        }
        assert!(samples.level > 0.0);
        assert_eq!(samples.sequence, 2);
        for _ in 0..samples.limit + 100 {
            samples.push(0.2);
        }
        assert_eq!(samples.mono.len(), OUTPUT_RATE * MAX_SECONDS);
    }
    #[test]
    fn conversion_writes_actual_pcm16_mono_and_filters_aliasing() {
        let rate = 48_000;
        let tone = |frequency: f64| {
            (0..rate / 10)
                .map(|i| {
                    (2.0 * std::f64::consts::PI * frequency * i as f64 / rate as f64).sin() as f32
                        * 0.5
                })
                .collect::<Vec<_>>()
        };
        let low = resample(&tone(1_000.0), rate);
        let high = resample(&tone(12_000.0), rate);
        assert_eq!(low.len(), 1600);
        let rms = |values: &[i16]| {
            (values[100..values.len() - 100]
                .iter()
                .map(|value| f64::from(*value).powi(2))
                .sum::<f64>()
                / (values.len() - 200) as f64)
                .sqrt()
        };
        assert!(rms(&low) > 10_000.0);
        assert!(rms(&high) < 100.0);
        let path =
            std::env::temp_dir().join(format!("sakura-asr-test-{}.wav", uuid::Uuid::new_v4()));
        write_wav(&path, &tone(1_000.0), rate).unwrap();
        let bytes = fs::read(&path).unwrap();
        fs::remove_file(&path).unwrap();
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(&bytes[8..12], b"WAVE");
        assert_eq!(
            u32::from_le_bytes(bytes[24..28].try_into().unwrap()),
            16_000
        );
        assert_eq!(u16::from_le_bytes(bytes[22..24].try_into().unwrap()), 1);
        assert_eq!(u16::from_le_bytes(bytes[34..36].try_into().unwrap()), 16);
        assert_eq!(bytes.len(), 44 + low.len() * 2);
    }
    #[test]
    fn cancellation_is_isolated_and_completed_session_can_be_replaced() {
        let state = AsrState::default();
        let first = state.reserve("first", "main").unwrap();
        assert!(state.reserve("second", "main").is_err());
        first.cancel();
        first.finish(Err("ASR_CANCELLED".into()));
        let second = state.reserve("second", "main").unwrap();
        assert!(state.current("first").is_err());
        assert!(!second.cancelled.load(Ordering::SeqCst));
        state.cancel_active();
        assert!(second.cancelled.load(Ordering::SeqCst));
    }

    #[test]
    fn concurrent_stop_sources_claim_only_one_submission() {
        let session = Arc::new(CaptureSession::new("one-recording".into()));
        let sources: Vec<_> = (0..4)
            .map(|_| {
                let session = session.clone();
                thread::spawn(move || {
                    session.stop.store(true, Ordering::SeqCst);
                    session.claim_submission().is_ok()
                })
            })
            .collect();
        assert_eq!(
            sources
                .into_iter()
                .map(|source| usize::from(source.join().unwrap()))
                .sum::<usize>(),
            1
        );
    }

    #[test]
    fn core_failure_stops_capture_without_frontend_polling() {
        let session = Arc::new(CaptureSession::new("provider-exited".into()));
        let watch = CaptureStatusWatch::start(session.clone(), move || {
            Ok(json!({"state":"failed", "errorCode":"ASR_PROVIDER_UNAVAILABLE"}))
        })
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while !session.cancelled.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        drop(watch);
        assert!(session.cancelled.load(Ordering::SeqCst));
        assert_eq!(session.cancellation_error(), "ASR_PROVIDER_UNAVAILABLE");
        assert_eq!(
            capture_failure(&Err("private transport details".into()), true),
            Some("ASR_CORE_UNAVAILABLE".into())
        );
    }

    #[test]
    fn shutdown_does_not_wait_forever_for_an_os_permission_dialog() {
        let (release, waiting) = mpsc::channel();
        let (finished, done) = mpsc::channel();
        let worker = thread::spawn(move || {
            let _ = waiting.recv();
            let _ = finished.send(());
        });
        let start = Instant::now();
        assert!(!join_until(worker, Duration::from_millis(20)));
        assert!(start.elapsed() < Duration::from_secs(1));
        release.send(()).unwrap();
        done.recv_timeout(Duration::from_secs(1)).unwrap();
        let state = AsrState::default();
        state.shutdown();
        assert!(
            matches!(state.reserve("late-start", "main"), Err(error) if error == "ASR_CANCELLED")
        );
    }

    #[test]
    fn late_capture_status_cannot_cancel_a_stopped_recording() {
        let session = Arc::new(CaptureSession::new("stopped".into()));
        let (entered, called) = mpsc::channel();
        let (release, blocked) = mpsc::channel();
        let blocked = Mutex::new(blocked);
        let watch = CaptureStatusWatch::start(session.clone(), move || {
            entered.send(()).unwrap();
            blocked.lock().unwrap().recv().unwrap();
            Ok(json!({"state":"failed", "errorCode":"ASR_PROVIDER_UNAVAILABLE"}))
        })
        .unwrap();
        called.recv_timeout(Duration::from_secs(1)).unwrap();
        let stopped = watch.stopped.clone();
        let stopping = thread::spawn(move || drop(watch));
        let deadline = Instant::now() + Duration::from_secs(1);
        while !stopped.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(stopped.load(Ordering::SeqCst));
        release.send(()).unwrap();
        stopping.join().unwrap();
        assert!(!session.cancelled.load(Ordering::SeqCst));
        assert!(session.claim_submission().is_ok());
    }

    #[test]
    fn settings_test_is_separate_from_the_chat_recording_owner() {
        let state = AsrState::default();
        state.remember_owner("chat", "main").unwrap();
        let chat = state.reserve("chat", "main").unwrap();
        assert!(state.assert_owner("chat", "main").is_ok());
        assert_eq!(
            state.assert_owner("chat", "settings").unwrap_err(),
            "ASR_INPUT_NOT_OWNED"
        );
        state.cancel_window("settings");
        assert!(!chat.cancelled.load(Ordering::SeqCst));
        state.cancel_window("main");
        assert!(chat.cancelled.load(Ordering::SeqCst));
        chat.finish(Err("ASR_CANCELLED".into()));
        state.remember_owner("test", "settings").unwrap();
        let test = state.reserve("test", "settings").unwrap();
        assert_eq!(test.window_label, "settings");
        assert!(state.assert_owner("test", "main").is_err());
        state.cancel_window("main");
        assert!(!test.cancelled.load(Ordering::SeqCst));
        state.cancel_window("settings");
        assert!(test.cancelled.load(Ordering::SeqCst));
    }

    #[test]
    fn only_settings_test_may_override_provider_and_microphone() {
        let test = json!({"purpose":"test", "providerId":"third.party.asr", "inputDeviceId":"selected:microphone"});
        assert!(validate_prepare_origin("settings", &test).is_ok());
        assert!(validate_prepare_origin("main", &test).is_err());
        assert!(validate_prepare_origin("settings", &json!({})).is_err());
        assert!(
            validate_prepare_origin("main", &json!({"inputDeviceId":"selected:microphone"}))
                .is_err()
        );
        assert!(validate_prepare_origin("main", &json!({"purpose":"draft"})).is_ok());
        assert!(validate_prepare_origin(
            "settings",
            &json!({"purpose":"test","inputDeviceId":"bad\nidentifier"})
        )
        .is_err());
        assert!(
            validate_prepare_origin("settings", &json!({"purpose":"test","providerId":42}))
                .is_err()
        );
        assert!(
            matches!(select_input_device("not-a-device-id"), Err(error) if error == "ASR_INPUT_DEVICE_NOT_FOUND")
        );
    }
}
