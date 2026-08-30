use std::fmt;

use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PlatformService {
    InstanceLock,
    ManagedProcessTree,
    WindowInteraction,
    RuntimeLocator,
    NativeDiagnostics,
}

impl PlatformService {
    pub const fn code(self) -> &'static str {
        match self {
            Self::InstanceLock => "instance_lock",
            Self::ManagedProcessTree => "managed_process_tree",
            Self::WindowInteraction => "window_interaction",
            Self::RuntimeLocator => "runtime_locator",
            Self::NativeDiagnostics => "native_diagnostics",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PlatformErrorCategory {
    InvalidInput,
    NotFound,
    PermissionDenied,
    UnsupportedEnvironment,
    IncompatibleArchitecture,
    IntegrityMismatch,
    ResourceBusy,
    ResourceExhausted,
    TemporarilyUnavailable,
    TimedOut,
    IdentityChanged,
    NativeFailure,
}

impl PlatformErrorCategory {
    pub const ALL: [Self; 12] = [
        Self::InvalidInput,
        Self::NotFound,
        Self::PermissionDenied,
        Self::UnsupportedEnvironment,
        Self::IncompatibleArchitecture,
        Self::IntegrityMismatch,
        Self::ResourceBusy,
        Self::ResourceExhausted,
        Self::TemporarilyUnavailable,
        Self::TimedOut,
        Self::IdentityChanged,
        Self::NativeFailure,
    ];

    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid_input",
            Self::NotFound => "not_found",
            Self::PermissionDenied => "permission_denied",
            Self::UnsupportedEnvironment => "unsupported_environment",
            Self::IncompatibleArchitecture => "incompatible_architecture",
            Self::IntegrityMismatch => "integrity_mismatch",
            Self::ResourceBusy => "resource_busy",
            Self::ResourceExhausted => "resource_exhausted",
            Self::TemporarilyUnavailable => "temporarily_unavailable",
            Self::TimedOut => "timed_out",
            Self::IdentityChanged => "identity_changed",
            Self::NativeFailure => "native_failure",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RetryAdvice {
    Never,
    AfterUserAction,
    AfterExternalChange,
    WithinSupervisorBudget,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeErrorCode {
    pub namespace: &'static str,
    pub value: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlatformError {
    pub service: PlatformService,
    pub category: PlatformErrorCategory,
    pub operation: &'static str,
    pub retry: RetryAdvice,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub native_code: Option<NativeErrorCode>,
}

impl PlatformError {
    pub fn new(
        service: PlatformService,
        category: PlatformErrorCategory,
        operation: &'static str,
        retry: RetryAdvice,
        message: impl Into<String>,
    ) -> Self {
        Self {
            service,
            category,
            operation,
            retry,
            message: message.into(),
            native_code: None,
        }
    }

    pub fn with_native_code(mut self, namespace: &'static str, value: i64) -> Self {
        self.native_code = Some(NativeErrorCode { namespace, value });
        self
    }

    pub fn stable_code(&self) -> String {
        format!("platform.{}.{}", self.service.code(), self.category.code())
    }
}

impl fmt::Display for PlatformError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{} during {}: {}",
            self.stable_code(),
            self.operation,
            self.message
        )
    }
}

impl std::error::Error for PlatformError {}

pub type PlatformResult<T> = Result<T, PlatformError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_codes_do_not_depend_on_native_error_numbers() {
        let windows = PlatformError::new(
            PlatformService::RuntimeLocator,
            PlatformErrorCategory::NotFound,
            "locate_python",
            RetryAdvice::AfterExternalChange,
            "runtime is absent",
        )
        .with_native_code("win32", 2);
        let posix = PlatformError::new(
            PlatformService::RuntimeLocator,
            PlatformErrorCategory::NotFound,
            "locate_python",
            RetryAdvice::AfterExternalChange,
            "runtime is absent",
        )
        .with_native_code("errno", 2);

        assert_eq!(windows.stable_code(), "platform.runtime_locator.not_found");
        assert_eq!(windows.stable_code(), posix.stable_code());
        assert_ne!(windows.native_code, posix.native_code);
    }

    #[test]
    fn category_codes_are_unique_and_complete() {
        let mut codes = PlatformErrorCategory::ALL
            .map(PlatformErrorCategory::code)
            .to_vec();
        codes.sort_unstable();
        codes.dedup();
        assert_eq!(codes.len(), PlatformErrorCategory::ALL.len());
    }

    #[test]
    fn diagnostics_shape_is_serializable_without_flattening_native_codes() {
        let error = PlatformError::new(
            PlatformService::ManagedProcessTree,
            PlatformErrorCategory::PermissionDenied,
            "spawn",
            RetryAdvice::AfterUserAction,
            "access denied",
        )
        .with_native_code("errno", 13);
        let value = serde_json::to_value(error).expect("platform error must serialize");
        assert_eq!(value["service"], "managed_process_tree");
        assert_eq!(value["category"], "permission_denied");
        assert_eq!(value["retry"], "after_user_action");
        assert_eq!(value["nativeCode"]["namespace"], "errno");
        assert_eq!(value["nativeCode"]["value"], 13);
    }
}
