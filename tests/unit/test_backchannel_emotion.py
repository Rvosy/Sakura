from __future__ import annotations

import json
from pathlib import Path

from app.backchannel.emotion import EmotionScorer, load_emotion_lexicon
from app.backchannel.models import EMOTIONS


def test_packaged_lexicon_loads_and_stays_inside_vocabulary() -> None:
    lexicon = load_emotion_lexicon()
    assert lexicon, "随包词典必须可加载"
    assert set(lexicon) <= set(EMOTIONS)
    assert all(weight > 0 for words in lexicon.values() for weight in words.values())


def test_single_strong_word_passes_threshold() -> None:
    scorer = EmotionScorer()
    assert scorer.best("气死我了") == "angry"
    assert scorer.best("呜呜,想哭") == "sad"


def test_weak_signals_accumulate() -> None:
    # 单个弱信号(0.5/0.8)不过阈值,叠加后通过——打分制相对首个命中的核心差异。
    scorer = EmotionScorer(
        {"sad": {"好累": 0.8, "唉": 1.0}},
    )
    assert scorer.best("好累") is None
    assert scorer.best("唉,好累") == "sad"


def test_longest_match_suppresses_substring() -> None:
    # 「不开心」(sad)命中时,其子串「开心」(happy)不得计分。
    scorer = EmotionScorer(
        {"happy": {"开心": 1.0}, "sad": {"不开心": 1.5}},
    )
    assert scorer.best("今天不开心") == "sad"
    assert scorer.best("今天很开心") == "happy"


def test_same_word_counts_once() -> None:
    scorer = EmotionScorer({"happy": {"哈哈": 0.8}})
    # "哈哈哈哈" 只算一次 0.8,不过 1.0 阈值
    assert scorer.best("哈哈哈哈") is None


def test_no_signal_returns_none_and_empty_scores() -> None:
    scorer = EmotionScorer()
    assert scorer.scores("帮我查个东西") == {}
    assert scorer.best("") is None


def test_emoji_entries_match() -> None:
    scorer = EmotionScorer()
    assert scorer.best("今天好惨 😭") == "sad"


def test_broken_lexicon_degrades_to_idle(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_emotion_lexicon(path) == {}
    # 非法词条(未知情绪/非法权重)被跳过
    path2 = tmp_path / "partial.json"
    path2.write_text(
        json.dumps(
            {"entries": {"excited": {"哇": 1.0}, "sad": {"难过": "abc", "想哭": 2.0}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lexicon = load_emotion_lexicon(path2)
    assert lexicon == {"sad": {"想哭": 2.0}}
