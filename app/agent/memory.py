from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

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
DEFAULT_HUGGINGFACE_ENDPOINT = "https://huggingface.co"
_MEM0_CREATE_LOCK = threading.Lock()
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
    _status: str = field(default="idle", init=False, repr=False)
    _status_message: str = field(default="", init=False, repr=False)
    _status_listeners: list[Callable[[str, str], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_dir = _resolve_base_dir(self.base_dir)
        self.scope_id = _normalize_scope_id(self.scope_id)
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
            if self._memory is not None:
                self._status = "ready"
                self._status_message = "长期记忆系统已就绪。"
            else:
                self._status = "idle"
                self._status_message = ""

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
            status_event = self._start_loading_locked()
        self._notify_status_event(status_event)

    def reload_api_settings(self, api_settings: "ApiSettings", *, wait: bool = False) -> None:
        """后台使用新 API 配置重建 mem0，成功前保留旧实例继续服务。"""

        with self._lock:
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
            with self._lock:
                if generation != self._reload_generation:
                    return
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
                    "model_kwargs": _local_embedding_model_kwargs(DEFAULT_EMBEDDING_MODEL, self.base_dir),
                },
            },
            "history_db_path": str(memory_dir / "mem0_history.db"),
            "custom_instructions": DEFAULT_MEMORY_LANGUAGE_INSTRUCTIONS,
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
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
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
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
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
        cache_reset = self._reset_scope_curation_cache(mem, memory_ids=[memory_id])
        return {"memory": previous or {"id": memory_id, "content": ""}, "curation_cache_reset": cache_reset}

    def forget_memory(self, arguments: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        memory_id = _required_text(arguments, "id")
        try:
            mem = self._get_memory(wait=wait)
        except RuntimeError as exc:
            if wait:
                raise
            return self._failed_response(str(exc))
        if mem is None:
            return self._loading_response()
        previous = _normalize_memory_record(mem.get(memory_id))
        mem.delete(memory_id)
        cache_reset = self._reset_scope_curation_cache(mem, memory_ids=[memory_id])
        forgotten = previous or {"id": memory_id, "content": ""}
        return {"forgotten": forgotten, "memory": forgotten, "curation_cache_reset": cache_reset}

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
            with self._lock:
                if generation != self._reload_generation or self.api_settings != api_settings:
                    self._loading = False
                    return
                self._memory = mem
                self._loading = False
            if report_dependency_loading:
                self._publish_status("ready", "长期记忆系统已就绪。")

        thread = threading.Thread(target=load, name="sakura-mem0-loader", daemon=True)
        thread.start()
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


def _format_memory_load_error(exc: Exception, *, embedding_download: bool) -> str:
    raw_message = str(exc).strip() or exc.__class__.__name__
    if not embedding_download:
        return f"长期记忆系统初始化失败：{raw_message}"
    return (
        "长期记忆系统初始化失败：本地嵌入模型下载失败，"
        "请检查 HuggingFace 访问、网络或代理后重试；普通聊天仍可继续。"
        f"\n\n原始错误：{raw_message}"
    )


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
