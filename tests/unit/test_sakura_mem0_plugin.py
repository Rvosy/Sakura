from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from sakura_assistant.agent.context_orchestrator import ContextOrchestrator
from sakura_context import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution
from plugins.optional.sakura_mem0.plugin import (
    SakuraMem0Plugin,
    SakuraMem0Runtime,
    _default_runtime,
    _context_request,
)
from plugins.optional.sakura_mem0.memory import MemoryRecord


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
        records = self.memories if limit is None else self.memories[:limit]
        return [{**MemoryRecord(id=item["id"], content=item["content"]).to_dict(),
                 "pinned": False, "state": "active", "revision": 1, "evidence": [], "occurred_at": "",
                 "subject": "未分类", "subject_kind": "topic", "predicate": "一般事实", "target": "", "target_kind": "",
                 **item, **item["metadata"]} for item in records]


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

    def list_memories(self, *, limit=None):
        return self.memory_store.list_memories(limit=limit)

    def settings_get(self):
        return {
            "status": "ready",
            "message": "",
            "curation": {"triggerTurns": 8},
            "curationModelSlot": {"serviceKey": "sakura.model.openai_compatible", "profileId": "fixture", "modelId": "curator"},
            "providerChoices": [
                {"id": "fixture", "alias": "Fixture", "models": ["curator"]}
            ],
            "embedding": {
                "model": "BAAI/bge-small-zh-v1.5",
                "index": {"state": "ready"},
                "installed": self.installed,
            },
        }

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
            "sakura.host.model_slots.v2": Recorder(),
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


def test_memory_collection_registers_and_routes_file_transfer(tmp_path: Path) -> None:
    from app.core_host.plugin_host_services import _SettingsHostService

    runtime, boundary = _runtime(tmp_path)
    requests = []
    def transfer(request):
        requests.append(request)
        return {"exported": 2}
    boundary.transfer = transfer
    context = FakeContext()
    SakuraMem0Plugin(lambda _context: runtime).setup(context)
    try:
        (section_id, descriptor), callbacks = next(call for call in context.services["sakura.host.settings.collection-v0"].calls if "transfer" in call[1])
        service = _SettingsHostService(lambda _handle, _shape, payload: callbacks["transfer"](payload))
        service.call("register", ["sakura.memory.mem0",
            {"sectionId": section_id, "title": "记忆", "fields": []},
            {"load": None, "save": None, "actions": {}}])
        service.register_collection("sakura.memory.mem0", section_id, descriptor,
            {name: "cb_" + "a" * 32 for name in callbacks})
        section, = service.sections_for_plugin("sakura.memory.mem0")
        assert section["collections"][0]["canTransfer"] is True
        request = {"action": "export", "path": str(tmp_path / "memory_sakura.json"),
                   "format": "json", "expectedCharacterId": "sakura"}
        assert service.collection("transfer", "sakura.memory.mem0", section_id,
                                  descriptor["collectionId"], request) == {"exported": 2}
        assert requests == [request]
    finally:
        context.effects[0]()


def test_about_surface_is_resource_only_and_omits_incomplete_v1_values() -> None:
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
    unavailable = settings.sections_for_plugin("fixture")[0]
    assert unavailable["fields"] == []
    assert unavailable["reasonCode"] == "SETTINGS_DESCRIPTOR_INVALID"

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


def test_runtime_cancels_and_joins_model_download_before_closing_store(
    tmp_path: Path,
) -> None:
    runtime, boundary = _runtime(tmp_path)

    runtime.start_model_download({})
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
    assert failed["ready"] is False
    assert "DOWNLOAD_NETWORK_FAILED" in failed["detail"]
    assert failed["availableActionIds"] == ["retryEmbedding"]

    boundary.installed = True
    retained = runtime.load_component_settings()["embeddingResource"]
    assert retained["ready"] is True
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
                "source": "explicit", "subject": "未分类", "subject_kind": "topic",
                "predicate": "一般事实", "target": "", "target_kind": "",
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
                "save": handle,
                "actions": {item["actionId"]: handle for item in runtime.memory_management_descriptor()["actions"]},
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


def test_plugin_waits_for_character_then_rebinds_and_releases_old_memory(tmp_path):
    context = FakeContext()
    created = []
    selected = None

    def factory(_context):
        if selected is None:
            return None
        boundary = FakeBoundary()
        runtime = SakuraMem0Runtime(tmp_path, selected, boundary=boundary)
        created.append((runtime, boundary))
        return runtime

    plugin = SakuraMem0Plugin(factory)
    plugin.setup(context)
    waiting = context.services["sakura.host.settings"].calls[-1][0][0]
    assert waiting["fields"][0]["default"]["state"] == "neutral"
    assert created == []
    changed = next(callback for event, callback in context.events if event == "sakura.host.character.changed")
    selected = "alpha"
    changed({"characterId": selected})
    assert created[-1][0].character_id == "alpha"
    selected = "beta"
    changed({"characterId": selected})
    assert created[0][1].closed
    assert created[-1][0].character_id == "beta"
    assert not created[-1][1].closed
    plugin.close()
    assert created[-1][1].closed
    selected = "gamma"
    changed({"characterId": selected})
    assert len(created) == 2


@pytest.mark.parametrize("code", ["CHARACTER_NOT_FOUND", "CHARACTER_RESOURCE_INVALID"])
def test_default_runtime_waits_only_for_missing_character(tmp_path, code):
    class CharacterError(RuntimeError):
        pass

    error = CharacterError(code)
    error.code = code

    def current():
        raise error

    context = SimpleNamespace(data_path=lambda _path: tmp_path,
                              get=lambda key: SimpleNamespace(current=current))
    if code == "CHARACTER_NOT_FOUND":
        assert _default_runtime(context) is None
    else:
        with pytest.raises(CharacterError):
            _default_runtime(context)
