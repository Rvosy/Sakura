"""Stdlib-only helpers owned by the GPT-SoVITS plugin process."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import signal
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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse, urlunparse


DEFAULT_TONE = "中性"
DEFAULT_GPT_SOVITS_BASE_URL = "http://127.0.0.1:9880"
DEFAULT_GPT_SOVITS_TTS_PATH = "/tts"
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LATIN = re.compile(r"[A-Za-z]")


class OperationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ToneReference:
    tone: str
    ref_audio_path: Path
    ref_text: str
    ref_lang: str


@dataclass
class _TTSRequest:
    text: str
    tone: str | None
    request_id: str = ""
    cancelled: bool = False


@dataclass(frozen=True)
class GPTSoVITSTTSSettings:
    enabled: bool
    api_url: str
    ref_audio_path: Path
    ref_text_path: Path
    ref_text: str
    provider: str = "gpt-sovits"
    gpt_model_path: Path | None = None
    sovits_model_path: Path | None = None
    work_dir: Path | None = None
    python_path: Path | None = None
    tts_config_path: Path | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"
    timeout_seconds: int = 60
    tone_references: dict[str, list[ToneReference]] = field(default_factory=dict)
    custom_base_url: str | None = None
    tts_path: str = DEFAULT_GPT_SOVITS_TTS_PATH
    remote_reference_root: str | None = None
    character_id: str = ""
    character_package_dir: Path | None = None

    def validate(self) -> None:
        endpoint = urlparse(self.api_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
            raise ValueError("TTS_CONFIG_INVALID")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("TTS_CONFIG_INVALID")
        if not self.ref_text or not self.ref_lang or not self.text_lang:
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
        references = [
            item for values in self.tone_references.values() for item in values
        ] or [ToneReference(DEFAULT_TONE, self.ref_audio_path, self.ref_text, self.ref_lang)]
        if any(not item.ref_audio_path.is_file() or not item.ref_text for item in references):
            raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
        if self.custom_base_url is None:
            if self.work_dir is None or not self.work_dir.is_dir():
                raise ValueError("TTS_RUNTIME_INVALID")
            if self.python_path is not None and not self.python_path.is_file():
                raise ValueError("TTS_RUNTIME_INVALID")
            if self.tts_config_path is not None and not self.tts_config_path.is_file():
                raise ValueError("TTS_RUNTIME_INVALID")
            if self.gpt_model_path is not None and not self.gpt_model_path.is_file():
                raise ValueError("TTS_CHARACTER_CONFIG_INVALID")
            if self.sovits_model_path is not None and not self.sovits_model_path.is_file():
                raise ValueError("TTS_CHARACTER_CONFIG_INVALID")


def is_loopback_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _open_url(
    url: str | urllib.request.Request,
    *,
    timeout: float,
) -> object:
    target = str(getattr(url, "full_url", url))
    if is_loopback_base_url(target):
        return _LOOPBACK_OPENER.open(url, timeout=timeout)
    return urllib.request.urlopen(url, timeout=timeout)


def _read_url(
    request: str | urllib.request.Request,
    *,
    timeout: float,
    cancel_checker: Callable[[], None] | None = None,
) -> tuple[bytes, int | None]:
    if cancel_checker is None:
        with _open_url(request, timeout=timeout) as response:
            return response.read(), getattr(response, "status", None)
    done = threading.Event()
    abort = threading.Event()
    state: dict[str, Any] = {}
    lock = threading.Lock()

    def read() -> None:
        chunks: list[bytes] = []
        try:
            with _open_url(request, timeout=timeout) as response:
                with lock:
                    state["response"] = response
                state["status"] = getattr(response, "status", None)
                while not abort.is_set():
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                if not abort.is_set():
                    state["body"] = b"".join(chunks)
        except BaseException as error:
            if not abort.is_set():
                state["error"] = error
        finally:
            done.set()

    threading.Thread(target=read, name="gpt-sovits-http-read", daemon=True).start()
    try:
        while not done.wait(0.05):
            cancel_checker()
        cancel_checker()
    except BaseException:
        abort.set()
        with lock:
            response = state.get("response")
        try:
            if response is not None:
                response.close()
        except Exception:
            pass
        raise
    error = state.get("error")
    if isinstance(error, BaseException):
        raise error
    return bytes(state.get("body", b"")), state.get("status")


def terminate_process_tree(process: subprocess.Popen[Any], *, timeout: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(0.1, timeout),
        )
    else:
        descendants = _posix_descendant_pids(process.pid)
        for pid in (*reversed(descendants), process.pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=max(0.05, timeout))
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        process.kill()
    else:
        for pid in (*reversed(descendants), process.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    process.wait(timeout=max(0.05, timeout))


def _posix_descendant_pids(root_pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        try:
            pid_text, parent_text = line.split(None, 1)
            pid, parent = int(pid_text), int(parent_text)
        except (TypeError, ValueError):
            continue
        children.setdefault(parent, []).append(pid)
    descendants: list[int] = []
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def find_usable_runtime_python(runtime_dir: Path) -> Path | None:
    names = (
        ("python.exe", "python")
        if sys.platform == "win32"
        else ("bin/python3", "bin/python", "python3", "python")
    )
    for name in names:
        candidate = runtime_dir / name
        if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
            return candidate
    return None


def user_facing_path(value: str | Path) -> str:
    return os.path.normpath(str(value)) if str(value) else ""


def _verify_wav(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with wave.open(str(path), "rb") as handle:
            return (
                handle.getnchannels() in (1, 2)
                and handle.getsampwidth() > 0
                and handle.getframerate() > 0
            )
    except (OSError, EOFError, wave.Error):
        return False


@dataclass(frozen=True)
class _Endpoint:
    base_url: str
    synthesis_url: str
    kind: str


class _ManagedRuntime:
    def __init__(
        self,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        is_closed: Callable[[], bool],
    ) -> None:
        self.settings = settings
        self._base_dir = base_dir
        self._is_closed = is_closed
        self._server_process: subprocess.Popen[Any] | None = None
        self._log_handle: Any = None
        self._weights_ready = False
        self._service_ready = False

    def ensure_available(self, fail: Callable[[str], None]) -> bool:
        if self._is_closed():
            return False
        process = self._server_process
        if self._service_ready and process is not None and process.poll() is None:
            return True
        parsed = urlparse(self.settings.api_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if process is None:
            if _probe_tcp(host, port, min(self.settings.timeout_seconds, 3)):
                fail("TTS_PORT_OCCUPIED")
                return False
            if not self._start(fail):
                return False
            process = self._server_process
        assert process is not None
        deadline = time.monotonic() + self.settings.timeout_seconds
        while time.monotonic() < deadline:
            if self._is_closed():
                return False
            if process.poll() is not None:
                fail("TTS_RUNTIME_EXITED")
                return False
            if _probe_http(self.settings.api_url, 1):
                self._service_ready = True
                return True
            time.sleep(0.05)
        fail("TTS_RUNTIME_TIMEOUT")
        return False

    def ensure_weights(
        self,
        fail: Callable[[str], None],
        cancel_checker: Callable[[], None] | None,
    ) -> bool:
        if self._weights_ready:
            return True
        for endpoint, path in (
            ("set_gpt_weights", self.settings.gpt_model_path),
            ("set_sovits_weights", self.settings.sovits_model_path),
        ):
            if path is None:
                continue
            if cancel_checker is not None:
                cancel_checker()
            parsed = urlparse(self.settings.api_url)
            base_path = parsed.path.rsplit("/", 1)[0]
            url = urlunparse(parsed._replace(
                path=f"{base_path}/{endpoint}" if base_path else f"/{endpoint}",
                query=urlencode({"weights_path": os.path.normpath(str(path))}),
            ))
            try:
                _read_url(
                    urllib.request.Request(url, method="GET"),
                    timeout=self.settings.timeout_seconds,
                    cancel_checker=cancel_checker,
                )
            except Exception:
                fail("TTS_WEIGHTS_UNAVAILABLE")
                return False
        self._weights_ready = True
        return True

    def restart_after_failure(self, status: int, body: str) -> bool:
        if status != 400 or "tts failed" not in body.lower() or "broken pipe" not in body.lower():
            return False
        self.close()
        self._service_ready = False
        self._weights_ready = False
        return True

    def _start(self, fail: Callable[[str], None]) -> bool:
        work_dir = self.settings.work_dir
        if work_dir is None or not work_dir.is_dir():
            fail("TTS_RUNTIME_INVALID")
            return False
        python = self.settings.python_path or find_usable_runtime_python(work_dir / "runtime")
        script = work_dir / "api_v2.py"
        if python is None or not python.is_file() or not script.is_file():
            fail("TTS_RUNTIME_INVALID")
            return False
        command = [os.path.normpath(str(python)), os.path.normpath(str(script))]
        if self.settings.tts_config_path is not None:
            command.extend(["-c", os.path.normpath(str(self.settings.tts_config_path))])
        parsed = urlparse(self.settings.api_url)
        command.extend(["-a", "127.0.0.1" if parsed.hostname == "localhost" else str(parsed.hostname)])
        if parsed.port is not None:
            command.extend(["-p", str(parsed.port)])
        log_path = self._base_dir / "gpt-sovits.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8")
        kwargs: dict[str, Any] = {
            "cwd": str(work_dir),
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": {**os.environ, "PYTHONIOENCODING": "utf-8"},
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        try:
            self._server_process = subprocess.Popen(command, **kwargs)
        except OSError:
            log.close()
            fail("TTS_RUNTIME_START_FAILED")
            return False
        self._log_handle = log
        return True

    def close(self) -> None:
        process = self._server_process
        self._server_process = None
        if process is not None and process.poll() is None:
            terminate_process_tree(process, timeout=0.5)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class GptSovitsEndpointResolver:
    def __init__(
        self,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        resource_manager: object,
        is_closed: Callable[[], bool],
    ) -> None:
        del resource_manager
        base_url = settings.custom_base_url or DEFAULT_GPT_SOVITS_BASE_URL
        synthesis_url = f"{base_url.rstrip('/')}{settings.tts_path}"
        self.endpoint = _Endpoint(
            base_url,
            synthesis_url,
            "custom" if settings.custom_base_url is not None else "managed",
        )
        self.settings = replace(settings, api_url=synthesis_url)
        self.runtime = (
            None
            if settings.custom_base_url is not None
            else _ManagedRuntime(self.settings, base_dir=base_dir, is_closed=is_closed)
        )
        self._custom_checked = False
        self._is_closed = is_closed

    def ensure_available(self, fail: Callable[[str], None]) -> bool:
        if self.runtime is not None:
            self.runtime.settings = self.settings
            return self.runtime.ensure_available(fail)
        if self._custom_checked:
            return True
        parsed = urlparse(self.endpoint.base_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        timeout = min(self.settings.timeout_seconds, 3)
        if not host or not _probe_tcp(host, port, timeout) or not _probe_http(self.endpoint.synthesis_url, timeout):
            fail("TTS_RUNTIME_UNAVAILABLE")
            return False
        self._custom_checked = True
        return True

    def ensure_character_weights(
        self,
        fail: Callable[[str], None],
        *,
        cancel_checker: Callable[[], None] | None,
    ) -> bool:
        if self.runtime is None:
            return True
        return self.runtime.ensure_weights(fail, cancel_checker)

    def restart_owned_after_http_failure(self, status: int, body: str) -> bool:
        return self.runtime is not None and self.runtime.restart_after_failure(status, body)

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()


class GptSovitsEndpointSupervisor:
    def __init__(self, resolver: GptSovitsEndpointResolver) -> None:
        self.resolver = resolver
        self.settings = resolver.settings

    @property
    def endpoint_kind(self) -> str:
        return self.resolver.endpoint.kind

    def _ensure_service_available(self, fail: Callable[[str], None]) -> bool:
        self.resolver.settings = self.settings
        return self.resolver.ensure_available(fail)

    def _ensure_character_weights(
        self,
        fail: Callable[[str], None],
        *,
        cancel_checker: Callable[[], None] | None = None,
    ) -> bool:
        return self.resolver.ensure_character_weights(
            fail,
            cancel_checker=cancel_checker,
        )

    def _restart_local_service_after_http_failure(self, status: int, body: str) -> bool:
        return self.resolver.restart_owned_after_http_failure(status, body)


class GPTSoVITSSynthesisEngine:
    def synthesize(
        self,
        queue: object,
        request: _TTSRequest,
        *,
        fail: Callable[[str], None],
        skip: Callable[[str], None],
    ) -> Path | None:
        del skip
        supervisor = getattr(queue, "_supervisor")
        settings = getattr(queue, "settings")

        def check_cancelled() -> None:
            if request.cancelled:
                raise OperationCancelled("TTS job cancelled")

        restart_attempted = False
        while True:
            check_cancelled()
            if not supervisor._ensure_service_available(fail):
                return None
            if not supervisor._ensure_character_weights(fail):
                return None
            reference = getattr(queue, "_select_reference")(request.tone)
            try:
                reference_path = _reference_path(settings, reference.ref_audio_path)
            except ValueError as error:
                fail(str(error))
                return None
            text_lang = settings.text_lang.strip().lower() or "ja"
            if text_lang in {"ja", "all_ja", "zh", "all_zh", "ko", "all_ko", "yue", "all_yue"} and _LATIN.search(request.text):
                text_lang = "auto_yue" if text_lang in {"yue", "all_yue"} else "auto"
            payload = {
                "text": request.text,
                "text_lang": text_lang,
                "ref_audio_path": reference_path,
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
            http_request = urllib.request.Request(
                settings.api_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                audio, _status = _read_url(
                    http_request,
                    timeout=settings.timeout_seconds,
                    cancel_checker=check_cancelled,
                )
                break
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                if not restart_attempted and supervisor._restart_local_service_after_http_failure(error.code, body):
                    restart_attempted = True
                    continue
                fail("TTS_SYNTHESIS_FAILED")
                return None
            except (urllib.error.URLError, TimeoutError, OSError):
                fail("TTS_RUNTIME_UNAVAILABLE")
                return None
        if not audio:
            fail("TTS_AUDIO_INVALID")
            return None
        cache_dir = Path(getattr(queue, "_cache_dir"))
        with tempfile.NamedTemporaryFile(
            prefix="gpt-sovits-",
            suffix=".wav",
            delete=False,
            dir=str(cache_dir),
        ) as handle:
            handle.write(audio)
            path = Path(handle.name)
        if not _verify_wav(path):
            path.unlink(missing_ok=True)
            fail("TTS_AUDIO_INVALID")
            return None
        return path


def _reference_path(settings: GPTSoVITSTTSSettings, path: Path) -> str:
    custom = settings.custom_base_url
    if custom is None or is_loopback_base_url(custom):
        return str(path)
    root = str(settings.remote_reference_root or "").strip()
    package = settings.character_package_dir
    if not root or package is None or not settings.character_id:
        raise ValueError("TTS_REFERENCE_AUDIO_UNAVAILABLE")
    try:
        relative = path.resolve(strict=True).relative_to(package.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ValueError("TTS_REFERENCE_AUDIO_UNAVAILABLE") from error
    parts = (settings.character_id, *relative.parts)
    if root.startswith("\\\\") or (len(root) >= 2 and root[1] == ":") or "\\" in root:
        return str(PureWindowsPath(root).joinpath(*parts))
    return str(PurePosixPath(root).joinpath(*parts))


def _probe_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _probe_http(api_url: str, timeout: float) -> bool:
    parsed = urlparse(api_url)
    base_path = parsed.path.rsplit("/", 1)[0]
    request = urllib.request.Request(
        urlunparse(parsed._replace(path=base_path or "/", query="")),
        method="GET",
    )
    try:
        with _open_url(request, timeout=timeout):
            pass
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True


try:
    from ._bundle import TTSBundleResource, recommend_gpt_sovits_bundle
except ImportError:
    from _bundle import TTSBundleResource, recommend_gpt_sovits_bundle
