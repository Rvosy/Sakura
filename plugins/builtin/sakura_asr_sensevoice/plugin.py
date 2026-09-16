from __future__ import annotations

import importlib
import re
import threading
import time
import uuid
import wave
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

try:
    from ._resources import ModelResources, VERSION, Cancelled, log_event
except ImportError:
    from _resources import ModelResources, VERSION, Cancelled, log_event

PROVIDER_ID = "sakura.asr.sensevoice"
SERVICE_KEY = "sakura.asr.provider.sensevoice"
LANGUAGES = {"auto", "zh", "yue", "en", "ja", "ko"}
_TAGS = re.compile(r"<\|[^|<>]*\|>")


@dataclass
class Job:
    request: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    consumed: bool = False
    started_at: float = field(default_factory=time.monotonic)


class SenseVoiceProvider:
    def __init__(self, context, resources):
        self.context = context
        self.resources = resources
        self.audio = context.get("sakura.host.audio_input")
        try:
            self.logger = context.get("sakura.host.logging")
        except Exception:
            self.logger = None
        self.resources.logger = self.logger
        self.lock = threading.RLock()
        self.jobs = OrderedDict()
        self.thread = None
        self.closed = False
        self.engine = None
        self.engine_language = "auto"
        config = getattr(context, "config", None)
        self.language = config.get().get("language", "auto") if config else "auto"
        self.np = None
        self.sherpa = None
        self.state = "unloaded"
        self.error = ""
        # Language is captured per input; model identity changes with each process.
        self.config_version = VERSION + "-" + uuid.uuid4().hex

    def status(self):
        with self.lock:
            if self.closed:
                state, error = "unavailable", "ASR_PROVIDER_UNAVAILABLE"
            elif getattr(self.resources, "state", None) == "running":
                state, error = "installing", "ASR_PREPARING"
            elif getattr(self.resources, "invalid", False):
                state, error = "failed", "ASR_MODEL_INVALID"
            elif not self.resources.ready():
                state, error = "missing_resources", "ASR_MODEL_MISSING"
            else:
                state, error = self.state, self.error
            ready = state == "ready"
            return {"state": state, "available": ready, "ready": ready, "configVersion": self.config_version, "language": self.language, "errorCode": error or ("READY" if ready else "ASR_PREPARING"), "reasonCode": error or ("READY" if ready else "ASR_PREPARING")}

    def load_settings(self):
        with self.lock:
            return {"language": self.language}

    def save_settings(self, values):
        if not isinstance(values, Mapping) or set(values) != {"language"} or values["language"] not in LANGUAGES:
            raise ValueError("ASR_LANGUAGE_UNSUPPORTED")
        with self.lock:
            self.context.config.update({"language": values["language"]})
            self.language = values["language"]
            return "applied"

    def warmup(self):
        with self.lock:
            if self.closed or getattr(self.resources, "state", None) == "running" or not self.resources.ready():
                status = self.status()
                log_event(self.logger, "warning", "asr.model.load.rejected", "语音模型暂不可加载", error_code=status["errorCode"])
                return status
            if self.state in ("loading", "ready"):
                return self.status()
            self.state, self.error = "loading", ""
            log_event(self.logger, "info", "asr.model.load.started", "正在加载语音模型")
            self.thread = threading.Thread(target=self._load, name="sensevoice-load", daemon=True)
            self.thread.start()
        return self.status()

    def _load(self):
        started_at = time.monotonic()
        try:
            self.resources.verify(self._check_closed)
            sherpa = importlib.import_module("sherpa_onnx")
            np = importlib.import_module("numpy")
            engine = sherpa.OfflineRecognizer.from_sense_voice(
                model=str(self.resources.path / "model.int8.onnx"),
                tokens=str(self.resources.path / "tokens.txt"), num_threads=2,
                use_itn=True, language="", provider="cpu")
            with self.lock:
                if not self.closed:
                    self.sherpa, self.np, self.engine, self.state = sherpa, np, engine, "ready"
                    self.engine_language = "auto"
        except Exception as error:
            with self.lock:
                self.state = "failed"
                self.error = "ASR_DEPENDENCY_UNAVAILABLE" if isinstance(error, (ImportError, OSError)) else "ASR_MODEL_INVALID"
                if self.error == "ASR_MODEL_INVALID" and not self.closed:
                    self.resources.mark_invalid()
        finally:
            outcome = "cancelled" if self.closed else "succeeded" if self.state == "ready" else "failed"
            log_event(self.logger, "error" if outcome == "failed" else "info", "asr.model.load." + outcome, {"succeeded": "语音模型加载完成", "failed": "语音模型加载失败", "cancelled": "语音模型加载已取消"}[outcome], duration_ms=round((time.monotonic() - started_at) * 1000), **({"error_code": self.error} if outcome == "failed" else {}))

    def install_models(self, values=None):
        with self.lock:
            if self.closed:
                raise ValueError("ASR_PROVIDER_UNAVAILABLE")
            if self.state == "loading" or any(job.result is None for job in self.jobs.values()):
                raise ValueError("ASR_BUSY")
            if getattr(self.resources, "state", None) != "running":
                # No live reader can race directory publication. An already
                # prepared recording must also not reuse its old configuration.
                self.engine, self.state, self.error = None, "unloaded", ""
                self.config_version = VERSION + "-" + uuid.uuid4().hex
            return self.resources.start(values)

    def _check_closed(self):
        if self.closed:
            raise Cancelled()

    def begin(self, request):
        if not isinstance(request, Mapping) or not isinstance(request.get("audio"), Mapping) or request.get("language", "auto") not in LANGUAGES:
            return {"state": "failed", "errorCode": "ASR_LANGUAGE_UNSUPPORTED"}
        with self.lock:
            if not self.status()["available"]:
                return {"state": "failed", "errorCode": self.status()["errorCode"]}
            if request.get("configVersion") != self.config_version:
                return {"state": "failed", "errorCode": "ASR_CONFIGURATION_CHANGED"}
            if any(job.result is None for job in self.jobs.values()):
                return {"state": "failed", "errorCode": "ASR_BUSY"}
            if len(self.jobs) >= 64:
                for key in list(self.jobs):
                    if self.jobs[key].consumed:
                        del self.jobs[key]
                        break
                if len(self.jobs) >= 64:
                    return {"state": "failed", "errorCode": "ASR_CAPACITY_EXCEEDED"}
            job_id = uuid.uuid4().hex
            job = Job(dict(request))
            self.jobs[job_id] = job
            log_event(self.logger, "info", "asr.recognition.started", "正在识别语音", request_id=job.request.get("requestId"), language=request.get("language", "auto"))
            self.thread = threading.Thread(target=self._run, args=(job,), name="sensevoice-recognize", daemon=True)
            self.thread.start()
        return job_id

    def _run(self, job):
        lease = None
        try:
            if job.cancel.is_set() or self.closed:
                raise Cancelled()
            lease = self.audio.acquire(job.request["audio"]["resourceId"])
            text, language = self._recognize(Path(lease["path"]), job.request.get("language", "auto"), job.cancel)
            text = _TAGS.sub("", text).strip()
            if not text:
                raise ValueError("ASR_NO_SPEECH")
            result = {"state": "succeeded", "text": text, "language": language}
        except Cancelled:
            result = {"state": "cancelled"}
        except Exception as error:
            code = str(getattr(error, "code", error))
            result = {"state": "failed", "errorCode": code if re.fullmatch(r"ASR_[A-Z_]{1,70}", code) else "ASR_RECOGNITION_FAILED"}
            log_event(self.logger, "error", "asr.recognition.failed", "语音识别失败", request_id=job.request.get("requestId"), duration_ms=round((time.monotonic() - job.started_at) * 1000), error_code=result["errorCode"])
        finally:
            # Always release before making a terminal result visible, including
            # cancellation while native decoding was still using this file.
            if lease:
                try:
                    self.audio.release(lease["leaseId"])
                except Exception:
                    pass
        with self.lock:
            job.result = {"state": "cancelled"} if job.cancel.is_set() or self.closed else result
            if job.result["state"] != "failed":
                log_event(self.logger, "error" if job.result["state"] == "failed" else "info", "asr.recognition." + job.result["state"], {"succeeded": "语音识别完成", "failed": "语音识别失败", "cancelled": "语音识别已取消"}[job.result["state"]], request_id=job.request.get("requestId"), duration_ms=round((time.monotonic() - job.started_at) * 1000), **({"error_code": job.result["errorCode"]} if job.result["state"] == "failed" else {}))

    def _recognize(self, path, language, cancel):
        def check():
            if cancel.is_set() or self.closed:
                raise Cancelled()

        with wave.open(str(path), "rb") as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE" or not 1 <= audio.getnframes() <= 960000:
                raise ValueError("ASR_AUDIO_INVALID")
            samples = self.np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(self.np.float32) / 32768.0
        check()
        config = self.sherpa.VadModelConfig()
        config.silero_vad.model = str(self.resources.path / "silero_vad.onnx")
        config.silero_vad.threshold = 0.5
        # Detect short natural pauses, then merge them below. Waiting for a
        # long silence can force the maximum-duration cut into a spoken phrase.
        config.silero_vad.min_silence_duration = 0.25
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.max_speech_duration = 15
        config.sample_rate = 16000
        config.num_threads = 1
        vad = self.sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=65)
        window = config.silero_vad.window_size
        for offset in range(0, len(samples), window):
            check()
            chunk = samples[offset:offset + window]
            if len(chunk) < window:
                chunk = self.np.pad(chunk, (0, window - len(chunk)))
            vad.accept_waveform(chunk)
        vad.flush()
        spans = []
        while not vad.empty():
            segment = vad.front
            # Silero can declare speech after a soft initial consonant. Keep
            # 250 ms of surrounding audio to avoid clipping that phonetic context.
            start = max(0, segment.start - 4000)
            end = min(len(samples), segment.start + len(segment.samples) + 4000)
            # Preserve nearby phrases in one bounded decode so brief pauses do
            # not discard linguistic context. The boundary remains a VAD pause.
            if spans and start - spans[-1][1] <= 16000 and end - spans[-1][0] <= 15 * 16000:
                spans[-1] = (spans[-1][0], end)
                vad.pop()
                continue
            # Adjacent padded regions meet at a midpoint rather than repeat audio.
            if spans and start < spans[-1][1]:
                boundary = (start + spans[-1][1]) // 2
                spans[-1] = (spans[-1][0], boundary)
                start = boundary
            spans.append((start, end))
            vad.pop()
        if not spans:
            raise ValueError("ASR_NO_SPEECH")
        if language != self.engine_language:
            # sherpa-onnx SenseVoice reads language from model configuration,
            # not from stream options. Keep only the last selected recognizer.
            self.engine = None
            started_at = time.monotonic()
            log_event(self.logger, "info", "asr.model.load.started", "正在加载语音模型", language=language)
            try:
                self.engine = self.sherpa.OfflineRecognizer.from_sense_voice(
                    model=str(self.resources.path / "model.int8.onnx"),
                    tokens=str(self.resources.path / "tokens.txt"), num_threads=2,
                    use_itn=True, language="" if language == "auto" else language,
                    provider="cpu")
            except Exception:
                with self.lock:
                    self.state, self.error = "failed", "ASR_MODEL_INVALID"
                    self.resources.mark_invalid()
                log_event(self.logger, "error", "asr.model.load.failed", "语音模型加载失败", language=language, duration_ms=round((time.monotonic() - started_at) * 1000), error_code="ASR_MODEL_INVALID")
                raise
            self.engine_language = language
            log_event(self.logger, "info", "asr.model.load.succeeded", "语音模型加载完成", language=language, duration_ms=round((time.monotonic() - started_at) * 1000))
        texts, detected = [], None
        for start, end in spans:
            check()
            stream = self.engine.create_stream()
            stream.accept_waveform(16000, samples[start:end])
            self.engine.decode_stream(stream)
            check()
            text = _TAGS.sub("", stream.result.text).strip()
            if text:
                texts.append(text)
            detected = str(getattr(stream.result, "lang", "") or "").replace("<|", "").replace("|>", "") or detected
        return " ".join(texts), (language if language != "auto" else detected)

    def poll(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return {"state": "failed", "errorCode": "ASR_JOB_NOT_FOUND"}
            if job.result:
                job.consumed = True
            return dict(job.result or {"state": "running"})

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.result is not None:
                return False
            job.cancel.set()
            job.consumed = True
            return True

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for job in self.jobs.values():
                job.cancel.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        log_event(self.logger, "info", "asr.provider.stopped", "语音识别插件已退出")


class SenseVoicePlugin:
    def setup(self, context):
        hub = context.get("sakura.asr")
        if "language" not in context.config.get():
            previous = hub.status()
            language = previous.get("language", "auto") if previous.get("selectedProviderId") == PROVIDER_ID else "auto"
            context.config.update({"language": language if language in LANGUAGES else "auto"})
        resources = ModelResources(Path(context.data_path("models")))
        provider = SenseVoiceProvider(context, resources)
        context.effect(resources.close)
        context.effect(provider.close)
        context.provide(SERVICE_KEY, provider, exports=("status", "warmup", "begin", "poll", "cancel"))
        hub.registerProvider({"providerId": PROVIDER_ID, "serviceKey": SERVICE_KEY, "label": "SenseVoice", "processingLocation": "local"})
        context.effect(lambda: hub.unregisterProvider(PROVIDER_ID, SERVICE_KEY))
        settings = context.get("sakura.host.settings")
        settings.register({
            "sectionId": "recognition", "title": "识别", "order": 90,
            "fields": [{"key": "language", "label": "识别语言", "type": "select", "default": "auto",
                        "options": [{"value": value, "label": label} for value, label in (
                            ("auto", "自动检测"), ("zh", "普通话"), ("yue", "粤语"),
                            ("en", "英语"), ("ja", "日语"), ("ko", "韩语"))]}],
        }, load=provider.load_settings, save=provider.save_settings)
        context.get("sakura.host.settings.surface-v0").register("recognition", "voice-input")
        settings.register(resources.descriptor(), load=resources.load, actions={"installModels": provider.install_models, "retryModels": provider.install_models, "cancelModels": resources.cancel})
        context.get("sakura.host.settings.surface-v0").register("models", "voice-input")
        log_event(provider.logger, "info", "asr.provider.started", "语音识别插件已启用")
