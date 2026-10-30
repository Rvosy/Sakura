"""保存模型返回的用量；历史追踪日志只用于补齐启用统计之前的记录。"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from app.plugin_sdk.sakura_usage import CATEGORIES, category_for_purpose


def token_usage(usage):
    def number(*values):
        return next((v for v in values if type(v) is int and v >= 0), None)

    usage = usage or {}
    incoming = number(usage.get("prompt_tokens"), usage.get("input_tokens"))
    outgoing = number(usage.get("completion_tokens"), usage.get("output_tokens"))
    total = number(usage.get("total_tokens"))
    if total is None and incoming is not None and outgoing is not None:
        total = incoming + outgoing
    cached = number(usage.get("cached_input_tokens"),
                    (usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}).get("cached_tokens"))
    reasoning = number(usage.get("reasoning_tokens"),
                       (usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}).get("reasoning_tokens"))
    return incoming, outgoing, total, cached, reasoning


class UsageStore:
    def __init__(self, user_root: Path):
        self.path = Path(user_root) / "data" / "usage.sqlite3"
        self.logs = Path(user_root) / "data" / "logs"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS usage_calls (
                id TEXT PRIMARY KEY, at REAL NOT NULL, model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
                cached_tokens INTEGER, reasoning_tokens INTEGER, source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS usage_time ON usage_calls(at);
            CREATE TABLE IF NOT EXISTS usage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("PRAGMA user_version").fetchone()[0] < 2:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(usage_calls)")}
            for column, default in (("category", "uncategorized"), ("category_label", "未分类"), ("plugin_id", ""), ("plugin_name", "历史数据")):
                if column not in columns:
                    connection.execute(f"ALTER TABLE usage_calls ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
            connection.execute("DELETE FROM usage_meta WHERE key='imported'")
            connection.execute("PRAGMA user_version=2")
        connection.execute("INSERT OR IGNORE INTO usage_meta VALUES ('started', ?)",
                           (str(datetime.now().replace(microsecond=0).timestamp()),))
        connection.commit()
        return connection

    def record_response(self, response, metadata):
        return self.record({"recordId": metadata["operationId"], "model": metadata["model"]["modelId"],
            "category": metadata.get("usageCategory", "other"), "categoryLabel": metadata.get("usageLabel"),
            "usage": response.get("usage") or {}}, plugin_id=metadata.get("callerId", ""), plugin_name=metadata.get("callerName", ""))

    def record(self, record, *, plugin_id, plugin_name):
        identity = record.get("recordId")
        category = record.get("category", "other")
        label = record.get("categoryLabel") or category
        model = record.get("model", "")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("用量记录需要稳定的 recordId。")
        if not isinstance(category, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", category):
            raise ValueError("用量分类 ID 无效。")
        if not isinstance(label, str) or not isinstance(model, str) or not isinstance(record.get("usage", {}), dict):
            raise ValueError("用量记录的名称或 usage 格式无效。")
        at = datetime.fromisoformat(record["timestamp"]).timestamp() if record.get("timestamp") else datetime.now().timestamp()
        label = CATEGORIES.get(category, label)
        category = category if category in CATEGORIES else f"{plugin_id}:{category}"
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("INSERT OR IGNORE INTO usage_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                json.dumps([plugin_id, identity]), at, model, *token_usage(record.get("usage")), "live",
                category, label, plugin_id, plugin_name or plugin_id or "历史数据",
            ))
            return {"recorded": cursor.rowcount > 0}

    def import_traces(self):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM usage_meta WHERE key='imported'").fetchone():
                return
            cutoff = float(connection.execute("SELECT value FROM usage_meta WHERE key='started'").fetchone()[0])
            skipped = 0
            requests = {}
            paths = sorted(self.logs.glob("sakura-agent-trace.*.log"))
            if (self.logs / "sakura-agent-trace.log").exists():
                paths.append(self.logs / "sakura-agent-trace.log")
            for path in paths:
                for record in trace_replies(path, requests):
                    try:
                        at = datetime.fromisoformat(record["time"]).timestamp()
                    except (KeyError, ValueError):
                        skipped += 1
                        continue
                    if at >= cutoff:
                        continue
                    identity = json.dumps([record["request_time"], record["trace"], record["call"], record["model"]])
                    category = category_for_purpose(record["purpose"])
                    connection.execute("""INSERT INTO usage_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET category=excluded.category, category_label=excluded.category_label
                        WHERE usage_calls.source='trace' AND usage_calls.category='uncategorized'""", (
                        "trace:" + identity, at, record["model"], *token_usage(record["usage"]), "trace",
                        category, CATEGORIES[category], "", "历史日志",
                    ))
            connection.execute("INSERT INTO usage_meta VALUES ('imported', ?)", (str(skipped),))

    def snapshot(self, period="30d", *, now=None, start_date=None, end_date=None):
        now = now or datetime.now().astimezone()
        if period not in {"today", "7d", "30d", "all", "custom"}:
            raise ValueError("统计时间范围无效。")
        if period == "custom":
            start = datetime.fromisoformat(start_date).astimezone()
            end = datetime.fromisoformat(end_date).astimezone().replace(hour=23, minute=59, second=59, microsecond=999999)
            if start > end:
                raise ValueError("开始日期不能晚于结束日期。")
        else:
            start = None if period == "all" else now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days={"today": 0, "7d": 6, "30d": 29}[period])
            end = now
        self.import_traces()
        hourly = start is not None and (end - start).total_seconds() <= 24 * 60 * 60
        with closing(self._connect()) as connection:
            params = (start.timestamp() if start else 0, end.timestamp())
            columns = """COUNT(*) AS calls, COUNT(total_tokens) AS measuredCalls,
                SUM(input_tokens) AS inputTokens, SUM(output_tokens) AS outputTokens,
                SUM(total_tokens) AS totalTokens, SUM(cached_tokens) AS cachedTokens,
                SUM(reasoning_tokens) AS reasoningTokens,
                SUM(source='trace') AS historicalCalls"""
            where = " FROM usage_calls WHERE at >= ? AND at <= ?"
            summary = dict(connection.execute("SELECT " + columns + where, params).fetchone())
            daily = [dict(row) for row in connection.execute(
                "SELECT date(at, 'unixepoch', 'localtime') AS day, " + columns + where + " GROUP BY day ORDER BY day", params)]
            hours = [dict(row) for row in connection.execute(
                "SELECT strftime('%Y-%m-%dT%H:00:00', at, 'unixepoch', 'localtime') AS hour, "
                + columns + where + " GROUP BY hour ORDER BY hour", params)] if hourly else []
            models = [dict(row) for row in connection.execute(
                "SELECT model, " + columns + where + " GROUP BY model ORDER BY totalTokens DESC, calls DESC", params)]
            categories = [dict(row) for row in connection.execute(
                "SELECT category AS id, category_label AS label, " + columns + where + " GROUP BY category ORDER BY totalTokens DESC, calls DESC", params)]
            plugins = [dict(row) for row in connection.execute(
                "SELECT plugin_id AS id, plugin_name AS label, " + columns + where + " GROUP BY plugin_id ORDER BY totalTokens DESC, calls DESC", params)]
            earliest = connection.execute("SELECT MIN(at) FROM usage_calls").fetchone()[0]
            skipped = int(connection.execute("SELECT value FROM usage_meta WHERE key='imported'").fetchone()[0])
        return {"period": period, "start": start.isoformat() if start else None, "end": end.isoformat(), "updatedAt": now.isoformat(),
                "summary": summary, "daily": daily, "hourly": hours, "granularity": "hour" if hourly else "day",
                "models": models, "categories": categories, "plugins": plugins, "skipped": skipped,
                "earliest": datetime.fromtimestamp(earliest).astimezone().isoformat() if earliest is not None else None}


def trace_replies(path, requests):
    """只读取追踪块的元数据与用量区，不从提示词或正文中匹配数字。"""
    labels = {"提示词 tokens": "prompt_tokens", "回复 tokens": "completion_tokens",
              "总计 tokens": "total_tokens", "输入 tokens": "input_tokens", "输出 tokens": "output_tokens",
              "缓存输入 tokens": "cached_input_tokens", "推理 tokens": "reasoning_tokens"}
    text = path.read_text(encoding="utf-8-sig")
    for block in re.split(r"(?m)^={60}\r?\n(?=\[Agent Trace\])", text):
        if not block.startswith("[Agent Trace] "):
            continue
        sections = re.split(r"(?m)^-{60}\r?$", block)
        if len(sections) < 3:
            continue
        fields = dict(re.findall(r"(?m)^([^:\r\n]+?)\s*:\s*(.*?)\s*$", sections[1]))
        key = (fields.get("追踪编号"), fields.get("模型调用"))
        if not all(key):
            continue
        if block.startswith("[Agent Trace] 模型请求"):
            requests[key] = fields
            continue
        request = requests.pop(key, None)
        if request is None:
            continue
        usage = {}
        for section in sections[2:]:
            if section.strip().startswith("Token 用量\n") or section.strip().startswith("Token 用量\r\n"):
                for label, value in re.findall(r"(?m)^  (.*?)\s*:\s*(\d+)\s*$", section):
                    if label in labels:
                        usage[labels[label]] = int(value)
        # 早期日志把不存在的别名写成 0；实际有值的字段优先。
        for primary, alias in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            if not usage.get(primary) and usage.get(alias):
                usage.pop(primary, None)
        if usage and not any(usage.values()):
            usage = {}
        yield {"time": fields.get("时间", ""), "request_time": request.get("时间", ""),
               "trace": key[0], "call": key[1], "model": request.get("模型", "未知模型"),
               "purpose": request.get("用途", ""), "usage": usage}
