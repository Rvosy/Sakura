from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TRACKED_ROOTS = {
    ".gitattributes",
    ".github",
    ".gitignore",
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "VERSION",
    "app",
    "desktop",
    "docs",
    "harness",
    "packaging",
    "plugins",
    "pytest.ini",
    "requirements.txt",
    "scripts",
    "tests",
    "tools",
}


def _git_paths(*arguments: str) -> set[Path]:
    completed = subprocess.run(
        ["git", *arguments, "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {
        Path(value.decode("utf-8"))
        for value in completed.stdout.split(b"\0")
        if value
    }


def test_git_tracked_root_stays_focused() -> None:
    paths = _git_paths("ls-files")
    # Include the projected working tree so this guard also validates a cleanup
    # before its moves have been added to the index.
    paths.difference_update(_git_paths("ls-files", "--deleted"))
    paths.update(_git_paths("ls-files", "--others", "--exclude-standard"))

    assert {path.parts[0] for path in paths} == EXPECTED_TRACKED_ROOTS


def test_product_assets_and_windows_entrypoints_have_explicit_owners() -> None:
    assert (ROOT / "desktop/src-tauri/icons/icon-source.png").is_file()
    for name in ("install.bat", "start.bat", "package.bat"):
        assert (ROOT / "scripts" / name).is_file()
        assert not (ROOT / name).exists()
