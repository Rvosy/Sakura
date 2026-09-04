#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod audio;
mod autostart_settings;
mod capture;
mod character_appearance;
mod character_presentation;
mod character_studio_window;
mod chat_bridge;
mod chat_settings;
mod color_picker;
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
mod history_window;
mod input_visual_effect;
mod interaction_latency;
mod legacy_import;
#[cfg(target_os = "macos")]
mod macos_input_glass;
#[cfg(target_os = "macos")]
mod macos_surface_snapshot;
#[allow(dead_code)] // Consumed by the serial Supervisor beginning in WP-1B-02.
mod managed_process_tree;
#[allow(dead_code)] // Compile-only platform contracts are wired by WP-1P-02 through WP-1P-05.
mod platform;
mod plugin_settings;
mod product_shell;
mod runtime_log;
mod runtime_log_window;
mod shared_instance;
mod shell_lifecycle;
mod telemetry;
mod tool_settings;
mod ui_config;
mod update_settings;
mod window_geometry;
mod window_interaction;
#[cfg(windows)]
mod windows_glass_poc;
#[cfg(windows)]
mod windows_liquid_glass;
#[cfg(windows)]
mod windows_liquid_glass_native;
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
    apply_window_layout, apply_window_layout_with_fit_bounds,
    clip_expanded_surface_bounds_to_work_area, AnchorPolicy, ControlSurfaceLayout,
    InputSurfaceTransition, LayoutApplication, LayoutContract, LayoutRevisionGuard,
    MonitorDescriptor, PhysicalRect, PresentationState,
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
const SETTINGS_TOOLS_SCRIPT: &str = include_str!("../../frontend/settings/tools-runtime.js");
const SETTINGS_SCREEN_AWARENESS_SCRIPT: &str =
    include_str!("../../frontend/settings/screen-awareness-runtime.js");
const SETTINGS_AUTOSTART_SCRIPT: &str =
    include_str!("../../frontend/settings/autostart-runtime.js");
const LAYOUT_CONTRACT_JSON: &str = include_str!("../../frontend/pet/layout-contract.json");
const VISIBILITY_PROBE_HIDDEN_DURATION: std::time::Duration = std::time::Duration::from_millis(220);
#[cfg(windows)]
const CONTROL_CONTRACTION_REGION_GRACE_MS: u64 = 40;
const ALREADY_RUNNING_TITLE: &str = "Sakura 已在运行";
const ALREADY_RUNNING_BODY: &str =
    "另一个 Sakura Runtime v2 实例正在运行。请先退出现有实例，再重试。";
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

#[derive(Clone)]
struct PendingInputSurfaceTransition {
    revision: u64,
    previous_surface: ControlSurfaceLayout,
    target_surface: ControlSurfaceLayout,
    application: LayoutApplication,
    transition: InputSurfaceTransition,
    contraction_hit_regions: Option<window_interaction::PhysicalHitRegions>,
}

#[derive(Clone)]
#[cfg_attr(not(windows), allow(dead_code))]
struct PendingBubbleSurfaceTransition {
    revision: u64,
    transition: InputSurfaceTransition,
    contraction_hit_regions: window_interaction::PhysicalHitRegions,
}

#[derive(Clone, Copy)]
struct StartedInputExpansion {
    previous_height: u32,
    target_height: u32,
    transition: InputSurfaceTransition,
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
    anchor_user_positioned: bool,
    deferred_drag_pending: bool,
    portrait_alpha_mask: Option<character_presentation::PortraitAlphaMask>,
    portrait_transition_active: bool,
    portrait_transition_drag: Option<(
        character_presentation::PortraitAlphaMask,
        window_interaction::LogicalHitRect,
    )>,
    portrait_transition_pending: Option<PendingPortraitTransition>,
    input_surface_transition_pending: Option<PendingInputSurfaceTransition>,
    bubble_surface_transition_pending: Option<PendingBubbleSurfaceTransition>,
    input_expansion_started: Option<StartedInputExpansion>,
    portrait_hit_generation: Option<String>,
    portrait_hit_key: Option<String>,
    portrait_hit_resource_id: Option<String>,
    portrait_hit_revision: u64,
    portrait_hit_relaxed: bool,
    portrait_scale_preview_active: bool,
    portrait_scale_gesture_active: bool,
    control_surface_preview_active: bool,
    control_surface_preview_revision: u64,
    portrait_scale_percent: u16,
    bubble_auto_expand: bool,
    context_menu_open: bool,
    context_menu_rect: Option<[u32; 4]>,
    context_menu_hit_regions: Option<window_interaction::PhysicalHitRegions>,
    context_menu_base_application: Option<LayoutApplication>,
    context_menu_base_hit_regions: Option<window_interaction::PhysicalHitRegions>,
    control_surface: Option<ControlSurfaceLayout>,
    hit_regions: Option<window_interaction::PhysicalHitRegions>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ContextMenuRegionPolicy {
    RelaxedWholeWindow,
    PreciseOverlay,
}

const fn current_context_menu_region_policy() -> ContextMenuRegionPolicy {
    if cfg!(windows) {
        ContextMenuRegionPolicy::RelaxedWholeWindow
    } else {
        ContextMenuRegionPolicy::PreciseOverlay
    }
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
            anchor_user_positioned: false,
            deferred_drag_pending: false,
            portrait_alpha_mask: None,
            portrait_transition_active: false,
            portrait_transition_drag: None,
            portrait_transition_pending: None,
            input_surface_transition_pending: None,
            bubble_surface_transition_pending: None,
            input_expansion_started: None,
            portrait_hit_generation: None,
            portrait_hit_key: None,
            portrait_hit_resource_id: None,
            portrait_hit_revision: 0,
            portrait_hit_relaxed: false,
            portrait_scale_preview_active: false,
            portrait_scale_gesture_active: false,
            control_surface_preview_active: false,
            control_surface_preview_revision: 0,
            portrait_scale_percent: 100,
            bubble_auto_expand: false,
            context_menu_open: false,
            context_menu_rect: None,
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

#[tauri::command]
async fn first_run_start_core(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    let _ = runtime_log.submit(RuntimeLogEvent::rust(
        Severity::Info,
        "first_run",
        "first_run.core_start.started",
        "首次配置正在启动 Core",
    ));
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "LIFECYCLE_UNAVAILABLE".to_string())?
        .clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        handle.start_core_and_wait_available(std::time::Duration::from_secs(40))
    })
    .await
    .map_err(|_| "CORE_START_ABORTED".to_string())?
    .map_err(str::to_string);
    match &result {
        Ok(()) => {
            let _ = runtime_log.submit(RuntimeLogEvent::rust(
                Severity::Info,
                "first_run",
                "first_run.core_start.completed",
                "首次配置 Core 已就绪",
            ));
        }
        Err(error) => {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Error,
                    "first_run",
                    "first_run.core_start.failed",
                    "首次配置 Core 启动失败",
                )
                .attributes(json!({
                    "code": stable_runtime_code(error, "FIRST_RUN_CORE_START_FAILED"),
                    "diagnostic": error,
                    "error_type": "CoreStartError",
                    "reason_code": "FIRST_RUN_CORE_START_FAILED",
                    "stage": "core_start"
                })),
            );
        }
    }
    result
}

fn stable_runtime_code(error: &str, fallback: &'static str) -> String {
    let candidate = error.split([':', '|']).next().unwrap_or_default().trim();
    if !candidate.is_empty()
        && candidate.len() <= 64
        && candidate
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        candidate.to_string()
    } else {
        fallback.to_string()
    }
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

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PortraitScalePreview {
    application: Option<LayoutApplication>,
    deferred_native: bool,
    deferred_hit_regions: bool,
    precommit_on_first_frame: bool,
    snapshot_required: bool,
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
    fn begin_context_menu(
        &mut self,
        base_application: LayoutApplication,
        base_hit_regions: window_interaction::PhysicalHitRegions,
    ) -> bool {
        if self.context_menu_open {
            return false;
        }
        self.context_menu_base_application = Some(base_application);
        self.context_menu_base_hit_regions = Some(base_hit_regions);
        self.context_menu_rect = None;
        self.context_menu_hit_regions = None;
        self.context_menu_open = true;
        true
    }

    fn require_context_menu_closed(&self) -> Result<(), String> {
        if self.context_menu_open {
            return Err("PET_CONTEXT_MENU_OPEN".to_string());
        }
        Ok(())
    }

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
        self.anchor_user_positioned = true;
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

    fn defers_precise_surface_hit_regions(&self) -> bool {
        self.defers_precise_portrait_scale_hit_regions()
            || (cfg!(windows) && self.control_surface_preview_active)
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
    input_transition_prepared: bool,
    bubble_transition_prepared: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PetSurfaceDiagnostics {
    revision: u64,
    logical_bounds: [u32; 4],
    scale_reference_size: [u32; 2],
    visible_fit_bounds: [u32; 4],
    resident_backing_bounds: [u32; 4],
    physical_window: window_geometry::PhysicalPlacement,
    physical_work_area: PhysicalRect,
    global_anchor: window_geometry::PhysicalPoint,
    physical_local_anchor: [u32; 2],
    dpi_scale: f64,
    content_scale: f64,
    anchor_policy: &'static str,
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

fn is_animated_input_resize(
    previous: &ControlSurfaceLayout,
    target: &ControlSurfaceLayout,
    transition: Option<InputSurfaceTransition>,
) -> bool {
    transition.is_some_and(|transition| transition.duration_ms > 0)
        && previous.bubble_rect == target.bubble_rect
        && previous.input_rect[..3] == target.input_rect[..3]
        && previous.input_rect[3] != target.input_rect[3]
}

fn is_animated_bubble_contraction(
    previous: &ControlSurfaceLayout,
    target: &ControlSurfaceLayout,
    transition: Option<InputSurfaceTransition>,
) -> bool {
    transition.is_some_and(|transition| transition.duration_ms > 0)
        && is_bubble_resize_geometry(previous, target)
        && previous.bubble_rect[3] > target.bubble_rect[3]
}

fn is_bubble_resize_geometry(
    previous: &ControlSurfaceLayout,
    target: &ControlSurfaceLayout,
) -> bool {
    previous.input_rect == target.input_rect
        && previous.bubble_rect[0] == target.bubble_rect[0]
        && previous.bubble_rect[2] == target.bubble_rect[2]
        && previous.bubble_rect[1].saturating_add(previous.bubble_rect[3])
            == target.bubble_rect[1].saturating_add(target.bubble_rect[3])
        && previous.bubble_rect[3] != target.bubble_rect[3]
        && previous.controls_rect[0] == target.controls_rect[0]
        && previous.controls_rect[2..] == target.controls_rect[2..]
        && i64::from(target.controls_rect[1]) - i64::from(previous.controls_rect[1])
            == i64::from(target.bubble_rect[1]) - i64::from(previous.bubble_rect[1])
}

fn remaining_input_motion_delay_ms(start_at_unix_ms: u64) -> u32 {
    use std::time::{SystemTime, UNIX_EPOCH};

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(start_at_unix_ms);
    start_at_unix_ms.saturating_sub(now).min(200) as u32
}

fn matches_started_input_expansion(
    started: StartedInputExpansion,
    previous: &ControlSurfaceLayout,
    target: &ControlSurfaceLayout,
    transition: Option<InputSurfaceTransition>,
) -> bool {
    previous.input_rect[3] == started.previous_height
        && target.input_rect[3] == started.target_height
        && transition.is_some_and(|transition| {
            transition.duration_ms == started.transition.duration_ms
                && transition.staging_height == started.transition.staging_height
        })
}

#[cfg(windows)]
fn schedule_control_contraction_region_commit(
    window: &WebviewWindow,
    revision: u64,
    duration_ms: u32,
    hit_regions: window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    let delayed_window = window.clone();
    std::thread::Builder::new()
        .name("control-surface-contraction-region".to_string())
        .spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(
                u64::from(duration_ms) + CONTROL_CONTRACTION_REGION_GRACE_MS,
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
                    eprintln!("failed to settle control surface contraction region: {error}");
                }
            }) {
                eprintln!(
                    "failed to schedule control surface contraction region settlement: {error}"
                );
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

fn uses_bubble_expansion_stable_surface_bounds(bubble_auto_expand: bool) -> bool {
    cfg!(windows) && bubble_auto_expand
}

fn preserves_portrait_anchor_for_scale_settlement(
    preview_active: bool,
    gesture_active: bool,
) -> bool {
    preview_active && !gesture_active
}

fn compute_pet_window_layout(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<window_geometry::PhysicalPoint>,
    anchor_policy: AnchorPolicy,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    stabilize_portrait_scale: bool,
    stabilize_bubble_expansion: bool,
) -> Result<LayoutApplication, String> {
    // Win32 SetWindowRgn already provides the exact visible/input shape. Keep the underlying
    // rectangular HWND/WebView envelope stable across every portrait-scale and control-panel
    // setting so neither slider gesture has to resize or reposition the compositor surface.
    let resident_stable_surface = uses_resident_stable_surface_bounds(
        portrait_alpha_mask.is_some(),
        control_surface.is_some(),
    );
    compute_pet_window_layout_with_surface_policy(
        contract,
        state,
        revision,
        monitor,
        existing_anchor,
        anchor_policy,
        portrait_scale_percent,
        control_surface,
        portrait_alpha_mask,
        stabilize_portrait_scale,
        resident_stable_surface,
        stabilize_bubble_expansion,
    )
}

#[allow(clippy::too_many_arguments)]
fn compute_pet_window_layout_with_surface_policy(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    existing_anchor: Option<window_geometry::PhysicalPoint>,
    anchor_policy: AnchorPolicy,
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    stabilize_portrait_scale: bool,
    resident_stable_surface: bool,
    stabilize_bubble_expansion: bool,
) -> Result<LayoutApplication, String> {
    let bounds_started = std::time::Instant::now();
    // On Windows the alpha mask owns only the exact Win32 region and hit testing. Work-area fit
    // must use the complete canonical portrait slot at the largest legal appearance scale;
    // otherwise releasing the scale slider can change contentScale and window placement.
    let visible_fit_portrait_mask = if resident_stable_surface {
        None
    } else {
        portrait_alpha_mask
    };
    let visible_fit_portrait_scale_percent = if resident_stable_surface {
        window_interaction::PORTRAIT_SCALE_MAX_PERCENT
    } else {
        portrait_scale_percent
    };
    let current_visible_bounds =
        window_interaction::logical_visible_surface_bounds_with_control_surface(
            contract,
            state,
            visible_fit_portrait_scale_percent,
            control_surface,
            visible_fit_portrait_mask,
        )?;
    let bubble_expansion_bounds = if stabilize_bubble_expansion {
        Some(
            window_interaction::logical_bubble_expansion_stable_surface_bounds(
                contract,
                state,
                visible_fit_portrait_scale_percent,
                control_surface.ok_or_else(|| "CONTROL_SURFACE_REQUIRED".to_string())?,
                visible_fit_portrait_mask,
            )?,
        )
    } else {
        None
    };
    let backing_base_bounds = if resident_stable_surface {
        // Windows keeps the rectangular HWND/WebView envelope independent of the
        // current expression and layout slider while precise regions control the
        // actual visible and interactive pixels.
        window_interaction::logical_scale_and_control_stable_surface_bounds(
            contract,
            state,
            portrait_scale_percent,
            portrait_alpha_mask,
        )?
    } else if let Some(bounds) = bubble_expansion_bounds {
        bounds
    } else if stabilize_portrait_scale {
        window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
            contract,
            state,
            portrait_scale_percent,
            control_surface,
            portrait_alpha_mask,
        )?
    } else {
        current_visible_bounds
    };
    let visible_fit_base = bubble_expansion_bounds.unwrap_or(current_visible_bounds);
    let visible_fit_bounds = match composer_tool_dock_reserve_rect(contract, control_surface)? {
        Some(dock_reserve) => window_interaction::expand_surface_bounds_for_overlay(
            visible_fit_base,
            dock_reserve,
            composer_resident_viewport(contract),
        )?,
        None => visible_fit_base,
    };
    let [x, y, width, height] = backing_base_bounds;
    let bottom = y.saturating_add(height);
    let reserved_bottom = if resident_stable_surface {
        composer_resident_viewport(contract)[1]
    } else {
        composer_tool_dock_reserved_bottom(contract, control_surface)
    };
    let resident_backing_bounds = if bottom >= reserved_bottom {
        backing_base_bounds
    } else {
        [x, y, width, reserved_bottom - y]
    };
    interaction_latency::stage_elapsed("surface-bounds-compute-return", bounds_started);
    apply_window_layout_with_fit_bounds(
        contract,
        state,
        revision,
        monitor,
        existing_anchor,
        anchor_policy,
        visible_fit_bounds,
        resident_backing_bounds,
    )
}

fn clip_portrait_scale_preview_application_to_work_area(
    contract: &LayoutContract,
    monitor: &MonitorDescriptor,
    current: &LayoutApplication,
    stable: LayoutApplication,
) -> Result<LayoutApplication, String> {
    let expanded_bounds =
        window_interaction::union_surface_bounds(current.active_bounds, stable.active_bounds);
    let preview_bounds = clip_expanded_surface_bounds_to_work_area(
        current,
        expanded_bounds,
        contract.viewport.portrait_anchor,
    )?;
    if preview_bounds == stable.active_bounds {
        return Ok(stable);
    }
    apply_window_layout_with_fit_bounds(
        contract,
        stable.state,
        stable.revision,
        monitor,
        Some(current.portrait_anchor),
        AnchorPolicy::UserPositioned,
        stable.visible_fit_bounds,
        preview_bounds,
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
        .map(|session| session.applied_revision.max(session.revision.latest()))
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
        scale_reference_size: layout_contract()?.viewport.content_scale_size,
        visible_fit_bounds: application.visible_fit_bounds,
        resident_backing_bounds: application.active_bounds,
        physical_window: application.physical_placement,
        physical_work_area: application.work_area,
        global_anchor: application.portrait_anchor,
        physical_local_anchor: application.physical_local_anchor,
        dpi_scale: application.scale_factor,
        content_scale: application.content_scale,
        anchor_policy: if geometry.anchor_user_positioned {
            "user-positioned"
        } else {
            "automatic"
        },
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
    bubble_transition: Option<window_geometry::InputSurfaceTransition>,
    bubble_auto_expand: bool,
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
        let bubble_transition = bubble_transition
            .map(|value| value.validate())
            .transpose()?;
        let mut session = interaction_latency::lock(
            session.inner(),
            "geometry-mutex-wait-start",
            "geometry-mutex-acquired",
        )?;

        if !session.revision.accept(revision) {
            return Ok(PetLayoutApplication {
                layout: LayoutApplication::rejected(revision, state, contract.schema_version),
                hit_regions: None,
                input_transition_prepared: false,
                bubble_transition_prepared: false,
            });
        }

        let anchor_user_positioned =
            session.anchor_user_positioned || session.is_deferred_drag_pending();
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
            if anchor_user_positioned {
                AnchorPolicy::UserPositioned
            } else {
                AnchorPolicy::Automatic
            },
            session.portrait_scale_percent,
            control_surface.as_ref(),
            session.portrait_alpha_mask.as_ref(),
            false,
            uses_bubble_expansion_stable_surface_bounds(bubble_auto_expand),
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
            if let Some(staging_height) = input_transition
                .and_then(|transition| transition.staging_height)
                .filter(|_| input_transition.is_some_and(|transition| transition.duration_ms > 0))
            {
                let minimum = previous.input_rect[3].min(target.input_rect[3]);
                let maximum = previous.input_rect[3].max(target.input_rect[3]);
                if target.input_rect[3] <= previous.input_rect[3]
                    || staging_height < minimum
                    || staging_height >= maximum
                {
                    return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
                }
            }
        }
        if bubble_transition.is_some() {
            let previous = previous_control_surface
                .as_ref()
                .ok_or_else(|| "CONTROL_SURFACE_INVALID:bubbleTransition".to_string())?;
            let target = control_surface
                .as_ref()
                .ok_or_else(|| "CONTROL_SURFACE_INVALID:bubbleTransition".to_string())?;
            if !is_bubble_resize_geometry(previous, target)
                || bubble_transition.is_some_and(|transition| transition.staging_height.is_some())
            {
                return Err("CONTROL_SURFACE_INVALID:bubbleTransition".to_string());
            }
        }
        let input_expansion_started =
            session
                .input_expansion_started
                .take()
                .is_some_and(|started| {
                    previous_control_surface.as_ref().is_some_and(|previous| {
                        control_surface.as_ref().is_some_and(|target| {
                            matches_started_input_expansion(
                                started,
                                previous,
                                target,
                                input_transition,
                            )
                        })
                    })
                });
        if session.context_menu_open {
            let menu_surface = match session.context_menu_rect {
                Some(rect) => build_context_menu_surface_geometry(
                    &contract,
                    &application,
                    rect,
                    control_surface.as_ref(),
                    session.portrait_alpha_mask.as_ref(),
                    session.portrait_scale_percent,
                )?,
                None => {
                    // open_pet_context_menu relaxes the Windows region before the WebView has
                    // measured the menu. A concurrent layout frame must update that base instead
                    // of failing the menu-opening transaction or restoring a stale snapshot.
                    let base_hit_regions = build_native_interaction_regions(
                        &contract,
                        &application,
                        control_surface.as_ref(),
                        session.portrait_alpha_mask.as_ref(),
                        session.portrait_scale_percent,
                    )?;
                    ContextMenuSurfaceGeometry {
                        application: application.clone(),
                        expanded_hit_regions: base_hit_regions.clone(),
                        base_hit_regions,
                    }
                }
            };
            let native_application = menu_surface.application;
            let previous_regions = session
                .context_menu_hit_regions
                .clone()
                .or_else(|| session.hit_regions.clone());
            let geometry_changed = previous_application
                .as_ref()
                .is_none_or(|previous| !same_surface_geometry(previous, &native_application));
            if geometry_changed {
                if let Err(error) = apply_native_pet_surface_bounds_transaction(
                    &window,
                    &native_application,
                    previous_application.as_ref(),
                    previous_regions.as_ref(),
                ) {
                    if current_context_menu_region_policy()
                        == ContextMenuRegionPolicy::RelaxedWholeWindow
                    {
                        NativeWindowInteractionBackend
                            .relax_hit_regions(&window)
                            .map_err(|fallback_error| {
                                format!(
                                    "PET_CONTEXT_MENU_LAYOUT_FAILED: {error}; PET_CONTEXT_MENU_RELAX_FALLBACK_FAILED: {fallback_error}"
                                )
                            })?;
                    }
                    return Err(error);
                }
            }
            match current_context_menu_region_policy() {
                ContextMenuRegionPolicy::RelaxedWholeWindow => {
                    NativeWindowInteractionBackend
                        .relax_hit_regions(&window)
                        .map_err(|error| {
                            format!("PET_CONTEXT_MENU_LAYOUT_RELAX_FAILED: {error}")
                        })?;
                }
                ContextMenuRegionPolicy::PreciseOverlay => {
                    if let Err(error) =
                        apply_precise_hit_regions(&window, &menu_surface.expanded_hit_regions)
                    {
                        if geometry_changed {
                            if let Err(rollback_error) = rollback_pet_surface(
                                &window,
                                previous_application.as_ref(),
                                previous_regions.as_ref(),
                            ) {
                                return Err(format!(
                                    "PET_CONTEXT_MENU_LAYOUT_FAILED: {error}; PET_CONTEXT_MENU_ROLLBACK_FAILED: {rollback_error}"
                                ));
                            }
                        }
                        return Err(format!("PET_CONTEXT_MENU_LAYOUT_FAILED: {error}"));
                    }
                }
            }
            if let Some(surface) = control_surface.as_ref() {
                if !input_expansion_started {
                    glass.update_control_surface(
                        &window,
                        surface,
                        &native_application,
                        previous_control_surface.as_ref(),
                        input_transition,
                    )?;
                }
            }
            session.portrait_anchor = Some(native_application.portrait_anchor);
            session.physical_local_anchor = Some(native_application.physical_local_anchor);
            session.active_bounds = Some(native_application.active_bounds);
            session.surface_scale =
                native_application.scale_factor * native_application.content_scale;
            session.application = Some(native_application);
            session.state = Some(state);
            session.applied_revision = revision;
            session.anchor_user_positioned = anchor_user_positioned;
            session.bubble_auto_expand = bubble_auto_expand;
            session.control_surface = control_surface;
            session.context_menu_base_application = Some(application.clone());
            session.context_menu_base_hit_regions = Some(menu_surface.base_hit_regions);
            session.hit_regions = Some(menu_surface.expanded_hit_regions.clone());
            session.context_menu_hit_regions = Some(menu_surface.expanded_hit_regions.clone());
            session.input_surface_transition_pending = None;
            session.bubble_surface_transition_pending = None;
            return Ok(PetLayoutApplication {
                layout: application,
                hit_regions: Some(menu_surface.expanded_hit_regions),
                input_transition_prepared: false,
                bubble_transition_prepared: false,
            });
        }
        let previous_regions = session.hit_regions.clone();
        let defer_precise_control_regions = cfg!(windows) && session.control_surface_preview_active;
        let prepare_input_transition = !defer_precise_control_regions
            && previous_application
                .as_ref()
                .is_some_and(|previous| same_surface_geometry(previous, &application))
            && previous_control_surface.as_ref().is_some_and(|previous| {
                control_surface.as_ref().is_some_and(|target| {
                    is_animated_input_contraction(previous, target, input_transition)
                })
            });
        let defer_input_contraction = cfg!(windows)
            && prepare_input_transition
            && previous_regions.is_some()
            && previous_control_surface.as_ref().is_some_and(|previous| {
                control_surface.as_ref().is_some_and(|target| {
                    is_animated_input_contraction(previous, target, input_transition)
                })
            });
        let prepare_bubble_transition = cfg!(windows)
            && !defer_precise_control_regions
            && previous_application
                .as_ref()
                .is_some_and(|previous| same_surface_geometry(previous, &application))
            && previous_control_surface.as_ref().is_some_and(|previous| {
                control_surface.as_ref().is_some_and(|target| {
                    is_animated_bubble_contraction(previous, target, bubble_transition)
                })
            });
        let defer_control_contraction = defer_input_contraction || prepare_bubble_transition;
        let hit_regions = if defer_control_contraction {
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
                input_expansion_started,
            )?
        };
        if session.is_deferred_drag_pending() {
            session.finish_deferred_drag();
        }
        if let Some(surface) = control_surface.as_ref() {
            if input_expansion_started {
                // The lightweight input command already opened the native clip and started the
                // glass animation. This full transaction only commits the final precise region.
            } else if prepare_input_transition {
                let previous = previous_control_surface
                    .as_ref()
                    .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?;
                // Contraction keeps the old native clip until the WebView has accepted the final
                // geometry. Expansion normally starts through the lightweight command; this
                // branch remains the acknowledgement-gated contraction path.
                glass.update_control_surface(&window, previous, &application, None, None)?;
            } else {
                glass.update_control_surface(
                    &window,
                    surface,
                    &application,
                    previous_control_surface.as_ref(),
                    input_transition,
                )?;
            }
        }
        session.portrait_anchor = Some(application.portrait_anchor);
        session.physical_local_anchor = Some(application.physical_local_anchor);
        session.active_bounds = Some(application.active_bounds);
        session.surface_scale = application.scale_factor * application.content_scale;
        session.application = Some(application.clone());
        session.state = Some(state);
        session.applied_revision = revision;
        session.anchor_user_positioned = anchor_user_positioned;
        session.bubble_auto_expand = bubble_auto_expand;
        session.control_surface = control_surface;
        session.hit_regions = if defer_control_contraction {
            previous_regions
        } else {
            Some(hit_regions.clone())
        };
        session.input_surface_transition_pending = if prepare_input_transition {
            Some(PendingInputSurfaceTransition {
                revision,
                previous_surface: previous_control_surface
                    .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?,
                target_surface: session
                    .control_surface
                    .clone()
                    .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?,
                application: application.clone(),
                transition: input_transition
                    .ok_or_else(|| "CONTROL_SURFACE_INVALID:inputTransition".to_string())?,
                contraction_hit_regions: defer_input_contraction.then(|| hit_regions.clone()),
            })
        } else {
            None
        };
        session.bubble_surface_transition_pending = if prepare_bubble_transition {
            Some(PendingBubbleSurfaceTransition {
                revision,
                transition: bubble_transition
                    .ok_or_else(|| "CONTROL_SURFACE_INVALID:bubbleTransition".to_string())?,
                contraction_hit_regions: hit_regions.clone(),
            })
        } else {
            None
        };
        let result = PetLayoutApplication {
            layout: application,
            hit_regions: Some(hit_regions.clone()),
            input_transition_prepared: prepare_input_transition,
            bubble_transition_prepared: prepare_bubble_transition,
        };
        drop(session);
        Ok(result)
    })
}

#[tauri::command]
fn start_pet_input_expansion(
    window: WebviewWindow,
    target_height: u32,
    staging_height: u32,
    duration_ms: u32,
    start_at_unix_ms: u64,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
    glass: tauri::State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<bool, String> {
    let contract = layout_contract()?;
    let transition = InputSurfaceTransition {
        duration_ms,
        staging_height: Some(staging_height),
        delay_ms: remaining_input_motion_delay_ms(start_at_unix_ms),
    }
    .validate()?;
    let mut session = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    let state = session
        .state
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let application = session
        .application
        .clone()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let previous = session
        .control_surface
        .clone()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let mut target = previous.clone();
    target.input_rect[3] = target_height;
    contract.validate_control_surface(state, &target)?;
    if !is_animated_input_resize(&previous, &target, Some(transition))
        || target_height <= previous.input_rect[3]
        || staging_height < previous.input_rect[3]
        || staging_height >= target_height
    {
        return Err("CONTROL_SURFACE_INVALID:inputTransition".to_string());
    }
    #[cfg(windows)]
    window_interaction::relax_native_hit_regions(&window).map_err(|error| {
        eprintln!("[pet-input-expansion] relax-native-hit-regions failed: {error}");
        error
    })?;
    if let Err(error) = glass.update_control_surface(
        &window,
        &target,
        &application,
        Some(&previous),
        Some(transition),
    ) {
        #[cfg(windows)]
        if let Some(regions) = session.hit_regions.as_ref() {
            let _ = apply_precise_hit_regions_with_synchronous_redraw(&window, regions);
        }
        return Err(error);
    }
    session.input_expansion_started = Some(StartedInputExpansion {
        previous_height: previous.input_rect[3],
        target_height,
        transition,
    });
    Ok(true)
}

#[tauri::command]
fn start_pet_input_transition(
    window: WebviewWindow,
    revision: u64,
    start_at_unix_ms: u64,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
    glass: tauri::State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<bool, String> {
    let pending = {
        let mut session = session
            .lock()
            .map_err(|_| "window geometry state is unavailable".to_string())?;
        if session.applied_revision != revision
            || session
                .input_surface_transition_pending
                .as_ref()
                .is_none_or(|pending| pending.revision != revision)
        {
            return Ok(false);
        }
        session.input_surface_transition_pending.take()
    };
    let Some(mut pending) = pending else {
        return Ok(false);
    };
    pending.transition.delay_ms = remaining_input_motion_delay_ms(start_at_unix_ms);
    glass.update_control_surface(
        &window,
        &pending.target_surface,
        &pending.application,
        Some(&pending.previous_surface),
        Some(pending.transition),
    )?;
    #[cfg(windows)]
    if let Some(hit_regions) = pending.contraction_hit_regions {
        if let Err(error) = schedule_control_contraction_region_commit(
            &window,
            revision,
            pending
                .transition
                .duration_ms
                .saturating_add(pending.transition.delay_ms),
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
    let _ = pending.contraction_hit_regions;
    Ok(true)
}

#[tauri::command]
fn start_pet_bubble_transition(
    window: WebviewWindow,
    revision: u64,
    start_at_unix_ms: u64,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<bool, String> {
    let pending = {
        let mut session = session
            .lock()
            .map_err(|_| "window geometry state is unavailable".to_string())?;
        if session.applied_revision != revision
            || session
                .bubble_surface_transition_pending
                .as_ref()
                .is_none_or(|pending| pending.revision != revision)
        {
            return Ok(false);
        }
        session.bubble_surface_transition_pending.take()
    };
    let Some(pending) = pending else {
        return Ok(false);
    };
    #[cfg(windows)]
    {
        let delay_ms = remaining_input_motion_delay_ms(start_at_unix_ms);
        if let Err(error) = schedule_control_contraction_region_commit(
            &window,
            revision,
            pending.transition.duration_ms.saturating_add(delay_ms),
            pending.contraction_hit_regions.clone(),
        ) {
            eprintln!("{error}; applying final bubble region immediately");
            apply_precise_hit_regions(&window, &pending.contraction_hit_regions)?;
            let state = window.state::<Mutex<WindowGeometrySession>>();
            if let Ok(mut geometry) = state.lock() {
                if geometry.applied_revision == revision {
                    geometry.hit_regions = Some(pending.contraction_hit_regions);
                }
            };
        }
    }
    #[cfg(not(windows))]
    let _ = (window, start_at_unix_ms, pending);
    Ok(true)
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
    apply_precise_hit_regions_with_synchronous_redraw(window, &physical)?;
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

#[cfg(any(windows, test))]
fn build_coarse_native_interaction_regions(
    contract: &LayoutContract,
    application: &LayoutApplication,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let precise = build_native_interaction_regions(
        contract,
        application,
        control_surface,
        portrait_alpha_mask,
        portrait_scale_percent,
    )?;
    Ok(window_interaction::coarse_preview_hit_regions(&precise))
}

struct ContextMenuSurfaceGeometry {
    application: LayoutApplication,
    base_hit_regions: window_interaction::PhysicalHitRegions,
    expanded_hit_regions: window_interaction::PhysicalHitRegions,
}

fn build_context_menu_surface_geometry(
    contract: &LayoutContract,
    base_application: &LayoutApplication,
    rect: [u32; 4],
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    portrait_scale_percent: u16,
) -> Result<ContextMenuSurfaceGeometry, String> {
    let [x, y, width, height] = rect;
    let expanded_bounds = window_interaction::expand_surface_bounds_for_overlay(
        base_application.active_bounds,
        rect,
        composer_resident_viewport(contract),
    )
    .map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID".to_string())?;
    let application = window_geometry::expand_application_preserving_anchor(
        base_application,
        expanded_bounds,
        contract.viewport.portrait_anchor,
    )?;
    let base_hit_regions = build_native_interaction_regions(
        contract,
        base_application,
        control_surface,
        portrait_alpha_mask,
        portrait_scale_percent,
    )?;
    let mut expanded_hit_regions = build_native_interaction_regions(
        contract,
        &application,
        control_surface,
        portrait_alpha_mask,
        portrait_scale_percent,
    )?;
    let logical_menu = window_interaction::LogicalHitRegions {
        state: expanded_hit_regions.state,
        interactive: vec![window_interaction::LogicalHitRect::checked(
            i32::try_from(x).map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID")?,
            i32::try_from(y).map_err(|_| "PET_CONTEXT_MENU_RECT_INVALID")?,
            width,
            height,
            composer_resident_viewport(contract),
        )?],
        drag: Vec::new(),
        neutral: Vec::new(),
    };
    let mut menu_hit_regions = window_interaction::scale_hit_regions_for_surface(
        &logical_menu,
        application.scale_factor * application.content_scale,
        application.active_bounds,
        contract.viewport.portrait_anchor,
    )?;
    expanded_hit_regions
        .interactive
        .append(&mut menu_hit_regions.interactive);
    Ok(ContextMenuSurfaceGeometry {
        application,
        base_hit_regions,
        expanded_hit_regions,
    })
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

fn apply_precise_hit_regions_with_synchronous_redraw(
    window: &WebviewWindow,
    physical: &window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    #[cfg(windows)]
    return window_interaction::apply_native_hit_regions_with_synchronous_redraw(window, physical)
        .map_err(|error| {
            format!("failed to apply native hit regions; previous region retained: {error}")
        });

    #[cfg(not(windows))]
    apply_precise_hit_regions(window, physical)
}

fn reapply_current_pet_hit_region(window: &WebviewWindow) -> Result<(), String> {
    let session = window.state::<Mutex<WindowGeometrySession>>();
    let geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if geometry.context_menu_open
        && current_context_menu_region_policy() == ContextMenuRegionPolicy::RelaxedWholeWindow
    {
        drop(geometry);
        return NativeWindowInteractionBackend
            .relax_hit_regions(window)
            .map_err(|error| format!("failed to preserve relaxed context-menu region: {error}"));
    }
    let hit_regions = geometry
        .context_menu_hit_regions
        .as_ref()
        .or(geometry.hit_regions.as_ref())
        .cloned()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    drop(geometry);

    apply_precise_hit_regions_with_synchronous_redraw(window, &hit_regions)
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

fn same_drag_visual_effect_geometry(
    previous: &LayoutApplication,
    next: &LayoutApplication,
) -> bool {
    same_local_surface_geometry(previous, next)
        && previous.work_area == next.work_area
        && previous.monitor_name.as_deref() == next.monitor_name.as_deref()
}

fn can_reuse_resident_portrait_application(
    resident_stable_surface: bool,
    application: &LayoutApplication,
    state: PresentationState,
    applied_revision: u64,
    monitor: &MonitorDescriptor,
) -> bool {
    resident_stable_surface
        && application.applied
        && application.revision == applied_revision
        && application.state == state
        && application.scale_factor == monitor.scale_factor
        && application.work_area == monitor.work_area
        && application.monitor_name.as_deref() == monitor.name.as_deref()
}

fn sync_context_menu_input_glass(
    window: &WebviewWindow,
    control_surface: Option<&ControlSurfaceLayout>,
    application: &LayoutApplication,
) -> Result<(), String> {
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (window, control_surface, application);
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    let Some(control_surface) = control_surface
    else {
        return Ok(());
    };
    // The menu changes the native window envelope without changing the canonical input rect.
    // Re-resolve that rect in the new AppKit/WebView coordinate space before the menu becomes
    // visible; otherwise the native glass remains at its pre-menu local position.
    #[cfg(target_os = "macos")]
    return window
        .state::<input_visual_effect::InputVisualEffectState>()
        .update_control_surface(window, control_surface, application, None, None);
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
        if previous_region_relaxed {
            apply_precise_hit_regions_with_synchronous_redraw(window, &next_regions)?;
        } else {
            apply_precise_hit_regions(window, &next_regions)?;
        }
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

fn commit_bootstrap_geometry(
    session: &mut WindowGeometrySession,
    application: LayoutApplication,
    hit_regions: window_interaction::PhysicalHitRegions,
) -> Result<(), String> {
    if !application.applied || application.revision != 0 {
        return Err("PET_BOOTSTRAP_LAYOUT_INVALID".to_string());
    }
    session.portrait_anchor = Some(application.portrait_anchor);
    session.physical_local_anchor = Some(application.physical_local_anchor);
    session.active_bounds = Some(application.active_bounds);
    session.surface_scale = application.scale_factor * application.content_scale;
    session.application = Some(application);
    session.state = Some(PresentationState::Product);
    session.applied_revision = 0;
    session.anchor_user_positioned = false;
    session.control_surface = None;
    session.hit_regions = Some(hit_regions);
    Ok(())
}

fn prepare_initial_pet_window(window: &WebviewWindow) -> Result<(), String> {
    let contract = layout_contract()?;
    let monitor = target_monitor(window, None)?;
    // Revision zero is a recoverable bootstrap. It is published to the session without
    // advancing the revision guard, so the WebView still owns the first normal revision.
    let application = compute_pet_window_layout(
        &contract,
        PresentationState::Product,
        0,
        &monitor,
        None,
        AnchorPolicy::Automatic,
        100,
        None,
        None,
        false,
        false,
    )?;
    let hit_regions = apply_native_pet_surface(window, &contract, &application, None, None, 100)?;
    let state = window.state::<Mutex<WindowGeometrySession>>();
    let mut session = state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    commit_bootstrap_geometry(&mut session, application, hit_regions)
}

#[tauri::command]
fn reveal_pet_window(
    window: WebviewWindow,
    session: State<'_, Mutex<WindowGeometrySession>>,
    lifecycle: State<'_, ShellLifecycleState>,
    first_run_guide: State<'_, product_shell::FirstRunGuideState>,
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
    let first_run_completed = first_run_guide.snapshot()?.completed;
    let session_ready = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "LIFECYCLE_COMMAND_UNAVAILABLE".to_string())?
        .character_presentation()
        .map_err(str::to_string)?
        .is_some();
    if !first_run_completed {
        window.hide().map_err(|error| error.to_string())?;
        product_shell::sync_product_tray_visibility(window.app_handle(), false)?;
        // Setup owns the one first-run settings dispatch. Dispatching again
        // from the hidden pet WebView races a user closing onboarding and can
        // queue an unwanted replacement window.
        return Ok(());
    }
    if !session_ready {
        window.hide().map_err(|error| error.to_string())?;
        product_shell::sync_product_tray_visibility(window.app_handle(), false)?;
        // Windows runs synchronous Tauri commands inside WebView2's
        // WebMessageReceived callback. Building another WebView before this
        // invoke returns can deadlock the callback, so reuse the deferred
        // product-action dispatcher used by the pet context menu.
        dispatch_webview_product_menu_action(
            window.app_handle().clone(),
            product_shell::ProductMenuAction::OpenSettings,
        )?;
        return Ok(());
    }
    window
        .show()
        .map_err(|error| format!("failed to reveal pet window: {error}"))?;
    reapply_current_pet_hit_region(&window)?;
    product_shell::sync_product_tray_visibility(window.app_handle(), true)
}

fn compute_dragged_pet_window_layout(
    contract: &LayoutContract,
    state: PresentationState,
    revision: u64,
    monitor: &MonitorDescriptor,
    position: window_geometry::PhysicalPoint,
    previous_local_anchor: [u32; 2],
    portrait_scale_percent: u16,
    control_surface: Option<&ControlSurfaceLayout>,
    portrait_alpha_mask: Option<&character_presentation::PortraitAlphaMask>,
    stabilize_bubble_expansion: bool,
) -> Result<LayoutApplication, String> {
    let provisional_anchor =
        window_geometry::anchor_from_window_position(position, previous_local_anchor)?;
    let provisional_application = compute_pet_window_layout(
        contract,
        state,
        revision,
        monitor,
        Some(provisional_anchor),
        AnchorPolicy::UserPositioned,
        portrait_scale_percent,
        control_surface,
        portrait_alpha_mask,
        false,
        stabilize_bubble_expansion,
    )?;
    // The custom Windows drag loop follows the physical top-left. After WM_DPICHANGED the local
    // portrait anchor has a different physical offset, so deriving the final anchor from the old
    // offset makes pointer-up shift the whole surface by exactly that DPI delta. Resolve the
    // target-DPI offset first, then make the final application preserve the HWND position the
    // user actually released.
    let requested_anchor = window_geometry::anchor_from_window_position(
        position,
        provisional_application.physical_local_anchor,
    )?;
    let application = if requested_anchor == provisional_application.portrait_anchor {
        provisional_application
    } else {
        compute_pet_window_layout(
            contract,
            state,
            revision,
            monitor,
            Some(requested_anchor),
            AnchorPolicy::UserPositioned,
            portrait_scale_percent,
            control_surface,
            portrait_alpha_mask,
            false,
            stabilize_bubble_expansion,
        )?
    };
    Ok(application)
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
    let application = compute_dragged_pet_window_layout(
        &contract,
        state,
        session.applied_revision,
        &monitor,
        position,
        session
            .physical_local_anchor
            .ok_or_else(|| "pet surface local anchor is unavailable".to_string())?,
        session.portrait_scale_percent,
        session.control_surface.as_ref(),
        session.portrait_alpha_mask.as_ref(),
        uses_bubble_expansion_stable_surface_bounds(session.bubble_auto_expand),
    )?;
    let previous_application = session.application.clone();
    let previous_regions = session.hit_regions.clone();
    let refresh_input_visual_effect = previous_application
        .as_ref()
        .is_none_or(|previous| !same_drag_visual_effect_geometry(previous, &application));
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
    if refresh_input_visual_effect {
        if let Some(surface) = session.control_surface.as_ref() {
            window
                .state::<input_visual_effect::InputVisualEffectState>()
                .update_control_surface(&window, surface, &application, None, None)?;
        }
    }
    session.portrait_anchor = Some(application.portrait_anchor);
    session.physical_local_anchor = Some(application.physical_local_anchor);
    session.active_bounds = Some(application.active_bounds);
    session.surface_scale = application.scale_factor * application.content_scale;
    session.application = Some(application.clone());
    session.hit_regions = Some(hit_regions.clone());
    session.anchor_user_positioned = true;
    Ok(PetLayoutApplication {
        layout: application,
        hit_regions: Some(hit_regions),
        input_transition_prepared: false,
        bubble_transition_prepared: false,
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
        #[cfg(windows)]
        let precise_hit_regions;
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
            #[cfg(windows)]
            {
                precise_hit_regions = session
                    .hit_regions
                    .clone()
                    .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
            }
            interaction_latency::stage("drag-authorization-return");
            if expects_deferred_completion {
                session.begin_deferred_drag();
            }
        }

        #[cfg(windows)]
        let drag_hit_region_guard = {
            let started = std::time::Instant::now();
            interaction_latency::stage("drag-hit-region-coarsen-start");
            let guard = window_interaction::use_coarse_native_hit_region_while_dragging(
                window,
                &precise_hit_regions,
            )?;
            interaction_latency::stage_elapsed("drag-hit-region-coarsen-return", started);
            guard
        };
        let native_drag_started = std::time::Instant::now();
        interaction_latency::stage("native-drag-call-start");
        let completion_result = NativeWindowInteractionBackend.start_drag(&window);
        #[cfg(windows)]
        let restore_result = if let Some(guard) = drag_hit_region_guard {
            let started = std::time::Instant::now();
            interaction_latency::stage("drag-hit-region-restore-start");
            let result = guard.restore(window);
            interaction_latency::stage_elapsed("drag-hit-region-restore-return", started);
            result
        } else {
            Ok(())
        };
        let completion = match completion_result {
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
                #[cfg(windows)]
                if let Err(restore_error) = restore_result {
                    return Err(format!("{error}; {restore_error}"));
                }
                return Err(error.to_string());
            }
        };
        #[cfg(windows)]
        restore_result?;
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
    topmost: tauri::State<'_, product_shell::PetTopmostState>,
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
    let manifest = product_shell::product_menu_capability_manifest(
        subtitle.get()?.is_chinese(),
        topmost.enabled()?,
    );
    // Repositioning an already-open menu must keep the first frame as the close target. Replacing
    // these snapshots with the expanded frame would make Escape permanently retain the menu size.
    if geometry.context_menu_open {
        return Ok(manifest);
    }
    let base_application = geometry
        .application
        .clone()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let base_hit_regions = geometry
        .hit_regions
        .clone()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    // Windows must not rebuild the complex PNG alpha region while the HWND is being enlarged
    // for the WebView menu. Keeping the region relaxed across the whole menu transaction avoids
    // exposing an alpha mask whose surface-local coordinates belong to the previous HWND size.
    if current_context_menu_region_policy() == ContextMenuRegionPolicy::RelaxedWholeWindow {
        NativeWindowInteractionBackend
            .relax_hit_regions(&window)
            .map_err(|error| format!("PET_CONTEXT_MENU_RELAX_FAILED: {error}"))?;
    }
    // Capture the committed surface before the WebView grows the menu. This snapshot is the
    // exact frame to restore on close; it must never be reconstructed through the default
    // work-area placement policy.
    let started = geometry.begin_context_menu(base_application, base_hit_regions);
    debug_assert!(started);
    Ok(manifest)
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
    let surface = build_context_menu_surface_geometry(
        &contract,
        &base_application,
        rect,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        geometry.portrait_scale_percent,
    )?;
    let application = surface.application;
    let expanded_base = surface.expanded_hit_regions;
    let previous_application = geometry.application.clone();
    let previous_regions = geometry
        .context_menu_hit_regions
        .clone()
        .or_else(|| geometry.hit_regions.clone());
    let geometry_changed = previous_application
        .as_ref()
        .is_none_or(|previous| !same_surface_geometry(previous, &application));
    if geometry_changed {
        if let Err(error) = apply_native_pet_surface_bounds_transaction_preserving_top_left(
            &window,
            &application,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        ) {
            // The bounds helper restores the previous precise region on rollback. Windows menu
            // sessions deliberately stay relaxed, including the failed-resize path.
            if current_context_menu_region_policy() == ContextMenuRegionPolicy::RelaxedWholeWindow {
                NativeWindowInteractionBackend
                    .relax_hit_regions(&window)
                    .map_err(|fallback_error| {
                        format!(
                            "PET_CONTEXT_MENU_SURFACE_FAILED: {error}; PET_CONTEXT_MENU_RELAX_FALLBACK_FAILED: {fallback_error}"
                        )
                    })?;
            }
            return Err(error);
        }
    }
    if current_context_menu_region_policy() == ContextMenuRegionPolicy::PreciseOverlay {
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
    }
    sync_context_menu_input_glass(&window, geometry.control_surface.as_ref(), &application)?;
    if geometry.context_menu_base_application.is_none() {
        geometry.context_menu_base_application = Some(base_application);
        geometry.context_menu_base_hit_regions = Some(base_hit_regions);
    }
    geometry.context_menu_rect = Some(rect);
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
        geometry.context_menu_rect = None;
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
        if let Err(error) = apply_native_pet_surface_bounds_transaction_preserving_top_left(
            window,
            &base_application,
            previous_application.as_ref(),
            previous_regions.as_ref(),
        ) {
            #[cfg(windows)]
            {
                let fallback = NativeWindowInteractionBackend
                    .relax_hit_regions(window)
                    .map_err(|fallback_error| {
                        format!(
                            "PET_CONTEXT_MENU_CLOSE_FAILED: {error}; PET_CONTEXT_MENU_RELAX_FALLBACK_FAILED: {fallback_error}"
                        )
                    });
                geometry.context_menu_open = false;
                geometry.context_menu_rect = None;
                geometry.context_menu_hit_regions = None;
                geometry.context_menu_base_application = None;
                geometry.context_menu_base_hit_regions = None;
                fallback?;
                return Err(format!(
                    "PET_CONTEXT_MENU_CLOSE_FAILED_SAFE_FALLBACK_RELAXED: {error}"
                ));
            }
            #[cfg(not(windows))]
            return Err(error);
        }
    }
    if let Err(error) = apply_precise_hit_regions_with_synchronous_redraw(window, &base_hit_regions)
    {
        #[cfg(windows)]
        {
            // SetWindowRgn may fail before or after taking ownership of the new region. Explicitly
            // remove either result so the safe fallback is always a fully interactive window.
            let fallback = NativeWindowInteractionBackend
                .relax_hit_regions(window)
                .map_err(|fallback_error| {
                    format!(
                        "PET_CONTEXT_MENU_CLOSE_FAILED: {error}; PET_CONTEXT_MENU_RELAX_FALLBACK_FAILED: {fallback_error}"
                    )
                });
            geometry.context_menu_open = false;
            geometry.context_menu_rect = None;
            geometry.context_menu_hit_regions = None;
            geometry.context_menu_base_application = None;
            geometry.context_menu_base_hit_regions = None;
            geometry.portrait_anchor = Some(base_application.portrait_anchor);
            geometry.physical_local_anchor = Some(base_application.physical_local_anchor);
            geometry.active_bounds = Some(base_application.active_bounds);
            geometry.surface_scale = base_application.scale_factor * base_application.content_scale;
            geometry.application = Some(base_application);
            geometry.hit_regions = Some(base_hit_regions);
            fallback?;
            return Err(format!(
                "PET_CONTEXT_MENU_CLOSE_FAILED_SAFE_FALLBACK_RELAXED: {error}"
            ));
        }
        #[cfg(not(windows))]
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
        #[cfg(not(windows))]
        return Err(format!("PET_CONTEXT_MENU_CLOSE_FAILED: {error}"));
    }
    sync_context_menu_input_glass(window, geometry.control_surface.as_ref(), &base_application)?;
    geometry.context_menu_open = false;
    geometry.context_menu_rect = None;
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

const COMPOSER_TOOL_DOCK_WIDTH: u32 = 216;
const COMPOSER_TOOL_DOCK_MAX_HEIGHT: u32 = 104;
const COMPOSER_TOOL_DOCK_CORNER_RADIUS: u32 = 16;
const COMPOSER_TOOL_DOCK_RESERVE_HEIGHT: u32 = COMPOSER_TOOL_DOCK_MAX_HEIGHT + 12;

fn composer_resident_viewport(contract: &LayoutContract) -> [u32; 2] {
    [
        contract.viewport.window_size[0],
        contract.viewport.window_size[1].saturating_add(COMPOSER_TOOL_DOCK_RESERVE_HEIGHT),
    ]
}

fn composer_tool_dock_reserved_bottom(
    contract: &LayoutContract,
    control_surface: Option<&ControlSurfaceLayout>,
) -> u32 {
    if control_surface.is_some_and(|surface| !surface.input_visible) {
        return 0;
    }
    let input_rect = control_surface
        .map(|surface| surface.input_rect)
        .or_else(|| {
            contract
                .states
                .get(PresentationState::Product.key())
                .and_then(|layout| layout.input_rect)
        });
    input_rect
        .map(|rect| {
            rect[1]
                .saturating_add(rect[3])
                .saturating_add(COMPOSER_TOOL_DOCK_RESERVE_HEIGHT)
        })
        .unwrap_or(contract.viewport.window_size[1])
}

fn composer_tool_dock_reserve_rect(
    contract: &LayoutContract,
    control_surface: Option<&ControlSurfaceLayout>,
) -> Result<Option<[u32; 4]>, String> {
    if control_surface.is_some_and(|surface| !surface.input_visible) {
        return Ok(None);
    }
    let input_rect = control_surface
        .map(|surface| surface.input_rect)
        .or_else(|| {
            contract
                .states
                .get(PresentationState::Product.key())
                .and_then(|layout| layout.input_rect)
        })
        .ok_or_else(|| "PET_TOOL_DOCK_GEOMETRY_INVALID".to_string())?;
    Ok(Some([
        input_rect[0],
        input_rect[1]
            .checked_add(input_rect[3])
            .ok_or_else(|| "PET_TOOL_DOCK_GEOMETRY_INVALID".to_string())?,
        COMPOSER_TOOL_DOCK_WIDTH,
        COMPOSER_TOOL_DOCK_RESERVE_HEIGHT,
    ]))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PetSurfaceVisibilityCapabilities {
    bubble_auto_hide: bool,
    input_hover_reveal: bool,
}

#[tauri::command]
fn pet_surface_visibility_capabilities(
    window: WebviewWindow,
) -> Result<PetSurfaceVisibilityCapabilities, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    Ok(PetSurfaceVisibilityCapabilities {
        bubble_auto_hide: cfg!(windows),
        input_hover_reveal: cfg!(windows),
    })
}

#[tauri::command]
fn set_pet_input_surface_presented(
    window: WebviewWindow,
    presented: bool,
    duration_ms: u32,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if duration_ms > 1_000 {
        return Err("PET_SURFACE_PRESENTATION_DURATION_INVALID".to_string());
    }
    glass.set_control_surface_presented(&window, presented, duration_ms)
}

fn composer_tool_dock_hit_regions(
    contract: &LayoutContract,
    application: &LayoutApplication,
    base: &window_interaction::PhysicalHitRegions,
    rect: [u32; 4],
) -> Result<window_interaction::PhysicalHitRegions, String> {
    let [x, y, width, height] = rect;
    if width != COMPOSER_TOOL_DOCK_WIDTH || height == 0 || height > COMPOSER_TOOL_DOCK_MAX_HEIGHT {
        return Err("PET_TOOL_DOCK_GEOMETRY_INVALID".to_string());
    }
    let resident_envelope = composer_resident_viewport(contract);
    let mut dock = window_interaction::LogicalHitRect::checked(
        i32::try_from(x).map_err(|_| "PET_TOOL_DOCK_GEOMETRY_INVALID")?,
        i32::try_from(y).map_err(|_| "PET_TOOL_DOCK_GEOMETRY_INVALID")?,
        width,
        height,
        resident_envelope,
    )
    .map_err(|_| "PET_TOOL_DOCK_GEOMETRY_INVALID".to_string())?;
    dock.corner_radius = COMPOSER_TOOL_DOCK_CORNER_RADIUS;
    let logical = window_interaction::LogicalHitRegions {
        state: application.state,
        interactive: vec![dock],
        drag: Vec::new(),
        neutral: Vec::new(),
    };
    let mut physical = window_interaction::scale_hit_regions_for_surface(
        &logical,
        application.scale_factor * application.content_scale,
        application.active_bounds,
        contract.viewport.portrait_anchor,
    )
    .map_err(|_| "PET_TOOL_DOCK_GEOMETRY_INVALID".to_string())?;
    let mut combined = base.clone();
    combined.interactive.append(&mut physical.interactive);
    Ok(combined)
}

#[tauri::command]
fn set_pet_tool_dock_surface(
    window: WebviewWindow,
    rect: Option<[u32; 4]>,
    session: tauri::State<'_, Mutex<WindowGeometrySession>>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let geometry = session
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if rect.is_some() && geometry.context_menu_open {
        return Err("PET_CONTEXT_MENU_OPEN".to_string());
    }
    if geometry.context_menu_open
        && current_context_menu_region_policy() == ContextMenuRegionPolicy::RelaxedWholeWindow
    {
        drop(geometry);
        return NativeWindowInteractionBackend
            .relax_hit_regions(&window)
            .map_err(|error| format!("failed to preserve relaxed context-menu region: {error}"));
    }
    let application = geometry
        .application
        .clone()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let base = geometry
        .context_menu_hit_regions
        .as_ref()
        .or(geometry.hit_regions.as_ref())
        .cloned()
        .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
    drop(geometry);
    let next = match rect {
        Some(rect) => {
            composer_tool_dock_hit_regions(&layout_contract()?, &application, &base, rect)?
        }
        None => base,
    };
    apply_precise_hit_regions(&window, &next)
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
async fn start_screen_capture(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
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
    let theme_primary = resources
        .active_presentation()
        .ok()
        .flatten()
        .and_then(|presentation| appearance.current(&presentation).ok())
        .and_then(|publication| publication.values.theme_tokens.get("primary").cloned())
        .unwrap_or_else(|| "#4b9ac4".to_string());
    let task_generation_id = generation_id.clone();
    let task = tauri::async_runtime::spawn_blocking(move || {
        let monitors = capture::monitor_descriptors()?;
        let monitor_count = monitors.len();
        let (session_id, labels, previous) =
            capture_manager.begin_session(&task_generation_id, &monitors)?;
        capture::close_windows(&app, &previous);
        if let Err(error) =
            capture::show_overlays(&app, &session_id, &labels, &monitors, &theme_primary)
        {
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
        let item_id = payload
            .get("itemId")
            .and_then(Value::as_str)
            .filter(|value| capture::valid_attachment_item_id(value))
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
        let count = payload
            .get("count")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| (1..=6).contains(value))
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        Ok(capture::ScreenAttachmentPublication {
            attachment_id: attachment_id.to_string(),
            item_id: item_id.to_string(),
            width,
            height,
            count,
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
                    "count": publication.count,
                }),
            );
            app.emit_to("main", capture::ATTACHED_EVENT, publication)
                .map_err(|_| "SCREEN_ATTACHMENT_PUBLICATION_FAILED".to_string())?;
            Ok(())
        }
        Err(code) => {
            let (stable_code, public_message) =
                if code.contains("manual screen attachment limit exceeded") {
                    (
                        "SCREEN_ATTACHMENT_LIMIT_EXCEEDED",
                        "每条消息最多附加 6 张截图。",
                    )
                } else {
                    (code.as_str(), "截图失败，请检查系统屏幕录制权限后重试。")
                };
            record_screen_capture(
                &runtime_log,
                &generation_id,
                "screen.capture.failed",
                Severity::Warning,
                json!({"outcome": "failed", "code": stable_code}),
            );
            let _ = app.emit_to(
                "main",
                capture::ERROR_EVENT,
                json!({"message": public_message}),
            );
            Err(public_message.to_string())
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

#[tauri::command]
async fn remove_screen_attachment_item(
    window: WebviewWindow,
    payload: capture::AttachmentItemRemoveRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<capture::ScreenAttachmentItemRemovePublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if !capture::valid_attachment_id(&payload.attachment_id)
        || !capture::valid_attachment_item_id(&payload.item_id)
    {
        return Err("SCREEN_ATTACHMENT_ITEM_ID_INVALID".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let requested_attachment_id = payload.attachment_id;
    let requested_item_id = payload.item_id;
    let request_attachment_id = requested_attachment_id.clone();
    let request_item_id = requested_item_id.clone();
    let response = tauri::async_runtime::spawn_blocking(move || {
        handle.settings_request(
            None,
            "screen.remove",
            json!({
                "attachmentId": request_attachment_id,
                "itemId": request_item_id,
            }),
            std::time::Duration::from_secs(3),
        )
    })
    .await
    .map_err(|_| "SCREEN_ATTACHMENT_REMOVE_ABORTED".to_string())??;
    let response = settings_response_payload(response)?;
    let accepted = response
        .get("accepted")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let response_attachment_id = response
        .get("attachmentId")
        .and_then(Value::as_str)
        .filter(|value| *value == requested_attachment_id)
        .ok_or_else(|| "SCREEN_ATTACHMENT_REMOVE_RESPONSE_INVALID".to_string())?;
    let response_item_id = response
        .get("itemId")
        .and_then(Value::as_str)
        .filter(|value| *value == requested_item_id)
        .ok_or_else(|| "SCREEN_ATTACHMENT_REMOVE_RESPONSE_INVALID".to_string())?;
    let count = response
        .get("count")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .filter(|value| *value <= 6)
        .ok_or_else(|| "SCREEN_ATTACHMENT_REMOVE_RESPONSE_INVALID".to_string())?;
    Ok(capture::ScreenAttachmentItemRemovePublication {
        accepted,
        attachment_id: response_attachment_id.to_string(),
        item_id: response_item_id.to_string(),
        count,
    })
}

#[tauri::command]
async fn capture_screen_awareness_frame(
    window: WebviewWindow,
    payload: capture::ScreenAwarenessCaptureRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<capture::ScreenAwarenessCapturePublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SCREEN_CAPTURE_CORE_NOT_READY".to_string())?;
    let cursor = window
        .app_handle()
        .cursor_position()
        .map_err(|_| "SCREEN_CAPTURE_CURSOR_UNAVAILABLE".to_string())?;
    let manager = captures.inner().clone();
    let task_generation_id = generation_id.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        manager.capture_screen_awareness_frame(
            &task_generation_id,
            cursor.x.round() as i32,
            cursor.y.round() as i32,
            &payload.resolution,
            payload.batch_limit,
        )
    })
    .await
    .map_err(|_| "SCREEN_CAPTURE_TASK_ABORTED".to_string())??;
    record_screen_capture(
        &lifecycle.runtime_log,
        &generation_id,
        "screen.awareness.frame.captured",
        Severity::Info,
        json!({
            "outcome": "completed",
            "batch_count": result.count,
            "dropped_count": result.dropped_count,
        }),
    );
    Ok(result)
}

#[tauri::command]
async fn attach_screen_awareness_batch(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<capture::ScreenAwarenessAttachmentPublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SCREEN_CAPTURE_CORE_NOT_READY".to_string())?;
    let manager = captures.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let descriptors = manager.materialize_screen_awareness_batch(&generation_id)?;
        let count = descriptors.len();
        let response = handle.settings_request(
            None,
            "screen.attachBatch",
            json!({"resources": descriptors}),
            std::time::Duration::from_secs(15),
        );
        manager.release_descriptors(&descriptors, &generation_id);
        let payload = settings_response_payload(response?)?;
        let attachment_id = payload
            .get("attachmentId")
            .and_then(Value::as_str)
            .filter(|value| capture::valid_attachment_id(value))
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        let attached_count = payload
            .get("count")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| *value == count)
            .ok_or_else(|| "SCREEN_ATTACHMENT_RESPONSE_INVALID".to_string())?;
        Ok(capture::ScreenAwarenessAttachmentPublication {
            attachment_id: attachment_id.to_string(),
            count: attached_count,
        })
    })
    .await
    .map_err(|_| "SCREEN_ATTACHMENT_TASK_ABORTED".to_string())?
}

#[tauri::command]
fn clear_screen_awareness_batch(
    window: WebviewWindow,
    captures: State<'_, Arc<capture::CaptureManager>>,
) -> Result<usize, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    Ok(captures.clear_screen_awareness_batch())
}

fn valid_composer_tool_segment(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
}

fn valid_composer_tool_id(value: &str) -> bool {
    value.split_once(':').is_some_and(|(plugin_id, tool_id)| {
        valid_composer_tool_segment(plugin_id) && valid_composer_tool_segment(tool_id)
    })
}

fn validate_composer_tools_snapshot(value: &Value, generation_id: &str) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "COMPOSER_TOOLS_RESPONSE_INVALID".to_string())?;
    if object.len() != 3
        || object.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || object.get("coreGenerationId").and_then(Value::as_str) != Some(generation_id)
    {
        return Err("COMPOSER_TOOLS_RESPONSE_INVALID".to_string());
    }
    let tools = object
        .get("tools")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 64)
        .ok_or_else(|| "COMPOSER_TOOLS_RESPONSE_INVALID".to_string())?;
    for tool in tools {
        let item = tool
            .as_object()
            .ok_or_else(|| "COMPOSER_TOOLS_RESPONSE_INVALID".to_string())?;
        let expected = [
            "description",
            "icon",
            "id",
            "label",
            "order",
            "pluginId",
            "toolId",
        ];
        if item.len() != expected.len() || expected.iter().any(|key| !item.contains_key(*key)) {
            return Err("COMPOSER_TOOLS_RESPONSE_INVALID".to_string());
        }
        let id = item.get("id").and_then(Value::as_str).unwrap_or_default();
        let plugin_id = item
            .get("pluginId")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let tool_id = item
            .get("toolId")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let label = item
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let description = item
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let icon = item.get("icon").and_then(Value::as_str).unwrap_or_default();
        if !valid_composer_tool_id(id)
            || id != format!("{plugin_id}:{tool_id}")
            || label.is_empty()
            || label.len() > 40
            || description.len() > 120
            || !matches!(
                icon,
                "camera"
                    | "folder"
                    | "globe"
                    | "link"
                    | "note"
                    | "settings"
                    | "sparkles"
                    | "terminal"
            )
            || item.get("order").and_then(Value::as_f64).is_none()
        {
            return Err("COMPOSER_TOOLS_RESPONSE_INVALID".to_string());
        }
    }
    Ok(())
}

#[tauri::command]
async fn composer_tools_get(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "COMPOSER_TOOLS_NOT_READY".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "ui.composer_tools.get",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    if handle.available_generation_id().ok().flatten().as_deref() != Some(generation_id.as_str()) {
        return Err("GENERATION_INVALIDATED".to_string());
    }
    let payload = settings_response_payload(response)?;
    validate_composer_tools_snapshot(&payload, &generation_id)?;
    Ok(payload)
}

#[tauri::command]
async fn composer_tool_invoke(
    window: WebviewWindow,
    tool_id: String,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if !valid_composer_tool_id(&tool_id) {
        return Err("COMPOSER_TOOL_ID_INVALID".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let response = dispatch_settings_request(
        handle,
        None,
        "ui.composer_tools.invoke",
        json!({"toolId": tool_id}),
        std::time::Duration::from_secs(15),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    let object = payload
        .as_object()
        .ok_or_else(|| "COMPOSER_TOOL_RESULT_INVALID".to_string())?;
    if object.len() != 2
        || object.get("status").and_then(Value::as_str) != Some("completed")
        || object
            .get("message")
            .and_then(Value::as_str)
            .is_none_or(|message| message.len() > 200)
    {
        return Err("COMPOSER_TOOL_RESULT_INVALID".to_string());
    }
    Ok(payload)
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TtsCancelSynthesisRequest {
    operation_id: String,
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
async fn tts_cancel_synthesis(
    window: WebviewWindow,
    payload: TtsCancelSynthesisRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<bool, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    if payload.operation_id.trim().is_empty() || payload.operation_id.len() > 128 {
        return Err("TTS_SYNTHESIS_CANCELLED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let response = dispatch_settings_request(
        handle,
        None,
        "tts.synthesis.cancel",
        json!({"operationId": payload.operation_id}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    Ok(settings_response_payload(response)?
        .get("accepted")
        .and_then(Value::as_bool)
        .unwrap_or(false))
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
    audio_state: State<'_, audio::AudioState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    // Playback belongs to the active AudioState, not to whichever Core
    // generation happens to be queryable at command time. During restart the
    // lifecycle intentionally exposes no available generation.
    audio_state.shutdown();
    Ok(())
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
fn current_bubble_auto_hide(
    window: WebviewWindow,
    settings: State<'_, chat_settings::BubbleAutoHideState>,
) -> Result<chat_settings::BubbleAutoHideSettings, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    settings.get()
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

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HistoryBootstrap {
    core_generation_id: String,
    character_id: String,
    assistant_name: String,
    subtitle_language: chat_settings::SubtitleLanguage,
    theme_tokens: std::collections::BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct HistoryPageRequest {
    core_generation_id: String,
    character_id: String,
    before_cursor: Option<String>,
}

async fn request_history_page(
    handle: shell_lifecycle::ShellLifecycleHandle,
    character_id: String,
    before_cursor: Option<String>,
) -> Result<history_window::HistoryPage, String> {
    let response = dispatch_settings_request(
        handle,
        None,
        "ui.history.page",
        json!({
            "expectedCharacterId": character_id,
            "beforeCursor": before_cursor,
            "limit": history_window::HISTORY_PAGE_LIMIT,
        }),
        std::time::Duration::from_secs(5),
    )
    .await?;
    history_window::validate_page(settings_response_payload(response)?)
}

#[tauri::command]
fn history_bootstrap(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
    subtitle: State<'_, chat_settings::SubtitleLanguageState>,
) -> Result<HistoryBootstrap, String> {
    history_window::validate_history_window(&window)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let active_appearance = appearance.persisted(&presentation.presentation)?;
    if active_appearance.core_generation_id != presentation.presentation.generation_id
        || active_appearance.character_id != presentation.presentation.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    Ok(HistoryBootstrap {
        core_generation_id: presentation.presentation.generation_id,
        character_id: presentation.presentation.character_id,
        assistant_name: presentation.presentation.display_name,
        subtitle_language: subtitle.get()?,
        theme_tokens: active_appearance.values.theme_tokens,
    })
}

#[tauri::command]
async fn history_page(
    window: WebviewWindow,
    request: HistoryPageRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
) -> Result<history_window::HistoryPage, String> {
    history_window::validate_history_window(&window)?;
    if request.core_generation_id.trim().is_empty()
        || request.character_id.trim().is_empty()
        || request
            .before_cursor
            .as_deref()
            .is_some_and(|cursor| cursor.trim().is_empty())
    {
        return Err("HISTORY_REQUEST_INVALID".to_string());
    }
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    if presentation.presentation.generation_id != request.core_generation_id
        || presentation.presentation.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    let page = request_history_page(
        settings_core_handle(&lifecycle)?,
        request.character_id.clone(),
        request.before_cursor,
    )
    .await?;
    if page.core_generation_id != request.core_generation_id
        || page.character_id != request.character_id
    {
        return Err("HISTORY_IDENTITY_MISMATCH".to_string());
    }
    Ok(page)
}

#[tauri::command]
fn close_history_window(window: WebviewWindow) -> Result<(), String> {
    history_window::validate_history_window(&window)?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
fn reveal_history_window(window: WebviewWindow) -> Result<(), String> {
    history_window::validate_history_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn runtime_log_viewer_bootstrap(
    window: WebviewWindow,
    runtime_log: State<'_, RuntimeLogService>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<runtime_log_window::RuntimeLogViewerBootstrap, String> {
    runtime_log_window::validate_runtime_log_window(&window)?;
    let theme_tokens = resources
        .active_presentation()
        .ok()
        .flatten()
        .and_then(|presentation| appearance.current(&presentation).ok())
        .map(|publication| publication.values.theme_tokens)
        .unwrap_or_else(runtime_log_window::fallback_theme_tokens);
    let snapshot = runtime_log.viewer_snapshot(None).map_err(str::to_string)?;
    Ok(runtime_log_window::RuntimeLogViewerBootstrap {
        schema_version: 2,
        theme_tokens,
        snapshot,
    })
}

#[tauri::command]
fn runtime_log_viewer_snapshot(
    window: WebviewWindow,
    after_sequence: Option<u64>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<runtime_log::RuntimeLogViewerSnapshot, String> {
    runtime_log_window::validate_runtime_log_window(&window)?;
    runtime_log
        .viewer_snapshot(after_sequence)
        .map_err(str::to_string)
}

#[tauri::command]
fn close_runtime_log_viewer(window: WebviewWindow) -> Result<(), String> {
    runtime_log_window::validate_runtime_log_window(&window)?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
fn reveal_runtime_log_viewer(window: WebviewWindow) -> Result<(), String> {
    runtime_log_window::validate_runtime_log_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
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
fn settings_bubble_auto_hide_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    settings: State<'_, chat_settings::BubbleAutoHideState>,
) -> Result<chat_settings::BubbleAutoHideSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    settings.snapshot(shell.generation()?)
}

#[tauri::command]
fn settings_bubble_auto_hide_save(
    window: WebviewWindow,
    window_generation: u64,
    values: chat_settings::BubbleAutoHideSettings,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    settings: State<'_, chat_settings::BubbleAutoHideState>,
) -> Result<chat_settings::BubbleAutoHideSettings, String> {
    product_shell::validate_settings_window(&window)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    let saved = settings.save(values)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    app_handle
        .emit_to("main", chat_settings::BUBBLE_AUTO_HIDE_CHANGED_EVENT, saved)
        .map_err(|error| format!("BUBBLE_AUTO_HIDE_PUBLICATION_FAILED: {error}"))?;
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

const CHARACTER_VISUAL_PREVIEW_EVENT: &str = "sakura://character-visual-preview";
const SETTINGS_APPEARANCE_ACTIVE_EVENT: &str = "sakura://settings-appearance-active";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterVisualPreviewPublication {
    schema_version: u32,
    window_generation: u64,
    revision: u64,
    presentation: character_presentation::FrontendCharacterPresentation,
    appearance: character_appearance::AppearancePublication,
}

fn emit_appearance(
    app_handle: &tauri::AppHandle,
    publication: character_appearance::AppearancePublication,
) -> Result<(), String> {
    app_handle
        .emit_to(
            "main",
            character_appearance::APPEARANCE_CHANGED_EVENT,
            publication.clone(),
        )
        .map_err(|error| error.to_string())?;
    if app_handle
        .get_webview_window(runtime_log_window::RUNTIME_LOG_WINDOW_LABEL)
        .is_some()
    {
        let _ = app_handle.emit_to(
            runtime_log_window::RUNTIME_LOG_WINDOW_LABEL,
            character_appearance::APPEARANCE_CHANGED_EVENT,
            publication,
        );
    }
    Ok(())
}

fn emit_settings_appearance_active(
    app_handle: &tauri::AppHandle,
    active: bool,
) -> Result<(), String> {
    app_handle
        .emit_to("main", SETTINGS_APPEARANCE_ACTIVE_EVENT, active)
        .map_err(|error| format!("failed to publish settings appearance state: {error}"))
}

#[tauri::command]
fn current_character_appearance(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<character_appearance::AppearancePublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let publication = appearance.persisted(&presentation.presentation)?;
    if app_handle
        .get_webview_window(runtime_log_window::RUNTIME_LOG_WINDOW_LABEL)
        .is_some()
    {
        let _ = app_handle.emit_to(
            runtime_log_window::RUNTIME_LOG_WINDOW_LABEL,
            character_appearance::APPEARANCE_CHANGED_EVENT,
            publication.clone(),
        );
    }
    Ok(publication)
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
    emit_settings_appearance_active(&app_handle, true)?;
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
fn settings_character_visual_preview(
    window: WebviewWindow,
    character_id: String,
    revision: u64,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
) -> Result<CharacterVisualPreviewPublication, String> {
    product_shell::validate_settings_window(&window)?;
    let window_generation = shell.generation()?;
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHARACTER_PRESENTATION_UNAVAILABLE".to_string())?
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "CHARACTER_PRESENTATION_NOT_READY".to_string())?;
    let (presentation, accepted) = resources.preview_character(
        character_id.trim(),
        &generation_id,
        window_generation,
        revision,
    )?;
    let appearance = appearance.persisted(&presentation.presentation)?;
    let publication = CharacterVisualPreviewPublication {
        schema_version: 1,
        window_generation,
        revision,
        presentation,
        appearance,
    };
    if accepted {
        sync_settings_window_appearance_background(&window, &publication.appearance)?;
        app_handle
            .emit_to("main", CHARACTER_VISUAL_PREVIEW_EVENT, publication.clone())
            .map_err(|error| format!("CHARACTER_VISUAL_PREVIEW_PUBLICATION_FAILED: {error}"))?;
    }
    Ok(publication)
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
        if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
            eprintln!("[macos-surface-snapshot] phase=settings-gesture active={active}");
        }
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
        if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
            eprintln!(
                "[macos-surface-snapshot] phase=settings-frame scale={portrait_scale_percent}"
            );
        }
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

fn validate_character_settings_snapshot(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "CHARACTER_SETTINGS_RESPONSE_INVALID".to_string())?;
    let expected = [
        "schemaVersion",
        "revision",
        "currentCharacterId",
        "characters",
    ];
    if object.len() != expected.len()
        || expected.iter().any(|key| !object.contains_key(*key))
        || object.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || object.get("revision").and_then(Value::as_u64).is_none()
    {
        return Err("CHARACTER_SETTINGS_RESPONSE_INVALID".to_string());
    }
    let current = object.get("currentCharacterId").expect("validated field");
    if !current.is_null() && current.as_str().is_none() {
        return Err("CHARACTER_SETTINGS_RESPONSE_INVALID".to_string());
    }
    let characters = object
        .get("characters")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 256)
        .ok_or_else(|| "CHARACTER_SETTINGS_RESPONSE_INVALID".to_string())?;
    let mut ids = std::collections::BTreeSet::new();
    for character in characters {
        let item = character
            .as_object()
            .ok_or_else(|| "CHARACTER_SETTINGS_RESPONSE_INVALID".to_string())?;
        if item.len() != 3
            || !["id", "displayName", "hasVoice"]
                .iter()
                .all(|key| item.contains_key(*key))
        {
            return Err("CHARACTER_SETTINGS_RESPONSE_INVALID".to_string());
        }
        let id = item.get("id").and_then(Value::as_str).unwrap_or_default();
        let display_name = item
            .get("displayName")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if id.is_empty()
            || id.len() > 128
            || display_name.is_empty()
            || display_name.len() > 128
            || item.get("hasVoice").and_then(Value::as_bool).is_none()
            || !ids.insert(id)
        {
            return Err("CHARACTER_SETTINGS_RESPONSE_INVALID".to_string());
        }
    }
    if let Some(current) = current.as_str() {
        if !ids.contains(current) {
            return Err("CHARACTER_SETTINGS_RESPONSE_INVALID".to_string());
        }
    }
    Ok(())
}

fn validate_character_settings_change(value: Value) -> Result<(Value, String), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "CHARACTER_SETTINGS_CHANGE_INVALID".to_string())?;
    let expected = ["schemaVersion", "snapshot", "changePlan"];
    if object.len() != expected.len()
        || expected.iter().any(|key| !object.contains_key(*key))
        || object.get("schemaVersion").and_then(Value::as_u64) != Some(1)
    {
        return Err("CHARACTER_SETTINGS_CHANGE_INVALID".to_string());
    }
    let snapshot = object
        .get("snapshot")
        .cloned()
        .ok_or_else(|| "CHARACTER_SETTINGS_CHANGE_INVALID".to_string())?;
    validate_character_settings_snapshot(&snapshot)?;
    let change_plan = object
        .get("changePlan")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "unchanged" | "core_restart_required"))
        .ok_or_else(|| "CHARACTER_SETTINGS_CHANGE_INVALID".to_string())?
        .to_string();
    Ok((snapshot, change_plan))
}

fn character_restart_target(snapshot: &Value, change_plan: &str) -> Result<Option<String>, String> {
    if change_plan != "core_restart_required" {
        return Ok(None);
    }
    snapshot
        .get("currentCharacterId")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(|value| Some(value.to_string()))
        .ok_or_else(|| "CHARACTER_SETTINGS_CHANGE_INVALID".to_string())
}

fn reveal_pet_when_session_ready(
    app_handle: tauri::AppHandle,
    handle: shell_lifecycle::ShellLifecycleHandle,
) {
    let _ = std::thread::Builder::new()
        .name("pet-session-ready".to_string())
        .spawn(move || {
            for _ in 0..80 {
                if handle.character_presentation().ok().flatten().is_some() {
                    let target = app_handle.clone();
                    let _ = app_handle.run_on_main_thread(move || {
                        let Some(window) = target.get_webview_window("main") else {
                            return;
                        };
                        if NativeWindowInteractionBackend
                            .set_visible(&window, true)
                            .is_ok()
                        {
                            let _ = reapply_current_pet_hit_region(&window);
                            let _ = product_shell::sync_product_tray_visibility(&target, true);
                        }
                    });
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        });
}

fn observe_character_restart(
    app_handle: tauri::AppHandle,
    handle: shell_lifecycle::ShellLifecycleHandle,
    previous_generation_id: String,
    previous_generation_number: u64,
    target_character_id: String,
) {
    let _ = std::thread::Builder::new()
        .name("character-switch-ready".to_string())
        .spawn(move || {
            // Shutdown can take 5s, hello/initialize 8s and Core readiness up
            // to 30s. Keep the native ready event alive across that complete
            // lifecycle budget so history cannot remain on the old identity.
            for _ in 0..1300 {
                let generation_id = handle
                    .ready_character_generation(
                        &previous_generation_id,
                        previous_generation_number,
                        &target_character_id,
                    )
                    .ok()
                    .flatten();
                if let Some(generation_id) = generation_id {
                    let target = app_handle.clone();
                    let character_id = target_character_id.clone();
                    let _ = app_handle.run_on_main_thread(move || {
                        if let Some(window) = target.get_webview_window("main") {
                            if NativeWindowInteractionBackend
                                .set_visible(&window, true)
                                .is_ok()
                            {
                                let _ = reapply_current_pet_hit_region(&window);
                                let _ = product_shell::sync_product_tray_visibility(&target, true);
                            }
                        }
                        if let Some(history) =
                            target.get_webview_window(history_window::HISTORY_WINDOW_LABEL)
                        {
                            let _ = history.emit(
                                history_window::HISTORY_REFRESH_REQUESTED_EVENT,
                                json!({
                                    "generationId": generation_id,
                                    "characterId": character_id,
                                    "reset": true,
                                    "ready": true,
                                }),
                            );
                        }
                    });
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        });
}

fn observe_studio_character_restart(
    app_handle: tauri::AppHandle,
    handle: shell_lifecycle::ShellLifecycleHandle,
    previous_generation_id: String,
    previous_generation_number: u64,
    target_character_id: String,
) {
    let _ = std::thread::Builder::new()
        .name("studio-character-reload-ready".to_string())
        .spawn(move || {
            for _ in 0..1300 {
                if let Some(generation_id) = handle
                    .ready_character_generation(
                        &previous_generation_id,
                        previous_generation_number,
                        &target_character_id,
                    )
                    .ok()
                    .flatten()
                {
                    let _ = app_handle.emit_to(
                        character_studio_window::STUDIO_WINDOW_LABEL,
                        "sakura://studio-runtime-reload",
                        json!({"state": "ready", "generationId": generation_id}),
                    );
                    let _ = app_handle.emit_to(
                        product_shell::SETTINGS_WINDOW_LABEL,
                        character_studio_window::CHARACTER_CATALOG_CHANGED_EVENT,
                        json!({"generationId": generation_id}),
                    );
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            let _ = app_handle.emit_to(
                character_studio_window::STUDIO_WINDOW_LABEL,
                "sakura://studio-runtime-reload",
                json!({
                    "state": "failed",
                    "message": "角色已经保存，但运行态未能重新加载。请重启 Sakura 后使用新数据。"
                }),
            );
        });
}

async fn character_settings_request(
    window: &WebviewWindow,
    shell: &product_shell::ProductShellState,
    lifecycle: &ShellLifecycleState,
    name: &'static str,
    payload: Value,
    deadline: std::time::Duration,
) -> Result<(Value, shell_lifecycle::ShellLifecycleHandle), String> {
    product_shell::validate_settings_window(window)?;
    let handle = lifecycle
        .handle
        .clone()
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(handle.clone(), None, name, payload, deadline).await?;
    assert_settings_identity(shell, &handle, window_generation, &core_generation_id)?;
    let snapshot = settings_response_payload(response)?;
    validate_character_settings_snapshot(&snapshot)?;
    Ok((snapshot, handle))
}

async fn character_settings_change_request(
    window: &WebviewWindow,
    shell: &product_shell::ProductShellState,
    lifecycle: &ShellLifecycleState,
    name: &'static str,
    payload: Value,
    deadline: std::time::Duration,
) -> Result<
    (
        Value,
        String,
        shell_lifecycle::ShellLifecycleHandle,
        String,
        u64,
        Option<String>,
    ),
    String,
> {
    product_shell::validate_settings_window(window)?;
    let handle = lifecycle
        .handle
        .clone()
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let window_generation = shell.generation()?;
    let (core_generation_id, core_generation_number) = handle
        .available_generation_identity()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(handle.clone(), None, name, payload, deadline).await?;
    assert_settings_identity(shell, &handle, window_generation, &core_generation_id)?;
    let change = settings_response_payload(response)?;
    let (snapshot, change_plan) = validate_character_settings_change(change)?;
    // Resolve every restart identity before the caller can enqueue any
    // lifecycle side effect.
    let target_character_id = character_restart_target(&snapshot, &change_plan)?;
    Ok((
        snapshot,
        change_plan,
        handle,
        core_generation_id,
        core_generation_number,
        target_character_id,
    ))
}

fn character_switch_receipt(
    snapshot: Value,
    previous_core_generation_id: String,
    restart_state: &str,
) -> Result<Value, String> {
    let target_character_id = snapshot
        .get("currentCharacterId")
        .cloned()
        .unwrap_or(Value::Null);
    if !matches!(restart_state, "not_required" | "requested") {
        return Err("CHARACTER_SETTINGS_CHANGE_INVALID".to_string());
    }
    Ok(json!({
        "schemaVersion": 1,
        "snapshot": snapshot,
        "targetCharacterId": target_character_id,
        "previousCoreGenerationId": previous_core_generation_id,
        "restartState": restart_state,
    }))
}

#[tauri::command]
async fn settings_characters_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    character_settings_request(
        &window,
        &shell,
        &lifecycle,
        "characters.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await
    .map(|(snapshot, _)| snapshot)
}

fn archive_dialog_path(path: &std::path::Path) -> Result<String, String> {
    path.to_str()
        .filter(|value| !value.is_empty() && value.len() <= 4096)
        .map(str::to_string)
        .ok_or_else(|| "CHARACTER_ARCHIVE_PATH_INVALID".to_string())
}

#[tauri::command]
async fn settings_character_choose_import(
    window: WebviewWindow,
    kind: String,
) -> Result<Option<String>, String> {
    product_shell::validate_settings_window(&window)?;
    let (title, filter_name, extension) = match kind.as_str() {
        "character" => ("导入 Sakura 角色包", "Sakura 角色包", "char"),
        "voice" => ("导入 Sakura TTS 模型包", "Sakura TTS 模型包", "voice"),
        _ => return Err("CHARACTER_ARCHIVE_KIND_INVALID".to_string()),
    };
    let selected = rfd::AsyncFileDialog::new()
        .set_title(title)
        .add_filter(filter_name, &[extension])
        .pick_file()
        .await;
    selected
        .map(|file| archive_dialog_path(file.path()))
        .transpose()
}

#[tauri::command]
async fn settings_character_choose_export(
    window: WebviewWindow,
    kind: String,
    default_name: String,
) -> Result<Option<String>, String> {
    product_shell::validate_settings_window(&window)?;
    let (title, filter_name, extension) = match kind.as_str() {
        "full" => ("导出 Sakura 完整角色包", "Sakura 角色包", "char"),
        "card" => ("导出 Sakura 单角色包", "Sakura 角色包", "char"),
        "voice" => ("导出 Sakura TTS 模型包", "Sakura TTS 模型包", "voice"),
        _ => return Err("CHARACTER_ARCHIVE_KIND_INVALID".to_string()),
    };
    let default_path = std::path::Path::new(default_name.trim());
    let valid_default_name = default_path
        .file_name()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value == default_name.trim())
        && default_path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(extension));
    if !valid_default_name || default_name.len() > 255 {
        return Err("CHARACTER_ARCHIVE_DEFAULT_NAME_INVALID".to_string());
    }
    let selected = rfd::AsyncFileDialog::new()
        .set_title(title)
        .set_file_name(default_name.trim())
        .add_filter(filter_name, &[extension])
        .save_file()
        .await;
    selected
        .map(|file| archive_dialog_path(file.path()))
        .transpose()
}

#[tauri::command]
async fn settings_character_import(
    window: WebviewWindow,
    path: String,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
) -> Result<Value, String> {
    let (
        snapshot,
        _change_plan,
        handle,
        previous_generation_id,
        previous_generation_number,
        target_character_id,
    ) = character_settings_change_request(
        &window,
        &shell,
        &lifecycle,
        "characters.settings.import",
        json!({"path": path}),
        std::time::Duration::from_secs(120),
    )
    .await?;
    if let Some(target_character_id) = target_character_id {
        handle
            .restart()
            .map_err(|_| "CHARACTER_RESTART_REQUEST_FAILED".to_string())?;
        audio_state.shutdown();
        if let Some(history) = app_handle.get_webview_window(history_window::HISTORY_WINDOW_LABEL) {
            let _ = history.emit(
                history_window::HISTORY_REFRESH_REQUESTED_EVENT,
                json!({
                    "previousGenerationId": previous_generation_id.clone(),
                    "characterId": target_character_id.clone(),
                    "reset": true,
                    "ready": false,
                }),
            );
        }
        observe_character_restart(
            app_handle,
            handle,
            previous_generation_id.clone(),
            previous_generation_number,
            target_character_id,
        );
        return character_switch_receipt(snapshot, previous_generation_id, "requested");
    }
    character_switch_receipt(snapshot, previous_generation_id, "not_required")
}

#[tauri::command]
async fn settings_character_select(
    window: WebviewWindow,
    character_id: String,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
) -> Result<Value, String> {
    let (
        snapshot,
        _change_plan,
        handle,
        previous_generation_id,
        previous_generation_number,
        target_character_id,
    ) = character_settings_change_request(
        &window,
        &shell,
        &lifecycle,
        "characters.settings.select",
        json!({"characterId": character_id}),
        std::time::Duration::from_secs(15),
    )
    .await?;
    if let Some(target_character_id) = target_character_id {
        handle
            .restart()
            .map_err(|_| "CHARACTER_RESTART_REQUEST_FAILED".to_string())?;
        audio_state.shutdown();
        if let Some(history) = app_handle.get_webview_window(history_window::HISTORY_WINDOW_LABEL) {
            let _ = history.emit(
                history_window::HISTORY_REFRESH_REQUESTED_EVENT,
                json!({
                    "previousGenerationId": previous_generation_id.clone(),
                    "characterId": target_character_id.clone(),
                    "reset": true,
                    "ready": false,
                }),
            );
        }
        observe_character_restart(
            app_handle,
            handle,
            previous_generation_id.clone(),
            previous_generation_number,
            target_character_id,
        );
        return character_switch_receipt(snapshot, previous_generation_id, "requested");
    }
    character_switch_receipt(snapshot, previous_generation_id, "not_required")
}

fn validate_storage_settings_snapshot(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "STORAGE_SETTINGS_RESPONSE_INVALID".to_string())?;
    let expected = [
        "schemaVersion",
        "userRoot",
        "ttsRoot",
        "ttsRootSource",
        "ttsRootAvailable",
        "reasonCode",
    ];
    if object.len() != expected.len()
        || expected.iter().any(|key| !object.contains_key(*key))
        || object.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || !matches!(
            object.get("ttsRootSource").and_then(Value::as_str),
            Some("default" | "custom")
        )
        || object
            .get("ttsRootAvailable")
            .and_then(Value::as_bool)
            .is_none()
    {
        return Err("STORAGE_SETTINGS_RESPONSE_INVALID".to_string());
    }
    for key in ["userRoot", "ttsRoot"] {
        let path = object.get(key).and_then(Value::as_str).unwrap_or_default();
        if path.is_empty() || !std::path::Path::new(path).is_absolute() {
            return Err("STORAGE_SETTINGS_RESPONSE_INVALID".to_string());
        }
    }
    let reason = object.get("reasonCode").expect("validated field");
    if !reason.is_null()
        && !matches!(
            reason.as_str(),
            Some("TTS_ROOT_MISSING" | "TTS_ROOT_NOT_DIRECTORY" | "TTS_ROOT_NOT_WRITABLE")
        )
    {
        return Err("STORAGE_SETTINGS_RESPONSE_INVALID".to_string());
    }
    let available = object
        .get("ttsRootAvailable")
        .and_then(Value::as_bool)
        .expect("validated field");
    if (available && !reason.is_null()) || (!available && reason.is_null()) {
        return Err("STORAGE_SETTINGS_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

async fn storage_settings_request(
    window: &WebviewWindow,
    shell: &product_shell::ProductShellState,
    lifecycle: &ShellLifecycleState,
    name: &'static str,
    payload: Value,
) -> Result<Value, String> {
    product_shell::validate_settings_window(window)?;
    let handle = lifecycle
        .handle
        .clone()
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        name,
        payload,
        std::time::Duration::from_secs(5),
    )
    .await?;
    assert_settings_identity(shell, &handle, window_generation, &core_generation_id)?;
    let snapshot = settings_response_payload(response)?;
    validate_storage_settings_snapshot(&snapshot)?;
    Ok(snapshot)
}

#[tauri::command]
async fn settings_storage_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    storage_settings_request(
        &window,
        &shell,
        &lifecycle,
        "storage.settings.get",
        json!({}),
    )
    .await
}

fn current_executable_directory() -> Result<std::path::PathBuf, String> {
    std::env::current_exe()
        .map_err(|_| "EXECUTABLE_DIRECTORY_UNAVAILABLE".to_string())?
        .parent()
        .map(ToOwned::to_owned)
        .ok_or_else(|| "EXECUTABLE_DIRECTORY_UNAVAILABLE".to_string())
}

#[tauri::command]
async fn settings_update_get(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<update_settings::UpdateSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::check(
        &app_handle,
        &current_executable_directory()?,
        runtime_log.inner(),
        "manual",
    )
    .await
}

#[tauri::command]
fn settings_update_cached_get(
    window: WebviewWindow,
    coordinator: State<'_, update_settings::UpdateCoordinator>,
) -> Result<Option<update_settings::UpdateSnapshot>, String> {
    product_shell::validate_settings_window(&window)?;
    coordinator.checked_snapshot()
}

#[tauri::command]
fn settings_update_preferences_get(
    window: WebviewWindow,
    coordinator: State<'_, update_settings::UpdateCoordinator>,
) -> Result<update_settings::UpdatePreferencesSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    coordinator.preferences()
}

#[tauri::command]
fn settings_update_preferences_set(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    coordinator: State<'_, update_settings::UpdateCoordinator>,
    auto_check_enabled: bool,
) -> Result<update_settings::UpdatePreferencesSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    let snapshot = coordinator.set_auto_check_enabled(auto_check_enabled)?;
    let _ = app_handle.emit_to(
        "main",
        update_settings::UPDATE_PREFERENCES_CHANGED_EVENT,
        &snapshot,
    );
    Ok(snapshot)
}

#[tauri::command]
fn settings_autostart_get(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
) -> Result<autostart_settings::AutostartSettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    autostart_settings::snapshot(&app_handle, shell.generation()?)
}

#[tauri::command]
fn settings_autostart_save(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    shell: State<'_, product_shell::ProductShellState>,
    window_generation: u64,
    launch_at_login: bool,
) -> Result<autostart_settings::AutostartSettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    if shell.generation()? != window_generation {
        return Err("SETTINGS_WINDOW_GENERATION_MISMATCH".to_string());
    }
    autostart_settings::save(&app_handle, window_generation, launch_at_login)
}

#[tauri::command]
async fn startup_update_check(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    coordinator: State<'_, update_settings::UpdateCoordinator>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<update_settings::StartupUpdateSnapshot, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    Ok(coordinator
        .startup_check(
            &app_handle,
            &current_executable_directory()?,
            runtime_log.inner(),
        )
        .await)
}

#[tauri::command]
async fn chat_update_announce(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    coordinator: State<'_, update_settings::UpdateCoordinator>,
) -> Result<chat_bridge::ChatSendPublication, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    let (event, version) = coordinator.pending_event()?;
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "CHAT_BRIDGE_UNAVAILABLE".to_string())?;
    let pending = handle
        .chat_bridge()?
        .send_update_available(window.label(), event, version)?;
    tauri::async_runtime::spawn_blocking(move || pending.wait())
        .await
        .map_err(|_| "CHAT_DISPATCH_ABORTED".to_string())?
}

#[tauri::command]
fn settings_about_get(window: WebviewWindow) -> Result<update_settings::AboutSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    Ok(update_settings::about_snapshot())
}

#[tauri::command]
fn settings_telemetry_get(
    window: WebviewWindow,
    telemetry: State<'_, telemetry::TelemetryService>,
) -> Result<telemetry::TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.snapshot()
}

#[tauri::command]
fn settings_telemetry_set_enabled(
    window: WebviewWindow,
    telemetry: State<'_, telemetry::TelemetryService>,
    enabled: bool,
) -> Result<telemetry::TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.set_enabled(enabled)
}

#[tauri::command]
fn settings_telemetry_regenerate_installation_id(
    window: WebviewWindow,
    telemetry: State<'_, telemetry::TelemetryService>,
) -> Result<telemetry::TelemetrySettingsSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    telemetry.regenerate_installation_id()
}

#[tauri::command]
fn settings_telemetry_open_documentation(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    telemetry::open_documentation()
}

#[tauri::command]
fn settings_about_open_website(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::open_website()
}

#[tauri::command]
fn settings_about_open_repository(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::open_repository()
}

#[tauri::command]
fn settings_about_open_changelog(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::open_changelog()
}

#[tauri::command]
fn settings_about_open_sponsor(window: WebviewWindow) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::open_sponsor()
}

#[tauri::command]
async fn settings_update_install(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    let lifecycle_handle = lifecycle.handle.clone();
    update_settings::install(
        &app_handle,
        &current_executable_directory()?,
        runtime_log.inner(),
        move || {
            lifecycle_handle
                .as_ref()
                .ok_or_else(|| "LIFECYCLE_COMMAND_UNAVAILABLE".to_string())?
                .shutdown_and_wait(std::time::Duration::from_secs(5))
                .map_err(str::to_string)
        },
    )
    .await
}

#[tauri::command]
fn settings_update_open_portable_download(
    window: WebviewWindow,
    url: String,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    update_settings::open_portable_download(&url)
}

fn open_directory(path: &std::path::Path) -> Result<(), String> {
    if !path.is_absolute() || !path.is_dir() {
        return Err("STORAGE_DIRECTORY_UNAVAILABLE".to_string());
    }
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer.exe");
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = std::process::Command::new("xdg-open");
    command
        .arg(path)
        .spawn()
        .map(|_| ())
        .map_err(|_| "STORAGE_DIRECTORY_OPEN_FAILED".to_string())
}

#[tauri::command]
async fn settings_storage_open_user_root(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<(), String> {
    let snapshot = storage_settings_request(
        &window,
        &shell,
        &lifecycle,
        "storage.settings.get",
        json!({}),
    )
    .await?;
    let root = snapshot
        .get("userRoot")
        .and_then(Value::as_str)
        .ok_or_else(|| "STORAGE_SETTINGS_RESPONSE_INVALID".to_string())?;
    open_directory(std::path::Path::new(root))
}

#[tauri::command]
async fn settings_storage_choose_tts_root(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Option<Value>, String> {
    product_shell::validate_settings_window(&window)?;
    let selected = tauri::async_runtime::spawn_blocking(|| {
        rfd::FileDialog::new()
            .set_title("选择 TTS 数据目录")
            .pick_folder()
    })
    .await
    .map_err(|_| "TTS_ROOT_CHOOSER_FAILED".to_string())?;
    let Some(path) = selected else {
        return Ok(None);
    };
    let snapshot = storage_settings_request(
        &window,
        &shell,
        &lifecycle,
        "storage.settings.choose_tts_root",
        json!({"path": path.to_string_lossy()}),
    )
    .await?;
    Ok(Some(snapshot))
}

#[tauri::command]
async fn settings_storage_reset_tts_root(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    storage_settings_request(
        &window,
        &shell,
        &lifecycle,
        "storage.settings.reset_tts_root",
        json!({}),
    )
    .await
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
        std::time::Duration::from_secs(10),
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
    app_handle: tauri::AppHandle,
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
        std::time::Duration::from_secs(15),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    reveal_pet_when_session_ready(app_handle, handle);
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
async fn settings_screen_awareness_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    if !matches!(
        window.label(),
        "main" | product_shell::SETTINGS_WINDOW_LABEL
    ) {
        return Err("SCREEN_AWARENESS_WINDOW_INVALID".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle,
        None,
        "screen_awareness.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "SCREEN_AWARENESS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(shell.generation()?));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_screen_awareness_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    settings: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "screen_awareness.settings.save",
        json!({"settings": settings}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "SCREEN_AWARENESS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    if let Some(saved_settings) = object.get("settings").cloned() {
        let _ = window.app_handle().emit_to(
            "main",
            "sakura://screen-awareness-settings",
            saved_settings,
        );
    }
    Ok(payload)
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
    plugin_id: String,
    section_id: String,
    values: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    plugin_settings::validate_settings_save_request(&plugin_id, &section_id, &values)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.settings.save",
        json!({"pluginId": plugin_id, "sectionId": section_id, "values": values}),
        std::time::Duration::from_secs(8),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_settings_save_result(&payload)?;
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_enabled_set(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    install_id: String,
    enabled: bool,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    plugin_settings::validate_enabled_request(&revision, &install_id)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.enabled.set",
        json!({"revision": revision, "installId": install_id, "enabled": enabled}),
        std::time::Duration::from_secs(12),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("enabled_changed")
        || payload.get("installId").and_then(Value::as_str) != Some(install_id.as_str())
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
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
async fn settings_plugins_install(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    source_kind: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let selected = match source_kind.as_str() {
        "zip" => {
            rfd::AsyncFileDialog::new()
                .add_filter("Sakura 插件 ZIP", &["zip"])
                .pick_file()
                .await
        }
        "folder" => rfd::AsyncFileDialog::new().pick_folder().await,
        _ => return Err("PLUGIN_INSTALL_SOURCE_INVALID".to_string()),
    };
    let Some(selected) = selected else {
        return Ok(json!({"cancelled": true}));
    };
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let source_path = selected
        .path()
        .to_str()
        .filter(|value| !value.is_empty() && value.len() <= 4096)
        .ok_or_else(|| "PLUGIN_INSTALL_SOURCE_INVALID".to_string())?;
    if source_kind == "zip"
        && selected
            .path()
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("zip"))
    {
        return Err("PLUGIN_INSTALL_SOURCE_INVALID".to_string());
    }
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.install",
        json!({
            "revision": revision,
            "sourceKind": source_kind,
            "sourcePath": source_path,
        }),
        std::time::Duration::from_secs(30),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("installed") {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_uninstall(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    install_id: String,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.uninstall",
        json!({"revision": revision, "installId": install_id}),
        std::time::Duration::from_secs(30),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_management_result(&payload)?;
    if payload.get("managementAction").and_then(Value::as_str) != Some("uninstalled")
        || payload.get("installId").and_then(Value::as_str) != Some(install_id.as_str())
    {
        return Err("PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string());
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "PLUGIN_MANAGEMENT_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
async fn settings_plugins_collection(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    operation: String,
    plugin_id: String,
    section_id: String,
    collection_id: String,
    payload: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    plugin_settings::validate_collection_request(
        &operation,
        &plugin_id,
        &section_id,
        &collection_id,
        &payload,
    )?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut request_payload = payload
        .as_object()
        .cloned()
        .ok_or_else(|| "PLUGIN_COLLECTION_REQUEST_INVALID".to_string())?;
    request_payload.insert("pluginId".to_string(), json!(plugin_id));
    request_payload.insert("sectionId".to_string(), json!(section_id));
    request_payload.insert("collectionId".to_string(), json!(collection_id));
    let request_name = match operation.as_str() {
        "query" => "plugins.collection.query",
        "create" => "plugins.collection.create",
        "update" => "plugins.collection.update",
        "delete" => "plugins.collection.delete",
        _ => return Err("PLUGIN_COLLECTION_REQUEST_INVALID".to_string()),
    };
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        request_name,
        Value::Object(request_payload),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let result = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    plugin_settings::validate_collection_result(&operation, &result)?;
    Ok(result)
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
        #[cfg(windows)]
        {
            let current = geometry
                .hit_regions
                .as_ref()
                .ok_or_else(|| "PET_HIT_REGIONS_NOT_READY".to_string())?;
            let coarse = window_interaction::coarse_preview_hit_regions(current);
            apply_precise_hit_regions(&window, &coarse)?;
        }
        geometry.activate_control_surface_preview(revision);
        Ok(())
    })
}

#[tauri::command]
fn preview_pet_control_surface(
    window: WebviewWindow,
    preview_revision: u64,
    control_surface: ControlSurfaceLayout,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    layout_contract()?.validate_control_surface(PresentationState::Product, &control_surface)?;
    let mut geometry = geometry_state
        .lock()
        .map_err(|_| "window geometry state is unavailable".to_string())?;
    if !geometry.can_end_control_surface_preview(preview_revision) {
        return Ok(());
    }
    let application = geometry
        .application
        .clone()
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let previous = geometry.control_surface.clone();
    let input_surface_changed = previous.as_ref().is_none_or(|previous| {
        previous.input_rect != control_surface.input_rect
            || previous.input_visible != control_surface.input_visible
    });
    #[cfg(windows)]
    {
        let contract = layout_contract()?;
        let coarse = build_coarse_native_interaction_regions(
            &contract,
            &application,
            Some(&control_surface),
            geometry.portrait_alpha_mask.as_ref(),
            geometry.portrait_scale_percent,
        )?;
        // Keep the resident HWND stable, but never expose its transparent remainder as input.
        // This region has only component rectangles, so it is cheap enough for latest-wins frames.
        apply_precise_hit_regions(&window, &coarse)?;
    }
    // Deferred settings frames still own the latest logical geometry. Portrait-scale settlement
    // and drag authorization must not fall back to the control surface from before the slider
    // session merely because the expensive precise native region is intentionally postponed.
    geometry.control_surface = Some(control_surface.clone());
    if input_surface_changed {
        glass.update_control_surface(
            &window,
            &control_surface,
            &application,
            previous.as_ref(),
            None,
        )?;
    }
    Ok(())
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
        if geometry.context_menu_open || geometry.portrait_hit_relaxed {
            geometry.control_surface_preview_active = false;
            return Ok(());
        }
        let application = geometry
            .application
            .clone()
            .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
        let hit_regions = build_native_interaction_regions(
            &layout_contract()?,
            &application,
            geometry.control_surface.as_ref(),
            geometry.portrait_alpha_mask.as_ref(),
            geometry.portrait_scale_percent,
        )?;
        if let Err(error) = apply_precise_hit_regions_with_synchronous_redraw(&window, &hit_regions)
        {
            return Err(error);
        }
        geometry.hit_regions = Some(hit_regions);
        geometry.control_surface_preview_active = false;
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
    geometry.require_context_menu_closed()?;
    let state = geometry
        .state
        .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
    let contract = layout_contract()?;
    let monitor = target_monitor(&window, geometry.portrait_anchor)?;
    let application = if current_portrait_scale_platform_capabilities().resident_stable_bounds {
        // Windows already owns a canonical backing envelope that covers every portrait and
        // control layout. Keep it byte-for-byte stable and only widen the precise Win32 region
        // below so the two cross-fade layers remain visible and interactive.
        geometry
            .application
            .clone()
            .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?
    } else {
        let current_application = geometry
            .application
            .clone()
            .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
        let old_visible_bounds =
            window_interaction::logical_visible_surface_bounds_with_control_surface(
                &contract,
                state,
                geometry.portrait_scale_percent,
                geometry.control_surface.as_ref(),
                geometry.portrait_alpha_mask.as_ref(),
            )?;
        let new_visible_bounds =
            window_interaction::logical_visible_surface_bounds_with_control_surface(
                &contract,
                state,
                geometry.portrait_scale_percent,
                geometry.control_surface.as_ref(),
                Some(&next_mask),
            )?;
        let visible_transition_bounds =
            window_interaction::union_surface_bounds(old_visible_bounds, new_visible_bounds);
        if window_interaction::logical_surface_contains(
            current_application.active_bounds,
            visible_transition_bounds,
        ) {
            current_application
        } else {
            // macOS/Linux do not retain the Windows all-layout envelope. Keep their native frame
            // stable for the duration of the cross-fade across just the two involved portraits.
            let old_bounds =
                window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
                    &contract,
                    state,
                    geometry.portrait_scale_percent,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                )?;
            let new_bounds =
                window_interaction::logical_scale_stable_surface_bounds_with_control_surface(
                    &contract,
                    state,
                    geometry.portrait_scale_percent,
                    geometry.control_surface.as_ref(),
                    Some(&next_mask),
                )?;
            apply_window_layout(
                &contract,
                state,
                geometry.applied_revision,
                &monitor,
                geometry.portrait_anchor,
                window_interaction::union_surface_bounds(old_bounds, new_bounds),
            )?
        }
    };
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
    // Never re-submit an unchanged native frame. AppKit/WebView2 can rebuild the root surface
    // before the CSS cross-fade has painted, exposing the stale stage as a clipped or shifted
    // bubble/input frame. The platform hit router can be widened in place.
    let commit = if geometry_unchanged {
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
    Ok(Some(application))
}

#[tauri::command]
fn begin_portrait_scale_preview(
    window: WebviewWindow,
    revision: u64,
    trace: Option<interaction_latency::InteractionTraceContext>,
    lifecycle: State<'_, ShellLifecycleState>,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
    _glass: State<'_, input_visual_effect::InputVisualEffectState>,
) -> Result<Option<PortraitScalePreview>, String> {
    interaction_latency::command(
        "main.begin-portrait-scale-preview",
        trace,
        || {
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
                return Ok((None, false));
            }
            geometry.require_context_menu_closed()?;
            // A scale gesture owns the native surface transaction. If it starts while
            // a portrait cross-fade is waiting for its final commit, discard that
            // stale transition rather than letting it resize the window later.
            geometry.portrait_transition_pending = None;
            geometry.portrait_transition_active = false;
            geometry.portrait_transition_drag = None;

            // Beginning a gesture does not itself change the native surface. Publishing the current
            // application again would make the WebView rewrite every stage offset and layout variable
            // on a no-op pointer press, which can invalidate the whole transparent layer on macOS.
            let mut preview_application = None;
            #[cfg(target_os = "macos")]
            let mut snapshot_required = false;
            #[cfg(not(target_os = "macos"))]
            let snapshot_required = false;
            #[cfg(windows)]
            if !geometry.portrait_scale_preview_active {
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
                    if geometry.anchor_user_positioned {
                        AnchorPolicy::UserPositioned
                    } else {
                        AnchorPolicy::Automatic
                    },
                    geometry.portrait_scale_percent,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                    true,
                    uses_bubble_expansion_stable_surface_bounds(geometry.bubble_auto_expand),
                )?;
                let hit_regions = build_native_interaction_regions(
                    &contract,
                    &application,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                    geometry.portrait_scale_percent,
                )?;
                let coarse_preview_regions = build_coarse_native_interaction_regions(
                    &contract,
                    &application,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                    window_interaction::PORTRAIT_SCALE_MAX_PERCENT,
                )?;
                let previous_application = geometry.application.clone();
                let previous_regions = geometry.hit_regions.clone();
                let geometry_changed = previous_application
                    .as_ref()
                    .is_none_or(|previous| !same_surface_geometry(previous, &application));
                // A one-time relaxation is needed only if this older session has not entered the
                // resident Windows envelope yet. The steady-state settings session keeps a coarse
                // maximum-scale portrait/control region instead of making the whole HWND clickable.
                if geometry_changed {
                    NativeWindowInteractionBackend
                        .relax_hit_regions(&window)
                        .map_err(|error| error.to_string())?;
                }
                apply_native_pet_surface_bounds_transaction(
                    &window,
                    &application,
                    previous_application.as_ref(),
                    previous_regions.as_ref(),
                )?;
                if let Err(error) = apply_precise_hit_regions(&window, &coarse_preview_regions) {
                    if geometry_changed {
                        return match rollback_pet_surface(
                        &window,
                        previous_application.as_ref(),
                        previous_regions.as_ref(),
                    ) {
                        Ok(()) => Err(format!(
                            "PET_SCALE_PREVIEW_REGION_FAILED_PREVIOUS_RESTORED: {error}"
                        )),
                        Err(rollback_error) => Err(format!(
                            "PET_SCALE_PREVIEW_REGION_FAILED: {error}; PET_SURFACE_ROLLBACK_FAILED: {rollback_error}"
                        )),
                    };
                    }
                    return Err(error);
                }
                if let Some(surface) = geometry.control_surface.as_ref() {
                    _glass.update_control_surface(&window, surface, &application, None, None)?;
                }
                geometry.portrait_anchor = Some(application.portrait_anchor);
                geometry.physical_local_anchor = Some(application.physical_local_anchor);
                geometry.active_bounds = Some(application.active_bounds);
                geometry.surface_scale = application.scale_factor * application.content_scale;
                geometry.application = Some(application.clone());
                geometry.hit_regions = Some(hit_regions);
                preview_application = Some(application);
            }

            #[cfg(target_os = "macos")]
            if !geometry.portrait_scale_preview_active {
                let state = geometry
                    .state
                    .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
                let contract = layout_contract()?;
                let monitor = target_monitor(&window, geometry.portrait_anchor)?;
                let current_application = geometry
                    .application
                    .as_ref()
                    .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
                let stable_application = compute_pet_window_layout(
                    &contract,
                    state,
                    geometry.applied_revision,
                    &monitor,
                    geometry.portrait_anchor,
                    if geometry.anchor_user_positioned {
                        AnchorPolicy::UserPositioned
                    } else {
                        AnchorPolicy::Automatic
                    },
                    geometry.portrait_scale_percent,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                    true,
                    uses_bubble_expansion_stable_surface_bounds(geometry.bubble_auto_expand),
                )?;
                let preview = clip_portrait_scale_preview_application_to_work_area(
                    &contract,
                    &monitor,
                    current_application,
                    stable_application,
                )?;
                if !same_surface_geometry(current_application, &preview) {
                    snapshot_required = true;
                }
                preview_application = Some(preview);
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
                    if geometry.anchor_user_positioned {
                        AnchorPolicy::UserPositioned
                    } else {
                        AnchorPolicy::Automatic
                    },
                    geometry.portrait_scale_percent,
                    geometry.control_surface.as_ref(),
                    geometry.portrait_alpha_mask.as_ref(),
                    true,
                    uses_bubble_expansion_stable_surface_bounds(geometry.bubble_auto_expand),
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
                geometry.portrait_hit_resource_id = None;
            }
            geometry.portrait_hit_generation = Some(generation_id);
            geometry.portrait_hit_revision = revision;
            geometry.portrait_hit_relaxed = defers_portrait_scale_hit_region_frames();
            geometry.portrait_scale_preview_active = true;
            Ok((
                Some(PortraitScalePreview {
                    application: preview_application,
                    deferred_native: defers_native_portrait_scale_frames(),
                    deferred_hit_regions: defers_portrait_scale_hit_region_frames(),
                    precommit_on_first_frame: cfg!(target_os = "macos"),
                    snapshot_required,
                }),
                snapshot_required,
            ))
        },
    )
    .map(|(preview, _)| preview)
}

#[tauri::command]
async fn prepare_portrait_scale_preview_snapshot(
    window: WebviewWindow,
    revision: u64,
    geometry_state: State<'_, Mutex<WindowGeometrySession>>,
) -> Result<bool, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    #[cfg(target_os = "macos")]
    {
        let ready = {
            let geometry = geometry_state
                .lock()
                .map_err(|_| "window geometry state is unavailable".to_string())?;
            geometry.portrait_scale_preview_active && geometry.portrait_hit_revision == revision
        };
        if !ready {
            return Ok(false);
        }
        let installed = macos_surface_snapshot::install(&window, revision).await?;
        let still_current = {
            let geometry = geometry_state
                .lock()
                .map_err(|_| "window geometry state is unavailable".to_string())?;
            installed
                && geometry.portrait_scale_preview_active
                && geometry.portrait_hit_revision == revision
        };
        if !still_current {
            macos_surface_snapshot::finish(&window, revision).await?;
            return Ok(false);
        }
        if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
            eprintln!(
                "[macos-surface-snapshot] phase=prepare-return revision={revision} snapshot=true"
            );
        }
        return Ok(true);
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (revision, geometry_state);
        Ok(false)
    }
}

#[tauri::command]
fn activate_portrait_hit_test(
    window: WebviewWindow,
    portrait_key: String,
    portrait_resource_id: Option<String>,
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
        geometry.require_context_menu_closed()?;
        let transition_pending = cfg!(target_os = "macos") && geometry.portrait_transition_active;
        let cache_matches = same_generation
            && geometry.portrait_hit_key.as_deref() == Some(portrait_key.as_str())
            && geometry.portrait_hit_resource_id.as_deref() == portrait_resource_id.as_deref()
            && geometry.portrait_alpha_mask.is_some();
        let mut resolved_alpha_mask = if cache_matches {
            geometry.portrait_alpha_mask.clone()
        } else {
            None
        };
        if !cache_matches {
            drop(geometry);
            let mask_started = std::time::Instant::now();
            let alpha_mask = resources.portrait_alpha_mask(
                &portrait_key,
                portrait_resource_id.as_deref(),
                &generation_id,
            )?;
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
            geometry.require_context_menu_closed()?;
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
                geometry.portrait_hit_resource_id = portrait_resource_id.clone();
                resolved_alpha_mask = Some(alpha_mask);
            }
        }
        let state = geometry
            .state
            .ok_or_else(|| "pet layout is not ready for portrait hit testing".to_string())?;
        let contract = layout_contract()?;
        let monitor = target_monitor(&window, geometry.portrait_anchor)?;
        let stabilize_portrait_scale = geometry.stabilizes_portrait_scale_bounds();
        let preserve_portrait_scale_anchor = preserves_portrait_anchor_for_scale_settlement(
            geometry.portrait_scale_preview_active,
            geometry.portrait_scale_gesture_active,
        );
        let defer_portrait_hit_regions = geometry.defers_precise_portrait_scale_hit_regions();
        let defer_precise_hit_regions = geometry.defers_precise_surface_hit_regions();
        let defer_portrait_transition_native =
            cfg!(target_os = "macos") && geometry.portrait_transition_active;
        let portrait_alpha_mask = resolved_alpha_mask
            .as_ref()
            .or(geometry.portrait_alpha_mask.as_ref());
        let reusable_application = geometry.application.as_ref().filter(|application| {
            can_reuse_resident_portrait_application(
                current_portrait_scale_platform_capabilities().resident_stable_bounds,
                application,
                state,
                geometry.applied_revision,
                &monitor,
            )
        });
        let application = match reusable_application {
            Some(application) => application.clone(),
            None => compute_pet_window_layout(
                &contract,
                state,
                geometry.applied_revision,
                &monitor,
                geometry.portrait_anchor,
                if geometry.anchor_user_positioned || preserve_portrait_scale_anchor {
                    AnchorPolicy::UserPositioned
                } else {
                    AnchorPolicy::Automatic
                },
                portrait_scale_percent,
                geometry.control_surface.as_ref(),
                portrait_alpha_mask,
                stabilize_portrait_scale,
                uses_bubble_expansion_stable_surface_bounds(geometry.bubble_auto_expand),
            )?,
        };
        let application = if cfg!(target_os = "macos") && stabilize_portrait_scale {
            let current_application = geometry
                .application
                .as_ref()
                .ok_or_else(|| "PET_LAYOUT_NOT_READY".to_string())?;
            clip_portrait_scale_preview_application_to_work_area(
                &contract,
                &monitor,
                current_application,
                application,
            )?
        } else {
            application
        };
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
        geometry.portrait_hit_resource_id = portrait_resource_id;
        geometry.portrait_hit_revision = revision;
        // A concurrent control-surface preview keeps a coarse HWND region, but it must not masquerade
        // as an unfinished portrait gesture. end_control_surface_preview owns the final precise
        // region once both previews have actually ended.
        geometry.portrait_hit_relaxed = defer_portrait_hit_regions;
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
    geometry.require_context_menu_closed()?;
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
    geometry.portrait_hit_resource_id = None;
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
    geometry.require_context_menu_closed()?;
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
        if geometry.anchor_user_positioned {
            AnchorPolicy::UserPositioned
        } else {
            AnchorPolicy::Automatic
        },
        geometry.portrait_scale_percent,
        geometry.control_surface.as_ref(),
        geometry.portrait_alpha_mask.as_ref(),
        false,
        uses_bubble_expansion_stable_surface_bounds(geometry.bubble_auto_expand),
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
async fn finish_portrait_scale_preview_snapshot(
    window: WebviewWindow,
    revision: u64,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    #[cfg(target_os = "macos")]
    {
        if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
            eprintln!("[macos-surface-snapshot] phase=finish-command");
        }
        macos_surface_snapshot::finish(&window, revision).await?;
    }
    #[cfg(not(target_os = "macos"))]
    let _ = revision;
    Ok(())
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

fn studio_method_name(method: &str) -> Result<&'static str, String> {
    match method {
        "studio.bootstrap" => Ok("studio.bootstrap"),
        "studio.character.open" => Ok("studio.character.open"),
        "studio.character.create" => Ok("studio.character.create"),
        "studio.character.publish" => Ok("studio.character.publish"),
        "studio.draft.save" => Ok("studio.draft.save"),
        "studio.draft.discard" => Ok("studio.draft.discard"),
        "studio.workspace.release" => Ok("studio.workspace.release"),
        "studio.asset.import" => Ok("studio.asset.import"),
        "studio.reference.preview" => Ok("studio.reference.preview"),
        "studio.archive.export" => Ok("studio.archive.export"),
        "studio.operation.cancel" => Ok("studio.operation.cancel"),
        _ => Err("STUDIO_COMMAND_UNKNOWN".to_string()),
    }
}

fn validate_studio_payload(payload: &Value) -> Result<(), String> {
    let object = payload
        .as_object()
        .ok_or_else(|| "STUDIO_RESPONSE_INVALID".to_string())?;
    fn contains_private_path(value: &Value) -> bool {
        match value {
            Value::Object(object) => object.iter().any(|(key, item)| {
                matches!(key.as_str(), "packageDir" | "sourcePath") || contains_private_path(item)
            }),
            Value::Array(items) => items.iter().any(contains_private_path),
            _ => false,
        }
    }
    if object.get("schemaVersion").and_then(Value::as_u64) != Some(1)
        || contains_private_path(payload)
    {
        return Err("STUDIO_RESPONSE_INVALID".to_string());
    }
    Ok(())
}

#[tauri::command]
fn open_character_studio(
    window: WebviewWindow,
    character_id: String,
    app_handle: tauri::AppHandle,
    state: State<'_, character_studio_window::CharacterStudioWindowState>,
    topmost: State<'_, product_shell::PetTopmostState>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    let character_id = character_id.trim();
    if character_id.is_empty() || character_id.len() > 128 {
        return Err("STUDIO_CHARACTER_ID_INVALID".to_string());
    }
    character_studio_window::show_or_focus(
        &app_handle,
        character_id,
        state.inner(),
        topmost.inner(),
    )
}

#[tauri::command]
async fn studio_bootstrap(
    window: WebviewWindow,
    lifecycle: State<'_, ShellLifecycleState>,
    resources: State<'_, character_presentation::CharacterPresentationState>,
    appearance: State<'_, character_appearance::CharacterAppearanceState>,
    state: State<'_, character_studio_window::CharacterStudioWindowState>,
) -> Result<Value, String> {
    character_studio_window::validate_studio_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(str::to_string)?
        .ok_or_else(|| "STUDIO_CORE_UNAVAILABLE".to_string())?;
    state.bind_generation(&generation_id)?;
    let presentation = load_current_character_presentation(&lifecycle, &resources)?;
    let shell_appearance = appearance.current(&presentation.presentation)?;
    let page_background = shell_appearance
        .values
        .theme_tokens
        .get("pageBackground")
        .ok_or_else(|| "APPEARANCE_THEME_INVALID".to_string())?;
    product_shell::set_settings_window_theme_background(&window, page_background)?;
    let initial_character_id = state.initial_character_id()?;
    let response = dispatch_settings_request(
        handle,
        None,
        "studio.bootstrap",
        json!({"initialCharacterId": initial_character_id}),
        std::time::Duration::from_secs(15),
    )
    .await?;
    let mut payload = settings_response_payload(response)?;
    validate_studio_payload(&payload)?;
    payload["shellThemeTokens"] = serde_json::to_value(shell_appearance.values.theme_tokens)
        .map_err(|error| format!("STUDIO_THEME_SERIALIZE_FAILED: {error}"))?;
    Ok(payload)
}

#[tauri::command]
async fn studio_request(
    window: WebviewWindow,
    method: String,
    params: Value,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, audio::AudioState>,
    state: State<'_, character_studio_window::CharacterStudioWindowState>,
) -> Result<Value, String> {
    character_studio_window::validate_studio_window(&window)?;
    if !params.is_object() {
        return Err("STUDIO_REQUEST_INVALID".to_string());
    }
    let name = studio_method_name(method.trim())?;
    if name == "studio.bootstrap" {
        return Err("STUDIO_COMMAND_UNKNOWN".to_string());
    }
    let publish_target_character_id = if name == "studio.character.publish" {
        params
            .pointer("/doc/id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
    } else {
        None
    };
    let handle = settings_core_handle(&lifecycle)?;
    let (previous_generation_id, previous_generation_number) = handle
        .available_generation_identity()
        .map_err(str::to_string)?
        .ok_or_else(|| "STUDIO_CORE_UNAVAILABLE".to_string())?;
    state.bind_generation(&previous_generation_id)?;
    let deadline = if matches!(
        name,
        "studio.character.publish" | "studio.asset.import" | "studio.archive.export"
    ) {
        std::time::Duration::from_secs(30 * 60)
    } else {
        std::time::Duration::from_secs(30)
    };
    let response = dispatch_settings_request(handle.clone(), None, name, params, deadline).await?;
    if name == "studio.character.publish"
        && response
            .pointer("/error/details/generationInvalidated")
            .and_then(Value::as_bool)
            == Some(true)
    {
        let target_character_id =
            publish_target_character_id.ok_or_else(|| "STUDIO_RESPONSE_INVALID".to_string())?;
        handle
            .restart()
            .map_err(|error| format!("STUDIO_PUBLISH_RECOVERY_RESTART_FAILED: {error}"))?;
        audio_state.shutdown();
        state.bind_generation("")?;
        observe_studio_character_restart(
            app_handle.clone(),
            handle.clone(),
            previous_generation_id.clone(),
            previous_generation_number,
            target_character_id.clone(),
        );
        observe_character_restart(
            app_handle,
            handle,
            previous_generation_id,
            previous_generation_number,
            target_character_id,
        );
        return settings_response_payload(response);
    }
    let mut payload = settings_response_payload(response)?;

    if name == "studio.reference.preview" {
        let object = payload
            .as_object_mut()
            .ok_or_else(|| "STUDIO_PREVIEW_RESPONSE_INVALID".to_string())?;
        let source_path = object
            .remove("sourcePath")
            .and_then(|value| value.as_str().map(ToOwned::to_owned))
            .ok_or_else(|| "STUDIO_PREVIEW_RESPONSE_INVALID".to_string())?;
        let media_type = object
            .get("mediaType")
            .and_then(Value::as_str)
            .ok_or_else(|| "STUDIO_PREVIEW_RESPONSE_INVALID".to_string())?;
        let byte_length = object
            .get("byteLength")
            .and_then(Value::as_u64)
            .ok_or_else(|| "STUDIO_PREVIEW_RESPONSE_INVALID".to_string())?;
        let registration = state.register_preview(
            std::path::Path::new(&source_path),
            media_type,
            byte_length,
            &previous_generation_id,
        )?;
        payload = serde_json::to_value(registration)
            .map_err(|_| "STUDIO_PREVIEW_RESPONSE_INVALID".to_string())?;
        if let Some(object) = payload.as_object_mut() {
            object.insert("schemaVersion".to_string(), json!(1));
        }
    }
    validate_studio_payload(&payload)?;

    if name == "studio.character.publish" {
        if payload.get("changePlan").and_then(Value::as_str) == Some("core_restart_required") {
            let target_character_id = payload
                .get("savedCharacterId")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| "STUDIO_RESPONSE_INVALID".to_string())?
                .to_string();
            let restart = handle.restart();
            if restart.is_ok() {
                audio_state.shutdown();
                state.bind_generation("")?;
                observe_studio_character_restart(
                    app_handle.clone(),
                    handle.clone(),
                    previous_generation_id.clone(),
                    previous_generation_number,
                    target_character_id.clone(),
                );
                observe_character_restart(
                    app_handle,
                    handle,
                    previous_generation_id,
                    previous_generation_number,
                    target_character_id,
                );
                payload["runtimeReload"] = json!("requested");
            } else {
                payload["runtimeReload"] = json!("failed");
                payload["reloadError"] =
                    json!("保存成功，运行态重载失败。请重启 Sakura 后使用新角色数据。");
                let _ = app_handle.emit_to(
                    product_shell::SETTINGS_WINDOW_LABEL,
                    character_studio_window::CHARACTER_CATALOG_CHANGED_EVENT,
                    (),
                );
            }
        } else {
            payload["runtimeReload"] = json!("not_required");
            let _ = app_handle.emit_to(
                product_shell::SETTINGS_WINDOW_LABEL,
                character_studio_window::CHARACTER_CATALOG_CHANGED_EVENT,
                (),
            );
        }
    }
    Ok(payload)
}

#[tauri::command]
async fn studio_choose_source(
    window: WebviewWindow,
    kind: String,
    multiple: bool,
) -> Result<Value, String> {
    character_studio_window::validate_studio_window(&window)?;
    let dialog = rfd::AsyncFileDialog::new().set_title("选择角色工坊资源");
    let selected = match kind.as_str() {
        "portrait" => {
            let dialog = dialog.add_filter("立绘图片", &["png", "jpg", "jpeg", "webp", "gif"]);
            if multiple {
                return Ok(json!(dialog
                    .pick_files()
                    .await
                    .unwrap_or_default()
                    .iter()
                    .map(|file| file.path().to_string_lossy().to_string())
                    .collect::<Vec<_>>()));
            }
            dialog.pick_file().await
        }
        "gptModel" => dialog.add_filter("GPT 模型", &["ckpt"]).pick_file().await,
        "sovitsModel" => dialog.add_filter("SoVITS 模型", &["pth"]).pick_file().await,
        "referenceAudio" => {
            let dialog = dialog.add_filter("参考语音", &["wav", "mp3", "ogg", "flac"]);
            if multiple {
                return Ok(json!(dialog
                    .pick_files()
                    .await
                    .unwrap_or_default()
                    .iter()
                    .map(|file| file.path().to_string_lossy().to_string())
                    .collect::<Vec<_>>()));
            }
            dialog.pick_file().await
        }
        "portraitFolder" | "referenceAudioFolder" => dialog.pick_folder().await,
        _ => return Err("STUDIO_ASSET_KIND_INVALID".to_string()),
    };
    Ok(selected
        .map(|file| json!(file.path().to_string_lossy().to_string()))
        .unwrap_or(Value::Null))
}

#[tauri::command]
async fn studio_choose_export(
    window: WebviewWindow,
    default_name: String,
) -> Result<Option<String>, String> {
    character_studio_window::validate_studio_window(&window)?;
    let safe_name = std::path::Path::new(&default_name)
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .unwrap_or("character.char");
    Ok(rfd::AsyncFileDialog::new()
        .set_title("导出 Sakura 角色包")
        .set_file_name(safe_name)
        .add_filter("Sakura 角色包", &["char"])
        .save_file()
        .await
        .map(|file| file.path().to_string_lossy().to_string()))
}

#[tauri::command]
async fn studio_pick_screen_color(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    state: State<'_, color_picker::ColorPickerState>,
) -> Result<Value, String> {
    character_studio_window::validate_studio_window(&window)?;
    let monitors = capture::monitor_descriptors()?;
    let (session, previous) = state.begin(&monitors)?;
    capture::close_windows(&app_handle, &previous);
    if let Err(error) = color_picker::show_overlays(&app_handle, &session, &monitors) {
        state.fail(&session.id, &error);
        capture::close_windows(&app_handle, &session.labels);
        return Err(error);
    }
    let result = tauri::async_runtime::spawn_blocking(move || {
        color_picker::wait_for_result(session.receiver)
    })
    .await
    .map_err(|_| "STUDIO_COLOR_ABORTED".to_string())?;
    if let Some(studio) =
        app_handle.get_webview_window(character_studio_window::STUDIO_WINDOW_LABEL)
    {
        let _ = studio.show();
        let _ = studio.set_focus();
    }
    match result {
        Ok(color) => Ok(json!({"color": color})),
        Err(code) if code == "STUDIO_COLOR_CANCELLED" => Ok(json!({"cancelled": true})),
        Err(code) => Err(code),
    }
}

#[tauri::command]
async fn studio_color_pick(
    window: WebviewWindow,
    payload: color_picker::ColorPickRequest,
    app_handle: tauri::AppHandle,
    state: State<'_, color_picker::ColorPickerState>,
) -> Result<(), String> {
    if !window.label().starts_with("studio-color-") {
        return Err("STUDIO_COLOR_WINDOW_REQUIRED".to_string());
    }
    let point = color_picker::logical_point(&window, payload.x, payload.y)?;
    let claim = state.claim(window.label(), &payload)?;
    capture::hide_windows(&app_handle, &claim.labels);
    let labels = claim.labels.clone();
    let monitor_id = claim.monitor_id;
    let result = tauri::async_runtime::spawn_blocking(move || {
        std::thread::sleep(std::time::Duration::from_millis(100));
        color_picker::capture_color(monitor_id, point.0, point.1)
    })
    .await
    .map_err(|_| "STUDIO_COLOR_CAPTURE_ABORTED".to_string())?;
    capture::close_windows(&app_handle, &labels);
    claim.complete(result);
    Ok(())
}

#[tauri::command]
fn studio_color_cancel(
    window: WebviewWindow,
    payload: color_picker::ColorCancelRequest,
    app_handle: tauri::AppHandle,
    state: State<'_, color_picker::ColorPickerState>,
) -> Result<(), String> {
    if !window.label().starts_with("studio-color-") {
        return Err("STUDIO_COLOR_WINDOW_REQUIRED".to_string());
    }
    if let Some(labels) = state.cancel(window.label(), &payload.session_id) {
        capture::close_windows(&app_handle, &labels);
        return Ok(());
    }
    Err("STUDIO_COLOR_SESSION_STALE".to_string())
}

#[tauri::command]
fn show_studio(window: WebviewWindow) -> Result<(), String> {
    character_studio_window::validate_studio_window(&window)?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn close_character_studio(
    window: WebviewWindow,
    state: State<'_, character_studio_window::CharacterStudioWindowState>,
) -> Result<(), String> {
    character_studio_window::validate_studio_window(&window)?;
    state.authorize_close()?;
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
fn close_character_studio_for_exit(
    window: WebviewWindow,
    app_handle: tauri::AppHandle,
    state: State<'_, character_studio_window::CharacterStudioWindowState>,
) -> Result<(), String> {
    character_studio_window::validate_studio_window(&window)?;
    state.mark_exiting();
    window.destroy().map_err(|error| error.to_string())?;
    if let Some(settings) = app_handle.get_webview_window(product_shell::SETTINGS_WINDOW_LABEL) {
        let _ = settings.show();
        let _ = settings.set_focus();
    }
    let exit_app = app_handle.clone();
    app_handle
        .run_on_main_thread(move || {
            let lifecycle = exit_app.state::<ShellLifecycleState>();
            if let Err(error) = request_app_exit(&exit_app, &lifecycle) {
                product_shell::emit_product_menu_error(&exit_app, error);
            }
        })
        .map_err(|error| error.to_string())
}

fn studio_preview_protocol_response(
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
            .expect("static studio preview response")
    };
    if request.method() != Method::GET || request.uri().query().is_some() {
        return fail(StatusCode::BAD_REQUEST, "STUDIO_PREVIEW_REQUEST_REJECTED");
    }
    let segments: Vec<_> = request.uri().path().trim_matches('/').split('/').collect();
    if segments.len() != 2
        || segments[0] != "v1"
        || segments[1].is_empty()
        || segments[1].contains('%')
    {
        return fail(StatusCode::BAD_REQUEST, "STUDIO_PREVIEW_REQUEST_REJECTED");
    }
    let lifecycle = context.app_handle().state::<ShellLifecycleState>();
    let current_generation = match lifecycle
        .handle
        .as_ref()
        .and_then(|handle| handle.available_generation_id().ok().flatten())
    {
        Some(value) => value,
        None => return fail(StatusCode::GONE, "STUDIO_PREVIEW_GENERATION_STALE"),
    };
    let state = context
        .app_handle()
        .state::<character_studio_window::CharacterStudioWindowState>();
    match state.load_preview(segments[1], &current_generation) {
        Ok(resource) => tauri::http::Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, resource.media_type)
            .header(header::CONTENT_LENGTH, resource.bytes.len().to_string())
            .header(header::CACHE_CONTROL, "no-store, max-age=0")
            .header("X-Content-Type-Options", "nosniff")
            .body(resource.bytes)
            .expect("validated studio preview response"),
        Err(code) => fail(
            if code.contains("STALE") {
                StatusCode::GONE
            } else if code.contains("NOT_FOUND") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            },
            &code,
        ),
    }
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
    match resources.load_resource(segments[1], segments[2], &current_generation) {
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
    let lifecycle = app.state::<ShellLifecycleState>();
    let session_ready = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "LIFECYCLE_COMMAND_UNAVAILABLE".to_string())?
        .character_presentation()
        .map_err(str::to_string)?
        .is_some();
    if !session_ready {
        if let Some(window) = app.get_webview_window("main") {
            window.hide().map_err(|error| error.to_string())?;
        }
        product_shell::sync_product_tray_visibility(app, false)?;
        return product_shell::show_or_focus_settings(app);
    }
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
        product_shell::ProductMenuAction::ToggleTopmost => {
            let window = app
                .get_webview_window("main")
                .ok_or_else(|| "PET_WINDOW_UNAVAILABLE".to_string())?;
            app.state::<product_shell::PetTopmostState>()
                .toggle(&window)
                .map(|_| ())
        }
        product_shell::ProductMenuAction::OpenHistory => history_window::show_or_focus(app),
        product_shell::ProductMenuAction::OpenRuntimeLog => runtime_log_window::show_or_focus(app),
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
    if let Some(studio) =
        app_handle.get_webview_window(character_studio_window::STUDIO_WINDOW_LABEL)
    {
        let state = app_handle.state::<character_studio_window::CharacterStudioWindowState>();
        if !state.begin_exit()? {
            return Ok(());
        }
        if let Err(error) = studio.emit(character_studio_window::STUDIO_EXIT_REQUESTED_EVENT, ()) {
            state.cancel_exit_request();
            return Err(error.to_string());
        }
        let timeout_app = app_handle.clone();
        let exit_timeout = std::thread::Builder::new()
            .name("studio-exit-timeout".to_string())
            .spawn(move || {
                std::thread::sleep(std::time::Duration::from_secs(5));
                let check_app = timeout_app.clone();
                let _ = timeout_app.run_on_main_thread(move || {
                    let Some(studio) =
                        check_app.get_webview_window(character_studio_window::STUDIO_WINDOW_LABEL)
                    else {
                        return;
                    };
                    let state =
                        check_app.state::<character_studio_window::CharacterStudioWindowState>();
                    state.mark_exiting();
                    let _ = studio.destroy();
                    if let Some(settings) =
                        check_app.get_webview_window(product_shell::SETTINGS_WINDOW_LABEL)
                    {
                        let _ = settings.show();
                        let _ = settings.set_focus();
                    }
                    let lifecycle = check_app.state::<ShellLifecycleState>();
                    if let Err(error) = request_app_exit(&check_app, &lifecycle) {
                        product_shell::emit_product_menu_error(&check_app, error);
                    }
                });
            });
        if let Err(error) = exit_timeout {
            state.cancel_exit_request();
            return Err(format!("failed to start bounded Studio exit wait: {error}"));
        }
        return Ok(());
    }
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

fn standard_user_root(development: bool) -> Result<std::path::PathBuf, String> {
    let product = if development {
        "Sakura Development"
    } else {
        "Sakura"
    };
    #[cfg(target_os = "windows")]
    {
        if !development {
            return std::env::current_exe()
                .map_err(|error| format!("USER_ROOT_UNAVAILABLE: {error}"))?
                .parent()
                .map(ToOwned::to_owned)
                .ok_or_else(|| "USER_ROOT_UNAVAILABLE".to_string());
        }
        let base = std::env::var_os("LOCALAPPDATA")
            .map(std::path::PathBuf::from)
            .ok_or_else(|| "USER_ROOT_UNAVAILABLE".to_string())?;
        return Ok(base.join(product));
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .ok_or_else(|| "USER_ROOT_UNAVAILABLE".to_string())?;
        return Ok(home.join("Library/Application Support").join(product));
    }
    #[cfg(target_os = "linux")]
    {
        let base = std::env::var_os("XDG_DATA_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(std::path::PathBuf::from)
                    .map(|home| home.join(".local/share"))
            })
            .ok_or_else(|| "USER_ROOT_UNAVAILABLE".to_string())?;
        return Ok(base.join(product));
    }
}

fn ensure_user_layout(root: &std::path::Path) -> Result<std::path::PathBuf, String> {
    for relative in ["config", "data", "characters", "plugins/user", "tts"] {
        std::fs::create_dir_all(root.join(relative))
            .map_err(|error| format!("USER_ROOT_UNAVAILABLE: {error}"))?;
    }
    root.canonicalize()
        .map_err(|error| format!("USER_ROOT_UNAVAILABLE: {error}"))
}

fn runtime_request() -> Result<platform::RuntimeLocationRequest, String> {
    let executable_directory = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(ToOwned::to_owned))
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    #[cfg(not(debug_assertions))]
    {
        #[cfg(target_os = "macos")]
        let resource_directory = executable_directory
            .parent()
            .map(|contents| contents.join("Resources"))
            .ok_or_else(|| "DISTRIBUTION_ROOT_UNAVAILABLE".to_string())?;
        #[cfg(not(target_os = "macos"))]
        let resource_directory = executable_directory.clone();
        let user_root = ensure_user_layout(&standard_user_root(false)?)?;
        return Ok(platform::RuntimeLocationRequest {
            mode: platform::RuntimeMode::Packaged,
            target: platform::current_platform_target()
                .ok_or_else(|| "PLATFORM_UNSUPPORTED".to_string())?,
            executable_directory,
            resource_directory,
            explicit_development_root: None,
            user_root,
        });
    }
    #[cfg(debug_assertions)]
    {
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
        let configured_user_root = std::env::var_os("SAKURA_RUNTIME_USER_ROOT")
            .map(std::path::PathBuf::from)
            .unwrap_or(standard_user_root(true)?);
        let user_root = ensure_user_layout(&configured_user_root)?;
        Ok(platform::RuntimeLocationRequest {
            mode: platform::RuntimeMode::ExplicitDevelopment,
            target: platform::current_platform_target()
                .expect("Runtime v2 Shell requires a formal target"),
            executable_directory,
            resource_directory: repository_root.clone(),
            explicit_development_root: Some(repository_root.clone()),
            user_root,
        })
    }
}

fn character_appearance_state(
    repository: ui_config::UiConfigRepository,
) -> character_appearance::CharacterAppearanceState {
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
        || !root.join("config/system_config.yaml").is_file()
        || !root.join("config/api.yaml").is_file()
        || !root.join("config/characters.yaml").is_file()
    {
        return Err("WP_4_01_MANUAL_ROOT_INVALID".to_string());
    }
    Ok(root)
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

    #[cfg(not(debug_assertions))]
    if std::env::var_os("SAKURA_WP_4_01_MANUAL_ROOT").is_some() {
        show_startup_message(
            "Sakura WP-4-01 验收启动失败",
            "release 构建不接受验收根覆盖。",
            true,
        );
        std::process::exit(2);
    }

    let instance_lock_backend = NativeInstanceLockBackend;
    let _instance_guard = match instance_lock_backend.acquire(SHARED_INSTANCE_ID) {
        Ok(InstanceLockAcquire::Acquired(guard)) => guard,
        Ok(InstanceLockAcquire::AlreadyRunning) => {
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
        SETTINGS_TOOLS_SCRIPT.len(),
        SETTINGS_SCREEN_AWARENESS_SCRIPT.len(),
        SETTINGS_AUTOSTART_SCRIPT.len(),
    );

    let runtime_request = runtime_request().unwrap_or_else(|error| {
        show_startup_message("Sakura 启动失败", &error, true);
        std::process::exit(1);
    });
    #[cfg(debug_assertions)]
    let mut runtime_request = runtime_request;
    #[cfg(debug_assertions)]
    if let Some(root) = std::env::var_os(WP_4_01_MANUAL_ROOT_ENV) {
        runtime_request.user_root = wp_4_01_manual_root(root.into())
            .expect("WP-4-01 manual acceptance root must be isolated and complete");
    }
    let character_resource_root = runtime_request.user_root.clone();
    let runtime_log =
        RuntimeLogService::start(character_resource_root.join("data/logs/sakura-runtime.log"));
    let mut runtime_log_shutdown = RuntimeLogShutdown::new(runtime_log.clone());
    let ui_config_repository =
        ui_config::UiConfigRepository::new(character_resource_root.join("config/ui.json"));
    let telemetry = telemetry::TelemetryService::initialize(
        ui_config_repository.clone(),
        runtime_log.run_id().to_string(),
    );
    runtime_log.attach_telemetry(telemetry.clone());
    telemetry.submit_app_started();
    install_runtime_panic_hook(runtime_log.clone());
    interaction_latency::initialize(runtime_log.clone());
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(
            Severity::Info,
            "shell",
            "shell.started",
            "Runtime shell started",
        )
        .attributes(json!({
            "current_version": env!("CARGO_PKG_VERSION"),
        })),
    );
    match legacy_import::recover_interrupted(&runtime_request) {
        Ok(true) => {
            let _ = runtime_log.submit(RuntimeLogEvent::rust(
                Severity::Warning,
                "legacy_import",
                "legacy_import.recovery.completed",
                "上次中断的旧版本迁移已回滚",
            ));
        }
        Ok(false) => {}
        Err(error) => {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Error,
                    "legacy_import",
                    "legacy_import.recovery.failed",
                    "旧版本迁移恢复失败",
                )
                .attributes(json!({
                    "code": stable_runtime_code(&error, "LEGACY_IMPORT_RECOVERY_FAILED"),
                    "diagnostic": error,
                    "error_type": "LegacyImportRecoveryError",
                    "reason_code": "LEGACY_IMPORT_RECOVERY_FAILED",
                    "stage": "recovery"
                })),
            );
            telemetry.shutdown();
            runtime_log_shutdown.finish();
            show_startup_message(
                "Sakura 迁移恢复失败",
                "上次迁移未能安全回滚。请查看 data/logs/sakura-runtime.log。Sakura 未继续启动。",
                true,
            );
            std::process::exit(1);
        }
    }
    let first_run_guide_state =
        product_shell::FirstRunGuideState::new(ui_config_repository.clone());
    let first_run_completed = match first_run_guide_state.snapshot() {
        Ok(snapshot) => {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Info,
                    "first_run",
                    "first_run.state.loaded",
                    "首次配置状态已读取",
                )
                .attributes(
                    json!({"status": if snapshot.completed { "completed" } else { "pending" }}),
                ),
            );
            snapshot.completed
        }
        Err(error) => {
            let _ = runtime_log.submit(
                RuntimeLogEvent::rust(
                    Severity::Error,
                    "first_run",
                    "first_run.state.failed",
                    "首次配置状态读取失败",
                )
                .attributes(json!({
                    "code": stable_runtime_code(&error, "FIRST_RUN_STATE_FAILED"),
                    "diagnostic": error,
                    "error_type": "FirstRunStateError",
                    "reason_code": "FIRST_RUN_STATE_FAILED",
                    "stage": "state_load"
                })),
            );
            telemetry.shutdown();
            runtime_log_shutdown.finish();
            show_startup_message(
                "Sakura 启动失败",
                "首次配置状态无法读取。请查看 data/logs/sakura-runtime.log。",
                true,
            );
            std::process::exit(1);
        }
    };
    let legacy_import_state = Arc::new(legacy_import::LegacyImportState::new(
        runtime_request.clone(),
    ));
    let mut shell_lifecycle_session = Some(if first_run_completed {
        shell_lifecycle::ShellLifecycleSession::start_observed(runtime_request, runtime_log.clone())
    } else {
        shell_lifecycle::ShellLifecycleSession::start_paused_observed(
            runtime_request,
            runtime_log.clone(),
        )
    });
    let shell_lifecycle_handle = shell_lifecycle_session
        .as_ref()
        .map(shell_lifecycle::ShellLifecycleSession::handle);
    let update_coordinator = update_settings::UpdateCoordinator::new(ui_config_repository.clone());
    let setup_runtime_log = runtime_log.clone();
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name("Sakura")
                .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Mutex::new(WindowGeometrySession::default()))
        .manage(product_shell::ProductShellState::default())
        .manage(character_studio_window::CharacterStudioWindowState::default())
        .manage(color_picker::ColorPickerState::default())
        .manage(first_run_guide_state)
        .manage(legacy_import_state)
        .manage(product_shell::PetTopmostState::new(
            ui_config_repository.clone(),
        ))
        .manage(runtime_log.clone())
        .manage(telemetry.clone())
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
        .manage(chat_settings::BubbleAutoHideState::new(
            ui_config_repository.clone(),
            character_resource_root.join("config/system_config.yaml"),
        ))
        .manage(chat_settings::SubtitleLanguageState::new(
            ui_config_repository,
        ))
        .manage(update_coordinator)
        .manage(audio::AudioState::new(character_resource_root.clone()))
        .manage(Arc::new(capture::CaptureManager::new()))
        .manage(input_visual_effect::InputVisualEffectState::from_environment(runtime_log.clone()))
        .register_uri_scheme_protocol(
            character_presentation::CHARACTER_PROTOCOL,
            character_protocol_response,
        )
        .register_uri_scheme_protocol(
            character_studio_window::STUDIO_PREVIEW_PROTOCOL,
            studio_preview_protocol_response,
        )
        .setup(move |app| {
            let window = app
                .get_webview_window("main")
                .ok_or("main pet window was not created")?;
            prepare_initial_pet_window(&window)?;
            let topmost = app.state::<product_shell::PetTopmostState>();
            if let Err(error) = topmost.initialize(&window) {
                let lifecycle = app.state::<ShellLifecycleState>();
                append_runtime_diagnostic_event(
                    &lifecycle.runtime_log,
                    "pet_topmost",
                    "pet_topmost_initialize_failed",
                    json!({
                        "stage": "window_initialize",
                        "outcome": "failed",
                        "code": error,
                    }),
                );
            }
            let glass = app.state::<input_visual_effect::InputVisualEffectState>();
            glass.install(&window);
            let pet_visible = window.is_visible().map_err(|error| error.to_string())?;
            product_shell::install_product_tray(app, pet_visible)?;
            if !first_run_completed {
                let _ = setup_runtime_log.submit(RuntimeLogEvent::rust(
                    Severity::Info,
                    "first_run",
                    "first_run.onboarding.opened",
                    "首次启动欢迎页已打开",
                ));
                // The paused first-run lifecycle cannot reach the pet WebView's
                // reveal command because that page waits for Core. Queue the
                // settings WebView after setup returns to the event loop.
                dispatch_webview_product_menu_action(
                    app.handle().clone(),
                    product_shell::ProductMenuAction::OpenSettings,
                )?;
            }
            Ok(())
        })
        .on_menu_event(|app, event| {
            let Some(action) = product_shell::ProductMenuAction::from_id(event.id().as_ref())
            else {
                return;
            };
            // Windows keeps the native tray menu inside its modal message loop while this
            // callback runs. Creating a WebView synchronously here can leave its child HWND
            // permanently disabled, so queue every tray action for the next event-loop turn.
            if let Err(error) = dispatch_webview_product_menu_action(app.clone(), action) {
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
            if window.label() == character_studio_window::STUDIO_WINDOW_LABEL {
                let state = window.state::<character_studio_window::CharacterStudioWindowState>();
                match event {
                    tauri::WindowEvent::CloseRequested { api, .. } => {
                        if !state.consume_close_authorization().unwrap_or(false) {
                            api.prevent_close();
                            let _ = window
                                .emit(character_studio_window::STUDIO_CLOSE_REQUESTED_EVENT, ());
                        }
                    }
                    tauri::WindowEvent::Destroyed => {
                        let topmost = window.state::<product_shell::PetTopmostState>();
                        if let Err(error) = character_studio_window::restore_after_destroyed(
                            window.app_handle(),
                            state.inner(),
                            topmost.inner(),
                        ) {
                            eprintln!("failed to restore windows after Studio closed: {error}");
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
                    #[cfg(target_os = "macos")]
                    if let Some(main_window) = window.app_handle().get_webview_window("main") {
                        let _ = macos_surface_snapshot::clear(&main_window);
                    }
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
                    let _ = emit_settings_appearance_active(window.app_handle(), false);
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
            pet_surface_visibility_capabilities,
            set_pet_input_surface_presented,
            current_pet_surface_diagnostics,
            apply_pet_layout,
            start_pet_input_expansion,
            start_pet_input_transition,
            start_pet_bubble_transition,
            reveal_pet_window,
            start_pet_drag,
            open_pet_context_menu,
            set_pet_context_menu_surface,
            close_pet_context_menu,
            set_pet_tool_dock_surface,
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
            remove_screen_attachment_item,
            capture_screen_awareness_frame,
            attach_screen_awareness_batch,
            clear_screen_awareness_batch,
            composer_tools_get,
            composer_tool_invoke,
            tts_prepare_segment,
            tts_cancel_synthesis,
            tts_play_prepared,
            tts_stop_playback,
            settings_voice_get,
            settings_voice_status_get,
            settings_voice_save,
            current_chat_presentation_timing,
            current_bubble_auto_hide,
            current_subtitle_language,
            history_bootstrap,
            history_page,
            close_history_window,
            reveal_history_window,
            runtime_log_viewer_bootstrap,
            runtime_log_viewer_snapshot,
            close_runtime_log_viewer,
            reveal_runtime_log_viewer,
            current_character_presentation,
            current_character_appearance,
            apply_input_visual_effect,
            begin_control_surface_preview,
            preview_pet_control_surface,
            end_control_surface_preview,
            begin_portrait_scale_preview,
            prepare_portrait_transition,
            activate_portrait_hit_test,
            commit_portrait_transition,
            settle_portrait_scale_surface,
            prepare_portrait_scale_preview_snapshot,
            finish_portrait_scale_preview_snapshot,
            interaction_latency_diagnostics_enabled,
            input_visual_effect_status,
            record_interaction_latency_trace,
            record_runtime_diagnostics,
            retry_core,
            first_run_start_core,
            exit_runtime,
            product_shell::settings_capability_manifest,
            product_shell::first_run_guide_get,
            product_shell::first_run_guide_complete,
            legacy_import::legacy_import_choose_source,
            legacy_import::legacy_import_inspect,
            legacy_import::legacy_import_state,
            legacy_import::legacy_import_start,
            legacy_import::legacy_import_cancel,
            legacy_import::settings_legacy_data_import_choose,
            legacy_import::settings_legacy_data_import_apply,
            product_shell::reveal_settings_window,
            settings_characters_get,
            settings_character_choose_import,
            settings_character_choose_export,
            settings_character_import,
            settings_character_select,
            open_character_studio,
            studio_bootstrap,
            studio_request,
            studio_choose_source,
            studio_choose_export,
            studio_pick_screen_color,
            studio_color_pick,
            studio_color_cancel,
            show_studio,
            close_character_studio,
            close_character_studio_for_exit,
            settings_storage_get,
            settings_storage_open_user_root,
            settings_storage_choose_tts_root,
            settings_storage_reset_tts_root,
            settings_update_get,
            settings_update_cached_get,
            settings_update_preferences_get,
            settings_update_preferences_set,
            settings_autostart_get,
            settings_autostart_save,
            startup_update_check,
            chat_update_announce,
            settings_update_install,
            settings_update_open_portable_download,
            settings_about_get,
            settings_about_open_website,
            settings_about_open_repository,
            settings_about_open_changelog,
            settings_about_open_sponsor,
            settings_telemetry_get,
            settings_telemetry_set_enabled,
            settings_telemetry_regenerate_installation_id,
            settings_telemetry_open_documentation,
            settings_character_appearance_get,
            settings_character_visual_preview,
            settings_character_appearance_preview,
            settings_character_appearance_scale_gesture,
            settings_character_appearance_scale_frame,
            settings_character_appearance_layout_gesture,
            settings_character_appearance_layout_frame,
            settings_character_appearance_save,
            settings_character_appearance_cancel_preview,
            settings_chat_presentation_timing_get,
            settings_chat_presentation_timing_save,
            settings_bubble_auto_hide_get,
            settings_bubble_auto_hide_save,
            settings_provider_model_get,
            settings_provider_model_save,
            settings_provider_model_probe,
            settings_provider_model_cancel,
            settings_tools_get,
            settings_tools_save,
            settings_screen_awareness_get,
            settings_screen_awareness_save,
            settings_plugins_get,
            settings_plugins_save,
            settings_plugins_enabled_set,
            settings_plugins_action,
            settings_plugins_install,
            settings_plugins_uninstall,
            settings_plugins_collection,
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
    telemetry.submit_app_ready();

    let exit_code = app.run_return(move |app_handle, event| match event {
        tauri::RunEvent::Exit => {
            app_handle
                .state::<character_studio_window::CharacterStudioWindowState>()
                .mark_exiting();
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
    telemetry.shutdown();
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
    fn character_settings_snapshot_accepts_empty_state_and_requires_selected_membership() {
        assert!(validate_character_settings_snapshot(&json!({
            "schemaVersion": 1,
            "revision": 0,
            "currentCharacterId": null,
            "characters": [],
        }))
        .is_ok());
        assert!(validate_character_settings_snapshot(&json!({
            "schemaVersion": 1,
            "revision": 2,
            "currentCharacterId": "navi",
            "characters": [{"id": "navi", "displayName": "N.A.V.I.", "hasVoice": false}],
        }))
        .is_ok());
        assert_eq!(
            validate_character_settings_snapshot(&json!({
                "schemaVersion": 1,
                "revision": 2,
                "currentCharacterId": "missing",
                "characters": [{"id": "navi", "displayName": "N.A.V.I.", "hasVoice": false}],
            }))
            .unwrap_err(),
            "CHARACTER_SETTINGS_RESPONSE_INVALID"
        );
    }

    #[test]
    fn wp_5_03_character_settings_change_requires_exact_restart_plan_and_snapshot() {
        let snapshot = json!({
            "schemaVersion": 1,
            "revision": 2,
            "currentCharacterId": "navi",
            "characters": [{"id": "navi", "displayName": "N.A.V.I.", "hasVoice": false}],
        });
        let (validated, plan) = validate_character_settings_change(json!({
            "schemaVersion": 1,
            "snapshot": snapshot.clone(),
            "changePlan": "core_restart_required",
        }))
        .unwrap();
        assert_eq!(validated, snapshot);
        assert_eq!(plan, "core_restart_required");

        assert_eq!(
            validate_character_settings_change(json!({
                "schemaVersion": 1,
                "snapshot": snapshot,
                "changePlan": "hot_apply",
            })),
            Err("CHARACTER_SETTINGS_CHANGE_INVALID".to_string())
        );

        let empty_snapshot = json!({
            "schemaVersion": 1,
            "revision": 0,
            "currentCharacterId": null,
            "characters": [],
        });
        assert_eq!(
            character_restart_target(&empty_snapshot, "core_restart_required"),
            Err("CHARACTER_SETTINGS_CHANGE_INVALID".to_string())
        );
        assert_eq!(
            character_restart_target(&empty_snapshot, "unchanged"),
            Ok(None)
        );
    }

    #[test]
    fn storage_settings_snapshot_accepts_consistent_default_and_custom_states() {
        let default_root = if cfg!(windows) {
            "C:\\Sakura"
        } else {
            "/tmp/Sakura"
        };
        let default_tts = if cfg!(windows) {
            "C:\\Sakura\\tts"
        } else {
            "/tmp/Sakura/tts"
        };
        assert!(validate_storage_settings_snapshot(&json!({
            "schemaVersion": 1,
            "userRoot": default_root,
            "ttsRoot": default_tts,
            "ttsRootSource": "default",
            "ttsRootAvailable": true,
            "reasonCode": null,
        }))
        .is_ok());
        assert!(validate_storage_settings_snapshot(&json!({
            "schemaVersion": 1,
            "userRoot": default_root,
            "ttsRoot": if cfg!(windows) { "D:\\Voice" } else { "/Volumes/Voice" },
            "ttsRootSource": "custom",
            "ttsRootAvailable": false,
            "reasonCode": "TTS_ROOT_MISSING",
        }))
        .is_ok());
    }

    #[test]
    fn storage_settings_snapshot_rejects_reason_availability_contradictions() {
        let base = json!({
            "schemaVersion": 1,
            "userRoot": if cfg!(windows) { "C:\\Sakura" } else { "/tmp/Sakura" },
            "ttsRoot": if cfg!(windows) { "D:\\Voice" } else { "/Volumes/Voice" },
            "ttsRootSource": "custom",
            "ttsRootAvailable": true,
            "reasonCode": null,
        });
        let mut unavailable_without_reason = base.clone();
        unavailable_without_reason["ttsRootAvailable"] = json!(false);
        assert_eq!(
            validate_storage_settings_snapshot(&unavailable_without_reason).unwrap_err(),
            "STORAGE_SETTINGS_RESPONSE_INVALID"
        );
        let mut available_with_reason = base;
        available_with_reason["reasonCode"] = json!("TTS_ROOT_NOT_WRITABLE");
        assert_eq!(
            validate_storage_settings_snapshot(&available_with_reason).unwrap_err(),
            "STORAGE_SETTINGS_RESPONSE_INVALID"
        );
    }

    #[test]
    fn plugin_kernel_v3_tts_cancel_accepts_only_operation_identity() {
        let request: TtsCancelSynthesisRequest =
            serde_json::from_value(json!({"operationId": "operation-1"})).unwrap();
        assert_eq!(request.operation_id, "operation-1");
        assert!(serde_json::from_value::<TtsCancelSynthesisRequest>(json!({
            "requestId": "tts-private-job"
        }))
        .is_err());
        assert!(serde_json::from_value::<TtsCancelSynthesisRequest>(json!({
            "operationId": "operation-1",
            "requestId": "tts-private-job"
        }))
        .is_err());
    }

    #[test]
    fn composer_tool_bridge_accepts_only_bounded_host_rendered_descriptors() {
        assert!(valid_composer_tool_id("com.example.tools:browser"));
        assert!(!valid_composer_tool_id("../private"));
        let valid = json!({
            "schemaVersion": 1,
            "coreGenerationId": "generation-a",
            "tools": [{
                "id": "com.example.tools:browser",
                "pluginId": "com.example.tools",
                "toolId": "browser",
                "label": "浏览器",
                "description": "打开受控浏览器",
                "icon": "globe",
                "order": 20.0,
            }],
        });
        assert!(validate_composer_tools_snapshot(&valid, "generation-a").is_ok());
        let mut invalid = valid;
        invalid["tools"][0]["icon"] = json!("<svg>");
        assert_eq!(
            validate_composer_tools_snapshot(&invalid, "generation-a").unwrap_err(),
            "COMPOSER_TOOLS_RESPONSE_INVALID"
        );
    }

    #[test]
    fn composer_tool_dock_uses_the_resident_surface_without_mutating_window_placement() {
        let contract = layout_contract().unwrap();
        assert_eq!(composer_resident_viewport(&contract), [900, 1_490]);
        assert_eq!(composer_tool_dock_reserved_bottom(&contract, None), 986);
        let lowered_surface = ControlSurfaceLayout {
            bubble_rect: [20, 880, 860, 128],
            input_rect: [20, 1_218, 860, 152],
            controls_rect: [840, 890, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        assert_eq!(
            composer_tool_dock_reserved_bottom(&contract, Some(&lowered_surface)),
            1_486
        );
        let mut hidden_input = lowered_surface.clone();
        hidden_input.input_visible = false;
        assert_eq!(
            composer_tool_dock_reserved_bottom(&contract, Some(&hidden_input)),
            0
        );
        assert_eq!(
            composer_tool_dock_reserve_rect(&contract, Some(&hidden_input)).unwrap(),
            None
        );
        let mut application = LayoutApplication::rejected(1, PresentationState::Product, 5);
        application.scale_factor = 1.0;
        application.content_scale = 1.0;
        application.active_bounds = [0, 0, 900, 1_112];
        application.physical_placement = window_geometry::PhysicalPlacement {
            x: 1_000,
            y: 500,
            width: 900,
            height: 1_112,
        };
        let placement = application.physical_placement;
        let base = window_interaction::PhysicalHitRegions {
            state: PresentationState::Product,
            scale: 1.0,
            envelope: [900, 1_112],
            interactive: Vec::new(),
            drag: Vec::new(),
            neutral: Vec::new(),
            portrait_alpha_mask: None,
            extra_native_rectangles: Vec::new(),
        };
        let opened =
            composer_tool_dock_hit_regions(&contract, &application, &base, [130, 882, 216, 104])
                .unwrap();
        assert_eq!(opened.interactive.len(), 1);
        assert_eq!(opened.interactive[0].x, 130);
        assert_eq!(opened.interactive[0].y, 882);
        assert_eq!(opened.interactive[0].corner_radius, 16);
        assert_eq!(application.physical_placement, placement);
        assert_eq!(
            window_interaction::expand_surface_bounds_for_overlay(
                application.active_bounds,
                [680, 900, 200, 80],
                composer_resident_viewport(&contract),
            )
            .unwrap(),
            application.active_bounds,
        );
        assert!(composer_tool_dock_hit_regions(
            &contract,
            &application,
            &base,
            [130, 882, 216, 105],
        )
        .is_err());
    }

    #[test]
    fn current_control_surface_and_tool_dock_define_fit_bounds() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: None,
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 2_560,
                height: 1_392,
            },
            scale_factor: 1.25,
        };
        let default = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            None,
            None,
            false,
            false,
        )
        .unwrap();
        assert_eq!(
            default.visible_fit_bounds[1] + default.visible_fit_bounds[3],
            986
        );

        let lowered_surface = ControlSurfaceLayout {
            bubble_rect: [20, 880, 860, 128],
            input_rect: [20, 1_218, 860, 152],
            controls_rect: [840, 890, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let lowered = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(default.portrait_anchor),
            AnchorPolicy::Automatic,
            100,
            Some(&lowered_surface),
            None,
            false,
            false,
        )
        .unwrap();
        assert_eq!(
            lowered.visible_fit_bounds[1] + lowered.visible_fit_bounds[3],
            1_486
        );
        assert!(
            lowered.active_bounds[1] + lowered.active_bounds[3]
                >= lowered.visible_fit_bounds[1] + lowered.visible_fit_bounds[3]
        );
        assert!(lowered.portrait_anchor.y < default.portrait_anchor.y);
    }

    #[test]
    fn automatic_bubble_expansion_reserves_a_maximum_surface_only_on_windows() {
        assert!(!uses_bubble_expansion_stable_surface_bounds(false));
        assert_eq!(
            uses_bubble_expansion_stable_surface_bounds(true),
            cfg!(windows)
        );
    }

    #[test]
    fn portrait_scale_settlement_keeps_the_existing_physical_anchor_at_screen_edges() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: None,
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_080,
            },
            scale_factor: 1.0,
        };
        let initial = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            None,
            None,
            true,
            false,
        )
        .unwrap();
        let automatically_refitted = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(initial.portrait_anchor),
            AnchorPolicy::Automatic,
            150,
            None,
            None,
            false,
            false,
        )
        .unwrap();
        let settled = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(initial.portrait_anchor),
            AnchorPolicy::UserPositioned,
            150,
            None,
            None,
            false,
            false,
        )
        .unwrap();

        assert!(preserves_portrait_anchor_for_scale_settlement(true, false));
        assert!(!preserves_portrait_anchor_for_scale_settlement(true, true));
        assert_ne!(
            automatically_refitted.portrait_anchor,
            initial.portrait_anchor
        );
        assert_eq!(settled.portrait_anchor, initial.portrait_anchor);
    }

    #[test]
    fn message_expansion_keeps_the_native_window_and_input_anchor_stable() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: None,
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_080,
            },
            scale_factor: 1.0,
        };
        let compact_surface = ControlSurfaceLayout {
            bubble_rect: [130, 686, 640, 122],
            input_rect: [130, 818, 640, 52],
            controls_rect: [730, 696, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let expanded_surface = ControlSurfaceLayout {
            bubble_rect: [130, 88, 640, 720],
            input_rect: [130, 818, 640, 52],
            controls_rect: [730, 98, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let compact = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            Some(&compact_surface),
            None,
            false,
            true,
        )
        .unwrap();
        let expanded = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            2,
            &monitor,
            Some(compact.portrait_anchor),
            AnchorPolicy::Automatic,
            100,
            Some(&expanded_surface),
            None,
            false,
            true,
        )
        .unwrap();

        assert!(same_surface_geometry(&compact, &expanded));
        assert_eq!(
            compact.physical_local_anchor,
            expanded.physical_local_anchor
        );
    }

    #[test]
    fn control_surface_preview_region_stays_coarse_and_follows_the_latest_frame() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: None,
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 2_560,
                height: 1_440,
            },
            scale_factor: 1.0,
        };
        let first = ControlSurfaceLayout {
            bubble_rect: [130, 600, 640, 128],
            input_rect: [130, 738, 640, 52],
            controls_rect: [730, 610, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let application = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            Some(&first),
            None,
            true,
            false,
        )
        .unwrap();
        let mask = character_presentation::PortraitAlphaMask::new(4, 4, vec![255; 16]);
        let first_regions = build_coarse_native_interaction_regions(
            &contract,
            &application,
            Some(&first),
            Some(&mask),
            100,
        )
        .unwrap();
        let second = ControlSurfaceLayout {
            bubble_rect: [70, 520, 760, 208],
            input_rect: [70, 738, 760, 52],
            controls_rect: [790, 530, 30, 30],
            ..first
        };
        let second_regions = build_coarse_native_interaction_regions(
            &contract,
            &application,
            Some(&second),
            Some(&mask),
            100,
        )
        .unwrap();
        let maximum_scale_regions = build_coarse_native_interaction_regions(
            &contract,
            &application,
            Some(&second),
            Some(&mask),
            window_interaction::PORTRAIT_SCALE_MAX_PERCENT,
        )
        .unwrap();

        assert!(first_regions.portrait_alpha_mask.is_none());
        assert!(second_regions.portrait_alpha_mask.is_none());
        assert!(maximum_scale_regions.portrait_alpha_mask.is_none());
        assert_ne!(first_regions.interactive, second_regions.interactive);
        assert_ne!(first_regions.drag, second_regions.drag);
        assert!(maximum_scale_regions.drag[0].width >= second_regions.drag[0].width);
        assert!(maximum_scale_regions.drag[0].height >= second_regions.drag[0].height);
        assert!(second_regions
            .interactive
            .iter()
            .chain(second_regions.drag.iter())
            .all(|rect| rect.width < second_regions.envelope[0]
                || rect.height < second_regions.envelope[1]));
    }

    #[test]
    fn window_surface_regression_windows_portrait_masks_change_only_the_exact_region_at_screen_edges(
    ) {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: Some("fixture-monitor".to_string()),
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_080,
            },
            scale_factor: 1.5,
        };
        let masks = [
            character_presentation::PortraitAlphaMask::new(
                5,
                5,
                vec![
                    255, 0, 0, 0, 0, //
                    255, 0, 0, 0, 0, //
                    255, 0, 0, 0, 0, //
                    255, 0, 0, 0, 0, //
                    255, 0, 0, 0, 0,
                ],
            ),
            character_presentation::PortraitAlphaMask::new(
                5,
                5,
                vec![
                    0, 0, 0, 0, 255, //
                    0, 0, 0, 0, 255, //
                    0, 0, 0, 0, 255, //
                    0, 0, 0, 0, 255, //
                    0, 0, 0, 0, 255,
                ],
            ),
            character_presentation::PortraitAlphaMask::new(
                5,
                5,
                vec![
                    255, 255, 255, 255, 0, //
                    255, 255, 255, 255, 0, //
                    255, 255, 255, 255, 0, //
                    255, 255, 255, 255, 0, //
                    255, 255, 255, 255, 0,
                ],
            ),
            character_presentation::PortraitAlphaMask::new(5, 5, vec![0; 25]),
        ];

        for portrait_scale_percent in [100, 150] {
            for anchor in [
                window_geometry::PhysicalPoint { x: 0, y: 1_079 },
                window_geometry::PhysicalPoint { x: 1_919, y: 1_079 },
            ] {
                let mut expected_application: Option<LayoutApplication> = None;
                let mut exact_regions = Vec::new();
                for mask in &masks {
                    let application = compute_pet_window_layout_with_surface_policy(
                        &contract,
                        PresentationState::Product,
                        42,
                        &monitor,
                        Some(anchor),
                        AnchorPolicy::UserPositioned,
                        portrait_scale_percent,
                        None,
                        Some(mask),
                        false,
                        true,
                        false,
                    )
                    .unwrap();
                    if let Some(expected) = expected_application.as_ref() {
                        assert!(same_surface_geometry(expected, &application));
                        assert_eq!(expected.visible_fit_bounds, application.visible_fit_bounds);
                        assert_eq!(
                            expected.physical_local_anchor,
                            application.physical_local_anchor
                        );
                        assert_eq!(expected.portrait_anchor, application.portrait_anchor);
                    } else {
                        expected_application = Some(application.clone());
                    }
                    let regions = build_native_interaction_regions(
                        &contract,
                        &application,
                        None,
                        Some(mask),
                        portrait_scale_percent,
                    )
                    .unwrap();
                    exact_regions.push(
                        window_interaction::native_hit_rectangles(
                            &regions,
                            [
                                application.physical_placement.width,
                                application.physical_placement.height,
                            ],
                        )
                        .unwrap(),
                    );
                }
                for pair in exact_regions.windows(2) {
                    assert_ne!(pair[0], pair[1]);
                }
            }
        }
    }

    #[test]
    fn window_surface_regression_windows_portrait_scale_changes_only_the_exact_region_at_automatic_anchor(
    ) {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: Some("fixture-monitor".to_string()),
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_080,
            },
            scale_factor: 1.5,
        };
        let mask = character_presentation::PortraitAlphaMask::new(
            5,
            5,
            vec![
                255, 255, 255, 255, 255, //
                255, 255, 255, 255, 255, //
                255, 255, 255, 255, 255, //
                255, 255, 255, 255, 255, //
                255, 255, 255, 255, 255,
            ],
        );
        let mut expected_application: Option<LayoutApplication> = None;
        let mut exact_regions = Vec::new();

        for portrait_scale_percent in [100, 150, 50] {
            let application = compute_pet_window_layout_with_surface_policy(
                &contract,
                PresentationState::Product,
                42,
                &monitor,
                None,
                AnchorPolicy::Automatic,
                portrait_scale_percent,
                None,
                Some(&mask),
                false,
                true,
                false,
            )
            .unwrap();
            if let Some(expected) = expected_application.as_ref() {
                assert_eq!(expected.physical_placement, application.physical_placement);
                assert_eq!(expected.portrait_anchor, application.portrait_anchor);
                assert_eq!(
                    expected.physical_local_anchor,
                    application.physical_local_anchor
                );
                assert_eq!(expected.active_bounds, application.active_bounds);
                assert_eq!(expected.content_scale, application.content_scale);
                assert_eq!(expected.visible_fit_bounds, application.visible_fit_bounds);
            } else {
                expected_application = Some(application.clone());
            }
            let regions = build_native_interaction_regions(
                &contract,
                &application,
                None,
                Some(&mask),
                portrait_scale_percent,
            )
            .unwrap();
            exact_regions.push(
                window_interaction::native_hit_rectangles(
                    &regions,
                    [
                        application.physical_placement.width,
                        application.physical_placement.height,
                    ],
                )
                .unwrap(),
            );
        }

        for pair in exact_regions.windows(2) {
            assert_ne!(pair[0], pair[1]);
        }
    }

    #[test]
    fn resident_portrait_application_reuse_requires_unchanged_layout_and_dpi() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: Some("fixture-monitor".to_string()),
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_080,
            },
            scale_factor: 1.5,
        };
        let application = compute_pet_window_layout_with_surface_policy(
            &contract,
            PresentationState::Product,
            42,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            None,
            None,
            false,
            true,
            false,
        )
        .unwrap();
        assert!(can_reuse_resident_portrait_application(
            true,
            &application,
            PresentationState::Product,
            42,
            &monitor,
        ));
        let different_dpi = MonitorDescriptor {
            scale_factor: 1.25,
            ..monitor
        };
        assert!(!can_reuse_resident_portrait_application(
            true,
            &application,
            PresentationState::Product,
            42,
            &different_dpi,
        ));
    }

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
        assert!(contents.contains("[TTS]"));
        assert!(contents.contains("code=AUDIO_DEVICE_UNAVAILABLE"));
        assert!(!contents.contains("not persisted"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn animated_input_resizes_prepare_one_transition_and_only_contractions_defer_the_region() {
        let surface = |height| ControlSurfaceLayout {
            bubble_rect: [130, 680, 640, 128],
            input_rect: [130, 818, 640, height],
            controls_rect: [730, 690, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let motion = Some(InputSurfaceTransition {
            duration_ms: 260,
            staging_height: None,
            delay_ms: 0,
        });
        assert!(is_animated_input_resize(
            &surface(52),
            &surface(100),
            motion,
        ));
        assert!(is_animated_input_resize(
            &surface(124),
            &surface(100),
            motion,
        ));
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
        let started = StartedInputExpansion {
            previous_height: 52,
            target_height: 124,
            transition: InputSurfaceTransition {
                duration_ms: 260,
                staging_height: Some(76),
                delay_ms: 40,
            },
        };
        assert!(matches_started_input_expansion(
            started,
            &surface(52),
            &surface(124),
            Some(InputSurfaceTransition {
                delay_ms: 0,
                ..started.transition
            }),
        ));
        assert!(!matches_started_input_expansion(
            started,
            &surface(52),
            &surface(148),
            Some(started.transition),
        ));
        assert!(!is_animated_input_contraction(
            &surface(124),
            &surface(100),
            Some(InputSurfaceTransition {
                duration_ms: 0,
                staging_height: None,
                delay_ms: 0,
            }),
        ));
    }

    #[test]
    fn animated_bubble_contraction_requires_a_fixed_bottom_edge_and_stable_input() {
        let surface = |top, height| ControlSurfaceLayout {
            bubble_rect: [130, top, 640, height],
            input_rect: [130, 818, 640, 52],
            controls_rect: [730, top + 10, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let motion = Some(InputSurfaceTransition {
            duration_ms: 240,
            staging_height: None,
            delay_ms: 0,
        });
        assert!(is_animated_bubble_contraction(
            &surface(632, 176),
            &surface(680, 128),
            motion,
        ));
        assert!(!is_animated_bubble_contraction(
            &surface(680, 128),
            &surface(632, 176),
            motion,
        ));
        let mut moved_input = surface(680, 128);
        moved_input.input_rect[1] += 1;
        assert!(!is_animated_bubble_contraction(
            &surface(632, 176),
            &moved_input,
            motion,
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
        assert!(session.context_menu_rect.is_none());
        assert!(session.require_context_menu_closed().is_ok());
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
    fn revision_zero_bootstrap_is_diagnostic_ready_without_consuming_revision_one() {
        let mut session = WindowGeometrySession::default();
        let mut application = LayoutApplication::rejected(0, PresentationState::Product, 1);
        application.applied = true;
        application.content_scale = 0.875;
        application.scale_factor = 1.25;
        application.visible_fit_bounds = [126, 326, 648, 660];
        application.active_bounds = [0, 0, 900, 1_490];
        application.physical_local_anchor = [394, 861];
        application.portrait_anchor = window_geometry::PhysicalPoint { x: 2_000, y: 1_100 };
        let hit_regions = window_interaction::PhysicalHitRegions {
            state: PresentationState::Product,
            scale: application.scale_factor * application.content_scale,
            envelope: [900, 1_490],
            interactive: Vec::new(),
            drag: Vec::new(),
            neutral: Vec::new(),
            portrait_alpha_mask: None,
            extra_native_rectangles: Vec::new(),
        };

        commit_bootstrap_geometry(&mut session, application, hit_regions).unwrap();

        assert_eq!(session.applied_revision, 0);
        assert_eq!(session.state, Some(PresentationState::Product));
        assert!(session.application.is_some());
        assert!(session.hit_regions.is_some());
        assert!(!session.anchor_user_positioned);
        assert!(session.revision.accept(1));
    }

    #[test]
    fn product_menu_region_policy_relaxes_only_windows_menu_sessions() {
        let expected = if cfg!(windows) {
            ContextMenuRegionPolicy::RelaxedWholeWindow
        } else {
            ContextMenuRegionPolicy::PreciseOverlay
        };
        assert_eq!(current_context_menu_region_policy(), expected);
    }

    #[test]
    fn reopening_product_menu_preserves_the_original_restore_snapshot() {
        let mut session = WindowGeometrySession::default();
        let base_application = LayoutApplication::rejected(41, PresentationState::Product, 5);
        let base_regions = window_interaction::PhysicalHitRegions {
            state: PresentationState::Product,
            scale: 1.0,
            envelope: [900, 1_374],
            interactive: Vec::new(),
            drag: Vec::new(),
            neutral: Vec::new(),
            portrait_alpha_mask: None,
            extra_native_rectangles: Vec::new(),
        };
        assert!(session.begin_context_menu(base_application.clone(), base_regions.clone()));
        assert!(session.context_menu_rect.is_none());

        assert!(!session.begin_context_menu(
            LayoutApplication::rejected(42, PresentationState::Product, 5),
            window_interaction::PhysicalHitRegions {
                envelope: [1_200, 1_200],
                ..base_regions.clone()
            },
        ));
        assert_eq!(
            session
                .context_menu_base_application
                .as_ref()
                .unwrap()
                .revision,
            base_application.revision
        );
        assert_eq!(
            session
                .context_menu_base_hit_regions
                .as_ref()
                .unwrap()
                .envelope,
            base_regions.envelope
        );
    }

    #[test]
    fn context_menu_surface_keeps_its_overlay_when_control_visibility_changes() {
        let contract = layout_contract().unwrap();
        let monitor = MonitorDescriptor {
            name: None,
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 2_560,
                height: 1_392,
            },
            scale_factor: 1.25,
        };
        let visible = ControlSurfaceLayout {
            bubble_rect: [130, 680, 640, 128],
            input_rect: [130, 818, 640, 52],
            controls_rect: [730, 690, 30, 30],
            bubble_visible: true,
            input_visible: true,
        };
        let application = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            1,
            &monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            Some(&visible),
            None,
            false,
            false,
        )
        .unwrap();
        let rect = [300, 900, 226, 273];
        let opened = build_context_menu_surface_geometry(
            &contract,
            &application,
            rect,
            Some(&visible),
            None,
            100,
        )
        .unwrap();
        assert_eq!(opened.base_hit_regions.interactive.len(), 2);
        let menu_region = *opened.expanded_hit_regions.interactive.last().unwrap();

        let hidden = ControlSurfaceLayout {
            bubble_visible: false,
            input_visible: false,
            ..visible
        };
        let changed = build_context_menu_surface_geometry(
            &contract,
            &application,
            rect,
            Some(&hidden),
            None,
            100,
        )
        .unwrap();
        assert!(changed.base_hit_regions.interactive.is_empty());
        assert_eq!(changed.base_hit_regions.drag.len(), 1);
        assert_eq!(changed.expanded_hit_regions.interactive, vec![menu_region]);
        assert_eq!(
            changed.application.active_bounds,
            opened.application.active_bounds
        );
        assert_eq!(
            changed.application.physical_placement,
            opened.application.physical_placement
        );
    }

    #[test]
    fn portrait_surface_mutations_reject_an_open_product_menu() {
        let mut session = WindowGeometrySession::default();
        session.context_menu_open = true;

        assert_eq!(
            session.require_context_menu_closed(),
            Err("PET_CONTEXT_MENU_OPEN".to_string())
        );
        assert!(session.context_menu_open);
        assert!(session.context_menu_base_application.is_none());
        assert!(session.context_menu_base_hit_regions.is_none());
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

        session.control_surface_preview_active = true;
        assert_eq!(session.defers_precise_surface_hit_regions(), cfg!(windows));
        session.control_surface_preview_active = false;
        assert!(!session.defers_precise_surface_hit_regions());

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
    fn mixed_dpi_drag_commit_preserves_the_released_window_position() {
        let contract = layout_contract().unwrap();
        let source_monitor = MonitorDescriptor {
            name: Some("source-150".to_string()),
            work_area: PhysicalRect {
                x: 0,
                y: 0,
                width: 3_840,
                height: 2_160,
            },
            scale_factor: 1.5,
        };
        let target_monitor = MonitorDescriptor {
            name: Some("target-100".to_string()),
            work_area: PhysicalRect {
                x: -3_840,
                y: 0,
                width: 3_840,
                height: 2_160,
            },
            scale_factor: 1.0,
        };
        let source = compute_pet_window_layout(
            &contract,
            PresentationState::Product,
            7,
            &source_monitor,
            None,
            AnchorPolicy::Automatic,
            100,
            None,
            None,
            false,
            false,
        )
        .unwrap();
        let released = window_geometry::PhysicalPoint { x: -3_200, y: 180 };
        let settled = compute_dragged_pet_window_layout(
            &contract,
            PresentationState::Product,
            7,
            &target_monitor,
            released,
            source.physical_local_anchor,
            100,
            None,
            None,
            false,
        )
        .unwrap();

        assert_ne!(source.physical_local_anchor, settled.physical_local_anchor);
        assert_eq!(settled.physical_placement.x, released.x);
        assert_eq!(settled.physical_placement.y, released.y);
        assert_eq!(
            settled.portrait_anchor,
            window_geometry::anchor_from_window_position(released, settled.physical_local_anchor,)
                .unwrap()
        );
    }

    #[test]
    fn drag_visual_effect_sync_detects_monitor_and_dpi_transitions() {
        let mut previous = LayoutApplication::rejected(1, PresentationState::Product, 3);
        previous.active_bounds = [48, 320, 804, 664];
        previous.physical_placement = window_geometry::PhysicalPlacement {
            x: 100,
            y: 200,
            width: 804,
            height: 664,
        };
        previous.work_area = PhysicalRect {
            x: 0,
            y: 0,
            width: 1_920,
            height: 1_080,
        };
        previous.monitor_name = Some("left".to_string());
        let mut moved = previous.clone();
        moved.physical_placement.x += 100;
        assert!(same_drag_visual_effect_geometry(&previous, &moved));

        moved.monitor_name = Some("right".to_string());
        assert!(!same_drag_visual_effect_geometry(&previous, &moved));
        moved.monitor_name = previous.monitor_name.clone();
        moved.scale_factor = 1.5;
        assert!(!same_drag_visual_effect_geometry(&previous, &moved));
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
        let request = runtime_request().expect("development runtime request should resolve");
        let root = request
            .explicit_development_root
            .expect("development root should be explicit");
        assert!(root.join("app/core_host/__main__.py").is_file());
        assert!(root.join("desktop/src-tauri/runtime-layouts").is_dir());
        assert_ne!(request.user_root, root);
        assert!(request.user_root.ends_with("Sakura Development"));
    }
}
