use std::collections::BTreeMap;
use std::fs::{self, File};
use std::path::PathBuf;
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use rodio::source::{SineWave, Source};
use rodio::{Decoder, DeviceSinkBuilder, Player};
use serde::Serialize;
use serde_json::{json, Value};

const PROTOTYPE_FREQUENCY_HZ: f32 = 660.0;
const PROTOTYPE_DURATION_MS: u64 = 180;
const MAX_TTS_AUDIO_BYTES: u64 = 64 * 1024 * 1024;
const AUDIO_RESOURCE_TTL: Duration = Duration::from_secs(5 * 60);

#[derive(Debug, Clone)]
pub struct RegisteredAudioResource {
    pub path: PathBuf,
    registered_at: std::time::Instant,
}

#[derive(Clone)]
pub struct AudioResourceRegistry {
    root: PathBuf,
    items: Arc<Mutex<BTreeMap<String, RegisteredAudioResource>>>,
}

impl AudioResourceRegistry {
    pub fn new(root: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&root).map_err(|error| error.to_string())?;
        let root = root.canonicalize().map_err(|error| error.to_string())?;
        Ok(Self {
            root,
            items: Arc::new(Mutex::new(BTreeMap::new())),
        })
    }

    pub fn register(&self, _session_id: &str, resource: &Value) -> Result<Value, String> {
        self.cleanup_expired();
        let id = required_string(resource, "id")?;
        let path = PathBuf::from(required_string(resource, "path")?)
            .canonicalize()
            .map_err(|error| format!("audio resource path is invalid: {error}"))?;
        path.strip_prefix(&self.root)
            .map_err(|_| "audio resource escaped the TTS cache root".to_string())?;
        let media_type = required_string(resource, "mediaType")?;
        if media_type != "audio/wav" {
            return Err("only audio/wav resources are accepted".into());
        }
        let actual_size = path.metadata().map_err(|error| error.to_string())?.len();
        let declared_size = resource
            .get("byteLength")
            .and_then(Value::as_u64)
            .unwrap_or(actual_size);
        if actual_size != declared_size || actual_size > MAX_TTS_AUDIO_BYTES {
            return Err("audio resource size is invalid".into());
        }
        let expires_at = required_string(resource, "expiresAt")?;
        let registered = RegisteredAudioResource {
            path,
            registered_at: std::time::Instant::now(),
        };
        let mut items = self.items.lock().expect("audio resource lock poisoned");
        if items.insert(id.clone(), registered).is_some() {
            return Err("duplicate audio resource ID".into());
        }
        Ok(json!({
            "version": 1,
            "id": id,
            "mediaType": media_type,
            "byteLength": actual_size,
            "expiresAt": expires_at,
        }))
    }

    pub fn take(&self, resource_id: &str) -> Result<RegisteredAudioResource, String> {
        self.cleanup_expired();
        self.items
            .lock()
            .expect("audio resource lock poisoned")
            .remove(resource_id)
            .ok_or_else(|| "audio resource does not exist or already expired".to_string())
    }

    pub fn clear_all(&self) {
        let paths = {
            let mut items = self.items.lock().expect("audio resource lock poisoned");
            std::mem::take(&mut *items)
                .into_values()
                .map(|item| item.path)
                .collect::<Vec<_>>()
        };
        cleanup_paths(paths);
    }

    pub fn cleanup_expired(&self) {
        let paths = {
            let mut items = self.items.lock().expect("audio resource lock poisoned");
            let ids: Vec<String> = items
                .iter()
                .filter(|(_id, item)| item.registered_at.elapsed() >= AUDIO_RESOURCE_TTL)
                .map(|(id, _item)| id.clone())
                .collect();
            ids.into_iter()
                .filter_map(|id| items.remove(&id).map(|item| item.path))
                .collect::<Vec<_>>()
        };
        cleanup_paths(paths);
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioPlaybackEvent {
    pub playback_id: String,
    pub state: String,
    pub error: Option<String>,
}

pub type AudioEventCallback = Arc<dyn Fn(AudioPlaybackEvent) + Send + Sync + 'static>;

enum AudioCommand {
    Play {
        playback_id: String,
        path: PathBuf,
        volume: f32,
    },
    Stop,
    SetVolume(f32),
    Shutdown,
}

struct AudioManagerInner {
    registry: AudioResourceRegistry,
    commands: Mutex<Option<Sender<AudioCommand>>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

#[derive(Clone)]
pub struct AudioManager {
    inner: Arc<AudioManagerInner>,
}

impl AudioManager {
    pub fn start(cache_root: PathBuf, callback: AudioEventCallback) -> Result<Self, String> {
        let registry = AudioResourceRegistry::new(cache_root)?;
        let thread_registry = registry.clone();
        let (commands, receiver) = mpsc::channel();
        let handle = thread::Builder::new()
            .name("sakura-audio-playback".into())
            .spawn(move || audio_loop(receiver, thread_registry, callback))
            .map_err(|error| error.to_string())?;
        Ok(Self {
            inner: Arc::new(AudioManagerInner {
                registry,
                commands: Mutex::new(Some(commands)),
                thread: Mutex::new(Some(handle)),
            }),
        })
    }

    pub fn register_brain_resource(
        &self,
        session_id: &str,
        resource: &Value,
    ) -> Result<Value, String> {
        self.inner.registry.register(session_id, resource)
    }

    pub fn play(&self, resource_id: &str, playback_id: &str, volume: f32) -> Result<(), String> {
        let resource = self.inner.registry.take(resource_id)?;
        self.send(AudioCommand::Play {
            playback_id: playback_id.to_string(),
            path: resource.path,
            volume: volume.clamp(0.0, 1.0),
        })
    }

    pub fn stop(&self) -> Result<(), String> {
        self.send(AudioCommand::Stop)
    }

    pub fn set_volume(&self, volume: f32) -> Result<(), String> {
        self.send(AudioCommand::SetVolume(volume.clamp(0.0, 1.0)))
    }

    pub fn reset(&self) {
        self.inner.registry.clear_all();
        let _ = self.stop();
    }

    pub fn shutdown(&self) {
        self.inner.registry.clear_all();
        let sender = self
            .inner
            .commands
            .lock()
            .expect("audio command lock poisoned")
            .take();
        if let Some(sender) = sender {
            let _ = sender.send(AudioCommand::Shutdown);
        }
        if let Some(handle) = self
            .inner
            .thread
            .lock()
            .expect("audio thread lock poisoned")
            .take()
        {
            let _ = handle.join();
        }
    }

    fn send(&self, command: AudioCommand) -> Result<(), String> {
        self.inner
            .commands
            .lock()
            .expect("audio command lock poisoned")
            .as_ref()
            .ok_or_else(|| "audio manager is stopped".to_string())?
            .send(command)
            .map_err(|_| "audio playback thread is stopped".to_string())
    }
}

impl Drop for AudioManagerInner {
    fn drop(&mut self) {
        if let Some(sender) = self.commands.get_mut().ok().and_then(Option::take) {
            let _ = sender.send(AudioCommand::Shutdown);
        }
        if let Some(handle) = self.thread.get_mut().ok().and_then(Option::take) {
            let _ = handle.join();
        }
        self.registry.clear_all();
    }
}

struct ActivePlayback {
    playback_id: String,
    path: PathBuf,
    player: Player,
}

fn audio_loop(
    receiver: mpsc::Receiver<AudioCommand>,
    registry: AudioResourceRegistry,
    callback: AudioEventCallback,
) {
    let output = DeviceSinkBuilder::open_default_sink().ok();
    let mut active: Option<ActivePlayback> = None;
    loop {
        match receiver.recv_timeout(Duration::from_millis(25)) {
            Ok(AudioCommand::Play {
                playback_id,
                path,
                volume: requested_volume,
            }) => {
                finish_active(&mut active, "stopped", &callback);
                let result = (|| {
                    let output = output
                        .as_ref()
                        .ok_or_else(|| "系统没有可用的音频输出设备。".to_string())?;
                    let file = File::open(&path).map_err(|error| error.to_string())?;
                    let source = Decoder::try_from(file).map_err(|error| error.to_string())?;
                    let player = Player::connect_new(output.mixer());
                    player.set_volume(requested_volume);
                    player.append(source);
                    Ok::<Player, String>(player)
                })();
                match result {
                    Ok(player) => {
                        callback(AudioPlaybackEvent {
                            playback_id: playback_id.clone(),
                            state: "started".into(),
                            error: None,
                        });
                        active = Some(ActivePlayback {
                            playback_id,
                            path,
                            player,
                        });
                    }
                    Err(error) => {
                        let _ = fs::remove_file(path);
                        callback(AudioPlaybackEvent {
                            playback_id,
                            state: "error".into(),
                            error: Some(error),
                        });
                    }
                }
            }
            Ok(AudioCommand::Stop) => finish_active(&mut active, "stopped", &callback),
            Ok(AudioCommand::SetVolume(requested)) => {
                if let Some(current) = active.as_ref() {
                    current.player.set_volume(requested);
                }
            }
            Ok(AudioCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                finish_active(&mut active, "stopped", &callback);
                return;
            }
            Err(RecvTimeoutError::Timeout) => {
                if active
                    .as_ref()
                    .is_some_and(|current| current.player.empty())
                {
                    finish_active(&mut active, "finished", &callback);
                }
                registry.cleanup_expired();
            }
        }
    }
}

fn finish_active(active: &mut Option<ActivePlayback>, state: &str, callback: &AudioEventCallback) {
    let Some(current) = active.take() else {
        return;
    };
    current.player.stop();
    let _ = fs::remove_file(current.path);
    callback(AudioPlaybackEvent {
        playback_id: current.playback_id,
        state: state.to_string(),
        error: None,
    });
}

fn required_string(value: &Value, key: &str) -> Result<String, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("audio resource {key} is required"))
}

fn cleanup_paths(paths: Vec<PathBuf>) {
    for path in paths {
        let _ = fs::remove_file(path);
    }
}

#[tauri::command]
pub async fn play_audio_prototype() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(play_tone)
        .await
        .map_err(|error| error.to_string())?
}

fn play_tone() -> Result<(), String> {
    let device = DeviceSinkBuilder::open_default_sink().map_err(|error| error.to_string())?;
    let player = Player::connect_new(device.mixer());
    player.append(
        SineWave::new(PROTOTYPE_FREQUENCY_HZ)
            .take_duration(Duration::from_millis(PROTOTYPE_DURATION_MS))
            .amplify(0.12),
    );
    player.sleep_until_end();
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::json;
    use tempfile::TempDir;

    use super::*;

    #[test]
    fn prototype_tone_is_short_and_audible() {
        assert!((200.0..=2_000.0).contains(&PROTOTYPE_FREQUENCY_HZ));
        assert!((50..=500).contains(&PROTOTYPE_DURATION_MS));
    }

    #[test]
    fn audio_resource_registry_hides_paths_and_consumes_tokens_once() {
        let temp = TempDir::new().unwrap();
        let cache = temp.path().join("data/cache/tts");
        fs::create_dir_all(&cache).unwrap();
        let path = cache.join("voice.wav");
        fs::write(&path, b"RIFF-audio").unwrap();
        let registry = AudioResourceRegistry::new(cache.clone()).unwrap();

        let public = registry
            .register(
                "session-1",
                &json!({
                    "version": 1,
                    "id": "audio-token",
                    "path": path,
                    "mediaType": "audio/wav",
                    "byteLength": 10,
                    "expiresAt": "2099-01-01T00:00:00+00:00"
                }),
            )
            .unwrap();

        assert_eq!(public["id"], "audio-token");
        assert!(public.get("path").is_none());
        assert_eq!(
            registry.take("audio-token").unwrap().path,
            path.canonicalize().unwrap()
        );
        assert!(registry.take("audio-token").is_err());
    }

    #[test]
    fn audio_resource_registry_rejects_escape_and_cleans_session_files() {
        let temp = TempDir::new().unwrap();
        let cache = temp.path().join("cache");
        fs::create_dir_all(&cache).unwrap();
        let outside = temp.path().join("outside.wav");
        fs::write(&outside, b"RIFF-outside").unwrap();
        let inside = cache.join("inside.wav");
        fs::write(&inside, b"RIFF-inside").unwrap();
        let registry = AudioResourceRegistry::new(cache).unwrap();

        assert!(registry
            .register(
                "session-1",
                &json!({
                    "id": "escape",
                    "path": outside,
                    "mediaType": "audio/wav",
                    "byteLength": 12,
                    "expiresAt": "2099-01-01T00:00:00+00:00"
                }),
            )
            .is_err());
        registry
            .register(
                "session-1",
                &json!({
                    "id": "inside",
                    "path": inside,
                    "mediaType": "audio/wav",
                    "byteLength": 11,
                    "expiresAt": "2099-01-01T00:00:00+00:00"
                }),
            )
            .unwrap();

        registry.clear_all();
        assert!(!inside.exists());
    }
}
