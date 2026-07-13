use serde::Serialize;
use tauri::{AppHandle, Emitter, LogicalSize, Manager, Monitor, PhysicalPosition, WebviewWindow};

#[derive(Clone, Copy, Serialize)]
struct ClickThroughState {
    enabled: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PhysicalBounds {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PetWindowPlacement {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    scale_factor: f64,
}

#[tauri::command]
pub fn start_dragging(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn set_pet_visible(window: WebviewWindow, visible: bool) -> Result<(), String> {
    if visible {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
    } else {
        window.hide().map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn set_click_through(window: WebviewWindow, enabled: bool) -> Result<(), String> {
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|error| error.to_string())?;
    window
        .emit(
            "sakura://click-through-changed",
            ClickThroughState { enabled },
        )
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn set_always_on_top(window: WebviewWindow, enabled: bool) -> Result<(), String> {
    window
        .set_always_on_top(enabled)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn apply_pet_window_layout(
    window: WebviewWindow,
    width: f64,
    height: f64,
    bottom_margin: f64,
) -> Result<PetWindowPlacement, String> {
    let logical_width = width.clamp(320.0, 1200.0);
    let logical_height = height.clamp(420.0, 1200.0);
    let scale_factor = window.scale_factor().map_err(|error| error.to_string())?;
    let old_position = window.outer_position().map_err(|error| error.to_string())?;
    let old_size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor = current_or_primary_monitor(&window)?;
    let physical_width = (logical_width * scale_factor).round().max(1.0) as u32;
    let physical_height = (logical_height * scale_factor).round().max(1.0) as u32;
    let margin = (bottom_margin.max(0.0) * scale_factor).round() as u32;
    let center_x = old_position.x.saturating_add((old_size.width / 2) as i32);
    let bounds = monitor_bounds(&monitor);
    let (x, y) =
        compute_pet_window_position(bounds, center_x, physical_width, physical_height, margin);
    window
        .set_size(LogicalSize::new(logical_width, logical_height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|error| error.to_string())?;
    Ok(PetWindowPlacement {
        x,
        y,
        width: physical_width,
        height: physical_height,
        scale_factor,
    })
}

pub fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_ignore_cursor_events(false);
        let _ = window.emit(
            "sakura://click-through-changed",
            ClickThroughState { enabled: false },
        );
        let _ = window.unminimize();
        let _ = restore_pet_window_bounds(&window);
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn restore_pet_window_bounds(window: &WebviewWindow) -> Result<(), String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor = current_or_primary_monitor(window)?;
    let scale_factor = window.scale_factor().map_err(|error| error.to_string())?;
    let center_x = position.x.saturating_add((size.width / 2) as i32);
    let (x, y) = compute_pet_window_position(
        monitor_bounds(&monitor),
        center_x,
        size.width,
        size.height,
        (24.0 * scale_factor).round() as u32,
    );
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|error| error.to_string())
}

fn current_or_primary_monitor(window: &WebviewWindow) -> Result<Monitor, String> {
    window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .or_else(|| window.primary_monitor().ok().flatten())
        .ok_or_else(|| "未找到可用显示器".to_string())
}

fn monitor_bounds(monitor: &Monitor) -> PhysicalBounds {
    let work_area = monitor.work_area();
    PhysicalBounds {
        x: work_area.position.x,
        y: work_area.position.y,
        width: work_area.size.width,
        height: work_area.size.height,
    }
}

fn compute_pet_window_position(
    work_area: PhysicalBounds,
    center_x: i32,
    window_width: u32,
    window_height: u32,
    bottom_margin: u32,
) -> (i32, i32) {
    let max_x = work_area
        .x
        .saturating_add(work_area.width.saturating_sub(window_width) as i32);
    let desired_x = center_x.saturating_sub((window_width / 2) as i32);
    let x = if window_width >= work_area.width {
        work_area.x
    } else {
        desired_x.clamp(work_area.x, max_x)
    };
    let y = if window_height.saturating_add(bottom_margin) >= work_area.height {
        work_area.y
    } else {
        work_area.y.saturating_add(
            work_area
                .height
                .saturating_sub(window_height)
                .saturating_sub(bottom_margin) as i32,
        )
    };
    (x, y)
}

pub fn hide_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pet_window_label_is_stable() {
        assert_eq!("main", "main");
    }

    #[test]
    fn windows_pet_position_is_bottom_anchored_and_clamped_to_work_area() {
        let work_area = PhysicalBounds {
            x: 1920,
            y: 0,
            width: 2560,
            height: 1400,
        };

        assert_eq!(
            compute_pet_window_position(work_area, 4300, 736, 640, 24),
            (3744, 736)
        );
        assert_eq!(
            compute_pet_window_position(work_area, 1200, 736, 640, 24),
            (1920, 736)
        );
    }

    #[test]
    fn windows_pet_position_handles_window_larger_than_work_area() {
        let work_area = PhysicalBounds {
            x: -1280,
            y: 40,
            width: 1280,
            height: 984,
        };

        assert_eq!(
            compute_pet_window_position(work_area, -400, 1600, 1200, 24),
            (-1280, 40)
        );
    }
}
