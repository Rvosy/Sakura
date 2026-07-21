use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use crate::core_host_runtime::CoreHostRuntime;

const ACCEPTANCE_DIRECTORY_ENV: &str = "SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY";
const PYTHON_ENV: &str = "SAKURA_PHASE_1C_PYTHON";
const REPO_ROOT_ENV: &str = "SAKURA_PHASE_1C_REPO_ROOT";
const PHASE_1B_DIRECTORY_ENV: &str = "SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY";
const ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-1c-01-";
const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";

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
        if std::env::var_os(PHASE_1B_DIRECTORY_ENV).is_some() {
            return Err("Phase 1B and Phase 1C acceptance modes cannot run together".to_string());
        }
        let directory = validate_acceptance_path(PathBuf::from(directory))?;
        let repo_root = required_canonical_path(REPO_ROOT_ENV)?;
        if !repo_root.join("app/core_host/__main__.py").is_file() {
            return Err("Phase 1C acceptance repo root does not contain app.core_host".to_string());
        }
        let python = required_canonical_path(PYTHON_ENV)?;
        let expected_python = repo_root.join("runtime/python.exe");
        if python
            != fs::canonicalize(&expected_python).map_err(|error| {
                format!("failed to resolve acceptance runtime/python.exe: {error}")
            })?
        {
            return Err(
                "Phase 1C acceptance Python must be this repo's runtime/python.exe".to_string(),
            );
        }

        let cancellation = Arc::new(AtomicBool::new(false));
        let worker_cancellation = cancellation.clone();
        let worker_directory = directory.clone();
        let worker = thread::spawn(move || {
            let result = run_scenario(&worker_directory, &python, &repo_root, &worker_cancellation);
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
            .map_err(|_| "Phase 1C acceptance worker panicked".to_string())?
    }
}

fn run_scenario(
    directory: &Path,
    python: &Path,
    repo_root: &Path,
    cancellation: &AtomicBool,
) -> Result<(), String> {
    fs::write(directory.join("acceptance.worker.started"), b"started")
        .map_err(|error| format!("failed to write worker marker: {error}"))?;
    let mut host = CoreHostRuntime::launch(python, repo_root, GENERATION_ID)?;
    fs::write(directory.join("core.pid"), host.pid().to_string())
        .map_err(|error| format!("failed to write Core PID marker: {error}"))?;

    let hello = host.request("hello", "system.hello", Duration::from_secs(3))?;
    if hello.get("ok").and_then(serde_json::Value::as_bool) != Some(true)
        || hello
            .pointer("/payload/hostState")
            .and_then(serde_json::Value::as_str)
            != Some("transport_ready")
    {
        return Err("real Core Host returned an invalid hello response".to_string());
    }
    fs::write(directory.join("acceptance.hello"), b"hello")
        .map_err(|error| format!("failed to write hello marker: {error}"))?;

    for index in 0..2 {
        let health = host.request(
            &format!("health-{index}"),
            "system.health",
            Duration::from_secs(3),
        )?;
        if health
            .pointer("/payload/status")
            .and_then(serde_json::Value::as_str)
            != Some("healthy")
        {
            return Err("real Core Host returned an invalid health response".to_string());
        }
    }
    fs::write(directory.join("acceptance.ready"), b"ready")
        .map_err(|error| format!("failed to write ready marker: {error}"))?;
    wait_for_cancellation(cancellation, Instant::now() + Duration::from_secs(30))?;

    let exit = host.shutdown(Duration::from_secs(3), Duration::from_secs(5))?;
    if exit.root_exit_code != 0 || !exit.tree_empty || exit.forced || !exit.stderr.is_empty() {
        return Err(format!("real Core Host shutdown was not clean: {exit:?}"));
    }
    fs::write(directory.join("acceptance.cleaned"), b"cleaned")
        .map_err(|error| format!("failed to write cleanup marker: {error}"))
}

fn required_canonical_path(name: &str) -> Result<PathBuf, String> {
    let path = std::env::var_os(name).ok_or_else(|| format!("{name} is required"))?;
    let path = PathBuf::from(path);
    if !path.is_absolute() {
        return Err(format!("{name} must be absolute"));
    }
    fs::canonicalize(&path).map_err(|error| format!("failed to resolve {name}: {error}"))
}

fn validate_acceptance_path(path: PathBuf) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("Phase 1C acceptance directory must be absolute".to_string());
    }
    let temp_root = fs::canonicalize(std::env::temp_dir())
        .map_err(|error| format!("failed to resolve system temp directory: {error}"))?;
    let resolved = fs::canonicalize(&path)
        .map_err(|error| format!("failed to resolve acceptance directory: {error}"))?;
    let named = resolved.components().any(|component| {
        component
            .as_os_str()
            .to_string_lossy()
            .starts_with(ACCEPTANCE_DIRECTORY_PREFIX)
    });
    if !resolved.starts_with(&temp_root) || !named {
        return Err(format!(
            "Phase 1C acceptance directory is outside its isolated temp scope: {}",
            resolved.display()
        ));
    }
    Ok(resolved)
}

fn wait_for_cancellation(cancellation: &AtomicBool, deadline: Instant) -> Result<(), String> {
    loop {
        if cancellation.load(Ordering::Acquire) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(
                "Tauri window did not request Phase 1C shutdown before deadline".to_string(),
            );
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
mod tests {
    use std::{fs, process};

    use super::{validate_acceptance_path, ACCEPTANCE_DIRECTORY_PREFIX};

    #[test]
    fn acceptance_directory_is_restricted_to_its_named_system_temp_scope() {
        let accepted = std::env::temp_dir().join(format!(
            "{ACCEPTANCE_DIRECTORY_PREFIX}{}-path-test",
            process::id()
        ));
        fs::create_dir_all(&accepted).expect("accepted directory should create");
        assert!(validate_acceptance_path(accepted.clone()).is_ok());
        fs::remove_dir_all(&accepted).expect("accepted directory should remove");

        let rejected = std::env::temp_dir().join(format!("unrelated-{}", process::id()));
        fs::create_dir_all(&rejected).expect("rejected directory should create");
        assert!(validate_acceptance_path(rejected.clone()).is_err());
        fs::remove_dir_all(&rejected).expect("rejected directory should remove");
    }
}
