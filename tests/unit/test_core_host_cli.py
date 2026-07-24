from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.core_host.__main__ as core_host_main


GENERATION_ID = "00000000-0000-4000-8000-000000001c04"
GENERATION_CREDENTIAL = bytes.fromhex("99" * 16)


def test_app_root_is_required_and_has_no_cwd_repository_or_user_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_cwd = tmp_path / "cwd"
    isolated_cwd.mkdir()
    monkeypatch.chdir(isolated_cwd)

    with pytest.raises(SystemExit) as raised:
        core_host_main.parse_args(["--generation-id", GENERATION_ID])

    assert raised.value.code == 2


def test_explicit_app_root_is_resolved_without_requiring_it_to_exist(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing" / "app-root"

    args = core_host_main.parse_args(
        [
            "--app-root",
            str(missing_root),
            "--generation-id",
            GENERATION_ID,
            "--generation-number",
            "4",
        ]
    )

    assert args.app_root == missing_root.resolve(strict=False)
    assert args.app_root.is_absolute()
    assert not missing_root.exists()
    assert args.generation_number == 4


def test_main_passes_only_the_explicit_resolved_app_root_to_host_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_root = tmp_path / "layout" / "app"
    captured: list[object] = []
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(GENERATION_CREDENTIAL)))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(
        core_host_main,
        "run_host",
        lambda _input, _output, config: captured.append(config),
    )

    exit_code = core_host_main.main(
        [
            "--app-root",
            str(requested_root),
            "--generation-id",
            GENERATION_ID,
        ]
    )

    assert exit_code == 0
    assert len(captured) == 1
    config = captured[0]
    assert config.app_root == requested_root.resolve(strict=False)
    assert config.generation_id == GENERATION_ID
    assert config.generation_credential == GENERATION_CREDENTIAL.hex()
