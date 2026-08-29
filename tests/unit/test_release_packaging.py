from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from app.plugins.inventory import PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeManager
from app.storage.runtime_roots import RuntimeRoots
from tools import development_plugin_dependencies
from tools.release.artifact_report import build_report
from tools.release.package_optional_plugin import build as build_optional_plugin
from tools.release.stage_distribution import (
    copy_tree,
    forbidden_paths,
    move_tools,
    validate_layout,
    write_windows_pth,
)
from tools.release.tauri_release_config import build_config
from tools.release.updater_manifest import build_manifest
from tools.release.versioning import projected_versions, source_version


ROOT = Path(__file__).resolve().parents[2]


def _development_dependency_repo(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    python = (
        repo / "runtime/python.exe"
        if os.name == "nt"
        else repo / "runtime/bin/python3"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    uv = (
        python.parent / "Scripts/uv.exe"
        if os.name == "nt"
        else python.with_name("uv")
    )
    uv.parent.mkdir(parents=True, exist_ok=True)
    uv.write_bytes(b"uv")
    (repo / "app/plugins").mkdir(parents=True)
    (repo / "app/plugins/plugin_runner_v4.py").write_text("", encoding="utf-8")
    for directory_name in development_plugin_dependencies.PLUGIN_DIRECTORIES:
        plugin_id = f"com.example.{directory_name}"
        plugin = repo / "plugins/builtin" / directory_name
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            f"api: 4\nid: {plugin_id}\nentry: plugin:Plugin\n",
            encoding="utf-8",
        )
        (plugin / "requirements.txt").write_text("fixture==1.0\n", encoding="utf-8")
    return repo, python


def _fake_dependency_install(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
    if "--target" in command:
        dependency_root = Path(command[command.index("--target") + 1])
        (dependency_root / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    return subprocess.CompletedProcess(command, 0)


def test_development_dependency_build_replaces_all_roots_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, python = _development_dependency_repo(tmp_path)
    previous = repo / "plugins/dependencies/old"
    previous.mkdir(parents=True)
    (previous / "keep.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(development_plugin_dependencies.subprocess, "run", _fake_dependency_install)

    development_plugin_dependencies.prepare(repo, python)

    dependency_parent = repo / "plugins/dependencies"
    expected_ids = {
        f"com.example.{directory_name}"
        for directory_name in development_plugin_dependencies.PLUGIN_DIRECTORIES
    }
    assert {path.name for path in dependency_parent.iterdir()} == expected_ids
    for plugin_id in expected_ids:
        root = dependency_parent / plugin_id
        assert (root / "fixture.py").is_file()
        marker = json.loads((root / ".sakura-dependencies.json").read_text(encoding="utf-8"))
        assert marker["schemaVersion"] == 1
        assert marker["kind"] == "requirements.txt"
    assert not list((repo / "plugins").glob(".dependencies-*"))


def test_development_dependency_publish_failure_restores_previous_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, python = _development_dependency_repo(tmp_path)
    previous = repo / "plugins/dependencies"
    previous.mkdir(parents=True)
    (previous / "keep.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(development_plugin_dependencies.subprocess, "run", _fake_dependency_install)
    real_replace = os.replace

    def fail_publish(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        if source_path.name.startswith(".dependencies-build-"):
            raise OSError("publish failed")
        real_replace(source, target)

    monkeypatch.setattr(development_plugin_dependencies.os, "replace", fail_publish)

    with pytest.raises(OSError, match="publish failed"):
        development_plugin_dependencies.prepare(repo, python)

    assert (previous / "keep.txt").read_text(encoding="utf-8") == "old"
    assert not list((repo / "plugins").glob(".dependencies-*"))


def test_version_is_the_only_release_version_source() -> None:
    version = source_version(ROOT)
    assert set(projected_versions(ROOT).values()) == {version}


def test_tauri_bundle_uses_the_runtime_shell_binary() -> None:
    cargo = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    assert 'default-run = "sakura-runtime-v2-shell"' in cargo


def test_base_tauri_config_keeps_unsigned_and_development_updater_config_valid() -> None:
    config = json.loads(
        (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    assert config["plugins"]["updater"] == {
        "endpoints": [],
        "pubkey": "",
        "windows": {"installMode": "passive"},
    }


def test_package_and_release_use_the_current_tauri_cli() -> None:
    command = "npx --yes @tauri-apps/cli@2.11.4 build --config tauri.release.json"
    for workflow in ("package.yml", "release.yml"):
        document = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        assert document.count(command) == 1


def _minimal_stage(root: Path, target: str) -> Path:
    stage = root / "stage"
    (stage / "core/app/core_host").mkdir(parents=True)
    (stage / "core/app/core_host/__main__.py").write_text("", encoding="utf-8")
    (stage / "plugins/builtin").mkdir(parents=True)
    (stage / "plugins/builtin/__init__.py").write_text("", encoding="utf-8")
    plugin_ids = {
        "sakura_mem0": "sakura.memory.mem0",
        "sakura_mobile": "sakura.mobile",
        "sakura_tts_hub": "sakura.tts",
        "sakura_genie": "sakura.tts.genie",
        "sakura_gpt_sovits": "sakura.tts.gpt-sovits",
    }
    dependency_plugins = {"sakura_mem0", "sakura_genie", "sakura_gpt_sovits"}
    for plugin, plugin_id in plugin_ids.items():
        directory = stage / "plugins/builtin" / plugin
        directory.mkdir()
        (directory / "plugin.yaml").write_text(
            f"api: 4\nid: {plugin_id}\n",
            encoding="utf-8",
        )
        if plugin in dependency_plugins:
            requirements = directory / "requirements.txt"
            requirements.write_text("fixture==1.0\n", encoding="utf-8")
            dependency = stage / "plugins/dependencies" / plugin_id
            dependency.mkdir(parents=True)
            (dependency / ".sakura-dependencies.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "requirements.txt",
                        "fingerprint": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                        "python": "3.12",
                    }
                ),
                encoding="utf-8",
            )
    if target == "windows-x64":
        executable = stage / "python/python.exe"
        packages = stage / "python/Lib/site-packages"
    else:
        executable = stage / "python/bin/python3"
        packages = stage / "python/lib/python3.12/site-packages"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"python")
    packages.mkdir(parents=True)
    (packages / "PyYAML-6.0.2.dist-info").mkdir()
    tools = stage / "python/tools"
    tools.mkdir(parents=True)
    suffix = ".exe" if target == "windows-x64" else ""
    for name in ("uv", "uvx", "7zz"):
        (tools / f"{name}{suffix}").write_bytes(b"tool")
    (stage / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (stage / "runtime-manifest.json").write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    return stage


def test_distribution_validator_accepts_only_the_five_api4_builtins(tmp_path: Path) -> None:
    stage = _minimal_stage(tmp_path, "macos-arm64")
    validate_layout(stage, "macos-arm64", portable=False)
    extra = stage / "plugins/builtin/extra"
    extra.mkdir()
    (extra / "plugin.yaml").write_text("api: 4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="STAGING_PLUGIN_SET_INVALID"):
        validate_layout(stage, "macos-arm64", portable=False)


def test_bundled_dependency_roots_use_manifest_ids_through_runtime_start(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    user.mkdir()
    expected_ids: set[str] = set()
    for directory_name in development_plugin_dependencies.PLUGIN_DIRECTORIES:
        source = ROOT / "plugins/builtin" / directory_name
        plugin_root = distribution / "plugins/builtin" / directory_name
        plugin_root.mkdir(parents=True)
        manifest = yaml.safe_load((source / "plugin.yaml").read_text(encoding="utf-8"))
        assert isinstance(manifest, dict)
        plugin_id = manifest["id"]
        assert isinstance(plugin_id, str)
        expected_ids.add(plugin_id)
        manifest.update(entry="fixture:Plugin", provides=[], requires=[])
        (plugin_root / "plugin.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (plugin_root / "fixture.py").write_text(
            "class Plugin:\n    def setup(self, context):\n        return None\n",
            encoding="utf-8",
        )
        requirements = (source / "requirements.txt").read_bytes()
        (plugin_root / "requirements.txt").write_bytes(requirements)
        dependency_root = distribution / "plugins/dependencies" / plugin_id
        dependency_root.mkdir(parents=True)
        (dependency_root / ".sakura-dependencies.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "kind": "requirements.txt",
                "fingerprint": hashlib.sha256(requirements).hexdigest(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }),
            encoding="utf-8",
        )

    roots = RuntimeRoots(distribution, user)
    manager = PluginRuntimeManager(
        roots,
        "release-manifest-id-regression",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        snapshot = manager.start()
        records = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert set(records) == expected_ids
        assert all(item["state"] == "active" for item in records.values())
        assert not (user / "data/plugin-runtime/dependencies").exists()
    finally:
        manager.close()


def test_playwright_is_an_installable_api4_optional_plugin_not_a_builtin() -> None:
    assert not (ROOT / "plugins/builtin/playwright_browser").exists()
    optional = ROOT / "plugins/optional/playwright_browser"
    manifest = (optional / "plugin.yaml").read_text(encoding="utf-8")
    assert "api: 4" in manifest.splitlines()
    assert (optional / "requirements.txt").read_text(encoding="utf-8").strip().startswith(
        "playwright"
    )


def test_optional_plugin_release_zip_keeps_one_installable_root(tmp_path: Path) -> None:
    output = tmp_path / "playwright.sakplugin.zip"
    build_optional_plugin(ROOT / "plugins/optional/playwright_browser", output)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "playwright_browser/plugin.yaml" in names
    assert "playwright_browser/requirements.txt" in names
    assert not any("__pycache__" in name for name in names)


def test_core_requirements_do_not_directly_own_plugin_distributions() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
    for distribution in (
        "playwright",
        "openai",
        "qdrant-client",
        "sqlalchemy",
        "posthog",
        "pytz",
        "protobuf",
        "fastembed",
        "onnxruntime",
        "py7zr",
    ):
        assert distribution not in requirements
    dev = (ROOT / "tools/requirements-dev.txt").read_text(encoding="utf-8")
    assert "plugins/builtin/sakura_mem0/requirements.txt" in dev
    assert "plugins/optional/playwright_browser/requirements.txt" in dev


def test_distribution_validator_rejects_user_data_and_heavy_optional_payloads(tmp_path: Path) -> None:
    stage = _minimal_stage(tmp_path, "macos-arm64")
    (stage / "data").mkdir()
    with pytest.raises(ValueError, match="STAGING_CONTAINS_USER_DATA"):
        validate_layout(stage, "macos-arm64", portable=False)
    (stage / "data").rmdir()
    model = stage / "python/lib/python3.12/site-packages/model.safetensors"
    model.write_bytes(b"model")
    assert model.relative_to(stage).as_posix() in forbidden_paths(stage)


def test_distribution_validator_rejects_legacy_third_party_core(tmp_path: Path) -> None:
    stage = _minimal_stage(tmp_path, "macos-arm64")
    (stage / "core/third_party/mem0").mkdir(parents=True)

    with pytest.raises(ValueError, match="STAGING_CONTAINS_LEGACY_THIRD_PARTY"):
        validate_layout(stage, "macos-arm64", portable=False)


def test_distribution_validator_allows_python_pth_but_rejects_weights_and_model_caches(
    tmp_path: Path,
) -> None:
    stage = _minimal_stage(tmp_path, "windows-x64")
    packages = stage / "python/Lib/site-packages"
    (packages / "pywin32.pth").write_text("win32\n", encoding="utf-8")
    (packages / "distutils-precedence.pth").write_text(
        "import _distutils_hack\n",
        encoding="utf-8",
    )
    validate_layout(stage, "windows-x64", portable=False)

    weights = packages / "example/model.pth"
    weights.parent.mkdir()
    weights.write_bytes(b"model")
    assert weights.relative_to(stage).as_posix() in forbidden_paths(stage)

    cache = stage / "python/fastembed-cache/model.onnx"
    cache.parent.mkdir()
    cache.write_bytes(b"model")
    assert cache.relative_to(stage).as_posix() in forbidden_paths(stage)


def test_distribution_copy_excludes_forbidden_runtime_caches(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "lib/python3.12/site-packages").mkdir(parents=True)
    (source / "lib/python3.12/site-packages/core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "fastembed-cache").mkdir()
    (source / "fastembed-cache/model.onnx").write_bytes(b"model")
    (source / "hf-cache").mkdir()
    (source / "hf-cache/model.bin").write_bytes(b"model")

    target = tmp_path / "target"
    copy_tree(source, target)

    assert (target / "lib/python3.12/site-packages/core.py").is_file()
    assert not (target / "fastembed-cache").exists()
    assert not (target / "hf-cache").exists()


def test_windows_pth_is_exact_and_keeps_native_site_packages(tmp_path: Path) -> None:
    python_root = tmp_path / "python"
    python_root.mkdir()
    write_windows_pth(python_root)
    assert (python_root / "python312._pth").read_text(encoding="utf-8") == (
        "python312.zip\n.\nLib/site-packages\nimport site\n"
    )


def test_tool_staging_uses_native_7zz_instead_of_build_machine_console_script(tmp_path: Path) -> None:
    python_root = tmp_path / "python"
    (python_root / "bin").mkdir(parents=True)
    (python_root / "lib/python3.12/site-packages/py7zz/bin").mkdir(parents=True)
    (python_root / "bin/uv").write_bytes(b"uv")
    (python_root / "bin/uvx").write_bytes(b"uvx")
    (python_root / "bin/py7zz").write_bytes(b"#!/temporary/python\n")
    native = python_root / "lib/python3.12/site-packages/py7zz/bin/7zz"
    native.write_bytes(b"native-7zz")
    move_tools(python_root, "macos-arm64")
    assert (python_root / "tools/7zz").read_bytes() == b"native-7zz"
    assert (python_root / "bin/py7zz").read_bytes().startswith(b"#!/temporary")


def test_updater_overlay_requires_https_endpoint_and_public_key() -> None:
    development = build_config(target="macos-arm64", updater=False, endpoint="", public_key="")
    assert development["bundle"]["createUpdaterArtifacts"] is False
    assert development["bundle"]["targets"] == ["app", "dmg"]
    with pytest.raises(ValueError, match="UPDATER_RELEASE_CONFIGURATION_MISSING"):
        build_config(
            target="windows-x64", updater=True, endpoint="http://example.test/latest.json", public_key="key"
        )
    release = build_config(
        target="windows-x64",
        updater=True,
        endpoint="https://example.test/latest.json",
        public_key="public-key",
        windows_certificate_thumbprint="AABBCC",
    )
    assert release["plugins"]["updater"]["pubkey"] == "public-key"
    assert release["bundle"]["targets"] == ["nsis"]
    assert release["bundle"]["windows"]["certificateThumbprint"] == "AABBCC"


def test_artifact_report_keeps_staged_and_compressed_evidence(tmp_path: Path) -> None:
    inventory = tmp_path / "release-inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "target": "windows-x64",
                "version": "1.0.0",
                "uncompressedBytes": 30,
                "topLevelBytes": {"python": 20, "core": 10},
            }
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "Sakura.zip"
    artifact.write_bytes(b"zip")
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "file").write_bytes(b"12345")
    report = build_report(inventory, [artifact], [installed])
    assert report["largestTopLevelDirectory"] == {"name": "python", "bytes": 20}
    assert report["installedBytes"] == 5
    assert report["artifacts"][0]["bytes"] == 3


def test_static_updater_manifest_requires_both_signed_platforms(tmp_path: Path) -> None:
    releases = []
    for target, name in (("windows-x64", "Sakura.nsis.zip"), ("macos-arm64", "Sakura.app.tar.gz")):
        artifact = tmp_path / name
        signature = tmp_path / f"{name}.sig"
        artifact.write_bytes(b"artifact")
        signature.write_text(f"signature-{target}\n", encoding="utf-8")
        releases.append((target, artifact, signature))
    portable = tmp_path / "Sakura-portable.zip"
    portable.write_bytes(b"portable")
    manifest = build_manifest(
        version="1.0.0",
        notes="release",
        base_url="https://example.test/downloads",
        releases=releases,
        portable=portable,
        pub_date="2026-08-26T00:00:00Z",
    )
    assert set(manifest["platforms"]) == {"windows-x86_64", "darwin-aarch64"}
    assert manifest["platforms"]["darwin-aarch64"]["url"].endswith("Sakura.app.tar.gz")
    assert manifest["portable"]["windows-x86_64"]["url"].endswith("Sakura-portable.zip")
    assert len(manifest["portable"]["windows-x86_64"]["sha256"]) == 64
