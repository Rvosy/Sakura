//! Registry downloads stay in the Shell; Core owns plugin installation.
use crate::{
    download_sources::{self, DownloadSources},
    product_shell,
    shell_lifecycle::{
        dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};
use serde_json::{json, Value};
use std::{collections::HashMap, fs, path::PathBuf, sync::Mutex};
use tauri::{ipc::Channel, State, WebviewWindow};
use tokio::sync::watch;

const CATALOG_URL: &str =
    "https://github.com/Rvosy/Sakura-Registry/releases/latest/download/catalog.json";
#[derive(Default)]
pub struct MarketplaceState {
    catalog: Mutex<Option<Value>>,
    tasks: Mutex<HashMap<String, watch::Sender<bool>>>,
}

fn selected_release(catalog: &Value, id: &str, version: &str) -> Result<Value, String> {
    let plugin = catalog["plugins"]
        .as_array()
        .and_then(|items| items.iter().find(|p| p["id"] == id))
        .ok_or("插件不在当前目录中。")?;
    let release = plugin["versions"]
        .as_array()
        .and_then(|items| items.iter().find(|v| v["version"] == version))
        .ok_or("版本不在当前目录中。")?;
    if release["yanked"] != false
        || release["manifest"]["id"] != id
        || release["manifest"]["version"] != version
        || release["package"]["size"].as_u64().is_none()
    {
        return Err("该版本不可安装。".into());
    }
    download_sources::https_url(
        release["package"]["url"]
            .as_str()
            .ok_or("安装包地址缺失。")?,
    )?;
    Ok(release.clone())
}

#[tauri::command]
pub(crate) async fn settings_marketplace_catalog(
    window: WebviewWindow,
    sources: State<'_, DownloadSources>,
    market: State<'_, MarketplaceState>,
    progress: Channel<Value>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let data = download_sources::fetch(
        &download_sources::https_url(CATALOG_URL)?,
        &sources.load()?,
        8 * 1024 * 1024,
        |source, _, _| {
            let _ = progress.send(json!({"source":source}));
        },
    )
    .await?;
    let catalog: Value = serde_json::from_slice(&data).map_err(|_| "插件目录格式无效。")?;
    if catalog["schema_version"] != 1 || !catalog["plugins"].is_array() {
        return Err("插件目录版本不受支持。".into());
    }
    let context = settings_response_payload(
        dispatch_settings_request(
            settings_core_handle(&lifecycle)?,
            None,
            "plugins.marketplace.context",
            json!({}),
            std::time::Duration::from_secs(4),
        )
        .await?,
    )?;
    *market
        .catalog
        .lock()
        .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")? = Some(catalog.clone());
    Ok(json!({"catalog":catalog,"context":context}))
}

// Temp packages live until Core has acknowledged installation, including when
// the settings window closes. A cancellation only interrupts the download.
struct PackageFile(PathBuf);
impl Drop for PackageFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

#[tauri::command]
pub(crate) async fn settings_marketplace_install(
    window: WebviewWindow,
    request_id: String,
    plugin_id: String,
    version: String,
    window_generation: u64,
    core_generation_id: String,
    revision: String,
    progress: Channel<Value>,
    market: State<'_, MarketplaceState>,
    sources: State<'_, DownloadSources>,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    product_shell::assert_settings_identity(
        &shell,
        &handle,
        window_generation,
        &core_generation_id,
    )?;
    let release = {
        let catalog = market
            .catalog
            .lock()
            .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?;
        selected_release(
            catalog.as_ref().ok_or("请先刷新插件目录。")?,
            &plugin_id,
            &version,
        )?
    };
    let sources = sources.load()?;
    let url = download_sources::https_url(release["package"]["url"].as_str().unwrap())?;
    let (cancel, mut cancellation) = watch::channel(false);
    {
        let mut tasks = market
            .tasks
            .lock()
            .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?;
        if tasks.contains_key(&request_id) {
            return Err("下载任务已存在。".into());
        }
        tasks.insert(request_id.clone(), cancel);
    }
    let _ = progress.send(json!({"phase":"downloading","progress":0}));
    let data = tokio::select! {
        result = download_sources::fetch(&url, &sources, 64 * 1024 * 1024, |source, size, total| {
            let percent = total.filter(|n| *n > 0).map(|n| (size as f64 / n as f64 * 100.0).min(100.0)).unwrap_or(0.0);
            let _ = progress.send(json!({"phase":"downloading","source":source,"progress":percent}));
        }) => result,
        _ = cancellation.changed() => Err("下载已取消。".into()),
    };
    market
        .tasks
        .lock()
        .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?
        .remove(&request_id);
    let data = data?;
    if *cancellation.borrow() {
        return Err("下载已取消。".into());
    }
    if Some(data.len() as u64) != release["package"]["size"].as_u64() {
        return Err("安装包大小与目录不一致。".into());
    }
    product_shell::assert_settings_identity(
        &shell,
        &handle,
        window_generation,
        &core_generation_id,
    )?;
    let file = PackageFile(
        std::env::temp_dir().join(format!("sakura-plugin-{}.zip", uuid::Uuid::new_v4())),
    );
    fs::write(&file.0, data).map_err(|_| "无法写入临时安装包。")?;
    let _ = progress.send(json!({"phase":"installing","progress":100}));
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "plugins.marketplace.install",
        json!({
            "revision":revision,"sourcePath":file.0,"pluginId":plugin_id,"version":version,
        }),
        std::time::Duration::from_secs(300),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    product_shell::assert_settings_identity(
        &shell,
        &handle,
        window_generation,
        &core_generation_id,
    )?;
    Ok(payload)
}

#[tauri::command]
pub(crate) fn settings_marketplace_cancel(
    window: WebviewWindow,
    request_id: String,
    market: State<'_, MarketplaceState>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    if let Some(sender) = market
        .tasks
        .lock()
        .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?
        .get(&request_id)
    {
        let _ = sender.send(true);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn installation_uses_the_loaded_catalog_and_rejects_withdrawn_versions() {
        let mut catalog = json!({"plugins":[{"id":"demo","versions":[{"version":"1.0.0","yanked":false,
            "manifest":{"id":"demo","version":"1.0.0"},"package":{"url":"https://github.com/example/repo/releases/download/v1/plugin.zip","size":10}}]}]});
        assert!(selected_release(&catalog, "demo", "1.0.0").is_ok());
        assert!(selected_release(&catalog, "demo", "2.0.0").is_err());
        catalog["plugins"][0]["versions"][0]["yanked"] = json!(true);
        assert!(selected_release(&catalog, "demo", "1.0.0").is_err());
    }
}
