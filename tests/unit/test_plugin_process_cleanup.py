from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time

import psutil
import pytest

from app.plugin_sdk.sakura_process import terminate_process_tree


@pytest.mark.skipif(os.name == "nt", reason="POSIX child reaping")
def test_stopped_root_does_not_consume_entire_grace_period() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = time.monotonic()
        terminate_process_tree(process, timeout=5)
        assert process.poll() is not None
        assert time.monotonic() - started < 2
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal escalation")
@pytest.mark.parametrize("module_name", [
    "app.plugin_sdk.sakura_process",
    "plugins.builtin.sakura_gpt_sovits._support",
    "plugins.builtin.sakura_genie._support",
])
def test_owned_child_is_reclaimed_when_root_exits_before_it(module_name: str) -> None:
    child_code = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "print('ready',flush=True); time.sleep(60)"
    )
    root_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdout=subprocess.PIPE,text=True); "
        "child.stdout.readline(); print(child.pid,flush=True); time.sleep(60)"
    )
    root = subprocess.Popen(
        [sys.executable, "-c", root_code, child_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    child: psutil.Process | None = None
    try:
        assert root.stdout is not None
        child = psutil.Process(int(root.stdout.readline()))
        importlib.import_module(module_name).terminate_process_tree(root, timeout=0.2)
        deadline = time.monotonic() + 1
        while True:
            try:
                if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            if time.monotonic() >= deadline:
                pytest.fail("the owned child survived after its root exited")
            time.sleep(0.01)
        assert root.poll() is not None
    finally:
        if child is not None:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        if root.poll() is None:
            root.kill()
        root.wait(timeout=2)
        if root.stdout is not None:
            root.stdout.close()
