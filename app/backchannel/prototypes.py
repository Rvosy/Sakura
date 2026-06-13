from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.backchannel.models import INTENTS
from app.core.debug_log import debug_log

DEFAULT_INTENT_PROTOTYPES_PATH = Path(__file__).resolve().parent / "data" / "intent_prototypes.json"
LOCAL_INTENT_PROTOTYPES_RELATIVE_PATH = (
    Path("runtime") / "backchannel" / "prototypes" / "intent_prototypes.local.json"
)


@dataclass(frozen=True)
class IntentPrototype:
    """一条可提交的意图原型句。

    source 只用于审查和本地 overlay 摘要,不参与运行时分类。
    """

    intent: str
    text: str
    source: str = "seed"


@lru_cache(maxsize=4)
def load_intent_prototypes(path: Path = DEFAULT_INTENT_PROTOTYPES_PATH) -> tuple[IntentPrototype, ...]:
    """加载意图 prototype seed;非法条目跳过,文件级错误时空转降级。"""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        debug_log("Backchannel", "意图 prototype 加载失败,模型分类空转", {"path": str(path), "error": str(exc)})
        return ()
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        debug_log("Backchannel", "意图 prototype 顶层结构非法", {"path": str(path)})
        return ()

    prototypes: list[IntentPrototype] = []
    seen: set[tuple[str, str]] = set()
    for intent, raw_items in entries.items():
        intent_text = str(intent).strip()
        if intent_text not in INTENTS:
            debug_log("Backchannel", "意图 prototype intent 已跳过", {"path": str(path), "intent": intent_text})
            continue
        for text, source in _iter_raw_items(raw_items):
            key = (intent_text, text)
            if key in seen:
                continue
            seen.add(key)
            prototypes.append(IntentPrototype(intent=intent_text, text=text, source=source))
    return tuple(prototypes)


def load_intent_prototypes_from_paths(paths: Iterable[Path]) -> tuple[IntentPrototype, ...]:
    """按路径顺序合并 prototypes,保留首个重复项。

    本地 overlay 可排在 starter seed 之前,从而优先审查/统计用户初始化数据。
    """

    prototypes: list[IntentPrototype] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        for prototype in load_intent_prototypes(Path(path)):
            key = (prototype.intent, prototype.text)
            if key in seen:
                continue
            seen.add(key)
            prototypes.append(prototype)
    return tuple(prototypes)


def local_intent_prototypes_path(base_dir: Path) -> Path:
    return Path(base_dir) / LOCAL_INTENT_PROTOTYPES_RELATIVE_PATH


def load_runtime_intent_prototypes(base_dir: Path) -> tuple[IntentPrototype, ...]:
    paths: list[Path] = []
    local_path = local_intent_prototypes_path(base_dir)
    if local_path.exists():
        paths.append(local_path)
    paths.append(DEFAULT_INTENT_PROTOTYPES_PATH)
    return load_intent_prototypes_from_paths(paths)


def prototypes_by_intent(
    prototypes: Iterable[IntentPrototype],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for prototype in prototypes:
        grouped.setdefault(prototype.intent, []).append(prototype.text)
    return {intent: tuple(texts) for intent, texts in grouped.items()}


def _iter_raw_items(raw_items: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(raw_items, list):
        return ()
    result: list[tuple[str, str]] = []
    for item in raw_items:
        if isinstance(item, str):
            text = item.strip()
            source = "seed"
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            source = str(item.get("source", "seed") or "seed").strip() or "seed"
        else:
            continue
        if text:
            result.append((text, source))
    return tuple(result)
