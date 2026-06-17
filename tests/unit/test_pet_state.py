from __future__ import annotations

import json

import pytest

from app.agent.tools import ToolRegistry
from app.pet_state.prompting import build_pet_state_context_message
from app.pet_state.store import PetStateStore
from app.pet_state.tools import create_pet_state_tools


def test_pet_state_store_updates_clamps_and_persists(tmp_path) -> None:
    path = tmp_path / "pet_state.json"
    store = PetStateStore(path)

    result = store.update_from_tool(
        {
            "delta": {
                "mood": "happy",
                "affect": {"valence": 2.0, "arousal": -0.5, "confidence": 0.8},
                "evidence": {
                    "last_user_signal": "用户语气轻松",
                    "last_trigger": "user_message",
                    "reason": "用户表达了积极反馈",
                },
            }
        }
    )

    state = result["state"]
    assert result["accepted"] is True
    assert result["harness_decision"]["status"] == "revised"
    assert result["harness_decision"]["revised_fields"] == ["affect.arousal", "affect.valence"]
    assert state["mood"] == "happy"
    assert state["affect"]["valence"] == 1.0
    assert state["affect"]["arousal"] == 0.0
    assert state["display"] == {"label": "开心", "idle_expression_hint": "微笑"}

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["state"]["mood"] == "happy"
    assert PetStateStore(path).snapshot()["state"]["display"]["label"] == "开心"


def test_pet_state_update_rejects_readonly_display(tmp_path) -> None:
    store = PetStateStore(tmp_path / "pet_state.json")

    with pytest.raises(ValueError, match="display"):
        store.update_from_tool(
            {
                "delta": {
                    "display": {"label": "由模型指定"},
                }
            }
        )


def test_pet_state_tools_read_and_update(tmp_path) -> None:
    store = PetStateStore(tmp_path / "pet_state.json")
    registry = ToolRegistry(create_pet_state_tools(store))

    update_result = registry.execute(
        "pet_state_update",
        {
            "delta": {
                "mood": "curious",
                "affect": {"valence": 0.2, "arousal": 0.6, "confidence": 0.9},
            },
            "forced": True,
            "force_fields": ["mood"],
            "force_reason": "模型认为用户提出了新问题",
        },
    )
    assert update_result.success
    assert update_result.content["harness_decision"]["status"] == "model_forced"

    get_result = registry.execute("pet_state_get", {})
    assert get_result.success
    assert get_result.content["state"]["mood"] == "curious"
    assert get_result.content["last_model_delta"]["forced"] is True


def test_pet_state_context_keeps_display_readonly_boundary(tmp_path) -> None:
    store = PetStateStore(tmp_path / "pet_state.json")
    message = build_pet_state_context_message(store.snapshot())

    assert message is not None
    content = message["content"]
    assert message["role"] == "system"
    assert "ChatSegment.tone" in content
    assert "ChatSegment.portrait" in content
    assert "pet_state_get" in content
    assert "pet_state_delta" in content
    assert "必须" in content
    assert "当前心情" in content
    assert "不要写 display" in content
