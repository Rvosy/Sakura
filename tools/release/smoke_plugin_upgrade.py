#!/usr/bin/env python3
"""Exercise staged migration and normal installation without checkout imports.

The default cases use representative saved user states and the actual target
payload. --historical-distribution consumes retained builtin code/dependencies
from an extracted historical release in overlay mode; replacement mode discards
the old program resources and keeps the saved user-state fixtures. Neither mode
tests the native installer/updater or launches the historical application.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def local_dependency_plugin(work: Path) -> Path:
    """A wheel is local so this verifies uv execution without an index/cache."""
    wheel = work / "sakura_release_probe-1.0-py3-none-any.whl"
    files = {
        "sakura_release_probe.py": "VALUE = 'installed by bundled uv'\n",
        "sakura_release_probe-1.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\nName: sakura-release-probe\nVersion: 1.0\n"
        ),
        "sakura_release_probe-1.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: Sakura release smoke\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = "sakura_release_probe-1.0.dist-info/RECORD"
    files[record] = "".join(f"{name},,\n" for name in [*files, record])
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    plugin = work / "uv-probe"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "api: 4\nid: com.sakura.release.uv-probe\nname: Release probe\n"
        "version: 1.0.0\nentry: plugin:Plugin\nprovides: []\nrequires: []\n",
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        "from sakura_release_probe import VALUE\n"
        "assert VALUE == 'installed by bundled uv'\n"
        "class Plugin:\n    def setup(self, context):\n        pass\n",
        encoding="utf-8",
    )
    (plugin / "requirements.txt").write_text(wheel.as_uri() + "\n", encoding="utf-8")
    return plugin


def run(
    stage: Path, work: Path, historical_distribution: Path | None = None,
    *, upgrade_mode: str = "overlay",
) -> None:
    # -I leaves neither the checkout nor the script's directory on sys.path.
    sys.path.insert(0, str(stage / "core"))
    from app.plugins.bundled_migrations import MIGRATIONS, migrate_bundled_plugins
    from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots
    from app.plugins.installer import LocalPluginInstaller
    from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
    from app.storage.runtime_roots import RuntimeRoots

    import app
    assert Path(app.__file__).resolve().is_relative_to(stage / "core")
    assert not (stage / "plugins/optional").exists()
    bundled_uv = stage / "python/tools" / ("uv.exe" if os.name == "nt" else "uv")
    runner = stage / "core/app/plugins/plugin_runner_v4.py"
    uv_commands: list[list[str]] = []
    installing = False

    def audit(event: str, args: tuple) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "urllib.Request"}:
            raise AssertionError(f"Release migration attempted network access: {event}")
        if event == "subprocess.Popen":
            executable, command = Path(args[0]).resolve(), args[1]
            if executable == bundled_uv.resolve() and installing:
                uv_commands.append(list(command))
            elif executable == Path(sys.executable).resolve() and str(runner) in command:
                pass
            else:
                raise AssertionError(f"Unexpected release smoke subprocess: {command}")

    sys.addaudithook(audit)
    fresh = RuntimeRoots(stage, work / "new-user")
    assert migrate_bundled_plugins(fresh) == {}
    assert not any(record.plugin_id in MIGRATIONS for record in PluginInventory(fresh).scan().records)

    distribution = stage
    if historical_distribution is not None and upgrade_mode == "overlay":
        # Model replacement while retaining the old plugin directories, as with
        # a portable overlay. Current builtin implementations stay current.
        distribution = work / "upgraded-distribution"
        for name in ("plugins", "migration_payload"):
            shutil.copytree(stage / name, distribution / name)
        for plugin_id, directory in MIGRATIONS.items():
            old_code = historical_distribution / "plugins/builtin" / directory
            old_dependencies = historical_distribution / "plugins/dependencies" / plugin_id
            if old_code.is_dir():
                shutil.copytree(old_code, distribution / "plugins/builtin" / directory, dirs_exist_ok=True)
            if old_dependencies.is_dir():
                shutil.copytree(old_dependencies, distribution / "plugins/dependencies" / plugin_id, dirs_exist_ok=True)

    desired = {plugin_id: index % 2 == 0 for index, plugin_id in enumerate(MIGRATIONS)}
    # These providers defer dependency imports until enabled. An entry-only
    # check would accept a marker-only Mem0 root without usable dependencies.
    dependency_imports = {
        "sakura.asr.sensevoice": "numpy, sherpa_onnx",
        "sakura.memory.mem0": "qdrant_client, sqlalchemy, fastembed, onnxruntime",
        "sakura.tts.genie": "py7zr, py7zz",
        "sakura.tts.gpt-sovits": "py7zr, py7zz, yaml",
    }
    probes = work / "dependency-probes"
    probes.mkdir()
    for scenario in ("legacy-config", "interrupted-1.2.0"):
        user = work / scenario
        roots = RuntimeRoots(distribution, user)
        PluginDesiredStateStore(user).write(desired)
        saved_data = user / "data/plugin-settings/saved.json"
        saved_data.parent.mkdir(parents=True)
        saved_data.write_bytes(b'{"keep":"user resource"}\n')
        if scenario == "interrupted-1.2.0":
            (user / "config/plugin-migrations.json").write_text(
                json.dumps({key: "repairing" if index == 0 else "completed"
                            for index, key in enumerate(MIGRATIONS)}), encoding="utf-8",
            )
            for plugin_id, directory in list(MIGRATIONS.items())[1:]:
                shutil.copytree(
                    stage / "migration_payload/builtin-extraction-v1/plugins" / directory,
                    user / "plugins/user" / plugin_id,
                )
            # A marker without modules must not cause reuse of a broken root.
            broken = user / "data/plugin-runtime/dependencies/sakura.memory.mem0"
            broken.mkdir(parents=True)
            (broken / ".sakura-dependencies.json").write_text(
                json.dumps({"schemaVersion": 1, "kind": "requirements.txt",
                            "python": f"{sys.version_info.major}.{sys.version_info.minor}"}),
                encoding="utf-8",
            )
        failures = migrate_bundled_plugins(roots)
        assert not failures, (scenario, failures)
        records = {record.plugin_id: record for record in PluginInventory(roots).scan().records
                   if record.source == "user"}
        assert set(records) == set(MIGRATIONS), (scenario, records)
        dependencies = PluginDependencyRoots(user, distribution_root=distribution)
        for plugin_id, record in records.items():
            code = user / "plugins/user" / record.directory_name
            root = dependencies.verified_root(plugin_id, code)
            dependencies._validate_entry(plugin_id, code, root, record.entry)
            if modules := dependency_imports.get(plugin_id):
                probe = probes / plugin_id
                probe.mkdir(exist_ok=True)
                (probe / "probe.py").write_text(
                    f"import {modules}\nclass Probe: pass\n", encoding="utf-8",
                )
                try:
                    dependencies._validate_entry(plugin_id, probe, root, "probe:Probe")
                except PluginDependencyError as error:
                    raise AssertionError(f"{scenario}: {plugin_id}: {error.detail}") from error
        assert PluginDesiredStateStore(user).read() == desired
        assert saved_data.read_bytes() == b'{"keep":"user resource"}\n'
        assert migrate_bundled_plugins(roots) == {}
        print(f"staged offline migration passed: {scenario}", flush=True)

    # Normal user-triggered installation still needs uv. Exercise the actual
    # installer with the packaged binary, no PATH or Python-module fallback.
    installing = True
    plugin = local_dependency_plugin(work)
    installed = LocalPluginInstaller(RuntimeRoots(stage, work / "uv-user")).install(plugin, "folder")
    assert installed.plugin_id == "com.sakura.release.uv-probe"
    assert len(uv_commands) == 1 and uv_commands[0][1:3] == ["pip", "install"], uv_commands
    print(f"staged dependency installation passed: {bundled_uv}", flush=True)
    if historical_distribution is not None:
        version = (historical_distribution / "VERSION").read_text(encoding="utf-8").strip()
        print(f"historical upgrade passed: {version} -> {(stage / 'VERSION').read_text().strip()} "
              f"({upgrade_mode}, {historical_distribution})", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--historical-distribution", type=Path)
    parser.add_argument("--upgrade-mode", choices=("overlay", "replacement"), default="overlay")
    args = parser.parse_args()
    os.environ.update(PATH="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                      UV_OFFLINE="1", UV_NO_CACHE="1", UV_NO_INDEX="1")
    with tempfile.TemporaryDirectory(prefix="sakura-upgrade-acceptance-") as temporary:
        run(args.stage.resolve(), Path(temporary), args.historical_distribution,
            upgrade_mode=args.upgrade_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
