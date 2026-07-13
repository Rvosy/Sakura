use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use chrono::{Local, SecondsFormat};
use image::codecs::jpeg::JpegEncoder;
use image::imageops::{overlay, resize, FilterType};
use image::{ExtendedColorType, ImageEncoder, RgbaImage};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, State, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};
use uuid::Uuid;
use xcap::Monitor;

use crate::app_state::DesktopAppState;
use crate::brain_host::{BrainHostRequestError, BrainHostSupervisor};

const CAPTURE_WINDOW_LABEL: &str = "capture";
const CAPTURE_RESOURCE_TTL: Duration = Duration::from_secs(120);
const MAX_CAPTURE_BYTES: u64 = 24 * 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PhysicalRect {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

#[derive(Clone, Debug)]
struct MonitorDescriptor {
    id: u32,
    name: String,
    friendly_name: String,
    bounds: PhysicalRect,
    scale_factor: f32,
    primary: bool,
    monitor: Monitor,
}

#[derive(Clone, Debug)]
enum CaptureTarget {
    Primary,
    Fullscreen,
    Monitor(u32),
    Region(PhysicalRect),
}

#[derive(Clone, Copy, Debug)]
enum CaptureResolution {
    Original,
    MaxEdge(u32),
    Bounds(u32, u32),
}

#[derive(Clone, Debug)]
struct TemporaryCapture {
    path: PathBuf,
    created_at: Instant,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureMonitorDto {
    id: u32,
    name: String,
    friendly_name: String,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    scale_factor: f32,
    primary: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrivateCaptureResource {
    resource_id: String,
    path: PathBuf,
    mime_type: &'static str,
    width: u32,
    height: u32,
    captured_at: String,
    screen_name: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CapturePrototypeResult {
    width: u32,
    height: u32,
    byte_length: usize,
    monitor_count: usize,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureOverlayResult {
    capture_session_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProactiveCaptureRequest {
    capture_request_id: String,
    resolution: Option<String>,
    target: Option<CaptureTargetDto>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CaptureTargetDto {
    Primary,
    Fullscreen,
    Monitor { monitor_id: u32 },
}

pub struct CaptureManager {
    root: PathBuf,
    resources: Mutex<HashMap<String, TemporaryCapture>>,
    ttl: Duration,
}

impl CaptureManager {
    pub fn new(root: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&root).map_err(|error| error.to_string())?;
        let root = root.canonicalize().map_err(|error| error.to_string())?;
        Ok(Self {
            root,
            resources: Mutex::new(HashMap::new()),
            ttl: CAPTURE_RESOURCE_TTL,
        })
    }

    #[cfg(test)]
    fn with_ttl(root: PathBuf, ttl: Duration) -> Result<Self, String> {
        let mut manager = Self::new(root)?;
        manager.ttl = ttl;
        Ok(manager)
    }

    fn capture(
        &self,
        target: CaptureTarget,
        resolution: CaptureResolution,
    ) -> Result<PrivateCaptureResource, String> {
        self.cleanup_expired();
        let descriptors = monitor_descriptors()?;
        let (image, screen_name) = capture_target(&descriptors, target)?;
        let image = resize_capture(image, resolution);
        let bytes = encode_jpeg(&image)?;
        if bytes.len() as u64 > MAX_CAPTURE_BYTES {
            return Err("截图资源超过大小限制".into());
        }
        let resource_id = format!("capture-resource-{}", Uuid::new_v4().simple());
        let path = self.root.join(format!("{}.jpg", Uuid::new_v4().simple()));
        fs::write(&path, &bytes).map_err(|error| error.to_string())?;
        let canonical = path.canonicalize().map_err(|error| error.to_string())?;
        if !canonical.starts_with(&self.root) {
            let _ = fs::remove_file(&canonical);
            return Err("截图资源路径超出受控目录".into());
        }
        self.resources
            .lock()
            .expect("capture resource lock poisoned")
            .insert(
                resource_id.clone(),
                TemporaryCapture {
                    path: canonical.clone(),
                    created_at: Instant::now(),
                },
            );
        Ok(PrivateCaptureResource {
            resource_id,
            path: canonical,
            mime_type: "image/jpeg",
            width: image.width(),
            height: image.height(),
            captured_at: captured_at(),
            screen_name,
        })
    }

    pub fn release(&self, resource_id: &str) {
        let resource = self
            .resources
            .lock()
            .expect("capture resource lock poisoned")
            .remove(resource_id);
        if let Some(resource) = resource {
            let _ = fs::remove_file(resource.path);
        }
    }

    pub fn reset(&self) {
        let resources = {
            let mut resources = self
                .resources
                .lock()
                .expect("capture resource lock poisoned");
            resources.drain().map(|(_, item)| item).collect::<Vec<_>>()
        };
        for resource in resources {
            let _ = fs::remove_file(resource.path);
        }
    }

    fn cleanup_expired(&self) {
        self.cleanup_expired_at(Instant::now());
    }

    fn cleanup_expired_at(&self, now: Instant) {
        let expired = {
            let mut resources = self
                .resources
                .lock()
                .expect("capture resource lock poisoned");
            let ids = resources
                .iter()
                .filter_map(|(id, item)| {
                    now.checked_duration_since(item.created_at)
                        .filter(|age| *age >= self.ttl)
                        .map(|_| id.clone())
                })
                .collect::<Vec<_>>();
            ids.into_iter()
                .filter_map(|id| resources.remove(&id))
                .collect::<Vec<_>>()
        };
        for resource in expired {
            let _ = fs::remove_file(resource.path);
        }
    }
}

impl Drop for CaptureManager {
    fn drop(&mut self) {
        self.reset();
    }
}

#[tauri::command]
pub fn list_capture_monitors() -> Result<Vec<CaptureMonitorDto>, String> {
    monitor_descriptors().map(|monitors| {
        monitors
            .into_iter()
            .map(|monitor| CaptureMonitorDto {
                id: monitor.id,
                name: monitor.name,
                friendly_name: monitor.friendly_name,
                x: monitor.bounds.x,
                y: monitor.bounds.y,
                width: monitor.bounds.width,
                height: monitor.bounds.height,
                scale_factor: monitor.scale_factor,
                primary: monitor.primary,
            })
            .collect()
    })
}

#[tauri::command]
pub fn open_capture_overlay(
    app: AppHandle,
    state: State<'_, DesktopAppState>,
) -> Result<CaptureOverlayResult, BrainHostRequestError> {
    let response = state.request("observation.capture_started", json!({}))?;
    let capture_session_id = response
        .get("captureSessionId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| BrainHostRequestError::transport("Brain 未返回截图会话 ID"))?
        .to_string();
    if let Err(error) = show_capture_overlay(&app, &capture_session_id) {
        let _ = state.request(
            "observation.capture_cancelled",
            json!({"captureSessionId": capture_session_id}),
        );
        return Err(BrainHostRequestError::transport(error));
    }
    Ok(CaptureOverlayResult { capture_session_id })
}

#[tauri::command]
pub async fn capture_selected_region(
    window: WebviewWindow,
    state: State<'_, DesktopAppState>,
    capture_session_id: String,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<Value, String> {
    let rect = selection_to_physical(&window, x, y, width, height)?;
    let _ = window.hide();
    let manager = state.capture_manager();
    let brain = state.brain();
    let app = window.app_handle().clone();
    let capture_window = window.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        std::thread::sleep(Duration::from_millis(80));
        push_capture_to_brain(
            &manager,
            &brain,
            CaptureTarget::Region(rect),
            CaptureResolution::MaxEdge(1280),
            json!({
                "source": "manual",
                "captureSessionId": capture_session_id,
            }),
        )
    })
    .await
    .map_err(|error| error.to_string())?;
    match result {
        Ok(payload) => {
            let _ = app.emit_to("main", "sakura://manual-observation-ready", payload.clone());
            let _ = capture_window.close();
            Ok(payload)
        }
        Err(error) => {
            let _ = app.emit_to(
                "main",
                "sakura://manual-observation-error",
                json!({"message": error}),
            );
            let _ = capture_window.show();
            Err(error)
        }
    }
}

#[tauri::command]
pub fn cancel_capture_overlay(
    window: WebviewWindow,
    state: State<'_, DesktopAppState>,
    capture_session_id: String,
) -> Result<Value, BrainHostRequestError> {
    let result = state.request(
        "observation.capture_cancelled",
        json!({"captureSessionId": capture_session_id}),
    )?;
    let _ = window
        .app_handle()
        .emit_to("main", "sakura://manual-observation-cancelled", json!({}));
    let _ = window.close();
    Ok(result)
}

#[tauri::command]
pub async fn capture_screen_prototype() -> Result<CapturePrototypeResult, String> {
    tauri::async_runtime::spawn_blocking(capture_primary_monitor)
        .await
        .map_err(|error| error.to_string())?
}

pub fn handle_proactive_capture(
    manager: Arc<CaptureManager>,
    brain: Arc<BrainHostSupervisor>,
    payload: Value,
    app: AppHandle,
) {
    std::thread::spawn(move || {
        let request: Result<ProactiveCaptureRequest, _> = serde_json::from_value(payload);
        let request = match request {
            Ok(request) if !request.capture_request_id.is_empty() => request,
            _ => return,
        };
        let target = match request.target.unwrap_or(CaptureTargetDto::Fullscreen) {
            CaptureTargetDto::Primary => CaptureTarget::Primary,
            CaptureTargetDto::Fullscreen => CaptureTarget::Fullscreen,
            CaptureTargetDto::Monitor { monitor_id } => CaptureTarget::Monitor(monitor_id),
        };
        let resolution = capture_resolution(request.resolution.as_deref(), false);
        let result = push_capture_to_brain(
            &manager,
            &brain,
            target,
            resolution,
            json!({
                "source": "screen_awareness",
                "captureRequestId": request.capture_request_id,
            }),
        );
        if let Err(error) = result {
            let _ = brain.request(
                "observation.capture_failed",
                json!({"captureRequestId": request.capture_request_id}),
                Duration::from_secs(5),
            );
            let _ = app.emit(
                "sakura://manual-observation-error",
                json!({"message": format!("主动截图失败：{error}")}),
            );
        }
    });
}

fn push_capture_to_brain(
    manager: &CaptureManager,
    brain: &BrainHostSupervisor,
    target: CaptureTarget,
    resolution: CaptureResolution,
    mut payload: Value,
) -> Result<Value, String> {
    let resource = manager.capture(target, resolution)?;
    let resource_id = resource.resource_id.clone();
    payload["resource"] = serde_json::to_value(&resource).map_err(|error| error.to_string())?;
    let result = brain
        .request("observation.push", payload, Duration::from_secs(10))
        .map_err(|error| error.message);
    manager.release(&resource_id);
    result
}

fn show_capture_overlay(app: &AppHandle, capture_session_id: &str) -> Result<(), String> {
    if let Some(existing) = app.get_webview_window(CAPTURE_WINDOW_LABEL) {
        let _ = existing.close();
    }
    let bounds = virtual_bounds(&monitor_descriptors()?)
        .ok_or_else(|| "未检测到可截图的显示器".to_string())?;
    let url = WebviewUrl::App(format!("capture.html?captureSessionId={capture_session_id}").into());
    let window = WebviewWindowBuilder::new(app, CAPTURE_WINDOW_LABEL, url)
        .title("Sakura Capture")
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .maximizable(false)
        .minimizable(false)
        .closable(false)
        .visible(false)
        .build()
        .map_err(|error| error.to_string())?;
    window
        .set_position(PhysicalPosition::new(bounds.x, bounds.y))
        .map_err(|error| error.to_string())?;
    window
        .set_size(PhysicalSize::new(bounds.width, bounds.height))
        .map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

fn selection_to_physical(
    window: &WebviewWindow,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<PhysicalRect, String> {
    if !x.is_finite()
        || !y.is_finite()
        || !width.is_finite()
        || !height.is_finite()
        || width < 1.0
        || height < 1.0
    {
        return Err("截图区域无效".into());
    }
    let origin = window.outer_position().map_err(|error| error.to_string())?;
    let scale = window.scale_factor().map_err(|error| error.to_string())?;
    Ok(PhysicalRect {
        x: origin.x.saturating_add((x * scale).round() as i32),
        y: origin.y.saturating_add((y * scale).round() as i32),
        width: (width * scale).round().max(1.0) as u32,
        height: (height * scale).round().max(1.0) as u32,
    })
}

fn capture_primary_monitor() -> Result<CapturePrototypeResult, String> {
    let monitors = monitor_descriptors()?;
    let primary_index = select_primary_index(
        &monitors
            .iter()
            .map(|monitor| monitor.primary)
            .collect::<Vec<_>>(),
    )
    .ok_or_else(|| "未检测到可截图的显示器".to_string())?;
    let image = monitors[primary_index]
        .monitor
        .capture_image()
        .map_err(|error| error.to_string())?;
    Ok(CapturePrototypeResult {
        width: image.width(),
        height: image.height(),
        byte_length: image.as_raw().len(),
        monitor_count: monitors.len(),
    })
}

fn monitor_descriptors() -> Result<Vec<MonitorDescriptor>, String> {
    Monitor::all()
        .map_err(|error| error.to_string())?
        .into_iter()
        .map(|monitor| {
            let id = monitor.id().map_err(|error| error.to_string())?;
            let name = monitor.name().unwrap_or_else(|_| format!("monitor-{id}"));
            let friendly_name = monitor.friendly_name().unwrap_or_else(|_| name.clone());
            Ok(MonitorDescriptor {
                id,
                name,
                friendly_name,
                bounds: PhysicalRect {
                    x: monitor.x().map_err(|error| error.to_string())?,
                    y: monitor.y().map_err(|error| error.to_string())?,
                    width: monitor.width().map_err(|error| error.to_string())?,
                    height: monitor.height().map_err(|error| error.to_string())?,
                },
                scale_factor: monitor.scale_factor().unwrap_or(1.0),
                primary: monitor.is_primary().unwrap_or(false),
                monitor,
            })
        })
        .collect()
}

fn capture_target(
    monitors: &[MonitorDescriptor],
    target: CaptureTarget,
) -> Result<(RgbaImage, String), String> {
    match target {
        CaptureTarget::Primary => {
            let index = select_primary_index(
                &monitors
                    .iter()
                    .map(|monitor| monitor.primary)
                    .collect::<Vec<_>>(),
            )
            .ok_or_else(|| "未检测到可截图的显示器".to_string())?;
            let monitor = &monitors[index];
            Ok((
                monitor
                    .monitor
                    .capture_image()
                    .map_err(|error| error.to_string())?,
                monitor.name.clone(),
            ))
        }
        CaptureTarget::Monitor(id) => {
            let index = select_monitor_index(
                &monitors
                    .iter()
                    .map(|monitor| monitor.id)
                    .collect::<Vec<_>>(),
                id,
            )
            .ok_or_else(|| "指定显示器不存在".to_string())?;
            let monitor = &monitors[index];
            Ok((
                monitor
                    .monitor
                    .capture_image()
                    .map_err(|error| error.to_string())?,
                monitor.name.clone(),
            ))
        }
        CaptureTarget::Fullscreen => {
            let bounds =
                virtual_bounds(monitors).ok_or_else(|| "未检测到可截图的显示器".to_string())?;
            capture_region(monitors, bounds).map(|image| (image, "virtual-desktop".into()))
        }
        CaptureTarget::Region(rect) => {
            capture_region(monitors, rect).map(|image| (image, "manual-selection".into()))
        }
    }
}

fn capture_region(
    monitors: &[MonitorDescriptor],
    requested: PhysicalRect,
) -> Result<RgbaImage, String> {
    let virtual_desktop =
        virtual_bounds(monitors).ok_or_else(|| "未检测到可截图的显示器".to_string())?;
    let requested = intersection(requested, virtual_desktop)
        .ok_or_else(|| "截图区域不在任何显示器内".to_string())?;
    let mut output = RgbaImage::new(requested.width, requested.height);
    let mut captured_any = false;
    for monitor in monitors {
        let Some(piece) = intersection(requested, monitor.bounds) else {
            continue;
        };
        let local_x = piece.x.saturating_sub(monitor.bounds.x) as u32;
        let local_y = piece.y.saturating_sub(monitor.bounds.y) as u32;
        let image = monitor
            .monitor
            .capture_region(local_x, local_y, piece.width, piece.height)
            .map_err(|error| error.to_string())?;
        let destination_x = piece.x.saturating_sub(requested.x) as i64;
        let destination_y = piece.y.saturating_sub(requested.y) as i64;
        overlay(&mut output, &image, destination_x, destination_y);
        captured_any = true;
    }
    if !captured_any {
        return Err("截图区域不在任何显示器内".into());
    }
    Ok(output)
}

fn resize_capture(image: RgbaImage, resolution: CaptureResolution) -> RgbaImage {
    let (max_width, max_height) = match resolution {
        CaptureResolution::Original => return image,
        CaptureResolution::MaxEdge(edge) => (edge, edge),
        CaptureResolution::Bounds(width, height) => {
            if image.height() > image.width() {
                (height, width)
            } else {
                (width, height)
            }
        }
    };
    let scale = f64::min(
        1.0,
        f64::min(
            max_width as f64 / image.width().max(1) as f64,
            max_height as f64 / image.height().max(1) as f64,
        ),
    );
    if scale >= 1.0 {
        return image;
    }
    let width = (image.width() as f64 * scale).round().max(1.0) as u32;
    let height = (image.height() as f64 * scale).round().max(1.0) as u32;
    resize(&image, width, height, FilterType::Lanczos3)
}

fn encode_jpeg(image: &RgbaImage) -> Result<Vec<u8>, String> {
    let rgb = image::DynamicImage::ImageRgba8(image.clone()).to_rgb8();
    let mut bytes = Vec::new();
    JpegEncoder::new_with_quality(&mut bytes, 70)
        .write_image(
            rgb.as_raw(),
            rgb.width(),
            rgb.height(),
            ExtendedColorType::Rgb8,
        )
        .map_err(|error| error.to_string())?;
    Ok(bytes)
}

fn capture_resolution(value: Option<&str>, manual: bool) -> CaptureResolution {
    if manual {
        return CaptureResolution::MaxEdge(1280);
    }
    match value
        .unwrap_or("fullscreen")
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "720p" => CaptureResolution::Bounds(1280, 720),
        "1080p" => CaptureResolution::Bounds(1920, 1080),
        "2160p" => CaptureResolution::Bounds(3840, 2160),
        _ => CaptureResolution::Original,
    }
}

fn virtual_bounds(monitors: &[MonitorDescriptor]) -> Option<PhysicalRect> {
    bounds_union(
        &monitors
            .iter()
            .map(|monitor| monitor.bounds)
            .collect::<Vec<_>>(),
    )
}

fn bounds_union(bounds: &[PhysicalRect]) -> Option<PhysicalRect> {
    let min_x = bounds.iter().map(|bounds| bounds.x).min()?;
    let min_y = bounds.iter().map(|bounds| bounds.y).min()?;
    let max_x = bounds
        .iter()
        .map(|bounds| bounds.x as i64 + bounds.width as i64)
        .max()?;
    let max_y = bounds
        .iter()
        .map(|bounds| bounds.y as i64 + bounds.height as i64)
        .max()?;
    Some(PhysicalRect {
        x: min_x,
        y: min_y,
        width: (max_x - min_x as i64).max(1).min(u32::MAX as i64) as u32,
        height: (max_y - min_y as i64).max(1).min(u32::MAX as i64) as u32,
    })
}

fn intersection(left: PhysicalRect, right: PhysicalRect) -> Option<PhysicalRect> {
    let x1 = i64::from(left.x).max(i64::from(right.x));
    let y1 = i64::from(left.y).max(i64::from(right.y));
    let x2 = (i64::from(left.x) + i64::from(left.width))
        .min(i64::from(right.x) + i64::from(right.width));
    let y2 = (i64::from(left.y) + i64::from(left.height))
        .min(i64::from(right.y) + i64::from(right.height));
    if x2 <= x1 || y2 <= y1 {
        return None;
    }
    Some(PhysicalRect {
        x: x1 as i32,
        y: y1 as i32,
        width: (x2 - x1) as u32,
        height: (y2 - y1) as u32,
    })
}

fn captured_at() -> String {
    Local::now().to_rfc3339_opts(SecondsFormat::Secs, false)
}

fn select_primary_index(primary_flags: &[bool]) -> Option<usize> {
    primary_flags
        .iter()
        .position(|is_primary| *is_primary)
        .or_else(|| (!primary_flags.is_empty()).then_some(0))
}

fn select_monitor_index(monitor_ids: &[u32], requested_id: u32) -> Option<usize> {
    monitor_ids.iter().position(|id| *id == requested_id)
}

#[cfg(test)]
mod tests {
    use tempfile::TempDir;

    use super::*;

    #[test]
    fn selects_primary_monitor_or_first_fallback() {
        assert_eq!(select_primary_index(&[false, true, false]), Some(1));
        assert_eq!(select_primary_index(&[false, false]), Some(0));
        assert_eq!(select_primary_index(&[]), None);
        assert_eq!(select_monitor_index(&[4, 7, 9], 7), Some(1));
        assert_eq!(select_monitor_index(&[4, 7, 9], 8), None);
    }

    #[test]
    fn capture_geometry_clamps_region_and_splits_across_monitors() {
        let left = PhysicalRect {
            x: -1280,
            y: 0,
            width: 1280,
            height: 1024,
        };
        let right = PhysicalRect {
            x: 0,
            y: 0,
            width: 1920,
            height: 1080,
        };
        assert_eq!(
            bounds_union(&[left, right]),
            Some(PhysicalRect {
                x: -1280,
                y: 0,
                width: 3200,
                height: 1080,
            })
        );
        let requested = PhysicalRect {
            x: -100,
            y: 900,
            width: 300,
            height: 300,
        };
        let desktop = PhysicalRect {
            x: -1280,
            y: 0,
            width: 3200,
            height: 1080,
        };
        assert_eq!(
            intersection(requested, desktop),
            Some(PhysicalRect {
                x: -100,
                y: 900,
                width: 300,
                height: 180,
            })
        );
        assert_eq!(
            intersection(requested, left),
            Some(PhysicalRect {
                x: -100,
                y: 900,
                width: 100,
                height: 124,
            })
        );
        assert_eq!(
            intersection(requested, right),
            Some(PhysicalRect {
                x: 0,
                y: 900,
                width: 200,
                height: 180,
            })
        );
    }

    #[test]
    fn capture_resize_preserves_aspect_ratio_without_upscaling() {
        let image = RgbaImage::new(2560, 1440);
        assert_eq!(
            resize_capture(image.clone(), CaptureResolution::MaxEdge(1280)).dimensions(),
            (1280, 720)
        );
        assert_eq!(
            resize_capture(image, CaptureResolution::Bounds(1920, 1080)).dimensions(),
            (1920, 1080)
        );
        assert_eq!(
            resize_capture(
                RgbaImage::new(640, 360),
                CaptureResolution::Bounds(1280, 720)
            )
            .dimensions(),
            (640, 360)
        );
    }

    #[test]
    fn capture_resources_are_private_ttl_bound_and_resettable() {
        let temp = TempDir::new().unwrap();
        let manager =
            CaptureManager::with_ttl(temp.path().join("captures"), Duration::from_millis(1))
                .unwrap();
        let path = manager.root.join("fixture.jpg");
        fs::write(&path, b"fixture").unwrap();
        manager.resources.lock().unwrap().insert(
            "resource-1".into(),
            TemporaryCapture {
                path: path.clone(),
                created_at: Instant::now() - Duration::from_secs(1),
            },
        );
        manager.cleanup_expired_at(Instant::now());
        assert!(!path.exists());

        let path = manager.root.join("fixture-2.jpg");
        fs::write(&path, b"fixture").unwrap();
        manager.resources.lock().unwrap().insert(
            "resource-2".into(),
            TemporaryCapture {
                path: path.clone(),
                created_at: Instant::now(),
            },
        );
        manager.reset();
        assert!(!path.exists());
        assert!(manager.resources.lock().unwrap().is_empty());
    }

    #[test]
    fn public_capture_dtos_do_not_contain_private_paths() {
        let dto = CaptureMonitorDto {
            id: 1,
            name: "DISPLAY1".into(),
            friendly_name: "Display 1".into(),
            x: 0,
            y: 0,
            width: 1920,
            height: 1080,
            scale_factor: 1.0,
            primary: true,
        };
        let serialized = serde_json::to_string(&dto).unwrap();
        assert!(!serialized.contains("path"));
        assert!(!serialized.contains("resource"));
    }
}
