#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

#[allow(dead_code)] // Production wiring is activated incrementally across Phase 1C.
mod core_host_protocol;
#[allow(dead_code)] // Exercised by WP-1C tests and debug acceptance before release wiring.
mod core_host_runtime;
#[allow(dead_code)] // Exercised by WP-1B tests before Fake Core wiring in WP-1B-03.
mod core_supervisor;
#[cfg(test)]
mod fake_core_runtime;
#[allow(dead_code)] // Consumed by the serial Supervisor beginning in WP-1B-02.
mod managed_process_tree;
#[cfg(all(windows, debug_assertions))]
mod phase_1b_runtime_acceptance;
#[cfg(all(windows, debug_assertions))]
mod phase_1c_core_host_acceptance;
mod shared_instance;
mod window_geometry;
mod window_interaction;

use std::sync::Mutex;

use serde::Serialize;
use shared_instance::{AcquireOutcome, SharedInstanceGuard};
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
const HIT_REGIONS_SCRIPT: &str = include_str!("../../frontend/pet/hit-regions.js");
const INPUT_FOCUS_SCRIPT: &str = include_str!("../../frontend/pet/input-focus.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");
const ALREADY_RUNNING_TITLE: &str = "Sakura 已在运行";
const ALREADY_RUNNING_BODY: &str =
    "另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。";

#[derive(Default)]
struct WindowGeometrySession {
    revision: LayoutRevisionGuard,
    portrait_anchor: Option<window_geometry::PhysicalPoint>,
    state: Option<PresentationState>,
    applied_revision: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PetLayoutApplication {
    #[serde(flatten)]
    layout: LayoutApplication,
    hit_regions: Option<window_interaction::PhysicalHitRegions>,
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
) -> Result<PetLayoutApplication, String> {
    let contract = layout_contract()?;
    let mut session = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;

    if !session.revision.accept(revision) {
        return Ok(PetLayoutApplication {
            layout: LayoutApplication::rejected(revision, state, contract.schema_version),
            hit_regions: None,
        });
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
    let hit_regions = apply_native_interaction_region(&window, &contract, &application)?;
    session.portrait_anchor = Some(application.portrait_anchor);
    session.state = Some(state);
    session.applied_revision = revision;
    window
        .show()
        .map_err(|error| format!("failed to show pet window: {error}"))?;
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
    })
}

fn apply_native_interaction_region(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let logical = window_interaction::logical_hit_regions(contract, application.state)?;
    let physical = window_interaction::scale_hit_regions(
        &logical,
        application.scale_factor * application.content_scale,
    )?;
    if let Err(error) = window_interaction::apply_native_hit_regions(window, &physical) {
        return match window_interaction::restore_full_native_hit_region(window) {
            Ok(()) => Err(format!(
                "failed to apply native hit regions; restored full-window interaction: {error}"
            )),
            Err(recovery_error) => Err(format!(
                "failed to apply native hit regions ({error}) and recovery failed ({recovery_error})"
            )),
        };
    }
    Ok(physical)
}

#[tauri::command]
fn start_pet_drag(
    window: WebviewWindow,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<PetLayoutApplication, String> {
    window_interaction::start_native_drag_and_wait(&window)?;

    let contract = layout_contract()?;
    let mut session = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let state = session
        .state
        .ok_or_else(|| "pet layout is not ready for dragging".to_string())?;
    let position = window
        .outer_position()
        .map_err(|error| format!("failed to read dragged window position: {error}"))?;
    let monitor = target_monitor(&window, None)?;
    let requested_anchor = window_geometry::anchor_from_window_position(
        &contract,
        &monitor,
        window_geometry::PhysicalPoint {
            x: position.x,
            y: position.y,
        },
    )?;
    let application = apply_window_layout(
        &contract,
        state,
        session.applied_revision,
        &monitor,
        Some(requested_anchor),
    )?;
    apply_native_bounds(&window, &application.physical_placement)?;
    let hit_regions = apply_native_interaction_region(&window, &contract, &application)?;
    session.portrait_anchor = Some(application.portrait_anchor);
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
    })
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
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())
    } else {
        window.hide().map_err(|error| error.to_string())
    }
}

#[tauri::command]
fn close_pet_window(window: WebviewWindow) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

#[cfg(windows)]
fn show_startup_message(title: &str, body: &str, fatal: bool) {
    use windows::{
        core::PCWSTR,
        Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_ICONINFORMATION, MB_OK},
    };

    let wide_title = title
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let wide_body = body
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let style = MB_OK
        | if fatal {
            MB_ICONERROR
        } else {
            MB_ICONINFORMATION
        };
    unsafe {
        MessageBoxW(
            None,
            PCWSTR(wide_body.as_ptr()),
            PCWSTR(wide_title.as_ptr()),
            style,
        );
    }
}

#[cfg(not(windows))]
fn show_startup_message(title: &str, body: &str, _fatal: bool) {
    eprintln!("{title}: {body}");
}

fn main() {
    #[cfg(all(windows, debug_assertions))]
    if phase_1b_runtime_acceptance::run_fake_core_child_if_requested() {
        return;
    }

    let _instance_guard = match SharedInstanceGuard::acquire() {
        AcquireOutcome::Acquired(guard) => guard,
        AcquireOutcome::AlreadyRunning => {
            show_startup_message(ALREADY_RUNNING_TITLE, ALREADY_RUNNING_BODY, false);
            return;
        }
        AcquireOutcome::Fatal(error) => {
            show_startup_message(
                "Sakura 启动失败",
                &format!("无法创建共享应用锁（Win32 错误 {error}）。Sakura 未继续启动。"),
                true,
            );
            std::process::exit(1);
        }
    };

    let _embedded_assets = (
        STARTUP_HTML.len(),
        STARTUP_STYLES.len(),
        APP_SCRIPT.len(),
        LAYOUT_SCRIPT.len(),
        LAYOUT_CONTROLLER_SCRIPT.len(),
        HIT_REGIONS_SCRIPT.len(),
        INPUT_FOCUS_SCRIPT.len(),
        LAYOUT_CONTRACT_JSON.len(),
    );

    let app = tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .invoke_handler(tauri::generate_handler![
            apply_pet_layout,
            start_pet_drag,
            set_pet_visible,
            close_pet_window
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Sakura Runtime v2 pet geometry gate");

    #[cfg(all(windows, debug_assertions))]
    let mut phase_1c_acceptance =
        match phase_1c_core_host_acceptance::AcceptanceSession::start_if_requested() {
            Ok(session) => session,
            Err(error) => {
                show_startup_message("Sakura Phase 1C 验收启动失败", &error, true);
                std::process::exit(2);
            }
        };

    #[cfg(all(windows, debug_assertions))]
    let mut phase_1b_acceptance =
        match phase_1b_runtime_acceptance::AcceptanceSession::start_if_requested() {
            Ok(session) => session,
            Err(error) => {
                show_startup_message("Sakura Phase 1B 验收启动失败", &error, true);
                std::process::exit(2);
            }
        };

    #[cfg(all(windows, debug_assertions))]
    if let Some(session) = phase_1b_acceptance.take() {
        let shutdown = session.shutdown_signal();
        let exit_code = app.run_return(move |_app_handle, event| match event {
            tauri::RunEvent::Exit
            | tauri::RunEvent::ExitRequested { .. }
            | tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } => shutdown.request(),
            _ => {}
        });
        session
            .shutdown_and_join()
            .expect("Phase 1B acceptance worker should stop without residuals");
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }

    #[cfg(all(windows, debug_assertions))]
    if let Some(session) = phase_1c_acceptance.take() {
        let shutdown = session.shutdown_signal();
        let exit_code = app.run_return(move |_app_handle, event| match event {
            tauri::RunEvent::Exit
            | tauri::RunEvent::ExitRequested { .. }
            | tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } => shutdown.request(),
            _ => {}
        });
        session
            .shutdown_and_join()
            .expect("Phase 1C acceptance worker should stop without residuals");
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }

    app.run(|_, _| {});
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
        assert!(!HIT_REGIONS_SCRIPT.is_empty());
        assert!(!INPUT_FOCUS_SCRIPT.is_empty());
        let contract = layout_contract().expect("shared layout contract must parse");
        contract
            .validate()
            .expect("shared layout contract must validate");
    }
}
