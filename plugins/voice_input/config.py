from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_MODEL_NAME = "tiny"
DEFAULT_LANGUAGE = "auto"
DEFAULT_COMPUTE_DEVICE = "auto"
DEFAULT_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class VoiceInputConfig:
    model_name: str = DEFAULT_MODEL_NAME
    language: str = DEFAULT_LANGUAGE
    audio_device: str = ""
    compute_device: str = DEFAULT_COMPUTE_DEVICE
    vad_enabled: bool = True
    max_record_seconds: int = 30
    silence_timeout_ms: int = 1500
    vad_threshold: int = 500
    sample_rate: int = DEFAULT_SAMPLE_RATE

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "VoiceInputConfig":
        data = raw or {}
        return cls(
            model_name=_string_value(data.get("model_name"), DEFAULT_MODEL_NAME),
            language=_string_value(data.get("language"), DEFAULT_LANGUAGE),
            audio_device=_string_value(data.get("audio_device"), ""),
            compute_device=_string_value(data.get("compute_device"), DEFAULT_COMPUTE_DEVICE),
            vad_enabled=_bool_value(data.get("vad_enabled"), True),
            max_record_seconds=_bounded_int(data.get("max_record_seconds"), 30, 3, 300),
            silence_timeout_ms=_bounded_int(data.get("silence_timeout_ms"), 1500, 300, 10000),
            vad_threshold=_bounded_int(data.get("vad_threshold"), 500, 50, 10000),
            sample_rate=_bounded_int(data.get("sample_rate"), DEFAULT_SAMPLE_RATE, 8000, 48000),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "language": self.language,
            "audio_device": self.audio_device,
            "compute_device": self.compute_device,
            "vad_enabled": self.vad_enabled,
            "max_record_seconds": self.max_record_seconds,
            "silence_timeout_ms": self.silence_timeout_ms,
            "vad_threshold": self.vad_threshold,
            "sample_rate": self.sample_rate,
        }

    @property
    def asr_language(self) -> str | None:
        language = self.language.strip().lower()
        return None if language in {"", "auto", "自动"} else language

    @property
    def asr_device(self) -> str:
        device = self.compute_device.strip().lower()
        return device or DEFAULT_COMPUTE_DEVICE


def load_voice_input_config(context: Any) -> VoiceInputConfig:
    return VoiceInputConfig.from_mapping(context.get_config())


def save_voice_input_config(context: Any, config: VoiceInputConfig) -> None:
    context.save_config(config.to_mapping())


def _string_value(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
