from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import app.storage.visual_observation as visual_observation_module
from app.storage.visual_observation import (
    VisualObservationJob,
    VisualObservationRecord,
    VisualObservationStore,
    build_visual_context_message,
    visual_observation_record_from_summary,
)


def test_legacy_visual_summarizer_is_removed() -> None:
    assert not hasattr(visual_observation_module, "summarize_visual_observation")


def test_unused_visual_observation_search_is_removed() -> None:
    assert not hasattr(VisualObservationStore, "search")


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
