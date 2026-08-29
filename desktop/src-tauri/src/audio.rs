use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    sync::{mpsc, Arc, Mutex},
    thread::{self, JoinHandle},
    time::Duration,
};

use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player};
use serde::{Deserialize, Serialize};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const MAX_TTS_AUDIO_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DESCRIPTOR_FUTURE_SECONDS: i64 = 10 * 60;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AudioDescriptor {
    pub opaque_id: String,
    pub recording_id: Option<String>,
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
    pub message: &'static str,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioPlaybackEvent {
    pub playback_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recording_id: Option<String>,
    pub state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<AudioPlaybackError>,
}

#[derive(Debug)]
struct RegisteredAudio {
    path: PathBuf,
    expires_at: OffsetDateTime,
    recording_id: Option<String>,
}

#[derive(Clone)]
struct AudioRegistry {
    root: PathBuf,
    items: Arc<Mutex<BTreeMap<String, RegisteredAudio>>>,
}

impl AudioRegistry {
    fn new(root: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&root).map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        let root = root
            .canonicalize()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
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
        let metadata =
            fs::symlink_metadata(&unresolved).map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        let path = unresolved
            .canonicalize()
            .map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
        path.strip_prefix(&self.root)
            .map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
        let actual = metadata.len();
        if actual == 0 || actual != descriptor.byte_length || actual > MAX_TTS_AUDIO_BYTES {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        validate_wav_header(&path)?;
        let mut items = self
            .items
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        if items.contains_key(&descriptor.opaque_id) {
            return Err("AUDIO_RECORDING_INVALID".to_string());
        }
        items.insert(
            descriptor.opaque_id.clone(),
            RegisteredAudio {
                path,
                expires_at,
                recording_id: descriptor.recording_id.clone(),
            },
        );
        Ok(())
    }

    fn take(&self, opaque_id: &str) -> Result<RegisteredAudio, String> {
        validate_opaque_id(opaque_id)?;
        let item = self
            .items
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?
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
        path: PathBuf,
    },
    Stop,
    Shutdown,
}

struct ActivePlayback {
    playback_id: String,
    recording_id: Option<String>,
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
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        Ok(Self {
            registry,
            registration_revision: Mutex::new(0),
            sender: Mutex::new(Some(sender)),
            thread: Mutex::new(Some(thread)),
        })
    }

    pub fn registration_revision(&self) -> Result<u64, String> {
        self.registration_revision
            .lock()
            .map(|revision| *revision)
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())
    }

    pub fn register_at_revision(
        &self,
        descriptor: &AudioDescriptor,
        expected_revision: u64,
    ) -> Result<(), String> {
        let revision = self
            .registration_revision
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        if *revision != expected_revision {
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
            path: audio.path,
        })
    }

    pub fn stop_and_clear(&self) -> Result<(), String> {
        let mut revision = self
            .registration_revision
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        *revision = revision.wrapping_add(1);
        self.registry.clear();
        drop(revision);
        self.send(AudioCommand::Stop)
    }

    pub fn shutdown(&self) {
        self.registry.clear();
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
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?
            .as_ref()
            .ok_or_else(|| "AUDIO_PLAYBACK_FAILED".to_string())?
            .send(command)
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())
    }
}

impl Drop for AudioManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub struct AudioState {
    user_root: PathBuf,
    active: Mutex<Option<(String, Arc<AudioManager>)>>,
}

impl AudioState {
    pub fn new(user_root: PathBuf) -> Self {
        Self {
            user_root,
            active: Mutex::new(None),
        }
    }

    pub fn manager(
        &self,
        generation_id: &str,
        callback: AudioEventCallback,
    ) -> Result<Arc<AudioManager>, String> {
        validate_generation_id(generation_id)?;
        let mut active = self
            .active
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?;
        if let Some((active_generation, manager)) = active.as_ref() {
            if active_generation == generation_id {
                return Ok(manager.clone());
            }
        }
        if let Some((_generation, manager)) = active.take() {
            manager.shutdown();
        }
        let root = self
            .user_root
            .join("data/cache/tts/runtime-v2")
            .join(generation_id);
        let manager = Arc::new(AudioManager::start(root, callback)?);
        *active = Some((generation_id.to_string(), manager.clone()));
        Ok(manager)
    }

    pub fn current(&self, generation_id: &str) -> Result<Arc<AudioManager>, String> {
        self.active
            .lock()
            .map_err(|_| "AUDIO_PLAYBACK_FAILED".to_string())?
            .as_ref()
            .filter(|(active, _)| active == generation_id)
            .map(|(_, manager)| manager.clone())
            .ok_or_else(|| "STALE_GENERATION".to_string())
    }

    pub fn shutdown(&self) {
        if let Ok(mut active) = self.active.lock() {
            if let Some((_generation, manager)) = active.take() {
                let _ = manager.stop_and_clear();
                manager.shutdown();
            }
        }
    }
}

impl Drop for AudioState {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn playback_loop(receiver: mpsc::Receiver<AudioCommand>, callback: AudioEventCallback) {
    let mut active: Option<ActivePlayback> = None;
    loop {
        match receiver.recv_timeout(Duration::from_millis(20)) {
            Ok(AudioCommand::Play {
                playback_id,
                recording_id,
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
                                state: "started",
                                error: None,
                            },
                        );
                        active = Some(ActivePlayback {
                            playback_id,
                            recording_id,
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
    let sink = DeviceSinkBuilder::open_default_sink().map_err(|_| AudioPlaybackError {
        code: "AUDIO_DEVICE_UNAVAILABLE",
        message: "No system default audio output is available",
    })?;
    let file = File::open(path).map_err(|_| AudioPlaybackError {
        code: "AUDIO_RECORDING_INVALID",
        message: "Prepared audio is unavailable",
    })?;
    let decoder = Decoder::try_from(file).map_err(|_| AudioPlaybackError {
        code: "AUDIO_FORMAT_UNSUPPORTED",
        message: "Prepared audio is not a supported WAV file",
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
            state,
            error: None,
        },
    );
}

fn emit_audio_event(callback: &AudioEventCallback, event: AudioPlaybackEvent) {
    callback(event);
}

fn validate_expiry(value: &str) -> Result<OffsetDateTime, String> {
    let expiry = OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
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
        .map_err(|_| "AUDIO_RECORDING_INVALID".to_string())?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_TEMP_ROOT: AtomicU64 = AtomicU64::new(0);

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
            media_type: "audio/wav".to_string(),
            byte_length: len,
            expires_at: (OffsetDateTime::now_utc() + time::Duration::minutes(5))
                .format(&Rfc3339)
                .unwrap(),
        }
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
        assert_eq!(
            registry.take(id).unwrap().path,
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
}
