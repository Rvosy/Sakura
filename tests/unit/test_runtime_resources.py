from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.core.runtime_resources import ResourceRegistry, ResourceState


ROOT = Path(__file__).resolve().parents[2]


def test_core_memory_resource_imports_reject_pyside_in_fresh_process() -> None:
    script = r"""
import importlib.abc
import sys

class RejectPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise AssertionError(f"forbidden Qt import: {fullname}")
        return None

sys.meta_path.insert(0, RejectPySide())

from app.core.runtime_resources import ResourceRegistry, ThreadGroupResource
from app.agent.memory import MemoryStore
from app.core_host.memory_boundary import MemoryBoundary

assert ResourceRegistry.__module__ == "app.core.runtime_resources"
assert ThreadGroupResource.__module__ == "app.core.runtime_resources"
assert MemoryStore is not None
assert MemoryBoundary is not None
assert not any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules)
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_qt_free_registry_owns_and_stops_services() -> None:
    registry = ResourceRegistry()
    stopped: list[str] = []
    resource = registry.track_service(stop=lambda: stopped.append("service"))

    assert resource.health() is ResourceState.READY
    registry.stop_all()

    assert stopped == ["service"]
    assert resource.health() is ResourceState.STOPPED
    assert registry._resources == []
