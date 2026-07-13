from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TerminalState(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TerminalSpawnResult:
    session_id: str
    state: TerminalState
    output: bytes = b""
    cursor: int = 0
    exit_code: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class TerminalReadResult:
    session_id: str
    state: TerminalState
    output: bytes = b""
    cursor: int = 0
    exit_code: int | None = None
    truncated: bool = False


class TerminalTransport(Protocol):
    """Python 终端策略层到可见 PTY 宿主的最小协议。"""

    def spawn(
        self,
        *,
        command: tuple[str, ...],
        cwd: str,
        columns: int,
        rows: int,
        yield_time_ms: int,
        timeout_ms: int,
    ) -> TerminalSpawnResult: ...

    def read(self, *, session_id: str, cursor: int, max_bytes: int) -> TerminalReadResult: ...

    def write(self, *, session_id: str, data: bytes) -> TerminalReadResult: ...

    def stop(self, *, session_id: str) -> TerminalReadResult: ...

    def show(self) -> None: ...

    def shutdown(self, timeout_ms: int) -> None: ...
