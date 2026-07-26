from __future__ import annotations

import threading
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.agent.actions import AgentAction, AgentResult
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.core_host.real_chat import _classify_error
from app.llm.api_client import ApiRequestError
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.chat_history import ChatHistoryEntry


GENERATION_ID = "00000000-0000-4000-8000-000000003002"
CREDENTIAL = "30" * 16


@dataclass
class _Character:
    id: str = "sakura"
    display_name: str = "Sakura"


@dataclass
class _Session:
    pipeline: Any
    character: _Character = field(default_factory=_Character)


class _History:
    def __init__(self, entries: list[ChatHistoryEntry] | None = None) -> None:
        self.entries = list(entries or [])
        self.appended: list[tuple[object, ...]] = []
        self.fail_load = False
        self.fail_roles: set[str] = set()

    def load_recent(self, limit: int) -> list[ChatHistoryEntry]:
        if self.fail_load:
            raise OSError("PRIVATE_PATH")
        return self.entries[-limit:]

    def append(self, role: str, *values: object) -> None:
        if role in self.fail_roles:
            raise OSError("PRIVATE_DISK_ERROR")
        self.appended.append((role, *values))


class _Pipeline:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.messages: list[dict[str, object]] = []

    def run_user_message(self, messages: list[dict[str, object]], **_kwargs: object) -> AgentResult:
        self.messages = messages
        return self.result


def _request(operation_id: str, message: str = "hello") -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": CREDENTIAL,
        "id": operation_id,
        "name": "chat.send",
        "payload": {"message": message, "operationId": operation_id},
        "deadlineMs": 3000,
        "priority": "interactive",
    }


def _boundary(
    tmp_path: Path,
    session: _Session | None,
    history: _History,
    published: list[dict[str, object]],
) -> RealChatBoundary:
    return RealChatBoundary(
        GENERATION_ID,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=published.append,
        history_factory=lambda _path, _name: history,  # type: ignore[arg-type]
    )


def test_real_chat_projects_exact_segments_and_writes_bounded_history(tmp_path: Path) -> None:
    history = _History(
        [
            ChatHistoryEntry(str(index), "user" if index % 2 == 0 else "assistant", str(index))
            for index in range(30)
        ]
    )
    pipeline = _Pipeline(
        AgentResult(
            ChatReply(
                [
                    ChatSegment("こんにちは", "中性", "你好", "neutral"),
                    ChatSegment("", "中性", "", "", suppress_tts=True),
                ]
            ),
            _debug={"secret": "must-not-leak"},
        )
    )
    published: list[dict[str, object]] = []
    boundary = _boundary(tmp_path, _Session(pipeline), history, published)
    request = _request("chat-1", "new message")

    boundary.reserve_send(request)
    accepted = boundary.handle_send(request)

    assert accepted["payload"] == {"accepted": True, "operationId": "chat-1"}
    assert [item["name"] for item in published] == ["chat.started", "chat.completed"]
    terminal = published[-1]["payload"]
    assert terminal == {
        "operationId": "chat-1",
        "reply": {
            "segments": [
                {
                    "text": "こんにちは",
                    "translation": "你好",
                    "tone": "中性",
                    "portrait": "neutral",
                    "suppressTts": False,
                },
                {
                    "text": "",
                    "translation": "",
                    "tone": "中性",
                    "portrait": "",
                    "suppressTts": True,
                },
            ]
        },
        "historyStatus": "saved",
    }
    assert len(pipeline.messages) == 24
    assert pipeline.messages[-1] == {"role": "user", "content": "new message"}
    assert history.appended == [
        ("user", "new message"),
        ("assistant", "こんにちは", "你好", "中性", "neutral"),
    ]
    assert "secret" not in repr(terminal)


@pytest.mark.parametrize("failure", ["load", "user", "assistant"])
def test_history_fault_degrades_terminal_without_changing_success(
    tmp_path: Path, failure: str
) -> None:
    history = _History()
    if failure == "load":
        history.fail_load = True
    else:
        history.fail_roles.add(failure)
    pipeline = _Pipeline(AgentResult(ChatReply([ChatSegment("ok")])))
    published: list[dict[str, object]] = []
    boundary = _boundary(tmp_path, _Session(pipeline), history, published)
    request = _request(f"chat-{failure}")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert published[-1]["name"] == "chat.completed"
    assert published[-1]["payload"]["historyStatus"] == "degraded"


def test_action_is_a_sanitized_boundary_failure(tmp_path: Path) -> None:
    history = _History()
    pipeline = _Pipeline(
        AgentResult(
            ChatReply([ChatSegment("should not escape")]),
            actions=[AgentAction("PRIVATE_ACTION", {"secret": "value"})],
        )
    )
    published: list[dict[str, object]] = []
    boundary = _boundary(tmp_path, _Session(pipeline), history, published)
    request = _request("chat-action")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    terminal = published[-1]
    assert terminal["name"] == "chat.failed"
    assert terminal["payload"]["error"] == {
        "code": "UNEXPECTED_CHAT_ACTION",
        "message": "Assistant returned an unsupported action",
        "retryable": False,
        "details": {},
    }
    assert "PRIVATE_ACTION" not in repr(terminal)
    assert "secret" not in repr(terminal)


def test_cancel_wins_and_duplicate_cancel_is_rejected(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingPipeline:
        def run_user_message(self, _messages: object, *, cancel_checker: Any) -> AgentResult:
            entered.set()
            release.wait(1)
            cancel_checker()
            raise AssertionError("cancel checker must raise")

    history = _History()
    published: list[dict[str, object]] = []
    boundary = _boundary(tmp_path, _Session(BlockingPipeline()), history, published)
    request = _request("chat-cancel")
    boundary.reserve_send(request)
    worker = threading.Thread(target=lambda: boundary.handle_send(request))
    worker.start()
    assert entered.wait(1)

    first = boundary.handle_cancel(
        {**_request("cancel-1"), "payload": {"operationId": "chat-cancel"}}
    )
    second = boundary.handle_cancel(
        {**_request("cancel-2"), "payload": {"operationId": "chat-cancel"}}
    )
    release.set()
    worker.join(1)

    assert first["payload"]["accepted"] is True
    assert second["payload"]["accepted"] is False
    assert [item["name"] for item in published] == ["chat.started", "chat.cancelled"]
    assert published[-1]["payload"]["historyStatus"] == "saved"


def test_queued_cancel_still_publishes_started_then_one_cancelled(tmp_path: Path) -> None:
    pipeline = _Pipeline(AgentResult(ChatReply([ChatSegment("must not run")])))
    published: list[dict[str, object]] = []
    boundary = _boundary(tmp_path, _Session(pipeline), _History(), published)
    request = _request("chat-queued")
    boundary.reserve_send(request)

    cancelled = boundary.handle_cancel(
        {**_request("cancel-queued"), "payload": {"operationId": "chat-queued"}}
    )
    boundary.handle_send(request)

    assert cancelled["payload"]["accepted"] is True
    assert [item["name"] for item in published] == ["chat.started", "chat.cancelled"]
    assert pipeline.messages == []
    assert boundary.snapshot_fields("ready", None)["activeInteractionSummary"] is None


def test_single_active_interaction_and_publisher_failure_release_reservation(
    tmp_path: Path,
) -> None:
    pipeline = _Pipeline(AgentResult(ChatReply([ChatSegment("ok")])))
    session = _Session(pipeline)
    first = _request("chat-first")
    second = _request("chat-second")
    boundary = _boundary(tmp_path, session, _History(), [])
    boundary.reserve_send(first)
    with pytest.raises(RealChatRejection, match="CHAT_EXECUTION_LIMIT_EXCEEDED") as raised:
        boundary.reserve_send(second)
    assert raised.value.retryable is True
    boundary.abandon_send(first)
    boundary.reserve_send(second)
    boundary.abandon_send(second)

    def fail_publish(_message: dict[str, object]) -> None:
        raise OSError("PRIVATE_TRANSPORT")

    failing = RealChatBoundary(
        GENERATION_ID,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=fail_publish,
        history_factory=lambda _path, _name: _History(),  # type: ignore[arg-type]
    )
    failing.reserve_send(first)
    with pytest.raises(OSError, match="PRIVATE_TRANSPORT"):
        failing.handle_send(first)
    assert failing.snapshot_fields("ready", None)["activeInteractionSummary"] is None
    failing.close()


def test_not_ready_and_caller_owned_fields_fail_closed(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path, None, _History(), [])
    with pytest.raises(RealChatRejection, match="ASSISTANT_NOT_READY"):
        boundary.reserve_send(_request("not-ready"))

    invalid = _request("invalid")
    invalid["payload"] = {
        "message": "hello",
        "operationId": "invalid",
        "history": [],
    }
    with pytest.raises(RealChatRejection, match="INVALID_CHAT_PAYLOAD"):
        boundary.reserve_send(invalid)

    with pytest.raises(RealChatRejection, match="INVALID_CHAT_PAYLOAD"):
        boundary.reserve_send(_request("blank", "   "))

    stale = _request("stale")
    stale["generationId"] = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(RealChatRejection, match="GENERATION_MISMATCH"):
        boundary.reserve_send(stale)

    forged = _request("forged")
    forged["generationCredential"] = "ff" * 16
    with pytest.raises(RealChatRejection, match="GENERATION_CREDENTIAL_MISMATCH"):
        boundary.reserve_send(forged)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (429, True), (500, True), (503, True)],
)
def test_http_failure_retryability_is_stable(status: int, retryable: bool) -> None:
    cause = urllib.error.HTTPError("https://provider.invalid", status, "private", {}, None)
    try:
        raise ApiRequestError("PRIVATE_PROVIDER_BODY") from cause
    except ApiRequestError as error:
        assert _classify_error(error) == (
            "PROVIDER_REQUEST_FAILED",
            "Provider request failed",
            retryable,
        )


def test_timeout_failure_is_retryable_without_exposing_exception_text() -> None:
    try:
        raise ApiRequestError("PRIVATE_TIMEOUT_DETAILS") from TimeoutError("PRIVATE_SOCKET")
    except ApiRequestError as error:
        assert _classify_error(error) == (
            "PROVIDER_REQUEST_FAILED",
            "Provider request failed",
            True,
        )
