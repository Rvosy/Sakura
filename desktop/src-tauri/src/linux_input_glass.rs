//! X11 input-bar blur for sessions where the compositor has no per-window blur.
//!
//! WebKitGTK `backdrop-filter` only blurs pixels inside the web view, so the
//! composer samples the root window after its own background is cleared. The
//! sample is taken once the bar stops moving, then drawn back as a CSS image.

use std::collections::HashSet;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::character_appearance::{AppearanceValues, InputVisualEffectMode};
use crate::input_visual_effect::{InputVisualEffectStatus, InputVisualEffectSupport};
use crate::runtime_log::{RuntimeLogEvent, RuntimeLogService, Severity};
use crate::window_geometry::{ControlSurfaceLayout, LayoutApplication};

const REFRESH_DELAY: Duration = Duration::from_millis(140);
const CAPTURE_DELAY: Duration = Duration::from_millis(100);
const NOT_READY_RETRIES: u8 = 8;
const BLUR_DIVISOR: i32 = 8;
const MAX_PAINT_WIDTH: i32 = 640;

const PREPARE_CAPTURE_JS: &str = r#"(() => {
  const root = document.documentElement;
  root.dataset.linuxInputCapturing = "1";
  root.style.removeProperty("--linux-input-desktop");
})()"#;

const CANCEL_CAPTURE_JS: &str = r#"(() => {
  delete document.documentElement.dataset.linuxInputCapturing;
})()"#;

const CLEAR_PAINT_JS: &str = r#"(() => {
  const root = document.documentElement;
  delete root.dataset.linuxInputCapturing;
  root.style.removeProperty("--linux-input-desktop");
})()"#;

type WindowRect = (i32, i32, i32, i32);

enum CaptureError {
    NotReady,
    Failed {
        code: &'static str,
        detail: &'static str,
    },
}

struct GlassInner {
    mode: InputVisualEffectMode,
    input_visible: bool,
    window_rect: Option<WindowRect>,
    painted_rect: Option<WindowRect>,
    failed_rect: Option<WindowRect>,
    generation: u64,
}

struct Shared {
    status: Mutex<InputVisualEffectStatus>,
    inner: Mutex<GlassInner>,
    runtime_log: RuntimeLogService,
    reported: Mutex<HashSet<&'static str>>,
}

pub(crate) struct LinuxInputGlassState {
    shared: Arc<Shared>,
}

impl LinuxInputGlassState {
    pub(crate) fn new(runtime_log: RuntimeLogService) -> Self {
        Self {
            shared: Arc::new(Shared {
                status: Mutex::new(InputVisualEffectStatus::unavailable()),
                inner: Mutex::new(GlassInner {
                    mode: InputVisualEffectMode::Solid,
                    input_visible: false,
                    window_rect: None,
                    painted_rect: None,
                    failed_rect: None,
                    generation: 0,
                }),
                runtime_log,
                reported: Mutex::new(HashSet::new()),
            }),
        }
    }

    pub(crate) fn support(&self) -> InputVisualEffectSupport {
        InputVisualEffectSupport::new(x11_capture_available(), false)
    }

    pub(crate) fn status(&self) -> InputVisualEffectStatus {
        self.shared
            .status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| {
                InputVisualEffectStatus::failed("INPUT_VISUAL_EFFECT_STATE_UNAVAILABLE")
            })
    }

    pub(crate) fn update_appearance(
        &self,
        window: &tauri::WebviewWindow,
        values: &AppearanceValues,
    ) -> Result<InputVisualEffectStatus, String> {
        let status = match values.visual_effect_mode {
            InputVisualEffectMode::Solid => {
                self.set_mode(InputVisualEffectMode::Solid);
                self.clear_paint(window);
                InputVisualEffectStatus::ready(InputVisualEffectMode::Solid)
            }
            InputVisualEffectMode::LiquidGlass => {
                self.set_mode(InputVisualEffectMode::LiquidGlass);
                self.clear_paint(window);
                report(
                    &self.shared,
                    "LIQUID_GLASS_UNSUPPORTED_ON_LINUX",
                    "Linux 液态玻璃不可用，已回退为纯色",
                    "当前平台不支持液态玻璃",
                );
                InputVisualEffectStatus::failed("LIQUID_GLASS_UNSUPPORTED_ON_LINUX")
            }
            InputVisualEffectMode::GaussianBlur if !x11_capture_available() => {
                self.set_mode(InputVisualEffectMode::GaussianBlur);
                self.clear_paint(window);
                report(
                    &self.shared,
                    "LINUX_INPUT_BLUR_REQUIRES_X11",
                    "Linux 高斯模糊不可用，已回退为纯色",
                    "高斯模糊需要 X11 会话",
                );
                InputVisualEffectStatus::failed("LINUX_INPUT_BLUR_REQUIRES_X11")
            }
            InputVisualEffectMode::GaussianBlur => {
                let switched = self
                    .shared
                    .inner
                    .lock()
                    .map(|mut inner| {
                        let switched = inner.mode != InputVisualEffectMode::GaussianBlur;
                        inner.mode = InputVisualEffectMode::GaussianBlur;
                        if switched {
                            inner.painted_rect = None;
                            inner.failed_rect = None;
                        }
                        switched
                    })
                    .unwrap_or(true);
                if switched {
                    self.schedule(window);
                }
                InputVisualEffectStatus::ready(InputVisualEffectMode::GaussianBlur)
            }
        };
        self.set_status(status.clone());
        Ok(status)
    }

    pub(crate) fn update_control_surface(
        &self,
        window: &tauri::WebviewWindow,
        surface: &ControlSurfaceLayout,
        application: &LayoutApplication,
        _previous_surface: Option<&ControlSurfaceLayout>,
        _transition: Option<crate::window_geometry::InputSurfaceTransition>,
    ) -> Result<(), String> {
        let rect = linux_input_window_rect(
            surface.input_rect,
            [application.active_bounds[0], application.active_bounds[1]],
            application.content_scale,
        );
        let mut clear = !surface.input_visible;
        if let Ok(mut inner) = self.shared.inner.lock() {
            inner.input_visible = surface.input_visible;
            match rect {
                Ok(rect) => {
                    if inner.window_rect != Some(rect) {
                        inner.failed_rect = None;
                    }
                    inner.window_rect = Some(rect);
                }
                Err(_) => {
                    inner.window_rect = None;
                    clear = true;
                }
            }
        }
        if clear {
            self.clear_paint(window);
            return Ok(());
        }
        self.schedule(window);
        Ok(())
    }

    pub(crate) fn teardown(&self, window: &tauri::WebviewWindow) {
        self.set_mode(InputVisualEffectMode::Solid);
        self.clear_paint(window);
        self.set_status(InputVisualEffectStatus::unavailable());
    }

    fn set_status(&self, status: InputVisualEffectStatus) {
        if let Ok(mut current) = self.shared.status.lock() {
            *current = status;
        }
    }

    fn set_mode(&self, mode: InputVisualEffectMode) {
        if let Ok(mut inner) = self.shared.inner.lock() {
            inner.mode = mode;
        }
    }

    fn clear_paint(&self, window: &tauri::WebviewWindow) {
        if let Ok(mut inner) = self.shared.inner.lock() {
            inner.generation = inner.generation.wrapping_add(1);
            inner.painted_rect = None;
            inner.failed_rect = None;
        }
        let _ = window.eval(CLEAR_PAINT_JS);
    }

    fn schedule(&self, window: &tauri::WebviewWindow) {
        let Ok(mut inner) = self.shared.inner.lock() else {
            return;
        };
        if inner.mode != InputVisualEffectMode::GaussianBlur
            || !inner.input_visible
            || inner.window_rect.is_none()
        {
            return;
        }
        if inner.window_rect == inner.painted_rect || inner.window_rect == inner.failed_rect {
            return;
        }
        inner.generation = inner.generation.wrapping_add(1);
        let generation = inner.generation;
        drop(inner);
        arm_refresh(Arc::clone(&self.shared), window.clone(), generation);
    }
}

fn x11_capture_available() -> bool {
    std::env::var_os("DISPLAY").is_some() && !crate::window_geometry::native_wayland_session()
}

fn is_current(shared: &Shared, generation: u64) -> bool {
    shared
        .inner
        .lock()
        .map(|inner| {
            inner.generation == generation
                && inner.mode == InputVisualEffectMode::GaussianBlur
                && inner.input_visible
        })
        .unwrap_or(false)
}

fn arm_refresh(shared: Arc<Shared>, window: tauri::WebviewWindow, generation: u64) {
    gtk::glib::timeout_add(REFRESH_DELAY, move || {
        if !is_current(&shared, generation) {
            return gtk::glib::ControlFlow::Break;
        }
        let _ = window.eval(PREPARE_CAPTURE_JS);
        arm_capture(
            Arc::clone(&shared),
            window.clone(),
            generation,
            NOT_READY_RETRIES,
        );
        gtk::glib::ControlFlow::Break
    });
}

fn arm_capture(shared: Arc<Shared>, window: tauri::WebviewWindow, generation: u64, retries: u8) {
    gtk::glib::timeout_add(CAPTURE_DELAY, move || {
        if !is_current(&shared, generation) {
            return gtk::glib::ControlFlow::Break;
        }
        let rect = shared.inner.lock().ok().and_then(|inner| inner.window_rect);
        let Some(rect) = rect else {
            let _ = window.eval(CANCEL_CAPTURE_JS);
            return gtk::glib::ControlFlow::Break;
        };
        match capture_background(&window, rect) {
            Ok(encoded) => {
                if is_current(&shared, generation) {
                    let _ = window.eval(paint_js(&encoded));
                    if let Ok(mut inner) = shared.inner.lock() {
                        if inner.generation == generation {
                            inner.painted_rect = Some(rect);
                            inner.failed_rect = None;
                        }
                    }
                }
            }
            Err(CaptureError::NotReady) if retries > 0 => {
                arm_capture(Arc::clone(&shared), window.clone(), generation, retries - 1);
            }
            Err(error) => {
                let _ = window.eval(CANCEL_CAPTURE_JS);
                if let Ok(mut inner) = shared.inner.lock() {
                    if inner.generation == generation {
                        inner.failed_rect = Some(rect);
                    }
                }
                let (code, detail) = match error {
                    CaptureError::NotReady => {
                        ("LINUX_INPUT_BLUR_CAPTURE_FAILED", "GTK 窗口尚未就绪")
                    }
                    CaptureError::Failed { code, detail } => (code, detail),
                };
                report(
                    &shared,
                    code,
                    "Linux 输入栏没能采样到桌面，暂时只显示半透明底色",
                    detail,
                );
            }
        }
        gtk::glib::ControlFlow::Break
    });
}

fn paint_js(encoded: &str) -> String {
    format!(
        "(() => {{ const root = document.documentElement; delete root.dataset.linuxInputCapturing; root.style.setProperty('--linux-input-desktop', 'url(\"data:image/png;base64,{encoded}\")'); }})()"
    )
}

fn capture_background(
    window: &tauri::WebviewWindow,
    local: WindowRect,
) -> Result<String, CaptureError> {
    use gdk::prelude::WindowExtManual;
    use gtk::prelude::WidgetExt;

    let gtk_window = window.gtk_window().map_err(|_| CaptureError::NotReady)?;
    let pet = gtk_window.window().ok_or(CaptureError::NotReady)?;
    if let Some(display) = gdk::Display::default() {
        display.sync();
    }
    let root = gdk::Window::default_root_window();
    let (x1, y1) = pet.root_coords(local.0, local.1);
    let (x2, y2) = pet.root_coords(
        local.0.saturating_add(local.2),
        local.1.saturating_add(local.3),
    );
    let (x, y, width, height) = clamp_capture_rect(
        x1,
        y1,
        x2.saturating_sub(x1),
        y2.saturating_sub(y1),
        root.width(),
        root.height(),
    )
    .ok_or(CaptureError::Failed {
        code: "LINUX_INPUT_BLUR_CAPTURE_FAILED",
        detail: "输入栏不在可采样区域内",
    })?;
    let pixbuf = root
        .pixbuf(x, y, width, height)
        .ok_or(CaptureError::Failed {
            code: "LINUX_INPUT_BLUR_CAPTURE_FAILED",
            detail: "根窗口没有返回画面",
        })?;
    let blurred = blur_pixbuf(&pixbuf).ok_or(CaptureError::Failed {
        code: "LINUX_INPUT_BLUR_CAPTURE_FAILED",
        detail: "采样画面无法缩放",
    })?;
    let bytes = blurred
        .save_to_bufferv("png", &[("compression", "6")])
        .map_err(|_| CaptureError::Failed {
            code: "LINUX_INPUT_BLUR_CAPTURE_FAILED",
            detail: "采样结果无法编码",
        })?;
    Ok(base64_encode(&bytes))
}

fn blur_pixbuf(pixbuf: &gdk::gdk_pixbuf::Pixbuf) -> Option<gdk::gdk_pixbuf::Pixbuf> {
    let width = pixbuf.width().max(1);
    let height = pixbuf.height().max(1);
    let small_width = (width / BLUR_DIVISOR).clamp(1, width);
    let small_height = (height / BLUR_DIVISOR).clamp(1, height);
    let small = pixbuf.scale_simple(
        small_width,
        small_height,
        gdk::gdk_pixbuf::InterpType::Bilinear,
    )?;
    let output_width = width.min(MAX_PAINT_WIDTH);
    let output_height = ((f64::from(height) * f64::from(output_width)) / f64::from(width))
        .round()
        .clamp(1.0, f64::from(i32::MAX)) as i32;
    small.scale_simple(
        output_width,
        output_height,
        gdk::gdk_pixbuf::InterpType::Bilinear,
    )
}

fn report(shared: &Shared, code: &'static str, message: &'static str, detail: &str) {
    let should_report = shared
        .reported
        .lock()
        .map(|mut codes| codes.insert(code))
        .unwrap_or(true);
    if !should_report {
        return;
    }
    let _ = shared.runtime_log.submit(
        RuntimeLogEvent::rust(
            Severity::Warning,
            "appearance",
            "appearance.input_visual_effect.degraded",
            message,
        )
        .attributes(serde_json::json!({
            "code": code,
            "reason_code": code,
            "diagnostic": safe_detail(detail),
            "stage": "linux_input_glass",
            "outcome": "degraded",
        })),
    );
}

fn safe_detail(detail: &str) -> String {
    if detail.contains('/') || detail.contains('\\') {
        return "GDK capture failed".to_string();
    }
    detail.chars().take(180).collect()
}

pub(crate) fn linux_input_window_rect(
    input_rect: [u32; 4],
    active_origin: [u32; 2],
    content_scale: f64,
) -> Result<WindowRect, &'static str> {
    let [input_x, input_y, input_width, input_height] = input_rect;
    if input_width == 0 || input_height == 0 || !content_scale.is_finite() || content_scale <= 0.0 {
        return Err("LINUX_INPUT_BLUR_GEOMETRY_INVALID");
    }
    let x = round_to_i32((f64::from(input_x) - f64::from(active_origin[0])) * content_scale)?;
    let y = round_to_i32((f64::from(input_y) - f64::from(active_origin[1])) * content_scale)?;
    let width = round_to_i32(f64::from(input_width) * content_scale)?;
    let height = round_to_i32(f64::from(input_height) * content_scale)?;
    if width <= 0 || height <= 0 {
        return Err("LINUX_INPUT_BLUR_GEOMETRY_INVALID");
    }
    Ok((x, y, width, height))
}

fn round_to_i32(value: f64) -> Result<i32, &'static str> {
    if !value.is_finite() || value < f64::from(i32::MIN) || value > f64::from(i32::MAX) {
        return Err("LINUX_INPUT_BLUR_GEOMETRY_INVALID");
    }
    Ok(value.round() as i32)
}

pub(crate) fn clamp_capture_rect(
    x: i32,
    y: i32,
    width: i32,
    height: i32,
    root_width: i32,
    root_height: i32,
) -> Option<WindowRect> {
    if width <= 0 || height <= 0 || root_width <= 0 || root_height <= 0 {
        return None;
    }
    let right = x.saturating_add(width);
    let bottom = y.saturating_add(height);
    let left = x.max(0);
    let top = y.max(0);
    let right = right.min(root_width);
    let bottom = bottom.min(root_height);
    let width = right.saturating_sub(left);
    let height = bottom.saturating_sub(top);
    if width < 2 || height < 2 {
        return None;
    }
    Some((left, top, width, height))
}

fn base64_encode(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut encoded = String::with_capacity(input.len().div_ceil(3) * 4);
    let mut index = 0;
    while index + 3 <= input.len() {
        let value = (u32::from(input[index]) << 16)
            | (u32::from(input[index + 1]) << 8)
            | u32::from(input[index + 2]);
        encoded.push(TABLE[((value >> 18) & 63) as usize] as char);
        encoded.push(TABLE[((value >> 12) & 63) as usize] as char);
        encoded.push(TABLE[((value >> 6) & 63) as usize] as char);
        encoded.push(TABLE[(value & 63) as usize] as char);
        index += 3;
    }
    match input.len() - index {
        1 => {
            let value = u32::from(input[index]) << 16;
            encoded.push(TABLE[((value >> 18) & 63) as usize] as char);
            encoded.push(TABLE[((value >> 12) & 63) as usize] as char);
            encoded.push('=');
            encoded.push('=');
        }
        2 => {
            let value = (u32::from(input[index]) << 16) | (u32::from(input[index + 1]) << 8);
            encoded.push(TABLE[((value >> 18) & 63) as usize] as char);
            encoded.push(TABLE[((value >> 12) & 63) as usize] as char);
            encoded.push(TABLE[((value >> 6) & 63) as usize] as char);
            encoded.push('=');
        }
        _ => {}
    }
    encoded
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_rect_scales_from_the_active_origin() {
        assert_eq!(
            linux_input_window_rect([30, 50, 40, 16], [10, 20], 2.0).unwrap(),
            (40, 60, 80, 32)
        );
    }

    #[test]
    fn window_rect_can_start_outside_the_window() {
        assert_eq!(
            linux_input_window_rect([0, 0, 12, 8], [10, 4], 1.0).unwrap(),
            (-10, -4, 12, 8)
        );
    }

    #[test]
    fn window_rect_rejects_empty_or_invalid_scale() {
        assert!(linux_input_window_rect([0, 0, 0, 8], [0, 0], 1.0).is_err());
        assert!(linux_input_window_rect([0, 0, 8, 8], [0, 0], 0.0).is_err());
        assert!(linux_input_window_rect([0, 0, 8, 8], [0, 0], f64::NAN).is_err());
    }

    #[test]
    fn capture_rect_clips_to_the_root_window() {
        assert_eq!(
            clamp_capture_rect(-10, 5, 40, 20, 100, 80),
            Some((0, 5, 30, 20))
        );
        assert_eq!(
            clamp_capture_rect(90, 70, 40, 20, 100, 80),
            Some((90, 70, 10, 10))
        );
        assert_eq!(
            clamp_capture_rect(98, 0, 4, 8, 100, 80),
            Some((98, 0, 2, 8))
        );
        assert_eq!(clamp_capture_rect(99, 0, 4, 8, 100, 80), None);
        assert_eq!(clamp_capture_rect(0, 0, 8, 8, 0, 80), None);
    }

    #[test]
    fn base64_matches_the_standard_vectors() {
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
        assert_eq!(base64_encode(b"foob"), "Zm9vYg==");
        assert_eq!(base64_encode(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64_encode(b"foobar"), "Zm9vYmFy");
    }
}
