from __future__ import annotations

import logging
import json
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
from typing import TYPE_CHECKING, Any, Callable, Iterable

from app.core.resource_manager import (
    DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ResourceRegistry,
    ThreadGroupResource,
)
from app.storage.atomic import atomic_write_text, rename_with_retry
from app.storage.chat_history import ChatHistoryEntry
from app.storage.paths import StoragePaths

if TYPE_CHECKING:
    from app.llm.api_client import ApiSettings


logger = logging.getLogger(__name__)

MEM0_VENDOR_ROOT = Path(__file__).resolve().parents[2] / "third_party" / "mem0"
DEFAULT_MEMORY_SCOPE = "sakura"
DEFAULT_COLLECTION_NAME = "sakura_memories"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMS = 384
DEFAULT_MEMORY_LIMIT = 20
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
DEFAULT_EMBEDDING_MODEL_CACHE_NAME = "models--" + DEFAULT_EMBEDDING_MODEL.replace("/", "--")
DEFAULT_EMBEDDING_MODEL_ALLOW_PATTERNS = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "README.md",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
_MEM0_CREATE_LOCK = threading.Lock()
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
os.environ.setdefault("MEM0_TELEMETRY", "False")
DEFAULT_MEMORY_LANGUAGE_INSTRUCTIONS = (
    "Sakura 的长期记忆必须使用简体中文记录。"
    "无论用户或助手消息使用什么语言，都要把可记忆事实翻译、归纳为自然的简体中文；"
    "技术名词、代码标识符、专有名词、路径、ID 和品牌名可保留原文。"
    "输出 JSON 结构不变，只改变 memory/text 字段的自然语言内容。"
)


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
    _thread_group: ThreadGroupResource = field(init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_dir = _resolve_base_dir(self.base_dir)
        self.scope_id = _normalize_scope_id(self.scope_id)
        self.resource_registry = self.resource_registry or ResourceRegistry()
        self._thread_group = self.resource_registry.track_thread_group(
            label="memory_store",
            shutdown_order=1000,
        )
        if self.memory_client is not None:
            self._memory = self.memory_client
            self._status = "ready"
            self._status_message = "长期记忆系统已就绪。"

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

    def scoped(self, scope_id: str) -> "MemoryStore":
        """Return an independent, fixed-scope view of this store.

        Callers that need another character's memories must not temporarily
        mutate ``scope_id`` on the shared host store: desktop workers may be
        reading it at the same time. The view can share an already-created
        mem0 client, while keeping all scope selection local to the caller.
        """
        return MemoryStore(
            base_dir=self.base_dir,
            api_settings=self.api_settings,
            scope_id=scope_id,
            memory_client=self._memory or self.memory_client,
        )

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
        self._thread_group.stop(DEFAULT_THREAD_SHUTDOWN_WAIT_MS)
        _close_memory_client(old_memory)

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

    def import_embedding_model_archive(self, path: Path) -> EmbeddingModelImportResult:
        """导入离线嵌入模型 ZIP，并重置长期记忆运行时以复用新缓存。"""

        result = import_embedding_model_archive(path, self.base_dir)
        if not self.is_ready():
            self.reset_runtime()
            self.preload(wait=False)
        return result

    def download_embedding_model(self) -> EmbeddingModelImportResult:
        """在线安装记忆嵌入模型，并重置长期记忆运行时以复用新缓存。"""

        result = download_embedding_model(self.base_dir)
        if not self.is_ready():
            self.reset_runtime()
            self.preload(wait=False)
        return result

    def preload(self, *, wait: bool = False) -> None:
        """提前启动 mem0 加载，避免首次打开设置或聊天时才初始化。"""

        if wait:
            self._get_memory(wait=True)
            return
        with self._lock:
            if self._closed or self._memory is not None or self._loading:
                return
            if self._load_error:
                self._load_error = ""
            status_event = self._start_loading_locked()
        self._notify_status_event(status_event)

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
                    llm_config, llm = self._create_memory_llm(api_settings)
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
        self._notify_status_event(status_event)

        def reload() -> None:
            try:
                if reload_llm_only:
                    llm_config, llm = self._create_memory_llm(api_settings)
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
        qdrant_path.mkdir(parents=True, exist_ok=True)
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
                "provider": "huggingface",
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
            previous = _normalize_memory_record(mem.get(memory_id), default_scope=self.scope_id)
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
        previous = _normalize_memory_record(mem.get(memory_id), default_scope=self.scope_id)
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
        previous = _normalize_memory_record(mem.get(memory_id), default_scope=self.scope_id)
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
        previous = _normalize_memory_record(mem.get(memory_id), default_scope=self.scope_id)
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

        db = getattr(mem, "db", None)
        connection = getattr(db, "connection", None)
        if connection is None:
            return {"messages": 0, "history": 0}

        clean_memory_ids = [
            memory_id
            for memory_id in (str(item).strip() for item in (memory_ids or []))
            if memory_id
        ]
        session_scope = _mem0_session_scope({"user_id": self.scope_id})
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
        except (sqlite3.Error, RuntimeError) as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            logger.warning("mem0 整理缓存清理失败：%s", exc)
            return {"messages": 0, "history": 0}

        return {"messages": deleted_messages, "history": deleted_history}

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
                error_message = _format_memory_load_error(
                    exc,
                    embedding_download=report_dependency_loading,
                )
                with self._lock:
                    if generation == self._reload_generation:
                        self._load_error = error_message
                        self._loading = False
                if report_dependency_loading:
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
            if report_dependency_loading:
                self._publish_status("ready", "长期记忆系统已就绪。")

        thread = self._thread_group.spawn(
            load,
            name="sakura-mem0-loader",
            daemon=True,
        )
        if thread is None:
            self._loading = False
        return status_event

    def _create_memory_client(self, api_settings: "ApiSettings | None" = None) -> Any:
        with _MEM0_CREATE_LOCK:
            install_mem0_vendor()
            from mem0 import Memory

            return Memory.from_config(self.build_mem0_config(api_settings))

    def _supports_memory_llm_reload(self, memory: Any | None) -> bool:
        if memory is None:
            return False
        config = getattr(memory, "config", None)
        return hasattr(memory, "llm") and hasattr(config, "llm")

    def _create_memory_llm(self, api_settings: "ApiSettings") -> tuple[Any, Any]:
        """只按新 API 设置重建 mem0 的 LLM，避免重开本地 Qdrant 客户端。"""

        with _MEM0_CREATE_LOCK:
            install_mem0_vendor()
            from mem0.llms.configs import LlmConfig
            from mem0.utils.factory import LlmFactory

            llm_section = self.build_mem0_config(api_settings)["llm"]
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
                f"记忆系统正在初始化（已等待 {elapsed} 秒）。"
                "请告诉主人记忆系统稍后就绪，不要连续重复调用记忆工具。"
            ),
            "memories": [],
        }

    def _failed_response(self, error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "message": (
                "长期记忆系统暂时不可用。请告诉主人普通聊天仍可继续，"
                "不要重复调用记忆工具。"
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


def _local_embedding_model_kwargs(model_name: str, base_dir: Path | None = None) -> dict[str, Any]:
    """优先复用本地模型；缺失时下载到项目缓存。"""

    cache_folder = _embedding_model_cache_folder(model_name, base_dir)
    if cache_folder is not None:
        return {"cache_folder": str(cache_folder), "local_files_only": True}
    return {"cache_folder": str(_project_embedding_cache_folder(base_dir))}


def _embedding_model_cached(model_name: str, base_dir: Path | None = None) -> bool:
    """判断本地是否已有完整嵌入模型缓存，避免半下载缓存触发离线加载失败。"""

    return _embedding_model_cache_folder(model_name, base_dir) is not None


def _embedding_model_cache_folder(model_name: str, base_dir: Path | None = None) -> Path | None:
    """返回已命中的 HuggingFace 缓存根目录，供 SentenceTransformer 离线加载复用。"""

    model_cache_name = "models--" + model_name.replace("/", "--")
    for root in _embedding_model_cache_candidates(base_dir):
        snapshot_dir = root / model_cache_name / "snapshots"
        if _hub_snapshot_has_model_weights(snapshot_dir):
            return root
    return None


def _embedding_model_cache_candidates(base_dir: Path | None = None) -> list[Path]:
    """按加载优先级列出可能包含 hub 模型快照的缓存目录。"""

    cache_root = (
        os.environ.get("SENTENCE_TRANSFORMERS_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("TRANSFORMERS_CACHE")
    )
    cache_candidates: list[Path] = []

    def add_candidate(path: Path) -> None:
        candidate = path.expanduser()
        if candidate not in cache_candidates:
            cache_candidates.append(candidate)

    if cache_root:
        cache_path = Path(cache_root)
        add_candidate(cache_path)
        add_candidate(cache_path / "hub")
    if base_dir is not None:
        runtime_cache = Path(base_dir) / "runtime" / "hf-cache"
        add_candidate(runtime_cache)
        add_candidate(runtime_cache / "hub")
    hf_home = (os.environ.get("HF_HOME") or "").strip()
    default_hf_home = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    add_candidate(default_hf_home / "hub")
    return cache_candidates


def _project_embedding_cache_folder(base_dir: Path | None = None) -> Path:
    """返回 Sakura 自己管理的 HuggingFace hub 缓存目录。"""

    root = _resolve_base_dir(base_dir)
    return root / "runtime" / "hf-cache" / "hub"


def import_embedding_model_archive(path: Path, base_dir: Path | None = None) -> EmbeddingModelImportResult:
    """导入 all-MiniLM-L6-v2 的 HuggingFace hub 缓存 ZIP。"""

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
            model_prefix = _validate_embedding_model_zip_members(zf)
            temp_root.mkdir(parents=True, exist_ok=False)
            _extract_embedding_model_zip(zf, model_prefix, staging_model_dir)
            snapshot_dir = staging_model_dir / "snapshots"
            if not _hub_snapshot_has_model_weights(snapshot_dir):
                raise MemoryModelImportError(
                    "记忆模型包不完整：snapshots/ 下未找到 model.safetensors 或 pytorch_model.bin。"
                )

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
    except zipfile.BadZipFile as exc:
        raise MemoryModelImportError("不是有效的记忆模型 ZIP 包。") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    snapshot_count = sum(
        1
        for child in (destination_model_dir / "snapshots").iterdir()
        if child.is_dir()
    )
    return EmbeddingModelImportResult(
        model_name=DEFAULT_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=destination_model_dir,
        snapshot_count=snapshot_count,
    )


def download_embedding_model(base_dir: Path | None = None) -> EmbeddingModelImportResult:
    """下载 all-MiniLM-L6-v2 到 Sakura 管理的 HuggingFace hub 缓存。"""

    destination_root = _project_embedding_cache_folder(base_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        _download_hf_snapshot(DEFAULT_EMBEDDING_MODEL, destination_root)
    except MemoryModelImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MemoryModelImportError(
            "记忆模型在线安装失败，请检查 HuggingFace 访问、网络或代理后重试。"
            f"\n\n原始错误：{exc}"
        ) from exc

    model_dir = destination_root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    snapshot_dir = model_dir / "snapshots"
    if not _hub_snapshot_has_model_weights(snapshot_dir):
        raise MemoryModelImportError(
            "记忆模型下载后仍不完整：snapshots/ 下未找到 model.safetensors 或 pytorch_model.bin。"
        )
    snapshot_count = sum(1 for child in snapshot_dir.iterdir() if child.is_dir())
    return EmbeddingModelImportResult(
        model_name=DEFAULT_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=model_dir,
        snapshot_count=snapshot_count,
    )


def _download_hf_snapshot(repo_id: str, cache_folder: Path) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise MemoryModelImportError("缺少 huggingface_hub 依赖，无法在线安装记忆模型。") from exc
    return str(
        snapshot_download(
            repo_id=repo_id,
            cache_dir=str(cache_folder),
            endpoint=(os.environ.get("HF_ENDPOINT") or DEFAULT_HUGGINGFACE_ENDPOINT).strip(),
            allow_patterns=list(DEFAULT_EMBEDDING_MODEL_ALLOW_PATTERNS),
            local_files_only=False,
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
        PurePosixPath("hub", DEFAULT_EMBEDDING_MODEL_CACHE_NAME),
        PurePosixPath("hf-cache", "hub", DEFAULT_EMBEDDING_MODEL_CACHE_NAME),
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
) -> None:
    """只把目标模型目录抽取到 staging 目录，避免 zipfile.extractall 的路径风险。"""

    destination_model_dir.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
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
            shutil.copyfileobj(source, output)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _format_memory_load_error(exc: Exception, *, embedding_download: bool) -> str:
    raw_message = str(exc).strip() or exc.__class__.__name__
    if not embedding_download:
        return f"长期记忆系统初始化失败：{raw_message}"
    return (
        "长期记忆系统初始化失败：本地嵌入模型下载失败，"
        "请前往项目 Release 下载 models--sentence-transformers--all-MiniLM-L6-v2.zip，"
        "然后在设置页手动导入：\n"
        "https://github.com/Rvosy/Sakura/releases/download/v0.9.7/"
        "models--sentence-transformers--all-MiniLM-L6-v2.zip\n"
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
    vector_store = getattr(memory, "vector_store", None)
    client = getattr(vector_store, "client", None)
    client_close = getattr(client, "close", None)
    if callable(client_close):
        try:
            client_close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭 Qdrant 客户端失败", exc_info=True)


def _hub_snapshot_has_model_weights(snapshot_dir: Path) -> bool:
    """确认 HuggingFace snapshot 至少包含可加载的模型权重。"""

    if not snapshot_dir.is_dir():
        return False
    weight_filenames = {
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    }
    for revision_dir in snapshot_dir.iterdir():
        if not revision_dir.is_dir():
            continue
        if any((revision_dir / filename).is_file() for filename in weight_filenames):
            return True
    return False


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
    if mode in {"tool", "proactive"}:
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
