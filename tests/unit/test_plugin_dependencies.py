from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots


def test_entry_import_timeout_is_not_reported_as_a_broken_entry(tmp_path, monkeypatch):
    def slow_import(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", slow_import)
    with pytest.raises(PluginDependencyError, match="PLUGIN_ENTRY_IMPORT_TIMEOUT"):
        PluginDependencyRoots(tmp_path / "user")._validate_entry(
            "fixture", tmp_path, None, "plugin:Plugin")


@pytest.mark.skipif(os.name != "nt", reason="Windows verbatim path semantics")
@pytest.mark.parametrize("probe", ["working_directory", "relative_native_resource"])
def test_entry_validation_accepts_windows_verbatim_roots(tmp_path: Path, probe: str) -> None:
    code = tmp_path / "plugin"
    code.mkdir()
    (code / "plugin.py").write_text("class Plugin: pass\n", encoding="utf-8")
    dependency = tmp_path / "dependencies"
    module = dependency / "probe"
    module.mkdir(parents=True)
    if probe == "working_directory":
        source = '''import os
assert not os.getcwd().startswith("\\\\\\\\?\\\\"), "VERBATIM_WORKING_DIRECTORY"
'''
    else:
        (dependency / "native.bin").write_bytes(b"native resource")
        source = '''import os
with open(os.path.join(os.path.dirname(__file__), "..", "native.bin"), "rb") as stream:
    assert stream.read() == b"native resource"
'''
    (module / "__init__.py").write_text(source, encoding="utf-8")
    PluginDependencyRoots(tmp_path / "user")._validate_entry(
        "fixture", Path("\\\\?\\" + str(code)), Path("\\\\?\\" + str(dependency)),
        "plugin:Plugin", runtime_imports=["probe"],
    )


def test_entry_validation_does_not_write_bytecode(tmp_path: Path) -> None:
    code = tmp_path / "plugin"
    code.mkdir()
    (code / "plugin.py").write_text("class Plugin: pass\n", encoding="utf-8")
    dependency = tmp_path / "dependencies"
    dependency.mkdir()
    (dependency / "probe.py").write_text("value = 1\n", encoding="utf-8")
    PluginDependencyRoots(tmp_path / "user")._validate_entry(
        "fixture", code, dependency, "plugin:Plugin", runtime_imports=["probe"],
    )
    assert not list(code.rglob("*.pyc"))
    assert not list(dependency.rglob("*.pyc"))


def test_dependency_verification_accepts_legacy_line_endings_without_reinstall(
    tmp_path: Path,
) -> None:
    plugin_id = "fixture.asr"
    plugin_root = tmp_path / "distribution/plugins/builtin" / plugin_id
    plugin_root.mkdir(parents=True)
    dependency_root = tmp_path / "distribution/plugins/dependencies" / plugin_id
    dependency_root.mkdir(parents=True)
    current = b"sherpa-onnx==1.12.36\r\nnumpy==2.2.6\r\n"
    declaration_path = plugin_root / "requirements.txt"
    declaration_path.write_bytes(current)
    marker = {
        "schemaVersion": 1,
        "kind": "requirements.txt",
        "fingerprint": "old-unchecked-value",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    marker_path = dependency_root / ".sakura-dependencies.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = marker_path.read_bytes()
    roots = PluginDependencyRoots(tmp_path / "user", distribution_root=tmp_path / "distribution")

    assert roots.verified_root(plugin_id, plugin_root, source="bundled") == dependency_root
    assert marker_path.read_bytes() == before
    declaration_path.write_bytes(current.replace(b"1.12.36", b"1.12.37").replace(b"\r\n", b"\n"))
    assert roots.verified_root(plugin_id, plugin_root, source="bundled") == dependency_root

    marker["python"] = "0.0"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(PluginDependencyError, match="PLUGIN_DEPENDENCIES_STALE"):
        roots.verified_root(plugin_id, plugin_root, source="bundled")

    marker_path.unlink()
    with pytest.raises(PluginDependencyError, match="PLUGIN_DEPENDENCIES_MISSING"):
        roots.verified_root(plugin_id, plugin_root, source="bundled")


@pytest.mark.parametrize("kind", ["requirements.txt", "pyproject.toml", "uv.lock"])
def test_explicit_install_produces_a_reusable_environment(tmp_path: Path, monkeypatch, kind: str) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    if kind == "requirements.txt":
        (plugin_root / kind).write_text("numpy\n", encoding="utf-8")
    else:
        (plugin_root / "pyproject.toml").write_text('[project]\ndependencies = ["numpy"]\n', encoding="utf-8")
        if kind == "uv.lock":
            (plugin_root / kind).write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr("app.plugins.dependencies.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    roots = PluginDependencyRoots(tmp_path / "user")
    installed = roots.install("fixture", plugin_root)
    assert installed is not None
    assert roots.verified_root("fixture", plugin_root) == installed


def test_dependency_declaration_format_change_keeps_installed_imports(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.py").write_text("from fixture_dependency import value\nclass Plugin: pass\n", encoding="utf-8")
    (plugin / "requirements.lock").write_text("fixture-dependency==1\n", encoding="utf-8")
    dependency = tmp_path / "user/data/plugin-runtime/dependencies/fixture"
    dependency.mkdir(parents=True)
    (dependency / "fixture_dependency.py").write_text("value = 1\n", encoding="utf-8")
    marker = dependency / ".sakura-dependencies.json"
    marker.write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt", "fingerprint": "ignored",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }), encoding="utf-8")
    before = marker.read_bytes()
    roots = PluginDependencyRoots(tmp_path / "user")

    installed = roots.verified_root("fixture", plugin)
    roots._validate_entry("fixture", plugin, installed, "plugin:Plugin")

    assert installed == dependency
    assert marker.read_bytes() == before
