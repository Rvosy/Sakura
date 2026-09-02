from __future__ import annotations

import base64
import hashlib
import io
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
from scripts import runtime_v2_archive
from tools import development_plugin_dependencies
from tools.release.package_optional_plugin import build as build_optional_plugin
from tools.release import prepare_python_runtime
from tools.release.stage_distribution import (
    copy_tree,
    forbidden_paths,
    move_tools,
    prune_non_runtime_files,
    validate_layout,
    write_windows_pth,
)
from tools.release.tauri_release_config import build_config
from tools.release.updater_manifest import build_manifest
from tools.release.verify_updater_signature import UpdaterSignatureError, verify


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_archive_reuses_only_a_verified_existing_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"frozen-runtime"
    archive = tmp_path / "python-runtime.zip"
    archive.write_bytes(content)
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "archive": {
                    "fileName": archive.name,
                    "url": "https://example.test/python-runtime.zip",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        runtime_v2_archive.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("verified cache must not use the network"),
    )

    assert runtime_v2_archive.download_and_verify(manifest, archive) == (
        len(content),
        hashlib.sha256(content).hexdigest(),
    )

    archive.write_bytes(b"corrupt")
    with pytest.raises(runtime_v2_archive.ArchiveVerificationError, match="size mismatch"):
        runtime_v2_archive.download_and_verify(manifest, archive)


def test_runtime_archive_retries_transient_download_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"frozen-runtime"
    archive = tmp_path / "python-runtime.zip"
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "archive": {
                    "fileName": archive.name,
                    "url": "https://example.test/python-runtime.zip",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    attempts = 0
    delays: list[int] = []

    def fake_urlopen(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("temporary download failure")
        return io.BytesIO(content)

    monkeypatch.setattr(runtime_v2_archive.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runtime_v2_archive.time, "sleep", delays.append)

    assert runtime_v2_archive.download_and_verify(manifest, archive) == (
        len(content),
        hashlib.sha256(content).hexdigest(),
    )
    assert attempts == 3
    assert delays == [5, 5]


def test_release_dependency_install_allows_the_callers_pip_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_root = tmp_path / "python"
    python_root.mkdir()
    (python_root / "python.exe").write_bytes(b"python")
    lock = tmp_path / "requirements.lock"
    lock.write_text("fixture==1.0 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("PIP_NO_CACHE_DIR", raising=False)
    monkeypatch.setattr(prepare_python_runtime.subprocess, "run", fake_run)

    prepare_python_runtime.prepare(python_root, "windows-x64", lock)

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "PIP_NO_CACHE_DIR" not in environment
    assert "--require-hashes" in captured["command"]


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






def test_release_identity_and_1_0x_upgrade_modes_are_frozen() -> None:
    config = json.loads(
        (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )

    assert config["productName"] == "Sakura"
    assert config["identifier"] == "com.rvosy.sakura"
    assert config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"
    assert config["plugins"]["updater"]["windows"]["installMode"] == "passive"

    release = build_config(
        target="windows-x64",
        updater=True,
        endpoint="https://example.test/latest.json",
        public_key="public-key",
    )
    assert release["plugins"]["updater"]["windows"]["installMode"] == "passive"


def test_release_overlay_contains_only_the_program_domain() -> None:
    config = build_config(target="windows-x64", updater=False, endpoint="", public_key="")
    assert config["bundle"]["resources"] == {
        "release-staging/VERSION": "VERSION",
        "release-staging/runtime-manifest.json": "runtime-manifest.json",
        "release-staging/python": "python",
        "release-staging/core": "core",
        "release-staging/plugins": "plugins",
    }
    resources = "\n".join(config["bundle"]["resources"])
    for user_domain in ("config", "data", "characters", "plugins/user", "tts"):
        assert user_domain not in resources








def test_release_overlay_does_not_install_build_inventory() -> None:
    config = build_config(target="windows-x64", updater=False, endpoint="", public_key="")
    assert "release-staging/release-inventory.json" not in config["bundle"]["resources"]


def test_python_updater_signature_verifier_matches_the_tauri_outer_base64_contract(
    tmp_path: Path,
) -> None:
    public_key = (
        "untrusted comment: minisign public key\n"
        "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3\n"
    )
    signature = (
        "untrusted comment: signature from minisign secret key\n"
        "RWQf6LRCGA9i59SLOFxz6NxvASXDJeRtuZykwQepbDEGt87ig1BNpWaVWuNrm73Y"
        "iIiJbq71Wi+dP9eKL8OC351vwIasSSbXxwA=\n"
        "trusted comment: timestamp:1555779966\tfile:test\n"
        "QtKMXWyYcwdpZAlPF7tE2ENJkRd1ujvKjlj1m9RtHTBnZPa5WKU5uWRs5GoP5M/"
        "VqE81QFuMKI5k/SfNQUaOAA==\n"
    )
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"test")
    encoded_public_key = base64.b64encode(public_key.encode()).decode()
    encoded_signature = base64.b64encode(signature.encode()).decode()

    verify(encoded_public_key, artifact, encoded_signature)
    prehashed_signature = (
        "untrusted comment: signature from minisign secret key\n"
        "RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/"
        "z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=\n"
        "trusted comment: timestamp:1556193335\tfile:test\n"
        "y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1"
        "FkZZSNCisQbuQY+bHwhEBg==\n"
    )
    encoded_prehashed_signature = base64.b64encode(prehashed_signature.encode()).decode()
    verify(encoded_public_key, artifact, encoded_prehashed_signature)

    artifact.write_bytes(b"changed")
    with pytest.raises(UpdaterSignatureError, match="UPDATER_SIGNATURE_VERIFICATION_FAILED"):
        verify(encoded_public_key, artifact, encoded_prehashed_signature)


def test_portable_1_0x_overlay_preserves_every_user_domain_byte_for_byte(
    tmp_path: Path,
) -> None:
    install = tmp_path / "Sakura-portable"
    external_tts = tmp_path / "external-tts"
    external_tts.mkdir()
    (external_tts / "voice.bin").write_bytes(b"external-voice-v1")

    user_payloads = {
        "config/ui.json": b'{"settings":{"first_run_guide_completed":true}}',
        "config/storage.json": json.dumps({"ttsRoot": str(external_tts)}).encode(),
        "data/upgrade-marker.bin": b"runtime-user-data-v1",
        "characters/fixture/character.yaml": b"id: fixture\n",
        "plugins/user/example/plugin.yaml": b"id: com.example.user\n",
        "tts/default/model.bin": b"default-voice-v1",
    }
    for relative, content in user_payloads.items():
        path = install / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    old_program = {
        "VERSION": b"1.0.0\n",
        "runtime-manifest.json": b'{"productVersion":"1.0.0"}',
        "sakura.exe": b"shell-1.0.0",
        "python/runtime.bin": b"python-1.0.0",
        "core/app.bin": b"core-1.0.0",
        "plugins/builtin/current/plugin.yaml": b"version: 1.0.0\n",
        "plugins/dependencies/current/module.bin": b"dependency-1.0.0",
        "portable.flag": b"",
    }
    for relative, content in old_program.items():
        path = install / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    archive = tmp_path / "Sakura-1.0.1-windows-x64-portable.zip"
    new_program = {
        "VERSION": b"1.0.1\n",
        "runtime-manifest.json": b'{"productVersion":"1.0.1"}',
        "sakura.exe": b"shell-1.0.1",
        "python/runtime.bin": b"python-1.0.1",
        "core/app.bin": b"core-1.0.1",
        "plugins/builtin/current/plugin.yaml": b"version: 1.0.1\n",
        "plugins/dependencies/current/module.bin": b"dependency-1.0.1",
        "portable.flag": b"",
    }
    with zipfile.ZipFile(archive, "w") as package:
        for relative, content in new_program.items():
            package.writestr(relative, content)

    user_hashes = {
        relative: hashlib.sha256((install / relative).read_bytes()).hexdigest()
        for relative in user_payloads
    }
    external_tts_hash = hashlib.sha256((external_tts / "voice.bin").read_bytes()).hexdigest()

    with zipfile.ZipFile(archive) as package:
        members = {name.rstrip("/") for name in package.namelist() if name.rstrip("/")}
        assert all(
            member in {"VERSION", "runtime-manifest.json", "sakura.exe", "portable.flag"}
            or member.startswith(("python/", "core/", "plugins/builtin/", "plugins/dependencies/"))
            for member in members
        )
        package.extractall(install)

    assert {
        relative: hashlib.sha256((install / relative).read_bytes()).hexdigest()
        for relative in user_payloads
    } == user_hashes
    assert hashlib.sha256((external_tts / "voice.bin").read_bytes()).hexdigest() == external_tts_hash
    assert json.loads((install / "config/ui.json").read_text(encoding="utf-8"))["settings"][
        "first_run_guide_completed"
    ] is True
    for relative, content in new_program.items():
        assert (install / relative).read_bytes() == content


def test_macos_1_0x_app_replacement_is_disjoint_from_user_and_external_tts(
    tmp_path: Path,
) -> None:
    applications = tmp_path / "Applications"
    installed_app = applications / "Sakura.app"
    installed_resources = installed_app / "Contents/Resources"
    installed_resources.mkdir(parents=True)
    (installed_resources / "VERSION").write_text("1.0.0\n", encoding="utf-8")

    user_root = tmp_path / "Library/Application Support/Sakura"
    external_tts = tmp_path / "Volumes/Voice"
    user_files = {
        user_root / "config/ui.json": b'{"settings":{"first_run_guide_completed":true}}',
        user_root / "data/marker.bin": b"mac-user-data-v1",
        user_root / "characters/fixture/character.yaml": b"id: fixture\n",
        user_root / "plugins/user/example/plugin.yaml": b"id: com.example.user\n",
        user_root / "tts/default/model.bin": b"default-tts-v1",
        external_tts / "model.bin": b"external-tts-v1",
    }
    for path, content in user_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in user_files}

    replacement_app = tmp_path / "Sakura-1.0.1.app"
    replacement_resources = replacement_app / "Contents/Resources"
    replacement_resources.mkdir(parents=True)
    (replacement_resources / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    retired_app = tmp_path / "Sakura-1.0.0.retired.app"
    installed_app.rename(retired_app)
    replacement_app.rename(installed_app)

    assert (installed_app / "Contents/Resources/VERSION").read_text(encoding="utf-8") == "1.0.1\n"
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in user_files} == hashes
    assert json.loads((user_root / "config/ui.json").read_text(encoding="utf-8"))["settings"][
        "first_run_guide_completed"
    ] is True


def _minimal_stage(root: Path, target: str) -> Path:
    stage = root / "stage"
    (stage / "core/app/core_host").mkdir(parents=True)
    (stage / "core/app/core_host/__main__.py").write_text("", encoding="utf-8")
    (stage / "core/app/legacy_import").mkdir(parents=True)
    (stage / "core/app/legacy_import/__main__.py").write_text("", encoding="utf-8")
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




def test_optional_plugin_release_zip_keeps_one_installable_root(tmp_path: Path) -> None:
    output = tmp_path / "playwright.sakplugin.zip"
    build_optional_plugin(ROOT / "plugins/optional/playwright_browser", output)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "playwright_browser/plugin.yaml" in names
    assert "playwright_browser/requirements.txt" in names
    assert not any("__pycache__" in name for name in names)




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
    dependency_root = stage / "plugins/dependencies/sakura.memory.mem0"
    (dependency_root / "dependency-path.pth").write_text(
        "import dependency_bootstrap\n",
        encoding="utf-8",
    )
    validate_layout(stage, "windows-x64", portable=False)

    for weights in (
        packages / "example/model.pth",
        dependency_root / "example/model.pth",
    ):
        weights.parent.mkdir(exist_ok=True)
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


def test_distribution_prunes_dependency_tests_and_generated_console_scripts(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    site = stage / "python/Lib/site-packages"
    dependency = stage / "plugins/dependencies/sakura.memory.mem0"
    (site / "runtime_package").mkdir(parents=True)
    (site / "runtime_package/module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site / "runtime_package/tests").mkdir()
    (site / "runtime_package/tests/test_module.py").write_text("", encoding="utf-8")
    (site / "runtime_package/__pycache__").mkdir()
    (site / "runtime_package/__pycache__/module.pyc").write_bytes(b"cache")
    (stage / "python/Scripts").mkdir(parents=True)
    (stage / "python/Scripts/example.exe").write_bytes(b"launcher")
    (dependency / "bin").mkdir(parents=True)
    (dependency / "bin/example.exe").write_bytes(b"launcher")
    (dependency / "py7zz/bin").mkdir(parents=True)
    (dependency / "py7zz/bin/7zz.exe").write_bytes(b"runtime")

    prune_non_runtime_files(stage, "windows-x64")

    assert (site / "runtime_package/module.py").is_file()
    assert not (site / "runtime_package/tests").exists()
    assert not (site / "runtime_package/__pycache__").exists()
    assert not (stage / "python/Scripts").exists()
    assert not (dependency / "bin").exists()
    assert (dependency / "py7zz/bin/7zz.exe").is_file()


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

    client_only = build_config(
        target="windows-x64",
        updater=True,
        updater_artifacts=False,
        endpoint="https://example.test/latest.json",
        public_key="public-key",
    )
    assert client_only["plugins"]["updater"]["endpoints"] == [
        "https://example.test/latest.json"
    ]
    assert client_only["bundle"]["createUpdaterArtifacts"] is False


def test_static_updater_manifest_requires_both_signed_platforms(tmp_path: Path) -> None:
    releases = []
    for target, name in (("windows-x64", "Sakura-setup.exe"), ("macos-arm64", "Sakura.app.tar.gz")):
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


def test_static_updater_manifest_allows_explicit_platform_only_test(tmp_path: Path) -> None:
    artifact = tmp_path / "Sakura-setup.exe"
    signature = tmp_path / "Sakura-setup.exe.sig"
    artifact.write_bytes(b"signed-installer")
    signature.write_text("signature-windows\n", encoding="utf-8")

    manifest = build_manifest(
        version="1.0.1",
        notes="Windows updater test",
        base_url="https://example.test/downloads",
        releases=[("windows-x64", artifact, signature)],
        portable=None,
        pub_date="2026-08-30T00:00:00Z",
        require_all_platforms=False,
    )

    assert set(manifest["platforms"]) == {"windows-x86_64"}
    assert manifest["platforms"]["windows-x86_64"]["url"].endswith("Sakura-setup.exe")
    assert "portable" not in manifest
