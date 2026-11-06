"""聊天记录使用的可读数据文件。"""

from __future__ import annotations

import json
import os
import tempfile
import sqlite3
from contextlib import closing
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path


def read_archive(path, *, kind, provider, character_id, identity_path=None):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("format") != "sakura-data" or data.get("version") != 1:
        raise ValueError("请选择 Sakura 数据文件（版本 1）。")
    if data["kind"] != kind or data["provider"] != provider:
        raise ValueError("文件不是当前页面支持的聊天记录数据。")
    records = data["records"]
    if not isinstance(records, list):
        raise ValueError("数据文件的 records 必须是列表。")
    if data["characterId"] != character_id:
        if identity_path is None:
            raise ValueError("导入到其他角色需要提供身份映射存储。")
        _map_character(records, data["characterId"], character_id, identity_path)
    return data


def _map_character(records, source, target, path):
    """复制到其他角色时，同一条记录及其引用始终使用同一新 ID。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS import_ids (source TEXT, target TEXT, domain TEXT, "
                           "original TEXT, mapped TEXT, PRIMARY KEY (source, target, domain, original))")

        def mapped(domain, identity):
            connection.execute("INSERT OR IGNORE INTO import_ids VALUES (?, ?, ?, ?, ?)",
                               (source, target, domain, identity, str(uuid4())))
            return connection.execute("SELECT mapped FROM import_ids WHERE source=? AND target=? AND domain=? AND original=?",
                                      (source, target, domain, identity)).fetchone()[0]

        for item in records:
            item["entry_id"] = mapped("entry", item["entry_id"])
            item["turn_id"] = mapped("turn", item["turn_id"])
            # 插件业务记录仍属于来源角色，复制的聊天只保留摘要和详情。
            activity = item["payload"].get("activity")
            if activity:
                item["payload"]["text"] += "\n" + activity.get("details", "")
                del item["payload"]["activity"]


def write_archive(path, *, kind, provider, character_id, records, format="json"):
    if format == "json":
        text = json.dumps({
            "format": "sakura-data", "version": 1,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "kind": kind, "provider": provider, "characterId": character_id,
            "records": records,
        }, ensure_ascii=False, indent=2) + "\n"
    elif format == "txt":
        text = render_text(kind, character_id, records)
    else:
        raise ValueError("请选择 Sakura JSON 或 TXT 格式。")
    destination = Path(path)
    # 写完后再替换，保存失败时保留用户原来的文件。
    fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"exported": len(records), "path": str(destination)}


def plan_merge(records, existing, *, key="id"):
    """相同身份只导入一次；冲突保留本地内容。"""
    indexed = {item[key]: item for item in existing}
    additions = []
    counts = {"added": 0, "identical": 0, "conflicts": 0}
    for item in records:
        identity = item[key]
        if not isinstance(identity, str) or not identity:
            raise ValueError("记录缺少有效 ID。")
        old = indexed.get(identity)
        if old is None:
            additions.append(item)
            indexed[identity] = item
            counts["added"] += 1
        elif old == item:
            counts["identical"] += 1
        else:
            counts["conflicts"] += 1
    return additions, counts


def render_text(kind, character_id, records):
    lines = ["Sakura 聊天记录", f"角色：{character_id}", ""]
    labels = {"human": "用户", "assistant": "助手", "observation": "观察", "system": "系统"}
    for item in records:
        lines.append(f"[{item['created_at']}] {labels[item['kind']]}")
        payload = item["payload"]
        if item["kind"] == "assistant":
            for segment in payload["segments"]:
                lines.append(segment["text"])
                if segment.get("translation"):
                    lines.append(f"译文：{segment['translation']}")
        else:
            lines.append(payload["text"])
            if payload.get("activity", {}).get("details"):
                lines.append(payload["activity"]["details"])
        lines.append("")
    return "\n".join(lines)
