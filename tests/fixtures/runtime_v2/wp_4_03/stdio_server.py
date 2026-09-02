from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--release-file", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    arguments.pid_file.write_text(str(os.getpid()), encoding="ascii")
    while arguments.release_file is not None and not arguments.release_file.exists():
        time.sleep(0.01)

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("WP-4-03 fixture", log_level="ERROR")

    @server.tool(description="Return a bounded fixture value.")
    def fixture_echo(value: str, delay_seconds: float = 0) -> str:
        if delay_seconds > 0:
            time.sleep(min(delay_seconds, 30))
        return f"fixture:{value}"

    server.run("stdio")


if __name__ == "__main__":
    main()
