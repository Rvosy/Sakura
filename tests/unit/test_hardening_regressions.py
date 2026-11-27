from __future__ import annotations

import json
from pathlib import Path

from app.plugin_sdk.sakura_tools import Tool, ToolRegistry
from app.config.character_loader import CharacterRegistry


def test_character_registry_skips_broken_package_when_healthy_exists(tmp_path: Path) -> None:
    healthy = tmp_path / "characters" / "healthy"
    healthy.mkdir(parents=True)
    (healthy / "card.txt").write_text("prompt", encoding="utf-8")
    (healthy / "portrait.png").write_bytes(b"png")
    (healthy / "character.json").write_text(
        json.dumps(
            {
                "id": "healthy",
                "display_name": "Healthy",
                "card": "card.txt",
                "portrait": {"default": "portrait.png"},
            }
        ),
        encoding="utf-8",
    )
    broken = tmp_path / "characters" / "broken"
    broken.mkdir(parents=True)
    (broken / "character.json").write_text("{broken", encoding="utf-8")

    registry = CharacterRegistry(tmp_path)

    assert registry.get("healthy").display_name == "Healthy"
    assert len(registry.load_errors) == 1


def test_tool_registry_rejects_schema_violation_before_handler() -> None:
    called = False

    def handler(_arguments):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return "unexpected"

    registry = ToolRegistry(
        [
            Tool(
                name="bounded",
                description="test",
                parameters={
                    "type": "object",
                    "required": ["count"],
                    "additionalProperties": False,
                    "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 3}},
                },
                handler=handler,
            )
        ]
    )

    result = registry.execute("bounded", {"count": 99, "extra": True})

    assert result.success is False
    assert called is False
