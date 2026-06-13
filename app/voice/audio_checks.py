"""app/voice/audio_checks.py — 音频文件检查与 wav 元信息。

从 tts.py 拆出的纯函数层：生成后入队前、播放前的统一检查关卡，
以及播放完成兜底依赖的时长解析。不依赖 Qt。
"""

from __future__ import annotations

import wave
from pathlib import Path


def _verify_generated_audio(path: Path) -> str | None:
    """音频文件统一检查关卡：生成后入队前、播放前各过一次。

    返回 None 表示通过；否则返回错误码，日志可借此区分：
    audio_file_missing / audio_file_empty / audio_file_unreadable / audio_format_invalid
    """
    path = Path(path)
    if not path.is_file():
        return "audio_file_missing"
    try:
        size = path.stat().st_size
    except OSError:
        return "audio_file_unreadable"
    if size <= 0:
        return "audio_file_empty"
    try:
        with path.open("rb") as handle:
            handle.read(16)
    except OSError:
        return "audio_file_unreadable"
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
    # wave 对截断文件抛 EOFError，不属于 wave.Error，需单独捕获
    except (OSError, wave.Error, EOFError):
        return "audio_format_invalid"
    if channels not in (1, 2) or sample_width <= 0 or frame_rate <= 0:
        return "audio_format_invalid"
    return None


def _wav_duration_ms(path: Path) -> int | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (OSError, wave.Error, EOFError):
        return None
    if frame_rate <= 0 or frame_count < 0:
        return None
    return max(1, int(frame_count * 1000 / frame_rate))


def _is_valid_wav_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with wave.open(str(path), "rb") as wav_file:
            wav_file.getnchannels()
            wav_file.getframerate()
            wav_file.getnframes()
    except (OSError, wave.Error, EOFError):
        return False
    return True
