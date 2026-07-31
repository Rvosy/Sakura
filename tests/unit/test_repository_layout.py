from __future__ import annotations

import subprocess
from pathlib import Path


REQUIRED_ROOT_ENTRIES = {
    ".gitattributes",
    ".github",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "VERSION",
    "app",
    "desktop",
    "docs",
    "harness",
    "install.bat",
    "legacy_qt_main.py",
    "main.py",
    "plugins",
    "pytest.ini",
    "requirements.txt",
    "scripts",
    "start-legacy-qt.bat",
    "start.bat",
    "tests",
    "third_party",
    "tools",
    "update.bat",
}


def test_tracked_repository_root_matches_the_layout_contract() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    tracked_entries = {
        relative.split("/", 1)[0]
        for relative in result.stdout.decode("utf-8").split("\0")
        if relative and (root / relative).exists()
    }

    assert tracked_entries == REQUIRED_ROOT_ENTRIES
    assert len(tracked_entries) == 25


def test_retired_root_and_legacy_studio_paths_are_absent() -> None:
    root = Path(__file__).parents[2]
    retired = (
        "assets",
        "requirements-dev.txt",
        "requirements-macos-intel.txt",
        "start_studio.bat",
        "update-delete.json",
        "tools/_write_test.py",
        "tools/build-tauri.bat",
    )

    assert [path for path in retired if (root / path).exists()] == []
    legacy_studio = root / "tools" / "studio"
    assert not any(legacy_studio.rglob("*.py"))
    assert not any(legacy_studio.rglob("*.svg"))
