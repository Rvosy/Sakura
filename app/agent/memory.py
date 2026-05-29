from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryStore:
    """长期记忆接口占位；第一阶段仅驻留内存，不读写 data/memory.json。"""

    values: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return dict(self.values)
