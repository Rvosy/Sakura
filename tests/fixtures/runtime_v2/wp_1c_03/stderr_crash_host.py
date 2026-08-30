from __future__ import annotations

import os
import sys


sys.stdin.buffer.read(16)
os.write(2, b"crash diagnostic token=must-not-leak\n")
raise SystemExit(42)
