from __future__ import annotations

from dataclasses import dataclass

PC_MIN_DEF=10
PC_MAX_DEF=30
PC_IDLE_DEF=30
PC_MIN_LO=1
PC_MAX_HI=720
PC_IDLE_LO=0
PC_IDLE_HI=3600


@dataclass(frozen=True)
class PcSet:
    enabled: bool = True
    min_interval_minutes: int = PC_MIN_DEF
    max_interval_minutes: int = PC_MAX_DEF
    min_idle_seconds: int = PC_IDLE_DEF

    def norm(self) -> "PcSet":
        lo=_cl(self.min_interval_minutes, PC_MIN_LO, PC_MAX_HI)
        hi=_cl(self.max_interval_minutes, PC_MIN_LO, PC_MAX_HI)
        if lo > hi:
            lo, hi = hi, lo
        return PcSet(
            enabled=bool(self.enabled),
            min_interval_minutes=lo,
            max_interval_minutes=hi,
            min_idle_seconds=_cl(self.min_idle_seconds, PC_IDLE_LO, PC_IDLE_HI),
        )


def _cl(v: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, n))
