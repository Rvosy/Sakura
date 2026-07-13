from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from app.agent.mcp import web_search_server
from app.agent.tools.registry import Tool, ToolRegistry
from app.config.character_loader import CharacterRegistry
from app.storage.chat_history import ChatHistoryStore


def test_chat_history_repairs_truncated_utf8_tail(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    valid = {
        "created_at": "2026-07-12T00:00:00+08:00",
        "role": "user",
        "content": "保留我",
    }
    path.write_bytes((json.dumps(valid, ensure_ascii=False) + "\n").encode("utf-8") + b"\xe6")

    entries = ChatHistoryStore(path).load()

    assert [entry.content for entry in entries] == ["保留我"]
    assert path.read_bytes().endswith(b"\n")
    assert list(tmp_path.glob("history.jsonl.corrupt-*.bak"))


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


def test_dns_resolution_rejects_domain_with_private_address(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )

    with pytest.raises(ValueError, match="私有网络"):
        web_search_server._resolve_public_addresses("localtest.me", 80)
