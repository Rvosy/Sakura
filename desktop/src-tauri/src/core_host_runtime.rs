use std::{
    fs::File,
    io::{Read, Write},
    sync::mpsc,
    thread,
    time::Duration,
};

#[cfg(test)]
use std::path::Path;

use serde_json::{json, Value};

use crate::{
    core_host_protocol::{read_frame, write_frame, PROTOCOL_MAJOR, PROTOCOL_MINOR},
    platform::{
        ManagedProcessRequest, ManagedProcessTree, ManagedProcessTreeBackend,
        NativeManagedProcessTreeBackend, ProcessExitStatus, ProcessStdio, ProcessWaitOutcome,
        RuntimeLayout,
    },
};

const CONTROL_PRIORITY: &str = "control";
const DEADLINE_EXIT_CODE: u32 = 93;
const SNAPSHOT_READINESS: [&str; 6] = [
    "transport_ready",
    "initializing",
    "setup_required",
    "ready",
    "degraded",
    "failed",
];

#[derive(Debug, Clone, PartialEq)]
pub struct CoreSnapshotCache {
    generation_id: String,
    snapshot: Option<Value>,
}

impl CoreSnapshotCache {
    pub fn new(generation_id: &str) -> Result<Self, String> {
        if generation_id.trim().is_empty() {
            return Err("Snapshot generation ID must not be empty".to_string());
        }
        Ok(Self {
            generation_id: generation_id.to_string(),
            snapshot: None,
        })
    }

    pub fn begin_generation(&mut self, generation_id: &str) -> Result<(), String> {
        if generation_id.trim().is_empty() {
            return Err("Snapshot generation ID must not be empty".to_string());
        }
        self.generation_id.clear();
        self.generation_id.push_str(generation_id);
        self.snapshot = None;
        Ok(())
    }

    pub fn store_python_snapshot(&mut self, snapshot: &Value) -> Result<(), String> {
        let object = snapshot
            .as_object()
            .ok_or_else(|| "Core Snapshot must be an object".to_string())?;
        if object.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
            return Err("Core Snapshot schemaVersion is unsupported".to_string());
        }
        if object.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str()) {
            return Err("Core Snapshot belongs to another generation".to_string());
        }
        if object
            .get("generationNumber")
            .and_then(Value::as_u64)
            .is_none_or(|number| number == 0)
            || object.get("revision").and_then(Value::as_u64).is_none()
            || object
                .get("coreConfigRevision")
                .and_then(Value::as_u64)
                .is_none()
            || !object.get("components").is_some_and(Value::is_object)
        {
            return Err("Core Snapshot counters or components are invalid".to_string());
        }
        let readiness = object
            .get("readiness")
            .and_then(Value::as_str)
            .ok_or_else(|| "Core Snapshot readiness is invalid".to_string())?;
        if !SNAPSHOT_READINESS.contains(&readiness) {
            return Err("Core Snapshot readiness is unsupported".to_string());
        }
        let capabilities = object
            .get("capabilities")
            .and_then(Value::as_array)
            .ok_or_else(|| "Core Snapshot capabilities are invalid".to_string())?;
        if capabilities.iter().any(|value| {
            value
                .as_str()
                .is_none_or(|capability| capability.trim().is_empty())
        }) {
            return Err("Core Snapshot capability is invalid".to_string());
        }
        for key in ["currentCharacterSummary", "activeInteractionSummary"] {
            if object
                .get(key)
                .is_none_or(|value| !(value.is_null() || value.is_object()))
            {
                return Err(format!("Core Snapshot {key} is invalid"));
            }
        }
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }

    pub fn current(&self) -> Option<&Value> {
        self.snapshot.as_ref()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct CoreHostExit {
    pub root_exit_code: u32,
    pub tree_empty: bool,
    pub forced: bool,
    pub stderr: String,
}

pub struct CoreHostRuntime {
    tree: Box<dyn ManagedProcessTree>,
    stdin: Option<File>,
    stdout: Option<File>,
    stderr: Option<File>,
    generation_id: String,
    deadline_forced: bool,
    snapshot_cache: CoreSnapshotCache,
}

impl std::fmt::Debug for CoreHostRuntime {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CoreHostRuntime")
            .field("root_pid", &self.tree.root_pid())
            .field("generation_id", &self.generation_id)
            .field("deadline_forced", &self.deadline_forced)
            .field("snapshot_cache", &self.snapshot_cache)
            .finish_non_exhaustive()
    }
}

impl CoreHostRuntime {
    pub fn launch(layout: &RuntimeLayout, generation_id: &str) -> Result<Self, String> {
        Self::launch_with_backend(
            &NativeManagedProcessTreeBackend,
            ManagedProcessRequest {
                program: layout.python_executable.clone(),
                args: vec![
                    "-m".into(),
                    layout.core_module.clone().into(),
                    "--generation-id".into(),
                    generation_id.into(),
                    "--generation-number".into(),
                    "1".into(),
                ],
                current_directory: Some(layout.application_root.clone()),
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            },
            generation_id,
        )
    }

    fn launch_with_backend(
        backend: &dyn ManagedProcessTreeBackend,
        request: ManagedProcessRequest,
        generation_id: &str,
    ) -> Result<Self, String> {
        if generation_id.trim().is_empty() {
            return Err("Core Host generation ID must not be empty".to_string());
        }
        let spawned = backend
            .spawn(&request)
            .map_err(|error| format!("Core Host managed spawn failed: {error}"))?;
        let pipes = spawned
            .pipes
            .ok_or_else(|| "Core Host managed spawn returned no pipes".to_string())?;
        Ok(Self {
            tree: spawned.tree,
            stdin: Some(pipes.stdin),
            stdout: Some(pipes.stdout),
            stderr: Some(pipes.stderr),
            generation_id: generation_id.to_string(),
            deadline_forced: false,
            snapshot_cache: CoreSnapshotCache::new(generation_id)?,
        })
    }

    #[cfg(test)]
    fn launch_script_for_test(
        python: &Path,
        repo_root: &Path,
        script: &Path,
        generation_id: &str,
    ) -> Result<Self, String> {
        Self::launch_with_backend(
            &NativeManagedProcessTreeBackend,
            ManagedProcessRequest {
                program: python.to_path_buf(),
                args: vec![script.as_os_str().to_owned()],
                current_directory: Some(repo_root.to_path_buf()),
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            },
            generation_id,
        )
    }

    pub fn pid(&self) -> u32 {
        self.tree.root_pid()
    }

    pub fn request(
        &mut self,
        request_id: &str,
        name: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        self.request_with_payload(request_id, name, json!({}), deadline)
    }

    pub fn request_with_payload(
        &mut self,
        request_id: &str,
        name: &str,
        payload: Value,
        deadline: Duration,
    ) -> Result<Value, String> {
        if request_id.trim().is_empty() || name.trim().is_empty() || deadline.is_zero() {
            return Err("Core Host control request is invalid".to_string());
        }
        if !payload.is_object() {
            return Err("Core Host control payload must be an object".to_string());
        }
        let request = json!({
            "protocolMajor": PROTOCOL_MAJOR,
            "protocolMinor": PROTOCOL_MINOR,
            "kind": "request",
            "generationId": self.generation_id,
            "id": request_id,
            "name": name,
            "payload": payload,
            "deadlineMs": deadline.as_millis().min(u64::MAX as u128) as u64,
            "priority": CONTROL_PRIORITY,
        });
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "Core Host stdin is closed".to_string())?;
        write_frame(stdin, &request).map_err(|error| error.to_string())?;
        stdin
            .flush()
            .map_err(|error| format!("Core Host stdin flush failed: {error}"))?;

        let response = self.read_response(deadline)?;
        if response.get("generationId").and_then(Value::as_str) != Some(self.generation_id.as_str())
            || response.get("id").and_then(Value::as_str) != Some(request_id)
            || response.get("name").and_then(Value::as_str) != Some(name)
        {
            return Err("Core Host response identity did not match its request".to_string());
        }
        Ok(response)
    }

    pub fn refresh_snapshot(
        &mut self,
        request_id: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        let response = self.request(request_id, "core.snapshot", deadline)?;
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err("Core Host rejected core.snapshot".to_string());
        }
        let snapshot = response
            .get("payload")
            .ok_or_else(|| "Core Host snapshot response has no payload".to_string())?
            .clone();
        self.snapshot_cache.store_python_snapshot(&snapshot)?;
        Ok(snapshot)
    }

    pub fn cached_snapshot(&self) -> Option<&Value> {
        self.snapshot_cache.current()
    }

    pub fn shutdown(
        mut self,
        protocol_deadline: Duration,
        stop_deadline: Duration,
    ) -> Result<CoreHostExit, String> {
        let response = match self.request("shutdown", "system.shutdown", protocol_deadline) {
            Ok(response) => response,
            Err(error) => {
                self.stdin.take();
                let exit = self.finish_exit(stop_deadline)?;
                if exit.forced {
                    return Ok(exit);
                }
                return Err(format!(
                    "Core Host shutdown response failed ({error}); cleanup result: {exit:?}"
                ));
            }
        };
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err("Core Host rejected system.shutdown".to_string());
        }
        self.stdin.take();
        self.finish_exit(stop_deadline)
    }

    pub fn close_stdin_and_wait(mut self, stop_deadline: Duration) -> Result<CoreHostExit, String> {
        self.stdin.take();
        self.finish_exit(stop_deadline)
    }

    fn read_response(&mut self, deadline: Duration) -> Result<Value, String> {
        let mut stdout = self
            .stdout
            .take()
            .ok_or_else(|| "Core Host stdout is unavailable".to_string())?;
        let (sender, receiver) = mpsc::sync_channel(1);
        let reader = thread::spawn(move || {
            let result = read_frame(&mut stdout);
            let _ = sender.send((stdout, result));
        });

        let received = match receiver.recv_timeout(deadline) {
            Ok(received) => received,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.tree
                    .terminate_tree(DEADLINE_EXIT_CODE)
                    .map_err(|error| {
                        format!("Core Host response timeout cleanup failed: {error}")
                    })?;
                self.deadline_forced = true;
                let received = receiver.recv_timeout(Duration::from_secs(5)).map_err(|_| {
                    "Core Host stdout reader did not stop after timeout".to_string()
                })?;
                reader
                    .join()
                    .map_err(|_| "Core Host stdout reader panicked".to_string())?;
                self.stdout = Some(received.0);
                return Err("Core Host response exceeded its deadline".to_string());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                reader
                    .join()
                    .map_err(|_| "Core Host stdout reader panicked".to_string())?;
                return Err("Core Host stdout reader disconnected".to_string());
            }
        };
        reader
            .join()
            .map_err(|_| "Core Host stdout reader panicked".to_string())?;
        self.stdout = Some(received.0);
        received
            .1
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "Core Host stdout reached EOF before its response".to_string())
    }

    fn finish_exit(mut self, stop_deadline: Duration) -> Result<CoreHostExit, String> {
        let mut forced = self.deadline_forced;
        let root_exit_code = match self
            .tree
            .wait_root(stop_deadline)
            .map_err(|error| format!("Core Host root wait failed: {error}"))?
        {
            ProcessWaitOutcome::Exited(status) => process_exit_code(status),
            ProcessWaitOutcome::TimedOut => {
                forced = true;
                self.tree
                    .terminate_tree(DEADLINE_EXIT_CODE)
                    .map_err(|error| format!("Core Host forced cleanup failed: {error}"))?;
                match self
                    .tree
                    .wait_root(Duration::from_secs(5))
                    .map_err(|error| format!("Core Host forced root wait failed: {error}"))?
                {
                    ProcessWaitOutcome::Exited(status) => process_exit_code(status),
                    ProcessWaitOutcome::TimedOut => {
                        return Err("Core Host root survived forced cleanup".to_string())
                    }
                }
            }
        };
        let tree_empty = self
            .tree
            .wait_tree_exited(Duration::from_secs(5))
            .map_err(|error| format!("Core Host Job verification failed: {error}"))?;
        if !tree_empty {
            return Err("Core Host Job retained active processes".to_string());
        }
        let mut trailing_stdout = Vec::new();
        if let Some(mut pipe) = self.stdout.take() {
            pipe.read_to_end(&mut trailing_stdout)
                .map_err(|error| format!("Core Host stdout drain failed: {error}"))?;
        }
        let mut stderr_bytes = Vec::new();
        if let Some(mut pipe) = self.stderr.take() {
            pipe.read_to_end(&mut stderr_bytes)
                .map_err(|error| format!("Core Host stderr read failed: {error}"))?;
        }
        self.tree
            .release_exited()
            .map_err(|error| format!("Core Host handle release failed: {error}"))?;
        if !trailing_stdout.is_empty() {
            return Err("Core Host wrote unexpected trailing stdout bytes".to_string());
        }
        Ok(CoreHostExit {
            root_exit_code,
            tree_empty,
            forced,
            stderr: String::from_utf8_lossy(&stderr_bytes).into_owned(),
        })
    }
}

fn process_exit_code(status: ProcessExitStatus) -> u32 {
    match status {
        ProcessExitStatus::Code(code) => u32::try_from(code).unwrap_or(u32::MAX),
        ProcessExitStatus::Signal(signal) => {
            128_u32.saturating_add(u32::try_from(signal).unwrap_or_default())
        }
        ProcessExitStatus::Unknown => u32::MAX,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        path::PathBuf,
        sync::{mpsc, Mutex, OnceLock},
        thread,
        time::Duration,
    };

    use serde_json::json;

    use crate::{
        core_host_protocol::read_frame,
        managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
        platform::{
            FilesystemRuntimeLocator, InstanceLockAcquire, InstanceLockBackend,
            RuntimeLocationRequest, RuntimeLocator, RuntimeMode, SHARED_INSTANCE_ID,
        },
        shared_instance::NativeInstanceLockBackend,
    };

    use super::{CoreHostRuntime, CoreSnapshotCache};

    const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";

    static LIFECYCLE_TEST_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn lifecycle_test_lock() -> std::sync::MutexGuard<'static, ()> {
        LIFECYCLE_TEST_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .expect("Core lifecycle test lock should not be poisoned")
    }

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
    }

    fn development_layout() -> crate::platform::RuntimeLayout {
        let root = repo_root();
        FilesystemRuntimeLocator
            .locate(&RuntimeLocationRequest {
                mode: RuntimeMode::ExplicitDevelopment,
                target: crate::platform::current_platform_target()
                    .expect("tests run on a formal Runtime v2 target"),
                executable_directory: std::env::current_exe()
                    .unwrap()
                    .parent()
                    .unwrap()
                    .to_path_buf(),
                resource_directory: root.clone(),
                explicit_development_root: Some(root),
            })
            .expect("repository Runtime should resolve explicitly")
    }

    #[test]
    fn managed_real_python_host_answers_control_and_releases_its_job_and_pipes() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host = CoreHostRuntime::launch(&layout, GENERATION_ID)
            .expect("real Core Host should launch in a managed Job");
        assert!(host.pid() > 0);

        let hello = host
            .request("hello", "system.hello", Duration::from_secs(3))
            .expect("hello should respond");
        assert_eq!(hello["ok"], true);
        assert_eq!(hello["payload"]["hostState"], "transport_ready");

        for index in 0..2 {
            let health = host
                .request(
                    &format!("health-{index}"),
                    "system.health",
                    Duration::from_secs(3),
                )
                .expect("health should respond");
            assert_eq!(health["payload"]["status"], "healthy");
        }

        let unknown = host
            .request("unknown", "system.unknown", Duration::from_secs(3))
            .expect("unknown control should return a framed error");
        assert_eq!(unknown["ok"], false);
        assert_eq!(unknown["error"]["code"], "UNKNOWN_CONTROL");

        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("protocol shutdown should reclaim the complete Job");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
    }

    #[test]
    fn managed_real_python_host_treats_clean_stdin_eof_as_orderly_exit() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let host = CoreHostRuntime::launch(&layout, GENERATION_ID)
            .expect("real Core Host should launch in a managed Job");
        let exit = host
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("stdin EOF should stop and reclaim the Core Host");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
    }

    #[test]
    #[cfg(windows)]
    fn polluted_real_stdout_fails_framing_and_the_job_is_force_reclaimed() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/polluting_host.py");
        let mut spec = ManagedProcessSpec::new(python);
        spec.arg(fixture.as_os_str()).current_dir(&root);
        let (mut tree, pipes) =
            ManagedProcessTree::spawn_piped(&spec).expect("polluting fixture should launch");
        let (sender, receiver) = mpsc::sync_channel(1);
        let reader = thread::spawn(move || {
            let mut stdout = pipes.stdout;
            let result = read_frame(&mut stdout);
            let _ = sender.send((pipes.stdin, stdout, pipes.stderr, result));
        });
        let (stdin, stdout, stderr, result) = receiver
            .recv_timeout(Duration::from_secs(3))
            .expect("pollution should be observed before deadline");
        assert_eq!(
            result.expect_err("pollution must not decode").code,
            "FRAME_TOO_LARGE"
        );
        tree.terminate_tree(97)
            .expect("polluting fixture Job should terminate");
        assert_eq!(
            tree.wait(Duration::from_secs(5)).expect("root wait"),
            WaitOutcome::Exited(97)
        );
        assert!(tree
            .verify_tree_exited(Duration::from_secs(5))
            .expect("Job query"));
        tree.release_exited_handles().expect("handle release");
        drop((stdin, stdout, stderr));
        reader.join().expect("pollution reader should join");
    }

    #[test]
    fn ignored_control_deadline_force_reclaims_the_managed_python_job() {
        let _test_lock = lifecycle_test_lock();
        let root = repo_root();
        let python = development_layout().python_executable;
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py");
        let host = CoreHostRuntime::launch_script_for_test(&python, &root, &fixture, GENERATION_ID)
            .expect("ignoring fixture should launch");
        let exit = host
            .shutdown(Duration::from_millis(250), Duration::from_secs(5))
            .expect("ignored shutdown should force and finalize its Job");
        assert_eq!(exit.root_exit_code, 93);
        assert!(exit.tree_empty);
        assert!(exit.forced);
    }

    #[test]
    fn python_snapshot_cache_is_read_only_and_clears_on_new_generation() {
        let first_generation = "00000000-0000-4000-8000-000000001c02";
        let second_generation = "00000000-0000-4000-8000-000000002c02";
        let snapshot = json!({
            "schemaVersion": 1,
            "generationId": first_generation,
            "generationNumber": 1,
            "revision": 2,
            "readiness": "ready",
            "components": {"fixture": {"state": "ready", "pythonOwned": [1, 2, 3]}},
            "capabilities": ["core.snapshot"],
            "currentCharacterSummary": null,
            "activeInteractionSummary": null,
            "coreConfigRevision": 0
        });
        let mut cache = CoreSnapshotCache::new(first_generation).expect("generation cache");
        cache
            .store_python_snapshot(&snapshot)
            .expect("Python snapshot should cache");
        assert_eq!(cache.current(), Some(&snapshot));

        cache
            .begin_generation(second_generation)
            .expect("new generation should start");
        assert_eq!(cache.current(), None);
        assert!(cache.store_python_snapshot(&snapshot).is_err());
    }

    #[test]
    fn managed_real_python_host_initializes_and_caches_its_snapshot() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        let initialize = host
            .request_with_payload(
                "initialize",
                "core.initialize",
                json!({"mode": "ready", "delayMs": 20}),
                Duration::from_secs(3),
            )
            .expect("initialize should be accepted");
        assert_eq!(initialize["payload"]["readiness"], "initializing");

        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            let snapshot = host
                .refresh_snapshot("snapshot", Duration::from_secs(3))
                .expect("snapshot should respond");
            if snapshot["readiness"] == "ready" {
                assert_eq!(host.cached_snapshot(), Some(&snapshot));
                assert_eq!(snapshot["generationId"], GENERATION_ID);
                break;
            }
            assert!(std::time::Instant::now() < deadline);
        }

        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("initialized Host should stop cleanly");
        assert_eq!(exit.root_exit_code, 0);
        assert!(!exit.forced);
    }

    #[test]
    fn hung_python_initialize_keeps_health_and_shutdown_responsive() {
        let _test_lock = lifecycle_test_lock();
        let layout = development_layout();
        let mut host =
            CoreHostRuntime::launch(&layout, GENERATION_ID).expect("real Core Host should launch");
        host.request_with_payload(
            "initialize",
            "core.initialize",
            json!({"mode": "hang"}),
            Duration::from_secs(3),
        )
        .expect("hung initialize should still be accepted quickly");
        for index in 0..3 {
            let health = host
                .request(
                    &format!("health-hang-{index}"),
                    "system.health",
                    Duration::from_secs(3),
                )
                .expect("health should remain responsive");
            assert_eq!(health["payload"]["hostState"], "initializing");
        }
        let exit = host
            .shutdown(Duration::from_secs(3), Duration::from_secs(5))
            .expect("shutdown should cancel hung initialize");
        assert_eq!(exit.root_exit_code, 0);
        assert!(!exit.forced);
    }

    #[test]
    fn minimum_lifecycle_releases_and_reacquires_the_shared_lock() {
        let lock_backend = NativeInstanceLockBackend;
        let first = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("shared lock should be acquirable");
        assert!(matches!(first, InstanceLockAcquire::Acquired(_)));
        let conflict = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("second lock attempt should be classified");
        assert!(matches!(conflict, InstanceLockAcquire::AlreadyRunning));
        drop(first);
        let reacquired = lock_backend
            .acquire(SHARED_INSTANCE_ID)
            .expect("lock should be immediately reacquirable after release");
        assert!(matches!(reacquired, InstanceLockAcquire::Acquired(_)));
        drop(reacquired);
    }
}
