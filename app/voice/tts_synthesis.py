"""TTS 合成队列与合成引擎（issue #94 第 3 阶段）。

从 ``app/voice/tts.py`` 抽出「合成队列」这一职责：把 speak/prepare 提交的请求串行
化、在后台 daemon 线程里走服务就绪门控 + HTTP 合成 + 写临时 wav，再把结果交回
播放端点（sink）。

线程模型保持不变——每个请求一个一次性 daemon 线程，靠 ``_request_running`` 串行；
该线程登记进协调器自持 ``ResourceManager`` 的 :class:`ThreadResource`，关闭时随
``stop_all`` 走 cancel→join→linger。GPT-SoVITS 与 Genie 的合成差异封装在
:class:`GPTSoVITSSynthesisEngine` / :class:`GenieSynthesisEngine`，队列只负责调度。
"""

from __future__ import annotations

import array
import json
import math
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Protocol

from app.core.http_client import urlopen_direct_for_loopback
from app.core.http_client import read_url_cancellable
from app.core.cancellation import OperationCancelled
from app.core.runtime_log import log_event
from app.core.interaction import set_interaction_id
from app.llm.chat_reply import DEFAULT_TONE
from app.voice import audio_checks as _audio_checks
from app.voice.tts_settings import ToneReference as _ToneReference
from app.voice.tts_service import (
    _encode_genie_character_name,
    _format_gpt_sovits_http_error,
    _is_soft_synth_failure,
)
from app.voice.tts_types import TTSCallback, TTSPreparedAudio, _TTSRequest

_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
# 可发音字符:数字/拉丁字母/假名/汉字/谚文(含全角)。纯标点、emoji、符号不算——
# 这类文本喂给 GPT-SoVITS 归一化后音素为空,会触发服务端 [Errno 22] Invalid argument。
_VOICEABLE_CHAR_RE = re.compile(
    "[0-9A-Za-z"
    "぀-ヿ"  # 平假名/片假名
    "㐀-䶿"  # CJK 扩展 A
    "一-鿿"  # CJK 基本
    "豈-﫿"  # CJK 兼容
    "가-힣"  # 谚文音节
    "０-９Ａ-Ｚａ-ｚ"  # 全角数字/字母
    "ｦ-ﾟ"  # 半角片假名
    "]"
)
_CJK_TEXT_LANGS = {"ja", "all_ja", "zh", "all_zh", "ko", "all_ko", "yue", "all_yue"}


def _is_voiceable_text(text: str) -> bool:
    """文本是否含可发音内容。纯标点/emoji/符号归一化后音素为空，会触发服务端
    [Errno 22] Invalid argument，提前判定可避免无谓的失败往返。"""
    return bool(_VOICEABLE_CHAR_RE.search(text))


def _resolve_request_text_lang(text: str, configured_text_lang: str) -> str:
    """英文混入中日韩文本时切到 auto，避免 GPT-SoVITS 按单语 BERT 处理失败。"""
    normalized = configured_text_lang.strip().lower()
    if normalized in _CJK_TEXT_LANGS and _LATIN_LETTER_RE.search(text):
        return "auto_yue" if normalized in {"yue", "all_yue"} else "auto"
    return normalized or "ja"


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


class TTSSynthesisSink(Protocol):
    """合成结果交回播放端点（commit 5 前由协调器实现）的契约。"""

    def deliver_audio(
        self,
        audio_path: str,
        on_started: TTSCallback | None,
        on_finished: TTSCallback | None,
        text: str,
    ) -> None:
        """投递一段已合成音频到播放队列。"""

    def deliver_prepared(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        """投递一段预生成音频到对应句柄。"""

    def fail_audio_request(self, request: _TTSRequest, message: str) -> None:
        """合成失败：走失败回调并按需向 UI 报错。"""

    def skip_audio_request(self, request: _TTSRequest, reason: str) -> None:
        """合成静默跳过：正常走完回调但不报错。"""

    def schedule_cleanup(self, audio_path: Path) -> None:
        """安排清理无效/废弃的临时 wav。"""


class GPTSoVITSSynthesisEngine:
    """GPT-SoVITS HTTP 合成引擎：就绪门控 + 权重 + POST + Broken pipe 重启重试。"""

    service_label = "GPT-SoVITS"

    def synthesize(self, queue: "TTSSynthesisQueue", request: _TTSRequest, *, fail, skip) -> Path | None:
        supervisor = queue._supervisor
        settings = queue.settings
        restart_attempted = False
        cancel_checker = _request_cancel_checker(request)
        while True:
            cancel_checker()
            if not supervisor._ensure_service_available(fail):
                return None

            if not supervisor._ensure_character_weights(fail):
                return None

            reference = queue._select_reference(request.tone)
            ref_audio_path = (
                settings.to_remote_path(reference.ref_audio_path)
                or str(reference.ref_audio_path)
            )
            payload = {
                "text": request.text,
                "text_lang": _resolve_request_text_lang(
                    request.text,
                    settings.text_lang,
                ),
                "ref_audio_path": ref_audio_path,
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
            log_event(
                "TTS",
                "发送 GPT-SoVITS 请求",
                {
                    "api_url": settings.api_url,
                    "text": request.text,
                    "text_chars": len(request.text),
                    "tone": request.tone,
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
                url=settings.api_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )

            try:
                started_at = time.perf_counter()
                audio_data, response_status = read_url_cancellable(
                    urlopen_direct_for_loopback,
                    http_request,
                    timeout=settings.timeout_seconds,
                    cancel_checker=cancel_checker,
                )
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                log_event(
                    "TTS",
                    "GPT-SoVITS 请求成功",
                    {
                        "status": response_status,
                        "bytes": len(audio_data),
                        "audio_bytes": len(audio_data),
                        "duration_ms": elapsed_ms,
                        "attempt": 2 if restart_attempted else 1,
                    },
                )
                break
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                log_event(
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
                    and supervisor._restart_local_service_after_http_failure(exc.code, error_body)
                ):
                    restart_attempted = True
                    continue
                message = _format_gpt_sovits_http_error(exc.code, error_body)
                if _is_soft_synth_failure(exc.code, error_body):
                    # 单段合成失败（服务端 tts failed）：文本已照常显示，语音缺一段无需
                    # 打断用户，静默跳过、正常完成回调，不向 UI 弹 TTS 异常。
                    skip(message)
                else:
                    fail(message)
                return None
            except urllib.error.URLError as exc:
                log_event("TTS", "GPT-SoVITS 请求失败", {"reason": str(exc.reason)})
                fail(
                    f"GPT-SoVITS 请求失败，请确认服务已启动并可访问 {settings.api_url}：{exc.reason}"
                )
                return None
            except TimeoutError:
                log_event("TTS", "GPT-SoVITS 请求超时")
                fail("GPT-SoVITS 请求超时。")
                return None

        if not audio_data:
            log_event("TTS", "GPT-SoVITS 返回空音频")
            fail("GPT-SoVITS 返回了空音频。")
            return None

        with tempfile.NamedTemporaryFile(
            prefix="sakura_tts_",
            suffix=".wav",
            delete=False,
            dir=str(queue._cache_dir),
        ) as audio_file:
            audio_file.write(audio_data)
            audio_path = audio_file.name
        audio_issue = _audio_checks._verify_generated_audio(Path(audio_path))
        if audio_issue is not None:
            log_event("TTS", "生成音频校验失败", {"audio_path": audio_path, "issue": audio_issue})
            fail(f"GPT-SoVITS 生成的音频无效（{audio_issue}）。")
            queue._cleanup(Path(audio_path))
            return None
        return Path(audio_path)


class GenieSynthesisEngine:
    """Genie TTS 合成引擎：就绪门控 + 角色模型/参考音频 + POST + WAV 转换。"""

    service_label = "Genie TTS"

    def synthesize(self, queue: "TTSSynthesisQueue", request: _TTSRequest, *, fail, skip) -> Path | None:
        supervisor = queue._supervisor
        settings = queue.settings
        if not supervisor._ensure_service_available(fail):
            return None

        reference = queue._select_reference(request.tone)
        if not supervisor._ensure_character_model(reference.ref_lang, fail):
            return None
        if not supervisor._ensure_reference_audio(reference, fail):
            return None

        payload = {
            "character_name": _encode_genie_character_name(supervisor._genie_character_name()),
            "text": request.text,
            "split_sentence": False,
        }
        log_event(
            "TTS",
            "发送 Genie TTS 请求",
            {
                "api_url": settings.api_url,
                "text": request.text,
                "text_chars": len(request.text),
                "tone": request.tone,
                "payload": payload,
            },
        )
        try:
            started_at = time.perf_counter()
            audio_data = supervisor._post_json_and_read_bytes(
                "tts",
                payload,
                timeout=max(settings.timeout_seconds, 120),
                cancel_checker=_request_cancel_checker(request),
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            log_event(
                "TTS",
                "音频请求失败",
                {"provider": "Genie", "status": exc.code, "error": error_body},
            )
            fail(f"Genie TTS HTTP {exc.code}: {error_body}")
            return None
        except urllib.error.URLError as exc:
            log_event("TTS", "音频请求失败", {"provider": "Genie", "reason": str(exc.reason)})
            fail(f"Genie TTS 请求失败，请确认服务已启动并可访问 {settings.api_url}：{exc.reason}")
            return None
        except TimeoutError:
            log_event("TTS", "音频请求失败", {"provider": "Genie", "reason": "timeout"})
            fail("Genie TTS 请求超时。")
            return None

        if not audio_data:
            fail("Genie TTS 返回了空音频。")
            return None

        with tempfile.NamedTemporaryFile(
            prefix="sakura_genie_tts_",
            suffix=".wav",
            delete=False,
            dir=str(queue._cache_dir),
        ) as audio_file:
            audio_path = Path(audio_file.name)
        try:
            if not _write_genie_audio(audio_data, audio_path):
                fail("Genie TTS 返回的音频无法转换为 WAV。")
                queue._cleanup(audio_path)
                return None
        except OSError as exc:
            fail(f"Genie TTS 写入临时音频失败：{exc}")
            queue._cleanup(audio_path)
            return None

        log_event(
            "TTS",
            "Genie 临时音频已写入",
            {"audio_path": audio_path, "bytes": len(audio_data), "duration_ms": elapsed_ms},
        )
        audio_issue = _audio_checks._verify_generated_audio(audio_path)
        if audio_issue is not None:
            log_event("TTS", "Genie 生成音频校验失败", {"audio_path": str(audio_path), "issue": audio_issue})
            fail(f"Genie TTS 生成的音频无效（{audio_issue}）。")
            queue._cleanup(audio_path)
            return None
        return audio_path


class TTSSynthesisQueue:
    """串行化 speak/prepare 请求，在后台 daemon 线程里调引擎合成并交回 sink。

    线程域 = PYTHON_THREAD：每请求一个一次性 daemon 线程，靠 ``_request_running``
    串行；当前在飞线程登记进 RM 的 :class:`ThreadResource`，关闭随 stop_all 收敛。
    """

    def __init__(
        self,
        *,
        supervisor: object,
        engine: object,
        cache_dir: Path,
        resource_manager: object | None,
        sink: TTSSynthesisSink,
        is_closed,
    ) -> None:
        self._supervisor = supervisor
        self._engine = engine
        self._cache_dir = cache_dir
        self._resource_manager = resource_manager
        self._sink = sink
        self._is_closed = is_closed
        self._lock = threading.Lock()
        self._pending_requests: list[_TTSRequest] = []
        self._request_running = False
        self._active_request: _TTSRequest | None = None
        self._tone_indices: dict[str, int] = {}
        self._thread_resource = (
            resource_manager.track_python_thread(label="tts_synthesis")
            if resource_manager is not None
            else None
        )

    @property
    def settings(self):  # type: ignore[no-untyped-def]
        return self._supervisor.settings

    def submit(self, request: _TTSRequest) -> None:
        # is_closed 走协调器的 _close_lock；避免在持有本队列锁时回调形成反向锁序。
        if self._is_closed():
            if request.prepared_audio is not None:
                request.prepared_audio.failed = True
            log_event(
                "TTS",
                "Provider 已关闭，丢弃新请求",
                {
                    "text": request.text,
                    "tone": request.tone,
                    "prepared": request.prepared_audio is not None,
                },
            )
            return
        with self._lock:
            if request.prepared_audio is None:
                insert_at = next(
                    (
                        index
                        for index, pending in enumerate(self._pending_requests)
                        if pending.prepared_audio is not None
                    ),
                    len(self._pending_requests),
                )
                self._pending_requests.insert(insert_at, request)
            else:
                self._pending_requests.append(request)
        self._start_next_request()

    def _start_next_request(self) -> None:
        if self._is_closed():
            return
        with self._lock:
            if self._request_running or not self._pending_requests:
                return
            request = self._pending_requests.pop(0)
            self._request_running = True
            self._active_request = request

        thread = threading.Thread(
            target=self._request_audio,
            args=(request,),
            daemon=True,
        )
        # 必须先登记再启动：若 close() 恰好落在 start/track 之间，stop_all 会漏掉
        # 已经运行的线程，使其可能在 Qt 对象析构后继续投递结果。
        if self._thread_resource is not None:
            self._thread_resource.track(thread)
        thread.start()

    def _request_audio(self, tts_request: _TTSRequest) -> None:
        # 请求线程恢复发起方的交互 ID，使本线程内日志可与该次交互串联
        set_interaction_id(tts_request.interaction_id)
        try:
            if self._is_closed():
                log_event("TTS", "Provider 已关闭，跳过音频请求", {"text": tts_request.text})
                self._sink.skip_audio_request(tts_request, "Provider 已关闭")
                return
            if tts_request.prepared_audio is not None and tts_request.prepared_audio.cancelled:
                log_event("TTS", "请求已取消，跳过音频生成", {"text": tts_request.text})
                self._sink.skip_audio_request(tts_request, "请求已取消")
                return

            # 纯标点/emoji/符号段没有可发音内容，喂给服务端会归一化成空音素并触发
            # [Errno 22]；提前判定为“无需发音”，正常走完回调但不发请求、不报错。
            if not _is_voiceable_text(tts_request.text):
                log_event("TTS", "文本无可发音内容，跳过合成", {"text": tts_request.text})
                self._sink.skip_audio_request(tts_request, "无可发音内容")
                return

            request_resolved = False

            def fail(message: str) -> None:
                nonlocal request_resolved
                request_resolved = True
                self._sink.fail_audio_request(tts_request, message)

            def skip(reason: str) -> None:
                nonlocal request_resolved
                request_resolved = True
                self._sink.skip_audio_request(tts_request, reason)

            try:
                audio_path = self._engine.synthesize(self, tts_request, fail=fail, skip=skip)
            except OperationCancelled:
                skip("请求已取消")
                return
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "TTS",
                    "合成线程异常，已继续字幕流程",
                    {
                        "text": tts_request.text,
                        "tone": tts_request.tone,
                        "error": str(exc),
                    },
                )
                if not request_resolved:
                    fail(f"TTS 合成异常：{exc}")
                return
            if tts_request.cancelled:
                if audio_path is not None:
                    self._sink.schedule_cleanup(audio_path)
                self._sink.skip_audio_request(tts_request, "请求已取消")
                return
            if audio_path is None:
                return
            if tts_request.prepared_audio is None:
                self._sink.deliver_audio(
                    str(audio_path),
                    tts_request.on_started,
                    tts_request.on_finished,
                    tts_request.text,
                )
            else:
                self._sink.deliver_prepared(tts_request.prepared_audio, str(audio_path))
        finally:
            with self._lock:
                self._request_running = False
                self._active_request = None
            self._start_next_request()

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
            log_event(
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
        return reference

    def _cleanup(self, audio_path: Path) -> None:
        self._sink.schedule_cleanup(audio_path)

    def discard_pending(self, handle: TTSPreparedAudio) -> None:
        """从待合成队列移除指定预生成句柄的请求。"""
        with self._lock:
            self._pending_requests = [
                request
                for request in self._pending_requests
                if request.prepared_audio is not handle
            ]

    def clear_pending(self) -> None:
        """清空待合成队列（关闭时调用）。"""
        with self._lock:
            pending = self._pending_requests
            self._pending_requests = []
        for request in pending:
            request.cancelled = True
            self._sink.skip_audio_request(request, "Provider 已关闭")

    def cancel_all(self) -> None:
        with self._lock:
            pending = self._pending_requests
            self._pending_requests = []
            active = self._active_request
            if active is not None:
                active.cancelled = True
        for request in pending:
            request.cancelled = True
            self._sink.skip_audio_request(request, "请求已取消")


def _request_cancel_checker(request: _TTSRequest):  # type: ignore[no-untyped-def]
    def check() -> None:
        handle = request.prepared_audio
        if request.cancelled or (handle is not None and handle.cancelled):
            raise OperationCancelled()

    return check
