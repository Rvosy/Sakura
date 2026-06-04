from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sdk import PluginBase, PluginCapabilityRegistry, PluginContext
from sdk.types import ToolContribution


READ_LIMIT_MAX = 200
ANALYZE_LIMIT_MAX = 2000
DEFAULT_READ_LIMIT = 50
DEFAULT_ANALYZE_LIMIT = 500
LOG_NAME = "sakura-runtime.log"
ISSUE_KEYWORDS = (
    "失败",
    "错误",
    "异常",
    "超时",
    "不可用",
    "无效",
    "error",
    "fail",
    "failed",
    "exception",
    "timeout",
)


class SystemLogAnalyzerPlugin(PluginBase):
    """读取并分析 Sakura 自身运行日志的插件。"""

    plugin_id = "system_log_analyzer"
    plugin_version = "1.0.0"

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        log_dir = context.base_dir / "data" / "logs"
        register.register_tool(
            ToolContribution(
                name="sakura_runtime_log_read",
                description="读取 Sakura 最近的文件运行日志，支持按分类或文本过滤。",
                parameters=_object_schema(
                    {
                        "limit": {
                            "type": "integer",
                            "description": "最多返回条数，范围 1-200，默认 50。",
                        },
                        "category": {
                            "type": "string",
                            "description": "可选日志分类过滤，例如 API、TTS、AgentRuntime。",
                        },
                        "contains": {
                            "type": "string",
                            "description": "可选文本过滤，会匹配分类、消息和脱敏后的 data。",
                        },
                        "include_data": {
                            "type": "boolean",
                            "description": "是否返回日志 data 字段，默认 false。",
                        },
                    },
                    [],
                ),
                handler=lambda args: read_runtime_logs(log_dir, args),
                group="logs",
                risk="medium",
                requires_confirmation=True,
                capability="system_log_analyzer",
            )
        )
        register.register_tool(
            ToolContribution(
                name="sakura_runtime_log_analyze",
                description="分析 Sakura 最近的文件运行日志，统计分类、消息和疑似问题记录。",
                parameters=_object_schema(
                    {
                        "limit": {
                            "type": "integer",
                            "description": "最多分析条数，范围 1-2000，默认 500。",
                        },
                        "category": {
                            "type": "string",
                            "description": "可选日志分类过滤，例如 API、TTS、AgentRuntime。",
                        },
                        "contains": {
                            "type": "string",
                            "description": "可选文本过滤，会匹配分类、消息和脱敏后的 data。",
                        },
                    },
                    [],
                ),
                handler=lambda args: analyze_runtime_logs(log_dir, args),
                group="logs",
                risk="medium",
                requires_confirmation=True,
                capability="system_log_analyzer",
            )
        )


def read_runtime_logs(log_dir: Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取运行日志，返回过滤后的最近记录。"""
    arguments = args or {}
    limit = _clamp_int(arguments.get("limit"), DEFAULT_READ_LIMIT, READ_LIMIT_MAX)
    include_data = _bool_value(arguments.get("include_data"), False)
    category = _optional_text(arguments.get("category"))
    contains = _optional_text(arguments.get("contains"))

    snapshot = _collect_records(log_dir, limit=limit, category=category, contains=contains)
    records = [
        _public_record(record, include_data=include_data)
        for record in snapshot["records"]
    ]
    return {
        "log_dir": str(log_dir),
        "log_files": snapshot["log_files"],
        "returned": len(records),
        "matched": snapshot["matched"],
        "malformed_count": snapshot["malformed_count"],
        "records": records,
    }


def analyze_runtime_logs(log_dir: Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """分析运行日志，提取分类分布和疑似问题记录。"""
    arguments = args or {}
    limit = _clamp_int(arguments.get("limit"), DEFAULT_ANALYZE_LIMIT, ANALYZE_LIMIT_MAX)
    category = _optional_text(arguments.get("category"))
    contains = _optional_text(arguments.get("contains"))

    snapshot = _collect_records(log_dir, limit=limit, category=category, contains=contains)
    records = snapshot["records"]
    categories = Counter(str(record.get("category", "")) or "未分类" for record in records)
    messages = Counter(str(record.get("message", "")) or "空消息" for record in records)
    issues = [
        _public_record(record, include_data=True)
        for record in records
        if _looks_like_issue(record)
    ]
    return {
        "log_dir": str(log_dir),
        "log_files": snapshot["log_files"],
        "analyzed": len(records),
        "matched": snapshot["matched"],
        "malformed_count": snapshot["malformed_count"],
        "categories": dict(categories.most_common(20)),
        "messages": dict(messages.most_common(20)),
        "issue_count": len(issues),
        "recent_issues": issues[:20],
    }


def _collect_records(
    log_dir: Path,
    *,
    limit: int,
    category: str | None,
    contains: str | None,
) -> dict[str, Any]:
    log_files = _log_files(log_dir)
    records: list[dict[str, Any]] = []
    matched = 0
    malformed_count = 0

    for log_file in log_files:
        for line_number, line in _iter_recent_lines(log_file):
            parsed = _parse_record(line, log_file=log_file, line_number=line_number)
            if parsed is None:
                malformed_count += 1
                continue
            if not _matches(parsed, category=category, contains=contains):
                continue
            matched += 1
            if len(records) < limit:
                records.append(parsed)
        if len(records) >= limit:
            break

    return {
        "log_files": [str(path) for path in log_files],
        "matched": matched,
        "malformed_count": malformed_count,
        "records": records,
    }


def _log_files(log_dir: Path) -> list[Path]:
    current = log_dir / LOG_NAME
    if not log_dir.is_dir():
        return []
    backups: list[tuple[int, Path]] = []
    for path in log_dir.glob(f"{LOG_NAME}.*"):
        suffix = path.name.removeprefix(f"{LOG_NAME}.")
        if suffix.isdigit():
            backups.append((int(suffix), path))
    files = [current] if current.is_file() else []
    files.extend(path for _, path in sorted(backups, key=lambda item: item[0]))
    return files


def _iter_recent_lines(path: Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [
        (line_number, line)
        for line_number, line in reversed(list(enumerate(lines, start=1)))
        if line.strip()
    ]


def _parse_record(line: str, *, log_file: Path, line_number: int) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "schema_version": data.get("schema_version"),
        "timestamp": str(data.get("timestamp", "")),
        "category": str(data.get("category", "")),
        "message": str(data.get("message", "")),
        "data": data.get("data"),
        "source_file": str(log_file),
        "line_number": line_number,
    }


def _matches(record: dict[str, Any], *, category: str | None, contains: str | None) -> bool:
    if category is not None and record.get("category") != category:
        return False
    if contains is None:
        return True
    return contains.lower() in _record_text(record).lower()


def _looks_like_issue(record: dict[str, Any]) -> bool:
    text = _record_text(record).lower()
    return any(keyword in text for keyword in ISSUE_KEYWORDS)


def _record_text(record: dict[str, Any]) -> str:
    data = record.get("data")
    try:
        data_text = json.dumps(data, ensure_ascii=False, default=str)
    except TypeError:
        data_text = str(data)
    return f"{record.get('category', '')} {record.get('message', '')} {data_text}"


def _public_record(record: dict[str, Any], *, include_data: bool) -> dict[str, Any]:
    public = {
        "timestamp": record.get("timestamp", ""),
        "category": record.get("category", ""),
        "message": record.get("message", ""),
        "source_file": record.get("source_file", ""),
        "line_number": record.get("line_number", 0),
    }
    if record.get("schema_version") is not None:
        public["schema_version"] = record["schema_version"]
    if include_data:
        public["data"] = record.get("data")
    return public


def _clamp_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
