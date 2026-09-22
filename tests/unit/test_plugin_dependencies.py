from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots


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


@pytest.mark.parametrize("kind", ["requirements.txt", "requirements.lock"])
@pytest.mark.parametrize("current_newline", [b"\n", b"\r\n"])
def test_dependency_verification_accepts_legacy_line_endings_without_reinstall(
    tmp_path: Path, kind: str, current_newline: bytes,
) -> None:
    plugin_id = "fixture.asr"
    plugin_root = tmp_path / "distribution/plugins/builtin" / plugin_id
    plugin_root.mkdir(parents=True)
    dependency_root = tmp_path / "distribution/plugins/dependencies" / plugin_id
    dependency_root.mkdir(parents=True)
    current = current_newline.join([b"sherpa-onnx==1.12.36", b"numpy==2.2.6", b""])
    declaration_path = plugin_root / kind
    declaration_path.write_bytes(current)
    marker = {
        "schemaVersion": 1,
        "kind": kind,
        "fingerprint": "old-unchecked-value",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    marker_path = dependency_root / ".sakura-dependencies.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = marker_path.read_bytes()
    roots = PluginDependencyRoots(tmp_path / "user", distribution_root=tmp_path / "distribution")

    assert roots.verified_root(plugin_id, plugin_root, source="bundled") == dependency_root
    assert marker_path.read_bytes() == before
    declaration = roots.declaration(plugin_root)
    assert declaration is not None

    declaration_path.write_bytes(current.replace(b"1.12.36", b"1.12.37"))
    assert roots.verified_root(plugin_id, plugin_root, source="bundled") == dependency_root

    marker.pop("fingerprint")
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert roots.verified_root(plugin_id, plugin_root, source="bundled") == dependency_root

    declaration_path.write_bytes(current)
    marker["python"] = "0.0"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(PluginDependencyError, match="PLUGIN_DEPENDENCIES_STALE"):
        roots.verified_root(plugin_id, plugin_root, source="bundled")

    marker["python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    marker["kind"] = "other"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(PluginDependencyError, match="PLUGIN_DEPENDENCIES_STALE"):
        roots.verified_root(plugin_id, plugin_root, source="bundled")
    marker_path.unlink()
    with pytest.raises(PluginDependencyError, match="PLUGIN_DEPENDENCIES_MISSING"):
        roots.verified_root(plugin_id, plugin_root, source="bundled")


@pytest.mark.parametrize("kind", ["requirements.txt", "pyproject.toml", "uv.lock"])
def test_explicit_install_writes_marker_without_digest(tmp_path: Path, monkeypatch, kind: str) -> None:
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
    assert json.loads((installed / ".sakura-dependencies.json").read_text()) == {
        "schemaVersion": 1, "kind": kind, "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    assert roots.verified_root("fixture", plugin_root) == installed
