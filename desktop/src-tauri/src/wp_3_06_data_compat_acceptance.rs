//! Debug-only real-process acceptance driver for the WP-3-06 sanitized dataset.

use std::{
    fs,
    path::{Path, PathBuf},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use tauri::AppHandle;

use crate::{
    chat_settings::{SubtitleLanguage, SubtitleLanguageState},
    shell_lifecycle::ShellLifecycleHandle,
    ui_config::UiConfigRepository,
};

pub const DIRECTORY_ENV: &str = "SAKURA_WP_3_06_ACCEPTANCE_DIRECTORY";
pub const MODE_ENV: &str = "SAKURA_WP_3_06_ACCEPTANCE_MODE";
const SANITIZED_MARKER: &str = ".sakura-wp-3-06-sanitized";
const APP_ROOT: &str = "app-root";
const USER_MESSAGE: &str = "[WP-3-06-TAURI-USER]";
const ASSISTANT_REPLY: &str = "[WP-3-06-TAURI-REPLY]";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AcceptanceMode {
    Chat,
    Hold,
}

#[derive(Clone, Debug)]
pub struct AcceptanceRequest {
    pub directory: PathBuf,
    pub app_root: PathBuf,
    pub mode: AcceptanceMode,
}

pub fn request_from_environment() -> Result<Option<AcceptanceRequest>, String> {
    let directory = std::env::var_os(DIRECTORY_ENV);
    let mode = std::env::var_os(MODE_ENV);
    match (directory, mode) {
        (None, None) => Ok(None),
        (Some(directory), Some(mode)) => {
            validate_request(PathBuf::from(directory), &mode.to_string_lossy()).map(Some)
        }
        _ => Err("WP_3_06_ACCEPTANCE_REQUEST_INVALID".to_string()),
    }
}

fn validate_request(directory: PathBuf, mode: &str) -> Result<AcceptanceRequest, String> {
    if !directory.is_absolute() {
        return Err("WP_3_06_ACCEPTANCE_PATH_INVALID".to_string());
    }
    if fs::symlink_metadata(&directory)
        .map_err(|_| "WP_3_06_ACCEPTANCE_PATH_INVALID".to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("WP_3_06_ACCEPTANCE_PATH_INVALID".to_string());
    }
    let directory = directory
        .canonicalize()
        .map_err(|_| "WP_3_06_ACCEPTANCE_PATH_INVALID".to_string())?;
    let temp = std::env::temp_dir()
        .canonicalize()
        .map_err(|_| "WP_3_06_ACCEPTANCE_TEMP_UNAVAILABLE".to_string())?;
    let relative = directory
        .strip_prefix(&temp)
        .map_err(|_| "WP_3_06_ACCEPTANCE_PATH_OUTSIDE_TEMP".to_string())?;
    if relative.components().count() != 1
        || !relative
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.starts_with("sakura-wp-3-06-"))
    {
        return Err("WP_3_06_ACCEPTANCE_PATH_INVALID".to_string());
    }
    let marker = directory.join(SANITIZED_MARKER);
    if !regular_non_link(&marker) {
        return Err("WP_3_06_ACCEPTANCE_MARKER_MISSING".to_string());
    }
    let app_root = directory.join(APP_ROOT);
    if fs::symlink_metadata(&app_root)
        .map_err(|_| "WP_3_06_ACCEPTANCE_ROOT_INVALID".to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("WP_3_06_ACCEPTANCE_ROOT_INVALID".to_string());
    }
    let app_root = app_root
        .canonicalize()
        .map_err(|_| "WP_3_06_ACCEPTANCE_ROOT_INVALID".to_string())?;
    if app_root.parent() != Some(directory.as_path())
        || ![
            "data/config/system_config.yaml",
            "data/config/characters.yaml",
            "characters/fixture/character.json",
        ]
        .iter()
        .all(|relative| regular_non_link(&app_root.join(relative)))
    {
        return Err("WP_3_06_ACCEPTANCE_FIXTURE_INCOMPLETE".to_string());
    }
    let mode = match mode {
        "tauri-chat" => AcceptanceMode::Chat,
        "tauri-hold" => AcceptanceMode::Hold,
        _ => return Err("WP_3_06_ACCEPTANCE_MODE_INVALID".to_string()),
    };
    Ok(AcceptanceRequest {
        directory,
        app_root,
        mode,
    })
}

pub fn record_lock_conflict(request: &AcceptanceRequest) -> Result<(), String> {
    fs::write(
        request.directory.join("tauri.lock_conflict"),
        b"already_running",
    )
    .map_err(|_| "WP_3_06_ACCEPTANCE_MARKER_WRITE_FAILED".to_string())
}

pub fn start_driver(
    request: Option<AcceptanceRequest>,
    app: AppHandle,
    lifecycle: Option<ShellLifecycleHandle>,
) -> Result<Option<JoinHandle<()>>, String> {
    let Some(request) = request else {
        return Ok(None);
    };
    let lifecycle = lifecycle.ok_or_else(|| "WP_3_06_LIFECYCLE_UNAVAILABLE".to_string())?;
    Ok(Some(thread::spawn(move || {
        let result = match request.mode {
            AcceptanceMode::Chat => run_chat(&request, &lifecycle),
            AcceptanceMode::Hold => run_hold(&request, &lifecycle),
        };
        let exit_code = match result {
            Ok(()) => 0,
            Err(error) => {
                let _ = fs::write(request.directory.join("tauri.error"), error.as_bytes());
                2
            }
        };
        let _ = lifecycle.request_shutdown();
        app.exit(exit_code);
    })))
}

fn run_chat(request: &AcceptanceRequest, lifecycle: &ShellLifecycleHandle) -> Result<(), String> {
    wait_for(Duration::from_secs(35), || {
        lifecycle.available_generation_id().ok().flatten().is_some()
    })
    .ok_or_else(|| "WP_3_06_CORE_READY_TIMEOUT".to_string())?;
    let pending = lifecycle
        .chat_bridge()?
        .send("main", USER_MESSAGE.to_string())?;
    pending.wait()?;
    let history = request.app_root.join("data/chat_history/fixture.jsonl");
    wait_for(Duration::from_secs(35), || {
        fs::read_to_string(&history)
            .is_ok_and(|text| text.contains(USER_MESSAGE) && text.contains(ASSISTANT_REPLY))
    })
    .ok_or_else(|| "WP_3_06_CHAT_COMPLETION_TIMEOUT".to_string())?;
    SubtitleLanguageState::new(UiConfigRepository::new(
        request.app_root.join("data/runtime_v2/config/ui.json"),
    ))
    .save(SubtitleLanguage::Ja)?;
    fs::write(request.directory.join("tauri.chat_complete"), b"complete")
        .map_err(|_| "WP_3_06_ACCEPTANCE_MARKER_WRITE_FAILED".to_string())
}

fn run_hold(request: &AcceptanceRequest, _lifecycle: &ShellLifecycleHandle) -> Result<(), String> {
    fs::write(request.directory.join("tauri.holding"), b"holding")
        .map_err(|_| "WP_3_06_ACCEPTANCE_MARKER_WRITE_FAILED".to_string())?;
    wait_for(Duration::from_secs(30), || {
        request.directory.join("tauri.release").is_file()
    })
    .ok_or_else(|| "WP_3_06_HOLD_TIMEOUT".to_string())?;
    fs::write(request.directory.join("tauri.released"), b"released")
        .map_err(|_| "WP_3_06_ACCEPTANCE_MARKER_WRITE_FAILED".to_string())
}

fn wait_for(timeout: Duration, mut predicate: impl FnMut() -> bool) -> Option<()> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() {
            return Some(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    None
}

fn regular_non_link(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.file_type().is_file() && !metadata.file_type().is_symlink())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!("sakura-wp-3-06-{nonce}"));
            let app_root = root.join(APP_ROOT);
            fs::create_dir_all(app_root.join("data/config")).unwrap();
            fs::create_dir_all(app_root.join("characters/fixture")).unwrap();
            fs::write(root.join(SANITIZED_MARKER), b"sanitized").unwrap();
            fs::write(
                app_root.join("data/config/system_config.yaml"),
                b"config_version: 4\n",
            )
            .unwrap();
            fs::write(
                app_root.join("data/config/characters.yaml"),
                b"current_character_id: fixture\n",
            )
            .unwrap();
            fs::write(app_root.join("characters/fixture/character.json"), b"{}").unwrap();
            Self(root)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn request_accepts_only_named_sanitized_system_temp_root() {
        let fixture = Fixture::new();
        let request = validate_request(fixture.0.clone(), "tauri-chat").unwrap();
        assert_eq!(request.mode, AcceptanceMode::Chat);
        assert_eq!(
            request.app_root,
            fixture.0.join(APP_ROOT).canonicalize().unwrap()
        );

        fs::remove_file(fixture.0.join(SANITIZED_MARKER)).unwrap();
        assert!(validate_request(fixture.0.clone(), "tauri-chat").is_err());
    }

    #[test]
    fn request_rejects_unknown_mode_and_non_temp_root() {
        let fixture = Fixture::new();
        assert!(validate_request(fixture.0.clone(), "future-mode").is_err());
        assert!(validate_request(PathBuf::from("."), "tauri-chat").is_err());
    }
}
