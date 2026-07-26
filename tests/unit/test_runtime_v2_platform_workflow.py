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
    "tests/integration/test_chat_pipeline.py",
    "tests/unit/test_agent_runtime.py",
    "tests/unit/test_core_host_*.py",
    "tests/unit/test_hardening_regressions.py",
    "tests/unit/test_http_client.py",
    "tests/unit/test_runtime_v2_platform_workflow.py",
}

CORE_HOST_PYTEST_COMMAND = (
    "PYTHONDONTWRITEBYTECODE=1 python -m pytest "
    "tests/unit/test_core_host_*.py tests/integration/test_core_host_*.py "
    "tests/unit/test_hardening_regressions.py tests/unit/test_http_client.py"
)


def _triggers() -> dict[str, object]:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return document["on"]


def test_platform_workflow_avoids_duplicate_feature_branch_runs() -> None:
    triggers = _triggers()
    assert triggers["push"]["branches"] == ["main", "dev"]
    assert triggers["pull_request"]["branches"] == ["main", "dev"]

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
    assert "PyYAML==6.0.2" in dependency_step["run"]
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


def test_platform_acceptance_never_mutates_the_frozen_runtime() -> None:
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = document["jobs"]
    matrix_job = next(job for job in jobs.values() if "strategy" in job)
    acceptance_steps = matrix_job["steps"]
    staged_index = next(
        index
        for index, step in enumerate(acceptance_steps)
        if step.get("name") == "Stage exact Windows development Runtime"
    )
    commands_after_staging = "\n".join(
        str(step.get("run", "")) for step in acceptance_steps[staged_index + 1 :]
    )
    assert "requirements-runtime-v2.txt" not in WORKFLOW.read_text(encoding="utf-8")
    assert "Install Runtime v2 Python dependencies" not in {
        step.get("name") for step in acceptance_steps
    }
    assert "--target \"$runtime_dir" not in commands_after_staging
    assert '"$runtime_dir/bin/python3" -m pip' not in commands_after_staging
    assert "Lib/site-packages" not in commands_after_staging
    assert "python312._pth" not in commands_after_staging
    dependency_step = next(
        step
        for step in acceptance_steps
        if step.get("name") == "Download and verify the frozen Assistant dependency artifact"
    )
    assert "--selector assistantDependency" in dependency_step["run"]
    assert "developmentRelativePath" in dependency_step["run"]
    assert "$GITHUB_WORKSPACE/$dependency_path" in dependency_step["run"]
