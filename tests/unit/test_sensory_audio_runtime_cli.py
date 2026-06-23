from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app.sensory import audio_runtime_cli, audio_runtime_doctor
from app.sensory.llama_cpp_runtime import LlamaCppRuntimePackageSpec
from app.storage.paths import StoragePaths


def test_audio_runtime_cli_plan_uses_managed_llama_defaults(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    binary = _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )

    code = audio_runtime_cli.main(
        [
            "--base-dir",
            str(tmp_path),
            "plan",
            "--source",
            "speech",
            "--managed-llama-defaults",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["managed_runtime"] is True
    assert payload["binary_path"] == str(binary)
    assert payload["model"] == "ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0"
    assert payload["runtime_requirement"] == "cached"
    assert payload["model_location"] == "huggingface"
    assert payload["requires_model_download"] is True
    assert payload["requires_runtime_download"] is False
    assert payload["platform_key"]


def test_audio_runtime_cli_smoke_refuses_remote_llama_model_without_explicit_allow(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _executable(tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server")

    def fail_if_called(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("smoke test should not start without download consent")

    monkeypatch.setattr(audio_runtime_cli, "run_sensory_audio_smoke_test", fail_if_called)

    code = audio_runtime_cli.main(
        [
            "--base-dir",
            str(tmp_path),
            "smoke",
            "--source",
            "speech",
            "--managed-llama-defaults",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "--allow-model-download" in payload["message"]
    assert payload["plan"]["model_download_hint"] == "约 1.0 GB"
    assert payload["plan"]["requires_model_download"] is True


def test_audio_runtime_cli_install_runtime_requires_yes_before_download(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audio_runtime_cli, "discover_llama_server_binary", lambda base_dir: "")

    def fail_if_called(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("runtime download should require --yes")

    monkeypatch.setattr(audio_runtime_cli, "fetch_llama_cpp_runtime_package_catalog", fail_if_called)

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "install-runtime"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "--yes" in payload["message"]


def test_audio_runtime_cli_install_runtime_uses_local_manifest(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audio_runtime_cli, "discover_llama_server_binary", lambda base_dir: "")
    manifest = tmp_path / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "package_id": "mirror-macos",
                        "label": "Mirror macOS",
                        "platform_key": "macos-arm64",
                        "url": "https://mirror.example/llama.tar.gz",
                        "archive_format": "tar.gz",
                        "binary_relpath": "llama-server",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_install(base_dir, package, *, timeout_seconds):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            to_mapping=lambda: {
                "package": package.to_mapping(),
                "install_dir": str(tmp_path / "runtime"),
                "binary_path": str(tmp_path / "runtime" / "llama-server"),
                "already_installed": False,
                "message": "installed",
            }
        )

    monkeypatch.setattr(audio_runtime_cli, "install_llama_cpp_runtime_package", fake_install)

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "install-runtime", "--yes"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["package"]["package_id"] == "mirror-macos"
    assert payload["package_source"] == f"manifest:{manifest}"


def test_audio_runtime_cli_install_runtime_reuses_existing_binary(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    binary = str(tmp_path / "llama-server")
    monkeypatch.setattr(audio_runtime_cli, "discover_llama_server_binary", lambda base_dir: binary)

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "install-runtime"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["already_installed"] is True
    assert payload["binary_path"] == binary


def test_audio_runtime_cli_runtime_manifest_generates_relative_archive_urls(
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        audio_runtime_cli,
        "fetch_latest_llama_cpp_runtime_packages",
        lambda timeout_seconds: [
            LlamaCppRuntimePackageSpec(
                package_id="b1-linux",
                label="Linux",
                platform_key="linux-x64",
                url="https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-ubuntu-x64.tar.gz",
                archive_format="tar.gz",
                binary_relpath="llama-server",
                version="b1",
                variant="cpu",
            ),
            LlamaCppRuntimePackageSpec(
                package_id="b1-macos",
                label="macOS",
                platform_key="macos-arm64",
                url="https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-macos-arm64.tar.gz",
                archive_format="tar.gz",
                binary_relpath="llama-server",
                version="b1",
                variant="metal",
            ),
        ],
    )

    code = audio_runtime_cli.main(["runtime-manifest", "--relative-archive-dir", "archives"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["manifest_version"] == 1
    assert [package["platform_key"] for package in payload["packages"]] == ["linux-x64", "macos-arm64"]
    assert payload["packages"][0]["url"] == "archives/llama-b1-bin-ubuntu-x64.tar.gz"
    assert payload["packages"][1]["url"] == "archives/llama-b1-bin-macos-arm64.tar.gz"


def test_audio_runtime_cli_runtime_manifest_can_write_mirror_manifest(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        audio_runtime_cli,
        "fetch_latest_llama_cpp_runtime_packages",
        lambda timeout_seconds: [
            LlamaCppRuntimePackageSpec(
                package_id="b1-win",
                label="Windows",
                platform_key="windows-x64",
                url="https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-win-cpu-x64.zip",
                archive_format="zip",
                binary_relpath="llama-server.exe",
                version="b1",
                variant="cpu",
            )
        ],
    )
    output = tmp_path / "runtime_manifest.json"

    code = audio_runtime_cli.main(
        [
            "runtime-manifest",
            "--mirror-base-url",
            "https://mirror.example/llama.cpp/b1",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["packages"][0]["url"] == "https://mirror.example/llama.cpp/b1/llama-b1-bin-win-cpu-x64.zip"


def test_audio_runtime_cli_runtime_manifest_adds_local_archive_metadata(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    archive = archive_root / "llama-b1-bin-macos-arm64.tar.gz"
    archive.write_bytes(b"runtime archive")
    monkeypatch.setattr(
        audio_runtime_cli,
        "fetch_latest_llama_cpp_runtime_packages",
        lambda timeout_seconds: [
            LlamaCppRuntimePackageSpec(
                package_id="b1-macos",
                label="macOS",
                platform_key="macos-arm64",
                url="https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-macos-arm64.tar.gz",
                archive_format="tar.gz",
                binary_relpath="llama-server",
                version="b1",
                variant="metal",
            )
        ],
    )

    code = audio_runtime_cli.main(
        [
            "runtime-manifest",
            "--relative-archive-dir",
            "archives",
            "--archive-root",
            str(archive_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["packages"][0]["size_bytes"] == len(b"runtime archive")
    assert payload["packages"][0]["sha256"] == hashlib.sha256(b"runtime archive").hexdigest()


def test_audio_runtime_cli_runtime_manifest_errors_when_archive_root_is_incomplete(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    monkeypatch.setattr(
        audio_runtime_cli,
        "fetch_latest_llama_cpp_runtime_packages",
        lambda timeout_seconds: [
            LlamaCppRuntimePackageSpec(
                package_id="b1-macos",
                label="macOS",
                platform_key="macos-arm64",
                url="https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-macos-arm64.tar.gz",
                archive_format="tar.gz",
                binary_relpath="llama-server",
            )
        ],
    )

    code = audio_runtime_cli.main(
        ["runtime-manifest", "--archive-root", str(archive_root)]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "本地 archive 不存在" in payload["message"]


def test_audio_runtime_cli_runtime_manifest_check_validates_relative_archives(
    tmp_path: Path,
    capsys,
) -> None:
    archive = tmp_path / "archives" / "llama-b1-bin-macos-arm64.tar.gz"
    archive.parent.mkdir(parents=True)
    content = b"runtime archive"
    archive.write_bytes(content)
    manifest = _write_runtime_manifest(
        tmp_path,
        [
            {
                "package_id": "b1-macos",
                "label": "macOS",
                "platform_key": "macos-arm64",
                "url": "archives/llama-b1-bin-macos-arm64.tar.gz",
                "archive_format": "tar.gz",
                "binary_relpath": "llama-server",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ],
    )

    code = audio_runtime_cli.main(
        [
            "runtime-manifest-check",
            "--manifest",
            str(manifest),
            "--require-platform",
            "macos-arm64",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["packages"][0]["archive_exists"] is True
    assert payload["packages"][0]["size_ok"] is True
    assert payload["packages"][0]["sha256_ok"] is True


def test_audio_runtime_cli_runtime_manifest_check_reports_missing_platform(
    tmp_path: Path,
    capsys,
) -> None:
    manifest = _write_runtime_manifest(
        tmp_path,
        [
            {
                "package_id": "b1-macos",
                "label": "macOS",
                "platform_key": "macos-arm64",
                "url": "https://mirror.example/llama-b1-bin-macos-arm64.tar.gz",
                "archive_format": "tar.gz",
                "binary_relpath": "llama-server",
            }
        ],
    )

    code = audio_runtime_cli.main(
        [
            "runtime-manifest-check",
            "--manifest",
            str(manifest),
            "--require-platform",
            "windows-x64",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["missing_platforms"] == ["windows-x64"]
    assert "缺少平台包：windows-x64" in payload["issues"]


def test_audio_runtime_cli_runtime_manifest_check_reports_checksum_mismatch(
    tmp_path: Path,
    capsys,
) -> None:
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    archive = archive_root / "llama-b1-bin-win-cpu-x64.zip"
    archive.write_bytes(b"actual")
    manifest = _write_runtime_manifest(
        tmp_path,
        [
            {
                "package_id": "b1-win",
                "label": "Windows",
                "platform_key": "windows-x64",
                "url": "https://mirror.example/llama-b1-bin-win-cpu-x64.zip",
                "archive_format": "zip",
                "binary_relpath": "llama-server.exe",
                "sha256": hashlib.sha256(b"expected").hexdigest(),
                "size_bytes": len(b"actual"),
            }
        ],
    )

    code = audio_runtime_cli.main(
        [
            "runtime-manifest-check",
            "--manifest",
            str(manifest),
            "--archive-root",
            str(archive_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["packages"][0]["archive_exists"] is True
    assert payload["packages"][0]["sha256_ok"] is False
    assert "b1-win archive sha256 不匹配" in payload["issues"]


def test_audio_runtime_cli_doctor_reports_ready_plans_with_existing_runtime(
    tmp_path: Path,
    capsys,
) -> None:
    binary = _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["runtime"]["binary_found"] is True
    assert payload["runtime"]["binary_path"] == str(binary)
    assert payload["ready_for_smoke"] is True
    assert payload["plans"]["speech"]["requires_model_download"] is True
    assert payload["plans"]["sound"]["model_download_hint"] == "约 2.1 GB"
    assert "hf_cli_found" in payload["huggingface"]


def test_audio_runtime_cli_doctor_reports_missing_hf_cli_for_model_downloads(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )
    monkeypatch.setattr(audio_runtime_doctor, "hf_cli_path", lambda: "")

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["huggingface"]["hf_cli_found"] is False
    assert any("Hugging Face CLI" in action for action in payload["next_actions"])


def test_audio_runtime_cli_doctor_reports_model_disk_space_issue(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )
    monkeypatch.setattr(
        audio_runtime_doctor,
        "build_disk_space_check",
        lambda path, required_bytes: {
            "ok": False,
            "available_bytes": 1024,
            "needed_bytes": 2048,
            "required_bytes": required_bytes,
        },
    )

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["model_cache"]["speech"]["disk_space"]["ok"] is False
    assert any("磁盘空间不足" in action for action in payload["next_actions"])


def test_audio_runtime_cli_doctor_reports_runtime_next_action_when_missing(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audio_runtime_cli, "discover_llama_server_binary", lambda base_dir: "")

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["runtime"]["binary_found"] is False
    assert payload["ready_for_smoke"] is False
    assert any("prepare-backend" in action for action in payload["next_actions"])


def test_audio_runtime_cli_doctor_lists_manifest_candidate_platforms(
    tmp_path: Path,
    capsys,
) -> None:
    _executable(tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server")
    manifest = tmp_path / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "package_id": "b1-macos",
                        "label": "macOS",
                        "platform_key": "macos-arm64",
                        "url": "archives/llama.tar.gz",
                        "archive_format": "tar.gz",
                        "binary_relpath": "llama-server",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    candidates = payload["runtime"]["manifest_candidates"]
    assert candidates[0]["path"] == str(manifest)
    assert candidates[0]["exists"] is True
    assert candidates[0]["package_count"] == 1
    assert candidates[0]["platforms"] == ["macos-arm64"]


def test_audio_runtime_cli_doctor_uses_cached_recommended_speech_model(
    tmp_path: Path,
    capsys,
) -> None:
    _executable(tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server")
    cache_dir = StoragePaths(tmp_path).sensory_model_cache_for(
        "speech",
        "ggml-org/Qwen3-ASR-0.6B-GGUF",
    )
    cache_dir.mkdir(parents=True)
    (cache_dir / "Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")
    (cache_dir / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["model_cache"]["speech"]["used_for_plan"] is True
    assert payload["model_cache"]["speech"]["gguf_count"] == 2
    assert payload["plans"]["speech"]["model"] == str(cache_dir)
    assert payload["plans"]["speech"]["model_location"] == "local"
    assert payload["plans"]["speech"]["requires_model_download"] is False
    assert payload["plans"]["sound"]["requires_model_download"] is True


def test_audio_runtime_cli_prepare_backend_requires_yes_before_download(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        audio_runtime_cli,
        "build_sensory_audio_runtime_doctor_report",
        lambda base_dir: {
            "runtime": {"binary_found": False},
            "model_cache": {"speech": {"ready": False}},
        },
    )

    def fail_prepare(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepare should require --yes before downloads")

    monkeypatch.setattr(audio_runtime_cli, "prepare_llama_cpp_audio_backend", fail_prepare)

    code = audio_runtime_cli.main(
        ["--base-dir", str(tmp_path), "prepare-backend", "--source", "speech"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "--yes" in payload["message"]
    assert payload["requirement"]["needs_runtime_download"] is True
    assert payload["requirement"]["needs_model_download"] is True


def test_audio_runtime_cli_prepare_backend_runs_with_yes(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[Path, str, bool, bool]] = []
    monkeypatch.setattr(
        audio_runtime_cli,
        "build_sensory_audio_runtime_doctor_report",
        lambda base_dir: {
            "runtime": {"binary_found": False},
            "model_cache": {"sound": {"ready": False}},
        },
    )

    def fake_prepare(base_dir, source, *, download_runtime, download_model):  # type: ignore[no-untyped-def]
        calls.append((Path(base_dir), source.value, download_runtime, download_model))
        return {"ok": True, "source": source.value, "message": "prepared"}

    monkeypatch.setattr(audio_runtime_cli, "prepare_llama_cpp_audio_backend", fake_prepare)

    code = audio_runtime_cli.main(
        ["--base-dir", str(tmp_path), "prepare-backend", "--source", "sound", "--yes"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert calls == [(tmp_path, "sound", True, True)]


def test_audio_runtime_cli_prepare_backend_can_finish_ready_cache_without_yes(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[Path, str, bool, bool]] = []
    monkeypatch.setattr(
        audio_runtime_cli,
        "build_sensory_audio_runtime_doctor_report",
        lambda base_dir: {
            "runtime": {"binary_found": True},
            "model_cache": {"speech": {"ready": True}},
        },
    )

    def fake_prepare(base_dir, source, *, download_runtime, download_model):  # type: ignore[no-untyped-def]
        calls.append((Path(base_dir), source.value, download_runtime, download_model))
        return {"ok": True, "source": source.value, "message": "prepared from cache"}

    monkeypatch.setattr(audio_runtime_cli, "prepare_llama_cpp_audio_backend", fake_prepare)

    code = audio_runtime_cli.main(
        ["--base-dir", str(tmp_path), "prepare-backend", "--source", "speech"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["message"] == "prepared from cache"
    assert calls == [(tmp_path, "speech", False, False)]


def test_audio_runtime_cli_plan_reports_missing_saved_provider(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "plan", "--source", "speech"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "未配置 speech 感官 provider" in payload["message"]


def _write_runtime_manifest(tmp_path: Path, packages: list[dict[str, object]]) -> Path:
    manifest = tmp_path / "runtime_manifest.json"
    manifest.write_text(
        json.dumps({"manifest_version": 1, "packages": packages}),
        encoding="utf-8",
    )
    return manifest


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path
