from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.brain_host.application import BrainHostApplication, BrainHostConfig
from app.brain_host.secondary_windows import diagnostics_snapshot, history_page
from app.storage.chat_history import ChatHistoryStore


ROOT = Path(__file__).resolve().parents[2]


def test_secondary_frontends_are_part_of_one_tauri_app() -> None:
    frontend = ROOT / "desktop" / "frontend"
    rust_windows = (ROOT / "desktop" / "src-tauri" / "src" / "windows.rs").read_text(
        encoding="utf-8"
    )
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )
    rust_lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    for relative in (
        "settings/index.html",
        "settings/settings.js",
        "settings/styles.css",
        "studio/index.html",
        "studio/studio.js",
        "studio/styles.css",
        "history/index.html",
        "history/history.js",
        "diagnostics/index.html",
        "diagnostics/diagnostics.js",
    ):
        assert (frontend / relative).is_file()
    for kind in ("settings", "studio", "history", "diagnostics"):
        assert f'"{kind}"' in rust_windows
        assert f"open_{kind}_window" in rust_windows
        assert f"windows::open_{kind}_window" in rust_lib
    assert "run_on_main_thread" in rust_windows
    assert ".always_on_top(true)" in rust_windows
    for command in (
        "load_request",
        "host_call",
        "save_settings",
        "apply_settings",
        "preview_layout",
        "cancel_settings",
        "show_studio",
        "close_studio",
    ):
        assert f"fn {command}" in rust_state
        assert f"app_state::{command}" in rust_lib


def test_secondary_python_import_graph_does_not_load_qt() -> None:
    source = """
import json, sys
import app.brain_host.secondary_windows
import app.core.settings_resource_tasks
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


def test_history_uses_cursor_paging_without_returning_all_records(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path / "history.jsonl", "Demo")
    for index in range(7):
        store.append("user" if index % 2 == 0 else "assistant", f"message-{index}")
    context = SimpleNamespace(history_store=store)

    first = history_page(context, cursor=None, limit=3)
    second = history_page(context, cursor=first["nextCursor"], limit=3)

    assert [item["content"] for item in first["items"]] == [
        "message-4",
        "message-5",
        "message-6",
    ]
    assert [item["content"] for item in second["items"]] == [
        "message-1",
        "message-2",
        "message-3",
    ]
    assert first["hasMore"] is True
    assert second["nextCursor"] == "6"
    assert all("_debug" not in item for item in first["items"])


def test_diagnostics_snapshot_reports_brain_plugins_mcp_tts_and_resources(tmp_path: Path) -> None:
    application = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-diagnostics", "credential-diagnostics", 1)
    )
    application.state = "ready"
    application.assistant = SimpleNamespace(busy=False)
    application.tts_service = SimpleNamespace(service_ready=True)
    application.scheduler = SimpleNamespace(running=True, job_names=("reminders",))
    application.context = SimpleNamespace(
        character_profile=SimpleNamespace(id="demo"),
        mcp_tool_provider=SimpleNamespace(list_tools=lambda: [SimpleNamespace(name="desktop")]),
        plugin_manager=SimpleNamespace(
            results=[
                SimpleNamespace(
                    spec=SimpleNamespace(plugin_id="demo"),
                    loaded=True,
                    error=None,
                )
            ]
        ),
        resource_registry=SimpleNamespace(
            active_resource_count=2,
            resource_labels=("plugin:demo", "mcp"),
        ),
    )

    snapshot = diagnostics_snapshot(application)

    assert snapshot["brain"]["state"] == "ready"
    assert snapshot["plugins"] == {"loaded": 1, "failed": 0, "items": [{"id": "demo", "loaded": True, "error": ""}]}
    assert snapshot["mcp"]["toolCount"] == 1
    assert snapshot["tts"]["ready"] is True
    assert snapshot["resources"]["activeCount"] == 2
    assert snapshot["scheduler"]["jobs"] == ["reminders"]


def test_brain_routes_secondary_window_requests(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    application = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-secondary", "credential-secondary", 1)
    )
    application.state = "ready"
    application.context = SimpleNamespace(character_profile=SimpleNamespace(id="demo"))
    monkeypatch.setattr(
        "app.brain_host.application.secondary_window_request",
        lambda app, kind, payload: {"kind": kind, "payload": dict(payload)},
    )
    monkeypatch.setattr(
        "app.brain_host.application.secondary_host_call",
        lambda app, method, params: {"method": method, "params": dict(params)},
    )

    assert application.handle_request("window.request", {"kind": "history", "cursor": "3"}) == {
        "kind": "history",
        "payload": {"kind": "history", "cursor": "3"},
    }
    assert application.handle_request(
        "window.host_call",
        {"method": "studio.list_characters", "params": {"current_character_id": "demo"}},
    ) == {
        "method": "studio.list_characters",
        "params": {"current_character_id": "demo"},
    }
