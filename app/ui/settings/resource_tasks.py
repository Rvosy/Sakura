"""Background resource installation tasks for the Tauri settings window."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.agent.memory import DEFAULT_EMBEDDING_MODEL
from app.backchannel.model_cache import (
    DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
    backchannel_model_cache_kwargs,
    backchannel_model_cached,
    backchannel_model_endpoint,
    download_backchannel_model,
    import_backchannel_model_archive,
)
from app.voice.runtime_compat import find_usable_runtime_python
from app.voice.tts_bundle import (
    DownloadCancelledError,
    GENIE_TTS,
    GPT_SOVITS_NVIDIA50,
    GPT_SOVITS_STANDARD,
    MIN_GPT_SOVITS_VRAM_GB,
    TTSBundleDownloadProgress,
    TTSBundleEntry,
    TTSBundleInstallResult,
    _GPT_SOVITS_VRAM_TOLERANCE_GB,
    compatible_tts_bundles,
    default_bundle_work_dir,
    format_bundle_label,
    format_bundle_size,
    format_gpu_summary,
    format_platform_summary,
    install_tts_bundle,
    list_nvidia_gpus,
    recommend_gpt_sovits_bundle,
    recommend_tts_bundle,
)
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
)


TaskRunner = Callable[["ResourceTask"], dict[str, Any]]

_MANAGERS: dict[str, "SettingsResourceTaskManager"] = {}
_MANAGERS_LOCK = threading.Lock()


def settings_resource_task_manager(
    base_dir: Path,
    *,
    memory_store: Any | None = None,
) -> "SettingsResourceTaskManager":
    key = str(Path(base_dir).resolve())
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = SettingsResourceTaskManager(Path(base_dir))
            _MANAGERS[key] = manager
    manager.set_memory_store(memory_store)
    return manager


@dataclass
class ResourceTask:
    key: str
    kind: str
    title: str
    runner: TaskRunner
    context: dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    stage: str = "queued"
    message: str = ""
    detail: str = ""
    progress: int = 0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    bytes_per_second: float = 0.0
    resumed: bool = False
    cancellable: bool = False
    error: str = ""
    result: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name=f"sakura-settings-resource-{self.key}", daemon=True)
        self._thread = thread
        thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.update(
            message="正在暂停...",
            detail="已下载部分会保留，下次可继续。",
            cancellable=False,
        )

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelledError("用户取消了下载")

    def update(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
            self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "key": self.key,
                "kind": self.kind,
                "title": self.title,
                "context": dict(self.context),
                "status": self.status,
                "stage": self.stage,
                "message": self.message,
                "detail": self.detail,
                "progress": int(self.progress),
                "downloaded_bytes": int(self.downloaded_bytes),
                "total_bytes": int(self.total_bytes),
                "bytes_per_second": float(self.bytes_per_second),
                "resumed": bool(self.resumed),
                "cancellable": bool(self.cancellable and self.status == "running"),
                "error": self.error,
                "result": dict(self.result or {}),
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "finished_at": self.finished_at,
            }

    def _run(self) -> None:
        self.update(status="running", stage="prepare", message="正在准备...", progress=0)
        try:
            result = self.runner(self)
        except DownloadCancelledError:
            self.update(
                status="cancelled",
                stage="cancelled",
                message="已暂停",
                detail="已下载部分会保留，下次可继续。",
                cancellable=False,
                finished_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001 - UI boundary converts to a readable snapshot.
            self.update(
                status="failed",
                stage="failed",
                message="处理失败",
                detail=str(exc),
                error=str(exc),
                cancellable=False,
                finished_at=time.time(),
            )
        else:
            self.update(
                status="succeeded",
                stage="done",
                message="已完成",
                detail="",
                progress=100,
                result=result,
                cancellable=False,
                finished_at=time.time(),
            )


class SettingsResourceTaskManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._memory_store: Any | None = None
        self._lock = threading.Lock()
        self._tasks: dict[str, ResourceTask] = {}

    def set_memory_store(self, memory_store: Any | None) -> None:
        self._memory_store = memory_store

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "resources.status":
            return self.snapshot()
        if method == "resources.tts.install":
            return self.start_tts_install(str(params.get("bundle_key") or ""))
        if method == "resources.tts.cancel":
            return self.cancel_task("tts")
        if method == "resources.backchannel.download":
            return self.start_backchannel_download()
        if method == "resources.backchannel.import":
            return self.start_backchannel_import(_required_path(params, "path"))
        if method == "resources.memory.download":
            return self.start_memory_download()
        if method == "resources.memory.import":
            return self.start_memory_import(_required_path(params, "path"))
        raise ValueError(f"未知 Tauri RPC 方法：{method}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "tts": self._tts_snapshot(),
            "backchannel": self._backchannel_snapshot(),
            "memory_model": self._memory_model_snapshot(),
            "tasks": self._tasks_snapshot(),
        }

    def start_tts_install(self, bundle_key: str) -> dict[str, Any]:
        entries = {entry.key: entry for entry in compatible_tts_bundles()}
        entry = entries.get(bundle_key)
        if entry is None:
            recommended = recommend_tts_bundle(list_nvidia_gpus())
            entry = recommended or next(iter(entries.values()), None)
        if entry is None:
            raise ValueError("当前平台暂无可一键安装的 TTS 整合包。")

        def run(task: ResourceTask) -> dict[str, Any]:
            result = install_tts_bundle(
                entry,
                self.base_dir,
                check_cancel=task.check_cancelled,
                on_progress=lambda value: task.update(progress=int(value)),
                on_status=lambda status: task.update(
                    stage=status,
                    message=_tts_stage_message(status),
                    cancellable=status == "download",
                ),
                on_download_progress=lambda progress: _apply_tts_download_progress(task, progress),
            )
            return _tts_install_result_to_mapping(result)

        task = ResourceTask(
            key="tts",
            kind="tts",
            title=entry.label,
            runner=run,
            context={"bundle_key": entry.key, "provider": entry.provider},
            cancellable=True,
        )
        self._start_task(task)
        return self.snapshot()

    def start_backchannel_download(self) -> dict[str, Any]:
        def run(task: ResourceTask) -> dict[str, Any]:
            task.update(stage="download", message="正在在线安装接话模型...", progress=20)
            result = download_backchannel_model(self.base_dir)
            return _model_result_to_mapping(result)

        self._start_task(
            ResourceTask(
                key="backchannel",
                kind="backchannel",
                title="接话模型",
                runner=run,
            )
        )
        return self.snapshot()

    def start_backchannel_import(self, archive_path: Path) -> dict[str, Any]:
        def run(task: ResourceTask) -> dict[str, Any]:
            task.update(stage="import", message="正在导入接话模型...", progress=35)
            result = import_backchannel_model_archive(archive_path, self.base_dir)
            return _model_result_to_mapping(result)

        self._start_task(
            ResourceTask(
                key="backchannel",
                kind="backchannel",
                title="接话模型",
                runner=run,
            )
        )
        return self.snapshot()

    def start_memory_download(self) -> dict[str, Any]:
        store = self._require_memory_store()

        def run(task: ResourceTask) -> dict[str, Any]:
            task.update(stage="download", message="正在在线安装记忆模型...", progress=20)
            result = store.download_embedding_model()
            return _model_result_to_mapping(result)

        self._start_task(
            ResourceTask(
                key="memory_model",
                kind="memory_model",
                title="记忆模型",
                runner=run,
            )
        )
        return self.snapshot()

    def start_memory_import(self, archive_path: Path) -> dict[str, Any]:
        store = self._require_memory_store()

        def run(task: ResourceTask) -> dict[str, Any]:
            task.update(stage="import", message="正在导入记忆模型...", progress=35)
            result = store.import_embedding_model_archive(archive_path)
            return _model_result_to_mapping(result)

        self._start_task(
            ResourceTask(
                key="memory_model",
                kind="memory_model",
                title="记忆模型",
                runner=run,
            )
        )
        return self.snapshot()

    def cancel_task(self, key: str) -> dict[str, Any]:
        task = self._tasks.get(key)
        if task is not None and task.snapshot()["status"] == "running":
            task.cancel()
        return self.snapshot()

    def _start_task(self, task: ResourceTask) -> None:
        with self._lock:
            existing = self._tasks.get(task.key)
            if existing is not None and existing.snapshot()["status"] == "running":
                raise ValueError("已有资源任务正在处理，请等待完成。")
            self._tasks[task.key] = task
        task.start()

    def _require_memory_store(self) -> Any:
        if self._memory_store is None:
            raise ValueError("长期记忆系统不可用。")
        return self._memory_store

    def _tasks_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: task.snapshot() for key, task in self._tasks.items()}

    def _tts_snapshot(self) -> dict[str, Any]:
        gpus = list_nvidia_gpus()
        entries = list(compatible_tts_bundles())
        recommended = recommend_tts_bundle(gpus)
        gpt_sovits_recommended = recommend_gpt_sovits_bundle(gpus)
        return {
            "platform": format_platform_summary(),
            "gpu_summary": format_gpu_summary(gpus),
            "recommended_key": recommended.key if recommended is not None else "",
            "gpt_sovits_recommended_key": (
                gpt_sovits_recommended.key if gpt_sovits_recommended is not None else ""
            ),
            "genie_key": GENIE_TTS.key if GENIE_TTS in entries else "",
            "gpu_status": _tts_gpu_status(gpus, recommended, gpt_sovits_recommended),
            "bundles": [_tts_bundle_to_mapping(entry, self.base_dir) for entry in entries],
            "task": self._tasks.get("tts").snapshot() if self._tasks.get("tts") is not None else None,
        }

    def _backchannel_snapshot(self) -> dict[str, Any]:
        ready = backchannel_model_cached(self.base_dir)
        kwargs = backchannel_model_cache_kwargs(self.base_dir) if ready else {}
        return {
            "ready": ready,
            "model_name": DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
            "endpoint": backchannel_model_endpoint(),
            "cache_folder": str(kwargs.get("cache_folder") or ""),
            "task": (
                self._tasks.get("backchannel").snapshot()
                if self._tasks.get("backchannel") is not None
                else None
            ),
        }

    def _memory_model_snapshot(self) -> dict[str, Any]:
        store = self._memory_store
        ready = False
        available = store is not None and callable(getattr(store, "needs_embedding_model_download", None))
        error = ""
        if available:
            try:
                ready = not bool(store.needs_embedding_model_download())
            except Exception as exc:  # noqa: BLE001 - resource status must stay best-effort.
                error = str(exc)
        return {
            "available": available,
            "ready": ready,
            "model_name": DEFAULT_EMBEDDING_MODEL,
            "error": error,
            "task": (
                self._tasks.get("memory_model").snapshot()
                if self._tasks.get("memory_model") is not None
                else None
            ),
        }


def _required_path(params: dict[str, Any], key: str) -> Path:
    value = str(params.get(key) or "").strip().strip('"')
    if not value:
        raise ValueError("请选择要导入的 ZIP 文件。")
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    if path.suffix.lower() != ".zip":
        raise ValueError("只能导入 ZIP 文件。")
    return path


def _tts_bundle_to_mapping(entry: TTSBundleEntry, base_dir: Path) -> dict[str, Any]:
    work_dir = default_bundle_work_dir(entry, base_dir)
    installed = _tts_bundle_installed(entry, work_dir)
    variant, variant_label = _tts_bundle_variant(entry)
    return {
        "key": entry.key,
        "label": entry.label,
        "display_label": format_bundle_label(entry),
        "size_label": format_bundle_size(entry),
        "provider": entry.provider,
        "variant": variant,
        "variant_label": variant_label,
        "download_url": entry.download_url,
        "installed": installed,
        "work_dir": str(work_dir),
        "python_path": str(work_dir / "runtime" / "python.exe") if entry.install_method == "archive" else "",
        "api_url": _provider_api_url(entry.provider),
    }


def _tts_bundle_variant(entry: TTSBundleEntry) -> tuple[str, str]:
    if entry.key == GENIE_TTS.key:
        return "genie-cpu", "CPU 整合包"
    if entry.key == GPT_SOVITS_NVIDIA50.key:
        return "gpt-sovits-50", "NVIDIA 50 系专用包"
    if entry.key == GPT_SOVITS_STANDARD.key:
        return "gpt-sovits-standard", "通用 NVIDIA 整合包"
    if entry.provider == TTS_PROVIDER_GPT_SOVITS:
        return "gpt-sovits", "GPT-SoVITS 整合包"
    return entry.provider, "整合包"


def _tts_gpu_status(
    gpus: list[Any],
    recommended: TTSBundleEntry | None,
    gpt_sovits_recommended: TTSBundleEntry | None,
) -> dict[str, Any]:
    effective_min_vram = MIN_GPT_SOVITS_VRAM_GB - _GPT_SOVITS_VRAM_TOLERANCE_GB
    gpu_items = []
    for gpu in gpus:
        vram_gb = float(getattr(gpu, "vram_gb", 0.0) or 0.0)
        name = str(getattr(gpu, "name", "NVIDIA GPU") or "NVIDIA GPU")
        gpu_items.append(
            {
                "name": name,
                "vram_gb": vram_gb,
                "vram_label": f"{vram_gb:.2f} GB",
                "gpt_sovits_capable": vram_gb >= effective_min_vram,
                "is_50_series": _is_rtx_50_series_name(name),
            }
        )

    capable = any(item["gpt_sovits_capable"] for item in gpu_items)
    vram_note = ""
    if not gpu_items:
        severity = "warning"
        message = "未检测到 NVIDIA 显卡；仍可安装 GPT-SoVITS，但 Genie TTS CPU 整合包通常更稳。"
    elif not capable:
        max_vram = max(item["vram_gb"] for item in gpu_items)
        severity = "warning"
        message = (
            f"检测到 NVIDIA 显卡，但最高显存约 {max_vram:.2f}GB，运行 GPT-SoVITS 可能吃力；"
            "建议使用 Genie TTS CPU 整合包。"
        )
    elif gpt_sovits_recommended is not None and gpt_sovits_recommended.key == GPT_SOVITS_NVIDIA50.key:
        severity = "ok"
        message = ""
    else:
        severity = "ok"
        message = ""

    return {
        "has_nvidia": bool(gpu_items),
        "gpus": gpu_items,
        "gpt_sovits": {
            "capable": capable,
            "severity": severity,
            "message": message,
            "vram_note": vram_note,
            "recommended_key": gpt_sovits_recommended.key if gpt_sovits_recommended is not None else "",
            "overall_recommended_key": recommended.key if recommended is not None else "",
        },
    }


def _is_rtx_50_series_name(name: str) -> bool:
    return "RTX50" in name.upper().replace(" ", "")

def _tts_install_result_to_mapping(result: TTSBundleInstallResult) -> dict[str, Any]:
    provider = result.provider
    work_dir = Path(result.work_dir)
    return {
        "provider": provider,
        "work_dir": str(work_dir),
        "python_path": str(result.python_path or (work_dir / "runtime" / "python.exe")),
        "tts_config_path": str(result.tts_config_path or ""),
        "api_url": _provider_api_url(provider),
    }


def _model_result_to_mapping(result: Any) -> dict[str, Any]:
    return {
        "model_name": str(getattr(result, "model_name", "")),
        "cache_folder": str(getattr(result, "cache_folder", "")),
        "model_dir": str(getattr(result, "model_dir", "")),
        "snapshot_count": int(getattr(result, "snapshot_count", 0) or 0),
    }


def _tts_bundle_installed(entry: TTSBundleEntry, work_dir: Path) -> bool:
    if entry.install_method == "script":
        python_name = entry.python_path_name or ""
        return bool(python_name and (work_dir.parent / python_name).is_file())
    return find_usable_runtime_python(work_dir / "runtime") is not None


def _provider_api_url(provider: str) -> str:
    return DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL


def _tts_stage_message(status: str) -> str:
    return {
        "verify": "正在校验本地压缩包...",
        "download": "正在下载整合包...",
        "extract": "正在解压整合包...",
        "prepare": "正在准备安装环境...",
        "install": "正在安装运行环境...",
        "configure": "正在生成配置...",
        "cleanup": "正在清理下载压缩包...",
    }.get(status, status or "正在处理...")


def _apply_tts_download_progress(task: ResourceTask, progress: TTSBundleDownloadProgress) -> None:
    task.update(
        stage=progress.status,
        message=_tts_stage_message(progress.status),
        detail=_format_download_detail(progress),
        progress=int(progress.percent),
        downloaded_bytes=int(progress.downloaded_bytes),
        total_bytes=int(progress.total_bytes),
        bytes_per_second=float(progress.bytes_per_second),
        resumed=bool(progress.resumed),
        cancellable=True,
    )


def _format_download_detail(progress: TTSBundleDownloadProgress) -> str:
    prefix = "正在续传" if progress.resumed else "正在下载"
    return (
        f"{prefix}：{_format_bytes(progress.downloaded_bytes)} / "
        f"{_format_bytes(progress.total_bytes)}，{_format_speed(progress.bytes_per_second)}"
    )


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "正在计算速度"
    return f"{_format_bytes(int(bytes_per_second))}/s"
