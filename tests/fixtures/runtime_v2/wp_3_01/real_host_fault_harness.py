"""Debug-only real Assistant/Core Host fault harness for native lifecycle acceptance."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO


def _append_pid(directory: Path) -> None:
    with (directory / "descendant-pids.txt").open("a", encoding="ascii") as marker:
        marker.write(f"{os.getpid()}\n")
        marker.flush()


def _run_descendant(script: Path, directory: Path, depth: int) -> int:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _append_pid(directory)
    if depth > 0:
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                "utf8",
                str(script),
                "--descendant",
                "--fault-directory",
                str(directory),
                "--depth",
                str(depth - 1),
            ],
            stdin=subprocess.DEVNULL,
        )
    while True:
        time.sleep(60)


class FaultingAssistantAdapter:
    def __init__(self, app_root: Path, mode: str, directory: Path, script: Path) -> None:
        from app.core_host.assistant_adapter import AssistantAdapter

        self._owned = AssistantAdapter(app_root)
        self._mode = mode
        self._directory = directory
        self._script = script

    def initialize(self, cancel: threading.Event) -> object:
        result = self._owned.initialize(cancel)
        descendant_depth = {
            "crash-one-descendant": 0,
            "forced-recovery-multi-descendant": 1,
        }.get(self._mode)
        if descendant_depth is not None:
            subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    str(self._script),
                    "--descendant",
                    "--fault-directory",
                    str(self._directory),
                    "--depth",
                    str(descendant_depth),
                ],
                stdin=subprocess.DEVNULL,
            )
        if self._mode == "crash-one-descendant":
            threading.Thread(target=self._crash_when_requested, daemon=True).start()
        return result

    def close(self) -> None:
        self._owned.close()
        if self._mode == "close-throw":
            raise RuntimeError("FAULT_HARNESS_CLOSE_THROW")
        if self._mode == "close-block":
            threading.Event().wait()

    def _crash_when_requested(self) -> None:
        trigger = self._directory / "trigger-crash"
        while not trigger.exists():
            time.sleep(0.01)
        os._exit(37)


def _run_host(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    config: object,
    factory: object,
) -> None:
    from app.core_host.protocol import read_frame
    from app.core_host.server import ControlDispatcher, ResponseWriter

    writer = ResponseWriter(output_stream)
    dispatcher = ControlDispatcher(config, initializer_factory=factory)  # type: ignore[arg-type]
    primary_error: BaseException | None = None
    primary_traceback = None
    try:
        while True:
            request = read_frame(input_stream)
            if request is None:
                break
            message, should_stop = dispatcher.dispatch(request)
            writer.send(message)
            if should_stop:
                break
    except BaseException as error:  # noqa: BLE001 - process boundary owns aggregation
        primary_error = error
        primary_traceback = error.__traceback__
    for owner in (dispatcher, writer):
        try:
            owner.close()
        except BaseException as error:  # noqa: BLE001 - deterministic cleanup aggregation
            if primary_error is None:
                primary_error = error
                primary_traceback = error.__traceback__
            else:
                primary_error.add_note(f"Additional cleanup failure: {type(error).__name__}")
    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--descendant", action="store_true")
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--distribution-root", type=Path)
    parser.add_argument("--user-root", type=Path)
    parser.add_argument("--generation-id")
    parser.add_argument("--fault-mode")
    parser.add_argument("--fault-directory", required=True, type=Path)
    parser.add_argument("--python-path-entry", type=Path)
    args = parser.parse_args(argv)
    script = Path(__file__).resolve()
    if args.descendant:
        return _run_descendant(script, args.fault_directory, args.depth)

    assert args.repo_root is not None
    assert args.distribution_root is not None
    assert args.user_root is not None
    assert args.generation_id is not None
    assert args.fault_mode is not None
    sys.path.insert(0, str(args.repo_root.resolve(strict=True)))
    assert args.python_path_entry is not None
    sys.path.insert(1, str(args.python_path_entry.resolve(strict=True)))
    from app.core_host.server import HostConfig
    from app.storage.runtime_roots import RuntimeRoots

    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    credential = input_stream.read(16)
    if credential is None or len(credential) != 16:
        return 74
    config = HostConfig(
        RuntimeRoots(
            args.distribution_root.resolve(strict=True),
            args.user_root.resolve(strict=True),
        ),
        args.generation_id,
        credential.hex(),
    )
    factory = lambda roots: FaultingAssistantAdapter(  # noqa: E731 - injected seam
        roots.user_root, args.fault_mode, args.fault_directory, script
    )
    try:
        _run_host(input_stream, output_stream, config, factory)
    except BaseException as error:  # noqa: BLE001 - stable process boundary
        sys.stderr.write(f"CORE_HOST_FAULT_HARNESS_FATAL {type(error).__name__}\n")
        sys.stderr.flush()
        return 70 if not hasattr(error, "code") else 74
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
