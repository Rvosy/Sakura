from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PREFIXES = (
    "PySide6",
    "app.ui",
    "app.agent",
    "app.brain_host",
    "app.plugins",
    "app.voice",
)


def test_minimal_core_host_import_graph_has_no_qt_or_domain_modules() -> None:
    probe = """
import json
import sys
import app.core_host.__main__
import app.core_host.protocol
import app.core_host.server
forbidden = sorted(
    name for name in sys.modules
    if name.startswith((
        'PySide6', 'app.ui', 'app.agent', 'app.brain_host', 'app.plugins', 'app.voice'
    ))
)
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    imported = json.loads(completed.stdout)
    assert imported == []
    assert all(
        not name.startswith(FORBIDDEN_PREFIXES) for name in imported
    )
