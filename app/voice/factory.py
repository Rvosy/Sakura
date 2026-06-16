"""app/voice/factory.py — TTS Provider 统一工厂。

bootstrap（启动装配）与设置页保存后重建此前各自实例化 Provider，
两份逻辑存在漂移（设置页那份漏传 base_dir，导致缓存目录回退到
__file__ 推算）。统一入口后 UI 层不再直接依赖具体 Provider 类。

TTSConfigError 不在此处吞掉：启动路径降级为 Null，设置页路径弹窗，
由调用方决定。
"""

from __future__ import annotations

from pathlib import Path

from app.voice.tts import (
    GenieTTSProvider,
    GPTSoVITSTTSProvider,
    NullTTSProvider,
    TTSProvider,
)
from app.voice.tts_settings import TTS_PROVIDER_GENIE, GPTSoVITSTTSSettings


def create_tts_provider(
    settings: GPTSoVITSTTSSettings,
    *,
    base_dir: Path | None = None,
    adopt_existing_service: bool = True,
) -> TTSProvider:
    """按设置创建 TTS Provider；未启用时返回 NullTTSProvider。

    可能抛 TTSConfigError（Provider 构造内 validate），调用方负责提示策略。
    """
    if not settings.enabled:
        return NullTTSProvider()
    if settings.provider == TTS_PROVIDER_GENIE:
        return GenieTTSProvider(
            settings,
            base_dir=base_dir,
            adopt_existing_service=adopt_existing_service,
        )
    return GPTSoVITSTTSProvider(
        settings,
        base_dir=base_dir,
        adopt_existing_service=adopt_existing_service,
    )
