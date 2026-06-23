from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.sensory.audio_models import (
    llama_cpp_audio_cache_ready,
    llama_cpp_audio_model_repo_id,
    recommended_llama_cpp_audio_model,
)
from app.sensory.audio_smoke import build_sensory_audio_smoke_plan
from app.sensory.llama_cpp_runtime import (
    DEFAULT_LLAMA_CPP_MANAGED_PORT,
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    discover_llama_server_binary,
    llama_cpp_platform_key,
    llama_cpp_runtime_manifest_paths,
    llama_cpp_runtime_packages_from_manifest,
)
from app.sensory.huggingface import hf_cli_path
from app.sensory.models import SensoryProviderMode, SensorySource
from app.sensory.settings import SensoryProviderConfig
from app.storage.paths import StoragePaths


def build_sensory_audio_runtime_doctor_report(base_dir: Path) -> dict[str, Any]:
    """Summarize local audio runtime readiness without network or side effects."""

    root = Path(base_dir)
    binary_path = discover_llama_server_binary(root)
    manifest_candidates = _manifest_candidates(root)
    model_cache = {
        source.value: _model_cache_state(root, source)
        for source in (SensorySource.SPEECH, SensorySource.SOUND)
    }
    hf_path = hf_cli_path()
    plans = {
        source.value: build_sensory_audio_smoke_plan(
            _managed_llama_default_config(source, model_cache[source.value]),
            base_dir=root,
            source=source,
        ).to_mapping()
        for source in (SensorySource.SPEECH, SensorySource.SOUND)
    }
    ready_for_smoke = all(bool(plan["ok"]) for plan in plans.values())
    return {
        "ok": True,
        "platform_key": llama_cpp_platform_key(),
        "runtime": {
            "binary_found": bool(binary_path),
            "binary_path": binary_path,
            "manifest_candidates": manifest_candidates,
        },
        "huggingface": {
            "hf_cli_found": bool(hf_path),
            "hf_cli_path": hf_path,
            "builtin_file_download_supported": True,
            "manual_repository_download_requires_hf_cli": not bool(hf_path),
        },
        "model_cache": model_cache,
        "plans": plans,
        "ready_for_smoke": ready_for_smoke,
        "next_actions": _next_actions(
            binary_path=binary_path,
            hf_cli_found=bool(hf_path),
            manifest_candidates=manifest_candidates,
            plans=plans,
            model_cache=model_cache,
        ),
    }


def _managed_llama_default_config(
    source: SensorySource,
    cache_state: dict[str, Any] | None = None,
) -> SensoryProviderConfig:
    recommendation = recommended_llama_cpp_audio_model(source)
    model = str((cache_state or {}).get("path") or "").strip()
    if not model:
        model = recommendation.model if recommendation is not None else ""
    return SensoryProviderConfig(
        provider_id=f"{source.value}_local",
        source=source,
        mode=SensoryProviderMode.LOCAL,
        endpoint=f"http://127.0.0.1:{DEFAULT_LLAMA_CPP_MANAGED_PORT}/v1",
        model=model,
        extra={
            "backend": "llama",
            "managed_runtime": LLAMA_CPP_MANAGED_RUNTIME_MARKER,
        },
    ).normalized()


def _model_cache_state(base_dir: Path, source: SensorySource) -> dict[str, Any]:
    recommendation = recommended_llama_cpp_audio_model(source)
    repo_id = llama_cpp_audio_model_repo_id(recommendation.model) if recommendation is not None else ""
    path = StoragePaths(base_dir).sensory_model_cache_for(source.value, repo_id) if repo_id else Path()
    gguf_count = 0
    exists = False
    if repo_id:
        try:
            exists = path.is_dir()
            gguf_count = len(list(path.rglob("*.gguf"))) if exists else 0
        except OSError:
            exists = False
            gguf_count = 0
    ready = (
        exists
        and gguf_count > 0
        and llama_cpp_audio_cache_ready(path, recommendation.include_patterns if recommendation else ())
    )
    return {
        "repo_id": repo_id,
        "path": str(path) if ready else "",
        "candidate_path": str(path) if repo_id else "",
        "exists": exists,
        "gguf_count": gguf_count,
        "include_patterns": list(recommendation.include_patterns) if recommendation else [],
        "ready": ready,
        "used_for_plan": ready,
    }


def _manifest_candidates(base_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in llama_cpp_runtime_manifest_paths(base_dir):
        exists = path.is_file()
        entry: dict[str, Any] = {
            "path": str(path),
            "exists": exists,
            "package_count": 0,
            "platforms": [],
        }
        if exists:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                packages = llama_cpp_runtime_packages_from_manifest(
                    payload if isinstance(payload, dict) else {}
                )
            except (OSError, json.JSONDecodeError):
                packages = []
            entry["package_count"] = len(packages)
            entry["platforms"] = sorted(
                {package.normalized().platform_key for package in packages}
            )
        candidates.append(entry)
    return candidates


def _next_actions(
    *,
    binary_path: str,
    hf_cli_found: bool,
    manifest_candidates: list[dict[str, Any]],
    plans: dict[str, dict[str, object]],
    model_cache: dict[str, dict[str, Any]],
) -> list[str]:
    actions: list[str] = []
    if not binary_path:
        actions.append("运行 prepare-backend --source speech --yes 准备 llama.cpp 音频后端，或设置 SAKURA_LLAMA_SERVER。")
    if not any(bool(candidate["exists"]) for candidate in manifest_candidates):
        actions.append("发布包可生成 runtime_manifest.json 固定 llama.cpp 下载源。")
    for source, plan in plans.items():
        cache_state = model_cache.get(source, {})
        if bool(plan.get("requires_model_download")) and not bool(cache_state.get("ready")):
            hint = str(plan.get("model_download_hint") or "模型大小取决于仓库")
            actions.append(f"{source} 首次真实 smoke 需要确认 GGUF 模型下载：{hint}。")
    if not hf_cli_found and any(not bool(state.get("ready")) for state in model_cache.values()):
        actions.append("未安装 Hugging Face CLI `hf`；推荐 GGUF 文件会使用内置直连下载，手动下载任意仓库仍需 `hf`。")
    return actions
