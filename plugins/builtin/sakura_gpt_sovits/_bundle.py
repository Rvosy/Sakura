"""Plugin-owned download/install flow for GPT-SoVITS runtime bundles."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from ._runtime_profile import managed_profile_path, prepare_managed_profile
except ImportError:  # pragma: no cover - loose plugin execution
    from _runtime_profile import managed_profile_path, prepare_managed_profile


logger = logging.getLogger(__name__)


def _external_path(value: str | Path) -> str:
    text = str(value)
    if sys.platform == "win32":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return os.path.normpath(text)


class DownloadCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class TTSBundleEntry:
    key: str
    label: str
    filename: str = ""
    download_url: str = ""
    size: int = 0
    sha256: str = ""
    supported_systems: tuple[str, ...] = ()
    install_method: str = "archive"
    installer_script: str | None = None
    work_dir_name: str | None = None
    python_path_name: str | None = None
    tts_config_path_name: str | None = None


@dataclass(frozen=True)
class TTSBundleInstallResult:
    work_dir: Path
    python_path: Path | None = None
    tts_config_path: Path | None = None


@dataclass(frozen=True)
class TTSBundleDownloadProgress:
    downloaded_bytes: int
    total_bytes: int


GPT_SOVITS_STANDARD = TTSBundleEntry(
    key="gpt_sovits_v2pro",
    label="GPT-SoVITS v2pro 通用整合包",
    filename="GPT-SoVITS-v2pro-20250604.7z",
    download_url=(
        "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/"
        "resolve/master/GPT-SoVITS-v2pro-20250604.7z"
    ),
    size=8185086602,
    sha256="bd60d0796553ff05d8568136e199c13e0dc22ebe2ed24273134e34ed6f215cd6",
    supported_systems=("windows",),
)
GPT_SOVITS_NVIDIA50 = TTSBundleEntry(
    key="gpt_sovits_nvidia50",
    label="GPT-SoVITS v2pro NVIDIA 50 系整合包",
    filename="GPT-SoVITS-v2pro-20250604-nvidia50.7z",
    download_url=(
        "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/"
        "resolve/master/GPT-SoVITS-v2pro-20250604-nvidia50.7z"
    ),
    size=8835144925,
    sha256="97b4edcd451c42357db7e26e6c1c877ca5d85144fe97beaff6d7005d35bee008",
    supported_systems=("windows",),
)
GPT_SOVITS_MACOS = TTSBundleEntry(
    key="gpt_sovits_macos",
    label="GPT-SoVITS macOS 源码安装包",
    supported_systems=("macos",),
    install_method="script",
    installer_script="install_gpt_sovits_macos.sh",
    work_dir_name="GPT-SoVITS",
    python_path_name="miniforge3/envs/gpt-sovits310/bin/python",
    tts_config_path_name="GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml",
)


def _system_name() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def _supported(entry: TTSBundleEntry) -> bool:
    return not entry.supported_systems or _system_name() in entry.supported_systems


def recommend_gpt_sovits_bundle() -> TTSBundleEntry | None:
    if sys.platform == "darwin":
        return GPT_SOVITS_MACOS
    if sys.platform != "win32":
        return None
    try:
        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if probe.returncode == 0 and re.search(r"\bRTX\s*50[0-9]{2}\b", probe.stdout, re.IGNORECASE):
            return GPT_SOVITS_NVIDIA50
    except (OSError, subprocess.TimeoutExpired):
        pass
    return GPT_SOVITS_STANDARD


def _tts_root(user_root: Path) -> Path:
    try:
        payload = json.loads((user_root / "config" / "storage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    value = payload.get("ttsRoot") if isinstance(payload, Mapping) else None
    if isinstance(value, str) and value.strip() and Path(value).is_absolute():
        return Path(value).resolve(strict=False)
    return user_root / "tts"


def _install_dir(entry: TTSBundleEntry, user_root: Path) -> Path:
    short = {GPT_SOVITS_STANDARD.key: "gpt", GPT_SOVITS_NVIDIA50.key: "g50"}.get(entry.key, entry.key)
    return _tts_root(user_root) / short


def _result(entry: TTSBundleEntry, installed: Path) -> TTSBundleInstallResult:
    work = installed / entry.work_dir_name if entry.work_dir_name else installed
    python = installed / entry.python_path_name if entry.python_path_name else None
    config = installed / entry.tts_config_path_name if entry.tts_config_path_name else None
    if config is None and entry.key in {GPT_SOVITS_STANDARD.key, GPT_SOVITS_NVIDIA50.key}:
        generated = managed_profile_path(work)
        config = generated if generated.is_file() else None
    if not work.is_dir() or not (work / "api_v2.py").is_file():
        raise RuntimeError("TTS_BUNDLE_RUNTIME_INVALID")
    if python is not None and not python.is_file():
        raise RuntimeError("TTS_BUNDLE_PYTHON_INVALID")
    if config is not None and not config.is_file():
        raise RuntimeError("TTS_BUNDLE_CONFIG_INVALID")
    return TTSBundleInstallResult(
        work.resolve(),
        python.resolve() if python else None,
        config.resolve() if config else None,
    )


def _installed(entry: TTSBundleEntry, user_root: Path) -> bool:
    try:
        _result(entry, _install_dir(entry, user_root))
    except RuntimeError:
        return False
    return True


def installed_bundle_result(user_root: Path) -> TTSBundleInstallResult | None:
    """Return the currently recommended managed bundle when it is ready."""

    entry = recommend_gpt_sovits_bundle()
    if entry is None:
        return None
    try:
        return _result(entry, _install_dir(entry, user_root))
    except RuntimeError:
        return None


def _format_size(entry: TTSBundleEntry) -> str:
    if entry.install_method == "script":
        return "在线安装"
    return f"约 {entry.size / 1_000_000_000:.1f} GB"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            time.sleep(0)
    return digest.hexdigest()


def _download(
    entry: TTSBundleEntry,
    archive: Path,
    *,
    check_cancel: Callable[[], None],
    on_progress: Callable[[int], None],
    on_download: Callable[[TTSBundleDownloadProgress], None],
) -> None:
    part = archive.with_name(f"{archive.name}.part")
    offset = part.stat().st_size if part.is_file() else 0
    if offset > entry.size:
        part.unlink(missing_ok=True)
        offset = 0
    downloaded = offset
    if offset < entry.size:
        headers = {"User-Agent": "Sakura-Desktop-Pet/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with urllib.request.urlopen(urllib.request.Request(entry.download_url, headers=headers), timeout=600) as response:
            if offset and getattr(response, "status", None) != 206:
                offset = 0
                downloaded = 0
                part.unlink(missing_ok=True)
            with part.open("ab" if offset else "wb") as output:
                while True:
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    check_cancel()
                    on_progress(10 + int(60 * min(downloaded, entry.size) / entry.size))
                    on_download(TTSBundleDownloadProgress(downloaded, entry.size))
    if downloaded != entry.size:
        raise RuntimeError("TTS_BUNDLE_SIZE_MISMATCH")
    if _sha256(part).lower() != entry.sha256.lower():
        part.unlink(missing_ok=True)
        raise RuntimeError("TTS_BUNDLE_SHA256_MISMATCH")
    os.replace(part, archive)


def _failure_code(error: Exception, stage: str) -> str:
    known = {
        "TTS_BUNDLE_SIZE_MISMATCH": "DOWNLOAD_SIZE_MISMATCH",
        "TTS_BUNDLE_SHA256_MISMATCH": "DOWNLOAD_CHECKSUM_MISMATCH",
        "TTS_BUNDLE_EXTRACTOR_MISSING": "EXTRACTOR_MISSING",
        "TTS_BUNDLE_RUNTIME_INVALID": "DOWNLOAD_CONTENT_INVALID",
        "TTS_BUNDLE_PYTHON_INVALID": "DOWNLOAD_CONTENT_INVALID",
        "TTS_BUNDLE_CONFIG_INVALID": "DOWNLOAD_CONTENT_INVALID",
        "TTS_BUNDLE_INSTALLER_MISSING": "DOWNLOAD_DEPENDENCY_MISSING",
        "TTS_BUNDLE_INSTALL_FAILED": "INSTALL_FAILED",
        "TTS_BUNDLE_PLATFORM_UNSUPPORTED": "PLATFORM_UNSUPPORTED",
        "TTS_ACCELERATOR_UNAVAILABLE": "TTS_ACCELERATOR_UNAVAILABLE",
        "TTS_DEVICE_PROBE_FAILED": "TTS_DEVICE_PROBE_FAILED",
        "TTS_PROFILE_GENERATION_FAILED": "TTS_PROFILE_GENERATION_FAILED",
    }
    message = str(error)
    if message in known:
        return known[message]
    if isinstance(error, PermissionError):
        return "INSTALL_TARGET_BUSY"
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "DOWNLOAD_NETWORK_FAILED"
    if stage == "download":
        return "DOWNLOAD_NETWORK_FAILED"
    if stage == "extract":
        return "EXTRACT_FAILED"
    if stage in {"install", "cleanup"}:
        return "INSTALL_FAILED"
    return "DOWNLOAD_FAILED"


def _failure_detail(code: str) -> str:
    messages = {
        "DOWNLOAD_NETWORK_FAILED": "无法连接组件下载服务，请检查网络或代理后重试。",
        "DOWNLOAD_SIZE_MISMATCH": "下载文件大小不匹配，可保留分片后重试。",
        "DOWNLOAD_CHECKSUM_MISMATCH": "下载文件校验失败，损坏分片已清理。",
        "DOWNLOAD_CONTENT_INVALID": "下载内容不是有效的 GPT-SoVITS 组件。",
        "DOWNLOAD_DEPENDENCY_MISSING": "安装脚本或运行依赖缺失，请修复 Sakura Runtime。",
        "EXTRACTOR_MISSING": "缺少 7z 解压组件，请修复 Sakura Runtime。",
        "EXTRACT_FAILED": "组件解压失败，请确认磁盘空间充足后重试。",
        "INSTALL_TARGET_BUSY": "安装目录正被占用或不可写，请关闭相关程序后重试。",
        "INSTALL_FAILED": "组件安装失败，请确认磁盘空间和目录权限后重试。",
        "PLATFORM_UNSUPPORTED": "当前平台不支持这个组件包。",
        "TTS_ACCELERATOR_UNAVAILABLE": "未检测到此整合包要求的可用 CUDA 设备，请检查显卡驱动后重试。",
        "TTS_DEVICE_PROBE_FAILED": "无法使用 GPT-SoVITS 运行时检测推理设备，请检查整合包后重试。",
        "TTS_PROFILE_GENERATION_FAILED": "无法生成 GPT-SoVITS 推理配置，请检查整合包后重试。",
        "DOWNLOAD_FAILED": "组件安装发生内部错误，请重试。",
    }
    safe_code = code if code in messages else "DOWNLOAD_FAILED"
    return f"{messages[safe_code]}（{safe_code}）"


def _extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        py7zz = importlib.import_module("py7zz")
    except ImportError:
        py7zz = None
    if py7zz is not None:
        py7zz.extract_archive(_external_path(archive), _external_path(target))
        return
    for name in ("7zz.exe", "7za.exe", "7z.exe", "7zz", "7za", "7z"):
        executable = shutil.which(name)
        if executable:
            result = subprocess.run(
                [executable, "x", _external_path(archive), f"-o{_external_path(target)}", "-y"],
                check=False,
            )
            if result.returncode == 0:
                return
    try:
        py7zr = importlib.import_module("py7zr")
    except ImportError as error:
        raise RuntimeError("TTS_BUNDLE_EXTRACTOR_MISSING") from error
    with py7zr.SevenZipFile(archive, "r") as handle:
        handle.extractall(path=target)


def _extracted_root(staging: Path) -> Path:
    children = [item for item in staging.iterdir() if item.name != "__MACOSX"]
    root = children[0] if len(children) == 1 and children[0].is_dir() else staging
    if not (root / "api_v2.py").is_file():
        raise RuntimeError("TTS_BUNDLE_RUNTIME_INVALID")
    return root


def _replace_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.previous")
    shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(source, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
    else:
        descendants = _posix_descendants(process.pid)
        for pid in (*reversed(descendants), process.pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _posix_descendants(root_pid: int) -> list[int]:
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


def _install_archive(
    entry: TTSBundleEntry,
    user_root: Path,
    *,
    check_cancel: Callable[[], None],
    on_progress: Callable[[int], None],
    on_status: Callable[[str], None],
    on_download_progress: Callable[[TTSBundleDownloadProgress], None],
) -> TTSBundleInstallResult:
    root = _tts_root(user_root)
    archive = root / "_dl" / entry.filename
    staging = root / "_tmp" / entry.key
    archive.parent.mkdir(parents=True, exist_ok=True)
    on_status("verify")
    on_progress(0)
    if not archive.is_file() or archive.stat().st_size != entry.size or _sha256(archive) != entry.sha256:
        on_status("download")
        _download(entry, archive, check_cancel=check_cancel, on_progress=on_progress, on_download=on_download_progress)
    check_cancel()
    on_status("extract")
    shutil.rmtree(staging, ignore_errors=True)
    _extract(archive, staging)
    check_cancel()
    on_status("install")
    extracted = _extracted_root(staging)
    if entry.key in {GPT_SOVITS_STANDARD.key, GPT_SOVITS_NVIDIA50.key}:
        prepare_managed_profile(
            extracted,
            require_cuda=entry.key == GPT_SOVITS_NVIDIA50.key,
            platform="win32",
        )
        check_cancel()
    _replace_directory(extracted, _install_dir(entry, user_root))
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    on_status("cleanup")
    on_progress(100)
    return _result(entry, _install_dir(entry, user_root))


def _install_script(
    entry: TTSBundleEntry,
    user_root: Path,
    *,
    check_cancel: Callable[[], None],
    on_progress: Callable[[int], None],
    on_status: Callable[[str], None],
) -> TTSBundleInstallResult:
    if not entry.installer_script:
        raise RuntimeError("TTS_BUNDLE_INSTALLER_MISSING")
    script = Path(__file__).with_name(entry.installer_script)
    if not script.is_file():
        raise RuntimeError("TTS_BUNDLE_INSTALLER_MISSING")
    root = _tts_root(user_root)
    staging = root / "_tmp" / entry.key
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    env = os.environ.copy()
    env["SAKURA_TTS_INSTALL_DIR"] = _external_path(staging)
    env["SAKURA_TTS_DOWNLOADS_DIR"] = _external_path(root / "_dl")
    process = subprocess.Popen(
        ["bash", _external_path(script), _external_path(staging)],
        cwd=_external_path(Path(__file__).parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            check_cancel()
            match = re.search(r"::sakura-progress\s+status=([a-z_]+)\s+progress=(\d+)", line)
            if match:
                on_status(match.group(1))
                on_progress(int(match.group(2)))
        if process.wait() != 0:
            raise RuntimeError("TTS_BUNDLE_INSTALL_FAILED")
        _result(entry, staging)
        _replace_directory(staging, _install_dir(entry, user_root))
        return _result(entry, _install_dir(entry, user_root))
    except Exception:
        _terminate(process)
        shutil.rmtree(staging, ignore_errors=True)
        raise


def install_bundle(
    entry: TTSBundleEntry,
    user_root: Path,
    *,
    check_cancel: Callable[[], None],
    on_progress: Callable[[int], None],
    on_status: Callable[[str], None],
    on_download_progress: Callable[[TTSBundleDownloadProgress], None],
) -> TTSBundleInstallResult:
    if not _supported(entry):
        raise RuntimeError("TTS_BUNDLE_PLATFORM_UNSUPPORTED")
    if entry.install_method == "script":
        return _install_script(entry, user_root, check_cancel=check_cancel, on_progress=on_progress, on_status=on_status)
    return _install_archive(
        entry,
        user_root,
        check_cancel=check_cancel,
        on_progress=on_progress,
        on_status=on_status,
        on_download_progress=on_download_progress,
    )


class TTSBundleResource:
    def __init__(self, *, user_root: Path, config_get: Callable[[], Mapping[str, Any]], config_update: Callable[[Mapping[str, Any]], object], entry: Callable[[], TTSBundleEntry | None], custom_endpoint: Callable[[Mapping[str, Any]], bool], installer: Callable[..., TTSBundleInstallResult] = install_bundle) -> None:
        self._user_root, self._config_get, self._config_update = Path(user_root), config_get, config_update
        self._entry, self._custom_endpoint, self._installer = entry, custom_endpoint, installer
        self._lock, self._cancel = threading.RLock(), threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state, self._stage, self._progress = "idle", "", None
        self._downloaded = self._total = 0
        self._error_code = ""

    @staticmethod
    def descriptor(section_id: str, title: str, label: str) -> dict[str, object]:
        return {"sectionId": section_id, "title": title, "order": 100, "fields": [{"key": "bundleResource", "label": label, "type": "resource", "description": "由此插件安装和维护的本地运行组件。", "actionIds": ["installBundle", "retryBundle", "cancelBundle"], "default": {"applicability": "required", "subtitle": "", "ready": False, "taskState": "idle", "message": "", "detail": "", "progress": None, "availableActionIds": []}}], "actions": [{"actionId": "installBundle", "label": "安装", "description": "下载并安装推荐组件。"}, {"actionId": "retryBundle", "label": "重试", "description": "重新尝试安装推荐组件。"}, {"actionId": "cancelBundle", "label": "取消", "description": "取消安装。"}]}

    def load(self) -> dict[str, object]:
        entry = self._entry()
        if self._custom_endpoint(dict(self._config_get())):
            return {"bundleResource": self._value("not_required", "外部服务", True, "无需安装", "当前配置连接已有服务。", [])}
        if entry is None:
            return {"bundleResource": self._value("unsupported", "当前平台", False, "不支持一键安装", "当前平台没有兼容安装包，可连接已有服务。", [])}
        with self._lock:
            state = self._state
            error_code = self._error_code
            detail = (
                _failure_detail(error_code)
                if state == "failed"
                else f"已下载 {self._downloaded:,} / {self._total:,} 字节"
                if self._downloaded and self._total
                else "下载只会在点击安装或重试后开始。"
            )
        if state in {"idle", "succeeded"} and _installed(entry, self._user_root):
            return {"bundleResource": self._value("required", f"{entry.label} · {_format_size(entry)}", True, "已安装", "组件已就绪。", [], terminal="succeeded")}
        actions = ["cancelBundle"] if state in {"queued", "running"} else ["retryBundle"] if state in {"failed", "cancelled"} else ["installBundle"]
        message = {"queued": "等待下载", "running": self._stage or "正在安装", "failed": "安装失败", "cancelled": "已取消"}.get(state, "尚未安装")
        return {"bundleResource": self._value("required", f"{entry.label} · {_format_size(entry)}", False, message, detail, actions, terminal=state)}

    def start(self, _values: Mapping[str, object]) -> dict[str, object]:
        entry = self._entry()
        if self._custom_endpoint(dict(self._config_get())) or entry is None:
            return {"values": self.load(), "message": "当前组件无需安装。"}
        with self._lock:
            if self._closed:
                raise RuntimeError("TTS_BUNDLE_RESOURCE_STOPPED")
            if self._thread is not None and self._thread.is_alive():
                return {"values": self.load(), "message": "组件安装已在进行中。"}
            self._cancel.clear()
            self._state, self._stage, self._progress = "queued", "等待下载", None
            self._downloaded, self._total = 0, entry.size
            self._error_code = ""
            self._thread = threading.Thread(target=self._run, args=(entry,), name="gpt-sovits-bundle-install", daemon=True)
            self._thread.start()
        return {"values": self.load(), "message": "已开始安装组件。"}

    def cancel(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            if active:
                self._cancel.set()
        return {"values": self.load(), "message": "正在取消组件安装。" if active else "当前没有进行中的组件安装。"}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _run(self, entry: TTSBundleEntry) -> None:
        try:
            result = self._installer(entry, self._user_root, check_cancel=self._check_cancel, on_progress=self._set_progress, on_status=self._set_stage, on_download_progress=self._set_download)
            patch: dict[str, object] = {
                "workDir": _external_path(result.work_dir),
                # Clear optional overrides left by an older/different runtime.
                # The Windows bundle intentionally discovers its interpreter
                # under workDir when python_path is absent.
                "pythonPath": (
                    _external_path(result.python_path) if result.python_path else ""
                ),
                "ttsConfigPath": (
                    _external_path(result.tts_config_path)
                    if result.tts_config_path
                    else ""
                ),
            }
            self._config_update(patch)
            self._set_state("succeeded", "安装完成", 100)
        except DownloadCancelledError:
            self._set_state("cancelled", "已取消", None)
        except Exception as error:
            with self._lock:
                stage = self._stage
            code = _failure_code(error, stage)
            logger.warning(
                "GPT-SoVITS bundle install failed stage=%s error_type=%s code=%s",
                stage if stage in {"verify", "download", "extract", "install", "cleanup"} else "unknown",
                type(error).__name__,
                code,
            )
            with self._lock:
                self._error_code = code
            self._set_state("failed", "安装失败", None)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise DownloadCancelledError()

    def _set_state(self, state: str, stage: str, progress: int | None) -> None:
        with self._lock:
            self._state, self._stage, self._progress = state, stage[:240], progress

    def _set_stage(self, stage: str) -> None:
        self._set_state("running", str(stage), self._progress)

    def _set_progress(self, progress: int) -> None:
        self._set_state("running", self._stage, max(0, min(100, int(progress))))

    def _set_download(self, progress: TTSBundleDownloadProgress) -> None:
        with self._lock:
            self._downloaded, self._total = max(0, progress.downloaded_bytes), max(0, progress.total_bytes)

    def _value(self, applicability: str, subtitle: str, ready: bool, message: str, detail: str, actions: list[str], *, terminal: str | None = None) -> dict[str, object]:
        with self._lock:
            state = terminal or self._state
            progress = self._progress if state in {"queued", "running"} else None
        return {"applicability": applicability, "subtitle": subtitle[:512], "ready": ready, "taskState": state, "message": message[:240], "detail": detail[:240], "progress": progress, "availableActionIds": actions}
