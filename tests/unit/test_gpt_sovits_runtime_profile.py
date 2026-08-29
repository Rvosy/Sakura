from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from plugins.builtin.sakura_gpt_sovits import _bundle, _runtime_profile, _support


def _candidate(
    index: int,
    name: str,
    capability: tuple[int, int],
    memory: float,
    *,
    free: float | None = None,
    fp16: bool = True,
) -> _runtime_profile.DeviceCandidate:
    return _runtime_profile.DeviceCandidate(
        index=index,
        name=name,
        capability=capability,
        total_memory_gib=memory,
        free_memory_gib=memory if free is None else free,
        fp16_works=fp16,
    )


def test_device_profile_selects_precision_and_best_compatible_gpu() -> None:
    rtx_5060 = _runtime_profile.select_device_profile(
        [_candidate(0, "NVIDIA GeForce RTX 5060", (12, 0), 7.96)],
        require_cuda=True,
    )
    assert rtx_5060 == _runtime_profile.DeviceProfile(
        "cuda:0",
        True,
        "NVIDIA GeForce RTX 5060",
    )

    gtx_1060 = _runtime_profile.select_device_profile(
        [_candidate(0, "NVIDIA GeForce GTX 1060", (6, 1), 6.0)],
        require_cuda=True,
    )
    assert gtx_1060.device == "cuda:0"
    assert gtx_1060.is_half is False

    gtx_1660 = _runtime_profile.select_device_profile(
        [_candidate(0, "NVIDIA GeForce GTX 1660", (7, 5), 6.0)],
        require_cuda=True,
    )
    assert gtx_1660.is_half is False

    failed_fp16 = _runtime_profile.select_device_profile(
        [_candidate(0, "NVIDIA GeForce RTX 4070", (8, 9), 12.0, fp16=False)],
        require_cuda=True,
    )
    assert failed_fp16.device == "cuda:0"
    assert failed_fp16.is_half is False

    best = _runtime_profile.select_device_profile(
        [
            _candidate(0, "Older", (8, 6), 24.0, free=20.0),
            _candidate(2, "Newer", (12, 0), 8.0, free=6.0),
            _candidate(1, "Newer More Free", (12, 0), 8.0, free=7.0),
        ],
        require_cuda=True,
    )
    assert best.device == "cuda:1"


def test_device_profile_uses_cpu_or_fails_below_vram_floor() -> None:
    low_memory = [_candidate(0, "NVIDIA GeForce GTX 1050 Ti", (6, 1), 5.74)]
    assert _runtime_profile.select_device_profile(
        low_memory,
        require_cuda=False,
    ) == _runtime_profile.DeviceProfile("cpu", False, "CPU")
    with pytest.raises(_runtime_profile.RuntimeProfileError, match="TTS_ACCELERATOR_UNAVAILABLE"):
        _runtime_profile.select_device_profile(low_memory, require_cuda=True)


def test_profile_generation_updates_all_versions_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "g50"
    configs = work_dir / "GPT_SoVITS" / "configs"
    configs.mkdir(parents=True)
    source = configs / "tts_infer.yaml"
    original = {
        "custom": {"device": "cpu", "is_half": False, "version": "v2ProPlus", "weight": "custom.pth"},
        "v1": {"device": "cpu", "is_half": False, "version": "v1", "weight": "v1.pth"},
        "future": {"device": "cpu", "is_half": False, "version": "v9", "unknown": {"keep": True}},
        "metadata": {"keep": "unchanged"},
    }
    source.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        _runtime_profile,
        "_probe_candidates",
        lambda: [_candidate(0, "NVIDIA GeForce RTX 5060", (12, 0), 7.96)],
    )
    validated: list[tuple[Path, _runtime_profile.DeviceProfile]] = []
    monkeypatch.setattr(
        _runtime_profile,
        "_validate_tts_config",
        lambda _work, path, profile: validated.append((path, profile)),
    )

    generated, profile = _runtime_profile._generate_profile(work_dir, require_cuda=True)

    assert generated == _runtime_profile.managed_profile_path(work_dir).resolve()
    assert profile.device == "cuda:0"
    assert profile.is_half is True
    payload = yaml.safe_load(generated.read_text(encoding="utf-8"))
    for key in ("custom", "v1", "future"):
        assert payload[key]["device"] == "cuda:0"
        assert payload[key]["is_half"] is True
    assert payload["custom"]["weight"] == "custom.pth"
    assert payload["future"]["unknown"] == {"keep": True}
    assert payload["metadata"] == {"keep": "unchanged"}
    assert yaml.safe_load(source.read_text(encoding="utf-8")) == original
    assert validated and validated[0][0] != generated
    assert not list(configs.glob(".*.tmp.yaml"))


def test_existing_cuda_profile_refuses_silent_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "gpt"
    target = _runtime_profile.managed_profile_path(work_dir)
    target.parent.mkdir(parents=True)
    target.write_text(
        yaml.safe_dump({"custom": {"device": "cuda:0", "is_half": True, "version": "v2"}}),
        encoding="utf-8",
    )
    before = target.read_bytes()
    monkeypatch.setattr(_runtime_profile, "_probe_candidates", lambda: [])
    with pytest.raises(_runtime_profile.RuntimeProfileError, match="TTS_ACCELERATOR_UNAVAILABLE"):
        _runtime_profile._generate_profile(work_dir, require_cuda=False)
    assert target.read_bytes() == before


def test_user_config_remains_authoritative_without_probe(tmp_path: Path) -> None:
    work_dir = tmp_path / "gpt"
    python = work_dir / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    custom = tmp_path / "user.yaml"
    custom.write_text("custom: true", encoding="utf-8")

    def unexpected_runner(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("custom config must not trigger managed profile generation")

    assert _runtime_profile.prepare_managed_profile(
        work_dir,
        runtime_python=python,
        configured_path=custom,
        platform="win32",
        runner=unexpected_runner,
    ) == custom


def test_host_profile_runner_requires_structured_success(tmp_path: Path) -> None:
    work_dir = tmp_path / "gpt"
    python = work_dir / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    generated = _runtime_profile.managed_profile_path(work_dir)
    generated.parent.mkdir(parents=True)
    generated.write_text("custom: {}", encoding="utf-8")
    line = _runtime_profile._RESULT_PREFIX + json.dumps({"ok": True, "path": str(generated)})

    def runner(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess([], 0, stdout=f"noise\n{line}\n", stderr="")

    assert _runtime_profile.prepare_managed_profile(
        work_dir,
        runtime_python=python,
        platform="win32",
        runner=runner,
    ) == generated.resolve()


def test_bundle_prepares_profile_in_staging_before_replacing_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x"
    entry = replace(
        _bundle.GPT_SOVITS_STANDARD,
        filename="fixture.7z",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    archive = tmp_path / "tts" / "_dl" / entry.filename
    archive.parent.mkdir(parents=True)
    archive.write_bytes(payload)
    prepared: list[Path] = []

    def extract(_archive: Path, staging: Path) -> None:
        (staging / "runtime").mkdir(parents=True)
        (staging / "runtime" / "python.exe").write_bytes(b"")
        (staging / "api_v2.py").write_text("", encoding="utf-8")
        default = staging / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        default.parent.mkdir(parents=True)
        default.write_text("custom: {}", encoding="utf-8")

    def prepare(work_dir: Path, **_kwargs) -> Path:  # type: ignore[no-untyped-def]
        prepared.append(work_dir)
        target = _runtime_profile.managed_profile_path(work_dir)
        target.write_text("custom: {}", encoding="utf-8")
        return target

    monkeypatch.setattr(_bundle, "_extract", extract)
    monkeypatch.setattr(_bundle, "prepare_managed_profile", prepare)
    monkeypatch.setattr(
        _bundle,
        "_replace_directory",
        lambda source, target: shutil.copytree(source, target),
    )
    result = _bundle._install_archive(
        entry,
        tmp_path,
        check_cancel=lambda: None,
        on_progress=lambda _value: None,
        on_status=lambda _value: None,
        on_download_progress=lambda _value: None,
    )

    assert prepared and prepared[0].parent.name == "_tmp"
    assert result.work_dir == (tmp_path / "tts" / "gpt").resolve()
    assert result.tts_config_path == _runtime_profile.managed_profile_path(result.work_dir).resolve()
    assert result.tts_config_path.is_file()


def test_bundle_profile_failure_keeps_existing_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x"
    entry = replace(
        _bundle.GPT_SOVITS_NVIDIA50,
        filename="fixture.7z",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    archive = tmp_path / "tts" / "_dl" / entry.filename
    archive.parent.mkdir(parents=True)
    archive.write_bytes(payload)
    installed = tmp_path / "tts" / "g50"
    installed.mkdir(parents=True)
    sentinel = installed / "keep.txt"
    sentinel.write_text("existing", encoding="utf-8")

    def extract(_archive: Path, staging: Path) -> None:
        (staging / "api_v2.py").parent.mkdir(parents=True, exist_ok=True)
        (staging / "api_v2.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(_bundle, "_extract", extract)
    monkeypatch.setattr(
        _bundle,
        "prepare_managed_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _runtime_profile.RuntimeProfileError("TTS_ACCELERATOR_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        _bundle,
        "_replace_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not replace")),
    )

    with pytest.raises(_runtime_profile.RuntimeProfileError, match="TTS_ACCELERATOR_UNAVAILABLE"):
        _bundle._install_archive(
            entry,
            tmp_path,
            check_cancel=lambda: None,
            on_progress=lambda _value: None,
            on_status=lambda _value: None,
            on_download_progress=lambda _value: None,
        )
    assert sentinel.read_text(encoding="utf-8") == "existing"


class _StoppedProcess:
    def poll(self) -> int:
        return 0


def test_managed_runtime_binds_generated_profile_to_api_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "g50"
    python = work_dir / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (work_dir / "api_v2.py").write_text("", encoding="utf-8")
    generated = _runtime_profile.managed_profile_path(work_dir)
    generated.parent.mkdir(parents=True)
    generated.write_text("custom: {}", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(_support, "prepare_managed_profile", lambda *_args, **_kwargs: generated)
    monkeypatch.setattr(
        _support.subprocess,
        "Popen",
        lambda command, **_kwargs: calls.append(list(command)) or _StoppedProcess(),
    )
    runtime = _support._ManagedRuntime(
        SimpleNamespace(
            work_dir=work_dir,
            python_path=python,
            tts_config_path=None,
            api_url="http://127.0.0.1:9880/tts",
        ),
        base_dir=tmp_path,
        is_closed=lambda: False,
    )
    try:
        assert runtime._start(lambda _message: None) is True
    finally:
        runtime.close()
    assert calls
    assert calls[0][2:4] == ["-c", str(generated)]


def test_managed_runtime_does_not_spawn_when_accelerator_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "g50"
    python = work_dir / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (work_dir / "api_v2.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        _support,
        "prepare_managed_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _runtime_profile.RuntimeProfileError("TTS_ACCELERATOR_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        _support.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    errors: list[str] = []
    runtime = _support._ManagedRuntime(
        SimpleNamespace(
            work_dir=work_dir,
            python_path=python,
            tts_config_path=None,
            api_url="http://127.0.0.1:9880/tts",
        ),
        base_dir=tmp_path,
        is_closed=lambda: False,
    )
    assert runtime._start(errors.append) is False
    assert errors == ["TTS_ACCELERATOR_UNAVAILABLE"]
