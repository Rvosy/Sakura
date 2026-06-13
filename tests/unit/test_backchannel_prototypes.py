from __future__ import annotations

import json
from pathlib import Path

from app.backchannel.prototypes import (
    IntentPrototype,
    load_intent_prototypes,
    load_intent_prototypes_from_paths,
    prototypes_by_intent,
)


def _write(path: Path, entries: dict[str, object]) -> None:
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")


def test_load_intent_prototypes_filters_invalid_entries(tmp_path: Path) -> None:
    path = tmp_path / "intent_prototypes.json"
    _write(
        path,
        {
            "request": [
                {"text": "帮我整理一下", "source": "seed"},
                {"text": "帮我整理一下", "source": "duplicate"},
                "帮我查天气",
                {"text": "", "source": "empty"},
            ],
            "not_an_intent": [{"text": "跳过"}],
            "support": "bad shape",
        },
    )

    prototypes = load_intent_prototypes(path)

    assert prototypes == (
        IntentPrototype(intent="request", text="帮我整理一下", source="seed"),
        IntentPrototype(intent="request", text="帮我查天气", source="seed"),
    )


def test_load_intent_prototypes_from_paths_merges_in_order(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.json"
    starter = tmp_path / "starter.json"
    _write(overlay, {"support": [{"text": "我有点撑不住", "source": "local"}]})
    _write(
        starter,
        {
            "support": [{"text": "我有点撑不住", "source": "seed"}],
            "positive": [{"text": "终于成功了", "source": "seed"}],
        },
    )

    grouped = prototypes_by_intent(load_intent_prototypes_from_paths((overlay, starter)))

    assert grouped == {
        "support": ("我有点撑不住",),
        "positive": ("终于成功了",),
    }
