use std::{ffi::OsStr, sync::Mutex};

use serde::Serialize;

pub const ENABLE_ENV: &str = "SAKURA_WINDOWS_GLASS_POC";
pub const FORCE_FAILURE_ENV: &str = "SAKURA_WINDOWS_GLASS_POC_FORCE_FAILURE";

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WindowsGlassPocStatus {
    pub requested: bool,
    pub active: bool,
    pub outcome: &'static str,
    pub error_code: Option<&'static str>,
}

impl WindowsGlassPocStatus {
    const fn disabled() -> Self {
        Self {
            requested: false,
            active: false,
            outcome: "disabled",
            error_code: None,
        }
    }

    const fn pending() -> Self {
        Self {
            requested: true,
            active: false,
            outcome: "pending",
            error_code: None,
        }
    }

    const fn active() -> Self {
        Self {
            requested: true,
            active: true,
            outcome: "active",
            error_code: None,
        }
    }

    const fn failed(code: &'static str) -> Self {
        Self {
            requested: true,
            active: false,
            outcome: "failed",
            error_code: Some(code),
        }
    }
}

fn enabled_value(value: Option<&OsStr>) -> bool {
    value.and_then(OsStr::to_str).is_some_and(|value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "on"
        )
    })
}

pub struct WindowsGlassPocState {
    status: Mutex<WindowsGlassPocStatus>,
    #[cfg(windows)]
    layer: Mutex<Option<NativeGlassLayer>>,
    force_failure: bool,
}

impl WindowsGlassPocState {
    pub fn from_environment() -> Self {
        let requested = enabled_value(std::env::var_os(ENABLE_ENV).as_deref());
        Self {
            status: Mutex::new(if requested {
                WindowsGlassPocStatus::pending()
            } else {
                WindowsGlassPocStatus::disabled()
            }),
            #[cfg(windows)]
            layer: Mutex::new(None),
            force_failure: enabled_value(std::env::var_os(FORCE_FAILURE_ENV).as_deref()),
        }
    }

    pub fn status(&self) -> WindowsGlassPocStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| WindowsGlassPocStatus::failed("GLASS_STATE_UNAVAILABLE"))
    }

    pub fn install(&self, window: &tauri::WebviewWindow) {
        if !self.status().requested {
            return;
        }
        if self.force_failure {
            self.record_failure("GLASS_FORCED_FAILURE", "forced by the PoC failure switch");
            return;
        }

        #[cfg(windows)]
        match NativeGlassLayer::install(window) {
            Ok(layer) => match self.layer.lock() {
                Ok(mut slot) => {
                    *slot = Some(layer);
                    self.set_status(WindowsGlassPocStatus::active());
                    eprintln!("[windows-glass-poc] host backdrop visual is active");
                }
                Err(_) => self.record_failure(
                    "GLASS_STATE_UNAVAILABLE",
                    "native glass object store is unavailable",
                ),
            },
            Err(error) => self.record_failure(error.code, &error.detail),
        }

        #[cfg(not(windows))]
        self.record_failure(
            "GLASS_PLATFORM_UNSUPPORTED",
            "Windows glass PoC was requested on a non-Windows platform",
        );
    }

    fn set_status(&self, next: WindowsGlassPocStatus) {
        if let Ok(mut status) = self.status.lock() {
            *status = next;
        }
    }

    fn record_failure(&self, code: &'static str, detail: &str) {
        self.set_status(WindowsGlassPocStatus::failed(code));
        eprintln!("[windows-glass-poc] {code}: {detail}; continuing without native glass");
    }
}

#[cfg(windows)]
struct NativeGlassError {
    code: &'static str,
    detail: String,
}

#[cfg(windows)]
impl NativeGlassError {
    fn at(code: &'static str, error: impl std::fmt::Display) -> Self {
        Self {
            code,
            detail: error.to_string(),
        }
    }
}

#[cfg(windows)]
struct NativeGlassLayer {
    _compositor: windows::UI::Composition::Compositor,
    _target: windows::UI::Composition::Desktop::DesktopWindowTarget,
    _root: windows::UI::Composition::ContainerVisual,
    _backdrop_visual: windows::UI::Composition::SpriteVisual,
    _backdrop_brush: windows::UI::Composition::CompositionBackdropBrush,
    _tint_visual: windows::UI::Composition::SpriteVisual,
    _tint_brush: windows::UI::Composition::CompositionColorBrush,
}

#[cfg(windows)]
impl NativeGlassLayer {
    fn install(window: &tauri::WebviewWindow) -> Result<Self, NativeGlassError> {
        use windows::{
            core::Interface,
            Win32::System::WinRT::Composition::ICompositorDesktopInterop,
            UI::{Color, Composition::Compositor},
        };
        use windows_numerics::Vector2;

        let hwnd = window
            .hwnd()
            .map_err(|error| NativeGlassError::at("GLASS_HWND_UNAVAILABLE", error))?;
        let compositor = Compositor::new()
            .map_err(|error| NativeGlassError::at("GLASS_COMPOSITOR_CREATE_FAILED", error))?;
        let interop: ICompositorDesktopInterop = compositor
            .cast()
            .map_err(|error| NativeGlassError::at("GLASS_DESKTOP_INTEROP_UNAVAILABLE", error))?;
        let target = unsafe { interop.CreateDesktopWindowTarget(hwnd, false) }
            .map_err(|error| NativeGlassError::at("GLASS_TARGET_CREATE_FAILED", error))?;

        let fill = Vector2 { X: 1.0, Y: 1.0 };
        let root = compositor
            .CreateContainerVisual()
            .map_err(|error| NativeGlassError::at("GLASS_ROOT_CREATE_FAILED", error))?;
        root.SetRelativeSizeAdjustment(fill)
            .map_err(|error| NativeGlassError::at("GLASS_ROOT_SIZE_FAILED", error))?;

        let backdrop_brush = compositor
            .CreateHostBackdropBrush()
            .map_err(|error| NativeGlassError::at("GLASS_HOST_BACKDROP_UNAVAILABLE", error))?;
        let backdrop_visual = compositor
            .CreateSpriteVisual()
            .map_err(|error| NativeGlassError::at("GLASS_VISUAL_CREATE_FAILED", error))?;
        backdrop_visual
            .SetRelativeSizeAdjustment(fill)
            .map_err(|error| NativeGlassError::at("GLASS_VISUAL_SIZE_FAILED", error))?;
        backdrop_visual
            .SetBrush(&backdrop_brush)
            .map_err(|error| NativeGlassError::at("GLASS_BRUSH_ATTACH_FAILED", error))?;
        root.Children()
            .and_then(|children| children.InsertAtBottom(&backdrop_visual))
            .map_err(|error| NativeGlassError::at("GLASS_VISUAL_ATTACH_FAILED", error))?;

        // A faint Sakura tint makes a successful native layer obvious without obscuring the
        // live host backdrop. The WebView remains responsible for rounded regions and blur CSS.
        let tint_brush = compositor
            .CreateColorBrushWithColor(Color {
                A: 34,
                R: 255,
                G: 225,
                B: 239,
            })
            .map_err(|error| NativeGlassError::at("GLASS_TINT_CREATE_FAILED", error))?;
        let tint_visual = compositor
            .CreateSpriteVisual()
            .map_err(|error| NativeGlassError::at("GLASS_TINT_VISUAL_CREATE_FAILED", error))?;
        tint_visual
            .SetRelativeSizeAdjustment(fill)
            .map_err(|error| NativeGlassError::at("GLASS_TINT_SIZE_FAILED", error))?;
        tint_visual
            .SetBrush(&tint_brush)
            .map_err(|error| NativeGlassError::at("GLASS_TINT_ATTACH_FAILED", error))?;
        root.Children()
            .and_then(|children| children.InsertAtTop(&tint_visual))
            .map_err(|error| NativeGlassError::at("GLASS_TINT_INSERT_FAILED", error))?;

        target
            .SetRoot(&root)
            .map_err(|error| NativeGlassError::at("GLASS_ROOT_ATTACH_FAILED", error))?;

        Ok(Self {
            _compositor: compositor,
            _target: target,
            _root: root,
            _backdrop_visual: backdrop_visual,
            _backdrop_brush: backdrop_brush,
            _tint_visual: tint_visual,
            _tint_brush: tint_brush,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_truthy_values_enable_the_poc() {
        for value in ["1", "true", "TRUE", " on "] {
            assert!(enabled_value(Some(OsStr::new(value))), "{value}");
        }
    }

    #[test]
    fn missing_or_ambiguous_values_leave_the_poc_disabled() {
        assert!(!enabled_value(None));
        for value in ["", "0", "false", "yes", "enabled"] {
            assert!(!enabled_value(Some(OsStr::new(value))), "{value}");
        }
    }

    #[test]
    fn status_contract_separates_request_activation_and_failure() {
        assert_eq!(WindowsGlassPocStatus::disabled().outcome, "disabled");
        assert!(!WindowsGlassPocStatus::pending().active);
        assert!(WindowsGlassPocStatus::active().active);
        assert_eq!(
            WindowsGlassPocStatus::failed("GLASS_TEST").error_code,
            Some("GLASS_TEST")
        );
    }
}
