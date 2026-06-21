from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.voice import tts_bundle
from app.voice.tts_bundle import (
    GPUInfo,
    TTSBundleEntry,
    cleanup_stale_download_archives,
    default_provider_bundle_work_dir,
    download_and_extract_bundle,
    find_pending_bundle_migrations,
    install_tts_bundle,
    migrate_bundle_to_short_path,
    normalize_bundle_work_dir,
)


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self._data = data
        self._offset = 0
        self._status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def getcode(self) -> int:
        return self._status


def test_tts_bundle_downloads_to_part_then_verifies_and_extracts() -> None:
    root = _runtime_root("bundle_success")
    payload = b"sakura-tts-bundle"
    entry = _entry(payload)
    progress: list[int] = []
    statuses: list[str] = []

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    def fake_extract(_archive: Path, out_dir: Path) -> str | None:
        (out_dir / "api_v2.py").write_text("fake", encoding="utf-8")
        return None

    work_dir = download_and_extract_bundle(
        entry,
        root,
        on_progress=progress.append,
        on_status=statuses.append,
        urlopen=fake_urlopen,
        extractor=fake_extract,
    )

    archive = root / "tts" / "_dl" / entry.filename
    assert not archive.exists()
    assert not archive.with_name(f"{archive.name}.part").exists()
    assert work_dir == (root / "tts" / entry.key).resolve()
    assert (work_dir / "api_v2.py").exists()
    assert statuses == ["verify", "download", "extract", "cleanup"]
    assert progress[-1] == 100


def test_tts_bundle_verifies_cached_archive_with_progress() -> None:
    root = _runtime_root("bundle_cached_verify")
    payload = b"sakura-cached-tts-bundle" * 64
    entry = _entry(payload)
    archive = root / "tts" / "_dl" / entry.filename
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(payload)
    progress: list[int] = []
    statuses: list[str] = []

    def fail_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        raise AssertionError("本地压缩包校验通过时不应重新下载")

    def fake_extract(_archive: Path, out_dir: Path) -> str | None:
        (out_dir / "api_v2.py").write_text("fake", encoding="utf-8")
        return None

    work_dir = download_and_extract_bundle(
        entry,
        root,
        on_progress=progress.append,
        on_status=statuses.append,
        urlopen=fail_urlopen,
        extractor=fake_extract,
    )

    assert work_dir == (root / "tts" / entry.key).resolve()
    assert not archive.exists()
    assert statuses == ["verify", "extract", "cleanup"]
    assert 10 in progress
    assert progress[-1] == 100


def test_tts_bundle_resumes_part_download_with_range_request() -> None:
    root = _runtime_root("bundle_resume")
    payload = b"sakura-resumable-tts-bundle" * 32
    prefix = payload[:128]
    suffix = payload[128:]
    entry = _entry(payload)
    archive = root / "tts" / "_dl" / entry.filename
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.with_name(f"{archive.name}.part").write_bytes(prefix)
    progress: list[tts_bundle.TTSBundleDownloadProgress] = []
    seen_range: list[str | None] = []

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        seen_range.append(request.get_header("Range"))
        return FakeResponse(suffix, status=206)

    def fake_extract(_archive: Path, out_dir: Path) -> str | None:
        (out_dir / "api_v2.py").write_text("fake", encoding="utf-8")
        return None

    work_dir = download_and_extract_bundle(
        entry,
        root,
        on_download_progress=progress.append,
        urlopen=fake_urlopen,
        extractor=fake_extract,
    )

    assert work_dir == (root / "tts" / entry.key).resolve()
    assert seen_range == [f"bytes={len(prefix)}-"]
    assert any(item.resumed for item in progress)
    assert not archive.exists()
    assert not archive.with_name(f"{archive.name}.part").exists()


def test_tts_bundle_falls_back_to_full_download_when_range_is_ignored() -> None:
    root = _runtime_root("bundle_resume_fallback")
    payload = b"sakura-full-redownload" * 32
    stale_part = b"stale-partial"
    entry = _entry(payload)
    archive = root / "tts" / "_dl" / entry.filename
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.with_name(f"{archive.name}.part").write_bytes(stale_part)
    progress: list[tts_bundle.TTSBundleDownloadProgress] = []
    seen_range: list[str | None] = []

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        seen_range.append(request.get_header("Range"))
        return FakeResponse(payload, status=200)

    def fake_extract(_archive: Path, out_dir: Path) -> str | None:
        (out_dir / "api_v2.py").write_text("fake", encoding="utf-8")
        return None

    work_dir = download_and_extract_bundle(
        entry,
        root,
        on_download_progress=progress.append,
        urlopen=fake_urlopen,
        extractor=fake_extract,
    )

    assert work_dir == (root / "tts" / entry.key).resolve()
    assert seen_range == [f"bytes={len(stale_part)}-"]
    assert any(not item.resumed and item.downloaded_bytes == 0 for item in progress)
    assert not archive.with_name(f"{archive.name}.part").exists()


def test_tts_bundle_cancel_preserves_part_for_resume() -> None:
    root = _runtime_root("bundle_cancel_preserve_part")
    payload = b"sakura-cancel-resume" * 64
    entry = _entry(payload)
    checks = 0

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    def check_cancel() -> None:
        nonlocal checks
        checks += 1
        raise tts_bundle.DownloadCancelledError("pause")

    with pytest.raises(tts_bundle.DownloadCancelledError):
        download_and_extract_bundle(
            entry,
            root,
            check_cancel=check_cancel,
            urlopen=fake_urlopen,
            extractor=lambda *_args: None,
        )

    archive = root / "tts" / "_dl" / entry.filename
    assert checks == 1
    assert archive.with_name(f"{archive.name}.part").read_bytes() == payload


def test_tts_bundle_download_preserves_part_on_short_download() -> None:
    root = _runtime_root("bundle_verify_failure")
    payload = b"too-short"
    entry = TTSBundleEntry(
        key="demo",
        label="Demo",
        filename="demo.7z",
        download_url="https://example.test/demo.7z",
        size=len(payload) + 1,
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    with pytest.raises(RuntimeError, match="文件大小不匹配"):
        download_and_extract_bundle(entry, root, urlopen=fake_urlopen, extractor=lambda *_args: None)

    archive = root / "tts" / "_dl" / entry.filename
    assert not archive.exists()
    assert archive.with_name(f"{archive.name}.part").read_bytes() == payload


def test_tts_bundle_download_removes_part_on_sha256_failure() -> None:
    root = _runtime_root("bundle_sha_failure")
    payload = b"wrong-hash"
    entry = TTSBundleEntry(
        key="demo",
        label="Demo",
        filename="demo.7z",
        download_url="https://example.test/demo.7z",
        size=len(payload),
        sha256=hashlib.sha256(b"expected").hexdigest(),
    )

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    with pytest.raises(RuntimeError, match="SHA256 不匹配"):
        download_and_extract_bundle(entry, root, urlopen=fake_urlopen, extractor=lambda *_args: None)

    archive = root / "tts" / "_dl" / entry.filename
    assert not archive.exists()
    assert not archive.with_name(f"{archive.name}.part").exists()


def test_tts_bundle_reports_extract_failure() -> None:
    root = _runtime_root("bundle_extract_failure")
    payload = b"valid-archive"
    entry = _entry(payload)

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    with pytest.raises(RuntimeError, match="解压 TTS 整合包失败"):
        download_and_extract_bundle(
            entry,
            root,
            urlopen=fake_urlopen,
            extractor=lambda *_args: "boom",
        )
    archive = root / "tts" / "_dl" / entry.filename
    assert archive.read_bytes() == payload


def test_tts_bundle_flattens_single_extracted_root() -> None:
    root = _runtime_root("bundle_flatten_root")
    payload = b"valid-archive"
    entry = _entry(payload)

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(payload)

    def fake_extract(_archive: Path, out_dir: Path) -> str | None:
        runtime_python = out_dir / "GPT-SoVITS" / "runtime" / "python.exe"
        runtime_python.parent.mkdir(parents=True)
        _write_fake_runtime_python(runtime_python)
        return None

    work_dir = download_and_extract_bundle(entry, root, urlopen=fake_urlopen, extractor=fake_extract)

    assert work_dir == (root / "tts" / entry.key).resolve()
    assert (work_dir / "runtime" / "python.exe").is_file()
    assert not (work_dir / "GPT-SoVITS").exists()


def test_tts_bundle_cleans_legacy_archive_when_bundle_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    root = _runtime_root("cleanup_legacy_archive")
    entry = tts_bundle.GPT_SOVITS_STANDARD
    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"legacy-archive")
    runtime_python = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS"
        / "runtime"
        / "python.exe"
    )
    runtime_python.parent.mkdir(parents=True, exist_ok=True)
    _write_fake_runtime_python(runtime_python)

    cleaned = cleanup_stale_download_archives(root)

    assert cleaned == [archive]
    assert not archive.exists()


def test_tts_bundle_legacy_cleanup_preserves_uninstalled_and_unknown_archives() -> None:
    root = _runtime_root("cleanup_preserve_archives")
    entry = tts_bundle.GENIE_TTS
    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
    unknown_archive = archive.parent / "unknown.7z"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"not-installed")
    unknown_archive.write_bytes(b"unknown")
    (root / "data" / "tts_bundles" / "installed" / entry.key).mkdir(parents=True, exist_ok=True)

    cleaned = cleanup_stale_download_archives(root)

    assert cleaned == []
    assert archive.exists()
    assert unknown_archive.exists()


def test_tts_bundle_default_provider_work_dir_uses_installed_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    root = _runtime_root("default_provider_work_dir")
    work_dir = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / tts_bundle.GPT_SOVITS_NVIDIA50.key
        / "GPT-SoVITS-v2pro-20250604-nvidia50"
    )
    runtime_python = work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)

    assert default_provider_bundle_work_dir("gpt-sovits", root) == work_dir.resolve()


def test_tts_bundle_default_provider_prefers_short_installed_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    root = _runtime_root("default_provider_short_work_dir")
    work_dir = root / "tts" / "g50"
    runtime_python = work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)

    assert default_provider_bundle_work_dir("gpt-sovits", root) == work_dir.resolve()


def test_tts_bundle_detects_and_migrates_legacy_install() -> None:
    root = _runtime_root("migrate_legacy_install")
    entry = tts_bundle.GPT_SOVITS_NVIDIA50
    legacy_work_dir = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS-v2pro-20250604-nvidia50"
    )
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)

    migrations = find_pending_bundle_migrations(root, "gpt-sovits")

    assert len(migrations) == 1
    assert migrations[0].source_dir == legacy_work_dir.resolve()
    assert migrations[0].target_dir == root / "tts" / "g50"

    migrated = migrate_bundle_to_short_path(migrations[0])

    assert migrated == (root / "tts" / "g50").resolve()
    assert (migrated / "runtime" / "python.exe").is_file()
    assert not legacy_work_dir.exists()


def test_tts_bundle_migration_copies_with_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("migrate_copy_progress")
    entry = tts_bundle.GPT_SOVITS_NVIDIA50
    legacy_work_dir = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS-v2pro-20250604-nvidia50"
    )
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    model_file = legacy_work_dir / "models" / "demo.bin"
    runtime_python.parent.mkdir(parents=True)
    model_file.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)
    model_file.write_bytes(b"model")
    migration = find_pending_bundle_migrations(root, "gpt-sovits")[0]
    progress: list[tts_bundle.TTSBundleMigrationProgress] = []
    monkeypatch.setattr(tts_bundle, "_try_fast_migration_rename", lambda _migration: False)

    migrated = migrate_bundle_to_short_path(migration, on_progress=progress.append)

    assert migrated == (root / "tts" / "g50").resolve()
    assert (migrated / "runtime" / "python.exe").read_text(encoding="utf-8") == "fake"
    assert (migrated / "models" / "demo.bin").read_bytes() == b"model"
    assert progress
    assert progress[-1].completed_files == progress[-1].total_files == 2
    assert progress[-1].copied_bytes == progress[-1].total_bytes
    assert not (migrated / ".sakura_migration.json").exists()
    assert not legacy_work_dir.exists()


def test_tts_bundle_migration_resumes_existing_staging_file(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("migrate_resume_staging")
    entry = tts_bundle.GENIE_TTS
    legacy_work_dir = root / "data" / "tts_bundles" / "installed" / entry.key / "Genie-TTS Server"
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    voice_file = legacy_work_dir / "voices" / "demo.dat"
    runtime_python.parent.mkdir(parents=True)
    voice_file.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)
    voice_file.write_text("voice", encoding="utf-8")
    migration = find_pending_bundle_migrations(root, "genie-tts")[0]
    staging_runtime = root / "tts" / ".migrating" / entry.key / "runtime" / "python.exe"
    staging_runtime.parent.mkdir(parents=True)
    _write_fake_runtime_python(staging_runtime)
    original_copy = tts_bundle._copy_file_resumable
    copied: list[str] = []

    def record_copy(source: Path, target: Path) -> None:
        copied.append(source.relative_to(legacy_work_dir).as_posix())
        original_copy(source, target)

    monkeypatch.setattr(tts_bundle, "_try_fast_migration_rename", lambda _migration: False)
    monkeypatch.setattr(tts_bundle, "_copy_file_resumable", record_copy)

    migrated = migrate_bundle_to_short_path(migration)

    assert migrated == (root / "tts" / "cpu").resolve()
    assert copied == ["voices/demo.dat"]
    assert (migrated / "runtime" / "python.exe").read_text(encoding="utf-8") == "fake"
    assert (migrated / "voices" / "demo.dat").read_text(encoding="utf-8") == "voice"
    assert not (root / "tts" / ".migrating" / entry.key).exists()


def test_tts_bundle_migration_cleans_empty_legacy_dirs_but_keeps_onnx() -> None:
    root = _runtime_root("migrate_cleanup_legacy_keep_onnx")
    entry = tts_bundle.GENIE_TTS
    legacy_work_dir = root / "data" / "tts_bundles" / "installed" / entry.key / "Genie-TTS Server"
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)
    downloads_dir = root / "data" / "tts_bundles" / "downloads"
    downloads_dir.mkdir(parents=True)
    onnx_file = root / "data" / "tts_bundles" / "onnx" / "sakura" / "model.onnx"
    onnx_file.parent.mkdir(parents=True)
    onnx_file.write_bytes(b"onnx")
    migration = find_pending_bundle_migrations(root, "genie-tts")[0]

    migrated = migrate_bundle_to_short_path(migration)

    assert migrated == (root / "tts" / "cpu").resolve()
    assert not (root / "data" / "tts_bundles" / "installed").exists()
    assert not downloads_dir.exists()
    assert onnx_file.read_bytes() == b"onnx"
    assert (root / "data" / "tts_bundles").exists()


def test_tts_bundle_migration_skips_existing_short_dir() -> None:
    root = _runtime_root("migrate_skip_short")
    entry = tts_bundle.GPT_SOVITS_STANDARD
    short_runtime = root / "tts" / "gpt" / "runtime" / "python.exe"
    legacy_runtime = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS-v2pro-20250604"
        / "runtime"
        / "python.exe"
    )
    short_runtime.parent.mkdir(parents=True)
    _write_fake_runtime_python(short_runtime, "short")
    legacy_runtime.parent.mkdir(parents=True)
    _write_fake_runtime_python(legacy_runtime, "legacy")

    migrations = find_pending_bundle_migrations(root, "gpt-sovits")

    assert migrations == []
    assert short_runtime.read_text(encoding="utf-8") == "short"
    assert legacy_runtime.read_text(encoding="utf-8") == "legacy"


def test_tts_bundle_migration_replaces_invalid_short_dir() -> None:
    root = _runtime_root("migrate_invalid_short_target")
    entry = tts_bundle.GPT_SOVITS_NVIDIA50
    invalid_marker = root / "tts" / "g50" / "broken.txt"
    invalid_marker.parent.mkdir(parents=True)
    invalid_marker.write_text("broken", encoding="utf-8")
    legacy_work_dir = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS-v2pro-20250604-nvidia50"
    )
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python, "legacy")

    migrations = find_pending_bundle_migrations(root, "gpt-sovits")

    assert len(migrations) == 1
    assert migrations[0].target_dir == root / "tts" / "g50"

    migrated = migrate_bundle_to_short_path(migrations[0])

    assert migrated == (root / "tts" / "g50").resolve()
    assert (migrated / "runtime" / "python.exe").read_text(encoding="utf-8") == "legacy"
    assert not (migrated / "broken.txt").exists()
    assert not legacy_work_dir.exists()


def test_tts_bundle_migration_failure_preserves_legacy_install(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("migrate_failure_preserves_legacy")
    entry = tts_bundle.GENIE_TTS
    legacy_work_dir = root / "data" / "tts_bundles" / "installed" / entry.key / "Genie-TTS Server"
    runtime_python = legacy_work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)
    migration = find_pending_bundle_migrations(root, "genie-tts")[0]

    def fail_copy(_source: Path, _target: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr(tts_bundle, "_try_fast_migration_rename", lambda _migration: False)
    monkeypatch.setattr(tts_bundle, "_copy_file_resumable", fail_copy)

    with pytest.raises(OSError, match="locked"):
        migrate_bundle_to_short_path(migration)

    assert runtime_python.is_file()
    assert not (root / "tts" / "cpu").exists()
    assert (root / "tts" / ".migrating" / entry.key / ".sakura_migration.json").is_file()


def test_tts_bundle_normalizes_legacy_config_path_after_migration() -> None:
    root = _runtime_root("normalize_legacy_after_migration")
    entry = tts_bundle.GPT_SOVITS_STANDARD
    legacy_work_dir = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS-v2pro-20250604"
    )
    short_work_dir = root / "tts" / "gpt"
    runtime_python = short_work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)

    assert normalize_bundle_work_dir(legacy_work_dir, root) == short_work_dir.resolve()


def test_tts_bundle_recommends_genie_for_cpu_or_small_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    assert tts_bundle.recommend_tts_bundle([]).key == "genie_tts_server"
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce GTX 1050 Ti", 4.0)]).key == "genie_tts_server"


def test_tts_bundle_recommends_gptsovits_for_capable_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce GTX 1060", 6.0)]).key == "gpt_sovits_v2pro"
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce GTX 1060", 5.96)]).key == "gpt_sovits_v2pro"
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce RTX 4070", 12.0)]).key == "gpt_sovits_v2pro"
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce RTX 5080", 16.0)]).key == "gpt_sovits_nvidia50"
    assert tts_bundle.recommend_tts_bundle([GPUInfo("NVIDIA GeForce RTX 5060", 7.96)]).key == "gpt_sovits_nvidia50"


def test_list_nvidia_gpus_swallows_which_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """复刻 CI 场景：伪造 win32 但宿主非 Windows，shutil.which 抛异常时应按未检测到 GPU 处理，而非崩溃。"""

    # 伪造为 Windows，使 GPT-SoVITS 整合包在任意宿主上都判定为兼容，从而让推荐逻辑真正走到 GPU 探测。
    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")

    def _boom(_name: str):  # type: ignore[no-untyped-def]
        # 模拟非 Windows 宿主上 shutil.which 因 _winapi 缺失抛出的 AttributeError
        raise AttributeError("'NoneType' object has no attribute 'NeedCurrentDirectoryForExePath'")

    monkeypatch.setattr(tts_bundle.shutil, "which", _boom)

    assert tts_bundle.list_nvidia_gpus() == []
    # 上层推荐逻辑不应被探测异常打断
    assert tts_bundle.recommend_gpt_sovits_bundle() is not None


def test_tts_bundle_label_includes_approx_size() -> None:
    assert tts_bundle.format_bundle_label(tts_bundle.GPT_SOVITS_NVIDIA50).endswith("（约 8.8 GB，仅 Windows）")


def test_tts_bundle_filters_incompatible_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "darwin")

    assert tts_bundle.compatible_tts_bundles() == (tts_bundle.GPT_SOVITS_MACOS_INSTALLER,)
    assert tts_bundle.recommend_tts_bundle([]) == tts_bundle.GPT_SOVITS_MACOS_INSTALLER
    assert "GPT-SoVITS macOS" in tts_bundle.format_gpu_summary([])

    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")

    assert tts_bundle.GPT_SOVITS_MACOS_INSTALLER not in tts_bundle.compatible_tts_bundles()
    assert tts_bundle.GENIE_TTS in tts_bundle.compatible_tts_bundles()
    assert tts_bundle.GPT_SOVITS_STANDARD in tts_bundle.compatible_tts_bundles()


def test_tts_bundle_rejects_incompatible_platform_before_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bundle.sys, "platform", "darwin")
    root = _runtime_root("bundle_incompatible_platform")

    def fail_urlopen(_request, _timeout: int):  # type: ignore[no-untyped-def]
        raise AssertionError("平台不兼容时不应开始下载")

    with pytest.raises(RuntimeError, match="不支持当前平台"):
        download_and_extract_bundle(
            tts_bundle.GPT_SOVITS_STANDARD,
            root,
            urlopen=fail_urlopen,
            extractor=lambda *_args: None,
        )


@pytest.mark.skipif(sys.platform == "win32", reason="macOS source installer tests use bash paths")
def test_tts_bundle_runs_script_installer_and_returns_runtime_paths() -> None:
    root = _runtime_root("bundle_script_installer")
    script = root / "fake_installer.sh"
    script.write_text(
        """#!/bin/bash
set -e
install_root="$1"
echo "::sakura-progress status=install progress=50"
mkdir -p "$install_root/GPT-SoVITS/GPT_SoVITS/configs"
mkdir -p "$install_root/miniforge3/envs/gpt-sovits310/bin"
echo "fake api" > "$install_root/GPT-SoVITS/api_v2.py"
echo "custom: {}" > "$install_root/GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml"
echo '#!/bin/sh' > "$install_root/miniforge3/envs/gpt-sovits310/bin/python"
chmod +x "$install_root/miniforge3/envs/gpt-sovits310/bin/python"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    entry = TTSBundleEntry(
        key="script_demo",
        label="Script Demo",
        provider="custom-gpt-sovits",
        install_method="script",
        installer_script="fake_installer.sh",
        work_dir_name="GPT-SoVITS",
        python_path_name="miniforge3/envs/gpt-sovits310/bin/python",
        tts_config_path_name="GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml",
    )
    progress: list[int] = []
    statuses: list[str] = []

    result = install_tts_bundle(entry, root, on_progress=progress.append, on_status=statuses.append)

    assert result.provider == "custom-gpt-sovits"
    assert result.work_dir == (
        root / "data" / "tts_bundles" / "installed" / entry.key / "GPT-SoVITS"
    ).resolve()
    assert result.python_path == (
        root / "data" / "tts_bundles" / "installed" / entry.key / "miniforge3/envs/gpt-sovits310/bin/python"
    ).resolve()
    assert result.tts_config_path == (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / entry.key
        / "GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml"
    ).resolve()
    assert "install" in statuses
    assert 50 in progress
    assert progress[-1] == 100
    assert not (root / "data" / "tts_bundles" / "tmp" / entry.key).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="macOS source installer tests use bash paths")
def test_tts_bundle_script_installer_cleans_tmp_dir_on_failure() -> None:
    root = _runtime_root("bundle_script_installer_failure")
    script = root / "fake_installer_failure.sh"
    script.write_text(
        """#!/bin/bash
set -e
install_root="$1"
mkdir -p "$install_root/GPT-SoVITS"
echo "partial" > "$install_root/GPT-SoVITS/api_v2.py"
echo "boom"
exit 1
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    entry = TTSBundleEntry(
        key="script_failure",
        label="Script Failure",
        provider="custom-gpt-sovits",
        install_method="script",
        installer_script="fake_installer_failure.sh",
        work_dir_name="GPT-SoVITS",
        python_path_name="miniforge3/envs/gpt-sovits310/bin/python",
        tts_config_path_name="GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml",
    )

    with pytest.raises(RuntimeError, match="安装失败"):
        install_tts_bundle(entry, root)

    assert not (root / "data" / "tts_bundles" / "tmp" / entry.key).exists()
    assert not (root / "data" / "tts_bundles" / "installed" / entry.key).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="macOS source installer tests use bash paths")
def test_tts_bundle_script_installer_preserves_existing_install_on_failure() -> None:
    root = _runtime_root("bundle_script_installer_preserve_existing")
    entry = TTSBundleEntry(
        key="script_existing",
        label="Script Existing",
        provider="custom-gpt-sovits",
        install_method="script",
        installer_script="fake_installer_failure.sh",
        work_dir_name="GPT-SoVITS",
        python_path_name="miniforge3/envs/gpt-sovits310/bin/python",
        tts_config_path_name="GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml",
    )
    installed_dir = root / "data" / "tts_bundles" / "installed" / entry.key
    (installed_dir / "GPT-SoVITS/GPT_SoVITS/configs").mkdir(parents=True, exist_ok=True)
    (installed_dir / "miniforge3/envs/gpt-sovits310/bin").mkdir(parents=True, exist_ok=True)
    (installed_dir / "GPT-SoVITS/api_v2.py").write_text("existing", encoding="utf-8")
    (installed_dir / "GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml").write_text(
        "custom: {}\n",
        encoding="utf-8",
    )
    (installed_dir / "miniforge3/envs/gpt-sovits310/bin/python").write_text("#!/bin/sh\n", encoding="utf-8")

    script = root / "fake_installer_failure.sh"
    script.write_text(
        """#!/bin/bash
set -e
install_root="$1"
mkdir -p "$install_root/GPT-SoVITS"
echo "partial" > "$install_root/GPT-SoVITS/api_v2.py"
exit 1
""",
        encoding="utf-8",
    )
    script.chmod(0o755)

    with pytest.raises(RuntimeError, match="安装失败"):
        install_tts_bundle(entry, root)

    assert not (root / "data" / "tts_bundles" / "tmp" / entry.key).exists()
    assert (installed_dir / "GPT-SoVITS/api_v2.py").read_text(encoding="utf-8") == "existing"


def test_extract_archive_prefers_py7zz(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("extract_prefers_py7zz")
    calls: list[str] = []

    def fake_py7zz(_archive: Path, _out_dir: Path) -> str | None:
        calls.append("py7zz")
        return None

    monkeypatch.setattr(tts_bundle, "_extract_with_py7zz", fake_py7zz)
    monkeypatch.setattr(tts_bundle, "_seven_zip_exe", lambda: pytest.fail("不应查找 7-Zip"))
    monkeypatch.setattr(tts_bundle, "_load_py7zr", lambda: pytest.fail("不应加载 py7zr"))

    assert tts_bundle._extract_archive(root / "bundle.7z", root / "out") is None
    assert calls == ["py7zz"]


def test_extract_archive_uses_project_7zip_when_py7zz_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("extract_project_7zip")
    exe = root / "build_exe" / "7zz.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("fake", encoding="utf-8")
    used: list[Path] = []

    def fake_7zip(path: Path, _archive: Path, _out_dir: Path) -> str | None:
        used.append(path)
        return None

    monkeypatch.setattr(tts_bundle, "_project_root", lambda: root)
    monkeypatch.setattr(tts_bundle.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tts_bundle, "_extract_with_py7zz", lambda *_args: "missing")
    monkeypatch.setattr(tts_bundle, "_extract_with_7zip", fake_7zip)
    monkeypatch.setattr(tts_bundle, "_load_py7zr", lambda: pytest.fail("7-Zip 成功时不应加载 py7zr"))

    assert tts_bundle._extract_archive(root / "bundle.7z", root / "out") is None
    assert used == [exe]


def test_extract_archive_falls_back_to_py7zr(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("extract_py7zr")
    calls: list[str] = []
    fake_py7zr = SimpleNamespace()

    def fake_extract(_py7zr, _archive: Path, _out_dir: Path) -> None:  # type: ignore[no-untyped-def]
        calls.append("py7zr")

    monkeypatch.setattr(tts_bundle, "_extract_with_py7zz", lambda *_args: "missing")
    monkeypatch.setattr(tts_bundle, "_seven_zip_exe", lambda: None)
    monkeypatch.setattr(tts_bundle, "_load_py7zr", lambda: fake_py7zr)
    monkeypatch.setattr(tts_bundle, "_extract_with_py7zr", fake_extract)

    assert tts_bundle._extract_archive(root / "bundle.7z", root / "out") is None
    assert calls == ["py7zr"]


def test_extract_archive_reports_when_all_extractors_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("extract_all_missing")
    monkeypatch.setattr(tts_bundle, "_extract_with_py7zz", lambda *_args: "missing")
    monkeypatch.setattr(tts_bundle, "_seven_zip_exe", lambda: None)
    monkeypatch.setattr(tts_bundle, "_load_py7zr", lambda: None)

    error = tts_bundle._extract_archive(root / "bundle.7z", root / "out")

    assert error is not None
    assert "py7zz" in error
    assert "7-Zip CLI" in error
    assert "py7zr" in error


def test_extract_archive_py7zr_failure_mentions_7zip_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root("extract_py7zr_failure")

    def fail_py7zr(_py7zr, _archive: Path, _out_dir: Path) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("BCJ2 unsupported")

    monkeypatch.setattr(tts_bundle, "_extract_with_py7zz", lambda *_args: "missing")
    monkeypatch.setattr(tts_bundle, "_seven_zip_exe", lambda: None)
    monkeypatch.setattr(tts_bundle, "_load_py7zr", lambda: SimpleNamespace())
    monkeypatch.setattr(tts_bundle, "_extract_with_py7zr", fail_py7zr)

    error = tts_bundle._extract_archive(root / "bundle.7z", root / "out")

    assert error is not None
    assert "需要 py7zz 或 7-Zip CLI" in error
    assert "BCJ2 unsupported" in error


def _entry(payload: bytes) -> TTSBundleEntry:
    return TTSBundleEntry(
        key="demo",
        label="Demo",
        filename="demo.7z",
        download_url="https://example.test/demo.7z",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_fake_runtime_python(path: Path, content: str = "fake") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
