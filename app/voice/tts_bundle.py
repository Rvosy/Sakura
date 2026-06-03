from __future__ import annotations

import hashlib
import importlib
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.voice.runtime_compat import current_platform_label, current_system_name, find_usable_runtime_python


ProgressCallback = Callable[[int], None]
StatusCallback = Callable[[str], None]

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


def format_platform_summary() -> str:
    try:
        return platform.platform(aliased=True, terse=True)
    except Exception:
        return f"{platform.system()} {platform.release()}"


def list_nvidia_gpus() -> list[GPUInfo]:
    exe = shutil.which("nvidia-smi")
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

    installed_dir = base_dir / "data" / "tts_bundles" / "installed" / entry.key
    if entry.install_method == "script" and entry.work_dir_name:
        return installed_dir / entry.work_dir_name
    if installed_dir.is_dir():
        try:
            return _resolve_extracted_root(installed_dir)
        except OSError:
            pass
    return installed_dir / Path(entry.filename).stem


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
        installed_dir = base_dir / "data" / "tts_bundles" / "installed" / entry.key
        if _is_installed_bundle_ready(installed_dir):
            return default_bundle_work_dir(entry, base_dir)
    recommended = recommend_gpt_sovits_bundle()
    return default_bundle_work_dir(recommended, base_dir) if recommended is not None else None


def is_provider_bundle_work_dir(path: Path, base_dir: Path) -> bool:
    """判断路径是否位于内置 TTS 整合包安装目录下。"""

    try:
        path.resolve().relative_to((base_dir / "data" / "tts_bundles" / "installed").resolve())
    except ValueError:
        return False
    return True


def install_tts_bundle(
    entry: TTSBundleEntry,
    base_dir: Path,
    *,
    check_cancel: Callable[[], None] | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
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

    bundle_base = base_dir / "data" / "tts_bundles"
    downloads_dir = bundle_base / "downloads"
    installed_dir = bundle_base / "installed" / entry.key
    downloads_dir.mkdir(parents=True, exist_ok=True)
    archive = downloads_dir / entry.filename

    _emit_status(on_status, "verify")
    _emit_progress(on_progress, 0)
    if _archive_verification_error(archive, entry, on_progress=on_progress) is not None:
        _emit_status(on_status, "download")
        _download_archive(entry, archive, on_progress=on_progress, urlopen=urlopen, check_cancel=check_cancel)
    _emit_progress(on_progress, _DOWNLOAD_PROGRESS_END)

    _emit_status(on_status, "extract")
    if installed_dir.exists():
        shutil.rmtree(installed_dir, ignore_errors=True)
    installed_dir.mkdir(parents=True, exist_ok=True)
    extract = extractor or _extract_archive
    error = extract(archive, installed_dir)
    if error is not None:
        raise RuntimeError(f"解压 TTS 整合包失败：{error}")
    _emit_status(on_status, "cleanup")
    _cleanup_archive(archive)
    _emit_progress(on_progress, 100)
    return _resolve_extracted_root(installed_dir)


def cleanup_stale_download_archives(base_dir: Path) -> list[Path]:
    """清理旧版本解压成功后遗留在下载目录里的整合包压缩包。"""
    bundle_base = base_dir / "data" / "tts_bundles"
    downloads_dir = bundle_base / "downloads"
    installed_base = bundle_base / "installed"
    if not downloads_dir.is_dir():
        return []

    cleaned: list[Path] = []
    for entry in TTS_BUNDLES:
        if entry.install_method != "archive" or not entry.filename:
            continue
        archive = downloads_dir / entry.filename
        installed_dir = installed_base / entry.key
        if not archive.is_file() or not _is_installed_bundle_ready(installed_dir):
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
    installed_dir = base_dir / "data" / "tts_bundles" / "installed" / entry.key
    downloads_dir = base_dir / "data" / "tts_bundles" / "downloads"
    installed_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    _emit_status(on_status, "install")
    _emit_progress(on_progress, 0)
    env = os.environ.copy()
    env["SAKURA_TTS_INSTALL_DIR"] = str(installed_dir)
    env["SAKURA_TTS_DOWNLOADS_DIR"] = str(downloads_dir)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = ["bash", str(script), str(installed_dir)]
    process = subprocess.Popen(
        cmd,
        cwd=str(base_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    assert process.stdout is not None
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                tail.append(line)
                tail = tail[-20:]
                _handle_installer_progress_line(line, on_progress=on_progress, on_status=on_status)
            if check_cancel is not None:
                try:
                    check_cancel()
                except Exception:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise
    finally:
        process.stdout.close()
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(tail) or f"exit {return_code}"
        raise RuntimeError(f"{entry.label} 安装失败：\n{detail}"[:2000])

    result = _build_script_install_result(entry, installed_dir)
    _emit_status(on_status, "cleanup")
    _emit_progress(on_progress, 100)
    return result


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
    urlopen: UrlOpenCallable,
    check_cancel: Callable[[], None] | None = None,
) -> None:
    part = archive.with_name(f"{archive.name}.part")
    if part.exists():
        part.unlink()
    request = urllib.request.Request(
        entry.download_url,
        headers={"User-Agent": "Sakura-Desktop-Pet/1.0"},
    )
    hasher = hashlib.sha256()
    downloaded = 0
    try:
        with urlopen(request, timeout=600) as response:  # type: ignore[attr-defined]
            with part.open("wb") as file:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    file.write(chunk)
                    hasher.update(chunk)
                    if check_cancel is not None:
                        check_cancel()
                    downloaded += len(chunk)
                    _emit_progress(
                        on_progress,
                        _VERIFY_PROGRESS_END
                        + int((_DOWNLOAD_PROGRESS_END - _VERIFY_PROGRESS_END) * downloaded / entry.size),
                    )
        if downloaded != entry.size:
            raise RuntimeError(f"文件大小不匹配：期望 {entry.size}，实际 {downloaded}")
        actual_sha256 = hasher.hexdigest()
        if actual_sha256.lower() != entry.sha256.lower():
            raise RuntimeError(f"SHA256 不匹配：期望 {entry.sha256}，实际 {actual_sha256}")
        part.replace(archive)
    except Exception:
        if part.exists():
            part.unlink()
        raise


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
    children = [path for path in extract_to.iterdir() if not path.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0].resolve()
    return extract_to.resolve()
