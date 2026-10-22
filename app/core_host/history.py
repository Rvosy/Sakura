"""当前角色的历史读取与活动记录入口。"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core_host.protocol import response
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineDataError, TimelineEntry, TimelineStore, timeline_record
from app.plugin_sdk.sakura_data_transfer import read_archive, write_archive
from app.core.runtime_log import log_event


HISTORY_REQUEST_NAMES = frozenset({"ui.history.page", "ui.history.dates", "ui.history.open_record", "ui.history.transfer"})
HISTORY_PAGE_LIMIT = 50
_HISTORY_RESPONSE_BYTES = 700 * 1024


class HistoryBoundaryError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": {"feature": "ui.history", "field": ""},
        }


class HistoryBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        user_root: Path,
        *,
        character_provider: Callable[[], str | None],
        application_provider: Callable[[], object | None] = lambda: None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._character_provider = character_provider
        self._application_provider = application_provider
        self._timeline = TimelineStore(StoragePaths(user_root).timeline_database())
        self._import_identities = user_root / "data" / "import-identities.sqlite3"

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            if request.get("name") == "ui.history.dates":
                result = self._dates(request["payload"])
                return response(request, generation_id=self._generation_id,
                    generation_credential=self._generation_credential, payload=result)
            if request.get("name") == "ui.history.transfer":
                result = self._transfer(request["payload"])
                return response(request, generation_id=self._generation_id,
                    generation_credential=self._generation_credential, payload=result)
            if request.get("name") == "ui.history.open_record":
                self._open_record(request.get("payload"))
                return response(request, generation_id=self._generation_id,
                    generation_credential=self._generation_credential, payload={"opened": True})
            if request.get("name") != "ui.history.page":
                raise HistoryBoundaryError("UNKNOWN_COMMAND", "不支持的聊天记录请求。")
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise HistoryBoundaryError("INVALID_REQUEST", "聊天记录请求格式无效。")

            expected_character_id = payload.get("expectedCharacterId")
            before_cursor = payload.get("beforeCursor")
            after_cursor = payload.get("afterCursor")
            from_date = payload.get("fromDate")
            from_entry_id = payload.get("fromEntryId")
            query = payload.get("query")
            limit = payload.get("limit")
            if not isinstance(expected_character_id, str) or not expected_character_id.strip():
                raise HistoryBoundaryError("INVALID_REQUEST", "聊天记录角色标识无效。")
            if sum(value is not None for value in (before_cursor, after_cursor, from_date, from_entry_id)) > 1:
                raise HistoryBoundaryError("INVALID_REQUEST", "一次只能指定一个聊天记录起点。")
            if query is not None:
                if not isinstance(query, str):
                    raise HistoryBoundaryError("INVALID_REQUEST", "搜索内容必须是文字。")
                query = query.strip()
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= HISTORY_PAGE_LIMIT:
                raise HistoryBoundaryError("INVALID_REQUEST", "聊天记录分页大小无效。")
            if from_date is not None:
                try:
                    from_date = date.fromisoformat(from_date).isoformat()
                except (ValueError, TypeError) as error:
                    raise HistoryBoundaryError("INVALID_REQUEST", "聊天记录日期无效。") from error

            character_id = self._current_character_id()
            if character_id != expected_character_id:
                raise HistoryBoundaryError(
                    "HISTORY_CHARACTER_MISMATCH",
                    "当前角色已经变化，请刷新聊天记录。",
                )
            started = perf_counter()
            if query:
                log_event("History", "正在搜索聊天记录", {"character_id": character_id, "query_length": len(query)},
                          event="history.search.started")
            entries, before, after, total = self._timeline.read_history_page(
                character_id,
                limit,
                before_cursor=before_cursor,
                after_cursor=after_cursor,
                from_date=from_date,
                from_entry_id=from_entry_id,
                query=query,
                max_bytes=_HISTORY_RESPONSE_BYTES,
            )
            if query:
                log_event("History", "聊天记录搜索完成", {"character_id": character_id, "matches": total,
                          "returned": len(entries), "elapsed_ms": round((perf_counter() - started) * 1000)},
                          event="history.search.completed")
            result = {
                "schemaVersion": 1,
                "coreGenerationId": self._generation_id,
                "characterId": character_id,
                "totalCount": total,
                "entries": [_entry_mapping(entry) for entry in entries],
                "beforeCursor": before,
                "afterCursor": after,
            }
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except HistoryBoundaryError as error:
            return self._error_response(request, error)
        except TimelineDataError as error:
            if request.get("name") == "ui.history.transfer":
                return self._error_response(request, HistoryBoundaryError("HISTORY_TRANSFER_FAILED", str(error)))
            code = str(error)
            if code == "TIMELINE_CURSOR_INVALID":
                public = HistoryBoundaryError(
                    code,
                    "聊天记录已发生变化，请刷新后重试。",
                )
            elif code == "TIMELINE_ENTRY_NOT_FOUND":
                public = HistoryBoundaryError(code, "这条记录已不存在，请重新搜索。")
            elif code == "TIMELINE_NOT_ACTIVATED":
                public = HistoryBoundaryError(
                    "HISTORY_NOT_READY",
                    "聊天记录仍在准备，请稍后刷新。",
                    retryable=True,
                )
            else:
                log_event("History", "聊天记录读取失败", {"error": str(error.__cause__ or error)},
                          event="history.read.failed", severity="error")
                public = HistoryBoundaryError(
                    "TIMELINE_READ_FAILED",
                    "聊天记录读取失败，请稍后刷新。",
                    retryable=True,
                )
            return self._error_response(request, public)
        except Exception as error:
            if request.get("name") == "ui.history.transfer":
                log_event("History", "聊天记录导入导出失败", {"error": str(error)},
                          event="history.transfer.failed", severity="error")
                return self._error_response(request, HistoryBoundaryError("HISTORY_TRANSFER_FAILED", str(error)))
            log_event("History", "聊天记录读取失败", {"error": str(error)},
                      event="history.read.failed", severity="error")
            return self._error_response(
                request,
                HistoryBoundaryError(
                    "TIMELINE_READ_FAILED",
                    "聊天记录读取失败，请稍后刷新。",
                    retryable=True,
                ),
            )

    def _dates(self, payload) -> dict:
        if payload["expectedGenerationId"] != self._generation_id:
            raise HistoryBoundaryError("HISTORY_IDENTITY_MISMATCH", "后台已重启，请重新打开日历。")
        character_id = self._current_character_id()
        if payload["expectedCharacterId"] != character_id:
            raise HistoryBoundaryError("HISTORY_CHARACTER_MISMATCH", "角色已变化，请重新打开日历。")
        try:
            start = date.fromisoformat(payload["startDate"]).isoformat()
            end = date.fromisoformat(payload["endDate"]).isoformat()
        except (ValueError, TypeError) as error:
            raise HistoryBoundaryError("INVALID_REQUEST", "日历日期无效。") from error
        return {"dates": self._timeline.record_dates(character_id, start, end)}

    def _transfer(self, payload) -> dict:
        if payload["expectedGenerationId"] != self._generation_id:
            raise HistoryBoundaryError("HISTORY_IDENTITY_MISMATCH", "后台已重启，请重新打开导入导出。")
        character_id = self._current_character_id()
        if payload["expectedCharacterId"] != character_id:
            raise HistoryBoundaryError("HISTORY_CHARACTER_MISMATCH", "角色已变化，请重新打开导入导出。")
        action = payload["action"]
        if action == "range":
            start, end = self._timeline.date_range(character_id)
            return {"startDate": start, "endDate": end}
        log_event("History", "正在处理聊天记录文件", {"character_id": character_id, "action": action},
                  event="history.transfer.started")
        if action == "export":
            records = [timeline_record(entry) for entry in self._timeline.read_all(character_id)]
            start, end = payload.get("startDate", ""), payload.get("endDate", "")
            if start or end:
                records = [item for item in records if
                           (not start or item["created_at"][:10] >= start) and
                           (not end or item["created_at"][:10] <= end)]
            result = write_archive(payload["path"], kind="history", provider="sakura.timeline",
                                   character_id=character_id, records=records, format=payload["format"])
        elif action in {"preview", "import"}:
            archive = read_archive(payload["path"], kind="history", provider="sakura.timeline",
                                   character_id=character_id, identity_path=self._import_identities)
            result = self._timeline.import_records(character_id, archive["records"], preview=action == "preview")
            result["sourceCharacterId"] = archive["characterId"]
        else:
            raise ValueError("不支持的数据操作。")
        log_event("History", "聊天记录文件处理完成", {"character_id": character_id, "action": action, **result},
                  event="history.transfer.completed")
        return result

    def _open_record(self, payload) -> None:
        if (not isinstance(payload, Mapping) or set(payload) != {"expectedCharacterId", "entryId"}
                or not isinstance(payload["entryId"], str)):
            raise HistoryBoundaryError("INVALID_REQUEST", "活动记录请求格式无效。")
        character_id = self._current_character_id()
        if character_id != payload["expectedCharacterId"]:
            raise HistoryBoundaryError("HISTORY_CHARACTER_MISMATCH", "当前角色已经变化，请刷新聊天记录。")
        entry = self._timeline.get_entry(character_id, payload["entryId"])
        activity = entry.payload.get("activity") if entry else None
        if not activity:
            raise HistoryBoundaryError("HISTORY_RECORD_NOT_FOUND", "这条记录没有可打开的页面。")
        application = self._application_provider()
        if application is None:
            raise HistoryBoundaryError("HISTORY_PLUGIN_UNAVAILABLE", "插件尚未就绪，请稍后重试。")
        try:
            application.chat.open_record(activity["sourcePluginId"], activity["recordId"])
        except Exception as error:
            raise HistoryBoundaryError(getattr(error, "code", "HISTORY_RECORD_OPEN_FAILED"),
                getattr(error, "public_message", str(error))) from error

    def _current_character_id(self) -> str:
        character_id = self._character_provider()
        if not isinstance(character_id, str) or not character_id:
            raise HistoryBoundaryError(
                "HISTORY_NOT_READY",
                "当前角色仍在准备，请稍后刷新。",
                retryable=True,
            )
        return character_id

    def _error_response(
        self,
        request: dict[str, Any],
        error: HistoryBoundaryError,
    ) -> dict[str, Any]:
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            error=error.public_error(),
        )


def _entry_mapping(entry: TimelineEntry) -> dict[str, Any]:
    payload = entry.payload
    kind_text = entry.kind.value
    if kind_text == "assistant":
        public_payload: dict[str, Any] = {
            "segments": [
                {
                    "text": segment["text"],
                    "translation": segment["translation"],
                    "suppressTts": segment["suppressTts"],
                }
                for segment in payload["segments"]
            ]
        }
    else:
        public_payload = {"text": payload["text"]}
        if kind_text == "observation":
            for field in ("sourcePluginId", "sourcePluginName", "notificationKind"):
                if field in payload:
                    public_payload[field] = payload[field]
            visual = payload.get("visual")
            if visual and "imageCount" in visual:
                public_payload["visual"] = {"imageCount": visual["imageCount"]}
        elif kind_text == "system":
            for field in ("eventType", "activity"):
                if field in payload:
                    public_payload[field] = payload[field]
    return {
        "entryId": entry.entry_id,
        "turnId": entry.turn_id,
        "kind": kind_text,
        "origin": entry.origin,
        "createdAt": entry.created_at,
        "payload": public_payload,
    }


__all__ = ["HISTORY_PAGE_LIMIT", "HISTORY_REQUEST_NAMES", "HistoryBoundary"]
