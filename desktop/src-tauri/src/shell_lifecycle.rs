use std::{
    collections::VecDeque,
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::Emitter;

use crate::{
    chat_bridge::{ChatBridge, ChatEventPublication, CHAT_EVENT},
    core_host_protocol::{PROTOCOL_MAJOR, PROTOCOL_MINOR},
    core_host_runtime::{ConcurrentRequestHandle, CoreHostRuntime},
    core_supervisor::{
        CoreSupervisor, FailureReason, GenerationId, LifecycleAction, LifecycleIntent,
        SupervisorSnapshot, SupervisorState,
    },
    platform::{FilesystemRuntimeLocator, RuntimeLocationRequest, RuntimeLocator},
    runtime_log::{Correlation, RuntimeLogEvent, RuntimeLogService, Severity},
};

const HELLO_DEADLINE: Duration = Duration::from_secs(3);
const INITIALIZE_DEADLINE: Duration = Duration::from_secs(5);
const SNAPSHOT_DEADLINE: Duration = Duration::from_secs(3);
const READINESS_DEADLINE: Duration = Duration::from_secs(30);
const SNAPSHOT_POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SupervisorPublication {
    state: &'static str,
    generation_id: Option<String>,
    generation_number: u64,
    app_shutdown: bool,
    failure: Option<FailurePublication>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FailurePublication {
    code: &'static str,
    message: &'static str,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotPublication {
    generation_id: String,
    revision: u64,
    readiness: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VersionPublication {
    desktop_version: &'static str,
    core_version: String,
    protocol_version: String,
    log_location: &'static str,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShellLifecyclePublication {
    supervisor: SupervisorPublication,
    snapshot: Option<SnapshotPublication>,
    character_presentation: Option<Value>,
    versions: VersionPublication,
}

#[derive(Clone)]
enum ShellCommand {
    Retry,
    Restart,
    Shutdown,
    #[cfg(test)]
    CrashForTest(Sender<Result<(), String>>),
}

#[derive(Clone)]
pub struct ShellLifecycleHandle {
    command: Sender<ShellCommand>,
    publication: Arc<Mutex<ShellLifecyclePublication>>,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    settings_request_number: Arc<AtomicU64>,
    chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
}

impl ShellLifecycleHandle {
    pub fn snapshot(&self) -> Result<ShellLifecyclePublication, &'static str> {
        self.publication
            .lock()
            .map(|publication| publication.clone())
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn character_presentation(&self) -> Result<Option<Value>, &'static str> {
        self.publication
            .lock()
            .map(|publication| publication.character_presentation.clone())
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn available_generation_id(&self) -> Result<Option<String>, &'static str> {
        self.publication
            .lock()
            .map(|publication| available_generation_id(&publication))
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn available_generation_identity(&self) -> Result<Option<(String, u64)>, &'static str> {
        self.publication
            .lock()
            .map(|publication| {
                available_generation_id(&publication)
                    .map(|generation_id| (generation_id, publication.supervisor.generation_number))
            })
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn ready_character_generation(
        &self,
        previous_generation_id: &str,
        previous_generation_number: u64,
        target_character_id: &str,
    ) -> Result<Option<String>, &'static str> {
        self.publication
            .lock()
            .map(|publication| {
                ready_character_generation(
                    &publication,
                    previous_generation_id,
                    previous_generation_number,
                    target_character_id,
                )
            })
            .map_err(|_| "LIFECYCLE_STATE_UNAVAILABLE")
    }

    pub fn retry(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Retry)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    pub fn restart(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Restart)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    pub fn settings_request(
        &self,
        request_id: Option<&str>,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<Value, String> {
        let generated;
        let request_id = match request_id {
            Some(value) if !value.trim().is_empty() => value,
            Some(_) => return Err("SETTINGS_REQUEST_INVALID".to_string()),
            None => {
                generated = format!(
                    "settings-{}",
                    self.settings_request_number.fetch_add(1, Ordering::Relaxed) + 1
                );
                &generated
            }
        };
        let transport = self
            .settings_transport
            .lock()
            .map_err(|_| "SETTINGS_TRANSPORT_UNAVAILABLE".to_string())?
            .clone()
            .ok_or_else(|| "SETTINGS_TRANSPORT_UNAVAILABLE".to_string())?;
        transport.request(request_id, name, payload, deadline)
    }

    pub fn request_shutdown(&self) -> Result<(), &'static str> {
        self.command
            .send(ShellCommand::Shutdown)
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE")
    }

    #[cfg(test)]
    fn crash_core_for_test(&self) -> Result<(), String> {
        let (reply, result) = mpsc::channel();
        self.command
            .send(ShellCommand::CrashForTest(reply))
            .map_err(|_| "LIFECYCLE_COMMAND_UNAVAILABLE".to_string())?;
        result
            .recv_timeout(Duration::from_secs(2))
            .map_err(|_| "CORE_TEST_CRASH_TIMEOUT".to_string())?
    }

    pub fn chat_bridge(&self) -> Result<ChatBridge, String> {
        self.chat_bridge
            .lock()
            .map_err(|_| "CHAT_BRIDGE_UNAVAILABLE".to_string())?
            .clone()
            .ok_or_else(|| "CHAT_BRIDGE_UNAVAILABLE".to_string())
    }
}

pub struct ShellLifecycleSession {
    handle: ShellLifecycleHandle,
    worker: Option<JoinHandle<()>>,
    chat_events: Option<Receiver<ChatEventPublication>>,
    chat_projector: Option<JoinHandle<()>>,
}

impl ShellLifecycleSession {
    #[cfg(test)]
    pub fn start(request: RuntimeLocationRequest) -> Self {
        Self::start_inner(request, None)
    }

    pub fn start_observed(request: RuntimeLocationRequest, runtime_log: RuntimeLogService) -> Self {
        Self::start_inner(request, Some(runtime_log))
    }

    fn start_inner(
        request: RuntimeLocationRequest,
        runtime_log: Option<RuntimeLogService>,
    ) -> Self {
        let (command, commands) = mpsc::channel();
        let initial = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "stopped",
                generation_id: None,
                generation_number: 0,
                app_shutdown: false,
                failure: None,
            },
            snapshot: None,
            character_presentation: None,
            versions: VersionPublication {
                desktop_version: env!("CARGO_PKG_VERSION"),
                core_version: "unavailable".to_string(),
                protocol_version: format!("{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"),
                log_location: "Sakura application logs",
            },
        };
        let publication = Arc::new(Mutex::new(initial));
        let settings_transport = Arc::new(Mutex::new(None));
        let chat_bridge = Arc::new(Mutex::new(None));
        let (chat_event_sender, chat_events) = mpsc::channel();
        let worker_publication = publication.clone();
        let worker_settings_transport = settings_transport.clone();
        let worker_chat_bridge = chat_bridge.clone();
        let worker = thread::spawn(move || {
            run_worker(
                request,
                commands,
                worker_publication,
                worker_settings_transport,
                worker_chat_bridge,
                chat_event_sender,
                runtime_log,
            )
        });
        Self {
            handle: ShellLifecycleHandle {
                command,
                publication,
                settings_transport,
                settings_request_number: Arc::new(AtomicU64::new(0)),
                chat_bridge,
            },
            worker: Some(worker),
            chat_events: Some(chat_events),
            chat_projector: None,
        }
    }

    pub fn handle(&self) -> ShellLifecycleHandle {
        self.handle.clone()
    }

    pub fn bind_chat_projection(&mut self, app: tauri::AppHandle) -> Result<(), &'static str> {
        if self.chat_projector.is_some() {
            return Ok(());
        }
        let events = self
            .chat_events
            .take()
            .ok_or("CHAT_PROJECTOR_UNAVAILABLE")?;
        self.chat_projector = Some(thread::spawn(move || {
            while let Ok(event) = events.recv() {
                let _ = app.emit_to("main", CHAT_EVENT, event);
            }
        }));
        Ok(())
    }

    pub fn shutdown_and_join(mut self) -> Result<(), &'static str> {
        let _ = self.handle.request_shutdown();
        let Some(worker) = self.worker.take() else {
            return Ok(());
        };
        worker.join().map_err(|_| "LIFECYCLE_WORKER_FAILED")?;
        if let Some(projector) = self.chat_projector.take() {
            projector.join().map_err(|_| "CHAT_PROJECTOR_FAILED")?;
        }
        Ok(())
    }
}

impl Drop for ShellLifecycleSession {
    fn drop(&mut self) {
        let _ = self.handle.request_shutdown();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        if let Some(projector) = self.chat_projector.take() {
            let _ = projector.join();
        }
    }
}

struct WorkerState {
    request: RuntimeLocationRequest,
    supervisor: CoreSupervisor,
    host: Option<CoreHostRuntime>,
    chat_bridge: Option<ChatBridge>,
    snapshot: Option<Value>,
    identity: Option<(GenerationId, u64)>,
    core_version: String,
    request_number: u64,
    cleanup_blocked: bool,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    shared_chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
    runtime_log: Option<RuntimeLogService>,
}

fn run_worker(
    request: RuntimeLocationRequest,
    commands: Receiver<ShellCommand>,
    publication: Arc<Mutex<ShellLifecyclePublication>>,
    settings_transport: Arc<Mutex<Option<ConcurrentRequestHandle>>>,
    shared_chat_bridge: Arc<Mutex<Option<ChatBridge>>>,
    chat_events: Sender<ChatEventPublication>,
    runtime_log: Option<RuntimeLogService>,
) {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos() as u64)
        ^ u64::from(std::process::id());
    let mut state = WorkerState {
        request,
        supervisor: CoreSupervisor::new(nonce),
        host: None,
        chat_bridge: None,
        snapshot: None,
        identity: None,
        core_version: "unavailable".to_string(),
        request_number: 0,
        cleanup_blocked: false,
        settings_transport,
        shared_chat_bridge,
        runtime_log,
    };
    let mut actions = VecDeque::from(state.supervisor.submit(LifecycleIntent::Start));
    publish(&state, &publication);

    loop {
        while let Some(action) = actions.pop_front() {
            match action {
                LifecycleAction::SpawnGeneration {
                    generation_id,
                    generation_number,
                    ..
                } => {
                    state.identity = Some((generation_id, generation_number));
                    log_lifecycle(
                        &state,
                        Severity::Info,
                        "core.spawn.started",
                        "Core generation spawn started",
                        json!({"outcome": "started", "attempt": generation_number}),
                    );
                    invalidate_generation_surfaces(&mut state);
                    state.core_version = "unavailable".to_string();
                    publish(&state, &publication);
                    drain_commands(&commands, &mut state, &mut actions);
                    if state.supervisor.snapshot().state != SupervisorState::Spawning {
                        continue;
                    }
                    if state.cleanup_blocked {
                        continue;
                    }
                    if let Err(reason) = spawn_and_initialize(
                        &mut state,
                        generation_id,
                        &commands,
                        &mut actions,
                        &publication,
                    ) {
                        log_lifecycle(
                            &state,
                            Severity::Warning,
                            "core.spawn.failed",
                            "Core generation spawn or initialization failed",
                            json!({"outcome": "failed", "category": failure_reason(reason)}),
                        );
                        invalidate_generation_surfaces(&mut state);
                        actions.extend(
                            state
                                .supervisor
                                .observe_generation_failed(generation_id, reason),
                        );
                        publish(&state, &publication);
                    }
                }
                LifecycleAction::StopGeneration { generation_id, .. } => {
                    log_lifecycle(
                        &state,
                        Severity::Info,
                        "core.stop.started",
                        "Core generation stop started",
                        json!({"outcome": "started"}),
                    );
                    publish(&state, &publication);
                    let cleaned = stop_generation(&mut state);
                    log_lifecycle(
                        &state,
                        if cleaned {
                            Severity::Info
                        } else {
                            Severity::Warning
                        },
                        "core.stop.completed",
                        "Core generation stop completed",
                        json!({"outcome": if cleaned { "completed" } else { "failed" }}),
                    );
                    drain_commands(&commands, &mut state, &mut actions);
                    if cleaned && !state.cleanup_blocked {
                        state.snapshot = None;
                        actions.extend(state.supervisor.finalize_generation(generation_id).actions);
                    } else {
                        state.cleanup_blocked = true;
                    }
                    publish(&state, &publication);
                }
            }
        }

        publish(&state, &publication);
        if state.supervisor.snapshot().app_shutdown && state.host.is_none() {
            break;
        }
        if state.cleanup_blocked {
            match commands.recv() {
                Ok(ShellCommand::Shutdown) | Err(_) => break,
                Ok(ShellCommand::Retry | ShellCommand::Restart) => continue,
                #[cfg(test)]
                Ok(ShellCommand::CrashForTest(reply)) => {
                    let _ = reply.send(Err("CORE_TEST_CLEANUP_BLOCKED".to_string()));
                }
            }
        }

        match commands.recv_timeout(SNAPSHOT_POLL_INTERVAL) {
            Ok(command) => actions.extend(submit_command(&mut state, command)),
            Err(RecvTimeoutError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown))
            }
            Err(RecvTimeoutError::Timeout) => {
                drain_chat_events(&mut state, &chat_events);
                if state.supervisor.snapshot().state == SupervisorState::Running {
                    match refresh_snapshot(&mut state) {
                        Ok(()) => publish(&state, &publication),
                        Err(()) => {
                            if let Some((generation_id, _)) = state.identity {
                                invalidate_generation_surfaces(&mut state);
                                actions.extend(state.supervisor.observe_generation_failed(
                                    generation_id,
                                    FailureReason::UnexpectedExit,
                                ));
                                publish(&state, &publication);
                            }
                        }
                    }
                }
            }
        }
    }
    log_lifecycle(
        &state,
        Severity::Info,
        "core.lifecycle.stopped",
        "Core lifecycle worker stopped",
        json!({"outcome": "completed"}),
    );
}

fn drain_chat_events(state: &mut WorkerState, events: &Sender<ChatEventPublication>) {
    let Some(host) = state.host.as_ref() else {
        return;
    };
    loop {
        let event = match host.recv_event_timeout(Duration::ZERO) {
            Ok(Some(event)) => event,
            Ok(None) | Err(_) => return,
        };
        if event
            .get("name")
            .and_then(Value::as_str)
            .is_some_and(|name| name.starts_with("chat."))
        {
            if let Some(bridge) = state.chat_bridge.as_ref() {
                if let Ok(Some(publication)) = bridge.observe_event(&event) {
                    let _ = events.send(publication);
                }
            }
        }
    }
}

fn submit_command(state: &mut WorkerState, command: ShellCommand) -> Vec<LifecycleAction> {
    match command {
        ShellCommand::Retry => state.supervisor.submit(LifecycleIntent::Retry),
        ShellCommand::Restart => state.supervisor.submit(LifecycleIntent::Restart),
        ShellCommand::Shutdown => state.supervisor.submit(LifecycleIntent::AppShutdown),
        #[cfg(test)]
        ShellCommand::CrashForTest(reply) => {
            let result = state
                .host
                .as_mut()
                .ok_or_else(|| "CORE_TEST_HOST_UNAVAILABLE".to_string())
                .and_then(CoreHostRuntime::terminate_tree_for_test);
            let _ = reply.send(result);
            Vec::new()
        }
    }
}

fn drain_commands(
    commands: &Receiver<ShellCommand>,
    state: &mut WorkerState,
    actions: &mut VecDeque<LifecycleAction>,
) {
    loop {
        match commands.try_recv() {
            Ok(command) => actions.extend(submit_command(state, command)),
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown));
                return;
            }
        }
    }
}

fn spawn_and_initialize(
    state: &mut WorkerState,
    generation_id: GenerationId,
    commands: &Receiver<ShellCommand>,
    actions: &mut VecDeque<LifecycleAction>,
    publication: &Arc<Mutex<ShellLifecyclePublication>>,
) -> Result<(), FailureReason> {
    let layout = FilesystemRuntimeLocator
        .locate(&state.request)
        .map_err(|_| FailureReason::DeterministicRuntime)?;
    let generation_text = generation_text(generation_id);
    let generation_number = state.identity.map_or(1, |(_, number)| number);
    let host_result = match state.runtime_log.as_ref() {
        Some(runtime_log) => CoreHostRuntime::launch_observed(
            &layout,
            &generation_text,
            generation_number,
            runtime_log.clone(),
        ),
        None => CoreHostRuntime::launch(&layout, &generation_text),
    };
    let host = match host_result {
        Ok(host) => host,
        Err(failure) => {
            if failure.into_recovery().is_some() {
                state.cleanup_blocked = true;
            }
            return Err(FailureReason::TemporarySpawnFailure);
        }
    };
    let core_pid = host.root_pid();
    state.host = Some(host);
    state.supervisor.observe_spawn_succeeded(generation_id);
    log_lifecycle(
        state,
        Severity::Info,
        "core.spawn.completed",
        "Core generation spawn completed",
        json!({"outcome": "completed", "core_pid": core_pid}),
    );
    publish(state, publication);

    let hello = state
        .host
        .as_mut()
        .expect("spawned host remains owned")
        .request("shell-hello", "system.hello", HELLO_DEADLINE)
        .map_err(|error| classify_control_failure(&error, FailureReason::HelloTimeout))?;
    if hello.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(FailureReason::ProtocolMajorIncompatible);
    }
    log_lifecycle(
        state,
        Severity::Info,
        "core.hello.completed",
        "Core protocol hello completed",
        json!({"outcome": "completed"}),
    );
    state.core_version = hello
        .pointer("/payload/coreVersion")
        .and_then(Value::as_str)
        .filter(|value| is_safe_version(value))
        .unwrap_or("unavailable")
        .to_string();

    let initialize = state
        .host
        .as_mut()
        .expect("initialized host remains owned")
        .request_with_payload(
            "shell-initialize",
            "core.initialize",
            json!({}),
            INITIALIZE_DEADLINE,
        )
        .map_err(|error| classify_control_failure(&error, FailureReason::InitializeTimeout))?;
    if initialize.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(FailureReason::DeterministicConfiguration);
    }
    log_lifecycle(
        state,
        Severity::Info,
        "core.initialize.completed",
        "Core initialization request completed",
        json!({"outcome": "completed"}),
    );

    let readiness_deadline = Instant::now() + READINESS_DEADLINE;
    state.chat_bridge = state
        .host
        .as_ref()
        .and_then(|host| host.chat_gateway().ok())
        .and_then(|gateway| {
            ChatBridge::new(
                gateway,
                generation_text.clone(),
                state.identity.map_or(0, |(_, number)| number),
            )
            .ok()
        });
    if let Ok(mut target) = state.shared_chat_bridge.lock() {
        *target = state.chat_bridge.clone();
    }
    let settings_handle = state.host.as_ref().and_then(|host| {
        let negotiated = host.negotiation().is_some_and(|value| {
            value
                .capabilities
                .iter()
                .any(|capability| capability == "settings.provider-model")
        });
        negotiated
            .then(|| host.concurrent_request_handle().ok())
            .flatten()
    });
    if let Ok(mut target) = state.settings_transport.lock() {
        *target = settings_handle;
    }
    let mut restart_after_readiness = false;
    loop {
        match commands.try_recv() {
            Ok(ShellCommand::Restart) => {
                // Settings become writable as soon as Core transport is ready,
                // while Assistant/MCP initialization may still be running.
                // Coalesce restarts until readiness is stable so shutdown does
                // not race the initializer and report SHUTDOWN_DURING_INITIALIZE.
                restart_after_readiness = true;
            }
            Ok(command) => {
                actions.extend(submit_command(state, command));
                return Ok(());
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                actions.extend(state.supervisor.submit(LifecycleIntent::AppShutdown));
                return Ok(());
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        refresh_snapshot(state).map_err(|_| FailureReason::ConnectionLost)?;
        publish(state, publication);
        let readiness = state
            .snapshot
            .as_ref()
            .and_then(|snapshot| snapshot.get("readiness"))
            .and_then(Value::as_str);
        if matches!(
            readiness,
            Some("ready" | "setup_required" | "degraded" | "failed")
        ) {
            log_lifecycle(
                state,
                if matches!(readiness, Some("failed")) {
                    Severity::Warning
                } else {
                    Severity::Info
                },
                "core.readiness.reached",
                "Core reached a stable readiness state",
                json!({"host_state": readiness.unwrap_or("unknown"), "outcome": "completed"}),
            );
            if restart_after_readiness {
                actions.extend(submit_command(state, ShellCommand::Restart));
            }
            return Ok(());
        }
        if Instant::now() >= readiness_deadline {
            return Err(FailureReason::InitializeTimeout);
        }
        thread::sleep(SNAPSHOT_POLL_INTERVAL);
    }
}

fn log_lifecycle(
    state: &WorkerState,
    severity: Severity,
    event: &'static str,
    message: &'static str,
    attributes: Value,
) {
    let Some(runtime_log) = state.runtime_log.as_ref() else {
        return;
    };
    let correlation = state
        .identity
        .map_or_else(Correlation::default, |(generation, number)| Correlation {
            generation_id: Some(generation_text(generation)),
            generation_number: Some(number),
            core_pid: state.host.as_ref().map(CoreHostRuntime::root_pid),
            ..Correlation::default()
        });
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, "core.lifecycle", event, message)
            .correlation(correlation)
            .attributes(attributes),
    );
}

fn refresh_snapshot(state: &mut WorkerState) -> Result<(), ()> {
    state.request_number += 1;
    let request_id = format!("shell-snapshot-{}", state.request_number);
    let snapshot = state
        .host
        .as_mut()
        .ok_or(())?
        .refresh_snapshot(&request_id, SNAPSHOT_DEADLINE)
        .map_err(|_| ())?;
    state.snapshot = Some(snapshot);
    Ok(())
}

fn stop_generation(state: &mut WorkerState) -> bool {
    invalidate_generation_surfaces(state);
    let Some(host) = state.host.take() else {
        return true;
    };
    match host.shutdown() {
        Ok(exit) => exit.tree_empty && exit.stderr_stats.eof && !exit.stderr_stats.read_failed,
        Err(failure) => failure.into_recovery().is_none(),
    }
}

fn invalidate_generation_surfaces(state: &mut WorkerState) {
    clear_settings_transport(state);
    clear_chat_bridge(state);
    if let Some(bridge) = state.chat_bridge.take() {
        bridge.invalidate();
    }
    state.snapshot = None;
}

fn clear_chat_bridge(state: &WorkerState) {
    if let Ok(mut bridge) = state.shared_chat_bridge.lock() {
        if let Some(existing) = bridge.take() {
            existing.invalidate();
        }
    }
}

fn clear_settings_transport(state: &WorkerState) {
    if let Ok(mut transport) = state.settings_transport.lock() {
        *transport = None;
    }
}

fn classify_control_failure(error: &str, timeout: FailureReason) -> FailureReason {
    if error.starts_with("PROTOCOL_MAJOR_MISMATCH") {
        FailureReason::ProtocolMajorIncompatible
    } else if error.starts_with("MISSING_REQUIRED_CAPABILITY")
        || error.starts_with("INVALID_NEGOTIATION")
    {
        FailureReason::MissingRequiredCapability
    } else if error.starts_with("GENERATION_CREDENTIAL_MISMATCH") {
        FailureReason::SecurityBoundary
    } else {
        timeout
    }
}

fn publish(state: &WorkerState, target: &Arc<Mutex<ShellLifecyclePublication>>) {
    let supervisor = state.supervisor.snapshot();
    let identity = supervisor
        .current
        .map(|generation| (generation.id, generation.number))
        .or(state.identity);
    let snapshot = state.snapshot.as_ref().and_then(|snapshot| {
        let (generation_id, _) = identity?;
        let generation_id = generation_text(generation_id);
        if snapshot.get("generationId").and_then(Value::as_str) != Some(generation_id.as_str()) {
            return None;
        }
        Some(SnapshotPublication {
            generation_id,
            revision: snapshot.get("revision").and_then(Value::as_u64)?,
            readiness: snapshot
                .get("readiness")
                .and_then(Value::as_str)?
                .to_string(),
        })
    });
    let next = ShellLifecyclePublication {
        supervisor: supervisor_publication(supervisor, identity),
        snapshot,
        character_presentation: state.snapshot.as_ref().and_then(|snapshot| {
            let (generation_id, _) = identity?;
            let generation_id = generation_text(generation_id);
            let presentation = snapshot.get("characterPresentation")?;
            if presentation.get("generationId").and_then(Value::as_str)
                != Some(generation_id.as_str())
            {
                return None;
            }
            Some(presentation.clone())
        }),
        versions: VersionPublication {
            desktop_version: env!("CARGO_PKG_VERSION"),
            core_version: state.core_version.clone(),
            protocol_version: format!("{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"),
            log_location: "Sakura application logs",
        },
    };
    if let Ok(mut publication) = target.lock() {
        *publication = next;
    }
}

fn available_generation_id(publication: &ShellLifecyclePublication) -> Option<String> {
    if publication.supervisor.state != "running" {
        return None;
    }
    let generation_id = publication.supervisor.generation_id.as_ref()?;
    if publication.snapshot.as_ref()?.generation_id != *generation_id {
        return None;
    }
    Some(generation_id.clone())
}

fn ready_character_generation(
    publication: &ShellLifecyclePublication,
    previous_generation_id: &str,
    previous_generation_number: u64,
    target_character_id: &str,
) -> Option<String> {
    let generation_id = available_generation_id(publication)?;
    let snapshot = publication.snapshot.as_ref()?;
    let presentation = publication.character_presentation.as_ref()?;
    let ready = publication.supervisor.generation_number > previous_generation_number
        && generation_id != previous_generation_id
        && snapshot.generation_id == generation_id
        && matches!(snapshot.readiness.as_str(), "ready" | "degraded")
        && presentation.get("generationId").and_then(Value::as_str) == Some(generation_id.as_str())
        && presentation.get("characterId").and_then(Value::as_str) == Some(target_character_id);
    ready.then_some(generation_id)
}

fn supervisor_publication(
    snapshot: SupervisorSnapshot,
    identity: Option<(GenerationId, u64)>,
) -> SupervisorPublication {
    SupervisorPublication {
        state: supervisor_state(snapshot.state),
        generation_id: identity.map(|(generation_id, _)| generation_text(generation_id)),
        generation_number: identity.map_or(0, |(_, number)| number),
        app_shutdown: snapshot.app_shutdown,
        failure: snapshot.failure.map(|reason| FailurePublication {
            code: failure_reason(reason),
            message: failure_message(reason),
        }),
    }
}

fn supervisor_state(state: SupervisorState) -> &'static str {
    match state {
        SupervisorState::Stopped => "stopped",
        SupervisorState::Spawning => "spawning",
        SupervisorState::Running => "running",
        SupervisorState::Stopping => "stopping",
        SupervisorState::Failed => "failed",
    }
}

fn failure_reason(reason: FailureReason) -> &'static str {
    match reason {
        FailureReason::UnexpectedExit => "unexpected_exit",
        FailureReason::TemporarySpawnFailure => "temporary_spawn_failure",
        FailureReason::HelloTimeout => "hello_timeout",
        FailureReason::InitializeTimeout => "initialize_timeout",
        FailureReason::ConnectionLost => "connection_lost",
        FailureReason::ProtocolMajorIncompatible => "protocol_major_incompatible",
        FailureReason::MissingRequiredCapability => "missing_required_capability",
        FailureReason::SetupRequired => "setup_required",
        FailureReason::DeterministicConfiguration => "deterministic_configuration",
        FailureReason::DeterministicRuntime => "deterministic_runtime",
        FailureReason::SecurityBoundary => "security_boundary",
    }
}

fn failure_message(reason: FailureReason) -> &'static str {
    match reason {
        FailureReason::UnexpectedExit => "Core 进程意外退出。",
        FailureReason::TemporarySpawnFailure => "Core 进程启动失败。",
        FailureReason::HelloTimeout => "Core 启动握手超时。",
        FailureReason::InitializeTimeout => "Core 初始化超时。",
        FailureReason::ConnectionLost => "与 Core 的连接已中断。",
        FailureReason::ProtocolMajorIncompatible => "Core 协议版本不兼容。",
        FailureReason::MissingRequiredCapability => "Core 缺少必需能力。",
        FailureReason::SetupRequired => "Core 需要先完成基础设置。",
        FailureReason::DeterministicConfiguration => "Core 配置无效，无法启动。",
        FailureReason::DeterministicRuntime => "找不到可用的 Core 运行环境。",
        FailureReason::SecurityBoundary => "Core 安全校验失败。",
    }
}

fn generation_text(generation_id: GenerationId) -> String {
    format!("{:032x}", generation_id.as_u128())
}

fn is_safe_version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.as_bytes()[0].is_ascii_digit()
        && value.contains('.')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn copy_fixture_tree(source: &std::path::Path, target: &std::path::Path) {
        std::fs::create_dir_all(target).expect("temporary fixture directory");
        for entry in std::fs::read_dir(source).expect("read fixture directory") {
            let entry = entry.expect("fixture entry");
            let source_path = entry.path();
            let target_path = target.join(entry.file_name());
            if entry.file_type().expect("fixture type").is_dir() {
                copy_fixture_tree(&source_path, &target_path);
            } else {
                std::fs::copy(source_path, target_path).expect("copy fixture file");
            }
        }
    }

    fn isolated_ready_user_root(repository_root: &std::path::Path) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "sakura-wp-5-03-character-switch-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        copy_fixture_tree(
            &repository_root.join("tests/fixtures/runtime_v2/wp_3_01/ready"),
            &root,
        );
        let beta = root.join("characters/beta");
        std::fs::create_dir_all(beta.join("portraits")).expect("beta portrait directory");
        std::fs::write(beta.join("card.md"), "You are the isolated Beta fixture.")
            .expect("beta card");
        std::fs::write(beta.join("portraits/neutral.txt"), "isolated beta portrait")
            .expect("beta portrait");
        std::fs::write(
            beta.join("character.json"),
            r#"{
  "id": "beta",
  "display_name": "Beta Fixture",
  "initial_message": "Beta greeting.",
  "card": "card.md",
  "portrait": {
    "default": "portraits/neutral.txt",
    "expressions": {"neutral": "portraits/neutral.txt"}
  },
  "reply": {"tones": ["neutral"]}
}"#,
        )
        .expect("beta character manifest");
        root.canonicalize().expect("canonical isolated user root")
    }

    fn wait_for_failed(
        handle: &ShellLifecycleHandle,
        expected_generation: u64,
    ) -> ShellLifecyclePublication {
        // The default Windows suite intentionally runs process-heavy tests in
        // parallel.  This is observation headroom for scheduling the lifecycle
        // worker, not a product startup or shutdown deadline.
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let publication = handle.snapshot().expect("lifecycle publication");
            if publication.supervisor.state == "failed"
                && publication.supervisor.generation_number == expected_generation
            {
                return publication;
            }
            assert!(
                Instant::now() < deadline,
                "lifecycle failure was not bounded: state={}, generation={}, failure={:?}",
                publication.supervisor.state,
                publication.supervisor.generation_number,
                publication
                    .supervisor
                    .failure
                    .as_ref()
                    .map(|failure| failure.code)
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_stable_generation(
        handle: &ShellLifecycleHandle,
        expected_generation: u64,
    ) -> ShellLifecyclePublication {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let publication = handle.snapshot().expect("lifecycle publication");
            if publication.supervisor.state == "running"
                && publication.supervisor.generation_number == expected_generation
                && publication.snapshot.as_ref().is_some_and(|snapshot| {
                    matches!(
                        snapshot.readiness.as_str(),
                        "ready" | "setup_required" | "degraded" | "failed"
                    )
                })
            {
                return publication;
            }
            assert!(
                Instant::now() < deadline,
                "real Core generation did not reach stable readiness: state={}, generation={}, failure={:?}, readiness={:?}",
                publication.supervisor.state,
                publication.supervisor.generation_number,
                publication
                    .supervisor
                    .failure
                    .as_ref()
                    .map(|failure| failure.code),
                publication
                    .snapshot
                    .as_ref()
                    .map(|snapshot| snapshot.readiness.as_str())
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn missing_runtime_stays_visible_and_repeated_retry_exit_are_bounded() {
        let _test_lock = crate::core_host_runtime::lifecycle_test_lock();
        let root = std::env::temp_dir().join(format!(
            "sakura-runtime-v2-wp-1d-01-missing-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir(&root).expect("isolated missing Runtime root");
        let executable_directory = std::env::current_exe()
            .expect("test executable")
            .parent()
            .expect("test executable directory")
            .to_path_buf();
        let session = ShellLifecycleSession::start(RuntimeLocationRequest {
            mode: crate::platform::RuntimeMode::ExplicitDevelopment,
            target: crate::platform::current_platform_target().expect("formal test target"),
            executable_directory,
            resource_directory: root.clone(),
            explicit_development_root: Some(root.clone()),
            user_root: root.clone(),
        });
        let handle = session.handle();

        let first = wait_for_failed(&handle, 1);
        assert_eq!(
            first
                .supervisor
                .failure
                .as_ref()
                .map(|failure| failure.code),
            Some("deterministic_runtime")
        );
        assert!(first.snapshot.is_none());

        handle.retry().expect("first retry enters Supervisor");
        handle
            .retry()
            .expect("duplicate retry enters the same channel");
        let second = wait_for_failed(&handle, 2);
        assert_eq!(second.supervisor.generation_number, 2);
        thread::sleep(Duration::from_millis(50));
        assert_eq!(
            handle
                .snapshot()
                .expect("settled lifecycle publication")
                .supervisor
                .generation_number,
            2
        );

        let shutdown_started = Instant::now();
        session
            .shutdown_and_join()
            .expect("missing Runtime lifecycle should exit cleanly");
        assert!(shutdown_started.elapsed() < Duration::from_secs(2));
        std::fs::remove_dir(&root).expect("isolated missing Runtime root should be empty");
    }

    #[test]
    fn wp_5_03_character_switch_restart_waits_for_cleanup_and_releases_old_generation() {
        let _test_lock = crate::core_host_runtime::lifecycle_test_lock();
        let manifest_directory = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repository_root = manifest_directory
            .join("../..")
            .canonicalize()
            .expect("repository root");
        let user_root = isolated_ready_user_root(&repository_root);
        let executable_directory = std::env::current_exe()
            .expect("test executable")
            .parent()
            .expect("test executable directory")
            .to_path_buf();
        let session = ShellLifecycleSession::start(RuntimeLocationRequest {
            mode: crate::platform::RuntimeMode::ExplicitDevelopment,
            target: crate::platform::current_platform_target().expect("formal test target"),
            executable_directory,
            resource_directory: repository_root.clone(),
            explicit_development_root: Some(repository_root.clone()),
            user_root: user_root.clone(),
        });
        let handle = session.handle();

        let first = wait_for_stable_generation(&handle, 1);
        assert_eq!(
            first
                .snapshot
                .as_ref()
                .expect("first stable Snapshot")
                .generation_id,
            first
                .supervisor
                .generation_id
                .expect("first Supervisor generation")
        );
        assert_eq!(
            first
                .character_presentation
                .as_ref()
                .and_then(|value| value.get("characterId"))
                .and_then(Value::as_str),
            Some("sakura")
        );

        let select_beta = handle
            .settings_request(
                Some("wp-5-03-select-beta"),
                "characters.settings.select",
                json!({"characterId": "beta"}),
                Duration::from_secs(5),
            )
            .expect("persist beta selection");
        assert_eq!(
            select_beta
                .pointer("/payload/changePlan")
                .and_then(Value::as_str),
            Some("core_restart_required")
        );
        handle
            .restart()
            .expect("beta restart enters Supervisor once");
        let second = wait_for_stable_generation(&handle, 2);
        assert_eq!(second.supervisor.generation_number, 2);
        assert_eq!(
            second
                .character_presentation
                .as_ref()
                .and_then(|value| value.get("characterId"))
                .and_then(Value::as_str),
            Some("beta")
        );

        let select_sakura = handle
            .settings_request(
                Some("wp-5-03-select-sakura"),
                "characters.settings.select",
                json!({"characterId": "sakura"}),
                Duration::from_secs(5),
            )
            .expect("persist sakura selection");
        assert_eq!(
            select_sakura
                .pointer("/payload/changePlan")
                .and_then(Value::as_str),
            Some("core_restart_required")
        );
        handle
            .restart()
            .expect("sakura restart enters Supervisor once");
        let third = wait_for_stable_generation(&handle, 3);
        assert_eq!(
            third
                .character_presentation
                .as_ref()
                .and_then(|value| value.get("characterId"))
                .and_then(Value::as_str),
            Some("sakura")
        );

        let shutdown_started = Instant::now();
        session
            .shutdown_and_join()
            .expect("real Core lifecycle should exit cleanly");
        assert!(shutdown_started.elapsed() < Duration::from_secs(6));
        std::fs::remove_dir_all(user_root).expect("remove isolated character-switch root");
    }

    #[test]
    fn real_core_crash_stops_at_failed_until_manual_retry() {
        let _test_lock = crate::core_host_runtime::lifecycle_test_lock();
        let manifest_directory = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repository_root = manifest_directory
            .join("../..")
            .canonicalize()
            .expect("repository root");
        let user_root = repository_root
            .join("tests/fixtures/runtime_v2/wp_3_01/ready")
            .canonicalize()
            .expect("ready Assistant fixture");
        let executable_directory = std::env::current_exe()
            .expect("test executable")
            .parent()
            .expect("test executable directory")
            .to_path_buf();
        let session = ShellLifecycleSession::start(RuntimeLocationRequest {
            mode: crate::platform::RuntimeMode::ExplicitDevelopment,
            target: crate::platform::current_platform_target().expect("formal test target"),
            executable_directory,
            resource_directory: repository_root.clone(),
            explicit_development_root: Some(repository_root),
            user_root,
        });
        let handle = session.handle();
        let first = wait_for_stable_generation(&handle, 1);
        let first_id = first.supervisor.generation_id.expect("first generation");
        assert_eq!(
            handle
                .available_generation_id()
                .expect("available generation")
                .as_deref(),
            Some(first_id.as_str())
        );

        handle
            .crash_core_for_test()
            .expect("test fault should terminate the complete Core tree");
        let invalidation_deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let publication = handle.snapshot().expect("recovery publication");
            if publication.supervisor.generation_number >= 1
                && publication.snapshot.is_none()
                && publication.character_presentation.is_none()
                && handle
                    .available_generation_id()
                    .expect("generation availability")
                    .is_none()
            {
                break;
            }
            assert!(
                Instant::now() < invalidation_deadline,
                "old generation surfaces were not invalidated before recovery"
            );
            thread::sleep(Duration::from_millis(5));
        }

        let failed = wait_for_failed(&handle, 1);
        assert_eq!(
            failed
                .supervisor
                .failure
                .as_ref()
                .map(|failure| failure.code),
            Some("unexpected_exit")
        );
        assert_eq!(
            failed
                .supervisor
                .failure
                .as_ref()
                .map(|failure| failure.message),
            Some("Core 进程意外退出。")
        );
        assert!(failed.snapshot.is_none());
        assert!(failed.character_presentation.is_none());
        assert!(handle
            .available_generation_id()
            .expect("failed generation availability")
            .is_none());
        thread::sleep(Duration::from_millis(100));
        assert_eq!(
            handle
                .snapshot()
                .expect("failed state remains visible")
                .supervisor
                .generation_number,
            1
        );

        handle
            .retry()
            .expect("manual retry uses the same Supervisor");
        let second = wait_for_stable_generation(&handle, 2);
        assert_ne!(
            second.supervisor.generation_id.as_deref(),
            Some(first_id.as_str())
        );
        session
            .shutdown_and_join()
            .expect("recovered lifecycle should reclaim all resources");
    }

    #[test]
    fn lifecycle_publication_serializes_only_the_approved_minimum() {
        let publication = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "failed",
                generation_id: Some("generation-safe".to_string()),
                generation_number: 2,
                app_shutdown: false,
                failure: Some(FailurePublication {
                    code: "deterministic_runtime",
                    message: "找不到可用的 Core 运行环境。",
                }),
            },
            snapshot: None,
            character_presentation: None,
            versions: VersionPublication {
                desktop_version: "0.1.0",
                core_version: "2.1.0".to_string(),
                protocol_version: "2.1".to_string(),
                log_location: "Sakura application logs",
            },
        };
        let encoded = serde_json::to_value(publication).expect("publication should serialize");
        let object = encoded.as_object().expect("publication is an object");
        assert_eq!(
            object.keys().map(String::as_str).collect::<Vec<_>>(),
            [
                "characterPresentation",
                "snapshot",
                "supervisor",
                "versions"
            ]
        );
        assert_eq!(
            encoded["supervisor"]
                .as_object()
                .expect("supervisor is an object")
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            [
                "appShutdown",
                "failure",
                "generationId",
                "generationNumber",
                "state"
            ]
        );
        assert_eq!(
            encoded["supervisor"]["failure"]
                .as_object()
                .expect("failure is an object")
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            ["code", "message"]
        );
        let text = encoded.to_string().to_ascii_lowercase();
        for forbidden in [
            "credential",
            "apikey",
            "prompt",
            "providerendpoint",
            "model",
            "config",
            "exception",
            "c:\\\\users",
        ] {
            assert!(!text.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn generation_surfaces_require_one_complete_running_snapshot() {
        let generation = "generation-safe".to_string();
        let mut publication = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "running",
                generation_id: Some(generation.clone()),
                generation_number: 2,
                app_shutdown: false,
                failure: None,
            },
            snapshot: Some(SnapshotPublication {
                generation_id: generation.clone(),
                revision: 7,
                readiness: "ready".to_string(),
            }),
            character_presentation: Some(json!({ "generationId": generation })),
            versions: VersionPublication {
                desktop_version: "0.1.0",
                core_version: "2.1.0".to_string(),
                protocol_version: "2.1".to_string(),
                log_location: "Sakura application logs",
            },
        };
        assert_eq!(
            available_generation_id(&publication).as_deref(),
            Some("generation-safe")
        );
        assert!(
            ready_character_generation(&publication, "generation-old", 1, "character-b").is_none()
        );
        publication.character_presentation = Some(json!({
            "generationId": "generation-safe",
            "characterId": "character-b",
        }));
        assert_eq!(
            ready_character_generation(&publication, "generation-old", 1, "character-b").as_deref(),
            Some("generation-safe")
        );
        assert!(
            ready_character_generation(&publication, "generation-old", 2, "character-b").is_none()
        );
        assert!(
            ready_character_generation(&publication, "generation-old", 1, "character-a").is_none()
        );

        publication.supervisor.state = "stopping";
        assert!(available_generation_id(&publication).is_none());
        publication.supervisor.state = "running";
        publication.snapshot = None;
        assert!(available_generation_id(&publication).is_none());
        publication.snapshot = Some(SnapshotPublication {
            generation_id: "stale-generation".to_string(),
            revision: 8,
            readiness: "failed".to_string(),
        });
        publication.character_presentation = None;
        assert!(available_generation_id(&publication).is_none());
        publication.snapshot = Some(SnapshotPublication {
            generation_id: "generation-safe".to_string(),
            revision: 9,
            readiness: "failed".to_string(),
        });
        assert_eq!(
            available_generation_id(&publication).as_deref(),
            Some("generation-safe")
        );
    }

    #[test]
    fn wp_5_03_character_ready_gate_requires_new_identity_snapshot_and_presentation() {
        let generation = "generation-b".to_string();
        let mut publication = ShellLifecyclePublication {
            supervisor: SupervisorPublication {
                state: "running",
                generation_id: Some(generation.clone()),
                generation_number: 2,
                app_shutdown: false,
                failure: None,
            },
            snapshot: Some(SnapshotPublication {
                generation_id: generation.clone(),
                revision: 1,
                readiness: "degraded".to_string(),
            }),
            character_presentation: Some(json!({
                "generationId": generation,
                "characterId": "beta",
            })),
            versions: VersionPublication {
                desktop_version: "0.1.0",
                core_version: "2.1.0".to_string(),
                protocol_version: "2.1".to_string(),
                log_location: "Sakura application logs",
            },
        };
        assert_eq!(
            ready_character_generation(&publication, "generation-a", 1, "beta").as_deref(),
            Some("generation-b")
        );
        publication.snapshot.as_mut().expect("snapshot").readiness = "failed".to_string();
        assert!(ready_character_generation(&publication, "generation-a", 1, "beta").is_none());
    }

    #[test]
    fn version_filter_rejects_paths_endpoints_and_exception_text() {
        for unsafe_value in [
            "C:\\Users\\private\\runtime.exe",
            "https://provider.invalid/v1",
            "RuntimeError('secret')",
            "sk-live-private",
        ] {
            assert!(!is_safe_version(unsafe_value));
        }
        for safe in ["2.1", "2.1.0", "2.1.0-dev+local"] {
            assert!(is_safe_version(safe));
        }
    }
}
