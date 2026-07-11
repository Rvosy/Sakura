from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def keep_qapplication_alive(qapp: Any) -> Iterable[None]:
    """让 UI 测试全程持有 QApplication，避免 pytest-qt 处理事件时对象已被回收。"""
    _ = qapp
    yield


def _write_pet_window_runtime_root(root: Path, QPixmap, Qt) -> None:  # type: ignore[no-untyped-def]
    config_dir = root / "data" / "config"
    character_dir = root / "characters" / "demo"
    config_dir.mkdir(parents=True)
    character_dir.mkdir(parents=True)
    (config_dir / "api.yaml").write_text(
        "llm:\n"
        "  base_url: https://api.example.com/v1\n"
        "  api_key: test-key\n"
        "  model: test-model\n"
        "tts:\n"
        "  provider: none\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    (config_dir / "characters.yaml").write_text(
        "current_character_id: demo\n",
        encoding="utf-8",
    )
    (config_dir / "system_config.yaml").write_text(
        "ui:\n"
        "  portrait_scale_percent: 100\n"
        "memory_curation:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    portrait = QPixmap(320, 480)
    portrait.fill(Qt.GlobalColor.white)
    assert portrait.save(str(character_dir / "portrait.png"))
    (character_dir / "character.json").write_text(
        "{\n"
        '  "id": "demo",\n'
        '  "display_name": "Demo",\n'
        '  "initial_message": "hello",\n'
        '  "card": "card.md",\n'
        '  "portrait": {"default": "portrait.png"}\n'
        "}\n",
        encoding="utf-8",
    )


@pytest.fixture
def pet_window_factory(qtbot, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from app.agent.memory import MemoryStore
    from app.core.bootstrap import build_initial_app_context
    from app.ui.pet_window import PetWindow

    monkeypatch.setattr(MemoryStore, "preload", lambda *args, **kwargs: None)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda self: None)
    monkeypatch.setattr(PetWindow, "_activate_renderer_manager", lambda self: None)
    windows = []

    def create(*, startup_initializing: bool = False):  # type: ignore[no-untyped-def]
        root = tmp_path / f"pet_window_{len(windows)}"
        _write_pet_window_runtime_root(root, QPixmap, Qt)
        context = build_initial_app_context(root)
        context = replace(context, startup_initializing=startup_initializing)
        window = PetWindow(context)
        window.reminder_timer.stop()
        window.screen_awareness_timer.stop()
        qtbot.addWidget(window)
        window.show()
        windows.append(window)
        return window

    yield create

    for window in reversed(windows):
        window._shutdown_in_progress = False
        window.close_external_tools()
        window.close()


@pytest.fixture
def pet_window(pet_window_factory):  # type: ignore[no-untyped-def]
    return pet_window_factory(startup_initializing=False)


@pytest.fixture
def startup_pet_window(pet_window_factory):  # type: ignore[no-untyped-def]
    return pet_window_factory(startup_initializing=True)
