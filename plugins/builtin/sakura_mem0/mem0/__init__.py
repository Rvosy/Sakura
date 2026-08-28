import importlib.metadata

try:
    __version__ = importlib.metadata.version("mem0ai")
except importlib.metadata.PackageNotFoundError:
    __version__ = "2.0.4-vendored"

from mem0.memory.main import AsyncMemory, Memory  # noqa
