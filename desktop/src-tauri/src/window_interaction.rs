use serde::Serialize;
#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
use std::collections::HashMap;

use crate::{
    character_presentation::PortraitAlphaMask,
    window_geometry::{ControlSurfaceLayout, LayoutContract, PresentationState},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NativeDragCompletion {
    // Each variant is constructed only by its platform-specific implementation;
    // both remain in the shared enum so callers can handle one stable contract.
    #[cfg_attr(not(windows), allow(dead_code))]
    SynchronousMoveLoop,
    #[cfg_attr(windows, allow(dead_code))]
    DeferredWindowMoved,
}

pub const fn native_drag_completion() -> NativeDragCompletion {
    #[cfg(windows)]
    {
        NativeDragCompletion::SynchronousMoveLoop
    }
    #[cfg(not(windows))]
    {
        NativeDragCompletion::DeferredWindowMoved
    }
}

#[cfg(any(windows, test))]
fn dragged_window_origin(
    initial_window: [i32; 2],
    initial_cursor: [i32; 2],
    current_cursor: [i32; 2],
) -> Result<[i32; 2], String> {
    let x =
        i64::from(initial_window[0]) + i64::from(current_cursor[0]) - i64::from(initial_cursor[0]);
    let y =
        i64::from(initial_window[1]) + i64::from(current_cursor[1]) - i64::from(initial_cursor[1]);
    Ok([
        i32::try_from(x).map_err(|_| "native drag x coordinate overflow".to_string())?,
        i32::try_from(y).map_err(|_| "native drag y coordinate overflow".to_string())?,
    ])
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HitKind {
    Transparent,
    Interactive,
    Drag,
    Neutral,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LogicalHitRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub corner_radius: u32,
}

impl LogicalHitRect {
    pub const fn new(x: i32, y: i32, width: u32, height: u32) -> Self {
        Self {
            x,
            y,
            width,
            height,
            corner_radius: 0,
        }
    }

    const fn with_corner_radius(mut self, corner_radius: u32) -> Self {
        self.corner_radius = corner_radius;
        self
    }

    pub fn checked(
        x: i32,
        y: i32,
        width: u32,
        height: u32,
        envelope: [u32; 2],
    ) -> Result<Self, String> {
        let right = i64::from(x) + i64::from(width);
        let bottom = i64::from(y) + i64::from(height);
        if x < 0
            || y < 0
            || width == 0
            || height == 0
            || right > i64::from(envelope[0])
            || bottom > i64::from(envelope[1])
        {
            return Err("hit rectangle escapes native window envelope".to_string());
        }
        Ok(Self::new(x, y, width, height))
    }

    pub fn contains(self, point: [i32; 2]) -> bool {
        let inside_bounds = i64::from(point[0]) >= i64::from(self.x)
            && i64::from(point[0]) < i64::from(self.x) + i64::from(self.width)
            && i64::from(point[1]) >= i64::from(self.y)
            && i64::from(point[1]) < i64::from(self.y) + i64::from(self.height);
        if !inside_bounds || self.corner_radius == 0 {
            return inside_bounds;
        }
        let radius = f64::from(self.corner_radius.min(self.width / 2).min(self.height / 2));
        let local_x = f64::from(point[0] - self.x) + 0.5;
        let local_y = f64::from(point[1] - self.y) + 0.5;
        let width = f64::from(self.width);
        let height = f64::from(self.height);
        let dx = if local_x < radius {
            radius - local_x
        } else if local_x > width - radius {
            local_x - (width - radius)
        } else {
            0.0
        };
        let dy = if local_y < radius {
            radius - local_y
        } else if local_y > height - radius {
            local_y - (height - radius)
        } else {
            0.0
        };
        dx == 0.0 || dy == 0.0 || dx * dx + dy * dy <= radius * radius
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LogicalHitRegions {
    pub state: PresentationState,
    pub interactive: Vec<LogicalHitRect>,
    pub drag: Vec<LogicalHitRect>,
    pub neutral: Vec<LogicalHitRect>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalHitRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub corner_radius: u32,
}

impl PhysicalHitRect {
    #[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
    fn right(&self) -> i64 {
        i64::from(self.x) + i64::from(self.width)
    }

    #[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
    fn bottom(&self) -> i64 {
        i64::from(self.y) + i64::from(self.height)
    }

    #[cfg(any(target_os = "macos", target_os = "linux", test))]
    pub(crate) fn contains(self, point: [i32; 2]) -> bool {
        let inside_bounds = i64::from(point[0]) >= i64::from(self.x)
            && i64::from(point[0]) < self.right()
            && i64::from(point[1]) >= i64::from(self.y)
            && i64::from(point[1]) < self.bottom();
        if !inside_bounds || self.corner_radius == 0 {
            return inside_bounds;
        }
        let radius = f64::from(self.corner_radius.min(self.width / 2).min(self.height / 2));
        let local_x = f64::from(point[0] - self.x) + 0.5;
        let local_y = f64::from(point[1] - self.y) + 0.5;
        let width = f64::from(self.width);
        let height = f64::from(self.height);
        let dx = if local_x < radius {
            radius - local_x
        } else if local_x > width - radius {
            local_x - (width - radius)
        } else {
            0.0
        };
        let dy = if local_y < radius {
            radius - local_y
        } else if local_y > height - radius {
            local_y - (height - radius)
        } else {
            0.0
        };
        dx == 0.0 || dy == 0.0 || dx * dx + dy * dy <= radius * radius
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalHitRegions {
    pub state: PresentationState,
    pub scale: f64,
    #[serde(skip)]
    pub envelope: [u32; 2],
    pub interactive: Vec<PhysicalHitRect>,
    pub drag: Vec<PhysicalHitRect>,
    pub neutral: Vec<PhysicalHitRect>,
    #[serde(skip)]
    pub portrait_alpha_mask: Option<PortraitAlphaMask>,
    #[serde(skip)]
    pub extra_native_rectangles: Vec<PhysicalHitRect>,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HitRegionFallback {
    NotNeeded,
    RetainPrevious,
}

#[cfg(test)]
pub fn fallback_for_native_region_result(applied: bool) -> HitRegionFallback {
    if applied {
        HitRegionFallback::NotNeeded
    } else {
        HitRegionFallback::RetainPrevious
    }
}

fn translate_rect(
    rect: [u32; 4],
    offset: [u32; 2],
    envelope: [u32; 2],
    corner_radius: u32,
) -> Result<LogicalHitRect, String> {
    let x = i32::try_from(u64::from(rect[0]) + u64::from(offset[0]))
        .map_err(|_| "hit rectangle x coordinate overflow".to_string())?;
    let y = i32::try_from(u64::from(rect[1]) + u64::from(offset[1]))
        .map_err(|_| "hit rectangle y coordinate overflow".to_string())?;
    LogicalHitRect::checked(x, y, rect[2], rect[3], envelope)
        .map(|rect| rect.with_corner_radius(corner_radius))
}

const BUBBLE_CORNER_RADIUS: u32 = 22;
const INPUT_CORNER_RADIUS: u32 = 28;
const CONTROLS_CORNER_RADIUS: u32 = 15;
pub const PORTRAIT_SCALE_MIN_PERCENT: u16 = 50;
pub const PORTRAIT_SCALE_MAX_PERCENT: u16 = 150;

#[cfg(test)]
pub fn logical_hit_regions(
    contract: &LayoutContract,
    state: PresentationState,
) -> Result<LogicalHitRegions, String> {
    logical_hit_regions_with_portrait_size(contract, state, None)
}

#[cfg(test)]
pub fn logical_hit_regions_with_portrait_size(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_source_size: Option<[u32; 2]>,
) -> Result<LogicalHitRegions, String> {
    logical_hit_regions_with_portrait_transform(contract, state, portrait_source_size, 100)
}

#[cfg(test)]
pub fn logical_hit_regions_with_portrait_transform(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_source_size: Option<[u32; 2]>,
    portrait_scale_percent: u16,
) -> Result<LogicalHitRegions, String> {
    logical_hit_regions_with_control_surface(
        contract,
        state,
        portrait_source_size,
        portrait_scale_percent,
        None,
    )
}

pub fn logical_hit_regions_with_control_surface(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_source_size: Option<[u32; 2]>,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
) -> Result<LogicalHitRegions, String> {
    contract.validate()?;
    if let Some(surface) = control_surface {
        contract.validate_control_surface(state, surface)?;
    }
    let layout = contract
        .states
        .get(state.key())
        .ok_or_else(|| format!("missing layout state: {}", state.key()))?;
    let offset = [
        contract.viewport.portrait_anchor[0]
            .checked_sub(layout.portrait_anchor[0])
            .ok_or_else(|| "hit layout expands right of viewport anchor".to_string())?,
        contract.viewport.portrait_anchor[1]
            .checked_sub(layout.portrait_anchor[1])
            .ok_or_else(|| "hit layout expands below viewport anchor".to_string())?,
    ];
    let bubble_rect = control_surface
        .map(|surface| surface.bubble_rect)
        .or(layout.bubble_rect);
    let input_rect = control_surface
        .map(|surface| surface.input_rect)
        .or(layout.input_rect);
    let controls_rect = control_surface
        .map(|surface| surface.controls_rect)
        .unwrap_or(layout.controls_rect);
    let mut interactive = Vec::with_capacity(2);
    if let Some(rect) = input_rect {
        interactive.push(translate_rect(
            rect,
            offset,
            contract.viewport.window_size,
            INPUT_CORNER_RADIUS,
        )?);
    }
    interactive.push(translate_rect(
        controls_rect,
        offset,
        contract.viewport.window_size,
        CONTROLS_CORNER_RADIUS,
    )?);
    let portrait_rect = translate_rect(
        layout.portrait_rect,
        offset,
        contract.viewport.window_size,
        0,
    )?;
    let portrait_rect = match portrait_source_size {
        Some(source_size) => constrained_portrait_rect(
            contained_portrait_rect(portrait_rect, source_size)?,
            portrait_scale_percent,
            contract.viewport.window_size,
        )?,
        None => constrained_portrait_rect(
            portrait_rect,
            portrait_scale_percent,
            contract.viewport.window_size,
        )?,
    };
    let mut drag = Vec::with_capacity(2);
    drag.push(portrait_rect);
    if let Some(rect) = bubble_rect {
        drag.push(translate_rect(
            rect,
            offset,
            contract.viewport.window_size,
            BUBBLE_CORNER_RADIUS,
        )?);
    }
    Ok(LogicalHitRegions {
        state,
        interactive,
        drag,
        neutral: Vec::new(),
    })
}

#[cfg(test)]
pub fn logical_visible_surface_bounds(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_scale_percent: u16,
) -> Result<[u32; 4], String> {
    logical_visible_surface_bounds_with_control_surface(
        contract,
        state,
        portrait_scale_percent,
        None,
        None,
    )
}

pub fn logical_visible_surface_bounds_with_control_surface(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&PortraitAlphaMask>,
) -> Result<[u32; 4], String> {
    let mut regions = logical_hit_regions_with_control_surface(
        contract,
        state,
        portrait_alpha_mask.map(PortraitAlphaMask::source_size),
        portrait_scale_percent,
        control_surface,
    )?;
    if let (Some(mask), Some(target)) = (portrait_alpha_mask, regions.drag.first().copied()) {
        regions.drag[0] = alpha_bounding_logical_rect(mask, target)?;
    }
    let mut bounds: Option<(i64, i64, i64, i64)> = None;
    for (rectangles, outset) in [
        (regions.interactive.as_slice(), 4_i64),
        (regions.drag.as_slice(), 2_i64),
        (regions.neutral.as_slice(), 2_i64),
    ] {
        for rect in rectangles {
            let candidate = (
                i64::from(rect.x) - outset,
                i64::from(rect.y) - outset,
                i64::from(rect.x) + i64::from(rect.width) + outset,
                i64::from(rect.y) + i64::from(rect.height) + outset,
            );
            bounds = Some(match bounds {
                None => candidate,
                Some((left, top, right, bottom)) => (
                    left.min(candidate.0),
                    top.min(candidate.1),
                    right.max(candidate.2),
                    bottom.max(candidate.3),
                ),
            });
        }
    }
    let (mut left, mut top, mut right, mut bottom) =
        bounds.ok_or_else(|| "visible pet surface is empty".to_string())?;
    left = left.max(0);
    top = top.max(0);
    right = right.min(i64::from(contract.viewport.window_size[0]));
    bottom = bottom.min(i64::from(contract.viewport.window_size[1]));
    if left < 0 || top < 0 || right <= left || bottom <= top {
        return Err("visible pet surface bounds are invalid".to_string());
    }
    Ok([
        u32::try_from(left).map_err(|_| "visible pet surface x overflow".to_string())?,
        u32::try_from(top).map_err(|_| "visible pet surface y overflow".to_string())?,
        u32::try_from(right - left)
            .map_err(|_| "visible pet surface width overflow".to_string())?,
        u32::try_from(bottom - top)
            .map_err(|_| "visible pet surface height overflow".to_string())?,
    ])
}

/// Returns a dynamic native envelope that is stable for every allowed portrait scale.
///
/// The envelope still follows the current portrait alpha mask and control-surface layout, but it
/// is sized for the largest permitted portrait transform. Keeping this envelope unchanged while
/// the scale slider moves prevents the root WebView and its top-level window from having to
/// compensate one another across separate compositor messages.
pub fn logical_scale_stable_surface_bounds_with_control_surface(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&PortraitAlphaMask>,
) -> Result<[u32; 4], String> {
    if !(PORTRAIT_SCALE_MIN_PERCENT..=PORTRAIT_SCALE_MAX_PERCENT).contains(&portrait_scale_percent)
    {
        return Err("portrait appearance scale is out of range".to_string());
    }
    logical_visible_surface_bounds_with_control_surface(
        contract,
        state,
        PORTRAIT_SCALE_MAX_PERCENT,
        control_surface,
        portrait_alpha_mask,
    )
}

fn extreme_control_surface(
    contract: &LayoutContract,
    width: u32,
    bubble_height: u32,
    vertical_offset: i32,
    input_offset: u32,
    input_height: u32,
) -> Result<ControlSurfaceLayout, String> {
    let panel = &contract.control_panel;
    let x = i64::from(panel.center_x) - i64::from(width / 2);
    let reference_bubble_bottom = i64::from(panel.bubble_bottom) - i64::from(vertical_offset);
    let input_bottom = i64::from(panel.bubble_bottom)
        + i64::from(panel.input_gap)
        + i64::from(panel.input_base_height)
        + i64::from(input_offset)
        - i64::from(vertical_offset);
    let input_top = input_bottom - i64::from(input_height);
    let bubble_bottom = reference_bubble_bottom.min(input_top - i64::from(panel.input_gap));
    let bubble_top = bubble_bottom - i64::from(bubble_height);
    let to_u32 = |value: i64| {
        u32::try_from(value).map_err(|_| "stable control surface escapes viewport".to_string())
    };
    Ok(ControlSurfaceLayout {
        bubble_rect: [to_u32(x)?, to_u32(bubble_top)?, width, bubble_height],
        input_rect: [to_u32(x)?, to_u32(input_top)?, width, input_height],
        controls_rect: [
            to_u32(x + i64::from(width) - 40)?,
            to_u32(bubble_top + 10)?,
            30,
            30,
        ],
    })
}

/// Returns the Windows backing envelope that is stable for every allowed portrait scale and
/// every control-panel geometry setting. Precise window regions still expose only the current
/// visual pixels; the larger rectangle exists solely to keep HWND/WebView placement stationary.
pub fn logical_scale_and_control_stable_surface_bounds(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_scale_percent: u16,
    portrait_alpha_mask: Option<&PortraitAlphaMask>,
) -> Result<[u32; 4], String> {
    let mut bounds = logical_scale_stable_surface_bounds_with_control_surface(
        contract,
        state,
        portrait_scale_percent,
        None,
        portrait_alpha_mask,
    )?;
    let panel = &contract.control_panel;
    for width in [
        panel.control_panel_width.minimum,
        panel.control_panel_width.maximum,
    ] {
        for bubble_height in [
            panel.bubble_max_height.minimum,
            panel.bubble_max_height.maximum,
        ] {
            for vertical_offset in [
                panel.control_panel_vertical_offset.minimum,
                panel.control_panel_vertical_offset.maximum,
            ] {
                for input_offset in [
                    panel.input_bar_offset.minimum,
                    panel.input_bar_offset.maximum,
                ] {
                    for input_height in [panel.input_base_height, panel.input_max_height] {
                        let surface = extreme_control_surface(
                            contract,
                            width,
                            bubble_height,
                            vertical_offset,
                            input_offset,
                            input_height,
                        )?;
                        contract.validate_control_surface(state, &surface)?;
                        let candidate = logical_visible_surface_bounds_with_control_surface(
                            contract,
                            state,
                            PORTRAIT_SCALE_MAX_PERCENT,
                            Some(&surface),
                            portrait_alpha_mask,
                        )?;
                        bounds = union_surface_bounds(bounds, candidate);
                    }
                }
            }
        }
    }
    Ok(bounds)
}

pub fn union_surface_bounds(first: [u32; 4], second: [u32; 4]) -> [u32; 4] {
    let left = first[0].min(second[0]);
    let top = first[1].min(second[1]);
    let right = first[0]
        .saturating_add(first[2])
        .max(second[0].saturating_add(second[2]));
    let bottom = first[1]
        .saturating_add(first[3])
        .max(second[1].saturating_add(second[3]));
    [
        left,
        top,
        right.saturating_sub(left),
        bottom.saturating_sub(top),
    ]
}

fn alpha_bounding_logical_rect(
    mask: &PortraitAlphaMask,
    target: LogicalHitRect,
) -> Result<LogicalHitRect, String> {
    let expected_len = usize::try_from(u64::from(mask.width) * u64::from(mask.height))
        .map_err(|_| "portrait alpha mask dimensions overflow".to_string())?;
    if mask.width == 0 || mask.height == 0 || mask.alpha.len() != expected_len {
        return Err("portrait alpha mask is invalid".to_string());
    }
    let [source_left, source_top, source_width, source_height] = mask
        .visible_bounds()
        .ok_or_else(|| "portrait alpha mask has no visible pixels".to_string())?;
    let source_right = source_left
        .checked_add(source_width)
        .ok_or_else(|| "portrait alpha bounds overflow".to_string())?;
    let source_bottom = source_top
        .checked_add(source_height)
        .ok_or_else(|| "portrait alpha bounds overflow".to_string())?;
    let left = u64::from(source_left) * u64::from(target.width) / u64::from(mask.width);
    let top = u64::from(source_top) * u64::from(target.height) / u64::from(mask.height);
    let right = ((u64::from(source_right) * u64::from(target.width) + u64::from(mask.width) - 1)
        / u64::from(mask.width))
    .min(u64::from(target.width));
    let bottom = ((u64::from(source_bottom) * u64::from(target.height) + u64::from(mask.height)
        - 1)
        / u64::from(mask.height))
    .min(u64::from(target.height));
    Ok(LogicalHitRect::new(
        target
            .x
            .checked_add(i32::try_from(left).map_err(|_| "portrait alpha x overflow")?)
            .ok_or_else(|| "portrait alpha x overflow".to_string())?,
        target
            .y
            .checked_add(i32::try_from(top).map_err(|_| "portrait alpha y overflow")?)
            .ok_or_else(|| "portrait alpha y overflow".to_string())?,
        u32::try_from(right - left).map_err(|_| "portrait alpha width overflow".to_string())?,
        u32::try_from(bottom - top).map_err(|_| "portrait alpha height overflow".to_string())?,
    ))
}

fn constrained_portrait_rect(
    base: LogicalHitRect,
    scale_percent: u16,
    envelope: [u32; 2],
) -> Result<LogicalHitRect, String> {
    if !(PORTRAIT_SCALE_MIN_PERCENT..=PORTRAIT_SCALE_MAX_PERCENT).contains(&scale_percent) {
        return Err("portrait appearance scale is out of range".to_string());
    }
    let center_x = f64::from(base.x) + f64::from(base.width) / 2.0;
    let bottom = f64::from(base.y) + f64::from(base.height);
    let max_width = 2.0 * center_x.min(f64::from(envelope[0]) - center_x);
    let max_height = bottom;
    let requested = f64::from(scale_percent) / 100.0;
    let effective = requested
        .min(max_width / f64::from(base.width))
        .min(max_height / f64::from(base.height));
    if !effective.is_finite() || effective <= 0.0 {
        return Err("portrait appearance scale cannot fit the fixed envelope".to_string());
    }
    let width = (f64::from(base.width) * effective).ceil() as u32;
    let height = (f64::from(base.height) * effective).ceil() as u32;
    let x = (center_x - f64::from(width) / 2.0).floor() as i32;
    let y = (bottom - f64::from(height)).floor() as i32;
    LogicalHitRect::checked(x, y, width, height, envelope)
}

fn contained_portrait_rect(
    target: LogicalHitRect,
    source_size: [u32; 2],
) -> Result<LogicalHitRect, String> {
    let [source_width, source_height] = source_size;
    if source_width == 0 || source_height == 0 {
        return Err("portrait hit-test source dimensions must be non-zero".to_string());
    }
    let scale = (f64::from(target.width) / f64::from(source_width))
        .min(f64::from(target.height) / f64::from(source_height));
    if !scale.is_finite() || scale <= 0.0 {
        return Err("portrait hit-test scale must be positive and finite".to_string());
    }
    let width = ((f64::from(source_width) * scale).ceil() as u32).min(target.width);
    let height = ((f64::from(source_height) * scale).ceil() as u32).min(target.height);
    let x = target.x + i32::try_from((target.width - width) / 2).unwrap_or(0);
    let y = target.y + i32::try_from(target.height - height).unwrap_or(0);
    Ok(LogicalHitRect::new(x, y, width, height))
}

#[cfg(test)]
pub fn classify_logical_point(model: &LogicalHitRegions, point: [i32; 2]) -> HitKind {
    for (kind, regions) in [
        (HitKind::Interactive, model.interactive.as_slice()),
        (HitKind::Drag, model.drag.as_slice()),
        (HitKind::Neutral, model.neutral.as_slice()),
    ] {
        if regions.iter().any(|rect| rect.contains(point)) {
            return kind;
        }
    }
    HitKind::Transparent
}

pub fn classify_logical_point_with_alpha(
    model: &LogicalHitRegions,
    portrait_alpha_mask: Option<&PortraitAlphaMask>,
    point: [i32; 2],
) -> Result<HitKind, String> {
    if model.interactive.iter().any(|rect| rect.contains(point)) {
        return Ok(HitKind::Interactive);
    }
    if let Some(target) = model.drag.first().copied() {
        if target.contains(point) {
            let visible = match portrait_alpha_mask {
                None => true,
                Some(mask) => {
                    let expected_len =
                        usize::try_from(u64::from(mask.width) * u64::from(mask.height))
                            .map_err(|_| "portrait alpha mask dimensions overflow".to_string())?;
                    if mask.width == 0 || mask.height == 0 || mask.alpha.len() != expected_len {
                        return Err("portrait alpha mask is invalid".to_string());
                    }
                    let local_x = u32::try_from(point[0] - target.x)
                        .map_err(|_| "portrait alpha point x overflow".to_string())?;
                    let local_y = u32::try_from(point[1] - target.y)
                        .map_err(|_| "portrait alpha point y overflow".to_string())?;
                    let source_x = (u64::from(local_x) * u64::from(mask.width)
                        / u64::from(target.width))
                    .min(u64::from(mask.width - 1));
                    let source_y = (u64::from(local_y) * u64::from(mask.height)
                        / u64::from(target.height))
                    .min(u64::from(mask.height - 1));
                    let index = source_y * u64::from(mask.width) + source_x;
                    mask.alpha[index as usize] > 0
                }
            };
            if visible {
                return Ok(HitKind::Drag);
            }
        }
    }
    if model.drag.iter().skip(1).any(|rect| rect.contains(point)) {
        return Ok(HitKind::Drag);
    }
    if model.neutral.iter().any(|rect| rect.contains(point)) {
        return Ok(HitKind::Neutral);
    }
    Ok(HitKind::Transparent)
}

#[cfg(test)]
pub fn contains_visible_point(model: &LogicalHitRegions, point: [i32; 2]) -> bool {
    model
        .interactive
        .iter()
        .chain(&model.drag)
        .chain(&model.neutral)
        .any(|region| region.contains(point))
}

#[cfg(test)]
fn scale_rect(rect: LogicalHitRect, scale: f64) -> Result<PhysicalHitRect, String> {
    let left = (f64::from(rect.x) * scale).floor();
    let top = (f64::from(rect.y) * scale).floor();
    let right = ((f64::from(rect.x) + f64::from(rect.width)) * scale).ceil();
    let bottom = ((f64::from(rect.y) + f64::from(rect.height)) * scale).ceil();
    if ![left, top, right, bottom]
        .iter()
        .all(|value| value.is_finite())
        || left < f64::from(i32::MIN)
        || top < f64::from(i32::MIN)
        || right > f64::from(i32::MAX)
        || bottom > f64::from(i32::MAX)
        || right <= left
        || bottom <= top
    {
        return Err("scaled hit rectangle exceeds Win32 limits".to_string());
    }
    Ok(PhysicalHitRect {
        x: left as i32,
        y: top as i32,
        width: (right - left) as u32,
        height: (bottom - top) as u32,
        corner_radius: (f64::from(rect.corner_radius) * scale).ceil() as u32,
    })
}

#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
const NATIVE_ANTIALIAS_BLEED_LOGICAL_PX: f64 = 2.0;

#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
fn expand_rounded_clip_for_antialiasing(
    rect: PhysicalHitRect,
    scale: f64,
    envelope: [u32; 2],
) -> Result<PhysicalHitRect, String> {
    if rect.corner_radius == 0 {
        return Ok(rect);
    }
    if !scale.is_finite() || scale <= 0.0 {
        return Err("native antialias bleed scale must be positive and finite".to_string());
    }
    let bleed = (NATIVE_ANTIALIAS_BLEED_LOGICAL_PX * scale).ceil() as i64;
    let left = (i64::from(rect.x) - bleed).max(0);
    let top = (i64::from(rect.y) - bleed).max(0);
    let right = (rect.right() + bleed).min(i64::from(envelope[0]));
    let bottom = (rect.bottom() + bleed).min(i64::from(envelope[1]));
    if right <= left || bottom <= top {
        return Err("native rounded clip is empty".to_string());
    }
    Ok(PhysicalHitRect {
        x: i32::try_from(left).map_err(|_| "native rounded clip x overflow".to_string())?,
        y: i32::try_from(top).map_err(|_| "native rounded clip y overflow".to_string())?,
        width: u32::try_from(right - left)
            .map_err(|_| "native rounded clip width overflow".to_string())?,
        height: u32::try_from(bottom - top)
            .map_err(|_| "native rounded clip height overflow".to_string())?,
        corner_radius: rect.corner_radius.saturating_add(bleed as u32),
    })
}

#[cfg(test)]
pub fn scale_hit_regions(
    model: &LogicalHitRegions,
    scale: f64,
) -> Result<PhysicalHitRegions, String> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err("hit region scale must be positive and finite".to_string());
    }
    let scale_all = |regions: &[LogicalHitRect]| {
        regions
            .iter()
            .copied()
            .map(|rect| scale_rect(rect, scale))
            .collect::<Result<Vec<_>, _>>()
    };
    let interactive = scale_all(&model.interactive)?;
    let drag = scale_all(&model.drag)?;
    let neutral = scale_all(&model.neutral)?;
    let mut envelope = [0_u32, 0_u32];
    for rect in interactive.iter().chain(&drag).chain(&neutral) {
        envelope[0] = envelope[0].max(
            u32::try_from(rect.right())
                .map_err(|_| "physical hit-region envelope width overflow".to_string())?,
        );
        envelope[1] = envelope[1].max(
            u32::try_from(rect.bottom())
                .map_err(|_| "physical hit-region envelope height overflow".to_string())?,
        );
    }
    Ok(PhysicalHitRegions {
        state: model.state,
        scale,
        envelope,
        interactive,
        drag,
        neutral,
        portrait_alpha_mask: None,
        extra_native_rectangles: Vec::new(),
    })
}

pub fn scale_hit_regions_for_surface(
    model: &LogicalHitRegions,
    scale: f64,
    active_bounds: [u32; 4],
    portrait_anchor: [u32; 2],
) -> Result<PhysicalHitRegions, String> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err("hit region scale must be positive and finite".to_string());
    }
    let frame_left =
        ((f64::from(active_bounds[0]) - f64::from(portrait_anchor[0])) * scale).floor();
    let frame_top = ((f64::from(active_bounds[1]) - f64::from(portrait_anchor[1])) * scale).floor();
    let frame_right = ((f64::from(active_bounds[0].saturating_add(active_bounds[2]))
        - f64::from(portrait_anchor[0]))
        * scale)
        .ceil();
    let frame_bottom = ((f64::from(active_bounds[1].saturating_add(active_bounds[3]))
        - f64::from(portrait_anchor[1]))
        * scale)
        .ceil();
    let envelope_width = frame_right - frame_left;
    let envelope_height = frame_bottom - frame_top;
    if !envelope_width.is_finite()
        || !envelope_height.is_finite()
        || envelope_width <= 0.0
        || envelope_height <= 0.0
        || envelope_width > f64::from(u32::MAX)
        || envelope_height > f64::from(u32::MAX)
    {
        return Err("physical hit-region envelope is invalid".to_string());
    }
    let envelope = [envelope_width as u32, envelope_height as u32];
    let scale_one = |rect: LogicalHitRect| -> Result<PhysicalHitRect, String> {
        let left =
            ((f64::from(rect.x) - f64::from(portrait_anchor[0])) * scale).floor() - frame_left;
        let top = ((f64::from(rect.y) - f64::from(portrait_anchor[1])) * scale).floor() - frame_top;
        let right = ((f64::from(rect.x) + f64::from(rect.width) - f64::from(portrait_anchor[0]))
            * scale)
            .ceil()
            - frame_left;
        let bottom = ((f64::from(rect.y) + f64::from(rect.height) - f64::from(portrait_anchor[1]))
            * scale)
            .ceil()
            - frame_top;
        if ![left, top, right, bottom]
            .iter()
            .all(|value| value.is_finite())
            || left < 0.0
            || top < 0.0
            || right <= left
            || bottom <= top
        {
            return Err("surface-local hit rectangle is invalid".to_string());
        }
        Ok(PhysicalHitRect {
            x: left as i32,
            y: top as i32,
            width: (right - left) as u32,
            height: (bottom - top) as u32,
            corner_radius: (f64::from(rect.corner_radius) * scale).ceil() as u32,
        })
    };
    let scale_all = |regions: &[LogicalHitRect]| {
        regions
            .iter()
            .copied()
            .map(scale_one)
            .collect::<Result<Vec<_>, _>>()
    };
    Ok(PhysicalHitRegions {
        state: model.state,
        scale,
        envelope,
        interactive: scale_all(&model.interactive)?,
        drag: scale_all(&model.drag)?,
        neutral: scale_all(&model.neutral)?,
        portrait_alpha_mask: None,
        extra_native_rectangles: Vec::new(),
    })
}

#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
fn alpha_hit_rectangles(
    mask: &PortraitAlphaMask,
    target: PhysicalHitRect,
) -> Result<Vec<PhysicalHitRect>, String> {
    let expected_len = usize::try_from(u64::from(mask.width) * u64::from(mask.height))
        .map_err(|_| "portrait alpha mask dimensions overflow".to_string())?;
    if mask.width == 0
        || mask.height == 0
        || mask.alpha.len() != expected_len
        || target.width == 0
        || target.height == 0
    {
        return Err("portrait alpha mask is invalid".to_string());
    }

    let mut rectangles: Vec<PhysicalHitRect> = Vec::new();
    let mut previous: HashMap<(i32, u32), usize> = HashMap::new();
    for target_y in 0..target.height {
        let source_top = u64::from(target_y) * u64::from(mask.height) / u64::from(target.height);
        let source_bottom =
            ((u64::from(target_y + 1) * u64::from(mask.height) + u64::from(target.height) - 1)
                / u64::from(target.height))
            .max(source_top + 1)
            .min(u64::from(mask.height));
        let mut runs = Vec::new();
        let mut run_start = None;
        for target_x in 0..target.width {
            let source_left = u64::from(target_x) * u64::from(mask.width) / u64::from(target.width);
            let source_right =
                ((u64::from(target_x + 1) * u64::from(mask.width) + u64::from(target.width) - 1)
                    / u64::from(target.width))
                .max(source_left + 1)
                .min(u64::from(mask.width));
            let visible = (source_top..source_bottom).any(|source_y| {
                (source_left..source_right).any(|source_x| {
                    let index = source_y * u64::from(mask.width) + source_x;
                    mask.alpha[index as usize] > 0
                })
            });
            match (run_start, visible) {
                (None, true) => run_start = Some(target_x),
                (Some(start), false) => {
                    runs.push((start, target_x - start));
                    run_start = None;
                }
                _ => {}
            }
        }
        if let Some(start) = run_start {
            runs.push((start, target.width - start));
        }

        let row_y = target
            .y
            .checked_add(i32::try_from(target_y).map_err(|_| "portrait alpha row overflow")?)
            .ok_or_else(|| "portrait alpha row overflow".to_string())?;
        let mut current = HashMap::new();
        for (run_x, run_width) in runs {
            let x = target
                .x
                .checked_add(i32::try_from(run_x).map_err(|_| "portrait alpha run overflow")?)
                .ok_or_else(|| "portrait alpha run overflow".to_string())?;
            let key = (x, run_width);
            let index = if let Some(index) = previous.get(&key).copied() {
                rectangles[index].height = rectangles[index].height.saturating_add(1);
                index
            } else {
                rectangles.push(PhysicalHitRect {
                    x,
                    y: row_y,
                    width: run_width,
                    height: 1,
                    corner_radius: 0,
                });
                rectangles.len() - 1
            };
            current.insert(key, index);
        }
        previous = current;
    }
    Ok(rectangles)
}

#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
pub(crate) fn native_hit_rectangles(
    model: &PhysicalHitRegions,
    envelope: [u32; 2],
) -> Result<Vec<PhysicalHitRect>, String> {
    let mut rectangles = model.extra_native_rectangles.clone();
    rectangles.extend(model.interactive.iter().copied());
    if let Some(mask) = model.portrait_alpha_mask.as_ref() {
        let portrait = model
            .drag
            .first()
            .copied()
            .ok_or_else(|| "portrait hit region is unavailable".to_string())?;
        rectangles.extend(alpha_hit_rectangles(mask, portrait)?);
        rectangles.extend(model.drag.iter().skip(1).copied());
    } else {
        rectangles.extend(model.drag.iter().copied());
    }
    rectangles.extend(model.neutral.iter().copied());
    rectangles
        .into_iter()
        .map(|rect| expand_rounded_clip_for_antialiasing(rect, model.scale, envelope))
        .collect()
}

#[cfg(any(windows, target_os = "macos", target_os = "linux", test))]
pub(crate) fn translated_bridge_rectangles(
    previous: &PhysicalHitRegions,
    previous_envelope: [u32; 2],
    previous_origin: [i32; 2],
    next_origin: [i32; 2],
    next_envelope: [u32; 2],
) -> Result<Vec<PhysicalHitRect>, String> {
    let delta_x = i64::from(previous_origin[0]) - i64::from(next_origin[0]);
    let delta_y = i64::from(previous_origin[1]) - i64::from(next_origin[1]);
    let mut translated = Vec::new();
    for rect in native_hit_rectangles(previous, previous_envelope)? {
        let left = (i64::from(rect.x) + delta_x).max(0);
        let top = (i64::from(rect.y) + delta_y).max(0);
        let right = (rect.right() + delta_x).min(i64::from(next_envelope[0]));
        let bottom = (rect.bottom() + delta_y).min(i64::from(next_envelope[1]));
        if right <= left || bottom <= top {
            continue;
        }
        let unclipped = left == i64::from(rect.x) + delta_x
            && top == i64::from(rect.y) + delta_y
            && right == rect.right() + delta_x
            && bottom == rect.bottom() + delta_y;
        translated.push(PhysicalHitRect {
            x: i32::try_from(left).map_err(|_| "bridge hit region x overflow".to_string())?,
            y: i32::try_from(top).map_err(|_| "bridge hit region y overflow".to_string())?,
            width: u32::try_from(right - left)
                .map_err(|_| "bridge hit region width overflow".to_string())?,
            height: u32::try_from(bottom - top)
                .map_err(|_| "bridge hit region height overflow".to_string())?,
            corner_radius: if unclipped { rect.corner_radius } else { 0 },
        });
    }
    Ok(translated)
}

#[cfg(any(windows, test))]
fn normalize_plain_hit_rectangles(
    rectangles: &[PhysicalHitRect],
) -> Result<Vec<PhysicalHitRect>, String> {
    let mut y_edges = Vec::with_capacity(rectangles.len().saturating_mul(2));
    for rect in rectangles.iter().copied() {
        if rect.corner_radius != 0 || rect.width == 0 || rect.height == 0 {
            continue;
        }
        let bottom = i32::try_from(rect.bottom())
            .map_err(|_| "native hit region bottom edge overflow".to_string())?;
        y_edges.push(rect.y);
        y_edges.push(bottom);
    }
    y_edges.sort_unstable();
    y_edges.dedup();

    let mut normalized: Vec<PhysicalHitRect> = Vec::new();
    let mut previous: HashMap<(i32, i32), usize> = HashMap::new();
    for band in y_edges.windows(2) {
        let top = band[0];
        let bottom = band[1];
        if bottom <= top {
            continue;
        }
        let mut intervals = rectangles
            .iter()
            .copied()
            .filter(|rect| {
                rect.corner_radius == 0 && rect.y <= top && rect.bottom() >= i64::from(bottom)
            })
            .map(|rect| {
                let right = i32::try_from(rect.right())
                    .map_err(|_| "native hit region right edge overflow".to_string())?;
                Ok((rect.x, right))
            })
            .collect::<Result<Vec<_>, String>>()?;
        intervals.sort_unstable();
        let mut merged: Vec<(i32, i32)> = Vec::new();
        for (left, right) in intervals {
            if let Some(last) = merged.last_mut() {
                if left <= last.1 {
                    last.1 = last.1.max(right);
                    continue;
                }
            }
            merged.push((left, right));
        }

        let mut current = HashMap::new();
        for (left, right) in merged {
            let key = (left, right);
            let index = if let Some(index) = previous.get(&key).copied() {
                let band_height = u32::try_from(bottom - top)
                    .map_err(|_| "native hit region height overflow".to_string())?;
                normalized[index].height = normalized[index]
                    .height
                    .checked_add(band_height)
                    .ok_or_else(|| "native hit region height overflow".to_string())?;
                index
            } else {
                normalized.push(PhysicalHitRect {
                    x: left,
                    y: top,
                    width: u32::try_from(right - left)
                        .map_err(|_| "native hit region width overflow".to_string())?,
                    height: u32::try_from(bottom - top)
                        .map_err(|_| "native hit region height overflow".to_string())?,
                    corner_radius: 0,
                });
                normalized.len() - 1
            };
            current.insert(key, index);
        }
        previous = current;
    }
    Ok(normalized)
}

#[cfg(target_os = "linux")]
pub fn apply_native_hit_regions(
    window: &tauri::WebviewWindow,
    model: &PhysicalHitRegions,
) -> Result<(), String> {
    use gtk::prelude::WidgetExt;

    let rectangles = native_hit_rectangles(model, model.envelope)?;
    let region = cairo::Region::create();
    for rect in rectangles {
        let rows = if rect.corner_radius == 0 {
            vec![rect]
        } else {
            (0..rect.height)
                .filter_map(|offset_y| {
                    let y = rect.y.checked_add(i32::try_from(offset_y).ok()?)?;
                    let first = (0..rect.width).find(|offset_x| {
                        rect.contains([rect.x.saturating_add(*offset_x as i32), y])
                    })?;
                    let last = (first..rect.width).rfind(|offset_x| {
                        rect.contains([rect.x.saturating_add(*offset_x as i32), y])
                    })?;
                    Some(PhysicalHitRect {
                        x: rect.x.saturating_add(first as i32),
                        y,
                        width: last - first + 1,
                        height: 1,
                        corner_radius: 0,
                    })
                })
                .collect()
        };
        for row in rows {
            let width = i32::try_from(row.width)
                .map_err(|_| "native hit region width exceeds GTK limits".to_string())?;
            let height = i32::try_from(row.height)
                .map_err(|_| "native hit region height exceeds GTK limits".to_string())?;
            region
                .union_rectangle(&cairo::RectangleInt::new(row.x, row.y, width, height))
                .map_err(|error| format!("failed to combine GTK input region: {error}"))?;
        }
    }
    window
        .gtk_window()
        .map_err(|error| format!("failed to access GTK pet window: {error}"))?
        .input_shape_combine_region(Some(&region));
    Ok(())
}

#[cfg(target_os = "macos")]
#[derive(Clone)]
struct MacHitRouterSnapshot {
    window: tauri::WebviewWindow,
    rectangles: Vec<PhysicalHitRect>,
    envelope: [u32; 2],
}

#[cfg(any(target_os = "macos", test))]
fn mac_hit_router_contains(rectangles: &[PhysicalHitRect], point: [i32; 2]) -> bool {
    rectangles.iter().copied().any(|rect| rect.contains(point))
}

#[cfg(target_os = "macos")]
fn mac_hit_router_slot() -> &'static std::sync::Arc<std::sync::Mutex<Option<MacHitRouterSnapshot>>>
{
    static SLOT: std::sync::OnceLock<
        std::sync::Arc<std::sync::Mutex<Option<MacHitRouterSnapshot>>>,
    > = std::sync::OnceLock::new();
    SLOT.get_or_init(|| std::sync::Arc::new(std::sync::Mutex::new(None)))
}

#[cfg(target_os = "macos")]
fn ensure_mac_hit_router() -> Result<(), String> {
    static STARTED: std::sync::OnceLock<Result<(), String>> = std::sync::OnceLock::new();
    STARTED
        .get_or_init(|| {
            let slot = mac_hit_router_slot().clone();
            std::thread::Builder::new()
                .name("pet-macos-hit-router".to_string())
                .spawn(move || {
                    let drag_locked =
                        std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
                    loop {
                        let snapshot = slot.lock().ok().and_then(|guard| guard.clone());
                        if let Some(snapshot) = snapshot {
                            let routed_window = snapshot.window.clone();
                            let drag_locked = drag_locked.clone();
                            let _ = snapshot.window.run_on_main_thread(move || {
                                use objc2_app_kit::{NSEvent, NSWindow};
                                use std::sync::atomic::Ordering;

                                let Ok(raw_window) = routed_window.ns_window() else {
                                    return;
                                };
                                let ns_window = unsafe { &*raw_window.cast::<NSWindow>() };
                                let point = ns_window.mouseLocationOutsideOfEventStream();
                                let backing_scale = ns_window.backingScaleFactor() as f64;
                                let x = (point.x * backing_scale).floor() as i32;
                                let y = i64::from(snapshot.envelope[1])
                                    - (point.y * backing_scale).ceil() as i64;
                                let point =
                                    [x, y.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32];
                                let pressed = NSEvent::pressedMouseButtons() & 1 != 0;
                                let hit = mac_hit_router_contains(&snapshot.rectangles, point);
                                if !pressed {
                                    drag_locked.store(false, Ordering::Release);
                                } else if hit {
                                    drag_locked.store(true, Ordering::Release);
                                }
                                ns_window.setIgnoresMouseEvents(
                                    !(hit || drag_locked.load(Ordering::Acquire)),
                                );
                            });
                        }
                        std::thread::sleep(std::time::Duration::from_millis(8));
                    }
                })
                .map(|_| ())
                .map_err(|error| format!("failed to start macOS hit router: {error}"))
        })
        .clone()
}

#[cfg(target_os = "macos")]
fn ensure_mac_event_monitors(window: &tauri::WebviewWindow) -> Result<(), String> {
    use std::sync::atomic::{AtomicBool, Ordering};

    static SCHEDULED: AtomicBool = AtomicBool::new(false);
    static WAKE: AtomicBool = AtomicBool::new(false);
    if SCHEDULED.swap(true, Ordering::AcqRel) {
        return Ok(());
    }
    window
        .run_on_main_thread(move || {
            use block2::RcBlock;
            use objc2_app_kit::{NSEvent, NSEventMask};

            let mask = NSEventMask::MouseMoved
                | NSEventMask::LeftMouseDown
                | NSEventMask::LeftMouseUp
                | NSEventMask::LeftMouseDragged
                | NSEventMask::RightMouseDown
                | NSEventMask::RightMouseUp;
            let global = RcBlock::new(|_| WAKE.store(true, Ordering::Release));
            if let Some(token) =
                NSEvent::addGlobalMonitorForEventsMatchingMask_handler(mask, &global)
            {
                std::mem::forget(token);
            }
            let local = RcBlock::new(|event: std::ptr::NonNull<NSEvent>| {
                WAKE.store(true, Ordering::Release);
                event.as_ptr()
            });
            if let Some(token) =
                unsafe { NSEvent::addLocalMonitorForEventsMatchingMask_handler(mask, &local) }
            {
                std::mem::forget(token);
            }
        })
        .map_err(|error| format!("failed to install macOS event monitors: {error}"))
}

#[cfg(target_os = "macos")]
pub fn apply_native_hit_regions(
    window: &tauri::WebviewWindow,
    model: &PhysicalHitRegions,
) -> Result<(), String> {
    let rectangles = native_hit_rectangles(model, model.envelope)?;
    *mac_hit_router_slot()
        .lock()
        .map_err(|_| "macOS hit router state is unavailable".to_string())? =
        Some(MacHitRouterSnapshot {
            window: window.clone(),
            rectangles,
            envelope: model.envelope,
        });
    ensure_mac_event_monitors(window)?;
    ensure_mac_hit_router()
}

#[cfg(windows)]
const PET_BORDERLESS_SUBCLASS_ID: usize = 0x5341_4b42;

#[cfg(windows)]
unsafe extern "system" fn pet_window_borderless_proc(
    hwnd: windows::Win32::Foundation::HWND,
    message: u32,
    wparam: windows::Win32::Foundation::WPARAM,
    lparam: windows::Win32::Foundation::LPARAM,
    _subclass_id: usize,
    _reference_data: usize,
) -> windows::Win32::Foundation::LRESULT {
    use windows::Win32::Foundation::LRESULT;
    use windows::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass};
    use windows::Win32::UI::WindowsAndMessaging::{
        WM_NCACTIVATE, WM_NCCALCSIZE, WM_NCDESTROY, WM_NCPAINT,
    };

    match message {
        WM_NCCALCSIZE | WM_NCPAINT => return LRESULT(0),
        WM_NCACTIVATE => return LRESULT(1),
        _ => {}
    }

    let result = DefSubclassProc(hwnd, message, wparam, lparam);
    if message == WM_NCDESTROY {
        let _ = RemoveWindowSubclass(
            hwnd,
            Some(pet_window_borderless_proc),
            PET_BORDERLESS_SUBCLASS_ID,
        );
    }
    result
}

#[cfg(windows)]
fn install_native_borderless_subclass(
    hwnd: windows::Win32::Foundation::HWND,
) -> Result<(), String> {
    use windows::Win32::UI::Shell::SetWindowSubclass;

    unsafe {
        if !SetWindowSubclass(
            hwnd,
            Some(pet_window_borderless_proc),
            PET_BORDERLESS_SUBCLASS_ID,
            0,
        )
        .as_bool()
        {
            return Err("failed to install native pet borderless subclass".to_string());
        }
    }
    Ok(())
}

#[cfg(windows)]
pub fn apply_native_hit_regions(
    window: &tauri::WebviewWindow,
    model: &PhysicalHitRegions,
) -> Result<(), String> {
    use windows::Win32::Foundation::RECT;
    use windows::Win32::Graphics::Gdi::{
        CombineRgn, CreateRectRgn, CreateRoundRectRgn, DeleteObject, ExtCreateRegion,
        InvalidateRect, SetWindowRgn, ERROR, HGDIOBJ, RDH_RECTANGLES, RGNDATA, RGNDATAHEADER,
        RGN_OR,
    };

    let overall_started = std::time::Instant::now();
    crate::interaction_latency::stage("setwindowrgn-apply-start");
    let hwnd_started = std::time::Instant::now();
    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    crate::interaction_latency::stage_elapsed("setwindowrgn-hwnd-return", hwnd_started);
    let rectangles_started = std::time::Instant::now();
    let native_rectangles = native_hit_rectangles(model, model.envelope)?;
    crate::interaction_latency::stage_elapsed("setwindowrgn-rectangles-return", rectangles_started);
    let subclass_started = std::time::Instant::now();
    install_native_borderless_subclass(hwnd)?;
    crate::interaction_latency::stage_elapsed("setwindowrgn-subclass-return", subclass_started);
    let plain_regions = native_rectangles
        .iter()
        .copied()
        .filter(|rect| rect.corner_radius == 0)
        .collect::<Vec<_>>();
    let mut plain = normalize_plain_hit_rectangles(&plain_regions)?
        .into_iter()
        .map(|rect| {
            Ok(RECT {
                left: rect.x,
                top: rect.y,
                right: i32::try_from(rect.right())
                    .map_err(|_| "native hit region right edge overflow".to_string())?,
                bottom: i32::try_from(rect.bottom())
                    .map_err(|_| "native hit region bottom edge overflow".to_string())?,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    plain.sort_by_key(|rect| (rect.top, rect.left, rect.bottom, rect.right));
    let combined = if plain.is_empty() {
        unsafe { CreateRectRgn(0, 0, 0, 0) }
    } else {
        let header_size = std::mem::size_of::<RGNDATAHEADER>();
        let rectangle_bytes = plain
            .len()
            .checked_mul(std::mem::size_of::<RECT>())
            .ok_or_else(|| "native region data size overflow".to_string())?;
        let total_bytes = header_size
            .checked_add(rectangle_bytes)
            .ok_or_else(|| "native region data size overflow".to_string())?;
        let words = total_bytes.div_ceil(std::mem::size_of::<usize>());
        let mut storage = vec![0usize; words];
        let bounds = RECT {
            left: plain.iter().map(|rect| rect.left).min().unwrap_or(0),
            top: plain.iter().map(|rect| rect.top).min().unwrap_or(0),
            right: plain.iter().map(|rect| rect.right).max().unwrap_or(0),
            bottom: plain.iter().map(|rect| rect.bottom).max().unwrap_or(0),
        };
        unsafe {
            let bytes = storage.as_mut_ptr().cast::<u8>();
            bytes.cast::<RGNDATAHEADER>().write(RGNDATAHEADER {
                dwSize: u32::try_from(header_size).unwrap_or(u32::MAX),
                iType: RDH_RECTANGLES,
                nCount: u32::try_from(plain.len())
                    .map_err(|_| "native region rectangle count overflow".to_string())?,
                nRgnSize: u32::try_from(rectangle_bytes)
                    .map_err(|_| "native region data size overflow".to_string())?,
                rcBound: bounds,
            });
            std::ptr::copy_nonoverlapping(
                plain.as_ptr(),
                bytes.add(header_size).cast::<RECT>(),
                plain.len(),
            );
            ExtCreateRegion(
                None,
                u32::try_from(total_bytes)
                    .map_err(|_| "native region data size overflow".to_string())?,
                bytes.cast::<RGNDATA>(),
            )
        }
    };
    if combined.is_invalid() {
        return Err("failed to create native hit region".to_string());
    }
    for rect in native_rectangles
        .iter()
        .filter(|rect| rect.corner_radius > 0)
    {
        let right = i32::try_from(rect.right())
            .map_err(|_| "native hit region right edge overflow".to_string())?;
        let bottom = i32::try_from(rect.bottom())
            .map_err(|_| "native hit region bottom edge overflow".to_string())?;
        let diameter = i32::try_from(rect.corner_radius.saturating_mul(2))
            .map_err(|_| "native rounded clip radius overflow".to_string())?;
        let part = unsafe { CreateRoundRectRgn(rect.x, rect.y, right, bottom, diameter, diameter) };
        if part.is_invalid() {
            unsafe {
                let _ = DeleteObject(HGDIOBJ::from(combined));
            }
            return Err("failed to create native hit rectangle".to_string());
        }
        let result = unsafe { CombineRgn(Some(combined), Some(combined), Some(part), RGN_OR) };
        unsafe {
            let _ = DeleteObject(HGDIOBJ::from(part));
        }
        if result.0 == ERROR {
            unsafe {
                let _ = DeleteObject(HGDIOBJ::from(combined));
            }
            return Err("failed to combine native hit rectangles".to_string());
        }
    }
    crate::interaction_latency::stage_elapsed("setwindowrgn-region-built", overall_started);
    let set_region_started = std::time::Instant::now();
    if unsafe { SetWindowRgn(hwnd, Some(combined), false) } == 0 {
        unsafe {
            let _ = DeleteObject(HGDIOBJ::from(combined));
        }
        return Err("failed to apply native pet hit region".to_string());
    }
    crate::interaction_latency::stage_elapsed("setwindowrgn-call-return", set_region_started);
    // SetWindowRgn(redraw=true) sends synchronous non-client and paint work through the same HWND
    // that hosts WebView2, which can stall every pointer response for a full frame burst. The
    // shape is already committed synchronously; invalidation schedules repaint without blocking
    // the interaction command on painting the whole stable envelope.
    let invalidate_started = std::time::Instant::now();
    if !unsafe { InvalidateRect(Some(hwnd), None, false) }.as_bool() {
        return Err("failed to invalidate native pet hit region".to_string());
    }
    crate::interaction_latency::stage_elapsed("setwindowrgn-invalidate-return", invalidate_started);
    crate::interaction_latency::stage_elapsed("setwindowrgn-apply-return", overall_started);
    Ok(())
}

#[cfg(windows)]
pub fn relax_native_hit_regions(window: &tauri::WebviewWindow) -> Result<(), String> {
    use windows::Win32::Graphics::Gdi::{InvalidateRect, SetWindowRgn};

    let overall_started = std::time::Instant::now();
    crate::interaction_latency::stage("setwindowrgn-relax-start");
    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    install_native_borderless_subclass(hwnd)?;
    let set_region_started = std::time::Instant::now();
    if unsafe { SetWindowRgn(hwnd, None, false) } == 0 {
        return Err("failed to relax native pet hit regions".to_string());
    }
    crate::interaction_latency::stage_elapsed("setwindowrgn-relax-call-return", set_region_started);
    let invalidate_started = std::time::Instant::now();
    if !unsafe { InvalidateRect(Some(hwnd), None, false) }.as_bool() {
        return Err("failed to invalidate relaxed pet hit region".to_string());
    }
    crate::interaction_latency::stage_elapsed(
        "setwindowrgn-relax-invalidate-return",
        invalidate_started,
    );
    crate::interaction_latency::stage_elapsed("setwindowrgn-relax-return", overall_started);
    Ok(())
}

#[cfg(windows)]
pub fn start_native_drag(window: &tauri::WebviewWindow) -> Result<NativeDragCompletion, String> {
    use std::{thread, time::Duration};

    use windows::Win32::Foundation::{POINT, RECT};
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, ReleaseCapture, VK_LBUTTON,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        GetCursorPos, GetWindowRect, SetWindowPos, SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOZORDER,
    };

    let overall_started = std::time::Instant::now();
    crate::interaction_latency::stage("native-drag-enter");
    let hwnd_started = std::time::Instant::now();
    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    crate::interaction_latency::stage_elapsed("native-drag-hwnd-return", hwnd_started);
    let mut initial_cursor = POINT::default();
    let mut initial_window = RECT::default();
    unsafe {
        let cursor_started = std::time::Instant::now();
        GetCursorPos(&mut initial_cursor)
            .map_err(|error| format!("failed to read native drag cursor: {error}"))?;
        crate::interaction_latency::stage_elapsed(
            "native-drag-initial-cursor-return",
            cursor_started,
        );
        let bounds_started = std::time::Instant::now();
        GetWindowRect(hwnd, &mut initial_window)
            .map_err(|error| format!("failed to read native drag window bounds: {error}"))?;
        crate::interaction_latency::stage_elapsed("native-drag-window-rect-return", bounds_started);
        let release_started = std::time::Instant::now();
        ReleaseCapture().map_err(|error| format!("failed to release pointer capture: {error}"))?;
        crate::interaction_latency::stage_elapsed(
            "native-drag-release-capture-return",
            release_started,
        );
    }

    // HTCAPTION enters Windows' system move loop, which applies top-edge snap
    // and work-area policies even though our geometry layer accepts negative
    // coordinates. Follow the physical cursor directly so every monitor edge
    // has the same unrestricted behavior.
    let loop_started = std::time::Instant::now();
    crate::interaction_latency::stage("native-drag-loop-enter");
    let mut first_iteration = true;
    let mut first_cursor_delta = true;
    let mut first_set_window_pos = true;
    let mut last_window_origin = [initial_window.left, initial_window.top];
    while unsafe { GetAsyncKeyState(i32::from(VK_LBUTTON.0)) } < 0 {
        if first_iteration {
            first_iteration = false;
            crate::interaction_latency::stage_elapsed(
                "native-drag-first-button-poll",
                loop_started,
            );
        }
        let mut cursor = POINT::default();
        unsafe {
            GetCursorPos(&mut cursor)
                .map_err(|error| format!("failed to read native drag cursor: {error}"))?;
        }
        let [x, y] = dragged_window_origin(
            [initial_window.left, initial_window.top],
            [initial_cursor.x, initial_cursor.y],
            [cursor.x, cursor.y],
        )?;
        if first_cursor_delta && (cursor.x != initial_cursor.x || cursor.y != initial_cursor.y) {
            first_cursor_delta = false;
            crate::interaction_latency::stage_elapsed(
                "native-drag-first-cursor-delta",
                overall_started,
            );
        }
        if [x, y] != last_window_origin {
            let set_window_pos_started = std::time::Instant::now();
            unsafe {
                SetWindowPos(
                    hwnd,
                    None,
                    x,
                    y,
                    0,
                    0,
                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
                )
                .map_err(|error| format!("failed to move native pet window: {error}"))?;
            }
            last_window_origin = [x, y];
            let set_window_pos_elapsed = set_window_pos_started.elapsed();
            if first_set_window_pos {
                first_set_window_pos = false;
                crate::interaction_latency::stage_elapsed(
                    "native-drag-first-setwindowpos-return",
                    set_window_pos_started,
                );
                crate::interaction_latency::stage_elapsed(
                    "native-drag-first-setwindowpos-from-enter",
                    overall_started,
                );
            } else if set_window_pos_elapsed > Duration::from_millis(16) {
                crate::interaction_latency::stage_elapsed(
                    "native-drag-slow-setwindowpos-return",
                    set_window_pos_started,
                );
            }
        }
        thread::sleep(Duration::from_millis(8));
    }
    crate::interaction_latency::stage_elapsed("native-drag-loop-return", loop_started);
    crate::interaction_latency::stage_elapsed("native-drag-return", overall_started);
    Ok(native_drag_completion())
}

#[cfg(not(windows))]
pub fn start_native_drag(window: &tauri::WebviewWindow) -> Result<NativeDragCompletion, String> {
    window.start_dragging().map_err(|error| error.to_string())?;
    Ok(native_drag_completion())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::window_geometry::{LayoutContract, PresentationState};

    fn contract() -> LayoutContract {
        serde_json::from_str(include_str!("../../frontend/pet/layout-contract.json"))
            .expect("layout contract must parse")
    }

    #[test]
    fn product_layout_has_deterministic_ordered_hit_regions() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        assert_eq!(model.interactive.len(), 2);
        assert_eq!(model.drag.len(), 2);
        assert!(model.neutral.is_empty());
        assert_eq!(model.drag[0], LogicalHitRect::new(150, 328, 600, 656));
        assert_eq!(
            model.drag[1],
            LogicalHitRect::new(130, 680, 640, 128).with_corner_radius(BUBBLE_CORNER_RADIUS)
        );
    }

    #[test]
    fn native_drag_completion_matches_platform_event_timing() {
        #[cfg(windows)]
        assert_eq!(
            native_drag_completion(),
            NativeDragCompletion::SynchronousMoveLoop
        );
        #[cfg(not(windows))]
        assert_eq!(
            native_drag_completion(),
            NativeDragCompletion::DeferredWindowMoved
        );
    }

    #[test]
    fn mac_hit_router_uses_only_the_current_precise_rectangles() {
        let rectangles = [PhysicalHitRect {
            x: 10,
            y: 20,
            width: 30,
            height: 40,
            corner_radius: 0,
        }];
        assert!(mac_hit_router_contains(&rectangles, [10, 20]));
        assert!(!mac_hit_router_contains(&rectangles, [0, 0]));
    }

    #[test]
    fn custom_drag_origin_preserves_negative_and_cross_monitor_coordinates() {
        assert_eq!(
            dragged_window_origin([1500, 120], [1800, 400], [1650, 50]).unwrap(),
            [1350, -230]
        );
        assert_eq!(
            dragged_window_origin([-2400, -200], [-2000, 100], [-800, 900]).unwrap(),
            [-1200, 600]
        );
    }

    #[test]
    fn custom_drag_origin_rejects_coordinate_overflow() {
        assert!(dragged_window_origin([i32::MAX, 0], [0, 0], [1, 0]).is_err());
        assert!(dragged_window_origin([0, i32::MIN], [0, 1], [0, 0]).is_err());
    }

    #[test]
    fn interactive_regions_take_priority_over_drag_and_edges_are_half_open() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        assert_eq!(
            classify_logical_point(&model, [742, 696]),
            HitKind::Interactive
        );
        assert_eq!(classify_logical_point(&model, [426, 436]), HitKind::Drag);
        assert_eq!(classify_logical_point(&model, [142, 716]), HitKind::Drag);
        assert_eq!(classify_logical_point(&model, [749, 983]), HitKind::Drag);
        assert_eq!(
            classify_logical_point(&model, [750, 984]),
            HitKind::Transparent
        );
        assert_eq!(classify_logical_point(&model, [0, 0]), HitKind::Transparent);

        assert_eq!(
            classify_logical_point(&model, [242, 836]),
            HitKind::Interactive
        );
    }

    #[test]
    fn product_menu_surface_covers_every_visible_region_and_rejects_transparent_space() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        for point in [[342, 516], [142, 716], [242, 836], [742, 696]] {
            assert!(contains_visible_point(&model, point), "{point:?}");
        }
        assert!(!contains_visible_point(&model, [0, 0]));
        assert!(!contains_visible_point(&model, [899, 995]));
    }

    #[test]
    fn portrait_hit_region_matches_contain_bounds_and_excludes_outer_letterbox() {
        let contract = contract();
        let tall = logical_hit_regions_with_portrait_size(
            &contract,
            PresentationState::Product,
            Some([300, 600]),
        )
        .unwrap();
        assert_eq!(tall.drag[0], LogicalHitRect::new(286, 328, 328, 656));
        assert_eq!(
            classify_logical_point(&tall, [162, 416]),
            HitKind::Transparent
        );
        assert_eq!(classify_logical_point(&tall, [442, 416]), HitKind::Drag);

        let wide = logical_hit_regions_with_portrait_size(
            &contract,
            PresentationState::Product,
            Some([1200, 300]),
        )
        .unwrap();
        assert_eq!(wide.drag[0], LogicalHitRect::new(150, 834, 600, 150));
        assert_eq!(
            classify_logical_point(&wide, [442, 416]),
            HitKind::Transparent
        );
        assert!(logical_hit_regions_with_portrait_size(
            &contract,
            PresentationState::Product,
            Some([0, 300]),
        )
        .is_err());
    }

    #[test]
    fn portrait_appearance_scale_preserves_anchor_and_fixed_window_envelope() {
        let contract = contract();
        let base = logical_hit_regions_with_portrait_transform(
            &contract,
            PresentationState::Product,
            Some([400, 800]),
            100,
        )
        .unwrap();
        let small = logical_hit_regions_with_portrait_transform(
            &contract,
            PresentationState::Product,
            Some([400, 800]),
            50,
        )
        .unwrap();
        let requested_large = logical_hit_regions_with_portrait_transform(
            &contract,
            PresentationState::Product,
            Some([400, 800]),
            150,
        )
        .unwrap();
        let [base_rect, small_rect, large_rect] =
            [&base.drag[0], &small.drag[0], &requested_large.drag[0]];
        for rect in [base_rect, small_rect, large_rect] {
            assert_eq!(rect.x + i32::try_from(rect.width / 2).unwrap(), 450);
            assert_eq!(rect.y + i32::try_from(rect.height).unwrap(), 984);
            assert!(rect.x >= 0 && rect.y >= 0);
            assert!(rect.x as u32 + rect.width <= contract.viewport.window_size[0]);
            assert!(rect.y as u32 + rect.height <= contract.viewport.window_size[1]);
        }
        assert!(small_rect.height < base_rect.height);
        assert_eq!(large_rect.height, base_rect.height * 3 / 2);
    }

    #[test]
    fn visible_surface_bounds_follow_current_scale_without_using_png_shape() {
        let contract = contract();
        assert_eq!(
            logical_visible_surface_bounds(&contract, PresentationState::Product, 50).unwrap(),
            [126, 654, 648, 332]
        );
        assert_eq!(
            logical_visible_surface_bounds(&contract, PresentationState::Product, 100).unwrap(),
            [126, 326, 648, 660]
        );
        assert_eq!(
            logical_visible_surface_bounds(&contract, PresentationState::Product, 150).unwrap(),
            [0, 0, 900, 986]
        );

        let tall = logical_hit_regions_with_portrait_transform(
            &contract,
            PresentationState::Product,
            Some([400, 800]),
            100,
        )
        .unwrap();
        let wide = logical_hit_regions_with_portrait_transform(
            &contract,
            PresentationState::Product,
            Some([1200, 300]),
            100,
        )
        .unwrap();
        assert_ne!(tall.drag[0], wide.drag[0]);
        assert_eq!(
            logical_visible_surface_bounds(&contract, PresentationState::Product, 100).unwrap(),
            [126, 326, 648, 660]
        );
    }

    #[test]
    fn scale_stable_surface_bounds_do_not_move_the_root_webview_during_preview() {
        let contract = contract();
        let expected = [0, 0, 900, 986];
        for scale_percent in PORTRAIT_SCALE_MIN_PERCENT..=PORTRAIT_SCALE_MAX_PERCENT {
            assert_eq!(
                logical_scale_stable_surface_bounds_with_control_surface(
                    &contract,
                    PresentationState::Product,
                    scale_percent,
                    None,
                    None,
                )
                .unwrap(),
                expected
            );
        }
        assert!(logical_scale_stable_surface_bounds_with_control_surface(
            &contract,
            PresentationState::Product,
            PORTRAIT_SCALE_MIN_PERCENT - 1,
            None,
            None,
        )
        .is_err());
        assert!(logical_scale_stable_surface_bounds_with_control_surface(
            &contract,
            PresentationState::Product,
            PORTRAIT_SCALE_MAX_PERCENT + 1,
            None,
            None,
        )
        .is_err());
    }

    #[test]
    fn layout_and_scale_stable_bounds_contain_every_legal_control_surface_extreme() {
        let contract = contract();
        let mask = PortraitAlphaMask::new(3, 3, vec![0, 0, 0, 0, 255, 0, 0, 0, 0]);
        let expected = logical_scale_and_control_stable_surface_bounds(
            &contract,
            PresentationState::Product,
            PORTRAIT_SCALE_MIN_PERCENT,
            Some(&mask),
        )
        .unwrap();
        for scale_percent in PORTRAIT_SCALE_MIN_PERCENT..=PORTRAIT_SCALE_MAX_PERCENT {
            assert_eq!(
                logical_scale_and_control_stable_surface_bounds(
                    &contract,
                    PresentationState::Product,
                    scale_percent,
                    Some(&mask),
                )
                .unwrap(),
                expected
            );
        }

        let panel = &contract.control_panel;
        for width in [
            panel.control_panel_width.minimum,
            panel.control_panel_width.maximum,
        ] {
            for bubble_height in [
                panel.bubble_max_height.minimum,
                panel.bubble_max_height.maximum,
            ] {
                for vertical_offset in [
                    panel.control_panel_vertical_offset.minimum,
                    panel.control_panel_vertical_offset.maximum,
                ] {
                    for input_offset in [
                        panel.input_bar_offset.minimum,
                        panel.input_bar_offset.maximum,
                    ] {
                        for input_height in [panel.input_base_height, panel.input_max_height] {
                            let surface = extreme_control_surface(
                                &contract,
                                width,
                                bubble_height,
                                vertical_offset,
                                input_offset,
                                input_height,
                            )
                            .unwrap();
                            let current = logical_visible_surface_bounds_with_control_surface(
                                &contract,
                                PresentationState::Product,
                                PORTRAIT_SCALE_MAX_PERCENT,
                                Some(&surface),
                                Some(&mask),
                            )
                            .unwrap();
                            assert_eq!(union_surface_bounds(expected, current), expected);
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn portrait_alpha_mask_excludes_png_canvas_transparency_and_internal_holes() {
        let centered = PortraitAlphaMask::new(
            4,
            4,
            vec![
                0, 0, 0, 0, //
                0, 255, 255, 0, //
                0, 255, 255, 0, //
                0, 0, 0, 0,
            ],
        );
        assert_eq!(
            alpha_hit_rectangles(
                &centered,
                PhysicalHitRect {
                    x: 10,
                    y: 20,
                    width: 40,
                    height: 40,
                    corner_radius: 0,
                }
            )
            .unwrap(),
            vec![PhysicalHitRect {
                x: 20,
                y: 30,
                width: 20,
                height: 20,
                corner_radius: 0,
            }]
        );

        let hole = PortraitAlphaMask::new(3, 3, vec![255, 255, 255, 255, 0, 255, 255, 255, 255]);
        let rectangles = alpha_hit_rectangles(
            &hole,
            PhysicalHitRect {
                x: 0,
                y: 0,
                width: 3,
                height: 3,
                corner_radius: 0,
            },
        )
        .unwrap();
        assert!(!rectangles.iter().any(|rect| rect.x == 1 && rect.y == 1));

        let transparent = PortraitAlphaMask::new(2, 2, vec![0; 4]);
        assert!(alpha_hit_rectangles(
            &transparent,
            PhysicalHitRect {
                x: 0,
                y: 0,
                width: 2,
                height: 2,
                corner_radius: 0,
            }
        )
        .unwrap()
        .is_empty());
    }

    #[test]
    fn alpha_bounds_shrink_the_native_envelope_without_changing_control_outsets() {
        let mask = PortraitAlphaMask::new(
            4,
            4,
            vec![
                0, 0, 0, 0, //
                0, 255, 255, 0, //
                0, 255, 255, 0, //
                0, 0, 0, 0,
            ],
        );
        assert_eq!(mask.visible_bounds(), Some([1, 1, 2, 2]));
        assert_eq!(
            logical_visible_surface_bounds_with_control_surface(
                &contract(),
                PresentationState::Product,
                100,
                None,
                Some(&mask),
            )
            .unwrap(),
            [126, 532, 648, 342]
        );
    }

    #[test]
    fn exact_drag_authorization_rejects_alpha_holes_and_accepts_visible_pixels() {
        let mask = PortraitAlphaMask::new(
            4,
            4,
            vec![
                0, 0, 0, 0, //
                0, 255, 255, 0, //
                0, 255, 255, 0, //
                0, 0, 0, 0,
            ],
        );
        let model = logical_hit_regions_with_portrait_transform(
            &contract(),
            PresentationState::Product,
            Some(mask.source_size()),
            100,
        )
        .unwrap();
        assert_eq!(
            classify_logical_point_with_alpha(&model, Some(&mask), [450, 684]).unwrap(),
            HitKind::Drag
        );
        assert_eq!(
            classify_logical_point_with_alpha(&model, Some(&mask), [200, 434]).unwrap(),
            HitKind::Transparent
        );
        assert_eq!(
            classify_logical_point_with_alpha(&model, Some(&mask), [142, 716]).unwrap(),
            HitKind::Drag
        );
    }

    #[test]
    fn complex_alpha_masks_keep_holes_beyond_the_old_rectangle_limit() {
        let width = 130;
        let height = 130;
        let alpha = (0..width * height)
            .map(|index| {
                let x = index % width;
                let y = index / width;
                if (x + y) % 2 == 0 {
                    255
                } else {
                    0
                }
            })
            .collect();
        let mask = PortraitAlphaMask::new(width, height, alpha);
        let rectangles = alpha_hit_rectangles(
            &mask,
            PhysicalHitRect {
                x: 0,
                y: 0,
                width,
                height,
                corner_radius: 0,
            },
        )
        .unwrap();
        assert!(rectangles.len() > 4_096);
        assert!(!rectangles.iter().copied().any(|rect| rect.contains([1, 0])));
        assert!(rectangles.iter().copied().any(|rect| rect.contains([0, 0])));
    }

    #[test]
    fn overlapping_transition_rectangles_are_normalized_without_filling_holes() {
        let rectangles = vec![
            PhysicalHitRect {
                x: 0,
                y: 0,
                width: 4,
                height: 2,
                corner_radius: 0,
            },
            PhysicalHitRect {
                x: 2,
                y: 1,
                width: 4,
                height: 2,
                corner_radius: 0,
            },
            PhysicalHitRect {
                x: 8,
                y: 1,
                width: 1,
                height: 1,
                corner_radius: 0,
            },
        ];
        let normalized = normalize_plain_hit_rectangles(&rectangles).unwrap();
        for y in 0..3 {
            for x in 0..10 {
                let before = rectangles.iter().copied().any(|rect| rect.contains([x, y]));
                let after = normalized.iter().copied().any(|rect| rect.contains([x, y]));
                assert_eq!(after, before, "coverage changed at ({x}, {y})");
            }
        }
        for (index, rect) in normalized.iter().copied().enumerate() {
            assert!(normalized.iter().skip(index + 1).copied().all(|other| {
                rect.right() <= i64::from(other.x)
                    || other.right() <= i64::from(rect.x)
                    || rect.bottom() <= i64::from(other.y)
                    || other.bottom() <= i64::from(rect.y)
            }));
        }
    }

    #[test]
    fn bridge_regions_preserve_global_coverage_and_clip_to_the_next_surface() {
        let previous = PhysicalHitRegions {
            state: PresentationState::Product,
            scale: 1.0,
            envelope: [100, 100],
            interactive: vec![PhysicalHitRect {
                x: 10,
                y: 20,
                width: 30,
                height: 40,
                corner_radius: 6,
            }],
            drag: Vec::new(),
            neutral: Vec::new(),
            portrait_alpha_mask: None,
            extra_native_rectangles: Vec::new(),
        };
        let translated =
            translated_bridge_rectangles(&previous, [100, 100], [200, 300], [190, 330], [35, 50])
                .unwrap();
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0].x, 18);
        assert_eq!(translated[0].y, 0);
        assert_eq!(translated[0].width, 17);
        assert_eq!(translated[0].height, 32);
        assert_eq!(translated[0].corner_radius, 0);
    }

    #[test]
    fn hit_regions_scale_outward_at_all_target_dpis() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        for (scale, expected) in [(1.0, 900), (1.25, 1125), (1.5, 1350)] {
            let physical = scale_hit_regions(&model, scale).unwrap();
            let right = physical
                .interactive
                .iter()
                .chain(&physical.drag)
                .map(PhysicalHitRect::right)
                .max()
                .unwrap();
            assert!(right <= expected);
            assert_eq!(physical.scale, scale);
        }
    }

    #[test]
    fn native_hit_snapshot_uses_committed_envelope_instead_of_stale_window_readback() {
        let contract = contract();
        let model = logical_hit_regions(&contract, PresentationState::Product).unwrap();
        let physical = scale_hit_regions_for_surface(
            &model,
            1.0,
            [0, 0, 900, 986],
            contract.viewport.portrait_anchor,
        )
        .unwrap();
        assert_eq!(physical.envelope, [900, 986]);
        assert!(native_hit_rectangles(&physical, [816, 680]).is_err());
        assert!(native_hit_rectangles(&physical, physical.envelope).is_ok());
    }

    #[test]
    fn invalid_scale_and_extreme_rectangles_fail_closed() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        assert!(scale_hit_regions(&model, 0.0).is_err());
        assert!(scale_hit_regions(&model, f64::INFINITY).is_err());
        assert!(LogicalHitRect::checked(i32::MAX, 0, 2, 2, [816, 680]).is_err());
    }

    #[test]
    fn rounded_native_clip_has_only_a_two_pixel_antialias_guard() {
        let exact = PhysicalHitRect {
            x: 40,
            y: 450,
            width: 440,
            height: 164,
            corner_radius: 26,
        };
        let guarded = expand_rounded_clip_for_antialiasing(exact, 1.0, [816, 680]).unwrap();
        assert_eq!(guarded.x, 38);
        assert_eq!(guarded.y, 448);
        assert_eq!(guarded.width, 444);
        assert_eq!(guarded.height, 168);
        assert_eq!(guarded.corner_radius, 28);

        let portrait = PhysicalHitRect {
            x: 360,
            y: 332,
            width: 240,
            height: 336,
            corner_radius: 0,
        };
        assert_eq!(
            expand_rounded_clip_for_antialiasing(portrait, 1.0, [816, 680]).unwrap(),
            portrait
        );
    }

    #[test]
    fn physical_hit_testing_preserves_rounded_corners_and_half_open_edges() {
        let rounded = PhysicalHitRect {
            x: 10,
            y: 20,
            width: 40,
            height: 40,
            corner_radius: 20,
        };
        assert!(!rounded.contains([10, 20]));
        assert!(rounded.contains([10, 40]));
        assert!(rounded.contains([30, 40]));
        assert!(!rounded.contains([50, 40]));
        assert!(!rounded.contains([30, 60]));
    }

    #[test]
    fn platform_region_failure_retains_the_previous_precise_region() {
        assert_eq!(
            fallback_for_native_region_result(true),
            HitRegionFallback::NotNeeded
        );
        assert_eq!(
            fallback_for_native_region_result(false),
            HitRegionFallback::RetainPrevious
        );
    }
}
