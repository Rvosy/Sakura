from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "runtime-v2-platform-foundation.yml"
RUNTIME_REQUIREMENTS = REPO_ROOT / "requirements-runtime-v2.txt"

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


def test_native_platform_contract_tests_are_serialized() -> None:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = document["jobs"]
    matrix_job = next(job for job in jobs.values() if "strategy" in job)
    step = next(
        item
        for item in matrix_job["steps"]
        if item.get("name") == "Run platform contract and golden layout tests"
    )

    assert step["run"].split()[-2:] == ["--", "--test-threads=1"]


def test_exact_runtime_installs_the_minimal_assistant_dependency_closure() -> None:
    requirements = [
        line.strip()
        for line in RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirements == ["PyYAML>=6.0"]

    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = document["jobs"]
    matrix_job = next(job for job in jobs.values() if "strategy" in job)
    steps = matrix_job["steps"]
    names = [item.get("name") for item in steps]
    install_index = names.index("Install Runtime v2 Python dependencies")
    packaged_index = names.index("Stage the frozen packaged Runtime and Core resource layout")
    assert install_index < packaged_index

    install = steps[install_index]
    command = install["run"]
    assert "requirements-runtime-v2.txt" in command
    assert "--python-version 3.12" in command
    assert "--platform win_amd64" in command
    assert "Lib/site-packages" in command
    assert "bin/python3" in command
    assert "requirements.txt" not in command.replace("requirements-runtime-v2.txt", "")
