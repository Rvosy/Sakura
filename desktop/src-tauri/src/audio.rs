use std::{
    collections::BTreeMap,
    fs::{self, File},
    future::Future,
    io::Read,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::Duration,
};

use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, WebviewWindow};

use crate::{
    product_shell::{self, assert_settings_identity},
    runtime_log::{Correlation, RuntimeLogEvent, RuntimeLogService, Severity},
    shell_lifecycle::{
        self, dispatch_settings_request, settings_core_handle, settings_response_payload,
        ShellLifecycleState,
    },
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const MAX_TTS_AUDIO_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DESCRIPTOR_FUTURE_SECONDS: i64 = 10 * 60;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AudioDescriptor {
    pub opaque_id: String,
    pub recording_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub segment_index: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub segment_count: Option<u64>,
    pub media_type: String,
    pub byte_length: u64,
    pub expires_at: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PlayPreparedRequest {
    pub opaque_id: String,
    pub playback_id: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioPlaybackError {
    pub code: &'static str,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioPlaybackEvent {
    pub playback_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recording_id: Option<String>,
    #[serde(skip)]
    pub segment_index: Option<u64>,
    #[serde(skip)]
    pub segment_count: Option<u64>,
    pub state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<AudioPlaybackError>,
}

#[derive(Debug)]
struct RegisteredAudio {
    path: PathBuf,
    expires_at: OffsetDateTime,
    recording_id: Option<String>,
    segment_index: Option<u64>,
    segment_count: Option<u64>,
}

#[derive(Clone)]
struct AudioRegistry {
    root: PathBuf,
    items: Arc<Mutex<BTreeMap<String, RegisteredAudio>>>,
}

impl AudioRegistry {
    fn new(root: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&root).map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        let root = root.canonicalize().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        Ok(Self {
            root,
            items: Arc::new(Mutex::new(BTreeMap::new())),
        })
    }

    fn register(&self, descriptor: &AudioDescriptor) -> Result<(), String> {
        validate_opaque_id(&descriptor.opaque_id)?;
        if descriptor.media_type != "audio/wav" {
            return Err("AUDIO_FORMAT_UNSUPPORTED".to_string());
        }
        let expires_at = validate_expiry(&descriptor.expires_at)?;
        let unresolved = self.root.join(format!("{}.wav", descriptor.opaque_id));
        let metadata = fs::symlink_metadata(&unresolved).map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
        })?;
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        let path = unresolved.canonicalize().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
        })?;
        path.strip_prefix(&self.root).map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
        })?;
        let actual = metadata.len();
        if actual == 0 || actual != descriptor.byte_length || actual > MAX_TTS_AUDIO_BYTES {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        validate_wav_header(&path)?;
        let mut items = self.items.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        if items.contains_key(&descriptor.opaque_id) {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        items.insert(
            descriptor.opaque_id.clone(),
            RegisteredAudio {
                path,
                expires_at,
                recording_id: descriptor.recording_id.clone(),
                segment_index: descriptor.segment_index,
                segment_count: descriptor.segment_count,
            },
        );
        Ok(())
    }

    fn take(&self, opaque_id: &str) -> Result<RegisteredAudio, String> {
        validate_opaque_id(opaque_id)?;
        let item = self
            .items
            .lock()
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })?
            .remove(opaque_id)
            .ok_or_else(|| "AUDIO_RECORDING_INVALID".to_string())?;
        if item.expires_at <= OffsetDateTime::now_utc() {
            let _ = fs::remove_file(&item.path);
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        Ok(item)
    }

    fn clear(&self) {
        let paths = self
            .items
            .lock()
            .map(|mut items| {
                std::mem::take(&mut *items)
                    .into_values()
                    .map(|item| item.path)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for path in paths {
            let _ = fs::remove_file(path);
        }
    }

    fn discard_unregistered(&self, opaque_id: &str) {
        if validate_opaque_id(opaque_id).is_err() {
            return;
        }
        let unresolved = self.root.join(format!("{opaque_id}.wav"));
        let Ok(metadata) = fs::symlink_metadata(&unresolved) else {
            return;
        };
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            return;
        }
        let Ok(path) = unresolved.canonicalize() else {
            return;
        };
        if path.strip_prefix(&self.root).is_ok() {
            let _ = fs::remove_file(path);
        }
    }
}

enum AudioCommand {
    Play {
        playback_id: String,
        recording_id: Option<String>,
        segment_index: Option<u64>,
        segment_count: Option<u64>,
        path: PathBuf,
    },
    Stop,
    Shutdown,
}

struct ActivePlayback {
    playback_id: String,
    recording_id: Option<String>,
    segment_index: Option<u64>,
    segment_count: Option<u64>,
    path: PathBuf,
    player: Player,
    // Rodio's Player only owns its queue.  The OS stream lives in the
    // MixerDeviceSink and must remain alive until the queue drains; dropping
    // it immediately leaves Player::empty() permanently false on WASAPI.
    _device_sink: MixerDeviceSink,
}

pub type AudioEventCallback = Arc<dyn Fn(AudioPlaybackEvent) + Send + Sync + 'static>;

pub struct AudioManager {
    registry: AudioRegistry,
    registration_revision: Mutex<u64>,
    closed: AtomicBool,
    sender: Mutex<Option<mpsc::Sender<AudioCommand>>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl AudioManager {
    pub fn start(root: PathBuf, callback: AudioEventCallback) -> Result<Self, String> {
        let registry = AudioRegistry::new(root)?;
        let (sender, receiver) = mpsc::channel();
        let thread = thread::Builder::new()
            .name("sakura-runtime-v2-audio".to_string())
            .spawn(move || playback_loop(receiver, callback))
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })?;
        Ok(Self {
            registry,
            registration_revision: Mutex::new(0),
            closed: AtomicBool::new(false),
            sender: Mutex::new(Some(sender)),
            thread: Mutex::new(Some(thread)),
        })
    }

    pub fn registration_revision(&self) -> Result<u64, String> {
        self.registration_revision
            .lock()
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })
            .and_then(|revision| {
                if self.closed.load(Ordering::SeqCst) {
                    Err("STALE_GENERATION".into())
                } else {
                    Ok(*revision)
                }
            })
    }

    pub fn register_at_revision(
        &self,
        descriptor: &AudioDescriptor,
        expected_revision: u64,
    ) -> Result<(), String> {
        let revision = self.registration_revision.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        if self.closed.load(Ordering::SeqCst) || *revision != expected_revision {
            self.registry.discard_unregistered(&descriptor.opaque_id);
            return Err("STALE_GENERATION".to_string());
        }
        self.registry.register(descriptor)
    }

    pub fn play(&self, request: PlayPreparedRequest) -> Result<(), String> {
        if request.playback_id.trim().is_empty() || request.playback_id.len() > 128 {
            return Err("AUDIO_PLAYBACK_FAILED".to_string());
        }
        let audio = self.registry.take(&request.opaque_id)?;
        self.send(AudioCommand::Play {
            playback_id: request.playback_id,
            recording_id: audio.recording_id,
            segment_index: audio.segment_index,
            segment_count: audio.segment_count,
            path: audio.path,
        })
    }

    pub fn stop_and_clear(&self) -> Result<(), String> {
        let mut revision = self.registration_revision.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        *revision = revision.wrapping_add(1);
        self.registry.clear();
        drop(revision);
        self.send(AudioCommand::Stop)
    }

    pub fn shutdown(&self) {
        if let Ok(_revision) = self.registration_revision.lock() {
            self.closed.store(true, Ordering::SeqCst);
            self.registry.clear();
        }
        if let Ok(mut sender) = self.sender.lock() {
            if let Some(sender) = sender.take() {
                let _ = sender.send(AudioCommand::Shutdown);
            }
        }
        if let Ok(mut worker) = self.thread.lock() {
            if let Some(worker) = worker.take() {
                let _ = worker.join();
            }
        }
    }

    fn send(&self, command: AudioCommand) -> Result<(), String> {
        self.sender
            .lock()
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })?
            .as_ref()
            .ok_or_else(|| "AUDIO_PLAYBACK_FAILED".to_string())?
            .send(command)
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })
    }
}

impl Drop for AudioManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct AudioSession {
    generation: String,
    manager: Arc<AudioManager>,
    operation: Option<(String, String)>,
}

pub struct AudioState {
    user_root: PathBuf,
    active: Mutex<Option<AudioSession>>,
    observations: Mutex<
        Option<(
            String,
            tokio::sync::mpsc::UnboundedSender<AudioPlaybackEvent>,
        )>,
    >,
    input_active: Arc<AtomicBool>,
}

/// The microphone worker owns this guard through device teardown. Failed opens,
/// cancelled starts and unwinding cannot leave playback permanently disabled.
pub(crate) struct InputPlaybackPause {
    input_active: Arc<AtomicBool>,
    held: bool,
}

impl InputPlaybackPause {
    pub(crate) fn release(&mut self) {
        if self.held {
            self.held = false;
            self.input_active.store(false, Ordering::SeqCst);
        }
    }
}

impl Drop for InputPlaybackPause {
    fn drop(&mut self) {
        self.release();
    }
}

impl AudioState {
    pub fn new(user_root: PathBuf) -> Self {
        Self {
            user_root,
            active: Mutex::new(None),
            observations: Mutex::new(None),
            input_active: Arc::new(AtomicBool::new(false)),
        }
    }

    fn observation_sender(
        &self,
        generation: &str,
        handle: shell_lifecycle::ShellLifecycleHandle,
    ) -> Result<tokio::sync::mpsc::UnboundedSender<AudioPlaybackEvent>, String> {
        let mut observations = self.observations.lock().map_err(|error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", error)
        })?;
        if let Some((current, sender)) = observations.as_ref() {
            if current == generation {
                return Ok(sender.clone());
            }
        }
        let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
        let observer_generation = generation.to_string();
        tauri::async_runtime::spawn(forward_playback_observations(receiver, move |event| {
            observe_tts_playback(handle.clone(), observer_generation.clone(), event)
        }));
        *observations = Some((generation.to_string(), sender.clone()));
        Ok(sender)
    }

    #[cfg(test)]
    pub fn manager(
        &self,
        generation_id: &str,
        callback: AudioEventCallback,
    ) -> Result<Arc<AudioManager>, String> {
        self.open_manager(generation_id, None, callback, || {})
    }

    fn open_manager(
        &self,
        generation_id: &str,
        operation: Option<(&str, &str)>,
        callback: AudioEventCallback,
        on_started: impl FnOnce(),
    ) -> Result<Arc<AudioManager>, String> {
        validate_generation_id(generation_id)?;
        let mut active = self.active.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        if self.input_active.load(Ordering::SeqCst) {
            return Err("TTS_PAUSED_FOR_VOICE_INPUT".into());
        }
        if let Some(session) = active.as_ref() {
            if operation.is_none() && session.generation == generation_id {
                return Ok(session.manager.clone());
            }
        }
        if let Some(session) = active.take() {
            let _ = session.manager.stop_and_clear();
            session.manager.shutdown();
        }
        let root = self
            .user_root
            .join("data/cache/tts/runtime-v2")
            .join(generation_id);
        let manager = Arc::new(AudioManager::start(root, callback)?);
        *active = Some(AudioSession {
            generation: generation_id.to_string(),
            manager: manager.clone(),
            operation: operation.map(|(id, owner)| (id.to_string(), owner.to_string())),
        });
        on_started();
        Ok(manager)
    }

    fn manager_for_operation(
        &self,
        generation: &str,
        operation: &str,
    ) -> Result<Arc<AudioManager>, String> {
        let active = self.active.lock().map_err(|error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", error)
        })?;
        active
            .as_ref()
            .filter(|session| {
                session.generation == generation
                    && session
                        .operation
                        .as_ref()
                        .is_some_and(|(id, _)| id == operation)
            })
            .map(|session| session.manager.clone())
            .ok_or_else(|| "TTS_SYNTHESIS_CANCELLED".into())
    }

    fn stop_window(&self, owner: &str, operation: Option<&str>) -> Option<(String, String)> {
        let mut active = self.active.lock().ok()?;
        if !active
            .as_ref()?
            .operation
            .as_ref()
            .is_some_and(|(id, window)| {
                window == owner && operation.is_none_or(|operation| operation == id)
            })
        {
            return None;
        }
        let session = active.take()?;
        let _ = session.manager.stop_and_clear();
        session.manager.shutdown();
        session
            .operation
            .map(|(operation, _)| (session.generation, operation))
    }

    #[cfg(test)]
    pub fn current(&self, generation_id: &str) -> Result<Arc<AudioManager>, String> {
        self.active
            .lock()
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
            })?
            .as_ref()
            .filter(|session| session.generation == generation_id)
            .map(|session| session.manager.clone())
            .ok_or_else(|| "STALE_GENERATION".to_string())
    }

    pub fn shutdown(&self) {
        if let Ok(mut active) = self.active.lock() {
            if let Some(session) = active.take() {
                let _ = session.manager.stop_and_clear();
                session.manager.shutdown();
            }
        }
    }

    pub(crate) fn shutdown_generation(&self, generation: &str) {
        if let Ok(mut active) = self.active.lock() {
            if active
                .as_ref()
                .is_some_and(|session| session.generation == generation)
            {
                if let Some(session) = active.take() {
                    let _ = session.manager.stop_and_clear();
                    session.manager.shutdown();
                }
            }
        }
        if let Ok(mut observations) = self.observations.lock() {
            if observations
                .as_ref()
                .is_some_and(|(current, _)| current == generation)
            {
                observations.take();
            }
        }
    }

    pub(crate) fn pause_for_input(&self) -> InputPlaybackPause {
        self.input_active.store(true, Ordering::SeqCst);
        self.shutdown();
        InputPlaybackPause {
            input_active: self.input_active.clone(),
            held: true,
        }
    }

    fn play_if_allowed(
        &self,
        generation: &str,
        payload: PlayPreparedRequest,
    ) -> Result<(), String> {
        let active = self.active.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_PLAYBACK_FAILED", source_error)
        })?;
        if self.input_active.load(Ordering::SeqCst) {
            return Err("TTS_PAUSED_FOR_VOICE_INPUT".into());
        }
        active
            .as_ref()
            .filter(|session| session.generation == generation)
            .ok_or("STALE_GENERATION")?
            .manager
            .play(payload)
    }
}

impl Drop for AudioState {
    fn drop(&mut self) {
        self.shutdown();
        if let Ok(observations) = self.observations.get_mut() {
            observations.take();
        }
    }
}

fn playback_loop(receiver: mpsc::Receiver<AudioCommand>, callback: AudioEventCallback) {
    let mut active: Option<ActivePlayback> = None;
    loop {
        match receiver.recv_timeout(Duration::from_millis(20)) {
            Ok(AudioCommand::Play {
                playback_id,
                recording_id,
                segment_index,
                segment_count,
                path,
            }) => {
                finish_active(&mut active, "stopped", &callback);
                let result = open_default_playback(&path);
                match result {
                    Ok((device_sink, player)) => {
                        emit_audio_event(
                            &callback,
                            AudioPlaybackEvent {
                                playback_id: playback_id.clone(),
                                recording_id: recording_id.clone(),
                                segment_index,
                                segment_count,
                                state: "started",
                                error: None,
                            },
                        );
                        active = Some(ActivePlayback {
                            playback_id,
                            recording_id,
                            segment_index,
                            segment_count,
                            path,
                            player,
                            _device_sink: device_sink,
                        });
                    }
                    Err(error) => {
                        let _ = fs::remove_file(path);
                        emit_audio_event(
                            &callback,
                            AudioPlaybackEvent {
                                playback_id,
                                recording_id,
                                segment_index,
                                segment_count,
                                state: "failed",
                                error: Some(error),
                            },
                        );
                    }
                }
            }
            Ok(AudioCommand::Stop) => finish_active(&mut active, "stopped", &callback),
            Ok(AudioCommand::Shutdown) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                finish_active(&mut active, "stopped", &callback);
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if active.as_ref().is_some_and(|item| item.player.empty()) {
                    finish_active(&mut active, "finished", &callback);
                }
            }
        }
    }
}

fn open_default_playback(path: &Path) -> Result<(MixerDeviceSink, Player), AudioPlaybackError> {
    // Open the system default on every item so a device switch/disconnect can
    // recover on the next segment without restarting the application.
    let sink = DeviceSinkBuilder::open_default_sink().map_err(|error| AudioPlaybackError {
        code: "AUDIO_DEVICE_UNAVAILABLE",
        message: crate::runtime_log::sanitize_diagnostic(&error.to_string(), &[], 4096),
    })?;
    let file = File::open(path).map_err(|error| AudioPlaybackError {
        code: "AUDIO_RECORDING_INVALID",
        message: crate::runtime_log::sanitize_diagnostic(
            &format!("{}: {error}", path.display()),
            &[],
            4096,
        ),
    })?;
    let decoder = Decoder::try_from(file).map_err(|error| AudioPlaybackError {
        code: "AUDIO_FORMAT_UNSUPPORTED",
        message: crate::runtime_log::sanitize_diagnostic(
            &format!("{}: {error}", path.display()),
            &[],
            4096,
        ),
    })?;
    let player = Player::connect_new(sink.mixer());
    player.append(decoder);
    Ok((sink, player))
}

fn finish_active(
    active: &mut Option<ActivePlayback>,
    state: &'static str,
    callback: &AudioEventCallback,
) {
    let Some(current) = active.take() else {
        return;
    };
    current.player.stop();
    let _ = fs::remove_file(current.path);
    emit_audio_event(
        callback,
        AudioPlaybackEvent {
            playback_id: current.playback_id,
            recording_id: current.recording_id,
            segment_index: current.segment_index,
            segment_count: current.segment_count,
            state,
            error: None,
        },
    );
}

fn emit_audio_event(callback: &AudioEventCallback, event: AudioPlaybackEvent) {
    callback(event);
}

fn validate_expiry(value: &str) -> Result<OffsetDateTime, String> {
    let expiry = OffsetDateTime::parse(value, &Rfc3339).map_err(|source_error| {
        crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
    })?;
    let now = OffsetDateTime::now_utc();
    if expiry <= now || (expiry - now).whole_seconds() > MAX_DESCRIPTOR_FUTURE_SECONDS {
        return Err("AUDIO_RECORDING_INVALID".to_string());
    }
    Ok(expiry)
}

fn validate_wav_header(path: &Path) -> Result<(), String> {
    let mut header = [0_u8; 12];
    File::open(path)
        .and_then(|mut file| file.read_exact(&mut header))
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
        })?;
    if &header[0..4] != b"RIFF" || &header[8..12] != b"WAVE" {
        return Err("AUDIO_FORMAT_UNSUPPORTED".to_string());
    }
    Ok(())
}

fn validate_opaque_id(value: &str) -> Result<(), String> {
    if value.len() != 32 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("AUDIO_RECORDING_INVALID".to_string());
    }
    Ok(())
}

fn validate_generation_id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("STALE_GENERATION".to_string());
    }
    Ok(())
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct TtsPrepareSegmentRequest {
    operation_id: String,
    segment_index: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct TtsPrepareHistorySegmentRequest {
    operation_id: String,
    history_entry_id: String,
    segment_index: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct TtsCancelSynthesisRequest {
    operation_id: String,
}

#[tauri::command]
pub(crate) fn tts_begin_reply(
    window: WebviewWindow,
    payload: TtsCancelSynthesisRequest,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, AudioState>,
    runtime_log: State<'_, RuntimeLogService>,
) -> Result<(), String> {
    validate_playback_window(window.label())?;
    if payload.operation_id.trim().is_empty() || payload.operation_id.len() > 128 {
        return Err("TTS_SEGMENT_NOT_AUTHORIZED".into());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation = handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or("STALE_GENERATION")?;
    let callback_app = app_handle.clone();
    let observations = audio_state.observation_sender(&generation, handle)?;
    let playback_log = runtime_log.inner().clone();
    let playback_generation = generation.clone();
    audio_state.open_manager(
        &generation,
        Some((&payload.operation_id, window.label())),
        Arc::new(move |event| {
            record_tts_playback(&playback_log, &playback_generation, &event);
            for label in ["main", "history"] {
                let _ = callback_app.emit_to(label, "sakura://tts-playback-event", event.clone());
            }
            let _ = observations.send(event);
        }),
        || {
            for label in ["main", "history"] {
                let _ = app_handle.emit_to(
                    label,
                    "sakura://tts-operation-started",
                    json!({"operationId": payload.operation_id}),
                );
            }
        },
    )?;
    Ok(())
}

pub(crate) fn close_playback_window(app: &tauri::AppHandle, owner: &str) {
    let Some((generation, operation)) = app.state::<AudioState>().stop_window(owner, None) else {
        return;
    };
    let Ok(handle) = settings_core_handle(&app.state::<ShellLifecycleState>()) else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        if handle.available_generation_id().ok().flatten().as_deref() == Some(generation.as_str()) {
            let _ = dispatch_settings_request(
                handle,
                None,
                "tts.synthesis.cancel",
                json!({"operationId": operation}),
                Duration::from_secs(3),
            )
            .await;
        }
    });
}

#[tauri::command]
pub(crate) async fn tts_prepare_segment(
    window: WebviewWindow,
    payload: TtsPrepareSegmentRequest,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, AudioState>,
) -> Result<AudioDescriptor, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".to_string());
    }
    prepare_segment(
        payload.operation_id,
        payload.segment_index,
        None,
        app_handle,
        lifecycle,
        audio_state,
    )
    .await
}

#[tauri::command]
pub(crate) async fn tts_prepare_history_segment(
    window: WebviewWindow,
    payload: TtsPrepareHistorySegmentRequest,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, AudioState>,
) -> Result<AudioDescriptor, String> {
    validate_playback_window(window.label())?;
    prepare_segment(
        payload.operation_id,
        payload.segment_index,
        Some(payload.history_entry_id),
        app_handle,
        lifecycle,
        audio_state,
    )
    .await
}

fn validate_playback_window(label: &str) -> Result<(), String> {
    if matches!(label, "main" | "history") {
        Ok(())
    } else {
        Err("PET_WINDOW_REQUIRED".into())
    }
}

async fn prepare_segment(
    operation_id: String,
    segment_index: u64,
    history_entry_id: Option<String>,
    app_handle: tauri::AppHandle,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, AudioState>,
) -> Result<AudioDescriptor, String> {
    if operation_id.trim().is_empty() || operation_id.len() > 128 {
        return Err("TTS_SEGMENT_NOT_AUTHORIZED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let generation_id = handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "STALE_GENERATION".to_string())?;
    let manager = audio_state.manager_for_operation(&generation_id, &operation_id)?;
    let registration_revision = manager.registration_revision()?;
    let mut request = json!({"operationId": operation_id, "segmentIndex": segment_index});
    let request_name = if let Some(entry_id) = history_entry_id {
        request["historyEntryId"] = json!(entry_id);
        "tts.history.prepare"
    } else {
        "tts.synthesis.start"
    };
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        request_name,
        request,
        std::time::Duration::from_secs(305),
    )
    .await?;
    if handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .as_deref()
        != Some(generation_id.as_str())
    {
        return Err("STALE_GENERATION".to_string());
    }
    let descriptor: AudioDescriptor = serde_json::from_value(settings_response_payload(response)?)
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error("AUDIO_RECORDING_INVALID", source_error)
        })?;
    manager.register_at_revision(&descriptor, registration_revision)?;
    app_handle
        .emit_to(
            "main",
            "sakura://tts-synthesis-event",
            json!({
                "type": "tts.synthesis.ready",
                "operationId": operation_id,
                "segmentIndex": segment_index,
                "descriptor": descriptor.clone(),
            }),
        )
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error("TTS_PUBLICATION_FAILED", source_error)
        })?;
    Ok(descriptor)
}

#[tauri::command]
pub(crate) async fn tts_cancel_synthesis(
    window: WebviewWindow,
    payload: TtsCancelSynthesisRequest,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<bool, String> {
    validate_playback_window(window.label())?;
    if payload.operation_id.trim().is_empty() || payload.operation_id.len() > 128 {
        return Err("TTS_SYNTHESIS_CANCELLED".to_string());
    }
    let handle = settings_core_handle(&lifecycle)?;
    let response = dispatch_settings_request(
        handle,
        None,
        "tts.synthesis.cancel",
        json!({"operationId": payload.operation_id}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    Ok(settings_response_payload(response)?
        .get("accepted")
        .and_then(Value::as_bool)
        .unwrap_or(false))
}

#[tauri::command]
pub(crate) fn tts_play_prepared(
    window: WebviewWindow,
    payload: PlayPreparedRequest,
    lifecycle: State<'_, ShellLifecycleState>,
    audio_state: State<'_, AudioState>,
) -> Result<(), String> {
    validate_playback_window(window.label())?;
    let generation_id = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "STALE_GENERATION".to_string())?
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "STALE_GENERATION".to_string())?;
    audio_state.play_if_allowed(&generation_id, payload)
}

#[tauri::command]
pub(crate) fn tts_stop_playback(
    window: WebviewWindow,
    payload: TtsCancelSynthesisRequest,
    audio_state: State<'_, AudioState>,
) -> Result<(), String> {
    validate_playback_window(window.label())?;
    // Playback belongs to the active AudioState, not to whichever Core
    // generation happens to be queryable at command time. During restart the
    // lifecycle intentionally exposes no available generation.
    audio_state.stop_window(window.label(), Some(&payload.operation_id));
    Ok(())
}

const VOICE_CACHE_DEFAULT_MEGABYTES: u64 = 512;

fn voice_cache_display_directory(directory: &Path) -> String {
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};

        let mut components = directory.components();
        if let Some(Component::Prefix(prefix)) = components.next() {
            // Settings use ordinary path spelling; storage and I/O keep canonical paths.
            let mut ordinary = match prefix.kind() {
                Prefix::VerbatimDisk(drive) => PathBuf::from(format!("{}:", char::from(drive))),
                Prefix::VerbatimUNC(server, share) => {
                    let mut unc = PathBuf::from(r"\\");
                    unc.push(server);
                    unc.push(share);
                    unc
                }
                _ => return directory.display().to_string(),
            };
            ordinary.extend(components);
            return ordinary.display().to_string();
        }
    }
    directory.display().to_string()
}

fn voice_cache_document(user_root: &Path) -> (String, u64, bool) {
    let default_dir = user_root.join("data/voice/recordings");
    let mut directory = default_dir.display().to_string();
    let mut max_megabytes = VOICE_CACHE_DEFAULT_MEGABYTES;
    let mut idle_fill = false;
    let path = user_root.join("config/voice_cache.json");
    if let Ok(bytes) = fs::read(&path) {
        if let Ok(value) = serde_json::from_slice::<Value>(&bytes) {
            if value.get("schemaVersion").and_then(Value::as_u64) == Some(1) {
                if let Some(max_bytes) = value.get("maxBytes").and_then(Value::as_u64) {
                    if max_bytes > 0 {
                        max_megabytes = max_bytes.div_ceil(1024 * 1024);
                    }
                }
                if let Some(raw) = value.get("directory").and_then(Value::as_str) {
                    if !raw.trim().is_empty() {
                        directory = raw.to_string();
                    }
                }
                idle_fill = value
                    .get("idleFill")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
            }
        }
    }
    (directory, max_megabytes, idle_fill)
}

impl AudioState {
    fn voice_cache_snapshot(&self) -> Value {
        let (directory, max_megabytes, idle_fill) = voice_cache_document(&self.user_root);
        json!({
            "directory": voice_cache_display_directory(Path::new(&directory)),
            "defaultDirectory": voice_cache_display_directory(&self.user_root.join("data/voice/recordings")),
            "maxMegabytes": max_megabytes,
            "idleFill": idle_fill,
        })
    }

    fn save_voice_cache(
        &self,
        directory: &str,
        max_megabytes: u64,
        idle_fill: bool,
    ) -> Result<Value, String> {
        let max_bytes = max_megabytes
            .checked_mul(1024 * 1024)
            .filter(|value| *value > 0)
            .ok_or_else(|| "VOICE_CACHE_SIZE_INVALID".to_string())?;
        let default_dir = self.user_root.join("data/voice/recordings");
        let trimmed = directory.trim();
        let path = if trimmed.is_empty() {
            default_dir.clone()
        } else {
            PathBuf::from(trimmed)
        };
        if !path.is_absolute() {
            return Err("VOICE_CACHE_DIRECTORY_INVALID".to_string());
        }
        fs::create_dir_all(&path)
            .map_err(|error| format!("VOICE_CACHE_DIRECTORY_INVALID: {error}"))?;
        let resolved = fs::canonicalize(&path)
            .map_err(|error| format!("VOICE_CACHE_DIRECTORY_INVALID: {error}"))?;
        let stored = if resolved == fs::canonicalize(&default_dir).unwrap_or(default_dir) {
            String::new()
        } else {
            resolved.display().to_string()
        };
        let document = json!({
            "schemaVersion": 1,
            "directory": stored,
            "maxBytes": max_bytes,
            "idleFill": idle_fill,
        });
        let mut bytes = serde_json::to_vec_pretty(&document).map_err(|error| error.to_string())?;
        bytes.push(b'\n');
        let config = self.user_root.join("config/voice_cache.json");
        crate::ui_config::atomic_write(&config, &bytes, "VOICE_CACHE")?;
        Ok(self.voice_cache_snapshot())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct VoiceCacheSaveRequest {
    directory: String,
    max_megabytes: u64,
    idle_fill: bool,
}

#[tauri::command]
pub(crate) fn settings_voice_cache_get(
    window: WebviewWindow,
    audio: State<'_, AudioState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    Ok(audio.voice_cache_snapshot())
}

#[tauri::command]
pub(crate) fn settings_voice_cache_save(
    window: WebviewWindow,
    audio: State<'_, AudioState>,
    request: VoiceCacheSaveRequest,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    audio.save_voice_cache(&request.directory, request.max_megabytes, request.idle_fill)
}

#[tauri::command]
pub(crate) async fn settings_voice_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.settings.get",
        json!({}),
        std::time::Duration::from_secs(3),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TTS_SETTINGS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_voice_status_get(
    window: WebviewWindow,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    let window_generation = shell.generation()?;
    let core_generation_id = handle
        .available_generation_id()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "SETTINGS_CORE_UNAVAILABLE".to_string())?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.status.get",
        json!({}),
        std::time::Duration::from_secs(4),
    )
    .await?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let mut payload = settings_response_payload(response)?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| "TTS_STATUS_RESPONSE_INVALID".to_string())?;
    object.insert("windowGeneration".to_string(), json!(window_generation));
    object.insert("coreGenerationId".to_string(), json!(core_generation_id));
    Ok(payload)
}

#[tauri::command]
pub(crate) async fn settings_voice_save(
    window: WebviewWindow,
    window_generation: u64,
    core_generation_id: String,
    draft: Value,
    shell: State<'_, product_shell::ProductShellState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let handle = settings_core_handle(&lifecycle)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    let response = dispatch_settings_request(
        handle.clone(),
        None,
        "tts.settings.save",
        json!({"settings": draft}),
        std::time::Duration::from_secs(5),
    )
    .await?;
    let payload = settings_response_payload(response)?;
    assert_settings_identity(&shell, &handle, window_generation, &core_generation_id)?;
    Ok(payload)
}

// 同一 generation 的窗口和播放器共用队列，等上一条提交完成后再发送下一条，
// 保证旧播放的 stopped 先于替代播放的 started 到达 Core。
async fn forward_playback_observations<F, Fut>(
    mut receiver: tokio::sync::mpsc::UnboundedReceiver<AudioPlaybackEvent>,
    mut observe: F,
) where
    F: FnMut(AudioPlaybackEvent) -> Fut,
    Fut: Future<Output = ()>,
{
    while let Some(event) = receiver.recv().await {
        observe(event).await;
    }
}

async fn observe_tts_playback(
    handle: shell_lifecycle::ShellLifecycleHandle,
    generation_id: String,
    event: AudioPlaybackEvent,
) {
    let current = handle.available_generation_id().ok().flatten();
    if current.as_deref() != Some(generation_id.as_str()) {
        return;
    }
    let error_code = event.error.as_ref().map(|error| error.code);
    let _ = dispatch_settings_request(
        handle,
        None,
        "tts.playback.observe",
        json!({
            "playbackId": event.playback_id,
            "recordingId": event.recording_id,
            "state": event.state,
            "errorCode": error_code,
        }),
        std::time::Duration::from_secs(2),
    )
    .await;
}

fn record_tts_playback(
    runtime_log: &RuntimeLogService,
    generation_id: &str,
    event: &AudioPlaybackEvent,
) {
    let (event_name, message, severity) = match event.state {
        "started" => (
            "tts.playback.started",
            "TTS playback started",
            Severity::Info,
        ),
        "finished" => (
            "tts.playback.finished",
            "TTS playback finished",
            Severity::Info,
        ),
        "stopped" => (
            "tts.playback.stopped",
            "TTS playback stopped",
            Severity::Info,
        ),
        _ => (
            "tts.playback.failed",
            "TTS playback failed",
            Severity::Error,
        ),
    };
    let code = event.error.as_ref().map(|error| error.code);
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, "tts", event_name, message)
            .correlation(Correlation {
                generation_id: Some(generation_id.to_string()),
                request_id: Some(event.playback_id.clone()),
                ..Correlation::default()
            })
            .attributes(json!({
                "playbackId": event.playback_id,
                "recordingId": event.recording_id,
                "segment_index": event.segment_index,
                "segment_count": event.segment_count,
                "status": event.state,
                "code": code,
            })),
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_TEMP_ROOT: AtomicU64 = AtomicU64::new(0);

    #[cfg(windows)]
    #[test]
    fn voice_cache_snapshot_displays_drive_and_unc_paths_without_changing_config() {
        let root = temp_root();
        let state = AudioState::new(root.canonicalize().unwrap());
        let config = root.join("config/voice_cache.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        let default_directory = root.join("data/voice/recordings");
        fs::create_dir_all(&default_directory).unwrap();
        for (stored, displayed) in [
            (
                r"\\?\C:\Sakura 角色\recordings",
                r"C:\Sakura 角色\recordings",
            ),
            (
                r"\\?\UNC\server\share\recordings",
                r"\\server\share\recordings",
            ),
            (r"C:\Sakura\recordings", r"C:\Sakura\recordings"),
            (r"\\server\share\recordings", r"\\server\share\recordings"),
        ] {
            let bytes =
                serde_json::to_vec(&json!({"schemaVersion": 1, "directory": stored})).unwrap();
            fs::write(&config, &bytes).unwrap();
            let snapshot = state.voice_cache_snapshot();
            assert_eq!(snapshot["directory"], displayed);
            let displayed_default = snapshot["defaultDirectory"].as_str().unwrap();
            assert!(!displayed_default.starts_with(r"\\?\"));
            // Windows TEMP may use an 8.3 alias while AudioState uses the long path.
            assert_eq!(
                Path::new(displayed_default).canonicalize().unwrap(),
                default_directory.canonicalize().unwrap()
            );
            assert_eq!(fs::read(&config).unwrap(), bytes);
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn voice_cache_save_and_reload_keep_display_paths_and_canonical_storage_consistent() {
        let root = temp_root();
        let state = AudioState::new(root.canonicalize().unwrap());
        let directory = root.join("cache with 空格");
        let saved = state
            .save_voice_cache(&directory.display().to_string(), 128, true)
            .unwrap();
        assert_eq!(state.voice_cache_snapshot(), saved);
        let displayed = saved["directory"].as_str().unwrap();
        #[cfg(windows)]
        assert!(!displayed.starts_with(r"\\?\"));
        assert_eq!(
            Path::new(displayed).canonicalize().unwrap(),
            directory.canonicalize().unwrap()
        );
        let config = root.join("config/voice_cache.json");
        let document: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        assert_eq!(
            document["directory"],
            directory.canonicalize().unwrap().display().to_string()
        );
        assert_eq!(state.save_voice_cache(displayed, 128, true).unwrap(), saved);

        let default = state.save_voice_cache("", 64, false).unwrap();
        assert_eq!(state.voice_cache_snapshot(), default);
        assert_eq!(default["directory"], default["defaultDirectory"]);
        #[cfg(windows)]
        assert!(!default["directory"].as_str().unwrap().starts_with(r"\\?\"));
        let document: Value = serde_json::from_slice(&fs::read(config).unwrap()).unwrap();
        assert_eq!(document["directory"], "");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn voice_cache_accepts_user_capacity_and_custom_directory_when_default_is_unavailable() {
        let root = temp_root();
        let state = AudioState::new(root.canonicalize().unwrap());
        let default_dir = root.join("data/voice/recordings");
        fs::create_dir_all(default_dir.parent().unwrap()).unwrap();
        fs::write(&default_dir, b"occupied").unwrap();
        let directory = root.join("selected-cache");
        for megabytes in [1, 32768] {
            let saved = state
                .save_voice_cache(&directory.display().to_string(), megabytes, false)
                .unwrap();
            assert_eq!(saved["maxMegabytes"], megabytes);
            assert_eq!(state.voice_cache_snapshot(), saved);
            let document: Value =
                serde_json::from_slice(&fs::read(root.join("config/voice_cache.json")).unwrap())
                    .unwrap();
            assert_eq!(document["maxBytes"], megabytes * 1024 * 1024);
        }
        assert_eq!(fs::read(&default_dir).unwrap(), b"occupied");
        let before = fs::read(root.join("config/voice_cache.json")).unwrap();
        for invalid in [0, u64::MAX] {
            assert_eq!(
                state.save_voice_cache(&directory.display().to_string(), invalid, false),
                Err("VOICE_CACHE_SIZE_INVALID".to_string())
            );
        }
        assert_eq!(
            fs::read(root.join("config/voice_cache.json")).unwrap(),
            before
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn playback_observations_wait_for_submission_before_forwarding_the_next_event() {
        use std::task::{Context, Poll, Waker};

        let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
        let (release, held) = tokio::sync::oneshot::channel::<()>();
        let mut held = Some(held);
        let entered = Arc::new(Mutex::new(Vec::new()));
        let completed = Arc::new(Mutex::new(Vec::new()));
        let entered_observer = entered.clone();
        let completed_observer = completed.clone();
        let mut forwarding = Box::pin(forward_playback_observations(receiver, move |event| {
            let held = held.take();
            let entered = entered_observer.clone();
            let completed = completed_observer.clone();
            async move {
                let identity = (event.playback_id, event.state);
                entered.lock().unwrap().push(identity.clone());
                if let Some(held) = held {
                    held.await.unwrap();
                }
                completed.lock().unwrap().push(identity);
            }
        }));
        let expected = vec![
            ("old".to_string(), "started"),
            ("old".to_string(), "stopped"),
            ("new".to_string(), "started"),
            ("new".to_string(), "finished"),
        ];
        for (playback_id, state) in &expected {
            sender
                .send(AudioPlaybackEvent {
                    playback_id: playback_id.clone(),
                    recording_id: Some("recording".to_string()),
                    segment_index: None,
                    segment_count: None,
                    state,
                    error: None,
                })
                .unwrap();
        }
        // 固定第一条 Core 提交尚未完成；旧的逐事件并发投递会让后续事件越过它。
        let mut context = Context::from_waker(Waker::noop());
        assert!(matches!(
            forwarding.as_mut().poll(&mut context),
            Poll::Pending
        ));
        assert_eq!(*entered.lock().unwrap(), vec![expected[0].clone()]);
        assert!(completed.lock().unwrap().is_empty());
        release.send(()).unwrap();
        assert!(matches!(
            forwarding.as_mut().poll(&mut context),
            Poll::Pending
        ));
        assert_eq!(*entered.lock().unwrap(), expected);
        assert_eq!(*completed.lock().unwrap(), expected);
        drop(sender);
        assert!(matches!(
            forwarding.as_mut().poll(&mut context),
            Poll::Ready(())
        ));
    }

    #[test]
    fn playback_observation_queue_closes_with_its_generation_and_state() {
        let state = AudioState::new(PathBuf::new());
        let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        *state.observations.lock().unwrap() = Some(("new".into(), sender));
        state.shutdown_generation("old");
        assert!(matches!(
            receiver.try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Empty)
        ));
        state.shutdown_generation("new");
        assert!(matches!(
            receiver.try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected)
        ));
        let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        *state.observations.lock().unwrap() = Some(("last".into(), sender));
        drop(state);
        assert!(matches!(
            receiver.try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected)
        ));
    }

    #[test]
    fn saved_voice_commands_are_limited_to_chat_and_history_windows() {
        assert!(validate_playback_window("main").is_ok());
        assert!(validate_playback_window("history").is_ok());
        for label in ["settings", "studio", "plugin-panel", "capture-1"] {
            assert!(validate_playback_window(label).is_err());
        }
    }

    #[test]
    fn new_window_operation_rejects_old_preparation_and_old_window_stop() {
        let root = temp_root();
        let state = AudioState::new(root.clone());
        let first = state
            .open_manager(
                "generation",
                Some(("history-old", "history")),
                Arc::new(|_| {}),
                || {},
            )
            .unwrap();
        let revision = first.registration_revision().unwrap();
        state
            .open_manager(
                "generation",
                Some(("chat-new", "main")),
                Arc::new(|_| {}),
                || {},
            )
            .unwrap();
        assert!(state
            .manager_for_operation("generation", "history-old")
            .is_err());
        assert!(state.stop_window("history", Some("history-old")).is_none());
        assert!(state.stop_window("main", Some("chat-old")).is_none());
        let audio = descriptor("0123456789abcdef0123456789abcdef", wav_bytes().len() as u64);
        fs::write(
            root.join("data/cache/tts/runtime-v2/generation")
                .join(format!("{}.wav", audio.opaque_id)),
            wav_bytes(),
        )
        .unwrap();
        assert!(first.register_at_revision(&audio, revision).is_err());
        assert!(state
            .manager_for_operation("generation", "chat-new")
            .is_ok());
        assert_eq!(
            state.stop_window("main", None),
            Some(("generation".into(), "chat-new".into()))
        );
        assert!(state
            .manager_for_operation("generation", "chat-new")
            .is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn generation_end_revokes_history_audio_without_stopping_a_new_generation() {
        let root = temp_root();
        let state = AudioState::new(root.clone());
        let old = state
            .open_manager(
                "old-generation",
                Some(("history-old", "history")),
                Arc::new(|_| {}),
                || {},
            )
            .unwrap();
        state.shutdown_generation("old-generation");
        assert!(old.registration_revision().is_err());
        assert!(state
            .manager_for_operation("old-generation", "history-old")
            .is_err());
        state
            .open_manager(
                "new-generation",
                Some(("history-new", "history")),
                Arc::new(|_| {}),
                || {},
            )
            .unwrap();
        state.shutdown_generation("old-generation");
        assert!(state
            .manager_for_operation("new-generation", "history-new")
            .is_ok());
        state.shutdown();
        fs::remove_dir_all(root).unwrap();
    }

    fn temp_root() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let sequence = NEXT_TEMP_ROOT.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "sakura-audio-gate-{}-{nonce}-{sequence}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn wav_bytes() -> Vec<u8> {
        vec![
            b'R', b'I', b'F', b'F', 36, 0, 0, 0, b'W', b'A', b'V', b'E', b'f', b'm', b't', b' ',
            16, 0, 0, 0, 1, 0, 1, 0, 0x80, 0x3e, 0, 0, 0, 0x7d, 0, 0, 2, 0, 16, 0, b'd', b'a',
            b't', b'a', 0, 0, 0, 0,
        ]
    }

    fn descriptor(id: &str, len: u64) -> AudioDescriptor {
        AudioDescriptor {
            opaque_id: id.to_string(),
            recording_id: Some("recording-1".to_string()),
            segment_index: Some(1),
            segment_count: Some(3),
            media_type: "audio/wav".to_string(),
            byte_length: len,
            expires_at: (OffsetDateTime::now_utc() + time::Duration::minutes(5))
                .format(&Rfc3339)
                .unwrap(),
        }
    }

    #[test]
    fn plugin_kernel_v3_tts_cancel_accepts_only_operation_identity() {
        let request: TtsCancelSynthesisRequest =
            serde_json::from_value(json!({"operationId": "operation-1"})).unwrap();
        assert_eq!(request.operation_id, "operation-1");
        assert!(serde_json::from_value::<TtsCancelSynthesisRequest>(json!({
            "requestId": "tts-private-job"
        }))
        .is_err());
        assert!(serde_json::from_value::<TtsCancelSynthesisRequest>(json!({
            "operationId": "operation-1",
            "requestId": "tts-private-job"
        }))
        .is_err());
    }

    #[test]
    fn wp_4_05_playback_failure_is_logged_at_the_audio_callback_source() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sakura-tts-playback-log-{}-{nonce}",
            std::process::id()
        ));
        let path = root.join("data/logs/sakura-runtime.log");
        let runtime_log = RuntimeLogService::start(path.clone());
        record_tts_playback(
            &runtime_log,
            "generation-tts-1",
            &AudioPlaybackEvent {
                playback_id: "playback-1".to_string(),
                recording_id: Some("recording-1".to_string()),
                segment_index: Some(1),
                segment_count: Some(3),
                state: "failed",
                error: Some(AudioPlaybackError {
                    code: "AUDIO_DEVICE_UNAVAILABLE",
                    message: "not persisted".to_string(),
                }),
            },
        );
        let records = runtime_log.viewer_snapshot(None).unwrap().records;
        assert!(records.last().unwrap().message.ends_with("（2/3）"));
        runtime_log.drain_and_shutdown_for_test();
        let contents = std::fs::read_to_string(&path).unwrap();
        assert!(contents.contains("[TTS]"));
        assert!(contents.contains("code=AUDIO_DEVICE_UNAVAILABLE"));
        assert!(!contents.contains("not persisted"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4_05_opaque_gate_derives_contained_path_and_consumes_once() {
        let root = temp_root();
        let id = "0123456789abcdef0123456789abcdef";
        let bytes = wav_bytes();
        fs::write(root.join(format!("{id}.wav")), &bytes).unwrap();
        let registry = AudioRegistry::new(root.clone()).unwrap();
        registry
            .register(&descriptor(id, bytes.len() as u64))
            .unwrap();
        let audio = registry.take(id).unwrap();
        assert_eq!(audio.segment_index, Some(1));
        assert_eq!(audio.segment_count, Some(3));
        assert_eq!(
            audio.path,
            root.join(format!("{id}.wav")).canonicalize().unwrap()
        );
        assert_eq!(registry.take(id).unwrap_err(), "AUDIO_RECORDING_INVALID");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4_05_gate_rejects_bad_length_format_and_opaque_identity() {
        let root = temp_root();
        let id = "fedcba9876543210fedcba9876543210";
        fs::write(root.join(format!("{id}.wav")), b"not wav").unwrap();
        let registry = AudioRegistry::new(root.clone()).unwrap();
        assert!(registry.register(&descriptor(id, 7)).is_err());
        assert!(registry.register(&descriptor("../escape", 7)).is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_4_05_gate_rechecks_expiry_when_descriptor_is_consumed() {
        let root = temp_root();
        let id = "00112233445566778899aabbccddeeff";
        let bytes = wav_bytes();
        let path = root.join(format!("{id}.wav"));
        fs::write(&path, &bytes).unwrap();
        let registry = AudioRegistry::new(root.clone()).unwrap();
        registry
            .register(&descriptor(id, bytes.len() as u64))
            .unwrap();
        registry
            .items
            .lock()
            .unwrap()
            .get_mut(id)
            .unwrap()
            .expires_at = OffsetDateTime::now_utc() - time::Duration::seconds(1);

        assert_eq!(registry.take(id).unwrap_err(), "AUDIO_RECORDING_INVALID");
        assert!(!path.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_03_character_switch_stop_invalidates_late_unconsumed_audio_descriptor() {
        let root = temp_root();
        let id = "ffeeddccbbaa99887766554433221100";
        let manager = AudioManager::start(root.clone(), Arc::new(|_| {})).unwrap();
        let revision = manager.registration_revision().unwrap();
        manager.stop_and_clear().unwrap();
        let bytes = wav_bytes();
        let path = root.join(format!("{id}.wav"));
        fs::write(&path, &bytes).unwrap();

        assert_eq!(
            manager
                .register_at_revision(&descriptor(id, bytes.len() as u64), revision)
                .unwrap_err(),
            "STALE_GENERATION"
        );
        assert!(!path.exists());
        manager.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn wp_5_03_character_switch_shutdown_releases_the_active_audio_generation() {
        let root = temp_root();
        let state = AudioState::new(root.clone());
        let manager = state
            .manager("generation-character-a", Arc::new(|_| {}))
            .unwrap();

        state.shutdown();

        assert!(matches!(
            state.current("generation-character-a"),
            Err(error) if error == "STALE_GENERATION"
        ));
        assert_eq!(
            manager.stop_and_clear().unwrap_err(),
            "AUDIO_PLAYBACK_FAILED"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn voice_input_drops_pending_playback_and_rejects_late_synthesis() {
        let root = temp_root();
        let state = AudioState::new(root.clone());
        let manager = state.manager("generation-asr", Arc::new(|_| {})).unwrap();
        let revision = manager.registration_revision().unwrap();
        let mut pause = state.pause_for_input();
        assert!(
            matches!(state.manager("generation-asr", Arc::new(|_| {})), Err(error) if error == "TTS_PAUSED_FOR_VOICE_INPUT")
        );
        assert_eq!(
            state
                .play_if_allowed(
                    "generation-asr",
                    PlayPreparedRequest {
                        opaque_id: "discarded".into(),
                        playback_id: "skip".into()
                    }
                )
                .unwrap_err(),
            "TTS_PAUSED_FOR_VOICE_INPUT"
        );
        pause.release();
        assert!(
            matches!(state.current("generation-asr"), Err(error) if error == "STALE_GENERATION")
        );
        assert!(manager.registration_revision().is_err());
        assert_eq!(
            manager
                .register_at_revision(
                    &descriptor("0123456789abcdef0123456789abcdef", 44),
                    revision,
                )
                .unwrap_err(),
            "STALE_GENERATION"
        );
        let fresh = state.manager("generation-asr", Arc::new(|_| {})).unwrap();
        assert!(!Arc::ptr_eq(&fresh, &manager));
        state.shutdown();
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn failed_microphone_open_releases_playback_pause_without_allowing_an_early_stop() {
        let root = temp_root();
        let state = AudioState::new(root.clone());
        {
            let _device_open_guard = state.pause_for_input();
            // A requested UI stop does not own this guard. Until the actual
            // stream scope ends, the host still rejects TTS playback.
            assert_eq!(
                state
                    .play_if_allowed(
                        "generation",
                        PlayPreparedRequest {
                            opaque_id: "pending".into(),
                            playback_id: "new".into()
                        }
                    )
                    .unwrap_err(),
                "TTS_PAUSED_FOR_VOICE_INPUT"
            );
        }
        assert!(state.manager("generation", Arc::new(|_| {})).is_ok());
        state.shutdown();
        let _ = fs::remove_dir_all(root);
    }
}
