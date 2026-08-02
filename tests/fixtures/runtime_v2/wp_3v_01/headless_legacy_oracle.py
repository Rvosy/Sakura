from __future__ import annotations

import importlib.abc
import json
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


SANITIZED_MARKER = ".sakura-wp-3-06-sanitized"
EXPECTED_HISTORY_MARKERS = {
    "[WP-3-06-LEGACY-USER]",
    "[WP-3-06-LEGACY-REPLY]",
    "[WP-3-06-TAURI-USER]",
    "[WP-3-06-TAURI-REPLY]",
    "[WP-3V-01-COMPLETE]",
    "[WP-3V-01-REPLY-1]",
    "[WP-3V-01-CANCEL]",
    "[WP-3V-01-RECOVERY]",
    "[WP-3V-01-REPLY-3]",
}


class _RejectQtImports(importlib.abc.MetaPathFinder):
    """Keep this reference oracle independent of the retired UI runtime."""

    def find_spec(self, fullname: str, _path=None, _target=None):  # type: ignore[no-untyped-def]
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise ModuleNotFoundError("WP-3V-01 headless oracle forbids PySide6")
        return None


@contextmanager
def reject_qt_imports() -> Iterator[None]:
    blocker = _RejectQtImports()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)


def validate_acceptance_root(raw_directory: str | Path) -> tuple[Path, Path]:
    directory = Path(raw_directory)
    if not directory.is_absolute():
        raise RuntimeError("WP-3V-01 oracle directory must be absolute")
    directory = directory.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        relative = directory.relative_to(temp_root)
    except ValueError as exc:
        raise RuntimeError("WP-3V-01 oracle directory is outside system temp") from exc
    if len(relative.parts) != 1 or not relative.name.startswith("sakura-wp-3-06-"):
        raise RuntimeError("WP-3V-01 oracle directory name is invalid")

    marker = directory / SANITIZED_MARKER
    app_root = directory / "app-root"
    if (
        not marker.is_file()
        or marker.is_symlink()
        or not app_root.is_dir()
        or app_root.is_symlink()
    ):
        raise RuntimeError("WP-3V-01 sanitized oracle fixture is invalid")
    app_root = app_root.resolve(strict=True)
    if app_root.parent != directory:
        raise RuntimeError("WP-3V-01 oracle app root escaped its acceptance directory")
    return directory, app_root


def read_compatible_history(app_root: Path) -> int:
    with reject_qt_imports():
        from app.storage.chat_history import ChatHistoryStore

        history = ChatHistoryStore(
            app_root / "data/chat_history/fixture.jsonl",
            "fixture",
        )
        history.assert_compatible_append()
        entries = history.load()
        contents = {entry.content for entry in entries}
        missing = EXPECTED_HISTORY_MARKERS - contents
        if missing:
            raise RuntimeError(f"WP-3V-01 oracle history markers missing: {sorted(missing)}")
        history.assert_compatible_append()
        return len(entries)


def run(raw_directory: str | Path) -> dict[str, object]:
    with reject_qt_imports():
        from app.core.instance import InstanceAcquireStatus, SingleInstanceGuard

        directory, app_root = validate_acceptance_root(raw_directory)
        guard = SingleInstanceGuard()
        status = guard.acquire()
        if status is not InstanceAcquireStatus.ACQUIRED:
            raise RuntimeError(
                f"WP-3V-01 oracle failed to reacquire shared lock: {status.value}"
            )
        try:
            entry_count = read_compatible_history(app_root)
            (directory / "oracle.read_complete").write_text("complete", encoding="utf-8")
            return {
                "status": "passed",
                "headless": True,
                "lock_reacquired": True,
                "history_entries": entry_count,
            }
        finally:
            guard.release()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: headless_legacy_oracle.py <acceptance-directory>")
    print(json.dumps(run(sys.argv[1]), ensure_ascii=False, sort_keys=True))
