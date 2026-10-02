from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.release.release_notes import release_changes, render_body, version_changes


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.4.0"
CHANGES = "新增功能。\n\n### 使用说明\n\n- 保留字面量 `$value`。\n- [安装](userdocs/SETUP.md#开始)"


def test_selects_only_exact_version_with_prereleases_and_previous_notes() -> None:
    changelog = (
        "# 更新日志\n\n## 1.4.0-rc.1\n\n预发布内容。\n\n"
        f"## {VERSION}（待发布）\n\n{CHANGES}\n\n"
        "## 1.3.0 - 2026-09-30\n\n旧版内容。\n"
    )
    assert version_changes(changelog, VERSION) == CHANGES
    assert version_changes(changelog, "1.4.0-rc.1") == "预发布内容。"


@pytest.mark.parametrize("changelog", [
    "## 1.4.1\n\n不属于当前版本。\n",
    "## 1.4.0（待发布）\n\n## 1.3.0\n\n旧版内容。\n",
    "## 1.4.0\n\n内容。\n\n## 1.4.0\n\n重复内容。\n",
])
def test_rejects_missing_empty_or_duplicate_current_version(changelog: str) -> None:
    with pytest.raises(ValueError, match="CHANGELOG"):
        version_changes(changelog, VERSION)


def test_links_resolve_at_released_tag_without_changing_external_or_anchor_links() -> None:
    source = (
        "[指南](userdocs/SETUP.md#开始) [首页](../README.md) "
        "[外部](https://example.test/guide) [同页](#使用说明)"
    )
    actual = release_changes(source, "example/Sakura", "1.4.0-rc.1")
    assert "https://github.com/example/Sakura/blob/v1.4.0-rc.1/docs/userdocs/SETUP.md#开始" in actual
    assert "https://github.com/example/Sakura/blob/v1.4.0-rc.1/README.md" in actual
    assert "[外部](https://example.test/guide) [同页](#使用说明)" in actual


def test_refuses_to_link_missing_recommended_installers() -> None:
    template = (ROOT / "packaging/release-body.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="windows-x64-setup.exe"):
        render_body(template, CHANGES, VERSION, "Rvosy/Sakura", set())


def test_cli_refreshes_portable_links_and_keeps_updater_notes_separate(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "docs").mkdir(parents=True)
    (root / "packaging").mkdir()
    (root / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    (root / "docs/CHANGELOG.md").write_text(
        f"# 更新日志\n\n## {VERSION}（待发布）\n\n{CHANGES}\n\n## 1.3.0\n\n旧版内容。\n",
        encoding="utf-8",
    )
    (root / "packaging/release-body.md").write_text(
        (ROOT / "packaging/release-body.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    assets = tmp_path / "release-assets"
    assets.mkdir()
    for suffix in (
        "windows-x64-setup.exe", "macos-arm64.dmg", "macos-arm64.app.zip",
        "linux-x64.AppImage", "linux-x64.deb", "macos-open-help.html",
    ):
        (assets / f"Sakura-{VERSION}-{suffix}").write_bytes(b"artifact fixture")
    (assets / f"Sakura-Playwright-{VERSION}.sakplugin.zip").write_bytes(b"plugin fixture")
    body_path = tmp_path / "release-body.md"
    notes_path = tmp_path / "release-notes.md"
    command = [
        sys.executable, "-m", "tools.release.release_notes", "--root", str(root),
        "--repository", "example/Sakura", "--assets-dir", str(assets),
        "--output", str(body_path), "--notes-output", str(notes_path),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    initial_body = body_path.read_text(encoding="utf-8")
    assert "windows-x64-portable.zip" not in initial_body
    assert "待发布" not in initial_body
    assert "旧版内容" not in initial_body
    assert initial_body.index("## 下载") < initial_body.index("## 本次更新")

    portable = assets / f"Sakura-{VERSION}-windows-x64-portable.zip"
    portable.write_bytes(b"portable fixture")
    subprocess.run(command, cwd=ROOT, check=True)
    final_body = body_path.read_text(encoding="utf-8")
    assert final_body.count(portable.name) == 1
    assert final_body.count("新增功能。") == 1
    download_links = re.findall(r"https://github.com/example/Sakura/releases/download/v1\.4\.0/([^\s)]+)", final_body)
    assert set(download_links) == {path.name for path in assets.iterdir()}
    notes = notes_path.read_text(encoding="utf-8").strip()
    assert notes == release_changes(CHANGES, "example/Sakura", VERSION)

    updater_path = tmp_path / "latest.json"
    signature = assets / (f"Sakura-{VERSION}-windows-x64-setup.exe.sig")
    signature.write_text("signature-fixture", encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "tools.release.updater_manifest", "--version", VERSION,
        "--notes-file", str(notes_path), "--base-url", "https://example.test/releases",
        "--output", str(updater_path), "--allow-platform-subset",
        "--release", "windows-x64", str(assets / f"Sakura-{VERSION}-windows-x64-setup.exe"), str(signature),
    ], cwd=ROOT, check=True)
    manifest = json.loads(updater_path.read_text(encoding="utf-8"))
    assert manifest["notes"] == notes
    assert "releases/download" not in manifest["notes"]


def test_publish_and_portable_refresh_keep_github_generated_footer() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    for job, published_file in (
        ("publish", "release-assets/*"),
        ("publish-portable", "windows-x64-portable.zip"),
    ):
        steps = workflow["jobs"][job]["steps"]
        publication = next(
            step for step in steps
            if step.get("uses", "").startswith("softprops/action-gh-release@")
            and published_file in step["with"]["files"]
        )
        # The action prepends body_path to GitHub's PR list and compare link.
        assert publication["with"]["body_path"] == "release-body.md"
        assert publication["with"]["generate_release_notes"] is True
        assert not publication["with"].get("append_body", False)
        generator = next(
            step for step in steps if "tools.release.release_notes" in step.get("run", "")
        )
        assert steps.index(generator) < steps.index(publication)
