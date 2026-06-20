from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.debug_log import debug_log
from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
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
                or bool(normalized_request.metadata.get("image_urls")),
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
