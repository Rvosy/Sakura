from __future__ import annotations

import random
from pathlib import Path

from app.backchannel.models import (
    BackchannelLabel,
    BackchannelManifest,
    BackchannelTemplate,
    BackchannelVariant,
)
from app.backchannel.resolver import TemplateResolver
from app.backchannel.resolver import SYSTEM_FALLBACK_TEMPLATE_ID


def _variants(*texts: str) -> tuple[BackchannelVariant, ...]:
    return tuple(BackchannelVariant(ja=t, zh=f"{t}-zh") for t in texts)


def _template(
    template_id: str,
    *,
    intent: str | None = None,
    emotion: str | None = None,
    phase: str | None = None,
    texts: tuple[str, ...] = ("a", "b", "c", "d"),
) -> BackchannelTemplate:
    return BackchannelTemplate(
        id=template_id,
        tone="中性",
        portrait="站立待机",
        variants=_variants(*texts),
        intent=intent,
        emotion=emotion,
        phase=phase,
    )


def _manifest(*templates: BackchannelTemplate) -> BackchannelManifest:
    return BackchannelManifest(templates=templates, source_path=Path("test.json"))


_MANIFEST = _manifest(
    _template("fb", intent="fallback", emotion="neutral"),
    _template("err", intent="error", emotion="frustrated"),
    _template("err_angry", intent="error", emotion="angry"),
    _template("repeat", intent="error", emotion="frustrated", phase="repeated_issue"),
    _template("tool", phase="tool_running"),
)


def _resolver(manifest: BackchannelManifest = _MANIFEST) -> TemplateResolver:
    return TemplateResolver(manifest, rng=random.Random(7))


def _label(intent: str, emotion: str = "neutral", confidence: float = 0.8) -> BackchannelLabel:
    return BackchannelLabel(intent, emotion, confidence=confidence)


def test_exact_intent_emotion_match() -> None:
    choice = _resolver().resolve(_label("error", "frustrated"))
    assert choice is not None
    assert choice.template.id == "err"


def test_intent_only_tier_when_emotion_differs() -> None:
    choice = _resolver().resolve(_label("error", "sad"))
    assert choice is not None
    assert choice.template.id in {"err", "err_angry"}


def test_phase_beats_exact_match() -> None:
    # 相位条目优先:repeated_issue 必须覆盖普通 error 条目,否则永远轮不到。
    choice = _resolver().resolve(
        _label("error", "frustrated"), phase="repeated_issue"
    )
    assert choice is not None
    assert choice.template.id == "repeat"


def test_unmatched_phase_falls_through_to_intent() -> None:
    choice = _resolver().resolve(_label("error", "frustrated"), phase="long_wait")
    assert choice is not None
    assert choice.template.id == "err"


def test_pure_phase_entry_needs_no_label() -> None:
    choice = _resolver().resolve(None, phase="tool_running")
    assert choice is not None
    assert choice.template.id == "tool"


def test_none_label_falls_to_fallback_pool() -> None:
    # 闲聊/低置信 → fallback(有意设计,chat 不设标签)。
    choice = _resolver().resolve(None)
    assert choice is not None
    assert choice.template.id == "fb"


def test_manifest_without_fallback_gets_system_fallback() -> None:
    manifest = _manifest(_template("request", intent="request", emotion="neutral"))
    choice = _resolver(manifest).resolve(None)
    assert choice is not None
    assert choice.template.id == SYSTEM_FALLBACK_TEMPLATE_ID
    assert choice.variant.zh in {"嗯……", "啊……", "那个……", "唔……", "我在。", "嗯嗯。"}


def test_character_fallback_beats_system_fallback() -> None:
    manifest = _manifest(
        _template("request", intent="request", emotion="neutral"),
        _template("character_fb", intent="fallback"),
    )
    choice = _resolver(manifest).resolve(None)
    assert choice is not None
    assert choice.template.id == "character_fb"


def test_unknown_intent_falls_to_fallback() -> None:
    choice = _resolver().resolve(_label("question", "confused"))
    assert choice is not None
    assert choice.template.id == "fb"


def test_phase_entries_excluded_from_intent_tiers() -> None:
    # 带 phase 的条目只在对应相位出场,不污染普通意图匹配。
    manifest = _manifest(
        _template("repeat", intent="error", emotion="frustrated", phase="repeated_issue"),
        _template("fb", intent="fallback"),
    )
    choice = _resolver(manifest).resolve(_label("error", "frustrated"))
    assert choice is not None
    assert choice.template.id == "fb"


def test_family_fallback_does_not_pool_sibling_subtypes() -> None:
    # "我回来了"(greeting_return)绝不能抽到 goodnight 子类的"晚安"。
    manifest = _manifest(
        _template("g_root", intent="greeting", emotion="neutral"),
        _template("g_night", intent="greeting_goodnight", emotion="neutral"),
        _template("fb", intent="fallback"),
    )
    resolver = _resolver(manifest)
    for _ in range(8):
        choice = resolver.resolve(_label("greeting_return", "neutral"))
        assert choice is not None
        # 子类无精确模板 → 回退家族根 greeting,而非兄弟子类
        assert choice.template.id == "g_root"


def test_subtype_exact_match_beats_family_root() -> None:
    manifest = _manifest(
        _template("g_root", intent="greeting", emotion="neutral"),
        _template("g_return", intent="greeting_return", emotion="neutral"),
    )
    choice = _resolver(manifest).resolve(_label("greeting_return", "neutral"))
    assert choice is not None
    assert choice.template.id == "g_return"


def test_low_confidence_label_uses_fallback() -> None:
    manifest = _manifest(
        _template("request", intent="request", emotion="neutral"),
        _template("fb", intent="fallback"),
    )
    choice = _resolver(manifest).resolve(
        _label("request", "neutral", confidence=0.2)
    )
    assert choice is not None
    assert choice.template.id == "fb"


def test_phase_still_beats_low_confidence_label() -> None:
    manifest = _manifest(
        _template("phase", intent=None, emotion=None, phase="tool_running"),
        _template("fb", intent="fallback"),
    )
    choice = _resolver(manifest).resolve(
        _label("request", "neutral", confidence=0.2),
        phase="tool_running",
    )
    assert choice is not None
    assert choice.template.id == "phase"


def test_anti_repetition_no_consecutive_variant() -> None:
    resolver = _resolver()
    label = _label("error", "frustrated")
    previous: str | None = None
    for _ in range(12):
        choice = resolver.resolve(label)
        assert choice is not None
        assert choice.variant.ja != previous, "同一变体不应连续出现"
        previous = choice.variant.ja


def test_anti_repetition_relaxes_when_pool_exhausted() -> None:
    # 变体池小于防重复窗口时放开限制,保证仍有输出。
    manifest = _manifest(_template("fb", intent="fallback", texts=("only",)))
    resolver = _resolver(manifest)
    for _ in range(3):
        choice = resolver.resolve(None)
        assert choice is not None
        assert choice.variant.ja == "only"


def test_empty_manifest_returns_none() -> None:
    assert _resolver(_manifest()).resolve(_label("error")) is None
    assert not _manifest()
