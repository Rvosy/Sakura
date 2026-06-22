"""Sensory middleware framework for multimodal observations."""

from app.sensory.audio_capture import (
    CapturedAudio,
    SystemAudioCapture,
    SystemAudioCaptureError,
    create_system_audio_capture,
)
from app.sensory.audio_inference import (
    BUILTIN_AUDIO_RUNTIME,
    OFFICIAL_AUDIO_FRAMEWORK_ID,
    OFFICIAL_AUDIO_FRAMEWORK_LABEL,
    SIDECAR_AUDIO_RUNTIME,
    AudioInferenceEngine,
    AudioInferenceFrameworkSpec,
    AudioInferenceRequest,
    AudioInferenceResult,
    AudioInferenceTask,
    ShortAudioInferenceEngine,
    create_default_audio_inference_engine,
    official_audio_inference_framework,
)
from app.sensory.context import SensoryContextProvider
from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
)
from app.sensory.pipeline import SensoryPipeline
from app.sensory.providers import (
    ApiSensoryProvider,
    DisabledProvider,
    FakeSensoryProvider,
    LlamaCppSensoryProvider,
    LmStudioSensoryProvider,
    LocalSensoryProvider,
    OllamaSensoryProvider,
    SensoryProvider,
    SensoryProviderUnavailable,
)
from app.sensory.settings import (
    SensoryProviderConfig,
    SensorySettings,
    SensorySourceSettings,
)
from app.sensory.store import SensoryObservationStore
from app.sensory.tools import (
    SENSORY_OBSERVATION_CAPABILITY,
    SENSORY_OBSERVATION_TOOL_NAME,
    create_sensory_observation_tool,
)

__all__ = [
    "ApiSensoryProvider",
    "AudioInferenceEngine",
    "AudioInferenceFrameworkSpec",
    "AudioInferenceRequest",
    "AudioInferenceResult",
    "AudioInferenceTask",
    "BUILTIN_AUDIO_RUNTIME",
    "CapturedAudio",
    "DisabledProvider",
    "FakeSensoryProvider",
    "LlamaCppSensoryProvider",
    "LmStudioSensoryProvider",
    "LocalSensoryProvider",
    "OllamaSensoryProvider",
    "OFFICIAL_AUDIO_FRAMEWORK_ID",
    "OFFICIAL_AUDIO_FRAMEWORK_LABEL",
    "SensoryContextProvider",
    "SensoryObservation",
    "SensoryObservationStore",
    "SensoryPipeline",
    "SensoryProvider",
    "SensoryProviderConfig",
    "SensoryProviderMode",
    "SensoryProviderUnavailable",
    "SensoryRequest",
    "SensorySettings",
    "SensorySource",
    "SensorySourceSettings",
    "ShortAudioInferenceEngine",
    "SIDECAR_AUDIO_RUNTIME",
    "SystemAudioCapture",
    "SystemAudioCaptureError",
    "SENSORY_OBSERVATION_CAPABILITY",
    "SENSORY_OBSERVATION_TOOL_NAME",
    "create_default_audio_inference_engine",
    "create_sensory_observation_tool",
    "create_system_audio_capture",
    "official_audio_inference_framework",
]
