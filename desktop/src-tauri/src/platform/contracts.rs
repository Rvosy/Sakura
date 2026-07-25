use std::{
    collections::BTreeMap,
    ffi::OsString,
    fmt,
    fs::File,
    path::PathBuf,
    sync::atomic::AtomicBool,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};

use super::{
    PlatformError, PlatformErrorCategory, PlatformResult, PlatformService, PlatformTarget,
    RetryAdvice,
};
use crate::{
    window_geometry::PhysicalPlacement,
    window_interaction::{NativeDragCompletion, PhysicalHitRegions},
};

pub const SHARED_INSTANCE_ID: &str = "sakura.desktop.shared-user-data.v1";

pub trait InstanceLockLease: Send {}

pub enum InstanceLockAcquire {
    Acquired(Box<dyn InstanceLockLease>),
    AlreadyRunning,
}

pub trait InstanceLockBackend: Send + Sync {
    fn acquire(&self, application_id: &str) -> PlatformResult<InstanceLockAcquire>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProcessStdio {
    Null,
    Piped,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ManagedProcessRequest {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub current_directory: Option<PathBuf>,
    pub environment_overrides: Vec<(OsString, OsString)>,
    pub stdio: ProcessStdio,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ManagedPipeReadOutcome {
    Read(usize),
    Eof,
    Cancelled,
    TimedOut,
}

pub trait ManagedPipeReader: Send {
    fn read_until(
        &mut self,
        buffer: &mut [u8],
        deadline: Instant,
        cancelled: &AtomicBool,
    ) -> PlatformResult<ManagedPipeReadOutcome>;
}

pub struct ManagedProcessPipes {
    pub stdin: File,
    pub stdout: Box<dyn ManagedPipeReader>,
    pub stderr: Box<dyn ManagedPipeReader>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum ProcessExitStatus {
    Code(i64),
    Signal(i32),
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProcessWaitOutcome {
    Exited(ProcessExitStatus),
    TimedOut,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProcessTreeFinalization {
    pub root_status: ProcessExitStatus,
    pub forced: bool,
}

pub struct ProcessTreeFinalizationFailure {
    error: PlatformError,
    recovery: Box<dyn ManagedProcessTree>,
}

impl ProcessTreeFinalizationFailure {
    pub fn new(error: PlatformError, recovery: Box<dyn ManagedProcessTree>) -> Self {
        Self { error, recovery }
    }

    pub fn error(&self) -> &PlatformError {
        &self.error
    }

    pub fn into_parts(self) -> (PlatformError, Box<dyn ManagedProcessTree>) {
        (self.error, self.recovery)
    }
}

impl fmt::Debug for ProcessTreeFinalizationFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProcessTreeFinalizationFailure")
            .field("error", &self.error)
            .field("has_recovery_owner", &true)
            .finish()
    }
}

pub type ProcessTreeFinalizationResult =
    Result<ProcessTreeFinalization, ProcessTreeFinalizationFailure>;

pub trait ManagedProcessTree: Send {
    fn root_pid(&self) -> u32;
    #[cfg(test)]
    fn native_owner_pid_for_test(&self) -> Option<u32> {
        None
    }
    fn wait_root(&mut self, timeout: Duration) -> PlatformResult<ProcessWaitOutcome>;
    fn terminate_tree(&mut self, reason_code: u32) -> PlatformResult<()>;
    fn wait_tree_exited(&self, timeout: Duration) -> PlatformResult<bool>;
    fn release_exited(self: Box<Self>) -> PlatformResult<()>;
    fn finalize_until(
        self: Box<Self>,
        deadline: Instant,
        reason_code: u32,
    ) -> ProcessTreeFinalizationResult;
}

pub struct SpawnedProcessTree {
    pub tree: Box<dyn ManagedProcessTree>,
    pub pipes: Option<ManagedProcessPipes>,
}

pub trait ManagedProcessTreeBackend: Send + Sync {
    fn spawn(&self, request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree>;
}

pub trait WindowInteractionBackend: Send + Sync {
    fn apply_bounds(
        &self,
        window: &tauri::WebviewWindow,
        placement: &PhysicalPlacement,
    ) -> PlatformResult<()>;

    fn apply_hit_regions(
        &self,
        window: &tauri::WebviewWindow,
        regions: &PhysicalHitRegions,
    ) -> PlatformResult<()>;

    fn restore_full_hit_region(&self, window: &tauri::WebviewWindow) -> PlatformResult<()>;
    fn start_drag(&self, window: &tauri::WebviewWindow) -> PlatformResult<NativeDragCompletion>;
    fn set_visible(&self, window: &tauri::WebviewWindow, visible: bool) -> PlatformResult<()>;
    fn focus_text_input(&self, window: &tauri::WebviewWindow) -> PlatformResult<()>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeMode {
    ExplicitDevelopment,
    Packaged,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeArchitecture {
    X64,
    Arm64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuntimeLocationRequest {
    pub mode: RuntimeMode,
    pub target: PlatformTarget,
    pub executable_directory: PathBuf,
    pub resource_directory: PathBuf,
    pub explicit_development_root: Option<PathBuf>,
    pub assistant_root: PathBuf,
}

impl RuntimeLocationRequest {
    pub fn validate(&self) -> PlatformResult<()> {
        if self.mode == RuntimeMode::ExplicitDevelopment && self.explicit_development_root.is_none()
        {
            return Err(PlatformError::new(
                PlatformService::RuntimeLocator,
                PlatformErrorCategory::InvalidInput,
                "validate_location_request",
                RetryAdvice::Never,
                "development mode requires an explicit runtime root",
            ));
        }
        if self.mode == RuntimeMode::Packaged && self.explicit_development_root.is_some() {
            return Err(PlatformError::new(
                PlatformService::RuntimeLocator,
                PlatformErrorCategory::InvalidInput,
                "validate_location_request",
                RetryAdvice::Never,
                "packaged mode cannot use a development runtime root",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLayout {
    pub target: PlatformTarget,
    pub architecture: RuntimeArchitecture,
    pub mode: RuntimeMode,
    pub runtime_root: PathBuf,
    pub python_executable: PathBuf,
    /// Root containing the Python Core resources approved by RuntimeLocator.
    pub resource_root: PathBuf,
    /// Canonical configuration and data root supplied to the Assistant.
    pub assistant_root: PathBuf,
    pub core_entry: PathBuf,
    pub core_module: String,
    pub working_directory: PathBuf,
    pub source_id: String,
}

pub trait RuntimeLocator: Send + Sync {
    fn locate(&self, request: &RuntimeLocationRequest) -> PlatformResult<RuntimeLayout>;
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NativeDiagnosticsRequest {
    pub window_label: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeDiagnosticsSnapshot {
    pub target: PlatformTarget,
    pub window_backend: String,
    pub display_server: Option<String>,
    pub webview_version: Option<String>,
    pub facts: BTreeMap<String, String>,
}

pub trait NativeDiagnosticsBackend: Send + Sync {
    fn collect(
        &self,
        request: &NativeDiagnosticsRequest,
    ) -> PlatformResult<NativeDiagnosticsSnapshot>;
}

pub trait PlatformRuntime: Send + Sync {
    fn target(&self) -> PlatformTarget;
    fn instance_lock(&self) -> &dyn InstanceLockBackend;
    fn managed_process_tree(&self) -> &dyn ManagedProcessTreeBackend;
    fn window_interaction(&self) -> &dyn WindowInteractionBackend;
    fn runtime_locator(&self) -> &dyn RuntimeLocator;
    fn native_diagnostics(&self) -> &dyn NativeDiagnosticsBackend;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::{PlatformError, PlatformErrorCategory, PlatformService, RetryAdvice};

    struct ContractOnlyBackend;

    fn contract_only(service: PlatformService, operation: &'static str) -> PlatformError {
        PlatformError::new(
            service,
            PlatformErrorCategory::UnsupportedEnvironment,
            operation,
            RetryAdvice::Never,
            "WP-1P-01 freezes contracts without a concrete backend",
        )
    }

    impl InstanceLockBackend for ContractOnlyBackend {
        fn acquire(&self, _application_id: &str) -> PlatformResult<InstanceLockAcquire> {
            Err(contract_only(PlatformService::InstanceLock, "acquire"))
        }
    }

    impl ManagedProcessTreeBackend for ContractOnlyBackend {
        fn spawn(&self, _request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
            Err(contract_only(PlatformService::ManagedProcessTree, "spawn"))
        }
    }

    impl WindowInteractionBackend for ContractOnlyBackend {
        fn apply_bounds(
            &self,
            _window: &tauri::WebviewWindow,
            _placement: &PhysicalPlacement,
        ) -> PlatformResult<()> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "apply_bounds",
            ))
        }

        fn apply_hit_regions(
            &self,
            _window: &tauri::WebviewWindow,
            _regions: &PhysicalHitRegions,
        ) -> PlatformResult<()> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "apply_hit_regions",
            ))
        }

        fn restore_full_hit_region(&self, _window: &tauri::WebviewWindow) -> PlatformResult<()> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "restore_full_hit_region",
            ))
        }

        fn start_drag(
            &self,
            _window: &tauri::WebviewWindow,
        ) -> PlatformResult<NativeDragCompletion> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "start_drag",
            ))
        }

        fn set_visible(
            &self,
            _window: &tauri::WebviewWindow,
            _visible: bool,
        ) -> PlatformResult<()> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "set_visible",
            ))
        }

        fn focus_text_input(&self, _window: &tauri::WebviewWindow) -> PlatformResult<()> {
            Err(contract_only(
                PlatformService::WindowInteraction,
                "focus_text_input",
            ))
        }
    }

    impl RuntimeLocator for ContractOnlyBackend {
        fn locate(&self, _request: &RuntimeLocationRequest) -> PlatformResult<RuntimeLayout> {
            Err(contract_only(PlatformService::RuntimeLocator, "locate"))
        }
    }

    impl NativeDiagnosticsBackend for ContractOnlyBackend {
        fn collect(
            &self,
            _request: &NativeDiagnosticsRequest,
        ) -> PlatformResult<NativeDiagnosticsSnapshot> {
            Err(contract_only(PlatformService::NativeDiagnostics, "collect"))
        }
    }

    #[test]
    fn all_five_backend_contracts_are_object_safe() {
        let backend = ContractOnlyBackend;
        let _: &dyn InstanceLockBackend = &backend;
        let _: &dyn ManagedProcessTreeBackend = &backend;
        let _: &dyn WindowInteractionBackend = &backend;
        let _: &dyn RuntimeLocator = &backend;
        let _: &dyn NativeDiagnosticsBackend = &backend;
    }

    #[test]
    fn shared_lock_identity_matches_the_data_compatibility_contract() {
        assert_eq!(SHARED_INSTANCE_ID, "sakura.desktop.shared-user-data.v1");
    }

    #[test]
    fn development_runtime_selection_must_be_explicit() {
        let request = RuntimeLocationRequest {
            mode: RuntimeMode::ExplicitDevelopment,
            target: PlatformTarget::WindowsX64,
            executable_directory: PathBuf::from("bin"),
            resource_directory: PathBuf::from("resources"),
            explicit_development_root: None,
            assistant_root: PathBuf::from("assistant-root"),
        };
        let error = request
            .validate()
            .expect_err("implicit development runtime selection must fail closed");
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
        assert_eq!(error.retry, RetryAdvice::Never);
    }
}
