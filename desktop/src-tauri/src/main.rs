#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod character_presentation;
#[allow(dead_code)] // WP-2-02 allowlisted chat Gateway and terminal registry.
mod core_host_gateway;
#[allow(dead_code)] // Production wiring is activated incrementally across Phase 1C.
mod core_host_protocol;
#[allow(dead_code)] // WP-2-01 generation-scoped concurrent transport owner.
mod core_host_router;
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
mod product_shell;
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
use tauri::{Emitter, Manager, State, WebviewWindow};
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
const SETTINGS_HTML: &str = include_str!("../../frontend/settings/index.html");
const SETTINGS_STYLES: &str = include_str!("../../frontend/settings/styles.css");
const SETTINGS_SCRIPT: &str = include_str!("../../frontend/settings/settings.js");
const SETTINGS_CAPABILITY_SCRIPT: &str =
    include_str!("../../frontend/settings/capability-shell.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");
const VISIBILITY_PROBE_HIDDEN_DURATION: std::time::Duration = std::time::Duration::from_millis(220);
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
    let hit_regions = apply_native_pet_surface(&window, &contract, &application)?;
    session.portrait_anchor = Some(application.portrait_anchor);
    session.state = Some(state);
    session.applied_revision = revision;
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

fn apply_native_pet_surface(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let backend = NativeWindowInteractionBackend;
    backend
        .prepare_window(window)
        .map_err(|error| error.to_string())?;
    backend
        .apply_bounds(window, &application.physical_placement)
        .map_err(|error| error.to_string())?;
    let hit_regions = apply_native_interaction_region(window, contract, application)?;
    window
        .show()
        .map_err(|error| format!("failed to show pet window: {error}"))?;
    Ok(hit_regions)
}

fn prepare_initial_pet_window(window: &WebviewWindow) -> Result<(), String> {
    let contract = layout_contract()?;
    let monitor = target_monitor(window, None)?;
    // Revision zero is a native bootstrap only. The frontend owns revision one
    // and the first committed WindowGeometrySession state after WebView startup.
    let application =
        apply_window_layout(&contract, PresentationState::Product, 0, &monitor, None)?;
    apply_native_pet_surface(window, &contract, &application)?;
    Ok(())
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
fn show_pet_context_menu(
    window: WebviewWindow,
    surface_x: f64,
    surface_y: f64,
    popup_x: f64,
    popup_y: f64,
) -> Result<(), String> {
    if !surface_x.is_finite() || !surface_y.is_finite() {
        return Err("PRODUCT_MENU_REQUEST_REJECTED".to_string());
    }
    let regions =
        window_interaction::logical_hit_regions(&layout_contract()?, PresentationState::Product)?;
    let point = [surface_x.floor() as i32, surface_y.floor() as i32];
    let allowed = regions
        .drag
        .first()
        .is_some_and(|portrait| portrait.contains(point));
    if !allowed {
        return Err("PRODUCT_MENU_SURFACE_REJECTED".to_string());
    }
    product_shell::show_product_menu(&window, popup_x, popup_y)
}

#[tauri::command]
fn probe_pet_visibility(window: WebviewWindow) -> Result<(), String> {
    NativeWindowInteractionBackend
        .set_visible(&window, false)
        .map_err(|error| error.to_string())?;

    let delayed_window = window.clone();
    std::thread::Builder::new()
        .name("pet-visibility-probe".to_string())
        .spawn(move || {
            std::thread::sleep(VISIBILITY_PROBE_HIDDEN_DURATION);
            let restore_window = delayed_window.clone();
            if let Err(error) = delayed_window.run_on_main_thread(move || {
                if let Err(error) = NativeWindowInteractionBackend.set_visible(&restore_window, true)
                {
                    eprintln!("failed to restore pet visibility probe: {error}");
                }
            }) {
                eprintln!("failed to schedule pet visibility restoration: {error}");
                if let Err(recovery_error) =
                    NativeWindowInteractionBackend.set_visible(&delayed_window, true)
                {
                    eprintln!(
                        "failed to recover pet visibility after scheduling error: {recovery_error}"
                    );
                }
            }
        })
        .map_err(|error| {
            let recovery = NativeWindowInteractionBackend.set_visible(&window, true);
            match recovery {
                Ok(()) => format!("failed to start pet visibility timer: {error}"),
                Err(recovery_error) => format!(
                    "failed to start pet visibility timer ({error}) and immediate recovery failed ({recovery_error})"
                ),
            }
        })?;
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
fn current_character_presentation(
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
) -> Result<character_presentation::FrontendCharacterPresentation, String> {
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?;
    let generation_id = handle
        .current_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())?;

    #[cfg(debug_assertions)]
    if std::env::var("SAKURA_WP_3_03_ACCEPTANCE").ok().as_deref() == Some("1") {
        if let Ok(character_id) = std::env::var("SAKURA_WP_3_03_ACCEPTANCE_CHARACTER") {
            if matches!(character_id.as_str(), "Sakura" | "N.A.V.I.") {
                let presentation =
                    character_presentation::presentation_from_manifest_for_acceptance(
                        &development_runtime_request().assistant_root,
                        &character_id,
                        &generation_id,
                    )?;
                return resources.activate(presentation, &generation_id);
            }
        }
    }

    let value = handle
        .character_presentation()
        .map_err(str::to_string)?
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())?;
    let presentation =
        character_presentation::CharacterPresentation::from_value(&value, &generation_id)?;
    resources.activate(presentation, &generation_id)
}

#[tauri::command]
fn wp_3_03_acceptance_enabled() -> bool {
    cfg!(debug_assertions)
        && std::env::var("SAKURA_WP_3_03_ACCEPTANCE").ok().as_deref() == Some("1")
}

fn character_protocol_response(
    context: tauri::UriSchemeContext<'_, tauri::Wry>,
    request: tauri::http::Request<Vec<u8>>,
) -> tauri::http::Response<Vec<u8>> {
    use tauri::http::{header, Method, StatusCode};

    let fail = |status: StatusCode, code: &str| {
        tauri::http::Response::builder()
            .status(status)
            .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
            .header(header::CACHE_CONTROL, "no-store")
            .header("X-Content-Type-Options", "nosniff")
            .body(code.as_bytes().to_vec())
            .expect("static character protocol response")
    };
    if request.method() != Method::GET || request.uri().query().is_some() {
        return fail(
            StatusCode::BAD_REQUEST,
            "CHARACTER_RESOURCE_REQUEST_REJECTED",
        );
    }
    let segments: Vec<_> = request.uri().path().trim_matches('/').split('/').collect();
    if segments.len() != 3
        || segments[0] != "v1"
        || segments[1].is_empty()
        || segments[2].is_empty()
        || segments.iter().any(|segment| segment.contains('%'))
    {
        return fail(
            StatusCode::BAD_REQUEST,
            "CHARACTER_RESOURCE_REQUEST_REJECTED",
        );
    }
    let lifecycle = context.app_handle().state::<ShellLifecycleState>();
    let Some(handle) = lifecycle.handle.as_ref() else {
        return fail(
            StatusCode::SERVICE_UNAVAILABLE,
            "CHARACTER_RESOURCE_NOT_READY",
        );
    };
    let current_generation = match handle.current_generation_id() {
        Ok(Some(value)) => value,
        _ => return fail(StatusCode::GONE, "CHARACTER_RESOURCE_GENERATION_STALE"),
    };
    let resources = context
        .app_handle()
        .state::<character_presentation::CharacterPresentationState>();
    match resources.load_active_resource(segments[1], segments[2], &current_generation) {
        Ok(resource) => tauri::http::Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "image/png")
            .header(
                header::CONTENT_LENGTH,
                resource.metadata.byte_length.to_string(),
            )
            .header(header::CACHE_CONTROL, "no-store, max-age=0")
            .header("X-Content-Type-Options", "nosniff")
            .body(resource.bytes)
            .expect("validated character resource response"),
        Err(code) => {
            let status = if code.contains("GENERATION") {
                StatusCode::GONE
            } else if code.contains("UNKNOWN") || code.contains("NOT_FOUND") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            };
            fail(status, &code)
        }
    }
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
    request_app_exit(&app_handle, &lifecycle).map_err(|_| "APP_EXIT_REQUEST_FAILED")
}

#[tauri::command]
fn close_pet_window(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    request_app_exit(&app_handle, &lifecycle)
}

fn finish_app_exit(
    app_handle: &tauri::AppHandle,
    lifecycle: &ShellLifecycleState,
) -> Result<(), String> {
    if let Some(handle) = &lifecycle.handle {
        handle.request_shutdown().map_err(str::to_string)?;
    }
    app_handle.exit(0);
    Ok(())
}

fn request_app_exit(
    app_handle: &tauri::AppHandle,
    lifecycle: &ShellLifecycleState,
) -> Result<(), String> {
    let Some(settings) = app_handle.get_webview_window(product_shell::SETTINGS_WINDOW_LABEL) else {
        return finish_app_exit(app_handle, lifecycle);
    };
    let state = app_handle.state::<product_shell::ProductShellState>();
    if !state.begin_exit()? {
        settings.show().map_err(|error| error.to_string())?;
        settings.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    settings
        .emit(product_shell::SETTINGS_EXIT_REQUESTED_EVENT, ())
        .map_err(|error| error.to_string())?;

    let timeout_app = app_handle.clone();
    std::thread::Builder::new()
        .name("settings-exit-timeout".to_string())
        .spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(5));
            let check_app = timeout_app.clone();
            let _ = timeout_app.run_on_main_thread(move || {
                let state = check_app.state::<product_shell::ProductShellState>();
                if state.exit_pending().unwrap_or(false) {
                    let _ = state.resolve_exit();
                    if let Some(window) =
                        check_app.get_webview_window(product_shell::SETTINGS_WINDOW_LABEL)
                    {
                        let _ = window.emit(product_shell::SETTINGS_EXIT_TIMEOUT_EVENT, ());
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            });
        })
        .map_err(|error| format!("failed to start bounded settings exit wait: {error}"))?;
    Ok(())
}

#[tauri::command]
fn resolve_settings_exit(
    window: WebviewWindow,
    discard: bool,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    shell: State<'_, product_shell::ProductShellState>,
) -> Result<(), String> {
    if window.label() != product_shell::SETTINGS_WINDOW_LABEL {
        return Err("SETTINGS_WINDOW_REQUIRED".to_string());
    }
    if !shell.resolve_exit()? {
        return Ok(());
    }
    if !discard {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    shell.authorize_close()?;
    window.close().map_err(|error| error.to_string())?;
    if let Some(handle) = &lifecycle.handle {
        handle.request_shutdown().map_err(str::to_string)?;
    }
    shell.authorize_app_exit()?;
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
        SETTINGS_HTML.len(),
        SETTINGS_STYLES.len(),
        SETTINGS_SCRIPT.len(),
        SETTINGS_CAPABILITY_SCRIPT.len(),
    );

    let acceptance_mode = std::env::var_os("SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY").is_some();
    let runtime_request = development_runtime_request();
    let character_resource_root = runtime_request.assistant_root.clone();
    let shell_lifecycle_session =
        (!acceptance_mode).then(|| shell_lifecycle::ShellLifecycleSession::start(runtime_request));
    let shell_lifecycle_handle = shell_lifecycle_session
        .as_ref()
        .map(shell_lifecycle::ShellLifecycleSession::handle);

    let app = tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .manage(product_shell::ProductShellState::default())
        .manage(ShellLifecycleState {
            handle: shell_lifecycle_handle.clone(),
        })
        .manage(character_presentation::CharacterPresentationState::new(
            character_resource_root,
        ))
        .register_uri_scheme_protocol(
            character_presentation::CHARACTER_PROTOCOL,
            character_protocol_response,
        )
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .ok_or("main pet window was not created")?;
            prepare_initial_pet_window(&window)?;
            Ok(())
        })
        .on_menu_event(|app, event| {
            let Some(action) = product_shell::ProductMenuAction::from_id(event.id().as_ref())
            else {
                return;
            };
            let result = match action {
                product_shell::ProductMenuAction::TogglePet => app
                    .get_webview_window("main")
                    .ok_or_else(|| "PET_WINDOW_UNAVAILABLE".to_string())
                    .and_then(|window| {
                        let visible = window.is_visible().map_err(|error| error.to_string())?;
                        if visible {
                            window.hide()
                        } else {
                            window.show()
                        }
                        .map_err(|error| error.to_string())
                    }),
                product_shell::ProductMenuAction::OpenSettings => {
                    product_shell::show_or_focus_settings(app)
                }
                product_shell::ProductMenuAction::ExitApp => {
                    let lifecycle = app.state::<ShellLifecycleState>();
                    request_app_exit(app, &lifecycle)
                }
            };
            if let Err(error) = result {
                product_shell::emit_product_menu_error(app, error);
            }
        })
        .on_window_event(|window, event| {
            if window.label() != product_shell::SETTINGS_WINDOW_LABEL {
                return;
            }
            let state = window.state::<product_shell::ProductShellState>();
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    if !state.consume_close_authorization().unwrap_or(false) {
                        api.prevent_close();
                        let _ = window.emit(product_shell::SETTINGS_CLOSE_REQUESTED_EVENT, ());
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    let _ = state.window_destroyed();
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            apply_pet_layout,
            start_pet_drag,
            show_pet_context_menu,
            probe_pet_visibility,
            close_pet_window,
            collect_native_diagnostics,
            runtime_lifecycle_snapshot,
            current_character_presentation,
            wp_3_03_acceptance_enabled,
            retry_core,
            exit_runtime,
            product_shell::settings_capability_manifest,
            product_shell::resolve_settings_close,
            resolve_settings_exit
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

    let exit_code = app.run_return(move |app_handle, event| match event {
        tauri::RunEvent::Exit => {
            if let Some(handle) = &shell_lifecycle_handle {
                let _ = handle.request_shutdown();
            }
        }
        tauri::RunEvent::ExitRequested { api, .. } => {
            let shell = app_handle.state::<product_shell::ProductShellState>();
            if !shell.consume_app_exit_authorization().unwrap_or(false) {
                if app_handle
                    .get_webview_window(product_shell::SETTINGS_WINDOW_LABEL)
                    .is_some()
                {
                    api.prevent_exit();
                    let lifecycle = app_handle.state::<ShellLifecycleState>();
                    if let Err(error) = request_app_exit(app_handle, &lifecycle) {
                        product_shell::emit_product_menu_error(app_handle, error);
                    }
                } else if let Some(handle) = &shell_lifecycle_handle {
                    let _ = handle.request_shutdown();
                }
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
        assert!(!SETTINGS_HTML.is_empty());
        assert!(!SETTINGS_STYLES.is_empty());
        assert!(!SETTINGS_SCRIPT.is_empty());
        assert!(!SETTINGS_CAPABILITY_SCRIPT.is_empty());
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
    fn visibility_probe_keeps_a_perceptible_native_owned_hidden_interval() {
        assert_eq!(VISIBILITY_PROBE_HIDDEN_DURATION.as_millis(), 220);
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
