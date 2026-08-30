from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.core_host.plugin_settings import _preview_plugin
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory


def _plugin(
    root: Path,
    directory: str,
    plugin_id: str,
    *,
    source: str = "bundled",
    extra: str = "",
) -> Path:
    base = root / "plugins" / ("builtin" if source == "bundled" else "user")
    target = base / directory
    target.mkdir(parents=True)
    (target / "plugin.py").write_text(
        "class Plugin:\n    def setup(self, _context):\n        pass\n",
        encoding="utf-8",
    )
    (target / "plugin.yaml").write_text(
        (
            "api: 4\n"
            f"id: {plugin_id}\n"
            f"name: {directory}\n"
            "version: 1.0.0\n"
            "entry: plugin:Plugin\n"
            "provides: []\n"
            "requires: []\n"
            f"{extra}"
        ),
        encoding="utf-8",
    )
    return target


def _by_directory(snapshot) -> dict[str, object]:
    return {record.directory_name: record for record in snapshot.records}


def test_inventory_keeps_every_non_hidden_invalid_user_installation_visible(tmp_path: Path) -> None:
    root = tmp_path / "app"
    plugins = root / "plugins" / "user"
    plugins.mkdir(parents=True)
    (plugins / "missing_manifest").mkdir()
    malformed = plugins / "malformed_yaml"
    malformed.mkdir()
    (malformed / "plugin.yaml").write_text("[not: valid", encoding="utf-8")
    missing_id = plugins / "missing_id"
    missing_id.mkdir()
    (missing_id / "plugin.py").write_text("class Plugin: pass\n", encoding="utf-8")
    (missing_id / "plugin.yaml").write_text(
        "api: 4\nentry: plugin:Plugin\n",
        encoding="utf-8",
    )
    too_long = "p" * 65
    _plugin(root, "too_long", too_long)
    hidden = plugins / ".install-staging"
    hidden.mkdir()
    (hidden / "plugin.yaml").write_text("invalid", encoding="utf-8")

    snapshot = PluginInventory(root).scan()
    records = _by_directory(snapshot)

    assert set(records) == {"missing_manifest", "malformed_yaml", "missing_id", "too_long"}
    assert snapshot.runtime_specs == ()
    for record in records.values():
        assert record.plugin_id is None
        assert record.reason_code == "PLUGIN_MANIFEST_INVALID"
        assert record.runtime_eligible is False
        assert re.fullmatch(r"pi_[0-9a-f]{24}", record.install_id)


def test_inventory_ignores_non_plugin_directories_in_bundled_python_package(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    plugins = root / "plugins" / "builtin"
    (plugins / "__pycache__").mkdir(parents=True)
    (plugins / "legacy_example").mkdir()
    (plugins / "helper_package").mkdir()
    (plugins / "helper_package" / "__init__.py").write_text("", encoding="utf-8")
    malformed = plugins / "declared_but_malformed"
    malformed.mkdir()
    (malformed / "plugin.yaml").write_text("[not: valid", encoding="utf-8")

    records = _by_directory(PluginInventory(root).scan())

    assert set(records) == {"declared_but_malformed"}
    assert records["declared_but_malformed"].reason_code == "PLUGIN_MANIFEST_INVALID"


def test_inventory_duplicate_policy_keeps_only_single_bundled_winner(tmp_path: Path) -> None:
    root = tmp_path / "app"
    _plugin(root, "bundled", "com.example.same")
    _plugin(root, "user_a", "com.example.same", source="user")
    _plugin(root, "user_b", "com.example.same", source="user")

    snapshot = PluginInventory(root).scan()
    records = _by_directory(snapshot)

    assert [spec.directory_name for spec in snapshot.runtime_specs] == ["bundled"]
    assert records["bundled"].reason_code == "READY"
    assert records["user_a"].reason_code == "PLUGIN_ID_CONFLICT"
    assert records["user_b"].reason_code == "PLUGIN_ID_CONFLICT"
    assert records["user_a"].can_uninstall is True


@pytest.mark.parametrize("source", ["bundled", "user"])
def test_inventory_same_source_duplicates_all_conflict(tmp_path: Path, source: str) -> None:
    root = tmp_path / "app"
    _plugin(root, "copy_a", "com.example.duplicate", source=source)
    _plugin(root, "copy_b", "com.example.duplicate", source=source)

    snapshot = PluginInventory(root).scan()

    assert snapshot.runtime_specs == ()
    assert {record.reason_code for record in snapshot.records} == {"PLUGIN_ID_CONFLICT"}


def test_only_bundled_manifest_can_make_plugin_required(tmp_path: Path) -> None:
    root = tmp_path / "app"
    _plugin(root, "bundled_required", "com.example.bundled", extra="required: true\n")
    _plugin(
        root,
        "user_required",
        "com.example.user",
        source="user",
        extra="required: true\n",
    )
    desired = PluginDesiredStateStore(root)
    desired.write({"com.example.bundled": False, "com.example.user": False})

    records = _by_directory(PluginInventory(root, desired).scan())

    assert records["bundled_required"].required is True
    assert records["bundled_required"].desired_enabled is True
    assert records["user_required"].required is False
    assert records["user_required"].desired_enabled is False


@pytest.mark.parametrize("retired_field", ["plugin_id: com.example.old\n", "api_version: 3\n"])
def test_inventory_rejects_retired_manifest_fields(
    tmp_path: Path,
    retired_field: str,
) -> None:
    root = tmp_path / "app"
    plugin = _plugin(root, "fixture", "com.example.fixture")
    manifest = plugin / "plugin.yaml"
    text = manifest.read_text(encoding="utf-8")
    if retired_field.startswith("plugin_id"):
        text = text.replace("id: com.example.fixture\n", retired_field)
    else:
        text = text.replace("api: 4\n", retired_field)
    manifest.write_text(text, encoding="utf-8")

    record = PluginInventory(root).scan().records[0]

    assert record.reason_code == "PLUGIN_MANIFEST_INVALID"
    assert record.runtime_eligible is False


def test_management_write_rejects_retired_fields_without_rewriting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    plugin = _plugin(root, "fixture", "com.example.fixture")
    store = PluginDesiredStateStore(root)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        "- id: com.example.fixture\n  enabled: false\n  priority: 99\n  required: true\n",
        encoding="utf-8",
    )
    before = store.path.read_bytes()

    with pytest.raises(ValueError, match="PLUGIN_CONFIG_INVALID"):
        store.set("com.example.fixture", True)

    assert store.path.read_bytes() == before


def test_inventory_install_id_is_stable_opaque_and_public_preview_has_no_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-app-root"
    plugin = _plugin(root, "readable-directory", "com.example.fixture", source="user")
    (plugin / "plugin.yaml").write_text(
        (plugin / "plugin.yaml").read_text(encoding="utf-8").replace(
            "name: readable-directory", "name: Fixture"
        ),
        encoding="utf-8",
    )
    first = PluginInventory(root).scan().records[0]
    second = PluginInventory(root).scan().records[0]
    preview = _preview_plugin(first)

    assert first.install_id == second.install_id
    assert first.install_id != first.plugin_id
    assert "readable-directory" not in repr(preview)
    assert str(root) not in repr(preview)
    assert set(preview).isdisjoint({"directoryName", "path", "entry"})


def test_inventory_rejects_linked_plugin_directories(tmp_path: Path) -> None:
    root = tmp_path / "app"
    target = _plugin(root, "real", "com.example.real")
    link = root / "plugins" / "user" / "linked"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    records = _by_directory(PluginInventory(root).scan())

    assert records["real"].runtime_eligible is True
    assert records["linked"].plugin_id is None
    assert records["linked"].reason_code == "PLUGIN_MANIFEST_INVALID"
    assert records["linked"].can_uninstall is True
