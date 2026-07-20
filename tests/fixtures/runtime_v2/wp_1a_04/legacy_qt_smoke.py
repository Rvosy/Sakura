from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--hold-ms", type=int, default=1500)
    parser.add_argument("--drain-ready-file", type=Path)
    parser.add_argument("--drain-hold-ms", type=int, default=0)
    parser.add_argument("--drain-fail", action="store_true")
    args = parser.parse_args()

    if (args.drain_hold_ms or args.drain_fail) and args.drain_ready_file is None:
        parser.error("--drain-ready-file is required with drain fault injection")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from app.core import runtime_log
    from app.storage.paths import StoragePaths

    isolated_base_dir = args.base_dir.resolve()
    runtime_log._FILE_LOG_PATH = StoragePaths(isolated_base_dir).runtime_log_file()
    runtime_log._load_debug_values = lambda: {}

    import legacy_qt_main

    legacy_qt_main.BASE_DIR = isolated_base_dir
    original_show = legacy_qt_main.PetWindow.show

    def show_and_schedule_exit(window) -> None:  # type: ignore[no-untyped-def]
        original_show(window)
        if args.drain_hold_ms or args.drain_fail:
            original_wait = window.resource_manager.wait_for_lingering_qthreads

            def wait_with_barrier(timeout_ms: int) -> bool:
                assert args.drain_ready_file is not None
                args.drain_ready_file.write_text("draining\n", encoding="utf-8")
                time.sleep(args.drain_hold_ms / 1000)
                if args.drain_fail:
                    return False
                return original_wait(timeout_ms)

            window.resource_manager.wait_for_lingering_qthreads = wait_with_barrier
        args.ready_file.write_text("ready\n", encoding="utf-8")
        legacy_qt_main.QTimer.singleShot(
            args.hold_ms,
            legacy_qt_main.QApplication.instance().quit,
        )

    legacy_qt_main.PetWindow.show = show_and_schedule_exit
    legacy_qt_main._start_tts_migration_or_deferred = lambda _base, _window: None
    legacy_qt_main._ensure_launch_at_login_state = lambda _base, _settings: None
    return legacy_qt_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
