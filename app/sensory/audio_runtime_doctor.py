from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.sensory.audio_models import recommended_llama_cpp_audio_model
from app.sensory.audio_smoke import build_sensory_audio_smoke_plan
from app.sensory.llama_cpp_runtime import (
    DEFAULT_LLAMA_CPP_MANAGED_PORT,
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    discover_llama_server_binary,
    llama_cpp_platform_key,
    llama_cpp_runtime_manifest_paths,
    llama_cpp_runtime_packages_from_manifest,
)
from app.sensory.models import SensoryProviderMode, SensorySource
from app.sensory.settings import SensoryProviderConfig


def build_sensory_audio_runtime_doctor_report(base_dir: Path) -> dict[str, Any]:
    """Summarize local audio runtime readiness without network or side effects."""

    root = Path(base_dir)
    binary_path = discover_llama_server_binary(root)
    manifest_candidates = _manifest_candidates(root)
    plans = {
        source.value: build_sensory_audio_smoke_plan(
            _managed_llama_default_config(source),
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
        "plans": plans,
        "ready_for_smoke": ready_for_smoke,
        "next_actions": _next_actions(
            binary_path=binary_path,
            manifest_candidates=manifest_candidates,
            plans=plans,
        ),
    }


def _managed_llama_default_config(source: SensorySource) -> SensoryProviderConfig:
    recommendation = recommended_llama_cpp_audio_model(source)
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
    manifest_candidates: list[dict[str, Any]],
    plans: dict[str, dict[str, object]],
) -> list[str]:
    actions: list[str] = []
    if not binary_path:
        actions.append("运行 install-runtime --yes 配置 llama.cpp 运行时，或设置 SAKURA_LLAMA_SERVER。")
    if not any(bool(candidate["exists"]) for candidate in manifest_candidates):
        actions.append("发布包可生成 runtime_manifest.json 固定 llama.cpp 下载源。")
    for source, plan in plans.items():
        if bool(plan.get("requires_model_download")):
            hint = str(plan.get("model_download_hint") or "模型大小取决于仓库")
            actions.append(f"{source} 首次真实 smoke 需要确认 GGUF 模型下载：{hint}。")
    return actions
