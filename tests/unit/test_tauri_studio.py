from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


def _mark_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_minimal_character(root: Path, character_id: str = "sakura") -> None:
    package_dir = root / "characters" / character_id
    package_dir.mkdir(parents=True)
    (package_dir / "card.md").write_text("card", encoding="utf-8")
    (package_dir / "portrait.png").write_bytes(b"png")
    (package_dir / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_resolve_tauri_studio_binary_uses_env_and_platform(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_studio as tauri_studio

    custom = tmp_path / "custom-studio.exe"
    custom.write_text("bin", encoding="utf-8")
    _mark_executable(custom)
    assert tauri_studio.resolve_tauri_studio_binary(
        tmp_path,
        environ={tauri_studio.TAURI_STUDIO_BIN_ENV: str(custom)},
    ) == custom

    release = tmp_path / "tools" / "studio-tauri" / "src-tauri" / "target" / "release"
    release.mkdir(parents=True)
    win_bin = release / "sakura-studio.exe"
    unix_bin = release / "sakura-studio"
    win_bin.write_text("win", encoding="utf-8")
    unix_bin.write_text("unix", encoding="utf-8")
    _mark_executable(unix_bin)

    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    assert tauri_studio.resolve_tauri_studio_binary(tmp_path, environ={}) == win_bin
    monkeypatch.setattr(tauri_studio.sys, "platform", "darwin")
    assert tauri_studio.resolve_tauri_studio_binary(tmp_path, environ={}) == unix_bin


def test_tauri_binary_resolvers_reject_non_executable_posix_files(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings
    import app.ui.tauri_studio as tauri_studio

    settings = tmp_path / "custom-settings"
    studio = tmp_path / "custom-studio"
    settings.write_text("settings", encoding="utf-8")
    studio.write_text("studio", encoding="utf-8")
    monkeypatch.setattr(tauri_settings.sys, "platform", "darwin")
    monkeypatch.setattr(tauri_studio.sys, "platform", "darwin")
    monkeypatch.setattr(tauri_settings.os, "access", lambda _path, _mode: False)
    monkeypatch.setattr(tauri_studio.os, "access", lambda _path, _mode: False)

    assert tauri_settings.resolve_tauri_settings_binary(
        tmp_path,
        environ={tauri_settings.TAURI_SETTINGS_BIN_ENV: str(settings)},
    ) is None
    assert tauri_studio.resolve_tauri_studio_binary(
        tmp_path,
        environ={tauri_studio.TAURI_STUDIO_BIN_ENV: str(studio)},
    ) is None


def test_build_tauri_studio_request_contains_characters_and_nonce(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService
    from app.ui.tauri_studio import build_tauri_studio_request
    from app.ui.theme import DEFAULT_THEME_SETTINGS, THEME_COLOR_FIELDS, theme_to_mapping

    _write_minimal_character(tmp_path)

    request = build_tauri_studio_request(tmp_path, initial_character_id="sakura", nonce="nonce")

    assert request["version"] == 1
    assert request["nonce"] == "nonce"
    assert request["initial_character_id"] == "sakura"
    assert request["characters"][0]["id"] == "sakura"
    assert request["theme"] == theme_to_mapping(DEFAULT_THEME_SETTINGS)
    assert request["theme_defaults"] == theme_to_mapping(DEFAULT_THEME_SETTINGS)
    assert request["theme_fields"] == [
        {"id": field, "label": label}
        for field, label, _default in THEME_COLOR_FIELDS
    ]
    assert CharacterStudioService(tmp_path).list_characters(current_character_id="sakura")[0]["is_current"] is True


def test_portrait_default_radio_uses_themeable_custom_style() -> None:
    stylesheet = (
        Path(__file__).parents[2] / "tools" / "studio-tauri" / "frontend" / "styles.css"
    ).read_text(encoding="utf-8")

    assert "--portrait-radio-size:" in stylesheet
    assert "--portrait-radio-fill:" in stylesheet
    assert "appearance: none;" in stylesheet
    assert "padding: 0;" in stylesheet
    assert ".portrait-default-control input:checked::before" in stylesheet


def test_release_workflows_build_and_package_both_tauri_apps() -> None:
    root = Path(__file__).parents[2]
    for relative_path in (".github/workflows/package.yml", ".github/workflows/release.yml"):
        workflow = (root / relative_path).read_text(encoding="utf-8")

        assert "tools/settings-tauri/src-tauri" in workflow
        assert "tools/studio-tauri/src-tauri" in workflow
        assert "cargo build --release --locked" in workflow
        assert "target/release/sakura-settings.exe" in workflow
        assert "target/release/sakura-studio.exe" in workflow
        assert "target/release/sakura-settings" in workflow
        assert "target/release/sakura-studio" in workflow
        assert 'chmod +x "target/release/$binary"' in workflow


def test_character_selector_distinguishes_workspace_and_published_roles() -> None:
    frontend = Path(__file__).parents[2] / "tools" / "studio-tauri" / "frontend"
    index = (frontend / "index.html").read_text(encoding="utf-8")
    source = (frontend / "studio.js").read_text(encoding="utf-8")
    stylesheet = (frontend / "styles.css").read_text(encoding="utf-8")

    assert 'id="saveDraftButton"' in index
    assert 'id="publishButton"' in index
    assert "工作区" in source
    assert "已发布角色" in source
    assert "（草稿）" not in source
    assert "character.is_installed" in source
    assert "function saveWorkspaceDraft()" in source
    assert "function publishCharacter()" in source
    assert "--studio-dirty-dot-size:" in stylesheet
    assert ".custom-select__dirty-dot" in stylesheet
    assert "background: var(--sakura-primary);" in stylesheet


def test_dispatch_tauri_studio_rpc_picks_screen_color(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_studio as tauri_studio

    monkeypatch.setattr(tauri_studio, "pick_screen_color", lambda: "#112233")
    assert tauri_studio.dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.pick_screen_color",
        {},
    ) == {"color": "#112233"}

    monkeypatch.setattr(tauri_studio, "pick_screen_color", lambda: None)
    assert tauri_studio.dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.pick_screen_color",
        {},
    ) == {"cancelled": True}


def test_dispatch_tauri_studio_rpc_routes_core_methods(tmp_path: Path) -> None:
    from app.ui.tauri_studio import dispatch_tauri_studio_rpc

    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    created = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )
    draft_dir = created["package_dir"]
    portrait = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_portrait",
        {"package_dir": draft_dir, "path": str(source), "label": "default"},
    )
    doc = created["doc"]
    doc["card_text"] = "card"
    doc["default_portrait"] = portrait["relative_path"]
    saved = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.save_character",
        {"package_dir": draft_dir, "doc": doc, "current_character_id": "sakura"},
    )

    assert saved["saved_character_id"] == "demo"
    assert saved["current_character_id"] == "sakura"
    assert (tmp_path / "characters" / "demo" / "character.json").exists()


def test_dispatch_tauri_studio_rpc_routes_voice_asset_methods(tmp_path: Path) -> None:
    from app.ui.tauri_studio import dispatch_tauri_studio_rpc

    model_source = tmp_path / "model.ckpt"
    audio_source = tmp_path / "neutral.wav"
    model_source.write_bytes(b"model")
    audio_source.write_bytes(b"audio")
    created = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )

    model = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_voice_model",
        {
            "package_dir": created["package_dir"],
            "path": str(model_source),
            "model_type": "gpt",
        },
    )
    audio = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_reference_audio",
        {"package_dir": created["package_dir"], "path": str(audio_source)},
    )
    preview = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.load_reference_audio_preview",
        {
            "package_dir": created["package_dir"],
            "relative_path": audio["relative_path"],
        },
    )

    assert model["relative_path"].endswith("model.ckpt")
    assert audio["relative_path"].endswith("neutral.wav")
    assert preview["data_url"].startswith("data:audio/wav;base64,")


def test_dispatch_tauri_studio_rpc_routes_workspace_and_folder_methods(tmp_path: Path) -> None:
    from app.ui.tauri_studio import dispatch_tauri_studio_rpc

    portraits = tmp_path / "portraits"
    audios = tmp_path / "audios"
    portraits.mkdir()
    audios.mkdir()
    (portraits / "neutral.png").write_bytes(b"png")
    (audios / "neutral.wav").write_bytes(b"wav")
    created = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )
    workspace_id = created["workspace_id"]

    portrait_result = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_portrait_folder",
        {"workspace_id": workspace_id, "path": str(portraits)},
    )
    audio_result = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.import_reference_audio_folder",
        {"workspace_id": workspace_id, "path": str(audios), "ref_lang": "JA"},
    )
    doc = created["doc"]
    doc["default_portrait"] = portrait_result["items"][0]["relative_path"]
    autosaved = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.save_workspace_draft",
        {"workspace_id": workspace_id, "doc": doc},
    )

    assert portrait_result["items"][0]["suggested_label"] == "neutral"
    assert audio_result["items"][0]["ref_lang"] == "JA"
    assert autosaved["is_dirty"] is True

    discarded = dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.discard_draft",
        {"workspace_id": workspace_id},
    )
    assert discarded["discarded_character_id"] == "demo"


def test_tauri_studio_process_writes_rpc_response_line(tmp_path: Path) -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import (
        TAURI_STUDIO_RPC_MARKER,
        TAURI_STUDIO_RPC_RESULT_MARKER,
        TauriStudioProcess,
    )

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk
            self.writes: list[bytes] = []

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    request = {"id": "rpc-1", "method": "studio.list_characters", "params": {}}
    fake = FakeQProcess(
        (TAURI_STUDIO_RPC_MARKER + json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    )
    process = TauriStudioProcess(tmp_path, initial_character_id="")
    process._process = fake

    process._handle_stdout()

    line = b"".join(fake.writes).decode("utf-8").strip()
    assert line.startswith(TAURI_STUDIO_RPC_RESULT_MARKER)
    payload = json.loads(line[len(TAURI_STUDIO_RPC_RESULT_MARKER):])
    assert payload["id"] == "rpc-1"
    assert payload["ok"] is True
    assert payload["result"]["characters"] == []


def test_tauri_studio_process_schedules_bounded_focus_retries_after_start(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_studio as tauri_studio

    scheduled: list[tuple[int, object]] = []

    class FakeQProcess:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    monkeypatch.setattr(
        tauri_studio.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    process = tauri_studio.TauriStudioProcess(tmp_path)
    fake = FakeQProcess()
    process._process = fake
    process._request_payload = b'{"version": 1}'

    process._handle_started()

    assert fake.writes == [b'{"version": 1}\n']
    assert [delay for delay, _callback in scheduled] == list(
        tauri_studio.STUDIO_FOCUS_RETRY_DELAYS_MS
    )


def test_tauri_studio_process_does_not_schedule_focus_retries_off_windows(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_studio as tauri_studio

    scheduled: list[int] = []

    class FakeQProcess:
        def write(self, data: bytes) -> int:
            return len(data)

    monkeypatch.setattr(tauri_studio.sys, "platform", "linux")
    monkeypatch.setattr(
        tauri_studio.QTimer,
        "singleShot",
        lambda delay, _callback: scheduled.append(delay),
    )
    process = tauri_studio.TauriStudioProcess(tmp_path)
    process._process = FakeQProcess()
    process._request_payload = b"{}"

    process._handle_started()

    assert scheduled == []


def test_tauri_studio_process_stops_startup_focus_retries_after_success(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import TauriStudioProcess

    process = TauriStudioProcess(tmp_path)
    fake = object()
    process._process = fake
    focus_results = iter((False, True))
    focus_calls: list[bool] = []

    def focus_window() -> bool:
        focus_calls.append(True)
        return next(focus_results)

    monkeypatch.setattr(process, "focus_window", focus_window)

    process._try_startup_focus(fake)
    process._try_startup_focus(fake)
    process._try_startup_focus(fake)

    assert focus_calls == [True, True]
    assert process._startup_focus_complete is True


def test_tauri_studio_process_ignores_stale_startup_focus_callbacks(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import TauriStudioProcess

    process = TauriStudioProcess(tmp_path)
    original = object()
    focus_calls: list[bool] = []
    monkeypatch.setattr(
        process,
        "focus_window",
        lambda: focus_calls.append(True) or True,
    )

    process._process = original
    process._done = True
    process._try_startup_focus(original)

    process._done = False
    process._process = object()
    process._try_startup_focus(original)

    assert focus_calls == []


def test_tauri_studio_process_focus_uses_forced_foreground_restore(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings
    import app.ui.tauri_studio as tauri_studio

    calls: list[tuple[int, bool]] = []

    class FakeQProcess:
        def processId(self) -> int:  # noqa: N802
            return 4321

    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    monkeypatch.setattr(
        tauri_settings,
        "_restore_windows_for_pid",
        lambda pid, *, force_foreground=False: calls.append((pid, force_foreground)) or True,
    )
    process = tauri_studio.TauriStudioProcess(tmp_path)
    process._process = FakeQProcess()

    assert process.focus_window() is True
    assert calls == [(4321, True)]


def test_tauri_process_focus_sends_cross_platform_control_message(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings
    import app.ui.tauri_studio as tauri_studio
    from app.agent.screen_awareness import ScreenAwarenessSettings

    class FakeQProcess:
        def __init__(self, result: int) -> None:
            self.result = result
            self.writes: list[bytes] = []

        def write(self, payload: bytes) -> int:
            self.writes.append(bytes(payload))
            return self.result

    monkeypatch.setattr(tauri_settings.sys, "platform", "darwin")
    monkeypatch.setattr(tauri_studio.sys, "platform", "darwin")

    studio_process = tauri_studio.TauriStudioProcess(tmp_path)
    studio_qprocess = FakeQProcess(1)
    studio_process._process = studio_qprocess
    assert studio_process.focus_window() is True
    assert studio_qprocess.writes == [
        b'@@SAKURA_STUDIO_CONTROL@@{"action": "focus"}\n'
    ]

    settings_process = tauri_settings.TauriSettingsProcess(
        base_dir=tmp_path,
        settings=ScreenAwarenessSettings(),
    )
    settings_qprocess = FakeQProcess(-1)
    settings_process._process = settings_qprocess
    assert settings_process.focus_window() is False
    assert settings_qprocess.writes == [
        b'@@SAKURA_SETTINGS_CONTROL@@{"action": "focus"}\n'
    ]


def test_tauri_studio_process_focus_tolerates_deleted_qprocess(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_studio as tauri_studio

    class DeletedQProcess:
        def processId(self) -> int:  # noqa: N802
            raise RuntimeError("wrapped C/C++ object has been deleted")

    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    process = tauri_studio.TauriStudioProcess(tmp_path)
    process._process = DeletedQProcess()

    assert process.focus_window() is False


def test_restore_windows_for_pid_uses_temporary_topmost_pulse(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import ctypes

    from app.ui.tauri_settings import _restore_windows_for_pid

    calls: list[tuple[str, int]] = []

    class FakeUser32:
        def EnumWindows(self, callback, _lparam) -> int:  # noqa: N802, ANN001
            callback(100, 0)
            return 1

        def IsWindowVisible(self, _hwnd: int) -> int:  # noqa: N802
            return 1

        def GetWindow(self, _hwnd: int, _command: int) -> int:  # noqa: N802
            return 0

        def GetWindowThreadProcessId(self, _hwnd: int, pid_pointer) -> int:  # noqa: N802, ANN001
            pid_pointer._obj.value = 4321
            return 1

        def IsIconic(self, _hwnd: int) -> int:  # noqa: N802
            return 0

        def SetWindowPos(self, _hwnd: int, insert_after, *_args) -> int:  # noqa: N802, ANN001
            calls.append(("position", int(insert_after.value)))
            return 1

        def BringWindowToTop(self, _hwnd: int) -> int:  # noqa: N802
            calls.append(("bring", 0))
            return 1

        def SetForegroundWindow(self, _hwnd: int) -> int:  # noqa: N802
            calls.append(("foreground", 0))
            return 1

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=FakeUser32()), raising=False)
    monkeypatch.setattr(
        ctypes,
        "WINFUNCTYPE",
        lambda *_args: lambda callback: callback,
        raising=False,
    )

    assert _restore_windows_for_pid(4321, force_foreground=True) is True
    assert calls == [
        ("position", ctypes.c_void_p(-1).value),
        ("position", ctypes.c_void_p(-2).value),
        ("bring", 0),
        ("foreground", 0),
    ]


def test_restore_windows_for_pid_reports_failed_topmost_cleanup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import ctypes

    from app.ui.tauri_settings import _restore_windows_for_pid

    class FakeUser32:
        def EnumWindows(self, callback, _lparam) -> int:  # noqa: N802, ANN001
            callback(100, 0)
            return 1

        def IsWindowVisible(self, _hwnd: int) -> int:  # noqa: N802
            return 1

        def GetWindow(self, _hwnd: int, _command: int) -> int:  # noqa: N802
            return 0

        def GetWindowThreadProcessId(self, _hwnd: int, pid_pointer) -> int:  # noqa: N802, ANN001
            pid_pointer._obj.value = 4321
            return 1

        def IsIconic(self, _hwnd: int) -> int:  # noqa: N802
            return 0

        def SetWindowPos(self, _hwnd: int, insert_after, *_args) -> int:  # noqa: N802, ANN001
            return insert_after.value != ctypes.c_void_p(-2).value

        def BringWindowToTop(self, _hwnd: int) -> int:  # noqa: N802
            return 1

        def SetForegroundWindow(self, _hwnd: int) -> int:  # noqa: N802
            return 1

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=FakeUser32()), raising=False)
    monkeypatch.setattr(
        ctypes,
        "WINFUNCTYPE",
        lambda *_args: lambda callback: callback,
        raising=False,
    )

    assert _restore_windows_for_pid(4321, force_foreground=True) is False


def test_tauri_studio_process_start_returns_false_on_synchronous_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_studio as tauri_studio

    binary_name = (
        "sakura-studio.exe"
        if tauri_studio.sys.platform == "win32"
        else "sakura-studio"
    )
    binary = (
        tmp_path
        / "tools"
        / "studio-tauri"
        / "src-tauri"
        / "target"
        / "debug"
        / binary_name
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"not executable")
    _mark_executable(binary)
    failed_to_start = tauri_studio.QProcess.ProcessError.FailedToStart

    class SignalStub:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.callbacks.append(callback)

        def emit(self, *args) -> None:  # type: ignore[no-untyped-def]
            for callback in list(self.callbacks):
                callback(*args)

    class FakeQProcess:
        def __init__(self, _parent) -> None:  # type: ignore[no-untyped-def]
            self.started = SignalStub()
            self.finished = SignalStub()
            self.errorOccurred = SignalStub()
            self.readyReadStandardOutput = SignalStub()

        def setProgram(self, _program: str) -> None:  # noqa: N802
            pass

        def setArguments(self, _arguments: list[str]) -> None:  # noqa: N802
            pass

        def setWorkingDirectory(self, _directory: str) -> None:  # noqa: N802
            pass

        def setProcessEnvironment(self, _environment) -> None:  # noqa: N802, ANN001
            pass

        def start(self) -> None:
            self.errorOccurred.emit(failed_to_start)

    monkeypatch.setattr(tauri_studio, "QProcess", FakeQProcess)
    process = tauri_studio.TauriStudioProcess(tmp_path)
    failures: list[str] = []
    process.failed.connect(failures.append)

    assert process.start() is False
    app.processEvents()
    assert process._process is None
    assert failures and "启动失败" in failures[0]


def test_tauri_studio_process_reports_abnormal_exit(tmp_path: Path) -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import TauriStudioProcess
    from PySide6.QtCore import QProcess

    class FakeQProcess:
        def readAllStandardOutput(self) -> bytes:  # noqa: N802
            return b""

    process = TauriStudioProcess(tmp_path)
    process._process = FakeQProcess()
    failures: list[str] = []
    closed: list[bool] = []
    process.failed.connect(failures.append)
    process.closed.connect(lambda: closed.append(True))

    process._handle_finished(7, QProcess.ExitStatus.CrashExit)

    assert failures and "异常退出" in failures[0]
    assert closed == []
    assert process._process is None
