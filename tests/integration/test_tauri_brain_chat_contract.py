from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.actions import AgentAction, AgentProgress, AgentResult, PendingToolAction
from app.brain_host.application import BrainHostApplication, BrainHostConfig
from app.brain_host.errors import BrainHostError
from app.brain_host.pending_actions import PendingActionStore
from app.brain_host.protocol import FrameDecoder
from app.brain_host.transport import FramedTransport
from app.core.assistant_service import AssistantApplication
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.chat_history import ChatHistoryStore


ROOT = Path(__file__).resolve().parents[2]


def _module_url(relative: str) -> str:
    return (ROOT / relative).resolve().as_uri()


def _run_node(source: str) -> dict[str, Any]:
    result = subprocess.run(
        ["node", "--experimental-default-type=module", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


class EventCollector:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []
        self._condition = threading.Condition()

    def __call__(self, name: str, payload: dict[str, Any]) -> None:
        with self._condition:
            self.items.append((name, payload))
            self._condition.notify_all()

    def wait_for(self, name: str, *, timeout: float = 2) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event_name, payload in self.items:
                    if event_name == name:
                        return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"timed out waiting for {name}: {self.items!r}")
                self._condition.wait(remaining)


class ContractPipeline:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, Any]]] = []
        self.confirmed: list[PendingToolAction] = []
        self.rejected: list[PendingToolAction] = []

    def run_user_message(
        self,
        messages: list[dict[str, Any]],
        *,
        progress_callback=None,  # type: ignore[no-untyped-def]
        cancel_checker=None,  # type: ignore[no-untyped-def]
        **_kwargs: Any,
    ) -> AgentResult:
        self.messages.append([dict(message) for message in messages])
        if cancel_checker is not None:
            cancel_checker()
        if progress_callback is not None:
            progress_callback(
                AgentProgress(
                    ChatReply(
                        [
                            ChatSegment(
                                ja="調べているよ。",
                                zh="正在查询。",
                                tone="专注",
                                portrait="thinking",
                                suppress_tts=True,
                            )
                        ]
                    ),
                    stage="tool_running",
                    metadata={"tool": "demo.lookup"},
                )
            )
        pending = PendingToolAction(
            "demo.lookup",
            {"query": "原始参数"},
            "需要读取演示数据",
            id="action-demo",
            tool_call_id="internal-call-id",
            continuation_messages=[{"role": "assistant", "content": "internal context"}],
        )
        return AgentResult(
            ChatReply(
                [
                    ChatSegment(
                        ja="確認してもいい？",
                        zh="可以确认一下吗？",
                        tone="温和",
                        portrait="smile",
                    ),
                    ChatSegment(
                        ja="待っているね。",
                        zh="我等你。",
                        tone="期待",
                        portrait="default",
                        suppress_tts=True,
                    ),
                ]
            ),
            actions=[AgentAction("pending_action", pending.to_dict(include_context=True))],
        )

    def run_confirmed_action(
        self,
        action: PendingToolAction,
        *,
        cancel_checker=None,  # type: ignore[no-untyped-def]
        **_kwargs: Any,
    ) -> AgentResult:
        if cancel_checker is not None:
            cancel_checker()
        self.confirmed.append(action)
        return AgentResult(ChatReply([ChatSegment(ja="完了したよ。", zh="已经完成。")]))

    def run_cancelled_action(
        self,
        action: PendingToolAction,
        *,
        cancel_checker=None,  # type: ignore[no-untyped-def]
        **_kwargs: Any,
    ) -> AgentResult:
        if cancel_checker is not None:
            cancel_checker()
        self.rejected.append(action)
        return AgentResult(ChatReply([ChatSegment(ja="今回はやめよう。", zh="这次先不执行。")]))


class LateResultPipeline:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run_user_message(self, _messages, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        self.entered.set()
        assert self.release.wait(2)
        return AgentResult(ChatReply([ChatSegment(ja="遅い結果", zh="迟到结果")]))


class FailingPipeline:
    def run_user_message(self, _messages, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        raise TimeoutError("provider timeout with internal details")


def _application(
    tmp_path: Path,
    pipeline: object,
    *,
    pending_actions: PendingActionStore | None = None,
) -> tuple[BrainHostApplication, ChatHistoryStore, EventCollector]:
    config = BrainHostConfig(tmp_path, "session-chat", "credential-chat", 1)
    history = ChatHistoryStore(tmp_path / "data" / "chat_history.jsonl", "Demo")
    application = BrainHostApplication(config)
    application.context = SimpleNamespace(history_store=history)
    application.assistant = AssistantApplication(
        pipeline,  # type: ignore[arg-type]
        session_id=config.session_id,
        pending_actions=pending_actions,
    )
    application.state = "ready"
    application.startup = {}
    events = EventCollector()
    application.set_event_sink(events)
    return application, history, events


def test_chat_send_emits_progress_reply_confirmation_and_writes_compatible_history(
    tmp_path: Path,
) -> None:
    pipeline = ContractPipeline()
    application, history, events = _application(tmp_path, pipeline)

    accepted = application.handle_request("chat.send", {"text": "请查询"})

    assert accepted["version"] == 1
    assert accepted["interactionId"].startswith("interaction-")
    assert accepted["requestId"]
    progress = events.wait_for("chat.progress")
    reply = events.wait_for("chat.reply")
    confirmation = events.wait_for("chat.confirmation_requested")

    assert progress == {
        "version": 1,
        "interactionId": accepted["interactionId"],
        "requestId": accepted["requestId"],
        "stage": "tool_running",
        "reply": {
            "version": 1,
            "text": "調べているよ。",
            "translation": "正在查询。",
            "segments": [
                {
                    "ja": "調べているよ。",
                    "zh": "正在查询。",
                    "tone": "专注",
                    "portrait": "thinking",
                    "suppressTts": True,
                }
            ],
        },
        "metadata": {"tool": "demo.lookup"},
    }
    assert reply["reply"]["segments"] == [
        {
            "ja": "確認してもいい？",
            "zh": "可以确认一下吗？",
            "tone": "温和",
            "portrait": "smile",
            "suppressTts": False,
        },
        {
            "ja": "待っているね。",
            "zh": "我等你。",
            "tone": "期待",
            "portrait": "default",
            "suppressTts": True,
        },
    ]
    action_dto = dict(confirmation["action"])
    created_at = action_dto.pop("createdAt")
    assert isinstance(created_at, str) and created_at
    assert action_dto == {
        "version": 1,
        "id": "action-demo",
        "toolName": "demo.lookup",
        "arguments": {"query": "原始参数"},
        "reason": "需要读取演示数据",
    }
    assert "continuationMessages" not in json.dumps(confirmation, ensure_ascii=False)
    assert "internal context" not in json.dumps(confirmation, ensure_ascii=False)
    assert "internal-call-id" not in json.dumps(confirmation, ensure_ascii=False)
    assert pipeline.messages == [[{"role": "user", "content": "请查询"}]]
    assert [(entry.role, entry.content) for entry in history.load()] == [
        ("user", "请查询"),
        ("assistant", "調べているよ。"),
        ("assistant", "確認してもいい？"),
        ("assistant", "待っているね。"),
    ]

    application.handle_request(
        "chat.confirm_action",
        {"action_id": "action-demo", "arguments": {"query": "篡改参数"}},
    )
    confirmed_reply = events.wait_for("chat.reply")
    deadline = time.monotonic() + 2
    while confirmed_reply["interactionId"] == accepted["interactionId"]:
        matching = [payload for name, payload in events.items if name == "chat.reply"]
        if len(matching) >= 2:
            confirmed_reply = matching[-1]
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert confirmed_reply["reply"]["segments"][0]["zh"] == "已经完成。"
    assert pipeline.confirmed[0].arguments == {"query": "原始参数"}
    assert pipeline.confirmed[0].continuation_messages == [
        {"role": "assistant", "content": "internal context"}
    ]

    with pytest.raises(BrainHostError) as duplicate:
        application.handle_request("chat.confirm_action", {"action_id": "action-demo"})
    assert duplicate.value.code == "ACTION_NOT_FOUND"
    application.handle_request("system.shutdown", {})


def test_chat_cancel_emits_cancelled_and_suppresses_late_reply(tmp_path: Path) -> None:
    pipeline = LateResultPipeline()
    application, history, events = _application(tmp_path, pipeline)
    accepted = application.handle_request("chat.send", {"text": "取消它"})
    assert pipeline.entered.wait(1)

    cancelled = application.handle_request(
        "chat.cancel",
        {"interaction_id": accepted["interactionId"]},
    )
    assert cancelled == {
        "version": 1,
        "interactionId": accepted["interactionId"],
        "cancelled": True,
    }
    pipeline.release.set()
    payload = events.wait_for("chat.cancelled")
    assert payload["interactionId"] == accepted["interactionId"]
    time.sleep(0.05)
    assert not any(name == "chat.reply" for name, _payload in events.items)
    assert [(entry.role, entry.content) for entry in history.load()] == [("user", "取消它")]
    application.handle_request("system.shutdown", {})


def test_chat_errors_use_stable_user_facing_dto(tmp_path: Path) -> None:
    application, history, events = _application(tmp_path, FailingPipeline())

    accepted = application.handle_request("chat.send", {"text": "触发错误"})
    failure = events.wait_for("chat.error")

    assert failure == {
        "version": 1,
        "interactionId": accepted["interactionId"],
        "requestId": accepted["requestId"],
        "error": {
            "code": "CHAT_REQUEST_FAILED",
            "message": "聊天请求没有成功完成，请检查网络、代理和模型配置后重试。",
            "retryable": True,
            "details": {"errorType": "TimeoutError"},
        },
    }
    assert "internal details" not in json.dumps(failure, ensure_ascii=False)
    assert [(entry.role, entry.content) for entry in history.load()] == [
        ("user", "触发错误"),
        ("error", "provider timeout with internal details"),
    ]
    application.handle_request("system.shutdown", {})


def test_action_ids_are_bound_to_the_current_session_and_rejection_uses_saved_action(
    tmp_path: Path,
) -> None:
    store = PendingActionStore()
    foreign = PendingToolAction("demo.foreign", {"safe": True}, "foreign", id="foreign-action")
    current = PendingToolAction("demo.reject", {"value": 7}, "reject", id="reject-action")
    store.add(foreign, session_id="another-session", interaction_id="old-interaction")
    store.add(current, session_id="session-chat", interaction_id="current-interaction")
    pipeline = ContractPipeline()
    application, _history, events = _application(tmp_path, pipeline, pending_actions=store)

    with pytest.raises(BrainHostError) as cross_session:
        application.handle_request("chat.confirm_action", {"action_id": "foreign-action"})
    assert cross_session.value.code == "ACTION_NOT_FOUND"

    accepted = application.handle_request(
        "chat.reject_action",
        {"action_id": "reject-action", "arguments": {"value": 999}},
    )
    reply = events.wait_for("chat.reply")
    assert reply["interactionId"] == accepted["interactionId"]
    assert reply["reply"]["segments"][0]["zh"] == "这次先不执行。"
    assert pipeline.rejected[0].arguments == {"value": 7}
    application.handle_request("system.shutdown", {})


def test_framed_transport_serializes_concurrent_event_and_response_writes() -> None:
    class SlowWriter:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, payload: bytes) -> int:
            for byte in payload:
                self.data.append(byte)
                time.sleep(0)
            return len(payload)

        def flush(self) -> None:
            return None

    writer = SlowWriter()
    transport = FramedTransport(SimpleNamespace(read=lambda _size: b""), writer)  # type: ignore[arg-type]
    barrier = threading.Barrier(3)

    def send(kind: str, message_id: str, sequence: int) -> None:
        barrier.wait()
        transport.send(
            {
                "protocol": 1,
                "kind": kind,
                "id": message_id,
                "session_id": "session-chat",
                "sequence": sequence,
                **(
                    {"method": "chat.progress", "payload": {}}
                    if kind == "event"
                    else {"ok": True, "payload": {}}
                ),
            }
        )

    threads = [
        threading.Thread(target=send, args=("response", "response-1", 1)),
        threading.Thread(target=send, args=("event", "event-1", 2)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    decoder = FrameDecoder()
    messages = decoder.feed(bytes(writer.data))
    decoder.finish()
    assert {message["id"] for message in messages} == {"response-1", "event-1"}


def test_tauri_chat_controller_routes_events_cancel_and_action_ids_only() -> None:
    payload = _run_node(
        f"""
import {{ createPetStore }} from {json.dumps(_module_url('desktop/frontend/core/store.js'))};
import {{ ChatController }} from {json.dumps(_module_url('desktop/frontend/chat/chat_controller.js'))};
const store = createPetStore();
store.setBootstrap({{ character: {{ id: "demo" }} }});
const calls = [];
const shown = [];
const statuses = [];
const confirmation = {{
  shown: [],
  hidden: 0,
  show(action) {{ this.shown.push(action); }},
  hide() {{ this.hidden += 1; }},
  setBusy(value) {{ this.busy = value; }},
}};
const invoke = async (command, args) => {{
  calls.push([command, args]);
  return {{ version: 1, interactionId: `interaction-${{calls.length}}`, requestId: `request-${{calls.length}}` }};
}};
const controller = new ChatController({{
  store,
  invoke,
  subtitleController: {{
    showSegments(segments) {{ shown.push(segments); }},
    setText(text) {{ shown.push(text); }},
    cancel(text) {{ shown.push(text); }},
  }},
  confirmationView: confirmation,
  setStatus: (message, kind) => statuses.push([message, kind]),
}});
await controller.send("hello");
controller.handleProgress({{
  interactionId: "interaction-1",
  stage: "thinking",
  reply: {{ segments: [{{ ja: "考え中", zh: "思考中" }}] }},
}});
controller.handleReply({{
  interactionId: "interaction-1",
  reply: {{ segments: [{{ ja: "完了", zh: "完成" }}] }},
  pendingActions: [{{ id: "action-1", toolName: "demo.tool", arguments: {{ safe: true }} }}],
}});
controller.handleConfirmation({{
  interactionId: "interaction-1",
  action: {{ id: "action-1", toolName: "demo.tool", arguments: {{ safe: true }} }},
}});
await controller.confirm("action-1");
await controller.cancel();
controller.handleCancelled({{ interactionId: "interaction-2" }});
await controller.reject("action-2");
controller.handleError({{
  interactionId: "interaction-4",
  error: {{ code: "CHAT_REQUEST_FAILED", message: "可理解错误" }},
}});
console.log(JSON.stringify({{
  calls,
  state: store.getState().interaction,
  shown,
  confirmation,
  statuses,
}}));
"""
    )

    assert payload["calls"] == [
        ["chat_send", {"text": "hello"}],
        ["chat_confirm_action", {"actionId": "action-1"}],
        ["chat_cancel", {"interactionId": "interaction-2"}],
        ["chat_reject_action", {"actionId": "action-2"}],
    ]
    assert payload["confirmation"]["shown"][-1]["id"] == "action-1"
    serialized_calls = json.dumps(payload["calls"], ensure_ascii=False)
    assert "arguments" not in serialized_calls
    assert payload["statuses"][-1] == ["可理解错误", "error"]


def test_tauri_chat_modules_markup_and_rust_commands_are_wired() -> None:
    frontend = ROOT / "desktop" / "frontend"
    chat_controller = frontend / "chat" / "chat_controller.js"
    confirmation_view = frontend / "chat" / "confirmation_view.js"
    html = (frontend / "index.html").read_text(encoding="utf-8")
    app_source = (frontend / "app.js").read_text(encoding="utf-8")
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )
    rust_lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    assert chat_controller.is_file()
    assert confirmation_view.is_file()
    for element_id in (
        "tool-confirmation",
        "tool-confirmation-name",
        "tool-confirmation-reason",
        "tool-confirmation-arguments",
        "confirm-tool-action",
        "reject-tool-action",
    ):
        assert f'id="{element_id}"' in html
    assert 'from "./chat/chat_controller.js"' in app_source
    assert 'from "./chat/confirmation_view.js"' in app_source
    for event_name in (
        "sakura://chat-progress",
        "sakura://chat-reply",
        "sakura://chat-cancelled",
        "sakura://chat-error",
        "sakura://chat-confirmation-requested",
    ):
        assert event_name in app_source
    for command in ("chat_send", "chat_cancel", "chat_confirm_action", "chat_reject_action"):
        assert f"fn {command}" in rust_state
        assert f"app_state::{command}" in rust_lib
