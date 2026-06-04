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
    install_tts_bundle,
)


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

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

    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
    assert not archive.exists()
    assert not archive.with_name(f"{archive.name}.part").exists()
    assert work_dir == (root / "data" / "tts_bundles" / "installed" / entry.key).resolve()
    assert (work_dir / "api_v2.py").exists()
    assert statuses == ["verify", "download", "extract", "cleanup"]
    assert progress[-1] == 100


def test_tts_bundle_verifies_cached_archive_with_progress() -> None:
    root = _runtime_root("bundle_cached_verify")
    payload = b"sakura-cached-tts-bundle" * 64
    entry = _entry(payload)
    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
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

    assert work_dir == (root / "data" / "tts_bundles" / "installed" / entry.key).resolve()
    assert not archive.exists()
    assert statuses == ["verify", "extract", "cleanup"]
    assert 10 in progress
    assert progress[-1] == 100


def test_tts_bundle_download_removes_part_on_verification_failure() -> None:
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

    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
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
    archive = root / "data" / "tts_bundles" / "downloads" / entry.filename
    assert archive.read_bytes() == payload


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
    runtime_python.write_text("fake", encoding="utf-8")

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
    runtime_python.write_text("fake", encoding="utf-8")

    assert default_provider_bundle_work_dir("gpt-sovits", root) == work_dir.resolve()


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
