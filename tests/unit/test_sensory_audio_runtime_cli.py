from __future__ import annotations

import json
import sys
from pathlib import Path

from app.sensory import audio_runtime_cli


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


def test_audio_runtime_cli_install_runtime_requires_yes_before_download(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audio_runtime_cli, "discover_llama_server_binary", lambda base_dir: "")

    def fail_if_called(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("runtime download should require --yes")

    monkeypatch.setattr(audio_runtime_cli, "fetch_latest_llama_cpp_runtime_packages", fail_if_called)

    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "install-runtime"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "--yes" in payload["message"]


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


def test_audio_runtime_cli_plan_reports_missing_saved_provider(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = audio_runtime_cli.main(["--base-dir", str(tmp_path), "plan", "--source", "speech"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "未配置 speech 感官 provider" in payload["message"]


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path
