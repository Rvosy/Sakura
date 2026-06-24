from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.agent.tools import ToolRegistry
from app.pet_state.models import apply_pet_state_delta, default_pet_state_record
from app.pet_state.prompting import build_pet_state_context_message
from app.pet_state.store import PetStateStore
from app.pet_state.tools import create_pet_state_tools


def test_pet_state_submodules_import_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.pet_state.models; import app.pet_state.tools",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


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


def test_pet_state_noop_delta_keeps_updated_at(monkeypatch) -> None:
    record = default_pet_state_record()
    original_updated_at = record.state.updated_at
    monkeypatch.setattr("app.pet_state.models._now_iso", lambda: "2099-01-01T00:00:00+08:00")

    next_record, decision = apply_pet_state_delta(record, {"mood": record.state.mood})

    assert decision["status"] == "noop"
    assert next_record.state.updated_at == original_updated_at


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
    assert "正式写路径" in content
    assert "不需要每次携带" in content
    assert "当前心情" in content
    assert "不要写 display" in content
