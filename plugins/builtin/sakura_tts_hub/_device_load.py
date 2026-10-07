"""Whether the machine is below peak use for background speech synthesis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


PEAK_LOAD_PER_CPU = 0.7
PEAK_GPU_PERCENT = 70


def device_below_peak(
    *,
    load_reader: Callable[[], float | None] | None = None,
    gpu_reader: Callable[[], int | None] | None = None,
    cpu_count: int | None = None,
) -> bool:
    """True when CPU load and any visible GPU busy percent are under the peak line.

    Windows has no load average. A missing reading is not treated as peak; the
    caller still has to be idle of user speech work.
    """

    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if cpus < 1:
        cpus = 1
    reader = load_reader or _load_average
    try:
        load1 = reader()
    except (AttributeError, OSError):
        load1 = None
    if load1 is not None and load1 >= cpus * PEAK_LOAD_PER_CPU:
        return False
    try:
        gpu = (gpu_reader or _gpu_busy_percent)()
    except OSError:
        gpu = None
    if gpu is not None and gpu >= PEAK_GPU_PERCENT:
        return False
    return True


def _load_average() -> float | None:
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return None
    return float(getloadavg()[0])


def _gpu_busy_percent() -> int | None:
    root = Path("/sys/class/drm")
    if not root.is_dir():
        return None
    values: list[int] = []
    for path in root.glob("card*/device/gpu_busy_percent"):
        try:
            values.append(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
    if not values:
        return None
    return max(values)
