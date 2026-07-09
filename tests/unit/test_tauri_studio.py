from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_minimal_character(root: Path, character_id: str = "sakura") -> None:
    package_dir = root / "characters" / character_id
    package_dir.mkdir(parents=True)
    (package_dir / "card.md").write_text("card", encoding="utf-8")
    (package_dir / "portrait.png").write_bytes(b"png")
    (package_dir / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_resolve_tauri_studio_binary_uses_env_and_platform(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_studio as tauri_studio

    custom = tmp_path / "custom-studio.exe"
    custom.write_text("bin", encoding="utf-8")
    assert tauri_studio.resolve_tauri_studio_binary(
        tmp_path,
        environ={tauri_studio.TAURI_STUDIO_BIN_ENV: str(custom)},
    ) == custom

    release = tmp_path / "tools" / "studio-tauri" / "src-tauri" / "target" / "release"
    release.mkdir(parents=True)
    win_bin = release / "sakura-studio.exe"
    unix_bin = release / "sakura-studio"
    win_bin.write_text("win", encoding="utf-8")
    unix_bin.write_text("unix", encoding="utf-8")

    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    assert tauri_studio.resolve_tauri_studio_binary(tmp_path, environ={}) == win_bin
    monkeypatch.setattr(tauri_studio.sys, "platform", "darwin")
    assert tauri_studio.resolve_tauri_studio_binary(tmp_path, environ={}) == unix_bin


def test_build_tauri_studio_request_contains_characters_and_nonce(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService
    from app.ui.tauri_studio import build_tauri_studio_request

    _write_minimal_character(tmp_path)

    request = build_tauri_studio_request(tmp_path, initial_character_id="sakura", nonce="nonce")

    assert request["version"] == 1
    assert request["nonce"] == "nonce"
    assert request["initial_character_id"] == "sakura"
    assert request["characters"][0]["id"] == "sakura"
    assert request["theme_fields"]
    assert CharacterStudioService(tmp_path).list_characters(current_character_id="sakura")[0]["is_current"] is True


def test_dispatch_tauri_studio_rpc_routes_core_methods(tmp_path: Path) -> None:
    from app.ui.tauri_studio import dispatch_tauri_studio_rpc

    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    created = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )
    draft_dir = created["package_dir"]
    portrait = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_portrait",
        {"package_dir": draft_dir, "path": str(source), "label": "default"},
    )
    doc = created["doc"]
    doc["card_text"] = "card"
    doc["default_portrait"] = portrait["relative_path"]
    saved = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.save_character",
        {"package_dir": draft_dir, "doc": doc, "current_character_id": "sakura"},
    )

    assert saved["saved_character_id"] == "demo"
    assert saved["current_character_id"] == "sakura"
    assert (tmp_path / "characters" / "demo" / "character.json").exists()


def test_tauri_studio_process_writes_rpc_response_line(tmp_path: Path) -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import (
        TAURI_STUDIO_RPC_MARKER,
        TAURI_STUDIO_RPC_RESULT_MARKER,
        TauriStudioProcess,
    )

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk
            self.writes: list[bytes] = []

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    request = {"id": "rpc-1", "method": "studio.list_characters", "params": {}}
    fake = FakeQProcess(
        (TAURI_STUDIO_RPC_MARKER + json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    )
    process = TauriStudioProcess(tmp_path, initial_character_id="")
    process._process = fake

    process._handle_stdout()

    line = b"".join(fake.writes).decode("utf-8").strip()
    assert line.startswith(TAURI_STUDIO_RPC_RESULT_MARKER)
    payload = json.loads(line[len(TAURI_STUDIO_RPC_RESULT_MARKER):])
    assert payload["id"] == "rpc-1"
    assert payload["ok"] is True
    assert payload["result"]["characters"] == []
