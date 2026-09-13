"""Fixed SSH import target: validated release input becomes a private draft only."""
import json
from pathlib import Path
import sqlite3
import sys

# Python -I ignores caller paths. This directory is deployed root-owned.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import control
import releases


def main():
    control.DATABASE = Path("/var/lib/sakura-console/console.db")
    item = control.import_ci_payload(sys.stdin.buffer.read(releases.MAX_PAYLOAD_BYTES + 1))
    print(json.dumps({"id": item["id"], "version": item["version"], "state": item["state"],
        "consoleUrl": "https://adm.sakura.cialloo.cn/admin/#releases"}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Draft import failed: {error}", file=sys.stderr)
        raise SystemExit(1)
