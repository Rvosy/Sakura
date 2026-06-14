from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.backchannel.models import (
    EMOTIONS,
    FALLBACK_INTENT,
    INTENTS,
    PHASES,
    BackchannelManifest,
    BackchannelTemplate,
    BackchannelVariant,
)
from app.core.debug_log import debug_log


class BackchannelManifestError(RuntimeError):
    """manifest 文件缺失、JSON 非法或顶层结构错误。"""


@runtime_checkable
class _CharacterVocabulary(Protocol):
    """校验所需的角色词表子集(结构化鸭子类型,避免依赖完整 CharacterProfile)。"""

    expression_portraits: dict[str, Path]
    reply_tones: list[str]


def load_backchannel_manifest(
    path: Path,
    profile: _CharacterVocabulary | None = None,
) -> BackchannelManifest:
    """加载并校验角色接话模板清单。

    容错策略:单个条目非法只跳过该条目并记 debug 日志,不让一条坏数据
    炸掉整个功能;文件级错误(缺失/JSON 非法/顶层结构错)抛
    BackchannelManifestError,由调用方决定降级。

    传入 profile 时:portrait 严格校验(不在角色表情词表 → 跳过条目);
    tone 宽松校验(不在 reply_tones 仅警告——TTS 对未知 tone 会回退中性参考,
    且参考音频词表可能比 reply_tones 更宽,如 sakura 的"困惑")。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackchannelManifestError(f"接话清单不存在:{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BackchannelManifestError(f"接话清单读取失败:{path}({exc})") from exc

    if not isinstance(raw, dict):
        raise BackchannelManifestError(f"接话清单顶层必须是对象:{path}")
    raw_templates = raw.get("templates")
    if not isinstance(raw_templates, list):
        raise BackchannelManifestError(f"接话清单缺少 templates 数组:{path}")

    templates: list[BackchannelTemplate] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw_templates):
        template = _parse_template(entry, index, path, profile)
        if template is None:
            continue
        if template.id in seen_ids:
            _skip(path, template.id, "id 重复,保留首条")
            continue
        seen_ids.add(template.id)
        templates.append(template)

    return BackchannelManifest(
        templates=tuple(templates),
        character_id=str(raw.get("character_id", "")).strip(),
        source_path=path,
    )


def _parse_template(
    entry: Any,
    index: int,
    path: Path,
    profile: _CharacterVocabulary | None,
) -> BackchannelTemplate | None:
    if not isinstance(entry, dict):
        _skip(path, f"#{index}", "条目必须是对象")
        return None
    entry_id = str(entry.get("id", "")).strip()
    if not entry_id:
        _skip(path, f"#{index}", "缺少 id")
        return None

    phase = _optional_label(entry, "phase")
    intent = _optional_label(entry, "intent")
    emotion = _optional_label(entry, "emotion")

    if phase is not None and phase not in PHASES:
        _skip(path, entry_id, f"非法 phase:{phase}")
        return None
    if intent is not None and intent not in INTENTS and intent != FALLBACK_INTENT:
        _skip(path, entry_id, f"intent 不在词表:{intent}")
        return None
    if emotion is not None and emotion not in EMOTIONS:
        _skip(path, entry_id, f"emotion 不在词表:{emotion}")
        return None
    if phase is None and intent is None:
        # 既无相位也无意图的条目永远不会被 resolver 选中(死条目)。
        _skip(path, entry_id, "既无 phase 也无 intent,不可达")
        return None

    tone = str(entry.get("tone", "")).strip()
    portrait = str(entry.get("portrait", "")).strip()
    if not tone or not portrait:
        _skip(path, entry_id, "缺少 tone 或 portrait")
        return None
    if profile is not None:
        if portrait not in profile.expression_portraits:
            _skip(path, entry_id, f"portrait 不在角色词表:{portrait}")
            return None
        if profile.reply_tones and tone not in profile.reply_tones:
            debug_log(
                "Backchannel",
                "接话模板 tone 不在 reply_tones,保留(TTS 未知 tone 回退中性参考)",
                {"manifest": str(path), "id": entry_id, "tone": tone},
            )

    variants = _parse_variants(entry.get("variants"), entry_id, path)
    if not variants:
        _skip(path, entry_id, "没有可用变体")
        return None

    return BackchannelTemplate(
        id=entry_id,
        tone=tone,
        portrait=portrait,
        variants=variants,
        intent=intent,
        emotion=emotion,
        phase=phase,
    )


def _parse_variants(
    raw: Any,
    entry_id: str,
    path: Path,
) -> tuple[BackchannelVariant, ...]:
    if not isinstance(raw, list):
        return ()
    variants: list[BackchannelVariant] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _skip(path, f"{entry_id}[{index}]", "变体必须是对象")
            continue
        ja = str(item.get("ja", "")).strip()
        zh = str(item.get("zh", "")).strip()
        if not ja or not zh:
            # ja/zh 必须成对:音频从 ja 合成而字幕可能显示 zh,缺一侧即破坏对应关系。
            _skip(path, f"{entry_id}[{index}]", "ja/zh 必须成对且非空")
            continue
        audio_raw = item.get("audio")
        audio = str(audio_raw).strip() if isinstance(audio_raw, str) and audio_raw.strip() else None
        variants.append(BackchannelVariant(ja=ja, zh=zh, audio=audio))
    return tuple(variants)


def _optional_label(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _skip(path: Path, entry_id: str, reason: str) -> None:
    debug_log(
        "Backchannel",
        "接话模板条目已跳过",
        {"manifest": str(path), "id": entry_id, "reason": reason},
    )
