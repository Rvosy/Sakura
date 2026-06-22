from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.agent import AgentEvent, AgentResult, PendingToolAction
from app.core.chat_pipeline import ChatPipeline
from app.llm.chat_reply import parse_chat_reply
from app.storage.visual_observation import VisualObservationJob, VisualObservationStore


class RuntimeStub:
    api_client = object()

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.events: list[AgentEvent] = []

    def handle_user_message(self, messages, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.calls.append(f"user:{len(messages)}")
        if progress_callback is not None:
            progress_callback
        return AgentResult(parse_chat_reply("はい"), [])

    def handle_confirmed_action(self, action, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.calls.append(f"confirmed:{action.tool_name}")
        return AgentResult(parse_chat_reply("確認したよ"), [])

    def handle_cancelled_action(self, action):  # type: ignore[no-untyped-def]
        self.calls.append(f"cancelled:{action.tool_name}")
        return AgentResult(parse_chat_reply("やめたよ"), [])

    def handle_event(self, event, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.calls.append(f"event:{event.type}")
        self.events.append(event)
        return AgentResult(parse_chat_reply("見たよ"), [])


def test_chat_pipeline_delegates_chat_actions() -> None:
    runtime = RuntimeStub()
    pipeline = ChatPipeline(runtime)  # type: ignore[arg-type]
    action = PendingToolAction.create("demo_tool", {}, "测试")

    pipeline.run_user_message([{"role": "user", "content": "你好"}])
    pipeline.run_confirmed_action(action)
    pipeline.run_cancelled_action(action)
    pipeline.run_event(AgentEvent(type="timer", payload={}))

    assert runtime.calls == [
        "user:1",
        "confirmed:demo_tool",
        "cancelled:demo_tool",
        "event:timer",
    ]


def test_chat_pipeline_records_event_visual_observation_after_reply() -> None:
    class Runtime(RuntimeStub):
        def handle_event(self, event, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            self.calls.append(f"event:{event.type}")
            self.events.append(event)
            return AgentResult(
                parse_chat_reply("見たよ"),
                [],
                visual_observation={
                    "summary": "屏幕正在编辑 prompt_templates.py。",
                    "visible_texts": ["prompt_templates.py", "build_proactive_rules"],
                    "uncertain_texts": [],
                    "notable_elements": ["VS Code", "终端日志"],
                    "confidence": 0.95,
                    "sensitive_redacted": False,
                },
            )

    runtime = Runtime()
    path = Path("__pycache__") / "test_runtime" / f"visual_pipeline_{uuid.uuid4().hex}.jsonl"
    try:
        pipeline = ChatPipeline(
            runtime,  # type: ignore[arg-type]
            visual_observation_store=VisualObservationStore(path),
        )

        pipeline.run_event(
            AgentEvent(type="screen_awareness_check", payload={"screen_context_count": 1}),
            visual_observation_jobs=[
                VisualObservationJob(
                    id="vis_event",
                    source="screen_awareness_context",
                    user_text="主动屏幕感知上下文批次",
                    screen_contexts=[
                        {
                            "data_url": "data:image/jpeg;base64,event",
                            "width": 1280,
                            "height": 720,
                            "captured_at": "2026-06-01T08:20:19+08:00",
                            "screen_name": "Mi monitor",
                        }
                    ],
                )
            ],
        )

        event = runtime.events[-1]
        assert "visual_contexts" not in event.payload
        raw = path.read_text(encoding="utf-8")
        assert "vis_event" in raw
        assert "屏幕正在编辑 prompt_templates.py" in raw
        assert "prompt_templates.py" in raw
        assert "data:image" not in raw
    finally:
        path.unlink(missing_ok=True)


def test_chat_pipeline_keeps_images_and_records_visual_observation_after_reply() -> None:
    class Runtime(RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.last_messages = []

        def handle_user_message(self, messages, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            self.last_messages = messages
            if cancel_checker is not None:
                cancel_checker()
            return AgentResult(
                parse_chat_reply("はい"),
                [],
                visual_observation={
                    "summary": "截图里有一张设置页。",
                    "visible_texts": ["模型设置"],
                    "uncertain_texts": [],
                    "notable_elements": ["设置卡片"],
                    "confidence": 0.9,
                    "sensitive_redacted": False,
                },
            )

    runtime = Runtime()
    path = Path("__pycache__") / "test_runtime" / f"visual_chat_{uuid.uuid4().hex}.jsonl"
    try:
        pipeline = ChatPipeline(
            runtime,  # type: ignore[arg-type]
            visual_observation_store=VisualObservationStore(path),
        )
        pipeline.run_user_message(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看看这张图"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,manual"}},
                    ],
                }
            ],
            visual_observation_jobs=[
                VisualObservationJob(
                    id="vis_chat",
                    source="manual_selection",
                    user_text="看看这张图",
                    screen_contexts=[
                        {
                            "data_url": "data:image/jpeg;base64,manual",
                            "width": 800,
                            "height": 600,
                            "captured_at": "2026-06-01T08:20:19+08:00",
                            "screen_name": "manual-selection",
                        }
                    ],
                )
            ],
        )

        serialized = json.dumps(runtime.last_messages, ensure_ascii=False)
        assert "data:image/jpeg;base64,manual" in serialized
        assert "截图里有一张设置页" not in serialized
        raw = path.read_text(encoding="utf-8")
        assert "vis_chat" in raw
        assert "截图里有一张设置页" in raw
    finally:
        path.unlink(missing_ok=True)
