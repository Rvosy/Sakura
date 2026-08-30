"""Stdlib-only helpers owned by the Genie plugin process."""

from __future__ import annotations

import array
import base64
import ipaddress
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse, urlunparse


DEFAULT_TONE = "中性"
DEFAULT_GENIE_TTS_API_URL = "http://127.0.0.1:9881/"
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def is_loopback_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urlopen_direct_for_loopback(
    url: str | urllib.request.Request,
    data: bytes | None = None,
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
) -> object:
    target = getattr(url, "full_url", str(url))
    opener = _LOOPBACK_OPENER.open if is_loopback_base_url(str(target)) else urllib.request.urlopen
    if data is None:
        return opener(url, timeout=timeout)
    return opener(url, data=data, timeout=timeout)


def read_url_cancellable(
    opener: Callable[..., Any],
    request: str | urllib.request.Request,
    *,
    timeout: float,
    cancel_checker: Callable[[], None] | None = None,
) -> tuple[bytes, int | None]:
    if cancel_checker is None:
        with opener(request, timeout=timeout) as response:
            return response.read(), getattr(response, "status", None)
    done = threading.Event()
    abort = threading.Event()
    state: dict[str, Any] = {}
    lock = threading.Lock()

    def read() -> None:
        chunks: list[bytes] = []
        try:
            with opener(request, timeout=timeout) as response:
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

    threading.Thread(target=read, name="genie-http-read", daemon=True).start()
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
    budget = max(0.0, float(timeout))
    deadline = time.monotonic() + budget
    if os.name == "nt":
        descendants = _windows_descendant_pids(process.pid)
        for pid in reversed(descendants):
            _terminate_windows_pid(pid)
        _terminate_windows_pid(process.pid)
        _wait_until_gone(descendants, deadline)
    else:
        descendants = _posix_descendant_pids(process.pid)
        for pid in (*reversed(descendants), process.pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name == "nt" and process.poll() is None:
        process.kill()
    elif os.name != "nt":
        for pid in (*reversed(descendants), process.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        pass


def _windows_descendant_pids(root_pid: int) -> list[int]:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid = ctypes.c_void_p(-1).value
    if snapshot in (None, invalid):
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            return []
        while True:
            children.setdefault(
                int(entry.th32ParentProcessID),
                [],
            ).append(int(entry.th32ProcessID))
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)

    descendants: list[int] = []
    pending = list(children.get(int(root_pid), ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _terminate_windows_pid(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        terminate_process(handle, 1)
    finally:
        close_handle(handle)


def _wait_until_gone(pids: list[int], deadline: float) -> None:
    while time.monotonic() < deadline:
        if not any(_windows_process_exists(pid) for pid in pids):
            return
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))


def _windows_process_exists(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(0x00100000, False, int(pid))  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
    try:
        return wait_for_single_object(handle, 0) == 0x00000102  # WAIT_TIMEOUT
    finally:
        close_handle(handle)


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


def _subprocess_path(value: str | Path) -> str:
    text = str(value)
    if sys.platform == "win32":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return os.path.normpath(text)


def _local_tts_subprocess_env(python_exe: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if python_exe is not None:
        env["PATH"] = f"{_subprocess_path(python_exe.parent)}{os.pathsep}{env.get('PATH', '')}"
    return env


def _build_genie_start_command(python_exe: Path, host: str, port: int) -> list[str]:
    code = (
        "import os, sys\n"
        "base_dir = os.getcwd()\n"
        "os.environ['GENIE_DATA_DIR'] = os.path.join(base_dir, 'GenieData')\n"
        "sys.path.insert(0, os.path.join(base_dir, 'runtime'))\n"
        "import genie_tts\n"
        f"genie_tts.start_server(host={host!r}, port={int(port)}, workers=1)\n"
    )
    return [_subprocess_path(python_exe), "-c", code]


def _build_genie_endpoint_url(base_url: str, endpoint: str) -> str:
    parsed = urlparse(base_url)
    parts = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []
    if parts and parts[-1] == "tts":
        parts[-1] = endpoint
    elif not parts or parts[-1] != endpoint:
        parts.append(endpoint)
    return urlunparse(parsed._replace(path="/" + "/".join(parts), query=""))


def _encode_genie_character_name(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")


def _probe_tcp_port(host: str, port: int, timeout: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _probe_genie_api_url(api_url: str, timeout: int) -> bool:
    request = urllib.request.Request(
        _build_genie_endpoint_url(api_url, "openapi.json"),
        method="GET",
    )
    try:
        with urlopen_direct_for_loopback(request, timeout=timeout) as response:
            import json

            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    paths = payload.get("paths") if isinstance(payload, Mapping) else None
    return isinstance(paths, Mapping) and any(
        str(path).rstrip("/").endswith("/load_character") for path in paths
    ) and any(str(path).rstrip("/").endswith("/tts") for path in paths)


def _resolve_genie_converter_script(work_dir: Path) -> Path | None:
    root = work_dir.resolve()
    if root.suffix.lower() == ".py":
        return root if root.is_file() else None
    for name in ("convert.py", "convery.py"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


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
    return _subprocess_path(value) if str(value) else ""


def verify_generated_audio(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return "audio_file_invalid"
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() not in (1, 2) or handle.getsampwidth() <= 0 or handle.getframerate() <= 0:
                return "audio_format_invalid"
    except (OSError, EOFError, wave.Error):
        return "audio_format_invalid"
    return None


def _write_genie_audio(audio_data: bytes, output_path: Path) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if audio_data[:4] == b"RIFF":
        output_path.write_bytes(audio_data)
        return verify_generated_audio(output_path) is None
    pcm_bytes = b""
    if len(audio_data) % 4 == 0:
        try:
            floats = array.array("f")
            floats.frombytes(audio_data)
            if floats and max(abs(value) for value in floats if math.isfinite(value)) <= 2.0:
                pcm = array.array("h")
                for value in floats:
                    pcm.append(int(max(-1.0, min(1.0, value if math.isfinite(value) else 0.0)) * 32767))
                pcm_bytes = pcm.tobytes()
        except (OverflowError, ValueError):
            pcm_bytes = b""
    if not pcm_bytes and len(audio_data) % 2 == 0:
        pcm_bytes = audio_data
    if not pcm_bytes:
        return False
    try:
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(32000)
            handle.writeframes(pcm_bytes)
    except (OSError, wave.Error):
        return False
    return verify_generated_audio(output_path) is None


try:
    from ._bundle import (
        GENIE_TTS,
        TTSBundleResource,
        installed_bundle_work_dir,
        is_bundle_supported,
    )
except ImportError:
    from _bundle import (  # type: ignore[no-redef]
        GENIE_TTS,
        TTSBundleResource,
        installed_bundle_work_dir,
        is_bundle_supported,
    )
