from __future__ import annotations

import json

import numpy as np

from app.backchannel.probe_classifier import ProbeIntentClassifier


class EncoderStub:
    """固定返回给定向量的句向量桩(每句一行)。"""

    def __init__(self, vector) -> None:
        self.vector = np.asarray(vector, dtype="float32")

    def encode(self, sentences, **kwargs):
        return np.asarray([self.vector for _ in sentences], dtype="float32")


def _write_head(tmp_path, coef, intercept, labels, threshold=0.5):
    head = tmp_path / "head.npz"
    np.savez(
        head,
        coef=np.asarray(coef, dtype="float32"),
        intercept=np.asarray(intercept, dtype="float32"),
        labels=np.asarray(labels).astype("U16"),
    )
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"threshold": threshold}), encoding="utf-8")
    return head, meta


def _clf(tmp_path, vector, *, coef, intercept, labels, threshold=0.5):
    head, meta = _write_head(tmp_path, coef, intercept, labels, threshold)
    return ProbeIntentClassifier(
        head_path=head, meta_path=meta, encoder=EncoderStub(vector)
    )


def test_probe_picks_argmax_above_threshold(tmp_path):
    clf = _clf(
        tmp_path, [1.0, 0.0],
        coef=[[5.0, 0.0], [0.0, 5.0]], intercept=[0.0, 0.0],
        labels=["support", "complaint"], threshold=0.5,
    )
    result = clf.classify_intent("我好难过")
    assert result is not None
    assert result[0] == "support"
    assert result[1] > 0.5


def test_probe_abstains_below_threshold(tmp_path):
    # 向量与两类等距 → 概率 ~0.5,阈值 0.9 → 弃权
    clf = _clf(
        tmp_path, [1.0, 1.0],
        coef=[[1.0, 0.0], [0.0, 1.0]], intercept=[0.0, 0.0],
        labels=["support", "complaint"], threshold=0.9,
    )
    assert clf.classify_intent("说点什么") is None


def test_probe_abstains_on_none_class(tmp_path):
    # argmax 落在 none 类 → 弃权(无视阈值)
    clf = _clf(
        tmp_path, [1.0, 0.0],
        coef=[[5.0, 0.0], [0.0, 5.0]], intercept=[0.0, 0.0],
        labels=["none", "support"], threshold=0.1,
    )
    assert clf.classify_intent("嗯") is None


def test_probe_graceful_when_head_missing(tmp_path):
    clf = ProbeIntentClassifier(
        head_path=tmp_path / "nope.npz",
        meta_path=tmp_path / "nope.json",
        encoder=EncoderStub([1.0, 0.0]),
    )
    assert clf.classify_intent("任意输入") is None
    assert clf.available is False


def test_probe_empty_text_returns_none(tmp_path):
    clf = _clf(
        tmp_path, [1.0, 0.0],
        coef=[[5.0, 0.0], [0.0, 5.0]], intercept=[0.0, 0.0],
        labels=["support", "complaint"],
    )
    assert clf.classify_intent("   ") is None


def test_probe_preload_uses_threshold_from_meta(tmp_path):
    head, meta = _write_head(
        tmp_path, [[5.0, 0.0], [0.0, 5.0]], [0.0, 0.0],
        ["support", "complaint"], threshold=0.995,
    )
    clf = ProbeIntentClassifier(head_path=head, meta_path=meta, encoder=EncoderStub([1.0, 0.0]))
    clf.preload()
    # 阈值 0.995,而 [1,0] 的最高概率约 0.993 < 0.995 → 弃权
    assert clf.classify_intent("我好难过") is None
