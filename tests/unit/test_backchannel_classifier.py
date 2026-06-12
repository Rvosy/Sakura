from __future__ import annotations

import pytest

from app.backchannel.classifier import RuleClassifier
from app.backchannel.models import EMOTIONS, INTENTS


@pytest.fixture()
def classifier() -> RuleClassifier:
    return RuleClassifier()


@pytest.mark.parametrize(
    ("text", "intent", "emotion"),
    [
        ("这个报错怎么回事", "error", "frustrated"),
        ("跑不起来了,还是报错", "error", "frustrated"),
        ("```\nTraceback (most recent call last):\n```", "error", "frustrated"),
        ("怎么又失败了!!", "error", "frustrated"),
        ("烦死了,这破东西真难用", "complaint", "angry"),
        ("今天好累,心情不好", "support", "sad"),
        ("最喜欢你了,贴贴", "affection", "embarrassed"),
        ("帮我搜一下明天的天气", "request", "neutral"),
        ("这是什么意思?", "question", "confused"),
        ("为什么会这样?", "question", "confused"),
        ("跑通了!太好了!!", "positive", "happy"),
    ],
)
def test_classify_representative_inputs(
    classifier: RuleClassifier, text: str, intent: str, emotion: str
) -> None:
    label = classifier.classify(text)
    assert label is not None
    assert label.intent == intent
    assert label.emotion == emotion
    assert 0.0 < label.confidence <= 0.9


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("我回来了", "greeting_return"),
        ("到家啦", "greeting_return"),
        ("早上好~", "greeting_morning"),
        ("早安", "greeting_morning"),
        ("晚上好", "greeting_evening"),
        ("晚安", "greeting_goodnight"),
        ("我先睡了", "greeting_goodnight"),
        ("在吗?", "greeting"),
        ("你好呀", "greeting"),
    ],
)
def test_greeting_family_short_circuit(
    classifier: RuleClassifier, text: str, intent: str
) -> None:
    # 程式化社交句是封闭集,短输入直接短路,不被疑问词/问号信号抢走("在吗?")。
    label = classifier.classify(text)
    assert label is not None
    assert label.intent == intent
    assert label.emotion == "neutral"


def test_long_text_with_task_signal_beats_greeting(classifier: RuleClassifier) -> None:
    # 长句中问候只是开场白,任务意图应胜出。
    label = classifier.classify("我回来了,帮我搜一下今天的新闻")
    assert label is not None
    assert label.intent == "request"


@pytest.mark.parametrize(
    "text",
    [
        "在吗帮我查天气",
        "我回来了帮我查天气",
        "到家啦好累",
    ],
)
def test_greeting_must_be_complete_social_act(classifier: RuleClassifier, text: str) -> None:
    label = classifier.classify(text)
    assert label is not None
    assert label.intent != "greeting"
    assert not label.intent.startswith("greeting_")


@pytest.mark.parametrize("text", ["今天好热呢", "这个颜色很怪呢"])
def test_sentence_final_particles_do_not_create_question_signal(
    classifier: RuleClassifier,
    text: str,
) -> None:
    assert classifier.classify(text) is None


@pytest.mark.parametrize("text", ["", "   ", "今天天气不错"])
def test_no_signal_returns_none(classifier: RuleClassifier, text: str) -> None:
    # 无可靠信号 → None,由 resolver 落兜底池(闲聊有意走 fallback,FEAT.md §9)。
    assert classifier.classify(text) is None


def test_question_mark_alone_is_question(classifier: RuleClassifier) -> None:
    label = classifier.classify("这样也行?")
    assert label is not None
    assert label.intent == "question"


def test_more_hits_raise_confidence(classifier: RuleClassifier) -> None:
    weak = classifier.classify("失败了")
    strong = classifier.classify("报错了,又失败,跑不起来")
    assert weak is not None and strong is not None
    assert strong.confidence > weak.confidence


def test_labels_stay_inside_vocabulary(classifier: RuleClassifier) -> None:
    """词表对齐硬约束:分类器输出必须落在 models 词表内。"""
    samples = [
        "报错了!!", "烦死了", "好累想哭", "喜欢你~", "帮我查查",
        "为什么呢?", "搞定,太好了", "急死了,deadline 来不及了", "嘿嘿 233",
    ]
    for text in samples:
        label = classifier.classify(text)
        if label is None:
            continue
        assert label.intent in INTENTS
        assert label.emotion in EMOTIONS
