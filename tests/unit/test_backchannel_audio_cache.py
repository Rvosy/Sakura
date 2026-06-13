from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.backchannel.audio_cache import (
    NO_VOICE_FINGERPRINT,
    BackchannelAudioCache,
    voice_fingerprint,
)


@dataclass
class _VoiceStub:
    gpt_model_path: Path | None
    sovits_model_path: Path | None
    tone_ref_path: Path


def _voice(tmp_path: Path, ref_content: bytes = b"refs-v1") -> _VoiceStub:
    ref = tmp_path / "ref.txt"
    ref.write_bytes(ref_content)
    return _VoiceStub(
        gpt_model_path=tmp_path / "model.ckpt",
        sovits_model_path=tmp_path / "model.pth",
        tone_ref_path=ref,
    )


def test_fingerprint_none_voice_is_sentinel() -> None:
    assert voice_fingerprint(None) == NO_VOICE_FINGERPRINT


def test_fingerprint_changes_with_ref_content(tmp_path: Path) -> None:
    fp1 = voice_fingerprint(_voice(tmp_path, b"refs-v1"))
    fp2 = voice_fingerprint(_voice(tmp_path, b"refs-v2"))
    assert fp1 != fp2
    # 同输入稳定
    assert fp1 == voice_fingerprint(_voice(tmp_path, b"refs-v1"))


def test_path_is_deterministic_and_fingerprint_scoped(tmp_path: Path) -> None:
    cache_a = BackchannelAudioCache(tmp_path, "aaaa1111")
    cache_b = BackchannelAudioCache(tmp_path, "bbbb2222")
    path1 = cache_a.path_for("中性", "……おかえり。")
    assert path1 == cache_a.path_for("中性", "……おかえり。")
    # 声线指纹不同 → 路径不同(声线变更后旧缓存自然失效)
    assert path1 != cache_b.path_for("中性", "……おかえり。")
    # tone 参与寻址(同句不同语气是不同音频)
    assert path1 != cache_a.path_for("不满", "……おかえり。")


def test_store_lookup_roundtrip_survives_source_deletion(tmp_path: Path) -> None:
    cache = BackchannelAudioCache(tmp_path / "audio", "fp")
    source = tmp_path / "synth.wav"
    source.write_bytes(b"wav-bytes")

    assert cache.lookup("中性", "うん。") is None
    stored = cache.store("中性", "うん。", source)
    assert stored is not None and stored.read_bytes() == b"wav-bytes"
    # 必须是复制:provider 播放后会删除源临时文件,缓存须独立存活
    source.unlink()
    assert cache.lookup("中性", "うん。") == stored
    # 幂等:重复 store 直接返回既有文件
    assert cache.store("中性", "うん。", source) == stored


def test_store_failure_degrades_to_none(tmp_path: Path) -> None:
    blocker = tmp_path / "occupied"
    blocker.write_text("file", encoding="utf-8")
    # 根路径被同名文件占据 → mkdir 失败 → 返回 None 不抛(缓存是优化不是依赖)
    cache = BackchannelAudioCache(blocker, "fp")
    source = tmp_path / "synth.wav"
    source.write_bytes(b"wav")
    assert cache.store("中性", "うん。", source) is None
