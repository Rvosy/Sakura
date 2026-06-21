from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest

from tools import update


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


def test_fetch_manifest_validates_required_fields() -> None:
    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 30
        return FakeResponse(b'{"version":"1.0.0"}')

    with pytest.raises(update.UpdateError, match="缺少必要字段"):
        update.fetch_manifest(urlopen=fake_urlopen)


def test_update_check_reports_latest() -> None:
    tmp_path = _runtime_root("check_latest")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    manifest = _manifest("1.0.0", b"unused")
    out = io.StringIO()

    result = update.run_update(
        tmp_path,
        check_only=True,
        urlopen=_urlopen_for_manifest(manifest),
        out=out,
    )

    assert result == 0
    assert "当前已是最新版本" in out.getvalue()


def test_update_check_reports_newer_prerelease() -> None:
    tmp_path = _runtime_root("check_newer_prerelease")
    (tmp_path / "VERSION").write_text("1.0.0-dev\n", encoding="utf-8")
    manifest = _manifest("1.0.0", b"unused")
    out = io.StringIO()

    result = update.run_update(
        tmp_path,
        check_only=True,
        urlopen=_urlopen_for_manifest(manifest),
        out=out,
    )

    assert result == 0
    assert "发现新版本：1.0.0-dev -> 1.0.0" in out.getvalue()


def test_download_file_rejects_sha256_mismatch() -> None:
    tmp_path = _runtime_root("sha_mismatch")

    def fake_urlopen(_request, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 600
        return FakeResponse(b"bad")

    with pytest.raises(update.UpdateError, match="sha256"):
        update.download_file(
            "https://example.test/update.zip",
            tmp_path / "update.zip",
            expected_sha256="0" * 64,
            urlopen=fake_urlopen,
        )
    assert not (tmp_path / "update.zip.part").exists()


def test_format_user_error_keeps_raw_error_and_solution() -> None:
    message = update.format_user_error(update.UpdateError("下载更新清单失败：HTTP Error 404: Not Found"))

    assert "可能原因" in message
    assert "解决办法" in message
    assert "--------------" in message
    assert "原始报错" not in message
    assert "HTTP Error 404: Not Found" in message
    assert "sakura-update.json" in message


def test_format_user_error_explains_dependency_failure() -> None:
    error = update.subprocess.CalledProcessError(1, ["python", "-m", "pip", "install"])
    message = update.format_user_error(error)

    assert "依赖安装失败" in message
    assert "install.bat" in message
    assert "returned non-zero exit status 1" in message


def test_apply_update_archive_skips_user_paths() -> None:
    tmp_path = _runtime_root("skip_user_paths")
    archive = tmp_path / "update.zip"
    _write_zip(
        archive,
        {
            "VERSION": "1.1.0\n",
            "app/new.py": "new",
            "assets/setup_01.webp": "replace",
            "data/config/api.yaml": "replace",
            "docs/SETUP.md": "replace",
            "runtime/python.exe": "replace",
            "characters/user.txt": "replace",
            "tts/model.bin": "replace",
            "plugins/playwright_browser/config.json": "replace",
        },
    )
    (tmp_path / "data/config").mkdir(parents=True)
    (tmp_path / "data/config/api.yaml").write_text("keep", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/setup_01.webp").write_text("keep", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/SETUP.md").write_text("keep", encoding="utf-8")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/python.exe").write_text("keep", encoding="utf-8")
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters/user.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "tts").mkdir()
    (tmp_path / "tts/model.bin").write_text("keep", encoding="utf-8")
    (tmp_path / "plugins/playwright_browser").mkdir(parents=True)
    (tmp_path / "plugins/playwright_browser/config.json").write_text("keep", encoding="utf-8")

    written = update.apply_update_archive(archive, tmp_path)

    assert tmp_path / "app/new.py" in written
    assert (tmp_path / "app/new.py").read_text(encoding="utf-8") == "new"
    assert (tmp_path / "assets/setup_01.webp").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "data/config/api.yaml").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "docs/SETUP.md").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "runtime/python.exe").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "characters/user.txt").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "tts/model.bin").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "plugins/playwright_browser/config.json").read_text(encoding="utf-8") == "keep"


def test_run_update_installs_dependencies_only_when_requirements_changed() -> None:
    tmp_path = _runtime_root("requirements_changed")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("old\n", encoding="utf-8")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/python.exe").write_text("fake", encoding="utf-8")
    archive = _zip_bytes({"VERSION": "1.1.0\n", "requirements.txt": "new\n"})
    manifest = _manifest("1.1.0", archive)
    installs: list[Path] = []

    result = update.run_update(
        tmp_path,
        urlopen=_urlopen_for_manifest_and_archive(manifest, archive),
        out=io.StringIO(),
        running_check=lambda _base: False,
        dependency_installer=lambda base, _python: installs.append(base),
    )

    assert result == 0
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == "new\n"
    assert installs == [tmp_path.resolve()]


def _manifest(version: str, archive: bytes) -> bytes:
    return json.dumps(
        {
            "version": version,
            "tag": f"v{version}",
            "prerelease": "-" in version,
            "windows_x64_update_url": "https://example.test/update.zip",
            "sha256": hashlib.sha256(archive).hexdigest(),
        }
    ).encode("utf-8")


def _urlopen_for_manifest(manifest: bytes):  # type: ignore[no-untyped-def]
    def fake_urlopen(request, _timeout=None, timeout=None):  # type: ignore[no-untyped-def]
        assert "sakura-update.json" in getattr(request, "full_url", str(request))
        return FakeResponse(manifest)

    return fake_urlopen


def _urlopen_for_manifest_and_archive(manifest: bytes, archive: bytes):  # type: ignore[no-untyped-def]
    def fake_urlopen(request, _timeout=None, timeout=None):  # type: ignore[no-untyped-def]
        url = getattr(request, "full_url", str(request))
        if url.endswith("update.zip"):
            return FakeResponse(archive)
        return FakeResponse(manifest)

    return fake_urlopen


def _write_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / "update" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root
