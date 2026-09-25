"""Windows paths consumed by plugin processes and third-party libraries."""

from __future__ import annotations

import os
from pathlib import Path


def process_path(path: str | Path) -> str:
    """Use ordinary drive/UNC paths outside the Win32 filesystem boundary.

    Verbatim working directories stall native-library/root-relative probes;
    verbatim import roots also break libraries that append ``..`` to __file__.
    """
    value = str(path)
    if os.name == "nt" and value.startswith("\\\\?\\"):
        if value[4:8].upper() == "UNC\\":
            return "\\\\" + value[8:]
        if len(value) >= 7 and value[4].isalpha() and value[5:7] == ":\\":
            return value[4:]
    return value
