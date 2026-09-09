import json
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
import db, queries, exports, admin
from app import app
from export_bundle import export_bundle, ExportLimitError
from verify_bundle import verify


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setattr(admin, "DB_PATH", database)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(queries, "DB_PATH", database)
    monkeypatch.setattr(exports, "ROOT", tmp_path / "exports")
    exports.JOBS.clear()
    with TestClient(app, base_url="http://admin.cialloo.cn") as client:
        yield client


def report(**overrides):
    value = {
        "schema": 2,
        "reportId": str(uuid.uuid4()),
        "installationId": "550e8400-e29b-41d4-a716-446655440000",
        "runId": "run-1",
        "operationId": "op-1",
        "app": {"version": "1.1.0", "build": "abcdef", "channel": "stable"},
        "system": {"platform": "windows"},
        "error": {
            "component": "webview",
            "event": "webview.error.unhandled",
            "code": "WEBVIEW_UNHANDLED_ERROR",
            "exceptionType": "TypeError",
            "fingerprint": "f-issue",
        },
        "diagnostics": {
            "buildId": "abcdef",
            "environment": "production",
            "generation": "g1",
            "occurredMs": 123,
        },
        "fingerprintVersion": 2,
        "details": {
            "severity": "error",
            "impact": "unavailable",
            "stage": "javascript",
            "file": "desktop/frontend/settings/settings.js",
            "line": 42,
        },
    }
    value.update(overrides)
    return value


def test_error_to_export_preserves_source_and_unknowns(client, tmp_path):
    r = report()
    assert client.post("/v2/errors", json=r).status_code == 202
    assert client.post("/v2/errors", json=r).status_code == 202
    listing = client.get("/admin/api/v2/records/errors").json()
    assert listing["total"] == 1 and listing["items"][0]["details"]["line"] == 42
    assert listing["items"][0]["reason_code"] is None
    path = tmp_path / "bundle.zip"
    result = export_bundle(path, queries.Filters())
    assert result["counts"]["error_events"]["exportedRows"] == 1
    assert all(set(info) == {"bytes"} for info in result["files"].values())
    assert verify(path)["error_events"]["rows"] == 1
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(path) as archive:
        archive.extractall(unpacked)
    with (unpacked / "groups.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="size mismatch"):
        verify(unpacked)


def test_v1_v2_compatibility_time_and_atomic_validation(client):
    legacy = report()
    legacy["schema"] = 1
    for k in ("diagnostics", "details", "fingerprintVersion"):
        legacy.pop(k)
    assert client.post("/v1/errors", json=legacy).status_code == 202
    with db.connect() as c:
        old = c.execute("SELECT received_at FROM error_events").fetchone()[0]
    db.initialize_database()
    from v2_db import initialize_v2

    initialize_v2()
    with db.connect() as c:
        assert c.execute("SELECT received_at FROM error_events").fetchone()[0] == old
    invalid = report()
    invalid["details"]["file"] = "C:/Users/private/secret.js"
    assert client.post("/v2/errors", json=invalid).status_code == 400
    private = report()
    private["details"]["reasonCode"] = "PRIVATE_CHAT_BODY"
    assert client.post("/v2/errors", json=private).status_code == 400
    assert client.get("/admin/api/v2/records/errors").json()["total"] == 1


def test_full_pagination_and_run_scoping(client):
    for i in range(205):
        r = report(runId="old-run" if i < 204 else "new-run")
        assert client.post("/v2/errors", json=r).status_code == 202
    first = client.get("/admin/api/v2/records/errors?limit=200").json()
    assert first["total"] == 205 and len(first["items"]) == 200
    second = client.get(
        "/admin/api/v2/records/errors",
        params={"limit": 200, "cursor": first["nextCursor"]},
    ).json()
    assert len(second["items"]) == 5
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert (
        client.get("/admin/api/v2/records/errors?run=new-run&operation=op-1").json()[
            "total"
        ]
        == 1
    )


def test_export_excludes_acceptance_and_fails_without_partial_zip(client, tmp_path):
    r = report()
    r["diagnostics"]["environment"] = "acceptance"
    assert client.post("/v2/errors", json=r).status_code == 202
    path = tmp_path / "empty.zip"
    m = export_bundle(path, queries.Filters())
    assert m["counts"]["error_events"]["exportedRows"] == 0
    failed = tmp_path / "failed.zip"
    with pytest.raises(ExportLimitError):
        export_bundle(failed, queries.Filters(), max_bytes=1)
    assert not failed.exists()


def test_admin_export_host_isolation_and_unknown_job(client):
    assert (
        client.post(
            "/admin/api/v2/exports", json={}, headers={"Host": "telemetry.cialloo.cn"}
        ).status_code
        == 404
    )
    assert client.get("/admin/api/v2/exports/" + str(uuid.uuid4())).status_code == 404


def event(r, name="error.repeated", details=None):
    return {
        "installationId": r["installationId"],
        "runId": r["runId"],
        "operationId": r["operationId"],
        "appVersion": "1.1.0",
        "platform": "windows",
        "event": name,
        "diagnostics": r["diagnostics"],
        "details": details or {"fingerprint": "f-issue", "occurrenceCount": 5},
    }


def test_cumulative_repeats_and_association_do_not_cross_run(client, tmp_path):
    r = report()
    assert client.post("/v2/errors", json=r).status_code == 202
    for count in (5, 7, 7):
        e = event(r, details={"fingerprint": "f-issue", "occurrenceCount": count})
        assert (
            client.post("/v2/events", json={"schema": 2, "items": [e]}).status_code
            == 202
        )
    foreign = event(report(runId="other-run"), "chat.finished", {"outcome": "failed"})
    assert (
        client.post("/v2/events", json={"schema": 2, "items": [foreign]}).status_code
        == 202
    )
    group = client.get("/admin/api/v2/groups").json()["items"][0]
    assert group["occurrences"] == 7 and group["reports"] == 1
    assert client.get("/admin/api/v2/timeline?operation=op-1").status_code == 400
    timeline = client.get(
        "/admin/api/v2/timeline",
        params={
            "installation": r["installationId"],
            "run": r["runId"],
            "generation": "g1",
            "operation": "op-1",
        },
    ).json()
    assert timeline["total"] == 4 and {x["run_id"] for x in timeline["items"]} == {
        "run-1"
    }
    p = tmp_path / "scoped.zip"
    m = export_bundle(p, queries.Filters(report=r["reportId"]))
    assert m["counts"]["telemetry_events"]["exportedRows"] == 3


def test_model_diagnostic_and_atomic_batch(client):
    r = report()
    m = {
        "installationId": r["installationId"],
        "runId": r["runId"],
        "operationId": "op-1",
        "appVersion": "1.1.0",
        "modelCall": 1,
        "purpose": "final_reply",
        "modelFamily": "openai",
        "outcome": "failed",
        "errorCode": "MODEL_REQUEST_FAILED",
        "latencyMs": 42,
        "contextWindowSource": "unknown",
        "diagnostics": r["diagnostics"],
        "request": {
            "faultDomain": "authentication",
            "reasonCode": "MODEL_AUTHENTICATION_FAILED",
            "httpStatus": 401,
            "attemptCount": 1,
            "stage": "response",
        },
    }
    assert (
        client.post("/v2/model-calls", json={"schema": 2, "items": [m]}).status_code
        == 202
    )
    data = client.get(
        "/admin/api/v2/records/model-calls?reason=MODEL_AUTHENTICATION_FAILED"
    ).json()
    assert data["total"] == 1 and data["items"][0]["http_status"] == 401
    bad = dict(m, request={"message": "PRIVATE_CHAT_BODY"})
    assert (
        client.post(
            "/v2/model-calls", json={"schema": 2, "items": [m, bad]}
        ).status_code
        == 400
    )
    assert client.get("/admin/api/v2/records/model-calls").json()["total"] == 1


def test_export_job_and_private_download(client):
    import time

    client.post("/v2/errors", json=report())
    created = client.post("/admin/api/v2/exports", json={})
    assert created.status_code == 202
    key = created.json()["id"]
    for _ in range(100):
        job = client.get("/admin/api/v2/exports/" + key).json()
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "ready"
    assert client.get("/admin/api/v2/exports/" + key + "/download").content.startswith(
        b"PK"
    )
    assert (
        client.get(
            "/admin/api/v2/exports/" + key + "/download",
            headers={"Host": "telemetry.cialloo.cn"},
        ).status_code
        == 404
    )
    assert (exports.ROOT / key / "analysis.zip").stat().st_mode & 0o777 == 0o600


def test_report_details_include_v2_and_query_plan_uses_run_index(client):
    r = report()
    client.post("/v2/errors", json=r)
    detail = client.get("/admin/api/reports/" + r["reportId"]).json()
    assert detail["details"]["line"] == 42 and detail["generation"] == "g1"
    with queries.connection() as c:
        plan = c.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM error_events WHERE installation_id=? AND run_id=? AND generation=? AND operation_id=? ORDER BY id",
            (r["installationId"], r["runId"], "g1", "op-1"),
        ).fetchall()
    assert any("run_operation" in p["detail"] for p in plan)


def test_ingestion_during_snapshot_export(client, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time, statistics

    r = report()
    with db.connect() as c:
        # Isolated scale fixture, retain the exact schema from genuine ingress.
        client.post("/v2/errors", json=r)
        for i in range(5000):
            c.execute(
                "INSERT INTO telemetry_events (received_at,installation_id,run_id,app_version,platform,event,environment,schema_version) VALUES (datetime('now','+8 hours'),?,'bulk-run','1.1.0','windows','app.started','production',1)",
                (r["installationId"],),
            )

    def ingest(i):
        start = time.perf_counter()
        response = client.post("/v2/errors", json=report(runId="load-run"))
        assert response.status_code == 202
        return time.perf_counter() - start

    with ThreadPoolExecutor(max_workers=9) as pool:
        export = pool.submit(export_bundle, tmp_path / "load.zip", queries.Filters())
        elapsed = list(pool.map(ingest, range(100)))
        assert export.result()["truncated"] is False
    p99 = sorted(elapsed)[98]
    assert p99 < 2.5
    print(
        f"local concurrent ingestion p99={p99:.4f}s; 5000 background rows, 100 reports, 8 concurrent producers"
    )


def test_retention_and_timezone_boundaries(client):
    from cleanup import DELETE_STATEMENTS

    r = report()
    client.post("/v2/errors", json=r)
    with db.connect() as c:
        c.execute("UPDATE error_events SET received_at='2026-09-09 00:00:00'")
    f = queries.Filters(
        start="2026-09-08T16:00:00+00:00", end="2026-09-09T00:01:00+08:00"
    )
    assert queries.page("errors", f)["total"] == 1
    with db.connect() as c:
        c.execute(
            "UPDATE error_events SET received_at=datetime('now','+8 hours','-91 days')"
        )
        for statement in DELETE_STATEMENTS:
            c.execute(statement)
    assert queries.page("errors", queries.Filters())["total"] == 0


def test_export_cancellation_limits_and_expired_download_cleanup(client, tmp_path):
    import threading, time

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ExportLimitError, match="EXPORT_CANCELLED"):
        export_bundle(
            tmp_path / "cancelled.zip", queries.Filters(), cancel_event=cancelled
        )
    with pytest.raises(ExportLimitError, match="EXPORT_TIME_LIMIT"):
        export_bundle(tmp_path / "timeout.zip", queries.Filters(), max_seconds=0)
    assert (
        not (tmp_path / "cancelled.zip").exists()
        and not (tmp_path / "timeout.zip").exists()
    )
    key = str(uuid.uuid4())
    (exports.ROOT / key).mkdir()
    (exports.ROOT / key / "analysis.zip").write_bytes(b"old")
    exports.JOBS[key] = {
        "created": time.time() - 7200,
        "completed": time.time() - 7200,
        "status": "ready",
        "readers": 1,
    }
    exports.cleanup()
    assert (exports.ROOT / key).exists()
    exports.JOBS[key]["readers"] = 0
    exports.cleanup()
    assert not (exports.ROOT / key).exists()
