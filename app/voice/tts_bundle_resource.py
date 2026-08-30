"""Plugin-owned Settings resource for one local TTS bundle."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.storage.paths import user_facing_path
from app.voice.tts_bundle import (
    DownloadCancelledError,
    TTSBundleDownloadProgress,
    TTSBundleEntry,
    TTSBundleInstallResult,
    format_bundle_size,
    install_tts_bundle,
    is_tts_bundle_installed,
)


Installer = Callable[..., TTSBundleInstallResult]
EntryProvider = Callable[[], TTSBundleEntry | None]


class TTSBundleResource:
    """Own the download thread and expose it through Plugin Settings actions."""

    def __init__(
        self,
        *,
        user_root: Path,
        config_get: Callable[[], Mapping[str, Any]],
        config_update: Callable[[Mapping[str, Any]], object],
        entry: EntryProvider,
        custom_endpoint: Callable[[Mapping[str, Any]], bool],
        installer: Installer = install_tts_bundle,
    ) -> None:
        self._user_root = Path(user_root)
        self._config_get = config_get
        self._config_update = config_update
        self._entry_provider = entry
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
        entry = self._entry_provider()
        if self._custom_endpoint(config):
            return {"bundleResource": self._value(
                applicability="not_required",
                subtitle="外部服务",
                ready=True,
                message="无需安装",
                detail="当前配置连接已有服务。",
                actions=[],
            )}
        if entry is None:
            return {"bundleResource": self._value(
                applicability="unsupported",
                subtitle="当前平台",
                ready=False,
                message="不支持一键安装",
                detail="当前平台没有兼容安装包，可连接已有服务。",
                actions=[],
            )}
        if is_tts_bundle_installed(entry, self._user_root):
            return {"bundleResource": self._value(
                applicability="required",
                subtitle=f"{entry.label} · {format_bundle_size(entry)}",
                ready=True,
                message="已安装",
                detail="组件已就绪。",
                actions=[],
                terminal_state="succeeded",
            )}
        with self._lock:
            state = self._state
        actions = (
            ["cancelBundle"] if state in {"queued", "running"}
            else ["retryBundle"] if state in {"failed", "cancelled"}
            else ["installBundle"]
        )
        messages = {
            "queued": "等待下载",
            "running": self._stage or "正在安装",
            "failed": "安装失败",
            "cancelled": "已取消",
        }
        return {"bundleResource": self._value(
            applicability="required",
            subtitle=f"{entry.label} · {format_bundle_size(entry)}",
            ready=False,
            message=messages.get(state, "尚未安装"),
            detail=self._detail(),
            actions=actions,
        )}

    def start(self, _values: Mapping[str, object]) -> dict[str, object]:
        config = dict(self._config_get())
        entry = self._entry_provider()
        if self._custom_endpoint(config) or entry is None:
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
            self._thread = threading.Thread(
                target=self._run,
                args=(entry,),
                name=f"tts-bundle-{entry.key}",
                daemon=True,
            )
            self._thread.start()
        return {"values": self.load(), "message": "已开始安装组件。"}

    def cancel(self, _values: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._cancel.set()
                message = "正在取消组件安装。"
            else:
                message = "当前没有进行中的组件安装。"
        return {"values": self.load(), "message": message}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _run(self, entry: TTSBundleEntry) -> None:
        try:
            self._set_state("running", "准备安装", 0)
            result = self._installer(
                entry,
                self._user_root,
                check_cancel=self._check_cancel,
                on_progress=lambda progress: self._set_progress(progress),
                on_status=lambda status: self._set_stage(status),
                on_download_progress=self._set_download_progress,
            )
            patch: dict[str, object] = {"workDir": user_facing_path(result.work_dir)}
            if result.python_path is not None:
                patch["pythonPath"] = user_facing_path(result.python_path)
            if result.tts_config_path is not None:
                patch["ttsConfigPath"] = user_facing_path(result.tts_config_path)
            self._config_update(patch)
            self._set_state("succeeded", "安装完成", 100)
        except DownloadCancelledError:
            self._set_state("cancelled", "已取消", None)
        except Exception:
            self._set_state("failed", "安装失败", None)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise DownloadCancelledError()

    def _set_state(self, state: str, stage: str, progress: int | None) -> None:
        with self._lock:
            self._state = state
            self._stage = stage[:240]
            self._progress = progress

    def _set_stage(self, stage: str) -> None:
        with self._lock:
            self._state = "running"
            self._stage = str(stage)[:240]

    def _set_progress(self, progress: int) -> None:
        with self._lock:
            self._state = "running"
            self._progress = max(0, min(100, int(progress)))

    def _set_download_progress(self, progress: TTSBundleDownloadProgress) -> None:
        with self._lock:
            self._state = "running"
            self._downloaded = max(0, int(progress.downloaded_bytes))
            self._total = max(0, int(progress.total_bytes))

    def _detail(self) -> str:
        with self._lock:
            if self._downloaded > 0 and self._total > 0:
                return f"已下载 {self._downloaded:,} / {self._total:,} 字节"
            return "下载只会在点击安装或重试后开始。"

    def _value(
        self,
        *,
        applicability: str,
        subtitle: str,
        ready: bool,
        message: str,
        detail: str,
        actions: list[str],
        terminal_state: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            state = terminal_state or self._state
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
