from __future__ import annotations

import json
import os
import tarfile
import zipfile
import io
from pathlib import Path
from typing import Any

import pytest

from app.core.resource_manager import ResourceRegistry
from app.sensory import llama_cpp_runtime
from app.sensory.llama_cpp_runtime import (
    LLAMA_CPP_RUNTIME_MANIFEST_ENV,
    LLAMA_CPP_SERVER_ENV,
    LlamaCppLaunchConfig,
    LlamaCppRuntimePackageSpec,
    LlamaCppRuntimeError,
    LlamaCppRuntimeManager,
    build_llama_server_command,
    check_llama_cpp_health,
    discover_llama_server_binary,
    fetch_llama_cpp_runtime_package_catalog,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    llama_cpp_runtime_manifest_paths,
    llama_cpp_runtime_packages_from_github_release,
    llama_cpp_runtime_packages_from_manifest,
    select_llama_cpp_runtime_package,
)


def test_build_llama_server_command_supports_hf_repo_and_runtime_tuning(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")

    command = build_llama_server_command(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF",
            host="127.0.0.1",
            port=18081,
            alias="sakura-audio",
            ctx_size=8192,
            n_gpu_layers=99,
            threads=6,
            extra_args=("--no-webui",),
        )
    )

    assert command == [
        str(binary),
        "--host",
        "127.0.0.1",
        "--port",
        "18081",
        "--alias",
        "sakura-audio",
        "-c",
        "8192",
        "-hf",
        "ggml-org/Qwen3-ASR-0.6B-GGUF",
        "--n-gpu-layers",
        "99",
        "--threads",
        "6",
        "--no-webui",
    ]


def test_build_llama_server_command_supports_local_gguf_and_mmproj(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"

    command = build_llama_server_command(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            model_path=str(model),
            mmproj_path=str(mmproj),
            n_gpu_layers="auto",
        )
    )

    assert "-m" in command
    assert str(model) in command
    assert "--mmproj" in command
    assert str(mmproj) in command
    assert "--n-gpu-layers" not in command


def test_build_llama_server_command_requires_model_or_hf_repo(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")

    with pytest.raises(LlamaCppRuntimeError, match="GGUF"):
        build_llama_server_command(LlamaCppLaunchConfig(binary_path=str(binary)))


def test_discover_llama_server_binary_prefers_env_then_bundled(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    env_binary = _executable(tmp_path / "env" / "llama-server")
    bundled_binary = _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )

    monkeypatch.setenv(LLAMA_CPP_SERVER_ENV, str(env_binary))
    assert discover_llama_server_binary(tmp_path) == str(env_binary)

    monkeypatch.delenv(LLAMA_CPP_SERVER_ENV)
    assert discover_llama_server_binary(tmp_path) == str(bundled_binary)


def test_check_llama_cpp_health_reads_openai_models_response() -> None:
    def fake_urlopen(request: object, timeout: float) -> _FakeHTTPResponse:
        assert str(getattr(request, "full_url", "")).endswith("/v1/models")
        assert timeout == 2.0
        return _FakeHTTPResponse({"data": [{"id": "sakura-audio"}]})

    status = check_llama_cpp_health(
        "http://127.0.0.1:18080/v1",
        timeout_seconds=2.0,
        urlopen=fake_urlopen,
    )

    assert status.healthy is True
    assert status.model_id == "sakura-audio"


def test_llama_cpp_runtime_manager_starts_process_and_registers_resource(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")
    calls: list[list[str]] = []
    stdout_handles: list[object] = []
    process = _FakeProcess(pid=4321)

    def fake_popen(args: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append(list(args))
        assert kwargs["cwd"] == str(tmp_path)
        assert "SAKURA_TEST_ENV" in kwargs["env"]
        assert kwargs["stderr"] == -2
        stdout_handles.append(kwargs["stdout"])
        return process

    health_calls = 0

    def fake_urlopen(_request: object, timeout: float) -> _FakeHTTPResponse:
        del timeout
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise OSError("not ready")
        return _FakeHTTPResponse({"data": [{"id": "sakura-managed"}]})

    registry = ResourceRegistry()
    manager = LlamaCppRuntimeManager(
        base_dir=tmp_path,
        resource_registry=registry,
        popen_factory=fake_popen,
        urlopen=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    status = manager.start(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF",
            env={"SAKURA_TEST_ENV": "1"},
        )
    )

    assert status.healthy is True
    assert status.managed is True
    assert status.pid == 4321
    assert status.model_id == "sakura-managed"
    assert status.log_path.endswith("data/logs/sensory-llama-server.log")
    assert calls and calls[0][0] == str(binary)
    assert len(registry._resources) == 1
    assert stdout_handles and not getattr(stdout_handles[0], "closed", False)

    assert manager.stop() is True
    assert process.terminated is True
    assert getattr(stdout_handles[0], "closed", False)
    assert registry._resources == []


def test_llama_cpp_runtime_log_handle_closes_when_registry_stops_process(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")
    stdout_handles: list[object] = []
    process = _FakeProcess(pid=4321)

    def fake_popen(_args: list[str], **kwargs: Any) -> _FakeProcess:
        stdout_handles.append(kwargs["stdout"])
        return process

    health_calls = 0

    def fake_urlopen(_request: object, timeout: float) -> _FakeHTTPResponse:
        del timeout
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise OSError("not ready")
        return _FakeHTTPResponse({"data": [{"id": "sakura-managed"}]})

    registry = ResourceRegistry()
    manager = LlamaCppRuntimeManager(
        base_dir=tmp_path,
        resource_registry=registry,
        popen_factory=fake_popen,
        urlopen=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    manager.start(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0",
        )
    )

    assert stdout_handles and not getattr(stdout_handles[0], "closed", False)

    registry.stop_all()

    assert process.terminated is True
    assert getattr(stdout_handles[0], "closed", False)
    assert registry._resources == []


def test_llama_cpp_runtime_manager_reuses_existing_healthy_endpoint(tmp_path: Path) -> None:
    def fake_urlopen(_request: object, timeout: float) -> _FakeHTTPResponse:
        del timeout
        return _FakeHTTPResponse({"data": [{"id": "already-running"}]})

    manager = LlamaCppRuntimeManager(
        base_dir=tmp_path,
        popen_factory=lambda _args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not start")),
        urlopen=fake_urlopen,
    )

    status = manager.start(LlamaCppLaunchConfig(hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF"))

    assert status.healthy is True
    assert status.managed is False
    assert status.model_id == "already-running"


def test_llama_cpp_platform_key_normalizes_common_platforms() -> None:
    assert llama_cpp_platform_key(system="darwin", machine="arm64") == "macos-arm64"
    assert llama_cpp_platform_key(system="win32", machine="AMD64") == "windows-x64"
    assert llama_cpp_platform_key(system="linux", machine="aarch64") == "linux-arm64"


def test_github_release_assets_are_filtered_to_compatible_runtime_packages() -> None:
    payload = {
        "tag_name": "b9763",
        "assets": [
            {
                "name": "llama-b9763-bin-macos-arm64.tar.gz",
                "browser_download_url": "https://example.invalid/macos.tar.gz",
                "size": 10,
            },
            {
                "name": "llama-b9763-bin-win-cpu-x64.zip",
                "browser_download_url": "https://example.invalid/win.zip",
                "size": 20,
            },
            {
                "name": "llama-b9763-bin-ubuntu-vulkan-x64.tar.gz",
                "browser_download_url": "https://example.invalid/vulkan.tar.gz",
                "size": 30,
            },
            {
                "name": "llama-b9763-ui.tar.gz",
                "browser_download_url": "https://example.invalid/ui.tar.gz",
                "size": 40,
            },
        ],
    }

    packages = llama_cpp_runtime_packages_from_github_release(payload)

    assert [package.platform_key for package in packages] == ["macos-arm64", "windows-x64"]
    selected = select_llama_cpp_runtime_package(packages, platform_key="macos-arm64")
    assert selected.package_id == "b9763-macos-arm64-metal"
    assert selected.binary_relpath == "llama-server"


def test_runtime_manifest_paths_include_env_and_data_dir(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    explicit = tmp_path / "mirror.json"
    monkeypatch.setenv(LLAMA_CPP_RUNTIME_MANIFEST_ENV, str(explicit))

    paths = llama_cpp_runtime_manifest_paths(tmp_path)

    assert paths[0] == explicit
    assert tmp_path / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json" in paths
    assert tmp_path / "data" / "local_runtimes" / "llama_cpp" / "llama_cpp_runtime_manifest.json" in paths


def test_runtime_manifest_packages_parse_flat_package_entries() -> None:
    packages = llama_cpp_runtime_packages_from_manifest(
        {
            "packages": [
                {
                    "id": "pinned",
                    "label": "Pinned macOS runtime",
                    "platform": "macos-arm64",
                    "url": "https://mirror.example/llama.tar.gz",
                    "binary": "bin/llama-server",
                    "sha256": "abc123",
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert len(packages) == 1
    assert packages[0].package_id == "pinned"
    assert packages[0].binary_relpath == "bin/llama-server"
    assert packages[0].sha256 == "abc123"


def test_fetch_runtime_package_catalog_prefers_local_manifest_without_network(tmp_path: Path) -> None:
    manifest = tmp_path / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "package_id": "local-macos",
                        "label": "Local mirror",
                        "platform_key": "macos-arm64",
                        "url": "https://mirror.example/llama.tar.gz",
                        "archive_format": "tar.gz",
                        "binary_relpath": "llama-server",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = fetch_llama_cpp_runtime_package_catalog(
        base_dir=tmp_path,
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network not expected")),
    )

    assert catalog.source == f"manifest:{manifest}"
    assert [package.package_id for package in catalog.packages] == ["local-macos"]


def test_runtime_manifest_relative_archive_installs_without_network(tmp_path: Path) -> None:
    manifest = tmp_path / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json"
    archive = manifest.parent / "archives" / "llama.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(_zip_bytes({"llama-server": "#!/bin/sh\n"}))
    manifest.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "package_id": "offline-macos",
                        "label": "Offline macOS",
                        "platform_key": "macos-arm64",
                        "url": "archives/llama.zip",
                        "archive_format": "zip",
                        "binary_relpath": "llama-server",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = fetch_llama_cpp_runtime_package_catalog(
        base_dir=tmp_path,
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network not expected")),
    )
    package = select_llama_cpp_runtime_package(catalog.packages, platform_key="macos-arm64")

    assert package.url == archive.resolve().as_uri()

    result = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network not expected")),
    )

    assert result.already_installed is False
    assert Path(result.binary_path).is_file()


def test_fetch_runtime_package_catalog_errors_on_missing_explicit_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "missing.json"
    monkeypatch.setenv(LLAMA_CPP_RUNTIME_MANIFEST_ENV, str(missing))

    with pytest.raises(LlamaCppRuntimeError, match="manifest 不存在"):
        fetch_llama_cpp_runtime_package_catalog(
            base_dir=tmp_path,
            urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network not expected")),
        )


def test_install_llama_cpp_runtime_package_downloads_and_extracts_zip(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"llama-server": "#!/bin/sh\n"})
    package = LlamaCppRuntimePackageSpec(
        package_id="b9763-test",
        label="test",
        platform_key="macos-arm64",
        url="https://example.invalid/llama.zip",
        archive_format="zip",
        binary_relpath="llama-server",
    )

    result = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda _request, timeout: _FakeBinaryResponse(archive_bytes),
    )

    assert result.already_installed is False
    assert Path(result.binary_path).is_file()
    assert os.access(result.binary_path, os.X_OK) or os.name == "nt"

    second = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda _request, timeout: (_ for _ in ()).throw(AssertionError("should not download")),
    )
    assert second.already_installed is True
    assert second.binary_path == result.binary_path


def test_install_llama_cpp_runtime_package_refuses_download_when_disk_is_low(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    package = LlamaCppRuntimePackageSpec(
        package_id="b9763-low-disk",
        label="test",
        platform_key="macos-arm64",
        url="https://example.invalid/llama.zip",
        archive_format="zip",
        binary_relpath="llama-server",
        size_bytes=100,
    )
    checks: list[tuple[Path, int]] = []

    def fake_disk_space_check(path: Path, required_bytes: int) -> dict[str, object]:
        checks.append((path, required_bytes))
        return {
            "ok": False,
            "available_bytes": 128,
            "needed_bytes": 256,
            "required_bytes": required_bytes,
        }

    monkeypatch.setattr(llama_cpp_runtime, "build_disk_space_check", fake_disk_space_check)

    with pytest.raises(LlamaCppRuntimeError, match="磁盘空间不足"):
        install_llama_cpp_runtime_package(
            tmp_path,
            package,
            urlopen=lambda _request, timeout: (_ for _ in ()).throw(
                AssertionError("runtime archive should not download when disk is low")
            ),
        )

    assert checks
    assert checks[0][0].name == "llama.zip"
    assert checks[0][1] == 200


def test_install_llama_cpp_runtime_package_reuses_nested_existing_binary(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"bin/llama-server": "#!/bin/sh\n"})
    package = LlamaCppRuntimePackageSpec(
        package_id="b9763-nested",
        label="test",
        platform_key="macos-arm64",
        url="https://example.invalid/llama.zip",
        archive_format="zip",
        binary_relpath="llama-server",
    )

    result = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda _request, timeout: _FakeBinaryResponse(archive_bytes),
    )

    assert Path(result.binary_path).name == "llama-server"
    assert Path(result.binary_path).parent.name == "bin"

    second = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda _request, timeout: (_ for _ in ()).throw(AssertionError("should not download")),
    )

    assert second.already_installed is True
    assert second.binary_path == result.binary_path


def test_install_llama_cpp_runtime_package_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"../llama-server": "#!/bin/sh\n"})
    package = LlamaCppRuntimePackageSpec(
        package_id="bad",
        label="bad",
        platform_key="macos-arm64",
        url="https://example.invalid/bad.zip",
        archive_format="zip",
        binary_relpath="llama-server",
    )

    with pytest.raises(LlamaCppRuntimeError, match="不安全"):
        install_llama_cpp_runtime_package(
            tmp_path,
            package,
            urlopen=lambda _request, timeout: _FakeBinaryResponse(archive_bytes),
        )


def test_install_llama_cpp_runtime_package_allows_safe_tar_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("tar symlink extraction is platform-specific")
    archive_bytes = _tar_gz_bytes(
        {
            "llama-b/bin/llama-server": "#!/bin/sh\n",
            "llama-b/libreal.dylib": "lib",
        },
        symlinks={"llama-b/libalias.dylib": "libreal.dylib"},
    )
    package = LlamaCppRuntimePackageSpec(
        package_id="b9763-tar",
        label="test",
        platform_key="macos-arm64",
        url="https://example.invalid/llama.tar.gz",
        archive_format="tar.gz",
        binary_relpath="llama-server",
    )

    result = install_llama_cpp_runtime_package(
        tmp_path,
        package,
        urlopen=lambda _request, timeout: _FakeBinaryResponse(archive_bytes),
    )

    assert Path(result.binary_path).name == "llama-server"
    assert (Path(result.install_dir) / "llama-b" / "libalias.dylib").exists()


def test_install_llama_cpp_runtime_package_rejects_tar_symlink_traversal(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("tar symlink extraction is platform-specific")
    archive_bytes = _tar_gz_bytes(
        {"llama-b/bin/llama-server": "#!/bin/sh\n"},
        symlinks={"llama-b/libalias.dylib": "../outside.dylib"},
    )
    package = LlamaCppRuntimePackageSpec(
        package_id="bad-tar",
        label="bad",
        platform_key="macos-arm64",
        url="https://example.invalid/bad.tar.gz",
        archive_format="tar.gz",
        binary_relpath="llama-server",
    )

    with pytest.raises(LlamaCppRuntimeError, match="不安全的链接"):
        install_llama_cpp_runtime_package(
            tmp_path,
            package,
            urlopen=lambda _request, timeout: _FakeBinaryResponse(archive_bytes),
        )


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeBinaryResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self) -> "_FakeBinaryResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        start = self.offset
        end = min(len(self.payload), start + size)
        self.offset = end
        return self.payload[start:end]


class _FakeProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._alive = False
        return 0


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _tar_gz_bytes(files: dict[str, str], *, symlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith("llama-server") else 0o644
            archive.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()
