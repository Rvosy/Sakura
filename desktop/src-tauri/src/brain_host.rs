use std::collections::{BTreeMap, BTreeSet};
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::ipc::{read_frame, validate_envelope, write_frame, PROTOCOL_VERSION};

const DEFAULT_MAX_RESTARTS: u32 = 3;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BrainHostPhase {
    Starting,
    Ready,
    Restarting,
    Stopping,
    Diagnostic,
    Stopped,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BrainHostDiagnostic {
    pub code: String,
    pub message: String,
    pub attempts: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrainHostStatus {
    pub phase: BrainHostPhase,
    pub session_id: Option<String>,
    pub session_generation: u64,
    pub restart_count: u32,
    pub accepting_requests: bool,
    pub pending_request_count: usize,
    pub temporary_resource_count: usize,
    pub diagnostic: Option<BrainHostDiagnostic>,
    pub last_shutdown_forced: bool,
}

impl BrainHostStatus {
    fn starting() -> Self {
        Self {
            phase: BrainHostPhase::Starting,
            session_id: None,
            session_generation: 0,
            restart_count: 0,
            accepting_requests: false,
            pending_request_count: 0,
            temporary_resource_count: 0,
            diagnostic: None,
            last_shutdown_forced: false,
        }
    }
}

#[derive(Debug, Clone)]
enum BrainHostEntrypoint {
    Module(String),
    Script(PathBuf),
}

#[derive(Debug, Clone)]
pub struct BrainHostLaunchConfig {
    pub python_exe: PathBuf,
    pub base_dir: PathBuf,
    entrypoint: BrainHostEntrypoint,
    pub environment: BTreeMap<OsString, OsString>,
    pub startup_timeout: Duration,
    pub shutdown_timeout: Duration,
    pub poll_interval: Duration,
    pub restart_backoff: Vec<Duration>,
    pub max_restarts: u32,
}

impl BrainHostLaunchConfig {
    pub fn module(python_exe: PathBuf, base_dir: PathBuf, module: impl Into<String>) -> Self {
        Self {
            python_exe,
            base_dir,
            entrypoint: BrainHostEntrypoint::Module(module.into()),
            environment: BTreeMap::new(),
            startup_timeout: Duration::from_secs(30),
            shutdown_timeout: Duration::from_secs(5),
            poll_interval: Duration::from_millis(50),
            restart_backoff: vec![
                Duration::from_millis(250),
                Duration::from_secs(1),
                Duration::from_secs(3),
            ],
            max_restarts: DEFAULT_MAX_RESTARTS,
        }
    }

    pub fn script(python_exe: PathBuf, base_dir: PathBuf, script: PathBuf) -> Self {
        let mut config = Self::module(python_exe, base_dir, "app.brain_host");
        config.entrypoint = BrainHostEntrypoint::Script(script);
        config
    }

    pub fn for_current_app() -> Self {
        let base_dir = resolve_base_dir();
        let explicit_python = std::env::var_os("SAKURA_PYTHON_EXE");
        let python_exe = resolve_python_executable(&base_dir, explicit_python.as_deref())
            .unwrap_or_else(|_| default_python_candidate(&base_dir));
        Self::module(python_exe, base_dir, "app.brain_host")
    }

    fn backoff(&self, restart_count: u32) -> Duration {
        if self.restart_backoff.is_empty() {
            return Duration::ZERO;
        }
        let index = restart_count.saturating_sub(1) as usize;
        self.restart_backoff[index.min(self.restart_backoff.len() - 1)]
    }
}

pub fn resolve_python_executable(base_dir: &Path, explicit: Option<&OsStr>) -> io::Result<PathBuf> {
    let candidate = explicit
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| default_python_candidate(base_dir));
    if !candidate.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("Python executable does not exist: {}", candidate.display()),
        ));
    }
    candidate.canonicalize()
}

fn resolve_base_dir() -> PathBuf {
    if let Some(explicit) = std::env::var_os("SAKURA_BASE_DIR").filter(|value| !value.is_empty()) {
        return PathBuf::from(explicit);
    }
    if cfg!(debug_assertions) {
        return Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    }
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn default_python_candidate(base_dir: &Path) -> PathBuf {
    #[cfg(windows)]
    let relative = Path::new("runtime/python.exe");
    #[cfg(not(windows))]
    let relative = Path::new("runtime/python");
    base_dir.join(relative)
}

pub type StatusCallback = Arc<dyn Fn(BrainHostStatus) + Send + Sync + 'static>;
pub type EventCallback = Arc<dyn Fn(BrainHostEvent) + Send + Sync + 'static>;

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrainHostEvent {
    pub method: String,
    pub payload: Value,
    pub session_id: String,
    pub sequence: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrainHostRequestError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub details: Value,
}

impl BrainHostRequestError {
    fn unavailable(message: impl Into<String>) -> Self {
        Self {
            code: "BACKEND_UNAVAILABLE".into(),
            message: message.into(),
            retryable: true,
            details: json!({}),
        }
    }

    fn transport(message: impl Into<String>) -> Self {
        Self {
            code: "BRAIN_TRANSPORT_FAILED".into(),
            message: message.into(),
            retryable: true,
            details: json!({}),
        }
    }
}

impl std::fmt::Display for BrainHostRequestError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for BrainHostRequestError {}

struct RuntimeState {
    status: BrainHostStatus,
    startup_state: Option<Value>,
    pending_request_ids: BTreeSet<String>,
    temporary_resources: BTreeSet<PathBuf>,
}

impl RuntimeState {
    fn new() -> Self {
        Self {
            status: BrainHostStatus::starting(),
            startup_state: None,
            pending_request_ids: BTreeSet::new(),
            temporary_resources: BTreeSet::new(),
        }
    }

    fn synchronize_counts(&mut self) {
        self.status.pending_request_count = self.pending_request_ids.len();
        self.status.temporary_resource_count = self.temporary_resources.len();
    }
}

enum SupervisorCommand {
    Shutdown,
    Request {
        tracking_id: String,
        method: String,
        payload: Value,
        timeout: Duration,
        response: Sender<Result<Value, BrainHostRequestError>>,
    },
}

enum LaunchError {
    Failed(String),
    Shutdown,
}

impl From<String> for LaunchError {
    fn from(error: String) -> Self {
        Self::Failed(error)
    }
}

pub struct BrainHostSupervisor {
    shared: Arc<Mutex<RuntimeState>>,
    commands: Mutex<Option<Sender<SupervisorCommand>>>,
    thread: Mutex<Option<JoinHandle<()>>>,
    callback: Option<StatusCallback>,
}

impl BrainHostSupervisor {
    pub fn start(config: BrainHostLaunchConfig, callback: Option<StatusCallback>) -> Self {
        Self::start_with_event_callback(config, callback, None)
    }

    pub fn start_with_event_callback(
        config: BrainHostLaunchConfig,
        callback: Option<StatusCallback>,
        event_callback: Option<EventCallback>,
    ) -> Self {
        let shared = Arc::new(Mutex::new(RuntimeState::new()));
        let (commands, receiver) = mpsc::channel();
        let thread_shared = Arc::clone(&shared);
        let thread_callback = callback.clone();
        let handle = thread::Builder::new()
            .name("sakura-brain-supervisor".into())
            .spawn(move || {
                supervise(
                    config,
                    receiver,
                    thread_shared,
                    thread_callback,
                    event_callback,
                )
            })
            .expect("Brain Host supervisor thread should start");
        Self {
            shared,
            commands: Mutex::new(Some(commands)),
            thread: Mutex::new(Some(handle)),
            callback,
        }
    }

    pub fn status(&self) -> BrainHostStatus {
        self.shared
            .lock()
            .expect("Brain state lock poisoned")
            .status
            .clone()
    }

    pub fn startup_state(&self) -> Option<Value> {
        self.shared
            .lock()
            .expect("Brain state lock poisoned")
            .startup_state
            .clone()
    }

    pub fn register_request(&self, request_id: impl Into<String>) -> bool {
        let mut state = self.shared.lock().expect("Brain state lock poisoned");
        if !state.status.accepting_requests {
            return false;
        }
        let inserted = state.pending_request_ids.insert(request_id.into());
        state.synchronize_counts();
        let status = state.status.clone();
        drop(state);
        notify(&self.callback, status);
        inserted
    }

    pub fn request(
        &self,
        method: impl Into<String>,
        payload: Value,
        timeout: Duration,
    ) -> Result<Value, BrainHostRequestError> {
        let tracking_id = format!("desktop-{}", Uuid::new_v4().simple());
        if !self.register_request(tracking_id.clone()) {
            return Err(BrainHostRequestError::unavailable(
                "Brain Host 尚未准备好，请稍后重试。",
            ));
        }
        let (response_tx, response_rx) = mpsc::channel();
        let sent = self
            .commands
            .lock()
            .expect("command lock poisoned")
            .as_ref()
            .is_some_and(|sender| {
                sender
                    .send(SupervisorCommand::Request {
                        tracking_id: tracking_id.clone(),
                        method: method.into(),
                        payload,
                        timeout,
                        response: response_tx,
                    })
                    .is_ok()
            });
        if !sent {
            complete_request(&self.shared, &self.callback, &tracking_id);
            return Err(BrainHostRequestError::unavailable(
                "Brain Host 监管线程已停止。",
            ));
        }
        response_rx
            .recv_timeout(timeout + Duration::from_secs(1))
            .unwrap_or_else(|error| {
                Err(BrainHostRequestError::transport(match error {
                    RecvTimeoutError::Timeout => "等待 Brain Host 响应超时。".to_string(),
                    RecvTimeoutError::Disconnected => "Brain Host 响应通道已关闭。".to_string(),
                }))
            })
    }

    pub fn register_temporary_resource(&self, path: PathBuf) -> bool {
        let mut state = self.shared.lock().expect("Brain state lock poisoned");
        if !state.status.accepting_requests {
            return false;
        }
        let inserted = state.temporary_resources.insert(path);
        state.synchronize_counts();
        let status = state.status.clone();
        drop(state);
        notify(&self.callback, status);
        inserted
    }

    pub fn shutdown(&self) {
        let sender = self.commands.lock().expect("command lock poisoned").take();
        let handle = self.thread.lock().expect("thread lock poisoned").take();
        if sender.is_none() && handle.is_none() {
            return;
        }
        mark_stopping(&self.shared, &self.callback);
        if let Some(sender) = sender {
            let _ = sender.send(SupervisorCommand::Shutdown);
        }
        if let Some(handle) = handle {
            let _ = handle.join();
        }
    }
}

impl Drop for BrainHostSupervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct ManagedProcess {
    child: Child,
    stdin: ChildStdin,
    responses: Receiver<Result<Value, String>>,
    reader_thread: Option<JoinHandle<()>>,
    session_id: String,
    startup_state: Option<Value>,
    next_sequence: u64,
    last_inbound_sequence: u64,
}

impl ManagedProcess {
    fn launch(
        config: &BrainHostLaunchConfig,
        session_id: String,
        credential: &str,
        commands: &Receiver<SupervisorCommand>,
    ) -> Result<Self, LaunchError> {
        let mut command = Command::new(&config.python_exe);
        match &config.entrypoint {
            BrainHostEntrypoint::Module(module) => {
                command.arg("-m").arg(module);
            }
            BrainHostEntrypoint::Script(script) => {
                command.arg(script);
            }
        }
        command
            .current_dir(&config.base_dir)
            .envs(&config.environment)
            .env("PYTHONIOENCODING", "utf-8")
            .env("SAKURA_HEADLESS", "1")
            .env("SAKURA_BASE_DIR", &config.base_dir)
            .env("SAKURA_SESSION_ID", &session_id)
            .env("SAKURA_SESSION_CREDENTIAL", credential)
            .env("SAKURA_PROTOCOL_VERSION", PROTOCOL_VERSION.to_string())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = command.spawn().map_err(|error| {
            LaunchError::Failed(format!(
                "failed to start Brain Host with {}: {error}",
                config.python_exe.display()
            ))
        })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Brain Host stdin was not piped".to_string())?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Brain Host stdout was not piped".to_string())?;
        let (responses_tx, responses) = mpsc::channel();
        let reader_thread = thread::Builder::new()
            .name("sakura-brain-reader".into())
            .spawn(move || loop {
                match read_frame(&mut stdout) {
                    Ok(Some(message)) => {
                        if responses_tx.send(Ok(message)).is_err() {
                            break;
                        }
                    }
                    Ok(None) => break,
                    Err(error) => {
                        let _ = responses_tx.send(Err(error.to_string()));
                        break;
                    }
                }
            })
            .map_err(|error| error.to_string())?;
        let mut process = Self {
            child,
            stdin,
            responses,
            reader_thread: Some(reader_thread),
            session_id,
            startup_state: None,
            next_sequence: 0,
            last_inbound_sequence: 0,
        };
        let startup = (|| {
            let hello = process.request_during_startup(
                "system.hello",
                json!({
                    "protocol": PROTOCOL_VERSION,
                    "session_credential": credential,
                }),
                config.startup_timeout,
                config.poll_interval,
                commands,
            )?;
            if hello.get("session_id").and_then(Value::as_str) != Some(process.session_id.as_str())
            {
                return Err(LaunchError::Failed(
                    "Brain Host hello returned another session".to_string(),
                ));
            }
            process.startup_state = hello.get("startup").cloned();
            let health = process.request_during_startup(
                "system.health",
                json!({}),
                config.startup_timeout,
                config.poll_interval,
                commands,
            )?;
            if health.get("ready").and_then(Value::as_bool) != Some(true) {
                return Err(LaunchError::Failed(
                    "Brain Host health check did not become ready".to_string(),
                ));
            }
            Ok(())
        })();
        if let Err(error) = startup {
            process.force_stop();
            return Err(error);
        }
        Ok(process)
    }

    fn request_during_startup(
        &mut self,
        method: &str,
        payload: Value,
        timeout: Duration,
        poll_interval: Duration,
        commands: &Receiver<SupervisorCommand>,
    ) -> Result<Value, LaunchError> {
        let request_id = self.write_request(method, payload, timeout)?;
        let deadline = Instant::now() + timeout;
        loop {
            match commands.try_recv() {
                Ok(SupervisorCommand::Shutdown) | Err(TryRecvError::Disconnected) => {
                    return Err(LaunchError::Shutdown);
                }
                Ok(SupervisorCommand::Request {
                    tracking_id: _,
                    response,
                    ..
                }) => {
                    let _ = response.send(Err(BrainHostRequestError::unavailable(
                        "Brain Host 正在启动，请稍后重试。",
                    )));
                }
                Err(TryRecvError::Empty) => {}
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(LaunchError::Failed(format!(
                    "Brain Host request timed out: {method}"
                )));
            }
            match self.responses.recv_timeout(remaining.min(poll_interval)) {
                Ok(Ok(response)) => {
                    return self
                        .validate_response(response, &request_id)
                        .map_err(LaunchError::Failed);
                }
                Ok(Err(error)) => return Err(LaunchError::Failed(error)),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(LaunchError::Failed(format!(
                        "Brain Host closed before responding: {method}"
                    )));
                }
            }
        }
    }

    fn request(
        &mut self,
        method: &str,
        payload: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        self.request_runtime(method, payload, timeout, &None)
            .map_err(|error| error.to_string())
    }

    fn request_runtime(
        &mut self,
        method: &str,
        payload: Value,
        timeout: Duration,
        event_callback: &Option<EventCallback>,
    ) -> Result<Value, BrainHostRequestError> {
        let request_id = self
            .write_request(method, payload, timeout)
            .map_err(BrainHostRequestError::transport)?;
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(BrainHostRequestError::transport(format!(
                    "Brain Host request timed out: {method}"
                )));
            }
            let message = self
                .responses
                .recv_timeout(remaining)
                .map_err(|error| {
                    BrainHostRequestError::transport(match error {
                        RecvTimeoutError::Timeout => {
                            format!("Brain Host request timed out: {method}")
                        }
                        RecvTimeoutError::Disconnected => {
                            format!("Brain Host closed before responding: {method}")
                        }
                    })
                })?
                .map_err(BrainHostRequestError::transport)?;
            self.validate_runtime_envelope(&message)?;
            match message.get("kind").and_then(Value::as_str) {
                Some("event") => self.forward_event(message, event_callback)?,
                Some("response") => {
                    return self.validate_runtime_response(message, &request_id);
                }
                _ => {
                    return Err(BrainHostRequestError::transport(
                        "Brain Host returned an unexpected message kind",
                    ));
                }
            }
        }
    }

    fn drain_events(&mut self, event_callback: &Option<EventCallback>) -> Result<(), String> {
        loop {
            match self.responses.try_recv() {
                Ok(Ok(message)) => {
                    self.validate_runtime_envelope(&message)
                        .map_err(|error| error.to_string())?;
                    if message.get("kind").and_then(Value::as_str) != Some("event") {
                        return Err("Brain Host returned an unexpected response".into());
                    }
                    self.forward_event(message, event_callback)
                        .map_err(|error| error.to_string())?;
                }
                Ok(Err(error)) => return Err(error),
                Err(TryRecvError::Empty) => return Ok(()),
                Err(TryRecvError::Disconnected) => {
                    return Err("Brain Host response channel closed".into());
                }
            }
        }
    }

    fn write_request(
        &mut self,
        method: &str,
        payload: Value,
        timeout: Duration,
    ) -> Result<String, String> {
        self.next_sequence += 1;
        let request_id = format!("system-{}", self.next_sequence);
        let message = json!({
            "protocol": PROTOCOL_VERSION,
            "kind": "request",
            "id": request_id,
            "session_id": self.session_id,
            "sequence": self.next_sequence,
            "method": method,
            "deadline_ms": timeout.as_millis().min(u64::MAX as u128) as u64,
            "payload": payload,
        });
        write_frame(&mut self.stdin, &message).map_err(|error| error.to_string())?;
        Ok(request_id)
    }

    fn validate_response(&mut self, response: Value, request_id: &str) -> Result<Value, String> {
        self.validate_runtime_envelope(&response)
            .map_err(|error| error.to_string())?;
        if response.get("kind").and_then(Value::as_str) != Some("response")
            || response.get("id").and_then(Value::as_str) != Some(request_id)
        {
            return Err("Brain Host returned a mismatched response".into());
        }
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            let code = response
                .pointer("/error/code")
                .and_then(Value::as_str)
                .unwrap_or("BRAIN_REQUEST_FAILED");
            return Err(format!("Brain Host request failed: {code}"));
        }
        Ok(response
            .get("payload")
            .cloned()
            .unwrap_or_else(|| json!({})))
    }

    fn validate_runtime_envelope(&mut self, message: &Value) -> Result<(), BrainHostRequestError> {
        validate_envelope(message)
            .map_err(|error| BrainHostRequestError::transport(error.to_string()))?;
        if message.get("session_id").and_then(Value::as_str) != Some(self.session_id.as_str()) {
            return Err(BrainHostRequestError::transport(
                "Brain Host returned a message for another session",
            ));
        }
        let sequence = message.get("sequence").and_then(Value::as_u64).unwrap_or(0);
        let expected = self.last_inbound_sequence + 1;
        if sequence != expected {
            return Err(BrainHostRequestError::transport(format!(
                "Brain Host sequence mismatch: expected {expected}, got {sequence}"
            )));
        }
        self.last_inbound_sequence = sequence;
        Ok(())
    }

    fn validate_runtime_response(
        &self,
        response: Value,
        request_id: &str,
    ) -> Result<Value, BrainHostRequestError> {
        if response.get("id").and_then(Value::as_str) != Some(request_id) {
            return Err(BrainHostRequestError::transport(
                "Brain Host returned a mismatched response",
            ));
        }
        if response.get("ok").and_then(Value::as_bool) == Some(true) {
            return Ok(response
                .get("payload")
                .cloned()
                .unwrap_or_else(|| json!({})));
        }
        let error = response.get("error").cloned().unwrap_or_else(|| json!({}));
        Ok::<(), BrainHostRequestError>(()).and_then(|()| {
            Err(BrainHostRequestError {
                code: error
                    .get("code")
                    .and_then(Value::as_str)
                    .unwrap_or("BRAIN_REQUEST_FAILED")
                    .to_string(),
                message: error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Brain Host request failed")
                    .to_string(),
                retryable: error
                    .get("retryable")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                details: error.get("details").cloned().unwrap_or_else(|| json!({})),
            })
        })
    }

    fn forward_event(
        &self,
        message: Value,
        event_callback: &Option<EventCallback>,
    ) -> Result<(), BrainHostRequestError> {
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| BrainHostRequestError::transport("Brain event method is missing"))?;
        let event = BrainHostEvent {
            method: method.to_string(),
            payload: message.get("payload").cloned().unwrap_or_else(|| json!({})),
            session_id: self.session_id.clone(),
            sequence: message.get("sequence").and_then(Value::as_u64).unwrap_or(0),
        };
        if let Some(callback) = event_callback {
            callback(event);
        }
        Ok(())
    }

    fn try_wait(&mut self) -> Result<Option<ExitStatus>, String> {
        self.child.try_wait().map_err(|error| error.to_string())
    }

    fn finish_after_exit(&mut self) {
        if let Some(reader) = self.reader_thread.take() {
            let _ = reader.join();
        }
    }

    fn shutdown(&mut self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let _ = self.request("system.shutdown", json!({}), timeout);
        while Instant::now() < deadline {
            match self.try_wait() {
                Ok(Some(_)) => {
                    self.finish_after_exit();
                    return false;
                }
                Ok(None) => thread::sleep(Duration::from_millis(10)),
                Err(_) => break,
            }
        }
        self.force_stop();
        true
    }

    fn force_stop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
        self.finish_after_exit();
    }
}

impl Drop for ManagedProcess {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            self.force_stop();
        } else {
            self.finish_after_exit();
        }
    }
}

fn supervise(
    config: BrainHostLaunchConfig,
    commands: Receiver<SupervisorCommand>,
    shared: Arc<Mutex<RuntimeState>>,
    callback: Option<StatusCallback>,
    event_callback: Option<EventCallback>,
) {
    let mut restart_count = 0;
    loop {
        let session_id = format!("session-{}", Uuid::new_v4().simple());
        let credential = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        begin_launch(&shared, &callback, restart_count);
        let launch = ManagedProcess::launch(&config, session_id.clone(), &credential, &commands);
        let failure = match launch {
            Ok(mut process) => {
                mark_ready(
                    &shared,
                    &callback,
                    session_id,
                    restart_count,
                    process.startup_state.clone(),
                );
                loop {
                    match commands.recv_timeout(config.poll_interval) {
                        Ok(SupervisorCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                            mark_stopping(&shared, &callback);
                            let forced = process.shutdown(config.shutdown_timeout);
                            mark_stopped(&shared, &callback, forced);
                            return;
                        }
                        Ok(SupervisorCommand::Request {
                            tracking_id,
                            method,
                            payload,
                            timeout,
                            response,
                        }) => {
                            let result =
                                process.request_runtime(&method, payload, timeout, &event_callback);
                            complete_request(&shared, &callback, &tracking_id);
                            let _ = response.send(result);
                        }
                        Err(RecvTimeoutError::Timeout) => {
                            if let Err(error) = process.drain_events(&event_callback) {
                                break format!("Brain Host event channel failed: {error}");
                            }
                            match process.try_wait() {
                                Ok(Some(status)) => {
                                    process.finish_after_exit();
                                    break format!("Brain Host exited unexpectedly: {status}");
                                }
                                Ok(None) => {}
                                Err(error) => {
                                    break format!("Brain Host status check failed: {error}");
                                }
                            }
                        }
                    }
                }
            }
            Err(LaunchError::Failed(error)) => error,
            Err(LaunchError::Shutdown) => {
                mark_stopped(&shared, &callback, false);
                return;
            }
        };

        invalidate_runtime(&shared);
        if restart_count >= config.max_restarts {
            mark_diagnostic(&shared, &callback, restart_count, &failure);
            let _ = commands.recv();
            mark_stopped(&shared, &callback, false);
            return;
        }

        restart_count += 1;
        mark_restarting(&shared, &callback, restart_count, &failure);
        match commands.recv_timeout(config.backoff(restart_count)) {
            Ok(SupervisorCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                mark_stopped(&shared, &callback, false);
                return;
            }
            Ok(SupervisorCommand::Request {
                tracking_id,
                response,
                ..
            }) => {
                let _ = response.send(Err(BrainHostRequestError::unavailable(
                    "Brain Host 正在恢复，请稍后重试。",
                )));
                complete_request(&shared, &callback, &tracking_id);
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
    }
}

fn begin_launch(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    restart_count: u32,
) {
    let status = {
        let mut state = shared.lock().expect("Brain state lock poisoned");
        state.status.phase = if restart_count == 0 {
            BrainHostPhase::Starting
        } else {
            BrainHostPhase::Restarting
        };
        state.status.session_generation += 1;
        state.status.session_id = None;
        state.startup_state = None;
        state.status.restart_count = restart_count;
        state.status.accepting_requests = false;
        state.status.diagnostic = None;
        state.status.last_shutdown_forced = false;
        state.synchronize_counts();
        state.status.clone()
    };
    notify(callback, status);
}

fn mark_ready(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    session_id: String,
    restart_count: u32,
    startup_state: Option<Value>,
) {
    update_status(shared, callback, |state| {
        state.status.phase = BrainHostPhase::Ready;
        state.status.session_id = Some(session_id);
        state.status.restart_count = restart_count;
        state.status.accepting_requests = true;
        state.status.diagnostic = None;
        state.startup_state = startup_state;
    });
}

fn mark_restarting(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    restart_count: u32,
    _failure: &str,
) {
    update_status(shared, callback, |state| {
        state.status.phase = BrainHostPhase::Restarting;
        state.status.session_id = None;
        state.status.restart_count = restart_count;
        state.status.accepting_requests = false;
        state.status.diagnostic = None;
    });
}

fn mark_stopping(shared: &Arc<Mutex<RuntimeState>>, callback: &Option<StatusCallback>) {
    update_status(shared, callback, |state| {
        state.status.phase = BrainHostPhase::Stopping;
        state.status.accepting_requests = false;
    });
}

fn mark_stopped(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    forced: bool,
) {
    invalidate_runtime(shared);
    update_status(shared, callback, |state| {
        state.status.phase = BrainHostPhase::Stopped;
        state.status.session_id = None;
        state.status.accepting_requests = false;
        state.startup_state = None;
        state.status.last_shutdown_forced = forced;
    });
}

fn mark_diagnostic(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    restart_count: u32,
    failure: &str,
) {
    update_status(shared, callback, |state| {
        state.status.phase = BrainHostPhase::Diagnostic;
        state.status.session_id = None;
        state.startup_state = None;
        state.status.restart_count = restart_count;
        state.status.accepting_requests = false;
        state.status.diagnostic = Some(BrainHostDiagnostic {
            code: "BRAIN_RESTART_LIMIT".into(),
            message: format!(
                "Brain Host 连续失败，已停止自动重启。最后错误：{}",
                sanitize_diagnostic(failure)
            ),
            attempts: restart_count + 1,
        });
    });
}

fn invalidate_runtime(shared: &Arc<Mutex<RuntimeState>>) {
    let resources = {
        let mut state = shared.lock().expect("Brain state lock poisoned");
        state.status.session_id = None;
        state.status.accepting_requests = false;
        state.startup_state = None;
        state.pending_request_ids.clear();
        let resources = std::mem::take(&mut state.temporary_resources);
        state.synchronize_counts();
        resources
    };
    for resource in resources {
        let _ = fs::remove_file(resource);
    }
}

fn complete_request(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    request_id: &str,
) {
    let status = {
        let mut state = shared.lock().expect("Brain state lock poisoned");
        state.pending_request_ids.remove(request_id);
        state.synchronize_counts();
        state.status.clone()
    };
    notify(callback, status);
}

fn update_status(
    shared: &Arc<Mutex<RuntimeState>>,
    callback: &Option<StatusCallback>,
    update: impl FnOnce(&mut RuntimeState),
) {
    let status = {
        let mut state = shared.lock().expect("Brain state lock poisoned");
        update(&mut state);
        state.synchronize_counts();
        state.status.clone()
    };
    notify(callback, status);
}

fn notify(callback: &Option<StatusCallback>, status: BrainHostStatus) {
    if let Some(callback) = callback {
        callback(status);
    }
}

fn sanitize_diagnostic(message: &str) -> String {
    message.chars().take(500).collect()
}

#[cfg(test)]
mod tests {
    use std::ffi::OsStr;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use serde_json::Value;
    use tempfile::TempDir;

    use super::*;

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .expect("repository root should exist")
    }

    fn fixture_config(temp: &TempDir, mode: &str) -> BrainHostLaunchConfig {
        let root = repository_root();
        let mut config = BrainHostLaunchConfig::script(
            root.join("runtime/python.exe"),
            root.clone(),
            root.join("tests/fixtures/fake_brain_host.py"),
        );
        config
            .environment
            .insert("FAKE_BRAIN_MODE".into(), mode.into());
        config.environment.insert(
            "FAKE_BRAIN_COUNTER".into(),
            temp.path().join("launch-count.txt").into_os_string(),
        );
        config.environment.insert(
            "FAKE_BRAIN_RECORD".into(),
            temp.path().join("launches.jsonl").into_os_string(),
        );
        config.startup_timeout = Duration::from_secs(3);
        config.shutdown_timeout = Duration::from_millis(250);
        config.poll_interval = Duration::from_millis(10);
        config.restart_backoff = vec![
            Duration::from_millis(10),
            Duration::from_millis(20),
            Duration::from_millis(30),
        ];
        config
    }

    fn wait_for_status(
        supervisor: &BrainHostSupervisor,
        timeout: Duration,
        predicate: impl Fn(&BrainHostStatus) -> bool,
    ) -> BrainHostStatus {
        let deadline = Instant::now() + timeout;
        loop {
            let status = supervisor.status();
            if predicate(&status) {
                return status;
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for status: {status:?}"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn launch_records(path: &Path) -> Vec<Value> {
        fs::read_to_string(path)
            .expect("launch record should exist")
            .lines()
            .map(|line| serde_json::from_str(line).expect("launch record should be JSON"))
            .collect()
    }

    #[test]
    fn brain_host_python_resolution_prefers_explicit_override() {
        let temp = TempDir::new().unwrap();
        let runtime = temp.path().join("runtime/python.exe");
        let explicit = temp.path().join("custom/python.exe");
        fs::create_dir_all(runtime.parent().unwrap()).unwrap();
        fs::create_dir_all(explicit.parent().unwrap()).unwrap();
        fs::write(&runtime, b"runtime").unwrap();
        fs::write(&explicit, b"explicit").unwrap();

        let resolved = resolve_python_executable(temp.path(), Some(OsStr::new(&explicit))).unwrap();

        assert_eq!(resolved, explicit.canonicalize().unwrap());
    }

    #[test]
    fn brain_host_supervisor_handshakes_checks_health_and_shuts_down() {
        let temp = TempDir::new().unwrap();
        let supervisor = BrainHostSupervisor::start(fixture_config(&temp, "healthy"), None);
        let ready = wait_for_status(&supervisor, Duration::from_secs(5), |status| {
            status.phase == BrainHostPhase::Ready
        });

        assert!(ready.accepting_requests);
        assert!(ready
            .session_id
            .as_deref()
            .is_some_and(|value| !value.is_empty()));
        supervisor.shutdown();
        supervisor.shutdown();

        let stopped = supervisor.status();
        assert_eq!(stopped.phase, BrainHostPhase::Stopped);
        assert!(!stopped.last_shutdown_forced);
    }

    #[test]
    fn brain_host_restart_uses_new_session_and_invalidates_requests_and_resources() {
        let temp = TempDir::new().unwrap();
        let trigger = temp.path().join("crash-now");
        let resource = temp.path().join("temporary-resource.bin");
        fs::write(&resource, b"temporary").unwrap();
        let mut config = fixture_config(&temp, "crash_once_on_trigger");
        config.environment.insert(
            "FAKE_BRAIN_TRIGGER".into(),
            trigger.clone().into_os_string(),
        );
        let supervisor = BrainHostSupervisor::start(config, None);
        let first = wait_for_status(&supervisor, Duration::from_secs(5), |status| {
            status.phase == BrainHostPhase::Ready && status.restart_count == 0
        });
        assert!(supervisor.register_request("request-old"));
        assert!(supervisor.register_temporary_resource(resource.clone()));

        fs::write(&trigger, b"crash").unwrap();
        let second = wait_for_status(&supervisor, Duration::from_secs(5), |status| {
            status.phase == BrainHostPhase::Ready && status.restart_count == 1
        });

        assert_ne!(first.session_id, second.session_id);
        assert_eq!(second.pending_request_count, 0);
        assert_eq!(second.temporary_resource_count, 0);
        assert!(!resource.exists());
        let records = launch_records(&temp.path().join("launches.jsonl"));
        assert_ne!(records[0]["session_id"], records[1]["session_id"]);
        assert_ne!(records[0]["credential"], records[1]["credential"]);
        supervisor.shutdown();
    }

    #[test]
    fn brain_host_stops_after_three_restarts_and_enters_diagnostic_state() {
        let temp = TempDir::new().unwrap();
        let supervisor = BrainHostSupervisor::start(fixture_config(&temp, "always_crash"), None);

        let diagnostic = wait_for_status(&supervisor, Duration::from_secs(8), |status| {
            status.phase == BrainHostPhase::Diagnostic
        });

        assert_eq!(diagnostic.restart_count, 3);
        assert!(!diagnostic.accepting_requests);
        assert!(diagnostic.session_id.is_none());
        assert_eq!(launch_records(&temp.path().join("launches.jsonl")).len(), 4);
        assert_eq!(
            diagnostic.diagnostic.as_ref().unwrap().code,
            "BRAIN_RESTART_LIMIT"
        );
        supervisor.shutdown();
    }

    #[test]
    fn brain_host_forces_termination_when_graceful_shutdown_times_out() {
        let temp = TempDir::new().unwrap();
        let supervisor = BrainHostSupervisor::start(fixture_config(&temp, "ignore_shutdown"), None);
        wait_for_status(&supervisor, Duration::from_secs(5), |status| {
            status.phase == BrainHostPhase::Ready
        });

        supervisor.shutdown();

        let stopped = supervisor.status();
        assert_eq!(stopped.phase, BrainHostPhase::Stopped);
        assert!(stopped.last_shutdown_forced);
    }

    #[test]
    fn brain_host_shutdown_interrupts_a_stalled_handshake() {
        let temp = TempDir::new().unwrap();
        let supervisor = BrainHostSupervisor::start(fixture_config(&temp, "ignore_hello"), None);
        let counter = temp.path().join("launch-count.txt");
        let deadline = Instant::now() + Duration::from_secs(2);
        while !counter.exists() {
            assert!(Instant::now() < deadline, "fake Brain Host did not launch");
            thread::sleep(Duration::from_millis(10));
        }

        let started = Instant::now();
        supervisor.shutdown();

        assert!(started.elapsed() < Duration::from_secs(1));
        assert_eq!(supervisor.status().phase, BrainHostPhase::Stopped);
    }

    #[test]
    fn brain_host_routes_runtime_requests_and_forwards_async_events() {
        let temp = TempDir::new().unwrap();
        let events = Arc::new(Mutex::new(Vec::<BrainHostEvent>::new()));
        let captured = Arc::clone(&events);
        let event_callback: EventCallback = Arc::new(move |event| {
            captured.lock().unwrap().push(event);
        });
        let supervisor = BrainHostSupervisor::start_with_event_callback(
            fixture_config(&temp, "chat_events"),
            None,
            Some(event_callback),
        );
        wait_for_status(&supervisor, Duration::from_secs(5), |status| {
            status.phase == BrainHostPhase::Ready
        });

        let accepted = supervisor
            .request(
                "chat.send",
                json!({"text": "hello"}),
                Duration::from_secs(2),
            )
            .unwrap();

        assert_eq!(accepted["interactionId"], "interaction-fake");
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if events.lock().unwrap().len() >= 2 {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for chat events"
            );
            thread::sleep(Duration::from_millis(10));
        }
        let captured = events.lock().unwrap();
        assert_eq!(captured[0].method, "chat.progress");
        assert_eq!(captured[0].payload["stage"], "thinking");
        assert_eq!(captured[1].method, "chat.reply");
        drop(captured);
        assert_eq!(supervisor.status().pending_request_count, 0);
        supervisor.shutdown();
    }
}
