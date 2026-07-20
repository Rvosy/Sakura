#[cfg(all(test, windows))]
use std::{
    fs,
    path::{Path, PathBuf},
    process,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

#[cfg(all(test, windows))]
const FIXTURE_DIRECTORY_ENV: &str = "SAKURA_WP_1B_03_FIXTURE_DIRECTORY";
#[cfg(all(test, windows))]
static FIXTURE_ENV_LOCK: Mutex<()> = Mutex::new(());
#[cfg(all(test, windows))]
static NEXT_FIXTURE_ID: AtomicU64 = AtomicU64::new(1);

#[cfg(all(test, windows))]
use crate::{
    core_supervisor::{CoreSupervisor, LifecycleAction, LifecycleIntent, SupervisorState},
    managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
};

#[cfg(all(test, windows))]
#[derive(Debug, Clone, Copy)]
enum FakeCoreMode {
    Normal,
    IgnoreShutdown,
    DelayedHello,
}

#[cfg(all(test, windows))]
#[derive(Debug)]
struct FakeCoreOutcome {
    hello_completed: bool,
    protocol_shutdown_acknowledged: bool,
    forced_tree_termination: bool,
    final_state: SupervisorState,
    finalize_count: usize,
    tree_exited: bool,
    fixture_directory_removed: bool,
}

#[cfg(all(test, windows))]
#[derive(Debug)]
struct DelayedHelloShutdownOutcome {
    app_shutdown_dispatch_elapsed: Duration,
    hello_pending_at_shutdown: bool,
    hello_never_completed: bool,
    hello_worker_cancelled: bool,
    forced_tree_termination: bool,
    final_state: SupervisorState,
    tree_exited: bool,
    fixture_directory_removed: bool,
}

#[cfg(all(test, windows))]
struct FixtureDirectory(PathBuf);

#[cfg(all(test, windows))]
impl FixtureDirectory {
    fn new(path: PathBuf) -> Self {
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }

    fn remove(&self) -> bool {
        if self.0.exists() {
            fs::remove_dir_all(&self.0).is_ok() && !self.0.exists()
        } else {
            true
        }
    }
}

#[cfg(all(test, windows))]
impl Drop for FixtureDirectory {
    fn drop(&mut self) {
        if self.0.exists() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

#[cfg(all(test, windows))]
fn next_fake_core_directory() -> PathBuf {
    let fixture_id = NEXT_FIXTURE_ID.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "sakura-runtime-v2-wp-1b-03-{}-{fixture_id}",
        process::id()
    ))
}

#[cfg(all(test, windows))]
fn fixture_directory_from_environment() -> PathBuf {
    std::env::var_os(FIXTURE_DIRECTORY_ENV)
        .map(PathBuf::from)
        .expect("Fake Core fixture directory must be inherited from its test parent")
}

#[cfg(all(test, windows))]
fn spawn_fake_core(mode: FakeCoreMode) -> (ManagedProcessTree, FixtureDirectory) {
    let _environment_lock = FIXTURE_ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let directory = next_fake_core_directory();
    if directory.exists() {
        fs::remove_dir_all(&directory).expect("unique stale Fake Core directory should remove");
    }
    std::env::set_var(FIXTURE_DIRECTORY_ENV, &directory);
    let spawn_result = ManagedProcessTree::spawn(&fake_core_spec(mode));
    std::env::remove_var(FIXTURE_DIRECTORY_ENV);
    let tree = spawn_result.expect("Fake Core should spawn inside a managed Windows Job");
    (tree, FixtureDirectory::new(directory))
}

#[cfg(all(test, windows))]
fn fake_core_spec(mode: FakeCoreMode) -> ManagedProcessSpec {
    let fixture = match mode {
        FakeCoreMode::Normal => "fixture_fake_core_normal",
        FakeCoreMode::IgnoreShutdown => "fixture_fake_core_ignores_shutdown",
        FakeCoreMode::DelayedHello => "fixture_fake_core_delays_hello",
    };
    let mut spec = ManagedProcessSpec::new(
        std::env::current_exe().expect("current Rust test executable should resolve"),
    );
    spec.arg("--ignored")
        .arg("--exact")
        .arg(format!("fake_core_runtime::tests::{fixture}"))
        .arg("--nocapture");
    spec
}

#[cfg(all(test, windows))]
fn wait_for_marker(directory: &Path, name: &str, deadline: Instant) -> bool {
    let marker = directory.join(name);
    loop {
        if Instant::now() >= deadline {
            return false;
        }
        if marker.exists() {
            return true;
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(all(test, windows))]
fn run_fake_core_scenario(mode: FakeCoreMode) -> FakeCoreOutcome {
    let mut supervisor = CoreSupervisor::new(0x1b03_1b03_1b03_1b03);
    let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
        [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
        actions => panic!("Fake Core should begin with one spawn action: {actions:?}"),
    };
    let (mut tree, fixture_directory) = spawn_fake_core(mode);

    assert!(
        wait_for_marker(
            fixture_directory.path(),
            "transport.ready",
            Instant::now() + Duration::from_secs(3),
        ),
        "Fake Core transport should become ready before deadline"
    );
    fs::write(fixture_directory.path().join("hello.request"), b"hello")
        .expect("hello request marker should write in isolated fixture directory");
    let hello_deadline = Instant::now() + Duration::from_secs(3);
    if matches!(mode, FakeCoreMode::DelayedHello) {
        assert!(wait_for_marker(
            fixture_directory.path(),
            "hello.pending",
            hello_deadline,
        ));
        thread::sleep(Duration::from_millis(250));
        fs::write(fixture_directory.path().join("hello.release"), b"release")
            .expect("delayed hello release marker should write");
    }
    let hello_completed =
        wait_for_marker(fixture_directory.path(), "hello.response", hello_deadline);
    assert!(hello_completed, "Fake Core hello should meet its deadline");
    assert_eq!(
        supervisor.observe_spawn_succeeded(generation_id),
        Some(SupervisorState::Running)
    );

    assert!(matches!(
        supervisor.submit(LifecycleIntent::Stop).as_slice(),
        [LifecycleAction::StopGeneration {
            generation_id: stopped_id,
            ..
        }] if *stopped_id == generation_id
    ));
    let stop_started = Instant::now();
    fs::write(
        fixture_directory.path().join("shutdown.request"),
        b"shutdown",
    )
    .expect("shutdown request marker should write in isolated fixture directory");
    let protocol_shutdown_acknowledged = wait_for_marker(
        fixture_directory.path(),
        "shutdown.ack",
        stop_started + Duration::from_secs(3),
    );
    let forced_tree_termination = if protocol_shutdown_acknowledged {
        false
    } else {
        tree.terminate_tree(92)
            .expect("shutdown deadline should force the Fake Core Job tree");
        true
    };

    let remaining =
        (stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now());
    let root_exit_code = match tree
        .wait(remaining)
        .expect("Fake Core root exit should be observable")
    {
        WaitOutcome::Exited(exit_code) => exit_code,
        WaitOutcome::TimedOut => panic!("Fake Core root exceeded the full stop deadline"),
    };
    if protocol_shutdown_acknowledged {
        assert_eq!(root_exit_code, 0, "acknowledged shutdown must exit cleanly");
    } else {
        assert_eq!(
            root_exit_code, 92,
            "forced shutdown must preserve its reason code"
        );
    }
    let tree_exited = tree
        .verify_tree_exited(
            (stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now()),
        )
        .expect("Fake Core Job ActiveProcesses should query");
    assert!(
        tree_exited,
        "Fake Core tree should exit before full deadline"
    );
    tree.release_exited_handles()
        .expect("Fake Core stopped handles should release");

    let first_finalize = supervisor.finalize_generation(generation_id);
    let duplicate_finalize = supervisor.finalize_generation(generation_id);
    let finalize_count =
        usize::from(first_finalize.applied) + usize::from(duplicate_finalize.applied);
    assert!(first_finalize.actions.is_empty());
    assert!(duplicate_finalize.actions.is_empty());
    let fixture_directory_removed = fixture_directory.remove();

    FakeCoreOutcome {
        hello_completed,
        protocol_shutdown_acknowledged,
        forced_tree_termination,
        final_state: supervisor.snapshot().state,
        finalize_count,
        tree_exited,
        fixture_directory_removed,
    }
}

#[cfg(all(test, windows))]
fn run_delayed_hello_shutdown_scenario() -> DelayedHelloShutdownOutcome {
    let mut supervisor = CoreSupervisor::new(0x1b03_d311_a7ed_0001);
    let (generation_id, cancellation) = match supervisor.submit(LifecycleIntent::Start).as_slice() {
        [LifecycleAction::SpawnGeneration {
            generation_id,
            cancellation,
            ..
        }] => (*generation_id, cancellation.clone()),
        actions => panic!("delayed Fake Core should start once: {actions:?}"),
    };
    let (mut tree, fixture_directory) = spawn_fake_core(FakeCoreMode::DelayedHello);
    assert!(wait_for_marker(
        fixture_directory.path(),
        "transport.ready",
        Instant::now() + Duration::from_secs(3),
    ));
    fs::write(fixture_directory.path().join("hello.request"), b"hello")
        .expect("delayed hello request marker should write");
    let hello_deadline = Instant::now() + Duration::from_secs(3);
    assert!(wait_for_marker(
        fixture_directory.path(),
        "hello.pending",
        hello_deadline,
    ));
    let hello_pending_at_shutdown = !fixture_directory.path().join("hello.response").exists();

    let worker_directory = fixture_directory.path().to_path_buf();
    let hello_worker = thread::spawn(move || loop {
        if cancellation.is_cancelled() {
            return true;
        }
        if worker_directory.join("hello.response").exists() {
            return false;
        }
        if Instant::now() >= hello_deadline {
            return false;
        }
        thread::sleep(Duration::from_millis(10));
    });

    let dispatch_started = Instant::now();
    assert!(matches!(
        supervisor.submit(LifecycleIntent::AppShutdown).as_slice(),
        [LifecycleAction::StopGeneration {
            generation_id: stopped_id,
            ..
        }] if *stopped_id == generation_id
    ));
    let app_shutdown_dispatch_elapsed = dispatch_started.elapsed();
    let hello_worker_cancelled = hello_worker
        .join()
        .expect("hello worker should join after generation cancellation");

    let stop_started = Instant::now();
    fs::write(
        fixture_directory.path().join("shutdown.request"),
        b"shutdown",
    )
    .expect("delayed Fake Core shutdown request marker should write");
    let acknowledged = wait_for_marker(
        fixture_directory.path(),
        "shutdown.ack",
        stop_started + Duration::from_secs(3),
    );
    let hello_never_completed = !fixture_directory.path().join("hello.response").exists();
    let forced_tree_termination = if acknowledged {
        false
    } else {
        tree.terminate_tree(93)
            .expect("delayed hello shutdown timeout should force the Job tree");
        true
    };
    let root_exit_code = match tree
        .wait((stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now()))
        .expect("delayed Fake Core exit should be observable")
    {
        WaitOutcome::Exited(exit_code) => exit_code,
        WaitOutcome::TimedOut => panic!("delayed Fake Core exceeded the full stop deadline"),
    };
    if acknowledged {
        assert_eq!(root_exit_code, 0, "priority shutdown must exit cleanly");
    } else {
        assert_eq!(
            root_exit_code, 93,
            "forced delayed shutdown reason must survive"
        );
    }
    let tree_exited = tree
        .verify_tree_exited(
            (stop_started + Duration::from_secs(5)).saturating_duration_since(Instant::now()),
        )
        .expect("delayed Fake Core Job should query");
    assert!(tree_exited);
    tree.release_exited_handles()
        .expect("delayed Fake Core handles should release");
    assert!(supervisor
        .observe_generation_stopped(generation_id)
        .is_empty());
    let fixture_directory_removed = fixture_directory.remove();

    DelayedHelloShutdownOutcome {
        app_shutdown_dispatch_elapsed,
        hello_pending_at_shutdown,
        hello_never_completed,
        hello_worker_cancelled,
        forced_tree_termination,
        final_state: supervisor.snapshot().state,
        tree_exited,
        fixture_directory_removed,
    }
}

#[cfg(all(test, windows))]
mod tests {
    use std::{
        fs, thread,
        time::{Duration, Instant},
    };

    use super::{
        next_fake_core_directory, run_delayed_hello_shutdown_scenario, run_fake_core_scenario,
        wait_for_marker, FakeCoreMode, FixtureDirectory,
    };
    use crate::core_supervisor::SupervisorState;

    #[test]
    fn normal_fake_core_hello_and_protocol_shutdown_finalize_once() {
        let outcome = run_fake_core_scenario(FakeCoreMode::Normal);

        assert!(outcome.hello_completed);
        assert!(outcome.protocol_shutdown_acknowledged);
        assert!(!outcome.forced_tree_termination);
        assert_eq!(outcome.final_state, SupervisorState::Stopped);
        assert_eq!(outcome.finalize_count, 1);
        assert!(outcome.tree_exited);
        assert!(outcome.fixture_directory_removed);
    }

    #[test]
    fn fake_core_ignoring_shutdown_is_forced_and_the_tree_is_reclaimed() {
        let outcome = run_fake_core_scenario(FakeCoreMode::IgnoreShutdown);

        assert!(outcome.hello_completed);
        assert!(!outcome.protocol_shutdown_acknowledged);
        assert!(outcome.forced_tree_termination);
        assert_eq!(outcome.final_state, SupervisorState::Stopped);
        assert_eq!(outcome.finalize_count, 1);
        assert!(outcome.tree_exited);
        assert!(outcome.fixture_directory_removed);
    }

    #[test]
    fn delayed_hello_wait_does_not_block_app_shutdown_or_tree_reclamation() {
        let outcome = run_delayed_hello_shutdown_scenario();

        assert!(outcome.app_shutdown_dispatch_elapsed < Duration::from_millis(100));
        assert!(outcome.hello_pending_at_shutdown);
        assert!(outcome.hello_never_completed);
        assert!(outcome.hello_worker_cancelled);
        assert!(!outcome.forced_tree_termination);
        assert_eq!(outcome.final_state, SupervisorState::Stopped);
        assert!(outcome.tree_exited);
        assert!(outcome.fixture_directory_removed);
    }

    #[test]
    fn delayed_hello_can_complete_before_deadline_and_then_shutdown_normally() {
        let started = Instant::now();
        let outcome = run_fake_core_scenario(FakeCoreMode::DelayedHello);

        assert!(started.elapsed() >= Duration::from_millis(200));
        assert!(outcome.hello_completed);
        assert!(outcome.protocol_shutdown_acknowledged);
        assert!(!outcome.forced_tree_termination);
        assert_eq!(outcome.final_state, SupervisorState::Stopped);
        assert_eq!(outcome.finalize_count, 1);
        assert!(outcome.tree_exited);
        assert!(outcome.fixture_directory_removed);
    }

    #[test]
    fn marker_observed_at_an_expired_deadline_is_rejected() {
        let fixture_directory = FixtureDirectory::new(next_fake_core_directory());
        fs::create_dir_all(fixture_directory.path())
            .expect("deadline boundary fixture directory should create");
        fs::write(fixture_directory.path().join("late.marker"), b"late")
            .expect("deadline boundary marker should write");

        assert!(!wait_for_marker(
            fixture_directory.path(),
            "late.marker",
            Instant::now(),
        ));
        assert!(fixture_directory.remove());
    }

    #[test]
    #[ignore = "test-process fixture; launched by Fake Core lifecycle tests"]
    fn fixture_fake_core_normal() {
        let directory = super::fixture_directory_from_environment();
        if directory.exists() {
            fs::remove_dir_all(&directory).expect("stale isolated fixture directory should remove");
        }
        fs::create_dir_all(&directory).expect("isolated Fake Core directory should create");
        fs::write(directory.join("transport.ready"), b"ready")
            .expect("transport ready marker should write");

        let fixture_deadline = Instant::now() + Duration::from_secs(15);
        while !directory.join("hello.request").exists() {
            assert!(Instant::now() < fixture_deadline, "hello request deadline");
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("hello.response"), b"hello")
            .expect("hello response marker should write");
        while !directory.join("shutdown.request").exists() {
            assert!(
                Instant::now() < fixture_deadline,
                "shutdown request deadline"
            );
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("shutdown.ack"), b"ack")
            .expect("shutdown ack marker should write");
    }

    #[test]
    #[ignore = "test-process fixture; launched by Fake Core lifecycle tests"]
    fn fixture_fake_core_ignores_shutdown() {
        let directory = super::fixture_directory_from_environment();
        if directory.exists() {
            fs::remove_dir_all(&directory).expect("stale isolated fixture directory should remove");
        }
        fs::create_dir_all(&directory).expect("isolated Fake Core directory should create");
        fs::write(directory.join("transport.ready"), b"ready")
            .expect("transport ready marker should write");

        let fixture_deadline = Instant::now() + Duration::from_secs(15);
        while !directory.join("hello.request").exists() {
            assert!(Instant::now() < fixture_deadline, "hello request deadline");
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("hello.response"), b"hello")
            .expect("hello response marker should write");
        while !directory.join("shutdown.request").exists() {
            assert!(
                Instant::now() < fixture_deadline,
                "shutdown request deadline"
            );
            thread::sleep(Duration::from_millis(10));
        }
        while Instant::now() < fixture_deadline {
            thread::sleep(Duration::from_millis(50));
        }
        panic!("ignoring shutdown fixture reached its independent deadline");
    }

    #[test]
    #[ignore = "test-process fixture; launched by Fake Core lifecycle tests"]
    fn fixture_fake_core_delays_hello() {
        let directory = super::fixture_directory_from_environment();
        if directory.exists() {
            fs::remove_dir_all(&directory).expect("stale isolated fixture directory should remove");
        }
        fs::create_dir_all(&directory).expect("isolated Fake Core directory should create");
        fs::write(directory.join("transport.ready"), b"ready")
            .expect("transport ready marker should write");

        let fixture_deadline = Instant::now() + Duration::from_secs(15);
        while !directory.join("hello.request").exists() {
            assert!(Instant::now() < fixture_deadline, "hello request deadline");
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("hello.pending"), b"pending")
            .expect("delayed hello pending marker should write");
        while !directory.join("hello.release").exists() {
            if directory.join("shutdown.request").exists() {
                fs::write(directory.join("shutdown.ack"), b"ack")
                    .expect("shutdown ack should bypass delayed hello");
                return;
            }
            assert!(
                Instant::now() < fixture_deadline,
                "delayed hello release deadline"
            );
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("hello.response"), b"hello")
            .expect("delayed hello response should write");
        while !directory.join("shutdown.request").exists() {
            assert!(
                Instant::now() < fixture_deadline,
                "delayed hello fixture deadline"
            );
            thread::sleep(Duration::from_millis(10));
        }
        fs::write(directory.join("shutdown.ack"), b"ack")
            .expect("shutdown ack should write after delayed hello");
    }
}
