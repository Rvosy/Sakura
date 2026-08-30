from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
import yaml

from app.agent.context_orchestrator import ContextOrchestrator
from app.llm.prompts.types import ContextFragment, ContextMessage, ContextRequest
from app.plugins.discovery import PluginDiscovery
from app.plugins.models import ContextProviderContribution
from plugins.builtin.sakura_mem0.plugin import (
    HOST_CHAT_COMPLETED_EVENT,
    MEMORY_COLLECTION_ID,
    SakuraMem0Plugin,
    SakuraMem0Runtime,
    _default_runtime,
    _context_request,
    _tool_registrations,
)
from plugins.builtin.sakura_mem0.api_client import ApiSettings, OpenAICompatibleClient


def test_mem0_api_client_normalizes_google_openai_url_without_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(request, timeout):
        assert timeout == 60
        captured.append((request.full_url, json.loads(request.data)))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="key",
            model="gemini-2.5-flash",
        )
    )
    result = client.complete_raw(
        "system",
        [{"role": "user", "content": "curate"}],
        temperature=0.2,
        response_format={"type": "json_object"},
        max_tokens=2000,
    )

    assert result == "{}"
    assert len(captured) == 1
    assert captured[0][0] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert captured[0][1] == {
        "model": "gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "curate"},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
    }


def test_context_request_keeps_latest_eight_messages_and_timeline_identity() -> None:
    request = _context_request(
        {
            "current_input": "现在的问题",
            "character_id": "sakura",
            "current_turn_id": "turn-9",
            "source_entry_ids": ["human-9"],
            "human_entry_id": "human-9",
            "recent_messages": [
                {"role": "user", "content": f"message-{index}"}
                for index in range(12)
            ],
        }
    )
    assert [message.content for message in request.recent_messages] == [
        f"message-{index}" for index in range(4, 12)
    ]
    assert request.current_turn_id == "turn-9"
    assert request.source_entry_ids == ("human-9",)


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
        self.download_started = threading.Event()
        self.download_cancelled = threading.Event()
        self.lifecycle: list[str] = []
        self.installed = True

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
                "installed": self.installed,
            },
        }

    def settings_save(self, values):
        self.saved.append(dict(values))
        return {"saved": True, "changePlan": "core_restart_required"}

    def begin_model_download(self, task_id):
        self.lifecycle.append(f"begin:{task_id}")

    def run_model_download(self, task_id, *, progress=None):
        self.lifecycle.append(f"run:{task_id}")
        if progress is not None:
            progress("connecting", 5)
            progress("downloading", 55)
        self.download_started.set()
        self.download_cancelled.wait(2)
        self.lifecycle.append(f"finish:{task_id}")
        return "cancelled" if self.download_cancelled.is_set() else "completed"

    def model_cancel(self, values):
        self.cancelled.append(dict(values))
        self.lifecycle.append(f"cancel:{values['taskHandle']}")
        self.download_cancelled.set()
        return {"accepted": True, "taskId": values["taskHandle"]}

    def note_timeline_changed(self, timeline):
        self.curated.append(timeline)

    def close(self):
        self.lifecycle.append("close")
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
            "sakura.host.settings.collection-v0": Recorder(),
            "sakura.host.settings.surface-v0": Recorder(),
            "sakura.host.model_slots": Recorder(),
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
    timeline = object()
    return (
        SakuraMem0Runtime(
            tmp_path,
            "sakura",
            boundary=boundary,  # type: ignore[arg-type]
            timeline=timeline,
            config_updater=lambda values: boundary.saved.append(dict(values)),
        ),
        boundary,
    )


def test_default_runtime_uses_only_declared_host_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Storage:
        def resolve(self, scope: str, name: str) -> Path:
            return tmp_path / scope / name

    class Character:
        def current(self) -> dict[str, str]:
            return {"id": "sakura", "systemPrompt": "system prompt"}

    class Models:
        def catalog(self):
            return []

        def resolve(self, selection):
            return {}

    class Config:
        def get(self):
            return {}

        def update(self, _values):
            return "applied"

    services = {
        "sakura.host.storage": Storage(),
        "sakura.host.character": Character(),
        "sakura.host.model_slots": Models(),
        "sakura.host.timeline": object(),
    }

    def fake_runtime(root, character_id, **kwargs):
        captured.update(root=root, character_id=character_id, **kwargs)
        return object()

    monkeypatch.setattr(
        "plugins.builtin.sakura_mem0.plugin.SakuraMem0Runtime",
        fake_runtime,
    )
    plugin_data = tmp_path / "plugin-data"
    context = SimpleNamespace(
        data_path=lambda _relative: plugin_data,
        get=lambda key: services[key],
        config=Config(),
    )
    result = _default_runtime(context)

    assert result is not None
    assert captured["root"] == plugin_data
    assert captured["character_id"] == "sakura"
    assert captured["system_prompt"] == "system prompt"
    assert captured["memory_dir"] == tmp_path / "data" / "memory"
    assert captured["memory_cache_dir"] == tmp_path / "cache" / "memory"


def test_manifest_is_discoverable_and_enabled_after_owner_cutover(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    spec = next(
        item
        for item in PluginDiscovery(
            root,
            config_path=tmp_path / "plugins.yaml",
        ).discover()
        if item.plugin_id == "sakura.memory.mem0"
    )
    assert spec.api_version == 4
    assert spec.enabled is True
    assert spec.requires == (
        "sakura.host.storage",
        "sakura.host.character",
        "sakura.host.timeline",
        "sakura.host.context",
        "sakura.host.tools",
        "sakura.host.settings",
        "sakura.host.settings.collection-v0",
        "sakura.host.settings.surface-v0",
        "sakura.host.model_slots",
    )


def test_plugin_registers_only_generic_host_services_and_effect_cleanup(tmp_path: Path) -> None:
    runtime, boundary = _runtime(tmp_path)
    context = FakeContext()

    SakuraMem0Plugin(lambda _context: runtime).setup(context)

    assert [name for name, _callback in context.events] == [
        HOST_CHAT_COMPLETED_EVENT,
    ]
    assert len(context.services["sakura.host.context"].calls) == 1
    tool_calls = context.services["sakura.host.tools"].calls
    assert [call[0][0]["name"] for call in tool_calls] == [
        "memory_search",
        "memory_remember",
        "memory_update",
        "memory_forget",
    ]
    assert {call[0][0]["group"] for call in tool_calls} == {"plugin"}
    settings_calls = context.services["sakura.host.settings"].calls
    assert [call[0][0]["sectionId"] for call in settings_calls] == [
        "memory",
        "memory_embedding_component",
        "memory_management",
    ]
    assert "collections" not in settings_calls[0][0][0]
    assert "surface" not in settings_calls[1][0][0]
    surface_calls = context.services["sakura.host.settings.surface-v0"].calls
    assert [call[0] for call in surface_calls] == [
        ("memory_embedding_component", "about"),
        ("memory_management", "memory"),
    ]
    collection_call = context.services["sakura.host.settings.collection-v0"].calls[0]
    assert collection_call[0][0] == "memory_management"
    assert collection_call[0][1]["collectionId"] == MEMORY_COLLECTION_ID
    slot_call = context.services["sakura.host.model_slots"].calls[0]
    assert slot_call[0][0] == {
        "slotId": "curation",
        "label": "记忆整理模型",
        "description": "用于把已完成的对话整理成长期记忆；继承时跟随对话模型。",
        "modelKind": "chat_completion",
        "required": False,
        "order": 30,
    }

    context.effects[0]()
    assert boundary.closed is True


def test_plugin_setup_does_not_wait_for_initial_timeline_catch_up(tmp_path: Path) -> None:
    runtime, boundary = _runtime(tmp_path)
    context = FakeContext()
    started = threading.Event()
    release = threading.Event()

    def blocking_catch_up() -> None:
        started.set()
        release.wait(5)

    runtime.catch_up_timeline = blocking_catch_up  # type: ignore[method-assign]
    setup_started = time.monotonic()
    SakuraMem0Plugin(lambda _context: runtime).setup(context)
    setup_elapsed = time.monotonic() - setup_started

    try:
        assert setup_elapsed < 0.5
        assert started.wait(1)
    finally:
        release.set()
        context.effects[0]()
    assert boundary.closed is True


def test_official_descriptors_pass_real_generic_host_validators(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_host_services import (
        _ModelSlotsHostService,
        _SettingsHostService,
        _ToolsHostService,
    )

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

    settings = _SettingsHostService(lambda *_args: {})
    settings.call(
        "register",
        [
            "sakura.memory.mem0",
            runtime.settings_descriptor(),
            {
                "load": handle,
                "save": handle,
                "actions": {},
            },
        ],
    )
    settings.call(
        "register",
        [
            "sakura.memory.mem0",
            runtime.component_descriptor(),
            {
                "load": handle,
                "save": None,
                "actions": {
                    "downloadEmbedding": handle,
                    "retryEmbedding": handle,
                    "cancelEmbedding": handle,
                },
            },
        ],
    )
    settings.register_surface(
        "sakura.memory.mem0",
        "memory_embedding_component",
        "about",
    )
    settings.call(
        "register",
        [
            "sakura.memory.mem0",
            runtime.memory_management_descriptor(),
            {
                "load": None,
                "save": None,
                "actions": {},
            },
        ],
    )
    settings.register_surface(
        "sakura.memory.mem0",
        "memory_management",
        "memory",
    )
    settings.register_collection(
        "sakura.memory.mem0",
        "memory_management",
        runtime.memory_collection_descriptor(),
        {
            "query": handle,
            "create": handle,
            "update": handle,
            "delete": handle,
        },
    )
    assert settings.count == 3
    section = next(
        item
        for item in settings.sections_for_plugin("sakura.memory.mem0")
        if item["sectionId"] == "memory"
    )
    assert [field["type"] for field in section["fields"]] == [
        "status",
        "integer",
    ]
    assert section["fields"][0]["placement"] == "section_header"
    component = next(
        item for item in settings.sections_for_plugin("sakura.memory.mem0")
        if item["sectionId"] == "memory_embedding_component"
    )
    assert component["surface"] == "about"
    assert component["fields"][0]["actionIds"] == [
        "downloadEmbedding",
        "retryEmbedding",
        "cancelEmbedding",
    ]
    assert component["values"]["embeddingResource"]["applicability"] == "required"
    assert component["values"]["embeddingResource"]["availableActionIds"] == []

    slots = _ModelSlotsHostService(lambda *_args: {"profileId": "", "model": ""})
    slots.call(
        "register",
        [
            "sakura.memory.mem0",
            {
                "slotId": "curation",
                "label": "记忆整理模型",
                "description": "用于整理长期记忆。",
                "modelKind": "chat_completion",
                "required": False,
                "order": 30,
            },
            {"load": handle, "save": handle},
        ],
    )
    assert slots.count == 1


def test_about_surface_is_resource_only_and_rejects_incomplete_v1_values() -> None:
    from app.core_host.plugin_host_services import HostServiceError, _SettingsHostService

    handle = "cb_" + "b" * 32
    incomplete_value = {
        "subtitle": "fixture",
        "ready": False,
        "taskState": "idle",
        "message": "missing",
        "detail": "",
        "progress": None,
        "availableActionIds": ["install"],
    }
    settings = _SettingsHostService(lambda *_args: {"component": incomplete_value})
    with pytest.raises(HostServiceError, match="SETTINGS_DESCRIPTOR_INVALID"):
        settings.call("register", ["fixture", {
            "sectionId": "component",
            "title": "Fixture",
            "fields": [{
                "key": "component", "label": "Component", "type": "resource",
                "default": incomplete_value, "actionIds": ["install"],
            }],
            "actions": [{"actionId": "install", "label": "Install"}],
        }, {
            "load": handle,
            "save": None,
            "actions": {"install": handle},
        }])

    current_value = {**incomplete_value, "applicability": "required"}
    settings = _SettingsHostService(lambda *_args: {"component": current_value})
    settings.call("register", ["fixture", {
        "sectionId": "component",
        "title": "Fixture",
        "fields": [{
            "key": "component", "label": "Component", "type": "resource",
            "default": current_value, "actionIds": ["install"],
        }],
        "actions": [{"actionId": "install", "label": "Install"}],
    }, {
        "load": handle,
        "save": None,
        "actions": {"install": handle},
    }])
    settings.register_surface("fixture", "component", "about")
    resource = settings.sections_for_plugin("fixture")[0]["values"]["component"]
    assert resource["applicability"] == "required"

    invalid = _SettingsHostService(lambda *_args: {})
    invalid.call("register", ["fixture", {
        "sectionId": "editable",
        "title": "Editable",
        "fields": [{"key": "name", "label": "Name", "type": "string", "default": ""}],
        "actions": [],
    }, {"load": handle, "save": handle, "actions": {}}])
    with pytest.raises(HostServiceError, match="SETTINGS_SURFACE_INVALID"):
        invalid.register_surface("fixture", "editable", "about")


def test_context_collection_and_settings_keep_character_scope(tmp_path: Path) -> None:
    runtime, boundary = _runtime(tmp_path)
    request = ContextRequest(
        current_input="我喜欢什么花？",
        character_id="sakura",
        character_name="Sakura",
        recent_messages=(ContextMessage("user", "我喜欢什么花？"),),
    )

    fragments = runtime.context(
        {
            "current_input": request.current_input,
            "character_id": request.character_id,
            "character_name": request.character_name,
            "source": request.source,
            "mode": request.mode,
            "recent_messages": [
                {"role": item.role, "content": item.content}
                for item in request.recent_messages
            ],
        }
    )
    assert fragments[0]["content"] == "与本轮相关的长期记忆：喜欢樱花"
    assert runtime.context({"current_input": "樱花", "character_id": "other"}) == []
    queried = runtime.query_collection(
        {"cursor": None, "limit": 25, "search": "樱花", "filters": {"layer": "semantic"}}
    )
    assert [item["itemId"] for item in queried["items"]] == ["memory-1"]
    assert "other-scope" not in str(queried)

    updated = runtime.update_collection_item("memory-1", {"content": "更喜欢八重樱"})
    assert updated["values"]["layer"] == "semantic"
    assert runtime.delete_collection_item("memory-1") == {"deleted": True}

    values = runtime.load_settings()
    assert values["status"] == {
        "state": "ready",
        "label": "运行正常",
        "message": "",
    }
    assert runtime.load_component_settings()["embeddingResource"] == {
        "applicability": "required",
        "subtitle": "sentence-transformers/all-MiniLM-L6-v2",
        "ready": True,
        "taskState": "idle",
        "message": "模型已安装，可用于长期记忆检索。",
        "detail": "",
        "progress": None,
        "availableActionIds": [],
    }
    runtime.save_settings({"triggerTurns": 12})
    assert runtime.load_model_slot() == {"profileId": "fixture", "model": "curator"}
    runtime.save_model_slot({"profileId": "fixture", "model": "curator"})
    assert boundary.saved == [
        {"triggerTurns": 12},
        {"curationProfileId": "fixture", "curationModel": "curator"},
    ]


def test_runtime_cancels_and_joins_model_download_before_closing_store(
    tmp_path: Path,
) -> None:
    runtime, boundary = _runtime(tmp_path)

    started = runtime.start_model_download({})
    assert started["message"] == "模型下载已在后台启动。"
    assert boundary.download_started.wait(1)
    resource = runtime.load_component_settings()["embeddingResource"]
    assert resource["taskState"] == "running"
    assert resource["detail"] == "下载模型文件"
    assert resource["progress"] == 55
    assert resource["availableActionIds"] == ["cancelEmbedding"]

    runtime.close()

    task_id = boundary.cancelled[0]["taskHandle"]
    assert boundary.lifecycle == [
        f"begin:{task_id}",
        f"run:{task_id}",
        f"cancel:{task_id}",
        f"finish:{task_id}",
        "close",
    ]
    assert boundary.closed is True


def test_memory_model_resource_exposes_contextual_actions_without_partial_install(
    tmp_path: Path,
) -> None:
    runtime, boundary = _runtime(tmp_path)
    boundary.installed = False

    missing = runtime.load_component_settings()["embeddingResource"]
    assert missing["taskState"] == "idle"
    assert missing["availableActionIds"] == ["downloadEmbedding"]

    runtime._model_task_state = "failed"
    runtime._model_task_error_code = "DOWNLOAD_NETWORK_FAILED"
    failed = runtime.load_component_settings()["embeddingResource"]
    assert failed["message"] == "下载失败，未安装不完整文件；普通聊天不受影响。"
    assert failed["detail"] == (
        "无法连接模型下载服务，请检查网络或代理后重试。"
        "（DOWNLOAD_NETWORK_FAILED）"
    )
    assert failed["availableActionIds"] == ["retryEmbedding"]

    boundary.installed = True
    retained = runtime.load_component_settings()["embeddingResource"]
    assert retained["ready"] is True
    assert retained["message"] == "下载失败，原有完整模型仍可使用。"
    assert "DOWNLOAD_NETWORK_FAILED" in retained["detail"]
    assert retained["availableActionIds"] == ["retryEmbedding"]


def test_long_legacy_memory_round_trips_through_generic_collection(tmp_path: Path) -> None:
    from app.core_host.plugin_host_services import _SettingsHostService

    runtime, boundary = _runtime(tmp_path)
    long_content = "🌸" * 16_384
    boundary.memory_store.memories.append(
        {
            "id": "memory-long",
            "content": long_content,
            "metadata": {
                "scope": "sakura",
                "layer": "semantic",
                "source": "explicit",
            },
        }
    )
    handle = "cb_" + "a" * 32

    def invoke(_handle, shape, *args):  # type: ignore[no-untyped-def]
        assert shape == "settings.collection.query"
        return runtime.query_collection(args[0])

    settings = _SettingsHostService(invoke)
    settings.call(
        "register",
        [
            "sakura.memory.mem0",
            runtime.memory_management_descriptor(),
            {
                "load": None,
                "save": None,
                "actions": {},
            },
        ],
    )
    settings.register_collection(
        "sakura.memory.mem0",
        "memory_management",
        runtime.memory_collection_descriptor(),
        {"query": handle, "create": None, "update": None, "delete": None},
    )
    result = settings.collection(
        "query",
        "sakura.memory.mem0",
        "memory_management",
        "memories",
        {"cursor": None, "limit": 25, "search": "🌸", "filters": {}},
    )
    assert result["items"][0]["values"]["content"] == long_content


def test_completed_fact_uses_timeline_service_and_ignores_other_character(
    tmp_path: Path,
) -> None:
    runtime, boundary = _runtime(tmp_path)
    # The plugin must not create or advance curation for another character.
    runtime.note_completed_chat(
        {
            "characterId": "other",
            "turnId": "turn-other",
            "cursor": "cursor-other",
        }
    )
    assert boundary.curated == []

    runtime.note_completed_chat(
        {
            "characterId": "sakura",
            "turnId": "turn-1",
            "cursor": "cursor-1",
        }
    )
    assert len(boundary.curated) == 1
    assert boundary.curated[0] is runtime._timeline  # noqa: SLF001 - verifies service routing


def test_two_memory_context_contributors_are_composable_and_failure_isolated() -> None:
    def fail(_request: ContextRequest):
        raise RuntimeError("vector store unavailable")

    providers = [
        ContextProviderContribution(
            provider_id="sakura.memory.mem0",
            description="vector memory",
            build_context=fail,
            order=40,
        ),
        ContextProviderContribution(
            provider_id="fixture.memory.flat-file",
            description="flat-file memory",
            build_context=lambda _request: (
                ContextFragment(
                    fragment_id="flat-memory",
                    source="flat-file",
                    content="来自非向量存储的长期事实",
                    priority=70,
                    token_budget=128,
                ),
            ),
            order=50,
        ),
    ]

    snapshot = ContextOrchestrator().build_snapshot(
        ContextRequest(current_input="继续项目", character_id="sakura"),
        providers=providers,
    )

    assert any(
        item.fragment.content == "来自非向量存储的长期事实"
        and item.fragment.source == "plugin:fixture.memory.flat-file"
        for item in snapshot.selected
    )
