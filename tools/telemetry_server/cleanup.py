from __future__ import annotations

from db import connect


DELETE_STATEMENTS = (
    "DELETE FROM error_events WHERE received_at < datetime('now', '+8 hours', '-90 days')",
    "DELETE FROM telemetry_events WHERE received_at < datetime('now', '+8 hours', '-90 days')",
    "DELETE FROM model_call_metrics WHERE received_at < datetime('now', '+8 hours', '-90 days')",
)


def main() -> None:
    deleted = 0
    with connect() as connection:
        for statement in DELETE_STATEMENTS:
            cursor = connection.execute(statement)
            deleted += cursor.rowcount
    with connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    print(f"retention cleanup complete: deleted={deleted}")


if __name__ == "__main__":
    main()
