use std::time::Duration;

use rodio::source::{SineWave, Source};
use rodio::{DeviceSinkBuilder, Player};

const PROTOTYPE_FREQUENCY_HZ: f32 = 660.0;
const PROTOTYPE_DURATION_MS: u64 = 180;

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
    use super::*;

    #[test]
    fn prototype_tone_is_short_and_audible() {
        assert!((200.0..=2_000.0).contains(&PROTOTYPE_FREQUENCY_HZ));
        assert!((50..=500).contains(&PROTOTYPE_DURATION_MS));
    }
}
