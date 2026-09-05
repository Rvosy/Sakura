from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


@dataclass(frozen=True)
class _ProviderDescriptor:
    provider_id: str
    service_key: str
    label: str


@dataclass
class _JobBinding:
    provider_id: str
    service_key: str
    job_id: str
    terminal: dict[str, Any] | None = None


@dataclass(frozen=True)
class _Selection:
    enabled: bool
    provider_id: str | None


class SakuraTTSHub:
    """Select one descriptor-backed Provider without engine-specific branches."""

    def __init__(self, context: object, character: object) -> None:
        self._context = context
        self._character = character
        self._providers: dict[str, _ProviderDescriptor] = {}
        self._jobs: dict[str, _JobBinding] = {}
        self._lock = threading.RLock()

    def registerProvider(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        value = self._descriptor(descriptor)
        with self._lock:
            existing = self._providers.get(value.provider_id)
            if existing is not None and existing.service_key != value.service_key:
                raise ValueError("TTS_PROVIDER_CONFLICT")
            if any(
                item.provider_id != value.provider_id
                and item.service_key == value.service_key
                for item in self._providers.values()
            ):
                raise ValueError("TTS_PROVIDER_CONFLICT")
            self._providers[value.provider_id] = value
        return {
            "registered": True,
            "providerId": value.provider_id,
            "serviceKey": value.service_key,
        }

    def unregisterProvider(self, provider_id: str, service_key: str) -> dict[str, Any]:
        if not self._valid_identifier(provider_id) or not self._valid_identifier(service_key):
            raise ValueError("TTS_PROVIDER_INVALID")
        with self._lock:
            descriptor = self._providers.get(provider_id)
            removed = descriptor is not None and descriptor.service_key == service_key
            if removed:
                del self._providers[provider_id]
                for binding in self._jobs.values():
                    if binding.provider_id == provider_id and binding.service_key == service_key:
                        binding.terminal = {"state": "cancelled"}
        return {
            "removed": removed,
            "providerId": provider_id,
            "serviceKey": service_key,
        }

    def listProviders(self) -> list[dict[str, Any]]:
        with self._lock:
            providers = list(self._providers.values())
        return [self._provider_status(descriptor) for descriptor in providers]

    def status(self, character_id: str) -> dict[str, Any]:
        selection = self._selection(character_id)
        with self._lock:
            descriptor = (
                self._providers.get(selection.provider_id)
                if selection.provider_id is not None
                else None
            )
        available = (
            selection.enabled
            and descriptor is not None
            and self._provider_available(descriptor)
        )
        return {
            "configured": selection.provider_id is not None,
            "enabled": selection.enabled,
            "providerId": selection.provider_id,
            "available": available,
            "providers": self.listProviders(),
        }

    def configure(self, character_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise ValueError("TTS_CHARACTER_INVALID")
        if not isinstance(values, Mapping) or set(values) != {"enabled", "provider"}:
            raise ValueError("TTS_SELECTION_INVALID")
        enabled = values.get("enabled")
        provider_id = values.get("provider")
        if not isinstance(enabled, bool) or (
            provider_id is not None and not self._valid_identifier(provider_id)
        ):
            raise ValueError("TTS_SELECTION_INVALID")
        if enabled and provider_id is None:
            raise ValueError("TTS_PROVIDER_NOT_SELECTED")
        getattr(self._character, "update")(
            character_id,
            {"enabled": enabled, "provider": provider_id},
        )
        return self.status(character_id)

    def warmup(self, character_id: str) -> dict[str, Any]:
        selection = self._selection(character_id)
        provider_id = selection.provider_id
        if not selection.enabled:
            return {"accepted": False, "providerId": provider_id, "reasonCode": "TTS_DISABLED"}
        if provider_id is None:
            return {
                "accepted": False,
                "providerId": None,
                "reasonCode": "TTS_PROVIDER_NOT_SELECTED",
            }
        with self._lock:
            descriptor = self._providers.get(provider_id)
        readiness = self._provider_readiness(descriptor) if descriptor is not None else None
        if descriptor is None or readiness is None or not readiness[0]:
            reason_code = readiness[1] if readiness is not None else "TTS_PROVIDER_UNAVAILABLE"
            stage = readiness[2] if readiness is not None else "provider_selection"
            return {
                "accepted": False,
                "providerId": provider_id,
                "reasonCode": reason_code,
                "stage": stage,
            }
        try:
            result = self._provider(descriptor).warmup(character_id)
        except Exception as error:
            return {
                "accepted": False,
                "providerId": provider_id,
                "reasonCode": _stable_error_code(error, "TTS_WARMUP_FAILED"),
                "stage": "provider_warmup",
                "errorType": type(error).__name__,
            }
        if isinstance(result, Mapping):
            accepted = result.get("accepted") is True
            reason_code = _stable_error_code(
                result.get("reasonCode"),
                "READY" if accepted else "TTS_WARMUP_SKIPPED",
            )
            response: dict[str, Any] = {
                "accepted": accepted,
                "providerId": provider_id,
                "reasonCode": reason_code,
            }
            stage = result.get("stage")
            error_type = result.get("errorType")
            if isinstance(stage, str) and _IDENTIFIER.fullmatch(stage):
                response["stage"] = stage
            if isinstance(error_type, str) and _IDENTIFIER.fullmatch(error_type):
                response["errorType"] = error_type
            return response
        accepted = bool(result)
        return {
            "accepted": accepted,
            "providerId": provider_id,
            "reasonCode": "READY" if accepted else "TTS_WARMUP_SKIPPED",
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
        selection = self._selection(character_id)
        provider_id = selection.provider_id
        if provider_id is None:
            return self._failed(request_id, None, "TTS_PROVIDER_NOT_SELECTED")
        if not selection.enabled:
            return self._failed(request_id, provider_id, "TTS_DISABLED")
        with self._lock:
            if request_id in self._jobs:
                return self._failed(request_id, provider_id, "TTS_JOB_CONFLICT")
            descriptor = self._providers.get(provider_id)
        readiness = self._provider_readiness(descriptor) if descriptor is not None else None
        if descriptor is None or readiness is None or not readiness[0]:
            return self._failed(
                request_id,
                provider_id,
                readiness[1] if readiness is not None else "TTS_PROVIDER_UNAVAILABLE",
            )
        try:
            job_id = self._provider(descriptor).begin(
                {
                    "requestId": request_id,
                    "characterId": character_id,
                    "text": text,
                    "options": dict(request["options"]),
                }
            )
        except Exception:
            return self._failed(request_id, provider_id, "TTS_SYNTHESIS_FAILED")
        if isinstance(job_id, Mapping):
            error_code = job_id.get("errorCode")
            return self._failed(
                request_id,
                provider_id,
                error_code
                if isinstance(error_code, str) and _ERROR_CODE.fullmatch(error_code)
                else "TTS_SYNTHESIS_FAILED",
            )
        if not self._valid_identifier(job_id):
            return self._failed(request_id, provider_id, "TTS_JOB_INVALID")
        binding = _JobBinding(provider_id, descriptor.service_key, job_id)
        with self._lock:
            if request_id in self._jobs:
                try:
                    self._provider(descriptor).cancel(job_id)
                except Exception:
                    pass
                return self._failed(request_id, provider_id, "TTS_JOB_CONFLICT")
            self._jobs[request_id] = binding
            if self._providers.get(provider_id) != descriptor:
                binding.terminal = {"state": "cancelled"}
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
            terminal = dict(binding.terminal) if binding and binding.terminal else None
        if binding is None:
            return self._failed(request_id, None, "TTS_JOB_NOT_FOUND")
        if terminal is None:
            try:
                result = self._provider_by_key(binding.service_key).poll(binding.job_id)
            except Exception:
                result = {"state": "failed", "errorCode": "TTS_PROVIDER_UNAVAILABLE"}
        else:
            result = terminal
        normalized = self._normalize_poll(request_id, binding.provider_id, result)
        if normalized["state"] != "running":
            with self._lock:
                if self._jobs.get(request_id) is binding:
                    del self._jobs[request_id]
        return normalized

    def cancel(self, request_id: str) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            return {"accepted": False, "requestId": ""}
        with self._lock:
            binding = self._jobs.get(request_id)
        if binding is None or binding.terminal is not None:
            accepted = False
        else:
            try:
                accepted = bool(
                    self._provider_by_key(binding.service_key).cancel(binding.job_id)
                )
            except Exception:
                accepted = False
        return {"accepted": accepted, "requestId": request_id}

    @classmethod
    def _normalize_poll(
        cls,
        request_id: str,
        provider_id: str,
        result: object,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            return cls._failed(request_id, provider_id, "TTS_JOB_RESULT_INVALID")
        state = result.get("state")
        if state == "running" and set(result) == {"state"}:
            return {"state": "running", "requestId": request_id, "providerId": provider_id}
        if state == "cancelled" and set(result) == {"state"}:
            return {"state": "cancelled", "requestId": request_id, "providerId": provider_id}
        if state == "failed" and set(result) == {"state", "errorCode"}:
            error_code = result.get("errorCode")
            return cls._failed(
                request_id,
                provider_id,
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
                "providerId": provider_id,
                "artifact": dict(artifact),
            }
        return cls._failed(request_id, provider_id, "TTS_JOB_RESULT_INVALID")

    @staticmethod
    def _failed(request_id: str, provider_id: str | None, error_code: str) -> dict[str, Any]:
        return {
            "state": "failed",
            "requestId": request_id,
            "providerId": provider_id,
            "errorCode": error_code,
        }

    def _selection(self, character_id: str) -> _Selection:
        extension = getattr(self._character, "get")(character_id)
        enabled = extension.get("enabled") if isinstance(extension, Mapping) else None
        provider_id = extension.get("provider") if isinstance(extension, Mapping) else None
        return _Selection(
            enabled=enabled if isinstance(enabled, bool) else False,
            provider_id=provider_id if self._valid_identifier(provider_id) else None,
        )

    def _provider_status(self, descriptor: _ProviderDescriptor) -> dict[str, Any]:
        available, _reason_code, _stage = self._provider_readiness(descriptor)
        return {
            "providerId": descriptor.provider_id,
            "label": descriptor.label,
            "available": available,
        }

    def _provider_readiness(
        self,
        descriptor: _ProviderDescriptor,
    ) -> tuple[bool, str, str]:
        try:
            result = self._provider(descriptor).status()
        except Exception:
            return False, "TTS_PROVIDER_UNAVAILABLE", "provider_status"
        if not isinstance(result, Mapping):
            return bool(result), "READY" if result else "TTS_PROVIDER_UNAVAILABLE", "provider_status"
        available = bool(result.get("available"))
        reason_code = _stable_error_code(
            result.get("reasonCode"),
            "READY" if available else "TTS_PROVIDER_UNAVAILABLE",
        )
        stage = result.get("stage")
        return (
            available,
            reason_code,
            stage if isinstance(stage, str) and _IDENTIFIER.fullmatch(stage) else "provider_status",
        )

    def _provider_available(self, descriptor: _ProviderDescriptor) -> bool:
        return bool(self._provider_status(descriptor)["available"])

    def _provider(self, descriptor: _ProviderDescriptor) -> object:
        return self._provider_by_key(descriptor.service_key)

    def _provider_by_key(self, service_key: str) -> object:
        return getattr(self._context, "get")(service_key)

    @classmethod
    def _descriptor(cls, value: Mapping[str, Any]) -> _ProviderDescriptor:
        if not isinstance(value, Mapping) or set(value) != {
            "providerId",
            "serviceKey",
            "label",
        }:
            raise ValueError("TTS_PROVIDER_INVALID")
        provider_id = value.get("providerId")
        service_key = value.get("serviceKey")
        label = value.get("label")
        if (
            not cls._valid_identifier(provider_id)
            or not cls._valid_identifier(service_key)
            or not isinstance(label, str)
            or not label.strip()
            or len(label.strip()) > 120
        ):
            raise ValueError("TTS_PROVIDER_INVALID")
        return _ProviderDescriptor(provider_id, service_key, label.strip())

    @staticmethod
    def _valid_identifier(value: object) -> bool:
        return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


class SakuraTTSHubPlugin:
    def setup(self, context: object) -> None:
        character = getattr(context, "get")("sakura.host.character")
        getattr(context, "provide")(
            "sakura.tts",
            SakuraTTSHub(context, character),
            exports=(
                "registerProvider",
                "unregisterProvider",
                "listProviders",
                "status",
                "configure",
                "warmup",
                "begin",
                "poll",
                "cancel",
            ),
        )


def _stable_error_code(value: object, fallback: str) -> str:
    direct = str(getattr(value, "code", value) or "").strip()
    prefix = direct.split(":", 1)[0].strip()
    return prefix if _ERROR_CODE.fullmatch(prefix) else fallback
