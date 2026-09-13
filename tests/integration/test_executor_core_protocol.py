from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from app.plugins.inventory import PluginDesiredStateStore
from app.plugins.installer import LocalPluginInstaller
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineKind, TimelineStore
from test_core_host_real_chat_integration import (
    REPO_ROOT,
    SOURCE_ROOT,
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
                "snapshot": snapshot, "executor": self.request("settings.executor.get"),
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
        assert any(frame["name"] == "chat.progress" and frame["payload"]["text"] for frame in events)
        assert [frame["name"] for frame in events if frame["name"] in TERMINALS] == ["chat.completed"]
        return terminal["payload"]


def test_core_and_focus_plugin_processes_support_offline_chat_cancel_and_executor_settings(tmp_path: Path) -> None:
    user = tmp_path / "user"
    distribution = tmp_path / "distribution"
    shutil.copytree(SOURCE_ROOT, user)
    (user / "config/api.yaml").unlink()
    (user / "config/system_config.yaml").write_text(
        f"config_version: 1\nchat_executor: {SERVICE_KEY}\n", encoding="utf-8",
    )
    distribution.mkdir()
    installed = LocalPluginInstaller(RuntimeRoots(distribution, user)).install(
        REPO_ROOT / "plugins/optional/focus_companion", "folder",
    )
    PluginDesiredStateStore(user).set(installed.plugin_id, True)

    process = _start_host(user, distribution_root=distribution)
    peer = _CorePeer(process)
    plugin_process = None
    try:
        peer.exchange(_hello([
            "transport.concurrent-router", "assistant.plugins-v1", "settings.provider-model",
        ]))
        peer.request("core.initialize")
        peer.readiness("ready")
        settings = peer.request("settings.executor.get")
        assert settings["selected_service_key"] == settings["applied_service_key"] == SERVICE_KEY
        assert settings["state"] == "ready"
        assert any(item["serviceKey"] == SERVICE_KEY for item in settings["candidates"])
        plugins = peer.request("plugins.settings.get")["plugins"]
        assert [(item["pluginId"], item["state"]) for item in plugins] == [("focus_companion", "active")]
        plugin_process = next(
            child for child in psutil.Process(process.pid).children(recursive=True)
            if "focus_companion" in child.cmdline()
        )
        assert len({os.getpid(), process.pid, plugin_process.pid}) == 3

        peer.chat("focus-complete", "专注 1 秒 整理桌面")
        completed = peer.completed("focus-complete")
        assert "整理桌面" in completed["reply"]["segments"][0]["text"]
        assert completed["historyStatus"] == "saved"

        peer.chat("focus-cancel", "专注 60 秒 阅读")
        progress = peer.event("focus-cancel", "chat.progress")
        assert progress["name"] == "chat.progress", progress
        assert "阅读" in progress["payload"]["text"]
        assert "reply" not in progress["payload"]
        active = peer.request("core.snapshot")["activeInteractionSummary"]
        assert active["operationId"] == "focus-cancel"
        assert active["state"] == "started"
        assert "阅读" in active["progress"]
        assert peer.request("chat.cancel", {"operationId": "focus-cancel"})["accepted"] is True
        cancelled = peer.event("focus-cancel", "chat.cancelled")
        assert cancelled["name"] == "chat.cancelled", cancelled

        # A subsequent real operation supplies a completion boundary without a guessed sleep.
        peer.chat("focus-after-cancel", "专注 1 秒 写笔记")
        assert "写笔记" in peer.completed("focus-after-cancel")["reply"]["segments"][0]["text"]
        assert peer.request("core.snapshot")["activeInteractionSummary"] is None
        assert [frame["name"] for frame in peer.frames if (
            frame.get("kind") == "event" and frame.get("id") == "focus-cancel"
            and frame["name"] in TERMINALS
        )] == ["chat.cancelled"]

        history = TimelineStore(user / "data/chat_history/timeline.sqlite3").read_all("sakura")
        assert [entry.kind for entry in history] == [
            TimelineKind.HUMAN, TimelineKind.ASSISTANT, TimelineKind.HUMAN,
            TimelineKind.HUMAN, TimelineKind.ASSISTANT,
        ]
        assert [entry.payload["text"] for entry in history if entry.kind == TimelineKind.HUMAN] == [
            "专注 1 秒 整理桌面", "专注 60 秒 阅读", "专注 1 秒 写笔记",
        ]

        peer.request("settings.executor.save", {"serviceKey": ""})
        peer.readiness("setup_required")
        settings = peer.request("settings.executor.get")
        assert settings["selected_service_key"] == ""
        assert settings["state"] == "unavailable"
        assert any(item["serviceKey"] == SERVICE_KEY for item in settings["candidates"])
        provider = peer.request("settings.provider_model.get")
        assert provider["setup_complete"] is False
        assert provider["providers"] == []
        assert peer.request("plugins.settings.get")["plugins"][0]["state"] == "active"
        assert not (user / "config/api.yaml").exists()

        peer.request("settings.executor.save", {"serviceKey": SERVICE_KEY})
        peer.readiness("ready")
        assert peer.request("settings.executor.get")["state"] == "ready"
    finally:
        _stop(process)
    assert process.returncode == 0
    assert plugin_process is not None and not plugin_process.is_running()
