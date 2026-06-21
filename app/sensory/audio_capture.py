from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.debug_log import debug_log
from app.storage.paths import StoragePaths


class SystemAudioCaptureError(RuntimeError):
    """Raised when system audio cannot be captured safely."""


@dataclass(frozen=True)
class CapturedAudio:
    path: Path
    duration_seconds: float
    sample_rate: int
    channel_count: int
    source: str = "system_audio"

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            debug_log("Sensory", "系统音频临时文件清理失败", {"path": str(self.path), "error": str(exc)})


@dataclass(frozen=True)
class _CompletedCommand:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _WavInfo:
    sample_rate: int
    channel_count: int


class SystemAudioCapture(Protocol):
    def capture(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
    ) -> CapturedAudio:
        """Capture a short system-output audio sample and return a temporary WAV path."""


class ManagedProcessResource(Protocol):
    def stop(self, timeout_ms: int = ...) -> bool:
        """Terminate the adopted process."""

    def detach(self) -> Any:
        """Remove the process from the registry without terminating it."""


class ProcessRegistry(Protocol):
    def adopt_process(
        self,
        process: Any,
        *,
        label: str = ...,
        shutdown_order: int = ...,
        terminate_timeout_s: int = ...,
    ) -> ManagedProcessResource:
        """Register a subprocess handle for app shutdown cleanup."""


def create_system_audio_capture(
    base_dir: Path,
    *,
    resource_registry: ProcessRegistry | None = None,
) -> SystemAudioCapture | None:
    paths = StoragePaths(base_dir)
    if sys.platform == "darwin":
        return MacOSSystemAudioCapture(
            cache_dir=paths.system_audio_cache_dir,
            helper_path=paths.system_audio_capture_helper(),
            resource_registry=resource_registry,
        )
    if sys.platform == "win32":
        return WindowsSystemAudioCapture(
            cache_dir=paths.system_audio_cache_dir,
            resource_registry=resource_registry,
        )
    if sys.platform.startswith("linux"):
        return LinuxSystemAudioCapture(
            cache_dir=paths.system_audio_cache_dir,
            resource_registry=resource_registry,
        )
    return None


class MacOSSystemAudioCapture:
    """ScreenCaptureKit-backed system audio capture."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        helper_path: Path,
        resource_registry: ProcessRegistry | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.helper_path = Path(helper_path)
        self.resource_registry = resource_registry
        self.source_path = Path(__file__).resolve().parents[1] / "platforms" / "macos_system_audio_capture.swift"

    def capture(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
    ) -> CapturedAudio:
        duration = _clamp_float(duration_seconds, 0.5, 10.0)
        sample_rate = _clamp_int(sample_rate, 8000, 48000)
        channel_count = _clamp_int(channel_count, 1, 2)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        helper = self._ensure_helper()
        output_path = _new_temp_wav_path(self.cache_dir)
        command = [
            str(helper),
            "--output",
            str(output_path),
            "--duration",
            f"{duration:.3f}",
            "--sample-rate",
            str(sample_rate),
            "--channels",
            str(channel_count),
        ]
        if exclude_current_process:
            command.append("--exclude-current-process")
        debug_log(
            "Sensory",
            "开始采集系统音频",
            {
                "platform": "macos",
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "channel_count": channel_count,
                "exclude_current_process": exclude_current_process,
            },
        )
        result = _run_managed_command(
            command,
            timeout_seconds=max(15.0, duration + 20.0),
            label="sensory_system_audio_macos",
            resource_registry=self.resource_registry,
        )
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            message = (result.stderr or result.stdout or "system audio capture failed").strip()
            raise SystemAudioCaptureError(message)
        wav_info = _validated_wav_info(output_path)
        debug_log(
            "Sensory",
            "系统音频采集完成",
            {"platform": "macos", "audio_path": str(output_path), "bytes": output_path.stat().st_size},
        )
        return CapturedAudio(
            path=output_path,
            duration_seconds=duration,
            sample_rate=wav_info.sample_rate,
            channel_count=wav_info.channel_count,
        )

    def _ensure_helper(self) -> Path:
        if not self.source_path.exists():
            raise SystemAudioCaptureError(f"系统音频采集 helper 源码不存在：{self.source_path}")
        needs_compile = not self.helper_path.exists()
        if not needs_compile:
            try:
                needs_compile = self.helper_path.stat().st_mtime < self.source_path.stat().st_mtime
            except OSError:
                needs_compile = True
        if not needs_compile:
            return self.helper_path
        swiftc = shutil.which("swiftc")
        if not swiftc:
            raise SystemAudioCaptureError("未找到 swiftc，无法构建 macOS 系统音频采集 helper。")
        self.helper_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            swiftc,
            "-O",
            "-parse-as-library",
            str(self.source_path),
            "-o",
            str(self.helper_path),
            "-framework",
            "ScreenCaptureKit",
            "-framework",
            "AVFoundation",
            "-framework",
            "CoreMedia",
        ]
        result = _run_managed_command(
            command,
            timeout_seconds=90,
            label="sensory_system_audio_macos_compile",
            resource_registry=self.resource_registry,
        )
        if result.returncode != 0:
            raise SystemAudioCaptureError(
                "构建 macOS 系统音频采集 helper 失败："
                + (result.stderr or result.stdout or "unknown compiler error").strip()
            )
        try:
            self.helper_path.chmod(0o755)
        except OSError:
            pass
        return self.helper_path


class WindowsSystemAudioCapture:
    """WASAPI loopback capture through an isolated Python helper process."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        resource_registry: ProcessRegistry | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.resource_registry = resource_registry
        self.helper_path = Path(__file__).resolve().parents[1] / "platforms" / "windows_system_audio_capture.py"

    def capture(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
    ) -> CapturedAudio:
        del exclude_current_process
        if not self.helper_path.exists():
            raise SystemAudioCaptureError(f"Windows 系统音频采集 helper 不存在：{self.helper_path}")
        duration = _clamp_float(duration_seconds, 0.5, 10.0)
        sample_rate = _clamp_int(sample_rate, 8000, 48000)
        channel_count = _clamp_int(channel_count, 1, 2)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        output_path = _new_temp_wav_path(self.cache_dir)
        command = [
            sys.executable,
            str(self.helper_path),
            "--output",
            str(output_path),
            "--duration",
            f"{duration:.3f}",
            "--sample-rate",
            str(sample_rate),
            "--channels",
            str(channel_count),
        ]
        debug_log(
            "Sensory",
            "开始采集系统音频",
            {
                "platform": "windows",
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "channel_count": channel_count,
            },
        )
        result = _run_managed_command(
            command,
            timeout_seconds=max(15.0, duration + 20.0),
            label="sensory_system_audio_windows",
            resource_registry=self.resource_registry,
            **_subprocess_platform_options(),
        )
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            message = (result.stderr or result.stdout or "system audio capture failed").strip()
            raise SystemAudioCaptureError(message)
        wav_info = _validated_wav_info(output_path)
        debug_log(
            "Sensory",
            "系统音频采集完成",
            {"platform": "windows", "audio_path": str(output_path), "bytes": output_path.stat().st_size},
        )
        return CapturedAudio(
            path=output_path,
            duration_seconds=duration,
            sample_rate=wav_info.sample_rate,
            channel_count=wav_info.channel_count,
        )


class LinuxSystemAudioCapture:
    """Best-effort Linux system-output capture using PipeWire or PulseAudio tools."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        resource_registry: ProcessRegistry | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.resource_registry = resource_registry

    def capture(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
    ) -> CapturedAudio:
        del exclude_current_process
        duration = _clamp_float(duration_seconds, 0.5, 10.0)
        sample_rate = _clamp_int(sample_rate, 8000, 48000)
        channel_count = _clamp_int(channel_count, 1, 2)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for label, command in self._candidate_commands(sample_rate=sample_rate, channel_count=channel_count):
            output_path = _new_temp_wav_path(self.cache_dir)
            prepared = [part.format(output=str(output_path)) for part in command]
            debug_log(
                "Sensory",
                "开始采集系统音频",
                {
                    "platform": "linux",
                    "backend": label,
                    "duration_seconds": duration,
                    "sample_rate": sample_rate,
                    "channel_count": channel_count,
                },
            )
            try:
                result = _run_timed_capture_command(
                    prepared,
                    output_path=output_path,
                    duration_seconds=duration,
                    timeout_seconds=max(15.0, duration + 20.0),
                    label=f"sensory_system_audio_linux_{label}",
                    resource_registry=self.resource_registry,
                )
                if result.returncode != 0 and not _is_valid_wav_file(output_path):
                    message = (result.stderr or result.stdout or f"{label} capture failed").strip()
                    raise SystemAudioCaptureError(message)
                wav_info = _validated_wav_info(output_path)
                debug_log(
                    "Sensory",
                    "系统音频采集完成",
                    {
                        "platform": "linux",
                        "backend": label,
                        "audio_path": str(output_path),
                        "bytes": output_path.stat().st_size,
                    },
                )
                return CapturedAudio(
                    path=output_path,
                    duration_seconds=duration,
                    sample_rate=wav_info.sample_rate,
                    channel_count=wav_info.channel_count,
                )
            except SystemAudioCaptureError as exc:
                output_path.unlink(missing_ok=True)
                errors.append(f"{label}: {exc}")
        detail = "; ".join(errors) if errors else "未找到 pw-record 或 PulseAudio monitor 录音工具。"
        raise SystemAudioCaptureError(f"Linux 系统音频采集不可用：{detail}")

    def _candidate_commands(self, *, sample_rate: int, channel_count: int) -> list[tuple[str, list[str]]]:
        commands: list[tuple[str, list[str]]] = []
        pw_record = shutil.which("pw-record")
        if pw_record:
            commands.append(
                (
                    "pipewire",
                    [
                        pw_record,
                        "--rate",
                        str(sample_rate),
                        "--channels",
                        str(channel_count),
                        "-P",
                        "{ stream.capture.sink=true }",
                        "{output}",
                    ],
                )
            )
        parec = shutil.which("parec") or shutil.which("parecord")
        pactl = shutil.which("pactl")
        if parec and pactl:
            monitor = _default_pulse_monitor_source(pactl)
            if monitor:
                commands.append(
                    (
                        "pulseaudio",
                        [
                            parec,
                            "-d",
                            monitor,
                            "--file-format=wav",
                            "--rate",
                            str(sample_rate),
                            "--channels",
                            str(channel_count),
                            "{output}",
                        ],
                    )
                )
        return commands


def _default_pulse_monitor_source(pactl: str) -> str:
    try:
        result = subprocess.run(
            [pactl, "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    sink = (result.stdout or "").strip()
    if result.returncode != 0 or not sink:
        return ""
    return f"{sink}.monitor"


def _new_temp_wav_path(cache_dir: Path) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix="system_audio_",
        suffix=".wav",
        dir=str(cache_dir),
    )
    os.close(fd)
    output_path = Path(raw_path)
    output_path.unlink(missing_ok=True)
    return output_path


def _run_managed_command(
    command: list[str],
    *,
    timeout_seconds: float,
    label: str,
    resource_registry: ProcessRegistry | None = None,
    **popen_kwargs: object,
) -> _CompletedCommand:
    process_resource: ManagedProcessResource | None = None
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - command is built by platform adapters, not user input.
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs,
        )
        if resource_registry is not None:
            process_resource = resource_registry.adopt_process(
                process,
                label=label,
                shutdown_order=850,
                terminate_timeout_s=2,
            )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return _CompletedCommand(process.returncode or 0, stdout or "", stderr or "")
    except subprocess.TimeoutExpired as exc:
        if process_resource is not None:
            process_resource.stop()
        elif process is not None:
            _terminate_process(process)
        raise SystemAudioCaptureError(f"系统音频采集进程超时：{label}") from exc
    except OSError as exc:
        raise SystemAudioCaptureError(str(exc)) from exc
    finally:
        if process_resource is not None:
            process_resource.detach()


def _run_timed_capture_command(
    command: list[str],
    *,
    output_path: Path,
    duration_seconds: float,
    timeout_seconds: float,
    label: str,
    resource_registry: ProcessRegistry | None = None,
) -> _CompletedCommand:
    process_resource: ManagedProcessResource | None = None
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - command is selected from trusted recorder binaries.
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_subprocess_platform_options(),
        )
        if resource_registry is not None:
            process_resource = resource_registry.adopt_process(
                process,
                label=label,
                shutdown_order=850,
                terminate_timeout_s=2,
            )
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        if process.poll() is None:
            _request_recorder_stop(process)
        try:
            stdout, stderr = process.communicate(timeout=max(1.0, timeout_seconds - duration_seconds))
        except subprocess.TimeoutExpired:
            if process_resource is not None:
                process_resource.stop()
            else:
                _terminate_process(process)
            raise SystemAudioCaptureError(f"系统音频采集进程超时：{label}")
        return _CompletedCommand(process.returncode or 0, stdout or "", stderr or "")
    except OSError as exc:
        output_path.unlink(missing_ok=True)
        raise SystemAudioCaptureError(str(exc)) from exc
    finally:
        if process_resource is not None:
            process_resource.detach()


def _request_recorder_stop(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            process.send_signal(signal.SIGINT)
            return
        except OSError:
            pass
    try:
        process.terminate()
    except OSError:
        pass


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except OSError:
            pass


def _validated_wav_info(path: Path) -> _WavInfo:
    if not path.exists() or path.stat().st_size <= 44:
        path.unlink(missing_ok=True)
        raise SystemAudioCaptureError("系统音频采集未生成有效 WAV 文件。")
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        path.unlink(missing_ok=True)
        raise SystemAudioCaptureError("系统音频采集生成的 WAV 文件无效。") from exc
    if channels <= 0 or sample_rate <= 0 or frames <= 0:
        path.unlink(missing_ok=True)
        raise SystemAudioCaptureError("系统音频采集生成的 WAV 文件没有有效音频帧。")
    return _WavInfo(sample_rate=sample_rate, channel_count=channels)


def _is_valid_wav_file(path: Path) -> bool:
    try:
        _validated_wav_info(path)
    except SystemAudioCaptureError:
        return False
    return True


def _subprocess_platform_options() -> dict[str, int]:
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")}
    return {}


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
