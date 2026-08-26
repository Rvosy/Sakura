from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.artifact_report import build_report
from tools.release.stage_distribution import forbidden_paths, move_tools, validate_layout, write_windows_pth
from tools.release.tauri_release_config import build_config
from tools.release.updater_manifest import build_manifest
from tools.release.versioning import projected_versions, source_version


ROOT = Path(__file__).resolve().parents[2]


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
    for plugin in {
        "playwright_browser", "sakura_mem0", "sakura_mobile", "sakura_tts_hub",
        "sakura_genie", "sakura_gpt_sovits",
    }:
        directory = stage / "plugins/builtin" / plugin
        directory.mkdir()
        (directory / "plugin.yaml").write_text("api_version: 3\n", encoding="utf-8")
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


def test_distribution_validator_accepts_only_the_six_builtins(tmp_path: Path) -> None:
    stage = _minimal_stage(tmp_path, "macos-arm64")
    validate_layout(stage, "macos-arm64", portable=False)
    extra = stage / "plugins/builtin/extra"
    extra.mkdir()
    (extra / "plugin.yaml").write_text("api_version: 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="STAGING_PLUGIN_SET_INVALID"):
        validate_layout(stage, "macos-arm64", portable=False)


def test_distribution_validator_rejects_user_data_and_heavy_optional_payloads(tmp_path: Path) -> None:
    stage = _minimal_stage(tmp_path, "macos-arm64")
    (stage / "data").mkdir()
    with pytest.raises(ValueError, match="STAGING_CONTAINS_USER_DATA"):
        validate_layout(stage, "macos-arm64", portable=False)
    (stage / "data").rmdir()
    model = stage / "python/lib/python3.12/site-packages/model.safetensors"
    model.write_bytes(b"model")
    assert model.relative_to(stage).as_posix() in forbidden_paths(stage)


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
