from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_default_python_entry_only_hands_off_to_tauri() -> None:
    source = _source("main.py")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "PySide6" not in source
    assert "subprocess" not in imports
    assert "os.execv(" in source
    assert 'TAURI_BINARY_STEM = "sakura-runtime-v2-shell"' in source
    assert 'if (platform or sys.platform) == "win32"' in source
    assert 'return f"{TAURI_BINARY_STEM}.exe"' in source


def test_legacy_entry_acquires_mutex_before_any_shared_data_action() -> None:
    source = _source("legacy_qt_main.py")
    main_source = source[source.index("def main() -> int:") :]

    acquire = main_source.index("instance_guard.acquire()")
    for later_action in (
        "_enable_crash_diagnostics(BASE_DIR)",
        "run_startup_self_check(BASE_DIR)",
        "ensure_default_configs(BASE_DIR)",
        "record_app_version(BASE_DIR)",
        "MigrationRunner(BASE_DIR).run()",
        "build_initial_app_context(BASE_DIR)",
    ):
        assert acquire < main_source.index(later_action)

    assert "InstanceAcquireStatus.ALREADY_RUNNING" in main_source
    assert "InstanceAcquireStatus.FATAL" in main_source
    assert "另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。" in source


def test_legacy_entry_releases_mutex_after_external_cleanup_and_qthread_drain() -> None:
    source = _source("legacy_qt_main.py")
    main_source = source[
        source.index("def main() -> int:") : source.index(
            "def _run_acquired_legacy_qt_application() -> int:"
        )
    ]
    acquired_source = source[
        source.index("def _run_acquired_legacy_qt_application() -> int:") :
    ]

    assert "app.aboutToQuit.connect(instance_guard.release)" not in source
    assert "try:\n        return _run_acquired_legacy_qt_application()" in main_source
    assert "finally:\n        instance_guard.release()" in main_source
    assert "wait_for_lingering_qthreads(" in acquired_source
    assert "if not pet_window.resource_manager.wait_for_lingering_qthreads(" in acquired_source
    assert "os._exit(1)" in acquired_source
    fixture_source = _source("tests/fixtures/runtime_v2/wp_1a_04/legacy_qt_smoke.py")
    assert 'parser.add_argument("--drain-fail", action="store_true")' in fixture_source
    assert "if args.drain_fail:\n                    return False" in fixture_source


def test_batch_entries_select_tauri_directly_and_keep_explicit_qt_rollback() -> None:
    default = _source("start.bat")
    legacy = _source("start-legacy-qt.bat")

    assert "sakura-runtime-v2-shell.exe" in default
    assert "python.exe\" main.py" not in default
    assert "runtime\\python.exe" in legacy
    assert "legacy_qt_main.py" in legacy


def test_windows_batch_entries_only_use_crlf_line_endings() -> None:
    for relative in ("start.bat", "start-legacy-qt.bat"):
        raw = (ROOT / relative).read_bytes()
        assert b"\n" not in raw.replace(b"\r\n", b""), relative


def test_tauri_acquires_shared_mutex_before_building_the_webview_shell() -> None:
    source = _source("desktop/src-tauri/src/main.rs")
    acquire = source.index("instance_lock_backend.acquire(SHARED_INSTANCE_ID)")
    builder = source.index("tauri::Builder::default()")

    assert acquire < builder
    assert "InstanceLockAcquire::AlreadyRunning" in source
    assert "Err(error)" in source
    assert "Sakura 已在运行" in source
    assert "另一个 Sakura 桌面入口正在运行。请先退出现有的 legacy Qt 或 Tauri 实例，再重试。" in source


def test_legacy_qt_smoke_isolates_runtime_logging_before_importing_legacy_entry() -> None:
    source = _source("tests/fixtures/runtime_v2/wp_1a_04/legacy_qt_smoke.py")

    assert "isolated_base_dir = args.base_dir.resolve()" in source
    assert "runtime_log._FILE_LOG_PATH =" in source
    assert "runtime_log._load_debug_values = lambda: {}" in source
    assert "StoragePaths(isolated_base_dir).runtime_log_file()" in source

    isolate_root = source.index("isolated_base_dir = args.base_dir.resolve()")
    redirect_file_log = source.index("runtime_log._FILE_LOG_PATH =")
    isolate_debug_config = source.index("runtime_log._load_debug_values = lambda: {}")
    import_legacy_entry = source.index("import legacy_qt_main")

    assert isolate_root < redirect_file_log < import_legacy_entry
    assert isolate_root < isolate_debug_config < import_legacy_entry


def test_acceptance_only_skips_exit_code_for_discovered_processes() -> None:
    source = _source("desktop/tests/windows_shared_instance_acceptance.ps1")
    close_helper = source[
        source.index("function Close-WindowAndAssertExit") : source.index("function Start-Tauri")
    ]
    default_entry = source[source.index("$knownShellIds =") : source.index("$scenarios.Add(\"default main.py")]
    batch_entry = source[
        source.index("$batch = Register-StartedRoot (Start-Process") : source.index(
            "$scenarios.Add(\"start.bat"
        )
    ]
    legacy_batch_entry = source[
        source.index("$legacyBatch = Register-StartedRoot (Start-Process") : source.index(
            '$scenarios.Add("start-legacy-qt.bat propagates shared-lock conflict'
        )
    ]

    assert "[switch]$SkipExitCodeCheck" in close_helper
    assert "if (-not $SkipExitCodeCheck -and $Process.ExitCode -ne $ExpectedExitCode)" in close_helper
    assert "-SkipExitCodeCheck" in default_entry
    assert "-SkipExitCodeCheck" in batch_entry
    assert "-SkipExitCodeCheck" in legacy_batch_entry


def test_start_batch_acceptance_discovers_the_new_tauri_root_not_only_a_direct_child() -> None:
    source = _source("desktop/tests/windows_shared_instance_acceptance.ps1")
    batch_start = source.index("$knownShellIds =", source.index('$scenarios.Add("default main.py'))
    batch_entry = source[batch_start : source.index("$scenarios.Add(\"start.bat")]

    assert "$knownShellIds =" in batch_entry
    assert "Wait-ForNewProcess -ProcessName \"sakura-runtime-v2-shell\"" in batch_entry
    assert "Wait-ForChildProcess -Parent $batch" not in batch_entry


def test_acceptance_tracks_and_cleans_every_started_root_on_failure() -> None:
    source = _source("desktop/tests/windows_shared_instance_acceptance.ps1")
    cleanup = source[source.rindex("\nfinally {") :]

    assert "$startedRoots = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()" in source
    assert "function Register-StartedRoot" in source
    assert "function Register-ProcessIdentity" in source
    assert "function Update-TrackedProcessTree" in source
    assert "function Test-TrackedProcessIdentityAlive" in source
    assert "function Stop-TrackedProcessTree" in source
    assert "Register-StartedRoot (Start-Process" in source
    assert "foreach ($startedRoot in @($startedRoots))" in source
    assert ".StartTime.ToUniversalTime().Ticks" in source
    assert "[System.StringComparer]::OrdinalIgnoreCase.Equals" in source
    assert "Project runtime Python roots remained after acceptance" in source
    assert 'Get-Process -Name "sakura-runtime-v2-shell"' not in cleanup
