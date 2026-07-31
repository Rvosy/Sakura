"""Small, dependency-free runner for Sakura's repository verification profiles."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from .report import write_json_atomic


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("suites.json")
DEFAULT_REPORT_ROOT = REPO_ROOT / "temp" / "harness"


class HarnessError(ValueError):
    """Raised when a harness manifest or invocation is invalid."""


@dataclass(frozen=True)
class Case:
    case_id: str
    description: str
    argv: tuple[str, ...]
    timeout_seconds: float
    env: dict[str, str]


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(f"{field} must be a non-empty string")
    return value


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Load and validate the deliberately small v1 harness manifest."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"cannot load manifest {path}: {error}") from error

    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise HarnessError("manifest schema_version must be 1")
    if not isinstance(raw.get("profiles"), dict) or not raw["profiles"]:
        raise HarnessError("manifest profiles must be a non-empty object")
    if not isinstance(raw.get("cases"), list) or not raw["cases"]:
        raise HarnessError("manifest cases must be a non-empty array")

    case_ids: set[str] = set()
    for index, item in enumerate(raw["cases"]):
        field = f"cases[{index}]"
        if not isinstance(item, dict):
            raise HarnessError(f"{field} must be an object")
        case_id = _required_string(item.get("id"), f"{field}.id")
        if case_id in case_ids:
            raise HarnessError(f"duplicate case id: {case_id}")
        case_ids.add(case_id)
        argv = item.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(token, str) or not token for token in argv)
        ):
            raise HarnessError(f"{field}.argv must be a non-empty string array")
        timeout = item.get("timeout_seconds", 60)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise HarnessError(f"{field}.timeout_seconds must be positive")
        env = item.get("env", {})
        if not isinstance(env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in env.items()
        ):
            raise HarnessError(f"{field}.env must be a string-to-string object")

    for profile_name, profile in raw["profiles"].items():
        _required_string(profile_name, "profile name")
        if not isinstance(profile, dict):
            raise HarnessError(f"profile {profile_name} must be an object")
        selected = profile.get("cases")
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(case_id, str) for case_id in selected)
        ):
            raise HarnessError(f"profile {profile_name}.cases must be a non-empty array")
        unknown = [case_id for case_id in selected if case_id not in case_ids]
        if unknown:
            raise HarnessError(
                f"profile {profile_name} references unknown cases: {', '.join(unknown)}"
            )
    return raw


def _case_from_dict(item: dict[str, Any]) -> Case:
    return Case(
        case_id=item["id"],
        description=item.get("description", ""),
        argv=tuple(item["argv"]),
        timeout_seconds=float(item.get("timeout_seconds", 60)),
        env=dict(item.get("env", {})),
    )


def _resolve_argv(argv: Sequence[str]) -> list[str]:
    replacements = {"{python}": sys.executable, "{repo}": str(REPO_ROOT)}
    return [replacements.get(token, token) for token in argv]


def _default_report_path(profile_name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_REPORT_ROOT / f"{stamp}-{profile_name}.json"


def _write_captured_output(value: str, stream: Any) -> None:
    """Write captured UTF-8 output even when the Windows console uses GBK."""
    if not value:
        return
    encoding = getattr(stream, "encoding", None)
    console_value = value
    if encoding:
        console_value = value.encode(encoding, errors="backslashreplace").decode(encoding)
    print(
        console_value,
        end="" if console_value.endswith("\n") else "\n",
        file=stream,
    )


def run_profile(
    manifest: dict[str, Any],
    profile_name: str,
    *,
    report_path: Path | None = None,
) -> tuple[int, dict[str, Any], Path]:
    """Run a profile sequentially and persist a stable JSON report."""
    profiles = manifest["profiles"]
    if profile_name not in profiles:
        raise HarnessError(f"unknown profile: {profile_name}")

    indexed = {item["id"]: _case_from_dict(item) for item in manifest["cases"]}
    cases = [indexed[case_id] for case_id in profiles[profile_name]["cases"]]
    started_at = datetime.now(UTC)
    results: list[dict[str, Any]] = []

    for case in cases:
        argv = _resolve_argv(case.argv)
        env = os.environ.copy()
        env.update(case.env)
        print(f"[harness] RUN  {case.case_id}: {case.description}", flush=True)
        case_started = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=case.timeout_seconds,
                check=False,
            )
            exit_code: int | None = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as error:
            timed_out = True
            exit_code = None
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        duration = round(time.monotonic() - case_started, 3)
        passed = exit_code == 0 and not timed_out
        label = "PASS" if passed else "FAIL"
        print(f"[harness] {label} {case.case_id} ({duration:.3f}s)", flush=True)
        _write_captured_output(stdout, sys.stdout)
        _write_captured_output(stderr, sys.stderr)
        results.append(
            {
                "id": case.case_id,
                "description": case.description,
                "status": "passed" if passed else "failed",
                "argv": argv,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_seconds": case.timeout_seconds,
                "duration_seconds": duration,
                "stdout": stdout,
                "stderr": stderr,
            }
        )

    finished_at = datetime.now(UTC)
    passed_count = sum(result["status"] == "passed" for result in results)
    report = {
        "schema_version": 1,
        "profile": profile_name,
        "status": "passed" if passed_count == len(results) else "failed",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "summary": {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
        },
        "cases": results,
    }
    destination = write_json_atomic(
        report_path or _default_report_path(profile_name), report
    )
    return (0 if report["status"] == "passed" else 1), report, destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sakura repository harness profiles.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="path to a v1 harness manifest",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list profiles and cases")
    run_parser = subparsers.add_parser("run", help="run one profile")
    run_parser.add_argument("profile", nargs="?", default="smoke")
    run_parser.add_argument("--report", type=Path, help="write JSON report to this path")
    current_parser = subparsers.add_parser(
        "current", help="show the active or stabilizing Work Package"
    )
    current_parser.add_argument("--json", action="store_true", help="emit JSON")
    preflight_parser = subparsers.add_parser(
        "preflight", help="validate a task before implementation"
    )
    preflight_parser.add_argument("task", nargs="?")
    preflight_parser.add_argument("--active", action="store_true")
    check_parser = subparsers.add_parser(
        "check", help="check task scope, dependencies and frozen boundaries"
    )
    check_parser.add_argument("task", nargs="?")
    check_parser.add_argument("--active", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify", help="run full task verification"
    )
    verify_parser.add_argument("task", nargs="?")
    verify_parser.add_argument("--active", action="store_true")
    verify_parser.add_argument("--report", type=Path, help="write JSON report to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "current":
            from .task_runner import current_task

            result = current_task()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                print(
                    f"{result['task']} {result['status']} "
                    f"source={result['status_source']}"
                )
            return 0
        if args.command in {"preflight", "check", "verify"}:
            from .git_state import GitStateError
            from .task_contract import ContractError
            from .task_runner import (
                _default_task_report,
                check_task,
                current_task,
                preflight_task,
                verify_task,
            )
            from .work_packages import WorkPackageError

            if bool(args.task) == bool(args.active):
                raise HarnessError(
                    f"{args.command} requires exactly one task argument or --active"
                )
            task_id = current_task()["task"] if args.active else args.task
            if args.command == "preflight":
                exit_code, result = preflight_task(
                    task_id, manifest_path=args.manifest
                )
            elif args.command == "check":
                exit_code, result = check_task(task_id, manifest_path=args.manifest)
            else:
                destination = args.report or _default_task_report(REPO_ROOT, task_id)
                exit_code, result = verify_task(
                    task_id,
                    manifest_path=args.manifest,
                    report_path=destination,
                )
                print(f"[harness] report={destination.resolve()}")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return exit_code

        manifest = load_manifest(args.manifest)
        if args.command == "list":
            cases = {item["id"]: item for item in manifest["cases"]}
            for name, profile in manifest["profiles"].items():
                print(f"{name}: {profile.get('description', '')}")
                for case_id in profile["cases"]:
                    print(f"  - {case_id}: {cases[case_id].get('description', '')}")
            return 0
        exit_code, report, destination = run_profile(
            manifest,
            args.profile,
            report_path=args.report,
        )
    except (HarnessError, ValueError) as error:
        print(f"harness error: {error}", file=sys.stderr)
        return 2
    print(
        f"[harness] {report['status'].upper()} "
        f"{report['summary']['passed']}/{report['summary']['total']} "
        f"report={destination}"
    )
    return exit_code
