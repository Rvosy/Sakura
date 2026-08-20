from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.agent.memory import MEMORY_LAYERS
from app.agent.memory_recall import MemoryRecallService
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.core_config_reader import CoreConfigReader
from app.config.yaml_config import load_yaml_mapping
from app.core_host.memory_boundary import MemoryBoundary, _project_memory
from app.llm.prompts.types import ContextMessage, ContextRequest
from app.storage.chat_history import ChatHistoryStore
from app.storage.paths import StoragePaths


PLUGIN_ID = "sakura.memory.mem0"
MEMORY_CONTEXT_PROVIDER_ID = "sakura.memory.mem0.recall"
MEMORY_SETTINGS_SECTION_ID = "memory"
MEMORY_COLLECTION_ID = "memories"
HOST_CHAT_COMPLETED_EVENT = "sakura.host.chat.completed"
_MODEL_SELECTION_SEPARATOR = "\x1f"
_MAX_COLLECTION_ITEMS = 10_000


class SakuraMem0Runtime:
    """Plugin-owned facade over the existing generation-private Memory runtime."""

    def __init__(
        self,
        app_root: Path,
        character_id: str,
        *,
        system_prompt: str = "",
        boundary: MemoryBoundary | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self._character_id = character_id
        self._boundary = boundary or MemoryBoundary(
            self._app_root,
            character_id,
            generation_id=f"plugin-worker-{os.getpid()}",
            system_prompt=system_prompt,
            agent_trace_recorder=_trace_recorder(self._app_root),
        )
        self._recall = MemoryRecallService(self._boundary)
        self._task_lock = threading.RLock()
        self._model_task_id = ""
        self._model_task_state = "idle"
        self._model_task_thread: threading.Thread | None = None
        self._closed = False

    @property
    def character_id(self) -> str:
        return self._character_id

    def context(self, request: object) -> list[dict[str, object]]:
        context_request = _context_request(request)
        if context_request.character_id != self._character_id:
            return []
        recalled = self._recall.recall(context_request)
        return [
            {
                "id": fragment.fragment_id,
                "content": fragment.content,
                "priority": fragment.priority,
                "budgetHint": fragment.token_budget,
                "sensitivity": fragment.sensitivity,
            }
            for fragment in recalled.fragments
        ]

    def search_tool(self, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._boundary.search_memory(dict(arguments), wait=False)

    def remember_tool(self, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._boundary.upsert({**dict(arguments), "source": "explicit"})

    def update_tool(self, arguments: Mapping[str, object]) -> dict[str, object]:
        values = {key: value for key, value in arguments.items() if key != "memory_id"}
        values.update({"id": arguments.get("memory_id"), "source": "explicit"})
        return self._boundary.upsert(values)

    def forget_tool(self, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._boundary.delete({"id": arguments.get("memory_id")})

    def settings_descriptor(self) -> dict[str, object]:
        snapshot = self._boundary.settings_get()
        model_options = self._model_options(snapshot)
        return {
            "sectionId": MEMORY_SETTINGS_SECTION_ID,
            "title": "长期记忆",
            "order": 40,
            "fields": [
                {
                    "key": "status",
                    "label": "运行状态",
                    "type": "readonly",
                    "default": "",
                    "description": "记忆故障不会阻断普通聊天。",
                },
                {
                    "key": "triggerTurns",
                    "label": "自动整理间隔",
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "maximum": 50,
                    "step": 1,
                    "description": "完成多少轮对话后尝试整理一次长期记忆。",
                },
                {
                    "key": "curationModel",
                    "label": "记忆整理模型",
                    "type": "select",
                    "default": "",
                    "options": model_options,
                    "description": "只供 Sakura MemoryCurator 使用；Mem0 不执行内置 LLM 推理。",
                },
                {
                    "key": "embeddingModel",
                    "label": "本地向量模型",
                    "type": "readonly",
                    "default": "",
                },
                {
                    "key": "embeddingStatus",
                    "label": "模型安装状态",
                    "type": "readonly",
                    "default": "",
                },
                {
                    "key": "backgroundTask",
                    "label": "后台任务",
                    "type": "readonly",
                    "default": "空闲",
                },
            ],
            "actions": [
                {
                    "actionId": "downloadEmbedding",
                    "label": "下载本地模型",
                    "description": "在插件后台下载固定版本的 ONNX 模型，不阻塞设置 Bridge。",
                },
                {
                    "actionId": "refreshStatus",
                    "label": "刷新状态",
                    "description": "读取当前记忆与后台任务状态。",
                },
                {
                    "actionId": "cancelEmbedding",
                    "label": "取消下载",
                    "description": "取消当前插件 generation 启动的模型下载任务。",
                },
            ],
            "collections": [
                {
                    "collectionId": MEMORY_COLLECTION_ID,
                    "title": "记忆条目",
                    "description": "管理当前角色在现有 Qdrant、SQLite 与核心档案中的长期记忆。",
                    "columns": [
                        {
                            "key": "content",
                            "label": "内容",
                            "type": "string",
                            "maxLength": 16_384,
                        },
                        {"key": "layer", "label": "分层", "type": "string"},
                        {"key": "category", "label": "类别", "type": "string"},
                        {"key": "source", "label": "来源", "type": "string"},
                        {"key": "importance", "label": "重要度", "type": "number"},
                        {"key": "confidence", "label": "置信度", "type": "number"},
                        {"key": "updatedAt", "label": "更新时间", "type": "datetime"},
                    ],
                    "fields": [
                        {
                            "key": "content",
                            "label": "内容",
                            "type": "text",
                            "default": None,
                            "required": True,
                            "maxLength": 16_384,
                        },
                        {
                            "key": "layer",
                            "label": "分层",
                            "type": "select",
                            "default": "semantic",
                            "required": True,
                            "options": _layer_options(),
                        },
                        {
                            "key": "category",
                            "label": "类别",
                            "type": "text",
                            "default": "",
                        },
                        {
                            "key": "source",
                            "label": "来源",
                            "type": "text",
                            "default": "explicit",
                        },
                        {
                            "key": "importance",
                            "label": "重要度",
                            "type": "number",
                            "default": 0.5,
                            "minimum": 0,
                            "maximum": 1,
                            "step": 0.05,
                        },
                        {
                            "key": "confidence",
                            "label": "置信度",
                            "type": "number",
                            "default": 0.8,
                            "minimum": 0,
                            "maximum": 1,
                            "step": 0.05,
                        },
                    ],
                    "filters": [
                        {"key": "layer", "label": "分层", "options": _layer_options()},
                    ],
                    "searchable": True,
                    "pageSize": 25,
                    "deleteConfirmation": "确定删除这条长期记忆吗？此操作不能撤销。",
                },
            ],
        }

    def load_settings(self) -> dict[str, object]:
        snapshot = self._boundary.settings_get()
        curation = _mapping(snapshot.get("curation"))
        slot = _mapping(snapshot.get("curationModelSlot"))
        embedding = _mapping(snapshot.get("embedding"))
        status = str(snapshot.get("status", "degraded"))
        message = str(snapshot.get("message", "")).strip()
        return {
            "status": message or _status_label(status),
            "triggerTurns": int(curation.get("triggerTurns", 8)),
            "curationModel": _encode_model_selection(slot),
            "embeddingModel": str(embedding.get("model", "")),
            "embeddingStatus": "已安装" if embedding.get("installed") is True else "未安装",
            "backgroundTask": self._task_label(),
        }

    def save_settings(self, values: Mapping[str, object]) -> dict[str, str]:
        current = self.load_settings()
        selection = _decode_model_selection(
            values.get("curationModel", current["curationModel"])
        )
        self._boundary.settings_save(
            {
                "triggerTurns": values.get("triggerTurns", current["triggerTurns"]),
                "curationModelSlot": selection,
            }
        )
        # The boundary reads both settings at curation time; no Core or plugin
        # generation replacement is required merely to change this selection.
        return {"applicationState": "applied"}

    def refresh_status(self, _values: Mapping[str, object]) -> dict[str, object]:
        return {"values": self.load_settings(), "message": "记忆状态已刷新。"}

    def start_model_download(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._task_lock:
            if self._closed:
                raise RuntimeError("MEMORY_STOPPED")
            if self._model_task_thread is not None and self._model_task_thread.is_alive():
                return {"values": self.load_settings(), "message": "模型下载已在进行中。"}
            task_id = f"memory-model-{uuid.uuid4().hex}"
            self._model_task_id = task_id
            self._model_task_state = "running"

            def run() -> None:
                try:
                    result = self._boundary.model_download({"id": task_id})
                    state = str(result.get("status", "failed"))
                except Exception:
                    state = "failed"
                with self._task_lock:
                    if self._model_task_id == task_id:
                        self._model_task_state = state

            thread = threading.Thread(
                target=run,
                name="sakura-mem0-model-download",
                daemon=True,
            )
            self._model_task_thread = thread
            thread.start()
        return {"values": self.load_settings(), "message": "模型下载已在后台启动。"}

    def cancel_model_download(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._task_lock:
            task_id = self._model_task_id
        result = self._boundary.model_cancel({"taskHandle": task_id}) if task_id else {"accepted": False}
        return {
            "values": self.load_settings(),
            "message": "已请求取消模型下载。" if result.get("accepted") else "当前没有可取消的下载任务。",
        }

    def query_collection(self, request: Mapping[str, object]) -> dict[str, object]:
        if self._boundary.status()["status"] != "ready":
            return {"items": [], "nextCursor": None, "total": 0}
        records = self._projected_records()
        search = str(request.get("search", "")).strip().casefold()
        filters = _mapping(request.get("filters"))
        layer = str(filters.get("layer", ""))
        if search:
            records = [
                item
                for item in records
                if search
                in " ".join(
                    str(item.get(key, ""))
                    for key in ("content", "category", "source")
                ).casefold()
            ]
        if layer:
            records = [item for item in records if item.get("layer") == layer]
        records.sort(
            key=lambda item: (str(item.get("updatedAt", "")), str(item.get("id", ""))),
            reverse=True,
        )
        try:
            offset = int(str(request.get("cursor") or "0"))
        except ValueError as error:
            raise ValueError("MEMORY_CURSOR_INVALID") from error
        if offset < 0:
            raise ValueError("MEMORY_CURSOR_INVALID")
        limit = max(1, min(100, int(request.get("limit", 25))))
        page: list[dict[str, object]] = []
        for item in records[offset : offset + limit]:
            projected = _collection_item(item)
            candidate = {
                "items": [*page, projected],
                "nextCursor": str(offset + len(page) + 1),
                "total": len(records),
            }
            if page and not _json_fits(candidate, 240 * 1024):
                break
            page.append(projected)
        next_offset = offset + len(page)
        return {
            "items": page,
            "nextCursor": str(next_offset) if next_offset < len(records) else None,
            "total": len(records),
        }

    def create_collection_item(self, values: Mapping[str, object]) -> dict[str, object]:
        result = self._boundary.upsert({**dict(values), "source": values.get("source") or "explicit"})
        return _collection_item(_mapping(result.get("memory")))

    def update_collection_item(
        self,
        item_id: str,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        current = next(
            (item for item in self._projected_records() if item.get("id") == item_id),
            None,
        )
        if current is None:
            raise ValueError("MEMORY_NOT_FOUND")
        writable = {
            key: current.get(key)
            for key in ("content", "layer", "category", "source", "importance", "confidence")
        }
        writable.update(values)
        result = self._boundary.upsert({"id": item_id, **writable})
        return _collection_item(_mapping(result.get("memory")))

    def delete_collection_item(self, item_id: str) -> dict[str, bool]:
        result = self._boundary.delete({"id": item_id})
        return {"deleted": not bool(result.get("alreadyMissing"))}

    def note_completed_chat(self, payload: object) -> None:
        if not isinstance(payload, Mapping) or payload.get("characterId") != self._character_id:
            return
        messages = payload.get("messages")
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            return
        if any(
            not isinstance(item, Mapping)
            or item.get("role") not in {"user", "assistant"}
            or not isinstance(item.get("content"), str)
            for item in messages
        ):
            return
        history = ChatHistoryStore(
            StoragePaths(self._app_root).chat_history_for(self._character_id)
        )
        self._boundary.note_completed_chat(history)

    def close(self) -> None:
        with self._task_lock:
            if self._closed:
                return
            self._closed = True
        self._boundary.close()
        with self._task_lock:
            thread = self._model_task_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.8)

    def _projected_records(self) -> list[dict[str, object]]:
        records = self._boundary.memory_store.list_memories(limit=None)
        projected = [
            item
            for raw in records[:_MAX_COLLECTION_ITEMS]
            if isinstance(raw, Mapping)
            and (item := _project_memory(raw, self._character_id)) is not None
        ]
        return projected

    def _model_options(self, snapshot: Mapping[str, object]) -> list[dict[str, str]]:
        selected = _encode_model_selection(_mapping(snapshot.get("curationModelSlot")))
        options = [{"label": "不启用自动整理", "value": ""}]
        for provider in snapshot.get("providerChoices", []):
            if not isinstance(provider, Mapping):
                continue
            provider_id = str(provider.get("id", ""))
            alias = str(provider.get("alias", provider_id))
            models = provider.get("models", [])
            if not provider_id or not isinstance(models, list):
                continue
            for model in models:
                encoded = _encode_model_selection(
                    {"profileId": provider_id, "model": str(model)}
                )
                options.append({"label": f"{alias} / {model}", "value": encoded})
        unique = {item["value"]: item for item in options}
        bounded = list(unique.values())[:64]
        if selected and selected not in {item["value"] for item in bounded}:
            bounded[-1:] = [{"label": "当前选择", "value": selected}]
        return bounded

    def _task_label(self) -> str:
        with self._task_lock:
            state = self._model_task_state
        return {
            "idle": "空闲",
            "running": "下载中",
            "completed": "已完成",
            "cancelled": "已取消",
            "failed": "失败（旧缓存保持不变）",
        }.get(state, "空闲")


class SakuraMem0Plugin:
    def __init__(
        self,
        runtime_factory: Callable[[], SakuraMem0Runtime] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory or _default_runtime

    def setup(self, context: object) -> None:
        runtime = self._runtime_factory()
        getattr(context, "effect")(runtime.close)
        getattr(context, "on")(HOST_CHAT_COMPLETED_EVENT, runtime.note_completed_chat)
        getattr(context, "get")("sakura.host.context").register(
            {
                "providerId": MEMORY_CONTEXT_PROVIDER_ID,
                "description": "从当前角色的本地长期记忆中选择与本轮相关的少量事实。",
                "order": 60,
            },
            runtime.context,
        )
        tools = getattr(context, "get")("sakura.host.tools")
        for descriptor, callback in _tool_registrations(runtime):
            tools.register(descriptor, callback)
        getattr(context, "get")("sakura.host.settings").register(
            runtime.settings_descriptor(),
            load=runtime.load_settings,
            save=runtime.save_settings,
            actions={
                "downloadEmbedding": runtime.start_model_download,
                "refreshStatus": runtime.refresh_status,
                "cancelEmbedding": runtime.cancel_model_download,
            },
            collections={
                MEMORY_COLLECTION_ID: {
                    "query": runtime.query_collection,
                    "create": runtime.create_collection_item,
                    "update": runtime.update_collection_item,
                    "delete": runtime.delete_collection_item,
                }
            },
        )


def _default_runtime() -> SakuraMem0Runtime:
    app_root = _assistant_root_from_module()
    config = CoreConfigReader().read(app_root)
    if config.config_problem is not None or not config.current_character_id:
        raise RuntimeError("MEMORY_CHARACTER_UNAVAILABLE")
    registry = CharacterRegistry(app_root)
    profile = registry.profiles.get(config.current_character_id)
    if profile is None:
        profile = registry.profiles.get(DEFAULT_CHARACTER_ID)
    if profile is None:
        profiles = registry.all()
        if not profiles:
            raise RuntimeError("MEMORY_CHARACTER_UNAVAILABLE")
        profile = profiles[0]
    return SakuraMem0Runtime(
        app_root,
        profile.id,
        system_prompt=load_character_system_prompt(profile),
    )


def _assistant_root_from_module(module_file: str | Path = __file__) -> Path:
    """Resolve the bundled layout without expanding the public Host API."""

    path = Path(module_file).resolve()
    try:
        root = path.parents[2]
    except IndexError as error:
        raise RuntimeError("MEMORY_PLUGIN_LAYOUT_INVALID") from error
    if not (root / "app").is_dir() or not (root / "plugins").is_dir():
        raise RuntimeError("MEMORY_PLUGIN_LAYOUT_INVALID")
    return root


def _trace_recorder(app_root: Path) -> object:
    from app.agent.trace import AgentTraceRecorder, normalize_agent_trace_settings

    settings = normalize_agent_trace_settings(
        load_yaml_mapping(app_root / "data" / "config" / "system_config.yaml").get(
            "agent_trace"
        )
    )
    return AgentTraceRecorder(app_root, settings)


def _tool_registrations(
    runtime: SakuraMem0Runtime,
) -> list[tuple[dict[str, object], Callable[[Mapping[str, object]], object]]]:
    return [
        (
            {
                "name": "memory_search",
                "description": "搜索当前角色的长期记忆；需要跨会话事实、偏好或项目状态时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                    },
                    "required": ["query"],
                },
                "group": "plugin",
                "risk": "low",
            },
            runtime.search_tool,
        ),
        (
            {
                "name": "memory_remember",
                "description": "保存一条明确、长期有用且不含凭据或身份秘密的当前角色记忆。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                        "category": {"type": "string"},
                        "importance": {"type": "number"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["content"],
                },
                "group": "plugin",
                "risk": "medium",
            },
            runtime.remember_tool,
        ),
        (
            {
                "name": "memory_update",
                "description": "更新一条当前角色的长期记忆；应先搜索并取得准确的 memory_id。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                        "category": {"type": "string"},
                        "importance": {"type": "number"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["memory_id", "content"],
                },
                "group": "plugin",
                "risk": "medium",
            },
            runtime.update_tool,
        ),
        (
            {
                "name": "memory_forget",
                "description": "按 memory_id 删除当前角色的一条长期记忆；只在用户明确要求忘记时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {"memory_id": {"type": "string"}},
                    "required": ["memory_id"],
                },
                "group": "plugin",
                "risk": "high",
            },
            runtime.forget_tool,
        ),
    ]


def _layer_options() -> list[dict[str, str]]:
    labels = {
        "core_profile": "核心档案",
        "semantic": "语义记忆",
        "episodic": "情景记忆",
        "procedural": "程序记忆",
        "session": "会话记忆",
    }
    return [{"label": labels.get(layer, layer), "value": layer} for layer in MEMORY_LAYERS]


def _collection_item(memory: Mapping[str, object]) -> dict[str, object]:
    item_id = str(memory.get("id", ""))
    if not item_id:
        raise ValueError("MEMORY_RESPONSE_INVALID")
    return {
        "itemId": item_id,
        "values": {
            key: memory.get(key, "")
            for key in (
                "content",
                "layer",
                "category",
                "source",
                "importance",
                "confidence",
                "updatedAt",
            )
        },
    }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _context_request(value: object) -> ContextRequest:
    if isinstance(value, ContextRequest):
        return value
    raw = _mapping(value)
    recent: list[ContextMessage] = []
    messages = raw.get("recent_messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for item in messages[:8]:
            message = _mapping(item)
            role = str(message.get("role", ""))
            content = message.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                recent.append(ContextMessage(role, content[:2000]))
    return ContextRequest(
        current_input=str(raw.get("current_input", ""))[:4096],
        character_id=str(raw.get("character_id", ""))[:128],
        character_name=str(raw.get("character_name", ""))[:120],
        source=(
            raw.get("source")
            if raw.get("source") in {"chat", "event", "confirmed_action"}
            else "chat"
        ),
        mode=(
            raw.get("mode")
            if raw.get("mode") in {"normal", "screen_awareness"}
            else "normal"
        ),
        event_type=str(raw.get("event_type", ""))[:64],
        step_index=_bounded_context_int(raw.get("step_index"), 0, 32),
        remaining_steps=_bounded_context_int(raw.get("remaining_steps"), 0, 32),
        recent_messages=tuple(recent),
        available_tools=tuple(
            str(item)[:64]
            for item in (
                raw.get("available_tools", [])
                if isinstance(raw.get("available_tools"), list)
                else []
            )[:64]
        ),
        screen_context_available=bool(raw.get("screen_context_available")),
        current_time=str(raw.get("current_time", ""))[:80],
    )


def _bounded_context_int(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return min(maximum, max(minimum, value))
    return minimum


def _json_fits(value: object, maximum: int) -> bool:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= maximum


def _encode_model_selection(slot: Mapping[str, object]) -> str:
    profile_id = str(slot.get("profileId", "")).strip()
    model = str(slot.get("model", "")).strip()
    return f"{profile_id}{_MODEL_SELECTION_SEPARATOR}{model}" if profile_id and model else ""


def _decode_model_selection(value: object) -> dict[str, str]:
    text = str(value or "")
    if not text:
        return {"profileId": "", "model": ""}
    if text.count(_MODEL_SELECTION_SEPARATOR) != 1:
        raise ValueError("MODEL_REFERENCE_INVALID")
    profile_id, model = text.split(_MODEL_SELECTION_SEPARATOR, 1)
    if not profile_id or not model:
        raise ValueError("MODEL_REFERENCE_INVALID")
    return {"profileId": profile_id, "model": model}


def _status_label(status: str) -> str:
    return {
        "ready": "就绪",
        "loading": "初始化中",
        "degraded": "暂时不可用（聊天可继续）",
        "read_only": "只读",
        "failed": "不可用",
        "stopped": "已停止",
    }.get(status, "状态未知")


__all__ = ["SakuraMem0Plugin", "SakuraMem0Runtime"]
