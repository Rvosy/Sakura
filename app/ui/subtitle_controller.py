from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
    Slot,
)
from PySide6.QtWidgets import QLabel

from app.llm.chat_reply import ChatSegment
from app.core.debug_log import debug_log
from app.voice import VoicePlaybackController

# 字幕逐字显示速度。
SPEECH_TYPING_INTERVAL_MS = 35
# 分段回复之间的默认停顿时间。
REPLY_SEGMENT_PAUSE_MS = 100
# 等待模型回复时的点状动效。
WAITING_INDICATOR_INTERVAL_MS = 360
WAITING_INDICATOR_FRAMES = (".", "..", "...", "....", ".....", "......", ".....")
SUBTITLE_TYPING_INTERVAL_MIN_MS = 5
SUBTITLE_TYPING_INTERVAL_MAX_MS = 200
REPLY_SEGMENT_PAUSE_MIN_MS = 0
REPLY_SEGMENT_PAUSE_MAX_MS = 3000

LogStageCallback = Callable[[str, dict[str, Any] | None], None]
SegmentCallback = Callable[[ChatSegment], None]


class SubtitleController(QObject):
    """管理回复分段、字幕语言切换和打字机展示流程。"""

    def __init__(
        self,
        speech_label: QLabel,
        voice_playback: VoicePlaybackController,
        subtitle_language: str,
        log_stage: LogStageCallback,
        apply_segment: SegmentCallback,
        on_reply_completed: Callable[[], None],
        should_complete_reply: Callable[[], bool],
        parent: QObject | None = None,
        preload_segment: SegmentCallback | None = None,
        typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
        segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
        bubble_opacity_effect: Any = None,
        on_typing_overflow: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.speech_label = speech_label
        self.voice_playback = voice_playback
        self.subtitle_language = subtitle_language
        self._log_stage = log_stage
        self._apply_segment = apply_segment
        self._on_reply_completed = on_reply_completed
        self._should_complete_reply = should_complete_reply
        self._preload_segment = preload_segment
        # 气泡淡入脉冲：用于每段台词浮现，None 时退化为无动画（测试/历史窗口单独构造安全）。
        self._bubble_opacity_effect = bubble_opacity_effect
        self._bubble_fade_anim: QSequentialAnimationGroup | None = None
        # 打字机溢出回调：标签高度增大时通知调用方按需扩展气泡（None 时不触发）。
        self._on_typing_overflow = on_typing_overflow
        self._last_label_height: int = 0

        self.speech_text = ""
        self.speech_index = 0
        self.pending_reply_segments: list[ChatSegment] = []
        self.queued_reply_segment_batches: list[list[ChatSegment]] = []
        self.current_segment: ChatSegment | None = None
        self.reply_sequence_id = 0
        self.reply_advance_token = 0
        self.current_segment_sequence_id: int | None = None
        self.current_segment_speech_done = False
        self.current_segment_tts_done = True
        self.reply_advance_scheduled = False
        self.waiting_indicator_active = False
        self.waiting_indicator_index = 0
        self.typing_interval_ms, self.segment_pause_ms = normalize_subtitle_display_speed(
            typing_interval_ms,
            segment_pause_ms,
        )

        self.speech_timer = QTimer(self)
        self.speech_timer.setInterval(self.typing_interval_ms)
        self.speech_timer.timeout.connect(self._show_next_speech_char)
        self.waiting_indicator_timer = QTimer(self)
        self.waiting_indicator_timer.setInterval(WAITING_INDICATOR_INTERVAL_MS)
        self.waiting_indicator_timer.timeout.connect(self._show_next_waiting_indicator_frame)

    def set_display_speed(self, typing_interval_ms: int, segment_pause_ms: int) -> None:
        """更新字幕逐字间隔和分段停顿，后续显示流程立即使用新配置。"""
        self.typing_interval_ms, self.segment_pause_ms = normalize_subtitle_display_speed(
            typing_interval_ms,
            segment_pause_ms,
        )
        self.speech_timer.setInterval(self.typing_interval_ms)

    def show_segments(self, segments: list[ChatSegment]) -> None:
        clean_segments = [segment for segment in segments if segment.text.strip()]
        if not clean_segments:
            self.stop_waiting_indicator()
            return
        if self._reply_segments_active():
            self.queued_reply_segment_batches.append(clean_segments)
            self._log_stage(
                "reply_segments_queued",
                {
                    "queued_batch_count": len(self.queued_reply_segment_batches),
                    "segment_count": len(clean_segments),
                },
            )
            debug_log(
                "PetWindow",
                "当前回复未播完，后续分段已排队",
                {
                    "queued_batch_count": len(self.queued_reply_segment_batches),
                    "segments": [_segment_debug_payload(segment) for segment in clean_segments],
                },
            )
            return

        self._start_reply_segments_now(clean_segments)

    def start_waiting_indicator(self) -> None:
        """显示模型回复等待动效；收到真实回复或错误时由后续流程停止。"""
        self.reply_sequence_id += 1
        self.pending_reply_segments = []
        self.queued_reply_segment_batches = []
        self.reset_current_segment_progress()
        self.speech_timer.stop()
        self.speech_text = ""
        self.speech_index = 0
        self._last_label_height = 0
        self.waiting_indicator_active = True
        self.waiting_indicator_index = 0
        self._show_waiting_indicator_frame()
        self.waiting_indicator_timer.start()
        self._log_stage("waiting_indicator_started", None)

    def stop_waiting_indicator(self) -> None:
        """停止等待动效；若动效未启动则安全空操作。"""
        if not self.waiting_indicator_active and not self.waiting_indicator_timer.isActive():
            return
        self.waiting_indicator_active = False
        self.waiting_indicator_timer.stop()
        self._log_stage("waiting_indicator_stopped", None)

    def cancel_reply_flow(
        self,
        placeholder_text: str | None = None,
        *,
        transition: bool = False,
    ) -> None:
        self.stop_waiting_indicator()
        self.reply_sequence_id += 1
        self.pending_reply_segments = []
        self.queued_reply_segment_batches = []
        self.reset_current_segment_progress()
        if placeholder_text is not None:
            # transition=True 用于真正打断当前台词的场景（如通信失败），让新文本带轻微浮现；
            # 发消息占位等高频路径用默认 False 瞬时切换，避免气泡频繁闪烁。
            self.set_speech(placeholder_text, pulse=transition)

    def clear_queued_reply_segments_for_action_resolution(self) -> None:
        if not self.queued_reply_segment_batches:
            return
        cleared_count = len(self.queued_reply_segment_batches)
        self.queued_reply_segment_batches = []
        self._log_stage(
            "queued_reply_segments_cleared_for_action",
            {"cleared_batch_count": cleared_count},
        )
        debug_log(
            "PetWindow",
            "已清理待确认动作相关的排队回复",
            {"cleared_batch_count": cleared_count},
        )

    def is_reply_sequence_active(self) -> bool:
        if self.waiting_indicator_active:
            return True
        return self._reply_segments_active()

    def _reply_segments_active(self) -> bool:
        if self.pending_reply_segments or self.reply_advance_scheduled:
            return True
        return self.current_segment_in_progress()

    def current_segment_in_progress(self) -> bool:
        return (
            self.current_segment_sequence_id is not None
            and (not self.current_segment_speech_done or not self.current_segment_tts_done)
        )

    def set_subtitle_language(self, subtitle_language: str) -> None:
        self.subtitle_language = subtitle_language

    @Slot(str)
    def set_speech(self, text: str, *, pulse: bool = False) -> None:
        self.stop_waiting_indicator()
        cleaned = " ".join(text.split())
        self.speech_timer.stop()
        self.speech_text = cleaned
        self.speech_index = 0
        self._last_label_height = 0
        self.speech_label.clear()
        if self.speech_text:
            self.speech_timer.start()
            if pulse:
                self._pulse_bubble()
        self._log_stage("speech_text_started", {"text": cleaned})

    def _pulse_bubble(self) -> None:
        """每段台词开始时让气泡做一次轻微"暗-亮"脉冲，营造浮现感（克制、不闪黑）。"""
        effect = self._bubble_opacity_effect
        if effect is None:
            return
        previous = self._bubble_fade_anim
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        # fade_out 不设 startValue，从当前透明度起步，连续切换永不突跳。
        fade_out = QPropertyAnimation(effect, b"opacity")
        fade_out.setDuration(110)
        fade_out.setEndValue(0.5)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)
        fade_in = QPropertyAnimation(effect, b"opacity")
        fade_in.setDuration(130)
        fade_in.setStartValue(0.5)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutQuad)
        group = QSequentialAnimationGroup(self)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)
        group.start()
        self._bubble_fade_anim = group

    def show_text_immediately(self, text: str) -> None:
        """立即显示完整字幕，用于历史回看，不触发 TTS 或分段推进。"""
        self.stop_waiting_indicator()
        cleaned = " ".join(text.split())
        self.speech_timer.stop()
        self.speech_text = cleaned
        self.speech_index = len(cleaned)
        self.speech_label.setText(cleaned)
        self._log_stage("speech_text_shown_immediately", {"text": cleaned})

    def restart_current_segment_speech(self) -> None:
        if self.current_segment_sequence_id is None or self.current_segment is None:
            return

        self.reply_advance_token += 1
        self.current_segment_speech_done = False
        self.reply_advance_scheduled = False
        self.set_speech(self.current_segment.display_text(self.subtitle_language), pulse=True)

    def reset_current_segment_progress(self) -> None:
        self.voice_playback.discard_prepared()
        self.current_segment = None
        self.reply_advance_token += 1
        self.current_segment_sequence_id = None
        self.current_segment_speech_done = False
        self.current_segment_tts_done = True
        self.reply_advance_scheduled = False

    def _start_reply_segments_now(self, segments: list[ChatSegment]) -> None:
        self.reply_sequence_id += 1
        self.pending_reply_segments = segments
        self._log_stage(
            "reply_segments_ready",
            {
                "sequence_id": self.reply_sequence_id,
                "segment_count": len(self.pending_reply_segments),
            },
        )
        debug_log(
            "PetWindow",
            "准备分段展示回复",
            {
                "sequence_id": self.reply_sequence_id,
                "segments": [_segment_debug_payload(segment) for segment in self.pending_reply_segments],
            },
        )
        self.reset_current_segment_progress()
        self._show_next_reply_segment(self.reply_sequence_id)

    def _show_next_reply_segment(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or not self.pending_reply_segments:
            return

        segment = self.pending_reply_segments.pop(0)
        debug_log(
            "PetWindow",
            "展示下一段回复",
            {
                "sequence_id": sequence_id,
                "text": segment.text,
                "tone": segment.tone,
                "portrait": segment.portrait,
                "remaining_segments": len(self.pending_reply_segments),
            },
        )
        self.current_segment = segment
        self.current_segment_sequence_id = sequence_id
        self.current_segment_speech_done = False
        self.current_segment_tts_done = False
        self.reply_advance_scheduled = False
        if self._preload_segment is not None:
            self._preload_segment(segment)
        self.voice_playback.speak_segment(
            segment,
            sequence_id,
            on_started=lambda: self._start_segment_speech(sequence_id),
            on_finished=lambda: self._mark_segment_tts_done(sequence_id),
        )
        self.voice_playback.prepare_next(
            self.pending_reply_segments[0] if self.pending_reply_segments else None
        )

    def _start_segment_speech(self, sequence_id: int) -> None:
        if (
            sequence_id != self.reply_sequence_id
            or sequence_id != self.current_segment_sequence_id
            or self.current_segment is None
        ):
            return
        self._log_stage(
            "segment_speech_started",
            {
                "sequence_id": sequence_id,
                "tone": self.current_segment.tone,
                "portrait": self.current_segment.portrait,
            },
        )
        self._apply_segment(self.current_segment)
        self.set_speech(self.current_segment.display_text(self.subtitle_language), pulse=True)

    def _mark_segment_speech_done(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or sequence_id != self.current_segment_sequence_id:
            return
        self.current_segment_speech_done = True
        self._log_stage("segment_text_render_done", {"sequence_id": sequence_id})
        self._end_interaction_if_reply_done()
        self._schedule_next_reply_segment_if_ready(sequence_id)

    def _mark_segment_tts_done(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or sequence_id != self.current_segment_sequence_id:
            return
        self.current_segment_tts_done = True
        self._log_stage("segment_tts_done", {"sequence_id": sequence_id})
        self._end_interaction_if_reply_done()
        self._schedule_next_reply_segment_if_ready(sequence_id)

    def _schedule_next_reply_segment_if_ready(self, sequence_id: int) -> None:
        if (
            sequence_id != self.reply_sequence_id
            or sequence_id != self.current_segment_sequence_id
            or self.reply_advance_scheduled
            or not self.current_segment_speech_done
            or not self.current_segment_tts_done
            or not self.pending_reply_segments
        ):
            return

        self.reply_advance_scheduled = True
        self.reply_advance_token += 1
        reply_advance_token = self.reply_advance_token
        self._log_stage(
            "next_segment_scheduled",
            {
                "sequence_id": sequence_id,
                "delay_ms": self.segment_pause_ms,
                "remaining_segments": len(self.pending_reply_segments),
            },
        )
        QTimer.singleShot(
            self.segment_pause_ms,
            lambda: self._show_scheduled_next_reply_segment(sequence_id, reply_advance_token),
        )

    def _show_scheduled_next_reply_segment(self, sequence_id: int, reply_advance_token: int) -> None:
        if reply_advance_token != self.reply_advance_token:
            return
        self._log_stage("next_segment_timer_fired", {"sequence_id": sequence_id})
        self._show_next_reply_segment(sequence_id)

    def _end_interaction_if_reply_done(self) -> None:
        if (
            self._should_complete_reply()
            and self.current_segment_speech_done
            and self.current_segment_tts_done
            and not self.pending_reply_segments
        ):
            if self.queued_reply_segment_batches:
                self._show_next_queued_reply_batch()
                return
            self._on_reply_completed()

    def _show_next_queued_reply_batch(self) -> None:
        if not self.queued_reply_segment_batches:
            return
        next_segments = self.queued_reply_segment_batches.pop(0)
        self._log_stage(
            "queued_reply_segments_dequeued",
            {
                "remaining_batch_count": len(self.queued_reply_segment_batches),
                "segment_count": len(next_segments),
            },
        )
        self._start_reply_segments_now(next_segments)

    @Slot()
    def _show_next_speech_char(self) -> None:
        if self.speech_index >= len(self.speech_text):
            self.speech_timer.stop()
            return

        self.speech_index += 1
        self.speech_label.setText(self.speech_text[: self.speech_index])

        if self._on_typing_overflow is not None:
            w = self.speech_label.width()
            if w > 0:
                h = self.speech_label.heightForWidth(w)
                if h > 0 and h > self._last_label_height:
                    self._last_label_height = h
                    self._on_typing_overflow(h)

        if self.speech_index >= len(self.speech_text):
            self.speech_timer.stop()
            if self.current_segment_sequence_id is not None:
                self._mark_segment_speech_done(self.current_segment_sequence_id)

    def _show_waiting_indicator_frame(self) -> None:
        if not self.waiting_indicator_active:
            return
        self.speech_label.setText(WAITING_INDICATOR_FRAMES[self.waiting_indicator_index])

    @Slot()
    def _show_next_waiting_indicator_frame(self) -> None:
        if not self.waiting_indicator_active:
            self.waiting_indicator_timer.stop()
            return
        if self.waiting_indicator_index < len(WAITING_INDICATOR_FRAMES) - 1:
            self.waiting_indicator_index += 1
        else:
            self.waiting_indicator_index = len(WAITING_INDICATOR_FRAMES) - 2
        self._show_waiting_indicator_frame()


def _segment_debug_payload(segment: ChatSegment) -> dict[str, str]:
    return {
        "text": segment.text,
        "tone": segment.tone,
        "portrait": segment.portrait,
        "translation": segment.translation,
    }


def normalize_subtitle_display_speed(
    typing_interval_ms: Any,
    segment_pause_ms: Any,
) -> tuple[int, int]:
    return (
        _clamped_int_value(
            typing_interval_ms,
            SPEECH_TYPING_INTERVAL_MS,
            SUBTITLE_TYPING_INTERVAL_MIN_MS,
            SUBTITLE_TYPING_INTERVAL_MAX_MS,
        ),
        _clamped_int_value(
            segment_pause_ms,
            REPLY_SEGMENT_PAUSE_MS,
            REPLY_SEGMENT_PAUSE_MIN_MS,
            REPLY_SEGMENT_PAUSE_MAX_MS,
        ),
    )


def _clamped_int_value(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
