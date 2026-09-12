import json
import os
import zipfile
from pathlib import Path

from test_diagnostics import client, report
from export_bundle import export_bundle
from queries import Filters


def original_report(message, **overrides):
    return report(schema=3, fingerprintVersion=3, evidence={
        "diagnostic": message,
        "exception_chain": f"RuntimeError: import failed\nCaused by: OperationalError: {message}",
        "exception_stack": f"{message}\n  at migration:import_rows:42",
        "path": "C:\\Users\\测试 用户\\Sakura\\data\\memory.db",
        "endpoint": "https://provider.test/v1/chat/completions?model=test&token=[REDACTED]",
    }, **overrides)


def test_original_error_survives_ingestion_details_and_export(client, tmp_path):
    payload = original_report("no such table: memories; see https://example.test/help")
    assert client.post("/v3/errors", json=payload).status_code == 202
    assert client.post("/v3/errors", json=payload).status_code == 202
    listing = client.get("/admin/api/v2/records/errors").json()
    assert listing["total"] == 1
    assert listing["items"][0]["evidence"] == payload["evidence"]
    details = client.get(f'/admin/api/reports/{payload["reportId"]}').json()
    assert details["evidence"] == payload["evidence"]
    assert details["rawReport"]["evidence"] == payload["evidence"]
    bundle = tmp_path / "report.zip"
    export_bundle(bundle, Filters())
    with zipfile.ZipFile(bundle) as archive:
        rows = [json.loads(line) for line in archive.read("error_events.jsonl").splitlines()]
        assert rows[0]["report"]["evidence"] == payload["evidence"]
        assert rows[0]["evidence"] == payload["evidence"]
    assert client.get("/admin/api/v2/records/errors", params={"q": "no such table"}).json()["total"] == 1


def test_grouping_compares_actual_cause_across_runs_and_keeps_legacy(client):
    first = original_report("no such table: memories")
    second = original_report("no such table: memories", runId="run-2")
    third = original_report("UNIQUE constraint failed: memories.id", runId="run-3")
    for index, payload in enumerate((first, second, third)):
        payload["error"]["fingerprint"] = f"sample-{index}"
        assert client.post("/v3/errors", json=payload).status_code == 202
    groups = client.get("/admin/api/v2/groups").json()
    assert groups["total"] == 2
    assert sorted(row["reports"] for row in groups["items"]) == [1, 2]
    grouped = next(row for row in groups["items"] if row["reports"] == 2)
    assert client.get("/admin/api/v2/records/errors", params={"group": grouped["fingerprint"]}).json()["total"] == 2
    old = report()
    assert client.post("/v2/errors", json=old).status_code == 202
    assert client.get(f'/admin/api/reports/{old["reportId"]}').json()["evidence"] is None


def test_original_report_still_rejects_wrong_types_and_oversize_body(client):
    payload = original_report("original failure")
    payload["evidence"]["unexpected_object"] = {"request": "not a scalar diagnostic"}
    assert client.post("/v3/errors", json=payload).status_code == 400
    assert client.post("/v3/errors", content=b" " * (128 * 1024 + 1), headers={"content-type": "application/json"}).status_code == 413


def test_actual_rust_wire_when_provided(client, tmp_path):
    """The integration command feeds Python bridge records through the Rust sender."""
    capture = os.environ.get("SAKURA_ACCEPTANCE_WIRE_OUTPUT")
    if not capture:
        import pytest
        pytest.skip("requires the Python -> Rust wire capture")
    reports = []
    for item in json.loads(Path(capture).read_text()):
        payload = item["body"]
        assert client.post(item["endpoint"], json=payload).status_code == 202
        if payload.get("schema") == 3:
            reports.append(payload)
            details = client.get(f'/admin/api/reports/{payload["reportId"]}').json()
            assert details["evidence"] == payload["evidence"]
    assert len(reports) >= 2
    assert len({r["evidence"]["diagnostic"] for r in reports}) >= 2
    bundle = tmp_path / "actual-wire.zip"
    export_bundle(bundle, Filters(includeTest=True))
    with zipfile.ZipFile(bundle) as archive:
        rows = [json.loads(line) for line in archive.read("error_events.jsonl").splitlines()]
    assert {r["reportId"]: r["evidence"] for r in reports} == {r["report_id"]: r["evidence"] for r in rows}
