"""voice_input 录音辅助逻辑测试。"""

from __future__ import annotations

from array import array

from plugins.voice_input.recorder import audio_device_for_sounddevice, pcm16_rms


def test_audio_device_for_sounddevice() -> None:
    assert audio_device_for_sounddevice("") is None
    assert audio_device_for_sounddevice("auto") is None
    assert audio_device_for_sounddevice(" 2 ") == 2
    assert audio_device_for_sounddevice("USB Microphone") == "USB Microphone"


def test_pcm16_rms_empty() -> None:
    assert pcm16_rms(b"") == 0.0


def test_pcm16_rms_for_known_samples() -> None:
    samples = array("h", [1000, -1000, 1000, -1000])

    assert pcm16_rms(samples.tobytes()) == 1000.0
