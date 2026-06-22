from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
    coerce_provider_mode,
    coerce_sensory_source,
    generate_sensory_id,
    now_iso,
)
from app.sensory.providers import SensoryProvider
from app.storage.paths import StoragePaths


OFFICIAL_AUDIO_FRAMEWORK_ID = "sakura_official_short"
OFFICIAL_AUDIO_FRAMEWORK_LABEL = "Sakura 官方短音频推理框架"
BUILTIN_AUDIO_RUNTIME = "builtin"
SIDECAR_AUDIO_RUNTIME = "sidecar"


class AudioInferenceTask(str, Enum):
    AUTO = "auto"
    SPEECH = "speech"
    SOUND = "sound"


@dataclass(frozen=True)
class AudioInferenceFrameworkSpec:
    framework_id: str
    label: str
    runtime_kind: str = BUILTIN_AUDIO_RUNTIME
    package_optional: bool = True
    package_dir: str = ""
    health_path: str = "/health"
    infer_path: str = "/infer"

    def normalized(self) -> "AudioInferenceFrameworkSpec":
        runtime_kind = _normalize_runtime_kind(self.runtime_kind)
        framework_id = (
            str(self.framework_id or OFFICIAL_AUDIO_FRAMEWORK_ID).strip()
            or OFFICIAL_AUDIO_FRAMEWORK_ID
        )
        label = str(self.label or OFFICIAL_AUDIO_FRAMEWORK_LABEL).strip() or OFFICIAL_AUDIO_FRAMEWORK_LABEL
        return AudioInferenceFrameworkSpec(
            framework_id=framework_id,
            label=label,
            runtime_kind=runtime_kind,
            package_optional=bool(self.package_optional),
            package_dir=str(self.package_dir or "").strip(),
            health_path=str(self.health_path or "/health").strip(),
            infer_path=str(self.infer_path or "/infer").strip(),
        )


@dataclass(frozen=True)
class AudioInferenceRequest:
    id: str
    source: SensorySource
    task: AudioInferenceTask = AudioInferenceTask.AUTO
    audio_ref: str = ""
    user_text: str = ""
    event_type: str = ""
    text: str = ""
    duration_seconds: float = 3.0
    sample_rate: int = 16000
    channel_count: int = 1
    provider_id: str = ""
    provider_mode: SensoryProviderMode = SensoryProviderMode.OFF
    framework_id: str = OFFICIAL_AUDIO_FRAMEWORK_ID
    runtime_kind: str = BUILTIN_AUDIO_RUNTIME
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def normalized(self) -> "AudioInferenceRequest":
        source = coerce_sensory_source(self.source)
        return AudioInferenceRequest(
            id=str(self.id or generate_sensory_id("audio_inf")).strip(),
            source=source,
            task=coerce_audio_inference_task(self.task, source),
            audio_ref=str(self.audio_ref or "").strip(),
            user_text=str(self.user_text or ""),
            event_type=str(self.event_type or ""),
            text=str(self.text or ""),
            duration_seconds=_clamp_float(self.duration_seconds, 0.5, 10.0, 3.0),
            sample_rate=_clamp_int(self.sample_rate, 8000, 96000, 16000),
            channel_count=_clamp_int(self.channel_count, 1, 8, 1),
            provider_id=str(self.provider_id or "").strip(),
            provider_mode=coerce_provider_mode(self.provider_mode),
            framework_id=(
                str(self.framework_id or OFFICIAL_AUDIO_FRAMEWORK_ID).strip()
                or OFFICIAL_AUDIO_FRAMEWORK_ID
            ),
            runtime_kind=_normalize_runtime_kind(self.runtime_kind),
            metadata=_mapping(self.metadata),
            created_at=str(self.created_at or now_iso()),
        )


@dataclass(frozen=True)
class AudioInferenceResult:
    id: str
    request_id: str
    source: SensorySource
    task: AudioInferenceTask
    summary: str
    transcript: str = ""
    sound_events: tuple[str, ...] = ()
    confidence: float = 0.0
    provider_id: str = ""
    provider_mode: SensoryProviderMode = SensoryProviderMode.OFF
    framework_id: str = OFFICIAL_AUDIO_FRAMEWORK_ID
    runtime_kind: str = BUILTIN_AUDIO_RUNTIME
    duration_seconds: float = 0.0
    sample_rate: int = 0
    channel_count: int = 0
    observation: SensoryObservation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_observation(self) -> SensoryObservation:
        observation = self.observation.normalized() if self.observation is not None else None
        audio_metadata = {
            "framework_id": self.framework_id,
            "runtime_kind": self.runtime_kind,
            "task": self.task.value,
            "request_id": self.request_id,
        }
        if self.duration_seconds > 0:
            audio_metadata["duration_seconds"] = self.duration_seconds
        if self.sample_rate > 0:
            audio_metadata["sample_rate"] = self.sample_rate
        if self.channel_count > 0:
            audio_metadata["channel_count"] = self.channel_count
        if observation is not None:
            details = dict(observation.details)
            if self.transcript and "transcript" not in details:
                details["transcript"] = self.transcript
            if self.sound_events and "sound_events" not in details:
                details["sound_events"] = list(self.sound_events)
            return SensoryObservation(
                id=observation.id,
                source=observation.source,
                created_at=observation.created_at,
                summary=observation.summary or self.summary,
                details=details,
                confidence=observation.confidence,
                provider_id=observation.provider_id or self.provider_id,
                mode=observation.mode,
                user_text=observation.user_text,
                event_type=observation.event_type,
                sensitive_redacted=observation.sensitive_redacted,
                metadata={
                    **observation.metadata,
                    "audio_inference": audio_metadata,
                },
            ).normalized()
        return SensoryObservation(
            id=generate_sensory_id("audio_obs"),
            source=self.source,
            created_at=now_iso(),
            summary=self.summary,
            details={
                **({"transcript": self.transcript} if self.transcript else {}),
                **({"sound_events": list(self.sound_events)} if self.sound_events else {}),
            },
            confidence=self.confidence,
            provider_id=self.provider_id,
            mode=self.provider_mode,
            metadata={"audio_inference": audio_metadata, **self.metadata},
        ).normalized()


class AudioInferenceEngine(Protocol):
    framework: AudioInferenceFrameworkSpec

    def infer(
        self,
        request: AudioInferenceRequest,
        provider: SensoryProvider,
    ) -> AudioInferenceResult:
        """Infer structured short-window audio evidence with ``provider``."""


class ShortAudioInferenceEngine:
    """Built-in short-window engine.

    It owns the stable request/result contract and delegates the actual model
    call to the configured sensory provider. A future optional sidecar can
    implement the same contract without changing the tool or storage layers.
    """

    def __init__(self, framework: AudioInferenceFrameworkSpec | None = None) -> None:
        self.framework = (framework or official_audio_inference_framework()).normalized()

    def infer(
        self,
        request: AudioInferenceRequest,
        provider: SensoryProvider,
    ) -> AudioInferenceResult:
        normalized = request.normalized()
        sensory_request = SensoryRequest(
            id=normalized.id,
            source=normalized.source,
            user_text=normalized.user_text,
            event_type=normalized.event_type or "audio_inference",
            text=normalized.text,
            media_ref=normalized.audio_ref,
            metadata={
                **normalized.metadata,
                "duration_seconds": normalized.duration_seconds,
                "sample_rate": normalized.sample_rate,
                "channel_count": normalized.channel_count,
                "audio_inference": {
                    "framework_id": normalized.framework_id,
                    "runtime_kind": normalized.runtime_kind,
                    "task": normalized.task.value,
                    "duration_seconds": normalized.duration_seconds,
                    "sample_rate": normalized.sample_rate,
                    "channel_count": normalized.channel_count,
                },
            },
        )
        observation = provider.observe(sensory_request).normalized()
        return audio_inference_result_from_observation(normalized, observation)


def official_audio_inference_framework(base_dir: Path | None = None) -> AudioInferenceFrameworkSpec:
    package_dir = ""
    if base_dir is not None:
        package_dir = str(StoragePaths(base_dir).audio_inference_framework_for(OFFICIAL_AUDIO_FRAMEWORK_ID))
    return AudioInferenceFrameworkSpec(
        framework_id=OFFICIAL_AUDIO_FRAMEWORK_ID,
        label=OFFICIAL_AUDIO_FRAMEWORK_LABEL,
        runtime_kind=BUILTIN_AUDIO_RUNTIME,
        package_optional=True,
        package_dir=package_dir,
    )


def create_default_audio_inference_engine(base_dir: Path | None = None) -> ShortAudioInferenceEngine:
    return ShortAudioInferenceEngine(official_audio_inference_framework(base_dir))


def coerce_audio_inference_task(
    value: Any,
    source: SensorySource | str = SensorySource.SPEECH,
) -> AudioInferenceTask:
    if isinstance(value, AudioInferenceTask):
        task = value
    else:
        text = str(value or "").strip().lower()
        task = next(
            (candidate for candidate in AudioInferenceTask if candidate.value == text),
            AudioInferenceTask.AUTO,
        )
    if task != AudioInferenceTask.AUTO:
        return task
    normalized_source = coerce_sensory_source(source)
    if normalized_source == SensorySource.SOUND:
        return AudioInferenceTask.SOUND
    return AudioInferenceTask.SPEECH


def audio_inference_result_from_observation(
    request: AudioInferenceRequest,
    observation: SensoryObservation,
) -> AudioInferenceResult:
    normalized_request = request.normalized()
    normalized_observation = observation.normalized()
    return AudioInferenceResult(
        id=generate_sensory_id("audio_result"),
        request_id=normalized_request.id,
        source=normalized_request.source,
        task=normalized_request.task,
        summary=normalized_observation.summary,
        transcript=_extract_transcript(normalized_request, normalized_observation),
        sound_events=_extract_sound_events(normalized_request, normalized_observation),
        confidence=normalized_observation.confidence,
        provider_id=normalized_observation.provider_id or normalized_request.provider_id,
        provider_mode=normalized_observation.mode,
        framework_id=normalized_request.framework_id,
        runtime_kind=normalized_request.runtime_kind,
        duration_seconds=normalized_request.duration_seconds,
        sample_rate=normalized_request.sample_rate,
        channel_count=normalized_request.channel_count,
        observation=normalized_observation,
    )


def _extract_transcript(
    request: AudioInferenceRequest,
    observation: SensoryObservation,
) -> str:
    if request.task != AudioInferenceTask.SPEECH:
        return ""
    details = observation.details
    for key in ("transcript", "text", "utterance", "asr_text"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return observation.summary.strip()


def _extract_sound_events(
    request: AudioInferenceRequest,
    observation: SensoryObservation,
) -> tuple[str, ...]:
    if request.task != AudioInferenceTask.SOUND:
        return ()
    details = observation.details
    for key in ("sound_events", "events", "labels"):
        value = details.get(key)
        if isinstance(value, list):
            events = tuple(str(item).strip() for item in value if str(item).strip())
            if events:
                return events[:8]
        if isinstance(value, str) and value.strip():
            return (value.strip(),)
    return (observation.summary.strip(),) if observation.summary.strip() else ()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_runtime_kind(value: Any) -> str:
    runtime_kind = str(value or BUILTIN_AUDIO_RUNTIME).strip().lower()
    if runtime_kind not in {BUILTIN_AUDIO_RUNTIME, SIDECAR_AUDIO_RUNTIME, "custom_http"}:
        runtime_kind = BUILTIN_AUDIO_RUNTIME
    return runtime_kind


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
