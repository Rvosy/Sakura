use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use serde_json::{json, Map, Value};
use tauri::http::header::{ACCESS_CONTROL_ALLOW_ORIGIN, CACHE_CONTROL, CONTENT_TYPE};
use tauri::http::{Request, Response, StatusCode};
use tauri::{AppHandle, Emitter, Manager, Runtime, State, UriSchemeContext};

use crate::audio::{AudioEventCallback, AudioManager, AudioPlaybackEvent};
use crate::brain_host::{
    BrainHostLaunchConfig, BrainHostRequestError, BrainHostStatus, BrainHostSupervisor,
    EventCallback, StatusCallback,
};
use crate::capture::{self, CaptureManager};
use crate::windows;

pub const BRAIN_STATUS_EVENT: &str = "sakura://brain-status";
pub const TTS_PLAYBACK_EVENT: &str = "sakura://tts-playback-state";
const CHARACTER_ASSET_SCHEME: &str = "sakura-asset";
const MAX_CHARACTER_ASSET_BYTES: u64 = 32 * 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum StartupRoute {
    Pending,
    OnboardingRequired,
    Ready,
    RuntimeRepair,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum StartupRoutingDecision {
    Wait,
    Present(StartupRoute),
    Stop,
}

pub struct DesktopAppState {
    brain: Arc<BrainHostSupervisor>,
    startup: Arc<Mutex<Option<Value>>>,
    startup_route: Arc<Mutex<StartupRoute>>,
    capture: Arc<CaptureManager>,
    audio: Option<AudioManager>,
    audio_error: Option<String>,
}

impl DesktopAppState {
    pub fn start(app: AppHandle) -> Result<Self, String> {
        let launch_config = BrainHostLaunchConfig::for_current_app();
        let capture = Arc::new(CaptureManager::new(
            launch_config.base_dir.join("data/cache/captures"),
        )?);
        let brain_slot = Arc::new(OnceLock::<Arc<BrainHostSupervisor>>::new());
        let audio_app = app.clone();
        let audio_callback: AudioEventCallback = Arc::new(move |event: AudioPlaybackEvent| {
            let _ = audio_app.emit(TTS_PLAYBACK_EVENT, event);
        });
        let (audio, audio_error) = match AudioManager::start(
            launch_config.base_dir.join("data/cache/tts"),
            audio_callback,
        ) {
            Ok(audio) => (Some(audio), None),
            Err(error) => (None, Some(error)),
        };
        let status_audio = audio.clone();
        let status_capture = Arc::clone(&capture);
        let status_app = app.clone();
        let callback: StatusCallback = Arc::new(move |status| {
            if !status.accepting_requests {
                if let Some(audio) = status_audio.as_ref() {
                    audio.reset();
                }
                status_capture.reset();
                if let Some(window) = status_app.get_webview_window("capture") {
                    let _ = window.close();
                    let _ = status_app.emit_to(
                        "main",
                        "sakura://manual-observation-cancelled",
                        json!({}),
                    );
                }
            }
            let _ = status_app.emit(BRAIN_STATUS_EVENT, status);
        });
        let event_audio = audio.clone();
        let event_capture = Arc::clone(&capture);
        let event_brain = Arc::clone(&brain_slot);
        let event_app = app.clone();
        let event_callback: EventCallback = Arc::new(move |event| {
            if event.method == "observation.capture_requested" {
                if let Some(brain) = event_brain.get().cloned() {
                    capture::handle_proactive_capture(
                        Arc::clone(&event_capture),
                        brain,
                        event.payload,
                        event_app.clone(),
                    );
                }
                return;
            }
            let name = format!("sakura://{}", event.method.replace(['.', '_'], "-"));
            let mut payload = event.payload;
            if event.method == "tts.audio_ready" {
                if let Some(resource) = payload.get("resource").cloned().filter(Value::is_object) {
                    let registered = event_audio
                        .as_ref()
                        .ok_or_else(|| "Rust 音频服务不可用".to_string())
                        .and_then(|audio| {
                            audio.register_brain_resource(&event.session_id, &resource)
                        });
                    match registered {
                        Ok(public) => payload["resource"] = public,
                        Err(error) => {
                            let _ = event_app.emit(
                                "sakura://tts-error",
                                json!({
                                    "version": 1,
                                    "synthesisId": payload.get("synthesisId"),
                                    "segmentId": payload.get("segmentId"),
                                    "error": {
                                        "code": "AUDIO_RESOURCE_REJECTED",
                                        "message": "生成的语音资源无法播放。",
                                        "retryable": false,
                                        "details": {"error": error},
                                    }
                                }),
                            );
                            return;
                        }
                    }
                }
            }
            let _ = event_app.emit(&name, payload);
        });
        let brain = Arc::new(BrainHostSupervisor::start_with_event_callback(
            launch_config,
            Some(callback),
            Some(event_callback),
        ));
        let _ = brain_slot.set(Arc::clone(&brain));
        Ok(Self {
            brain,
            startup: Arc::new(Mutex::new(None)),
            startup_route: Arc::new(Mutex::new(StartupRoute::Pending)),
            capture,
            audio,
            audio_error,
        })
    }

    pub fn shutdown(&self) {
        self.brain.shutdown();
        if let Some(audio) = self.audio.as_ref() {
            audio.shutdown();
        }
        self.capture.reset();
    }

    pub fn brain_status(&self) -> BrainHostStatus {
        self.brain.status()
    }

    pub fn begin_startup_routing(&self, app: AppHandle) {
        let brain = Arc::clone(&self.brain);
        let startup = Arc::clone(&self.startup);
        let route = Arc::clone(&self.startup_route);
        let _ = thread::Builder::new()
            .name("sakura-startup-router".into())
            .spawn(move || {
                let mut routed_ready_generation = None;
                loop {
                    let status = brain.status();
                    let current_route = *route.lock().expect("startup route lock poisoned");
                    let payload = (status.phase == crate::brain_host::BrainHostPhase::Ready)
                        .then(|| brain.startup_state())
                        .flatten();
                    match startup_routing_decision(
                        current_route,
                        routed_ready_generation,
                        &status,
                        payload.as_ref(),
                    ) {
                        StartupRoutingDecision::Wait => {}
                        StartupRoutingDecision::Stop => break,
                        StartupRoutingDecision::Present(next) => {
                            if status.phase == crate::brain_host::BrainHostPhase::Ready {
                                *startup.lock().expect("startup state lock poisoned") =
                                    payload.clone();
                                routed_ready_generation = Some(status.session_generation);
                            }
                            *route.lock().expect("startup route lock poisoned") = next;
                            present_startup_route(&app, next);
                        }
                    }
                    thread::sleep(Duration::from_millis(50));
                }
            });
    }

    fn startup_route(&self) -> StartupRoute {
        *self
            .startup_route
            .lock()
            .expect("startup route lock poisoned")
    }

    fn set_startup_route(&self, route: StartupRoute) {
        *self
            .startup_route
            .lock()
            .expect("startup route lock poisoned") = route;
    }

    fn cached_startup(&self) -> Option<Value> {
        self.startup
            .lock()
            .expect("startup state lock poisoned")
            .clone()
    }

    fn store_startup(&self, startup: Value) {
        *self.startup.lock().expect("startup state lock poisoned") = Some(startup);
    }

    fn refresh_startup(&self) -> Result<Value, String> {
        let startup = self
            .request_with_timeout("bootstrap.status", json!({}), Duration::from_secs(5))
            .map_err(|error| error.to_string())?;
        self.store_startup(startup.clone());
        Ok(startup)
    }

    fn bootstrap_status(&self) -> Result<Value, String> {
        let status = self.brain.status();
        match status.phase {
            crate::brain_host::BrainHostPhase::Ready => self.refresh_startup(),
            crate::brain_host::BrainHostPhase::Diagnostic
            | crate::brain_host::BrainHostPhase::Stopped => Ok(json!({
                "version": 1,
                "state": "runtime_repair",
                "diagnostic": status.diagnostic,
            })),
            _ => Ok(json!({
                "version": 1,
                "state": "brain_recovering",
                "brain": status,
            })),
        }
    }

    fn local_diagnostics(&self) -> Value {
        let status = self.brain.status();
        json!({
            "version": 1,
            "brain": {
                "state": status.phase,
                "sessionId": status.session_id,
                "busy": !status.accepting_requests,
                "restartCount": status.restart_count,
                "diagnostic": status.diagnostic,
            },
            "plugins": {"loaded": 0, "failed": 0, "items": [], "available": false},
            "mcp": {"ready": false, "toolCount": 0},
            "tts": {"ready": self.audio.is_some(), "service": "RustAudioManager"},
            "resources": {
                "activeCount": status.temporary_resource_count,
                "labels": ["temporary"]
            },
            "scheduler": {"running": false, "jobs": []},
            "theme": {},
        })
    }

    fn pet_bootstrap(&self) -> Result<Value, String> {
        let status = self.brain.status();
        if !status.accepting_requests {
            return Err("Brain Host 尚未就绪".into());
        }
        let startup = self
            .request_with_timeout("pet.bootstrap", json!({}), Duration::from_secs(5))
            .map_err(|error| error.to_string())?;
        self.store_startup(startup.clone());
        build_pet_bootstrap(&startup, status.session_generation)
    }

    pub(crate) fn request(
        &self,
        method: &str,
        payload: Value,
    ) -> Result<Value, BrainHostRequestError> {
        self.request_with_timeout(method, payload, Duration::from_secs(5))
    }

    fn request_with_timeout(
        &self,
        method: &str,
        payload: Value,
        timeout: Duration,
    ) -> Result<Value, BrainHostRequestError> {
        self.brain.request(method, payload, timeout)
    }

    pub(crate) fn brain(&self) -> Arc<BrainHostSupervisor> {
        Arc::clone(&self.brain)
    }

    pub(crate) fn capture_manager(&self) -> Arc<CaptureManager> {
        Arc::clone(&self.capture)
    }

    fn audio(&self) -> Result<&AudioManager, String> {
        self.audio.as_ref().ok_or_else(|| {
            self.audio_error
                .clone()
                .unwrap_or_else(|| "Rust 音频服务不可用".to_string())
        })
    }
}

fn startup_route_from_payload(startup: Option<&Value>) -> StartupRoute {
    match startup
        .and_then(|value| value.get("state"))
        .and_then(Value::as_str)
    {
        Some("ready") => StartupRoute::Ready,
        Some("onboarding_required") | Some("needs_character") => StartupRoute::OnboardingRequired,
        Some("runtime_repair") | None => StartupRoute::RuntimeRepair,
        Some(_) => StartupRoute::RuntimeRepair,
    }
}

fn startup_routing_decision(
    current_route: StartupRoute,
    routed_ready_generation: Option<u64>,
    status: &BrainHostStatus,
    startup: Option<&Value>,
) -> StartupRoutingDecision {
    match status.phase {
        crate::brain_host::BrainHostPhase::Ready
            if routed_ready_generation != Some(status.session_generation) =>
        {
            StartupRoutingDecision::Present(startup_route_from_payload(startup))
        }
        crate::brain_host::BrainHostPhase::Diagnostic
            if current_route != StartupRoute::RuntimeRepair =>
        {
            StartupRoutingDecision::Present(StartupRoute::RuntimeRepair)
        }
        crate::brain_host::BrainHostPhase::Stopping
        | crate::brain_host::BrainHostPhase::Stopped => StartupRoutingDecision::Stop,
        _ => StartupRoutingDecision::Wait,
    }
}

fn present_startup_route(app: &AppHandle, route: StartupRoute) {
    match route {
        StartupRoute::Pending => {}
        StartupRoute::Ready => windows::show_ready_route(app),
        StartupRoute::OnboardingRequired => {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let _ = windows::show_onboarding_route(app).await;
            });
        }
        StartupRoute::RuntimeRepair => {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let _ = windows::show_runtime_repair_route(app).await;
            });
        }
    }
}

pub fn show_application_window(app: &AppHandle) {
    if let Some(state) = app.try_state::<DesktopAppState>() {
        present_startup_route(app, state.startup_route());
    }
}

#[tauri::command]
pub fn brain_status(state: State<'_, DesktopAppState>) -> BrainHostStatus {
    state.brain_status()
}

#[tauri::command]
pub fn bootstrap_status(state: State<'_, DesktopAppState>) -> Result<Value, String> {
    state.bootstrap_status()
}

#[tauri::command]
pub fn pet_bootstrap(state: State<'_, DesktopAppState>) -> Result<Value, String> {
    state.pet_bootstrap()
}

#[tauri::command]
pub fn chat_send(
    state: State<'_, DesktopAppState>,
    text: String,
    observation_id: Option<String>,
) -> Result<Value, BrainHostRequestError> {
    state.request(
        "chat.send",
        json!({"text": text, "observation_id": observation_id}),
    )
}

#[tauri::command]
pub fn chat_cancel(
    state: State<'_, DesktopAppState>,
    interaction_id: String,
) -> Result<Value, BrainHostRequestError> {
    state.request("chat.cancel", json!({"interaction_id": interaction_id}))
}

#[tauri::command]
pub fn chat_confirm_action(
    state: State<'_, DesktopAppState>,
    action_id: String,
) -> Result<Value, BrainHostRequestError> {
    state.request("chat.confirm_action", json!({"action_id": action_id}))
}

#[tauri::command]
pub fn chat_reject_action(
    state: State<'_, DesktopAppState>,
    action_id: String,
) -> Result<Value, BrainHostRequestError> {
    state.request("chat.reject_action", json!({"action_id": action_id}))
}

#[tauri::command]
pub fn tts_synthesize(
    state: State<'_, DesktopAppState>,
    text: String,
    tone: Option<String>,
    segment_id: Option<String>,
    audio_key: Option<String>,
) -> Result<Value, BrainHostRequestError> {
    state.request(
        "tts.synthesize",
        json!({
            "text": text,
            "tone": tone,
            "segment_id": segment_id.unwrap_or_default(),
            "audio_key": audio_key.unwrap_or_default(),
        }),
    )
}

#[tauri::command]
pub fn tts_cancel(
    state: State<'_, DesktopAppState>,
    synthesis_id: String,
) -> Result<Value, BrainHostRequestError> {
    state.request("tts.cancel", json!({"synthesis_id": synthesis_id}))
}

#[tauri::command]
pub fn play_tts_audio(
    state: State<'_, DesktopAppState>,
    resource_id: String,
    playback_id: String,
    volume: Option<f32>,
) -> Result<(), String> {
    state
        .audio()?
        .play(&resource_id, &playback_id, volume.unwrap_or(1.0))
}

#[tauri::command]
pub fn stop_tts_audio(state: State<'_, DesktopAppState>) -> Result<(), String> {
    state.audio()?.stop()
}

#[tauri::command]
pub fn set_tts_volume(state: State<'_, DesktopAppState>, volume: f32) -> Result<(), String> {
    state.audio()?.set_volume(volume)
}

#[tauri::command]
pub fn load_request(
    window: tauri::WebviewWindow,
    state: State<'_, DesktopAppState>,
) -> Result<Value, BrainHostRequestError> {
    let kind = secondary_window_kind(window.label()).ok_or_else(|| BrainHostRequestError {
        code: "SECONDARY_WINDOW_UNKNOWN".into(),
        message: format!("未知次级窗口：{}", window.label()),
        retryable: false,
        details: json!({}),
    })?;
    let request = state.request_with_timeout(
        "window.request",
        json!({"kind": kind}),
        Duration::from_secs(30),
    );
    if kind == "diagnostics" {
        return Ok(request.unwrap_or_else(|_| state.local_diagnostics()));
    }
    request
}

#[tauri::command]
pub async fn host_call(
    app: AppHandle,
    state: State<'_, DesktopAppState>,
    method: String,
    params: Value,
) -> Result<Value, BrainHostRequestError> {
    if method == "diagnostics.snapshot" && !state.brain_status().accepting_requests {
        return Ok(state.local_diagnostics());
    }
    let response = state.request_with_timeout(
        "window.host_call",
        json!({"method": method, "params": params}),
        secondary_call_timeout(&method),
    )?;
    if response.get("openWindow").and_then(Value::as_str) == Some("studio") {
        windows::open_studio_window(app.clone())
            .await
            .map_err(BrainHostRequestError::transport)?;
    }
    if method.starts_with("studio.save_") || method == "studio.create_character" {
        let _ = app.emit("sakura://character-changed", response.clone());
    }
    Ok(response)
}

#[tauri::command]
pub fn save_settings(
    window: tauri::WebviewWindow,
    state: State<'_, DesktopAppState>,
    settings: Value,
) -> Result<(), String> {
    let response = apply_secondary_settings(&window, &state, settings)?;
    let startup = state.refresh_startup()?;
    let route = startup_route_from_payload(Some(&startup));
    if state.startup_route() == StartupRoute::OnboardingRequired && route != StartupRoute::Ready {
        return Err("首次设置尚未完成：请配置聊天模型并选择角色。".into());
    }
    state.set_startup_route(route);
    emit_settings_refresh(window.app_handle(), &response);
    let app = window.app_handle().clone();
    window.destroy().map_err(|error| error.to_string())?;
    present_startup_route(&app, route);
    Ok(())
}

#[tauri::command]
pub fn apply_settings(
    window: tauri::WebviewWindow,
    state: State<'_, DesktopAppState>,
    settings: Value,
) -> Result<Value, String> {
    let response = apply_secondary_settings(&window, &state, settings)?;
    let startup = state.refresh_startup()?;
    let route = startup_route_from_payload(Some(&startup));
    if state.startup_route() != StartupRoute::Ready {
        state.set_startup_route(route);
    }
    emit_settings_refresh(window.app_handle(), &response);
    Ok(response)
}

#[tauri::command]
pub fn preview_layout(app: AppHandle, layout: Value) -> Result<(), String> {
    app.emit_to("main", "sakura://layout-preview", layout)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn cancel_settings(window: tauri::WebviewWindow) -> Result<(), String> {
    window.destroy().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn show_studio(window: tauri::WebviewWindow) -> Result<(), String> {
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn close_studio(window: tauri::WebviewWindow) -> Result<(), String> {
    window.destroy().map_err(|error| error.to_string())
}

fn apply_secondary_settings(
    window: &tauri::WebviewWindow,
    state: &DesktopAppState,
    settings: Value,
) -> Result<Value, String> {
    state
        .request_with_timeout(
            "window.host_call",
            json!({"method": "settings.apply", "params": {"settings": settings}}),
            Duration::from_secs(30),
        )
        .map_err(|error| error.to_string())
        .map_err(|error| format!("{}：{error}", window.label()))
}

fn emit_settings_refresh(app: &AppHandle, response: &Value) {
    let _ = app.emit("sakura://settings-changed", response.clone());
}

fn secondary_window_kind(label: &str) -> Option<&'static str> {
    match label {
        "settings" => Some("settings"),
        "studio" => Some("studio"),
        "history" => Some("history"),
        "diagnostics" => Some("diagnostics"),
        _ => None,
    }
}

fn secondary_call_timeout(method: &str) -> Duration {
    if method.starts_with("studio.import_")
        || method.starts_with("studio.export_")
        || method.starts_with("character.import_")
        || method.starts_with("character.export_")
        || method.starts_with("resources.")
    {
        Duration::from_secs(30 * 60)
    } else {
        Duration::from_secs(30)
    }
}

pub fn character_asset_protocol<R: Runtime>(
    context: UriSchemeContext<'_, R>,
    request: Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    let response = context
        .app_handle()
        .try_state::<DesktopAppState>()
        .and_then(|state| state.cached_startup())
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
    let state = startup
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or("ready");
    if state != "ready" {
        return Err("桌宠只能在首次设置完成后加载".to_string());
    }
    let character_value = startup
        .get("character")
        .ok_or_else(|| "启动状态缺少角色信息".to_string())?;
    let character = character_value
        .as_object()
        .ok_or_else(|| "启动状态角色信息格式无效".to_string())?;
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
        "state": state,
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

    fn brain_status_fixture(
        phase: crate::brain_host::BrainHostPhase,
        session_generation: u64,
    ) -> BrainHostStatus {
        BrainHostStatus {
            phase,
            session_id: Some("session-test".into()),
            session_generation,
            restart_count: 0,
            accepting_requests: true,
            pending_request_count: 0,
            temporary_resource_count: 0,
            diagnostic: None,
            last_shutdown_forced: false,
        }
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
    fn startup_route_separates_onboarding_ready_and_runtime_repair() {
        let startup = json!({
            "version": 1,
            "state": "onboarding_required",
            "character": null,
        });

        assert_eq!(
            startup_route_from_payload(Some(&startup)),
            StartupRoute::OnboardingRequired
        );
        assert_eq!(
            startup_route_from_payload(Some(&json!({"state": "ready"}))),
            StartupRoute::Ready
        );
        assert_eq!(
            startup_route_from_payload(None),
            StartupRoute::RuntimeRepair
        );
        assert!(build_pet_bootstrap(&startup, 2).is_err());
    }

    #[test]
    fn startup_routing_is_idempotent_and_rechecks_each_ready_generation() {
        let first_ready = brain_status_fixture(crate::brain_host::BrainHostPhase::Ready, 1);
        assert_eq!(
            startup_routing_decision(
                StartupRoute::Pending,
                None,
                &first_ready,
                Some(&json!({"state": "ready"})),
            ),
            StartupRoutingDecision::Present(StartupRoute::Ready)
        );
        assert_eq!(
            startup_routing_decision(
                StartupRoute::Ready,
                Some(1),
                &first_ready,
                Some(&json!({"state": "ready"})),
            ),
            StartupRoutingDecision::Wait
        );

        let recovered = brain_status_fixture(crate::brain_host::BrainHostPhase::Ready, 2);
        assert_eq!(
            startup_routing_decision(
                StartupRoute::Ready,
                Some(1),
                &recovered,
                Some(&json!({"state": "onboarding_required"})),
            ),
            StartupRoutingDecision::Present(StartupRoute::OnboardingRequired)
        );
    }

    #[test]
    fn startup_routing_preserves_ui_while_recovering_then_opens_repair_once() {
        let recovering = brain_status_fixture(crate::brain_host::BrainHostPhase::Restarting, 2);
        assert_eq!(
            startup_routing_decision(StartupRoute::Ready, Some(1), &recovering, None),
            StartupRoutingDecision::Wait
        );

        let diagnostic = brain_status_fixture(crate::brain_host::BrainHostPhase::Diagnostic, 4);
        assert_eq!(
            startup_routing_decision(StartupRoute::Ready, Some(1), &diagnostic, None),
            StartupRoutingDecision::Present(StartupRoute::RuntimeRepair)
        );
        assert_eq!(
            startup_routing_decision(StartupRoute::RuntimeRepair, Some(1), &diagnostic, None,),
            StartupRoutingDecision::Wait
        );
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

    #[test]
    fn secondary_window_labels_route_to_brain_request_kinds() {
        assert_eq!(secondary_window_kind("settings"), Some("settings"));
        assert_eq!(secondary_window_kind("studio"), Some("studio"));
        assert_eq!(secondary_window_kind("history"), Some("history"));
        assert_eq!(secondary_window_kind("diagnostics"), Some("diagnostics"));
        assert_eq!(secondary_window_kind("main"), None);
    }

    #[test]
    fn secondary_file_calls_receive_long_timeouts() {
        assert_eq!(
            secondary_call_timeout("studio.import_portrait"),
            Duration::from_secs(30 * 60)
        );
        assert_eq!(
            secondary_call_timeout("history.page"),
            Duration::from_secs(30)
        );
    }
}
