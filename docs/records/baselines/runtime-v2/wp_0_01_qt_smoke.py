from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil


RESULT_PREFIX = "WP001_RESULT="


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated WP-0-01 Qt smoke baseline.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--result-dir", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def _run_child(workspace: Path) -> int:
    workspace = workspace.resolve()
    os.chdir(workspace)
    sys.path.insert(0, str(workspace))

    import main as sakura_main
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    state: dict[str, Any] = {
        "visible_at": None,
        "request_quit": None,
        "timed_out": False,
    }
    original_show = sakura_main.PetWindow.show

    def tracked_show(window: Any) -> None:
        original_show(window)

        visible_timer = QTimer(window)
        visible_timer.setInterval(10)

        def wait_for_visible() -> None:
            application = QApplication.instance()
            top_levels = QApplication.topLevelWidgets() if application is not None else []
            if window not in top_levels or not window.isVisible():
                return
            state["visible_at"] = time.perf_counter()
            visible_timer.stop()

            def request_quit() -> None:
                state["request_quit"] = bool(window.request_quit())

            QTimer.singleShot(1000, request_quit)

        visible_timer.timeout.connect(wait_for_visible)
        visible_timer.start()

        watchdog = QTimer(window)
        watchdog.setSingleShot(True)

        def stop_on_timeout() -> None:
            state["timed_out"] = True
            application = QApplication.instance()
            if application is not None:
                application.quit()

        watchdog.timeout.connect(stop_on_timeout)
        watchdog.start(15000)
        window._wp_0_01_smoke_timers = (visible_timer, watchdog)

    sakura_main.PetWindow.show = tracked_show
    return_code = sakura_main.main()
    result = {
        **state,
        "return_code": return_code,
    }
    print(f"{RESULT_PREFIX}{json.dumps(result, ensure_ascii=False)}", flush=True)
    return int(
        return_code != 0
        or state["visible_at"] is None
        or state["request_quit"] is not True
        or state["timed_out"]
    )


def _process_details(process: psutil.Process) -> dict[str, Any]:
    try:
        return {
            "pid": process.pid,
            "name": process.name(),
            "exe": process.exe(),
            "cmdline": process.cmdline(),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return {"pid": process.pid, "unavailable": True}


def _stop_recorded_processes(processes: list[psutil.Process]) -> None:
    if not processes:
        return
    for process in processes:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    _, alive = psutil.wait_procs(processes, timeout=2)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    psutil.wait_procs(alive, timeout=2)


def _parse_child_result(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _run_sample(
    workspace: Path,
    result_dir: Path,
    sample_index: int,
) -> dict[str, Any]:
    stdout_path = result_dir / f"qt-smoke-{sample_index:02d}.stdout.log"
    stderr_path = result_dir / f"qt-smoke-{sample_index:02d}.stderr.log"
    started_at = time.perf_counter()
    seen_descendants: dict[int, psutil.Process] = {}

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--workspace",
                str(workspace),
            ],
            cwd=workspace,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
        )
        root_process = psutil.Process(process.pid)
        deadline = time.monotonic() + 20
        timed_out = False
        while process.poll() is None:
            try:
                for child in root_process.children(recursive=True):
                    seen_descendants[child.pid] = child
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            if time.monotonic() >= deadline:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                break
            time.sleep(0.05)

    time.sleep(1)
    living_descendants: list[psutil.Process] = []
    for child in seen_descendants.values():
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                living_descendants.append(child)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    residual_details = [_process_details(child) for child in living_descendants]
    _stop_recorded_processes(living_descendants)

    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    child_result = _parse_child_result(stdout)
    visible_at = child_result.get("visible_at") if child_result else None
    visible_ms = (
        (float(visible_at) - started_at) * 1000
        if isinstance(visible_at, (int, float))
        else None
    )
    return {
        "sample": sample_index,
        "process_return_code": process.returncode,
        "timed_out": timed_out,
        "visible_ms": visible_ms,
        "child_result": child_result,
        "stderr_empty": not stderr.strip(),
        "residual_processes": residual_details,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def _run_parent(workspace: Path, samples: int, result_dir: Path) -> int:
    workspace = workspace.resolve()
    result_dir = result_dir.resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    if samples < 1:
        raise ValueError("samples must be at least 1")

    results = [
        _run_sample(workspace, result_dir, sample_index)
        for sample_index in range(1, samples + 1)
    ]
    visible_values = [
        float(result["visible_ms"])
        for result in results
        if isinstance(result.get("visible_ms"), (int, float))
    ]
    sorted_values = sorted(visible_values)
    statistics_payload: dict[str, float] | None = None
    if sorted_values:
        p95_index = max(0, math.ceil(0.95 * len(sorted_values)) - 1)
        statistics_payload = {
            "min_ms": min(sorted_values),
            "median_ms": statistics.median(sorted_values),
            "mean_ms": statistics.fmean(sorted_values),
            "p95_nearest_rank_ms": sorted_values[p95_index],
            "max_ms": max(sorted_values),
        }

    passed = all(
        result["process_return_code"] == 0
        and not result["timed_out"]
        and result["visible_ms"] is not None
        and result["child_result"] is not None
        and result["child_result"].get("return_code") == 0
        and result["child_result"].get("request_quit") is True
        and result["stderr_empty"]
        and not result["residual_processes"]
        for result in results
    )
    report = {
        "workspace": str(workspace),
        "python": sys.executable,
        "samples": samples,
        "passed": passed,
        "statistics": statistics_payload,
        "results": results,
    }
    report_path = result_dir / "qt-smoke-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def main() -> int:
    args = _parse_args()
    workspace = args.workspace.resolve()
    if args.child:
        return _run_child(workspace)
    result_dir = args.result_dir or workspace / "wp-0-01-results"
    return _run_parent(workspace, args.samples, result_dir)


if __name__ == "__main__":
    raise SystemExit(main())
