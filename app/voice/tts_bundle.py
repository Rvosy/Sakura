from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.storage.atomic import rename_with_retry, replace_with_retry
from app.storage.paths import StoragePaths
from app.voice.runtime_compat import current_platform_label, current_system_name, find_usable_runtime_python


ProgressCallback = Callable[[int], None]
StatusCallback = Callable[[str], None]
MigrationProgressCallback = Callable[["TTSBundleMigrationProgress"], None]
DownloadProgressCallback = Callable[["TTSBundleDownloadProgress"], None]

class DownloadCancelledError(Exception):
    """用户主动取消下载时抛出此异常，调用方据此判断是用户取消而非真正的错误。"""

_DOWNLOAD_CHUNK_SIZE = 512 * 1024
_HASH_CHUNK_SIZE = 4 * 1024 * 1024
_VERIFY_PROGRESS_END = 10
_DOWNLOAD_PROGRESS_END = 70
_SEVEN_ZIP_COMMANDS = ("7zz.exe", "7za.exe", "7z.exe", "7zz", "7za", "7z")
_WIN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UrlOpenCallable(Protocol):
    def __call__(self, request: urllib.request.Request, timeout: int) -> object:
        ...


@dataclass(frozen=True)
class TTSBundleEntry:
    key: str
    label: str
    filename: str = ""
    download_url: str = ""
    size: int = 0
    sha256: str = ""
    provider: str = "gpt-sovits"
    supported_systems: tuple[str, ...] = ()
    install_method: str = "archive"
    installer_script: str | None = None
    work_dir_name: str | None = None
    python_path_name: str | None = None
    tts_config_path_name: str | None = None


@dataclass(frozen=True)
class TTSBundleInstallResult:
    work_dir: Path
    provider: str
    python_path: Path | None = None
    tts_config_path: Path | None = None


@dataclass(frozen=True)
class GPUInfo:
    name: str
    vram_gb: float


@dataclass(frozen=True)
class TTSBundleMigration:
    """内置 TTS 整合包从旧深目录迁移到短目录所需的信息。"""

    entry: TTSBundleEntry
    source_dir: Path
    target_dir: Path


@dataclass(frozen=True)
class TTSBundleMigrationProgress:
    """TTS 整合包迁移进度。"""

    entry: TTSBundleEntry
    current_file: str
    completed_files: int
    total_files: int
    copied_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class TTSBundleDownloadProgress:
    """TTS 整合包下载阶段的详细进度。"""

    status: str
    downloaded_bytes: int
    total_bytes: int
    percent: int
    resumed: bool = False
    bytes_per_second: float = 0.0


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
    provider="genie-tts",
    supported_systems=("windows",),
)
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
GPT_SOVITS_MACOS_INSTALLER = TTSBundleEntry(
    key="gpt_sovits_macos",
    label="GPT-SoVITS macOS 源码安装包",
    provider="custom-gpt-sovits",
    supported_systems=("macos",),
    install_method="script",
    installer_script="scripts/install_gpt_sovits_macos.sh",
    work_dir_name="GPT-SoVITS",
    python_path_name="miniforge3/envs/gpt-sovits310/bin/python",
    tts_config_path_name="GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml",
)
GPT_SOVITS_BUNDLES = (GPT_SOVITS_STANDARD, GPT_SOVITS_NVIDIA50)
TTS_BUNDLES = (GENIE_TTS, GPT_SOVITS_STANDARD, GPT_SOVITS_NVIDIA50, GPT_SOVITS_MACOS_INSTALLER)
MIN_GPT_SOVITS_VRAM_GB = 6.0
_GPT_SOVITS_VRAM_TOLERANCE_GB = 0.25
_SYSTEM_LABELS = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
}
_SHORT_BUNDLE_DIRS = {
    GENIE_TTS.key: "cpu",
    GPT_SOVITS_STANDARD.key: "gpt",
    GPT_SOVITS_NVIDIA50.key: "g50",
}
_MIGRATING_DIR_NAME = ".migrating"
_MIGRATION_STATE_FILE = ".sakura_migration.json"
_MIGRATION_TEMP_SUFFIX = ".__sakura_tmp__"
_MIGRATION_COPY_CHUNK_SIZE = 16 * 1024 * 1024


def format_platform_summary() -> str:
    try:
        return platform.platform(aliased=True, terse=True)
    except Exception:
        return f"{platform.system()} {platform.release()}"


def list_nvidia_gpus() -> list[GPUInfo]:
    # GPU 探测属于尽力而为：任何异常都按“未检测到 GPU”处理，避免拖垮设置页等调用方的构造。
    # 注意 shutil.which 也需保护——当 sys.platform 被伪造为 win32（如测试）但宿主非 Windows 时，
    # 其内部会访问 _winapi.NeedCurrentDirectoryForExePath，而非 Windows 上 _winapi 为 None 会抛 AttributeError。
    try:
        exe = shutil.which("nvidia-smi")
    except Exception:
        return []
    if not exe:
        return []
    cmd = [
        exe,
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    kwargs: dict[str, object] = {
        "args": cmd,
        "capture_output": True,
        "text": True,
        "timeout": 8,
    }
    if sys.platform == "win32" and _WIN_NO_WINDOW:
        kwargs["creationflags"] = _WIN_NO_WINDOW
    try:
        result = subprocess.run(**kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    gpus: list[GPUInfo] = []
    for raw_line in (result.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, memory = line.partition(",")
        try:
            memory_mib = float(re.sub(r"[^\d.]", "", memory) or "0")
        except ValueError:
            memory_mib = 0.0
        gpus.append(GPUInfo(name=name.strip() or "NVIDIA GPU", vram_gb=round(memory_mib / 1024, 2)))
    return gpus


def format_gpu_summary(gpus: list[GPUInfo]) -> str:
    if not compatible_tts_bundles():
        return (
            f"当前平台 {current_platform_label()} 暂无可一键下载的 TTS 整合包；"
            "可在设置中选择“自定义 GPT-SoVITS（macOS/Linux）”接入本机源码版运行环境。"
        )
    if not gpus:
        recommended = recommend_tts_bundle(gpus)
        if recommended is not None and recommended != GENIE_TTS:
            return f"未检测到 NVIDIA GPU，将推荐 {format_bundle_label(recommended)}。"
        return "未检测到 NVIDIA GPU，将推荐 Genie TTS CPU 整合包。"
    return "\n".join(f"#{i} NVIDIA | {gpu.name} | {gpu.vram_gb} GB" for i, gpu in enumerate(gpus, start=1))


def format_bundle_size(entry: TTSBundleEntry) -> str:
    if entry.install_method == "script":
        return "在线安装"
    gb = entry.size / 1_000_000_000
    if gb >= 1:
        return f"约 {gb:.1f} GB"
    return f"约 {entry.size / 1_000_000:.0f} MB"


def format_bundle_label(entry: TTSBundleEntry) -> str:
    details = [format_bundle_size(entry)]
    if entry.supported_systems:
        details.append(f"仅 {format_supported_systems(entry.supported_systems)}")
    return f"{entry.label}（{'，'.join(details)}）"


def format_supported_systems(supported_systems: tuple[str, ...]) -> str:
    if not supported_systems:
        return "全部平台"
    return "、".join(_SYSTEM_LABELS.get(system, system) for system in supported_systems)


def is_bundle_supported(entry: TTSBundleEntry) -> bool:
    supported_systems = tuple(system.lower() for system in entry.supported_systems)
    return not supported_systems or current_system_name() in supported_systems


def compatible_tts_bundles() -> tuple[TTSBundleEntry, ...]:
    return tuple(entry for entry in TTS_BUNDLES if is_bundle_supported(entry))


def recommend_gpt_sovits_bundle(gpus: list[GPUInfo] | None = None) -> TTSBundleEntry | None:
    compatible_gpt_bundles = tuple(entry for entry in GPT_SOVITS_BUNDLES if is_bundle_supported(entry))
    if not compatible_gpt_bundles:
        return None
    gpus = list_nvidia_gpus() if gpus is None else gpus
    if GPT_SOVITS_NVIDIA50 in compatible_gpt_bundles and any(_is_rtx_50_series(gpu.name) for gpu in gpus):
        return GPT_SOVITS_NVIDIA50
    if GPT_SOVITS_STANDARD not in compatible_gpt_bundles:
        return compatible_gpt_bundles[0]
    return GPT_SOVITS_STANDARD


def recommend_tts_bundle(gpus: list[GPUInfo] | None = None) -> TTSBundleEntry | None:
    compatible_bundles = compatible_tts_bundles()
    if not compatible_bundles:
        return None
    gpus = list_nvidia_gpus() if gpus is None else gpus
    capable_nvidia = [gpu for gpu in gpus if _has_gpt_sovits_vram(gpu)]
    if not capable_nvidia:
        return GENIE_TTS if GENIE_TTS in compatible_bundles else compatible_bundles[0]
    if GPT_SOVITS_NVIDIA50 in compatible_bundles and any(_is_rtx_50_series(gpu.name) for gpu in capable_nvidia):
        return GPT_SOVITS_NVIDIA50
    return GPT_SOVITS_STANDARD if GPT_SOVITS_STANDARD in compatible_bundles else compatible_bundles[0]


def default_bundle_work_dir(entry: TTSBundleEntry, base_dir: Path) -> Path:
    """返回整合包对应的工作目录；已安装时解析真实解压根目录，未安装时返回预期目录。"""

    if entry.install_method == "script":
        installed_dir = StoragePaths(base_dir).tts_bundle_installed_for(entry.key)
        if entry.work_dir_name:
            return installed_dir / entry.work_dir_name
        return installed_dir

    short_dir = _short_bundle_install_dir(entry, base_dir)
    if _is_installed_bundle_ready(short_dir):
        try:
            return _resolve_extracted_root(short_dir)
        except OSError:
            pass

    legacy_dir = _legacy_bundle_install_dir(entry, base_dir)
    if _is_installed_bundle_ready(legacy_dir):
        try:
            return _resolve_extracted_root(legacy_dir)
        except OSError:
            pass
    return short_dir


def default_provider_bundle_work_dir(provider: str, base_dir: Path) -> Path | None:
    """按 provider 选择整合包默认工作目录，自定义外部 provider 不返回目录。"""

    normalized = provider.strip().lower().replace("_", "-")
    if normalized in {"genie", "genie-tts", "genietts"}:
        return default_bundle_work_dir(GENIE_TTS, base_dir) if is_bundle_supported(GENIE_TTS) else None
    if normalized not in {"gpt-sovits", "gpt-so-vits", "gptsovits"}:
        return None

    if not any(is_bundle_supported(entry) for entry in GPT_SOVITS_BUNDLES):
        return None
    for entry in GPT_SOVITS_BUNDLES:
        if not is_bundle_supported(entry):
            continue
        if _is_bundle_installed(entry, base_dir):
            return default_bundle_work_dir(entry, base_dir)
    recommended = recommend_gpt_sovits_bundle()
    return default_bundle_work_dir(recommended, base_dir) if recommended is not None else None


def is_provider_bundle_work_dir(path: Path, base_dir: Path) -> bool:
    """判断路径是否位于内置 TTS 整合包安装目录下。"""

    resolved = path.resolve()
    for root in (
        (base_dir / "tts").resolve(),
        StoragePaths(base_dir).tts_bundles_installed_dir.resolve(),
    ):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def find_pending_bundle_migrations(base_dir: Path, provider: str | None = None) -> list[TTSBundleMigration]:
    """查找需要从旧深目录迁移到短目录的内置 TTS 整合包。"""

    migrations: list[TTSBundleMigration] = []
    for entry in _entries_for_provider(provider):
        target_dir = _short_bundle_install_dir(entry, base_dir)
        if _is_installed_bundle_ready(target_dir):
            _cleanup_migration_work_dir(TTSBundleMigration(entry=entry, source_dir=target_dir, target_dir=target_dir))
            continue
        legacy_dir = _legacy_bundle_install_dir(entry, base_dir)
        if not _is_installed_bundle_ready(legacy_dir):
            continue
        try:
            source_dir = _resolve_extracted_root(legacy_dir)
        except OSError:
            continue
        migrations.append(TTSBundleMigration(entry=entry, source_dir=source_dir, target_dir=target_dir))
    return migrations


def migrate_bundle_to_short_path(
    migration: TTSBundleMigration,
    *,
    on_progress: MigrationProgressCallback | None = None,
) -> Path:
    """执行单个整合包迁移；失败时保留旧目录和中间目录供下次续迁。"""

    if migration.target_dir.exists():
        if _is_installed_bundle_ready(migration.target_dir):
            _cleanup_migration_work_dir(migration)
            return _resolve_extracted_root(migration.target_dir)
        _remove_invalid_bundle_target(migration.target_dir)
    migration.target_dir.parent.mkdir(parents=True, exist_ok=True)

    if _try_fast_migration_rename(migration):
        source_parent = migration.source_dir.parent
        _remove_empty_legacy_parents(source_parent)
        return migration.target_dir.resolve()

    work_dir = _migration_work_dir(migration)
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = _migration_file_plan(migration.source_dir)
    total_files = len(plan)
    total_bytes = sum(size for _relative, size in plan)
    copied_files = 0
    copied_bytes = 0
    _write_migration_state(
        migration,
        total_files=total_files,
        total_bytes=total_bytes,
        completed_files=0,
        copied_bytes=0,
        current_file="",
    )

    for relative_path, size in plan:
        source = migration.source_dir / relative_path
        target = work_dir / relative_path
        current_file = relative_path.as_posix()
        if _migration_target_file_ready(source, target, size):
            copied_files += 1
            copied_bytes += size
            _emit_migration_progress(
                on_progress,
                migration,
                current_file=current_file,
                completed_files=copied_files,
                total_files=total_files,
                copied_bytes=copied_bytes,
                total_bytes=total_bytes,
            )
            _write_migration_state(
                migration,
                total_files=total_files,
                total_bytes=total_bytes,
                completed_files=copied_files,
                copied_bytes=copied_bytes,
                current_file=current_file,
            )
            continue

        _copy_file_resumable(source, target)
        copied_files += 1
        copied_bytes += size
        _emit_migration_progress(
            on_progress,
            migration,
            current_file=current_file,
            completed_files=copied_files,
            total_files=total_files,
            copied_bytes=copied_bytes,
            total_bytes=total_bytes,
        )
        _write_migration_state(
            migration,
            total_files=total_files,
            total_bytes=total_bytes,
            completed_files=copied_files,
            copied_bytes=copied_bytes,
            current_file=current_file,
        )

    _migration_state_path(work_dir).unlink(missing_ok=True)
    rename_with_retry(work_dir, migration.target_dir)
    source_parent = migration.source_dir.parent
    shutil.rmtree(migration.source_dir, ignore_errors=True)
    _remove_empty_legacy_parents(source_parent)
    return migration.target_dir.resolve()


def normalize_bundle_work_dir(path: Path | None, base_dir: Path) -> Path | None:
    """把旧内置整合包路径归一到已迁移后的短路径。"""

    if path is None:
        return None
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for entry in TTS_BUNDLES:
        target_dir = _short_bundle_install_dir(entry, base_dir)
        if not _is_installed_bundle_ready(target_dir):
            continue
        legacy_dir = _legacy_bundle_install_dir(entry, base_dir)
        try:
            resolved.relative_to(legacy_dir.resolve())
        except ValueError:
            continue
        return _resolve_extracted_root(target_dir)
    return path


def install_tts_bundle(
    entry: TTSBundleEntry,
    base_dir: Path,
    *,
    check_cancel: Callable[[], None] | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    on_download_progress: DownloadProgressCallback | None = None,
    urlopen: UrlOpenCallable = urllib.request.urlopen,
    extractor: Callable[[Path, Path], str | None] | None = None,
) -> TTSBundleInstallResult:
    if entry.install_method == "archive":
        work_dir = download_and_extract_bundle(
            entry,
            base_dir,
            check_cancel=check_cancel,
            on_progress=on_progress,
            on_status=on_status,
            on_download_progress=on_download_progress,
            urlopen=urlopen,
            extractor=extractor,
        )
        return TTSBundleInstallResult(work_dir=work_dir, provider=entry.provider)
    if entry.install_method == "script":
        return _run_script_bundle_installer(
            entry,
            base_dir,
            check_cancel=check_cancel,
            on_progress=on_progress,
            on_status=on_status,
        )
    raise RuntimeError(f"不支持的 TTS 整合包安装方式：{entry.install_method}")


def download_and_extract_bundle(
    entry: TTSBundleEntry,
    base_dir: Path,
    *,
    check_cancel: Callable[[], None] | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    on_download_progress: DownloadProgressCallback | None = None,
    urlopen: UrlOpenCallable = urllib.request.urlopen,
    extractor: Callable[[Path, Path], str | None] | None = None,
) -> Path:
    if entry.install_method != "archive":
        raise RuntimeError(f"{entry.label} 不是压缩包整合包，请使用安装器入口。")
    if not is_bundle_supported(entry):
        raise RuntimeError(
            f"{entry.label} 不支持当前平台：{current_platform_label()}；"
            f"支持平台：{format_supported_systems(entry.supported_systems)}。"
        )

    bundle_base = base_dir / "tts"
    downloads_dir = bundle_base / "_dl"
    tmp_dir = bundle_base / "_tmp" / entry.key
    installed_dir = _short_bundle_install_dir(entry, base_dir)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    archive = downloads_dir / entry.filename

    _emit_status(on_status, "verify")
    _emit_progress(on_progress, 0)
    if _archive_verification_error(archive, entry, on_progress=on_progress) is not None:
        _emit_status(on_status, "download")
        _download_archive(
            entry,
            archive,
            on_progress=on_progress,
            on_download_progress=on_download_progress,
            urlopen=urlopen,
            check_cancel=check_cancel,
        )
    _emit_progress(on_progress, _DOWNLOAD_PROGRESS_END)

    _emit_status(on_status, "extract")
    _reset_extract_dir(tmp_dir)
    extract = extractor or _extract_archive
    error = extract(archive, tmp_dir)
    if error is not None:
        raise RuntimeError(f"解压 TTS 整合包失败：{error}")
    work_dir = _replace_installed_bundle_from_extract(tmp_dir, installed_dir)
    _emit_status(on_status, "cleanup")
    _cleanup_archive(archive)
    _emit_progress(on_progress, 100)
    return work_dir


def cleanup_stale_download_archives(base_dir: Path) -> list[Path]:
    """清理旧版本解压成功后遗留在下载目录里的整合包压缩包。"""

    cleaned: list[Path] = []
    for entry in TTS_BUNDLES:
        if entry.install_method != "archive" or not entry.filename:
            continue
        if not _is_bundle_installed(entry, base_dir):
            continue
        for downloads_dir in (base_dir / "tts" / "_dl", StoragePaths(base_dir).tts_bundles_downloads_dir):
            archive = downloads_dir / entry.filename
            if not archive.is_file():
                continue
            _cleanup_archive(archive)
            cleaned.append(archive)
    return cleaned


def _run_script_bundle_installer(
    entry: TTSBundleEntry,
    base_dir: Path,
    *,
    check_cancel: Callable[[], None] | None,
    on_progress: ProgressCallback | None,
    on_status: StatusCallback | None,
) -> TTSBundleInstallResult:
    if not is_bundle_supported(entry):
        raise RuntimeError(
            f"{entry.label} 不支持当前平台：{current_platform_label()}；"
            f"支持平台：{format_supported_systems(entry.supported_systems)}。"
        )
    if not entry.installer_script:
        raise RuntimeError(f"{entry.label} 缺少安装脚本。")

    script = _resolve_installer_script(entry.installer_script, base_dir)
    bundle_base = StoragePaths(base_dir).tts_bundles_dir
    installed_dir = bundle_base / "installed" / entry.key
    install_tmp_dir = bundle_base / "tmp" / entry.key
    downloads_dir = bundle_base / "downloads"
    if install_tmp_dir.exists():
        shutil.rmtree(install_tmp_dir, ignore_errors=True)
    install_tmp_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    _emit_status(on_status, "install")
    _emit_progress(on_progress, 0)
    env = os.environ.copy()
    env["SAKURA_TTS_INSTALL_DIR"] = str(install_tmp_dir)
    env["SAKURA_TTS_DOWNLOADS_DIR"] = str(downloads_dir)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = ["bash", str(script), str(install_tmp_dir)]
    popen_kwargs: dict[str, Any] = {
        "args": cmd,
        "cwd": str(base_dir),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = _WIN_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True

    process: subprocess.Popen[str] | None = None
    tail: list[str] = []
    try:
        process = subprocess.Popen(**popen_kwargs)
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line:
                    tail.append(line)
                    tail = tail[-20:]
                    _handle_installer_progress_line(line, on_progress=on_progress, on_status=on_status)
                if check_cancel is not None:
                    check_cancel()
        except Exception:
            _terminate_process_tree(process)
            raise
        finally:
            process.stdout.close()

        return_code = process.wait()
        if return_code != 0:
            detail = "\n".join(tail) or f"exit {return_code}"
            raise RuntimeError(f"{entry.label} 安装失败：\n{detail}"[:2000])

        _build_script_install_result(entry, install_tmp_dir)
        _replace_installed_bundle_dir(install_tmp_dir, installed_dir)
        result = _build_script_install_result(entry, installed_dir)
        _emit_status(on_status, "cleanup")
        _emit_progress(on_progress, 100)
        return result
    except Exception:
        if process is not None:
            _terminate_process_tree(process)
        shutil.rmtree(install_tmp_dir, ignore_errors=True)
        raise


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()
    process.wait()


def _replace_installed_bundle_dir(source_dir: Path, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = target_dir.with_name(f".{target_dir.name}.previous")
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    had_previous = target_dir.exists()
    if had_previous:
        rename_with_retry(target_dir, backup_dir)
    try:
        shutil.move(str(source_dir), str(target_dir))
    except Exception:
        if had_previous and backup_dir.exists() and not target_dir.exists():
            rename_with_retry(backup_dir, target_dir)
        raise
    if had_previous:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _resolve_installer_script(relative_script: str, base_dir: Path) -> Path:
    candidates = [base_dir / relative_script, _project_root() / relative_script]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"未找到 TTS 整合包安装脚本：{relative_script}")


def _handle_installer_progress_line(
    line: str,
    *,
    on_progress: ProgressCallback | None,
    on_status: StatusCallback | None,
) -> None:
    match = re.search(r"::sakura-progress\s+status=([a-z_]+)\s+progress=(\d+)", line)
    if match is None:
        return
    _emit_status(on_status, match.group(1))
    _emit_progress(on_progress, int(match.group(2)))


def _build_script_install_result(entry: TTSBundleEntry, installed_dir: Path) -> TTSBundleInstallResult:
    work_dir = installed_dir / entry.work_dir_name if entry.work_dir_name else installed_dir
    python_path = installed_dir / entry.python_path_name if entry.python_path_name else None
    tts_config_path = installed_dir / entry.tts_config_path_name if entry.tts_config_path_name else None
    if not work_dir.is_dir():
        raise RuntimeError(f"{entry.label} 安装后未找到工作目录：{work_dir}")
    if not (work_dir / "api_v2.py").is_file():
        raise RuntimeError(f"{entry.label} 安装后未找到 api_v2.py：{work_dir}")
    if python_path is not None and not python_path.is_file():
        raise RuntimeError(f"{entry.label} 安装后未找到 Python：{python_path}")
    if tts_config_path is not None and not tts_config_path.is_file():
        raise RuntimeError(f"{entry.label} 安装后未找到推理配置：{tts_config_path}")
    return TTSBundleInstallResult(
        work_dir=work_dir.resolve(),
        provider=entry.provider,
        python_path=python_path.resolve() if python_path is not None else None,
        tts_config_path=tts_config_path.resolve() if tts_config_path is not None else None,
    )


def _entries_for_provider(provider: str | None) -> tuple[TTSBundleEntry, ...]:
    if provider is None:
        return tuple(entry for entry in TTS_BUNDLES if entry.install_method == "archive")
    normalized = provider.strip().lower().replace("_", "-")
    if normalized in {"genie", "genie-tts", "genietts"}:
        return (GENIE_TTS,)
    if normalized in {"gpt-sovits", "gpt-so-vits", "gptsovits"}:
        return GPT_SOVITS_BUNDLES
    return ()


def _short_bundle_install_dir(entry: TTSBundleEntry, base_dir: Path) -> Path:
    short_name = _SHORT_BUNDLE_DIRS.get(entry.key, entry.key)
    return base_dir / "tts" / short_name


def _legacy_bundle_install_dir(entry: TTSBundleEntry, base_dir: Path) -> Path:
    return StoragePaths(base_dir).tts_bundle_installed_for(entry.key)


def _is_bundle_installed(entry: TTSBundleEntry, base_dir: Path) -> bool:
    return (
        _is_installed_bundle_ready(_short_bundle_install_dir(entry, base_dir))
        or _is_installed_bundle_ready(_legacy_bundle_install_dir(entry, base_dir))
    )


def _replace_installed_bundle_from_extract(tmp_dir: Path, installed_dir: Path) -> Path:
    root = _resolve_extracted_root(tmp_dir)
    if installed_dir.exists():
        shutil.rmtree(installed_dir, ignore_errors=True)
    installed_dir.parent.mkdir(parents=True, exist_ok=True)

    if root == tmp_dir.resolve():
        installed_dir.mkdir(parents=True, exist_ok=True)
        for child in list(tmp_dir.iterdir()):
            shutil.move(str(child), str(installed_dir / child.name))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return installed_dir.resolve()

    shutil.move(str(root), str(installed_dir))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return installed_dir.resolve()


def _migration_work_dir(migration: TTSBundleMigration) -> Path:
    return migration.target_dir.parent / _MIGRATING_DIR_NAME / migration.entry.key


def _migration_state_path(work_dir: Path) -> Path:
    return work_dir / _MIGRATION_STATE_FILE


def _cleanup_migration_work_dir(migration: TTSBundleMigration) -> None:
    work_dir = _migration_work_dir(migration)
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)


def _remove_invalid_bundle_target(target_dir: Path) -> None:
    """移除 Sakura 管理的无效短目录，避免阻塞旧整合包迁移。"""

    if target_dir.is_dir() and not target_dir.is_symlink():
        shutil.rmtree(target_dir)
        return
    target_dir.unlink(missing_ok=True)


def _try_fast_migration_rename(migration: TTSBundleMigration) -> bool:
    """同盘且没有续迁目录时优先尝试目录改名，失败则交给可恢复复制。"""

    if _migration_work_dir(migration).exists():
        return False
    try:
        if migration.source_dir.resolve().anchor != migration.target_dir.resolve().anchor:
            return False
        rename_with_retry(migration.source_dir, migration.target_dir)
    except OSError:
        return False
    _cleanup_migration_work_dir(migration)
    return True


def _migration_file_plan(source_dir: Path) -> list[tuple[Path, int]]:
    plan: list[tuple[Path, int]] = []
    for path in sorted(source_dir.rglob("*"), key=lambda item: item.relative_to(source_dir).as_posix()):
        if not path.is_file():
            continue
        if path.name == _MIGRATION_STATE_FILE or path.name.endswith(_MIGRATION_TEMP_SUFFIX):
            continue
        relative_path = path.relative_to(source_dir)
        plan.append((relative_path, path.stat().st_size))
    return plan


def _write_migration_state(
    migration: TTSBundleMigration,
    *,
    total_files: int,
    total_bytes: int,
    completed_files: int,
    copied_bytes: int,
    current_file: str,
) -> None:
    work_dir = _migration_work_dir(migration)
    work_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "entry_key": migration.entry.key,
        "source_dir": str(migration.source_dir),
        "target_dir": str(migration.target_dir),
        "work_dir": str(work_dir),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "completed_files": completed_files,
        "copied_bytes": copied_bytes,
        "current_file": current_file,
        "updated_at": time.time(),
    }
    _migration_state_path(work_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _migration_target_file_ready(source: Path, target: Path, expected_size: int) -> bool:
    if not target.is_file():
        return False
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except OSError:
        return False
    if target_stat.st_size != expected_size or target_stat.st_size != source_stat.st_size:
        return False
    # Windows/FAT/压缩包解压后的 mtime 精度可能有误差，大小一致即可续迁跳过。
    return True


def _copy_file_resumable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_name(f"{target.name}{_MIGRATION_TEMP_SUFFIX}")
    tmp_target.unlink(missing_ok=True)
    try:
        with source.open("rb") as src, tmp_target.open("wb") as dst:
            while True:
                chunk = src.read(_MIGRATION_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                dst.write(chunk)
                time.sleep(0)
        shutil.copystat(source, tmp_target)
        replace_with_retry(tmp_target, target)
    except Exception:
        tmp_target.unlink(missing_ok=True)
        raise


def _emit_migration_progress(
    callback: MigrationProgressCallback | None,
    migration: TTSBundleMigration,
    *,
    current_file: str,
    completed_files: int,
    total_files: int,
    copied_bytes: int,
    total_bytes: int,
) -> None:
    if callback is None:
        return
    callback(
        TTSBundleMigrationProgress(
            entry=migration.entry,
            current_file=current_file,
            completed_files=completed_files,
            total_files=total_files,
            copied_bytes=copied_bytes,
            total_bytes=total_bytes,
        )
    )


def _remove_empty_legacy_parents(path: Path) -> None:
    tts_bundles_dir: Path | None = None
    for parent in path.parents:
        if parent.name == "tts_bundles":
            tts_bundles_dir = parent
            break
    if tts_bundles_dir is None:
        return

    current = path
    while True:
        try:
            current.rmdir()
        except OSError:
            break
        if current == tts_bundles_dir:
            return
        current = current.parent

    # 迁移后只清理旧整合包运行时相关的空目录；ONNX 属于用户生成数据，保留在 data 下。
    for candidate in (
        tts_bundles_dir / "downloads",
        tts_bundles_dir / "installed",
        tts_bundles_dir,
    ):
        try:
            candidate.rmdir()
        except OSError:
            continue


def _is_rtx_50_series(name: str) -> bool:
    return bool(re.search(r"\bRTX\s*50[0-9]{2}\b", name, re.IGNORECASE))


def _has_gpt_sovits_vram(gpu: GPUInfo) -> bool:
    # nvidia-smi 常把 6GB / 8GB 显卡报成 5.9x / 7.9x GB，这里保留误差余量避免误判成 CPU 包。
    return gpu.vram_gb + _GPT_SOVITS_VRAM_TOLERANCE_GB >= MIN_GPT_SOVITS_VRAM_GB


def _cleanup_archive(archive: Path) -> None:
    try:
        archive.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"TTS 整合包已解压，但清理下载压缩包失败：{exc}") from exc


def _is_installed_bundle_ready(installed_dir: Path) -> bool:
    if not installed_dir.is_dir():
        return False
    try:
        root = _resolve_extracted_root(installed_dir)
    except OSError:
        return False
    return find_usable_runtime_python(root / "runtime") is not None


def _emit_progress(callback: ProgressCallback | None, value: int) -> None:
    if callback is not None:
        callback(max(0, min(100, int(value))))


def _emit_status(callback: StatusCallback | None, value: str) -> None:
    if callback is not None:
        callback(value)


def _emit_download_progress(
    callback: DownloadProgressCallback | None,
    progress: TTSBundleDownloadProgress,
) -> None:
    if callback is not None:
        callback(progress)


def _download_percent(downloaded: int, total: int) -> int:
    if total <= 0:
        return _DOWNLOAD_PROGRESS_END
    downloaded = max(0, min(downloaded, total))
    return _VERIFY_PROGRESS_END + int(
        (_DOWNLOAD_PROGRESS_END - _VERIFY_PROGRESS_END) * downloaded / total
    )


def _archive_verification_error(
    archive: Path,
    entry: TTSBundleEntry,
    *,
    on_progress: ProgressCallback | None = None,
) -> str | None:
    if not archive.is_file():
        return "archive is missing"
    if archive.stat().st_size != entry.size:
        return "size mismatch"
    if _sha256_file(
        archive,
        expected_size=entry.size,
        on_progress=on_progress,
        progress_start=0,
        progress_end=_VERIFY_PROGRESS_END,
    ).lower() != entry.sha256.lower():
        return "sha256 mismatch"
    return None


def _download_archive(
    entry: TTSBundleEntry,
    archive: Path,
    *,
    on_progress: ProgressCallback | None,
    on_download_progress: DownloadProgressCallback | None,
    urlopen: UrlOpenCallable,
    check_cancel: Callable[[], None] | None = None,
) -> None:
    part = archive.with_name(f"{archive.name}.part")
    resume_from = part.stat().st_size if part.is_file() else 0
    if entry.size > 0 and resume_from > entry.size:
        part.unlink(missing_ok=True)
        resume_from = 0
    if resume_from == entry.size and entry.size > 0:
        actual_sha256 = _sha256_file(part)
        if actual_sha256.lower() == entry.sha256.lower():
            replace_with_retry(part, archive)
            return
        part.unlink(missing_ok=True)
        resume_from = 0

    headers = {"User-Agent": "Sakura-Desktop-Pet/1.0"}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(entry.download_url, headers=headers)

    transfer_start = time.monotonic()
    transfer_start_bytes = resume_from
    downloaded = resume_from
    resumed = resume_from > 0
    try:
        with urlopen(request, timeout=600) as response:  # type: ignore[attr-defined]
            status_code = _response_status_code(response)
            if resumed and status_code != 206:
                part.unlink(missing_ok=True)
                downloaded = 0
                transfer_start_bytes = 0
                resumed = False
            mode = "ab" if resumed else "wb"
            _emit_download_progress(
                on_download_progress,
                TTSBundleDownloadProgress(
                    status="download",
                    downloaded_bytes=downloaded,
                    total_bytes=entry.size,
                    percent=_download_percent(downloaded, entry.size),
                    resumed=resumed,
                ),
            )
            _emit_progress(on_progress, _download_percent(downloaded, entry.size))
            with part.open(mode) as file:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    file.write(chunk)
                    if check_cancel is not None:
                        check_cancel()
                    downloaded += len(chunk)
                    progress = _download_percent(downloaded, entry.size)
                    elapsed = max(0.001, time.monotonic() - transfer_start)
                    speed = max(0.0, (downloaded - transfer_start_bytes) / elapsed)
                    _emit_progress(on_progress, progress)
                    _emit_download_progress(
                        on_download_progress,
                        TTSBundleDownloadProgress(
                            status="download",
                            downloaded_bytes=downloaded,
                            total_bytes=entry.size,
                            percent=progress,
                            resumed=resumed,
                            bytes_per_second=speed,
                        ),
                    )
        if downloaded != entry.size:
            raise RuntimeError(f"文件大小不匹配：期望 {entry.size}，实际 {downloaded}")
        actual_sha256 = _sha256_file(part)
        if actual_sha256.lower() != entry.sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(f"SHA256 不匹配：期望 {entry.sha256}，实际 {actual_sha256}")
        replace_with_retry(part, archive)
    except DownloadCancelledError:
        raise
    except Exception:
        if part.is_file() and entry.size > 0 and part.stat().st_size >= entry.size:
            part.unlink()
        raise


def _response_status_code(response: object) -> int | None:
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        try:
            code = getcode()
        except Exception:
            return None
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None
    status = getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _sha256_file(
    path: Path,
    *,
    expected_size: int | None = None,
    on_progress: ProgressCallback | None = None,
    progress_start: int = 0,
    progress_end: int = 100,
) -> str:
    hasher = hashlib.sha256()
    total = expected_size if expected_size and expected_size > 0 else path.stat().st_size
    read_bytes = 0
    last_progress: int | None = None
    with path.open("rb") as file:
        while True:
            chunk = file.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            read_bytes += len(chunk)
            if total > 0:
                progress = progress_start + int((progress_end - progress_start) * read_bytes / total)
                if progress != last_progress:
                    _emit_progress(on_progress, progress)
                    last_progress = progress
            # 大文件哈希会持续数秒，主动让出执行权避免 Qt 前台窗口假死。
            time.sleep(0)
    _emit_progress(on_progress, progress_end)
    return hasher.hexdigest()


def _extract_archive(archive: Path, out_dir: Path) -> str | None:
    py7zz_error = _extract_with_py7zz(archive, out_dir)
    if py7zz_error is None:
        return None

    seven_zip = _seven_zip_exe()
    cli_error: str | None = None
    if seven_zip is not None:
        if py7zz_error != "missing":
            _reset_extract_dir(out_dir)
        cli_error = _extract_with_7zip(seven_zip, archive, out_dir)
        if cli_error is None:
            return None

    py7zr = _load_py7zr()
    if py7zr is None:
        return _format_extractor_missing_error(py7zz_error, cli_error)

    if cli_error is not None or py7zz_error != "missing":
        _reset_extract_dir(out_dir)
    try:
        _extract_with_py7zr(py7zr, archive, out_dir)
    except Exception as exc:
        return _format_py7zr_failure_error(py7zz_error, cli_error, exc)
    return None


def _extract_with_py7zz(archive: Path, out_dir: Path) -> str | None:
    try:
        py7zz = importlib.import_module("py7zz")
    except ImportError:
        return "missing"
    try:
        py7zz.extract_archive(str(archive), str(out_dir))
    except Exception as exc:
        return str(exc)[:2000]
    return None


def _load_py7zr() -> Any | None:
    """py7zr 是最后兜底，部分 BCJ2 压缩包仍需要 7-Zip CLI。"""
    try:
        return importlib.import_module("py7zr")
    except ImportError:
        return None


def _seven_zip_exe() -> Path | None:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for name in _SEVEN_ZIP_COMMANDS:
                bundled = Path(meipass) / "7za" / name
                if bundled.is_file():
                    return bundled

    project_root = _project_root()
    for name in _SEVEN_ZIP_COMMANDS:
        bundled = project_root / "build_exe" / name
        if bundled.is_file():
            return bundled

    for name in _SEVEN_ZIP_COMMANDS:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _extract_with_7zip(exe: Path, archive: Path, out_dir: Path) -> str | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    output_dir = str(out_dir.resolve())
    if not output_dir.endswith(("/", "\\")):
        output_dir += "\\" if sys.platform == "win32" else "/"
    cmd = [str(exe), "x", "-y", f"-o{output_dir}", str(archive)]
    kwargs: dict[str, object] = {
        "args": cmd,
        "capture_output": True,
        "text": True,
        "timeout": 3600,
    }
    if sys.platform == "win32" and _WIN_NO_WINDOW:
        kwargs["creationflags"] = _WIN_NO_WINDOW
    try:
        result = subprocess.run(**kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:2000]
    if result.returncode != 0:
        return (result.stderr or result.stdout or f"exit {result.returncode}")[:2000]
    return None


def _extract_with_py7zr(py7zr: Any, archive: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive, "r") as seven_zip:
        seven_zip.extractall(path=out_dir)


def _reset_extract_dir(out_dir: Path) -> None:
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)


def _format_extractor_missing_error(py7zz_error: str, cli_error: str | None) -> str:
    if cli_error is None:
        return (
            "未找到 py7zz、7-Zip CLI 或 py7zr，请先安装 py7zz、7-Zip 或 py7zr 后重试。"
            f"py7zz: {py7zz_error}"
        )[:2000]
    return f"py7zz: {py7zz_error}; 7-Zip CLI: {cli_error}; py7zr: missing"[:2000]


def _format_py7zr_failure_error(py7zz_error: str, cli_error: str | None, exc: Exception) -> str:
    cli_part = "missing" if cli_error is None else cli_error
    return (
        "需要 py7zz 或 7-Zip CLI 才能解压此压缩包；"
        f"py7zr 兜底解压失败：{exc}; py7zz: {py7zz_error}; 7-Zip CLI: {cli_part}"
    )[:2000]


def _resolve_extracted_root(extract_to: Path) -> Path:
    if (extract_to / "runtime" / "python.exe").is_file():
        return extract_to.resolve()
    children = [path for path in extract_to.iterdir() if not path.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0].resolve()
    return extract_to.resolve()
