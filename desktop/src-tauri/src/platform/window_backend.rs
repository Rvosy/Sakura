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

#[cfg(any(target_os = "linux", test))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LinuxBoundsRequest {
    X11MoveResize {
        x: i32,
        y: i32,
        width: i32,
        height: i32,
    },
    WaylandResizeOnly {
        width: i32,
        height: i32,
    },
}

#[cfg(any(target_os = "linux", test))]
fn linux_bounds_request(
    placement: &PhysicalPlacement,
    scale_factor: f64,
    native_wayland: bool,
) -> Result<LinuxBoundsRequest, &'static str> {
    if !scale_factor.is_finite() || scale_factor <= 0.0 {
        return Err("Linux GTK scale must be positive and finite");
    }
    let position =
        tauri::PhysicalPosition::new(placement.x, placement.y).to_logical::<i32>(scale_factor);
    let size =
        tauri::PhysicalSize::new(placement.width, placement.height).to_logical::<i32>(scale_factor);
    if size.width <= 0 || size.height <= 0 {
        return Err("Linux GTK window size must be positive");
    }
    if native_wayland {
        Ok(LinuxBoundsRequest::WaylandResizeOnly {
            width: size.width,
            height: size.height,
        })
    } else {
        Ok(LinuxBoundsRequest::X11MoveResize {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
        })
    }
}

#[cfg(any(target_os = "macos", test))]
fn macos_frame_for_physical_placement(
    current_frame: [f64; 4],
    current_physical_position: [i32; 2],
    placement: &PhysicalPlacement,
    backing_scale: f64,
) -> Result<[f64; 4], &'static str> {
    if !backing_scale.is_finite() || backing_scale <= 0.0 {
        return Err("macOS backing scale must be positive and finite");
    }
    let [current_x, current_y, current_width, current_height] = current_frame;
    if !current_x.is_finite()
        || !current_y.is_finite()
        || !current_width.is_finite()
        || !current_height.is_finite()
    {
        return Err("macOS current frame must be finite");
    }
    let delta_x = f64::from(placement.x) - f64::from(current_physical_position[0]);
    let delta_y = f64::from(placement.y) - f64::from(current_physical_position[1]);
    let width = f64::from(placement.width) / backing_scale;
    let height = f64::from(placement.height) / backing_scale;
    let top = current_y + current_height - delta_y / backing_scale;
    Ok([
        current_x + delta_x / backing_scale,
        top - height,
        width,
        height,
    ])
}

#[cfg(target_os = "macos")]
fn macos_atomic_frame(
    window: &tauri::WebviewWindow,
    placement: &PhysicalPlacement,
) -> Result<(), String> {
    fn apply(window: &tauri::WebviewWindow, placement: PhysicalPlacement) -> Result<(), String> {
        use objc2_app_kit::NSWindow;
        use objc2_foundation::{NSPoint, NSRect, NSSize};

        let current_position = window
            .outer_position()
            .map_err(|error| format!("failed to read current macOS window position: {error}"))?;
        let raw_window = window
            .ns_window()
            .map_err(|error| format!("failed to access macOS window: {error}"))?;
        let ns_window = unsafe { &*raw_window.cast::<NSWindow>() };
        let backing_scale = ns_window.backingScaleFactor() as f64;
        let current = ns_window.frame();
        let [x, y, width, height] = macos_frame_for_physical_placement(
            [
                current.origin.x,
                current.origin.y,
                current.size.width,
                current.size.height,
            ],
            [current_position.x, current_position.y],
            &placement,
            backing_scale,
        )?;
        let frame = NSRect::new(NSPoint::new(x, y), NSSize::new(width, height));
        // `display=true` forces AppKit to paint the resized surface before WebKit has consumed
        // the precommitted stage offset, exposing one stale frame at gesture begin/end.
        ns_window.setFrame_display(frame, false);
        Ok(())
    }

    let placement = *placement;
    if objc2::MainThreadMarker::new().is_some() {
        return apply(window, placement);
    }
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    let main_window = window.clone();
    window
        .run_on_main_thread(move || {
            let _ = sender.send(apply(&main_window, placement));
        })
        .map_err(|error| format!("failed to dispatch atomic macOS window frame: {error}"))?;
    receiver
        .recv_timeout(std::time::Duration::from_secs(5))
        .map_err(|_| "timed out applying atomic macOS window frame".to_string())?
}

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
    use windows::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMNCRP_DISABLED, DWMWA_NCRENDERING_POLICY,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongW, SetWindowLongW, SetWindowPos, GWL_STYLE, SWP_FRAMECHANGED, SWP_NOACTIVATE,
        SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE, SWP_NOZORDER,
    };

    let overall_started = std::time::Instant::now();
    crate::interaction_latency::stage("borderless-enforcement-start");
    let hwnd_started = std::time::Instant::now();
    let hwnd = window
        .hwnd()
        .map_err(|error| map_error("prepare_window", error.to_string()))?;
    crate::interaction_latency::stage_elapsed("borderless-hwnd-return", hwnd_started);
    unsafe {
        let style_read_started = std::time::Instant::now();
        SetLastError(WIN32_ERROR(0));
        let raw_style = GetWindowLongW(hwnd, GWL_STYLE);
        let read_error = GetLastError();
        crate::interaction_latency::stage_elapsed(
            "borderless-style-read-return",
            style_read_started,
        );
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
        let style_changed = borderless != style;
        if style_changed {
            let style_write_started = std::time::Instant::now();
            SetLastError(WIN32_ERROR(0));
            let previous = SetWindowLongW(hwnd, GWL_STYLE, borderless as i32);
            let error = GetLastError();
            crate::interaction_latency::stage_elapsed(
                "borderless-style-write-return",
                style_write_started,
            );
            if previous == 0 && error != WIN32_ERROR(0) {
                return Err(map_error(
                    "prepare_window",
                    format!(
                        "failed to set borderless window style: Win32 error {}",
                        error.0
                    ),
                ));
            }
            // SWP_FRAMECHANGED synchronously drives non-client recalculation and repaint. On the
            // large stable WebView envelope it is visibly expensive, so never issue it merely to
            // re-verify an already-borderless window at pointer-down or drag completion.
            let frame_changed_started = std::time::Instant::now();
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
            crate::interaction_latency::stage_elapsed(
                "borderless-framechanged-setwindowpos-return",
                frame_changed_started,
            );
        }
        let style_verify_started = std::time::Instant::now();
        SetLastError(WIN32_ERROR(0));
        let raw_verified = GetWindowLongW(hwnd, GWL_STYLE);
        let verify_error = GetLastError();
        crate::interaction_latency::stage_elapsed(
            "borderless-style-verify-return",
            style_verify_started,
        );
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
        let non_client_policy = DWMNCRP_DISABLED;
        let dwm_started = std::time::Instant::now();
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_NCRENDERING_POLICY,
            (&raw const non_client_policy).cast(),
            u32::try_from(std::mem::size_of_val(&non_client_policy)).unwrap_or(u32::MAX),
        )
        .map_err(|error| {
            map_error(
                "prepare_window",
                format!("failed to disable the native DWM shadow: {error}"),
            )
        })?;
        crate::interaction_latency::stage_elapsed("borderless-dwm-return", dwm_started);
    }
    crate::interaction_latency::stage_elapsed("borderless-enforcement-return", overall_started);
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
            let decorations_started = std::time::Instant::now();
            window
                .set_decorations(false)
                .map_err(|error| map_error("prepare_window", error.to_string()))?;
            crate::interaction_latency::stage_elapsed(
                "tauri-set-decorations-return",
                decorations_started,
            );
            let shadow_started = std::time::Instant::now();
            window
                .set_shadow(false)
                .map_err(|error| map_error("prepare_window", error.to_string()))?;
            crate::interaction_latency::stage_elapsed("tauri-set-shadow-return", shadow_started);
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
            let set_window_pos_started = std::time::Instant::now();
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
            crate::interaction_latency::stage_elapsed(
                "apply-bounds-setwindowpos-return",
                set_window_pos_started,
            );
            Ok(())
        }

        #[cfg(target_os = "macos")]
        {
            macos_atomic_frame(window, placement).map_err(|error| map_error("apply_bounds", error))
        }

        #[cfg(target_os = "linux")]
        {
            use gtk::prelude::{GtkWindowExt, WidgetExt};

            let gtk_window = window
                .gtk_window()
                .map_err(|error| map_error("apply_bounds", error.to_string()))?;
            let request = linux_bounds_request(
                placement,
                f64::from(gtk_window.scale_factor()),
                crate::window_geometry::native_wayland_session(),
            )
            .map_err(|error| map_error("apply_bounds", error))?;
            match request {
                LinuxBoundsRequest::X11MoveResize {
                    x,
                    y,
                    width,
                    height,
                } => gtk_window
                    .window()
                    .ok_or_else(|| native_failure("apply_bounds", "GTK surface is unavailable"))?
                    .move_resize(x, y, width, height),
                LinuxBoundsRequest::WaylandResizeOnly { width, height } => {
                    gtk_window.resize(width, height);
                }
            }
            Ok(())
        }

        #[cfg(all(not(windows), not(target_os = "macos"), not(target_os = "linux")))]
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
        #[cfg(any(windows, target_os = "macos", target_os = "linux"))]
        {
            window_interaction::apply_native_hit_regions(window, regions)
                .map_err(|error| map_error("apply_hit_regions", error))
        }

        #[cfg(all(not(windows), not(target_os = "macos"), not(target_os = "linux")))]
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

    fn relax_hit_regions(&self, window: &tauri::WebviewWindow) -> PlatformResult<()> {
        #[cfg(windows)]
        {
            window_interaction::relax_native_hit_regions(window)
                .map_err(|error| map_error("relax_hit_regions", error))
        }

        #[cfg(target_os = "macos")]
        {
            let _ = window;
            Err(native_failure(
                "relax_hit_regions",
                "macOS requires precise cursor routing during scale preview",
            ))
        }

        #[cfg(all(not(windows), not(target_os = "macos")))]
        {
            window
                .set_ignore_cursor_events(false)
                .map_err(|error| map_error("relax_hit_regions", error.to_string()))
        }
    }

    fn start_drag(&self, window: &tauri::WebviewWindow) -> PlatformResult<NativeDragCompletion> {
        // A native move loop is a non-client operation even for a frameless
        // window. Reassert the borderless invariant on both sides so a frame
        // refresh cannot leave a caption visible after the pointer is released.
        #[cfg(windows)]
        {
            let started = std::time::Instant::now();
            enforce_native_borderless_window(window)?;
            crate::interaction_latency::stage_elapsed("drag-borderless-before-return", started);
        }
        let native_drag_started = std::time::Instant::now();
        let completion = window_interaction::start_native_drag(window)
            .map_err(|error| map_error("start_drag", error))?;
        crate::interaction_latency::stage_elapsed("start-native-drag-return", native_drag_started);
        #[cfg(windows)]
        {
            let started = std::time::Instant::now();
            enforce_native_borderless_window(window)?;
            crate::interaction_latency::stage_elapsed("drag-borderless-after-return", started);
        }
        Ok(completion)
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

    #[test]
    fn macos_atomic_frame_preserves_the_requested_physical_top_left() {
        let placement = PhysicalPlacement {
            x: 80,
            y: 160,
            width: 800,
            height: 600,
        };
        assert_eq!(
            macos_frame_for_physical_placement(
                [50.0, 400.0, 300.0, 200.0],
                [100, 200],
                &placement,
                2.0,
            )
            .unwrap(),
            [40.0, 320.0, 400.0, 300.0]
        );
    }

    #[test]
    fn linux_bounds_use_one_x11_configuration_and_never_position_native_wayland() {
        let placement = PhysicalPlacement {
            x: -150,
            y: 75,
            width: 900,
            height: 600,
        };
        assert_eq!(
            linux_bounds_request(&placement, 1.5, false).unwrap(),
            LinuxBoundsRequest::X11MoveResize {
                x: -100,
                y: 50,
                width: 600,
                height: 400,
            }
        );
        assert_eq!(
            linux_bounds_request(&placement, 1.5, true).unwrap(),
            LinuxBoundsRequest::WaylandResizeOnly {
                width: 600,
                height: 400,
            }
        );
        assert!(linux_bounds_request(&placement, 0.0, false).is_err());
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
