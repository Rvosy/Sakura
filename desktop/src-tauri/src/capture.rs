//! Generation-private manual screen capture resources and per-monitor overlay windows.

use std::{
    collections::HashMap,
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::Mutex,
    time::{Duration, Instant},
};

use image::{codecs::jpeg::JpegEncoder, imageops::FilterType, ExtendedColorType, ImageEncoder};
use serde::{Deserialize, Serialize};
use tauri::{
    webview::{Color, WebviewBuilder},
    window::WindowBuilder,
    AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use uuid::Uuid;
use xcap::Monitor;

pub const ATTACHED_EVENT: &str = "sakura://screen-attachment";
pub const CANCELLED_EVENT: &str = "sakura://screen-capture-cancelled";
pub const ERROR_EVENT: &str = "sakura://screen-capture-error";
const RESOURCE_DIRECTORY: &str = "sakura-runtime-v2-screen-resources";
const RESOURCE_TTL: Duration = Duration::from_secs(120);
const MAX_CAPTURE_BYTES: usize = 24 * 1024 * 1024;
const MAX_CAPTURE_PIXELS: u64 = 32_000_000;
const MAX_CAPTURE_EDGE: u32 = 1280;
const MIN_SELECTION_LOGICAL_PX: f64 = 8.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PhysicalRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug)]
pub struct CaptureMonitor {
    pub id: u32,
    pub name: String,
    pub bounds: PhysicalRect,
    pub primary: bool,
}

#[derive(Clone, Debug)]
struct CaptureSession {
    id: String,
    generation_id: String,
    character_session_id: String,
    capture_revision: u64,
    windows: HashMap<String, u32>,
}

#[derive(Clone, Debug)]
struct CaptureResource {
    path: PathBuf,
    generation_id: String,
    created_at: Instant,
}

#[derive(Clone, Debug)]
pub struct CaptureClaim {
    pub generation_id: String,
    pub character_session_id: String,
    pub capture_revision: u64,
    pub monitor_id: u32,
    pub window_labels: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenResourceDescriptor {
    pub generation_id: String,
    pub resource_token: String,
    pub mime_type: &'static str,
    pub width: u32,
    pub height: u32,
    pub byte_length: usize,
    pub captured_at: String,
    pub screen_name: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenAttachmentPublication {
    pub capture_revision: u64,
    pub attachment_id: String,
    pub item_id: String,
    pub width: u32,
    pub height: u32,
    pub count: usize,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenAttachmentItemRemovePublication {
    pub accepted: bool,
    pub attachment_id: String,
    pub item_id: String,
    pub count: usize,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CaptureStartRequest {
    pub capture_revision: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CaptureSelectionRequest {
    pub session_id: String,
    pub monitor_id: u32,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CaptureCancelRequest {
    pub session_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AttachmentReleaseRequest {
    pub attachment_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AttachmentItemRemoveRequest {
    pub attachment_id: String,
    pub item_id: String,
}

pub struct CaptureManager {
    base_root: PathBuf,
    available: bool,
    state: Mutex<CaptureState>,
}

pub fn valid_attachment_id(value: &str) -> bool {
    value.strip_prefix("screen-").is_some_and(|token| {
        token.len() == 32
            && token
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

pub fn valid_attachment_item_id(value: &str) -> bool {
    value.strip_prefix("shot-").is_some_and(|token| {
        token.len() == 32
            && token
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

#[derive(Default)]
struct CaptureState {
    active: Option<CaptureSession>,
    resources: HashMap<String, CaptureResource>,
    active_generation: Option<String>,
}

impl CaptureManager {
    pub fn new() -> Self {
        let base_root = std::env::temp_dir().join(RESOURCE_DIRECTORY);
        Self::with_base(base_root.clone()).unwrap_or(Self {
            base_root,
            available: false,
            state: Mutex::new(CaptureState::default()),
        })
    }

    fn with_base(base_root: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&base_root).map_err(|_| "SCREEN_RESOURCE_ROOT_UNAVAILABLE")?;
        restrict_directory(&base_root)?;
        let base_root = base_root
            .canonicalize()
            .map_err(|_| "SCREEN_RESOURCE_ROOT_UNAVAILABLE")?;
        Ok(Self {
            base_root,
            available: true,
            state: Mutex::new(CaptureState::default()),
        })
    }

    pub fn begin_session(
        &self,
        generation_id: &str,
        character_session_id: &str,
        capture_revision: u64,
        monitors: &[CaptureMonitor],
    ) -> Result<(String, Vec<String>, Vec<String>), String> {
        if !self.available {
            return Err("SCREEN_RESOURCE_ROOT_UNAVAILABLE".to_string());
        }
        validate_generation(generation_id)?;
        if monitors.is_empty() {
            return Err("SCREEN_CAPTURE_NO_MONITORS".to_string());
        }
        self.cleanup_expired();
        let mut state = self
            .state
            .lock()
            .map_err(|_| "SCREEN_CAPTURE_STATE_UNAVAILABLE".to_string())?;
        let previous = state
            .active
            .take()
            .map(|session| session.windows.into_keys().collect())
            .unwrap_or_default();
        if state.active_generation.as_deref() != Some(generation_id) {
            cleanup_resources(&mut state.resources);
            state.active_generation = Some(generation_id.to_string());
        }
        let session_id = Uuid::new_v4().simple().to_string();
        let mut windows = HashMap::new();
        let mut labels = Vec::with_capacity(monitors.len());
        for (index, monitor) in monitors.iter().enumerate() {
            let label = format!("capture-{}-{index}", &session_id[..12]);
            windows.insert(label.clone(), monitor.id);
            labels.push(label);
        }
        state.active = Some(CaptureSession {
            id: session_id.clone(),
            generation_id: generation_id.to_string(),
            character_session_id: character_session_id.to_string(),
            capture_revision,
            windows,
        });
        Ok((session_id, labels, previous))
    }

    pub fn claim_selection(
        &self,
        session_id: &str,
        window_label: &str,
        monitor_id: u32,
    ) -> Result<CaptureClaim, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "SCREEN_CAPTURE_STATE_UNAVAILABLE".to_string())?;
        let session = state
            .active
            .take()
            .ok_or_else(|| "SCREEN_CAPTURE_SESSION_STALE".to_string())?;
        if session.id != session_id
            || session.windows.get(window_label).copied() != Some(monitor_id)
        {
            state.active = Some(session);
            return Err("SCREEN_CAPTURE_SESSION_STALE".to_string());
        }
        Ok(CaptureClaim {
            generation_id: session.generation_id,
            character_session_id: session.character_session_id,
            capture_revision: session.capture_revision,
            monitor_id,
            window_labels: session.windows.into_keys().collect(),
        })
    }

    pub fn cancel_session(
        &self,
        session_id: &str,
        window_label: &str,
    ) -> Option<(u64, Vec<String>)> {
        let mut state = self.state.lock().ok()?;
        let matches = state.active.as_ref().is_some_and(|session| {
            session.id == session_id && session.windows.contains_key(window_label)
        });
        matches.then(|| {
            let session = state.active.take().expect("matched session exists");
            (
                session.capture_revision,
                session.windows.into_keys().collect(),
            )
        })
    }

    pub fn capture(
        &self,
        claim: &CaptureClaim,
        local_rect: PhysicalRect,
    ) -> Result<ScreenResourceDescriptor, String> {
        self.cleanup_expired();
        let monitor = Monitor::all()
            .map_err(|_| "SCREEN_CAPTURE_PLATFORM_UNAVAILABLE".to_string())?
            .into_iter()
            .find(|monitor| monitor.id().ok() == Some(claim.monitor_id))
            .ok_or_else(|| "SCREEN_CAPTURE_MONITOR_GONE".to_string())?;
        let monitor_width = monitor
            .width()
            .map_err(|_| "SCREEN_CAPTURE_MONITOR_GONE".to_string())?;
        let monitor_height = monitor
            .height()
            .map_err(|_| "SCREEN_CAPTURE_MONITOR_GONE".to_string())?;
        let rect = intersect_local(local_rect, monitor_width, monitor_height)
            .ok_or_else(|| "SCREEN_CAPTURE_SELECTION_INVALID".to_string())?;
        let image = monitor
            .capture_region(rect.x as u32, rect.y as u32, rect.width, rect.height)
            .map_err(|_| "SCREEN_CAPTURE_PLATFORM_DENIED".to_string())?;
        let image = resize_capture(image);
        let rgb = image::DynamicImage::ImageRgba8(image).to_rgb8();
        let mut bytes = Vec::new();
        JpegEncoder::new_with_quality(&mut bytes, 70)
            .write_image(
                rgb.as_raw(),
                rgb.width(),
                rgb.height(),
                ExtendedColorType::Rgb8,
            )
            .map_err(|_| "SCREEN_CAPTURE_ENCODE_FAILED".to_string())?;
        if bytes.is_empty() || bytes.len() > MAX_CAPTURE_BYTES {
            return Err("SCREEN_CAPTURE_RESOURCE_LIMIT".to_string());
        }
        let root = self.generation_root(&claim.generation_id)?;
        let token = Uuid::new_v4().simple().to_string();
        let path = root.join(format!("{token}.jpg"));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|_| "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string())?;
        restrict_file(&path)?;
        if file.write_all(&bytes).and_then(|_| file.flush()).is_err() {
            let _ = fs::remove_file(&path);
            return Err("SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string());
        }
        let canonical = path
            .canonicalize()
            .map_err(|_| "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string())?;
        if canonical.parent() != Some(root.as_path()) {
            let _ = fs::remove_file(&canonical);
            return Err("SCREEN_CAPTURE_RESOURCE_ESCAPE".to_string());
        }
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => {
                let _ = fs::remove_file(&canonical);
                return Err("SCREEN_CAPTURE_STATE_UNAVAILABLE".to_string());
            }
        };
        state.resources.insert(
            token.clone(),
            CaptureResource {
                path: canonical,
                generation_id: claim.generation_id.clone(),
                created_at: Instant::now(),
            },
        );
        Ok(ScreenResourceDescriptor {
            generation_id: claim.generation_id.clone(),
            resource_token: token,
            mime_type: "image/jpeg",
            width: rgb.width(),
            height: rgb.height(),
            byte_length: bytes.len(),
            captured_at: OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_string()),
            screen_name: monitor
                .name()
                .ok()
                .filter(|name| !name.trim().is_empty())
                .unwrap_or_else(|| format!("monitor-{}", claim.monitor_id))
                .chars()
                .take(128)
                .collect(),
        })
    }

    pub fn release(&self, token: &str, generation_id: &str) {
        let resource = self.state.lock().ok().and_then(|mut state| {
            let matches = state
                .resources
                .get(token)
                .is_some_and(|resource| resource.generation_id == generation_id);
            matches.then(|| state.resources.remove(token)).flatten()
        });
        if let Some(resource) = resource {
            let _ = fs::remove_file(resource.path);
        }
    }

    pub fn capture_host_frame(
        &self,
        generation_id: &str,
        session_id: &str,
        cursor_x: i32,
        cursor_y: i32,
        resolution: &str,
    ) -> Result<ScreenResourceDescriptor, String> {
        if !self.available {
            return Err("SCREEN_RESOURCE_ROOT_UNAVAILABLE".to_string());
        }
        validate_generation(generation_id)?;
        if session_id.is_empty() || !valid_capture_resolution(resolution) {
            return Err("SCREEN_CAPTURE_REQUEST_INVALID".to_string());
        }
        let monitor = Monitor::from_point(cursor_x, cursor_y)
            .map_err(|_| "SCREEN_CAPTURE_MONITOR_GONE".to_string())?;
        let image = monitor
            .capture_image()
            .map_err(|_| "SCREEN_CAPTURE_PLATFORM_DENIED".to_string())?;
        let screen_name = monitor
            .name()
            .ok()
            .filter(|name| !name.trim().is_empty())
            .unwrap_or_else(|| "monitor".to_string())
            .chars()
            .take(128)
            .collect();
        self.publish_host_image(generation_id, image, resolution, screen_name)
    }

    fn publish_host_image(
        &self,
        generation_id: &str,
        image: image::RgbaImage,
        resolution: &str,
        screen_name: String,
    ) -> Result<ScreenResourceDescriptor, String> {
        self.cleanup_expired();
        let image = resize_host_capture(image, resolution);
        if u64::from(image.width()) * u64::from(image.height()) > MAX_CAPTURE_PIXELS {
            return Err("SCREEN_CAPTURE_RESOURCE_LIMIT".to_string());
        }
        let rgb = image::DynamicImage::ImageRgba8(image).to_rgb8();
        let mut bytes = Vec::new();
        JpegEncoder::new_with_quality(&mut bytes, 70)
            .write_image(
                rgb.as_raw(),
                rgb.width(),
                rgb.height(),
                ExtendedColorType::Rgb8,
            )
            .map_err(|_| "SCREEN_CAPTURE_ENCODE_FAILED".to_string())?;
        if bytes.is_empty() || bytes.len() > MAX_CAPTURE_BYTES {
            return Err("SCREEN_CAPTURE_RESOURCE_LIMIT".to_string());
        }
        let root = self.generation_root(generation_id)?;
        let token = Uuid::new_v4().simple().to_string();
        let path = root.join(format!("{token}.jpg"));
        let result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .map_err(|_| "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string())?;
            restrict_file(&path)?;
            file.write_all(&bytes)
                .and_then(|_| file.flush())
                .map_err(|_| "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string())?;
            let canonical = path
                .canonicalize()
                .map_err(|_| "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string())?;
            if canonical.parent() != Some(root.as_path()) {
                return Err("SCREEN_CAPTURE_RESOURCE_ESCAPE".to_string());
            }
            let mut state = self
                .state
                .lock()
                .map_err(|_| "SCREEN_CAPTURE_STATE_UNAVAILABLE".to_string())?;
            // Only temporary publication handles live here. Batch selection and
            // resource ownership belong to the plugin and the Core Host service.
            if state.resources.len() >= 64 {
                return Err("SCREEN_CAPTURE_RESOURCE_LIMIT".to_string());
            }
            state.resources.insert(
                token.clone(),
                CaptureResource {
                    path: canonical,
                    generation_id: generation_id.to_string(),
                    created_at: Instant::now(),
                },
            );
            Ok(ScreenResourceDescriptor {
                generation_id: generation_id.to_string(),
                resource_token: token,
                mime_type: "image/jpeg",
                width: rgb.width(),
                height: rgb.height(),
                byte_length: bytes.len(),
                captured_at: OffsetDateTime::now_utc()
                    .format(&Rfc3339)
                    .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_string()),
                screen_name,
            })
        })();
        if result.is_err() {
            let _ = fs::remove_file(&path);
        }
        result
    }

    pub fn release_descriptors(
        &self,
        descriptors: &[ScreenResourceDescriptor],
        generation_id: &str,
    ) {
        for descriptor in descriptors {
            self.release(&descriptor.resource_token, generation_id);
        }
    }

    fn generation_root(&self, generation_id: &str) -> Result<PathBuf, String> {
        validate_generation(generation_id)?;
        let root = self.base_root.join(generation_id);
        fs::create_dir_all(&root).map_err(|_| "SCREEN_RESOURCE_ROOT_UNAVAILABLE".to_string())?;
        restrict_directory(&root)?;
        let root = root
            .canonicalize()
            .map_err(|_| "SCREEN_RESOURCE_ROOT_UNAVAILABLE".to_string())?;
        if root.parent() != Some(self.base_root.as_path()) {
            return Err("SCREEN_CAPTURE_RESOURCE_ESCAPE".to_string());
        }
        Ok(root)
    }

    fn cleanup_expired(&self) {
        let expired = self.state.lock().ok().map(|mut state| {
            let now = Instant::now();
            let tokens = state
                .resources
                .iter()
                .filter(|(_, resource)| {
                    now.checked_duration_since(resource.created_at)
                        .is_some_and(|age| age >= RESOURCE_TTL)
                })
                .map(|(token, _)| token.clone())
                .collect::<Vec<_>>();
            tokens
                .into_iter()
                .filter_map(|token| state.resources.remove(&token))
                .collect::<Vec<_>>()
        });
        for resource in expired.unwrap_or_default() {
            let _ = fs::remove_file(resource.path);
        }
    }
}

impl Drop for CaptureManager {
    fn drop(&mut self) {
        if let Ok(state) = self.state.get_mut() {
            cleanup_resources(&mut state.resources);
        }
    }
}

pub fn monitor_descriptors() -> Result<Vec<CaptureMonitor>, String> {
    Monitor::all()
        .map_err(|_| "SCREEN_CAPTURE_PLATFORM_UNAVAILABLE".to_string())?
        .into_iter()
        .map(|monitor| {
            let id = monitor
                .id()
                .map_err(|_| "SCREEN_CAPTURE_MONITOR_INVALID".to_string())?;
            Ok(CaptureMonitor {
                id,
                name: monitor.name().unwrap_or_else(|_| format!("monitor-{id}")),
                bounds: PhysicalRect {
                    x: monitor
                        .x()
                        .map_err(|_| "SCREEN_CAPTURE_MONITOR_INVALID".to_string())?,
                    y: monitor
                        .y()
                        .map_err(|_| "SCREEN_CAPTURE_MONITOR_INVALID".to_string())?,
                    width: monitor
                        .width()
                        .map_err(|_| "SCREEN_CAPTURE_MONITOR_INVALID".to_string())?,
                    height: monitor
                        .height()
                        .map_err(|_| "SCREEN_CAPTURE_MONITOR_INVALID".to_string())?,
                },
                primary: monitor.is_primary().unwrap_or(false),
            })
        })
        .collect()
}

pub fn show_overlays(
    app: &AppHandle,
    session_id: &str,
    labels: &[String],
    monitors: &[CaptureMonitor],
    theme_primary: &str,
) -> Result<(), String> {
    if labels.len() != monitors.len() {
        return Err("SCREEN_CAPTURE_OVERLAY_INVALID".to_string());
    }
    let creation_scale = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| monitor.scale_factor())
        .filter(|scale| scale.is_finite() && *scale > 0.0)
        .unwrap_or(1.0);
    let mut created = Vec::new();
    for (label, monitor) in labels.iter().zip(monitors) {
        let query = capture_overlay_url(session_id, monitor.id, theme_primary);
        let initial_size = initial_overlay_size(monitor, creation_scale);
        // WebView2 binds its composition controller to the monitor that owns the
        // parent HWND when the controller is created. Building a WebviewWindow at
        // its default position and moving it afterwards can therefore leave a
        // secondary-monitor controller with an opaque white backing surface.
        // Create the hidden native window at its final physical size, place it,
        // then attach the transparent webview after the HWND reaches its monitor.
        // This also avoids resizing Tauri's default 800x600 transparent surface,
        // which can leave the newly exposed area opaque on some Windows displays.
        // Tao also enables shadows for undecorated windows by default; that
        // non-client surface can remain opaque white on some Windows displays.
        let window = match WindowBuilder::new(app, label)
            .title(format!("Sakura 截图 · {}", monitor.name))
            .inner_size(initial_size.width, initial_size.height)
            .decorations(false)
            .shadow(false)
            .transparent(true)
            .always_on_top(true)
            .skip_taskbar(true)
            .resizable(false)
            .maximizable(false)
            .minimizable(false)
            .closable(false)
            .visible(false)
            .build()
        {
            Ok(window) => window,
            Err(_) => {
                close_windows(app, &created);
                return Err("SCREEN_CAPTURE_OVERLAY_UNAVAILABLE".to_string());
            }
        };
        if window
            .set_position(PhysicalPosition::new(monitor.bounds.x, monitor.bounds.y))
            .and_then(|_| window.set_size(overlay_size(monitor)))
            .is_err()
        {
            close_windows(app, &created);
            let _ = window.close();
            return Err("SCREEN_CAPTURE_OVERLAY_UNAVAILABLE".to_string());
        }
        let webview = WebviewBuilder::new(label, WebviewUrl::App(query.into()))
            .devtools(false)
            .transparent(true)
            .background_color(Color(0, 0, 0, 0))
            .focused(false)
            .auto_resize();
        if window
            .add_child(webview, PhysicalPosition::new(0, 0), overlay_size(monitor))
            .and_then(|_| window.show())
            .is_err()
        {
            close_windows(app, &created);
            let _ = window.close();
            return Err("SCREEN_CAPTURE_OVERLAY_UNAVAILABLE".to_string());
        }
        created.push(label.clone());
    }
    if let Some(primary_index) = monitors.iter().position(|monitor| monitor.primary) {
        if let Some(window) = app.get_webview_window(&labels[primary_index]) {
            let _ = window.set_focus();
        }
    }
    Ok(())
}

fn overlay_size(monitor: &CaptureMonitor) -> PhysicalSize<u32> {
    PhysicalSize::new(monitor.bounds.width, monitor.bounds.height)
}

fn initial_overlay_size(monitor: &CaptureMonitor, creation_scale: f64) -> LogicalSize<f64> {
    debug_assert!(creation_scale.is_finite() && creation_scale > 0.0);
    LogicalSize::new(
        f64::from(monitor.bounds.width) / creation_scale,
        f64::from(monitor.bounds.height) / creation_scale,
    )
}

fn capture_overlay_url(session_id: &str, monitor_id: u32, theme_primary: &str) -> String {
    let theme_primary = theme_primary
        .strip_prefix('#')
        .filter(|value| value.len() == 6 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
        .unwrap_or("4b9ac4");
    format!(
        "capture.html?sessionId={session_id}&monitorId={monitor_id}&themePrimary={theme_primary}"
    )
}

pub fn close_windows(app: &AppHandle, labels: &[String]) {
    for label in labels {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.close();
        }
    }
}

pub fn hide_windows(app: &AppHandle, labels: &[String]) {
    for label in labels {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.hide();
        }
    }
}

pub fn logical_selection_to_physical(
    window: &WebviewWindow,
    request: &CaptureSelectionRequest,
) -> Result<PhysicalRect, String> {
    if !request.x.is_finite()
        || !request.y.is_finite()
        || !request.width.is_finite()
        || !request.height.is_finite()
        || request.x < 0.0
        || request.y < 0.0
        || request.width < MIN_SELECTION_LOGICAL_PX
        || request.height < MIN_SELECTION_LOGICAL_PX
    {
        return Err("SCREEN_CAPTURE_SELECTION_INVALID".to_string());
    }
    let scale = window
        .scale_factor()
        .map_err(|_| "SCREEN_CAPTURE_SCALE_UNAVAILABLE".to_string())?;
    selection_at_scale(request, scale)
}

fn selection_at_scale(
    request: &CaptureSelectionRequest,
    scale: f64,
) -> Result<PhysicalRect, String> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err("SCREEN_CAPTURE_SCALE_UNAVAILABLE".to_string());
    }
    Ok(PhysicalRect {
        x: (request.x * scale).round().max(0.0) as i32,
        y: (request.y * scale).round().max(0.0) as i32,
        width: (request.width * scale).round().max(1.0) as u32,
        height: (request.height * scale).round().max(1.0) as u32,
    })
}

fn intersect_local(rect: PhysicalRect, width: u32, height: u32) -> Option<PhysicalRect> {
    if rect.x < 0 || rect.y < 0 {
        return None;
    }
    let x = rect.x as u32;
    let y = rect.y as u32;
    let right = x.saturating_add(rect.width).min(width);
    let bottom = y.saturating_add(rect.height).min(height);
    (right > x && bottom > y).then_some(PhysicalRect {
        x: rect.x,
        y: rect.y,
        width: right - x,
        height: bottom - y,
    })
}

fn resize_capture(image: image::RgbaImage) -> image::RgbaImage {
    let longest = image.width().max(image.height());
    if longest <= MAX_CAPTURE_EDGE {
        return image;
    }
    let scale = MAX_CAPTURE_EDGE as f64 / longest as f64;
    image::imageops::resize(
        &image,
        (image.width() as f64 * scale).round().max(1.0) as u32,
        (image.height() as f64 * scale).round().max(1.0) as u32,
        FilterType::Lanczos3,
    )
}

fn valid_capture_resolution(value: &str) -> bool {
    matches!(value, "fullscreen" | "720p" | "1080p" | "2160p")
}

fn capture_target_size(width: u32, height: u32, resolution: &str) -> (u32, u32) {
    let bounds = match resolution {
        "720p" => Some((1280_u32, 720_u32)),
        "1080p" => Some((1920_u32, 1080_u32)),
        "2160p" => Some((3840_u32, 2160_u32)),
        _ => None,
    };
    let Some((mut max_width, mut max_height)) = bounds else {
        return (width, height);
    };
    if height > width {
        std::mem::swap(&mut max_width, &mut max_height);
    }
    let scale = (max_width as f64 / width as f64)
        .min(max_height as f64 / height as f64)
        .min(1.0);
    (
        (width as f64 * scale).round().max(1.0) as u32,
        (height as f64 * scale).round().max(1.0) as u32,
    )
}

fn resize_host_capture(image: image::RgbaImage, resolution: &str) -> image::RgbaImage {
    let target = capture_target_size(image.width(), image.height(), resolution);
    if target == (image.width(), image.height()) {
        image
    } else {
        image::imageops::resize(&image, target.0, target.1, FilterType::Lanczos3)
    }
}

fn validate_generation(value: &str) -> Result<(), String> {
    let valid = (8..=64).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() || byte == b'-');
    valid
        .then_some(())
        .ok_or_else(|| "SCREEN_RESOURCE_GENERATION_INVALID".to_string())
}

fn cleanup_resources(resources: &mut HashMap<String, CaptureResource>) {
    for (_, resource) in resources.drain() {
        let _ = fs::remove_file(resource.path);
    }
}

#[cfg(unix)]
fn restrict_directory(path: &std::path::Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| "SCREEN_RESOURCE_ROOT_UNAVAILABLE".to_string())
}

#[cfg(not(unix))]
fn restrict_directory(_path: &std::path::Path) -> Result<(), String> {
    Ok(())
}

#[cfg(unix)]
fn restrict_file(path: &std::path::Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|_| {
        let _ = fs::remove_file(path);
        "SCREEN_CAPTURE_RESOURCE_WRITE_FAILED".to_string()
    })
}

#[cfg(not(unix))]
fn restrict_file(_path: &std::path::Path) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> CaptureSelectionRequest {
        CaptureSelectionRequest {
            session_id: "session".to_string(),
            monitor_id: 7,
            x: 10.0,
            y: 12.0,
            width: 80.0,
            height: 40.0,
        }
    }

    #[test]
    fn attachment_and_item_identifiers_use_distinct_opaque_prefixes() {
        assert!(valid_attachment_id(&format!("screen-{}", "a".repeat(32))));
        assert!(valid_attachment_item_id(&format!(
            "shot-{}",
            "b".repeat(32)
        )));
        assert!(!valid_attachment_id(&format!("shot-{}", "b".repeat(32))));
        assert!(!valid_attachment_item_id(&format!(
            "screen-{}",
            "a".repeat(32)
        )));
        assert!(!valid_attachment_item_id(&format!(
            "shot-{}",
            "g".repeat(32)
        )));
    }

    #[test]
    fn each_overlay_uses_its_own_scale_factor() {
        assert_eq!(
            selection_at_scale(&request(), 1.0).unwrap(),
            PhysicalRect {
                x: 10,
                y: 12,
                width: 80,
                height: 40
            }
        );
        assert_eq!(
            selection_at_scale(&request(), 1.5).unwrap(),
            PhysicalRect {
                x: 15,
                y: 18,
                width: 120,
                height: 60
            }
        );
    }

    #[test]
    fn overlay_native_surface_starts_at_the_target_monitor_size() {
        let monitor = CaptureMonitor {
            id: 7,
            name: "fixture".to_string(),
            bounds: PhysicalRect {
                x: 1920,
                y: 0,
                width: 2560,
                height: 1440,
            },
            primary: false,
        };
        let creation_scale = 1.25;
        let logical = initial_overlay_size(&monitor, creation_scale);

        assert_eq!(
            logical.to_physical::<u32>(creation_scale),
            overlay_size(&monitor)
        );
    }

    #[test]
    fn capture_overlay_uses_validated_theme_primary() {
        assert_eq!(
            capture_overlay_url("session", 7, "#A1b2C3"),
            "capture.html?sessionId=session&monitorId=7&themePrimary=A1b2C3"
        );
        assert_eq!(
            capture_overlay_url("session", 7, "#bad&injected=true"),
            "capture.html?sessionId=session&monitorId=7&themePrimary=4b9ac4"
        );
    }

    #[test]
    fn monitor_local_selection_clamps_at_the_monitor_edge() {
        assert_eq!(
            intersect_local(
                PhysicalRect {
                    x: 90,
                    y: 70,
                    width: 30,
                    height: 40
                },
                100,
                80
            ),
            Some(PhysicalRect {
                x: 90,
                y: 70,
                width: 10,
                height: 10
            })
        );
        assert!(intersect_local(
            PhysicalRect {
                x: -1,
                y: 0,
                width: 10,
                height: 10
            },
            100,
            80
        )
        .is_none());
    }

    #[test]
    fn sessions_are_generation_scoped_and_single_claim() {
        let root =
            std::env::temp_dir().join(format!("sakura-capture-test-{}", Uuid::new_v4().simple()));
        let manager = CaptureManager::with_base(root.clone()).unwrap();
        let monitor = CaptureMonitor {
            id: 7,
            name: "fixture".to_string(),
            bounds: PhysicalRect {
                x: -1920,
                y: 0,
                width: 1920,
                height: 1080,
            },
            primary: false,
        };
        let (session, labels, _) = manager
            .begin_session(
                "00000000-0000-4000-8000-000000004006",
                "session-a",
                7,
                &[monitor],
            )
            .unwrap();
        let claim = manager.claim_selection(&session, &labels[0], 7).unwrap();
        assert_eq!(claim.monitor_id, 7);
        assert!(manager.claim_selection(&session, &labels[0], 7).is_err());
        drop(manager);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn expired_resources_are_removed_from_registry_and_disk() {
        let root =
            std::env::temp_dir().join(format!("sakura-capture-test-{}", Uuid::new_v4().simple()));
        let manager = CaptureManager::with_base(root.clone()).unwrap();
        let generation_id = "00000000-0000-4000-8000-000000004006";
        let generation_root = manager.generation_root(generation_id).unwrap();
        let token = "a".repeat(32);
        let path = generation_root.join(format!("{token}.jpg"));
        fs::write(&path, b"expired").unwrap();
        manager.state.lock().unwrap().resources.insert(
            token.clone(),
            CaptureResource {
                path: path.clone(),
                generation_id: generation_id.to_string(),
                created_at: Instant::now()
                    .checked_sub(RESOURCE_TTL + Duration::from_secs(1))
                    .unwrap(),
            },
        );

        manager.cleanup_expired();

        assert!(!path.exists());
        assert!(!manager.state.lock().unwrap().resources.contains_key(&token));
        drop(manager);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn capture_resolution_preserves_aspect_ratio_and_never_upscales() {
        assert_eq!(capture_target_size(2560, 1440, "720p"), (1280, 720));
        assert_eq!(capture_target_size(1000, 600, "2160p"), (1000, 600));
        assert_eq!(capture_target_size(1440, 2560, "1080p"), (1080, 1920));
        assert_eq!(capture_target_size(3840, 2160, "fullscreen"), (3840, 2160));
    }

    #[test]
    fn host_frame_publishes_a_controlled_jpeg_and_releases_it() {
        let root =
            std::env::temp_dir().join(format!("sakura-host-capture-{}", Uuid::new_v4().simple()));
        let manager = CaptureManager::with_base(root.clone()).unwrap();
        let generation = "00000000-0000-4000-8000-000000004007";
        let descriptor = manager
            .publish_host_image(
                generation,
                image::RgbaImage::new(1600, 900),
                "720p",
                "fixture".to_string(),
            )
            .unwrap();
        assert_eq!((descriptor.width, descriptor.height), (1280, 720));
        let path = root
            .join(generation)
            .join(format!("{}.jpg", descriptor.resource_token));
        let bytes = fs::read(&path).unwrap();
        assert_eq!(bytes.len(), descriptor.byte_length);
        assert_eq!(&bytes[..2], &[0xff, 0xd8]);
        assert_eq!(image::load_from_memory(&bytes).unwrap().width(), 1280);
        manager.release(&descriptor.resource_token, "another-generation");
        assert!(path.exists());
        manager.release_descriptors(&[descriptor], generation);
        assert!(!path.exists());
        drop(manager);
        fs::remove_dir_all(root).unwrap();
    }
}
