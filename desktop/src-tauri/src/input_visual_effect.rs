#[cfg(not(any(windows, target_os = "macos")))]
use std::sync::Mutex;

use serde::Serialize;

use crate::character_appearance::{AppearanceValues, InputVisualEffectMode};

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct InputVisualEffectStatus {
    pub initialized: bool,
    pub effective_mode: InputVisualEffectMode,
    pub outcome: &'static str,
    pub error_code: Option<&'static str>,
}

impl InputVisualEffectStatus {
    #[cfg(any(test, not(target_os = "macos")))]
    pub const fn unavailable() -> Self {
        Self {
            initialized: false,
            effective_mode: InputVisualEffectMode::Solid,
            outcome: "unavailable",
            error_code: None,
        }
    }

    #[cfg(any(test, windows, target_os = "macos"))]
    pub const fn pending() -> Self {
        Self {
            initialized: false,
            effective_mode: InputVisualEffectMode::Solid,
            outcome: "pending",
            error_code: None,
        }
    }

    #[cfg(any(test, windows, target_os = "macos"))]
    pub const fn ready(mode: InputVisualEffectMode) -> Self {
        Self {
            initialized: true,
            effective_mode: mode,
            outcome: "ready",
            error_code: None,
        }
    }

    #[cfg(any(test, windows, target_os = "macos"))]
    pub const fn limited(mode: InputVisualEffectMode, code: &'static str) -> Self {
        Self {
            initialized: true,
            effective_mode: mode,
            outcome: "limited",
            error_code: Some(code),
        }
    }

    pub const fn failed(code: &'static str) -> Self {
        Self {
            initialized: false,
            effective_mode: InputVisualEffectMode::Solid,
            outcome: "degraded",
            error_code: Some(code),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InputVisualEffectSupport {
    pub gaussian_blur: bool,
    pub liquid_glass: bool,
}

impl InputVisualEffectSupport {
    pub const fn new(gaussian_blur: bool, liquid_glass: bool) -> Self {
        Self {
            gaussian_blur,
            liquid_glass,
        }
    }
}

pub struct InputVisualEffectState {
    #[cfg(windows)]
    backend: crate::windows_glass_poc::WindowsInputGlassState,
    #[cfg(target_os = "macos")]
    backend: crate::macos_input_glass::MacInputGlassState,
    #[cfg(not(any(windows, target_os = "macos")))]
    status: Mutex<InputVisualEffectStatus>,
}

impl InputVisualEffectState {
    pub fn from_environment() -> Self {
        Self {
            #[cfg(windows)]
            backend: crate::windows_glass_poc::WindowsInputGlassState::from_environment(),
            #[cfg(target_os = "macos")]
            backend: crate::macos_input_glass::MacInputGlassState::new(),
            #[cfg(not(any(windows, target_os = "macos")))]
            status: Mutex::new(InputVisualEffectStatus::unavailable()),
        }
    }

    pub fn support(&self) -> InputVisualEffectSupport {
        #[cfg(windows)]
        return InputVisualEffectSupport::new(true, true);
        #[cfg(target_os = "macos")]
        return self.backend.support();
        #[cfg(not(any(windows, target_os = "macos")))]
        return InputVisualEffectSupport::new(false, false);
    }

    pub fn status(&self) -> InputVisualEffectStatus {
        #[cfg(any(windows, target_os = "macos"))]
        return self.backend.status();
        #[cfg(not(any(windows, target_os = "macos")))]
        return self
            .status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| {
                InputVisualEffectStatus::failed("INPUT_VISUAL_EFFECT_STATE_UNAVAILABLE")
            });
    }

    pub fn install(&self, window: &tauri::WebviewWindow) {
        #[cfg(any(windows, target_os = "macos"))]
        self.backend.install(window);
        #[cfg(not(any(windows, target_os = "macos")))]
        let _ = window;
    }

    pub fn update_appearance(
        &self,
        window: &tauri::WebviewWindow,
        values: &AppearanceValues,
    ) -> Result<InputVisualEffectStatus, String> {
        #[cfg(windows)]
        {
            let _ = window;
            return self.backend.update_appearance(values);
        }
        #[cfg(target_os = "macos")]
        return self.backend.update_appearance(window, values);
        #[cfg(not(any(windows, target_os = "macos")))]
        {
            let _ = (window, values);
            Ok(self.status())
        }
    }

    pub fn update_control_surface(
        &self,
        window: &tauri::WebviewWindow,
        surface: &crate::window_geometry::ControlSurfaceLayout,
        application: &crate::window_geometry::LayoutApplication,
        previous_surface: Option<&crate::window_geometry::ControlSurfaceLayout>,
        transition: Option<crate::window_geometry::InputSurfaceTransition>,
    ) -> Result<(), String> {
        #[cfg(windows)]
        {
            let _ = window;
            return self.backend.update_control_surface(
                surface,
                application,
                previous_surface,
                transition,
            );
        }
        #[cfg(target_os = "macos")]
        return self.backend.update_control_surface(
            window,
            surface,
            application,
            previous_surface,
            transition,
        );
        #[cfg(not(any(windows, target_os = "macos")))]
        {
            let _ = (window, surface, application, previous_surface, transition);
            Ok(())
        }
    }

    pub fn teardown(&self, window: &tauri::WebviewWindow) {
        #[cfg(target_os = "macos")]
        self.backend.teardown(window);
        #[cfg(not(target_os = "macos"))]
        let _ = window;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_contract_separates_ready_limited_and_failure() {
        assert_eq!(
            InputVisualEffectStatus::unavailable().outcome,
            "unavailable"
        );
        assert!(!InputVisualEffectStatus::pending().initialized);
        assert_eq!(
            InputVisualEffectStatus::ready(InputVisualEffectMode::GaussianBlur).effective_mode,
            InputVisualEffectMode::GaussianBlur
        );
        let limited = InputVisualEffectStatus::limited(
            InputVisualEffectMode::Solid,
            "LIQUID_GLASS_REQUIRES_MACOS_26",
        );
        assert_eq!(limited.outcome, "limited");
        assert_eq!(limited.effective_mode, InputVisualEffectMode::Solid);
        assert_eq!(
            InputVisualEffectStatus::failed("GLASS_TEST").error_code,
            Some("GLASS_TEST")
        );
    }
}
