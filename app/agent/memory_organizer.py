from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from app.core.debug_log import debug_log
from app.llm.api_client import STRUCTURED_JSON_RESPONSE_FORMAT


MAX_MEMORY_ORGANIZATION_CHUNK_ITEMS = 24
MAX_MEMORY_ORGANIZATION_CHUNK_CHARS = 6000
MAX_MEMORY_ORGANIZATION_OUTPUT_TOKENS = 3000
MIN_MEMORY_ORGANIZATION_SPLIT_ITEMS = 2
MAX_MEMORY_ORGANIZATION_SPLIT_DEPTH = 2
MIN_MEMORY_ORGANIZATION_REPAIR_RAW_CHARS = 80
MAX_MEMORY_ORGANIZATION_REPAIR_RAW_CHARS = 2000

MemoryOrganizationActionKind = Literal["update", "delete", "keep"]

_ACTION_GROUP_KEYS: dict[MemoryOrganizationActionKind, tuple[str, ...]] = {
    "update": (
        "update",
        "updates",
        "updated",
        "to_update",
        "update_actions",
        "update_suggestions",
        "rewrite",
        "rewrites",
        "rewritten",
        "修改",
        "更新",
        "改写",
        "建议更新",
        "建议修改",
    ),
    "delete": (
        "delete",
        "deletes",
        "deleted",
        "to_delete",
        "delete_actions",
        "delete_suggestions",
        "remove",
        "removes",
        "removed",
        "删除",
        "移除",
        "建议删除",
        "建议移除",
    ),
    "keep": (
        "keep",
        "keeps",
        "kept",
        "to_keep",
        "keep_actions",
        "keep_suggestions",
        "unchanged",
        "保留",
        "保持",
        "建议保留",
    ),
}

_ACTION_VALUE_ALIASES: dict[str, MemoryOrganizationActionKind] = {
    "update": "update",
    "updated": "update",
    "rewrite": "update",
    "modify": "update",
    "修改": "update",
    "更新": "update",
    "改写": "update",
    "delete": "delete",
    "deleted": "delete",
    "remove": "delete",
    "removed": "delete",
    "删除": "delete",
    "移除": "delete",
    "keep": "keep",
    "kept": "keep",
    "unchanged": "keep",
    "保留": "keep",
    "保持": "keep",
}

_DIRECT_ACTION_LIST_KEYS = (
    "actions",
    "plan",
    "suggestions",
    "items",
    "results",
    "changes",
    "operations",
    "recommendations",
    "建议",
    "整理建议",
)

_ID_KEYS = (
    "id",
    "memory_id",
    "memoryId",
    "target_id",
    "targetId",
    "source_id",
    "sourceId",
    "original_id",
    "originalId",
    "record_id",
    "recordId",
    "记忆id",
    "记忆ID",
    "原始ID",
    "目标ID",
)

_MULTI_ID_KEYS = (
    "ids",
    "memory_ids",
    "memoryIds",
    "target_ids",
    "targetIds",
    "delete_ids",
    "deleteIds",
    "source_ids",
    "sourceIds",
)


@dataclass(frozen=True)
class MemoryOrganizationAction:
    """一条长期记忆整理建议。"""

    action: MemoryOrganizationActionKind
    memory_id: str
    content: str
    reason: str = ""
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryOrganizationPlan:
    """长期记忆整理预览计划，确认后才会写入 MemoryStore。"""

    actions: tuple[MemoryOrganizationAction, ...] = ()
    source_count: int = 0
    raw_text: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def updates(self) -> tuple[MemoryOrganizationAction, ...]:
        return tuple(action for action in self.actions if action.action == "update")

    @property
    def deletes(self) -> tuple[MemoryOrganizationAction, ...]:
        return tuple(action for action in self.actions if action.action == "delete")

    @property
    def keeps(self) -> tuple[MemoryOrganizationAction, ...]:
        return tuple(action for action in self.actions if action.action == "keep")

    def has_changes(self) -> bool:
        return bool(self.updates or self.deletes)


@dataclass(frozen=True)
class MemoryOrganizationResult:
    """长期记忆整理应用结果。"""

    updated: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ChunkOrganizationResult:
    plan: MemoryOrganizationPlan | None = None
    raw_texts: tuple[str, ...] = ()
    error: str = ""


class MemoryOrganizer:
    """使用主模型分析现有长期记忆，生成去重和冲突整理建议。"""

    def __init__(self, api_client: Any) -> None:
        self.api_client = api_client

    def organize_memories(self, memories: list[dict[str, object]]) -> MemoryOrganizationPlan:
        normalized_memories = _normalize_source_memories(memories)
        if not normalized_memories:
            return MemoryOrganizationPlan(source_count=0)

        chunks = _chunk_memories_for_organization(
            sorted(normalized_memories, key=_memory_sort_key)
        )
        actions: list[MemoryOrganizationAction] = []
        raw_parts: list[str] = []
        warnings: list[str] = []
        handled_ids: set[str] = set()
        for index, chunk in enumerate(chunks, start=1):
            plan = self._organize_chunk(
                chunk,
                chunk_label=f"{index}/{len(chunks)}",
                chunk_index=index,
                chunk_count=len(chunks),
                split_depth=0,
            )
            if plan.raw_text:
                raw_parts.append(plan.raw_text)
            warnings.extend(plan.warnings)
            for action in plan.actions:
                if action.memory_id in handled_ids:
                    continue
                handled_ids.add(action.memory_id)
                actions.append(action)

        source_by_id = dict(_source_content_pairs(normalized_memories))
        for memory_id, content in source_by_id.items():
            if memory_id in handled_ids:
                continue
            actions.append(
                MemoryOrganizationAction(
                    action="keep",
                    memory_id=memory_id,
                    content=content,
                    reason="未进入有效整理结果，默认保留。",
                )
            )
        return MemoryOrganizationPlan(
            actions=tuple(actions),
            source_count=len(normalized_memories),
            raw_text="\n\n".join(raw_parts),
            warnings=tuple(warnings),
        )

    def _organize_chunk(
        self,
        chunk: list[dict[str, object]],
        *,
        chunk_label: str,
        chunk_index: int,
        chunk_count: int,
        split_depth: int,
    ) -> MemoryOrganizationPlan:
        result = self._request_chunk_plan(
            chunk,
            chunk_label=chunk_label,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )
        raw_texts = list(result.raw_texts)
        if result.plan is not None:
            return MemoryOrganizationPlan(
                actions=result.plan.actions,
                source_count=result.plan.source_count,
                raw_text="\n\n".join(raw_texts) or result.plan.raw_text,
                warnings=result.plan.warnings,
            )

        if (
            len(chunk) >= MIN_MEMORY_ORGANIZATION_SPLIT_ITEMS
            and split_depth < MAX_MEMORY_ORGANIZATION_SPLIT_DEPTH
        ):
            middle = len(chunk) // 2
            debug_log(
                "Memory",
                "长期记忆整理分块继续拆分",
                {
                    "chunk_label": chunk_label,
                    "memory_count": len(chunk),
                    "left_count": len(chunk[:middle]),
                    "right_count": len(chunk[middle:]),
                    "split_depth": split_depth,
                    "error": result.error,
                },
            )
            left = self._organize_chunk(
                chunk[:middle],
                chunk_label=f"{chunk_label}.1",
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                split_depth=split_depth + 1,
            )
            right = self._organize_chunk(
                chunk[middle:],
                chunk_label=f"{chunk_label}.2",
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                split_depth=split_depth + 1,
            )
            return MemoryOrganizationPlan(
                actions=(*left.actions, *right.actions),
                source_count=len(chunk),
                raw_text="\n\n".join((*raw_texts, left.raw_text, right.raw_text)),
                warnings=(*left.warnings, *right.warnings),
            )

        warning = f"第 {chunk_label} 块返回格式无效，已默认保留该块记忆。"
        debug_log(
            "Memory",
            "长期记忆整理分块解析失败，已默认保留",
            {
                "chunk_label": chunk_label,
                "memory_count": len(chunk),
                "raw_chars": sum(len(raw) for raw in raw_texts),
                "error": result.error,
            },
        )
        plan = _keep_plan_for_chunk(chunk, reason=warning)
        return MemoryOrganizationPlan(
            actions=plan.actions,
            source_count=plan.source_count,
            raw_text="\n\n".join(raw_texts),
            warnings=(warning,),
        )

    def _request_chunk_plan(
        self,
        chunk: list[dict[str, object]],
        *,
        chunk_label: str,
        chunk_index: int,
        chunk_count: int,
    ) -> _ChunkOrganizationResult:
        raw_texts: list[str] = []
        last_error = ""
        for mode in ("structured", "plain"):
            chat_params: dict[str, object] = {
                "max_tokens": MAX_MEMORY_ORGANIZATION_OUTPUT_TOKENS,
            }
            if mode == "structured":
                chat_params["response_format"] = STRUCTURED_JSON_RESPONSE_FORMAT
            raw = self.api_client.complete_raw(
                _MEMORY_ORGANIZATION_SYSTEM_PROMPT,
                [
                    {
                        "role": "user",
                        "content": _memory_organization_user_prompt(
                            chunk,
                            chunk_index=chunk_index,
                            chunk_count=chunk_count,
                            plain_retry=mode == "plain",
                        ),
                    }
                ],
                temperature=0.1,
                **chat_params,
            )
            raw_texts.append(raw)
            try:
                plan = parse_memory_organization_plan(raw, source_memories=chunk)
            except ValueError as exc:
                if _looks_like_no_change_response(raw):
                    plan = _keep_plan_for_chunk(
                        chunk,
                        reason="模型判断无需修改，默认保留。",
                    )
                    debug_log(
                        "Memory",
                        "长期记忆整理返回自然语言无变更结果，已默认保留",
                        {
                            "chunk_label": chunk_label,
                            "memory_count": len(chunk),
                            "mode": mode,
                            "raw_chars": len(raw),
                        },
                    )
                    return _ChunkOrganizationResult(
                        plan=MemoryOrganizationPlan(
                            actions=plan.actions,
                            source_count=plan.source_count,
                            raw_text=raw,
                        ),
                        raw_texts=tuple(raw_texts),
                    )
                last_error = str(exc)
                debug_log(
                    "Memory",
                    "长期记忆整理分块解析失败，准备兜底",
                    {
                        "chunk_label": chunk_label,
                        "memory_count": len(chunk),
                        "mode": mode,
                        "raw_chars": len(raw),
                        "error": last_error,
                    },
                )
                continue

            debug_log(
                "Memory",
                "长期记忆整理分块解析完成",
                {
                    "chunk_label": chunk_label,
                    "memory_count": len(chunk),
                    "mode": mode,
                    "updates": len(plan.updates),
                    "deletes": len(plan.deletes),
                    "keeps": len(plan.keeps),
                    "raw_chars": len(raw),
                },
            )
            return _ChunkOrganizationResult(plan=plan, raw_texts=tuple(raw_texts))
        return _ChunkOrganizationResult(raw_texts=tuple(raw_texts), error=last_error)


def parse_memory_organization_plan(
    raw: str,
    *,
    source_memories: list[dict[str, object]] | None = None,
) -> MemoryOrganizationPlan:
    """解析模型返回的长期记忆整理建议，并过滤不安全动作。"""

    source_by_id = {
        memory_id: content
        for memory_id, content in _source_content_pairs(source_memories or [])
    }
    data = _load_json_object(raw)
    raw_actions = _extract_raw_actions(data)
    if raw_actions is None:
        raise ValueError("长期记忆整理结果缺少 actions 列表。")

    actions: list[MemoryOrganizationAction] = []
    handled_ids: set[str] = set()
    for item in raw_actions:
        action = _normalize_action(item, source_by_id)
        if action is None:
            continue
        if source_by_id and action.memory_id not in source_by_id:
            continue
        if action.memory_id in handled_ids:
            continue
        handled_ids.add(action.memory_id)
        actions.append(action)

    for memory_id, content in source_by_id.items():
        if memory_id in handled_ids:
            continue
        actions.append(
            MemoryOrganizationAction(
                action="keep",
                memory_id=memory_id,
                content=content,
                reason="模型未建议修改，默认保留。",
            )
        )

    return MemoryOrganizationPlan(
        actions=tuple(actions),
        source_count=len(source_by_id) if source_by_id else len(actions),
        raw_text=raw,
    )


def _normalize_action(
    raw: object,
    source_by_id: dict[str, str],
) -> MemoryOrganizationAction | None:
    if not isinstance(raw, dict):
        return None
    action = _normalize_action_kind(raw.get("action") or raw.get("type"))
    if action is None:
        return None

    memory_id = _first_text_value(raw, _ID_KEYS)
    if not memory_id:
        return None

    content = _first_text_value(
        raw,
        (
            "content",
            "suggested_content",
            "suggestedContent",
            "new_content",
            "newContent",
            "updated_content",
            "updatedContent",
            "memory",
            "text",
            "value",
            "内容",
            "建议内容",
            "新内容",
            "更新后内容",
        ),
    )
    if not content:
        content = source_by_id.get(memory_id, "")
    if not content:
        return None

    reason = _first_text_value(raw, ("reason", "why", "rationale", "原因", "理由"))
    related_ids = _normalize_related_ids(raw.get("related_ids") or raw.get("source_ids"), memory_id)
    return MemoryOrganizationAction(
        action=action,
        memory_id=memory_id,
        content=content[:800],
        reason=reason[:500],
        related_ids=related_ids,
    )


def _extract_raw_actions(data: dict[str, Any]) -> list[dict[str, object]] | None:
    """兼容模型常见 JSON 变体，并统一转换为带 action 的动作列表。"""

    for key in _DIRECT_ACTION_LIST_KEYS:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, list):
            return _actions_from_list(value)
        if isinstance(value, dict):
            nested_actions = _extract_raw_actions(value)
            if nested_actions is not None:
                return nested_actions

    grouped_actions: list[dict[str, object]] = []
    grouped_key_seen = False
    for action, keys in _ACTION_GROUP_KEYS.items():
        for key in keys:
            if key not in data:
                continue
            grouped_key_seen = True
            value = data.get(key)
            grouped_actions.extend(_actions_from_group(value, action))
    if grouped_key_seen:
        return grouped_actions

    # 有些模型会把所有建议包在单个 result/result 对象中。
    for key in ("result", "summary", "organization", "整理结果"):
        value = data.get(key)
        if isinstance(value, dict):
            nested_actions = _extract_raw_actions(value)
            if nested_actions is not None:
                return nested_actions
    return None


def _actions_from_list(items: list[object]) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for item in items:
        if isinstance(item, dict):
            actions.append(dict(item))
    return actions


def _actions_from_group(
    value: object,
    action: MemoryOrganizationActionKind,
) -> list[dict[str, object]]:
    if isinstance(value, list):
        actions: list[dict[str, object]] = []
        for item in value:
            if isinstance(item, dict):
                actions.extend(_expand_group_item(item, action))
                continue
            if action in {"delete", "keep"}:
                memory_id = str(item).strip()
                if memory_id:
                    actions.append({"action": action, "id": memory_id})
        return actions
    if isinstance(value, dict):
        if any(key in value for key in (*_ID_KEYS, *_MULTI_ID_KEYS)):
            return _expand_group_item(value, action)
        nested = _extract_raw_actions(value)
        if nested:
            return nested
    return []


def _expand_group_item(
    item: dict[str, object],
    action: MemoryOrganizationActionKind,
) -> list[dict[str, object]]:
    ids = _list_text_values(item, _MULTI_ID_KEYS)
    if not ids:
        return [_with_default_action(item, action)]
    actions: list[dict[str, object]] = []
    for memory_id in ids:
        normalized = _with_default_action(item, action)
        normalized["id"] = memory_id
        for key in _MULTI_ID_KEYS:
            normalized.pop(key, None)
        actions.append(normalized)
    return actions


def _with_default_action(
    item: dict[str, object],
    action: MemoryOrganizationActionKind,
) -> dict[str, object]:
    normalized = dict(item)
    normalized.setdefault("action", action)
    return normalized


def _normalize_action_kind(raw: object) -> MemoryOrganizationActionKind | None:
    action_text = str(raw or "").strip().lower()
    if not action_text:
        return None
    return _ACTION_VALUE_ALIASES.get(action_text)


def _first_text_value(raw: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _list_text_values(raw: dict[str, object], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for key in keys:
        raw_value = raw.get(key)
        if not isinstance(raw_value, list):
            continue
        for item in raw_value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return values


def _normalize_related_ids(raw: object, memory_id: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    related: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip()
        if not value or value == memory_id or value in seen:
            continue
        seen.add(value)
        related.append(value)
    return tuple(related)


def _load_json_object(raw: str) -> dict[str, Any]:
    text = _strip_json_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        try:
            data = _load_embedded_json(text)
        except ValueError as nested_exc:
            raise ValueError("长期记忆整理结果不是有效 JSON。") from nested_exc
        if data is None:
            raise ValueError("长期记忆整理结果不是有效 JSON。") from exc
    if isinstance(data, list):
        return {"actions": data}
    if not isinstance(data, dict):
        raise ValueError("长期记忆整理结果必须是 JSON 对象。")
    return data


def _strip_json_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip().lower()
    if first not in {"```", "```json"}:
        return text
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _load_embedded_json(text: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            data, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, (dict, list)):
            return data
    raise ValueError("长期记忆整理结果不是有效 JSON。")


def _normalize_source_memories(memories: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for memory in memories:
        memory_id = str(memory.get("id", "")).strip()
        content = str(memory.get("content") or memory.get("memory") or "").strip()
        if not memory_id or not content or memory_id in seen:
            continue
        seen.add(memory_id)
        normalized.append(
            {
                "id": memory_id,
                "content": content,
            }
        )
    return normalized


def _source_content_pairs(memories: list[dict[str, object]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for memory in _normalize_source_memories(memories):
        pairs.append((str(memory["id"]), str(memory["content"])))
    return pairs


def _chunk_memories_for_organization(
    memories: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    for memory in _normalize_source_memories(memories):
        memory_chars = _memory_record_char_count(memory)
        if current and (
            len(current) >= MAX_MEMORY_ORGANIZATION_CHUNK_ITEMS
            or current_chars + memory_chars > MAX_MEMORY_ORGANIZATION_CHUNK_CHARS
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(memory)
        current_chars += memory_chars
    if current:
        chunks.append(current)
    return chunks


def _memory_sort_key(memory: dict[str, object]) -> tuple[str, str]:
    content = " ".join(str(memory.get("content") or "").split()).casefold()
    memory_id = str(memory.get("id") or "")
    return content, memory_id


def _memory_record_char_count(memory: dict[str, object]) -> int:
    return len(str(memory.get("id") or "")) + len(str(memory.get("content") or "")) + 16


def _keep_plan_for_chunk(
    memories: list[dict[str, object]],
    *,
    reason: str,
) -> MemoryOrganizationPlan:
    actions = tuple(
        MemoryOrganizationAction(
            action="keep",
            memory_id=memory_id,
            content=content,
            reason=reason,
        )
        for memory_id, content in _source_content_pairs(memories)
    )
    return MemoryOrganizationPlan(
        actions=actions,
        source_count=len(actions),
        warnings=(reason,),
    )


def _looks_like_no_change_response(raw: str) -> bool:
    text = "".join(raw.strip().split()).lower()
    if not text:
        return False
    if any(marker in text for marker in ("```", "{\"", "[{\"", "\"actions\"")):
        return False
    change_markers = (
        "建议更新",
        "建议删除",
        "需要更新",
        "需要删除",
        "应删除",
        "应更新",
        "可删除",
        "可更新",
    )
    if any(marker in text for marker in change_markers):
        return False
    no_change_markers = (
        "无需修改",
        "不需要修改",
        "没有需要修改",
        "未发现需要修改",
        "没有发现需要修改",
        "无需整理",
        "没有需要整理",
        "未发现重复",
        "未发现明显重复",
        "没有发现重复",
        "没有发现明显重复",
        "没有发现重复项",
        "未发现重复项",
        "没有明显重复",
        "没有重复",
        "无重复",
        "未发现冲突",
        "未发现明显冲突",
        "没有发现冲突",
        "没有发现明显冲突",
        "没有发现冲突项",
        "未发现冲突项",
        "没有明显冲突",
        "没有冲突",
        "无冲突",
        "无需删除",
        "不需要删除",
        "没有需要删除",
        "未发现需要删除",
        "无需更新",
        "不需要更新",
        "没有需要更新",
        "未发现需要更新",
        "保持不变",
        "默认保留",
        "全部保留",
        "全都保留",
        "建议保留全部",
        "建议全部保留",
    )
    return any(marker in text for marker in no_change_markers)


def _memory_organization_user_prompt(
    memories: list[dict[str, object]],
    *,
    chunk_index: int = 1,
    chunk_count: int = 1,
    plain_retry: bool = False,
) -> str:
    retry_text = (
        "上一次返回无法解析为 JSON。这次必须只输出一个 JSON 对象，"
        "如果没有需要修改或删除的内容，返回 {\"actions\":[]}。"
        if plain_retry
        else "如果没有需要修改或删除的内容，返回 {\"actions\":[]}。"
    )
    return (
        f"请整理以下长期记忆（第 {chunk_index}/{chunk_count} 块）。"
        "这只是总列表的一部分，请只处理当前输入中的 ID，不要引用未给出的记忆。"
        "请重点输出需要 update/delete 的记忆；有冲突或不确定但不应修改的记忆才显式输出 keep。"
        "完全独立且无需修改的记忆可以省略，系统会默认保留。"
        f"{retry_text}"
        "只返回 JSON，不要输出 Markdown、分析过程或解释。\n\n"
        f"{json.dumps({'chunk_index': chunk_index, 'chunk_count': chunk_count, 'memories': memories}, ensure_ascii=False)}"
    )


_MEMORY_ORGANIZATION_SYSTEM_PROMPT = (
    "你是 Sakura 的长期记忆整理器。你的任务是分析已经存在的长期记忆，找出重复、冗余、"
    "语义高度相似或互相冲突的内容，并生成可供用户预览确认的整理建议。"
    "只能使用输入中已经存在的记忆 ID，不要编造 ID。"
    "动作只能是 update、delete、keep。"
    "update 表示把该 ID 的记忆改写为更准确、合并后的简体中文内容；"
    "delete 表示该 ID 是明显重复或被 update 合并后的冗余记忆；"
    "keep 表示该 ID 暂不修改。"
    "遇到冲突事实时不要擅自删除冲突项，优先用 keep 标出原因，或用 update 改写为更审慎的表达。"
    "纯粹无需修改的记忆可以省略，系统会默认保留；但冲突、不确定或需要用户关注的保留项必须显式 keep。"
    "每条建议必须包含 action、id、content、reason，可选 related_ids。"
    "如果没有任何需要修改、删除或特别提示的内容，必须返回 {\"actions\":[]}。"
    "必须返回严格 JSON：{\"actions\":[{\"action\":\"update|delete|keep\",\"id\":\"...\","
    "\"content\":\"...\",\"reason\":\"...\",\"related_ids\":[\"...\"]}]}。"
)
