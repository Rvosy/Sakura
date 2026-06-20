"""app/agent/runtime_events.py — 统一的桌宠运行时事件系统（issue #40）。

桌宠运行过程中的关键操作（启动、关闭、隐藏、重新打开等）被结构化为 RuntimeEvent，
并以两种方式消费：

- RuntimeEventQueue：进程内内存队列，负责去重 / 合并 / 容量控制。drain 后注入到下一次
  模型请求的 system 上下文，**只影响本次请求**，不写入 self.messages、不写入 chat_history。
- RuntimeEventLog：JSONL 落盘（append-only），负责跨进程 / 跨会话衔接（例如「上次关闭距今
  多久」「上次回复是否被中途打断」）以及后续行为分析。与 chat_history 完全独立、互不写入。

发射侧统一走 PetWindow.emit_runtime_event，作为后续情绪 / 好感 / 精力状态机与插件订阅的
唯一入口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.llm.prompts.runtime import wrap_untrusted_runtime_facts

# ---- 事件类型常量 ----
APP_STARTED = "app.started"
APP_CLOSED = "app.closed"
PET_HIDDEN = "pet.hidden"
PET_SHOWN = "pet.shown"
PET_REOPENED = "pet.reopened"

# 队列默认容量上限：超出后丢弃最旧事件，避免长时间不发消息时事件无限堆积。
DEFAULT_QUEUE_MAX_SIZE = 8
# 长时间离开阈值（秒）：超过则 reopen / app.started 升为高优先级提示。
LONG_HIDDEN_SECONDS = 600


def _now_iso() -> str:
    """统一的 ISO 本地时区时间戳，与 actions.py / chat_history.py 保持一致。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class RuntimeEvent:
    """结构化运行时事件。

    event_type: 事件类型（见本模块常量），如 "pet.reopened"。
    timestamp: ISO 本地时区时间戳。
    source: 触发来源，如 "tray" / "startup" / "shutdown"。
    metadata: 事件附加信息，如 {"hidden_duration": 300}。
    priority: 注入排序优先级，数值越大越靠前；长时间离开等场景可给更高优先级。
    """

    event_type: str
    timestamp: str = field(default_factory=_now_iso)
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "metadata": dict(self.metadata),
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvent | None":
        """从落盘记录还原；字段非法时尽量容错，无法识别则返回 None。"""
        if not isinstance(data, dict):
            return None
        event_type = data.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return None
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp.strip():
            timestamp = _now_iso()
        source = data.get("source", "")
        if not isinstance(source, str):
            source = ""
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        priority = data.get("priority", 0)
        if not isinstance(priority, int):
            priority = 0
        return cls(
            event_type=event_type.strip(),
            timestamp=timestamp,
            source=source,
            metadata=dict(metadata),
            priority=priority,
        )


class RuntimeEventQueue:
    """进程内运行时事件队列：负责去重 / 合并 / 容量控制，供注入到模型请求。"""

    def __init__(self, *, max_size: int = DEFAULT_QUEUE_MAX_SIZE) -> None:
        self._events: list[RuntimeEvent] = []
        self._max_size = max(1, max_size)

    def __len__(self) -> int:
        return len(self._events)

    def peek(self) -> list[RuntimeEvent]:
        """返回队列快照（不清空），供测试 / 调试使用。"""
        return list(self._events)

    def push(self, event: RuntimeEvent) -> None:
        # 折叠规则：reopen / shown 表示「净状态是回来了」，移除尚未消费的 hidden，
        # 避免同一次请求里同时注入 hidden + reopened 这种自相矛盾的上下文。
        if event.event_type in (PET_REOPENED, PET_SHOWN):
            self._events = [e for e in self._events if e.event_type != PET_HIDDEN]

        # 去重合并：与队尾同类型事件合并并累加 count，而不是堆叠重复条目
        # （例如用户连点 10 次，只保留一条带计数的事件）。
        if self._events and self._events[-1].event_type == event.event_type:
            previous = self._events[-1]
            merged_meta = {**previous.metadata, **event.metadata}
            merged_meta["count"] = int(previous.metadata.get("count", 1)) + 1
            self._events[-1] = RuntimeEvent(
                event_type=event.event_type,
                timestamp=event.timestamp,
                source=event.source or previous.source,
                metadata=merged_meta,
                priority=max(previous.priority, event.priority),
            )
        else:
            self._events.append(event)

        # 容量控制：超限丢最旧。
        if len(self._events) > self._max_size:
            self._events = self._events[-self._max_size :]

    def drain(self) -> list[RuntimeEvent]:
        """取出全部待注入事件并清空，按 (优先级降序, 时间升序) 排序。"""
        events = sorted(self._events, key=lambda e: (-e.priority, e.timestamp))
        self._events = []
        return events


def build_runtime_event_context_message(
    events: list[RuntimeEvent],
) -> dict[str, str] | None:
    """把若干运行时事件合并成一条 {role:system} 上下文消息；空列表返回 None。

    该消息只用于本次模型请求，指示模型自然地意识到用户刚发生的操作，
    但不要逐字复述或机械念出这些系统事件。
    """
    if not events:
        return None
    intro = (
        "以下是桌宠最近发生的运行时系统事件（用户对程序 / 窗口的操作），仅供你自然地感知当前情境，"
        "让回应更贴合现状（例如用户刚把你藏起来又重新打开、刚启动应用等）。"
        "请不要逐字复述或机械念出这些事件，也不要把它们当成用户说的话。"
    )
    event_lines = [_describe_event(event) for event in events]
    # 与屏幕视觉记忆一致：宿主收集的运行时事实统一套“事实非指令”信封并标 untrusted。
    content = wrap_untrusted_runtime_facts(
        "\n".join(event_lines),
        source="runtime_events",
        fragment_id="runtime_events",
        intro=intro,
    )
    return {"role": "system", "content": content}


def _describe_event(event: RuntimeEvent) -> str:
    human = _humanize_event(event) or f"发生事件 {event.event_type}。"
    count = event.metadata.get("count")
    suffix = f"（重复 {count} 次）" if isinstance(count, int) and count > 1 else ""
    return f"- [{event.timestamp}] {human}{suffix}"


def _humanize_event(event: RuntimeEvent) -> str:
    """把已知事件类型转成自然语言；未知类型返回空串走通用回退。"""
    meta = event.metadata or {}
    etype = event.event_type
    if etype in (PET_REOPENED, PET_SHOWN):
        duration = meta.get("hidden_duration")
        if isinstance(duration, (int, float)) and duration > 0:
            return f"用户重新打开了桌宠，距上次隐藏约 {_format_duration(duration)}。"
        return "用户重新打开了桌宠。"
    if etype == PET_HIDDEN:
        return "用户把桌宠隐藏到了托盘。"
    if etype == APP_STARTED:
        bits = ["用户启动了 Sakura。"]
        away = meta.get("away_seconds")
        if isinstance(away, (int, float)) and away > 0:
            bits.append(f"距上次关闭约 {_format_duration(away)}。")
        if meta.get("previous_reply_interrupted"):
            bits.append("上次关闭时你的回复被中途打断了。")
        return "".join(bits)
    if etype == APP_CLOSED:
        return "用户关闭了 Sakura。"
    return ""


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours} 小时 {remaining_minutes} 分钟"
    return f"{hours} 小时"


def _seconds_since(iso_timestamp: str) -> int | None:
    """计算距给定 ISO 时间戳的秒数；解析失败或为未来时间则返回 None。"""
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except (TypeError, ValueError):
        return None
    now = datetime.now().astimezone()
    if then.tzinfo is None:
        then = then.astimezone()
    delta = (now - then).total_seconds()
    if delta < 0:
        return None
    return int(delta)


class RuntimeEventLog:
    """运行时事件的 JSONL 落盘存储（append-only）。

    用于跨进程 / 跨会话衔接与后续行为分析；与 chat_history 完全独立，互不写入。
    落盘失败按 ChatHistoryStore 的容错风格静默忽略，不阻断 UI / 退出流程。
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, event: RuntimeEvent) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            # 落盘失败不应阻断关闭 / 交互流程。
            pass

    def read_recent(self, limit: int = 50) -> list[RuntimeEvent]:
        """读取末尾若干条事件（有界读取，避免日志增长后整文件解析）。"""
        if limit <= 0 or not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[RuntimeEvent] = []
        for raw in lines[-limit:]:
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = RuntimeEvent.from_dict(data)
            if event is not None:
                events.append(event)
        return events

    def load_startup_carryover(self) -> dict[str, Any] | None:
        """合成本次启动 app.started 的跨会话上下文 metadata。

        - 定位最近一条 app.closed，据此计算距今离开时长、上次回复是否被打断；
        - 若没有任何 app.closed 记录（首次启动或上次异常退出），返回 None，启动不注入。
        """
        last_closed: RuntimeEvent | None = None
        for event in reversed(self.read_recent(limit=50)):
            if event.event_type == APP_CLOSED:
                last_closed = event
                break
        if last_closed is None:
            return None
        metadata: dict[str, Any] = {"previous_close_at": last_closed.timestamp}
        away = _seconds_since(last_closed.timestamp)
        if away is not None:
            metadata["away_seconds"] = away
        if last_closed.metadata.get("interrupted_reply"):
            metadata["previous_reply_interrupted"] = True
        return metadata
