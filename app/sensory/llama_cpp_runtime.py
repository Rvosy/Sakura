from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.debug_log import debug_log
from app.core.resource_manager import ProcessResource, ResourceRegistry
from app.storage.paths import StoragePaths


DEFAULT_LLAMA_CPP_HOST = "127.0.0.1"
DEFAULT_LLAMA_CPP_MANAGED_PORT = 18080
DEFAULT_LLAMA_CPP_ALIAS = "sakura-sensory"
LLAMA_CPP_SERVER_ENV = "SAKURA_LLAMA_SERVER"


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
        debug_log(
            "Sensory",
            "启动 llama.cpp 感知运行时",
            {
                "endpoint": endpoint,
                "binary": command[0],
                "model": normalized.hf_repo or normalized.model_path,
                "has_mmproj": bool(normalized.mmproj_path),
            },
        )
        process = self.popen_factory(
            command,
            cwd=str(self.base_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=False,
        )
        self._process_resource = self.resource_registry.adopt_process(
            process,
            label="sensory_llama_cpp_runtime",
            shutdown_order=840,
            terminate_timeout_s=5,
        )
        try:
            status = self._wait_until_healthy(
                endpoint,
                process,
                timeout_seconds=normalized.timeout_seconds,
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
        )

    def stop(self) -> bool:
        resource = self._process_resource
        self._process_resource = None
        if resource is None:
            return True
        return resource.stop()

    def _wait_until_healthy(
        self,
        endpoint: str,
        process: Any,
        *,
        timeout_seconds: float,
    ) -> LlamaCppRuntimeStatus:
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                raise LlamaCppRuntimeError("llama-server 启动后立即退出。")
            status = self.check_health(endpoint, timeout_seconds=1.0)
            if status.healthy:
                return status
            last_error = "health check failed"
            self.sleep(0.2)
        raise LlamaCppRuntimeError(f"llama-server 未在 {timeout_seconds:.0f} 秒内就绪：{last_error}")


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


def _llama_server_binary_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("llama-server.exe",)
    return ("llama-server",)


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
