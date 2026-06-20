from __future__ import annotations

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat
import sys
import threading
import time
from types import SimpleNamespace
import uuid
import zipfile

import pytest

import app.agent.memory as memory_module
from app.agent.actions import AgentEvent, PendingToolAction
from app.agent.builtin_tools import create_builtin_tool_registry
from app.agent.memory import MemoryModelImportError, MemoryStore
from app.agent.mcp.bridge import MCPToolSpec
from app.agent.mcp.config import load_mcp_config
from app.agent.mcp.provider import MCPToolProvider, register_mcp_tools_from_config
from app.agent.mcp.settings import MCPRuntimeSettings, apply_mcp_runtime_settings
from app.agent.reminders import ReminderStore
from app.agent.runtime import AgentRuntime, _build_event_messages, _build_tool_results_message
from app.agent.runtime import _redact_event_for_model, _redact_tool_result_for_model
from app.agent.screen_tools import (
    OBSERVE_SCREEN_TOOL_NAME,
    SCREEN_OBSERVATION_DISABLED_ERROR,
    SCREEN_OBSERVATION_REQUEST_ACTION,
    create_screen_observation_tool,
)
from app.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from app.config.settings_service import AppSettingsService
from app.config.yaml_config import load_yaml_mapping
from app.llm.api_client import (
    ApiSettings,
    ApiRequestError,
    ChatCompletionTurn,
    NativeToolCall,
    is_vision_unsupported_error,
    messages_contain_image,
)
from app.plugins.manager import PluginManager
from app.llm.context_trimming import MAX_MODEL_CONTEXT_MESSAGES, trim_messages_for_model
from app.llm.prompt_templates import (
    build_event_system_prompt,
    build_screen_awareness_check_tool_system_prompt,
)
from app.agent.screen_awareness import (
    ScreenAwarenessSettings,
    estimate_screen_context_batch_tokens_for_size,
    estimate_screen_context_image_tokens_for_size,
)
from app.agent.screen_observation import (
    SCREEN_OBSERVATION_HISTORY_MARKER,
    ScreenObservation,
    append_observation_marker,
    build_screen_observation_user_message,
    should_observe_screen,
)


def _legacy_complete_with_tools(self, system_prompt, messages, **_kwargs):  # type: ignore[no-untyped-def]
    tools = _kwargs.get("tools") or []
    tool_names = [
        tool["function"]["name"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    ]
    if hasattr(self, "tool_names"):
        self.tool_names = tool_names
    if hasattr(self, "tool_name_batches"):
        self.tool_name_batches.append(tool_names)
    raw = self.complete_raw(system_prompt, messages)
    return _chat_completion_turn_from_legacy_raw(raw)


def _chat_completion_turn_from_legacy_raw(raw: str) -> ChatCompletionTurn:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    content = raw
    raw_tool_calls: list[dict[str, object]] = []
    if isinstance(payload, dict) and "reply" in payload:
        content = json.dumps(payload["reply"], ensure_ascii=False)
        raw_tool_calls_value = payload.get("tool_calls")
        if isinstance(raw_tool_calls_value, list):
            raw_tool_calls = [
                item
                for item in raw_tool_calls_value
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]

    tool_calls: list[NativeToolCall] = []
    message_tool_calls: list[dict[str, object]] = []
    for index, item in enumerate(raw_tool_calls):
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        else:
            arguments = dict(arguments)
        reason = item.get("reason")
        if isinstance(reason, str) and reason.strip() and "reason" not in arguments:
            arguments["reason"] = reason.strip()
        arguments_json = json.dumps(arguments, ensure_ascii=False)
        call_id = f"call_{index}"
        name = str(item["name"])
        tool_calls.append(
            NativeToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                arguments_json=arguments_json,
            )
        )
        message_tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments_json},
            }
        )

    message: dict[str, object] = {"role": "assistant", "content": content}
    if message_tool_calls:
        message["tool_calls"] = message_tool_calls
    return ChatCompletionTurn(
        content=content,
        tool_calls=tool_calls,
        message=message,
    )


def test_add_reminder_delay_seconds_generates_future_time() -> None:
    store = ReminderStore(_runtime_json_path("reminders"))
    before = datetime.now().astimezone()

    result = store.add_reminder({"text": "喝水", "delay_seconds": 30})

    trigger_at = datetime.fromisoformat(result["reminder"]["trigger_at"])
    after = datetime.now().astimezone()
    assert before + timedelta(seconds=25) <= trigger_at <= after + timedelta(seconds=35)


def test_add_reminder_delay_minutes_generates_future_time() -> None:
    store = ReminderStore(_runtime_json_path("reminders"))
    before = datetime.now().astimezone()

    result = store.add_reminder({"text": "休息", "delay_minutes": 2})

    trigger_at = datetime.fromisoformat(result["reminder"]["trigger_at"])
    after = datetime.now().astimezone()
    assert before + timedelta(seconds=115) <= trigger_at <= after + timedelta(seconds=125)


def test_add_reminder_rejects_past_trigger_at() -> None:
    store = ReminderStore(_runtime_json_path("reminders"))
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")

    with pytest.raises(ValueError, match="提醒时间必须晚于当前时间"):
        store.add_reminder({"text": "过期提醒", "trigger_at": past})


def test_due_reminders_and_mark_completed() -> None:
    store = ReminderStore(_runtime_json_path("reminders"))
    now = datetime.now().astimezone()
    due = store.add_reminder({"text": "到点", "delay_seconds": 1})["reminder"]
    future = store.add_reminder({"text": "稍后", "delay_minutes": 5})["reminder"]

    due["trigger_at"] = (now - timedelta(seconds=1)).isoformat(timespec="seconds")
    future["trigger_at"] = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
    store._save({"reminders": [due, future]})

    due_reminders = store.due_reminders(now)
    assert [reminder["id"] for reminder in due_reminders] == [due["id"]]

    store.mark_completed(due["id"])

    assert store.due_reminders(now) == []


def test_screen_awareness_settings_default_to_enabled() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_defaults"))
    settings = service.load_screen_awareness_settings()

    assert settings.enabled
    assert settings.screen_context_enabled
    assert settings.check_interval_minutes == 2
    assert settings.cooldown_minutes == 10
    assert settings.screen_context_batch_limit == 6


def test_screen_awareness_token_estimate_uses_high_detail_rules() -> None:
    assert estimate_screen_context_image_tokens_for_size(
        1920,
        1080,
        model="gpt-4o",
    ) == 1105
    assert estimate_screen_context_image_tokens_for_size(
        3840,
        2160,
        model="gpt-4o",
    ) == 1105
    assert estimate_screen_context_batch_tokens_for_size(
        1920,
        1080,
        6,
        model="gpt-4o",
    ) == 6630


def test_screen_awareness_settings_clamp_intervals() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_interval"))
    service.save_system_values(
        "screen_awareness",
        {
            "enabled": True,
            "screen_context_enabled": True,
            "check_interval_minutes": 999,
            "cooldown_minutes": 999,
            "screen_context_batch_limit": 999,
        },
    )

    settings = service.load_screen_awareness_settings().normalized()

    assert settings.enabled
    assert settings.screen_context_enabled
    assert settings.check_interval_minutes == 120
    assert settings.cooldown_minutes == 120
    assert settings.screen_context_batch_limit == 20


def test_screen_awareness_settings_min_intervals_are_one_minute() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_min_interval"))
    service.save_system_values(
        "screen_awareness",
        {
            "check_interval_minutes": 0,
            "cooldown_minutes": 0,
            "screen_context_batch_limit": 0,
        },
    )

    settings = service.load_screen_awareness_settings().normalized()

    assert settings.check_interval_minutes == 1
    assert settings.cooldown_minutes == 1
    assert settings.screen_context_batch_limit == 1


def test_screen_awareness_settings_invalid_cooldown_uses_default() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_invalid_cooldown"))
    service.save_system_values("screen_awareness", {"cooldown_minutes": "soon"})

    settings = service.load_screen_awareness_settings()

    assert settings.cooldown_minutes == 10


def test_screen_awareness_settings_invalid_batch_limit_uses_default() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_invalid_batch_limit"))
    service.save_system_values("screen_awareness", {"screen_context_batch_limit": "many"})

    settings = service.load_screen_awareness_settings()

    assert settings.screen_context_batch_limit == 6


def test_screen_awareness_settings_save_writes_yaml() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_save_cooldown"))

    service.save_screen_awareness_settings(
        ScreenAwarenessSettings(
            enabled=True,
            screen_context_enabled=True,
            check_interval_minutes=3,
            cooldown_minutes=7,
            screen_context_batch_limit=4,
        )
    )

    config = load_yaml_mapping(service.system_config_path)
    assert config["screen_awareness"]["enabled"] is True
    assert config["screen_awareness"]["screen_context_enabled"] is True
    assert config["screen_awareness"]["check_interval_minutes"] == 3
    assert config["screen_awareness"]["cooldown_minutes"] == 7
    assert config["screen_awareness"]["screen_context_batch_limit"] == 4


def test_screen_awareness_settings_save_normalizes_enabled_flag() -> None:
    service = AppSettingsService(_runtime_root_path("screen_awareness_save_sync_enabled"))

    service.save_screen_awareness_settings(
        ScreenAwarenessSettings(
            enabled=True,
            screen_context_enabled=False,
            check_interval_minutes=3,
            cooldown_minutes=7,
        )
    )

    config = load_yaml_mapping(service.system_config_path)
    assert config["screen_awareness"]["enabled"] is True
    assert config["screen_awareness"]["screen_context_enabled"] is False


def test_screen_awareness_screen_context_flag_controls_active_care() -> None:
    enabled_settings = ScreenAwarenessSettings(
        enabled=True,
        screen_context_enabled=True,
        check_interval_minutes=20,
    )
    care_disabled_settings = ScreenAwarenessSettings(
        enabled=False,
        screen_context_enabled=True,
        check_interval_minutes=20,
    )
    screen_disabled_settings = ScreenAwarenessSettings(
        enabled=True,
        screen_context_enabled=False,
        check_interval_minutes=20,
    )

    assert enabled_settings.allows_screen_context()
    assert not care_disabled_settings.allows_screen_context()
    assert not screen_disabled_settings.allows_screen_context()


def test_memory_store_builds_local_mem0_config() -> None:
    root = _runtime_root_path("memory_config")
    store = MemoryStore(
        base_dir=root,
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        scope_id="sakura",
    )

    config = store.build_mem0_config()

    assert config["vector_store"]["provider"] == "qdrant"
    assert config["vector_store"]["config"]["collection_name"] == "sakura_memories"
    assert config["vector_store"]["config"]["embedding_model_dims"] == 384
    assert config["vector_store"]["config"]["path"].endswith("data/memory/qdrant")
    assert config["history_db_path"].endswith("data\\memory\\mem0_history.db") or config[
        "history_db_path"
    ].endswith("data/memory/mem0_history.db")
    assert config["llm"]["config"]["model"] == "test-model"
    assert config["llm"]["config"]["api_key"] == "test-key"
    assert config["llm"]["config"]["openai_base_url"] == "https://api.example.com/v1"
    assert config["embedder"]["provider"] == "huggingface"
    assert "简体中文" in config["custom_instructions"]
    assert "memory/text" in config["custom_instructions"]


def test_memory_download_failure_suggests_manual_import_or_proxy_restart() -> None:
    message = memory_module._format_memory_load_error(
        RuntimeError("offline"),
        embedding_download=True,
    )

    assert "models--sentence-transformers--all-MiniLM-L6-v2.zip" in message
    assert "https://github.com/Rvosy/Sakura/releases/download/v0.9.7/" in message
    assert "设置页手动导入" in message
    assert "开启代理并重启 Sakura" in message


def test_memory_store_reuses_runtime_when_api_settings_unchanged() -> None:
    settings = ApiSettings(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    store = MemoryStore(base_dir=_runtime_root_path("memory_same_settings"), api_settings=settings)
    sentinel = object()
    store._memory = sentinel

    store.set_api_settings(settings)

    assert store._memory is sentinel


@pytest.mark.allow_memory_preload
def test_memory_store_preload_only_creates_runtime_once() -> None:
    class CountingMemoryStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__(base_dir=_runtime_root_path("memory_preload_once"))
            self.create_count = 0

        def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
            self.create_count += 1
            return FakeMem0()

    store = CountingMemoryStore()

    store.preload(wait=True)
    store.preload(wait=True)

    assert store.create_count == 1
    assert store.is_ready()


def test_memory_store_returns_failed_response_for_nonblocking_memory_tools() -> None:
    store = MemoryStore(base_dir=_runtime_root_path("memory_failed_nonblocking"))
    store._load_error = "Cannot send a request, as the client has been closed."

    result = store.search_memory({"query": "偏好"}, wait=False)
    update_result = store.update_memory(
        {"id": "memory-001", "content": "主人喜欢低糖咖啡"},
        wait=False,
    )

    assert result["status"] == "failed"
    assert "普通聊天仍可继续" in result["message"]
    assert result["memories"] == []
    assert "client has been closed" in result["error"]
    assert update_result["status"] == "failed"
    assert "client has been closed" in update_result["error"]


def test_memory_store_downgrades_closed_client_during_search() -> None:
    class ClosedClientMemory:
        def search(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("Cannot send a request, as the client has been closed.")

    store = MemoryStore(base_dir=_runtime_root_path("memory_closed_client_search"))
    store._memory = ClosedClientMemory()

    result = store.search_memory({"query": "偏好"})

    assert result["status"] == "failed"
    assert "普通聊天仍可继续" in result["message"]
    assert "client has been closed" in result["error"]
    assert store._memory is None


def test_memory_store_reload_keeps_old_runtime_until_new_runtime_is_ready() -> None:
    old_settings = ApiSettings("https://old.example.com/v1", "old-key", "old-model")
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")
    ready_to_finish = threading.Event()
    reloaded = threading.Event()

    class BlockingReloadMemoryStore(MemoryStore):
        def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
            ready_to_finish.wait(timeout=2)
            reloaded.set()
            return {"settings": api_settings}

    store = BlockingReloadMemoryStore(
        base_dir=_runtime_root_path("memory_reload_keeps_old"),
        api_settings=old_settings,
    )
    old_runtime = {"settings": old_settings}
    store._memory = old_runtime

    store.reload_api_settings(new_settings, wait=False)

    assert store._memory is old_runtime
    ready_to_finish.set()
    assert reloaded.wait(timeout=2)
    for _ in range(20):
        if store._memory is not old_runtime:
            break
        time.sleep(0.05)
    assert store._memory == {"settings": new_settings}


def test_memory_store_reload_existing_runtime_replaces_only_llm() -> None:
    old_settings = ApiSettings("https://old.example.com/v1", "old-key", "old-model")
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")

    class HotReloadMemoryStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__(
                base_dir=_runtime_root_path("memory_reload_llm_only"),
                api_settings=old_settings,
            )
            self.create_memory_calls = 0
            self.create_llm_calls = 0

        def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
            self.create_memory_calls += 1
            return {"settings": api_settings}

        def _create_memory_llm(self, api_settings):  # type: ignore[no-untyped-def]
            self.create_llm_calls += 1
            return {"settings": api_settings}, {"settings": api_settings}

    store = HotReloadMemoryStore()
    old_config = {"settings": old_settings}
    old_llm = {"settings": old_settings}
    runtime = SimpleNamespace(config=SimpleNamespace(llm=old_config), llm=old_llm)
    store._memory = runtime

    store.reload_api_settings(new_settings, wait=True)

    assert store._memory is runtime
    assert store.create_memory_calls == 0
    assert store.create_llm_calls == 1
    assert runtime.config.llm == {"settings": new_settings}
    assert runtime.llm == {"settings": new_settings}
    assert store._reload_error == ""


def test_memory_store_reload_llm_failure_keeps_old_runtime_llm() -> None:
    old_settings = ApiSettings("https://old.example.com/v1", "old-key", "old-model")
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")

    class FailingLlmReloadMemoryStore(MemoryStore):
        def _create_memory_llm(self, api_settings):  # type: ignore[no-untyped-def]
            raise RuntimeError("llm failed")

    store = FailingLlmReloadMemoryStore(
        base_dir=_runtime_root_path("memory_reload_llm_failure"),
        api_settings=old_settings,
    )
    old_config = {"settings": old_settings}
    old_llm = {"settings": old_settings}
    runtime = SimpleNamespace(config=SimpleNamespace(llm=old_config), llm=old_llm)
    store._memory = runtime

    store.reload_api_settings(new_settings, wait=True)

    assert store._memory is runtime
    assert runtime.config.llm is old_config
    assert runtime.llm is old_llm
    assert store._reload_error == "llm failed"


def test_memory_store_reload_without_runtime_still_creates_memory_client() -> None:
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")

    class ColdReloadMemoryStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__(base_dir=_runtime_root_path("memory_reload_cold"))
            self.create_memory_calls = 0

        def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
            self.create_memory_calls += 1
            return {"settings": api_settings}

    store = ColdReloadMemoryStore()

    store.reload_api_settings(new_settings, wait=True)

    assert store.create_memory_calls == 1
    assert store._memory == {"settings": new_settings}


def test_memory_store_reload_failure_keeps_old_runtime() -> None:
    old_settings = ApiSettings("https://old.example.com/v1", "old-key", "old-model")
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")

    class FailingReloadMemoryStore(MemoryStore):
        def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    store = FailingReloadMemoryStore(
        base_dir=_runtime_root_path("memory_reload_failure"),
        api_settings=old_settings,
    )
    old_runtime = {"settings": old_settings}
    store._memory = old_runtime

    store.reload_api_settings(new_settings, wait=True)

    assert store._memory is old_runtime
    assert store._reload_error == "boom"


def test_memory_store_uses_local_embedding_cache_when_available(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _runtime_root_path("memory_local_embedding_cache")
    cache_root = root / "hf"
    snapshot = (
        cache_root
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_text("fake", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(cache_root))
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)

    store = MemoryStore(base_dir=root)

    config = store.build_mem0_config()

    assert config["embedder"]["config"]["model_kwargs"] == {
        "cache_folder": str(cache_root / "hub"),
        "local_files_only": True,
    }


def test_memory_store_passes_project_embedding_cache_folder(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _runtime_root_path("memory_project_embedding_cache")
    cache_folder = root / "runtime" / "hf-cache" / "hub"
    snapshot = (
        cache_folder
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_text("fake", encoding="utf-8")
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)

    store = MemoryStore(base_dir=root)

    config = store.build_mem0_config()

    assert store.needs_embedding_model_download() is False
    assert config["embedder"]["config"]["model_kwargs"] == {
        "cache_folder": str(cache_folder),
        "local_files_only": True,
    }


def test_memory_store_ignores_incomplete_local_embedding_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _runtime_root_path("memory_incomplete_embedding_cache")
    cache_root = root / "hf"
    snapshot = (
        cache_root
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(cache_root))
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)

    store = MemoryStore(base_dir=root)

    config = store.build_mem0_config()

    assert config["embedder"]["config"]["model_kwargs"] == {
        "cache_folder": str(root / "runtime" / "hf-cache" / "hub"),
    }


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "models--sentence-transformers--all-MiniLM-L6-v2",
        "hub/models--sentence-transformers--all-MiniLM-L6-v2",
        "hf-cache/hub/models--sentence-transformers--all-MiniLM-L6-v2",
    ],
)
def test_memory_store_imports_embedding_model_archive_structures(prefix: str) -> None:
    root = _runtime_root_path("memory_model_import")
    archive_path = root / "models--sentence-transformers--all-MiniLM-L6-v2.zip"
    _write_memory_model_zip(archive_path, prefix=prefix)
    store = MemoryStore(base_dir=root, memory_client=FakeMem0())

    result = store.import_embedding_model_archive(archive_path)

    expected_model_dir = (
        root
        / "runtime"
        / "hf-cache"
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
    )
    assert result.model_dir == expected_model_dir
    assert result.snapshot_count == 1
    assert (expected_model_dir / "snapshots" / "revision" / "model.safetensors").is_file()
    assert store.needs_embedding_model_download() is False


def test_memory_store_downloads_embedding_model_to_project_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _runtime_root_path("memory_model_download")
    calls: list[tuple[str, Path]] = []

    def fake_download(repo_id: str, cache_folder: Path) -> str:
        calls.append((repo_id, cache_folder))
        snapshot = (
            cache_folder
            / "models--sentence-transformers--all-MiniLM-L6-v2"
            / "snapshots"
            / "revision"
        )
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors").write_bytes(b"fake")
        return str(snapshot)

    monkeypatch.setattr(memory_module, "_download_hf_snapshot", fake_download)
    store = MemoryStore(base_dir=root, memory_client=FakeMem0())

    result = store.download_embedding_model()

    expected_cache = root / "runtime" / "hf-cache" / "hub"
    expected_model_dir = expected_cache / "models--sentence-transformers--all-MiniLM-L6-v2"
    assert calls == [("sentence-transformers/all-MiniLM-L6-v2", expected_cache)]
    assert result.cache_folder == expected_cache
    assert result.model_dir == expected_model_dir
    assert result.snapshot_count == 1
    assert store.needs_embedding_model_download() is False


def test_memory_hf_snapshot_download_uses_minimal_file_allowlist(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_snapshot_download(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return "snapshot"

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=fake_snapshot_download),
    )

    result = memory_module._download_hf_snapshot(
        "sentence-transformers/all-MiniLM-L6-v2",
        Path("runtime/hf-cache/hub"),
    )

    assert result == "snapshot"
    assert captured["repo_id"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert captured["allow_patterns"] == list(memory_module.DEFAULT_EMBEDDING_MODEL_ALLOW_PATTERNS)
    assert "onnx/*" not in captured["allow_patterns"]
    assert "openvino/*" not in captured["allow_patterns"]
    assert "tf_model.h5" not in captured["allow_patterns"]
    assert "rust_model.ot" not in captured["allow_patterns"]


def test_memory_store_import_does_not_reload_ready_runtime() -> None:
    root = _runtime_root_path("memory_model_import_ready")
    archive_path = root / "models--sentence-transformers--all-MiniLM-L6-v2.zip"
    _write_memory_model_zip(archive_path, prefix="")
    runtime = ClosableMemoryRuntime()
    store = MemoryStore(base_dir=root, memory_client=runtime)

    store.import_embedding_model_archive(archive_path)

    assert store.is_ready() is True
    assert runtime.memory_closed is False
    assert runtime.client.closed is False


def test_memory_store_reset_runtime_closes_qdrant_client() -> None:
    runtime = ClosableMemoryRuntime()
    store = MemoryStore(base_dir=_runtime_root_path("memory_reset_closes_runtime"))
    store._memory = runtime  # type: ignore[attr-defined]

    store.reset_runtime()

    assert runtime.memory_closed is True
    assert runtime.client.closed is True


def test_memory_store_rejects_incomplete_embedding_model_archive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _runtime_root_path("memory_model_import_incomplete")
    monkeypatch.setenv("HF_HOME", str(root / "empty_hf_home"))
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)
    archive_path = root / "bad.zip"
    _write_memory_model_zip(
        archive_path,
        prefix="models--sentence-transformers--all-MiniLM-L6-v2",
        include_weight=False,
    )
    store = MemoryStore(base_dir=root, memory_client=FakeMem0())

    with pytest.raises(MemoryModelImportError):
        store.import_embedding_model_archive(archive_path)

    assert store.needs_embedding_model_download() is True


def test_memory_store_import_failure_keeps_existing_embedding_cache() -> None:
    root = _runtime_root_path("memory_model_import_keeps_existing")
    existing_snapshot = (
        root
        / "runtime"
        / "hf-cache"
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
        / "existing"
    )
    existing_snapshot.mkdir(parents=True)
    (existing_snapshot / "model.safetensors").write_text("existing", encoding="utf-8")
    archive_path = root / "bad.zip"
    _write_memory_model_zip(
        archive_path,
        prefix="models--sentence-transformers--all-MiniLM-L6-v2",
        include_weight=False,
    )
    store = MemoryStore(base_dir=root, memory_client=FakeMem0())

    with pytest.raises(MemoryModelImportError):
        store.import_embedding_model_archive(archive_path)

    assert (existing_snapshot / "model.safetensors").read_text(encoding="utf-8") == "existing"
    assert store.needs_embedding_model_download() is False


@pytest.mark.parametrize(
    "member_name",
    [
        "../models--sentence-transformers--all-MiniLM-L6-v2/snapshots/revision/model.safetensors",
        "models--other--model/snapshots/revision/model.safetensors",
        "models--sentence-transformers--all-MiniLM-L6-v2/snapshots/revision/model.safetensors",
    ],
)
def test_memory_store_rejects_unsafe_or_wrong_embedding_model_archive(member_name: str) -> None:
    root = _runtime_root_path("memory_model_import_rejects")
    archive_path = root / "bad.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if member_name.startswith("models--sentence-transformers"):
            info = zipfile.ZipInfo(member_name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "target")
        else:
            zf.writestr(member_name, "bad")
    store = MemoryStore(base_dir=root, memory_client=FakeMem0())

    with pytest.raises(MemoryModelImportError):
        store.import_embedding_model_archive(archive_path)


def test_memory_store_create_update_search_and_delete() -> None:
    fake = FakeMem0()
    store = MemoryStore(base_dir=_runtime_root_path("memory"), scope_id="sakura", memory_client=fake)
    created = store.create_memory(
        {
            "content": "Sakura 正在实现自动记忆整理",
        }
    )["memory"]

    updated = store.update_memory(
        {
            "id": created["id"],
            "content": "Sakura 正在实现自动记忆整理和管理页",
        }
    )["memory"]

    search_result = store.search_memory({"query": "管理页"})
    assert search_result["memories"] == [updated]

    removed = store.forget_memory({"id": created["id"]})["forgotten"]
    assert removed["content"] == "Sakura 正在实现自动记忆整理和管理页"
    assert store.list_memories() == []


def test_memory_store_lists_all_memories_without_limit() -> None:
    fake = FakeMem0()
    fake.records = [
        {"id": str(index), "memory": f"memory-{index}", "user_id": "sakura", "metadata": {}}
        for index in range(201)
    ]
    store = MemoryStore(
        base_dir=_runtime_root_path("memory_list_all"), scope_id="sakura", memory_client=fake
    )

    assert len(store.list_memories(limit=None)) == 201


def test_memory_store_forget_missing_id_is_idempotent() -> None:
    fake = FakeMem0RaisesOnMissingDelete()
    store = MemoryStore(base_dir=_runtime_root_path("memory_missing_forget"), scope_id="sakura", memory_client=fake)

    result = store.forget_memory({"id": "missing-id"})

    assert result["forgotten"] == {"id": "missing-id", "content": ""}
    assert result["memory"] == {"id": "missing-id", "content": ""}
    assert result["already_missing"] is True


def test_memory_store_defaults_legacy_records_to_semantic_layer() -> None:
    fake = FakeMem0()
    store = MemoryStore(base_dir=_runtime_root_path("memory_legacy_layer"), scope_id="sakura", memory_client=fake)
    created = store.create_memory({"content": "主人喜欢低糖咖啡"})["memory"]

    assert created["layer"] == "semantic"
    assert created["metadata"]["layer"] == "semantic"
    assert created["scope"] == "sakura"


def test_memory_store_builds_layered_context_with_core_profile() -> None:
    fake = FakeMem0()
    store = MemoryStore(base_dir=_runtime_root_path("memory_context"), scope_id="sakura", memory_client=fake)
    store.create_memory(
        {
            "content": "主人希望默认使用简体中文回答",
            "layer": "procedural",
            "category": "preference",
            "confidence": 0.9,
        }
    )
    store.create_memory(
        {
            "content": "Sakura 正在升级记忆系统",
            "layer": "session",
            "category": "project",
        }
    )
    store.create_memory(
        {
            "content": "主人喜欢低糖咖啡",
            "layer": "semantic",
            "category": "preference",
        }
    )
    store.create_memory(
        {
            "content": "上次讨论过记忆注入策略",
            "layer": "episodic",
            "category": "project",
        }
    )
    store.create_memory(
        {
            "content": "主人长期偏好中文沟通",
            "layer": "core_profile",
            "category": "profile",
        }
    )

    context = store.build_memory_context("", mode="tool")

    assert "【常驻档案】" in context
    assert "主人长期偏好中文沟通" in context
    assert "【当前任务记忆】" in context
    assert "【相关长期事实】" in context
    assert "【协作规则与偏好】" in context
    episodic_context = store.build_memory_context("上次", mode="event")
    assert "【过往事件总结】" in episodic_context


def test_memory_store_searches_and_converts_core_profile() -> None:
    fake = FakeMem0()
    store = MemoryStore(base_dir=_runtime_root_path("memory_core_profile"), scope_id="sakura", memory_client=fake)
    created = store.create_memory({"content": "临时普通记忆"})["memory"]

    converted = store.update_memory(
        {
            "id": created["id"],
            "content": "主人长期偏好中文沟通",
            "layer": "core_profile",
            "category": "profile",
        }
    )["memory"]
    result = store.search_memory({"layer": "core_profile", "query": "中文"})

    assert converted["id"] == "core_profile:sakura"
    assert result["memories"] == [converted]
    assert fake.records == []


def test_memory_store_uses_vendored_mem0_path_first() -> None:
    from app.agent.memory import MEM0_VENDOR_ROOT, install_mem0_vendor

    vendor_root = install_mem0_vendor()

    assert vendor_root == MEM0_VENDOR_ROOT
    assert str(MEM0_VENDOR_ROOT) == sys.path[0]


def test_builtin_registry_registers_mem0_memory_tools() -> None:
    registry = create_builtin_tool_registry(
        _runtime_root_path("builtin_memory_tools"),
        memory=MemoryStore(memory_client=FakeMem0()),
    )

    descriptions = {tool["name"]: tool for tool in registry.describe_tools()}

    assert descriptions["memory_search"]["group"] == "memory"
    assert descriptions["memory_remember"]["group"] == "memory"
    assert descriptions["memory_update"]["group"] == "memory"
    assert descriptions["memory_forget"]["group"] == "memory"


def test_builtin_memory_update_tool_updates_existing_memory() -> None:
    fake = FakeMem0()
    registry = create_builtin_tool_registry(
        _runtime_root_path("builtin_memory_update_tool"),
        memory=MemoryStore(memory_client=fake),
    )
    remember_result = registry.execute(
        "memory_remember",
        {"content": "主人喜欢热咖啡"},
    )
    memory_id = remember_result.content["memory"]["id"]

    update_result = registry.execute(
        "memory_update",
        {
            "memory_id": memory_id,
            "content": "主人喜欢低糖热咖啡",
        },
    )

    assert update_result.success
    assert update_result.content["memory"]["id"] == memory_id
    assert update_result.content["memory"]["content"] == "主人喜欢低糖热咖啡"


def test_tool_registry_requires_confirmation_returns_pending_action() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="open_url",
                description="打开网页",
                handler=lambda _arguments: {"opened": True},
                requires_confirmation=True,
            )
        ]
    )
    registry.set_free_access_enabled(False)

    result = registry.prepare_or_execute(
        "open_url",
        {"url": "https://example.com"},
        "用户要求打开网页",
    )

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "open_url"
    assert result.arguments == {"url": "https://example.com"}


def test_tool_registry_free_access_is_enabled_by_default() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="open_url",
                description="打开网页",
                handler=lambda _arguments: {"opened": True},
                requires_confirmation=True,
            )
        ]
    )
    result = registry.prepare_or_execute("open_url", {"url": "https://example.com"})

    assert not isinstance(result, PendingToolAction)
    assert result.success
    assert result.content == {"opened": True}


def test_tool_registry_free_access_keeps_file_delete_confirmation() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="delete_file",
                description="删除本地文件",
                handler=lambda _arguments: {"deleted": True},
                requires_confirmation=True,
                confirmation_risk="delete_file",
            )
        ]
    )
    registry.set_free_access_enabled(True)

    result = registry.prepare_or_execute("delete_file", {"path": "a.txt"})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "delete_file"


def test_tool_registry_free_access_keeps_high_risk_confirmation() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="run_external_action",
                description="执行高风险动作",
                handler=lambda _arguments: {"done": True},
                requires_confirmation=True,
                risk="high",
            )
        ]
    )
    registry.set_free_access_enabled(True)

    result = registry.prepare_or_execute("run_external_action", {"value": "x"})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "run_external_action"


def test_tool_registry_free_access_allows_playwright_actions() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开可见浏览器页面",
                handler=lambda arguments: {"url": arguments["url"]},
                requires_confirmation=True,
                risk="low",
            ),
            Tool(
                name="playwright_evaluate",
                description="执行页面脚本",
                handler=lambda _arguments: {"done": True},
                requires_confirmation=True,
                risk="high",
            ),
        ]
    )
    registry.set_free_access_enabled(True)

    navigate_result = registry.prepare_or_execute(
        "playwright_navigate",
        {"url": "https://example.com"},
    )
    evaluate_result = registry.prepare_or_execute("playwright_evaluate", {})

    assert not isinstance(navigate_result, PendingToolAction)
    assert navigate_result.success
    assert isinstance(evaluate_result, PendingToolAction)
    assert evaluate_result.tool_name == "playwright_evaluate"


def test_tool_registry_playwright_evaluate_requires_confirmation_without_free_access() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="playwright_evaluate",
                description="执行页面脚本",
                handler=lambda _arguments: {"done": True},
                requires_confirmation=True,
                risk="high",
            ),
        ]
    )
    registry.set_free_access_enabled(False)

    result = registry.prepare_or_execute("playwright_evaluate", {"js_code": "document.title"})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "playwright_evaluate"


def test_tool_registry_describes_group_and_risk_metadata() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="memory_search",
                description="搜索记忆",
                group="memory",
                risk="low",
            )
        ]
    )

    description = registry.describe_tools()[0]

    assert description["group"] == "memory"
    assert description["risk"] == "low"


def test_tool_registry_filters_tools_by_capability() -> None:
    registry = ToolRegistry(
        [
            Tool(name="plain_tool", description="普通工具"),
            create_screen_observation_tool(),
        ]
    )

    visible_without_capability = {tool["name"] for tool in registry.describe_tools(set())}
    visible_with_capability = {
        tool["name"]
        for tool in registry.describe_tools({"screen_observation"})
    }

    assert OBSERVE_SCREEN_TOOL_NAME not in visible_without_capability
    assert OBSERVE_SCREEN_TOOL_NAME in visible_with_capability


def test_mcp_missing_config_does_not_register_tools() -> None:
    root = _runtime_root_path("mcp_missing")
    registry = ToolRegistry()

    provider = register_mcp_tools_from_config(root, registry)

    assert provider is None
    assert registry.describe_tools() == []


def test_mcp_disabled_config_does_not_register_tools() -> None:
    root = _runtime_root_path("mcp_disabled")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text(
        """
enabled: false
servers:
  demo:
    transport: stdio
    command: python
""".strip(),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    provider = register_mcp_tools_from_config(
        root,
        registry,
        bridge_factory=_FakeMCPBridge,
    )

    assert provider is None
    assert registry.describe_tools() == []


def test_mcp_empty_servers_do_not_register_tools() -> None:
    root = _runtime_root_path("mcp_empty")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text("enabled: true\nservers: {}\n", encoding="utf-8")

    registry = ToolRegistry()
    provider = register_mcp_tools_from_config(root, registry)

    assert provider is None
    assert registry.describe_tools() == []


def test_mcp_invalid_config_does_not_break_registry() -> None:
    root = _runtime_root_path("mcp_invalid")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text("enabled: [not_bool]\n", encoding="utf-8")
    registry = ToolRegistry()

    provider = register_mcp_tools_from_config(root, registry)

    assert provider is None
    assert registry.describe_tools() == []


def test_mcp_config_parses_prefix_and_low_risk() -> None:
    config_path = _runtime_root_path("mcp_parse") / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
enabled: true
default_call_timeout: 12
servers:
  demo:
    transport: sse
    url: http://127.0.0.1:8000/sse
    name_prefix: demo_
    risk: low
""".strip(),
        encoding="utf-8",
    )

    config = load_mcp_config(config_path)

    assert config.enabled
    assert config.default_call_timeout == 12
    assert config.servers[0].effective_name_prefix() == "demo_"
    assert config.servers[0].risk == "low"
    assert not config.servers[0].effective_requires_confirmation()


def test_mcp_config_parses_tool_filter_and_policies() -> None:
    config_path = _runtime_root_path("mcp_tool_policy_parse") / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
enabled: true
servers:
  demo:
    transport: stdio
    command: python
    include_tools: ["Read*", "Danger", "DangerousRead"]
    exclude_tools: ["DangerousRead"]
    tool_policies:
      "Read*":
        risk: medium
        requires_confirmation: false
      Danger:
        risk: high
""".strip(),
        encoding="utf-8",
    )

    config = load_mcp_config(config_path)
    server = config.servers[0]

    assert server.allows_tool("ReadPage")
    assert server.allows_tool("Danger")
    assert not server.allows_tool("DangerousRead")
    assert server.effective_tool_risk("ReadPage") == "medium"
    assert not server.effective_tool_requires_confirmation("ReadPage")
    assert server.effective_tool_risk("Danger") == "high"
    assert server.effective_tool_requires_confirmation("Danger")


def test_mcp_provider_registers_prefixed_tools_and_schema() -> None:
    root = _runtime_root_path("mcp_register")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text(
        """
enabled: true
servers:
  demo:
    transport: stdio
    command: python
    name_prefix: demo_
""".strip(),
        encoding="utf-8",
    )
    registry = ToolRegistry()

    provider = register_mcp_tools_from_config(
        root,
        registry,
        bridge_factory=_FakeMCPBridge,
    )

    assert provider is not None
    tool = registry.get("demo_echo")
    assert tool is not None
    assert tool.parameters["properties"]["message"]["type"] == "string"
    assert tool.group == "mcp"
    assert tool.risk == "medium"
    assert tool.requires_confirmation
    provider.close()


def test_mcp_provider_closed_tool_returns_failed_result() -> None:
    root = _runtime_root_path("mcp_closed_tool")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(_write_mcp_config(root, "name_prefix: demo_")),
        bridge_factory=_FakeMCPBridge,
    )
    provider.register_tools(registry)
    tool = registry.get("demo_echo")
    assert tool is not None

    provider.close()
    result = tool.handler({"message": "hello"})

    assert result["isError"] is True
    assert "连接已关闭" in result["error"]


def test_mcp_provider_skips_disabled_server() -> None:
    root = _runtime_root_path("mcp_disabled_server")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(_write_mcp_config(root, "enabled: false\nname_prefix: demo_")),
        bridge_factory=_FakeMCPBridge,
    )

    registered = provider.register_tools(registry)

    assert registered == 0
    assert registry.describe_tools() == []
    provider.close()


def test_mcp_runtime_settings_force_disables_windows_server() -> None:
    config_path = _runtime_root_path("mcp_runtime_override") / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
enabled: true
servers:
  windows:
    enabled: false
    transport: stdio
    command: python
""".strip(),
        encoding="utf-8",
    )

    config = apply_mcp_runtime_settings(
        load_mcp_config(config_path),
        MCPRuntimeSettings(windows_enabled=True),
    )

    assert config.servers[0].name == "windows"
    assert config.servers[0].enabled


def test_settings_service_saves_and_loads_experimental_windows_mcp() -> None:
    service = AppSettingsService(_runtime_root_path("mcp_runtime_save"))

    service.save_mcp_runtime_settings(MCPRuntimeSettings(windows_enabled=True))

    assert service.load_mcp_runtime_settings().windows_enabled


def test_register_mcp_tools_loads_experimental_windows_mcp_when_enabled() -> None:
    root = _runtime_root_path("mcp_register_windows_override")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text(
        """
enabled: true
servers:
  windows:
    enabled: false
    transport: stdio
    command: python
    name_prefix: windows__
""".strip(),
        encoding="utf-8",
    )
    registry = ToolRegistry()

    provider = register_mcp_tools_from_config(
        root,
        registry,
        bridge_factory=_FakeMCPBridge,
        runtime_settings=MCPRuntimeSettings(windows_enabled=True),
    )

    assert provider is not None
    assert registry.get("windows__echo") is not None
    provider.close()


def test_mcp_provider_filters_tools_and_applies_tool_policies() -> None:
    root = _runtime_root_path("mcp_tool_policy_register")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(
            _write_mcp_config(
                root,
                """
name_prefix: demo_
risk: high
include_tools: ["read_state", "click_target"]
tool_policies:
  read_state:
    risk: medium
    requires_confirmation: false
""".strip(),
            )
        ),
        bridge_factory=_MultiToolMCPBridge,
    )

    provider.register_tools(registry)

    read_tool = registry.get("demo_read_state")
    click_tool = registry.get("demo_click_target")
    assert read_tool is not None
    assert click_tool is not None
    assert registry.get("demo_hidden_tool") is None
    assert read_tool.risk == "medium"
    assert not read_tool.requires_confirmation
    assert click_tool.risk == "high"
    assert click_tool.requires_confirmation
    provider.close()


def test_mcp_tool_call_uses_external_name() -> None:
    root = _runtime_root_path("mcp_call")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(_write_mcp_config(root, "name_prefix: demo_\nrisk: low")),
        bridge_factory=_FakeMCPBridge,
    )
    provider.register_tools(registry)

    result = registry.execute("demo_echo", {"message": "hello"})

    assert result.success
    assert result.content["server_tool_name"] == "echo"
    assert result.content["arguments"] == {"message": "hello"}
    provider.close()


def test_mcp_tool_call_exception_returns_failed_result() -> None:
    root = _runtime_root_path("mcp_fail")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(_write_mcp_config(root, "name_prefix: fail_\nrisk: low")),
        bridge_factory=_FailingMCPBridge,
    )
    provider.register_tools(registry)

    result = registry.execute("fail_echo", {"message": "hello"})

    assert not result.success
    assert "MCP 调用失败" in result.error
    provider.close()


def test_mcp_high_risk_still_requires_confirmation_with_free_access() -> None:
    root = _runtime_root_path("mcp_high_risk")
    registry = ToolRegistry()
    provider = MCPToolProvider(
        load_mcp_config(_write_mcp_config(root, "name_prefix: dangerous_\nrisk: high")),
        bridge_factory=_FakeMCPBridge,
    )
    provider.register_tools(registry)
    registry.set_free_access_enabled(True)

    result = registry.prepare_or_execute("dangerous_echo", {"message": "hello"})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "dangerous_echo"
    provider.close()


def test_builtin_registry_excludes_internal_browser_tools() -> None:
    registry = create_builtin_tool_registry(Path(__file__).resolve().parents[2])

    names = {tool["name"] for tool in registry.describe_tools()}

    assert {
        "browser_open_url",
        "browser_get_content",
        "browser_scroll",
        "browser_click",
        "browser_get_state",
    }.isdisjoint(names)
    assert OBSERVE_SCREEN_TOOL_NAME in names
    assert "open_url" in names


def test_playwright_plugin_registers_native_browser_tools() -> None:
    registry = create_builtin_tool_registry(Path(__file__).resolve().parents[2])
    manager = PluginManager(Path(__file__).resolve().parents[2])

    manager.load_from_config(registry)
    names = {tool.name for tool in registry.all()}

    assert {
        "playwright_navigate",
        "playwright_get_text",
        "playwright_search_web",
        "playwright_screenshot",
        "playwright_click",
        "playwright_fill",
        "playwright_evaluate",
    }.issubset(names)
    assert registry.get("playwright_evaluate").requires_confirmation  # type: ignore[union-attr]
    manager.shutdown_all()


def test_playwright_search_web_returns_structured_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class TitleEl:
        def inner_text(self) -> str:
            return "萌娘百科 - 二阶堂真红"

    class SnippetEl:
        def inner_text(self) -> str:
            return "二阶堂真红是《五彩斑斓的世界》系列角色。"

    class DisplayUrlEl:
        def inner_text(self) -> str:
            return "zh.moegirl.org.cn"

    class LinkEl:
        def get_attribute(self, name: str) -> str | None:
            return "https://zh.moegirl.org.cn/二阶堂真红" if name == "href" else None

    class ResultEl:
        def query_selector(self, selector: str):  # type: ignore[no-untyped-def]
            if selector == "h2":
                return TitleEl()
            if selector == "p":
                return SnippetEl()
            if selector == ".b_attribution cite":
                return DisplayUrlEl()
            if selector == "h2 a":
                return LinkEl()
            return None

    class Page:
        url = "https://www.bing.com/search?q=%E4%BA%8C%E9%98%B6%E5%A0%82%E7%9C%9F%E7%BA%A2"
        navigated: list[str] = []

        def goto(self, url, **_kwargs):  # type: ignore[no-untyped-def]
            self.navigated.append(url)
            return None

        def query_selector_all(self, selector: str):  # type: ignore[no-untyped-def]
            return [ResultEl()] if selector == "li.b_algo" else []

    from plugins.playwright_browser import browser

    monkeypatch.setattr(browser, "_page", None)
    monkeypatch.setattr(browser, "_bg_executor", None)
    monkeypatch.setattr(browser, "_browser_thread_id", None)
    monkeypatch.setattr(browser, "_use_bg_thread", True)
    page = Page()
    monkeypatch.setattr(browser, "_ensure_browser", lambda: page)

    result = browser.search_web("二阶堂真红")

    assert page.navigated == ["https://www.bing.com/search?q=%E4%BA%8C%E9%98%B6%E5%A0%82%E7%9C%9F%E7%BA%A2"]
    assert "萌娘百科 - 二阶堂真红" in result
    assert "二阶堂真红是《五彩斑斓的世界》系列角色。" in result
    assert "zh.moegirl.org.cn" in result
    browser.shutdown_browser()


def test_playwright_search_web_registry_keeps_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class TitleEl:
        def inner_text(self) -> str:
            return "萌娘百科 - 二阶堂真红"

    class SnippetEl:
        def inner_text(self) -> str:
            return "二阶堂真红是《五彩斑斓的世界》系列角色。"

    class DisplayUrlEl:
        def inner_text(self) -> str:
            return "zh.moegirl.org.cn"

    class LinkEl:
        def get_attribute(self, name: str) -> str | None:
            return "https://zh.moegirl.org.cn/二阶堂真红" if name == "href" else None

    class ResultEl:
        def query_selector(self, selector: str):  # type: ignore[no-untyped-def]
            if selector == "h2":
                return TitleEl()
            if selector == "p":
                return SnippetEl()
            if selector == ".b_attribution cite":
                return DisplayUrlEl()
            if selector == "h2 a":
                return LinkEl()
            return None

    class Page:
        def goto(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return None

        def query_selector_all(self, selector: str):  # type: ignore[no-untyped-def]
            return [ResultEl()] if selector == "li.b_algo" else []

    from plugins.playwright_browser import browser

    monkeypatch.setattr(browser, "_page", None)
    monkeypatch.setattr(browser, "_bg_executor", None)
    monkeypatch.setattr(browser, "_browser_thread_id", None)
    monkeypatch.setattr(browser, "_use_bg_thread", False)
    monkeypatch.setattr(browser, "_ensure_browser", lambda: Page())

    registry = ToolRegistry()
    manager = PluginManager(Path(__file__).resolve().parents[2])
    manager.load_from_config(registry)
    try:
        result = registry.execute("playwright_search_web", {"query": "二阶堂真红 百科"})
    finally:
        manager.shutdown_all()

    assert result.success
    assert "萌娘百科 - 二阶堂真红" in result.content


def test_playwright_browser_operations_stay_on_single_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugins.playwright_browser import browser

    class Page:
        def __init__(self) -> None:
            self.owner_thread_id = threading.get_ident()

        def is_closed(self) -> bool:
            return False

        def inner_text(self, _selector: str) -> str:
            assert threading.get_ident() == self.owner_thread_id
            return "页面文本"

    page_holder: dict[str, Page] = {}
    observed_thread_ids: list[int] = []

    def fake_ensure_browser() -> Page:
        observed_thread_ids.append(threading.get_ident())
        if "page" not in page_holder:
            page_holder["page"] = Page()
        return page_holder["page"]

    monkeypatch.setattr(browser, "_ensure_browser", fake_ensure_browser)
    monkeypatch.setattr(browser, "_bg_executor", None)
    monkeypatch.setattr(browser, "_browser_thread_id", None)
    monkeypatch.setattr(browser, "_use_bg_thread", True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: browser.get_text(), range(2)))

    assert results == ["页面文本", "页面文本"]
    assert len(set(observed_thread_ids)) == 1
    browser.shutdown_browser()


def test_open_url_stays_confirmation_required() -> None:
    registry = create_builtin_tool_registry(Path(__file__).resolve().parents[2])
    registry.set_free_access_enabled(False)

    result = registry.prepare_or_execute("open_url", {"url": "https://example.com"})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "open_url"


def test_browser_screenshot_fallback_is_attached_as_image_url() -> None:
    result = ToolExecutionResult(
        tool_name="playwright_screenshot",
        success=True,
        content={
            "url": "https://example.com",
            "title": "Canvas Page",
            "text": "",
            "screenshot_data_url": "data:image/jpeg;base64,abc123",
            "screenshot_fallback": True,
        },
    )

    message = _build_tool_results_message([result], include_images=True)

    content = message["content"]
    assert isinstance(content, list)
    assert content[1] == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,abc123",
            "detail": "low",
        },
    }
    assert "screenshot_data_url" not in content[0]["text"]
    assert "screenshot_attached" in content[0]["text"]


def test_browser_screenshot_fallback_is_not_attached_without_vision() -> None:
    result = ToolExecutionResult(
        tool_name="playwright_screenshot",
        success=True,
        content={
            "url": "https://example.com",
            "title": "Canvas Page",
            "text": "",
            "screenshot_data_url": "data:image/jpeg;base64,abc123",
        },
    )

    message = _build_tool_results_message([result], include_images=False)

    assert isinstance(message["content"], str)
    assert "screenshot_data_url" not in message["content"]
    assert "screenshot_attached" in message["content"]


def test_tool_result_for_model_truncates_large_content() -> None:
    result = ToolExecutionResult(
        tool_name="playwright_get_text",
        success=True,
        content={
            "url": "https://example.com",
            "text": "x" * 7000,
        },
    )

    redacted = _redact_tool_result_for_model(result)

    content = redacted["content"]
    assert isinstance(content, dict)
    assert content["truncated"] is True
    assert content["original_chars"] > 6000
    assert "head" in content
    assert "tail" in content


def test_agent_runtime_can_continue_tool_loop_after_tool_results() -> None:
    class MultiStepClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.messages: list[list[dict[str, object]]] = []
            self.final_chat_called = False

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            self.messages.append(messages)
            call_index = len(self.prompts)
            if call_index == 1:
                return (
                    '{"reply":{"segments":[{"ja":"まず確認するね。","zh":"我先确认一下。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"first_tool","arguments":{"value":"start"},"reason":"先取第一步结果"}]}'
                )
            if call_index == 2:
                assert "first_result" in str(messages)
                return (
                    '{"reply":{"segments":[{"ja":"次も見るね。","zh":"我再看下一步。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"second_tool","arguments":{"value":"next"},"reason":"根据第一步结果继续"}]}'
                )
            assert "second_result" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"二つとも確認できたよ。","zh":"两步都确认好了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.final_chat_called = True
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"fallback","zh":"fallback","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="first_tool",
                description="第一步工具",
                handler=lambda arguments: {"first_result": arguments["value"]},
            ),
            Tool(
                name="second_tool",
                description="第二步工具",
                handler=lambda arguments: {"second_result": arguments["value"]},
            ),
        ]
    )
    client = MultiStepClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )
    progress_replies = []

    result = runtime.handle_user_message(
        [{"role": "user", "content": "连续处理这个任务"}],
        progress_callback=progress_replies.append,
    )

    assert result.reply.translation == "两步都确认好了。"
    assert [progress.reply.translation for progress in progress_replies] == ["我先确认一下。"]
    assert [action.payload["tool_name"] for action in result.actions] == ["first_tool", "second_tool"]
    assert len(client.prompts) == 3
    assert not client.final_chat_called
    # 步数等易变状态已移出系统提示，改由 api_client 注入 runtime_context；
    # 因此静态系统提示在多步之间保持一致（利于前缀缓存与角色稳定）。
    assert "这是第 1 步" not in client.prompts[0]
    assert client.prompts[0] == client.prompts[1] == client.prompts[2]


def test_agent_runtime_stops_tool_loop_at_turn_limit() -> None:
    class RepeatingToolClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.chat_messages: list[dict[str, object]] = []

        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            return (
                '{"reply":{"segments":[{"ja":"続けるね。","zh":"继续。","tone":"中性"}]},'
                '"tool_calls":[{"name":"echo_tool","arguments":{},"reason":"测试上限"},'
                '{"name":"echo_tool","arguments":{},"reason":"测试上限"},'
                '{"name":"echo_tool","arguments":{},"reason":"测试上限"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.chat_messages = messages
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"上限で止めたよ。","zh":"已经按上限停止了。","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="echo_tool",
                description="回显工具",
                handler=lambda _arguments: {"ok": True},
            )
        ]
    )
    client = RepeatingToolClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "一直调用工具"}])

    assert result.reply.translation == "已经按上限停止了。"
    assert len(
        [
            action
            for action in result.actions
            if action.payload["tool_name"] == "echo_tool" and action.payload["success"]
        ]
    ) == 8
    assert any(action.payload["tool_name"] == "runtime" for action in result.actions)
    assert client.raw_calls == 3
    assert "已跳过" in str(client.chat_messages)


def test_agent_runtime_emits_progress_before_pending_confirmation() -> None:
    class ConfirmToolClient:
        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return (
                '{"reply":{"segments":[{"ja":"開く前に確認するね。","zh":"打开前我先确认一下。","tone":"中性"}]},'
                '"tool_calls":[{"name":"open_tool","arguments":{"url":"https://example.com"},"reason":"需要打开网页"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(
                name="open_tool",
                description="需要确认的工具",
                handler=lambda arguments: {"opened": arguments["url"]},
                requires_confirmation=True,
                risk="high",
            )
        ]
    )
    runtime = AgentRuntime(
        api_client=ConfirmToolClient(),  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )
    progress_replies = []

    result = runtime.handle_user_message(
        [{"role": "user", "content": "打开这个网页"}],
        progress_callback=progress_replies.append,
    )

    assert [progress.reply.translation for progress in progress_replies] == ["打开前我先确认一下。"]
    assert any(action.type == "pending_action" for action in result.actions)


def test_browser_page_mode_hides_windows_mouse_tools_from_planner() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompt = ""
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompt = system_prompt
            return (
                '{"reply":{"segments":[{"ja":"確認できたよ。","zh":"确认好了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}),
            Tool(name="playwright_click", description="浏览器点击", handler=lambda _arguments: {}),
            Tool(name="playwright_fill", description="浏览器输入", handler=lambda _arguments: {}),
            Tool(name="windows__Snapshot", description="桌面快照", handler=lambda _arguments: {}),
            Tool(name="windows__Click", description="桌面点击", handler=lambda _arguments: {}),
        ]
    )
    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    runtime.handle_user_message(
        [
            {"role": "user", "content": "打开浏览器搜一下二阶堂真红"},
            {
                "role": "user",
                "content": '工具执行结果如下：[{"tool_name":"playwright_fill","success":true}]',
            },
            {"role": "user", "content": "帮我点进百科看看"},
        ]
    )

    assert "playwright_get_text" in client.tool_names
    assert "playwright_click" in client.tool_names
    assert "playwright_fill" in client.tool_names
    assert "windows__Snapshot" not in client.tool_names
    assert "windows__Click" not in client.tool_names


def test_visible_browser_request_hides_background_web_tools_from_planner() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompt = ""
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompt = system_prompt
            return (
                '{"reply":{"segments":[{"ja":"開くね。","zh":"我打开。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(name="playwright_navigate", description="打开浏览器页面", handler=lambda _arguments: {}),
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}),
            Tool(name="playwright_fill", description="浏览器输入", handler=lambda _arguments: {}),
            Tool(name="playwright_search_web", description="浏览器搜索", handler=lambda _arguments: {}),
            Tool(name="web__web_search", description="后台搜索", handler=lambda _arguments: {}),
            Tool(name="web__fetch_url", description="后台读取网页", handler=lambda _arguments: {}),
        ]
    )
    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    runtime.handle_user_message([{"role": "user", "content": "打开浏览器搜索一下二阶堂真红,看看百科怎么描述的"}])

    assert "playwright_navigate" in client.tool_names
    assert "playwright_get_text" in client.tool_names
    assert "playwright_fill" in client.tool_names
    assert "playwright_search_web" in client.tool_names
    assert "web__web_search" not in client.tool_names
    assert "web__fetch_url" not in client.tool_names
    assert "playwright_search_web" in client.prompt
    assert "不要先打开搜索首页再操作输入框" in client.prompt


def test_browser_navigate_auto_snapshots_and_fast_forwards_lookup_reply() -> None:
    class BrowserLookupClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.chat_called = False
            self.chat_messages: list[dict[str, object]] = []

        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            return (
                '{"reply":{"segments":[{"ja":"開くね。","zh":"我打开看看。","tone":"中性"}]},'
                '"tool_calls":[{"name":"playwright_navigate",'
                '"arguments":{"url":"https://zh.moegirl.org.cn/二阶堂真红"},'
                '"reason":"打开目标百科页面"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.chat_called = True
            self.chat_messages = messages
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"確認できたよ。","zh":"已经确认页面内容了。","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda arguments: {"url": arguments["url"], "title": "二阶堂真红"},
            ),
            Tool(
                name="playwright_get_text",
                description="读取浏览器文本",
                handler=lambda arguments: {
                    "text": "二阶堂真红是《五彩斑斓的世界》系列的女主角，页面包含角色信息。",
                },
            ),
        ]
    )
    client = BrowserLookupClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message(
        [{"role": "user", "content": "打开浏览器搜索一下二阶堂真红的萌娘百科，告诉我她的信息"}]
    )

    assert result.reply.translation == "已经确认页面内容了。"
    assert [action.payload["tool_name"] for action in result.actions] == [
        "playwright_navigate",
        "playwright_get_text",
    ]
    assert client.raw_calls == 1
    assert client.chat_called
    assert "二阶堂真红是《五彩斑斓的世界》系列的女主角" in str(client.chat_messages)


def test_browser_navigate_does_not_duplicate_planned_snapshot() -> None:
    class BrowserSnapshotClient:
        def __init__(self) -> None:
            self.raw_calls = 0

        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"確認するね。","zh":"我确认一下。","tone":"中性"}]},'
                    '"tool_calls":['
                    '{"name":"playwright_navigate","arguments":{"url":"https://example.com"},"reason":"打开页面"},'
                    '{"name":"playwright_get_text","arguments":{},"reason":"读取页面"}'
                    "]} "
                )
            return (
                '{"reply":{"segments":[{"ja":"読めたよ。","zh":"读到了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    snapshot_calls: list[dict[str, object]] = []
    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda _arguments: {"url": "https://example.com"},
            ),
            Tool(
                name="playwright_get_text",
                description="读取浏览器文本",
                handler=lambda arguments: snapshot_calls.append(arguments) or {"text": "Example Page"},
            ),
        ]
    )
    client = BrowserSnapshotClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "打开浏览器看一下这个页面"}])

    assert result.reply.translation == "读到了。"
    assert [action.payload["tool_name"] for action in result.actions] == [
        "playwright_navigate",
        "playwright_get_text",
    ]
    assert snapshot_calls == [{}]
    assert client.raw_calls == 2


def test_browser_navigate_failure_does_not_auto_snapshot() -> None:
    class BrowserFailureClient:
        def __init__(self) -> None:
            self.raw_calls = 0

        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"開くね。","zh":"我打开。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_navigate",'
                    '"arguments":{"url":"https://example.invalid"},"reason":"打开页面"}]}'
                )
            return (
                '{"reply":{"segments":[{"ja":"開けなかった。","zh":"页面没打开。","tone":"困惑"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    snapshot_called = False

    def snapshot(_arguments):  # type: ignore[no-untyped-def]
        nonlocal snapshot_called
        snapshot_called = True
        return {"text": "不应读取"}

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda _arguments: (_ for _ in ()).throw(RuntimeError("导航失败")),
            ),
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=snapshot),
        ]
    )
    client = BrowserFailureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "打开浏览器查一下页面信息"}])

    assert result.reply.translation == "页面没打开。"
    assert [action.payload["tool_name"] for action in result.actions] == ["playwright_navigate"]
    assert not snapshot_called
    assert client.raw_calls == 2


def test_browser_interaction_request_auto_snapshots_without_fast_forward() -> None:
    class BrowserInteractionClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.chat_called = False
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            self.messages.append(messages)
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"開くね。","zh":"我打开。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_navigate",'
                    '"arguments":{"url":"https://example.com/login"},"reason":"打开登录页"}]}'
                )
            assert "playwright_get_text" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"次の操作を確認したよ。","zh":"我确认下一步操作了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.chat_called = True
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"fallback","zh":"fallback","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda arguments: {"url": arguments["url"]},
            ),
            Tool(
                name="playwright_get_text",
                description="读取浏览器文本",
                handler=lambda _arguments: {"text": "Login Page 用户名 密码 登录按钮"},
            ),
        ]
    )
    client = BrowserInteractionClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "打开这个页面并点击登录按钮"}])

    assert result.reply.translation == "我确认下一步操作了。"
    assert [action.payload["tool_name"] for action in result.actions] == [
        "playwright_navigate",
        "playwright_get_text",
    ]
    assert client.raw_calls == 2
    assert not client.chat_called


def test_browser_lookup_does_not_fast_forward_when_auto_snapshot_has_no_content() -> None:
    class EmptySnapshotClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.chat_called = False

        def complete_raw(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"開くね。","zh":"我打开。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_navigate",'
                    '"arguments":{"url":"https://example.com"},"reason":"打开页面"}]}'
                )
            assert "playwright_get_text" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"もう少し確認するね。","zh":"我再确认一下。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.chat_called = True
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"fallback","zh":"fallback","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda arguments: {"url": arguments["url"]},
            ),
            Tool(
                name="playwright_get_text",
                description="读取浏览器文本",
                handler=lambda _arguments: {"text": ""},
            ),
        ]
    )
    client = EmptySnapshotClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "打开浏览器查一下页面信息"}])

    assert result.reply.translation == "我再确认一下。"
    assert client.raw_calls == 2
    assert not client.chat_called


def test_browser_lookup_does_not_fast_forward_on_search_results_page() -> None:
    class SearchResultsClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.chat_called = False

        def complete_raw(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"検索するね。","zh":"我搜索一下。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_navigate",'
                    '"arguments":{"url":"https://www.google.com/search?q=二階堂真紅+百科"},'
                    '"reason":"打开搜索结果"}]}'
                )
            assert "google.com/search" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"結果をもう少し見るね。","zh":"我再看一下搜索结果。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.chat_called = True
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"fallback","zh":"fallback","tone":"中性"}]}'
            )

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_navigate",
                description="打开浏览器页面",
                handler=lambda arguments: {"url": arguments["url"]},
            ),
            Tool(
                name="playwright_get_text",
                description="读取浏览器文本",
                handler=lambda _arguments: {
                    "text": (
                        "### Page\n"
                        "- Page URL: https://www.google.com/search?q=二階堂真紅+百科\n"
                        "- Page Title: 二階堂真紅 百科 - Google Search\n"
                        "搜索结果包含若干链接。"
                    )
                },
            ),
        ]
    )
    client = SearchResultsClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message([{"role": "user", "content": "打开浏览器查查二阶堂真红的百科"}])

    assert result.reply.translation == "我再看一下搜索结果。"
    assert client.raw_calls == 2
    assert not client.chat_called


def test_visible_browser_request_blocks_background_search_and_continues_planning() -> None:
    class MisroutedSearchClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            self.messages.append(messages)
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"検索するね。","zh":"我来搜索。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"web__web_search","arguments":{"query":"二阶堂真红 百科"},"reason":"搜索百科"}]}'
                )
            assert "用户明确要求打开浏览器" in str(messages)
            assert "playwright_navigate" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"ブラウザで開くね。","zh":"我改用浏览器打开。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    called = {"web_search": False}

    def web_search(_arguments):  # type: ignore[no-untyped-def]
        called["web_search"] = True
        return {"results": []}

    registry = ToolRegistry(
        [
            Tool(name="playwright_navigate", description="打开浏览器页面", handler=lambda _arguments: {}),
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}),
            Tool(name="playwright_fill", description="浏览器输入", handler=lambda _arguments: {}),
            Tool(name="web__web_search", description="后台搜索", handler=web_search),
        ]
    )
    client = MisroutedSearchClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message(
        [{"role": "user", "content": "打开浏览器搜索一下二阶堂真红,看看百科怎么描述的"}]
    )

    assert result.reply.translation == "我改用浏览器打开。"
    assert [action.payload["tool_name"] for action in result.actions] == ["runtime"]
    assert not called["web_search"]
    assert client.raw_calls == 2


def test_visible_browser_request_keeps_blocking_background_search_after_playwright_failure() -> None:
    class BrowserFailureThenWebFallbackClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.tool_names: list[str] = []
            self.tool_name_batches: list[list[str]] = []
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            self.messages.append(messages)
            if self.raw_calls == 1:
                assert "web__web_search" not in self.tool_names
                return (
                    '{"reply":{"segments":[{"ja":"調べるね。","zh":"我查一下。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_search_web","arguments":{"query":"二阶堂真红 百科"},"reason":"可见搜索"}]}'
                )
            if self.raw_calls == 2:
                assert "web__web_search" not in self.tool_names
                assert "本轮是显式可见浏览器任务" in system_prompt
                return (
                    '{"reply":{"segments":[{"ja":"別の方法で探すね。","zh":"我换个方式找。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"web__web_search","arguments":{"query":"二阶堂真红 百科"},"reason":"后台搜索"}]}'
                )
            assert "已阻止 web__web_search" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"ブラウザ検索が失敗した。裏側の検索には切り替えない。",'
                '"zh":"浏览器搜索失败了。我不会切到后台搜索。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    called = {"playwright": False, "web_search": False}

    def playwright_search(_arguments):  # type: ignore[no-untyped-def]
        called["playwright"] = True
        raise RuntimeError("browser broke")

    def web_search(_arguments):  # type: ignore[no-untyped-def]
        called["web_search"] = True
        return {"results": []}

    registry = ToolRegistry(
        [
            Tool(name="playwright_search_web", description="浏览器搜索", handler=playwright_search, group="browser"),
            Tool(name="playwright_navigate", description="打开浏览器页面", handler=lambda _arguments: {}, group="browser"),
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}, group="browser"),
            Tool(name="web__web_search", description="后台搜索", handler=web_search, group="mcp"),
        ]
    )
    client = BrowserFailureThenWebFallbackClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message(
        [{"role": "user", "content": "用浏览器查查看二阶堂真红的百科吧,我们一起来看看"}]
    )

    assert result.reply.translation == "浏览器搜索失败了。我不会切到后台搜索。"
    assert called["playwright"]
    assert not called["web_search"]
    assert [action.payload["tool_name"] for action in result.actions] == [
        "playwright_search_web",
        "runtime",
    ]
    assert client.raw_calls == 3


def test_plain_lookup_still_exposes_background_web_tools() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompt = ""
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompt = system_prompt
            return (
                '{"reply":{"segments":[{"ja":"調べるね。","zh":"我查一下。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(name="playwright_navigate", description="打开浏览器页面", handler=lambda _arguments: {}),
            Tool(name="web__web_search", description="后台搜索", handler=lambda _arguments: {}),
            Tool(name="web__fetch_url", description="后台读取网页", handler=lambda _arguments: {}),
        ]
    )
    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    runtime.handle_user_message([{"role": "user", "content": "查一下二阶堂真红百科怎么描述的"}])

    assert "web__web_search" in client.tool_names
    assert "web__fetch_url" in client.tool_names


def test_browser_page_mode_blocks_windows_click_and_continues_planning() -> None:
    class MisroutedClickClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, _system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            self.messages.append(messages)
            if self.raw_calls == 1:
                return (
                    '{"reply":{"segments":[{"ja":"クリックするね。","zh":"我来点击。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"windows__Click","arguments":{"loc":[180,615]},"reason":"点击搜索结果链接"}]}'
                )
            assert "浏览器页面内部操作" in str(messages)
            assert "playwright_get_text" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"ブラウザの操作に戻したよ。","zh":"已经切回浏览器操作了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    called = {"windows_click": False}

    def windows_click(_arguments):  # type: ignore[no-untyped-def]
        called["windows_click"] = True
        return {"clicked": True}

    registry = ToolRegistry(
        [
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}),
            Tool(name="playwright_click", description="浏览器点击", handler=lambda _arguments: {}),
            Tool(
                name="windows__Click",
                description="桌面点击",
                handler=windows_click,
                requires_confirmation=True,
                risk="high",
            ),
        ]
    )
    client = MisroutedClickClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_user_message(
        [
            {"role": "user", "content": "打开浏览器搜一下二阶堂真红"},
            {
                "role": "user",
                "content": '工具执行结果如下：[{"tool_name":"playwright_fill","success":true}]',
            },
            {"role": "user", "content": "帮我点进百科看看"},
        ]
    )

    assert result.reply.translation == "已经切回浏览器操作了。"
    assert [action.payload["tool_name"] for action in result.actions] == ["runtime"]
    assert not any(action.type == "pending_action" for action in result.actions)
    assert not called["windows_click"]
    assert client.raw_calls == 2


def test_desktop_task_still_exposes_windows_mouse_tools() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompt = ""
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompt = system_prompt
            return (
                '{"reply":{"segments":[{"ja":"見ておくね。","zh":"我看一下。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(name="playwright_get_text", description="读取浏览器文本", handler=lambda _arguments: {}),
            Tool(name="windows__Snapshot", description="桌面快照", handler=lambda _arguments: {}),
            Tool(name="windows__Click", description="桌面点击", handler=lambda _arguments: {}),
        ]
    )
    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    runtime.handle_user_message([{"role": "user", "content": "帮我打开此电脑图标"}])

    assert "windows__Snapshot" in client.tool_names
    assert "windows__Click" in client.tool_names


def test_agent_runtime_continues_after_confirmed_action_with_context() -> None:
    class ConfirmedContinuationClient:
        def __init__(self) -> None:
            self.raw_calls = 0
            self.prompts: list[str] = []
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.raw_calls += 1
            self.prompts.append(system_prompt)
            self.messages.append(messages)
            if self.raw_calls == 1:
                assert "帮我打开桌面上的回收站" in str(messages)
                assert "Pressed win+r." in str(messages)
                assert "确认动作续接规则" in system_prompt
                return (
                    '{"reply":{"segments":[{"ja":"続けて入力するね。","zh":"我继续输入命令。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"type_tool","arguments":{"text":"shell:RecycleBinFolder"},"reason":"运行窗口已打开，继续输入回收站命令"}]}'
                )
            assert "shell:RecycleBinFolder" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"開けたよ。","zh":"已经打开了。","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("确认动作有上下文时应回到工具循环，而不是直接总结。")

    registry = ToolRegistry(
        [
            Tool(
                name="shortcut_tool",
                description="快捷键工具",
                handler=lambda _arguments: "Pressed win+r.",
            ),
            Tool(
                name="type_tool",
                description="输入工具",
                handler=lambda arguments: {"typed": arguments["text"]},
            ),
        ]
    )
    client = ConfirmedContinuationClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )
    action = PendingToolAction.create(
        "shortcut_tool",
        {"shortcut": "win+r"},
        "通过运行窗口输入命令来直接打开回收站。",
    ).with_continuation_messages(
        [
            {"role": "user", "content": "帮我打开桌面上的回收站"},
            {
                "role": "assistant",
                "content": "我会先打开运行窗口，然后输入回收站命令。",
            },
        ]
    )

    result = runtime.handle_confirmed_action(action)

    assert result.reply.translation == "已经打开了。"
    assert [agent_action.payload["tool_name"] for agent_action in result.actions] == [
        "shortcut_tool",
        "type_tool",
    ]
    assert client.raw_calls == 2


def test_agent_runtime_extracts_planner_json_from_mixed_output() -> None:
    class MixedPlannerClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete_raw(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return (
                    "（先说一句自然话，然后错误地贴了工具规划）\n"
                    "`observe_screen` 获取上下文\n"
                    '{'
                    '"reply":{"segments":[{"ja":"少し確認するね。","zh":"我先确认一下。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"echo_tool","arguments":{"value":"ok"},"reason":"补充上下文"}]'
                    "}"
                )
            return '{"reply":{"segments":[{"ja":"確認できたよ。","zh":"确认好了。","tone":"中性"}]},"tool_calls":[]}'

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(
                name="echo_tool",
                description="回显工具",
                handler=lambda arguments: {"value": arguments["value"]},
            )
        ]
    )
    client = MixedPlannerClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )
    progress_replies = []

    result = runtime.handle_user_message(
        [{"role": "user", "content": "下午好"}],
        progress_callback=progress_replies.append,
    )

    assert [progress.reply.translation for progress in progress_replies] == ["我先确认一下。"]
    assert result.reply.translation == "确认好了。"
    assert "tool_calls" not in result.reply.text
    assert [action.payload["tool_name"] for action in result.actions] == ["echo_tool"]


def test_trim_messages_for_model_keeps_recent_messages_without_mutating_history() -> None:
    messages = [
        {"role": "user", "content": f"message {index}"}
        for index in range(MAX_MODEL_CONTEXT_MESSAGES + 5)
    ]

    trimmed = trim_messages_for_model(messages)

    assert len(trimmed) == MAX_MODEL_CONTEXT_MESSAGES
    assert trimmed[0]["content"] == "message 5"
    assert len(messages) == MAX_MODEL_CONTEXT_MESSAGES + 5


def test_autonomous_screen_observation_can_request_screen_without_explicit_user_command() -> None:
    class ScreenRequestClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return (
                '{"reply":{"segments":[{"ja":"見るね。","zh":"我看看。","tone":"请求"}]},'
                '"tool_calls":[{"name":"observe_screen","arguments":{},"reason":"需要当前画面"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    client = ScreenRequestClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)
    progress_replies = []
    result = runtime.handle_user_message(
        [{"role": "user", "content": "你觉得我现在是不是卡住了？"}],
        progress_callback=progress_replies.append,
    )

    assert "observe_screen" in client.prompts[0]
    assert "主动获取上下文策略" in client.prompts[0]
    assert "用户输入简短模糊" in client.prompts[0]
    assert "不要重复截图" in client.prompts[0]
    assert [progress.reply.translation for progress in progress_replies] == ["我看看。"]
    assert result.actions
    assert result.actions[0].type == SCREEN_OBSERVATION_REQUEST_ACTION


def test_tool_planning_prompt_encourages_web_search_for_uncertain_external_info() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"確認するね。","zh":"我来确认一下。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry(
            [
                Tool(
                    name="web__web_search",
                    description="搜索公开网页，并返回标题、链接和简短摘要。",
                    handler=lambda arguments: {"results": []},
                )
            ]
        ),
    )

    runtime.handle_user_message([{"role": "user", "content": "这个库现在最新版本是多少？"}])

    assert "web__web_search" in client.tool_names
    assert "最新、外部、公开或不确定的信息" in client.prompts[0]
    assert "主动使用可用的网页搜索工具" in client.prompts[0]
    assert "搜索摘要不足以回答时，再读取具体网页" in client.prompts[0]


def test_autonomous_screen_observation_disabled_hides_screen_tool() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"見えないよ。","zh":"我先不看屏幕。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(False)

    runtime.handle_user_message([{"role": "user", "content": "你觉得我现在是不是卡住了？"}])

    assert "当前没有可用的自主屏幕观察工具" in client.prompts[0]


def test_screen_observation_tool_hidden_without_user_message() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"うん。","zh":"嗯。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)

    runtime.handle_user_message([{"role": "assistant", "content": "少し見るね。"}])

    assert OBSERVE_SCREEN_TOOL_NAME not in client.tool_names


def test_screen_observation_tool_hidden_after_observation_marker() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"見たよ。","zh":"我看过了。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)

    runtime.handle_user_message(
        [{"role": "user", "content": f"下午好\n{SCREEN_OBSERVATION_HISTORY_MARKER}"}]
    )

    assert OBSERVE_SCREEN_TOOL_NAME not in client.tool_names


def test_model_vision_disabled_hides_screen_observation_tool() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"見えないよ。","zh":"我看不到哦。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)
    runtime.set_model_vision_enabled(False)

    runtime.handle_user_message([{"role": "user", "content": "普通聊天"}])

    assert OBSERVE_SCREEN_TOOL_NAME not in client.tool_names


def test_screen_observation_tool_hidden_after_image_is_attached() -> None:
    class PromptCaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.tool_names: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"見るよ。","zh":"我看看。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = PromptCaptureClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)
    runtime.set_model_vision_enabled(True)

    runtime.handle_user_message(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "已经有图了"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,abc123"},
                    },
                ],
            }
        ]
    )

    assert OBSERVE_SCREEN_TOOL_NAME not in client.tool_names


def test_hidden_screen_observation_call_returns_failure_without_action() -> None:
    class HiddenScreenRequestClient:
        def complete_raw(self, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            return (
                '{"reply":{"segments":[{"ja":"見るね。","zh":"我看看。","tone":"请求"}]},'
                '"tool_calls":[{"name":"observe_screen","arguments":{},"reason":"需要当前画面"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

        def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            from app.llm.chat_reply import parse_chat_reply

            return parse_chat_reply(
                '{"segments":[{"ja":"今は見えないよ。","zh":"现在看不到哦。","tone":"中性"}]}'
            )

    runtime = AgentRuntime(
        api_client=HiddenScreenRequestClient(),  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)
    runtime.set_model_vision_enabled(False)

    result = runtime.handle_user_message([{"role": "user", "content": "普通聊天"}])

    assert not any(action.type == SCREEN_OBSERVATION_REQUEST_ACTION for action in result.actions)
    assert result.actions[0].payload["error"] == SCREEN_OBSERVATION_DISABLED_ERROR


def test_screen_observation_message_uses_openai_image_url_format() -> None:
    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,abc123",
        width=1280,
        height=720,
        captured_at="2026-05-29T20:00:00+08:00",
        screen_name="DISPLAY1",
    )

    message = build_screen_observation_user_message("帮我看这个", observation)

    assert message["role"] == "user"
    content = message["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1] == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,abc123",
            "detail": "low",
        },
    }
    assert messages_contain_image([message])


def test_screen_observation_history_marker_does_not_store_image_data() -> None:
    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,secret",
        width=800,
        height=600,
        captured_at="2026-05-29T20:00:00+08:00",
        screen_name="DISPLAY1",
    )

    history_text = append_observation_marker("看看屏幕", observation)

    assert SCREEN_OBSERVATION_HISTORY_MARKER in history_text
    assert "data:image/jpeg;base64" not in history_text
    assert "secret" not in history_text


def test_screen_observation_trigger_requires_explicit_text() -> None:
    assert should_observe_screen("帮我看这个界面哪里不对")
    assert should_observe_screen("看看当前画面")
    assert not should_observe_screen("今天聊点什么")


def test_vision_unsupported_error_gets_local_fallback_reply() -> None:
    class VisionUnsupportedClient:
        def complete_raw(self, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            raise ApiRequestError("model does not support image_url content")

        complete_with_tools = _legacy_complete_with_tools

    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,abc123",
        width=1280,
        height=720,
        captured_at="2026-05-29T20:00:00+08:00",
        screen_name="DISPLAY1",
    )
    runtime = AgentRuntime(
        api_client=VisionUnsupportedClient(),  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry(),
    )

    result = runtime.handle_user_message([build_screen_observation_user_message("看看屏幕", observation)])

    assert "不支持图片输入" in result.reply.translation
    assert not result.actions


def test_proactive_check_tool_prompt_uses_single_segment_heading() -> None:
    prompt = build_screen_awareness_check_tool_system_prompt(
        "你是 Sakura。",
        ["中性"],
        ["站立待机"],
        memory_summary="主人正在整理提示词。",
        current_time="2026-06-01T08:00:00+08:00",
        step_index=0,
        remaining_steps=3,
        max_tool_calls_per_step=3,
        max_tool_calls_per_turn=8,
        extra_instructions="额外规则。",
    )

    assert prompt.count("分段规则：") == 1
    assert "主动屏幕感知事件" in prompt
    assert "这是第 1 步" in prompt
    assert "每步最多请求 3 个工具，整轮最多 8 个工具" in prompt
    assert "主人正在整理提示词。" in prompt
    assert "2026-06-01T08:00:00+08:00" in prompt
    assert "额外规则。" in prompt
    assert "JSON 格式如下" in prompt
    assert "主动屏幕感知回复决策流程" in prompt
    assert "主动屏幕感知场景策略" in prompt
    assert "最终回复必须至少包含一个来自 screen_contexts 或 visual_contexts 的具体可见信息" in prompt
    assert "图片/角色/女性照片" in prompt
    assert "不确定时就普通问候" not in prompt


def test_proactive_check_event_prompt_reuses_segment_rules() -> None:
    prompt = build_event_system_prompt(
        "你是 Sakura。",
        ["中性"],
        ["站立待机"],
        event_type="proactive_check",
    )

    assert prompt.count("分段规则：") == 1
    assert "低打扰主动屏幕感知" in prompt
    assert "根据一段时间内的屏幕变化找自然话题" in prompt
    assert "主动搭话时不要固定使用同一种语气" in prompt
    assert "先阅读 recent_conversation" in prompt
    assert "把 screen_contexts/visual_contexts 和 recent_conversation 交叉对照" in prompt
    assert "只有画面确实为空、黑屏、桌面无内容" in prompt


def test_proactive_check_event_generates_segmented_reply() -> None:
    class ProactiveClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            self.messages.append(messages)
            return (
                '{"reply":{"segments":[{"ja":"少し休もう。","zh":"稍微休息一下吧。","tone":"请求"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    client = ProactiveClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
    )

    result = runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "seconds_since_pet_interaction": 1800,
                "check_interval_minutes": 20,
                "screen_context_allowed": False,
            },
        )
    )

    assert "低打扰主动屏幕感知" in client.prompts[0]
    assert "只表示用户一段时间没有和桌宠交互" in client.prompts[0]
    assert "不要据此推断用户离开" in client.prompts[0]
    assert "不要主动催睡觉、休息或喝水" in client.prompts[0]
    assert "真实可见或已知的具体内容" in client.prompts[0]
    assert "基于屏幕内容找话题" in client.prompts[0]
    assert "tone 和 portrait 要根据内容选择" in client.prompts[0]
    assert "主动屏幕感知事件" in str(client.messages[0][0]["content"])
    assert "seconds_since_pet_interaction" in str(client.messages[0][0]["content"])
    assert "idle_seconds" not in str(client.messages[0][0]["content"])
    assert result.reply.translation == "稍微休息一下吧。"
    assert result.actions[0].payload["event_type"] == "proactive_check"


def test_proactive_check_event_attaches_screen_context_image() -> None:
    class ProactiveImageClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            self.messages.append(messages)
            return (
                '{"reply":{"segments":[{"ja":"画面は見たよ。","zh":"我看过画面了。","tone":"请求"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    client = ProactiveImageClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
    )

    runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "screen_context": {
                    "data_url": "data:image/jpeg;base64,abc123",
                    "width": 800,
                    "height": 600,
                    "captured_at": "2026-05-30T12:00:00+08:00",
                    "screen_name": "DISPLAY1",
                }
            },
        )
    )

    content = client.messages[0][0]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc123"
    assert content[1]["image_url"]["detail"] == "high"
    assert "abc123" not in content[0]["text"]
    assert "image_attached" in content[0]["text"]
    assert "先理解屏幕画面本身" in client.prompts[0]
    assert "自然评论、接续任务、提问或轻量协助" in client.prompts[0]
    assert "不要编造看不清" in client.prompts[0]
    assert "不要再请求 observe_screen" in client.prompts[0]
    assert "主动搭话时不要固定使用同一种语气" in client.prompts[0]
    assert "基于屏幕内容找话题" in content[0]["text"]


def test_proactive_check_event_attaches_screen_context_image_batch() -> None:
    event = AgentEvent(
        type="proactive_check",
        payload={
            "screen_contexts": [
                {
                    "data_url": "data:image/jpeg;base64,first",
                    "width": 800,
                    "height": 600,
                    "captured_at": "2026-05-30T12:00:00+08:00",
                    "screen_name": "DISPLAY1",
                },
                {
                    "data_url": "data:image/jpeg;base64,second",
                    "width": 800,
                    "height": 600,
                    "captured_at": "2026-05-30T12:10:00+08:00",
                    "screen_name": "DISPLAY1",
                },
            ],
            "screen_context_count": 2,
        },
    )

    messages = _build_event_messages(event)
    content = messages[0]["content"]

    assert isinstance(content, list)
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,first"
    assert content[2]["image_url"]["url"] == "data:image/jpeg;base64,second"
    assert content[1]["image_url"]["detail"] == "high"
    assert content[2]["image_url"]["detail"] == "high"
    assert "first" not in content[0]["text"]
    assert "second" not in content[0]["text"]
    assert "image_attached" in content[0]["text"]
    assert "screen_contexts" in content[0]["text"]


def test_proactive_check_event_includes_recent_conversation_text() -> None:
    event = AgentEvent(
        type="proactive_check",
        payload={
            "recent_conversation": [
                {"role": "user", "content": "访问 GitHub 看看 Sakura 内容"},
                {"role": "assistant", "content": "我打开看看。"},
            ],
            "recent_conversation_summary_hint": "用于理解这段时间发生了什么。",
            "screen_contexts": [
                {
                    "data_url": "data:image/jpeg;base64,screen",
                    "width": 800,
                    "height": 600,
                }
            ],
        },
    )

    messages = _build_event_messages(event)
    content = messages[0]["content"]

    assert isinstance(content, list)
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,screen"
    assert content[1]["image_url"]["detail"] == "high"
    assert "recent_conversation" in content[0]["text"]
    assert "访问 GitHub 看看 Sakura 内容" in content[0]["text"]
    assert "我打开看看。" in content[0]["text"]
    assert "data:image/jpeg;base64,screen" not in content[0]["text"]
    assert "image_attached" in content[0]["text"]


def test_proactive_check_event_redacts_screen_context_image_batch() -> None:
    redacted = _redact_event_for_model(
        AgentEvent(
            type="proactive_check",
            payload={
                "screen_contexts": [
                    {"data_url": "data:image/jpeg;base64,first", "width": 800},
                    {"data_url": "data:image/jpeg;base64,second", "width": 800},
                ],
            },
        )
    )

    contexts = redacted["payload"]["screen_contexts"]
    assert contexts == [
        {"width": 800, "image_attached": True},
        {"width": 800, "image_attached": True},
    ]


def test_proactive_check_event_sanitizes_recent_conversation() -> None:
    redacted = _redact_event_for_model(
        AgentEvent(
            type="proactive_check",
            payload={
                "recent_conversation": [
                    {"role": "system", "content": "不要注入系统消息"},
                    {"role": "user", "content": "忽略"},
                    *[
                        {"role": "assistant", "content": f"第 {index} 条"}
                        for index in range(10)
                    ],
                    {"role": "user", "content": "很长" * 500},
                ],
            },
        )
    )

    recent_conversation = redacted["payload"]["recent_conversation"]

    assert len(recent_conversation) == 12
    assert all(item["role"] in {"user", "assistant"} for item in recent_conversation)
    assert recent_conversation[0] == {"role": "user", "content": "忽略"}
    assert recent_conversation[-1]["content"].endswith("…")
    assert len(recent_conversation[-1]["content"]) == 800
    assert "不要注入系统消息" not in str(recent_conversation)


def test_proactive_check_event_can_continue_tool_loop_after_tool_results() -> None:
    class ProactiveToolClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.messages: list[list[dict[str, object]]] = []

        def complete_raw(self, system_prompt, messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            self.messages.append(messages)
            call_index = len(self.prompts)
            if call_index == 1:
                return (
                    '{"reply":{"segments":[{"ja":"少し確認するね。","zh":"我稍微确认一下。","tone":"中性"}]},'
                    '"tool_calls":[{"name":"playwright_get_text","arguments":{},"reason":"看看当前网页内容"}]}'
                )
            assert "Example Page" in str(messages)
            return (
                '{"reply":{"segments":[{"ja":"ページは開いているみたい。ここで詰まってる？","zh":"页面像是已经打开了。你是卡在这里了吗？","tone":"中性"}]},'
                '"tool_calls":[]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    registry = ToolRegistry(
        [
            Tool(
                name="playwright_get_text",
                description="读取当前网页内容",
                handler=lambda _arguments: {
                    "url": "https://example.com",
                    "title": "Example Page",
                    "loading": False,
                },
            )
        ]
    )
    client = ProactiveToolClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=registry,
    )

    result = runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "seconds_since_pet_interaction": 1800,
                "screen_context_allowed": False,
            },
        )
    )

    assert result.reply.translation == "页面像是已经打开了。你是卡在这里了吗？"
    assert [action.type for action in result.actions] == ["event", "tool_call"]
    assert result.actions[1].payload["tool_name"] == "playwright_get_text"
    assert len(client.prompts) == 2
    assert "主动屏幕感知事件" in client.prompts[0]
    assert "不要为了显得主动而循环调用工具" in client.prompts[0]


def test_proactive_check_can_request_screen_when_screen_context_allowed() -> None:
    class ProactiveScreenClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return (
                '{"reply":{"segments":[{"ja":"ちょっと見てみるね。","zh":"我稍微看一下。","tone":"中性"}]},'
                '"tool_calls":[{"name":"observe_screen","arguments":{},"reason":"想看看主人现在在做什么"}]}'
            )

        complete_with_tools = _legacy_complete_with_tools

    client = ProactiveScreenClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )

    result = runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "seconds_since_pet_interaction": 1800,
                "screen_context_allowed": True,
            },
        )
    )

    assert "observe_screen" in client.prompts[0]
    assert result.actions[0].type == "event"
    assert result.actions[1].type == SCREEN_OBSERVATION_REQUEST_ACTION
    assert result.actions[1].payload["reason"] == "想看看主人现在在做什么"


def test_proactive_check_hides_screen_tool_when_screen_context_disallowed() -> None:
    class ProactiveScreenClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_raw(self, system_prompt, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.prompts.append(system_prompt)
            return '{"reply":{"segments":[{"ja":"声をかけるね。","zh":"我轻轻叫你一下。","tone":"中性"}]}}'

        complete_with_tools = _legacy_complete_with_tools

    client = ProactiveScreenClient()
    runtime = AgentRuntime(
        api_client=client,  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
        tools=ToolRegistry([create_screen_observation_tool()]),
    )
    runtime.set_autonomous_screen_observation_enabled(True)

    result = runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "seconds_since_pet_interaction": 1800,
                "screen_context_allowed": False,
            },
        )
    )

    assert f'"name": "{OBSERVE_SCREEN_TOOL_NAME}"' not in client.prompts[0]
    assert result.actions[0].type == "event"
    assert not any(action.type == SCREEN_OBSERVATION_REQUEST_ACTION for action in result.actions)


def test_proactive_check_vision_unsupported_uses_silent_fallback() -> None:
    class ProactiveVisionUnsupportedClient:
        def complete_raw(self, *_args, **_kwargs) -> str:  # type: ignore[no-untyped-def]
            raise ApiRequestError("model does not support image_url content")

        complete_with_tools = _legacy_complete_with_tools

    runtime = AgentRuntime(
        api_client=ProactiveVisionUnsupportedClient(),  # type: ignore[arg-type]
        system_prompt="你是 Sakura。",
    )

    result = runtime.handle_event(
        AgentEvent(
            type="proactive_check",
            payload={
                "screen_context": {
                    "data_url": "data:image/jpeg;base64,abc123",
                    "width": 800,
                    "height": 600,
                }
            },
        )
    )

    assert result.reply.segments == []


def test_plain_text_messages_do_not_contain_image() -> None:
    assert not messages_contain_image([{"role": "user", "content": "普通聊天"}])
    assert is_vision_unsupported_error("This model does not support image input")


def _runtime_json_path(name: str) -> Path:
    return _runtime_root_path(name) / f"{name}.json"


def _runtime_root_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex


def _write_memory_model_zip(
    archive_path: Path,
    *,
    prefix: str,
    include_weight: bool = True,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        revision = f"{prefix}/snapshots/revision" if prefix else "snapshots/revision"
        zf.writestr(f"{revision}/config.json", "{}")
        if not prefix:
            zf.writestr("refs/main", "revision")
            zf.writestr("blobs/fake", "fake")
        if include_weight:
            zf.writestr(f"{revision}/model.safetensors", "fake")


class FakeMem0:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def add(self, messages, *, user_id=None, metadata=None, infer=True):  # type: ignore[no-untyped-def]
        content = messages if isinstance(messages, str) else messages[0]["content"]
        record = {
            "id": uuid.uuid4().hex[:8],
            "memory": str(content),
            "user_id": user_id,
            "metadata": metadata or {},
            "event": "ADD",
        }
        self.records.append(record)
        return {"results": [record]}

    def get_all(self, *, filters=None, top_k=20):  # type: ignore[no-untyped-def]
        return {"results": self._filtered(filters)[:top_k]}

    def search(self, query, *, filters=None, top_k=20):  # type: ignore[no-untyped-def]
        results = [
            record
            for record in self._filtered(filters)
            if str(query) in str(record.get("memory", ""))
        ]
        return {"results": results[:top_k]}

    def get(self, memory_id):  # type: ignore[no-untyped-def]
        for record in self.records:
            if record["id"] == memory_id:
                return dict(record)
        return None

    def update(self, memory_id, data, metadata=None):  # type: ignore[no-untyped-def]
        for record in self.records:
            if record["id"] == memory_id:
                record["memory"] = data
                if metadata:
                    record["metadata"] = metadata
                updated = {**record, "event": "UPDATE"}
                return {"results": [updated]}
        raise ValueError(memory_id)

    def delete(self, memory_id):  # type: ignore[no-untyped-def]
        self.records = [record for record in self.records if record["id"] != memory_id]
        return {"ok": True}

    def _filtered(self, filters):  # type: ignore[no-untyped-def]
        user_id = (filters or {}).get("user_id")
        return [
            dict(record)
            for record in self.records
            if user_id is None or record.get("user_id") == user_id
        ]


class FakeMem0RaisesOnMissingDelete(FakeMem0):
    def delete(self, memory_id):  # type: ignore[no-untyped-def]
        if self.get(memory_id) is None:
            raise ValueError(f"Memory with id {memory_id} not found")
        return super().delete(memory_id)


class ClosableClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ClosableVectorStore:
    def __init__(self, client: ClosableClient) -> None:
        self.client = client


class ClosableMemoryRuntime:
    def __init__(self) -> None:
        self.client = ClosableClient()
        self.vector_store = ClosableVectorStore(self.client)
        self.memory_closed = False

    def close(self) -> None:
        self.memory_closed = True


def _write_mcp_config(root: Path, server_body: str) -> Path:
    config_path = root / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    indented_body = "\n".join(f"    {line}" if line else "" for line in server_body.splitlines())
    config_path.write_text(
        f"""
enabled: true
servers:
  demo:
    transport: stdio
    command: python
{indented_body}
""".strip(),
        encoding="utf-8",
    )
    return config_path


class _FakeMCPBridge:
    def __init__(self, _server_config, _default_call_timeout) -> None:  # type: ignore[no-untyped-def]
        self.closed = False

    def connect(self) -> None:
        return None

    def list_tools(self) -> list[MCPToolSpec]:
        return [
            MCPToolSpec(
                name="echo",
                description="回显消息。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {
            "server_tool_name": name,
            "arguments": arguments,
        }

    def close(self) -> None:
        self.closed = True


class _MultiToolMCPBridge(_FakeMCPBridge):
    def list_tools(self) -> list[MCPToolSpec]:
        return [
            MCPToolSpec(
                name="read_state",
                description="读取状态。",
                input_schema={"type": "object", "properties": {}},
            ),
            MCPToolSpec(
                name="click_target",
                description="点击目标。",
                input_schema={"type": "object", "properties": {}},
            ),
            MCPToolSpec(
                name="hidden_tool",
                description="不应暴露。",
                input_schema={"type": "object", "properties": {}},
            ),
        ]


class _FailingMCPBridge(_FakeMCPBridge):
    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        _ = name, arguments
        raise RuntimeError("MCP 调用失败")
