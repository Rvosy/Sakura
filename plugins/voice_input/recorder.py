from __future__ import annotations

import math
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import VoiceInputConfig


class VoiceInputRecordingError(RuntimeError):
    """录音失败。"""


@dataclass(frozen=True)
class RecordingCallbacks:
    started: Callable[[], None] | None = None
    stopped: Callable[[Path], None] | None = None
    failed: Callable[[str], None] | None = None
    level: Callable[[float], None] | None = None


class RecordingWorker:
    """后台录音线程。

    录音音频写入插件私有 data/temp_audio 目录，不触碰宿主 UI 控件。
    """

    def __init__(
        self,
        *,
        config: VoiceInputConfig,
        output_path: Path,
        callbacks: RecordingCallbacks | None = None,
    ) -> None:
        self.config = config
        self.output_path = output_path
        self.callbacks = callbacks or RecordingCallbacks()
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="voice-input-recorder",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            path = self._record_to_wav()
        except Exception as exc:  # noqa: BLE001
            _call(self.callbacks.failed, str(exc))
            return
        if not self._cancel_event.is_set():
            _call(self.callbacks.stopped, path)

    def _record_to_wav(self) -> Path:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceInputRecordingError("缺少 sounddevice 依赖，无法访问麦克风。") from exc

        sample_rate = int(self.config.sample_rate)
        channels = 1
        block_frames = max(400, int(sample_rate * 0.1))
        device = audio_device_for_sounddevice(self.config.audio_device)
        max_seconds = max(1.0, float(self.config.max_record_seconds))
        silence_seconds = max(0.3, float(self.config.silence_timeout_ms) / 1000.0)
        threshold = max(1, int(self.config.vad_threshold))
        frames: list[bytes] = []
        started_at = time.monotonic()
        last_voice_at = started_at
        has_voice = False

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        _call(self.callbacks.started)
        try:
            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="int16",
                blocksize=block_frames,
                device=device,
            ) as stream:
                while not self._stop_event.is_set() and not self._cancel_event.is_set():
                    chunk, overflowed = stream.read(block_frames)
                    data = bytes(chunk)
                    if overflowed:
                        # 继续录制，后续 ASR 通常能容忍短暂 overflow。
                        pass
                    frames.append(data)
                    rms = pcm16_rms(data)
                    _call(self.callbacks.level, rms)
                    now = time.monotonic()
                    if rms >= threshold:
                        has_voice = True
                        last_voice_at = now
                    if now - started_at >= max_seconds:
                        break
                    if self.config.vad_enabled and (has_voice or now - started_at > 0.5):
                        if now - last_voice_at >= silence_seconds:
                            break
        except Exception as exc:  # noqa: BLE001
            raise VoiceInputRecordingError(f"麦克风不可用或录音失败：{exc}") from exc

        if self._cancel_event.is_set():
            raise VoiceInputRecordingError("录音已取消。")
        if not frames:
            raise VoiceInputRecordingError("未录到音频。")
        with wave.open(str(self.output_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"".join(frames))
        return self.output_path


def audio_device_for_sounddevice(value: str) -> int | str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"auto", "default", "默认"}:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def pcm16_rms(data: bytes) -> float:
    if not data:
        return 0.0
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples)
    return math.sqrt(total / len(samples))


def _call(callback: Callable[..., None] | None, *args: object) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        return
