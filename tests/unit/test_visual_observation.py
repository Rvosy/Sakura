from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from app.agent.screen_observation import ScreenObservation
from app.storage.visual_observation import (
    VisualObservationJob,
    VisualObservationRecord,
    VisualObservationStore,
    build_visual_context_message,
    visual_observation_record_from_summary,
    summarize_visual_observation,
)


def test_summarize_visual_observation_saves_structured_text_without_image_data() -> None:
    class Client:
        def complete_raw(self, _system_prompt, messages, **_kwargs):  # type: ignore[no-untyped-def]
            assert messages[0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,abc"
            return json.dumps(
                {
                    "summary": "聊天窗口里有一句鼓励的话。",
                    "visible_texts": ["屏幕上的那句台词：你真的打算让我说吗？"],
                    "uncertain_texts": [],
                    "notable_elements": ["聊天气泡"],
                    "confidence": 0.92,
                    "sensitive_redacted": False,
                },
                ensure_ascii=False,
            )

    record = summarize_visual_observation(
        Client(),
        VisualObservationJob(
            id="vis_test",
            source="manual_screenshot",
            user_text="看这里",
            observation=ScreenObservation(
                data_url="data:image/jpeg;base64,abc",
                width=320,
                height=180,
                captured_at="2026-05-31T12:00:00+08:00",
                screen_name="manual-selection",
            ),
        ),
    )

    assert record.id == "vis_test"
    assert record.summary == "聊天窗口里有一句鼓励的话。"
    assert record.visible_texts == ["屏幕上的那句台词：你真的打算让我说吗？"]
    assert "base64" not in json.dumps(record.__dict__, ensure_ascii=False)


def test_summarize_visual_observation_uses_high_detail_for_screen_contexts() -> None:
    class Client:
        def complete_raw(self, _system_prompt, messages, **_kwargs):  # type: ignore[no-untyped-def]
            assert messages[0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,screen"
            assert messages[0]["content"][1]["image_url"]["detail"] == "high"
            return json.dumps(
                {
                    "summary": "屏幕上有一段代码。",
                    "visible_texts": ["def main()"],
                    "uncertain_texts": [],
                    "notable_elements": ["代码编辑器"],
                    "confidence": 0.9,
                    "sensitive_redacted": False,
                },
                ensure_ascii=False,
            )

    record = summarize_visual_observation(
        Client(),
        VisualObservationJob(
            id="vis_screen",
            source="screen_awareness_context",
            user_text="主动屏幕感知上下文批次",
            screen_contexts=[
                {
                    "data_url": "data:image/jpeg;base64,screen",
                    "width": 1920,
                    "height": 1080,
                    "captured_at": "2026-05-31T12:00:00+08:00",
                    "screen_name": "DISPLAY1",
                }
            ],
        ),
    )

    assert record.id == "vis_screen"
    assert record.summary == "屏幕上有一段代码。"


def test_summarize_visual_observation_accepts_fenced_json() -> None:
    class Client:
        def complete_raw(self, _system_prompt, _messages, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["response_format"] == {"type": "json_object"}
            return """```json
{
  "summary": "桌面上有调试日志。",
  "visible_texts": ["Debug"],
  "uncertain_texts": [],
  "notable_elements": ["终端"],
  "confidence": 0.8,
  "sensitive_redacted": false
}
```"""

    record = summarize_visual_observation(
        Client(),
        VisualObservationJob(
            id="vis_fenced",
            source="screen_awareness_context",
            user_text="看看桌面",
            screen_contexts=[
                {
                    "data_url": "data:image/jpeg;base64,screen",
                    "width": 1280,
                    "height": 720,
                    "captured_at": "2026-05-31T12:00:00+08:00",
                    "screen_name": "DISPLAY1",
                }
            ],
        ),
    )

    assert record.summary == "桌面上有调试日志。"
    assert record.visible_texts == ["Debug"]


def test_visual_observation_record_from_summary_redacts_sensitive_text() -> None:
    record = visual_observation_record_from_summary(
        VisualObservationJob(
            id="vis_hidden",
            source="manual_screenshot",
            user_text="看看 token=sk-abc123456789012345",
            screen_contexts=[
                {
                    "data_url": "data:image/jpeg;base64,screen",
                    "width": 1280,
                    "height": 720,
                    "captured_at": "2026-05-31T12:00:00+08:00",
                    "screen_name": "DISPLAY1",
                }
            ],
        ),
        {
            "summary": "页面里有 api_key: secret-value",
            "visible_texts": ["sk-abcdefghijklmnop"],
            "uncertain_texts": [],
            "notable_elements": ["设置页"],
            "confidence": 0.8,
            "sensitive_redacted": False,
        },
    )

    assert record is not None
    serialized = json.dumps(record.__dict__, ensure_ascii=False)
    assert "secret-value" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert record.sensitive_redacted is True


def test_visual_observation_store_redacts_sensitive_text_and_omits_images() -> None:
    path = Path("data") / f"test_visual_{uuid.uuid4().hex}.jsonl"
    try:
        store = VisualObservationStore(path)
        store.append(
            VisualObservationRecord(
                id="vis_secret",
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                source="manual_screenshot",
                user_text="密码: 123456",
                screen_name="DISPLAY1",
                width=100,
                height=100,
                summary="看到 API_KEY=secret-value",
                visible_texts=["token: abcdefghijklmnopqrstuvwxyz"],
                uncertain_texts=[],
                notable_elements=[],
                confidence=0.8,
            )
        )

        raw = path.read_text(encoding="utf-8")
        assert "123456" not in raw
        assert "secret-value" not in raw
        assert "abcdefghijklmnopqrstuvwxyz" not in raw
        assert "data:image" not in raw
        assert "[REDACTED]" in raw
    finally:
        path.unlink(missing_ok=True)


def test_visual_context_message_contains_recent_ocr_text() -> None:
    message = build_visual_context_message(
        "刚才截图里有什么台词？",
        [
            VisualObservationRecord(
                id="vis_dialogue",
                created_at="2026-05-31T12:00:00+08:00",
                source="manual_screenshot",
                user_text="看这里",
                screen_name="manual-selection",
                width=320,
                height=180,
                summary="聊天窗口截图。",
                visible_texts=["学姐，你一直在调整我的系统呢。"],
                uncertain_texts=[],
                notable_elements=["聊天气泡"],
                confidence=0.9,
            )
        ],
    )

    assert message is not None
    assert message["role"] == "system"
    assert "visual_id=vis_dialogue" in message["content"]
    assert "学姐，你一直在调整我的系统呢。" in message["content"]
