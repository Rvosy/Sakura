from __future__ import annotations

from typing import Any, Callable

from app.llm.chat_reply import ChatSegment
from app.core.debug_log import debug_log
from app.voice.text_language_guard import should_skip_tts_text
from app.voice.tts import TTSPreparedAudio, TTSProvider


LogStageCallback = Callable[[str, dict[str, Any] | None], None]
TTSCallback = Callable[[], None]
TTSErrorCallback = Callable[[str], None]
TTSSegmentCallback = Callable[[ChatSegment, int], None]


class VoicePlaybackController:
    """管理回复分段对应的 TTS 播放和下一段预生成。"""

    def __init__(
        self,
        tts_provider: TTSProvider,
        log_stage: LogStageCallback,
        target_text_lang_getter: Callable[[], str] | None = None,
        on_error: TTSErrorCallback | None = None,
        on_tts_started: TTSSegmentCallback | None = None,
        on_tts_finished: TTSSegmentCallback | None = None,
    ) -> None:
        self.tts_provider = tts_provider
        self._log_stage = log_stage
        self._target_text_lang_getter = target_text_lang_getter or (lambda: "ja")
        self._on_error = on_error
        self._on_tts_started = on_tts_started
        self._on_tts_finished = on_tts_finished
        self._prepared_next_segment: ChatSegment | None = None
        self._prepared_next_tts: TTSPreparedAudio | None = None

    def set_provider(self, tts_provider: TTSProvider) -> None:
        self.discard_prepared()
        self.tts_provider = tts_provider

    def speak_segment(
        self,
        segment: ChatSegment,
        sequence_id: int,
        on_started: TTSCallback,
        on_finished: TTSCallback,
    ) -> None:
        prepared_tts = self._take_prepared_tts_for_segment(segment)
        try:
            if prepared_tts is None and self._should_skip_segment_tts(segment):
                self._log_tts_skipped(segment, sequence_id, "speak")
                on_started()
                on_finished()
                return

            if prepared_tts is None:
                self._log_stage(
                    "tts_speak_requested",
                    {"sequence_id": sequence_id, "tone": segment.tone},
                )
                self.tts_provider.speak(
                    segment.text,
                    segment.tone,
                    on_finished=self._wrap_tts_finished(segment, sequence_id, on_finished),
                    on_started=self._wrap_tts_started(segment, sequence_id, on_started),
                )
                return

            self._log_stage(
                "tts_prepared_speak_requested",
                {"sequence_id": sequence_id, "tone": segment.tone},
            )
            self.tts_provider.speak_prepared(
                prepared_tts,
                on_started=self._wrap_tts_started(segment, sequence_id, on_started),
                on_finished=self._wrap_tts_finished(segment, sequence_id, on_finished),
            )
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "TTS",
                "播放控制器捕获 TTS 异常，回退为仅显示字幕",
                {
                    "text": segment.text,
                    "tone": segment.tone,
                    "error": str(exc),
                },
            )
            debug_log("TTS", "播放失败，已继续显示字幕", {"error": str(exc)})
            self._notify_error(f"播放失败，已继续显示字幕：{exc}")
            on_started()
            on_finished()

    def _wrap_tts_started(
        self,
        segment: ChatSegment,
        sequence_id: int,
        callback: TTSCallback,
    ) -> TTSCallback:
        def wrapped() -> None:
            self._notify_tts_started(segment, sequence_id)
            callback()

        return wrapped

    def _wrap_tts_finished(
        self,
        segment: ChatSegment,
        sequence_id: int,
        callback: TTSCallback,
    ) -> TTSCallback:
        def wrapped() -> None:
            self._notify_tts_finished(segment, sequence_id)
            callback()

        return wrapped

    def prepare_next(self, next_segment: ChatSegment | None) -> None:
        if next_segment is None:
            self.discard_prepared()
            return
        if self._prepared_next_segment is next_segment and self._prepared_next_tts is not None:
            return

        self.discard_prepared()
        if self._should_skip_segment_tts(next_segment):
            self._log_tts_skipped(next_segment, None, "prepare")
            return

        self._prepared_next_segment = next_segment
        self._log_stage(
            "next_segment_tts_prepare_requested",
            {
                "text": next_segment.text,
                "tone": next_segment.tone,
                "portrait": next_segment.portrait,
            },
        )
        debug_log(
            "PetWindow",
            "预生成下一段 TTS",
            {
                "text": next_segment.text,
                "tone": next_segment.tone,
                "portrait": next_segment.portrait,
            },
        )
        try:
            self._prepared_next_tts = self.tts_provider.prepare(
                next_segment.text,
                next_segment.tone,
            )
        except Exception as exc:  # noqa: BLE001
            self._prepared_next_segment = None
            self._prepared_next_tts = None
            debug_log(
                "TTS",
                "预生成下一段 TTS 失败，后续将即时播放或仅显示字幕",
                {
                    "text": next_segment.text,
                    "tone": next_segment.tone,
                    "error": str(exc),
                },
            )
            debug_log("TTS", "预生成失败，已继续字幕流程", {"error": str(exc)})
            self._notify_error(f"预生成失败，已继续字幕流程：{exc}")

    def discard_prepared(self) -> None:
        if self._prepared_next_tts is not None:
            self.tts_provider.discard_prepared(self._prepared_next_tts)
        self._prepared_next_segment = None
        self._prepared_next_tts = None

    def _take_prepared_tts_for_segment(
        self,
        segment: ChatSegment,
    ) -> TTSPreparedAudio | None:
        if self._prepared_next_segment is not segment:
            return None

        prepared_tts = self._prepared_next_tts
        self._prepared_next_segment = None
        self._prepared_next_tts = None
        return prepared_tts

    def _target_text_lang(self) -> str:
        try:
            return self._target_text_lang_getter()
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "读取目标 TTS 文本语言失败，回退为 ja", {"error": str(exc)})
            return "ja"

    def _should_skip_segment_tts(self, segment: ChatSegment) -> bool:
        if segment.suppress_tts:
            return True
        return should_skip_tts_text(segment.text, self._target_text_lang())

    def _log_tts_skipped(
        self,
        segment: ChatSegment,
        sequence_id: int | None,
        phase: str,
    ) -> None:
        payload = {
            "sequence_id": sequence_id,
            "phase": phase,
            "text": segment.text,
            "tone": segment.tone,
            "target_lang": self._target_text_lang(),
        }
        self._log_stage("tts_skipped_language_guard", payload)
        debug_log("TTS", "语言守卫跳过异常文本 TTS", payload)

    def _notify_error(self, message: str) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(message)
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "TTS 错误提示回调失败", {"error": str(exc)})

    def _notify_tts_started(self, segment: ChatSegment, sequence_id: int) -> None:
        if self._on_tts_started is None:
            return
        try:
            self._on_tts_started(segment, sequence_id)
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "TTS start hook 回调失败", {"error": str(exc)})

    def _notify_tts_finished(self, segment: ChatSegment, sequence_id: int) -> None:
        if self._on_tts_finished is None:
            return
        try:
            self._on_tts_finished(segment, sequence_id)
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "TTS end hook 回调失败", {"error": str(exc)})
