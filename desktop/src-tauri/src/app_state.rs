use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::{json, Map, Value};
use tauri::http::header::{ACCESS_CONTROL_ALLOW_ORIGIN, CACHE_CONTROL, CONTENT_TYPE};
use tauri::http::{Request, Response, StatusCode};
use tauri::{AppHandle, Emitter, Manager, Runtime, State, UriSchemeContext};

use crate::brain_host::{
    BrainHostLaunchConfig, BrainHostStatus, BrainHostSupervisor, StatusCallback,
};

pub const BRAIN_STATUS_EVENT: &str = "sakura://brain-status";
const CHARACTER_ASSET_SCHEME: &str = "sakura-asset";
const MAX_CHARACTER_ASSET_BYTES: u64 = 32 * 1024 * 1024;

pub struct DesktopAppState {
    brain: BrainHostSupervisor,
}

impl DesktopAppState {
    pub fn start(app: AppHandle) -> Self {
        let callback: StatusCallback = Arc::new(move |status| {
            let _ = app.emit(BRAIN_STATUS_EVENT, status);
        });
        let brain =
            BrainHostSupervisor::start(BrainHostLaunchConfig::for_current_app(), Some(callback));
        Self { brain }
    }

    pub fn shutdown(&self) {
        self.brain.shutdown();
    }

    pub fn brain_status(&self) -> BrainHostStatus {
        self.brain.status()
    }

    fn pet_bootstrap(&self) -> Result<Value, String> {
        let status = self.brain.status();
        if !status.accepting_requests {
            return Err("Brain Host 尚未就绪".into());
        }
        let startup = self
            .brain
            .startup_state()
            .ok_or_else(|| "Brain Host 未返回启动状态".to_string())?;
        build_pet_bootstrap(&startup, status.session_generation)
    }
}

#[tauri::command]
pub fn brain_status(state: State<'_, DesktopAppState>) -> BrainHostStatus {
    state.brain_status()
}

#[tauri::command]
pub fn pet_bootstrap(state: State<'_, DesktopAppState>) -> Result<Value, String> {
    state.pet_bootstrap()
}

pub fn character_asset_protocol<R: Runtime>(
    context: UriSchemeContext<'_, R>,
    request: Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    let response = context
        .app_handle()
        .try_state::<DesktopAppState>()
        .and_then(|state| state.brain.startup_state())
        .ok_or_else(|| "Brain Host 启动状态不可用".to_string())
        .and_then(|startup| resolve_character_asset(&startup, request.uri().path()))
        .and_then(|path| read_character_asset(&path).map(|bytes| (path, bytes)));
    match response {
        Ok((path, bytes)) => Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, asset_content_type(&path))
            .header(CACHE_CONTROL, "private, max-age=3600")
            .header(ACCESS_CONTROL_ALLOW_ORIGIN, "*")
            .body(bytes)
            .expect("valid asset response"),
        Err(message) => Response::builder()
            .status(StatusCode::NOT_FOUND)
            .header(CONTENT_TYPE, "text/plain; charset=utf-8")
            .body(message.into_bytes())
            .expect("valid asset error response"),
    }
}

fn build_pet_bootstrap(startup: &Value, session_generation: u64) -> Result<Value, String> {
    let character = startup
        .get("character")
        .and_then(Value::as_object)
        .ok_or_else(|| "启动状态缺少角色信息".to_string())?;
    let character_id = required_text(character.get("id"), "character.id")?;
    let default_url = character_asset_url("/portrait/default");
    let expression_urls: Map<String, Value> = expression_entries(startup)
        .into_iter()
        .enumerate()
        .map(|(index, (name, _path))| {
            (
                name,
                Value::String(character_asset_url(&format!(
                    "/portrait/expression/{index}"
                ))),
            )
        })
        .collect();
    Ok(json!({
        "version": startup.get("version").and_then(Value::as_u64).unwrap_or(1),
        "sessionGeneration": session_generation,
        "character": {
            "id": character_id,
            "displayName": character.get("display_name").and_then(Value::as_str).unwrap_or(character_id),
            "initialMessage": character.get("initial_message").and_then(Value::as_str).unwrap_or(""),
            "replyTones": character.get("reply_tones").cloned().unwrap_or_else(|| json!([])),
            "portraitChoices": character.get("portrait_choices").cloned().unwrap_or_else(|| json!([])),
            "portraits": {
                "default": default_url,
                "expressions": expression_urls,
            },
        },
        "theme": startup.get("theme").cloned().unwrap_or_else(|| json!({})),
        "layout": startup.get("layout").cloned().unwrap_or_else(|| json!({})),
        "subtitle": startup.get("subtitle").cloned().unwrap_or_else(|| json!({})),
    }))
}

fn character_asset_url(path: &str) -> String {
    #[cfg(any(windows, target_os = "android"))]
    {
        format!("http://{CHARACTER_ASSET_SCHEME}.localhost{path}")
    }
    #[cfg(not(any(windows, target_os = "android")))]
    {
        format!("{CHARACTER_ASSET_SCHEME}://localhost{path}")
    }
}

fn resolve_character_asset(startup: &Value, request_path: &str) -> Result<PathBuf, String> {
    let base_dir = startup
        .get("base_dir")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .ok_or_else(|| "启动状态缺少 base_dir".to_string())?;
    let character = startup
        .get("character")
        .and_then(Value::as_object)
        .ok_or_else(|| "启动状态缺少角色信息".to_string())?;
    let character_id = required_text(character.get("id"), "character.id")?;
    let relative = if request_path == "/portrait/default" {
        character
            .get("portraits")
            .and_then(Value::as_object)
            .and_then(|portraits| portraits.get("default"))
            .and_then(Value::as_str)
            .ok_or_else(|| "角色缺少默认立绘".to_string())?
            .to_string()
    } else if let Some(index) = request_path.strip_prefix("/portrait/expression/") {
        let index = index
            .parse::<usize>()
            .map_err(|_| "表情立绘索引无效".to_string())?;
        expression_entries(startup)
            .get(index)
            .map(|(_name, path)| path.clone())
            .ok_or_else(|| "表情立绘不存在".to_string())?
    } else {
        return Err("未知角色资源".into());
    };
    let relative_path = Path::new(&relative);
    if relative_path.is_absolute() {
        return Err("角色资源必须使用相对路径".into());
    }
    let package_root = base_dir
        .join("characters")
        .join(character_id)
        .canonicalize()
        .map_err(|_| "角色包目录不存在".to_string())?;
    let asset = base_dir
        .join(relative_path)
        .canonicalize()
        .map_err(|_| "角色资源不存在".to_string())?;
    if !asset.starts_with(&package_root) || !asset.is_file() {
        return Err("角色资源路径超出当前角色包".into());
    }
    if !matches!(
        asset
            .extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase()
            .as_str(),
        "png" | "jpg" | "jpeg" | "webp" | "gif" | "svg"
    ) {
        return Err("角色资源格式不受支持".into());
    }
    Ok(asset)
}

fn expression_entries(startup: &Value) -> Vec<(String, String)> {
    let mut entries: Vec<(String, String)> = startup
        .pointer("/character/portraits/expressions")
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|mapping| mapping.iter())
        .filter_map(|(name, path)| path.as_str().map(|path| (name.clone(), path.to_string())))
        .collect();
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    entries
}

fn read_character_asset(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = path
        .metadata()
        .map_err(|_| "角色资源不可读取".to_string())?;
    if metadata.len() > MAX_CHARACTER_ASSET_BYTES {
        return Err("角色资源超过大小上限".into());
    }
    fs::read(path).map_err(|_| "角色资源不可读取".to_string())
}

fn asset_content_type(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "svg" => "image/svg+xml",
        _ => "image/png",
    }
}

fn required_text<'a>(value: Option<&'a Value>, field: &str) -> Result<&'a str, String> {
    value
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty())
        .ok_or_else(|| format!("启动状态缺少 {field}"))
}

#[cfg(test)]
mod tests {
    use tempfile::TempDir;

    use super::*;

    fn startup_fixture(temp: &TempDir) -> Value {
        let package = temp.path().join("characters/demo");
        fs::create_dir_all(&package).unwrap();
        fs::write(package.join("default.png"), b"png").unwrap();
        fs::write(package.join("smile.png"), b"png").unwrap();
        json!({
            "version": 1,
            "base_dir": temp.path(),
            "character": {
                "id": "demo",
                "display_name": "Demo",
                "initial_message": "hello",
                "reply_tones": ["calm"],
                "portrait_choices": ["smile"],
                "portraits": {
                    "default": "characters/demo/default.png",
                    "expressions": {"smile": "characters/demo/smile.png"},
                },
            },
            "theme": {"primary_color": "#123456"},
            "layout": {"portrait_scale_percent": 100},
            "subtitle": {"language": "zh"},
        })
    }

    #[test]
    fn windows_pet_bootstrap_uses_controlled_asset_urls_without_local_paths() {
        let temp = TempDir::new().unwrap();
        let dto = build_pet_bootstrap(&startup_fixture(&temp), 4).unwrap();

        assert_eq!(dto["sessionGeneration"], 4);
        assert_eq!(
            dto.pointer("/character/portraits/default").unwrap(),
            "http://sakura-asset.localhost/portrait/default"
        );
        assert!(!dto.to_string().contains(&temp.path().display().to_string()));
    }

    #[test]
    fn windows_character_asset_resolution_rejects_escape_and_unknown_paths() {
        let temp = TempDir::new().unwrap();
        let startup = startup_fixture(&temp);

        assert!(resolve_character_asset(&startup, "/portrait/default").is_ok());
        assert!(resolve_character_asset(&startup, "/portrait/expression/0").is_ok());
        assert!(resolve_character_asset(&startup, "/portrait/expression/99").is_err());

        let escaped = temp.path().join("outside.png");
        fs::write(&escaped, b"png").unwrap();
        let mut malicious = startup;
        malicious["character"]["portraits"]["default"] = json!("outside.png");
        assert!(resolve_character_asset(&malicious, "/portrait/default").is_err());
    }
}
