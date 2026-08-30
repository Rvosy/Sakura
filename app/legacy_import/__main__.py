from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from io import TextIOBase
from pathlib import Path
from typing import Iterator

from .errors import LegacyImportError
from .importer import inspect_legacy_installation, run_legacy_import
from .transaction import (
    PendingCommit,
    finalize_commit,
    recover_pending_commits,
    rollback_commit,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sakura-legacy-import")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--target", required=True)
    run = sub.add_parser("run")
    run.add_argument("--source", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--import-id", required=True)
    for name in ("cancel", "finalize", "rollback"):
        action = sub.add_parser(name)
        action.add_argument("--target", required=True)
        action.add_argument("--import-id", required=True)
    recover = sub.add_parser("recover")
    recover.add_argument("--target", required=True)
    return parser


def _emit(value: object) -> None:
    # This stdout stream is a machine protocol consumed by the Rust shell.
    # Windows pipes inherit the active ANSI code page, so emitting literal
    # Chinese text can make an otherwise valid JSON line non-UTF-8 and cause
    # the shell to discard every progress/result message. ASCII-escaped JSON
    # is encoding-independent and serde_json restores the original text.
    stream = getattr(sys, "_sakura_legacy_protocol_stdout", sys.stdout)
    print(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")),
        file=stream,
        flush=True,
    )


@contextmanager
def _exclusive_protocol_stdout() -> Iterator[None]:
    """Reserve OS stdout for JSON even when native libraries write to fd 1."""

    original_stdout = sys.stdout
    previous_protocol_stdout = getattr(sys, "_sakura_legacy_protocol_stdout", None)
    protocol_stream: TextIOBase | None = None
    saved_stdout_fd: int | None = None
    try:
        original_stdout.flush()
        sys.stderr.flush()
        # The Rust parent owns the process-level stdout pipe at descriptor 1.
        # Test capture and embedded hosts may wrap sys.stdout with another fd,
        # so reserve fd 1 explicitly rather than trusting the wrapper.
        stdout_fd = 1
        stderr_fd = sys.stderr.fileno()
        protocol_fd = os.dup(stdout_fd)
        saved_stdout_fd = os.dup(stdout_fd)
        protocol_stream = os.fdopen(
            protocol_fd,
            "w",
            encoding="ascii",
            errors="strict",
            newline="\n",
            buffering=1,
        )
        os.dup2(stderr_fd, stdout_fd)
        sys.stdout = sys.stderr
    except (AttributeError, OSError, ValueError):
        if protocol_stream is not None:
            protocol_stream.close()
        if saved_stdout_fd is not None:
            os.close(saved_stdout_fd)
        protocol_stream = original_stdout
        saved_stdout_fd = None
        sys.stdout = sys.stderr
    sys._sakura_legacy_protocol_stdout = protocol_stream  # type: ignore[attr-defined]
    try:
        yield
    finally:
        protocol_stream.flush()
        if protocol_stream is not original_stdout:
            protocol_stream.close()
        if saved_stdout_fd is not None:
            os.dup2(saved_stdout_fd, 1)
            os.close(saved_stdout_fd)
        sys.stdout = original_stdout
        if previous_protocol_stdout is None:
            del sys._sakura_legacy_protocol_stdout  # type: ignore[attr-defined]
        else:
            sys._sakura_legacy_protocol_stdout = previous_protocol_stdout  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with _exclusive_protocol_stdout():
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "inspect":
            inspection = inspect_legacy_installation(Path(args.source), Path(args.target))
            _emit({"type": "inspection", "inspection": inspection.to_public_dict()})
            return 0
        if args.command == "run":
            inspection = inspect_legacy_installation(Path(args.source), Path(args.target))
            _emit({"type": "inspection", "inspection": inspection.to_public_dict()})
            if not inspection.compatible:
                first = inspection.blockers[0]
                raise LegacyImportError(str(first["code"]), "inspect")

            def progress(stage: str, percent: int, message: str) -> None:
                _emit(
                    {
                        "type": "progress",
                        "stage": stage,
                        "percent": percent,
                        "message": message,
                        "cancellable": stage in {"staging", "validating"},
                    }
                )

            def diagnostic(
                event: str,
                message: str,
                attributes: dict[str, object],
                severity: str,
            ) -> None:
                _emit(
                    {
                        "type": "diagnostic",
                        "event": event,
                        "message": message,
                        "attributes": attributes,
                        "severity": severity,
                    }
                )

            report, pending = run_legacy_import(
                Path(args.source),
                Path(args.target),
                import_id=args.import_id,
                progress=progress,
                diagnostic=diagnostic,
                inspection=inspection,
            )
            _emit(
                {
                    "type": "result",
                    "state": "core_validating" if pending else "completed",
                    "report": report.to_public_dict(),
                }
            )
            return 0
        target = Path(args.target).resolve(strict=True)
        if args.command != "recover" and not re.fullmatch(r"[A-Za-z0-9-]{8,64}", args.import_id):
            raise LegacyImportError("LEGACY_IMPORT_ID_INVALID", "inspect")
        if args.command == "cancel":
            (target / f".legacy-import-cancel-{args.import_id}").touch(exist_ok=True)
            _emit({"type": "cancel", "accepted": True})
            return 0
        if args.command == "recover":
            recovered = recover_pending_commits(target)
            _emit({"type": "recovery", "recovered": len(recovered)})
            return 0
        pending = PendingCommit(args.import_id, target)
        if args.command == "finalize":
            finalize_commit(pending)
        elif args.command == "rollback":
            rollback_commit(pending)
        _emit({"type": args.command, "completed": True})
        return 0
    except LegacyImportError as exc:
        error = exc.to_public_dict()
        if getattr(args, "command", "") == "run":
            error["diagnosticLog"] = "data/logs/sakura-runtime.log"
        _emit({"type": "error", "error": error})
        return 2
    except Exception:
        error = {"code": "LEGACY_IMPORT_INTERNAL", "stage": "internal"}
        if getattr(args, "command", "") == "run":
            error["diagnosticLog"] = "data/logs/sakura-runtime.log"
        _emit(
            {
                "type": "error",
                "error": error,
            }
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
