from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from app.core.resource_manager import ResourceManager
from app.core.debug_log import debug_log
from app.core.interaction import get_interaction_id
from app.storage.paths import StoragePaths
from app.voice.tts_settings import GPTSoVITSTTSSettings as _GPTSoVITSTTSSettings
from app.voice.tts_types import (
    TTSCallback,
    TTSPreparedAudio,
    TTSServiceState,
    _parse_service_endpoint,
    _set_service_state,
    _TTSRequest,
)
# 服务监督已抽到 tts_service.py、合成队列已抽到 tts_synthesis.py。
# 这里 re-export 供既有测试/装配从 app.voice.tts 导入。
from app.voice.tts_service import (  # noqa: F401
    GenieServiceSupervisor,
    TTSServiceSupervisor,
    _AttachedLocalProcess,
    _LocalProcessHandle,
    _build_genie_endpoint_url,
    _build_genie_start_command,
    _build_gpt_sovits_start_command,
    _build_tts_endpoint_url,
    _encode_genie_character_name,
    _find_running_local_tts_process,
    _format_gpt_sovits_http_error,
    _is_restartable_local_tts_service_failure,
    _is_soft_synth_failure,
    _local_tts_service_log_path,
    _local_tts_subprocess_env,
    _read_local_tts_output,
    _wait_local_service_ready,
)
from app.voice.tts_synthesis import (  # noqa: F401
    GenieSynthesisEngine,
    GPTSoVITSSynthesisEngine,
    TTSSynthesisQueue,
    _is_voiceable_text,
    _resolve_request_text_lang,
    _write_genie_audio,
    _write_raw_float_or_pcm_as_wav,
    _write_raw_pcm_as_wav,
)
# 播放端点已抽到 tts_playback.py；re-export 端点类与兜底常量供既有测试导入。
from app.voice.tts_playback import (  # noqa: F401
    TTSPlaybackEndpoint,
    _AUDIO_FINISH_FALLBACK_GRACE_MS,
    _AUDIO_FINISH_FALLBACK_MAX_MS,
)


def _resolve_project_root(base_dir: Path | None = None) -> Path:
    """解析项目根目录；base_dir 为空时基于 __file__ 推算（app/voice/tts.py → 项目根），
    与 main.py 的路径惯例一致。"""
    return Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[2]


def _resolve_tts_cache_dir(base_dir: Path | None = None) -> Path:
    """返回 TTS 临时音频缓存目录（data/cache/tts），并确保存在。

    不再写入系统 Temp，改用 Sakura 自有数据目录，便于集中管理与启动清理。
    """
    cache_dir = StoragePaths(_resolve_project_root(base_dir)).tts_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def purge_tts_cache(base_dir: Path | None = None) -> None:
    """启动时清空 data/cache/tts 残留（崩溃/强退遗留的临时 wav）。

    该目录完全归 Sakura 所有、仅存放 TTS 临时音频，清空安全。
    逐个删除并忽略个别占用错误，不影响启动。
    """
    cache_dir = _resolve_tts_cache_dir(base_dir)
    for entry in cache_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            entry.unlink()
        except OSError as exc:
            debug_log("TTS", "启动清理缓存文件失败，已跳过", {"path": str(entry), "error": str(exc)})


class TTSProvider(Protocol):
    @property
    def service_ready(self) -> bool:
        """本地 TTS 服务是否已探测/预热完成。"""
        ...

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        """播放或提交一段待朗读文本。"""

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        """提前生成一段待朗读音频，但不立即播放。"""

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        """播放 prepare 返回的音频；若仍在生成，则等待生成完成后播放。"""

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        """丢弃不再需要的预生成音频。"""

    def warm_up_playback(self) -> None:
        """提前初始化本地播放器，避免第一句朗读承担冷启动成本。"""

    def ensure_ready(self) -> tuple[bool, str]:
        """同步检测并预热 TTS 服务，不生成或播放音频。"""

    def close(self) -> None:
        """释放 Provider 自己启动的本地服务。"""


class NullTTSProvider:
    @property
    def service_ready(self) -> bool:
        return False

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        # GPT-SoVITS 接入前保留调用点，避免聊天流程以后再改。
        debug_log(
            "TTS",
            "静音 Provider 跳过播放",
            {
                "text": text,
                "tone": tone,
            },
        )
        _ = text
        _ = tone
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        debug_log("TTS", "静音 Provider 跳过预生成", {"text": text, "tone": tone})
        return TTSPreparedAudio(text=text.strip(), tone=tone)

    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        debug_log(
            "TTS",
            "静音 Provider 跳过预生成播放",
            {
                "text": handle.text,
                "tone": handle.tone,
            },
        )
        _ = handle
        if on_started is not None:
            on_started()
        if on_finished is not None:
            on_finished()

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        debug_log("TTS", "丢弃静音预生成句柄", {"text": handle.text, "tone": handle.tone})
        handle.cancelled = True

    def warm_up_playback(self) -> None:
        debug_log("TTS", "静音 Provider 跳过播放器预热")

    def ensure_ready(self) -> tuple[bool, str]:
        debug_log("TTS", "静音 Provider 跳过服务检测")
        return True, "TTS 已关闭。"

    def close(self) -> None:
        debug_log("TTS", "静音 Provider 无需关闭")


class GPTSoVITSTTSProvider(QObject):
    # 装配 + 委托的协调器：服务监督 / 合成队列 / 播放端点三者各司其职。
    # 播放相关信号已随播放端点迁到 TTSPlaybackEndpoint；error_occurred 由端点 re-emit
    # 给本协调器，供 PetWindow 连接（公开 API 不变）。
    error_occurred = Signal(str)

    def __init__(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        adopt_existing_service: bool = True,
    ) -> None:
        super().__init__()
        settings.validate()
        # TTS 临时音频缓存目录（data/cache/tts）。由调用方注入 base_dir，
        # 与启动清理 purge_tts_cache(base_dir) 同源，避免写入目录与清理目录错位。
        # base_dir 为空时退回 _resolve_tts_cache_dir 的 __file__ 推算，保持向后兼容。
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._tts_cache_dir = _resolve_tts_cache_dir(base_dir)
        # 关闭标志由 _close_lock 守护（合成队列在自己的锁内不持有它，避免反向锁序）。
        self._close_lock = threading.Lock()
        self._closed = False
        # 协调器自持一个 ResourceManager：本地子进程 + 合成线程都注册进去，
        # close() 走 stop_all 统一关闭；provider 退役/热切换沿用 close()，无需共享 RM。
        self._resource_manager = ResourceManager(self)
        # 服务进程监督拆到 TTSServiceSupervisor；settings 由 supervisor 持有（见 settings 属性）。
        self._supervisor = self._create_supervisor(
            settings, adopt_existing_service=adopt_existing_service
        )
        # 播放端点拆到 TTSPlaybackEndpoint（UI 主线程子对象，随本协调器 moveToThread）；
        # error_occurred re-emit 给本协调器供 PetWindow 连接。
        self._playback = self._create_playback_endpoint(settings)
        self._playback.error_occurred.connect(self.error_occurred)
        # 合成队列拆到 TTSSynthesisQueue；以播放端点为 sink 把结果投回播放队列。
        self._synthesis_queue = self._create_synthesis_queue()

    def _create_supervisor(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        adopt_existing_service: bool,
    ) -> TTSServiceSupervisor:
        """装配本 Provider 的服务监督；Genie 子类覆写为 GenieServiceSupervisor。"""
        return TTSServiceSupervisor(
            settings,
            base_dir=self._base_dir,
            resource_manager=self._resource_manager,
            is_closed=self._is_closed,
            adopt_existing_service=adopt_existing_service,
        )

    def _create_synthesis_engine(self) -> object:
        """装配本 Provider 的合成引擎；Genie 子类覆写为 GenieSynthesisEngine。"""
        return GPTSoVITSSynthesisEngine()

    def _create_synthesis_queue(self) -> TTSSynthesisQueue:
        return TTSSynthesisQueue(
            supervisor=self._supervisor,
            engine=self._create_synthesis_engine(),
            cache_dir=self._tts_cache_dir,
            resource_manager=self._resource_manager,
            sink=self._playback,
            is_closed=self._is_closed,
        )

    def _create_playback_endpoint(self, settings: _GPTSoVITSTTSSettings) -> TTSPlaybackEndpoint:
        return TTSPlaybackEndpoint(
            self,
            cache_dir=self._tts_cache_dir,
            playback_backend=getattr(settings, "playback_backend", "") or "",
            is_closed=self._is_closed,
        )

    @property
    def settings(self) -> _GPTSoVITSTTSSettings:
        """settings 由 supervisor 持有，使 Genie 备用端口切换能传播到合成路径。"""
        return self._supervisor.settings

    @property
    def service_ready(self) -> bool:
        """服务探测是否已成功(实际可达)，委托给服务监督。

        供接话音频预生成等调用方做就绪门控:provider 实例存在不代表
        服务已启动,未就绪时发起 prepare 只会得到静默失败。
        """
        return self._supervisor.service_ready

    def speak(
        self,
        text: str,
        tone: str | None = None,
        on_finished: TTSCallback | None = None,
        on_started: TTSCallback | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            debug_log("TTS", "空文本跳过播放")
            self._playback.run_callbacks(on_started, on_finished)
            return
        debug_log("TTS", "提交播放请求", {"text": text, "tone": tone})
        self._synthesis_queue.submit(
            _TTSRequest(
                text=text,
                tone=tone,
                on_started=on_started,
                on_finished=on_finished,
                interaction_id=get_interaction_id(),
            )
        )

    def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
        text = text.strip()
        handle = TTSPreparedAudio(text=text, tone=tone)
        if not text:
            debug_log("TTS", "空文本跳过预生成")
            handle.failed = True
            return handle
        debug_log("TTS", "提交预生成请求", {"text": text, "tone": tone})
        self._synthesis_queue.submit(
            _TTSRequest(
                text=text,
                tone=tone,
                prepared_audio=handle,
                interaction_id=get_interaction_id(),
            )
        )
        return handle


    def speak_prepared(
        self,
        handle: TTSPreparedAudio,
        on_started: TTSCallback | None = None,
        on_finished: TTSCallback | None = None,
    ) -> None:
        """播放 prepare 返回的音频；委托给播放端点。"""
        self._playback.speak_prepared(handle, on_started=on_started, on_finished=on_finished)

    def discard_prepared(self, handle: TTSPreparedAudio) -> None:
        handle.cancelled = True
        debug_log("TTS", "取消预生成音频", {"text": handle.text, "tone": handle.tone})
        # 队列侧丢弃待合成请求，播放侧清理已入播放队列的临时音频。
        self._synthesis_queue.discard_pending(handle)
        self._playback.discard_prepared(handle)

    def warm_up_playback(self) -> None:
        """提前初始化本地播放器；委托给播放端点。"""
        self._playback.warm_up_playback()

    def ensure_ready(self) -> tuple[bool, str]:
        """同步检测并预热本地 TTS 服务，委托给服务监督。"""
        return self._supervisor.ensure_ready()

    def close(self) -> None:
        with self._close_lock:
            self._closed = True
        self._synthesis_queue.clear_pending()
        self._playback.shutdown()
        # 本地子进程 + 合成线程由协调器自持的 RM 托管，统一经 stop_all 关闭。
        self._resource_manager.stop_all()

    def _is_closed(self) -> bool:
        with self._close_lock:
            return self._closed


    def detach_local_service(self) -> None:
        """交出本地服务进程所有权，供新的 Provider 在后台接管（委托服务监督）。"""
        self._supervisor.detach_local_service()


class GenieTTSProvider(GPTSoVITSTTSProvider):
    """Genie TTS CPU 推理 Provider，复用现有队列、预生成和播放器链路。

    差异收敛到装配选型：服务监督差异（Genie API 探测 / 备用端口 / 角色模型 /
    参考音频 / ONNX 转换）在 GenieServiceSupervisor，合成差异在 GenieSynthesisEngine。
    """

    def _create_supervisor(
        self,
        settings: _GPTSoVITSTTSSettings,
        *,
        adopt_existing_service: bool,
    ) -> TTSServiceSupervisor:
        return GenieServiceSupervisor(
            settings,
            base_dir=self._base_dir,
            resource_manager=self._resource_manager,
            is_closed=self._is_closed,
            adopt_existing_service=adopt_existing_service,
        )

    def _create_synthesis_engine(self) -> object:
        return GenieSynthesisEngine()

