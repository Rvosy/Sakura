from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from app.llm.prompts.types import ContextMessage, ContextRequest
from app.plugins.discovery import PluginDiscovery
from plugins.sakura_mem0.plugin import (
    HOST_CHAT_COMPLETED_EVENT,
    MEMORY_COLLECTION_ID,
    SakuraMem0Plugin,
    SakuraMem0Runtime,
    _assistant_root_from_module,
    _tool_registrations,
)


class FakeStore:
    def __init__(self) -> None:
        self.memories = [
            {
                "id": "memory-1",
                "content": "喜欢樱花",
                "metadata": {
                    "scope": "sakura",
                    "layer": "semantic",
                    "category": "preference",
                    "source": "explicit",
                    "importance": 0.8,
                    "confidence": 0.9,
                    "updated_at": "2026-08-20T10:00:00+08:00",
                },
            },
            {
                "id": "other-scope",
                "content": "不得越过角色 scope",
                "metadata": {"scope": "other", "layer": "semantic"},
            },
        ]

    def list_memories(self, *, limit=None):
        return list(self.memories if limit is None else self.memories[:limit])


class FakeBoundary:
    def __init__(self) -> None:
        self.memory_store = FakeStore()
        self.saved: list[dict[str, object]] = []
        self.curated = []
        self.closed = False
        self.cancelled = []

    def status(self):
        return {"status": "ready", "message": ""}

    def search_memory(self, arguments, *, wait=False):
        assert wait is False
        return {
            "status": "ready",
            "memories": [
                {
                    "id": "memory-1",
                    "content": "喜欢樱花",
                    "source": "explicit",
                    "score": 0.9,
                    "updated_at": "2026-08-20T10:00:00+08:00",
                }
            ],
        }

    def upsert(self, values):
        payload = dict(values)
        memory_id = str(payload.get("id") or "created")
        record = {
            "id": memory_id,
            "content": payload["content"],
            "layer": payload.get("layer", "semantic"),
            "category": payload.get("category", ""),
            "source": payload.get("source", "explicit"),
            "importance": payload.get("importance", 0.5),
            "confidence": payload.get("confidence", 0.8),
            "updatedAt": "2026-08-20T11:00:00+08:00",
        }
        return {"status": "ready", "memory": record}

    def delete(self, values):
        return {
            "status": "ready",
            "deletedId": values["id"],
            "alreadyMissing": values["id"] == "missing",
        }

    def settings_get(self):
        return {
            "status": "ready",
            "message": "",
            "curation": {"triggerTurns": 8},
            "curationModelSlot": {"profileId": "fixture", "model": "curator"},
            "providerChoices": [
                {"id": "fixture", "alias": "Fixture", "models": ["curator"]}
            ],
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "installed": True,
            },
        }

    def settings_save(self, values):
        self.saved.append(dict(values))
        return {"saved": True, "changePlan": "core_restart_required"}

    def model_download(self, request):
        return {"accepted": True, "taskId": request["id"], "status": "completed"}

    def model_cancel(self, values):
        self.cancelled.append(dict(values))
        return {"accepted": True, "taskId": values["taskHandle"]}

    def note_completed_chat(self, history):
        self.curated.append(history.load())

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self) -> None:
        self.calls = []

    def register(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return lambda: None


class FakeContext:
    def __init__(self) -> None:
        self.effects = []
        self.events = []
        self.services = {
            "sakura.host.context": Recorder(),
            "sakura.host.tools": Recorder(),
            "sakura.host.settings": Recorder(),
        }

    def effect(self, cleanup):
        self.effects.append(cleanup)
        return cleanup

    def on(self, name, callback):
        self.events.append((name, callback))
        return lambda: None

    def get(self, key):
        return self.services[key]


def _runtime(tmp_path: Path) -> tuple[SakuraMem0Runtime, FakeBoundary]:
    boundary = FakeBoundary()
    return (
        SakuraMem0Runtime(
            tmp_path,
            "sakura",
            boundary=boundary,  # type: ignore[arg-type]
        ),
        boundary,
    )


def test_bundled_layout_resolves_existing_assistant_root() -> None:
    module = Path(__file__).parents[2] / "plugins" / "sakura_mem0" / "plugin.py"
    assert _assistant_root_from_module(module) == Path(__file__).parents[2].resolve()


def test_manifest_is_discoverable_but_stays_disabled_before_owner_cutover() -> None:
    root = Path(__file__).parents[2]
    spec = next(
        item
        for item in PluginDiscovery(root).discover()
        if item.plugin_id == "sakura.memory.mem0"
    )
    assert spec.api_version == 3
    assert spec.enabled is False
    assert spec.requires == (
        "sakura.host.context",
        "sakura.host.tools",
        "sakura.host.settings",
    )


def test_plugin_registers_only_generic_host_services_and_effect_cleanup(tmp_path: Path) -> None:
    runtime, boundary = _runtime(tmp_path)
    context = FakeContext()

    SakuraMem0Plugin(lambda: runtime).setup(context)

    assert [name for name, _callback in context.events] == [HOST_CHAT_COMPLETED_EVENT]
    assert len(context.services["sakura.host.context"].calls) == 1
    tool_calls = context.services["sakura.host.tools"].calls
    assert [call[0][0]["name"] for call in tool_calls] == [
        "memory_search",
        "memory_remember",
        "memory_update",
        "memory_forget",
    ]
    assert {call[0][0]["group"] for call in tool_calls} == {"plugin"}
    settings_call = context.services["sakura.host.settings"].calls[0]
    assert settings_call[0][0]["collections"][0]["collectionId"] == MEMORY_COLLECTION_ID

    context.effects[0]()
    assert boundary.closed is True


def test_official_descriptors_pass_real_generic_host_validators(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_host_services import _SettingsHostService, _ToolsHostService

    runtime, _boundary = _runtime(tmp_path)
    handle = "cb_" + "a" * 32
    registry = ToolRegistry()
    tools = _ToolsHostService(registry, lambda *_args: {})
    for descriptor, _callback in _tool_registrations(runtime):
        tools.call("register", [descriptor, handle])
    assert {tool.name for tool in registry.all()} == {
        "memory_search",
        "memory_remember",
        "memory_update",
        "memory_forget",
    }

    settings = _SettingsHostService(lambda *_args: {}, lambda _plugin_id: None)
    settings.call(
        "register",
        [
            "sakura.memory.mem0",
            runtime.settings_descriptor(),
            {
                "load": handle,
                "save": handle,
                "actions": {
                    "downloadEmbedding": handle,
                    "refreshStatus": handle,
                    "cancelEmbedding": handle,
                },
                "collections": {
                    "memories": {
                        "query": handle,
                        "create": handle,
                        "update": handle,
                        "delete": handle,
                    }
                },
            },
        ],
    )
    assert settings.count == 1


def test_context_collection_and_settings_keep_character_scope(tmp_path: Path) -> None:
    runtime, boundary = _runtime(tmp_path)
    request = ContextRequest(
        current_input="我喜欢什么花？",
        character_id="sakura",
        character_name="Sakura",
        recent_messages=(ContextMessage("user", "我喜欢什么花？"),),
    )

    fragments = runtime.context(request)
    assert fragments[0]["content"] == "与本轮相关的长期记忆：喜欢樱花"
    queried = runtime.query_collection(
        {"cursor": None, "limit": 25, "search": "樱花", "filters": {"layer": "semantic"}}
    )
    assert [item["itemId"] for item in queried["items"]] == ["memory-1"]
    assert "other-scope" not in str(queried)

    updated = runtime.update_collection_item("memory-1", {"content": "更喜欢八重樱"})
    assert updated["values"]["layer"] == "semantic"
    assert runtime.delete_collection_item("memory-1") == {"deleted": True}

    values = runtime.load_settings()
    assert values["status"] == "就绪"
    assert values["embeddingStatus"] == "已安装"
    runtime.save_settings({"triggerTurns": 12, "curationModel": values["curationModel"]})
    assert boundary.saved == [
        {
            "triggerTurns": 12,
            "curationModelSlot": {"profileId": "fixture", "model": "curator"},
        }
    ]


def test_completed_fact_reuses_existing_history_and_ignores_other_character(
    tmp_path: Path,
) -> None:
    runtime, boundary = _runtime(tmp_path)
    history_path = tmp_path / "data" / "chat_history" / "sakura.jsonl"
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        yaml.safe_dump({"not": "the history format"}),
        encoding="utf-8",
    )
    # The plugin must not create or advance curation for another character.
    runtime.note_completed_chat(
        {
            "characterId": "other",
            "messages": [
                {"role": "user", "content": "ignored"},
                {"role": "assistant", "content": "ignored"},
            ],
        }
    )
    assert boundary.curated == []

    history_path.unlink()
    from app.storage.chat_history import ChatHistoryStore

    history = ChatHistoryStore(history_path)
    history.append("user", "请记住樱花")
    history.append("assistant", "好的")
    runtime.note_completed_chat(
        {
            "characterId": "sakura",
            "messages": [
                {"role": "user", "content": "请记住樱花"},
                {"role": "assistant", "content": "好的"},
            ],
        }
    )
    assert len(boundary.curated) == 1
    assert [item.role for item in boundary.curated[0]] == ["user", "assistant"]
