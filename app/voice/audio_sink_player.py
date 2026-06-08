from __future__ import annotations

import time
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from app.core.debug_log import debug_log

# QAudioSink 写入定时器间隔（毫秒）
_SINK_WRITE_INTERVAL_MS = 20
# PCM 全部写入后的排空延迟上限（毫秒）
_SINK_DRAIN_MAX_MS = 1000
# 日志限速：每隔 N 次写入记录一次详细日志
_SINK_LOG_INTERVAL = 50


class AudioSinkPlayer(QObject):
    """使用 wave + QAudioSink 直接写入 PCM 数据播放音频。

    相比 QMediaPlayer 播放文件的方式，本后端完全掌控 PCM 写入节奏，
    不依赖 Windows 多媒体后端的 EndOfMedia / StoppedState 回调。

    完成判定逻辑：
    1. 优先由 QAudioSink 状态变化触发：IdleState + all_pcm_written + ever_active
       → reason = "idle_after_all_pcm_written"
    2. PCM 全部写入后启动短 drain 定时器兜底：
       → reason = "drain_after_all_pcm_written"
    3. 异常路径（stop/cancel/error）：
       → reason = "stopped" / "sink_start_failed" / "write_error" / "callback_error"

    fallback_timeout 只应在 AudioSink 彻底无响应时由外层 Provider 触发。
    """

    # 播放开始信号
    started = Signal()
    # 播放完成信号: (reason: str, audio_path: str)
    finished = Signal(str, str)
    # 播放错误信号: (message: str)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._audio_path: Path | None = None
        self._sink: QAudioSink | None = None
        self._io_device: object | None = None
        self._pcm_buffer: bytes = b""
        self._write_offset: int = 0
        self._total_pcm_bytes: int = 0
        self._duration_ms: int = 0
        self._sample_rate: int = 0
        self._channels: int = 0
        self._sample_width: int = 0
        self._bytes_per_second: int = 0
        self._write_timer: QTimer | None = None
        self._write_count: int = 0
        self._started_at: float = 0.0

        # --- 状态机 ---
        self._ever_active: bool = False
        self._all_pcm_written: bool = False
        self._finishing: bool = False
        self._finished_emitted: bool = False

    # ---- 公开 API ----

    def start(self, audio_path: Path) -> bool:
        """开始播放指定 wav 文件。

        Returns:
            True 表示成功启动播放，False 表示格式不支持，调用方应 fallback。
        """
        self._audio_path = audio_path
        self._write_offset = 0
        self._write_count = 0
        self._started_at = 0.0
        self._ever_active = False
        self._all_pcm_written = False
        self._finishing = False
        self._finished_emitted = False

        # 1. 使用 wave 打开文件并校验格式
        try:
            wav_info = AudioSinkPlayer._read_wav_info(audio_path)
            if wav_info is None:
                return False
        except Exception as exc:
            debug_log(
                "TTS",
                "AudioSink: 无法读取 wav 文件",
                {"audio_path": str(audio_path), "error": str(exc)},
            )
            return False

        (
            self._pcm_buffer,
            self._total_pcm_bytes,
            self._sample_rate,
            self._channels,
            self._sample_width,
            self._duration_ms,
        ) = wav_info
        self._bytes_per_second = self._sample_rate * self._channels * self._sample_width

        debug_log(
            "TTS",
            "AudioSink: 开始播放",
            {
                "backend": "audio_sink",
                "audio_path": str(audio_path),
                "file_size": audio_path.stat().st_size if audio_path.exists() else 0,
                "total_pcm_bytes": self._total_pcm_bytes,
                "sample_rate": self._sample_rate,
                "channels": self._channels,
                "sample_width": self._sample_width,
                "duration_ms": self._duration_ms,
            },
        )

        # 2. 构建 QAudioFormat
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self._sample_rate)
        audio_format.setChannelCount(self._channels)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        # 3. 获取默认输出设备并检查格式支持
        device = QMediaDevices.defaultAudioOutput()
        if not device.isFormatSupported(audio_format):
            debug_log(
                "TTS",
                "AudioSink: 音频设备不支持格式，fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "device_format_unsupported",
                    "sample_rate": self._sample_rate,
                    "channels": self._channels,
                    "sample_format": "Int16",
                    "device": device.description(),
                },
            )
            return False

        # 4. 创建 QAudioSink 并启动
        self._sink = QAudioSink(device, audio_format, self)
        self._sink.stateChanged.connect(self._on_sink_state_changed)
        io_device = self._sink.start()
        if io_device is None:
            debug_log(
                "TTS",
                "AudioSink: QAudioSink.start() 返回空 IO 设备",
                {"audio_path": str(audio_path)},
            )
            self._finish_once("sink_start_failed")
            return False
        self._io_device = io_device

        self._started_at = time.perf_counter()
        self.started.emit()

        # 5. 启动写入定时器
        self._write_timer = QTimer(self)
        self._write_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._write_timer.timeout.connect(self._write_chunk)
        self._write_timer.start(_SINK_WRITE_INTERVAL_MS)

        return True

    def stop(self) -> None:
        """停止播放（由外部调用，如丢弃音频）。"""
        debug_log(
            "TTS",
            "AudioSink: 外部请求停止",
            {"audio_path": str(self._audio_path) if self._audio_path else ""},
        )
        self._finish_once("stopped")

    def cancel(self) -> None:
        """取消播放（等同于 stop）。"""
        self.stop()

    # ---- 内部实现 ----

    @staticmethod
    def _read_wav_info(
        audio_path: Path,
    ) -> tuple[bytes, int, int, int, int, int] | None:
        """读取 wav 文件并校验格式。

        Returns:
            (pcm_bytes, total_pcm_bytes, sample_rate, channels, sample_width, duration_ms)
            格式不支持时返回 None。
        """
        try:
            with wave.open(str(audio_path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                comptype = wav_file.getcomptype()
                compname = wav_file.getcompname()
                file_size = audio_path.stat().st_size
                pcm_bytes = wav_file.readframes(frames)
        except (OSError, wave.Error):
            return None

        total_pcm_bytes = len(pcm_bytes)
        duration_ms = max(1, int(frames * 1000 / sample_rate)) if sample_rate > 0 else 0

        if comptype != "NONE":
            debug_log(
                "TTS",
                "AudioSink: wav 压缩格式不支持，fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "compressed_wav",
                    "comptype": comptype,
                    "compname": compname,
                    "audio_path": str(audio_path),
                },
            )
            return None

        if sample_width != 2:
            debug_log(
                "TTS",
                "AudioSink: 采样位深不支持，fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "unsupported_sample_width",
                    "sample_width": sample_width,
                    "audio_path": str(audio_path),
                },
            )
            return None

        if channels not in (1, 2):
            debug_log(
                "TTS",
                "AudioSink: 声道数不支持，fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "unsupported_channels",
                    "channels": channels,
                    "audio_path": str(audio_path),
                },
            )
            return None

        debug_log(
            "TTS",
            "AudioSink: wav 格式校验通过",
            {
                "audio_path": str(audio_path),
                "file_size": file_size,
                "channels": channels,
                "sample_width": sample_width,
                "sample_rate": sample_rate,
                "frames": frames,
                "duration_ms": duration_ms,
                "comptype": comptype,
                "compname": compname,
            },
        )

        return (pcm_bytes, total_pcm_bytes, sample_rate, channels, sample_width, duration_ms)

    @Slot()
    def _write_chunk(self) -> None:
        """定时器回调：将 PCM 数据写入 QAudioSink 缓冲区。"""
        if self._sink is None or self._finishing:
            return

        try:
            bytes_free = self._sink.bytesFree()
            remaining = self._total_pcm_bytes - self._write_offset
            chunk_size = min(bytes_free, remaining)

            if chunk_size > 0:
                chunk = self._pcm_buffer[self._write_offset : self._write_offset + chunk_size]
                if self._io_device is not None and hasattr(self._io_device, "write"):
                    written = self._io_device.write(chunk)
                else:
                    written = 0
                self._write_offset += written
                self._write_count += 1

            # 限速日志
            if self._write_count % _SINK_LOG_INTERVAL == 0 or remaining <= 0:
                sink_state_str = (
                    self._sink.state().name
                    if hasattr(self._sink.state(), "name")
                    else str(self._sink.state())
                )
                debug_log(
                    "TTS",
                    "AudioSink: PCM 写入进度",
                    {
                        "total_pcm_bytes": self._total_pcm_bytes,
                        "written_bytes": self._write_offset,
                        "bytes_free": bytes_free,
                        "buffer_size": self._sink.bufferSize() if hasattr(self._sink, "bufferSize") else None,
                        "duration_ms": self._duration_ms,
                        "sink_state": sink_state_str,
                    },
                )

            # PCM 全部写入后，停止写入定时器，启动短排空定时器
            if self._write_offset >= self._total_pcm_bytes and not self._all_pcm_written:
                self._all_pcm_written = True
                self._stop_write_timer()

                # 估算排空时间：基于缓冲内剩余数据量
                drain_ms = 300
                try:
                    if self._sink is not None and self._bytes_per_second > 0:
                        buffer_size = self._sink.bufferSize() if hasattr(self._sink, "bufferSize") else 0
                        buffered_bytes = max(0, buffer_size - bytes_free)
                        if buffered_bytes > 0:
                            drain_ms = int(buffered_bytes / self._bytes_per_second * 1000) + 200
                        drain_ms = max(200, min(drain_ms, _SINK_DRAIN_MAX_MS))
                except Exception:
                    pass

                debug_log(
                    "TTS",
                    "AudioSink: PCM 全部写入，等待缓冲排空",
                    {
                        "audio_path": str(self._audio_path),
                        "written_bytes": self._write_offset,
                        "total_pcm_bytes": self._total_pcm_bytes,
                        "bytes_free": self._sink.bytesFree() if self._sink else None,
                        "buffer_size": self._sink.bufferSize() if self._sink and hasattr(self._sink, "bufferSize") else None,
                        "drain_ms": drain_ms,
                    },
                )

                QTimer.singleShot(drain_ms, self._finish_if_drained)

        except Exception as exc:
            debug_log(
                "TTS",
                "AudioSink: PCM 写入异常",
                {
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                    "audio_path": str(self._audio_path),
                    "written_bytes": self._write_offset,
                    "total_pcm_bytes": self._total_pcm_bytes,
                },
            )
            self.error.emit("音频播放失败：" + str(exc))
            self._finish_once("write_error")

    @Slot()
    @Slot()
    def _finish_if_drained(self) -> None:
        if self._finishing:
            return
        if not self._all_pcm_written:
            return

        state_name_drain = "unknown"
        try:
            sink_obj = self._sink
            if sink_obj is not None:
                st = sink_obj.state()
                state_name_drain = st.name if hasattr(st, "name") else str(st)
            else:
                state_name_drain = "no_sink"
        except Exception:
            pass

        debug_log(
            "TTS",
            "AudioSink: drain timer fired",
            {
                "audio_path": str(self._audio_path) if self._audio_path else "",
                "ever_active": self._ever_active,
                "sink_state": state_name_drain,
            },
        )

        # drain timer fired, all PCM consumed, finish regardless of ever_active
        self._finish_once("drain_after_all_pcm_written")

    def _on_sink_state_changed(self) -> None:
        """QAudioSink 状态变化回调——核心完成判定路径。"""
        if self._sink is None:
            return
        try:
            state = self._sink.state()
        except Exception:
            return

        state_name = state.name if hasattr(state, "name") else str(state)

        is_active = "ActiveState" in state_name
        is_idle = "IdleState" in state_name

        # 跟踪是否曾经进入 ActiveState
        if "ActiveState" in state_name:
            self._ever_active = True

        debug_log(
            "TTS",
            "AudioSink: 状态变化",
            {
                "state": state_name,
                "audio_path": str(self._audio_path) if self._audio_path else "",
                "all_pcm_written": self._all_pcm_written,
                "ever_active": self._ever_active,
                "is_active": is_active,
                "is_idle": is_idle,
            },
        )

        # 核心：IdleState + all_pcm_written = 自然播放完成
        if (
            "IdleState" in state_name
            and self._all_pcm_written
            and not self._finishing
        ):
            self._finish_once("idle_after_all_pcm_written")

    def _finish_once(self, reason: str) -> None:
        """统一 finish 入口，保证 exactly once。"""
        if self._finishing or self._finished_emitted:
            debug_log(
                "TTS",
                "AudioSink: 跳过重复完成",
                {
                    "reason": reason,
                    "audio_path": str(self._audio_path) if self._audio_path else "",
                },
            )
            return

        self._finishing = True
        self._finished_emitted = True

        self._stop_write_timer()

        audio_path_saved = self._audio_path
        elapsed_ms = int((time.perf_counter() - self._started_at) * 1000) if self._started_at > 0 else 0
        written_bytes = self._write_offset
        total_pcm_bytes = self._total_pcm_bytes

        # 安全关闭 sink
        if self._sink is not None:
            try:
                self._sink.stateChanged.disconnect(self._on_sink_state_changed)
            except Exception:
                pass
            try:
                self._sink.stop()
            except Exception:
                pass

        # 清理内部状态
        self._io_device = None
        self._sink = None
        self._pcm_buffer = b""
        self._write_offset = 0
        self._total_pcm_bytes = 0
        self._all_pcm_written = False
        self._ever_active = False

        debug_log(
            "TTS",
            "AudioSink: 播放完成",
            {
                "reason": reason,
                "audio_path": str(audio_path_saved) if audio_path_saved else "",
                "elapsed_ms": elapsed_ms,
                "written_bytes": written_bytes,
                "total_pcm_bytes": total_pcm_bytes,
            },
        )

        self.finished.emit(reason, str(audio_path_saved) if audio_path_saved else "")

    def _stop_write_timer(self) -> None:
        if self._write_timer is not None:
            try:
                self._write_timer.stop()
            except Exception:
                pass
            self._write_timer = None