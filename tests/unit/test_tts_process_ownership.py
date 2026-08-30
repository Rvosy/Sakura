from __future__ import annotations

import signal
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from app.voice import tts_service
from app.voice.tts_service import GenieServiceSupervisor, TTSServiceSupervisor
from app.voice.tts_settings import (
    GPTSoVITSTTSSettings,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    ToneReference,
)


def _settings(tmp_path: Path) -> GPTSoVITSTTSSettings:
    work_dir = tmp_path / "GPT SoVITS"
    runtime_dir = work_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    python = runtime_dir / ("python.exe" if tts_service.sys.platform == "win32" else "python")
    python.write_bytes(b"")
    (work_dir / "api_v2.py").write_text("# fixture", encoding="utf-8")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"fixture")
    return GPTSoVITSTTSSettings(
        enabled=True,
        provider=TTS_PROVIDER_GPT_SOVITS,
        api_url="http://127.0.0.1:9880/tts",
        ref_audio_path=reference,
        ref_text_path=reference,
        ref_text="fixture",
        work_dir=work_dir,
        python_path=python,
    )


def _command(settings: GPTSoVITSTTSSettings, script: Path | None = None) -> str:
    assert settings.python_path is not None
    assert settings.work_dir is not None
    script = script or settings.work_dir / "api_v2.py"
    return subprocess.list2cmdline([str(settings.python_path.resolve()), str(script.resolve()), "-p", "9880"])


def test_exact_command_match_requires_python_and_api_script_tokens(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert tts_service._command_line_matches_local_tts(settings, _command(settings), 9880)
    assert not tts_service._command_line_matches_local_tts(
        settings, _command(settings, settings.work_dir / "api_v2.py.bak"), 9880  # type: ignore[operator]
    )
    assert not tts_service._command_line_matches_local_tts(
        settings,
        subprocess.list2cmdline(
            [str(tmp_path / "other-python.exe"), str(settings.work_dir / "api_v2.py")]
        ),
        9880,
    )


def test_windows_subprocess_path_removes_verbatim_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts_service.sys, "platform", "win32")

    assert tts_service._subprocess_path(r"\\?\D:\Project\sakura\tts\cpu") == (
        r"D:\Project\sakura\tts\cpu"
    )
    assert tts_service._subprocess_path(r"\\?\UNC\server\share\tts") == (
        r"\\server\share\tts"
    )


def test_windows_tts_commands_and_environment_do_not_expose_verbatim_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts_service.sys, "platform", "win32")
    python_exe = Path(r"\\?\D:\Project\sakura\tts\cpu\runtime\python.exe")
    api_script = Path(r"\\?\D:\Project\sakura\tts\cpu\api_v2.py")
    settings = replace(
        _settings(tmp_path),
        tts_config_path=Path(r"\\?\D:\Project\sakura\tts\cpu\tts_infer.yaml"),
    )

    commands = [
        *tts_service._build_genie_start_command(python_exe, "127.0.0.1", 9881),
        *tts_service._build_gpt_sovits_start_command(python_exe, api_script, settings),
    ]
    environment = tts_service._local_tts_subprocess_env(python_exe)

    assert all("\\\\?\\" not in value for value in commands)
    assert "\\\\?\\" not in environment["PATH"]


def test_exact_process_match_treats_verbatim_and_regular_paths_as_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    assert settings.python_path is not None
    assert settings.work_dir is not None
    monkeypatch.setattr(tts_service.sys, "platform", "win32")
    regular_command = subprocess.list2cmdline(
        [str(settings.python_path.resolve()), str((settings.work_dir / "api_v2.py").resolve())]
    )
    settings = replace(
        settings,
        python_path=Path("\\\\?\\" + str(settings.python_path.resolve())),
        work_dir=Path("\\\\?\\" + str(settings.work_dir.resolve())),
    )

    assert tts_service._command_line_matches_local_tts(settings, regular_command, 9880)


def test_windows_onnx_scan_and_genie_payloads_use_regular_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tts_service.sys, "platform", "win32")
    onnx_dir = tmp_path / "onnx"
    onnx_dir.mkdir()
    (onnx_dir / "model.onnx").write_bytes(b"fixture")
    reference_path = tmp_path / "reference.wav"
    reference_path.write_bytes(b"fixture")
    verbatim_onnx = Path("\\\\?\\" + str(onnx_dir.resolve()))
    verbatim_reference = Path("\\\\?\\" + str(reference_path.resolve()))
    settings = replace(
        _settings(tmp_path),
        provider=TTS_PROVIDER_GENIE,
        character_name="fixture",
        onnx_model_dir=verbatim_onnx,
    )
    supervisor = GenieServiceSupervisor(settings)
    payloads: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(supervisor, "_ensure_onnx_model_dir", lambda _fail: True)
    monkeypatch.setattr(
        supervisor,
        "_post_json_and_read_bytes",
        lambda endpoint, payload, **_kwargs: payloads.append((endpoint, payload)) or b"ok",
    )
    failures: list[str] = []

    assert tts_service._has_onnx_files(verbatim_onnx)
    assert supervisor._ensure_character_model("ja", failures.append)
    assert supervisor._ensure_reference_audio(
        ToneReference("neutral", verbatim_reference, "fixture", "ja"),
        failures.append,
    )
    assert failures == []
    assert payloads[0][1]["onnx_model_dir"] == str(onnx_dir.resolve())
    assert payloads[1][1]["audio_path"] == str(reference_path.resolve())


def test_bundled_gpt_kills_exact_same_user_tree_before_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = TTSServiceSupervisor(_settings(tmp_path))
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(tts_service, "_find_listening_tcp_pid", lambda _port: 4242)
    monkeypatch.setattr(tts_service, "_process_belongs_to_current_user", lambda _pid: True)
    monkeypatch.setattr(
        tts_service, "_query_process_command_line", lambda _pid: _command(supervisor.settings)
    )
    monkeypatch.setattr(
        tts_service, "_terminate_pid_tree", lambda pid, timeout: killed.append((pid, timeout))
    )
    monkeypatch.setattr(
        tts_service, "_wait_for_process_and_port_release", lambda *_args: True
    )
    failures: list[str] = []

    assert supervisor._prepare_bundled_gpt_port("127.0.0.1", 9880, failures.append)
    assert killed == [(4242, 5)]
    assert failures == []


@pytest.mark.parametrize("same_user", [False, True])
def test_unknown_or_nonmatching_port_owner_is_never_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_user: bool
) -> None:
    supervisor = TTSServiceSupervisor(_settings(tmp_path))
    monkeypatch.setattr(tts_service, "_find_listening_tcp_pid", lambda _port: 5151)
    monkeypatch.setattr(
        tts_service, "_process_belongs_to_current_user", lambda _pid: same_user
    )
    monkeypatch.setattr(tts_service, "_query_process_command_line", lambda _pid: "python other.py")
    monkeypatch.setattr(
        tts_service,
        "_terminate_pid_tree",
        lambda *_args, **_kwargs: pytest.fail("unknown process was killed"),
    )
    failures: list[str] = []

    assert not supervisor._prepare_bundled_gpt_port("127.0.0.1", 9880, failures.append)
    assert failures[0].startswith("TTS_PORT_OCCUPIED_BY_OTHER_PROCESS")


def test_kill_or_release_failure_has_stable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = TTSServiceSupervisor(_settings(tmp_path))
    monkeypatch.setattr(tts_service, "_find_listening_tcp_pid", lambda _port: 6161)
    monkeypatch.setattr(tts_service, "_process_belongs_to_current_user", lambda _pid: True)
    monkeypatch.setattr(
        tts_service, "_query_process_command_line", lambda _pid: _command(supervisor.settings)
    )
    monkeypatch.setattr(
        tts_service,
        "_terminate_pid_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("tree", 5)),
    )
    failures: list[str] = []

    assert not supervisor._prepare_bundled_gpt_port("127.0.0.1", 9880, failures.append)
    assert failures[0].startswith("TTS_STALE_PROCESS_KILL_FAILED")


def test_posix_tree_termination_targets_descendants_then_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts_service.sys, "platform", "linux")
    monkeypatch.setattr(tts_service.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(tts_service, "_posix_descendant_pids", lambda _pid: [11, 12, 13])
    alive = {10, 11, 12, 13}
    signals: list[tuple[int, signal.Signals]] = []

    def fake_kill(pid: int, sent_signal: signal.Signals) -> None:
        signals.append((pid, sent_signal))
        if sent_signal == signal.SIGKILL:
            alive.discard(pid)

    monkeypatch.setattr(tts_service.os, "kill", fake_kill)
    monkeypatch.setattr(tts_service, "_process_exists", lambda pid: pid in alive)

    tts_service._terminate_pid_tree(10, timeout=0)

    assert signals[:4] == [
        (13, signal.SIGTERM),
        (12, signal.SIGTERM),
        (11, signal.SIGTERM),
        (10, signal.SIGTERM),
    ]
    assert {pid for pid, sent in signals if sent == signal.SIGKILL} == {10, 11, 12, 13}


def test_service_probe_logs_stable_events_without_raw_socket_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = TTSServiceSupervisor(_settings(tmp_path))
    events: list[tuple[str, dict[str, object]]] = []

    def capture(_channel, _message, attributes=None, **kwargs):
        events.append((kwargs.get("event", ""), dict(attributes or {})))

    monkeypatch.setattr(tts_service, "log_event", capture)
    monkeypatch.setattr(
        tts_service.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private socket detail")),
    )

    assert not supervisor._probe_service_port(
        "127.0.0.1", 9880, 1, purpose="pre_start_check"
    )
    assert [event for event, _attributes in events] == [
        "tts.service.probe.started",
        "tts.service.probe.failed",
    ]
    assert events[-1][1] == {
        "provider": "gpt-sovits",
        "port": 9880,
        "purpose": "pre_start_check",
        "code": "TTS_PROBE_UNAVAILABLE",
    }
