use serde::Serialize;
use tauri::{AppHandle, Runtime, State, WebviewWindow};

use crate::product_shell;
use tauri_plugin_autostart::{AutoLaunchManager, ManagerExt};

const READ_FAILED: &str = "AUTOSTART_SETTINGS_READ_FAILED";
const UPDATE_FAILED: &str = "AUTOSTART_SETTINGS_UPDATE_FAILED";
const VERIFY_FAILED: &str = "AUTOSTART_SETTINGS_VERIFY_FAILED";

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutostartSettingsSnapshot {
    pub schema_version: u32,
    pub window_generation: u64,
    pub launch_at_login: bool,
}

trait AutostartBackend {
    fn enable(&self) -> Result<(), String>;
    fn disable(&self) -> Result<(), String>;
    fn is_enabled(&self) -> Result<bool, String>;
}

impl AutostartBackend for AutoLaunchManager {
    fn enable(&self) -> Result<(), String> {
        AutoLaunchManager::enable(self).map_err(|error| error.to_string())
    }

    fn disable(&self) -> Result<(), String> {
        AutoLaunchManager::disable(self).map_err(|error| error.to_string())
    }

    fn is_enabled(&self) -> Result<bool, String> {
        AutoLaunchManager::is_enabled(self).map_err(|error| error.to_string())
    }
}

fn snapshot_from(
    backend: &impl AutostartBackend,
    window_generation: u64,
) -> Result<AutostartSettingsSnapshot, String> {
    let launch_at_login = backend
        .is_enabled()
        .map_err(|source_error| crate::runtime_log::diagnostic_error(READ_FAILED, source_error))?;
    Ok(AutostartSettingsSnapshot {
        schema_version: 1,
        window_generation,
        launch_at_login,
    })
}

fn save_with(
    backend: &impl AutostartBackend,
    window_generation: u64,
    launch_at_login: bool,
) -> Result<AutostartSettingsSnapshot, String> {
    let current = backend
        .is_enabled()
        .map_err(|source_error| crate::runtime_log::diagnostic_error(READ_FAILED, source_error))?;
    if current != launch_at_login {
        let result = if launch_at_login {
            backend.enable()
        } else {
            backend.disable()
        };
        result.map_err(|source_error| {
            crate::runtime_log::diagnostic_error(UPDATE_FAILED, source_error)
        })?;
    }
    let snapshot = snapshot_from(backend, window_generation).map_err(|source_error| {
        crate::runtime_log::diagnostic_error(VERIFY_FAILED, source_error)
    })?;
    if snapshot.launch_at_login != launch_at_login {
        return Err(VERIFY_FAILED.to_string());
    }
    Ok(snapshot)
}

pub fn snapshot<R: Runtime>(
    app: &AppHandle<R>,
    window_generation: u64,
) -> Result<AutostartSettingsSnapshot, String> {
    snapshot_from(app.autolaunch().inner(), window_generation)
}

pub fn save<R: Runtime>(
    app: &AppHandle<R>,
    window_generation: u64,
    launch_at_login: bool,
) -> Result<AutostartSettingsSnapshot, String> {
    save_with(app.autolaunch().inner(), window_generation, launch_at_login)
}

#[tauri::command]
pub(crate) fn settings_autostart_get(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
) -> Result<AutostartSettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    snapshot(&app_handle, shell.generation()?)
}

#[tauri::command]
pub(crate) fn settings_autostart_save(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    window_generation: u64,
    launch_at_login: bool,
) -> Result<AutostartSettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    save(&app_handle, window_generation, launch_at_login)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    struct FakeBackend {
        enabled: Mutex<bool>,
        fail_update: bool,
    }

    impl FakeBackend {
        fn new(enabled: bool) -> Self {
            Self {
                enabled: Mutex::new(enabled),
                fail_update: false,
            }
        }
    }

    impl AutostartBackend for FakeBackend {
        fn enable(&self) -> Result<(), String> {
            if self.fail_update {
                return Err("system refused autostart update".to_string());
            }
            *self.enabled.lock().unwrap() = true;
            Ok(())
        }

        fn disable(&self) -> Result<(), String> {
            if self.fail_update {
                return Err("system refused autostart update".to_string());
            }
            *self.enabled.lock().unwrap() = false;
            Ok(())
        }

        fn is_enabled(&self) -> Result<bool, String> {
            Ok(*self.enabled.lock().unwrap())
        }
    }

    #[test]
    fn snapshot_reports_the_platform_state() {
        let snapshot = snapshot_from(&FakeBackend::new(true), 7).unwrap();
        assert_eq!(snapshot.schema_version, 1);
        assert_eq!(snapshot.window_generation, 7);
        assert!(snapshot.launch_at_login);
    }

    #[test]
    fn save_changes_and_verifies_the_platform_state() {
        let backend = FakeBackend::new(false);
        let snapshot = save_with(&backend, 9, true).unwrap();
        assert!(snapshot.launch_at_login);
        assert_eq!(*backend.enabled.lock().unwrap(), true);
    }

    #[test]
    fn failed_update_keeps_the_previous_state() {
        let mut backend = FakeBackend::new(false);
        backend.fail_update = true;
        assert_eq!(
            save_with(&backend, 3, true).unwrap_err(),
            format!("{UPDATE_FAILED}: system refused autostart update")
        );
        assert!(!*backend.enabled.lock().unwrap());
    }
}
