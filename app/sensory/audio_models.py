from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from app.sensory.models import SensorySource


RECOMMENDED_LLAMA_CPP_SPEECH_MODEL = "ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0"
RECOMMENDED_LLAMA_CPP_SOUND_MODEL = "ggml-org/ultravox-v0_5-llama-3_2-1b-GGUF:Q4_K_M"


@dataclass(frozen=True)
class SensoryAudioModelRecommendation:
    source: SensorySource
    model: str
    download_hint: str
    estimated_download_bytes: int
    include_patterns: tuple[str, ...] = ()
    notes: str = ""


_RECOMMENDATIONS = {
    SensorySource.SPEECH: SensoryAudioModelRecommendation(
        source=SensorySource.SPEECH,
        model=RECOMMENDED_LLAMA_CPP_SPEECH_MODEL,
        download_hint="约 1.0 GB",
        estimated_download_bytes=1_019_141_728,
        include_patterns=(
            "Qwen3-ASR-0.6B-Q8_0.gguf",
            "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
        ),
        notes="Qwen3-ASR Q8_0 + mmproj",
    ),
    SensorySource.SOUND: SensoryAudioModelRecommendation(
        source=SensorySource.SOUND,
        model=RECOMMENDED_LLAMA_CPP_SOUND_MODEL,
        download_hint="约 2.1 GB",
        estimated_download_bytes=2_178_818_080,
        include_patterns=(
            "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
            "mmproj-*.gguf",
        ),
        notes="Ultravox Q4_K_M + mmproj",
    ),
}


def recommended_llama_cpp_audio_model(
    source: SensorySource,
) -> SensoryAudioModelRecommendation | None:
    return _RECOMMENDATIONS.get(source)


def sensory_audio_model_download_hint(model: str) -> str:
    normalized = model.strip()
    for recommendation in _RECOMMENDATIONS.values():
        if recommendation.model == normalized:
            return recommendation.download_hint
    return ""


def llama_cpp_audio_model_repo_id(model: str) -> str:
    normalized = str(model or "").strip()
    if ":" in normalized and "/" in normalized.split(":", 1)[0]:
        return normalized.split(":", 1)[0]
    return normalized


def llama_cpp_audio_cache_ready(path: Path, include_patterns: tuple[str, ...]) -> bool:
    patterns = tuple(pattern for pattern in include_patterns if str(pattern).strip())
    try:
        filenames = [file.name for file in Path(path).rglob("*.gguf") if file.is_file()]
    except OSError:
        return False
    if not filenames:
        return False
    if not patterns:
        return True
    return all(any(fnmatch.fnmatch(name, pattern) for name in filenames) for pattern in patterns)
