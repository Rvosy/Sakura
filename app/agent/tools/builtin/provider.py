"""BuiltinToolProvider — 内置工具提供者。

将 builtin_tools.py 中的 create_builtin_tool_registry() 重写为 Provider 模式。
Provider 返回工具列表，由 ToolRegistry 负责注册。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.desktop_tools import NotesStore, open_local_folder, open_url
from app.agent.memory import MemoryStore
from app.agent.reminders import ReminderStore
from app.agent.screen_tools import create_screen_observation_tool
from app.agent.tools.registry import Tool
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


class BuiltinToolProvider:
    """提供 Sakura 的所有内置工具。

    使用方式:
        provider = BuiltinToolProvider(base_dir, memory, reminders)
        registry.register_from_provider(provider)
    """

    def __init__(
        self,
        base_dir: Path,
        memory: MemoryStore | None = None,
        reminders: ReminderStore | None = None,
    ) -> None:
        self.base_dir = base_dir
        paths = StoragePaths(base_dir)
        # MemoryStore 是 dataclass，第一个字段是 base_dir；旧写法误把 json 路径
        # 当 base_dir 传入（主链路总会注入 memory，未实际触发），一并修正
        self.memory = memory or MemoryStore(base_dir=base_dir)
        self.reminders = reminders or ReminderStore(paths.reminders_store())
        self._todo_store = TodoStore(paths.tasks_store())
        self._notes_store = NotesStore(paths.notes_dir)

    def contribute_tools(self) -> list[Tool]:
        """返回所有内置工具。"""
        tools: list[Tool] = [
            create_screen_observation_tool(),
            Tool(
                name="get_current_time",
                description="获取当前本机时间和时区。",
                parameters={},
                handler=lambda _: get_current_time(),
            ),
            Tool(
                name="add_todo",
                description="新增一条待办事项。",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "待办内容。"}},
                    "required": ["text"],
                },
                handler=self._todo_store.add_todo,
            ),
            Tool(
                name="list_todos",
                description="列出所有未完成待办事项。",
                parameters={},
                handler=self._todo_store.list_todos,
            ),
            Tool(
                name="complete_todo",
                description="按 id 标记一条待办事项为完成。",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "待办 id。"}},
                    "required": ["id"],
                },
                handler=self._todo_store.complete_todo,
            ),
            Tool(
                name="add_reminder",
                description=(
                    "创建一次性提醒。用户说几分钟后/几秒后这类相对时间时，"
                    "必须优先使用 delay_seconds 或 delay_minutes，让程序计算触发时间；"
                    "只有用户给出明确日期时间时才使用 trigger_at。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "提醒内容。"},
                        "trigger_at": {"type": "string", "description": "明确的提醒时间。"},
                        "delay_seconds": {"type": "number", "description": "延迟秒数。"},
                        "delay_minutes": {"type": "number", "description": "延迟分钟数。"},
                        "repeat": {"type": ["null"], "description": "暂只支持 null。"},
                    },
                    "required": ["text"],
                },
                handler=self.reminders.add_reminder,
            ),
            Tool(
                name="list_reminders",
                description="列出未完成且未取消的一次性提醒。",
                parameters={},
                handler=self.reminders.list_reminders,
            ),
            Tool(
                name="cancel_reminder",
                description="按 id 取消一条未完成提醒。",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "提醒 id。"}},
                    "required": ["id"],
                },
                handler=self.reminders.cancel_reminder,
            ),
            Tool(
                name="read_note",
                description="读取 data/notes/ 下的文本笔记。",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "笔记名。"}},
                    "required": ["name"],
                },
                handler=self._notes_store.read_note,
            ),
            Tool(
                name="write_note",
                description="写入 data/notes/ 下的文本笔记。",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "笔记名。"},
                        "content": {"type": "string", "description": "笔记内容。"},
                    },
                    "required": ["name", "content"],
                },
                handler=self._notes_store.write_note,
            ),
            Tool(
                name="open_url",
                description="打开 http 或 https 网页，需要用户确认。",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "URL。"}},
                    "required": ["url"],
                },
                handler=open_url,
                requires_confirmation=True,
            ),
            Tool(
                name="open_local_folder",
                description="打开本地文件夹，需要用户确认。",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "路径。"}},
                    "required": ["path"],
                },
                handler=open_local_folder,
                requires_confirmation=True,
            ),
            Tool(
                name="memory_search",
                description="搜索长期记忆。首次可能返回 status='loading'。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词。"},
                        "limit": {"type": "integer", "description": "返回上限。"},
                    },
                },
                handler=lambda args: self.memory.search_memory(args, wait=False),
                group="memory",
            ),
            Tool(
                name="memory_remember",
                description="保存长期记忆，不要保存密码/token/密钥。",
                parameters={
                    "type": "object",
                    "properties": {"content": {"type": "string", "description": "记忆内容。"}},
                    "required": ["content"],
                },
                handler=lambda args: self.memory.remember_memory(args, wait=False),
                group="memory",
            ),
            Tool(
                name="memory_forget",
                description="按 memory_id 删除长期记忆。",
                parameters={
                    "type": "object",
                    "properties": {"memory_id": {"type": "string", "description": "记忆 id。"}},
                    "required": ["memory_id"],
                },
                handler=lambda args: self.memory.forget_memory(
                    {"id": args.get("memory_id") or args.get("id")}, wait=False
                ),
                group="memory",
            ),
        ]
        return tools


def get_current_time() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "datetime": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
    }


class TodoStore:
    """以 JSON 文件保存轻量待办，供内部工具使用。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = _required_text(arguments, "text")
        data = self._load()
        task = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "created_at": _now_iso(),
            "completed_at": None,
        }
        data["tasks"].append(task)
        self._save(data)
        return {"task": task}

    def list_todos(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        tasks = [task for task in data["tasks"] if task.get("completed_at") is None]
        return {"tasks": tasks}

    def complete_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = _required_text(arguments, "id")
        data = self._load()
        for task in data["tasks"]:
            if task.get("id") == task_id and task.get("completed_at") is None:
                task["completed_at"] = _now_iso()
                self._save(data)
                return {"task": task}
        raise ValueError(f"未找到待办：{task_id}")

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"tasks": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise ValueError("待办文件格式无效")
        return {"tasks": [t for t in data["tasks"] if isinstance(t, dict)]}

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少必填参数：{key}")
    return value.strip()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
