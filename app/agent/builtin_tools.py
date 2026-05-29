from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.tool_registry import Tool, ToolRegistry


def create_builtin_tool_registry(base_dir: Path) -> ToolRegistry:
    store = TodoStore(base_dir / "data" / "tasks.json")
    return ToolRegistry(
        [
            Tool(
                name="get_current_time",
                description="获取当前本机时间和时区。",
                parameters={},
                handler=lambda _arguments: get_current_time(),
            ),
            Tool(
                name="add_todo",
                description="新增一条待办事项。",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "待办内容。"},
                    },
                    "required": ["text"],
                },
                handler=store.add_todo,
            ),
            Tool(
                name="list_todos",
                description="列出所有未完成待办事项。",
                parameters={},
                handler=store.list_todos,
            ),
            Tool(
                name="complete_todo",
                description="按 id 标记一条待办事项为完成。",
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "待办 id。"},
                    },
                    "required": ["id"],
                },
                handler=store.complete_todo,
            ),
        ]
    )


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
            if task.get("id") == task_id:
                if task.get("completed_at") is None:
                    task["completed_at"] = _now_iso()
                    self._save(data)
                return {"task": task}
        raise ValueError(f"未找到待办：{task_id}")

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"tasks": []}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"待办文件不是有效 JSON：{self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise ValueError("待办文件格式无效，顶层必须是包含 tasks 列表的对象。")
        tasks = [task for task in data["tasks"] if isinstance(task, dict)]
        return {"tasks": tasks}

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
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
