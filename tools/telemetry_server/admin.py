from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse

from db import DB_PATH


ADMIN_HOST = "admin.cialloo.cn"
STATIC_ROOT = Path(__file__).with_name("admin_static")

router = APIRouter(include_in_schema=False)


def _not_found() -> None:
    raise HTTPException(status_code=404, detail="NOT_FOUND")


def _require_admin_host(request: Request) -> None:
    host = request.headers.get("host", "").split(":", 1)[0].rstrip(".").lower()
    if host != ADMIN_HOST:
        _not_found()


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError:
        _not_found()
    canonical = str(parsed)
    if canonical != value.lower():
        _not_found()
    return canonical


@contextmanager
def _read_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _one(cursor: sqlite3.Cursor) -> dict[str, Any]:
    row = cursor.fetchone()
    return dict(row) if row is not None else {}


def _since(days: int) -> str:
    return f"-{days} days"


def _safe_json(value: str | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _percent(
    numerator: int | float | None, denominator: int | float | None
) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(float(numerator) * 100.0 / float(denominator), 2)


@router.get("/admin", response_class=RedirectResponse)
def admin_redirect(request: Request) -> RedirectResponse:
    _require_admin_host(request)
    return RedirectResponse(url="/admin/", status_code=307)


@router.get("/admin/")
def admin_index(request: Request) -> FileResponse:
    _require_admin_host(request)
    return FileResponse(
        STATIC_ROOT / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/assets/admin.css")
def admin_css(request: Request) -> FileResponse:
    _require_admin_host(request)
    return FileResponse(
        STATIC_ROOT / "assets" / "admin.css",
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/assets/admin.js")
def admin_js(request: Request) -> FileResponse:
    _require_admin_host(request)
    return FileResponse(
        STATIC_ROOT / "assets" / "admin.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/assets/sakura-icon.png")
def admin_icon(request: Request) -> FileResponse:
    _require_admin_host(request)
    return FileResponse(
        STATIC_ROOT / "assets" / "sakura-icon.png",
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/assets/sakura-ui.woff2")
def admin_font(request: Request) -> FileResponse:
    _require_admin_host(request)
    return FileResponse(
        STATIC_ROOT / "assets" / "sakura-ui.woff2",
        media_type="font/woff2",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/api/overview")
def overview(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    _require_admin_host(request)
    since = _since(days)
    try:
        with _read_connection() as connection:
            totals = _one(
                connection.execute(
                    """
                    WITH observed AS (
                        SELECT installation_id FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                        UNION ALL
                        SELECT installation_id FROM telemetry_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                        UNION ALL
                        SELECT installation_id FROM model_call_metrics WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    )
                    SELECT
                        COUNT(DISTINCT installation_id) AS active_installations,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS errors,
                        (SELECT COUNT(*) FROM telemetry_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS events,
                        (SELECT COUNT(*) FROM model_call_metrics WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS model_calls,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND severity IN ('error', 'critical') AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS error_severity_reports,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND severity = 'warning' AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS warning_reports,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND severity IS NULL AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS unclassified_reports,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', '-24 hours') AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS errors_24h,
                        (SELECT COUNT(*) FROM error_events WHERE received_at >= datetime('now', '+8 hours', '-48 hours') AND received_at < datetime('now', '+8 hours', '-24 hours') AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0) AS errors_previous_24h
                    FROM observed
                    """,
                    (since, since, since, since, since, since, since, since, since),
                )
            )
            versions = _rows(
                connection.execute(
                    """
                    WITH observations AS (
                        SELECT installation_id, app_version FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                        UNION ALL
                        SELECT installation_id, app_version FROM telemetry_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                        UNION ALL
                        SELECT installation_id, app_version FROM model_call_metrics WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    )
                    SELECT app_version,
                           COUNT(DISTINCT installation_id) AS installations,
                           COUNT(*) AS observations
                    FROM observations
                    GROUP BY app_version
                    ORDER BY installations DESC, observations DESC, app_version DESC
                    LIMIT 12
                    """,
                    (since, since, since),
                )
            )
            platforms = _rows(
                connection.execute(
                    """
                    WITH observations AS (
                        SELECT installation_id, platform FROM error_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                        UNION ALL
                        SELECT installation_id, platform FROM telemetry_events WHERE received_at >= datetime('now', '+8 hours', ?) AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    )
                    SELECT platform,
                           COUNT(DISTINCT installation_id) AS installations,
                           COUNT(*) AS observations
                    FROM observations
                    GROUP BY platform
                    ORDER BY installations DESC, observations DESC, platform
                    """,
                    (since, since),
                )
            )
            top_errors = _rows(
                connection.execute(
                    """
                    SELECT error_code, severity, COUNT(*) AS occurrences,
                           COUNT(DISTINCT installation_id) AS installations,
                           MAX(received_at) AS last_seen
                    FROM error_events
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY error_code, severity
                    ORDER BY occurrences DESC, last_seen DESC
                    LIMIT 8
                    """,
                    (since,),
                )
            )
            latest = connection.execute(
                """
                SELECT MAX(received_at) FROM (
                    SELECT MAX(received_at) AS received_at FROM error_events WHERE instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    UNION ALL SELECT MAX(received_at) FROM telemetry_events WHERE instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    UNION ALL SELECT MAX(received_at) FROM model_call_metrics WHERE instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                )
                """
            ).fetchone()[0]
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")
    return {
        "rangeDays": days,
        "latestReceivedAt": latest,
        "totals": totals,
        "versions": versions,
        "platforms": platforms,
        "topErrors": top_errors,
    }


@router.get("/admin/api/errors")
def errors(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_admin_host(request)
    since = _since(days)
    try:
        with _read_connection() as connection:
            top = _rows(
                connection.execute(
                    """
                    SELECT error_code, severity, location, component, event, fingerprint,
                           json_extract(report_json, '$.evidence.diagnostic') AS diagnostic,
                           COUNT(*) AS occurrences,
                           COUNT(DISTINCT installation_id) AS installations,
                           MIN(received_at) AS first_seen,
                           MAX(received_at) AS last_seen
                    FROM error_events
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY error_code, severity, location, component, event, coalesce(group_key,fingerprint)
                    ORDER BY occurrences DESC, last_seen DESC
                    LIMIT 50
                    """,
                    (since,),
                )
            )
            by_version = _rows(
                connection.execute(
                    """
                    SELECT app_version, COUNT(*) AS occurrences,
                           COUNT(DISTINCT installation_id) AS installations
                    FROM error_events
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY app_version
                    ORDER BY occurrences DESC, app_version DESC
                    """,
                    (since,),
                )
            )
            daily = _rows(
                connection.execute(
                    """
                    SELECT date(received_at) AS day,
                           COUNT(*) AS occurrences,
                           COUNT(DISTINCT installation_id) AS installations
                    FROM error_events
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY date(received_at)
                    ORDER BY day
                    """,
                    (since,),
                )
            )
            recent = _rows(
                connection.execute(
                    """
                    SELECT report_id, installation_id,
                           received_at,
                           app_version, platform, component, event, error_code,
                           severity, location, fingerprint
                    FROM error_events
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    ORDER BY received_at DESC, id DESC
                    LIMIT ?
                    """,
                    (since, limit),
                )
            )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")
    return {
        "rangeDays": days,
        "top": top,
        "byVersion": by_version,
        "daily": daily,
        "recent": recent,
    }


@router.get("/admin/api/reports/{report_id}")
def report(request: Request, report_id: str) -> dict[str, Any]:
    _require_admin_host(request)
    report_id = _canonical_uuid(report_id)
    try:
        with _read_connection() as connection:
            row = connection.execute(
                """
                SELECT received_at,
                       report_id, installation_id, run_id, operation_id,
                       app_version, build, release_channel, platform, os_version,
                       arch, webview_version, component, event, error_code,
                       severity, location, exception_type, fingerprint,
                       install_kind, upgraded_from,
                       stack_json, breadcrumbs_json, details_json, generation, build_id, schema_version, report_json
                FROM error_events
                WHERE report_id = ?
                LIMIT 1
                """,
                (report_id,),
            ).fetchone()
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")
    if row is None:
        _not_found()
    data = dict(row)
    return {
        "details": json.loads(data["details_json"])
        if data.get("details_json")
        else None,
        "evidence": json.loads(data["report_json"]).get("evidence") if data.get("report_json") else None,
        "rawReport": json.loads(data["report_json"]) if data.get("report_json") else None,
        "generation": data.get("generation"),
        "schema": data.get("schema_version"),
        "receivedAt": data["received_at"],
        "reportId": data["report_id"],
        "installationId": data["installation_id"],
        "runId": data["run_id"],
        "operationId": data["operation_id"],
        "app": {
            "version": data["app_version"],
            "build": data.get("build_id") or data["build"],
            "channel": data["release_channel"],
        },
        "system": {
            "platform": data["platform"],
            "osVersion": data["os_version"],
            "arch": data["arch"],
            "webviewVersion": data["webview_version"],
        },
        "error": {
            "component": data["component"],
            "event": data["event"],
            "code": data["error_code"],
            "severity": data["severity"],
            "location": data["location"],
            "exceptionType": data["exception_type"],
            "fingerprint": data["fingerprint"],
        },
        "context": {
            "installKind": data["install_kind"],
            "upgradedFrom": data["upgraded_from"],
        },
        "stack": _safe_json(data["stack_json"]),
        "breadcrumbs": _safe_json(data["breadcrumbs_json"]),
    }


@router.get("/admin/api/installations/{installation_id}")
def installation(
    request: Request,
    installation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    _require_admin_host(request)
    installation_id = _canonical_uuid(installation_id)
    try:
        with _read_connection() as connection:
            exists = connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM error_events WHERE installation_id = ?
                    UNION ALL
                    SELECT 1 FROM telemetry_events WHERE installation_id = ?
                    UNION ALL
                    SELECT 1 FROM model_call_metrics WHERE installation_id = ?
                )
                """,
                (installation_id, installation_id, installation_id),
            ).fetchone()[0]
            if not exists:
                _not_found()
            counts = _one(
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM error_events WHERE installation_id = ?) AS errors,
                        (SELECT COUNT(*) FROM telemetry_events WHERE installation_id = ?) AS events,
                        (SELECT COUNT(*) FROM model_call_metrics WHERE installation_id = ?) AS model_calls
                    """,
                    (installation_id, installation_id, installation_id),
                )
            )
            recent_errors = _rows(
                connection.execute(
                    """
                    SELECT report_id, received_at,
                           app_version, platform, component,
                           event, error_code, severity, location, fingerprint
                    FROM error_events
                    WHERE installation_id = ?
                    ORDER BY received_at DESC, id DESC
                    LIMIT ?
                    """,
                    (installation_id, limit),
                )
            )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")
    return {
        "installationId": installation_id,
        "retainedCounts": counts,
        "recentErrors": recent_errors,
    }


@router.get("/admin/api/model-metrics")
def model_metrics(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    _require_admin_host(request)
    since = _since(days)
    paired_request = "CASE WHEN request_estimated_tokens IS NOT NULL AND context_window_tokens > 0 THEN request_estimated_tokens END"
    paired_window = "CASE WHEN request_estimated_tokens IS NOT NULL AND context_window_tokens > 0 THEN context_window_tokens END"
    try:
        with _read_connection() as connection:
            summary = _one(
                connection.execute(
                    f"""
                    SELECT COUNT(*) AS calls,
                           SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successful_calls,
                           SUM(COALESCE(input_tokens, prompt_tokens)) AS input_tokens,
                           SUM(COALESCE(output_tokens, completion_tokens)) AS output_tokens,
                           ROUND(AVG(latency_ms), 1) AS average_latency_ms,
                           SUM({paired_request}) AS measured_request_tokens,
                           SUM({paired_window}) AS measured_context_window_tokens,
                           SUM(CASE WHEN COALESCE(input_tokens, prompt_tokens) IS NOT NULL THEN 1 ELSE 0 END) AS usage_samples,
                           SUM(CASE WHEN request_estimated_tokens IS NOT NULL AND context_window_tokens > 0 THEN 1 ELSE 0 END) AS context_samples
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    """,
                    (since,),
                )
            )
            composition = _one(
                connection.execute(
                    """
                    SELECT SUM(request_estimated_tokens) AS request_tokens,
                           SUM(COALESCE(history_estimated_tokens, 0)) AS history_tokens,
                           SUM(COALESCE(memory_estimated_tokens, 0)) AS memory_tokens,
                           SUM(COALESCE(dynamic_context_estimated_tokens, 0)) AS dynamic_context_tokens,
                           SUM(COALESCE(tool_schema_estimated_tokens, 0)) AS tool_schema_tokens,
                           SUM(MAX(request_estimated_tokens
                               - COALESCE(history_estimated_tokens, 0)
                               - COALESCE(memory_estimated_tokens, 0)
                               - COALESCE(dynamic_context_estimated_tokens, 0)
                               - COALESCE(tool_schema_estimated_tokens, 0), 0)) AS other_tokens
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                      AND request_estimated_tokens IS NOT NULL
                    """,
                    (since,),
                )
            )
            daily = _rows(
                connection.execute(
                    f"""
                    SELECT date(received_at) AS day,
                           COUNT(*) AS calls,
                           SUM(COALESCE(input_tokens, prompt_tokens)) AS input_tokens,
                           SUM(COALESCE(output_tokens, completion_tokens)) AS output_tokens,
                           SUM({paired_request}) AS measured_request_tokens,
                           SUM({paired_window}) AS measured_context_window_tokens
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY date(received_at)
                    ORDER BY day
                    """,
                    (since,),
                )
            )
            by_model = _rows(
                connection.execute(
                    """
                    SELECT model_family, COUNT(*) AS calls,
                           SUM(COALESCE(input_tokens, prompt_tokens)) AS input_tokens,
                           SUM(COALESCE(output_tokens, completion_tokens)) AS output_tokens,
                           ROUND(AVG(latency_ms), 1) AS average_latency_ms
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY model_family
                    ORDER BY calls DESC, model_family
                    """,
                    (since,),
                )
            )
            by_purpose = _rows(
                connection.execute(
                    """
                    SELECT purpose, outcome, COUNT(*) AS calls
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY purpose, outcome
                    ORDER BY calls DESC, purpose, outcome
                    """,
                    (since,),
                )
            )
            installations = _rows(
                connection.execute(
                    f"""
                    SELECT installation_id,
                           COUNT(*) AS calls,
                           COUNT(DISTINCT COALESCE(operation_id, 'call:' || id)) AS operations,
                           MAX(received_at) AS last_seen,
                           ROUND(AVG(100.0 * ({paired_request}) / NULLIF(({paired_window}), 0)), 2) AS average_context_window_usage_percent,
                           ROUND(MAX(100.0 * ({paired_request}) / NULLIF(({paired_window}), 0)), 2) AS peak_context_window_usage_percent
                    FROM model_call_metrics
                    WHERE received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    GROUP BY installation_id
                    ORDER BY calls DESC, last_seen DESC
                    LIMIT 100
                    """,
                    (since,),
                )
            )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")

    summary["contextWindowUsagePercent"] = _percent(
        summary.pop("measured_request_tokens", None),
        summary.pop("measured_context_window_tokens", None),
    )
    for row in daily:
        row["context_window_usage_percent"] = _percent(
            row.pop("measured_request_tokens", None),
            row.pop("measured_context_window_tokens", None),
        )

    request_tokens = composition.get("request_tokens")
    composition["shares"] = {
        "history": _percent(composition.get("history_tokens"), request_tokens),
        "memory": _percent(composition.get("memory_tokens"), request_tokens),
        "dynamicContext": _percent(
            composition.get("dynamic_context_tokens"), request_tokens
        ),
        "toolSchema": _percent(composition.get("tool_schema_tokens"), request_tokens),
        "other": _percent(composition.get("other_tokens"), request_tokens),
    }
    return {
        "rangeDays": days,
        "summary": summary,
        "composition": composition,
        "daily": daily,
        "byModel": by_model,
        "byPurpose": by_purpose,
        "installations": installations,
    }


@router.get("/admin/api/model-calls/{installation_id}")
def model_calls(
    request: Request,
    installation_id: str,
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    _require_admin_host(request)
    installation_id = _canonical_uuid(installation_id)
    since = _since(days)
    try:
        with _read_connection() as connection:
            rows = _rows(
                connection.execute(
                    """
                    SELECT received_at,
                           run_id, operation_id, app_version, model_call,
                           purpose, model_family, outcome, error_code, latency_ms,
                           context_window_tokens, context_window_source,
                           COALESCE(input_tokens, prompt_tokens) AS input_tokens,
                           COALESCE(output_tokens, completion_tokens) AS output_tokens,
                           cached_input_tokens, reasoning_tokens,
                           request_estimated_tokens,
                           history_estimated_tokens, memory_estimated_tokens,
                           dynamic_context_estimated_tokens,
                           tool_schema_estimated_tokens,
                           history_messages, memories, tool_count
                    FROM model_call_metrics
                    WHERE installation_id = ?
                      AND received_at >= datetime('now', '+8 hours', ?)
                      AND instr(lower(COALESCE(run_id, '')), 'acceptance') = 0
                    ORDER BY received_at DESC, id DESC
                    LIMIT ?
                    """,
                    (installation_id, since, limit),
                )
            )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE")

    if not rows:
        _not_found()

    for row in rows:
        request_tokens = row.get("request_estimated_tokens")
        components = {
            "history": row.get("history_estimated_tokens"),
            "memory": row.get("memory_estimated_tokens"),
            "dynamicContext": row.get("dynamic_context_estimated_tokens"),
            "toolSchema": row.get("tool_schema_estimated_tokens"),
        }
        known_tokens = sum(value or 0 for value in components.values())
        other_tokens = (
            max((request_tokens or 0) - known_tokens, 0)
            if request_tokens is not None
            else None
        )
        components["other"] = other_tokens
        row["context_window_usage_percent"] = _percent(
            request_tokens,
            row.get("context_window_tokens"),
        )
        row["composition"] = {
            key: {
                "tokens": value,
                "share": _percent(value, request_tokens),
            }
            for key, value in components.items()
        }

    return {
        "rangeDays": days,
        "installationId": installation_id,
        "calls": rows,
        "returned": len(rows),
        "limit": limit,
    }
