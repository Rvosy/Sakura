from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from typing import Any


_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")


class SakuraTTSHub:
    """Select one explicitly configured Provider without engine-specific branches."""

    def __init__(self, character: object) -> None:
        self._character = character
        self._providers: dict[str, object] = {}
        self._lock = threading.RLock()

    def registerProvider(self, provider_id: str, provider: object) -> Callable[[], None]:
        if (
            not isinstance(provider_id, str)
            or not _PROVIDER_ID.fullmatch(provider_id)
            or not callable(getattr(provider, "synthesize", None))
        ):
            raise ValueError("TTS_PROVIDER_INVALID")
        with self._lock:
            if provider_id in self._providers:
                raise ValueError("TTS_PROVIDER_CONFLICT")
            self._providers[provider_id] = provider
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            with self._lock:
                if self._providers.get(provider_id) is provider:
                    del self._providers[provider_id]

        return dispose

    def listProviders(self) -> list[dict[str, Any]]:
        with self._lock:
            providers = list(self._providers.items())
        return [self._provider_status(provider_id, provider) for provider_id, provider in providers]

    def status(self, character_id: str) -> dict[str, Any]:
        selected = self._selected_provider(character_id)
        if selected is None:
            return {
                "configured": False,
                "providerId": None,
                "available": False,
                "providers": self.listProviders(),
            }
        with self._lock:
            provider = self._providers.get(selected)
        return {
            "configured": True,
            "providerId": selected,
            "available": provider is not None and self._provider_available(provider),
            "providers": self.listProviders(),
        }

    def synthesize(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            return {"ok": False, "errorCode": "TTS_REQUEST_INVALID"}
        character_id = request.get("characterId")
        request_id = request.get("requestId")
        text = request.get("text")
        if (
            not isinstance(character_id, str)
            or not character_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(text, str)
            or not text.strip()
        ):
            return {"ok": False, "errorCode": "TTS_REQUEST_INVALID"}
        provider_id = self._selected_provider(character_id)
        if provider_id is None:
            return {"ok": False, "errorCode": "TTS_PROVIDER_NOT_SELECTED"}
        with self._lock:
            provider = self._providers.get(provider_id)
        if provider is None or not self._provider_available(provider):
            return {
                "ok": False,
                "errorCode": "TTS_PROVIDER_UNAVAILABLE",
                "providerId": provider_id,
            }
        try:
            artifact = getattr(provider, "synthesize")(dict(request))
        except Exception:
            return {
                "ok": False,
                "errorCode": "TTS_SYNTHESIS_FAILED",
                "providerId": provider_id,
            }
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "artifactId",
            "mediaType",
            "byteLength",
        }:
            return {
                "ok": False,
                "errorCode": "TTS_ARTIFACT_INVALID",
                "providerId": provider_id,
            }
        return {
            "ok": True,
            "providerId": provider_id,
            "artifact": dict(artifact),
        }

    def stop(self, request_id: str) -> dict[str, Any]:
        stopped = False
        with self._lock:
            providers = list(self._providers.values())
        for provider in providers:
            stop = getattr(provider, "stop", None)
            if not callable(stop):
                continue
            try:
                stopped = bool(stop(request_id)) or stopped
            except Exception:
                continue
        return {"accepted": stopped, "requestId": request_id}

    def _selected_provider(self, character_id: str) -> str | None:
        extension = getattr(self._character, "get")(character_id)
        provider_id = extension.get("provider") if isinstance(extension, Mapping) else None
        return (
            provider_id
            if isinstance(provider_id, str) and _PROVIDER_ID.fullmatch(provider_id)
            else None
        )

    @staticmethod
    def _provider_available(provider: object) -> bool:
        status = getattr(provider, "status", None)
        if not callable(status):
            return True
        try:
            result = status()
        except Exception:
            return False
        return bool(result.get("available")) if isinstance(result, Mapping) else bool(result)

    @classmethod
    def _provider_status(cls, provider_id: str, provider: object) -> dict[str, Any]:
        return {
            "providerId": provider_id,
            "available": cls._provider_available(provider),
        }


class SakuraTTSHubPlugin:
    def setup(self, context: object) -> None:
        character = getattr(context, "get")("sakura.host.character")
        getattr(context, "provide")(
            "sakura.tts",
            SakuraTTSHub(character),
            exports=("listProviders", "status", "synthesize", "stop"),
        )
