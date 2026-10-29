from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

try:
    from .boundary import MemoryBoundary, _project_memory
    from .memory import ENTITY_KINDS, KNOWLEDGE_FIELDS, MEMORY_LAYERS
    from .memory_recall import MemoryRecallService
    from .domain_types import ContextMessage, ContextRequest
    from .support import bind_logger, log_event
except ImportError:
    from boundary import MemoryBoundary, _project_memory
    from memory import ENTITY_KINDS, KNOWLEDGE_FIELDS, MEMORY_LAYERS
    from memory_recall import MemoryRecallService
    from domain_types import ContextMessage, ContextRequest
    from support import bind_logger, log_event


PLUGIN_ID = "sakura.memory.mem0"
MEMORY_CONTEXT_PROVIDER_ID = "sakura.memory.mem0.recall"
MEMORY_SETTINGS_SECTION_ID = "memory"
MEMORY_COMPONENT_SECTION_ID = "memory_embedding_component"
MEMORY_MANAGEMENT_SECTION_ID = "memory_management"
MEMORY_OPTIMIZATION_SECTION_ID = "memory_optimization"
MEMORY_INDEX_SECTION_ID = "memory_index"
MEMORY_BACKFILL_SECTION_ID = "memory_backfill"
MEMORY_TEXT_IMPORT_SECTION_ID = "memory_text_import"
MEMORY_COLLECTION_ID = "memories"
HOST_CHAT_COMPLETED_EVENT = "sakura.host.chat.completed"


class SakuraMem0Runtime:
    """Plugin-owned facade over the existing generation-private Memory runtime."""

    def __init__(
        self,
        app_root: Path,
        character_id: str,
        *,
        system_prompt: str = "",
        boundary: MemoryBoundary | None = None,
        timeline: object | None = None,
        config_getter: Callable[[], Mapping[str, object]] | None = None,
        config_updater: Callable[[Mapping[str, object]], object] | None = None,
        memory_dir: Path | None = None,
        memory_cache_dir: Path | None = None,
        model_catalog_getter: Callable[[], object] | None = None,
        model_resolver: Callable[[Mapping[str, object]], object] | None = None,
        model_client_factory: Callable[[Mapping[str, object]], object] | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self._character_id = character_id
        self._config_getter = config_getter or (lambda: {})
        self._config_updater = config_updater or (lambda _values: None)
        self._timeline = timeline
        self._boundary = boundary or MemoryBoundary(
            self._app_root,
            character_id,
            system_prompt=system_prompt,
            memory_dir=memory_dir,
            memory_cache_dir=memory_cache_dir,
            curation_config_getter=self._config_getter,
            model_catalog_getter=model_catalog_getter,
            model_resolver=model_resolver,
            model_client_factory=model_client_factory,
        )
        self._recall = MemoryRecallService(self._boundary)
        self._task_lock = threading.RLock()
        self._model_task_id = ""
        self._model_task_state = "idle"
        self._model_task_stage = ""
        self._model_task_progress: int | None = None
        self._model_task_error_code = ""
        self._model_task_thread: threading.Thread | None = None
        self._merge_result = ""
        self._rebuild_collection: str | None = None
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
                "metadata": dict(fragment.metadata),
            }
            for fragment in recalled.fragments
        ]

    def search_tool(self, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._boundary.search_memory(dict(arguments), wait=False)

    def transfer_collection(self, request: Mapping[str, object]) -> dict:
        return self._boundary.transfer(request)

    def text_import_descriptor(self) -> dict:
        return {
            "sectionId": MEMORY_TEXT_IMPORT_SECTION_ID, "title": "从文本提取记忆", "order": 5,
            "presentation": {"component": "module", "standalone": True,
                             "source": Path(__file__).with_name("frontend").joinpath("text-import.js").read_text(encoding="utf-8")},
            "fields": [
                {"key": "request", "label": "提取请求", "type": "data", "default": {}},
                {"key": "result", "label": "提取结果", "type": "data", "default": {}, "readonly": True},
            ],
            "actions": [{"actionId": key, "label": label} for key, label in
                        (("extract", "提取候选"), ("status", "提取进度"), ("save", "保存所选记忆"), ("cancel", "停止提取"))],
        }

    def text_import_action(self, action: str, values: Mapping[str, object]) -> dict:
        handler = {"extract": self._boundary.start_text_import, "status": self._boundary.text_import_status,
                   "save": self._boundary.save_text_import, "cancel": self._boundary.cancel_text_import}[action]
        return {"values": {"result": handler(values["request"])}}

    def _tool_evidence(self, invocation: Mapping[str, object] | None) -> dict:
        if not invocation or invocation.get("characterId") != self._character_id:
            raise ValueError("当前没有可引用的用户消息，请在用户明确要求时保存记忆。")
        entry = invocation["humanEntry"]
        return {"source": "user_tool", "source_turn_id": entry["turnId"],
                "created_in_turn_id": entry["turnId"], "source_entry_ids": [entry["entryId"]],
                "occurred_at": entry["createdAt"], "evidence_kind": "human",
                "evidence": [{"entry_id": entry["entryId"], "turn_id": entry["turnId"],
                              "role": "user", "quote": entry["payload"]["text"], "created_at": entry["createdAt"]}]}

    def remember_tool(self, arguments: Mapping[str, object], invocation=None) -> dict[str, object]:
        return self._boundary.upsert({**dict(arguments), **self._tool_evidence(invocation)})

    def update_tool(self, arguments: Mapping[str, object], invocation=None) -> dict[str, object]:
        values = {key: value for key, value in arguments.items() if key != "memory_id"}
        values.update({"id": arguments.get("memory_id"), **self._tool_evidence(invocation)})
        return self._boundary.upsert(values)

    def forget_tool(self, arguments: Mapping[str, object], invocation=None) -> dict[str, object]:
        evidence = self._tool_evidence(invocation)
        return self._boundary.delete({"id": arguments.get("memory_id"), "source": evidence["source"],
                                      "evidence": evidence["evidence"]})

    def _combined_settings_descriptor(self) -> dict[str, object]:
        return {
            "sectionId": MEMORY_SETTINGS_SECTION_ID,
            "title": "长期记忆",
            "order": 40,
            "fields": [
                {
                    "key": "status",
                    "label": "运行状态",
                    "type": "status",
                    "placement": "section_header",
                    "default": {
                        "state": "neutral",
                        "label": "状态未知",
                        "message": "",
                    },
                },
                {
                    "key": "triggerTurns",
                    "label": "自动整理间隔（轮）",
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "maximum": 50,
                    "step": 1,
                },
                {
                    "key": "embeddingResource",
                    "label": "本地向量模型",
                    "type": "resource",
                    "actionIds": [
                        "downloadEmbedding",
                        "retryEmbedding",
                        "cancelEmbedding",
                    ],
                    "default": {
                        "applicability": "required",
                        "subtitle": "",
                        "ready": False,
                        "taskState": "idle",
                        "message": "",
                        "detail": "",
                        "progress": None,
                        "availableActionIds": [],
                    },
                },
            ],
            "actions": [
                {
                    "actionId": "downloadEmbedding",
                    "label": "下载本地模型",
                },
                {
                    "actionId": "retryEmbedding",
                    "label": "重试",
                },
                {
                    "actionId": "cancelEmbedding",
                    "label": "取消下载",
                },
            ],
            "collections": [
                {
                    "collectionId": MEMORY_COLLECTION_ID,
                    "scope": "character",
                    "title": "全部记忆",
                    "description": "",
                    "columns": [
                        {
                            "key": "content",
                            "label": "内容",
                            "type": "string",
                            "maxLength": 16_384,
                        },
                        {"key": "kindLabel", "label": "分类", "type": "string"},
                        {"key": "pinLabel", "label": "固定", "type": "string"},
                        {"key": "updatedAt", "label": "更新于", "type": "datetime"},
                        *[{"key": key, "label": label, "type": "string"} for key, label in
                          (("subject", "关于"), ("predicate", "信息项"), ("target", "另一方"))],
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
                        {"key": "layer", "label": "记住什么", "type": "select", "default": "semantic",
                         "options": [
                             {"label": "关于我的长期信息", "value": "core_profile"},
                             {"label": "相处约定", "value": "procedural"},
                             {"label": "一般事实", "value": "semantic"},
                             {"label": "共同经历", "value": "episodic"},
                             {"label": "当前事项", "value": "session"},
                         ]},
                        {"key": "pinned", "label": "固定内容", "type": "toggle", "default": True,
                         "description": "后台整理不会改写或删除，仍按这条记忆的用途使用。"},
                        {"key": "state", "label": "使用状态", "type": "select", "default": "active",
                         "options": [{"label": label, "value": value} for value, label in
                                     (("active", "继续使用"), ("completed", "事项已完成"), ("cancelled", "不再适用"))]},
                        {"key": "evidenceText", "label": "原文依据", "type": "readonly", "default": ""},
                        {"key": "occurredAt", "label": "记录时间", "type": "readonly", "default": ""},
                        {"key": "subject", "label": "关于谁或什么", "type": "text", "default": "用户", "required": True},
                        {"key": "predicate", "label": "信息项", "type": "text", "default": "", "required": True,
                         "description": "例如称呼、职业、饮食偏好。"},
                        {"key": "target", "label": "另一方", "type": "text", "default": "",
                         "description": "记录两个对象之间的联系时填写。"},
                        *[{"key": key, "label": label, "type": "select", "default": "person",
                           "options": [{"label": title, "value": kind} for kind, title in zip(
                               ENTITY_KINDS, ("人物", "项目", "地点", "物品", "事件", "话题"))]}
                          for key, label in (("subject_kind", "对象类型"), ("target_kind", "另一方类型"))],
                    ],
                    "filters": [],
                    "searchable": True,
                    "pageSize": 25,
                    "deleteConfirmation": "确定删除这条长期记忆吗？此操作不能撤销。",
                },
            ],
        }

    def settings_descriptor(self) -> dict[str, object]:
        descriptor = self._combined_settings_descriptor()
        descriptor.pop("collections", None)
        descriptor["fields"] = [
            field for field in descriptor["fields"]
            if field["key"] != "embeddingResource"
        ]
        descriptor["actions"] = []
        return descriptor

    def component_descriptor(self) -> dict[str, object]:
        combined = self._combined_settings_descriptor()
        resource = next(
            field for field in combined["fields"]
            if field["key"] == "embeddingResource"
        )
        return {
            "sectionId": MEMORY_COMPONENT_SECTION_ID,
            "title": "中文长期记忆",
            "order": 40,
            "fields": [resource],
            "actions": combined["actions"],
        }

    def memory_management_descriptor(self) -> dict[str, object]:
        return {
            "sectionId": MEMORY_MANAGEMENT_SECTION_ID, "title": "更新最近的记忆", "order": 10,
            "presentation": {"component": "form", "inlineDescriptions": True},
            "fields": [
                {"key": "curationStatus", "label": "自动更新", "type": "status",
                 "default": {"state": "neutral", "label": "读取中", "message": ""},
                 "description": "自动更新会定期处理聊天，也可以在这里立即更新。"},
                {"key": "curationResult", "label": "最近结果", "type": "readonly", "default": "", "maxLength": 16384},
            ],
            "actions": [{"actionId": "updateNow", "label": "立即更新"}],
        }

    def memory_optimization_descriptor(self) -> dict:
        return {
            "sectionId": MEMORY_OPTIMIZATION_SECTION_ID, "title": "合并重复记忆", "order": 20,
            "presentation": {"component": "form", "inlineDescriptions": True},
            "fields": [
                {"key": "duplicates", "label": "重复内容", "type": "readonly", "default": "",
                 "description": "合并完全重复的自动记忆，保留手动编辑和固定的内容。"},
            ],
            "actions": [{"actionId": "mergeDuplicates", "label": "合并重复记忆"}],
        }

    def memory_index_descriptor(self) -> dict:
        return {
            "sectionId": MEMORY_INDEX_SECTION_ID, "title": "修复记忆检索", "order": 30,
            "presentation": {"component": "form", "inlineDescriptions": True},
            "fields": [
                {"key": "indexStatus", "label": "检索索引", "type": "status",
                 "default": {"state": "neutral", "label": "读取中", "message": ""},
                 "description": "重新建立检索数据，帮助角色找到已保存的记忆。"},
                {"key": "indexError", "label": "索引说明", "type": "readonly", "default": "", "maxLength": 16384},
            ],
            "actions": [{"actionId": "rebuildIndex", "label": "修复检索"}],
        }

    def load_management_settings(self) -> dict:
        curation = self._boundary.settings_get()["curation"]
        running = curation.get("running", False)
        result = curation.get("result", {})
        pending = curation.get("pendingTurns", 0)
        label = ("正在更新记忆…" if running else "更新没有完成。" if result.get("error")
                 else f"有 {pending} 轮新对话待更新。" if pending else "自动更新已开启")
        return {
            "curationStatus": {"state": "working" if running else "error" if result.get("error") else "ready" if pending else "neutral",
                               "label": label, "message": ""},
            "curationResult": result.get("error") or result.get("message") or (
                f"{'本次已处理' if running else '最近一次处理了'} {result['processed']} 条消息，新增 {result['created']} 条，更新 {result['updated']} 条记忆。"
                if "processed" in result else ""),
        }

    def load_optimization_settings(self) -> dict:
        groups = self._boundary.memory_store.duplicate_groups()
        return {
            "duplicates": f"发现 {len(groups)} 组重复内容，合并后可减少 {sum(len(items) - 1 for items in groups)} 条记忆。"
                          if groups else self._merge_result,
        }

    def load_index_settings(self) -> dict:
        index = self._boundary.memory_store.index_status()
        state = index.get("state", "missing")
        repaired = state == "ready" and self._rebuild_collection is not None and index["collection"] != self._rebuild_collection
        labels = {"loading": "正在准备检索…", "building": "正在修复检索…",
                  "indexing": "正在更新检索数据…", "ready": "检索已修复。" if repaired else "检索已就绪", "failed": "检索数据更新失败。", "stopped": "检索已停止。"}
        return {
            "indexStatus": {"state": "working" if state in {"loading", "building", "indexing"} else "error" if state == "failed" else "ready" if repaired else "neutral",
                            "label": labels.get(state, "待用"),
                            "message": f"已处理 {index.get('completed', 0)} / {index.get('total', 0)} 条。" if state in {"building", "indexing"} else ""},
            "indexError": index.get("error", ""),
        }

    def update_now(self, _values: Mapping[str, object]) -> dict:
        self._boundary.update_now(self._timeline)
        return {"values": self.load_management_settings()}

    def merge_duplicates(self, _values: Mapping[str, object]) -> dict:
        result = self._boundary.merge_duplicates()
        self._merge_result = f"已合并 {result['merged']} 条重复记忆。" if result["merged"] else "没有需要合并的重复记忆。"
        return {"values": self.load_optimization_settings()}

    def rebuild_index(self, _values: Mapping[str, object]) -> dict:
        previous = self._boundary.memory_store.index_status()["collection"]
        self._boundary.rebuild_index()
        self._rebuild_collection = previous
        return {"values": self.load_index_settings()}

    def backfill_descriptor(self) -> dict:
        return {
            "sectionId": MEMORY_BACKFILL_SECTION_ID,
            "title": "从旧聊天补充记忆",
            "order": 10,
            "presentation": {"component": "form", "collapsible": True, "inlineDescriptions": True},
            "fields": [
                {"key": "backfillStatus", "label": "整理进度", "type": "status",
                 "default": {"state": "neutral", "label": "尚未开始", "message": ""}},
                {"key": "startDate", "label": "从哪一天", "type": "text", "default": "",
                 "description": "日期格式为 YYYY-MM-DD。只整理当前角色的聊天，已处理的原文会跳过。"},
                {"key": "endDate", "label": "到哪一天", "type": "text", "default": "",
                 "description": "包含当天。取消后再次选择相同日期，可以继续整理。"},
            ],
            "actions": [
                {"actionId": "backfill", "label": "开始整理"},
                {"actionId": "cancelBackfill", "label": "停止整理"},
            ],
        }

    def load_backfill_settings(self) -> dict:
        snapshot = self._boundary.settings_get()
        backfill = snapshot.get("backfill", {})
        config = self._config_getter()
        state = backfill.get("status", "")
        labels = {"running": "正在整理", "completed": "整理完成", "cancelled": "已停止，可继续", "failed": "整理失败"}
        progress = f"已处理 {backfill.get('completed', 0)} / {backfill.get('total', 0)} 条消息" if state else "从已有聊天中补充记忆，并核对已有内容。"
        return {
            "backfillStatus": {"state": {"running": "working", "failed": "error", "completed": "ready"}.get(state, "neutral"),
                               "label": labels.get(state, "尚未开始"),
                               "message": "\n".join(filter(None, [progress, backfill.get("error", "")]))},
            "startDate": config.get("backfillStartDate", backfill.get("startDate", "")),
            "endDate": config.get("backfillEndDate", backfill.get("endDate", "")),
        }

    def save_backfill_settings(self, values: Mapping[str, object]) -> dict:
        self._config_updater({"backfillStartDate": values.get("startDate", ""),
                              "backfillEndDate": values.get("endDate", "")})
        return {"applicationState": "applied"}

    def start_backfill(self, values: Mapping[str, object]) -> dict:
        self._boundary.start_backfill(self._timeline, str(values.get("startDate", "")), str(values.get("endDate", "")))
        return {"values": self.load_backfill_settings()}

    def cancel_backfill(self, _values: Mapping[str, object]) -> dict:
        self._boundary.cancel_backfill()
        return {"values": self.load_backfill_settings()}

    def memory_collection_descriptor(self, collection_id: str = MEMORY_COLLECTION_ID) -> dict[str, object]:
        descriptor = dict(self._combined_settings_descriptor()["collections"][0])
        descriptor["collectionId"] = collection_id
        views = {
            "profile": ("个人画像", "身份、习惯和偏好。记错的地方可以直接纠正。", "core_profile", "用户", "person", "个人信息"),
            "knowledge": ("人与事", "聊到的人、项目和地点，以及它们之间的联系。", "semantic", "", "person", "相关信息"),
            "agreements": ("相处约定", "你提出的交流方式和边界，会用于之后的聊天。", "procedural", "用户", "person", "相处约定"),
            "experiences": ("共同经历", "一起经历的事情、讨论过的话题和还在继续的事项。", "episodic", "", "event", "经过"),
        }
        if collection_id in views:
            title, description, layer, subject, kind, predicate = views[collection_id]
            descriptor.update(title=title, description=description)
            defaults = {"layer": layer, "subject": subject, "subject_kind": kind, "predicate": predicate}
            for field in descriptor["fields"]:
                if field["key"] in defaults:
                    field["default"] = defaults[field["key"]]
                if field["key"] == "predicate" and collection_id == "knowledge":
                    field.update(label="信息项或联系", description="例如职业、进展，或与另一方的联系。")
                if field["key"] == "subject" and collection_id == "experiences":
                    field["label"] = "事件或话题"
        return descriptor

    def load_settings(self) -> dict[str, object]:
        snapshot = self._boundary.settings_get()
        curation = _mapping(snapshot.get("curation"))
        status = str(snapshot.get("status", "degraded"))
        message = str(snapshot.get("message", "")).strip()
        return {
            "status": _runtime_status_value(status, message),
            "triggerTurns": int(curation.get("triggerTurns", 8)),
        }

    def load_component_settings(self) -> dict[str, object]:
        embedding = _mapping(self._boundary.settings_get().get("embedding"))
        return {"embeddingResource": self._embedding_resource_value(embedding)}

    def save_settings(self, values: Mapping[str, object]) -> dict[str, str]:
        current = self.load_settings()
        self._config_updater(
            {
                "triggerTurns": values.get("triggerTurns", current["triggerTurns"]),
            }
        )
        return {"applicationState": "applied"}

    def load_model_slot(self) -> dict[str, str]:
        slot = _mapping(self._boundary.settings_get().get("curationModelSlot"))
        return {
            "serviceKey": str(slot.get("serviceKey", "")),
            "profileId": str(slot.get("profileId", "")),
            "modelId": str(slot.get("modelId", "")),
        }

    def save_model_slot(self, selection: Mapping[str, object]) -> dict[str, str]:
        parsed = _parse_model_slot_selection({**self.load_model_slot(), **selection})
        self._config_updater(
            {
                "curationModelRef": parsed,
            }
        )
        return {"applicationState": "applied"}

    def start_model_download(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._task_lock:
            if self._closed:
                raise RuntimeError("MEMORY_STOPPED")
            if self._model_task_thread is not None and self._model_task_thread.is_alive():
                return {"values": self.load_component_settings(), "message": "模型下载已在进行中。"}
            task_id = f"memory-model-{uuid.uuid4().hex}"
            self._model_task_id = task_id
            self._model_task_state = "queued"
            self._model_task_stage = "等待下载"
            self._model_task_progress = None
            self._model_task_error_code = ""
            self._boundary.begin_model_download(task_id)

            def run() -> None:
                try:
                    with self._task_lock:
                        if self._model_task_id == task_id:
                            self._model_task_state = "running"
                    state = self._boundary.run_model_download(
                        task_id,
                        progress=self._record_model_progress,
                    )
                except Exception:
                    state = "failed"
                with self._task_lock:
                    if self._model_task_id == task_id:
                        self._model_task_state = (
                            "succeeded" if state == "completed" else state
                        )
                        if state == "completed":
                            self._model_task_stage = "安装完成"
                            self._model_task_progress = 100
                            self._model_task_error_code = ""
                        elif state == "failed":
                            reader = getattr(
                                self._boundary,
                                "model_download_error_code",
                                None,
                            )
                            code = reader() if callable(reader) else ""
                            self._model_task_error_code = (
                                str(code) if code else "DOWNLOAD_FAILED"
                            )

            thread = threading.Thread(
                target=run,
                name="sakura-mem0-model-download",
                daemon=True,
            )
            self._model_task_thread = thread
            thread.start()
        return {"values": self.load_component_settings(), "message": "已开始下载模型。"}

    def cancel_model_download(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._task_lock:
            task_id = self._model_task_id
        result = self._boundary.model_cancel({"taskHandle": task_id}) if task_id else {"accepted": False}
        return {
            "values": self.load_component_settings(),
            "message": "已请求取消模型下载。" if result.get("accepted") else "当前没有可取消的下载任务。",
        }

    def query_collection(self, request: Mapping[str, object], collection_id: str = MEMORY_COLLECTION_ID) -> dict[str, object]:
        if self._boundary.status()["status"] != "ready":
            return {"items": [], "nextCursor": None, "total": 0}
        records = self._projected_records()
        if collection_id == "profile":
            records = [item for item in records if item["layer"] in {"core_profile", "semantic"}
                       and not item.get("target") and item.get("subject") == "用户"]
        elif collection_id == "knowledge":
            records = [item for item in records if item["layer"] in {"core_profile", "semantic"}
                       and item.get("subject") != "未分类"
                       and (item.get("target") or item.get("subject") != "用户")]
        elif collection_id == "agreements":
            records = [item for item in records if item["layer"] == "procedural"]
        elif collection_id == "experiences":
            records = [item for item in records if item["layer"] in {"episodic", "session"}
                       or item.get("subject") == "未分类"]
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
                    for key in ("content", "category", "source", "subject", "predicate", "target")
                ).casefold()
            ]
        if layer:
            records = [item for item in records if item.get("layer") == layer]
        if "pinned" in filters:
            records = [item for item in records if bool(item.get("pinned")) == filters["pinned"]]
        records.sort(key=lambda item: (str(item.get("occurredAt" if collection_id == "experiences" else "updatedAt", "")), item["id"]), reverse=True)
        if collection_id == "profile":
            records.sort(key=lambda item: (item.get("subject") != "用户", item.get("subject", ""), item.get("predicate", "")))
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
        writable = {key: value for key, value in values.items() if key not in {"evidenceText", "occurredAt", "source"}}
        result = self._boundary.upsert({**writable, "source": "manual", "pinned": values.get("pinned", True)})
        return _collection_item(_mapping(result.get("memory")))

    def update_collection_item(
        self,
        item_id: str,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        writable = {key: value for key, value in values.items() if key not in {"evidenceText", "occurredAt", "source"}}
        result = self._boundary.upsert({"id": item_id, **writable, "source": "manual"})
        return _collection_item(_mapping(result.get("memory")))

    def delete_collection_item(self, item_id: str) -> dict[str, bool]:
        result = self._boundary.delete({"id": item_id})
        return {"deleted": not bool(result.get("alreadyMissing"))}

    def note_completed_chat(self, payload: object) -> None:
        if (
            not isinstance(payload, Mapping)
            or payload.get("characterId") != self._character_id
            or not isinstance(payload.get("turnId"), str)
            or not payload.get("turnId")
            or not isinstance(payload.get("cursor"), str)
            or not payload.get("cursor")
        ):
            return
        self.catch_up_timeline()

    def catch_up_timeline(self) -> None:
        if self._timeline is None:
            return
        try:
            self._boundary.note_timeline_changed(self._timeline)
        except Exception:
            log_event("memory", "记忆时间线同步失败", event="memory.timeline.failed", severity="error")

    def close(self) -> None:
        with self._task_lock:
            if self._closed:
                return
            self._closed = True
            task_id = self._model_task_id
            thread = self._model_task_thread
        if task_id:
            try:
                self._boundary.model_cancel({"taskHandle": task_id})
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._boundary.close()

    def _projected_records(self) -> list[dict[str, object]]:
        return [item for raw in self._boundary.list_memories(limit=None)
                if (item := _project_memory(raw, self._character_id)) is not None]

    def _record_model_progress(self, stage: str, progress: int) -> None:
        with self._task_lock:
            self._model_task_state = "running"
            self._model_task_stage = _model_stage_label(stage)
            self._model_task_progress = max(0, min(100, int(progress)))

    def _embedding_resource_value(
        self,
        embedding: Mapping[str, object],
    ) -> dict[str, object]:
        installed = embedding.get("installed") is True
        with self._task_lock:
            state = self._model_task_state
            stage = self._model_task_stage
            progress = self._model_task_progress
            error_code = self._model_task_error_code
        if state in {"queued", "running"}:
            actions: list[str] = ["cancelEmbedding"]
            message = "正在下载模型"
        elif state in {"failed", "cancelled"}:
            actions = ["retryEmbedding"]
            if state == "cancelled":
                message = (
                    "已取消，原模型仍可用。"
                    if installed
                    else "已取消"
                )
            else:
                message = (
                    "下载失败，原模型仍可用。"
                    if installed
                    else "下载失败"
                )
        elif installed:
            actions = []
            message = "已安装"
        else:
            actions = ["downloadEmbedding"]
            message = "尚未安装"
        return {
            "applicability": "required",
            "subtitle": str(embedding.get("model", ""))[:512],
            "ready": installed,
            "taskState": state if state in {
                "idle", "queued", "running", "succeeded", "failed", "cancelled"
            } else "idle",
            "message": message,
            "detail": (
                stage[:240]
                if state in {"queued", "running"}
                else _model_download_error_detail(error_code)
                if state == "failed"
                else ""
            ),
            "progress": progress if state in {"queued", "running"} else None,
            "availableActionIds": actions,
        }


def _model_download_error_detail(code: str) -> str:
    messages = {
        "DOWNLOAD_NETWORK_FAILED": "无法连接模型下载服务，请检查网络或代理后重试。",
        "DOWNLOAD_DEPENDENCY_MISSING": "下载组件依赖缺失，请重新安装或修复 Sakura Runtime。",
        "DOWNLOAD_INCOMPLETE": "下载内容不完整，请重试。",
        "DOWNLOAD_SIZE_MISMATCH": "模型文件大小不匹配，请重试。",
        "INSTALL_TARGET_BUSY": "模型目录正被占用或不可写，请关闭相关程序后重试。",
        "DOWNLOAD_FAILED": "下载过程发生内部错误，请重试。",
    }
    safe_code = code if code in messages else "DOWNLOAD_FAILED"
    return f"{messages[safe_code]}（{safe_code}）"


class SakuraMem0Plugin:
    def __init__(
        self,
        runtime_factory: Callable[[object], SakuraMem0Runtime | None] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory or _default_runtime
        self._scope = ExitStack()
        self._lock = threading.RLock()
        self._runtime: SakuraMem0Runtime | None = None
        self._closed = False

    def setup(self, context: object) -> None:
        try:
            bind_logger(getattr(context, "get")("sakura.host.logging"))
            getattr(context, "effect")(lambda: bind_logger(None))
        except Exception:
            bind_logger(None)
        getattr(context, "effect")(self.close)
        getattr(context, "on")("sakura.host.character.changed", lambda event: self._bind_character(context, event))
        getattr(context, "on")(HOST_CHAT_COMPLETED_EVENT, self._note_completed_chat)
        self._bind_character(context)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._scope.close()
            self._runtime = None

    def _note_completed_chat(self, event: Mapping[str, object]) -> None:
        with self._lock:
            if self._runtime is not None:
                self._runtime.note_completed_chat(event)

    def _bind_character(self, context: object, event: Mapping[str, object] | None = None) -> None:
        with self._lock:
            if self._closed or (event is not None and self._runtime is not None
                               and event["characterId"] == self._runtime.character_id):
                return
            self._scope.close()
            self._runtime = None
            with ExitStack() as scope:
                runtime = self._runtime_factory(context)
                if runtime is None:
                    scope.callback(getattr(context, "get")("sakura.host.settings").register({
                        "sectionId": MEMORY_SETTINGS_SECTION_ID, "title": "长期记忆", "order": 40,
                        "fields": [{"key": "status", "label": "运行状态", "type": "status",
                                    "default": {"state": "neutral", "label": "等待选择角色",
                                                "message": "请先导入并选择角色。"}}],
                    }))
                else:
                    scope.callback(runtime.close)
                    self._register(context, runtime, scope)
                self._runtime = runtime
                self._scope = scope.pop_all()

    def _register(self, context: object, runtime: SakuraMem0Runtime, scope: ExitStack) -> None:
        scope.callback(getattr(context, "get")("sakura.host.context").register(
            {
                "providerId": MEMORY_CONTEXT_PROVIDER_ID,
                "description": "从当前角色的本地长期记忆中选择与本轮相关的少量事实。",
                "order": 60,
                "scope": "turn",
            },
            runtime.context,
        ))
        tools = getattr(context, "get")("sakura.host.tools")
        for descriptor, callback in _tool_registrations(runtime):
            if descriptor["name"] != "memory_search":
                descriptor["invocationContext"] = True
            scope.callback(tools.register(descriptor, callback))
        settings = getattr(context, "get")("sakura.host.settings")
        scope.callback(settings.register(
            runtime.settings_descriptor(),
            load=runtime.load_settings,
            save=runtime.save_settings,
        ))
        scope.callback(settings.register(
            runtime.component_descriptor(),
            load=runtime.load_component_settings,
            actions={
                "downloadEmbedding": runtime.start_model_download,
                "retryEmbedding": runtime.start_model_download,
                "cancelEmbedding": runtime.cancel_model_download,
            },
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_COMPONENT_SECTION_ID,
            "about",
        ))
        scope.callback(settings.register(
            runtime.memory_management_descriptor(), load=runtime.load_management_settings,
            actions={"updateNow": runtime.update_now},
        ))
        scope.callback(settings.register(
            runtime.text_import_descriptor(),
            # 请求是临时输入；提取和保存由对应操作负责，不写入插件配置。
            save=lambda _values: {"applicationState": "applied"},
            actions={key: (lambda values, action=key: runtime.text_import_action(action, values))
                     for key in ("extract", "status", "save", "cancel")},
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_TEXT_IMPORT_SECTION_ID, "memory",
        ))
        scope.callback(settings.register(
            runtime.memory_optimization_descriptor(), load=runtime.load_optimization_settings,
            actions={"mergeDuplicates": runtime.merge_duplicates},
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_OPTIMIZATION_SECTION_ID, "memory",
        ))
        scope.callback(settings.register(
            runtime.memory_index_descriptor(), load=runtime.load_index_settings,
            actions={"rebuildIndex": runtime.rebuild_index},
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_INDEX_SECTION_ID, "memory",
        ))
        scope.callback(settings.register(
            runtime.backfill_descriptor(), load=runtime.load_backfill_settings,
            save=runtime.save_backfill_settings,
            actions={
                "backfill": runtime.start_backfill,
                "cancelBackfill": runtime.cancel_backfill,
            },
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_BACKFILL_SECTION_ID, "memory",
        ))
        scope.callback(getattr(context, "get")("sakura.host.settings.surface-v0").register(
            MEMORY_MANAGEMENT_SECTION_ID,
            "memory",
        ))
        for collection_id in ("profile", "agreements", "experiences", "knowledge", MEMORY_COLLECTION_ID):
            scope.callback(getattr(context, "get")("sakura.host.settings.collection-v0").register(
                MEMORY_MANAGEMENT_SECTION_ID,
                runtime.memory_collection_descriptor(collection_id),
                query=lambda request, key=collection_id: runtime.query_collection(request, key),
                create=runtime.create_collection_item,
                update=runtime.update_collection_item,
                delete=runtime.delete_collection_item,
                **({"transfer": runtime.transfer_collection} if collection_id == MEMORY_COLLECTION_ID else {}),
            ))
        scope.callback(getattr(context, "get")("sakura.host.model_slots.v2").register(
            {
                "slotId": "curation",
                "label": "记忆整理模型",
                "description": "继承时使用对话模型。",
                "modelKind": "chat_completion",
                "required": False,
                "order": 30,
            },
            load=runtime.load_model_slot,
            save=runtime.save_model_slot,
        ))
        # Backlog catch-up may scan and curate a large Timeline. Plugin setup is serial, so it
        # must not hold up unrelated plugins such as the TTS Hub and its providers.
        threading.Thread(
            target=runtime.catch_up_timeline,
            name="sakura-mem0-initial-catch-up",
            daemon=True,
        ).start()


def _default_runtime(context: object) -> SakuraMem0Runtime | None:
    from sakura_model_client import ModelClient
    plugin_data_root = Path(getattr(context, "data_path")("."))
    storage = getattr(context, "get")("sakura.host.storage")
    try:
        character = getattr(context, "get")("sakura.host.character").current()
    except Exception as error:
        if getattr(error, "code", None) == "CHARACTER_NOT_FOUND":
            return None
        raise
    model_slots = getattr(context, "get")("sakura.host.model_slots.v2")
    character_id = str(character.get("id", ""))
    system_prompt = str(character.get("systemPrompt", ""))
    if not character_id or not system_prompt:
        raise RuntimeError("MEMORY_CHARACTER_UNAVAILABLE")
    plugin_config = getattr(context, "config")
    config_getter = getattr(plugin_config, "get")
    config_updater = getattr(plugin_config, "update")
    return SakuraMem0Runtime(
        plugin_data_root,
        character_id,
        system_prompt=system_prompt,
        timeline=getattr(context, "get")("sakura.host.timeline"),
        config_getter=config_getter,
        config_updater=config_updater,
        memory_dir=Path(storage.resolve("data", "memory")),
        memory_cache_dir=Path(storage.resolve("cache", "memory")),
        model_catalog_getter=model_slots.catalog,
        model_resolver=model_slots.resolve,
        model_client_factory=lambda ref: ModelClient(context, ref),
    )


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
                        **{key: {"type": "string", "description": description} for key, description in (("subject", "准确的实体名称，如用户或项目名称"), ("predicate", "要查询的信息项"), ("target", "关系另一方的准确名称"))},
                        "limit": {"type": "integer"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                    },
                    "required": [],
                },
                "group": "plugin",
                "risk": "low",
            },
            runtime.search_tool,
        ),
        (
            {
                "name": "memory_remember",
                "description": (
                    "保存一条当前角色的长期记忆。只在用户明确要求记住，或信息明显会长期帮助陪伴/协作时使用；"
                    "不得保存密码、token、密钥、证件号、银行卡等敏感凭据或身份秘密。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                        "category": {"type": "string"},
                        **{key: {"type": "string"} for key in KNOWLEDGE_FIELDS},
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
                "description": (
                    "更新一条当前角色的长期记忆。应先搜索并取得准确的 memory_id；"
                    "只在用户明确纠正、补充、合并旧记忆，或已有记忆明显过时时使用；"
                    "不得写入密码、token、密钥、证件号、银行卡等敏感凭据或身份秘密。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "layer": {"type": "string", "enum": list(MEMORY_LAYERS)},
                        "category": {"type": "string"},
                        **{key: {"type": "string"} for key in KNOWLEDGE_FIELDS},
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
                "updatedAt",
                "occurredAt",
                "state",
                "pinned",
                *KNOWLEDGE_FIELDS,
            )
        } | {"target_kind": memory.get("target_kind") or "person",
             "kindLabel": {"semantic": "一般事实", "episodic": "共同经历", "core_profile": "用户画像",
                          "procedural": "相处约定", "session": "当前事项"}.get(memory.get("layer"), "一般事实"),
             "pinLabel": "已固定" if memory.get("pinned") else "",
             "evidenceText": "\n".join(
            f"{source.get('created_at', '')} · { {'user': '用户', 'assistant': '助手', 'observation': '观察', 'document': '导入文本'}.get(source.get('role'), '原文') }\n{source.get('quote', '')}"
            for source in memory.get("evidence", [])
        )},
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
        for item in messages[-8:]:
            message = _mapping(item)
            role = str(message.get("role", ""))
            content = message.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                recent.append(ContextMessage(role, content[:2000]))
    return ContextRequest(
        current_input=str(raw.get("current_input", ""))[:4096],
        character_id=str(raw.get("character_id", ""))[:128],
        character_name=str(raw.get("character_name", ""))[:120],
        current_turn_id=str(raw.get("current_turn_id", ""))[:128],
        source_entry_ids=tuple(
            str(item)[:128]
            for item in (
                raw.get("source_entry_ids", [])
                if isinstance(raw.get("source_entry_ids"), (list, tuple))
                else []
            )[:16]
        ),
        human_entry_id=str(raw.get("human_entry_id", ""))[:128],
        observation_entry_ids=tuple(
            str(item)[:128]
            for item in (
                raw.get("observation_entry_ids", [])
                if isinstance(raw.get("observation_entry_ids"), (list, tuple))
                else []
            )[:16]
        ),
        source=(
            raw.get("source")
            if raw.get("source") in {"chat", "event"}
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


def _parse_model_slot_selection(value: object) -> dict[str, str]:
    try:
        from .boundary import _resolved_model
    except ImportError:
        from boundary import _resolved_model
    return _resolved_model(value)


def _runtime_status_value(status: str, message: str) -> dict[str, str]:
    state = {
        "ready": "ready",
        "loading": "working",
        "degraded": "warning",
        "read_only": "warning",
        "failed": "error",
        "stopped": "error",
    }.get(status, "neutral")
    label = {
        "ready": "运行正常",
        "loading": "正在初始化",
        "degraded": "功能受限",
        "read_only": "只读运行",
        "failed": "运行失败",
        "stopped": "已停止",
    }.get(status, "状态未知")
    return {
        "state": state,
        "label": label,
        "message": message[:240] if state not in {"ready", "neutral"} else "",
    }


def _model_stage_label(stage: str) -> str:
    return {
        "connecting": "连接下载源",
        "downloading": "下载模型文件",
        "installing": "安装并校验",
        "completed": "安装完成",
    }.get(stage, "处理模型文件")


__all__ = ["SakuraMem0Plugin", "SakuraMem0Runtime"]
