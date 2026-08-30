from __future__ import annotations

import struct
import sys
import time


sys.stdin.buffer.read(16)
header = sys.stdin.buffer.read(4)
if len(header) == 4:
    length = struct.unpack(">I", header)[0]
    sys.stdin.buffer.read(length)
time.sleep(30)
