"""Process-lifetime bounded counters; no rejected payloads or exception messages."""

from threading import Lock

_lock = Lock()
_counts = {"rejected": 0, "storageFailed": 0}


def increment(kind):
    with _lock:
        _counts[kind] = min(2**53 - 1, _counts[kind] + 1)


def snapshot():
    with _lock:
        return dict(_counts)
