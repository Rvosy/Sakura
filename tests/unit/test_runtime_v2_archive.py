from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "runtime_v2_archive.py"
SPEC = importlib.util.spec_from_file_location("runtime_v2_archive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime_v2_archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_v2_archive)


def archive_manifest(payload: bytes) -> dict[str, object]:
    return {
        "fileName": "runtime.test",
        "url": "https://example.invalid/runtime.test",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_verify_archive_accepts_only_exact_size_and_sha256(tmp_path: Path) -> None:
    payload = b"pinned-runtime-archive"
    archive = tmp_path / "runtime.test"
    archive.write_bytes(payload)

    assert runtime_v2_archive.verify_archive(archive_manifest(payload), archive) == (
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )

    wrong_size = archive_manifest(payload)
    wrong_size["size"] = len(payload) + 1
    with pytest.raises(runtime_v2_archive.ArchiveVerificationError, match="size mismatch"):
        runtime_v2_archive.verify_archive(wrong_size, archive)

    wrong_hash = archive_manifest(payload)
    wrong_hash["sha256"] = "0" * 64
    with pytest.raises(runtime_v2_archive.ArchiveVerificationError, match="SHA-256 mismatch"):
        runtime_v2_archive.verify_archive(wrong_hash, archive)


def test_load_manifest_rejects_ambiguous_or_untrusted_sources(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    payload = b"runtime"
    document = {"archive": archive_manifest(payload)}
    manifest.write_text(json.dumps(document), encoding="utf-8")
    assert runtime_v2_archive.load_archive_manifest(manifest) == document["archive"]

    document["archive"]["url"] = "http://example.invalid/runtime.test"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        runtime_v2_archive.ArchiveVerificationError, match="identity is invalid"
    ):
        runtime_v2_archive.load_archive_manifest(manifest)

    document["archive"].pop("sha256")
    document["archive"]["archiveRoot"] = "."
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(runtime_v2_archive.ArchiveVerificationError, match="missing"):
        runtime_v2_archive.load_archive_manifest(manifest)
