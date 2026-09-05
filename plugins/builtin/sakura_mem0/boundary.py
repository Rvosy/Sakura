"""Official Mem0 plugin owner over Sakura's existing Memory data and libraries.

The boundary is deliberately narrow: it owns the existing ``MemoryStore`` and
curation resources, validates the public protocol, and projects records into a
stable DTO.  It never imports the legacy application bootstrap or a Qt worker.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Literal

try:
    from .memory import (
        DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MEMORY_CONFIDENCE,
        DEFAULT_MEMORY_IMPORTANCE,
        DEFAULT_MEMORY_LAYER,
        MEMORY_LAYERS,
        MemoryModelImportError,
        MemoryModelTaskCancelled,
        MemoryStore,
        append_memory_initialization_diagnostic,
    )
    from .api_client import (
        MAX_CURATION_HTTP_REQUESTS_PER_JOB,
        ApiSettings,
        OpenAICompatibleClient,
    )
    from .memory_curator import MemoryCurationState, MemoryCurator
    from .support import (
        OperationCancelled,
        ResourceRegistry,
        StoragePaths,
        interaction_context,
        log_event,
    )
    from .domain_types import ChatHistoryEntry
except ImportError:
    from memory import (
        DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MEMORY_CONFIDENCE,
        DEFAULT_MEMORY_IMPORTANCE,
        DEFAULT_MEMORY_LAYER,
        MEMORY_LAYERS,
        MemoryModelImportError,
        MemoryModelTaskCancelled,
        MemoryStore,
        append_memory_initialization_diagnostic,
    )
    from api_client import (
        MAX_CURATION_HTTP_REQUESTS_PER_JOB,
        ApiSettings,
        OpenAICompatibleClient,
    )
    from memory_curator import MemoryCurationState, MemoryCurator
    from support import (
        OperationCancelled,
        ResourceRegistry,
        StoragePaths,
        interaction_context,
        log_event,
    )
    from domain_types import ChatHistoryEntry


MEMORY_STATUSES = frozenset({"ready", "loading", "degraded", "read_only", "failed", "stopped"})
PLUGIN_MEMORY_REQUEST_TIMEOUT_SECONDS = 2.2
MAX_MEMORY_CONTENT = 16_384
MAX_MEMORY_QUERY = 4_000
MAX_MEMORY_TEXT_FIELD = 256
MAX_MEMORY_RESULTS = 120


class MemoryBoundaryError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        feature: str = "memory.manage",
        field: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.feature = feature
        self.field = field

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": {"feature": self.feature, "field": self.field},
        }


class MemoryBoundary:
    """Own one role-scoped MemoryStore for one Core generation."""

    def __init__(
        self,
        app_root: Path,
        character_id: str,
        *,
        system_prompt: str = "",
        memory_store: MemoryStore | None = None,
        memory_dir: Path | None = None,
        memory_cache_dir: Path | None = None,
        curation_config_getter: Callable[[], Mapping[str, object]] | None = None,
        model_catalog_getter: Callable[[], object] | None = None,
        model_resolver: Callable[[Mapping[str, object]], object] | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self._character_id = _required_text(character_id, "character_id", 128)
        self._system_prompt = system_prompt.strip()
        self._lock = threading.RLock()
        self._status_changed = threading.Condition(self._lock)
        self._write_lock = threading.Lock()
        self._closed = False
        self._status: Literal[
            "ready", "loading", "degraded", "read_only", "failed", "stopped"
        ] = "loading"
        self._message = "长期记忆系统正在初始化。"
        self._curation_cancel = threading.Event()
        self._curation_active = False
        self._curation_request_fuse_open = False
        self._pending_timeline: object | None = None
        self._model_task_active = False
        self._model_task_id = ""
        self._model_task_cancel = threading.Event()
        self._model_download_error_code = ""
        self._curation_config_getter = curation_config_getter or (lambda: {})
        self._model_catalog_getter = model_catalog_getter or (lambda: [])
        self._model_resolver = model_resolver or (lambda _selection: {})
        self._preload_started = False
        self._store_failed = False
        self._resources = ResourceRegistry()
        self._curation_threads = self._resources.track_thread_group(
            cancel=self._curation_cancel.set,
            label="runtime_v2_memory_curation",
            shutdown_order=1100,
        )
        paths = StoragePaths(self._app_root)
        self._memory_dir = Path(memory_dir or paths.memory_dir)
        self._memory_cache_dir = Path(memory_cache_dir or paths.memory_cache_dir)
        self._store = memory_store or MemoryStore(
            base_dir=self._app_root,
            scope_id=self._character_id,
            resource_registry=self._resources,
            request_timeout_seconds=PLUGIN_MEMORY_REQUEST_TIMEOUT_SECONDS,
            memory_dir=self._memory_dir,
            memory_cache_dir=self._memory_cache_dir,
        )
        self._store.add_status_listener(self._on_store_status)
        state_name = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in self._character_id
        )
        self._curation_state = MemoryCurationState(
            self._memory_dir / "curation_state" / f"{state_name}.json"
        )
        # Missing embeddings must never cause implicit network access.  The user
        # can explicitly start the fixed-model download from the settings page.
        store_ready = self._store.is_ready()
        model_cached = not self._store.needs_embedding_model_download()
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_created",
            stage="owner_create",
            outcome="completed",
            status="ready" if store_ready else "loading",
            model_cached=model_cached,
        )
        if store_ready:
            self._set_status("ready", "")
        elif not model_cached:
            self._set_status("degraded", "本地记忆模型尚未安装；聊天将继续但不会召回记忆。")
        else:
            self._start_preload()

    def _start_preload(self) -> None:
        with self._lock:
            if self._closed or self._preload_started:
                return
            self._preload_started = True
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_preload_requested",
            stage="preload",
            outcome="started",
            wait=False,
            model_cached=True,
        )
        try:
            self._store.preload(wait=False)
        except Exception as exc:
            append_memory_initialization_diagnostic(
                self._app_root,
                component="plugin_memory_owner",
                event="owner_preload_returned",
                stage="preload",
                outcome="failed",
                category="preload_call_failed",
                error_type=exc.__class__.__name__,
                wait=False,
            )
            self._set_status("degraded", "记忆暂时不可用；聊天不受影响。")
            return
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_preload_returned",
            stage="preload",
            outcome="scheduled",
            wait=False,
        )

    @property
    def memory_store(self) -> MemoryStore:
        return self._store

    @property
    def character_id(self) -> str:
        return self._character_id

    def __bool__(self) -> bool:
        return True

    def search_memory(
        self,
        arguments: dict[str, object],
        *,
        wait: bool = False,
    ) -> dict[str, object]:
        """MemoryRecallService-compatible degradation facade.

        ``wait`` is intentionally ignored: ordinary chat never blocks on the
        embedding/Qdrant owner and never turns a miss into implicit network I/O.
        """

        payload: dict[str, object] = {
            "query": arguments.get("query", ""),
            "limit": arguments.get("limit", 10),
        }
        if "layer" in arguments:
            payload["layer"] = arguments["layer"]
        return self.search(payload)

    def summary(self) -> str:
        return ""

    def status(self) -> dict[str, str]:
        promoted = False
        with self._lock:
            if (
                not self._closed
                and not self._store_failed
                and self._status in {"loading", "degraded"}
                and self._store.is_ready()
            ):
                self._status = "ready"
                self._message = ""
                promoted = True
                self._status_changed.notify_all()
            current = {"status": self._status, "message": self._message}
        if promoted:
            append_memory_initialization_diagnostic(
                self._app_root,
                component="plugin_memory_owner",
                event="owner_status_changed",
                stage="store_status",
                outcome="observed",
                status="ready",
            )
        return current

    def wait_until_settled(
        self,
        timeout: float,
        *,
        cancel_checker: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        """Wait boundedly for preload while keeping chat cancellation responsive."""

        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if cancel_checker is not None:
                cancel_checker()
            snapshot = self.status()
            if snapshot["status"] != "loading":
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return snapshot
            with self._status_changed:
                self._status_changed.wait(timeout=min(0.05, remaining))

    def prompt_dependency_snapshot(self) -> dict[str, object]:
        """Expose only stable, body-free startup diagnostics for prompt logging."""

        snapshot: dict[str, object] = dict(self.status())
        diagnostic_getter = getattr(self._store, "load_diagnostic", None)
        diagnostic = diagnostic_getter() if callable(diagnostic_getter) else {}
        if isinstance(diagnostic, Mapping):
            for source, target in (
                ("stage", "stage"),
                ("category", "category"),
                ("errorType", "error_type"),
            ):
                value = diagnostic.get(source)
                if isinstance(value, str) and value.strip():
                    snapshot[target] = value.strip()
        return snapshot

    def list_memories(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read manager/curation snapshots through one stable failure boundary."""

        if self.status()["status"] != "ready":
            raise MemoryBoundaryError(
                "MEMORY_NOT_READY",
                "记忆暂时不可用。",
                retryable=True,
            )
        try:
            return self._store.list_memories(limit=limit)
        except Exception as exc:
            with self._lock:
                self._store_failed = True
            self._set_status("degraded", "记忆读取暂时不可用；聊天不受影响。")
            raise MemoryBoundaryError(
                "MEMORY_READ_FAILED",
                "记忆读取暂时不可用。",
                retryable=False,
            ) from exc

    def search(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(payload, {"query", "limit", "layer"})
        query = _text(payload.get("query"), "query", MAX_MEMORY_QUERY)
        limit = _bounded_int(payload.get("limit"), "limit", 1, MAX_MEMORY_RESULTS)
        layer = payload.get("layer")
        if layer is not None and layer not in MEMORY_LAYERS:
            raise MemoryBoundaryError("FIELD_INVALID", "记忆分层无效。", field="layer")
        current = self.status()
        if current["status"] != "ready":
            return {**current, "memories": []}
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        if layer is not None:
            arguments["layer"] = layer
        try:
            result = self._store.search_memory(arguments, wait=False)
        except Exception:
            with self._lock:
                self._store_failed = True
            self._set_status("degraded", "记忆检索暂时不可用；聊天不受影响。")
            return {**self.status(), "memories": []}
        status = str(result.get("status") or "ready")
        if status != "ready":
            safe_status = status if status in MEMORY_STATUSES else "degraded"
            with self._lock:
                self._store_failed = True
            self._set_status(safe_status, "记忆检索暂时不可用；聊天不受影响。")
            return {**self.status(), "memories": []}
        memories = result.get("memories")
        if not isinstance(memories, list):
            with self._lock:
                self._store_failed = True
            self._set_status("degraded", "记忆检索暂时不可用；聊天不受影响。")
            return {**self.status(), "memories": []}
        projected = [
            record
            for item in memories
            if isinstance(item, Mapping) and (record := _project_memory(item, self._character_id))
        ]
        return {"status": "ready", "message": "", "memories": projected[:limit]}

    def upsert(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(
            payload,
            {
                "id",
                "content",
                "layer",
                "category",
                "source",
                "importance",
                "confidence",
                "source_turn_id",
                "source_entry_ids",
                "created_in_turn_id",
                "evidence_kind",
            },
        )
        self._assert_writable()
        content = _required_text(payload.get("content"), "content", MAX_MEMORY_CONTENT)
        layer = payload.get("layer", DEFAULT_MEMORY_LAYER)
        if layer not in MEMORY_LAYERS:
            raise MemoryBoundaryError("FIELD_INVALID", "记忆分层无效。", field="layer")
        arguments: dict[str, object] = {
            "content": content,
            "layer": layer,
            "category": _text(payload.get("category"), "category", MAX_MEMORY_TEXT_FIELD),
            "source": _text(payload.get("source"), "source", MAX_MEMORY_TEXT_FIELD) or "explicit",
            "importance": _bounded_number(
                payload.get("importance", DEFAULT_MEMORY_IMPORTANCE), "importance"
            ),
            "confidence": _bounded_number(
                payload.get("confidence", DEFAULT_MEMORY_CONFIDENCE), "confidence"
            ),
        }
        memory_id = _text(payload.get("id"), "id", 256)
        if memory_id:
            arguments["id"] = memory_id
        for key in (
            "source_turn_id",
            "source_entry_ids",
            "created_in_turn_id",
            "evidence_kind",
        ):
            if key in payload:
                arguments[key] = payload[key]
        try:
            with self._write_lock:
                result = (
                    self._store.update_memory(arguments, wait=True)
                    if memory_id
                    else self._store.create_memory(arguments, wait=True)
                )
        except ValueError as exc:
            raise MemoryBoundaryError("MEMORY_VALIDATION_FAILED", str(exc), field="content") from exc
        except Exception as exc:
            raise MemoryBoundaryError(
                "MEMORY_WRITE_FAILED", "记忆保存失败，原数据保持不变。", retryable=True
            ) from exc
        memory = result.get("memory")
        if not isinstance(memory, Mapping):
            raise MemoryBoundaryError("MEMORY_RESPONSE_INVALID", "记忆保存响应无效。")
        projected = _project_memory(memory, self._character_id)
        if projected is None:
            raise MemoryBoundaryError("MEMORY_RESPONSE_INVALID", "记忆保存响应无效。")
        _assert_memory_round_trip(projected, arguments)
        return {"status": "ready", "memory": projected}

    def delete(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(payload, {"id"})
        self._assert_writable()
        memory_id = _required_text(payload.get("id"), "id", 256)
        try:
            with self._write_lock:
                result = self._store.forget_memory({"id": memory_id}, wait=True)
        except Exception as exc:
            raise MemoryBoundaryError(
                "MEMORY_DELETE_FAILED", "记忆删除失败，原数据保持不变。", retryable=True
            ) from exc
        return {
            "status": "ready",
            "deletedId": memory_id,
            "alreadyMissing": bool(result.get("already_missing", False)),
        }

    def settings_get(self) -> dict[str, object]:
        try:
            catalog = _provider_choices(self._model_catalog_getter())
            trigger, backfill, configured_slot = _curation_values(
                self._curation_config_getter(),
                catalog,
            )
            effective = _resolved_model(self._model_resolver(configured_slot))
        except MemoryBoundaryError:
            self._set_status("read_only", "记忆设置不可写；现有数据保持不变。")
            raise
        current = self.status()
        return {
            **current,
            "schemaVersion": 1,
            "curation": {
                "enabled": True,
                "triggerTurns": trigger,
                "backfillLimit": backfill,
                "available": bool(effective["profileId"] and effective["model"]),
            },
            "curationModelSlot": configured_slot,
            "providerChoices": catalog,
            "embedding": {
                "model": DEFAULT_EMBEDDING_MODEL,
                "dimensions": DEFAULT_EMBEDDING_DIMS,
                "installed": not self._store.needs_embedding_model_download(),
                "task": None,
            },
        }

    def model_download(
        self,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        task_id = self._begin_model_task(request)
        status = self.run_model_download(task_id)
        return {"accepted": True, "taskId": task_id, "status": status}

    def begin_model_download(self, task_id: str) -> None:
        self._begin_model_task({"id": task_id})

    def run_model_download(
        self,
        task_id: str,
        *,
        progress: Callable[[str, int], None] | None = None,
    ) -> str:
        try:
            with self._write_lock:
                self._store.download_embedding_model(
                    progress=self._model_progress(progress),
                    cancel=self._model_task_cancel,
                )
            self._set_status("loading", "本地记忆模型已安装，正在初始化记忆。")
            status = "completed"
        except MemoryModelTaskCancelled:
            status = "cancelled"
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, MemoryModelImportError)
                else "DOWNLOAD_FAILED"
            )
            with self._lock:
                self._model_download_error_code = code
            self._set_status("degraded", "本地记忆模型下载失败；原缓存保持不变。")
            append_memory_initialization_diagnostic(
                self._app_root,
                component="plugin_memory_owner",
                event="model_download_failed",
                stage="download",
                outcome="failed",
                category=code,
                error_type=type(exc).__name__,
            )
            status = "failed"
        finally:
            self._finish_model_task(task_id)
        return status

    def model_cancel(self, payload: Mapping[str, object]) -> dict[str, object]:
        task_id = _required_text(payload.get("taskHandle"), "taskHandle", 256)
        with self._lock:
            accepted = self._model_task_active and self._model_task_id == task_id
            if accepted:
                self._model_task_cancel.set()
        return {"accepted": accepted, "taskId": task_id if accepted else ""}

    def model_download_error_code(self) -> str:
        with self._lock:
            return self._model_download_error_code

    def _begin_model_task(self, request: Mapping[str, Any] | None) -> str:
        self._assert_writable(require_ready=False, feature="memory.embedding_model")
        task_id = (
            _required_text(request.get("id"), "taskId", 256)
            if request is not None
            else f"memory-model-{uuid.uuid4().hex}"
        )
        with self._lock:
            if self._curation_active or self._model_task_active:
                raise MemoryBoundaryError(
                    "MEMORY_TASK_BUSY",
                    "已有记忆后台任务正在运行。",
                    retryable=True,
                    feature="memory.embedding_model",
                )
            self._model_task_cancel.clear()
            self._model_task_active = True
            self._model_task_id = task_id
            self._model_download_error_code = ""
        return task_id

    def _finish_model_task(self, task_id: str) -> None:
        pending_timeline: object | None = None
        with self._lock:
            if self._model_task_id == task_id:
                self._model_task_active = False
                self._model_task_id = ""
                self._model_task_cancel.clear()
                pending_timeline = self._pending_timeline
                self._pending_timeline = None
        if pending_timeline is not None:
            self.note_timeline_changed(pending_timeline)

    def _model_task_cancelled(self) -> None:
        if self._model_task_cancel.is_set() or self._closed:
            raise MemoryModelTaskCancelled("记忆模型任务已取消。")

    def _model_progress(
        self,
        observer: Callable[[str, int], None] | None = None,
    ) -> Callable[[str, int], None]:
        def check_cancelled(stage: str, progress: int) -> None:
            self._model_task_cancelled()
            if observer is not None:
                observer(stage, progress)
            self._model_task_cancelled()

        return check_cancelled

    def note_timeline_changed(self, timeline: object) -> None:
        """Catch up committed Timeline entries and schedule at most one curation job."""

        with self._lock:
            if (
                self._closed
                or self._store_failed
                or self._status != "ready"
                or self._curation_request_fuse_open
            ):
                return
            if self._curation_active or self._model_task_active:
                self._pending_timeline = timeline
                return
            try:
                catalog = _provider_choices(self._model_catalog_getter())
                trigger, backfill, configured_slot = _curation_values(
                    self._curation_config_getter(),
                    catalog,
                )
                entries, next_cursor = _read_timeline_interval(
                    timeline,
                    self._character_id,
                    self._curation_state.curation_cursor(),
                    backfill,
                )
                self._curation_state.mark_timeline_synced(next_cursor)
                entries, pending = _curation_evidence_turns(entries)
                self._curation_state.set_timeline_pending(pending)
                if not pending:
                    self._curation_state.mark_timeline_processed(next_cursor)
                    return
                if pending < trigger:
                    return
                log_event(
                    "Memory",
                    "证据 Turn 达到阈值，开始记忆整理",
                    {
                        "reason_code": "EVIDENCE_TURN_THRESHOLD",
                        "eligible_turns": pending,
                        "trigger_turns": trigger,
                        "turn_ids": list(
                            dict.fromkeys(entry.turn_id for entry in entries if entry.turn_id)
                        ),
                    },
                    event="memory.curation.triggered",
                    verbosity=1,
                )
                resolved = _resolved_model(self._model_resolver(configured_slot))
                if not resolved["profileId"]:
                    return
                settings = ApiSettings(
                    base_url=resolved["baseUrl"],
                    api_key=resolved["apiKey"],
                    model=resolved["model"],
                    timeout_seconds=resolved["timeoutSeconds"],
                )
                self._curation_active = True
            except Exception:
                return

        self._start_curation(
            entries,
            settings,
            lambda: self._curation_state.mark_timeline_processed(next_cursor),
        )

    def _start_curation(
        self,
        entries: list[ChatHistoryEntry],
        settings: ApiSettings,
        mark_success: Callable[[], None],
    ) -> None:
        def curate() -> None:
            client: OpenAICompatibleClient | None = None
            pending_timeline: object | None = None
            operation_id = f"memory-curation-{uuid.uuid4().hex}"
            try:
                with interaction_context(operation_id):
                    if self._curation_cancel.is_set():
                        return
                    log_event(
                        "Memory",
                        "开始后台记忆整理",
                        {"history_messages": len(entries)},
                        severity="debug",
                    )
                    client = OpenAICompatibleClient(settings)
                    curator = MemoryCurator(
                        client,
                        self._store.scoped(self._character_id),
                        system_prompt=self._system_prompt,
                    )

                    def check_cancelled() -> None:
                        if self._curation_cancel.is_set():
                            raise OperationCancelled()

                    with self._write_lock:
                        result = curator.curate_entries(entries, cancel_checker=check_cancelled)
                    if self._curation_cancel.is_set():
                        return
                    mark_success()
                    log_event(
                        "Memory",
                        "后台记忆整理完成",
                        {
                            "history_messages": len(entries),
                            "created": result.created,
                            "updated": result.updated,
                            "archived": result.archived,
                            "ignored": result.ignored,
                        },
                        severity="info" if result.created or result.updated or result.archived else "debug",
                    )
            except OperationCancelled:
                return
            except Exception as exc:
                # Cursor and existing memories remain untouched; the next
                # generation can retry the same committed interval. If this job
                # exhausted its HTTP allowance, stop automatic curation for the
                # current plugin generation so later chat events cannot replay it.
                with interaction_context(operation_id):
                    diagnostic_getter = getattr(self._store, "load_diagnostic", None)
                    diagnostic = (
                        diagnostic_getter() if callable(diagnostic_getter) else {}
                    )
                    reason_code = str(getattr(exc, "code", ""))
                    if reason_code not in {
                        "MEMORY_CURATION_SNAPSHOT_FAILED",
                        "MEMORY_CURATION_WRITE_FAILED",
                        "CURATION_REQUEST_LIMIT_EXCEEDED",
                        "CURATION_RESPONSE_INVALID",
                    }:
                        reason_code = "CURATION_FAILED"
                    requests_sent = int(getattr(client, "requests_sent", 0))
                    deterministic_response_failure = reason_code in {
                        "CURATION_REQUEST_LIMIT_EXCEEDED",
                        "CURATION_RESPONSE_INVALID",
                    }
                    request_fuse_opened = (
                        requests_sent >= MAX_CURATION_HTTP_REQUESTS_PER_JOB
                        or deterministic_response_failure
                    )
                    if request_fuse_opened:
                        with self._lock:
                            self._curation_request_fuse_open = True
                            self._pending_timeline = None
                        log_event(
                            "Memory",
                            "自动记忆整理请求保险丝已触发，本次运行不再重试",
                            {
                                "reason_code": "CURATION_REQUEST_FUSE_OPEN",
                                "requests_sent": requests_sent,
                            },
                            event="memory.curation.request_fuse_opened",
                        severity="warning",
                        )
                    log_event(
                        "Memory",
                        (
                            "后台记忆整理已停止"
                            if request_fuse_opened
                            else "后台记忆整理失败，稍后将重试"
                        ),
                        {
                            "error_type": type(exc).__name__,
                            "reason_code": reason_code,
                            "category": str(diagnostic.get("category") or "curation_failed"),
                            "runtime_error_type": str(
                                diagnostic.get("errorType") or "UnknownError"
                            ),
                        },
                        event="memory.curation.failed",
                        severity="error",
                    )
                return
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                with self._lock:
                    self._curation_active = False
                    pending_timeline = (
                        None
                        if self._curation_request_fuse_open
                        else self._pending_timeline
                    )
                    self._pending_timeline = None
                if pending_timeline is not None:
                    self.note_timeline_changed(pending_timeline)

        if self._curation_threads.spawn(
            curate, name="sakura-runtime-v2-memory-curation", daemon=True
        ) is None:
            pending_timeline: object | None = None
            with self._lock:
                self._curation_active = False
                pending_timeline = self._pending_timeline
                self._pending_timeline = None
            if pending_timeline is not None:
                self.note_timeline_changed(pending_timeline)

    def close(self) -> None:
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_close_started",
            stage="shutdown",
            outcome="started",
            status="stopped",
        )
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending_timeline = None
            self._status = "stopped"
            self._message = "记忆能力已停止。"
            self._status_changed.notify_all()
        self._curation_cancel.set()
        self._model_task_cancel.set()
        self._resources.stop_all(timeout_ms=10_000)
        self._store.remove_status_listener(self._on_store_status)
        self._store.close()
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_close_completed",
            stage="shutdown",
            outcome="completed",
            status="stopped",
        )

    def _assert_writable(
        self,
        *,
        require_ready: bool = True,
        feature: str = "memory.manage",
    ) -> None:
        status = self.status()["status"]
        with self._lock:
            task_busy = self._curation_active or self._model_task_active
        if status in {"read_only", "failed", "stopped"}:
            raise MemoryBoundaryError(
                "MEMORY_READ_ONLY", "记忆当前不可写。", feature=feature
            )
        if require_ready and status != "ready":
            raise MemoryBoundaryError(
                "MEMORY_NOT_READY", "记忆仍在初始化。", retryable=True, feature=feature
            )
        if require_ready and task_busy:
            raise MemoryBoundaryError(
                "MEMORY_TASK_BUSY", "记忆后台任务正在运行，请稍后重试。",
                retryable=True, feature=feature,
            )

    def _on_store_status(self, status: str, message: str) -> None:
        projected = {
            "idle": "loading", "reloading": "loading", "ready": "ready",
            "failed": "degraded", "stopped": "stopped",
        }.get(status, "degraded")
        with self._lock:
            if status == "ready":
                self._store_failed = False
            elif status in {"failed", "stopped"}:
                self._store_failed = True
        self._set_status(projected, _public_status_message(projected, message))

    def _set_status(self, status: str, message: str) -> None:
        safe = status if status in MEMORY_STATUSES else "degraded"
        changed = False
        with self._lock:
            if self._closed and safe != "stopped":
                return
            changed = self._status != safe
            self._status = safe  # type: ignore[assignment]
            self._message = message
            self._status_changed.notify_all()
        if changed:
            append_memory_initialization_diagnostic(
                self._app_root,
                component="plugin_memory_owner",
                event="owner_status_changed",
                stage="store_status",
                outcome="observed",
                status=safe,
            )


def _read_timeline_interval(
    timeline: object,
    character_id: str,
    cursor: str,
    backfill: int,
) -> tuple[list[ChatHistoryEntry], str]:
    if cursor:
        try:
            return _read_timeline_since(timeline, character_id, cursor)
        except Exception as exc:
            if getattr(exc, "code", str(exc)) != "TIMELINE_CURSOR_INVALID":
                raise
    result = getattr(timeline, "read_recent")({"limit": min(backfill, 500)})
    if not isinstance(result, Mapping):
        raise ValueError("TIMELINE_RESPONSE_INVALID")
    entries = _project_timeline_entries(result.get("entries"), character_id)
    next_cursor = result.get("cursor")
    if not isinstance(next_cursor, str) or not next_cursor:
        raise ValueError("TIMELINE_RESPONSE_INVALID")
    return entries, next_cursor


def _read_timeline_since(
    timeline: object,
    character_id: str,
    cursor: str,
) -> tuple[list[ChatHistoryEntry], str]:
    entries: list[ChatHistoryEntry] = []
    next_cursor = cursor
    while True:
        result = getattr(timeline, "read_since")({"cursor": next_cursor, "limit": 500})
        if not isinstance(result, Mapping):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        entries.extend(_project_timeline_entries(result.get("entries"), character_id))
        candidate = result.get("nextCursor")
        has_more = result.get("hasMore")
        if not isinstance(candidate, str) or not candidate or not isinstance(has_more, bool):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        if has_more and candidate == next_cursor:
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        next_cursor = candidate
        if not has_more:
            return entries, next_cursor


def _project_timeline_entries(value: object, character_id: str) -> list[ChatHistoryEntry]:
    if not isinstance(value, list):
        raise ValueError("TIMELINE_RESPONSE_INVALID")
    projected: list[ChatHistoryEntry] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("characterId") != character_id:
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        entry_id = item.get("entryId")
        created_at = item.get("createdAt")
        turn_id = item.get("turnId")
        origin = item.get("origin")
        kind = item.get("kind")
        payload = item.get("payload")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or not isinstance(created_at, str)
            or not isinstance(turn_id, str)
            or not turn_id
            or not isinstance(origin, str)
            or not isinstance(kind, str)
            or not isinstance(payload, Mapping)
        ):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        if kind == "human":
            text = payload.get("text")
            role = "user"
            evidence_ready = True
        elif kind == "assistant":
            segments = payload.get("segments")
            if not isinstance(segments, list):
                raise ValueError("TIMELINE_RESPONSE_INVALID")
            texts = [
                segment.get("text")
                for segment in segments
                if isinstance(segment, Mapping) and isinstance(segment.get("text"), str)
            ]
            if len(texts) != len(segments):
                raise ValueError("TIMELINE_RESPONSE_INVALID")
            text = "\n".join(texts)
            role = "assistant"
            evidence_ready = False
        elif kind in {"observation", "system"}:
            text = payload.get("text")
            role = kind
            visual = payload.get("visual")
            evidence_ready = bool(
                kind == "observation"
                and isinstance(visual, Mapping)
                and visual.get("analysisStatus") == "succeeded"
            )
        else:
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        if not isinstance(text, str):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        projected.append(
            ChatHistoryEntry(
                created_at=created_at,
                role=role,
                content=text,
                entry_id=entry_id,
                turn_id=turn_id,
                origin=origin,
                evidence_ready=evidence_ready,
            )
        )
    return projected


def _curation_evidence_turns(
    entries: list[ChatHistoryEntry],
) -> tuple[list[ChatHistoryEntry], int]:
    """Select complete evidence Turns and count each logical Turn once."""

    grouped: dict[str, list[ChatHistoryEntry]] = {}
    for entry in entries:
        key = entry.turn_id or entry.entry_id
        if key:
            grouped.setdefault(key, []).append(entry)

    selected: list[ChatHistoryEntry] = []
    eligible_turns = 0
    for turn_entries in grouped.values():
        if not any(
            entry.role == "user"
            or (entry.role == "observation" and entry.evidence_ready)
            for entry in turn_entries
        ):
            continue
        eligible_turns += 1
        selected.extend(
            entry
            for entry in turn_entries
            if entry.role in {"user", "assistant"}
            or (entry.role == "observation" and entry.evidence_ready)
        )
    return selected, eligible_turns


def _curation_values(
    plugin: Mapping[str, object],
    catalog: list[dict[str, object]],
) -> tuple[int, int, dict[str, str]]:
    trigger = _bounded_int(
        plugin.get("triggerTurns", 8),
        "triggerTurns",
        1,
        50,
    )
    backfill = _bounded_int(
        plugin.get("backfillLimit", 200),
        "backfillLimit",
        1,
        100_000,
    )
    profile = _text(
        plugin.get("curationProfileId", ""),
        "curationProfileId",
        64,
    )
    model = _text(
        plugin.get("curationModel", ""),
        "curationModel",
        256,
    )
    configured_slot = _parse_slot(
        {"profileId": profile, "model": model},
        catalog,
    )
    return trigger, backfill, configured_slot


def _provider_choices(value: object) -> list[dict[str, object]]:
    raw = value
    if not isinstance(raw, list):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise MemoryBoundaryError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
        if set(item) != {"id", "alias", "models"}:
            raise MemoryBoundaryError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
        profile_id = _required_text(item.get("id"), "id", 64)
        alias = _text(item.get("alias"), "alias", 120) or profile_id
        models_raw = item.get("models", [])
        if not isinstance(models_raw, list):
            raise MemoryBoundaryError("CONFIG_DATA_INVALID", "Provider 模型格式无效。")
        models: list[str] = []
        for model in models_raw:
            value = model.get("name") if isinstance(model, Mapping) else model
            models.append(_required_text(value, "model", 256))
        result.append({"id": profile_id, "alias": alias, "models": models})
    return result


def _parse_slot(
    raw: Mapping[str, object] | None,
    catalog: list[dict[str, object]],
) -> dict[str, str]:
    if raw is None:
        return {"profileId": "", "model": ""}
    _only(raw, {"profileId", "model"})
    profile = _text(raw.get("profileId"), "profileId", 64)
    model = _text(raw.get("model"), "model", 256)
    if bool(profile) != bool(model):
        raise MemoryBoundaryError("FIELD_INVALID", "模型槽必须同时选择 Provider 和模型。")
    if not profile:
        return {"profileId": "", "model": ""}
    choices = {item["id"]: item for item in catalog}
    selected = choices.get(profile)
    if selected is None or model not in selected["models"]:
        raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "模型槽引用无效。")
    return {"profileId": profile, "model": model}


def _resolved_model(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "profileId",
        "model",
        "baseUrl",
        "apiKey",
        "timeoutSeconds",
    }:
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "模型解析响应无效。")
    profile_id = _text(value.get("profileId"), "profileId", 64)
    model = _text(value.get("model"), "model", 256)
    base_url = _text(value.get("baseUrl"), "baseUrl", 2048)
    api_key = _text(value.get("apiKey"), "apiKey", 16_384)
    timeout = _bounded_int(value.get("timeoutSeconds"), "timeoutSeconds", 1, 600)
    if bool(profile_id) != bool(model) or (profile_id and (not base_url or not api_key)):
        raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "记忆整理模型槽引用无效。")
    return {
        "profileId": profile_id,
        "model": model,
        "baseUrl": base_url,
        "apiKey": api_key,
        "timeoutSeconds": timeout,
    }


def _project_memory(raw: Mapping[str, object], scope: str) -> dict[str, object] | None:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    record_scope = str(raw.get("scope") or metadata.get("scope") or scope)
    if record_scope != scope:
        return None
    content = str(raw.get("content") or raw.get("memory") or "").strip()
    memory_id = str(raw.get("id") or raw.get("memory_id") or "").strip()
    if not content or not memory_id:
        return None
    def field(name: str, default: object = "") -> object:
        return raw.get(name, metadata.get(name, default))
    projected: dict[str, object] = {
        "id": memory_id,
        "content": content[:MAX_MEMORY_CONTENT],
        "layer": str(field("layer", DEFAULT_MEMORY_LAYER)),
        "category": str(field("category")),
        "importance": _safe_number(field("importance", DEFAULT_MEMORY_IMPORTANCE)),
        "confidence": _safe_number(field("confidence", DEFAULT_MEMORY_CONFIDENCE)),
        "source": str(field("source", "inferred")),
        "scope": scope,
        "createdAt": str(field("created_at")),
        "updatedAt": str(field("updated_at")),
        "lastAccessedAt": str(field("last_accessed_at")),
        "score": _safe_number(raw.get("score"), default=None),
    }
    optional_fields = {
        "sourceTurnId": field("source_turn_id"),
        "sourceEntryIds": field("source_entry_ids", []),
        "createdInTurnId": field("created_in_turn_id"),
        "evidenceKind": field("evidence_kind"),
    }
    for key, value in optional_fields.items():
        if key == "sourceEntryIds":
            if isinstance(value, (list, tuple)) and value:
                projected[key] = list(value)
        elif str(value or "").strip():
            projected[key] = str(value)
    return projected


def _assert_memory_round_trip(
    memory: Mapping[str, object],
    requested: Mapping[str, object],
) -> None:
    fields = {
        "layer": "layer",
        "category": "category",
        "source": "source",
        "importance": "importance",
        "confidence": "confidence",
        "source_turn_id": "sourceTurnId",
        "source_entry_ids": "sourceEntryIds",
        "created_in_turn_id": "createdInTurnId",
        "evidence_kind": "evidenceKind",
    }
    for request_key, memory_key in fields.items():
        if request_key not in requested:
            continue
        expected = requested[request_key]
        actual = memory.get(memory_key)
        if request_key in {"importance", "confidence"}:
            matches = _safe_number(actual) == _safe_number(expected)
        elif request_key == "source_entry_ids":
            matches = (
                isinstance(actual, list)
                and isinstance(expected, (list, tuple))
                and actual == list(expected)
            )
        else:
            matches = str(actual or "") == str(expected or "")
        if not matches:
            raise MemoryBoundaryError(
                "MEMORY_ROUND_TRIP_MISMATCH",
                "记忆保存后的元数据校验失败，未返回不可信的成功结果。",
                retryable=True,
                field=request_key,
            )


def _public_status_message(status: str, _internal: str) -> str:
    return {
        "ready": "",
        "loading": "记忆系统正在初始化。",
        "degraded": "记忆暂时不可用；聊天不受影响。",
        "read_only": "记忆处于只读状态。",
        "failed": "记忆能力不可用。",
        "stopped": "记忆能力已停止。",
    }[status]


def _only(value: Mapping[str, object], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise MemoryBoundaryError("INVALID_REQUEST", "记忆请求包含未知字段。")


def _text(value: object, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise MemoryBoundaryError("FIELD_INVALID", f"{field} 格式无效。", field=field)
    return value.strip()


def _required_text(value: object, field: str, maximum: int) -> str:
    text = _text(value, field, maximum)
    if not text:
        raise MemoryBoundaryError("FIELD_REQUIRED", f"{field} 不能为空。", field=field)
    return text


def _bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MemoryBoundaryError("FIELD_INVALID", f"{field} 超出允许范围。", field=field)
    return value


def _bounded_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise MemoryBoundaryError("FIELD_INVALID", f"{field} 超出允许范围。", field=field)
    return float(value)


def _safe_number(value: object, *, default: float | None = 0.0) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


__all__ = ["MemoryBoundary", "MemoryBoundaryError"]
