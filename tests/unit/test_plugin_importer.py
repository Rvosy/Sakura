from __future__ import annotations

import sys
from pathlib import Path

from app.plugins.importer import _load_module_from_file


def test_file_loader_keeps_punctuation_distinct_and_reloads_relative_imports(tmp_path: Path) -> None:
    try:
        loaded = []
        for plugin_id, value in [("example.one", 1), ("example-one", 2), ("example_one", 3)]:
            root = tmp_path / plugin_id
            root.mkdir()
            (root / "helper.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
            module = root / "plugin.py"
            module.write_text("from .helper import VALUE\n", encoding="utf-8")
            loaded.append(_load_module_from_file(plugin_id, root, "plugin", module))
        assert [module.VALUE for module in loaded] == [1, 2, 3]
        assert len({module.__package__ for module in loaded}) == 3
        root = tmp_path / "example.one"
        (root / "helper.py").write_text("VALUE = 1000\n", encoding="utf-8")
        refreshed = _load_module_from_file("example.one", root, "plugin", root / "plugin.py")
        assert refreshed.VALUE == 1000
        assert loaded[1].VALUE == 2
    finally:
        for name in tuple(sys.modules):
            if name.startswith("sakura_user_plugins.p_6578616d706c65"):
                del sys.modules[name]
