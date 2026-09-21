"""Additive migration and transactional v2 inserts. V1 timestamps are untouched."""

import json
from db import connect, beijing_received_at

TABLES = ("error_events", "telemetry_events", "model_call_metrics")
COMMON = {
    "schema_version": "INTEGER DEFAULT 1",
    "build_id": "TEXT",
    "environment": "TEXT",
    "generation": "TEXT",
    "occurred_ms": "INTEGER",
}
EXTRA = {
    "error_events": {
        "fingerprint_version": "INTEGER",
        "report_json": "TEXT",
        "group_key": "TEXT",
        "stage": "TEXT",
        "reason_code": "TEXT",
        "impact": "TEXT",
        "details_json": "TEXT",
    },
    "telemetry_events": {
        "operation_id": "TEXT",
        "fingerprint": "TEXT",
        "details_json": "TEXT",
    },
    "model_call_metrics": {
        "fault_domain": "TEXT",
        "reason_code": "TEXT",
        "http_status": "INTEGER",
        "request_stage": "TEXT",
        "attempt_count": "INTEGER",
        "compatibility_fallback": "TEXT",
    },
}


def initialize_v2():
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for table in TABLES:
            columns = {r[1] for r in c.execute("PRAGMA table_info(" + table + ")")}
            for name, kind in {**COMMON, **EXTRA[table]}.items():
                if name not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            c.execute(
                f"UPDATE {table} SET schema_version=1 WHERE schema_version IS NULL"
            )
            c.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_run_operation ON {table}(installation_id,run_id,generation,operation_id,id)"
            )
            c.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_time_id ON {table}(received_at,id)"
            )
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_error_group ON error_events(fingerprint_version,fingerprint,received_at,id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_error_build_time ON error_events(build_id,received_at,id)"
        )
        c.execute("PRAGMA user_version=3")


def common(item, received):
    d = item.diagnostics
    return dict(
        received_at=received,
        installation_id=item.installation_id,
        run_id=item.run_id,
        operation_id=item.operation_id,
        schema_version=getattr(item, "schema_version", 2),
        build_id=d.build_id,
        environment=d.environment,
        generation=d.generation,
        occurred_ms=d.occurred_ms,
    )


def encoded(model):
    return json.dumps(
        model.model_dump(mode="json", by_alias=True, exclude_none=True),
        separators=(",", ":"),
    )


def insert_v2(kind, payload):
    received = beijing_received_at()
    rows = []
    if kind == "errors":
        r = payload
        row = common(r, received)
        row.update(
            report_id=r.report_id,
            app_version=r.app.version,
            build=r.app.build,
            release_channel=r.app.channel,
            platform=r.system.platform,
            os_version=r.system.os_version,
            arch=r.system.arch,
            webview_version=r.system.webview_version,
            component=r.error.component,
            event=r.error.event,
            error_code=r.error.code,
            severity=r.details.severity,
            location=r.error.location,
            exception_type=r.error.exception_type,
            fingerprint=r.error.fingerprint,
            fingerprint_version=r.fingerprint_version,
            stage=r.details.stage,
            reason_code=r.details.reason_code,
            impact=r.details.impact,
            install_kind=r.context.install_kind if r.context else None,
            upgraded_from=r.context.upgraded_from if r.context else None,
            details_json=encoded(r.details),
            stack_json=json.dumps(
                [x.model_dump(by_alias=True, exclude_none=True) for x in r.stack]
            ),
            breadcrumbs_json=json.dumps(
                [x.model_dump(by_alias=True, exclude_none=True) for x in r.breadcrumbs]
            ),
        )
        if r.schema_version == 3:
            row["report_json"] = encoded(r)
            row["group_key"] = json.dumps([
                r.error.component, r.error.event, r.error.code,
                r.details.reason_code, r.details.stage,
                r.evidence.get("diagnostic"), r.evidence.get("exception_stack"),
                r.evidence.get("exception_chain"),
                [frame.model_dump(exclude_none=True) for frame in r.stack],
            ], ensure_ascii=False, separators=(",", ":"))
        rows.append(row)
        table = "error_events"
    elif kind == "events":
        table = "telemetry_events"
        for r in payload.items:
            row = common(r, received)
            row.update(
                {
                    k: getattr(r, k)
                    for k in (
                        "app_version",
                        "platform",
                        "os_version",
                        "arch",
                        "event",
                        "feature",
                        "duration_ms",
                        "from_version",
                        "to_version",
                        "error_code",
                    )
                }
            )
            row.update(
                details_json=encoded(r.details), fingerprint=r.details.fingerprint
            )
            rows.append(row)
    else:
        table = "model_call_metrics"
        for r in payload.items:
            row = common(r, received)
            row.update(
                {
                    k: getattr(r, k)
                    for k in (
                        "app_version",
                        "model_call",
                        "purpose",
                        "model_family",
                        "outcome",
                        "error_code",
                        "latency_ms",
                        "context_window_tokens",
                        "context_window_source",
                    )
                }
            )
            if r.usage:
                row.update(r.usage.model_dump())
            if r.estimate:
                names = {
                    "request_tokens": "request_estimated_tokens",
                    "history_tokens": "history_estimated_tokens",
                    "memory_tokens": "memory_estimated_tokens",
                    "dynamic_context_tokens": "dynamic_context_estimated_tokens",
                    "tool_schema_tokens": "tool_schema_estimated_tokens",
                }
                row.update(
                    {names.get(k, k): v for k, v in r.estimate.model_dump().items()}
                )
            req = r.request.model_dump()
            req["request_stage"] = req.pop("stage")
            row.update(req)
            rows.append(row)
    with connect() as c:
        for row in rows:
            columns = list(row)
            # Only the fixed dictionaries above provide SQL identifiers.
            conflict = " OR IGNORE" if kind == "errors" else ""
            c.execute(
                f"INSERT{conflict} INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [row[k] for k in columns],
            )
    return {
        "ok": True,
        "accepted": len(rows),
        "receivedAt": received.replace(" ", "T") + "+08:00",
    }
