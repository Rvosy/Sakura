from __future__ import annotations

import json
from pathlib import Path

from app.backchannel.eval_log import BackchannelEvalLogger, backchannel_eval_log_path
from app.backchannel.models import (
    BackchannelLabel,
    BackchannelTemplate,
    BackchannelVariant,
)
from app.backchannel.resolver import BackchannelChoice


def _choice() -> BackchannelChoice:
    template = BackchannelTemplate(
        id="err",
        tone="不满",
        portrait="不满无语",
        variants=(BackchannelVariant(ja="見てみる。", zh="我看看。"),),
        intent="error",
        emotion="frustrated",
    )
    return BackchannelChoice(template, template.variants[0])


def test_disabled_logger_writes_nothing(tmp_path: Path) -> None:
    logger = BackchannelEvalLogger(tmp_path, enabled=False)
    logger.log("text", BackchannelLabel("error", "frustrated", 0.9), _choice(), mode="hybrid")
    assert not backchannel_eval_log_path(tmp_path).exists()


def test_enabled_logger_appends_jsonl_record(tmp_path: Path) -> None:
    logger = BackchannelEvalLogger(tmp_path, enabled=True)
    logger.log("报错了", BackchannelLabel("error", "frustrated", 0.87), _choice(), mode="hybrid")
    logger.log("今天天气不错", None, None, mode="rules")

    rows = [
        json.loads(line)
        for line in backchannel_eval_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["text"] == "报错了"
    assert rows[0]["intent"] == "error"
    assert rows[0]["confidence"] == 0.87
    assert rows[0]["template"] == "err"
    assert rows[0]["gold_intent"] is None  # 待人工标注
    assert rows[1]["intent"] is None and rows[1]["template"] is None


def test_set_enabled_toggles_writing(tmp_path: Path) -> None:
    logger = BackchannelEvalLogger(tmp_path, enabled=False)
    logger.log("x", None, None, mode="rules")
    assert not backchannel_eval_log_path(tmp_path).exists()
    logger.set_enabled(True)
    logger.log("y", None, None, mode="rules")
    assert backchannel_eval_log_path(tmp_path).exists()
