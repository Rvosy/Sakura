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
#[cfg(debug_assertions)]
mod phase_1c_core_host_acceptance;
#[allow(dead_code)] // Compile-only platform contracts are wired by WP-1P-02 through WP-1P-05.
mod platform;
mod shared_instance;
mod shell_lifecycle;
mod window_geometry;
mod window_interaction;

use std::sync::Mutex;

use platform::{
    InstanceLockAcquire, InstanceLockBackend, NativeDiagnosticsBackend,
    NativeDiagnosticsBackendImpl, NativeDiagnosticsRequest, NativeWindowInteractionBackend,
    WindowInteractionBackend, SHARED_INSTANCE_ID,
};
use serde::Serialize;
use shared_instance::NativeInstanceLockBackend;
use tauri::{State, WebviewWindow};
use window_geometry::{
    apply_window_layout, LayoutApplication, LayoutContract, LayoutRevisionGuard, MonitorDescriptor,
    PhysicalRect, PresentationState,
};

const STARTUP_HTML: &str = include_str!("../../frontend/index.html");
const STARTUP_STYLES: &str = include_str!("../../frontend/styles.css");
const APP_SCRIPT: &str = include_str!("../../frontend/app.js");
const LIFECYCLE_SCRIPT: &str = include_str!("../../frontend/lifecycle.js");
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
    deferred_drag_pending: bool,
}

struct ShellLifecycleState {
    handle: Option<shell_lifecycle::ShellLifecycleHandle>,
}

impl WindowGeometrySession {
    fn begin_deferred_drag(&mut self) {
        self.deferred_drag_pending = true;
    }

    fn cancel_deferred_drag(&mut self) {
        self.deferred_drag_pending = false;
    }

    fn is_deferred_drag_pending(&self) -> bool {
        self.deferred_drag_pending
    }

    fn finish_deferred_drag(&mut self) {
        self.deferred_drag_pending = false;
    }
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

    let requested_anchor = if session.is_deferred_drag_pending() {
        let position = window
            .outer_position()
            .map_err(|error| format!("failed to read dragged window position: {error}"))?;
        let monitor = target_monitor(&window, None)?;
        Some(window_geometry::anchor_from_window_position(
            &contract,
            &monitor,
            window_geometry::PhysicalPoint {
                x: position.x,
                y: position.y,
            },
        )?)
    } else {
        session.portrait_anchor
    };
    let monitor = target_monitor(&window, requested_anchor)?;
    let application = apply_window_layout(&contract, state, revision, &monitor, requested_anchor)?;

    if session.is_deferred_drag_pending() {
        session.finish_deferred_drag();
    }
    NativeWindowInteractionBackend
        .apply_bounds(&window, &application.physical_placement)
        .map_err(|error| error.to_string())?;
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
    let backend = NativeWindowInteractionBackend;
    if let Err(error) = backend.apply_hit_regions(window, &physical) {
        return match backend.restore_full_hit_region(window) {
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

fn commit_dragged_window_position(
    window: WebviewWindow,
    session: &mut WindowGeometrySession,
    position: window_geometry::PhysicalPoint,
) -> Result<PetLayoutApplication, String> {
    let contract = layout_contract()?;
    let state = session
        .state
        .ok_or_else(|| "pet layout is not ready for dragging".to_string())?;
    let monitor = target_monitor(&window, None)?;
    let requested_anchor =
        window_geometry::anchor_from_window_position(&contract, &monitor, position)?;
    let application = apply_window_layout(
        &contract,
        state,
        session.applied_revision,
        &monitor,
        Some(requested_anchor),
    )?;
    NativeWindowInteractionBackend
        .apply_bounds(&window, &application.physical_placement)
        .map_err(|error| error.to_string())?;
    let hit_regions = apply_native_interaction_region(&window, &contract, &application)?;
    session.portrait_anchor = Some(application.portrait_anchor);
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
    })
}

#[tauri::command]
fn start_pet_drag(
    window: WebviewWindow,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<Option<PetLayoutApplication>, String> {
    let expects_deferred_completion = matches!(
        window_interaction::native_drag_completion(),
        window_interaction::NativeDragCompletion::DeferredWindowMoved
    );
    {
        let mut session = session
            .lock()
            .map_err(|_| "window geometry state is unavailable".to_string())?;
        if session.state.is_none() {
            return Err("pet layout is not ready for dragging".to_string());
        }
        if expects_deferred_completion {
            session.begin_deferred_drag();
        }
    }

    let completion = match NativeWindowInteractionBackend.start_drag(&window) {
        Ok(completion) => completion,
        Err(error) => {
            if expects_deferred_completion {
                let mut session = session
                    .lock()
                    .map_err(|_| "window geometry state is unavailable".to_string())?;
                session.cancel_deferred_drag();
            }
            return Err(error.to_string());
        }
    };

    match completion {
        window_interaction::NativeDragCompletion::SynchronousMoveLoop => {
            if expects_deferred_completion {
                let mut session = session
                    .lock()
                    .map_err(|_| "window geometry state is unavailable".to_string())?;
                session.cancel_deferred_drag();
            }
            let position = window
                .outer_position()
                .map_err(|error| format!("failed to read dragged window position: {error}"))?;
            let mut session = session
                .lock()
                .map_err(|_| "window geometry state is unavailable".to_string())?;
            commit_dragged_window_position(
                window,
                &mut session,
                window_geometry::PhysicalPoint {
                    x: position.x,
                    y: position.y,
                },
            )
            .map(Some)
        }
        window_interaction::NativeDragCompletion::DeferredWindowMoved => {
            if !expects_deferred_completion {
                let mut session = session
                    .lock()
                    .map_err(|_| "window geometry state is unavailable".to_string())?;
                session.begin_deferred_drag();
            }
            Ok(None)
        }
    }
}

#[tauri::command]
fn probe_pet_visibility(window: WebviewWindow) -> Result<(), String> {
    NativeWindowInteractionBackend
        .set_visible(&window, false)
        .map_err(|error| error.to_string())?;

    let restore_window = window.clone();
    if let Err(error) = window.run_on_main_thread(move || {
        if let Err(error) = NativeWindowInteractionBackend.set_visible(&restore_window, true) {
            eprintln!("failed to restore pet visibility probe: {error}");
        }
    }) {
        return match NativeWindowInteractionBackend.set_visible(&window, true) {
            Ok(()) => Err(format!(
                "failed to schedule pet visibility restoration: {error}"
            )),
            Err(recovery_error) => Err(format!(
                "failed to schedule pet visibility restoration ({error}) and immediate recovery failed ({recovery_error})"
            )),
        };
    }
    Ok(())
}

#[tauri::command]
fn collect_native_diagnostics(
    request: Option<NativeDiagnosticsRequest>,
) -> Result<platform::NativeDiagnosticsSnapshot, String> {
    NativeDiagnosticsBackendImpl
        .collect(&request.unwrap_or_default())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn runtime_lifecycle_snapshot(
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<shell_lifecycle::ShellLifecyclePublication, &'static str> {
    lifecycle
        .handle
        .as_ref()
        .ok_or("LIFECYCLE_COMMAND_UNAVAILABLE")?
        .snapshot()
}

#[tauri::command]
fn retry_core(lifecycle: State<'_, ShellLifecycleState>) -> Result<(), &'static str> {
    lifecycle
        .handle
        .as_ref()
        .ok_or("LIFECYCLE_COMMAND_UNAVAILABLE")?
        .retry()
}

#[tauri::command]
fn exit_runtime(
    lifecycle: State<'_, ShellLifecycleState>,
    app_handle: tauri::AppHandle,
) -> Result<(), &'static str> {
    lifecycle
        .handle
        .as_ref()
        .ok_or("LIFECYCLE_COMMAND_UNAVAILABLE")?
        .request_shutdown()?;
    app_handle.exit(0);
    Ok(())
}

#[tauri::command]
fn close_pet_window(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<(), String> {
    if let Some(handle) = &lifecycle.handle {
        handle.request_shutdown().map_err(str::to_string)?;
    }
    window.close().map_err(|error| error.to_string())?;
    app_handle.exit(0);
    Ok(())
}

fn development_runtime_request() -> platform::RuntimeLocationRequest {
    let executable_directory = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(ToOwned::to_owned))
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    let current_directory =
        std::env::current_dir().unwrap_or_else(|_| executable_directory.clone());
    let repository_root = current_directory
        .ancestors()
        .chain(executable_directory.ancestors())
        .find(|candidate| {
            candidate.join("app/core_host/__main__.py").is_file()
                && candidate.join("desktop/src-tauri/runtime-layouts").is_dir()
        })
        .map(ToOwned::to_owned)
        .unwrap_or(current_directory);
    platform::RuntimeLocationRequest {
        mode: platform::RuntimeMode::ExplicitDevelopment,
        target: platform::current_platform_target()
            .expect("Runtime v2 Shell requires a formal target"),
        executable_directory,
        resource_directory: repository_root.clone(),
        explicit_development_root: Some(repository_root.clone()),
        assistant_root: repository_root,
    }
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
    #[cfg(unix)]
    if platform::run_guardian_if_requested() {
        return;
    }

    #[cfg(all(windows, debug_assertions))]
    if phase_1b_runtime_acceptance::run_fake_core_child_if_requested() {
        return;
    }

    let instance_lock_backend = NativeInstanceLockBackend;
    let _instance_guard = match instance_lock_backend.acquire(SHARED_INSTANCE_ID) {
        Ok(InstanceLockAcquire::Acquired(guard)) => guard,
        Ok(InstanceLockAcquire::AlreadyRunning) => {
            #[cfg(debug_assertions)]
            if phase_1c_core_host_acceptance::record_lock_conflict_if_requested().unwrap_or(false) {
                eprintln!("{ALREADY_RUNNING_TITLE}: {ALREADY_RUNNING_BODY}");
                return;
            }
            show_startup_message(ALREADY_RUNNING_TITLE, ALREADY_RUNNING_BODY, false);
            return;
        }
        Err(error) => {
            show_startup_message(
                "Sakura 启动失败",
                &format!("无法创建共享应用锁（{error}）。Sakura 未继续启动。"),
                true,
            );
            std::process::exit(1);
        }
    };

    let _embedded_assets = (
        STARTUP_HTML.len(),
        STARTUP_STYLES.len(),
        APP_SCRIPT.len(),
        LIFECYCLE_SCRIPT.len(),
        LAYOUT_SCRIPT.len(),
        LAYOUT_CONTROLLER_SCRIPT.len(),
        HIT_REGIONS_SCRIPT.len(),
        INPUT_FOCUS_SCRIPT.len(),
        LAYOUT_CONTRACT_JSON.len(),
    );

    let acceptance_mode = std::env::var_os("SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY").is_some();
    let shell_lifecycle_session = (!acceptance_mode)
        .then(|| shell_lifecycle::ShellLifecycleSession::start(development_runtime_request()));
    let shell_lifecycle_handle = shell_lifecycle_session
        .as_ref()
        .map(shell_lifecycle::ShellLifecycleSession::handle);

    let app = tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .manage(ShellLifecycleState {
            handle: shell_lifecycle_handle.clone(),
        })
        .invoke_handler(tauri::generate_handler![
            apply_pet_layout,
            start_pet_drag,
            probe_pet_visibility,
            close_pet_window,
            collect_native_diagnostics,
            runtime_lifecycle_snapshot,
            retry_core,
            exit_runtime
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Sakura Runtime v2 pet geometry gate");

    #[cfg(debug_assertions)]
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

    #[cfg(debug_assertions)]
    if let Some(session) = phase_1c_acceptance.take() {
        let shutdown = session.shutdown_signal();
        let exit_watcher = session.start_controlled_exit_watcher(app.handle().clone());
        let exit_code = app.run_return(move |_app_handle, event| match event {
            tauri::RunEvent::Exit
            | tauri::RunEvent::ExitRequested { .. }
            | tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } => shutdown.request(),
            _ => {}
        });
        if let Some(exit_watcher) = exit_watcher {
            exit_watcher
                .join()
                .expect("Phase 1P controlled exit watcher should not panic");
        }
        session
            .shutdown_and_join()
            .expect("Phase 1C acceptance worker should stop without residuals");
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }

    let exit_code = app.run_return(move |_app_handle, event| match event {
        tauri::RunEvent::Exit
        | tauri::RunEvent::ExitRequested { .. }
        | tauri::RunEvent::WindowEvent {
            event: tauri::WindowEvent::CloseRequested { .. },
            ..
        } => {
            if let Some(handle) = &shell_lifecycle_handle {
                let _ = handle.request_shutdown();
            }
        }
        _ => {}
    });
    if let Some(session) = shell_lifecycle_session {
        session
            .shutdown_and_join()
            .expect("Runtime lifecycle worker should stop without residuals");
    }
    if exit_code != 0 {
        std::process::exit(exit_code);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_runtime_assets_are_embedded_and_the_contract_is_executable() {
        assert!(!STARTUP_HTML.is_empty());
        assert!(!STARTUP_STYLES.is_empty());
        assert!(!APP_SCRIPT.is_empty());
        assert!(!LIFECYCLE_SCRIPT.is_empty());
        assert!(!LAYOUT_SCRIPT.is_empty());
        assert!(!LAYOUT_CONTROLLER_SCRIPT.is_empty());
        assert!(!HIT_REGIONS_SCRIPT.is_empty());
        assert!(!INPUT_FOCUS_SCRIPT.is_empty());
        let contract = layout_contract().expect("shared layout contract must parse");
        contract
            .validate()
            .expect("shared layout contract must validate");
    }

    #[test]
    fn deferred_drag_is_finished_only_by_the_next_layout() {
        let mut session = WindowGeometrySession::default();

        session.begin_deferred_drag();

        assert!(session.is_deferred_drag_pending());
        session.finish_deferred_drag();
        assert!(!session.is_deferred_drag_pending());
    }

    #[test]
    fn development_runtime_request_resolves_the_repository_without_a_fixed_absolute_path() {
        let request = development_runtime_request();
        let root = request
            .explicit_development_root
            .expect("development root should be explicit");
        assert!(root.join("app/core_host/__main__.py").is_file());
        assert!(root.join("desktop/src-tauri/runtime-layouts").is_dir());
        assert_eq!(request.assistant_root, root);
    }
}
