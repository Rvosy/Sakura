use serde::Serialize;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, Monitor, PhysicalPosition, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

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

#[derive(Clone, Copy, Debug, PartialEq)]
struct SecondaryWindowSpec {
    label: &'static str,
    title: &'static str,
    path: &'static str,
    width: f64,
    height: f64,
    min_width: f64,
    min_height: f64,
}

const SETTINGS_WINDOW: SecondaryWindowSpec = SecondaryWindowSpec {
    label: "settings",
    title: "Sakura 设置",
    path: "/settings/index.html",
    width: 1120.0,
    height: 760.0,
    min_width: 900.0,
    min_height: 640.0,
};
const STUDIO_WINDOW: SecondaryWindowSpec = SecondaryWindowSpec {
    label: "studio",
    title: "Sakura 角色工作室",
    path: "/studio/index.html",
    width: 1180.0,
    height: 800.0,
    min_width: 960.0,
    min_height: 680.0,
};
const HISTORY_WINDOW: SecondaryWindowSpec = SecondaryWindowSpec {
    label: "history",
    title: "Sakura 对话历史",
    path: "/history/index.html",
    width: 820.0,
    height: 680.0,
    min_width: 620.0,
    min_height: 480.0,
};
const DIAGNOSTICS_WINDOW: SecondaryWindowSpec = SecondaryWindowSpec {
    label: "diagnostics",
    title: "Sakura 诊断",
    path: "/diagnostics/index.html",
    width: 860.0,
    height: 700.0,
    min_width: 680.0,
    min_height: 520.0,
};
const RUNTIME_REPAIR_WINDOW: SecondaryWindowSpec = SecondaryWindowSpec {
    label: "runtime-repair",
    title: "Sakura 启动修复",
    path: "/runtime-repair/index.html",
    width: 760.0,
    height: 560.0,
    min_width: 640.0,
    min_height: 480.0,
};

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

#[tauri::command]
pub async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    open_secondary_window(app, SETTINGS_WINDOW).await
}

#[tauri::command]
pub async fn open_studio_window(app: AppHandle) -> Result<(), String> {
    open_secondary_window(app, STUDIO_WINDOW).await
}

#[tauri::command]
pub async fn open_history_window(app: AppHandle) -> Result<(), String> {
    open_secondary_window(app, HISTORY_WINDOW).await
}

#[tauri::command]
pub async fn open_diagnostics_window(app: AppHandle) -> Result<(), String> {
    open_secondary_window(app, DIAGNOSTICS_WINDOW).await
}

pub async fn show_onboarding_route(app: AppHandle) -> Result<(), String> {
    hide_main_window(&app);
    close_window(&app, RUNTIME_REPAIR_WINDOW.label);
    open_secondary_window(app, SETTINGS_WINDOW).await
}

pub async fn show_runtime_repair_route(app: AppHandle) -> Result<(), String> {
    hide_main_window(&app);
    close_window(&app, SETTINGS_WINDOW.label);
    open_secondary_window(app, RUNTIME_REPAIR_WINDOW).await
}

pub fn show_ready_route(app: &AppHandle) {
    close_window(app, RUNTIME_REPAIR_WINDOW.label);
    show_main_window(app);
}

async fn open_secondary_window(app: AppHandle, spec: SecondaryWindowSpec) -> Result<(), String> {
    let (sender, mut receiver) = tauri::async_runtime::channel(1);
    let main_thread_app = app.clone();
    app.run_on_main_thread(move || {
        let result = open_secondary_window_on_main_thread(&main_thread_app, spec);
        let _ = sender.blocking_send(result);
    })
    .map_err(|error| error.to_string())?;
    receiver
        .recv()
        .await
        .ok_or_else(|| "次级窗口创建任务已中断".to_string())?
}

fn open_secondary_window_on_main_thread(
    app: &AppHandle,
    spec: SecondaryWindowSpec,
) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(spec.label) {
        let _ = window.set_title(spec.title);
        return focus_window(&window);
    }
    let window = WebviewWindowBuilder::new(app, spec.label, WebviewUrl::App(spec.path.into()))
        .title(spec.title)
        .inner_size(spec.width, spec.height)
        .min_inner_size(spec.min_width, spec.min_height)
        .resizable(true)
        .maximizable(true)
        .minimizable(true)
        .closable(true)
        .decorations(true)
        .transparent(false)
        .always_on_top(true)
        .center()
        .build()
        .map_err(|error| error.to_string())?;
    focus_window(&window)
}

fn close_window(app: &AppHandle, label: &str) {
    if let Some(window) = app.get_webview_window(label) {
        let _ = window.destroy();
    }
}

fn focus_window(window: &WebviewWindow) -> Result<(), String> {
    window
        .set_always_on_top(true)
        .map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
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

    #[test]
    fn secondary_windows_have_stable_labels_and_app_urls() {
        assert_eq!(SETTINGS_WINDOW.label, "settings");
        assert_eq!(SETTINGS_WINDOW.path, "/settings/index.html");
        assert_eq!(STUDIO_WINDOW.label, "studio");
        assert_eq!(STUDIO_WINDOW.path, "/studio/index.html");
        assert_eq!(HISTORY_WINDOW.label, "history");
        assert_eq!(HISTORY_WINDOW.path, "/history/index.html");
        assert_eq!(DIAGNOSTICS_WINDOW.label, "diagnostics");
        assert_eq!(DIAGNOSTICS_WINDOW.path, "/diagnostics/index.html");
        assert_eq!(RUNTIME_REPAIR_WINDOW.label, "runtime-repair");
        assert_eq!(RUNTIME_REPAIR_WINDOW.path, "/runtime-repair/index.html");
    }
}

pub fn set_main_always_on_top(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "桌宠主窗口不存在".to_string())?;
    window
        .set_always_on_top(enabled)
        .map_err(|error| error.to_string())
}
