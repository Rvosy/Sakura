use std::{
    fs::File,
    io::{Read, Write},
    path::Path,
    sync::mpsc,
    thread,
    time::Duration,
};

use serde_json::{json, Value};

use crate::{
    core_host_protocol::{read_frame, write_frame, PROTOCOL_MAJOR, PROTOCOL_MINOR},
    managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
};

const CONTROL_PRIORITY: &str = "control";
const DEADLINE_EXIT_CODE: u32 = 93;

#[derive(Debug, PartialEq, Eq)]
pub struct CoreHostExit {
    pub root_exit_code: u32,
    pub tree_empty: bool,
    pub forced: bool,
    pub stderr: String,
}

#[derive(Debug)]
pub struct CoreHostRuntime {
    tree: ManagedProcessTree,
    stdin: Option<File>,
    stdout: Option<File>,
    stderr: Option<File>,
    generation_id: String,
    deadline_forced: bool,
}

impl CoreHostRuntime {
    pub fn launch(python: &Path, repo_root: &Path, generation_id: &str) -> Result<Self, String> {
        if generation_id.trim().is_empty() {
            return Err("Core Host generation ID must not be empty".to_string());
        }
        let mut spec = ManagedProcessSpec::new(python);
        spec.arg("-m")
            .arg("app.core_host")
            .arg("--generation-id")
            .arg(generation_id)
            .current_dir(repo_root);
        let (tree, pipes) = ManagedProcessTree::spawn_piped(&spec)
            .map_err(|error| format!("Core Host managed spawn failed: {error}"))?;
        Ok(Self {
            tree,
            stdin: Some(pipes.stdin),
            stdout: Some(pipes.stdout),
            stderr: Some(pipes.stderr),
            generation_id: generation_id.to_string(),
            deadline_forced: false,
        })
    }

    #[cfg(test)]
    fn launch_script_for_test(
        python: &Path,
        repo_root: &Path,
        script: &Path,
        generation_id: &str,
    ) -> Result<Self, String> {
        let mut spec = ManagedProcessSpec::new(python);
        spec.arg(script).current_dir(repo_root);
        let (tree, pipes) = ManagedProcessTree::spawn_piped(&spec)
            .map_err(|error| format!("Core Host fixture spawn failed: {error}"))?;
        Ok(Self {
            tree,
            stdin: Some(pipes.stdin),
            stdout: Some(pipes.stdout),
            stderr: Some(pipes.stderr),
            generation_id: generation_id.to_string(),
            deadline_forced: false,
        })
    }

    pub fn pid(&self) -> u32 {
        self.tree.pid()
    }

    pub fn request(
        &mut self,
        request_id: &str,
        name: &str,
        deadline: Duration,
    ) -> Result<Value, String> {
        if request_id.trim().is_empty() || name.trim().is_empty() || deadline.is_zero() {
            return Err("Core Host control request is invalid".to_string());
        }
        let request = json!({
            "protocolMajor": PROTOCOL_MAJOR,
            "protocolMinor": PROTOCOL_MINOR,
            "kind": "request",
            "generationId": self.generation_id,
            "id": request_id,
            "name": name,
            "payload": {},
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
            .wait(stop_deadline)
            .map_err(|error| format!("Core Host root wait failed: {error}"))?
        {
            WaitOutcome::Exited(code) => code,
            WaitOutcome::TimedOut => {
                forced = true;
                self.tree
                    .terminate_tree(DEADLINE_EXIT_CODE)
                    .map_err(|error| format!("Core Host forced cleanup failed: {error}"))?;
                match self
                    .tree
                    .wait(Duration::from_secs(5))
                    .map_err(|error| format!("Core Host forced root wait failed: {error}"))?
                {
                    WaitOutcome::Exited(code) => code,
                    WaitOutcome::TimedOut => {
                        return Err("Core Host root survived forced cleanup".to_string())
                    }
                }
            }
        };
        let tree_empty = self
            .tree
            .verify_tree_exited(Duration::from_secs(5))
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
            .release_exited_handles()
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

#[cfg(all(test, windows))]
mod tests {
    use std::{path::PathBuf, sync::mpsc, thread, time::Duration};

    use crate::{
        core_host_protocol::read_frame,
        managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
    };

    use super::CoreHostRuntime;

    const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
    }

    fn python() -> PathBuf {
        repo_root().join("runtime/python.exe")
    }

    #[test]
    fn managed_real_python_host_answers_control_and_releases_its_job_and_pipes() {
        let root = repo_root();
        let mut host = CoreHostRuntime::launch(&python(), &root, GENERATION_ID)
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
        let root = repo_root();
        let host = CoreHostRuntime::launch(&python(), &root, GENERATION_ID)
            .expect("real Core Host should launch in a managed Job");
        let exit = host
            .close_stdin_and_wait(Duration::from_secs(5))
            .expect("stdin EOF should stop and reclaim the Core Host");
        assert_eq!(exit.root_exit_code, 0);
        assert!(exit.tree_empty);
        assert!(exit.stderr.is_empty());
    }

    #[test]
    fn polluted_real_stdout_fails_framing_and_the_job_is_force_reclaimed() {
        let root = repo_root();
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/polluting_host.py");
        let mut spec = ManagedProcessSpec::new(python());
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
        let root = repo_root();
        let fixture = root.join("tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py");
        let host =
            CoreHostRuntime::launch_script_for_test(&python(), &root, &fixture, GENERATION_ID)
                .expect("ignoring fixture should launch");
        let exit = host
            .shutdown(Duration::from_millis(250), Duration::from_secs(5))
            .expect("ignored shutdown should force and finalize its Job");
        assert_eq!(exit.root_exit_code, 93);
        assert!(exit.tree_empty);
        assert!(exit.forced);
    }
}
