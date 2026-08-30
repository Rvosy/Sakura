"""Read-only current-character history boundary for the Runtime v2 shell."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core_host.protocol import response
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineDataError, TimelineStore


HISTORY_REQUEST_NAMES = frozenset({"ui.history.page"})
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
        session_provider: Callable[[], object | None],
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._session_provider = session_provider
        self._timeline = TimelineStore(StoragePaths(user_root).timeline_database())

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            if request.get("name") != "ui.history.page":
                raise HistoryBoundaryError("UNKNOWN_COMMAND", "不支持的历史记录请求。")
            payload = request.get("payload")
            if not isinstance(payload, Mapping) or set(payload) != {
                "expectedCharacterId",
                "beforeCursor",
                "limit",
            }:
                raise HistoryBoundaryError("INVALID_REQUEST", "历史记录请求格式无效。")

            expected_character_id = payload.get("expectedCharacterId")
            before_cursor = payload.get("beforeCursor")
            limit = payload.get("limit")
            if not isinstance(expected_character_id, str) or not expected_character_id.strip():
                raise HistoryBoundaryError("INVALID_REQUEST", "历史记录角色标识无效。")
            if before_cursor is not None and not isinstance(before_cursor, str):
                raise HistoryBoundaryError("INVALID_REQUEST", "历史记录游标无效。")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= HISTORY_PAGE_LIMIT:
                raise HistoryBoundaryError("INVALID_REQUEST", "历史记录分页大小无效。")

            character_id = self._current_character_id()
            if character_id != expected_character_id:
                raise HistoryBoundaryError(
                    "HISTORY_CHARACTER_MISMATCH",
                    "当前角色已经变化，请刷新历史记录。",
                )
            entries, next_cursor, has_more, total = self._timeline.read_page_before(
                character_id,
                limit,
                before_cursor=before_cursor,
                max_bytes=_HISTORY_RESPONSE_BYTES,
            )
            result = {
                "schemaVersion": 1,
                "coreGenerationId": self._generation_id,
                "characterId": character_id,
                "totalCount": total,
                "entries": [_entry_mapping(entry) for entry in entries],
                "beforeCursor": next_cursor,
                "hasMore": has_more,
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
            code = str(error)
            if code == "TIMELINE_CURSOR_INVALID":
                public = HistoryBoundaryError(
                    code,
                    "历史记录已发生变化，请刷新后重试。",
                )
            elif code == "TIMELINE_NOT_ACTIVATED":
                public = HistoryBoundaryError(
                    "HISTORY_NOT_READY",
                    "聊天记录仍在准备，请稍后刷新。",
                    retryable=True,
                )
            else:
                public = HistoryBoundaryError(
                    "TIMELINE_READ_FAILED",
                    "历史记录读取失败，请稍后刷新。",
                    retryable=True,
                )
            return self._error_response(request, public)
        except Exception:  # noqa: BLE001 - private storage failures stay behind the boundary
            return self._error_response(
                request,
                HistoryBoundaryError(
                    "TIMELINE_READ_FAILED",
                    "历史记录读取失败，请稍后刷新。",
                    retryable=True,
                ),
            )

    def _current_character_id(self) -> str:
        session = self._session_provider()
        character = getattr(session, "character", None) if session is not None else None
        character_id = getattr(character, "id", None)
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


def _entry_mapping(entry: object) -> dict[str, Any]:
    kind = getattr(entry, "kind")
    kind_value = getattr(kind, "value", kind)
    payload = getattr(entry, "payload")
    if not isinstance(payload, Mapping):
        raise HistoryBoundaryError("TIMELINE_READ_FAILED", "历史记录读取失败，请稍后刷新。")
    kind_text = str(kind_value)
    if kind_text == "assistant":
        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise HistoryBoundaryError("TIMELINE_READ_FAILED", "历史记录读取失败，请稍后刷新。")
        public_payload: dict[str, Any] = {
            "segments": [
                {
                    "text": str(segment.get("text", "")),
                    "translation": str(segment.get("translation", "")),
                }
                for segment in segments
                if isinstance(segment, Mapping)
            ]
        }
    else:
        public_payload = {"text": str(payload.get("text", ""))}
    return {
        "entryId": str(getattr(entry, "entry_id")),
        "turnId": str(getattr(entry, "turn_id")),
        "kind": kind_text,
        "origin": str(getattr(entry, "origin")),
        "createdAt": str(getattr(entry, "created_at")),
        "payload": json.loads(json.dumps(public_payload, ensure_ascii=False)),
    }


__all__ = ["HISTORY_PAGE_LIMIT", "HISTORY_REQUEST_NAMES", "HistoryBoundary"]
