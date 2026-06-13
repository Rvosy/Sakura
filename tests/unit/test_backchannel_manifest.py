from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.backchannel.manifest import BackchannelManifestError, load_backchannel_manifest


@dataclass
class _StubProfile:
    """manifest 校验只需要角色词表子集(鸭子类型,见 _CharacterVocabulary)。"""

    expression_portraits: dict[str, Path] = field(
        default_factory=lambda: {"站立待机": Path("a.png"), "张嘴疑问": Path("b.png")}
    )
    reply_tones: list[str] = field(default_factory=lambda: ["中性", "请求"])


def _write_manifest(tmp_path: Path, templates: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": 1, "character_id": "sakura", "templates": templates}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _entry(**overrides) -> dict:
    entry = {
        "id": "q_confused",
        "intent": "question",
        "emotion": "confused",
        "tone": "中性",
        "portrait": "张嘴疑问",
        "variants": [{"ja": "見てみる。", "zh": "我看看。", "audio": None}],
    }
    entry.update(overrides)
    return entry


def test_load_valid_manifest(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            _entry(),
            _entry(id="fb", intent="fallback", emotion="neutral"),
            # 纯相位条目:无 intent/emotion 也合法
            _entry(id="tool", intent=None, emotion=None, phase="tool_running"),
        ],
    )
    manifest = load_backchannel_manifest(path)
    assert manifest.character_id == "sakura"
    assert [t.id for t in manifest.templates] == ["q_confused", "fb", "tool"]
    assert manifest.templates[2].phase == "tool_running"
    assert manifest.templates[2].intent is None
    assert [t.id for t in manifest.fallback_templates] == ["fb"]
    variant = manifest.templates[0].variants[0]
    assert (variant.ja, variant.zh, variant.audio) == ("見てみる。", "我看看。", None)


def test_variant_display_text_language_fallback(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_entry()])
    variant = load_backchannel_manifest(path).templates[0].variants[0]
    assert variant.display_text("zh") == "我看看。"
    assert variant.display_text("ja") == "見てみる。"


def test_skip_unpaired_variant_keeps_rest(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            _entry(
                variants=[
                    {"ja": "見てみる。", "zh": "我看看。"},
                    {"ja": "只有日文"},  # 缺 zh,破坏音频↔字幕配对 → 跳过
                ]
            )
        ],
    )
    manifest = load_backchannel_manifest(path)
    assert len(manifest.templates[0].variants) == 1


def test_skip_template_without_valid_variants(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_entry(variants=[{"ja": "只有日文"}])])
    assert not load_backchannel_manifest(path).templates


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "tool"},  # 假 intent(相位词)不在词表
        {"emotion": "excited"},
        {"phase": "unknown_phase"},
        {"intent": None, "emotion": None},  # 既无 phase 也无 intent → 不可达
        {"id": ""},
        {"tone": ""},
        {"portrait": ""},
    ],
)
def test_skip_invalid_entries(tmp_path: Path, overrides: dict) -> None:
    path = _write_manifest(tmp_path, [_entry(**overrides)])
    assert not load_backchannel_manifest(path).templates


def test_duplicate_id_keeps_first(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [_entry(tone="中性"), _entry(tone="请求")],
    )
    manifest = load_backchannel_manifest(path)
    assert len(manifest.templates) == 1
    assert manifest.templates[0].tone == "中性"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(BackchannelManifestError):
        load_backchannel_manifest(tmp_path / "absent.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BackchannelManifestError):
        load_backchannel_manifest(path)


def test_missing_templates_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(BackchannelManifestError):
        load_backchannel_manifest(path)


def test_profile_unknown_portrait_skips_entry(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_entry(portrait="不存在的立绘")])
    assert not load_backchannel_manifest(path, profile=_StubProfile()).templates


def test_profile_unknown_tone_warns_but_keeps_entry(tmp_path: Path) -> None:
    # tone 宽松校验:角色参考音频词表可能比 reply_tones 宽(如"困惑"),
    # 且 TTS 对未知 tone 回退中性参考,不应整条丢弃。
    path = _write_manifest(tmp_path, [_entry(tone="困惑")])
    manifest = load_backchannel_manifest(path, profile=_StubProfile())
    assert len(manifest.templates) == 1
    assert manifest.templates[0].tone == "困惑"


def test_load_representative_manifest(tmp_path: Path) -> None:
    """框架级自检:样例清单应整体可加载且无条目/变体被静默跳过。"""
    templates = [
        _entry(id="fb", intent="fallback", emotion="neutral"),
        _entry(id="greeting_return", intent="greeting_return", emotion="neutral"),
        _entry(id="support_sad", intent="support", emotion="sad"),
        _entry(id="repeat", intent="error", emotion="frustrated", phase="repeated_issue"),
        _entry(id="tool", intent=None, emotion=None, phase="tool_running"),
        _entry(id="wait", intent=None, emotion=None, phase="long_wait"),
    ]
    path = _write_manifest(tmp_path, templates)
    manifest = load_backchannel_manifest(path)
    assert len(manifest.templates) == len(templates)
    assert sum(len(t.variants) for t in manifest.templates) == sum(
        len(entry["variants"]) for entry in templates
    )
    assert {t.phase for t in manifest.templates if t.phase} == {
        "repeated_issue",
        "tool_running",
        "long_wait",
    }
    assert manifest.fallback_templates, "兜底池不能为空"
