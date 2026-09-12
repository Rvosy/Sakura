"""Bounded, parameterized queries shared by the dashboard and export worker."""

import base64
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Literal
from pydantic import Field, model_validator
from models import StrictModel, SafeToken, ErrorCode, UUIDText, VersionText
from db import DB_PATH

TABLES = {
    "errors": "error_events",
    "events": "telemetry_events",
    "model-calls": "model_call_metrics",
}


class Filters(StrictModel):
    q: str | None = Field(default=None, max_length=512)
    start: str | None = None
    end: str | None = None
    build: SafeToken | None = None
    version: VersionText | None = None
    platform: Literal["windows", "macos", "linux"] | None = None
    component: SafeToken | None = None
    reason: ErrorCode | None = None
    severity: Literal["warning", "error", "critical"] | None = None
    installation: UUIDText | None = None
    run: SafeToken | None = None
    generation: SafeToken | None = None
    operation: SafeToken | None = None
    group: SafeToken | None = None
    report: UUIDText | None = None
    include_test: bool = Field(default=False, alias="includeTest")

    @model_validator(mode="after")
    def dates(self):
        for key in ("start", "end"):
            value = getattr(self, key)
            if value is not None:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    raise ValueError("time requires timezone")
                setattr(
                    self,
                    key,
                    parsed.astimezone(timezone(timedelta(hours=8))).isoformat(),
                )
        if self.start and self.end and self.start >= self.end:
            raise ValueError("invalid time range")
        return self


@contextmanager
def connection():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        yield c
    finally:
        c.close()


def where(f, table, alias=""):
    prefix = alias + "." if alias else ""
    clauses = []
    args = []
    if not f.include_test:
        clauses.append(
            f"({prefix}environment='production' OR ({prefix}environment IS NULL AND lower(coalesce({prefix}run_id,'')) NOT LIKE '%acceptance%'))"
        )
    for key, column in [
        ("build", "build_id"),
        ("version", "app_version"),
        ("installation", "installation_id"),
        ("run", "run_id"),
        ("generation", "generation"),
        ("operation", "operation_id"),
    ]:
        value = getattr(f, key)
        if value is not None:
            clauses.append(prefix + column + "=?")
            args.append(value)
    if f.platform:
        if table != "model_call_metrics":
            clauses.append(prefix + "platform=?")
            args.append(f.platform)
        else:
            clauses.append(
                f"EXISTS (SELECT 1 FROM telemetry_events p WHERE p.installation_id={prefix}installation_id AND p.run_id={prefix}run_id AND p.platform=?)"
            )
            args.append(f.platform)
    for key, operator in [("start", ">="), ("end", "<")]:
        value = getattr(f, key)
        if value:
            clauses.append(prefix + "received_at" + operator + "?")
            args.append(datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S"))
    if table == "error_events":
        for key, column in [
            ("component", "component"),
            ("reason", "reason_code"),
            ("severity", "severity"),
            ("report", "report_id"),
        ]:
            value = getattr(f, key)
            if value is not None:
                clauses.append(prefix + column + "=?")
                args.append(value)
        if f.group:
            clauses.append(f"coalesce({prefix}group_key,{prefix}fingerprint) IN (SELECT coalesce(group_key,fingerprint) FROM error_events WHERE fingerprint=?)")
            args.append(f.group)
        if f.q:
            clauses.append(
                f"({prefix}error_code LIKE ? OR {prefix}fingerprint LIKE ? OR {prefix}event LIKE ? OR {prefix}report_json LIKE ?)"
            )
            args.extend(["%" + f.q + "%"] * 4)
    elif any((f.component, f.group, f.report, f.severity, f.q)):
        error_condition, error_args = where(f, "error_events", "selected")
        clauses.append(
            f"EXISTS (SELECT 1 FROM error_events selected WHERE {error_condition} AND selected.installation_id={prefix}installation_id AND selected.run_id={prefix}run_id)"
        )
        args.extend(error_args)
    if table == "model_call_metrics" and f.reason:
        clauses.append(prefix + "reason_code=?")
        args.append(f.reason)
    if table == "telemetry_events" and f.reason:
        clauses.append("json_extract(" + prefix + "details_json,'$.reasonCode')=?")
        args.append(f.reason)
    return " AND ".join(clauses) or "1", args


def normalize(row):
    row = dict(row)
    row["received_at_iso"] = row["received_at"].replace(" ", "T") + "+08:00"
    for name in ("stack", "breadcrumbs", "details"):
        value = row.pop(name + "_json", None)
        row[name] = json.loads(value) if value else ([] if name != "details" else None)
    raw = row.pop("report_json", None)
    if raw:
        row["report"] = json.loads(raw)
        row["evidence"] = row["report"]["evidence"]
    return row


def page(kind, filters, limit=100, cursor=None):
    table = TABLES[kind]
    conditions, args = where(filters, table)
    with connection() as c:
        c.execute("BEGIN")
        high = c.execute(f"SELECT coalesce(max(id),0) FROM {table}").fetchone()[0]
        before = high + 1
        if cursor:
            try:
                high, before = json.loads(base64.urlsafe_b64decode(cursor + "==="))
                if type(high) != int or type(before) != int or min(high, before) < 0:
                    raise ValueError()
            except Exception as e:
                raise ValueError("INVALID_CURSOR") from e
        count = c.execute(
            f"SELECT count(*) FROM {table} WHERE {conditions} AND id<=?", [*args, high]
        ).fetchone()[0]
        rows = list(
            c.execute(
                f"SELECT * FROM {table} WHERE {conditions} AND id<=? AND id<? ORDER BY id DESC LIMIT ?",
                [*args, high, before, limit + 1],
            )
        )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps([high, rows[-1]["id"]]).encode())
        .decode()
        .rstrip("=")
        if more
        else None
    )
    return dict(
        items=[normalize(r) for r in rows],
        total=count,
        nextCursor=next_cursor,
        hasMore=more,
        limit=limit,
    )


def quality(c=None, filters=None):
    if c is None:
        with connection() as owned:
            return quality(owned, filters)
    condition, args = where(filters or Filters(), "error_events")
    return [
        dict(r)
        for r in c.execute(
            f"""SELECT schema_version,build_id,count(*) AS reports,
        sum(severity IS NULL) AS missing_severity,
        sum(reason_code IS NULL) AS missing_reason,
        sum(operation_id IS NULL) AS missing_operation,
        sum(json_extract(details_json,'$.file') IS NULL AND (stack_json IS NULL OR stack_json='[]') AND json_extract(report_json,'$.evidence.exception_stack') IS NULL) AS missing_location
        FROM error_events WHERE {condition} GROUP BY schema_version,build_id ORDER BY max(id) DESC""",
            args,
        )
    ]


def groups(filters, limit=100, offset=0):
    condition, args = where(filters, "error_events", "e")
    with connection() as c:
        c.execute("BEGIN")
        sql = f"""WITH counts AS (
            SELECT installation_id,run_id,generation,fingerprint,
                max(json_extract(details_json,'$.occurrenceCount')) AS occurrences
            FROM telemetry_events WHERE event='error.repeated'
            GROUP BY installation_id,run_id,generation,fingerprint
        ), runs AS (
            SELECT json_extract(e.report_json,'$.evidence.diagnostic') AS diagnostic,e.group_key,e.fingerprint,e.fingerprint_version,e.component,e.error_code,e.reason_code,e.stage,
                e.installation_id,e.run_id,e.generation,count(*) AS reports,
                max(count(*),coalesce(max(counts.occurrences),1)) AS occurrences,
                min(e.received_at) AS first_seen,max(e.received_at) AS last_seen
            FROM error_events e LEFT JOIN counts ON counts.installation_id=e.installation_id AND counts.run_id=e.run_id
                AND counts.generation IS e.generation AND counts.fingerprint=e.fingerprint
            WHERE {condition}
            GROUP BY e.fingerprint,e.fingerprint_version,e.component,e.error_code,e.reason_code,e.stage,e.installation_id,e.run_id,e.generation
        ) SELECT min(diagnostic) AS diagnostic,min(fingerprint) AS fingerprint,fingerprint_version,component,error_code,reason_code,stage,
            sum(reports) AS reports,count(DISTINCT installation_id) AS installations,
            sum(occurrences) AS occurrences,min(first_seen) AS first_seen,max(last_seen) AS last_seen
        FROM runs GROUP BY coalesce(group_key,fingerprint),fingerprint_version,component,error_code,reason_code,stage"""
        total = c.execute("SELECT count(*) FROM (" + sql + ")", args).fetchone()[0]
        rows = [
            dict(r)
            for r in c.execute(
                sql + " ORDER BY reports DESC,fingerprint LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
        ]
        return dict(
            items=rows,
            total=total,
            hasMore=offset + len(rows) < total,
            nextCursor=str(offset + len(rows)) if offset + len(rows) < total else None,
        )


def client_quality(filters):
    condition, args = where(filters, "telemetry_events")
    with connection() as c:
        return [
            dict(r)
            for r in c.execute(
                f"""WITH runs AS (
          SELECT schema_version,build_id,installation_id,run_id,
          max(json_extract(details_json,'$.dropped')) AS dropped,
          max(json_extract(details_json,'$.failed')) AS failed,
          max(json_extract(details_json,'$.rejected')) AS rejected
          FROM telemetry_events WHERE {condition} AND event='diagnostics.summary'
          GROUP BY schema_version,build_id,installation_id,run_id)
          SELECT schema_version,build_id,count(*) AS reporting_runs,count(DISTINCT installation_id) AS reporting_installations,
          sum(dropped) AS dropped,sum(failed) AS failed,sum(rejected) AS rejected
          FROM runs GROUP BY schema_version,build_id""",
                args,
            )
        ]


def timeline(filters, limit=100, offset=0):
    selects = []
    args = []
    for kind, table in TABLES.items():
        condition, values = where(filters, table)
        selects.append(
            f"SELECT '{kind}' AS kind,id,occurred_ms,received_at FROM {table} WHERE {condition}"
        )
        args.extend(values)
    union = " UNION ALL ".join(selects)
    with connection() as c:
        c.execute("BEGIN")
        total = c.execute("SELECT count(*) FROM (" + union + ")", args).fetchone()[0]
        keys = c.execute(
            "SELECT * FROM ("
            + union
            + ") ORDER BY occurred_ms IS NULL,occurred_ms,received_at,kind,id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        rows = []
        for key in keys:
            row = normalize(
                c.execute(
                    "SELECT * FROM " + TABLES[key["kind"]] + " WHERE id=?", (key["id"],)
                ).fetchone()
            )
            row["kind"] = key["kind"]
            rows.append(row)
    return dict(
        items=rows,
        total=total,
        hasMore=offset + len(rows) < total,
        nextCursor=str(offset + len(rows)) if offset + len(rows) < total else None,
    )
