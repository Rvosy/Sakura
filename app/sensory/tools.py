from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent.tools import Tool
from app.sensory.audio_capture import AudioInputSource
from app.sensory.models import (
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
    coerce_sensory_source,
    generate_sensory_id,
)
from app.sensory.audio_capture import SystemAudioCaptureError
from app.sensory.pipeline import SensoryPipeline
from app.sensory.settings import SensoryProviderConfig


SENSORY_OBSERVATION_TOOL_NAME = "observe_sensory"
SENSORY_OBSERVATION_CAPABILITY = "sensory_observation"
SENSORY_SPEECH_OBSERVATION_CAPABILITY = "sensory_speech_observation"
SENSORY_SOUND_OBSERVATION_CAPABILITY = "sensory_sound_observation"
OBSERVE_SYSTEM_SPEECH_TOOL_NAME = "observe_system_speech"
OBSERVE_SYSTEM_SOUND_TOOL_NAME = "observe_system_sound"
OBSERVE_ENVIRONMENT_SPEECH_TOOL_NAME = "observe_environment_speech"
OBSERVE_ENVIRONMENT_SOUND_TOOL_NAME = "observe_environment_sound"
SENSORY_OBSERVATION_DISABLED_ERROR = "当前没有可用的增强感知模型，或该感官源未启用。"
SENSORY_AUDIO_CAPTURE_TOOL_NAMES = frozenset(
    {
        OBSERVE_SYSTEM_SPEECH_TOOL_NAME,
        OBSERVE_SYSTEM_SOUND_TOOL_NAME,
        OBSERVE_ENVIRONMENT_SPEECH_TOOL_NAME,
        OBSERVE_ENVIRONMENT_SOUND_TOOL_NAME,
    }
)


def create_sensory_observation_tool(
    pipeline_getter: Callable[[], SensoryPipeline | None],
) -> Tool:
    return Tool(
        name=SENSORY_OBSERVATION_TOOL_NAME,
        description=(
            "调用已配置的增强感知中间件，把视觉、语音或声音事件输入整理成结构化观察证据。"
            "只在对应感官模型已配置并且确实需要补充感官证据时使用。"
            "speech/sound 不带媒体输入时会短暂采集电脑系统声音；local 为本机处理，lan/api 会发送到配置的 endpoint。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": [source.value for source in SensorySource],
                    "description": "要调用的感官源：vision、speech 或 sound。",
                },
                "user_text": {
                    "type": "string",
                    "description": "触发本次观察的用户原话或当前问题。",
                },
                "event_type": {
                    "type": "string",
                    "description": "可选事件类型，例如 user_message、screen_awareness_check。",
                },
                "text": {
                    "type": "string",
                    "description": "已获得的文字线索，例如转写文本、声音标签、用户提供的画面描述。",
                },
                "media_ref": {
                    "type": "string",
                    "description": (
                        "可选媒体引用。vision 可传 data:image/...、图片 URL 或本地临时图片路径；"
                        "speech/sound 可传 data:audio/... 或本地临时音频路径。"
                    ),
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "speech/sound 没有媒体输入时采集系统声音的秒数，默认 3 秒，范围 0.5 到 10。",
                },
                "audio_input_source": {
                    "type": "string",
                    "enum": [source.value for source in AudioInputSource],
                    "description": "speech/sound 没有媒体输入时的采集来源；默认 system_audio，可选 microphone。",
                },
                "sample_rate": {
                    "type": "integer",
                    "description": "采集音频采样率，默认 16000，范围 8000 到 48000。",
                },
                "channel_count": {
                    "type": "integer",
                    "description": "采集声道数，默认 1，范围 1 到 2。",
                },
                "exclude_current_process": {
                    "type": "boolean",
                    "description": "系统音频采集时是否排除 Sakura 自己的声音，默认 true；麦克风采集会忽略该值。",
                },
                "metadata": {
                    "type": "object",
                    "description": "可选结构化元数据；不要放入密码、token、原始音频或不必要的原始媒体。",
                },
            },
            "required": ["source"],
        },
        handler=lambda arguments: observe_sensory(arguments, pipeline_getter),
        requires_confirmation=True,
        confirmation_risk="sensory_audio_capture",
        group="sensory",
        risk="medium",
        capability=SENSORY_OBSERVATION_CAPABILITY,
    )


def create_sensory_audio_observation_tools(
    pipeline_getter: Callable[[], SensoryPipeline | None],
) -> list[Tool]:
    return [
        _create_audio_tool(
            name=OBSERVE_SYSTEM_SPEECH_TOOL_NAME,
            source=SensorySource.SPEECH,
            input_source=AudioInputSource.SYSTEM_AUDIO,
            capability=SENSORY_SPEECH_OBSERVATION_CAPABILITY,
            description=(
                "短暂录制电脑系统输出声音并交给已配置的 speech 感知模型转写。"
                "只在用户询问电脑、视频、会议或网页里说了什么时使用；local 本机处理，lan/api 会发送到配置的 endpoint。"
            ),
            pipeline_getter=pipeline_getter,
        ),
        _create_audio_tool(
            name=OBSERVE_SYSTEM_SOUND_TOOL_NAME,
            source=SensorySource.SOUND,
            input_source=AudioInputSource.SYSTEM_AUDIO,
            capability=SENSORY_SOUND_OBSERVATION_CAPABILITY,
            description=(
                "短暂录制电脑系统输出声音并交给已配置的 sound 感知模型识别声音事件。"
                "用于提示音、播放内容、音乐是否存在、游戏/视频背景声等非逐字转写问题。"
            ),
            pipeline_getter=pipeline_getter,
        ),
        _create_audio_tool(
            name=OBSERVE_ENVIRONMENT_SPEECH_TOOL_NAME,
            source=SensorySource.SPEECH,
            input_source=AudioInputSource.MICROPHONE,
            capability=SENSORY_SPEECH_OBSERVATION_CAPABILITY,
            description=(
                "短暂录制麦克风环境音并交给已配置的 speech 感知模型转写。"
                "只在用户询问身边的人或房间里有人说了什么时使用。"
            ),
            pipeline_getter=pipeline_getter,
        ),
        _create_audio_tool(
            name=OBSERVE_ENVIRONMENT_SOUND_TOOL_NAME,
            source=SensorySource.SOUND,
            input_source=AudioInputSource.MICROPHONE,
            capability=SENSORY_SOUND_OBSERVATION_CAPABILITY,
            description=(
                "短暂录制麦克风环境音并交给已配置的 sound 感知模型识别声音事件。"
                "用于门铃、敲击、噪音、背景音乐是否存在等环境声问题。"
            ),
            pipeline_getter=pipeline_getter,
        ),
    ]


def _create_audio_tool(
    *,
    name: str,
    source: SensorySource,
    input_source: AudioInputSource,
    capability: str,
    description: str,
    pipeline_getter: Callable[[], SensoryPipeline | None],
) -> Tool:
    return Tool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "user_text": {
                    "type": "string",
                    "description": "触发本次观察的用户原话或当前问题。",
                },
                "event_type": {
                    "type": "string",
                    "description": "可选事件类型，例如 user_message、screen_awareness_check。",
                },
                "text": {
                    "type": "string",
                    "description": "已获得的文字线索，例如转写文本、声音标签或用户补充说明。",
                },
                "media_ref": {
                    "type": "string",
                    "description": "可选音频引用。传入 data:audio/... 或本地临时音频路径时不会重新录音。",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "没有媒体输入时采集音频的秒数，默认 3 秒，范围 0.5 到 10。",
                },
                "sample_rate": {
                    "type": "integer",
                    "description": "采集音频采样率，默认 16000，范围 8000 到 48000。",
                },
                "channel_count": {
                    "type": "integer",
                    "description": "采集声道数，默认 1，范围 1 到 2。",
                },
                "exclude_current_process": {
                    "type": "boolean",
                    "description": "系统音频采集时是否排除 Sakura 自己的声音，默认 true；麦克风采集会忽略该值。",
                },
                "metadata": {
                    "type": "object",
                    "description": "可选结构化元数据；不要放入密码、token、原始音频或不必要的原始媒体。",
                },
            },
        },
        handler=lambda arguments: observe_sensory(
            {
                **arguments,
                "source": source.value,
                "audio_input_source": input_source.value,
            },
            pipeline_getter,
        ),
        requires_confirmation=True,
        confirmation_risk="sensory_audio_capture",
        group="sensory",
        risk="medium",
        capability=capability,
    )


def observe_sensory(
    arguments: dict[str, Any],
    pipeline_getter: Callable[[], SensoryPipeline | None],
) -> dict[str, Any]:
    pipeline = pipeline_getter()
    if pipeline is None:
        return _unavailable("sensory_pipeline_missing")
    settings = pipeline.settings.normalized()
    source = coerce_sensory_source(arguments.get("source"))
    source_settings = settings.sources[source]
    if not settings.enabled or source_settings.mode == SensoryProviderMode.OFF:
        return _unavailable("source_disabled", source=source)
    provider = settings.provider_for_source(source)
    if provider is None or not _provider_config_usable(provider):
        return _unavailable("provider_not_configured", source=source)

    metadata = _mapping(arguments.get("metadata"))
    request = SensoryRequest(
        id=generate_sensory_id("req"),
        source=source,
        user_text=str(arguments.get("user_text") or ""),
        event_type=str(arguments.get("event_type") or ""),
        text=str(arguments.get("text") or ""),
        media_ref=str(arguments.get("media_ref") or ""),
        metadata={
            **metadata,
            "requested_by": "observe_sensory_tool",
        },
    ).normalized()
    if source == SensorySource.VISION and not _request_has_media(request):
        return {
            "status": "needs_media",
            "source": source.value,
            "provider_id": provider.provider_id,
            "mode": provider.mode.value,
            "error": (
                "vision 感官模型需要图片输入。若需要当前屏幕，请先调用 observe_screen；"
                "如果本轮已经有截图，请直接依据截图或已有 sensory 上下文回答。"
            ),
        }

    if source in {SensorySource.SPEECH, SensorySource.SOUND} and not _request_has_audio_media(request):
        audio_input_source = _audio_input_source(
            arguments.get("audio_input_source")
            or request.metadata.get("audio_input_source")
            or request.metadata.get("capture_source")
        )
        try:
            observation = pipeline.observe_audio(
                source=source,
                input_source=audio_input_source,
                user_text=request.user_text,
                event_type=request.event_type,
                text=request.text,
                duration_seconds=_duration_seconds(arguments.get("duration_seconds")),
                sample_rate=_sample_rate(arguments.get("sample_rate")),
                channel_count=_channel_count(arguments.get("channel_count")),
                exclude_current_process=_bool_argument(arguments.get("exclude_current_process"), default=True),
            )
        except SystemAudioCaptureError as exc:
            unavailable = _unavailable("audio_capture_unavailable", source=source, provider_id=provider.provider_id)
            unavailable["audio_input_source"] = audio_input_source.value
            unavailable["error"] = str(exc)
            return unavailable
    else:
        observation = pipeline.observe(request)
    if observation is None:
        return _unavailable("provider_unavailable", source=source, provider_id=provider.provider_id)
    return {
        "status": "ok",
        "source": observation.source.value,
        "observation": observation.to_dict(),
    }


def configured_sensory_sources(pipeline: SensoryPipeline | None) -> tuple[SensorySource, ...]:
    if pipeline is None:
        return ()
    settings = pipeline.settings.normalized()
    if not settings.enabled:
        return ()
    sources: list[SensorySource] = []
    for source, source_settings in settings.sources.items():
        if source_settings.mode == SensoryProviderMode.OFF:
            continue
        provider = settings.provider_for_source(source)
        if provider is None or not _provider_config_usable(provider):
            continue
        sources.append(source)
    return tuple(sources)


def configured_sensory_capabilities(pipeline: SensoryPipeline | None) -> set[str]:
    sources = set(configured_sensory_sources(pipeline))
    capabilities: set[str] = set()
    if sources:
        capabilities.add(SENSORY_OBSERVATION_CAPABILITY)
    if SensorySource.SPEECH in sources:
        capabilities.add(SENSORY_SPEECH_OBSERVATION_CAPABILITY)
    if SensorySource.SOUND in sources:
        capabilities.add(SENSORY_SOUND_OBSERVATION_CAPABILITY)
    return capabilities


def build_sensory_tool_prompt(sources: tuple[Any, ...]) -> str:
    if not sources:
        return ""
    normalized_sources = {coerce_sensory_source(source) for source in sources}
    labels = ", ".join(_source_label(source) for source in sources)
    lines = [
        f"- 增强感知：已配置 {labels} 感官中间件，需要额外感官证据时可调用 observe_sensory。",
        "- observe_sensory 返回的是中间件整理过的证据，不是角色回复；最终判断仍需结合用户问题和已有上下文。",
    ]
    if SensorySource.VISION in normalized_sources:
        lines.append(
            "- 对 vision：如果需要当前屏幕且本轮还没有图片，先调用 observe_screen 获取截图；不要让 observe_sensory 凭空描述屏幕。"
        )
    if SensorySource.SPEECH in normalized_sources:
        lines.append(
            "- 对 speech：用户问电脑/视频/网页里说了什么时用 observe_system_speech；问身边的人或房间里有人说了什么时用 observe_environment_speech。"
        )
    if SensorySource.SOUND in normalized_sources:
        lines.append(
            "- 对 sound：用户问电脑播放了什么、提示音或背景声时用 observe_system_sound；问房间噪音、门铃、敲击或环境音乐是否存在时用 observe_environment_sound。"
        )
    if {SensorySource.SPEECH, SensorySource.SOUND} & normalized_sources:
        lines.extend(
            [
                "- 兼容入口 observe_sensory 仍可使用；speech/sound 没有 media_ref 时默认采集 system_audio，也可传 audio_input_source=mic/microphone。",
                "- speech/sound 使用 local 时音频交给本机服务；使用 lan/api 时音频会发送到用户配置的 endpoint。采集电脑或麦克风声音都需要用户确认。",
            ]
        )
    lines.append("- 不要把密码、token、密钥、身份证、银行卡等敏感内容传入 metadata；工具结果中出现敏感内容时按 [REDACTED] 处理。")
    return "\n".join(lines)


def _provider_config_usable(provider: SensoryProviderConfig) -> bool:
    if not provider.model:
        return False
    if provider.mode == SensoryProviderMode.API:
        return bool(provider.endpoint)
    if provider.mode == SensoryProviderMode.LOCAL:
        if provider.endpoint:
            return True
        backend = str(provider.extra.get("backend") or provider.extra.get("provider") or "").lower()
        return backend in {"lmstudio", "lm_studio", "ollama", "llama", "llama.cpp", "llama_cpp", "llamacpp"}
    return False


def _source_label(source: Any) -> str:
    value = getattr(source, "value", source)
    return str(value or "").strip()


def _request_has_media(request: SensoryRequest) -> bool:
    if request.media_ref:
        return True
    for key in ("data_url", "image_url", "audio_url", "media_ref", "path"):
        if request.metadata.get(key):
            return True
    for key in ("image_urls", "audio_urls", "images", "audios", "media_refs"):
        value = request.metadata.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _request_has_audio_media(request: SensoryRequest) -> bool:
    if request.media_ref:
        return True
    for key in ("data_url", "audio_url", "media_ref", "path"):
        if request.metadata.get(key):
            return True
    for key in ("audio_urls", "audios", "media_refs"):
        value = request.metadata.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _duration_seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 3.0
    return max(0.5, min(10.0, number))


def _sample_rate(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 16000
    return max(8000, min(48000, number))


def _channel_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return max(1, min(2, number))


def _bool_argument(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _audio_input_source(value: Any) -> AudioInputSource:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"microphone", "mic", "environment", "environment_audio", "ambient", "ambient_audio"}:
        return AudioInputSource.MICROPHONE
    return AudioInputSource.SYSTEM_AUDIO


def _unavailable(
    reason: str,
    *,
    source: SensorySource | None = None,
    provider_id: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "unavailable",
        "reason": reason,
        "error": SENSORY_OBSERVATION_DISABLED_ERROR,
    }
    if source is not None:
        data["source"] = source.value
    if provider_id:
        data["provider_id"] = provider_id
    return data


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
