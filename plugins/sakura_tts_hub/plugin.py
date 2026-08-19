from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


@dataclass(frozen=True)
class _JobBinding:
    provider_id: str
    provider: object
    job: object


class SakuraTTSHub:
    """Select one explicitly configured Provider without engine-specific branches."""

    def __init__(self, character: object) -> None:
        self._character = character
        self._providers: dict[str, object] = {}
        self._jobs: dict[str, _JobBinding] = {}
        self._lock = threading.RLock()

    def registerProvider(self, provider_id: str, provider: object) -> Callable[[], None]:
        if (
            not isinstance(provider_id, str)
            or not _PROVIDER_ID.fullmatch(provider_id)
            or not callable(getattr(provider, "begin", None))
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
                jobs = [
                    (request_id, binding)
                    for request_id, binding in self._jobs.items()
                    if binding.provider is provider
                ]
                for request_id, _binding in jobs:
                    self._jobs.pop(request_id, None)
            for _request_id, binding in jobs:
                self._cancel_job(binding.job)

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

    def begin(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "requestId",
            "characterId",
            "text",
            "options",
        }:
            return self._failed("", None, "TTS_REQUEST_INVALID")
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
            or not isinstance(request.get("options"), Mapping)
        ):
            return self._failed(
                request_id if isinstance(request_id, str) else "",
                None,
                "TTS_REQUEST_INVALID",
            )
        provider_id = self._selected_provider(character_id)
        if provider_id is None:
            return self._failed(request_id, None, "TTS_PROVIDER_NOT_SELECTED")
        with self._lock:
            if request_id in self._jobs:
                return self._failed(request_id, provider_id, "TTS_JOB_CONFLICT")
            provider = self._providers.get(provider_id)
        if provider is None or not self._provider_available(provider):
            return self._failed(request_id, provider_id, "TTS_PROVIDER_UNAVAILABLE")
        try:
            job = getattr(provider, "begin")(
                {
                    "requestId": request_id,
                    "characterId": character_id,
                    "text": text,
                    "options": dict(request["options"]),
                }
            )
        except Exception:
            return self._failed(request_id, provider_id, "TTS_SYNTHESIS_FAILED")
        if not callable(getattr(job, "poll", None)) or not callable(
            getattr(job, "cancel", None)
        ):
            self._cancel_job(job)
            return self._failed(request_id, provider_id, "TTS_JOB_INVALID")
        binding = _JobBinding(provider_id, provider, job)
        with self._lock:
            if self._providers.get(provider_id) is not provider:
                self._cancel_job(job)
                return self._failed(request_id, provider_id, "TTS_PROVIDER_UNAVAILABLE")
            if request_id in self._jobs:
                self._cancel_job(job)
                return self._failed(request_id, provider_id, "TTS_JOB_CONFLICT")
            self._jobs[request_id] = binding
        return {
            "state": "running",
            "requestId": request_id,
            "providerId": provider_id,
        }

    def poll(self, request_id: str) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            return self._failed("", None, "TTS_REQUEST_INVALID")
        with self._lock:
            binding = self._jobs.get(request_id)
        if binding is None:
            return self._failed(request_id, None, "TTS_JOB_NOT_FOUND")
        try:
            result = getattr(binding.job, "poll")()
        except Exception:
            result = {"state": "failed", "errorCode": "TTS_SYNTHESIS_FAILED"}
        normalized = self._normalize_poll(request_id, binding, result)
        if normalized["state"] != "running":
            with self._lock:
                if self._jobs.get(request_id) is binding:
                    self._jobs.pop(request_id, None)
        return normalized

    def cancel(self, request_id: str) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            return {"accepted": False, "requestId": ""}
        with self._lock:
            binding = self._jobs.get(request_id)
        return {
            "accepted": binding is not None and self._cancel_job(binding.job),
            "requestId": request_id,
        }

    @classmethod
    def _normalize_poll(
        cls,
        request_id: str,
        binding: _JobBinding,
        result: object,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            return cls._failed(request_id, binding.provider_id, "TTS_JOB_RESULT_INVALID")
        state = result.get("state")
        if state == "running" and set(result) == {"state"}:
            return {
                "state": "running",
                "requestId": request_id,
                "providerId": binding.provider_id,
            }
        if state == "cancelled" and set(result) == {"state"}:
            return {
                "state": "cancelled",
                "requestId": request_id,
                "providerId": binding.provider_id,
            }
        if state == "failed" and set(result) == {"state", "errorCode"}:
            error_code = result.get("errorCode")
            return cls._failed(
                request_id,
                binding.provider_id,
                error_code
                if isinstance(error_code, str) and _ERROR_CODE.fullmatch(error_code)
                else "TTS_SYNTHESIS_FAILED",
            )
        artifact = result.get("artifact")
        if (
            state == "succeeded"
            and set(result) == {"state", "artifact"}
            and isinstance(artifact, Mapping)
            and set(artifact) == {"artifactId", "mediaType", "byteLength"}
        ):
            return {
                "state": "succeeded",
                "requestId": request_id,
                "providerId": binding.provider_id,
                "artifact": dict(artifact),
            }
        return cls._failed(request_id, binding.provider_id, "TTS_JOB_RESULT_INVALID")

    @staticmethod
    def _failed(
        request_id: str,
        provider_id: str | None,
        error_code: str,
    ) -> dict[str, Any]:
        return {
            "state": "failed",
            "requestId": request_id,
            "providerId": provider_id,
            "errorCode": error_code,
        }

    @staticmethod
    def _cancel_job(job: object) -> bool:
        try:
            return bool(getattr(job, "cancel")())
        except Exception:
            return False

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
            exports=("listProviders", "status", "begin", "poll", "cancel"),
        )
