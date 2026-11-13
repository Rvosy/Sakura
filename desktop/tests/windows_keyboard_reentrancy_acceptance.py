"""Exercise Tao's keyboard/focus reentrancy against an isolated Sakura process.

Regression: https://github.com/tauri-apps/tao/issues/1379
Run with the bundled Python after `cargo build --locked`.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This acceptance test requires Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    executable = args.executable
    if executable is None:
        metadata = subprocess.check_output(
            ["cargo", "metadata", "--format-version", "1", "--no-deps", "--offline",
             "--manifest-path", "desktop/src-tauri/Cargo.toml"],
            cwd=repo, text=True, encoding="utf-8",
        )
        executable = Path(json.loads(metadata)["target_directory"]) / "debug/sakura.exe"

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM

    evidence = Path(tempfile.mkdtemp(prefix="sakura-keyboard-reentrancy-"))
    print(f"Evidence: {evidence}", flush=True)
    stop = threading.Event()
    with (evidence / "stdout.log").open("wb") as stdout, (evidence / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            [str(executable.resolve())], cwd=repo,
            env={**os.environ, "SAKURA_RUNTIME_USER_ROOT": str(evidence / "user")},
            stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            windows: list[int] = []

            @callback_type
            def find_window(hwnd: int, _parameter: int) -> bool:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == process.pid and user32.IsWindowVisible(hwnd):
                    class_name = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_name, len(class_name))
                    # Tao's thread event target is also enumerated as visible,
                    # but it does not run the window keyboard handler.
                    if class_name.value == "Tauri Window":
                        windows.append(hwnd)
                return True

            deadline = time.monotonic() + 20
            while not windows:
                if process.poll() is not None:
                    raise RuntimeError(f"Sakura exited during startup: {process.returncode}; see {evidence}")
                user32.EnumWindows(find_window, 0)
                if not windows:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"No visible Sakura window; see {evidence}")
                    time.sleep(0.05)
            hwnd = windows[0]

            def send(message: int) -> None:
                result = ctypes.c_size_t()
                # A sent focus message must be dispatched inside PeekMessageW,
                # unlike the posted keyboard messages. Bound each send so a
                # regression reports the hung window instead of hanging the test.
                if not user32.SendMessageTimeoutW(hwnd, message, 0, 0, 0x3, 2000, ctypes.byref(result)):
                    raise TimeoutError(f"Window stopped answering message 0x{message:04x}; see {evidence}")

            def post_keys() -> int:
                count = 0
                for _ in range(2000):
                    if stop.is_set():
                        break
                    for message, key_data in ((0x100, 0x001E0001), (0x101, 0xC01E0001)):
                        if not user32.PostMessageW(hwnd, message, 0x41, key_data):
                            raise ctypes.WinError(ctypes.get_last_error())
                        count += 1
                    time.sleep(0.0002)
                return count

            def send_focus() -> int:
                count = 0
                while not stop.is_set():
                    send(0x8)  # WM_KILLFOCUS
                    send(0x7)  # WM_SETFOCUS
                    count += 2
                return count

            send(0)  # WM_NULL: establish that startup completed responsively.
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=3) as workers:
                keys = workers.submit(post_keys)
                focus = [workers.submit(send_focus) for _ in range(2)]
                try:
                    while not keys.done():
                        for task in focus:
                            if task.done():
                                task.result()
                        send(0)
                        time.sleep(0.05)
                    key_count = keys.result()
                finally:
                    stop.set()
                focus_count = sum(task.result() for task in focus)
            send(0)
            result = {
                "outcome": "passed", "keyboard_messages": key_count,
                "focus_messages": focus_count, "elapsed_seconds": round(time.monotonic() - started, 2),
            }
            (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result), flush=True)
        finally:
            stop.set()
            if process.poll() is None:
                # This process owns only the temporary user root. Killing the
                # Shell also closes its managed Core job on a failed probe.
                process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
