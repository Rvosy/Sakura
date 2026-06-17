"""插件 .plugin 包导入/导出测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.plugins.archive import (
    PluginArchiveError,
    export_plugin_archive,
    import_plugin_archive,
)


def test_export_plugin_archive_excludes_runtime_and_cache(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, "demo")
    (plugin_dir / "data").mkdir()
    (plugin_dir / "data" / "state.json").write_text("{}", encoding="utf-8")
    (plugin_dir / "models").mkdir()
    (plugin_dir / "models" / "cache.bin").write_bytes(b"cache")
    (plugin_dir / "__pycache__").mkdir()
    (plugin_dir / "__pycache__" / "plugin.pyc").write_bytes(b"pyc")

    result = export_plugin_archive(plugin_dir, tmp_path / "demo.plugin")

    assert result.plugin_id == "demo"
    with zipfile.ZipFile(result.archive_path, "r") as archive:
        names = set(archive.namelist())
    assert "plugin.yaml" in names
    assert "plugin.py" in names
    assert "data/state.json" not in names
    assert "models/cache.bin" not in names
    assert "__pycache__/plugin.pyc" not in names


def test_import_plugin_archive_extracts_safe_package(tmp_path: Path) -> None:
    source_plugin = _write_plugin(tmp_path / "source", "voice_input")
    archive_path = tmp_path / "voice_input.plugin"
    export_plugin_archive(source_plugin, archive_path)
    base_dir = tmp_path / "runtime"

    result = import_plugin_archive(archive_path, base_dir)

    assert result.plugin_id == "voice_input"
    assert (base_dir / "plugins" / "voice_input" / "plugin.yaml").is_file()
    assert (base_dir / "plugins" / "voice_input" / "plugin.py").is_file()


def test_import_plugin_archive_rejects_existing_plugin(tmp_path: Path) -> None:
    source_plugin = _write_plugin(tmp_path / "source", "demo")
    archive_path = tmp_path / "demo.plugin"
    export_plugin_archive(source_plugin, archive_path)
    base_dir = tmp_path / "runtime"
    (base_dir / "plugins" / "demo").mkdir(parents=True)

    with pytest.raises(PluginArchiveError, match="已存在"):
        import_plugin_archive(archive_path, base_dir)


def test_import_plugin_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.plugin"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin.yaml", _manifest_text("demo"))
        archive.writestr("plugin.py", "from app.plugins import PluginBase\n")
        archive.writestr("../escape.txt", "bad")

    with pytest.raises(PluginArchiveError, match="不安全路径"):
        import_plugin_archive(archive_path, tmp_path / "runtime")


def test_import_plugin_archive_rejects_absolute_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.plugin"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin.yaml", _manifest_text("demo"))
        archive.writestr("plugin.py", "from app.plugins import PluginBase\n")
        archive.writestr("/absolute.txt", "bad")

    with pytest.raises(PluginArchiveError, match="安全的相对路径"):
        import_plugin_archive(archive_path, tmp_path / "runtime")


def test_import_plugin_archive_rejects_unknown_permission(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.plugin"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin.yaml", _manifest_text("demo", permissions=("unknown",)))
        archive.writestr("plugin.py", "from app.plugins import PluginBase\n")

    with pytest.raises(PluginArchiveError, match="未知权限"):
        import_plugin_archive(archive_path, tmp_path / "runtime")


def test_import_plugin_archive_requires_entry_file(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.plugin"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin.yaml", _manifest_text("demo", entry="missing:DemoPlugin"))

    with pytest.raises(PluginArchiveError, match="入口文件"):
        import_plugin_archive(archive_path, tmp_path / "runtime")


def test_import_plugin_archive_requires_plugin_suffix(tmp_path: Path) -> None:
    archive_path = tmp_path / "demo.zip"
    archive_path.write_bytes(b"not checked")

    with pytest.raises(PluginArchiveError, match=r"\.plugin"):
        import_plugin_archive(archive_path, tmp_path / "runtime")


def _write_plugin(root: Path, plugin_id: str) -> Path:
    plugin_dir = root / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(_manifest_text(plugin_id), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        f"""
from app.plugins import PluginBase


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"
""".lstrip(),
        encoding="utf-8",
    )
    return plugin_dir


def _manifest_text(
    plugin_id: str,
    *,
    entry: str = "plugin:DemoPlugin",
    permissions: tuple[str, ...] = ("tool",),
) -> str:
    permissions_text = "\n".join(f"  - {permission}" for permission in permissions)
    return f"""
api_version: 1
id: {plugin_id}
name: Demo
version: 1.0.0
entry: {entry}
enabled: true
permissions:
{permissions_text}
""".lstrip()
