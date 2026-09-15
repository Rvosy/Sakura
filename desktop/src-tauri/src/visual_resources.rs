//! Authorized assets and installed renderer modules. No resource-type semantics.
use crate::character_presentation::{decode_png_alpha_mask, inspect_png, PortraitAlphaMask};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, VecDeque},
    fs,
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
    time::SystemTime,
};

pub const CHARACTER_PROTOCOL: &str = "sakura-character";
const ASSET_LIMIT: u64 = 64 * 1024 * 1024;
const THEME_KEYS: [&str; 11] = [
    "primary",
    "primaryHover",
    "accent",
    "text",
    "secondaryText",
    "mutedText",
    "pageBackground",
    "panelBackground",
    "inputBackground",
    "bubbleBackground",
    "border",
];

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VisualPresentation {
    pub binding_id: String,
    pub resource_id: String,
    pub r#type: String,
    pub provider_id: String,
    pub install_id: String,
    pub renderer: String,
    pub editor: Option<String>,
    pub data: Value,
    pub assets: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterPresentation {
    pub schema_version: u32,
    pub generation_id: String,
    pub character_id: String,
    pub display_name: String,
    pub initial_message: String,
    pub theme_tokens: BTreeMap<String, String>,
    pub visual: Option<VisualPresentation>,
    pub visual_reason_code: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FrontendCharacterPresentation {
    #[serde(flatten)]
    pub presentation: CharacterPresentation,
}

pub struct CharacterResource {
    pub bytes: Vec<u8>,
    pub content_type: &'static str,
}
#[derive(Clone)]
struct Active {
    presentation: CharacterPresentation,
    public: FrontendCharacterPresentation,
    root: Option<PathBuf>,
    masks: Arc<Mutex<VecDeque<CachedMask>>>,
}
#[derive(Clone)]
struct CachedMask {
    path: PathBuf,
    modified: Option<SystemTime>,
    size: u64,
    mask: PortraitAlphaMask,
}
#[derive(Clone)]
struct Editor {
    active: Active,
    scope_id: String,
    thumbnail: bool,
}
pub struct CharacterPresentationState {
    user_root: PathBuf,
    distribution_root: PathBuf,
    active: Mutex<Option<Active>>,
    preview: Mutex<Option<(u64, u64, Active)>>,
    editors: Mutex<BTreeMap<String, Editor>>,
}

impl CharacterPresentation {
    pub fn from_value(value: &Value, generation: &str) -> Result<Self, String> {
        let result: Self = serde_json::from_value(value.clone()).map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_PRESENTATION_INVALID", error)
        })?;
        result.validate(generation)?;
        Ok(result)
    }
    pub fn validate(&self, generation: &str) -> Result<(), String> {
        if self.schema_version != 2 {
            return Err("CHARACTER_PRESENTATION_SCHEMA_UNSUPPORTED".into());
        }
        if self.generation_id != generation {
            return Err("CHARACTER_PRESENTATION_GENERATION_STALE".into());
        }
        if !identifier(&self.character_id)
            || self.display_name.is_empty()
            || self.display_name.len() > 512
            || self.initial_message.len() > 65536
        {
            return Err("CHARACTER_PRESENTATION_INVALID".into());
        }
        if self.theme_tokens.len() != 11
            || THEME_KEYS
                .iter()
                .any(|key| !self.theme_tokens.contains_key(*key))
            || self.theme_tokens.values().any(|s| {
                s.len() != 7
                    || !s.starts_with('#')
                    || !s.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
            })
        {
            return Err("CHARACTER_PRESENTATION_THEME_INVALID".into());
        }
        if let Some(visual) = &self.visual {
            if !hex_id(&visual.binding_id)
                || !identifier(&visual.resource_id)
                || visual.assets.len() > 256
            {
                return Err("VISUAL_PRESENTATION_INVALID".into());
            }
            safe_relative(&visual.renderer)?;
            if let Some(editor) = &visual.editor {
                safe_relative(editor)?;
            }
            for (key, path) in &visual.assets {
                if key.is_empty() || key.len() > 1024 {
                    return Err("VISUAL_ASSET_INVALID".into());
                }
                safe_relative(path)?;
            }
        }
        Ok(())
    }
}

impl CharacterPresentationState {
    #[cfg(test)]
    pub fn new(user_root: PathBuf) -> Self {
        Self::with_distribution(user_root.clone(), user_root)
    }
    pub fn with_distribution(user_root: PathBuf, distribution_root: PathBuf) -> Self {
        Self {
            user_root,
            distribution_root,
            active: Mutex::new(None),
            preview: Mutex::new(None),
            editors: Mutex::new(BTreeMap::new()),
        }
    }
    fn prepare(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
    ) -> Result<(FrontendCharacterPresentation, Active), String> {
        presentation.validate(generation)?;
        let root = if presentation
            .visual
            .as_ref()
            .is_some_and(|v| !v.assets.is_empty())
        {
            Some(find_package(&self.user_root, &presentation.character_id)?)
        } else {
            None
        };
        if let (Some(visual), Some(root)) = (&presentation.visual, &root) {
            for path in visual.assets.values() {
                resolve(root, path)?;
            }
        }
        let mut public = presentation.clone();
        if let Some(visual) = &mut public.visual {
            let prefix = format!("module/{}/{}/", hex_text(generation), visual.binding_id);
            visual.renderer = protocol_url(&format!("{prefix}{}", visual.renderer));
            visual.editor = visual
                .editor
                .as_ref()
                .map(|entry| protocol_url(&format!("{prefix}{entry}")));
            visual.assets = visual
                .assets
                .keys()
                .map(|key| {
                    (
                        key.clone(),
                        protocol_url(&format!(
                            "v1/{}/{}-{}",
                            hex_text(generation),
                            visual.binding_id,
                            hex_text(key)
                        )),
                    )
                })
                .collect();
            // Physical installation and package paths never cross into the WebView.
            visual.install_id.clear();
        }
        let public = FrontendCharacterPresentation {
            presentation: public,
        };
        Ok((
            public.clone(),
            Active {
                presentation,
                public,
                root,
                masks: Arc::new(Mutex::new(VecDeque::new())),
            },
        ))
    }
    pub fn activate(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
    ) -> Result<FrontendCharacterPresentation, String> {
        presentation.validate(generation)?;
        if let Some(active) = self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
            .as_ref()
        {
            if active.presentation == presentation {
                return Ok(active.public.clone());
            }
        }
        let (public, active) = self.prepare(presentation, generation)?;
        *self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")? = Some(active);
        *self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")? = None;
        Ok(public)
    }
    pub fn preview_character(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
        window_generation: u64,
        revision: u64,
    ) -> Result<(FrontendCharacterPresentation, bool), String> {
        let (public, active) = self.prepare(presentation, generation)?;
        let mut slot = self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?;
        let accepted = slot
            .as_ref()
            .is_none_or(|(window, rev, _)| (window_generation, revision) >= (*window, *rev));
        if accepted {
            *slot = Some((window_generation, revision, active));
        }
        Ok((public, accepted))
    }
    pub fn active_presentation(&self) -> Result<Option<CharacterPresentation>, String> {
        Ok(self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
            .as_ref()
            .map(|a| a.presentation.clone()))
    }
    pub fn authorize_editor(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
        scope_id: &str,
        asset_root: &Path,
    ) -> Result<FrontendCharacterPresentation, String> {
        self.authorize_editor_surface(presentation, generation, scope_id, asset_root, false)
    }
    pub fn authorize_thumbnail(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
        scope_id: &str,
        asset_root: &Path,
    ) -> Result<FrontendCharacterPresentation, String> {
        self.authorize_editor_surface(presentation, generation, scope_id, asset_root, true)
    }
    fn authorize_editor_surface(
        &self,
        presentation: CharacterPresentation,
        generation: &str,
        scope_id: &str,
        asset_root: &Path,
        thumbnail: bool,
    ) -> Result<FrontendCharacterPresentation, String> {
        if scope_id.is_empty() {
            return Err("VISUAL_EDITOR_INVALID".into());
        }
        if presentation
            .visual
            .as_ref()
            .is_none_or(|v| !v.assets.is_empty() || v.editor.is_none())
        {
            return Err("VISUAL_EDITOR_INVALID".into());
        }
        let (public, mut active) = self.prepare(presentation, generation)?;
        let root = asset_root.canonicalize().map_err(|error| {
            crate::runtime_log::diagnostic_error("VISUAL_EDITOR_ROOT_INVALID", error)
        })?;
        let workspaces = self
            .user_root
            .join("data/character_studio/drafts")
            .canonicalize()
            .map_err(|error| {
                crate::runtime_log::diagnostic_error("VISUAL_EDITOR_ROOT_INVALID", error)
            })?;
        if !root.starts_with(workspaces) || !root.is_dir() {
            return Err("VISUAL_EDITOR_ROOT_INVALID".into());
        }
        active.root = Some(root);
        let mut editors = self
            .editors
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?;
        // One selected editor and one sequential thumbnail job may coexist.
        // Replacing either revokes only its own files and modules.
        editors.retain(|_, editor| editor.thumbnail != thumbnail);
        editors.insert(
            active
                .presentation
                .visual
                .as_ref()
                .unwrap()
                .binding_id
                .clone(),
            Editor {
                active,
                scope_id: scope_id.to_owned(),
                thumbnail,
            },
        );
        Ok(public)
    }
    pub fn clear_editors(&self) {
        if let Ok(mut editors) = self.editors.lock() {
            editors.clear();
        }
    }
    pub fn retain_editor_catalog(&self, items: &[Value]) {
        if let Ok(mut editors) = self.editors.lock() {
            editors.retain(|_, editor| {
                editor
                    .active
                    .presentation
                    .visual
                    .as_ref()
                    .is_some_and(|visual| {
                        items.iter().any(|item| {
                            item["pluginId"] == visual.provider_id
                                && item["type"] == visual.r#type
                                && item["scopeId"] == editor.scope_id
                                && item["reasonCode"] == "READY"
                        })
                    })
            });
        }
    }
    pub fn editor_asset_base(&self, generation: &str, binding: &str) -> String {
        protocol_url(&format!(
            "editor-assets/{}/{binding}/",
            hex_text(generation)
        ))
    }
    pub fn load_editor_asset(
        &self,
        generation_hex: &str,
        binding: &str,
        relative_hex: &str,
        generation: &str,
    ) -> Result<CharacterResource, String> {
        if generation_hex != hex_text(generation) {
            return Err("CHARACTER_RESOURCE_GENERATION_STALE".into());
        }
        let active = self
            .editors
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
            .get(binding)
            .map(|editor| editor.active.clone())
            .ok_or("VISUAL_BINDING_EXPIRED")?;
        active.presentation.validate(generation)?;
        let path = resolve(
            active.root.as_ref().ok_or("VISUAL_EDITOR_ROOT_INVALID")?,
            &unhex(relative_hex)?,
        )?;
        let content_type = match path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str()
        {
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "webp" => "image/webp",
            "gif" => "image/gif",
            "json" => "application/json",
            _ => "application/octet-stream",
        };
        Ok(CharacterResource {
            bytes: read_bounded(&path, ASSET_LIMIT)?,
            content_type,
        })
    }
    fn target(&self, binding: Option<&str>, generation: &str) -> Result<Active, String> {
        let active = self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
            .clone();
        let preview = self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
            .as_ref()
            .map(|(_, _, a)| a.clone());
        let editor = match binding {
            Some(id) => self
                .editors
                .lock()
                .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?
                .get(id)
                .map(|editor| editor.active.clone()),
            None => None,
        };
        let result = [active, preview, editor]
            .into_iter()
            .flatten()
            .find(|a| {
                binding.is_none_or(|id| {
                    a.presentation
                        .visual
                        .as_ref()
                        .is_some_and(|v| v.binding_id == id)
                })
            })
            .ok_or("CHARACTER_RESOURCE_ID_UNKNOWN")?;
        result.presentation.validate(generation)?;
        Ok(result)
    }
    pub fn protocol_response(
        &self,
        request: &tauri::http::Request<Vec<u8>>,
        current_generation: &str,
        report_error: impl FnOnce(&str, &str),
    ) -> tauri::http::Response<Vec<u8>> {
        use tauri::http::{header, Method, StatusCode};
        if request.method() != Method::GET || request.uri().query().is_some() {
            return protocol_error(
                StatusCode::BAD_REQUEST,
                "CHARACTER_RESOURCE_REQUEST_REJECTED",
            );
        }
        // Decode each URL path segment once. A literal percent in a filename
        // remains literal after decoding; encoded separators cannot change routes.
        let segments: Result<Vec<_>, _> = request
            .uri()
            .path()
            .trim_matches('/')
            .split('/')
            .map(|segment| percent_encoding::percent_decode_str(segment).decode_utf8())
            .collect();
        let Ok(segments) = segments else {
            return protocol_error(
                StatusCode::BAD_REQUEST,
                "CHARACTER_RESOURCE_REQUEST_REJECTED",
            );
        };
        if !((segments.len() == 3 && segments[0] == "v1")
            || (segments.len() >= 4 && segments[0] == "module")
            || (segments.len() == 4 && segments[0] == "editor-assets"))
            || segments[1].is_empty()
            || segments[2].is_empty()
            || segments
                .iter()
                .any(|segment| segment.contains(['/', '\\', '\0']))
        {
            return protocol_error(
                StatusCode::BAD_REQUEST,
                "CHARACTER_RESOURCE_REQUEST_REJECTED",
            );
        }
        let loaded = if segments[0] == "module" {
            self.load_module(
                &segments[1],
                &segments[2],
                &segments[3..].join("/"),
                current_generation,
            )
        } else if segments[0] == "editor-assets" {
            self.load_editor_asset(&segments[1], &segments[2], &segments[3], current_generation)
        } else {
            self.load_resource(&segments[1], &segments[2], current_generation)
        };
        match loaded {
            Ok(resource) => tauri::http::Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, resource.content_type)
                .header("Access-Control-Allow-Origin", "*")
                .header(header::CONTENT_LENGTH, resource.bytes.len().to_string())
                .header(header::CACHE_CONTROL, "no-store, max-age=0")
                .header("X-Content-Type-Options", "nosniff")
                .body(resource.bytes)
                .expect("validated character resource response"),
            Err(error) => {
                let stage = match segments[0].as_ref() {
                    "module" => "visual.module.read",
                    "editor-assets" => "studio.visual.asset.read",
                    _ => "visual.asset.read",
                };
                report_error(stage, &error);
                let code = error
                    .split_once(':')
                    .map_or(error.as_str(), |(code, _)| code);
                let status = if code.contains("GENERATION") {
                    StatusCode::GONE
                } else if code.contains("UNKNOWN") || code.contains("NOT_FOUND") {
                    StatusCode::NOT_FOUND
                } else {
                    StatusCode::UNPROCESSABLE_ENTITY
                };
                protocol_error(status, &code)
            }
        }
    }

    pub fn load_resource(
        &self,
        generation_hex: &str,
        resource_id: &str,
        generation: &str,
    ) -> Result<CharacterResource, String> {
        if generation_hex != hex_text(generation) {
            return Err("CHARACTER_RESOURCE_GENERATION_STALE".into());
        }
        let (binding, key) = resource_id
            .split_once('-')
            .ok_or("CHARACTER_RESOURCE_ID_UNKNOWN")?;
        let key = unhex(key)?;
        let active = self.target(Some(binding), generation)?;
        let path = asset_path(&active, &key)?;
        let content_type = match path
            .extension()
            .and_then(|x| x.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str()
        {
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "webp" => "image/webp",
            "json" => "application/json",
            _ => "application/octet-stream",
        };
        Ok(CharacterResource {
            bytes: read_bounded(&path, ASSET_LIMIT)?,
            content_type,
        })
    }
    pub fn load_module(
        &self,
        generation_hex: &str,
        binding: &str,
        entry: &str,
        generation: &str,
    ) -> Result<CharacterResource, String> {
        if generation_hex != hex_text(generation) {
            return Err("CHARACTER_RESOURCE_GENERATION_STALE".into());
        }
        let active = self.target(Some(binding), generation)?;
        let visual = active
            .presentation
            .visual
            .as_ref()
            .ok_or("VISUAL_NOT_BOUND")?;
        let (source, directory) = visual
            .install_id
            .strip_prefix("pi_")
            .and_then(|s| s.split_once('_'))
            .ok_or("VISUAL_INSTALL_INVALID")?;
        let directory = unhex(directory)?;
        if directory.contains(['/', '\\', ':']) || directory == "." || directory == ".." {
            return Err("VISUAL_INSTALL_INVALID".into());
        }
        let base = match source {
            "bundled" => self.distribution_root.join("plugins/builtin"),
            "user" => self.user_root.join("plugins/user"),
            _ => return Err("VISUAL_INSTALL_INVALID".into()),
        };
        let root = resolve_directory(&base, &directory)?;
        let path = resolve(&root, entry)?;
        if !matches!(
            path.extension().and_then(|x| x.to_str()),
            Some("js" | "mjs")
        ) {
            return Err("VISUAL_MODULE_REJECTED".into());
        }
        Ok(CharacterResource {
            bytes: read_bounded(&path, 4 * 1024 * 1024)?,
            content_type: "text/javascript; charset=utf-8",
        })
    }
    pub fn active_portrait_alpha_mask(
        &self,
        key: &str,
        generation: &str,
    ) -> Result<PortraitAlphaMask, String> {
        self.portrait_alpha_mask(key, None, generation)
    }
    pub fn portrait_alpha_mask(
        &self,
        key: &str,
        resource_id: Option<&str>,
        generation: &str,
    ) -> Result<PortraitAlphaMask, String> {
        let binding = resource_id.and_then(|id| id.split_once('-').map(|(binding, _)| binding));
        let active = self.target(binding, generation)?;
        let path = asset_path(&active, key)?;
        let file = path.metadata().map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_RESOURCE_MISSING", error)
        })?;
        let modified = file.modified().ok();
        let size = file.len();
        {
            let mut cache = active
                .masks
                .lock()
                .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?;
            if let Some(index) = cache.iter().position(|item| {
                item.path == path && item.modified == modified && item.size == size
            }) {
                let item = cache.remove(index).expect("matched cache entry");
                let mask = item.mask.clone();
                cache.push_back(item);
                return Ok(mask);
            }
        }
        let bytes = read_bounded(&path, 16 * 1024 * 1024)?;
        let metadata = inspect_png(&path, bytes.len() as u64)?;
        let mask = decode_png_alpha_mask(&bytes, metadata)?;
        let mut cache = active
            .masks
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE")?;
        cache.retain(|item| item.path != path);
        cache.push_back(CachedMask {
            path,
            modified,
            size,
            mask: mask.clone(),
        });
        while cache.len() > 8 {
            cache.pop_front();
        }
        Ok(mask)
    }
}

fn asset_path(active: &Active, key: &str) -> Result<PathBuf, String> {
    let relative = active
        .presentation
        .visual
        .as_ref()
        .and_then(|v| v.assets.get(key))
        .ok_or("CHARACTER_RESOURCE_KEY_UNKNOWN")?;
    resolve(
        active.root.as_ref().ok_or("CHARACTER_ROOT_UNAVAILABLE")?,
        relative,
    )
}
fn find_package(user: &Path, id: &str) -> Result<PathBuf, String> {
    let base = user.join("characters").canonicalize().map_err(|error| {
        crate::runtime_log::diagnostic_error("CHARACTER_ROOT_UNAVAILABLE", error)
    })?;
    for entry in fs::read_dir(&base).map_err(|error| {
        crate::runtime_log::diagnostic_error("CHARACTER_ROOT_UNAVAILABLE", error)
    })? {
        let entry = entry.map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_ROOT_UNAVAILABLE", error)
        })?;
        if !entry
            .file_type()
            .map_err(|error| {
                crate::runtime_log::diagnostic_error("CHARACTER_ROOT_UNAVAILABLE", error)
            })?
            .is_dir()
        {
            continue;
        }
        let root = entry.path().canonicalize().map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_ROOT_UNAVAILABLE", error)
        })?;
        if !root.starts_with(&base) {
            continue;
        }
        let Ok(path) = resolve(&root, "character.json") else {
            continue;
        };
        let Ok(bytes) = read_bounded(&path, 256 * 1024) else {
            continue;
        };
        let Ok(manifest) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        if manifest.get("id").and_then(Value::as_str) == Some(id) {
            return Ok(root);
        }
    }
    Err("CHARACTER_MANIFEST_NOT_FOUND".into())
}
fn resolve_directory(base: &Path, name: &str) -> Result<PathBuf, String> {
    let base = base
        .canonicalize()
        .map_err(|error| crate::runtime_log::diagnostic_error("VISUAL_INSTALL_INVALID", error))?;
    let root = base
        .join(name)
        .canonicalize()
        .map_err(|error| crate::runtime_log::diagnostic_error("VISUAL_INSTALL_INVALID", error))?;
    if !root.starts_with(base) || !root.is_dir() {
        return Err("VISUAL_INSTALL_INVALID".into());
    }
    Ok(root)
}
fn safe_relative(value: &str) -> Result<&Path, String> {
    let path = Path::new(value);
    if value.is_empty()
        || value.contains(['\\', ':', '\0'])
        || path
            .components()
            .any(|p| !matches!(p, Component::Normal(_)))
        || value
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err("CHARACTER_RESOURCE_PATH_REJECTED".into());
    }
    Ok(path)
}
fn resolve(root: &Path, value: &str) -> Result<PathBuf, String> {
    let path = root
        .join(safe_relative(value)?)
        .canonicalize()
        .map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_RESOURCE_MISSING", error)
        })?;
    if !path.starts_with(root) || !path.is_file() {
        return Err("CHARACTER_RESOURCE_PATH_REJECTED".into());
    }
    Ok(path)
}
fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    use std::io::Read;
    let file = fs::File::open(path).map_err(|error| {
        crate::runtime_log::diagnostic_error("CHARACTER_RESOURCE_READ_FAILED", error)
    })?;
    let mut bytes = Vec::new();
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| {
            crate::runtime_log::diagnostic_error("CHARACTER_RESOURCE_READ_FAILED", error)
        })?;
    if bytes.len() as u64 > limit {
        return Err("CHARACTER_RESOURCE_SIZE_REJECTED".into());
    }
    Ok(bytes)
}
fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s != "."
        && s != ".."
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
}
fn hex_id(s: &str) -> bool {
    s.len() == 32
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn hex_text(s: &str) -> String {
    s.as_bytes().iter().map(|b| format!("{b:02x}")).collect()
}
fn unhex(s: &str) -> Result<String, String> {
    if s.is_empty() || !s.len().is_multiple_of(2) || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("CHARACTER_RESOURCE_ID_UNKNOWN".into());
    }
    let bytes: Result<Vec<u8>, _> = (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16))
        .collect();
    String::from_utf8(bytes.map_err(|_| "CHARACTER_RESOURCE_ID_UNKNOWN")?)
        .map_err(|_| "CHARACTER_RESOURCE_ID_UNKNOWN".into())
}
pub fn protocol_error(
    status: tauri::http::StatusCode,
    code: &str,
) -> tauri::http::Response<Vec<u8>> {
    use tauri::http::header;
    tauri::http::Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store")
        .header("X-Content-Type-Options", "nosniff")
        .body(code.as_bytes().to_vec())
        .expect("static character protocol response")
}

fn protocol_url(path: &str) -> String {
    let base = if cfg!(any(target_os = "windows", target_os = "android")) {
        format!("http://{CHARACTER_PROTOCOL}.localhost/")
    } else {
        format!("{CHARACTER_PROTOCOL}://localhost/")
    };
    let mut url = tauri::Url::parse(&base).expect("static character protocol URL");
    url.path_segments_mut()
        .expect("hierarchical character protocol URL")
        .extend(path.split('/'));
    url.into()
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let id = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let nanos = SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "sakura-visual-test-{}-{nanos}-{id}",
                std::process::id()
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture(root: &Path) -> CharacterPresentation {
        fs::create_dir_all(root.join("characters/model/assets")).unwrap();
        fs::write(
            root.join("characters/model/character.json"),
            br#"{"id":"model"}"#,
        )
        .unwrap();
        fs::write(
            root.join("characters/model/assets/model.json"),
            b"{\"angle\":12}",
        )
        .unwrap();
        let plugin = root.join("plugins/builtin/numeric/frontend");
        fs::create_dir_all(&plugin).unwrap();
        fs::write(
            plugin.join("renderer.js"),
            b"export const mount = () => {};",
        )
        .unwrap();
        CharacterPresentation {
            schema_version: 2,
            generation_id: "g".into(),
            character_id: "model".into(),
            display_name: "模型".into(),
            initial_message: "你好".into(),
            theme_tokens: THEME_KEYS
                .iter()
                .map(|key| (key.to_string(), "#123456".into()))
                .collect(),
            visual_reason_code: "READY".into(),
            visual: Some(VisualPresentation {
                binding_id: "a".repeat(32),
                resource_id: "model-1".into(),
                r#type: "fixture.numeric@1".into(),
                provider_id: "fixture.numeric".into(),
                install_id: format!("pi_bundled_{}", hex_text("numeric")),
                renderer: "frontend/renderer.js".into(),
                editor: None,
                data: serde_json::json!({"maxAngle":30}),
                assets: BTreeMap::from([("model".into(), "assets/model.json".into())]),
            }),
        }
    }
    #[test]
    fn protocol_reads_unicode_and_space_module_paths_from_real_files() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let module_root = dir.path().join("plugins/builtin/numeric");
        for entry in ["前端/渲染器.js", "frontend/my editor.mjs"] {
            let path = module_root.join(entry);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, format!("export const label = '{entry}';")).unwrap();
        }
        let visual = input.visual.as_mut().unwrap();
        visual.renderer = "前端/渲染器.js".into();
        visual.editor = Some("frontend/my editor.mjs".into());
        let visual = state
            .activate(input, "g")
            .unwrap()
            .presentation
            .visual
            .unwrap();
        let urls = [visual.renderer, visual.editor.unwrap()];
        for origin in [
            "sakura-character://localhost",
            "http://sakura-character.localhost",
        ] {
            let responses = urls.each_ref().map(|url| {
                // WebViews parse module URLs before issuing a custom-protocol request.
                let url = tauri::Url::parse(url).unwrap();
                let request = tauri::http::Request::builder()
                    .uri(format!("{origin}{}", url.path()))
                    .body(Vec::new())
                    .unwrap();
                state.protocol_response(&request, "g", |_, _| {})
            });
            assert_eq!(
                responses
                    .each_ref()
                    .map(|response| response.status().as_u16()),
                [200, 200]
            );
            for (response, entry) in responses
                .into_iter()
                .zip(["前端/渲染器.js", "frontend/my editor.mjs"])
            {
                assert_eq!(
                    response.body(),
                    format!("export const label = '{entry}';").as_bytes()
                );
                assert_eq!(
                    response.headers()["Content-Type"],
                    "text/javascript; charset=utf-8"
                );
            }
        }
    }
    #[test]
    fn protocol_decodes_once_and_keeps_module_scope_and_path_checks() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let module_root = dir.path().join("plugins/builtin/numeric");
        let entry = "%2e%2e/渲染器 #%.mjs";
        fs::create_dir_all(module_root.join("%2e%2e")).unwrap();
        fs::write(
            module_root.join(entry),
            b"export const value = 'literal filename';",
        )
        .unwrap();
        fs::write(module_root.join("private.json"), b"private data").unwrap();
        fs::write(
            module_root.parent().unwrap().join("secret.js"),
            b"outside plugin",
        )
        .unwrap();
        input.visual.as_mut().unwrap().renderer = entry.into();
        let visual = state
            .activate(input.clone(), "g")
            .unwrap()
            .presentation
            .visual
            .unwrap();
        let request = tauri::http::Request::builder()
            .uri(&visual.renderer)
            .body(Vec::new())
            .unwrap();
        let response = state.protocol_response(&request, "g", |_, _| {});
        assert_eq!(response.status(), tauri::http::StatusCode::OK);
        assert_eq!(response.body(), b"export const value = 'literal filename';");
        let prefix = format!("sakura-character://localhost/module/67/{}/", "a".repeat(32));
        for path in [
            "../secret.js",
            "%2e%2e/secret.js",
            "%2e%2e%2fsecret.js",
            "frontend%2frenderer.js",
            "frontend/%5csecret.js",
            "frontend//renderer.js",
            "frontend/%00.js",
            "frontend/%FF.js",
            "private.json",
        ] {
            let request = tauri::http::Request::builder()
                .uri(format!("{prefix}{path}"))
                .body(Vec::new())
                .unwrap();
            let response = state.protocol_response(&request, "g", |_, _| {});
            assert!(!response.status().is_success(), "{path}");
            assert!(
                !String::from_utf8_lossy(response.body()).contains(dir.path().to_str().unwrap())
            );
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(
                module_root.parent().unwrap().join("secret.js"),
                module_root.join("escape.js"),
            )
            .unwrap();
            let request = tauri::http::Request::builder()
                .uri(format!("{prefix}escape.js"))
                .body(Vec::new())
                .unwrap();
            assert_eq!(
                state.protocol_response(&request, "g", |_, _| {}).status(),
                tauri::http::StatusCode::UNPROCESSABLE_ENTITY
            );
        }
        assert_eq!(
            state
                .protocol_response(&request, "next-generation", |_, _| {})
                .status(),
            tauri::http::StatusCode::GONE
        );
        input.visual = None;
        state.activate(input, "g").unwrap();
        assert_eq!(
            state.protocol_response(&request, "g", |_, _| {}).status(),
            tauri::http::StatusCode::NOT_FOUND
        );
    }
    #[test]
    fn resource_io_failure_preserves_system_error_without_a_private_path() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        state.activate(fixture(dir.path()), "g").unwrap();
        fs::remove_file(dir.path().join("characters/model/assets/model.json")).unwrap();
        let error = state
            .load_resource(
                "67",
                &format!("{}-{}", "a".repeat(32), hex_text("model")),
                "g",
            )
            .err()
            .unwrap();
        let source =
            fs::canonicalize(dir.path().join("characters/model/assets/model.json")).unwrap_err();
        assert!(error.starts_with("CHARACTER_RESOURCE_MISSING: "));
        assert!(error.contains(&source.to_string()));
        assert!(!error.contains(dir.path().to_str().unwrap()));
    }

    #[test]
    fn numeric_assets_and_installed_modules_are_scoped_and_old_tokens_expire() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let public = state.activate(input.clone(), "g").unwrap();
        assert!(!serde_json::to_string(&public)
            .unwrap()
            .contains("assets/model.json"));
        let id = format!("{}-{}", "a".repeat(32), hex_text("model"));
        assert_eq!(
            state.load_resource("67", &id, "g").unwrap().content_type,
            "application/json"
        );
        assert!(state
            .load_module("67", &"a".repeat(32), "frontend/renderer.js", "g")
            .unwrap()
            .content_type
            .starts_with("text/javascript"));
        assert!(state
            .load_module("67", &"a".repeat(32), "../character.json", "g")
            .is_err());
        assert!(state.load_resource("00", &id, "g").is_err());
        input.visual = None;
        state.activate(input, "g").unwrap();
        assert!(state.load_resource("67", &id, "g").is_err());
        assert!(state
            .load_module("67", &"a".repeat(32), "frontend/renderer.js", "g")
            .is_err());
    }
    #[test]
    fn unsafe_asset_paths_and_package_scripts_cannot_be_loaded_as_modules() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        for path in [
            "../secret",
            "C:/secret",
            "assets/file:secret",
            "./assets/model.json",
            "assets/../character.json",
        ] {
            let mut input = fixture(dir.path());
            input
                .visual
                .as_mut()
                .unwrap()
                .assets
                .insert("bad".into(), path.into());
            assert!(state.activate(input, "g").is_err(), "{path}");
        }
        let input = fixture(dir.path());
        fs::write(
            dir.path().join("characters/model/assets/renderer.js"),
            b"untrusted package code",
        )
        .unwrap();
        state.activate(input, "g").unwrap();
        assert!(state
            .load_module("67", &"a".repeat(32), "assets/renderer.js", "g")
            .is_err());
    }
    #[test]
    fn editor_modules_stop_loading_after_scope_revocation_or_window_close() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let visual = input.visual.as_mut().unwrap();
        visual.assets.clear();
        visual.editor = Some("frontend/renderer.js".into());
        let root = dir
            .path()
            .join("data/character_studio/drafts/model/package");
        fs::create_dir_all(&root).unwrap();
        state
            .authorize_editor(input.clone(), "g", "scope-a", &root)
            .unwrap();
        assert!(state
            .load_module("67", &"a".repeat(32), "frontend/renderer.js", "g")
            .is_ok());
        state.retain_editor_catalog(&[]);
        assert!(state
            .load_module("67", &"a".repeat(32), "frontend/renderer.js", "g")
            .is_err());
        state
            .authorize_editor(input, "g", "scope-b", &root)
            .unwrap();
        state.clear_editors();
        assert!(state
            .load_module("67", &"a".repeat(32), "frontend/renderer.js", "g")
            .is_err());
    }
    #[test]
    fn thumbnail_authorization_does_not_revoke_the_selected_editor() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let visual = input.visual.as_mut().unwrap();
        visual.assets.clear();
        visual.editor = Some("frontend/renderer.js".into());
        let root = dir
            .path()
            .join("data/character_studio/drafts/model/package");
        fs::create_dir_all(&root).unwrap();
        state
            .authorize_editor(input.clone(), "g", "scope", &root)
            .unwrap();
        input.visual.as_mut().unwrap().binding_id = "b".repeat(32);
        state
            .authorize_thumbnail(input.clone(), "g", "scope", &root)
            .unwrap();
        let available = |letter: &str| {
            state
                .load_module("67", &letter.repeat(32), "frontend/renderer.js", "g")
                .is_ok()
        };
        assert!(available("a") && available("b"));
        input.visual.as_mut().unwrap().binding_id = "c".repeat(32);
        state
            .authorize_thumbnail(input.clone(), "g", "scope", &root)
            .unwrap();
        assert!(available("a") && available("c") && !available("b"));
        input.visual.as_mut().unwrap().binding_id = "d".repeat(32);
        state.authorize_editor(input, "g", "scope", &root).unwrap();
        assert!(available("c") && available("d") && !available("a"));
        state.clear_editors();
        assert!(!available("c") && !available("d"));
    }
    #[test]
    fn png_alpha_service_preserves_transparency_without_reading_portrait_manifest() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, 2, 1);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            encoder
                .write_header()
                .unwrap()
                .write_image_data(&[255, 0, 0, 0, 0, 255, 0, 255])
                .unwrap();
        }
        // Exercise the encoded size boundary while retaining a tiny alpha-mask fixture.
        bytes.resize(16 * 1024 * 1024, 0);
        fs::write(dir.path().join("characters/model/assets/image.png"), &bytes).unwrap();
        input
            .visual
            .as_mut()
            .unwrap()
            .assets
            .insert("surface".into(), "assets/image.png".into());
        state.activate(input, "g").unwrap();
        let mask = state.active_portrait_alpha_mask("surface", "g").unwrap();
        assert_eq!(mask.alpha, vec![0, 255]);
        assert_eq!(mask.visible_bounds(), Some([1, 0, 1, 1]));
        assert_eq!(
            state.active_portrait_alpha_mask("surface", "g").unwrap(),
            mask
        );
        bytes.push(0);
        fs::write(dir.path().join("characters/model/assets/image.png"), &bytes).unwrap();
        assert!(state.active_portrait_alpha_mask("surface", "g").is_err());
    }
    #[test]
    fn editor_assets_are_generic_bounded_and_revoked_with_the_provider() {
        let dir = Fixture::new();
        let state = CharacterPresentationState::new(dir.path().into());
        let mut input = fixture(dir.path());
        let visual = input.visual.as_mut().unwrap();
        visual.assets.clear();
        visual.editor = Some("frontend/renderer.js".into());
        let provider = visual.provider_id.clone();
        let resource_type = visual.r#type.clone();
        let root = dir
            .path()
            .join("data/character_studio/drafts/model/package");
        fs::create_dir_all(root.join("textures")).unwrap();
        fs::write(root.join("model.json"), b"{}").unwrap();
        fs::write(root.join("textures/图片 #%.bin"), b"model bytes").unwrap();
        fs::write(root.join("script.js"), b"package script").unwrap();
        fs::write(root.parent().unwrap().join("secret"), b"secret").unwrap();
        assert!(state
            .authorize_editor(input.clone(), "g", "scope", dir.path())
            .is_err());
        state.authorize_editor(input, "g", "scope", &root).unwrap();
        let binding = "a".repeat(32);
        let load = |path: &str| state.load_editor_asset("67", &binding, &hex_text(path), "g");
        assert_eq!(load("model.json").unwrap().content_type, "application/json");
        assert_eq!(load("textures/图片 #%.bin").unwrap().bytes, b"model bytes");
        assert_eq!(
            load("script.js").unwrap().content_type,
            "application/octet-stream"
        );
        for path in [
            "../secret",
            "textures/../../secret",
            "C:/secret",
            "./model.json",
            "textures\\image.png",
        ] {
            assert!(load(path).is_err());
        }
        let oversized = fs::File::create(root.join("large.bin")).unwrap();
        oversized.set_len(ASSET_LIMIT + 1).unwrap();
        assert!(load("large.bin").is_err());
        assert!(state
            .load_editor_asset("67", &binding, &hex_text("model.json"), "next")
            .is_err());
        state.retain_editor_catalog(&[serde_json::json!({"pluginId": provider, "type": resource_type, "scopeId": "scope", "reasonCode": "READY"})]);
        assert!(load("model.json").is_ok());
        state.retain_editor_catalog(&[]);
        assert!(load("model.json").is_err());
        assert!(state
            .load_module("67", &binding, "frontend/renderer.js", "g")
            .is_err());
    }
}
