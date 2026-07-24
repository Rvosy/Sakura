"""Command-line entry for the minimal Runtime v2 Core Host."""

from __future__ import annotations

import argparse
import io
import sys

from .protocol import ProtocolError
from .server import HostConfig, TransportFailure, WriterError, run_host


class StdoutPollutionError(RuntimeError):
    pass


class GuardedStdout(io.TextIOBase):
    """Reject accidental text writes after the protocol stream is captured."""

    def write(self, _text: str) -> int:
        raise StdoutPollutionError("stdout is reserved for framed protocol bytes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--generation-number", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    sys.stdout = GuardedStdout()
    try:
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
                args.generation_id,
                credential_bytes.hex(),
                generation_number=args.generation_number,
            ),
        )
    except ProtocolError as error:
        sys.stderr.write(f"CORE_HOST_PROTOCOL_ERROR {error.code}\n")
        sys.stderr.flush()
        return 64
    except (TransportFailure, WriterError, StdoutPollutionError) as error:
        sys.stderr.write(f"CORE_HOST_TRANSPORT_ERROR {type(error).__name__}\n")
        sys.stderr.flush()
        return 74
    except BaseException as error:  # noqa: BLE001 - process boundary must fail closed
        sys.stderr.write(f"CORE_HOST_FATAL {type(error).__name__}\n")
        sys.stderr.flush()
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
