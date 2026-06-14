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
from app.agent.tools import Tool, ToolRegistry
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


def create_builtin_tool_registry(
    base_dir: Path,
    memory: MemoryStore | None = None,
    reminders: ReminderStore | None = None,
) -> ToolRegistry:
    paths = StoragePaths(base_dir)
    store = TodoStore(paths.tasks_store())
    notes = NotesStore(paths.notes_dir)
    # MemoryStore 是 dataclass，第一个字段是 base_dir；旧写法把 json 路径误传成
    # base_dir（主链路总会注入 memory，未实际触发），这里一并修正
    memory = memory or MemoryStore(base_dir=base_dir)
    reminders = reminders or ReminderStore(paths.reminders_store())
    registry = ToolRegistry(
        [
            create_screen_observation_tool(),
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
            Tool(
                name="add_reminder",
                description="创建一次性提醒。用户说“几分钟后/几秒后”这类相对时间时，必须优先使用 delay_seconds 或 delay_minutes，让程序计算触发时间；只有用户给出明确日期时间时才使用 trigger_at。repeat 第一版只支持 null 或省略。",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "提醒内容。"},
                        "trigger_at": {
                            "type": "string",
                            "description": "明确的提醒时间，本地时区 ISO 字符串。相对时间不要使用这个字段。",
                        },
                        "delay_seconds": {
                            "type": "number",
                            "description": "从现在开始延迟多少秒触发。适合“30 秒后”等相对提醒。",
                        },
                        "delay_minutes": {
                            "type": "number",
                            "description": "从现在开始延迟多少分钟触发。适合“3 分钟后”等相对提醒。",
                        },
                        "repeat": {
                            "type": ["null"],
                            "description": "第一版只支持 null。",
                        },
                    },
                    "required": ["text"],
                },
                handler=reminders.add_reminder,
            ),
            Tool(
                name="list_reminders",
                description="列出未完成且未取消的一次性提醒。",
                parameters={},
                handler=reminders.list_reminders,
            ),
            Tool(
                name="cancel_reminder",
                description="按 id 取消一条未完成提醒。",
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "提醒 id。"},
                    },
                    "required": ["id"],
                },
                handler=reminders.cancel_reminder,
            ),
            Tool(
                name="read_note",
                description="读取 data/notes/ 下的文本笔记。只能读取笔记名，不能读取任意路径。",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "笔记名，可省略 .txt 后缀。"},
                    },
                    "required": ["name"],
                },
                handler=notes.read_note,
            ),
            Tool(
                name="write_note",
                description="写入 data/notes/ 下的文本笔记。只能写入笔记名，不能写入任意路径。",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "笔记名，可省略 .txt 后缀。"},
                        "content": {"type": "string", "description": "笔记内容。"},
                    },
                    "required": ["name", "content"],
                },
                handler=notes.write_note,
            ),
            Tool(
                name="open_url",
                description="打开 http 或 https 网页。该工具会离开聊天窗口，需要用户确认后才能执行。",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的 http/https URL。"},
                    },
                    "required": ["url"],
                },
                handler=open_url,
                requires_confirmation=True,
            ),
            Tool(
                name="open_local_folder",
                description="打开已存在的本地文件夹。该工具会访问桌面环境，需要用户确认后才能执行。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要打开的本地文件夹路径。"},
                    },
                    "required": ["path"],
                },
                handler=open_local_folder,
                requires_confirmation=True,
            ),
            Tool(
                name="memory_search",
                description=(
                    "搜索 Sakura 的长期记忆。需要跨会话信息、用户偏好、项目状态或过往约定时使用。"
                    "首次调用可能返回 status='loading'，这时直接告诉主人记忆系统正在初始化，不要重复调用。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词，可为空；为空时列出最近记忆。"},
                        "limit": {"type": "integer", "description": "最多返回多少条，默认 20。"},
                    },
                },
                handler=lambda arguments: memory.search_memory(arguments, wait=False),
                group="memory",
            ),
            Tool(
                name="memory_remember",
                description=(
                    "保存一条明确、长期有用的记忆。只在用户明确要求记住，或信息明显会长期帮助陪伴/协作时使用。"
                    "不要保存密码、token、密钥、身份证、银行卡等敏感凭据。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "要保存的长期记忆内容。"},
                    },
                    "required": ["content"],
                },
                handler=lambda arguments: memory.remember_memory(arguments, wait=False),
                group="memory",
            ),
            Tool(
                name="memory_forget",
                description="在用户明确要求忘记某条信息时，按 memory_id 删除长期记忆。",
                parameters={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "记忆 id，来自 memory_search 结果。"},
                    },
                    "required": ["memory_id"],
                },
                handler=lambda arguments: memory.forget_memory(_memory_forget_arguments(arguments), wait=False),
                group="memory",
            ),
        ]
    )
    registry.register(
        Tool(
            name="search_tools",
            description=(
                "搜索 Sakura 当前已安装但可能尚未暴露的工具。"
                "当你需要浏览器、桌面、网页、文件等能力但当前工具列表不足时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的工具关键词或能力名称。"},
                },
                "required": ["keyword"],
            },
            handler=registry.search_tools,
            group="default",
            risk="low",
        )
    )
    registry.register(
        Tool(
            name="list_tool_groups",
            description="列出 Sakura 当前可用工具组及数量，用于决定是否需要搜索并激活更多工具。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=registry.list_tool_groups,
            group="default",
            risk="low",
        )
    )
    return registry


def get_current_time() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "datetime": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
    }


def _memory_forget_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    memory_id = arguments.get("memory_id") or arguments.get("id")
    return {"id": memory_id}


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
