from __future__ import annotations

import stat
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from app.plugins.discovery import PluginDiscovery
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.runtime_v4 import PluginRuntimeManager
from app.storage.paths import StoragePaths


MANIFEST = """
api: 4
id: com.example.local
name: Local Fixture
version: 1.0.0
entry: plugin:LocalPlugin
provides: [com.example.local]
requires: []
""".strip()

PLUGIN_SOURCE = """
from pathlib import Path
from helper import Service

Path(__file__).with_name("imported.marker").write_text("imported", encoding="utf-8")

class LocalPlugin:
    def setup(self, context):
        Path(__file__).with_name("setup.marker").write_text("setup", encoding="utf-8")
        context.provide("com.example.local", Service(), exports=("ping",))
""".strip()

HELPER_SOURCE = """
class Service:
    def ping(self):
        return "pong"
""".strip()


def _plugin_folder(parent: Path, *, manifest: str = MANIFEST) -> Path:
    root = parent / "local-plugin"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(manifest, encoding="utf-8")
    (root / "plugin.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    (root / "helper.py").write_text(HELPER_SOURCE, encoding="utf-8")
    return root


def _plugin_zip(path: Path, *, manifest: str = MANIFEST) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("wrapper/plugin.yaml", manifest)
        archive.writestr("wrapper/plugin.py", PLUGIN_SOURCE)
        archive.writestr("wrapper/helper.py", HELPER_SOURCE)
    return path


class _BoundaryWorker:
    state = "ready"
    reason_code = "READY"

    def __init__(self, app_root: Path, *, fail_lifecycles: int = 0) -> None:
        self.app_root = app_root
        self.fail_lifecycles = fail_lifecycles
        self.lifecycle_count = 0

    def _apply_lifecycle(self) -> dict[str, object]:
        self.lifecycle_count += 1
        if self.fail_lifecycles:
            self.fail_lifecycles -= 1
            raise RuntimeError("plugin lifecycle failed")
        return self.settings_snapshot()

    def install_plugin(self, _install_id: str) -> dict[str, object]:
        return self._apply_lifecycle()

    def uninstall_plugin(self, _plugin_id: str) -> dict[str, object]:
        return self._apply_lifecycle()

    def settings_snapshot(self) -> dict[str, object]:
        plugins = []
        for spec in PluginDiscovery(self.app_root).discover():
            source = spec.source if spec.source in {"bundled", "user"} else "bundled"
            required = bool(spec.required and source != "user")
            invalid_user_required = source == "user" and spec.required
            enabled = bool(spec.enabled or required)
            plugins.append(
                {
                    "pluginId": spec.plugin_id,
                    "name": spec.name or spec.plugin_id,
                    "version": spec.version,
                    "author": spec.author,
                    "description": spec.description,
                    "enabled": enabled,
                    "required": required,
                    "supported": spec.api_version == 4,
                    "source": source,
                    "canUninstall": source == "user",
                    "state": "failed" if invalid_user_required else "active" if enabled else "disabled",
                    "reasonCode": (
                        "PLUGIN_MANIFEST_INVALID"
                        if invalid_user_required
                        else "READY"
                        if enabled
                        else "PLUGIN_DISABLED"
                    ),
                    "sections": [],
                }
            )
        return {"plugins": plugins}

    def public_snapshot(self) -> dict[str, object]:
        return self.settings_snapshot()


def _plugin_boundary(app_root: Path, worker: _BoundaryWorker):
    from app.core_host.plugin_settings import PluginSettingsBoundary

    session = type("Session", (), {"plugin_application": worker})()
    return PluginSettingsBoundary(
        "generation-local-install",
        "credential",
        app_root,
        session_provider=lambda: session,
    )


def test_folder_install_is_disabled_until_enabled_and_uninstall_keeps_data(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    installer = LocalPluginInstaller(app_root)

    installed = installer.install(source, "folder")
    assert installed.plugin_id == "com.example.local"
    assert installed.code_dir.parent == StoragePaths(app_root).user_plugins_dir
    assert (installed.code_dir / "imported.marker").is_file()
    assert not (installed.code_dir / "setup.marker").exists()
    spec = next(item for item in PluginDiscovery(app_root).discover() if item.plugin_id == installed.plugin_id)
    assert spec.source == "user"
    assert spec.enabled is False

    runtime = PluginRuntimeManager(
        app_root,
        "generation-local",
        PluginInventory(app_root).scan().runtime_specs,
    )
    try:
        plugin = runtime.start()["plugins"][0]
        assert plugin["state"] == "disabled"
        assert not (installed.code_dir / "setup.marker").exists()

        runtime.close()
        PluginDesiredStateStore(app_root).set(installed.plugin_id, True)
        runtime = PluginRuntimeManager(
            app_root,
            "generation-local-enabled",
            PluginInventory(app_root).scan().runtime_specs,
        )
        enabled = runtime.start()
        assert enabled["plugins"][0]["state"] == "active"
        assert (installed.code_dir / "setup.marker").is_file()
        assert runtime.call_service("com.example.local", "ping") == "pong"
    finally:
        runtime.close()

    private_data = StoragePaths(app_root).plugin_data_for(installed.plugin_id) / "keep.txt"
    private_data.parent.mkdir(parents=True, exist_ok=True)
    private_data.write_text("keep", encoding="utf-8")
    installer.uninstall(installed.install_id)
    assert not installed.code_dir.exists()
    assert private_data.read_text(encoding="utf-8") == "keep"
    assert installed.plugin_id not in {
        item.plugin_id for item in PluginDiscovery(app_root).discover()
    }
    assert installed.plugin_id not in StoragePaths(app_root).plugins_config().read_text(
        encoding="utf-8"
    )


def test_core_boundary_applies_local_lifecycle_and_never_returns_source_path(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root)
    boundary = _plugin_boundary(app_root, worker)
    revision = boundary.snapshot()["revision"]

    response = boundary.handle(
        {
            "id": "install-local",
            "name": "plugins.install",
            "generationId": "generation-local-install",
            "generationCredential": "credential",
            "payload": {
                "revision": revision,
                "sourceKind": "folder",
                "sourcePath": str(source.resolve()),
            },
        }
    )
    assert response["ok"] is True
    installed = response["payload"]
    assert installed["managementAction"] == "installed"
    assert installed["pluginId"] == "com.example.local"
    assert worker.lifecycle_count == 1
    assert str(source.resolve()) not in repr(response)
    assert "sourcePath" not in repr(response)

    private_data = StoragePaths(app_root).plugin_data_for("com.example.local") / "keep.txt"
    private_data.parent.mkdir(parents=True, exist_ok=True)
    private_data.write_text("keep", encoding="utf-8")
    uninstalled = boundary.uninstall(installed["revision"], installed["installId"])
    assert uninstalled["managementAction"] == "uninstalled"
    assert worker.lifecycle_count == 2
    assert private_data.read_text(encoding="utf-8") == "keep"


def test_core_boundary_rejects_revision_conflict_and_bundled_uninstall(tmp_path: Path) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root)
    boundary = _plugin_boundary(app_root, worker)
    stale_revision = boundary.snapshot()["revision"]
    config = StoragePaths(app_root).plugins_config()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[]\n", encoding="utf-8")
    with pytest.raises(PluginSettingsError) as conflict:
        boundary.install(stale_revision, "folder", str(source.resolve()))
    assert conflict.value.code == "CONFIG_REVISION_CONFLICT"
    assert worker.lifecycle_count == 0
    assert not StoragePaths(app_root).user_plugins_dir.exists()

    bundled = app_root / "plugins" / "builtin" / "bundled"
    bundled.mkdir(parents=True)
    (bundled / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    (bundled / "plugin.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    with pytest.raises(PluginSettingsError) as locked:
        bundled_record = next(
            item for item in boundary.snapshot()["plugins"]
            if item["pluginId"] == "com.example.local"
        )
        boundary.uninstall(boundary.snapshot()["revision"], bundled_record["installId"])
    assert locked.value.code == "BUNDLED_PLUGIN_LOCKED"
    assert worker.lifecycle_count == 0


def test_core_boundary_rolls_back_code_when_plugin_lifecycle_fails(tmp_path: Path) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root, fail_lifecycles=1)
    boundary = _plugin_boundary(app_root, worker)
    with pytest.raises(PluginSettingsError) as failed:
        boundary.install(boundary.snapshot()["revision"], "folder", str(source.resolve()))
    assert failed.value.code == "PLUGIN_INSTALL_APPLY_FAILED"
    assert worker.lifecycle_count == 1
    assert "com.example.local" not in {
        spec.plugin_id for spec in PluginDiscovery(app_root).discover()
    }
    config = StoragePaths(app_root).plugins_config()
    assert not config.is_file() or "com.example.local" not in config.read_text(encoding="utf-8")


def test_install_is_disabled_before_code_becomes_discoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    published = threading.Event()
    release = threading.Event()
    original_replace = LocalPluginInstaller._replace_path
    result: list[object] = []

    def pause_after_publish(source_path: Path, target_path: Path) -> None:
        original_replace(source_path, target_path)
        if target_path.parent == StoragePaths(app_root).user_plugins_dir:
            published.set()
            assert release.wait(5)

    monkeypatch.setattr(
        LocalPluginInstaller,
        "_replace_path",
        staticmethod(pause_after_publish),
    )

    def install() -> None:
        try:
            result.append(LocalPluginInstaller(app_root).install(source.resolve(), "folder"))
        except BaseException as error:  # pragma: no cover - surfaced below
            result.append(error)

    thread = threading.Thread(target=install)
    thread.start()
    assert published.wait(5)
    try:
        plugin = PluginInventory(app_root).scan().records[0]
        assert plugin.desired_enabled is False
        assert plugin.reason_code == "READY"
        assert not (
            StoragePaths(app_root).user_plugins_dir
            / "com.example.local"
                / "setup.marker"
        ).exists()
    finally:
        release.set()
        thread.join(timeout=5)
    assert len(result) == 1 and not isinstance(result[0], BaseException)


def test_failed_install_keeps_disabled_guard_when_code_removal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root, fail_lifecycles=1)
    boundary = _plugin_boundary(app_root, worker)
    original_remove = LocalPluginInstaller._remove_tree_checked

    def fail_code_removal(path: Path, code: str) -> None:
        if code == "PLUGIN_INSTALL_ROLLBACK_FAILED":
            raise PluginInstallError(code)
        original_remove(path, code)

    monkeypatch.setattr(
        LocalPluginInstaller,
        "_remove_tree_checked",
        staticmethod(fail_code_removal),
    )
    with pytest.raises(PluginSettingsError) as failed:
        boundary.install(boundary.snapshot()["revision"], "folder", str(source.resolve()))
    assert failed.value.code == "PLUGIN_INSTALL_ROLLBACK_FAILED"
    spec = next(
        item
        for item in PluginDiscovery(app_root).discover()
        if item.plugin_id == "com.example.local"
    )
    assert spec.enabled is False
    assert not (spec.plugin_root / "setup.marker").exists()


def test_core_boundary_rolls_back_uninstall_when_plugin_lifecycle_fails(tmp_path: Path) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root)
    boundary = _plugin_boundary(app_root, worker)
    installed = boundary.install(
        boundary.snapshot()["revision"],
        "folder",
        str(source.resolve()),
    )
    code_dir = StoragePaths(app_root).user_plugins_dir / "com.example.local"
    config = StoragePaths(app_root).plugins_config()
    config_before = config.read_text(encoding="utf-8")
    worker.fail_lifecycles = 1

    with pytest.raises(PluginSettingsError) as failed:
        boundary.uninstall(installed["revision"], installed["installId"])
    assert failed.value.code == "PLUGIN_UNINSTALL_APPLY_FAILED"
    assert worker.lifecycle_count == 2
    assert code_dir.is_dir()
    assert config.read_text(encoding="utf-8") == config_before
    assert "com.example.local" in {
        spec.plugin_id for spec in PluginDiscovery(app_root).discover()
    }




def test_uninstall_cleanup_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root)
    boundary = _plugin_boundary(app_root, worker)
    installed = boundary.install(
        boundary.snapshot()["revision"],
        "folder",
        str(source.resolve()),
    )
    original_remove = LocalPluginInstaller._remove_tree_checked

    def fail_cleanup(path: Path, code: str) -> None:
        if code == "PLUGIN_UNINSTALL_CLEANUP_FAILED":
            raise PluginInstallError(code)
        original_remove(path, code)

    monkeypatch.setattr(
        LocalPluginInstaller,
        "_remove_tree_checked",
        staticmethod(fail_cleanup),
    )
    with pytest.raises(PluginSettingsError) as failed:
        boundary.uninstall(installed["revision"], installed["installId"])
    assert failed.value.code == "PLUGIN_UNINSTALL_CLEANUP_FAILED"
    assert "com.example.local" not in {
        spec.plugin_id for spec in PluginDiscovery(app_root).discover()
    }
    assert list(StoragePaths(app_root).user_plugins_dir.glob(".uninstall-*/code"))


def test_uninstall_rollback_keeps_quarantine_when_code_restore_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host.plugin_settings import PluginSettingsError

    app_root = tmp_path / "app"
    source = _plugin_folder(tmp_path / "source")
    worker = _BoundaryWorker(app_root)
    boundary = _plugin_boundary(app_root, worker)
    installed = boundary.install(
        boundary.snapshot()["revision"],
        "folder",
        str(source.resolve()),
    )
    worker.fail_lifecycles = 1
    original_replace = LocalPluginInstaller._replace_path

    def fail_restore(source_path: Path, target_path: Path) -> None:
        if source_path.name == "code" and target_path.name == "com.example.local":
            raise PermissionError("locked")
        original_replace(source_path, target_path)

    monkeypatch.setattr(
        LocalPluginInstaller,
        "_replace_path",
        staticmethod(fail_restore),
    )
    with pytest.raises(PluginSettingsError) as failed:
        boundary.uninstall(installed["revision"], installed["installId"])
    assert failed.value.code == "PLUGIN_UNINSTALL_ROLLBACK_FAILED"
    assert not (StoragePaths(app_root).user_plugins_dir / "com.example.local").exists()
    assert list(StoragePaths(app_root).user_plugins_dir.glob(".uninstall-*/code"))




def test_zip_install_accepts_one_wrapper_and_rejects_duplicate_id(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    archive = _plugin_zip(tmp_path / "plugin.zip")
    installer = LocalPluginInstaller(app_root)

    installed = installer.install(archive, "zip")
    assert (installed.code_dir / "plugin.yaml").is_file()
    with pytest.raises(PluginInstallError, match="PLUGIN_ID_CONFLICT"):
        installer.install(archive, "zip")


def test_zip_install_rejects_escaping_paths(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("wrapper/plugin.yaml", MANIFEST)
        value.writestr("wrapper/plugin.py", PLUGIN_SOURCE)
        value.writestr("../escape.py", "escape")

    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_PATH_INVALID"):
        LocalPluginInstaller(tmp_path / "app").install(archive, "zip")
    assert not (tmp_path / "escape.py").exists()


def test_zip_and_folder_install_reject_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("wrapper/plugin.yaml", MANIFEST)
        value.writestr("wrapper/plugin.py", PLUGIN_SOURCE)
        link = zipfile.ZipInfo("wrapper/link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        value.writestr(link, "plugin.py")
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_SYMLINK_FORBIDDEN"):
        LocalPluginInstaller(tmp_path / "app-zip").install(archive, "zip")

    source = _plugin_folder(tmp_path / "source")
    try:
        (source / "link.py").symlink_to(source / "plugin.py")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_SYMLINK_FORBIDDEN"):
        LocalPluginInstaller(tmp_path / "app-folder").install(source, "folder")

    linked_source = _plugin_folder(tmp_path / "linked-source")
    source_link = tmp_path / "source-link"
    try:
        source_link.symlink_to(linked_source, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_SYMLINK_FORBIDDEN"):
        LocalPluginInstaller(tmp_path / "app-source-link").install(source_link, "folder")


def test_zip_install_rejects_windows_reserved_paths(tmp_path: Path) -> None:
    archive = tmp_path / "reserved.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("wrapper/plugin.yaml", MANIFEST)
        value.writestr("wrapper/plugin.py", PLUGIN_SOURCE)
        value.writestr("wrapper/CON.py", "reserved")
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_PATH_INVALID"):
        LocalPluginInstaller(tmp_path / "app").install(archive, "zip")




def test_install_rejects_unsupported_required_and_bundled_id_conflicts(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    bundled = app_root / "plugins" / "builtin" / "bundled"
    bundled.mkdir(parents=True)
    (bundled / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")

    source = _plugin_folder(tmp_path / "source")
    with pytest.raises(PluginInstallError, match="PLUGIN_ID_CONFLICT"):
        LocalPluginInstaller(app_root).install(source, "folder")

    api2 = _plugin_folder(
        tmp_path / "api2",
        manifest=MANIFEST.replace("api: 4", "api: 2").replace(
            "com.example.local", "com.example.api2"
        ),
    )
    with pytest.raises(PluginInstallError, match="API_VERSION_UNSUPPORTED"):
        LocalPluginInstaller(tmp_path / "app-api2").install(api2, "folder")

    required = _plugin_folder(
        tmp_path / "required",
        manifest=MANIFEST.replace(
            "id: com.example.local", "id: com.example.required\nrequired: true"
        ).replace("com.example.local]", "com.example.required]"),
    )
    with pytest.raises(PluginInstallError, match="PLUGIN_MANIFEST_INVALID"):
        LocalPluginInstaller(tmp_path / "app-required").install(required, "folder")


def test_install_rejects_invalid_existing_plugin_config(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    config = StoragePaths(app_root).plugins_config()
    config.parent.mkdir(parents=True)
    config.write_text("invalid: mapping\n", encoding="utf-8")
    source = _plugin_folder(tmp_path / "source")
    with pytest.raises(PluginInstallError, match="PLUGIN_CONFIG_INVALID"):
        LocalPluginInstaller(app_root).install(source, "folder")
    user_root = StoragePaths(app_root).user_plugins_dir
    assert not user_root.exists() or not any(user_root.iterdir())


def test_install_rejects_plugins_beyond_public_management_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.plugins import installer as installer_module

    app_root = tmp_path / "app"
    bundled = app_root / "plugins" / "builtin" / "bundled"
    bundled.mkdir(parents=True)
    (bundled / "plugin.yaml").write_text(
        MANIFEST.replace("com.example.local", "com.example.bundled"),
        encoding="utf-8",
    )
    (bundled / "plugin.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    monkeypatch.setattr(installer_module, "MAX_DISCOVERED_PLUGINS", 1)

    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_TOO_MANY_PLUGINS"):
        LocalPluginInstaller(app_root).install(
            _plugin_folder(tmp_path / "source"),
            "folder",
        )




def test_install_rejects_malformed_manifest_field_types(
    tmp_path: Path,
) -> None:
    manifest = MANIFEST.replace("requires: []", "requires: [com.example.required, 7]")
    source = _plugin_folder(tmp_path / "source", manifest=manifest)
    with pytest.raises(PluginInstallError, match="PLUGIN_MANIFEST_INVALID"):
        LocalPluginInstaller(tmp_path / "app").install(source, "folder")








def test_user_plugin_import_names_do_not_collide_after_normalization(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    installer = LocalPluginInstaller(app_root)

    for suffix, result in (("a-b", "hyphen"), ("a_b", "underscore")):
        plugin_id = f"com.example.{suffix}"
        root = _plugin_folder(
            tmp_path / suffix,
            manifest=MANIFEST.replace("com.example.local", plugin_id),
        )
        (root / "plugin.py").write_text(
            f'''class Service:
    def ping(self):
        from helper import VALUE
        return VALUE

class LocalPlugin:
    def setup(self, context):
        context.provide("{plugin_id}", Service(), exports=("ping",))
''',
            encoding="utf-8",
        )
        (root / "helper.py").write_text(f"VALUE = {result!r}\n", encoding="utf-8")
        installer.install(root, "folder")

    desired = PluginDesiredStateStore(app_root)
    desired.write({
        "com.example.a-b": True,
        "com.example.a_b": True,
    })
    runtime = PluginRuntimeManager(
        app_root,
        "generation-import-names",
        PluginInventory(app_root).scan().runtime_specs,
    )
    try:
        runtime.start()
        plugin_results = (
            ("com.example.a-b", "hyphen"),
            ("com.example.a_b", "underscore"),
        )
        for plugin_id, result in plugin_results:
            assert runtime.call_service(plugin_id, "ping") == result
    finally:
        runtime.close()




def test_folder_copy_stays_bounded_when_source_grows_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.plugins import installer as installer_module

    source = _plugin_folder(tmp_path / "source")
    growing = source / "grow.bin"
    growing.write_bytes(b"x")
    original_open = LocalPluginInstaller._open_regular_source

    @contextmanager
    def grow_after_open(self, path: Path):
        with original_open(self, path) as opened:
            if path.name == "grow.bin":
                with path.open("ab") as writer:
                    writer.write(b"x" * 2048)
            yield opened

    monkeypatch.setattr(installer_module, "MAX_PLUGIN_FILE_BYTES", 1024)
    monkeypatch.setattr(LocalPluginInstaller, "_open_regular_source", grow_after_open)
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_TOO_LARGE"):
        LocalPluginInstaller(tmp_path / "app").install(source.resolve(), "folder")


def test_install_file_limit_rolls_back_promoted_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.plugins import installer as installer_module

    source = _plugin_folder(tmp_path / "source")
    monkeypatch.setattr(installer_module, "MAX_PLUGIN_FILES", 2)
    app_root = tmp_path / "app"
    with pytest.raises(PluginInstallError, match="PLUGIN_INSTALL_TOO_MANY_FILES"):
        LocalPluginInstaller(app_root).install(source, "folder")
    user_root = StoragePaths(app_root).user_plugins_dir
    assert not user_root.exists() or not [
        path for path in user_root.iterdir() if not path.name.startswith(".install-")
    ]
