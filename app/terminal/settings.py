from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TerminalSettings:
    """可见终端能力配置；默认关闭。"""

    enabled: bool = False
    default_cwd: str = ""

    def normalized(self, *, home: Path | None = None) -> "TerminalSettings":
        fallback = (home or Path.home()).expanduser().resolve()
        raw_cwd = self.default_cwd.strip()
        if not raw_cwd:
            cwd = fallback
        else:
            candidate = Path(raw_cwd).expanduser()
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = fallback
            cwd = resolved if resolved.is_absolute() and resolved.is_dir() else fallback
        return TerminalSettings(enabled=bool(self.enabled), default_cwd=str(cwd))
