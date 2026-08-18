#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod agent_trace_settings;
mod audio;
mod capture;
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
mod input_visual_effect;
mod interaction_latency;
#[cfg(target_os = "macos")]
mod macos_input_glass;
#[allow(dead_code)] // Consumed by the serial Supervisor beginning in WP-1B-02.
mod managed_process_tree;
mod mcp_settings;
mod memory_gateway;
mod native_tool_confirmation;
#[cfg(all(windows, debug_assertions))]
mod phase_1b_runtime_acceptance;
#[cfg(debug_assertions)]
mod phase_1c_core_host_acceptance;
#[allow(dead_code)] // Compile-only platform contracts are wired by WP-1P-02 through WP-1P-05.
mod platform;
mod plugin_settings;
mod product_shell;
mod runtime_log;
mod shared_instance;
mod shell_lifecycle;
mod tool_settings;
mod ui_config;
mod window_geometry;
mod window_interaction;
#[cfg(windows)]
mod windows_glass_poc;
#[cfg(windows)]
mod windows_liquid_glass;
#[cfg(windows)]
mod windows_liquid_glass_native;
#[cfg(debug_assertions)]
mod wp_3_06_data_compat_acceptance;
#[cfg(debug_assertions)]
mod wp_3v_01_assistant_architecture_acceptance;

use std::sync::{Arc, Mutex, TryLockError};

use platform::{
    InstanceLockAcquire, InstanceLockBackend, NativeDiagnosticsBackend,
    NativeDiagnosticsBackendImpl, NativeDiagnosticsRequest, NativeWindowInteractionBackend,
    WindowInteractionBackend, SHARED_INSTANCE_ID,
};
use runtime_log::{
    Correlation, RuntimeLogEvent, RuntimeLogService, Severity, WebviewDiagnosticEntry,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use shared_instance::NativeInstanceLockBackend;
use tauri::{Emitter, Manager, State, WebviewWindow};
use window_geometry::{
    apply_window_layout, ControlSurfaceLayout, InputSurfaceTransition, LayoutApplication,
    LayoutContract, LayoutRevisionGuard, MonitorDescriptor, PhysicalRect, PresentationState,
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
const SETTINGS_MEMORY_SCRIPT: &str = include_str!("../../frontend/settings/memory-runtime.js");
const SETTINGS_TOOLS_SCRIPT: &str = include_str!("../../frontend/settings/tools-runtime.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");
const VISIBILITY_PROBE_HIDDEN_DURATION: std::time::Duration = std::time::Duration::from_millis(220);
#[cfg(windows)]
const INPUT_CONTRACTION_REGION_GRACE_MS: u64 = 40;
const ALREADY_RUNNING_TITLE: &str = "Sakura 已在运行";
const ALREADY_RUNNING_BODY: &str =
    "另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。";
#[cfg(debug_assertions)]
const WP_3U_02_ACCEPTANCE_FAILURE_ROOT_ENV: &str = "SAKURA_WP_3U_02_ACCEPTANCE_FAILURE_ROOT";
#[cfg(debug_assertions)]
const WP_3U_02_ACCEPTANCE_DIRECTORY_PREFIX: &str = "sakura-runtime-v2-wp-3u-02-";
#[cfg(debug_assertions)]
const WP_4_01_MANUAL_ROOT_ENV: &str = "SAKURA_WP_4_01_MANUAL_ROOT";
#[cfg(debug_assertions)]
const WP_4_01_MANUAL_DIRECTORY_PREFIX: &str = "sakura-wp-4-01-manual-";

#[derive(Clone)]
struct PendingPortraitTransition {
    revision: u64,
    generation_id: String,
    portrait_key: String,
    portrait_alpha_mask: character_presentation::PortraitAlphaMask,
    application: LayoutApplication,
    hit_regions: window_interaction::PhysicalHitRegions,
}

struct WindowGeometrySession {
    revision: LayoutRevisionGuard,
    portrait_anchor: Option<window_geometry::PhysicalPoint>,
    physical_local_anchor: Option<[u32; 2]>,
    active_bounds: Option<[u32; 4]>,
    surface_scale: f64,
    application: Option<LayoutApplication>,
    state: Option<PresentationState>,
    applied_revision: u64,
    deferred_drag_pending: bool,
    portrait_alpha_mask: Option<character_presentation::PortraitAlphaMask>,
    portrait_transition_active: bool,
    portrait_transition_drag: Option<(
        character_presentation::PortraitAlphaMask,
        window_interaction::LogicalHitRect,
    )>,
    portrait_transition_pending: Option<PendingPortraitTransition>,
    portrait_hit_generation: Option<String>,
    portrait_hit_key: Option<String>,
    portrait_hit_revision: u64,
    portrait_hit_relaxed: bool,
    portrait_scale_preview_active: bool,
    portrait_scale_gesture_active: bool,
    control_surface_preview_active: bool,
    control_surface_preview_revision: u64,
    portrait_scale_percent: u16,
    context_menu_open: bool,
    context_menu_hit_regions: Option<window_interaction::PhysicalHitRegions>,
    context_menu_base_application: Option<LayoutApplication>,
    context_menu_base_hit_regions: Option<window_interaction::PhysicalHitRegions>,
    control_surface: Option<ControlSurfaceLayout>,
    hit_regions: Option<window_interaction::PhysicalHitRegions>,
}

impl Default for WindowGeometrySession {
    fn default() -> Self {
        Self {
            revision: LayoutRevisionGuard::default(),
            portrait_anchor: None,
            physical_local_anchor: None,
            active_bounds: None,
            surface_scale: 1.0,
            application: None,
            state: None,
            applied_revision: 0,
            deferred_drag_pending: false,
            portrait_alpha_mask: None,
            portrait_transition_active: false,
            portrait_transition_drag: None,
            portrait_transition_pending: None,
            portrait_hit_generation: None,
            portrait_hit_key: None,
            portrait_hit_revision: 0,
            portrait_hit_relaxed: false,
            portrait_scale_preview_active: false,
            portrait_scale_gesture_active: false,
            control_surface_preview_active: false,
            control_surface_preview_revision: 0,
            portrait_scale_percent: 100,
            context_menu_open: false,
            context_menu_hit_regions: None,
            context_menu_base_application: None,
            context_menu_base_hit_regions: None,
            control_surface: None,
            hit_regions: None,
        }
    }
}

struct ShellLifecycleState {
    handle: Option<shell_lifecycle::ShellLifecycleHandle>,
    runtime_log: RuntimeLogService,
}

struct RuntimeLogShutdown {
    runtime_log: RuntimeLogService,
    finished: bool,
}

impl RuntimeLogShutdown {
    fn new(runtime_log: RuntimeLogService) -> Self {
        Self {
            runtime_log,
            finished: false,
        }
    }

    fn finish(&mut self) {
        if self.finished {
            return;
        }
        self.finished = true;
        let _ = self.runtime_log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "shell",
            "shell.stopping",
            "Runtime shell is stopping",
        ));
        let _ = self.runtime_log.submit(RuntimeLogEvent::rust(
            Severity::Info,
            "shell",
            "shell.stopped",
            "Runtime shell stopped",
        ));
        let _ = self
            .runtime_log
            .shutdown(runtime_log::PRODUCTION_SHUTDOWN_TIMEOUT);
    }
}

impl Drop for RuntimeLogShutdown {
    fn drop(&mut self) {
        self.finish();
    }
}

fn install_runtime_panic_hook(runtime_log: RuntimeLogService) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        let _ = runtime_log.submit(
            RuntimeLogEvent::rust(
                Severity::Error,
                "shell.error",
                "shell.error.unhandled",
                "Unhandled Rust error",
            )
            .attributes(json!({"code": "RUST_PANIC", "category": "panic"})),
        );
        previous(panic_info);
    }));
}

fn append_runtime_diagnostic_event(
    runtime_log: &RuntimeLogService,
    component: &'static str,
    event: &'static str,
    details: Value,
) {
    let severity = if details
        .get("outcome")
        .and_then(Value::as_str)
        .is_some_and(|outcome| outcome == "failed")
        || event.contains("failed")
    {
        Severity::Warning
    } else {
        Severity::Debug
    };
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, component, event, "Runtime diagnostic event")
            .attributes(details),
    );
}

fn classify_memory_request_error(error: &str) -> &'static str {
    if error.contains("REQUEST_DEADLINE_EXCEEDED") {
        "deadline_exceeded"
    } else if error.contains("GENERATION_INVALIDATED")
        || error.contains("SETTINGS_CORE_GENERATION_MISMATCH")
    {
        "generation_transition"
    } else if error.contains("SETTINGS_CORE_UNAVAILABLE") {
        "core_unavailable"
    } else if error.contains("SETTINGS_TRANSPORT_UNAVAILABLE")
        || error.contains("Router closed")
        || error.contains("TRANSPORT")
    {
        "transport_unavailable"
    } else if error.contains("RESPONSE_INVALID") || error.contains("PROTOCOL") {
        "invalid_response"
    } else if error.contains("SETTINGS_WINDOW_REQUIRED") || error.contains("MEMORY_WINDOW_REQUIRED")
    {
        "window_rejected"
    } else {
        "request_failed"
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PortraitScalePreview {
    application: Option<LayoutApplication>,
    deferred_native: bool,
    deferred_hit_regions: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct PortraitScalePlatformCapabilities {
    stable_bounds_during_gesture: bool,
    precise_hit_regions_during_gesture: bool,
    resident_stable_bounds: bool,
}

const fn portrait_scale_platform_capabilities(
    target: platform::PlatformTarget,
) -> PortraitScalePlatformCapabilities {
    match target {
        platform::PlatformTarget::WindowsX64 => PortraitScalePlatformCapabilities {
            stable_bounds_during_gesture: true,
            precise_hit_regions_during_gesture: false,
            resident_stable_bounds: true,
        },
        platform::PlatformTarget::MacOsArm64 => PortraitScalePlatformCapabilities {
            stable_bounds_during_gesture: true,
            precise_hit_regions_during_gesture: true,
            resident_stable_bounds: false,
        },
        platform::PlatformTarget::LinuxX64 => PortraitScalePlatformCapabilities {
            stable_bounds_during_gesture: true,
            precise_hit_regions_during_gesture: true,
            resident_stable_bounds: false,
        },
    }
}

fn current_portrait_scale_platform_capabilities() -> PortraitScalePlatformCapabilities {
    platform::current_platform_target()
        .map(portrait_scale_platform_capabilities)
        .unwrap_or_default()
}

fn portrait_hit_revision_is_stale(
    same_generation: bool,
    requested_revision: u64,
    current_revision: u64,
    current_region_relaxed: bool,
) -> bool {
    same_generation
        && (requested_revision < current_revision
            || (requested_revision == current_revision && !current_region_relaxed))
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

    fn observe_deferred_window_position(
        &mut self,
        position: window_geometry::PhysicalPoint,
    ) -> Result<(), String> {
        if !self.deferred_drag_pending {
            return Ok(());
        }
        let local_anchor = self
            .physical_local_anchor
            .ok_or_else(|| "pet surface local anchor is unavailable".to_string())?;
        let anchor = window_geometry::anchor_from_window_position(position, local_anchor)?;
        self.portrait_anchor = Some(anchor);
        // macOS/Linux complete the native move loop asynchronously. Keep the cached application
        // placement in sync with the window event as well as the logical anchor; otherwise the
        // next menu/layout transaction can resurrect the pre-drag default bottom-right frame.
        if let Some(application) = self.application.as_mut() {
            application.physical_placement.x = position.x;
            application.physical_placement.y = position.y;
            application.portrait_anchor = anchor;
        }
        Ok(())
    }

    /// Returns true only when the caller owns the next control-surface preview revision.
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

    fn can_settle_portrait_scale(&self, revision: u64) -> bool {
        self.portrait_scale_preview_active
            && !self.portrait_scale_gesture_active
            && revision == self.portrait_hit_revision
    }

    fn defers_precise_portrait_scale_hit_regions(&self) -> bool {
        defers_portrait_scale_hit_region_frames()
            && self.portrait_scale_preview_active
            && self.portrait_scale_gesture_active
            && self.portrait_hit_relaxed
    }

    fn stabilizes_portrait_scale_bounds(&self) -> bool {
        defers_native_portrait_scale_frames()
            && self.portrait_scale_preview_active
            && self.portrait_scale_gesture_active
    }
}

fn defers_native_portrait_scale_frames() -> bool {
    current_portrait_scale_platform_capabilities().stable_bounds_during_gesture
}

fn defers_portrait_scale_hit_region_frames() -> bool {
    let capabilities = current_portrait_scale_platform_capabilities();
    capabilities.stable_bounds_during_gesture && !capabilities.precise_hit_regions_during_gesture
}

fn resolve_portrait_hit_generation(
    available_generation: Option<String>,
    confirmed_generation: Option<&str>,
) -> Result<String, String> {
    available_generation
        .or_else(|| confirmed_generation.map(str::to_owned))
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())
}

fn try_observe_deferred_window_position(
    session: &Mutex<WindowGeometrySession>,
    position: window_geometry::PhysicalPoint,
) -> Result<bool, String> {
    match session.try_lock() {
        Ok(mut session) => {
            session.observe_deferred_window_position(position)?;
            Ok(true)
        }
        // Native bounds commits can synchronously dispatch WindowEvent::Moved while
        // the committing command owns this mutex. The command will publish the same
        // geometry after SetWindowPos returns, so the reentrant observation is skipped.
        Err(TryLockError::WouldBlock) => Ok(false),
        Err(TryLockError::Poisoned(_)) => Err("window geometry state is unavailable".to_string()),
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PetLayoutApplication {
    #[serde(flatten)]
    layout: LayoutApplication,
    hit_regions: Option<window_interaction::PhysicalHitRegions>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PetSurfaceDiagnostics {
    revision: u64,
    logical_bounds: [u32; 4],
    physical_window: window_geometry::PhysicalPlacement,
    global_anchor: window_geometry::PhysicalPoint,
    physical_local_anchor: [u32; 2],
    dpi_scale: f64,
    content_scale: f64,
    region_count: usize,
    backend_mode: &'static str,
    degraded_reason: Option<&'static str>,
    last_commit_result: &'static str,
}

fn is_animated_input_contraction(
    previous: &ControlSurfaceLayout,
    target: &ControlSurfaceLayout,
    transition: Option<InputSurfaceTransition>,
) -> bool {
    transition.is_some_and(|transition| transition.duration_ms > 0)
        && previous.input_rect[..3] == target.input_rect[..3]
        && previous.input_rect[3] > target.input_rect[3]
}

#[cfg(windows)]
fn schedule_input_contraction_region_commit(
    window: &WebviewWindow,
    revision: u64,
    duration_ms: u32,
    hit_regions: window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    let delayed_window = window.clone();
    std::thread::Builder::new()
        .name("input-contraction-region".to_string())
        .spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(
                u64::from(duration_ms) + INPUT_CONTRACTION_REGION_GRACE_MS,
            ));
            let commit_window = delayed_window.clone();
            if let Err(error) = delayed_window.run_on_main_thread(move || {
                let commit = (|| -> Result<(), String> {
                    let state = commit_window.state::<Mutex<WindowGeometrySession>>();
                    let mut geometry = state
                        .lock()
                        .map_err(|_| "window geometry state is unavailable".to_string())?;
                    if geometry.applied_revision != revision {
                        return Ok(());
                    }
                    if geometry.context_menu_open {
                        geometry.context_menu_base_hit_regions = Some(hit_regions.clone());
                        geometry.hit_regions = Some(hit_regions);
                        return Ok(());
                    }
                    apply_precise_hit_regions(&commit_window, &hit_regions)?;
                    geometry.hit_regions = Some(hit_regions);
                    Ok(())
                })();
                if let Err(error) = commit {
                    eprintln!("failed to settle input contraction region: {error}");
                }
            }) {
                eprintln!("failed to schedule input contraction region settlement: {error}");
            }
        })
        .map(|_| ())
        .map_err(|error| format!("failed to start input contraction region timer: {error}"))
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

fn uses_resident_stable_surface_bounds(
    portrait_alpha_mask_available: bool,
    control_surface_available: bool,
) -> bool {
    current_portrait_scale_platform_capabilities().resident_stable_bounds
        && (portrait_alpha_mask_available || control_surface_available)
}

fn compute_pet_window_layout(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<window_geometry::PhysicalPoint>,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    stabilize_portrait_scale: bool,
) -> Result<LayoutApplication, String> {
    // Win32 SetWindowRgn already provides the exact visible/input shape. Keep the underlying
    // rectangular HWND/WebView envelope stable across every portrait-scale and control-panel
    // setting so neither slider gesture has to resize or reposition the compositor surface.
    let bounds_started = std::time::Instant::now();
    let visible_surface_bounds = if uses_resident_stable_surface_bounds(
        portrait_alpha_mask.is_some(),
        control_surface.is_some(),
    ) {
        // Windows keeps the rectangular HWND/WebView envelope independent of the
        // current expression and layout slider while precise regions control the
        // actual visible and interactive pixels.
        window_interaction::logical_scale_and_control_stable_surface_bounds(
            contract,
            state,
            portrait_scale_percent,
            portrait_alpha_mask,
        )?
    } else if stabilize_portrait_scale {
        window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
            contract,
            state,
            portrait_scale_percent,
            control_surface,
            portrait_alpha_mask,
        )?
    } else {
        window_interaction::logical_visible_surface_bounds_with_control_surface(
            contract,
            state,
            portrait_scale_percent,
            control_surface,
            portrait_alpha_mask,
        )?
    };
    interaction_latency::stage_elapsed("surface-bounds-compute-return", bounds_started);
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
fn current_pet_surface_diagnostics(
    window: WebviewWindow,
    session: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<PetSurfaceDiagnostics, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let application = geometry
        .application
        .as_ref()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let regions = geometry
        .context_menu_hit_regions
        .as_ref()
        .or(geometry.hit_regions.as_ref())
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    let region_count = window_interaction::native_hit_rectangles(
        regions,
        [
            application.physical_placement.width,
            application.physical_placement.height,
        ],
    )?
    .len();
    Ok(PetSurfaceDiagnostics {
        revision: application.revision,
        logical_bounds: application.active_bounds,
        physical_window: application.physical_placement,
        global_anchor: application.portrait_anchor,
        physical_local_anchor: application.physical_local_anchor,
        dpi_scale: application.scale_factor,
        content_scale: application.content_scale,
        region_count,
        backend_mode: application.backend_mode,
        degraded_reason: application.degraded_reason,
        last_commit_result: "applied",
    })
}

#[tauri::command]
fn apply_pet_layout(
    window: WebviewWindow,
    state: PresentationState,
    revision: u64,
    control_surface: Option<ControlSurfaceLayout>,
    input_transition: Option<window_geometry::InputSurfaceTransition>,
    trace: Option<interaction_latency::InteractionTraceContext>,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
    glass: tauri::State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<PetLayoutApplication, String> {
    interaction_latency::command("main.apply-pet-layout", trace, || {
        let contract = layout_contract()?;
        if let Some(surface) = control_surface.as_ref() {
            contract.validate_control_surface(state, surface)?;
        }
        let input_transition = input_transition.map(|value| value.validate()).transpose()?;
        let mut session = interaction_latency::lock(
            session.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;

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
            Some(window_geometry::anchor_from_window_position(
                window_geometry::PhysicalPoint {
                    x: position.x,
                    y: position.y,
                },
                session
                    .physical_local_anchor
                    .ok_or_else(|| "pet surface local anchor is unavailable".to_string())?,
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
            control_surface.as_ref(),
            session.portrait_alpha_mask.as_ref(),
            false,
        )?;
        let previous_application = session.application.clone();
        let previous_control_surface = session.control_surface.clone();
        if input_transition.is_some() {
            let previous = previous_control_surface
                .as_ref()
                .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?;
            let target = control_surface
                .as_ref()
                .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?;
            if previous.bubble_rect != target.bubble_rect
                || previous.input_rect[..3] != target.input_rect[..3]
                || previous.input_rect[3] == target.input_rect[3]
            {
                return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
            }
        }
        let previous_regions = session.hit_regions.clone();
        let defer_precise_control_regions = cfg!(windows) && session.control_surface_preview_active;
        let defer_input_contraction = cfg!(windows)
            && !defer_precise_control_regions
            && previous_application
                .as_ref()
                .is_some_and(|previous| same_surface_geometry(previous, &application))
            && previous_regions.is_some()
            && previous_control_surface.as_ref().is_some_and(|previous| {
                control_surface.as_ref().is_some_and(|target| {
                    is_animated_input_contraction(previous, target, input_transition)
                })
            });
        let hit_regions = if defer_input_contraction {
            build_native_interaction_regions(
                &contract,
                &application,
                control_surface.as_ref(),
                session.portrait_alpha_mask.as_ref(),
                session.portrait_scale_percent,
            )?
        } else if defer_precise_control_regions {
            let hit_regions = build_native_interaction_regions(
                &contract,
                &application,
                control_surface.as_ref(),
                session.portrait_alpha_mask.as_ref(),
                session.portrait_scale_percent,
            )?;
            apply_native_pet_surface_bounds_transaction(
                &window,
                &application,
                previous_application.as_ref(),
                previous_regions.as_ref(),
            )?;
            hit_regions
        } else {
            apply_native_pet_surface_transaction(
                &window,
                &contract,
                &application,
                control_surface.as_ref(),
                session.portrait_alpha_mask.as_ref(),
                session.portrait_scale_percent,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                false,
            )?
        };
        if session.is_deferred_drag_pending() {
            session.finish_deferred_drag();
        }
        if let Some(surface) = control_surface.as_ref() {
            glass.update_control_surface(
                &window,
                surface,
                &application,
                previous_control_surface.as_ref(),
                input_transition,
            )?;
        }
        session.portrait_anchor = Some(application.portrait_anchor);
        session.physical_local_anchor = Some(application.physical_local_anchor);
        session.active_bounds = Some(application.active_bounds);
        session.surface_scale = application.scale_factor * application.content_scale;
        session.application = Some(application.clone());
        session.state = Some(state);
        session.applied_revision = revision;
        session.control_surface = control_surface;
        session.hit_regions = if defer_input_contraction {
            previous_regions
        } else {
            Some(hit_regions.clone())
        };
        let result = PetLayoutApplication {
            layout: application,
            hit_regions: Some(hit_regions.clone()),
        };
        let deferred_duration = defer_input_contraction
            .then(|| input_transition.map(|transition| transition.duration_ms))
            .flatten();
        drop(session);
        #[cfg(windows)]
        if let Some(duration_ms) = deferred_duration {
            if let Err(error) = schedule_input_contraction_region_commit(
                &window,
                revision,
                duration_ms,
                hit_regions.clone(),
            ) {
                eprintln!("{error}; applying final input region immediately");
                apply_precise_hit_regions(&window, &hit_regions)?;
                let state = window.state::<Mutex<WindowGeometrySession>>();
                if let Ok(mut geometry) = state.lock() {
                    if geometry.applied_revision == revision {
                        geometry.hit_regions = Some(hit_regions);
                    }
                };
            }
        }
        #[cfg(not(windows))]
        let _ = deferred_duration;
        Ok(result)
    })
}

fn apply_native_interaction_region(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let physical = build_native_interaction_regions(
        contract,
        application,
        control_surface,
        portrait_alpha_mask,
        portrait_scale_percent,
    )?;
    apply_precise_hit_regions(window, &physical)?;
    Ok(physical)
}

fn build_native_interaction_regions(
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let started = std::time::Instant::now();
    let mut logical = window_interaction::logical_hit_regions_with_control_surface(
        contract,
        application.state,
        portrait_alpha_mask.map(character_presentation::PortraitAlphaMask::source_size),
        portrait_scale_percent,
        control_surface,
    )?;
    let native_portrait_alpha_mask =
        window_interaction::apply_portrait_alpha_bounds(&mut logical, portrait_alpha_mask)?;
    let mut physical = window_interaction::scale_hit_regions_for_surface(
        &logical,
        application.scale_factor * application.content_scale,
        application.active_bounds,
        contract.viewport.portrait_anchor,
    )?;
    physical.portrait_alpha_mask = native_portrait_alpha_mask;
    interaction_latency::stage_elapsed("interaction-regions-build-return", started);
    Ok(physical)
}

fn apply_precise_hit_regions(
    window: &WebviewWindow,
    physical: &window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    let backend = NativeWindowInteractionBackend;
    backend
        .apply_hit_regions(window, physical)
        .map_err(|error| {
            format!("failed to apply native hit regions; previous region retained: {error}")
        })
}

fn reapply_current_pet_hit_region(window: &WebviewWindow) -> Result<(), String> {
    let session = window.state::<Mutex<WindowGeometrySession>>();
    let geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let hit_regions = geometry
        .context_menu_hit_regions
        .as_ref()
        .or(geometry.hit_regions.as_ref())
        .cloned()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    drop(geometry);

    apply_precise_hit_regions(window, &hit_regions)
}

fn precommit_webview_surface(
    window: &WebviewWindow,
    application: &LayoutApplication,
) -> Result<(), String> {
    let overall_started = std::time::Instant::now();
    interaction_latency::stage("webview-precommit-start");
    let [active_x, active_y, _, _] = application.active_bounds;
    let left = -f64::from(active_x) * application.content_scale;
    let top = -f64::from(active_y) * application.content_scale;
    if !left.is_finite() || !top.is_finite() {
        return Err("PET_SURFACE_OFFSET_INVALID".to_string());
    }
    let script = format!(
        "(()=>{{const s=document.querySelector('#pet-stage');if(!s)return;s.style.left='{left}px';s.style.top='{top}px';s.dataset.surfaceX='{active_x}';s.dataset.surfaceY='{active_y}';s.dataset.surfaceRevision='{}';}})()",
        application.revision
    );
    let eval_started = std::time::Instant::now();
    window
        .eval(&script)
        .map_err(|error| format!("failed to precommit WebView surface offset: {error}"))?;
    interaction_latency::stage_elapsed("webview-eval-return", eval_started);
    interaction_latency::stage_elapsed("webview-precommit-return", overall_started);
    Ok(())
}

fn apply_native_pet_surface(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let backend = NativeWindowInteractionBackend;
    backend
        .prepare_window(window)
        .map_err(|error| error.to_string())?;
    precommit_webview_surface(window, application)?;
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
    )?;
    Ok(hit_regions)
}

fn rollback_pet_surface(
    window: &WebviewWindow,
    application: Option<&LayoutApplication>,
    regions: Option<&window_interaction::PhysicalHitRegions>,
) -> Result<(), String> {
    rollback_pet_surface_with_bounds_mode(window, application, regions, false)
}

fn rollback_pet_surface_with_bounds_mode(
    window: &WebviewWindow,
    application: Option<&LayoutApplication>,
    regions: Option<&window_interaction::PhysicalHitRegions>,
    preserve_top_left: bool,
) -> Result<(), String> {
    let (application, regions) = match (application, regions) {
        (Some(application), Some(regions)) => (application, regions),
        _ => return Ok(()),
    };
    NativeWindowInteractionBackend
        .prepare_window(window)
        .map_err(|error| error.to_string())?;
    precommit_webview_surface(window, application)?;
    let backend = NativeWindowInteractionBackend;
    if preserve_top_left {
        backend
            .apply_bounds_preserving_top_left(window, &application.physical_placement)
            .map_err(|error| error.to_string())?;
    } else {
        backend
            .apply_bounds(window, &application.physical_placement)
            .map_err(|error| error.to_string())?;
    }
    apply_precise_hit_regions(window, regions)
}

fn same_surface_geometry(previous: &LayoutApplication, next: &LayoutApplication) -> bool {
    previous.physical_placement == next.physical_placement
        && previous.active_bounds == next.active_bounds
        && previous.content_scale == next.content_scale
        && previous.scale_factor == next.scale_factor
}

fn same_local_surface_geometry(previous: &LayoutApplication, next: &LayoutApplication) -> bool {
    previous.physical_placement.width == next.physical_placement.width
        && previous.physical_placement.height == next.physical_placement.height
        && previous.active_bounds == next.active_bounds
        && previous.content_scale == next.content_scale
        && previous.scale_factor == next.scale_factor
}

fn apply_native_pet_surface_transaction(
    window: &WebviewWindow,
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
    previous_application: Option<&LayoutApplication>,
    previous_regions: Option<&window_interaction::PhysicalHitRegions>,
    previous_region_relaxed: bool,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let geometry_unchanged =
        previous_application.is_some_and(|previous| same_surface_geometry(previous, application));
    let commit = || -> Result<window_interaction::PhysicalHitRegions, String> {
        let next_regions = build_native_interaction_regions(
            contract,
            application,
            control_surface,
            portrait_alpha_mask,
            portrait_scale_percent,
        )?;
        if !geometry_unchanged {
            let backend = NativeWindowInteractionBackend;
            backend
                .prepare_window(window)
                .map_err(|error| error.to_string())?;
            precommit_webview_surface(window, application)?;
            backend
                .apply_bounds(window, &application.physical_placement)
                .map_err(|error| error.to_string())?;
        }

        if !previous_region_relaxed {
            if let (Some(previous_application), Some(previous_regions)) =
                (previous_application, previous_regions)
            {
                if !geometry_unchanged {
                    let previous_placement = previous_application.physical_placement;
                    let next_placement = application.physical_placement;
                    let mut bridge = next_regions.clone();
                    bridge.extra_native_rectangles.extend(
                        window_interaction::translated_bridge_rectangles(
                            previous_regions,
                            [previous_placement.width, previous_placement.height],
                            [previous_placement.x, previous_placement.y],
                            [next_placement.x, next_placement.y],
                            [next_placement.width, next_placement.height],
                        )?,
                    );
                    apply_precise_hit_regions(window, &bridge)?;
                }
            }
        }
        apply_precise_hit_regions(window, &next_regions)?;
        Ok(next_regions)
    };
    match commit() {
        Ok(regions) => Ok(regions),
        Err(error) => match if geometry_unchanged {
            previous_regions
                .map(|regions| apply_precise_hit_regions(window, regions))
                .unwrap_or(Ok(()))
        } else {
            rollback_pet_surface(window, previous_application, previous_regions)
        } {
            Ok(()) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED_PREVIOUS_RESTORED: {error}"
            )),
            Err(rollback_error) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED: {error}; PET_SURFACE_ROLLBACK_FAILED: {rollback_error}"
            )),
        },
    }
}

fn apply_native_pet_surface_bounds_transaction(
    window: &WebviewWindow,
    application: &LayoutApplication,
    previous_application: Option<&LayoutApplication>,
    previous_regions: Option<&window_interaction::PhysicalHitRegions>,
) -> Result<(), String> {
    apply_native_pet_surface_bounds_transaction_with_mode(
        window,
        application,
        previous_application,
        previous_regions,
        false,
    )
}

fn apply_native_pet_surface_bounds_transaction_preserving_top_left(
    window: &WebviewWindow,
    application: &LayoutApplication,
    previous_application: Option<&LayoutApplication>,
    previous_regions: Option<&window_interaction::PhysicalHitRegions>,
) -> Result<(), String> {
    apply_native_pet_surface_bounds_transaction_with_mode(
        window,
        application,
        previous_application,
        previous_regions,
        true,
    )
}

fn apply_native_pet_surface_bounds_transaction_with_mode(
    window: &WebviewWindow,
    application: &LayoutApplication,
    previous_application: Option<&LayoutApplication>,
    previous_regions: Option<&window_interaction::PhysicalHitRegions>,
    preserve_top_left: bool,
) -> Result<(), String> {
    if previous_application.is_some_and(|previous| same_surface_geometry(previous, application)) {
        return Ok(());
    }
    let backend = NativeWindowInteractionBackend;
    let commit = NativeWindowInteractionBackend
        .prepare_window(window)
        .map_err(|error| error.to_string())
        .and_then(|_| precommit_webview_surface(window, application))
        .and_then(|_| {
            if preserve_top_left {
                backend
                    .apply_bounds_preserving_top_left(window, &application.physical_placement)
                    .map_err(|error| error.to_string())
            } else {
                backend
                    .apply_bounds(window, &application.physical_placement)
                    .map_err(|error| error.to_string())
            }
        });
    match commit {
        Ok(()) => Ok(()),
        Err(error) => match rollback_pet_surface_with_bounds_mode(
            window,
            previous_application,
            previous_regions,
            preserve_top_left,
        ) {
            Ok(()) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED_PREVIOUS_RESTORED: {error}"
            )),
            Err(rollback_error) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED: {error}; PET_SURFACE_ROLLBACK_FAILED: {rollback_error}"
            )),
        },
    }
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
        None,
        None,
        false,
    )?;
    apply_native_pet_surface(window, &contract, &application, None, None, 100)?;
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
    let requested_anchor = window_geometry::anchor_from_window_position(
        position,
        session
            .physical_local_anchor
            .ok_or_else(|| "pet surface local anchor is unavailable".to_string())?,
    )?;
    let application = compute_pet_window_layout(
        &contract,
        state,
        session.applied_revision,
        &monitor,
        Some(requested_anchor),
        session.portrait_scale_percent,
        session.control_surface.as_ref(),
        session.portrait_alpha_mask.as_ref(),
        false,
    )?;
    let previous_application = session.application.clone();
    let previous_regions = session.hit_regions.clone();
    // The Windows drag loop has already moved the HWND. On the same local surface, issuing the
    // same SetWindowPos and SetWindowRgn again forces DWM/Composition to rebuild unchanged content
    // and can flash the Gaussian output at pointer-up. Cross-monitor DPI/size changes still take
    // the complete transaction.
    let hit_regions = if previous_application
        .as_ref()
        .is_some_and(|previous| same_local_surface_geometry(previous, &application))
    {
        previous_regions
            .clone()
            .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?
    } else {
        apply_native_pet_surface_transaction(
            &window,
            &contract,
            &application,
            session.control_surface.as_ref(),
            session.portrait_alpha_mask.as_ref(),
            session.portrait_scale_percent,
            previous_application.as_ref(),
            previous_regions.as_ref(),
            false,
        )?
    };
    session.portrait_anchor = Some(application.portrait_anchor);
    session.physical_local_anchor = Some(application.physical_local_anchor);
    session.active_bounds = Some(application.active_bounds);
    session.surface_scale = application.scale_factor * application.content_scale;
    session.application = Some(application.clone());
    session.hit_regions = Some(hit_regions.clone());
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
    })
}

fn commit_dragged_window_position_on_main_thread(
    window: WebviewWindow,
    trace: Option<interaction_latency::InteractionTraceContext>,
) -> Result<PetLayoutApplication, String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    let commit_window = window.clone();
    let dispatch_started = std::time::Instant::now();
    interaction_latency::stage("drag-commit-main-thread-dispatch-start");
    window
        .run_on_main_thread(move || {
            let result = interaction_latency::command("main.commit-pet-drag", trace, || {
                let position_started = std::time::Instant::now();
                let position = commit_window
                    .outer_position()
                    .map_err(|error| format!("failed to read dragged window position: {error}"))?;
                interaction_latency::stage_elapsed(
                    "dragged-outer-position-return",
                    position_started,
                );
                let session = commit_window.state::<Mutex<WindowGeometrySession>>();
                let mut session = interaction_latency::lock(
                    session.inner(),
                    "geometry-mutex-commit-wait-start",
                    "geometry-mutex-commit-acquired",
                )?;
                commit_dragged_window_position(
                    commit_window.clone(),
                    &mut session,
                    window_geometry::PhysicalPoint {
                        x: position.x,
                        y: position.y,
                    },
                )
            });
            let _ = sender.send(result);
        })
        .map_err(|error| format!("PET_DRAG_COMMIT_DISPATCH_FAILED: {error}"))?;
    interaction_latency::stage_elapsed("drag-commit-main-thread-dispatch-return", dispatch_started);
    let wait_started = std::time::Instant::now();
    let result = receiver
        .recv_timeout(std::time::Duration::from_secs(5))
        .map_err(|_| "PET_DRAG_COMMIT_MAIN_THREAD_TIMEOUT".to_string())?;
    interaction_latency::stage_elapsed("drag-commit-main-thread-return", wait_started);
    result
}

#[tauri::command]
async fn start_pet_drag(
    window: WebviewWindow,
    revision: u64,
    surface_x: f64,
    surface_y: f64,
    trace: Option<interaction_latency::InteractionTraceContext>,
) -> Result<Option<PetLayoutApplication>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let session = window.state::<Mutex<WindowGeometrySession>>();
        start_pet_drag_blocking(
            &window,
            revision,
            surface_x,
            surface_y,
            trace,
            session.inner(),
        )
    })
    .await
    .map_err(|_| "PET_DRAG_TASK_ABORTED".to_string())?
}

fn start_pet_drag_blocking(
    window: &WebviewWindow,
    revision: u64,
    surface_x: f64,
    surface_y: f64,
    trace: Option<interaction_latency::InteractionTraceContext>,
    session: &Mutex<WindowGeometrySession>,
) -> Result<Option<PetLayoutApplication>, String> {
    let commit_trace = trace.clone();
    interaction_latency::command("main.start-pet-drag", trace, || {
        if !surface_x.is_finite() || !surface_y.is_finite() {
            return Err("PET_DRAG_POINT_INVALID".to_string());
        }
        let expects_deferred_completion = matches!(
            window_interaction::native_drag_completion(),
            window_interaction::NativeDragCompletion::DeferredWindowMoved
        );
        {
            let mut session = interaction_latency::lock(
                session,
                "geometry-mutex-wait-start",
                "geometry-mutex-acquired",
            )?;
            interaction_latency::stage("drag-authorization-start");
            if session.state.is_none() {
                return Err("pet layout is not ready for dragging".to_string());
            }
            if revision != session.applied_revision {
                return Err("PET_DRAG_REVISION_STALE".to_string());
            }
            let state = session.state.expect("checked above");
            let regions = window_interaction::logical_hit_regions_with_control_surface(
                &layout_contract()?,
                state,
                session
                    .portrait_alpha_mask
                    .as_ref()
                    .map(character_presentation::PortraitAlphaMask::source_size),
                session.portrait_scale_percent,
                session.control_surface.as_ref(),
            )?;
            let point = [surface_x.floor() as i32, surface_y.floor() as i32];
            let mut drag_authorized = window_interaction::classify_logical_point_with_alpha(
                &regions,
                session.portrait_alpha_mask.as_ref(),
                point,
            )? == window_interaction::HitKind::Drag;
            if !drag_authorized {
                if let Some((mask, target)) = session.portrait_transition_drag.as_ref() {
                    let transition = window_interaction::LogicalHitRegions {
                        state,
                        interactive: regions.interactive.clone(),
                        drag: vec![*target],
                        neutral: regions.neutral.clone(),
                    };
                    drag_authorized = window_interaction::classify_logical_point_with_alpha(
                        &transition,
                        Some(mask),
                        point,
                    )? == window_interaction::HitKind::Drag;
                }
            }
            if !drag_authorized {
                return Err("PET_DRAG_POINT_REJECTED".to_string());
            }
            interaction_latency::stage("drag-authorization-return");
            if expects_deferred_completion {
                session.begin_deferred_drag();
            }
        }

        let native_drag_started = std::time::Instant::now();
        interaction_latency::stage("native-drag-call-start");
        let completion = match NativeWindowInteractionBackend.start_drag(&window) {
            Ok(completion) => completion,
            Err(error) => {
                if expects_deferred_completion {
                    let mut session = interaction_latency::lock(
                        session,
                        "geometry-mutex-error-wait-start",
                        "geometry-mutex-error-acquired",
                    )?;
                    session.cancel_deferred_drag();
                }
                return Err(error.to_string());
            }
        };
        interaction_latency::stage_elapsed("native-drag-call-return", native_drag_started);

        match completion {
            window_interaction::NativeDragCompletion::SynchronousMoveLoop => {
                if expects_deferred_completion {
                    let mut session = interaction_latency::lock(
                        session,
                        "geometry-mutex-cancel-wait-start",
                        "geometry-mutex-cancel-acquired",
                    )?;
                    session.cancel_deferred_drag();
                }
                commit_dragged_window_position_on_main_thread(window.clone(), commit_trace.clone())
                    .map(Some)
            }
            window_interaction::NativeDragCompletion::DeferredWindowMoved => {
                if !expects_deferred_completion {
                    let mut session = interaction_latency::lock(
                        session,
                        "geometry-mutex-deferred-wait-start",
                        "geometry-mutex-deferred-acquired",
                    )?;
                    session.begin_deferred_drag();
                }
                Ok(None)
            }
        }
    })
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
    if geometry.is_deferred_drag_pending() {
        let position = window
            .outer_position()
            .map_err(|error| format!("failed to read dragged window position: {error}"))?;
        geometry.observe_deferred_window_position(window_geometry::PhysicalPoint {
            x: position.x,
            y: position.y,
        })?;
    }
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
    if window_interaction::classify_logical_point_with_alpha(
        &regions,
        geometry.portrait_alpha_mask.as_ref(),
        point,
    )? == window_interaction::HitKind::Transparent
    {
        return Err("PRODUCT_MENU_SURFACE_REJECTED".to_string());
    }
    // Capture the committed surface before the WebView grows the menu. This snapshot is the
    // exact frame to restore on close; it must never be reconstructed through the default
    // work-area placement policy.
    geometry.context_menu_base_application = geometry.application.clone();
    geometry.context_menu_base_hit_regions = geometry.hit_regions.clone();
    geometry.context_menu_hit_regions = None;
    geometry.context_menu_open = true;
    Ok(product_shell::product_menu_capability_manifest(
        subtitle.get()?.is_chinese(),
    ))
}

#[tauri::command]
fn set_pet_context_menu_surface(
    window: WebviewWindow,
    rect: [u32; 4],
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let contract = layout_contract()?;
    let mut geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.context_menu_open {
        return Err("PET_CONTEXT_MENU_NOT_OPEN".to_string());
    }
    let base_application = geometry
        .context_menu_base_application
        .clone()
        .or_else(|| geometry.application.clone())
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let base_hit_regions = geometry
        .context_menu_base_hit_regions
        .clone()
        .or_else(|| geometry.hit_regions.clone())
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    let [x, y, requested_width, requested_height] = rect;
    let expanded_bounds = window_interaction::expand_surface_bounds_for_overlay(
        base_application.active_bounds,
        rect,
        contract.viewport.window_size,
    )
    .map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID".to_string())?;
    let application = window_geometry::expand_application_preserving_anchor(
        &base_application,
        expanded_bounds,
        contract.viewport.portrait_anchor,
    )?;

    let mut expanded_base = build_native_interaction_regions(
        &contract,
        &application,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        geometry.portrait_scale_percent,
    )?;
    let logical = window_interaction::LogicalHitRegions {
        state: expanded_base.state,
        interactive: vec![window_interaction::LogicalHitRect::checked(
            i32::try_from(x).map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID")?,
            i32::try_from(y).map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID")?,
            requested_width,
            requested_height,
            contract.viewport.window_size,
        )?],
        drag: Vec::new(),
        neutral: Vec::new(),
    };
    let canonical_anchor = contract.viewport.portrait_anchor;
    let mut menu = window_interaction::scale_hit_regions_for_surface(
        &logical,
        application.scale_factor * application.content_scale,
        application.active_bounds,
        canonical_anchor,
    )?;
    expanded_base.interactive.append(&mut menu.interactive);
    let previous_application = geometry.application.clone();
    let previous_regions = geometry
        .context_menu_hit_regions
        .clone()
        .or_else(|| geometry.hit_regions.clone());
    let geometry_changed = previous_application
        .as_ref()
        .is_none_or(|previous| !same_surface_geometry(previous, &application));
    if geometry_changed {
        apply_native_pet_surface_bounds_transaction_preserving_top_left(
            &window,
            &application,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        )?;
    }
    if let Err(error) = apply_precise_hit_regions(&window, &expanded_base) {
        if geometry_changed {
            if let Err(rollback_error) = rollback_pet_surface_with_bounds_mode(
                &window,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                true,
            ) {
                return Err(format!(
                    "PET_CONTEXT_MENU_SURFACE_FAILED: {error}; PET_CONTEXT_MENU_ROLLBACK_FAILED: {rollback_error}"
                ));
            }
        }
        return Err(format!("PET_CONTEXT_MENU_SURFACE_FAILED: {error}"));
    }
    if geometry.context_menu_base_application.is_none() {
        geometry.context_menu_base_application = Some(base_application);
        geometry.context_menu_base_hit_regions = Some(base_hit_regions);
    }
    geometry.portrait_anchor = Some(application.portrait_anchor);
    geometry.physical_local_anchor = Some(application.physical_local_anchor);
    geometry.active_bounds = Some(application.active_bounds);
    geometry.surface_scale = application.scale_factor * application.content_scale;
    geometry.application = Some(application);
    geometry.hit_regions = Some(expanded_base.clone());
    geometry.context_menu_hit_regions = Some(expanded_base);
    Ok(())
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
    let Some(base_application) = geometry.context_menu_base_application.clone() else {
        geometry.context_menu_open = false;
        geometry.context_menu_hit_regions = None;
        geometry.context_menu_base_hit_regions = None;
        return Ok(());
    };
    let base_hit_regions = geometry
        .context_menu_base_hit_regions
        .clone()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    let previous_application = geometry.application.clone();
    let previous_regions = geometry
        .context_menu_hit_regions
        .clone()
        .or_else(|| geometry.hit_regions.clone());
    let geometry_changed = previous_application
        .as_ref()
        .is_none_or(|previous| !same_surface_geometry(previous, &base_application));
    if geometry_changed {
        apply_native_pet_surface_bounds_transaction_preserving_top_left(
            window,
            &base_application,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        )?;
    }
    if let Err(error) = apply_precise_hit_regions(window, &base_hit_regions) {
        if geometry_changed {
            if let Err(rollback_error) = rollback_pet_surface_with_bounds_mode(
                window,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                true,
            ) {
                return Err(format!(
                    "PET_CONTEXT_MENU_CLOSE_FAILED: {error}; PET_CONTEXT_MENU_ROLLBACK_FAILED: {rollback_error}"
                ));
            }
        }
        return Err(format!("PET_CONTEXT_MENU_CLOSE_FAILED: {error}"));
    }
    geometry.context_menu_open = false;
    geometry.context_menu_hit_regions = None;
    geometry.context_menu_base_application = None;
    geometry.context_menu_base_hit_regions = None;
    geometry.portrait_anchor = Some(base_application.portrait_anchor);
    geometry.physical_local_anchor = Some(base_application.physical_local_anchor);
    geometry.active_bounds = Some(base_application.active_bounds);
    geometry.surface_scale = base_application.scale_factor * base_application.content_scale;
    geometry.application = Some(base_application);
    geometry.hit_regions = Some(base_hit_regions);
    Ok(())
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
    let pending = handle.chat_bridge()?.send_with_attachment(
        window.label(),
        payload.message,
        payload.attachment_id,
    )?;
    tauri::async_runtime::spawn_blocking(move || pending.wait())
        .await
        .map_err(|_| "CHAT_DISPATCH_ABORTED".to_string())?
}

#[tauri::command]
async fn chat_cancel(
    window: WebviewWindow,
    payload: chat_bridge::ChatCancelRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    confirmations: State<'_, native_tool_confirmation::NativeToolConfirmationState>,
) -> Result<chat_bridge::ChatCancelPublication, String> {
    confirmations.cancel_current();
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
async fn start_screen_capture(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SCREEN_CAPTURE_CORE_NOT_READY".to_string())?;
    let app = window.app_handle().clone();
    let capture_manager = captures.inner().clone();
    let task_generation_id = generation_id.clone();
    let task = tauri::async_runtime::spawn_blocking(move || {
        let monitors = capture::monitor_descriptors()?;
        let monitor_count = monitors.len();
        let (session_id, labels, previous) =
            capture_manager.begin_session(&task_generation_id, &monitors)?;
        capture::close_windows(&app, &previous);
        if let Err(error) = capture::show_overlays(&app, &session_id, &labels, &monitors) {
            if let Some(active_labels) = capture_manager.cancel_session(&session_id, &labels[0]) {
                capture::close_windows(&app, &active_labels);
            }
            return Err(error);
        }
        Ok(monitor_count)
    })
    .await
    .map_err(|_| "SCREEN_CAPTURE_PREPARATION_ABORTED".to_string())?;
    let monitor_count = match task {
        Ok(count) => count,
        Err(error) => {
            record_screen_capture(
                &lifecycle.runtime_log,
                &generation_id,
                "screen.capture.failed",
                Severity::Warning,
                json!({"outcome": "failed", "code": error}),
            );
            return Err("无法开始截图，请检查系统屏幕录制权限。".to_string());
        }
    };
    record_screen_capture(
        &lifecycle.runtime_log,
        &generation_id,
        "screen.capture.started",
        Severity::Info,
        json!({"outcome": "started", "monitor_count": monitor_count}),
    );
    Ok(())
}

#[tauri::command]
async fn capture_selected_region(
    window: WebviewWindow,
    payload: capture::CaptureSelectionRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<(), String> {
    let local_rect = capture::logical_selection_to_physical(&window, &payload)?;
    let handle = settings_core_handle(&lifecycle)?;
    let claim =
        captures.claim_selection(&payload.session_id, window.label(), payload.monitor_id)?;
    let app = window.app_handle().clone();
    capture::hide_windows(&app, &claim.window_labels);
    let manager = captures.inner().clone();
    let generation_id = claim.generation_id.clone();
    let runtime_log = lifecycle.runtime_log.clone();
    let task_generation_id = generation_id.clone();
    let task_claim = claim.clone();
    let task_result = tauri::async_runtime::spawn_blocking(move || {
        std::thread::sleep(std::time::Duration::from_millis(100));
        if handle.available_generation_id().ok().flatten().as_deref()
            != Some(task_generation_id.as_str())
        {
            return Err("SCREEN_CAPTURE_GENERATION_STALE".to_string());
        }
        let descriptor = manager.capture(&task_claim, local_rect)?;
        let token = descriptor.resource_token.clone();
        let response = handle.settings_request(
            None,
            "screen.attach",
            json!({"resource": descriptor}),
            std::time::Duration::from_secs(10),
        );
        manager.release(&token, &task_generation_id);
        let payload = settings_response_payload(response?)?;
        let attachment_id = payload
            .get("attachmentId")
            .and_then(Value::as_str)
            .filter(|value| capture::valid_attachment_id(value))
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        let width = payload
            .get("width")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        let height = payload
            .get("height")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        Ok(capture::ScreenAttachmentPublication {
            attachment_id: attachment_id.to_string(),
            width,
            height,
        })
    })
    .await;
    capture::close_windows(&app, &claim.window_labels);
    let result = task_result.map_err(|_| "SCREEN_CAPTURE_TASK_ABORTED".to_string())?;
    match result {
        Ok(publication) => {
            record_screen_capture(
                &runtime_log,
                &generation_id,
                "screen.capture.attached",
                Severity::Info,
                json!({
                    "outcome": "completed",
                    "width": publication.width,
                    "height": publication.height,
                }),
            );
            app.emit_to("main", capture::ATTACHED_EVENT, publication)
                .map_err(|_| "SCREEN_ATTACHMENT_PUBLICATION_FAILED".to_string())?;
            Ok(())
        }
        Err(code) => {
            record_screen_capture(
                &runtime_log,
                &generation_id,
                "screen.capture.failed",
                Severity::Warning,
                json!({"outcome": "failed", "code": code}),
            );
            let _ = app.emit_to(
                "main",
                capture::ERROR_EVENT,
                json!({"message": "截图失败，请检查系统屏幕录制权限后重试。"}),
            );
            Err("截图失败，请检查系统屏幕录制权限后重试。".to_string())
        }
    }
}

#[tauri::command]
async fn cancel_screen_capture(
    window: WebviewWindow,
    payload: capture::CaptureCancelRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<(), String> {
    let labels = captures
        .cancel_session(&payload.session_id, window.label())
        .ok_or_else(|| "SCREEN_CAPTURE_SESSION_STALE".to_string())?;
    capture::close_windows(window.app_handle(), &labels);
    let generation_id = lifecycle
        .handle
        .as_ref()
        .and_then(|handle| handle.available_generation_id().ok().flatten())
        .unwrap_or_else(|| "unavailable".to_string());
    record_screen_capture(
        &lifecycle.runtime_log,
        &generation_id,
        "screen.capture.cancelled",
        Severity::Info,
        json!({"outcome": "cancelled"}),
    );
    let _ = window
        .app_handle()
        .emit_to("main", capture::CANCELLED_EVENT, ());
    Ok(())
}

#[tauri::command]
async fn release_screen_attachment(
    window: WebviewWindow,
    payload: capture::AttachmentReleaseRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<bool, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if !capture::valid_attachment_id(&payload.attachment_id) {
        return Err("SCREEN_ATTACHMENT_ID_INVALID".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let response = tauri::async_runtime::spawn_blocking(move || {
        handle.settings_request(
            None,
            "screen.release",
            json!({"attachmentId": payload.attachment_id}),
            std::time::Duration::from_secs(3),
        )
    })
    .await
    .map_err(|_| "SCREEN_ATTACHMENT_RELEASE_ABORTED".to_string())??;
    Ok(settings_response_payload(response)?
        .get("accepted")
        .and_then(Value::as_bool)
        .unwrap_or(false))
}

fn record_screen_capture(
    runtime_log: &RuntimeLogService,
    generation_id: &str,
    event: &'static str,
    severity: Severity,
    attributes: Value,
) {
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, "screen", event, "Screen capture state changed")
            .correlation(Correlation {
                generation_id: Some(generation_id.to_string()),
                ..Correlation::default()
            })
            .attributes(attributes),
    );
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TtsPrepareSegmentRequest {
    operation_id: String,
    segment_index: u64,
}

#[tauri::command]
async fn tts_prepare_segment(
    window: WebviewWindow,
    payload: TtsPrepareSegmentRequest,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<audio::AudioDescriptor, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if payload.operation_id.trim().is_empty() || payload.operation_id.len() > 128 {
        return Err("TTS_SEGMENT_NOT_AUTHORIZED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "STALE_GENERATION".to_string())?;
    let callback_app = app_handle.clone();
    let observer_handle = handle.clone();
    let observer_generation = generation_id.clone();
    let playback_log = runtime_log.inner().clone();
    let playback_generation = generation_id.clone();
    let manager = audio_state.manager(
        &generation_id,
        Arc::new(move |event| {
            record_tts_playback(&playback_log, &playback_generation, &event);
            let _ = callback_app.emit_to("main", "sakura://tts-playback-event", event.clone());
            observe_tts_playback(observer_handle.clone(), observer_generation.clone(), event);
        }),
    )?;
    let registration_revision = manager.registration_revision()?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.synthesis.start",
        json!({
            "operationId": payload.operation_id,
            "segmentIndex": payload.segment_index,
        }),
        std::time::Duration::from_secs(305),
    )
    .await?;
    if handle
        .available_generation_id()
        .map_err(str::to_string)?
        .as_deref()
        != Some(generation_id.as_str())
    {
        return Err("STALE_GENERATION".to_string());
    }
    let descriptor: audio::AudioDescriptor =
        serde_json::from_value(settings_response_payload(response)?)
            .map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
    manager.register_at_revision(&descriptor, registration_revision)?;
    app_handle
        .emit_to(
            "main",
            "sakura://tts-synthesis-event",
            json!({
                "type": "tts.synthesis.ready",
                "operationId": payload.operation_id,
                "segmentIndex": payload.segment_index,
                "descriptor": descriptor.clone(),
            }),
        )
        .map_err(|_| "TTS_PUBLICATION_FAILED".to_string())?;
    Ok(descriptor)
}

#[tauri::command]
fn tts_play_prepared(
    window: WebviewWindow,
    payload: audio::PlayPreparedRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "STALE_GENERATION".to_string())?
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "STALE_GENERATION".to_string())?;
    audio_state.current(&generation_id)?.play(payload)
}

#[tauri::command]
fn tts_stop_playback(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "STALE_GENERATION".to_string())?
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "STALE_GENERATION".to_string())?;
    match audio_state.current(&generation_id) {
        Ok(manager) => manager.stop_and_clear(),
        Err(error) if error == "STALE_GENERATION" => Ok(()),
        Err(error) => Err(error),
    }
}

#[tauri::command]
async fn settings_voice_get(
    window: WebviewWindow,
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
        "tts.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TTS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_voice_status_get(
    window: WebviewWindow,
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
        "tts.status.get",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TTS_STATUS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_voice_save(
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
        "tts.settings.save",
        json!({"settings": draft}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    handle.restart().map_err(str::to_string)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_voice_test(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    draft: Value,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let callback_app = app_handle.clone();
    let observer_handle = handle.clone();
    let observer_generation = core_generation_id.clone();
    let playback_log = runtime_log.inner().clone();
    let playback_generation = core_generation_id.clone();
    let manager = audio_state.manager(
        &core_generation_id,
        Arc::new(move |event| {
            record_tts_playback(&playback_log, &playback_generation, &event);
            let _ = callback_app.emit("sakura://tts-playback-event", event.clone());
            observe_tts_playback(observer_handle.clone(), observer_generation.clone(), event);
        }),
    )?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.settings.test",
        json!({"settings": draft}),
        std::time::Duration::from_secs(305),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let provider = payload
        .get("provider")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "gpt-sovits" | "genie-tts"))
        .ok_or_else(|| "TTS_SETTINGS_RESPONSE_INVALID".to_string())?
        .to_string();
    payload
        .as_object_mut()
        .ok_or_else(|| "TTS_SETTINGS_RESPONSE_INVALID".to_string())?
        .remove("provider");
    let descriptor: audio::AudioDescriptor =
        serde_json::from_value(payload).map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
    manager.register(&descriptor)?;
    let terminal = manager.observe_terminal("tts-settings-test")?;
    manager.play(audio::PlayPreparedRequest {
        opaque_id: descriptor.opaque_id,
        playback_id: "tts-settings-test".to_string(),
    })?;
    let terminal_event = tauri::async_runtime::spawn_blocking(move || loop {
        match terminal.recv_timeout(std::time::Duration::from_secs(120)) {
            Ok(event) if event.state != "started" => return Some(event),
            Ok(_) => continue,
            Err(_) => return None,
        }
    })
    .await
    .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
    let status = terminal_event
        .as_ref()
        .map_or("failed", |event| event.state);
    let error_code = terminal_event
        .as_ref()
        .and_then(|event| event.error.as_ref().map(|error| error.code))
        .or_else(|| (terminal_event.is_none()).then_some("AUDIO_PLAYBACK_FAILED"));
    Ok(json!({
        "provider": provider,
        "status": status,
        "errorCode": error_code,
    }))
}

#[tauri::command]
async fn settings_voice_bundle_status(
    window: WebviewWindow,
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
        "tts.bundle.status",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TTS_BUNDLE_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_voice_bundle_install(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    bundle_key: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.bundle.install",
        json!({"bundleKey": bundle_key}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_voice_bundle_cancel(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    task_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.bundle.cancel",
        json!({"taskId": task_id}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    Ok(payload)
}

fn observe_tts_playback(
    handle: shell_lifecycle::ShellLifecycleHandle,
    generation_id: String,
    event: audio::AudioPlaybackEvent,
) {
    tauri::async_runtime::spawn(async move {
        let current = handle.available_generation_id().ok().flatten();
        if current.as_deref() != Some(generation_id.as_str()) {
            return;
        }
        let error_code = event.error.as_ref().map(|error| error.code);
        let _ = dispatch_settings_request(
            handle,
            None,
            "tts.playback.observe",
            json!({
                "playbackId": event.playback_id,
                "recordingId": event.recording_id,
                "state": event.state,
                "errorCode": error_code,
            }),
            std::time::Duration::from_secs(2),
        )
        .await;
    });
}

fn record_tts_playback(
    runtime_log: &RuntimeLogService,
    generation_id: &str,
    event: &audio::AudioPlaybackEvent,
) {
    let (event_name, message, severity) = match event.state {
        "started" => (
            "tts.playback.started",
            "TTS playback started",
            Severity::Info,
        ),
        "finished" => (
            "tts.playback.finished",
            "TTS playback finished",
            Severity::Info,
        ),
        "stopped" => (
            "tts.playback.stopped",
            "TTS playback stopped",
            Severity::Info,
        ),
        _ => (
            "tts.playback.failed",
            "TTS playback failed",
            Severity::Error,
        ),
    };
    let code = event.error.as_ref().map(|error| error.code);
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, "tts", event_name, message)
            .correlation(Correlation {
                generation_id: Some(generation_id.to_string()),
                request_id: Some(event.playback_id.clone()),
                ..Correlation::default()
            })
            .attributes(json!({
                "playbackId": event.playback_id,
                "recordingId": event.recording_id,
                "status": event.state,
                "code": code,
            })),
    );
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
fn apply_input_visual_effect(
    window: WebviewWindow,
    values: character_appearance::AppearanceValues,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<input_visual_effect::InputVisualEffectStatus, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    values.validate()?;
    glass.update_appearance(&window, &values)
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
    trace: Option<interaction_latency::InteractionTraceContext>,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<character_appearance::AppearancePublication, String> {
    interaction_latency::command("settings.appearance-preview", trace, || {
        product_shell::validate_settings_window(&window)?;
        let (publication, settings_background_changed) =
            appearance.preview_bound_session(shell.generation()?, values)?;
        // Layout/font previews are high-frequency. Avoid a redundant native background update on
        // every slider tick; on Windows that call otherwise sits directly on the visual preview path.
        if settings_background_changed {
            sync_settings_window_appearance_background(&window, &publication)?;
            appearance.mark_settings_background_synced(&publication.values)?;
        }
        emit_appearance(&app_handle, publication.clone())?;
        Ok(publication)
    })
}

#[tauri::command]
fn settings_character_appearance_scale_gesture(
    window: WebviewWindow,
    active: bool,
    trace: Option<interaction_latency::InteractionTraceContext>,
    app_handle: tauri::AppHandle,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    let publication_trace = trace.clone();
    interaction_latency::command("settings.portrait-scale-gesture", trace, || {
        product_shell::validate_settings_window(&window)?;
        interaction_latency::lock(
            geometry_state.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?
        .portrait_scale_gesture_active = active;
        interaction_latency::stage("main-event-emit-start");
        let emitted = if let Some(trace) = publication_trace {
            app_handle.emit_to(
                "main",
                "sakura://portrait-scale-gesture",
                serde_json::json!({ "active": active, "trace": trace }),
            )
        } else {
            app_handle.emit_to("main", "sakura://portrait-scale-gesture", active)
        };
        emitted.map_err(|error| format!("failed to publish portrait scale gesture: {error}"))?;
        interaction_latency::stage("main-event-emit-return");
        Ok(())
    })
}

#[tauri::command]
fn settings_character_appearance_scale_frame(
    window: WebviewWindow,
    portrait_scale_percent: u16,
    trace: Option<interaction_latency::InteractionTraceContext>,
    app_handle: tauri::AppHandle,
) -> Result<(), String> {
    let publication_trace = trace.clone();
    interaction_latency::command("settings.portrait-scale-frame", trace, || {
        product_shell::validate_settings_window(&window)?;
        if !(window_interaction::PORTRAIT_SCALE_MIN_PERCENT
            ..=window_interaction::PORTRAIT_SCALE_MAX_PERCENT)
            .contains(&portrait_scale_percent)
        {
            return Err("PORTRAIT_SCALE_OUT_OF_RANGE".to_string());
        }
        interaction_latency::stage("main-event-emit-start");
        let emitted = if let Some(trace) = publication_trace {
            app_handle.emit_to(
                "main",
                "sakura://portrait-scale-frame",
                serde_json::json!({
                    "portraitScalePercent": portrait_scale_percent,
                    "trace": trace,
                }),
            )
        } else {
            app_handle.emit_to(
                "main",
                "sakura://portrait-scale-frame",
                portrait_scale_percent,
            )
        };
        emitted.map_err(|error| format!("failed to publish portrait scale frame: {error}"))?;
        interaction_latency::stage("main-event-emit-return");
        Ok(())
    })
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CharacterAppearanceLayoutFrame {
    control_panel_width: u16,
    bubble_max_height: u16,
    control_panel_vertical_offset: i16,
    input_bar_offset: u16,
}

impl CharacterAppearanceLayoutFrame {
    fn validate(&self) -> Result<(), String> {
        let limits = character_appearance::AppearanceLimits::default();
        for (name, value, range) in [
            (
                "controlPanelWidth",
                self.control_panel_width,
                limits.control_panel_width,
            ),
            (
                "bubbleMaxHeight",
                self.bubble_max_height,
                limits.bubble_max_height,
            ),
            (
                "inputBarOffset",
                self.input_bar_offset,
                limits.input_bar_offset,
            ),
        ] {
            if !(range[0]..=range[1]).contains(&value) {
                return Err(format!("APPEARANCE_FIELD_INVALID:{name}"));
            }
        }
        let vertical = limits.control_panel_vertical_offset;
        if !(vertical[0]..=vertical[1]).contains(&self.control_panel_vertical_offset) {
            return Err("APPEARANCE_FIELD_INVALID:controlPanelVerticalOffset".to_string());
        }
        Ok(())
    }
}

#[tauri::command]
fn settings_character_appearance_layout_gesture(
    window: WebviewWindow,
    active: bool,
    trace: Option<interaction_latency::InteractionTraceContext>,
    app_handle: tauri::AppHandle,
) -> Result<(), String> {
    let publication_trace = trace.clone();
    interaction_latency::command("settings.control-surface-gesture", trace, || {
        product_shell::validate_settings_window(&window)?;
        interaction_latency::stage("main-event-emit-start");
        let emitted = if let Some(trace) = publication_trace {
            app_handle.emit_to(
                "main",
                "sakura://control-surface-gesture",
                serde_json::json!({ "active": active, "trace": trace }),
            )
        } else {
            app_handle.emit_to("main", "sakura://control-surface-gesture", active)
        };
        emitted.map_err(|error| format!("failed to publish control surface gesture: {error}"))?;
        interaction_latency::stage("main-event-emit-return");
        Ok(())
    })
}

#[tauri::command]
fn settings_character_appearance_layout_frame(
    window: WebviewWindow,
    values: CharacterAppearanceLayoutFrame,
    trace: Option<interaction_latency::InteractionTraceContext>,
    app_handle: tauri::AppHandle,
) -> Result<(), String> {
    let publication_trace = trace.clone();
    interaction_latency::command("settings.control-surface-frame", trace, || {
        product_shell::validate_settings_window(&window)?;
        values.validate()?;
        let mut payload = serde_json::json!({
            "controlPanelWidth": values.control_panel_width,
            "bubbleMaxHeight": values.bubble_max_height,
            "controlPanelVerticalOffset": values.control_panel_vertical_offset,
            "inputBarOffset": values.input_bar_offset,
            // Only Windows owns one backing envelope covering every legal layout adjustment.
            // Other platforms must continue committing their native surface on each frame.
            "deferNative": cfg!(windows),
        });
        if let Some(trace) = publication_trace {
            payload["trace"] = serde_json::to_value(trace)
                .map_err(|_| "INTERACTION_LATENCY_TRACE_SERIALIZATION_FAILED".to_string())?;
        }
        interaction_latency::stage("main-event-emit-start");
        app_handle
            .emit_to("main", "sakura://control-surface-frame", payload)
            .map_err(|error| format!("failed to publish control surface frame: {error}"))?;
        interaction_latency::stage("main-event-emit-return");
        Ok(())
    })
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
async fn settings_tools_get(
    window: WebviewWindow,
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
        "tools.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    tool_settings::validate_snapshot(&payload, false)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TOOLS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_agent_trace_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    settings: State<'_, agent_trace_settings::AgentTraceSettingsState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let values = settings.get()?;
    Ok(json!({
        "schemaVersion": 1,
        "enabled": values.enabled,
        "windowGeneration": window_generation,
        "coreGenerationId": core_generation_id,
    }))
}

#[tauri::command]
async fn settings_agent_trace_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: agent_trace_settings::AgentTraceSettings,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    state: State<'_, agent_trace_settings::AgentTraceSettingsState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    state.save(settings)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    handle.restart().map_err(str::to_string)?;
    Ok(json!({"saved": true, "changePlan": "core_restart_required"}))
}

#[tauri::command]
async fn settings_tools_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    tool_settings::validate_draft(&settings)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tools.settings.save",
        json!({"settings": settings}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    tool_settings::validate_snapshot(&payload, true)?;
    handle.restart().map_err(str::to_string)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_mcp_get(
    window: WebviewWindow,
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
        "mcp.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    mcp_settings::validate_snapshot(&payload, false)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "MCP_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_mcp_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    mcp_settings::validate_draft(&settings)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "mcp.settings.save",
        json!({"settings": settings}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    mcp_settings::validate_snapshot(&payload, true)?;
    handle.restart().map_err(str::to_string)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_get(
    window: WebviewWindow,
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
        "plugins.settings.get",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    plugin_settings::validate_snapshot(&payload, false)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    plugin_settings::validate_draft(&settings)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.save",
        json!({"revision": revision, "settings": settings}),
        std::time::Duration::from_secs(8),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_snapshot(&payload, true)?;
    handle.restart().map_err(str::to_string)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_action(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    plugin_id: String,
    section_id: String,
    action_id: String,
    values: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.action",
        json!({"pluginId": plugin_id, "sectionId": section_id, "actionId": action_id, "values": values}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_action_result(&payload)?;
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

async fn dispatch_memory_request(
    window: &WebviewWindow,
    shell: &product_shell::ProductShellState,
    lifecycle: &State<'_, ShellLifecycleState>,
    window_generation: u64,
    core_generation_id: &str,
    name: &'static str,
    payload: Value,
    deadline: std::time::Duration,
) -> Result<Value, String> {
    let started_at = std::time::Instant::now();
    let deadline_ms = deadline.as_millis();
    append_runtime_diagnostic_event(
        &lifecycle.runtime_log,
        "shell_memory_gateway",
        "request_started",
        json!({
            "stage": "dispatch",
            "outcome": "started",
            "request": name,
            "deadlineMs": deadline_ms,
            "windowGeneration": window_generation,
        }),
    );
    let result: Result<Value, String> = async {
        memory_gateway::authorize_settings_window(window.label())?;
        let handle = settings_core_handle(lifecycle)?;
        assert_settings_identity(shell, &handle, window_generation, core_generation_id)?;
        let response =
            dispatch_settings_request(handle.clone(), None, name, payload, deadline).await?;
        let payload = settings_response_payload(response)?;
        assert_settings_identity(shell, &handle, window_generation, core_generation_id)?;
        memory_gateway::validate_public_response(&payload)?;
        Ok(payload)
    }
    .await;
    let elapsed_ms = started_at.elapsed().as_millis();
    match &result {
        Ok(payload) => {
            let status = payload
                .get("status")
                .and_then(Value::as_str)
                .filter(|status| {
                    matches!(
                        *status,
                        "ready" | "loading" | "degraded" | "read_only" | "failed" | "stopped"
                    )
                })
                .unwrap_or("completed");
            append_runtime_diagnostic_event(
                &lifecycle.runtime_log,
                "shell_memory_gateway",
                "request_completed",
                json!({
                    "stage": "dispatch",
                    "outcome": "completed",
                    "request": name,
                    "status": status,
                    "deadlineMs": deadline_ms,
                    "elapsedMs": elapsed_ms,
                    "windowGeneration": window_generation,
                }),
            );
        }
        Err(error) => append_runtime_diagnostic_event(
            &lifecycle.runtime_log,
            "shell_memory_gateway",
            "request_completed",
            json!({
                "stage": "dispatch",
                "outcome": "failed",
                "request": name,
                "category": classify_memory_request_error(error),
                "deadlineMs": deadline_ms,
                "elapsedMs": elapsed_ms,
                "windowGeneration": window_generation,
            }),
        ),
    }
    result
}

#[tauri::command]
async fn settings_memory_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::authorize_settings_window(window.label())?;
    let handle = settings_core_handle(&lifecycle).map_err(|error| {
        append_runtime_diagnostic_event(
            &lifecycle.runtime_log,
            "shell_memory_gateway",
            "request_not_dispatched",
            json!({
                "stage": "core_identity",
                "outcome": "failed",
                "request": "memory.settings.get",
                "category": classify_memory_request_error(&error),
            }),
        );
        error
    })?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| {
            append_runtime_diagnostic_event(
                &lifecycle.runtime_log,
                "shell_memory_gateway",
                "request_not_dispatched",
                json!({
                    "stage": "core_identity",
                    "outcome": "failed",
                    "request": "memory.settings.get",
                    "category": "core_unavailable",
                    "windowGeneration": window_generation,
                }),
            );
            "SETTINGS_CORE_UNAVAILABLE".to_string()
        })?;
    let mut payload = dispatch_memory_request(
        &window,
        &shell,
        &lifecycle,
        window_generation,
        &core_generation_id,
        "memory.settings.get",
        json!({}),
        std::time::Duration::from_secs(memory_gateway::MEMORY_DEADLINE_SECONDS),
    )
    .await?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "MEMORY_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_memory_search(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    query: String,
    limit: i64,
    layer: Option<String>,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    let mut payload = json!({"query": query, "limit": limit});
    if let Some(layer) = layer.filter(|value| !value.is_empty()) {
        payload
            .as_object_mut()
            .expect("memory search payload")
            .insert("layer".to_string(), json!(layer));
    }
    memory_gateway::validate_search(&payload)?;
    dispatch_memory_request(
        &window,
        &shell,
        &lifecycle,
        window_generation,
        &core_generation_id,
        "memory.search",
        payload,
        std::time::Duration::from_secs(memory_gateway::MEMORY_DEADLINE_SECONDS),
    )
    .await
}

#[tauri::command]
async fn settings_memory_upsert(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    memory: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::validate_upsert(&memory)?;
    dispatch_memory_request(
        &window,
        &shell,
        &lifecycle,
        window_generation,
        &core_generation_id,
        "memory.upsert",
        memory,
        std::time::Duration::from_secs(memory_gateway::MEMORY_DEADLINE_SECONDS),
    )
    .await
}

#[tauri::command]
async fn settings_memory_delete(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    let payload = json!({"id": id});
    memory_gateway::validate_delete(&payload)?;
    dispatch_memory_request(
        &window,
        &shell,
        &lifecycle,
        window_generation,
        &core_generation_id,
        "memory.delete",
        payload,
        std::time::Duration::from_secs(memory_gateway::MEMORY_DEADLINE_SECONDS),
    )
    .await
}

#[tauri::command]
async fn settings_memory_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::validate_settings_save(&settings)?;
    let handle = settings_core_handle(&lifecycle)?;
    let result = dispatch_memory_request(
        &window,
        &shell,
        &lifecycle,
        window_generation,
        &core_generation_id,
        "memory.settings.save",
        settings,
        std::time::Duration::from_secs(memory_gateway::MEMORY_DEADLINE_SECONDS),
    )
    .await?;
    if result.get("changePlan").and_then(Value::as_str) == Some("core_restart_required") {
        handle.restart().map_err(str::to_string)?;
    }
    Ok(result)
}

#[tauri::command]
async fn settings_memory_model_download(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::authorize_settings_window(window.label())?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    start_memory_model_task(
        window,
        handle,
        window_generation,
        core_generation_id,
        "memory.model.download",
        json!({}),
        None,
    )
}

#[tauri::command]
async fn settings_memory_model_import(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::authorize_settings_window(window.label())?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let Some(selection_token) = memory_gateway::select_and_register_archive(&core_generation_id)?
    else {
        return Ok(json!({"accepted": false, "cancelled": true}));
    };
    start_memory_model_task(
        window,
        handle,
        window_generation,
        core_generation_id,
        "memory.model.import",
        json!({"selectionToken": selection_token.clone()}),
        Some(selection_token),
    )
}

fn start_memory_model_task(
    window: WebviewWindow,
    handle: shell_lifecycle::ShellLifecycleHandle,
    window_generation: u64,
    core_generation_id: String,
    name: &'static str,
    payload: Value,
    selection_token: Option<String>,
) -> Result<Value, String> {
    let registration = memory_gateway::begin_model_task(&core_generation_id, window_generation)?;
    let task_id = registration.task_id.clone();
    let task_handle = registration.task_handle.clone();
    let request_task_id = task_id.clone();
    std::thread::Builder::new()
        .name("sakura-memory-model-request".to_string())
        .spawn(move || {
            let result = handle
                .settings_request(
                    Some(&request_task_id),
                    name,
                    payload,
                    std::time::Duration::from_secs(
                        memory_gateway::MEMORY_MODEL_TASK_DEADLINE_SECONDS,
                    ),
                )
                .and_then(settings_response_payload);
            if result.is_err() {
                memory_gateway::fail_model_task(
                    &request_task_id,
                    "MEMORY_MODEL_TASK_INTERRUPTED",
                    "记忆模型任务因 Core 连接中断而停止。",
                );
            }
            if let Some(token) = selection_token {
                memory_gateway::remove_archive_selection(&token);
            }
        })
        .map_err(|_| "MEMORY_MODEL_TASK_START_FAILED".to_string())?;
    let event_task_id = task_id.clone();
    std::thread::Builder::new()
        .name("sakura-memory-model-events".to_string())
        .spawn(move || {
            while let Ok(publication) = registration.receiver.recv() {
                let terminal =
                    publication
                        .get("type")
                        .and_then(Value::as_str)
                        .is_some_and(|kind| {
                            matches!(
                                kind,
                                "memory.model.completed"
                                    | "memory.model.failed"
                                    | "memory.model.cancelled"
                            )
                        });
                let _ = window.emit(memory_gateway::MEMORY_MODEL_EVENT, publication);
                if terminal {
                    break;
                }
            }
            memory_gateway::remove_model_task(&event_task_id);
        })
        .map_err(|_| "MEMORY_MODEL_EVENT_START_FAILED".to_string())?;
    Ok(json!({
        "accepted": true,
        "taskId": task_id,
        "taskHandle": task_handle,
        "status": "starting",
    }))
}

#[tauri::command]
async fn settings_memory_model_cancel(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    task_handle: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    memory_gateway::authorize_settings_window(window.label())?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let task_id = memory_gateway::resolve_cancel_handle(
        &task_handle,
        &core_generation_id,
        window_generation,
    )?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "memory.model.cancel",
        json!({"taskHandle": task_id}),
        std::time::Duration::from_secs(memory_gateway::MEMORY_MODEL_DEADLINE_SECONDS),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    settings_response_payload(response)
}

#[tauri::command]
fn begin_control_surface_preview(
    window: WebviewWindow,
    revision: u64,
    trace: Option<interaction_latency::InteractionTraceContext>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    interaction_latency::command("main.begin-control-surface-preview", trace, || {
        if window.label() != "main" {
            return Err("PET_WINDOW_REQUIRED".to_string());
        }
        let mut geometry = interaction_latency::lock(
            geometry_state.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;
        if !geometry.request_control_surface_preview(revision) {
            return Ok(());
        }
        if cfg!(windows) {
            NativeWindowInteractionBackend
                .relax_hit_regions(&window)
                .map_err(|error| error.to_string())?;
        }
        geometry.activate_control_surface_preview(revision);
        Ok(())
    })
}

#[tauri::command]
fn end_control_surface_preview(
    window: WebviewWindow,
    revision: u64,
    trace: Option<interaction_latency::InteractionTraceContext>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    interaction_latency::command("main.end-control-surface-preview", trace, || {
        if window.label() != "main" {
            return Err("PET_WINDOW_REQUIRED".to_string());
        }
        let mut geometry = interaction_latency::lock(
            geometry_state.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;
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
            // Keep the preview flag retryable so a later settle can restore the precise mask.
            geometry.control_surface_preview_active = true;
            return Err(error);
        }
        Ok(())
    })
}

#[tauri::command]
fn prepare_portrait_transition(
    window: WebviewWindow,
    portrait_key: String,
    revision: u64,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<LayoutApplication>, String> {
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
    let next_mask = resources.active_portrait_alpha_mask(&portrait_key, &generation_id)?;
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if revision < geometry.portrait_hit_revision {
        return Ok(None);
    }
    let state = geometry
        .state
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let contract = layout_contract()?;
    let monitor = target_monitor(&window, geometry.portrait_anchor)?;
    // Keep the native frame stable for the duration of the cross-fade, but only
    // across the two portraits involved in this transition. macOS must not use
    // its Windows-style resident all-layout envelope in the idle path.
    let old_bounds = window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
        &contract,
        state,
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
    )?;
    let new_bounds = window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
        &contract,
        state,
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
        Some(&next_mask),
    )?;
    let application = apply_window_layout(
        &contract,
        state,
        geometry.applied_revision,
        &monitor,
        geometry.portrait_anchor,
        window_interaction::union_surface_bounds(old_bounds, new_bounds),
    )?;
    let mut combined = build_native_interaction_regions(
        &contract,
        &application,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        geometry.portrait_scale_percent,
    )?;
    let mut next_logical = window_interaction::logical_hit_regions_with_control_surface(
        &contract,
        state,
        Some(next_mask.source_size()),
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
    )?;
    let next_native_portrait_alpha_mask =
        window_interaction::apply_portrait_alpha_bounds(&mut next_logical, Some(&next_mask))?;
    let next_target = next_native_portrait_alpha_mask
        .as_ref()
        .and_then(|_| next_logical.drag.first().copied());
    let next_transition_drag = next_native_portrait_alpha_mask
        .as_ref()
        .zip(next_target)
        .map(|(mask, target)| (mask.clone(), target));
    if let Some(next_native_portrait_alpha_mask) = next_native_portrait_alpha_mask {
        let mut next_physical = window_interaction::scale_hit_regions_for_surface(
            &next_logical,
            application.scale_factor * application.content_scale,
            application.active_bounds,
            contract.viewport.portrait_anchor,
        )?;
        next_physical.portrait_alpha_mask = Some(next_native_portrait_alpha_mask.clone());
        combined
            .extra_native_rectangles
            .extend(window_interaction::native_hit_rectangles(
                &next_physical,
                [
                    application.physical_placement.width,
                    application.physical_placement.height,
                ],
            )?);
    }
    let previous_application = geometry.application.clone();
    let previous_regions = geometry.hit_regions.clone();
    let geometry_unchanged = previous_application
        .as_ref()
        .is_some_and(|previous| same_surface_geometry(previous, &application));
    // During a macOS portrait transition, avoid re-submitting an unchanged AppKit frame.
    // Even setFrame_display(false) can make WebKit rebuild the root surface before the CSS
    // cross-fade has painted, exposing the stale stage as a clipped bubble/input frame. The hit
    // router can be widened in place; defer all native frame/glass work until the final frame.
    let commit = if cfg!(target_os = "macos") && geometry_unchanged {
        apply_precise_hit_regions(&window, &combined)
    } else {
        NativeWindowInteractionBackend
            .prepare_window(&window)
            .map_err(|error| error.to_string())
            .and_then(|_| precommit_webview_surface(&window, &application))
            .and_then(|_| {
                NativeWindowInteractionBackend
                    .apply_bounds(&window, &application.physical_placement)
                    .map_err(|error| error.to_string())
            })
            .and_then(|_| apply_precise_hit_regions(&window, &combined))
    };
    if let Err(error) = commit {
        return match rollback_pet_surface(
            &window,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        ) {
            Ok(()) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED_PREVIOUS_RESTORED: {error}"
            )),
            Err(rollback_error) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED: {error}; PET_SURFACE_ROLLBACK_FAILED: {rollback_error}"
            )),
        };
    }
    if !geometry_unchanged {
        if let Some(surface) = geometry.control_surface.as_ref() {
            glass.update_control_surface(&window, surface, &application, None, None)?;
        }
    }
    geometry.portrait_transition_active = cfg!(target_os = "macos");
    geometry.portrait_transition_drag = next_transition_drag;
    geometry.portrait_transition_pending = None;
    geometry.portrait_hit_generation = Some(generation_id);
    geometry.portrait_hit_revision = revision;
    geometry.portrait_hit_relaxed = true;
    geometry.portrait_scale_preview_active = false;
    geometry.portrait_anchor = Some(application.portrait_anchor);
    geometry.physical_local_anchor = Some(application.physical_local_anchor);
    geometry.active_bounds = Some(application.active_bounds);
    geometry.surface_scale = application.scale_factor * application.content_scale;
    geometry.application = Some(application.clone());
    geometry.hit_regions = Some(combined);
    geometry.context_menu_hit_regions = None;
    geometry.context_menu_open = false;
    geometry.context_menu_base_application = None;
    geometry.context_menu_base_hit_regions = None;
    Ok(Some(application))
}

#[tauri::command]
fn begin_portrait_scale_preview(
    window: WebviewWindow,
    revision: u64,
    trace: Option<interaction_latency::InteractionTraceContext>,
    lifecycle: State<'_, ShellLifecycleState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<PortraitScalePreview>, String> {
    interaction_latency::command("main.begin-portrait-scale-preview", trace, || {
        if window.label() != "main" {
            return Err("PET_WINDOW_REQUIRED".to_string());
        }
        let available_generation = lifecycle
            .handle
            .as_ref()
            .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?
            .available_generation_id()
            .map_err(str::to_string)?;
        let mut geometry = interaction_latency::lock(
            geometry_state.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;
        let generation_id = resolve_portrait_hit_generation(
            available_generation,
            geometry.portrait_hit_generation.as_deref(),
        )?;
        let same_generation =
            geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
        if same_generation && revision <= geometry.portrait_hit_revision {
            return Ok(None);
        }
        // A scale gesture owns the native surface transaction. If it starts while
        // a portrait cross-fade is waiting for its final commit, discard that
        // stale transition rather than letting it resize the window later.
        geometry.portrait_transition_pending = None;
        geometry.portrait_transition_active = false;
        geometry.portrait_transition_drag = None;

        let mut preview_application = if defers_native_portrait_scale_frames() {
            geometry.application.clone()
        } else {
            None
        };
        if cfg!(windows) && !geometry.portrait_scale_preview_active {
            let state = geometry
                .state
                .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
            let contract = layout_contract()?;
            let monitor = target_monitor(&window, geometry.portrait_anchor)?;
            let application = compute_pet_window_layout(
                &contract,
                state,
                geometry.applied_revision,
                &monitor,
                geometry.portrait_anchor,
                geometry.portrait_scale_percent,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                true,
            )?;
            let hit_regions = build_native_interaction_regions(
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                geometry.portrait_scale_percent,
            )?;
            let previous_application = geometry.application.clone();
            let previous_regions = geometry.hit_regions.clone();
            NativeWindowInteractionBackend
                .relax_hit_regions(&window)
                .map_err(|error| error.to_string())?;
            apply_native_pet_surface_bounds_transaction(
                &window,
                &application,
                previous_application.as_ref(),
                previous_regions.as_ref(),
            )?;
            if let Some(surface) = geometry.control_surface.as_ref() {
                glass.update_control_surface(&window, surface, &application, None, None)?;
            }
            geometry.portrait_anchor = Some(application.portrait_anchor);
            geometry.physical_local_anchor = Some(application.physical_local_anchor);
            geometry.active_bounds = Some(application.active_bounds);
            geometry.surface_scale = application.scale_factor * application.content_scale;
            geometry.application = Some(application.clone());
            geometry.hit_regions = Some(hit_regions);
            preview_application = Some(application);
        }

        // Keep this as an explicit platform branch instead of folding it into the Windows path:
        // Win32 retains its stable HWND/all-layout envelope and SetWindowRgn transaction unchanged.
        if cfg!(target_os = "macos") && !geometry.portrait_scale_preview_active {
            let state = geometry
                .state
                .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
            let contract = layout_contract()?;
            let monitor = target_monitor(&window, geometry.portrait_anchor)?;
            let application = compute_pet_window_layout(
                &contract,
                state,
                geometry.applied_revision,
                &monitor,
                geometry.portrait_anchor,
                geometry.portrait_scale_percent,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                true,
            )?;
            let previous_application = geometry.application.clone();
            let previous_regions = geometry.hit_regions.clone();
            let hit_regions = apply_native_pet_surface_transaction(
                &window,
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                geometry.portrait_scale_percent,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                geometry.portrait_hit_relaxed,
            )?;
            geometry.portrait_anchor = Some(application.portrait_anchor);
            geometry.physical_local_anchor = Some(application.physical_local_anchor);
            geometry.active_bounds = Some(application.active_bounds);
            geometry.surface_scale = application.scale_factor * application.content_scale;
            geometry.application = Some(application.clone());
            geometry.hit_regions = Some(hit_regions);
            preview_application = Some(application);
        }

        // GTK/GDK owns a separate Linux transaction. X11/XWayland can atomically move+resize;
        // native Wayland receives only the compositor-owned resize request. Both use the same
        // precommitted 150% surface snapshot and retain precise surface-local input routing.
        if cfg!(target_os = "linux")
            && geometry.portrait_scale_gesture_active
            && !geometry.portrait_scale_preview_active
        {
            let state = geometry
                .state
                .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
            let contract = layout_contract()?;
            let monitor = target_monitor(&window, geometry.portrait_anchor)?;
            let application = compute_pet_window_layout(
                &contract,
                state,
                geometry.applied_revision,
                &monitor,
                geometry.portrait_anchor,
                geometry.portrait_scale_percent,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                true,
            )?;
            let previous_application = geometry.application.clone();
            let previous_regions = geometry.hit_regions.clone();
            let hit_regions = apply_native_pet_surface_transaction(
                &window,
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
                geometry.portrait_scale_percent,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                geometry.portrait_hit_relaxed,
            )?;
            geometry.portrait_anchor = Some(application.portrait_anchor);
            geometry.physical_local_anchor = Some(application.physical_local_anchor);
            geometry.active_bounds = Some(application.active_bounds);
            geometry.surface_scale = application.scale_factor * application.content_scale;
            geometry.application = Some(application.clone());
            geometry.hit_regions = Some(hit_regions);
            preview_application = Some(application);
        }

        if !same_generation {
            geometry.portrait_alpha_mask = None;
            geometry.portrait_hit_key = None;
        }
        geometry.portrait_hit_generation = Some(generation_id);
        geometry.portrait_hit_revision = revision;
        geometry.portrait_hit_relaxed = defers_portrait_scale_hit_region_frames();
        geometry.portrait_scale_preview_active = true;
        Ok(Some(PortraitScalePreview {
            application: preview_application,
            deferred_native: defers_native_portrait_scale_frames(),
            deferred_hit_regions: defers_portrait_scale_hit_region_frames(),
        }))
    })
}

#[tauri::command]
fn activate_portrait_hit_test(
    window: WebviewWindow,
    portrait_key: String,
    revision: u64,
    portrait_scale_percent: u16,
    trace: Option<interaction_latency::InteractionTraceContext>,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<LayoutApplication>, String> {
    interaction_latency::command("main.activate-portrait-hit-test", trace, || {
        if window.label() != "main" {
            return Err("PET_WINDOW_REQUIRED".to_string());
        }
        let available_generation = lifecycle
            .handle
            .as_ref()
            .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?
            .available_generation_id()
            .map_err(str::to_string)?;
        let mut geometry = interaction_latency::lock(
            geometry_state.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;
        let generation_id = resolve_portrait_hit_generation(
            available_generation,
            geometry.portrait_hit_generation.as_deref(),
        )?;
        let same_generation =
            geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
        if portrait_hit_revision_is_stale(
            same_generation,
            revision,
            geometry.portrait_hit_revision,
            geometry.portrait_hit_relaxed,
        ) {
            return Ok(None);
        }
        let transition_pending = cfg!(target_os = "macos") && geometry.portrait_transition_active;
        let cache_matches = same_generation
            && geometry.portrait_hit_key.as_deref() == Some(portrait_key.as_str())
            && geometry.portrait_alpha_mask.is_some();
        let mut resolved_alpha_mask = if cache_matches {
            geometry.portrait_alpha_mask.clone()
        } else {
            None
        };
        if !cache_matches {
            drop(geometry);
            let mask_started = std::time::Instant::now();
            let alpha_mask = resources.active_portrait_alpha_mask(&portrait_key, &generation_id)?;
            interaction_latency::stage_elapsed("portrait-mask-loaded", mask_started);
            geometry = interaction_latency::lock(
                geometry_state.inner(),
                "geometry-mutex-reacquire-wait-start",
                "geometry-mutex-reacquired",
            )?;
            let same_generation =
                geometry.portrait_hit_generation.as_deref() == Some(generation_id.as_str());
            if portrait_hit_revision_is_stale(
                same_generation,
                revision,
                geometry.portrait_hit_revision,
                geometry.portrait_hit_relaxed,
            ) {
                return Ok(None);
            }
            if transition_pending && cfg!(target_os = "macos") {
                // Keep the currently committed alpha mask authoritative until the
                // WebView has painted the new portrait and commit_portrait_transition
                // applies its final native frame. This also lets a scale gesture that
                // interrupts the transition continue from the visible portrait.
                resolved_alpha_mask = Some(alpha_mask);
            } else {
                geometry.portrait_alpha_mask = Some(alpha_mask.clone());
                geometry.portrait_hit_generation = Some(generation_id.clone());
                geometry.portrait_hit_key = Some(portrait_key.clone());
                resolved_alpha_mask = Some(alpha_mask);
            }
        }
        let state = geometry
            .state
            .ok_or_else(|| "pet layout is not ready for portrait hit testing".to_string())?;
        let contract = layout_contract()?;
        let monitor = target_monitor(&window, geometry.portrait_anchor)?;
        let stabilize_portrait_scale = geometry.stabilizes_portrait_scale_bounds();
        let defer_precise_hit_regions = geometry.defers_precise_portrait_scale_hit_regions();
        let defer_portrait_transition_native =
            cfg!(target_os = "macos") && geometry.portrait_transition_active;
        let portrait_alpha_mask = resolved_alpha_mask
            .as_ref()
            .or(geometry.portrait_alpha_mask.as_ref());
        let application = compute_pet_window_layout(
            &contract,
            state,
            geometry.applied_revision,
            &monitor,
            geometry.portrait_anchor,
            portrait_scale_percent,
            geometry.control_surface.as_ref(),
            portrait_alpha_mask,
            stabilize_portrait_scale,
        )?;
        let previous_application = geometry.application.clone();
        let previous_regions = geometry.hit_regions.clone();
        let hit_regions = if defer_precise_hit_regions {
            let hit_regions = build_native_interaction_regions(
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                portrait_alpha_mask,
                portrait_scale_percent,
            )?;
            apply_native_pet_surface_bounds_transaction(
                &window,
                &application,
                previous_application.as_ref(),
                previous_regions.as_ref(),
            )?;
            hit_regions
        } else if defer_portrait_transition_native {
            // Keep the union envelope committed by prepare_portrait_transition on
            // screen while the CSS cross-fade is active. The final native frame is
            // committed only after the WebView has painted the final stage offset.
            build_native_interaction_regions(
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                portrait_alpha_mask,
                portrait_scale_percent,
            )?
        } else {
            apply_native_pet_surface_transaction(
                &window,
                &contract,
                &application,
                geometry.control_surface.as_ref(),
                portrait_alpha_mask,
                portrait_scale_percent,
                previous_application.as_ref(),
                previous_regions.as_ref(),
                geometry.portrait_hit_relaxed,
            )?
        };
        // Loading the portrait alpha mask can shrink the HWND's active bounds after the control
        // surface was first committed. Keep the HWND-local native glass clip paired with that
        // final surface origin; otherwise the WebView moves left while the glass remains at its
        // startup x coordinate.
        if !defer_portrait_transition_native {
            if let Some(surface) = geometry.control_surface.as_ref() {
                glass.update_control_surface(&window, surface, &application, None, None)?;
            }
        }
        if defer_portrait_transition_native {
            let portrait_alpha_mask = resolved_alpha_mask
                .ok_or_else(|| "PORTRAIT_TRANSITION_MASK_NOT_READY".to_string())?;
            geometry.portrait_transition_pending = Some(PendingPortraitTransition {
                revision,
                generation_id: generation_id.clone(),
                portrait_key: portrait_key.clone(),
                portrait_alpha_mask,
                application: application.clone(),
                hit_regions,
            });
            geometry.portrait_hit_revision = revision;
            geometry.portrait_scale_preview_active = false;
            return Ok(Some(application));
        }
        geometry.portrait_hit_generation = Some(generation_id);
        geometry.portrait_hit_key = Some(portrait_key);
        geometry.portrait_hit_revision = revision;
        geometry.portrait_hit_relaxed = defer_precise_hit_regions;
        geometry.portrait_scale_preview_active = stabilize_portrait_scale;
        geometry.portrait_scale_percent = portrait_scale_percent;
        geometry.portrait_transition_active = false;
        geometry.portrait_transition_drag = None;
        geometry.portrait_anchor = Some(application.portrait_anchor);
        geometry.physical_local_anchor = Some(application.physical_local_anchor);
        geometry.active_bounds = Some(application.active_bounds);
        geometry.surface_scale = application.scale_factor * application.content_scale;
        geometry.application = Some(application.clone());
        geometry.hit_regions = Some(hit_regions);
        Ok(Some(application))
    })
}

#[tauri::command]
fn commit_portrait_transition(
    window: WebviewWindow,
    revision: u64,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<LayoutApplication>, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let pending = match geometry.portrait_transition_pending.clone() {
        Some(pending) if pending.revision == revision => pending,
        _ => return Ok(None),
    };
    let previous_application = geometry.application.clone();
    let previous_regions = geometry.hit_regions.clone();
    let geometry_unchanged = previous_application
        .as_ref()
        .is_some_and(|previous| same_surface_geometry(previous, &pending.application));
    let commit = (|| -> Result<(), String> {
        if !geometry_unchanged {
            NativeWindowInteractionBackend
                .prepare_window(&window)
                .map_err(|error| error.to_string())?;
            precommit_webview_surface(&window, &pending.application)?;
            NativeWindowInteractionBackend
                .apply_bounds(&window, &pending.application.physical_placement)
                .map_err(|error| error.to_string())?;
        }
        apply_precise_hit_regions(&window, &pending.hit_regions)
    })();
    if let Err(error) = commit {
        return match rollback_pet_surface(
            &window,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        ) {
            Ok(()) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED_PREVIOUS_RESTORED: {error}"
            )),
            Err(rollback_error) => Err(format!(
                "PET_SURFACE_COMMIT_FAILED: {error}; PET_SURFACE_ROLLBACK_FAILED: {rollback_error}"
            )),
        };
    }
    if let Some(surface) = geometry.control_surface.as_ref() {
        glass.update_control_surface(&window, surface, &pending.application, None, None)?;
    }
    geometry.portrait_transition_pending = None;
    geometry.portrait_alpha_mask = Some(pending.portrait_alpha_mask);
    geometry.portrait_hit_generation = Some(pending.generation_id);
    geometry.portrait_hit_key = Some(pending.portrait_key);
    geometry.portrait_hit_revision = revision;
    geometry.portrait_hit_relaxed = false;
    geometry.portrait_scale_preview_active = false;
    geometry.portrait_transition_active = false;
    geometry.portrait_transition_drag = None;
    geometry.portrait_anchor = Some(pending.application.portrait_anchor);
    geometry.physical_local_anchor = Some(pending.application.physical_local_anchor);
    geometry.active_bounds = Some(pending.application.active_bounds);
    geometry.surface_scale = pending.application.scale_factor * pending.application.content_scale;
    geometry.application = Some(pending.application.clone());
    geometry.hit_regions = Some(pending.hit_regions);
    geometry.context_menu_hit_regions = None;
    geometry.context_menu_open = false;
    geometry.context_menu_base_application = None;
    geometry.context_menu_base_hit_regions = None;
    Ok(Some(pending.application))
}

#[tauri::command]
fn settle_portrait_scale_surface(
    window: WebviewWindow,
    revision: u64,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<LayoutApplication>, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.can_settle_portrait_scale(revision) {
        return Ok(None);
    }
    let state = geometry
        .state
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let contract = layout_contract()?;
    let monitor = target_monitor(&window, geometry.portrait_anchor)?;
    let application = compute_pet_window_layout(
        &contract,
        state,
        geometry.applied_revision,
        &monitor,
        geometry.portrait_anchor,
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        false,
    )?;
    let previous_application = geometry.application.clone();
    let previous_regions = geometry.hit_regions.clone();
    let hit_regions = apply_native_pet_surface_transaction(
        &window,
        &contract,
        &application,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        geometry.portrait_scale_percent,
        previous_application.as_ref(),
        previous_regions.as_ref(),
        geometry.portrait_hit_relaxed,
    )?;
    if let Some(surface) = geometry.control_surface.as_ref() {
        glass.update_control_surface(&window, surface, &application, None, None)?;
    }
    geometry.portrait_scale_preview_active = false;
    geometry.portrait_hit_relaxed = false;
    geometry.portrait_anchor = Some(application.portrait_anchor);
    geometry.physical_local_anchor = Some(application.physical_local_anchor);
    geometry.active_bounds = Some(application.active_bounds);
    geometry.surface_scale = application.scale_factor * application.content_scale;
    geometry.application = Some(application.clone());
    geometry.hit_regions = Some(hit_regions);
    Ok(Some(application))
}

#[tauri::command]
fn wp_3_03_acceptance_enabled() -> bool {
    cfg!(debug_assertions)
        && std::env::var("SAKURA_WP_3_03_ACCEPTANCE").ok().as_deref() == Some("1")
}

#[tauri::command]
fn interaction_latency_diagnostics_enabled() -> bool {
    interaction_latency::enabled()
}

#[tauri::command]
fn input_visual_effect_status(
    state: State<'_, input_visual_effect::InputVisualEffectState>,
) -> input_visual_effect::InputVisualEffectStatus {
    state.status()
}

#[tauri::command]
fn record_interaction_latency_trace(
    window: WebviewWindow,
    entries: Vec<interaction_latency::FrontendTraceEntry>,
) -> Result<(), String> {
    interaction_latency::record_frontend(window.label(), entries)
}

#[tauri::command]
fn record_runtime_diagnostics(
    window: WebviewWindow,
    entries: Vec<WebviewDiagnosticEntry>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<(), String> {
    if entries.is_empty() || entries.len() > 64 {
        return Err("RUNTIME_DIAGNOSTIC_BATCH_INVALID".to_string());
    }
    let prepared = entries
        .into_iter()
        .map(|entry| runtime_log.prepare_webview(window.label(), entry))
        .collect::<Result<Vec<_>, _>>()
        .map_err(str::to_string)?;
    for event in prepared {
        let _ = runtime_log.submit(event);
    }
    Ok(())
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
            let lifecycle = app.state::<ShellLifecycleState>();
            append_runtime_diagnostic_event(
                &lifecycle.runtime_log,
                "settings_window",
                "settings_open_requested",
                json!({"stage": "window_open", "outcome": "started"}),
            );
            let result = product_shell::show_or_focus_settings(app);
            append_runtime_diagnostic_event(
                &lifecycle.runtime_log,
                "settings_window",
                "settings_open_returned",
                json!({
                    "stage": "window_open",
                    "outcome": if result.is_ok() { "completed" } else { "failed" },
                    "category": if result.is_ok() { Value::Null } else { json!("window_operation_failed") },
                }),
            );
            result
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
    if let Err(error) = window.destroy() {
        let _ = shell.cancel_close();
        return Err(error.to_string());
    }
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
fn wp_4_01_manual_root(path: std::path::PathBuf) -> Result<std::path::PathBuf, String> {
    if !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("WP_4_01_MANUAL_ROOT_INVALID".to_string());
    }
    let root = path
        .canonicalize()
        .map_err(|_| "WP_4_01_MANUAL_ROOT_INVALID".to_string())?;
    let directory = root
        .parent()
        .ok_or_else(|| "WP_4_01_MANUAL_ROOT_INVALID".to_string())?;
    let temp = std::env::temp_dir()
        .canonicalize()
        .map_err(|_| "WP_4_01_MANUAL_TEMP_UNAVAILABLE".to_string())?;
    let name = directory
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "WP_4_01_MANUAL_ROOT_INVALID".to_string())?;
    if directory.parent() != Some(temp.as_path())
        || !name.starts_with(WP_4_01_MANUAL_DIRECTORY_PREFIX)
        || root.file_name().and_then(|value| value.to_str()) != Some("app-root")
        || !directory.join(".sakura-wp-4-01-manual").is_file()
        || !root.join("data/config/system_config.yaml").is_file()
        || !root.join("data/config/api.yaml").is_file()
        || !root.join("data/config/characters.yaml").is_file()
    {
        return Err("WP_4_01_MANUAL_ROOT_INVALID".to_string());
    }
    Ok(root)
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

    #[cfg(target_os = "linux")]
    if std::env::var_os("GDK_BACKEND").is_none() && std::env::var_os("DISPLAY").is_some() {
        // Prefer X11/XWayland before GTK is initialized so Runtime v2 retains
        // absolute positioning and negative-coordinate multi-monitor semantics.
        std::env::set_var("GDK_BACKEND", "x11");
    }

    #[cfg(all(windows, debug_assertions))]
    if phase_1b_runtime_acceptance::run_fake_core_child_if_requested() {
        return;
    }

    #[cfg(not(debug_assertions))]
    if std::env::var_os("SAKURA_WP_3_06_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_WP_3_06_ACCEPTANCE_MODE").is_some()
    {
        show_startup_message(
            "Sakura WP-3-06 验收启动失败",
            "release 构建不接受验收根覆盖。",
            true,
        );
        std::process::exit(2);
    }

    #[cfg(not(debug_assertions))]
    if std::env::var_os("SAKURA_WP_3V_01_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_WP_3V_01_ACCEPTANCE_MODE").is_some()
    {
        show_startup_message(
            "Sakura WP-3V-01 验收启动失败",
            "release 构建不接受组合验收根覆盖。",
            true,
        );
        std::process::exit(2);
    }

    #[cfg(not(debug_assertions))]
    if std::env::var_os("SAKURA_WP_4_01_MANUAL_ROOT").is_some() {
        show_startup_message(
            "Sakura WP-4-01 验收启动失败",
            "release 构建不接受验收根覆盖。",
            true,
        );
        std::process::exit(2);
    }

    #[cfg(debug_assertions)]
    let wp_3_06_acceptance = match wp_3_06_data_compat_acceptance::request_from_environment() {
        Ok(request) => request,
        Err(error) => {
            show_startup_message("Sakura WP-3-06 验收启动失败", &error, true);
            std::process::exit(2);
        }
    };

    #[cfg(debug_assertions)]
    let wp_3v_01_acceptance =
        match wp_3v_01_assistant_architecture_acceptance::request_from_environment() {
            Ok(request) => request,
            Err(error) => {
                show_startup_message("Sakura WP-3V-01 验收启动失败", &error, true);
                std::process::exit(2);
            }
        };

    #[cfg(debug_assertions)]
    if wp_3_06_acceptance.is_some() && wp_3v_01_acceptance.is_some() {
        show_startup_message(
            "Sakura 验收启动失败",
            "WP-3-06 与 WP-3V-01 验收模式不能同时启用。",
            true,
        );
        std::process::exit(2);
    }

    let instance_lock_backend = NativeInstanceLockBackend;
    let _instance_guard = match instance_lock_backend.acquire(SHARED_INSTANCE_ID) {
        Ok(InstanceLockAcquire::Acquired(guard)) => guard,
        Ok(InstanceLockAcquire::AlreadyRunning) => {
            #[cfg(debug_assertions)]
            if let Some(request) = &wp_3_06_acceptance {
                let _ = wp_3_06_data_compat_acceptance::record_lock_conflict(request);
                eprintln!("{ALREADY_RUNNING_TITLE}: {ALREADY_RUNNING_BODY}");
                return;
            }
            #[cfg(debug_assertions)]
            if let Some(request) = &wp_3v_01_acceptance {
                let _ = wp_3v_01_assistant_architecture_acceptance::record_lock_conflict(request);
                eprintln!("{ALREADY_RUNNING_TITLE}: {ALREADY_RUNNING_BODY}");
                return;
            }
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
        SETTINGS_MEMORY_SCRIPT.len(),
        SETTINGS_TOOLS_SCRIPT.len(),
    );

    let acceptance_mode = std::env::var_os("SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY").is_some()
        || std::env::var_os("SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY").is_some();
    let runtime_request = development_runtime_request();
    #[cfg(debug_assertions)]
    let mut runtime_request = runtime_request;
    #[cfg(debug_assertions)]
    if let Some(request) = &wp_3_06_acceptance {
        runtime_request.assistant_root = request.app_root.clone();
    }
    #[cfg(debug_assertions)]
    if let Some(request) = &wp_3v_01_acceptance {
        runtime_request.assistant_root = request.app_root.clone();
    }
    #[cfg(debug_assertions)]
    if let Some(root) = std::env::var_os(WP_4_01_MANUAL_ROOT_ENV) {
        runtime_request.assistant_root = wp_4_01_manual_root(root.into())
            .expect("WP-4-01 manual acceptance root must be isolated and complete");
    }
    let character_resource_root = runtime_request.assistant_root.clone();
    let runtime_log =
        RuntimeLogService::start(character_resource_root.join("data/logs/sakura-runtime.log"));
    let mut runtime_log_shutdown = RuntimeLogShutdown::new(runtime_log.clone());
    install_runtime_panic_hook(runtime_log.clone());
    interaction_latency::initialize(runtime_log.clone());
    let _ = runtime_log.submit(RuntimeLogEvent::rust(
        Severity::Info,
        "shell",
        "shell.started",
        "Runtime shell started",
    ));
    let mut shell_lifecycle_session = (!acceptance_mode).then(|| {
        shell_lifecycle::ShellLifecycleSession::start_observed(runtime_request, runtime_log.clone())
    });
    let shell_lifecycle_handle = shell_lifecycle_session
        .as_ref()
        .map(shell_lifecycle::ShellLifecycleSession::handle);
    let ui_config_repository = ui_config::UiConfigRepository::new(
        character_resource_root.join("data/runtime_v2/config/ui.json"),
    );
    let app = tauri::Builder::default()
        .manage(Mutex::new(WindowGeometrySession::default()))
        .manage(product_shell::ProductShellState::default())
        .manage(native_tool_confirmation::NativeToolConfirmationState::default())
        .manage(runtime_log.clone())
        .manage(ShellLifecycleState {
            handle: shell_lifecycle_handle.clone(),
            runtime_log: runtime_log.clone(),
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
        .manage(agent_trace_settings::AgentTraceSettingsState::new(
            character_resource_root.join("data/config/system_config.yaml"),
        ))
        .manage(audio::AudioState::new(character_resource_root.clone()))
        .manage(Arc::new(capture::CaptureManager::new()))
        .manage(input_visual_effect::InputVisualEffectState::from_environment())
        .register_uri_scheme_protocol(
            character_presentation::CHARACTER_PROTOCOL,
            character_protocol_response,
        )
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .ok_or("main pet window was not created")?;
            prepare_initial_pet_window(&window)?;
            let glass = app.state::<input_visual_effect::InputVisualEffectState>();
            glass.install(&window);
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
                match event {
                    tauri::WindowEvent::Moved(position) => {
                        let session = window.state::<Mutex<WindowGeometrySession>>();
                        if let Err(error) = try_observe_deferred_window_position(
                            session.inner(),
                            window_geometry::PhysicalPoint {
                                x: position.x,
                                y: position.y,
                            },
                        ) {
                            eprintln!("failed to observe pet window movement: {error}");
                        }
                    }
                    tauri::WindowEvent::CloseRequested { api, .. } => {
                        api.prevent_close();
                        let lifecycle = window.state::<ShellLifecycleState>();
                        if let Err(error) = request_app_exit(window.app_handle(), &lifecycle) {
                            product_shell::emit_product_menu_error(window.app_handle(), error);
                        }
                    }
                    _ => {}
                }
                return;
            }
            if window.label() != product_shell::SETTINGS_WINDOW_LABEL {
                return;
            }
            let state = window.state::<product_shell::ProductShellState>();
            let lifecycle = window.state::<ShellLifecycleState>();
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    let authorized = state.consume_close_authorization().unwrap_or(false);
                    append_runtime_diagnostic_event(
                        &lifecycle.runtime_log,
                        "settings_window",
                        "settings_close_requested",
                        json!({
                            "stage": "window_close",
                            "outcome": if authorized { "authorized" } else { "prompted" },
                        }),
                    );
                    if !authorized {
                        api.prevent_close();
                        let _ = window.emit(product_shell::SETTINGS_CLOSE_REQUESTED_EVENT, ());
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    let geometry = window.state::<Mutex<WindowGeometrySession>>();
                    if let Ok(mut geometry) = geometry.lock() {
                        if geometry.portrait_scale_gesture_active {
                            geometry.portrait_scale_gesture_active = false;
                            let _ = window.app_handle().emit_to(
                                "main",
                                "sakura://portrait-scale-gesture",
                                false,
                            );
                        }
                    }
                    let appearance =
                        window.state::<character_appearance::CharacterAppearanceState>();
                    if let Ok(Some(publication)) = appearance.close_session() {
                        let _ = emit_appearance(window.app_handle(), publication);
                    }
                    let reopen = state.window_destroyed().unwrap_or(false);
                    append_runtime_diagnostic_event(
                        &lifecycle.runtime_log,
                        "settings_window",
                        "settings_window_destroyed",
                        json!({
                            "stage": "window_close",
                            "outcome": "completed",
                            "reopenQueued": reopen,
                        }),
                    );
                    if reopen {
                        append_runtime_diagnostic_event(
                            &lifecycle.runtime_log,
                            "settings_window",
                            "settings_reopen_dispatched",
                            json!({"stage": "window_reopen", "outcome": "scheduled"}),
                        );
                        let _ = dispatch_webview_product_menu_action(
                            window.app_handle().clone(),
                            product_shell::ProductMenuAction::OpenSettings,
                        );
                    }
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            current_pet_layout_revision,
            current_pet_surface_diagnostics,
            apply_pet_layout,
            reveal_pet_window,
            start_pet_drag,
            open_pet_context_menu,
            set_pet_context_menu_surface,
            close_pet_context_menu,
            activate_pet_context_menu_action,
            probe_pet_visibility,
            close_pet_window,
            collect_native_diagnostics,
            runtime_lifecycle_snapshot,
            chat_send,
            chat_cancel,
            start_screen_capture,
            capture_selected_region,
            cancel_screen_capture,
            release_screen_attachment,
            tts_prepare_segment,
            tts_play_prepared,
            tts_stop_playback,
            settings_voice_get,
            settings_voice_status_get,
            settings_voice_save,
            settings_voice_test,
            settings_voice_bundle_status,
            settings_voice_bundle_install,
            settings_voice_bundle_cancel,
            current_chat_presentation_timing,
            current_subtitle_language,
            current_character_presentation,
            current_character_appearance,
            apply_input_visual_effect,
            begin_control_surface_preview,
            end_control_surface_preview,
            begin_portrait_scale_preview,
            prepare_portrait_transition,
            activate_portrait_hit_test,
            commit_portrait_transition,
            settle_portrait_scale_surface,
            wp_3_03_acceptance_enabled,
            interaction_latency_diagnostics_enabled,
            input_visual_effect_status,
            record_interaction_latency_trace,
            record_runtime_diagnostics,
            retry_core,
            exit_runtime,
            product_shell::settings_capability_manifest,
            product_shell::reveal_settings_window,
            settings_character_appearance_get,
            settings_character_appearance_preview,
            settings_character_appearance_scale_gesture,
            settings_character_appearance_scale_frame,
            settings_character_appearance_layout_gesture,
            settings_character_appearance_layout_frame,
            settings_character_appearance_save,
            settings_character_appearance_cancel_preview,
            settings_chat_presentation_timing_get,
            settings_chat_presentation_timing_save,
            settings_provider_model_get,
            settings_provider_model_save,
            settings_provider_model_probe,
            settings_provider_model_cancel,
            settings_tools_get,
            settings_tools_save,
            settings_agent_trace_get,
            settings_agent_trace_save,
            settings_mcp_get,
            settings_mcp_save,
            settings_plugins_get,
            settings_plugins_save,
            settings_plugins_action,
            settings_memory_get,
            settings_memory_search,
            settings_memory_upsert,
            settings_memory_delete,
            settings_memory_save,
            settings_memory_model_download,
            settings_memory_model_import,
            settings_memory_model_cancel,
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
    let _ = runtime_log.submit(RuntimeLogEvent::rust(
        Severity::Info,
        "shell",
        "shell.ready",
        "Runtime shell is ready",
    ));

    #[cfg(debug_assertions)]
    let wp_3_06_driver = match wp_3_06_data_compat_acceptance::start_driver(
        wp_3_06_acceptance,
        app.handle().clone(),
        shell_lifecycle_handle.clone(),
    ) {
        Ok(driver) => driver,
        Err(error) => {
            show_startup_message("Sakura WP-3-06 验收启动失败", &error, true);
            runtime_log_shutdown.finish();
            std::process::exit(2);
        }
    };

    #[cfg(debug_assertions)]
    let wp_3v_01_driver = match wp_3v_01_assistant_architecture_acceptance::start_driver(
        wp_3v_01_acceptance,
        app.handle().clone(),
        shell_lifecycle_handle.clone(),
    ) {
        Ok(driver) => driver,
        Err(error) => {
            show_startup_message("Sakura WP-3V-01 验收启动失败", &error, true);
            runtime_log_shutdown.finish();
            std::process::exit(2);
        }
    };

    #[cfg(debug_assertions)]
    let mut phase_1c_acceptance =
        match phase_1c_core_host_acceptance::AcceptanceSession::start_if_requested() {
            Ok(session) => session,
            Err(error) => {
                show_startup_message("Sakura Phase 1C 验收启动失败", &error, true);
                runtime_log_shutdown.finish();
                std::process::exit(2);
            }
        };

    #[cfg(all(windows, debug_assertions))]
    let mut phase_1b_acceptance =
        match phase_1b_runtime_acceptance::AcceptanceSession::start_if_requested() {
            Ok(session) => session,
            Err(error) => {
                show_startup_message("Sakura Phase 1B 验收启动失败", &error, true);
                runtime_log_shutdown.finish();
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
        runtime_log_shutdown.finish();
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
        runtime_log_shutdown.finish();
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }

    let exit_code = app.run_return(move |app_handle, event| match event {
        tauri::RunEvent::Exit => {
            let appearance = app_handle.state::<character_appearance::CharacterAppearanceState>();
            let _ = appearance.close_session();
            if let Some(window) = app_handle.get_webview_window("main") {
                app_handle
                    .state::<input_visual_effect::InputVisualEffectState>()
                    .teardown(&window);
            }
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
    #[cfg(debug_assertions)]
    if let Some(driver) = wp_3_06_driver {
        driver
            .join()
            .expect("WP-3-06 acceptance driver should not panic");
    }
    #[cfg(debug_assertions)]
    if let Some(driver) = wp_3v_01_driver {
        driver
            .join()
            .expect("WP-3V-01 acceptance driver should not panic");
    }
    runtime_log_shutdown.finish();
    if exit_code != 0 {
        std::process::exit(exit_code);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn wp_4_05_playback_failure_is_logged_at_the_audio_callback_source() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sakura-tts-playback-log-{}-{nonce}",
            std::process::id()
        ));
        let path = root.join("data/logs/sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(path.clone());
        record_tts_playback(
            &runtime_log,
            "generation-tts-1",
            &audio::AudioPlaybackEvent {
                playback_id: "playback-1".to_string(),
                recording_id: Some("recording-1".to_string()),
                state: "failed",
                error: Some(audio::AudioPlaybackError {
                    code: "AUDIO_DEVICE_UNAVAILABLE",
                    message: "not persisted",
                }),
            },
        );
        assert!(runtime_log.shutdown(std::time::Duration::from_millis(500)));
        let contents = std::fs::read_to_string(&path).unwrap();
        assert!(contents.contains("[TTS] 语音播放失败"));
        assert!(contents.contains("code=AUDIO_DEVICE_UNAVAILABLE"));
        assert!(!contents.contains("not persisted"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4l_01_memory_diagnostics_stop_writing_the_legacy_file() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sakura-memory-diagnostic-{}-{nonce}",
            std::process::id()
        ));
        let legacy_path = root.join("data/logs/memory-initialization.jsonl");
        let runtime_path = root.join("data/logs/sakura-runtime.log");
        std::fs::create_dir_all(legacy_path.parent().unwrap()).unwrap();
        std::fs::write(&legacy_path, "LEGACY_DIAGNOSTIC\n").unwrap();
        let runtime_log = RuntimeLogService::start(runtime_path.clone());

        append_runtime_diagnostic_event(
            &runtime_log,
            "shell_memory_gateway",
            "request_completed",
            json!({
                "stage": "dispatch",
                "outcome": "failed",
                "request": "memory.settings.get",
                "category": "deadline_exceeded",
                "elapsedMs": 5000,
            }),
        );
        assert!(runtime_log.shutdown(std::time::Duration::from_millis(500)));

        assert_eq!(
            std::fs::read_to_string(&legacy_path).unwrap(),
            "LEGACY_DIAGNOSTIC\n"
        );
        let contents = std::fs::read_to_string(&runtime_path).unwrap();
        let line = contents.lines().next().unwrap();
        assert!(line.contains("[SHELL_MEMORY_GAT] Runtime diagnostic event"));
        assert!(line.contains("stage=dispatch"));
        assert!(line.contains("outcome=failed"));
        assert!(line.contains("elapsed_ms=5000ms"));
        assert!(line.contains("request=memory.settings.get"));
        assert!(line.contains("category=deadline_exceeded"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn memory_request_failures_use_stable_diagnostic_categories() {
        assert_eq!(
            classify_memory_request_error("REQUEST_DEADLINE_EXCEEDED: private transport"),
            "deadline_exceeded"
        );
        assert_eq!(
            classify_memory_request_error("SETTINGS_CORE_GENERATION_MISMATCH"),
            "generation_transition"
        );
        assert_eq!(
            classify_memory_request_error("SETTINGS_CORE_UNAVAILABLE"),
            "core_unavailable"
        );
    }

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
        assert!(!SETTINGS_MEMORY_SCRIPT.is_empty());
        assert!(!SETTINGS_TOOLS_SCRIPT.is_empty());
        assert!(!SETTINGS_CLOSE_FLOW_SCRIPT.is_empty());
        let contract = layout_contract().expect("shared layout contract must parse");
        contract
            .validate()
            .expect("shared layout contract must validate");
    }

    #[test]
    fn only_animated_input_contractions_defer_the_precise_region() {
        let surface = |height| ControlSurfaceLayout {
            bubble_rect: [130, 680, 640, 128],
            input_rect: [130, 818, 640, height],
            controls_rect: [730, 690, 30, 30],
        };
        let motion = Some(InputSurfaceTransition { duration_ms: 220 });
        assert!(is_animated_input_contraction(
            &surface(124),
            &surface(100),
            motion,
        ));
        assert!(is_animated_input_contraction(
            &surface(100),
            &surface(52),
            motion,
        ));
        assert!(!is_animated_input_contraction(
            &surface(52),
            &surface(100),
            motion,
        ));
        assert!(!is_animated_input_contraction(
            &surface(124),
            &surface(100),
            Some(InputSurfaceTransition { duration_ms: 0 }),
        ));
    }

    #[test]
    fn deferred_drag_is_finished_only_by_the_next_layout() {
        let mut session = WindowGeometrySession::default();
        session.physical_local_anchor = Some([320, 640]);

        session.begin_deferred_drag();

        assert!(session.is_deferred_drag_pending());
        session
            .observe_deferred_window_position(window_geometry::PhysicalPoint { x: -900, y: 40 })
            .unwrap();
        assert_eq!(
            session.portrait_anchor,
            Some(window_geometry::PhysicalPoint { x: -580, y: 680 })
        );
        assert!(session.is_deferred_drag_pending());
        session.finish_deferred_drag();
        assert!(!session.is_deferred_drag_pending());
    }

    #[test]
    fn deferred_drag_updates_cached_application_position_before_menu_or_layout() {
        let mut session = WindowGeometrySession::default();
        session.physical_local_anchor = Some([320, 640]);
        let mut application = LayoutApplication::rejected(1, PresentationState::Product, 3);
        application.physical_placement = window_geometry::PhysicalPlacement {
            x: 100,
            y: 200,
            width: 648,
            height: 342,
        };
        application.physical_local_anchor = [320, 640];
        application.portrait_anchor = window_geometry::PhysicalPoint { x: 420, y: 840 };
        session.application = Some(application);
        session.begin_deferred_drag();

        session
            .observe_deferred_window_position(window_geometry::PhysicalPoint { x: -900, y: 40 })
            .unwrap();

        let application = session.application.as_ref().unwrap();
        assert_eq!(
            application.physical_placement.x, -900,
            "a later menu transaction must not resurrect the old placement"
        );
        assert_eq!(application.physical_placement.y, 40);
        assert_eq!(
            application.portrait_anchor,
            window_geometry::PhysicalPoint { x: -580, y: 680 }
        );
    }

    #[test]
    fn moved_observation_skips_reentrant_geometry_lock_and_keeps_deferred_drag() {
        let session = Mutex::new(WindowGeometrySession::default());
        {
            let mut locked = session.lock().unwrap();
            locked.physical_local_anchor = Some([320, 640]);
            locked.begin_deferred_drag();

            assert!(!try_observe_deferred_window_position(
                &session,
                window_geometry::PhysicalPoint { x: -900, y: 40 },
            )
            .unwrap());
            assert_eq!(locked.portrait_anchor, None);
            assert!(locked.is_deferred_drag_pending());
        }

        assert!(try_observe_deferred_window_position(
            &session,
            window_geometry::PhysicalPoint { x: -900, y: 40 },
        )
        .unwrap());
        let observed = session.lock().unwrap();
        assert_eq!(
            observed.portrait_anchor,
            Some(window_geometry::PhysicalPoint { x: -580, y: 680 })
        );
        assert!(observed.is_deferred_drag_pending());
    }

    #[test]
    fn product_menu_session_starts_closed_without_stale_hit_regions() {
        let session = WindowGeometrySession::default();
        assert!(!session.context_menu_open);
        assert!(session.hit_regions.is_none());
        assert!(session.context_menu_base_application.is_none());
        assert!(session.context_menu_base_hit_regions.is_none());
        assert!(!session.portrait_hit_relaxed);
        assert!(!session.portrait_scale_preview_active);
        assert!(!session.portrait_scale_gesture_active);
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
    fn portrait_scale_defers_only_the_windows_region_while_the_gesture_bounds_stay_stable() {
        let mut session = WindowGeometrySession::default();
        session.portrait_scale_preview_active = true;
        session.portrait_scale_gesture_active = true;
        session.portrait_hit_relaxed = true;
        session.portrait_hit_revision = 51;
        assert!(!session.can_settle_portrait_scale(51));
        assert_eq!(
            session.defers_precise_portrait_scale_hit_regions(),
            cfg!(windows)
        );
        assert_eq!(
            session.stabilizes_portrait_scale_bounds(),
            cfg!(any(windows, target_os = "macos", target_os = "linux"))
        );

        session.portrait_hit_revision = 55;
        assert!(!session.can_settle_portrait_scale(51));
        assert!(!session.can_settle_portrait_scale(55));

        session.portrait_scale_gesture_active = false;
        assert!(!session.defers_precise_portrait_scale_hit_regions());
        assert!(!session.can_settle_portrait_scale(51));
        assert!(session.can_settle_portrait_scale(55));

        session.portrait_hit_relaxed = false;
        assert!(!session.defers_precise_portrait_scale_hit_regions());
    }

    #[test]
    fn portrait_hit_generation_survives_transient_core_absence() {
        assert_eq!(
            resolve_portrait_hit_generation(None, Some("generation-a")).unwrap(),
            "generation-a"
        );
        assert_eq!(
            resolve_portrait_hit_generation(Some("generation-b".to_string()), Some("generation-a"))
                .unwrap(),
            "generation-b"
        );
        assert_eq!(
            resolve_portrait_hit_generation(None, None).unwrap_err(),
            "CHARACTER_PRESENTATION_NOT_READY"
        );
    }

    #[test]
    fn stale_portrait_hit_revisions_cannot_overwrite_a_new_gesture() {
        assert!(portrait_hit_revision_is_stale(true, 40, 41, true));
        assert!(portrait_hit_revision_is_stale(true, 40, 41, false));
        assert!(portrait_hit_revision_is_stale(true, 41, 41, false));
        assert!(!portrait_hit_revision_is_stale(true, 41, 41, true));
        assert!(!portrait_hit_revision_is_stale(true, 42, 41, false));
        assert!(!portrait_hit_revision_is_stale(false, 1, 41, false));
    }

    #[test]
    fn visibility_probe_keeps_a_perceptible_native_owned_hidden_interval() {
        assert_eq!(VISIBILITY_PROBE_HIDDEN_DURATION.as_millis(), 220);
    }

    #[test]
    fn hit_only_scale_revisions_do_not_request_root_window_geometry() {
        let mut previous = LayoutApplication::rejected(1, PresentationState::Product, 3);
        previous.active_bounds = [80, 120, 720, 820];
        previous.physical_placement = window_geometry::PhysicalPlacement {
            x: -1200,
            y: 240,
            width: 900,
            height: 1025,
        };
        previous.content_scale = 1.0;
        previous.scale_factor = 1.25;
        let mut next = previous.clone();
        next.revision = 2;
        assert!(same_surface_geometry(&previous, &next));

        next.active_bounds[1] += 1;
        assert!(!same_surface_geometry(&previous, &next));
    }

    #[test]
    fn completed_drag_reuses_unchanged_local_surface_geometry() {
        let mut previous = LayoutApplication::rejected(1, PresentationState::Product, 3);
        previous.active_bounds = [48, 320, 804, 664];
        previous.physical_placement = window_geometry::PhysicalPlacement {
            x: 100,
            y: 200,
            width: 804,
            height: 664,
        };
        let mut moved = previous.clone();
        moved.physical_placement.x += 37;
        moved.physical_placement.y -= 12;

        assert!(!same_surface_geometry(&previous, &moved));
        assert!(same_local_surface_geometry(&previous, &moved));

        moved.scale_factor = 1.25;
        assert!(!same_local_surface_geometry(&previous, &moved));
    }

    #[test]
    fn window_surface_regression_resident_envelope_is_windows_only() {
        assert_eq!(
            uses_resident_stable_surface_bounds(true, false),
            cfg!(windows)
        );
        assert_eq!(
            uses_resident_stable_surface_bounds(false, true),
            cfg!(windows)
        );
        assert!(!uses_resident_stable_surface_bounds(false, false));
    }

    #[test]
    fn linux_uses_the_maximum_envelope_only_during_the_scale_gesture() {
        let mut session = WindowGeometrySession::default();
        assert!(!session.stabilizes_portrait_scale_bounds());
        session.portrait_scale_preview_active = true;
        session.portrait_scale_gesture_active = true;
        assert_eq!(
            session.stabilizes_portrait_scale_bounds(),
            cfg!(any(windows, target_os = "macos", target_os = "linux"))
        );
        assert_eq!(
            defers_native_portrait_scale_frames(),
            cfg!(any(windows, target_os = "macos", target_os = "linux"))
        );
        assert_eq!(defers_portrait_scale_hit_region_frames(), cfg!(windows));
        session.portrait_scale_gesture_active = false;
        assert!(!session.stabilizes_portrait_scale_bounds());
    }

    #[test]
    fn window_surface_regression_platform_capabilities_keep_transient_macos_bounds() {
        let windows = portrait_scale_platform_capabilities(platform::PlatformTarget::WindowsX64);
        assert!(windows.stable_bounds_during_gesture);
        assert!(!windows.precise_hit_regions_during_gesture);
        assert!(windows.resident_stable_bounds);

        for target in [
            platform::PlatformTarget::MacOsArm64,
            platform::PlatformTarget::LinuxX64,
        ] {
            let capabilities = portrait_scale_platform_capabilities(target);
            assert!(capabilities.stable_bounds_during_gesture);
            assert!(capabilities.precise_hit_regions_during_gesture);
            assert_eq!(capabilities.resident_stable_bounds, false);
        }
        assert_eq!(
            current_portrait_scale_platform_capabilities().stable_bounds_during_gesture,
            defers_native_portrait_scale_frames()
        );
        assert_eq!(
            !current_portrait_scale_platform_capabilities().precise_hit_regions_during_gesture,
            defers_portrait_scale_hit_region_frames()
        );
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
