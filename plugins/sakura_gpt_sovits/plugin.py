from __future__ import annotations

import os
import queue
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from app.core.cancellation import OperationCancelled
from app.core.process_tree import terminate_process_tree
from app.llm.chat_reply import DEFAULT_TONE
from app.voice.runtime_compat import find_usable_runtime_python
from app.voice.tts_endpoint import GptSovitsEndpointResolver, GptSovitsEndpointSupervisor
from app.voice.tts_settings import (
    DEFAULT_GPT_SOVITS_BASE_URL,
    DEFAULT_GPT_SOVITS_TTS_PATH,
    GPTSoVITSTTSSettings,
    ToneReference,
)
from app.voice.tts_synthesis import GPTSoVITSSynthesisEngine
from app.voice.tts_types import _TTSRequest


PROVIDER_ID = "sakura.tts.gpt-sovits"
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_STOP = object()


@dataclass(frozen=True)
class _ProviderConfig:
    enabled: bool
    custom_base_url: str | None
    tts_path: str
    timeout_seconds: int
    remote_reference_root: str | None
    work_dir: Path | None
    python_path: Path | None
    tts_config_path: Path | None


@dataclass(frozen=True)
class _CharacterVoice:
    character_id: str
    package_dir: Path
    ref_text_path: Path
    ref_audio_path: Path
    ref_text: str
    ref_lang: str
    text_lang: str
    tone_references: dict[str, list[ToneReference]]
    gpt_model_path: Path | None
    sovits_model_path: Path | None

    def settings(self, config: _ProviderConfig) -> GPTSoVITSTTSSettings:
        base_url = config.custom_base_url or DEFAULT_GPT_SOVITS_BASE_URL
        return GPTSoVITSTTSSettings(
            enabled=True,
            provider="gpt-sovits",
            api_url=f"{base_url.rstrip('/')}{config.tts_path}",
            custom_base_url=config.custom_base_url,
            tts_path=config.tts_path,
            remote_reference_root=config.remote_reference_root,
            timeout_seconds=config.timeout_seconds,
            work_dir=config.work_dir if config.custom_base_url is None else None,
            python_path=config.python_path if config.custom_base_url is None else None,
            tts_config_path=config.tts_config_path if config.custom_base_url is None else None,
            character_id=self.character_id,
            character_package_dir=self.package_dir,
            ref_text_path=self.ref_text_path,
            ref_audio_path=self.ref_audio_path,
            ref_text=self.ref_text,
            ref_lang=self.ref_lang,
            text_lang=self.text_lang,
            tone_references=self.tone_references,
            gpt_model_path=self.gpt_model_path,
            sovits_model_path=self.sovits_model_path,
        )


class _Job:
    def __init__(
        self,
        context: object,
        artifacts: object,
        request: Mapping[str, Any],
        voice: _CharacterVoice,
    ) -> None:
        self.request = dict(request)
        self.voice = voice
        self._artifacts = artifacts
        self._allocation = artifacts.allocate({"mediaType": "audio/wav", "suffix": ".wav"})
        self.output_path = Path(self._allocation["path"])
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._cancelled = threading.Event()
        self._started = False
        self._request: _TTSRequest | None = None
        self._state = "running"
        self._error_code = "TTS_SYNTHESIS_FAILED"
        self._disposer = context.effect(self.close)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def mark_started(self) -> bool:
        with self._lock:
            if self._state != "running" or self._cancelled.is_set():
                self._state = "cancelled"
                self._done.set()
                return False
            self._started = True
            return True

    def succeed(self) -> None:
        with self._lock:
            if self._cancelled.is_set():
                self._state = "cancelled"
            else:
                self._state = "succeeded"
            self._done.set()

    def fail(self, error_code: str) -> None:
        with self._lock:
            self._error_code = _stable_error_code(error_code)
            self._state = "cancelled" if self._cancelled.is_set() else "failed"
            self._done.set()

    def cancel(self) -> bool:
        with self._lock:
            accepted = self._state == "running"
            self._cancelled.set()
            if self._request is not None:
                self._request.cancelled = True
            if accepted and not self._started:
                self._state = "cancelled"
                self._done.set()
            return accepted

    def attach_request(self, request: _TTSRequest) -> None:
        with self._lock:
            self._request = request
            if self._cancelled.is_set():
                request.cancelled = True

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise OperationCancelled("TTS job cancelled")

    def poll(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            error_code = self._error_code
        if state == "running":
            return {"state": "running"}
        try:
            if state == "succeeded":
                try:
                    artifact = self._artifacts.commit(self._allocation["artifactId"])
                except Exception:
                    self._artifacts.release(self._allocation["artifactId"])
                    return {"state": "failed", "errorCode": "TTS_ARTIFACT_INVALID"}
                return {"state": "succeeded", "artifact": artifact}
            self._artifacts.release(self._allocation["artifactId"])
            if state == "cancelled":
                return {"state": "cancelled"}
            return {"state": "failed", "errorCode": error_code}
        finally:
            self._disposer()

    def close(self) -> None:
        self.cancel()
        # Artifact cleanup is the next older root Effect. Do not let it remove
        # the allocation while the coordinator can still write the payload.
        # A truly stuck synthesis is intentionally escalated to the Core-owned
        # Worker lifecycle deadline and full process-tree rebuild.
        self._done.wait()


class _JobSupervisor:
    def __init__(self, supervisor: object, job: _Job) -> None:
        self._supervisor = supervisor
        self._job = job
        self.settings = getattr(supervisor, "settings")
        self.endpoint_kind = getattr(supervisor, "endpoint_kind", "managed")

    def _ensure_service_available(self, fail: object) -> bool:
        self._job.check_cancelled()
        result = getattr(self._supervisor, "_ensure_service_available")(fail)
        self._job.check_cancelled()
        return bool(result)

    def _ensure_character_weights(self, fail: object) -> bool:
        return bool(
            getattr(self._supervisor, "_ensure_character_weights")(
                fail,
                cancel_checker=self._job.check_cancelled,
            )
        )

    def _restart_local_service_after_http_failure(self, status: int, body: str) -> bool:
        self._job.check_cancelled()
        return bool(
            getattr(self._supervisor, "_restart_local_service_after_http_failure")(
                status,
                body,
            )
        )


class _EngineQueue:
    def __init__(self, supervisor: _JobSupervisor, settings: GPTSoVITSTTSSettings, job: _Job) -> None:
        self._supervisor = supervisor
        self.settings = settings
        self._cache_dir = job.output_path.parent
        self._job = job
        self._tone_indices: dict[str, int] = {}

    def _select_reference(self, tone: str | None) -> ToneReference:
        tone_key = (tone or DEFAULT_TONE).strip() or DEFAULT_TONE
        references = self.settings.tone_references.get(tone_key)
        if not references:
            references = self.settings.tone_references.get(DEFAULT_TONE)
        if not references:
            return ToneReference(
                DEFAULT_TONE,
                self.settings.ref_audio_path,
                self.settings.ref_text,
                self.settings.ref_lang,
            )
        index = self._tone_indices.get(tone_key, 0) % len(references)
        self._tone_indices[tone_key] = index + 1
        return references[index]

    @staticmethod
    def _cleanup(path: Path) -> None:
        path.unlink(missing_ok=True)


class _Coordinator:
    def __init__(self, config: _ProviderConfig) -> None:
        self._config = config
        self._queue: queue.Queue[_Job | object] = queue.Queue(maxsize=16)
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._active: _Job | None = None
        self._resolver: GptSovitsEndpointResolver | None = None
        self._supervisor: GptSovitsEndpointSupervisor | None = None
        self._loaded_weights: tuple[str, str] | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="sakura-gpt-sovits-coordinator",
            daemon=True,
        )
        self._thread.start()

    def submit(self, job: _Job) -> None:
        # Serialize submit with close so a job cannot be queued after close has
        # already drained the queue and stopped the coordinator.
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("TTS_PROVIDER_CLOSED")
            try:
                self._queue.put_nowait(job)
            except queue.Full as error:
                raise RuntimeError("TTS_PROVIDER_BUSY") from error

    def _run(self) -> None:
        while not self._closed.is_set():
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _Job)
                if not item.mark_started():
                    continue
                with self._lock:
                    self._active = item
                self._execute(item)
            finally:
                with self._lock:
                    if self._active is item:
                        self._active = None
                self._queue.task_done()

    def _execute(self, job: _Job) -> None:
        source: Path | None = None
        errors: list[str] = []
        try:
            job.check_cancelled()
            settings, supervisor = self._configure(job.voice)
            request = _TTSRequest(
                text=str(job.request["text"]),
                tone=str(job.request.get("options", {}).get("tone", DEFAULT_TONE)),
                request_id=str(job.request["requestId"]),
            )
            job.attach_request(request)

            def fail(message: str) -> None:
                errors.append(message)

            def skip(message: str) -> None:
                errors.append("TTS_SYNTHESIS_CANCELLED" if job.cancelled else message)

            source = GPTSoVITSSynthesisEngine().synthesize(
                _EngineQueue(_JobSupervisor(supervisor, job), settings, job),
                request,
                fail=fail,
                skip=skip,
            )
            job.check_cancelled()
            if source is None:
                job.fail(errors[-1] if errors else "TTS_SYNTHESIS_FAILED")
                return
            os.replace(source, job.output_path)
            source = None
            job.succeed()
            runtime = self._resolver.runtime if self._resolver is not None else None
            if runtime is not None and getattr(runtime, "_weights_ready", False):
                self._loaded_weights = _weight_key(settings)
        except OperationCancelled:
            job.cancel()
            job.fail("TTS_SYNTHESIS_CANCELLED")
        except Exception as error:
            job.fail(getattr(error, "code", str(error)))
        finally:
            if source is not None:
                source.unlink(missing_ok=True)

    def _configure(
        self,
        voice: _CharacterVoice,
    ) -> tuple[GPTSoVITSTTSSettings, GptSovitsEndpointSupervisor]:
        settings = voice.settings(self._config)
        settings.validate()
        if self._resolver is None:
            base_dir = self._config.work_dir.parent if self._config.work_dir is not None else voice.package_dir
            self._resolver = GptSovitsEndpointResolver(
                settings,
                base_dir=base_dir,
                resource_manager=None,
                is_closed=self._operation_cancelled,
            )
            self._supervisor = GptSovitsEndpointSupervisor(self._resolver)
        else:
            resolved = replace(settings, api_url=self._resolver.endpoint.synthesis_url)
            self._resolver.settings = resolved
            assert self._supervisor is not None
            self._supervisor.settings = resolved
            runtime = self._resolver.runtime
            if runtime is not None:
                runtime.settings = resolved
                if self._loaded_weights != _weight_key(resolved):
                    runtime._weights_ready = False
            settings = resolved
        assert self._supervisor is not None
        return settings, self._supervisor

    def _operation_cancelled(self) -> bool:
        if self._closed.is_set():
            return True
        with self._lock:
            return self._active is not None and self._active.cancelled

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            active = self._active
        if active is not None:
            active.cancel()
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(pending, _Job):
                pending.cancel()
            self._queue.task_done()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass
        self._thread.join(0.35)
        runtime = self._resolver.runtime if self._resolver is not None else None
        process = getattr(runtime, "_server_process", None) if runtime is not None else None
        if process is not None and process.poll() is None:
            terminate_process_tree(process, timeout=0.2)
        if runtime is not None:
            runtime._server_process = None
            runtime._process_resource = None
        # A plugin is not allowed to report a completed disable while its
        # coordinator can still mutate files. The Core-owned lifecycle
        # deadline remains the final escape hatch: a truly stuck coordinator
        # causes the whole generation-private Worker to be rebuilt.
        self._thread.join()


class GPTSoVITSProvider:
    def __init__(self, context: object, character: object, artifacts: object) -> None:
        self._context = context
        self._character = character
        self._artifacts = artifacts
        try:
            self._config = _parse_config(context.config.get())
            self._coordinator: _Coordinator | None = _Coordinator(self._config)
        except (TypeError, ValueError):
            self._config = None
            self._coordinator = None

    def status(self) -> dict[str, Any]:
        return {
            "label": "GPT-SoVITS",
            "available": self._config is not None
            and self._config.enabled
            and _config_available(self._config),
        }

    def begin(self, request: Mapping[str, Any]) -> _Job:
        if self._config is None or not self._config.enabled or self._coordinator is None:
            raise RuntimeError("TTS_PROVIDER_UNAVAILABLE")
        character_id = request.get("characterId")
        if not isinstance(character_id, str) or not character_id:
            raise ValueError("TTS_REQUEST_INVALID")
        extension = self._character.get(character_id)
        voice = _parse_character_voice(self._character, character_id, extension)
        job = _Job(self._context, self._artifacts, request, voice)
        try:
            self._coordinator.submit(job)
        except Exception:
            job.close()
            self._artifacts.release(job._allocation["artifactId"])
            job._disposer()
            raise
        return job

    def close(self) -> None:
        if self._coordinator is not None:
            self._coordinator.close()


class GPTSoVITSPlugin:
    def setup(self, context: object) -> None:
        hub = context.get("sakura.tts")
        character = context.get("sakura.host.character")
        artifacts = context.get("sakura.host.artifacts")
        settings = context.get("sakura.host.settings")
        surface = context.get("sakura.host.settings.surface-v0")
        provider = GPTSoVITSProvider(context, character, artifacts)
        context.effect(provider.close)
        context.effect(hub.registerProvider(PROVIDER_ID, provider))
        context.config.on_change(lambda _values: "restart_required")
        settings.register(
            {
                "sectionId": "runtime",
                "title": "GPT-SoVITS Provider",
                "order": 100,
                "fields": [
                    {"key": "customBaseUrl", "label": "自定义服务地址", "type": "string", "default": "", "description": "留空时由 Sakura 管理本地 Runtime。"},
                    {"key": "ttsPath", "label": "合成请求路径", "type": "string", "default": "/tts"},
                    {"key": "remoteReferenceRoot", "label": "远程参考音频根目录", "type": "string", "default": ""},
                    {"key": "workDir", "label": "工作目录", "type": "string", "default": ""},
                    {"key": "pythonPath", "label": "Python 路径", "type": "string", "default": ""},
                    {"key": "ttsConfigPath", "label": "推理配置路径", "type": "string", "default": ""},
                    {"key": "timeoutSeconds", "label": "超时", "type": "integer", "default": 60, "minimum": 1, "maximum": 300, "step": 1},
                ],
            },
            load=context.config.get,
            save=context.config.update,
        )
        surface.register("runtime", "voice")


def _parse_config(value: Mapping[str, Any]) -> _ProviderConfig:
    custom = str(value.get("customBaseUrl") or "").strip().rstrip("/") or None
    if custom is not None:
        try:
            endpoint = urlparse(custom)
        except ValueError as error:
            raise ValueError("TTS_CONFIG_INVALID") from error
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.fragment
        ):
            raise ValueError("TTS_CONFIG_INVALID")
    tts_path = str(value.get("ttsPath") or DEFAULT_GPT_SOVITS_TTS_PATH).strip()
    if not tts_path.startswith("/"):
        tts_path = f"/{tts_path}"
    timeout = value.get("timeoutSeconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("TTS_CONFIG_INVALID")
    return _ProviderConfig(
        enabled=True,
        custom_base_url=custom,
        tts_path=tts_path,
        timeout_seconds=timeout,
        remote_reference_root=str(value.get("remoteReferenceRoot") or "").strip() or None,
        work_dir=_absolute_path(value.get("workDir")),
        python_path=_absolute_path(value.get("pythonPath")),
        tts_config_path=_absolute_path(value.get("ttsConfigPath")),
    )


def _config_available(config: _ProviderConfig) -> bool:
    if config.custom_base_url is not None:
        return True
    work_dir = config.work_dir
    if work_dir is None or not work_dir.is_dir() or not (work_dir / "api_v2.py").is_file():
        return False
    python = config.python_path or find_usable_runtime_python(work_dir / "runtime")
    return python is not None and python.is_file()


def _parse_character_voice(
    character: object,
    character_id: str,
    extension: Mapping[str, Any],
) -> _CharacterVoice:
    tone_refs_relative = extension.get("toneRefs")
    if not isinstance(tone_refs_relative, str) or not tone_refs_relative.strip():
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    tone_refs_relative = tone_refs_relative.strip()
    tone_refs_path = Path(character.resolve_resource(character_id, tone_refs_relative))
    package_dir = _package_root(tone_refs_path, tone_refs_relative)
    references: dict[str, list[ToneReference]] = {}
    for raw_line in tone_refs_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4 or not all(parts):
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
        audio_relative, language, text, tone = parts
        audio_path = Path(character.resolve_resource(character_id, audio_relative))
        references.setdefault(tone, []).append(
            ToneReference(tone, audio_path, text, language.lower())
        )
    flattened = [item for values in references.values() for item in values]
    if not flattened:
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    neutral = references.get(DEFAULT_TONE, flattened)[0]
    return _CharacterVoice(
        character_id=character_id,
        package_dir=package_dir,
        ref_text_path=tone_refs_path,
        ref_audio_path=neutral.ref_audio_path,
        ref_text=neutral.ref_text,
        ref_lang=str(extension.get("refLang") or neutral.ref_lang).strip().lower(),
        text_lang=str(extension.get("textLang") or "ja").strip().lower(),
        tone_references=references,
        gpt_model_path=_character_resource(character, character_id, extension.get("gptModel")),
        sovits_model_path=_character_resource(character, character_id, extension.get("sovitsModel")),
    )


def _character_resource(character: object, character_id: str, value: object) -> Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    return Path(character.resolve_resource(character_id, value))


def _package_root(resolved: Path, relative: str) -> Path:
    root = resolved
    for _part in Path(relative).parts:
        root = root.parent
    try:
        if (root / relative).resolve(strict=True) != resolved.resolve(strict=True):
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    except OSError as error:
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID") from error
    return root


def _absolute_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("TTS_CONFIG_INVALID")
    return path


def _weight_key(settings: GPTSoVITSTTSSettings) -> tuple[str, str]:
    return (
        str(settings.gpt_model_path or ""),
        str(settings.sovits_model_path or ""),
    )


def _stable_error_code(value: object) -> str:
    direct = str(getattr(value, "code", value) or "").strip()
    prefix = direct.split(":", 1)[0].strip()
    if _ERROR_CODE.fullmatch(prefix):
        return prefix
    return "TTS_SYNTHESIS_FAILED"
