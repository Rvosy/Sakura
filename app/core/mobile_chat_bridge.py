from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from app.agent.runtime import AgentRuntime
from app.agent.tools.registry import ToolRegistry
from app.config.character_loader import CharacterProfile, load_character_system_prompt
from app.llm.api_client import ChatMessage, OpenAICompatibleClient
from app.llm.context_trimming import trim_messages_for_model
from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore


MAX_MOBILE_HISTORY_MESSAGES = 24
MOBILE_IMAGE_MARKER = "（手机端发送了一张图片）"


class MobileChatBusyError(RuntimeError):
    """Raised when the desktop chat lane cannot accept a mobile request now."""


@dataclass
class _MobileCharacterSession:
    profile: CharacterProfile
    runtime: AgentRuntime
    history_store: ChatHistoryStore


class MobileChatBridge:
    """让手机插件复用桌宠主进程的对话、历史和记忆服务。"""

    def __init__(self, host: Any) -> None:
        self._host = host
        self._sessions: dict[str, _MobileCharacterSession] = {}
        self._lock = threading.RLock()

    def characters(self) -> list[dict[str, str]]:
        profile = self._host.character_profile
        return [
            {
                "id": profile.id,
                "name": profile.display_name,
                "initial_message": profile.initial_message,
                "current": "true",
            }
        ]

    def history(self, character_id: str, limit: int = 50) -> list[dict[str, str]]:
        with self._lock:
            session = self._session(character_id)
            entries = session.history_store.load()[-max(1, limit) :]
        return [_history_entry_for_mobile(entry) for entry in entries]

    def chat(self, character_id: str, text: str, image_data_url: str = "") -> dict[str, Any]:
        submit = getattr(self._host, "submit_mobile_chat", None)
        if not callable(submit):
            raise RuntimeError("移动端聊天调度器尚未就绪。")
        return submit(self, character_id, text, image_data_url)

    def execute_chat(self, character_id: str, text: str, image_data_url: str = "") -> dict[str, Any]:
        """Execute one request inside the host-controlled Qt worker queue."""
        clean_text = text.strip()
        clean_image = image_data_url.strip()
        if not clean_text and not clean_image:
            raise ValueError("消息内容不能为空。")
        if clean_image and not clean_image.startswith("data:image/"):
            raise ValueError("图片必须是 data:image/... 格式。")

        with self._lock:
            session = self._session(character_id)
            user_history_text = clean_text or "请看这张图片。"
            if clean_image:
                user_history_text = f"{user_history_text}\n{MOBILE_IMAGE_MARKER}"
            session.history_store.append("user", user_history_text)

            messages = _messages_from_history(session.history_store.load())
            if not messages or messages[-1].get("role") != "user":
                messages.append({"role": "user", "content": user_history_text})
            if clean_image:
                messages[-1] = _mobile_image_message(clean_text or "请看这张图片。", clean_image)
            messages = trim_messages_for_model(messages[-MAX_MOBILE_HISTORY_MESSAGES:])

            runtime = session.runtime
            runtime.api_client.update_settings(self._host.api_client.settings)
            runtime.set_prompt_patches(self._host.agent_runtime.prompt_patches)
            runtime.set_context_providers(self._context_providers(session.profile))

            memory_store = self._host.memory_store
            previous_scope = memory_store.scope_id
            memory_store.set_scope(session.profile.id)
            try:
                result = runtime.handle_user_message(messages)
            finally:
                memory_store.set_scope(previous_scope)

            segments = [segment for segment in result.reply.segments if segment.text.strip()]
            for segment in segments:
                session.history_store.append(
                    "assistant",
                    segment.text,
                    segment.translation,
                    segment.tone,
                    segment.portrait,
                )

        self._notify_host_chat_completed(
            {
                "character_id": session.profile.id,
                "user_text": user_history_text,
                "assistant_text": result.reply.text,
                "segments": segments,
            }
        )
        return {
            "character_id": session.profile.id,
            "reply": result.reply.display_text("zh"),
            "reply_raw": result.reply.text,
            "translation": result.reply.translation,
            "tone": result.reply.tone,
            "segments": [_segment_for_mobile(segment) for segment in segments],
            "actions": [action.type for action in result.actions],
        }

    def _session(self, character_id: str) -> _MobileCharacterSession:
        clean_id = character_id.strip() or self._host.character_profile.id
        current_id = self._host.character_profile.id
        if clean_id != current_id:
            raise ValueError("手机端只能使用桌面当前角色。")
        session = self._sessions.get(clean_id)
        if session is not None:
            return session

        profile = self._host.character_registry.get(clean_id)
        history_store = self._host._create_history_store(profile)
        memory_store = self._host.memory_store
        runtime = AgentRuntime(
            api_client=OpenAICompatibleClient(self._host.api_client.settings),
            system_prompt=load_character_system_prompt(profile),
            reply_tones=profile.reply_tones,
            reply_portraits=profile.portrait_choices,
            # Mobile has no confirmation UI yet. Do not advertise host tools
            # and leave a user stranded with a pending high-risk action.
            tools=ToolRegistry([]),
            memory=memory_store,
            history_store=history_store,
            prompt_patches=self._host.agent_runtime.prompt_patches,
            context_providers=self._context_providers(profile),
            runtime_loop_settings=self._host.agent_runtime.runtime_loop_settings,
            character_id=profile.id,
            character_name=profile.display_name,
        )
        runtime.set_autonomous_screen_observation_enabled(False)
        session = _MobileCharacterSession(
            profile,
            runtime,
            history_store,
        )
        self._sessions[profile.id] = session
        return session

    def _context_providers(self, profile: CharacterProfile) -> list[Any]:
        provider_factory = getattr(self._host, "mobile_context_providers", None)
        if callable(provider_factory):
            return list(provider_factory(profile))
        return list(getattr(self._host.agent_runtime, "context_providers", []))

    def _notify_host_chat_completed(self, payload: dict[str, Any]) -> None:
        """让 UI 线程安全地同步当前角色手机对话到桌面状态。"""
        signal = getattr(self._host, "mobile_chat_completed", None)
        emit = getattr(signal, "emit", None)
        if callable(emit):
            emit(payload)


def _messages_from_history(entries: list[ChatHistoryEntry]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for entry in entries[-MAX_MOBILE_HISTORY_MESSAGES:]:
        if entry.role not in {"user", "assistant"}:
            continue
        content = entry.content.strip()
        if entry.role == "assistant" and entry.translation.strip():
            content = f"{content}\n中文翻译：{entry.translation.strip()}"
        if content:
            messages.append({"role": entry.role, "content": content})
    return messages


def _mobile_image_message(text: str, image_data_url: str) -> ChatMessage:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text.strip() or "请看这张图片。"},
            {"type": "image_url", "image_url": {"url": image_data_url, "detail": "low"}},
        ],
    }


def _history_entry_for_mobile(entry: ChatHistoryEntry) -> dict[str, str]:
    return {
        "created_at": entry.created_at,
        "role": entry.role,
        "content": entry.display_content("zh"),
        "raw_content": entry.content,
        "translation": entry.translation,
    }


def _segment_for_mobile(segment: Any) -> dict[str, str]:
    return {
        "content": segment.display_text("zh"),
        "raw_content": segment.text,
        "translation": segment.translation,
        "tone": segment.tone,
        "portrait": segment.portrait,
    }
