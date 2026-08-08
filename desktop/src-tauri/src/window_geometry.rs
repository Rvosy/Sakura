use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum PresentationState {
    Product,
}

impl PresentationState {
    pub(crate) fn key(self) -> &'static str {
        match self {
            Self::Product => "product",
        }
    }

    #[cfg(test)]
    fn all() -> [Self; 1] {
        [Self::Product]
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LayoutContract {
    pub schema_version: u32,
    pub viewport: ViewportLayout,
    pub control_panel: ControlPanelLayout,
    pub states: BTreeMap<String, StateLayout>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RangeU32 {
    pub default: u32,
    pub minimum: u32,
    pub maximum: u32,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RangeI32 {
    pub default: i32,
    pub minimum: i32,
    pub maximum: i32,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ControlPanelLayout {
    pub center_x: u32,
    pub bubble_bottom: u32,
    pub input_gap: u32,
    pub bubble_min_height: u32,
    pub input_base_height: u32,
    pub input_max_height: u32,
    pub input_max_rows: u32,
    pub control_panel_width: RangeU32,
    pub bubble_max_height: RangeU32,
    pub control_panel_vertical_offset: RangeI32,
    pub input_bar_offset: RangeU32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ControlSurfaceLayout {
    pub bubble_rect: [u32; 4],
    pub input_rect: [u32; 4],
    pub controls_rect: [u32; 4],
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ViewportLayout {
    pub window_size: [u32; 2],
    pub portrait_anchor: [u32; 2],
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StateLayout {
    pub window_size: [u32; 2],
    pub portrait_rect: [u32; 4],
    pub bubble_rect: Option<[u32; 4]>,
    pub input_rect: Option<[u32; 4]>,
    pub controls_rect: [u32; 4],
    pub portrait_anchor: [u32; 2],
}

impl LayoutContract {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 3 {
            return Err(format!(
                "unsupported layout contract version: {}",
                self.schema_version
            ));
        }
        let [viewport_width, viewport_height] = self.viewport.window_size;
        let [viewport_anchor_x, viewport_anchor_y] = self.viewport.portrait_anchor;
        if viewport_width == 0
            || viewport_height == 0
            || viewport_width > 1200
            || viewport_height > 1200
            || viewport_anchor_x > viewport_width
            || viewport_anchor_y > viewport_height
        {
            return Err("invalid native viewport envelope".to_string());
        }
        let panel = &self.control_panel;
        for (name, range) in [
            ("controlPanelWidth", panel.control_panel_width),
            ("bubbleMaxHeight", panel.bubble_max_height),
            ("inputBarOffset", panel.input_bar_offset),
        ] {
            if range.minimum > range.default || range.default > range.maximum {
                return Err(format!("invalid control panel range: {name}"));
            }
        }
        let vertical = panel.control_panel_vertical_offset;
        if vertical.minimum > vertical.default
            || vertical.default > vertical.maximum
            || panel.bubble_min_height == 0
            || panel.bubble_min_height > panel.bubble_max_height.minimum
            || panel.input_base_height == 0
            || panel.input_base_height > panel.input_max_height
            || !(1..=8).contains(&panel.input_max_rows)
        {
            return Err("invalid adaptive control panel contract".to_string());
        }
        for state in PresentationState::all_values() {
            let layout = self
                .states
                .get(state.key())
                .ok_or_else(|| format!("missing layout state: {}", state.key()))?;
            let [width, height] = layout.window_size;
            if width == 0 || height == 0 || width > 1200 || height > 1200 {
                return Err(format!("unsafe native window size for {}", state.key()));
            }
            let [x, y, portrait_width, portrait_height] = layout.portrait_rect;
            if x.saturating_add(portrait_width) > width
                || y.saturating_add(portrait_height) > height
                || layout.portrait_anchor
                    != [
                        x.saturating_add(portrait_width / 2),
                        y.saturating_add(portrait_height),
                    ]
            {
                return Err(format!("portrait anchor mismatch for {}", state.key()));
            }
            for (name, rect) in [
                ("bubbleRect", layout.bubble_rect),
                ("inputRect", layout.input_rect),
                ("controlsRect", Some(layout.controls_rect)),
            ] {
                if let Some([rect_x, rect_y, rect_width, rect_height]) = rect {
                    if rect_width == 0
                        || rect_height == 0
                        || rect_x.saturating_add(rect_width) > width
                        || rect_y.saturating_add(rect_height) > height
                    {
                        return Err(format!(
                            "{}.{} escapes native window bounds",
                            state.key(),
                            name
                        ));
                    }
                }
            }
            let offset_x = viewport_anchor_x
                .checked_sub(layout.portrait_anchor[0])
                .ok_or_else(|| format!("{} expands right of viewport anchor", state.key()))?;
            let offset_y = viewport_anchor_y
                .checked_sub(layout.portrait_anchor[1])
                .ok_or_else(|| format!("{} expands below viewport anchor", state.key()))?;
            if offset_x.saturating_add(width) > viewport_width
                || offset_y.saturating_add(height) > viewport_height
            {
                return Err(format!(
                    "{} active layout escapes viewport envelope",
                    state.key()
                ));
            }
        }
        Ok(())
    }

    pub fn validate_control_surface(
        &self,
        state: PresentationState,
        surface: &ControlSurfaceLayout,
    ) -> Result<(), String> {
        self.validate()?;
        let layout = self
            .states
            .get(state.key())
            .ok_or_else(|| format!("missing layout state: {}", state.key()))?;
        let [window_width, window_height] = layout.window_size;
        for (name, [x, y, width, height]) in [
            ("bubbleRect", surface.bubble_rect),
            ("inputRect", surface.input_rect),
            ("controlsRect", surface.controls_rect),
        ] {
            if width == 0
                || height == 0
                || x.saturating_add(width) > window_width
                || y.saturating_add(height) > window_height
            {
                return Err(format!("CONTROL_SURFACE_INVALID:{name}"));
            }
        }
        let [bubble_x, bubble_y, bubble_width, bubble_height] = surface.bubble_rect;
        let [input_x, input_y, input_width, input_height] = surface.input_rect;
        let panel = &self.control_panel;
        if bubble_x != input_x
            || bubble_width != input_width
            || bubble_width < panel.control_panel_width.minimum
            || bubble_width > panel.control_panel_width.maximum
            || bubble_x.saturating_add(bubble_width / 2) != panel.center_x
            || bubble_height < panel.bubble_min_height
            || bubble_height > panel.bubble_max_height.maximum
            || input_height < panel.input_base_height
            || input_height > panel.input_max_height
            || bubble_y
                .saturating_add(bubble_height)
                .saturating_add(panel.input_gap)
                > input_y
        {
            return Err("CONTROL_SURFACE_INVALID:geometry".to_string());
        }
        let expected_controls = [bubble_x + bubble_width - 40, bubble_y + 10, 30, 30];
        if surface.controls_rect != expected_controls {
            return Err("CONTROL_SURFACE_INVALID:controlsRect".to_string());
        }
        Ok(())
    }

    #[cfg(test)]
    fn layout(&self, state: PresentationState) -> Result<&StateLayout, String> {
        self.states
            .get(state.key())
            .ok_or_else(|| format!("missing layout state: {}", state.key()))
    }

    fn all_values() -> [PresentationState; 1] {
        [PresentationState::Product]
    }
}

impl PresentationState {
    fn all_values() -> [Self; 1] {
        LayoutContract::all_values()
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalPoint {
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

impl PhysicalRect {
    fn right(self) -> i64 {
        i64::from(self.x) + i64::from(self.width)
    }

    fn bottom(self) -> i64 {
        i64::from(self.y) + i64::from(self.height)
    }

    fn contains(self, point: PhysicalPoint) -> bool {
        i64::from(point.x) >= i64::from(self.x)
            && i64::from(point.x) < self.right()
            && i64::from(point.y) >= i64::from(self.y)
            && i64::from(point.y) < self.bottom()
    }
}

#[derive(Clone, Debug)]
pub struct MonitorDescriptor {
    pub name: Option<String>,
    pub work_area: PhysicalRect,
    pub scale_factor: f64,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalPlacement {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LayoutApplication {
    pub applied: bool,
    pub revision: u64,
    pub state: PresentationState,
    pub contract_version: u32,
    pub content_scale: f64,
    pub scale_factor: f64,
    pub physical_placement: PhysicalPlacement,
    pub active_bounds: [u32; 4],
    pub physical_local_anchor: [u32; 2],
    pub portrait_anchor: PhysicalPoint,
    pub work_area: PhysicalRect,
    pub monitor_name: Option<String>,
    pub backend_mode: &'static str,
    pub degraded_reason: Option<&'static str>,
}

impl LayoutApplication {
    pub fn rejected(revision: u64, state: PresentationState, contract_version: u32) -> Self {
        Self {
            applied: false,
            revision,
            state,
            contract_version,
            content_scale: 1.0,
            scale_factor: 1.0,
            physical_placement: PhysicalPlacement {
                x: 0,
                y: 0,
                width: 0,
                height: 0,
            },
            active_bounds: [0, 0, 0, 0],
            physical_local_anchor: [0, 0],
            portrait_anchor: PhysicalPoint { x: 0, y: 0 },
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 0,
                height: 0,
            },
            monitor_name: None,
            backend_mode: platform_backend_mode().0,
            degraded_reason: platform_backend_mode().1,
        }
    }
}

#[derive(Default)]
pub struct LayoutRevisionGuard {
    latest: u64,
}

impl LayoutRevisionGuard {
    pub fn accept(&mut self, revision: u64) -> bool {
        if revision <= self.latest {
            false
        } else {
            self.latest = revision;
            true
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
struct ScaledLayout {
    size: [u32; 2],
    anchor: [u32; 2],
}

#[derive(Clone, Copy)]
struct AnchorEnvelope {
    left: i64,
    right: i64,
    top: i64,
    bottom: i64,
}

pub fn select_target_monitor(
    monitors: &[MonitorDescriptor],
    anchor: PhysicalPoint,
) -> Option<usize> {
    if let Some(index) = monitors
        .iter()
        .position(|monitor| monitor.work_area.contains(anchor))
    {
        return Some(index);
    }

    monitors
        .iter()
        .enumerate()
        .min_by_key(|(_, monitor)| distance_squared(monitor.work_area, anchor))
        .map(|(index, _)| index)
}

fn distance_squared(rect: PhysicalRect, point: PhysicalPoint) -> i128 {
    let point_x = i64::from(point.x);
    let point_y = i64::from(point.y);
    let dx = if point_x < i64::from(rect.x) {
        i64::from(rect.x) - point_x
    } else if point_x >= rect.right() {
        point_x - (rect.right() - 1)
    } else {
        0
    };
    let dy = if point_y < i64::from(rect.y) {
        i64::from(rect.y) - point_y
    } else if point_y >= rect.bottom() {
        point_y - (rect.bottom() - 1)
    } else {
        0
    };
    i128::from(dx) * i128::from(dx) + i128::from(dy) * i128::from(dy)
}

pub fn apply_window_layout(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<PhysicalPoint>,
    visible_surface_bounds: [u32; 4],
) -> Result<LayoutApplication, String> {
    contract.validate()?;
    validate_visible_surface_bounds(contract, visible_surface_bounds)?;
    if !monitor.scale_factor.is_finite() || monitor.scale_factor <= 0.0 {
        return Err("monitor scale factor must be positive and finite".to_string());
    }
    if monitor.work_area.width == 0 || monitor.work_area.height == 0 {
        return Err("monitor work area must be non-empty".to_string());
    }

    let (content_scale, envelope) =
        fit_contract_to_work_area(contract, monitor, visible_surface_bounds)?;
    let anchor = resolve_anchor(monitor.work_area, envelope, existing_anchor)?;
    let scale = monitor.scale_factor * content_scale;
    let [surface_x, surface_y, surface_width, surface_height] = visible_surface_bounds;
    let surface_right = surface_x.saturating_add(surface_width);
    let surface_bottom = surface_y.saturating_add(surface_height);
    let [anchor_x, anchor_y] = contract.viewport.portrait_anchor;
    let relative_left = (f64::from(surface_x) - f64::from(anchor_x)) * scale;
    let relative_top = (f64::from(surface_y) - f64::from(anchor_y)) * scale;
    let relative_right = (f64::from(surface_right) - f64::from(anchor_x)) * scale;
    let relative_bottom = (f64::from(surface_bottom) - f64::from(anchor_y)) * scale;
    let left = relative_left.floor() as i64;
    let top = relative_top.floor() as i64;
    let right = relative_right.ceil() as i64;
    let bottom = relative_bottom.ceil() as i64;
    let width =
        u32::try_from(right - left).map_err(|_| "pet surface width overflow".to_string())?;
    let height =
        u32::try_from(bottom - top).map_err(|_| "pet surface height overflow".to_string())?;
    let local_anchor_x =
        u32::try_from(-left).map_err(|_| "pet surface local anchor x overflow".to_string())?;
    let local_anchor_y =
        u32::try_from(-top).map_err(|_| "pet surface local anchor y overflow".to_string())?;
    let placement = PhysicalPlacement {
        x: i32::try_from(i64::from(anchor.x) + left)
            .map_err(|_| "pet window x coordinate overflow".to_string())?,
        y: i32::try_from(i64::from(anchor.y) + top)
            .map_err(|_| "pet window y coordinate overflow".to_string())?,
        width,
        height,
    };
    if existing_anchor.is_none() {
        ensure_placement_within_work_area(placement, monitor.work_area)?;
    }

    Ok(LayoutApplication {
        applied: true,
        revision,
        state,
        contract_version: contract.schema_version,
        content_scale,
        scale_factor: monitor.scale_factor,
        physical_placement: placement,
        active_bounds: visible_surface_bounds,
        physical_local_anchor: [local_anchor_x, local_anchor_y],
        portrait_anchor: anchor,
        work_area: monitor.work_area,
        monitor_name: monitor.name.clone(),
        backend_mode: platform_backend_mode().0,
        degraded_reason: platform_backend_mode().1,
    })
}

fn platform_backend_mode() -> (&'static str, Option<&'static str>) {
    #[cfg(windows)]
    return ("windows_region", None);
    #[cfg(target_os = "macos")]
    return ("macos_cursor_router", None);
    #[cfg(target_os = "linux")]
    {
        if native_wayland_session() {
            return (
                "wayland_degraded_anchor",
                Some("native Wayland does not expose global surface coordinates"),
            );
        }
        return ("x11_input_region", None);
    }
    #[allow(unreachable_code)]
    (
        "unsupported",
        Some("native input region backend is unavailable"),
    )
}

#[cfg(any(target_os = "linux", test))]
fn is_native_wayland_environment(
    gdk_backend: Option<&str>,
    display: Option<&str>,
    wayland_display: Option<&str>,
) -> bool {
    match gdk_backend.filter(|value| !value.trim().is_empty()) {
        Some(backends) => backends
            .split(',')
            .next()
            .is_some_and(|candidate| candidate.trim() == "wayland"),
        None => display.is_none() && wayland_display.is_some(),
    }
}

#[cfg(target_os = "linux")]
pub fn native_wayland_session() -> bool {
    let gdk_backend = std::env::var("GDK_BACKEND").ok();
    let display = std::env::var("DISPLAY").ok();
    let wayland_display = std::env::var("WAYLAND_DISPLAY").ok();
    is_native_wayland_environment(
        gdk_backend.as_deref(),
        display.as_deref(),
        wayland_display.as_deref(),
    )
}

pub fn anchor_from_window_position(
    window_position: PhysicalPoint,
    physical_local_anchor: [u32; 2],
) -> Result<PhysicalPoint, String> {
    let x = i64::from(window_position.x) + i64::from(physical_local_anchor[0]);
    let y = i64::from(window_position.y) + i64::from(physical_local_anchor[1]);
    Ok(PhysicalPoint {
        x: x.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32,
        y: y.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32,
    })
}

fn content_scale_for_work_area(
    monitor: &MonitorDescriptor,
    visible_surface_bounds: [u32; 4],
) -> Result<f64, String> {
    let physical_width = f64::from(visible_surface_bounds[2]) * monitor.scale_factor;
    let physical_height = f64::from(visible_surface_bounds[3]) * monitor.scale_factor;
    Ok((f64::from(monitor.work_area.width) / physical_width)
        .min(f64::from(monitor.work_area.height) / physical_height)
        .min(1.0))
}

fn fit_contract_to_work_area(
    contract: &LayoutContract,
    monitor: &MonitorDescriptor,
    visible_surface_bounds: [u32; 4],
) -> Result<(f64, AnchorEnvelope), String> {
    let mut content_scale = content_scale_for_work_area(monitor, visible_surface_bounds)?;
    for _ in 0..16 {
        let envelope = anchor_envelope(
            contract,
            visible_surface_bounds,
            monitor.scale_factor,
            content_scale,
        )?;
        if envelope.right.saturating_sub(envelope.left) <= i64::from(monitor.work_area.width)
            && envelope.bottom.saturating_sub(envelope.top) <= i64::from(monitor.work_area.height)
        {
            return Ok((content_scale, envelope));
        }
        content_scale *= 0.995;
    }
    Err("layout envelope cannot fit inside target work area".to_string())
}

#[cfg(test)]
fn scale_layout(layout: &StateLayout, scale_factor: f64, content_scale: f64) -> ScaledLayout {
    let scale = scale_factor * content_scale;
    ScaledLayout {
        size: [
            round_positive(f64::from(layout.window_size[0]) * scale),
            round_positive(f64::from(layout.window_size[1]) * scale),
        ],
        anchor: [
            round_nonnegative(f64::from(layout.portrait_anchor[0]) * scale),
            round_nonnegative(f64::from(layout.portrait_anchor[1]) * scale),
        ],
    }
}

#[cfg(test)]
fn round_positive(value: f64) -> u32 {
    value.round().max(1.0).min(f64::from(u32::MAX)) as u32
}

#[cfg(test)]
fn round_nonnegative(value: f64) -> u32 {
    value.round().max(0.0).min(f64::from(u32::MAX)) as u32
}

fn validate_visible_surface_bounds(
    contract: &LayoutContract,
    bounds: [u32; 4],
) -> Result<(), String> {
    let [x, y, width, height] = bounds;
    let [viewport_width, viewport_height] = contract.viewport.window_size;
    if width == 0
        || height == 0
        || x.saturating_add(width) > viewport_width
        || y.saturating_add(height) > viewport_height
    {
        return Err("visible pet surface escapes native viewport envelope".to_string());
    }
    Ok(())
}

fn anchor_envelope(
    contract: &LayoutContract,
    visible_surface_bounds: [u32; 4],
    scale_factor: f64,
    content_scale: f64,
) -> Result<AnchorEnvelope, String> {
    let scale = scale_factor * content_scale;
    let [x, y, width, height] = visible_surface_bounds;
    let [anchor_x, anchor_y] = contract.viewport.portrait_anchor;
    let left = ((f64::from(x) - f64::from(anchor_x)) * scale).floor() as i64;
    let top = ((f64::from(y) - f64::from(anchor_y)) * scale).floor() as i64;
    let right = ((f64::from(x.saturating_add(width)) - f64::from(anchor_x)) * scale).ceil() as i64;
    let bottom =
        ((f64::from(y.saturating_add(height)) - f64::from(anchor_y)) * scale).ceil() as i64;
    if left > 0 || top > 0 || right <= left || bottom <= top {
        return Err("visible pet surface cannot represent the portrait anchor".to_string());
    }
    Ok(AnchorEnvelope {
        left,
        right,
        top,
        bottom,
    })
}

fn resolve_anchor(
    work_area: PhysicalRect,
    envelope: AnchorEnvelope,
    requested: Option<PhysicalPoint>,
) -> Result<PhysicalPoint, String> {
    let min_x = i64::from(work_area.x) - envelope.left;
    let max_x = work_area.right() - envelope.right;
    let min_y = i64::from(work_area.y) - envelope.top;
    let max_y = work_area.bottom() - envelope.bottom;
    if min_x > max_x || min_y > max_y {
        return Err("layout envelope cannot fit inside target work area".to_string());
    }
    if let Some(requested) = requested {
        return Ok(requested);
    }
    Ok(PhysicalPoint {
        x: i32::try_from(max_x).map_err(|_| "default anchor x overflow".to_string())?,
        y: i32::try_from(max_y).map_err(|_| "default anchor y overflow".to_string())?,
    })
}

fn ensure_placement_within_work_area(
    placement: PhysicalPlacement,
    work_area: PhysicalRect,
) -> Result<(), String> {
    let left = i64::from(placement.x);
    let top = i64::from(placement.y);
    let right = left + i64::from(placement.width);
    let bottom = top + i64::from(placement.height);
    if left < i64::from(work_area.x)
        || top < i64::from(work_area.y)
        || right > work_area.right()
        || bottom > work_area.bottom()
    {
        return Err("computed visible pet surface escaped target work area".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn contract() -> LayoutContract {
        serde_json::from_str(include_str!("../../frontend/pet/layout-contract.json"))
            .expect("layout contract must parse")
    }

    fn monitor(work_area: PhysicalRect, scale_factor: f64) -> MonitorDescriptor {
        MonitorDescriptor {
            name: Some("test".to_string()),
            work_area,
            scale_factor,
        }
    }

    fn visible_bounds(scale_percent: u16) -> [u32; 4] {
        crate::window_interaction::logical_visible_surface_bounds(
            &contract(),
            PresentationState::Product,
            scale_percent,
        )
        .expect("product visible surface must be valid")
    }

    fn apply(
        contract: &LayoutContract,
        state: PresentationState,
        revision: u64,
        monitor: &MonitorDescriptor,
        anchor: Option<PhysicalPoint>,
        scale_percent: u16,
    ) -> Result<LayoutApplication, String> {
        apply_window_layout(
            contract,
            state,
            revision,
            monitor,
            anchor,
            visible_bounds(scale_percent),
        )
    }

    fn assert_visible_inside(application: &LayoutApplication, scale_percent: u16) {
        let _ = scale_percent;
        ensure_placement_within_work_area(application.physical_placement, application.work_area)
            .expect("visible surface must stay inside work area");
    }

    #[test]
    fn shared_contract_defines_one_fixed_bounded_product_layout() {
        let contract = contract();
        contract.validate().expect("contract should validate");
        assert_eq!(
            contract
                .layout(PresentationState::Product)
                .unwrap()
                .window_size,
            [900, 996]
        );
    }

    fn control_surface(bubble_rect: [u32; 4], input_rect: [u32; 4]) -> ControlSurfaceLayout {
        ControlSurfaceLayout {
            bubble_rect,
            input_rect,
            controls_rect: [
                bubble_rect[0] + bubble_rect[2] - 40,
                bubble_rect[1] + 10,
                30,
                30,
            ],
        }
    }

    #[test]
    fn adaptive_control_surface_accepts_compact_and_four_line_geometry() {
        let contract = contract();
        let compact = control_surface([130, 720, 640, 88], [130, 818, 640, 52]);
        contract
            .validate_control_surface(PresentationState::Product, &compact)
            .expect("compact surface should validate");

        let four_line = control_surface([130, 618, 640, 128], [130, 756, 640, 114]);
        contract
            .validate_control_surface(PresentationState::Product, &four_line)
            .expect("four-line surface should validate");
    }

    #[test]
    fn adaptive_control_surface_rejects_bounds_width_center_gap_and_controls_forgery() {
        let contract = contract();
        let cases = [
            control_surface([130, 720, 640, 88], [130, 960, 640, 52]),
            control_surface([70, 720, 761, 88], [70, 818, 761, 52]),
            control_surface([120, 720, 640, 88], [120, 818, 640, 52]),
            control_surface([130, 720, 640, 88], [131, 818, 640, 52]),
            control_surface([130, 720, 640, 88], [130, 814, 640, 52]),
        ];
        for surface in cases {
            assert!(contract
                .validate_control_surface(PresentationState::Product, &surface)
                .is_err());
        }

        let mut forged_controls = control_surface([130, 720, 640, 88], [130, 818, 640, 52]);
        forged_controls.controls_rect[0] += 1;
        assert_eq!(
            contract
                .validate_control_surface(PresentationState::Product, &forged_controls)
                .unwrap_err(),
            "CONTROL_SURFACE_INVALID:controlsRect"
        );
    }

    #[test]
    fn requested_anchor_may_move_the_entire_visible_surface_outside_the_work_area() {
        let contract = contract();
        for scale_factor in [1.0, 1.25, 1.5] {
            let monitor = monitor(
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 2560,
                    height: 1800,
                },
                scale_factor,
            );
            let result = apply(
                &contract,
                PresentationState::Product,
                1,
                &monitor,
                Some(PhysicalPoint { x: 0, y: 0 }),
                100,
            )
            .unwrap();
            assert_eq!(result.portrait_anchor, PhysicalPoint { x: 0, y: 0 });
            assert!(result.physical_placement.x < 0);
            assert!(result.physical_placement.y < 0);
            assert!(
                ensure_placement_within_work_area(result.physical_placement, result.work_area,)
                    .is_err()
            );

            let initial = apply(
                &contract,
                PresentationState::Product,
                2,
                &monitor,
                None,
                100,
            )
            .unwrap();
            assert_visible_inside(&initial, 100);
        }
    }

    #[test]
    fn scale_changes_preserve_an_explicit_offscreen_anchor() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: 0,
                y: 0,
                width: 1920,
                height: 1040,
            },
            1.0,
        );
        let parked = apply(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            Some(PhysicalPoint { x: 320, y: 656 }),
            100,
        )
        .unwrap();
        let enlarged = apply(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(parked.portrait_anchor),
            150,
        )
        .unwrap();
        assert_eq!(enlarged.portrait_anchor, parked.portrait_anchor);
        assert_ne!(enlarged.physical_placement, parked.physical_placement);

        let reduced = apply(
            &contract,
            PresentationState::Product,
            3,
            &monitor,
            Some(enlarged.portrait_anchor),
            50,
        )
        .unwrap();
        assert_eq!(reduced.portrait_anchor, enlarged.portrait_anchor);
        assert_ne!(reduced.physical_placement, enlarged.physical_placement);
    }

    #[test]
    fn invalid_visible_surface_bounds_fail_closed() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: 0,
                y: 0,
                width: 1920,
                height: 1040,
            },
            1.0,
        );
        for bounds in [[0, 0, 0, 10], [899, 0, 2, 10], [0, 995, 10, 2]] {
            assert!(apply_window_layout(
                &contract,
                PresentationState::Product,
                1,
                &monitor,
                None,
                bounds,
            )
            .is_err());
        }
    }

    #[test]
    fn state_changes_preserve_the_physical_portrait_anchor_at_all_target_dpis() {
        let contract = contract();
        for scale_factor in [1.0, 1.25, 1.5] {
            let monitor = monitor(
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 2560,
                    height: 1440,
                },
                scale_factor,
            );
            let mut anchor = None;
            let mut placement = None;
            for (revision, state) in PresentationState::all().into_iter().enumerate() {
                let result =
                    apply(&contract, state, revision as u64 + 1, &monitor, anchor, 100).unwrap();
                if let Some(previous) = anchor {
                    assert_eq!(result.portrait_anchor, previous);
                }
                if let Some(previous) = placement {
                    assert_eq!(result.physical_placement, previous);
                }
                assert_visible_inside(&result, 100);
                anchor = Some(result.portrait_anchor);
                placement = Some(result.physical_placement);
            }
        }
    }

    #[test]
    fn negative_coordinate_secondary_monitor_is_selected_and_preserves_anchor() {
        let monitors = vec![
            monitor(
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 1920,
                    height: 1040,
                },
                1.0,
            ),
            monitor(
                PhysicalRect {
                    x: -2560,
                    y: -180,
                    width: 2560,
                    height: 1440,
                },
                1.25,
            ),
        ];
        let anchor = PhysicalPoint { x: -600, y: 1050 };
        assert_eq!(select_target_monitor(&monitors, anchor), Some(1));
        let result = apply(
            &contract(),
            PresentationState::Product,
            1,
            &monitors[1],
            Some(anchor),
            100,
        )
        .unwrap();
        assert_eq!(result.portrait_anchor, anchor);
        assert_visible_inside(&result, 100);
    }

    #[test]
    fn nearest_monitor_is_deterministic_for_an_anchor_between_displays() {
        let monitors = vec![
            monitor(
                PhysicalRect {
                    x: -1920,
                    y: 0,
                    width: 1920,
                    height: 1080,
                },
                1.0,
            ),
            monitor(
                PhysicalRect {
                    x: 200,
                    y: -300,
                    width: 2560,
                    height: 1440,
                },
                1.5,
            ),
        ];
        assert_eq!(
            select_target_monitor(&monitors, PhysicalPoint { x: 120, y: 500 }),
            Some(1)
        );
    }

    #[test]
    fn requested_anchor_near_each_edge_is_preserved_without_work_area_clamping() {
        let contract = contract();
        for scale_factor in [1.0, 1.25, 1.5] {
            let monitor = monitor(
                PhysicalRect {
                    x: -1280,
                    y: 40,
                    width: 1280,
                    height: 984,
                },
                scale_factor,
            );
            for requested in [
                PhysicalPoint { x: -1280, y: 40 },
                PhysicalPoint { x: 0, y: 40 },
                PhysicalPoint { x: -1280, y: 1024 },
                PhysicalPoint { x: 0, y: 1024 },
            ] {
                let first = apply(
                    &contract,
                    PresentationState::Product,
                    1,
                    &monitor,
                    Some(requested),
                    100,
                )
                .unwrap();
                assert_eq!(first.portrait_anchor, requested);
                let anchor = first.portrait_anchor;
                for state in PresentationState::all() {
                    let result = apply(&contract, state, 2, &monitor, Some(anchor), 100).unwrap();
                    assert_eq!(result.portrait_anchor, anchor);
                }
            }
        }
    }

    #[test]
    fn a_window_envelope_larger_than_the_work_area_is_uniformly_fitted() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: -400,
                y: -200,
                width: 360,
                height: 240,
            },
            1.5,
        );
        let mut anchor = None;
        for state in PresentationState::all() {
            let result = apply(&contract, state, 1, &monitor, anchor, 100).unwrap();
            assert!(result.content_scale < 1.0);
            assert!(result.physical_placement.width <= monitor.work_area.width);
            assert!(result.physical_placement.height <= monitor.work_area.height);
            if let Some(previous) = anchor {
                assert_eq!(result.portrait_anchor, previous);
            }
            assert_visible_inside(&result, 100);
            anchor = Some(result.portrait_anchor);
        }
    }

    #[test]
    fn logical_to_physical_conversion_is_explicit_and_repeatable() {
        let layout = StateLayout {
            window_size: [320, 420],
            portrait_rect: [56, 72, 240, 336],
            bubble_rect: None,
            input_rect: None,
            controls_rect: [8, 370, 193, 38],
            portrait_anchor: [176, 408],
        };
        assert_eq!(scale_layout(&layout, 1.0, 1.0).size, [320, 420]);
        assert_eq!(scale_layout(&layout, 1.25, 1.0).size, [400, 525]);
        assert_eq!(scale_layout(&layout, 1.5, 1.0).size, [480, 630]);
        assert_eq!(scale_layout(&layout, 1.25, 1.0).anchor, [220, 510]);
    }

    #[test]
    fn invalid_scale_and_empty_work_area_fail_closed() {
        let contract = contract();
        for (work_area, scale_factor) in [
            (
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 0,
                    height: 1080,
                },
                1.0,
            ),
            (
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 1920,
                    height: 1080,
                },
                f64::INFINITY,
            ),
        ] {
            assert!(apply_window_layout(
                &contract,
                PresentationState::Product,
                1,
                &monitor(work_area, scale_factor),
                None,
                visible_bounds(100),
            )
            .is_err());
        }
    }

    #[test]
    fn old_or_duplicate_layout_revisions_are_rejected() {
        let mut guard = LayoutRevisionGuard::default();
        assert!(guard.accept(1));
        assert!(guard.accept(4));
        assert!(!guard.accept(2));
        assert!(!guard.accept(4));
        assert!(guard.accept(5));
    }

    #[test]
    fn dragged_window_position_becomes_a_physical_anchor_at_each_target_dpi() {
        let contract = contract();
        for scale_factor in [1.0, 1.25, 1.5] {
            let monitor = monitor(
                PhysicalRect {
                    x: -2560,
                    y: -200,
                    width: 2560,
                    height: 1800,
                },
                scale_factor,
            );
            let position = PhysicalPoint { x: -2300, y: -100 };
            let initial = apply(
                &contract,
                PresentationState::Product,
                1,
                &monitor,
                Some(PhysicalPoint { x: -2000, y: 400 }),
                100,
            )
            .unwrap();
            let anchor =
                anchor_from_window_position(position, initial.physical_local_anchor).unwrap();
            let result = apply(
                &contract,
                PresentationState::Product,
                2,
                &monitor,
                Some(anchor),
                100,
            )
            .unwrap();
            assert_eq!(result.portrait_anchor, anchor);
            for state in PresentationState::all() {
                let transitioned = apply(&contract, state, 2, &monitor, Some(anchor), 100).unwrap();
                assert_eq!(transitioned.portrait_anchor, anchor);
                assert_eq!(transitioned.physical_placement, result.physical_placement);
            }
        }
    }

    #[test]
    fn dragged_anchor_outside_a_smaller_work_area_is_preserved() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: 200,
                y: 40,
                width: 360,
                height: 240,
            },
            1.5,
        );
        let requested =
            anchor_from_window_position(PhysicalPoint { x: -1000, y: -800 }, [321, 456]).unwrap();
        let result = apply(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            Some(requested),
            100,
        )
        .unwrap();
        assert_eq!(result.portrait_anchor, requested);
        assert!(
            ensure_placement_within_work_area(result.physical_placement, result.work_area,)
                .is_err()
        );
    }

    #[test]
    fn dynamic_envelopes_keep_the_physical_anchor_exact_across_repeated_dpi_changes() {
        let contract = contract();
        let anchor = PhysicalPoint { x: -413, y: 827 };
        let bounds = [
            [126, 326, 648, 660],
            [126, 490, 648, 384],
            [0, 0, 900, 986],
            [210, 610, 480, 376],
        ];
        for cycle in 0..20 {
            let scale_factor = [1.0, 1.25, 1.5, 2.0][cycle % 4];
            let monitor = monitor(
                PhysicalRect {
                    x: -2560,
                    y: -1440,
                    width: 5120,
                    height: 2880,
                },
                scale_factor,
            );
            let application = apply_window_layout(
                &contract,
                PresentationState::Product,
                cycle as u64 + 1,
                &monitor,
                Some(anchor),
                bounds[cycle % bounds.len()],
            )
            .unwrap();
            assert_eq!(
                i64::from(application.physical_placement.x)
                    + i64::from(application.physical_local_anchor[0]),
                i64::from(anchor.x)
            );
            assert_eq!(
                i64::from(application.physical_placement.y)
                    + i64::from(application.physical_local_anchor[1]),
                i64::from(anchor.y)
            );
            assert_eq!(
                anchor_from_window_position(
                    PhysicalPoint {
                        x: application.physical_placement.x,
                        y: application.physical_placement.y,
                    },
                    application.physical_local_anchor,
                )
                .unwrap(),
                anchor
            );
        }
    }

    #[test]
    fn native_placement_uses_the_active_envelope_instead_of_the_canonical_viewport() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            },
            1.0,
        );
        let application = apply_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            [126, 490, 648, 384],
        )
        .unwrap();
        assert_eq!(application.physical_placement.width, 648);
        assert_eq!(application.physical_placement.height, 384);
        assert_ne!(
            [
                application.physical_placement.width,
                application.physical_placement.height
            ],
            contract.viewport.window_size
        );
    }

    #[test]
    fn linux_backend_detection_distinguishes_xwayland_and_native_wayland() {
        assert!(!is_native_wayland_environment(
            None,
            Some(":0"),
            Some("wayland-0")
        ));
        assert!(is_native_wayland_environment(None, None, Some("wayland-0")));
        assert!(is_native_wayland_environment(
            Some("wayland,x11"),
            Some(":0"),
            Some("wayland-0")
        ));
        assert!(!is_native_wayland_environment(
            Some("x11"),
            Some(":0"),
            Some("wayland-0")
        ));
    }
}
