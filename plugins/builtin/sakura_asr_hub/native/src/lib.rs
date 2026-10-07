//! Native half of the built-in ASR core plugin.
//! Owns input devices, capture, level extraction and the recognizer WAV format.
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde_json::{json, Value};
use std::{
    fs::{self, OpenOptions},
    io::{BufWriter, Write},
    path::Path,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant, SystemTime},
};

const MAX_SECONDS: usize = 60;
const OUTPUT_RATE: usize = 16_000;
const FRAME_INTERVAL: Duration = Duration::from_millis(50);

fn diagnostic_error(code: &str, source: impl std::fmt::Display) -> String {
    format!("{code}|{source}")
}

/// Shell-owned permissions, window lifetime, playback suspension and event delivery.
/// The shell never receives PCM or chooses the microphone for the plugin.
pub trait CaptureHost {
    fn check_active(&self) -> Result<(), String>;
    fn ready(&mut self) -> Result<(), String>;
    fn opening_failed(&mut self, error: &str);
    fn stopping(&self) -> bool;
    fn level(&self, sequence: u64, level: f32);
    fn ended(&mut self);
}

pub fn capture(
    path: &Path,
    input_device_id: &str,
    host: &mut impl CaptureHost,
) -> Result<(), String> {
    let opened: Result<OpenInput, String> = (|| {
        host.check_active()?;
        let input = open_input(input_device_id)?;
        host.check_active()?;
        input
            .stream
            .play()
            .map_err(|error| diagnostic_error("ASR_MICROPHONE_UNAVAILABLE", error))?;
        host.ready()?;
        Ok(input)
    })();
    let OpenInput {
        stream,
        samples,
        failed,
        rate,
    } = match opened {
        Ok(input) => input,
        Err(error) => {
            host.opening_failed(&error);
            return Err(error);
        }
    };
    let started = Instant::now();
    let mut last_tick = SystemTime::now();
    let mut last_sequence = 0;
    let outcome = loop {
        thread::sleep(FRAME_INTERVAL);
        let now = SystemTime::now();
        if now.duration_since(last_tick).unwrap_or_default() > Duration::from_secs(2) {
            break Err("ASR_CAPTURE_INTERRUPTED".to_owned());
        }
        last_tick = now;
        if let Err(error) = host.check_active() {
            break Err(error);
        }
        if let Some(error) = failed
            .lock()
            .map_err(|error| diagnostic_error("ASR_CAPTURE_FAILED", error))?
            .take()
        {
            break Err(error);
        }
        let (sequence, level, full, stalled) = {
            let buffer = samples
                .lock()
                .map_err(|error| diagnostic_error("ASR_CAPTURE_FAILED", error))?;
            (
                buffer.sequence,
                buffer.level,
                buffer.mono.len() >= buffer.limit,
                buffer.last_callback.elapsed() > Duration::from_secs(2),
            )
        };
        if stalled {
            break Err("ASR_MICROPHONE_DISCONNECTED".into());
        }
        if sequence != last_sequence {
            last_sequence = sequence;
            host.level(sequence, level);
        }
        if host.stopping() || full || started.elapsed() >= Duration::from_secs(MAX_SECONDS as u64) {
            break Ok(());
        }
    };
    drop(stream);
    host.ended();
    outcome?;
    let mono = std::mem::take(
        &mut samples
            .lock()
            .map_err(|error| diagnostic_error("ASR_CAPTURE_FAILED", error))?
            .mono,
    );
    if mono.is_empty() {
        return Err("ASR_NO_SPEECH".into());
    }
    host.check_active()?;
    let result = write_wav(path, &mono, rate).and_then(|_| host.check_active());
    if result.is_err() {
        let _ = fs::remove_file(path);
    }
    result
}

struct Samples {
    mono: Vec<f32>,
    limit: usize,
    window_size: usize,
    window_count: usize,
    energy: f64,
    level: f32,
    sequence: u64,
    last_callback: Instant,
}
impl Samples {
    fn new(rate: usize) -> Self {
        Self {
            mono: Vec::with_capacity(rate * MAX_SECONDS),
            limit: rate * MAX_SECONDS,
            window_size: (rate / 20).max(1),
            window_count: 0,
            energy: 0.0,
            level: 0.0,
            sequence: 0,
            last_callback: Instant::now(),
        }
    }
    fn push(&mut self, sample: f32) {
        if self.mono.len() >= self.limit {
            return;
        }
        let sample = if sample.is_finite() {
            sample.clamp(-1.0, 1.0)
        } else {
            0.0
        };
        self.mono.push(sample);
        self.energy += f64::from(sample).powi(2);
        self.window_count += 1;
        if self.window_count >= self.window_size {
            let rms = (self.energy / self.window_count as f64).sqrt() as f32;
            // A decibel scale preserves useful feedback for a quiet microphone.
            let normalized = ((20.0 * rms.max(0.000_01).log10() + 60.0) / 60.0).clamp(0.0, 1.0);
            // Follow syllable attacks and pauses without smearing several audio windows together.
            let response = if normalized > self.level { 0.85 } else { 0.65 };
            self.level += response * (normalized - self.level);
            self.sequence += 1;
            self.energy = 0.0;
            self.window_count = 0;
        }
    }
}

fn input_stream<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    samples: Arc<Mutex<Samples>>,
    failed: Arc<Mutex<Option<String>>>,
) -> Result<cpal::Stream, String>
where
    T: cpal::SizedSample,
    f32: cpal::FromSample<T>,
{
    let channels = usize::from(config.channels);
    device
        .build_input_stream(
            config,
            move |data: &[T], _: &cpal::InputCallbackInfo| {
                if let Ok(mut buffer) = samples.lock() {
                    buffer.last_callback = Instant::now();
                    for frame in data.chunks_exact(channels) {
                        let mono = frame
                            .iter()
                            .map(|sample| <f32 as cpal::Sample>::from_sample(*sample))
                            .sum::<f32>()
                            / channels as f32;
                        buffer.push(mono);
                    }
                }
            },
            move |error| {
                if let Ok(mut failure) = failed.lock() {
                    *failure = Some(diagnostic_error("ASR_MICROPHONE_DISCONNECTED", error));
                }
            },
            None,
        )
        .map_err(|source_error| diagnostic_error("ASR_MICROPHONE_UNAVAILABLE", source_error))
}

struct OpenInput {
    stream: cpal::Stream,
    samples: Arc<Mutex<Samples>>,
    failed: Arc<Mutex<Option<String>>>,
    rate: usize,
}

pub fn input_device_snapshot() -> Result<Value, String> {
    let host = cpal::default_host();
    let default_id = host
        .default_input_device()
        .and_then(|device| device.id().ok())
        .map(|id| id.to_string());
    let mut devices = Vec::new();
    for device in host
        .input_devices()
        .map_err(|source_error| diagnostic_error("ASR_INPUT_DEVICES_UNAVAILABLE", source_error))?
    {
        let Ok(id) = device.id() else {
            continue;
        };
        let label = device
            .description()
            .map(|description| description.name().to_owned())
            .unwrap_or_else(|_| "麦克风".into());
        devices.push(json!({"id": id.to_string(), "label": label}));
    }
    Ok(json!({"devices": devices, "defaultDeviceId": default_id}))
}

fn select_input_device(input_device_id: &str) -> Result<cpal::Device, String> {
    let host = cpal::default_host();
    if input_device_id.is_empty() {
        return host
            .default_input_device()
            .ok_or_else(|| "ASR_MICROPHONE_UNAVAILABLE".into());
    }
    let id: cpal::DeviceId = input_device_id
        .parse()
        .map_err(|source_error| diagnostic_error("ASR_INPUT_DEVICE_NOT_FOUND", source_error))?;
    host.input_devices()
        .map_err(|source_error| diagnostic_error("ASR_INPUT_DEVICES_UNAVAILABLE", source_error))?
        .find(|device| device.id().ok().as_ref() == Some(&id))
        .ok_or_else(|| "ASR_INPUT_DEVICE_NOT_FOUND".into())
}

fn open_input(input_device_id: &str) -> Result<OpenInput, String> {
    let device = select_input_device(input_device_id)?;
    let supported = device
        .default_input_config()
        .map_err(|source_error| diagnostic_error("ASR_MICROPHONE_UNAVAILABLE", source_error))?;
    let config: cpal::StreamConfig = supported.clone().into();
    let rate = config.sample_rate as usize;
    if !(8_000..=192_000).contains(&rate) || config.channels == 0 || config.channels > 32 {
        return Err("ASR_AUDIO_FORMAT_UNSUPPORTED".into());
    }
    let samples = Arc::new(Mutex::new(Samples::new(rate)));
    let failed = Arc::new(Mutex::new(None));
    macro_rules! build {
        ($kind:ty) => {
            input_stream::<$kind>(&device, &config, samples.clone(), failed.clone())
        };
    }
    let stream = match supported.sample_format() {
        cpal::SampleFormat::I8 => build!(i8),
        cpal::SampleFormat::I16 => build!(i16),
        cpal::SampleFormat::I32 => build!(i32),
        cpal::SampleFormat::I64 => build!(i64),
        cpal::SampleFormat::U8 => build!(u8),
        cpal::SampleFormat::U16 => build!(u16),
        cpal::SampleFormat::U32 => build!(u32),
        cpal::SampleFormat::U64 => build!(u64),
        cpal::SampleFormat::F32 => build!(f32),
        cpal::SampleFormat::F64 => build!(f64),
        _ => Err("ASR_AUDIO_FORMAT_UNSUPPORTED".into()),
    }?;
    Ok(OpenInput {
        stream,
        samples,
        failed,
        rate,
    })
}

fn write_wav(path: &Path, samples: &[f32], rate: usize) -> Result<(), String> {
    let pcm = resample(samples, rate);
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|source_error| diagnostic_error("ASR_AUDIO_WRITE_FAILED", source_error))?;
    let mut writer = BufWriter::new(file);
    let bytes = (pcm.len() * 2) as u32;
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&(36 + bytes).to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16_u32.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&(OUTPUT_RATE as u32).to_le_bytes());
    header.extend_from_slice(&32_000_u32.to_le_bytes());
    header.extend_from_slice(&2_u16.to_le_bytes());
    header.extend_from_slice(&16_u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&bytes.to_le_bytes());
    writer
        .write_all(&header)
        .map_err(|source_error| diagnostic_error("ASR_AUDIO_WRITE_FAILED", source_error))?;
    for sample in pcm {
        writer
            .write_all(&sample.to_le_bytes())
            .map_err(|source_error| diagnostic_error("ASR_AUDIO_WRITE_FAILED", source_error))?;
    }
    writer
        .flush()
        .map_err(|source_error| diagnostic_error("ASR_AUDIO_WRITE_FAILED", source_error))
}

// Windowed sinc low-pass before decimation avoids aliasing the microphone's
// high-frequency content into the recognizer's 16 kHz input band.
fn resample(samples: &[f32], rate: usize) -> Vec<i16> {
    let count = (samples.len() * OUTPUT_RATE / rate).min(OUTPUT_RATE * MAX_SECONDS);
    let quantize = |value: f64| (value.clamp(-1.0, 1.0) * i16::MAX as f64).round() as i16;
    if rate == OUTPUT_RATE {
        return samples
            .iter()
            .take(count)
            .map(|s| quantize(f64::from(*s)))
            .collect();
    }
    let cutoff = (OUTPUT_RATE as f64 / rate as f64).min(1.0) * 0.94;
    let radius = (24.0 / cutoff).ceil() as isize;
    (0..count)
        .map(|output| {
            let position = output as f64 * rate as f64 / OUTPUT_RATE as f64;
            let center = position.floor() as isize;
            let mut value = 0.0;
            let mut weight_sum = 0.0;
            for input in (center - radius)..=(center + radius) {
                if input < 0 || input >= samples.len() as isize {
                    continue;
                }
                let distance = input as f64 - position;
                let argument = std::f64::consts::PI * distance * cutoff;
                let sinc = if argument.abs() < 1e-8 {
                    1.0
                } else {
                    argument.sin() / argument
                };
                let window = 0.5 + 0.5 * (std::f64::consts::PI * distance / radius as f64).cos();
                let weight = cutoff * sinc * window;
                value += f64::from(samples[input as usize]) * weight;
                weight_sum += weight;
            }
            quantize(if weight_sum.abs() > 1e-8 {
                value / weight_sum
            } else {
                0.0
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "Opens the real system microphone; run explicitly on a device-enabled host"]
    fn real_microphone_captures_pcm_and_releases_device() {
        let inventory = input_device_snapshot().unwrap();
        let selected = inventory["defaultDeviceId"]
            .as_str()
            .expect("No default microphone ID");
        let refreshed = input_device_snapshot().unwrap();
        assert!(refreshed["devices"]
            .as_array()
            .unwrap()
            .iter()
            .any(|device| device["id"].as_str() == Some(selected)));
        let missing = format!(
            "{}:sakura-test-missing-{}",
            cpal::default_host().id(),
            uuid::Uuid::new_v4()
        );
        assert!(
            matches!(select_input_device(&missing), Err(error) if error.split('|').next().unwrap_or("") == "ASR_INPUT_DEVICE_NOT_FOUND")
        );
        let OpenInput {
            stream,
            samples,
            failed,
            rate,
        } = open_input(selected).unwrap();
        stream.play().unwrap();
        thread::sleep(Duration::from_millis(600));
        drop(stream);
        assert!(failed.lock().unwrap().is_none());
        let buffer = samples.lock().unwrap();
        assert!(
            buffer.mono.len() > rate / 10,
            "The OS returned no microphone frames"
        );
        assert!(buffer.sequence > 0);
        let path =
            std::env::temp_dir().join(format!("sakura-asr-device-{}.wav", uuid::Uuid::new_v4()));
        write_wav(&path, &buffer.mono, rate).unwrap();
        let bytes = fs::metadata(&path).unwrap().len();
        fs::remove_file(&path).unwrap();
        eprintln!("Selected microphone capture verified: devices={}, native_rate={rate}, frames={}, rms_summaries={}, wav_bytes={bytes}", inventory["devices"].as_array().unwrap().len(), buffer.mono.len(), buffer.sequence);
        // A subsequent open proves the first stream released the input handle.
        drop(open_input(selected).unwrap());
    }
    #[test]
    fn microphone_level_follows_syllables_and_pauses() {
        let mut samples = Samples::new(16_000);
        for _ in 0..800 {
            samples.push(0.1);
        }
        let attack = samples.level;
        assert!(
            attack > 0.5,
            "one syllable should register without a long fade-in"
        );
        for _ in 0..800 {
            samples.push(0.0);
        }
        assert!(
            samples.level < attack * 0.4,
            "a pause should separate syllables"
        );
        for _ in 0..800 {
            samples.push(0.03);
        }
        assert!(samples.level > 0.35 && samples.level < attack);
    }

    #[test]
    fn microphone_rms_is_real_bounded_and_monotonic() {
        let mut samples = Samples::new(16_000);
        for _ in 0..800 {
            samples.push(0.0);
        }
        assert_eq!(samples.level, 0.0);
        assert_eq!(samples.sequence, 1);
        for _ in 0..800 {
            samples.push(0.1);
        }
        assert!(samples.level > 0.0);
        assert_eq!(samples.sequence, 2);
        for _ in 0..samples.limit + 100 {
            samples.push(0.2);
        }
        assert_eq!(samples.mono.len(), OUTPUT_RATE * MAX_SECONDS);
    }
    #[test]
    fn conversion_writes_actual_pcm16_mono_and_filters_aliasing() {
        let rate = 48_000;
        let tone = |frequency: f64| {
            (0..rate / 10)
                .map(|i| {
                    (2.0 * std::f64::consts::PI * frequency * i as f64 / rate as f64).sin() as f32
                        * 0.5
                })
                .collect::<Vec<_>>()
        };
        let low = resample(&tone(1_000.0), rate);
        let high = resample(&tone(12_000.0), rate);
        assert_eq!(low.len(), 1600);
        let rms = |values: &[i16]| {
            (values[100..values.len() - 100]
                .iter()
                .map(|value| f64::from(*value).powi(2))
                .sum::<f64>()
                / (values.len() - 200) as f64)
                .sqrt()
        };
        assert!(rms(&low) > 10_000.0);
        assert!(rms(&high) < 100.0);
        let path =
            std::env::temp_dir().join(format!("sakura-asr-test-{}.wav", uuid::Uuid::new_v4()));
        write_wav(&path, &tone(1_000.0), rate).unwrap();
        let bytes = fs::read(&path).unwrap();
        fs::remove_file(&path).unwrap();
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(&bytes[8..12], b"WAVE");
        assert_eq!(
            u32::from_le_bytes(bytes[24..28].try_into().unwrap()),
            16_000
        );
        assert_eq!(u16::from_le_bytes(bytes[22..24].try_into().unwrap()), 1);
        assert_eq!(u16::from_le_bytes(bytes[34..36].try_into().unwrap()), 16);
        assert_eq!(bytes.len(), 44 + low.len() * 2);
    }

    #[test]
    fn missing_selected_device_does_not_fall_back_to_default() {
        assert!(
            matches!(select_input_device("not-a-device-id"), Err(error) if error.starts_with("ASR_INPUT_DEVICE_NOT_FOUND"))
        );
    }
}
