"""Plugin-owned download/install flow for the Genie runtime bundle."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


logger = logging.getLogger(__name__)


class DownloadCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class TTSBundleEntry:
    key: str
    label: str
    filename: str
    download_url: str
    size: int
    sha256: str
    supported_systems: tuple[str, ...] = ()


@dataclass(frozen=True)
class TTSBundleInstallResult:
    work_dir: Path


@dataclass(frozen=True)
class TTSBundleDownloadProgress:
    downloaded_bytes: int
    total_bytes: int


GENIE_TTS = TTSBundleEntry(
    key="genie_tts_server",
    label="Genie TTS CPU 整合包",
    filename="Genie-TTS Server.7z",
    download_url=(
        "https://www.modelscope.cn/models/twillzxy/genie-tts-server/"
        "resolve/master/Genie-TTS%20Server.7z"
    ),
    size=1041915345,
    sha256="8f06077b6102aa29f1c9473926db9a74890d627f077393aa8ebb928b52f15de1",
    supported_systems=("windows",),
)


def _system_name() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def is_bundle_supported(entry: TTSBundleEntry) -> bool:
    return not entry.supported_systems or _system_name() in entry.supported_systems


def _tts_root(user_root: Path) -> Path:
    config_path = user_root / "config" / "storage.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    value = payload.get("ttsRoot") if isinstance(payload, Mapping) else None
    if isinstance(value, str) and value.strip() and Path(value).is_absolute():
        return Path(value).resolve(strict=False)
    return user_root / "tts"


def _install_dir(user_root: Path) -> Path:
    return _tts_root(user_root) / "cpu"


def _runtime_ready(path: Path) -> bool:
    names = ("python.exe",) if sys.platform == "win32" else ("bin/python3", "bin/python", "python3", "python")
    return any((path / "runtime" / name).is_file() for name in names)


def _format_size(size: int) -> str:
    return f"约 {size / 1_000_000_000:.1f} GB" if size >= 1_000_000_000 else f"约 {size / 1_000_000:.0f} MB"


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
        request = urllib.request.Request(entry.download_url, headers=headers)
        with urllib.request.urlopen(request, timeout=600) as response:
            status = getattr(response, "status", None)
            if offset and status != 206:
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
        "TTS_BUNDLE_PLATFORM_UNSUPPORTED": "PLATFORM_UNSUPPORTED",
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
        "DOWNLOAD_CONTENT_INVALID": "下载内容不是有效的 Genie TTS 组件。",
        "EXTRACTOR_MISSING": "缺少 7z 解压组件，请修复 Sakura Runtime。",
        "EXTRACT_FAILED": "组件解压失败，请确认磁盘空间充足后重试。",
        "INSTALL_TARGET_BUSY": "安装目录正被占用或不可写，请关闭相关程序后重试。",
        "INSTALL_FAILED": "组件安装失败，请确认磁盘空间和目录权限后重试。",
        "PLATFORM_UNSUPPORTED": "当前平台不支持这个组件包。",
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
        py7zz.extract_archive(str(archive), str(target))
        return
    for name in ("7zz.exe", "7za.exe", "7z.exe", "7zz", "7za", "7z"):
        executable = shutil.which(name)
        if executable:
            result = subprocess.run(
                [executable, "x", str(archive), f"-o{target}", "-y"],
                check=False,
                capture_output=True,
                text=True,
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
    if not _runtime_ready(root):
        raise RuntimeError("TTS_BUNDLE_RUNTIME_INVALID")
    return root


def _replace_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.previous")
    if backup.exists():
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


def install_bundle(
    entry: TTSBundleEntry,
    user_root: Path,
    *,
    check_cancel: Callable[[], None],
    on_progress: Callable[[int], None],
    on_status: Callable[[str], None],
    on_download_progress: Callable[[TTSBundleDownloadProgress], None],
) -> TTSBundleInstallResult:
    if not is_bundle_supported(entry):
        raise RuntimeError("TTS_BUNDLE_PLATFORM_UNSUPPORTED")
    root = _tts_root(user_root)
    archive = root / "_dl" / entry.filename
    staging = root / "_tmp" / entry.key
    archive.parent.mkdir(parents=True, exist_ok=True)
    on_status("verify")
    on_progress(0)
    if not archive.is_file() or archive.stat().st_size != entry.size or _sha256(archive) != entry.sha256:
        on_status("download")
        _download(
            entry,
            archive,
            check_cancel=check_cancel,
            on_progress=on_progress,
            on_download=on_download_progress,
        )
    check_cancel()
    on_status("extract")
    shutil.rmtree(staging, ignore_errors=True)
    _extract(archive, staging)
    check_cancel()
    on_status("install")
    _replace_directory(_extracted_root(staging), _install_dir(user_root))
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    on_status("cleanup")
    on_progress(100)
    return TTSBundleInstallResult(_install_dir(user_root).resolve())


class TTSBundleResource:
    def __init__(
        self,
        *,
        user_root: Path,
        config_get: Callable[[], Mapping[str, Any]],
        config_update: Callable[[Mapping[str, Any]], object],
        entry: Callable[[], TTSBundleEntry | None],
        custom_endpoint: Callable[[Mapping[str, Any]], bool],
        installer: Callable[..., TTSBundleInstallResult] = install_bundle,
    ) -> None:
        self._user_root = Path(user_root)
        self._config_get = config_get
        self._config_update = config_update
        self._entry = entry
        self._custom_endpoint = custom_endpoint
        self._installer = installer
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state = "idle"
        self._stage = ""
        self._progress: int | None = None
        self._downloaded = 0
        self._total = 0
        self._error_code = ""

    @staticmethod
    def descriptor(section_id: str, title: str, label: str) -> dict[str, object]:
        return {
            "sectionId": section_id,
            "title": title,
            "order": 100,
            "fields": [{
                "key": "bundleResource",
                "label": label,
                "type": "resource",
                "description": "由此插件安装和维护的本地运行组件。",
                "actionIds": ["installBundle", "retryBundle", "cancelBundle"],
                "default": {
                    "applicability": "required",
                    "subtitle": "",
                    "ready": False,
                    "taskState": "idle",
                    "message": "",
                    "detail": "",
                    "progress": None,
                    "availableActionIds": [],
                },
            }],
            "actions": [
                {"actionId": "installBundle", "label": "安装", "description": "下载并安装推荐组件。"},
                {"actionId": "retryBundle", "label": "重试", "description": "重新尝试安装推荐组件。"},
                {"actionId": "cancelBundle", "label": "取消", "description": "取消下载并保留可续传分片。"},
            ],
        }

    def load(self) -> dict[str, object]:
        config = dict(self._config_get())
        entry = self._entry()
        if self._custom_endpoint(config):
            return {"bundleResource": self._value("not_required", "外部服务", True, "无需安装", "当前配置连接已有服务。", [])}
        if entry is None:
            return {"bundleResource": self._value("unsupported", "当前平台", False, "不支持一键安装", "当前平台没有兼容安装包，可连接已有服务。", [])}
        if _runtime_ready(_install_dir(self._user_root)):
            return {"bundleResource": self._value("required", f"{entry.label} · {_format_size(entry.size)}", True, "已安装", "组件已就绪。", [], terminal="succeeded")}
        with self._lock:
            state = self._state
            error_code = self._error_code
        actions = ["cancelBundle"] if state in {"queued", "running"} else ["retryBundle"] if state in {"failed", "cancelled"} else ["installBundle"]
        message = {"queued": "等待下载", "running": self._stage or "正在安装", "failed": "安装失败", "cancelled": "已取消"}.get(state, "尚未安装")
        detail = (
            _failure_detail(error_code)
            if state == "failed"
            else f"已下载 {self._downloaded:,} / {self._total:,} 字节"
            if self._downloaded and self._total
            else "下载只会在点击安装或重试后开始。"
        )
        return {"bundleResource": self._value("required", f"{entry.label} · {_format_size(entry.size)}", False, message, detail, actions)}

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
            self._state = "queued"
            self._stage = "等待下载"
            self._progress = None
            self._downloaded = 0
            self._total = entry.size
            self._error_code = ""
            self._thread = threading.Thread(target=self._run, args=(entry,), name="genie-bundle-install", daemon=True)
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
            result = self._installer(
                entry,
                self._user_root,
                check_cancel=self._check_cancel,
                on_progress=self._set_progress,
                on_status=self._set_stage,
                on_download_progress=self._set_download,
            )
            self._config_update({"workDir": os.path.normpath(str(result.work_dir))})
            self._set_state("succeeded", "安装完成", 100)
        except DownloadCancelledError:
            self._set_state("cancelled", "已取消", None)
        except Exception as error:
            with self._lock:
                stage = self._stage
            code = _failure_code(error, stage)
            logger.warning(
                "Genie bundle install failed stage=%s error_type=%s code=%s",
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
            self._downloaded = max(0, progress.downloaded_bytes)
            self._total = max(0, progress.total_bytes)

    def _value(self, applicability: str, subtitle: str, ready: bool, message: str, detail: str, actions: list[str], *, terminal: str | None = None) -> dict[str, object]:
        with self._lock:
            state = terminal or self._state
            progress = self._progress if state in {"queued", "running"} else None
        return {
            "applicability": applicability,
            "subtitle": subtitle[:512],
            "ready": ready,
            "taskState": state,
            "message": message[:240],
            "detail": detail[:240],
            "progress": progress,
            "availableActionIds": actions,
        }
