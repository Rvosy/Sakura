#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod window_geometry;

use std::sync::Mutex;

use tauri::WebviewWindow;
use window_geometry::{
    apply_window_layout, LayoutApplication, LayoutContract, LayoutRevisionGuard, MonitorDescriptor,
    PhysicalRect, PresentationState,
};

const STARTUP_HTML: &str = include_str!("../../frontend/index.html");
const STARTUP_STYLES: &str = include_str!("../../frontend/styles.css");
const APP_SCRIPT: &str = include_str!("../../frontend/app.js");
const LAYOUT_SCRIPT: &str = include_str!("../../frontend/pet/layout.js");
const LAYOUT_CONTROLLER_SCRIPT: &str = include_str!("../../frontend/pet/layout-controller.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");

#[derive(Default)]
struct WindowGeometrySession {
    revision: LayoutRevisionGuard,
    portrait_anchor: Option<window_geometry::PhysicalPoint>,
}

fn layout_contract() -> Result<LayoutContract, String> {
    serde_json::from_str(LAYOUT_CONTRACT_JSON)
        .map_err(|error| format!("invalid embedded pet layout contract: {error}"))
}

fn monitor_descriptor(monitor: &tauri::Monitor) -> MonitorDescriptor {
    let work_area = monitor.work_area();
    MonitorDescriptor {
        name: monitor.name().map(ToOwned::to_owned),
        work_area: PhysicalRect {
            x: work_area.position.x,
            y: work_area.position.y,
            width: work_area.size.width,
            height: work_area.size.height,
        },
        scale_factor: monitor.scale_factor(),
    }
}

fn target_monitor(
    window: &WebviewWindow,
    anchor: Option<window_geometry::PhysicalPoint>,
) -> Result<MonitorDescriptor, String> {
    let monitors = window
        .available_monitors()
        .map_err(|error| format!("failed to enumerate monitors: {error}"))?
        .iter()
        .map(monitor_descriptor)
        .collect::<Vec<_>>();
    if monitors.is_empty() {
        return Err("no monitor work area is available".to_string());
    }

    if let Some(anchor) = anchor {
        let index = window_geometry::select_target_monitor(&monitors, anchor)
            .ok_or_else(|| "no target monitor is available".to_string())?;
        return Ok(monitors[index].clone());
    }

    if let Some(monitor) = window
        .current_monitor()
        .map_err(|error| format!("failed to query current monitor: {error}"))?
    {
        return Ok(monitor_descriptor(&monitor));
    }
    if let Some(monitor) = window
        .primary_monitor()
        .map_err(|error| format!("failed to query primary monitor: {error}"))?
    {
        return Ok(monitor_descriptor(&monitor));
    }
    Ok(monitors[0].clone())
}

#[tauri::command]
fn apply_pet_layout(
    window: WebviewWindow,
    state: PresentationState,
    revision: u64,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<LayoutApplication, String> {
    let contract = layout_contract()?;
    let mut session = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;

    if !session.revision.accept(revision) {
        return Ok(LayoutApplication::rejected(
            revision,
            state,
            contract.schema_version,
        ));
    }

    let monitor = target_monitor(&window, session.portrait_anchor)?;
    let application = apply_window_layout(
        &contract,
        state,
        revision,
        &monitor,
        session.portrait_anchor,
    )?;

    apply_native_bounds(&window, &application.physical_placement)?;
    session.portrait_anchor = Some(application.portrait_anchor);
    window
        .show()
        .map_err(|error| format!("failed to show pet window: {error}"))?;
    Ok(application)
}

#[cfg(windows)]
fn apply_native_bounds(
    window: &WebviewWindow,
    placement: &window_geometry::PhysicalPlacement,
) -> Result<(), String> {
    use windows::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, SWP_NOACTIVATE, SWP_NOOWNERZORDER, SWP_NOZORDER,
    };

    let hwnd = window
        .hwnd()
        .map_err(|error| format!("failed to access native pet window: {error}"))?;
    let width = i32::try_from(placement.width)
        .map_err(|_| "pet window width exceeds Win32 limits".to_string())?;
    let height = i32::try_from(placement.height)
        .map_err(|_| "pet window height exceeds Win32 limits".to_string())?;
    unsafe {
        SetWindowPos(
            hwnd,
            None,
            placement.x,
            placement.y,
            width,
            height,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOZORDER,
        )
        .map_err(|error| format!("failed to apply atomic pet window bounds: {error}"))?;
    }
    Ok(())
}

#[cfg(not(windows))]
fn apply_native_bounds(
    window: &WebviewWindow,
    placement: &window_geometry::PhysicalPlacement,
) -> Result<(), String> {
    use tauri::{PhysicalPosition, PhysicalSize};

    window
        .set_size(PhysicalSize::new(placement.width, placement.height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(PhysicalPosition::new(placement.x, placement.y))
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_pet_visible(window: WebviewWindow, visible: bool) -> Result<(), String> {
    if visible {
        window.show().map_err(|error| error.to_string())
    } else {
        window.hide().map_err(|error| error.to_string())
    }
}

#[tauri::command]
fn close_pet_window(window: WebviewWindow) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

fn main() {
    let _embedded_assets = (
        STARTUP_HTML.len(),
        STARTUP_STYLES.len(),
        APP_SCRIPT.len(),
        LAYOUT_SCRIPT.len(),
        LAYOUT_CONTROLLER_SCRIPT.len(),
        LAYOUT_CONTRACT_JSON.len(),
    );

    tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .invoke_handler(tauri::generate_handler![
            apply_pet_layout,
            set_pet_visible,
            close_pet_window
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Sakura Runtime v2 pet geometry gate");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_runtime_assets_are_embedded_and_the_contract_is_executable() {
        assert!(!STARTUP_HTML.is_empty());
        assert!(!STARTUP_STYLES.is_empty());
        assert!(!APP_SCRIPT.is_empty());
        assert!(!LAYOUT_SCRIPT.is_empty());
        assert!(!LAYOUT_CONTROLLER_SCRIPT.is_empty());
        let contract = layout_contract().expect("shared layout contract must parse");
        contract
            .validate()
            .expect("shared layout contract must validate");
    }
}
