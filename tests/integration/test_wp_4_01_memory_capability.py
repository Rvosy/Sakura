from __future__ import annotations

import json
import hashlib
import shutil
import time
from pathlib import Path

from tests.integration.test_core_host_real_chat_integration import (
    CAPABILITIES,
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


def _install_official_mem0(app_root: Path) -> None:
    (app_root / "app").mkdir(exist_ok=True)
    plugin_root = app_root / "plugins"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(
        REPO_ROOT / "plugins" / "sakura_mem0",
        plugin_root / "sakura_mem0",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
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
    sequence = 0
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
    raise TimeoutError("Mem0 plugin did not become active")


def test_real_core_runs_mem0_as_generic_plugin_without_mutating_legacy_config_or_chat_block(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    _install_official_mem0(app_root)
    legacy_path = app_root / "data" / "memory.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"legacy-memory-must-stay-byte-identical\x00\xff")
    before = legacy_path.read_bytes()
    api_path = app_root / "data" / "config" / "api.yaml"
    system_path = app_root / "data" / "config" / "system_config.yaml"
    api_before = api_path.read_bytes()
    system_before = system_path.read_bytes()
    protected = [
        app_root / "data" / "memory" / "qdrant" / "fixture.bin",
        app_root / "data" / "memory" / "mem0_history.db",
        app_root / "data" / "memory" / "core_profiles.json",
        app_root / "data" / "memory_curation_state.json",
        app_root / "runtime" / "fastembed-cache" / "existing.bin",
        app_root / "runtime" / "hf-cache" / "legacy-pytorch.bin",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
    protected[0].write_bytes(b"existing-qdrant-bytes")
    protected[1].write_bytes(b"existing-sqlite-bytes")
    protected[2].write_text("{}", encoding="utf-8")
    protected[3].write_text(
        json.dumps(
            {
                "processed_history_count": 0,
                "pending_turns": 0,
                "backfill_completed": False,
            }
        ),
        encoding="utf-8",
    )
    protected[4].write_bytes(b"existing-onnx-cache")
    protected[5].write_bytes(b"existing-pytorch-cache")
    protected_before = _fingerprint(protected)
    isolated_cache = tmp_path / "isolated-fastembed-cache"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(isolated_cache))
    process = _start_host(app_root)
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
        section = next(item for item in mem0["sections"] if item["sectionId"] == "memory")
        assert section["values"]["embeddingStatus"] == "未安装"
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
        plugin_config = app_root / "data" / "plugins" / "sakura.memory.mem0" / "config.json"
        migrated = json.loads(plugin_config.read_text(encoding="utf-8"))
        assert migrated["triggerTurns"] == 8
        assert migrated["backfillLimit"] == 200
        assert set(path.name for path in plugin_config.parent.iterdir()) == {"config.json"}
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
        assert [frame.get("name", "response") for frame in frames] == [
            "chat.started",
            "chat.completed",
            "chat.send",
        ]
        assert len(_ProviderHandler.requests) == 1
        assert legacy_path.read_bytes() == before
        assert api_path.read_bytes() == api_before
        assert system_path.read_bytes() == system_before
        assert _fingerprint([path for path in protected if path != protected[3]]) == {
            key: value
            for key, value in protected_before.items()
            if key != str(protected[3])
        }
        assert json.loads(protected[3].read_text(encoding="utf-8")) == {
            "processed_history_count": 0,
            "pending_turns": 1,
            "backfill_completed": False,
        }
        shutdown = _exchange(process, _request("memory-shutdown", "system.shutdown", {}))
        assert shutdown["payload"]["accepted"] is True
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)


def test_plugin_settings_without_negotiation_fails_closed_without_opening_memory_storage(
    tmp_path: Path,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    _install_official_mem0(app_root)
    process = _start_host(app_root)
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
        assert not (app_root / "data" / "plugins" / "sakura.memory.mem0").exists()
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)
