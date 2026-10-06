"""SakuraTTS operation results and optional NVIDIA device context."""
from contextlib import contextmanager
import csv
import io
import shutil
import subprocess
import time

from sakura_provider_errors import provider_exception_diagnostics

try:
    from ._bundle import Cancelled
except ImportError:
    from _bundle import Cancelled


def nvidia_device():
    executable = shutil.which('nvidia-smi')
    if not executable:
        return {}
    try:
        result = subprocess.run(
            [executable, '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        name, memory, driver = next(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))
        return {'gpu_name': name, 'gpu_memory_mib': int(float(memory)), 'gpu_driver': driver}
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        # Optional inventory must not turn a usable CPU runtime into a failure.
        return {}


@contextmanager
def operation(log, stage, *, cancel=None, report_error=True):
    started = time.monotonic()
    fields = {'event': 'tts.operation.finished', 'stage': stage}
    level = 'info'
    try:
        yield
        fields['outcome'] = 'cancelled' if cancel is not None and cancel.is_set() else 'success'
    except Cancelled:
        fields['outcome'] = 'cancelled'
        raise
    except Exception as error:
        if cancel is not None and cancel.is_set():
            fields['outcome'] = 'cancelled'
            raise Cancelled() from error
        fields.update(outcome='failed', error_type=type(error).__name__)
        if report_error:
            level = 'error'
            fields.update(provider_exception_diagnostics(error))
        raise
    finally:
        log(level, 'SakuraTTS 操作结束', **fields,
            elapsed_ms=round((time.monotonic() - started) * 1000))
