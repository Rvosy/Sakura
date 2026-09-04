use std::{
    collections::HashMap,
    sync::{mpsc, Mutex},
    time::Duration,
};

use serde::Deserialize;
use tauri::{
    webview::{Color, WebviewBuilder},
    window::WindowBuilder,
    AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
};
use uuid::Uuid;
use xcap::Monitor;

use crate::capture::{self, CaptureMonitor};

const PICK_TIMEOUT: Duration = Duration::from_secs(2 * 60);

struct ActivePicker {
    session_id: String,
    windows: HashMap<String, u32>,
    result: mpsc::Sender<Result<String, String>>,
}

#[derive(Default)]
pub struct ColorPickerState {
    active: Mutex<Option<ActivePicker>>,
}

pub struct PickerSession {
    pub id: String,
    pub labels: Vec<String>,
    pub receiver: mpsc::Receiver<Result<String, String>>,
}

#[derive(Debug)]
pub struct PickerClaim {
    pub monitor_id: u32,
    pub labels: Vec<String>,
    result: mpsc::Sender<Result<String, String>>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ColorPickRequest {
    pub session_id: String,
    pub monitor_id: u32,
    pub x: f64,
    pub y: f64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ColorCancelRequest {
    pub session_id: String,
}

impl ColorPickerState {
    pub fn begin(
        &self,
        monitors: &[CaptureMonitor],
    ) -> Result<(PickerSession, Vec<String>), String> {
        if monitors.is_empty() {
            return Err("STUDIO_COLOR_NO_MONITORS".to_string());
        }
        let (sender, receiver) = mpsc::channel();
        let session_id = Uuid::new_v4().simple().to_string();
        let mut windows = HashMap::new();
        let mut labels = Vec::with_capacity(monitors.len());
        for (index, monitor) in monitors.iter().enumerate() {
            let label = format!("studio-color-{}-{index}", &session_id[..12]);
            windows.insert(label.clone(), monitor.id);
            labels.push(label);
        }
        let mut active = self
            .active
            .lock()
            .map_err(|_| "STUDIO_COLOR_STATE_UNAVAILABLE".to_string())?;
        let previous = active
            .take()
            .map(|picker| {
                let _ = picker.result.send(Err("STUDIO_COLOR_REPLACED".to_string()));
                picker.windows.into_keys().collect()
            })
            .unwrap_or_default();
        *active = Some(ActivePicker {
            session_id: session_id.clone(),
            windows,
            result: sender,
        });
        Ok((
            PickerSession {
                id: session_id,
                labels,
                receiver,
            },
            previous,
        ))
    }

    pub fn claim(
        &self,
        window_label: &str,
        request: &ColorPickRequest,
    ) -> Result<PickerClaim, String> {
        let mut active = self
            .active
            .lock()
            .map_err(|_| "STUDIO_COLOR_STATE_UNAVAILABLE".to_string())?;
        let picker = active
            .take()
            .ok_or_else(|| "STUDIO_COLOR_SESSION_STALE".to_string())?;
        if picker.session_id != request.session_id
            || picker.windows.get(window_label).copied() != Some(request.monitor_id)
        {
            *active = Some(picker);
            return Err("STUDIO_COLOR_SESSION_STALE".to_string());
        }
        Ok(PickerClaim {
            monitor_id: request.monitor_id,
            labels: picker.windows.into_keys().collect(),
            result: picker.result,
        })
    }

    pub fn cancel(&self, window_label: &str, session_id: &str) -> Option<Vec<String>> {
        let mut active = self.active.lock().ok()?;
        let matches = active.as_ref().is_some_and(|picker| {
            picker.session_id == session_id && picker.windows.contains_key(window_label)
        });
        matches.then(|| {
            let picker = active.take().expect("matched picker exists");
            let _ = picker
                .result
                .send(Err("STUDIO_COLOR_CANCELLED".to_string()));
            picker.windows.into_keys().collect()
        })
    }

    pub fn fail(&self, session_id: &str, code: &str) {
        if let Ok(mut active) = self.active.lock() {
            if active
                .as_ref()
                .is_some_and(|picker| picker.session_id == session_id)
            {
                if let Some(picker) = active.take() {
                    let _ = picker.result.send(Err(code.to_string()));
                }
            }
        }
    }
}

impl PickerClaim {
    pub fn complete(self, result: Result<String, String>) {
        let _ = self.result.send(result);
    }
}

pub fn show_overlays(
    app: &AppHandle,
    session: &PickerSession,
    monitors: &[CaptureMonitor],
) -> Result<(), String> {
    if session.labels.len() != monitors.len() {
        return Err("STUDIO_COLOR_OVERLAY_INVALID".to_string());
    }
    let creation_scale = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| monitor.scale_factor())
        .filter(|scale| scale.is_finite() && *scale > 0.0)
        .unwrap_or(1.0);
    let mut created = Vec::new();
    for (label, monitor) in session.labels.iter().zip(monitors) {
        let logical = LogicalSize::new(
            f64::from(monitor.bounds.width) / creation_scale,
            f64::from(monitor.bounds.height) / creation_scale,
        );
        let window = match WindowBuilder::new(app, label)
            .title(format!("Sakura 取色 · {}", monitor.name))
            .inner_size(logical.width, logical.height)
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
                capture::close_windows(app, &created);
                return Err("STUDIO_COLOR_OVERLAY_UNAVAILABLE".to_string());
            }
        };
        let size = PhysicalSize::new(monitor.bounds.width, monitor.bounds.height);
        if window
            .set_position(PhysicalPosition::new(monitor.bounds.x, monitor.bounds.y))
            .and_then(|_| window.set_size(size))
            .is_err()
        {
            capture::close_windows(app, &created);
            let _ = window.close();
            return Err("STUDIO_COLOR_OVERLAY_UNAVAILABLE".to_string());
        }
        let url = format!(
            "studio/color-picker.html?sessionId={}&monitorId={}",
            session.id, monitor.id
        );
        let webview = WebviewBuilder::new(label, WebviewUrl::App(url.into()))
            .devtools(false)
            .transparent(true)
            .background_color(Color(0, 0, 0, 0))
            .focused(false)
            .auto_resize();
        if window
            .add_child(webview, PhysicalPosition::new(0, 0), size)
            .and_then(|_| window.show())
            .is_err()
        {
            capture::close_windows(app, &created);
            let _ = window.close();
            return Err("STUDIO_COLOR_OVERLAY_UNAVAILABLE".to_string());
        }
        created.push(label.clone());
    }
    if let Some(primary_index) = monitors.iter().position(|monitor| monitor.primary) {
        if let Some(window) = app.get_webview_window(&session.labels[primary_index]) {
            let _ = window.set_focus();
        }
    }
    Ok(())
}

pub fn logical_point(window: &WebviewWindow, x: f64, y: f64) -> Result<(u32, u32), String> {
    if !x.is_finite() || !y.is_finite() || x < 0.0 || y < 0.0 {
        return Err("STUDIO_COLOR_POINT_INVALID".to_string());
    }
    let scale = window
        .scale_factor()
        .map_err(|_| "STUDIO_COLOR_SCALE_UNAVAILABLE".to_string())?;
    if !scale.is_finite() || scale <= 0.0 {
        return Err("STUDIO_COLOR_SCALE_UNAVAILABLE".to_string());
    }
    Ok(((x * scale).floor() as u32, (y * scale).floor() as u32))
}

pub fn capture_color(monitor_id: u32, x: u32, y: u32) -> Result<String, String> {
    let monitor = Monitor::all()
        .map_err(|_| "STUDIO_COLOR_PLATFORM_UNAVAILABLE".to_string())?
        .into_iter()
        .find(|monitor| monitor.id().ok() == Some(monitor_id))
        .ok_or_else(|| "STUDIO_COLOR_MONITOR_GONE".to_string())?;
    let width = monitor
        .width()
        .map_err(|_| "STUDIO_COLOR_MONITOR_GONE".to_string())?;
    let height = monitor
        .height()
        .map_err(|_| "STUDIO_COLOR_MONITOR_GONE".to_string())?;
    if x >= width || y >= height {
        return Err("STUDIO_COLOR_POINT_INVALID".to_string());
    }
    let image = monitor
        .capture_region(x, y, 1, 1)
        .map_err(|_| "STUDIO_COLOR_PLATFORM_DENIED".to_string())?;
    let pixel = image.get_pixel(0, 0).0;
    Ok(format!("#{:02X}{:02X}{:02X}", pixel[0], pixel[1], pixel[2]))
}

pub fn wait_for_result(receiver: mpsc::Receiver<Result<String, String>>) -> Result<String, String> {
    receiver
        .recv_timeout(PICK_TIMEOUT)
        .map_err(|_| "STUDIO_COLOR_TIMEOUT".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn studio_color_picker_claim_is_bound_to_its_overlay_window() {
        let state = ColorPickerState::default();
        let monitor = CaptureMonitor {
            id: 7,
            name: "fixture".to_string(),
            bounds: crate::capture::PhysicalRect {
                x: 0,
                y: 0,
                width: 100,
                height: 80,
            },
            primary: true,
        };
        let (session, _) = state.begin(&[monitor]).unwrap();
        let request = ColorPickRequest {
            session_id: session.id.clone(),
            monitor_id: 7,
            x: 10.0,
            y: 20.0,
        };
        assert_eq!(
            state.claim("wrong-window", &request).unwrap_err(),
            "STUDIO_COLOR_SESSION_STALE"
        );
        assert!(state.claim(&session.labels[0], &request).is_ok());
    }
}
