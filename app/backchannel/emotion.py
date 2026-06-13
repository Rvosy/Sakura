from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.backchannel.models import EMOTIONS
from app.core.debug_log import debug_log

# 情感打分制:从"首个信号命中即采用"升级为累计打分。
# 词典走子串匹配 + 最长匹配压制(「不开心」命中时压住其子串「开心」),
# argmax 过阈值才输出;低于阈值由调用方(分类器)回退意图缺省映射。
DEFAULT_EMOTION_THRESHOLD = 1.0

_LEXICON_PATH = Path(__file__).resolve().parent / "data" / "emotion_lexicon.json"


# maxsize=2:默认随包词典 + 测试/将来替换词典注入的自定义路径可并存不互逐
@lru_cache(maxsize=2)
def load_emotion_lexicon(path: Path = _LEXICON_PATH) -> dict[str, dict[str, float]]:
    """加载情感词典;非法/缺失时返回空表(打分器空转,分类器走缺省映射)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("entries 必须是对象")
    except Exception as exc:  # noqa: BLE001
        debug_log("Backchannel", "情感词典加载失败,打分器空转", {"path": str(path), "error": str(exc)})
        return {}
    lexicon: dict[str, dict[str, float]] = {}
    for emotion, words in entries.items():
        if emotion not in EMOTIONS or not isinstance(words, dict):
            debug_log("Backchannel", "情感词典条目已跳过", {"emotion": emotion})
            continue
        cleaned: dict[str, float] = {}
        for word, weight in words.items():
            text = str(word).strip()
            try:
                value = float(weight)
            except (TypeError, ValueError):
                continue
            if text and value > 0:
                cleaned[text] = value
        if cleaned:
            lexicon[emotion] = cleaned
    return lexicon


class EmotionScorer:
    """词典累计打分的情绪识别(纯规则,零模型依赖)。

    返回的是"用户情绪",与角色回应语气(模板 tone)是两个概念。
    """

    def __init__(
        self,
        lexicon: dict[str, dict[str, float]] | None = None,
        *,
        threshold: float = DEFAULT_EMOTION_THRESHOLD,
    ) -> None:
        self._lexicon = lexicon if lexicon is not None else load_emotion_lexicon()
        self._threshold = threshold

    def scores(self, text: str) -> dict[str, float]:
        """各情绪累计得分。同一词只计一次;被更长命中词包含的子串词不计分。

        性能边界:暴力子串匹配,词典 ~150 条对短输入 <1ms;
        词典扩到数千条(如 DUTIR 裁剪版)时应换 Aho-Corasick。
        """
        content = (text or "").strip()
        if not content or not self._lexicon:
            return {}
        matched: list[tuple[str, str, float]] = [
            (word, emotion, weight)
            for emotion, words in self._lexicon.items()
            for word, weight in words.items()
            if word in content
        ]
        if not matched:
            return {}
        # 最长匹配压制:「不开心」(sad)命中时,其子串「开心」(happy)不计分。
        words_hit = [word for word, _, _ in matched]
        result: dict[str, float] = {}
        for word, emotion, weight in matched:
            if any(word != other and word in other for other in words_hit):
                continue
            result[emotion] = result.get(emotion, 0.0) + weight
        return result

    def best(self, text: str) -> str | None:
        """过阈值的最高分情绪;无可靠信号返回 None。平分按词表固定顺序决胜。"""
        scores = self.scores(text)
        if not scores:
            return None
        emotion = max(scores, key=lambda key: (scores[key], -EMOTIONS.index(key)))
        if scores[emotion] < self._threshold:
            return None
        return emotion
