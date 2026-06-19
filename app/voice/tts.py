from __future__ import annotations

import array
import base64
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol
from urllib.parse import urlencode, urlparse, urlunparse

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot

from app.config.character_loader import CharacterProfile
from app.core.gui_log import record_tts_service_output
from app.llm.chat_reply import DEFAULT_TONE
from app.core.debug_log import debug_log
from app.core.interaction import get_interaction_id, set_interaction_id
from app.storage.paths import StoragePaths
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL as _DEFAULT_GENIE_TTS_API_URL,
    GPTSoVITSTTSSettings as _GPTSoVITSTTSSettings,
    TTS_PLAYBACK_BACKEND_AUDIO_SINK as _TTS_PLAYBACK_BACKEND_AUDIO_SINK,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS as _TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE as _TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS as _TTS_PROVIDER_GPT_SOVITS,
    TTSConfigError as _TTSConfigError,
    ToneReference as _ToneReference,
    _normalize_tts_provider as _normalize_tts_provider_setting,
)
from app.voice import audio_checks as _audio_checks
from app.voice.runtime_compat import find_usable_runtime_python, format_runtime_python_issue

if TYPE_CHECKING:
    from PySide6.QtMultimedia import QAudioOutput as QAudioOutputType
    from PySide6.QtMultimedia import QMediaPlayer as QMediaPlayerType

    from app.voice.audio_sink_player import AudioSinkPlayer

QAudioOutput: type[Any] | None = None
QMediaPlayer: type[Any] | None = None

# 默认使用 AudioSink 后端
_DEFAULT_PLAYBACK_BACKEND = _TTS_PLAYBACK_BACKEND_AUDIO_SINK

TTSCallback = Callable[[], None]
_AUDIO_CLEANUP_DELAY_MS = 5000
_AUDIO_CLEANUP_MAX_ATTEMPTS = 5
_AUDIO_FINISH_FALLBACK_GRACE_MS = 1500
_AUDIO_FINISH_FALLBACK_MIN_MS = 2000
# 播放完成兜底的上限：时长无法解析或异常超长时按此值兜底，防止流程永久挂起
_AUDIO_FINISH_FALLBACK_MAX_MS = 60_000
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
# 可发音字符:数字/拉丁字母/假名/汉字/谚文(含全角)。纯标点、emoji、符号不算——
# 这类文本喂给 GPT-SoVITS 归一化后音素为空,会触发服务端 [Errno 22] Invalid argument。
_VOICEABLE_CHAR_RE = re.compile(
    "[0-9A-Za-z"
    "぀-ヿ"  # 平假名/片假名
    "㐀-䶿"  # CJK 扩展 A
    "一-鿿"  # CJK 基本
    "豈-﫿"  # CJK 兼容
    "가-힣"  # 谚文音节
    "０-９Ａ-Ｚａ-ｚ"  # 全角数字/字母
    "ｦ-ﾟ"  # 半角片假名
    "]"
)
_CJK_TEXT_LANGS = {"ja", "all_ja", "zh", "all_zh", "ko", "all_ko", "yue", "all_yue"}
_LOCAL_SERVICE_STARTUP_TIMEOUT_MAX = 180


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


def _resolve_project_root(base_dir: Path | None = None) -> Path:
    """解析项目根目录；base_dir 为空时基于 __file__ 推算（app/voice/tts.py → 项目根），
    与 main.py 的路径惯例一致。"""
    return Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[2]


def _resolve_tts_cache_dir(base_dir: Path | None = None) -> Path:
    """返回 TTS 临时音频缓存目录（data/cache/tts），并确保存在。

    不再写入系统 Temp，改用 Sakura 自有数据目录，便于集中管理与启动清理。
    """
    cache_dir = StoragePaths(_resolve_project_root(base_dir)).tts_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def purge_tts_cache(base_dir: Path | None = None) -> None:
    """启动时清空 data/cache/tts 残留（崩溃/强退遗留的临时 wav）。

    该目录完全归 Sakura 所有、仅存放 TTS 临时音频，清空安全。
    逐个删除并忽略个别占用错误，不影响启动。
    """
    cache_dir = _resolve_tts_cache_dir(base_dir)
    for entry in cache_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            entry.unlink()
        except OSError as exc:
            debug_log("TTS", "启动清理缓存文件失败，已跳过", {"path": str(entry), "error": str(exc)})


@dataclass
class TTSPreparedAudio:
    """一段已提交预生成的 TTS 音频句柄。"""

    text: str
    tone: str | None = None
    audio_path: Path | None = None
    play_requested: bool = False
    enqueued: bool = False
    cancelled: bool = False
    failed: bool = False
    on_started: TTSCallback | None = None
    on_finished: TTSCallback | None = None


@dataclass(frozen=True)
class _TTSRequest:
    text: str
    tone: str | None
    on_started: TTSCallback | None = None
    on_finished: TTSCallback | None = None
    prepared_audio: TTSPreparedAudio | None = None
    # 发起请求时的交互 ID；请求线程入口恢复，使 TTS 日志可与该次交互串联
    interaction_id: str = ""


class TTSServiceState(str, Enum):
    """TTS 本地服务生命周期的显式状态；转移由 _set_service_state 统一记日志。

    IDLE → PROBING → (READY | STARTING) ; STARTING → WAITING_READY → (READY | FAILED)
    READY 后探测短路；FAILED 不缓存——下次请求重新走完整流程（服务可能被手动拉起）。
    """

    IDLE = "idle"
    PROBING = "probing"
    STARTING = "starting"
    WAITING_READY = "waiting_ready"
    READY = "ready"
    FAILED = "failed"


def _set_service_state(provider: object, new_state: TTSServiceState, detail: dict | None = None) -> None:
    """记录服务状态转移；provider 可能是测试桩（SimpleNamespace），全程容错。"""
    old_state = getattr(provider, "_service_state", TTSServiceState.IDLE)
    try:
        setattr(provider, "_service_state", new_state)
    except (AttributeError, TypeError):
        pass
    if old_state == new_state:
        return
    payload = {"from": str(getattr(old_state, "value", old_state)), "to": new_state.value}
    if detail:
        payload.update(detail)
    debug_log("TTS", "tts.service_state", payload)


def _provider_is_closed(provider: object) -> bool:
    is_closed = getattr(provider, "_is_closed", None)
    if callable(is_closed):
        return bool(is_closed())
    return bool(getattr(provider, "_closed", False))


def _parse_service_endpoint(api_url: str) -> tuple[str, int] | None:
    """解析服务地址为 (host, port)；地址非法返回 None，由调用方给出服务名相关提示。"""
    parsed_url = urlparse(api_url)
    host = parsed_url.hostname
    try:
        port = parsed_url.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed_url.scheme == "https" else 80
    if not host:
        return None
    return host, port


def _wait_local_service_ready(
    *,
    provider: object,
    service_name: str,
    ready_check: Callable[[], bool],
    fail_callback: Callable[[str], None],
    timeout_seconds: int,
) -> bool:
    """启动本地服务后的统一就绪轮询：进程存活检查 + ready_check，直到超时。

    大模型首次加载可能超过 30 秒，按用户配置等待（封顶 _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX），
    避免刚加载完成就被判超时。
    """
    settings = getattr(provider, "settings")
    base_dir = getattr(provider, "_base_dir", None)
    _set_service_state(provider, TTSServiceState.WAITING_READY)
    deadline = time.monotonic() + max(3, min(timeout_seconds, _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX))
    while time.monotonic() < deadline:
        if _provider_is_closed(provider):
            _set_service_state(provider, TTSServiceState.FAILED, {"reason": "provider_closed"})
            return False
        process = getattr(provider, "_server_process", None)
        exit_code = process.poll() if process is not None else None
        if exit_code is not None:
            log_path = _local_tts_service_log_path(settings.provider, base_dir)
            _set_service_state(provider, TTSServiceState.FAILED, {"reason": "process_exited", "exit_code": exit_code})
            fail_callback(
                f"{service_name} 本地服务进程已退出，退出码：{exit_code}。"
                f"请查看启动日志：{log_path}"
            )
            return False
        if ready_check():
            return True
        time.sleep(0.5)
    log_path = _local_tts_service_log_path(settings.provider, base_dir)
    _set_service_state(provider, TTSServiceState.FAILED, {"reason": "startup_timeout"})
    fail_callback(
        f"{service_name} 已尝试启动，但端口仍不可用：{settings.api_url}。"
        f"请查看启动日志：{log_path}"
    )
    return False


class _LocalProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None:
        """返回本地 TTS 进程是否仍在运行。"""

    def terminate(self) -> None:
        """终止本地 TTS 进程。"""

    def kill(self) -> None:
        """强制终止本地 TTS 进程。"""

    def wait(self, timeout: int | float | None = None) -> int | None:
        """等待本地 TTS 进程退出。"""


class _AttachedLocalProcess:
    """把启动前已存在的本地 TTS 进程纳入关闭流程。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None if _process_exists(self.pid) else 0

    def terminate(self) -> None:
        _terminate_pid_tree(self.pid, timeout=5)

    def kill(self) -> None:
        _terminate_pid_tree(self.pid, timeout=5)

    def wait(self, timeout: int | float | None = None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(["pid", str(self.pid)], timeout)
            time.sleep(0.1)
        return 0


class TTSProvider(Protocol):
    @property
    def service_ready(self) -> bool:
        """本地 TTS 服务是否已探测/预热完成。"""
        ...

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        """播放或提交一段待朗读文本。"""

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        """提前生成一段待朗读音频，但不立即播放。"""

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        """播放 prepare 返回的音频；若仍在生成，则等待生成完成后播放。"""

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        """丢弃不再需要的预生成音频。"""

    def warm_up_playback(self) -> None:
        """提前初始化本地播放器，避免第一句朗读承担冷启动成本。"""

    def ensure_ready(self) -> tuple[bool, str]:
        """同步检测并预热 TTS 服务，不生成或播放音频。"""

    def close(self) -> None:
        """释放 Provider 自己启动的本地服务。"""


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
        # GPT-SoVITS 接入前保留调用点，避免聊天流程以后再改。
        debug_log(
            "TTS",
            "静音 Provider 跳过播放",
            {
                "text": text,
                "tone": tone,
            },
        )
        _ = text
        _ = tone
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        debug_log("TTS", "静音 Provider 跳过预生成", {"text": text, "tone": tone})
        return TTSPreparedAudio(text=text.strip(), tone=tone)

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        debug_log(
            "TTS",
            "静音 Provider 跳过预生成播放",
            {
                "text": handle.text,
                "tone": handle.tone,
            },
        )
        _ = handle
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        debug_log("TTS", "丢弃静音预生成句柄", {"text": handle.text, "tone": handle.tone})
        handle.cancelled = True

    def warm_up_playback(self) -> None:
        debug_log("TTS", "静音 Provider 跳过播放器预热")

    def ensure_ready(self) -> tuple[bool, str]:
        debug_log("TTS", "静音 Provider 跳过服务检测")
        return True, "TTS 已关闭。"

    def close(self) -> None:
        debug_log("TTS", "静音 Provider 无需关闭")


class GPTSoVITSTTSProvider(QObject):
    error_occurred = Signal(str)
    _audio_ready = Signal(str, object, object, str)
    _prepared_audio_ready = Signal(object, str)
    _prepared_audio_failed = Signal(object, str)
    _prepared_audio_skipped = Signal(object)
    _failed = Signal(str)
    _started = Signal(object)
    _finished = Signal(object)

    def __init__(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        adopt_existing_service: bool = True,
    ) -> None:
        super().__init__()
        settings.validate()
        self.settings = settings
        # TTS 临时音频缓存目录（data/cache/tts）。由调用方注入 base_dir，
        # 与启动清理 purge_tts_cache(base_dir) 同源，避免写入目录与清理目录错位。
        # base_dir 为空时退回 _resolve_tts_cache_dir 的 __file__ 推算，保持向后兼容。
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._tts_cache_dir = _resolve_tts_cache_dir(base_dir)
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
        self._request_lock = threading.Lock()
        self._pending_requests: list[_TTSRequest] = []
        self._request_running = False
        self._closed = False
        self._tone_indices: dict[str, int] = {}
        self._weights_ready = False
        self._service_checked = False
        # 服务生命周期显式状态（_service_checked 是其 READY 的向后兼容投影）
        self._service_state = TTSServiceState.IDLE
        self._server_process: _LocalProcessHandle | None = None
        self._playback_warmup_requested = False
        self._playback_finish_token = 0
        # 播放后端：audio_sink 或 media_player
        self._playback_backend: str = (
            getattr(settings, "playback_backend", _DEFAULT_PLAYBACK_BACKEND)
            or _DEFAULT_PLAYBACK_BACKEND
        )
        self._sink_player: AudioSinkPlayer | None = None

        self._audio_output: QAudioOutputType | None = None
        self._player: QMediaPlayerType | None = None
        self._audio_ready.connect(self._enqueue_audio)
        self._prepared_audio_ready.connect(self._store_prepared_audio)
        self._prepared_audio_failed.connect(self._fail_prepared_audio)
        self._prepared_audio_skipped.connect(self._skip_prepared_audio)
        self._failed.connect(self._log_error)
        self._started.connect(self._run_callback)
        self._finished.connect(self._run_callback)
        if adopt_existing_service:
            self._adopt_existing_configured_service()

    @property
    def service_ready(self) -> bool:
        """服务探测是否已成功(实际可达)。

        供接话音频预生成等调用方做就绪门控:provider 实例存在不代表
        服务已启动,未就绪时发起 prepare 只会得到静默失败。
        Genie 子类共用 _service_checked,无需覆写。
        """
        return self._service_checked

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            debug_log("TTS", "空文本跳过播放")
            self._started.emit(on_started)
            self._finished.emit(on_finished)
            return
        debug_log("TTS", "提交播放请求", {"text": text, "tone": tone})
        self._queue_request(
            _TTSRequest(
                text=text,
                tone=tone,
                on_started=on_started,
                on_finished=on_finished,
                interaction_id=get_interaction_id(),
            )
        )

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        text = text.strip()
        handle = TTSPreparedAudio(text=text, tone=tone)
        if not text:
            debug_log("TTS", "空文本跳过预生成")
            handle.failed = True
            return handle
        debug_log("TTS", "提交预生成请求", {"text": text, "tone": tone})
        self._queue_request(
            _TTSRequest(
                text=text,
                tone=tone,
                prepared_audio=handle,
                interaction_id=get_interaction_id(),
            )
        )
        return handle

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        if handle.cancelled:
            debug_log("TTS", "预生成句柄已取消，跳过播放", {"text": handle.text, "tone": handle.tone})
            self._started.emit(on_started)
            self._finished.emit(on_finished)
            return
        if not handle.text or handle.failed:
            debug_log(
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
        debug_log(
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

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        handle.cancelled = True
        debug_log("TTS", "取消预生成音频", {"text": handle.text, "tone": handle.tone})
        with self._request_lock:
            self._pending_requests = [
                request
                for request in self._pending_requests
                if request.prepared_audio is not handle
            ]

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

    def warm_up_playback(self) -> None:
        """把 Qt Multimedia 的冷启动提前到空闲阶段完成。"""

        if self._player is not None:
            debug_log("TTS", "Qt 多媒体播放器已初始化，跳过预热")
            return
        if self._playback_warmup_requested:
            debug_log("TTS", "Qt 多媒体播放器预热已排队，跳过重复请求")
            return
        self._playback_warmup_requested = True
        debug_log("TTS", "安排 Qt 多媒体播放器预热")
        QTimer.singleShot(0, self._warm_up_playback)

    @Slot()
    def _warm_up_playback(self) -> None:
        started_at = time.perf_counter()
        try:
            if self._player is not None:
                debug_log("TTS", "Qt 多媒体播放器已初始化，预热无需执行")
                return
            debug_log("TTS", "开始预热 Qt 多媒体播放器")
            self._ensure_player()
            debug_log(
                "TTS",
                "Qt 多媒体播放器预热完成",
                {"elapsed_ms": int((time.perf_counter() - started_at) * 1000)},
            )
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "Qt 多媒体播放器预热失败", {"error": str(exc)})
            self._failed.emit(f"Qt 多媒体播放器预热失败：{exc}")
        finally:
            self._playback_warmup_requested = False

    def ensure_ready(self) -> tuple[bool, str]:
        """启动并检测 GPT-SoVITS 服务，同时预加载角色权重。"""

        try:
            self.settings.validate()
        except _TTSConfigError as exc:
            return False, str(exc)

        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 服务不可用。"
        if not self._ensure_character_weights(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 角色权重加载失败。"
        return True, "TTS 服务已就绪。"

    def _queue_request(self, request: _TTSRequest) -> None:
        with self._request_lock:
            if self._closed:
                if request.prepared_audio is not None:
                    request.prepared_audio.failed = True
                debug_log(
                    "TTS",
                    "Provider 已关闭，丢弃新请求",
                    {
                        "text": request.text,
                        "tone": request.tone,
                        "prepared": request.prepared_audio is not None,
                    },
                )
                return
            self._pending_requests.append(request)
            pending_count = len(self._pending_requests)
        debug_log(
            "TTS",
            "请求加入队列",
            {
                "text": request.text,
                "tone": request.tone,
                "prepared": request.prepared_audio is not None,
                "pending_count": pending_count,
            },
        )
        self._start_next_request()

    def _start_next_request(self) -> None:
        with self._request_lock:
            if self._closed or self._request_running or not self._pending_requests:
                return
            request = self._pending_requests.pop(0)
            self._request_running = True

        debug_log(
            "TTS",
            "开始处理队列请求",
            {
                "text": request.text,
                "tone": request.tone,
                "prepared": request.prepared_audio is not None,
            },
        )
        thread = threading.Thread(
            target=self._request_audio,
            args=(request,),
            daemon=True,
        )
        thread.start()

    def _request_audio(self, tts_request: _TTSRequest) -> None:
        # 请求线程恢复发起方的交互 ID，使本线程内日志可与该次交互串联
        set_interaction_id(tts_request.interaction_id)
        try:
            if _provider_is_closed(self):
                debug_log("TTS", "Provider 已关闭，跳过音频请求", {"text": tts_request.text})
                return
            if tts_request.prepared_audio is not None and tts_request.prepared_audio.cancelled:
                debug_log("TTS", "请求已取消，跳过音频生成", {"text": tts_request.text})
                return

            # 纯标点/emoji/符号段没有可发音内容，喂给服务端会归一化成空音素并触发
            # [Errno 22]；提前判定为“无需发音”，正常走完回调但不发请求、不报错。
            if not _is_voiceable_text(tts_request.text):
                debug_log("TTS", "文本无可发音内容，跳过合成", {"text": tts_request.text})
                self._skip_audio_request(tts_request, "无可发音内容")
                return

            fail = lambda message: self._fail_audio_request(tts_request, message)
            restart_attempted = False
            while True:
                if not self._ensure_service_available(fail):
                    return

                if not self._ensure_character_weights(fail):
                    return

                reference = self._select_reference(tts_request.tone)
                payload = {
                    "text": tts_request.text,
                    "text_lang": _resolve_request_text_lang(
                        tts_request.text,
                        self.settings.text_lang,
                    ),
                    "ref_audio_path": str(reference.ref_audio_path),
                    "prompt_text": reference.ref_text,
                    "prompt_lang": reference.ref_lang,
                    "text_split_method": "cut1",
                    "batch_size": 1,
                    "media_type": "wav",
                    "streaming_mode": False,
                    "top_k": 15,
                    "top_p": 1,
                    "temperature": 1,
                    "repetition_penalty": 1.2,
                }
                debug_log(
                    "TTS",
                    "发送 GPT-SoVITS 请求",
                    {
                        "api_url": self.settings.api_url,
                        "text": tts_request.text,
                        "tone": tts_request.tone,
                        "reference": {
                            "tone": reference.tone,
                            "ref_audio_path": reference.ref_audio_path,
                            "ref_lang": reference.ref_lang,
                        },
                        "payload": payload,
                        "attempt": 2 if restart_attempted else 1,
                    },
                )
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                http_request = urllib.request.Request(
                    url=self.settings.api_url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )

                try:
                    with urllib.request.urlopen(
                        http_request,
                        timeout=self.settings.timeout_seconds,
                    ) as response:
                        audio_data = response.read()
                        debug_log(
                            "TTS",
                            "GPT-SoVITS 请求成功",
                            {
                                "status": getattr(response, "status", None),
                                "audio_bytes": len(audio_data),
                                "attempt": 2 if restart_attempted else 1,
                            },
                        )
                    break
                except urllib.error.HTTPError as exc:
                    error_body = exc.read().decode("utf-8", errors="replace")
                    debug_log(
                        "TTS",
                        "GPT-SoVITS HTTP 失败",
                        {
                            "status": exc.code,
                            "error_body": error_body,
                            "attempt": 2 if restart_attempted else 1,
                        },
                    )
                    if (
                        not restart_attempted
                        and self._restart_local_service_after_http_failure(exc.code, error_body)
                    ):
                        restart_attempted = True
                        continue
                    message = _format_gpt_sovits_http_error(exc.code, error_body)
                    if _is_soft_synth_failure(exc.code, error_body):
                        # 单段合成失败（服务端 tts failed）：文本已照常显示，语音缺一段无需
                        # 打断用户，静默跳过、正常完成回调，不向 UI 弹 TTS 异常。
                        self._skip_audio_request(tts_request, message)
                    else:
                        self._fail_audio_request(tts_request, message)
                    return
                except urllib.error.URLError as exc:
                    debug_log("TTS", "GPT-SoVITS 请求失败", {"reason": str(exc.reason)})
                    self._fail_audio_request(
                        tts_request,
                        f"GPT-SoVITS 请求失败，请确认服务已启动并可访问 {self.settings.api_url}：{exc.reason}",
                    )
                    return
                except TimeoutError:
                    debug_log("TTS", "GPT-SoVITS 请求超时")
                    self._fail_audio_request(tts_request, "GPT-SoVITS 请求超时。")
                    return

            if not audio_data:
                debug_log("TTS", "GPT-SoVITS 返回空音频")
                self._fail_audio_request(tts_request, "GPT-SoVITS 返回了空音频。")
                return

            with tempfile.NamedTemporaryFile(
                prefix="sakura_tts_",
                suffix=".wav",
                delete=False,
                dir=str(self._tts_cache_dir),
            ) as audio_file:
                audio_file.write(audio_data)
                audio_path = audio_file.name
            debug_log("TTS", "临时音频已写入", {"audio_path": audio_path, "bytes": len(audio_data)})
            audio_issue = _audio_checks._verify_generated_audio(Path(audio_path))
            if audio_issue is not None:
                debug_log("TTS", "生成音频校验失败", {"audio_path": audio_path, "issue": audio_issue})
                self._fail_audio_request(tts_request, f"GPT-SoVITS 生成的音频无效（{audio_issue}）。")
                self._schedule_audio_cleanup(Path(audio_path))
                return
            if tts_request.prepared_audio is None:
                self._audio_ready.emit(
                    audio_path,
                    tts_request.on_started,
                    tts_request.on_finished,
                    tts_request.text,
                )
            else:
                self._prepared_audio_ready.emit(tts_request.prepared_audio, audio_path)
        finally:
            with self._request_lock:
                self._request_running = False
            self._start_next_request()

    def _ensure_service_available(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if _provider_is_closed(self):
            debug_log("TTS", "Provider 已关闭，跳过服务探测", {"api_url": self.settings.api_url})
            return False
        if self._service_checked:
            debug_log("TTS", "服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if endpoint is None:
            debug_log("TTS", "服务地址无效", {"api_url": self.settings.api_url})
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "invalid_api_url"})
            fail_callback(f"GPT-SoVITS 服务地址无效：{self.settings.api_url}")
            return False
        host, port = endpoint

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        _set_service_state(self, TTSServiceState.PROBING)
        if GPTSoVITSTTSProvider._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            GPTSoVITSTTSProvider._adopt_existing_local_service(self, host, port)
            self._service_checked = True
            _set_service_state(self, TTSServiceState.READY, {"via": "probe"})
            debug_log("TTS", "服务探测成功", {"api_url": self.settings.api_url})
            return True

        if self.settings.work_dir is None:
            # 没有可启动的本地整合包：探测失败即不可用（远端/手动服务场景）
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "service_unreachable"})
            fail_callback(f"GPT-SoVITS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        _set_service_state(self, TTSServiceState.STARTING)
        if _provider_is_closed(self):
            return False
        if not GPTSoVITSTTSProvider._start_local_service(self, fail_callback):
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "start_failed"})
            return False

        def _ready() -> bool:
            # 端口通但 HTTP 层尚未就绪（模型仍在加载）时继续等待
            return GPTSoVITSTTSProvider._probe_service_port(
                self, host, port, timeout, purpose="startup_wait"
            ) and _probe_gpt_sovits_http(self.settings.api_url, timeout)

        if not _wait_local_service_ready(
            provider=self,
            service_name="GPT-SoVITS",
            ready_check=_ready,
            fail_callback=fail_callback,
            timeout_seconds=self.settings.timeout_seconds,
        ):
            return False
        self._service_checked = True
        _set_service_state(self, TTSServiceState.READY, {"via": "local_start"})
        debug_log(
            "TTS",
            "本地 GPT-SoVITS 服务启动并探测成功",
            {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
        )
        return True

    def _restart_local_service_after_http_failure(
        self,
        status_code: int,
        error_body: str,
    ) -> bool:
        """HTTP 层显示本地服务管道已坏时，重启 Sakura 管理的本地服务。

        GPT-SoVITS 在进程 stdout/stderr 管道断开时可能把任意有效文本都包装成
        400 + tts failed + Broken pipe。它不是单段文本问题；继续复用该端口只会
        让后续回复全部无声。只对带 work_dir 的本地整合包启用，远端/手动服务不
        擅自终止。
        """
        if not _is_restartable_local_tts_service_failure(status_code, error_body):
            return False
        if self.settings.work_dir is None:
            debug_log(
                "TTS",
                "GPT-SoVITS 服务疑似管道断开，但非本地整合包，不自动重启",
                {"status": status_code, "error_body": error_body},
            )
            return False
        if _provider_is_closed(self):
            return False

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if self._server_process is None and endpoint is not None:
            host, port = endpoint
            GPTSoVITSTTSProvider._adopt_existing_local_service(self, host, port)
        if self._server_process is None:
            debug_log(
                "TTS",
                "GPT-SoVITS 服务疑似管道断开，但未能定位本地服务进程",
                {"status": status_code, "api_url": self.settings.api_url},
            )
            return False

        debug_log(
            "TTS",
            "GPT-SoVITS 服务疑似管道断开，重启本地服务后重试",
            {
                "status": status_code,
                "pid": self._server_process.pid,
                "api_url": self.settings.api_url,
            },
        )
        self._service_checked = False
        self._weights_ready = False
        _set_service_state(self, TTSServiceState.STARTING, {"reason": "restart_after_broken_pipe"})
        GPTSoVITSTTSProvider._stop_local_service(self)
        return True

    def _adopt_existing_local_service(self, host: str, port: int) -> None:
        current = getattr(self, "_server_process", None)
        if current is not None and current.poll() is None:
            return
        process = _find_running_local_tts_process(self.settings, port)
        if process is None:
            return
        self._server_process = process
        debug_log(
            "TTS",
            "接管已有本地 TTS 服务进程，退出时将一并清理",
            {
                "pid": process.pid,
                "provider": self.settings.provider,
                "host": host,
                "port": port,
                "work_dir": str(self.settings.work_dir) if self.settings.work_dir is not None else "",
            },
        )

    def _adopt_existing_configured_service(self) -> None:
        parsed_url = urlparse(self.settings.api_url)
        host = parsed_url.hostname or "127.0.0.1"
        try:
            port = parsed_url.port
        except ValueError:
            return
        if port is None:
            return
        self._adopt_existing_local_service(host, port)

    def _probe_service_port(self, host: str, port: int, timeout: int, *, purpose: str = "availability_check") -> bool:
        service_name = _tts_service_display_name(self.settings.provider)
        payload = {
            "api_url": self.settings.api_url,
            "host": host,
            "port": port,
            "purpose": purpose,
        }
        try:
            debug_log(
                "TTS",
                f"探测 {service_name} 端口",
                payload,
            )
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except TimeoutError:
            debug_log("TTS", _probe_failure_message(service_name, purpose, timeout=True), payload)
            return False
        except OSError as exc:
            debug_log(
                "TTS",
                _probe_failure_message(service_name, purpose, timeout=False),
                {**payload, "reason": str(exc)},
            )
            return False
        return True

    def _start_local_service(self, fail_callback: Callable[[str], None]) -> bool:
        if _provider_is_closed(self):
            return False
        work_dir = self.settings.work_dir
        if work_dir is None:
            return False
        work_dir = work_dir.resolve()
        runtime_dir = work_dir / "runtime"
        python_exe = self.settings.python_path
        if python_exe is not None:
            python_exe = python_exe.resolve()
        else:
            python_exe = find_usable_runtime_python(runtime_dir)
        api_script = work_dir / "api_v2.py"
        if not work_dir.is_dir():
            fail_callback(f"GPT-SoVITS 工作目录不存在：{work_dir}")
            return False
        if python_exe is None:
            fail_callback(f"GPT-SoVITS 运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False
        if not python_exe.is_file():
            fail_callback(f"GPT-SoVITS Python 不存在：{python_exe}")
            return False
        if not api_script.is_file():
            fail_callback(f"GPT-SoVITS 启动脚本不存在：{api_script}")
            return False

        if self._server_process is not None and self._server_process.poll() is None:
            debug_log("TTS", "本地 GPT-SoVITS 进程已启动，跳过重复启动", {"work_dir": str(work_dir)})
            return True

        try:
            log_path = _local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, object] = {
                "cwd": str(work_dir),
                "env": _local_tts_subprocess_env(python_exe),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 GPT-SoVITS：{work_dir}\n")
                log_file.flush()
            self._server_process = subprocess.Popen(
                _build_gpt_sovits_start_command(python_exe, api_script, self.settings),
                **kwargs,
            )
            if _provider_is_closed(self):
                self._stop_local_service()
                return False
            _start_local_tts_output_reader(
                self._server_process,
                log_path,
                "GPT-SoVITS",
            )
        except OSError as exc:
            debug_log("TTS", "本地 GPT-SoVITS 服务启动失败", {"work_dir": str(work_dir), "error": str(exc)})
            fail_callback(f"GPT-SoVITS 服务启动失败：{exc}")
            return False

        debug_log(
            "TTS",
            "已启动本地 GPT-SoVITS 服务",
            {
                "work_dir": str(work_dir),
                "pid": self._server_process.pid,
                "log_path": str(_local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))),
            },
        )
        return True

    def _ensure_character_weights(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if self._weights_ready:
            debug_log("TTS", "角色权重已就绪，跳过切换")
            return True

        for endpoint, path in (
            ("set_gpt_weights", self.settings.gpt_model_path),
            ("set_sovits_weights", self.settings.sovits_model_path),
        ):
            if path is None:
                continue
            debug_log("TTS", "准备切换角色权重", {"endpoint": endpoint, "path": path})
            if not self._request_weight_switch(endpoint, path, fail_callback):
                return False

        self._weights_ready = True
        debug_log("TTS", "角色权重切换完成")
        return True

    def _request_weight_switch(
        self,
        endpoint: str,
        weights_path: Path,
        fail_callback: Callable[[str], None],
    ) -> bool:
        url = _build_tts_endpoint_url(
            self.settings.api_url,
            endpoint,
            {"weights_path": str(weights_path)},
        )
        request = urllib.request.Request(url=url, method="GET")
        try:
            debug_log("TTS", "请求切换权重", {"endpoint": endpoint, "weights_path": weights_path})
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                response.read()
                debug_log(
                    "TTS",
                    "权重切换成功",
                    {
                        "endpoint": endpoint,
                        "weights_path": weights_path,
                        "status": getattr(response, "status", None),
                    },
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            debug_log(
                "TTS",
                "权重切换 HTTP 失败",
                {
                    "endpoint": endpoint,
                    "weights_path": weights_path,
                    "status": exc.code,
                    "error_body": error_body,
                },
            )
            fail_callback(
                f"GPT-SoVITS 切换权重失败（{endpoint}, {weights_path}）HTTP {exc.code}: {error_body}"
            )
            return False
        except urllib.error.URLError as exc:
            debug_log(
                "TTS",
                "权重切换请求失败",
                {
                    "endpoint": endpoint,
                    "weights_path": weights_path,
                    "reason": str(exc.reason),
                },
            )
            fail_callback(f"GPT-SoVITS 切换权重失败（{endpoint}, {weights_path}）：{exc.reason}")
            return False
        except TimeoutError:
            debug_log("TTS", "权重切换超时", {"endpoint": endpoint, "weights_path": weights_path})
            fail_callback(f"GPT-SoVITS 切换权重超时（{endpoint}, {weights_path}）。")
            return False
        return True

    def _select_reference(self, tone: str | None) -> _ToneReference:
        tone_key = (tone or DEFAULT_TONE).strip() or DEFAULT_TONE
        references = self.settings.tone_references.get(tone_key)
        if not references:
            references = self.settings.tone_references.get(DEFAULT_TONE)
        if not references:
            reference = _ToneReference(
                tone=DEFAULT_TONE,
                ref_audio_path=self.settings.ref_audio_path,
                ref_text=self.settings.ref_text,
                ref_lang=self.settings.ref_lang,
            )
            debug_log(
                "TTS",
                "选择默认参考音频",
                {
                    "requested_tone": tone,
                    "ref_audio_path": reference.ref_audio_path,
                    "ref_lang": reference.ref_lang,
                },
            )
            return reference

        index = self._tone_indices.get(tone_key, 0) % len(references)
        self._tone_indices[tone_key] = index + 1
        reference = references[index]
        debug_log(
            "TTS",
            "选择语气参考音频",
            {
                "requested_tone": tone,
                "resolved_tone": tone_key,
                "index": index,
                "count": len(references),
                "ref_audio_path": reference.ref_audio_path,
                "ref_lang": reference.ref_lang,
            },
        )
        return reference

    @Slot(str, object, object)
    def _enqueue_audio(
        self,
        audio_path: str,
        on_started: TTSCallback | None,
        on_finished: TTSCallback | None,
        text: str = "",
    ) -> None:
        if _provider_is_closed(self):
            path = Path(audio_path)
            debug_log("TTS", "Provider 已关闭，清理迟到音频", {"audio_path": path, "text": text})
            self._schedule_audio_cleanup(path)
            return
        self._pending_audio.append((Path(audio_path), on_started, on_finished, None, text))
        debug_log(
            "TTS",
            "音频加入播放队列",
            {
                "text": text,
                "audio_path": audio_path,
                "pending_audio": len(self._pending_audio),
                "current_audio": str(self._current_audio) if self._current_audio else None,
                "playback_state": self._playback_backend,
            },
        )
        if self._current_audio is None:
            QTimer.singleShot(0, self._play_next)

    @Slot(object, str)
    def _store_prepared_audio(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        path = Path(audio_path)
        if _provider_is_closed(self):
            handle.failed = True
            debug_log("TTS", "Provider 已关闭，清理迟到的预生成音频", {"audio_path": path})
            self._schedule_audio_cleanup(path)
            return
        if handle.cancelled:
            debug_log("TTS", "预生成音频已取消，清理文件", {"audio_path": path})
            self._schedule_audio_cleanup(path)
            return
        handle.audio_path = path
        debug_log(
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
        if handle.cancelled or not handle.play_requested:
            return
        self._started.emit(handle.on_started)
        self._finished.emit(handle.on_finished)
        handle.on_started = None
        handle.on_finished = None

    @Slot(object)
    def _handle_media_status(self, status: object) -> None:
        debug_log(
            "TTS",
            "播放器媒体状态变化",
            {
                "status": str(status),
                "audio_path": str(self._current_audio) if self._current_audio else "",
            },
        )
        _QAudioOutput, QMediaPlayer = _load_qt_multimedia()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish_current_audio("end_of_media")
            self._play_next()

    @Slot(object)
    def _handle_playback_state(self, state: object) -> None:
        debug_log(
            "TTS",
            "播放器播放状态变化",
            {
                "state": str(state),
                "audio_path": str(self._current_audio) if self._current_audio else "",
            },
        )
        _QAudioOutput, QMediaPlayer = _load_qt_multimedia()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._emit_current_started()
            return
        if (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self._current_audio is not None
            and self._current_started_emitted
        ):
            debug_log(
                "TTS",
                "播放器停止，按当前音频播放完成处理",
                {"audio_path": str(self._current_audio)},
            )
            self._finish_current_audio("stopped_state")
            self._play_next()

    @Slot(object, str)
    def _handle_player_error(self, _error: object, error_text: str) -> None:
        debug_log(
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
        debug_log("TTS", "错误通知", {"message": message})
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
        debug_log("TTS", "音频请求失败", {"message": message})
        self._started.emit(on_started)
        self._finished.emit(on_finished)

    def _fail_audio_request(self, request: _TTSRequest, message: str) -> None:
        if _provider_is_closed(self):
            debug_log("TTS", "Provider 已关闭，忽略音频请求失败通知", {"message": message})
            return
        if request.prepared_audio is None:
            self._fail_request(message, request.on_started, request.on_finished)
            return
        self._prepared_audio_failed.emit(request.prepared_audio, message)

    def _skip_audio_request(self, request: _TTSRequest, reason: str) -> None:
        """本段无需/无法发音但不算故障：正常走完回调让流程推进，不向 UI 报错。

        与 _fail_audio_request 相比，不 emit _failed/error_occurred，只记 debug；
        用于纯标点段（无可发音内容）与服务端单段 tts failed 的优雅降级。
        """
        debug_log("TTS", "跳过本段合成", {"text": request.text, "reason": reason})
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
        debug_log(
            "TTS",
            "预生成音频加入播放队列",
            {
                "text": handle.text,
                "tone": handle.tone,
                "audio_path": handle.audio_path,
                "pending_audio": len(self._pending_audio),
                "prepared": True,
                "play_requested": handle.play_requested,
                "current_audio": str(self._current_audio) if self._current_audio else None,
            },
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

        debug_log(
            "TTS",
            "开始播放音频",
            {
                "text": text,
                "backend": self._playback_backend,
                "audio_path": str(audio_path),
                "file_size": audio_path.stat().st_size if audio_path.exists() else 0,
                "pending_audio": len(self._pending_audio),
            },
        )

        # 播放前最后一道检查：文件可能在排队期间被清理/损坏；
        # 坏条目直接跳过并继续播放队列，绝不交给播放器去卡死
        audio_issue = _audio_checks._verify_generated_audio(audio_path)
        if audio_issue is not None:
            debug_log(
                "TTS",
                "播放前音频校验失败，跳过该条目",
                {"audio_path": str(audio_path), "issue": audio_issue},
            )
            self._finish_current_audio("invalid_audio")
            self._play_next()
            return

        if self._playback_backend == _TTS_PLAYBACK_BACKEND_AUDIO_SINK:
            self._play_next_with_sink()
        else:
            self._play_next_with_media_player()

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

        # 销毁旧 sink player
        if self._sink_player is not None:
            try:
                self._sink_player.finished.disconnect()
                self._sink_player.started.disconnect()
                self._sink_player.error.disconnect()
            except Exception:
                pass
            self._sink_player = None

        self._sink_player = _create_audio_sink_player(self)
        self._sink_player.started.connect(self._on_sink_started)
        self._sink_player.finished.connect(self._on_sink_finished)
        self._sink_player.error.connect(self._on_sink_error)

        debug_log(
            "TTS",
            "AudioSink: 尝试启动播放",
            {"audio_path": str(audio_path), "token": playback_finish_token},
        )
        ok = self._sink_player.start(audio_path)
        if not ok:
            # sink 不支持此格式，fallback 到 QMediaPlayer
            debug_log(
                "TTS",
                "AudioSink: fallback 到 QMediaPlayer",
                {
                    "fallback_reason": "sink_start_returned_false",
                    "audio_path": str(audio_path),
                },
            )
            self._sink_player = None
            self._play_next_with_media_player()
            return

        # sink 后端也设置兜底定时器（作为额外安全网）
        self._schedule_current_audio_finish_fallback(
            audio_path,
            playback_finish_token,
        )

    @Slot()
    def _on_sink_started(self) -> None:
        """AudioSinkPlayer 开始播放回调。"""
        debug_log(
            "TTS",
            "AudioSink: 播放开始回调",
            {"audio_path": str(self._current_audio) if self._current_audio else ""},
        )
        self._emit_current_started()

    @Slot(str, str)
    def _on_sink_finished(self, reason: str, audio_path_str: str) -> None:
        """AudioSinkPlayer 播放完成回调。"""
        debug_log(
            "TTS",
            "AudioSink: 播放完成回调",
            {"reason": reason, "audio_path": audio_path_str},
        )
        try:
            self._finish_current_audio(reason)
            self._play_next()
        except Exception as exc:
            debug_log(
                "TTS",
                "AudioSink: 完成回调异常",
                {"error": str(exc), "exception_type": type(exc).__name__},
            )
            self._finish_current_audio("callback_error")
            self._play_next()

    @Slot(str)
    def _on_sink_error(self, message: str) -> None:
        """AudioSinkPlayer 播放错误回调。"""
        debug_log(
            "TTS",
            "AudioSink: 播放错误回调",
            {"error": message, "audio_path": str(self._current_audio) if self._current_audio else ""},
        )
        self._log_error(message)
        try:
            self._finish_current_audio("sink_error")
            self._play_next()
        except Exception as exc:
            debug_log(
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
        debug_log("TTS", "Qt 多媒体播放器已初始化")

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
        debug_log("TTS", "音频开始回调", {"audio_path": self._current_audio})
        self._started.emit(self._current_started)

    def _finish_current_audio(self, reason: str = "normal") -> None:
        """统一 finish 入口，保证幂等性。"""
        if self._finishing_audio:
            debug_log(
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
            debug_log(
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
            # 用保守上限兜住，绝不能因解析失败而放弃兜底导致对话流程挂起
            debug_log(
                "TTS",
                "无法读取音频时长，使用上限时长兜底",
                {"audio_path": audio_path, "delay_ms": _AUDIO_FINISH_FALLBACK_MAX_MS},
            )
            duration_ms = _AUDIO_FINISH_FALLBACK_MAX_MS
        delay_ms = max(
            _AUDIO_FINISH_FALLBACK_MIN_MS,
            min(duration_ms + _AUDIO_FINISH_FALLBACK_GRACE_MS, _AUDIO_FINISH_FALLBACK_MAX_MS),
        )
        debug_log(
            "TTS",
            "安排音频播放完成兜底",
            {
                "audio_path": audio_path,
                "duration_ms": duration_ms,
                "delay_ms": delay_ms,
                "token": playback_finish_token,
            },
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
            debug_log(
                "TTS",
                "音频播放完成兜底已过期，跳过",
                {
                    "audio_path": str(audio_path),
                    "token": playback_finish_token,
                },
            )
            return
        debug_log(
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

    def _schedule_audio_cleanup(self, audio_path: Path, attempt: int = 1) -> None:
        debug_log("TTS", "计划清理临时音频", {"audio_path": audio_path, "attempt": attempt})
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
            debug_log("TTS", "临时音频清理完成", {"audio_path": audio_path, "attempt": attempt})
        except OSError as exc:
            if attempt < _AUDIO_CLEANUP_MAX_ATTEMPTS:
                self._schedule_audio_cleanup(audio_path, attempt + 1)
                return
            self._log_error(f"临时音频清理失败：{exc}")

    def close(self) -> None:
        with self._request_lock:
            self._closed = True
            self._pending_requests.clear()
        self._clear_pending_audio()
        if self._current_audio is not None:
            self._finish_current_audio("provider_closed")
        self._release_player_source()
        self._stop_local_service()

    def _is_closed(self) -> bool:
        with self._request_lock:
            return self._closed

    def _clear_pending_audio(self) -> None:
        pending_audio = self._pending_audio
        self._pending_audio = []
        for audio_path, _on_started, _on_finished, _prepared_audio, _text in pending_audio:
            self._schedule_audio_cleanup(audio_path)

    def detach_local_service(self) -> None:
        """交出本地服务进程所有权，供新的 Provider 在后台接管。"""

        self._server_process = None

    def _stop_local_service(self) -> None:
        process = self._server_process
        if process is None:
            return
        if process.poll() is not None:
            self._server_process = None
            return
        debug_log("TTS", "关闭本地 TTS 服务进程", {"pid": process.pid, "provider": self.settings.provider})
        try:
            _terminate_process_tree(process, timeout=5)
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "本地 TTS 服务正常关闭失败，尝试强制结束", {"pid": process.pid, "error": str(exc)})
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception as kill_exc:  # noqa: BLE001
                debug_log("TTS", "本地 TTS 服务强制结束失败", {"pid": process.pid, "error": str(kill_exc)})
        finally:
            self._server_process = None


class GenieTTSProvider(GPTSoVITSTTSProvider):
    """Genie TTS CPU 推理 Provider，复用现有队列、预生成和播放器链路。"""

    def __init__(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        adopt_existing_service: bool = True,
    ) -> None:
        super().__init__(
            settings,
            base_dir=base_dir,
            adopt_existing_service=adopt_existing_service,
        )
        self._loaded_character_name: str | None = None
        self._reference_audio_key: str | None = None

    def _request_audio(self, tts_request: _TTSRequest) -> None:
        set_interaction_id(tts_request.interaction_id)
        try:
            if _provider_is_closed(self):
                debug_log("TTS", "Provider 已关闭，跳过 Genie 音频请求", {"text": tts_request.text})
                return
            if tts_request.prepared_audio is not None and tts_request.prepared_audio.cancelled:
                debug_log("TTS", "请求已取消，跳过 Genie 音频生成", {"text": tts_request.text})
                return

            # 与 GPT-SoVITS 同理：纯标点/符号段无可发音内容，提前静默跳过。
            if not _is_voiceable_text(tts_request.text):
                debug_log("TTS", "文本无可发音内容，跳过 Genie 合成", {"text": tts_request.text})
                self._skip_audio_request(tts_request, "无可发音内容")
                return

            fail = lambda message: self._fail_audio_request(tts_request, message)
            if not self._ensure_service_available(fail):
                return

            reference = self._select_reference(tts_request.tone)
            if not self._ensure_character_model(reference.ref_lang, fail):
                return
            if not self._ensure_reference_audio(reference, fail):
                return

            payload = {
                "character_name": _encode_genie_character_name(self._genie_character_name()),
                "text": tts_request.text,
                "split_sentence": False,
            }
            debug_log(
                "TTS",
                "发送 Genie TTS 请求",
                {
                    "api_url": self.settings.api_url,
                    "text": tts_request.text,
                    "tone": tts_request.tone,
                    "payload": payload,
                },
            )
            try:
                audio_data = self._post_json_and_read_bytes(
                    "tts",
                    payload,
                    timeout=max(self.settings.timeout_seconds, 120),
                )
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                fail(f"Genie TTS HTTP {exc.code}: {error_body}")
                return
            except urllib.error.URLError as exc:
                fail(f"Genie TTS 请求失败，请确认服务已启动并可访问 {self.settings.api_url}：{exc.reason}")
                return
            except TimeoutError:
                fail("Genie TTS 请求超时。")
                return

            if not audio_data:
                fail("Genie TTS 返回了空音频。")
                return

            with tempfile.NamedTemporaryFile(
                prefix="sakura_genie_tts_",
                suffix=".wav",
                delete=False,
                dir=str(self._tts_cache_dir),
            ) as audio_file:
                audio_path = Path(audio_file.name)
            try:
                if not _write_genie_audio(audio_data, audio_path):
                    fail("Genie TTS 返回的音频无法转换为 WAV。")
                    self._schedule_audio_cleanup(audio_path)
                    return
            except OSError as exc:
                fail(f"Genie TTS 写入临时音频失败：{exc}")
                self._schedule_audio_cleanup(audio_path)
                return

            debug_log("TTS", "Genie 临时音频已写入", {"audio_path": audio_path, "bytes": len(audio_data)})
            audio_issue = _audio_checks._verify_generated_audio(audio_path)
            if audio_issue is not None:
                debug_log("TTS", "Genie 生成音频校验失败", {"audio_path": str(audio_path), "issue": audio_issue})
                self._fail_audio_request(tts_request, f"Genie TTS 生成的音频无效（{audio_issue}）。")
                self._schedule_audio_cleanup(audio_path)
                return
            if tts_request.prepared_audio is None:
                self._audio_ready.emit(
                    str(audio_path),
                    tts_request.on_started,
                    tts_request.on_finished,
                    tts_request.text,
                )
            else:
                self._prepared_audio_ready.emit(tts_request.prepared_audio, str(audio_path))
        finally:
            with self._request_lock:
                self._request_running = False
            self._start_next_request()

    def ensure_ready(self) -> tuple[bool, str]:
        """启动并检测 Genie TTS 服务，同时预加载角色模型与参考音频。"""

        try:
            self.settings.validate()
        except _TTSConfigError as exc:
            return False, str(exc)

        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "Genie TTS 服务不可用。"
        reference = self._select_reference(DEFAULT_TONE)
        if not self._ensure_character_model(reference.ref_lang, messages.append):
            return False, messages[-1] if messages else "Genie TTS 角色模型加载失败。"
        if not self._ensure_reference_audio(reference, messages.append):
            return False, messages[-1] if messages else "Genie TTS 参考音频设置失败。"
        return True, "TTS 服务已就绪。"

    def _ensure_service_available(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if _provider_is_closed(self):
            debug_log("TTS", "Provider 已关闭，跳过 Genie 服务探测", {"api_url": self.settings.api_url})
            return False
        if self._service_checked:
            debug_log("TTS", "Genie 服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if endpoint is None:
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "invalid_api_url"})
            fail_callback(f"Genie TTS 服务地址无效：{self.settings.api_url}")
            return False
        host, port = endpoint

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        _set_service_state(self, TTSServiceState.PROBING)
        if GenieTTSProvider._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            if GenieTTSProvider._probe_genie_api(self, timeout):
                GenieTTSProvider._adopt_existing_local_service(self, host, port)
                self._service_checked = True
                _set_service_state(self, TTSServiceState.READY, {"via": "probe"})
                debug_log("TTS", "Genie 服务探测成功", {"api_url": self.settings.api_url})
                return True
            # 端口通但不是 Genie（典型：被 GPT-SoVITS 占用 9880）→ 尝试备用端口
            fallback_port = GenieTTSProvider._select_fallback_port(self, host, port, timeout)
            if fallback_port is None:
                _set_service_state(self, TTSServiceState.FAILED, {"reason": "port_conflict"})
                fail_callback(
                    f"端口 {port} 上的服务不是 Genie TTS，且未找到可用的本地备用端口。"
                    f"请将 Genie API URL 改为 {_DEFAULT_GENIE_TTS_API_URL} 或检查占用服务。"
                )
                return False
            old_api_url = self.settings.api_url
            self.settings = replace(self.settings, api_url=_replace_url_port(self.settings.api_url, fallback_port))
            port = fallback_port
            debug_log(
                "TTS",
                "Genie 端口被其他 TTS 服务占用，已切换到备用端口",
                {"old_api_url": old_api_url, "api_url": self.settings.api_url},
            )
            if (
                GenieTTSProvider._probe_service_port(self, host, port, timeout, purpose=probe_purpose)
                and GenieTTSProvider._probe_genie_api(self, timeout)
            ):
                GenieTTSProvider._adopt_existing_local_service(self, host, port)
                self._service_checked = True
                _set_service_state(self, TTSServiceState.READY, {"via": "fallback_port"})
                debug_log("TTS", "Genie 备用端口已有可用服务", {"api_url": self.settings.api_url})
                return True

        if self.settings.work_dir is None:
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "service_unreachable"})
            fail_callback(f"Genie TTS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        _set_service_state(self, TTSServiceState.STARTING)
        if _provider_is_closed(self):
            return False
        if not GenieTTSProvider._start_local_service(self, fail_callback, host, port):
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "start_failed"})
            return False

        def _ready() -> bool:
            return GenieTTSProvider._probe_service_port(
                self, host, port, timeout, purpose="startup_wait"
            ) and GenieTTSProvider._probe_genie_api(self, timeout)

        if not _wait_local_service_ready(
            provider=self,
            service_name="Genie TTS",
            ready_check=_ready,
            fail_callback=fail_callback,
            timeout_seconds=self.settings.timeout_seconds,
        ):
            return False
        self._service_checked = True
        _set_service_state(self, TTSServiceState.READY, {"via": "local_start"})
        debug_log(
            "TTS",
            "本地 Genie TTS 服务启动并探测成功",
            {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
        )
        return True

    def _start_local_service(self, fail_callback: Callable[[str], None], host: str, port: int) -> bool:
        if _provider_is_closed(self):
            return False
        work_dir = self.settings.work_dir
        if work_dir is None:
            return False
        work_dir = work_dir.resolve()
        runtime_dir = work_dir / "runtime"
        python_exe = find_usable_runtime_python(runtime_dir)
        if not work_dir.is_dir():
            fail_callback(f"Genie TTS 工作目录不存在：{work_dir}")
            return False
        if python_exe is None:
            fail_callback(f"Genie TTS 运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False

        if self._server_process is not None and self._server_process.poll() is None:
            debug_log("TTS", "本地 Genie TTS 进程已启动，跳过重复启动", {"work_dir": str(work_dir)})
            return True

        try:
            kwargs: dict[str, object] = {
                "cwd": str(work_dir),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            log_path = _local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 Genie TTS：{work_dir}\n")
                log_file.flush()
            self._server_process = subprocess.Popen(
                _build_genie_start_command(python_exe, host, port),
                **kwargs,
            )
            if _provider_is_closed(self):
                self._stop_local_service()
                return False
            _start_local_tts_output_reader(
                self._server_process,
                log_path,
                "Genie TTS",
            )
        except OSError as exc:
            fail_callback(f"Genie TTS 服务启动失败：{exc}")
            return False

        debug_log(
            "TTS",
            "已启动本地 Genie TTS 服务",
            {"work_dir": str(work_dir), "pid": self._server_process.pid, "api_url": self.settings.api_url},
        )
        return True

    def _probe_genie_api(self, timeout: int) -> bool:
        return _probe_genie_api_url(self.settings.api_url, timeout)

    def _select_fallback_port(self, host: str, occupied_port: int, timeout: int) -> int | None:
        if self.settings.work_dir is None or not _is_loopback_host(host):
            return None
        for candidate_port in range(max(1, occupied_port + 1), min(65535, occupied_port + 20) + 1):
            candidate_url = _replace_url_port(self.settings.api_url, candidate_port)
            if _probe_tcp_port(host, candidate_port, timeout):
                if _probe_genie_api_url(candidate_url, timeout):
                    return candidate_port
                continue
            if _can_bind_local_port(host, candidate_port):
                return candidate_port
        return None

    def _ensure_character_model(
        self,
        language: str,
        fail_callback: Callable[[str], None],
    ) -> bool:
        character_name = self._genie_character_name()
        if self._loaded_character_name == character_name:
            return True
        if not self._ensure_onnx_model_dir(fail_callback):
            return False
        if self.settings.onnx_model_dir is None:
            fail_callback("Genie TTS 缺少 ONNX 模型目录。")
            return False

        payload = {
            "character_name": _encode_genie_character_name(character_name),
            "onnx_model_dir": str(self.settings.onnx_model_dir),
            "language": language or self.settings.ref_lang or "ja",
        }
        try:
            self._post_json_and_read_bytes("load_character", payload, timeout=20)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            fail_callback(f"Genie TTS 加载角色模型失败 HTTP {exc.code}: {error_body}")
            return False
        except urllib.error.URLError as exc:
            fail_callback(f"Genie TTS 加载角色模型失败：{exc.reason}")
            return False
        except TimeoutError:
            fail_callback("Genie TTS 加载角色模型超时。")
            return False

        self._loaded_character_name = character_name
        return True

    def _ensure_reference_audio(
        self,
        reference: _ToneReference,
        fail_callback: Callable[[str], None],
    ) -> bool:
        character_name = self._genie_character_name()
        key = f"{character_name}|{reference.ref_audio_path}|{reference.ref_text}|{reference.ref_lang}"
        if self._reference_audio_key == key:
            return True
        payload = {
            "character_name": _encode_genie_character_name(character_name),
            "audio_path": str(reference.ref_audio_path),
            "audio_text": reference.ref_text,
            "language": reference.ref_lang,
        }
        try:
            self._post_json_and_read_bytes("set_reference_audio", payload, timeout=20)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            fail_callback(f"Genie TTS 设置参考音频失败 HTTP {exc.code}: {error_body}")
            return False
        except urllib.error.URLError as exc:
            fail_callback(f"Genie TTS 设置参考音频失败：{exc.reason}")
            return False
        except TimeoutError:
            fail_callback("Genie TTS 设置参考音频超时。")
            return False
        self._reference_audio_key = key
        return True

    def _ensure_onnx_model_dir(self, fail_callback: Callable[[str], None]) -> bool:
        onnx_dir = self.settings.onnx_model_dir
        if onnx_dir is not None and _has_onnx_files(onnx_dir):
            return True
        if onnx_dir is None:
            fail_callback("Genie TTS 缺少 ONNX 模型目录。")
            return False
        if self.settings.work_dir is None:
            fail_callback(f"Genie TTS ONNX 模型不存在：{onnx_dir}，且未配置工作目录用于转换。")
            return False
        if self.settings.gpt_model_path is None or self.settings.sovits_model_path is None:
            fail_callback(f"Genie TTS ONNX 模型不存在：{onnx_dir}，且角色缺少 GPT/SoVITS 权重用于转换。")
            return False

        converter_script = _resolve_genie_converter_script(self.settings.work_dir)
        if converter_script is None:
            fail_callback(f"Genie TTS 工作目录缺少 convert.py/convery.py：{self.settings.work_dir}")
            return False
        runtime_dir = converter_script.parent / "runtime"
        python_exe = find_usable_runtime_python(runtime_dir)
        if python_exe is None:
            fail_callback(f"Genie TTS 转换运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False

        onnx_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(python_exe),
            str(converter_script),
            "--pth",
            str(self.settings.sovits_model_path),
            "--ckpt",
            str(self.settings.gpt_model_path),
            "--out",
            str(onnx_dir),
        ]
        kwargs: dict[str, object] = {
            "args": cmd,
            "cwd": str(converter_script.parent),
            "capture_output": True,
            "text": True,
            "timeout": max(600, self.settings.timeout_seconds),
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
        try:
            result = subprocess.run(**kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            fail_callback(f"Genie TTS ONNX 转换失败：{exc}")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}")[:2000]
            fail_callback(f"Genie TTS ONNX 转换失败：{detail}")
            return False
        if not _has_onnx_files(onnx_dir):
            fail_callback(f"Genie TTS ONNX 转换完成但未生成 .onnx 文件：{onnx_dir}")
            return False
        return True

    def _post_json_and_read_bytes(self, endpoint: str, payload: dict[str, object], *, timeout: int) -> bytes:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=_build_genie_endpoint_url(self.settings.api_url, endpoint),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _genie_character_name(self) -> str:
        return self.settings.character_name.strip() or "sakura"


def _find_running_local_tts_process(
    settings: _GPTSoVITSTTSSettings,
    port: int,
) -> _AttachedLocalProcess | None:
    if settings.work_dir is None:
        return None
    if settings.provider not in {
        _TTS_PROVIDER_GPT_SOVITS,
        _TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        _TTS_PROVIDER_GENIE,
    }:
        return None

    pid = _find_listening_tcp_pid(port)
    if pid is None or pid == os.getpid():
        return None

    command_line = _query_process_command_line(pid)
    if not command_line or not _command_line_matches_local_tts(settings, command_line, port):
        return None
    return _AttachedLocalProcess(pid)


def _find_listening_tcp_pid(port: int) -> int | None:
    if sys.platform != "win32":
        return _find_listening_tcp_pid_lsof(port)

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            **_windows_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地监听端口失败", {"port": port, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        state = parts[-2].upper()
        if state != "LISTENING" or _netstat_address_port(parts[1]) != port:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            return None
    return None


def _find_listening_tcp_pid_lsof(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地监听端口失败", {"port": port, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.startswith("p"):
            continue
        try:
            return int(line[1:])
        except ValueError:
            return None
    return None


def _netstat_address_port(address: str) -> int | None:
    if address.startswith("["):
        _host, separator, port_text = address.rpartition("]:")
    else:
        _host, separator, port_text = address.rpartition(":")
    if not separator:
        return None
    try:
        return int(port_text)
    except ValueError:
        return None


def _query_process_command_line(pid: int) -> str | None:
    if sys.platform == "win32":
        return _query_windows_process_command_line(pid)
    return _query_posix_process_command_line(pid)


def _query_windows_process_command_line(pid: int) -> str | None:
    script = f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            **_windows_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地 TTS 进程命令行失败", {"pid": pid, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _query_posix_process_command_line(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地 TTS 进程命令行失败", {"pid": pid, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _command_line_matches_local_tts(
    settings: _GPTSoVITSTTSSettings,
    command_line: str,
    port: int,
) -> bool:
    work_dir = settings.work_dir
    if work_dir is None:
        return False

    normalized_command = _normalize_process_text(command_line)
    configured_python = settings.python_path.resolve() if settings.python_path is not None else None
    python_exe = _normalize_process_text(str(configured_python or work_dir.resolve() / "runtime" / "python.exe"))
    if python_exe not in normalized_command:
        return False

    if settings.provider == _TTS_PROVIDER_GENIE:
        return "genie_tts.start_server" in normalized_command and f"port={int(port)}" in normalized_command

    if settings.provider in {_TTS_PROVIDER_GPT_SOVITS, _TTS_PROVIDER_CUSTOM_GPT_SOVITS}:
        api_script = _normalize_process_text(str(work_dir.resolve() / "api_v2.py"))
        return api_script in normalized_command

    return False


def _normalize_process_text(value: str) -> str:
    return value.replace("/", "\\").casefold()


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=3,
                **_windows_no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and str(int(pid)) in result.stdout

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid_tree(pid: int, timeout: int) -> None:
    if sys.platform == "win32":
        _run_windows_taskkill(pid, timeout)
        return
    os.kill(pid, 15)


def _run_windows_taskkill(pid: int, timeout: int) -> None:
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "check": False,
        "timeout": timeout,
    }
    kwargs.update(_windows_no_window_kwargs())
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], **kwargs)


def _windows_no_window_kwargs() -> dict[str, object]:
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")}
    return {}


def _terminate_process_tree(process: _LocalProcessHandle, timeout: int) -> None:
    pid = getattr(process, "pid", None)
    if sys.platform == "win32" and pid is not None:
        try:
            _run_windows_taskkill(pid, timeout)
            process.wait(timeout=timeout)
            if process.poll() is not None:
                return
        except (OSError, subprocess.TimeoutExpired) as exc:
            debug_log("TTS", "taskkill 清理本地 TTS 进程树失败，改用 Popen 关闭", {"pid": pid, "error": str(exc)})

    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _build_genie_start_command(python_exe: Path, host: str, port: int) -> list[str]:
    start_host = host.strip() or "127.0.0.1"
    start_code = (
        "import os, sys\n"
        "base_dir = os.getcwd()\n"
        "os.environ['GENIE_DATA_DIR'] = os.path.join(base_dir, 'GenieData')\n"
        "sys.path.insert(0, os.path.join(base_dir, 'runtime'))\n"
        "import genie_tts\n"
        f"genie_tts.start_server(host={start_host!r}, port={int(port)}, workers=1)\n"
    )
    return [str(python_exe), "-c", start_code]


def _build_gpt_sovits_start_command(
    python_exe: Path,
    api_script: Path,
    settings: _GPTSoVITSTTSSettings,
) -> list[str]:
    cmd = [str(python_exe), str(api_script)]
    if settings.tts_config_path is not None:
        cmd.extend(["-c", str(settings.tts_config_path)])

    parsed_url = urlparse(settings.api_url)
    if parsed_url.hostname:
        host = "127.0.0.1" if parsed_url.hostname == "localhost" else parsed_url.hostname
        cmd.extend(["-a", host])
    try:
        port = parsed_url.port
    except ValueError:
        port = None
    if port is not None:
        cmd.extend(["-p", str(port)])
    return cmd


def _local_tts_subprocess_env(python_exe: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if python_exe is not None:
        bin_dir = str(python_exe.parent)
        path = env.get("PATH", "")
        if path:
            env["PATH"] = f"{bin_dir}{os.pathsep}{path}"
        else:
            env["PATH"] = bin_dir
    return env


def _format_gpt_sovits_http_error(status_code: int, error_body: str) -> str:
    if status_code == 400 and _looks_like_charmap_encode_error(error_body):
        return (
            "GPT-SoVITS HTTP 400: 本地 GPT-SoVITS 运行时编码不是 UTF-8，"
            "中文或日文文本写入时触发 charmap 编码错误。"
            "Sakura 启动本地服务时已启用 UTF-8 标准输入输出；如果仍然失败，"
            "请关闭当前 GPT-SoVITS 服务后由 Sakura 重新启动，或手动检查运行时编码。"
            f"\n原始响应：{error_body}"
        )
    return f"GPT-SoVITS HTTP {status_code}: {error_body}"


def _looks_like_charmap_encode_error(error_body: str) -> bool:
    normalized = error_body.lower()
    return "charmap" in normalized and "can't encode" in normalized


def _is_restartable_local_tts_service_failure(status_code: int, error_body: str) -> bool:
    """本地 TTS 服务自身进入坏状态，重启服务比跳过单段更正确。"""
    if status_code != 400:
        return False
    normalized = error_body.lower()
    return "tts failed" in normalized and (
        "broken pipe" in normalized or "[errno 32]" in normalized
    )


def _is_soft_synth_failure(status_code: int, error_body: str) -> bool:
    """判断是否为可静默降级的单段合成失败，区别于需提示用户的服务/配置故障。

    GPT-SoVITS api_v2 在推理异常时统一返回 400 + {"message":"tts failed",...}，
    多由个别文本段触发（如归一化后为空、含服务端不支持的内容），属偶发且无害，
    文本已照常显示，按单段静默跳过即可。charmap 编码错误是运行时配置问题，
    会持续影响所有中日文合成，仍需保留提示，故在此排除。
    """
    if status_code != 400:
        return False
    if _looks_like_charmap_encode_error(error_body):
        return False
    if _is_restartable_local_tts_service_failure(status_code, error_body):
        return False
    return "tts failed" in error_body.lower()


def _is_voiceable_text(text: str) -> bool:
    """文本是否含可发音内容。纯标点/emoji/符号归一化后音素为空，会触发服务端
    [Errno 22] Invalid argument，提前判定可避免无谓的失败往返。"""
    return bool(_VOICEABLE_CHAR_RE.search(text))


def _probe_tcp_port(host: str, port: int, timeout: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (TimeoutError, OSError):
        return False
    return True


def _probe_gpt_sovits_http(api_url: str, timeout: int) -> bool:
    """探测 GPT-SoVITS HTTP 层是否就绪（TCP 通后 HTTP 可能仍在初始化）。"""
    parsed = urlparse(api_url)
    base_path = parsed.path.rsplit("/", 1)[0]
    probe_url = urlunparse(parsed._replace(path=base_path or "/", query=""))
    request = urllib.request.Request(url=probe_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            pass
    except urllib.error.HTTPError:
        # 任何 HTTP 状态码都说明服务 HTTP 层已就绪
        pass
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True


def _probe_genie_api_url(api_url: str, timeout: int) -> bool:
    request = urllib.request.Request(
        url=_build_genie_endpoint_url(api_url, "openapi.json"),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        debug_log("TTS", "Genie API 端点探测失败", {"api_url": api_url, "error": str(exc)})
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        debug_log("TTS", "Genie API 端点探测返回非 JSON", {"api_url": api_url})
        return False
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return False
    has_load_character = any(str(path).rstrip("/").endswith("/load_character") for path in paths)
    has_tts = any(str(path).rstrip("/").endswith("/tts") for path in paths)
    return has_load_character and has_tts


def _replace_url_port(api_url: str, port: int) -> str:
    parsed_url = urlparse(api_url)
    host = parsed_url.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host_text = f"[{host}]"
    else:
        host_text = host
    auth = ""
    if parsed_url.username:
        auth = parsed_url.username
        if parsed_url.password:
            auth += f":{parsed_url.password}"
        auth += "@"
    netloc = f"{auth}{host_text}:{int(port)}"
    return urlunparse(parsed_url._replace(netloc=netloc))


def _is_loopback_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _can_bind_local_port(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host.strip().lower() == "localhost" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind((bind_host, port))
    except OSError:
        return False
    return True


def _tts_service_display_name(provider: str) -> str:
    normalized = _normalize_tts_provider_setting(provider)
    if normalized == _TTS_PROVIDER_GENIE:
        return "Genie TTS"
    return "GPT-SoVITS"


def _probe_failure_message(service_name: str, purpose: str, *, timeout: bool) -> str:
    if purpose == "startup_wait":
        return f"本地 {service_name} 服务尚未就绪，继续等待"
    if purpose == "pre_start_check":
        return f"{service_name} 服务当前未响应，准备尝试启动本地服务"
    return "服务探测超时" if timeout else "服务不可用"


def _local_tts_service_log_path(provider: str, base_dir: Path | None = None) -> Path:
    """返回本地 TTS 子进程启动日志路径。

    旧实现基于 Path.cwd()，工作目录与安装目录不一致时日志会写错位置；
    现统一走 StoragePaths（base_dir 缺省时与缓存目录同样按 __file__ 推算根）。
    """

    return StoragePaths(_resolve_project_root(base_dir)).tts_service_log(provider)


def _start_local_tts_output_reader(
    process: subprocess.Popen[str],
    log_path: Path,
    provider: str,
) -> None:
    stream = getattr(process, "stdout", None)
    if stream is None:
        return
    thread = threading.Thread(
        target=_read_local_tts_output,
        args=(stream, log_path, provider),
        daemon=True,
    )
    thread.start()


def _iter_tts_service_segments(stream):  # type: ignore[no-untyped-def]
    """逐段产出服务输出。

    tqdm 进度条用 \r 原地刷新且长时间不输出 \n，按行读取要等进度条整条结束
    才能一次性收到，无法实时展示推理进度，因此优先按字符读取并以 \r/\n 切段；
    不支持 read() 的流（如测试桩）退回按行迭代。
    """
    if hasattr(stream, "read"):
        buffer = ""
        while True:
            chunk = stream.read(1)
            if not chunk:
                break
            if chunk in ("\r", "\n"):
                if buffer:
                    yield buffer
                buffer = ""
                continue
            buffer += chunk
        if buffer:
            yield buffer
        return
    for raw_line in stream:
        yield str(raw_line)


def _read_local_tts_output(stream, log_path: Path, provider: str) -> None:  # type: ignore[no-untyped-def]
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            for segment in _iter_tts_service_segments(stream):
                line = segment.rstrip("\r\n")
                if not line.strip():
                    continue
                log_file.write(f"{line}\n")
                log_file.flush()
                record_tts_service_output(provider, line)
    except Exception as exc:  # noqa: BLE001
        debug_log("TTS", "本地 TTS 服务输出读取失败", {"provider": provider, "error": str(exc)})
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _resolve_request_text_lang(text: str, configured_text_lang: str) -> str:
    """英文混入中日韩文本时切到 auto，避免 GPT-SoVITS 按单语 BERT 处理失败。"""
    normalized = configured_text_lang.strip().lower()
    if normalized in _CJK_TEXT_LANGS and _LATIN_LETTER_RE.search(text):
        return "auto_yue" if normalized in {"yue", "all_yue"} else "auto"
    return normalized or "ja"


def _build_tts_endpoint_url(base_url: str, endpoint: str, query: dict[str, str]) -> str:
    parsed_url = urlparse(base_url)
    base_path = parsed_url.path.rsplit("/", 1)[0]
    endpoint_path = f"{base_path}/{endpoint}" if base_path else f"/{endpoint}"
    return urlunparse(
        parsed_url._replace(
            path=endpoint_path,
            query=urlencode(query),
        )
    )


def _build_genie_endpoint_url(base_url: str, endpoint: str) -> str:
    parsed_url = urlparse(base_url)
    path = parsed_url.path.strip("/")
    if not path:
        endpoint_path = f"/{endpoint}"
    else:
        parts = path.split("/")
        if parts[-1] == "tts":
            parts[-1] = endpoint
        elif parts[-1] != endpoint:
            parts.append(endpoint)
        endpoint_path = "/" + "/".join(parts)
    return urlunparse(parsed_url._replace(path=endpoint_path, query=""))


def _encode_genie_character_name(name: str) -> str:
    if not name:
        return ""
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")


def _has_onnx_files(path: Path) -> bool:
    return path.is_dir() and any(child.suffix.lower() == ".onnx" for child in path.glob("*.onnx"))


def _resolve_genie_converter_script(work_dir: Path) -> Path | None:
    base_path = work_dir.resolve()
    if base_path.suffix.lower() == ".py":
        return base_path if base_path.exists() else None
    for name in ("convert.py", "convery.py"):
        candidate = base_path / name
        if candidate.is_file():
            return candidate
    return None


def _write_genie_audio(audio_data: bytes, output_path: Path) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if audio_data[:4] == b"RIFF":
        output_path.write_bytes(audio_data)
        return _audio_checks._is_valid_wav_file(output_path)
    return _write_raw_float_or_pcm_as_wav(audio_data, output_path, sample_rate=32000)


def _write_raw_pcm_as_wav(raw_bytes: bytes, output_path: Path, *, sample_rate: int) -> bool:
    if not raw_bytes or len(raw_bytes) % 2 != 0:
        return False
    try:
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(raw_bytes)
        return _audio_checks._is_valid_wav_file(output_path)
    except (OSError, wave.Error):
        return False


def _write_raw_float_or_pcm_as_wav(raw_bytes: bytes, output_path: Path, *, sample_rate: int) -> bool:
    pcm_bytes = b""
    if len(raw_bytes) % 4 == 0:
        try:
            floats = array.array("f")
            floats.frombytes(raw_bytes)
            finite_values = [value for value in floats if math.isfinite(value)]
            if finite_values and max(abs(value) for value in finite_values) <= 2.0:
                pcm = array.array("h")
                for value in floats:
                    if not math.isfinite(value):
                        value = 0.0
                    pcm.append(int(max(-1.0, min(1.0, value)) * 32767.0))
                pcm_bytes = pcm.tobytes()
        except (OverflowError, ValueError):
            pcm_bytes = b""
    if not pcm_bytes and len(raw_bytes) % 2 == 0:
        pcm_bytes = raw_bytes
    if not pcm_bytes:
        return False
    return _write_raw_pcm_as_wav(pcm_bytes, output_path, sample_rate=sample_rate)
