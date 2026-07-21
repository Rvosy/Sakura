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

use serde_json::json;

use crate::{
    core_host_runtime::CoreHostRuntime,
    platform::{
        current_platform_target, FilesystemRuntimeLocator, RuntimeLayout, RuntimeLocationRequest,
        RuntimeLocator, RuntimeMode,
    },
};

const ACCEPTANCE_DIRECTORY_ENV: &str = "SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY";
const REPO_ROOT_ENV: &str = "SAKURA_PHASE_1C_REPO_ROOT";
const INITIALIZE_MODE_ENV: &str = "SAKURA_PHASE_1C_INITIALIZE_MODE";
const PHASE_1B_DIRECTORY_ENV: &str = "SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY";
const ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-1c-02-";
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
        let initialize_mode = std::env::var(INITIALIZE_MODE_ENV)
            .map_err(|_| format!("{INITIALIZE_MODE_ENV} is required"))?;
        if !matches!(initialize_mode.as_str(), "ready" | "hang") {
            return Err("Phase 1C initialize mode must be ready or hang".to_string());
        }
        let executable_directory = std::env::current_exe()
            .map_err(|error| format!("failed to resolve acceptance executable: {error}"))?
            .parent()
            .ok_or_else(|| "acceptance executable has no parent directory".to_string())?
            .to_path_buf();
        let target = current_platform_target()
            .ok_or_else(|| "Phase 1C acceptance requires a formal platform target".to_string())?;
        let layout = FilesystemRuntimeLocator
            .locate(&RuntimeLocationRequest {
                mode: RuntimeMode::ExplicitDevelopment,
                target,
                executable_directory,
                resource_directory: repo_root.clone(),
                explicit_development_root: Some(repo_root),
            })
            .map_err(|error| format!("Phase 1C RuntimeLocator failed: {error}"))?;

        let cancellation = Arc::new(AtomicBool::new(false));
        let worker_cancellation = cancellation.clone();
        let worker_directory = directory.clone();
        let worker = thread::spawn(move || {
            let result = run_scenario(
                &worker_directory,
                &layout,
                &initialize_mode,
                &worker_cancellation,
            );
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
    layout: &RuntimeLayout,
    initialize_mode: &str,
    cancellation: &AtomicBool,
) -> Result<(), String> {
    fs::write(directory.join("acceptance.worker.started"), b"started")
        .map_err(|error| format!("failed to write worker marker: {error}"))?;
    fs::write(
        directory.join("runtime-layout.json"),
        serde_json::to_vec_pretty(&json!({
            "target": layout.target,
            "mode": layout.mode,
            "sourceId": layout.source_id,
            "pythonExecutable": layout.python_executable,
            "applicationRoot": layout.application_root,
            "coreModule": layout.core_module,
        }))
        .map_err(|error| format!("failed to encode Runtime layout evidence: {error}"))?,
    )
    .map_err(|error| format!("failed to write Runtime layout evidence: {error}"))?;
    let mut host = CoreHostRuntime::launch(layout, GENERATION_ID)?;
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

    let initialize = host.request_with_payload(
        "initialize",
        "core.initialize",
        if initialize_mode == "ready" {
            json!({"mode": "ready", "delayMs": 50})
        } else {
            json!({"mode": "hang"})
        },
        Duration::from_secs(5),
    )?;
    if initialize
        .pointer("/payload/accepted")
        .and_then(serde_json::Value::as_bool)
        != Some(true)
        || initialize
            .pointer("/payload/readiness")
            .and_then(serde_json::Value::as_str)
            != Some("initializing")
    {
        return Err("real Core Host returned an invalid initialize response".to_string());
    }
    fs::write(directory.join("acceptance.initialize"), b"accepted")
        .map_err(|error| format!("failed to write initialize marker: {error}"))?;

    let readiness_deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let snapshot = host.refresh_snapshot("snapshot", Duration::from_secs(3))?;
        let readiness = snapshot
            .get("readiness")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "real Core Host Snapshot omitted readiness".to_string())?;
        if initialize_mode == "hang" || readiness == "ready" {
            if initialize_mode == "hang" && readiness != "initializing" {
                return Err("hung initialize left initializing unexpectedly".to_string());
            }
            fs::write(
                directory.join("snapshot.json"),
                serde_json::to_vec_pretty(&snapshot)
                    .map_err(|error| format!("failed to encode snapshot evidence: {error}"))?,
            )
            .map_err(|error| format!("failed to write snapshot evidence: {error}"))?;
            break;
        }
        if Instant::now() >= readiness_deadline {
            return Err("real Core Host did not become ready before watchdog".to_string());
        }
        thread::sleep(Duration::from_millis(10));
    }

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
    fs::write(
        directory.join("acceptance.ready"),
        initialize_mode.as_bytes(),
    )
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
