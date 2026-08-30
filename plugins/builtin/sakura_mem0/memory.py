from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Callable, Iterable

try:
    from .support import (
        ResourceRegistry,
        StoragePaths,
        ThreadGroupResource,
        atomic_write_text,
        external_runtime_sink_active,
        log_event,
        rename_with_retry,
        suppress_runtime_logs,
        validate_zip_resource_limits,
    )
except ImportError:
    from support import (
        ResourceRegistry,
        StoragePaths,
        ThreadGroupResource,
        atomic_write_text,
        external_runtime_sink_active,
        log_event,
        rename_with_retry,
        suppress_runtime_logs,
        validate_zip_resource_limits,
    )


logger = logging.getLogger(__name__)


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
MAX_MEMORY_SOURCE_ENTRY_IDS = 500
MAX_MEMORY_SOURCE_ENTRY_ID_CHARS = 128
CORE_PROFILE_CONTEXT_BUDGET = 1200
SESSION_CONTEXT_BUDGET = 600
MEMORY_SECTION_CHAR_BUDGET = 1600
DEFAULT_MODELSCOPE_ENDPOINT = "https://www.modelscope.cn"
DEFAULT_MODELSCOPE_EMBEDDING_REPO = "onnx-community/all-MiniLM-L6-v2-ONNX"
DEFAULT_MODELSCOPE_EMBEDDING_REVISION = "e1da369847063d70f2fd772226551865bcab1c2d"
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
MODELSCOPE_EMBEDDING_MODEL_ARTIFACTS = {
    "config.json": (
        "config.json",
        794,
        "fe5da868b77bdb104140822a5af0837cb6450ad6de8ff3dfcc8dd44ddd3e3ae7",
    ),
    "model.onnx": (
        "onnx/model.onnx",
        56_796,
        "2f019cf6217537cc4bfc7f5192f21dea1e18445177edaab0bc6163a813e5c7a1",
    ),
    "model.onnx_data": (
        "onnx/model.onnx_data",
        90_261_504,
        "60c758432aa596c30a122942dfe594c457d4d713f890926f1c5f920bd496c8de",
    ),
    "special_tokens_map.json": (
        "special_tokens_map.json",
        695,
        "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        533_808,
        "07805d116826679de90b4edeb2222269c4b8753bc0981be4399f732b2708e904",
    ),
    "tokenizer_config.json": (
        "tokenizer_config.json",
        1_463,
        "e10bb633ba0d7f69ed342ae7de607f36b39ce53b455fbda69c71700bf57e6f66",
    ),
}
_MEM0_CREATE_LOCK = threading.Lock()
_MEMORY_DIAGNOSTIC_WRITE_LOCK = threading.Lock()
_EMBEDDER_OWNER = threading.local()
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DIAGNOSTIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
os.environ.setdefault("MEM0_TELEMETRY", "False")


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

    Core-only diagnostic runs keep a bounded fallback path. When the Runtime v2
    bridge is installed, the same safe fields are routed to the unified Runtime
    log and the fallback JSONL file is left byte-for-byte untouched. All string
    fields are internal identifiers; invalid/free-form values are replaced
    instead of being persisted.
    """

    try:
        if external_runtime_sink_active():
            attributes: dict[str, object] = {
                "component": _diagnostic_token(component),
                "detail_stage": _diagnostic_token(event),
            }
            for key, value in (
                ("stage", stage),
                ("outcome", outcome),
                ("status", status),
                ("category", category),
                ("error_type", error_type),
                ("request", request),
            ):
                if value:
                    attributes[key] = _diagnostic_token(value)
            if elapsed_ms is not None:
                attributes["elapsed_ms"] = max(0, min(int(elapsed_ms), 86_400_000))
            if wait is not None:
                attributes["wait"] = bool(wait)
            if model_cached is not None:
                attributes["model_cached"] = bool(model_cached)
            if child_pid is not None:
                attributes["child_pid"] = max(0, int(child_pid))
            if process_alive is not None:
                attributes["process_alive"] = bool(process_alive)
            with suppress_runtime_logs():
                log_event(
                    "Memory",
                    "Runtime v2 Memory initialization diagnostic",
                    attributes,
                    event="memory.initialization.stage",
                    severity="warning" if outcome == "failed" else "info",
                )
            return

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
            # The Runtime v2 Shell owns truncation and creation. Core-only and
            # fixture runs must not leave surprise log artifacts.
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


def _create_mem0_component(
    *,
    event_prefix: str,
    stage: str,
    factory: Callable[[], Any],
) -> Any:
    """Create one plugin-local backend component and expose direct failures."""

    del event_prefix, stage
    return factory()


def _create_raw_memory_backend(
    memory_type: type[Any],
    mem0_memory_main: Any,
    embedder_factory: Any,
    vector_store_factory: Any,
    config: dict[str, Any],
) -> Any:
    """Build Mem0's compatible local backend without constructing an LLM.

    Sakura owns extraction and curation.  This deliberately bypasses
    ``Memory.__init__`` because that upstream constructor always creates an
    LLM, even when every write uses ``infer=False``.
    """

    from copy import deepcopy
    from types import SimpleNamespace

    from mem0.vector_stores.configs import VectorStoreConfig

    vector_config = VectorStoreConfig(**dict(config["vector_store"]))
    embedder_section = dict(config["embedder"])
    embedder: Any | None = None
    vector_store: Any | None = None
    history: Any | None = None
    try:
        embedder = _create_mem0_component(
            event_prefix="embedding_create",
            stage="embedding_create",
            factory=lambda: embedder_factory.create(
                str(embedder_section["provider"]),
                dict(embedder_section.get("config") or {}),
                vector_config.config,
            ),
        )
        vector_store = _create_mem0_component(
            event_prefix="qdrant_create",
            stage="qdrant_create",
            factory=lambda: vector_store_factory.create(
                vector_config.provider,
                vector_config.config,
            ),
        )
        history = _create_mem0_component(
            event_prefix="sqlite_create",
            stage="sqlite_create",
            factory=lambda: mem0_memory_main.SQLiteManager(str(config["history_db_path"])),
        )
    except BaseException:
        for component in (
            history,
            getattr(vector_store, "client", None),
            embedder,
        ):
            close_component = getattr(component, "close", None)
            if callable(close_component):
                try:
                    close_component()
                except Exception:  # noqa: BLE001 - preserve the startup failure.
                    pass
        raise

    class SakuraRawMemoryBackend(memory_type):
        """Mem0-compatible CRUD/search facade with inference permanently disabled."""

        def __init__(self) -> None:
            self.config = SimpleNamespace(
                vector_store=vector_config,
                version="v1.1",
                reranker=None,
            )
            self.embedding_model = embedder
            self.vector_store = vector_store
            self.db = history
            self.collection_name = vector_config.config.collection_name
            self.api_version = "v1.1"
            self.custom_instructions = None
            self.reranker = None
            self._entity_store = None

        def add(
            self,
            messages: Any,
            *,
            user_id: str | None = None,
            agent_id: str | None = None,
            run_id: str | None = None,
            metadata: dict[str, Any] | None = None,
            infer: bool = True,
            **kwargs: Any,
        ) -> dict[str, list[dict[str, Any]]]:
            if infer is not False:
                raise ValueError("Sakura Memory 后端禁止 Mem0 inference；请先使用 MemoryCurator 整理。")
            if kwargs:
                raise ValueError("Sakura Memory 后端不支持 Mem0 提炼参数。")
            scopes = {
                key: str(value).strip()
                for key, value in (
                    ("user_id", user_id),
                    ("agent_id", agent_id),
                    ("run_id", run_id),
                )
                if value is not None and str(value).strip()
            }
            if not scopes or any(any(ch.isspace() for ch in value) for value in scopes.values()):
                raise ValueError("长期记忆必须提供不含空白的 user_id、agent_id 或 run_id。")
            if isinstance(messages, str):
                normalized_messages = [{"role": "user", "content": messages}]
            elif isinstance(messages, dict):
                normalized_messages = [messages]
            elif isinstance(messages, list):
                normalized_messages = messages
            else:
                raise ValueError("长期记忆内容必须是字符串、消息对象或消息数组。")

            results: list[dict[str, Any]] = []
            for message in normalized_messages:
                if not isinstance(message, dict):
                    raise ValueError("长期记忆消息格式无效。")
                role = str(message.get("role") or "").strip()
                content = message.get("content")
                if role == "system":
                    continue
                if not role or not isinstance(content, str):
                    raise ValueError("长期记忆消息必须包含 role 和文本 content。")
                record_metadata = deepcopy(metadata) if metadata is not None else {}
                record_metadata.update(scopes)
                record_metadata["role"] = role
                actor_name = str(message.get("name") or "").strip()
                if actor_name:
                    record_metadata["actor_id"] = actor_name
                embedding = self.embedding_model.embed(content, "add")
                memory_id = self._create_memory(
                    content,
                    {content: embedding},
                    record_metadata,
                )
                results.append(
                    {
                        "id": memory_id,
                        "memory": content,
                        "event": "ADD",
                        "actor_id": actor_name or None,
                        "role": role,
                    }
                )
            return {"results": results}

    return SakuraRawMemoryBackend()


def _warm_memory_backend(memory: Any) -> None:
    """Materialize the lazy ONNX session before the bounded RPC is published."""

    _create_mem0_component(
        event_prefix="embedding_warmup",
        stage="embedding_warmup",
        factory=lambda: memory.embedding_model.embed(
            "sakura-memory-runtime-warmup",
            "search",
        ),
    )


def _import_mem0_dependencies() -> tuple[Any, Any, Any, Any]:
    """Import only dependencies owned by this plugin's dependency root."""

    _install_disabled_mem0_telemetry_module()
    _install_disabled_qdrant_grpc_module()
    _install_synchronous_qdrant_client_facade()
    from mem0 import Memory
    import mem0.memory.main as mem0_memory_main
    from mem0.utils.factory import EmbedderFactory, VectorStoreFactory

    return Memory, mem0_memory_main, EmbedderFactory, VectorStoreFactory


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


class MemoryModelImportError(RuntimeError):
    """记忆嵌入模型归档包格式错误或导入失败。"""

    def __init__(self, message: str, *, code: str = "DOWNLOAD_FAILED") -> None:
        super().__init__(message)
        self.code = code


class MemoryRuntimeUnavailableError(RuntimeError):
    """Raised when the plugin-owned local backend cannot become ready."""


class MemoryModelTaskCancelled(RuntimeError):
    """用户或当前 Core generation 取消了模型导入/下载。"""


def validate_existing_memory_store(memory_dir: Path) -> None:
    """Open an existing local Qdrant store without loading an embedding model.

    The legacy importer uses this while Core is paused.  Opening a copied store
    through the same Qdrant client as the current plugin catches storage-format
    incompatibilities that metadata-only checks cannot detect.
    """

    qdrant_path = Path(memory_dir) / "qdrant"
    if not qdrant_path.is_dir() or not any(path.is_file() for path in qdrant_path.rglob("*")):
        return
    _install_disabled_qdrant_grpc_module()
    _install_synchronous_qdrant_client_facade()
    from qdrant_client import QdrantClient

    client = QdrantClient(path=qdrant_path.as_posix())
    try:
        collection = client.get_collection(DEFAULT_COLLECTION_NAME)
        vectors = collection.config.params.vectors
        if isinstance(vectors, dict):
            dimensions = {int(value.size) for value in vectors.values()}
        else:
            dimensions = {int(vectors.size)}
        if dimensions != {DEFAULT_EMBEDDING_DIMS}:
            raise ValueError("memory vector dimensions are incompatible")
    finally:
        client.close()
        # Local Qdrant uses this only while the validator owns the copied store.
        # It is runtime state and must not become part of the committed payload.
        (qdrant_path / ".lock").unlink(missing_ok=True)


def normalize_existing_history_database(database: Path) -> None:
    """Normalize a copied Mem0 history database with the runtime SQLite manager.

    Packaged plugin processes import Mem0 from their private dependency root.
    Source-tree tests deliberately do not put that root on ``sys.path``, so the
    narrow fallback loads the same bundled storage module by file without
    importing Mem0's heavyweight package initializer.
    """

    try:
        from mem0.memory.storage import SQLiteManager
    except ModuleNotFoundError as exc:
        if exc.name != "mem0":
            raise
        storage_path = Path(__file__).with_name("mem0") / "memory" / "storage.py"
        spec = importlib.util.spec_from_file_location(
            "sakura_mem0_legacy_storage", storage_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("bundled Mem0 storage module is unavailable") from exc
        storage_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage_module)
        SQLiteManager = storage_module.SQLiteManager

    manager = SQLiteManager(str(database))
    manager.close()


@dataclass(frozen=True)
class EmbeddingModelImportResult:
    """记忆嵌入模型导入结果。"""

    model_name: str
    cache_folder: Path
    model_dir: Path
    snapshot_count: int


@dataclass
class MemoryStore:
    """Sakura 对本地 embedding、Qdrant 与兼容 history 的适配层。"""

    base_dir: Path | None = None
    scope_id: str = DEFAULT_MEMORY_SCOPE
    memory_client: Any | None = None
    resource_registry: ResourceRegistry | None = None
    request_timeout_seconds: float | None = None
    memory_dir: Path | None = None
    memory_cache_dir: Path | None = None
    _memory: Any | None = field(default=None, init=False, repr=False)
    _loading: bool = field(default=False, init=False, repr=False)
    _loading_started_at: float = field(default=0.0, init=False, repr=False)
    _load_error: str = field(default="", init=False, repr=False)
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
        paths = StoragePaths(self.base_dir)
        self.memory_dir = Path(self.memory_dir or paths.memory_dir)
        self.memory_cache_dir = Path(self.memory_cache_dir or paths.memory_cache_dir)
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
            model_cached=_embedding_model_cached(
                DEFAULT_EMBEDDING_MODEL,
                self.base_dir,
                cache_dir=self.memory_cache_dir,
            ),
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
        request: str = "",
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
        if request:
            diagnostic["request"] = _diagnostic_token(request, "unknown")
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
            request=str(diagnostic.get("request") or ""),
        )

    def _on_embedder_diagnostic(self, diagnostic: dict[str, object]) -> None:
        event = str(diagnostic.get("event") or "embedding_progress")
        self._record_load_diagnostic(
            event=event,
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
            request=str(diagnostic.get("request") or ""),
            component=str(diagnostic.get("component") or "embedding_process"),
        )
        if event == "mem0_request_failed":
            self._mark_runtime_failed(
                category=str(diagnostic.get("category") or "request_failed"),
                error_type=str(diagnostic.get("errorType") or "RuntimeError"),
                request=str(diagnostic.get("request") or "unknown"),
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

    def reset_runtime(self) -> None:
        old_memory: Any | None = None
        with self._lock:
            if self._memory is not None and self._memory is not self.memory_client:
                old_memory = self._memory
            self._memory = self.memory_client
            self._loading = False
            self._loading_started_at = 0.0
            self._load_error = ""
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

        return not _embedding_model_cached(
            DEFAULT_EMBEDDING_MODEL,
            self.base_dir,
            cache_dir=self.memory_cache_dir,
        )

    def embedding_model_endpoint(self) -> str:
        """返回当前嵌入模型下载端点，便于 UI 提示用户。"""

        return DEFAULT_MODELSCOPE_ENDPOINT

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
            cache_dir=self.memory_cache_dir,
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

        result = download_embedding_model(
            self.base_dir,
            cache_dir=self.memory_cache_dir,
            progress=progress,
            cancel=cancel,
        )
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
            model_cached=_embedding_model_cached(
                DEFAULT_EMBEDDING_MODEL,
                self.base_dir,
                cache_dir=self.memory_cache_dir,
            ),
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

    def build_local_backend_config(self) -> dict[str, Any]:
        """生成不含 Provider/LLM 的本地 raw vector backend 配置。"""

        assert self.memory_dir is not None
        qdrant_path = self.memory_dir / "qdrant"

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
            "embedder": {
                "provider": "fastembed",
                "config": {
                    "model": DEFAULT_EMBEDDING_MODEL,
                    "embedding_dims": DEFAULT_EMBEDDING_DIMS,
                    "model_kwargs": _local_embedding_model_kwargs(
                        DEFAULT_EMBEDDING_MODEL,
                        self.base_dir,
                        cache_dir=self.memory_cache_dir,
                    ),
                },
            },
            "history_db_path": str(self.memory_dir / "mem0_history.db"),
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
        try:
            while True:
                raw = mem.get_all(filters={"user_id": self.scope_id}, top_k=top_k)
                memories = _normalize_memory_results(raw, default_scope=self.scope_id)
                if limit is not None or len(memories) < top_k:
                    break
                top_k *= 2
        except Exception as exc:
            diagnostic = self.load_diagnostic()
            self._mark_runtime_failed(
                category=str(diagnostic.get("category") or "request_failed"),
                error_type=str(diagnostic.get("errorType") or exc.__class__.__name__),
                request="get_all",
            )
            raise MemoryRuntimeUnavailableError(
                category=str(diagnostic.get("category") or "request_failed"),
                error_type=str(diagnostic.get("errorType") or exc.__class__.__name__),
            ) from exc
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
                self._mark_runtime_failed(
                    category="connection_interrupted",
                    error_type=exc.__class__.__name__,
                    request="search" if query else "get_all",
                )
                return self._failed_response("长期记忆运行时暂时不可用。")
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
        memory = _memory_result_with_requested_fallback(
            raw,
            metadata,
            default_scope=self.scope_id,
            fallback_content=content,
        )
        memory_id = str(memory.get("id") or memory.get("memory_id") or "").strip()
        if memory_id:
            authoritative = mem.get(memory_id)
            if authoritative is not None:
                memory = _memory_result_with_requested_fallback(
                    authoritative,
                    metadata,
                    default_scope=self.scope_id,
                    fallback_content=content,
                    fallback_id=memory_id,
                )
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
        current = mem.get(memory_id)
        memory = _memory_result_with_requested_fallback(
            current if current is not None else raw,
            metadata,
            default_scope=self.scope_id,
            fallback_content=content,
            fallback_id=memory_id,
        )
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

    def _load_core_profiles(self) -> dict[str, Any]:
        assert self.memory_dir is not None
        path = self.memory_dir / "core_profiles.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("读取常驻档案失败", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def _save_core_profiles(self, profiles: dict[str, Any]) -> None:
        assert self.memory_dir is not None
        path = self.memory_dir / "core_profiles.json"
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
        report_dependency_loading = not _embedding_model_cached(
            DEFAULT_EMBEDDING_MODEL,
            self.base_dir,
            cache_dir=self.memory_cache_dir,
        )
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
                mem = self._create_memory_client()
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
                if generation != self._reload_generation or self._closed:
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

    def _create_memory_client(self) -> Any:
        with _MEM0_CREATE_LOCK:
            try:
                (
                    memory_type,
                    mem0_memory_main,
                    embedder_factory,
                    vector_store_factory,
                ) = _import_mem0_dependencies()
                memory = _create_raw_memory_backend(
                    memory_type,
                    mem0_memory_main,
                    embedder_factory,
                    vector_store_factory,
                    self.build_local_backend_config(),
                )
            except Exception as exc:
                self._record_load_diagnostic(
                    event="mem0_backend_start_failed",
                    stage="backend_start",
                    outcome="failed",
                    category="backend_start_failed",
                    error_type=exc.__class__.__name__,
                )
                raise
            self._register_active_embedder(memory)
            try:
                _warm_memory_backend(memory)
                if self._closed or self._load_cancel.is_set():
                    raise RuntimeError("MEMORY_LOAD_CANCELLED")
            except BaseException:
                _close_memory_client(memory)
                raise
            return memory

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

    def _mark_runtime_failed(
        self,
        error: str = "长期记忆运行时暂时不可用。",
        *,
        category: str = "request_failed",
        error_type: str = "RuntimeError",
        request: str = "unknown",
    ) -> None:
        old_memory: Any | None = None
        with self._lock:
            old_memory = self._memory
            self._memory = None
            if self._active_embedder is old_memory:
                self._active_embedder = None
            self._loading = False
            self._load_error = error
            status_event = self._set_status_locked(
                "failed",
                "长期记忆系统暂时不可用；普通聊天仍可继续。",
            )
        self._record_load_diagnostic(
            event="memory_store_runtime_failed",
            stage="request",
            outcome="failed",
            status="failed",
            category=category,
            error_type=error_type,
            request=request,
        )
        self._notify_status_event(status_event)
        _close_memory_client(old_memory)


class ScopedMemoryStore(MemoryStore):
    """复用同一个 mem0 运行时，但把业务 scope 固定在创建时的角色上。"""

    def __init__(self, owner: MemoryStore, scope_id: str) -> None:
        self._owner = owner
        self.base_dir = owner.base_dir
        self.scope_id = _normalize_scope_id(scope_id)
        self.memory_client = owner.memory_client
        self.resource_registry = owner.resource_registry
        self._loading_started_at = owner._loading_started_at

    def set_scope(self, scope_id: str) -> None:
        self.scope_id = _normalize_scope_id(scope_id)

    def is_ready(self) -> bool:
        return self._owner.is_ready()

    def load_diagnostic(self) -> dict[str, object]:
        return self._owner.load_diagnostic()

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

    def _mark_runtime_failed(
        self,
        error: str = "长期记忆运行时暂时不可用。",
        *,
        category: str = "request_failed",
        error_type: str = "RuntimeError",
        request: str = "unknown",
    ) -> None:
        self._owner._mark_runtime_failed(
            error,
            category=category,
            error_type=error_type,
            request=request,
        )


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


def _local_embedding_model_kwargs(
    model_name: str,
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """只向 FastEmbed 传入固定的本地 ONNX snapshot，绝不隐式联网。"""

    snapshot = _embedding_model_snapshot(model_name, base_dir, cache_dir=cache_dir)
    if snapshot is None:
        snapshot = (
            _project_embedding_cache_folder(base_dir, cache_dir=cache_dir)
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


def _embedding_model_cached(
    model_name: str,
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> bool:
    """判断本地是否已有完整嵌入模型缓存，避免半下载缓存触发离线加载失败。"""

    return _embedding_model_cache_folder(
        model_name,
        base_dir,
        cache_dir=cache_dir,
    ) is not None


def _embedding_model_cache_folder(
    model_name: str,
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> Path | None:
    """返回包含固定 FastEmbed ONNX revision 的缓存根目录。"""

    snapshot = _embedding_model_snapshot(model_name, base_dir, cache_dir=cache_dir)
    return snapshot.parents[2] if snapshot is not None else None


def _embedding_model_snapshot(
    model_name: str,
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> Path | None:
    """返回已校验的固定 ONNX snapshot；其他 revision 和旧 PyTorch cache 均不命中。"""

    if model_name != DEFAULT_EMBEDDING_MODEL:
        return None
    for root in _embedding_model_cache_candidates(base_dir, cache_dir=cache_dir):
        snapshot = (
            root
            / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
            / "snapshots"
            / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
        if _fastembed_snapshot_is_complete(snapshot):
            return snapshot
    return None


def _embedding_model_cache_candidates(
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> list[Path]:
    """按加载优先级列出 Sakura 管理或显式覆盖的 FastEmbed 缓存目录。"""

    cache_candidates: list[Path] = []

    def add_candidate(path: Path) -> None:
        candidate = path.expanduser()
        if candidate not in cache_candidates:
            cache_candidates.append(candidate)

    cache_root = (os.environ.get("FASTEMBED_CACHE_PATH") or "").strip()
    if cache_root:
        add_candidate(Path(cache_root))
    if cache_dir is not None:
        add_candidate(Path(cache_dir))
    elif base_dir is not None:
        add_candidate(StoragePaths(Path(base_dir)).memory_cache_dir)
    return cache_candidates


def _project_embedding_cache_folder(
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
) -> Path:
    """返回 Sakura 自己管理的 FastEmbed/Hugging Face snapshot 缓存目录。"""

    if cache_dir is not None:
        return Path(cache_dir)
    root = _resolve_base_dir(base_dir)
    return StoragePaths(root).memory_cache_dir


def import_embedding_model_archive(
    path: Path,
    base_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> EmbeddingModelImportResult:
    """导入 all-MiniLM-L6-v2 的固定 FastEmbed ONNX snapshot ZIP。"""

    _check_model_task_cancelled(cancel)
    _report_model_progress(progress, "validating", 5)
    archive_path = Path(path)
    if not archive_path.exists():
        raise FileNotFoundError(f"记忆模型包不存在：{archive_path}")
    destination_root = _project_embedding_cache_folder(
        base_dir,
        cache_dir=cache_dir,
    )
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
            if not snapshot.is_dir() or not all(
                (snapshot / filename).is_file()
                for filename in DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES
            ):
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
    cache_dir: Path | None = None,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> EmbeddingModelImportResult:
    """下载固定 all-MiniLM-L6-v2 ONNX revision 到 Sakura 管理的缓存。"""

    destination_root = _project_embedding_cache_folder(
        base_dir,
        cache_dir=cache_dir,
    )
    temp_root = destination_root / (
        f".memory_model_download_{int(time.time() * 1000)}_{threading.get_ident()}"
    )
    staging_root = temp_root / "hub"
    staging_model_dir = staging_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    destination_model_dir = destination_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    backup_model_dir = destination_root / f".{DEFAULT_EMBEDDING_MODEL_CACHE_NAME}.backup"
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        _check_model_task_cancelled(cancel)
        _report_model_progress(progress, "connecting", 5)
        temp_root.mkdir(parents=True, exist_ok=False)
        try:
            _download_modelscope_snapshot(
                staging_model_dir / "snapshots" / DEFAULT_EMBEDDING_ARTIFACT_REVISION,
                progress=progress,
                cancel=cancel,
            )
        except MemoryModelTaskCancelled:
            raise
        except MemoryModelImportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MemoryModelImportError(
                "记忆模型在线安装失败，请检查 ModelScope 访问、网络或代理后重试。",
                code="DOWNLOAD_NETWORK_FAILED",
            ) from exc
        _check_model_task_cancelled(cancel)
        snapshot = (
            staging_model_dir
            / "snapshots"
            / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
        if not _fastembed_snapshot_is_complete(snapshot):
            raise MemoryModelImportError(
                "记忆模型下载后仍不完整：缺少固定 revision 的 model.onnx 或 tokenizer/config 文件。",
                code="DOWNLOAD_INCOMPLETE",
            )
        _validate_fastembed_snapshot_artifacts(snapshot)
        _report_model_progress(progress, "installing", 90)
        try:
            _replace_embedding_model_dir(
                staging_model_dir,
                destination_model_dir,
                backup_model_dir,
            )
        except OSError as exc:
            raise MemoryModelImportError(
                "记忆模型安装目录正被占用或不可写。",
                code="INSTALL_TARGET_BUSY",
            ) from exc
        _report_model_progress(progress, "completed", 100)
    except MemoryModelTaskCancelled:
        raise
    except MemoryModelImportError:
        raise
    except PermissionError as exc:
        raise MemoryModelImportError(
            "记忆模型安装目录正被占用或不可写。",
            code="INSTALL_TARGET_BUSY",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise MemoryModelImportError(
            "记忆模型安装过程发生内部错误。",
            code="DOWNLOAD_FAILED",
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


def _download_modelscope_snapshot(
    snapshot: Path,
    *,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """从固定 ModelScope revision 下载可由 FastEmbed 加载的 ONNX 工件。"""

    snapshot.mkdir(parents=True, exist_ok=False)
    total_bytes = sum(size for _, size, _ in MODELSCOPE_EMBEDDING_MODEL_ARTIFACTS.values())
    downloaded_bytes = 0
    for local_name, (remote_name, expected_size, _expected_sha256) in (
        MODELSCOPE_EMBEDDING_MODEL_ARTIFACTS.items()
    ):
        _check_model_task_cancelled(cancel)
        quoted_path = urllib.parse.quote(remote_name, safe="/")
        url = (
            f"{DEFAULT_MODELSCOPE_ENDPOINT}/models/{DEFAULT_MODELSCOPE_EMBEDDING_REPO}/"
            f"resolve/{DEFAULT_MODELSCOPE_EMBEDDING_REVISION}/{quoted_path}"
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Sakura-Desktop-Pet/1.0"},
        )
        target = snapshot / local_name
        try:
            with urllib.request.urlopen(request, timeout=600) as response, target.open("wb") as output:
                written = 0
                while chunk := response.read(512 * 1024):
                    output.write(chunk)
                    written += len(chunk)
                    downloaded_bytes += len(chunk)
                    _check_model_task_cancelled(cancel)
                    percent = 10 + min(75, int((downloaded_bytes / total_bytes) * 75))
                    _report_model_progress(progress, "downloading", percent)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise MemoryModelImportError(
                "无法连接 ModelScope 下载记忆模型。",
                code="DOWNLOAD_NETWORK_FAILED",
            ) from exc
        if written != expected_size:
            raise MemoryModelImportError(
                f"记忆 ONNX 模型文件大小不匹配：{local_name}",
                code="DOWNLOAD_SIZE_MISMATCH",
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
    if stage in {"embedding_wait", "embedding_warmup", "model_load", "dependency_import"}:
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

    if not snapshot.is_dir():
        return False
    return any(
        all(
            (snapshot / filename).is_file()
            and (snapshot / filename).stat().st_size == expected_size
            for filename, (expected_size, _expected_sha256) in artifacts.items()
        )
        for artifacts in _embedding_model_artifact_layouts()
    )


def _validate_fastembed_snapshot_artifacts(snapshot: Path) -> None:
    """按固定 size/SHA-256 校验 ONNX 工件，避免错误 ZIP 替换可读缓存。"""

    matching_layout = next(
        (
            artifacts
            for artifacts in _embedding_model_artifact_layouts()
            if all(
                (snapshot / filename).is_file()
                and (snapshot / filename).stat().st_size == expected_size
                for filename, (expected_size, _expected_sha256) in artifacts.items()
            )
        ),
        None,
    )
    if matching_layout is None:
        raise MemoryModelImportError(
            "记忆 ONNX 模型文件大小不匹配。",
            code="DOWNLOAD_SIZE_MISMATCH",
        )

    for filename, (expected_size, expected_sha256) in matching_layout.items():
        path = snapshot / filename
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise MemoryModelImportError(
                f"记忆 ONNX 模型文件校验失败：{filename}",
                code="DOWNLOAD_CHECKSUM_MISMATCH",
            )


def _embedding_model_artifact_layouts() -> tuple[dict[str, tuple[int, str]], ...]:
    modelscope_layout = {
        local_name: (size, sha256)
        for local_name, (_remote_name, size, sha256) in (
            MODELSCOPE_EMBEDDING_MODEL_ARTIFACTS.items()
        )
    }
    return DEFAULT_EMBEDDING_MODEL_ARTIFACTS, modelscope_layout


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
    source_entry_ids = _merged_source_entry_ids(
        existing_metadata.get("source_entry_ids"),
        arguments.get("source_entry_ids"),
    )
    if source_entry_ids:
        metadata["source_entry_ids"] = source_entry_ids
    for key in ("source_turn_id", "created_in_turn_id"):
        value = _optional_text(arguments, key)
        if value:
            if len(value) > MAX_MEMORY_SOURCE_ENTRY_ID_CHARS:
                raise ValueError(f"{key} contains an invalid Timeline turn ID")
            metadata[key] = value
    evidence_kind = _optional_text(arguments, "evidence_kind")
    if evidence_kind:
        if evidence_kind not in {"human", "observation", "mixed"}:
            raise ValueError("evidence_kind is invalid")
        metadata["evidence_kind"] = evidence_kind
    return metadata


def _merged_source_entry_ids(existing: object, added: object) -> list[str]:
    result: list[str] = []
    for value, strict in ((existing, False), (added, True)):
        if value is None:
            continue
        if not isinstance(value, (list, tuple, set)):
            if not strict:
                continue
            raise ValueError("source_entry_ids must be a list of Timeline entry IDs")
        for item in value:
            if not isinstance(item, str) or not item or len(item) > MAX_MEMORY_SOURCE_ENTRY_ID_CHARS:
                if not strict:
                    continue
                raise ValueError("source_entry_ids contains an invalid Timeline entry ID")
            if item not in result:
                result.append(item)
    return result[-MAX_MEMORY_SOURCE_ENTRY_IDS:]


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


def _memory_result_with_requested_fallback(
    raw: Any,
    requested_metadata: dict[str, Any],
    *,
    default_scope: str,
    fallback_content: str,
    fallback_id: str = "",
) -> dict[str, Any]:
    candidates = _raw_memory_candidates(raw)
    raw_candidate = dict(candidates[0]) if candidates and isinstance(candidates[0], dict) else {}
    returned_metadata = _metadata_mapping(raw_candidate)
    raw_candidate["metadata"] = {**requested_metadata, **returned_metadata}
    for key, requested_value in requested_metadata.items():
        if key not in raw_candidate and key not in returned_metadata:
            raw_candidate[key] = requested_value
    raw_candidate.setdefault("content", fallback_content)
    raw_candidate.setdefault("memory", fallback_content)
    if fallback_id:
        raw_candidate.setdefault("id", fallback_id)
    return _normalize_memory_record(raw_candidate, default_scope=default_scope) or raw_candidate


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
