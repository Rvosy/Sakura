from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.util
import json
import logging
import multiprocessing
import os
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Iterable

from app.core.runtime_resources import (
    ResourceRegistry,
    ThreadGroupResource,
)
from app.storage.atomic import atomic_write_text, rename_with_retry
from app.storage.archive_security import validate_zip_resource_limits
from app.storage.chat_history import ChatHistoryEntry
from app.storage.paths import StoragePaths

if TYPE_CHECKING:
    from app.llm.api_client import ApiSettings


logger = logging.getLogger(__name__)


def _prepare_memory_background_imports() -> bool:
    """在 Core 路由启动前完成 mem0/OpenAI 共享的 anyio 导入。

    Memory preload 与 MCP deferred startup 会并行触发 OpenAI/anyio 依赖；让
    首次 anyio 初始化发生在 Core 启动线程，避免 Router 请求线程和后台线程
    同时观察到 partially initialized module 并把 Memory RPC 卡在 import lock 上。
    """
    try:
        import anyio  # noqa: F401
    except ImportError:
        # Runtime v2 的最小 staged Python 只保证 Core 基础依赖。Memory 是
        # 可选能力；缺少其后台依赖时必须由 Memory 自身降级，不能阻断 Core
        # 握手、旧能力验收或普通聊天。
        return False
    return True


_prepare_memory_background_imports()


MEM0_VENDOR_ROOT = Path(__file__).resolve().parents[2] / "third_party" / "mem0"
DEFAULT_MEMORY_SCOPE = "sakura"
DEFAULT_COLLECTION_NAME = "sakura_memories"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_ARTIFACT_REPO = "qdrant/all-MiniLM-L6-v2-onnx"
DEFAULT_EMBEDDING_ARTIFACT_REVISION = "5f1b8cd78bc4fb444dd171e59b18f3a3af89a079"
DEFAULT_EMBEDDING_DIMS = 384
DEFAULT_MEMORY_LIMIT = 20
MEMORY_INITIALIZATION_LOG_NAME = "memory-initialization.jsonl"
MEMORY_INITIALIZATION_LOG_MAX_BYTES = 1024 * 1024
MEMORY_LAYER_CORE_PROFILE = "core_profile"
MEMORY_LAYER_SEMANTIC = "semantic"
MEMORY_LAYER_EPISODIC = "episodic"
MEMORY_LAYER_PROCEDURAL = "procedural"
MEMORY_LAYER_SESSION = "session"
DEFAULT_MEMORY_LAYER = MEMORY_LAYER_SEMANTIC
MEMORY_LAYERS = (
    MEMORY_LAYER_CORE_PROFILE,
    MEMORY_LAYER_SEMANTIC,
    MEMORY_LAYER_EPISODIC,
    MEMORY_LAYER_PROCEDURAL,
    MEMORY_LAYER_SESSION,
)
VECTOR_MEMORY_LAYERS = (
    MEMORY_LAYER_SEMANTIC,
    MEMORY_LAYER_EPISODIC,
    MEMORY_LAYER_PROCEDURAL,
    MEMORY_LAYER_SESSION,
)
MEMORY_LAYER_LABELS = {
    MEMORY_LAYER_CORE_PROFILE: "常驻档案",
    MEMORY_LAYER_SEMANTIC: "长期事实",
    MEMORY_LAYER_EPISODIC: "事件总结",
    MEMORY_LAYER_PROCEDURAL: "协作规则",
    MEMORY_LAYER_SESSION: "当前任务",
}
DEFAULT_MEMORY_IMPORTANCE = 0.5
DEFAULT_MEMORY_CONFIDENCE = 0.75
DEFAULT_MEMORY_SOURCE = "manual"
CORE_PROFILE_CONTEXT_BUDGET = 1200
SESSION_CONTEXT_BUDGET = 600
MEMORY_SECTION_CHAR_BUDGET = 1600
DEFAULT_HUGGINGFACE_ENDPOINT = "https://huggingface.co"
DEFAULT_EMBEDDING_MODEL_CACHE_NAME = "models--" + DEFAULT_EMBEDDING_ARTIFACT_REPO.replace(
    "/", "--"
)
DEFAULT_EMBEDDING_MODEL_ARTIFACTS = {
    "config.json": (
        650,
        "1b4d8e2a3988377ed8b519a31d8d31025a25f1c5f8606998e8014111438efcd7",
    ),
    "model.onnx": (
        90_387_630,
        "bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5",
    ),
    "special_tokens_map.json": (
        695,
        "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
    ),
    "tokenizer.json": (
        711_661,
        "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",
    ),
    "tokenizer_config.json": (
        1_433,
        "bd2e06a5b20fd1b13ca988bedc8763d332d242381b4fbc98f8fead4524158f79",
    ),
}
DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES = tuple(DEFAULT_EMBEDDING_MODEL_ARTIFACTS)
DEFAULT_EMBEDDING_MODEL_ALLOW_PATTERNS = (
    *DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES,
)
_MEM0_CREATE_LOCK = threading.Lock()
_MEMORY_DIAGNOSTIC_WRITE_LOCK = threading.Lock()
_EMBEDDER_OWNER = threading.local()
_MEM0_CHILD_CONNECTION: Any | None = None
_MEM0_CHILD_STAGE = "process_start"
_MEM0_CHILD_OUTCOME = "started"
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DIAGNOSTIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_MEM0_IMPORT_DIAGNOSTIC_PREFIXES = (
    ("qdrant_client.async_qdrant_fastembed", "qdrant_async_fastembed"),
    ("qdrant_client.conversions.common_types", "qdrant_common_types"),
    ("qdrant_client.qdrant_fastembed", "qdrant_fastembed"),
    ("qdrant_client.qdrant_client", "qdrant_sync_client"),
    ("qdrant_client.qdrant_remote", "qdrant_remote"),
    ("qdrant_client.client_base", "qdrant_client_base"),
    ("qdrant_client.fastembed_common", "qdrant_fastembed_common"),
    ("qdrant_client.conversions", "qdrant_conversions"),
    ("qdrant_client.local", "qdrant_local"),
    ("qdrant_client.http", "qdrant_http"),
    ("qdrant_client.grpc", "qdrant_grpc"),
    ("qdrant_client.embed", "qdrant_embed"),
    ("google.protobuf", "protobuf"),
    ("huggingface_hub", "huggingface_hub"),
    ("importlib.metadata", "importlib_metadata"),
    ("qdrant_client", "qdrant_client"),
    ("onnxruntime", "onnxruntime"),
    ("portalocker", "portalocker"),
    ("fastembed", "fastembed"),
    ("requests", "requests"),
    ("pydantic", "pydantic"),
    ("posthog", "posthog"),
    ("numpy", "numpy"),
    ("httpx", "httpx"),
    ("grpc", "grpc"),
    ("mem0", "mem0"),
)
os.environ.setdefault("MEM0_TELEMETRY", "False")
DEFAULT_MEMORY_LANGUAGE_INSTRUCTIONS = (
    "Sakura 的长期记忆必须使用简体中文记录。"
    "无论用户或助手消息使用什么语言，都要把可记忆事实翻译、归纳为自然的简体中文；"
    "技术名词、代码标识符、专有名词、路径、ID 和品牌名可保留原文。"
    "输出 JSON 结构不变，只改变 memory/text 字段的自然语言内容。"
)


def _diagnostic_token(value: object, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if _DIAGNOSTIC_TOKEN_RE.fullmatch(text) else fallback


def append_memory_initialization_diagnostic(
    base_dir: Path | None,
    *,
    component: str,
    event: str,
    stage: str = "",
    outcome: str = "",
    status: str = "",
    category: str = "",
    error_type: str = "",
    elapsed_ms: int | None = None,
    wait: bool | None = None,
    model_cached: bool | None = None,
    child_pid: int | None = None,
    process_alive: bool | None = None,
    request: str = "",
) -> None:
    """Append one bounded, content-free Memory startup diagnostic event.

    This timeline is intentionally independent from the normal runtime log so
    a blocked Router or logging configuration cannot hide cold-start evidence.
    All string fields are internal identifiers; invalid/free-form values are
    replaced instead of being persisted.
    """

    try:
        payload: dict[str, object] = {
            "timestampMs": int(time.time() * 1000),
            "component": _diagnostic_token(component),
            "event": _diagnostic_token(event),
            "pid": os.getpid(),
        }
        for key, value in (
            ("stage", stage),
            ("outcome", outcome),
            ("status", status),
            ("category", category),
            ("errorType", error_type),
            ("request", request),
        ):
            if value:
                payload[key] = _diagnostic_token(value)
        if elapsed_ms is not None:
            payload["elapsedMs"] = max(0, min(int(elapsed_ms), 86_400_000))
        if wait is not None:
            payload["wait"] = bool(wait)
        if model_cached is not None:
            payload["modelCached"] = bool(model_cached)
        if child_pid is not None:
            payload["childPid"] = max(0, int(child_pid))
        if process_alive is not None:
            payload["processAlive"] = bool(process_alive)
        path = StoragePaths(_resolve_base_dir(base_dir)).logs_dir / MEMORY_INITIALIZATION_LOG_NAME
        line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        flags = os.O_APPEND | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        with _MEMORY_DIAGNOSTIC_WRITE_LOCK:
            # The Runtime v2 Shell owns truncation and creation.  Core-only,
            # legacy and fixture runs must not leave surprise log artifacts.
            if not path.is_file():
                return
            current_size = path.stat().st_size
            if current_size + len(line) > MEMORY_INITIALIZATION_LOG_MAX_BYTES:
                return
            descriptor = os.open(path, flags, 0o600)
            try:
                os.write(descriptor, line)
            finally:
                os.close(descriptor)
    except Exception:  # noqa: BLE001 - diagnostics must never affect Memory.
        return


class ProcessIsolatedMem0RequestError(RuntimeError):
    """A bounded error returned by the isolated mem0 process."""

    def __init__(self, message: str, *, remote_type: str = "RemoteError") -> None:
        super().__init__(message)
        self.remote_type = _diagnostic_token(remote_type, "RemoteError")


class _ProcessLocalDiagnosticFastEmbedEmbedding:
    """Load FastEmbed/ONNX inside the mem0 process while reporting safe stages."""

    def __init__(self, config: Any) -> None:
        _send_process_isolated_mem0_progress(
            event="embedding_dependency_import_started",
            stage="dependency_import",
            outcome="started",
        )
        try:
            from fastembed import TextEmbedding
        except BaseException as exc:
            _send_process_isolated_mem0_progress(
                event="embedding_startup_failed",
                stage="dependency_import",
                outcome="failed",
                category="dependency_import_failed",
                error_type=exc.__class__.__name__,
            )
            raise
        _send_process_isolated_mem0_progress(
            event="embedding_dependency_import_completed",
            stage="dependency_import",
            outcome="completed",
        )
        _send_process_isolated_mem0_progress(
            event="embedding_model_load_started",
            stage="model_load",
            outcome="started",
        )
        try:
            model_name = str(config.model or DEFAULT_EMBEDDING_MODEL)
            model_kwargs = dict(config.model_kwargs or {})
            model_kwargs.setdefault("local_files_only", True)
            model_kwargs.setdefault("providers", ["CPUExecutionProvider"])
            self._delegate = TextEmbedding(
                model_name=model_name,
                **model_kwargs,
            )
            if self._delegate.embedding_size != DEFAULT_EMBEDDING_DIMS:
                raise ValueError(
                    "记忆 ONNX 模型维度不匹配："
                    f"expected {DEFAULT_EMBEDDING_DIMS}, got {self._delegate.embedding_size}"
                )
        except BaseException as exc:
            _send_process_isolated_mem0_progress(
                event="embedding_startup_failed",
                stage="model_load",
                outcome="failed",
                category=_classify_memory_load_exception(exc, stage="model_load"),
                error_type=exc.__class__.__name__,
            )
            raise
        self.config = config
        self.config.model = model_name
        self.config.embedding_dims = DEFAULT_EMBEDDING_DIMS
        _send_process_isolated_mem0_progress(
            event="embedding_model_load_completed",
            stage="model_load",
            outcome="completed",
        )

    def embed(self, text: object, memory_action: str | None = None) -> list[float]:
        del memory_action
        normalized = str(text).replace("\n", " ")
        vector = next(self._delegate.embed([normalized], batch_size=1))
        return [float(value) for value in vector]

    def embed_batch(
        self,
        texts: Iterable[object],
        memory_action: str = "add",
    ) -> list[list[float]]:
        del memory_action
        normalized = [str(text).replace("\n", " ") for text in texts]
        return [
            [float(value) for value in vector]
            for vector in self._delegate.embed(normalized)
        ]


class ProcessIsolatedMem0Client:
    """Run mem0, Qdrant, SQLite and FastEmbed/ONNX outside Core.

    The Core process only owns this bounded Pipe proxy.  Cold imports and
    native model work therefore cannot retain Core's GIL long enough for the
    Shell supervisor to misclassify a healthy generation as unresponsive.
    """

    STARTUP_TIMEOUT_SECONDS = 120.0
    REQUEST_TIMEOUT_SECONDS = 120.0

    def __init__(self, config: dict[str, Any]) -> None:
        self._lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._closed = False
        self._ready = False
        self._startup_started_at = time.monotonic()
        self._diagnostic_listener: Callable[[dict[str, object]], None] | None = None
        self._startup_diagnostic: dict[str, object] = {
            "component": "mem0_process",
            "event": "mem0_process_starting",
            "stage": "process_start",
            "outcome": "started",
        }
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_run_process_isolated_mem0_client,
            args=(child, config),
            name="sakura-memory-runtime",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._connection = parent
        self._process = process
        self._record_startup_diagnostic(
            event="mem0_process_started",
            stage="process_start",
            outcome="completed",
        )

    def set_diagnostic_listener(
        self,
        listener: Callable[[dict[str, object]], None],
    ) -> None:
        with self._diagnostic_lock:
            self._diagnostic_listener = listener
            snapshot = dict(self._startup_diagnostic)
        try:
            listener(snapshot)
        except Exception:  # noqa: BLE001 - diagnostics cannot affect startup.
            pass

    def load_diagnostic(self) -> dict[str, object]:
        with self._diagnostic_lock:
            return dict(self._startup_diagnostic)

    def _record_startup_diagnostic(
        self,
        *,
        event: str,
        stage: str,
        outcome: str,
        category: str = "",
        error_type: str = "",
    ) -> None:
        process = getattr(self, "_process", None)
        process_alive = None
        if process is not None:
            try:
                process_alive = bool(process.is_alive())
            except (AssertionError, ValueError):
                process_alive = False
        diagnostic: dict[str, object] = {
            "component": "mem0_process",
            "event": _diagnostic_token(event),
            "stage": _diagnostic_token(stage),
            "outcome": _diagnostic_token(outcome),
            "elapsedMs": max(0, int((time.monotonic() - self._startup_started_at) * 1000)),
        }
        if category:
            diagnostic["category"] = _diagnostic_token(category)
        if error_type:
            diagnostic["errorType"] = _diagnostic_token(error_type, "UnknownError")
        try:
            child_pid = getattr(process, "pid", None)
        except ValueError:
            child_pid = None
        if isinstance(child_pid, int):
            diagnostic["childPid"] = child_pid
        if process_alive is not None:
            diagnostic["processAlive"] = process_alive
        with self._diagnostic_lock:
            self._startup_diagnostic = diagnostic
            listener = self._diagnostic_listener
        if listener is not None:
            try:
                listener(dict(diagnostic))
            except Exception:  # noqa: BLE001 - diagnostics cannot affect startup.
                pass

    def _record_child_diagnostic(self, payload: object) -> None:
        source = payload if isinstance(payload, dict) else {}
        self._record_startup_diagnostic(
            event=_diagnostic_token(source.get("event"), "mem0_progress"),
            stage=_diagnostic_token(source.get("stage"), "unknown"),
            outcome=_diagnostic_token(source.get("outcome"), "started"),
            category=(
                _diagnostic_token(source.get("category"), "load_failed")
                if source.get("category")
                else ""
            ),
            error_type=(
                _diagnostic_token(source.get("errorType"), "UnknownError")
                if source.get("errorType")
                else ""
            ),
        )

    def wait_ready(
        self,
        *,
        cancel: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> None:
        deadline = time.monotonic() + (
            self.STARTUP_TIMEOUT_SECONDS if timeout is None else max(0.0, timeout)
        )
        while not self._ready:
            if cancel is not None and cancel():
                self._record_startup_diagnostic(
                    event="mem0_startup_cancelled",
                    stage="cancelled",
                    outcome="cancelled",
                    category="startup_cancelled",
                )
                self.close()
                raise RuntimeError("长期记忆子进程初始化已取消。")
            if self._closed:
                raise RuntimeError("长期记忆子进程已关闭。")
            if not self._process.is_alive():
                self._record_startup_diagnostic(
                    event="mem0_startup_failed",
                    stage="process_exit",
                    outcome="failed",
                    category="process_exited",
                    error_type="ChildProcessExit",
                )
                self.close()
                raise RuntimeError("长期记忆子进程启动失败。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._record_startup_diagnostic(
                    event="mem0_startup_failed",
                    stage="startup_wait",
                    outcome="failed",
                    category="startup_timeout",
                    error_type="TimeoutError",
                )
                self.close()
                raise RuntimeError("长期记忆子进程初始化超时。")
            if not self._connection.poll(min(0.1, remaining)):
                continue
            try:
                kind, payload = self._connection.recv()
            except (EOFError, OSError) as exc:
                self._record_startup_diagnostic(
                    event="mem0_startup_failed",
                    stage="startup_wait",
                    outcome="failed",
                    category="connection_interrupted",
                    error_type=exc.__class__.__name__,
                )
                self.close()
                raise RuntimeError("长期记忆子进程连接中断。") from exc
            if kind == "progress":
                self._record_child_diagnostic(payload)
                continue
            if kind == "ready":
                self._ready = True
                self._record_startup_diagnostic(
                    event="mem0_ready",
                    stage="ready",
                    outcome="completed",
                )
                return
            if kind == "startup_error":
                self._record_child_diagnostic(payload)
            else:
                self._record_startup_diagnostic(
                    event="mem0_startup_failed",
                    stage="startup_protocol",
                    outcome="failed",
                    category="invalid_response",
                    error_type="ProtocolError",
                )
            self.close()
            raise RuntimeError("长期记忆子进程初始化失败。")

    def get_all(self, *args: object, **kwargs: object) -> object:
        return self._request("get_all", args, kwargs)

    def search(self, *args: object, **kwargs: object) -> object:
        return self._request("search", args, kwargs)

    def add(self, *args: object, **kwargs: object) -> object:
        return self._request("add", args, kwargs)

    def get(self, *args: object, **kwargs: object) -> object:
        return self._request("get", args, kwargs)

    def update(self, *args: object, **kwargs: object) -> object:
        return self._request("update", args, kwargs)

    def delete(self, *args: object, **kwargs: object) -> object:
        return self._request("delete", args, kwargs)

    def reset_curation_cache(
        self,
        *,
        scope_id: str,
        memory_ids: Iterable[str] | None = None,
    ) -> dict[str, int]:
        result = self._request(
            "reset_curation_cache",
            (),
            {"scope_id": scope_id, "memory_ids": list(memory_ids or [])},
        )
        if not isinstance(result, dict):
            raise RuntimeError("长期记忆子进程响应无效。")
        return {
            "messages": max(0, int(result.get("messages") or 0)),
            "history": max(0, int(result.get("history") or 0)),
        }

    def reload_llm(self, llm_section: dict[str, Any]) -> None:
        self._request("reload_llm", (llm_section,), {})

    def _request(
        self,
        method: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> object:
        with self._lock:
            self.wait_ready()
            try:
                self._connection.send(("request", method, args, kwargs))
            except (BrokenPipeError, EOFError, OSError) as exc:
                self.close()
                raise RuntimeError("长期记忆子进程连接中断。") from exc
            if not self._connection.poll(self.REQUEST_TIMEOUT_SECONDS):
                self.close()
                raise TimeoutError("长期记忆子进程请求超时。")
            try:
                response_kind, payload = self._connection.recv()
            except (EOFError, OSError) as exc:
                self.close()
                raise RuntimeError("长期记忆子进程连接中断。") from exc
            if response_kind == "result":
                return payload
            if response_kind == "error" and isinstance(payload, dict):
                message = str(payload.get("message") or "长期记忆子进程请求失败。")[:2000]
                raise ProcessIsolatedMem0RequestError(
                    message,
                    remote_type=str(payload.get("errorType") or "RemoteError"),
                )
            self.close()
            raise RuntimeError("长期记忆子进程响应无效。")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = getattr(self, "_connection", None)
        process = getattr(self, "_process", None)
        if connection is not None:
            try:
                connection.send(("close",))
            except (BrokenPipeError, EOFError, OSError):
                pass
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            try:
                process.join(timeout=0.15)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.25)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.25)
            except (AssertionError, OSError, ValueError):
                pass
            try:
                process.close()
            except ValueError:
                pass


class ProcessIsolatedFastEmbedEmbedding:
    """Run FastEmbed/ONNX Runtime outside the Core control process.

    This narrow proxy remains available to preserve the bounded embedding Pipe
    contract.  Runtime v2 normally owns it as part of the complete Memory child,
    so Core never imports ONNX Runtime or performs native inference itself.
    """

    STARTUP_TIMEOUT_SECONDS = 120.0
    REQUEST_TIMEOUT_SECONDS = 30.0

    def __init__(self, config: Any) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._closed = False
        self._ready = False
        self._startup_started_at = time.monotonic()
        self._diagnostic_listener: Callable[[dict[str, object]], None] | None = None
        self._startup_diagnostic: dict[str, object] = {
            "event": "embedding_process_starting",
            "stage": "process_start",
            "outcome": "started",
        }
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_run_process_isolated_fastembed_embedding,
            args=(
                child,
                {
                    "model": str(config.model or DEFAULT_EMBEDDING_MODEL),
                    "model_kwargs": dict(config.model_kwargs or {}),
                },
            ),
            name="sakura-memory-embedding",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._connection = parent
        self._process = process
        self._record_startup_diagnostic(
            event="embedding_process_started",
            stage="process_start",
            outcome="completed",
        )
        owner = getattr(_EMBEDDER_OWNER, "register", None)
        if callable(owner):
            owner(self)

    def set_diagnostic_listener(
        self,
        listener: Callable[[dict[str, object]], None],
    ) -> None:
        with self._diagnostic_lock:
            self._diagnostic_listener = listener
            snapshot = dict(self._startup_diagnostic)
        try:
            listener(snapshot)
        except Exception:  # noqa: BLE001 - diagnostics cannot affect startup.
            pass

    def load_diagnostic(self) -> dict[str, object]:
        with self._diagnostic_lock:
            return dict(self._startup_diagnostic)

    def _record_startup_diagnostic(
        self,
        *,
        event: str,
        stage: str,
        outcome: str,
        category: str = "",
        error_type: str = "",
    ) -> None:
        process = getattr(self, "_process", None)
        process_alive = None
        if process is not None:
            try:
                process_alive = bool(process.is_alive())
            except (AssertionError, ValueError):
                process_alive = False
        diagnostic: dict[str, object] = {
            "event": _diagnostic_token(event),
            "stage": _diagnostic_token(stage),
            "outcome": _diagnostic_token(outcome),
            "elapsedMs": max(0, int((time.monotonic() - self._startup_started_at) * 1000)),
        }
        if category:
            diagnostic["category"] = _diagnostic_token(category)
        if error_type:
            diagnostic["errorType"] = _diagnostic_token(error_type)
        child_pid = getattr(process, "pid", None)
        if isinstance(child_pid, int):
            diagnostic["childPid"] = child_pid
        if process_alive is not None:
            diagnostic["processAlive"] = process_alive
        with self._diagnostic_lock:
            self._startup_diagnostic = diagnostic
            listener = self._diagnostic_listener
        if listener is not None:
            try:
                listener(dict(diagnostic))
            except Exception:  # noqa: BLE001 - diagnostics cannot affect startup.
                pass

    def _record_child_diagnostic(self, payload: object) -> None:
        source = payload if isinstance(payload, dict) else {}
        self._record_startup_diagnostic(
            event=_diagnostic_token(source.get("event"), "embedding_progress"),
            stage=_diagnostic_token(source.get("stage"), "unknown"),
            outcome=_diagnostic_token(source.get("outcome"), "started"),
            category=_diagnostic_token(source.get("category"), "") if source.get("category") else "",
            error_type=(
                _diagnostic_token(source.get("errorType"), "UnknownError")
                if source.get("errorType")
                else ""
            ),
        )

    def wait_ready(
        self,
        *,
        cancel: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> None:
        deadline = time.monotonic() + (
            self.STARTUP_TIMEOUT_SECONDS if timeout is None else max(0.0, timeout)
        )
        while not self._ready:
            if cancel is not None and cancel():
                self._record_startup_diagnostic(
                    event="embedding_startup_cancelled",
                    stage="cancelled",
                    outcome="cancelled",
                    category="startup_cancelled",
                )
                self.close()
                raise RuntimeError("记忆嵌入模型初始化已取消。")
            if self._closed:
                raise RuntimeError("记忆嵌入模型进程已关闭。")
            if not self._process.is_alive():
                self._record_startup_diagnostic(
                    event="embedding_startup_failed",
                    stage="process_exit",
                    outcome="failed",
                    category="process_exited",
                    error_type="ChildProcessExit",
                )
                self.close()
                raise RuntimeError("记忆嵌入模型进程启动失败。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._record_startup_diagnostic(
                    event="embedding_startup_failed",
                    stage="startup_wait",
                    outcome="failed",
                    category="startup_timeout",
                    error_type="TimeoutError",
                )
                self.close()
                raise RuntimeError("记忆嵌入模型初始化超时。")
            if not self._connection.poll(min(0.1, remaining)):
                continue
            try:
                kind, payload = self._connection.recv()
            except (EOFError, OSError) as exc:
                self._record_startup_diagnostic(
                    event="embedding_startup_failed",
                    stage="startup_wait",
                    outcome="failed",
                    category="connection_interrupted",
                    error_type=exc.__class__.__name__,
                )
                self.close()
                raise RuntimeError("记忆嵌入模型进程连接中断。") from exc
            if kind == "progress":
                self._record_child_diagnostic(payload)
                continue
            if kind == "ready":
                self._ready = True
                self._record_startup_diagnostic(
                    event="embedding_ready",
                    stage="ready",
                    outcome="completed",
                )
                return
            if kind == "startup_error":
                self._record_child_diagnostic(payload)
            else:
                self._record_startup_diagnostic(
                    event="embedding_startup_failed",
                    stage="startup_protocol",
                    outcome="failed",
                    category="invalid_response",
                    error_type="ProtocolError",
                )
            self.close()
            raise RuntimeError("记忆嵌入模型初始化失败。")

    def embed(self, text: object, memory_action: str | None = None) -> list[float]:
        result = self._request("embed", str(text), memory_action)
        if not isinstance(result, list):
            raise RuntimeError("记忆嵌入模型响应无效。")
        return [float(item) for item in result]

    def embed_batch(
        self,
        texts: Iterable[object],
        memory_action: str = "add",
    ) -> list[list[float]]:
        result = self._request("embed_batch", [str(item) for item in texts], memory_action)
        if not isinstance(result, list):
            raise RuntimeError("记忆嵌入模型响应无效。")
        return [[float(value) for value in row] for row in result]

    def _request(self, kind: str, payload: object, memory_action: str | None) -> object:
        with self._lock:
            self.wait_ready()
            try:
                self._connection.send((kind, payload, memory_action))
            except (BrokenPipeError, EOFError, OSError) as exc:
                self.close()
                raise RuntimeError("记忆嵌入模型进程连接中断。") from exc
            if not self._connection.poll(self.REQUEST_TIMEOUT_SECONDS):
                self.close()
                raise RuntimeError("记忆嵌入模型请求超时。")
            try:
                response_kind, result = self._connection.recv()
            except (EOFError, OSError) as exc:
                self.close()
                raise RuntimeError("记忆嵌入模型进程连接中断。") from exc
            if response_kind != "result":
                raise RuntimeError("记忆嵌入模型请求失败。")
            return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = getattr(self, "_connection", None)
        process = getattr(self, "_process", None)
        if connection is not None:
            try:
                connection.send(("close", None, None))
            except (BrokenPipeError, EOFError, OSError):
                pass
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.25)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.25)
            try:
                process.close()
            except ValueError:
                pass


def _send_process_isolated_mem0_progress(
    *,
    event: str,
    stage: str,
    outcome: str,
    category: str = "",
    error_type: str = "",
) -> None:
    """Send one content-free startup stage from the mem0 child process."""

    global _MEM0_CHILD_STAGE, _MEM0_CHILD_OUTCOME
    _MEM0_CHILD_STAGE = _diagnostic_token(stage)
    _MEM0_CHILD_OUTCOME = _diagnostic_token(outcome)
    connection = _MEM0_CHILD_CONNECTION
    if connection is None:
        return
    payload: dict[str, object] = {
        "event": _diagnostic_token(event),
        "stage": _MEM0_CHILD_STAGE,
        "outcome": _MEM0_CHILD_OUTCOME,
    }
    if category:
        payload["category"] = _diagnostic_token(category, "load_failed")
    if error_type:
        payload["errorType"] = _diagnostic_token(error_type, "UnknownError")
    try:
        connection.send(("progress", payload))
    except (BrokenPipeError, EOFError, OSError):
        pass


def _create_process_isolated_mem0_component(
    *,
    event_prefix: str,
    stage: str,
    factory: Callable[[], Any],
) -> Any:
    """Create one mem0 dependency while preserving its exact startup stage."""

    _send_process_isolated_mem0_progress(
        event=f"{event_prefix}_started",
        stage=stage,
        outcome="started",
    )
    try:
        component = factory()
    except BaseException as exc:
        _send_process_isolated_mem0_progress(
            event=f"{event_prefix}_failed",
            stage=stage,
            outcome="failed",
            category=_classify_memory_load_exception(exc, stage=stage),
            error_type=exc.__class__.__name__,
        )
        raise
    _send_process_isolated_mem0_progress(
        event=f"{event_prefix}_completed",
        stage=stage,
        outcome="completed",
    )
    return component


def _dispatch_process_isolated_mem0_request(
    memory: Any,
    method: object,
    args: object,
    kwargs: object,
) -> object:
    method_name = str(method)
    if not isinstance(args, (list, tuple)) or not isinstance(kwargs, dict):
        raise ValueError("invalid mem0 request payload")
    if method_name in {"get_all", "search", "add", "get", "update", "delete"}:
        target = getattr(memory, method_name)
        return target(*args, **kwargs)
    if method_name == "reset_curation_cache":
        return _reset_mem0_curation_cache(
            memory,
            scope_id=str(kwargs.get("scope_id") or DEFAULT_MEMORY_SCOPE),
            memory_ids=kwargs.get("memory_ids"),
        )
    if method_name == "reload_llm":
        if len(args) != 1 or not isinstance(args[0], dict):
            raise ValueError("invalid mem0 LLM configuration")
        _reload_process_isolated_mem0_llm(memory, args[0])
        return None
    raise ValueError("unsupported mem0 request")


def _reload_process_isolated_mem0_llm(memory: Any, llm_section: dict[str, Any]) -> None:
    from mem0.llms.configs import LlmConfig
    from mem0.utils.factory import LlmFactory

    provider = str(llm_section.get("provider") or "openai")
    config_values = dict(llm_section.get("config") or {})
    llm_config = LlmConfig(provider=provider, config=config_values)
    llm = LlmFactory.create(provider, config_values)
    previous = getattr(memory, "llm", None)
    memory.config.llm = llm_config
    memory.llm = llm
    previous_close = getattr(previous, "close", None)
    if callable(previous_close):
        try:
            previous_close()
        except Exception:  # noqa: BLE001 - the replacement is already active.
            pass


def _run_process_isolated_mem0_client(
    connection: Any,
    config: dict[str, Any],
) -> None:
    """Own the complete local Memory runtime in one disposable process."""

    global _MEM0_CHILD_CONNECTION, _MEM0_CHILD_STAGE, _MEM0_CHILD_OUTCOME
    _MEM0_CHILD_CONNECTION = connection
    _MEM0_CHILD_STAGE = "mem0_import"
    _MEM0_CHILD_OUTCOME = "started"
    memory: Any | None = None
    startup_complete = False
    try:
        # Core stdout is the framed protocol transport.  A spawned dependency
        # must never inherit it as an unframed print/log destination.
        try:
            if (
                multiprocessing.parent_process() is not None
                and sys.stdout is not None
                and sys.stderr is not None
            ):
                os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        except (AttributeError, OSError, ValueError):
            pass
        _send_process_isolated_mem0_progress(
            event="mem0_import_started",
            stage="mem0_import",
            outcome="started",
        )
        install_mem0_vendor()
        try:
            (
                Memory,
                mem0_memory_main,
                EmbedderFactory,
                LlmFactory,
                VectorStoreFactory,
            ) = _import_process_isolated_mem0_dependencies()
        except BaseException:
            _MEM0_CHILD_STAGE = "mem0_import"
            _MEM0_CHILD_OUTCOME = "failed"
            raise

        _send_process_isolated_mem0_progress(
            event="mem0_import_completed",
            stage="mem0_import",
            outcome="completed",
        )
        EmbedderFactory.provider_to_class["fastembed"] = (
            "app.agent.memory._ProcessLocalDiagnosticFastEmbedEmbedding"
        )
        original_vector_create_descriptor = VectorStoreFactory.__dict__["create"]
        original_vector_create = VectorStoreFactory.create
        original_llm_create_descriptor = LlmFactory.__dict__["create"]
        original_llm_create = LlmFactory.create
        original_sqlite_manager = mem0_memory_main.SQLiteManager

        def create_vector_store(
            _factory: type[Any],
            provider_name: str,
            vector_config: Any,
        ) -> Any:
            return _create_process_isolated_mem0_component(
                event_prefix="qdrant_create",
                stage="qdrant_create",
                factory=lambda: original_vector_create(provider_name, vector_config),
            )

        def create_llm(
            _factory: type[Any],
            provider_name: str,
            llm_config: Any = None,
            **kwargs: Any,
        ) -> Any:
            return _create_process_isolated_mem0_component(
                event_prefix="llm_create",
                stage="llm_create",
                factory=lambda: original_llm_create(provider_name, llm_config, **kwargs),
            )

        def create_sqlite_manager(*args: Any, **kwargs: Any) -> Any:
            return _create_process_isolated_mem0_component(
                event_prefix="sqlite_create",
                stage="sqlite_create",
                factory=lambda: original_sqlite_manager(*args, **kwargs),
            )

        VectorStoreFactory.create = classmethod(create_vector_store)
        LlmFactory.create = classmethod(create_llm)
        mem0_memory_main.SQLiteManager = create_sqlite_manager
        _send_process_isolated_mem0_progress(
            event="mem0_client_create_started",
            stage="mem0_client_create",
            outcome="started",
        )
        try:
            memory = Memory.from_config(config)
        finally:
            VectorStoreFactory.create = original_vector_create_descriptor
            LlmFactory.create = original_llm_create_descriptor
            mem0_memory_main.SQLiteManager = original_sqlite_manager
        _send_process_isolated_mem0_progress(
            event="mem0_client_create_completed",
            stage="mem0_client_create",
            outcome="completed",
        )
        connection.send(("ready", None))
        startup_complete = True
        while True:
            message = connection.recv()
            if not isinstance(message, tuple) or not message:
                raise ValueError("invalid mem0 process protocol")
            if message[0] == "close":
                return
            if len(message) != 4 or message[0] != "request":
                raise ValueError("invalid mem0 process request")
            _, method, args, kwargs = message
            try:
                result = _dispatch_process_isolated_mem0_request(
                    memory,
                    method,
                    args,
                    kwargs,
                )
                connection.send(("result", result))
            except Exception as exc:  # noqa: BLE001 - return a bounded RPC error.
                connection.send(
                    (
                        "error",
                        {
                            "errorType": _diagnostic_token(
                                exc.__class__.__name__,
                                "RemoteError",
                            ),
                            "message": (str(exc).strip() or "长期记忆请求失败。")[:2000],
                        },
                    )
                )
    except (EOFError, BrokenPipeError):
        return
    except BaseException as exc:
        if not startup_complete:
            stage = _MEM0_CHILD_STAGE
            category = _classify_memory_load_exception(exc, stage=stage)
            try:
                connection.send(
                    (
                        "startup_error",
                        {
                            "event": "mem0_startup_failed",
                            "stage": _diagnostic_token(stage),
                            "outcome": "failed",
                            "category": _diagnostic_token(category, "load_failed"),
                            "errorType": _diagnostic_token(
                                exc.__class__.__name__,
                                "UnknownError",
                            ),
                        },
                    )
                )
            except Exception:
                pass
    finally:
        _MEM0_CHILD_CONNECTION = None
        if memory is not None:
            _close_memory_client(memory)
        try:
            connection.close()
        except OSError:
            pass


def _import_process_isolated_mem0_dependencies() -> tuple[Any, Any, Any, Any, Any]:
    """Import mem0 while exposing bounded dependency checkpoints to startup logs."""

    original_import = builtins.__import__
    active: set[str] = set()
    observed: set[str] = set()

    def diagnostic_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> Any:
        import_name = str(name)
        label = next(
            (
                candidate
                for prefix, candidate in _MEM0_IMPORT_DIAGNOSTIC_PREFIXES
                if import_name == prefix or import_name.startswith(f"{prefix}.")
            ),
            None,
        )
        report = bool(label and label not in active and label not in observed)
        stage = f"mem0_import_{label}" if label else "mem0_import"
        if report:
            active.add(label)
            _send_process_isolated_mem0_progress(
                event=f"mem0_import_{label}_started",
                stage=stage,
                outcome="started",
            )
        try:
            module = original_import(name, globals, locals, fromlist, level)
        except BaseException as exc:
            if report:
                _send_process_isolated_mem0_progress(
                    event=f"mem0_import_{label}_failed",
                    stage=stage,
                    outcome="failed",
                    category="dependency_import_failed",
                    error_type=exc.__class__.__name__,
                )
            raise
        else:
            if report:
                _send_process_isolated_mem0_progress(
                    event=f"mem0_import_{label}_completed",
                    stage=stage,
                    outcome="completed",
                )
            return module
        finally:
            if report:
                active.discard(label)
                observed.add(label)

    builtins.__import__ = diagnostic_import
    try:
        _install_disabled_mem0_telemetry_module()
        _install_disabled_qdrant_grpc_module()
        _install_synchronous_qdrant_client_facade()
        from mem0 import Memory
        import mem0.memory.main as mem0_memory_main
        from mem0.utils.factory import EmbedderFactory, LlmFactory, VectorStoreFactory

        return Memory, mem0_memory_main, EmbedderFactory, LlmFactory, VectorStoreFactory
    finally:
        builtins.__import__ = original_import


def _install_disabled_mem0_telemetry_module() -> None:
    """Avoid importing the full PostHog SDK when mem0 telemetry is disabled."""

    enabled = (os.environ.get("MEM0_TELEMETRY") or "").strip().lower()
    if enabled in {"true", "1", "yes"} or "posthog" in sys.modules:
        return

    class DisabledPosthog:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("mem0 telemetry is disabled")

    module = ModuleType("posthog")
    module.Posthog = DisabledPosthog
    sys.modules["posthog"] = module


def _install_synchronous_qdrant_client_facade() -> None:
    """Load only Qdrant's synchronous client used by the local Memory store."""

    if "qdrant_client" in sys.modules:
        return
    spec = importlib.util.find_spec("qdrant_client")
    if spec is None or spec.submodule_search_locations is None:
        return

    package_locations = list(spec.submodule_search_locations)
    facade = ModuleType("qdrant_client")
    facade.__file__ = spec.origin
    facade.__package__ = "qdrant_client"
    facade.__path__ = package_locations
    facade.__spec__ = importlib.util.spec_from_loader(
        "qdrant_client",
        loader=None,
        is_package=True,
    )
    if facade.__spec__ is not None:
        facade.__spec__.submodule_search_locations = package_locations

    def load_attribute(name: str) -> Any:
        if name != "QdrantClient":
            raise AttributeError(name)
        synchronous = importlib.import_module("qdrant_client.qdrant_client")
        facade.QdrantClient = synchronous.QdrantClient
        return synchronous.QdrantClient

    facade.__getattr__ = load_attribute
    sys.modules["qdrant_client"] = facade


class _DisabledGrpcModule(ModuleType):
    """Provide import-only gRPC symbols for Qdrant's unused remote code path."""

    def __getattr__(self, name: str) -> Any:
        value = type(f"DisabledGrpc_{_diagnostic_token(name)}", (), {})
        setattr(self, name, value)
        return value


def _install_disabled_qdrant_grpc_module() -> None:
    """Skip grpcio native startup because Sakura always uses local Qdrant."""

    if "grpc" in sys.modules:
        return
    grpc = _DisabledGrpcModule("grpc")
    grpc_aio = _DisabledGrpcModule("grpc.aio")
    grpc.aio = grpc_aio
    sys.modules["grpc"] = grpc
    sys.modules["grpc.aio"] = grpc_aio


def _run_process_isolated_fastembed_embedding(
    connection: Any,
    config: dict[str, object],
) -> None:
    stage = "dependency_import"
    try:
        connection.send(
            (
                "progress",
                {
                    "event": "embedding_dependency_import_started",
                    "stage": stage,
                    "outcome": "started",
                },
            )
        )
        from fastembed import TextEmbedding

        connection.send(
            (
                "progress",
                {
                    "event": "embedding_dependency_import_completed",
                    "stage": stage,
                    "outcome": "completed",
                },
            )
        )
        stage = "model_load"
        connection.send(
            (
                "progress",
                {
                    "event": "embedding_model_load_started",
                    "stage": stage,
                    "outcome": "started",
                },
            )
        )
        model_kwargs = dict(config.get("model_kwargs") or {})
        model_kwargs.setdefault("local_files_only", True)
        model_kwargs.setdefault("providers", ["CPUExecutionProvider"])
        model = TextEmbedding(
            model_name=str(config["model"]),
            **model_kwargs,
        )
        if model.embedding_size != DEFAULT_EMBEDDING_DIMS:
            raise ValueError("记忆 ONNX 模型维度不匹配。")
        stage = "ready"
        connection.send(("ready", {"stage": stage}))
        while True:
            kind, payload, _memory_action = connection.recv()
            if kind == "close":
                return
            try:
                if kind == "embed":
                    vector = next(model.embed([str(payload).replace("\n", " ")], batch_size=1))
                    result = [float(value) for value in vector]
                elif kind == "embed_batch" and isinstance(payload, list):
                    result = [
                        [float(value) for value in vector]
                        for vector in model.embed(
                            [str(item).replace("\n", " ") for item in payload]
                        )
                    ]
                else:
                    raise ValueError("invalid embedding request")
                connection.send(("result", result))
            except Exception:
                connection.send(("error", None))
    except EOFError:
        return
    except BaseException as exc:
        if stage == "dependency_import":
            category = "dependency_import_failed"
        elif isinstance(exc, MemoryError):
            category = "resource_exhausted"
        elif isinstance(exc, (OSError, ValueError)):
            category = "model_files_unavailable"
        else:
            category = "model_load_failed"
        try:
            connection.send(
                (
                    "startup_error",
                    {
                        "event": "embedding_startup_failed",
                        "stage": stage,
                        "outcome": "failed",
                        "category": category,
                        "errorType": exc.__class__.__name__,
                    },
                )
            )
        except Exception:
            pass
    finally:
        try:
            connection.close()
        except OSError:
            pass


def install_mem0_vendor() -> Path:
    """优先把仓库内置的 mem0 放到导入路径最前面。"""

    vendor_path = str(MEM0_VENDOR_ROOT)
    if MEM0_VENDOR_ROOT.exists():
        if vendor_path in sys.path:
            sys.path.remove(vendor_path)
        sys.path.insert(0, vendor_path)
    return MEM0_VENDOR_ROOT


install_mem0_vendor()


@dataclass(frozen=True)
class MemoryRecord:
    """Sakura 业务层统一记忆记录，屏蔽 mem0 原始字段差异。"""

    id: str
    content: str
    layer: str = DEFAULT_MEMORY_LAYER
    category: str = ""
    importance: float = DEFAULT_MEMORY_IMPORTANCE
    confidence: float = DEFAULT_MEMORY_CONFIDENCE
    source: str = DEFAULT_MEMORY_SOURCE
    scope: str = DEFAULT_MEMORY_SCOPE
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        for key in (
            "layer",
            "category",
            "importance",
            "confidence",
            "source",
            "scope",
            "created_at",
            "updated_at",
            "last_accessed_at",
        ):
            metadata[key] = getattr(self, key)
        return {
            "id": self.id,
            "content": self.content,
            "memory": self.content,
            "layer": self.layer,
            "category": self.category,
            "importance": self.importance,
            "confidence": self.confidence,
            "source": self.source,
            "scope": self.scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "score": self.score,
            "metadata": metadata,
        }


@dataclass(frozen=True)
class MemorySearchResult:
    """Sakura 记忆检索结果。工具层仍会转成 dict 返回。"""

    agent_id: str
    query: str
    memories: list[MemoryRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "query": self.query,
            "count": len(self.memories),
            "memories": [memory.to_dict() for memory in self.memories],
        }


@dataclass
class MemoryCurationCounts:
    """mem0 写入结果的轻量统计。"""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    ignored: int = 0
    total: int = 0
    returned: int = 0
    unclassified: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)


class MemoryModelImportError(RuntimeError):
    """记忆嵌入模型归档包格式错误或导入失败。"""


class MemoryModelTaskCancelled(RuntimeError):
    """用户或当前 Core generation 取消了模型导入/下载。"""


@dataclass(frozen=True)
class EmbeddingModelImportResult:
    """记忆嵌入模型导入结果。"""

    model_name: str
    cache_folder: Path
    model_dir: Path
    snapshot_count: int


@dataclass
class MemoryStore:
    """Sakura 对本地内置 mem0 的适配层。"""

    base_dir: Path | None = None
    api_settings: "ApiSettings | None" = None
    scope_id: str = DEFAULT_MEMORY_SCOPE
    memory_client: Any | None = None
    resource_registry: ResourceRegistry | None = None
    _memory: Any | None = field(default=None, init=False, repr=False)
    _loading: bool = field(default=False, init=False, repr=False)
    _loading_started_at: float = field(default=0.0, init=False, repr=False)
    _load_error: str = field(default="", init=False, repr=False)
    _reloading: bool = field(default=False, init=False, repr=False)
    _reload_error: str = field(default="", init=False, repr=False)
    _reload_generation: int = field(default=0, init=False, repr=False)
    _status: str = field(default="idle", init=False, repr=False)
    _status_message: str = field(default="", init=False, repr=False)
    _status_listeners: list[Callable[[str, str], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _load_cancel: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _active_embedder: Any | None = field(default=None, init=False, repr=False)
    _load_diagnostic: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    _diagnostic_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _thread_group: ThreadGroupResource = field(init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_dir = _resolve_base_dir(self.base_dir)
        self.scope_id = _normalize_scope_id(self.scope_id)
        self.resource_registry = self.resource_registry or ResourceRegistry()
        self._thread_group = self.resource_registry.track_thread_group(
            cancel=self._cancel_memory_load,
            label="memory_store",
            shutdown_order=1000,
        )
        if self.memory_client is not None:
            self._memory = self.memory_client
            self._status = "ready"
            self._status_message = "长期记忆系统已就绪。"
        self._record_load_diagnostic(
            event="memory_store_created",
            stage="owner_create",
            outcome="completed",
            status=self._status,
            model_cached=_embedding_model_cached(DEFAULT_EMBEDDING_MODEL, self.base_dir),
        )

    def load_diagnostic(self) -> dict[str, object]:
        """Return the latest bounded startup diagnostic without private errors."""

        with self._diagnostic_lock:
            return dict(self._load_diagnostic)

    def _record_load_diagnostic(
        self,
        *,
        event: str,
        stage: str,
        outcome: str,
        status: str = "",
        category: str = "",
        error_type: str = "",
        elapsed_ms: int | None = None,
        wait: bool | None = None,
        model_cached: bool | None = None,
        child_pid: int | None = None,
        process_alive: bool | None = None,
        component: str = "memory_store",
        update_snapshot: bool = True,
    ) -> None:
        diagnostic: dict[str, object] = {
            "event": _diagnostic_token(event),
            "stage": _diagnostic_token(stage),
            "outcome": _diagnostic_token(outcome),
        }
        if status:
            diagnostic["status"] = _diagnostic_token(status)
        if category:
            diagnostic["category"] = _diagnostic_token(category)
        if error_type:
            diagnostic["errorType"] = _diagnostic_token(error_type)
        if elapsed_ms is not None:
            diagnostic["elapsedMs"] = max(0, int(elapsed_ms))
        if child_pid is not None:
            diagnostic["childPid"] = max(0, int(child_pid))
        if process_alive is not None:
            diagnostic["processAlive"] = bool(process_alive)
        if update_snapshot:
            with self._diagnostic_lock:
                self._load_diagnostic = diagnostic
        append_memory_initialization_diagnostic(
            self.base_dir,
            component=component,
            event=str(diagnostic["event"]),
            stage=str(diagnostic["stage"]),
            outcome=str(diagnostic["outcome"]),
            status=str(diagnostic.get("status") or ""),
            category=str(diagnostic.get("category") or ""),
            error_type=str(diagnostic.get("errorType") or ""),
            elapsed_ms=(int(diagnostic["elapsedMs"]) if "elapsedMs" in diagnostic else None),
            wait=wait,
            model_cached=model_cached,
            child_pid=(int(diagnostic["childPid"]) if "childPid" in diagnostic else None),
            process_alive=(
                bool(diagnostic["processAlive"]) if "processAlive" in diagnostic else None
            ),
        )

    def _on_embedder_diagnostic(self, diagnostic: dict[str, object]) -> None:
        self._record_load_diagnostic(
            event=str(diagnostic.get("event") or "embedding_progress"),
            stage=str(diagnostic.get("stage") or "unknown"),
            outcome=str(diagnostic.get("outcome") or "started"),
            category=str(diagnostic.get("category") or ""),
            error_type=str(diagnostic.get("errorType") or ""),
            elapsed_ms=(
                int(diagnostic["elapsedMs"])
                if isinstance(diagnostic.get("elapsedMs"), int)
                else None
            ),
            child_pid=(
                int(diagnostic["childPid"])
                if isinstance(diagnostic.get("childPid"), int)
                else None
            ),
            process_alive=(
                bool(diagnostic["processAlive"])
                if isinstance(diagnostic.get("processAlive"), bool)
                else None
            ),
            component=str(diagnostic.get("component") or "embedding_process"),
        )

    def add_status_listener(
        self,
        listener: Callable[[str, str], None],
        *,
        replay: bool = True,
    ) -> None:
        """监听 mem0 加载状态，供 UI 显示后台初始化进度。"""

        with self._lock:
            if listener not in self._status_listeners:
                self._status_listeners.append(listener)
            status = self._status
            message = self._status_message
        if replay and message:
            self._notify_status_listener(listener, status, message)

    def remove_status_listener(self, listener: Callable[[str, str], None]) -> None:
        with self._lock:
            if listener in self._status_listeners:
                self._status_listeners.remove(listener)

    def set_scope(self, scope_id: str) -> None:
        """切换角色后更新 mem0 user_id 作用域。"""

        self.scope_id = _normalize_scope_id(scope_id)

    def scoped(self, scope_id: str) -> "ScopedMemoryStore":
        """创建固定角色 scope 的轻量视图，供后台任务隔离角色切换。"""

        return ScopedMemoryStore(self, scope_id)

    def set_api_settings(self, api_settings: "ApiSettings") -> None:
        """API 设置变更后重置 mem0，下次使用新配置重新初始化。"""

        if self.api_settings == api_settings:
            return
        self.api_settings = api_settings
        self.reset_runtime()

    def reset_runtime(self) -> None:
        old_memory: Any | None = None
        with self._lock:
            if self._memory is not None and self._memory is not self.memory_client:
                old_memory = self._memory
            self._memory = self.memory_client
            self._loading = False
            self._loading_started_at = 0.0
            self._load_error = ""
            self._reloading = False
            self._reload_error = ""
            self._reload_generation += 1
            if self._memory is not None:
                self._status = "ready"
                self._status_message = "长期记忆系统已就绪。"
            else:
                self._status = "idle"
                self._status_message = ""
        _close_memory_client(old_memory)

    def close(self) -> None:
        """关闭长期记忆运行时并阻止迟到的后台加载结果重新写回。"""
        self._record_load_diagnostic(
            event="memory_store_close_started",
            stage="shutdown",
            outcome="started",
            status="stopped",
        )
        self._cancel_memory_load()
        old_memory: Any | None = None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._reload_generation += 1
            old_memory = self._memory
            self._memory = None
            self._loading = False
            self._loading_started_at = 0.0
            self._load_error = ""
            self._reloading = False
            self._reload_error = ""
            self._status = "stopped"
            self._status_message = "长期记忆系统已关闭。"
        # The active Memory child is closed or terminated by cancellation.
        # Generation invalidation prevents a late loader result from being
        # published, so the Core never waits out a cold import on shutdown.
        self._thread_group.stop(0)
        _close_memory_client(old_memory)
        self._record_load_diagnostic(
            event="memory_store_close_completed",
            stage="shutdown",
            outcome="completed",
            status="stopped",
        )

    def _register_active_embedder(self, embedder: Any) -> None:
        with self._lock:
            self._active_embedder = embedder
        set_listener = getattr(embedder, "set_diagnostic_listener", None)
        if callable(set_listener):
            set_listener(self._on_embedder_diagnostic)

    def _cancel_memory_load(self) -> None:
        self._load_cancel.set()
        with self._lock:
            embedder = self._active_embedder
        close = getattr(embedder, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                logger.debug("取消记忆嵌入模型初始化失败", exc_info=True)

    def is_ready(self) -> bool:
        """返回长期记忆运行时是否已经可直接使用。"""

        with self._lock:
            return self._memory is not None

    def needs_embedding_model_download(self) -> bool:
        """返回首次初始化是否可能需要下载本地嵌入模型。"""

        return not _embedding_model_cached(DEFAULT_EMBEDDING_MODEL, self.base_dir)

    def embedding_model_endpoint(self) -> str:
        """返回当前嵌入模型下载端点，便于 UI 提示用户。"""

        return (os.environ.get("HF_ENDPOINT") or DEFAULT_HUGGINGFACE_ENDPOINT).strip()

    def import_embedding_model_archive(
        self,
        path: Path,
        *,
        progress: Callable[[str, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> EmbeddingModelImportResult:
        """导入离线嵌入模型 ZIP，并重置长期记忆运行时以复用新缓存。"""

        result = import_embedding_model_archive(
            path,
            self.base_dir,
            progress=progress,
            cancel=cancel,
        )
        if not self.is_ready():
            self.reset_runtime()
            self.preload(wait=False)
        return result

    def download_embedding_model(
        self,
        *,
        progress: Callable[[str, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> EmbeddingModelImportResult:
        """在线安装记忆嵌入模型，并重置长期记忆运行时以复用新缓存。"""

        result = download_embedding_model(self.base_dir, progress=progress, cancel=cancel)
        if not self.is_ready():
            self.reset_runtime()
            self.preload(wait=False)
        return result

    def preload(self, *, wait: bool = False) -> None:
        """提前启动 mem0 加载，避免首次打开设置或聊天时才初始化。"""

        self._record_load_diagnostic(
            event="preload_requested",
            stage="preload",
            outcome="started",
            wait=wait,
            model_cached=_embedding_model_cached(DEFAULT_EMBEDDING_MODEL, self.base_dir),
        )
        if wait:
            started_at = time.monotonic()
            try:
                self._get_memory(wait=True)
            except Exception as exc:
                diagnostic = self.load_diagnostic()
                self._record_load_diagnostic(
                    event="preload_returned",
                    stage=str(diagnostic.get("stage") or "preload"),
                    outcome="failed",
                    category=str(diagnostic.get("category") or "load_failed"),
                    error_type=str(diagnostic.get("errorType") or exc.__class__.__name__),
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                    wait=True,
                )
                raise
            self._record_load_diagnostic(
                event="preload_returned",
                stage="ready",
                outcome="completed",
                status="ready",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                wait=True,
            )
            return
        skip_category = ""
        with self._lock:
            if self._closed:
                skip_category = "store_closed"
                status_event = None
            elif self._memory is not None:
                skip_category = "already_ready"
                status_event = None
            elif self._loading:
                skip_category = "already_loading"
                status_event = None
            else:
                if self._load_error:
                    self._load_error = ""
                status_event = self._start_loading_locked()
        self._notify_status_event(status_event)
        self._record_load_diagnostic(
            event="preload_returned",
            stage="preload",
            outcome="skipped" if skip_category else "scheduled",
            category=skip_category,
            wait=False,
            update_snapshot=False,
        )

    def reload_api_settings(self, api_settings: "ApiSettings", *, wait: bool = False) -> None:
        """后台使用新 API 配置重建 mem0，成功前保留旧实例继续服务。"""

        with self._lock:
            if self._closed:
                return
            if self.api_settings == api_settings and self._memory is not None and not self._reload_error:
                return
            self.api_settings = api_settings
            self._reload_generation += 1
            generation = self._reload_generation
            self._reload_error = ""
            existing_memory = self._memory
            reload_llm_only = self._supports_memory_llm_reload(existing_memory)

        if wait:
            try:
                self._publish_status("reloading", "长期记忆系统正在根据新的 API 设置重载。")
                if reload_llm_only:
                    llm_config, llm = self._create_memory_llm(
                        api_settings,
                        existing_memory,
                    )
                    memory = existing_memory
                else:
                    llm_config = None
                    llm = None
                    memory = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 后台重载失败")
                current_generation = False
                with self._lock:
                    if generation == self._reload_generation:
                        self._reload_error = str(exc)
                        current_generation = True
                if current_generation:
                    self._publish_status("failed", f"长期记忆系统重载失败：{exc}")
                return
            applied = False
            with self._lock:
                if generation == self._reload_generation:
                    if reload_llm_only and self._memory is not existing_memory:
                        return
                    if reload_llm_only:
                        self._apply_memory_llm(memory, llm_config, llm)
                    else:
                        self._memory = memory
                    self._load_error = ""
                    self._reload_error = ""
                    self._loading = False
                    self._reloading = False
                    applied = True
            if applied:
                self._publish_status("ready", "长期记忆系统已就绪。")
            return

        with self._lock:
            self._reloading = True
            status_event = self._set_status_locked(
                "reloading",
                "长期记忆系统正在根据新的 API 设置重载。",
            )
        _prepare_memory_background_imports()
        self._notify_status_event(status_event)

        def reload() -> None:
            try:
                if reload_llm_only:
                    llm_config, llm = self._create_memory_llm(
                        api_settings,
                        existing_memory,
                    )
                    memory = existing_memory
                else:
                    llm_config = None
                    llm = None
                    memory = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 后台重载失败")
                current_generation = False
                with self._lock:
                    if generation == self._reload_generation:
                        self._reload_error = str(exc)
                        self._reloading = False
                        current_generation = True
                if current_generation:
                    self._publish_status("failed", f"长期记忆系统重载失败：{exc}")
                return
            applied = False
            should_apply = False
            stale_memory: Any | None = None
            with self._lock:
                if generation == self._reload_generation and not (
                    reload_llm_only and self._memory is not existing_memory
                ):
                    should_apply = True
                elif not reload_llm_only:
                    stale_memory = memory
            if not should_apply:
                _close_memory_client(stale_memory)
                return
            with self._lock:
                if reload_llm_only:
                    self._apply_memory_llm(memory, llm_config, llm)
                else:
                    self._memory = memory
                self._load_error = ""
                self._reload_error = ""
                self._loading = False
                self._reloading = False
                applied = True
            if applied:
                self._publish_status("ready", "长期记忆系统已就绪。")

        thread = self._thread_group.spawn(
            reload,
            name="sakura-mem0-reloader",
            daemon=True,
        )
        if thread is None:
            with self._lock:
                self._reloading = False

    def build_mem0_config(self, api_settings: "ApiSettings | None" = None) -> dict[str, Any]:
        """生成 mem0 配置：本地 Qdrant + Sakura 当前 OpenAI-compatible LLM。"""

        memory_dir = StoragePaths(self.base_dir).memory_dir
        qdrant_path = memory_dir / "qdrant"
        settings = self.api_settings if api_settings is None else api_settings

        llm_config: dict[str, Any] = {
            "provider": "openai",
            "config": {
                "model": "gpt-4.1-mini",
                "temperature": 0.1,
                "max_tokens": 2000,
            },
        }
        if settings is not None:
            llm_config["config"]["model"] = settings.model or "gpt-4.1-mini"
            if settings.api_key:
                llm_config["config"]["api_key"] = settings.api_key
            if settings.base_url:
                llm_config["config"]["openai_base_url"] = settings.base_url.rstrip("/")

        return {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": qdrant_path.as_posix(),
                    "collection_name": DEFAULT_COLLECTION_NAME,
                    "embedding_model_dims": DEFAULT_EMBEDDING_DIMS,
                    "on_disk": True,
                },
            },
            "llm": llm_config,
            "embedder": {
                "provider": "fastembed",
                "config": {
                    "model": DEFAULT_EMBEDDING_MODEL,
                    "embedding_dims": DEFAULT_EMBEDDING_DIMS,
                    "model_kwargs": _local_embedding_model_kwargs(DEFAULT_EMBEDDING_MODEL, self.base_dir),
                },
            },
            "history_db_path": str(memory_dir / "mem0_history.db"),
            "custom_instructions": DEFAULT_MEMORY_LANGUAGE_INSTRUCTIONS,
        }

    def summary(self, limit: int = 12) -> str:
        mem = self._get_memory(wait=False)
        core_profile = self.core_profile()
        if mem is None:
            if core_profile is not None:
                return _format_memory_context(
                    core_profile=core_profile,
                    semantic=[],
                    episodic=[],
                    procedural=[],
                    session=[],
                    status="长期记忆系统正在初始化。",
                )
            return "长期记忆系统正在初始化。"
        raw = mem.get_all(filters={"user_id": self.scope_id}, top_k=limit)
        memories = _normalize_memory_results(raw, default_scope=self.scope_id)
        if core_profile is not None:
            memories.insert(0, core_profile)
        if not memories:
            return "暂无长期记忆。"
        lines = ["长期记忆："]
        for memory in memories:
            memory_id = str(memory.get("id", ""))
            content = str(memory.get("content", ""))
            layer = str(memory.get("layer") or DEFAULT_MEMORY_LAYER)
            lines.append(f"- [{memory_id}] {_memory_layer_label(layer)}：{content}")
        return "\n".join(lines)

    def list_memories(self, *, limit: int | None = DEFAULT_MEMORY_LIMIT) -> list[dict[str, Any]]:
        mem = self._get_memory()
        top_k = DEFAULT_MEMORY_LIMIT if limit is None else limit
        while True:
            raw = mem.get_all(filters={"user_id": self.scope_id}, top_k=top_k)
            memories = _normalize_memory_results(raw, default_scope=self.scope_id)
            if limit is not None or len(memories) < top_k:
                break
            top_k *= 2
        core_profile = self.core_profile()
        if core_profile is not None:
            memories.insert(0, core_profile)
        return memories if limit is None else memories[:limit]

    def core_profile(self) -> dict[str, Any] | None:
        """读取当前角色的常驻档案块；缺失时返回 None。"""

        profiles = self._load_core_profiles()
        raw = profiles.get(self.scope_id)
        if not isinstance(raw, dict):
            return None
        record = _normalize_memory_record(raw, default_scope=self.scope_id)
        if record is None:
            return None
        record["id"] = _core_profile_id(self.scope_id)
        record["layer"] = MEMORY_LAYER_CORE_PROFILE
        record["metadata"]["layer"] = MEMORY_LAYER_CORE_PROFILE
        return record

    def set_core_profile(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写入当前角色的常驻档案块，不进入向量库。"""

        text = content.strip()
        if not text:
            raise ValueError("常驻档案内容不能为空。")
        profiles = self._load_core_profiles()
        now = _now_iso()
        previous = profiles.get(self.scope_id) if isinstance(profiles.get(self.scope_id), dict) else {}
        previous_metadata = previous.get("metadata") if isinstance(previous, dict) else {}
        merged_metadata = {
            **(previous_metadata if isinstance(previous_metadata, dict) else {}),
            **(metadata or {}),
            "layer": MEMORY_LAYER_CORE_PROFILE,
            "scope": self.scope_id,
            "updated_at": now,
            "created_at": _metadata_text(previous_metadata, "created_at") or now
            if isinstance(previous_metadata, dict)
            else now,
        }
        record = {
            "id": _core_profile_id(self.scope_id),
            "content": text,
            "memory": text,
            "metadata": merged_metadata,
        }
        profiles[self.scope_id] = record
        self._save_core_profiles(profiles)
        normalized = _normalize_memory_record(record, default_scope=self.scope_id)
        return normalized or record

    def delete_core_profile(self) -> dict[str, Any] | None:
        """删除当前角色的常驻档案块。"""

        profiles = self._load_core_profiles()
        previous = profiles.pop(self.scope_id, None)
        self._save_core_profiles(profiles)
        if not isinstance(previous, dict):
            return None
        return _normalize_memory_record(previous, default_scope=self.scope_id)

    def build_memory_context(self, query: str = "", *, mode: str = "chat") -> str:
        """按当前对话场景构建分层记忆注入文本。"""

        query_text = query.strip()
        status = ""
        search = self.search_memory(
            {"query": query_text, "limit": 48},
            wait=False,
        )
        if str(search.get("status") or "") in {"loading", "failed"}:
            status = str(search.get("message") or "")
        memories = [
            memory
            for memory in search.get("memories", [])
            if isinstance(memory, dict)
        ]
        core_profile = self.core_profile()
        if core_profile is None:
            core_candidates = [
                memory
                for memory in memories
                if str(memory.get("layer") or "") == MEMORY_LAYER_CORE_PROFILE
            ]
            core_profile = core_candidates[0] if core_candidates else None

        grouped: dict[str, list[dict[str, Any]]] = {
            layer: [] for layer in VECTOR_MEMORY_LAYERS
        }
        for memory in memories:
            layer = _normalize_memory_layer(memory.get("layer"))
            if layer in grouped:
                grouped[layer].append(memory)

        include_procedural = _query_needs_procedural_memory(query_text, mode)
        include_episodic = _query_needs_episodic_memory(query_text, mode)
        return _format_memory_context(
            core_profile=core_profile,
            semantic=grouped[MEMORY_LAYER_SEMANTIC][:8],
            episodic=grouped[MEMORY_LAYER_EPISODIC][:3] if include_episodic else [],
            procedural=grouped[MEMORY_LAYER_PROCEDURAL][:3] if include_procedural else [],
            session=grouped[MEMORY_LAYER_SESSION][:3],
            status=status,
        )

    def search_memory(
        self,
        arguments: dict[str, Any],
        *,
        wait: bool = True,
    ) -> dict[str, Any]:
        query = _optional_text(arguments, "query") or _optional_text(arguments, "keyword")
        limit = _positive_int(arguments.get("limit") or arguments.get("top_k"), DEFAULT_MEMORY_LIMIT)
        layer_filter = _optional_memory_layer(arguments.get("layer"))
        category_filter = _optional_text(arguments, "category").lower()
        scope = _normalize_scope_id(_optional_text(arguments, "scope") or self.scope_id)
        core_profile = self.core_profile() if scope == self.scope_id else None
        if layer_filter == MEMORY_LAYER_CORE_PROFILE:
            memories = []
            if (
                core_profile is not None
                and _memory_matches_query(core_profile, query)
                and _memory_matches_filters(
                    core_profile,
                    layer=layer_filter,
                    category=category_filter,
                    scope=scope,
                )
            ):
                memories = [core_profile]
            return {
                "agent_id": scope,
                "query": query,
                "count": len(memories),
                "memories": memories,
            }
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
        if mem is None:
            return self._loading_response()
        try:
            raw = (
                mem.get_all(filters={"user_id": scope}, top_k=max(limit, DEFAULT_MEMORY_LIMIT))
                if not query
                else mem.search(query, filters={"user_id": scope}, top_k=max(limit, DEFAULT_MEMORY_LIMIT))
            )
        except Exception as exc:  # noqa: BLE001
            if _is_closed_client_error(exc):
                error = str(exc)
                self._mark_runtime_failed(error)
                return self._failed_response(error)
            raise
        memories = _normalize_memory_results(raw, default_scope=scope)
        if core_profile is not None and _memory_matches_query(core_profile, query):
            memories.insert(0, core_profile)
        memories = [
            memory
            for memory in memories
            if _memory_matches_filters(
                memory,
                layer=layer_filter,
                category=category_filter,
                scope=scope,
            )
        ]
        memories = _rank_memories(memories, query=query)[:limit]
        return {
            "agent_id": scope,
            "query": query,
            "count": len(memories),
            "memories": memories,
        }

    def create_memory(
        self,
        arguments: dict[str, Any],
        *,
        allow_sensitive: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        content = _required_text(arguments, "content")
        if not allow_sensitive and looks_like_sensitive_memory(content):
            raise ValueError("这条内容看起来包含敏感凭据或身份信息，已拒绝写入长期记忆。")
        requested_layer = _normalize_memory_layer(arguments.get("layer"))
        now = _now_iso()
        metadata = _memory_metadata(
            arguments,
            scope_id=self.scope_id,
            existing=None,
            created_at=now,
            updated_at=now,
        )
        if requested_layer == MEMORY_LAYER_CORE_PROFILE:
            memory = self.set_core_profile(content, metadata)
            return {"memory": memory, "ok": True}
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
        if mem is None:
            return self._loading_response()
        raw = mem.add(content, user_id=self.scope_id, metadata=metadata, infer=False)
        memory = _first_memory_result(raw, default_scope=self.scope_id) or {
            "content": content,
            "memory": content,
            "metadata": metadata,
        }
        memory = _normalize_memory_record(memory, default_scope=self.scope_id) or memory
        return {"memory": memory, "ok": True}

    def remember_memory(self, arguments: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        return self.create_memory(arguments, allow_sensitive=False, wait=wait)

    def update_memory(
        self,
        arguments: dict[str, Any],
        *,
        allow_sensitive: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        content = _required_text(arguments, "content")
        if not allow_sensitive and looks_like_sensitive_memory(content):
            raise ValueError("这条内容看起来包含敏感凭据或身份信息，已拒绝写入长期记忆。")
        requested_layer = _normalize_memory_layer(arguments.get("layer"))
        if _is_core_profile_id(memory_id):
            existing = self.core_profile()
            metadata = _memory_metadata(
                arguments,
                scope_id=self.scope_id,
                existing=existing,
                updated_at=_now_iso(),
            )
            memory = self.set_core_profile(content, metadata)
            return {"memory": memory, "ok": True}
        if requested_layer == MEMORY_LAYER_CORE_PROFILE:
            try:
                mem = self._get_memory(wait=wait)
            except RuntimeError as exc:
                if wait:
                    raise
                return self._failed_response(str(exc))
            if mem is None:
                return self._loading_response()
            previous = _require_owned_memory(mem, memory_id, self.scope_id)
            metadata = _memory_metadata(
                arguments,
                scope_id=self.scope_id,
                existing=previous,
                updated_at=_now_iso(),
            )
            memory = self.set_core_profile(content, metadata)
            mem.delete(memory_id)
            self._reset_scope_curation_cache(mem, memory_ids=[memory_id])
            return {"memory": memory, "ok": True, "converted_from": previous}
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
        if mem is None:
            return self._loading_response()
        previous = _require_owned_memory(mem, memory_id, self.scope_id)
        metadata = _memory_metadata(
            arguments,
            scope_id=self.scope_id,
            existing=previous,
            updated_at=_now_iso(),
        )
        raw = mem.update(memory_id, content, metadata=metadata)
        current = _normalize_memory_record(mem.get(memory_id), default_scope=self.scope_id)
        memory = current or _first_memory_result(raw, default_scope=self.scope_id) or {
            "id": memory_id,
            "content": content,
            "memory": content,
            "metadata": metadata,
        }
        memory = _normalize_memory_record(memory, default_scope=self.scope_id) or memory
        return {"memory": memory, "ok": True}

    def delete_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        if _is_core_profile_id(memory_id):
            previous = self.delete_core_profile()
            return {"memory": previous or {"id": memory_id, "content": ""}, "curation_cache_reset": {"messages": 0, "history": 0}}
        mem = self._get_memory()
        previous = _require_owned_memory(mem, memory_id, self.scope_id, allow_missing=True)
        already_missing = _delete_memory_idempotently(mem, memory_id)
        cache_reset = self._reset_scope_curation_cache(mem, memory_ids=[memory_id])
        memory = previous or {"id": memory_id, "content": ""}
        return {"memory": memory, "curation_cache_reset": cache_reset, "already_missing": already_missing}

    def forget_memory(self, arguments: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        if _is_core_profile_id(memory_id):
            previous = self.delete_core_profile()
            forgotten = previous or {"id": memory_id, "content": ""}
            return {"forgotten": forgotten, "memory": forgotten, "curation_cache_reset": {"messages": 0, "history": 0}}
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
        if mem is None:
            return self._loading_response()
        previous = _require_owned_memory(mem, memory_id, self.scope_id, allow_missing=True)
        already_missing = _delete_memory_idempotently(mem, memory_id)
        cache_reset = self._reset_scope_curation_cache(mem, memory_ids=[memory_id])
        forgotten = previous or {"id": memory_id, "content": ""}
        return {
            "forgotten": forgotten,
            "memory": forgotten,
            "curation_cache_reset": cache_reset,
            "already_missing": already_missing,
        }

    def reset_curation_cache(self, *, wait: bool = True) -> dict[str, int]:
        """清理当前角色的 mem0 整理缓存，不影响 Sakura 自己的聊天历史文件。"""

        mem = self._get_memory(wait=wait)
        if mem is None:
            return {"messages": 0, "history": 0}
        return self._reset_scope_curation_cache(mem)

    def add_history_entries(self, entries: list[ChatHistoryEntry]) -> MemoryCurationCounts:
        messages = _entries_for_mem0(entries)
        if not messages:
            return MemoryCurationCounts(total=len(entries))
        mem = self._get_memory()
        raw = mem.add(messages, user_id=self.scope_id, infer=True)
        return _count_mem0_events(raw, total=len(messages))

    def _load_core_profiles(self) -> dict[str, Any]:
        path = StoragePaths(self.base_dir).memory_core_profiles()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("读取常驻档案失败", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def _save_core_profiles(self, profiles: dict[str, Any]) -> None:
        path = StoragePaths(self.base_dir).memory_core_profiles()
        atomic_write_text(
            path,
            json.dumps(profiles, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _reset_scope_curation_cache(
        self,
        mem: Any,
        *,
        memory_ids: Iterable[str] | None = None,
    ) -> dict[str, int]:
        """清理 mem0 内部整理缓存，避免删除长期记忆后旧缓存继续参与抽取。"""

        clean_memory_ids = [
            memory_id
            for memory_id in (str(item).strip() for item in (memory_ids or []))
            if memory_id
        ]
        remote_reset = getattr(mem, "reset_curation_cache", None)
        if callable(remote_reset):
            try:
                return remote_reset(
                    scope_id=self.scope_id,
                    memory_ids=clean_memory_ids,
                )
            except Exception as exc:  # noqa: BLE001 - cache reset is best effort.
                logger.warning("mem0 整理缓存清理失败：%s", exc)
                return {"messages": 0, "history": 0}
        try:
            return _reset_mem0_curation_cache(
                mem,
                scope_id=self.scope_id,
                memory_ids=clean_memory_ids,
            )
        except (sqlite3.Error, RuntimeError) as exc:
            logger.warning("mem0 整理缓存清理失败：%s", exc)
            return {"messages": 0, "history": 0}

    def _get_memory(self, *, wait: bool = True) -> Any | None:
        with self._lock:
            if self._closed:
                if wait:
                    raise RuntimeError("长期记忆系统已关闭。")
                return None
            if self._memory is not None:
                return self._memory
            if self._load_error and not self._loading:
                raise RuntimeError(self._load_error)
            if not self._loading:
                status_event = self._start_loading_locked()
            else:
                status_event = None
            if not wait:
                if status_event is not None:
                    self._notify_status_event(status_event)
                return None

        if status_event is not None:
            self._notify_status_event(status_event)

        while True:
            with self._lock:
                if self._memory is not None:
                    return self._memory
                if not self._loading:
                    break
            time.sleep(0.2)

        with self._lock:
            if self._memory is not None:
                return self._memory
            if self._load_error:
                raise RuntimeError(self._load_error)
        raise RuntimeError("mem0 加载失败")

    def _start_loading_locked(self) -> tuple[list[Callable[[str, str], None]], str, str] | None:
        self._loading = True
        self._loading_started_at = time.time()
        self._load_error = ""
        generation = self._reload_generation
        api_settings = self.api_settings
        report_dependency_loading = not _embedding_model_cached(DEFAULT_EMBEDDING_MODEL, self.base_dir)
        load_started_at = time.monotonic()
        self._record_load_diagnostic(
            event="memory_store_load_started",
            stage="store_load",
            outcome="started",
            status="loading",
            model_cached=not report_dependency_loading,
        )
        status_event = (
            self._set_status_locked(
                "loading",
                "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。",
            )
            if report_dependency_loading
            else None
        )

        def load() -> None:
            try:
                mem = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 初始化失败")
                diagnostic = self.load_diagnostic()
                stage = str(diagnostic.get("stage") or "store_load")
                category = str(diagnostic.get("category") or "")
                error_type = str(diagnostic.get("errorType") or "")
                if diagnostic.get("outcome") != "failed":
                    category = _classify_memory_load_exception(exc, stage=stage)
                    error_type = exc.__class__.__name__
                self._record_load_diagnostic(
                    event="memory_store_load_failed",
                    stage=stage,
                    outcome="failed",
                    status="failed",
                    category=category or "load_failed",
                    error_type=error_type or exc.__class__.__name__,
                    elapsed_ms=int((time.monotonic() - load_started_at) * 1000),
                )
                error_message = _format_memory_load_error(
                    exc,
                    embedding_download=report_dependency_loading,
                )
                with self._lock:
                    if generation == self._reload_generation:
                        self._load_error = error_message
                        self._loading = False
                self._publish_status("failed", error_message)
                return
            stale_mem: Any | None = None
            with self._lock:
                if generation != self._reload_generation or self.api_settings != api_settings or self._closed:
                    self._loading = False
                    stale_mem = mem
                else:
                    self._memory = mem
            if stale_mem is not None:
                _close_memory_client(stale_mem)
                return
            with self._lock:
                self._loading = False
            self._record_load_diagnostic(
                event="memory_store_load_completed",
                stage="ready",
                outcome="completed",
                status="ready",
                elapsed_ms=int((time.monotonic() - load_started_at) * 1000),
            )
            if report_dependency_loading:
                self._publish_status("ready", "长期记忆系统已就绪。")

        thread = self._thread_group.spawn(
            load,
            name="sakura-mem0-loader",
            daemon=True,
        )
        if thread is None:
            self._loading = False
            self._record_load_diagnostic(
                event="memory_store_load_failed",
                stage="loader_thread",
                outcome="failed",
                status="failed",
                category="loader_thread_unavailable",
                error_type="ThreadStartError",
                elapsed_ms=int((time.monotonic() - load_started_at) * 1000),
            )
        return status_event

    def _create_memory_client(self, api_settings: "ApiSettings | None" = None) -> Any:
        with _MEM0_CREATE_LOCK:
            try:
                memory = ProcessIsolatedMem0Client(
                    self.build_mem0_config(api_settings),
                )
            except Exception as exc:
                self._record_load_diagnostic(
                    event="mem0_process_start_failed",
                    stage="process_start",
                    outcome="failed",
                    category="process_start_failed",
                    error_type=exc.__class__.__name__,
                )
                raise
            self._register_active_embedder(memory)
            try:
                memory.wait_ready(cancel=lambda: self._closed or self._load_cancel.is_set())
            except BaseException:
                memory.close()
                raise
            return memory

    def _supports_memory_llm_reload(self, memory: Any | None) -> bool:
        if memory is None:
            return False
        if callable(getattr(memory, "reload_llm", None)):
            return True
        config = getattr(memory, "config", None)
        return hasattr(memory, "llm") and hasattr(config, "llm")

    def _create_memory_llm(
        self,
        api_settings: "ApiSettings",
        memory: Any | None = None,
    ) -> tuple[Any, Any]:
        """只按新 API 设置重建 mem0 的 LLM，避免重开本地 Qdrant 客户端。"""

        with _MEM0_CREATE_LOCK:
            llm_section = self.build_mem0_config(api_settings)["llm"]
            reload_llm = getattr(memory, "reload_llm", None)
            if callable(reload_llm):
                reload_llm(llm_section)
                return None, None
            install_mem0_vendor()
            from mem0.llms.configs import LlmConfig
            from mem0.utils.factory import LlmFactory

            llm_config = LlmConfig(
                provider=llm_section["provider"],
                config=dict(llm_section.get("config") or {}),
            )
            llm = LlmFactory.create(llm_config.provider, llm_config.config)
            return llm_config, llm

    def _apply_memory_llm(self, memory: Any, llm_config: Any, llm: Any) -> None:
        if memory is None or llm_config is None or llm is None:
            return
        memory.config.llm = llm_config
        memory.llm = llm

    def _set_status_locked(
        self,
        status: str,
        message: str,
    ) -> tuple[list[Callable[[str, str], None]], str, str]:
        self._status = status
        self._status_message = message
        return list(self._status_listeners), status, message

    def _publish_status(self, status: str, message: str) -> None:
        with self._lock:
            status_event = self._set_status_locked(status, message)
        self._notify_status_event(status_event)

    def _notify_status_event(
        self,
        status_event: tuple[list[Callable[[str, str], None]], str, str] | None,
    ) -> None:
        if status_event is None:
            return
        listeners, status, message = status_event
        for listener in listeners:
            self._notify_status_listener(listener, status, message)

    def _notify_status_listener(
        self,
        listener: Callable[[str, str], None],
        status: str,
        message: str,
    ) -> None:
        try:
            listener(status, message)
        except Exception:  # noqa: BLE001
            logger.debug("mem0 状态监听器执行失败", exc_info=True)

    def _loading_response(self) -> dict[str, Any]:
        elapsed = int(time.time() - self._loading_started_at) if self._loading_started_at else 0
        return {
            "status": "loading",
            "message": (
                f"记忆系统正在初始化（已等待 {elapsed} 秒），稍后会自动就绪。"
            ),
            "memories": [],
        }

    def _failed_response(self, error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "message": (
                "长期记忆系统暂时不可用，普通聊天仍可继续。"
            ),
            "error": error,
            "memories": [],
        }

    def _mark_runtime_failed(self, error: str) -> None:
        with self._lock:
            self._memory = None
            self._loading = False
            self._load_error = error
            self._status = "failed"
            self._status_message = f"长期记忆系统暂时不可用：{error}"


class ScopedMemoryStore(MemoryStore):
    """复用同一个 mem0 运行时，但把业务 scope 固定在创建时的角色上。"""

    def __init__(self, owner: MemoryStore, scope_id: str) -> None:
        self._owner = owner
        self.base_dir = owner.base_dir
        self.api_settings = owner.api_settings
        self.scope_id = _normalize_scope_id(scope_id)
        self.memory_client = owner.memory_client
        self.resource_registry = owner.resource_registry
        self._loading_started_at = owner._loading_started_at

    def set_scope(self, scope_id: str) -> None:
        self.scope_id = _normalize_scope_id(scope_id)

    def is_ready(self) -> bool:
        return self._owner.is_ready()

    def needs_embedding_model_download(self) -> bool:
        return self._owner.needs_embedding_model_download()

    def close(self) -> None:
        """视图不拥有底层 mem0 运行时，关闭由 owner 负责。"""

        return None

    def _get_memory(self, *, wait: bool = True) -> Any | None:
        return self._owner._get_memory(wait=wait)

    def _load_core_profiles(self) -> dict[str, Any]:
        return self._owner._load_core_profiles()

    def _save_core_profiles(self, profiles: dict[str, Any]) -> None:
        self._owner._save_core_profiles(profiles)

    def _loading_response(self) -> dict[str, Any]:
        return self._owner._loading_response()

    def _failed_response(self, error: str) -> dict[str, Any]:
        return self._owner._failed_response(error)

    def _mark_runtime_failed(self, error: str) -> None:
        self._owner._mark_runtime_failed(error)


def _resolve_base_dir(base_dir: Path | None) -> Path:
    if base_dir is None:
        return Path.cwd()
    path = Path(base_dir)
    if path.name == "memory.json" and path.parent.name == "data":
        return path.parent.parent
    return path


def _normalize_scope_id(scope_id: str | None) -> str:
    text = (scope_id or "").strip()
    return text if text and not any(ch.isspace() for ch in text) else DEFAULT_MEMORY_SCOPE


def _mem0_session_scope(filters: dict[str, str]) -> str:
    parts: list[str] = []
    for key in sorted(("user_id", "agent_id", "run_id")):
        value = filters.get(key)
        if value:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _reset_mem0_curation_cache(
    memory: Any,
    *,
    scope_id: str,
    memory_ids: Iterable[object] | None = None,
) -> dict[str, int]:
    """Reset mem0's SQLite curation cache in its owning process."""

    db = getattr(memory, "db", None)
    connection = getattr(db, "connection", None)
    if connection is None:
        return {"messages": 0, "history": 0}
    clean_memory_ids = [
        memory_id
        for memory_id in (str(item).strip() for item in (memory_ids or []))
        if memory_id
    ]
    session_scope = _mem0_session_scope({"user_id": _normalize_scope_id(scope_id)})
    lock = getattr(db, "_lock", None)
    context = lock if lock is not None else nullcontext()
    deleted_messages = 0
    deleted_history = 0
    try:
        with context:
            connection.execute("BEGIN")
            message_cursor = connection.execute(
                "DELETE FROM messages WHERE session_scope = ?",
                (session_scope,),
            )
            deleted_messages = max(0, int(message_cursor.rowcount or 0))
            if clean_memory_ids:
                placeholders = ",".join("?" for _ in clean_memory_ids)
                history_cursor = connection.execute(
                    f"DELETE FROM history WHERE memory_id IN ({placeholders})",
                    clean_memory_ids,
                )
                deleted_history = max(0, int(history_cursor.rowcount or 0))
            connection.execute("COMMIT")
    except (sqlite3.Error, RuntimeError):
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    return {"messages": deleted_messages, "history": deleted_history}


def _local_embedding_model_kwargs(model_name: str, base_dir: Path | None = None) -> dict[str, Any]:
    """只向 FastEmbed 传入固定的本地 ONNX snapshot，绝不隐式联网。"""

    snapshot = _embedding_model_snapshot(model_name, base_dir)
    if snapshot is None:
        snapshot = (
            _project_embedding_cache_folder(base_dir)
            / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
            / "snapshots"
            / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
    return {
        "specific_model_path": str(snapshot),
        "local_files_only": True,
        "providers": ["CPUExecutionProvider"],
        "threads": max(1, min(4, os.cpu_count() or 1)),
    }


def _embedding_model_cached(model_name: str, base_dir: Path | None = None) -> bool:
    """判断本地是否已有完整嵌入模型缓存，避免半下载缓存触发离线加载失败。"""

    return _embedding_model_cache_folder(model_name, base_dir) is not None


def _embedding_model_cache_folder(model_name: str, base_dir: Path | None = None) -> Path | None:
    """返回包含固定 FastEmbed ONNX revision 的缓存根目录。"""

    snapshot = _embedding_model_snapshot(model_name, base_dir)
    return snapshot.parents[2] if snapshot is not None else None


def _embedding_model_snapshot(model_name: str, base_dir: Path | None = None) -> Path | None:
    """返回已校验的固定 ONNX snapshot；其他 revision 和旧 PyTorch cache 均不命中。"""

    if model_name != DEFAULT_EMBEDDING_MODEL:
        return None
    for root in _embedding_model_cache_candidates(base_dir):
        snapshot = (
            root
            / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
            / "snapshots"
            / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
        if _fastembed_snapshot_is_complete(snapshot):
            return snapshot
    return None


def _embedding_model_cache_candidates(base_dir: Path | None = None) -> list[Path]:
    """按加载优先级列出 Sakura 管理或显式覆盖的 FastEmbed 缓存目录。"""

    cache_candidates: list[Path] = []

    def add_candidate(path: Path) -> None:
        candidate = path.expanduser()
        if candidate not in cache_candidates:
            cache_candidates.append(candidate)

    cache_root = (os.environ.get("FASTEMBED_CACHE_PATH") or "").strip()
    if cache_root:
        add_candidate(Path(cache_root))
    if base_dir is not None:
        add_candidate(Path(base_dir) / "runtime" / "fastembed-cache")
    return cache_candidates


def _project_embedding_cache_folder(base_dir: Path | None = None) -> Path:
    """返回 Sakura 自己管理的 FastEmbed/Hugging Face snapshot 缓存目录。"""

    root = _resolve_base_dir(base_dir)
    return root / "runtime" / "fastembed-cache"


def import_embedding_model_archive(
    path: Path,
    base_dir: Path | None = None,
    *,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> EmbeddingModelImportResult:
    """导入 all-MiniLM-L6-v2 的固定 FastEmbed ONNX snapshot ZIP。"""

    _check_model_task_cancelled(cancel)
    _report_model_progress(progress, "validating", 5)
    archive_path = Path(path)
    if not archive_path.exists():
        raise FileNotFoundError(f"记忆模型包不存在：{archive_path}")
    destination_root = _project_embedding_cache_folder(base_dir)
    destination_model_dir = destination_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    destination_root.mkdir(parents=True, exist_ok=True)

    temp_root = destination_root / f".memory_model_import_{int(time.time() * 1000)}_{threading.get_ident()}"
    staging_model_dir = temp_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    backup_model_dir = destination_root / f".{DEFAULT_EMBEDDING_MODEL_CACHE_NAME}.backup"
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            try:
                validate_zip_resource_limits(
                    zf,
                    destination=destination_root,
                    label="记忆模型包",
                )
            except ValueError as exc:
                raise MemoryModelImportError(str(exc)) from exc
            model_prefix = _validate_embedding_model_zip_members(zf)
            temp_root.mkdir(parents=True, exist_ok=False)
            _extract_embedding_model_zip(
                zf,
                model_prefix,
                staging_model_dir,
                progress=progress,
                cancel=cancel,
            )
            snapshot = (
                staging_model_dir
                / "snapshots"
                / DEFAULT_EMBEDDING_ARTIFACT_REVISION
            )
            if not _fastembed_snapshot_is_complete(snapshot):
                raise MemoryModelImportError(
                    "记忆模型包不完整：缺少固定 revision 的 model.onnx 或 tokenizer/config 文件。"
                )
            _validate_fastembed_snapshot_artifacts(snapshot)

        _check_model_task_cancelled(cancel)
        _report_model_progress(progress, "installing", 90)
        _replace_embedding_model_dir(
            staging_model_dir,
            destination_model_dir,
            backup_model_dir,
        )
    except zipfile.BadZipFile as exc:
        raise MemoryModelImportError("不是有效的记忆模型 ZIP 包。") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    snapshot_count = sum(
        1
        for child in (destination_model_dir / "snapshots").iterdir()
        if child.is_dir()
    )
    _report_model_progress(progress, "completed", 100)
    return EmbeddingModelImportResult(
        model_name=DEFAULT_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=destination_model_dir,
        snapshot_count=snapshot_count,
    )


def download_embedding_model(
    base_dir: Path | None = None,
    *,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> EmbeddingModelImportResult:
    """下载固定 all-MiniLM-L6-v2 ONNX revision 到 Sakura 管理的缓存。"""

    destination_root = _project_embedding_cache_folder(base_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    temp_root = destination_root / (
        f".memory_model_download_{int(time.time() * 1000)}_{threading.get_ident()}"
    )
    staging_root = temp_root / "hub"
    staging_model_dir = staging_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    destination_model_dir = destination_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    backup_model_dir = destination_root / f".{DEFAULT_EMBEDDING_MODEL_CACHE_NAME}.backup"
    try:
        _check_model_task_cancelled(cancel)
        _report_model_progress(progress, "connecting", 5)
        temp_root.mkdir(parents=True, exist_ok=False)
        _download_hf_snapshot(
            DEFAULT_EMBEDDING_ARTIFACT_REPO,
            staging_root,
            progress=progress,
            cancel=cancel,
        )
        _check_model_task_cancelled(cancel)
        snapshot = (
            staging_model_dir
            / "snapshots"
            / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
        if not _fastembed_snapshot_is_complete(snapshot):
            raise MemoryModelImportError(
                "记忆模型下载后仍不完整：缺少固定 revision 的 model.onnx 或 tokenizer/config 文件。"
            )
        _validate_fastembed_snapshot_artifacts(snapshot)
        _report_model_progress(progress, "installing", 90)
        _replace_embedding_model_dir(
            staging_model_dir,
            destination_model_dir,
            backup_model_dir,
        )
        _report_model_progress(progress, "completed", 100)
    except MemoryModelTaskCancelled:
        raise
    except MemoryModelImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MemoryModelImportError(
            "记忆模型在线安装失败，请检查 HuggingFace 访问、网络或代理后重试。"
            f"\n\n原始错误：{exc}"
        ) from exc

    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    snapshot_dir = destination_model_dir / "snapshots"
    snapshot_count = sum(1 for child in snapshot_dir.iterdir() if child.is_dir())
    return EmbeddingModelImportResult(
        model_name=DEFAULT_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=destination_model_dir,
        snapshot_count=snapshot_count,
    )


def _download_hf_snapshot(
    repo_id: str,
    cache_folder: Path,
    *,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> str:
    try:
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm
    except ImportError as exc:
        raise MemoryModelImportError("缺少 huggingface_hub 依赖，无法在线安装记忆模型。") from exc

    class CancellableProgress(tqdm):
        def update(self, count: int | float = 1) -> bool | None:
            _check_model_task_cancelled(cancel)
            changed = super().update(count)
            total = int(self.total or 0)
            if total > 0:
                percent = 10 + min(75, int((float(self.n) / total) * 75))
                _report_model_progress(progress, "downloading", percent)
            _check_model_task_cancelled(cancel)
            return changed

    return str(
        snapshot_download(
            repo_id=repo_id,
            revision=DEFAULT_EMBEDDING_ARTIFACT_REVISION,
            cache_dir=str(cache_folder),
            endpoint=(os.environ.get("HF_ENDPOINT") or DEFAULT_HUGGINGFACE_ENDPOINT).strip(),
            allow_patterns=list(DEFAULT_EMBEDDING_MODEL_ALLOW_PATTERNS),
            local_files_only=False,
            tqdm_class=CancellableProgress,
        )
    )


def _validate_embedding_model_zip_members(zf: zipfile.ZipFile) -> PurePosixPath:
    """校验 ZIP 只包含目标模型目录，并返回模型目录在 ZIP 内的前缀。"""

    paths: list[PurePosixPath] = []
    file_paths: list[PurePosixPath] = []
    for info in zf.infolist():
        rel = _safe_zip_member_path(info)
        paths.append(rel)
        if not info.is_dir():
            file_paths.append(rel)
    if not file_paths:
        raise MemoryModelImportError("记忆模型包为空。")

    prefixes = [
        PurePosixPath(DEFAULT_EMBEDDING_MODEL_CACHE_NAME),
        PurePosixPath("fastembed-cache", DEFAULT_EMBEDDING_MODEL_CACHE_NAME),
        PurePosixPath("hub", DEFAULT_EMBEDDING_MODEL_CACHE_NAME),
    ]
    for prefix in prefixes:
        if not any(_zip_path_is_under(path, prefix) for path in file_paths):
            continue
        allowed_parents = set(prefix.parents)
        for path in paths:
            if path == PurePosixPath("."):
                continue
            if path in allowed_parents:
                continue
            if not _zip_path_is_under(path, prefix):
                raise MemoryModelImportError(
                    "记忆模型包只能包含 "
                    f"{DEFAULT_EMBEDDING_MODEL_CACHE_NAME} 模型缓存目录。"
                )
        return prefix
    if any(path.parts[0] == "snapshots" for path in file_paths):
        allowed_root_parts = {"blobs", "refs", "snapshots", ".no_exist"}
        for path in paths:
            if path.parts[0] not in allowed_root_parts:
                raise MemoryModelImportError(
                    "记忆模型包根目录只能包含 blobs/、refs/、snapshots/ 或 .no_exist/。"
                )
        return PurePosixPath(".")
    raise MemoryModelImportError(
        f"记忆模型包缺少 {DEFAULT_EMBEDDING_MODEL_CACHE_NAME} 目录。"
    )


def _safe_zip_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    member = str(info.filename or "").replace("\\", "/").rstrip("/")
    if not member:
        raise MemoryModelImportError("记忆模型包包含空 ZIP 成员名。")
    if _is_zip_symlink(info):
        raise MemoryModelImportError(f"记忆模型包不允许包含符号链接：{member}")
    if "\x00" in member or member.startswith("/") or _WINDOWS_DRIVE_RE.match(member):
        raise MemoryModelImportError(f"ZIP 成员必须是安全的相对路径：{member!r}")
    parts = member.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise MemoryModelImportError(f"ZIP 成员包含不安全路径片段：{member!r}")
    return PurePosixPath(*parts)


def _zip_path_is_under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    if prefix == PurePosixPath("."):
        return True
    return path == prefix or path.is_relative_to(prefix)


def _extract_embedding_model_zip(
    zf: zipfile.ZipFile,
    model_prefix: PurePosixPath,
    destination_model_dir: Path,
    *,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """只把目标模型目录抽取到 staging 目录，避免 zipfile.extractall 的路径风险。"""

    destination_model_dir.mkdir(parents=True, exist_ok=True)
    members = zf.infolist()
    total = max(1, len(members))
    for index, info in enumerate(members, start=1):
        _check_model_task_cancelled(cancel)
        rel = _safe_zip_member_path(info)
        if not _zip_path_is_under(rel, model_prefix) or rel == model_prefix:
            continue
        prefix_length = 0 if model_prefix == PurePosixPath(".") else len(model_prefix.parts)
        target_rel = PurePosixPath(*rel.parts[prefix_length:])
        target = destination_model_dir.joinpath(*target_rel.parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as source, target.open("wb") as output:
            while True:
                _check_model_task_cancelled(cancel)
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        _report_model_progress(progress, "extracting", 10 + int(index * 75 / total))


def _replace_embedding_model_dir(
    staging_model_dir: Path,
    destination_model_dir: Path,
    backup_model_dir: Path,
) -> None:
    """原子边界内替换模型目录；任何失败都恢复旧的可读缓存。"""

    if backup_model_dir.exists():
        shutil.rmtree(backup_model_dir, ignore_errors=True)
    if destination_model_dir.exists():
        rename_with_retry(destination_model_dir, backup_model_dir)
    moved = False
    try:
        shutil.move(str(staging_model_dir), str(destination_model_dir))
        moved = True
        if backup_model_dir.exists():
            shutil.rmtree(backup_model_dir, ignore_errors=True)
    except Exception:
        if moved and destination_model_dir.exists():
            shutil.rmtree(destination_model_dir, ignore_errors=True)
        if backup_model_dir.exists() and not destination_model_dir.exists():
            rename_with_retry(backup_model_dir, destination_model_dir)
        raise


def _check_model_task_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise MemoryModelTaskCancelled("记忆模型任务已取消。")


def _report_model_progress(
    progress: Callable[[str, int], None] | None,
    stage: str,
    percent: int,
) -> None:
    if progress is not None:
        progress(stage, max(0, min(100, int(percent))))


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _classify_memory_load_exception(exc: Exception, *, stage: str) -> str:
    if isinstance(exc, (ModuleNotFoundError, ImportError)):
        return "dependency_import_failed"
    if isinstance(exc, MemoryError):
        return "resource_exhausted"
    if isinstance(exc, PermissionError):
        return "storage_permission_denied"
    if isinstance(exc, FileNotFoundError):
        return "model_files_unavailable" if "embedding" in stage else "storage_unavailable"
    if isinstance(exc, TimeoutError):
        return "startup_timeout"
    if isinstance(exc, OSError):
        return "storage_unavailable"
    if stage in {"embedding_wait", "model_load", "dependency_import"}:
        return "embedding_startup_failed"
    if stage == "mem0_import":
        return "mem0_import_failed"
    if stage == "mem0_client_create":
        return "client_initialization_failed"
    return "load_failed"


def _format_memory_load_error(exc: Exception, *, embedding_download: bool) -> str:
    raw_message = str(exc).strip() or exc.__class__.__name__
    if not embedding_download:
        return f"长期记忆系统初始化失败：{raw_message}"
    return (
        "长期记忆系统初始化失败：本地嵌入模型下载失败，"
        "请前往项目 Release 下载 models--qdrant--all-MiniLM-L6-v2-onnx.zip，"
        "然后在设置页手动导入：\n"
        "https://github.com/Rvosy/Sakura/releases/download/v0.9.7/"
        "models--qdrant--all-MiniLM-L6-v2-onnx.zip\n"
        "也可以尝试开启代理并重启 Sakura 重新下载；普通聊天仍可继续。"
        f"\n\n原始错误：{raw_message}"
    )


def _is_closed_client_error(exc: Exception) -> bool:
    return "client has been closed" in str(exc).lower()


def _is_missing_memory_error(exc: Exception, memory_id: str) -> bool:
    message = str(exc).lower()
    has_missing_marker = any(
        marker in message
        for marker in (
            "not found",
            "does not exist",
            "not exist",
            "no memory",
            "未找到",
            "不存在",
        )
    )
    if not has_missing_marker:
        return False
    normalized_id = str(memory_id).lower()
    return bool(normalized_id and normalized_id in message) or "memory" in message or "记忆" in message


def _delete_memory_idempotently(mem: Any, memory_id: str) -> bool:
    """删除长期记忆；底层已不存在时视为删除完成，避免清理工具误报异常。"""

    try:
        mem.delete(memory_id)
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_memory_error(exc, memory_id):
            raise
        return True
    return False


def _close_memory_client(memory: Any | None) -> None:
    """释放 mem0 及本地 Qdrant 资源，避免重建时残留文件锁。"""

    if memory is None:
        return
    close = getattr(memory, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭 mem0 运行时失败", exc_info=True)
    embedder = getattr(memory, "embedding_model", None)
    embedder_close = getattr(embedder, "close", None)
    if callable(embedder_close):
        try:
            embedder_close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭记忆嵌入模型进程失败", exc_info=True)
    vector_store = getattr(memory, "vector_store", None)
    client = getattr(vector_store, "client", None)
    client_close = getattr(client, "close", None)
    if callable(client_close):
        try:
            client_close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭 Qdrant 客户端失败", exc_info=True)


def _fastembed_snapshot_is_complete(snapshot: Path) -> bool:
    """确认固定 ONNX snapshot 包含 FastEmbed 加载所需的全部文件。"""

    return snapshot.is_dir() and all(
        (snapshot / filename).is_file()
        for filename in DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES
    )


def _validate_fastembed_snapshot_artifacts(snapshot: Path) -> None:
    """按固定 size/SHA-256 校验 ONNX 工件，避免错误 ZIP 替换可读缓存。"""

    for filename, (expected_size, expected_sha256) in DEFAULT_EMBEDDING_MODEL_ARTIFACTS.items():
        path = snapshot / filename
        if not path.is_file() or path.stat().st_size != expected_size:
            raise MemoryModelImportError(f"记忆 ONNX 模型文件大小不匹配：{filename}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise MemoryModelImportError(f"记忆 ONNX 模型文件校验失败：{filename}")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _core_profile_id(scope_id: str) -> str:
    return f"{MEMORY_LAYER_CORE_PROFILE}:{_normalize_scope_id(scope_id)}"


def _is_core_profile_id(memory_id: str) -> bool:
    return memory_id.strip().startswith(f"{MEMORY_LAYER_CORE_PROFILE}:")


def _normalize_memory_layer(value: Any, *, default: str = DEFAULT_MEMORY_LAYER) -> str:
    text = str(value or "").strip()
    return text if text in MEMORY_LAYERS else default


def _optional_memory_layer(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text in MEMORY_LAYERS else None


def _memory_layer_label(layer: str) -> str:
    return MEMORY_LAYER_LABELS.get(layer, layer)


def _metadata_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    metadata = raw.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) else ""


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _memory_metadata(
    arguments: dict[str, Any],
    *,
    scope_id: str,
    existing: dict[str, Any] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """把工具/UI/整理传入字段归一成 Sakura 记忆 metadata。"""

    existing_metadata = _metadata_mapping(existing or {})
    now = updated_at or _now_iso()
    layer_default = str((existing or {}).get("layer") or existing_metadata.get("layer") or DEFAULT_MEMORY_LAYER)
    metadata: dict[str, Any] = dict(existing_metadata)
    layer = _normalize_memory_layer(arguments.get("layer") or metadata.get("layer"), default=layer_default)
    metadata.update(
        {
            "layer": layer,
            "category": _optional_text(arguments, "category")
            or str(metadata.get("category") or "").strip(),
            "importance": _bounded_float(
                arguments.get("importance", metadata.get("importance")),
                default=_bounded_float(metadata.get("importance"), default=DEFAULT_MEMORY_IMPORTANCE),
            ),
            "confidence": _bounded_float(
                arguments.get("confidence", metadata.get("confidence")),
                default=_bounded_float(metadata.get("confidence"), default=DEFAULT_MEMORY_CONFIDENCE),
            ),
            "source": _optional_text(arguments, "source")
            or str(metadata.get("source") or DEFAULT_MEMORY_SOURCE).strip(),
            "scope": _normalize_scope_id(_optional_text(arguments, "scope") or str(metadata.get("scope") or scope_id)),
            "created_at": created_at
            or str(metadata.get("created_at") or (existing or {}).get("created_at") or now),
            "updated_at": now,
            "last_accessed_at": str(
                arguments.get("last_accessed_at")
                or metadata.get("last_accessed_at")
                or (existing or {}).get("last_accessed_at")
                or ""
            ),
        }
    )
    return metadata


def looks_like_sensitive_memory(content: str) -> bool:
    """粗粒度识别不应自动进入长期记忆的敏感凭据和身份信息。"""

    text = content.strip()
    lowered = text.lower()
    keyword_patterns = (
        "password",
        "passwd",
        "api_key",
        "apikey",
        "secret",
        "token",
        "access key",
        "private key",
        "密钥",
        "密码",
        "口令",
        "令牌",
        "身份证",
        "银行卡",
        "信用卡",
    )
    if any(keyword in lowered for keyword in keyword_patterns):
        return True
    regexes = (
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\b[A-Za-z0-9_=-]{32,}\.[A-Za-z0-9_=-]{16,}\.[A-Za-z0-9_=-]{16,}\b",
        r"\b\d{15}(\d{2}[0-9Xx])?\b",
        r"\b(?:\d[ -]*?){13,19}\b",
    )
    return any(re.search(pattern, text) for pattern in regexes)


def _query_needs_procedural_memory(query: str, mode: str) -> bool:
    if mode in {"tool", "screen_awareness"}:
        return True
    text = query.lower()
    keywords = (
        "格式",
        "风格",
        "习惯",
        "偏好",
        "默认",
        "规则",
        "协作",
        "流程",
        "怎么做",
        "以后",
        "下次",
        "format",
        "style",
        "preference",
        "workflow",
        "rule",
    )
    return any(keyword in text for keyword in keywords)


def _query_needs_episodic_memory(query: str, mode: str) -> bool:
    if mode in {"event", "recap"}:
        return True
    text = query.lower()
    keywords = (
        "之前",
        "上次",
        "刚才",
        "历史",
        "进展",
        "回顾",
        "发生",
        "做过",
        "项目状态",
        "remember when",
        "last time",
        "previous",
        "history",
        "progress",
    )
    return any(keyword in text for keyword in keywords)


def _memory_matches_query(memory: dict[str, Any], query: str) -> bool:
    text = query.strip().lower()
    if not text:
        return True
    haystack = " ".join(
        str(memory.get(key) or "")
        for key in ("id", "content", "category", "source", "layer")
    ).lower()
    return text in haystack


def _memory_matches_filters(
    memory: dict[str, Any],
    *,
    layer: str | None,
    category: str,
    scope: str,
) -> bool:
    if layer is not None and str(memory.get("layer") or DEFAULT_MEMORY_LAYER) != layer:
        return False
    if category and category not in str(memory.get("category") or "").lower():
        return False
    memory_scope = _normalize_scope_id(str(memory.get("scope") or scope))
    return memory_scope == scope


def _rank_memories(memories: list[dict[str, Any]], *, query: str) -> list[dict[str, Any]]:
    query_text = query.strip().lower()

    def rank_key(memory: dict[str, Any]) -> tuple[float, float, float]:
        content = str(memory.get("content") or "")
        score = _bounded_float(memory.get("score"), default=0.0)
        if query_text and query_text in content.lower():
            score = max(score, 0.7)
        importance = _bounded_float(memory.get("importance"), default=DEFAULT_MEMORY_IMPORTANCE)
        updated_ts = _parse_iso_timestamp(str(memory.get("updated_at") or memory.get("created_at") or ""))
        return (score + importance * 0.25, importance, updated_ts)

    return sorted(memories, key=rank_key, reverse=True)


def _parse_iso_timestamp(value: str) -> float:
    text = value.strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _format_memory_context(
    *,
    core_profile: dict[str, Any] | None,
    semantic: list[dict[str, Any]],
    episodic: list[dict[str, Any]],
    procedural: list[dict[str, Any]],
    session: list[dict[str, Any]],
    status: str = "",
) -> str:
    sections: list[str] = []
    if status.strip():
        sections.append(f"记忆系统状态：{status.strip()}")
    if core_profile is not None:
        content = _clip_text(str(core_profile.get("content") or ""), CORE_PROFILE_CONTEXT_BUDGET)
        if content:
            sections.append(f"【常驻档案】\n{content}")
    sections.extend(
        _format_memory_section(
            title,
            memories,
            budget=budget,
        )
        for title, memories, budget in (
            ("【当前任务记忆】", session, SESSION_CONTEXT_BUDGET),
            ("【相关长期事实】", semantic, MEMORY_SECTION_CHAR_BUDGET),
            ("【协作规则与偏好】", procedural, MEMORY_SECTION_CHAR_BUDGET),
            ("【过往事件总结】", episodic, MEMORY_SECTION_CHAR_BUDGET),
        )
        if memories
    )
    if not sections:
        return "暂无可注入的长期记忆。"
    sections.append("注入说明：以上记忆按相关性选择；低置信或过时内容应结合当前对话核实。")
    return "\n\n".join(sections)


def _format_memory_section(
    title: str,
    memories: list[dict[str, Any]],
    *,
    budget: int,
) -> str:
    lines: list[str] = []
    used = 0
    for memory in memories:
        content = str(memory.get("content") or "").strip()
        if not content:
            continue
        category = str(memory.get("category") or "").strip()
        confidence = _bounded_float(memory.get("confidence"), default=DEFAULT_MEMORY_CONFIDENCE)
        prefix = f"- [{category}]" if category else "-"
        line = f"{prefix} {content}"
        if confidence < 0.7:
            line += f"（置信度 {confidence:.2f}）"
        if used + len(line) > budget and lines:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return f"{title}\n" + "\n".join(lines)


def _clip_text(text: str, budget: int) -> str:
    value = text.strip()
    if len(value) <= budget:
        return value
    return value[: max(0, budget - 1)].rstrip() + "…"


def _normalize_memory_results(raw: Any, *, default_scope: str = DEFAULT_MEMORY_SCOPE) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        candidates = raw.get("results") or raw.get("memories") or []
    else:
        candidates = raw
    if not isinstance(candidates, list):
        return []
    memories: list[dict[str, Any]] = []
    for item in candidates:
        memory = _normalize_memory_record(item, default_scope=default_scope)
        if memory is not None:
            memories.append(memory)
    return memories


def _normalize_memory_record(raw: Any, *, default_scope: str = DEFAULT_MEMORY_SCOPE) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("memory") or raw.get("content") or raw.get("data") or "").strip()
    memory_id = str(raw.get("id") or raw.get("memory_id") or "").strip()
    if not content and not memory_id:
        return None
    metadata = _metadata_mapping(raw)
    layer = _normalize_memory_layer(raw.get("layer") or metadata.get("layer"))
    category = str(raw.get("category") or metadata.get("category") or "").strip()
    source = str(raw.get("source") or metadata.get("source") or DEFAULT_MEMORY_SOURCE).strip()
    created_at = str(raw.get("created_at") or metadata.get("created_at") or "").strip()
    updated_at = str(raw.get("updated_at") or metadata.get("updated_at") or created_at).strip()
    last_accessed_at = str(raw.get("last_accessed_at") or metadata.get("last_accessed_at") or "").strip()
    scope = _normalize_scope_id(str(raw.get("scope") or metadata.get("scope") or raw.get("user_id") or default_scope))
    record = MemoryRecord(
        id=memory_id,
        content=content,
        layer=layer,
        category=category,
        importance=_bounded_float(raw.get("importance", metadata.get("importance")), default=DEFAULT_MEMORY_IMPORTANCE),
        confidence=_bounded_float(raw.get("confidence", metadata.get("confidence")), default=DEFAULT_MEMORY_CONFIDENCE),
        source=source,
        scope=scope,
        created_at=created_at,
        updated_at=updated_at,
        last_accessed_at=last_accessed_at,
        score=_bounded_float(raw.get("score", raw.get("relevance_score")), default=0.0),
        metadata=metadata,
    )
    memory = {**dict(raw), **record.to_dict()}
    return memory


def _require_owned_memory(
    mem: Any,
    memory_id: str,
    scope_id: str,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    raw = mem.get(memory_id)
    if not isinstance(raw, dict):
        if allow_missing:
            return None
        raise ValueError(f"未找到长期记忆：{memory_id}")
    metadata = _metadata_mapping(raw)
    explicit_scope = str(
        raw.get("scope") or metadata.get("scope") or raw.get("user_id") or ""
    ).strip()
    normalized_scope = _normalize_scope_id(explicit_scope) if explicit_scope else ""
    if not normalized_scope:
        get_all = getattr(mem, "get_all", None)
        if not callable(get_all):
            # 兼容只实现 get/delete 的旧测试或第三方后端；正式 mem0 后端支持
            # 按 user_id 查询，必须走下面的所有权验证。
            normalized_scope = _normalize_scope_id(scope_id)
        else:
            try:
                scoped_raw = get_all(filters={"user_id": scope_id}, top_k=10000)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"无法验证长期记忆作用域：{memory_id}") from exc
            scoped_ids = {
                str(item.get("id") or item.get("memory_id") or "").strip()
                for item in _raw_memory_candidates(scoped_raw)
                if isinstance(item, dict)
            }
            if memory_id not in scoped_ids:
                raise ValueError(f"长期记忆不属于当前角色，已拒绝修改：{memory_id}")
            normalized_scope = _normalize_scope_id(scope_id)
    if normalized_scope != _normalize_scope_id(scope_id):
        raise ValueError(f"长期记忆不属于当前角色，已拒绝修改：{memory_id}")
    normalized = _normalize_memory_record(raw, default_scope=scope_id)
    if normalized is None:
        raise ValueError(f"长期记忆记录无效：{memory_id}")
    return normalized


def _raw_memory_candidates(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        for key in ("results", "memories", "data"):
            if isinstance(raw.get(key), list):
                return list(raw[key])
        return [raw]
    return list(raw) if isinstance(raw, list) else []


def _first_memory_result(raw: Any, *, default_scope: str = DEFAULT_MEMORY_SCOPE) -> dict[str, Any] | None:
    memories = _normalize_memory_results(raw, default_scope=default_scope)
    return memories[0] if memories else _normalize_memory_record(raw, default_scope=default_scope)


def _entries_for_mem0(entries: list[ChatHistoryEntry]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for entry in entries:
        if entry.role not in {"user", "assistant"}:
            continue
        content = entry.content.strip()
        if not content:
            continue
        if entry.translation.strip():
            content = f"{content}\n中文翻译：{entry.translation.strip()}"
        messages.append({"role": entry.role, "content": content})
    return messages


def _count_mem0_events(raw: Any, *, total: int) -> MemoryCurationCounts:
    results = _normalize_memory_results(raw)
    counts = MemoryCurationCounts(total=total)
    counts.returned = len(results)
    if not results:
        counts.ignored = total
        return counts
    for item in results:
        event = str(item.get("event") or item.get("action") or "").upper()
        event_key = event or "<missing>"
        counts.event_counts[event_key] = counts.event_counts.get(event_key, 0) + 1
        if event in {"ADD", "CREATE", "CREATED"}:
            counts.created += 1
        elif event in {"UPDATE", "UPDATED"}:
            counts.updated += 1
        elif event in {"DELETE", "ARCHIVE", "DELETED", "ARCHIVED"}:
            counts.deleted += 1
        else:
            counts.unclassified += 1
    counts.ignored = max(0, total - counts.created - counts.updated - counts.deleted)
    return counts


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少必填参数：{key}")
    return value.strip()


def _optional_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)
