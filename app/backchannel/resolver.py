from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from app.backchannel.models import (
    BackchannelLabel,
    BackchannelManifest,
    BackchannelTemplate,
    BackchannelVariant,
)

# 防重复记忆窗口:最近 N 个变体不再被选中。
# 依据:filler 的缓和效果随重复使用衰减,所以短期内轮换变体。
DEFAULT_RECENT_LIMIT = 3
MIN_DIRECT_CONFIDENCE = 0.55
SYSTEM_FALLBACK_TEMPLATE_ID = "__system_fallback__"
SYSTEM_FALLBACK_TEMPLATE = BackchannelTemplate(
    id=SYSTEM_FALLBACK_TEMPLATE_ID,
    tone="中性",
    portrait="站立待机",
    intent="fallback",
    variants=(
        BackchannelVariant(ja="うん……", zh="嗯……"),
        BackchannelVariant(ja="あ……", zh="啊……"),
        BackchannelVariant(ja="えっと……", zh="那个……"),
        BackchannelVariant(ja="うーん……", zh="唔……"),
        BackchannelVariant(ja="いるよ。", zh="我在。"),
        BackchannelVariant(ja="うんうん。", zh="嗯嗯。"),
    ),
)


@dataclass(frozen=True)
class BackchannelChoice:
    """resolver 的输出:选中的模板(tone/portrait)+ 具体变体(文本/音频)。"""

    template: BackchannelTemplate
    variant: BackchannelVariant


class TemplateResolver:
    """模板匹配 + 防重复轮换。

    匹配优先级:
      1. phase 命中(相位条目优先,如 repeated_issue 覆盖普通 error 条目)
      2. (intent, emotion) 精确命中
      3. 同 intent 命中(emotion 不同——窄词表下让模板尽量可用)
      4. 意图家族根命中(如 greeting_return 无模板时落 greeting;
         绝不混抽同家族其他子类——"我回来了"不能抽到"晚安")
      5. fallback 兜底池(闲聊与低置信输入有意落在这里)
    任何一级命中即在该级的全部变体池中防重复随机选取。
    """

    def __init__(
        self,
        manifest: BackchannelManifest,
        *,
        rng: random.Random | None = None,
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        min_direct_confidence: float = MIN_DIRECT_CONFIDENCE,
    ) -> None:
        self._templates = self._templates_with_system_fallback(manifest)
        self._rng = rng if rng is not None else random.Random()
        self._recent: deque[tuple[str, int]] = deque(maxlen=max(1, recent_limit))
        self._min_direct_confidence = max(0.0, min(1.0, min_direct_confidence))

    def _templates_with_system_fallback(
        self,
        manifest: BackchannelManifest,
    ) -> tuple[BackchannelTemplate, ...]:
        # 完全空清单仍表示 opt-out/无可用条目,不注入系统 fallback。
        if not manifest.templates or manifest.fallback_templates:
            return manifest.templates
        return (*manifest.templates, SYSTEM_FALLBACK_TEMPLATE)

    def resolve(
        self,
        label: BackchannelLabel | None,
        *,
        phase: str | None = None,
    ) -> BackchannelChoice | None:
        for tier in self._match_tiers(label, phase):
            if tier:
                return self._pick(tier)
        return None

    def _match_tiers(
        self,
        label: BackchannelLabel | None,
        phase: str | None,
    ) -> list[list[BackchannelTemplate]]:
        tiers: list[list[BackchannelTemplate]] = []
        if phase is not None:
            tiers.append([t for t in self._templates if t.phase == phase])
        if label is not None and label.confidence >= self._min_direct_confidence:
            # 相位条目不参与意图匹配(带 phase 的条目只在对应相位出场)。
            plain = [t for t in self._templates if t.phase is None]
            tiers.append(
                [t for t in plain if t.intent == label.intent and t.emotion == label.emotion]
            )
            tiers.append([t for t in plain if t.intent == label.intent])
            # 家族回退:子类意图(含下划线)落到家族根,不混抽其他子类。
            if "_" in label.intent:
                family = label.intent.split("_", 1)[0]
                tiers.append([t for t in plain if t.intent == family])
        tiers.append([t for t in self._templates if t.is_fallback and t.phase is None])
        return tiers

    def _pick(self, templates: list[BackchannelTemplate]) -> BackchannelChoice | None:
        pool = [
            (template, variant, (template.id, index))
            for template in templates
            for index, variant in enumerate(template.variants)
        ]
        if not pool:
            return None
        fresh = [item for item in pool if item[2] not in self._recent]
        # 变体池太小、全部都在最近窗口内时放开限制,保证仍有输出。
        candidates = fresh or pool
        template, variant, key = self._rng.choice(candidates)
        self._recent.append(key)
        return BackchannelChoice(template=template, variant=variant)
