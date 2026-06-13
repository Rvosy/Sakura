from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.debug_log import debug_log

NO_VOICE_FINGERPRINT = "novoice"


@runtime_checkable
class _VoiceProfile(Protocol):
    """指纹计算所需的角色声线子集(鸭子类型,避免依赖完整 CharacterVoice)。"""

    gpt_model_path: Path | None
    sovits_model_path: Path | None
    tone_ref_path: Path


def voice_fingerprint(voice: _VoiceProfile | None) -> str:
    """角色声线指纹:模型文件名 + 语气参考清单内容。

    声线(模型/参考音频)变更后旧合成音频按指纹自然失效,新指纹的
    缓存从零积累;旧指纹文件留在目录里只占空间不影响正确性。
    """
    if voice is None:
        return NO_VOICE_FINGERPRINT
    digest = hashlib.sha256()
    digest.update((voice.gpt_model_path.name if voice.gpt_model_path else "").encode("utf-8"))
    digest.update(b"|")
    digest.update(
        (voice.sovits_model_path.name if voice.sovits_model_path else "").encode("utf-8")
    )
    digest.update(b"|")
    try:
        digest.update(voice.tone_ref_path.read_bytes())
    except OSError:
        pass
    return digest.hexdigest()[:8]


class BackchannelAudioCache:
    """运行时合成接话音频的磁盘持久化。

    位置 data/backchannels/<character_id>/audio/ —— 角色包保持只读,
    运行时产物一律落 data/,角色包升级整体覆盖时缓存存活。

    文件名内容寻址:{voice_fp}_{sha1(tone|ja)[:16]}.wav。清单条目与
    音频的"动态链接"即指纹 + 内容寻址的 lookup:模板增删改名不影响
    命中、同句多模板共享一份音频,无需回写 manifest,也无需把
    frozen 的 variant 改成可变。
    """

    def __init__(self, root: Path, fingerprint: str) -> None:
        self._root = root
        self._fingerprint = fingerprint or NO_VOICE_FINGERPRINT

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, tone: str, ja_text: str) -> Path:
        content = hashlib.sha1(f"{tone}|{ja_text}".encode("utf-8")).hexdigest()[:16]
        return self._root / f"{self._fingerprint}_{content}.wav"

    def lookup(self, tone: str, ja_text: str) -> Path | None:
        path = self.path_for(tone, ja_text)
        return path if path.exists() else None

    def store(self, tone: str, ja_text: str, source: Path) -> Path | None:
        """把合成产物复制进缓存。幂等;失败只记日志(缓存是优化不是依赖)。

        必须复制而非移动/直链:provider 在播放结束后会删除它产出的
        临时文件(_schedule_audio_cleanup),缓存文件须独立于其生命周期。
        """
        target = self.path_for(tone, ja_text)
        if target.exists():
            return target
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return target
        except OSError as exc:
            debug_log(
                "Backchannel",
                "接话音频写入磁盘缓存失败",
                {"target": str(target), "error": str(exc)},
            )
            return None
