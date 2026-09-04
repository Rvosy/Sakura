"""Stdlib-only helpers owned by the Genie plugin process."""

from __future__ import annotations

import array
import base64
import ipaddress
import math
import os
import socket
import subprocess
import sys
import threading
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
    # Core also imports this module to validate legacy configuration. Only the
    # running plugin needs the public SDK helper on its isolated import path.
    from sakura_process import terminate_process_tree as terminate_owned_tree

    terminate_owned_tree(process, timeout=timeout)


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
