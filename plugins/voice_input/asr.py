from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import VoiceInputConfig
from .model_manager import model_available, model_dir, safe_model_name


class VoiceInputASRError(RuntimeError):
    """ASR 识别失败。"""


@dataclass(frozen=True)
class ASRCallbacks:
    started: Callable[[], None] | None = None
    succeeded: Callable[[str], None] | None = None
    failed: Callable[[str], None] | None = None
    finished: Callable[[], None] | None = None


class ASRWorker:
    """后台 ASR 识别线程。"""

    def __init__(
        self,
        *,
        config: VoiceInputConfig,
        data_dir: Path,
        audio_path: Path,
        callbacks: ASRCallbacks | None = None,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.audio_path = Path(audio_path)
        self.callbacks = callbacks or ASRCallbacks()
        self._cancel_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="voice-input-asr",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        _call(self.callbacks.started)
        try:
            text = transcribe_audio(
                self.audio_path,
                self.config,
                self.data_dir,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:  # noqa: BLE001
            if not self._cancel_event.is_set():
                _call(self.callbacks.failed, str(exc))
        else:
            if not self._cancel_event.is_set():
                _call(self.callbacks.succeeded, text)
        finally:
            _call(self.callbacks.finished)


def transcribe_audio(
    audio_path: Path,
    config: VoiceInputConfig,
    data_dir: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> str:
    if cancel_event is not None and cancel_event.is_set():
        raise VoiceInputASRError("识别已取消。")
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise VoiceInputASRError(f"临时音频不存在：{audio_path}")
    model_name = safe_model_name(config.model_name)
    if not model_available(data_dir, model_name):
        raise VoiceInputASRError(
            f"缺少 ASR 模型：{model_dir(data_dir, model_name)}。请在 Voice Input 设置页下载模型。"
        )
    errors: list[str] = []
    for backend in (_transcribe_with_faster_whisper, _transcribe_with_openai_whisper):
        if cancel_event is not None and cancel_event.is_set():
            raise VoiceInputASRError("识别已取消。")
        try:
            text = backend(audio_path, config, data_dir)
        except ImportError as exc:
            errors.append(str(exc))
            continue
        if text.strip():
            return text.strip()
        raise VoiceInputASRError("未识别到有效语音文本。")
    detail = "；".join(error for error in errors if error)
    if detail:
        raise VoiceInputASRError(f"缺少本地 ASR 依赖：{detail}")
    raise VoiceInputASRError("缺少本地 ASR 依赖，无法识别音频。")


def _transcribe_with_faster_whisper(
    audio_path: Path,
    config: VoiceInputConfig,
    data_dir: Path,
) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError("未安装 faster-whisper") from exc
    model_path = model_dir(data_dir, config.model_name)
    model = WhisperModel(
        str(model_path),
        device=config.asr_device,
        compute_type="int8",
    )
    segments, _info = model.transcribe(
        str(audio_path),
        language=config.asr_language,
        vad_filter=bool(config.vad_enabled),
    )
    return "".join(segment.text for segment in segments).strip()


def _transcribe_with_openai_whisper(
    audio_path: Path,
    config: VoiceInputConfig,
    data_dir: Path,
) -> str:
    try:
        import whisper
    except ImportError as exc:
        raise ImportError("未安装 openai-whisper") from exc
    model_path = model_dir(data_dir, config.model_name)
    device = None if config.asr_device == "auto" else config.asr_device
    model = whisper.load_model(str(model_path), device=device)
    result = model.transcribe(str(audio_path), language=config.asr_language)
    return str(result.get("text", "")).strip()


def _call(callback: Callable[..., None] | None, *args: object) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        return
