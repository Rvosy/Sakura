use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs::{self, File},
    io::{Cursor, Read},
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
    time::SystemTime,
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const CHARACTER_PROTOCOL: &str = "sakura-character";
pub const DEFAULT_PORTRAIT_KEY: &str = "__default__";
const MANIFEST_LIMIT: u64 = 256 * 1024;
const PORTRAIT_LIMIT: u64 = 8 * 1024 * 1024;
const MAX_PORTRAITS: usize = 64;
const ALPHA_MASK_CACHE_CAPACITY: usize = 8;
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
pub struct CharacterPresentation {
    pub schema_version: u32,
    pub generation_id: String,
    pub character_id: String,
    pub display_name: String,
    pub initial_message: String,
    pub theme_tokens: BTreeMap<String, String>,
    pub default_portrait_key: String,
    pub portrait_keys: Vec<String>,
    pub portrait_resource_ids: BTreeMap<String, String>,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PortraitMetadata {
    pub width: u32,
    pub height: u32,
    pub byte_length: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PortraitAlphaMask {
    pub width: u32,
    pub height: u32,
    pub alpha: Vec<u8>,
    visible_bounds: Option<[u32; 4]>,
}

impl PortraitAlphaMask {
    pub fn new(width: u32, height: u32, alpha: Vec<u8>) -> Self {
        let expected_len = usize::try_from(u64::from(width) * u64::from(height)).ok();
        let visible_bounds = (width > 0 && height > 0 && expected_len == Some(alpha.len()))
            .then(|| {
                let mut bounds: Option<(u32, u32, u32, u32)> = None;
                for (index, value) in alpha.iter().copied().enumerate() {
                    if value == 0 {
                        continue;
                    }
                    let x = index as u32 % width;
                    let y = index as u32 / width;
                    bounds = Some(match bounds {
                        None => (x, y, x, y),
                        Some((left, top, right, bottom)) => {
                            (left.min(x), top.min(y), right.max(x), bottom.max(y))
                        }
                    });
                }
                bounds.map(|(left, top, right, bottom)| {
                    [left, top, right - left + 1, bottom - top + 1]
                })
            })
            .flatten();
        Self {
            width,
            height,
            alpha,
            visible_bounds,
        }
    }

    pub fn source_size(&self) -> [u32; 2] {
        [self.width, self.height]
    }

    pub fn visible_bounds(&self) -> Option<[u32; 4]> {
        self.visible_bounds
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FrontendCharacterPresentation {
    #[serde(flatten)]
    pub presentation: CharacterPresentation,
    pub portrait_resource_urls: BTreeMap<String, String>,
    pub portrait_metadata: BTreeMap<String, PortraitMetadata>,
}

#[derive(Clone, Debug)]
pub struct CharacterResource {
    pub bytes: Vec<u8>,
    pub metadata: PortraitMetadata,
}

pub struct CharacterPresentationState {
    app_root: PathBuf,
    active: Mutex<Option<ActivePresentation>>,
    preview: Mutex<Option<PreviewPresentation>>,
}

#[derive(Clone)]
struct ActivePresentation {
    presentation: CharacterPresentation,
    portrait_metadata: BTreeMap<String, PortraitMetadata>,
    portrait_alpha_masks: Arc<Mutex<PortraitAlphaMaskCache>>,
}

#[derive(Clone)]
struct PreviewPresentation {
    window_generation: u64,
    revision: u64,
    active: ActivePresentation,
}

#[derive(Clone)]
struct CachedPortraitAlphaMask {
    key: String,
    path: PathBuf,
    metadata: PortraitMetadata,
    modified: Option<SystemTime>,
    mask: PortraitAlphaMask,
}

#[derive(Default)]
struct PortraitAlphaMaskCache {
    entries: VecDeque<CachedPortraitAlphaMask>,
}

impl PortraitAlphaMaskCache {
    fn get(
        &mut self,
        key: &str,
        path: &Path,
        metadata: PortraitMetadata,
        modified: Option<SystemTime>,
    ) -> Option<PortraitAlphaMask> {
        let index = self.entries.iter().position(|entry| {
            entry.key == key
                && entry.path == path
                && entry.metadata == metadata
                && entry.modified == modified
        })?;
        let entry = self.entries.remove(index)?;
        let mask = entry.mask.clone();
        self.entries.push_back(entry);
        Some(mask)
    }

    fn insert(&mut self, entry: CachedPortraitAlphaMask) {
        self.entries.retain(|cached| cached.key != entry.key);
        self.entries.push_back(entry);
        while self.entries.len() > ALPHA_MASK_CACHE_CAPACITY {
            self.entries.pop_front();
        }
    }
}

impl CharacterPresentationState {
    pub fn new(app_root: PathBuf) -> Self {
        Self {
            app_root,
            active: Mutex::new(None),
            preview: Mutex::new(None),
        }
    }

    fn prepare(
        &self,
        presentation: CharacterPresentation,
        current_generation: &str,
    ) -> Result<(FrontendCharacterPresentation, ActivePresentation), String> {
        presentation.validate(current_generation)?;
        let mut urls = BTreeMap::new();
        let mut metadata = BTreeMap::new();
        for key in &presentation.portrait_keys {
            let resource_id = presentation
                .portrait_resource_ids
                .get(key)
                .ok_or_else(|| "CHARACTER_PRESENTATION_INVALID".to_string())?;
            let (_, portrait_metadata) =
                resolve_portrait_path(&self.app_root, &presentation, resource_id)?;
            urls.insert(
                key.clone(),
                resource_url(&presentation.generation_id, resource_id),
            );
            metadata.insert(key.clone(), portrait_metadata);
        }
        let frontend = FrontendCharacterPresentation {
            presentation: presentation.clone(),
            portrait_resource_urls: urls,
            portrait_metadata: metadata.clone(),
        };
        let active = ActivePresentation {
            presentation,
            portrait_metadata: metadata,
            portrait_alpha_masks: Arc::new(Mutex::new(PortraitAlphaMaskCache::default())),
        };
        Ok((frontend, active))
    }

    pub fn activate(
        &self,
        presentation: CharacterPresentation,
        current_generation: &str,
    ) -> Result<FrontendCharacterPresentation, String> {
        let (frontend, active) = self.prepare(presentation, current_generation)?;
        *self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())? = Some(active);
        Ok(frontend)
    }

    pub fn preview_character(
        &self,
        character_id: &str,
        current_generation: &str,
        window_generation: u64,
        revision: u64,
    ) -> Result<(FrontendCharacterPresentation, bool), String> {
        let presentation =
            presentation_from_manifest(&self.app_root, character_id, current_generation)?;
        let (frontend, preview) = self.prepare(presentation, current_generation)?;
        let mut slot = self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?;
        let should_replace = slot.as_ref().is_none_or(|current| {
            (window_generation, revision) >= (current.window_generation, current.revision)
        });
        if should_replace {
            *slot = Some(PreviewPresentation {
                window_generation,
                revision,
                active: preview,
            });
        }
        Ok((frontend, should_replace))
    }

    pub fn active_presentation(&self) -> Result<Option<CharacterPresentation>, String> {
        self.active
            .lock()
            .map(|active| active.as_ref().map(|active| active.presentation.clone()))
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())
    }

    pub fn load_resource(
        &self,
        generation_hex: &str,
        resource_id: &str,
        current_generation: &str,
    ) -> Result<CharacterResource, String> {
        let active = self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .clone();
        let preview = self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .as_ref()
            .map(|preview| preview.active.clone());
        let candidates = [active, preview];
        let active = candidates
            .into_iter()
            .flatten()
            .find(|candidate| {
                candidate
                    .presentation
                    .portrait_resource_ids
                    .values()
                    .any(|value| value == resource_id)
            })
            .ok_or_else(|| "CHARACTER_RESOURCE_ID_UNKNOWN".to_string())?;
        let presentation = active.presentation;
        presentation.validate(current_generation)?;
        if generation_hex != hex_text(&presentation.generation_id) {
            return Err("CHARACTER_RESOURCE_GENERATION_STALE".to_string());
        }
        let key = presentation
            .portrait_resource_ids
            .iter()
            .find_map(|(key, candidate)| (candidate == resource_id).then_some(key))
            .ok_or_else(|| "CHARACTER_RESOURCE_ID_UNKNOWN".to_string())?;
        let expected = active
            .portrait_metadata
            .get(key)
            .copied()
            .ok_or_else(|| "CHARACTER_RESOURCE_CHANGED".to_string())?;
        let (path, resolved) = resolve_portrait_path(&self.app_root, &presentation, resource_id)?;
        if resolved != expected {
            return Err("CHARACTER_RESOURCE_CHANGED".to_string());
        }
        let bytes = fs::read(path).map_err(|_| "CHARACTER_RESOURCE_READ_FAILED".to_string())?;
        let actual = png_metadata(&bytes, expected.byte_length)?;
        if actual != expected {
            return Err("CHARACTER_RESOURCE_CHANGED".to_string());
        }
        Ok(CharacterResource {
            bytes,
            metadata: actual,
        })
    }

    pub fn active_portrait_alpha_mask(
        &self,
        portrait_key: &str,
        current_generation: &str,
    ) -> Result<PortraitAlphaMask, String> {
        self.portrait_alpha_mask(portrait_key, None, current_generation)
    }

    pub fn portrait_alpha_mask(
        &self,
        portrait_key: &str,
        resource_id: Option<&str>,
        current_generation: &str,
    ) -> Result<PortraitAlphaMask, String> {
        let active = self
            .active
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .clone();
        let preview = self
            .preview
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .as_ref()
            .map(|preview| preview.active.clone());
        let active = match resource_id {
            Some(resource_id) => [active, preview].into_iter().flatten().find(|candidate| {
                candidate
                    .presentation
                    .portrait_resource_ids
                    .get(portrait_key)
                    .is_some_and(|candidate| candidate == resource_id)
            }),
            None => active,
        }
        .ok_or_else(|| "CHARACTER_RESOURCE_NOT_READY".to_string())?;
        active.presentation.validate(current_generation)?;
        let resource_id = active
            .presentation
            .portrait_resource_ids
            .get(portrait_key)
            .ok_or_else(|| "CHARACTER_RESOURCE_KEY_UNKNOWN".to_string())?;
        let expected = active
            .portrait_metadata
            .get(portrait_key)
            .copied()
            .ok_or_else(|| "CHARACTER_RESOURCE_CHANGED".to_string())?;
        let (path, resolved) =
            resolve_portrait_path(&self.app_root, &active.presentation, resource_id)?;
        if resolved != expected {
            return Err("CHARACTER_RESOURCE_CHANGED".to_string());
        }
        let modified = path
            .metadata()
            .ok()
            .and_then(|metadata| metadata.modified().ok());
        if let Some(mask) = active
            .portrait_alpha_masks
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .get(portrait_key, &path, expected, modified)
        {
            return Ok(mask);
        }
        let bytes = fs::read(&path).map_err(|_| "CHARACTER_RESOURCE_READ_FAILED".to_string())?;
        if png_metadata(&bytes, expected.byte_length)? != expected {
            return Err("CHARACTER_RESOURCE_CHANGED".to_string());
        }
        let mask = decode_png_alpha_mask(&bytes, expected)?;
        active
            .portrait_alpha_masks
            .lock()
            .map_err(|_| "CHARACTER_RESOURCE_STATE_UNAVAILABLE".to_string())?
            .insert(CachedPortraitAlphaMask {
                key: portrait_key.to_string(),
                path,
                metadata: expected,
                modified,
                mask: mask.clone(),
            });
        Ok(mask)
    }
}

impl CharacterPresentation {
    pub fn from_value(value: &Value, current_generation: &str) -> Result<Self, String> {
        let presentation: Self = serde_json::from_value(value.clone())
            .map_err(|_| "CHARACTER_PRESENTATION_INVALID".to_string())?;
        presentation.validate(current_generation)?;
        Ok(presentation)
    }

    pub fn validate(&self, current_generation: &str) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err("CHARACTER_PRESENTATION_SCHEMA_UNSUPPORTED".to_string());
        }
        if self.generation_id != current_generation {
            return Err("CHARACTER_PRESENTATION_GENERATION_STALE".to_string());
        }
        if !safe_identifier(&self.character_id)
            || !bounded_text(&self.display_name, 128)
            || !bounded_text(&self.initial_message, 16 * 1024)
            || self.default_portrait_key != DEFAULT_PORTRAIT_KEY
        {
            return Err("CHARACTER_PRESENTATION_INVALID".to_string());
        }
        if self.theme_tokens.len() != THEME_KEYS.len()
            || THEME_KEYS.iter().any(|key| {
                self.theme_tokens
                    .get(*key)
                    .is_none_or(|value| !is_hex_color(value))
            })
        {
            return Err("CHARACTER_PRESENTATION_THEME_INVALID".to_string());
        }
        if self.portrait_keys.is_empty() || self.portrait_keys.len() > MAX_PORTRAITS {
            return Err("CHARACTER_PRESENTATION_PORTRAITS_INVALID".to_string());
        }
        let keys: BTreeSet<_> = self.portrait_keys.iter().collect();
        if keys.len() != self.portrait_keys.len()
            || !keys.contains(&self.default_portrait_key)
            || self.portrait_resource_ids.len() != keys.len()
        {
            return Err("CHARACTER_PRESENTATION_PORTRAITS_INVALID".to_string());
        }
        for key in &self.portrait_keys {
            if !bounded_text(key, 256)
                || self.portrait_resource_ids.get(key)
                    != Some(&portrait_resource_id(&self.character_id, key))
            {
                return Err("CHARACTER_PRESENTATION_RESOURCE_ID_INVALID".to_string());
            }
        }
        Ok(())
    }
}

pub fn portrait_resource_id(character_id: &str, portrait_key: &str) -> String {
    format!(
        "character-v1-{}-portrait-{}",
        hex_text(character_id),
        hex_text(portrait_key)
    )
}

fn resource_url(generation_id: &str, resource_id: &str) -> String {
    let generation = hex_text(generation_id);
    #[cfg(any(target_os = "windows", target_os = "android"))]
    {
        format!("http://{CHARACTER_PROTOCOL}.localhost/v1/{generation}/{resource_id}")
    }
    #[cfg(not(any(target_os = "windows", target_os = "android")))]
    {
        format!("{CHARACTER_PROTOCOL}://localhost/v1/{generation}/{resource_id}")
    }
}

fn hex_text(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(value.len() * 2);
    for byte in value.as_bytes() {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

fn safe_identifier(value: &str) -> bool {
    bounded_text(value, 128)
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
        && value != "."
        && value != ".."
}

fn bounded_text(value: &str, max: usize) -> bool {
    !value.trim().is_empty() && value.len() <= max && !value.chars().any(char::is_control)
}

fn is_hex_color(value: &str) -> bool {
    value.len() == 7
        && value.starts_with('#')
        && value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
}

fn resolve_portrait_path(
    app_root: &Path,
    presentation: &CharacterPresentation,
    resource_id: &str,
) -> Result<(PathBuf, PortraitMetadata), String> {
    let key = presentation
        .portrait_resource_ids
        .iter()
        .find_map(|(key, candidate)| (candidate == resource_id).then_some(key.as_str()))
        .ok_or_else(|| "CHARACTER_RESOURCE_ID_UNKNOWN".to_string())?;
    let characters_root = app_root
        .join("characters")
        .canonicalize()
        .map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?;
    let (package_root, manifest) = find_manifest(&characters_root, &presentation.character_id)?;
    let portrait = manifest
        .get("portrait")
        .and_then(Value::as_object)
        .ok_or_else(|| "CHARACTER_MANIFEST_INVALID".to_string())?;
    let relative = if key == DEFAULT_PORTRAIT_KEY {
        portrait.get("default").and_then(Value::as_str)
    } else {
        portrait
            .get("expressions")
            .and_then(Value::as_object)
            .and_then(|expressions| expressions.get(key))
            .and_then(Value::as_str)
    }
    .ok_or_else(|| "CHARACTER_RESOURCE_KEY_UNKNOWN".to_string())?;
    let relative_path = safe_relative_path(relative)?;
    let candidate = package_root.join(relative_path);
    let canonical = candidate
        .canonicalize()
        .map_err(|_| "CHARACTER_RESOURCE_MISSING".to_string())?;
    if !canonical.starts_with(&package_root) {
        return Err("CHARACTER_RESOURCE_SYMLINK_ESCAPE".to_string());
    }
    if canonical
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|extension| !extension.eq_ignore_ascii_case("png"))
    {
        return Err("CHARACTER_RESOURCE_MIME_REJECTED".to_string());
    }
    let file_metadata = canonical
        .metadata()
        .map_err(|_| "CHARACTER_RESOURCE_MISSING".to_string())?;
    if !file_metadata.is_file() || file_metadata.len() == 0 || file_metadata.len() > PORTRAIT_LIMIT
    {
        return Err("CHARACTER_RESOURCE_SIZE_REJECTED".to_string());
    }
    let metadata = inspect_png(&canonical, file_metadata.len())?;
    Ok((canonical, metadata))
}

fn find_manifest(characters_root: &Path, character_id: &str) -> Result<(PathBuf, Value), String> {
    for entry in
        fs::read_dir(characters_root).map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?
    {
        let entry = entry.map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?;
        let file_type = entry
            .file_type()
            .map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?;
        if !file_type.is_dir() || file_type.is_symlink() {
            continue;
        }
        let package_root = entry
            .path()
            .canonicalize()
            .map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?;
        if !package_root.starts_with(characters_root) {
            continue;
        }
        let manifest_path = package_root.join("character.json");
        let Ok(metadata) = manifest_path.metadata() else {
            continue;
        };
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MANIFEST_LIMIT {
            continue;
        }
        let bytes =
            fs::read(&manifest_path).map_err(|_| "CHARACTER_MANIFEST_INVALID".to_string())?;
        let manifest: Value =
            serde_json::from_slice(&bytes).map_err(|_| "CHARACTER_MANIFEST_INVALID".to_string())?;
        if manifest.get("id").and_then(Value::as_str) == Some(character_id) {
            return Ok((package_root, manifest));
        }
    }
    Err("CHARACTER_MANIFEST_NOT_FOUND".to_string())
}

fn safe_relative_path(value: &str) -> Result<PathBuf, String> {
    if value.is_empty() || value.contains('\\') || value.contains('\0') {
        return Err("CHARACTER_RESOURCE_PATH_REJECTED".to_string());
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err("CHARACTER_RESOURCE_PATH_REJECTED".to_string());
    }
    Ok(path.to_path_buf())
}

fn inspect_png(path: &Path, byte_length: u64) -> Result<PortraitMetadata, String> {
    let mut header = [0_u8; 33];
    File::open(path)
        .and_then(|mut file| file.read_exact(&mut header))
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    png_metadata(&header, byte_length)
}

fn png_metadata(bytes: &[u8], byte_length: u64) -> Result<PortraitMetadata, String> {
    const SIGNATURE: [u8; 8] = [137, 80, 78, 71, 13, 10, 26, 10];
    if bytes.len() < 33
        || bytes[..8] != SIGNATURE
        || bytes[8..12] != [0, 0, 0, 13]
        || bytes[12..16] != *b"IHDR"
    {
        return Err("CHARACTER_RESOURCE_MIME_REJECTED".to_string());
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().expect("PNG width slice"));
    let height = u32::from_be_bytes(bytes[20..24].try_into().expect("PNG height slice"));
    if width == 0
        || height == 0
        || width > 8192
        || height > 8192
        || u64::from(width) * u64::from(height) > 40_000_000
    {
        return Err("CHARACTER_RESOURCE_DIMENSIONS_REJECTED".to_string());
    }
    Ok(PortraitMetadata {
        width,
        height,
        byte_length,
    })
}

fn decode_png_alpha_mask(
    bytes: &[u8],
    expected: PortraitMetadata,
) -> Result<PortraitAlphaMask, String> {
    const MAX_DECODE_BYTES: usize = 192 * 1024 * 1024;
    let mut decoder = png::Decoder::new_with_limits(
        Cursor::new(bytes),
        png::Limits {
            bytes: MAX_DECODE_BYTES,
        },
    );
    decoder.set_transformations(png::Transformations::ALPHA | png::Transformations::STRIP_16);
    let mut reader = decoder
        .read_info()
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let output_size = reader
        .output_buffer_size()
        .filter(|size| *size <= MAX_DECODE_BYTES)
        .ok_or_else(|| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let mut decoded = vec![0_u8; output_size];
    let info = reader
        .next_frame(&mut decoded)
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    if info.width != expected.width
        || info.height != expected.height
        || info.bit_depth != png::BitDepth::Eight
        || !matches!(
            info.color_type,
            png::ColorType::GrayscaleAlpha | png::ColorType::Rgba
        )
    {
        return Err("CHARACTER_RESOURCE_DECODE_REJECTED".to_string());
    }
    let channels = info.color_type.samples();
    let pixel_count = usize::try_from(u64::from(info.width) * u64::from(info.height))
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let frame = &decoded[..info.buffer_size()];
    if frame.len() != pixel_count.saturating_mul(channels) {
        return Err("CHARACTER_RESOURCE_DECODE_REJECTED".to_string());
    }
    let alpha = frame
        .chunks_exact(channels)
        .map(|pixel| pixel[channels - 1])
        .collect();
    Ok(PortraitAlphaMask::new(info.width, info.height, alpha))
}

fn presentation_from_manifest(
    app_root: &Path,
    character_id: &str,
    generation_id: &str,
) -> Result<CharacterPresentation, String> {
    let characters_root = app_root
        .join("characters")
        .canonicalize()
        .map_err(|_| "CHARACTER_ROOT_UNAVAILABLE".to_string())?;
    let (_package_root, manifest) = find_manifest(&characters_root, character_id)?;
    let display_name = manifest
        .get("display_name")
        .and_then(Value::as_str)
        .ok_or_else(|| "CHARACTER_MANIFEST_INVALID".to_string())?;
    let initial_message = manifest
        .get("initial_message")
        .and_then(Value::as_str)
        .ok_or_else(|| "CHARACTER_MANIFEST_INVALID".to_string())?;
    let portrait = manifest
        .get("portrait")
        .and_then(Value::as_object)
        .ok_or_else(|| "CHARACTER_MANIFEST_INVALID".to_string())?;
    let mut expression_keys: Vec<String> = portrait
        .get("expressions")
        .and_then(Value::as_object)
        .ok_or_else(|| "CHARACTER_MANIFEST_INVALID".to_string())?
        .keys()
        .cloned()
        .collect();
    expression_keys.sort();
    let mut portrait_keys = vec![DEFAULT_PORTRAIT_KEY.to_string()];
    portrait_keys.extend(expression_keys);
    let theme = manifest.get("theme").and_then(Value::as_object);
    let source_names = [
        ("primary", "primary_color", "#4b9ac4"),
        ("primaryHover", "primary_hover_color", "#3b83aa"),
        ("accent", "accent_color", "#e36c96"),
        ("text", "text_color", "#27445a"),
        ("secondaryText", "secondary_text_color", "#54768b"),
        ("mutedText", "muted_text_color", "#7d99a9"),
        ("pageBackground", "page_background_color", "#f8fcfe"),
        ("panelBackground", "panel_background_color", "#eaf5fa"),
        ("inputBackground", "input_background_color", "#ffffff"),
        ("bubbleBackground", "bubble_background_color", "#e3f1f7"),
        ("border", "border_color", "#accfde"),
    ];
    let theme_tokens = source_names
        .into_iter()
        .map(|(public, source, fallback)| {
            let value = theme
                .and_then(|tokens| tokens.get(source))
                .and_then(Value::as_str)
                .filter(|value| is_hex_color(value))
                .unwrap_or(fallback);
            (public.to_string(), value.to_ascii_lowercase())
        })
        .collect();
    let presentation = CharacterPresentation {
        schema_version: 1,
        generation_id: generation_id.to_string(),
        character_id: character_id.to_string(),
        display_name: display_name.to_string(),
        initial_message: initial_message.to_string(),
        theme_tokens,
        default_portrait_key: DEFAULT_PORTRAIT_KEY.to_string(),
        portrait_resource_ids: portrait_keys
            .iter()
            .map(|key| (key.clone(), portrait_resource_id(character_id, key)))
            .collect(),
        portrait_keys,
    };
    presentation.validate(generation_id)?;
    Ok(presentation)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct FixtureRoot(PathBuf);

    impl FixtureRoot {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock should be after epoch")
                .as_nanos();
            let sequence = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "sakura-wp-3-03-resource-{}-{nonce}-{sequence}",
                std::process::id()
            ));
            fs::create_dir_all(path.join("characters/Fixture/portraits"))
                .expect("fixture directories should create");
            Self(path)
        }

        fn package(&self) -> PathBuf {
            self.0.join("characters/Fixture")
        }
    }

    impl Drop for FixtureRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn fixture_png(width: u32, height: u32) -> Vec<u8> {
        let mut bytes = vec![0_u8; 33];
        bytes[..8].copy_from_slice(&[137, 80, 78, 71, 13, 10, 26, 10]);
        bytes[8..12].copy_from_slice(&[0, 0, 0, 13]);
        bytes[12..16].copy_from_slice(b"IHDR");
        bytes[16..20].copy_from_slice(&width.to_be_bytes());
        bytes[20..24].copy_from_slice(&height.to_be_bytes());
        bytes
    }

    fn rgba_png(width: u32, height: u32, pixels: &[u8]) -> Vec<u8> {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, width, height);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            let mut writer = encoder.write_header().expect("PNG header should encode");
            writer
                .write_image_data(pixels)
                .expect("PNG pixels should encode");
        }
        bytes
    }

    #[test]
    fn png_alpha_mask_preserves_fully_transparent_pixels() {
        let pixels = [
            255, 0, 0, 0, 0, 255, 0, 1, 0, 0, 255, 127, 255, 255, 255, 255,
        ];
        let bytes = rgba_png(2, 2, &pixels);
        let metadata = PortraitMetadata {
            width: 2,
            height: 2,
            byte_length: bytes.len() as u64,
        };
        let mask = decode_png_alpha_mask(&bytes, metadata).expect("RGBA PNG should decode");
        assert_eq!(mask.source_size(), [2, 2]);
        assert_eq!(mask.alpha, vec![0, 1, 127, 255]);
    }

    #[test]
    fn portrait_alpha_mask_cache_is_bounded_and_promotes_recent_entries() {
        let metadata = PortraitMetadata {
            width: 1,
            height: 1,
            byte_length: 33,
        };
        let mut cache = PortraitAlphaMaskCache::default();
        for index in 0..ALPHA_MASK_CACHE_CAPACITY {
            cache.insert(CachedPortraitAlphaMask {
                key: format!("portrait-{index}"),
                path: PathBuf::from(format!("portrait-{index}.png")),
                metadata,
                modified: Some(UNIX_EPOCH),
                mask: PortraitAlphaMask::new(1, 1, vec![index as u8]),
            });
        }

        assert!(cache
            .get(
                "portrait-0",
                Path::new("portrait-0.png"),
                metadata,
                Some(UNIX_EPOCH),
            )
            .is_some());
        cache.insert(CachedPortraitAlphaMask {
            key: "portrait-new".to_string(),
            path: PathBuf::from("portrait-new.png"),
            metadata,
            modified: Some(UNIX_EPOCH),
            mask: PortraitAlphaMask::new(1, 1, vec![255]),
        });

        assert_eq!(cache.entries.len(), ALPHA_MASK_CACHE_CAPACITY);
        assert!(cache
            .get(
                "portrait-1",
                Path::new("portrait-1.png"),
                metadata,
                Some(UNIX_EPOCH),
            )
            .is_none());
        assert!(cache
            .get(
                "portrait-0",
                Path::new("portrait-0.png"),
                metadata,
                Some(UNIX_EPOCH),
            )
            .is_some());
    }

    fn write_manifest(root: &FixtureRoot, default_path: &str) {
        let manifest = serde_json::json!({
            "id": "Fixture",
            "display_name": "Fixture",
            "initial_message": "hello",
            "portrait": { "default": default_path, "expressions": {} },
            "theme": {}
        });
        fs::write(
            root.package().join("character.json"),
            serde_json::to_vec(&manifest).expect("manifest should serialize"),
        )
        .expect("manifest should write");
    }

    fn fixture_presentation(generation: &str) -> CharacterPresentation {
        let portrait_keys = vec![DEFAULT_PORTRAIT_KEY.to_string()];
        CharacterPresentation {
            schema_version: 1,
            generation_id: generation.to_string(),
            character_id: "Fixture".to_string(),
            display_name: "Fixture".to_string(),
            initial_message: "hello".to_string(),
            theme_tokens: THEME_KEYS
                .into_iter()
                .map(|key| (key.to_string(), "#123456".to_string()))
                .collect(),
            default_portrait_key: DEFAULT_PORTRAIT_KEY.to_string(),
            portrait_resource_ids: portrait_keys
                .iter()
                .map(|key| (key.clone(), portrait_resource_id("Fixture", key)))
                .collect(),
            portrait_keys,
        }
    }

    #[test]
    fn fixture_manifest_exposes_all_portraits_with_distinct_aspect_ratios() {
        let root = FixtureRoot::new();
        let app_root = root.0.clone();
        let manifest = serde_json::json!({
            "id": "Fixture",
            "display_name": "Fixture",
            "initial_message": "hello",
            "portrait": {
                "default": "portraits/wide.png",
                "expressions": { "tall": "portraits/tall.png" }
            },
            "theme": {}
        });
        fs::write(
            root.package().join("character.json"),
            serde_json::to_vec(&manifest).expect("manifest should serialize"),
        )
        .expect("manifest should write");
        fs::write(
            root.package().join("portraits/wide.png"),
            rgba_png(2, 1, &[0, 0, 0, 0, 255, 255, 255, 255]),
        )
        .expect("wide portrait should write");
        fs::write(
            root.package().join("portraits/tall.png"),
            rgba_png(1, 2, &[0, 0, 0, 0, 255, 255, 255, 255]),
        )
        .expect("tall portrait should write");

        let state = CharacterPresentationState::new(app_root.clone());
        let presentation = presentation_from_manifest(&app_root, "Fixture", "gen-fixture")
            .expect("fixture manifest should project");
        let frontend = state
            .activate(presentation.clone(), "gen-fixture")
            .expect("fixture resources should activate");
        assert_eq!(
            frontend.portrait_metadata.len(),
            presentation.portrait_keys.len()
        );
        let wide = frontend.portrait_metadata[DEFAULT_PORTRAIT_KEY];
        let tall = frontend.portrait_metadata["tall"];
        assert!(wide.width > wide.height);
        assert!(tall.width < tall.height);
        let mask = state
            .active_portrait_alpha_mask(DEFAULT_PORTRAIT_KEY, "gen-fixture")
            .expect("fixture alpha mask should decode");
        assert!(mask.alpha.iter().any(|alpha| *alpha == 0));
        assert!(mask.alpha.iter().any(|alpha| *alpha > 0));
        for resource_id in presentation.portrait_resource_ids.values() {
            let resource = state
                .load_resource(&hex_text("gen-fixture"), resource_id, "gen-fixture")
                .expect("every fixture portrait should load");
            assert_eq!(&resource.bytes[..8], &[137, 80, 78, 71, 13, 10, 26, 10]);
        }
        let serialized = serde_json::to_string(&frontend).expect("DTO should serialize");
        assert!(!serialized.contains("characters"));
        assert!(!serialized.contains(&app_root.to_string_lossy().to_string()));
    }

    #[test]
    fn character_visual_preview_exposes_target_assets_without_replacing_active_identity() {
        let root = FixtureRoot::new();
        write_manifest(&root, "portraits/default.png");
        fs::write(
            root.package().join("portraits/default.png"),
            rgba_png(2, 3, &[255; 24]),
        )
        .expect("active portrait should write");
        let preview_root = root.0.join("characters/Preview");
        fs::create_dir_all(preview_root.join("portraits"))
            .expect("preview directories should create");
        let preview_manifest = serde_json::json!({
            "id": "Preview",
            "display_name": "Preview",
            "initial_message": "preview",
            "portrait": { "default": "portraits/default.png", "expressions": {} },
            "theme": { "primary_color": "#654321" }
        });
        fs::write(
            preview_root.join("character.json"),
            serde_json::to_vec(&preview_manifest).expect("preview manifest should serialize"),
        )
        .expect("preview manifest should write");
        fs::write(
            preview_root.join("portraits/default.png"),
            rgba_png(3, 2, &[255; 24]),
        )
        .expect("preview portrait should write");

        let state = CharacterPresentationState::new(root.0.clone());
        let active = state
            .activate(fixture_presentation("generation-a"), "generation-a")
            .expect("active character should install");
        let (preview, accepted) = state
            .preview_character("Preview", "generation-a", 1, 2)
            .expect("preview character should project");
        assert!(accepted);
        let (_, older_accepted) = state
            .preview_character("Fixture", "generation-a", 1, 1)
            .expect("older preview may complete but must not replace the latest slot");
        assert!(!older_accepted);

        assert_eq!(
            state
                .active_presentation()
                .expect("active presentation should read")
                .expect("active presentation should exist")
                .character_id,
            "Fixture"
        );
        assert_eq!(preview.presentation.character_id, "Preview");
        assert_eq!(
            state
                .preview
                .lock()
                .expect("preview slot should read")
                .as_ref()
                .expect("preview slot should exist")
                .active
                .presentation
                .character_id,
            "Preview"
        );
        assert_ne!(
            active.presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY],
            preview.presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY]
        );
        for presentation in [&active, &preview] {
            let resource_id =
                &presentation.presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY];
            state
                .load_resource(&hex_text("generation-a"), resource_id, "generation-a")
                .expect("active and preview resources should both remain readable");
        }
        let preview_resource_id = &preview.presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY];
        let preview_mask = state
            .portrait_alpha_mask(
                DEFAULT_PORTRAIT_KEY,
                Some(preview_resource_id),
                "generation-a",
            )
            .expect("preview alpha mask should resolve by exact resource identity");
        assert_eq!(preview_mask.source_size(), [3, 2]);
        let active_mask = state
            .active_portrait_alpha_mask(DEFAULT_PORTRAIT_KEY, "generation-a")
            .expect("active alpha mask should remain unchanged");
        assert_eq!(active_mask.source_size(), [2, 3]);
    }

    #[test]
    fn real_sakura_and_navi_manifests_expose_every_safe_portrait_resource() {
        let app_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        // Character packages are user/runtime data and are intentionally ignored by git.
        // Keep this smoke test active for developer checkouts that have the real packages,
        // without making a clean CI checkout depend on untracked local assets.
        if !app_root.join("characters").is_dir() {
            return;
        }
        let state = CharacterPresentationState::new(app_root.clone());
        let mut default_ratios = Vec::new();
        for character_id in ["Sakura", "N.A.V.I."] {
            let generation = format!("real-{character_id}");
            let presentation =
                presentation_from_manifest(&app_root, character_id, &generation).unwrap();
            let frontend = state.activate(presentation, &generation).unwrap();
            assert_eq!(
                frontend.presentation.portrait_keys.len(),
                frontend.portrait_metadata.len()
            );
            assert_eq!(
                frontend.presentation.portrait_keys.len(),
                frontend.portrait_resource_urls.len()
            );
            for key in &frontend.presentation.portrait_keys {
                let url = &frontend.portrait_resource_urls[key];
                assert!(url.contains("sakura-character"));
                assert!(!url.contains("characters"));
                assert!(frontend.portrait_metadata[key].byte_length > 0);
            }
            let default = frontend.portrait_metadata[DEFAULT_PORTRAIT_KEY];
            default_ratios.push(f64::from(default.width) / f64::from(default.height));
        }
        assert!((default_ratios[0] - default_ratios[1]).abs() > 0.05);
    }

    #[test]
    fn wp_5_03_character_switch_unknown_resource_and_old_generation_are_rejected() {
        let root = FixtureRoot::new();
        write_manifest(&root, "portraits/default.png");
        fs::write(
            root.package().join("portraits/default.png"),
            fixture_png(32, 48),
        )
        .expect("portrait should write");
        let state = CharacterPresentationState::new(root.0.clone());
        let presentation = fixture_presentation("generation-1");
        state
            .activate(presentation.clone(), "generation-1")
            .expect("fixture should activate");
        assert_eq!(
            state
                .load_resource(&hex_text("generation-1"), "unknown", "generation-1")
                .unwrap_err(),
            "CHARACTER_RESOURCE_ID_UNKNOWN"
        );
        let resource_id = &presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY];
        assert_eq!(
            state
                .load_resource(&hex_text("generation-0"), resource_id, "generation-1")
                .unwrap_err(),
            "CHARACTER_RESOURCE_GENERATION_STALE"
        );
        assert_eq!(
            state
                .load_resource(&hex_text("generation-1"), resource_id, "generation-2")
                .unwrap_err(),
            "CHARACTER_PRESENTATION_GENERATION_STALE"
        );
    }

    #[test]
    fn traversal_wrong_extension_oversize_and_bad_decode_are_rejected() {
        let cases = [
            ("../outside.png", None, "CHARACTER_RESOURCE_PATH_REJECTED"),
            (
                "portraits/default.jpg",
                Some(fixture_png(32, 48)),
                "CHARACTER_RESOURCE_MIME_REJECTED",
            ),
            (
                "portraits/truncated.png",
                Some(vec![1, 2, 3]),
                "CHARACTER_RESOURCE_DECODE_REJECTED",
            ),
        ];
        for (path, bytes, expected) in cases {
            let root = FixtureRoot::new();
            write_manifest(&root, path);
            if let Some(bytes) = bytes {
                fs::write(root.package().join(path), bytes).expect("fixture asset should write");
            }
            let state = CharacterPresentationState::new(root.0.clone());
            assert_eq!(
                state
                    .activate(fixture_presentation("gen"), "gen")
                    .unwrap_err(),
                expected
            );
        }

        let root = FixtureRoot::new();
        write_manifest(&root, "portraits/large.png");
        let path = root.package().join("portraits/large.png");
        fs::write(&path, fixture_png(32, 48)).expect("large fixture header should write");
        File::options()
            .write(true)
            .open(path)
            .expect("large fixture should reopen")
            .set_len(PORTRAIT_LIMIT + 1)
            .expect("large fixture should resize");
        let state = CharacterPresentationState::new(root.0.clone());
        assert_eq!(
            state
                .activate(fixture_presentation("gen"), "gen")
                .unwrap_err(),
            "CHARACTER_RESOURCE_SIZE_REJECTED"
        );
    }

    #[test]
    fn invalid_dimensions_and_changed_resources_fail_closed() {
        let root = FixtureRoot::new();
        write_manifest(&root, "portraits/default.png");
        let path = root.package().join("portraits/default.png");
        fs::write(&path, fixture_png(32, 48)).expect("portrait should write");
        let state = CharacterPresentationState::new(root.0.clone());
        let presentation = fixture_presentation("gen");
        state
            .activate(presentation.clone(), "gen")
            .expect("fixture should activate");
        fs::write(&path, fixture_png(64, 48)).expect("portrait should mutate");
        assert_eq!(
            state
                .load_resource(
                    &hex_text("gen"),
                    &presentation.portrait_resource_ids[DEFAULT_PORTRAIT_KEY],
                    "gen",
                )
                .unwrap_err(),
            "CHARACTER_RESOURCE_CHANGED"
        );

        fs::write(&path, fixture_png(0, 48)).expect("invalid portrait should write");
        let fresh = CharacterPresentationState::new(root.0.clone());
        assert_eq!(
            fresh
                .activate(fixture_presentation("gen"), "gen")
                .unwrap_err(),
            "CHARACTER_RESOURCE_DIMENSIONS_REJECTED"
        );
    }

    #[test]
    fn portrait_metadata_limits_are_checked_without_decoding_large_rgba_surfaces() {
        let mut header = fixture_png(1, 1);
        let metadata = |bytes: &mut [u8], width: u32, height: u32| {
            bytes[16..20].copy_from_slice(&width.to_be_bytes());
            bytes[20..24].copy_from_slice(&height.to_be_bytes());
            png_metadata(&bytes[..33], 1024)
        };

        let accepted = metadata(&mut header, 8_192, 4_882).unwrap();
        assert_eq!([accepted.width, accepted.height], [8_192, 4_882]);
        assert_eq!(
            metadata(&mut header, 8_192, 4_883).unwrap_err(),
            "CHARACTER_RESOURCE_DIMENSIONS_REJECTED"
        );
        assert_eq!(
            metadata(&mut header, 8_193, 1).unwrap_err(),
            "CHARACTER_RESOURCE_DIMENSIONS_REJECTED"
        );
    }

    #[test]
    fn symlink_escape_is_rejected_when_fixture_symlinks_are_available() {
        let root = FixtureRoot::new();
        write_manifest(&root, "portraits/link.png");
        let outside = root.0.join("outside.png");
        fs::write(&outside, fixture_png(32, 48)).expect("outside portrait should write");
        let link = root.package().join("portraits/link.png");
        #[cfg(windows)]
        let linked = std::os::windows::fs::symlink_file(&outside, &link);
        #[cfg(unix)]
        let linked = std::os::unix::fs::symlink(&outside, &link);
        if linked.is_err() {
            return;
        }
        let state = CharacterPresentationState::new(root.0.clone());
        assert_eq!(
            state
                .activate(fixture_presentation("gen"), "gen")
                .unwrap_err(),
            "CHARACTER_RESOURCE_SYMLINK_ESCAPE"
        );
    }

    #[test]
    fn presentation_schema_rejects_extra_fields_and_forged_resource_ids() {
        let mut value = serde_json::to_value(fixture_presentation("gen"))
            .expect("presentation should serialize");
        value
            .as_object_mut()
            .expect("presentation should be object")
            .insert(
                "absolutePath".to_string(),
                Value::String("secret".to_string()),
            );
        assert_eq!(
            CharacterPresentation::from_value(&value, "gen").unwrap_err(),
            "CHARACTER_PRESENTATION_INVALID"
        );

        let mut forged = fixture_presentation("gen");
        forged.portrait_resource_ids.insert(
            DEFAULT_PORTRAIT_KEY.to_string(),
            "character-v1-forged-portrait-forged".to_string(),
        );
        assert_eq!(
            forged.validate("gen").unwrap_err(),
            "CHARACTER_PRESENTATION_RESOURCE_ID_INVALID"
        );
    }
}
