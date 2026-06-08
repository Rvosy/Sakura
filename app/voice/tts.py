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
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode, urlparse, urlunparse

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.config.character_loader import CharacterProfile
from app.llm.chat_reply import DEFAULT_TONE
from app.core.debug_log import debug_log
from app.voice.runtime_compat import find_usable_runtime_python, format_runtime_python_issue


TTSCallback = Callable[[], None]
_AUDIO_CLEANUP_DELAY_MS = 200
_AUDIO_CLEANUP_MAX_ATTEMPTS = 5
_AUDIO_FINISH_FALLBACK_GRACE_MS = 1500
_AUDIO_FINISH_FALLBACK_MIN_MS = 3000
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_CJK_TEXT_LANGS = {"ja", "all_ja", "zh", "all_zh", "ko", "all_ko", "yue", "all_yue"}
TTS_PROVIDER_NONE = "none"
TTS_PROVIDER_GPT_SOVITS = "gpt-sovits"
TTS_PROVIDER_CUSTOM_GPT_SOVITS = "custom-gpt-sovits"
TTS_PROVIDER_GENIE = "genie-tts"
DEFAULT_GPT_SOVITS_API_URL = "http://127.0.0.1:9880/tts"
DEFAULT_GENIE_TTS_API_URL = "http://127.0.0.1:9881/"
_LOCAL_SERVICE_STARTUP_TIMEOUT_MAX = 180
_SUPPORTED_TTS_PROVIDERS = {
    TTS_PROVIDER_GPT_SOVITS,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
}


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


class TTSConfigError(RuntimeError):
    """TTS 配置缺失或格式错误。"""


@dataclass(frozen=True)
class ToneReference:
    tone: str
    ref_audio_path: Path
    ref_text: str
    ref_lang: str


@dataclass(frozen=True)
class GPTSoVITSTTSSettings:
    enabled: bool
    api_url: str
    ref_audio_path: Path
    ref_text_path: Path
    ref_text: str
    provider: str = TTS_PROVIDER_GPT_SOVITS
    gpt_model_path: Path | None = None
    sovits_model_path: Path | None = None
    work_dir: Path | None = None
    python_path: Path | None = None
    tts_config_path: Path | None = None
    character_name: str = ""
    onnx_model_dir: Path | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"
    timeout_seconds: int = 60
    tone_references: dict[str, list[ToneReference]] = field(default_factory=dict)

    @classmethod
    def from_character_profile(
        cls,
        character_profile: CharacterProfile,
        enabled: bool,
        api_url: str,
        ref_lang: str,
        text_lang: str,
        timeout_seconds: int,
        provider: str = TTS_PROVIDER_GPT_SOVITS,
        work_dir: Path | None = None,
        python_path: Path | None = None,
        tts_config_path: Path | None = None,
        onnx_model_dir: Path | None = None,
        validate_enabled: bool = True,
    ) -> "GPTSoVITSTTSSettings":
        provider = _normalize_tts_provider(provider, enabled)
        if character_profile.voice is None:
            settings = cls(
                provider=provider,
                enabled=enabled,
                api_url=api_url,
                ref_audio_path=character_profile.package_dir,
                ref_text_path=character_profile.package_dir,
                ref_text="",
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=timeout_seconds,
                work_dir=work_dir,
                python_path=python_path,
                tts_config_path=tts_config_path,
                character_name=character_profile.display_name or character_profile.id,
                onnx_model_dir=onnx_model_dir,
            )
            if enabled and validate_enabled:
                settings.validate()
            return settings

        voice = character_profile.voice
        tone_references = _load_tone_references(
            voice.tone_ref_path,
            character_profile.package_dir,
        )
        neutral_reference = _select_neutral_reference(tone_references)
        settings = cls(
            provider=provider,
            enabled=enabled,
            api_url=api_url,
            ref_audio_path=neutral_reference.ref_audio_path if neutral_reference else character_profile.package_dir,
            ref_text_path=neutral_reference.ref_audio_path if neutral_reference else character_profile.package_dir,
            ref_text=neutral_reference.ref_text if neutral_reference else "",
            gpt_model_path=voice.gpt_model_path,
            sovits_model_path=voice.sovits_model_path,
            work_dir=work_dir,
            python_path=python_path,
            tts_config_path=tts_config_path,
            character_name=character_profile.display_name or character_profile.id,
            onnx_model_dir=onnx_model_dir,
            ref_lang=ref_lang,
            text_lang=text_lang,
            timeout_seconds=timeout_seconds,
            tone_references=tone_references,
        )
        if enabled and validate_enabled:
            settings.validate()
        return settings

    def validate(self) -> None:
        if not self.api_url:
            raise TTSConfigError("缺少 TTS API URL。")
        if self.provider not in _SUPPORTED_TTS_PROVIDERS:
            raise TTSConfigError(f"不支持的 TTS Provider：{self.provider}")
        if self.python_path is not None and not self.python_path.exists():
            raise TTSConfigError(f"TTS Python 不存在：{self.python_path}")
        if self.tts_config_path is not None and not self.tts_config_path.exists():
            raise TTSConfigError(f"GPT-SoVITS 推理配置不存在：{self.tts_config_path}")
        if self.gpt_model_path is not None and not self.gpt_model_path.exists():
            raise TTSConfigError(f"GPT 模型不存在：{self.gpt_model_path}")
        if self.sovits_model_path is not None and not self.sovits_model_path.exists():
            raise TTSConfigError(f"SoVITS 模型不存在：{self.sovits_model_path}")
        if self.tone_references:
            for references in self.tone_references.values():
                for reference in references:
                    if not reference.ref_audio_path.exists():
                        raise TTSConfigError(f"语气参考音频不存在：{reference.ref_audio_path}")
                    if not reference.ref_text:
                        raise TTSConfigError(f"语气参考文本为空：{reference.ref_audio_path}")
                    if not reference.ref_lang:
                        raise TTSConfigError(f"语气参考语言为空：{reference.ref_audio_path}")
        else:
            if not self.ref_audio_path.exists():
                raise TTSConfigError(f"参考音频不存在：{self.ref_audio_path}")
            if not self.ref_text:
                raise TTSConfigError("缺少参考文本，请配置 GPT_SOVITS_REF_TEXT 或 GPT_SOVITS_REF_TEXT_PATH。")
        if not self.ref_lang:
            raise TTSConfigError("缺少 GPT_SOVITS_REF_LANG。")
        if not self.text_lang:
            raise TTSConfigError("缺少 GPT_SOVITS_TEXT_LANG。")

class GPTSoVITSTTSProvider(QObject):
    error_occurred = Signal(str)
    _audio_ready = Signal(str, object, object)
    _prepared_audio_ready = Signal(object, str)
    _prepared_audio_failed = Signal(object, str)
    _failed = Signal(str)
    _started = Signal(object)
    _finished = Signal(object)

    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        super().__init__()
        settings.validate()
        self.settings = settings
        self._pending_audio: list[
            tuple[Path, TTSCallback | None, TTSCallback | None, TTSPreparedAudio | None]
        ] = []
        self._current_audio: Path | None = None
        self._current_started: TTSCallback | None = None
        self._current_finished: TTSCallback | None = None
        self._current_started_emitted = False
        self._finishing_audio = False
        self._request_lock = threading.Lock()
        self._pending_requests: list[_TTSRequest] = []
        self._request_running = False
        self._tone_indices: dict[str, int] = {}
        self._weights_ready = False
        self._service_checked = False
        self._server_process: _LocalProcessHandle | None = None
        self._playback_warmup_requested = False
        self._playback_finish_token = 0

        self._audio_output: QAudioOutput | None = None
        self._player: QMediaPlayer | None = None
        self._audio_ready.connect(self._enqueue_audio)
        self._prepared_audio_ready.connect(self._store_prepared_audio)
        self._prepared_audio_failed.connect(self._fail_prepared_audio)
        self._failed.connect(self._log_error)
        self._started.connect(self._run_callback)
        self._finished.connect(self._run_callback)
        self._adopt_existing_configured_service()

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
        self._queue_request(_TTSRequest(text=text, tone=tone, prepared_audio=handle))
        return handle

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        if handle.cancelled:
            debug_log("TTS", "预生成句柄已取消，跳过播放", {"text": handle.text, "tone": handle.tone})
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
            tuple[Path, TTSCallback | None, TTSCallback | None, TTSPreparedAudio | None]
        ] = []
        for audio_path, on_started, on_finished, prepared_audio in self._pending_audio:
            if prepared_audio is handle:
                self._schedule_audio_cleanup(audio_path)
                continue
            pending_audio.append((audio_path, on_started, on_finished, prepared_audio))
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
        except TTSConfigError as exc:
            return False, str(exc)

        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 服务不可用。"
        if not self._ensure_character_weights(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 角色权重加载失败。"
        return True, "TTS 服务已就绪。"

    def _queue_request(self, request: _TTSRequest) -> None:
        with self._request_lock:
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
            if self._request_running or not self._pending_requests:
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
        try:
            if tts_request.prepared_audio is not None and tts_request.prepared_audio.cancelled:
                debug_log("TTS", "请求已取消，跳过音频生成", {"text": tts_request.text})
                return

            fail = lambda message: self._fail_audio_request(tts_request, message)
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
                        },
                    )
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                debug_log(
                    "TTS",
                    "GPT-SoVITS HTTP 失败",
                    {
                        "status": exc.code,
                        "error_body": error_body,
                    },
                )
                self._fail_audio_request(
                    tts_request,
                    _format_gpt_sovits_http_error(exc.code, error_body),
                )
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
            ) as audio_file:
                audio_file.write(audio_data)
                audio_path = audio_file.name
            debug_log("TTS", "临时音频已写入", {"audio_path": audio_path, "bytes": len(audio_data)})
            if tts_request.prepared_audio is None:
                self._audio_ready.emit(audio_path, tts_request.on_started, tts_request.on_finished)
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
        if self._service_checked:
            debug_log("TTS", "服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        parsed_url = urlparse(self.settings.api_url)
        host = parsed_url.hostname
        try:
            port = parsed_url.port
        except ValueError as exc:
            debug_log("TTS", "服务地址端口无效", {"api_url": self.settings.api_url, "reason": str(exc)})
            fail_callback(f"GPT-SoVITS 服务地址端口无效：{self.settings.api_url}")
            return False

        if port is None:
            port = 443 if parsed_url.scheme == "https" else 80
        if not host:
            debug_log("TTS", "服务地址无效", {"api_url": self.settings.api_url})
            fail_callback(f"GPT-SoVITS 服务地址无效：{self.settings.api_url}")
            return False

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        if GPTSoVITSTTSProvider._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            GPTSoVITSTTSProvider._adopt_existing_local_service(self, host, port)
            self._service_checked = True
            debug_log("TTS", "服务探测成功", {"api_url": self.settings.api_url})
            return True

        if self.settings.work_dir is None:
            fail_callback(f"GPT-SoVITS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        if not GPTSoVITSTTSProvider._start_local_service(self, fail_callback):
            return False

        # 大模型首次加载可能超过 30 秒，按用户配置等待，避免刚加载完成就被杀掉。
        deadline = time.monotonic() + max(3, min(self.settings.timeout_seconds, _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX))
        while time.monotonic() < deadline:
            exit_code = self._server_process.poll() if self._server_process is not None else None
            if exit_code is not None:
                log_path = _local_tts_service_log_path(self.settings.provider)
                fail_callback(
                    f"GPT-SoVITS 本地服务进程已退出，退出码：{exit_code}。"
                    f"请查看启动日志：{log_path}"
                )
                return False
            if GPTSoVITSTTSProvider._probe_service_port(self, host, port, timeout, purpose="startup_wait"):
                self._service_checked = True
                debug_log(
                    "TTS",
                    "本地 GPT-SoVITS 服务启动并探测成功",
                    {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
                )
                return True
            time.sleep(0.5)

        fail_callback(
            f"GPT-SoVITS 已尝试启动，但端口仍不可用：{self.settings.api_url}。"
            f"请查看启动日志：{_local_tts_service_log_path(self.settings.provider)}"
        )
        return False

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
            log_path = _local_tts_service_log_path(self.settings.provider)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, object] = {
                "cwd": str(work_dir),
                "env": _local_tts_subprocess_env(),
                "stderr": subprocess.STDOUT,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 GPT-SoVITS：{work_dir}\n")
                log_file.flush()
                kwargs["stdout"] = log_file
                self._server_process = subprocess.Popen(
                    _build_gpt_sovits_start_command(python_exe, api_script, self.settings),
                    **kwargs,
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
                "log_path": str(_local_tts_service_log_path(self.settings.provider)),
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

    def _select_reference(self, tone: str | None) -> ToneReference:
        tone_key = (tone or DEFAULT_TONE).strip() or DEFAULT_TONE
        references = self.settings.tone_references.get(tone_key)
        if not references:
            references = self.settings.tone_references.get(DEFAULT_TONE)
        if not references:
            reference = ToneReference(
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
    ) -> None:
        self._pending_audio.append((Path(audio_path), on_started, on_finished, None))
        debug_log(
            "TTS",
            "音频加入播放队列",
            {
                "audio_path": audio_path,
                "pending_audio": len(self._pending_audio),
            },
        )
        if self._current_audio is None:
            self._play_next()

    @Slot(object, str)
    def _store_prepared_audio(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        path = Path(audio_path)
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
        self._log_error(message)
        handle.failed = True
        if handle.cancelled or not handle.play_requested:
            return
        self._started.emit(handle.on_started)
        self._finished.emit(handle.on_finished)
        handle.on_started = None
        handle.on_finished = None

    @Slot(QMediaPlayer.MediaStatus)
    def _handle_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        debug_log("TTS", "播放器媒体状态变化", {"status": str(status)})
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish_current_audio()
            self._play_next()

    @Slot(QMediaPlayer.PlaybackState)
    def _handle_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        debug_log("TTS", "播放器播放状态变化", {"state": str(state)})
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._emit_current_started()
            return
        if (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self._current_audio is not None
            and self._current_started_emitted
        ):
            debug_log("TTS", "播放器停止，按当前音频播放完成处理", {"audio_path": self._current_audio})
            self._finish_current_audio()
            self._play_next()

    @Slot(QMediaPlayer.Error, str)
    def _handle_player_error(self, _error: QMediaPlayer.Error, error_text: str) -> None:
        debug_log("TTS", "播放器错误", {"error": error_text})
        self._log_error(f"音频播放失败：{error_text}")
        self._finish_current_audio()
        self._play_next()

    @Slot(str)
    def _log_error(self, message: str) -> None:
        print(f"[TTS] {message}")
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
        if request.prepared_audio is None:
            self._fail_request(message, request.on_started, request.on_finished)
            return
        self._prepared_audio_failed.emit(request.prepared_audio, message)

    def _enqueue_prepared_audio(self, handle: TTSPreparedAudio) -> None:
        if handle.cancelled or handle.enqueued or handle.audio_path is None:
            return
        handle.enqueued = True
        self._pending_audio.append(
            (handle.audio_path, handle.on_started, handle.on_finished, handle)
        )
        debug_log(
            "TTS",
            "预生成音频加入播放队列",
            {
                "text": handle.text,
                "tone": handle.tone,
                "audio_path": handle.audio_path,
                "pending_audio": len(self._pending_audio),
            },
        )
        handle.audio_path = None
        if self._current_audio is None:
            self._play_next()

    def _play_next(self) -> None:
        if self._current_audio is not None or not self._pending_audio:
            return
        self._ensure_player()
        (
            self._current_audio,
            self._current_started,
            self._current_finished,
            _prepared_audio,
        ) = self._pending_audio.pop(0)
        self._current_started_emitted = False
        self._playback_finish_token += 1
        playback_finish_token = self._playback_finish_token
        debug_log("TTS", "开始播放音频", {"audio_path": self._current_audio})
        if self._player is None:
            self._fail_audio_playback("播放器初始化失败。")
            return
        self._player.setSource(QUrl.fromLocalFile(str(self._current_audio)))
        self._player.play()
        self._schedule_current_audio_finish_fallback(
            self._current_audio,
            playback_finish_token,
        )

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
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

    def _finish_current_audio(self) -> None:
        if self._finishing_audio:
            return
        audio_path = self._current_audio
        on_finished = self._current_finished
        if audio_path is None:
            self._reset_current_audio_state()
            return
        self._finishing_audio = True
        try:
            debug_log("TTS", "音频播放完成", {"audio_path": audio_path})
            self._emit_current_started()
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
        self._current_started = None
        self._current_finished = None
        self._current_started_emitted = False

    def _schedule_current_audio_finish_fallback(self, audio_path: Path, playback_finish_token: int) -> None:
        duration_ms = _wav_duration_ms(audio_path)
        if duration_ms is None:
            debug_log("TTS", "无法读取音频时长，跳过播放完成兜底", {"audio_path": audio_path})
            return
        delay_ms = max(
            _AUDIO_FINISH_FALLBACK_MIN_MS,
            duration_ms + _AUDIO_FINISH_FALLBACK_GRACE_MS,
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
        debug_log(
            "TTS",
            "音频播放完成事件未触发，使用时长兜底完成",
            {
                "audio_path": audio_path,
                "token": playback_finish_token,
            },
        )
        self._finish_current_audio()
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
        self._release_player_source()
        self._stop_local_service()

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

    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        super().__init__(settings)
        self._loaded_character_name: str | None = None
        self._reference_audio_key: str | None = None

    def _request_audio(self, tts_request: _TTSRequest) -> None:
        try:
            if tts_request.prepared_audio is not None and tts_request.prepared_audio.cancelled:
                debug_log("TTS", "请求已取消，跳过 Genie 音频生成", {"text": tts_request.text})
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
            if tts_request.prepared_audio is None:
                self._audio_ready.emit(str(audio_path), tts_request.on_started, tts_request.on_finished)
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
        except TTSConfigError as exc:
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
        if self._service_checked:
            debug_log("TTS", "Genie 服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        parsed_url = urlparse(self.settings.api_url)
        host = parsed_url.hostname
        try:
            port = parsed_url.port
        except ValueError:
            fail_callback(f"Genie TTS 服务地址端口无效：{self.settings.api_url}")
            return False
        if port is None:
            port = 443 if parsed_url.scheme == "https" else 80
        if not host:
            fail_callback(f"Genie TTS 服务地址无效：{self.settings.api_url}")
            return False

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        if GenieTTSProvider._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            if GenieTTSProvider._probe_genie_api(self, timeout):
                GenieTTSProvider._adopt_existing_local_service(self, host, port)
                self._service_checked = True
                debug_log("TTS", "Genie 服务探测成功", {"api_url": self.settings.api_url})
                return True
            fallback_port = GenieTTSProvider._select_fallback_port(self, host, port, timeout)
            if fallback_port is None:
                fail_callback(
                    f"端口 {port} 上的服务不是 Genie TTS，且未找到可用的本地备用端口。"
                    f"请将 Genie API URL 改为 {DEFAULT_GENIE_TTS_API_URL} 或检查占用服务。"
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
                debug_log("TTS", "Genie 备用端口已有可用服务", {"api_url": self.settings.api_url})
                return True

        if self.settings.work_dir is None:
            fail_callback(f"Genie TTS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        if not GenieTTSProvider._start_local_service(self, fail_callback, host, port):
            return False

        deadline = time.monotonic() + max(3, min(self.settings.timeout_seconds, _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX))
        while time.monotonic() < deadline:
            if self._server_process is not None and self._server_process.poll() is not None:
                fail_callback(f"Genie TTS 本地服务进程已退出，退出码：{self._server_process.poll()}")
                return False
            if (
                GenieTTSProvider._probe_service_port(self, host, port, timeout, purpose="startup_wait")
                and GenieTTSProvider._probe_genie_api(self, timeout)
            ):
                self._service_checked = True
                debug_log(
                    "TTS",
                    "本地 Genie TTS 服务启动并探测成功",
                    {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
                )
                return True
            time.sleep(0.5)

        fail_callback(f"Genie TTS 已尝试启动，但端口仍不可用：{self.settings.api_url}")
        return False

    def _start_local_service(self, fail_callback: Callable[[str], None], host: str, port: int) -> bool:
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
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            self._server_process = subprocess.Popen(
                _build_genie_start_command(python_exe, host, port),
                **kwargs,
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
        reference: ToneReference,
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
    settings: GPTSoVITSTTSSettings,
    port: int,
) -> _AttachedLocalProcess | None:
    if sys.platform != "win32" or settings.work_dir is None:
        return None
    if settings.provider not in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}:
        return None

    pid = _find_listening_tcp_pid(port)
    if pid is None or pid == os.getpid():
        return None

    command_line = _query_windows_process_command_line(pid)
    if not command_line or not _command_line_matches_local_tts(settings, command_line, port):
        return None
    return _AttachedLocalProcess(pid)


def _find_listening_tcp_pid(port: int) -> int | None:
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


def _command_line_matches_local_tts(
    settings: GPTSoVITSTTSSettings,
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

    if settings.provider == TTS_PROVIDER_GENIE:
        return "genie_tts.start_server" in normalized_command and f"port={int(port)}" in normalized_command

    if settings.provider == TTS_PROVIDER_GPT_SOVITS:
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
    settings: GPTSoVITSTTSSettings,
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


def _local_tts_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _format_gpt_sovits_http_error(status_code: int, error_body: str) -> str:
    if status_code == 400 and _looks_like_charmap_encode_error(error_body):
        return (
            "GPT-SoVITS HTTP 400: 本地 GPT-SoVITS 运行时编码不是 UTF-8，"
            "中文或日文文本写入时触发 charmap 编码错误。"
            "Sakura 启动本地服务时已启用 UTF-8 环境；如果仍然失败，"
            "请关闭当前 GPT-SoVITS 服务后由 Sakura 重新启动，或手动以 UTF-8 环境重启服务。"
            f"\n原始响应：{error_body}"
        )
    return f"GPT-SoVITS HTTP {status_code}: {error_body}"


def _looks_like_charmap_encode_error(error_body: str) -> bool:
    normalized = error_body.lower()
    return "charmap" in normalized and "can't encode" in normalized


def _probe_tcp_port(host: str, port: int, timeout: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (TimeoutError, OSError):
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
    normalized = _normalize_tts_provider(provider)
    if normalized == TTS_PROVIDER_GENIE:
        return "Genie TTS"
    return "GPT-SoVITS"


def _probe_failure_message(service_name: str, purpose: str, *, timeout: bool) -> str:
    if purpose == "startup_wait":
        return f"本地 {service_name} 服务尚未就绪，继续等待"
    if purpose == "pre_start_check":
        return f"{service_name} 服务当前未响应，准备尝试启动本地服务"
    return "服务探测超时" if timeout else "服务不可用"


def _local_tts_service_log_path(provider: str) -> Path:
    """返回本地 TTS 子进程启动日志路径。"""

    safe_provider = re.sub(r"[^A-Za-z0-9_.-]+", "-", provider.strip().lower()) or "tts"
    return Path.cwd() / "data" / "logs" / f"{safe_provider}-service.log"


def _resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text.strip().strip('"').strip("'"))
    if path.is_absolute():
        return path
    return base_dir / path


def _normalize_tts_provider(provider: str, enabled: bool = True) -> str:
    if not enabled:
        return TTS_PROVIDER_NONE
    normalized = provider.strip().lower().replace("_", "-")
    if normalized in {"", "gptsovits"}:
        return TTS_PROVIDER_GPT_SOVITS
    if normalized in {"gpt-so-vits", "gpt-sovits"}:
        return TTS_PROVIDER_GPT_SOVITS
    if normalized in {"custom-gpt-sovits", "external-gpt-sovits", "custom-sovits", "external-sovits"}:
        return TTS_PROVIDER_CUSTOM_GPT_SOVITS
    if normalized in {"genie", "genie-tts", "genietts"}:
        return TTS_PROVIDER_GENIE
    if normalized in {"none", "off", "disabled", "不使用"}:
        return TTS_PROVIDER_NONE
    return normalized


def _load_tone_references(ref_path: Path | None, base_dir: Path) -> dict[str, list[ToneReference]]:
    if ref_path is None or not ref_path.exists():
        return {}

    tone_references: dict[str, list[ToneReference]] = {}
    for raw_line in ref_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            continue

        audio_text, lang, prompt_text, tone = parts
        audio_path = _resolve_path(audio_text, base_dir)
        copied_path = ref_path.parent / "tone_refs" / audio_path.name
        if copied_path.exists():
            audio_path = copied_path

        tone_key = tone or DEFAULT_TONE
        reference = ToneReference(
            tone=tone_key,
            ref_audio_path=audio_path,
            ref_text=prompt_text,
            ref_lang=_normalize_lang(lang),
        )
        tone_references.setdefault(tone_key, []).append(reference)

    return tone_references


def _select_neutral_reference(
    tone_references: dict[str, list[ToneReference]],
) -> ToneReference | None:
    neutral_references = tone_references.get(DEFAULT_TONE)
    if neutral_references:
        return neutral_references[0]
    for references in tone_references.values():
        if references:
            return references[0]
    return None


def _normalize_lang(lang: str) -> str:
    normalized = lang.strip().lower()
    if normalized == "ja":
        return "ja"
    return normalized or "ja"


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
        return _is_valid_wav_file(output_path)
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
        return _is_valid_wav_file(output_path)
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


def _wav_duration_ms(path: Path) -> int | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (OSError, wave.Error):
        return None
    if frame_rate <= 0 or frame_count < 0:
        return None
    return max(1, int(frame_count * 1000 / frame_rate))


def _is_valid_wav_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with wave.open(str(path), "rb") as wav_file:
            wav_file.getnchannels()
            wav_file.getframerate()
            wav_file.getnframes()
    except (OSError, wave.Error):
        return False
    return True
