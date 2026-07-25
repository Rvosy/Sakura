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

CORE_HOST_PYTEST_COMMAND = (
    "PYTHONDONTWRITEBYTECODE=1 python -m pytest "
    "tests/unit/test_core_host_*.py tests/integration/test_core_host_*.py"
)


def _triggers() -> dict[str, object]:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return document["on"]


def test_platform_workflow_runs_once_for_python_core_only_changes() -> None:
    triggers = _triggers()
    push_paths = set(triggers["push"]["paths"])
    pull_request_paths = set(triggers["pull_request"]["paths"])

    assert REQUIRED_PLATFORM_TRIGGER_PATHS <= push_paths
    assert REQUIRED_PLATFORM_TRIGGER_PATHS <= pull_request_paths


def test_each_native_platform_runs_the_explicit_core_host_pytest_gate() -> None:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = document["jobs"]
    matrix_job = next(job for job in jobs.values() if "strategy" in job)
    names = [item.get("name") for item in matrix_job["steps"]]
    python_index = names.index("Select the frozen Python test interpreter")
    dependency_index = names.index("Install Python Core Host test dependencies")
    acceptance_index = names.index("Run Python Core Host acceptance")
    step = next(
        item
        for item in matrix_job["steps"]
        if item.get("name") == "Run Python Core Host acceptance"
    )

    assert python_index < dependency_index < acceptance_index
    python_step = matrix_job["steps"][python_index]
    assert python_step["uses"] == "actions/setup-python@v6"
    assert python_step["with"] == {"python-version": "3.12"}
    dependency_step = matrix_job["steps"][dependency_index]
    assert "requirements-dev.txt" in dependency_step["run"]
    assert "requirements-runtime-v2.txt" in dependency_step["run"]
    assert "PySide6>=6.7" in dependency_step["run"]
    assert step.get("if") is None
    assert "working-directory" not in step
    assert step["env"] == {
        "PYTHONIOENCODING": "utf-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    assert " ".join(step["run"].split()) == CORE_HOST_PYTEST_COMMAND


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
