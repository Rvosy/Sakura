//! Registry downloads stay in the Shell; Core owns plugin installation.
use crate::{
    download_sources::{self, DownloadSources},
    product_shell,
    shell_lifecycle::{
        dispatch_settings_install, dispatch_settings_request, settings_core_handle,
        settings_response_payload, ShellLifecycleState,
    },
};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::{ipc::Channel, State, WebviewWindow};
use tokio::sync::watch;

const CATALOG_URL: &str =
    "https://github.com/Rvosy/Sakura-Registry/releases/latest/download/catalog.json";
pub struct MarketplaceState {
    cache_root: PathBuf,
    refresh: tokio::sync::Mutex<()>,
    catalog: Mutex<Option<Value>>,
    tasks: Mutex<HashMap<String, watch::Sender<bool>>>,
}

impl MarketplaceState {
    pub fn new(user_root: PathBuf) -> Self {
        Self {
            cache_root: user_root.join("data/cache/plugin-marketplace"),
            refresh: tokio::sync::Mutex::new(()),
            catalog: Mutex::new(None),
            tasks: Mutex::new(HashMap::new()),
        }
    }

    fn cached_catalog(&self) -> Option<Value> {
        let catalog = read_cache(&self.cache_root.join("catalog.json"), 8 * 1024 * 1024)?;
        valid_catalog(&catalog).then_some(catalog)
    }

    fn readme_path(&self, url: &str) -> Option<PathBuf> {
        let url = reqwest::Url::parse(url).ok()?;
        let parts: Vec<_> = url.path_segments()?.collect();
        // Only addresses already validated by readme_addresses enter this cache.
        if url.host_str() != Some("github.com")
            || parts.len() != 5
            || parts[2] != "blob"
            || parts.iter().any(|p| {
                p.is_empty()
                    || *p == "."
                    || *p == ".."
                    || !p
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
            })
        {
            return None;
        }
        Some(
            self.cache_root
                .join("readmes")
                .join(format!("owner-{}", parts[0]))
                .join(format!("repo-{}", parts[1]))
                .join(format!("commit-{}.json", parts[3])),
        )
    }

    fn cached_readme(&self, url: &str) -> Option<Value> {
        let value = read_cache(&self.readme_path(url)?, 2 * 1024 * 1024)?;
        let markdown = value["markdown"].as_str()?;
        (value["url"] == url && !markdown.trim().is_empty() && markdown.len() <= 256 * 1024)
            .then_some(value)
    }

    fn cached_readmes(&self, catalog: &Value) -> Vec<Value> {
        let mut docs = Vec::new();
        for plugin in catalog["plugins"].as_array().into_iter().flatten() {
            for release in plugin["versions"].as_array().into_iter().flatten() {
                let Some((id, version)) = plugin["id"].as_str().zip(release["version"].as_str())
                else {
                    continue;
                };
                let Ok((_, url)) = readme_addresses(catalog, id, version) else {
                    continue;
                };
                if let Some(document) = self.cached_readme(&url) {
                    docs.push(json!({"id":id,"version":version,"commit":release["commit"],"repository":plugin["repository"],"document":document}));
                }
            }
        }
        docs
    }
}

fn valid_catalog(catalog: &Value) -> bool {
    catalog["schema_version"] == 1
        && catalog["plugins"].as_array().is_some_and(|plugins| {
            plugins.iter().all(|p| {
                p["id"].is_string()
                    && p["versions"]
                        .as_array()
                        .is_some_and(|versions| versions.iter().all(|v| v["version"].is_string()))
            })
        })
}

fn read_cache(path: &Path, maximum: u64) -> Option<Value> {
    if fs::metadata(path).ok()?.len() > maximum {
        return None;
    }
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}

fn save_cache(path: &Path, value: &Value) {
    // A cache write failure must not discard a successful network response.
    if let Ok(bytes) = serde_json::to_vec(value) {
        if let Err(error) = crate::ui_config::atomic_write(path, &bytes, "MARKETPLACE_CACHE") {
            eprintln!("Plugin marketplace cache write failed: {error}");
        }
    }
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

fn validate_install_version(
    current: &Value,
    revision: &str,
    plugin_id: &str,
    version: &str,
) -> Result<(), String> {
    if current["revision"] != revision {
        return Err("CONFIG_REVISION_CONFLICT: 插件列表已变化，请刷新后重试。".into());
    }
    let target = semver::Version::parse(version).map_err(|_| "市场版本号格式无效。")?;
    for plugin in current["plugins"].as_array().into_iter().flatten() {
        if plugin["pluginId"] != plugin_id
            || plugin["reasonCode"]
                .as_str()
                .is_some_and(|reason| reason.starts_with("PLUGIN_MIGRATION_"))
        {
            continue;
        }
        // An unreadable local manifest has no comparable version and remains
        // repairable. Core still owns installation identity and conflict checks.
        if let Some(installed) = plugin["version"]
            .as_str()
            .and_then(|text| semver::Version::parse(text).ok())
        {
            if installed.cmp_precedence(&target).is_gt() {
                return Err("PLUGIN_DOWNGRADE_FORBIDDEN: 已安装更新版本。".into());
            }
        }
    }
    Ok(())
}

fn readme_addresses(catalog: &Value, id: &str, version: &str) -> Result<(String, String), String> {
    let release = selected_release(catalog, id, version)?;
    let plugin = catalog["plugins"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["id"] == id)
        .unwrap();
    let repository =
        download_sources::https_url(plugin["repository"].as_str().ok_or("项目地址缺失。")?)?;
    let parts: Vec<_> = repository.path().trim_matches('/').split('/').collect();
    let commit = release["commit"].as_str().ok_or("版本来源缺失。")?;
    if repository.host_str() != Some("github.com")
        || parts.len() != 2
        || parts.iter().any(|p| {
            p.is_empty()
                || *p == "."
                || *p == ".."
                || !p
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        })
        || repository.query().is_some()
        || repository.port().is_some()
        || commit.len() != 40
        || !commit.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err("项目说明来源无效。".into());
    }
    let path = parts.join("/");
    Ok((
        format!("https://raw.githubusercontent.com/{path}/{commit}/README.md"),
        format!("https://github.com/{path}/blob/{commit}/README.md"),
    ))
}

#[tauri::command]
pub(crate) async fn settings_marketplace_readme(
    window: WebviewWindow,
    plugin_id: String,
    version: String,
    sources: State<'_, DownloadSources>,
    market: State<'_, MarketplaceState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let (raw, url) = {
        let catalog = market
            .catalog
            .lock()
            .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?;
        readme_addresses(
            catalog.as_ref().ok_or("请先刷新插件目录。")?,
            &plugin_id,
            &version,
        )?
    };
    if let Some(document) = market.cached_readme(&url) {
        return Ok(document);
    }
    let data = tokio::time::timeout(
        std::time::Duration::from_secs(45),
        download_sources::fetch(
            &download_sources::https_url(&raw)?,
            &sources.load()?,
            256 * 1024,
            |_, _, _| {},
        ),
    )
    .await
    .map_err(|_| "项目说明加载超时。")??;
    let markdown = String::from_utf8(data).map_err(|_| "项目说明编码无效。")?;
    if markdown.trim().is_empty() {
        return Err("项目说明为空。".into());
    }
    let document = json!({"markdown":markdown,"url":url});
    if let Some(path) = market.readme_path(&url) {
        save_cache(&path, &document);
    }
    Ok(document)
}

#[tauri::command]
pub(crate) fn settings_marketplace_open_url(
    window: WebviewWindow,
    url: String,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    let url = reqwest::Url::parse(&url).map_err(|_| "网页地址无效。")?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("网页地址必须使用 HTTPS，且不能包含凭据。".into());
    }
    crate::update_settings::open_https_url(url.as_str(), "无法打开网页。")
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
    let cached = {
        let mut current = market
            .catalog
            .lock()
            .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")?;
        if current.is_none() {
            *current = market.cached_catalog();
        }
        current.clone()
    };
    if let Some(catalog) = cached {
        let _ = progress.send(
            json!({"catalog":catalog,"context":context,"readmes":market.cached_readmes(&catalog)}),
        );
    }
    // Reopened windows can read the cache while an older window's fetch finishes.
    // Serialize publication so an older response cannot overwrite a newer catalog.
    let _refresh = market.refresh.lock().await;
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
    if !valid_catalog(&catalog) {
        return Err("插件目录版本不受支持。".into());
    }
    save_cache(&market.cache_root.join("catalog.json"), &catalog);
    *market
        .catalog
        .lock()
        .map_err(|_| "MARKETPLACE_STATE_UNAVAILABLE")? = Some(catalog.clone());
    Ok(json!({"catalog":catalog,"context":context,"readmes":market.cached_readmes(&catalog)}))
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
    let current = settings_response_payload(
        dispatch_settings_request(
            handle.clone(),
            None,
            "plugins.settings.get",
            json!({}),
            std::time::Duration::from_secs(4),
        )
        .await?,
    )?;
    validate_install_version(&current, &revision, &plugin_id, &version)?;
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
    let response = dispatch_settings_install(
        handle.clone(),
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
    fn native_install_rejects_downgrades_but_allows_equal_precedence_repairs() {
        let current = |version| {
            json!({"revision":"current","plugins":[{
                "pluginId":"demo","version":version,"source":"user","reasonCode":"READY"
            }]})
        };
        for (installed, target) in [
            ("2.0.0", "1.9.0"),
            ("1.0.0", "1.0.0-beta.9"),
            ("1.0.0-beta.10", "1.0.0-beta.2"),
        ] {
            assert!(
                validate_install_version(&current(installed), "current", "demo", target)
                    .unwrap_err()
                    .starts_with("PLUGIN_DOWNGRADE_FORBIDDEN:")
            );
        }
        for (installed, target) in [
            ("1.0.0", "1.0.0"),
            ("1.0.0+build.9", "1.0.0+build.2"),
            ("1.0.0-beta.2", "1.0.0-beta.10"),
            ("unknown", "1.0.0"),
        ] {
            assert!(
                validate_install_version(&current(installed), "current", "demo", target).is_ok()
            );
        }
        assert!(
            validate_install_version(&current("1.0.0"), "stale", "demo", "1.1.0")
                .unwrap_err()
                .starts_with("CONFIG_REVISION_CONFLICT:")
        );
        let missing = json!({"revision":"current","plugins":[
            {"pluginId":null,"version":"0.0.0","source":"user","reasonCode":"PLUGIN_MANIFEST_INVALID"},
            {"pluginId":"demo","version":"0.0.0","source":"bundled","reasonCode":"PLUGIN_MIGRATION_FAILED"}
        ]});
        assert!(validate_install_version(&missing, "current", "demo", "1.0.0").is_ok());
    }
    #[test]
    fn cache_survives_new_state_and_keeps_readmes_bound_to_repository_and_commit() {
        let root =
            std::env::temp_dir().join(format!("sakura-market-cache-{}", uuid::Uuid::new_v4()));
        let state = MarketplaceState::new(root.clone());
        let commit = "e245e6710e7f7baf18711cb7c8c62ae8dd3ea2a3";
        let mut catalog = json!({"schema_version":1,"plugins":[{"id":"demo","repository":"https://github.com/author/plugin",
            "versions":[{"version":"1.0.0","commit":commit,"yanked":false,
            "manifest":{"id":"demo","version":"1.0.0"},"package":{"url":"https://example.com/plugin.zip","size":10}}]}]});
        let (_, url) = readme_addresses(&catalog, "demo", "1.0.0").unwrap();
        let document = json!({"url":url,"markdown":"# 使用方式\n\n缓存的正文。"});
        save_cache(&state.cache_root.join("catalog.json"), &catalog);
        save_cache(&state.readme_path(&url).unwrap(), &document);
        drop(state);
        let reopened = MarketplaceState::new(root.clone());
        assert_eq!(reopened.cached_catalog(), Some(catalog.clone()));
        assert_eq!(reopened.cached_readme(&url), Some(document.clone()));
        assert_eq!(reopened.cached_readmes(&catalog)[0]["document"], document);
        assert!(reopened
            .cached_readme(&url.replace("author/plugin", "author/other"))
            .is_none());
        assert!(reopened
            .cached_readme(&url.replace(commit, &"a".repeat(40)))
            .is_none());
        catalog["plugins"][0]["versions"][0]["yanked"] = json!(true);
        save_cache(&reopened.cache_root.join("catalog.json"), &catalog);
        let latest = reopened.cached_catalog().unwrap();
        assert!(selected_release(&latest, "demo", "1.0.0").is_err());
        assert!(reopened.cached_readmes(&latest).is_empty());
        fs::write(reopened.cache_root.join("catalog.json"), b"{truncated").unwrap();
        assert!(reopened.cached_catalog().is_none());
        save_cache(
            &reopened.cache_root.join("catalog.json"),
            &json!({"schema_version":2,"plugins":[]}),
        );
        assert!(reopened.cached_catalog().is_none());
        save_cache(
            &reopened.readme_path(&url).unwrap(),
            &json!({"url":"https://github.com/other/repo","markdown":"wrong"}),
        );
        assert!(reopened.cached_readme(&url).is_none());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn installation_uses_the_loaded_catalog_and_rejects_withdrawn_versions() {
        let mut catalog = json!({"plugins":[{"id":"demo","versions":[{"version":"1.0.0","yanked":false,
            "manifest":{"id":"demo","version":"1.0.0"},"package":{"url":"https://github.com/example/repo/releases/download/v1/plugin.zip","size":10}}]}]});
        assert!(selected_release(&catalog, "demo", "1.0.0").is_ok());
        assert!(selected_release(&catalog, "demo", "2.0.0").is_err());
        catalog["plugins"][0]["versions"][0]["yanked"] = json!(true);
        assert!(selected_release(&catalog, "demo", "1.0.0").is_err());
    }

    #[test]
    fn readme_uses_the_catalog_commit_and_rejects_non_repository_addresses() {
        let commit = "e245e6710e7f7baf18711cb7c8c62ae8dd3ea2a3";
        let mut catalog = json!({"plugins":[{"id":"demo","repository":"https://github.com/author/plugin",
            "versions":[{"version":"1.0.0","commit":commit,"yanked":false,
            "manifest":{"id":"demo","version":"1.0.0"},"package":{"url":"https://example.com/plugin.zip","size":10}}]}]});
        let (raw, page) = readme_addresses(&catalog, "demo", "1.0.0").unwrap();
        assert_eq!(
            raw,
            format!("https://raw.githubusercontent.com/author/plugin/{commit}/README.md")
        );
        assert_eq!(
            page,
            format!("https://github.com/author/plugin/blob/{commit}/README.md")
        );
        for repository in [
            "https://example.com/author/plugin",
            "https://github.com/author/plugin/tree/main",
            "https://github.com/author/plugin?ref=main",
            "https://github.com/author/%2e%2e",
        ] {
            catalog["plugins"][0]["repository"] = json!(repository);
            assert!(readme_addresses(&catalog, "demo", "1.0.0").is_err());
        }
        catalog["plugins"][0]["repository"] = json!("https://github.com/author/plugin");
        catalog["plugins"][0]["versions"][0]["commit"] = json!("main");
        assert!(readme_addresses(&catalog, "demo", "1.0.0").is_err());
    }
}
