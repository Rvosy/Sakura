from __future__ import annotations

import json
import os
import subprocess
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
    _read,
    _request,
    _send,
    _start_provider,
    _stop,
    _stop_provider,
)


CURRENT_OPTIONAL_CAPABILITIES = [
    "transport.concurrent-router",
    "settings.provider-model",
    "assistant.memory",
]

QT_BLOCKED_CORE_BOOTSTRAP = r"""
import importlib.abc
import runpy
import sys

class RejectPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise AssertionError(f"forbidden Qt import: {fullname}")
        return None

sys.meta_path.insert(0, RejectPySide())
sys.argv = ["app.core_host", *sys.argv[1:]]
runpy.run_module("app.core_host", run_name="__main__")
"""


def _start_qt_blocked_host(app_root: Path) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            QT_BLOCKED_CORE_BOOTSTRAP,
            "--app-root",
            str(app_root),
            "--generation-id",
            GENERATION_ID,
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    assert process.stdin is not None
    process.stdin.write(bytes.fromhex(GENERATION_CREDENTIAL))
    process.stdin.flush()
    return process


def _wait_for_current_topology(process: subprocess.Popen[bytes]) -> None:
    hello = _exchange(
        process,
        _request(
            "current-hello",
            "system.hello",
            {
                "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
                "requiredCapabilities": CAPABILITIES,
                "optionalCapabilities": CURRENT_OPTIONAL_CAPABILITIES,
            },
        ),
    )
    negotiated = hello["payload"]["capabilities"]
    assert all(capability in negotiated for capability in CURRENT_OPTIONAL_CAPABILITIES)

    initialize = _exchange(
        process,
        _request("current-initialize", "core.initialize", {}),
    )
    assert initialize["payload"]["readiness"] == "initializing"

    deadline = time.monotonic() + 10
    index = 0
    while time.monotonic() < deadline:
        snapshot = _exchange(
            process,
            _request(f"current-snapshot-{index}", "core.snapshot", {}),
        )["payload"]
        if snapshot["readiness"] in {"ready", "degraded"}:
            return
        index += 1
        time.sleep(0.01)
    raise TimeoutError("current product topology did not become ready")


def test_qt_free_current_product_topology_runs_chat_provider_settings_and_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    isolated_cache = tmp_path / "isolated-hf-cache"
    for name in (
        "SENTENCE_TRANSFORMERS_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "HF_HOME",
    ):
        monkeypatch.setenv(name, str(isolated_cache))
    process = _start_qt_blocked_host(app_root)
    try:
        _wait_for_current_topology(process)

        provider_settings = _exchange(
            process,
            _request(
                "current-provider-settings",
                "settings.provider_model.get",
                {},
            ),
        )
        assert provider_settings["ok"] is True
        assert provider_settings["payload"]["providers"][0]["configured"] is True
        assert "LOCAL_TEST_KEY" not in json.dumps(provider_settings)

        memory_settings = _exchange(
            process,
            _request("current-memory-settings", "memory.settings.get", {}),
        )
        assert memory_settings["ok"] is True
        assert memory_settings["payload"]["embedding"]["installed"] is False

        memory_search = _exchange(
            process,
            _request(
                "current-memory-search",
                "memory.search",
                {"query": "当前产品拓扑", "limit": 5},
            ),
        )
        assert memory_search["ok"] is True
        assert memory_search["payload"]["status"] in {"ready", "degraded"}
        assert isinstance(memory_search["payload"]["memories"], list)

        _send(
            process,
            _request(
                "current-chat",
                "chat.send",
                {"message": "ただいま", "operationId": "current-chat"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        assert [frame.get("name", "response") for frame in frames] == [
            "chat.started",
            "chat.completed",
            "chat.send",
        ]
        assert frames[2]["payload"]["accepted"] is True
        assert len(_ProviderHandler.requests) == 1

        shutdown = _exchange(
            process,
            _request("current-shutdown", "system.shutdown", {}),
        )
        assert shutdown["payload"]["accepted"] is True
        process.wait(timeout=5)
        assert process.returncode == 0
        assert process.stderr is not None
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert "forbidden Qt import" not in stderr
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)
