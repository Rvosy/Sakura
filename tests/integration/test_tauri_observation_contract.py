from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.actions import AgentResult
from app.agent.screen_awareness import ScreenAwarenessSettings
from app.agent.screen_observation import build_screen_observation_from_private_resource
from app.brain_host.application import BrainHostApplication, BrainHostConfig
from app.brain_host.errors import BrainHostError
from app.brain_host.scheduler import PeriodicScheduler
from app.core.assistant_service import AssistantApplication
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.chat_history import ChatHistoryStore


ROOT = Path(__file__).resolve().parents[2]
JPEG_BYTES = b"\xff\xd8\xff\xe0sakura-observation\xff\xd9"


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

    def wait_for(
        self,
        name: str,
        *,
        predicate=lambda _payload: True,  # type: ignore[no-untyped-def]
        timeout: float = 2,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event_name, payload in self.items:
                    if event_name == name and predicate(payload):
                        return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"timed out waiting for {name}: {self.items!r}")
                self._condition.wait(remaining)


class ObservationPipeline:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, Any]]] = []
        self.events: list[Any] = []

    def run_user_message(self, messages, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        return AgentResult(ChatReply([ChatSegment(ja="見えたよ。", zh="我看到了。")]))

    def run_event(self, event, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        self.events.append(event)
        if event.type == "reminder_due":
            return AgentResult(ChatReply([ChatSegment(ja="時間だよ。", zh="到时间了。")]))
        return AgentResult(ChatReply([ChatSegment(ja="画面を見たよ。", zh="我留意了一下屏幕。")]))


class BlockingPipeline(ObservationPipeline):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run_user_message(self, messages, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        self.entered.set()
        assert self.release.wait(2)
        return AgentResult(ChatReply([ChatSegment(ja="終わったよ。", zh="完成了。")]))


class ReminderStore:
    def __init__(self) -> None:
        self.due = [
            {
                "id": "reminder-1",
                "text": "喝水",
                "trigger_at": "2026-07-14T12:00:00+08:00",
            }
        ]
        self.completed: list[str] = []

    def due_reminders(self) -> list[dict[str, Any]]:
        return list(self.due)

    def mark_completed(self, reminder_id: str) -> dict[str, Any]:
        self.completed.append(reminder_id)
        self.due = [item for item in self.due if item["id"] != reminder_id]
        return {"id": reminder_id}


def _private_resource(path: Path, *, width: int = 320, height: int = 180) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG_BYTES)
    return {
        "path": str(path),
        "mimeType": "image/jpeg",
        "width": width,
        "height": height,
        "capturedAt": "2026-07-14T12:00:00+08:00",
        "screenName": "DISPLAY1",
    }


def _application(
    tmp_path: Path,
    pipeline: ObservationPipeline,
    *,
    reminders: ReminderStore | None = None,
) -> tuple[BrainHostApplication, ChatHistoryStore, EventCollector]:
    config = BrainHostConfig(tmp_path, "session-observation", "credential-observation", 1)
    history = ChatHistoryStore(tmp_path / "data" / "chat_history.jsonl", "Demo")
    context = SimpleNamespace(
        history_store=history,
        reminder_store=reminders or ReminderStore(),
        screen_awareness_settings=ScreenAwarenessSettings(
            enabled=True,
            screen_context_enabled=True,
            check_interval_minutes=2,
            cooldown_minutes=10,
        ),
        visual_observation_store=None,
    )
    application = BrainHostApplication(config)
    application.context = context
    application.assistant = AssistantApplication(pipeline, session_id=config.session_id)
    application.scheduler = PeriodicScheduler()
    application.state = "ready"
    application.startup = {}
    events = EventCollector()
    application.set_event_sink(events)
    application._configure_screen_observation_runtime()
    application._last_user_activity_at = time.monotonic() - 1_000
    application.sync_scheduler_jobs(start=False)
    return application, history, events


def test_private_capture_resource_is_read_once_and_deleted(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cache" / "captures" / "capture.jpg"
    resource = _private_resource(path)

    observation = build_screen_observation_from_private_resource(resource, base_dir=tmp_path)

    assert observation.width == 320
    assert observation.height == 180
    assert observation.data_url.startswith("data:image/jpeg;base64,")
    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length path contract")
def test_private_capture_resource_accepts_windows_extended_length_path(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cache" / "captures" / "extended.jpg"
    resource = _private_resource(path)
    resource["path"] = rf"\\?\{path.resolve()}"

    observation = build_screen_observation_from_private_resource(resource, base_dir=tmp_path)

    assert observation.width == 320
    assert observation.height == 180
    assert not path.exists()


def test_private_capture_resource_rejects_path_escape_without_deleting_foreign_file(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.jpg"
    resource = _private_resource(outside)

    with pytest.raises(ValueError, match="受控截图目录"):
        build_screen_observation_from_private_resource(resource, base_dir=tmp_path)

    assert outside.exists()


def test_invalid_controlled_capture_is_also_deleted(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cache" / "captures" / "invalid.jpg"
    resource = _private_resource(path)
    resource["width"] = 0

    with pytest.raises(ValueError, match="width"):
        build_screen_observation_from_private_resource(resource, base_dir=tmp_path)

    assert not path.exists()


def test_manual_observation_is_consumed_once_and_chat_uses_multimodal_message(
    tmp_path: Path,
) -> None:
    pipeline = ObservationPipeline()
    application, history, events = _application(tmp_path, pipeline)
    capture = application.handle_request("observation.capture_started", {})
    resource_path = tmp_path / "data" / "cache" / "captures" / "manual.jpg"

    pushed = application.handle_request(
        "observation.push",
        {
            "source": "manual",
            "captureSessionId": capture["captureSessionId"],
            "resource": _private_resource(resource_path),
        },
    )
    accepted = application.handle_request(
        "chat.send",
        {"text": "帮我看这里", "observationId": pushed["observationId"]},
    )
    events.wait_for("chat.reply", predicate=lambda item: item["interactionId"] == accepted["interactionId"])

    content = pipeline.messages[0][-1]["content"]
    assert [part["type"] for part in content] == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    history_text = history.load()[0].content
    assert "[Sakura 已附加手动框选截图" in history_text
    assert "base64" not in history_text
    assert not resource_path.exists()

    with pytest.raises(BrainHostError) as reused:
        application.handle_request(
            "chat.send",
            {"text": "再看一次", "observationId": pushed["observationId"]},
        )
    assert reused.value.code == "OBSERVATION_NOT_FOUND"
    application.shutdown()


def test_chat_screen_capture_and_proactive_events_share_busy_state(tmp_path: Path) -> None:
    pipeline = BlockingPipeline()
    reminders = ReminderStore()
    application, _history, events = _application(tmp_path, pipeline, reminders=reminders)
    accepted = application.handle_request("chat.send", {"text": "先处理聊天"})
    assert pipeline.entered.wait(1)

    assert application.check_due_reminders() is False
    assert application.request_screen_awareness_capture() is False
    assert not any(name == "observation.capture_requested" for name, _payload in events.items)
    assert reminders.completed == []

    with pytest.raises(BrainHostError) as busy_capture:
        application.handle_request("observation.capture_started", {})
    assert busy_capture.value.code == "ASSISTANT_BUSY"

    pipeline.release.set()
    events.wait_for("chat.reply", predicate=lambda item: item["interactionId"] == accepted["interactionId"])
    application.shutdown()


def test_reminders_and_screen_awareness_emit_one_proactive_message_dto(tmp_path: Path) -> None:
    pipeline = ObservationPipeline()
    reminders = ReminderStore()
    application, _history, events = _application(tmp_path, pipeline, reminders=reminders)

    assert application.request_screen_awareness_capture() is True
    capture_request = events.wait_for("observation.capture_requested")
    application._screen_batch_started_at = time.monotonic() - 601
    proactive_path = tmp_path / "data" / "cache" / "captures" / "proactive.jpg"
    application.handle_request(
        "observation.push",
        {
            "source": "screen_awareness",
            "captureRequestId": capture_request["captureRequestId"],
            "resource": _private_resource(proactive_path, width=1280, height=720),
        },
    )
    screen_message = events.wait_for(
        "assistant.proactive_message",
        predicate=lambda item: item["kind"] == "screen_awareness",
    )
    assert screen_message["reply"]["segments"][0]["zh"] == "我留意了一下屏幕。"
    assert pipeline.events[0].payload["screen_contexts"][0]["data_url"].startswith(
        "data:image/jpeg;base64,"
    )

    assert application.check_due_reminders() is True
    reminder_message = events.wait_for(
        "assistant.proactive_message",
        predicate=lambda item: item["kind"] == "reminder",
    )
    assert reminder_message["eventId"] == "reminder-1"
    assert reminder_message["reply"]["segments"][0]["zh"] == "到时间了。"
    assert reminders.completed == ["reminder-1"]
    application.shutdown()


def test_screen_awareness_keeps_capture_batch_until_cooldown(tmp_path: Path) -> None:
    application, _history, events = _application(tmp_path, ObservationPipeline())
    assert application.request_screen_awareness_capture() is True
    capture_request = events.wait_for("observation.capture_requested")
    resource_path = tmp_path / "data" / "cache" / "captures" / "batch.jpg"

    result = application.handle_request(
        "observation.push",
        {
            "source": "screen_awareness",
            "captureRequestId": capture_request["captureRequestId"],
            "resource": _private_resource(resource_path),
        },
    )

    assert result["accepted"] is True
    assert result["dispatched"] is False
    assert len(application._screen_contexts) == 1
    assert not any(name == "assistant.proactive_message" for name, _payload in events.items)
    assert events.items[-1] == (
        "assistant.busy_changed",
        {"version": 1, "busy": False, "kind": "screen_awareness"},
    )
    application.shutdown()


def test_disabling_screen_awareness_removes_scheduler_job(tmp_path: Path) -> None:
    application, _history, _events = _application(tmp_path, ObservationPipeline())
    assert "screen-awareness" in application.scheduler.job_names

    result = application.handle_request("observation.configure", {"enabled": False})

    assert result["screenAwarenessEnabled"] is False
    assert "screen-awareness" not in application.scheduler.job_names
    assert application.request_screen_awareness_capture() is False
    application.shutdown()


def test_tauri_capture_controller_attaches_observation_and_passes_only_id_to_chat() -> None:
    payload = _run_node(
        f"""
import {{ createPetStore }} from {json.dumps(_module_url('desktop/frontend/core/store.js'))};
import {{ CaptureController }} from {json.dumps(_module_url('desktop/frontend/capture/capture_controller.js'))};
const store = createPetStore();
store.setBootstrap({{ character: {{ id: "demo" }} }});
const calls = [];
const controller = new CaptureController({{
  store,
  invoke: async (command, args) => {{ calls.push([command, args]); return {{ captureSessionId: "capture-1" }}; }},
  setStatus: () => {{}},
}});
await controller.open();
controller.handleReady({{ observationId: "observation-1", width: 640, height: 360 }});
const attachment = controller.consumeAttachment();
console.log(JSON.stringify({{ calls, attachment, state: store.getState() }}));
"""
    )

    assert payload["calls"] == [["open_capture_overlay", {}]]
    assert payload["attachment"] == {
        "observationId": "observation-1",
        "width": 640,
        "height": 360,
    }
    assert payload["state"]["interaction"]["busy"] is False
    assert payload["state"]["observation"]["attached"] is False


def test_tauri_capture_overlay_and_commands_are_wired_without_private_paths() -> None:
    frontend = ROOT / "desktop" / "frontend"
    capture_html = frontend / "capture.html"
    capture_controller = frontend / "capture" / "capture_controller.js"
    capture_selection = frontend / "capture" / "capture_selection.js"
    app_source = (frontend / "app.js").read_text(encoding="utf-8")
    rust_capture = (ROOT / "desktop" / "src-tauri" / "src" / "capture.rs").read_text(
        encoding="utf-8"
    )
    rust_lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    assert capture_html.is_file()
    assert capture_controller.is_file()
    assert capture_selection.is_file()
    assert "selection-box" in capture_html.read_text(encoding="utf-8")
    assert 'from "./capture/capture_controller.js"' in app_source
    assert "sakura://manual-observation-ready" in app_source
    assert "sakura://assistant-proactive-message" in app_source
    for command in (
        "list_capture_monitors",
        "open_capture_overlay",
        "capture_selected_region",
        "cancel_capture_overlay",
    ):
        assert f"fn {command}" in rust_capture
        assert f"capture::{command}" in rust_lib
    assert "resource.path" not in app_source
    assert "capturePath" not in app_source
