"""管理当前角色的记忆存储、模型下载和后台整理任务。"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .memory import (
        DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MEMORY_CONFIDENCE,
        DEFAULT_MEMORY_IMPORTANCE,
        DEFAULT_MEMORY_LAYER,
        MEMORY_LAYERS,
        KNOWLEDGE_FIELDS,
        MemoryModelImportError,
        MemoryModelTaskCancelled,
        MemoryStore,
        append_memory_initialization_diagnostic,
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
        KNOWLEDGE_FIELDS,
        MemoryModelImportError,
        MemoryModelTaskCancelled,
        MemoryStore,
        append_memory_initialization_diagnostic,
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
        model_client_factory: Callable[[Mapping[str, object]], object] | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self._character_id = _required_text(character_id, "character_id", 128)
        self._system_prompt = system_prompt.strip()
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._closed = False
        self._curation_cancel = threading.Event()
        self._curation_active = False
        self._curation_result: dict = {}
        self._text_import_cancel = threading.Event()
        self._text_import_result: dict = {"state": "idle"}
        self._pending_timeline: object | None = None
        self._model_task_active = False
        self._model_task_id = ""
        self._model_task_cancel = threading.Event()
        self._model_download_error_code = ""
        self._curation_config_getter = curation_config_getter or (lambda: {})
        self._model_catalog_getter = model_catalog_getter or (lambda: [])
        self._model_resolver = model_resolver or (lambda selection: selection)
        self._model_client_factory = model_client_factory
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
            memory_dir=self._memory_dir,
            memory_cache_dir=self._memory_cache_dir,
        )
        self._curation_state = MemoryCurationState(self._store)
        self._start_preload()

    def _start_preload(self) -> None:
        try:
            self._store.preload(wait=False)
        except Exception as exc:
            log_event("Memory", "向量检索准备失败，仍可使用关键词检索", {"error": str(exc)},
                      event="memory.index.preload_failed", severity="warning")

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

        return self.search({"limit": 10, **arguments})

    def profile_memories(self) -> list[dict]:
        return [_project_memory(item, self._character_id) for item in self._store.profile_memories()]

    def summary(self) -> str:
        return ""

    def status(self) -> dict[str, str]:
        if self._closed or not self._store.is_ready():
            return {"status": "stopped", "message": "记忆存储已关闭。"}
        return {"status": "ready", "message": ""}

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
        try:
            return self._store.list_memories(limit=limit)
        except Exception as exc:
            raise MemoryBoundaryError("MEMORY_READ_FAILED", f"记忆读取失败：{exc}", retryable=True) from exc

    def search(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(payload, {"query", "limit", "layer", "automatic", "excluded_turn_id", "subject", "predicate", "target"})
        query = _text(payload.get("query"), "query", MAX_MEMORY_QUERY)
        limit = _bounded_int(payload.get("limit", 10), "limit", 1, MAX_MEMORY_RESULTS)
        layer = payload.get("layer")
        if layer is not None and layer not in MEMORY_LAYERS:
            raise MemoryBoundaryError("FIELD_INVALID", "记忆分层无效。", field="layer")
        try:
            result = self._store.search_memory({**payload, "query": query, "limit": limit}, wait=False)
        except Exception as exc:
            log_event("Memory", "记忆检索失败", {"error": str(exc)}, event="memory.search.failed", severity="error")
            return {"status": "degraded", "message": str(exc), "memories": []}
        return {"status": "ready", "message": "", "memories": [
            _project_memory(item, self._character_id) for item in result["memories"]
        ]}

    def transfer(self, request: Mapping[str, object]) -> dict:
        if request["expectedCharacterId"] != self._character_id:
            raise ValueError("角色已变化，请重新打开导入导出。")
        action = request["action"]
        with self._write_lock:
            existing = self._store.export_records()
            if action == "export":
                return write_archive(request["path"], kind="memory", provider="sakura.memory.mem0",
                                     character_id=self._character_id, records=existing, format=request["format"])
            archive = read_archive(request["path"], kind="memory", provider="sakura.memory.mem0",
                                   character_id=self._character_id, identity_path=request.get("identityPath"))
            records = archive["records"]
            for item in records:
                _required_text(item["content"], "content", MAX_MEMORY_CONTENT)
                if item["layer"] not in MEMORY_LAYERS:
                    raise ValueError("记忆分类无效。")
            additions, counts = plan_merge(records, existing)
            if action == "import":
                self._assert_writable()
                self._store.restore_records(additions)
            elif action != "preview":
                raise ValueError("不支持的数据操作。")
            return {**counts, "sourceCharacterId": archive["characterId"]}

    def start_text_import(self, request: Mapping[str, object]) -> dict:
        text = str(request["text"]).strip()
        if not text:
            raise ValueError("请先粘贴需要提取的文本。")
        request_id = request["requestId"]
        with self._lock:
            self._assert_writable()
            _, _, configured = _curation_values(self._curation_config_getter())
            selection = _resolved_model(self._model_resolver(configured))
            if not selection["serviceKey"] or self._model_client_factory is None:
                raise ValueError("请先配置记忆整理模型。")
            self._text_import_cancel.set()
            cancel = self._text_import_cancel = threading.Event()
            self._text_import_result = {"requestId": request_id, "state": "running", "candidates": []}

        def cancelled() -> None:
            if cancel.is_set():
                raise OperationCancelled()

        def run() -> None:
            client = None
            log_event("Memory", "开始从文本提取记忆", {"request_id": request_id, "scope": self._character_id},
                      event="memory.text_import.started")
            try:
                cancelled()
                client = self._model_client_factory(selection)
                entry = ChatHistoryEntry(datetime.now(timezone.utc).isoformat(), "document", text,
                                         entry_id="text-import:" + request_id)
                candidates = MemoryCurator(client, self._store).preview_text(entry, cancel_checker=cancelled)
                with self._lock:
                    cancelled()
                    self._text_import_result.update(state="preview", candidates=[
                        {**item, "candidateId": str(uuid.uuid4())} for item in candidates])
                log_event("Memory", "文本记忆候选已生成", {"request_id": request_id, "count": len(candidates)},
                          event="memory.text_import.preview")
            except OperationCancelled:
                pass
            except Exception as exc:
                with self._lock:
                    if not cancel.is_set():
                        self._text_import_result.update(state="failed", error=str(exc))
                        log_event("Memory", "文本记忆提取失败", {"request_id": request_id, "error": str(exc)},
                                  event="memory.text_import.failed", severity="warning")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as exc:
                        log_event("Memory", "整理模型连接关闭失败", {"error": str(exc)}, severity="debug")

        if self._curation_threads.spawn(run, name="sakura-memory-text-import", daemon=True) is None:
            with self._lock:
                self._text_import_result.update(state="failed", error="无法启动文本提取任务。")
        return self.text_import_status(request)

    def text_import_status(self, request: Mapping[str, object]) -> dict:
        with self._lock:
            if request["requestId"] != self._text_import_result.get("requestId"):
                raise ValueError("这次文本提取已结束，请重新提取。")
            return deepcopy(self._text_import_result)

    def cancel_text_import(self, request: Mapping[str, object]) -> dict:
        with self._lock:
            if request["requestId"] == self._text_import_result.get("requestId"):
                self._text_import_cancel.set()
                self._text_import_result = {"requestId": request["requestId"], "state": "cancelled"}
        return {"state": "cancelled"}

    def save_text_import(self, request: Mapping[str, object]) -> dict:
        with self._lock:
            self._assert_writable()
            preview = self.text_import_status(request)
            if preview["state"] == "saved":
                return preview
            if preview["state"] != "preview":
                raise ValueError("请先完成文本提取并核对候选。")
            candidates = {item["candidateId"]: item for item in preview["candidates"]}
            selected = {item["candidateId"]: item for item in request["candidates"]}
            if not selected:
                raise ValueError("请至少选择一条记忆。")
            with self._write_lock, self._store.transaction():
                for identity, edits in selected.items():
                    candidate = candidates[identity]
                    values = {key: value for key, value in candidate.items() if key != "candidateId"}
                    values.update({key: value for key, value in edits.items()
                                   if key in {"content", "layer", "state", *KNOWLEDGE_FIELDS}})
                    self.upsert({**values, "source": "manual"})
            self._text_import_result = {"requestId": request["requestId"], "state": "saved", "saved": len(selected)}
            log_event("Memory", "已保存用户确认的文本记忆", {"request_id": request["requestId"], "count": len(selected)},
                      event="memory.text_import.saved")
            return dict(self._text_import_result)

    def upsert(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(
            payload,
            {
                "id",
                "content",
                "layer",
                "category",
                "source",
                *KNOWLEDGE_FIELDS,
                "importance",
                "confidence",
                "source_turn_id",
                "source_entry_ids",
                "created_in_turn_id",
                "evidence_kind", "evidence", "pinned", "state", "occurred_at", "revision",
            },
        )
        self._assert_writable()
        memory_id = _text(payload.get("id"), "id", 256)
        previous = self._store.get(memory_id) if memory_id else {}
        if memory_id and previous is None:
            raise ValueError("记忆不存在或不属于当前角色。")
        if previous.get("pinned") and payload.get("source") == "user_tool":
            raise ValueError("这条记忆已固定，请在记忆管理页修改。")
        content = _required_text(payload.get("content", previous.get("content")), "content", MAX_MEMORY_CONTENT)
        layer = payload.get("layer", previous.get("layer", DEFAULT_MEMORY_LAYER))
        if layer not in MEMORY_LAYERS:
            raise MemoryBoundaryError("FIELD_INVALID", "记忆分层无效。", field="layer")
        arguments: dict[str, object] = {
            "content": content,
            "layer": layer,
            "category": _text(payload.get("category", previous.get("category")), "category", MAX_MEMORY_TEXT_FIELD),
            "source": _text(payload.get("source", previous.get("source")), "source", MAX_MEMORY_TEXT_FIELD) or "manual",
            "importance": _bounded_number(
                payload.get("importance", previous.get("importance", DEFAULT_MEMORY_IMPORTANCE)), "importance"
            ),
            "confidence": _bounded_number(
                payload.get("confidence", previous.get("confidence", DEFAULT_MEMORY_CONFIDENCE)), "confidence"
            ),
        }
        if memory_id:
            arguments["id"] = memory_id
            arguments["revision"] = previous["revision"]
        for key in (
            "source_turn_id",
            "source_entry_ids",
            "created_in_turn_id",
            "evidence_kind", "evidence", "pinned", "state", "occurred_at", "revision", *KNOWLEDGE_FIELDS,
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
                "MEMORY_WRITE_FAILED", f"记忆保存失败：{exc}", retryable=True
            ) from exc
        return {"status": "ready", "memory": _project_memory(result["memory"], self._character_id)}

    def delete(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(payload, {"id", "source", "evidence"})
        self._assert_writable()
        memory_id = _required_text(payload.get("id"), "id", 256)
        previous = self._store.get(memory_id)
        if previous and previous["pinned"] and payload.get("source") == "user_tool":
            raise ValueError("这条记忆已固定，请在记忆管理页删除。")
        try:
            with self._write_lock:
                result = self._store.forget_memory(dict(payload), wait=True)
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
        trigger, backfill, configured_slot = _curation_values(self._curation_config_getter())
        catalog = []
        effective = {}
        try:
            catalog = self._model_catalog_getter()
            effective = _resolved_model(self._model_resolver(configured_slot))
        except Exception:
            # A missing/reloading model only disables curation. The local store,
            # manual management and recall have no inference dependency.
            pass
        available = any(
            provider.get("serviceKey") == effective.get("serviceKey")
            and any(profile.get("profileId") == effective.get("profileId")
                    and any(model.get("modelId") == effective.get("modelId") for model in profile.get("models", []))
                    for profile in provider.get("profiles", []))
            for provider in catalog if isinstance(provider, Mapping)
        ) if isinstance(catalog, list) else False
        current = self.status()
        return {
            **current,
            "schemaVersion": 1,
            "curation": {
                "enabled": True,
                "triggerTurns": trigger,
                "backfillLimit": backfill,
                "available": available,
                "running": self._curation_active,
                "pendingTurns": self._curation_state.pending_turns(),
                "result": self._curation_result,
            },
            "curationModelSlot": configured_slot,
            "providerChoices": catalog,
            "backfill": self._store.get_state("backfill", default={}),
            "embedding": {
                "model": DEFAULT_EMBEDDING_MODEL,
                "dimensions": DEFAULT_EMBEDDING_DIMS,
                "installed": not self._store.needs_embedding_model_download(),
                "index": self._store.index_status(),
                "task": None,
            },
        }

    def rebuild_index(self) -> None:
        self._assert_writable()
        if self._store.needs_embedding_model_download():
            raise ValueError("请先下载中文向量模型。")
        self._store.rebuild_index()

    def merge_duplicates(self) -> dict:
        self._assert_writable()
        with self._write_lock:
            return self._store.merge_duplicates()

    def update_now(self, timeline: object) -> None:
        self._assert_writable()
        self.note_timeline_changed(timeline, force=True)

    def start_backfill(self, timeline: object, start_date: str, end_date: str) -> None:
        from datetime import date

        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError("开始日期不能晚于结束日期。")
        with self._lock:
            self._assert_writable()
            if self._curation_active:
                raise ValueError("记忆整理正在运行。")
            _, _, configured = _curation_values(self._curation_config_getter())
            selection = _resolved_model(self._model_resolver(configured))
            if not selection["serviceKey"] or self._model_client_factory is None:
                raise ValueError("请先配置记忆整理模型。")
            saved = self._store.get_state("backfill", default={})
            if saved.get("startDate") != start_date or saved.get("endDate") != end_date or saved.get("status") == "completed":
                saved = {"startDate": start_date, "endDate": end_date, "completed": 0}
            saved.update(status="running", error="")
            self._store.set_state("backfill", saved)
            self._curation_cancel.clear()
            self._curation_active = True

        def run() -> None:
            client = None
            def finish(status: str, **values) -> None:
                with self._lock:
                    if not self._closed:
                        current = self._store.get_state("backfill", default={})
                        self._store.set_state("backfill", {**current, "status": status, **values})
            try:
                client = self._model_client_factory(selection)
                curator = MemoryCurator(client, self._store)

                def cancelled() -> None:
                    if self._curation_cancel.is_set():
                        raise OperationCancelled()

                for entries, cursor, snapshot, total in _read_timeline_range(
                    timeline, self._character_id, saved, cancelled,
                ):
                    cancelled()
                    self._store.set_state("backfill", {
                        **self._store.get_state("backfill", default={}), "snapshotCursor": snapshot, "total": total,
                    })
                    curator.curate_entries(_curation_evidence_turns(entries)[0],
                                           cursor=cursor, state_key="backfill", cancel_checker=cancelled)
                    cancelled()
                    current = self._store.get_state("backfill", default={})
                    current["completed"] = current.get("completed", 0) + len(entries)
                    self._store.set_state("backfill", current)
                    log_event("Memory", "历史记忆整理进度",
                              {"completed": current["completed"], "total": total}, event="memory.backfill.progress")
                finish("completed")
            except OperationCancelled:
                finish("cancelled")
            except Exception as exc:
                if not self._closed:
                    finish("failed", error=str(exc))
                    log_event("Memory", "历史记忆整理失败", {"error": str(exc)}, severity="warning")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as exc:
                        log_event("Memory", "整理模型连接关闭失败", {"error": str(exc)}, severity="debug")
                with self._lock:
                    self._curation_active = False
                    pending = self._pending_timeline
                    self._pending_timeline = None
                    if not self._closed:
                        self._curation_cancel.clear()
                if pending is not None:
                    self.note_timeline_changed(pending)

        self._curation_threads.spawn(run, name="sakura-memory-backfill", daemon=True)

    def cancel_backfill(self) -> None:
        self._curation_cancel.set()

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
            self._store.download_embedding_model(
                progress=self._model_progress(progress),
                cancel=self._model_task_cancel,
            )
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
            log_event("Memory", "本地向量模型下载失败", {"error": str(exc)}, severity="warning")
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
        self._assert_writable(feature="memory.embedding_model")
        task_id = (
            _required_text(request.get("id"), "taskId", 256)
            if request is not None
            else f"memory-model-{uuid.uuid4().hex}"
        )
        with self._lock:
            if self._model_task_active:
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

    def note_timeline_changed(self, timeline: object, *, force: bool = False) -> None:
        """Catch up committed Timeline entries and schedule at most one curation job."""

        with self._lock:
            if self._closed:
                return
            if self._curation_active:
                self._pending_timeline = timeline
                return
            try:
                trigger, backfill, configured_slot = _curation_values(self._curation_config_getter())
                intervals = _read_timeline_interval(
                    timeline,
                    self._character_id,
                    self._curation_state.curation_cursor(),
                    backfill,
                )
                next_cursor = intervals[-1][1]
                self._curation_state.mark_timeline_synced(next_cursor)
                entries, pending = _curation_evidence_turns(
                    [entry for page, _cursor in intervals for entry in page]
                )
                self._curation_state.set_timeline_pending(pending)
                if not pending:
                    self._curation_state.mark_timeline_processed(next_cursor)
                    if force:
                        self._curation_result = {"message": "没有待更新的对话。"}
                    return
                if pending < trigger and not force:
                    return
                log_event(
                    "Memory",
                    "开始处理待整理对话",
                    {
                        "reason_code": "MANUAL_UPDATE" if force else "EVIDENCE_TURN_THRESHOLD",
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
                if not resolved["serviceKey"] or self._model_client_factory is None:
                    if force:
                        raise ValueError("请先在模型设置中选择记忆整理模型。")
                    return
                selected_ids = {entry.entry_id for entry in entries}
                intervals = [
                    ([entry for entry in page if entry.entry_id in selected_ids], cursor)
                    for page, cursor in intervals
                ]
                self._curation_active = True
                self._curation_result = {}
            except Exception as exc:
                if force:
                    raise
                log_event("Memory", "读取待整理对话失败", {"error": str(exc)}, severity="warning")
                return

        self._start_curation(intervals, resolved)

    def _start_curation(
        self,
        intervals: list[tuple[list[ChatHistoryEntry], str]],
        selection: Mapping[str, str],
    ) -> None:
        def curate() -> None:
            client = None
            pending_timeline: object | None = None
            operation_id = f"memory-curation-{uuid.uuid4().hex}"
            succeeded = False
            counts = {"created": 0, "updated": 0, "processed": 0}
            try:
                with interaction_context(operation_id):
                    if self._curation_cancel.is_set():
                        return
                    log_event(
                        "Memory",
                        "开始后台记忆整理",
                        {"history_messages": sum(len(page) for page, _cursor in intervals)},
                        severity="debug",
                    )
                    client = self._model_client_factory(selection)
                    curator = MemoryCurator(
                        client,
                        self._store.scoped(self._character_id),
                        system_prompt=self._system_prompt,
                    )

                    def check_cancelled() -> None:
                        if self._curation_cancel.is_set():
                            raise OperationCancelled()

                    remaining_turns = {
                        entry.turn_id for page, _cursor in intervals for entry in page
                    }
                    for entries, cursor in intervals:
                        check_cancelled()
                        result = curator.curate_entries(entries, cancel_checker=check_cancelled, cursor=cursor)
                        check_cancelled()
                        counts["created"] += result.created
                        counts["updated"] += result.updated
                        counts["processed"] += result.processed_entries
                        self._curation_result = dict(counts)
                        remaining_turns.difference_update(entry.turn_id for entry in entries)
                        self._curation_state.set_timeline_pending(len(remaining_turns))
                        log_event(
                            "Memory",
                            f"记忆已更新：新增 {result.created} 条，更新 {result.updated} 条，归档 {result.archived} 条"
                            if result.created or result.updated or result.archived else "这次整理没有需要更新的记忆",
                            {
                                "history_messages": len(entries),
                                "created": result.created,
                                "updated": result.updated,
                                "archived": result.archived,
                                "ignored": result.ignored,
                            },
                            severity="info" if result.created or result.updated or result.archived else "debug",
                        )
                    succeeded = True
            except OperationCancelled:
                return
            except Exception as exc:
                if getattr(exc, "code", "") in {"SERVICE_BINDING_EXPIRED", "GENERATION_INVALIDATED"}:
                    return
                self._curation_result = {**counts, "error": str(exc)}
                # Keep completed page cursors and any successful writes. A later
                # event retries the failed page; source IDs protect partial writes.
                with interaction_context(operation_id):
                    diagnostic_getter = getattr(self._store, "load_diagnostic", None)
                    diagnostic = (
                        diagnostic_getter() if callable(diagnostic_getter) else {}
                    )
                    reason_code = str(getattr(exc, "code", ""))
                    if reason_code not in {
                        "MEMORY_CURATION_SNAPSHOT_FAILED",
                        "MEMORY_CURATION_WRITE_FAILED",
                        "CURATION_RESPONSE_INVALID",
                    }:
                        reason_code = "CURATION_FAILED"
                    log_event(
                        "Memory",
                        "后台记忆整理失败，后续历史更新时重试",
                        {
                            "error_type": type(exc).__name__,
                            "error": str(exc),
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
                    pending_timeline = self._pending_timeline if succeeded else None
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
        self._curation_cancel.set()
        self._text_import_cancel.set()
        self._model_task_cancel.set()
        self._resources.stop_all(timeout_ms=10_000)
        self._store.close()
        append_memory_initialization_diagnostic(
            self._app_root,
            component="plugin_memory_owner",
            event="owner_close_completed",
            stage="shutdown",
            outcome="completed",
            status="stopped",
        )

    def _assert_writable(self, *, feature: str = "memory.manage") -> None:
        if self.status()["status"] == "stopped":
            raise MemoryBoundaryError(
                "MEMORY_READ_ONLY", "记忆存储已关闭。", feature=feature
            )


def _read_timeline_interval(
    timeline: object,
    character_id: str,
    cursor: str,
    backfill: int,
) -> list[tuple[list[ChatHistoryEntry], str]]:
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
    return [(entries, next_cursor)]


def _read_timeline_since(
    timeline: object,
    character_id: str,
    cursor: str,
) -> list[tuple[list[ChatHistoryEntry], str]]:
    pages: list[tuple[list[ChatHistoryEntry], str]] = []
    next_cursor = cursor
    while True:
        result = getattr(timeline, "read_since")({"cursor": next_cursor, "limit": 500})
        if not isinstance(result, Mapping):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        entries = _project_timeline_entries(result.get("entries"), character_id)
        candidate = result.get("nextCursor")
        has_more = result.get("hasMore")
        if not isinstance(candidate, str) or not candidate or not isinstance(has_more, bool):
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        if has_more and candidate == next_cursor:
            raise ValueError("TIMELINE_RESPONSE_INVALID")
        # Keep a Turn together when the host page limit falls inside it.
        if pages and {entry.turn_id for entry in pages[-1][0]} & {
            entry.turn_id for entry in entries
        }:
            entries = pages.pop()[0] + entries
        pages.append((entries, candidate))
        next_cursor = candidate
        if not has_more:
            return pages


def _project_timeline_entries(value: object, character_id: str, *, include_imported: bool = False) -> list[ChatHistoryEntry]:
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
        if isinstance(payload, Mapping) and payload.get("imported") and not include_imported:
            continue
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


def _read_timeline_range(timeline, character_id: str, saved: dict, cancelled):
    cursor = saved.get("curation_cursor", "")
    snapshot = saved.get("snapshotCursor", "")
    pending = None
    while True:
        cancelled()
        page = timeline.read_range({"startDate": saved["startDate"], "endDate": saved["endDate"],
                                    "cursor": cursor, "snapshotCursor": snapshot, "limit": 200})
        entries = _project_timeline_entries(page["entries"], character_id, include_imported=True)
        if pending is not None:
            if {entry.turn_id for entry in pending[0]} & {entry.turn_id for entry in entries}:
                entries = pending[0] + entries
            else:
                yield pending
        cursor, snapshot = page["nextCursor"], page["snapshotCursor"]
        pending = (entries, cursor, snapshot, page["total"])
        if not page["hasMore"]:
            yield pending
            return


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


def _curation_values(plugin: Mapping[str, object]) -> tuple[int, int, dict[str, str]]:
    trigger = _bounded_int(plugin.get("triggerTurns", 8), "triggerTurns", 1, 50)
    backfill = _bounded_int(plugin.get("backfillLimit", 200), "backfillLimit", 1, 100_000)
    configured = plugin.get("curationModelRef")
    if configured is None:
        profile = plugin.get("curationProfileId", "")
        configured = {"serviceKey": "sakura.model.openai_compatible" if profile else "",
                      "profileId": profile, "modelId": plugin.get("curationModel", "")}
    return trigger, backfill, _resolved_model(configured)


def _resolved_model(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"serviceKey", "profileId", "modelId"}:
        raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "记忆整理模型引用无效。")
    result = {key: _text(value.get(key), key, limit) for key, limit in (("serviceKey", 200), ("profileId", 64), ("modelId", 256))}
    if len({bool(item) for item in result.values()}) != 1:
        raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "记忆整理模型引用无效。")
    return result


def _project_memory(raw: Mapping[str, object], scope: str) -> dict[str, object] | None:
    if raw["scope"] != scope:
        return None
    projected: dict[str, object] = {
        **{key: raw[key] for key in ("id", "content", "layer", "category", "importance", "confidence", "source",
                                    "scope", "pinned", "state", "revision", "evidence", *KNOWLEDGE_FIELDS)},
        "createdAt": raw["created_at"],
        "updatedAt": raw["updated_at"],
        "lastAccessedAt": raw["last_accessed_at"],
        "occurredAt": raw["occurred_at"],
        "score": raw.get("score"),
        "retrieval": raw.get("retrieval", {}),
    }
    for field, key in (("source_turn_id", "sourceTurnId"), ("source_entry_ids", "sourceEntryIds"),
                       ("created_in_turn_id", "createdInTurnId"), ("evidence_kind", "evidenceKind")):
        if raw["metadata"].get(field):
            projected[key] = raw["metadata"][field]
    return projected



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


__all__ = ["MemoryBoundary", "MemoryBoundaryError"]
