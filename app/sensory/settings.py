from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.sensory.models import (
    SensoryProviderMode,
    SensorySource,
    clamp_confidence,
    coerce_provider_mode,
    coerce_sensory_source,
)


SENSORY_DEFAULT_CONTEXT_BUDGET_CHARS = 1200
SENSORY_MIN_CONTEXT_BUDGET_CHARS = 200
SENSORY_MAX_CONTEXT_BUDGET_CHARS = 6000
SENSORY_DEFAULT_CONTEXT_LIMIT = 4
SENSORY_MAX_CONTEXT_LIMIT = 20
SENSORY_DEFAULT_RETENTION_DAYS = 7
SENSORY_DEFAULT_RETENTION_LIMIT = 300
SENSORY_DEFAULT_PROVIDER_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class SensorySourceSettings:
    mode: SensoryProviderMode = SensoryProviderMode.OFF
    provider_id: str = ""
    confidence_threshold: float = 0.5
    context_enabled: bool = True
    context_limit: int = SENSORY_DEFAULT_CONTEXT_LIMIT

    @property
    def enabled(self) -> bool:
        return self.mode != SensoryProviderMode.OFF

    def normalized(self, source: SensorySource | str | None = None) -> "SensorySourceSettings":
        mode = coerce_provider_mode(self.mode)
        provider_id = str(self.provider_id or "").strip()
        if not provider_id and source is not None and mode != SensoryProviderMode.OFF:
            provider_id = default_provider_id(coerce_sensory_source(source), mode)
        return SensorySourceSettings(
            mode=mode,
            provider_id=provider_id,
            confidence_threshold=clamp_confidence(self.confidence_threshold),
            context_enabled=bool(self.context_enabled),
            context_limit=_clamp_int(self.context_limit, 1, SENSORY_MAX_CONTEXT_LIMIT),
        )

    def to_mapping(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "mode": normalized.mode.value,
            "provider_id": normalized.provider_id,
            "confidence_threshold": float(normalized.confidence_threshold),
            "context_enabled": bool(normalized.context_enabled),
            "context_limit": int(normalized.context_limit),
        }


@dataclass(frozen=True)
class SensoryProviderConfig:
    provider_id: str
    source: SensorySource
    mode: SensoryProviderMode
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = SENSORY_DEFAULT_PROVIDER_TIMEOUT_SECONDS
    extra: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "SensoryProviderConfig":
        source = coerce_sensory_source(self.source)
        mode = coerce_provider_mode(self.mode)
        provider_id = str(self.provider_id or "").strip() or default_provider_id(source, mode)
        return SensoryProviderConfig(
            provider_id=provider_id,
            source=source,
            mode=mode,
            endpoint=str(self.endpoint or "").strip(),
            model=str(self.model or "").strip(),
            api_key=str(self.api_key or "").strip(),
            timeout_seconds=_clamp_int(self.timeout_seconds, 1, 300),
            extra=_mapping(self.extra),
        )

    def to_mapping(self) -> dict[str, Any]:
        normalized = self.normalized()
        data: dict[str, Any] = {
            "source": normalized.source.value,
            "mode": normalized.mode.value,
            "endpoint": normalized.endpoint,
            "model": normalized.model,
            "timeout_seconds": int(normalized.timeout_seconds),
        }
        if normalized.api_key:
            data["api_key"] = normalized.api_key
        data.update(normalized.extra)
        return data


@dataclass(frozen=True)
class SensorySettings:
    enabled: bool = False
    context_enabled: bool = True
    context_budget_chars: int = SENSORY_DEFAULT_CONTEXT_BUDGET_CHARS
    retention_days: int = SENSORY_DEFAULT_RETENTION_DAYS
    retention_limit: int = SENSORY_DEFAULT_RETENTION_LIMIT
    sources: dict[SensorySource, SensorySourceSettings] = field(default_factory=dict)
    providers: dict[str, SensoryProviderConfig] = field(default_factory=dict)

    def normalized(self) -> "SensorySettings":
        sources = _default_source_settings()
        for source, settings in self.sources.items():
            normalized_source = coerce_sensory_source(source)
            sources[normalized_source] = settings.normalized(normalized_source)
        providers = {
            config.normalized().provider_id: config.normalized()
            for config in self.providers.values()
        }
        return SensorySettings(
            enabled=bool(self.enabled),
            context_enabled=bool(self.context_enabled),
            context_budget_chars=_clamp_int(
                self.context_budget_chars,
                SENSORY_MIN_CONTEXT_BUDGET_CHARS,
                SENSORY_MAX_CONTEXT_BUDGET_CHARS,
            ),
            retention_days=_clamp_int(self.retention_days, 1, 365),
            retention_limit=_clamp_int(self.retention_limit, 10, 5000),
            sources=sources,
            providers=providers,
        )

    def source_settings(self, source: SensorySource | str) -> SensorySourceSettings:
        normalized = self.normalized()
        return normalized.sources[coerce_sensory_source(source)]

    def provider_for_source(self, source: SensorySource | str) -> SensoryProviderConfig | None:
        normalized = self.normalized()
        source_id = coerce_sensory_source(source)
        source_settings = normalized.sources[source_id]
        if source_settings.mode == SensoryProviderMode.OFF:
            return None
        provider_id = source_settings.provider_id or default_provider_id(source_id, source_settings.mode)
        return normalized.providers.get(provider_id)

    def with_providers(self, providers: dict[str, SensoryProviderConfig]) -> "SensorySettings":
        return replace(self, providers=providers).normalized()

    def to_system_mapping(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "enabled": bool(normalized.enabled),
            "context_enabled": bool(normalized.context_enabled),
            "context_budget_chars": int(normalized.context_budget_chars),
            "retention_days": int(normalized.retention_days),
            "retention_limit": int(normalized.retention_limit),
            "sources": {
                source.value: settings.to_mapping()
                for source, settings in normalized.sources.items()
            },
        }

    def to_api_mapping(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "providers": {
                provider_id: config.to_mapping()
                for provider_id, config in normalized.providers.items()
            }
        }


def default_provider_id(source: SensorySource, mode: SensoryProviderMode) -> str:
    return f"{source.value}_{mode.value}"


def sensory_settings_from_config(
    system_section: dict[str, Any] | None,
    api_section: dict[str, Any] | None = None,
) -> SensorySettings:
    system = _mapping(system_section)
    source_data = _mapping(system.get("sources"))
    sources = _default_source_settings()
    for source in SensorySource:
        if source.value in source_data:
            sources[source] = _source_settings_from_mapping(source_data[source.value], source)

    settings = SensorySettings(
        enabled=_bool_value(system.get("enabled"), False),
        context_enabled=_bool_value(system.get("context_enabled"), True),
        context_budget_chars=_int_value(
            system.get("context_budget_chars"),
            SENSORY_DEFAULT_CONTEXT_BUDGET_CHARS,
        ),
        retention_days=_int_value(system.get("retention_days"), SENSORY_DEFAULT_RETENTION_DAYS),
        retention_limit=_int_value(system.get("retention_limit"), SENSORY_DEFAULT_RETENTION_LIMIT),
        sources=sources,
        providers=_provider_configs_from_mapping(_mapping(api_section).get("providers")),
    )
    return settings.normalized()


def _default_source_settings() -> dict[SensorySource, SensorySourceSettings]:
    return {
        SensorySource.VISION: SensorySourceSettings(),
        SensorySource.SPEECH: SensorySourceSettings(),
        SensorySource.SOUND: SensorySourceSettings(),
    }


def _source_settings_from_mapping(
    value: Any,
    source: SensorySource,
) -> SensorySourceSettings:
    data = _mapping(value)
    return SensorySourceSettings(
        mode=coerce_provider_mode(data.get("mode")),
        provider_id=str(data.get("provider_id") or ""),
        confidence_threshold=clamp_confidence(data.get("confidence_threshold", 0.5)),
        context_enabled=_bool_value(data.get("context_enabled"), True),
        context_limit=_int_value(data.get("context_limit"), SENSORY_DEFAULT_CONTEXT_LIMIT),
    ).normalized(source)


def _provider_configs_from_mapping(value: Any) -> dict[str, SensoryProviderConfig]:
    data = _mapping(value)
    providers: dict[str, SensoryProviderConfig] = {}
    for key, raw in data.items():
        item = _mapping(raw)
        if "source" in item or "mode" in item:
            config = _provider_config_from_flat_mapping(str(key), item)
            providers[config.provider_id] = config
            continue
        source = coerce_sensory_source(key)
        for mode_key, nested in item.items():
            mode = coerce_provider_mode(mode_key)
            if mode == SensoryProviderMode.OFF:
                continue
            nested_data = _mapping(nested)
            config = _provider_config_from_flat_mapping(
                str(nested_data.get("id") or default_provider_id(source, mode)),
                {**nested_data, "source": source.value, "mode": mode.value},
            )
            providers[config.provider_id] = config
    return providers


def _provider_config_from_flat_mapping(provider_id: str, data: dict[str, Any]) -> SensoryProviderConfig:
    known = {"source", "mode", "endpoint", "model", "api_key", "timeout_seconds", "id"}
    config = SensoryProviderConfig(
        provider_id=str(data.get("id") or provider_id),
        source=coerce_sensory_source(data.get("source")),
        mode=coerce_provider_mode(data.get("mode")),
        endpoint=str(data.get("endpoint") or data.get("base_url") or ""),
        model=str(data.get("model") or ""),
        api_key=str(data.get("api_key") or ""),
        timeout_seconds=_int_value(data.get("timeout_seconds"), SENSORY_DEFAULT_PROVIDER_TIMEOUT_SECONDS),
        extra={key: value for key, value in data.items() if key not in known},
    ).normalized()
    return config


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _int_value(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
