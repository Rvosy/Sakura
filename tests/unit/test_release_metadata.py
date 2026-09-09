from __future__ import annotations

import io
import json
import subprocess
import tarfile

import pytest

from scripts import runtime_v2_archive
from tools.release import diagnostic_build
from tools.release.stage_distribution import inventory


def test_release_mapping_keeps_paths_and_sizes_with_unique_build_ids(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "app/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass\n", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "VERSION").write_bytes(b"1.1.0\n")
    (stage / "resource.bin").write_bytes(b"resource")
    monkeypatch.setattr(diagnostic_build.subprocess, "check_output", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(
        diagnostic_build.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1)
    )

    resources = inventory(stage, "windows-x64")
    assert resources["schemaVersion"] == 2
    assert resources["uncompressedBytes"] == 14
    assert all(set(entry) == {"path", "size"} for entry in resources["files"])
    first = diagnostic_build.write_mapping(repo, stage, "windows-x64", resources)
    second = diagnostic_build.write_mapping(repo, stage, "windows-x64", resources)
    assert first["buildId"] != second["buildId"]
    assert second["schemaVersion"] == 3
    assert second["environment"] == "development"
    assert second["sources"] == [{"file": "app/main.py"}]
    assert second["resources"] == resources["files"]
    assert json.loads((stage / "diagnostic-build.json").read_text()) == second
    assert (stage / "diagnostic-build-id.txt").read_text().strip() == second["buildId"]


def test_runtime_tar_download_parses_archive_without_digest(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        entry = tarfile.TarInfo("python/bin/python3")
        entry.size = 6
        bundle.addfile(entry, io.BytesIO(b"python"))
    content = buffer.getvalue()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"archive": {
        "fileName": "python.tar.gz", "url": "https://example.test/python.tar.gz",
        "size": len(content),
    }}))
    monkeypatch.setattr(runtime_v2_archive.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(content))
    output = tmp_path / "python.tar.gz"
    assert runtime_v2_archive.download_and_verify(manifest, output) == len(content)
    assert output.read_bytes() == content


def test_runtime_download_rejects_unparseable_archive_without_partial_file(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"archive": {
        "fileName": "python.zip", "url": "https://example.test/python.zip", "size": 6,
    }}))
    monkeypatch.setattr(runtime_v2_archive.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"broken"))
    output = tmp_path / "python.zip"
    with pytest.raises(runtime_v2_archive.ArchiveVerificationError, match="cannot parse"):
        runtime_v2_archive.download_and_verify(manifest, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".python.zip.partial-*"))
