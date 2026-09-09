"""One read-only snapshot exporter for CLI and authenticated admin downloads."""

import argparse
import collections
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from queries import TABLES, Filters, connection, where, normalize, quality

EXPORT_VERSION = 2
MAX_BYTES = 1024**3
MAX_SECONDS = 300
PRIVATE = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|(?:https?|file)://|(?:^|\s)(?:/Users/|/home/|[A-Za-z]:[\\/])|(?:SENTINEL|PRIVATE)_(?:CHAT|PROMPT|KEY|MEMORY|TOOL|EXCEPTION|MODEL))"
)


class ExportLimitError(RuntimeError):
    pass


def export_bundle(
    destination: Path,
    filters: Filters,
    *,
    max_bytes=MAX_BYTES,
    max_seconds=MAX_SECONDS,
    cancel_event=None,
):
    from export_lock import exclusive_export

    with exclusive_export():
        return _export_snapshot(
            destination,
            filters,
            max_bytes=max_bytes,
            max_seconds=max_seconds,
            cancel_event=cancel_event,
        )


def _export_snapshot(
    destination: Path, filters: Filters, *, max_bytes, max_seconds, cancel_event
):
    start = time.monotonic()
    written = 0
    redactions = []

    def check(size=0):
        nonlocal written
        if cancel_event is not None and cancel_event.is_set():
            raise ExportLimitError("EXPORT_CANCELLED")
        written += size
        if written > max_bytes:
            raise ExportLimitError("EXPORT_SIZE_LIMIT")
        if time.monotonic() - start > max_seconds:
            raise ExportLimitError("EXPORT_TIME_LIMIT")

    def clean(value, table, row_id, field=""):
        if isinstance(value, dict):
            return {
                k: clean(v, table, row_id, field + "." + k) for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                clean(v, table, row_id, field + f"[{i}]") for i, v in enumerate(value)
            ]
        if isinstance(value, str) and PRIVATE.search(value):
            redactions.append({"table": table, "id": row_id, "field": field})
            return "[REDACTED]"
        return value

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(
        prefix="telemetry-export-", dir=destination.parent
    ) as work:
        root = Path(work)
        files = {}
        counts = {}
        grouped = {}
        builds = set()
        occurrences = {}
        installs = {}

        def add(name, data):
            raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
            check(len(raw))
            (root / name).write_bytes(raw)

        with connection() as c:
            c.execute("BEGIN")
            c.execute(
                "SELECT count(*) FROM sqlite_master"
            ).fetchone()  # fixes the WAL snapshot
            snapshot = datetime.now(timezone.utc).isoformat()
            c.set_progress_handler(
                lambda: int(time.monotonic() - start > max_seconds), 1000
            )
            ew, ea = where(filters, "error_events", "e")
            seed_only = any(
                (
                    filters.report,
                    filters.group,
                    filters.component,
                    filters.severity,
                    filters.reason,
                    filters.q,
                )
            )
            timeline = (root / "timeline.jsonl").open("wb")
            try:
                for table in TABLES.values():
                    check()
                    condition, args = where(filters, table, "t")
                    extension = "0"
                    extension_args = []
                    if table != "error_events" or filters.report:
                        extension = f"EXISTS (SELECT 1 FROM error_events e WHERE {ew} AND e.installation_id=t.installation_id AND e.run_id=t.run_id)"
                        extension_args = list(ea)
                        if not filters.include_test:
                            extension += " AND (t.environment='production' OR (t.environment IS NULL AND lower(coalesce(t.run_id,'')) NOT LIKE '%acceptance%'))"
                        if seed_only and table != "error_events":
                            condition = "0"
                            args = []
                    sql = f"SELECT t.* FROM {table} t WHERE ({condition}) OR ({extension}) ORDER BY t.id"
                    total = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    matched = c.execute(
                        f"SELECT count(*) FROM {table} t WHERE {condition}", args
                    ).fetchone()[0]
                    n = 0
                    with (root / (table + ".jsonl")).open("wb") as output:
                        for source in c.execute(sql, [*args, *extension_args]):
                            row = clean(normalize(source), table, source["id"])
                            raw = (
                                json.dumps(
                                    row, ensure_ascii=False, separators=(",", ":")
                                )
                                + "\n"
                            ).encode()
                            check(len(raw))
                            output.write(raw)
                            n += 1
                            builds.add(row.get("build_id") or row.get("build"))
                            event = {
                                k: row.get(k)
                                for k in (
                                    "id",
                                    "installation_id",
                                    "run_id",
                                    "generation",
                                    "operation_id",
                                    "occurred_ms",
                                    "received_at_iso",
                                    "event",
                                    "error_code",
                                    "outcome",
                                )
                            }
                            detail = row.get("details") or {}
                            event["outcome"] = row.get("outcome") or detail.get(
                                "outcome"
                            )
                            event["stage"] = row.get("stage") or detail.get("stage")
                            event["reason_code"] = row.get("reason_code") or detail.get(
                                "reasonCode"
                            )
                            event["repair_outcome"] = detail.get("repairOutcome")
                            event["recovery_outcome"] = detail.get("recoveryOutcome")
                            event["table"] = table
                            data = (
                                json.dumps(event, separators=(",", ":")) + "\n"
                            ).encode()
                            check(len(data))
                            timeline.write(data)
                            if table == "error_events":
                                key = (
                                    row.get("fingerprint_version"),
                                    row.get("fingerprint"),
                                )
                                group = grouped.setdefault(
                                    key,
                                    {
                                        "fingerprintVersion": key[0],
                                        "fingerprint": key[1],
                                        "reports": 0,
                                        "reportIds": [],
                                    },
                                )
                                group["reports"] += 1
                                group["reportIds"].append(row["report_id"])
                                installs.setdefault(key, set()).add(
                                    row["installation_id"]
                                )
                                occurrence_key = (
                                    row["installation_id"],
                                    row["run_id"],
                                    row.get("generation"),
                                    row.get("fingerprint"),
                                )
                                occurrences[occurrence_key] = max(
                                    1, occurrences.get(occurrence_key, 0)
                                )
                            elif (
                                table == "telemetry_events"
                                and row.get("event") == "error.repeated"
                            ):
                                occurrence_key = (
                                    row["installation_id"],
                                    row["run_id"],
                                    row.get("generation"),
                                    row.get("fingerprint"),
                                )
                                occurrences[occurrence_key] = max(
                                    occurrences.get(occurrence_key, 0),
                                    (row.get("details") or {}).get(
                                        "occurrenceCount", 0
                                    ),
                                )
                    counts[table] = {
                        "sourceRows": total,
                        "matchedRows": matched,
                        "exportedRows": n,
                        "expandedRows": max(0, n - matched),
                        "excludedRows": total - n,
                    }
                schemas = {
                    t: [dict(r) for r in c.execute("PRAGMA table_info(" + t + ")")]
                    for t in TABLES.values()
                }
                data_quality = quality(c, filters)
            except sqlite3.OperationalError as e:
                if time.monotonic() - start > max_seconds:
                    raise ExportLimitError("EXPORT_TIME_LIMIT") from e
                raise
            finally:
                timeline.close()
            c.rollback()
        for key, group in grouped.items():
            group["installations"] = len(installs.get(key, set()))
            group["occurrences"] = max(
                group["reports"],
                sum(v for k, v in occurrences.items() if k[3] == key[1]),
            )
        from v2_models import ErrorReportV2, EventBatchV2, ModelBatchV2

        add(
            "protocol.json",
            {
                name: model.model_json_schema(by_alias=True)
                for name, model in [
                    ("errors", ErrorReportV2),
                    ("events", EventBatchV2),
                    ("modelCalls", ModelBatchV2),
                ]
            },
        )
        add("schema.json", schemas)
        add("groups.json", list(grouped.values()))
        add("quality.json", data_quality)
        add("redactions.json", redactions)
        mapping = []
        for build in sorted(b for b in builds if b):
            # Only release manifests shipped by maintainers, never client supplied source maps.
            safe = bool(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", build))
            manifest = (
                Path(__file__).with_name("builds") / (build + ".json") if safe else None
            )
            mapping.append(
                {
                    "buildId": build,
                    "mapping": json.loads(manifest.read_text())
                    if manifest and manifest.is_file()
                    else None,
                }
            )
        add("builds.json", mapping)
        readme = """# Sakura 遥测分析包 v2

先运行 `python verify_bundle.py .` 校验所有文件，再读取 groups.json 与 quality.json。
三张 JSONL 保留数据库行 ID，stack/breadcrumbs 已转为数组，details 是固定结构。
按 installation_id + run_id + generation + operation_id 关联；单调时间仅在同 run 内可比较。
timeline.jsonl 按数据源分批写出，分析时按同 run 的 occurred_ms 排序；缺失时使用接收时间并注明局限。
received_at 是北京时间无时区旧列；received_at_iso 明确带 +08:00。
API success 只表示模型请求成功，回复有效性须查看 reply.repair.finished / chat.finished。
重复摘要为累计值，按安装、run、generation、fingerprint 取最大值，不能直接求和。
quality.json 是原始筛选范围内的错误覆盖概览；manifest 中的 filters 和 counts 定义实际导出范围。
默认排除显式 development/acceptance；旧数据只按 acceptance 字样启发式排除。
关联扩展可能包含筛选时间之外的同运行记录；原始原因未知、位置缺失或没有终态均不得推断为成功。
builds.json 的 mapping=null 表示服务器尚未安装该发布构建的资源映射，不得使用其他版本代码代替。
"""
        check(len(readme.encode()))
        (root / "README.md").write_text(readme)
        verifier = Path(__file__).with_name("verify_bundle.py").read_bytes()
        check(len(verifier))
        (root / "verify_bundle.py").write_bytes(verifier)
        for path in sorted(root.iterdir()):
            check()
            files[path.name] = {
                "bytes": path.stat().st_size,
            }
        manifest = {
            "bundleVersion": 2,
            "exporterVersion": EXPORT_VERSION,
            "snapshotUtc": snapshot,
            "timezone": "Asia/Shanghai",
            "filters": filters.model_dump(by_alias=True),
            "counts": counts,
            "files": files,
            "truncated": False,
            "redactedFields": len(redactions),
            "associationExpansion": "same installation_id + run_id",
            "coverageLimit": "best-effort client delivery; absence is unknown",
        }
        add("manifest.json", manifest)
        temporary = root / "bundle.zip"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for path in root.iterdir():
                if path == temporary:
                    continue
                check()
                with (
                    path.open("rb") as source,
                    z.open(path.name, "w", force_zip64=True) as target,
                ):
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        check()
                        target.write(chunk)
        check()
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    for name in (
        "start",
        "end",
        "build",
        "version",
        "installation",
        "run",
        "generation",
        "operation",
        "group",
        "report",
        "component",
        "reason",
        "severity",
        "platform",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("--include-test", action="store_true")
    args = vars(parser.parse_args())
    output = args.pop("output")
    args["includeTest"] = args.pop("include_test")
    manifest = export_bundle(
        output, Filters.model_validate({k: v for k, v in args.items() if v is not None})
    )
    print(
        json.dumps(
            {"output": str(output), "counts": manifest["counts"]}, ensure_ascii=False
        )
    )
