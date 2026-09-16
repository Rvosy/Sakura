"""Ingest the Rust loopback capture and verify only the resulting ZIP."""

import json, os, sys, tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
work = Path(tempfile.mkdtemp(prefix="sakura-v2-acceptance-"))
os.environ["SAKURA_TELEMETRY_DB_PATH"] = str(work / "telemetry.db")
os.environ["SAKURA_TELEMETRY_EXPORT_ROOT"] = str(work / "exports")
from fastapi.testclient import TestClient
from app import app
from queries import Filters
from export_bundle import export_bundle
from verify_bundle import verify

with TestClient(app, base_url="http://admin.cialloo.cn") as client:
    for record in json.loads(Path(sys.argv[1]).read_text()):
        response = client.post(record["endpoint"], json=record["body"])
        assert response.status_code == 202, (record["endpoint"], response.text)
    destination = work / "analysis.zip"
    export_bundle(destination, Filters(includeTest=True))
    result = verify(destination)
    import zipfile

    with zipfile.ZipFile(destination) as archive:
        rows = [json.loads(x) for x in archive.read("error_events.jsonl").splitlines()]
        assert rows[0]["reason_code"] == "TRANSPORT_WRITE_FAILED"
        assert any(
            f.get("file") == "app/core_host/server.py" and f.get("function") == "send"
            for f in rows[0]["stack"]
        )
        assert rows[0]["generation"] == "acceptance-generation" and rows[0][
            "build_id"
        ].startswith("development-")
        metrics = [
            json.loads(x) for x in archive.read("model_call_metrics.jsonl").splitlines()
        ]
        assert (
            metrics[0]["http_status"] == 401
            and metrics[0]["fault_domain"] == "authentication"
        )
        events = [
            json.loads(x) for x in archive.read("telemetry_events.jsonl").splitlines()
        ]
        assert any(
            e["event"] == "chat.finished" and e["details"]["outcome"] == "failed"
            for e in events
        )
        assert any(e["operation_id"] == metrics[0]["operation_id"] for e in events)
        timeline = [
            json.loads(line) for line in archive.read("timeline.jsonl").splitlines()
        ]
        assert any(
            e["event"] == "chat.finished" and e["outcome"] == "failed" for e in timeline
        )
        for name in (
            "error_events.jsonl",
            "model_call_metrics.jsonl",
            "telemetry_events.jsonl",
        ):
            assert b"PRIVATE" not in archive.read(name)
    print(
        json.dumps(
            {
                "zip": str(destination),
                "rows": {t: v["rows"] for t, v in result.items() if t != "groups"},
                "verified": True,
            }
        )
    )
