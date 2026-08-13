"""Generation-scoped headless real chat boundary for Runtime v2."""

from __future__ import annotations

import hmac
import json
import re
import sys
import threading
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from .chat_fixture import CHAT_CLOSE_TIMEOUT_SECONDS, CHAT_MESSAGE_LIMIT
from .protocol import event, response

if TYPE_CHECKING:
    from app.core.cancellation import CancellationToken
    from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore


REAL_CHAT_EXECUTION_LIMIT = 1


class RealChatRejection(ValueError):
    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _new_cancellation_token() -> CancellationToken:
    from app.core.cancellation import CancellationToken

    return CancellationToken()


@dataclass
class _Execution:
    operation_id: str
    cancel: CancellationToken = field(default_factory=_new_cancellation_token)
    started: bool = False
    cancel_requested: bool = False
    terminal: str | None = None


class RealChatBoundary:
    """Own operation arbitration, real Pipeline calls and best-effort history."""

    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
        event_publisher: Callable[[dict[str, Any]], None] | None = None,
        history_factory: Callable[[Path, str], ChatHistoryStore] | None = None,
    ) -> None:
        if not generation_id.strip() or not generation_credential.strip():
            raise ValueError("real chat generation identity must not be empty")
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._session_provider = session_provider
        self._event_publisher = event_publisher
        self._history_factory = history_factory
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._executions: dict[str, _Execution] = {}
        self._revision = 0
        self._closed = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if self._event_publisher is not None:
                raise RuntimeError("chat event publisher is already configured")
            self._event_publisher = publisher

    def reserve_send(self, request: Mapping[str, Any]) -> None:
        self._validate_send(request)
        operation_id = str(request["id"])
        with self._changed:
            if self._closed:
                raise RealChatRejection("GENERATION_INVALIDATED", "chat generation is closing")
            if self._session_provider() is None:
                raise RealChatRejection("ASSISTANT_NOT_READY", "Assistant is not ready")
            if operation_id in self._executions:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            if len(self._executions) >= REAL_CHAT_EXECUTION_LIMIT:
                raise RealChatRejection(
                    "CHAT_EXECUTION_LIMIT_EXCEEDED",
                    "another chat interaction is active",
                    retryable=True,
                )
            self._executions[operation_id] = _Execution(operation_id)
            self._revision += 1
            self._changed.notify_all()

    def abandon_send(self, request: Mapping[str, Any]) -> None:
        operation_id = str(request.get("id", ""))
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is not None and not execution.started:
                self._executions.pop(operation_id, None)
                self._revision += 1
                self._changed.notify_all()

    def handle_send(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = self._validate_send(request)
        operation_id = str(request["id"])
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None:
                raise RealChatRejection("CHAT_RESERVATION_MISSING", "chat request was not reserved")
            if execution.started:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            execution.started = True
            self._changed.notify_all()

        try:
            self._publish(request, "chat.started", {"operationId": operation_id})
        except BaseException:  # noqa: BLE001 - transport owner will terminate the generation
            self._drop_execution(operation_id)
            raise
        history_status = "saved"
        history_committed = False
        terminal = "chat.failed"
        terminal_payload: dict[str, Any]
        runtime = None
        try:
            from app.core.runtime_log import suppress_runtime_logs
            from app.llm.context_trimming import (
                MAX_MODEL_CONTEXT_MESSAGES,
                trim_messages_for_model,
            )
            from app.storage.paths import StoragePaths

            execution.cancel.throw_if_cancelled()
            session = self._session_provider()
            if session is None:
                raise _BoundaryFailure("ASSISTANT_NOT_READY", "Assistant is not ready", False)
            character = getattr(session, "character")
            runtime = getattr(session, "runtime", None)
            wait_dependencies = getattr(session, "wait_prompt_dependencies", None)
            if callable(wait_dependencies):
                from app.core.runtime_log import log_event

                dependency_results = wait_dependencies(
                    cancel_checker=execution.cancel.throw_if_cancelled
                )
                for dependency in dependency_results:
                    ready = bool(dependency.get("ready"))
                    log_event(
                        "Context",
                        "Prompt 依赖已就绪" if ready else "Prompt 依赖未就绪，继续降级对话",
                        dependency,
                        severity="info" if ready else "warning",
                        verbosity=1 if ready else 0,
                    )
            history_factory = self._history_factory
            if history_factory is None:
                from app.storage.chat_history import ChatHistoryStore

                history_factory = ChatHistoryStore
            history = history_factory(
                StoragePaths(self._app_root).chat_history_for(str(character.id)),
                str(character.display_name),
            )
            message = str(payload["message"])
            plugin_worker = getattr(session, "plugin_worker", None)
            if plugin_worker is not None:
                try:
                    getattr(plugin_worker, "emit_event")(
                        "message.user",
                        {"role": "user", "characters": len(message)},
                    )
                except Exception:
                    pass
            try:
                history.assert_compatible_append()
            except Exception as exc:
                raise _BoundaryFailure(
                    "HISTORY_COMPATIBILITY_READ_ONLY",
                    "Chat history is read-only because existing data is incompatible",
                    False,
                ) from exc
            try:
                recent = history.load_recent(max(0, MAX_MODEL_CONTEXT_MESSAGES - 1))
            except Exception:
                recent = []
                history_status = "degraded"
            messages = trim_messages_for_model(
                [*_messages_from_history(recent), {"role": "user", "content": message}]
            )
            try:
                history.append("user", message)
                history_committed = True
            except Exception:
                history_status = "degraded"

            execution.cancel.throw_if_cancelled()
            with suppress_runtime_logs():
                result = getattr(session, "pipeline").run_user_message(
                    messages,
                    cancel_checker=execution.cancel.throw_if_cancelled,
                )
            execution.cancel.throw_if_cancelled()
            from app.core_host.tools import pending_actions_from_result

            while True:
                pending = pending_actions_from_result(result)
                unsupported = [
                    action
                    for action in getattr(result, "actions", [])
                    if getattr(action, "type", "") not in {"tool_call", "pending_action", "cancelled_action"}
                ]
                if unsupported or len(pending) > 1:
                    raise _BoundaryFailure(
                        "UNEXPECTED_CHAT_ACTION",
                        "Assistant returned an unsupported action",
                        False,
                    )
                if not pending:
                    break
                coordinator = getattr(session, "tool_actions", None)
                if coordinator is None:
                    raise _BoundaryFailure(
                        "TOOLS_NOT_AVAILABLE",
                        "Assistant tools are not available",
                        False,
                    )
                action = pending[0]
                decision = coordinator.await_decision(
                    action,
                    operation_id=operation_id,
                    publish=lambda payload: self._publish(
                        request, "tool.confirmation.requested", payload
                    ),
                    cancel_checker=execution.cancel.throw_if_cancelled,
                )
                execution.cancel.throw_if_cancelled()
                with suppress_runtime_logs():
                    if decision == "confirm":
                        result = getattr(session, "pipeline").run_confirmed_action(
                            action,
                            cancel_checker=execution.cancel.throw_if_cancelled,
                        )
                    else:
                        result = getattr(session, "pipeline").run_cancelled_action(
                            action,
                            cancel_checker=execution.cancel.throw_if_cancelled,
                        )
            if plugin_worker is not None:
                try:
                    reply_text = str(getattr(getattr(result, "reply", None), "speech", ""))
                    getattr(plugin_worker, "emit_event")(
                        "message.ai",
                        {"role": "assistant", "characters": len(reply_text)},
                    )
                except Exception:
                    pass
            execution.cancel.throw_if_cancelled()
            segments = _project_reply(getattr(result, "reply", None))
            for segment in segments:
                if not segment["text"].strip():
                    continue
                execution.cancel.throw_if_cancelled()
                try:
                    history.append(
                        "assistant",
                        segment["text"],
                        segment["translation"],
                        segment["tone"],
                        segment["portrait"],
                    )
                except Exception:
                    history_status = "degraded"
                    history_committed = False
            execution.cancel.throw_if_cancelled()
            terminal = "chat.completed"
            terminal_payload = {
                "operationId": operation_id,
                "reply": {"segments": segments},
                "historyStatus": history_status,
            }
            if history_committed:
                memory_boundary = getattr(session, "memory_boundary", None)
                note_completed = getattr(memory_boundary, "note_completed_chat", None)
                if callable(note_completed):
                    try:
                        note_completed(history)
                    except Exception:
                        # Curation is best effort and cannot change the unique
                        # terminal outcome of an already completed chat.
                        pass
        except BaseException as error:  # noqa: BLE001 - sanitize at the process boundary
            if _is_operation_cancelled(error):
                terminal = "chat.cancelled"
                terminal_payload = {
                    "operationId": operation_id,
                    "historyStatus": history_status,
                }
            else:
                _safe_diagnostic(error)
                code, message, retryable = _classify_error(error)
                terminal_payload = {
                    "operationId": operation_id,
                    "error": {
                        "code": code,
                        "message": message,
                        "retryable": retryable,
                        "details": {},
                    },
                    "historyStatus": history_status,
                }

        resolved_terminal = self._finish(operation_id, terminal)
        finish_trace = getattr(runtime, "finish_trace_operation", None)
        if callable(finish_trace):
            try:
                finish_trace(
                    operation_id,
                    status={
                        "chat.completed": "completed",
                        "chat.cancelled": "cancelled",
                    }.get(resolved_terminal or terminal, "failed"),
                )
            except Exception:
                pass
        if resolved_terminal is not None:
            if resolved_terminal == "chat.cancelled" and terminal != "chat.cancelled":
                terminal_payload = {
                    "operationId": operation_id,
                    "historyStatus": history_status,
                }
            self._publish(request, resolved_terminal, terminal_payload)
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": True, "operationId": operation_id},
        )

    def handle_cancel(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"operationId"}:
            raise ValueError("chat.cancel payload must contain only operationId")
        operation_id = payload.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("chat.cancel operationId is invalid")
        with self._lock:
            execution = self._executions.get(operation_id)
            accepted = bool(
                execution is not None
                and execution.terminal is None
                and not execution.cancel_requested
                and not self._closed
            )
            if accepted:
                assert execution is not None
                execution.cancel_requested = True
                execution.cancel.cancel()
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": accepted, "operationId": operation_id},
        )

    def handle_tool_decision(self, request: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"actionId"}:
            raise ValueError("tool decision payload must contain only actionId")
        session = self._session_provider()
        coordinator = getattr(session, "tool_actions", None) if session is not None else None
        if coordinator is None:
            raise RealChatRejection("TOOLS_NOT_AVAILABLE", "Assistant tools are not available")
        result = coordinator.decide(payload.get("actionId"), confirm=confirm)
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload=result,
        )

    def snapshot_fields(
        self,
        readiness: str,
        current_character_summary: Mapping[str, Any] | None,
        *,
        base_revision: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            revision = base_revision + self._revision
            active = next(iter(self._executions.values()), None)
            interaction = (
                {
                    "operationId": active.operation_id,
                    "state": "cancelling" if active.cancel_requested else "started",
                }
                if active is not None
                else None
            )
        return {
            "generationId": self._generation_id,
            "revision": revision,
            "readiness": readiness,
            "currentCharacterSummary": (
                dict(current_character_summary) if current_character_summary is not None else None
            ),
            "activeInteractionSummary": interaction,
        }

    def cancel_all(self) -> None:
        with self._lock:
            for execution in self._executions.values():
                execution.cancel_requested = True
                execution.cancel.cancel()

    def close(self) -> None:
        deadline = monotonic() + CHAT_CLOSE_TIMEOUT_SECONDS
        with self._changed:
            if not self._closed:
                self._closed = True
                for execution in self._executions.values():
                    execution.cancel_requested = True
                    execution.cancel.cancel()
            while self._executions and monotonic() < deadline:
                self._changed.wait(timeout=max(0.0, deadline - monotonic()))
            if self._executions:
                raise RuntimeError("CHAT_CLOSE_TIMEOUT")

    def _finish(self, operation_id: str, terminal: str) -> str | None:
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None or execution.terminal is not None:
                return None
            if execution.cancel.is_cancelled() and terminal != "chat.cancelled":
                terminal = "chat.cancelled"
            execution.terminal = terminal
            self._executions.pop(operation_id, None)
            self._revision += 1
            self._changed.notify_all()
            return terminal

    def _drop_execution(self, operation_id: str) -> None:
        with self._changed:
            if self._executions.pop(operation_id, None) is not None:
                self._revision += 1
                self._changed.notify_all()

    def _publish(self, request: Mapping[str, Any], name: str, payload: Mapping[str, Any]) -> None:
        publisher = self._event_publisher
        if publisher is not None:
            publisher(
                event(
                    request,
                    generation_id=self._generation_id,
                    generation_credential=self._generation_credential,
                    name=name,
                    payload=payload,
                )
            )

    def _validate_send(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        # The Router deliberately does not understand generation credentials;
        # the product boundary therefore repeats the transport checks before
        # touching the published Assistant session.
        if request.get("generationId") != self._generation_id:
            raise RealChatRejection("GENERATION_MISMATCH", "chat request is stale")
        supplied_credential = request.get("generationCredential")
        if not isinstance(supplied_credential, str) or not hmac.compare_digest(
            supplied_credential,
            self._generation_credential,
        ):
            raise RealChatRejection(
                "GENERATION_CREDENTIAL_MISMATCH",
                "chat generation credential is invalid",
            )
        if request.get("kind") != "request" or request.get("name") != "chat.send":
            raise RealChatRejection("INVALID_CHAT_REQUEST", "chat request envelope is invalid")
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"message", "operationId"}:
            raise RealChatRejection(
                "INVALID_CHAT_PAYLOAD",
                "chat.send payload must contain only message and operationId",
            )
        message = payload.get("message")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message.encode("utf-8")) > CHAT_MESSAGE_LIMIT
        ):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat message is invalid")
        if payload.get("operationId") != request.get("id"):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat identity is invalid")
        return payload


class _BoundaryFailure(RuntimeError):
    def __init__(self, code: str, public_message: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _messages_from_history(entries: list[ChatHistoryEntry]) -> list[dict[str, str]]:
    return [
        {"role": entry.role, "content": entry.content}
        for entry in entries
        if entry.role in {"user", "assistant"} and entry.content.strip()
    ]


def _project_reply(reply: object) -> list[dict[str, object]]:
    raw_segments = getattr(reply, "segments", None)
    if not isinstance(raw_segments, list):
        raise _BoundaryFailure("INVALID_CHAT_REPLY", "Assistant reply was invalid", False)
    projected: list[dict[str, object]] = []
    for segment in raw_segments:
        values = (
            getattr(segment, "text", None),
            getattr(segment, "translation", None),
            getattr(segment, "tone", None),
            getattr(segment, "portrait", None),
            getattr(segment, "suppress_tts", None),
        )
        if not all(isinstance(value, str) for value in values[:4]) or not isinstance(values[4], bool):
            raise _BoundaryFailure("INVALID_CHAT_REPLY", "Assistant reply was invalid", False)
        projected.append(
            {
                "text": values[0],
                "translation": values[1],
                "tone": values[2],
                "portrait": values[3],
                "suppressTts": values[4],
            }
        )
    return projected


def _classify_error(error: BaseException) -> tuple[str, str, bool]:
    if isinstance(error, _BoundaryFailure):
        return error.code, error.public_message, error.retryable
    from app.llm.api_client import ApiConfigError, ApiRequestError

    if isinstance(error, ApiConfigError):
        return "PROVIDER_CONFIGURATION_INVALID", "Provider configuration is invalid", False
    if isinstance(error, ApiRequestError):
        text = str(error).lower()
        cause: BaseException | None = error
        while cause is not None:
            if isinstance(cause, urllib.error.HTTPError):
                retryable = cause.code == 429 or cause.code >= 500
                return (
                    "PROVIDER_REQUEST_FAILED",
                    _public_provider_http_message(error, cause.code),
                    retryable,
                )
            cause = cause.__cause__
        response_invalid = any(
            marker in text
            for marker in (
                "格式无法解析",
                "invalid json",
                "remained invalid",
                "missing",
                "empty response",
            )
        )
        if response_invalid:
            message = (
                "供应商响应格式无效：返回内容不是有效 JSON。"
                if "格式无法解析" in text or "invalid json" in text
                else "供应商响应格式无效：回复结构不符合协议。"
            )
            return "PROVIDER_RESPONSE_INVALID", message, False
        return "PROVIDER_REQUEST_FAILED", "Provider request failed", True
    return "CHAT_EXECUTION_FAILED", "Chat execution failed", False


_PROVIDER_PUBLIC_FIELDS = ("message", "code", "type", "status")
_PROVIDER_DIAGNOSTIC_LIMIT = 360
_PROVIDER_SENSITIVE_PATTERNS = (
    re.compile(r"\bPRIVATE_[A-Z0-9_]+\b"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|authorization|bearer|token|secret|password|credential)\b"
        r"\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
    re.compile(r"https?://[^\s\])}>,;]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z]:\\[^\s\])}>,;]+"),
    re.compile(r"(?<![\w:])/(?:[^/\s]+/)+[^/\s\])}>,;]+"),
)


def _public_provider_http_message(error: BaseException, status_code: int) -> str:
    payload = _provider_error_payload(str(error), status_code)
    if payload is None:
        return f"API HTTP {status_code}: 供应商请求失败。"

    raw_error = payload.get("error")
    public_source = raw_error if isinstance(raw_error, Mapping) else payload
    public_values: dict[str, str] = {}
    for field in _PROVIDER_PUBLIC_FIELDS:
        value = public_source.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        sanitized = _sanitize_provider_diagnostic(str(value))
        if sanitized:
            public_values[field] = sanitized

    message = public_values.pop("message", "")
    metadata = "; ".join(
        f"{field}: {public_values[field]}"
        for field in _PROVIDER_PUBLIC_FIELDS[1:]
        if field in public_values
    )
    if message and metadata:
        return f"API HTTP {status_code}: {message} ({metadata})"
    if message:
        return f"API HTTP {status_code}: {message}"
    if metadata:
        return f"API HTTP {status_code}: {metadata}"
    return f"API HTTP {status_code}: 供应商请求失败。"


def _provider_error_payload(error_text: str, status_code: int) -> Mapping[str, Any] | None:
    raw_marker = "\n原始响应："
    if raw_marker in error_text:
        candidate = error_text.rsplit(raw_marker, 1)[1].strip()
    else:
        prefix = f"API HTTP {status_code}:"
        candidate = error_text.split(prefix, 1)[1].strip() if prefix in error_text else ""
    if not candidate.startswith("{"):
        return None
    try:
        decoded = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _sanitize_provider_diagnostic(value: str) -> str:
    sanitized = " ".join(value.split())
    for pattern in _PROVIDER_SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    if len(sanitized) > _PROVIDER_DIAGNOSTIC_LIMIT:
        sanitized = sanitized[: _PROVIDER_DIAGNOSTIC_LIMIT - 1].rstrip() + "…"
    return sanitized


def _safe_diagnostic(error: BaseException) -> None:
    try:
        print(f"Real chat failed: {type(error).__name__}", file=sys.stderr)
    except Exception:
        pass


def _is_operation_cancelled(error: BaseException) -> bool:
    from app.core.cancellation import OperationCancelled

    return isinstance(error, OperationCancelled)


__all__ = ["REAL_CHAT_EXECUTION_LIMIT", "RealChatBoundary", "RealChatRejection"]
