"""Qt-free, generation-scoped Memory domain for Runtime v2.

The boundary is deliberately narrow: it owns the existing ``MemoryStore`` and
curation resources, validates the public protocol, and projects records into a
stable DTO.  It never imports the legacy application bootstrap or a Qt worker.
"""

from __future__ import annotations

import json
import tempfile
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from app.agent.memory import (
    DEFAULT_EMBEDDING_DIMS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MEMORY_CONFIDENCE,
    DEFAULT_MEMORY_IMPORTANCE,
    DEFAULT_MEMORY_LAYER,
    MEMORY_LAYERS,
    MemoryModelTaskCancelled,
    MemoryStore,
)
from app.agent.memory_curator import MemoryCurationState, MemoryCurator
from app.config.models import MODEL_SLOT_MEMORY_CURATION
from app.core.resource_manager import ResourceRegistry
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.storage.atomic import atomic_write_text
from app.storage.chat_history import ChatHistoryStore
from app.storage.paths import StoragePaths
from app.core_host.protocol import event
from app.core_host.server import MEMORY_REQUEST_NAMES
MEMORY_STATUSES = frozenset({"ready", "loading", "degraded", "read_only", "failed", "stopped"})
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
        api_settings: ApiSettings,
        *,
        generation_id: str = "test-generation",
        system_prompt: str = "",
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self._character_id = _required_text(character_id, "character_id", 128)
        self._generation_id = _required_text(generation_id, "generation_id", 256)
        self._system_prompt = system_prompt.strip()
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._closed = False
        self._status: Literal[
            "ready", "loading", "degraded", "read_only", "failed", "stopped"
        ] = "loading"
        self._message = "长期记忆系统正在初始化。"
        self._curation_cancel = threading.Event()
        self._curation_active = False
        self._model_task_active = False
        self._model_task_id = ""
        self._model_task_cancel = threading.Event()
        self._event_publisher: Callable[[dict[str, Any]], None] | None = None
        self._preload_started = False
        self._resources = ResourceRegistry()
        self._curation_threads = self._resources.track_thread_group(
            cancel=self._curation_cancel.set,
            label="runtime_v2_memory_curation",
            shutdown_order=1100,
        )
        self._store = memory_store or MemoryStore(
            base_dir=self._app_root,
            api_settings=api_settings,
            scope_id=self._character_id,
            resource_registry=self._resources,
        )
        self._store.add_status_listener(self._on_store_status)
        self._curation_state = MemoryCurationState(
            StoragePaths(self._app_root).memory_curation_state()
        )
        # Missing embeddings must never cause implicit network access.  The user
        # can explicitly start the fixed-model download from the settings page.
        if self._store.is_ready():
            self._set_status("ready", "")
        elif self._store.needs_embedding_model_download():
            self._set_status("degraded", "本地记忆模型尚未安装；聊天将继续但不会召回记忆。")

    @property
    def memory_store(self) -> MemoryStore:
        return self._store

    @property
    def character_id(self) -> str:
        return self._character_id

    def __bool__(self) -> bool:
        return True

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if self._event_publisher is not None:
                return
            self._event_publisher = publisher

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
        with self._lock:
            if (
                not self._closed
                and not self._preload_started
                and self._status == "loading"
                and not self._store.needs_embedding_model_download()
            ):
                self._preload_started = True
                self._store.preload(wait=False)
            if not self._closed and self._status in {"loading", "degraded"} and self._store.is_ready():
                self._status = "ready"
                self._message = ""
            return {"status": self._status, "message": self._message}

    def handle(
        self,
        name: str,
        payload: object,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        if name not in MEMORY_REQUEST_NAMES:
            raise MemoryBoundaryError("UNKNOWN_MEMORY_REQUEST", "不支持的记忆请求。")
        with self._lock:
            if self._closed:
                raise MemoryBoundaryError(
                    "MEMORY_STOPPED", "记忆能力已停止。", feature="memory.manage"
                )
        if not isinstance(payload, Mapping):
            raise MemoryBoundaryError("INVALID_REQUEST", "记忆请求格式无效。")
        if name == "memory.search":
            return self.search(payload)
        if name == "memory.upsert":
            return self.upsert(payload)
        if name == "memory.delete":
            return self.delete(payload)
        if name == "memory.settings.get":
            _only(payload, set())
            return self.settings_get()
        if name == "memory.settings.save":
            return self.settings_save(payload)
        if name == "memory.model.download":
            _only(payload, set())
            return self.model_download(request)
        if name == "memory.model.import":
            _only(payload, {"selectionToken"})
            return self.model_import(payload, request)
        _only(payload, {"taskHandle"})
        return self.model_cancel(payload)

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
            self._set_status("degraded", "记忆检索暂时不可用；聊天不受影响。")
            return {**self.status(), "memories": []}
        status = str(result.get("status") or "ready")
        if status != "ready":
            safe_status = status if status in MEMORY_STATUSES else "degraded"
            self._set_status(safe_status, "记忆检索暂时不可用；聊天不受影响。")
            return {**self.status(), "memories": []}
        memories = result.get("memories")
        if not isinstance(memories, list):
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
            {"id", "content", "layer", "category", "source", "importance", "confidence"},
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
            system, api = self._read_settings_documents()
            trigger, backfill = _curation_values(system)
            slot = _memory_slot(api)
            providers = _public_provider_choices(api)
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
                "available": bool(slot["profileId"] and slot["model"]),
            },
            "curationModelSlot": slot,
            "providerChoices": providers,
            "embedding": {
                "model": DEFAULT_EMBEDDING_MODEL,
                "dimensions": DEFAULT_EMBEDDING_DIMS,
                "installed": not self._store.needs_embedding_model_download(),
                "task": None,
            },
        }

    def settings_save(self, payload: Mapping[str, object]) -> dict[str, object]:
        _only(payload, {"triggerTurns", "curationModelSlot"})
        self._assert_writable(require_ready=False, feature="memory.curation")
        trigger = _bounded_int(payload.get("triggerTurns"), "triggerTurns", 1, 50)
        raw_slot = payload.get("curationModelSlot")
        if raw_slot is not None and not isinstance(raw_slot, Mapping):
            raise MemoryBoundaryError("FIELD_INVALID", "记忆整理模型槽无效。", field="curationModelSlot")
        try:
            system, api = self._read_settings_documents()
            _old_trigger, backfill = _curation_values(system)
            old_slot = _memory_slot(api)
            new_slot = _parse_slot(raw_slot, api)
            memory_section = dict(system.get("memory_curation", {}))
            memory_section.update({"enabled": True, "trigger_turns": trigger, "backfill_limit": backfill})
            system["memory_curation"] = memory_section
            slots = dict(api.get("model_slots", {}))
            if new_slot["profileId"]:
                slots[MODEL_SLOT_MEMORY_CURATION] = {
                    "profile_id": new_slot["profileId"], "model": new_slot["model"]
                }
            else:
                slots.pop(MODEL_SLOT_MEMORY_CURATION, None)
            api["model_slots"] = slots
            system_path = self._app_root / "data" / "config" / "system_config.yaml"
            api_path = self._app_root / "data" / "config" / "api.yaml"
            old_api_bytes = api_path.read_bytes()
            api_changed = new_slot != old_slot
            if api_changed:
                atomic_write_text(
                    api_path,
                    yaml.safe_dump(api, allow_unicode=True, sort_keys=False),
                    backup=False,
                )
            try:
                atomic_write_text(
                    system_path,
                    yaml.safe_dump(system, allow_unicode=True, sort_keys=False),
                    backup=False,
                )
            except OSError:
                if api_changed:
                    try:
                        atomic_write_text(
                            api_path,
                            old_api_bytes.decode("utf-8"),
                            backup=False,
                        )
                    except (OSError, UnicodeError):
                        self._set_status(
                            "read_only",
                            "记忆设置回滚失败；请在重试前检查配置文件。",
                        )
                raise
        except MemoryBoundaryError:
            raise
        except OSError as exc:
            raise MemoryBoundaryError(
                "CONFIG_SAVE_FAILED", "记忆设置保存失败，原文件保持不变。", retryable=True,
                feature="memory.curation",
            ) from exc
        restart = new_slot != old_slot
        return {
            "saved": True,
            "changePlan": "core_restart_required" if restart else "applied",
            "curation": {"triggerTurns": trigger, "backfillLimit": backfill},
            "curationModelSlot": new_slot,
        }

    def model_download(
        self,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        task_id = self._begin_model_task(request)
        self._publish_model_event(request, "memory.model.started", task_id, "starting", 0)
        try:
            with self._write_lock:
                self._store.download_embedding_model(
                    progress=self._model_progress(request, task_id),
                    cancel=self._model_task_cancel,
                )
            self._set_status("loading", "本地记忆模型已安装，正在初始化记忆。")
            self._publish_model_event(
                request, "memory.model.completed", task_id, "completed", 100
            )
            status = "completed"
        except MemoryModelTaskCancelled:
            self._publish_model_event(
                request, "memory.model.cancelled", task_id, "cancelled", 0
            )
            status = "cancelled"
        except Exception:
            self._set_status("degraded", "本地记忆模型下载失败；原缓存保持不变。")
            self._publish_model_event(
                request,
                "memory.model.failed",
                task_id,
                "failed",
                0,
                error={
                    "code": "MODEL_DOWNLOAD_FAILED",
                    "message": "记忆模型下载失败，原缓存保持不变。",
                    "retryable": True,
                },
            )
            status = "failed"
        finally:
            self._finish_model_task(task_id)
        return {"accepted": True, "taskId": task_id, "status": status}

    def model_import(
        self,
        payload: Mapping[str, object],
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        self._assert_writable(require_ready=False, feature="memory.embedding_model")
        token = _required_text(payload.get("selectionToken"), "selectionToken", 128)
        if not token.isascii() or not token.replace("-", "").isalnum():
            raise MemoryBoundaryError(
                "SELECTION_TOKEN_INVALID", "所选模型归档令牌无效或已过期。",
                feature="memory.embedding_model", field="selectionToken",
            )
        selection = (
            Path(tempfile.gettempdir())
            / "sakura-runtime-v2-memory-selections"
            / f"{token}.json"
        )
        try:
            stat = selection.lstat()
            if not selection.is_file() or selection.is_symlink() or stat.st_size > 16_384:
                raise OSError("unsafe selection")
            document = json.loads(selection.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MemoryBoundaryError(
                "SELECTION_TOKEN_INVALID", "所选模型归档令牌无效或已过期。",
                feature="memory.embedding_model", field="selectionToken",
            ) from exc
        finally:
            try:
                selection.unlink(missing_ok=True)
                selection.parent.rmdir()
            except OSError:
                pass
        if (
            not isinstance(document, Mapping)
            or set(document) != {"generationId", "path"}
            or document.get("generationId") != self._generation_id
        ):
            raise MemoryBoundaryError(
                "SELECTION_TOKEN_STALE", "所选模型归档令牌已过期。",
                feature="memory.embedding_model", field="selectionToken",
            )
        archive = Path(str(document.get("path", "")))
        if (
            not archive.is_absolute()
            or archive.suffix.lower() != ".zip"
            or not archive.is_file()
            or archive.is_symlink()
        ):
            raise MemoryBoundaryError(
                "MODEL_ARCHIVE_INVALID", "所选文件不是有效的记忆模型 ZIP。",
                feature="memory.embedding_model",
            )
        task_id = self._begin_model_task(request)
        self._publish_model_event(request, "memory.model.started", task_id, "validating", 0)
        try:
            with self._write_lock:
                self._store.import_embedding_model_archive(
                    archive,
                    progress=self._model_progress(request, task_id),
                    cancel=self._model_task_cancel,
                )
            self._preload_started = True
            self._set_status("loading", "本地记忆模型已导入，正在初始化记忆。")
            self._publish_model_event(
                request, "memory.model.completed", task_id, "completed", 100
            )
            status = "completed"
        except MemoryModelTaskCancelled:
            self._publish_model_event(
                request, "memory.model.cancelled", task_id, "cancelled", 0
            )
            status = "cancelled"
        except MemoryBoundaryError:
            raise
        except Exception:
            self._publish_model_event(
                request,
                "memory.model.failed",
                task_id,
                "failed",
                0,
                error={
                    "code": "MODEL_IMPORT_FAILED",
                    "message": "记忆模型导入失败，原缓存保持不变。",
                    "retryable": True,
                },
            )
            status = "failed"
        finally:
            self._finish_model_task(task_id)
        return {"accepted": True, "taskId": task_id, "status": status}

    def model_cancel(self, payload: Mapping[str, object]) -> dict[str, object]:
        task_id = _required_text(payload.get("taskHandle"), "taskHandle", 256)
        with self._lock:
            accepted = self._model_task_active and self._model_task_id == task_id
            if accepted:
                self._model_task_cancel.set()
        return {"accepted": accepted, "taskId": task_id if accepted else ""}

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
        return task_id

    def _finish_model_task(self, task_id: str) -> None:
        with self._lock:
            if self._model_task_id == task_id:
                self._model_task_active = False
                self._model_task_id = ""
                self._model_task_cancel.clear()

    def _model_task_cancelled(self) -> None:
        if self._model_task_cancel.is_set() or self._closed:
            raise MemoryModelTaskCancelled("记忆模型任务已取消。")

    def _model_progress(
        self,
        request: Mapping[str, Any] | None,
        task_id: str,
    ) -> Callable[[str, int], None]:
        last_progress = -5

        def publish(stage: str, progress: int) -> None:
            nonlocal last_progress
            self._model_task_cancelled()
            bounded = max(0, min(100, int(progress)))
            if bounded < 100 and bounded < last_progress + 5:
                return
            last_progress = max(last_progress, bounded)
            self._publish_model_event(
                request, "memory.model.progress", task_id, stage, bounded
            )

        return publish

    def _publish_model_event(
        self,
        request: Mapping[str, Any] | None,
        name: str,
        task_id: str,
        stage: str,
        progress: int,
        *,
        error: Mapping[str, object] | None = None,
    ) -> None:
        publisher = self._event_publisher
        if publisher is None or request is None:
            return
        payload: dict[str, object] = {
            "taskId": task_id,
            "stage": stage,
            "progress": max(0, min(100, int(progress))),
        }
        if error is not None:
            payload["error"] = dict(error)
        publisher(
            event(
                request,
                generation_id=self._generation_id,
                generation_credential=str(request.get("generationCredential", "")),
                protocol_minor=int(request.get("protocolMinor", 0)),
                name=name,
                payload=payload,
            )
        )

    def note_completed_chat(self, history: ChatHistoryStore) -> None:
        """Count one fully persisted turn and schedule at most one curation job."""

        with self._lock:
            if self._closed:
                return
            try:
                pending = self._curation_state.increment_pending_turns()
                system, api = self._read_settings_documents()
                trigger, backfill = _curation_values(system)
                slot = _memory_slot(api)
                if (
                    pending < trigger
                    or self._curation_active
                    or self._model_task_active
                    or not slot["profileId"]
                ):
                    return
                settings = _resolve_api_settings(api, slot)
                entries = self._curation_state.unprocessed_entries(history.load())[-backfill:]
                if not entries:
                    return
                processed_count = history.total_count()
                consumed_turns = pending
                self._curation_active = True
            except Exception:
                return

        def curate() -> None:
            client: OpenAICompatibleClient | None = None
            try:
                if self._curation_cancel.is_set():
                    return
                client = OpenAICompatibleClient(settings)
                curator = MemoryCurator(
                    client,
                    self._store.scoped(self._character_id),
                    system_prompt=self._system_prompt,
                )
                def check_cancelled() -> None:
                    if self._curation_cancel.is_set():
                        from app.core.cancellation import OperationCancelled

                        raise OperationCancelled()

                with self._write_lock:
                    curator.curate_entries(entries, cancel_checker=check_cancelled)
                if self._curation_cancel.is_set():
                    return
                self._curation_state.mark_processed(
                    processed_count, consumed_turns=consumed_turns, backfill_completed=True
                )
            except Exception:
                # Cursor and existing memories remain untouched; the next
                # generation can retry the same committed interval.
                return
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                with self._lock:
                    self._curation_active = False

        if self._curation_threads.spawn(
            curate, name="sakura-runtime-v2-memory-curation", daemon=True
        ) is None:
            with self._lock:
                self._curation_active = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._status = "stopped"
            self._message = "记忆能力已停止。"
        self._curation_cancel.set()
        self._model_task_cancel.set()
        self._resources.stop_all(timeout_ms=0)
        self._store.remove_status_listener(self._on_store_status)
        self._store.close()

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

    def _read_settings_documents(self) -> tuple[dict[str, Any], dict[str, Any]]:
        config = self._app_root / "data" / "config"
        system = _read_yaml(config / "system_config.yaml")
        api = _read_yaml(config / "api.yaml")
        if system.get("config_version") != 4:
            raise MemoryBoundaryError(
                "CONFIG_VERSION_UNSUPPORTED", "配置版本不受支持。", feature="memory.curation"
            )
        if not isinstance(api.get("model_slots", {}), Mapping):
            raise MemoryBoundaryError(
                "CONFIG_DATA_INVALID", "模型槽配置格式无效。", feature="model.memory_curation_slot"
            )
        return system, api

    def _on_store_status(self, status: str, message: str) -> None:
        projected = {
            "idle": "loading", "reloading": "loading", "ready": "ready",
            "failed": "degraded", "stopped": "stopped",
        }.get(status, "degraded")
        self._set_status(projected, _public_status_message(projected, message))

    def _set_status(self, status: str, message: str) -> None:
        safe = status if status in MEMORY_STATUSES else "degraded"
        with self._lock:
            if self._closed and safe != "stopped":
                return
            self._status = safe  # type: ignore[assignment]
            self._message = message


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "记忆设置数据不可用。") from exc
    if not isinstance(value, Mapping):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "记忆设置数据不可用。")
    return dict(value)


def _curation_values(system: Mapping[str, Any]) -> tuple[int, int]:
    raw = system.get("memory_curation", {})
    if not isinstance(raw, Mapping):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "记忆整理设置格式无效。")
    trigger = raw.get("trigger_turns", 8)
    backfill = raw.get("backfill_limit", 200)
    return (
        _bounded_int(trigger, "trigger_turns", 1, 50),
        _bounded_int(backfill, "backfill_limit", 1, 100_000),
    )


def _memory_slot(api: Mapping[str, Any]) -> dict[str, str]:
    slots = api.get("model_slots", {})
    raw = slots.get(MODEL_SLOT_MEMORY_CURATION, {}) if isinstance(slots, Mapping) else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "记忆整理模型槽格式无效。")
    profile = _text(raw.get("profile_id"), "profile_id", 64)
    model = _text(raw.get("model"), "model", 256)
    if bool(profile) != bool(model):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "记忆整理模型槽不完整。")
    return {"profileId": profile, "model": model}


def _public_provider_choices(api: Mapping[str, Any]) -> list[dict[str, object]]:
    raw = api.get("api_profiles", [])
    if not isinstance(raw, list):
        raise MemoryBoundaryError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
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


def _parse_slot(raw: Mapping[str, object] | None, api: Mapping[str, Any]) -> dict[str, str]:
    if raw is None:
        return {"profileId": "", "model": ""}
    _only(raw, {"profileId", "model"})
    profile = _text(raw.get("profileId"), "profileId", 64)
    model = _text(raw.get("model"), "model", 256)
    if bool(profile) != bool(model):
        raise MemoryBoundaryError("FIELD_INVALID", "模型槽必须同时选择 Provider 和模型。")
    if not profile:
        return {"profileId": "", "model": ""}
    choices = {item["id"]: item for item in _public_provider_choices(api)}
    selected = choices.get(profile)
    if selected is None or model not in selected["models"]:
        raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "模型槽引用无效。")
    return {"profileId": profile, "model": model}


def _resolve_api_settings(api: Mapping[str, Any], slot: Mapping[str, str]) -> ApiSettings:
    for item in api.get("api_profiles", []):
        if isinstance(item, Mapping) and item.get("id") == slot["profileId"]:
            return ApiSettings(
                base_url=_required_text(item.get("base_url"), "base_url", 2048),
                api_key=_required_text(item.get("api_key"), "api_key", 16_384),
                model=slot["model"],
            )
    raise MemoryBoundaryError("MODEL_REFERENCE_INVALID", "记忆整理模型槽引用无效。")


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
    return {
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


__all__ = ["MEMORY_REQUEST_NAMES", "MemoryBoundary", "MemoryBoundaryError"]
