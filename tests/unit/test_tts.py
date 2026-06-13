from __future__ import annotations

import importlib.util
import sys
import types
import urllib.error
import uuid
import wave
from dataclasses import replace
from pathlib import Path

if importlib.util.find_spec("PySide6") is None:
    pyside_module = types.ModuleType("PySide6")
    qtcore_module = types.ModuleType("PySide6.QtCore")
    qtmultimedia_module = types.ModuleType("PySide6.QtMultimedia")

    class QObject:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class QTimer:
        @staticmethod
        def singleShot(*_args: object, **_kwargs: object) -> None:
            pass

    class QUrl:
        @staticmethod
        def fromLocalFile(path: str) -> str:
            return path

    class Signal:
        def __init__(self, *_args: object) -> None:
            pass

        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

        def emit(self, *_args: object, **_kwargs: object) -> None:
            pass

    def Slot(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        def decorator(function):  # type: ignore[no-untyped-def]
            return function

        return decorator

    class QAudioOutput:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class QMediaPlayer:
        class MediaStatus:
            EndOfMedia = object()

        class PlaybackState:
            PlayingState = object()

        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    qtcore_module.QObject = QObject
    qtcore_module.QTimer = QTimer
    qtcore_module.QUrl = QUrl
    qtcore_module.Signal = Signal
    qtcore_module.Slot = Slot
    qtmultimedia_module.QAudioOutput = QAudioOutput
    qtmultimedia_module.QMediaPlayer = QMediaPlayer
    sys.modules["PySide6"] = pyside_module
    sys.modules["PySide6.QtCore"] = qtcore_module
    sys.modules["PySide6.QtMultimedia"] = qtmultimedia_module

from app.voice.tts import (
    GenieTTSProvider,
    GPTSoVITSTTSProvider,
    GPTSoVITSTTSSettings,
    TTSPreparedAudio,
    _build_gpt_sovits_start_command,
    _build_genie_endpoint_url,
    _format_gpt_sovits_http_error,
    _load_tone_references,
    _local_tts_subprocess_env,
    _read_local_tts_output,
    _resolve_request_text_lang,
    _resolve_tts_cache_dir,
    _write_genie_audio,
    purge_tts_cache,
)
from app.core.gui_log import GUI_LOG_SCOPE_TTS, clear_gui_logs, get_gui_log_buffer
from app.voice import VoicePlaybackController
from app.voice.text_language_guard import should_skip_tts_text


def test_language_guard_allows_japanese_text_for_japanese_tts() -> None:
    assert not should_skip_tts_text("うん。大丈夫。", "ja")


def test_language_guard_skips_obvious_chinese_for_japanese_tts() -> None:
    assert should_skip_tts_text("原因是 Mermaid 语法。", "ja")
    assert should_skip_tts_text("这是中文，不能进 TTS。", "all_ja")


def test_language_guard_keeps_kanji_only_japanese_candidate() -> None:
    assert not should_skip_tts_text("大丈夫", "ja")


def test_language_guard_only_applies_to_japanese_targets() -> None:
    assert not should_skip_tts_text("这是中文，不能进 TTS。", "zh")
    assert not should_skip_tts_text("这是中文，不能进 TTS。", "en")


def test_tts_mixed_japanese_and_english_uses_auto_lang() -> None:
    text = "Steamを開いているんだね。Muse Dash…楽しそうなゲーム。"

    assert _resolve_request_text_lang(text, "ja") == "auto"


def test_tts_plain_japanese_keeps_configured_lang() -> None:
    text = "でも私、初めて君に会った時、思ったよ。"

    assert _resolve_request_text_lang(text, "ja") == "ja"


def test_tts_explicit_english_lang_is_not_overridden() -> None:
    text = "Steam is open."

    assert _resolve_request_text_lang(text, "en") == "en"


def test_tts_yue_mixed_english_uses_auto_yue() -> None:
    text = "Steam 打开咗。"

    assert _resolve_request_text_lang(text, "all_yue") == "auto_yue"


def test_tone_references_load_four_part_rows_only() -> None:
    root = _runtime_root("tone_refs")
    ref_dir = root / "voice" / "refs"
    audio_path = ref_dir / "tone_refs" / "neutral.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"wav")
    ref_path = ref_dir / "ref.txt"
    ref_path.write_text(
        "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
        encoding="utf-8",
    )
    rows = [line for line in ref_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    references = _load_tone_references(ref_path, root)

    assert all(len(row.split("|")) == 4 for row in rows)
    assert references
    assert all("|" not in reference.ref_text for items in references.values() for reference in items)
    assert all(reference.ref_audio_path.exists() for items in references.values() for reference in items)


def test_tts_provider_can_skip_constructor_service_adoption(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fake_adopt(self) -> None:  # type: ignore[no-untyped-def]
        calls.append(type(self).__name__)

    monkeypatch.setattr(
        GPTSoVITSTTSProvider,
        "_adopt_existing_configured_service",
        fake_adopt,
    )

    GPTSoVITSTTSProvider(_minimal_tts_settings(), adopt_existing_service=False)
    GPTSoVITSTTSProvider(_minimal_tts_settings())

    assert calls == ["GPTSoVITSTTSProvider"]


def test_service_ready_property_reflects_probe_state() -> None:
    # 接话音频预生成依赖此公开属性做就绪门控:探测成功前必须为 False。
    provider = GPTSoVITSTTSProvider(_minimal_tts_settings(), adopt_existing_service=False)
    assert provider.service_ready is False
    provider._service_checked = True
    assert provider.service_ready is True


def test_tts_service_probe_reports_unavailable_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings()
    provider._service_checked = False
    messages: list[str] = []

    def fake_create_connection(*_args: object, **_kwargs: object) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)

    assert not GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert "服务不可用" in messages[0]
    assert "http://127.0.0.1:9880/tts" in messages[0]


def test_tts_service_probe_uses_tcp_connection_without_get(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings()
    provider._service_checked = False
    messages: list[str] = []
    calls: list[tuple[tuple[str, int], int]] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_create_connection(address: tuple[str, int], timeout: int) -> FakeConnection:
        calls.append((address, timeout))
        return FakeConnection()

    def fail_urlopen(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("服务探测不应请求 /tts")

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)
    monkeypatch.setattr("app.voice.tts.urllib.request.urlopen", fail_urlopen)

    assert GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert messages == []
    assert calls == [(("127.0.0.1", 9880), 1)]


def test_tts_service_probe_does_not_start_process_when_port_is_ready(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(
        work_dir=Path("data/tts_bundles/installed/gpt_sovits_v2pro")
    )
    provider._service_checked = False
    provider._server_process = None

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("app.voice.tts.socket.create_connection", lambda *_args, **_kwargs: FakeConnection())
    monkeypatch.setattr("app.voice.tts._find_running_local_tts_process", lambda *_args: None)
    monkeypatch.setattr(
        "app.voice.tts.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应启动本地服务")),
    )

    assert GPTSoVITSTTSProvider._ensure_service_available(provider, lambda _msg: None)


def test_genie_service_probe_adopts_existing_local_process_when_port_is_ready(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("genie_adopt") / "genie"
    (work_dir / "runtime").mkdir(parents=True)
    _write_fake_runtime_python(work_dir / "runtime" / "python.exe")
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(provider="genie-tts", work_dir=work_dir, api_url="http://127.0.0.1:9881/")
    provider._service_checked = False
    provider._server_process = None

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    python_exe = work_dir.resolve() / "runtime" / "python.exe"
    command_line = (
        f'"{python_exe}" -c "import genie_tts\n'
        "genie_tts.start_server(host='127.0.0.1', port=9881, workers=1)\n"
        '"'
    )

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        if args[0] == "netstat":
            return types.SimpleNamespace(
                returncode=0,
                stdout="  TCP    127.0.0.1:9881     0.0.0.0:0      LISTENING       41608\n",
            )
        if args[0] == "powershell":
            return types.SimpleNamespace(returncode=0, stdout=command_line)
        raise AssertionError(f"未预期的命令：{args}")

    monkeypatch.setattr("app.voice.tts.sys.platform", "win32")
    monkeypatch.setattr("app.voice.tts.os.getpid", lambda: 1234)
    monkeypatch.setattr("app.voice.tts.socket.create_connection", lambda *_args, **_kwargs: FakeConnection())
    monkeypatch.setattr("app.voice.tts.subprocess.run", fake_run)
    monkeypatch.setattr(GenieTTSProvider, "_probe_genie_api", lambda *_args: True)

    assert GenieTTSProvider._ensure_service_available(provider, lambda _msg: None)
    assert provider._server_process is not None
    assert provider._server_process.pid == 41608


def test_tts_provider_adopts_existing_local_process_on_init(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("gptsovits_adopt_init") / "gpt-sovits"

    class FakeAttachedProcess:
        pid = 24680

        def poll(self) -> None:
            return None

    attached = FakeAttachedProcess()

    def fake_find_process(settings, port):  # type: ignore[no-untyped-def]
        assert settings.work_dir == work_dir
        assert port == 9880
        return attached

    monkeypatch.setattr("app.voice.tts._find_running_local_tts_process", fake_find_process)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings(work_dir=work_dir))

    assert provider._server_process is attached


def test_tts_service_probe_starts_local_gptsovits_when_port_is_down(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("gptsovits_start") / "gpt-sovits"
    (work_dir / "runtime").mkdir(parents=True)
    runtime_python = work_dir / "runtime" / "python.exe"
    _write_fake_runtime_python(runtime_python)
    (work_dir / "api_v2.py").write_text("fake", encoding="utf-8")
    monkeypatch.chdir(work_dir.parent)
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(work_dir=work_dir)
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []
    connection_calls = 0
    popen_calls: list[list[str]] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProcess:
        pid = 1234

        def poll(self) -> None:
            return None

    def fake_create_connection(*_args: object, **_kwargs: object) -> FakeConnection:
        nonlocal connection_calls
        connection_calls += 1
        if connection_calls == 1:
            raise OSError("connection refused")
        return FakeConnection()

    def fake_popen(args, **_kwargs):  # type: ignore[no-untyped-def]
        popen_calls.append(list(args))
        return FakeProcess()

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)
    monkeypatch.setattr("app.voice.tts.subprocess.Popen", fake_popen)
    # TCP 探测成功后还会做 HTTP 探测，这里 mock 掉避免真实网络请求
    monkeypatch.setattr("app.voice.tts._probe_gpt_sovits_http", lambda *_: True)

    assert GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert messages == []
    assert len(popen_calls) == 1
    assert popen_calls[0] == [
        str(work_dir / "runtime" / "python.exe"),
        str(work_dir / "api_v2.py"),
        "-a",
        "127.0.0.1",
        "-p",
        "9880",
    ]
    assert (work_dir.parent / "data" / "logs" / "gpt-sovits-service.log").is_file()


def test_gptsovits_start_command_uses_custom_python_and_tts_config() -> None:
    root = _runtime_root("gptsovits_custom_python")
    work_dir = root / "GPT-SoVITS"
    python_path = root / "miniforge3" / "envs" / "gpt-sovits" / "bin" / "python"
    api_script = work_dir / "api_v2.py"
    tts_config_path = work_dir / "GPT_SoVITS" / "configs" / "tts_infer_sakura.yaml"
    settings = _minimal_tts_settings(
        work_dir=work_dir,
        api_url="http://localhost:9880/tts",
        python_path=python_path,
        tts_config_path=tts_config_path,
    )

    assert _build_gpt_sovits_start_command(python_path, api_script, settings) == [
        str(python_path),
        str(api_script),
        "-c",
        str(tts_config_path),
        "-a",
        "127.0.0.1",
        "-p",
        "9880",
    ]


def test_tts_service_waits_past_thirty_seconds_for_slow_gptsovits_start(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("gptsovits_slow_start") / "gpt-sovits"
    (work_dir / "runtime").mkdir(parents=True)
    _write_fake_runtime_python(work_dir / "runtime" / "python.exe")
    (work_dir / "api_v2.py").write_text("fake", encoding="utf-8")
    monkeypatch.chdir(work_dir.parent)
    provider = types.SimpleNamespace()
    provider.settings = replace(_minimal_tts_settings(work_dir=work_dir), timeout_seconds=55)
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []
    debug_messages: list[tuple[str, object]] = []
    elapsed = 0.0

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProcess:
        pid = 1234

        def poll(self) -> None:
            return None

    def fake_create_connection(*_args: object, **_kwargs: object) -> FakeConnection:
        if elapsed < 31:
            raise OSError("connection refused")
        return FakeConnection()

    def fake_sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)
    monkeypatch.setattr("app.voice.tts.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr("app.voice.tts.time.monotonic", lambda: elapsed)
    monkeypatch.setattr("app.voice.tts.time.sleep", fake_sleep)
    # TCP 探测成功后还会做 HTTP 探测，这里 mock 掉避免真实网络请求
    monkeypatch.setattr("app.voice.tts._probe_gpt_sovits_http", lambda *_: True)
    monkeypatch.setattr(
        "app.voice.tts.debug_log",
        lambda _category, message, data=None: debug_messages.append((message, data)),
    )

    assert GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert messages == []
    assert elapsed >= 31
    log_messages = [message for message, _data in debug_messages]
    assert "本地 GPT-SoVITS 服务尚未就绪，继续等待" in log_messages
    assert "服务不可用" not in log_messages
    assert any(
        isinstance(data, dict) and data.get("purpose") == "startup_wait"
        for _message, data in debug_messages
    )


def test_tts_service_probe_reports_missing_local_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("gptsovits_missing_runtime") / "gpt-sovits"
    work_dir.mkdir()
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(work_dir=work_dir)
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []

    def fake_create_connection(*_args: object, **_kwargs: object) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)

    assert not GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert "运行时不可用" in messages[0]
    assert "未找到当前系统可执行的 Python 运行时" in messages[0]


def test_tts_service_probe_reports_incompatible_windows_runtime_on_macos(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("gptsovits_incompatible_runtime") / "gpt-sovits"
    runtime_python = work_dir / "runtime" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_bytes(b"MZ\x00\x00")
    runtime_python.chmod(0o755)
    (work_dir / "api_v2.py").write_text("fake", encoding="utf-8")
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(work_dir=work_dir)
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []

    def fake_create_connection(*_args: object, **_kwargs: object) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr("app.voice.tts.sys.platform", "darwin")
    monkeypatch.setattr("app.voice.runtime_compat.sys.platform", "darwin")
    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)

    assert not GPTSoVITSTTSProvider._ensure_service_available(provider, messages.append)
    assert "检测到 Windows Python 运行时" in messages[0]
    assert "当前系统是 macOS" in messages[0]


def test_gptsovits_ensure_ready_returns_success_after_service_and_weights(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    calls: list[str] = []

    def fake_service(_self, _fail):  # type: ignore[no-untyped-def]
        calls.append("service")
        return True

    def fake_weights(_self, _fail):  # type: ignore[no-untyped-def]
        calls.append("weights")
        return True

    monkeypatch.setattr(GPTSoVITSTTSProvider, "_ensure_service_available", fake_service)
    monkeypatch.setattr(GPTSoVITSTTSProvider, "_ensure_character_weights", fake_weights)

    ok, message = GPTSoVITSTTSProvider.ensure_ready(provider)

    assert ok
    assert "已就绪" in message
    assert calls == ["service", "weights"]


def test_gptsovits_ensure_ready_returns_service_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())

    def fake_service(_self, fail):  # type: ignore[no-untyped-def]
        fail("GPT-SoVITS 服务不可用")
        return False

    monkeypatch.setattr(GPTSoVITSTTSProvider, "_ensure_service_available", fake_service)

    ok, message = GPTSoVITSTTSProvider.ensure_ready(provider)

    assert not ok
    assert "服务不可用" in message


def test_gptsovits_ensure_ready_returns_weight_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())

    def fake_weights(_self, fail):  # type: ignore[no-untyped-def]
        fail("权重切换失败")
        return False

    monkeypatch.setattr(GPTSoVITSTTSProvider, "_ensure_service_available", lambda *_args: True)
    monkeypatch.setattr(GPTSoVITSTTSProvider, "_ensure_character_weights", fake_weights)

    ok, message = GPTSoVITSTTSProvider.ensure_ready(provider)

    assert not ok
    assert "权重切换失败" in message


def test_genie_service_probe_starts_local_server_when_port_is_down(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("genie_start") / "genie"
    (work_dir / "runtime").mkdir(parents=True)
    runtime_python = work_dir / "runtime" / "python.exe"
    _write_fake_runtime_python(runtime_python)
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(provider="genie-tts", work_dir=work_dir, api_url="http://127.0.0.1:9881/")
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []
    connection_calls = 0
    popen_calls: list[list[str]] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProcess:
        pid = 4321

        def poll(self) -> None:
            return None

    def fake_create_connection(*_args: object, **_kwargs: object) -> FakeConnection:
        nonlocal connection_calls
        connection_calls += 1
        if connection_calls == 1:
            raise OSError("connection refused")
        return FakeConnection()

    def fake_popen(args, **_kwargs):  # type: ignore[no-untyped-def]
        popen_calls.append(list(args))
        return FakeProcess()

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)
    monkeypatch.setattr("app.voice.tts.subprocess.Popen", fake_popen)
    monkeypatch.setattr(GenieTTSProvider, "_probe_genie_api", lambda *_args: True)

    assert GenieTTSProvider._ensure_service_available(provider, messages.append)
    assert messages == []
    assert len(popen_calls) == 1
    assert popen_calls[0][0] == str(work_dir / "runtime" / "python.exe")
    assert popen_calls[0][1] == "-c"
    assert "port=9881" in popen_calls[0][2]


def test_genie_ensure_ready_loads_model_and_reference(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GenieTTSProvider(_minimal_tts_settings(provider="genie-tts", api_url="http://127.0.0.1:9881/"))
    calls: list[str] = []

    def fake_service(_self, _fail):  # type: ignore[no-untyped-def]
        calls.append("service")
        return True

    def fake_model(_self, language, _fail):  # type: ignore[no-untyped-def]
        calls.append(f"model:{language}")
        return True

    def fake_reference(_self, reference, _fail):  # type: ignore[no-untyped-def]
        calls.append(f"reference:{reference.ref_text}")
        return True

    monkeypatch.setattr(GenieTTSProvider, "_ensure_service_available", fake_service)
    monkeypatch.setattr(GenieTTSProvider, "_ensure_character_model", fake_model)
    monkeypatch.setattr(GenieTTSProvider, "_ensure_reference_audio", fake_reference)

    ok, message = GenieTTSProvider.ensure_ready(provider)

    assert ok
    assert "已就绪" in message
    assert calls == ["service", "model:ja", "reference:テスト"]


def test_genie_ensure_ready_returns_reference_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GenieTTSProvider(_minimal_tts_settings(provider="genie-tts", api_url="http://127.0.0.1:9881/"))

    def fake_reference(_self, _reference, fail):  # type: ignore[no-untyped-def]
        fail("参考音频设置失败")
        return False

    monkeypatch.setattr(GenieTTSProvider, "_ensure_service_available", lambda *_args: True)
    monkeypatch.setattr(GenieTTSProvider, "_ensure_character_model", lambda *_args: True)
    monkeypatch.setattr(GenieTTSProvider, "_ensure_reference_audio", fake_reference)

    ok, message = GenieTTSProvider.ensure_ready(provider)

    assert not ok
    assert "参考音频设置失败" in message


def test_genie_service_probe_moves_to_fallback_port_when_9880_is_gptsovits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    work_dir = _runtime_root("genie_fallback_port") / "genie"
    (work_dir / "runtime").mkdir(parents=True)
    runtime_python = work_dir / "runtime" / "python.exe"
    _write_fake_runtime_python(runtime_python)
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(provider="genie-tts", work_dir=work_dir, api_url="http://127.0.0.1:9880/")
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []
    service_started = False
    popen_calls: list[list[str]] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProcess:
        pid = 4322

        def poll(self) -> None:
            return None

    def fake_create_connection(address, **_kwargs):  # type: ignore[no-untyped-def]
        _host, port = address
        if port == 9880:
            return FakeConnection()
        if port == 9881 and service_started:
            return FakeConnection()
        raise OSError("connection refused")

    def fake_popen(args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal service_started
        popen_calls.append(list(args))
        service_started = True
        return FakeProcess()

    def fake_probe_genie_api(self, _timeout):  # type: ignore[no-untyped-def]
        return str(self.settings.api_url).endswith(":9881/")

    monkeypatch.setattr("app.voice.tts.socket.create_connection", fake_create_connection)
    monkeypatch.setattr("app.voice.tts.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.voice.tts._can_bind_local_port", lambda *_args: True)
    monkeypatch.setattr(GenieTTSProvider, "_probe_genie_api", fake_probe_genie_api)

    assert GenieTTSProvider._ensure_service_available(provider, messages.append)
    assert messages == []
    assert provider.settings.api_url == "http://127.0.0.1:9881/"
    assert "port=9881" in popen_calls[0][2]


def test_genie_service_probe_rejects_non_genie_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings(provider="genie-tts", api_url="http://127.0.0.1:9880/")
    provider._service_checked = False
    provider._server_process = None
    messages: list[str] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("app.voice.tts.socket.create_connection", lambda *_args, **_kwargs: FakeConnection())
    monkeypatch.setattr(GenieTTSProvider, "_probe_genie_api", lambda *_args: False)

    assert not GenieTTSProvider._ensure_service_available(provider, messages.append)
    assert "不是 Genie TTS" in messages[0]


def test_genie_endpoint_replaces_tts_path() -> None:
    assert _build_genie_endpoint_url("http://127.0.0.1:9880/", "load_character") == "http://127.0.0.1:9880/load_character"
    assert _build_genie_endpoint_url("http://127.0.0.1:9880/tts", "set_reference_audio") == "http://127.0.0.1:9880/set_reference_audio"


def test_genie_audio_writer_accepts_raw_pcm() -> None:
    output = _runtime_root("genie_audio") / "out.wav"

    assert _write_genie_audio(b"\x00\x00\x10\x00\x00\x00", output)
    assert output.is_file()


def test_tts_provider_stop_local_service_terminates_owned_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    monkeypatch.setattr("app.voice.tts.sys.platform", "linux")

    class FakeProcess:
        pid = 9876

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: int) -> None:
            calls.append(f"wait:{timeout}")

    provider = types.SimpleNamespace(
        settings=_minimal_tts_settings(),
        _server_process=FakeProcess(),
    )

    GPTSoVITSTTSProvider._stop_local_service(provider)

    assert calls == ["terminate", "wait:5"]
    assert provider._server_process is None


def test_tts_provider_stop_local_service_uses_taskkill_tree_on_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[object] = []

    class FakeProcess:
        pid = 2468
        alive = True

        def poll(self):  # type: ignore[no-untyped-def]
            return None if self.alive else 0

        def wait(self, timeout: int) -> None:
            calls.append(f"wait:{timeout}")
            self.alive = False

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(args), kwargs.get("timeout")))
        return types.SimpleNamespace(returncode=0)

    provider = types.SimpleNamespace(
        settings=_minimal_tts_settings(),
        _server_process=FakeProcess(),
    )
    monkeypatch.setattr("app.voice.tts.sys.platform", "win32")
    monkeypatch.setattr("app.voice.tts.subprocess.run", fake_run)

    GPTSoVITSTTSProvider._stop_local_service(provider)

    assert calls[0] == (["taskkill", "/PID", "2468", "/T", "/F"], 5)
    assert calls[1] == "wait:5"
    assert "terminate" not in calls
    assert provider._server_process is None


def test_tts_weight_switch_error_includes_endpoint_and_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = types.SimpleNamespace()
    provider.settings = _minimal_tts_settings()
    messages: list[str] = []

    def fake_urlopen(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("bad weights")

    monkeypatch.setattr("app.voice.tts.urllib.request.urlopen", fake_urlopen)

    ok = GPTSoVITSTTSProvider._request_weight_switch(
        provider,
        "set_gpt_weights",
        Path("characters/sakura/voice/models/Sakura-e15.ckpt"),
        messages.append,
    )

    assert not ok
    assert "set_gpt_weights" in messages[0]
    assert "Sakura-e15.ckpt" in messages[0]
    assert "bad weights" in messages[0]


def test_local_tts_subprocess_env_uses_utf8_stdio_without_forcing_interpreter(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")

    env = _local_tts_subprocess_env()

    assert "PYTHONUTF8" not in env
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_local_tts_output_reader_writes_file_and_gui_log() -> None:
    clear_gui_logs()

    class Stream:
        def __init__(self) -> None:
            self.closed = False
            self._lines = iter(
                [
                    "########## 合成音频 ##########\n",
                    'INFO: 127.0.0.1:49840 - "POST /tts HTTP/1.1" 200 OK\n',
                ]
            )

        def __iter__(self):  # type: ignore[no-untyped-def]
            return self

        def __next__(self) -> str:
            return next(self._lines)

        def close(self) -> None:
            self.closed = True

    stream = Stream()
    log_path = _runtime_root("local_tts_output_reader") / "service.log"

    _read_local_tts_output(stream, log_path, "GPT-SoVITS")

    assert stream.closed
    assert "合成音频" in log_path.read_text(encoding="utf-8")
    records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_TTS)
    assert [record.message for record in records] == [
        "开始合成音频",
        "HTTP POST /tts -> 200 OK",
    ]
    clear_gui_logs()


def test_gptsovits_charmap_http_error_gets_actionable_message() -> None:
    message = _format_gpt_sovits_http_error(
        400,
        '{"message":"tts failed","Exception":"\'charmap\' codec can\'t encode characters"}',
    )

    assert "运行时编码不是 UTF-8" in message
    assert "由 Sakura 重新启动" in message
    assert "UTF-8 标准输入输出" in message
    assert "原始响应" in message


def test_gptsovits_provider_warms_up_qt_player_before_first_play(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.voice.tts as tts_module

    calls: list[str] = []

    class TimerStub:
        @staticmethod
        def singleShot(_interval: int, callback) -> None:  # type: ignore[no-untyped-def]
            calls.append("timer")
            callback()

    class SignalStub:
        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

    class AudioOutputStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            calls.append("audio")

    class MediaPlayerStub:
        class MediaStatus:
            EndOfMedia = object()

        class PlaybackState:
            PlayingState = object()

        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            calls.append("player")
            self.mediaStatusChanged = SignalStub()
            self.playbackStateChanged = SignalStub()
            self.errorOccurred = SignalStub()

        def setAudioOutput(self, _output: object) -> None:
            pass

        def setSource(self, _source: object) -> None:
            calls.append("source")

        def play(self) -> None:
            calls.append("play")

        def stop(self) -> None:
            pass

    monkeypatch.setattr(tts_module, "QTimer", TimerStub)
    monkeypatch.setattr(tts_module, "QAudioOutput", AudioOutputStub)
    monkeypatch.setattr(tts_module, "QMediaPlayer", MediaPlayerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())

    assert calls == []

    provider.warm_up_playback()

    assert calls == ["timer", "audio", "player"]

    provider._pending_audio.append((Path("dummy.wav"), None, None, None, ""))
    provider._play_next()

    assert calls == ["timer", "audio", "player", "source", "play"]


def test_tts_provider_treats_started_stopped_state_as_audio_finished(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.voice.tts as tts_module

    events: list[str] = []
    sources: list[object] = []
    cleaned: list[Path] = []

    class SignalStub:
        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

    class AudioOutputStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class MediaPlayerStub:
        class MediaStatus:
            EndOfMedia = object()

        class PlaybackState:
            PlayingState = object()
            StoppedState = object()

        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.mediaStatusChanged = SignalStub()
            self.playbackStateChanged = SignalStub()
            self.errorOccurred = SignalStub()

        def setAudioOutput(self, _output: object) -> None:
            pass

        def setSource(self, source: object) -> None:
            sources.append(source)

        def play(self) -> None:
            events.append("play")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(tts_module, "QAudioOutput", AudioOutputStub)
    monkeypatch.setattr(tts_module, "QMediaPlayer", MediaPlayerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    provider._playback_backend = "media_player"  # force media_player for this legacy test
    monkeypatch.setattr(provider, "_schedule_audio_cleanup", lambda path: cleaned.append(path))
    provider._pending_audio.append(
        (
            Path("first.wav"),
            lambda: events.append("first_started"),
            lambda: events.append("first_finished"),
            None,
            "",
        )
    )
    provider._pending_audio.append(
        (
            Path("second.wav"),
            lambda: events.append("second_started"),
            lambda: events.append("second_finished"),
            None,
            "",
        )
    )

    provider._play_next()
    provider._handle_playback_state(MediaPlayerStub.PlaybackState.PlayingState)
    provider._handle_playback_state(MediaPlayerStub.PlaybackState.StoppedState)

    assert events == ["play", "first_started", "stop", "first_finished", "play"]
    assert cleaned == [Path("first.wav")]
    assert provider._current_audio == Path("second.wav")
    assert len(sources) == 3


def test_tts_provider_finish_fallback_advances_queue_without_player_end_signal(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.voice.tts as tts_module

    events: list[str] = []
    cleaned: list[Path] = []
    timers: list[tuple[int, object]] = []
    root = _runtime_root("playback_finish_fallback")
    first_audio = root / "first.wav"
    second_audio = root / "second.wav"
    _write_silence_wav(first_audio, frame_count=1600, frame_rate=16000)
    _write_silence_wav(second_audio, frame_count=1600, frame_rate=16000)

    class TimerStub:
        @staticmethod
        def singleShot(delay_ms: int, callback: object) -> None:
            timers.append((delay_ms, callback))

    class SignalStub:
        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

    class AudioOutputStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class MediaPlayerStub:
        class MediaStatus:
            EndOfMedia = object()

        class PlaybackState:
            PlayingState = object()
            StoppedState = object()

        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.mediaStatusChanged = SignalStub()
            self.playbackStateChanged = SignalStub()
            self.errorOccurred = SignalStub()

        def setAudioOutput(self, _output: object) -> None:
            pass

        def setSource(self, _source: object) -> None:
            pass

        def play(self) -> None:
            events.append("play")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(tts_module, "QTimer", TimerStub)
    monkeypatch.setattr(tts_module, "QAudioOutput", AudioOutputStub)
    monkeypatch.setattr(tts_module, "QMediaPlayer", MediaPlayerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    provider._playback_backend = "media_player"  # force media_player for this legacy test
    monkeypatch.setattr(provider, "_schedule_audio_cleanup", lambda path: cleaned.append(path))
    provider._pending_audio.append(
        (
            first_audio,
            lambda: events.append("first_started"),
            lambda: events.append("first_finished"),
            None,
            "",
        )
    )
    provider._pending_audio.append(
        (
            second_audio,
            lambda: events.append("second_started"),
            lambda: events.append("second_finished"),
            None,
            "",
        )
    )

    provider._play_next()
    provider._handle_playback_state(MediaPlayerStub.PlaybackState.PlayingState)
    assert timers[0][0] == 2000

    timers[0][1]()

    assert events == ["play", "first_started", "stop", "first_finished", "play"]
    assert cleaned == [first_audio]
    assert provider._current_audio == second_audio
    assert len(timers) == 2


def test_voice_playback_controller_falls_back_to_subtitle_callbacks_on_tts_error() -> None:
    from app.llm.chat_reply import ChatSegment

    class FailingTTS:
        def speak(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("tts down")

    events: list[str] = []
    errors: list[str] = []
    controller = VoicePlaybackController(
        FailingTTS(),
        lambda *_args, **_kwargs: None,
        on_error=errors.append,
    )  # type: ignore[arg-type]

    controller.speak_segment(
        ChatSegment("こんにちは", "中性"),
        1,
        on_started=lambda: events.append("started"),
        on_finished=lambda: events.append("finished"),
    )

    assert events == ["started", "finished"]
    assert errors == ["播放失败，已继续显示字幕：tts down"]


def test_voice_playback_controller_skips_chinese_text_for_japanese_tts() -> None:
    from app.llm.chat_reply import ChatSegment

    class RecordingTTS:
        def __init__(self) -> None:
            self.speak_calls = 0

        def speak(self, *_args: object, **_kwargs: object) -> None:
            self.speak_calls += 1

    events: list[str] = []
    stages: list[str] = []
    tts = RecordingTTS()
    controller = VoicePlaybackController(
        tts,
        lambda stage, _payload=None: stages.append(stage),
        target_text_lang_getter=lambda: "ja",
    )  # type: ignore[arg-type]

    controller.speak_segment(
        ChatSegment("这是中文，不能进 TTS。", "中性"),
        1,
        on_started=lambda: events.append("started"),
        on_finished=lambda: events.append("finished"),
    )

    assert tts.speak_calls == 0
    assert events == ["started", "finished"]
    assert "tts_skipped_language_guard" in stages


def test_voice_playback_controller_skips_prepare_for_chinese_text() -> None:
    from app.llm.chat_reply import ChatSegment

    class RecordingTTS:
        def __init__(self) -> None:
            self.prepare_calls = 0

        def prepare(self, *_args: object, **_kwargs: object) -> TTSPreparedAudio:
            self.prepare_calls += 1
            return TTSPreparedAudio(text="dummy")

        def discard_prepared(self, *_args: object, **_kwargs: object) -> None:
            pass

    tts = RecordingTTS()
    controller = VoicePlaybackController(
        tts,
        lambda *_args, **_kwargs: None,
        target_text_lang_getter=lambda: "ja",
    )  # type: ignore[arg-type]

    controller.prepare_next(ChatSegment("这是中文，不能进 TTS。", "中性"))

    assert tts.prepare_calls == 0


def test_voice_playback_controller_allows_japanese_speak_and_prepare() -> None:
    from app.llm.chat_reply import ChatSegment

    class RecordingTTS:
        def __init__(self) -> None:
            self.speak_calls = 0
            self.prepare_calls = 0

        def speak(
            self,
            *_args: object,
            on_started=None,  # type: ignore[no-untyped-def]
            on_finished=None,  # type: ignore[no-untyped-def]
            **_kwargs: object,
        ) -> None:
            self.speak_calls += 1
            if on_started is not None:
                on_started()
            if on_finished is not None:
                on_finished()

        def prepare(self, text: str, *_args: object, **_kwargs: object) -> TTSPreparedAudio:
            self.prepare_calls += 1
            return TTSPreparedAudio(text=text)

        def discard_prepared(self, *_args: object, **_kwargs: object) -> None:
            pass

    events: list[str] = []
    tts = RecordingTTS()
    controller = VoicePlaybackController(tts, lambda *_args, **_kwargs: None)  # type: ignore[arg-type]

    controller.speak_segment(
        ChatSegment("うん。大丈夫。", "中性"),
        1,
        on_started=lambda: events.append("started"),
        on_finished=lambda: events.append("finished"),
    )
    controller.prepare_next(ChatSegment("次の一段。", "中性"))

    assert tts.speak_calls == 1
    assert tts.prepare_calls == 1
    assert events == ["started", "finished"]


def test_voice_playback_controller_uses_prepared_japanese_audio() -> None:
    from app.llm.chat_reply import ChatSegment

    class RecordingTTS:
        def __init__(self) -> None:
            self.prepared = TTSPreparedAudio(text="次の一段。")
            self.speak_prepared_calls = 0

        def prepare(self, *_args: object, **_kwargs: object) -> TTSPreparedAudio:
            return self.prepared

        def speak_prepared(
            self,
            _handle: TTSPreparedAudio,
            on_started=None,  # type: ignore[no-untyped-def]
            on_finished=None,  # type: ignore[no-untyped-def]
        ) -> None:
            self.speak_prepared_calls += 1
            if on_started is not None:
                on_started()
            if on_finished is not None:
                on_finished()

        def discard_prepared(self, *_args: object, **_kwargs: object) -> None:
            pass

    events: list[str] = []
    segment = ChatSegment("次の一段。", "中性")
    tts = RecordingTTS()
    controller = VoicePlaybackController(tts, lambda *_args, **_kwargs: None)  # type: ignore[arg-type]

    controller.prepare_next(segment)
    controller.speak_segment(
        segment,
        1,
        on_started=lambda: events.append("started"),
        on_finished=lambda: events.append("finished"),
    )

    assert tts.speak_prepared_calls == 1
    assert events == ["started", "finished"]


def test_voice_playback_controller_ignores_prepare_error() -> None:
    from app.llm.chat_reply import ChatSegment

    class FailingPrepareTTS:
        def prepare(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("prepare down")

        def discard_prepared(self, *_args: object, **_kwargs: object) -> None:
            pass

    errors: list[str] = []
    controller = VoicePlaybackController(
        FailingPrepareTTS(),
        lambda *_args, **_kwargs: None,
        on_error=errors.append,
    )  # type: ignore[arg-type]

    controller.prepare_next(ChatSegment("次の一段", "中性"))

    assert errors == ["预生成失败，已继续字幕流程：prepare down"]


def _minimal_tts_settings(
    work_dir: Path | None = None,
    *,
    provider: str = "gpt-sovits",
    api_url: str = "http://127.0.0.1:9880/tts",
    python_path: Path | None = None,
    tts_config_path: Path | None = None,
) -> GPTSoVITSTTSSettings:
    root = _runtime_root("minimal_tts")
    ref_audio_path = root / "voice" / "refs" / "tone_refs" / "neutral.wav"
    ref_audio_path.parent.mkdir(parents=True)
    ref_audio_path.write_bytes(b"wav")
    ref_text_path = root / "voice" / "refs" / "ref.txt"
    ref_text_path.write_text(
        "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
        encoding="utf-8",
    )
    return GPTSoVITSTTSSettings(
        enabled=True,
        provider=provider,
        api_url=api_url,
        ref_audio_path=ref_audio_path,
        ref_text_path=ref_text_path,
        ref_text="テスト",
        work_dir=work_dir,
        python_path=python_path,
        tts_config_path=tts_config_path,
        character_name="夜乃桜",
        onnx_model_dir=Path("data/tts_bundles/onnx/sakura") if provider == "genie-tts" else None,
        ref_lang="ja",
        text_lang="ja",
        timeout_seconds=1,
    )


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_fake_runtime_python(path: Path, content: str = "fake") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_silence_wav(path: Path, *, frame_count: int, frame_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)

# === 新增：双后端与播放链路测试 ===

def test_speak_prepared_cancelled_emits_callbacks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """cancelled prepared audio must trigger started/finished callbacks."""
    import app.voice.tts as tts_module

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    started_calls: list[object] = []
    finished_calls: list[object] = []

    # Replace the whole signal attribute on the instance
    monkeypatch.setattr(provider, "_started", types.SimpleNamespace(emit=lambda cb: started_calls.append(cb) if cb is not None else None))
    monkeypatch.setattr(provider, "_finished", types.SimpleNamespace(emit=lambda cb: finished_calls.append(cb) if cb is not None else None))

    handle = TTSPreparedAudio(text="test", tone="neutral")
    handle.cancelled = True

    cb_started = lambda: None
    cb_finished = lambda: None

    provider.speak_prepared(handle, on_started=cb_started, on_finished=cb_finished)

    assert cb_started in started_calls
    assert cb_finished in finished_calls

def test_speak_prepared_failed_emits_callbacks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """failed prepared audio must trigger started/finished callbacks."""
    import app.voice.tts as tts_module

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    started_calls: list[object] = []
    finished_calls: list[object] = []

    # Replace the whole signal attribute on the instance
    monkeypatch.setattr(provider, "_started", types.SimpleNamespace(emit=lambda cb: started_calls.append(cb) if cb is not None else None))
    monkeypatch.setattr(provider, "_finished", types.SimpleNamespace(emit=lambda cb: finished_calls.append(cb) if cb is not None else None))

    handle = TTSPreparedAudio(text="test", tone="neutral")
    handle.failed = True
    handle.text = "test"

    cb_started = lambda: None
    cb_finished = lambda: None

    provider.speak_prepared(handle, on_started=cb_started, on_finished=cb_finished)

    assert cb_started in started_calls
    assert cb_finished in finished_calls

def test_finish_current_audio_is_idempotent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_finish_current_audio repeated calls should be blocked by _finishing_audio guard."""
    import app.voice.tts as tts_module

    class SignalStub:
        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

    class AudioOutputStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class MediaPlayerStub:
        class MediaStatus:
            EndOfMedia = object()
        class PlaybackState:
            PlayingState = object()
            StoppedState = object()
        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.mediaStatusChanged = SignalStub()
            self.playbackStateChanged = SignalStub()
            self.errorOccurred = SignalStub()

        def setAudioOutput(self, _output: object) -> None:
            pass

        def setSource(self, _source: object) -> None:
            pass

        def play(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(tts_module, "QAudioOutput", AudioOutputStub)
    monkeypatch.setattr(tts_module, "QMediaPlayer", MediaPlayerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    cleanup_calls: list[Path] = []
    monkeypatch.setattr(provider, "_schedule_audio_cleanup", lambda path: cleanup_calls.append(path))

    # Replace signal attribute on instance
    finished_calls: list[object] = []
    monkeypatch.setattr(provider, "_finished", types.SimpleNamespace(emit=lambda cb: finished_calls.append(cb) if cb is not None else None))

    root = _runtime_root("finish_idempotent")
    audio_path = root / "test.wav"
    _write_silence_wav(audio_path, frame_count=1600, frame_rate=16000)

    provider._current_audio = audio_path
    provider._current_finished = lambda: None
    provider._current_started = lambda: None
    provider._current_started_emitted = False

    # First finish
    provider._finish_current_audio("normal")
    assert provider._current_audio is None
    assert len(cleanup_calls) == 1
    assert len(finished_calls) == 1

    # Second call - _current_audio is None so returns early
    provider._finish_current_audio("duplicate")
    assert provider._current_audio is None

def test_enqueue_audio_dispatches_play_next_via_timer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_enqueue_audio 应使用 QTimer.singleShot(0, self._play_next) 触发播放。"""
    import app.voice.tts as tts_module

    timer_calls: list[tuple[int, object]] = []

    class TimerStub:
        @staticmethod
        def singleShot(delay_ms: int, callback: object) -> None:
            timer_calls.append((delay_ms, callback))

    monkeypatch.setattr(tts_module, "QTimer", TimerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    play_next_calls: list = []
    monkeypatch.setattr(provider, "_play_next", lambda: play_next_calls.append(True))

    root = _runtime_root("enqueue_dispatch")
    audio_path = root / "test.wav"
    _write_silence_wav(audio_path, frame_count=1600, frame_rate=16000)

    provider._enqueue_audio(str(audio_path), None, None)

    assert len(timer_calls) == 1
    assert timer_calls[0][0] == 0
    # 执行回调
    timer_calls[0][1]()
    assert len(play_next_calls) == 1


def test_handle_media_status_passes_reason_to_finish(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_handle_media_status EndOfMedia 分支应传入 reason 字符串。"""
    import app.voice.tts as tts_module

    class SignalStub:
        def connect(self, *_args: object, **_kwargs: object) -> None:
            pass

    class AudioOutputStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class MediaPlayerStub:
        class MediaStatus:
            EndOfMedia = object()
        class PlaybackState:
            PlayingState = object()
            StoppedState = object()
        class Error:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.mediaStatusChanged = SignalStub()
            self.playbackStateChanged = SignalStub()
            self.errorOccurred = SignalStub()

        def setAudioOutput(self, _output: object) -> None:
            pass

        def setSource(self, _source: object) -> None:
            pass

        def play(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(tts_module, "QAudioOutput", AudioOutputStub)
    monkeypatch.setattr(tts_module, "QMediaPlayer", MediaPlayerStub)

    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    finish_reasons: list[str] = []
    orig_finish = provider._finish_current_audio

    def capture_finish(reason: str = "normal") -> None:
        finish_reasons.append(reason)
        return orig_finish(reason)

    monkeypatch.setattr(provider, "_finish_current_audio", capture_finish)
    monkeypatch.setattr(provider, "_schedule_audio_cleanup", lambda _path: None)

    root = _runtime_root("media_status_reason")
    audio_path = root / "test.wav"
    _write_silence_wav(audio_path, frame_count=1600, frame_rate=16000)

    provider._current_audio = audio_path
    provider._current_finished = lambda: None
    provider._current_started = lambda: None

    provider._handle_media_status(MediaPlayerStub.MediaStatus.EndOfMedia)

    assert finish_reasons == ["end_of_media"]


def test_playback_backend_is_configurable() -> None:
    """playback backend should be readable from settings, defaulting to media_player."""
    from dataclasses import replace as dc_replace
    from app.voice.tts import (
        TTS_PLAYBACK_BACKEND_MEDIA_PLAYER,
        TTS_PLAYBACK_BACKEND_AUDIO_SINK,
    )

    # Default
    settings = _minimal_tts_settings()
    assert settings.playback_backend == ""
    provider = GPTSoVITSTTSProvider(settings)
    assert provider._playback_backend == TTS_PLAYBACK_BACKEND_AUDIO_SINK

    # Explicitly set audio_sink
    sink_settings = dc_replace(settings, playback_backend=TTS_PLAYBACK_BACKEND_AUDIO_SINK)
    sink_provider = GPTSoVITSTTSProvider(sink_settings)
    assert sink_provider._playback_backend == TTS_PLAYBACK_BACKEND_AUDIO_SINK


# === 新增：TTS 缓存目录（data/cache/tts）测试 ===

def test_resolve_tts_cache_dir_creates_data_cache_tts() -> None:
    """缓存目录应位于 base_dir/data/cache/tts 且被自动创建。"""
    root = _runtime_root("tts_cache_resolve")

    cache_dir = _resolve_tts_cache_dir(root)

    assert cache_dir == root / "data" / "cache" / "tts"
    assert cache_dir.is_dir()


def test_purge_tts_cache_removes_residual_files_keeps_dir() -> None:
    """启动清理应删除残留临时文件，但保留缓存目录与其中的子目录。"""
    root = _runtime_root("tts_cache_purge")
    cache_dir = _resolve_tts_cache_dir(root)
    (cache_dir / "sakura_tts_a.wav").write_bytes(b"x")
    (cache_dir / "sakura_genie_tts_b.wav").write_bytes(b"y")
    sub_dir = cache_dir / "keep"
    sub_dir.mkdir()

    purge_tts_cache(root)

    assert cache_dir.is_dir()
    assert sub_dir.is_dir()  # 非文件项不应被删除
    assert not any(entry.is_file() for entry in cache_dir.iterdir())
