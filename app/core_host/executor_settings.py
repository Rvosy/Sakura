"""Generation-bound executor selection for the existing settings window."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.config.executor_settings import (
    ExecutorSettingsError, ExecutorSettingsRepository, parse_executor_selection,
)
from .protocol import error_payload, response


EXECUTOR_SETTINGS_REQUEST_NAMES = frozenset({"settings.executor.get", "settings.executor.save"})


class ExecutorSettingsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        user_root: Path,
        *,
        application_provider: Callable[[], object | None],
        status_provider: Callable[[], dict[str, object]],
        runtime_apply: Callable[[], None],
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._repository = ExecutorSettingsRepository(user_root)
        self._application_provider = application_provider
        self._status_provider = status_provider
        self._runtime_apply = runtime_apply
        self._save_lock = threading.Lock()

    def _candidates(self) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = [
            {"serviceKey": "", "displayName": "默认 Assistant", "pluginId": ""}
        ]
        application = self._application_provider()
        if application is not None:
            wait = getattr(application, "wait_until_loaded", None)
            if callable(wait) and not wait():
                return candidates
            collect = getattr(application, "execution_candidates", None)
            if callable(collect):
                candidates.extend(collect())
        return candidates

    def snapshot(self) -> dict[str, object]:
        selected = self._repository.read()
        candidates = self._candidates()
        runtime = self._status_provider()
        applied = runtime.get("serviceKey")
        readiness = runtime.get("readiness")
        if readiness in {"transport_ready", "initializing"}:
            state = "initializing"
        elif runtime.get("pending") is True or (applied is not None and applied != selected):
            state = "pending"
        elif applied == selected and readiness in {"ready", "degraded"}:
            state = "ready"
        else:
            state = "unavailable"
        return {
            "schema_version": 1,
            "selected_service_key": selected,
            "applied_service_key": applied,
            "state": state,
            "reason_code": str(runtime.get("code") or ""),
            "candidates": candidates,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise ExecutorSettingsError("INVALID_REQUEST", "互动方式设置请求无效。")
            if request.get("name") == "settings.executor.get" and not payload:
                result = self.snapshot()
            elif request.get("name") == "settings.executor.save" and set(payload) == {"serviceKey"}:
                with self._save_lock:
                    selected = parse_executor_selection(payload["serviceKey"])
                    if selected not in {item["serviceKey"] for item in self._candidates()}:
                        raise ExecutorSettingsError("EXECUTOR_UNAVAILABLE", "所选互动方式不可用，请检查插件是否已启用。")
                    self._repository.save(selected)
                    self._runtime_apply()
                    result = self.snapshot()
            else:
                raise ExecutorSettingsError("INVALID_REQUEST", "互动方式设置请求无效。")
            return response(request, generation_id=self._generation_id,
                            generation_credential=self._generation_credential,
                            protocol_minor=2, payload=result)
        except ExecutorSettingsError as error:
            return response(request, generation_id=self._generation_id,
                            generation_credential=self._generation_credential,
                            protocol_minor=2,
                            error=error_payload(error.code, error.message))
