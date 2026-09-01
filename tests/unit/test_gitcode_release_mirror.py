from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gitcode_release_mirror import (
    GitCodeMirrorError,
    build_gitcode_manifest,
    release_asset_download_url,
    rewrite_updater_manifest,
    split_repository,
)


def _manifest() -> dict[str, object]:
    return {
        "version": "1.0.3",
        "notes": "same notes",
        "pub_date": "2026-09-01T00:00:00Z",
        "platforms": {
            "windows-x86_64": {
                "signature": "windows-signature",
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-windows-x64-setup.exe",
            },
            "darwin-aarch64": {
                "signature": "mac-signature",
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-macos-arm64.app.tar.gz",
            },
        },
        "portable": {
            "windows-x86_64": {
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-windows-x64-portable.zip",
                "sha256": "a" * 64,
            }
        },
    }


def test_gitcode_manifest_changes_only_download_urls(tmp_path: Path) -> None:
    source = tmp_path / "latest.json"
    destination = tmp_path / "gitcode-latest.json"
    original = _manifest()
    source.write_text(json.dumps(original), encoding="utf-8")

    build_gitcode_manifest(
        source,
        destination,
        repository="Rvosy/Sakura",
        tag="v1.0.3",
    )

    mirrored = json.loads(destination.read_text(encoding="utf-8"))
    assert mirrored["version"] == original["version"]
    assert mirrored["notes"] == original["notes"]
    assert mirrored["pub_date"] == original["pub_date"]
    assert mirrored["platforms"]["windows-x86_64"]["signature"] == "windows-signature"
    assert mirrored["platforms"]["darwin-aarch64"]["signature"] == "mac-signature"
    assert mirrored["portable"]["windows-x86_64"]["sha256"] == "a" * 64
    assert mirrored["platforms"]["windows-x86_64"]["url"] == (
        "https://api.gitcode.com/api/v5/repos/Rvosy/Sakura/releases/v1.0.3/"
        "attach_files/Sakura-1.0.3-windows-x64-setup.exe/download"
    )
    assert mirrored["portable"]["windows-x86_64"]["url"] == (
        "https://api.gitcode.com/api/v5/repos/Rvosy/Sakura/releases/v1.0.3/"
        "attach_files/Sakura-1.0.3-windows-x64-portable.zip/download"
    )


def test_gitcode_manifest_rewrite_does_not_mutate_source() -> None:
    original = _manifest()
    snapshot = json.loads(json.dumps(original))
    mirrored = rewrite_updater_manifest(
        original,
        owner="Rvosy",
        repo="Sakura",
        tag="v1.0.3",
    )
    assert original == snapshot
    assert mirrored != original


def test_gitcode_download_url_encodes_asset_name() -> None:
    assert release_asset_download_url("owner", "repo", "v1.0.3", "Sakura test.zip") == (
        "https://api.gitcode.com/api/v5/repos/owner/repo/releases/v1.0.3/"
        "attach_files/Sakura%20test.zip/download"
    )


def test_gitcode_repository_requires_owner_and_repo() -> None:
    assert split_repository("Rvosy/Sakura") == ("Rvosy", "Sakura")
    with pytest.raises(GitCodeMirrorError, match="GITCODE_REPOSITORY_INVALID"):
        split_repository("Sakura")
