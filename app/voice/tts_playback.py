"""TTS 播放端点（issue #94 第 3 阶段）。

从 ``app/voice/tts.py`` 抽出「播放端点」这一职责：QMediaPlayer / AudioSinkPlayer 的
播放、播放完成回调、fallback timeout 兜底、临时 wav 清理，以及合成结果入队的
Qt 信号/slot。

线程域 = MAIN_THREAD_ONLY：本端点是协调器的子 QObject（parented），随协调器一起
``moveToThread`` 到 UI 线程，QMediaPlayer/QAudioOutput 绝不移出主线程。合成线程
（daemon）经 sink（``deliver_audio``/``deliver_prepared`` → ``_audio_ready`` 等信号）把
结果以 queued 连接投回本端点所在的 UI 线程。``error_occurred`` 由协调器再 re-emit
给 PetWindow。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot

try:
    import shiboken6
except ImportError:  # pragma: no cover - 仅供无真实 PySide6 的最小测试桩环境
    shiboken6 = None  # type: ignore[assignment]

from app.core.runtime_log import log_event
from app.voice import audio_checks as _audio_checks
from app.voice.tts_types import TTSCallback, TTSPreparedAudio, _provider_is_closed

if TYPE_CHECKING:
    from PySide6.QtMultimedia import QAudioOutput as QAudioOutputType
    from PySide6.QtMultimedia import QMediaPlayer as QMediaPlayerType

    from app.voice.audio_sink_player import AudioSinkPlayer

QAudioOutput: type[Any] | None = None
QMediaPlayer: type[Any] | None = None

_AUDIO_CLEANUP_DELAY_MS = 5000
_AUDIO_CLEANUP_MAX_ATTEMPTS = 5
_AUDIO_FINISH_FALLBACK_GRACE_MS = 1500
_AUDIO_FINISH_FALLBACK_MIN_MS = 2000
# 时长意外无法解析时的保守兜底，防止流程永久挂起
_AUDIO_FINISH_FALLBACK_UNKNOWN_MS = 60_000

def _load_qt_multimedia() -> tuple[type[Any], type[Any]]:
    global QAudioOutput, QMediaPlayer
    if QAudioOutput is None or QMediaPlayer is None:
        from PySide6.QtMultimedia import QAudioOutput as _QAudioOutput
        from PySide6.QtMultimedia import QMediaPlayer as _QMediaPlayer

        QAudioOutput = _QAudioOutput
        QMediaPlayer = _QMediaPlayer
    return QAudioOutput, QMediaPlayer

def _create_audio_sink_player(parent: QObject) -> "AudioSinkPlayer":
    from app.voice.audio_sink_player import AudioSinkPlayer

    return AudioSinkPlayer(parent)

class TTSPlaybackEndpoint(QObject):
    """UI 主线程播放端点：持有播放器/队列状态，把合成音频依次播完并触发回调。"""

    error_occurred = Signal(str)
    _audio_ready = Signal(str, object, object, str)
    _prepared_audio_ready = Signal(object, str)
    _prepared_audio_failed = Signal(object, str)
    _prepared_audio_skipped = Signal(object)
    _cleanup_requested = Signal(object)
    _failed = Signal(str)
    _started = Signal(object)
    _finished = Signal(object)

    def __init__(
        self,
        parent: QObject,
        *,
        cache_dir: Path,
        is_closed,
    ) -> None:
        super().__init__(parent)
        self._tts_cache_dir = cache_dir
        # _provider_is_closed(self) 经此回调读取协调器关闭状态
        self._is_closed = is_closed
        # close() 第一阶段只改 Python 状态，后台线程从此不再触碰 Qt 信号。
        self._accepting_results = True
        # 队列元素：(音频路径, 开始回调, 完成回调, 预生成句柄, 合成文本)
        self._pending_audio: list[
            tuple[Path, TTSCallback | None, TTSCallback | None, TTSPreparedAudio | None, str]
        ] = []
        self._current_audio: Path | None = None
        # 当前正在播放的音频对应的合成文本，仅用于日志展示
        self._current_text: str = ""
        self._current_started: TTSCallback | None = None
        self._current_finished: TTSCallback | None = None
        self._current_started_emitted = False
        self._finishing_audio = False
        self._playback_finish_token = 0
        self._sink_player: "AudioSinkPlayer | None" = None
        self._audio_output: "QAudioOutputType | None" = None
        self._player: "QMediaPlayerType | None" = None
        self._audio_ready.connect(self._enqueue_audio)
        self._prepared_audio_ready.connect(self._store_prepared_audio)
        self._prepared_audio_failed.connect(self._fail_prepared_audio)
        self._prepared_audio_skipped.connect(self._skip_prepared_audio)
        self._cleanup_requested.connect(self._schedule_synthesis_cleanup)
        self._failed.connect(self._log_error)
        self._started.connect(self._run_callback)
        self._finished.connect(self._run_callback)

    def begin_shutdown(self) -> None:
        """封闭合成结果入口；本方法不调用 Qt API，可在等待后台线程前执行。"""
        self._accepting_results = False

    def shutdown(self) -> None:
        """关闭时清空播放队列、收尾当前音频并释放播放器（不杀进程/线程）。"""
        self.begin_shutdown()
        self._clear_pending_audio()
        if self._current_audio is not None:
            self._finish_current_audio("provider_closed")
        self._release_sink_player()
        self._release_player_source()

    def cancel_playback(self) -> None:
        self._clear_pending_audio()
        if self._current_audio is not None:
            self._finish_current_audio("cancelled")

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        """从播放队列移除指定预生成句柄并清理其临时音频。"""
        pending_audio: list[
            tuple[Path, TTSCallback | None, TTSCallback | None, TTSPreparedAudio | None, str]
        ] = []
        for audio_path, on_started, on_finished, prepared_audio, text in self._pending_audio:
            if prepared_audio is handle:
                self._schedule_audio_cleanup(audio_path)
                continue
            pending_audio.append((audio_path, on_started, on_finished, prepared_audio, text))
        self._pending_audio = pending_audio

        if handle.audio_path is not None:
            self._schedule_audio_cleanup(handle.audio_path)
            handle.audio_path = None

    def run_callbacks(self, on_started: TTSCallback | None, on_finished: TTSCallback | None) -> None:
        """把一对 started/finished 回调 marshal 回 UI 线程执行（空文本等直通场景）。"""
        self._started.emit(on_started)
        self._finished.emit(on_finished)

    @Slot(str, object, object, str)
    def _enqueue_audio(
        self,
        audio_path: str,
        on_started: TTSCallback | None,
        on_finished: TTSCallback | None,
        text: str = "",
    ) -> None:
        if _provider_is_closed(self):
            path = Path(audio_path)
            log_event("TTS", "Provider 已关闭，清理迟到音频", {"audio_path": path, "text": text})
            self._discard_late_audio(path)
            return
        self._pending_audio.append((Path(audio_path), on_started, on_finished, None, text))
        if self._current_audio is None:
            QTimer.singleShot(0, self._play_next)

    @Slot(object, str)
    def _store_prepared_audio(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        path = Path(audio_path)
        if _provider_is_closed(self):
            handle.failed = True
            log_event("TTS", "Provider 已关闭，清理迟到的预生成音频", {"audio_path": path})
            self._discard_late_audio(path)
            return
        if handle.cancelled:
            log_event("TTS", "预生成音频已取消，清理文件", {"audio_path": path})
            self._schedule_audio_cleanup(path)
            return
        handle.audio_path = path
        log_event(
            "TTS",
            "预生成音频已就绪",
            {
                "text": handle.text,
                "tone": handle.tone,
                "audio_path": path,
                "play_requested": handle.play_requested,
            },
        )
        if handle.play_requested:
            self._enqueue_prepared_audio(handle)

    @Slot(object, str)
    def _fail_prepared_audio(self, handle: TTSPreparedAudio, message: str) -> None:
        if _provider_is_closed(self):
            handle.failed = True
            return
        self._log_error(message)
        handle.failed = True
        if handle.cancelled or not handle.play_requested:
            return
        self._started.emit(handle.on_started)
        self._finished.emit(handle.on_finished)
        handle.on_started = None
        handle.on_finished = None

    @Slot(object)
    def _skip_prepared_audio(self, handle: TTSPreparedAudio) -> None:
        """预生成句柄静默失败：标记 failed 并完成回调，但不触发 error_occurred。

        与 _fail_prepared_audio 的唯一区别是不调用 _log_error，因此不会向 UI 报错。
        """
        handle.failed = True
        if _provider_is_closed(self):
            return
        if handle.cancelled or not handle.play_requested:
            return
        self._started.emit(handle.on_started)
        self._finished.emit(handle.on_finished)
        handle.on_started = None
        handle.on_finished = None

    @Slot(object)
    def _handle_media_status(self, status: object) -> None:
        _QAudioOutput, QMediaPlayer = _load_qt_multimedia()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish_current_audio("end_of_media")
            self._play_next()

    @Slot(object)
    def _handle_playback_state(self, state: object) -> None:
        _QAudioOutput, QMediaPlayer = _load_qt_multimedia()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._emit_current_started()
            return
        if (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self._current_audio is not None
            and self._current_started_emitted
        ):
            log_event(
                "TTS",
                "播放器停止，按当前音频播放完成处理",
                {"audio_path": str(self._current_audio)},
            )
            self._finish_current_audio("stopped_state")
            self._play_next()

    @Slot(object, str)
    def _handle_player_error(self, _error: object, error_text: str) -> None:
        log_event(
            "TTS",
            "播放器错误",
            {
                "error": error_text,
                "audio_path": str(self._current_audio) if self._current_audio else "",
                "pending_audio": len(self._pending_audio),
            },
        )
        self._log_error(f"音频播放失败：{error_text}")
        self._finish_current_audio("player_error")
        self._play_next()

    @Slot(str)
    def _log_error(self, message: str) -> None:
        if _provider_is_closed(self):
            return
        log_event("TTS", "错误通知", {"message": message})
        self.error_occurred.emit(message)

    @Slot(object)
    def _run_callback(self, callback: TTSCallback | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"TTS 回调执行失败：{exc}")

    def _fail_request(
        self,
        message: str,
        on_started: TTSCallback | None,
        on_finished: TTSCallback | None,
    ) -> None:
        self._failed.emit(message)
        log_event("TTS", "音频请求失败", {"message": message})
        self._started.emit(on_started)
        self._finished.emit(on_finished)

    def fail_audio_request(self, request: _TTSRequest, message: str) -> None:
        if not self._can_accept_synthesis_result():
            if request.prepared_audio is not None:
                request.prepared_audio.failed = True
            log_event("TTS", "Provider 已关闭，忽略音频请求失败通知", {"message": message})
            return
        if request.prepared_audio is None:
            self._fail_request(message, request.on_started, request.on_finished)
            return
        self._prepared_audio_failed.emit(request.prepared_audio, message)

    def skip_audio_request(self, request: _TTSRequest, reason: str) -> None:
        """本段无需/无法发音但不算故障：正常走完回调让流程推进，不向 UI 报错。

        与 fail_audio_request 相比，不 emit _failed/error_occurred，只记 debug；
        用于纯标点段（无可发音内容）与服务端单段 tts failed 的优雅降级。
        """
        if not self._can_accept_synthesis_result():
            if request.prepared_audio is not None:
                request.prepared_audio.failed = True
            return
        log_event("TTS", "跳过本段合成", {"text": request.text, "reason": reason})
        if request.prepared_audio is None:
            self._started.emit(request.on_started)
            self._finished.emit(request.on_finished)
            return
        self._prepared_audio_skipped.emit(request.prepared_audio)

    def _enqueue_prepared_audio(self, handle: TTSPreparedAudio) -> None:
        if _provider_is_closed(self):
            if handle.audio_path is not None:
                self._schedule_audio_cleanup(handle.audio_path)
                handle.audio_path = None
            handle.failed = True
            return
        if handle.cancelled or handle.enqueued or handle.audio_path is None:
            return
        handle.enqueued = True
        self._pending_audio.append(
            (handle.audio_path, handle.on_started, handle.on_finished, handle, handle.text)
        )
        handle.audio_path = None
        if self._current_audio is None:
            QTimer.singleShot(0, self._play_next)

    def _play_next(self) -> None:
        """从播放队列取下一段音频并播放，根据后端配置分发。"""
        if _provider_is_closed(self):
            self._clear_pending_audio()
            return
        if self._current_audio is not None or not self._pending_audio:
            return
        (
            audio_path,
            on_started,
            on_finished,
            _prepared_audio,
            text,
        ) = self._pending_audio.pop(0)
        self._current_audio = audio_path
        self._current_text = text
        self._current_started = on_started
        self._current_finished = on_finished
        self._current_started_emitted = False
        self._playback_finish_token += 1

        log_event(
            "TTS",
            "开始播放音频",
            {
                "text": text,
                "audio_path": str(audio_path),
                "file_size": audio_path.stat().st_size if audio_path.exists() else 0,
                "pending_audio": len(self._pending_audio),
            },
        )

        # 播放前最后一道检查：文件可能在排队期间被清理/损坏；
        # 坏条目直接跳过并继续播放队列，绝不交给播放器去卡死
        audio_issue = _audio_checks._verify_generated_audio(audio_path)
        if audio_issue is not None:
            log_event(
                "TTS",
                "播放前音频校验失败，跳过该条目",
                {"audio_path": str(audio_path), "issue": audio_issue},
            )
            self._finish_current_audio("invalid_audio")
            self._play_next()
            return

        self._play_next_with_sink()

    def _play_next_with_media_player(self) -> None:
        """旧 QMediaPlayer 播放后端。"""
        audio_path = self._current_audio
        playback_finish_token = self._playback_finish_token
        if audio_path is None:
            return

        self._ensure_player()
        if self._player is None:
            self._fail_audio_playback("播放器初始化失败。")
            return

        self._player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self._player.play()
        self._schedule_current_audio_finish_fallback(
            audio_path,
            playback_finish_token,
        )

    def _play_next_with_sink(self) -> None:
        """QAudioSink 播放后端。"""
        audio_path = self._current_audio
        playback_finish_token = self._playback_finish_token
        if audio_path is None:
            return

        self._release_sink_player()

        self._sink_player = _create_audio_sink_player(self)
        self._sink_player.started.connect(self._on_sink_started)
        self._sink_player.finished.connect(self._on_sink_finished)
        self._sink_player.error.connect(self._on_sink_error)

        ok = self._sink_player.start(audio_path)
        if not ok:
            # sink 不支持此格式，fallback 到 QMediaPlayer
            log_event(
                "TTS",
                "AudioSink: fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "sink_start_returned_false",
                    "audio_path": str(audio_path),
                },
            )
            self._release_sink_player()
            self._play_next_with_media_player()
            return

        # sink 后端也设置兜底定时器（作为额外安全网）
        self._schedule_current_audio_finish_fallback(
            audio_path,
            playback_finish_token,
        )

    def _release_sink_player(self) -> None:
        player = self._sink_player
        self._sink_player = None
        if player is None:
            return
        for signal_name in ("finished", "started", "error"):
            signal = getattr(player, signal_name, None)
            disconnect = getattr(signal, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass
        try:
            player.stop()
        except Exception:
            pass
        delete_later = getattr(player, "deleteLater", None)
        if callable(delete_later):
            try:
                delete_later()
            except RuntimeError:
                pass

    @Slot()
    def _on_sink_started(self) -> None:
        """AudioSinkPlayer 开始播放回调。"""
        self._emit_current_started()

    @Slot(str, str)
    def _on_sink_finished(self, reason: str, audio_path_str: str) -> None:
        """AudioSinkPlayer 播放完成回调。"""
        finished_path = Path(audio_path_str)
        if self._current_audio != finished_path:
            log_event(
                "TTS",
                "忽略过期的 AudioSink 完成信号",
                {
                    "finished_audio": str(finished_path),
                    "current_audio": str(self._current_audio) if self._current_audio else "",
                    "reason": reason,
                },
            )
            return
        try:
            self._finish_current_audio(reason)
            self._play_next()
        except Exception as exc:
            log_event(
                "TTS",
                "AudioSink: 完成回调异常",
                {"error": str(exc), "exception_type": type(exc).__name__},
            )
            self._finish_current_audio("callback_error")
            self._play_next()

    @Slot(str)
    def _on_sink_error(self, message: str) -> None:
        """AudioSinkPlayer 播放错误回调。"""
        log_event(
            "TTS",
            "AudioSink: 播放错误回调",
            {"error": message, "audio_path": str(self._current_audio) if self._current_audio else ""},
        )
        self._log_error(message)
        try:
            self._finish_current_audio("sink_error")
            self._play_next()
        except Exception as exc:
            log_event(
                "TTS",
                "AudioSink: 错误回调异常",
                {"error": str(exc), "exception_type": type(exc).__name__},
            )
            self._finish_current_audio("callback_error")
            self._play_next()

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        QAudioOutput, QMediaPlayer = _load_qt_multimedia()
        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.mediaStatusChanged.connect(self._handle_media_status)
        self._player.playbackStateChanged.connect(self._handle_playback_state)
        self._player.errorOccurred.connect(self._handle_player_error)
        log_event("TTS", "Qt 多媒体播放器已初始化")

    def _fail_audio_playback(self, message: str) -> None:
        audio_path = self._current_audio
        on_started = self._current_started
        on_finished = self._current_finished
        self._reset_current_audio_state()
        if audio_path is not None:
            self._schedule_audio_cleanup(audio_path)
        self._log_error(message)
        self._started.emit(on_started)
        self._finished.emit(on_finished)

    def _emit_current_started(self) -> None:
        if self._current_started_emitted:
            return
        self._current_started_emitted = True
        self._started.emit(self._current_started)

    def _finish_current_audio(self, reason: str = "normal") -> None:
        """统一 finish 入口，保证幂等性。"""
        if self._finishing_audio:
            log_event(
                "TTS",
                "音频正在 finish 中，跳过重复调用",
                {"reason": reason, "audio_path": str(self._current_audio) if self._current_audio else ""},
            )
            return
        audio_path = self._current_audio
        on_finished = self._current_finished
        if audio_path is None:
            self._reset_current_audio_state()
            return
        self._finishing_audio = True
        try:
            log_event(
                "TTS",
                "音频播放完成",
                {
                    "text": self._current_text,
                    "reason": reason,
                    "audio_path": str(audio_path),
                    "pending_audio": len(self._pending_audio),
                },
            )
            self._emit_current_started()
            # 停止 sink player（如果正在使用）
            if self._sink_player is not None:
                try:
                    self._sink_player.stop()
                except Exception:
                    pass
                self._sink_player = None
            # 释放 QMediaPlayer（如果正在使用）
            self._release_player_source()
            self._reset_current_audio_state()
            self._schedule_audio_cleanup(audio_path)
            self._finished.emit(on_finished)
        finally:
            self._finishing_audio = False

    def _release_player_source(self) -> None:
        if self._player is None:
            return
        self._player.stop()
        self._player.setSource(QUrl())

    def _reset_current_audio_state(self) -> None:
        self._current_audio = None
        self._current_text = ""
        self._current_started = None
        self._current_finished = None
        self._current_started_emitted = False

    def _schedule_current_audio_finish_fallback(self, audio_path: Path, playback_finish_token: int) -> None:
        duration_ms = _audio_checks._wav_duration_ms(audio_path)
        if duration_ms is None:
            # 时长读不出（文件损坏/被占用）更要兜底——这是播放器最可能卡死的场景；
            # 用固定未知时长兜住，绝不能因解析失败而放弃兜底导致对话流程挂起
            log_event(
                "TTS",
                "无法读取音频时长，使用未知时长兜底",
                {"audio_path": audio_path, "delay_ms": _AUDIO_FINISH_FALLBACK_UNKNOWN_MS},
            )
        delay_ms = (
            _AUDIO_FINISH_FALLBACK_UNKNOWN_MS
            if duration_ms is None
            else max(
                _AUDIO_FINISH_FALLBACK_MIN_MS,
                duration_ms + _AUDIO_FINISH_FALLBACK_GRACE_MS,
            )
        )
        QTimer.singleShot(
            delay_ms,
            lambda path=audio_path, token=playback_finish_token: self._finish_current_audio_if_stalled(
                path,
                token,
            ),
        )

    def _finish_current_audio_if_stalled(self, audio_path: Path, playback_finish_token: int) -> None:
        if playback_finish_token != self._playback_finish_token or self._current_audio != audio_path:
            return
        if self._finishing_audio:
            log_event(
                "TTS",
                "音频播放完成兜底已过期，跳过",
                {
                    "audio_path": str(audio_path),
                    "token": playback_finish_token,
                },
            )
            return
        log_event(
            "TTS",
            "音频播放完成事件未触发，使用时长兜底完成",
            {
                "audio_path": str(audio_path),
                "token": playback_finish_token,
                "current_audio": str(self._current_audio) if self._current_audio else "",
            },
        )
        self._finish_current_audio("fallback_timeout")
        self._play_next()

    @Slot(object)
    def _schedule_synthesis_cleanup(self, audio_path: Path) -> None:
        if _provider_is_closed(self):
            self._discard_late_audio(Path(audio_path))
            return
        self._schedule_audio_cleanup(Path(audio_path))

    def _schedule_audio_cleanup(self, audio_path: Path, attempt: int = 1) -> None:
        QTimer.singleShot(
            _AUDIO_CLEANUP_DELAY_MS,
            lambda path=audio_path, current_attempt=attempt: self._cleanup_audio_file(
                path,
                current_attempt,
            ),
        )

    def _cleanup_audio_file(self, audio_path: Path, attempt: int) -> None:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError as exc:
            if attempt < _AUDIO_CLEANUP_MAX_ATTEMPTS:
                self._schedule_audio_cleanup(audio_path, attempt + 1)
                return
            self._log_error(f"临时音频清理失败：{exc}")

    def _clear_pending_audio(self) -> None:
        pending_audio = self._pending_audio
        self._pending_audio = []
        for audio_path, on_started, on_finished, _prepared_audio, _text in pending_audio:
            self._schedule_audio_cleanup(audio_path)
            self._started.emit(on_started)
            self._finished.emit(on_finished)

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        if handle.cancelled:
            log_event("TTS", "预生成句柄已取消，跳过播放", {"text": handle.text, "tone": handle.tone})
            self._started.emit(on_started)
            self._finished.emit(on_finished)
            return
        if not handle.text or handle.failed:
            log_event(
                "TTS",
                "预生成句柄不可播放，直接完成",
                {
                    "text": handle.text,
                    "tone": handle.tone,
                    "failed": handle.failed,
                },
            )
            self._started.emit(on_started)
            self._finished.emit(on_finished)
            return
        handle.play_requested = True
        handle.on_started = on_started
        handle.on_finished = on_finished
        log_event(
            "TTS",
            "请求播放预生成音频",
            {
                "text": handle.text,
                "tone": handle.tone,
                "audio_ready": handle.audio_path is not None,
            },
        )
        if handle.audio_path is not None:
            self._enqueue_prepared_audio(handle)

    def deliver_audio(
        self,
        audio_path: str,
        on_started: TTSCallback | None,
        on_finished: TTSCallback | None,
        text: str,
    ) -> None:
        if not self._can_accept_synthesis_result():
            self._discard_late_audio(Path(audio_path))
            return
        self._audio_ready.emit(audio_path, on_started, on_finished, text)

    def deliver_prepared(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        if not self._can_accept_synthesis_result():
            handle.failed = True
            self._discard_late_audio(Path(audio_path))
            return
        self._prepared_audio_ready.emit(handle, audio_path)

    def schedule_cleanup(self, audio_path: Path) -> None:
        if not self._can_accept_synthesis_result():
            self._discard_late_audio(audio_path)
            return
        self._cleanup_requested.emit(audio_path)

    def _can_accept_synthesis_result(self) -> bool:
        """后台线程投递前确认 Provider 与底层 C++ QObject 都仍然存活。"""
        if not self._accepting_results or _provider_is_closed(self):
            return False
        if shiboken6 is None:
            return True
        try:
            return bool(shiboken6.isValid(self))
        except RuntimeError:
            return False

    @staticmethod
    def _discard_late_audio(audio_path: Path) -> None:
        """Qt 已关闭时同步清理未进入播放链路的临时音频。"""
        try:
            audio_path.unlink(missing_ok=True)
            log_event("TTS", "迟到音频已清理", {"audio_path": audio_path})
        except OSError as exc:
            # 无法再依赖 QTimer 重试；残留文件会由下次启动的缓存清理接管。
            log_event(
                "TTS",
                "迟到音频清理失败，留待下次启动处理",
                {"audio_path": audio_path, "error": str(exc)},
            )
