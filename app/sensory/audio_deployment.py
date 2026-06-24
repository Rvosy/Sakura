from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sensory.audio_models import (
    llama_cpp_audio_cache_ready,
    llama_cpp_audio_model_repo_id,
    recommended_llama_cpp_audio_model,
)
from app.sensory.audio_model_manifest import copy_llama_cpp_audio_model_from_manifest
from app.sensory.huggingface import (
    HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    download_huggingface_model,
)
from app.sensory.disk_space import build_disk_space_check, format_bytes
from app.sensory.llama_cpp_runtime import (
    LlamaCppRuntimeError,
    discover_llama_server_binary,
    fetch_llama_cpp_runtime_package_catalog,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    llama_cpp_runtime_archive_filename,
    llama_cpp_runtime_install_required_bytes,
    select_llama_cpp_runtime_package,
)
from app.sensory.models import SensorySource
from app.storage.paths import StoragePaths


def ensure_llama_cpp_runtime(
    base_dir: Path,
    *,
    download_runtime: bool = True,
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    existing = discover_llama_server_binary(base_dir)
    if existing:
        return {
            "binary_path": existing,
            "install_dir": str(Path(existing).parent),
            "already_installed": True,
            "platform_key": llama_cpp_platform_key(),
            "message": "已找到可用的 llama-server。",
        }
    if not download_runtime:
        raise RuntimeError("未找到可用的 llama-server；确认后才能下载或安装 llama.cpp 运行时。")
    catalog = fetch_llama_cpp_runtime_package_catalog(
        base_dir=base_dir,
        timeout_seconds=30,
    )
    package = select_llama_cpp_runtime_package(catalog.packages)
    result = install_llama_cpp_runtime_package(
        base_dir,
        package,
        timeout_seconds=timeout_seconds,
    )
    payload = result.to_mapping()
    payload["platform_key"] = llama_cpp_platform_key()
    payload["package_source"] = catalog.source
    return payload


def prepare_llama_cpp_audio_backend(
    base_dir: Path,
    source: SensorySource,
    *,
    download_runtime: bool = True,
    download_model: bool,
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if source not in {SensorySource.SPEECH, SensorySource.SOUND}:
        raise RuntimeError("llama.cpp 一键准备仅适用于语音和声音事件。")
    recommendation = recommended_llama_cpp_audio_model(source)
    if recommendation is None:
        raise RuntimeError(f"{source.value} 没有内置推荐 llama.cpp 音频模型。")
    runtime_payload = ensure_llama_cpp_runtime(
        Path(base_dir),
        download_runtime=download_runtime,
        timeout_seconds=timeout_seconds,
    )
    repo_id = llama_cpp_audio_model_repo_id(recommendation.model)
    local_dir = StoragePaths(base_dir).sensory_model_cache_for(source.value, repo_id)
    cached_before = llama_cpp_audio_cache_ready(local_dir, recommendation.include_patterns)
    download_result: dict[str, object] = {}
    disk_space = build_disk_space_check(
        local_dir,
        0 if cached_before else recommendation.estimated_download_bytes,
    )
    if not cached_before:
        if not download_model:
            raise RuntimeError(
                f"推荐模型 {recommendation.model} 尚未缓存；确认后才能下载 {recommendation.download_hint}。"
            )
        if not bool(disk_space.get("ok", True)):
            available = format_bytes(int(disk_space.get("available_bytes") or 0))
            needed = format_bytes(int(disk_space.get("needed_bytes") or 0))
            raise RuntimeError(f"磁盘空间不足，无法下载推荐模型：需要 {needed}，可用 {available}。")
        download_result = copy_llama_cpp_audio_model_from_manifest(
            Path(base_dir),
            source=source,
            repo_id=repo_id,
            include_patterns=recommendation.include_patterns,
            local_dir=local_dir,
        )
        if not download_result:
            download_result = download_huggingface_model(
                repo_id,
                local_dir,
                include_patterns=recommendation.include_patterns,
                timeout_seconds=timeout_seconds,
            )
    gguf_count = _gguf_count(local_dir)
    model_payload: dict[str, object] = {
        "repo_id": repo_id,
        "model": recommendation.model,
        "local_dir": str(local_dir),
        "download_hint": recommendation.download_hint,
        "estimated_download_bytes": recommendation.estimated_download_bytes,
        "include_patterns": list(recommendation.include_patterns),
        "cached_before": cached_before,
        "downloaded": not cached_before,
        "gguf_count": gguf_count,
        "disk_space": disk_space,
    }
    if download_result:
        model_payload["download_message"] = str(download_result.get("message") or "")
    if not llama_cpp_audio_cache_ready(local_dir, recommendation.include_patterns):
        raise RuntimeError(
            f"推荐模型 {recommendation.model} 下载后未找到 GGUF 文件，请检查 Hugging Face 仓库文件或 include patterns。"
        )
    return {
        "ok": True,
        "source": source.value,
        "runtime": runtime_payload,
        "model": model_payload,
        "message": "llama.cpp 音频后端已准备好。",
    }


def build_llama_cpp_runtime_download_preflight(
    base_dir: Path,
    *,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    root = Path(base_dir)
    existing = discover_llama_server_binary(root)
    platform_key = llama_cpp_platform_key()
    if existing:
        return {
            "required": False,
            "ok": True,
            "platform_key": platform_key,
            "binary_path": existing,
            "message": "已找到可用的 llama-server。",
        }
    try:
        catalog = fetch_llama_cpp_runtime_package_catalog(
            base_dir=root,
            timeout_seconds=timeout_seconds,
        )
        package = select_llama_cpp_runtime_package(catalog.packages)
    except (LlamaCppRuntimeError, OSError) as exc:
        return {
            "required": True,
            "ok": False,
            "platform_key": platform_key,
            "error": str(exc),
            "message": f"无法确认当前平台的 llama.cpp 运行时包：{exc}",
        }
    archive_path = (
        StoragePaths(root).llama_cpp_runtime_dir
        / "_downloads"
        / llama_cpp_runtime_archive_filename(package)
    )
    required_bytes = llama_cpp_runtime_install_required_bytes(package)
    disk_space = build_disk_space_check(archive_path, required_bytes)
    download_bytes = int(package.size_bytes or 0)
    download_hint = format_bytes(download_bytes) if download_bytes > 0 else "大小未知"
    return {
        "required": True,
        "ok": bool(disk_space.get("ok", True)),
        "platform_key": platform_key,
        "package_source": catalog.source,
        "package": package.to_mapping(),
        "archive_path": str(archive_path),
        "estimated_download_bytes": download_bytes,
        "estimated_required_bytes": required_bytes,
        "download_hint": download_hint,
        "disk_space": disk_space,
        "message": f"将下载 {package.label}（{download_hint}）。",
    }


def build_llama_cpp_audio_prepare_requirement(
    report: dict[str, Any],
    source: SensorySource,
    *,
    runtime_preflight: dict[str, Any] | None = None,
) -> dict[str, object]:
    runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    model_cache = report.get("model_cache") if isinstance(report.get("model_cache"), dict) else {}
    cache_state = model_cache.get(source.value) if isinstance(model_cache, dict) else {}
    needs_runtime_download = not bool(runtime.get("binary_found")) if isinstance(runtime, dict) else True
    needs_model_download = not bool(cache_state.get("ready")) if isinstance(cache_state, dict) else True
    disk_space = cache_state.get("disk_space") if isinstance(cache_state, dict) else {}
    model_manifest = cache_state.get("model_manifest") if isinstance(cache_state, dict) else {}
    actions: list[str] = []
    if needs_runtime_download:
        if isinstance(runtime_preflight, dict) and runtime_preflight:
            if runtime_preflight.get("error"):
                actions.append(str(runtime_preflight.get("message") or "无法确认当前平台的 llama.cpp 运行时包。"))
            else:
                actions.append(str(runtime_preflight.get("message") or "需要下载或配置 llama.cpp 运行时。"))
            runtime_disk = runtime_preflight.get("disk_space")
            if isinstance(runtime_disk, dict) and not bool(runtime_disk.get("ok", True)):
                actions.append(
                    "运行时磁盘空间不足："
                    f"需要 {format_bytes(int(runtime_disk.get('needed_bytes') or 0))}，"
                    f"可用 {format_bytes(int(runtime_disk.get('available_bytes') or 0))}。"
                )
        else:
            actions.append("需要下载或配置 llama.cpp 运行时。")
    if needs_model_download:
        if isinstance(model_manifest, dict) and model_manifest:
            actions.append(f"可从本地音频模型 manifest 安装 {source.value} 推荐 GGUF 模型。")
        else:
            actions.append(f"需要下载 {source.value} 推荐 GGUF 模型。")
    if isinstance(disk_space, dict) and not bool(disk_space.get("ok", True)):
        actions.append(
            "磁盘空间不足："
            f"需要 {format_bytes(int(disk_space.get('needed_bytes') or 0))}，"
            f"可用 {format_bytes(int(disk_space.get('available_bytes') or 0))}。"
        )
    return {
        "source": source.value,
        "ok": not needs_runtime_download
        and not needs_model_download
        and not (isinstance(disk_space, dict) and not bool(disk_space.get("ok", True))),
        "needs_runtime_download": needs_runtime_download,
        "needs_model_download": needs_model_download,
        "runtime_preflight": runtime_preflight if isinstance(runtime_preflight, dict) else {},
        "model_manifest": model_manifest if isinstance(model_manifest, dict) else {},
        "disk_space": disk_space if isinstance(disk_space, dict) else {},
        "actions": actions,
    }


def _gguf_count(path: Path) -> int:
    try:
        return len(list(Path(path).rglob("*.gguf"))) if Path(path).is_dir() else 0
    except OSError:
        return 0
