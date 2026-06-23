from __future__ import annotations

import base64
import io
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from app.sensory.audio_models import sensory_audio_model_download_hint
from app.sensory.llama_cpp_runtime import (
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    discover_llama_server_binary,
)
from app.sensory.models import SensoryObservation, SensoryRequest, SensorySource, generate_sensory_id
from app.sensory.providers import SensoryProviderUnavailable, provider_from_config
from app.sensory.settings import SensoryProviderConfig


@dataclass(frozen=True)
class SensoryAudioSmokePlan:
    ok: bool
    source: SensorySource
    provider_id: str
    backend: str
    managed_runtime: bool
    endpoint: str
    model: str
    binary_path: str = ""
    model_download_hint: str = ""
    message: str = ""

    def to_mapping(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "source": self.source.value,
            "provider_id": self.provider_id,
            "backend": self.backend,
            "managed_runtime": self.managed_runtime,
            "endpoint": self.endpoint,
            "model": self.model,
            "binary_path": self.binary_path,
            "model_download_hint": self.model_download_hint,
            "message": self.message,
        }


@dataclass(frozen=True)
class SensoryAudioSmokeResult:
    ok: bool
    message: str
    observation: SensoryObservation | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "message": self.message,
            "observation": self.observation.to_dict() if self.observation is not None else None,
        }


def build_sensory_audio_smoke_plan(
    config: SensoryProviderConfig,
    *,
    base_dir: Path | None = None,
    source: SensorySource = SensorySource.SPEECH,
) -> SensoryAudioSmokePlan:
    normalized = config.normalized()
    backend = str(
        normalized.extra.get("backend") or normalized.extra.get("provider") or ""
    ).strip().lower()
    managed_runtime = (
        normalized.mode.value == "local"
        and backend in {"llama", "llama.cpp", "llama_cpp", "llamacpp"}
        and str(normalized.extra.get("managed_runtime") or "").strip().lower()
        == LLAMA_CPP_MANAGED_RUNTIME_MARKER
    )
    binary_path = ""
    if managed_runtime and base_dir is not None:
        binary_path = discover_llama_server_binary(base_dir)
    model_hint = sensory_audio_model_download_hint(normalized.model)
    issues: list[str] = []
    if source not in {SensorySource.SPEECH, SensorySource.SOUND}:
        issues.append("audio smoke test only supports speech or sound")
    if not normalized.endpoint:
        issues.append("provider endpoint is empty")
    if not normalized.model:
        issues.append("provider model is empty")
    if managed_runtime and not binary_path:
        issues.append("managed llama.cpp runtime binary is not installed")
    ok = not issues
    if ok:
        message = "音频推理 smoke test 已准备好。"
        if model_hint:
            message = f"{message} 首次测试预计下载 {model_hint}。"
    else:
        message = "；".join(issues)
    return SensoryAudioSmokePlan(
        ok=ok,
        source=source,
        provider_id=normalized.provider_id,
        backend=backend,
        managed_runtime=managed_runtime,
        endpoint=normalized.endpoint,
        model=normalized.model,
        binary_path=binary_path,
        model_download_hint=model_hint,
        message=message,
    )


def run_sensory_audio_smoke_test(
    config: SensoryProviderConfig,
    *,
    base_dir: Path | None = None,
    source: SensorySource = SensorySource.SPEECH,
) -> SensoryAudioSmokeResult:
    normalized_config = config.normalized()
    try:
        provider = provider_from_config(normalized_config, base_dir=base_dir)
        observation = provider.observe(
            SensoryRequest(
                id=generate_sensory_id("audio_smoke"),
                source=source,
                user_text="设置页测试增强感知音频推理后端",
                event_type="audio_smoke_test",
                text=_audio_smoke_prompt(source),
                media_ref=build_sensory_audio_smoke_data_url(),
                metadata={
                    "test": True,
                    "duration_seconds": 0.35,
                    "sample_rate": 16000,
                    "channel_count": 1,
                },
            )
        ).normalized()
    except SensoryProviderUnavailable as exc:
        return SensoryAudioSmokeResult(False, str(exc))
    except Exception as exc:  # UI/diagnostic boundary: return readable failure.
        return SensoryAudioSmokeResult(False, str(exc))
    return SensoryAudioSmokeResult(
        True,
        f"音频推理 smoke test 成功：{observation.summary[:120]}",
        observation,
    )


def build_sensory_audio_smoke_data_url() -> str:
    sample_rate = 16000
    duration_seconds = 0.35
    frequency = 880.0
    amplitude = 0.28
    frame_count = int(sample_rate * duration_seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for index in range(frame_count):
            value = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            wav.writeframesraw(struct.pack("<h", value))
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def _audio_smoke_prompt(source: SensorySource) -> str:
    if source == SensorySource.SOUND:
        return "请识别这段短测试音频中的声音类型，并返回结构化 JSON。"
    return "请判断这段短测试音频中是否有人声，并返回结构化 JSON。"
