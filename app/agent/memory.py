from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.storage.chat_history import ChatHistoryEntry

if TYPE_CHECKING:
    from app.llm.api_client import ApiSettings


logger = logging.getLogger(__name__)

MEM0_VENDOR_ROOT = Path(__file__).resolve().parents[2] / "third_party" / "mem0"
DEFAULT_MEMORY_SCOPE = "sakura"
DEFAULT_COLLECTION_NAME = "sakura_memories"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMS = 384
DEFAULT_MEMORY_LIMIT = 20


def install_mem0_vendor() -> Path:
    """优先把仓库内置的 mem0 放到导入路径最前面。"""

    vendor_path = str(MEM0_VENDOR_ROOT)
    if MEM0_VENDOR_ROOT.exists():
        if vendor_path in sys.path:
            sys.path.remove(vendor_path)
        sys.path.insert(0, vendor_path)
    return MEM0_VENDOR_ROOT


install_mem0_vendor()


@dataclass
class MemoryCurationCounts:
    """mem0 写入结果的轻量统计。"""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    ignored: int = 0
    total: int = 0


@dataclass
class MemoryStore:
    """Sakura 对本地内置 mem0 的适配层。"""

    base_dir: Path | None = None
    api_settings: "ApiSettings | None" = None
    scope_id: str = DEFAULT_MEMORY_SCOPE
    memory_client: Any | None = None
    _memory: Any | None = field(default=None, init=False, repr=False)
    _loading: bool = field(default=False, init=False, repr=False)
    _loading_started_at: float = field(default=0.0, init=False, repr=False)
    _load_error: str = field(default="", init=False, repr=False)
    _reloading: bool = field(default=False, init=False, repr=False)
    _reload_error: str = field(default="", init=False, repr=False)
    _reload_generation: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_dir = _resolve_base_dir(self.base_dir)
        self.scope_id = _normalize_scope_id(self.scope_id)
        if self.memory_client is not None:
            self._memory = self.memory_client

    def set_scope(self, scope_id: str) -> None:
        """切换角色后更新 mem0 user_id 作用域。"""

        self.scope_id = _normalize_scope_id(scope_id)

    def set_api_settings(self, api_settings: "ApiSettings") -> None:
        """API 设置变更后重置 mem0，下次使用新配置重新初始化。"""

        if self.api_settings == api_settings:
            return
        self.api_settings = api_settings
        self.reset_runtime()

    def reset_runtime(self) -> None:
        with self._lock:
            self._memory = self.memory_client
            self._loading = False
            self._loading_started_at = 0.0
            self._load_error = ""
            self._reloading = False
            self._reload_error = ""
            self._reload_generation += 1

    def is_ready(self) -> bool:
        """返回长期记忆运行时是否已经可直接使用。"""

        with self._lock:
            return self._memory is not None

    def preload(self, *, wait: bool = False) -> None:
        """提前启动 mem0 加载，避免首次打开设置或聊天时才初始化。"""

        if wait:
            self._get_memory(wait=True)
            return
        with self._lock:
            if self._memory is not None or self._loading:
                return
            if self._load_error:
                self._load_error = ""
            self._start_loading_locked()

    def reload_api_settings(self, api_settings: "ApiSettings", *, wait: bool = False) -> None:
        """后台使用新 API 配置重建 mem0，成功前保留旧实例继续服务。"""

        with self._lock:
            if self.api_settings == api_settings and self._memory is not None and not self._reload_error:
                return
            self.api_settings = api_settings
            self._reload_generation += 1
            generation = self._reload_generation
            self._reload_error = ""

        if wait:
            try:
                memory = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 后台重载失败")
                with self._lock:
                    if generation == self._reload_generation:
                        self._reload_error = str(exc)
                return
            with self._lock:
                if generation == self._reload_generation:
                    self._memory = memory
                    self._load_error = ""
                    self._loading = False
                    self._reloading = False
            return

        with self._lock:
            self._reloading = True

        def reload() -> None:
            try:
                memory = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 后台重载失败")
                with self._lock:
                    if generation == self._reload_generation:
                        self._reload_error = str(exc)
                        self._reloading = False
                return
            with self._lock:
                if generation != self._reload_generation:
                    return
                self._memory = memory
                self._load_error = ""
                self._reload_error = ""
                self._loading = False
                self._reloading = False

        thread = threading.Thread(target=reload, name="sakura-mem0-reloader", daemon=True)
        thread.start()

    def build_mem0_config(self, api_settings: "ApiSettings | None" = None) -> dict[str, Any]:
        """生成 mem0 配置：本地 Qdrant + Sakura 当前 OpenAI-compatible LLM。"""

        memory_dir = self.base_dir / "data" / "memory"
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
                    "model_kwargs": _local_embedding_model_kwargs(DEFAULT_EMBEDDING_MODEL),
                },
            },
            "history_db_path": str(memory_dir / "mem0_history.db"),
        }

    def summary(self, limit: int = 12) -> str:
        mem = self._get_memory(wait=False)
        if mem is None:
            return "长期记忆系统正在初始化。"
        raw = mem.get_all(filters={"user_id": self.scope_id}, top_k=limit)
        memories = _normalize_memory_results(raw)
        if not memories:
            return "暂无长期记忆。"
        lines = ["长期记忆："]
        for memory in memories:
            memory_id = str(memory.get("id", ""))
            content = str(memory.get("content", ""))
            lines.append(f"- [{memory_id}] {content}")
        return "\n".join(lines)

    def list_memories(self, *, limit: int = DEFAULT_MEMORY_LIMIT) -> list[dict[str, Any]]:
        mem = self._get_memory()
        raw = mem.get_all(filters={"user_id": self.scope_id}, top_k=limit)
        return _normalize_memory_results(raw)

    def search_memory(
        self,
        arguments: dict[str, Any],
        *,
        wait: bool = True,
    ) -> dict[str, Any]:
        query = _optional_text(arguments, "query") or _optional_text(arguments, "keyword")
        limit = _positive_int(arguments.get("limit") or arguments.get("top_k"), DEFAULT_MEMORY_LIMIT)
        mem = self._get_memory(wait=wait)
        if mem is None:
            return self._loading_response()
        raw = (
            mem.get_all(filters={"user_id": self.scope_id}, top_k=limit)
            if not query
            else mem.search(query, filters={"user_id": self.scope_id}, top_k=limit)
        )
        memories = _normalize_memory_results(raw)
        return {
            "agent_id": self.scope_id,
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
        _ = allow_sensitive
        content = _required_text(arguments, "content")
        mem = self._get_memory(wait=wait)
        if mem is None:
            return self._loading_response()
        metadata = _memory_metadata(arguments)
        raw = mem.add(content, user_id=self.scope_id, metadata=metadata or None, infer=False)
        memory = _first_memory_result(raw) or {"content": content, "memory": content}
        return {"memory": memory, "ok": True}

    def remember_memory(self, arguments: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        return self.create_memory(arguments, allow_sensitive=True, wait=wait)

    def update_memory(
        self,
        arguments: dict[str, Any],
        *,
        allow_sensitive: bool = False,
    ) -> dict[str, Any]:
        _ = allow_sensitive
        memory_id = _required_text(arguments, "id")
        content = _required_text(arguments, "content")
        mem = self._get_memory()
        metadata = _memory_metadata(arguments)
        raw = mem.update(memory_id, content, metadata=metadata or None)
        current = _normalize_memory_record(mem.get(memory_id))
        memory = current or _first_memory_result(raw) or {"id": memory_id, "content": content, "memory": content}
        return {"memory": memory}

    def delete_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        mem = self._get_memory()
        previous = _normalize_memory_record(mem.get(memory_id))
        mem.delete(memory_id)
        return {"memory": previous or {"id": memory_id, "content": ""}}

    def forget_memory(self, arguments: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        mem = self._get_memory(wait=wait)
        if mem is None:
            return self._loading_response()
        previous = _normalize_memory_record(mem.get(memory_id))
        mem.delete(memory_id)
        forgotten = previous or {"id": memory_id, "content": ""}
        return {"forgotten": forgotten, "memory": forgotten}

    def add_history_entries(self, entries: list[ChatHistoryEntry]) -> MemoryCurationCounts:
        messages = _entries_for_mem0(entries)
        if not messages:
            return MemoryCurationCounts(total=len(entries))
        mem = self._get_memory()
        raw = mem.add(messages, user_id=self.scope_id, infer=True)
        return _count_mem0_events(raw, total=len(messages))

    def _get_memory(self, *, wait: bool = True) -> Any | None:
        with self._lock:
            if self._memory is not None:
                return self._memory
            if self._load_error and not self._loading:
                raise RuntimeError(self._load_error)
            if not self._loading:
                self._start_loading_locked()
            if not wait:
                return None

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

    def _start_loading_locked(self) -> None:
        self._loading = True
        self._loading_started_at = time.time()
        self._load_error = ""
        generation = self._reload_generation
        api_settings = self.api_settings

        def load() -> None:
            try:
                mem = self._create_memory_client(api_settings)
            except Exception as exc:
                logger.exception("mem0 初始化失败")
                with self._lock:
                    if generation == self._reload_generation:
                        self._load_error = str(exc)
                        self._loading = False
                return
            with self._lock:
                if generation != self._reload_generation or self.api_settings != api_settings:
                    self._loading = False
                    return
                self._memory = mem
                self._loading = False

        thread = threading.Thread(target=load, name="sakura-mem0-loader", daemon=True)
        thread.start()

    def _create_memory_client(self, api_settings: "ApiSettings | None" = None) -> Any:
        install_mem0_vendor()
        from mem0 import Memory

        return Memory.from_config(self.build_mem0_config(api_settings))

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


def _local_embedding_model_kwargs(model_name: str) -> dict[str, Any]:
    """本地已有 HuggingFace 缓存时禁止联网探测，避免设置页反复卡顿。"""

    cache_root = (
        os.environ.get("SENTENCE_TRANSFORMERS_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("TRANSFORMERS_CACHE")
    )
    cache_candidates: list[Path] = []
    if cache_root:
        cache_path = Path(cache_root)
        cache_candidates.extend([cache_path, cache_path / "hub"])
    default_hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    cache_candidates.append(default_hf_home / "hub")

    model_cache_name = "models--" + model_name.replace("/", "--")
    for root in cache_candidates:
        snapshot_dir = root / model_cache_name / "snapshots"
        if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
            return {"local_files_only": True}
    return {}


def _normalize_memory_results(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        candidates = raw.get("results") or raw.get("memories") or []
    else:
        candidates = raw
    if not isinstance(candidates, list):
        return []
    memories: list[dict[str, Any]] = []
    for item in candidates:
        memory = _normalize_memory_record(item)
        if memory is not None:
            memories.append(memory)
    return memories


def _normalize_memory_record(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("memory") or raw.get("content") or raw.get("data") or "").strip()
    memory_id = str(raw.get("id") or raw.get("memory_id") or "").strip()
    if not content and not memory_id:
        return None
    memory = dict(raw)
    memory["id"] = memory_id
    memory["content"] = content
    memory["memory"] = content
    return memory


def _first_memory_result(raw: Any) -> dict[str, Any] | None:
    memories = _normalize_memory_results(raw)
    return memories[0] if memories else _normalize_memory_record(raw)


def _memory_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("category", "importance", "confidence", "source"):
        value = arguments.get(key)
        if value not in (None, ""):
            metadata[key] = value
    return metadata


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
    if not results:
        counts.ignored = total
        return counts
    for item in results:
        event = str(item.get("event") or item.get("action") or "").upper()
        if event in {"ADD", "CREATE", "CREATED"}:
            counts.created += 1
        elif event in {"UPDATE", "UPDATED"}:
            counts.updated += 1
        elif event in {"DELETE", "ARCHIVE", "DELETED", "ARCHIVED"}:
            counts.deleted += 1
        else:
            counts.ignored += 1
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
