from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.backchannel.models import BackchannelLabel
from app.backchannel.resolver import BackchannelChoice
from app.core.debug_log import debug_log

# 接话评测日志:记录每次分类的(输入, 预测标签, 选中模板),供事后人工
# 标注 gold_intent 后用 tuning.sweep 网格调参。默认关闭,仅 debug 时落盘;
# 内容是用户自己的输入,永不上传。
EVAL_LOG_RELATIVE_PATH = Path("data") / "backchannel_eval.jsonl"


def backchannel_eval_log_path(base_dir: Path) -> Path:
    return Path(base_dir) / EVAL_LOG_RELATIVE_PATH


class BackchannelEvalLogger:
    """把分类轨迹追加到本地 jsonl。enabled=False 时彻底空转。"""

    def __init__(self, base_dir: Path, *, enabled: bool) -> None:
        self._path = backchannel_eval_log_path(base_dir)
        self._enabled = bool(enabled)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def log(
        self,
        text: str,
        label: BackchannelLabel | None,
        choice: BackchannelChoice | None,
        *,
        mode: str,
    ) -> None:
        if not self._enabled:
            return
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": mode,
            "text": text,
            "intent": label.intent if label is not None else None,
            "emotion": label.emotion if label is not None else None,
            "confidence": round(label.confidence, 4) if label is not None else None,
            "template": choice.template.id if choice is not None else None,
            # 留空待人工标注:正确意图(或 null 表示本不该接话)。
            "gold_intent": None,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            debug_log("Backchannel", "评测日志写入失败", {"path": str(self._path), "error": str(exc)})
