from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "runtime-v2-platform-foundation.yml"

REQUIRED_PLATFORM_TRIGGER_PATHS = {
    ".github/workflows/runtime-v2-platform-foundation.yml",
    "desktop/src-tauri/**",
    "desktop/tests/**",
    "app/**",
    "requirements*.txt",
    "tests/fixtures/runtime_v2/**",
    "tests/integration/test_core_host_*.py",
    "tests/unit/test_core_host_*.py",
    "tests/unit/test_runtime_v2_platform_workflow.py",
}


def _event_paths() -> tuple[set[str], set[str]]:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    triggers = document["on"]
    return set(triggers["push"]["paths"]), set(triggers["pull_request"]["paths"])


def test_platform_workflow_cannot_skip_python_core_only_changes() -> None:
    push_paths, pull_request_paths = _event_paths()

    assert push_paths == pull_request_paths
    assert REQUIRED_PLATFORM_TRIGGER_PATHS <= push_paths
