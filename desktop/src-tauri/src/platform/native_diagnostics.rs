use std::env;

use super::{
    current_platform_target, NativeDiagnosticsBackend, NativeDiagnosticsRequest,
    NativeDiagnosticsSnapshot, PlatformError, PlatformErrorCategory, PlatformResult,
    PlatformService, RetryAdvice,
};

pub struct NativeDiagnosticsBackendImpl;

fn display_server() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        return "win32";
    }
    #[cfg(target_os = "macos")]
    {
        return "quartz";
    }
    #[cfg(target_os = "linux")]
    {
        if env::var_os("WAYLAND_DISPLAY").is_some() {
            return "wayland";
        }
        if env::var_os("DISPLAY").is_some() {
            return "x11";
        }
        return "unknown";
    }
    #[allow(unreachable_code)]
    "unknown"
}

fn window_backend() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        return "win32-webview2";
    }
    #[cfg(target_os = "macos")]
    {
        return "nswindow-webkit";
    }
    #[cfg(target_os = "linux")]
    {
        return match display_server() {
            "wayland" => "wayland-webkit",
            "x11" => "x11-webkit",
            _ => "webkit-unknown-display",
        };
    }
    #[allow(unreachable_code)]
    "unknown"
}

impl NativeDiagnosticsBackend for NativeDiagnosticsBackendImpl {
    fn collect(
        &self,
        request: &NativeDiagnosticsRequest,
    ) -> PlatformResult<NativeDiagnosticsSnapshot> {
        let target = current_platform_target().ok_or_else(|| {
            PlatformError::new(
                PlatformService::NativeDiagnostics,
                PlatformErrorCategory::UnsupportedEnvironment,
                "collect",
                RetryAdvice::Never,
                "current CPU/OS target is outside the formal Runtime v2 matrix",
            )
        })?;

        let mut facts = std::collections::BTreeMap::new();
        facts.insert("cpuArchitecture".to_string(), env::consts::ARCH.to_string());
        facts.insert("osFamily".to_string(), env::consts::OS.to_string());
        facts.insert(
            "displayServerSource".to_string(),
            display_server().to_string(),
        );
        facts.insert(
            "scaleSource".to_string(),
            "tauri-monitor-scale-factor".to_string(),
        );
        facts.insert(
            "displayFactsAvailable".to_string(),
            "window-scoped".to_string(),
        );
        facts.insert(
            "windowLabelProvided".to_string(),
            request.window_label.is_some().to_string(),
        );
        facts.insert(
            "ciEnvironment".to_string(),
            env::var_os("CI").is_some().to_string(),
        );

        Ok(NativeDiagnosticsSnapshot {
            target,
            window_backend: window_backend().to_string(),
            display_server: Some(display_server().to_string()),
            webview_version: tauri::webview_version().ok(),
            facts,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostics_are_structured_and_do_not_expose_paths_or_environment_values() {
        let snapshot = NativeDiagnosticsBackendImpl
            .collect(&NativeDiagnosticsRequest {
                window_label: Some("pet".to_string()),
            })
            .expect("formal target diagnostics should collect");
        assert!(matches!(
            snapshot.window_backend.as_str(),
            "win32-webview2"
                | "nswindow-webkit"
                | "x11-webkit"
                | "wayland-webkit"
                | "webkit-unknown-display"
        ));
        assert!(snapshot.display_server.is_some());
        assert_eq!(snapshot.facts["windowLabelProvided"], "true");
        assert!(!snapshot.facts.values().any(|value| value.contains('\\')));
    }

    #[test]
    fn display_server_classification_is_stable() {
        assert!(matches!(
            display_server(),
            "win32" | "quartz" | "x11" | "wayland" | "unknown"
        ));
    }
}
