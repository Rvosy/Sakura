"""voice_input ASR 后端测试。"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from plugins.voice_input.asr import VoiceInputASRError, transcribe_audio
from plugins.voice_input.config import VoiceInputConfig


def test_transcribe_audio_requires_existing_audio(tmp_path: Path) -> None:
    with pytest.raises(VoiceInputASRError, match="临时音频不存在"):
        transcribe_audio(tmp_path / "missing.wav", VoiceInputConfig(), tmp_path)


def test_transcribe_audio_reports_missing_model(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    _write_empty_wav(audio_path)

    with pytest.raises(VoiceInputASRError, match="缺少 ASR 模型"):
        transcribe_audio(audio_path, VoiceInputConfig(model_name="tiny"), tmp_path)


def _write_empty_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 100)
