from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import ErrorReport, ModelCallBatch, TelemetryEventBatch


DB_PATH = Path(
    os.environ.get(
        "SAKURA_TELEMETRY_DB_PATH",
        "/var/lib/sakura-telemetry/telemetry.db",
    )
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS error_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    report_id TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    run_id TEXT,
    operation_id TEXT,
    app_version TEXT NOT NULL,
    build TEXT,
    release_channel TEXT,
    platform TEXT NOT NULL,
    os_version TEXT,
    arch TEXT,
    webview_version TEXT,
    component TEXT NOT NULL,
    event TEXT NOT NULL,
    error_code TEXT NOT NULL,
    severity TEXT,
    location TEXT,
    exception_type TEXT,
    fingerprint TEXT,
    install_kind TEXT,
    upgraded_from TEXT,
    stack_json TEXT,
    breadcrumbs_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_error_events_report_id
    ON error_events(report_id);
CREATE INDEX IF NOT EXISTS ix_error_events_installation_id
    ON error_events(installation_id);
CREATE INDEX IF NOT EXISTS ix_error_events_received_at
    ON error_events(received_at);
CREATE INDEX IF NOT EXISTS ix_error_events_error_code
    ON error_events(error_code);
CREATE INDEX IF NOT EXISTS ix_error_events_app_version
    ON error_events(app_version);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    run_id TEXT,
    app_version TEXT NOT NULL,
    platform TEXT NOT NULL,
    os_version TEXT,
    arch TEXT,
    event TEXT NOT NULL,
    feature TEXT,
    duration_ms INTEGER,
    from_version TEXT,
    to_version TEXT,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS model_call_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    run_id TEXT,
    operation_id TEXT,
    app_version TEXT NOT NULL,
    model_call INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    model_family TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error_code TEXT,
    latency_ms INTEGER,
    context_window_tokens INTEGER,
    context_window_source TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_input_tokens INTEGER,
    reasoning_tokens INTEGER,
    request_estimated_tokens INTEGER,
    history_estimated_tokens INTEGER,
    memory_estimated_tokens INTEGER,
    dynamic_context_estimated_tokens INTEGER,
    tool_schema_estimated_tokens INTEGER,
    history_messages INTEGER,
    memories INTEGER,
    tool_count INTEGER
);
"""


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def beijing_received_at() -> str:
    return datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=5.0)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA_SQL)
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(error_events)")
        }
        if "severity" not in columns:
            connection.execute("ALTER TABLE error_events ADD COLUMN severity TEXT")
        if "location" not in columns:
            connection.execute("ALTER TABLE error_events ADD COLUMN location TEXT")
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version < 1:
            for table in ("error_events", "telemetry_events", "model_call_metrics"):
                connection.execute(
                    f"UPDATE {table} SET received_at = datetime(received_at, '+8 hours')"
                )
            connection.execute("PRAGMA user_version=1")


def database_is_healthy() -> bool:
    with connect() as connection:
        row = connection.execute("SELECT 1").fetchone()
    return row == (1,)


def insert_error(report: ErrorReport) -> str:
    received_at = beijing_received_at()
    context = report.context
    stack_json = (
        json.dumps(
            [
                frame.model_dump(mode="json", by_alias=True, exclude_none=True)
                for frame in report.stack
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if report.stack
        else None
    )
    breadcrumbs_json = (
        json.dumps(
            [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in report.breadcrumbs
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if report.breadcrumbs
        else None
    )
    values = (
        received_at,
        report.report_id,
        report.installation_id,
        report.run_id,
        report.operation_id,
        report.app.version,
        report.app.build,
        report.app.channel,
        report.system.platform,
        report.system.os_version,
        report.system.arch,
        report.system.webview_version,
        report.error.component,
        report.error.event,
        report.error.code,
        report.error.severity,
        report.error.location,
        report.error.exception_type,
        report.error.fingerprint,
        context.install_kind if context else None,
        context.upgraded_from if context else None,
        stack_json,
        breadcrumbs_json,
    )
    with connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO error_events (
                received_at, report_id, installation_id, run_id, operation_id,
                app_version, build, release_channel, platform, os_version, arch,
                webview_version, component, event, error_code, severity, location,
                exception_type, fingerprint, install_kind, upgraded_from, stack_json,
                breadcrumbs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        stored = connection.execute(
            "SELECT received_at FROM error_events WHERE report_id = ?",
            (report.report_id,),
        ).fetchone()
    return stored[0]


def insert_events(batch: TelemetryEventBatch) -> tuple[int, str]:
    received_at = beijing_received_at()
    rows = [
        (
            received_at,
            item.installation_id,
            item.run_id,
            item.app_version,
            item.platform,
            item.os_version,
            item.arch,
            item.event,
            item.feature,
            item.duration_ms,
            item.from_version,
            item.to_version,
            item.error_code,
        )
        for item in batch.items
    ]
    with connect() as connection:
        connection.executemany(
            """
            INSERT INTO telemetry_events (
                received_at, installation_id, run_id, app_version, platform,
                os_version, arch, event, feature, duration_ms, from_version,
                to_version, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows), received_at


def insert_model_calls(batch: ModelCallBatch) -> tuple[int, str]:
    received_at = beijing_received_at()
    rows = []
    for item in batch.items:
        usage = item.usage
        estimate = item.estimate
        rows.append(
            (
                received_at,
                item.installation_id,
                item.run_id,
                item.operation_id,
                item.app_version,
                item.model_call,
                item.purpose,
                item.model_family,
                item.outcome,
                item.error_code,
                item.latency_ms,
                item.context_window_tokens,
                item.context_window_source,
                usage.prompt_tokens if usage else None,
                usage.completion_tokens if usage else None,
                usage.total_tokens if usage else None,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
                usage.cached_input_tokens if usage else None,
                usage.reasoning_tokens if usage else None,
                estimate.request_tokens if estimate else None,
                estimate.history_tokens if estimate else None,
                estimate.memory_tokens if estimate else None,
                estimate.dynamic_context_tokens if estimate else None,
                estimate.tool_schema_tokens if estimate else None,
                estimate.history_messages if estimate else None,
                estimate.memories if estimate else None,
                estimate.tool_count if estimate else None,
            )
        )
    with connect() as connection:
        connection.executemany(
            """
            INSERT INTO model_call_metrics (
                received_at, installation_id, run_id, operation_id, app_version,
                model_call, purpose, model_family, outcome, error_code,
                latency_ms, context_window_tokens, context_window_source,
                prompt_tokens, completion_tokens, total_tokens, input_tokens,
                output_tokens, cached_input_tokens, reasoning_tokens,
                request_estimated_tokens, history_estimated_tokens,
                memory_estimated_tokens, dynamic_context_estimated_tokens,
                tool_schema_estimated_tokens, history_messages, memories,
                tool_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows), received_at
