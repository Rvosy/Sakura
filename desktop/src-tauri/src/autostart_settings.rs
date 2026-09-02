use serde::Serialize;
use tauri::{AppHandle, Runtime};
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
    fn enable(&self) -> Result<(), ()>;
    fn disable(&self) -> Result<(), ()>;
    fn is_enabled(&self) -> Result<bool, ()>;
}

impl AutostartBackend for AutoLaunchManager {
    fn enable(&self) -> Result<(), ()> {
        AutoLaunchManager::enable(self).map_err(|_| ())
    }

    fn disable(&self) -> Result<(), ()> {
        AutoLaunchManager::disable(self).map_err(|_| ())
    }

    fn is_enabled(&self) -> Result<bool, ()> {
        AutoLaunchManager::is_enabled(self).map_err(|_| ())
    }
}

fn snapshot_from(
    backend: &impl AutostartBackend,
    window_generation: u64,
) -> Result<AutostartSettingsSnapshot, String> {
    let launch_at_login = backend.is_enabled().map_err(|_| READ_FAILED.to_string())?;
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
    let current = backend.is_enabled().map_err(|_| READ_FAILED.to_string())?;
    if current != launch_at_login {
        let result = if launch_at_login {
            backend.enable()
        } else {
            backend.disable()
        };
        result.map_err(|_| UPDATE_FAILED.to_string())?;
    }
    let snapshot =
        snapshot_from(backend, window_generation).map_err(|_| VERIFY_FAILED.to_string())?;
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
        fn enable(&self) -> Result<(), ()> {
            if self.fail_update {
                return Err(());
            }
            *self.enabled.lock().unwrap() = true;
            Ok(())
        }

        fn disable(&self) -> Result<(), ()> {
            if self.fail_update {
                return Err(());
            }
            *self.enabled.lock().unwrap() = false;
            Ok(())
        }

        fn is_enabled(&self) -> Result<bool, ()> {
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
        assert_eq!(save_with(&backend, 3, true).unwrap_err(), UPDATE_FAILED);
        assert!(!*backend.enabled.lock().unwrap());
    }
}
