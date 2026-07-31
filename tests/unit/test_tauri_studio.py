from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ui.tauri_studio import (
    TAURI_STUDIO_PROTOCOL_VERSION,
    build_tauri_studio_request,
    dispatch_tauri_studio_rpc,
)


def test_tauri_studio_request_exposes_protocol_theme_and_characters(tmp_path: Path) -> None:
    request = build_tauri_studio_request(
        tmp_path,
        initial_character_id="sakura",
        nonce="fixed",
    )

    assert request["version"] == TAURI_STUDIO_PROTOCOL_VERSION
    assert request["nonce"] == "fixed"
    assert request["initial_character_id"] == "sakura"
    assert request["characters"] == []
    assert request["theme_fields"]
    assert request["theme_defaults"]


def test_tauri_studio_rpc_routes_create_import_save_and_export(tmp_path: Path) -> None:
    portrait_source = tmp_path / "source.png"
    portrait_source.write_bytes(b"portrait")
    created = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )
    portrait = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_portrait",
        {
            "workspace_id": created["workspace_id"],
            "path": str(portrait_source),
            "label": "default",
        },
    )
    doc = created["doc"]
    doc["card_text"] = "card"
    doc["default_portrait"] = portrait["relative_path"]
    doc["expressions"] = {"默认": portrait["relative_path"]}
    saved = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.save_character",
        {
            "workspace_id": created["workspace_id"],
            "doc": doc,
            "current_character_id": "sakura",
        },
    )
    exported = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.export_archive",
        {
            "workspace_id": created["workspace_id"],
            "path": str(tmp_path / "demo.char"),
            "include_voice": False,
        },
    )

    assert saved["saved_character_id"] == "demo"
    assert Path(exported["output_path"]).is_file()


def test_tauri_studio_rpc_rejects_unknown_and_malformed_methods(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知 Tauri Studio RPC"):
        dispatch_tauri_studio_rpc(tmp_path, "settings.open", {})
    with pytest.raises(ValueError, match="需要 doc 对象"):
        dispatch_tauri_studio_rpc(tmp_path, "studio.create_character", {"doc": "bad"})


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
