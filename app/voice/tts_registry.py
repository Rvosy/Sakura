"""Runtime v2 TTS provider registry and built-in adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.voice.tts_contracts import TtsError, TtsErrorCode
from app.voice.tts_endpoint import GptSovitsEndpointResolver, GptSovitsEndpointSupervisor
from app.voice.tts_service import GenieServiceSupervisor
from app.voice.tts_settings import (
    GPTSoVITSTTSSettings,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
)
from app.voice.tts_synthesis import GenieSynthesisEngine, GPTSoVITSSynthesisEngine


@dataclass(frozen=True)
class TtsProviderComponents:
    provider_id: str
    supervisor: object
    engine: object
    endpoint_kind: str


ProviderFactory = Callable[
    [GPTSoVITSTTSSettings, Path, object, Callable[[], bool]],
    TtsProviderComponents,
]


class TtsProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, provider_id: str, factory: ProviderFactory) -> None:
        normalized = str(provider_id).strip()
        if not normalized or normalized in self._factories:
            raise ValueError(f"duplicate or empty TTS provider: {normalized}")
        self._factories[normalized] = factory

    def create(
        self,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        resource_manager: object,
        is_closed: Callable[[], bool],
    ) -> TtsProviderComponents:
        factory = self._factories.get(settings.provider)
        if factory is None:
            raise TtsError(
                TtsErrorCode.PROVIDER_NOT_FOUND,
                f"找不到 TTS Provider：{settings.provider}",
            )
        return factory(settings, base_dir, resource_manager, is_closed)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._factories)


def _gpt_sovits_factory(
    settings: GPTSoVITSTTSSettings,
    base_dir: Path,
    resource_manager: object,
    is_closed: Callable[[], bool],
) -> TtsProviderComponents:
    resolver = GptSovitsEndpointResolver(
        settings,
        base_dir=base_dir,
        resource_manager=resource_manager,
        is_closed=is_closed,
    )
    return TtsProviderComponents(
        provider_id=TTS_PROVIDER_GPT_SOVITS,
        supervisor=GptSovitsEndpointSupervisor(resolver),
        engine=GPTSoVITSSynthesisEngine(),
        endpoint_kind=resolver.endpoint.kind,
    )


def _genie_factory(
    settings: GPTSoVITSTTSSettings,
    base_dir: Path,
    resource_manager: object,
    is_closed: Callable[[], bool],
) -> TtsProviderComponents:
    # Adapter intentionally retains the established Genie implementation.
    supervisor = GenieServiceSupervisor(
        settings,
        base_dir=base_dir,
        resource_manager=resource_manager,
        is_closed=is_closed,
        adopt_existing_service=False,
    )
    return TtsProviderComponents(
        provider_id=TTS_PROVIDER_GENIE,
        supervisor=supervisor,
        engine=GenieSynthesisEngine(),
        endpoint_kind="managed" if settings.work_dir is not None else "custom",
    )


def default_tts_provider_registry() -> TtsProviderRegistry:
    registry = TtsProviderRegistry()
    registry.register(TTS_PROVIDER_GPT_SOVITS, _gpt_sovits_factory)
    registry.register(TTS_PROVIDER_GENIE, _genie_factory)
    return registry


__all__ = [
    "TtsProviderComponents",
    "TtsProviderRegistry",
    "default_tts_provider_registry",
]
