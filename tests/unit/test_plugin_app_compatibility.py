from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core_host.plugin_application import PluginApplicationHost
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.installer import LocalPluginInstaller
from app.storage.runtime_roots import RuntimeRoots


def _roots(tmp_path: Path, version: str | None = "1.2.3") -> RuntimeRoots:
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    distribution.mkdir()
    user.mkdir()
    if version is not None:
        (distribution / "VERSION").write_text(version, encoding="utf-8")
    return RuntimeRoots(distribution, user)


def _package(path: Path, declaration: str = "", *, api: int = 4) -> Path:
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(
        f"api: {api}\nid: example.minimum\nversion: 1.0.0\nentry: plugin:Plugin\n{declaration}",
        encoding="utf-8",
    )
    (path / "plugin.py").write_text(
        'from pathlib import Path\nPath(__file__).with_name("imported").write_text("yes")\n'
        'class Plugin:\n    def setup(self, context): pass\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(("current", "minimum"), [(None, "99.0.0"), ("invalid", "broken"), ("1.0.0", "99.0.0")])
def test_legacy_version_declarations_do_not_block_install_or_runtime(tmp_path, current, minimum):
    roots = _roots(tmp_path, current)
    package = _package(tmp_path / "package", yaml.safe_dump({"min_app_version": minimum}))
    installed = LocalPluginInstaller(roots).install(package, "folder")
    host = PluginApplicationHost(roots, "no-version-gate", ToolRegistry())
    try:
        record = host.inventory().records[0]
        assert record.runtime_eligible
        assert record.reason_code == "READY"
        assert (installed.code_dir / "imported").read_text() == "yes"
        assert host.set_enabled(record.install_id, True)["applicationState"] == "applied"
    finally:
        host.close()
