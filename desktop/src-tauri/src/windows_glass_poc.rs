use std::{ffi::OsStr, sync::Mutex};

use serde::Serialize;

pub const ENABLE_ENV: &str = "SAKURA_WINDOWS_GLASS_POC";
pub const FORCE_FAILURE_ENV: &str = "SAKURA_WINDOWS_GLASS_POC_FORCE_FAILURE";

const BUBBLE_CORNER_RADIUS: f64 = 22.0;
const INPUT_CORNER_RADIUS: f64 = 28.0;
const GAUSSIAN_STANDARD_DEVIATION: f32 = 18.0;

#[derive(Clone, Copy, Debug, PartialEq)]
struct NativeRegionGeometry {
    offset: [f32; 2],
    size: [f32; 2],
    corner_radius: f32,
}

fn native_region_geometry(
    rect: [u32; 4],
    scale: f64,
    active_origin: [u32; 2],
    logical_corner_radius: f64,
) -> Result<NativeRegionGeometry, &'static str> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err("glass region scale must be positive and finite");
    }
    let [x, y, width, height] = rect;
    if width == 0 || height == 0 || x < active_origin[0] || y < active_origin[1] {
        return Err("glass region must be non-empty and surface-local");
    }
    let left = ((f64::from(x) - f64::from(active_origin[0])) * scale).floor();
    let top = ((f64::from(y) - f64::from(active_origin[1])) * scale).floor();
    let right = ((f64::from(x.saturating_add(width)) - f64::from(active_origin[0])) * scale).ceil();
    let bottom =
        ((f64::from(y.saturating_add(height)) - f64::from(active_origin[1])) * scale).ceil();
    let physical_width = right - left;
    let physical_height = bottom - top;
    if ![left, top, physical_width, physical_height]
        .iter()
        .all(|value| value.is_finite())
        || physical_width <= 0.0
        || physical_height <= 0.0
    {
        return Err("glass region geometry is invalid");
    }
    Ok(NativeRegionGeometry {
        offset: [left as f32, top as f32],
        size: [physical_width as f32, physical_height as f32],
        corner_radius: (logical_corner_radius * scale)
            .min(physical_width / 2.0)
            .min(physical_height / 2.0) as f32,
    })
}

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

    pub fn update_control_surface(
        &self,
        surface: &crate::window_geometry::ControlSurfaceLayout,
        application: &crate::window_geometry::LayoutApplication,
    ) -> Result<(), String> {
        #[cfg(windows)]
        {
            let layer = self
                .layer
                .lock()
                .map_err(|_| "native glass object store is unavailable".to_string())?;
            if let Some(layer) = layer.as_ref() {
                layer
                    .update_control_surface(surface, application)
                    .map_err(|error| format!("{}: {}", error.code, error.detail))?;
            }
        }
        #[cfg(not(windows))]
        let _ = (surface, application);
        Ok(())
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
    _dispatcher_controller: Option<windows::System::DispatcherQueueController>,
    _compositor: windows::UI::Composition::Compositor,
    _target: windows::UI::Composition::Desktop::DesktopWindowTarget,
    _root: windows::UI::Composition::ContainerVisual,
    _backdrop_brush: windows::UI::Composition::CompositionBackdropBrush,
    _blur_factory: windows::UI::Composition::CompositionEffectFactory,
    _blur_brush: windows::UI::Composition::CompositionEffectBrush,
    bubble_region: NativeGlassRegion,
    input_region: NativeGlassRegion,
}

#[cfg(windows)]
struct NativeGlassRegion {
    container: windows::UI::Composition::ContainerVisual,
    _blur_visual: windows::UI::Composition::SpriteVisual,
    _tint_visual: windows::UI::Composition::SpriteVisual,
    clip: windows::UI::Composition::RectangleClip,
}

#[cfg(windows)]
impl NativeGlassRegion {
    fn create(
        compositor: &windows::UI::Composition::Compositor,
        blur_brush: &windows::UI::Composition::CompositionEffectBrush,
        tint_brush: &windows::UI::Composition::CompositionColorBrush,
    ) -> windows::core::Result<Self> {
        use windows_numerics::Vector2;

        let fill = Vector2 { X: 1.0, Y: 1.0 };
        let container = compositor.CreateContainerVisual()?;
        container.SetRelativeSizeAdjustment(fill)?;
        container.SetIsVisible(false)?;

        let blur_visual = compositor.CreateSpriteVisual()?;
        blur_visual.SetRelativeSizeAdjustment(fill)?;
        blur_visual.SetBrush(blur_brush)?;
        container.Children()?.InsertAtBottom(&blur_visual)?;

        let tint_visual = compositor.CreateSpriteVisual()?;
        tint_visual.SetRelativeSizeAdjustment(fill)?;
        tint_visual.SetBrush(tint_brush)?;
        container.Children()?.InsertAtTop(&tint_visual)?;

        let clip = compositor.CreateRectangleClip()?;
        container.SetClip(&clip)?;

        Ok(Self {
            container,
            _blur_visual: blur_visual,
            _tint_visual: tint_visual,
            clip,
        })
    }

    fn update(
        &self,
        rect: [u32; 4],
        scale: f64,
        active_origin: [u32; 2],
        logical_corner_radius: f64,
    ) -> windows::core::Result<()> {
        use windows_numerics::Vector2;

        let geometry = native_region_geometry(rect, scale, active_origin, logical_corner_radius)
            .map_err(|message| windows::core::Error::new(E_INVALIDARG_HRESULT, message))?;
        let radius = Vector2 {
            X: geometry.corner_radius,
            Y: geometry.corner_radius,
        };
        self.clip.SetLeft(geometry.offset[0])?;
        self.clip.SetTop(geometry.offset[1])?;
        self.clip.SetRight(geometry.offset[0] + geometry.size[0])?;
        self.clip.SetBottom(geometry.offset[1] + geometry.size[1])?;
        self.clip.SetTopLeftRadius(radius)?;
        self.clip.SetTopRightRadius(radius)?;
        self.clip.SetBottomRightRadius(radius)?;
        self.clip.SetBottomLeftRadius(radius)?;
        self.container.SetIsVisible(true)
    }
}

#[cfg(windows)]
impl NativeGlassLayer {
    fn install(window: &tauri::WebviewWindow) -> Result<Self, NativeGlassError> {
        use std::{ffi::c_void, mem::size_of};

        use windows::{
            core::{Interface, BOOL, HSTRING},
            Graphics::Effects::{IGraphicsEffect, IGraphicsEffectSource},
            System::DispatcherQueue,
            Win32::{
                Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_USE_HOSTBACKDROPBRUSH},
                System::WinRT::{
                    Composition::ICompositorDesktopInterop, CreateDispatcherQueueController,
                    DispatcherQueueOptions, Graphics::Direct2D::IGraphicsEffectD2D1Interop,
                    DQTAT_COM_ASTA, DQTYPE_THREAD_CURRENT,
                },
            },
            UI::{Color, Composition::Compositor},
        };
        use windows_numerics::Vector2;

        let hwnd = window
            .hwnd()
            .map_err(|error| NativeGlassError::at("GLASS_HWND_UNAVAILABLE", error))?;

        let host_backdrop_enabled = BOOL::from(true);
        unsafe {
            DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_HOSTBACKDROPBRUSH,
                (&host_backdrop_enabled as *const BOOL).cast::<c_void>(),
                size_of::<BOOL>() as u32,
            )
        }
        .map_err(|error| NativeGlassError::at("GLASS_DWM_HOST_BACKDROP_ENABLE_FAILED", error))?;

        let dispatcher_controller = if DispatcherQueue::GetForCurrentThread().is_ok() {
            None
        } else {
            Some(
                unsafe {
                    CreateDispatcherQueueController(DispatcherQueueOptions {
                        dwSize: size_of::<DispatcherQueueOptions>() as u32,
                        threadType: DQTYPE_THREAD_CURRENT,
                        apartmentType: DQTAT_COM_ASTA,
                    })
                }
                .map_err(|error| {
                    NativeGlassError::at("GLASS_DISPATCHER_QUEUE_CREATE_FAILED", error)
                })?,
            )
        };
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
        let blur_source = windows::UI::Composition::CompositionEffectSourceParameter::Create(
            &HSTRING::from("backdrop"),
        )
        .map_err(|error| NativeGlassError::at("GLASS_BLUR_SOURCE_CREATE_FAILED", error))?;
        let backdrop_source: IGraphicsEffectSource = blur_source
            .cast()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_SOURCE_CAST_FAILED", error))?;
        // Border first turns the HostBackdrop input into an effectively unbounded source. Without
        // it D2D reserves the Gaussian kernel's left/top expansion inside the SpriteVisual, which
        // shows up as an unblurred strip approximately three standard deviations wide.
        let border_source: IGraphicsEffectSource =
            BorderEffectDescription::new(backdrop_source).into();
        let blur_effect: IGraphicsEffect =
            GaussianBlurEffectDescription::new(GAUSSIAN_STANDARD_DEVIATION, border_source).into();
        blur_effect
            .cast::<IGraphicsEffectSource>()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_EFFECT_SOURCE_MISSING", error))?;
        blur_effect
            .cast::<IGraphicsEffectD2D1Interop>()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_INTEROP_MISSING", error))?;
        let blur_factory = compositor
            .CreateEffectFactory(&blur_effect)
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_FACTORY_CREATE_FAILED", error))?;
        let blur_brush = blur_factory
            .CreateBrush()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_BRUSH_CREATE_FAILED", error))?;
        blur_brush
            .SetSourceParameter(&HSTRING::from("backdrop"), &backdrop_brush)
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_SOURCE_BIND_FAILED", error))?;
        // Keep a deliberately saturated but translucent diagnostic tint until the native/WebView
        // coverage seam is resolved. The WebView surface is neutral, so missing native coverage
        // remains visibly different while the desktop is still readable through covered pixels.
        let tint_brush = compositor
            .CreateColorBrushWithColor(Color {
                A: 88,
                R: 255,
                G: 24,
                B: 148,
            })
            .map_err(|error| NativeGlassError::at("GLASS_TINT_CREATE_FAILED", error))?;
        let bubble_region = NativeGlassRegion::create(&compositor, &blur_brush, &tint_brush)
            .map_err(|error| NativeGlassError::at("GLASS_BUBBLE_REGION_CREATE_FAILED", error))?;
        let input_region = NativeGlassRegion::create(&compositor, &blur_brush, &tint_brush)
            .map_err(|error| NativeGlassError::at("GLASS_INPUT_REGION_CREATE_FAILED", error))?;
        root.Children()
            .and_then(|children| children.InsertAtTop(&bubble_region.container))
            .and_then(|_| root.Children())
            .and_then(|children| children.InsertAtTop(&input_region.container))
            .map_err(|error| NativeGlassError::at("GLASS_REGION_INSERT_FAILED", error))?;

        target
            .SetRoot(&root)
            .map_err(|error| NativeGlassError::at("GLASS_ROOT_ATTACH_FAILED", error))?;

        Ok(Self {
            _dispatcher_controller: dispatcher_controller,
            _compositor: compositor,
            _target: target,
            _root: root,
            _backdrop_brush: backdrop_brush,
            _blur_factory: blur_factory,
            _blur_brush: blur_brush,
            bubble_region,
            input_region,
        })
    }

    fn update_control_surface(
        &self,
        surface: &crate::window_geometry::ControlSurfaceLayout,
        application: &crate::window_geometry::LayoutApplication,
    ) -> Result<(), NativeGlassError> {
        let scale = application.scale_factor * application.content_scale;
        let [active_x, active_y, _, _] = application.active_bounds;
        let bubble_geometry = native_region_geometry(
            surface.bubble_rect,
            scale,
            [active_x, active_y],
            BUBBLE_CORNER_RADIUS,
        )
        .map_err(|error| NativeGlassError::at("GLASS_REGION_GEOMETRY_FAILED", error))?;
        eprintln!(
            "[windows-glass-poc] region active={:?} bubble={:?} scale={scale:.6} offset={:?} size={:?} placement={}x{}",
            application.active_bounds,
            surface.bubble_rect,
            bubble_geometry.offset,
            bubble_geometry.size,
            application.physical_placement.width,
            application.physical_placement.height,
        );
        self.bubble_region
            .update(
                surface.bubble_rect,
                scale,
                [active_x, active_y],
                BUBBLE_CORNER_RADIUS,
            )
            .and_then(|_| {
                self.input_region.update(
                    surface.input_rect,
                    scale,
                    [active_x, active_y],
                    INPUT_CORNER_RADIUS,
                )
            })
            .map_err(|error| NativeGlassError::at("GLASS_REGION_LAYOUT_FAILED", error))
    }
}

#[cfg(windows)]
const E_INVALIDARG_HRESULT: windows::core::HRESULT = windows::core::HRESULT(0x80070057_u32 as i32);

#[cfg(windows)]
#[windows::core::implement(
    windows::Graphics::Effects::IGraphicsEffect,
    windows::Graphics::Effects::IGraphicsEffectSource,
    windows::Win32::System::WinRT::Graphics::Direct2D::IGraphicsEffectD2D1Interop
)]
struct BorderEffectDescription {
    name: Mutex<windows::core::HSTRING>,
    source: windows::Graphics::Effects::IGraphicsEffectSource,
}

#[cfg(windows)]
impl BorderEffectDescription {
    fn new(source: windows::Graphics::Effects::IGraphicsEffectSource) -> Self {
        Self {
            name: Mutex::new(windows::core::HSTRING::from("SakuraBackdropBorder")),
            source,
        }
    }
}

#[cfg(windows)]
impl windows::Graphics::Effects::IGraphicsEffectSource_Impl for BorderEffectDescription_Impl {}

#[cfg(windows)]
impl windows::Graphics::Effects::IGraphicsEffect_Impl for BorderEffectDescription_Impl {
    fn Name(&self) -> windows::core::Result<windows::core::HSTRING> {
        self.name.lock().map(|name| name.clone()).map_err(|_| {
            windows::core::Error::new(E_INVALIDARG_HRESULT, "border effect name lock poisoned")
        })
    }

    fn SetName(&self, name: &windows::core::HSTRING) -> windows::core::Result<()> {
        *self.name.lock().map_err(|_| {
            windows::core::Error::new(E_INVALIDARG_HRESULT, "border effect name lock poisoned")
        })? = name.clone();
        Ok(())
    }
}

#[cfg(windows)]
impl windows::Win32::System::WinRT::Graphics::Direct2D::IGraphicsEffectD2D1Interop_Impl
    for BorderEffectDescription_Impl
{
    fn GetEffectId(&self) -> windows::core::Result<windows::core::GUID> {
        Ok(windows::Win32::Graphics::Direct2D::CLSID_D2D1Border)
    }

    fn GetNamedPropertyMapping(
        &self,
        _name: &windows::core::PCWSTR,
        _index: *mut u32,
        _mapping: *mut windows::Win32::System::WinRT::Graphics::Direct2D::GRAPHICS_EFFECT_PROPERTY_MAPPING,
    ) -> windows::core::Result<()> {
        Err(E_INVALIDARG_HRESULT.into())
    }

    fn GetPropertyCount(&self) -> windows::core::Result<u32> {
        Ok(2)
    }

    fn GetProperty(
        &self,
        index: u32,
    ) -> windows::core::Result<windows::Foundation::IPropertyValue> {
        use windows::{
            core::Interface, Foundation::PropertyValue,
            Win32::Graphics::Direct2D::D2D1_BORDER_EDGE_MODE_CLAMP,
        };

        if index > 1 {
            return Err(E_INVALIDARG_HRESULT.into());
        }
        PropertyValue::CreateUInt32(D2D1_BORDER_EDGE_MODE_CLAMP.0 as u32)?.cast()
    }

    fn GetSource(
        &self,
        index: u32,
    ) -> windows::core::Result<windows::Graphics::Effects::IGraphicsEffectSource> {
        if index == 0 {
            Ok(self.source.clone())
        } else {
            Err(E_INVALIDARG_HRESULT.into())
        }
    }

    fn GetSourceCount(&self) -> windows::core::Result<u32> {
        Ok(1)
    }
}

#[cfg(windows)]
#[windows::core::implement(
    windows::Graphics::Effects::IGraphicsEffect,
    windows::Graphics::Effects::IGraphicsEffectSource,
    windows::Win32::System::WinRT::Graphics::Direct2D::IGraphicsEffectD2D1Interop
)]
struct GaussianBlurEffectDescription {
    name: Mutex<windows::core::HSTRING>,
    standard_deviation: f32,
    source: windows::Graphics::Effects::IGraphicsEffectSource,
}

#[cfg(windows)]
impl GaussianBlurEffectDescription {
    fn new(
        standard_deviation: f32,
        source: windows::Graphics::Effects::IGraphicsEffectSource,
    ) -> Self {
        Self {
            name: Mutex::new(windows::core::HSTRING::from("SakuraGaussianBlur")),
            standard_deviation,
            source,
        }
    }
}

#[cfg(windows)]
impl windows::Graphics::Effects::IGraphicsEffectSource_Impl for GaussianBlurEffectDescription_Impl {}

#[cfg(windows)]
impl windows::Graphics::Effects::IGraphicsEffect_Impl for GaussianBlurEffectDescription_Impl {
    fn Name(&self) -> windows::core::Result<windows::core::HSTRING> {
        self.name.lock().map(|name| name.clone()).map_err(|_| {
            windows::core::Error::new(E_INVALIDARG_HRESULT, "blur effect name lock poisoned")
        })
    }

    fn SetName(&self, name: &windows::core::HSTRING) -> windows::core::Result<()> {
        *self.name.lock().map_err(|_| {
            windows::core::Error::new(E_INVALIDARG_HRESULT, "blur effect name lock poisoned")
        })? = name.clone();
        Ok(())
    }
}

#[cfg(windows)]
impl windows::Win32::System::WinRT::Graphics::Direct2D::IGraphicsEffectD2D1Interop_Impl
    for GaussianBlurEffectDescription_Impl
{
    fn GetEffectId(&self) -> windows::core::Result<windows::core::GUID> {
        Ok(windows::Win32::Graphics::Direct2D::CLSID_D2D1GaussianBlur)
    }

    fn GetNamedPropertyMapping(
        &self,
        _name: &windows::core::PCWSTR,
        _index: *mut u32,
        _mapping: *mut windows::Win32::System::WinRT::Graphics::Direct2D::GRAPHICS_EFFECT_PROPERTY_MAPPING,
    ) -> windows::core::Result<()> {
        Err(E_INVALIDARG_HRESULT.into())
    }

    fn GetPropertyCount(&self) -> windows::core::Result<u32> {
        Ok(3)
    }

    fn GetProperty(
        &self,
        index: u32,
    ) -> windows::core::Result<windows::Foundation::IPropertyValue> {
        use windows::{
            core::Interface,
            Foundation::PropertyValue,
            Win32::Graphics::Direct2D::{
                Common::D2D1_BORDER_MODE_HARD, D2D1_GAUSSIANBLUR_OPTIMIZATION_BALANCED,
            },
        };

        let value = match index {
            0 => PropertyValue::CreateSingle(self.standard_deviation)?,
            1 => PropertyValue::CreateUInt32(D2D1_GAUSSIANBLUR_OPTIMIZATION_BALANCED.0 as u32)?,
            2 => PropertyValue::CreateUInt32(D2D1_BORDER_MODE_HARD.0 as u32)?,
            _ => return Err(E_INVALIDARG_HRESULT.into()),
        };
        value.cast()
    }

    fn GetSource(
        &self,
        index: u32,
    ) -> windows::core::Result<windows::Graphics::Effects::IGraphicsEffectSource> {
        if index == 0 {
            Ok(self.source.clone())
        } else {
            Err(E_INVALIDARG_HRESULT.into())
        }
    }

    fn GetSourceCount(&self) -> windows::core::Result<u32> {
        Ok(1)
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

    #[test]
    fn native_regions_use_surface_local_floor_and_ceil_edges() {
        let geometry = native_region_geometry([130, 680, 640, 128], 1.25, [128, 326], 22.0)
            .expect("bubble geometry");
        assert_eq!(geometry.offset, [2.0, 442.0]);
        assert_eq!(geometry.size, [801.0, 161.0]);
        assert_eq!(geometry.corner_radius, 27.5);
    }

    #[test]
    fn native_regions_cover_fractional_scale_edges_without_gaps() {
        let geometry = native_region_geometry([130, 818, 640, 52], 1.05, [128, 326], 28.0)
            .expect("input geometry");
        assert_eq!(geometry.offset, [2.0, 516.0]);
        assert_eq!(geometry.size, [673.0, 56.0]);
        assert_eq!(geometry.corner_radius, 28.0);
    }

    #[test]
    fn native_regions_reject_invalid_scale_and_origin() {
        assert!(native_region_geometry([10, 10, 20, 20], 0.0, [0, 0], 4.0).is_err());
        assert!(native_region_geometry([9, 10, 20, 20], 1.0, [10, 10], 4.0).is_err());
    }
}
