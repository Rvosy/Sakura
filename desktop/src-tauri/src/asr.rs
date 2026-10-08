//! Window/lifecycle bridge to the built-in ASR plugin. PCM stays in its native crate.
use std::{
    collections::VecDeque,
    fs,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
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
use sakura_asr_native::input_device_snapshot;
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, WebviewWindow};

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
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("ASR_ALREADY_SUBMITTED", source_error)
            })
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
                .map_err(|source_error| {
                    crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
                })?
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
        let mut active = self.active.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
        })?;
        if self.closing.load(Ordering::SeqCst) {
            return Err("ASR_CANCELLED".into());
        }
        if let Some(previous) = active.as_ref() {
            if previous
                .result
                .lock()
                .map_err(|source_error| {
                    crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
                })?
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
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
            })?
            .as_ref()
            .filter(|session| session.id == id)
            .cloned()
            .ok_or_else(|| "ASR_RECORDING_STALE".into())
    }
    fn remember_owner(&self, id: &str, window_label: &str) -> Result<(), String> {
        let mut owners = self.owners.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
        })?;
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
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
            })?
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
        Err(error) => crate::runtime_log::diagnostic_code(error),
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
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
            })?;
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
    settings_response_payload(handle.settings_request(None, name, payload)?)
}
async fn proxy(
    lifecycle: &State<'_, ShellLifecycleState>,
    name: &'static str,
    payload: Value,
) -> Result<Value, String> {
    let handle = settings_core_handle(lifecycle)?;
    settings_response_payload(dispatch_settings_request(handle, None, name, payload).await?)
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
        let mut input = state.input.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
        })?;
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
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
        })?
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
        .map_err(|error| error.to_string())?
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
                    discarded["diagnostic"] = json!(result.as_ref().err());
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
                        "recordingId": worker_session.id, "state": "failed", "errorCode": code, "diagnostic": result.as_ref().err()
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
            *session.worker.lock().map_err(|source_error| {
                crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
            })? = Some(worker);
        }
        Err(error) => {
            let error = crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", error);
            session.finish(Err(error.clone()));
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
            return Err(error);
        }
    }
    opened.await.map_err(|source_error| {
        crate::runtime_log::diagnostic_error("ASR_CAPTURE_FAILED", source_error)
    })??;
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
pub(crate) async fn settings_asr_devices(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    if proxy(&lifecycle, "asr.input.availability", json!({})).await?["enabled"] != true {
        return Err("ASR_SERVICE_UNAVAILABLE".into());
    }
    tauri::async_runtime::spawn_blocking(input_device_snapshot)
        .await
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error("ASR_INPUT_DEVICES_UNAVAILABLE", source_error)
        })?
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
    struct ShellCapture<'a> {
        app: &'a tauri::AppHandle,
        handle: &'a ShellLifecycleHandle,
        generation: &'a str,
        session: &'a Arc<CaptureSession>,
        ready: Option<tokio::sync::oneshot::Sender<Result<(), String>>>,
        playback_pause: &'a mut InputPlaybackPause,
        watch: Option<CaptureStatusWatch>,
    }
    impl sakura_asr_native::CaptureHost for ShellCapture<'_> {
        fn check_active(&self) -> Result<(), String> {
            if self.session.cancelled.load(Ordering::SeqCst) {
                return Err(self.session.cancellation_error());
            }
            if self
                .handle
                .available_generation_id()
                .ok()
                .flatten()
                .as_deref()
                != Some(self.generation)
            {
                return Err("STALE_GENERATION".into());
            }
            if !self
                .app
                .get_webview_window(&self.session.window_label)
                .and_then(|window| window.is_visible().ok())
                .unwrap_or(false)
            {
                return Err("ASR_CANCELLED".into());
            }
            let input_visible = self.session.window_label != "main"
                || self
                    .app
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
                return Err("ASR_CANCELLED".into());
            }
            Ok(())
        }
        fn ready(&mut self) -> Result<(), String> {
            core_call(
                self.handle,
                "asr.input.capture_ready",
                json!({"recordingId":self.session.id}),
            )?;
            self.check_active()?;
            let handle = self.handle.clone();
            let id = self.session.id.clone();
            self.watch = Some(CaptureStatusWatch::start(
                self.session.clone(),
                move || {
                    settings_response_payload(handle.settings_request(
                        None,
                        "asr.input.capture_status",
                        json!({"recordingId":id}),
                    )?)
                },
            )?);
            self.session.started_capture();
            if let Some(ready) = self.ready.take() {
                let _ = ready.send(Ok(()));
            }
            Ok(())
        }
        fn opening_failed(&mut self, error: &str) {
            if let Some(ready) = self.ready.take() {
                let _ = ready.send(Err(error.to_owned()));
            }
        }
        fn stopping(&self) -> bool {
            self.session.stop.load(Ordering::SeqCst)
        }
        fn level(&self, sequence: u64, level: f32) {
            let _ = self.app.emit_to(
                &self.session.window_label,
                "sakura://asr-level",
                json!({"recordingId":self.session.id,"sequence":sequence,"level":level}),
            );
        }
        fn ended(&mut self) {
            self.session.ended_capture();
            self.playback_pause.release();
            self.watch.take();
        }
    }
    sakura_asr_native::capture(
        path,
        input_device_id,
        &mut ShellCapture {
            app,
            handle,
            generation,
            session,
            ready: Some(ready),
            playback_pause: &mut playback_pause,
            watch: None,
        },
    )?;
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
    }
}
