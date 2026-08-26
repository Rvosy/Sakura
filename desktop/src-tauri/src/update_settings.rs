use std::path::Path;

use serde::Serialize;
use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

pub const REPOSITORY_URL: &str = "https://github.com/Rvosy/Sakura";
pub const WEBSITE_URL: &str = "https://sakura.cialloo.cn/";
pub const CHANGELOG_URL: &str = "https://github.com/Rvosy/Sakura/blob/main/CHANGELOG.md";
pub const SPONSOR_URL: &str = "https://ifdian.net/a/Rvosy";

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AboutSnapshot {
    schema_version: u32,
    version: String,
    repository_url: &'static str,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateSnapshot {
    schema_version: u32,
    current_version: String,
    mode: &'static str,
    available: bool,
    version: Option<String>,
    notes: Option<String>,
    download_url: Option<String>,
}

pub fn about_snapshot() -> AboutSnapshot {
    AboutSnapshot {
        schema_version: 1,
        version: env!("CARGO_PKG_VERSION").to_string(),
        repository_url: REPOSITORY_URL,
    }
}

pub fn is_portable(executable_directory: &Path) -> bool {
    portable_mode(
        executable_directory.join("portable.flag").is_file(),
        cfg!(target_os = "windows"),
    )
}

fn portable_mode(has_marker: bool, is_windows: bool) -> bool {
    has_marker && is_windows
}

fn portable_download_url(raw: &Value) -> Result<String, String> {
    let value = raw
        .get("portable")
        .and_then(|value| value.get("windows-x86_64"))
        .and_then(|value| value.get("url"))
        .and_then(Value::as_str)
        .filter(|value| value.starts_with("https://") && !value.chars().any(char::is_control))
        .ok_or_else(|| "PORTABLE_UPDATE_URL_MISSING".to_string())?;
    Ok(value.to_string())
}

pub async fn check(app: &AppHandle, executable_directory: &Path) -> Result<UpdateSnapshot, String> {
    let portable = is_portable(executable_directory);
    let update = app
        .updater()
        .map_err(|_| "UPDATE_CONFIGURATION_INVALID".to_string())?
        .check()
        .await
        .map_err(|_| "UPDATE_CHECK_FAILED".to_string())?;
    let Some(update) = update else {
        return Ok(UpdateSnapshot {
            schema_version: 1,
            current_version: env!("CARGO_PKG_VERSION").to_string(),
            mode: if portable { "portable" } else { "installed" },
            available: false,
            version: None,
            notes: None,
            download_url: None,
        });
    };
    let download_url = if portable {
        Some(portable_download_url(&update.raw_json)?)
    } else {
        None
    };
    Ok(UpdateSnapshot {
        schema_version: 1,
        current_version: update.current_version,
        mode: if portable { "portable" } else { "installed" },
        available: true,
        version: Some(update.version),
        notes: update.body,
        download_url,
    })
}

pub async fn install(app: &AppHandle, executable_directory: &Path) -> Result<(), String> {
    if is_portable(executable_directory) {
        return Err("PORTABLE_UPDATE_MANUAL_REQUIRED".to_string());
    }
    let update = app
        .updater()
        .map_err(|_| "UPDATE_CONFIGURATION_INVALID".to_string())?
        .check()
        .await
        .map_err(|_| "UPDATE_CHECK_FAILED".to_string())?
        .ok_or_else(|| "UPDATE_NOT_AVAILABLE".to_string())?;
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|_| "UPDATE_INSTALL_FAILED".to_string())
}

pub fn open_portable_download(url: &str) -> Result<(), String> {
    if !url.starts_with("https://") || url.chars().any(char::is_control) {
        return Err("PORTABLE_UPDATE_URL_INVALID".to_string());
    }
    open_https_url(url, "PORTABLE_UPDATE_OPEN_FAILED")
}

pub fn open_repository() -> Result<(), String> {
    open_https_url(REPOSITORY_URL, "ABOUT_REPOSITORY_OPEN_FAILED")
}

pub fn open_website() -> Result<(), String> {
    open_https_url(WEBSITE_URL, "ABOUT_WEBSITE_OPEN_FAILED")
}

pub fn open_changelog() -> Result<(), String> {
    open_https_url(CHANGELOG_URL, "ABOUT_CHANGELOG_OPEN_FAILED")
}

pub fn open_sponsor() -> Result<(), String> {
    open_https_url(SPONSOR_URL, "ABOUT_SPONSOR_OPEN_FAILED")
}

fn open_https_url(url: &str, error_code: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer.exe");
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = std::process::Command::new("xdg-open");
    command
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|_| error_code.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn portable_manifest_url_is_explicit_and_https_only() {
        let raw = serde_json::json!({
            "portable": {"windows-x86_64": {"url": "https://example.test/Sakura.zip"}}
        });
        assert_eq!(
            portable_download_url(&raw).unwrap(),
            "https://example.test/Sakura.zip"
        );
        assert_eq!(
            portable_download_url(&serde_json::json!({
                "portable": {"windows-x86_64": {"url": "http://example.test/Sakura.zip"}}
            })),
            Err("PORTABLE_UPDATE_URL_MISSING".to_string())
        );
    }

    #[test]
    fn portable_flag_is_a_windows_only_contract() {
        assert!(portable_mode(true, true));
        assert!(!portable_mode(false, true));
        assert!(!portable_mode(true, false));
    }

    #[test]
    fn about_snapshot_and_product_links_are_fixed() {
        assert_eq!(
            about_snapshot(),
            AboutSnapshot {
                schema_version: 1,
                version: env!("CARGO_PKG_VERSION").to_string(),
                repository_url: "https://github.com/Rvosy/Sakura",
            }
        );
        assert_eq!(WEBSITE_URL, "https://sakura.cialloo.cn/");
        assert_eq!(
            CHANGELOG_URL,
            "https://github.com/Rvosy/Sakura/blob/main/CHANGELOG.md"
        );
        assert_eq!(SPONSOR_URL, "https://ifdian.net/a/Rvosy");
    }
}
