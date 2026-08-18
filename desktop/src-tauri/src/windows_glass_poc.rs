use std::{ffi::OsStr, sync::Mutex};

use crate::character_appearance::{AppearanceValues, InputVisualEffectMode};
use crate::input_visual_effect::InputVisualEffectStatus;

pub const FORCE_FAILURE_ENV: &str = "SAKURA_WINDOWS_INPUT_GLASS_FORCE_FAILURE";
pub const LIQUID_GLASS_POC_ENV: &str = "SAKURA_WINDOWS_LIQUID_GLASS_POC";

const INPUT_CORNER_RADIUS: f64 = 28.0;
const BASE_GAUSSIAN_STANDARD_DEVIATION: f32 = 8.0;

#[derive(Clone, Copy, Debug, PartialEq)]
struct NativeRegionGeometry {
    offset: [f32; 2],
    size: [f32; 2],
    corner_radius: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct NativeLayerVisibility {
    container: bool,
    gaussian: bool,
    liquid_requested: bool,
}

fn native_layer_visibility(
    mode: InputVisualEffectMode,
    has_geometry: bool,
) -> NativeLayerVisibility {
    NativeLayerVisibility {
        container: mode != InputVisualEffectMode::Solid && has_geometry,
        gaussian: mode == InputVisualEffectMode::GaussianBlur && has_geometry,
        liquid_requested: mode == InputVisualEffectMode::LiquidGlass,
    }
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

fn enabled_value(value: Option<&OsStr>) -> bool {
    value.and_then(OsStr::to_str).is_some_and(|value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "on"
        )
    })
}

pub struct WindowsInputGlassState {
    status: Mutex<InputVisualEffectStatus>,
    #[cfg(windows)]
    layer: Mutex<Option<NativeGlassLayer>>,
    force_failure: bool,
}

impl WindowsInputGlassState {
    pub fn from_environment() -> Self {
        Self {
            status: Mutex::new(if cfg!(windows) {
                InputVisualEffectStatus::pending()
            } else {
                InputVisualEffectStatus::unavailable()
            }),
            #[cfg(windows)]
            layer: Mutex::new(None),
            force_failure: enabled_value(std::env::var_os(FORCE_FAILURE_ENV).as_deref()),
        }
    }

    pub fn status(&self) -> InputVisualEffectStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| InputVisualEffectStatus::failed("INPUT_GLASS_STATE_UNAVAILABLE"))
    }

    pub fn install(&self, window: &tauri::WebviewWindow) {
        if self.force_failure {
            self.record_failure(
                "INPUT_GLASS_FORCED_FAILURE",
                "forced by the input glass failure switch",
            );
            return;
        }

        #[cfg(windows)]
        {
            if enabled_value(std::env::var_os(LIQUID_GLASS_POC_ENV).as_deref()) {
                eprintln!(
                    "[windows-input-glass] LIQUID_GLASS_UNSAFE_BACKEND_RETIRED: ignoring legacy PoC switch"
                );
            }
            match NativeGlassLayer::install(window) {
                Ok(layer) => match self.layer.lock() {
                    Ok(mut slot) => {
                        *slot = Some(layer);
                        self.set_status(InputVisualEffectStatus::ready(
                            InputVisualEffectMode::Solid,
                        ));
                        eprintln!("[windows-input-glass] host backdrop backend initialized hidden");
                    }
                    Err(_) => self.record_failure(
                        "INPUT_GLASS_STATE_UNAVAILABLE",
                        "native input glass object store is unavailable",
                    ),
                },
                Err(error) => self.record_failure(error.code, &error.detail),
            }
        }

        #[cfg(not(windows))]
        let _ = window;
    }

    pub fn update_appearance(
        &self,
        values: &AppearanceValues,
    ) -> Result<InputVisualEffectStatus, String> {
        if self.status().outcome == "degraded" {
            return Ok(self.status());
        }
        #[cfg(windows)]
        {
            let result = self
                .layer
                .lock()
                .map_err(|_| "native input glass object store is unavailable".to_string())?
                .as_ref()
                .map(|layer| layer.update_appearance(values))
                .transpose();
            match result {
                Err(error) => {
                    self.record_failure(error.code, &error.detail);
                    return Ok(self.status());
                }
                Ok(Some(outcome)) => {
                    let mut status = if let Some(code) = outcome.error_code {
                        InputVisualEffectStatus::limited(outcome.effective_mode, code)
                    } else {
                        InputVisualEffectStatus::ready(InputVisualEffectMode::Solid)
                    };
                    status.effective_mode = outcome.effective_mode;
                    self.set_status(status);
                }
                Ok(None) => {}
            }
        }
        #[cfg(not(windows))]
        let _ = values;
        Ok(self.status())
    }

    pub fn update_control_surface(
        &self,
        surface: &crate::window_geometry::ControlSurfaceLayout,
        application: &crate::window_geometry::LayoutApplication,
        previous_surface: Option<&crate::window_geometry::ControlSurfaceLayout>,
        transition: Option<crate::window_geometry::InputSurfaceTransition>,
    ) -> Result<(), String> {
        #[cfg(windows)]
        {
            if self.status().outcome == "degraded" {
                return Ok(());
            }
            let result = self
                .layer
                .lock()
                .map_err(|_| "native glass object store is unavailable".to_string())?
                .as_ref()
                .map(|layer| {
                    layer.update_control_surface(surface, application, previous_surface, transition)
                })
                .transpose();
            if let Err(error) = result {
                self.record_failure(error.code, &error.detail);
            }
        }
        #[cfg(not(windows))]
        let _ = (surface, application, previous_surface, transition);
        Ok(())
    }

    fn set_status(&self, next: InputVisualEffectStatus) {
        if let Ok(mut status) = self.status.lock() {
            *status = next;
        }
    }

    fn record_failure(&self, code: &'static str, detail: &str) {
        #[cfg(windows)]
        if let Ok(layer) = self.layer.lock() {
            if let Some(layer) = layer.as_ref() {
                let _ = layer.input_region.set_visible(false);
            }
        }
        self.set_status(InputVisualEffectStatus::failed(code));
        eprintln!("[windows-input-glass] {code}: {detail}; continuing with solid input");
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
    compositor: windows::UI::Composition::Compositor,
    _target: windows::UI::Composition::Desktop::DesktopWindowTarget,
    _root: windows::UI::Composition::ContainerVisual,
    _backdrop_brush: windows::UI::Composition::CompositionBackdropBrush,
    _blur_factory: windows::UI::Composition::CompositionEffectFactory,
    _blur_brush: windows::UI::Composition::CompositionEffectBrush,
    input_region: NativeGlassRegion,
    primary_overlay_brush: windows::UI::Composition::CompositionColorBrush,
    theme_tint_brush: windows::UI::Composition::CompositionColorBrush,
    liquid: Option<crate::windows_liquid_glass_native::SinglePipelineController>,
    liquid_install_error: Option<&'static str>,
    latest_surface: Mutex<Option<(crate::window_geometry::ControlSurfaceLayout, [u32; 4], f64)>>,
    requested_mode: Mutex<InputVisualEffectMode>,
}

#[cfg(windows)]
struct NativeAppearanceOutcome {
    effective_mode: InputVisualEffectMode,
    error_code: Option<&'static str>,
}

#[cfg(windows)]
struct NativeGlassRegion {
    container: windows::UI::Composition::ContainerVisual,
    blur_visual: windows::UI::Composition::SpriteVisual,
    _primary_overlay_visual: windows::UI::Composition::SpriteVisual,
    _theme_tint_visual: windows::UI::Composition::SpriteVisual,
    clip: windows::UI::Composition::RectangleClip,
}

#[cfg(windows)]
impl NativeGlassRegion {
    fn create(
        compositor: &windows::UI::Composition::Compositor,
        blur_brush: &windows::UI::Composition::CompositionEffectBrush,
        primary_overlay_brush: &windows::UI::Composition::CompositionColorBrush,
        theme_tint_brush: &windows::UI::Composition::CompositionColorBrush,
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

        let primary_overlay_visual = compositor.CreateSpriteVisual()?;
        primary_overlay_visual.SetRelativeSizeAdjustment(fill)?;
        primary_overlay_visual.SetBrush(primary_overlay_brush)?;
        container.Children()?.InsertAtTop(&primary_overlay_visual)?;

        let theme_tint_visual = compositor.CreateSpriteVisual()?;
        theme_tint_visual.SetRelativeSizeAdjustment(fill)?;
        theme_tint_visual.SetBrush(theme_tint_brush)?;
        container.Children()?.InsertAtTop(&theme_tint_visual)?;

        let clip = compositor.CreateRectangleClip()?;
        container.SetClip(&clip)?;

        Ok(Self {
            container,
            blur_visual,
            _primary_overlay_visual: primary_overlay_visual,
            _theme_tint_visual: theme_tint_visual,
            clip,
        })
    }

    fn update(
        &self,
        compositor: &windows::UI::Composition::Compositor,
        rect: [u32; 4],
        scale: f64,
        active_origin: [u32; 2],
        logical_corner_radius: f64,
        previous_rect: Option<[u32; 4]>,
        transition: Option<crate::window_geometry::InputSurfaceTransition>,
    ) -> windows::core::Result<()> {
        use windows::{core::HSTRING, Foundation::TimeSpan};
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
        self.clip.StopAnimation(&HSTRING::from("Bottom"))?;
        if let (Some(previous_rect), Some(transition)) = (previous_rect, transition) {
            if transition.duration_ms > 0 {
                let previous = native_region_geometry(
                    previous_rect,
                    scale,
                    active_origin,
                    logical_corner_radius,
                )
                .map_err(|message| windows::core::Error::new(E_INVALIDARG_HRESULT, message))?;
                let previous_bottom = previous.offset[1] + previous.size[1];
                let target_bottom = geometry.offset[1] + geometry.size[1];
                if (previous_bottom - target_bottom).abs() > 0.5 {
                    let animation = compositor.CreateScalarKeyFrameAnimation()?;
                    let easing = compositor.CreateCubicBezierEasingFunction(
                        Vector2 { X: 0.22, Y: 1.0 },
                        Vector2 { X: 0.36, Y: 1.0 },
                    )?;
                    animation.SetDuration(TimeSpan {
                        Duration: i64::from(transition.duration_ms) * 10_000,
                    })?;
                    animation.InsertKeyFrame(0.0, previous_bottom)?;
                    animation.InsertKeyFrameWithEasingFunction(1.0, target_bottom, &easing)?;
                    self.clip
                        .StartAnimation(&HSTRING::from("Bottom"), &animation)?;
                }
            }
        }
        self.container.SetIsVisible(true)
    }

    fn set_visible(&self, visible: bool) -> windows::core::Result<()> {
        self.container.SetIsVisible(visible)
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
            GaussianBlurEffectDescription::new(BASE_GAUSSIAN_STANDARD_DEVIATION, border_source)
                .into();
        blur_effect
            .cast::<IGraphicsEffectSource>()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_EFFECT_SOURCE_MISSING", error))?;
        blur_effect
            .cast::<IGraphicsEffectD2D1Interop>()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_INTEROP_MISSING", error))?;
        let animatable_properties = windows_collections::IIterable::from(vec![HSTRING::from(
            "SakuraGaussianBlur.StandardDeviation",
        )]);
        let blur_factory = compositor
            .CreateEffectFactoryWithProperties(&blur_effect, &animatable_properties)
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_FACTORY_CREATE_FAILED", error))?;
        let blur_brush = blur_factory
            .CreateBrush()
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_BRUSH_CREATE_FAILED", error))?;
        blur_brush
            .SetSourceParameter(&HSTRING::from("backdrop"), &backdrop_brush)
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_SOURCE_BIND_FAILED", error))?;
        blur_brush
            .Properties()
            .and_then(|properties| {
                properties.InsertScalar(
                    &HSTRING::from("SakuraGaussianBlur.StandardDeviation"),
                    BASE_GAUSSIAN_STANDARD_DEVIATION,
                )
            })
            .map_err(|error| {
                NativeGlassError::at("GLASS_BLUR_STRENGTH_INITIALIZE_FAILED", error)
            })?;
        let primary_overlay_brush = compositor
            .CreateColorBrushWithColor(Color {
                A: 24,
                R: 0,
                G: 0,
                B: 0,
            })
            .map_err(|error| NativeGlassError::at("GLASS_PRIMARY_OVERLAY_CREATE_FAILED", error))?;
        let theme_tint_brush = compositor
            .CreateColorBrushWithColor(Color {
                A: 55,
                R: 255,
                G: 255,
                B: 255,
            })
            .map_err(|error| NativeGlassError::at("GLASS_THEME_TINT_CREATE_FAILED", error))?;
        let input_region = NativeGlassRegion::create(
            &compositor,
            &blur_brush,
            &primary_overlay_brush,
            &theme_tint_brush,
        )
        .map_err(|error| NativeGlassError::at("GLASS_INPUT_REGION_CREATE_FAILED", error))?;
        // Installing the controller creates only one hidden composition visual.
        // Capture/D3D resources remain lazy until the user selects Liquid Glass.
        let (liquid, liquid_install_error) =
            match crate::windows_liquid_glass_native::SinglePipelineController::install(
                hwnd,
                &compositor,
                &input_region.container,
                &input_region.blur_visual,
            ) {
                Ok(liquid) => (Some(liquid), None),
                Err(error) => {
                    eprintln!(
                    "[windows-input-glass] {}: {}; liquid unavailable, no substitute effect enabled",
                    error.code, error.detail
                );
                    (None, Some(error.code))
                }
            };
        root.Children()
            .and_then(|children| children.InsertAtTop(&input_region.container))
            .map_err(|error| NativeGlassError::at("GLASS_REGION_INSERT_FAILED", error))?;

        target
            .SetRoot(&root)
            .map_err(|error| NativeGlassError::at("GLASS_ROOT_ATTACH_FAILED", error))?;

        Ok(Self {
            _dispatcher_controller: dispatcher_controller,
            compositor,
            _target: target,
            _root: root,
            _backdrop_brush: backdrop_brush,
            _blur_factory: blur_factory,
            _blur_brush: blur_brush,
            input_region,
            primary_overlay_brush,
            theme_tint_brush,
            liquid,
            liquid_install_error,
            latest_surface: Mutex::new(None),
            requested_mode: Mutex::new(InputVisualEffectMode::Solid),
        })
    }

    fn update_appearance(
        &self,
        values: &AppearanceValues,
    ) -> Result<NativeAppearanceOutcome, NativeGlassError> {
        use windows::UI::Color;

        let primary = parse_hex(values.theme_tokens.get("primary")).ok_or_else(|| {
            NativeGlassError::at("GLASS_PRIMARY_COLOR_INVALID", "invalid primary")
        })?;
        let bubble = parse_hex(values.theme_tokens.get("bubbleBackground")).ok_or_else(|| {
            NativeGlassError::at(
                "GLASS_THEME_TINT_COLOR_INVALID",
                "invalid bubble background",
            )
        })?;
        self.primary_overlay_brush
            .SetColor(Color {
                A: 24,
                R: ((f32::from(primary[0]) * 0.35).round() as u8),
                G: ((f32::from(primary[1]) * 0.35).round() as u8),
                B: ((f32::from(primary[2]) * 0.35).round() as u8),
            })
            .map_err(|error| NativeGlassError::at("GLASS_PRIMARY_OVERLAY_UPDATE_FAILED", error))?;
        self.theme_tint_brush
            .SetColor(Color {
                A: 55,
                R: bubble[0],
                G: bubble[1],
                B: bubble[2],
            })
            .map_err(|error| NativeGlassError::at("GLASS_THEME_TINT_UPDATE_FAILED", error))?;
        if let Some(liquid) = self.liquid.as_ref() {
            if let Err(error) = liquid.update_tint([255, 255, 255], 0.0) {
                eprintln!(
                    "[windows-input-glass] {}: {}; liquid tint update skipped",
                    error.code, error.detail
                );
            }
        }
        let requested_mode = values.visual_effect_mode;
        let has_geometry = self
            .latest_surface
            .lock()
            .map_err(|_| NativeGlassError::at("GLASS_LAYOUT_STATE_UNAVAILABLE", "layout lock"))?
            .is_some();
        let visibility = native_layer_visibility(requested_mode, has_geometry);
        let mut liquid_error = None;
        if let Some(liquid) = self.liquid.as_ref() {
            if let Err(error) = liquid.set_requested_visible(visibility.liquid_requested) {
                eprintln!(
                    "[windows-input-glass] {}: {}; liquid unavailable, no substitute effect enabled",
                    error.code, error.detail
                );
                liquid_error = Some(error.code);
            }
        } else if visibility.liquid_requested {
            liquid_error = Some(
                self.liquid_install_error
                    .unwrap_or("LIQUID_GLASS_BACKEND_UNAVAILABLE"),
            );
        }
        self.input_region
            .blur_visual
            .SetIsVisible(visibility.gaussian)
            .map_err(|error| NativeGlassError::at("GLASS_GAUSSIAN_VISIBILITY_FAILED", error))?;
        *self
            .requested_mode
            .lock()
            .map_err(|_| NativeGlassError::at("GLASS_MODE_STATE_UNAVAILABLE", "mode lock"))? =
            requested_mode;
        self.input_region
            .set_visible(visibility.container)
            .map_err(|error| NativeGlassError::at("GLASS_REGION_VISIBILITY_FAILED", error))?;
        Ok(NativeAppearanceOutcome {
            effective_mode: requested_mode,
            error_code: liquid_error,
        })
    }

    fn update_control_surface(
        &self,
        surface: &crate::window_geometry::ControlSurfaceLayout,
        application: &crate::window_geometry::LayoutApplication,
        previous_surface: Option<&crate::window_geometry::ControlSurfaceLayout>,
        transition: Option<crate::window_geometry::InputSurfaceTransition>,
    ) -> Result<(), NativeGlassError> {
        let scale = application.scale_factor * application.content_scale;
        let [active_x, active_y, _, _] = application.active_bounds;
        let input_geometry = native_region_geometry(
            surface.input_rect,
            scale,
            [active_x, active_y],
            INPUT_CORNER_RADIUS,
        )
        .map_err(|error| NativeGlassError::at("GLASS_REGION_GEOMETRY_FAILED", error))?;
        let blur_standard_deviation = BASE_GAUSSIAN_STANDARD_DEVIATION * scale as f32;
        self._blur_brush
            .Properties()
            .and_then(|properties| {
                properties.InsertScalar(
                    &windows::core::HSTRING::from("SakuraGaussianBlur.StandardDeviation"),
                    blur_standard_deviation,
                )
            })
            .map_err(|error| NativeGlassError::at("GLASS_BLUR_STRENGTH_UPDATE_FAILED", error))?;
        eprintln!(
            "[windows-input-glass] region active={:?} input={:?} scale={scale:.6} blur={:.3} offset={:?} size={:?} placement={}x{}",
            application.active_bounds,
            surface.input_rect,
            blur_standard_deviation,
            input_geometry.offset,
            input_geometry.size,
            application.physical_placement.width,
            application.physical_placement.height,
        );
        self.input_region
            .update(
                &self.compositor,
                surface.input_rect,
                scale,
                [active_x, active_y],
                INPUT_CORNER_RADIUS,
                previous_surface.map(|value| value.input_rect),
                transition,
            )
            .map_err(|error| NativeGlassError::at("GLASS_REGION_LAYOUT_FAILED", error))?;
        if let Some(liquid) = self.liquid.as_ref() {
            let liquid_geometry = crate::windows_liquid_glass::sampling_geometry(
                surface.input_rect,
                [active_x, active_y],
                [
                    application.physical_placement.x,
                    application.physical_placement.y,
                ],
                [application.work_area.x, application.work_area.y],
                [application.work_area.width, application.work_area.height],
                application.scale_factor,
                application.content_scale,
            )
            .map_err(|error| NativeGlassError::at("LIQUID_GLASS_GEOMETRY_FAILED", error))?;
            if let Err(error) = liquid.update_geometry(liquid_geometry) {
                eprintln!(
                    "[windows-input-glass] {}: {}; liquid fused, no substitute effect enabled",
                    error.code, error.detail
                );
                let _ = liquid.set_requested_visible(false);
            }
        }
        *self
            .latest_surface
            .lock()
            .map_err(|_| NativeGlassError::at("GLASS_LAYOUT_STATE_UNAVAILABLE", "layout lock"))? =
            Some((surface.clone(), application.active_bounds, scale));
        let requested_mode = *self
            .requested_mode
            .lock()
            .map_err(|_| NativeGlassError::at("GLASS_MODE_STATE_UNAVAILABLE", "mode lock"))?;
        let visibility = native_layer_visibility(requested_mode, true);
        self.input_region
            .blur_visual
            .SetIsVisible(visibility.gaussian)
            .map_err(|error| NativeGlassError::at("GLASS_GAUSSIAN_VISIBILITY_FAILED", error))?;
        self.input_region
            .set_visible(visibility.container)
            .map_err(|error| NativeGlassError::at("GLASS_REGION_VISIBILITY_FAILED", error))
    }
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
        name: &windows::core::PCWSTR,
        index: *mut u32,
        mapping: *mut windows::Win32::System::WinRT::Graphics::Direct2D::GRAPHICS_EFFECT_PROPERTY_MAPPING,
    ) -> windows::core::Result<()> {
        use windows::Win32::System::WinRT::Graphics::Direct2D::GRAPHICS_EFFECT_PROPERTY_MAPPING_DIRECT;

        if unsafe { name.to_string() }.ok().as_deref() != Some("StandardDeviation")
            || index.is_null()
            || mapping.is_null()
        {
            return Err(E_INVALIDARG_HRESULT.into());
        }
        unsafe {
            *index = 0;
            *mapping = GRAPHICS_EFFECT_PROPERTY_MAPPING_DIRECT;
        }
        Ok(())
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
    fn explicit_truthy_values_enable_failure_injection() {
        for value in ["1", "true", "TRUE", " on "] {
            assert!(enabled_value(Some(OsStr::new(value))), "{value}");
        }
    }

    #[test]
    fn missing_or_ambiguous_values_leave_failure_injection_disabled() {
        assert!(!enabled_value(None));
        for value in ["", "0", "false", "yes", "enabled"] {
            assert!(!enabled_value(Some(OsStr::new(value))), "{value}");
        }
    }

    #[test]
    fn status_contract_separates_request_activation_and_failure() {
        assert_eq!(
            InputVisualEffectStatus::unavailable().outcome,
            "unavailable"
        );
        assert!(!InputVisualEffectStatus::pending().initialized);
        assert!(InputVisualEffectStatus::ready(InputVisualEffectMode::Solid).initialized);
        assert_eq!(
            InputVisualEffectStatus::failed("GLASS_TEST").error_code,
            Some("GLASS_TEST")
        );
        let limited = InputVisualEffectStatus::limited(
            InputVisualEffectMode::LiquidGlass,
            "LIQUID_GLASS_CAPTURE_ISOLATION_UNAVAILABLE",
        );
        assert_eq!(limited.outcome, "limited");
        assert_eq!(limited.effective_mode, InputVisualEffectMode::LiquidGlass);
        assert!(limited.initialized);
    }

    #[test]
    fn liquid_mode_never_enables_the_gaussian_layer_as_a_fallback() {
        let liquid = native_layer_visibility(InputVisualEffectMode::LiquidGlass, true);
        assert!(liquid.container);
        assert!(liquid.liquid_requested);
        assert!(!liquid.gaussian);

        let gaussian = native_layer_visibility(InputVisualEffectMode::GaussianBlur, true);
        assert!(gaussian.container);
        assert!(gaussian.gaussian);
        assert!(!gaussian.liquid_requested);

        assert!(!native_layer_visibility(InputVisualEffectMode::Solid, true).container);
        assert!(!native_layer_visibility(InputVisualEffectMode::LiquidGlass, false).container);
    }

    #[test]
    fn blur_strength_scales_from_legacy_equivalent_logical_radius() {
        assert_eq!(BASE_GAUSSIAN_STANDARD_DEVIATION * 1.0, 8.0);
        assert_eq!(BASE_GAUSSIAN_STANDARD_DEVIATION * 1.5, 12.0);
    }

    #[test]
    fn theme_hex_parser_is_strict() {
        assert_eq!(
            parse_hex(Some(&"#d55b91".to_string())),
            Some([213, 91, 145])
        );
        assert_eq!(parse_hex(Some(&"d55b91".to_string())), None);
        assert_eq!(parse_hex(Some(&"#xyzxyz".to_string())), None);
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
