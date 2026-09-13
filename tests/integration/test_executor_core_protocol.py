from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
import pytest

from app.plugins.inventory import PluginDesiredStateStore
from app.plugins.installer import LocalPluginInstaller
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineKind, TimelineStore
from test_core_host_real_chat_integration import (
    REPO_ROOT,
    _ProviderHandler,
    _configure_app_root,
    _start_provider,
    _stop_provider,
    _hello,
    _read,
    _request,
    _send,
    _start_host,
    _stop,
)


SERVICE_KEY = "focus_companion.executor"
TERMINALS = {"chat.completed", "chat.failed", "chat.cancelled"}


class _CorePeer:
    """Retain interleaved events while waiting for individual stdio responses."""

    def __init__(self, process: Any) -> None:
        self.process = process
        self.frames: list[dict[str, Any]] = []
        self.next_id = 0

    def until(self, predicate: Callable[[dict[str, Any]], bool], *, start: int = 0) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        index = start
        while True:
            for frame in self.frames[index:]:
                if predicate(frame):
                    return frame
            index = len(self.frames)
            remaining = deadline - time.monotonic()
            assert remaining > 0, self.frames[start:]
            self.frames.append(_read(self.process, timeout=remaining))

    def exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        start = len(self.frames)
        _send(self.process, message)
        reply = self.until(
            lambda frame: frame.get("kind") == "response" and frame.get("id") == message["id"],
            start=start,
        )
        assert reply.get("ok") is True, reply
        return reply["payload"]

    def request(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        return self.exchange(_request(f"control-{self.next_id}", name, payload or {}))

    def readiness(self, expected: str) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while True:
            snapshot = self.request("core.snapshot")
            if snapshot["readiness"] == expected:
                return snapshot
            assert snapshot["readiness"] in {"transport_ready", "initializing"}, json.dumps({
                "snapshot": snapshot,
                "plugins": self.request("plugins.settings.get"),
            }, ensure_ascii=False, indent=2)
            assert time.monotonic() < deadline, snapshot

    def chat(self, operation_id: str, message: str) -> None:
        accepted = self.exchange(_request(operation_id, "chat.send", {
            "operationId": operation_id, "message": message,
        }))
        assert accepted == {"accepted": True, "operationId": operation_id}

    def event(self, operation_id: str, name: str) -> dict[str, Any]:
        return self.until(lambda frame: (
            frame.get("kind") == "event" and frame.get("id") == operation_id
            and (frame.get("name") == name or frame.get("name") == "chat.failed")
        ))

    def completed(self, operation_id: str) -> dict[str, Any]:
        terminal = self.event(operation_id, "chat.completed")
        assert terminal["name"] == "chat.completed", terminal
        events = [frame for frame in self.frames if frame.get("kind") == "event" and frame.get("id") == operation_id]
        assert events[0]["name"] == "chat.started"
        assert [frame["name"] for frame in events if frame["name"] in TERMINALS] == ["chat.completed"]
        return terminal["payload"]


@pytest.mark.parametrize("model_configured", [True, False])
def test_old_executor_choice_and_enabled_plugin_do_not_override_default_assistant(
    tmp_path: Path, model_configured: bool,
) -> None:
    server, provider_thread = _start_provider("complete")
    process = None
    plugin_process = None
    try:
        user = _configure_app_root(tmp_path, server.server_address[1])
        distribution = tmp_path / "distribution"
        distribution.mkdir()
        if not model_configured:
            (user / "config/api.yaml").unlink()
        system_config = user / "config/system_config.yaml"
        previous_settings = f"config_version: 1\nchat_executor: {SERVICE_KEY}\n"
        system_config.write_text(previous_settings, encoding="utf-8")
        installed = LocalPluginInstaller(RuntimeRoots(distribution, user)).install(
            REPO_ROOT / "plugins/optional/focus_companion", "folder",
        )
        PluginDesiredStateStore(user).set(installed.plugin_id, True)

        process = _start_host(user, distribution_root=distribution)
        peer = _CorePeer(process)
        peer.exchange(_hello([
            "transport.concurrent-router", "assistant.plugins-v1", "settings.provider-model",
        ]))
        peer.request("core.initialize")
        peer.readiness("ready" if model_configured else "setup_required")
        plugins = peer.request("plugins.settings.get")["plugins"]
        assert [(item["pluginId"], item["state"]) for item in plugins] == [("focus_companion", "active")]
        plugin_process = next(
            child for child in psutil.Process(process.pid).children(recursive=True)
            if "focus_companion" in child.cmdline()
        )
        assert len({os.getpid(), process.pid, plugin_process.pid}) == 3

        # Removing the UI also removes its mutation API; old clients cannot
        # silently select an executor after the selector has been withdrawn.
        for method, payload in (("get", {}), ("save", {"serviceKey": SERVICE_KEY})):
            message = _request(f"retired-{method}", f"settings.executor.{method}", payload)
            _send(process, message)
            reply = peer.until(lambda frame: frame.get("kind") == "response" and frame.get("id") == message["id"])
            assert reply["ok"] is False
            assert reply["error"]["code"] == "UNKNOWN_CONTROL"
        assert system_config.read_text(encoding="utf-8") == previous_settings

        provider = peer.request("settings.provider_model.get")
        assert provider["setup_complete"] is model_configured
        if model_configured:
            peer.chat("default-chat", "专注 1 秒 整理桌面")
            completed = peer.completed("default-chat")
            assert completed["reply"]["segments"][0]["translation"] == "欢迎回来。"
            assert len(_ProviderHandler.requests) == 1
            assert not any(frame.get("name") == "chat.progress" for frame in peer.frames)
            history = TimelineStore(user / "data/chat_history/timeline.sqlite3").read_all("sakura")
            assert [entry.kind for entry in history] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
        else:
            assert not _ProviderHandler.requests
            assert not (user / "config/api.yaml").exists()
        assert peer.request("core.snapshot")["activeInteractionSummary"] is None
    finally:
        if process is not None:
            _stop(process)
        _stop_provider(server, provider_thread)
    assert process.returncode == 0
    assert plugin_process is not None and not plugin_process.is_running()
