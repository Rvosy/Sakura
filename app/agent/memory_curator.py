from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.debug_log import debug_log
from app.agent.memory import MemoryStore
from app.core.cancellation import CancelChecker, OperationCancelled, check_cancelled
from app.storage.atomic import atomic_write_text
from app.storage.chat_history import ChatHistoryEntry


DEFAULT_AUTO_MEMORY_TRIGGER_TURNS = 8
DEFAULT_AUTO_MEMORY_BACKFILL_LIMIT = 200
MAX_CURATION_CHUNK_MESSAGES = 32
MAX_CURATION_CHUNK_CHARS = 12000


@dataclass(frozen=True)
class MemoryCurationSettings:
    enabled: bool = True
    trigger_turns: int = DEFAULT_AUTO_MEMORY_TRIGGER_TURNS
    backfill_limit: int = DEFAULT_AUTO_MEMORY_BACKFILL_LIMIT


@dataclass(frozen=True)
class MemoryCurationResult:
    created: int = 0
    updated: int = 0
    archived: int = 0
    ignored: int = 0
    processed_entries: int = 0
    returned: int = 0
    unclassified: int = 0
    event_counts: dict[str, int] | None = None

    def summary(self) -> str:
        return (
            f"整理完成：新增 {self.created} 条，更新 {self.updated} 条，"
            f"删除 {self.archived} 条，忽略 {self.ignored} 条。"
        )


class MemoryCurationState:
    """记录自动整理进度，避免重复处理历史。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return _normalize_state({})
        try:
            raw_data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _normalize_state({})
        return _normalize_state(raw_data)

    def pending_turns(self) -> int:
        return int(self.snapshot()["pending_turns"])

    def increment_pending_turns(self) -> int:
        state = self.snapshot()
        state["pending_turns"] = int(state["pending_turns"]) + 1
        self._save(state)
        return int(state["pending_turns"])

    def mark_processed(
        self,
        processed_history_count: int,
        *,
        consumed_turns: int = 0,
        backfill_completed: bool | None = None,
    ) -> None:
        state = self.snapshot()
        state["processed_history_count"] = max(0, processed_history_count)
        state["pending_turns"] = max(0, int(state["pending_turns"]) - max(0, consumed_turns))
        if backfill_completed is not None:
            state["backfill_completed"] = bool(backfill_completed)
        self._save(state)

    def mark_history_cleared(self) -> None:
        state = self.snapshot()
        state["processed_history_count"] = 0
        state["pending_turns"] = 0
        state["backfill_completed"] = True
        self._save(state)

    def unprocessed_entries(self, entries: list[ChatHistoryEntry]) -> list[ChatHistoryEntry]:
        state = self.snapshot()
        processed = int(state["processed_history_count"])
        if processed < 0 or processed > len(entries):
            processed = 0
        return entries[processed:]

    def _save(self, state: dict[str, Any]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(_normalize_state(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class MemoryCurator:
    """调用 mem0 把聊天历史整理为长期记忆。"""

    def __init__(
        self,
        api_client_or_memory_store: Any,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.api_client = None if memory_store is None else api_client_or_memory_store
        self.memory_store = (
            api_client_or_memory_store
            if memory_store is None
            else memory_store
        )

    def curate_entries(
        self,
        entries: list[ChatHistoryEntry],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> MemoryCurationResult:
        model_entries = _entries_for_model(entries)
        if not model_entries:
            return MemoryCurationResult(processed_entries=len(entries))

        created = 0
        updated = 0
        archived = 0
        ignored = 0
        returned = 0
        unclassified = 0
        event_counts: dict[str, int] = {}
        for chunk in _chunk_entries_for_curation(entries):
            check_cancelled(cancel_checker)
            counts = self.memory_store.add_history_entries(chunk)
            check_cancelled(cancel_checker)
            chunk_created = counts.created
            chunk_updated = counts.updated
            chunk_archived = counts.deleted
            chunk_ignored = counts.ignored
            chunk_returned = counts.returned
            chunk_unclassified = counts.unclassified
            _merge_event_counts(event_counts, counts.event_counts)
            if chunk_returned == 0 and self.api_client is not None:
                fallback_created = self._curate_entries_with_fallback(
                    _entries_for_model(chunk),
                    cancel_checker=cancel_checker,
                )
                if fallback_created:
                    event_counts["FALLBACK_ADD"] = event_counts.get("FALLBACK_ADD", 0) + fallback_created
                    chunk_created += fallback_created
                    chunk_returned += fallback_created
                    chunk_ignored = max(0, chunk_ignored - fallback_created)
            created += chunk_created
            updated += chunk_updated
            archived += chunk_archived
            ignored += chunk_ignored
            returned += chunk_returned
            unclassified += chunk_unclassified
        return MemoryCurationResult(
            created=created,
            updated=updated,
            archived=archived,
            ignored=ignored,
            processed_entries=len(entries),
            returned=returned,
            unclassified=unclassified,
            event_counts=event_counts,
        )

    def _curate_entries_with_fallback(
        self,
        entries: list[dict[str, str]],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> int:
        """mem0 抽取为空时，用主模型兜底抽取明确长期事实。"""

        if not entries:
            return 0
        prompt = _fallback_extraction_prompt(entries)
        try:
            raw = self.api_client.complete_raw(
                _FALLBACK_SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_tokens=1200,
                cancel_checker=cancel_checker,
            )
        except OperationCancelled:
            raise
        except Exception as exc:  # 兜底失败不应让主聊天崩溃。
            debug_log("Memory", "记忆整理兜底抽取失败", {"error": str(exc)})
            return 0

        memories = _parse_fallback_memories(raw)
        debug_log(
            "Memory",
            "记忆整理兜底抽取完成",
            {"candidate_count": len(memories), "raw_chars": len(raw)},
        )
        created = 0
        for memory in memories:
            try:
                self.memory_store.create_memory(
                    {"content": memory, "source": "curation_fallback"},
                    allow_sensitive=True,
                )
            except Exception as exc:  # 单条失败只跳过，保留其它可用结果。
                debug_log("Memory", "记忆整理兜底写入失败", {"error": str(exc), "memory": memory})
                continue
            created += 1
        return created


def _merge_event_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def _chunk_entries_for_curation(entries: list[ChatHistoryEntry]) -> list[list[ChatHistoryEntry]]:
    chunks: list[list[ChatHistoryEntry]] = []
    current: list[ChatHistoryEntry] = []
    current_messages = 0
    current_chars = 0
    for entry in entries:
        model_entry = _entry_for_model(entry)
        if model_entry is None:
            continue
        entry_chars = _model_entry_char_count(model_entry)
        if current and (
            current_messages >= MAX_CURATION_CHUNK_MESSAGES
            or current_chars + entry_chars > MAX_CURATION_CHUNK_CHARS
        ):
            chunks.append(current)
            current = []
            current_messages = 0
            current_chars = 0
        current.append(entry)
        current_messages += 1
        current_chars += entry_chars
    if current:
        chunks.append(current)
    return chunks


def _entry_for_model(entry: ChatHistoryEntry) -> dict[str, str] | None:
    if entry.role not in {"user", "assistant"}:
        return None
    content = entry.content.strip()
    if not content:
        return None
    return {
        "created_at": entry.created_at,
        "role": entry.role,
        "content": content,
        "translation": entry.translation.strip(),
    }


def _model_entry_char_count(entry: dict[str, str]) -> int:
    return (
        len(entry.get("created_at", ""))
        + len(entry.get("role", ""))
        + len(entry.get("content", ""))
        + len(entry.get("translation", ""))
    )


def _entries_for_model(entries: list[ChatHistoryEntry]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for entry in entries:
        model_entry = _entry_for_model(entry)
        if model_entry is not None:
            result.append(model_entry)
    return result


_FALLBACK_SYSTEM_PROMPT = (
    "你是长期记忆抽取器。只提取明确、长期有用、以后会帮助陪伴或协作的事实。"
    "忽略寒暄、临时提醒、重复内容、一次性工具调用、无长期价值的情绪附和。"
    "必须返回严格 JSON：{\"memories\":[\"...\"]}。如果没有可记忆事实，返回 {\"memories\":[]}。"
)


def _fallback_extraction_prompt(entries: list[dict[str, str]]) -> str:
    return (
        "请从以下聊天记录中提取长期记忆。记忆必须使用简体中文，且每条自包含、可独立理解。\n\n"
        f"{json.dumps(entries, ensure_ascii=False)}"
    )


def _parse_fallback_memories(raw: str) -> list[str]:
    data = _load_fallback_json(raw)
    candidates = data.get("memories") or data.get("memory") or []
    if not isinstance(candidates, list):
        return []
    memories: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            text = str(item.get("content") or item.get("memory") or item.get("text") or "").strip()
        else:
            text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        memories.append(text[:500])
        if len(memories) >= 10:
            break
    return memories


def _load_fallback_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _normalize_state(raw_data: Any) -> dict[str, Any]:
    data = raw_data if isinstance(raw_data, dict) else {}
    return {
        "processed_history_count": max(0, _int_value(data.get("processed_history_count"), default=0)),
        "pending_turns": max(0, _int_value(data.get("pending_turns"), default=0)),
        "backfill_completed": bool(data.get("backfill_completed", False)),
    }


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
