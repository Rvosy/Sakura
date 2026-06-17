from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from .asr import ASRCallbacks, ASRWorker
from .config import load_voice_input_config
from .model_manager import model_available, safe_model_name, temp_audio_dir
from .recorder import RecordingCallbacks, RecordingWorker


@dataclass(frozen=True)
class VoiceInputControllerCallbacks:
    recording_started: Callable[[], None] | None = None
    recognizing_started: Callable[[], None] | None = None
    finished: Callable[[str], None] | None = None
    error: Callable[[str], None] | None = None
    status: Callable[[str], None] | None = None


class VoiceInputController:
    """串联录音与 ASR 的控制器。"""

    def __init__(
        self,
        context: Any,
        *,
        callbacks: VoiceInputControllerCallbacks | None = None,
    ) -> None:
        self.context = context
        self.callbacks = callbacks or VoiceInputControllerCallbacks()
        self._lock = threading.RLock()
        self._recording_worker: RecordingWorker | None = None
        self._asr_worker: ASRWorker | None = None
        self._shutdown = False

    def toggle_recording(self) -> None:
        if self.is_recording:
            self.stop_recording()
            return
        self.start_recording()

    def start_recording(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            if self._recording_worker is not None:
                return
            if self._asr_worker is not None:
                _call(self.callbacks.error, "正在识别上一段语音，请稍候。")
                return
            config = load_voice_input_config(self.context)
            model_name = safe_model_name(config.model_name)
            if not model_available(self.context.data_dir, model_name):
                _call(
                    self.callbacks.error,
                    "缺少 ASR 模型。请先在 Voice Input 设置页下载模型。",
                )
                return
            audio_path = _next_audio_path(self.context.data_dir)
            worker = RecordingWorker(
                config=config,
                output_path=audio_path,
                callbacks=RecordingCallbacks(
                    started=self._handle_recording_started,
                    stopped=self._handle_recording_stopped,
                    failed=self._handle_error,
                ),
            )
            self._recording_worker = worker
            worker.start()

    def stop_recording(self) -> None:
        with self._lock:
            worker = self._recording_worker
        if worker is not None:
            worker.stop()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            recording_worker = self._recording_worker
            asr_worker = self._asr_worker
            self._recording_worker = None
            self._asr_worker = None
        if recording_worker is not None:
            recording_worker.cancel()
            recording_worker.join(1.0)
        if asr_worker is not None:
            asr_worker.cancel()
            asr_worker.join(1.0)

    @property
    def is_recording(self) -> bool:
        with self._lock:
            worker = self._recording_worker
        return bool(worker is not None and worker.is_alive())

    @property
    def is_busy(self) -> bool:
        with self._lock:
            recording_worker = self._recording_worker
            asr_worker = self._asr_worker
        return bool(
            (recording_worker is not None and recording_worker.is_alive())
            or (asr_worker is not None and asr_worker.is_alive())
        )

    def _handle_recording_started(self) -> None:
        _call(self.callbacks.recording_started)
        _call(self.callbacks.status, "正在录音，再次点击停止。")

    def _handle_recording_stopped(self, audio_path: Path) -> None:
        with self._lock:
            self._recording_worker = None
            if self._shutdown:
                return
            config = load_voice_input_config(self.context)
            worker = ASRWorker(
                config=config,
                data_dir=self.context.data_dir,
                audio_path=audio_path,
                callbacks=ASRCallbacks(
                    started=self._handle_asr_started,
                    succeeded=self._handle_asr_succeeded,
                    failed=self._handle_error,
                    finished=self._handle_asr_finished,
                ),
            )
            self._asr_worker = worker
            worker.start()

    def _handle_asr_started(self) -> None:
        _call(self.callbacks.recognizing_started)
        _call(self.callbacks.status, "正在识别语音...")

    def _handle_asr_succeeded(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._handle_error("未识别到有效语音文本。")
            return
        services = getattr(self.context, "services", None)
        input_service = getattr(services, "input", None)
        set_input_text = getattr(input_service, "set_input_text", None)
        if not callable(set_input_text):
            self._handle_error("宿主输入服务不可用，无法回填识别文本。")
            return
        set_input_text(text)
        _call(self.callbacks.finished, text)
        _call(self.callbacks.status, "识别完成，文本已填入输入框。")

    def _handle_asr_finished(self) -> None:
        with self._lock:
            self._asr_worker = None

    def _handle_error(self, message: str) -> None:
        with self._lock:
            self._recording_worker = None
        _call(self.callbacks.error, message)
        _call(self.callbacks.status, message)


def _next_audio_path(data_dir: Path) -> Path:
    directory = temp_audio_dir(data_dir)
    filename = f"voice_input_{time.strftime('%Y%m%d_%H%M%S')}_{time.monotonic_ns()}.wav"
    return directory / filename


def _call(callback: Callable[..., None] | None, *args: object) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        return
