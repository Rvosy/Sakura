use std::{
    collections::BTreeSet,
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
use serde_json::Value;

use crate::{
    core_host_runtime::{CoreHostLifecycleFailure, CoreHostRuntime},
    platform::{
        current_platform_target, FilesystemRuntimeLocator, RuntimeLayout, RuntimeLocationRequest,
        RuntimeLocator, RuntimeMode,
    },
};

const ACCEPTANCE_DIRECTORY_ENV: &str = "SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY";
const REPO_ROOT_ENV: &str = "SAKURA_PHASE_1C_REPO_ROOT";
const INITIALIZE_MODE_ENV: &str = "SAKURA_PHASE_1C_INITIALIZE_MODE";
const PHASE_1B_DIRECTORY_ENV: &str = "SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY";
const CONTROLLED_EXIT_ENV: &str = "SAKURA_PHASE_1P_CONTROLLED_EXIT";
const ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-1c-02-";
const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";
const READY_FIXTURE_RELATIVE: &str = "tests/fixtures/runtime_v2/wp_3_01/ready";
const SHUTDOWN_SCHEDULING_TOLERANCE: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, PartialEq, Eq)]
struct FixtureFileRecord {
    relative_path: PathBuf,
    length: u64,
    modified_seconds: u64,
    modified_nanos: u32,
    sha256: String,
}

#[derive(Debug)]
struct FixtureCopy {
    source_root: PathBuf,
    copied_root: PathBuf,
    source_manifest: Vec<FixtureFileRecord>,
    copied_manifest: Vec<FixtureFileRecord>,
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
        if initialize_mode != "ready" {
            return Err("Phase 1C initialize mode must be ready".to_string());
        }
        let fixture_source = repo_root.join(READY_FIXTURE_RELATIVE);
        let fixture_copy = copy_fixture_tree(&fixture_source, &directory.join("assistant-root"))?;
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
                explicit_development_root: Some(repo_root.clone()),
                assistant_root: fixture_copy.copied_root.clone(),
            })
            .map_err(|error| format!("Phase 1C RuntimeLocator failed: {error}"))?;

        let cancellation = Arc::new(AtomicBool::new(false));
        let worker_cancellation = cancellation.clone();
        let worker_directory = directory.clone();
        let worker = thread::spawn(move || {
            let result = run_scenario(
                &worker_directory,
                &layout,
                &fixture_copy,
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

    pub fn start_controlled_exit_watcher(
        &self,
        app_handle: tauri::AppHandle,
    ) -> Option<JoinHandle<()>> {
        if std::env::var(CONTROLLED_EXIT_ENV).as_deref() != Ok("1") {
            return None;
        }
        let shutdown = self.shutdown_signal();
        let directory = self.directory.clone();
        Some(thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(60);
            loop {
                if directory.join("acceptance.error").is_file() {
                    app_handle.exit(3);
                    return;
                }
                if directory.join("acceptance.exit_requested").is_file() {
                    shutdown.request();
                    break;
                }
                if Instant::now() >= deadline {
                    let _ = fs::write(
                        directory.join("acceptance.error"),
                        b"controlled Shell exit was not requested before deadline",
                    );
                    app_handle.exit(3);
                    return;
                }
                thread::sleep(Duration::from_millis(10));
            }

            let cleanup_deadline = Instant::now() + Duration::from_secs(10);
            while !directory.join("acceptance.cleaned").is_file() {
                if directory.join("acceptance.error").is_file()
                    || Instant::now() >= cleanup_deadline
                {
                    app_handle.exit(3);
                    return;
                }
                thread::sleep(Duration::from_millis(10));
            }
            app_handle.exit(0);
        }))
    }

    pub fn shutdown_and_join(self) -> Result<(), String> {
        self.shutdown_signal().request();
        self.worker
            .join()
            .map_err(|_| "Phase 1C acceptance worker panicked".to_string())?
    }
}

pub fn record_lock_conflict_if_requested() -> Result<bool, String> {
    let Some(directory) = std::env::var_os(ACCEPTANCE_DIRECTORY_ENV) else {
        return Ok(false);
    };
    let directory = validate_acceptance_path(PathBuf::from(directory))?;
    fs::write(
        directory.join("acceptance.lock_conflict"),
        b"already-running",
    )
    .map_err(|error| format!("failed to write shared-lock conflict marker: {error}"))?;
    Ok(true)
}

fn run_scenario(
    directory: &Path,
    layout: &RuntimeLayout,
    fixture_copy: &FixtureCopy,
    cancellation: &AtomicBool,
) -> Result<(), String> {
    if layout.assistant_root != fixture_copy.copied_root
        || layout.assistant_root == fixture_copy.source_root
        || !layout.assistant_root.starts_with(directory)
    {
        return Err("Phase 1C Assistant root is not the isolated fixture copy".to_string());
    }
    fs::write(directory.join("acceptance.worker.started"), b"started")
        .map_err(|error| format!("failed to write worker marker: {error}"))?;
    fs::write(
        directory.join("runtime-layout.json"),
        serde_json::to_vec_pretty(&json!({
            "target": layout.target,
            "mode": layout.mode,
            "sourceId": layout.source_id,
            "pythonExecutable": layout.python_executable,
            "assistantRoot": layout.assistant_root,
            "coreModule": layout.core_module,
        }))
        .map_err(|error| format!("failed to encode Runtime layout evidence: {error}"))?,
    )
    .map_err(|error| format!("failed to write Runtime layout evidence: {error}"))?;
    let mut host = CoreHostRuntime::launch(layout, GENERATION_ID)
        .map_err(CoreHostLifecycleFailure::into_terminal_diagnostic)?;
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
        json!({}),
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
        if readiness != "initializing" {
            validate_ready_snapshot(&snapshot)?;
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
    fs::write(directory.join("acceptance.ready"), b"ready")
        .map_err(|error| format!("failed to write ready marker: {error}"))?;
    wait_for_cancellation(cancellation, Instant::now() + Duration::from_secs(30))?;

    let shutdown_started = Instant::now();
    let exit = match host.shutdown() {
        Ok(exit) => exit,
        Err(failure) => {
            let diagnostic = failure.diagnostic().to_string();
            if failure.into_recovery().is_some() {
                return Err(
                    "real ready Core Host shutdown returned an unexpected recovery capsule"
                        .to_string(),
                );
            }
            return Err(diagnostic);
        }
    };
    let shutdown_elapsed = shutdown_started.elapsed();
    let stderr_reader_completed = exit.stderr_stats.eof && !exit.stderr_stats.read_failed;
    if shutdown_elapsed >= Duration::from_secs(5) + SHUTDOWN_SCHEDULING_TOLERANCE
        || exit.root_exit_code != 0
        || !exit.tree_empty
        || exit.forced
        || !exit.stderr.is_empty()
        || !stderr_reader_completed
    {
        return Err(format!("real Core Host shutdown was not clean: {exit:?}"));
    }
    let source_unchanged =
        fixture_manifest(&fixture_copy.source_root)? == fixture_copy.source_manifest;
    let copied_unchanged =
        fixture_manifest(&fixture_copy.copied_root)? == fixture_copy.copied_manifest;
    if !source_unchanged || !copied_unchanged {
        return Err("real Core Host changed the source or copied Assistant fixture".to_string());
    }
    fs::write(
        directory.join("shutdown-evidence.json"),
        serde_json::to_vec_pretty(&json!({
            "shutdownElapsedMs": shutdown_elapsed.as_millis(),
            "treeEmpty": exit.tree_empty,
            "forced": exit.forced,
            "rootExitCode": exit.root_exit_code,
            "stderrEmpty": exit.stderr.is_empty(),
            "stderrReaderCompleted": stderr_reader_completed,
            "coreIdentityPresent": !exit.tree_empty,
            "sourceFixtureUnchanged": source_unchanged,
            "copiedFixtureUnchanged": copied_unchanged,
        }))
        .map_err(|error| format!("failed to encode shutdown evidence: {error}"))?,
    )
    .map_err(|error| format!("failed to write shutdown evidence: {error}"))?;
    fs::write(directory.join("acceptance.cleaned"), b"cleaned")
        .map_err(|error| format!("failed to write cleanup marker: {error}"))
}

fn validate_ready_snapshot(snapshot: &Value) -> Result<(), String> {
    if snapshot.get("readiness").and_then(Value::as_str) != Some("ready")
        || snapshot
            .pointer("/components/assistant/state")
            .and_then(Value::as_str)
            != Some("ready")
        || snapshot
            .pointer("/components/assistant/code")
            .and_then(Value::as_str)
            != Some("READY")
        || snapshot
            .pointer("/components/assistant/retryable")
            .and_then(Value::as_bool)
            != Some(false)
    {
        return Err("real Core Host did not produce the exact ready Assistant state".to_string());
    }
    let assistant = snapshot
        .pointer("/components/assistant")
        .and_then(Value::as_object)
        .ok_or_else(|| "real Core Host Snapshot omitted the Assistant component".to_string())?;
    if assistant
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>()
        != BTreeSet::from(["code", "retryable", "state"])
    {
        return Err("real Core Host Assistant component exposed unexpected fields".to_string());
    }
    let summary = snapshot
        .get("currentCharacterSummary")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            "real Core Host Snapshot omitted the public character summary".to_string()
        })?;
    let summary_keys = summary.keys().map(String::as_str).collect::<BTreeSet<_>>();
    if summary_keys
        != BTreeSet::from([
            "displayName",
            "id",
            "initialMessage",
            "portraitChoices",
            "replyTones",
        ])
    {
        return Err("real Core Host character summary fields are not exact".to_string());
    }
    Ok(())
}

fn copy_fixture_tree(source: &Path, destination: &Path) -> Result<FixtureCopy, String> {
    let source_type = fs::symlink_metadata(source)
        .map_err(|error| format!("failed to inspect Assistant fixture source: {error}"))?
        .file_type();
    if source_type.is_symlink() {
        return Err("Assistant fixture source is a symlink".to_string());
    }
    let source_root = fs::canonicalize(source)
        .map_err(|error| format!("failed to resolve Assistant fixture source: {error}"))?;
    if !source_type.is_dir() {
        return Err("Assistant fixture source is not a directory".to_string());
    }
    if destination.exists() {
        return Err("isolated Assistant fixture destination already exists".to_string());
    }
    let destination_parent = destination
        .parent()
        .ok_or_else(|| "isolated Assistant fixture destination has no parent".to_string())?;
    let destination_parent = fs::canonicalize(destination_parent)
        .map_err(|error| format!("failed to resolve Assistant fixture destination: {error}"))?;
    if destination_parent.starts_with(&source_root) {
        return Err("isolated Assistant fixture destination overlaps its source".to_string());
    }

    let source_manifest = fixture_manifest(&source_root)?;
    fs::create_dir(destination)
        .map_err(|error| format!("failed to create isolated Assistant root: {error}"))?;
    let copied_root = fs::canonicalize(destination)
        .map_err(|error| format!("failed to resolve isolated Assistant root: {error}"))?;
    copy_fixture_directory(&source_root, &source_root, &copied_root)?;

    let source_after = fixture_manifest(&source_root)?;
    if source_after != source_manifest {
        return Err("Assistant fixture source changed while it was copied".to_string());
    }
    let copied_manifest = fixture_manifest(&copied_root)?;
    if comparable_manifest(&source_manifest) != comparable_manifest(&copied_manifest) {
        return Err("isolated Assistant fixture copy does not match its source".to_string());
    }
    Ok(FixtureCopy {
        source_root,
        copied_root,
        source_manifest,
        copied_manifest,
    })
}

fn copy_fixture_directory(
    source_root: &Path,
    source: &Path,
    destination: &Path,
) -> Result<(), String> {
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("failed to read Assistant fixture directory: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("failed to enumerate Assistant fixture directory: {error}"))?;
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let source_path = entry.path();
        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|error| format!("failed to inspect Assistant fixture entry: {error}"))?;
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            return Err("Assistant fixture contains a symlink".to_string());
        }
        source_path
            .strip_prefix(source_root)
            .map_err(|_| "Assistant fixture entry escaped its source root".to_string())?;
        let destination_path = destination.join(entry.file_name());
        if file_type.is_dir() {
            fs::create_dir(&destination_path).map_err(|error| {
                format!("failed to create Assistant fixture directory: {error}")
            })?;
            copy_fixture_directory(source_root, &source_path, &destination_path)?;
        } else if file_type.is_file() {
            fs::copy(&source_path, &destination_path)
                .map_err(|error| format!("failed to copy Assistant fixture file: {error}"))?;
        } else {
            return Err("Assistant fixture contains a non-file entry".to_string());
        }
    }
    Ok(())
}

fn fixture_manifest(root: &Path) -> Result<Vec<FixtureFileRecord>, String> {
    let root = fs::canonicalize(root)
        .map_err(|error| format!("failed to resolve Assistant fixture manifest root: {error}"))?;
    let mut records = Vec::new();
    collect_fixture_manifest(&root, &root, &mut records)?;
    records.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    Ok(records)
}

fn collect_fixture_manifest(
    root: &Path,
    directory: &Path,
    records: &mut Vec<FixtureFileRecord>,
) -> Result<(), String> {
    let mut entries = fs::read_dir(directory)
        .map_err(|error| format!("failed to read Assistant fixture manifest: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("failed to enumerate Assistant fixture manifest: {error}"))?;
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path).map_err(|error| {
            format!("failed to inspect Assistant fixture manifest entry: {error}")
        })?;
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            return Err("Assistant fixture manifest contains a symlink".to_string());
        }
        if file_type.is_dir() {
            collect_fixture_manifest(root, &path, records)?;
        } else if file_type.is_file() {
            let modified = metadata
                .modified()
                .map_err(|error| format!("failed to read Assistant fixture mtime: {error}"))?
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| "Assistant fixture mtime predates the Unix epoch".to_string())?;
            let bytes = fs::read(&path)
                .map_err(|error| format!("failed to read Assistant fixture file: {error}"))?;
            records.push(FixtureFileRecord {
                relative_path: path
                    .strip_prefix(root)
                    .map_err(|_| "Assistant fixture manifest entry escaped its root".to_string())?
                    .to_path_buf(),
                length: metadata.len(),
                modified_seconds: modified.as_secs(),
                modified_nanos: modified.subsec_nanos(),
                sha256: sha256_hex(&bytes),
            });
        } else {
            return Err("Assistant fixture manifest contains a non-file entry".to_string());
        }
    }
    Ok(())
}

fn comparable_manifest(records: &[FixtureFileRecord]) -> Vec<(&Path, u64, &str)> {
    records
        .iter()
        .map(|record| {
            (
                record.relative_path.as_path(),
                record.length,
                record.sha256.as_str(),
            )
        })
        .collect()
}

fn sha256_hex(input: &[u8]) -> String {
    const INITIAL: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    const ROUND: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let bit_len = (input.len() as u64).wrapping_mul(8);
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_len.to_be_bytes());
    let mut state = INITIAL;
    for chunk in padded.chunks_exact(64) {
        let mut words = [0_u32; 64];
        for (index, word) in words[..16].iter_mut().enumerate() {
            *word = u32::from_be_bytes(chunk[index * 4..index * 4 + 4].try_into().expect("word"));
        }
        for index in 16..64 {
            let s0 = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let s1 = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(s0)
                .wrapping_add(words[index - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for index in 0..64 {
            let sum1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choice = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(sum1)
                .wrapping_add(choice)
                .wrapping_add(ROUND[index])
                .wrapping_add(words[index]);
            let sum0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = sum0.wrapping_add(majority);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        for (slot, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *slot = slot.wrapping_add(value);
        }
    }
    state.iter().map(|word| format!("{word:08x}")).collect()
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

    use serde_json::json;

    use super::{
        copy_fixture_tree, fixture_manifest, sha256_hex, validate_acceptance_path,
        validate_ready_snapshot, ACCEPTANCE_DIRECTORY_PREFIX,
    };

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

    #[test]
    fn fixture_copy_is_isolated_and_detects_any_post_copy_change() {
        let root = std::env::temp_dir().join(format!(
            "{ACCEPTANCE_DIRECTORY_PREFIX}{}-fixture-copy-test",
            process::id()
        ));
        let source = root.join("source");
        let destination = root.join("assistant-root");
        fs::create_dir_all(source.join("characters/sakura"))
            .expect("source fixture directories should create");
        fs::write(
            source.join("characters/sakura/character.json"),
            b"{\"id\":\"sakura\"}",
        )
        .expect("source fixture should write");

        let copy = copy_fixture_tree(&source, &destination).expect("fixture copy should succeed");
        assert_ne!(
            source.canonicalize().expect("source should resolve"),
            destination
                .canonicalize()
                .expect("destination should resolve")
        );
        assert_eq!(
            fixture_manifest(&source).expect("source manifest should resolve"),
            copy.source_manifest
        );
        assert_eq!(
            fixture_manifest(&destination).expect("copied manifest should resolve"),
            copy.copied_manifest
        );

        fs::write(
            destination.join("characters/sakura/character.json"),
            b"{\"id\":\"changed\"}",
        )
        .expect("copied fixture mutation should write");
        assert_ne!(
            fixture_manifest(&destination).expect("mutated manifest should resolve"),
            copy.copied_manifest
        );
        fs::remove_dir_all(&root).expect("fixture copy test directory should remove");
    }

    #[cfg(unix)]
    #[test]
    fn fixture_copy_rejects_symlinks() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "{ACCEPTANCE_DIRECTORY_PREFIX}{}-fixture-symlink-test",
            process::id()
        ));
        let source = root.join("source");
        fs::create_dir_all(&source).expect("source fixture directory should create");
        fs::write(source.join("regular.txt"), b"safe").expect("regular fixture should write");
        symlink(source.join("regular.txt"), source.join("escape.txt"))
            .expect("fixture symlink should create");

        let error = copy_fixture_tree(&source, &root.join("assistant-root"))
            .expect_err("fixture symlink must be rejected");
        assert!(error.contains("symlink"));

        fs::remove_file(source.join("escape.txt")).expect("entry symlink should remove");
        let source_link = root.join("source-link");
        symlink(&source, &source_link).expect("fixture root symlink should create");
        let error = copy_fixture_tree(&source_link, &root.join("assistant-root-from-link"))
            .expect_err("fixture root symlink must be rejected");
        assert!(error.contains("symlink"));
        fs::remove_dir_all(&root).expect("fixture symlink test directory should remove");
    }

    #[test]
    fn real_ready_snapshot_requires_the_exact_public_assistant_shape() {
        let snapshot = json!({
            "readiness": "ready",
            "components": {
                "assistant": {
                    "state": "ready",
                    "code": "READY",
                    "retryable": false
                }
            },
            "currentCharacterSummary": {
                "displayName": "Sakura",
                "id": "sakura",
                "initialMessage": "hello",
                "portraitChoices": ["neutral"],
                "replyTones": ["gentle"]
            }
        });
        validate_ready_snapshot(&snapshot).expect("exact ready Snapshot should pass");

        let mut polluted = snapshot;
        polluted["currentCharacterSummary"]["apiKey"] = json!("secret");
        assert!(validate_ready_snapshot(&polluted).is_err());
    }

    #[test]
    fn fixture_manifest_uses_standard_sha256() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
