use std::{
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use crate::{
    core_supervisor::{
        CoreSupervisor, FailureReason, LifecycleAction, LifecycleIntent, StopReason,
        SupervisorState,
    },
    managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
};

const ACCEPTANCE_DIRECTORY_ENV: &str = "SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY";
const ACCEPTANCE_MODE_ENV: &str = "SAKURA_PHASE_1B_ACCEPTANCE_MODE";
const CHILD_DIRECTORY_ENV: &str = "SAKURA_PHASE_1B_FAKE_CORE_CHILD_DIRECTORY";
const CHILD_MODE_ENV: &str = "SAKURA_PHASE_1B_FAKE_CORE_CHILD_MODE";
const ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-1b-04-";
static CHILD_ENVIRONMENT_LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AcceptanceMode {
    PendingHello,
    RestartBackoff,
}

impl AcceptanceMode {
    fn parse(value: &str) -> Result<Self, String> {
        match value {
            "pending-hello" => Ok(Self::PendingHello),
            "restart-backoff" => Ok(Self::RestartBackoff),
            _ => Err(format!("unsupported Phase 1B acceptance mode: {value}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChildMode {
    PendingHello,
    CrashAfterHello,
}

impl ChildMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::PendingHello => "pending-hello",
            Self::CrashAfterHello => "crash-after-hello",
        }
    }

    fn parse(value: &str) -> Result<Self, String> {
        match value {
            "pending-hello" => Ok(Self::PendingHello),
            "crash-after-hello" => Ok(Self::CrashAfterHello),
            _ => Err(format!(
                "unsupported Phase 1B Fake Core child mode: {value}"
            )),
        }
    }
}

pub struct AcceptanceSession {
    cancellation: Arc<AtomicBool>,
    directory: PathBuf,
    worker: JoinHandle<Result<(), String>>,
}

#[derive(Clone)]
pub struct AcceptanceShutdown {
    cancellation: Arc<AtomicBool>,
    directory: PathBuf,
}

impl AcceptanceShutdown {
    pub fn request(&self) {
        self.cancellation.store(true, Ordering::Release);
        let _ = fs::write(
            self.directory.join("acceptance.shutdown_requested"),
            b"requested",
        );
    }
}

impl AcceptanceSession {
    pub fn start_if_requested() -> Result<Option<Self>, String> {
        let Some(directory) = std::env::var_os(ACCEPTANCE_DIRECTORY_ENV) else {
            return Ok(None);
        };
        let directory = validate_acceptance_path(PathBuf::from(directory))?;
        let mode = std::env::var(ACCEPTANCE_MODE_ENV)
            .map_err(|_| format!("{ACCEPTANCE_MODE_ENV} is required when acceptance is active"))
            .and_then(|value| AcceptanceMode::parse(&value))?;
        let cancellation = Arc::new(AtomicBool::new(false));
        let worker_cancellation = cancellation.clone();
        let worker_directory = directory.clone();
        let worker = thread::spawn(move || {
            let result = run_parent_scenario(&worker_directory, mode, &worker_cancellation);
            if let Err(error) = &result {
                let _ = fs::write(worker_directory.join("acceptance.error"), error.as_bytes());
            }
            result
        });
        Ok(Some(Self {
            cancellation,
            directory,
            worker,
        }))
    }

    pub fn shutdown_signal(&self) -> AcceptanceShutdown {
        AcceptanceShutdown {
            cancellation: self.cancellation.clone(),
            directory: self.directory.clone(),
        }
    }

    pub fn shutdown_and_join(self) -> Result<(), String> {
        self.shutdown_signal().request();
        self.worker
            .join()
            .map_err(|_| "Phase 1B acceptance worker panicked".to_string())?
    }
}

pub fn run_fake_core_child_if_requested() -> bool {
    let Some(directory) = std::env::var_os(CHILD_DIRECTORY_ENV) else {
        return false;
    };
    let result = validate_acceptance_path(PathBuf::from(directory)).and_then(|directory| {
        let mode = std::env::var(CHILD_MODE_ENV)
            .map_err(|_| format!("{CHILD_MODE_ENV} is required for a Fake Core child"))
            .and_then(|value| ChildMode::parse(&value))?;
        run_fake_core_child(&directory, mode)
    });
    if let Err(error) = result {
        eprintln!("Phase 1B Fake Core child failed: {error}");
        process::exit(61);
    }
    true
}

fn validate_acceptance_path(path: PathBuf) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("Phase 1B acceptance directory must be absolute".to_string());
    }
    let temp_root = fs::canonicalize(std::env::temp_dir())
        .map_err(|error| format!("failed to resolve system temp directory: {error}"))?;
    let resolved = fs::canonicalize(&path)
        .map_err(|error| format!("failed to resolve acceptance directory: {error}"))?;
    let has_expected_component = resolved.components().any(|component| {
        component
            .as_os_str()
            .to_string_lossy()
            .starts_with(ACCEPTANCE_DIRECTORY_PREFIX)
    });
    if !resolved.starts_with(&temp_root) || !has_expected_component {
        return Err(format!(
            "Phase 1B acceptance directory is outside its isolated temp scope: {}",
            resolved.display()
        ));
    }
    Ok(resolved)
}

fn run_fake_core_child(directory: &Path, mode: ChildMode) -> Result<(), String> {
    write_marker(directory, "transport.ready", b"ready")?;
    wait_for_marker(
        directory,
        "hello.request",
        Instant::now() + Duration::from_secs(15),
    )?;
    match mode {
        ChildMode::PendingHello => {
            write_marker(directory, "hello.pending", b"pending")?;
            wait_for_marker(
                directory,
                "shutdown.request",
                Instant::now() + Duration::from_secs(15),
            )?;
            write_marker(directory, "shutdown.ack", b"ack")
        }
        ChildMode::CrashAfterHello => {
            write_marker(directory, "hello.response", b"hello")?;
            write_marker(directory, "crash.ready", b"ready")?;
            process::exit(37);
        }
    }
}

fn run_parent_scenario(
    directory: &Path,
    mode: AcceptanceMode,
    cancellation: &AtomicBool,
) -> Result<(), String> {
    write_marker(directory, "acceptance.worker.started", b"started")?;
    match mode {
        AcceptanceMode::PendingHello => run_pending_hello(directory, cancellation),
        AcceptanceMode::RestartBackoff => run_restart_backoff(directory, cancellation),
    }
}

fn run_pending_hello(directory: &Path, cancellation: &AtomicBool) -> Result<(), String> {
    let mut supervisor = CoreSupervisor::new(0x1b04_7a02_0000_0001);
    let generation_id = take_spawn(supervisor.submit(LifecycleIntent::Start), 1)?;
    let child_directory = create_generation_directory(directory, 1)?;
    let mut tree = spawn_child(&child_directory, ChildMode::PendingHello)?;
    wait_for_marker(
        &child_directory,
        "transport.ready",
        Instant::now() + Duration::from_secs(3),
    )?;
    write_marker(&child_directory, "hello.request", b"hello")?;
    wait_for_marker(
        &child_directory,
        "hello.pending",
        Instant::now() + Duration::from_secs(3),
    )?;
    if child_directory.join("hello.response").exists() {
        return Err("pending hello child responded before app shutdown".to_string());
    }
    write_marker(directory, "acceptance.pending_hello", b"pending")?;
    wait_for_cancellation(cancellation, Instant::now() + Duration::from_secs(30))?;
    let actions = supervisor.submit(LifecycleIntent::AppShutdown);
    if !matches!(
        actions.as_slice(),
        [LifecycleAction::StopGeneration {
            generation_id: stopped_id,
            reason: StopReason::AppShutdown,
        }] if *stopped_id == generation_id
    ) {
        return Err(format!(
            "pending hello app shutdown actions were invalid: {actions:?}"
        ));
    }
    stop_child(&mut tree, &child_directory, 95)?;
    let finalized = supervisor.finalize_generation(generation_id);
    if !finalized.applied || !finalized.actions.is_empty() {
        return Err("pending hello generation did not finalize exactly once".to_string());
    }
    if child_directory.join("hello.response").exists() {
        return Err("pending hello completed after app shutdown".to_string());
    }
    write_marker(directory, "acceptance.cleaned", b"pending-hello")
}

fn run_restart_backoff(directory: &Path, cancellation: &AtomicBool) -> Result<(), String> {
    let mut supervisor = CoreSupervisor::new(0x1b04_7a02_0000_0002);
    let mut generation_id = take_spawn(supervisor.submit(LifecycleIntent::Start), 1)?;
    let mut final_token = None;
    for attempt in 1..=3_u64 {
        let child_directory = create_generation_directory(directory, attempt)?;
        let mut tree = spawn_child(&child_directory, ChildMode::CrashAfterHello)?;
        wait_for_marker(
            &child_directory,
            "transport.ready",
            Instant::now() + Duration::from_secs(3),
        )?;
        write_marker(&child_directory, "hello.request", b"hello")?;
        wait_for_marker(
            &child_directory,
            "hello.response",
            Instant::now() + Duration::from_secs(3),
        )?;
        if supervisor.observe_spawn_succeeded(generation_id) != Some(SupervisorState::Running) {
            return Err(format!("generation {attempt} did not enter Running"));
        }
        wait_for_marker(
            &child_directory,
            "crash.ready",
            Instant::now() + Duration::from_secs(3),
        )?;
        match tree
            .wait(Duration::from_secs(3))
            .map_err(|error| format!("crash root wait failed: {error}"))?
        {
            WaitOutcome::Exited(37) => {}
            outcome => return Err(format!("crash root had unexpected outcome: {outcome:?}")),
        }
        let failure_actions =
            supervisor.observe_generation_failed(generation_id, FailureReason::UnexpectedExit);
        if !matches!(
            failure_actions.as_slice(),
            [LifecycleAction::StopGeneration {
                generation_id: failed_id,
                reason: StopReason::Recovery,
            }] if *failed_id == generation_id
        ) {
            return Err(format!(
                "crash recovery actions were invalid: {failure_actions:?}"
            ));
        }
        tree.terminate_tree(96)
            .map_err(|error| format!("crash tree reclamation failed: {error}"))?;
        if !tree
            .verify_tree_exited(Duration::from_secs(5))
            .map_err(|error| format!("crash Job query failed: {error}"))?
        {
            return Err("crash Job retained active processes".to_string());
        }
        tree.release_exited_handles()
            .map_err(|error| format!("crash Job handle release failed: {error}"))?;
        let (token, delay) = take_schedule(supervisor.finalize_generation(generation_id).actions)?;
        if attempt < 3 {
            thread::sleep(delay);
            generation_id = take_spawn(supervisor.observe_restart_timer(token), attempt + 1)?;
        } else {
            if delay != Duration::from_secs(3) {
                return Err(format!("third restart delay was not 3 seconds: {delay:?}"));
            }
            final_token = Some(token);
        }
    }
    let token = final_token.ok_or_else(|| "third restart token was not recorded".to_string())?;
    write_marker(
        directory,
        "acceptance.restart_backoff",
        token.as_u64().to_string().as_bytes(),
    )?;
    wait_for_cancellation(cancellation, Instant::now() + Duration::from_secs(3))?;
    let actions = supervisor.submit(LifecycleIntent::AppShutdown);
    if actions != vec![LifecycleAction::CancelRestart { token }] {
        return Err(format!(
            "backoff shutdown did not cancel its timer: {actions:?}"
        ));
    }
    if !supervisor.observe_restart_timer(token).is_empty() {
        return Err("cancelled restart timer spawned a stale generation".to_string());
    }
    write_marker(directory, "acceptance.timer_cancelled", b"cancelled")?;
    write_marker(directory, "acceptance.cleaned", b"restart-backoff")
}

fn create_generation_directory(root: &Path, number: u64) -> Result<PathBuf, String> {
    let directory = root.join(format!("generation-{number}"));
    fs::create_dir(&directory)
        .map_err(|error| format!("failed to create generation directory: {error}"))?;
    Ok(directory)
}

fn spawn_child(directory: &Path, mode: ChildMode) -> Result<ManagedProcessTree, String> {
    let _environment_lock = CHILD_ENVIRONMENT_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let previous_directory = std::env::var_os(CHILD_DIRECTORY_ENV);
    let previous_mode = std::env::var_os(CHILD_MODE_ENV);
    std::env::set_var(CHILD_DIRECTORY_ENV, directory);
    std::env::set_var(CHILD_MODE_ENV, mode.as_str());
    let spawn_result = ManagedProcessTree::spawn(&ManagedProcessSpec::new(
        std::env::current_exe().map_err(|error| format!("current executable failed: {error}"))?,
    ));
    restore_environment(CHILD_DIRECTORY_ENV, previous_directory);
    restore_environment(CHILD_MODE_ENV, previous_mode);
    spawn_result.map_err(|error| format!("Fake Core child spawn failed: {error}"))
}

fn restore_environment(name: &str, previous: Option<OsString>) {
    if let Some(previous) = previous {
        std::env::set_var(name, previous);
    } else {
        std::env::remove_var(name);
    }
}

fn stop_child(
    tree: &mut ManagedProcessTree,
    directory: &Path,
    forced_exit_code: u32,
) -> Result<(), String> {
    let stop_started = Instant::now();
    write_marker(directory, "shutdown.request", b"shutdown")?;
    let acknowledged = wait_for_marker(
        directory,
        "shutdown.ack",
        stop_started + Duration::from_secs(3),
    )
    .is_ok();
    if !acknowledged {
        tree.terminate_tree(forced_exit_code)
            .map_err(|error| format!("Fake Core forced shutdown failed: {error}"))?;
    }
    let root_exit = tree
        .wait((stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now()))
        .map_err(|error| format!("Fake Core root wait failed: {error}"))?;
    match (acknowledged, root_exit) {
        (true, WaitOutcome::Exited(0)) => {}
        (false, WaitOutcome::Exited(code)) if code == forced_exit_code => {}
        outcome => {
            return Err(format!(
                "Fake Core shutdown outcome was invalid: {outcome:?}"
            ))
        }
    }
    if !tree
        .verify_tree_exited(
            (stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now()),
        )
        .map_err(|error| format!("Fake Core Job query failed: {error}"))?
    {
        return Err("Fake Core Job retained active processes".to_string());
    }
    tree.release_exited_handles()
        .map_err(|error| format!("Fake Core handle release failed: {error}"))
}

fn take_spawn(
    actions: Vec<LifecycleAction>,
    expected_number: u64,
) -> Result<crate::core_supervisor::GenerationId, String> {
    match actions.as_slice() {
        [LifecycleAction::SpawnGeneration {
            generation_id,
            generation_number,
            ..
        }] if *generation_number == expected_number => Ok(*generation_id),
        _ => Err(format!(
            "expected generation {expected_number} spawn: {actions:?}"
        )),
    }
}

fn take_schedule(
    actions: Vec<LifecycleAction>,
) -> Result<(crate::core_supervisor::RestartToken, Duration), String> {
    match actions.as_slice() {
        [LifecycleAction::ScheduleRestart { token, delay }] => Ok((*token, *delay)),
        _ => Err(format!("expected one restart schedule: {actions:?}")),
    }
}

fn write_marker(directory: &Path, name: &str, contents: &[u8]) -> Result<(), String> {
    fs::write(directory.join(name), contents)
        .map_err(|error| format!("failed to write marker {name}: {error}"))
}

fn wait_for_marker(directory: &Path, name: &str, deadline: Instant) -> Result<(), String> {
    let marker = directory.join(name);
    loop {
        if Instant::now() >= deadline {
            return Err(format!("marker {name} exceeded its deadline"));
        }
        if marker.exists() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn wait_for_cancellation(cancellation: &AtomicBool, deadline: Instant) -> Result<(), String> {
    loop {
        if cancellation.load(Ordering::Acquire) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("Tauri window did not request app shutdown before deadline".to_string());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
mod tests {
    use std::{fs, process, thread, time::Duration};

    use super::{
        run_fake_core_child, validate_acceptance_path, AcceptanceMode, ChildMode,
        ACCEPTANCE_DIRECTORY_PREFIX,
    };

    #[test]
    fn acceptance_modes_are_explicit_and_fail_closed() {
        assert_eq!(
            AcceptanceMode::parse("pending-hello"),
            Ok(AcceptanceMode::PendingHello)
        );
        assert_eq!(
            AcceptanceMode::parse("restart-backoff"),
            Ok(AcceptanceMode::RestartBackoff)
        );
        assert!(AcceptanceMode::parse("production").is_err());
    }

    #[test]
    fn child_modes_are_explicit_and_fail_closed() {
        assert_eq!(
            ChildMode::parse("pending-hello"),
            Ok(ChildMode::PendingHello)
        );
        assert_eq!(
            ChildMode::parse("crash-after-hello"),
            Ok(ChildMode::CrashAfterHello)
        );
        assert!(ChildMode::parse("python-core").is_err());
    }

    #[test]
    fn acceptance_paths_are_restricted_to_the_named_system_temp_scope() {
        let accepted = std::env::temp_dir().join(format!(
            "{ACCEPTANCE_DIRECTORY_PREFIX}{}-path-test",
            process::id()
        ));
        fs::create_dir_all(&accepted).expect("accepted path fixture should create");
        assert_eq!(
            validate_acceptance_path(accepted.clone()).expect("named temp path should be accepted"),
            fs::canonicalize(&accepted).expect("accepted path should resolve")
        );
        fs::remove_dir_all(&accepted).expect("accepted path fixture should remove");

        let rejected = std::env::temp_dir().join(format!("unrelated-{}", process::id()));
        fs::create_dir_all(&rejected).expect("rejected path fixture should create");
        assert!(validate_acceptance_path(rejected.clone()).is_err());
        fs::remove_dir_all(&rejected).expect("rejected path fixture should remove");
    }

    #[test]
    fn pending_hello_child_never_responds_and_acknowledges_priority_shutdown() {
        let directory = std::env::temp_dir().join(format!(
            "{ACCEPTANCE_DIRECTORY_PREFIX}{}-child-test",
            process::id()
        ));
        fs::create_dir_all(&directory).expect("child fixture should create");
        let child_directory = directory.clone();
        let child =
            thread::spawn(move || run_fake_core_child(&child_directory, ChildMode::PendingHello));
        wait_until(|| directory.join("transport.ready").exists());
        fs::write(directory.join("hello.request"), b"hello").expect("hello request should write");
        wait_until(|| directory.join("hello.pending").exists());
        assert!(!directory.join("hello.response").exists());
        fs::write(directory.join("shutdown.request"), b"shutdown")
            .expect("shutdown request should write");
        child
            .join()
            .expect("pending hello child should join")
            .expect("pending hello child should stop cleanly");
        assert!(directory.join("shutdown.ack").exists());
        assert!(!directory.join("hello.response").exists());
        fs::remove_dir_all(&directory).expect("child fixture should remove");
    }

    fn wait_until(predicate: impl Fn() -> bool) {
        for _ in 0..300 {
            if predicate() {
                return;
            }
            thread::sleep(Duration::from_millis(10));
        }
        panic!("test marker deadline exceeded");
    }
}
