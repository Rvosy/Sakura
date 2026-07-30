"""Provider/model settings commands owned by one Core generation."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config.provider_model_settings import (
    ProviderModelSettingsError,
    ProviderModelSettingsRepository,
)
from app.core.cancellation import CancellationToken, OperationCancelled
from app.core.retry_policy import MAX_AUTO_RETRY_ATTEMPTS
from app.llm.api_client import ApiConfigError, ApiRequestError, ApiSettings, OpenAICompatibleClient

from .protocol import error_payload, response


SETTINGS_REQUEST_NAMES = frozenset(
    {
        "settings.provider_model.get",
        "settings.provider_model.save",
        "settings.provider_model.list_models",
        "settings.provider_model.test_connection",
    }
)


class ProviderSettingsBoundary:
    def __init__(self, generation_id: str, generation_credential: str, app_root: Path) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._repository = ProviderModelSettingsRepository(app_root)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._operations: dict[str, CancellationToken] = {}
        self._closed = False
        self._enabled = False

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied_credential = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied_credential, str)
            or not hmac.compare_digest(supplied_credential, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        name = request.get("name")
        try:
            with self._lock:
                enabled = self._enabled and not self._closed
            if not enabled:
                raise ProviderModelSettingsError(
                    "CAPABILITY_NEGOTIATION_FAILED",
                    "设置能力尚未协商。",
                )
            if name == "settings.provider_model.get":
                self._require_empty_payload(request)
                payload = self._repository.snapshot()
            elif name == "settings.provider_model.save":
                raw = request.get("payload")
                if not isinstance(raw, Mapping) or set(raw) != {"draft"}:
                    raise ProviderModelSettingsError("INVALID_REQUEST", "设置请求格式无效。")
                with self._save_lock:
                    payload = self._repository.save(raw["draft"])
            elif name in {
                "settings.provider_model.list_models",
                "settings.provider_model.test_connection",
            }:
                payload = self._probe(request, require_model=name.endswith("test_connection"))
            else:
                raise ProviderModelSettingsError("UNKNOWN_COMMAND", "不支持的设置命令。")
            return self._response(request, payload=payload)
        except ProviderModelSettingsError as error:
            return self._response(request, error=error.public_error())
        except OperationCancelled:
            return self._response(
                request,
                error={
                    "code": "OPERATION_CANCELLED",
                    "message": "操作已取消。",
                    "feature": "providers.list_models",
                    "field": "",
                },
            )
        except ApiConfigError:
            return self._response(
                request,
                error={
                    "code": "CREDENTIAL_REQUIRED",
                    "message": "该供应商尚未配置凭据。",
                    "feature": "providers.credentials",
                    "field": "credential",
                },
            )
        except ApiRequestError as error:
            text = str(error).lower()
            if any(marker in text for marker in ("401", "403", "unauthorized", "forbidden")):
                code, message = "AUTHENTICATION_FAILED", "供应商认证失败。"
            elif any(marker in text for marker in ("timeout", "timed out", "超时")):
                code, message = "PROVIDER_TIMEOUT", "供应商请求超时。"
            else:
                code, message = "PROVIDER_REQUEST_FAILED", "供应商请求失败。"
            return self._response(
                request,
                error={"code": code, "message": message, "feature": "providers.test_connection", "field": ""},
            )
        except Exception:  # noqa: BLE001 - never cross the process boundary with private details
            return self._response(
                request,
                error={
                    "code": "PROVIDER_REQUEST_FAILED",
                    "message": "供应商请求失败。",
                    "feature": "providers.test_connection",
                    "field": "",
                },
            )

    def cancel(self, operation_id: object) -> bool:
        if not isinstance(operation_id, str) or not operation_id.strip():
            return False
        with self._lock:
            token = self._operations.get(operation_id)
        if token is None:
            return False
        token.cancel()
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            tokens = list(self._operations.values())
            self._operations.clear()
        for token in tokens:
            token.cancel()

    def _probe(self, request: dict[str, Any], *, require_model: bool) -> dict[str, Any]:
        from app.core.runtime_log import suppress_runtime_logs

        raw = request.get("payload")
        if not isinstance(raw, Mapping):
            raise ProviderModelSettingsError("INVALID_REQUEST", "请求格式无效。")
        operation_id = raw.get("operation_id")
        if operation_id != request.get("id") or not isinstance(operation_id, str):
            raise ProviderModelSettingsError("INVALID_OPERATION_ID", "操作标识无效。")
        if set(raw) != {"operation_id", "profile"}:
            raise ProviderModelSettingsError("INVALID_REQUEST", "请求格式无效。")
        token = CancellationToken()
        with self._lock:
            if self._closed:
                raise OperationCancelled()
            if operation_id in self._operations:
                raise ProviderModelSettingsError("OPERATION_DUPLICATE", "操作标识重复。")
            self._operations[operation_id] = token
        try:
            base_url, secret, model, timeout = self._repository.resolve_probe(
                raw["profile"],
                require_model=require_model,
            )
            # The shared client retries each HTTP request. Treat the setting as
            # a total probe budget so the Rust-side 65 second deadline remains
            # strictly larger than the worst-case three attempts plus backoff.
            per_attempt_timeout = max(1, timeout // MAX_AUTO_RETRY_ATTEMPTS)
            client = OpenAICompatibleClient(
                ApiSettings(
                    base_url=base_url,
                    api_key=secret,
                    model=model,
                    timeout_seconds=per_attempt_timeout,
                )
            )
            # Core stdout is reserved for framed protocol bytes.  The shared
            # client emits normal runtime logs to stdout, so probe traffic must
            # use the same suppression boundary as real chat execution.
            with suppress_runtime_logs():
                if require_model:
                    message = client.test_connection(cancel_checker=token.throw_if_cancelled)
                else:
                    models = client.list_models(cancel_checker=token.throw_if_cancelled)
            token.throw_if_cancelled()
            if require_model:
                return {"message": "OK" if message else "OK"}
            return {"models": models}
        finally:
            with self._lock:
                self._operations.pop(operation_id, None)

    @staticmethod
    def _require_empty_payload(request: Mapping[str, Any]) -> None:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or payload:
            raise ProviderModelSettingsError("INVALID_REQUEST", "设置读取请求格式无效。")

    def _response(
        self,
        request: dict[str, Any],
        *,
        payload: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        protocol_error = None
        if error:
            protocol_error = error_payload(error["code"], error["message"])
            protocol_error["details"] = {
                "feature": error["feature"],
                "field": error["field"],
            }
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=int(request["protocolMinor"]),
            payload=payload,
            error=protocol_error,
        )
