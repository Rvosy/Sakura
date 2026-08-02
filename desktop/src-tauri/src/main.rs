#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod character_appearance;
mod character_presentation;
mod chat_bridge;
mod chat_settings;
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
mod ui_config;
mod window_geometry;
mod window_interaction;

use std::sync::Mutex;

use platform::{
    InstanceLockAcquire, InstanceLockBackend, NativeDiagnosticsBackend,
    NativeDiagnosticsBackendImpl, NativeDiagnosticsRequest, NativeWindowInteractionBackend,
    WindowInteractionBackend, SHARED_INSTANCE_ID,
};
use serde::Serialize;
use serde_json::{json, Value};
use shared_instance::NativeInstanceLockBackend;
use tauri::{Emitter, Manager, State, WebviewWindow};
use window_geometry::{
    apply_window_layout, ControlSurfaceLayout, LayoutApplication, LayoutContract,
    LayoutRevisionGuard, MonitorDescriptor, PhysicalRect, PresentationState,
};

const STARTUP_HTML: &str = include_str!("../../frontend/index.html");
const STARTUP_STYLES: &str = include_str!("../../frontend/styles.css");
const APP_SCRIPT: &str = include_str!("../../frontend/app.js");
const LIFECYCLE_SCRIPT: &str = include_str!("../../frontend/lifecycle.js");
const LAYOUT_SCRIPT: &str = include_str!("../../frontend/pet/layout.js");
const LAYOUT_CONTROLLER_SCRIPT: &str = include_str!("../../frontend/pet/layout-controller.js");
const HIT_REGIONS_SCRIPT: &str = include_str!("../../frontend/pet/hit-regions.js");
const INPUT_FOCUS_SCRIPT: &str = include_str!("../../frontend/pet/input-focus.js");
const APPEARANCE_SCRIPT: &str = include_str!("../../frontend/pet/appearance.js");
const SETTINGS_HTML: &str = include_str!("../../frontend/settings/index.html");
const SETTINGS_STYLES: &str = include_str!("../../frontend/settings/styles.css");
const SETTINGS_SCRIPT: &str = include_str!("../../frontend/settings/settings.js");
const SETTINGS_CAPABILITY_SCRIPT: &str =
    include_str!("../../frontend/settings/capability-shell.js");
const SETTINGS_APPEARANCE_SCRIPT: &str =
    include_str!("../../frontend/settings/appearance-runtime.js");
const SETTINGS_PROVIDER_MODEL_SCRIPT: &str =
    include_str!("../../frontend/settings/provider-model-runtime.js");
const SETTINGS_CLOSE_FLOW_SCRIPT: &str = include_str!("../../frontend/settings/close-flow.js");
const SETTINGS_CHAT_TIMING_SCRIPT: &str =
    include_str!("../../frontend/settings/chat-timing-runtime.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");
const VISIBILITY_PROBE_HIDDEN_DURATION: std::time::Duration = std::time::Duration::from_millis(220);
const ALREADY_RUNNING_TITLE: &str = "Sakura 已在运行";
const ALREADY_RUNNING_BODY: &str =
    "另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。";
#[cfg(debug_assertions)]
const WP_3U_02_ACCEPTANCE_FAILURE_ROOT_ENV: &str = "SAKURA_WP_3U_02_ACCEPTANCE_FAILURE_ROOT";
#[cfg(debug_assertions)]
const WP_3U_02_ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-3u-02-";

struct WindowGeometrySession {
    revision: LayoutRevisionGuard,
    portrait_anchor: Option<window_geometry::PhysicalPoint>,
    state: Option<PresentationState>,
    applied_revision: u64,
    deferred_drag_pending: bool,
    portrait_alpha_mask: Option<character_presentation::PortraitAlphaMask>,
    portrait_hit_generation: Option<String>,
    portrait_hit_key: Option<String>,
    portrait_hit_revision: u64,
    portrait_hit_relaxed: bool,
    control_surface_preview_active: bool,
    control_surface_preview_revision: u64,
    portrait_scale_percent: u16,
    context_menu_open: bool,
    control_surface: Option<ControlSurfaceLayout>,
    hit_regions: Option<window_interaction::PhysicalHitRegions>,
}

impl Default for WindowGeometrySession {
    fn default() -> Self {
        Self {
            revision: LayoutRevisionGuard::default(),
            portrait_anchor: None,
            state: None,
            applied_revision: 0,
            deferred_drag_pending: false,
            portrait_alpha_mask: None,
            portrait_hit_generation: None,
            portrait_hit_key: None,
            portrait_hit_revision: 0,
            portrait_hit_relaxed: false,
            control_surface_preview_active: false,
            control_surface_preview_revision: 0,
            portrait_scale_percent: 100,
            context_menu_open: false,
            control_surface: None,
            hit_regions: None,
        }
    }
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

    /// Returns true only when the caller must expand the native region before activating preview.
    fn request_control_surface_preview(&mut self, revision: u64) -> bool {
        if revision < self.control_surface_preview_revision
            || (!self.control_surface_preview_active
                && revision == self.control_surface_preview_revision)
        {
            return false;
        }
        if self.control_surface_preview_active {
            self.control_surface_preview_revision = revision;
            return false;
        }
        true
    }

    fn activate_control_surface_preview(&mut self, revision: u64) {
        self.control_surface_preview_active = true;
        self.control_surface_preview_revision = revision;
    }

    fn can_end_control_surface_preview(&self, revision: u64) -> bool {
        self.control_surface_preview_active && revision == self.control_surface_preview_revision
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

fn compute_pet_window_layout(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<window_geometry::PhysicalPoint>,
    portrait_scale_percent: u16,
) -> Result<LayoutApplication, String> {
    let visible_surface_bounds = window_interaction::logical_visible_surface_bounds(
        contract,
        state,
        portrait_scale_percent,
    )?;
    apply_window_layout(
        contract,
        state,
        revision,
        monitor,
        existing_anchor,
        visible_surface_bounds,
    )
}

#[tauri::command]
fn current_pet_layout_revision(
    window: WebviewWindow,
    session: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<u64, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    session
        .lock()
        .map(|session| session.applied_revision)
        .map_err(|_| "window geometry state is unavailable".to_string())
}

#[tauri::command]
fn apply_pet_layout(
    window: WebviewWindow,
    state: PresentationState,
    revision: u64,
    control_surface: Option<ControlSurfaceLayout>,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<PetLayoutApplication, String> {
    let contract = layout_contract()?;
    if let Some(surface) = control_surface.as_ref() {
        contract.validate_control_surface(state, surface)?;
    }
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
    let application = compute_pet_window_layout(
        &contract,
        state,
        revision,
        &monitor,
        requested_anchor,
        session.portrait_scale_percent,
    )?;

    if session.is_deferred_drag_pending() {
        session.finish_deferred_drag();
    }
    let hit_regions = apply_native_pet_surface(
        &window,
        &contract,
        &application,
        control_surface.as_ref(),
        session.portrait_alpha_mask.as_ref(),
        session.portrait_scale_percent,
        session.context_menu_open
            || session.portrait_hit_relaxed
            || session.control_surface_preview_active,
    )?;
    session.portrait_anchor = Some(application.portrait_anchor);
    session.state = Some(state);
    session.applied_revision = revision;
    session.control_surface = control_surface;
    session.hit_regions = Some(hit_regions.clone());
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
    })
}

fn apply_native_interaction_region(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
    keep_full_hit_region: bool,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let logical = window_interaction::logical_hit_regions_with_control_surface(
        contract,
        application.state,
        portrait_alpha_mask.map(character_presentation::PortraitAlphaMask::source_size),
        portrait_scale_percent,
        control_surface,
    )?;
    let mut physical = window_interaction::scale_hit_regions(
        &logical,
        application.scale_factor * application.content_scale,
    )?;
    physical.portrait_alpha_mask = portrait_alpha_mask.cloned();
    if !keep_full_hit_region {
        apply_precise_hit_regions(window, &physical)?;
    }
    Ok(physical)
}

fn apply_precise_hit_regions(
    window: &WebviewWindow,
    physical: &window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    let backend = NativeWindowInteractionBackend;
    if let Err(error) = backend.apply_hit_regions(window, physical) {
        return match backend.restore_full_hit_region(window) {
            Ok(()) => Err(format!(
                "failed to apply native hit regions; restored full-window interaction: {error}"
            )),
            Err(recovery_error) => Err(format!(
                "failed to apply native hit regions ({error}) and recovery failed ({recovery_error})"
            )),
        };
    }
    Ok(())
}

fn reapply_current_pet_hit_region(window: &WebviewWindow) -> Result<(), String> {
    let session = window.state::<Mutex<WindowGeometrySession>>();
    let geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let keep_full_hit_region = geometry.context_menu_open
        || geometry.portrait_hit_relaxed
        || geometry.control_surface_preview_active;
    let hit_regions = geometry
        .hit_regions
        .clone()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    drop(geometry);

    if keep_full_hit_region {
        NativeWindowInteractionBackend
            .restore_full_hit_region(window)
            .map_err(|error| error.to_string())
    } else {
        apply_precise_hit_regions(window, &hit_regions)
    }
}

fn apply_native_pet_surface(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
    keep_full_hit_region: bool,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let backend = NativeWindowInteractionBackend;
    backend
        .prepare_window(window)
        .map_err(|error| error.to_string())?;
    backend
        .apply_bounds(window, &application.physical_placement)
        .map_err(|error| error.to_string())?;
    let hit_regions = apply_native_interaction_region(
        window,
        contract,
        application,
        control_surface,
        portrait_alpha_mask,
        portrait_scale_percent,
        keep_full_hit_region,
    )?;
    Ok(hit_regions)
}

fn prepare_initial_pet_window(window: &WebviewWindow) -> Result<(), String> {
    let contract = layout_contract()?;
    let monitor = target_monitor(window, None)?;
    // Revision zero is a native bootstrap only. The frontend owns revision one
    // and the first committed WindowGeometrySession state after WebView startup.
    let application = compute_pet_window_layout(
        &contract,
        PresentationState::Product,
        0,
        &monitor,
        None,
        100,
    )?;
    apply_native_pet_surface(window, &contract, &application, None, None, 100, false)?;
    Ok(())
}

#[tauri::command]
fn reveal_pet_window(
    window: WebviewWindow,
    session: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let layout_ready = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?
        .state
        .is_some();
    if !layout_ready {
        return Err("PET_LAYOUT_NOT_READY".to_string());
    }
    window
        .show()
        .map_err(|error| format!("failed to reveal pet window: {error}"))?;
    reapply_current_pet_hit_region(&window)?;
    product_shell::sync_product_tray_visibility(window.app_handle(), true)
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
    let application = compute_pet_window_layout(
        &contract,
        state,
        session.applied_revision,
        &monitor,
        Some(requested_anchor),
        session.portrait_scale_percent,
    )?;
    NativeWindowInteractionBackend
        .apply_bounds(&window, &application.physical_placement)
        .map_err(|error| error.to_string())?;
    let hit_regions = apply_native_interaction_region(
        &window,
        &contract,
        &application,
        session.control_surface.as_ref(),
        session.portrait_alpha_mask.as_ref(),
        session.portrait_scale_percent,
        session.context_menu_open
            || session.portrait_hit_relaxed
            || session.control_surface_preview_active,
    )?;
    session.portrait_anchor = Some(application.portrait_anchor);
    session.hit_regions = Some(hit_regions.clone());
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
fn open_pet_context_menu(
    window: WebviewWindow,
    surface_x: f64,
    surface_y: f64,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
    subtitle: tauri::State<'_, chat_settings::SubtitleLanguageState>,
) -> Result<product_shell::ProductMenuCapabilityManifest, String> {
    if window.label() != "main" || !surface_x.is_finite() || !surface_y.is_finite() {
        return Err("PRODUCT_MENU_REQUEST_REJECTED".to_string());
    }
    let mut geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let state = geometry
        .state
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let regions = window_interaction::logical_hit_regions_with_control_surface(
        &layout_contract()?,
        state,
        geometry
            .portrait_alpha_mask
            .as_ref()
            .map(character_presentation::PortraitAlphaMask::source_size),
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
    )?;
    let point = [surface_x.floor() as i32, surface_y.floor() as i32];
    if !window_interaction::contains_visible_point(&regions, point) {
        return Err("PRODUCT_MENU_SURFACE_REJECTED".to_string());
    }
    if !geometry.context_menu_open {
        NativeWindowInteractionBackend
            .restore_full_hit_region(&window)
            .map_err(|error| error.to_string())?;
        geometry.context_menu_open = true;
    }
    Ok(product_shell::product_menu_capability_manifest(
        subtitle.get()?.is_chinese(),
    ))
}

fn close_pet_context_menu_surface(
    window: &WebviewWindow,
    session: &Mutex<WindowGeometrySession>,
) -> Result<(), String> {
    let mut geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.context_menu_open {
        return Ok(());
    }
    geometry.context_menu_open = false;
    if geometry.portrait_hit_relaxed || geometry.control_surface_preview_active {
        return Ok(());
    }
    let hit_regions = geometry
        .hit_regions
        .clone()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    drop(geometry);
    apply_precise_hit_regions(window, &hit_regions)
}

#[tauri::command]
fn close_pet_context_menu(
    window: WebviewWindow,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    close_pet_context_menu_surface(&window, session.inner())
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
                let restored = NativeWindowInteractionBackend
                    .set_visible(&restore_window, true)
                    .map_err(|error| error.to_string())
                    .and_then(|_| reapply_current_pet_hit_region(&restore_window));
                if let Err(error) = restored {
                    eprintln!("failed to restore pet visibility probe: {error}");
                }
            }) {
                eprintln!("failed to schedule pet visibility restoration: {error}");
                let recovery = NativeWindowInteractionBackend
                    .set_visible(&delayed_window, true)
                    .map_err(|error| error.to_string())
                    .and_then(|_| reapply_current_pet_hit_region(&delayed_window));
                if let Err(recovery_error) = recovery {
                    eprintln!(
                        "failed to recover pet visibility after scheduling error: {recovery_error}"
                    );
                }
            }
        })
        .map_err(|error| {
            let recovery = NativeWindowInteractionBackend
                .set_visible(&window, true)
                .map_err(|error| error.to_string())
                .and_then(|_| reapply_current_pet_hit_region(&window));
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
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
    app_handle: tauri::AppHandle,
) -> Result<shell_lifecycle::ShellLifecyclePublication, &'static str> {
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or("LIFECYCLE_COMMAND_UNAVAILABLE")?;
    let publication = handle.snapshot()?;
    let generation_id = handle.available_generation_id()?;
    if let Some(rollback) = appearance
        .cancel_if_generation_changed(generation_id.as_deref())
        .map_err(|_| "LIFECYCLE_APPEARANCE_ROLLBACK_FAILED")?
    {
        emit_appearance(&app_handle, rollback)
            .map_err(|_| "LIFECYCLE_APPEARANCE_ROLLBACK_FAILED")?;
    }
    Ok(publication)
}

#[tauri::command]
async fn chat_send(
    window: WebviewWindow,
    payload: chat_bridge::ChatSendRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<chat_bridge::ChatSendPublication, String> {
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
    let pending = handle
        .chat_bridge()?
        .send(window.label(), payload.message)?;
    tauri::async_runtime::spawn_blocking(move || pending.wait())
        .await
        .map_err(|_| "CHAT_DISPATCH_ABORTED".to_string())?
}

#[tauri::command]
async fn chat_cancel(
    window: WebviewWindow,
    payload: chat_bridge::ChatCancelRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<chat_bridge::ChatCancelPublication, String> {
    let bridge = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHAT_BRIDGE_UNAVAILABLE".to_string())?
        .chat_bridge()?;
    let label = window.label().to_string();
    tauri::async_runtime::spawn_blocking(move || {
        bridge.cancel(&label, &payload.operation_id, &payload.cancel_handle)
    })
    .await
    .map_err(|_| "CHAT_CANCEL_ABORTED".to_string())?
}

#[tauri::command]
fn current_chat_presentation_timing(
    window: WebviewWindow,
    timing: State<'_, chat_settings::ChatPresentationTimingState>,
) -> Result<chat_settings::ChatPresentationTiming, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    timing.get()
}

#[tauri::command]
fn current_subtitle_language(
    window: WebviewWindow,
    subtitle: State<'_, chat_settings::SubtitleLanguageState>,
) -> Result<chat_settings::SubtitleLanguage, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    subtitle.get()
}

#[tauri::command]
fn settings_chat_presentation_timing_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    timing: State<'_, chat_settings::ChatPresentationTimingState>,
) -> Result<chat_settings::ChatPresentationTimingSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    timing.snapshot(shell.generation()?)
}

#[tauri::command]
fn settings_chat_presentation_timing_save(
    window: WebviewWindow,
    window_generation: u64,
    values: chat_settings::ChatPresentationTiming,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    timing: State<'_, chat_settings::ChatPresentationTimingState>,
) -> Result<chat_settings::ChatPresentationTiming, String> {
    product_shell::validate_settings_window(&window)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    let saved = timing.save(values)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    app_handle
        .emit_to("main", chat_settings::CHAT_TIMING_CHANGED_EVENT, saved)
        .map_err(|error| format!("CHAT_TIMING_PUBLICATION_FAILED: {error}"))?;
    Ok(saved)
}

#[tauri::command]
fn current_character_presentation(
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
) -> Result<character_presentation::FrontendCharacterPresentation, String> {
    load_current_character_presentation(&lifecycle, &resources)
}

fn load_current_character_presentation(
    lifecycle: &ShellLifecycleState,
    resources: &character_presentation::CharacterPresentationState,
) -> Result<character_presentation::FrontendCharacterPresentation, String> {
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?;
    let generation_id = handle
        .available_generation_id()
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

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SettingsCharacterAppearanceSnapshot {
    schema_version: u32,
    window_generation: u64,
    presentation: character_presentation::FrontendCharacterPresentation,
    appearance: character_appearance::AppearancePublication,
    limits: character_appearance::AppearanceLimits,
}

fn emit_appearance(
    app_handle: &tauri::AppHandle,
    publication: character_appearance::AppearancePublication,
) -> Result<(), String> {
    app_handle
        .emit_to(
            "main",
            character_appearance::APPEARANCE_CHANGED_EVENT,
            publication,
        )
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn current_character_appearance(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<character_appearance::AppearancePublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    appearance.persisted(&presentation.presentation)
}

#[tauri::command]
fn settings_character_appearance_get(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<SettingsCharacterAppearanceSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let window_generation = shell.generation()?;
    let (publication, cancelled) =
        appearance.open(window_generation, &presentation.presentation)?;
    if let Some(cancelled) = cancelled {
        emit_appearance(&app_handle, cancelled)?;
    }
    sync_settings_window_appearance_background(&window, &publication)?;
    appearance.mark_settings_background_synced(&publication.values)?;
    Ok(SettingsCharacterAppearanceSnapshot {
        schema_version: 1,
        window_generation,
        presentation,
        appearance: publication,
        limits: character_appearance::AppearanceLimits::default(),
    })
}

#[tauri::command]
fn settings_character_appearance_preview(
    window: WebviewWindow,
    values: character_appearance::AppearanceValues,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<character_appearance::AppearancePublication, String> {
    product_shell::validate_settings_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let (publication, settings_background_changed) =
        appearance.preview(shell.generation()?, &presentation.presentation, values)?;
    // Layout/font previews are high-frequency. Avoid a redundant native background update on
    // every slider tick; on Windows that call otherwise sits directly on the visual preview path.
    if settings_background_changed {
        sync_settings_window_appearance_background(&window, &publication)?;
        appearance.mark_settings_background_synced(&publication.values)?;
    }
    emit_appearance(&app_handle, publication.clone())?;
    Ok(publication)
}

#[tauri::command]
fn settings_character_appearance_save(
    window: WebviewWindow,
    values: character_appearance::AppearanceValues,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<character_appearance::AppearancePublication, String> {
    product_shell::validate_settings_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let publication = match appearance.save(shell.generation()?, &presentation.presentation, values)
    {
        Ok(publication) => publication,
        Err(error) => {
            if let Some(rollback) = appearance.cancel()? {
                emit_appearance(&app_handle, rollback)?;
            }
            return Err(error);
        }
    };
    sync_settings_window_appearance_background(&window, &publication)?;
    appearance.mark_settings_background_synced(&publication.values)?;
    emit_appearance(&app_handle, publication.clone())?;
    Ok(publication)
}

#[tauri::command]
fn settings_character_appearance_cancel_preview(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    if let Some(publication) = appearance.cancel()? {
        sync_settings_window_appearance_background(&window, &publication)?;
        appearance.mark_settings_background_synced(&publication.values)?;
        emit_appearance(&app_handle, publication)?;
    }
    Ok(())
}

fn sync_settings_window_appearance_background(
    window: &WebviewWindow,
    publication: &character_appearance::AppearancePublication,
) -> Result<(), String> {
    let background = publication
        .values
        .theme_tokens
        .get("pageBackground")
        .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
    product_shell::set_settings_window_theme_background(window, background)
}

fn settings_core_handle(
    lifecycle: &State<'_, ShellLifecycleState>,
) -> Result<shell_lifecycle::ShellLifecycleHandle, String> {
    lifecycle
        .handle
        .clone()
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())
}

fn settings_response_payload(response: Value) -> Result<Value, String> {
    if response.get("ok").and_then(Value::as_bool) == Some(true) {
        return response
            .get("payload")
            .cloned()
            .filter(Value::is_object)
            .ok_or_else(|| "SETTINGS_RESPONSE_INVALID".to_string());
    }
    let code = response
        .pointer("/error/code")
        .and_then(Value::as_str)
        .unwrap_or("SETTINGS_REQUEST_FAILED");
    let message = response
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or("设置请求失败。");
    let feature = response
        .pointer("/error/details/feature")
        .and_then(Value::as_str)
        .unwrap_or("");
    let field = response
        .pointer("/error/details/field")
        .and_then(Value::as_str)
        .unwrap_or("");
    Err(format!("{code}|{feature}|{field}|{message}"))
}

async fn dispatch_settings_request(
    handle: shell_lifecycle::ShellLifecycleHandle,
    request_id: Option<String>,
    name: &'static str,
    payload: Value,
    deadline: std::time::Duration,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        handle.settings_request(request_id.as_deref(), name, payload, deadline)
    })
    .await
    .map_err(|_| "SETTINGS_REQUEST_ABORTED".to_string())?
}

fn assert_settings_identity(
    shell: &product_shell::ProductShellState,
    handle: &shell_lifecycle::ShellLifecycleHandle,
    window_generation: u64,
    core_generation_id: &str,
) -> Result<(), String> {
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    let current = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    if current != core_generation_id {
        return Err("SETTINGS_CORE_GENERATION_MISMATCH".to_string());
    }
    Ok(())
}

#[tauri::command]
async fn settings_provider_model_get(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "settings.provider_model.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("window_generation".to_string(), json!(window_generation));
    object.insert("core_generation_id".to_string(), json!(core_generation_id));
    app_handle
        .emit_to(
            "main",
            "sakura://core-generation-changed",
            json!({"generationId": core_generation_id}),
        )
        .map_err(|error| format!("CORE_GENERATION_PUBLICATION_FAILED: {error}"))?;
    Ok(payload)
}

#[tauri::command]
async fn settings_provider_model_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    draft: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "settings.provider_model.save",
        json!({"draft": draft}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    handle.restart().map_err(str::to_string)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_provider_model_probe(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    operation_id: String,
    kind: String,
    profile: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let name = match kind.as_str() {
        "list_models" => "settings.provider_model.list_models",
        "test_connection" => "settings.provider_model.test_connection",
        _ => return Err("SETTINGS_PROBE_KIND_INVALID".to_string()),
    };
    let response = dispatch_settings_request(
        handle.clone(),
        Some(operation_id.clone()),
        name,
        json!({"operation_id": operation_id, "profile": profile}),
        std::time::Duration::from_secs(65),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    settings_response_payload(response)
}

#[tauri::command]
async fn settings_provider_model_cancel(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    operation_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<bool, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "settings.provider_model.cancel",
        json!({"operationId": operation_id}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    Ok(settings_response_payload(response)?
        .get("cancelled")
        .and_then(Value::as_bool)
        .unwrap_or(false))
}

#[tauri::command]
fn begin_control_surface_preview(
    window: WebviewWindow,
    revision: u64,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.request_control_surface_preview(revision) {
        return Ok(());
    }
    // SetWindowRgn is also the visible Win32 clip. Expand it once before WebView geometry starts
    // moving, then leave it untouched for the whole slider burst to avoid compositor tearing.
    NativeWindowInteractionBackend
        .restore_full_hit_region(&window)
        .map_err(|error| error.to_string())?;
    geometry.activate_control_surface_preview(revision);
    Ok(())
}

#[tauri::command]
fn end_control_surface_preview(
    window: WebviewWindow,
    revision: u64,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.can_end_control_surface_preview(revision) {
        return Ok(());
    }
    geometry.control_surface_preview_active = false;
    if geometry.context_menu_open || geometry.portrait_hit_relaxed {
        return Ok(());
    }
    let hit_regions = geometry
        .hit_regions
        .clone()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    if let Err(error) = apply_precise_hit_regions(&window, &hit_regions) {
        // apply_precise_hit_regions recovers to the full region on failure. Keep the preview flag
        // retryable so a later settle (or the next slider burst) can restore the precise mask.
        geometry.control_surface_preview_active = true;
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
fn begin_portrait_scale_preview(
    window: WebviewWindow,
    revision: u64,
    lifecycle: State<'_, ShellLifecycleState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())?;
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let same_generation =
        geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
    if same_generation && revision <= geometry.portrait_hit_revision {
        return Ok(());
    }

    // Scaling the DOM while the previous pixel-perfect Win32 region is still
    // installed clips the portrait until the debounced mask rebuild finishes.
    // Temporarily restoring the rectangular surface is a single cheap native
    // operation; the precise transparent region is restored after settling.
    NativeWindowInteractionBackend
        .restore_full_hit_region(&window)
        .map_err(|error| error.to_string())?;
    if !same_generation {
        geometry.portrait_alpha_mask = None;
        geometry.portrait_hit_key = None;
    }
    geometry.portrait_hit_generation = Some(generation_id);
    geometry.portrait_hit_revision = revision;
    geometry.portrait_hit_relaxed = true;
    Ok(())
}

#[tauri::command]
fn activate_portrait_hit_test(
    window: WebviewWindow,
    portrait_key: String,
    revision: u64,
    portrait_scale_percent: u16,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())?;
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let same_generation =
        geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
    if same_generation
        && (revision < geometry.portrait_hit_revision
            || (revision == geometry.portrait_hit_revision && !geometry.portrait_hit_relaxed))
    {
        return Ok(());
    }
    let cache_matches = same_generation
        && geometry.portrait_hit_key.as_deref() == Some(portrait_key.as_str())
        && geometry.portrait_alpha_mask.is_some();
    if !cache_matches {
        drop(geometry);
        let alpha_mask = resources.active_portrait_alpha_mask(&portrait_key, &generation_id)?;
        geometry = geometry_state
            .lock()
            .map_err(|_| "window geometry state is unavailable".to_string())?;
        let same_generation =
            geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
        if same_generation
            && (revision < geometry.portrait_hit_revision
                || (revision == geometry.portrait_hit_revision && !geometry.portrait_hit_relaxed))
        {
            return Ok(());
        }
        geometry.portrait_alpha_mask = Some(alpha_mask);
        geometry.portrait_hit_generation = Some(generation_id.clone());
        geometry.portrait_hit_key = Some(portrait_key.clone());
    }
    let state = geometry
        .state
        .ok_or_else(|| "pet layout is not ready for portrait hit testing".to_string())?;
    let contract = layout_contract()?;
    let monitor = target_monitor(&window, geometry.portrait_anchor)?;
    let application = compute_pet_window_layout(
        &contract,
        state,
        geometry.applied_revision,
        &monitor,
        geometry.portrait_anchor,
        portrait_scale_percent,
    )?;
    let hit_regions = apply_native_pet_surface(
        &window,
        &contract,
        &application,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        portrait_scale_percent,
        geometry.context_menu_open || geometry.control_surface_preview_active,
    )?;
    geometry.portrait_hit_generation = Some(generation_id);
    geometry.portrait_hit_key = Some(portrait_key);
    geometry.portrait_hit_revision = revision;
    geometry.portrait_hit_relaxed = false;
    geometry.portrait_scale_percent = portrait_scale_percent;
    geometry.portrait_anchor = Some(application.portrait_anchor);
    geometry.hit_regions = Some(hit_regions);
    Ok(())
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
    let current_generation = match handle.available_generation_id() {
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

fn toggle_pet_visibility(app: &tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "PET_WINDOW_UNAVAILABLE".to_string())?;
    let visible = window.is_visible().map_err(|error| error.to_string())?;
    if visible {
        window.hide().map_err(|error| error.to_string())?;
        product_shell::sync_product_tray_visibility(app, false)
    } else {
        NativeWindowInteractionBackend
            .set_visible(&window, true)
            .map_err(|error| error.to_string())?;
        reapply_current_pet_hit_region(&window)?;
        product_shell::sync_product_tray_visibility(app, true)?;
        Ok(())
    }
}

fn handle_product_menu_action(
    app: &tauri::AppHandle,
    action: product_shell::ProductMenuAction,
) -> Result<(), String> {
    match action {
        product_shell::ProductMenuAction::TogglePet => toggle_pet_visibility(app),
        product_shell::ProductMenuAction::ToggleSubtitle => {
            let subtitle = app.state::<chat_settings::SubtitleLanguageState>();
            let language = subtitle.toggle()?;
            app.emit_to(
                "main",
                chat_settings::SUBTITLE_LANGUAGE_CHANGED_EVENT,
                language,
            )
            .map_err(|error| format!("CHAT_SUBTITLE_EVENT_FAILED: {error}"))
        }
        product_shell::ProductMenuAction::OpenSettings => {
            product_shell::show_or_focus_settings(app)
        }
        product_shell::ProductMenuAction::ExitApp => {
            let lifecycle = app.state::<ShellLifecycleState>();
            request_app_exit(app, &lifecycle)
        }
    }
}

fn dispatch_webview_product_menu_action(
    app: tauri::AppHandle,
    action: product_shell::ProductMenuAction,
) -> Result<(), String> {
    std::thread::Builder::new()
        .name("pet-menu-action-dispatch".to_string())
        .spawn(move || {
            let action_app = app.clone();
            if let Err(error) = app.run_on_main_thread(move || {
                if let Err(error) = handle_product_menu_action(&action_app, action) {
                    product_shell::emit_product_menu_error(&action_app, error);
                }
            }) {
                product_shell::emit_product_menu_error(
                    &app,
                    format!("PRODUCT_MENU_ACTION_SCHEDULE_FAILED: {error}"),
                );
            }
        })
        .map(|_| ())
        .map_err(|error| format!("PRODUCT_MENU_ACTION_DISPATCH_FAILED: {error}"))
}

#[tauri::command]
fn activate_pet_context_menu_action(
    window: WebviewWindow,
    action_id: String,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let action = product_shell::ProductMenuAction::from_id(action_id.trim())
        .ok_or_else(|| "PRODUCT_MENU_ACTION_REJECTED".to_string())?;
    close_pet_context_menu_surface(&window, session.inner())?;
    // A synchronous Tauri command runs inside WebView2's WebMessageReceived callback on
    // Windows. Creating the settings WebView from that callback can leave a registered
    // but uninitialized white window. Hop through a worker so run_on_main_thread queues
    // the action for the next event-loop turn, after the invoke response has completed.
    dispatch_webview_product_menu_action(window.app_handle().clone(), action)
}

fn finish_app_exit(
    app_handle: &tauri::AppHandle,
    lifecycle: &ShellLifecycleState,
) -> Result<(), String> {
    let appearance = app_handle.state::<character_appearance::CharacterAppearanceState>();
    if let Some(publication) = appearance.close_session()? {
        emit_appearance(app_handle, publication)?;
    }
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
    window.destroy().map_err(|error| error.to_string())?;
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

fn character_appearance_state(
    repository: ui_config::UiConfigRepository,
) -> character_appearance::CharacterAppearanceState {
    #[cfg(debug_assertions)]
    if let Some(root) = std::env::var_os(WP_3U_02_ACCEPTANCE_FAILURE_ROOT_ENV) {
        let repository_path = wp_3u_02_acceptance_failure_repository_path(root.into())
            .expect("WP-3U-02 acceptance failure root must be an isolated system temp directory");
        return character_appearance::CharacterAppearanceState::new_with_repository_path(
            repository_path,
        );
    }

    character_appearance::CharacterAppearanceState::new_with_repository(repository)
}

#[cfg(debug_assertions)]
fn wp_3u_02_acceptance_failure_repository_path(
    root: std::path::PathBuf,
) -> Result<std::path::PathBuf, String> {
    if !root.is_absolute()
        || root
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("WP-3U-02 acceptance root must be absolute and normalized".to_string());
    }
    let root = root
        .canonicalize()
        .map_err(|_| "WP-3U-02 acceptance root is unavailable".to_string())?;
    let temp = std::env::temp_dir()
        .canonicalize()
        .map_err(|_| "system temp root is unavailable".to_string())?;
    let name = root
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "WP-3U-02 acceptance root name is invalid".to_string())?;
    if root.parent() != Some(temp.as_path())
        || !name.starts_with(WP_3U_02_ACCEPTANCE_DIRECTORY_PREFIX)
    {
        return Err("WP-3U-02 acceptance root is outside its isolated temp scope".to_string());
    }
    let blocker = root.join("blocked");
    let blocker_metadata = std::fs::symlink_metadata(&blocker)
        .map_err(|_| "WP-3U-02 acceptance blocker is unavailable".to_string())?;
    if !blocker_metadata.file_type().is_file() || blocker_metadata.file_type().is_symlink() {
        return Err("WP-3U-02 acceptance blocker must be a regular file".to_string());
    }
    Ok(blocker.join("ui.json"))
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
        APPEARANCE_SCRIPT.len(),
        LAYOUT_CONTRACT_JSON.len(),
        SETTINGS_HTML.len(),
        SETTINGS_STYLES.len(),
        SETTINGS_SCRIPT.len(),
        SETTINGS_CAPABILITY_SCRIPT.len(),
        SETTINGS_APPEARANCE_SCRIPT.len(),
        SETTINGS_PROVIDER_MODEL_SCRIPT.len(),
        SETTINGS_CLOSE_FLOW_SCRIPT.len(),
        SETTINGS_CHAT_TIMING_SCRIPT.len(),
    );

    let acceptance_mode = std::env::var_os("SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY").is_some();
    let runtime_request = development_runtime_request();
    let character_resource_root = runtime_request.assistant_root.clone();
    let mut shell_lifecycle_session =
        (!acceptance_mode).then(|| shell_lifecycle::ShellLifecycleSession::start(runtime_request));
    let shell_lifecycle_handle = shell_lifecycle_session
        .as_ref()
        .map(shell_lifecycle::ShellLifecycleSession::handle);

    let ui_config_repository = ui_config::UiConfigRepository::new(
        character_resource_root.join("data/runtime_v2/config/ui.json"),
    );
    let app = tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .manage(product_shell::ProductShellState::default())
        .manage(ShellLifecycleState {
            handle: shell_lifecycle_handle.clone(),
        })
        .manage(character_presentation::CharacterPresentationState::new(
            character_resource_root.clone(),
        ))
        .manage(character_appearance_state(ui_config_repository.clone()))
        .manage(chat_settings::ChatPresentationTimingState::new(
            ui_config_repository.clone(),
        ))
        .manage(chat_settings::SubtitleLanguageState::new(
            ui_config_repository,
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
            let pet_visible = window.is_visible().map_err(|error| error.to_string())?;
            product_shell::install_product_tray(app, pet_visible)?;
            Ok(())
        })
        .on_menu_event(|app, event| {
            let Some(action) = product_shell::ProductMenuAction::from_id(event.id().as_ref())
            else {
                return;
            };
            if let Err(error) = handle_product_menu_action(app, action) {
                product_shell::emit_product_menu_error(app, error);
            }
        })
        .on_tray_icon_event(|app, event| {
            if event.id().as_ref() != product_shell::PRODUCT_TRAY_ID {
                return;
            }
            if matches!(
                event,
                tauri::tray::TrayIconEvent::Click {
                    button: tauri::tray::MouseButton::Left,
                    button_state: tauri::tray::MouseButtonState::Up,
                    ..
                }
            ) {
                if let Err(error) = toggle_pet_visibility(app) {
                    product_shell::emit_product_menu_error(app, error);
                }
            }
        })
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let lifecycle = window.state::<ShellLifecycleState>();
                    if let Err(error) = request_app_exit(window.app_handle(), &lifecycle) {
                        product_shell::emit_product_menu_error(window.app_handle(), error);
                    }
                }
                return;
            }
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
                    let appearance =
                        window.state::<character_appearance::CharacterAppearanceState>();
                    if let Ok(Some(publication)) = appearance.close_session() {
                        let _ = emit_appearance(window.app_handle(), publication);
                    }
                    let _ = state.window_destroyed();
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            current_pet_layout_revision,
            apply_pet_layout,
            reveal_pet_window,
            start_pet_drag,
            open_pet_context_menu,
            close_pet_context_menu,
            activate_pet_context_menu_action,
            probe_pet_visibility,
            close_pet_window,
            collect_native_diagnostics,
            runtime_lifecycle_snapshot,
            chat_send,
            chat_cancel,
            current_chat_presentation_timing,
            current_subtitle_language,
            current_character_presentation,
            current_character_appearance,
            begin_control_surface_preview,
            end_control_surface_preview,
            begin_portrait_scale_preview,
            activate_portrait_hit_test,
            wp_3_03_acceptance_enabled,
            retry_core,
            exit_runtime,
            product_shell::settings_capability_manifest,
            product_shell::reveal_settings_window,
            settings_character_appearance_get,
            settings_character_appearance_preview,
            settings_character_appearance_save,
            settings_character_appearance_cancel_preview,
            settings_chat_presentation_timing_get,
            settings_chat_presentation_timing_save,
            settings_provider_model_get,
            settings_provider_model_save,
            settings_provider_model_probe,
            settings_provider_model_cancel,
            product_shell::resolve_settings_close,
            resolve_settings_exit
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Sakura Runtime v2 pet geometry gate");

    if let Some(session) = shell_lifecycle_session.as_mut() {
        session
            .bind_chat_projection(app.handle().clone())
            .expect("failed to bind the Runtime v2 chat event projector");
    }

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
            let appearance = app_handle.state::<character_appearance::CharacterAppearanceState>();
            let _ = appearance.close_session();
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
    use std::time::{SystemTime, UNIX_EPOCH};

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
        assert!(!SETTINGS_PROVIDER_MODEL_SCRIPT.is_empty());
        assert!(!SETTINGS_CLOSE_FLOW_SCRIPT.is_empty());
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
    fn product_menu_session_starts_closed_without_stale_hit_regions() {
        let session = WindowGeometrySession::default();
        assert!(!session.context_menu_open);
        assert!(session.hit_regions.is_none());
        assert!(!session.portrait_hit_relaxed);
        assert!(!session.control_surface_preview_active);
        assert_eq!(session.control_surface_preview_revision, 0);
    }

    #[test]
    fn stale_control_surface_settle_cannot_close_a_newer_preview_burst() {
        let mut session = WindowGeometrySession::default();
        assert!(session.request_control_surface_preview(8));
        session.activate_control_surface_preview(8);
        assert!(!session.request_control_surface_preview(7));
        assert!(!session.request_control_surface_preview(9));
        assert!(!session.can_end_control_surface_preview(8));
        assert!(session.can_end_control_surface_preview(9));
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

    #[test]
    fn wp_3u_02_failure_injection_is_restricted_to_a_named_system_temp_root() {
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }

        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "{WP_3U_02_ACCEPTANCE_DIRECTORY_PREFIX}{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        let _cleanup = Cleanup(root.clone());
        std::fs::write(root.join("blocked"), b"not a directory").unwrap();

        assert_eq!(
            wp_3u_02_acceptance_failure_repository_path(root.clone()).unwrap(),
            root.canonicalize().unwrap().join("blocked/ui.json")
        );
        assert!(
            wp_3u_02_acceptance_failure_repository_path(std::env::current_dir().unwrap()).is_err()
        );
    }
}
