from __future__ import annotations

import csv
import json
from pathlib import Path

from app.backchannel.prototype_builder import build_intent_prototypes_from_files
from app.backchannel.prototypes import load_intent_prototypes, local_intent_prototypes_path


def test_build_intent_prototypes_from_mixed_local_files(tmp_path: Path) -> None:
    massive = tmp_path / "massive.jsonl"
    massive.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                {"utt": "星期五早上九点叫醒我", "intent": "alarm_set"},
                {"utt": "你好呀", "intent": "general_greet"},
                {"utt": "这个词是什么意思", "intent": "qa_definition"},
            )
        ),
        encoding="utf-8",
    )

    cped = tmp_path / "cped.csv"
    with cped.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("text", "emotion", "dialogue_act"))
        writer.writeheader()
        writer.writerow({"text": "我今天有点撑不住了", "emotion": "sad", "dialogue_act": ""})
        writer.writerow({"text": "谢谢你一直陪我", "emotion": "", "dialogue_act": "appreciation"})

    direct = tmp_path / "direct.json"
    direct.write_text(
        json.dumps(
            {
                "rows": [
                    {"text": "想抱抱你", "sakura_intent": "affection"},
                    {"text": "not chinese", "sakura_intent": "request"},
                    {"text": "想抱抱你", "sakura_intent": "affection"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_intent_prototypes_from_files((massive, cped, direct), base_dir=tmp_path)

    assert result.output_path == local_intent_prototypes_path(tmp_path)
    assert result.counts == {
        "question": 1,
        "request": 1,
        "support": 1,
        "positive": 1,
        "affection": 1,
        "greeting": 1,
    }
    loaded = load_intent_prototypes(result.output_path)
    assert {(prototype.intent, prototype.text) for prototype in loaded} == {
        ("request", "星期五早上九点叫醒我"),
        ("greeting", "你好呀"),
        ("question", "这个词是什么意思"),
        ("support", "我今天有点撑不住了"),
        ("positive", "谢谢你一直陪我"),
        ("affection", "想抱抱你"),
    }


def test_build_intent_prototypes_filters_and_limits(tmp_path: Path) -> None:
    path = tmp_path / "rows.json"
    path.write_text(
        json.dumps(
            [
                {"text": "帮我总结一下", "sakura_intent": "request"},
                {"text": "帮我翻译一下", "sakura_intent": "request"},
                {"text": "帮我打开文件", "sakura_intent": "request"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_intent_prototypes_from_files((path,), base_dir=tmp_path, limit_per_intent=2)

    assert result.counts == {"request": 2}
    assert [prototype.text for prototype in load_intent_prototypes(result.output_path)] == [
        "帮我总结一下",
        "帮我翻译一下",
    ]
