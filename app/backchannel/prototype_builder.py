from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backchannel.models import INTENTS
from app.backchannel.prototypes import (
    IntentPrototype,
    load_intent_prototypes,
    local_intent_prototypes_path,
)

DEFAULT_LIMIT_PER_INTENT = 240
MAX_TEXT_LENGTH = 80

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPACE_RE = re.compile(r"\s+")
_SLOT_MARKUP_RE = re.compile(r"\[[^\[\]]+?:\s*([^\[\]]+?)\]")

_TEXT_FIELDS = (
    "utt",
    "utterance",
    "text",
    "sentence",
    "query",
    "content",
    "message",
    "user_utterance",
    "user",
)
_LABEL_FIELDS = (
    "sakura_intent",
    "intent",
    "dialogue_act",
    "dialog_act",
    "act",
    "da",
    "label",
    "category",
    "emotion",
)


@dataclass(frozen=True)
class PrototypeBuildResult:
    output_path: Path
    total_rows: int
    accepted_rows: int
    skipped_rows: int
    counts: dict[str, int]
    samples: dict[str, tuple[str, ...]]

    @property
    def total_prototypes(self) -> int:
        return sum(self.counts.values())


def build_intent_prototypes_from_files(
    paths: Iterable[Path],
    *,
    base_dir: Path,
    limit_per_intent: int = DEFAULT_LIMIT_PER_INTENT,
) -> PrototypeBuildResult:
    """从用户本地数据文件生成 Sakura intent prototypes overlay。

    仅做本地转换:不下载、不上传、不写入仓库。
    """

    input_paths = tuple(Path(path) for path in paths)
    grouped: dict[str, list[IntentPrototype]] = {intent: [] for intent in INTENTS}
    seen: set[tuple[str, str]] = set()
    total_rows = 0
    accepted_rows = 0

    for path in input_paths:
        for row in _read_rows(path):
            total_rows += 1
            prototype = _prototype_from_row(row, source=path.name)
            if prototype is None:
                continue
            key = (prototype.intent, prototype.text)
            if key in seen:
                continue
            if len(grouped[prototype.intent]) >= limit_per_intent:
                continue
            seen.add(key)
            grouped[prototype.intent].append(prototype)
            accepted_rows += 1

    output_path = local_intent_prototypes_path(base_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries = {
        intent: [
            {"text": prototype.text, "source": prototype.source}
            for prototype in prototypes
        ]
        for intent, prototypes in grouped.items()
        if prototypes
    }
    from app.storage.atomic import atomic_write_text
    payload = {
        "version": 1,
        "source": "local generated from user-provided datasets; never uploaded",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": [str(path) for path in input_paths],
        "entries": entries,
    }
    atomic_write_text(output_path, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    load_intent_prototypes.cache_clear()

    counts = {intent: len(prototypes) for intent, prototypes in grouped.items() if prototypes}
    samples = {
        intent: tuple(prototype.text for prototype in prototypes[:5])
        for intent, prototypes in grouped.items()
        if prototypes
    }
    return PrototypeBuildResult(
        output_path=output_path,
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        skipped_rows=max(0, total_rows - accepted_rows),
        counts=counts,
        samples=samples,
    )


def _read_rows(path: Path) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _read_jsonl(path)
    if suffix == ".json":
        return _read_json(path)
    if suffix in {".csv", ".tsv"}:
        return _read_table(path, delimiter="\t" if suffix == ".tsv" else ",")
    if suffix == ".txt":
        return _read_text_lines(path)
    return ()


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return tuple(rows)


def _read_json(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ()
    return tuple(_iter_dict_rows(raw))


def _read_table(path: Path, *, delimiter: str) -> tuple[dict[str, Any], ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter=delimiter))


def _read_text_lines(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(
        {"text": line.strip()}
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _iter_dict_rows(raw: Any) -> Iterable[dict[str, Any]]:
    if isinstance(raw, dict):
        if any(field in raw for field in (*_TEXT_FIELDS, *_LABEL_FIELDS)):
            yield raw
        for key in ("rows", "data", "examples", "items", "dialogues", "sessions"):
            value = raw.get(key)
            if isinstance(value, (list, dict)):
                yield from _iter_dict_rows(value)
    elif isinstance(raw, list):
        for item in raw:
            yield from _iter_dict_rows(item)


def _prototype_from_row(row: dict[str, Any], *, source: str) -> IntentPrototype | None:
    text = _extract_text(row)
    if text is None:
        return None
    intent = _extract_intent(row, text)
    if intent is None:
        return None
    return IntentPrototype(intent=intent, text=text, source=source)


def _extract_text(row: dict[str, Any]) -> str | None:
    for field in _TEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str):
            text = _normalize_text(value)
            if _valid_text(text):
                return text
    return None


def _extract_intent(row: dict[str, Any], text: str) -> str | None:
    for field in _LABEL_FIELDS:
        value = row.get(field)
        intent = _map_label(value)
        if intent is not None:
            return intent
    return _infer_intent_from_text(text)


def _map_label(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            intent = _map_label(item)
            if intent is not None:
                return intent
        return None
    if isinstance(value, dict):
        for key in ("intent", "act", "dialogue_act", "emotion", "label"):
            if key in value:
                intent = _map_label(value[key])
                if intent is not None:
                    return intent
        return None
    label = str(value or "").strip().casefold()
    if not label:
        return None
    if label in INTENTS:
        return label
    if label == "general_greet" or "greeting" in label or label in {"greet", "hello"}:
        return "greeting"
    if label.startswith("qa_") or label in {"question", "ask", "query"}:
        return "question"
    if label.endswith("_query") and label.startswith("qa_"):
        return "question"
    if any(token in label for token in ("request", "command", "directive", "alarm_", "calendar_", "email_", "iot_", "lists_", "play_", "weather_", "transport_", "takeaway_")):
        return "request"
    if any(token in label for token in ("comfort", "sad", "anxious", "fear", "depress", "helpless")):
        return "support"
    if any(token in label for token in ("complaint", "angry", "anger", "disgust", "critic", "negative")):
        return "complaint"
    if any(token in label for token in ("thank", "appreciation", "happy", "joy", "positive", "praise", "agreement")):
        return "positive"
    if any(token in label for token in ("affection", "love", "like")):
        return "affection"
    return None


def _infer_intent_from_text(text: str) -> str | None:
    lowered = text.casefold()
    if any(word in lowered for word in ("报错", "异常", "traceback", "exception", "崩了", "失败")):
        return "error"
    if any(word in text for word in ("好烦", "烦死", "难用", "无语", "受不了", "气死")):
        return "complaint"
    if any(word in lowered for word in ("难过", "撑不住", "心情不好", "压力", "想哭", "emo")):
        return "support"
    if any(word in lowered for word in ("喜欢你", "爱你", "抱抱", "贴贴", "可爱")):
        return "affection"
    if any(word in lowered for word in ("成功", "搞定", "跑通", "开心", "太好了", "谢谢")):
        return "positive"
    if any(word in lowered for word in ("你好", "早上好", "晚安", "在吗", "hello", "hi")):
        return "greeting"
    if "?" in text or "？" in text or any(word in text for word in ("什么", "为什么", "怎么", "如何", "哪里")):
        return "question"
    if any(word in text for word in ("帮我", "替我", "麻烦", "请你", "打开", "生成", "翻译", "总结")):
        return "request"
    return None


def _normalize_text(text: str) -> str:
    cleaned = _SLOT_MARKUP_RE.sub(r"\1", text)
    cleaned = cleaned.replace("\u3000", " ")
    return _SPACE_RE.sub(" ", cleaned).strip()


def _valid_text(text: str) -> bool:
    if len(text) < 2 or len(text) > MAX_TEXT_LENGTH:
        return False
    return bool(_CJK_RE.search(text))
