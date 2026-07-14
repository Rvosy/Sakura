from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def test_production_entry_import_graph_does_not_load_qt() -> None:
    source = """
import json, sys
import main
print(json.dumps({
    "qt": [name for name in sys.modules if name.startswith("PySide6")],
    "ui": [name for name in sys.modules if name == "app.ui" or name.startswith("app.ui.")],
}))
"""
    result = subprocess.run(
        [str(ROOT / "runtime" / "python.exe"), "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout) == {"qt": [], "ui": []}


def test_production_entry_launches_tauri_with_current_python(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import main

    desktop_exe = tmp_path / "sakura-desktop.exe"
    desktop_exe.write_bytes(b"MZ")
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "resolve_tauri_executable", lambda _base_dir: desktop_exe)
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda command, **kwargs: calls.append({"command": command, **kwargs})
        or SimpleNamespace(returncode=7),
    )

    assert main.main() == 7
    assert calls[0]["command"] == [str(desktop_exe)]
    assert calls[0]["cwd"] == tmp_path
    environment = calls[0]["env"]
    assert isinstance(environment, dict)
    assert environment["SAKURA_PYTHON_EXE"] == sys.executable
    assert environment["SAKURA_BASE_DIR"] == str(tmp_path)


def test_production_entry_has_explicit_legacy_fallback_without_automatic_import() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    legacy = ROOT / "legacy_qt_main.py"

    assert legacy.is_file()
    assert "PySide6" not in source
    assert "QApplication" not in source
    assert "legacy_qt_main" not in source
    assert "PetWindow" not in source
    assert "from PySide6" in legacy.read_text(encoding="utf-8")


def test_base_requirements_exclude_qt_and_optional_fallback_keeps_it() -> None:
    base = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    legacy = (ROOT / "requirements-legacy-qt.txt").read_text(encoding="utf-8")

    assert "PySide6" not in base
    assert "PySide6" not in development
    assert "pytest-qt" not in development
    assert "PySide6" in legacy
    assert "pytest-qt" in legacy


def test_production_dependency_sources_have_no_pyside_imports() -> None:
    checked = [ROOT / "main.py", ROOT / "app" / "core" / "assistant_service.py"]
    checked.extend(sorted((ROOT / "app" / "brain_host").glob("*.py")))

    matches = {
        str(path.relative_to(ROOT)): [line for line in path.read_text(encoding="utf-8").splitlines() if "PySide6" in line]
        for path in checked
    }

    assert all(not lines for lines in matches.values()), matches


def test_tauri_remains_the_only_owner_of_brain_host_process() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )
    rust_entry = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    assert "app.brain_host" not in main_source
    assert "BrainHostSupervisor" in rust_state
    assert "tauri_plugin_single_instance::init" in rust_entry


def test_windows_installer_does_not_require_legacy_qt() -> None:
    installer = (ROOT / "install.bat").read_text(encoding="utf-8")

    assert "PySide6" not in installer
    assert "项目路径包含非英文字符" not in installer
    assert "import playwright" in installer


def test_release_workflows_build_and_package_the_production_desktop() -> None:
    workflows = [
        ROOT / ".github" / "workflows" / "package.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ]

    for path in workflows:
        source = path.read_text(encoding="utf-8")
        assert "desktop/src-tauri" in source, path
        assert "sakura-desktop.exe" in source, path
        assert "sakura-desktop" in source, path
        assert "legacy_qt_main.py" in source, path
        assert "requirements-legacy-qt.txt" in source, path


def test_phase_one_docs_describe_tauri_brain_and_compatibility_boundary() -> None:
    technical = (ROOT / "docs" / "TECHNICAL_README.md").read_text(encoding="utf-8")
    contributing = (ROOT / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "Tauri" in technical
    assert "Brain Host" in technical
    assert "Capability Broker" in technical
    assert "Permission Manager" in technical
    assert "插件沙箱" in technical
    assert "desktop/src-tauri" in contributing
    assert "cargo test --manifest-path desktop/src-tauri/Cargo.toml" in contributing
