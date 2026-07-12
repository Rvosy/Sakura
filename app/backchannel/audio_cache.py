from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.runtime_log import log_event
from app.storage.atomic import replace_with_retry

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
    _update_path_fingerprint(digest, voice.gpt_model_path)
    _update_path_fingerprint(digest, voice.sovits_model_path)
    _update_path_fingerprint(digest, voice.tone_ref_path, full=True)
    try:
        package_root = voice.tone_ref_path.parents[2]
        for line in voice.tone_ref_path.read_text(encoding="utf-8").splitlines():
            path_text = line.split("|", 1)[0].strip()
            if not path_text:
                continue
            candidate = Path(path_text)
            if not candidate.is_absolute():
                candidate = package_root / candidate
            _update_path_fingerprint(digest, candidate)
    except (OSError, IndexError):
        pass
    return digest.hexdigest()[:8]


def _update_path_fingerprint(
    digest: "hashlib._Hash",
    path: Path | None,
    *,
    full: bool = False,
) -> None:
    if path is None:
        digest.update(b"<missing>")
        return
    candidate = Path(path)
    digest.update(str(candidate).encode("utf-8", errors="surrogatepass"))
    try:
        stat_result = candidate.stat()
        digest.update(f"|{stat_result.st_size}|".encode("ascii"))
        with candidate.open("rb") as handle:
            if full or stat_result.st_size <= 128 * 1024:
                digest.update(handle.read())
            else:
                digest.update(handle.read(64 * 1024))
                handle.seek(max(0, stat_result.st_size - 64 * 1024))
                digest.update(handle.read(64 * 1024))
    except OSError:
        digest.update(b"<unreadable>")


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
        if path.is_file() and path.stat().st_size > 0:
            return path
        path.unlink(missing_ok=True)
        return None

    def store(self, tone: str, ja_text: str, source: Path) -> Path | None:
        """把合成产物复制进缓存。幂等;失败只记日志(缓存是优化不是依赖)。

        必须复制而非移动/直链:provider 在播放结束后会删除它产出的
        临时文件(_schedule_audio_cleanup),缓存文件须独立于其生命周期。
        """
        target = self.path_for(tone, ja_text)
        if target.exists():
            return target if target.is_file() and target.stat().st_size > 0 else None
        temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, temp_target)
            if temp_target.stat().st_size <= 0 or temp_target.stat().st_size != Path(source).stat().st_size:
                raise OSError("缓存音频复制不完整")
            replace_with_retry(temp_target, target)
            return target
        except OSError as exc:
            log_event(
                "Backchannel",
                "接话音频写入磁盘缓存失败",
                {"target": str(target), "error": str(exc)},
            )
            return None
        finally:
            try:
                temp_target.unlink(missing_ok=True)
            except OSError:
                pass
