use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

const MAX_CANONICAL_VIEWPORT_WIDTH: u32 = 1200;
const MAX_CANONICAL_VIEWPORT_HEIGHT: u32 = 1600;

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
    pub input_expanded_min_rows: u32,
    pub input_max_rows: u32,
    pub input_toolbar_height: u32,
    pub input_expanded_gap: u32,
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

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InputSurfaceTransition {
    pub duration_ms: u32,
    #[serde(default)]
    pub staging_height: Option<u32>,
    #[serde(default)]
    pub delay_ms: u32,
}

impl InputSurfaceTransition {
    pub fn validate(self) -> Result<Self, String> {
        if self.duration_ms != 0 && !(120..=300).contains(&self.duration_ms) {
            return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
        }
        if self.staging_height == Some(0) {
            return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
        }
        if self.delay_ms > 200 {
            return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
        }
        Ok(self)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ViewportLayout {
    pub window_size: [u32; 2],
    pub content_scale_size: [u32; 2],
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
        if self.schema_version != 1 {
            return Err(format!(
                "unsupported layout contract version: {}",
                self.schema_version
            ));
        }
        let [viewport_width, viewport_height] = self.viewport.window_size;
        let [scale_width, scale_height] = self.viewport.content_scale_size;
        let [viewport_anchor_x, viewport_anchor_y] = self.viewport.portrait_anchor;
        if viewport_width == 0
            || viewport_height == 0
            || viewport_width > MAX_CANONICAL_VIEWPORT_WIDTH
            || viewport_height > MAX_CANONICAL_VIEWPORT_HEIGHT
            || viewport_anchor_x > viewport_width
            || viewport_anchor_y > viewport_height
            || scale_width == 0
            || scale_height == 0
            || scale_width > viewport_width
            || scale_height > viewport_height
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
            || !(1..=3).contains(&panel.input_expanded_min_rows)
            || panel.input_max_rows < panel.input_expanded_min_rows
            || panel.input_max_rows > 8
            || panel.input_toolbar_height == 0
        {
            return Err("invalid adaptive control panel contract".to_string());
        }
        for state in PresentationState::all_values() {
            let layout = self
                .states
                .get(state.key())
                .ok_or_else(|| format!("missing layout state: {}", state.key()))?;
            let [width, height] = layout.window_size;
            if width == 0
                || height == 0
                || width > MAX_CANONICAL_VIEWPORT_WIDTH
                || height > MAX_CANONICAL_VIEWPORT_HEIGHT
            {
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
    pub visible_fit_bounds: [u32; 4],
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
            visible_fit_bounds: [0, 0, 0, 0],
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

    pub fn latest(&self) -> u64 {
        self.latest
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnchorPolicy {
    Automatic,
    UserPositioned,
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
    apply_window_layout_with_fit_bounds(
        contract,
        state,
        revision,
        monitor,
        existing_anchor,
        if existing_anchor.is_some() {
            AnchorPolicy::UserPositioned
        } else {
            AnchorPolicy::Automatic
        },
        visible_surface_bounds,
        visible_surface_bounds,
    )
}

pub fn apply_window_layout_with_fit_bounds(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<PhysicalPoint>,
    anchor_policy: AnchorPolicy,
    visible_fit_bounds: [u32; 4],
    resident_backing_bounds: [u32; 4],
) -> Result<LayoutApplication, String> {
    contract.validate()?;
    validate_fit_inside_backing(contract, visible_fit_bounds, resident_backing_bounds)?;
    if !monitor.scale_factor.is_finite() || monitor.scale_factor <= 0.0 {
        return Err("monitor scale factor must be positive and finite".to_string());
    }
    if monitor.work_area.width == 0 || monitor.work_area.height == 0 {
        return Err("monitor work area must be non-empty".to_string());
    }

    let (content_scale, envelope) =
        fit_contract_to_work_area(contract, monitor, visible_fit_bounds)?;
    let anchor = resolve_anchor(monitor.work_area, envelope, existing_anchor, anchor_policy)?;
    let scale = monitor.scale_factor * content_scale;
    let [surface_x, surface_y, surface_width, surface_height] = resident_backing_bounds;
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
    if anchor_policy == AnchorPolicy::Automatic {
        let fit_placement = PhysicalPlacement {
            x: i32::try_from(i64::from(anchor.x) + envelope.left)
                .map_err(|_| "visible pet surface x coordinate overflow".to_string())?,
            y: i32::try_from(i64::from(anchor.y) + envelope.top)
                .map_err(|_| "visible pet surface y coordinate overflow".to_string())?,
            width: u32::try_from(envelope.right - envelope.left)
                .map_err(|_| "visible pet surface width overflow".to_string())?,
            height: u32::try_from(envelope.bottom - envelope.top)
                .map_err(|_| "visible pet surface height overflow".to_string())?,
        };
        ensure_placement_within_work_area(fit_placement, monitor.work_area)?;
    }

    Ok(LayoutApplication {
        applied: true,
        revision,
        state,
        contract_version: contract.schema_version,
        content_scale,
        scale_factor: monitor.scale_factor,
        physical_placement: placement,
        visible_fit_bounds,
        active_bounds: resident_backing_bounds,
        physical_local_anchor: [local_anchor_x, local_anchor_y],
        portrait_anchor: anchor,
        work_area: monitor.work_area,
        monitor_name: monitor.name.clone(),
        backend_mode: platform_backend_mode().0,
        degraded_reason: platform_backend_mode().1,
    })
}

/// Expands an already committed surface without recalculating its monitor fit or anchor.
///
/// Context menus are painted inside the existing WebView coordinate space, so opening one must
/// only grow the native surface toward the right/bottom. Re-running `apply_window_layout` would
/// allow the work-area placement policy to choose a new default anchor and can move a surface
/// that the user just dragged. The caller must provide the canonical viewport anchor so the
/// physical edge rounding remains identical to the normal layout calculation.
pub fn expand_application_preserving_anchor(
    application: &LayoutApplication,
    expanded_bounds: [u32; 4],
    viewport_anchor: [u32; 2],
) -> Result<LayoutApplication, String> {
    let [base_x, base_y, base_width, base_height] = application.active_bounds;
    let [next_x, next_y, next_width, next_height] = expanded_bounds;
    if base_width == 0 || base_height == 0 || next_width == 0 || next_height == 0 {
        return Err("expanded pet surface must be non-empty".to_string());
    }
    if next_x != base_x || next_y != base_y {
        return Err("expanded pet surface must preserve its top-left origin".to_string());
    }
    let base_right = u64::from(base_x)
        .checked_add(u64::from(base_width))
        .ok_or_else(|| "base pet surface right edge overflow".to_string())?;
    let base_bottom = u64::from(base_y)
        .checked_add(u64::from(base_height))
        .ok_or_else(|| "base pet surface bottom edge overflow".to_string())?;
    let next_right = u64::from(next_x)
        .checked_add(u64::from(next_width))
        .ok_or_else(|| "expanded pet surface right edge overflow".to_string())?;
    let next_bottom = u64::from(next_y)
        .checked_add(u64::from(next_height))
        .ok_or_else(|| "expanded pet surface bottom edge overflow".to_string())?;
    if next_right < base_right || next_bottom < base_bottom {
        return Err("expanded pet surface cannot shrink the committed bounds".to_string());
    }

    let scale = application.scale_factor * application.content_scale;
    if !scale.is_finite() || scale <= 0.0 {
        return Err("pet surface scale must be positive and finite".to_string());
    }
    let [anchor_x, anchor_y] = viewport_anchor;
    let edge = |value: u32, anchor: u32, round_up: bool| -> Result<i64, String> {
        let raw = (f64::from(value) - f64::from(anchor)) * scale;
        if !raw.is_finite() || raw < i64::MIN as f64 || raw > i64::MAX as f64 {
            return Err("pet surface physical edge overflow".to_string());
        }
        Ok(if round_up {
            raw.ceil() as i64
        } else {
            raw.floor() as i64
        })
    };
    let left = edge(base_x, anchor_x, false)?;
    let top = edge(base_y, anchor_y, false)?;
    let base_right_physical = edge(
        u32::try_from(base_right).map_err(|_| "base pet surface width overflow")?,
        anchor_x,
        true,
    )?;
    let base_bottom_physical = edge(
        u32::try_from(base_bottom).map_err(|_| "base pet surface height overflow")?,
        anchor_y,
        true,
    )?;
    let next_right_physical = edge(
        u32::try_from(next_right).map_err(|_| "expanded pet surface width overflow")?,
        anchor_x,
        true,
    )?;
    let next_bottom_physical = edge(
        u32::try_from(next_bottom).map_err(|_| "expanded pet surface height overflow")?,
        anchor_y,
        true,
    )?;
    let expected_local_anchor = [
        u32::try_from(-left).map_err(|_| "pet surface local anchor x overflow")?,
        u32::try_from(-top).map_err(|_| "pet surface local anchor y overflow")?,
    ];
    let expected_width = u32::try_from(base_right_physical - left)
        .map_err(|_| "base pet surface physical width overflow")?;
    let expected_height = u32::try_from(base_bottom_physical - top)
        .map_err(|_| "base pet surface physical height overflow")?;
    if application.physical_local_anchor != expected_local_anchor
        || application.physical_placement.width != expected_width
        || application.physical_placement.height != expected_height
    {
        return Err("committed pet surface geometry is inconsistent".to_string());
    }

    let mut expanded = application.clone();
    expanded.active_bounds = expanded_bounds;
    expanded.physical_placement.width = u32::try_from(next_right_physical - left)
        .map_err(|_| "expanded pet surface physical width overflow")?;
    expanded.physical_placement.height = u32::try_from(next_bottom_physical - top)
        .map_err(|_| "expanded pet surface physical height overflow")?;
    if expanded.physical_placement.width < application.physical_placement.width
        || expanded.physical_placement.height < application.physical_placement.height
    {
        return Err("expanded pet surface physical bounds unexpectedly shrank".to_string());
    }
    Ok(expanded)
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

fn content_scale_for_bounds(monitor: &MonitorDescriptor, bounds: [u32; 4]) -> Result<f64, String> {
    let physical_width = f64::from(bounds[2]) * monitor.scale_factor;
    let physical_height = f64::from(bounds[3]) * monitor.scale_factor;
    Ok((f64::from(monitor.work_area.width) / physical_width)
        .min(f64::from(monitor.work_area.height) / physical_height)
        .min(1.0))
}

fn fit_contract_to_work_area(
    contract: &LayoutContract,
    monitor: &MonitorDescriptor,
    visible_fit_bounds: [u32; 4],
) -> Result<(f64, AnchorEnvelope), String> {
    // contentScaleSize preserves the normal product scale. The current visible content may be
    // taller (for example after moving the composer downward), so it contributes a second direct
    // limit. The resident WebView backing envelope is intentionally not part of either limit.
    let reference_scale = content_scale_for_bounds(
        monitor,
        [
            0,
            0,
            contract.viewport.content_scale_size[0],
            contract.viewport.content_scale_size[1],
        ],
    )?;
    let visible_scale = content_scale_for_bounds(monitor, visible_fit_bounds)?;
    let mut content_scale = reference_scale.min(visible_scale);
    let mut envelope = anchor_envelope(
        contract,
        visible_fit_bounds,
        monitor.scale_factor,
        content_scale,
    )?;
    let width = envelope.right.saturating_sub(envelope.left);
    let height = envelope.bottom.saturating_sub(envelope.top);
    if width > i64::from(monitor.work_area.width) || height > i64::from(monitor.work_area.height) {
        // floor/ceil around the portrait anchor can add one physical pixel. Correct that exact
        // rounding excess once instead of relying on a capped sequence of percentage guesses.
        let width_limit = f64::from(monitor.work_area.width.saturating_sub(1)) / width as f64;
        let height_limit = f64::from(monitor.work_area.height.saturating_sub(1)) / height as f64;
        content_scale *= width_limit.min(height_limit).min(1.0);
        envelope = anchor_envelope(
            contract,
            visible_fit_bounds,
            monitor.scale_factor,
            content_scale,
        )?;
    }
    if envelope.right.saturating_sub(envelope.left) > i64::from(monitor.work_area.width)
        || envelope.bottom.saturating_sub(envelope.top) > i64::from(monitor.work_area.height)
    {
        return Err("layout envelope cannot fit inside target work area".to_string());
    }
    Ok((content_scale, envelope))
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

fn validate_surface_bounds(bounds: [u32; 4]) -> Result<(), String> {
    let [x, y, width, height] = bounds;
    if width == 0
        || height == 0
        || x.checked_add(width).is_none()
        || y.checked_add(height).is_none()
    {
        return Err("pet surface bounds are invalid".to_string());
    }
    Ok(())
}

fn validate_fit_inside_backing(
    contract: &LayoutContract,
    visible_fit_bounds: [u32; 4],
    resident_backing_bounds: [u32; 4],
) -> Result<(), String> {
    validate_surface_bounds(visible_fit_bounds)?;
    validate_surface_bounds(resident_backing_bounds)?;
    let [fit_x, fit_y, fit_width, fit_height] = visible_fit_bounds;
    let [backing_x, backing_y, backing_width, backing_height] = resident_backing_bounds;
    let fit_right = u64::from(fit_x) + u64::from(fit_width);
    let fit_bottom = u64::from(fit_y) + u64::from(fit_height);
    let backing_right = u64::from(backing_x) + u64::from(backing_width);
    let backing_bottom = u64::from(backing_y) + u64::from(backing_height);
    if backing_right > u64::from(contract.viewport.window_size[0]) || backing_bottom > 1_600 {
        return Err("resident pet backing exceeds its bounded viewport".to_string());
    }
    if fit_x < backing_x
        || fit_y < backing_y
        || fit_right > backing_right
        || fit_bottom > backing_bottom
    {
        return Err("visible pet surface escapes resident backing envelope".to_string());
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
    policy: AnchorPolicy,
) -> Result<PhysicalPoint, String> {
    let min_x = i64::from(work_area.x) - envelope.left;
    let max_x = work_area.right() - envelope.right;
    let min_y = i64::from(work_area.y) - envelope.top;
    let max_y = work_area.bottom() - envelope.bottom;
    if min_x > max_x || min_y > max_y {
        return Err("layout envelope cannot fit inside target work area".to_string());
    }
    if let Some(requested) = requested {
        return Ok(match policy {
            AnchorPolicy::Automatic => PhysicalPoint {
                x: i32::try_from(i64::from(requested.x).clamp(min_x, max_x))
                    .map_err(|_| "automatic anchor x overflow".to_string())?,
                y: i32::try_from(i64::from(requested.y).clamp(min_y, max_y))
                    .map_err(|_| "automatic anchor y overflow".to_string())?,
            },
            AnchorPolicy::UserPositioned => requested,
        });
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

    fn assert_fit_bounds_inside(application: &LayoutApplication) {
        let envelope = anchor_envelope(
            &contract(),
            application.visible_fit_bounds,
            application.scale_factor,
            application.content_scale,
        )
        .unwrap();
        ensure_placement_within_work_area(
            PhysicalPlacement {
                x: i32::try_from(i64::from(application.portrait_anchor.x) + envelope.left).unwrap(),
                y: i32::try_from(i64::from(application.portrait_anchor.y) + envelope.top).unwrap(),
                width: u32::try_from(envelope.right - envelope.left).unwrap(),
                height: u32::try_from(envelope.bottom - envelope.top).unwrap(),
            },
            application.work_area,
        )
        .expect("visible fit bounds must stay inside work area");
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
            [900, 1_374]
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
    fn adaptive_control_surface_accepts_fixed_top_compact_and_three_line_geometry() {
        let contract = contract();
        let compact = control_surface([130, 680, 640, 128], [130, 818, 640, 52]);
        contract
            .validate_control_surface(PresentationState::Product, &compact)
            .expect("compact surface should validate");

        let one_line_toolbar = control_surface([130, 680, 640, 128], [130, 818, 640, 100]);
        contract
            .validate_control_surface(PresentationState::Product, &one_line_toolbar)
            .expect("one-line toolbar surface should validate");

        let three_line = control_surface([130, 680, 640, 128], [130, 818, 640, 148]);
        contract
            .validate_control_surface(PresentationState::Product, &three_line)
            .expect("three-line surface should validate");

        let maximum_downward_offsets = control_surface([20, 880, 860, 128], [20, 1_218, 860, 152]);
        contract
            .validate_control_surface(PresentationState::Product, &maximum_downward_offsets)
            .expect("the complete 0.9.10 downward adjustment range should validate");
    }

    #[test]
    fn input_surface_transition_duration_is_strictly_bounded() {
        assert_eq!(
            InputSurfaceTransition {
                duration_ms: 260,
                staging_height: Some(76),
                delay_ms: 40,
            }
            .validate()
            .unwrap()
            .duration_ms,
            260
        );
        assert!(InputSurfaceTransition {
            duration_ms: 0,
            staging_height: None,
            delay_ms: 0,
        }
        .validate()
        .is_ok());
        assert!(InputSurfaceTransition {
            duration_ms: 80,
            staging_height: None,
            delay_ms: 0,
        }
        .validate()
        .is_err());
        assert!(InputSurfaceTransition {
            duration_ms: 301,
            staging_height: None,
            delay_ms: 0,
        }
        .validate()
        .is_err());
        assert!(InputSurfaceTransition {
            duration_ms: 260,
            staging_height: Some(0),
            delay_ms: 0,
        }
        .validate()
        .is_err());
    }

    #[test]
    fn adaptive_control_surface_rejects_bounds_width_center_gap_and_controls_forgery() {
        let contract = contract();
        let cases = [
            control_surface([130, 720, 640, 88], [130, 1323, 640, 52]),
            control_surface([20, 720, 861, 88], [20, 818, 861, 52]),
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
    fn resident_backing_does_not_participate_in_work_area_fit_at_target_dpis() {
        let contract = contract();
        for scale_factor in [1.0, 1.25, 1.5] {
            let application = apply_window_layout_with_fit_bounds(
                &contract,
                PresentationState::Product,
                1,
                &monitor(
                    PhysicalRect {
                        x: 0,
                        y: 0,
                        width: 2_560,
                        height: 1_392,
                    },
                    scale_factor,
                ),
                None,
                AnchorPolicy::Automatic,
                [126, 326, 648, 660],
                [0, 0, 900, 1_490],
            )
            .unwrap();
            assert_eq!(application.active_bounds, [0, 0, 900, 1_490]);
            assert_eq!(application.visible_fit_bounds, [126, 326, 648, 660]);
            assert_fit_bounds_inside(&application);
            if scale_factor > 1.0 {
                assert!(application.physical_placement.height > application.work_area.height);
            }
        }
    }

    #[test]
    fn direct_fit_handles_1080p_high_dpi_without_percentage_retry_limits() {
        let contract = contract();
        let application = apply_window_layout_with_fit_bounds(
            &contract,
            PresentationState::Product,
            1,
            &monitor(
                PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 1_920,
                    height: 1_040,
                },
                1.25,
            ),
            None,
            AnchorPolicy::Automatic,
            [0, 0, 900, 996],
            [0, 0, 900, 1_490],
        )
        .unwrap();
        assert!(application.content_scale < 0.84);
        assert_fit_bounds_inside(&application);
    }

    #[test]
    fn automatic_anchor_clamps_but_user_positioned_anchor_is_preserved() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_040,
            },
            1.0,
        );
        let requested = PhysicalPoint { x: 0, y: 0 };
        let automatic = apply_window_layout_with_fit_bounds(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            Some(requested),
            AnchorPolicy::Automatic,
            [126, 326, 648, 660],
            [0, 0, 900, 1_490],
        )
        .unwrap();
        assert_ne!(automatic.portrait_anchor, requested);
        assert_fit_bounds_inside(&automatic);

        let user_positioned = apply_window_layout_with_fit_bounds(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(requested),
            AnchorPolicy::UserPositioned,
            [126, 326, 648, 660],
            [0, 0, 900, 1_490],
        )
        .unwrap();
        assert_eq!(user_positioned.portrait_anchor, requested);
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
        assert_eq!(guard.latest(), 5);
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
    fn content_scale_does_not_change_when_the_dynamic_envelope_changes() {
        let contract = contract();
        let anchor = PhysicalPoint { x: -413, y: 827 };
        let bounds = [
            [126, 654, 648, 332],
            [126, 326, 648, 660],
            [0, 0, 900, 986],
            [210, 610, 480, 376],
        ];

        for scale_factor in [1.0, 1.25, 1.5, 2.0] {
            let monitor = monitor(
                PhysicalRect {
                    x: -2560,
                    y: -1440,
                    width: 1000,
                    height: 700,
                },
                scale_factor,
            );
            let mut expected_scale = None;
            for cycle in 0..20_u64 {
                let application = apply_window_layout(
                    &contract,
                    PresentationState::Product,
                    cycle + 1,
                    &monitor,
                    Some(anchor),
                    bounds[cycle as usize % bounds.len()],
                )
                .unwrap();
                if let Some(expected_scale) = expected_scale {
                    assert_eq!(application.content_scale, expected_scale);
                } else {
                    expected_scale = Some(application.content_scale);
                }
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
    }

    #[test]
    fn scale_preview_keeps_window_and_all_canonical_anchors_physically_stationary() {
        let contract = contract();
        let anchor = PhysicalPoint { x: -413, y: 827 };
        let mask = crate::character_presentation::PortraitAlphaMask::new(
            4,
            4,
            vec![
                0, 0, 0, 0, //
                0, 255, 255, 0, //
                0, 255, 255, 0, //
                0, 0, 0, 0,
            ],
        );

        for scale_factor in [1.0, 1.25, 1.5, 2.0] {
            let monitor = monitor(
                PhysicalRect {
                    x: -2560,
                    y: -1440,
                    width: 5120,
                    height: 2880,
                },
                scale_factor,
            );
            let mut expected: Option<LayoutApplication> = None;
            for cycle in 0..20_u64 {
                let scale_percent =
                    [50, 75, 100, 125, 150, 125, 100, 75][usize::try_from(cycle).unwrap() % 8];
                let bounds = crate::window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
                    &contract,
                    PresentationState::Product,
                    scale_percent,
                    None,
                    Some(&mask),
                )
                .unwrap();
                let application = apply_window_layout(
                    &contract,
                    PresentationState::Product,
                    cycle + 1,
                    &monitor,
                    Some(anchor),
                    bounds,
                )
                .unwrap();
                if let Some(expected) = expected.as_ref() {
                    assert_eq!(application.active_bounds, expected.active_bounds);
                    assert_eq!(application.physical_placement, expected.physical_placement);
                    assert_eq!(
                        application.physical_local_anchor,
                        expected.physical_local_anchor
                    );
                    assert_eq!(application.content_scale, expected.content_scale);
                } else {
                    expected = Some(application.clone());
                }
                assert_eq!(application.portrait_anchor, anchor);
            }
        }
    }

    #[test]
    fn settled_scale_restores_the_exact_envelope_without_moving_the_portrait_anchor() {
        let contract = contract();
        let anchor = PhysicalPoint { x: -413, y: 827 };
        let monitor = monitor(
            PhysicalRect {
                x: -2560,
                y: -1440,
                width: 5120,
                height: 2880,
            },
            1.25,
        );

        for scale_percent in [50, 100, 125] {
            let preview_bounds = crate::window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
                &contract,
                PresentationState::Product,
                scale_percent,
                None,
                None,
            )
            .unwrap();
            let settled_bounds =
                crate::window_interaction::logical_visible_surface_bounds_with_control_surface(
                    &contract,
                    PresentationState::Product,
                    scale_percent,
                    None,
                    None,
                )
                .unwrap();
            let preview = apply_window_layout(
                &contract,
                PresentationState::Product,
                1,
                &monitor,
                Some(anchor),
                preview_bounds,
            )
            .unwrap();
            let settled = apply_window_layout(
                &contract,
                PresentationState::Product,
                1,
                &monitor,
                Some(anchor),
                settled_bounds,
            )
            .unwrap();

            assert_eq!(preview.portrait_anchor, anchor);
            assert_eq!(settled.portrait_anchor, anchor);
            assert!(settled.active_bounds[1] > preview.active_bounds[1]);
            assert!(settled.physical_placement.height < preview.physical_placement.height);
            assert_eq!(
                anchor_from_window_position(
                    PhysicalPoint {
                        x: settled.physical_placement.x,
                        y: settled.physical_placement.y,
                    },
                    settled.physical_local_anchor,
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
    fn window_surface_regression_context_menu_resize_keeps_dragged_position_and_anchor() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: -2000,
                y: -1200,
                width: 4000,
                height: 3000,
            },
            1.25,
        );
        let anchor = PhysicalPoint { x: 640, y: 900 };
        let base = apply_window_layout(
            &contract,
            PresentationState::Product,
            7,
            &monitor,
            Some(anchor),
            [126, 678, 648, 196],
        )
        .unwrap();
        let expanded = expand_application_preserving_anchor(
            &base,
            [126, 678, 648, 293],
            contract.viewport.portrait_anchor,
        )
        .unwrap();

        assert_eq!(expanded.physical_placement.x, base.physical_placement.x);
        assert_eq!(expanded.physical_placement.y, base.physical_placement.y);
        assert_eq!(expanded.portrait_anchor, base.portrait_anchor);
        assert_eq!(expanded.physical_local_anchor, base.physical_local_anchor);
        assert_eq!(expanded.content_scale, base.content_scale);
        assert_eq!(expanded.scale_factor, base.scale_factor);
        assert_eq!(
            expanded.physical_placement.width,
            base.physical_placement.width
        );
        assert!(expanded.physical_placement.height > base.physical_placement.height);
        assert_eq!(expanded.active_bounds, [126, 678, 648, 293]);
        assert_eq!(
            anchor_from_window_position(
                PhysicalPoint {
                    x: expanded.physical_placement.x,
                    y: expanded.physical_placement.y,
                },
                expanded.physical_local_anchor,
            )
            .unwrap(),
            anchor
        );
        assert!(expand_application_preserving_anchor(
            &base,
            [126, 640, 648, 293],
            contract.viewport.portrait_anchor,
        )
        .is_err());
        assert!(expand_application_preserving_anchor(
            &base,
            [126, 678, 648, 180],
            contract.viewport.portrait_anchor,
        )
        .is_err());
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
