"""启动时迁出退役内置插件；旧文件优先，缺失时从固定源码版本恢复。"""
from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

from app.plugin_sdk.sakura_http import urlopen_direct_for_loopback
from app.storage.atomic import atomic_write_text
from app.storage.runtime_roots import RuntimeRoots

SOURCES = json.loads(Path(__file__).with_name("migration_sources.json").read_text(encoding="utf-8"))
MIGRATIONS = {key: value["directory"] for key, value in SOURCES.items()}
STATE_NAME = "plugin-migrations.json"


def download_migration_package(plugin_id: str, output: Path) -> None:
    source = SOURCES[plugin_id]
    url = f"https://codeload.github.com/{source['repository']}/zip/{source['commit']}"
    request = urllib.request.Request(url, headers={"User-Agent": "Sakura-Plugin-Migration/1"})
    from app.plugins.installer import MAX_ARCHIVE_BYTES
    with urlopen_direct_for_loopback(request, timeout=30) as response, output.open("wb") as target:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("PLUGIN_INSTALL_ARCHIVE_TOO_LARGE")
            target.write(chunk)


def ensure_external_plugin(roots: RuntimeRoots, plugin_id: str, *, enabled: bool) -> None:
    from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots
    from app.plugins.installer import LocalPluginInstaller
    from app.plugins.inventory import PluginInventory

    if any(r.source == "user" and r.plugin_id == plugin_id for r in PluginInventory(roots).scan().records):
        return
    directory = MIGRATIONS[plugin_id]
    source = roots.distribution_root / "plugins/builtin" / directory
    if not source.is_dir() and (roots.distribution_root / "app/core_host").is_dir():
        source = roots.distribution_root / "plugins/optional" / directory
    installer = LocalPluginInstaller(roots)
    if source.is_dir():
        dependencies = PluginDependencyRoots(roots.user_root, distribution_root=roots.distribution_root)
        try:
            dependencies.verified_root(plugin_id, source, source="bundled")
            offline = True
        except PluginDependencyError as error:
            if error.code not in {"PLUGIN_DEPENDENCIES_MISSING", "PLUGIN_DEPENDENCIES_STALE"}:
                raise
            offline = False
        installer.install(source, "folder", initial_enabled=enabled,
                          expected_plugin_id=plugin_id, offline_dependencies=offline, reuse_dependencies=True)
        return
    # No package or dependency is added to the main distribution for this fallback.
    temporary_root = roots.user_root / "plugins"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".migration-", dir=temporary_root) as temporary:
        package = Path(temporary) / "plugin.zip"
        download_migration_package(plugin_id, package)
        installer.install(package, "zip", initial_enabled=enabled,
                          expected=(plugin_id, SOURCES[plugin_id]["version"]), reuse_dependencies=True)


def migrate_bundled_plugins(roots: RuntimeRoots) -> None:
    from app.plugins.inventory import PluginDesiredStateStore

    config = roots.user_root / "config"
    state_path = config / STATE_NAME
    try:
        completed = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        completed = {} if config.exists() else {key: "not_applicable" for key in MIGRATIONS}
    if not isinstance(completed, dict) or any(
        not isinstance(key, str) or value not in ("completed", "not_applicable")
        for key, value in completed.items()
    ):
        raise ValueError("PLUGIN_MIGRATION_STATE_INVALID")
    desired = PluginDesiredStateStore(roots.user_root).read()
    for plugin_id in MIGRATIONS:
        if plugin_id in completed:
            continue
        try:
            ensure_external_plugin(roots, plugin_id, enabled=desired.get(plugin_id, True))
        except Exception as error:
            # Keep original failure and do not mark complete or touch plugin data.
            raise RuntimeError(f"插件恢复失败（{plugin_id}）：{error}。请检查网络或磁盘后重新启动。") from error
        completed[plugin_id] = "completed"
        atomic_write_text(state_path, json.dumps(completed, ensure_ascii=False, indent=2) + "\n")
    if not state_path.exists():
        atomic_write_text(state_path, json.dumps(completed, ensure_ascii=False, indent=2) + "\n")
