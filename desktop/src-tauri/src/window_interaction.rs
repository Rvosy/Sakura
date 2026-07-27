#[cfg(any(windows, test))]
use std::collections::HashMap;

use serde::Serialize;

use crate::{
    character_presentation::PortraitAlphaMask,
    window_geometry::{LayoutContract, PresentationState},
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
    #[cfg(any(windows, test))]
    fn right(&self) -> i64 {
        i64::from(self.x) + i64::from(self.width)
    }

    #[cfg(any(windows, test))]
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
    #[serde(skip)]
    pub portrait_alpha_mask: Option<PortraitAlphaMask>,
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

const BUBBLE_CORNER_RADIUS: u32 = 20;
const INPUT_CORNER_RADIUS: u32 = 26;
const CONTROLS_CORNER_RADIUS: u32 = 15;

#[cfg(test)]
pub fn logical_hit_regions(
    contract: &LayoutContract,
    state: PresentationState,
) -> Result<LogicalHitRegions, String> {
    logical_hit_regions_with_portrait_size(contract, state, None)
}

pub fn logical_hit_regions_with_portrait_size(
    contract: &LayoutContract,
    state: PresentationState,
    portrait_source_size: Option<[u32; 2]>,
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
    let portrait_rect = translate_rect(
        layout.portrait_rect,
        offset,
        contract.viewport.window_size,
        0,
    )?;
    let portrait_rect = match portrait_source_size {
        Some(source_size) => contained_portrait_rect(portrait_rect, source_size)?,
        None => portrait_rect,
    };
    let mut drag = vec![portrait_rect];
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

pub fn contains_visible_point(model: &LogicalHitRegions, point: [i32; 2]) -> bool {
    model
        .interactive
        .iter()
        .chain(&model.drag)
        .chain(&model.neutral)
        .any(|region| region.contains(point))
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

#[cfg(any(windows, test))]
const NATIVE_ANTIALIAS_BLEED_LOGICAL_PX: f64 = 2.0;

#[cfg(any(windows, test))]
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
        portrait_alpha_mask: None,
    })
}

#[cfg(any(windows, test))]
const MAX_ALPHA_REGION_RECTS: usize = 4_096;

#[cfg(any(windows, test))]
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
        if rectangles.len() > MAX_ALPHA_REGION_RECTS {
            return Ok(alpha_bounding_rectangle(mask, target).into_iter().collect());
        }
    }
    Ok(rectangles)
}

#[cfg(any(windows, test))]
fn alpha_bounding_rectangle(
    mask: &PortraitAlphaMask,
    target: PhysicalHitRect,
) -> Option<PhysicalHitRect> {
    let mut min_x = mask.width;
    let mut min_y = mask.height;
    let mut max_x = 0;
    let mut max_y = 0;
    let mut visible = false;
    for (index, alpha) in mask.alpha.iter().copied().enumerate() {
        if alpha == 0 {
            continue;
        }
        let source_x = u32::try_from(index % mask.width as usize).ok()?;
        let source_y = u32::try_from(index / mask.width as usize).ok()?;
        min_x = min_x.min(source_x);
        min_y = min_y.min(source_y);
        max_x = max_x.max(source_x);
        max_y = max_y.max(source_y);
        visible = true;
    }
    if !visible {
        return None;
    }
    let left = u64::from(min_x) * u64::from(target.width) / u64::from(mask.width);
    let top = u64::from(min_y) * u64::from(target.height) / u64::from(mask.height);
    let right = ((u64::from(max_x + 1) * u64::from(target.width) + u64::from(mask.width) - 1)
        / u64::from(mask.width))
    .min(u64::from(target.width));
    let bottom = ((u64::from(max_y + 1) * u64::from(target.height) + u64::from(mask.height) - 1)
        / u64::from(mask.height))
    .min(u64::from(target.height));
    Some(PhysicalHitRect {
        x: target.x.checked_add(i32::try_from(left).ok()?)?,
        y: target.y.checked_add(i32::try_from(top).ok()?)?,
        width: u32::try_from(right - left).ok()?,
        height: u32::try_from(bottom - top).ok()?,
        corner_radius: 0,
    })
}

#[cfg(windows)]
fn native_hit_rectangles(
    model: &PhysicalHitRegions,
    envelope: [u32; 2],
) -> Result<Vec<PhysicalHitRect>, String> {
    let mut rectangles = model.interactive.clone();
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
    use windows::Win32::Graphics::Gdi::{
        CombineRgn, CreateRectRgn, CreateRoundRectRgn, DeleteObject, SetWindowRgn, ERROR, HGDIOBJ,
        RGN_OR,
    };

    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    let inner_size = window
        .inner_size()
        .map_err(|error| format!("failed to read native pet window size: {error}"))?;
    let envelope = [inner_size.width, inner_size.height];
    let native_rectangles = native_hit_rectangles(model, envelope)?;
    install_native_borderless_subclass(hwnd)?;
    let combined = unsafe { CreateRectRgn(0, 0, 0, 0) };
    if combined.is_invalid() {
        return Err("failed to create native hit region".to_string());
    }
    for rect in &native_rectangles {
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
    install_native_borderless_subclass(hwnd)?;
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
        assert_eq!(model.drag[0], LogicalHitRect::new(108, 12, 600, 656));
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
            classify_logical_point(&model, [700, 380]),
            HitKind::Interactive
        );
        assert_eq!(classify_logical_point(&model, [384, 120]), HitKind::Drag);
        assert_eq!(classify_logical_point(&model, [707, 667]), HitKind::Drag);
        assert_eq!(
            classify_logical_point(&model, [708, 668]),
            HitKind::Transparent
        );
        assert_eq!(classify_logical_point(&model, [0, 0]), HitKind::Transparent);

        assert_eq!(
            classify_logical_point(&model, [200, 520]),
            HitKind::Interactive
        );
    }

    #[test]
    fn product_menu_surface_covers_every_visible_region_and_rejects_transparent_space() {
        let model = logical_hit_regions(&contract(), PresentationState::Product).unwrap();
        for point in [[300, 200], [100, 400], [200, 520], [700, 380]] {
            assert!(contains_visible_point(&model, point), "{point:?}");
        }
        assert!(!contains_visible_point(&model, [0, 0]));
        assert!(!contains_visible_point(&model, [815, 679]));
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
        assert_eq!(tall.drag[0], LogicalHitRect::new(244, 12, 328, 656));
        assert_eq!(
            classify_logical_point(&tall, [120, 100]),
            HitKind::Transparent
        );
        assert_eq!(classify_logical_point(&tall, [400, 100]), HitKind::Drag);

        let wide = logical_hit_regions_with_portrait_size(
            &contract,
            PresentationState::Product,
            Some([1200, 300]),
        )
        .unwrap();
        assert_eq!(wide.drag[0], LogicalHitRect::new(108, 518, 600, 150));
        assert_eq!(
            classify_logical_point(&wide, [400, 100]),
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
    fn portrait_alpha_mask_excludes_png_canvas_transparency_and_internal_holes() {
        let centered = PortraitAlphaMask {
            width: 4,
            height: 4,
            alpha: vec![
                0, 0, 0, 0, //
                0, 255, 255, 0, //
                0, 255, 255, 0, //
                0, 0, 0, 0,
            ],
        };
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

        let hole = PortraitAlphaMask {
            width: 3,
            height: 3,
            alpha: vec![255, 255, 255, 255, 0, 255, 255, 255, 255],
        };
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

        let transparent = PortraitAlphaMask {
            width: 2,
            height: 2,
            alpha: vec![0; 4],
        };
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
