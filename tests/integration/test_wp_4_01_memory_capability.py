from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from tests.integration.test_core_host_real_chat_integration import (
    CAPABILITIES,
    GENERATION_CREDENTIAL,
    GENERATION_ID,
    REPO_ROOT,
    _ProviderHandler,
    _configure_app_root,
    _exchange,
    _request,
    _read,
    _send,
    _start_host,
    _start_provider,
    _stop,
    _stop_provider,
)


def _install_official_mem0(distribution_root: Path) -> None:
    (distribution_root / "app").mkdir(parents=True)
    plugin_root = distribution_root / "plugins" / "builtin"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(
        REPO_ROOT / "plugins" / "builtin" / "sakura_mem0",
        plugin_root / "sakura_mem0",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    requirements = plugin_root / "sakura_mem0" / "requirements.txt"
    dependency_root = (
        distribution_root / "plugins" / "dependencies" / "sakura.memory.mem0"
    )
    dependency_root.mkdir(parents=True)
    (dependency_root / ".sakura-dependencies.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "requirements.txt",
                "fingerprint": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _fingerprint(paths: list[Path]) -> dict[str, tuple[int, str]]:
    return {
        str(path): (len(data), hashlib.sha256(data).hexdigest())
        for path in paths
        if path.is_file()
        for data in [path.read_bytes()]
    }


def _negotiate_mem0_plugin(process) -> None:
    hello = _request(
        "memory-hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": [
                "transport.concurrent-router",
                "assistant.tools-v1",
                "assistant.plugins-v1",
            ],
        },
    )
    response = _exchange(process, hello)
    assert "assistant.plugins-v1" in response["payload"]["capabilities"]
    _exchange(process, _request("memory-initialize", "core.initialize", {}))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = _exchange(
            process,
            _request("memory-snapshot", "core.snapshot", {}),
        )["payload"]
        if snapshot["readiness"] in {"ready", "degraded"}:
            break
        time.sleep(0.01)
    else:
        raise TimeoutError("Mem0 plugin Assistant did not become ready")
    deadline = time.monotonic() + 10
    sequence = 0
    mem0 = None
    while time.monotonic() < deadline:
        plugins = _exchange(
            process,
            _request(f"memory-plugins-{sequence}", "plugins.settings.get", {}),
        )["payload"]["plugins"]
        mem0 = next(
            (item for item in plugins if item["pluginId"] == "sakura.memory.mem0"),
            None,
        )
        if mem0 is not None and mem0["state"] == "active":
            return
        sequence += 1
        time.sleep(0.02)
    raise TimeoutError(f"Mem0 plugin did not become active: {mem0!r}")


def test_real_core_runs_mem0_as_generic_plugin_without_mutating_owned_config_or_chat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    distribution_root = tmp_path / "distribution"
    _install_official_mem0(distribution_root)
    api_path = app_root / "config" / "api.yaml"
    system_path = app_root / "config" / "system_config.yaml"
    api_before = api_path.read_bytes()
    system_before = system_path.read_bytes()
    protected = [
        app_root / "data" / "memory" / "qdrant" / "fixture.bin",
        app_root / "data" / "memory" / "mem0_history.db",
        app_root / "data" / "memory" / "core_profiles.json",
        app_root / "runtime" / "fastembed-cache" / "existing.bin",
        app_root / "runtime" / "hf-cache" / "existing-pytorch.bin",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
    protected[0].write_bytes(b"existing-qdrant-bytes")
    protected[1].write_bytes(b"existing-sqlite-bytes")
    protected[2].write_text("{}", encoding="utf-8")
    protected[3].write_bytes(b"existing-onnx-cache")
    protected[4].write_bytes(b"existing-pytorch-cache")
    protected_before = _fingerprint(protected)
    isolated_cache = tmp_path / "isolated-fastembed-cache"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(isolated_cache))
    process = _start_host(app_root, distribution_root=distribution_root)
    try:
        _negotiate_mem0_plugin(process)
        settings = _exchange(
            process,
            _request("memory-settings", "plugins.settings.get", {}),
        )
        assert settings["ok"] is True
        mem0 = next(
            item
            for item in settings["payload"]["plugins"]
            if item["pluginId"] == "sakura.memory.mem0"
        )
        assert mem0["state"] == "active"
        section = next(
            item for item in mem0["sections"]
            if item["sectionId"] == "memory_embedding_component"
        )
        assert section["surface"] == "about"
        assert section["values"]["embeddingResource"] == {
            "applicability": "required",
            "subtitle": "sentence-transformers/all-MiniLM-L6-v2",
            "ready": False,
            "taskState": "idle",
            "message": "长期记忆检索需要先安装这个本地模型。",
            "detail": "",
            "progress": None,
            "availableActionIds": ["downloadEmbedding"],
        }
        assert section["collections"] == []
        management = next(
            item
            for item in mem0["sections"]
            if item["sectionId"] == "memory_management"
        )
        assert management["surface"] == "memory"
        assert management["collections"][0]["collectionId"] == "memories"
        assert "LOCAL_TEST_KEY" not in json.dumps(settings)
        recall = _exchange(
            process,
            _request(
                "memory-search",
                "plugins.collection.query",
                {
                    "pluginId": "sakura.memory.mem0",
                    "sectionId": "memory_management",
                    "collectionId": "memories",
                    "cursor": None,
                    "limit": 5,
                    "search": "中文と日本語",
                    "filters": {},
                },
            ),
        )
        assert recall["payload"] == {"items": [], "nextCursor": None, "total": 0}
        assert _fingerprint(protected) == protected_before
        plugin_data = app_root / "data" / "plugins" / "sakura.memory.mem0"
        assert not any(plugin_data.iterdir())
        assert api_path.read_bytes() == api_before
        assert system_path.read_bytes() == system_before
        _send(
            process,
            _request(
                "memory-chat",
                "chat.send",
                {"message": "记忆不可用时也继续聊天", "operationId": "memory-chat"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        names = [frame.get("name", "response") for frame in frames]
        assert names[0] == "chat.started"
        assert set(names[1:]) == {"chat.send", "chat.completed"}
        assert len(_ProviderHandler.requests) == 1
        assert api_path.read_bytes() == api_before
        assert system_path.read_bytes() == system_before
        assert _fingerprint(protected) == protected_before
        shutdown = _exchange(process, _request("memory-shutdown", "system.shutdown", {}))
        assert shutdown["payload"]["accepted"] is True
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)


def test_mem0_model_slot_saves_in_one_phase_without_restarting_plugin(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost
    from app.core_host.provider_settings import ProviderSettingsBoundary
    from app.storage.runtime_roots import RuntimeRoots

    app_root = _configure_app_root(tmp_path, 9)
    distribution_root = tmp_path / "distribution"
    _install_official_mem0(distribution_root)
    first_application = PluginApplicationHost(
        RuntimeRoots(distribution_root, app_root),
        "generation-before-provider-save",
        ToolRegistry(),
    )
    first_application.start()
    first_boundary = ProviderSettingsBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        app_root,
        plugin_application_provider=lambda: first_application,
    )
    first_boundary.enable()
    try:
        current = first_boundary.handle(
            _request("model-slots-before", "settings.provider_model.get", {})
        )["payload"]
        identities = [slot["identity"] for slot in current["model_slots"]]
        assert "plugin:sakura.memory.mem0:curation" in identities
        draft = {
            "providers": [
                {
                    **current["providers"][0],
                    "credential": {"action": "keep", "value": ""},
                }
            ],
            "model_slots": {
                slot["identity"]: dict(slot["selection"])
                for slot in current["model_slots"]
            },
            "settings": dict(current["settings"]),
        }
        draft["model_slots"]["plugin:sakura.memory.mem0:curation"] = {
            "profile_id": "fixture",
            "model": "fixture-model",
        }
        before = first_application.application.public_snapshot()
        plugin_pid = next(
            item["pid"]
            for item in before["plugins"]
            if item["pluginId"] == "sakura.memory.mem0"
        )
        assert plugin_pid is not None
        saved = first_boundary.handle(
            _request(
                "model-slots-single-phase",
                "settings.provider_model.save",
                {"draft": draft},
            )
        )
        assert saved["ok"] is True
        assert saved["payload"]["change_plan"] == "applied"
        assert saved["payload"]["save_state"] == "complete"
        assert saved["payload"]["saved_slots"] == [
            "core:chat",
            "core:vision_chat",
            "plugin:sakura.memory.mem0:curation"
        ]
        after = first_application.application.public_snapshot()
        assert next(
            item["pid"]
            for item in after["plugins"]
            if item["pluginId"] == "sakura.memory.mem0"
        ) == plugin_pid
        plugin_config = json.loads(
            (
                app_root
                / "data"
                / "plugins"
                / "sakura.memory.mem0"
                / "config.json"
            ).read_text(encoding="utf-8")
        )
        assert plugin_config["curationProfileId"] == "fixture"
        assert plugin_config["curationModel"] == "fixture-model"
    finally:
        first_application.close()


def test_plugin_settings_without_negotiation_fails_closed_without_opening_memory_storage(
    tmp_path: Path,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    distribution_root = tmp_path / "distribution"
    _install_official_mem0(distribution_root)
    process = _start_host(app_root, distribution_root=distribution_root)
    try:
        hello = _request(
            "plain-hello",
            "system.hello",
            {
                "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
                "requiredCapabilities": CAPABILITIES,
                "optionalCapabilities": ["transport.concurrent-router"],
            },
        )
        _exchange(process, hello)
        _exchange(process, _request("plain-initialize", "core.initialize", {}))
        response = _exchange(
            process,
                _request("denied-memory", "plugins.settings.get", {}),
        )
        assert response["ok"] is False
        assert response["error"]["code"] == "CAPABILITY_NEGOTIATION_FAILED"
        _exchange(process, _request("plain-shutdown", "system.shutdown", {}))
        process.wait(timeout=5)
        assert process.returncode == 0
        assert not (app_root / "data" / "memory" / "qdrant").exists()
        plugin_data = app_root / "data" / "plugins" / "sakura.memory.mem0"
        assert not plugin_data.exists() or not any(plugin_data.iterdir())
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)
