from __future__ import annotations

import json
import time
from pathlib import Path

from tests.integration.test_core_host_real_chat_integration import (
    CAPABILITIES,
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


def _negotiate_memory(process) -> None:
    hello = _request(
        "memory-hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": ["transport.concurrent-router", "assistant.memory"],
        },
    )
    response = _exchange(process, hello)
    assert "assistant.memory" in response["payload"]["capabilities"]
    _exchange(process, _request("memory-initialize", "core.initialize", {}))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = _exchange(
            process,
            _request("memory-snapshot", "core.snapshot", {}),
        )["payload"]
        if snapshot["readiness"] in {"ready", "degraded"}:
            return
        time.sleep(0.01)
    raise TimeoutError("Memory Assistant did not become ready")


def test_real_core_negotiates_memory_and_missing_model_degrades_without_secret_or_chat_block(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    legacy_path = app_root / "data" / "memory.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"legacy-memory-must-stay-byte-identical\x00\xff")
    before = legacy_path.read_bytes()
    isolated_cache = tmp_path / "isolated-hf-cache"
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(isolated_cache))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(isolated_cache))
    monkeypatch.setenv("TRANSFORMERS_CACHE", str(isolated_cache))
    monkeypatch.setenv("HF_HOME", str(isolated_cache))
    process = _start_host(app_root)
    try:
        _negotiate_memory(process)
        settings = _exchange(
            process,
            _request("memory-settings", "memory.settings.get", {}),
        )
        assert settings["ok"] is True
        assert settings["payload"]["embedding"] == {
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "dimensions": 384,
            "installed": False,
            "task": None,
        }
        assert "LOCAL_TEST_KEY" not in json.dumps(settings)
        recall = _exchange(
            process,
            _request(
                "memory-search",
                "memory.search",
                {"query": "中文と日本語", "limit": 5},
            ),
        )
        assert recall["payload"]["status"] == "degraded"
        assert recall["payload"]["memories"] == []
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
        shutdown = _exchange(process, _request("memory-shutdown", "system.shutdown", {}))
        assert shutdown["payload"]["accepted"] is True
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)


def test_memory_request_without_negotiation_fails_closed_without_opening_storage(tmp_path: Path) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
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
            _request("denied-memory", "memory.settings.get", {}),
        )
        assert response["ok"] is False
        assert response["error"]["code"] == "CAPABILITY_NEGOTIATION_FAILED"
        _exchange(process, _request("plain-shutdown", "system.shutdown", {}))
        process.wait(timeout=5)
        assert process.returncode == 0
        assert not (app_root / "data" / "memory" / "qdrant").exists()
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)
