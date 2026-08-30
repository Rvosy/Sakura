from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LegacyImportError(RuntimeError):
    """A content-free failure suitable for the desktop boundary."""

    code: str
    stage: str
    relative_path: str = ""
    line: int | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.code)

    def to_public_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "stage": self.stage}
        if self.relative_path:
            result["relativePath"] = self.relative_path.replace("\\", "/")
        if self.line is not None:
            result["line"] = self.line
        return result
