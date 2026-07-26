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

#[cfg(windows)]
fn borderless_window_style(style: u32) -> u32 {
    use windows::Win32::UI::WindowsAndMessaging::{
        WS_CAPTION, WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_POPUP, WS_SYSMENU, WS_THICKFRAME,
    };

    (style & !(WS_CAPTION.0 | WS_THICKFRAME.0 | WS_SYSMENU.0 | WS_MINIMIZEBOX.0 | WS_MAXIMIZEBOX.0))
        | WS_POPUP.0
}

#[cfg(windows)]
fn enforce_native_borderless_window(window: &tauri::WebviewWindow) -> PlatformResult<()> {
    use windows::Win32::Foundation::{GetLastError, SetLastError, WIN32_ERROR};
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongW, SetWindowLongW, SetWindowPos, GWL_STYLE, SWP_FRAMECHANGED, SWP_NOACTIVATE,
        SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE, SWP_NOZORDER,
    };

    let hwnd = window
        .hwnd()
        .map_err(|error| map_error("prepare_window", error.to_string()))?;
    unsafe {
        SetLastError(WIN32_ERROR(0));
        let raw_style = GetWindowLongW(hwnd, GWL_STYLE);
        let read_error = GetLastError();
        if raw_style == 0 && read_error != WIN32_ERROR(0) {
            return Err(map_error(
                "prepare_window",
                format!(
                    "failed to read native window style: Win32 error {}",
                    read_error.0
                ),
            ));
        }
        let style = raw_style as u32;
        let borderless = borderless_window_style(style);
        if borderless != style {
            SetLastError(WIN32_ERROR(0));
            let previous = SetWindowLongW(hwnd, GWL_STYLE, borderless as i32);
            let error = GetLastError();
            if previous == 0 && error != WIN32_ERROR(0) {
                return Err(map_error(
                    "prepare_window",
                    format!(
                        "failed to set borderless window style: Win32 error {}",
                        error.0
                    ),
                ));
            }
        }
        SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED
                | SWP_NOACTIVATE
                | SWP_NOMOVE
                | SWP_NOOWNERZORDER
                | SWP_NOSIZE
                | SWP_NOZORDER,
        )
        .map_err(|error| map_error("prepare_window", error.to_string()))?;
        SetLastError(WIN32_ERROR(0));
        let raw_verified = GetWindowLongW(hwnd, GWL_STYLE);
        let verify_error = GetLastError();
        if raw_verified == 0 && verify_error != WIN32_ERROR(0) {
            return Err(map_error(
                "prepare_window",
                format!(
                    "failed to verify native window style: Win32 error {}",
                    verify_error.0
                ),
            ));
        }
        let verified = raw_verified as u32;
        if borderless_window_style(verified) != verified {
            return Err(map_error(
                "prepare_window",
                format!("native frame bits survived style refresh: 0x{verified:08x}"),
            ));
        }
    }
    Ok(())
}

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
    fn prepare_window(&self, window: &tauri::WebviewWindow) -> PlatformResult<()> {
        #[cfg(windows)]
        {
            // Tauri owns the portable declaration; Win32 readback makes the Windows
            // invariant observable before SetWindowRgn can expose non-client pixels.
            window
                .set_decorations(false)
                .map_err(|error| map_error("prepare_window", error.to_string()))?;
            window
                .set_shadow(false)
                .map_err(|error| map_error("prepare_window", error.to_string()))?;
            enforce_native_borderless_window(window)
        }

        #[cfg(not(windows))]
        {
            let _ = window;
            Ok(())
        }
    }

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
            self.prepare_window(window)?;
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

    #[cfg(windows)]
    #[test]
    fn borderless_style_removes_every_caption_bit_and_keeps_popup_semantics() {
        use windows::Win32::UI::WindowsAndMessaging::{
            WS_CAPTION, WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_POPUP, WS_SYSMENU, WS_THICKFRAME,
            WS_VISIBLE,
        };

        let decorated = WS_CAPTION.0
            | WS_THICKFRAME.0
            | WS_SYSMENU.0
            | WS_MINIMIZEBOX.0
            | WS_MAXIMIZEBOX.0
            | WS_VISIBLE.0;
        let result = borderless_window_style(decorated);
        assert_eq!(result & WS_CAPTION.0, 0);
        assert_eq!(result & WS_THICKFRAME.0, 0);
        assert_eq!(result & WS_SYSMENU.0, 0);
        assert_eq!(result & WS_MINIMIZEBOX.0, 0);
        assert_eq!(result & WS_MAXIMIZEBOX.0, 0);
        assert_ne!(result & WS_POPUP.0, 0);
        assert_ne!(result & WS_VISIBLE.0, 0);
    }

    #[cfg(windows)]
    #[test]
    fn borderless_style_is_idempotent_and_preserves_unrelated_bits() {
        use windows::Win32::UI::WindowsAndMessaging::{WS_DISABLED, WS_POPUP, WS_VISIBLE};

        let borderless = WS_POPUP.0 | WS_VISIBLE.0 | WS_DISABLED.0;
        assert_eq!(borderless_window_style(borderless), borderless);
    }
}
