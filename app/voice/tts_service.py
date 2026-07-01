"""TTS 本地服务监督（issue #94 第 3 阶段）。

从 ``app/voice/tts.py`` 抽出「服务进程监督」这一职责：本地 GPT-SoVITS / Genie
子进程的探测、启动、接管、Broken pipe 重启、角色权重/模型加载，以及与之配套的
一组 module-level helper。

子进程经 :class:`app.core.resource_manager.ProcessResource` 托管，关闭走协调器
自持 ``ResourceManager`` 的 ``stop_all``。``TTSServiceSupervisor`` 与
``GenieServiceSupervisor`` 仍把状态挂在 ``self`` 上（``_server_process`` /
``_service_checked`` / ``_weights_ready`` / ``_service_state`` 等），使现有
「未绑定方法 + SimpleNamespace 鸭子桩」测试只需把类名换成 supervisor、桩字段不变。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode, urlparse, urlunparse

from app.core.debug_log import debug_log
from app.core.gui_log import record_tts_service_output
from app.core.http_client import urlopen_direct_for_loopback
from app.llm.chat_reply import DEFAULT_TONE
from app.storage.paths import StoragePaths
from app.voice.runtime_compat import find_usable_runtime_python, format_runtime_python_issue
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL as _DEFAULT_GENIE_TTS_API_URL,
    GPTSoVITSTTSSettings as _GPTSoVITSTTSSettings,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS as _TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE as _TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS as _TTS_PROVIDER_GPT_SOVITS,
    TTSConfigError as _TTSConfigError,
    ToneReference as _ToneReference,
    _normalize_tts_provider as _normalize_tts_provider_setting,
)
from app.voice.tts_types import (
    TTSServiceState,
    _parse_service_endpoint,
    _provider_is_closed,
    _set_service_state,
)

_LOCAL_SERVICE_STARTUP_TIMEOUT_MAX = 180


def _service_project_root(base_dir: Path | None = None) -> Path:
    """解析项目根目录；与 tts.py 的 _resolve_project_root 同义（本文件位于 app/voice/）。"""
    return Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[2]


class _LocalProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None:
        """返回本地 TTS 进程是否仍在运行。"""

    def terminate(self) -> None:
        """终止本地 TTS 进程。"""

    def kill(self) -> None:
        """强制终止本地 TTS 进程。"""

    def wait(self, timeout: int | float | None = None) -> int | None:
        """等待本地 TTS 进程退出。"""


class _AttachedLocalProcess:
    """把启动前已存在的本地 TTS 进程纳入关闭流程。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None if _process_exists(self.pid) else 0

    def terminate(self) -> None:
        _terminate_pid_tree(self.pid, timeout=5)

    def kill(self) -> None:
        _terminate_pid_tree(self.pid, timeout=5)

    def wait(self, timeout: int | float | None = None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(["pid", str(self.pid)], timeout)
            time.sleep(0.1)
        return 0


def _wait_local_service_ready(
    *,
    provider: object,
    service_name: str,
    ready_check: Callable[[], bool],
    fail_callback: Callable[[str], None],
    timeout_seconds: int,
) -> bool:
    """启动本地服务后的统一就绪轮询：进程存活检查 + ready_check，直到超时。

    大模型首次加载可能超过 30 秒，按用户配置等待（封顶 _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX），
    避免刚加载完成就被判超时。
    """
    settings = getattr(provider, "settings")
    base_dir = getattr(provider, "_base_dir", None)
    _set_service_state(provider, TTSServiceState.WAITING_READY)
    deadline = time.monotonic() + max(3, min(timeout_seconds, _LOCAL_SERVICE_STARTUP_TIMEOUT_MAX))
    while time.monotonic() < deadline:
        if _provider_is_closed(provider):
            _set_service_state(provider, TTSServiceState.FAILED, {"reason": "provider_closed"})
            return False
        process = getattr(provider, "_server_process", None)
        exit_code = process.poll() if process is not None else None
        if exit_code is not None:
            log_path = _local_tts_service_log_path(settings.provider, base_dir)
            _set_service_state(provider, TTSServiceState.FAILED, {"reason": "process_exited", "exit_code": exit_code})
            fail_callback(
                f"{service_name} 本地服务进程已退出，退出码：{exit_code}。"
                f"请查看启动日志：{log_path}"
            )
            return False
        if ready_check():
            return True
        time.sleep(0.5)
    log_path = _local_tts_service_log_path(settings.provider, base_dir)
    _set_service_state(provider, TTSServiceState.FAILED, {"reason": "startup_timeout"})
    fail_callback(
        f"{service_name} 已尝试启动，但端口仍不可用：{settings.api_url}。"
        f"请查看启动日志：{log_path}"
    )
    return False


def _find_running_local_tts_process(
    settings: _GPTSoVITSTTSSettings,
    port: int,
) -> _AttachedLocalProcess | None:
    if settings.work_dir is None:
        return None
    if settings.provider not in {
        _TTS_PROVIDER_GPT_SOVITS,
        _TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        _TTS_PROVIDER_GENIE,
    }:
        return None

    pid = _find_listening_tcp_pid(port)
    if pid is None or pid == os.getpid():
        return None

    command_line = _query_process_command_line(pid)
    if not command_line or not _command_line_matches_local_tts(settings, command_line, port):
        return None
    return _AttachedLocalProcess(pid)


def _find_listening_tcp_pid(port: int) -> int | None:
    if sys.platform != "win32":
        return _find_listening_tcp_pid_lsof(port)

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            **_windows_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地监听端口失败", {"port": port, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        state = parts[-2].upper()
        if state != "LISTENING" or _netstat_address_port(parts[1]) != port:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            return None
    return None


def _find_listening_tcp_pid_lsof(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地监听端口失败", {"port": port, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.startswith("p"):
            continue
        try:
            return int(line[1:])
        except ValueError:
            return None
    return None


def _netstat_address_port(address: str) -> int | None:
    if address.startswith("["):
        _host, separator, port_text = address.rpartition("]:")
    else:
        _host, separator, port_text = address.rpartition(":")
    if not separator:
        return None
    try:
        return int(port_text)
    except ValueError:
        return None


def _query_process_command_line(pid: int) -> str | None:
    if sys.platform == "win32":
        return _query_windows_process_command_line(pid)
    return _query_posix_process_command_line(pid)


def _query_windows_process_command_line(pid: int) -> str | None:
    script = f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            **_windows_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地 TTS 进程命令行失败", {"pid": pid, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _query_posix_process_command_line(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug_log("TTS", "查询本地 TTS 进程命令行失败", {"pid": pid, "error": str(exc)})
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _command_line_matches_local_tts(
    settings: _GPTSoVITSTTSSettings,
    command_line: str,
    port: int,
) -> bool:
    work_dir = settings.work_dir
    if work_dir is None:
        return False

    normalized_command = _normalize_process_text(command_line)
    configured_python = settings.python_path.resolve() if settings.python_path is not None else None
    python_exe = _normalize_process_text(str(configured_python or work_dir.resolve() / "runtime" / "python.exe"))
    if python_exe not in normalized_command:
        return False

    if settings.provider == _TTS_PROVIDER_GENIE:
        return "genie_tts.start_server" in normalized_command and f"port={int(port)}" in normalized_command

    if settings.provider in {_TTS_PROVIDER_GPT_SOVITS, _TTS_PROVIDER_CUSTOM_GPT_SOVITS}:
        api_script = _normalize_process_text(str(work_dir.resolve() / "api_v2.py"))
        return api_script in normalized_command

    return False


def _normalize_process_text(value: str) -> str:
    return value.replace("/", "\\").casefold()


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=3,
                **_windows_no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and str(int(pid)) in result.stdout

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid_tree(pid: int, timeout: int) -> None:
    if sys.platform == "win32":
        _run_windows_taskkill(pid, timeout)
        return
    os.kill(pid, 15)


def _run_windows_taskkill(pid: int, timeout: int) -> None:
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "check": False,
        "timeout": timeout,
    }
    kwargs.update(_windows_no_window_kwargs())
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], **kwargs)


def _windows_no_window_kwargs() -> dict[str, object]:
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")}
    return {}


def _terminate_process_tree(process: _LocalProcessHandle, timeout: int) -> None:
    pid = getattr(process, "pid", None)
    if sys.platform == "win32" and pid is not None:
        try:
            _run_windows_taskkill(pid, timeout)
            process.wait(timeout=timeout)
            if process.poll() is not None:
                return
        except (OSError, subprocess.TimeoutExpired) as exc:
            debug_log("TTS", "taskkill 清理本地 TTS 进程树失败，改用 Popen 关闭", {"pid": pid, "error": str(exc)})

    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _build_genie_start_command(python_exe: Path, host: str, port: int) -> list[str]:
    start_host = host.strip() or "127.0.0.1"
    start_code = (
        "import os, sys\n"
        "base_dir = os.getcwd()\n"
        "os.environ['GENIE_DATA_DIR'] = os.path.join(base_dir, 'GenieData')\n"
        "sys.path.insert(0, os.path.join(base_dir, 'runtime'))\n"
        "import genie_tts\n"
        f"genie_tts.start_server(host={start_host!r}, port={int(port)}, workers=1)\n"
    )
    return [str(python_exe), "-c", start_code]


def _build_gpt_sovits_start_command(
    python_exe: Path,
    api_script: Path,
    settings: _GPTSoVITSTTSSettings,
) -> list[str]:
    cmd = [str(python_exe), str(api_script)]
    if settings.tts_config_path is not None:
        cmd.extend(["-c", str(settings.tts_config_path)])

    parsed_url = urlparse(settings.api_url)
    if parsed_url.hostname:
        host = "127.0.0.1" if parsed_url.hostname == "localhost" else parsed_url.hostname
        cmd.extend(["-a", host])
    try:
        port = parsed_url.port
    except ValueError:
        port = None
    if port is not None:
        cmd.extend(["-p", str(port)])
    return cmd


def _local_tts_subprocess_env(python_exe: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if python_exe is not None:
        bin_dir = str(python_exe.parent)
        path = env.get("PATH", "")
        if path:
            env["PATH"] = f"{bin_dir}{os.pathsep}{path}"
        else:
            env["PATH"] = bin_dir
    return env


def _format_gpt_sovits_http_error(status_code: int, error_body: str) -> str:
    if status_code == 400 and _looks_like_charmap_encode_error(error_body):
        return (
            "GPT-SoVITS HTTP 400: 本地 GPT-SoVITS 运行时编码不是 UTF-8，"
            "中文或日文文本写入时触发 charmap 编码错误。"
            "Sakura 启动本地服务时已启用 UTF-8 标准输入输出；如果仍然失败，"
            "请关闭当前 GPT-SoVITS 服务后由 Sakura 重新启动，或手动检查运行时编码。"
            f"\n原始响应：{error_body}"
        )
    return f"GPT-SoVITS HTTP {status_code}: {error_body}"


def _looks_like_charmap_encode_error(error_body: str) -> bool:
    normalized = error_body.lower()
    return "charmap" in normalized and "can't encode" in normalized


def _is_restartable_local_tts_service_failure(status_code: int, error_body: str) -> bool:
    """本地 TTS 服务自身进入坏状态，重启服务比跳过单段更正确。"""
    if status_code != 400:
        return False
    normalized = error_body.lower()
    return "tts failed" in normalized and (
        "broken pipe" in normalized or "[errno 32]" in normalized
    )


def _is_soft_synth_failure(status_code: int, error_body: str) -> bool:
    """判断是否为可静默降级的单段合成失败，区别于需提示用户的服务/配置故障。

    GPT-SoVITS api_v2 在推理异常时统一返回 400 + {"message":"tts failed",...}，
    多由个别文本段触发（如归一化后为空、含服务端不支持的内容），属偶发且无害，
    文本已照常显示，按单段静默跳过即可。charmap 编码错误是运行时配置问题，
    会持续影响所有中日文合成，仍需保留提示，故在此排除。
    """
    if status_code != 400:
        return False
    if _looks_like_charmap_encode_error(error_body):
        return False
    if _is_restartable_local_tts_service_failure(status_code, error_body):
        return False
    return "tts failed" in error_body.lower()


def _probe_tcp_port(host: str, port: int, timeout: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (TimeoutError, OSError):
        return False
    return True


def _probe_gpt_sovits_http(api_url: str, timeout: int) -> bool:
    """探测 GPT-SoVITS HTTP 层是否就绪（TCP 通后 HTTP 可能仍在初始化）。"""
    parsed = urlparse(api_url)
    base_path = parsed.path.rsplit("/", 1)[0]
    probe_url = urlunparse(parsed._replace(path=base_path or "/", query=""))
    request = urllib.request.Request(url=probe_url, method="GET")
    try:
        with urlopen_direct_for_loopback(request, timeout=timeout):
            pass
    except urllib.error.HTTPError:
        # 任何 HTTP 状态码都说明服务 HTTP 层已就绪
        pass
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True


def _probe_genie_api_url(api_url: str, timeout: int) -> bool:
    request = urllib.request.Request(
        url=_build_genie_endpoint_url(api_url, "openapi.json"),
        method="GET",
    )
    try:
        with urlopen_direct_for_loopback(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        debug_log("TTS", "Genie API 端点探测失败", {"api_url": api_url, "error": str(exc)})
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        debug_log("TTS", "Genie API 端点探测返回非 JSON", {"api_url": api_url})
        return False
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return False
    has_load_character = any(str(path).rstrip("/").endswith("/load_character") for path in paths)
    has_tts = any(str(path).rstrip("/").endswith("/tts") for path in paths)
    return has_load_character and has_tts


def _replace_url_port(api_url: str, port: int) -> str:
    parsed_url = urlparse(api_url)
    host = parsed_url.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host_text = f"[{host}]"
    else:
        host_text = host
    auth = ""
    if parsed_url.username:
        auth = parsed_url.username
        if parsed_url.password:
            auth += f":{parsed_url.password}"
        auth += "@"
    netloc = f"{auth}{host_text}:{int(port)}"
    return urlunparse(parsed_url._replace(netloc=netloc))


def _is_loopback_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _can_bind_local_port(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host.strip().lower() == "localhost" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind((bind_host, port))
    except OSError:
        return False
    return True


def _tts_service_display_name(provider: str) -> str:
    normalized = _normalize_tts_provider_setting(provider)
    if normalized == _TTS_PROVIDER_GENIE:
        return "Genie TTS"
    return "GPT-SoVITS"


def _probe_failure_message(service_name: str, purpose: str, *, timeout: bool) -> str:
    if purpose == "startup_wait":
        return f"本地 {service_name} 服务尚未就绪，继续等待"
    if purpose == "pre_start_check":
        return f"{service_name} 服务当前未响应，准备尝试启动本地服务"
    return "服务探测超时" if timeout else "服务不可用"


def _local_tts_service_log_path(provider: str, base_dir: Path | None = None) -> Path:
    """返回本地 TTS 子进程启动日志路径。

    旧实现基于 Path.cwd()，工作目录与安装目录不一致时日志会写错位置；
    现统一走 StoragePaths（base_dir 缺省时按 __file__ 推算根）。
    """

    return StoragePaths(_service_project_root(base_dir)).tts_service_log(provider)


def _start_local_tts_output_reader(
    process: subprocess.Popen[str],
    log_path: Path,
    provider: str,
) -> None:
    stream = getattr(process, "stdout", None)
    if stream is None:
        return
    thread = threading.Thread(
        target=_read_local_tts_output,
        args=(stream, log_path, provider),
        daemon=True,
    )
    thread.start()


def _iter_tts_service_segments(stream):  # type: ignore[no-untyped-def]
    """逐段产出服务输出。

    tqdm 进度条用 \r 原地刷新且长时间不输出 \n，按行读取要等进度条整条结束
    才能一次性收到，无法实时展示推理进度，因此优先按字符读取并以 \r/\n 切段；
    不支持 read() 的流（如测试桩）退回按行迭代。
    """
    if hasattr(stream, "read"):
        buffer = ""
        while True:
            chunk = stream.read(1)
            if not chunk:
                break
            if chunk in ("\r", "\n"):
                if buffer:
                    yield buffer
                buffer = ""
                continue
            buffer += chunk
        if buffer:
            yield buffer
        return
    for raw_line in stream:
        yield str(raw_line)


def _read_local_tts_output(stream, log_path: Path, provider: str) -> None:  # type: ignore[no-untyped-def]
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            for segment in _iter_tts_service_segments(stream):
                line = segment.rstrip("\r\n")
                if not line.strip():
                    continue
                log_file.write(f"{line}\n")
                log_file.flush()
                record_tts_service_output(provider, line)
    except Exception as exc:  # noqa: BLE001
        debug_log("TTS", "本地 TTS 服务输出读取失败", {"provider": provider, "error": str(exc)})
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _build_tts_endpoint_url(base_url: str, endpoint: str, query: dict[str, str]) -> str:
    parsed_url = urlparse(base_url)
    base_path = parsed_url.path.rsplit("/", 1)[0]
    endpoint_path = f"{base_path}/{endpoint}" if base_path else f"/{endpoint}"
    return urlunparse(
        parsed_url._replace(
            path=endpoint_path,
            query=urlencode(query),
        )
    )


def _build_genie_endpoint_url(base_url: str, endpoint: str) -> str:
    parsed_url = urlparse(base_url)
    path = parsed_url.path.strip("/")
    if not path:
        endpoint_path = f"/{endpoint}"
    else:
        parts = path.split("/")
        if parts[-1] == "tts":
            parts[-1] = endpoint
        elif parts[-1] != endpoint:
            parts.append(endpoint)
        endpoint_path = "/" + "/".join(parts)
    return urlunparse(parsed_url._replace(path=endpoint_path, query=""))


def _encode_genie_character_name(name: str) -> str:
    if not name:
        return ""
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")


def _has_onnx_files(path: Path) -> bool:
    return path.is_dir() and any(child.suffix.lower() == ".onnx" for child in path.glob("*.onnx"))


def _resolve_genie_converter_script(work_dir: Path) -> Path | None:
    base_path = work_dir.resolve()
    if base_path.suffix.lower() == ".py":
        return base_path if base_path.exists() else None
    for name in ("convert.py", "convery.py"):
        candidate = base_path / name
        if candidate.is_file():
            return candidate
    return None


def _never_closed() -> bool:
    return False


def _track_local_process(
    supervisor: "TTSServiceSupervisor",
    process: _LocalProcessHandle | None,
) -> None:
    """同步 ``supervisor._server_process`` 并把句柄登记进 RM 的 ProcessResource。

    SimpleNamespace 鸭子桩没有 ``_resource_manager``，只更新 ``_server_process`` 属性；
    真实 supervisor 则把当前进程交给 :class:`ResourceManager` 托管，以便 ``stop_all``
    在关闭时统一终止。
    """
    supervisor._server_process = process
    manager = getattr(supervisor, "_resource_manager", None)
    if manager is None:
        return
    resource = getattr(supervisor, "_process_resource", None)
    if resource is None:
        if process is None:
            return
        supervisor._process_resource = manager.adopt_process(
            process,
            terminator=_terminate_process_tree,
            terminate_timeout_s=5,
            label=f"tts_service:{getattr(supervisor.settings, 'provider', '')}",
        )
    else:
        resource.attach(process)


class TTSServiceSupervisor:
    """监督本地 GPT-SoVITS 服务进程：探测 / 启动 / 接管 / 重启 / 权重加载。

    状态挂在 ``self`` 上（``_server_process`` / ``_service_checked`` /
    ``_weights_ready`` / ``_service_state``），子进程经 ``ResourceManager`` 的
    ``ProcessResource`` 托管。``is_closed`` 回调由协调器注入，让本地服务探测/启动
    能在 provider 关闭时及时中止。
    """

    def __init__(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        resource_manager: object | None = None,
        is_closed: Callable[[], bool] | None = None,
        adopt_existing_service: bool = True,
    ) -> None:
        self.settings = settings
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._resource_manager = resource_manager
        # _provider_is_closed(self) 会调用 self._is_closed()，由协调器注入其关闭状态。
        self._is_closed = is_closed if is_closed is not None else _never_closed
        self._service_checked = False
        # 服务生命周期显式状态（_service_checked 是其 READY 的向后兼容投影）
        self._service_state = TTSServiceState.IDLE
        self._weights_ready = False
        self._server_process: _LocalProcessHandle | None = None
        self._process_resource = None
        # 串行化「启动/采用」与「停止」,避免预热/请求线程探测启动服务时
        # 主线程 close() 并发终止子进程引发原生闪退。可重入：_start_local_service
        # 在持锁状态下会回调 _stop_local_service。
        self._service_lifecycle_lock = threading.RLock()
        if adopt_existing_service:
            self._adopt_existing_configured_service()

    @property
    def service_ready(self) -> bool:
        """服务探测是否已成功（实际可达）。

        供接话音频预生成等调用方做就绪门控：supervisor 存在不代表服务已启动，
        未就绪时发起 prepare 只会得到静默失败。
        """
        return self._service_checked

    def ensure_ready(self) -> tuple[bool, str]:
        """启动并检测 GPT-SoVITS 服务，同时预加载角色权重。"""

        try:
            self.settings.validate()
        except _TTSConfigError as exc:
            return False, str(exc)

        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 服务不可用。"
        if not self._ensure_character_weights(messages.append):
            return False, messages[-1] if messages else "GPT-SoVITS 角色权重加载失败。"
        return True, "TTS 服务已就绪。"

    def _ensure_service_available(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if _provider_is_closed(self):
            debug_log("TTS", "Provider 已关闭，跳过服务探测", {"api_url": self.settings.api_url})
            return False
        if self._service_checked:
            debug_log("TTS", "服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if endpoint is None:
            debug_log("TTS", "服务地址无效", {"api_url": self.settings.api_url})
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "invalid_api_url"})
            fail_callback(f"GPT-SoVITS 服务地址无效：{self.settings.api_url}")
            return False
        host, port = endpoint

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        _set_service_state(self, TTSServiceState.PROBING)
        if TTSServiceSupervisor._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            TTSServiceSupervisor._adopt_existing_local_service(self, host, port)
            self._service_checked = True
            _set_service_state(self, TTSServiceState.READY, {"via": "probe"})
            debug_log("TTS", "服务探测成功", {"api_url": self.settings.api_url})
            return True

        if self.settings.work_dir is None:
            # 没有可启动的本地整合包：探测失败即不可用（远端/手动服务场景）
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "service_unreachable"})
            fail_callback(f"GPT-SoVITS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        _set_service_state(self, TTSServiceState.STARTING)
        if _provider_is_closed(self):
            return False
        if not TTSServiceSupervisor._start_local_service(self, fail_callback):
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "start_failed"})
            return False

        def _ready() -> bool:
            # 端口通但 HTTP 层尚未就绪（模型仍在加载）时继续等待
            return TTSServiceSupervisor._probe_service_port(
                self, host, port, timeout, purpose="startup_wait"
            ) and _probe_gpt_sovits_http(self.settings.api_url, timeout)

        if not _wait_local_service_ready(
            provider=self,
            service_name="GPT-SoVITS",
            ready_check=_ready,
            fail_callback=fail_callback,
            timeout_seconds=self.settings.timeout_seconds,
        ):
            return False
        self._service_checked = True
        _set_service_state(self, TTSServiceState.READY, {"via": "local_start"})
        debug_log(
            "TTS",
            "本地 GPT-SoVITS 服务启动并探测成功",
            {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
        )
        return True

    def _restart_local_service_after_http_failure(
        self,
        status_code: int,
        error_body: str,
    ) -> bool:
        """HTTP 层显示本地服务管道已坏时，重启 Sakura 管理的本地服务。

        GPT-SoVITS 在进程 stdout/stderr 管道断开时可能把任意有效文本都包装成
        400 + tts failed + Broken pipe。它不是单段文本问题；继续复用该端口只会
        让后续回复全部无声。只对带 work_dir 的本地整合包启用，远端/手动服务不
        擅自终止。
        """
        if not _is_restartable_local_tts_service_failure(status_code, error_body):
            return False
        if self.settings.work_dir is None:
            debug_log(
                "TTS",
                "GPT-SoVITS 服务疑似管道断开，但非本地整合包，不自动重启",
                {"status": status_code, "error_body": error_body},
            )
            return False
        if _provider_is_closed(self):
            return False

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if self._server_process is None and endpoint is not None:
            host, port = endpoint
            TTSServiceSupervisor._adopt_existing_local_service(self, host, port)
        if self._server_process is None:
            debug_log(
                "TTS",
                "GPT-SoVITS 服务疑似管道断开，但未能定位本地服务进程",
                {"status": status_code, "api_url": self.settings.api_url},
            )
            return False

        debug_log(
            "TTS",
            "GPT-SoVITS 服务疑似管道断开，重启本地服务后重试",
            {
                "status": status_code,
                "pid": self._server_process.pid,
                "api_url": self.settings.api_url,
            },
        )
        self._service_checked = False
        self._weights_ready = False
        _set_service_state(self, TTSServiceState.STARTING, {"reason": "restart_after_broken_pipe"})
        TTSServiceSupervisor._stop_local_service(self)
        return True

    def _adopt_existing_local_service(self, host: str, port: int) -> None:
        with self._service_lifecycle_lock:
            current = getattr(self, "_server_process", None)
            if current is not None and current.poll() is None:
                return
            process = _find_running_local_tts_process(self.settings, port)
            if process is None:
                return
            _track_local_process(self, process)
        debug_log(
            "TTS",
            "接管已有本地 TTS 服务进程，退出时将一并清理",
            {
                "pid": process.pid,
                "provider": self.settings.provider,
                "host": host,
                "port": port,
                "work_dir": str(self.settings.work_dir) if self.settings.work_dir is not None else "",
            },
        )

    def _adopt_existing_configured_service(self) -> None:
        parsed_url = urlparse(self.settings.api_url)
        host = parsed_url.hostname or "127.0.0.1"
        try:
            port = parsed_url.port
        except ValueError:
            return
        if port is None:
            return
        self._adopt_existing_local_service(host, port)

    def _probe_service_port(self, host: str, port: int, timeout: int, *, purpose: str = "availability_check") -> bool:
        service_name = _tts_service_display_name(self.settings.provider)
        payload = {
            "api_url": self.settings.api_url,
            "host": host,
            "port": port,
            "purpose": purpose,
        }
        try:
            debug_log(
                "TTS",
                f"探测 {service_name} 端口",
                payload,
            )
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except TimeoutError:
            debug_log("TTS", _probe_failure_message(service_name, purpose, timeout=True), payload)
            return False
        except OSError as exc:
            debug_log(
                "TTS",
                _probe_failure_message(service_name, purpose, timeout=False),
                {**payload, "reason": str(exc)},
            )
            return False
        return True

    def _start_local_service(self, fail_callback: Callable[[str], None]) -> bool:
        if _provider_is_closed(self):
            return False
        work_dir = self.settings.work_dir
        if work_dir is None:
            return False
        work_dir = work_dir.resolve()
        runtime_dir = work_dir / "runtime"
        python_exe = self.settings.python_path
        if python_exe is not None:
            python_exe = python_exe.resolve()
        else:
            python_exe = find_usable_runtime_python(runtime_dir)
        api_script = work_dir / "api_v2.py"
        if not work_dir.is_dir():
            fail_callback(f"GPT-SoVITS 工作目录不存在：{work_dir}")
            return False
        if python_exe is None:
            fail_callback(f"GPT-SoVITS 运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False
        if not python_exe.is_file():
            fail_callback(f"GPT-SoVITS Python 不存在：{python_exe}")
            return False
        if not api_script.is_file():
            fail_callback(f"GPT-SoVITS 启动脚本不存在：{api_script}")
            return False

        # 持服务生命周期锁完成「检查是否已启动 → Popen → 登记句柄」整段,避免与
        # close()/_stop_local_service 并发拆解子进程。_stop_local_service 用同一把可重入锁。
        with self._service_lifecycle_lock:
            if self._server_process is not None and self._server_process.poll() is None:
                debug_log("TTS", "本地 GPT-SoVITS 进程已启动，跳过重复启动", {"work_dir": str(work_dir)})
                return True

        try:
            log_path = _local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, object] = {
                "cwd": str(work_dir),
                "env": _local_tts_subprocess_env(python_exe),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 GPT-SoVITS：{work_dir}\n")
                log_file.flush()
            _track_local_process(
                self,
                subprocess.Popen(
                    _build_gpt_sovits_start_command(python_exe, api_script, self.settings),
                    **kwargs,
                ),
            )
            if _provider_is_closed(self):
                TTSServiceSupervisor._stop_local_service(self)
                return False
            _start_local_tts_output_reader(
                self._server_process,
                log_path,
                "GPT-SoVITS",
            )
        except OSError as exc:
            debug_log("TTS", "本地 GPT-SoVITS 服务启动失败", {"work_dir": str(work_dir), "error": str(exc)})
            fail_callback(f"GPT-SoVITS 服务启动失败：{exc}")
            return False

        debug_log(
            "TTS",
            "已启动本地 GPT-SoVITS 服务",
            {
                "work_dir": str(work_dir),
                "pid": self._server_process.pid,
                "log_path": str(_local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))),
            },
        )
        return True

    def _ensure_character_weights(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if self._weights_ready:
            debug_log("TTS", "角色权重已就绪，跳过切换")
            return True

        for endpoint, path in (
            ("set_gpt_weights", self.settings.gpt_model_path),
            ("set_sovits_weights", self.settings.sovits_model_path),
        ):
            if path is None:
                continue
            debug_log("TTS", "准备切换角色权重", {"endpoint": endpoint, "path": path})
            if not self._request_weight_switch(endpoint, path, fail_callback):
                return False

        self._weights_ready = True
        debug_log("TTS", "角色权重切换完成")
        return True

    def _request_weight_switch(
        self,
        endpoint: str,
        weights_path: Path,
        fail_callback: Callable[[str], None],
    ) -> bool:
        url = _build_tts_endpoint_url(
            self.settings.api_url,
            endpoint,
            {"weights_path": str(weights_path)},
        )
        request = urllib.request.Request(url=url, method="GET")
        try:
            debug_log("TTS", "请求切换权重", {"endpoint": endpoint, "weights_path": weights_path})
            with urlopen_direct_for_loopback(request, timeout=self.settings.timeout_seconds) as response:
                response.read()
                debug_log(
                    "TTS",
                    "权重切换成功",
                    {
                        "endpoint": endpoint,
                        "weights_path": weights_path,
                        "status": getattr(response, "status", None),
                    },
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            debug_log(
                "TTS",
                "权重切换 HTTP 失败",
                {
                    "endpoint": endpoint,
                    "weights_path": weights_path,
                    "status": exc.code,
                    "error_body": error_body,
                },
            )
            fail_callback(
                f"GPT-SoVITS 切换权重失败（{endpoint}, {weights_path}）HTTP {exc.code}: {error_body}"
            )
            return False
        except urllib.error.URLError as exc:
            debug_log(
                "TTS",
                "权重切换请求失败",
                {
                    "endpoint": endpoint,
                    "weights_path": weights_path,
                    "reason": str(exc.reason),
                },
            )
            fail_callback(f"GPT-SoVITS 切换权重失败（{endpoint}, {weights_path}）：{exc.reason}")
            return False
        except TimeoutError:
            debug_log("TTS", "权重切换超时", {"endpoint": endpoint, "weights_path": weights_path})
            fail_callback(f"GPT-SoVITS 切换权重超时（{endpoint}, {weights_path}）。")
            return False
        return True

    def detach_local_service(self) -> None:
        """交出本地服务进程所有权，供新的 Provider 在后台接管（不终止进程）。"""
        with self._service_lifecycle_lock:
            resource = getattr(self, "_process_resource", None)
            if resource is not None:
                resource.detach()
                self._process_resource = None
            self._server_process = None

    def _stop_local_service(self) -> None:
        with self._service_lifecycle_lock:
            process = self._server_process
            if process is None:
                return
            if process.poll() is not None:
                _track_local_process(self, None)
                return
        debug_log("TTS", "关闭本地 TTS 服务进程", {"pid": process.pid, "provider": self.settings.provider})
        try:
            _terminate_process_tree(process, timeout=5)
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "本地 TTS 服务正常关闭失败，尝试强制结束", {"pid": process.pid, "error": str(exc)})
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception as kill_exc:  # noqa: BLE001
                debug_log("TTS", "本地 TTS 服务强制结束失败", {"pid": process.pid, "error": str(kill_exc)})
        finally:
            _track_local_process(self, None)

    def close(self) -> None:
        """关闭监督的本地服务进程（协调器一般改走 RM.stop_all，本入口供直接调用）。"""
        self._stop_local_service()


class GenieServiceSupervisor(TTSServiceSupervisor):
    """Genie TTS 服务监督：在 GPT-SoVITS 监督基础上加 Genie API 探测、备用端口、
    角色模型 / 参考音频 / ONNX 转换等 Genie 专有逻辑。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._loaded_character_name: str | None = None
        self._reference_audio_key: str | None = None
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def ensure_ready(self) -> tuple[bool, str]:
        """启动并检测 Genie TTS 服务，同时预加载角色模型与参考音频。"""

        try:
            self.settings.validate()
        except _TTSConfigError as exc:
            return False, str(exc)

        messages: list[str] = []
        if not self._ensure_service_available(messages.append):
            return False, messages[-1] if messages else "Genie TTS 服务不可用。"
        reference = self._default_reference()
        if not self._ensure_character_model(reference.ref_lang, messages.append):
            return False, messages[-1] if messages else "Genie TTS 角色模型加载失败。"
        if not self._ensure_reference_audio(reference, messages.append):
            return False, messages[-1] if messages else "Genie TTS 参考音频设置失败。"
        return True, "TTS 服务已就绪。"

    def _default_reference(self) -> _ToneReference:
        """解析默认语气的参考音频，供服务预热时加载模型/参考音频使用。

        与合成路径的 _select_reference 对齐：优先取 DEFAULT_TONE 的首条，
        无配置时回退到 settings 上的单条默认参考。"""
        references = self.settings.tone_references.get(DEFAULT_TONE)
        if references:
            return references[0]
        return _ToneReference(
            tone=DEFAULT_TONE,
            ref_audio_path=self.settings.ref_audio_path,
            ref_text=self.settings.ref_text,
            ref_lang=self.settings.ref_lang,
        )

    def _ensure_service_available(
        self,
        fail_callback: Callable[[str], None],
    ) -> bool:
        if _provider_is_closed(self):
            debug_log("TTS", "Provider 已关闭，跳过 Genie 服务探测", {"api_url": self.settings.api_url})
            return False
        if self._service_checked:
            debug_log("TTS", "Genie 服务探测已完成，跳过重复探测", {"api_url": self.settings.api_url})
            return True

        endpoint = _parse_service_endpoint(self.settings.api_url)
        if endpoint is None:
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "invalid_api_url"})
            fail_callback(f"Genie TTS 服务地址无效：{self.settings.api_url}")
            return False
        host, port = endpoint

        timeout = min(self.settings.timeout_seconds, 3)
        probe_purpose = "pre_start_check" if self.settings.work_dir is not None else "availability_check"
        _set_service_state(self, TTSServiceState.PROBING)
        if GenieServiceSupervisor._probe_service_port(self, host, port, timeout, purpose=probe_purpose):
            if GenieServiceSupervisor._probe_genie_api(self, timeout):
                GenieServiceSupervisor._adopt_existing_local_service(self, host, port)
                self._service_checked = True
                _set_service_state(self, TTSServiceState.READY, {"via": "probe"})
                debug_log("TTS", "Genie 服务探测成功", {"api_url": self.settings.api_url})
                return True
            # 端口通但不是 Genie（典型：被 GPT-SoVITS 占用 9880）→ 尝试备用端口
            fallback_port = GenieServiceSupervisor._select_fallback_port(self, host, port, timeout)
            if fallback_port is None:
                _set_service_state(self, TTSServiceState.FAILED, {"reason": "port_conflict"})
                fail_callback(
                    f"端口 {port} 上的服务不是 Genie TTS，且未找到可用的本地备用端口。"
                    f"请将 Genie API URL 改为 {_DEFAULT_GENIE_TTS_API_URL} 或检查占用服务。"
                )
                return False
            old_api_url = self.settings.api_url
            self.settings = replace(self.settings, api_url=_replace_url_port(self.settings.api_url, fallback_port))
            port = fallback_port
            debug_log(
                "TTS",
                "Genie 端口被其他 TTS 服务占用，已切换到备用端口",
                {"old_api_url": old_api_url, "api_url": self.settings.api_url},
            )
            if (
                GenieServiceSupervisor._probe_service_port(self, host, port, timeout, purpose=probe_purpose)
                and GenieServiceSupervisor._probe_genie_api(self, timeout)
            ):
                GenieServiceSupervisor._adopt_existing_local_service(self, host, port)
                self._service_checked = True
                _set_service_state(self, TTSServiceState.READY, {"via": "fallback_port"})
                debug_log("TTS", "Genie 备用端口已有可用服务", {"api_url": self.settings.api_url})
                return True

        if self.settings.work_dir is None:
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "service_unreachable"})
            fail_callback(f"Genie TTS 服务不可用，请先启动或检查地址 {self.settings.api_url}。")
            return False

        _set_service_state(self, TTSServiceState.STARTING)
        if _provider_is_closed(self):
            return False
        if not GenieServiceSupervisor._start_local_service(self, fail_callback, host, port):
            _set_service_state(self, TTSServiceState.FAILED, {"reason": "start_failed"})
            return False

        def _ready() -> bool:
            return GenieServiceSupervisor._probe_service_port(
                self, host, port, timeout, purpose="startup_wait"
            ) and GenieServiceSupervisor._probe_genie_api(self, timeout)

        if not _wait_local_service_ready(
            provider=self,
            service_name="Genie TTS",
            ready_check=_ready,
            fail_callback=fail_callback,
            timeout_seconds=self.settings.timeout_seconds,
        ):
            return False
        self._service_checked = True
        _set_service_state(self, TTSServiceState.READY, {"via": "local_start"})
        debug_log(
            "TTS",
            "本地 Genie TTS 服务启动并探测成功",
            {"api_url": self.settings.api_url, "work_dir": str(self.settings.work_dir)},
        )
        return True

    def _start_local_service(self, fail_callback: Callable[[str], None], host: str, port: int) -> bool:
        if _provider_is_closed(self):
            return False
        work_dir = self.settings.work_dir
        if work_dir is None:
            return False
        work_dir = work_dir.resolve()
        runtime_dir = work_dir / "runtime"
        python_exe = find_usable_runtime_python(runtime_dir)
        if not work_dir.is_dir():
            fail_callback(f"Genie TTS 工作目录不存在：{work_dir}")
            return False
        if python_exe is None:
            fail_callback(f"Genie TTS 运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False

        if self._server_process is not None and self._server_process.poll() is None:
            debug_log("TTS", "本地 Genie TTS 进程已启动，跳过重复启动", {"work_dir": str(work_dir)})
            return True

        try:
            kwargs: dict[str, object] = {
                "cwd": str(work_dir),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
            log_path = _local_tts_service_log_path(self.settings.provider, getattr(self, "_base_dir", None))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 Genie TTS：{work_dir}\n")
                log_file.flush()
            _track_local_process(
                self,
                subprocess.Popen(
                    _build_genie_start_command(python_exe, host, port),
                    **kwargs,
                ),
            )
            if _provider_is_closed(self):
                GenieServiceSupervisor._stop_local_service(self)
                return False
            _start_local_tts_output_reader(
                self._server_process,
                log_path,
                "Genie TTS",
            )
        except OSError as exc:
            fail_callback(f"Genie TTS 服务启动失败：{exc}")
            return False

        debug_log(
            "TTS",
            "已启动本地 Genie TTS 服务",
            {"work_dir": str(work_dir), "pid": self._server_process.pid, "api_url": self.settings.api_url},
        )
        return True

    def _probe_genie_api(self, timeout: int) -> bool:
        return _probe_genie_api_url(self.settings.api_url, timeout)

    def _select_fallback_port(self, host: str, occupied_port: int, timeout: int) -> int | None:
        if self.settings.work_dir is None or not _is_loopback_host(host):
            return None
        for candidate_port in range(max(1, occupied_port + 1), min(65535, occupied_port + 20) + 1):
            candidate_url = _replace_url_port(self.settings.api_url, candidate_port)
            if _probe_tcp_port(host, candidate_port, timeout):
                if _probe_genie_api_url(candidate_url, timeout):
                    return candidate_port
                continue
            if _can_bind_local_port(host, candidate_port):
                return candidate_port
        return None

    def _ensure_character_model(
        self,
        language: str,
        fail_callback: Callable[[str], None],
    ) -> bool:
        character_name = self._genie_character_name()
        if self._loaded_character_name == character_name:
            return True
        if not self._ensure_onnx_model_dir(fail_callback):
            return False
        if self.settings.onnx_model_dir is None:
            fail_callback("Genie TTS 缺少 ONNX 模型目录。")
            return False

        payload = {
            "character_name": _encode_genie_character_name(character_name),
            "onnx_model_dir": str(self.settings.onnx_model_dir),
            "language": language or self.settings.ref_lang or "ja",
        }
        try:
            self._post_json_and_read_bytes("load_character", payload, timeout=20)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            fail_callback(f"Genie TTS 加载角色模型失败 HTTP {exc.code}: {error_body}")
            return False
        except urllib.error.URLError as exc:
            fail_callback(f"Genie TTS 加载角色模型失败：{exc.reason}")
            return False
        except TimeoutError:
            fail_callback("Genie TTS 加载角色模型超时。")
            return False

        self._loaded_character_name = character_name
        return True

    def _ensure_reference_audio(
        self,
        reference: _ToneReference,
        fail_callback: Callable[[str], None],
    ) -> bool:
        character_name = self._genie_character_name()
        key = f"{character_name}|{reference.ref_audio_path}|{reference.ref_text}|{reference.ref_lang}"
        if self._reference_audio_key == key:
            return True
        payload = {
            "character_name": _encode_genie_character_name(character_name),
            "audio_path": str(reference.ref_audio_path),
            "audio_text": reference.ref_text,
            "language": reference.ref_lang,
        }
        try:
            self._post_json_and_read_bytes("set_reference_audio", payload, timeout=20)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            fail_callback(f"Genie TTS 设置参考音频失败 HTTP {exc.code}: {error_body}")
            return False
        except urllib.error.URLError as exc:
            fail_callback(f"Genie TTS 设置参考音频失败：{exc.reason}")
            return False
        except TimeoutError:
            fail_callback("Genie TTS 设置参考音频超时。")
            return False
        self._reference_audio_key = key
        return True

    def _ensure_onnx_model_dir(self, fail_callback: Callable[[str], None]) -> bool:
        onnx_dir = self.settings.onnx_model_dir
        if onnx_dir is not None and _has_onnx_files(onnx_dir):
            return True
        if onnx_dir is None:
            fail_callback("Genie TTS 缺少 ONNX 模型目录。")
            return False
        if self.settings.work_dir is None:
            fail_callback(f"Genie TTS ONNX 模型不存在：{onnx_dir}，且未配置工作目录用于转换。")
            return False
        if self.settings.gpt_model_path is None or self.settings.sovits_model_path is None:
            fail_callback(f"Genie TTS ONNX 模型不存在：{onnx_dir}，且角色缺少 GPT/SoVITS 权重用于转换。")
            return False

        converter_script = _resolve_genie_converter_script(self.settings.work_dir)
        if converter_script is None:
            fail_callback(f"Genie TTS 工作目录缺少 convert.py/convery.py：{self.settings.work_dir}")
            return False
        runtime_dir = converter_script.parent / "runtime"
        python_exe = find_usable_runtime_python(runtime_dir)
        if python_exe is None:
            fail_callback(f"Genie TTS 转换运行时不可用：{format_runtime_python_issue(runtime_dir)}")
            return False

        onnx_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(python_exe),
            str(converter_script),
            "--pth",
            str(self.settings.sovits_model_path),
            "--ckpt",
            str(self.settings.gpt_model_path),
            "--out",
            str(onnx_dir),
        ]
        kwargs: dict[str, object] = {
            "args": cmd,
            "cwd": str(converter_script.parent),
            "capture_output": True,
            "text": True,
            "timeout": max(600, self.settings.timeout_seconds),
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
        try:
            result = subprocess.run(**kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            fail_callback(f"Genie TTS ONNX 转换失败：{exc}")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}")[:2000]
            fail_callback(f"Genie TTS ONNX 转换失败：{detail}")
            return False
        if not _has_onnx_files(onnx_dir):
            fail_callback(f"Genie TTS ONNX 转换完成但未生成 .onnx 文件：{onnx_dir}")
            return False
        return True

    def _post_json_and_read_bytes(self, endpoint: str, payload: dict[str, object], *, timeout: int) -> bytes:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=_build_genie_endpoint_url(self.settings.api_url, endpoint),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen_direct_for_loopback(request, timeout=timeout) as response:
            return response.read()

    def _genie_character_name(self) -> str:
        return self.settings.character_name.strip() or "sakura"
