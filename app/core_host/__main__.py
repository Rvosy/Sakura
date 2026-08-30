"""Command-line entry for the minimal Runtime v2 Core Host."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from app.storage.runtime_roots import RuntimeRoots

from .protocol import ProtocolError
from .runtime_logging import RuntimeLoggingBridge, install_runtime_logging
from .server import HostConfig, TransportFailure, WriterError, run_host


class StdoutPollutionError(RuntimeError):
    pass


class GuardedStdout(io.TextIOBase):
    """Reject accidental text writes after the protocol stream is captured."""

    def write(self, _text: str) -> int:
        raise StdoutPollutionError("stdout is reserved for framed protocol bytes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--distribution-root", required=True, type=_resolved_path)
    parser.add_argument("--user-root", required=True, type=_resolved_path)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--generation-number", type=int, default=1)
    return parser.parse_args(argv)


def _resolved_path(value: str) -> Path:
    return Path(value).resolve(strict=False)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    sys.stdout = GuardedStdout()
    runtime_logging: RuntimeLoggingBridge | None = None
    try:
        try:
            runtime_logging = install_runtime_logging()
            runtime_logging.emit_fixed(
                severity="info",
                channel="core.process",
                event="core.process.started",
            )
        except Exception:
            # Observability failure cannot prevent protocol startup.
            runtime_logging = None
        credential_bytes = input_stream.read(16)
        if credential_bytes is None or len(credential_bytes) != 16:
            raise TransportFailure(
                "GENERATION_CREDENTIAL_MISMATCH",
                "generation credential bootstrap was missing or incomplete",
            )
        run_host(
            input_stream,
            output_stream,
            HostConfig(
                RuntimeRoots(args.distribution_root, args.user_root),
                args.generation_id,
                credential_bytes.hex(),
                generation_number=args.generation_number,
            ),
        )
    except ProtocolError as error:
        if runtime_logging is not None:
            runtime_logging.emit_unhandled("CORE_HOST_PROTOCOL_ERROR", error)
        sys.stderr.write(f"CORE_HOST_PROTOCOL_ERROR {error.code}\n")
        sys.stderr.flush()
        return 64
    except (TransportFailure, WriterError, StdoutPollutionError) as error:
        if runtime_logging is not None:
            runtime_logging.emit_unhandled("CORE_HOST_TRANSPORT_ERROR", error)
        sys.stderr.write(f"CORE_HOST_TRANSPORT_ERROR {type(error).__name__}\n")
        sys.stderr.flush()
        return 74
    except BaseException as error:  # noqa: BLE001 - process boundary must fail closed
        if runtime_logging is not None:
            runtime_logging.emit_unhandled("CORE_HOST_FATAL", error)
        sys.stderr.write(f"CORE_HOST_FATAL {type(error).__name__}\n")
        sys.stderr.flush()
        return 70
    finally:
        if runtime_logging is not None:
            runtime_logging.emit_fixed(
                severity="info",
                channel="core.process",
                event="core.process.stopping",
            )
            runtime_logging.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
