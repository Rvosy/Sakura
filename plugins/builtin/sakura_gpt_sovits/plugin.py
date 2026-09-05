from __future__ import annotations

import os
import queue
import re
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

try:
    from . import _support
except ImportError:
    import _support  # type: ignore[no-redef]

DEFAULT_GPT_SOVITS_BASE_URL = _support.DEFAULT_GPT_SOVITS_BASE_URL
DEFAULT_GPT_SOVITS_TTS_PATH = _support.DEFAULT_GPT_SOVITS_TTS_PATH
DEFAULT_TONE = _support.DEFAULT_TONE
GPTSoVITSTTSSettings = _support.GPTSoVITSTTSSettings
GPTSoVITSSynthesisEngine = _support.GPTSoVITSSynthesisEngine
GptSovitsEndpointResolver = _support.GptSovitsEndpointResolver
GptSovitsEndpointSupervisor = _support.GptSovitsEndpointSupervisor
OperationCancelled = _support.OperationCancelled
TTSBundleResource = _support.TTSBundleResource
ToneReference = _support.ToneReference
_TTSRequest = _support._TTSRequest
find_usable_runtime_python = _support.find_usable_runtime_python
installed_bundle_result = _support.installed_bundle_result
recommend_gpt_sovits_bundle = _support.recommend_gpt_sovits_bundle
terminate_process_tree = _support.terminate_process_tree
user_facing_path = _support.user_facing_path


PROVIDER_ID = "sakura.tts.gpt-sovits"
SERVICE_KEY = "sakura.tts.provider.gpt-sovits"
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
            cancelled = self._state == "cancelled"
        if cancelled:
            self._disposer()

    def fail(self, error_code: str) -> None:
        with self._lock:
            self._error_code = _stable_error_code(error_code)
            self._state = "cancelled" if self._cancelled.is_set() else "failed"
            self._done.set()
        self._disposer()

    def cancel(self) -> bool:
        with self._lock:
            accepted = self._state == "running"
            self._cancelled.set()
            if self._request is not None:
                self._request.cancelled = True
            finished = accepted and not self._started
            if finished:
                self._state = "cancelled"
                self._done.set()
        if finished:
            self._disposer()
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
                    return {"state": "failed", "errorCode": "TTS_ARTIFACT_INVALID"}
                return {"state": "succeeded", "artifact": artifact}
            if state == "cancelled":
                return {"state": "cancelled"}
            return {"state": "failed", "errorCode": error_code}
        finally:
            self._disposer()

    def close(self) -> None:
        self.cancel()
        # The coordinator must stop writing before its artifact can be released.
        # A stuck writer is bounded by the Core-owned plugin shutdown deadline.
        self._done.wait()
        self._artifacts.release(self._allocation["artifactId"])


class _Warmup:
    def __init__(self, voice: _CharacterVoice) -> None:
        self.voice = voice
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise OperationCancelled("TTS warmup cancelled")


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
    def __init__(
        self,
        config: _ProviderConfig,
        diagnostic: Callable[[str, str, Mapping[str, str]], None] | None = None,
    ) -> None:
        self._config = config
        self._diagnostic = diagnostic
        self._queue: queue.Queue[_Job | _Warmup | object] = queue.Queue(maxsize=16)
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._active: _Job | _Warmup | None = None
        self._resolver: GptSovitsEndpointResolver | None = None
        self._supervisor: GptSovitsEndpointSupervisor | None = None
        self._loaded_weights: tuple[str, str] | None = None
        self._pending_config: _ProviderConfig | None = None
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

    def warmup(self, voice: _CharacterVoice) -> None:
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("TTS_PROVIDER_CLOSED")
            try:
                self._queue.put_nowait(_Warmup(voice))
            except queue.Full as error:
                raise RuntimeError("TTS_PROVIDER_BUSY") from error

    def _run(self) -> None:
        while not self._closed.is_set():
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, (_Job, _Warmup))
                if isinstance(item, _Job) and not item.mark_started():
                    continue
                with self._lock:
                    self._active = item
                if isinstance(item, _Warmup):
                    self._execute_warmup(item)
                else:
                    self._execute(item)
            finally:
                with self._lock:
                    if self._active is item:
                        self._active = None
                    pending_config = self._pending_config
                    self._pending_config = None
                if pending_config is not None:
                    self._apply_config(pending_config)
                self._queue.task_done()

    def reconfigure(self, config: _ProviderConfig) -> None:
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("TTS_PROVIDER_CLOSED")
            if self._active is not None:
                self._pending_config = config
                return
        self._apply_config(config)

    def _apply_config(self, config: _ProviderConfig) -> None:
        with self._lock:
            previous = self._config
            reset_endpoint = (
                previous.custom_base_url != config.custom_base_url
                or previous.tts_path != config.tts_path
                or (
                    previous.custom_base_url is None
                    and config.custom_base_url is None
                    and (
                        previous.work_dir != config.work_dir
                        or previous.python_path != config.python_path
                        or previous.tts_config_path != config.tts_config_path
                    )
                )
            )
            resolver = self._resolver if reset_endpoint else None
            if reset_endpoint:
                self._resolver = None
                self._supervisor = None
                self._loaded_weights = None
            self._config = config
        if resolver is not None:
            # Resolver.close owns only a Sakura-managed subprocess.  A custom
            # endpoint is merely forgotten and is never terminated.
            resolver.close()

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

    def _execute_warmup(self, warmup: _Warmup) -> None:
        if self._config.custom_base_url is not None:
            return
        stage = "configuration"
        try:
            warmup.check_cancelled()
            settings, supervisor = self._configure(warmup.voice)
            errors: list[str] = []
            stage = "runtime_start"
            if not supervisor._ensure_service_available(errors.append):
                return
            warmup.check_cancelled()
            stage = "weights"
            if not supervisor._ensure_character_weights(
                errors.append,
                cancel_checker=warmup.check_cancelled,
            ):
                return
            runtime = self._resolver.runtime if self._resolver is not None else None
            if runtime is not None and getattr(runtime, "_weights_ready", False):
                self._loaded_weights = _weight_key(settings)
        except OperationCancelled:
            return
        except Exception as error:
            # Warmup is best effort. The first synthesis retries the same
            # preparation path and publishes the user-visible terminal state.
            self._report_warmup_failure(
                _stable_error_code(error),
                stage,
                type(error).__name__,
            )
            return

    def _report_warmup_failure(
        self,
        reason_code: object,
        stage: str,
        error_type: str,
    ) -> None:
        self._report(
            "tts.service.warmup_failed",
            "warning",
            {
                "provider": PROVIDER_ID,
                "reason_code": _stable_error_code(reason_code),
                "stage": stage,
                "error_type": error_type,
            },
        )

    def _report(
        self,
        event: str,
        severity: str,
        attributes: Mapping[str, str],
    ) -> None:
        if self._diagnostic is None:
            return
        try:
            self._diagnostic(event, severity, attributes)
        except Exception:
            # Diagnostics must never change Provider behavior.
            return

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
                diagnostic=self._report_runtime_lifecycle,
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

    def _report_runtime_lifecycle(
        self,
        event: str,
        severity: str,
        attributes: Mapping[str, str],
    ) -> None:
        self._report(
            event,
            severity,
            {"provider": PROVIDER_ID, **dict(attributes)},
        )

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
            if isinstance(pending, (_Job, _Warmup)):
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
    def __init__(
        self,
        context: object,
        character: object,
        artifacts: object,
        diagnostics: object | None = None,
    ) -> None:
        self._context = context
        self._character = character
        self._artifacts = artifacts
        self._diagnostics = diagnostics
        self._jobs: dict[str, _Job] = {}
        self._jobs_lock = threading.RLock()
        try:
            self._config = _parse_config(context.config.get())
            self._coordinator: _Coordinator | None = _Coordinator(
                self._config,
                self._emit_diagnostic,
            )
        except (TypeError, ValueError):
            self._config = None
            self._coordinator = None

    def status(self) -> dict[str, Any]:
        available, reason_code, stage = _config_readiness(self._config)
        return {
            "label": "GPT-SoVITS",
            "available": available,
            "reasonCode": reason_code,
            "stage": stage,
        }

    def begin(self, request: Mapping[str, Any]) -> str | dict[str, str]:
        if self._config is None or not self._config.enabled or self._coordinator is None:
            return {"errorCode": "TTS_PROVIDER_UNAVAILABLE"}
        character_id = request.get("characterId")
        if not isinstance(character_id, str) or not character_id:
            return {"errorCode": "TTS_REQUEST_INVALID"}
        try:
            extension = self._character.get(character_id)
            voice = _parse_character_voice(self._character, character_id, extension)
        except Exception as error:
            return {"errorCode": _stable_error_code(error)}
        job = _Job(self._context, self._artifacts, request, voice)
        try:
            self._coordinator.submit(job)
        except Exception as error:
            job._disposer()
            return {"errorCode": _stable_error_code(error)}
        job_id = f"job_{uuid.uuid4().hex}"
        with self._jobs_lock:
            self._jobs[job_id] = job
        return job_id

    def poll(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"state": "failed", "errorCode": "TTS_JOB_NOT_FOUND"}
        result = job.poll()
        if result.get("state") != "running":
            with self._jobs_lock:
                if self._jobs.get(job_id) is job:
                    del self._jobs[job_id]
        return result

    def cancel(self, job_id: str) -> bool:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        return job.cancel() if job is not None else False

    def warmup(self, character_id: str) -> bool | dict[str, object]:
        config = self._config
        coordinator = self._coordinator
        available, reason_code, stage = _config_readiness(config)
        if not available or coordinator is None:
            return {
                "accepted": False,
                "reasonCode": reason_code,
                "stage": stage,
                "errorType": "RuntimeConfigurationError",
            }
        if config is None or config.custom_base_url is not None:
            return False
        try:
            extension = self._character.get(character_id)
            voice = _parse_character_voice(self._character, character_id, extension)
        except Exception as error:
            reason_code = _stable_error_code(error)
            return {
                "accepted": False,
                "reasonCode": reason_code,
                "stage": "character_configuration",
                "errorType": type(error).__name__,
            }
        try:
            coordinator.warmup(voice)
        except Exception as error:
            reason_code = _stable_error_code(error)
            return {
                "accepted": False,
                "reasonCode": reason_code,
                "stage": "queue",
                "errorType": type(error).__name__,
            }
        return True

    def reconfigure(self, values: Mapping[str, Any]) -> str:
        config = _parse_config(values)
        coordinator = self._coordinator
        if coordinator is None:
            coordinator = _Coordinator(config, self._emit_diagnostic)
            self._coordinator = coordinator
        else:
            coordinator.reconfigure(config)
        self._config = config
        return "applied"

    def close(self) -> None:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.cancel()
        coordinator = self._coordinator
        self._coordinator = None
        if coordinator is not None:
            coordinator.close()
        for job in jobs:
            job.close()

    def _emit_diagnostic(
        self,
        event: str,
        severity: str,
        attributes: Mapping[str, str],
    ) -> None:
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.emit(
                {
                    "event": event,
                    "severity": severity,
                    "attributes": dict(attributes),
                }
            )
        except Exception:
            return


class GPTSoVITSPlugin:
    def setup(self, context: object) -> None:
        hub = context.get("sakura.tts")
        character = context.get("sakura.host.character")
        artifacts = context.get("sakura.host.artifacts")
        diagnostics = context.get("sakura.host.diagnostics")
        settings = context.get("sakura.host.settings")
        surface = context.get("sakura.host.settings.surface-v0")
        user_root = Path(context.data_path(".")).parents[2]
        config_patch = _startup_config_patch(context.config.get(), user_root)
        if config_patch:
            context.config.update(config_patch)

        def save_runtime_settings(values: Mapping[str, Any]) -> object:
            patch = _settings_values(values)
            merged = {**context.config.get(), **patch}
            patch.update(_startup_config_patch(merged, user_root))
            return context.config.update(patch)

        provider = GPTSoVITSProvider(context, character, artifacts, diagnostics)
        context.effect(provider.close)
        context.provide(
            SERVICE_KEY,
            provider,
            exports=("status", "warmup", "begin", "poll", "cancel"),
        )
        hub.registerProvider(
            {
                "providerId": PROVIDER_ID,
                "serviceKey": SERVICE_KEY,
                "label": "GPT-SoVITS",
            }
        )
        context.effect(lambda: hub.unregisterProvider(PROVIDER_ID, SERVICE_KEY))
        context.config.on_change(provider.reconfigure)
        settings.register(
            {
                "sectionId": "runtime",
                "title": "GPT-SoVITS 语音服务",
                "order": 100,
                "fields": [
                    {
                        "key": "endpointMode",
                        "label": "服务来源",
                        "type": "select",
                        "default": "managed",
                        "description": "内置服务由 Sakura 启动和停止；已有服务只负责连接。",
                        "options": [
                            {"label": "Sakura 内置（推荐）", "value": "managed"},
                            {"label": "连接已有服务", "value": "custom"},
                        ],
                    },
                    {"key": "customBaseUrl", "label": "已有服务地址", "type": "string", "default": "", "description": "仅在连接已有服务时使用，例如 http://127.0.0.1:9880。", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "ttsPath", "label": "接口路径", "type": "string", "default": "/tts", "description": "已有服务的语音合成接口路径。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "remoteReferenceRoot", "label": "远程参考音频目录", "type": "string", "default": "", "description": "服务位于其他设备时，用于映射角色参考音频。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "workDir", "label": "内置服务工作目录", "type": "string", "default": "", "description": "Sakura 内置 GPT-SoVITS 的程序目录。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "pythonPath", "label": "Python 解释器", "type": "string", "default": "", "description": "留空时从内置运行环境自动查找。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "ttsConfigPath", "label": "推理配置", "type": "string", "default": "", "description": "可选的 GPT-SoVITS 推理配置文件。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "timeoutSeconds", "label": "合成超时", "type": "integer", "default": 60, "minimum": 1, "maximum": 300, "step": 1, "description": "等待一次语音合成完成的最长时间（秒）。", "placement": "advanced"},
                ],
            },
            load=lambda: _settings_values(context.config.get()),
            save=save_runtime_settings,
        )
        surface.register("runtime", "voice")
        bundle = TTSBundleResource(
            user_root=user_root,
            config_get=context.config.get,
            config_update=context.config.update,
            entry=recommend_gpt_sovits_bundle,
            custom_endpoint=_uses_custom_endpoint,
        )
        context.effect(bundle.close)
        settings.register(
            bundle.descriptor("aboutBundle", "GPT-SoVITS", "GPT-SoVITS 本地运行组件"),
            load=bundle.load,
            actions={
                "installBundle": bundle.start,
                "retryBundle": bundle.start,
                "cancelBundle": bundle.cancel,
            },
        )
        surface.register("aboutBundle", "plugin")


def _parse_config(value: Mapping[str, Any]) -> _ProviderConfig:
    custom = str(value.get("customBaseUrl") or "").strip().rstrip("/") or None
    raw_mode = str(value.get("endpointMode") or "").strip().lower()
    mode = raw_mode or ("custom" if custom is not None else "managed")
    if mode not in {"managed", "custom"} or (mode == "custom" and custom is None):
        raise ValueError("TTS_CONFIG_INVALID")
    if mode == "custom" and custom is not None:
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
        custom_base_url=custom if mode == "custom" else None,
        tts_path=tts_path,
        timeout_seconds=timeout,
        remote_reference_root=str(value.get("remoteReferenceRoot") or "").strip() or None,
        work_dir=_absolute_path(value.get("workDir")),
        python_path=_absolute_path(value.get("pythonPath")),
        tts_config_path=_absolute_path(value.get("ttsConfigPath")),
    )


def _settings_values(value: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(value)
    custom = str(values.get("customBaseUrl") or "").strip()
    values["endpointMode"] = str(values.get("endpointMode") or "").strip().lower() or (
        "custom" if custom else "managed"
    )
    for key in ("workDir", "pythonPath", "ttsConfigPath"):
        if key in values:
            values[key] = user_facing_path(str(values.get(key) or ""))
    return values


def _uses_custom_endpoint(value: Mapping[str, Any]) -> bool:
    raw_mode = str(value.get("endpointMode") or "").strip().lower()
    if raw_mode:
        return raw_mode == "custom"
    return bool(str(value.get("customBaseUrl") or "").strip())


def _startup_config_patch(
    value: Mapping[str, Any],
    user_root: Path,
) -> dict[str, object]:
    """Normalize stored paths and bind an already installed managed bundle."""

    patch: dict[str, object] = {}
    custom = str(value.get("customBaseUrl") or "").strip()
    raw_mode = str(value.get("endpointMode") or "").strip().lower()
    mode = raw_mode or ("custom" if custom else "managed")
    path_keys = ("workDir", "pythonPath", "ttsConfigPath")
    if mode == "managed":
        installed = installed_bundle_result(user_root)
        if installed is not None:
            expected = {
                "workDir": user_facing_path(installed.work_dir),
                "pythonPath": (
                    user_facing_path(installed.python_path)
                    if installed.python_path
                    else ""
                ),
                "ttsConfigPath": (
                    user_facing_path(installed.tts_config_path)
                    if installed.tts_config_path
                    else ""
                ),
            }
            for key, normalized in expected.items():
                if normalized != str(value.get(key) or "").strip():
                    patch[key] = normalized
            return patch
    for key in path_keys:
        raw = str(value.get(key) or "").strip()
        if raw:
            normalized = user_facing_path(raw)
            if normalized != raw:
                patch[key] = normalized
    return patch


def _config_available(config: _ProviderConfig) -> bool:
    return _config_readiness(config)[0]


def _config_readiness(
    config: _ProviderConfig | None,
) -> tuple[bool, str, str]:
    if config is None or not config.enabled:
        return False, "TTS_PROVIDER_UNAVAILABLE", "configuration"
    if config.custom_base_url is not None:
        return True, "READY", "custom_endpoint"
    work_dir = config.work_dir
    if work_dir is None or not work_dir.is_dir():
        return False, "TTS_RUNTIME_DIRECTORY_INVALID", "work_dir"
    if not (work_dir / "api_v2.py").is_file():
        return False, "TTS_RUNTIME_ENTRY_MISSING", "entrypoint"
    python = config.python_path or find_usable_runtime_python(work_dir / "runtime")
    if python is None or not python.is_file():
        return False, "TTS_RUNTIME_PYTHON_MISSING", "python"
    return True, "READY", "runtime_configuration"


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
