from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent.tools import Tool
from app.sensory.models import (
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
    coerce_sensory_source,
    generate_sensory_id,
)
from app.sensory.pipeline import SensoryPipeline
from app.sensory.settings import SensoryProviderConfig


SENSORY_OBSERVATION_TOOL_NAME = "observe_sensory"
SENSORY_OBSERVATION_CAPABILITY = "sensory_observation"
SENSORY_OBSERVATION_DISABLED_ERROR = "当前没有可用的增强感知模型，或该感官源未启用。"


def create_sensory_observation_tool(
    pipeline_getter: Callable[[], SensoryPipeline | None],
) -> Tool:
    return Tool(
        name=SENSORY_OBSERVATION_TOOL_NAME,
        description=(
            "调用已配置的增强感知中间件，把视觉、语音或环境声音输入整理成结构化观察证据。"
            "只在对应感官模型已配置并且确实需要补充感官证据时使用。"
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
                    "description": "可选媒体引用。视觉源可传 data:image/... base64、图片 URL 或本地临时图片路径。",
                },
                "metadata": {
                    "type": "object",
                    "description": "可选结构化元数据；不要放入密码、token、原始音频或不必要的原始媒体。",
                },
            },
            "required": ["source"],
        },
        handler=lambda arguments: observe_sensory(arguments, pipeline_getter),
        requires_confirmation=False,
        group="sensory",
        risk="low",
        capability=SENSORY_OBSERVATION_CAPABILITY,
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


def build_sensory_tool_prompt(sources: tuple[Any, ...]) -> str:
    if not sources:
        return ""
    labels = ", ".join(_source_label(source) for source in sources)
    return "\n".join(
        [
            f"- 增强感知：已配置 {labels} 感官中间件，需要额外感官证据时可调用 observe_sensory。",
            "- observe_sensory 返回的是中间件整理过的证据，不是角色回复；最终判断仍需结合用户问题和已有上下文。",
            "- 对 vision：如果需要当前屏幕且本轮还没有图片，先调用 observe_screen 获取截图；不要让 observe_sensory 凭空描述屏幕。",
            "- 不要把密码、token、密钥、身份证、银行卡等敏感内容传入 metadata；工具结果中出现敏感内容时按 [REDACTED] 处理。",
        ]
    )


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
    for key in ("data_url", "image_url", "media_ref", "path"):
        if request.metadata.get(key):
            return True
    for key in ("image_urls", "images", "media_refs"):
        value = request.metadata.get(key)
        if isinstance(value, list) and value:
            return True
    return False


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
