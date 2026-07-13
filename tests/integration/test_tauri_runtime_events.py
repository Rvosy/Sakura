from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.agent.actions import AgentResult
from app.brain_host.application import BrainHostApplication, BrainHostConfig
from app.core.assistant_service import AssistantApplication
from app.llm.chat_reply import ChatReply, ChatSegment
from app.plugins.events import (
    EVENT_APP_CLOSING,
    EVENT_APP_STARTED,
    EVENT_CHAT_MESSAGE_RECEIVED,
    EVENT_CHAT_MESSAGE_SENT,
)
from app.plugins.manager import (
    PLUGIN_EVENT_AI_MESSAGE,
    PLUGIN_EVENT_APP_START,
    PLUGIN_EVENT_CHARACTER_LOADED,
    PLUGIN_EVENT_USER_MESSAGE,
)
from app.storage.chat_history import ChatHistoryStore


ROOT = Path(__file__).resolve().parents[2]


class _Pipeline:
    def run_user_message(self, _messages, **_kwargs: Any) -> AgentResult:  # type: ignore[no-untyped-def]
        return AgentResult(ChatReply([ChatSegment("返事。", translation="回复。")]))


class _PluginManager:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str]] = []
        self.bus_events: list[tuple[str, dict[str, Any]]] = []
        self.shutdown_calls = 0

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> None:
        self.events.append((event_type, payload, source))

    def emit_bus_event(self, event_name: str, payload: dict[str, Any]) -> None:
        self.bus_events.append((event_name, payload))

    def shutdown_all(self) -> None:
        self.shutdown_calls += 1


class _MCPProvider:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _application(tmp_path: Path) -> tuple[BrainHostApplication, _PluginManager, _MCPProvider]:
    manager = _PluginManager()
    mcp = _MCPProvider()
    application = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-runtime", "credential-runtime", 1)
    )
    application.context = SimpleNamespace(
        character_profile=SimpleNamespace(id="demo", display_name="Demo"),
        history_store=ChatHistoryStore(tmp_path / "history.jsonl", "Demo"),
        plugin_manager=manager,
        mcp_tool_provider=mcp,
        tts_provider=None,
        resource_registry=SimpleNamespace(stop_all=lambda *_args: None),
    )
    application.assistant = AssistantApplication(_Pipeline(), session_id="session-runtime")
    application.state = "ready"
    return application, manager, mcp


def test_tray_single_instance_and_secondary_window_contract() -> None:
    tray = (ROOT / "desktop" / "src-tauri" / "src" / "tray.rs").read_text(encoding="utf-8")
    lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    for item in ("显示 Sakura", "隐藏 Sakura", "设置", "对话历史", "角色工作室", "退出"):
        assert item in tray
    for function in ("open_settings_window", "open_history_window", "open_studio_window"):
        assert function in tray
    assert "tauri_plugin_single_instance::init" in lib
    assert "windows::show_main_window(app)" in lib


def test_brain_host_emits_plugin_events_and_closes_runtime_services(tmp_path: Path) -> None:
    application, manager, mcp = _application(tmp_path)
    event = threading.Event()
    application.set_event_sink(
        lambda name, _payload: event.set() if name == "chat.reply" else None
    )

    application._emit_plugin_runtime_started()
    application.handle_request("chat.send", {"text": "你好"})
    assert event.wait(2)
    deadline = time.monotonic() + 2
    while not any(item[0] == PLUGIN_EVENT_AI_MESSAGE for item in manager.events):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    application.shutdown()

    assert [item[0] for item in manager.events] == [
        PLUGIN_EVENT_APP_START,
        PLUGIN_EVENT_CHARACTER_LOADED,
        PLUGIN_EVENT_USER_MESSAGE,
        PLUGIN_EVENT_AI_MESSAGE,
    ]
    assert [item[0] for item in manager.bus_events] == [
        EVENT_APP_STARTED,
        EVENT_CHAT_MESSAGE_RECEIVED,
        EVENT_CHAT_MESSAGE_SENT,
        EVENT_APP_CLOSING,
    ]
    assert manager.shutdown_calls == 1
    assert mcp.close_calls == 1


def test_mobile_chat_uses_brain_busy_lane(tmp_path: Path) -> None:
    application, _manager, _mcp = _application(tmp_path)
    observed: list[str | None] = []

    class Bridge:
        def execute_chat(
            self,
            character_id: str,
            text: str,
            image_data_url: str,
        ) -> dict[str, Any]:
            observed.append(application._background_event_kind)
            return {
                "character_id": character_id,
                "reply": "回复。",
                "reply_raw": "返事。",
                "segments": [],
                "actions": [],
            }

    result = application.submit_mobile_chat(Bridge(), "demo", "你好", "")

    assert result["reply"] == "回复。"
    assert observed == ["mobile_chat"]
    assert application._background_event_kind is None
    application.shutdown()


def test_brain_host_emits_tts_plugin_events(tmp_path: Path) -> None:
    application, manager, _mcp = _application(tmp_path)
    payload = {
        "synthesis_id": "tts-1",
        "text": "你好",
        "tone": "default",
        "character_id": "demo",
    }

    application._emit_tts_plugin_started(payload)
    application._emit_tts_plugin_finished({**payload, "status": "ready"})

    assert [item[0] for item in manager.events] == ["tts.start", "tts.end"]
    assert [item[0] for item in manager.bus_events] == ["tts.started", "tts.finished"]
    application.shutdown()


def test_headless_plugin_and_mobile_import_graph_does_not_load_qt() -> None:
    source = """
import json, os, sys
os.environ["SAKURA_HEADLESS"] = "1"
import app.plugins.manager
import app.plugins.services
import plugins.sakura_mobile.plugin
import plugins.sakura_mobile.server
print(json.dumps({"qt": [name for name in sys.modules if name.startswith("PySide6")]}))
"""
    result = subprocess.run(
        [str(ROOT / "runtime" / "python.exe"), "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout)["qt"] == []
    assert "from app.ui.theme" not in (
        ROOT / "plugins" / "sakura_mobile" / "server.py"
    ).read_text(encoding="utf-8")
