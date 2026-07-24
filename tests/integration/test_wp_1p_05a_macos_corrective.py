from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.parametrize(
    ("platform", "binary_name"),
    [
        ("win32", "sakura-runtime-v2-shell.exe"),
        ("darwin", "sakura-runtime-v2-shell"),
        ("linux", "sakura-runtime-v2-shell"),
    ],
)
def test_runtime_v2_entry_resolves_platform_specific_shell_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    binary_name: str,
) -> None:
    runtime_entry = importlib.import_module("main")
    release = tmp_path / "desktop" / "src-tauri" / "target" / "release" / binary_name
    debug = tmp_path / "desktop" / "src-tauri" / "target" / "debug" / binary_name
    _write_executable(debug, "exit 0")
    _write_executable(release, "exit 0")

    monkeypatch.setattr(runtime_entry.sys, "platform", platform)

    assert runtime_entry.resolve_tauri_binary(tmp_path) == release


@pytest.mark.parametrize("profile", ["debug", "release"])
def test_start_sh_executes_built_macos_shell_before_runtime_python(
    tmp_path: Path,
    profile: str,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    start_script = scripts / "start.sh"
    shutil.copy2(ROOT / "scripts" / "start.sh", start_script)
    _write_executable(tmp_path / "runtime" / "bin" / "python3", "echo python-fallback >&2; exit 91")
    _write_executable(
        tmp_path
        / "desktop"
        / "src-tauri"
        / "target"
        / profile
        / "sakura-runtime-v2-shell",
        f"echo shell-{profile}; exit 37",
    )

    completed = subprocess.run(
        ["bash", str(start_script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CI": "true"},
    )

    assert completed.returncode == 37
    assert completed.stdout.strip() == f"shell-{profile}"
    assert "python-fallback" not in completed.stderr


def test_tauri_config_enables_required_macos_transparent_window_support() -> None:
    config = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    cargo_toml = (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text("utf-8")
    styles = (ROOT / "desktop" / "frontend" / "styles.css").read_text("utf-8")

    assert config["app"]["macOSPrivateApi"] is True
    assert config["app"]["windows"][0]["transparent"] is True
    assert config["app"]["windows"][0]["visible"] is True
    assert 'tauri = { version = "=2.11.3", features = ["macos-private-api"] }' in cargo_toml
    assert 'body[data-shell-state="pet-geometry-loading"]' in styles
    assert "opacity: 0;" in styles


def test_deferred_drag_does_not_depend_on_webview_event_registration() -> None:
    source = (ROOT / "desktop" / "frontend" / "app.js").read_text("utf-8")

    assert "window.__TAURI__.event" not in source
    assert 'window.addEventListener("tauri://move"' not in source
    assert "commit_pet_drag" not in source


def test_deferred_drag_anchor_is_committed_by_the_native_window_event_loop() -> None:
    rust_source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")
    frontend_source = (ROOT / "desktop" / "frontend" / "app.js").read_text("utf-8")

    assert "tauri::WindowEvent::Moved(position)" in rust_source
    assert "commit_deferred_pet_drag" in rust_source
    assert "commit_pet_drag" not in frontend_source


def test_deferred_drag_session_is_reserved_before_native_drag_starts() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")

    assert source.index("session.begin_deferred_drag();") < source.index(
        ".start_drag(&window)",
    )
    assert "session.cancel_deferred_drag();" in source


def test_shell_close_releases_the_process_lifecycle_not_just_its_window() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")

    assert "fn close_pet_window(window: WebviewWindow, app_handle: tauri::AppHandle)" in source
    assert "app_handle.exit(0);" in source
