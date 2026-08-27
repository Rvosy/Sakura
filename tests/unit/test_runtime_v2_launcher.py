from __future__ import annotations

from pathlib import Path

import main


def _binary(root: Path, profile: str) -> Path:
    return root / "desktop" / "src-tauri" / "target" / profile / main.tauri_binary_name()


def test_development_launcher_selects_only_debug_shell(tmp_path: Path) -> None:
    debug = _binary(tmp_path, "debug")
    release = _binary(tmp_path, "release")
    debug.parent.mkdir(parents=True)
    release.parent.mkdir(parents=True)
    debug.touch()
    release.touch()

    assert main.resolve_tauri_binary(tmp_path) == debug

    debug.unlink()
    assert main.resolve_tauri_binary(tmp_path) is None
