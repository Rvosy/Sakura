from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.sensory.models import SensoryObservation, SensorySource, coerce_sensory_source
from app.sensory.settings import SENSORY_DEFAULT_RETENTION_DAYS, SENSORY_DEFAULT_RETENTION_LIMIT
from app.storage.atomic import atomic_write_text


_SENSITIVE_PATTERNS = (
    re.compile(r"\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=：]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(密码|口令|密钥|令牌|银行卡|信用卡|身份证)\s*[:：]\s*\S+"),
)

_RAW_MEDIA_KEYS = {
    "audio",
    "audio_bytes",
    "audio_url",
    "data_url",
    "frames",
    "image",
    "image_bytes",
    "image_url",
    "media",
    "media_bytes",
    "raw_audio",
    "raw_image",
    "raw_media",
    "screenshot",
    "waveform",
}


class SensoryObservationStore:
    """JSONL short-term storage for structured sensory observations."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = SENSORY_DEFAULT_RETENTION_DAYS,
        retention_limit: int = SENSORY_DEFAULT_RETENTION_LIMIT,
    ) -> None:
        self.path = path
        self.retention_days = max(1, int(retention_days))
        self.retention_limit = max(1, int(retention_limit))

    def append(self, observation: SensoryObservation) -> SensoryObservation:
        normalized = self._redact_observation(observation)
        records = [item.to_dict() for item in self._load_observations()]
        records.append(normalized.to_dict())
        records = self._prune(records)
        text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
        atomic_write_text(self.path, text, encoding="utf-8")
        return normalized

    def recent(
        self,
        limit: int = 8,
        *,
        sources: set[SensorySource] | None = None,
        since_minutes: int | None = None,
    ) -> list[SensoryObservation]:
        threshold = None
        if since_minutes is not None:
            threshold = datetime.now().astimezone() - timedelta(minutes=since_minutes)
        normalized_sources = {coerce_sensory_source(source) for source in sources} if sources else None

        records: list[SensoryObservation] = []
        for observation in reversed(self._load_observations()):
            if normalized_sources is not None and observation.source not in normalized_sources:
                continue
            if threshold is not None:
                created_at = _parse_iso(observation.created_at)
                if created_at is None or created_at < threshold:
                    continue
            records.append(observation)
            if len(records) >= limit:
                break
        return records

    def search(self, keyword: str, limit: int = 8) -> list[SensoryObservation]:
        normalized = keyword.strip().casefold()
        if not normalized:
            return self.recent(limit=limit)
        records: list[SensoryObservation] = []
        for observation in reversed(self._load_observations()):
            haystack = _observation_text(observation).casefold()
            if normalized not in haystack:
                continue
            records.append(observation)
            if len(records) >= limit:
                break
        return records

    def _load_observations(self) -> list[SensoryObservation]:
        if not self.path.exists():
            return []
        records: list[SensoryObservation] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                records.append(self._redact_observation(SensoryObservation.from_dict(data)))
            except (TypeError, ValueError):
                continue
        return records

    def _prune(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        threshold = datetime.now().astimezone() - timedelta(days=self.retention_days)
        kept: list[dict[str, Any]] = []
        for item in records:
            created_at = _parse_iso(str(item.get("created_at", "")))
            if created_at is not None and created_at < threshold:
                continue
            kept.append(item)
        return kept[-self.retention_limit :]

    def _redact_observation(self, observation: SensoryObservation) -> SensoryObservation:
        normalized = observation.normalized()
        summary, summary_redacted = _redact_text(normalized.summary)
        user_text, user_text_redacted = _redact_text(normalized.user_text)
        details, details_redacted = _redact_value(normalized.details)
        metadata, metadata_redacted = _redact_value(normalized.metadata)
        return SensoryObservation(
            id=normalized.id,
            source=normalized.source,
            created_at=normalized.created_at,
            summary=summary,
            details=details if isinstance(details, dict) else {},
            confidence=normalized.confidence,
            provider_id=normalized.provider_id,
            mode=normalized.mode,
            user_text=user_text,
            event_type=normalized.event_type,
            sensitive_redacted=(
                normalized.sensitive_redacted
                or summary_redacted
                or user_text_redacted
                or details_redacted
                or metadata_redacted
            ),
            metadata=metadata if isinstance(metadata, dict) else {},
        ).normalized()


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            redacted, item_changed = _redact_value(item)
            items.append(redacted)
            changed = changed or item_changed
        return items, changed
    if isinstance(value, dict):
        changed = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _RAW_MEDIA_KEYS:
                changed = True
                continue
            redacted, item_changed = _redact_value(item)
            result[key_text] = redacted
            changed = changed or item_changed
        return result, changed
    return value, False


def _redact_text(text: str) -> tuple[str, bool]:
    redacted = str(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, redacted != text


def _observation_text(observation: SensoryObservation) -> str:
    return "\n".join(
        [
            observation.summary,
            observation.user_text,
            observation.event_type,
            json.dumps(observation.details, ensure_ascii=False, default=str),
            json.dumps(observation.metadata, ensure_ascii=False, default=str),
        ]
    )


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
