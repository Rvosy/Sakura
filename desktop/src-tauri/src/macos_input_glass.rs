use std::{
    ffi::c_void,
    sync::{mpsc::sync_channel, Arc, Mutex},
    time::Duration,
};

use objc2::{rc::Retained, runtime::AnyClass, MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{
    NSAnimatablePropertyContainer, NSAnimationContext, NSAppearance, NSAppearanceCustomization,
    NSAppearanceNameDarkAqua, NSColor, NSGlassEffectView, NSGlassEffectViewStyle, NSView,
    NSVisualEffectBlendingMode, NSVisualEffectMaterial, NSVisualEffectState, NSVisualEffectView,
    NSWindowOrderingMode,
};
use objc2_foundation::{NSPoint, NSRect, NSSize};
use objc2_quartz_core::CAMediaTimingFunction;

use crate::{
    character_appearance::{AppearanceValues, InputVisualEffectMode},
    input_visual_effect::{InputVisualEffectStatus, InputVisualEffectSupport},
    window_geometry::{ControlSurfaceLayout, InputSurfaceTransition, LayoutApplication},
};

const INPUT_CORNER_RADIUS: f64 = 28.0;
const LIQUID_THEME_TINT_ALPHA: f64 = 32.0 / 255.0;
const NATIVE_OPERATION_TIMEOUT: Duration = Duration::from_secs(2);
const LIQUID_REQUIRES_MACOS_26: &str = "LIQUID_GLASS_REQUIRES_MACOS_26";

#[derive(Clone, Copy, Debug, PartialEq)]
struct MacInputGeometry {
    frame: NSRect,
    corner_radius: f64,
}

#[derive(Default)]
struct NativeViews {
    gaussian: Option<usize>,
    liquid_container: Option<usize>,
    liquid: Option<usize>,
    has_geometry: bool,
    requested_mode: Option<InputVisualEffectMode>,
}

pub struct MacInputGlassState {
    status: Arc<Mutex<InputVisualEffectStatus>>,
    views: Arc<Mutex<NativeViews>>,
    support: InputVisualEffectSupport,
}

impl MacInputGlassState {
    pub fn new() -> Self {
        Self {
            status: Arc::new(Mutex::new(InputVisualEffectStatus::pending())),
            views: Arc::new(Mutex::new(NativeViews::default())),
            support: detect_support(),
        }
    }

    pub fn support(&self) -> InputVisualEffectSupport {
        self.support
    }

    pub fn status(&self) -> InputVisualEffectStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| {
                InputVisualEffectStatus::failed("INPUT_VISUAL_EFFECT_STATE_UNAVAILABLE")
            })
    }

    pub fn install(&self, window: &tauri::WebviewWindow) {
        let views = self.views.clone();
        let status = self.status.clone();
        let support = self.support;
        let result = with_native_webview(window, move |webview, mtm| {
            let host = unsafe { webview.superview() }
                .ok_or_else(|| "MACOS_INPUT_GLASS_HOST_UNAVAILABLE".to_string())?;
            let frame = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(0.0, 0.0));

            let gaussian = NSVisualEffectView::initWithFrame(NSVisualEffectView::alloc(mtm), frame);
            gaussian.setMaterial(NSVisualEffectMaterial::HUDWindow);
            gaussian.setBlendingMode(NSVisualEffectBlendingMode::BehindWindow);
            gaussian.setState(NSVisualEffectState::Active);
            gaussian.setWantsLayer(true);
            if let Some(layer) = gaussian.layer() {
                layer.setCornerRadius(INPUT_CORNER_RADIUS);
                layer.setMasksToBounds(true);
            }
            gaussian.setHidden(true);
            host.addSubview_positioned_relativeTo(
                &gaussian,
                NSWindowOrderingMode::Below,
                Some(webview),
            );
            let gaussian_handle = Retained::as_ptr(&gaussian).cast::<c_void>() as usize;

            let (liquid_container_handle, liquid_handle) = if support.liquid_glass {
                // NSGlassEffectView owns its backdrop-rendering and rounded-edge layers. Keep it
                // inside a plain local container below WebKit's content, but do not layer-back or
                // mask either view: doing so flattens the system glass into a grey translucent
                // surface and suppresses its dynamic edge highlights.
                let container = NSView::initWithFrame(NSView::alloc(mtm), frame);
                container.setHidden(true);

                let liquid = NSGlassEffectView::initWithFrame(NSGlassEffectView::alloc(mtm), frame);
                // Clear keeps the refraction and specular edge intended for media-rich content
                // without Regular's opaque grey veil over the desktop/portrait backdrop.
                liquid.setStyle(NSGlassEffectViewStyle::Clear);
                let dark_aqua_name = unsafe { NSAppearanceNameDarkAqua };
                if let Some(appearance) = NSAppearance::appearanceNamed(dark_aqua_name) {
                    // Liquid Glass changes its base luminance with the key-window state. Pin only
                    // this native surface to Dark Aqua so focus does not promote it to a white
                    // material; the character primary tint remains dynamic and theme-owned.
                    liquid.setAppearance(Some(&appearance));
                }
                liquid.setCornerRadius(INPUT_CORNER_RADIUS);
                container.addSubview(&liquid);
                let web_content = webview.subviews().firstObject();
                webview.addSubview_positioned_relativeTo(
                    &container,
                    NSWindowOrderingMode::Below,
                    web_content.as_deref(),
                );
                (
                    Some(Retained::as_ptr(&container).cast::<c_void>() as usize),
                    Some(Retained::as_ptr(&liquid).cast::<c_void>() as usize),
                )
            } else {
                (None, None)
            };

            let mut native = views
                .lock()
                .map_err(|_| "MACOS_INPUT_GLASS_STATE_UNAVAILABLE".to_string())?;
            native.gaussian = Some(gaussian_handle);
            native.liquid_container = liquid_container_handle;
            native.liquid = liquid_handle;
            Ok(())
        });

        match result {
            Ok(()) => set_status(
                &status,
                InputVisualEffectStatus::ready(InputVisualEffectMode::Solid),
            ),
            Err(error) => {
                set_status(
                    &status,
                    InputVisualEffectStatus::failed("MACOS_INPUT_GLASS_INITIALIZATION_FAILED"),
                );
                eprintln!("[macos-input-glass] initialization failed: {error}");
            }
        }
    }

    pub fn update_appearance(
        &self,
        window: &tauri::WebviewWindow,
        values: &AppearanceValues,
    ) -> Result<InputVisualEffectStatus, String> {
        if self.status().outcome == "degraded" {
            return Ok(self.status());
        }
        let requested_mode = values.visual_effect_mode;
        let Some(tint) = parse_hex(values.theme_tokens.get("primary")) else {
            self.record_failure(
                window,
                "MACOS_INPUT_GLASS_THEME_INVALID",
                "the input glass theme primary color is invalid",
            );
            return Ok(self.status());
        };
        let views = self.views.clone();
        let support = self.support;
        let next = match with_native_webview(window, move |_webview, _mtm| {
            let mut native = views
                .lock()
                .map_err(|_| "MACOS_INPUT_GLASS_STATE_UNAVAILABLE".to_string())?;
            native.requested_mode = Some(requested_mode);
            if let Some(handle) = native.liquid {
                let liquid = unsafe { view_from_handle::<NSGlassEffectView>(handle) };
                let [red, green, blue, alpha] = tint_components(tint);
                let color = NSColor::colorWithDeviceRed_green_blue_alpha(red, green, blue, alpha);
                liquid.setTintColor(Some(&color));
            }
            Ok(apply_visibility(&native, requested_mode, support))
        }) {
            Ok(next) => next,
            Err(error) => {
                self.record_failure(window, "MACOS_INPUT_GLASS_UPDATE_FAILED", &error);
                return Ok(self.status());
            }
        };
        set_status(&self.status, next.clone());
        Ok(next)
    }

    pub fn update_control_surface(
        &self,
        window: &tauri::WebviewWindow,
        surface: &ControlSurfaceLayout,
        application: &LayoutApplication,
        previous_surface: Option<&ControlSurfaceLayout>,
        transition: Option<InputSurfaceTransition>,
    ) -> Result<(), String> {
        if self.status().outcome == "degraded" {
            return Ok(());
        }
        let geometry = match mac_input_geometry(surface.input_rect, application) {
            Ok(geometry) => geometry,
            Err(error) => {
                self.record_failure(window, "MACOS_INPUT_GLASS_GEOMETRY_FAILED", &error);
                return Ok(());
            }
        };
        let views = self.views.clone();
        let support = self.support;
        let animate = previous_surface
            .zip(transition)
            .filter(|(previous, transition)| {
                transition.duration_ms > 0
                    && previous.input_rect[0..3] == surface.input_rect[0..3]
                    && previous.input_rect[3] != surface.input_rect[3]
            })
            .map(|(_, transition)| transition);
        let staging_geometry = match animate
            .and_then(|transition| transition.staging_height)
            .map(|staging_height| {
                let mut staging_rect = surface.input_rect;
                staging_rect[3] = staging_height;
                mac_input_geometry(staging_rect, application)
            })
            .transpose()
        {
            Ok(geometry) => geometry,
            Err(error) => {
                self.record_failure(window, "MACOS_INPUT_GLASS_GEOMETRY_FAILED", &error);
                return Ok(());
            }
        };
        let next = match with_native_webview(window, move |webview, _mtm| {
            let mut native = views
                .lock()
                .map_err(|_| "MACOS_INPUT_GLASS_STATE_UNAVAILABLE".to_string())?;
            let (liquid_container_frame, staging_liquid_container_frame) =
                if native.liquid_container.is_some() {
                    let host = unsafe { webview.superview() }
                        .ok_or_else(|| "MACOS_INPUT_GLASS_HOST_UNAVAILABLE".to_string())?;
                    (
                        Some(webview.convertRect_fromView(geometry.frame, Some(&host))),
                        staging_geometry.map(|staging| {
                            webview.convertRect_fromView(staging.frame, Some(&host))
                        }),
                    )
                } else {
                    (None, None)
                };
            if let Some(staging) = staging_geometry {
                if let Some(handle) = native.gaussian {
                    unsafe { view_from_handle::<NSVisualEffectView>(handle) }
                        .setFrame(staging.frame);
                }
                if let Some(handle) = native.liquid {
                    unsafe { view_from_handle::<NSGlassEffectView>(handle) }
                        .setFrame(liquid_content_frame(staging));
                }
                if let Some(handle) = native.liquid_container {
                    unsafe { view_from_handle::<NSView>(handle) }.setFrame(
                        staging_liquid_container_frame
                            .expect("staging frame is paired with its native container"),
                    );
                }
            }
            if let Some(transition) = animate {
                NSAnimationContext::beginGrouping();
                let context = NSAnimationContext::currentContext();
                context.setDuration(f64::from(transition.duration_ms) / 1000.0);
                let timing = CAMediaTimingFunction::functionWithControlPoints(0.22, 1.0, 0.36, 1.0);
                context.setTimingFunction(Some(&timing));
            }
            if let Some(handle) = native.gaussian {
                let gaussian = unsafe { view_from_handle::<NSVisualEffectView>(handle) };
                if animate.is_some() {
                    let view: &NSView = gaussian;
                    view.animator().setFrame(geometry.frame);
                } else {
                    gaussian.setFrame(geometry.frame);
                }
                if let Some(layer) = gaussian.layer() {
                    layer.setCornerRadius(geometry.corner_radius);
                }
            }
            if let Some(handle) = native.liquid {
                let liquid = unsafe { view_from_handle::<NSGlassEffectView>(handle) };
                if animate.is_some() {
                    let view: &NSView = liquid;
                    view.animator().setFrame(liquid_content_frame(geometry));
                } else {
                    liquid.setFrame(liquid_content_frame(geometry));
                }
                liquid.setCornerRadius(geometry.corner_radius);
            }
            if let Some(handle) = native.liquid_container {
                let container = unsafe { view_from_handle::<NSView>(handle) };
                // The concrete WKWebView hierarchy may flip coordinates independently of the
                // host. Convert through AppKit instead of assuming either origin convention.
                let frame = liquid_container_frame
                    .expect("liquid container frame is paired with its native handle");
                if animate.is_some() {
                    container.animator().setFrame(frame);
                } else {
                    container.setFrame(frame);
                }
            }
            if animate.is_some() {
                NSAnimationContext::endGrouping();
            }
            native.has_geometry = true;
            Ok(native
                .requested_mode
                .map(|mode| apply_visibility(&native, mode, support)))
        }) {
            Ok(next) => next,
            Err(error) => {
                self.record_failure(window, "MACOS_INPUT_GLASS_LAYOUT_FAILED", &error);
                return Ok(());
            }
        };
        if let Some(next) = next {
            set_status(&self.status, next);
        }
        Ok(())
    }

    pub fn teardown(&self, window: &tauri::WebviewWindow) {
        let views = self.views.clone();
        let _ = with_native_webview(window, move |_webview, _mtm| {
            let mut native = views
                .lock()
                .map_err(|_| "MACOS_INPUT_GLASS_STATE_UNAVAILABLE".to_string())?;
            native.liquid.take();
            for handle in [native.gaussian.take(), native.liquid_container.take()]
                .into_iter()
                .flatten()
            {
                let view = unsafe { view_from_handle::<NSView>(handle) };
                view.removeFromSuperview();
            }
            native.has_geometry = false;
            Ok(())
        });
    }

    fn record_failure(&self, window: &tauri::WebviewWindow, code: &'static str, detail: &str) {
        let views = self.views.clone();
        let _ = with_native_webview(window, move |_webview, _mtm| {
            let native = views
                .lock()
                .map_err(|_| "MACOS_INPUT_GLASS_STATE_UNAVAILABLE".to_string())?;
            if let Some(handle) = native.gaussian {
                unsafe { view_from_handle::<NSVisualEffectView>(handle) }.setHidden(true);
            }
            if let Some(handle) = native.liquid_container {
                unsafe { view_from_handle::<NSView>(handle) }.setHidden(true);
            }
            Ok(())
        });
        set_status(&self.status, InputVisualEffectStatus::failed(code));
        eprintln!("[macos-input-glass] {code}: {detail}; continuing with solid input");
    }
}

fn detect_support() -> InputVisualEffectSupport {
    InputVisualEffectSupport::new(true, AnyClass::get(c"NSGlassEffectView").is_some())
}

fn with_native_webview<T, F>(window: &tauri::WebviewWindow, operation: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce(&NSView, MainThreadMarker) -> Result<T, String> + Send + 'static,
{
    let (sender, receiver) = sync_channel(1);
    window
        .with_webview(move |webview| {
            let result = (|| {
                let mtm = MainThreadMarker::new()
                    .ok_or_else(|| "MACOS_INPUT_GLASS_MAIN_THREAD_REQUIRED".to_string())?;
                let native_webview = unsafe { &*webview.inner().cast::<NSView>() };
                operation(native_webview, mtm)
            })();
            let _ = sender.send(result);
        })
        .map_err(|error| format!("MACOS_INPUT_GLASS_DISPATCH_FAILED:{error}"))?;
    receiver
        .recv_timeout(NATIVE_OPERATION_TIMEOUT)
        .map_err(|_| "MACOS_INPUT_GLASS_DISPATCH_TIMEOUT".to_string())?
}

fn apply_visibility(
    native: &NativeViews,
    requested_mode: InputVisualEffectMode,
    support: InputVisualEffectSupport,
) -> InputVisualEffectStatus {
    let gaussian_available = native.gaussian.is_some();
    let liquid_available =
        support.liquid_glass && native.liquid_container.is_some() && native.liquid.is_some();
    let (show_gaussian, show_liquid, status) = visibility_plan(
        requested_mode,
        native.has_geometry,
        gaussian_available,
        liquid_available,
    );
    if let Some(handle) = native.gaussian {
        unsafe { view_from_handle::<NSVisualEffectView>(handle) }.setHidden(!show_gaussian);
    }
    if let Some(handle) = native.liquid_container {
        unsafe { view_from_handle::<NSView>(handle) }.setHidden(!show_liquid);
    }
    status
}

fn visibility_plan(
    requested_mode: InputVisualEffectMode,
    has_geometry: bool,
    gaussian_available: bool,
    liquid_available: bool,
) -> (bool, bool, InputVisualEffectStatus) {
    match requested_mode {
        InputVisualEffectMode::Solid => (
            false,
            false,
            InputVisualEffectStatus::ready(InputVisualEffectMode::Solid),
        ),
        InputVisualEffectMode::GaussianBlur if gaussian_available => (
            has_geometry,
            false,
            InputVisualEffectStatus::ready(InputVisualEffectMode::GaussianBlur),
        ),
        InputVisualEffectMode::GaussianBlur => (
            false,
            false,
            InputVisualEffectStatus::failed("MACOS_GAUSSIAN_GLASS_UNAVAILABLE"),
        ),
        InputVisualEffectMode::LiquidGlass if liquid_available => (
            false,
            has_geometry,
            InputVisualEffectStatus::ready(InputVisualEffectMode::LiquidGlass),
        ),
        InputVisualEffectMode::LiquidGlass => (
            false,
            false,
            InputVisualEffectStatus::limited(
                InputVisualEffectMode::Solid,
                LIQUID_REQUIRES_MACOS_26,
            ),
        ),
    }
}

fn liquid_content_frame(geometry: MacInputGeometry) -> NSRect {
    NSRect::new(NSPoint::new(0.0, 0.0), geometry.frame.size)
}

fn tint_components(tint: [u8; 3]) -> [f64; 4] {
    [
        f64::from(tint[0]) / 255.0,
        f64::from(tint[1]) / 255.0,
        f64::from(tint[2]) / 255.0,
        LIQUID_THEME_TINT_ALPHA,
    ]
}

fn mac_input_geometry(
    input_rect: [u32; 4],
    application: &LayoutApplication,
) -> Result<MacInputGeometry, String> {
    let [input_x, input_y, input_width, input_height] = input_rect;
    let [active_x, active_y, _, _] = application.active_bounds;
    if input_x < active_x || input_y < active_y {
        return Err("MACOS_INPUT_GLASS_GEOMETRY_INVALID".to_string());
    }
    if !application.content_scale.is_finite()
        || application.content_scale <= 0.0
        || !application.scale_factor.is_finite()
        || application.scale_factor <= 0.0
    {
        return Err("MACOS_INPUT_GLASS_SCALE_INVALID".to_string());
    }
    let scale = application.content_scale;
    let content_width = f64::from(application.physical_placement.width) / application.scale_factor;
    let content_height =
        f64::from(application.physical_placement.height) / application.scale_factor;
    let x = f64::from(input_x - active_x) * scale;
    let top = f64::from(input_y - active_y) * scale;
    let width = f64::from(input_width) * scale;
    let height = f64::from(input_height) * scale;
    let y = content_height - top - height;
    if x < 0.0 || y < 0.0 || x + width > content_width + 0.5 || y + height > content_height + 0.5 {
        return Err("MACOS_INPUT_GLASS_GEOMETRY_INVALID".to_string());
    }
    Ok(MacInputGeometry {
        frame: NSRect::new(NSPoint::new(x, y), NSSize::new(width, height)),
        corner_radius: INPUT_CORNER_RADIUS * scale,
    })
}

fn parse_hex(value: Option<&String>) -> Option<[u8; 3]> {
    let value = value?;
    if value.len() != 7 || !value.starts_with('#') {
        return None;
    }
    Some([
        u8::from_str_radix(&value[1..3], 16).ok()?,
        u8::from_str_radix(&value[3..5], 16).ok()?,
        u8::from_str_radix(&value[5..7], 16).ok()?,
    ])
}

fn set_status(status: &Mutex<InputVisualEffectStatus>, next: InputVisualEffectStatus) {
    if let Ok(mut status) = status.lock() {
        *status = next;
    }
}

unsafe fn view_from_handle<'a, T>(handle: usize) -> &'a T {
    unsafe { &*(handle as *const T) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::window_geometry::{
        PhysicalPlacement, PhysicalPoint, PhysicalRect, PresentationState,
    };

    fn application(scale_factor: f64, content_scale: f64) -> LayoutApplication {
        LayoutApplication {
            applied: true,
            revision: 1,
            state: PresentationState::Product,
            contract_version: 3,
            content_scale,
            scale_factor,
            physical_placement: PhysicalPlacement {
                x: 0,
                y: 0,
                width: (640.0 * scale_factor * content_scale) as u32,
                height: (400.0 * scale_factor * content_scale) as u32,
            },
            active_bounds: [100, 200, 640, 400],
            physical_local_anchor: [0, 0],
            portrait_anchor: PhysicalPoint { x: 0, y: 0 },
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            },
            monitor_name: Some("test".to_string()),
            backend_mode: "macos_cursor_router",
            degraded_reason: None,
        }
    }

    #[test]
    fn geometry_flips_web_top_origin_for_appkit_and_leaves_retina_to_backing_scale() {
        let geometry = mac_input_geometry([120, 500, 300, 52], &application(2.0, 1.0)).unwrap();
        assert_eq!(geometry.frame.origin.x, 20.0);
        assert_eq!(geometry.frame.origin.y, 48.0);
        assert_eq!(geometry.frame.size.width, 300.0);
        assert_eq!(geometry.frame.size.height, 52.0);
        assert_eq!(geometry.corner_radius, 28.0);
    }

    #[test]
    fn geometry_applies_content_scale_in_appkit_points() {
        let geometry = mac_input_geometry([120, 500, 300, 52], &application(2.0, 0.75)).unwrap();
        assert_eq!(geometry.frame.origin.x, 15.0);
        assert_eq!(geometry.frame.origin.y, 36.0);
        assert_eq!(geometry.frame.size.width, 225.0);
        assert_eq!(geometry.frame.size.height, 39.0);
        assert_eq!(geometry.corner_radius, 21.0);
    }

    #[test]
    fn liquid_tint_uses_the_theme_primary_at_low_alpha() {
        assert_eq!(parse_hex(Some(&"#249a5a".to_string())), Some([36, 154, 90]));
        assert_eq!(parse_hex(Some(&"green".to_string())), None);
        assert_eq!(
            tint_components([36, 154, 90]),
            [
                36.0 / 255.0,
                154.0 / 255.0,
                90.0 / 255.0,
                LIQUID_THEME_TINT_ALPHA,
            ]
        );
    }

    #[test]
    fn liquid_content_is_local_to_the_clipped_input_container() {
        let geometry = mac_input_geometry([120, 500, 300, 52], &application(2.0, 1.0)).unwrap();
        let content = liquid_content_frame(geometry);
        assert_eq!(content.origin, NSPoint::new(0.0, 0.0));
        assert_eq!(content.size, geometry.frame.size);
    }

    #[test]
    fn visual_modes_are_mutually_exclusive_and_liquid_never_falls_back_to_gaussian() {
        let (gaussian, liquid, status) =
            visibility_plan(InputVisualEffectMode::GaussianBlur, true, true, true);
        assert!(gaussian);
        assert!(!liquid);
        assert_eq!(status.effective_mode, InputVisualEffectMode::GaussianBlur);

        let (gaussian, liquid, status) =
            visibility_plan(InputVisualEffectMode::LiquidGlass, true, true, false);
        assert!(!gaussian);
        assert!(!liquid);
        assert_eq!(status.effective_mode, InputVisualEffectMode::Solid);
        assert_eq!(status.error_code.as_deref(), Some(LIQUID_REQUIRES_MACOS_26));
    }

    #[test]
    fn runtime_support_tracks_the_public_appkit_class() {
        assert_eq!(
            detect_support().liquid_glass,
            AnyClass::get(c"NSGlassEffectView").is_some()
        );
        assert!(detect_support().gaussian_blur);
    }
}
