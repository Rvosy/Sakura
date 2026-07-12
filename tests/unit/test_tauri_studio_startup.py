from __future__ import annotations

import json
from pathlib import Path


def test_tauri_studio_waits_for_initialized_ui_before_showing() -> None:
    root = Path(__file__).parents[2] / "tools" / "studio-tauri"
    config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    source = (root / "frontend" / "studio.js").read_text(encoding="utf-8")
    rust_source = (root / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert config["app"]["windows"][0]["visible"] is False
    startup = source.split("async function startStudio()", 1)[1]
    assert startup.index("await load();") < startup.index('await invoke("show_studio");')
    assert "fn show_studio(window: Window)" in rust_source
    assert "window.show()" in rust_source
    assert "window.set_focus()" in rust_source
    assert "show_studio," in rust_source
