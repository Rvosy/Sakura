use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum PresentationState {
    Idle,
    Bubble,
    Composer,
    Expanded,
}

impl PresentationState {
    fn key(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Bubble => "bubble",
            Self::Composer => "composer",
            Self::Expanded => "expanded",
        }
    }

    #[cfg(test)]
    fn all() -> [Self; 4] {
        [Self::Idle, Self::Bubble, Self::Composer, Self::Expanded]
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LayoutContract {
    pub schema_version: u32,
    pub states: BTreeMap<String, StateLayout>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StateLayout {
    pub window_size: [u32; 2],
    pub portrait_rect: [u32; 4],
    pub bubble_rect: Option<[u32; 4]>,
    pub input_rect: Option<[u32; 4]>,
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
        }
        Ok(())
    }

    fn layout(&self, state: PresentationState) -> Result<&StateLayout, String> {
        self.states
            .get(state.key())
            .ok_or_else(|| format!("missing layout state: {}", state.key()))
    }

    fn all_values() -> [PresentationState; 4] {
        [
            PresentationState::Idle,
            PresentationState::Bubble,
            PresentationState::Composer,
            PresentationState::Expanded,
        ]
    }
}

impl PresentationState {
    fn all_values() -> [Self; 4] {
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
    pub portrait_anchor: PhysicalPoint,
    pub work_area: PhysicalRect,
    pub monitor_name: Option<String>,
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
            portrait_anchor: PhysicalPoint { x: 0, y: 0 },
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 0,
                height: 0,
            },
            monitor_name: None,
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

#[derive(Clone, Copy)]
struct ScaledLayout {
    size: [u32; 2],
    anchor: [u32; 2],
}

#[derive(Clone, Copy)]
struct AnchorEnvelope {
    left: u32,
    right: u32,
    top: u32,
    bottom: u32,
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
) -> Result<LayoutApplication, String> {
    contract.validate()?;
    if !monitor.scale_factor.is_finite() || monitor.scale_factor <= 0.0 {
        return Err("monitor scale factor must be positive and finite".to_string());
    }
    if monitor.work_area.width == 0 || monitor.work_area.height == 0 {
        return Err("monitor work area must be non-empty".to_string());
    }

    let (content_scale, envelope) = fit_contract_to_work_area(contract, monitor)?;
    let anchor = normalize_anchor(monitor.work_area, envelope, existing_anchor)?;
    let scaled = scale_layout(contract.layout(state)?, monitor.scale_factor, content_scale);
    let x = i64::from(anchor.x) - i64::from(scaled.anchor[0]);
    let y = i64::from(anchor.y) - i64::from(scaled.anchor[1]);
    let placement = PhysicalPlacement {
        x: i32::try_from(x).map_err(|_| "pet window x coordinate overflow".to_string())?,
        y: i32::try_from(y).map_err(|_| "pet window y coordinate overflow".to_string())?,
        width: scaled.size[0],
        height: scaled.size[1],
    };
    ensure_within_work_area(placement, monitor.work_area)?;

    Ok(LayoutApplication {
        applied: true,
        revision,
        state,
        contract_version: contract.schema_version,
        content_scale,
        scale_factor: monitor.scale_factor,
        physical_placement: placement,
        portrait_anchor: anchor,
        work_area: monitor.work_area,
        monitor_name: monitor.name.clone(),
    })
}

fn content_scale_for_work_area(
    contract: &LayoutContract,
    monitor: &MonitorDescriptor,
) -> Result<f64, String> {
    let mut maximum_width = 0;
    let mut maximum_height = 0;
    for state in LayoutContract::all_values() {
        let [width, height] = contract.layout(state)?.window_size;
        maximum_width = maximum_width.max(width);
        maximum_height = maximum_height.max(height);
    }
    let physical_width = f64::from(maximum_width) * monitor.scale_factor;
    let physical_height = f64::from(maximum_height) * monitor.scale_factor;
    Ok((f64::from(monitor.work_area.width) / physical_width)
        .min(f64::from(monitor.work_area.height) / physical_height)
        .min(1.0))
}

fn fit_contract_to_work_area(
    contract: &LayoutContract,
    monitor: &MonitorDescriptor,
) -> Result<(f64, AnchorEnvelope), String> {
    let mut content_scale = content_scale_for_work_area(contract, monitor)?;
    for _ in 0..16 {
        let envelope = anchor_envelope(contract, monitor.scale_factor, content_scale)?;
        if envelope.left.saturating_add(envelope.right) <= monitor.work_area.width
            && envelope.top.saturating_add(envelope.bottom) <= monitor.work_area.height
        {
            return Ok((content_scale, envelope));
        }
        content_scale *= 0.995;
    }
    Err("layout envelope cannot fit inside target work area".to_string())
}

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

fn round_positive(value: f64) -> u32 {
    value.round().max(1.0).min(f64::from(u32::MAX)) as u32
}

fn round_nonnegative(value: f64) -> u32 {
    value.round().max(0.0).min(f64::from(u32::MAX)) as u32
}

fn anchor_envelope(
    contract: &LayoutContract,
    scale_factor: f64,
    content_scale: f64,
) -> Result<AnchorEnvelope, String> {
    let mut envelope = AnchorEnvelope {
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
    };
    for state in LayoutContract::all_values() {
        let scaled = scale_layout(contract.layout(state)?, scale_factor, content_scale);
        envelope.left = envelope.left.max(scaled.anchor[0]);
        envelope.right = envelope
            .right
            .max(scaled.size[0].saturating_sub(scaled.anchor[0]));
        envelope.top = envelope.top.max(scaled.anchor[1]);
        envelope.bottom = envelope
            .bottom
            .max(scaled.size[1].saturating_sub(scaled.anchor[1]));
    }
    Ok(envelope)
}

fn normalize_anchor(
    work_area: PhysicalRect,
    envelope: AnchorEnvelope,
    requested: Option<PhysicalPoint>,
) -> Result<PhysicalPoint, String> {
    let min_x = i64::from(work_area.x) + i64::from(envelope.left);
    let max_x = work_area.right() - i64::from(envelope.right);
    let min_y = i64::from(work_area.y) + i64::from(envelope.top);
    let max_y = work_area.bottom() - i64::from(envelope.bottom);
    if min_x > max_x || min_y > max_y {
        return Err("layout envelope cannot fit inside target work area".to_string());
    }
    let requested = requested.unwrap_or(PhysicalPoint {
        x: i32::try_from(max_x).map_err(|_| "default anchor x overflow".to_string())?,
        y: i32::try_from(max_y).map_err(|_| "default anchor y overflow".to_string())?,
    });
    Ok(PhysicalPoint {
        x: i32::try_from(i64::from(requested.x).clamp(min_x, max_x))
            .map_err(|_| "normalized anchor x overflow".to_string())?,
        y: i32::try_from(i64::from(requested.y).clamp(min_y, max_y))
            .map_err(|_| "normalized anchor y overflow".to_string())?,
    })
}

fn ensure_within_work_area(
    placement: PhysicalPlacement,
    work_area: PhysicalRect,
) -> Result<(), String> {
    let right = i64::from(placement.x) + i64::from(placement.width);
    let bottom = i64::from(placement.y) + i64::from(placement.height);
    if i64::from(placement.x) < i64::from(work_area.x)
        || i64::from(placement.y) < i64::from(work_area.y)
        || right > work_area.right()
        || bottom > work_area.bottom()
    {
        return Err("computed pet window placement escaped target work area".to_string());
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

    fn assert_inside(application: &LayoutApplication) {
        ensure_within_work_area(application.physical_placement, application.work_area)
            .expect("placement must stay inside work area");
    }

    #[test]
    fn shared_contract_defines_all_four_bounded_state_layouts() {
        let contract = contract();
        contract.validate().expect("contract should validate");
        let expected = [
            (PresentationState::Idle, [320, 420]),
            (PresentationState::Bubble, [736, 500]),
            (PresentationState::Composer, [736, 592]),
            (PresentationState::Expanded, [816, 680]),
        ];
        for (state, size) in expected {
            assert_eq!(contract.layout(state).unwrap().window_size, size);
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
            for (revision, state) in PresentationState::all().into_iter().enumerate() {
                let result =
                    apply_window_layout(&contract, state, revision as u64 + 1, &monitor, anchor)
                        .unwrap();
                if let Some(previous) = anchor {
                    assert_eq!(result.portrait_anchor, previous);
                }
                assert_inside(&result);
                anchor = Some(result.portrait_anchor);
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
        let anchor = PhysicalPoint { x: -520, y: 980 };
        assert_eq!(select_target_monitor(&monitors, anchor), Some(1));
        let result = apply_window_layout(
            &contract(),
            PresentationState::Expanded,
            1,
            &monitors[1],
            Some(anchor),
        )
        .unwrap();
        assert_eq!(result.portrait_anchor, anchor);
        assert_inside(&result);
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
    fn requested_anchor_near_each_edge_is_corrected_once_for_the_full_envelope() {
        let contract = contract();
        let monitor = monitor(
            PhysicalRect {
                x: -1280,
                y: 40,
                width: 1280,
                height: 984,
            },
            1.0,
        );
        for requested in [
            PhysicalPoint { x: -1280, y: 40 },
            PhysicalPoint { x: 0, y: 40 },
            PhysicalPoint { x: -1280, y: 1024 },
            PhysicalPoint { x: 0, y: 1024 },
        ] {
            let first = apply_window_layout(
                &contract,
                PresentationState::Idle,
                1,
                &monitor,
                Some(requested),
            )
            .unwrap();
            let anchor = first.portrait_anchor;
            for state in PresentationState::all() {
                let result =
                    apply_window_layout(&contract, state, 2, &monitor, Some(anchor)).unwrap();
                assert_eq!(result.portrait_anchor, anchor);
                assert_inside(&result);
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
            let result = apply_window_layout(&contract, state, 1, &monitor, anchor).unwrap();
            assert!(result.content_scale < 1.0);
            assert!(result.physical_placement.width <= monitor.work_area.width);
            assert!(result.physical_placement.height <= monitor.work_area.height);
            if let Some(previous) = anchor {
                assert_eq!(result.portrait_anchor, previous);
            }
            assert_inside(&result);
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
                PresentationState::Idle,
                1,
                &monitor(work_area, scale_factor),
                None,
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
}
