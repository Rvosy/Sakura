"""Offline Python -> Rust HTTP -> FastAPI -> SQLite -> ZIP acceptance check."""

import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def capture(destination):
    sys.path.insert(0, str(ROOT))
    from app.core_host.runtime_logging import install_runtime_logging, TELEMETRY_BRIDGE_PREFIX

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        for statement in ("SELECT * FROM missing_memories", "INSERT INTO memories VALUES (1)"):
            try:
                try:
                    with sqlite3.connect(":memory:") as database:
                        database.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
                        database.execute("INSERT INTO memories VALUES (1)")
                        database.execute(statement)
                except sqlite3.Error as cause:
                    raise RuntimeError("legacy import failed") from cause
            except RuntimeError as error:
                bridge.emit_unhandled("CORE_UNHANDLED_ERROR", error)
    finally:
        bridge.close()
    lines = [line[len(TELEMETRY_BRIDGE_PREFIX):] for line in stream.getvalue().splitlines()
             if line.startswith(TELEMETRY_BRIDGE_PREFIX)]
    assert len(lines) == 2
    Path(destination).write_bytes(b"\n".join(lines) + b"\n")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--capture":
        capture(sys.argv[2])
        return
    runtime = ROOT / ("runtime/python.exe" if os.name == "nt" else "runtime/bin/python")
    with tempfile.TemporaryDirectory(prefix="sakura-original-errors-") as work:
        wire = Path(work) / "core.jsonl"
        output = Path(work) / "http.json"
        subprocess.run([str(runtime), str(Path(__file__).resolve()), "--capture", str(wire)], cwd=ROOT, check=True)
        environment = {**os.environ, "SAKURA_ACCEPTANCE_CORE_WIRE": str(wire), "SAKURA_ACCEPTANCE_WIRE_OUTPUT": str(output)}
        subprocess.run([
            "cargo", "test", "--manifest-path", "desktop/src-tauri/Cargo.toml",
            "telemetry::tests::acceptance_wire_capture_preserves_core_location_and_terminal", "--", "--exact",
        ], cwd=ROOT, env=environment, check=True)
        subprocess.run([
            sys.executable, "-m", "pytest", "-q", "tools/telemetry_server/tests/test_original_errors.py",
        ], cwd=ROOT, env=environment, check=True)
    print("原始错误已通过 Python、Rust HTTP、FastAPI、SQLite、详情和 ZIP 验证。")


if __name__ == "__main__":
    main()
