//! Safety-gated core for WP-3-03D's single-pipeline Windows liquid glass backend.
//!
//! Windows Graphics Capture is only the dynamic background source. All blur and
//! optical work happens in one D3D11 pipeline whose final texture is exposed to
//! DWM as one ordinary composition surface. This module deliberately contains no
//! HostBackdrop effect graph and cannot activate from the retired legacy switch.

use std::ffi::OsStr;
#[cfg(test)]
use std::sync::atomic::{AtomicBool, Ordering};

pub const DEBUG_STEP_ENV: &str = "SAKURA_WINDOWS_LIQUID_GLASS_DEBUG_STEP";
pub const REFRACTION_THICKNESS: f32 = 20.0;
pub const REFRACTION_FACTOR: f32 = 1.4;
pub const DISPERSION: f32 = 7.0;
pub const FRESNEL_RANGE: f32 = 30.0;
pub const FRESNEL_HARDNESS: f32 = 0.2;
pub const FRESNEL_FACTOR: f32 = 0.2;
pub const GLARE_RANGE: f32 = 30.0;
pub const GLARE_HARDNESS: f32 = 0.2;
pub const GLARE_ANGLE_RADIANS: f32 = -46.1_f32.to_radians();
pub const GLARE_FACTOR: f32 = 0.9036;
pub const GLARE_OPPOSITE: f32 = 0.8;
pub const GLARE_CONVERGENCE: f32 = 0.5;
pub const LIQUID_BLUR_RADIUS: f32 = 10.0;
pub const LIQUID_BLUR_SIGMA: f32 = LIQUID_BLUR_RADIUS / 3.0;

pub const MAX_CAPTURE_SESSIONS: u8 = 1;
pub const MAX_CAPTURE_BUFFERS: u8 = 2;
pub const MAX_INTERMEDIATE_TEXTURES: u8 = 2;
pub const MAX_SWAP_CHAIN_BUFFERS: u8 = 2;
pub const MAX_SURFACE_VISUALS: u8 = 1;
pub const MAX_CUSTOM_COMPOSITION_EFFECT_GRAPHS: u8 = 0;

const CAPTURE_PADDING_LOGICAL: f64 = 32.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DebugStep {
    Sdf = 0,
    SdfContours = 1,
    Normals = 2,
    EdgeFactor = 3,
    EdgeNormal = 4,
    Blur = 5,
    Refraction = 6,
    Fresnel = 7,
    Glare = 8,
    Composite = 9,
}

impl DebugStep {
    fn parse(value: Option<&OsStr>) -> Self {
        match value.and_then(OsStr::to_str).map(str::trim) {
            Some("0") => Self::Sdf,
            Some("1") => Self::SdfContours,
            Some("2") => Self::Normals,
            Some("3") => Self::EdgeFactor,
            Some("4") => Self::EdgeNormal,
            Some("5") => Self::Blur,
            Some("6") => Self::Refraction,
            Some("7") => Self::Fresnel,
            Some("8") => Self::Glare,
            _ => Self::Composite,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ResourceBudget {
    pub capture_sessions: u8,
    pub capture_buffers: u8,
    pub intermediate_textures: u8,
    pub swap_chain_buffers: u8,
    pub surface_visuals: u8,
    pub custom_composition_effect_graphs: u8,
}

impl ResourceBudget {
    pub const fn running() -> Self {
        Self {
            capture_sessions: 1,
            capture_buffers: 2,
            intermediate_textures: 2,
            swap_chain_buffers: 2,
            surface_visuals: 1,
            custom_composition_effect_graphs: 0,
        }
    }

    pub const fn within_limit(self) -> bool {
        self.capture_sessions <= MAX_CAPTURE_SESSIONS
            && self.capture_buffers <= MAX_CAPTURE_BUFFERS
            && self.intermediate_textures <= MAX_INTERMEDIATE_TEXTURES
            && self.swap_chain_buffers <= MAX_SWAP_CHAIN_BUFFERS
            && self.surface_visuals <= MAX_SURFACE_VISUALS
            && self.custom_composition_effect_graphs <= MAX_CUSTOM_COMPOSITION_EFFECT_GRAPHS
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Lifecycle {
    Armed,
    Running,
    Fused,
}

#[cfg(test)]
#[derive(Debug)]
pub struct LifecycleGate {
    phase: Lifecycle,
    frame_busy: AtomicBool,
}

#[cfg(test)]
impl LifecycleGate {
    pub const fn phase(&self) -> Lifecycle {
        self.phase
    }

    pub fn start(&mut self, resources: ResourceBudget) -> Result<(), &'static str> {
        if self.phase != Lifecycle::Armed {
            return Err("LIQUID_GLASS_NOT_ARMED");
        }
        if !resources.within_limit() {
            self.phase = Lifecycle::Fused;
            return Err("LIQUID_GLASS_RESOURCE_BUDGET_EXCEEDED");
        }
        self.phase = Lifecycle::Running;
        Ok(())
    }

    pub fn try_begin_frame(&self) -> Option<FramePermit<'_>> {
        if self.phase != Lifecycle::Running
            || self
                .frame_busy
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_err()
        {
            return None;
        }
        Some(FramePermit {
            busy: &self.frame_busy,
        })
    }
}

#[cfg(test)]
pub struct FramePermit<'a> {
    busy: &'a AtomicBool,
}

#[cfg(test)]
impl Drop for FramePermit<'_> {
    fn drop(&mut self) {
        self.busy.store(false, Ordering::Release);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PhysicalRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SamplingGeometry {
    pub input_surface: PhysicalRect,
    pub input_screen: PhysicalRect,
    pub monitor_crop: PhysicalRect,
    pub corner_radius: f32,
    pub blur_sigma: f32,
    pub effect_scale: f32,
}

pub fn sampling_geometry(
    input_rect: [u32; 4],
    active_origin: [u32; 2],
    window_origin: [i32; 2],
    monitor_origin: [i32; 2],
    monitor_size: [u32; 2],
    scale_factor: f64,
    content_scale: f64,
) -> Result<SamplingGeometry, &'static str> {
    let scale = scale_factor * content_scale;
    if !scale.is_finite() || scale <= 0.0 || monitor_size[0] == 0 || monitor_size[1] == 0 {
        return Err("LIQUID_GLASS_GEOMETRY_SCALE_INVALID");
    }
    let [x, y, width, height] = input_rect;
    if width == 0 || height == 0 || x < active_origin[0] || y < active_origin[1] {
        return Err("LIQUID_GLASS_INPUT_REGION_INVALID");
    }
    let left = ((f64::from(x - active_origin[0])) * scale).floor() as i64;
    let top = ((f64::from(y - active_origin[1])) * scale).floor() as i64;
    let right = ((f64::from(x.saturating_add(width) - active_origin[0])) * scale).ceil() as i64;
    let bottom = ((f64::from(y.saturating_add(height) - active_origin[1])) * scale).ceil() as i64;
    let surface = checked_rect(left, top, right, bottom)?;
    let screen_left = i64::from(window_origin[0]) + left;
    let screen_top = i64::from(window_origin[1]) + top;
    let screen = checked_rect(
        screen_left,
        screen_top,
        screen_left + i64::from(surface.width),
        screen_top + i64::from(surface.height),
    )?;

    let padding = (CAPTURE_PADDING_LOGICAL * scale).ceil() as i64;
    let mon_left = i64::from(monitor_origin[0]);
    let mon_top = i64::from(monitor_origin[1]);
    let mon_right = mon_left + i64::from(monitor_size[0]);
    let mon_bottom = mon_top + i64::from(monitor_size[1]);
    let crop_screen_left = (screen_left - padding).max(mon_left);
    let crop_screen_top = (screen_top - padding).max(mon_top);
    let crop_screen_right = (screen_left + i64::from(surface.width) + padding).min(mon_right);
    let crop_screen_bottom = (screen_top + i64::from(surface.height) + padding).min(mon_bottom);
    let crop = checked_rect(
        crop_screen_left - mon_left,
        crop_screen_top - mon_top,
        crop_screen_right - mon_left,
        crop_screen_bottom - mon_top,
    )?;

    Ok(SamplingGeometry {
        input_surface: surface,
        input_screen: screen,
        monitor_crop: crop,
        corner_radius: (28.0 * scale).min(f64::from(surface.width.min(surface.height)) / 2.0)
            as f32,
        blur_sigma: LIQUID_BLUR_SIGMA * scale as f32,
        effect_scale: scale as f32,
    })
}

fn checked_rect(
    left: i64,
    top: i64,
    right: i64,
    bottom: i64,
) -> Result<PhysicalRect, &'static str> {
    if right <= left
        || bottom <= top
        || left < i64::from(i32::MIN)
        || top < i64::from(i32::MIN)
        || right > i64::from(i32::MAX)
        || bottom > i64::from(i32::MAX)
    {
        return Err("LIQUID_GLASS_RECT_INVALID");
    }
    Ok(PhysicalRect {
        x: left as i32,
        y: top as i32,
        width: (right - left) as u32,
        height: (bottom - top) as u32,
    })
}

#[cfg(test)]
pub fn refraction_edge_factor(depth: f32, thickness: f32, factor: f32) -> f32 {
    if !depth.is_finite()
        || !thickness.is_finite()
        || !factor.is_finite()
        || depth < 0.0
        || thickness <= 0.0
        || factor <= 0.0
        || depth >= thickness
    {
        return 0.0;
    }
    let ratio = (1.0 - depth / thickness).clamp(0.0, 1.0);
    let theta_i = (ratio * ratio).clamp(-1.0, 1.0).asin();
    let theta_t = (theta_i.sin() / factor).clamp(-1.0, 1.0).asin();
    let result = -(theta_t - theta_i).tan();
    if result.is_finite() {
        result.max(0.0)
    } else {
        0.0
    }
}

pub fn configured_debug_step() -> DebugStep {
    DebugStep::parse(std::env::var_os(DEBUG_STEP_ENV).as_deref())
}

pub const FULLSCREEN_VERTEX_HLSL: &str =
    include_str!("windows_liquid_glass_shaders/fullscreen.hlsl");
pub const BLUR_PIXEL_HLSL: &str = include_str!("windows_liquid_glass_shaders/blur.hlsl");
pub const LIQUID_PIXEL_HLSL: &str = include_str!("windows_liquid_glass_shaders/liquid.hlsl");
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resource_budget_is_one_pipeline_and_rejects_excess() {
        assert!(ResourceBudget::running().within_limit());
        assert_eq!(
            ResourceBudget::running().custom_composition_effect_graphs,
            0
        );
        assert!(!ResourceBudget {
            surface_visuals: 2,
            ..ResourceBudget::running()
        }
        .within_limit());
    }

    #[test]
    fn lifecycle_fuses_permanently_on_budget_failure() {
        let mut gate = LifecycleGate {
            phase: Lifecycle::Armed,
            frame_busy: AtomicBool::new(false),
        };
        assert_eq!(
            gate.start(ResourceBudget {
                capture_sessions: 2,
                ..ResourceBudget::running()
            }),
            Err("LIQUID_GLASS_RESOURCE_BUDGET_EXCEEDED")
        );
        assert_eq!(gate.phase(), Lifecycle::Fused);
        assert_eq!(
            gate.start(ResourceBudget::running()),
            Err("LIQUID_GLASS_NOT_ARMED")
        );
    }

    #[test]
    fn busy_frame_is_dropped_instead_of_queued() {
        let gate = LifecycleGate {
            phase: Lifecycle::Running,
            frame_busy: AtomicBool::new(false),
        };
        let first = gate.try_begin_frame().expect("first frame");
        assert!(gate.try_begin_frame().is_none());
        drop(first);
        assert!(gate.try_begin_frame().is_some());
    }

    #[test]
    fn debug_step_parser_is_strict_and_defaults_to_composite() {
        assert_eq!(DebugStep::parse(Some(OsStr::new("0"))), DebugStep::Sdf);
        assert_eq!(DebugStep::parse(Some(OsStr::new("8"))), DebugStep::Glare);
        assert_eq!(
            DebugStep::parse(Some(OsStr::new("9"))),
            DebugStep::Composite
        );
        assert_eq!(
            DebugStep::parse(Some(OsStr::new("10"))),
            DebugStep::Composite
        );
        assert_eq!(
            DebugStep::parse(Some(OsStr::new("pink"))),
            DebugStep::Composite
        );
    }

    #[test]
    fn sampling_geometry_covers_fractional_edges_and_monitor_crop() {
        let geometry = sampling_geometry(
            [101, 50, 241, 81],
            [100, 40],
            [1800, 900],
            [1920, 0],
            [2560, 1440],
            1.5,
            1.0,
        )
        .unwrap();
        assert_eq!(
            geometry.input_surface,
            PhysicalRect {
                x: 1,
                y: 15,
                width: 362,
                height: 122
            }
        );
        assert_eq!(geometry.input_screen.x, 1801);
        assert_eq!(geometry.monitor_crop.x, 0);
        assert!((geometry.blur_sigma - 5.0).abs() < f32::EPSILON);
    }

    #[test]
    fn sampling_geometry_scales_at_100_and_150_percent() {
        let one = sampling_geometry(
            [20, 30, 200, 60],
            [0, 0],
            [100, 200],
            [0, 0],
            [1920, 1080],
            1.0,
            1.0,
        )
        .unwrap();
        let one_half = sampling_geometry(
            [20, 30, 200, 60],
            [0, 0],
            [100, 200],
            [0, 0],
            [1920, 1080],
            1.5,
            1.0,
        )
        .unwrap();
        assert_eq!(one.input_surface.width, 200);
        assert_eq!(one_half.input_surface.width, 300);
        assert!((one.blur_sigma - 10.0 / 3.0).abs() < f32::EPSILON);
        assert!((one_half.blur_sigma - 5.0).abs() < f32::EPSILON);
        assert_eq!(one.effect_scale, 1.0);
        assert_eq!(one_half.effect_scale, 1.5);
    }

    #[test]
    fn liquid_mode_strictly_parses_and_serializes() {
        let mode: crate::character_appearance::InputVisualEffectMode =
            serde_json::from_str("\"liquid_glass\"").unwrap();
        assert_eq!(
            mode,
            crate::character_appearance::InputVisualEffectMode::LiquidGlass
        );
        assert_eq!(serde_json::to_string(&mode).unwrap(), "\"liquid_glass\"");
        assert!(
            serde_json::from_str::<crate::character_appearance::InputVisualEffectMode>(
                "\"acrylic\""
            )
            .is_err()
        );
    }

    #[test]
    fn refraction_curve_is_finite_non_negative_and_reaches_zero() {
        let mut previous = f32::MAX;
        for depth in 0..=20 {
            let value =
                refraction_edge_factor(depth as f32, REFRACTION_THICKNESS, REFRACTION_FACTOR);
            assert!(value.is_finite() && value >= 0.0);
            assert!(value <= previous + f32::EPSILON);
            previous = value;
        }
        assert_eq!(previous, 0.0);
        assert_eq!(refraction_edge_factor(f32::NAN, 20.0, 1.4), 0.0);
    }
}
