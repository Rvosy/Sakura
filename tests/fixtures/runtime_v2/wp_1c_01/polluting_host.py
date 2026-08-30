from __future__ import annotations

import sys
import time


sys.stdout.buffer.write(b"stdout pollution")
sys.stdout.buffer.flush()
time.sleep(30)
