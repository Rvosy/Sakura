//! Fixed macOS launch-help actions exposed only to the first-party settings window.

pub const APPLE_SUPPORT_URL: &str =
    "https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac";

pub const fn is_available() -> bool {
    cfg!(target_os = "macos")
}

#[cfg(target_os = "macos")]
fn spawn_open(arguments: &[&str], error_code: &str) -> Result<(), String> {
    std::process::Command::new("/usr/bin/open")
        .args(arguments)
        .spawn()
        .map(|_| ())
        .map_err(|_| error_code.to_string())
}

#[cfg(target_os = "macos")]
pub fn open_system_settings() -> Result<(), String> {
    spawn_open(
        &["-a", "System Settings"],
        "MACOS_SYSTEM_SETTINGS_OPEN_FAILED",
    )
}

#[cfg(not(target_os = "macos"))]
pub fn open_system_settings() -> Result<(), String> {
    Err("MACOS_OPEN_HELP_UNAVAILABLE".to_string())
}

#[cfg(target_os = "macos")]
pub fn open_apple_support() -> Result<(), String> {
    spawn_open(&[APPLE_SUPPORT_URL], "MACOS_APPLE_SUPPORT_OPEN_FAILED")
}

#[cfg(not(target_os = "macos"))]
pub fn open_apple_support() -> Result<(), String> {
    Err("MACOS_OPEN_HELP_UNAVAILABLE".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apple_support_target_is_a_fixed_https_document() {
        assert!(APPLE_SUPPORT_URL.starts_with("https://support.apple.com/"));
        assert!(APPLE_SUPPORT_URL.contains("open-a-mac-app-from-an-unknown-developer"));
        assert!(!APPLE_SUPPORT_URL.chars().any(char::is_control));
    }

    #[test]
    fn availability_matches_the_compiled_platform() {
        assert_eq!(is_available(), cfg!(target_os = "macos"));
    }
}
