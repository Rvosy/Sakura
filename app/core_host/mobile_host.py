"""Compatibility facade for the former mobile-specific Host API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core_host.conversation_host import ConversationHostService
from app.core_host.screen_host import caller_identity
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineKind, TimelineStore


class MobileHostError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MobileHostService:
    def __init__(self, user_root: Path, *, session_provider: Callable,
                 conversation: ConversationHostService, characters: object) -> None:
        self._user_root = Path(user_root)
        self._session_provider = session_provider
        self._conversation = conversation
        self._characters = characters
        self._timeline = TimelineStore(StoragePaths(self._user_root).timeline_database())

    def characters(self) -> list[dict[str, str]]:
        session = self._require_session()
        current = getattr(session, "character", None)
        current_id = str(getattr(current, "id", ""))
        if not current_id:
            raise MobileHostError("ASSISTANT_NOT_READY")
        try:
            profiles = self._characters.list()
        except Exception as error:
            raise MobileHostError("CHARACTER_REGISTRY_UNAVAILABLE") from error
        return [{"id": item["id"], "name": item["displayName"],
                 "initial_message": item["initialMessage"],
                 "current": "true" if item["id"] == current_id else "false"}
                for item in profiles]

    def history(self, character_id: str, limit: int = 50) -> list[dict[str, str]]:
        profile = self._current_character(character_id)
        try:
            entries, _cursor = self._timeline.read_recent(
                profile.id,
                max(1, min(int(limit), 200)),
            )
        except Exception as error:
            raise MobileHostError("TIMELINE_READ_FAILED") from error
        projected: list[dict[str, str]] = []
        for entry in entries:
            if entry.kind is TimelineKind.HUMAN:
                content = str(entry.payload.get("text") or "").strip()
                role = "user"
                raw_content = content
                translation = ""
            elif entry.kind is TimelineKind.ASSISTANT:
                raw_segments = entry.payload.get("segments")
                if not isinstance(raw_segments, list):
                    continue
                texts: list[str] = []
                raw_texts: list[str] = []
                translations: list[str] = []
                for segment in raw_segments:
                    if not isinstance(segment, Mapping):
                        continue
                    text = str(segment.get("text") or "").strip()
                    translated = str(segment.get("translation") or "").strip()
                    if text:
                        raw_texts.append(text)
                        texts.append(translated or text)
                    if translated:
                        translations.append(translated)
                content = "\n".join(texts)
                raw_content = "\n".join(raw_texts)
                translation = "\n".join(translations)
                role = "assistant"
            else:
                continue
            if content:
                projected.append({
                    "created_at": entry.created_at,
                    "role": role,
                    "content": content,
                    "raw_content": raw_content,
                    "translation": translation,
                })
        return projected

    def _call(self, plugin_id: str, method: str, *args):
        if plugin_id != caller_identity()[0]:
            raise MobileHostError("PLUGIN_CALLER_INVALID")
        try:
            return getattr(self._conversation, method)(*args)
        except Exception as error:
            codes = {
                "CHAT_CHARACTER_NOT_CURRENT": "MOBILE_CHARACTER_NOT_CURRENT",
                "CHAT_IMAGE_INVALID": "MOBILE_IMAGE_INVALID",
                "CHAT_JOB_NOT_FOUND": "MOBILE_CHAT_JOB_NOT_FOUND",
                "CHAT_START_FAILED": "MOBILE_CHAT_UNAVAILABLE",
                "CHAT_FAILED": "MOBILE_CHAT_FAILED",
            }
            code = getattr(error, "code", "MOBILE_CHAT_FAILED")
            raise MobileHostError(codes.get(code, code)) from error

    def begin(self, plugin_id: str, character_id: str, text: str,
              artifact_descriptor: Mapping[str, Any] | str | None = None) -> dict[str, str]:
        # Older mobile clients use an empty string for a message without an image.
        if artifact_descriptor == "":
            artifact_descriptor = None
        result = self._call(plugin_id, "begin", character_id, text, artifact_descriptor)
        return {"jobId": result["jobId"]}

    def poll(self, plugin_id: str, job_id: str) -> dict[str, Any]:
        return self._call(plugin_id, "poll", job_id)

    def cancel(self, plugin_id: str, job_id: str) -> dict[str, bool]:
        return self._call(plugin_id, "cancel", job_id)

    def theme(self) -> dict[str, object]:
        self._current_character("")
        presentation = self._characters.presentation()
        tokens = presentation.get("themeTokens")
        return dict(tokens) if isinstance(tokens, Mapping) else {}

    def _require_session(self) -> object:
        session = self._session_provider()
        if session is None:
            raise MobileHostError("ASSISTANT_NOT_READY")
        return session

    def _current_character(self, character_id: str) -> object:
        profile = getattr(self._require_session(), "character", None)
        current_id = str(getattr(profile, "id", ""))
        requested = str(character_id).strip() or current_id
        if not current_id or requested != current_id:
            raise MobileHostError("MOBILE_CHARACTER_NOT_CURRENT")
        return profile
