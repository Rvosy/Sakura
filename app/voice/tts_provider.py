"""不依赖 Qt 的 TTS Provider 契约与静音实现。"""

from __future__ import annotations

from typing import Protocol

from app.core.runtime_log import log_event
from app.voice.tts_types import TTSCallback, TTSPreparedAudio


class TTSProvider(Protocol):
    @property
    def service_ready(self) -> bool: ...

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None: ...

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio: ...

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None: ...

    def discard_prepared(self, handle: TTSPreparedAudio) -> None: ...

    def cancel_playback(self) -> None: ...

    def ensure_ready(self) -> tuple[bool, str]: ...

    def close(self) -> None: ...


class NullTTSProvider:
    @property
    def service_ready(self) -> bool:
        return False

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        log_event("TTS", "静音 Provider 跳过播放", {"text": text, "tone": tone})
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        log_event("TTS", "静音 Provider 跳过预生成", {"text": text, "tone": tone})
        return TTSPreparedAudio(text=text.strip(), tone=tone)

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        log_event("TTS", "静音 Provider 跳过预生成播放", {"text": handle.text, "tone": handle.tone})
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        log_event("TTS", "丢弃静音预生成句柄", {"text": handle.text, "tone": handle.tone})
        handle.cancelled = True

    def cancel_playback(self) -> None:
        return

    def ensure_ready(self) -> tuple[bool, str]:
        log_event("TTS", "静音 Provider 跳过服务检测")
        return True, "TTS 已关闭。"

    def close(self) -> None:
        log_event("TTS", "静音 Provider 无需关闭")
