"""Offline Python -> Rust HTTP -> FastAPI -> SQLite -> ZIP acceptance check."""

import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def capture(destination):
    sys.path.insert(0, str(ROOT))
    from app.core_host.runtime_logging import install_runtime_logging, TELEMETRY_BRIDGE_PREFIX, CORE_BRIDGE_PREFIX
    from app.core_host.plugin_host_services import _ModelSlotsHostService
    from app.core.runtime_log import log_message
    from app.core.diagnostics import exception_diagnostics
    from app.plugins.dependencies import PluginDependencyRoots, PluginDependencyError

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
        # Snapshot failures are successful IPC responses with error states, not
        # process-boundary exceptions. Exercise their separate logging path.
        def failed_callback(*_):
            raise OSError(13, "模型配置读取失败", "C:/测试 用户/config.json")

        slots = _ModelSlotsHostService(failed_callback)
        for slot in ("chat", "summary"):
            slots.call("register", ["fixture.models", {
                "slotId": slot, "label": slot, "description": "", "modelKind": "chat_completion", "required": False, "order": 0,
            }, {"load": "cb_" + "a" * 32, "save": "cb_" + "b" * 32}])
        slots.snapshot()
        slots.call("register_provider", ["fixture.models", {"serviceKey": "fixture.model.service", "label": "Fixture"}, "cb_" + "c" * 32])
        slots.catalog()
        with tempfile.TemporaryDirectory(prefix="sakura-dependency-evidence-") as work:
            root = Path(work)
            (root / "requirements.txt").write_text("fixture-dependency==1.0\n", encoding="utf-8")
            try:
                PluginDependencyRoots(root).verified_root("fixture.dependencies", root)
            except PluginDependencyError as error:
                log_message("error", "插件启动失败", component="plugin", plugin_id="fixture.dependencies",
                            fields=exception_diagnostics(error, reason_code=error.code, stage="dependencies"))
    finally:
        bridge.close()
    lines = []
    for line in stream.getvalue().splitlines():
        if line.startswith(TELEMETRY_BRIDGE_PREFIX):
            lines.append(line[len(TELEMETRY_BRIDGE_PREFIX):])
        elif line.startswith(CORE_BRIDGE_PREFIX):
            record = json.loads(line[len(CORE_BRIDGE_PREFIX):])
            if record["event"] != "core.error.unhandled":
                lines.append(json.dumps({"kind": "runtimeLog", "record": record}, ensure_ascii=False).encode("utf-8"))
    assert len(lines) == 6
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
            sys.executable, "-m", "pytest", "-q", "services/sakura/tests/test_original_errors.py",
        ], cwd=ROOT, env=environment, check=True)
    print("原始错误已通过 Python、Rust HTTP、FastAPI、SQLite、详情和 ZIP 验证。")


if __name__ == "__main__":
    main()
