from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SensorySource(str, Enum):
    VISION = "vision"
    SPEECH = "speech"
    SOUND = "sound"


class SensoryProviderMode(str, Enum):
    OFF = "off"
    API = "api"
    LOCAL = "local"


@dataclass(frozen=True)
class SensoryRequest:
    """A source-agnostic request sent to one sensory provider.

    ``media_ref`` is intentionally a reference only. The framework does not
    persist raw screenshots, audio, or other media bytes by default.
    """

    id: str
    source: SensorySource
    user_text: str = ""
    event_type: str = ""
    text: str = ""
    media_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def normalized(self) -> "SensoryRequest":
        return SensoryRequest(
            id=str(self.id or generate_sensory_id("req")).strip(),
            source=coerce_sensory_source(self.source),
            user_text=str(self.user_text or ""),
            event_type=str(self.event_type or ""),
            text=str(self.text or ""),
            media_ref=str(self.media_ref or ""),
            metadata=_mapping(self.metadata),
            created_at=str(self.created_at or now_iso()),
        )


@dataclass(frozen=True)
class SensoryObservation:
    """Structured text observation produced by sensory middleware."""

    id: str
    source: SensorySource
    created_at: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    provider_id: str = ""
    mode: SensoryProviderMode = SensoryProviderMode.OFF
    user_text: str = ""
    event_type: str = ""
    sensitive_redacted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "SensoryObservation":
        return SensoryObservation(
            id=str(self.id or generate_sensory_id("obs")).strip(),
            source=coerce_sensory_source(self.source),
            created_at=str(self.created_at or now_iso()),
            summary=str(self.summary or "").strip(),
            details=_mapping(self.details),
            confidence=clamp_confidence(self.confidence),
            provider_id=str(self.provider_id or "").strip(),
            mode=coerce_provider_mode(self.mode),
            user_text=str(self.user_text or ""),
            event_type=str(self.event_type or ""),
            sensitive_redacted=bool(self.sensitive_redacted),
            metadata=_mapping(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        normalized = self.normalized()
        data = asdict(normalized)
        data["source"] = normalized.source.value
        data["mode"] = normalized.mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SensoryObservation":
        return cls(
            id=str(data.get("id") or ""),
            source=coerce_sensory_source(data.get("source")),
            created_at=str(data.get("created_at") or ""),
            summary=str(data.get("summary") or ""),
            details=_mapping(data.get("details")),
            confidence=clamp_confidence(data.get("confidence")),
            provider_id=str(data.get("provider_id") or ""),
            mode=coerce_provider_mode(data.get("mode")),
            user_text=str(data.get("user_text") or ""),
            event_type=str(data.get("event_type") or ""),
            sensitive_redacted=bool(data.get("sensitive_redacted", False)),
            metadata=_mapping(data.get("metadata")),
        ).normalized()


def coerce_sensory_source(value: Any) -> SensorySource:
    if isinstance(value, SensorySource):
        return value
    text = str(value or "").strip().lower()
    for source in SensorySource:
        if text == source.value:
            return source
    return SensorySource.VISION


def coerce_provider_mode(value: Any) -> SensoryProviderMode:
    if isinstance(value, SensoryProviderMode):
        return value
    text = str(value or "").strip().lower()
    for mode in SensoryProviderMode:
        if text == mode.value:
            return mode
    return SensoryProviderMode.OFF


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def generate_sensory_id(prefix: str = "sensory") -> str:
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch == "_") or "sensory"
    return f"{safe_prefix}_{uuid.uuid4().hex[:10]}"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
