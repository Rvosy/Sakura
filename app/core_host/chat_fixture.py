"""Narrow cancellable chat fixture for Runtime v2 WP-2-02.

This module deliberately contains no Assistant, provider, memory, tool or UI
integration.  It only freezes the generation-scoped chat boundary with sleep
and isolated file-read fixtures.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

from .protocol import event, response


CHAT_EXECUTION_LIMIT = 8
CHAT_MESSAGE_LIMIT = 64 * 1024
CHAT_FIXTURE_MAX_DELAY_MS = 30_000
CHAT_CLOSE_TIMEOUT_SECONDS = 3.0
CHAT_EVENT_NAMES = frozenset(
    {"chat.started", "chat.completed", "chat.failed", "chat.cancelled"}
)
_TRANSPORT_FIELDS = frozenset(
    {
        "protocolMajor",
        "protocolMinor",
        "kind",
        "generationId",
        "generationCredential",
        "id",
        "deadlineMs",
        "priority",
        "sequence",
        "requestId",
    }
)
_FIXTURE_TRANSPORT_FIELDS = frozenset(
    {"generationId", "generationCredential", "requestId", "operationId"}
)


@dataclass
class _ChatExecution:
    operation_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False
    terminal: str | None = None


class ChatFixtureBoundary:
    """Own bounded fixture executions and arbitrate exactly one terminal."""

    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        *,
        fixture_root: Path | None = None,
        event_publisher: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not generation_id.strip() or not generation_credential.strip():
            raise ValueError("chat fixture generation identity must not be empty")
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._fixture_root = fixture_root.resolve() if fixture_root is not None else None
        self._event_publisher = event_publisher
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._executions: dict[str, _ChatExecution] = {}
        self._revision = 0
        self._closed = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if self._event_publisher is not None:
                raise RuntimeError("chat event publisher is already configured")
            self._event_publisher = publisher

    def handle_send(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = self._validate_send(request)
        operation_id = str(request["id"])
        with self._lock:
            if self._closed:
                raise RuntimeError("CHAT_GENERATION_INVALIDATED")
            execution = self._executions.get(operation_id)
            if execution is None:
                if len(self._executions) >= CHAT_EXECUTION_LIMIT:
                    raise RuntimeError("CHAT_EXECUTION_LIMIT_EXCEEDED")
                execution = _ChatExecution(operation_id)
                self._executions[operation_id] = execution
                self._revision += 1
            elif execution.started.is_set():
                raise ValueError("duplicate chat operation identity")
            execution.started.set()
            self._changed.notify_all()

        self._publish(request, "chat.started", {"operationId": operation_id})
        terminal = "chat.failed"
        terminal_payload: dict[str, Any]
        try:
            fixture = payload.get("fixture", {"kind": "sleep", "delayMs": 0})
            assert isinstance(fixture, Mapping)
            kind = fixture.get("kind")
            if kind == "sleep":
                delay = int(fixture.get("delayMs", 0)) / 1000
                cancelled = execution.cancel.wait(delay)
            elif kind == "file":
                cancelled = self._run_file_fixture(execution, fixture)
            else:  # validation normally prevents this branch
                raise ValueError("unsupported chat fixture kind")
            if cancelled:
                terminal = "chat.cancelled"
                terminal_payload = {"operationId": operation_id}
            elif fixture.get("outcome", "complete") == "fail":
                terminal_payload = {
                    "operationId": operation_id,
                    "error": {
                        "code": "CHAT_FIXTURE_FAILED",
                        "message": "chat fixture failed",
                        "retryable": False,
                        "details": {},
                    },
                }
            else:
                terminal = "chat.completed"
                terminal_payload = {
                    "operationId": operation_id,
                    "reply": str(payload["message"]),
                }
        except BaseException:  # noqa: BLE001 - publish only a stable boundary error
            terminal_payload = {
                "operationId": operation_id,
                "error": {
                    "code": "CHAT_FIXTURE_FAILED",
                    "message": "chat fixture failed",
                    "retryable": False,
                    "details": {},
                },
            }

        won = self._finish(operation_id, terminal)
        if won:
            self._publish(request, terminal, terminal_payload)
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": True, "operationId": operation_id},
        )

    def reserve_send(self, request: Mapping[str, Any]) -> None:
        """Reserve identity in reader order before a fixture worker starts."""
        self._validate_send(request)
        operation_id = str(request["id"])
        with self._lock:
            if self._closed:
                raise RuntimeError("CHAT_GENERATION_INVALIDATED")
            if operation_id in self._executions:
                raise ValueError("duplicate chat operation identity")
            if len(self._executions) >= CHAT_EXECUTION_LIMIT:
                raise RuntimeError("CHAT_EXECUTION_LIMIT_EXCEEDED")
            self._executions[operation_id] = _ChatExecution(operation_id)
            self._revision += 1

    def abandon_send(self, request: Mapping[str, Any]) -> None:
        operation_id = str(request.get("id", ""))
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is not None and not execution.started.is_set():
                self._executions.pop(operation_id, None)
                self._revision += 1
                self._changed.notify_all()

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
                execution.cancel.set()
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": accepted, "operationId": operation_id},
        )

    def active_interaction_summary(self) -> dict[str, Any] | None:
        with self._lock:
            active = next(iter(self._executions.values()), None)
            if active is None:
                return None
            return {
                "operationId": active.operation_id,
                "state": "cancelling" if active.cancel_requested else "started",
            }

    def snapshot_fields(
        self,
        readiness: str,
        current_character_summary: Mapping[str, Any] | None,
        *,
        base_revision: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            revision = base_revision + self._revision
        return {
            "generationId": self._generation_id,
            "revision": revision,
            "readiness": readiness,
            "currentCharacterSummary": (
                dict(current_character_summary)
                if current_character_summary is not None
                else None
            ),
            "activeInteractionSummary": self.active_interaction_summary(),
        }

    def wait_started(self, operation_id: str, timeout: float) -> bool:
        deadline = monotonic() + timeout
        with self._changed:
            while monotonic() < deadline:
                execution = self._executions.get(operation_id)
                if execution is not None and execution.started.is_set():
                    return True
                self._changed.wait(timeout=max(0.0, deadline - monotonic()))
        return False

    def close(self) -> None:
        deadline = monotonic() + CHAT_CLOSE_TIMEOUT_SECONDS
        with self._changed:
            if not self._closed:
                self._closed = True
                for execution in self._executions.values():
                    execution.cancel.set()
            while self._executions and monotonic() < deadline:
                self._changed.wait(timeout=max(0.0, deadline - monotonic()))
            if self._executions:
                raise RuntimeError("CHAT_CLOSE_TIMEOUT")

    def cancel_all(self) -> None:
        """Signal every fixture without waiting; Router uses this before joins."""
        with self._lock:
            for execution in self._executions.values():
                execution.cancel.set()

    def _finish(self, operation_id: str, terminal: str) -> bool:
        if terminal not in CHAT_EVENT_NAMES - {"chat.started"}:
            raise ValueError("invalid chat terminal")
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None or execution.terminal is not None:
                return False
            execution.terminal = terminal
            self._executions.pop(operation_id, None)
            self._revision += 1
            self._changed.notify_all()
            return True

    def _publish(self, request: Mapping[str, Any], name: str, payload: Mapping[str, Any]) -> None:
        publisher = self._event_publisher
        if publisher is None:
            return
        publisher(
            event(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                name=name,
                payload=payload,
            )
        )

    def _run_file_fixture(self, execution: _ChatExecution, fixture: Mapping[str, Any]) -> bool:
        if self._fixture_root is None:
            raise ValueError("file fixture root is unavailable")
        name = fixture.get("name")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("file fixture name is invalid")
        path = (self._fixture_root / name).resolve()
        if path.parent != self._fixture_root:
            raise ValueError("file fixture escaped its isolated root")
        with path.open("rb") as stream:
            while stream.read(64 * 1024):
                if execution.cancel.is_set():
                    return True
        return execution.cancel.is_set()

    @staticmethod
    def _validate_send(request: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or not {"message"} <= set(payload):
            raise ValueError("chat.send payload must contain message")
        if set(payload) - {"message", "fixture", "operationId"}:
            raise ValueError("chat.send payload fields are invalid")
        if set(payload) & _TRANSPORT_FIELDS:
            raise ValueError("chat.send payload contains a transport field")
        message = payload.get("message")
        if not isinstance(message, str) or not message or len(message.encode("utf-8")) > CHAT_MESSAGE_LIMIT:
            raise ValueError("chat.send message is invalid")
        operation_id = payload.get("operationId")
        if operation_id != request.get("id"):
            raise ValueError("chat.send operationId did not match request identity")
        fixture = payload.get("fixture")
        if fixture is not None:
            if not isinstance(fixture, Mapping) or set(fixture) - {
                "kind",
                "delayMs",
                "outcome",
                "name",
            }:
                raise ValueError("chat fixture fields are invalid")
            if set(fixture) & _FIXTURE_TRANSPORT_FIELDS:
                raise ValueError("chat fixture contains a transport field")
            if fixture.get("kind") not in {"sleep", "file"}:
                raise ValueError("chat fixture kind is invalid")
            delay = fixture.get("delayMs", 0)
            if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= CHAT_FIXTURE_MAX_DELAY_MS:
                raise ValueError("chat fixture delay is invalid")
            if fixture.get("outcome", "complete") not in {"complete", "fail"}:
                raise ValueError("chat fixture outcome is invalid")
        return payload


__all__ = [
    "CHAT_EVENT_NAMES",
    "CHAT_EXECUTION_LIMIT",
    "CHAT_MESSAGE_LIMIT",
    "ChatFixtureBoundary",
]
