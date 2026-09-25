"""Domestic manifest publication preserves the GitHub signed download contract."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services.sakura import releases as service
from tools.release.updater_manifest import build_manifest


def documents(tmp_path: Path, version: str = "1.2.0", *, linux: bool = False) -> tuple[dict, dict]:
    releases = []
    targets = [
        ("windows-x64", "windows-x64-setup.exe"),
        ("macos-arm64", "macos-arm64.app.tar.gz"),
    ]
    if linux:
        targets.append(("linux-x64", "linux-x64.AppImage.tar.gz"))
    for target, suffix in targets:
        artifact = tmp_path / f"Sakura-{version}-{suffix}"
        artifact.write_bytes(b"signed artifact fixture")
        signature = artifact.with_suffix(artifact.suffix + ".sig")
        signature.write_text(f"signature-for-{target}", encoding="utf-8")
        releases.append((target, artifact, signature))
    portable = tmp_path / f"Sakura-{version}-windows-x64-portable.zip"
    portable.write_bytes(b"portable fixture")
    updater = build_manifest(
        version=version, notes="更新说明", releases=releases, portable=portable,
        base_url=f"https://github.com/Rvosy/Sakura/releases/download/v{version}",
        pub_date="2026-09-13T00:00:00Z",
        # Historical two-platform releases remain valid service input.
        require_all_platforms=linux,
    )
    metadata = {
        "schema": 1, "latest": version, "minimumSupported": None,
        "releaseUrl": f"https://github.com/Rvosy/Sakura/releases/tag/v{version}",
        "publishedAt": "2026-09-13T00:00:00Z", "urgent": False,
        "downloads": {
            "windowsX64Setup": service.asset_url(version, "windows-x64-setup.exe"),
            "windowsX64Portable": service.asset_url(version, "windows-x64-portable.zip"),
            "macosArm64Dmg": service.asset_url(version, "macos-arm64.dmg"),
        },
        "updaterManifestUrl": service.GITHUB_ENDPOINT,
    }
    if linux:
        metadata["downloads"]["linuxX64AppImage"] = service.asset_url(version, "linux-x64.AppImage")
    return metadata, updater


def encoded(value: dict) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_old_domestic_ci_payload_publishes_to_new_domain(tmp_path: Path) -> None:
    metadata, updater = documents(tmp_path)
    root = tmp_path / "public"
    root.mkdir()
    payload = service.build_payload(metadata, updater)
    payload["release"]["updaterManifestUrl"] = service.LEGACY_SERVICE_ENDPOINT
    service.publish(encoded(payload), root)
    actual = json.loads((root / "releases.json").read_text(encoding="utf-8"))
    assert actual["updaterManifestUrl"] == service.SERVICE_ENDPOINT
    assert json.loads((root / "latest.json").read_text(encoding="utf-8")) == updater


@pytest.mark.parametrize("linux", [False, True])
def test_publish_generated_updater_preserves_urls_signatures_and_portable(tmp_path: Path, linux: bool) -> None:
    metadata, updater = documents(tmp_path, linux=linux)
    root = tmp_path / "public"
    root.mkdir()
    payload = service.build_payload(metadata, updater)
    service.publish(encoded(payload), root)
    assert json.loads((root / "latest.json").read_text(encoding="utf-8")) == updater
    actual = json.loads((root / "releases.json").read_text(encoding="utf-8"))
    assert actual == {**metadata, "updaterManifestUrl": service.SERVICE_ENDPOINT}
    # The legacy metadata producer is not mutated by constructing a publication.
    assert metadata["updaterManifestUrl"] == service.GITHUB_ENDPOINT
    service.publish(encoded(payload), root)


@pytest.mark.parametrize("fault", [
    "wrong_version", "foreign_host", "wrong_tag", "missing_signature",
    "missing_platform", "missing_portable", "extra_field", "prerelease",
])
def test_invalid_publication_preserves_both_live_documents(tmp_path: Path, fault: str) -> None:
    metadata, updater = documents(tmp_path)
    root = tmp_path / "public"
    root.mkdir()
    payload = service.build_payload(metadata, updater)
    service.publish(encoded(payload), root)
    before = {name: (root / name).read_bytes() for name in ("latest.json", "releases.json")}
    invalid = deepcopy(payload)
    manifest = invalid["updater"]
    if fault == "wrong_version":
        manifest["version"] = "9.0.0"
    elif fault == "foreign_host":
        manifest["platforms"]["windows-x86_64"]["url"] += ".evil.test"
    elif fault == "wrong_tag":
        manifest["platforms"]["windows-x86_64"]["url"] = service.asset_url("1.1.0", "windows-x64-setup.exe")
    elif fault == "missing_signature":
        manifest["platforms"]["windows-x86_64"]["signature"] = ""
    elif fault == "missing_platform":
        del manifest["platforms"]["darwin-aarch64"]
    elif fault == "missing_portable":
        del manifest["portable"]
    elif fault == "extra_field":
        invalid["destination"] = "../../outside.json"
    else:
        invalid["release"]["latest"] = "1.3.0-beta.1"
    with pytest.raises(ValueError):
        service.publish(encoded(invalid), root)
    assert {name: (root / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("fault,code", [
    ("download_only", "SERVICE_LINUX_RELEASE_INCOMPLETE"),
    ("updater_only", "SERVICE_LINUX_RELEASE_INCOMPLETE"),
    ("download_url", "SERVICE_ASSET_URL_INVALID"),
    ("updater_url", "SERVICE_ASSET_URL_INVALID"),
    ("signature", "SERVICE_SIGNATURE_MISSING"),
    ("unknown_download", "SERVICE_FIELDS_INVALID"),
    ("unknown_platform", "SERVICE_FIELDS_INVALID"),
])
def test_invalid_linux_release_rejects_build_and_preserves_live_documents(tmp_path: Path, fault: str, code: str) -> None:
    metadata, updater = documents(tmp_path)
    root = tmp_path / "public"
    root.mkdir()
    service.publish(encoded(service.build_payload(metadata, updater)), root)
    before = {name: (root / name).read_bytes() for name in ("latest.json", "releases.json")}
    metadata, updater = documents(tmp_path, linux=True)
    if fault == "download_only":
        del updater["platforms"]["linux-x86_64"]
    elif fault == "updater_only":
        del metadata["downloads"]["linuxX64AppImage"]
    elif fault == "download_url":
        metadata["downloads"]["linuxX64AppImage"] += ".wrong"
    elif fault == "updater_url":
        updater["platforms"]["linux-x86_64"]["url"] = metadata["downloads"]["linuxX64AppImage"]
    elif fault == "signature":
        updater["platforms"]["linux-x86_64"]["signature"] = ""
    elif fault == "unknown_download":
        metadata["downloads"]["linuxArm64AppImage"] = metadata["downloads"]["linuxX64AppImage"]
    else:
        updater["platforms"]["linux-aarch64"] = updater["platforms"]["linux-x86_64"]
    with pytest.raises(ValueError, match=code):
        service.build_payload(metadata, updater)
    metadata["updaterManifestUrl"] = service.SERVICE_ENDPOINT
    with pytest.raises(ValueError, match=code):
        service.publish(encoded({"schema": 1, "release": metadata, "updater": updater}), root)
    assert {name: (root / name).read_bytes() for name in before} == before


def test_old_ci_payload_still_works_but_cannot_downgrade_domestic_updater(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    old, _ = documents(tmp_path, "1.9.0")
    service.publish(encoded(old), root)
    assert not (root / "latest.json").exists()
    new, updater = documents(tmp_path, "1.10.0")
    service.publish(encoded(service.build_payload(new, updater)), root)
    with pytest.raises(ValueError, match="SERVICE_VERSION_DOWNGRADE"):
        service.publish(encoded(old), root)
    assert json.loads((root / "releases.json").read_bytes())["latest"] == "1.10.0"


def test_linux_metadata_requires_the_companion_updater_manifest(tmp_path: Path) -> None:
    metadata, _ = documents(tmp_path, linux=True)
    root = tmp_path / "public"
    root.mkdir()
    with pytest.raises(ValueError, match="SERVICE_UPDATER_MANIFEST_REQUIRED"):
        service.publish(encoded(metadata), root)
    assert list(root.iterdir()) == []


def test_partial_publication_can_be_repaired_without_allowing_downgrade(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "public"
    root.mkdir()
    old, old_manifest = documents(tmp_path, "1.9.0")
    service.publish(encoded(service.build_payload(old, old_manifest)), root)
    new, new_manifest = documents(tmp_path, "1.10.0")
    original = service._atomic_write

    def fail_metadata(path: Path, value: dict) -> None:
        if path.name == "releases.json":
            raise OSError("disk failure")
        original(path, value)

    with monkeypatch.context() as context:
        context.setattr(service, "_atomic_write", fail_metadata)
        with pytest.raises(OSError):
            service.publish(encoded(service.build_payload(new, new_manifest)), root)
    with pytest.raises(ValueError, match="SERVICE_VERSION_DOWNGRADE"):
        service.publish(encoded(service.build_payload(old, old_manifest)), root)
    service.publish(encoded(service.build_payload(new, new_manifest)), root)
    assert json.loads((root / "releases.json").read_bytes())["latest"] == "1.10.0"


def test_rejects_duplicate_fields_and_oversized_input(tmp_path: Path) -> None:
    for raw in (b'{"schema":1,"schema":1}', b" " * (service.MAX_PAYLOAD_BYTES + 1)):
        with pytest.raises(ValueError):
            service.publish(raw, tmp_path)


def test_build_cli_and_default_client_endpoint(tmp_path: Path) -> None:
    metadata, updater = documents(tmp_path)
    for name, value in (("release.json", metadata), ("updater.json", updater)):
        (tmp_path / name).write_bytes(encoded(value))
    subprocess.run([
        sys.executable, "-m", "services.sakura.releases", "build",
        "--release", str(tmp_path / "release.json"), "--updater", str(tmp_path / "updater.json"),
        "--output", str(tmp_path / "publication.json"),
    ], check=True)
    assert json.loads((tmp_path / "publication.json").read_bytes())["updater"] == updater
    subprocess.run([
        sys.executable, "-m", "tools.release.tauri_release_config", "--target", "windows-x64",
        "--updater-client", "--public-key", "fixture-public-key", "--output", str(tmp_path / "config.json"),
    ], check=True, env={key: value for key, value in os.environ.items() if key != "SAKURA_UPDATER_ENDPOINT"})
    updater_config = json.loads((tmp_path / "config.json").read_bytes())["plugins"]["updater"]
    assert updater_config["endpoints"] == [service.SERVICE_ENDPOINT]
    assert updater_config["pubkey"] == "fixture-public-key"
