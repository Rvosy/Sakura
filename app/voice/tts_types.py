"""TTS 子系统的共享类型与跨组件小工具（issue #94 第 3 阶段）。

把原本散在 ``app/voice/tts.py`` 顶部、被 supervisor / synthesis / playback 三方
共用的类型与无状态 helper 抽到这里，避免拆分后产生循环依赖：
- ``TTSPreparedAudio`` / ``_TTSRequest``：合成请求与预生成句柄
- ``TTSServiceState`` + ``_set_service_state``：本地服务生命周期显式状态机
- ``_provider_is_closed`` / ``_parse_service_endpoint``：跨组件容错小工具

这些符号仍由 ``app/voice/tts.py`` re-export，保持既有导入路径与测试兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.core.runtime_log import log_event

TTSCallback = Callable[[], None]


@dataclass
class TTSPreparedAudio:
    """一段已提交预生成的 TTS 音频句柄。"""

    text: str
    tone: str | None = None
    audio_path: Path | None = None
    play_requested: bool = False
    enqueued: bool = False
    cancelled: bool = False
    failed: bool = False
    on_started: TTSCallback | None = None
    on_finished: TTSCallback | None = None


@dataclass(frozen=True)
class _TTSRequest:
    text: str
    tone: str | None
    on_started: TTSCallback | None = None
    on_finished: TTSCallback | None = None
    prepared_audio: TTSPreparedAudio | None = None
    # 发起请求时的交互 ID；请求线程入口恢复，使 TTS 日志可与该次交互串联
    interaction_id: str = ""


class TTSServiceState(str, Enum):
    """TTS 本地服务生命周期的显式状态；转移由 _set_service_state 统一记日志。

    IDLE → PROBING → (READY | STARTING) ; STARTING → WAITING_READY → (READY | FAILED)
    READY 后探测短路；FAILED 不缓存——下次请求重新走完整流程（服务可能被手动拉起）。
    """

    IDLE = "idle"
    PROBING = "probing"
    STARTING = "starting"
    WAITING_READY = "waiting_ready"
    READY = "ready"
    FAILED = "failed"


def _set_service_state(provider: object, new_state: TTSServiceState, detail: dict | None = None) -> None:
    """记录服务状态转移；provider 可能是测试桩（SimpleNamespace），全程容错。"""
    old_state = getattr(provider, "_service_state", TTSServiceState.IDLE)
    try:
        setattr(provider, "_service_state", new_state)
    except (AttributeError, TypeError):
        pass
    if old_state == new_state:
        return
    payload = {"from": str(getattr(old_state, "value", old_state)), "to": new_state.value}
    if detail:
        payload.update(detail)
    log_event("TTS", "tts.service_state", payload, verbosity=3)


def _provider_is_closed(provider: object) -> bool:
    is_closed = getattr(provider, "_is_closed", None)
    if callable(is_closed):
        return bool(is_closed())
    return bool(getattr(provider, "_closed", False))


def _parse_service_endpoint(api_url: str) -> tuple[str, int] | None:
    """解析服务地址为 (host, port)；地址非法返回 None，由调用方给出服务名相关提示。"""
    parsed_url = urlparse(api_url)
    host = parsed_url.hostname
    try:
        port = parsed_url.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed_url.scheme == "https" else 80
    if not host:
        return None
    return host, port
