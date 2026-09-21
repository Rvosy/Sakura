"""Read an old analysis ZIP into an isolated DB, migrate, export and compare counts."""

import json, os, sys, tempfile, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
root = Path(tempfile.mkdtemp(prefix="sakura-legacy-compat-"))
os.chmod(root, 0o700)
os.environ["SAKURA_TELEMETRY_DB_PATH"] = str(root / "test.db")
import db
from v2_db import initialize_v2
from queries import Filters
from export_bundle import export_bundle
from verify_bundle import verify

counts = {}
db.initialize_database()
with zipfile.ZipFile(sys.argv[1]) as z, db.connect() as c:
    for table in ("error_events", "telemetry_events", "model_call_metrics"):
        name = next(n for n in z.namelist() if n.endswith("/" + table + ".jsonl"))
        columns = {r[1] for r in c.execute("PRAGMA table_info(" + table + ")")}
        counts[table] = 0
        with z.open(name) as stream:
            for line in stream:
                row = json.loads(line)
                row = {k: v for k, v in row.items() if k in columns}
                for k, v in row.items():
                    if isinstance(v, (list, dict)):
                        row[k] = json.dumps(v)
                c.execute(
                    "INSERT INTO "
                    + table
                    + " ("
                    + ",".join(row)
                    + ") VALUES ("
                    + ",".join("?" for _ in row)
                    + ")",
                    list(row.values()),
                )
                counts[table] += 1
    before = [
        tuple(r)
        for r in c.execute(
            "SELECT id,received_at,severity,location FROM error_events ORDER BY id"
        )
    ]
initialize_v2()
with db.connect() as c:
    assert before == [
        tuple(r)
        for r in c.execute(
            "SELECT id,received_at,severity,location FROM error_events ORDER BY id"
        )
    ]
output = root / "legacy-v2.zip"
m = export_bundle(output, Filters(includeTest=True))
v = verify(output)
assert all(v[t]["rows"] == count for t, count in counts.items())
print(json.dumps({"counts": counts, "timestampsUnchanged": True, "complete": True}))
