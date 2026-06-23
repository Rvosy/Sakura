from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from app.core.debug_log import debug_log
from app.core.resource_manager import ProcessResource, ResourceRegistry
from app.storage.paths import StoragePaths


DEFAULT_LLAMA_CPP_HOST = "127.0.0.1"
DEFAULT_LLAMA_CPP_MANAGED_PORT = 18080
DEFAULT_LLAMA_CPP_ALIAS = "sakura-sensory"
LLAMA_CPP_SERVER_ENV = "SAKURA_LLAMA_SERVER"
LLAMA_CPP_RUNTIME_MANIFEST_ENV = "SAKURA_LLAMA_CPP_RUNTIME_MANIFEST"
LLAMA_CPP_RUNTIME_MANIFEST_FILENAMES = (
    "runtime_manifest.json",
    "llama_cpp_runtime_manifest.json",
)
LLAMA_CPP_GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
LLAMA_CPP_MANAGED_RUNTIME_MARKER = "llama.cpp"


class LlamaCppRuntimeError(RuntimeError):
    """Raised when Sakura cannot safely prepare or start a llama.cpp runtime."""


class PopenFactory(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        **kwargs: Any,
    ) -> Any:
        """Create a subprocess-compatible process handle."""


@dataclass(frozen=True)
class LlamaCppLaunchConfig:
    """Stable launch contract for a managed llama.cpp ``llama-server`` sidecar."""

    binary_path: str = ""
    model_path: str = ""
    hf_repo: str = ""
    mmproj_path: str = ""
    host: str = DEFAULT_LLAMA_CPP_HOST
    port: int = DEFAULT_LLAMA_CPP_MANAGED_PORT
    alias: str = DEFAULT_LLAMA_CPP_ALIAS
    ctx_size: int = 4096
    n_gpu_layers: int | str = "auto"
    threads: int = 0
    timeout_seconds: float = 30.0
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    def normalized(self) -> "LlamaCppLaunchConfig":
        host = str(self.host or DEFAULT_LLAMA_CPP_HOST).strip() or DEFAULT_LLAMA_CPP_HOST
        alias = str(self.alias or DEFAULT_LLAMA_CPP_ALIAS).strip() or DEFAULT_LLAMA_CPP_ALIAS
        return LlamaCppLaunchConfig(
            binary_path=str(self.binary_path or "").strip(),
            model_path=str(self.model_path or "").strip(),
            hf_repo=str(self.hf_repo or "").strip(),
            mmproj_path=str(self.mmproj_path or "").strip(),
            host=host,
            port=_clamp_int(self.port, 1, 65535, DEFAULT_LLAMA_CPP_MANAGED_PORT),
            alias=alias,
            ctx_size=_clamp_int(self.ctx_size, 512, 262144, 4096),
            n_gpu_layers=_normalize_gpu_layers(self.n_gpu_layers),
            threads=max(0, _clamp_int(self.threads, 0, 1024, 0)),
            timeout_seconds=max(1.0, min(300.0, _float(self.timeout_seconds, 30.0))),
            extra_args=tuple(str(arg).strip() for arg in self.extra_args if str(arg).strip()),
            env={str(key): str(value) for key, value in dict(self.env).items()},
        )

    @property
    def endpoint(self) -> str:
        normalized = self.normalized()
        return f"http://{normalized.host}:{normalized.port}/v1"


@dataclass(frozen=True)
class LlamaCppRuntimeStatus:
    endpoint: str
    model_id: str = ""
    pid: int = 0
    managed: bool = False
    healthy: bool = False
    log_path: str = ""


@dataclass(frozen=True)
class LlamaCppRuntimePackageSpec:
    """Downloadable llama.cpp runtime package selected for one platform."""

    package_id: str
    label: str
    platform_key: str
    url: str
    archive_format: str
    binary_relpath: str
    version: str = ""
    variant: str = "cpu"
    sha256: str = ""
    size_bytes: int = 0

    def normalized(self) -> "LlamaCppRuntimePackageSpec":
        package_id = _safe_package_id(self.package_id) or "llama-cpp-runtime"
        archive_format = _normalize_archive_format(self.archive_format, self.url)
        binary_relpath = str(self.binary_relpath or _default_binary_relpath()).strip()
        return LlamaCppRuntimePackageSpec(
            package_id=package_id,
            label=str(self.label or package_id).strip() or package_id,
            platform_key=str(self.platform_key or "").strip().lower(),
            url=str(self.url or "").strip(),
            archive_format=archive_format,
            binary_relpath=binary_relpath,
            version=str(self.version or "").strip(),
            variant=str(self.variant or "cpu").strip().lower() or "cpu",
            sha256=str(self.sha256 or "").strip().lower(),
            size_bytes=max(0, _clamp_int(self.size_bytes, 0, 10**12, 0)),
        )

    def to_mapping(self) -> dict[str, Any]:
        normalized = self.normalized()
        data: dict[str, Any] = {
            "package_id": normalized.package_id,
            "label": normalized.label,
            "platform_key": normalized.platform_key,
            "url": normalized.url,
            "archive_format": normalized.archive_format,
            "binary_relpath": normalized.binary_relpath,
            "version": normalized.version,
            "variant": normalized.variant,
        }
        if normalized.sha256:
            data["sha256"] = normalized.sha256
        if normalized.size_bytes:
            data["size_bytes"] = normalized.size_bytes
        return data


@dataclass(frozen=True)
class LlamaCppRuntimeInstallResult:
    package: LlamaCppRuntimePackageSpec | None
    install_dir: str
    binary_path: str
    already_installed: bool = False
    message: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "package": self.package.to_mapping() if self.package is not None else None,
            "install_dir": self.install_dir,
            "binary_path": self.binary_path,
            "already_installed": bool(self.already_installed),
            "message": self.message,
        }


@dataclass(frozen=True)
class LlamaCppRuntimePackageCatalog:
    """Runtime package list plus provenance for installer diagnostics."""

    source: str
    packages: tuple[LlamaCppRuntimePackageSpec, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "packages": [package.to_mapping() for package in self.packages],
        }


def llama_cpp_platform_key(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    normalized_system = (system or sys.platform).strip().lower()
    normalized_machine = (machine or platform.machine()).strip().lower()
    arch = _normalize_architecture(normalized_machine)
    if normalized_system == "darwin":
        return f"macos-{arch}"
    if normalized_system.startswith("win"):
        return f"windows-{arch}"
    if normalized_system.startswith("linux"):
        return f"linux-{arch}"
    return f"{normalized_system}-{arch}"


def fetch_latest_llama_cpp_runtime_packages(
    *,
    timeout_seconds: float = 20.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> list[LlamaCppRuntimePackageSpec]:
    """Fetch compatible official release assets from llama.cpp's latest release."""

    request = urllib.request.Request(
        LLAMA_CPP_GITHUB_LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Sakura sensory runtime installer",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LlamaCppRuntimeError(f"无法读取 llama.cpp release 信息：{exc}") from exc
    if not isinstance(payload, dict):
        raise LlamaCppRuntimeError("llama.cpp release 信息格式无效。")
    return llama_cpp_runtime_packages_from_github_release(payload)


def fetch_llama_cpp_runtime_package_catalog(
    *,
    base_dir: Path | None = None,
    timeout_seconds: float = 20.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> LlamaCppRuntimePackageCatalog:
    """Load a local pinned package manifest, falling back to GitHub latest."""

    manifest_catalog = _load_runtime_manifest_catalog(base_dir)
    if manifest_catalog is not None:
        return manifest_catalog
    packages = fetch_latest_llama_cpp_runtime_packages(
        timeout_seconds=timeout_seconds,
        urlopen=urlopen,
    )
    return LlamaCppRuntimePackageCatalog(
        source=LLAMA_CPP_GITHUB_LATEST_RELEASE_API,
        packages=tuple(packages),
    )


def llama_cpp_runtime_packages_from_github_release(
    payload: Mapping[str, Any],
) -> list[LlamaCppRuntimePackageSpec]:
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return []
    packages: list[LlamaCppRuntimePackageSpec] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        package = _package_from_github_asset(asset, tag)
        if package is not None:
            packages.append(package)
    return packages


def llama_cpp_runtime_packages_from_manifest(
    payload: Mapping[str, Any],
) -> list[LlamaCppRuntimePackageSpec]:
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        return []
    packages: list[LlamaCppRuntimePackageSpec] = []
    for raw_package in raw_packages:
        if not isinstance(raw_package, dict):
            continue
        packages.append(
            LlamaCppRuntimePackageSpec(
                package_id=str(raw_package.get("package_id") or raw_package.get("id") or ""),
                label=str(raw_package.get("label") or ""),
                platform_key=str(raw_package.get("platform_key") or raw_package.get("platform") or ""),
                url=str(raw_package.get("url") or ""),
                archive_format=str(raw_package.get("archive_format") or ""),
                binary_relpath=str(raw_package.get("binary_relpath") or raw_package.get("binary") or ""),
                version=str(raw_package.get("version") or ""),
                variant=str(raw_package.get("variant") or ""),
                sha256=str(raw_package.get("sha256") or ""),
                size_bytes=_clamp_int(raw_package.get("size_bytes"), 0, 10**12, 0),
            ).normalized()
        )
    return packages


def llama_cpp_runtime_manifest_paths(base_dir: Path | None = None) -> list[Path]:
    """Return local manifest candidates without touching the network."""

    candidates: list[Path] = []
    env_path = os.environ.get(LLAMA_CPP_RUNTIME_MANIFEST_ENV, "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    if base_dir is not None:
        runtime_dir = StoragePaths(base_dir).llama_cpp_runtime_dir
        candidates.extend(runtime_dir / filename for filename in LLAMA_CPP_RUNTIME_MANIFEST_FILENAMES)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_runtime_manifest_catalog(base_dir: Path | None) -> LlamaCppRuntimePackageCatalog | None:
    env_path = os.environ.get(LLAMA_CPP_RUNTIME_MANIFEST_ENV, "").strip()
    explicit_manifest = Path(env_path).expanduser() if env_path else None
    for path in llama_cpp_runtime_manifest_paths(base_dir):
        if not path.is_file():
            if explicit_manifest is not None and path == explicit_manifest:
                raise LlamaCppRuntimeError(f"llama.cpp 运行时 manifest 不存在：{path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LlamaCppRuntimeError(f"无法读取 llama.cpp 运行时 manifest：{path}：{exc}") from exc
        if not isinstance(payload, dict):
            raise LlamaCppRuntimeError(f"llama.cpp 运行时 manifest 必须是 JSON 对象：{path}")
        packages = _resolve_manifest_package_urls(
            llama_cpp_runtime_packages_from_manifest(payload),
            path.parent,
        )
        if not packages:
            raise LlamaCppRuntimeError(f"llama.cpp 运行时 manifest 未包含可用 packages：{path}")
        return LlamaCppRuntimePackageCatalog(
            source=f"manifest:{path}",
            packages=tuple(packages),
        )
    return None


def _resolve_manifest_package_urls(
    packages: Sequence[LlamaCppRuntimePackageSpec],
    manifest_dir: Path,
) -> list[LlamaCppRuntimePackageSpec]:
    resolved: list[LlamaCppRuntimePackageSpec] = []
    for package in packages:
        normalized = package.normalized()
        url = _resolve_manifest_package_url(normalized.url, manifest_dir)
        resolved.append(replace(normalized, url=url).normalized())
    return resolved


def _resolve_manifest_package_url(url: str, manifest_dir: Path) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme:
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = manifest_dir / path
    return path.resolve().as_uri()


def select_llama_cpp_runtime_package(
    packages: Sequence[LlamaCppRuntimePackageSpec],
    *,
    platform_key: str | None = None,
    preferred_variant: str = "auto",
) -> LlamaCppRuntimePackageSpec:
    key = (platform_key or llama_cpp_platform_key()).strip().lower()
    candidates = [package.normalized() for package in packages if package.normalized().platform_key == key]
    if not candidates:
        raise LlamaCppRuntimeError(f"没有找到适用于 {key} 的 llama.cpp 运行时包。")
    variant = preferred_variant.strip().lower()
    if variant and variant != "auto":
        variant_candidates = [package for package in candidates if package.variant == variant]
        if variant_candidates:
            candidates = variant_candidates
    return sorted(candidates, key=lambda package: _package_preference_score(package, key))[0]


def install_llama_cpp_runtime_package(
    base_dir: Path,
    package: LlamaCppRuntimePackageSpec,
    *,
    timeout_seconds: float = 600.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> LlamaCppRuntimeInstallResult:
    normalized = package.normalized()
    if not normalized.url:
        raise LlamaCppRuntimeError("llama.cpp 运行时包缺少下载 URL。")
    paths = StoragePaths(base_dir)
    root = paths.llama_cpp_runtime_dir
    root.mkdir(parents=True, exist_ok=True)
    install_dir = paths.llama_cpp_runtime_for(normalized.package_id)
    existing_binary = _resolve_installed_binary(normalized, install_dir)
    if not _is_executable_file(existing_binary) and install_dir.exists():
        existing_binary = _find_llama_server_binary(install_dir)
    if _is_executable_file(existing_binary):
        return LlamaCppRuntimeInstallResult(
            package=normalized,
            install_dir=str(install_dir),
            binary_path=str(existing_binary),
            already_installed=True,
            message="llama.cpp 运行时已存在。",
        )

    archive_dir = root / "_downloads"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / _archive_filename(normalized)
    _download_file(normalized.url, archive_path, timeout_seconds=timeout_seconds, urlopen=urlopen)
    if normalized.sha256:
        actual = _sha256_file(archive_path)
        if actual != normalized.sha256:
            archive_path.unlink(missing_ok=True)
            raise LlamaCppRuntimeError("llama.cpp 运行时包校验失败。")

    staging_dir = root / f".{normalized.package_id}.extracting"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        _extract_archive(archive_path, staging_dir, normalized.archive_format)
        binary_path = _resolve_installed_binary(normalized, staging_dir)
        if not binary_path.is_file():
            binary_path = _find_llama_server_binary(staging_dir)
        if not binary_path.is_file():
            raise LlamaCppRuntimeError("运行时包中未找到 llama-server 可执行文件。")
        if sys.platform != "win32":
            binary_path.chmod(binary_path.stat().st_mode | 0o755)
        if install_dir.exists():
            shutil.rmtree(install_dir)
        staging_dir.rename(install_dir)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    installed_binary = _resolve_installed_binary(normalized, install_dir)
    if not _is_executable_file(installed_binary):
        installed_binary = _find_llama_server_binary(install_dir)
    if not _is_executable_file(installed_binary):
        raise LlamaCppRuntimeError("llama-server 已解压但不可执行。")
    return LlamaCppRuntimeInstallResult(
        package=normalized,
        install_dir=str(install_dir),
        binary_path=str(installed_binary),
        already_installed=False,
        message="llama.cpp 运行时已安装。",
    )


def discover_llama_server_binary(base_dir: Path | None = None) -> str:
    """Find a usable ``llama-server`` without changing user global state."""

    env_path = os.environ.get(LLAMA_CPP_SERVER_ENV, "").strip()
    if env_path and _is_executable_file(Path(env_path)):
        return str(Path(env_path).expanduser())
    for path in _bundled_binary_candidates(base_dir):
        if _is_executable_file(path):
            return str(path)
    for name in _llama_server_binary_names():
        found = shutil.which(name)
        if found:
            return found
    return ""


def build_llama_server_command(
    config: LlamaCppLaunchConfig,
    *,
    base_dir: Path | None = None,
) -> list[str]:
    normalized = config.normalized()
    binary = normalized.binary_path or discover_llama_server_binary(base_dir)
    if not binary:
        raise LlamaCppRuntimeError(
            "未找到 llama-server。请先选择 llama.cpp binary，或安装/下载 Sakura 托管的 llama.cpp runtime。"
        )
    command = [
        binary,
        "--host",
        normalized.host,
        "--port",
        str(normalized.port),
        "--alias",
        normalized.alias,
        "-c",
        str(normalized.ctx_size),
    ]
    if normalized.hf_repo:
        command.extend(["-hf", normalized.hf_repo])
    elif normalized.model_path:
        command.extend(["-m", normalized.model_path])
    else:
        raise LlamaCppRuntimeError("请先选择 GGUF 模型文件或 Hugging Face GGUF 仓库。")
    if normalized.mmproj_path:
        command.extend(["--mmproj", normalized.mmproj_path])
    if normalized.n_gpu_layers != "auto":
        command.extend(["--n-gpu-layers", str(normalized.n_gpu_layers)])
    if normalized.threads > 0:
        command.extend(["--threads", str(normalized.threads)])
    command.extend(normalized.extra_args)
    return command


class LlamaCppRuntimeManager:
    """Start and stop a managed local ``llama-server`` sidecar."""

    def __init__(
        self,
        *,
        base_dir: Path,
        resource_registry: ResourceRegistry | None = None,
        popen_factory: PopenFactory = subprocess.Popen,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.resource_registry = resource_registry or ResourceRegistry()
        self.popen_factory = popen_factory
        self.urlopen = urlopen
        self.sleep = sleep
        self._process_resource: ProcessResource | None = None
        self._log_handle: Any | None = None

    def check_health(self, endpoint: str, *, timeout_seconds: float = 3.0) -> LlamaCppRuntimeStatus:
        return check_llama_cpp_health(endpoint, timeout_seconds=timeout_seconds, urlopen=self.urlopen)

    def start(self, config: LlamaCppLaunchConfig) -> LlamaCppRuntimeStatus:
        normalized = config.normalized()
        endpoint = normalized.endpoint
        existing = self.check_health(endpoint, timeout_seconds=1.0)
        if existing.healthy:
            return LlamaCppRuntimeStatus(
                endpoint=endpoint,
                model_id=existing.model_id,
                pid=0,
                managed=False,
                healthy=True,
            )
        command = build_llama_server_command(normalized, base_dir=self.base_dir)
        env = {**os.environ, **normalized.env}
        log_path = _llama_cpp_log_path(self.base_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab")
        self._log_handle = log_handle
        debug_log(
            "Sensory",
            "启动 llama.cpp 感知运行时",
            {
                "endpoint": endpoint,
                "binary": command[0],
                "model": normalized.hf_repo or normalized.model_path,
                "has_mmproj": bool(normalized.mmproj_path),
                "log_path": str(log_path),
            },
        )
        try:
            process = self.popen_factory(
                command,
                cwd=str(self.base_dir),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=False,
            )
        except Exception:
            self._close_log_handle()
            raise
        self._process_resource = self.resource_registry.adopt_process(
            process,
            terminator=self._terminate_process_and_close_log,
            label="sensory_llama_cpp_runtime",
            shutdown_order=840,
            terminate_timeout_s=5,
        )
        try:
            status = self._wait_until_healthy(
                endpoint,
                process,
                timeout_seconds=normalized.timeout_seconds,
                log_path=log_path,
            )
        except Exception:
            self.stop()
            raise
        return LlamaCppRuntimeStatus(
            endpoint=endpoint,
            model_id=status.model_id,
            pid=int(getattr(process, "pid", 0) or 0),
            managed=True,
            healthy=True,
            log_path=str(log_path),
        )

    def stop(self) -> bool:
        resource = self._process_resource
        self._process_resource = None
        if resource is None:
            self._close_log_handle()
            return True
        stopped = resource.stop()
        self._close_log_handle()
        return stopped

    def _wait_until_healthy(
        self,
        endpoint: str,
        process: Any,
        *,
        timeout_seconds: float,
        log_path: Path,
    ) -> LlamaCppRuntimeStatus:
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                raise LlamaCppRuntimeError(f"llama-server 启动后立即退出。日志：{log_path}")
            status = self.check_health(endpoint, timeout_seconds=1.0)
            if status.healthy:
                return status
            last_error = "health check failed"
            self.sleep(0.2)
        raise LlamaCppRuntimeError(
            f"llama-server 未在 {timeout_seconds:.0f} 秒内就绪：{last_error}。日志：{log_path}"
        )

    def _close_log_handle(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            pass

    def _terminate_process_and_close_log(self, process: Any, timeout_s: int) -> None:
        try:
            process.terminate()
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout_s)
        finally:
            self._close_log_handle()


def check_llama_cpp_health(
    endpoint: str,
    *,
    timeout_seconds: float = 3.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> LlamaCppRuntimeStatus:
    base = endpoint.strip().rstrip("/")
    models_url = base if base.endswith("/models") else f"{base}/models"
    try:
        request = urllib.request.Request(models_url, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return LlamaCppRuntimeStatus(endpoint=endpoint, healthy=False)
    model_id = ""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                model_id = str(first.get("id") or "").strip()
    return LlamaCppRuntimeStatus(
        endpoint=endpoint,
        model_id=model_id,
        healthy=bool(model_id),
    )


def _bundled_binary_candidates(base_dir: Path | None) -> list[Path]:
    if base_dir is None:
        return []
    root = StoragePaths(base_dir).llama_cpp_runtime_dir
    names = set(_llama_server_binary_names())
    candidates: list[Path] = []
    for name in names:
        candidates.append(root / name)
        candidates.append(root / "bin" / name)
    try:
        candidates.extend(path for path in root.rglob("*") if path.name in names)
    except OSError:
        pass
    return candidates


def _llama_cpp_log_path(base_dir: Path) -> Path:
    return StoragePaths(base_dir).logs_dir / "sensory-llama-server.log"


def _llama_server_binary_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("llama-server.exe",)
    return ("llama-server",)


def _package_from_github_asset(
    asset: Mapping[str, Any],
    tag: str,
) -> LlamaCppRuntimePackageSpec | None:
    name = str(asset.get("name") or "").strip()
    url = str(asset.get("browser_download_url") or "").strip()
    lower = name.lower()
    if not name or not url or "-bin-" not in lower:
        return None
    if lower.startswith("cudart-") or any(
        marker in lower
        for marker in (
            "android",
            "hip",
            "opencl",
            "openvino",
            "rocm",
            "sycl",
            "vulkan",
            "xcframework",
        )
    ):
        return None
    platform_key = ""
    variant = "cpu"
    if "-bin-macos-arm64" in lower:
        platform_key = "macos-arm64"
        variant = "metal"
    elif "-bin-macos-x64" in lower:
        platform_key = "macos-x64"
        variant = "cpu"
    elif "-bin-ubuntu-x64" in lower:
        platform_key = "linux-x64"
        variant = "cpu"
    elif "-bin-ubuntu-arm64" in lower:
        platform_key = "linux-arm64"
        variant = "cpu"
    elif "-bin-win-cpu-x64" in lower:
        platform_key = "windows-x64"
        variant = "cpu"
    elif "-bin-win-cpu-arm64" in lower:
        platform_key = "windows-arm64"
        variant = "cpu"
    if not platform_key:
        return None
    archive_format = _normalize_archive_format("", name)
    if not archive_format:
        return None
    version = tag or _version_from_asset_name(name)
    package_id = _safe_package_id(f"{version}-{platform_key}-{variant}")
    binary_name = "llama-server.exe" if platform_key.startswith("windows-") else "llama-server"
    label = f"llama.cpp {version or 'latest'} {platform_key} {variant}".strip()
    return LlamaCppRuntimePackageSpec(
        package_id=package_id,
        label=label,
        platform_key=platform_key,
        url=url,
        archive_format=archive_format,
        binary_relpath=binary_name,
        version=version,
        variant=variant,
        size_bytes=_clamp_int(asset.get("size"), 0, 10**12, 0),
    ).normalized()


def _version_from_asset_name(name: str) -> str:
    parts = name.split("-")
    for part in parts:
        if part.startswith("b") and part[1:].isdigit():
            return part
    return ""


def _normalize_architecture(machine: str) -> str:
    normalized = machine.strip().lower().replace("_", "-")
    if normalized in {"x86-64", "amd64", "x64"}:
        return "x64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    return normalized or "unknown"


def _package_preference_score(package: LlamaCppRuntimePackageSpec, platform_key: str) -> tuple[int, str]:
    if platform_key.startswith("macos-"):
        variant_order = {"metal": 0, "cpu": 1}
    else:
        variant_order = {"cpu": 0}
    return (variant_order.get(package.variant, 20), package.package_id)


def _normalize_archive_format(value: str, url_or_name: str = "") -> str:
    text = str(value or "").strip().lower()
    if text in {"zip", "tar.gz", "tgz"}:
        return "tar.gz" if text == "tgz" else text
    name = url_or_name.strip().lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar.gz"
    return ""


def _archive_filename(package: LlamaCppRuntimePackageSpec) -> str:
    suffix = ".zip" if package.archive_format == "zip" else ".tar.gz"
    raw_name = Path(package.url.split("?", 1)[0]).name
    if raw_name.endswith((".zip", ".tar.gz", ".tgz")):
        return raw_name
    return f"{package.package_id}{suffix}"


def _download_file(
    url: str,
    target: Path,
    *,
    timeout_seconds: float,
    urlopen: Callable[..., Any],
) -> None:
    local_archive = _local_archive_path_from_url(url)
    if local_archive is not None:
        try:
            if not local_archive.is_file():
                raise FileNotFoundError(local_archive)
            temp_path = target.with_suffix(target.suffix + ".tmp")
            shutil.copyfile(local_archive, temp_path)
            temp_path.replace(target)
            return
        except Exception as exc:
            target.with_suffix(target.suffix + ".tmp").unlink(missing_ok=True)
            raise LlamaCppRuntimeError(f"复制 llama.cpp 本地运行时包失败：{exc}") from exc
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Sakura sensory runtime installer"},
        method="GET",
    )
    temp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with urlopen(request, timeout=timeout_seconds) as response, temp_path.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        temp_path.replace(target)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise LlamaCppRuntimeError(f"下载 llama.cpp 运行时失败：{exc}") from exc


def _local_archive_path_from_url(url: str) -> Path | None:
    value = str(url or "").strip()
    if not value:
        return None
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path)).expanduser()
    if parsed.scheme:
        return None
    path = Path(value).expanduser()
    if path.is_absolute() or path.exists() or value.startswith((".", "~")):
        return path
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive(archive_path: Path, target_dir: Path, archive_format: str) -> None:
    if archive_format == "zip":
        _extract_zip_archive(archive_path, target_dir)
        return
    if archive_format == "tar.gz":
        _extract_tar_archive(archive_path, target_dir)
        return
    raise LlamaCppRuntimeError(f"不支持的运行时包格式：{archive_format}")


def _extract_zip_archive(archive_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            _validate_archive_member(info.filename, target_dir)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise LlamaCppRuntimeError("运行时包包含不受支持的符号链接。")
        archive.extractall(target_dir)


def _extract_tar_archive(archive_path: Path, target_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            _validate_archive_member(member.name, target_dir)
            if member.issym() or member.islnk():
                _validate_archive_link(member, target_dir)
        _extractall_tar_checked(archive, target_dir)


def _validate_archive_member(name: str, target_dir: Path) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise LlamaCppRuntimeError("运行时包包含不安全的路径。")
    destination = (target_dir / path).resolve()
    root = target_dir.resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise LlamaCppRuntimeError("运行时包包含越界路径。") from exc


def _validate_archive_link(member: tarfile.TarInfo, target_dir: Path) -> None:
    link = Path(member.linkname)
    if link.is_absolute() or ".." in link.parts:
        raise LlamaCppRuntimeError("运行时包包含不安全的链接。")
    root = target_dir.resolve()
    member_path = (target_dir / member.name).resolve()
    if member.issym():
        link_target = (member_path.parent / link).resolve()
    else:
        link_target = (target_dir / link).resolve()
    try:
        link_target.relative_to(root)
    except ValueError as exc:
        raise LlamaCppRuntimeError("运行时包包含越界链接。") from exc


def _extractall_tar_checked(archive: tarfile.TarFile, target_dir: Path) -> None:
    if sys.version_info >= (3, 12):
        archive.extractall(target_dir, filter="fully_trusted")
    else:
        archive.extractall(target_dir)


def _resolve_installed_binary(
    package: LlamaCppRuntimePackageSpec,
    install_dir: Path,
) -> Path:
    return install_dir / package.binary_relpath


def _find_llama_server_binary(root: Path) -> Path:
    names = set(_llama_server_binary_names())
    try:
        for path in root.rglob("*"):
            if path.name in names:
                return path
    except OSError:
        pass
    return root / _default_binary_relpath()


def _default_binary_relpath() -> str:
    return "llama-server.exe" if sys.platform == "win32" else "llama-server"


def _safe_package_id(value: str) -> str:
    text = str(value or "").strip().lower()
    safe = []
    for char in text:
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        elif char in {"/", " "}:
            safe.append("-")
    return "".join(safe).strip(".-_")[:96]


def _is_executable_file(path: Path) -> bool:
    expanded = path.expanduser()
    if not expanded.is_file():
        return False
    if sys.platform == "win32":
        return True
    return os.access(expanded, os.X_OK)


def _normalize_gpu_layers(value: int | str) -> int | str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "auto"}:
            return "auto"
        return _clamp_int(text, 0, 9999, 0)
    return _clamp_int(value, 0, 9999, 0)


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
