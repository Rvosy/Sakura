from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import _project_plugin
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.app_compatibility import semver_precedence
from app.plugins.discovery import PluginDiscovery, plugin_spec_from_manifest
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.plugins.models import PluginSpec
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
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


@pytest.mark.parametrize(("lower", "higher"), [
    ("1.2.3", "1.2.4"), ("1.9.0", "1.10.0"), ("1.99.99", "2.0.0"),
    ("1.0.0-alpha", "1.0.0-alpha.1"), ("1.0.0-alpha.1", "1.0.0-alpha.beta"),
    ("1.0.0-alpha.beta", "1.0.0-beta"), ("1.0.0-beta.2", "1.0.0-beta.11"),
    ("1.0.0-beta.11", "1.0.0-rc.1"), ("1.0.0-rc.1", "1.0.0"),
])
def test_semver_precedence(lower: str, higher: str) -> None:
    assert semver_precedence(lower) < semver_precedence(higher)
    assert semver_precedence(lower + "+build.1") == semver_precedence(lower + "+other.02")


@pytest.mark.parametrize("minimum", ["1.2", "v1.2.3", "01.2.3", "1.2.3-01", "1.2.3-", "1.2.3+", "1.2.3-汉字", " 1.2.3", "1.2.3\n", "", None, 123])
def test_invalid_declaration_is_manifest_error_before_any_install_probe(tmp_path, monkeypatch, minimum):
    roots = _roots(tmp_path)
    package = _package(tmp_path / "package", yaml.safe_dump({"min_app_version": minimum}))
    installer = LocalPluginInstaller(roots)
    monkeypatch.setattr(installer._dependencies, "install", lambda *args, **kwargs: pytest.fail("dependency probe"))
    with pytest.raises(PluginInstallError, match="PLUGIN_MANIFEST_INVALID"):
        installer.install(package, "folder")
    _package(roots.user_root / "plugins/user/invalid", yaml.safe_dump({"min_app_version": minimum}))
    record = PluginInventory(roots).scan().records[0]
    assert record.reason_code == "PLUGIN_MANIFEST_INVALID"
    assert record.runtime_spec() is None
    raw = yaml.safe_load((package / "plugin.yaml").read_text(encoding="utf-8"))
    assert plugin_spec_from_manifest(raw, package) is None
    assert not (package / "imported").exists()


@pytest.mark.parametrize(("current", "reason"), [
    ("1.2.2", "APP_VERSION_UNSUPPORTED"), ("1.2.3-rc.1", "APP_VERSION_UNSUPPORTED"),
    (None, "APP_VERSION_UNAVAILABLE"), ("invalid", "APP_VERSION_UNAVAILABLE"),
])
def test_install_rejects_before_dependencies_import_or_config_write(tmp_path, monkeypatch, current, reason):
    roots = _roots(tmp_path, current)
    # The distribution version owns compatibility even if the user root claims a newer one.
    (roots.user_root / "VERSION").write_text("99.0.0", encoding="utf-8")
    package = _package(tmp_path / "package", "min_app_version: 1.2.3\n", api=3)
    installer = LocalPluginInstaller(roots)
    monkeypatch.setattr(installer._dependencies, "install", lambda *args, **kwargs: pytest.fail("dependency probe"))
    with pytest.raises(PluginInstallError, match=reason):
        installer.install(package, "folder")
    assert PluginDesiredStateStore(roots.user_root).read() == {}
    assert not (package / "imported").exists()
    assert PluginInventory(roots).scan().records == ()


@pytest.mark.parametrize("declaration", ["", "min_app_version: 1.2.3+minimum.1\n"])
def test_legacy_or_compatible_plugin_installs_and_discovery_preserves_minimum(tmp_path, declaration):
    roots = _roots(tmp_path, "1.2.3+current.2" if declaration else None)
    package = _package(tmp_path / "package", declaration)
    installed = LocalPluginInstaller(roots).install(package, "folder")
    record = PluginInventory(roots).scan().records[0]
    assert record.supported and record.runtime_eligible
    assert record.min_app_version == ("1.2.3+minimum.1" if declaration else "")
    assert PluginDiscovery(roots).discover()[0].min_app_version == record.min_app_version
    assert record.runtime_spec().to_plugin_spec(roots).min_app_version == record.min_app_version
    assert (installed.code_dir / "imported").read_text() == "yes"


@pytest.mark.parametrize(("minimum", "reason"), [
    ("2.0.0", "APP_VERSION_UNSUPPORTED"), ("1.2.3-01", "PLUGIN_MANIFEST_INVALID"),
])
def test_direct_runtime_spec_rejects_before_prepare_dependencies_and_process(tmp_path, monkeypatch, minimum, reason):
    roots = _roots(tmp_path)
    package = _package(tmp_path / "package")
    spec = PluginSpec(entry="plugin:Plugin", plugin_id="example.minimum", plugin_root=package,
                      min_app_version=minimum, api_version=3, requires=("missing.service",))
    manager = PluginRuntimeManager(roots, "version-gate", [spec],
                                   before_start=lambda spec: pytest.fail("prepare callback"))
    monkeypatch.setattr(manager._dependencies, "verified_root", lambda *args, **kwargs: pytest.fail("dependency probe"))
    monkeypatch.setattr("app.plugins.runtime_v4._PluginProcess", lambda **kwargs: pytest.fail("process creation"))
    try:
        assert manager.start()["plugins"][0]["reasonCode"] == reason
        assert manager.set_enabled(spec.plugin_id, True)["plugins"][0]["reasonCode"] == reason
        assert not (package / "imported").exists()
    finally:
        manager.close()


def test_installed_incompatible_plugin_stays_visible_and_enable_does_not_write_desired_state(tmp_path):
    roots = _roots(tmp_path)
    _package(roots.user_root / "plugins/user/newer", "min_app_version: 2.0.0\n")
    host = PluginApplicationHost(roots, "installed-gate", ToolRegistry())
    try:
        record = host.inventory().records[0]
        assert record.reason_code == "APP_VERSION_UNSUPPORTED"
        assert host.inventory().runtime_specs == ()
        public = _project_plugin(host.settings_snapshot()["plugins"][0])
        assert public["minAppVersion"] == "2.0.0"
        assert public["reasonCode"] == "APP_VERSION_UNSUPPORTED"
        assert public["supported"] is False
        with pytest.raises(PluginRuntimeError) as failure:
            host.set_enabled(record.install_id, True)
        assert failure.value.code == "APP_VERSION_UNSUPPORTED"
        assert PluginDesiredStateStore(roots.user_root).read() == {}
        assert host.set_enabled(record.install_id, False)["applicationState"] == "applied"
        assert PluginDesiredStateStore(roots.user_root).read() == {"example.minimum": False}
        assert host.marketplace_context()["appVersion"] == "1.2.3"
        (roots.distribution_root / "VERSION").write_bytes(b"\xff")
        assert host.marketplace_context()["appVersion"] == ""
    finally:
        host.close()
