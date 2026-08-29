from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from app.core_host.character_settings import CharacterSettingsBoundary
from app.core_host.history import HistoryBoundary
from app.core_host.plugin_character import PluginCharacterStore
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from plugins.builtin.sakura_mem0.boundary import MemoryBoundary


GENERATION_A = "generation-character-a"
GENERATION_B = "generation-character-b"
CREDENTIAL_A = "credential-character-a"
CREDENTIAL_B = "credential-character-b"
NOW = "2026-08-29T12:00:00+08:00"


def _write_character(root: Path, character_id: str) -> None:
    package = root / "characters" / character_id
    package.mkdir(parents=True)
    (package / "card.md").write_text(f"You are {character_id}.", encoding="utf-8")
    (package / "portrait.png").write_bytes(b"isolated fixture")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": character_id.upper(),
                "card": "card.md",
                "portrait": {"default": "portrait.png", "expressions": {}},
            }
        ),
        encoding="utf-8",
    )


def _select_on_disk(root: Path, character_id: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "characters.yaml").write_text(
        yaml.safe_dump({"current_character_id": character_id}, sort_keys=False),
        encoding="utf-8",
    )


def _character_request(character_id: str) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION_A,
        "generationCredential": CREDENTIAL_A,
        "id": "switch-to-b",
        "name": "characters.settings.select",
        "payload": {"characterId": character_id},
    }


def _history_request(
    generation_id: str,
    credential: str,
    character_id: str,
    *,
    cursor: str | None = None,
) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": generation_id,
        "generationCredential": credential,
        "id": f"history-{generation_id}",
        "name": "ui.history.page",
        "payload": {
            "expectedCharacterId": character_id,
            "beforeCursor": cursor,
            "limit": 1,
        },
    }


class _ScopedMemoryStore:
    def __init__(
        self,
        character_id: str,
        records: dict[str, dict[str, dict[str, Any]]],
        *,
        ready: bool = True,
    ) -> None:
        self.character_id = character_id
        self.records = records
        self.ready = ready
        self.listener = None
        self.closed = False

    def add_status_listener(self, listener, *, replay: bool = True) -> None:  # type: ignore[no-untyped-def]
        self.listener = listener
        if replay and self.ready:
            listener("ready", "")

    def remove_status_listener(self, _listener) -> None:  # type: ignore[no-untyped-def]
        self.listener = None

    def is_ready(self) -> bool:
        return self.ready

    def needs_embedding_model_download(self) -> bool:
        return not self.ready

    def preload(self, *, wait: bool = False) -> None:
        del wait

    def search_memory(self, arguments, *, wait: bool = False):  # type: ignore[no-untyped-def]
        del wait
        query = str(arguments.get("query") or "")
        memories = [
            item
            for item in self.records.get(self.character_id, {}).values()
            if query in str(item["content"])
        ]
        return {"status": "ready", "memories": memories}

    def list_memories(self, *, limit=None):  # type: ignore[no-untyped-def]
        values = list(self.records.get(self.character_id, {}).values())
        return values if limit is None else values[:limit]

    def create_memory(self, arguments, *, wait: bool = True):  # type: ignore[no-untyped-def]
        del wait
        owned = self.records.setdefault(self.character_id, {})
        memory_id = f"{self.character_id}-{len(owned) + 1}"
        record = {
            "id": memory_id,
            "content": arguments["content"],
            "metadata": {**arguments, "scope": self.character_id},
        }
        owned[memory_id] = record
        return {"memory": record}

    def update_memory(self, arguments, *, wait: bool = True):  # type: ignore[no-untyped-def]
        del wait
        memory_id = str(arguments["id"])
        record = {
            "id": memory_id,
            "content": arguments["content"],
            "metadata": {**arguments, "scope": self.character_id},
        }
        self.records.setdefault(self.character_id, {})[memory_id] = record
        return {"memory": record}

    def forget_memory(self, arguments, *, wait: bool = True):  # type: ignore[no-untyped-def]
        del wait
        missing = self.records.setdefault(self.character_id, {}).pop(str(arguments["id"]), None)
        return {"already_missing": missing is None}

    def scoped(self, character_id: str):  # type: ignore[no-untyped-def]
        return _ScopedMemoryStore(character_id, self.records, ready=self.ready)

    def close(self) -> None:
        self.closed = True


def _memory(
    root: Path,
    character_id: str,
    records: dict[str, dict[str, dict[str, Any]]],
    *,
    ready: bool = True,
) -> MemoryBoundary:
    return MemoryBoundary(
        root,
        character_id,
        memory_store=_ScopedMemoryStore(character_id, records, ready=ready),  # type: ignore[arg-type]
    )


def _append_history(store: TimelineStore, character_id: str, index: int) -> None:
    store.append_many(
        [
            NewTimelineEntry(
                entry_id=f"{character_id}-entry-{index}",
                turn_id=f"{character_id}-turn-{index}",
                character_id=character_id,
                kind=TimelineKind.HUMAN,
                origin="chat",
                created_at=NOW,
                payload={"text": f"{character_id} history {index}"},
            )
        ]
    )


def test_a_b_a_switch_keeps_character_memory_history_and_generation_services_isolated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "isolated-user-root"
    _write_character(root, "alpha")
    _write_character(root, "beta")
    _select_on_disk(root, "alpha")

    old_character_service = PluginCharacterStore(root)
    settings = CharacterSettingsBoundary(GENERATION_A, CREDENTIAL_A, root)
    changed = settings.handle(_character_request("beta"))
    assert changed["payload"]["changePlan"] == "core_restart_required"
    assert changed["payload"]["snapshot"]["currentCharacterId"] == "beta"
    assert old_character_service.current("fixture.plugin")["id"] == "alpha"
    assert PluginCharacterStore(root).current("fixture.plugin")["id"] == "beta"

    records: dict[str, dict[str, dict[str, Any]]] = {}
    alpha_memory = _memory(root, "alpha", records)
    beta_memory = _memory(root, "beta", records)
    try:
        alpha_memory.upsert({"content": "alpha-only", "layer": "semantic"})
        beta_memory.upsert({"content": "beta-only", "layer": "core_profile"})
        assert [item["content"] for item in alpha_memory.search({"query": "only", "limit": 10})["memories"]] == ["alpha-only"]
        assert [item["content"] for item in beta_memory.search({"query": "only", "limit": 10})["memories"]] == ["beta-only"]

        alpha_memory._curation_state.mark_timeline_processed("alpha-cursor")  # noqa: SLF001
        beta_memory._curation_state.mark_timeline_processed("beta-cursor")  # noqa: SLF001
    finally:
        alpha_memory.close()
        beta_memory.close()

    alpha_again = _memory(root, "alpha", records)
    degraded_beta = _memory(root, "beta", records, ready=False)
    try:
        assert alpha_again._curation_state.curation_cursor() == "alpha-cursor"  # noqa: SLF001
        assert [item["content"] for item in alpha_again.search({"query": "only", "limit": 10})["memories"]] == ["alpha-only"]
        assert degraded_beta.search({"query": "alpha", "limit": 10}) == {
            "status": "degraded",
            "message": "本地记忆模型尚未安装；聊天将继续但不会召回记忆。",
            "memories": [],
        }
    finally:
        alpha_again.close()
        degraded_beta.close()

    timeline = TimelineStore(StoragePaths(root).timeline_database())
    timeline.initialize()
    _append_history(timeline, "alpha", 1)
    _append_history(timeline, "alpha", 2)
    _append_history(timeline, "beta", 1)
    alpha_history = HistoryBoundary(
        GENERATION_A,
        CREDENTIAL_A,
        root,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="alpha")),
    )
    beta_history = HistoryBoundary(
        GENERATION_B,
        CREDENTIAL_B,
        root,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="beta")),
    )
    alpha_page = alpha_history.handle(
        _history_request(GENERATION_A, CREDENTIAL_A, "alpha")
    )
    beta_page = beta_history.handle(
        _history_request(GENERATION_B, CREDENTIAL_B, "beta")
    )
    stale_cursor = beta_history.handle(
        _history_request(
            GENERATION_B,
            CREDENTIAL_B,
            "beta",
            cursor=alpha_page["payload"]["beforeCursor"],
        )
    )
    assert {item["payload"]["text"] for item in alpha_page["payload"]["entries"]} <= {
        "alpha history 1",
        "alpha history 2",
    }
    assert [item["payload"]["text"] for item in beta_page["payload"]["entries"]] == [
        "beta history 1"
    ]
    assert stale_cursor["ok"] is False
    assert stale_cursor["error"]["code"] == "TIMELINE_CURSOR_INVALID"

    _select_on_disk(root, "alpha")
    assert PluginCharacterStore(root).current("fixture.plugin")["id"] == "alpha"
    assert set(records) == {"alpha", "beta"}
