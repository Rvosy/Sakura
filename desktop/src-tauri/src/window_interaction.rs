use serde::Serialize;

use crate::window_geometry::{LayoutContract, PresentationState};

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

#[cfg(test)]
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

    #[cfg(test)]
    fn contains(self, point: [i32; 2]) -> bool {
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
    fn right(&self) -> i64 {
        i64::from(self.x) + i64::from(self.width)
    }

    fn bottom(&self) -> i64 {
        i64::from(self.y) + i64::from(self.height)
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalHitRegions {
    pub state: PresentationState,
    pub scale: f64,
    pub interactive: Vec<PhysicalHitRect>,
    pub drag: Vec<PhysicalHitRect>,
    pub neutral: Vec<PhysicalHitRect>,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HitRegionFallback {
    NotNeeded,
    RestoreFullWindow,
}

#[cfg(test)]
pub fn fallback_for_native_region_result(applied: bool) -> HitRegionFallback {
    if applied {
        HitRegionFallback::NotNeeded
    } else {
        HitRegionFallback::RestoreFullWindow
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

const BUBBLE_CORNER_RADIUS: u32 = 26;
const INPUT_CORNER_RADIUS: u32 = 18;
const CONTROLS_CORNER_RADIUS: u32 = 15;

pub fn logical_hit_regions(
    contract: &LayoutContract,
    state: PresentationState,
) -> Result<LogicalHitRegions, String> {
    contract.validate()?;
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
    let mut interactive = Vec::with_capacity(2);
    if let Some(rect) = layout.input_rect {
        interactive.push(translate_rect(
            rect,
            offset,
            contract.viewport.window_size,
            INPUT_CORNER_RADIUS,
        )?);
    }
    interactive.push(translate_rect(
        layout.controls_rect,
        offset,
        contract.viewport.window_size,
        CONTROLS_CORNER_RADIUS,
    )?);
    let mut drag = vec![translate_rect(
        layout.portrait_rect,
        offset,
        contract.viewport.window_size,
        0,
    )?];
    if let Some(rect) = layout.bubble_rect {
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

const NATIVE_ANTIALIAS_BLEED_LOGICAL_PX: f64 = 2.0;

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
    Ok(PhysicalHitRegions {
        state: model.state,
        scale,
        interactive: scale_all(&model.interactive)?,
        drag: scale_all(&model.drag)?,
        neutral: scale_all(&model.neutral)?,
    })
}

#[cfg(windows)]
pub fn apply_native_hit_regions(
    window: &tauri::WebviewWindow,
    model: &PhysicalHitRegions,
) -> Result<(), String> {
    use windows::Win32::Graphics::Gdi::SetWindowRgn;
    use windows::Win32::Graphics::Gdi::{
        CombineRgn, CreateRectRgn, CreateRoundRectRgn, DeleteObject, ERROR, HGDIOBJ, RGN_OR,
    };

    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    let inner_size = window
        .inner_size()
        .map_err(|error| format!("failed to read native pet window size: {error}"))?;
    let envelope = [inner_size.width, inner_size.height];
    let combined = unsafe { CreateRectRgn(0, 0, 0, 0) };
    if combined.is_invalid() {
        return Err("failed to create native hit region".to_string());
    }
    for rect in model
        .interactive
        .iter()
        .chain(&model.drag)
        .chain(&model.neutral)
    {
        let rect = expand_rounded_clip_for_antialiasing(*rect, model.scale, envelope)?;
        let right = i32::try_from(rect.right())
            .map_err(|_| "native hit region right edge overflow".to_string())?;
        let bottom = i32::try_from(rect.bottom())
            .map_err(|_| "native hit region bottom edge overflow".to_string())?;
        let part = if rect.corner_radius == 0 {
            unsafe { CreateRectRgn(rect.x, rect.y, right, bottom) }
        } else {
            let diameter = i32::try_from(rect.corner_radius.saturating_mul(2))
                .map_err(|_| "native rounded clip radius overflow".to_string())?;
            unsafe { CreateRoundRectRgn(rect.x, rect.y, right, bottom, diameter, diameter) }
        };
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
    if unsafe { SetWindowRgn(hwnd, Some(combined), true) } == 0 {
        unsafe {
            let _ = DeleteObject(HGDIOBJ::from(combined));
        }
        return Err("failed to apply native pet hit region".to_string());
    }
    Ok(())
}

#[cfg(windows)]
pub fn restore_full_native_hit_region(window: &tauri::WebviewWindow) -> Result<(), String> {
    use windows::Win32::Graphics::Gdi::SetWindowRgn;

    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    if unsafe { SetWindowRgn(hwnd, None, true) } == 0 {
        Err("failed to restore full native pet hit region".to_string())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
pub fn start_native_drag(window: &tauri::WebviewWindow) -> Result<NativeDragCompletion, String> {
    use windows::Win32::Foundation::{LPARAM, POINT, WPARAM};
    use windows::Win32::UI::Input::KeyboardAndMouse::ReleaseCapture;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetCursorPos, SendMessageW, HTCAPTION, WM_NCLBUTTONDOWN,
    };

    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    let mut cursor = POINT::default();
    unsafe {
        GetCursorPos(&mut cursor)
            .map_err(|error| format!("failed to read native drag cursor: {error}"))?;
        ReleaseCapture().map_err(|error| format!("failed to release pointer capture: {error}"))?;
        let x = u32::from(cursor.x as u16);
        let y = u32::from(cursor.y as u16);
        let packed = isize::try_from(x | (y << 16))
            .map_err(|_| "native drag cursor coordinate overflow".to_string())?;
        let _ = SendMessageW(
            hwnd,
            WM_NCLBUTTONDOWN,
            Some(WPARAM(HTCAPTION as usize)),
            Some(LPARAM(packed)),
        );
    }
    Ok(native_drag_completion())
}

#[cfg(not(windows))]
pub fn apply_native_hit_regions(
    window: &tauri::WebviewWindow,
    model: &PhysicalHitRegions,
) -> Result<(), String> {
    // POSIX routing is performed by WebView pointer-events and the shared
    // model. Keep this compatibility entry point interactive for callers that
    // still use the pre-backend helper.
    let _ = model;
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())
}

#[cfg(not(windows))]
pub fn restore_full_native_hit_region(window: &tauri::WebviewWindow) -> Result<(), String> {
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())
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
        assert_eq!(model.drag[0], LogicalHitRect::new(384, 88, 416, 580));
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
    fn interactive_regions_take_priority_over_drag_and_edges_are_half_open() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        assert_eq!(
            classify_logical_point(&model, [400, 400]),
            HitKind::Interactive
        );
        assert_eq!(classify_logical_point(&model, [384, 120]), HitKind::Drag);
        assert_eq!(classify_logical_point(&model, [799, 667]), HitKind::Drag);
        assert_eq!(
            classify_logical_point(&model, [600, 668]),
            HitKind::Transparent
        );
        assert_eq!(classify_logical_point(&model, [0, 0]), HitKind::Transparent);

        assert_eq!(
            classify_logical_point(&model, [200, 390]),
            HitKind::Interactive
        );
    }

    #[test]
    fn hit_regions_scale_outward_at_all_target_dpis() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        for (scale, expected) in [(1.0, 816), (1.25, 1020), (1.5, 1224)] {
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
    fn platform_region_failure_requires_full_window_interaction_recovery() {
        assert_eq!(
            fallback_for_native_region_result(true),
            HitRegionFallback::NotNeeded
        );
        assert_eq!(
            fallback_for_native_region_result(false),
            HitRegionFallback::RestoreFullWindow
        );
    }
}
