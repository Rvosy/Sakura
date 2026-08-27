from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from app.core.cancellation import OperationCancelled
from app.core.http_client import read_url_cancellable, urlopen_direct_for_loopback
from app.core.process_tree import terminate_process_tree
from app.llm.chat_reply import DEFAULT_TONE
from app.voice import audio_checks as _audio_checks
from app.voice.runtime_compat import find_usable_runtime_python, user_facing_path
from app.voice.tts_endpoint import is_loopback_base_url
from app.voice.tts_bundle import GENIE_TTS, is_bundle_supported
from app.voice.tts_bundle_resource import TTSBundleResource
from app.voice.tts_service import (
    _build_genie_endpoint_url,
    _build_genie_start_command,
    _encode_genie_character_name,
    _local_tts_subprocess_env,
    _probe_genie_api_url,
    _probe_tcp_port,
    _resolve_genie_converter_script,
    _subprocess_path,
)
from app.voice.tts_settings import DEFAULT_GENIE_TTS_API_URL, ToneReference
from app.voice.tts_synthesis import _write_genie_audio
from app.voice.tts_types import _TTSRequest


PROVIDER_ID = "sakura.tts.genie"
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_STOP = object()
_CONVERSION_FORMAT = 1


@dataclass(frozen=True)
class _ProviderConfig:
    enabled: bool
    endpoint_mode: str
    api_url: str
    timeout_seconds: int
    work_dir: Path | None


@dataclass(frozen=True)
class _CharacterVoice:
    character_id: str
    remote_character_name: str
    ref_lang: str
    tone_references: dict[str, list[ToneReference]]
    onnx_model_dir: Path | None
    gpt_model_path: Path | None
    sovits_model_path: Path | None

    def reference(self, tone: object) -> ToneReference:
        tone_key = str(tone or DEFAULT_TONE).strip() or DEFAULT_TONE
        references = self.tone_references.get(tone_key)
        if not references:
            references = self.tone_references.get(DEFAULT_TONE)
        if not references:
            references = [item for values in self.tone_references.values() for item in values]
        if not references:
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
        return references[0]


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

    def attach_request(self, request: _TTSRequest) -> None:
        with self._lock:
            self._request = request
            if self._cancelled.is_set():
                request.cancelled = True

    def succeed(self) -> None:
        with self._lock:
            self._state = "cancelled" if self._cancelled.is_set() else "succeeded"
            self._done.set()

    def fail(self, error_code: object) -> None:
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

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise OperationCancelled("Genie TTS job cancelled")

    def wait_or_cancel(self, seconds: float) -> None:
        if self._cancelled.wait(max(0.0, seconds)):
            self.check_cancelled()

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
        self._done.wait()


class _Warmup:
    def __init__(self, voice: _CharacterVoice) -> None:
        self.voice = voice
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise OperationCancelled("Genie TTS warmup cancelled")

    def wait_or_cancel(self, seconds: float) -> None:
        if self._cancelled.wait(max(0.0, seconds)):
            self.check_cancelled()


class _Coordinator:
    def __init__(
        self,
        config: _ProviderConfig,
        cache_root: Path,
        log_path: Path,
    ) -> None:
        self._config = config
        self._cache_root = cache_root
        self._log_path = log_path
        self._queue: queue.Queue[_Job | _Warmup | object] = queue.Queue(maxsize=16)
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._active: _Job | _Warmup | None = None
        self._server_process: subprocess.Popen[object] | None = None
        self._conversion_process: subprocess.Popen[object] | None = None
        self._log_handle: Any = None
        self._endpoint_ready = False
        self._loaded_model_key: tuple[str, str, str] | None = None
        self._reference_key: tuple[str, str, str, str] | None = None
        self._pending_config: _ProviderConfig | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="sakura-genie-coordinator",
            daemon=True,
        )
        self._thread.start()

    def submit(self, job: _Job) -> None:
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
            reset_runtime = (
                previous.endpoint_mode != config.endpoint_mode
                or previous.api_url != config.api_url
                or (
                    previous.endpoint_mode == "managed"
                    and config.endpoint_mode == "managed"
                    and previous.work_dir != config.work_dir
                )
            )
            self._config = config
            if not reset_runtime:
                return
        # Only _server_process is owned by Sakura; custom endpoints never
        # enter _reset_managed_runtime and are never terminated.
        self._reset_managed_runtime()

    def _execute(self, job: _Job) -> None:
        source: Path | None = None
        try:
            job.check_cancelled()
            character_name = self._prepare_voice(
                job.voice,
                job.request.get("options", {}).get("tone"),
                job,
            )

            request = _TTSRequest(
                text=str(job.request["text"]),
                tone=str(job.request.get("options", {}).get("tone", DEFAULT_TONE)),
                request_id=str(job.request["requestId"]),
            )
            job.attach_request(request)
            audio_data = self._post_json(
                "tts",
                {
                    "character_name": _encode_genie_character_name(character_name),
                    "text": request.text,
                    "split_sentence": False,
                },
                timeout=max(self._config.timeout_seconds, 120),
                cancel_checker=job.check_cancelled,
            )
            job.check_cancelled()
            source = job.output_path.with_name(f"source-{uuid.uuid4().hex}.wav")
            if not _write_genie_audio(audio_data, source):
                raise RuntimeError("TTS_AUDIO_INVALID")
            issue = _audio_checks._verify_generated_audio(source)
            if issue is not None:
                raise RuntimeError("TTS_AUDIO_INVALID")
            os.replace(source, job.output_path)
            source = None
            job.succeed()
        except OperationCancelled:
            if self._config.endpoint_mode == "managed":
                self._reset_managed_runtime()
            job.cancel()
            job.fail("TTS_SYNTHESIS_CANCELLED")
        except Exception as error:
            job.fail(getattr(error, "code", str(error)))
        finally:
            if source is not None:
                source.unlink(missing_ok=True)

    def _execute_warmup(self, warmup: _Warmup) -> None:
        if self._config.endpoint_mode != "managed":
            return
        try:
            self._prepare_voice(warmup.voice, DEFAULT_TONE, warmup)
        except OperationCancelled:
            return
        except Exception:
            # Warmup is best effort. The first synthesis retries the same
            # preparation path and publishes the user-visible terminal state.
            return

    def _prepare_voice(
        self,
        voice: _CharacterVoice,
        tone: object,
        operation: _Job | _Warmup,
    ) -> str:
        if self._config.endpoint_mode != "managed":
            self._ensure_custom_endpoint(operation)
            return voice.remote_character_name
        onnx_dir = self._ensure_onnx_model(voice, operation)
        self._ensure_managed_endpoint(operation)
        reference = voice.reference(tone)
        model_key = (
            voice.character_id,
            str(onnx_dir.resolve(strict=True)),
            reference.ref_lang,
        )
        if self._loaded_model_key != model_key:
            self._post_state(
                "load_character",
                {
                    "character_name": _encode_genie_character_name(voice.character_id),
                    "onnx_model_dir": _subprocess_path(onnx_dir),
                    "language": reference.ref_lang,
                },
                operation,
            )
            self._loaded_model_key = model_key
            self._reference_key = None
        reference_key = (
            voice.character_id,
            str(reference.ref_audio_path.resolve(strict=True)),
            reference.ref_text,
            reference.ref_lang,
        )
        if self._reference_key != reference_key:
            self._post_state(
                "set_reference_audio",
                {
                    "character_name": _encode_genie_character_name(voice.character_id),
                    "audio_path": _subprocess_path(reference.ref_audio_path),
                    "audio_text": reference.ref_text,
                    "language": reference.ref_lang,
                },
                operation,
            )
            self._reference_key = reference_key
        return voice.character_id

    def _post_state(
        self,
        endpoint: str,
        payload: dict[str, object],
        job: _Job | _Warmup,
    ) -> None:
        job.check_cancelled()
        self._post_json(
            endpoint,
            payload,
            timeout=min(self._config.timeout_seconds, 20),
            cancel_checker=None,
        )
        # State-changing calls deliberately finish before observing cancel so
        # no late response can overwrite the next character's shared state.
        job.check_cancelled()

    def _post_json(
        self,
        endpoint: str,
        payload: dict[str, object],
        *,
        timeout: int,
        cancel_checker: Callable[[], None] | None,
    ) -> bytes:
        request = urllib.request.Request(
            url=_build_genie_endpoint_url(self._config.api_url, endpoint),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        body, _status = read_url_cancellable(
            urlopen_direct_for_loopback,
            request,
            timeout=timeout,
            cancel_checker=cancel_checker,
        )
        return body

    def _ensure_custom_endpoint(self, job: _Job | _Warmup) -> None:
        if self._endpoint_ready:
            return
        job.check_cancelled()
        if not _probe_genie_api_url(
            self._config.api_url,
            min(self._config.timeout_seconds, 3),
        ):
            raise RuntimeError("TTS_RUNTIME_UNAVAILABLE")
        job.check_cancelled()
        self._endpoint_ready = True

    def _ensure_managed_endpoint(self, job: _Job | _Warmup) -> None:
        process = self._server_process
        if self._endpoint_ready and process is not None and process.poll() is None:
            return
        if process is not None and process.poll() is not None:
            self._reset_managed_runtime()
            process = None
        host, port = _endpoint_host_port(self._config.api_url)
        if process is None:
            if _probe_tcp_port(host, port, min(self._config.timeout_seconds, 3)):
                # Managed mode never adopts a process it did not create.
                raise RuntimeError("TTS_PORT_OCCUPIED")
            self._start_managed_runtime(host, port)
            process = self._server_process
        assert process is not None
        deadline = time.monotonic() + max(3, min(self._config.timeout_seconds, 180))
        while time.monotonic() < deadline:
            job.check_cancelled()
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError("TTS_RUNTIME_EXITED")
            if _probe_genie_api_url(self._config.api_url, 1):
                self._endpoint_ready = True
                return
            job.wait_or_cancel(0.05)
        raise RuntimeError("TTS_RUNTIME_TIMEOUT")

    def _start_managed_runtime(self, host: str, port: int) -> None:
        work_dir = self._config.work_dir
        if work_dir is None or not work_dir.is_dir():
            raise RuntimeError("TTS_RUNTIME_INVALID")
        python_exe = find_usable_runtime_python(work_dir / "runtime")
        if python_exe is None:
            raise RuntimeError("TTS_RUNTIME_INVALID")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self._log_path.open("a", encoding="utf-8")
        kwargs: dict[str, object] = {
            "cwd": _subprocess_path(work_dir),
            "env": _local_tts_subprocess_env(python_exe),
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
        try:
            process = subprocess.Popen(
                _build_genie_start_command(python_exe, host, port),
                **kwargs,
            )
        except Exception:
            log_handle.close()
            raise
        with self._lock:
            self._server_process = process
            self._log_handle = log_handle

    def _ensure_onnx_model(self, voice: _CharacterVoice, job: _Job | _Warmup) -> Path:
        if voice.onnx_model_dir is not None:
            if not _onnx_files(voice.onnx_model_dir):
                raise RuntimeError("TTS_ONNX_INVALID")
            return voice.onnx_model_dir
        if voice.gpt_model_path is None or voice.sovits_model_path is None:
            raise RuntimeError("TTS_ONNX_UNAVAILABLE")
        fingerprint = {
            "format": _CONVERSION_FORMAT,
            "characterId": voice.character_id,
            "gptSha256": _hash_file(voice.gpt_model_path, job.check_cancelled),
            "sovitsSha256": _hash_file(voice.sovits_model_path, job.check_cancelled),
        }
        digest = hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        final_dir = self._cache_root / digest
        if _valid_conversion(final_dir, fingerprint):
            return final_dir
        for stale in self._cache_root.glob(f"{digest}.staging-*"):
            shutil.rmtree(stale, ignore_errors=True)
        staging = self._cache_root / f"{digest}.staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        promoted = False
        try:
            self._run_converter(
                voice.gpt_model_path,
                voice.sovits_model_path,
                staging,
                job,
            )
            models = sorted(path.name for path in _onnx_files(staging))
            if not models:
                raise RuntimeError("TTS_ONNX_CONVERSION_FAILED")
            marker = {**fingerprint, "models": models}
            (staging / ".sakura-complete.json").write_text(
                json.dumps(marker, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            if final_dir.exists():
                shutil.rmtree(final_dir)
            os.replace(staging, final_dir)
            promoted = True
            return final_dir
        finally:
            if not promoted:
                for _attempt in range(5):
                    shutil.rmtree(staging, ignore_errors=True)
                    if not staging.exists():
                        break
                    time.sleep(0.05)

    def _run_converter(
        self,
        gpt_model: Path,
        sovits_model: Path,
        staging: Path,
        job: _Job | _Warmup,
    ) -> None:
        work_dir = self._config.work_dir
        if work_dir is None:
            raise RuntimeError("TTS_ONNX_CONVERSION_UNAVAILABLE")
        converter = _resolve_genie_converter_script(work_dir)
        if converter is None:
            raise RuntimeError("TTS_ONNX_CONVERSION_UNAVAILABLE")
        python_exe = find_usable_runtime_python(converter.parent / "runtime")
        if python_exe is None:
            raise RuntimeError("TTS_ONNX_CONVERSION_UNAVAILABLE")
        command = [
            _subprocess_path(python_exe),
            _subprocess_path(converter),
            "--pth",
            _subprocess_path(sovits_model),
            "--ckpt",
            _subprocess_path(gpt_model),
            "--out",
            _subprocess_path(staging),
        ]
        kwargs: dict[str, object] = {
            "cwd": _subprocess_path(converter.parent),
            "env": _local_tts_subprocess_env(python_exe),
            "stdin": subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
        with (staging / "converter.log").open("wb") as output:
            process = subprocess.Popen(command, stdout=output, **kwargs)
            with self._lock:
                self._conversion_process = process
            try:
                while process.poll() is None:
                    try:
                        job.wait_or_cancel(0.05)
                    except OperationCancelled:
                        terminate_process_tree(process, timeout=0.35)
                        raise
                if process.returncode != 0:
                    raise RuntimeError("TTS_ONNX_CONVERSION_FAILED")
            finally:
                with self._lock:
                    if self._conversion_process is process:
                        self._conversion_process = None

    def _reset_managed_runtime(self) -> None:
        with self._lock:
            process = self._server_process
            log_handle = self._log_handle
            self._server_process = None
            self._log_handle = None
            self._endpoint_ready = False
            self._loaded_model_key = None
            self._reference_key = None
        if process is not None and process.poll() is None:
            terminate_process_tree(process, timeout=0.35)
        if log_handle is not None:
            log_handle.close()

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
        with self._lock:
            converter = self._conversion_process
        if converter is not None and converter.poll() is None:
            terminate_process_tree(converter, timeout=0.2)
        self._reset_managed_runtime()
        self._thread.join()


class GenieProvider:
    def __init__(self, context: object, character: object, artifacts: object) -> None:
        self._context = context
        self._character = character
        self._artifacts = artifacts
        self._coordinator: _Coordinator | None = None
        self._cache_root = context.data_path("onnx")
        self._log_path = context.data_path("logs/genie.log")
        try:
            self._config = _parse_config(context.config.get())
        except (TypeError, ValueError):
            self._config = None

    def start(self) -> None:
        if self._config is None or not self._config.enabled:
            return
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._coordinator = _Coordinator(
            self._config,
            self._cache_root,
            self._log_path,
        )

    def status(self) -> dict[str, Any]:
        return {
            "label": "Genie TTS",
            "available": self._config is not None
            and self._config.enabled
            and self._coordinator is not None,
        }

    def begin(self, request: Mapping[str, Any]) -> _Job:
        if self._config is None or self._coordinator is None:
            raise RuntimeError("TTS_PROVIDER_UNAVAILABLE")
        character_id = request.get("characterId")
        if not isinstance(character_id, str) or not character_id:
            raise ValueError("TTS_REQUEST_INVALID")
        extension = self._character.get(character_id)
        voice = _parse_character_voice(
            self._character,
            character_id,
            extension,
            endpoint_mode=self._config.endpoint_mode,
        )
        job = _Job(self._context, self._artifacts, request, voice)
        try:
            self._coordinator.submit(job)
        except Exception:
            job.close()
            self._artifacts.release(job._allocation["artifactId"])
            job._disposer()
            raise
        return job

    def warmup(self, character_id: str) -> bool:
        config = self._config
        coordinator = self._coordinator
        if (
            config is None
            or not config.enabled
            or config.endpoint_mode != "managed"
            or coordinator is None
        ):
            return False
        extension = self._character.get(character_id)
        voice = _parse_character_voice(
            self._character,
            character_id,
            extension,
            endpoint_mode=config.endpoint_mode,
        )
        coordinator.warmup(voice)
        return True

    def reconfigure(self, values: Mapping[str, Any]) -> str:
        config = _parse_config(values)
        coordinator = self._coordinator
        if coordinator is None:
            assert self._cache_root is not None and self._log_path is not None
            coordinator = _Coordinator(config, self._cache_root, self._log_path)
            self._coordinator = coordinator
        else:
            coordinator.reconfigure(config)
        self._config = config
        return "applied"

    def close(self) -> None:
        coordinator = self._coordinator
        self._coordinator = None
        if coordinator is not None:
            coordinator.close()


class GeniePlugin:
    def setup(self, context: object) -> None:
        hub = context.get("sakura.tts")
        character = context.get("sakura.host.character")
        artifacts = context.get("sakura.host.artifacts")
        settings = context.get("sakura.host.settings")
        surface = context.get("sakura.host.settings.surface-v0")
        provider = GenieProvider(context, character, artifacts)
        context.effect(provider.close)
        provider.start()
        context.effect(hub.registerProvider(PROVIDER_ID, provider))
        context.config.on_change(provider.reconfigure)
        settings.register(
            {
                "sectionId": "runtime",
                "title": "Genie TTS 语音服务",
                "order": 110,
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
                    {"key": "apiUrl", "label": "已有服务地址", "type": "string", "default": "http://127.0.0.1:9881/", "description": "仅在连接已有服务时使用。", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "workDir", "label": "内置服务工作目录", "type": "string", "default": "", "description": "Sakura 内置 Genie TTS 的程序目录。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}},
                    {"key": "timeoutSeconds", "label": "合成超时", "type": "integer", "default": 60, "minimum": 1, "maximum": 300, "step": 1, "description": "等待一次语音合成完成的最长时间（秒）。", "placement": "advanced"},
                ],
            },
            load=lambda: _settings_values(context.config.get()),
            save=lambda values: context.config.update(_settings_values(values)),
        )
        surface.register("runtime", "voice")
        bundle = TTSBundleResource(
            user_root=Path(context.data_path(".")).parents[2],
            config_get=context.config.get,
            config_update=context.config.update,
            entry=lambda: GENIE_TTS if is_bundle_supported(GENIE_TTS) else None,
            custom_endpoint=lambda values: str(values.get("endpointMode") or "managed").strip().lower() == "custom",
        )
        context.effect(bundle.close)
        settings.register(
            bundle.descriptor("aboutBundle", "Genie TTS", "Genie TTS 本地运行组件"),
            load=bundle.load,
            actions={
                "installBundle": bundle.start,
                "retryBundle": bundle.start,
                "cancelBundle": bundle.cancel,
            },
        )
        surface.register("aboutBundle", "about")


def _parse_config(value: Mapping[str, Any]) -> _ProviderConfig:
    mode = str(value.get("endpointMode") or "managed").strip().lower()
    if mode not in {"managed", "custom"}:
        raise ValueError("TTS_CONFIG_INVALID")
    api_url = str(value.get("apiUrl") or DEFAULT_GENIE_TTS_API_URL).strip().rstrip("/") + "/"
    _endpoint_host_port(api_url)
    if mode == "managed":
        parsed = urlparse(api_url)
        if parsed.scheme != "http" or not is_loopback_base_url(api_url):
            raise ValueError("TTS_CONFIG_INVALID")
    timeout = value.get("timeoutSeconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("TTS_CONFIG_INVALID")
    # Custom endpoints are operator-owned; stale managed paths are ignored.
    work_dir = _absolute_path(value.get("workDir")) if mode == "managed" else None
    if mode == "managed" and work_dir is None:
        raise ValueError("TTS_CONFIG_INVALID")
    return _ProviderConfig(
        enabled=True,
        endpoint_mode=mode,
        api_url=api_url,
        timeout_seconds=timeout,
        work_dir=work_dir,
    )


def _settings_values(value: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(value)
    if "workDir" in values:
        values["workDir"] = user_facing_path(str(values.get("workDir") or ""))
    return values


def _parse_character_voice(
    character: object,
    character_id: str,
    extension: Mapping[str, Any],
    *,
    endpoint_mode: str,
) -> _CharacterVoice:
    if endpoint_mode == "custom":
        remote_name = extension.get("remoteCharacterName")
        if not isinstance(remote_name, str) or not remote_name.strip():
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
        return _CharacterVoice(
            character_id=character_id,
            remote_character_name=remote_name.strip(),
            ref_lang="",
            tone_references={},
            onnx_model_dir=None,
            gpt_model_path=None,
            sovits_model_path=None,
        )

    tone_refs_relative = extension.get("toneRefs")
    if not isinstance(tone_refs_relative, str) or not tone_refs_relative.strip():
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    tone_refs_path = Path(
        character.resolve_resource(character_id, tone_refs_relative.strip())
    )
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
    if not any(references.values()):
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    onnx = _character_resource(
        character,
        character_id,
        extension.get("onnxModelDir"),
    )
    gpt = _character_resource(character, character_id, extension.get("gptModel"))
    sovits = _character_resource(character, character_id, extension.get("sovitsModel"))
    if onnx is None and (gpt is None or sovits is None):
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    return _CharacterVoice(
        character_id=character_id,
        remote_character_name="",
        ref_lang=str(extension.get("refLang") or "ja").strip().lower(),
        tone_references=references,
        onnx_model_dir=onnx,
        gpt_model_path=gpt,
        sovits_model_path=sovits,
    )


def _character_resource(
    character: object,
    character_id: str,
    value: object,
) -> Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
    return Path(character.resolve_resource(character_id, value))


def _endpoint_host_port(api_url: str) -> tuple[str, int]:
    try:
        parsed = urlparse(api_url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("TTS_CONFIG_INVALID") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("TTS_CONFIG_INVALID")
    return parsed.hostname, port or (443 if parsed.scheme == "https" else 80)


def _absolute_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("TTS_CONFIG_INVALID")
    return path


def _hash_file(path: Path, cancel_checker: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            cancel_checker()
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _onnx_files(directory: Path) -> list[Path]:
    try:
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".onnx"
        )
    except OSError:
        return []


def _valid_conversion(directory: Path, fingerprint: Mapping[str, Any]) -> bool:
    try:
        marker = json.loads(
            (directory / ".sakura-complete.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(marker, Mapping):
        return False
    models = marker.get("models")
    return (
        all(marker.get(key) == value for key, value in fingerprint.items())
        and isinstance(models, list)
        and bool(models)
        and all(
            isinstance(name, str)
            and name == Path(name).name
            and (directory / name).is_file()
            and not (directory / name).is_symlink()
            for name in models
        )
    )


def _stable_error_code(value: object) -> str:
    direct = str(getattr(value, "code", value) or "").strip()
    prefix = direct.split(":", 1)[0].strip()
    if _ERROR_CODE.fullmatch(prefix):
        return prefix
    return "TTS_SYNTHESIS_FAILED"
