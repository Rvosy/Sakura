from __future__ import annotations

from dataclasses import dataclass

from app.sensory.models import SensorySource


RECOMMENDED_LLAMA_CPP_SPEECH_MODEL = "ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0"
RECOMMENDED_LLAMA_CPP_SOUND_MODEL = "ggml-org/ultravox-v0_5-llama-3_2-1b-GGUF:Q4_K_M"


@dataclass(frozen=True)
class SensoryAudioModelRecommendation:
    source: SensorySource
    model: str
    download_hint: str
    estimated_download_bytes: int
    notes: str = ""


_RECOMMENDATIONS = {
    SensorySource.SPEECH: SensoryAudioModelRecommendation(
        source=SensorySource.SPEECH,
        model=RECOMMENDED_LLAMA_CPP_SPEECH_MODEL,
        download_hint="约 1.0 GB",
        estimated_download_bytes=1_019_141_728,
        notes="Qwen3-ASR Q8_0 + mmproj",
    ),
    SensorySource.SOUND: SensoryAudioModelRecommendation(
        source=SensorySource.SOUND,
        model=RECOMMENDED_LLAMA_CPP_SOUND_MODEL,
        download_hint="约 2.1 GB",
        estimated_download_bytes=2_178_818_080,
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
