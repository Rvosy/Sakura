"""Runtime v2 distribution and user ownership roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeRoots:
    """The only two filesystem roots accepted by the Runtime v2 Core."""

    distribution_root: Path
    user_root: Path

    def __post_init__(self) -> None:
        distribution_root = Path(self.distribution_root).resolve(strict=False)
        user_root = Path(self.user_root).resolve(strict=False)
        if not distribution_root.is_absolute() or not user_root.is_absolute():
            raise ValueError("Runtime roots must be absolute")
        object.__setattr__(self, "distribution_root", distribution_root)
        object.__setattr__(self, "user_root", user_root)


class DistributionPaths:
    """Read-only paths owned by the installer/updater."""

    def __init__(self, distribution_root: Path) -> None:
        self.root = Path(distribution_root)

    @property
    def core_dir(self) -> Path:
        return self.root / "core"

    @property
    def python_dir(self) -> Path:
        return self.root / "python"

    @property
    def python_tools_dir(self) -> Path:
        return self.python_dir / "tools"

    @property
    def builtin_plugins_dir(self) -> Path:
        return self.root / "plugins" / "builtin"

    @property
    def runtime_manifest(self) -> Path:
        return self.root / "runtime-manifest.json"

    @property
    def version_file(self) -> Path:
        return self.root / "VERSION"


def coerce_runtime_roots(value: RuntimeRoots | Path) -> RuntimeRoots:
    """Normalize internal call sites; production entrypoints always pass RuntimeRoots."""

    if isinstance(value, RuntimeRoots):
        return value
    root = Path(value).resolve(strict=False)
    return RuntimeRoots(root, root)


__all__ = ["DistributionPaths", "RuntimeRoots", "coerce_runtime_roots"]
