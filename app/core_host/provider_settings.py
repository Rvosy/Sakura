"""Provider/model settings commands owned by one Core generation."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Mapping
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
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None] = lambda: None,
        plugin_application_provider: Callable[[], object | None] | None = None,
        runtime_apply: Callable[[], None] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._repository = ProviderModelSettingsRepository(app_root)
        self._session_provider = session_provider
        self._plugin_application_provider = plugin_application_provider
        self._runtime_apply = runtime_apply
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
                payload = self._snapshot()
            elif name == "settings.provider_model.save":
                raw = request.get("payload")
                if not isinstance(raw, Mapping) or set(raw) != {"draft"}:
                    raise ProviderModelSettingsError("INVALID_REQUEST", "设置请求格式无效。")
                with self._save_lock:
                    payload = self._save(raw["draft"])
                    if self._runtime_apply is not None:
                        self._runtime_apply()
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

    def _application(self) -> object | None:
        if self._plugin_application_provider is not None:
            return self._plugin_application_provider()
        session = self._session_provider()
        return getattr(session, "plugin_application", None) if session is not None else None

    def _plugin_slots(self) -> list[dict[str, Any]]:
        return self._plugin_slots_for_application(self._application())

    @staticmethod
    def _plugin_slots_for_application(application: object | None) -> list[dict[str, Any]]:
        if application is None:
            return []
        try:
            wait_until_loaded = getattr(application, "wait_until_loaded", None)
            if callable(wait_until_loaded):
                # The generation-scoped plugin application starts independently from
                # Assistant readiness.  A settings snapshot taken in this short
                # window must not publish an incomplete slot set that becomes
                # invalid by the time the user presses Save.
                wait_until_loaded()
            raw = getattr(application, "model_slots")()
        except Exception:
            return []
        result: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping):
                continue
            selection = item.get("selection", {})
            if not isinstance(selection, Mapping):
                selection = {}
            result.append({
                **dict(item),
                "selection": {
                    "profile_id": str(selection.get("profileId", "")),
                    "model": str(selection.get("model", "")),
                },
            })
        return result

    @classmethod
    def _plugin_slot_matches(
        cls,
        application: object,
        identity: str,
        selection: Mapping[str, str],
    ) -> bool:
        for slot in cls._plugin_slots_for_application(application):
            if slot.get("identity") != identity:
                continue
            return (
                slot.get("reasonCode") == "READY"
                and slot.get("selection") == dict(selection)
            )
        return False

    @staticmethod
    def _model_slot_failure_code(error: Exception) -> str:
        raw = getattr(error, "code", "")
        if not raw and error.args and error.args[0] in {
            "MODEL_SLOT_UNAVAILABLE",
            "MODEL_SLOT_SAVE_RESULT_INVALID",
            "MODEL_SLOT_SAVE_FAILED",
        }:
            raw = error.args[0]
        if (
            isinstance(raw, str)
            and 1 <= len(raw) <= 64
            and raw[0].isalpha()
            and raw == raw.upper()
            and raw.replace("_", "").isalnum()
        ):
            return raw
        return "MODEL_SLOT_SAVE_FAILED"

    @staticmethod
    def _log_model_slot_save_result(
        identity: str,
        *,
        reason_code: str,
        diagnostic: str = "",
    ) -> None:
        from app.core.runtime_log import external_runtime_sink_active, log_event

        if not external_runtime_sink_active():
            return

        attributes = {"name": identity, "reason_code": reason_code}
        if diagnostic:
            attributes["diagnostic"] = diagnostic
        reconciled = reason_code == "MODEL_SLOT_SAVE_RECONCILED"
        log_event(
            "Config",
            (
                "Plugin model slot save reconciled by readback"
                if reconciled
                else "Plugin model slot save failed"
            ),
            attributes,
            event=(
                "settings.provider_model.slot_save_reconciled"
                if reconciled
                else "settings.provider_model.slot_save_failed"
            ),
            severity="warning",
            verbosity=0,
        )

    def _snapshot(self) -> dict[str, Any]:
        base = self._repository.snapshot()
        core = base.get("model_slots", {})
        slots = [
            {
                "identity": "core:chat",
                "ownerType": "core",
                "ownerId": "sakura.core",
                "slotId": "chat",
                "label": "对话模型",
                "description": "Sakura 日常对话使用的主要模型。",
                "modelKind": "chat_completion",
                "required": True,
                "order": 10,
                "reasonCode": "READY",
                "selection": dict(core.get("chat", {})),
            },
            {
                "identity": "core:vision_chat",
                "ownerType": "core",
                "ownerId": "sakura.core",
                "slotId": "vision_chat",
                "label": "视觉对话模型",
                "description": "处理带图片或屏幕内容的对话。",
                "modelKind": "chat_completion",
                "required": False,
                "order": 20,
                "reasonCode": "READY",
                "selection": dict(core.get("vision_chat", {})),
            },
            *self._plugin_slots(),
        ]
        slots.sort(
            key=lambda slot: (
                float(slot.get("order", 100)),
                str(slot.get("ownerId", "")),
                str(slot.get("slotId", "")),
            )
        )
        return {
            **base,
            "schema_version": 1,
            "model_slots": slots,
        }

    def _save(
        self,
        raw: object,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ProviderModelSettingsError("INVALID_REQUEST", "设置请求格式无效。")
        if "model_slots" not in raw:
            return self._repository.save(raw)
        raw_slots = raw.get("model_slots")
        if not isinstance(raw_slots, Mapping):
            raise ProviderModelSettingsError("MODEL_SLOTS_INVALID", "模型槽配置无效。")
        current = {item["identity"]: item for item in self._snapshot()["model_slots"]}
        if set(raw_slots) <= {"chat", "vision_chat"}:
            raw_slots = {
                "core:chat": raw_slots.get("chat", {}),
                "core:vision_chat": raw_slots.get("vision_chat", {}),
                **{
                    identity: item.get("selection", {})
                    for identity, item in current.items()
                    if identity.startswith("plugin:")
                },
            }
        if set(raw_slots) != set(current):
            raise ProviderModelSettingsError("MODEL_SLOTS_INVALID", "模型槽已变化，请刷新后重试。")
        providers = raw.get("providers")
        if not isinstance(providers, list):
            raise ProviderModelSettingsError("PROVIDERS_INVALID", "Provider 列表无效。")
        allowed = {
            (str(item.get("id", "")), str(model))
            for item in providers if isinstance(item, Mapping)
            for model in item.get("models", []) if isinstance(item.get("models"), list)
        }
        normalized: dict[str, dict[str, Any]] = {}
        for identity, value in raw_slots.items():
            allowed_fields = {"profile_id", "model"}
            if identity == "core:chat":
                allowed_fields.add("context_window_tokens")
            if not isinstance(value, Mapping) or set(value) - allowed_fields:
                raise ProviderModelSettingsError("MODEL_SLOT_INVALID", "模型槽配置无效。")
            profile_id = value.get("profile_id", "")
            model = value.get("model", "")
            if not isinstance(profile_id, str) or not isinstance(model, str) or bool(profile_id) != bool(model):
                raise ProviderModelSettingsError("MODEL_SLOT_INCOMPLETE", "模型槽必须同时选择 Provider 和模型。")
            if current[str(identity)].get("required") is True and not profile_id:
                raise ProviderModelSettingsError("MODEL_SLOT_REQUIRED", "必选模型槽不能为空。")
            if profile_id and (profile_id, model) not in allowed:
                raise ProviderModelSettingsError("MODEL_REFERENCE_INVALID", "模型槽引用不存在的 Provider 或模型。")
            selection: dict[str, Any] = {"profile_id": profile_id, "model": model}
            if identity == "core:chat":
                context_window = value.get("context_window_tokens")
                if context_window is not None and (
                    isinstance(context_window, bool)
                    or not isinstance(context_window, int)
                    or not 4_096 <= context_window <= 2_000_000
                ):
                    raise ProviderModelSettingsError(
                        "MODEL_SLOT_INVALID",
                        "上下文窗口超出允许范围。",
                    )
                selection["context_window_tokens"] = context_window
            normalized[str(identity)] = selection

        core_draft = dict(raw)
        core_draft["model_slots"] = {
            "chat": normalized["core:chat"],
            "vision_chat": normalized["core:vision_chat"],
        }
        core_result = self._repository.save(core_draft)
        saved_slots = ["core:chat", "core:vision_chat"]
        pending = {
            identity: selection
            for identity, selection in normalized.items()
            if identity.startswith("plugin:")
            and dict(current[identity].get("selection", {})) != selection
        }
        plugin_result = self._save_plugin_slots(pending)
        return {
            **core_result,
            "save_state": plugin_result["save_state"],
            "saved_slots": [*saved_slots, *plugin_result["saved_slots"]],
            "failed_slot": plugin_result["failed_slot"],
            "plugin_reload_required": plugin_result["plugin_reload_required"],
        }

    def _save_plugin_slots(
        self,
        selections: Mapping[str, Mapping[str, str]],
    ) -> dict[str, Any]:
        saved_slots: list[str] = []
        application_states: list[str] = []
        failure: dict[str, str] | None = None
        application = self._application()
        for identity in sorted(selections):
            try:
                if application is None:
                    raise RuntimeError("MODEL_SLOT_UNAVAILABLE")
                result = getattr(application, "model_slot_save")(
                    identity,
                    {
                        "profileId": selections[identity]["profile_id"],
                        "model": selections[identity]["model"],
                    },
                )
                state = result.get("applicationState", "applied") if isinstance(result, Mapping) else "applied"
                if state not in {"applied", "restart_required", "error"}:
                    raise RuntimeError("MODEL_SLOT_SAVE_RESULT_INVALID")
                if state == "error":
                    raise RuntimeError("MODEL_SLOT_SAVE_FAILED")
                application_states.append(str(state))
                saved_slots.append(identity)
            except Exception as error:
                reason_code = self._model_slot_failure_code(error)
                if application is not None and self._plugin_slot_matches(
                    application,
                    identity,
                    selections[identity],
                ):
                    saved_slots.append(identity)
                    self._log_model_slot_save_result(
                        identity,
                        reason_code="MODEL_SLOT_SAVE_RECONCILED",
                        diagnostic=reason_code,
                    )
                    continue
                failure = {
                    "identity": identity,
                    "ownerType": "plugin",
                    "ownerId": identity.split(":", 2)[1],
                    "reasonCode": reason_code,
                }
                self._log_model_slot_save_result(
                    identity,
                    reason_code=reason_code,
                )
                break
        return {
            "save_state": "partial" if failure else "complete",
            "saved_slots": saved_slots,
            "failed_slot": failure,
            "plugin_reload_required": "restart_required" in application_states,
        }

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
