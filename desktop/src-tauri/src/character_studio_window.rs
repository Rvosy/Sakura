use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

use serde::Serialize;
use tauri::webview::Color;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use uuid::Uuid;

use crate::product_shell::{PetTopmostState, SETTINGS_WINDOW_LABEL};

pub const STUDIO_WINDOW_LABEL: &str = "studio";
pub const STUDIO_CLOSE_REQUESTED_EVENT: &str = "sakura://studio-close-requested";
pub const STUDIO_EXIT_REQUESTED_EVENT: &str = "sakura://studio-exit-requested";
pub const CHARACTER_CATALOG_CHANGED_EVENT: &str = "sakura://character-catalog-changed";
pub const STUDIO_PREVIEW_PROTOCOL: &str = "sakura-studio-media";
const PREVIEW_TTL: Duration = Duration::from_secs(5 * 60);
const PREVIEW_LIMIT: u64 = 20 * 1024 * 1024;

#[derive(Default)]
struct StudioSession {
    initial_character_id: String,
    generation_id: String,
    close_authorized: bool,
    settings_was_visible: bool,
    exiting: bool,
}

struct PreviewResource {
    path: PathBuf,
    media_type: String,
    byte_length: u64,
    generation_id: String,
    expires_at: Instant,
}

pub struct CharacterStudioWindowState {
    session: Mutex<StudioSession>,
    previews: Mutex<HashMap<String, PreviewResource>>,
}

impl Default for CharacterStudioWindowState {
    fn default() -> Self {
        Self {
            session: Mutex::new(StudioSession::default()),
            previews: Mutex::new(HashMap::new()),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewRegistration {
    pub preview_url: String,
    pub media_type: String,
    pub byte_length: u64,
}

#[derive(Debug)]
pub struct LoadedPreview {
    pub bytes: Vec<u8>,
    pub media_type: String,
}

impl CharacterStudioWindowState {
    pub fn initial_character_id(&self) -> Result<String, String> {
        self.session
            .lock()
            .map(|session| session.initial_character_id.clone())
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())
    }

    pub fn bind_generation(&self, generation_id: &str) -> Result<(), String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())?;
        if session.generation_id != generation_id {
            session.generation_id = generation_id.to_string();
            self.previews
                .lock()
                .map_err(|_| "STUDIO_PREVIEW_STATE_UNAVAILABLE".to_string())?
                .clear();
        }
        Ok(())
    }

    pub fn authorize_close(&self) -> Result<(), String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())?;
        session.close_authorized = true;
        Ok(())
    }

    pub fn consume_close_authorization(&self) -> Result<bool, String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())?;
        let authorized = session.close_authorized;
        session.close_authorized = false;
        Ok(authorized)
    }

    pub fn mark_exiting(&self) {
        if let Ok(mut session) = self.session.lock() {
            session.exiting = true;
            session.close_authorized = true;
        }
        if let Ok(mut previews) = self.previews.lock() {
            previews.clear();
        }
    }

    pub fn register_preview(
        &self,
        path: &Path,
        media_type: &str,
        byte_length: u64,
        generation_id: &str,
    ) -> Result<PreviewRegistration, String> {
        if !path.is_absolute()
            || path.is_symlink()
            || !path.is_file()
            || byte_length > PREVIEW_LIMIT
            || !matches!(
                media_type,
                "audio/flac" | "audio/mpeg" | "audio/ogg" | "audio/wav"
            )
        {
            return Err("STUDIO_PREVIEW_DESCRIPTOR_INVALID".to_string());
        }
        let metadata = path
            .metadata()
            .map_err(|_| "STUDIO_PREVIEW_DESCRIPTOR_INVALID".to_string())?;
        if metadata.len() != byte_length {
            return Err("STUDIO_PREVIEW_DESCRIPTOR_INVALID".to_string());
        }
        let token = Uuid::new_v4().simple().to_string();
        let resource = PreviewResource {
            path: path.to_path_buf(),
            media_type: media_type.to_string(),
            byte_length,
            generation_id: generation_id.to_string(),
            expires_at: Instant::now() + PREVIEW_TTL,
        };
        let mut previews = self
            .previews
            .lock()
            .map_err(|_| "STUDIO_PREVIEW_STATE_UNAVAILABLE".to_string())?;
        previews.retain(|_, item| item.expires_at > Instant::now());
        previews.insert(token.clone(), resource);
        let preview_url = if cfg!(target_os = "windows") {
            format!("http://{STUDIO_PREVIEW_PROTOCOL}.localhost/v1/{token}")
        } else {
            format!("{STUDIO_PREVIEW_PROTOCOL}://localhost/v1/{token}")
        };
        Ok(PreviewRegistration {
            preview_url,
            media_type: media_type.to_string(),
            byte_length,
        })
    }

    pub fn load_preview(&self, token: &str, generation_id: &str) -> Result<LoadedPreview, String> {
        let mut previews = self
            .previews
            .lock()
            .map_err(|_| "STUDIO_PREVIEW_STATE_UNAVAILABLE".to_string())?;
        previews.retain(|_, item| item.expires_at > Instant::now());
        let resource = previews
            .get(token)
            .ok_or_else(|| "STUDIO_PREVIEW_NOT_FOUND".to_string())?;
        if resource.generation_id != generation_id {
            return Err("STUDIO_PREVIEW_GENERATION_STALE".to_string());
        }
        let metadata = resource
            .path
            .metadata()
            .map_err(|_| "STUDIO_PREVIEW_NOT_FOUND".to_string())?;
        if metadata.len() != resource.byte_length || metadata.len() > PREVIEW_LIMIT {
            return Err("STUDIO_PREVIEW_CHANGED".to_string());
        }
        let bytes =
            fs::read(&resource.path).map_err(|_| "STUDIO_PREVIEW_READ_FAILED".to_string())?;
        Ok(LoadedPreview {
            bytes,
            media_type: resource.media_type.clone(),
        })
    }

    fn begin_session(
        &self,
        initial_character_id: &str,
        settings_was_visible: bool,
    ) -> Result<(), String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())?;
        *session = StudioSession {
            initial_character_id: initial_character_id.to_string(),
            settings_was_visible,
            ..StudioSession::default()
        };
        Ok(())
    }

    fn finish_session(&self) -> Result<(bool, bool), String> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| "STUDIO_WINDOW_STATE_UNAVAILABLE".to_string())?;
        let restore_settings = session.settings_was_visible;
        let exiting = session.exiting;
        *session = StudioSession::default();
        self.previews
            .lock()
            .map_err(|_| "STUDIO_PREVIEW_STATE_UNAVAILABLE".to_string())?
            .clear();
        Ok((restore_settings, exiting))
    }
}

pub fn validate_studio_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != STUDIO_WINDOW_LABEL {
        return Err("STUDIO_WINDOW_REQUIRED".to_string());
    }
    Ok(())
}

pub fn show_or_focus(
    app: &AppHandle,
    initial_character_id: &str,
    state: &CharacterStudioWindowState,
    topmost: &PetTopmostState,
) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(STUDIO_WINDOW_LABEL) {
        if window.is_minimized().map_err(|error| error.to_string())? {
            window.unminimize().map_err(|error| error.to_string())?;
        }
        window.show().map_err(|error| error.to_string())?;
        return window.set_focus().map_err(|error| error.to_string());
    }
    let settings = app.get_webview_window(SETTINGS_WINDOW_LABEL);
    let settings_was_visible = settings
        .as_ref()
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    // Validate the persisted preference before changing either native window.
    topmost.enabled()?;
    let studio = WebviewWindowBuilder::new(
        app,
        STUDIO_WINDOW_LABEL,
        WebviewUrl::App("studio/index.html".into()),
    )
    .title("Sakura 角色工坊")
    .background_color(Color(248, 252, 254, 255))
    .visible(false)
    .inner_size(1200.0, 800.0)
    .min_inner_size(900.0, 640.0)
    .resizable(true)
    .maximizable(true)
    .minimizable(true)
    .decorations(true)
    .devtools(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .build()
    .map_err(|error| format!("STUDIO_WINDOW_CREATE_FAILED: {error}"))?;
    if let Err(error) = state.begin_session(initial_character_id, settings_was_visible) {
        let _ = studio.destroy();
        return Err(error);
    }
    let prepare_result = (|| {
        if let Some(settings) = settings {
            settings.hide().map_err(|error| error.to_string())?;
        }
        if let Some(pet) = app.get_webview_window("main") {
            pet.set_always_on_top(false)
                .map_err(|_| "PET_TOPMOST_APPLY_FAILED".to_string())?;
        }
        Ok(())
    })();
    if let Err(error) = prepare_result {
        // Destroyed handling restores any setting visibility or topmost change that succeeded.
        let _ = studio.destroy();
        return Err(error);
    }
    Ok(())
}

pub fn restore_after_destroyed(
    app: &AppHandle,
    state: &CharacterStudioWindowState,
    topmost: &PetTopmostState,
) -> Result<(), String> {
    let (restore_settings, exiting) = state.finish_session()?;
    if exiting {
        return Ok(());
    }
    if let Some(pet) = app.get_webview_window("main") {
        pet.set_always_on_top(topmost.enabled()?)
            .map_err(|_| "PET_TOPMOST_APPLY_FAILED".to_string())?;
    }
    if restore_settings {
        if let Some(settings) = app.get_webview_window(SETTINGS_WINDOW_LABEL) {
            settings.show().map_err(|error| error.to_string())?;
            settings.set_focus().map_err(|error| error.to_string())?;
            let _ = settings.emit(CHARACTER_CATALOG_CHANGED_EVENT, ());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_registry_expires_and_is_generation_scoped() {
        let state = CharacterStudioWindowState::default();
        let path = std::env::temp_dir().join(format!("studio-preview-{}.wav", Uuid::new_v4()));
        fs::write(&path, b"audio").unwrap();
        let registered = state
            .register_preview(&path, "audio/wav", 5, "generation-a")
            .unwrap();
        assert!(registered.preview_url.contains(STUDIO_PREVIEW_PROTOCOL));
        assert_eq!(
            state.load_preview("missing", "generation-a").unwrap_err(),
            "STUDIO_PREVIEW_NOT_FOUND"
        );
        let token = registered.preview_url.rsplit('/').next().unwrap();
        assert_eq!(
            state.load_preview(token, "generation-a").unwrap().bytes,
            b"audio"
        );
        assert_eq!(
            state.load_preview(token, "generation-b").unwrap_err(),
            "STUDIO_PREVIEW_GENERATION_STALE"
        );
        fs::remove_file(path).unwrap();
    }
}
