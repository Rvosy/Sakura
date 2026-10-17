"""精简发行包通过已安装插件导入旧数据，不改变插件的启停选择。"""

import sys

import pytest

from app.legacy_import import plugin_support
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.runtime_roots import RuntimeRoots


@pytest.fixture
def roots(tmp_path, monkeypatch):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    monkeypatch.setattr(plugin_support, "_roots", roots)
    yield roots
    for name in list(sys.modules):
        if name == "sakura_legacy_sakura_mem0" or name.startswith("sakura_legacy_sakura_mem0."):
            del sys.modules[name]


def test_import_uses_installed_helper_without_changing_disabled_plugin(roots):
    plugin = roots.user_root / "plugins/user/custom-memory-folder"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "api: 4\nid: sakura.memory.mem0\nname: Memory\nversion: 1.0.0\nentry: plugin:Plugin\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "memory.py").write_text("def normalize(value): return value.strip()\n", encoding="utf-8")
    desired = PluginDesiredStateStore(roots.user_root)
    desired.set("sakura.memory.mem0", False)
    assert plugin_support.migration_module("sakura_mem0.memory").normalize(" old memory ") == "old memory"
    assert desired.read()["sakura.memory.mem0"] is False


def test_import_explains_which_plugin_must_be_installed(roots):
    with pytest.raises(ValueError, match="sakura.memory.mem0.*插件市场"):
        plugin_support.migration_module("sakura_mem0.memory")
