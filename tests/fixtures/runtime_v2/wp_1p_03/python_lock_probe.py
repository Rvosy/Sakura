from __future__ import annotations

import sys

from app.core.instance import InstanceAcquireStatus, SingleInstanceGuard


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"try", "hold"}:
        return 2

    guard = SingleInstanceGuard()
    outcome = guard.acquire()
    if outcome is InstanceAcquireStatus.ALREADY_RUNNING:
        print("already_running", flush=True)
        return 3
    if outcome is InstanceAcquireStatus.FATAL:
        print(f"fatal:{guard.last_error}", flush=True)
        return 4

    print("acquired", flush=True)
    if sys.argv[1] == "hold":
        sys.stdin.readline()
    guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
