use std::sync::Arc;

use tauri::{AppHandle, Emitter, State};

use crate::brain_host::{
    BrainHostLaunchConfig, BrainHostStatus, BrainHostSupervisor, StatusCallback,
};

pub const BRAIN_STATUS_EVENT: &str = "sakura://brain-status";

pub struct DesktopAppState {
    brain: BrainHostSupervisor,
}

impl DesktopAppState {
    pub fn start(app: AppHandle) -> Self {
        let callback: StatusCallback = Arc::new(move |status| {
            let _ = app.emit(BRAIN_STATUS_EVENT, status);
        });
        let brain =
            BrainHostSupervisor::start(BrainHostLaunchConfig::for_current_app(), Some(callback));
        Self { brain }
    }

    pub fn shutdown(&self) {
        self.brain.shutdown();
    }

    pub fn brain_status(&self) -> BrainHostStatus {
        self.brain.status()
    }
}

#[tauri::command]
pub fn brain_status(state: State<'_, DesktopAppState>) -> BrainHostStatus {
    state.brain_status()
}
