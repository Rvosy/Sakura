"""Non-blocking cross-process lock shared by the service and its CLI (Linux)."""

from contextlib import contextmanager
import fcntl
import os
import queries


@contextmanager
def exclusive_export():
    path = queries.DB_PATH.with_suffix(".export.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("EXPORT_BUSY") from error
        yield
    finally:
        os.close(fd)
