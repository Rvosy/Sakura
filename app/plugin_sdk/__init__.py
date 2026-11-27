"""Expose the same public SDK modules to host code and isolated plugins."""
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.util import spec_from_loader
from pathlib import Path
import sys


_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


class _PublicModuleAlias(MetaPathFinder, Loader):
    """Resolve qualified imports lazily, including SDKs with optional dependencies."""

    def find_spec(self, fullname, path, target=None):
        parent, _, name = fullname.rpartition(".")
        if parent == __name__ and (_root / (name + ".py")).is_file():
            return spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        sys.modules[module.__name__] = import_module(module.__name__.rsplit(".", 1)[1])


sys.meta_path.insert(0, _PublicModuleAlias())
