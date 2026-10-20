from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.core_host.character_settings import CharacterSettingsBoundary
from app.core_host.history import HistoryBoundary
from app.core_host.plugin_character import PluginCharacterStore
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from plugins.optional.sakura_mem0.boundary import MemoryBoundary


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
    settings = CharacterSettingsBoundary(GENERATION_A, CREDENTIAL_A, root,
        apply_switch=lambda: old_character_service.set_current("beta"))
    changed = settings.handle(_character_request("beta"))
    assert changed["payload"]["changePlan"] == "character_switch"
    assert changed["payload"]["snapshot"]["currentCharacterId"] == "beta"
    assert old_character_service.current("fixture.plugin")["id"] == "beta"
    assert PluginCharacterStore(root).current("fixture.plugin")["id"] == "beta"

    alpha_memory = MemoryBoundary(root, "alpha")
    beta_memory = MemoryBoundary(root, "beta")
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

    alpha_again = MemoryBoundary(root, "alpha")
    offline_beta = MemoryBoundary(root, "beta")
    try:
        assert alpha_again._curation_state.curation_cursor() == "alpha-cursor"  # noqa: SLF001
        assert [item["content"] for item in alpha_again.search({"query": "only", "limit": 10})["memories"]] == ["alpha-only"]
        assert offline_beta.memory_store.needs_embedding_model_download()
        assert offline_beta.search({"query": "alpha", "limit": 10})["memories"] == []
        assert offline_beta.search({"query": "beta", "limit": 10})["memories"][0]["content"] == "beta-only"
    finally:
        alpha_again.close()
        offline_beta.close()

    timeline = TimelineStore(StoragePaths(root).timeline_database())
    timeline.initialize()
    _append_history(timeline, "alpha", 1)
    _append_history(timeline, "alpha", 2)
    _append_history(timeline, "beta", 1)
    alpha_history = HistoryBoundary(
        GENERATION_A,
        CREDENTIAL_A,
        root,
        character_provider=lambda: "alpha",
    )
    beta_history = HistoryBoundary(
        GENERATION_A,
        CREDENTIAL_A,
        root,
        character_provider=lambda: "beta",
    )
    alpha_page = alpha_history.handle(
        _history_request(GENERATION_A, CREDENTIAL_A, "alpha")
    )
    beta_page = beta_history.handle(
        _history_request(GENERATION_A, CREDENTIAL_A, "beta")
    )
    stale_cursor = beta_history.handle(
        _history_request(
            GENERATION_A,
            CREDENTIAL_A,
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
