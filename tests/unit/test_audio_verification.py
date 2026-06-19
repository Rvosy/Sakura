"""tests/unit/test_audio_verification.py — 音频检查关卡与播放兜底测试。

覆盖：
- _verify_generated_audio 对缺失/空/不可解析/参数异常/合法 wav 的判定
- 播放完成兜底：时长解析失败时按上限兜底（修复原"跳过兜底导致挂起"缺陷）、
  超长音频截断到上限
- _play_next 播放前校验失败时跳过条目并继续队列
"""

from __future__ import annotations

import types
import uuid
import wave
from pathlib import Path

import pytest

import app.voice.tts as tts_module
from app.voice.audio_checks import _verify_generated_audio
import app.voice.tts_playback as tts_playback
from app.voice.tts import (
    _AUDIO_FINISH_FALLBACK_GRACE_MS,
    _AUDIO_FINISH_FALLBACK_MAX_MS,
)
from app.voice.tts_playback import TTSPlaybackEndpoint


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_audio_verification"


def _make_dir(name: str) -> Path:
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _write_wav(path: Path, *, channels: int = 1, sample_width: int = 2, frames: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00" * frames * sample_width * channels)


class TestVerifyGeneratedAudio:
    def test_valid_wav_passes(self) -> None:
        root = _make_dir("valid")
        wav = root / "ok.wav"
        _write_wav(wav)
        assert _verify_generated_audio(wav) is None

    def test_missing_file(self) -> None:
        root = _make_dir("missing")
        assert _verify_generated_audio(root / "nope.wav") == "audio_file_missing"

    def test_empty_file(self) -> None:
        root = _make_dir("empty")
        wav = root / "empty.wav"
        wav.write_bytes(b"")
        assert _verify_generated_audio(wav) == "audio_file_empty"

    def test_not_a_wav(self) -> None:
        root = _make_dir("garbage")
        wav = root / "garbage.wav"
        wav.write_bytes(b"this is not a riff file at all")
        assert _verify_generated_audio(wav) == "audio_format_invalid"

    def test_stereo_wav_passes(self) -> None:
        root = _make_dir("stereo")
        wav = root / "stereo.wav"
        _write_wav(wav, channels=2)
        assert _verify_generated_audio(wav) is None


class TestFinishFallback:
    def _provider_stub(self) -> types.SimpleNamespace:
        stub = types.SimpleNamespace()
        stub._playback_finish_token = 1
        return stub

    def test_unparseable_duration_still_schedules_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """核心缺陷修复断言：wav 时长读不出也必须挂兜底定时器。"""
        root = _make_dir("fallback_broken")
        broken = root / "broken.wav"
        broken.write_bytes(b"not a wav")
        scheduled: list[int] = []
        monkeypatch.setattr(
            tts_playback.QTimer,
            "singleShot",
            staticmethod(lambda delay, _fn: scheduled.append(int(delay))),
        )
        stub = self._provider_stub()
        TTSPlaybackEndpoint._schedule_current_audio_finish_fallback(stub, broken, 1)
        assert scheduled, "解析失败时未安排兜底，会导致播放流程可能永久挂起"
        assert scheduled[0] == _AUDIO_FINISH_FALLBACK_MAX_MS

    def test_normal_duration_uses_duration_plus_grace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_dir("fallback_normal")
        wav = root / "one_second.wav"
        _write_wav(wav, frames=16000)  # 1 秒
        scheduled: list[int] = []
        monkeypatch.setattr(
            tts_playback.QTimer,
            "singleShot",
            staticmethod(lambda delay, _fn: scheduled.append(int(delay))),
        )
        TTSPlaybackEndpoint._schedule_current_audio_finish_fallback(self._provider_stub(), wav, 1)
        assert scheduled[0] == 1000 + _AUDIO_FINISH_FALLBACK_GRACE_MS

    def test_oversized_duration_clamped_to_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_dir("fallback_long")
        wav = root / "long.wav"
        _write_wav(wav, frames=16000 * 120)  # 120 秒
        scheduled: list[int] = []
        monkeypatch.setattr(
            tts_playback.QTimer,
            "singleShot",
            staticmethod(lambda delay, _fn: scheduled.append(int(delay))),
        )
        TTSPlaybackEndpoint._schedule_current_audio_finish_fallback(self._provider_stub(), wav, 1)
        assert scheduled[0] == _AUDIO_FINISH_FALLBACK_MAX_MS


class TestPlayNextSkipsInvalidAudio:
    def test_invalid_entry_skipped_queue_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_dir("skip")
        bad = root / "bad.wav"
        bad.write_bytes(b"junk")
        good = root / "good.wav"
        _write_wav(good)

        stub = types.SimpleNamespace()
        stub._current_audio = None
        stub._current_text = ""
        stub._current_started = None
        stub._current_finished = None
        stub._current_started_emitted = False
        stub._playback_finish_token = 0
        stub._playback_backend = "media_player"
        stub._pending_audio = [
            (bad, None, None, None, "坏的"),
            (good, None, None, None, "好的"),
        ]
        finished: list[str] = []
        played: list[Path] = []

        def fake_finish(reason: str) -> None:
            finished.append(reason)
            stub._current_audio = None

        stub._finish_current_audio = fake_finish
        stub._play_next_with_media_player = lambda: played.append(stub._current_audio)
        stub._play_next_with_sink = lambda: played.append(stub._current_audio)
        stub._play_next = lambda: TTSPlaybackEndpoint._play_next(stub)

        TTSPlaybackEndpoint._play_next(stub)

        # 坏条目被跳过（invalid_audio），好条目正常进入播放
        assert finished == ["invalid_audio"]
        assert played == [good]
