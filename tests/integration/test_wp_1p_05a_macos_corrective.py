from __future__ import annotations

import errno
import importlib
import json
import os
import plistlib
import signal
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _prepare_start_fixture(project_root: Path, system_name: str) -> tuple[Path, dict[str, str]]:
    scripts = project_root / "scripts"
    scripts.mkdir(parents=True)
    start_script = scripts / "start.sh"
    shutil.copy2(ROOT / "scripts" / "start.sh", start_script)

    tools = project_root / "test-tools"
    _write_executable(tools / "uname", f"printf '%s\\n' '{system_name}'")
    environment = {
        **os.environ,
        "CI": "true",
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
    }
    return start_script, environment


def _macos_wrapper_paths(project_root: Path, profile: str) -> tuple[Path, Path, Path]:
    app = (
        project_root
        / "desktop"
        / "src-tauri"
        / "target"
        / profile
        / ".sakura-dev"
        / "Sakura Runtime v2.app"
    )
    return (
        app,
        app / "Contents" / "Info.plist",
        app / "Contents" / "MacOS" / "sakura-runtime-v2-shell",
    )


def _read_stable_symlink(path: Path) -> str:
    for _attempt in range(10):
        before = path.lstat()
        if not stat.S_ISLNK(before.st_mode):
            raise AssertionError(f"expected a symlink at {path}")
        try:
            target = os.readlink(path)
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            after = path.lstat()
            if not stat.S_ISLNK(after.st_mode):
                raise AssertionError(f"expected a symlink at {path}") from error
            if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
                raise
            continue
        after = path.lstat()
        if not stat.S_ISLNK(after.st_mode):
            raise AssertionError(f"expected a symlink at {path}")
        if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
            return target
    raise AssertionError(f"could not obtain a stable symlink snapshot at {path}")


def test_stable_symlink_snapshot_retries_an_atomic_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = tmp_path / "wrapper-link"
    replacement = tmp_path / "wrapper-link.replacement"
    try:
        link.symlink_to("old-target")
        replacement.symlink_to("new-target")
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or elevated privilege")
        raise
    original_readlink = os.readlink
    read_attempts = 0

    def replace_during_first_read(path: os.PathLike[str]) -> str:
        nonlocal read_attempts
        read_attempts += 1
        if read_attempts == 1:
            os.replace(replacement, link)
            raise OSError(errno.EINVAL, "simulated readlink/rename race", path)
        return original_readlink(path)

    monkeypatch.setattr(os, "readlink", replace_during_first_read)

    assert _read_stable_symlink(link) == "new-target"
    assert read_attempts == 2


@pytest.mark.parametrize(
    ("platform", "binary_name"),
    [
        ("win32", "sakura-runtime-v2-shell.exe"),
        ("darwin", "sakura-runtime-v2-shell"),
        ("linux", "sakura-runtime-v2-shell"),
    ],
)
def test_runtime_v2_entry_resolves_platform_specific_shell_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    binary_name: str,
) -> None:
    runtime_entry = importlib.import_module("main")
    release = tmp_path / "desktop" / "src-tauri" / "target" / "release" / binary_name
    debug = tmp_path / "desktop" / "src-tauri" / "target" / "debug" / binary_name
    _write_executable(debug, "exit 0")
    _write_executable(release, "exit 0")

    monkeypatch.setattr(runtime_entry.sys, "platform", platform)

    assert runtime_entry.resolve_tauri_binary(tmp_path) == release


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX symlinks")
@pytest.mark.parametrize("profile", ["debug", "release"])
def test_start_sh_executes_macos_shell_through_development_app_identity(
    tmp_path: Path,
    profile: str,
) -> None:
    project_root = tmp_path / "project with spaces"
    start_script, environment = _prepare_start_fixture(project_root, "Darwin")
    _write_executable(
        project_root / "runtime" / "bin" / "python3",
        "echo python-fallback >&2; exit 91",
    )
    _write_executable(
        project_root
        / "desktop"
        / "src-tauri"
        / "target"
        / profile
        / "sakura-runtime-v2-shell",
        "\n".join(
            (
                f"printf '%s\\n' 'shell-{profile}'",
                "printf 'argv0=%s\\n' \"$0\"",
                "for argument in \"$@\"; do printf 'arg=%s\\n' \"$argument\"; done",
                "exit 37",
            )
        ),
    )

    completed = subprocess.run(
        ["bash", str(start_script), "alpha", "two words", "雪"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 37
    app, info_plist, wrapper_executable = _macos_wrapper_paths(project_root, profile)
    assert completed.stdout.splitlines() == [
        f"shell-{profile}",
        f"argv0={wrapper_executable}",
        "arg=alpha",
        "arg=two words",
        "arg=雪",
    ]
    assert "python-fallback" not in completed.stderr
    assert app.is_dir()
    plist = plistlib.loads(info_plist.read_bytes())
    config = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["CFBundleExecutable"] == "sakura-runtime-v2-shell"
    assert plist["CFBundleIdentifier"] == config["identifier"]
    assert wrapper_executable.is_symlink()
    assert os.readlink(wrapper_executable) == "../../../../sakura-runtime-v2-shell"
    assert wrapper_executable.resolve(strict=True) == (
        project_root
        / "desktop"
        / "src-tauri"
        / "target"
        / profile
        / "sakura-runtime-v2-shell"
    ).resolve(strict=True)


@pytest.mark.skipif(os.name == "nt", reason="the shell entry requires a POSIX environment")
def test_start_sh_keeps_linux_on_the_raw_binary_path(tmp_path: Path) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Linux")
    shell = (
        tmp_path
        / "desktop"
        / "src-tauri"
        / "target"
        / "debug"
        / "sakura-runtime-v2-shell"
    )
    _write_executable(shell, "printf 'argv0=%s\\narg=%s\\n' \"$0\" \"$1\"; exit 29")

    completed = subprocess.run(
        ["bash", str(start_script), "linux argument"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 29
    assert completed.stdout.splitlines() == [f"argv0={shell}", "arg=linux argument"]
    assert not (shell.parent / ".sakura-dev").exists()


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX symlinks")
def test_start_sh_prefers_release_macos_shell_over_debug(tmp_path: Path) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Darwin")
    target = tmp_path / "desktop" / "src-tauri" / "target"
    _write_executable(target / "release" / "sakura-runtime-v2-shell", "echo release; exit 17")
    _write_executable(target / "debug" / "sakura-runtime-v2-shell", "echo debug; exit 18")

    completed = subprocess.run(
        ["bash", str(start_script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 17
    assert completed.stdout.strip() == "release"
    assert _macos_wrapper_paths(tmp_path, "release")[0].is_dir()
    assert not _macos_wrapper_paths(tmp_path, "debug")[0].exists()


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX symlinks")
def test_start_sh_preserves_complete_wrapper_during_concurrent_refresh(tmp_path: Path) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Darwin")
    shell = (
        tmp_path
        / "desktop"
        / "src-tauri"
        / "target"
        / "debug"
        / "sakura-runtime-v2-shell"
    )
    _write_executable(shell, "exit 0")
    _, info_plist, wrapper_executable = _macos_wrapper_paths(tmp_path, "debug")
    wrapper_executable.parent.mkdir(parents=True)
    info_plist.write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "sakura-runtime-v2-shell",
                "CFBundleIdentifier": "com.example.previous-wrapper",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    wrapper_executable.symlink_to("../../../../wrong-shell")
    barrier = tmp_path / "mv-barrier"
    barrier.mkdir()
    environment = {
        **environment,
        "SAKURA_TEST_MV_BARRIER": str(barrier),
    }
    _write_executable(
        tmp_path / "test-tools" / "mv",
        "\n".join(
            (
                ': > "$SAKURA_TEST_MV_BARRIER/$$"',
                "while :; do",
                "  marker_count=0",
                '  for marker in "$SAKURA_TEST_MV_BARRIER"/*; do',
                '    [ -f "$marker" ] && marker_count=$((marker_count + 1))',
                "  done",
                '  [ "$marker_count" -ge 4 ] && break',
                "  /bin/sleep 0.01",
                "done",
                "/bin/sleep 0.02",
                'exec /bin/mv "$@"',
            )
        ),
    )

    processes = [
        subprocess.Popen(
            ["bash", str(start_script)],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        for _ in range(4)
    ]
    try:
        assert sum(process.poll() is None for process in processes) >= 2

        observations = 0
        observation_deadline = time.monotonic() + 10
        while any(process.poll() is None for process in processes):
            assert time.monotonic() < observation_deadline
            plist = plistlib.loads(info_plist.read_bytes())
            assert plist["CFBundleIdentifier"] in {
                "com.example.previous-wrapper",
                "com.rvosy.sakura.runtimev2.shell",
            }
            observed_link = _read_stable_symlink(wrapper_executable)
            assert observed_link in {
                "../../../../wrong-shell",
                "../../../../sakura-runtime-v2-shell",
            }
            observations += 1
            time.sleep(0.001)
        results = [process.communicate(timeout=10) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    assert [process.returncode for process in processes] == [0, 0, 0, 0]
    assert all(stderr == "" for _, stderr in results)
    assert observations > 0
    plist = plistlib.loads(info_plist.read_bytes())
    assert plist["CFBundleIdentifier"] == "com.rvosy.sakura.runtimev2.shell"
    assert os.readlink(wrapper_executable) == "../../../../sakura-runtime-v2-shell"
    assert not list(info_plist.parent.glob(".Info.plist.*.tmp"))
    assert not list(wrapper_executable.parent.glob(".sakura-runtime-v2-shell.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX symlinks")
def test_start_sh_fails_closed_when_macos_wrapper_cannot_be_created(tmp_path: Path) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Darwin")
    profile_root = tmp_path / "desktop" / "src-tauri" / "target" / "debug"
    _write_executable(
        profile_root / "sakura-runtime-v2-shell",
        "echo raw-shell-must-not-run; exit 37",
    )
    (profile_root / ".sakura-dev").write_text("blocks wrapper directory", encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(start_script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode not in (0, 37)
    assert completed.stdout == ""
    assert "无法创建 macOS 开发应用包装" in completed.stderr


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX symlinks")
def test_start_sh_fails_closed_and_cleans_temps_when_symlink_refresh_fails(
    tmp_path: Path,
) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Darwin")
    profile_root = tmp_path / "desktop" / "src-tauri" / "target" / "debug"
    _write_executable(
        profile_root / "sakura-runtime-v2-shell",
        "echo raw-shell-must-not-run; exit 37",
    )
    _write_executable(tmp_path / "test-tools" / "ln", "echo injected-ln-failure >&2; exit 73")

    completed = subprocess.run(
        ["bash", str(start_script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    _, info_plist, wrapper_executable = _macos_wrapper_paths(tmp_path, "debug")
    assert completed.returncode not in (0, 37)
    assert completed.stdout == ""
    assert "injected-ln-failure" in completed.stderr
    assert "无法建立临时 Mach-O 入口链接" in completed.stderr
    assert plistlib.loads(info_plist.read_bytes())["CFBundlePackageType"] == "APPL"
    assert not wrapper_executable.exists()
    assert not list(info_plist.parent.glob(".Info.plist.*.tmp"))
    assert not list(wrapper_executable.parent.glob(".sakura-runtime-v2-shell.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="the macOS development wrapper requires POSIX signals")
def test_start_sh_exec_preserves_pid_and_forwards_runtime_signals(tmp_path: Path) -> None:
    start_script, environment = _prepare_start_fixture(tmp_path, "Darwin")
    ready_file = tmp_path / "shell.ready"
    environment = {**environment, "SAKURA_TEST_READY_FILE": str(ready_file)}
    shell = (
        tmp_path
        / "desktop"
        / "src-tauri"
        / "target"
        / "debug"
        / "sakura-runtime-v2-shell"
    )
    _write_executable(
        shell,
        "printf '%s\\n' \"$$\" > \"$SAKURA_TEST_READY_FILE\"\nwhile :; do :; done",
    )
    process = subprocess.Popen(
        ["bash", str(start_script)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )

    try:
        deadline = time.monotonic() + 10
        ready_pid: int | None = None
        while time.monotonic() < deadline and ready_pid is None:
            assert process.poll() is None
            if ready_file.is_file():
                try:
                    ready_pid = int(ready_file.read_text(encoding="ascii"))
                except (OSError, ValueError):
                    pass
            time.sleep(0.01)
        assert ready_pid == process.pid

        os.kill(process.pid, signal.SIGTERM)

        assert process.wait(timeout=10) == -signal.SIGTERM
        stdout, stderr = process.communicate()
        assert stdout == ""
        assert stderr == ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


class _ExecvCalled(RuntimeError):
    pass


def test_main_py_hands_darwin_to_the_shared_start_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_entry = importlib.import_module("main")
    executable = ROOT / "desktop" / "src-tauri" / "target" / "debug" / "sakura-runtime-v2-shell"
    calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(runtime_entry.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_entry.sys, "argv", ["main.py", "alpha", "two words"])
    monkeypatch.setattr(runtime_entry, "resolve_tauri_binary", lambda: executable)

    def record_execv(path: str, arguments: list[str]) -> None:
        calls.append((path, arguments))
        raise _ExecvCalled

    monkeypatch.setattr(runtime_entry.os, "execv", record_execv)

    with pytest.raises(_ExecvCalled):
        runtime_entry.main()

    assert calls == [
        (
            "/bin/bash",
            ["/bin/bash", str(ROOT / "scripts" / "start.sh"), "alpha", "two words"],
        )
    ]


def test_main_py_reports_missing_shell_before_any_exec(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_entry = importlib.import_module("main")

    monkeypatch.setattr(runtime_entry.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_entry, "resolve_tauri_binary", lambda: None)
    monkeypatch.setattr(
        runtime_entry.os,
        "execv",
        lambda *_arguments: pytest.fail("missing Shell must not be executed"),
    )

    assert runtime_entry.main() == 1
    assert "未找到 Tauri Shell" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_main_py_keeps_non_darwin_direct_exec(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    runtime_entry = importlib.import_module("main")
    executable = ROOT / "selected-shell"
    calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(runtime_entry.sys, "platform", platform)
    monkeypatch.setattr(runtime_entry.sys, "argv", ["main.py", "argument"])
    monkeypatch.setattr(runtime_entry, "resolve_tauri_binary", lambda: executable)

    def record_execv(path: str, arguments: list[str]) -> None:
        calls.append((path, arguments))
        raise _ExecvCalled

    monkeypatch.setattr(runtime_entry.os, "execv", record_execv)

    with pytest.raises(_ExecvCalled):
        runtime_entry.main()

    assert calls == [(str(executable), [str(executable), "argument"])]


def test_tauri_config_enables_required_macos_transparent_window_support() -> None:
    config = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    cargo_toml = (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text("utf-8")
    styles = (ROOT / "desktop" / "frontend" / "styles.css").read_text("utf-8")

    assert config["app"]["macOSPrivateApi"] is True
    assert config["app"]["windows"][0]["transparent"] is True
    assert config["app"]["windows"][0]["visible"] is True
    assert 'tauri = { version = "=2.11.3", features = ["macos-private-api"] }' in cargo_toml
    assert "background: transparent" in styles
    assert 'data-shell-state="loading"' in (
        ROOT / "desktop" / "frontend" / "index.html"
    ).read_text("utf-8")


def test_deferred_drag_does_not_depend_on_webview_event_registration() -> None:
    source = (ROOT / "desktop" / "frontend" / "app.js").read_text("utf-8")

    assert "window.__TAURI__.event" not in source
    assert 'window.addEventListener("tauri://move"' not in source
    assert "commit_pet_drag" not in source


def test_native_window_moves_do_not_commit_an_intermediate_drag_anchor() -> None:
    rust_source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")
    frontend_source = (ROOT / "desktop" / "frontend" / "app.js").read_text("utf-8")

    assert "tauri::WindowEvent::Moved(position)" not in rust_source
    assert "commit_deferred_pet_drag" not in rust_source
    assert "claim_deferred_drag_position" not in rust_source
    assert "commit_pet_drag" not in frontend_source


def test_next_layout_finalizes_deferred_drag_before_applying_programmatic_bounds() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")

    apply_layout = source[source.index("fn apply_pet_layout(") : source.index("fn apply_native_interaction_region(")]
    assert ".outer_position()" in apply_layout
    assert apply_layout.index("session.finish_deferred_drag();") < apply_layout.index(
        ".apply_bounds(&window, &application.physical_placement)",
    )


def test_visibility_probe_recovery_is_not_owned_by_the_hidden_webview_timer() -> None:
    rust_source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")
    frontend_source = (ROOT / "desktop" / "frontend" / "app.js").read_text("utf-8")

    assert 'invoke("probe_pet_visibility")' in frontend_source
    assert "window.setTimeout" not in frontend_source
    assert 'invoke("set_pet_visible"' not in frontend_source
    probe = rust_source[
        rust_source.index("fn probe_pet_visibility(") : rust_source.index("fn collect_native_diagnostics(")
    ]
    assert probe.index(".set_visible(&window, false)") < probe.index(
        ".set_visible(&restore_window, true)"
    )
    assert ".set_visible(&restore_window, true)" in probe


def test_deferred_drag_session_is_reserved_before_native_drag_starts() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")

    assert source.index("session.begin_deferred_drag();") < source.index(
        ".start_drag(&window)",
    )
    assert "session.cancel_deferred_drag();" in source


def test_shell_close_releases_the_process_lifecycle_not_just_its_window() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text("utf-8")

    close_command = source[source.index("fn close_pet_window(") : source.index("fn development_runtime_request(")]
    assert "lifecycle: State<'_, ShellLifecycleState>" in close_command
    assert close_command.index("handle.request_shutdown()") < close_command.index("window.close()")
    assert close_command.index("window.close()") < close_command.index("app_handle.exit(0)")
