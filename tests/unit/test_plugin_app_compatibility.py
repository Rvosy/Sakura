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



@pytest.mark.parametrize(("current", "minimum"), [(None, None), ("1.3.6", None), ("1.3.6", "1.3.6"), ("1.3.6+build", "1.3.5"), ("1.3.6-beta.10", "1.3.6-beta.2")])
def test_compatible_plugin_installs_and_runs(tmp_path, current, minimum):
    roots = _roots(tmp_path, current)
    declaration = "" if minimum is None else yaml.safe_dump({"min_app_version": minimum})
    package = _package(tmp_path / "package", declaration)
    installed = LocalPluginInstaller(roots).install(package, "folder")
    host = PluginApplicationHost(roots, "version-gate", ToolRegistry())
    try:
        record = host.inventory().records[0]
        assert record.runtime_eligible
        assert host.set_enabled(record.install_id, True)["applicationState"] == "applied"
        assert (installed.code_dir / "imported").read_text() == "yes"
    finally:
        host.close()


@pytest.mark.parametrize(("current", "minimum", "reason"), [
    ("1.3.5", "1.3.6", "APP_VERSION_UNSUPPORTED"),
    ("1.3.6-beta.1", "1.3.6", "APP_VERSION_UNSUPPORTED"),
    (None, "1.3.6", "APP_VERSION_UNAVAILABLE"),
    ("1.3.6", "broken", "PLUGIN_MANIFEST_INVALID"),
    ("1.3.6", None, "PLUGIN_MANIFEST_INVALID"),
])
def test_incompatible_plugin_cannot_install_or_load(tmp_path, current, minimum, reason):
    import shutil
    from app.plugins.installer import PluginInstallError
    roots = _roots(tmp_path, current)
    package = _package(tmp_path / "package", yaml.safe_dump({"min_app_version": minimum}))
    with pytest.raises(PluginInstallError, match=reason):
        LocalPluginInstaller(roots).install(package, "folder")
    target = roots.user_root / "plugins/user/example.minimum"
    shutil.copytree(package, target)
    host = PluginApplicationHost(roots, "version-gate", ToolRegistry())
    try:
        record = host.inventory().records[0]
        assert not record.runtime_eligible
        assert record.reason_code == reason
        assert not (target / "imported").exists()
    finally:
        host.close()


@pytest.mark.parametrize(("manifest", "allowed"), [({}, True), ({"min_app_version": "1.3.6"}, True), ({"min_app_version": "1.4.0"}, False)])
def test_marketplace_checks_host_version_before_downloading(tmp_path, manifest, allowed):
    from app.core_host.plugin_settings import PluginSettingsBoundary
    roots = _roots(tmp_path, "1.3.6")
    boundary = PluginSettingsBoundary("generation", "credential", roots)
    result = boundary.handle({
        "generationId": "generation", "generationCredential": "credential",
        "name": "plugins.marketplace.check", "payload": manifest,
    })
    assert result["ok"] is allowed
    if not allowed:
        assert result["error"]["code"] == "APP_VERSION_UNSUPPORTED"
        assert "升级 Sakura" in result["error"]["message"]
