use crate::{
    window_geometry::PhysicalPlacement,
    window_interaction::{self, NativeDragCompletion, PhysicalHitRegions},
};

use super::{
    PlatformError, PlatformErrorCategory, PlatformResult, PlatformService, RetryAdvice,
    WindowInteractionBackend,
};

/// Native window operations behind the shared placement/hit-test model.
///
/// Windows delegates the already-validated Win32 region and drag operations to
/// `window_interaction`. macOS/Linux keep hit routing in the WebView and use
/// the native window for bounds, visibility, focus and drag.
pub struct NativeWindowInteractionBackend;

fn native_failure(operation: &'static str, message: impl Into<String>) -> PlatformError {
    PlatformError::new(
        PlatformService::WindowInteraction,
        PlatformErrorCategory::NativeFailure,
        operation,
        RetryAdvice::AfterUserAction,
        message,
    )
}

fn map_error(operation: &'static str, error: impl Into<String>) -> PlatformError {
    native_failure(operation, error)
}

impl WindowInteractionBackend for NativeWindowInteractionBackend {
    fn apply_bounds(
        &self,
        window: &tauri::WebviewWindow,
        placement: &PhysicalPlacement,
    ) -> PlatformResult<()> {
        #[cfg(windows)]
        {
            use windows::Win32::UI::WindowsAndMessaging::{
                SetWindowPos, SWP_NOACTIVATE, SWP_NOOWNERZORDER, SWP_NOZORDER,
            };

            let hwnd = window
                .hwnd()
                .map_err(|error| map_error("apply_bounds", error.to_string()))?;
            let width = i32::try_from(placement.width)
                .map_err(|_| native_failure("apply_bounds", "window width exceeds Win32 limits"))?;
            let height = i32::try_from(placement.height).map_err(|_| {
                native_failure("apply_bounds", "window height exceeds Win32 limits")
            })?;
            unsafe {
                SetWindowPos(
                    hwnd,
                    None,
                    placement.x,
                    placement.y,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOZORDER,
                )
                .map_err(|error| map_error("apply_bounds", error.to_string()))?;
            }
            Ok(())
        }

        #[cfg(not(windows))]
        {
            use tauri::{PhysicalPosition, PhysicalSize};
            window
                .set_size(PhysicalSize::new(placement.width, placement.height))
                .map_err(|error| map_error("apply_bounds", error.to_string()))?;
            window
                .set_position(PhysicalPosition::new(placement.x, placement.y))
                .map_err(|error| map_error("apply_bounds", error.to_string()))
        }
    }

    fn apply_hit_regions(
        &self,
        window: &tauri::WebviewWindow,
        regions: &PhysicalHitRegions,
    ) -> PlatformResult<()> {
        #[cfg(windows)]
        {
            window_interaction::apply_native_hit_regions(window, regions)
                .map_err(|error| map_error("apply_hit_regions", error))
        }

        #[cfg(not(windows))]
        {
            // POSIX hit routing is handled by WebView pointer-events and the
            // shared model. Keep the native surface interactive as a safe
            // fallback rather than permanently disabling the pet window.
            let _ = regions;
            window
                .set_ignore_cursor_events(false)
                .map_err(|error| map_error("apply_hit_regions", error.to_string()))
        }
    }

    fn restore_full_hit_region(&self, window: &tauri::WebviewWindow) -> PlatformResult<()> {
        #[cfg(windows)]
        {
            window_interaction::restore_full_native_hit_region(window)
                .map_err(|error| map_error("restore_full_hit_region", error))
        }

        #[cfg(not(windows))]
        {
            window
                .set_ignore_cursor_events(false)
                .map_err(|error| map_error("restore_full_hit_region", error.to_string()))
        }
    }

    fn start_drag(&self, window: &tauri::WebviewWindow) -> PlatformResult<NativeDragCompletion> {
        window_interaction::start_native_drag(window)
            .map_err(|error| map_error("start_drag", error))
    }

    fn set_visible(&self, window: &tauri::WebviewWindow, visible: bool) -> PlatformResult<()> {
        if visible {
            window
                .show()
                .map_err(|error| map_error("set_visible", error.to_string()))?;
            window
                .set_focus()
                .map_err(|error| map_error("set_visible", error.to_string()))
        } else {
            window
                .hide()
                .map_err(|error| map_error("set_visible", error.to_string()))
        }
    }

    fn focus_text_input(&self, window: &tauri::WebviewWindow) -> PlatformResult<()> {
        window
            .set_focus()
            .map_err(|error| map_error("focus_text_input", error.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_is_concrete_on_every_formal_target() {
        let _backend = NativeWindowInteractionBackend;
        assert!(super::super::current_platform_target().is_some());
    }

    #[test]
    fn native_failures_use_stable_window_service_codes() {
        let error = native_failure("apply_bounds", "test failure");
        assert_eq!(
            error.stable_code(),
            "platform.window_interaction.native_failure"
        );
        assert_eq!(error.retry, RetryAdvice::AfterUserAction);
    }
}
