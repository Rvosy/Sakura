from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.debug_log import debug_log
from app.sensory.audio_capture import (
    AudioInputSource,
    MicrophoneAudioCapture,
    SystemAudioCapture,
    SystemAudioCaptureError,
)
from app.sensory.audio_inference import (
    AudioInferenceEngine,
    AudioInferenceRequest,
    AudioInferenceTask,
    BUILTIN_AUDIO_RUNTIME,
    OFFICIAL_AUDIO_FRAMEWORK_ID,
    create_default_audio_inference_engine,
    coerce_audio_inference_task,
)
from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
    coerce_sensory_source,
    generate_sensory_id,
    now_iso,
)
from app.sensory.providers import DisabledProvider, SensoryProvider, SensoryProviderUnavailable
from app.sensory.settings import SensorySettings
from app.sensory.store import SensoryObservationStore


@dataclass
class SensoryPipeline:
    settings: SensorySettings
    store: SensoryObservationStore
    providers: dict[str, SensoryProvider]
    audio_capture: SystemAudioCapture | None = None
    system_audio_capture: SystemAudioCapture | None = None
    microphone_audio_capture: MicrophoneAudioCapture | None = None
    audio_inference_engine: AudioInferenceEngine | None = None

    def observe(self, request: SensoryRequest) -> SensoryObservation | None:
        settings = self.settings.normalized()
        normalized_request = request.normalized()
        source_settings = settings.sources[normalized_request.source]
        if not settings.enabled or source_settings.mode == SensoryProviderMode.OFF:
            debug_log(
                "Sensory",
                "感官请求已关闭，跳过",
                {"source": normalized_request.source.value, "request_id": normalized_request.id},
            )
            return None
        provider = self.providers.get(source_settings.provider_id) or DisabledProvider()
        started_at = time.perf_counter()
        debug_log(
            "Sensory",
            "开始调用感官 provider",
            {
                "source": normalized_request.source.value,
                "provider_id": source_settings.provider_id,
                "request_id": normalized_request.id,
                "event_type": normalized_request.event_type,
                "has_media": bool(normalized_request.media_ref)
                or bool(normalized_request.metadata.get("image_urls"))
                or bool(normalized_request.metadata.get("audio_urls")),
            },
        )
        try:
            observation = provider.observe(normalized_request).normalized()
        except SensoryProviderUnavailable as exc:
            debug_log(
                "Sensory",
                "感官 provider 不可用，已关闭本次请求",
                {
                    "source": normalized_request.source.value,
                    "provider_id": source_settings.provider_id,
                    "error": str(exc),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                },
            )
            return None
        recorded = self.store.append(observation)
        debug_log(
            "Sensory",
            "感官观察已保存",
            {
                "sensory_id": recorded.id,
                "source": recorded.source.value,
                "provider_id": recorded.provider_id,
                "confidence": recorded.confidence,
                "sensitive_redacted": recorded.sensitive_redacted,
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return recorded

    def record_observation(self, observation: SensoryObservation) -> SensoryObservation | None:
        settings = self.settings.normalized()
        normalized = observation.normalized()
        if not settings.enabled:
            return None
        recorded = self.store.append(normalized)
        debug_log(
            "Sensory",
            "外部感官观察已镜像",
            {
                "sensory_id": recorded.id,
                "source": recorded.source.value,
                "provider_id": recorded.provider_id,
            },
        )
        return recorded

    def record_visual_observation(self, record: Any) -> SensoryObservation | None:
        settings = self.settings.normalized()
        if not settings.enabled or not settings.sources[SensorySource.VISION].context_enabled:
            return None
        observation = sensory_observation_from_visual_record(record)
        return self.record_observation(observation)

    def observe_system_audio(
        self,
        *,
        source: SensorySource = SensorySource.SPEECH,
        user_text: str = "",
        event_type: str = "",
        text: str = "",
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
        task: AudioInferenceTask | str = AudioInferenceTask.AUTO,
    ) -> SensoryObservation | None:
        return self.observe_audio(
            source=source,
            input_source=AudioInputSource.SYSTEM_AUDIO,
            user_text=user_text,
            event_type=event_type,
            text=text,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channel_count=channel_count,
            exclude_current_process=exclude_current_process,
            task=task,
        )

    def observe_environment_audio(
        self,
        *,
        source: SensorySource = SensorySource.SOUND,
        user_text: str = "",
        event_type: str = "",
        text: str = "",
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        task: AudioInferenceTask | str = AudioInferenceTask.AUTO,
    ) -> SensoryObservation | None:
        return self.observe_audio(
            source=source,
            input_source=AudioInputSource.MICROPHONE,
            user_text=user_text,
            event_type=event_type,
            text=text,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channel_count=channel_count,
            exclude_current_process=False,
            task=task,
        )

    def observe_audio(
        self,
        *,
        source: SensorySource = SensorySource.SPEECH,
        input_source: AudioInputSource | str = AudioInputSource.SYSTEM_AUDIO,
        user_text: str = "",
        event_type: str = "",
        text: str = "",
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
        task: AudioInferenceTask | str = AudioInferenceTask.AUTO,
    ) -> SensoryObservation | None:
        normalized_source = coerce_sensory_source(source)
        if normalized_source not in {SensorySource.SPEECH, SensorySource.SOUND}:
            raise SystemAudioCaptureError("短音频采集只支持 speech 或 sound 感官源。")
        normalized_input_source = _coerce_audio_input_source(input_source)
        settings = self.settings.normalized()
        source_settings = settings.sources[normalized_source]
        if not settings.enabled or source_settings.mode == SensoryProviderMode.OFF:
            debug_log(
                "Sensory",
                "短音频采集源已关闭，跳过",
                {"source": normalized_source.value, "audio_input_source": normalized_input_source.value},
            )
            return None
        provider_config = settings.provider_for_source(normalized_source)
        if provider_config is None:
            debug_log(
                "Sensory",
                "短音频采集 provider 未配置，跳过",
                {
                    "source": normalized_source.value,
                    "audio_input_source": normalized_input_source.value,
                    "provider_id": source_settings.provider_id,
                },
            )
            return None
        provider = self.providers.get(source_settings.provider_id)
        if provider is None:
            debug_log(
                "Sensory",
                "短音频推理 provider 未注册，跳过",
                {
                    "source": normalized_source.value,
                    "audio_input_source": normalized_input_source.value,
                    "provider_id": source_settings.provider_id,
                },
            )
            return None
        capture = self._audio_capture_for(normalized_input_source)
        if capture is None:
            if normalized_input_source == AudioInputSource.MICROPHONE:
                raise SystemAudioCaptureError("麦克风环境音采集接口不可用。")
            raise SystemAudioCaptureError("系统音频采集接口不可用。")

        captured = capture.capture(
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channel_count=channel_count,
            exclude_current_process=exclude_current_process,
        )
        try:
            engine = self.audio_inference_engine or create_default_audio_inference_engine()
            framework_id = str(
                provider_config.extra.get("audio_framework") or OFFICIAL_AUDIO_FRAMEWORK_ID
            ).strip() or OFFICIAL_AUDIO_FRAMEWORK_ID
            runtime_kind = str(
                provider_config.extra.get("audio_runtime") or BUILTIN_AUDIO_RUNTIME
            ).strip() or BUILTIN_AUDIO_RUNTIME
            inference_result = engine.infer(
                AudioInferenceRequest(
                    id=generate_sensory_id(normalized_input_source.value),
                    source=normalized_source,
                    task=coerce_audio_inference_task(task, normalized_source),
                    audio_ref=str(captured.path),
                    user_text=user_text,
                    event_type=event_type or f"{normalized_input_source.value}_capture",
                    text=text,
                    duration_seconds=captured.duration_seconds,
                    sample_rate=captured.sample_rate,
                    channel_count=captured.channel_count,
                    provider_id=source_settings.provider_id,
                    provider_mode=source_settings.mode,
                    framework_id=framework_id,
                    runtime_kind=runtime_kind,
                    metadata={
                        "capture_source": captured.source,
                        "audio_input_source": normalized_input_source.value,
                    },
                ),
                provider,
            )
            recorded = self.store.append(inference_result.to_observation())
            debug_log(
                "Sensory",
                "短音频推理观察已保存",
                {
                    "sensory_id": recorded.id,
                    "source": recorded.source.value,
                    "audio_input_source": normalized_input_source.value,
                    "provider_id": recorded.provider_id,
                    "framework_id": framework_id,
                    "runtime_kind": runtime_kind,
                },
            )
            return recorded
        except SensoryProviderUnavailable as exc:
            debug_log(
                "Sensory",
                "短音频推理 provider 不可用，已关闭本次请求",
                {
                    "source": normalized_source.value,
                    "provider_id": source_settings.provider_id,
                    "error": str(exc),
                },
            )
            return None
        finally:
            captured.cleanup()

    def _audio_capture_for(
        self,
        input_source: AudioInputSource,
    ) -> SystemAudioCapture | MicrophoneAudioCapture | None:
        if input_source == AudioInputSource.MICROPHONE:
            return self.microphone_audio_capture
        return self.system_audio_capture or self.audio_capture


def sensory_observation_from_visual_record(record: Any) -> SensoryObservation:
    return SensoryObservation(
        id=f"sensory_{str(getattr(record, 'id', generate_sensory_id('vis')))}",
        source=SensorySource.VISION,
        created_at=str(getattr(record, "created_at", "") or now_iso()),
        summary=str(getattr(record, "summary", "") or ""),
        details={
            "visual_id": str(getattr(record, "id", "") or ""),
            "screen_name": str(getattr(record, "screen_name", "") or ""),
            "width": int(getattr(record, "width", 0) or 0),
            "height": int(getattr(record, "height", 0) or 0),
            "visible_texts": list(getattr(record, "visible_texts", []) or [])[:12],
            "uncertain_texts": list(getattr(record, "uncertain_texts", []) or [])[:6],
            "notable_elements": list(getattr(record, "notable_elements", []) or [])[:10],
        },
        confidence=float(getattr(record, "confidence", 0.0) or 0.0),
        provider_id="visual_observation_store",
        mode=SensoryProviderMode.API,
        user_text=str(getattr(record, "user_text", "") or ""),
        event_type=str(getattr(record, "source", "") or ""),
        sensitive_redacted=bool(getattr(record, "sensitive_redacted", False)),
        metadata={"mirrored_from": "visual_observation"},
    ).normalized()


def _coerce_audio_input_source(value: AudioInputSource | str) -> AudioInputSource:
    if isinstance(value, AudioInputSource):
        return value
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"microphone", "mic", "environment", "environment_audio", "ambient", "ambient_audio"}:
        return AudioInputSource.MICROPHONE
    return AudioInputSource.SYSTEM_AUDIO
