"""Explicit, offline Sakura 0.9.x user-data importer.

The importer is deliberately isolated from normal startup.  It never imports
legacy Python code and treats the selected installation as read-only input.
"""

from .errors import LegacyImportError
from .importer import inspect_legacy_installation, run_legacy_import

__all__ = ["LegacyImportError", "inspect_legacy_installation", "run_legacy_import"]
