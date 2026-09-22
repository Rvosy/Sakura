from __future__ import annotations

import io
import json
import os
import select
import shutil
import signal
import threading
from pathlib import Path

import pytest
import psutil

from app.plugins.sakura_plugin_sdk import _DiagnosticsProxy
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA
from app.core_host.plugin_host_services import _DiagnosticsHostService
from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
from plugins.optional.sakura_gpt_sovits import _bundle


pytestmark = pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="macOS installer requires POSIX bash")


def _script_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> _bundle.TTSBundleEntry:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setattr(_bundle, "__file__", str(plugin / "_bundle.py"))
    (plugin / "install.sh").write_text(script, encoding="utf-8")
    return _bundle.TTSBundleEntry(
        key="fixture", label="fixture", install_method="script", installer_script="install.sh"
    )


def test_script_failure_preserves_install_and_reports_sanitized_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_bundle.urllib.request, "getproxies", lambda: {
        "https": "http://proxy-user:proxy-password@proxy.invalid:8080", "no": "localhost,127.0.0.1",
    })
    entry = _script_entry(tmp_path, monkeypatch, r'''set -eu
test "$HTTPS_PROXY" = "http://proxy-user:proxy-password@proxy.invalid:8080"
test "$https_proxy" = "$HTTPS_PROXY"
test "$no_proxy" = 'localhost,127.0.0.1'
printf '::sakura-progress status=install progress=60\n'
printf 'FIRST_REMOVED\n'
for n in $(seq 1 100); do printf 'installer normal progress %040d\n' "$n"; done
printf 'Authorization: Bearer auth-value-private\n'
printf 'Authorization: Basic basic-value-private\n'
printf 'request https://example.invalid/file?token=url-value-private\n'
printf 'proxy=%s\n' "$HTTPS_PROXY"
printf 'authorization=%020000d\n' 1
printf 'curl: (56) receiving HTTP stream failed\n' >&2
exit 27
''')
    user_root = tmp_path / "user"
    installed = user_root / "tts" / entry.key
    installed.mkdir(parents=True)
    (installed / "api_v2.py").write_text("existing runtime", encoding="utf-8")
    config = {"workDir": str(installed)}
    stream = io.BytesIO()
    requests = []

    class Context:
        def _remote_call(self, service, method, arguments):
            assert service == "sakura.host.diagnostics" and method == "emit"
            requests.append((method, arguments))
            return {"accepted": True}

        plugin_id = "sakura.tts.gpt-sovits"

    diagnostics = _DiagnosticsProxy(Context())
    resource = _bundle.TTSBundleResource(
        user_root=user_root, config_get=lambda: config, config_update=config.update,
        entry=lambda: entry, custom_endpoint=lambda _config: False,
        diagnostic=lambda event, severity, attributes: diagnostics.emit({
            "event": event, "severity": severity, "attributes": dict(attributes),
        }),
    )
    bridge = install_runtime_logging(stream)
    caller = HOST_CALLER.set(Context.plugin_id)
    metadata = HOST_CALLER_LOG_METADATA.set(("GPT-SoVITS", ("sakura.tts.provider.gpt-sovits",)))
    try:
        resource._run(entry)
        # The host receives serialized RPC arguments outside the plugin's
        # active exception context, as it does in its separate process.
        for method, arguments in requests:
            assert _DiagnosticsHostService().call(method, arguments) == {"accepted": True}
    finally:
        HOST_CALLER_LOG_METADATA.reset(metadata)
        HOST_CALLER.reset(caller)
        bridge.close()

    failed = resource.load()["bundleResource"]
    assert failed["taskState"] == "failed"
    assert "INSTALL_FAILED" in failed["detail"]
    assert "exit_code" not in failed["detail"]
    assert config == {"workDir": str(installed)}
    assert (installed / "api_v2.py").read_text(encoding="utf-8") == "existing runtime"
    assert not (user_root / "tts" / "_tmp" / entry.key).exists()
    events = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)) for line in stream.getvalue().splitlines()
              if line.startswith(CORE_BRIDGE_PREFIX)]
    events = [event for event in events if event.get("attributes", {}).get("event") == "tts.bundle.install.failed"]
    assert len(events) == 1
    assert events[0]["plugin_id"] == Context.plugin_id
    assert events[0]["severity"] == "warning"
    attributes = events[0]["attributes"]
    assert attributes["code"] == "TTS_BUNDLE_INSTALL_FAILED"
    assert attributes["reason_code"] == "INSTALL_FAILED"
    assert attributes["stage"] == "install"
    assert attributes["cause_code"] == "TTS_BUNDLE_INSTALL_FAILED"
    assert "exit_code=27" in attributes["exception_chain"]
    assert "curl: (56) receiving HTTP stream failed" in attributes["exception_chain"]
    assert len(attributes["diagnostic"]) <= 4096
    serialized = stream.getvalue().decode("utf-8")
    for secret in ("auth-value-private", "basic-value-private", "url-value-private", "proxy-password", "proxy-user", "FIRST_REMOVED"):
        assert secret not in serialized


def test_pending_script_cancellation_does_not_replace_existing_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _script_entry(tmp_path, monkeypatch, 'printf "replacement runtime" > "$1/api_v2.py"\n')
    installed = tmp_path / "tts" / entry.key
    installed.mkdir(parents=True)
    (installed / "api_v2.py").write_text("existing runtime", encoding="utf-8")
    events = []
    resource = _bundle.TTSBundleResource(
        user_root=tmp_path, config_get=lambda: {}, config_update=lambda _config: pytest.fail("cancelled install"),
        entry=lambda: entry, custom_endpoint=lambda _config: False,
        diagnostic=lambda *args: events.append(args),
    )
    resource._cancel.set()
    resource._run(entry)

    assert resource.load()["bundleResource"]["taskState"] == "cancelled"
    assert (installed / "api_v2.py").read_text(encoding="utf-8") == "existing runtime"
    assert not (tmp_path / "tts" / "_tmp" / entry.key).exists()
    assert events == []


@pytest.mark.parametrize(("action", "close_stdout"), [("cancel", False), ("close", True)])
def test_silent_script_cancellation_reaps_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, close_stdout: bool,
) -> None:
    # The child announces its PID before the progress handshake, then blocks
    # opening the FIFO. Neither an output line nor process exit can unblock us.
    entry = _script_entry(tmp_path, monkeypatch, r'''set -eu
mkfifo "$1/hold"
bash -c '
    printf "%s" "$$" > "$1/child.pid"
    printf "::sakura-progress status=install progress=60\n"
    printf "unfinished output"
    if [ "$2" = "true" ]; then exec >/dev/null 2>&1; fi
    IFS= read -r ignored < "$1/hold"
' child "$1" ''' + ("true" if close_stdout else "false") + r''' &
child_pid=$!
trap 'wait "$child_pid" 2>/dev/null || true; exit 143' TERM
exec >/dev/null 2>&1
wait "$child_pid"
''')
    installed = tmp_path / "tts" / entry.key
    installed.mkdir(parents=True)
    (installed / "api_v2.py").write_text("existing runtime", encoding="utf-8")
    ready = threading.Event()
    processes = []
    popen = _bundle.subprocess.Popen

    def capture_process(command, **kwargs):
        process = popen(command, **kwargs)
        if command[0] == "bash":
            processes.append(process)
        return process

    monkeypatch.setattr(_bundle.subprocess, "Popen", capture_process)
    resource = _bundle.TTSBundleResource(
        user_root=tmp_path, config_get=lambda: {}, config_update=lambda _config: pytest.fail("cancelled install"),
        entry=lambda: entry, custom_endpoint=lambda _config: False,
    )
    set_stage = resource._set_stage

    def stage(value):
        set_stage(value)
        if value == "install":
            ready.set()

    monkeypatch.setattr(resource, "_set_stage", stage)
    closer = None
    try:
        resource.start({})
        assert ready.wait(3), "installer child never reached its handshake"
        child_pid = int((tmp_path / "tts" / "_tmp" / entry.key / "child.pid").read_text())
        os.kill(child_pid, 0)
        if action == "cancel":
            resource.cancel({})
            resource._thread.join(3)
            assert not resource._thread.is_alive(), "cancel waited for installer output"
        else:
            closer = threading.Thread(target=resource.close)
            closer.start()
            closer.join(3)
            assert not closer.is_alive(), "close waited for installer process exit"
        assert resource.load()["bundleResource"]["taskState"] == "cancelled"
        assert processes[0].poll() is not None
        assert processes[0].stdout.closed
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert (installed / "api_v2.py").read_text(encoding="utf-8") == "existing runtime"
        assert not (tmp_path / "tts" / "_tmp" / entry.key).exists()
    finally:
        for process in processes:
            _bundle._terminate(process)
        resource.close()
        if closer is not None:
            closer.join(3)


def test_cancel_stops_background_child_after_installer_shell_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _script_entry(tmp_path, monkeypatch, r'''set -eu
mkfifo "$1/hold"
bash -c '
    trap "" TERM
    printf "%s" "$$" > "$1/child.pid"
    printf "::sakura-progress status=install progress=60\n"
    IFS= read -r ignored < "$1/hold"
' child "$1" &
exit 0
''')
    ready = threading.Event()
    processes = []
    popen = _bundle.subprocess.Popen

    def capture_process(command, **kwargs):
        process = popen(command, **kwargs)
        if command[0] == "bash":
            processes.append(process)
        return process

    monkeypatch.setattr(_bundle.subprocess, "Popen", capture_process)
    resource = _bundle.TTSBundleResource(
        user_root=tmp_path, config_get=lambda: {}, config_update=lambda _config: pytest.fail("cancelled install"),
        entry=lambda: entry, custom_endpoint=lambda _config: False,
    )
    set_stage = resource._set_stage

    def stage(value):
        set_stage(value)
        if value == "install":
            ready.set()

    monkeypatch.setattr(resource, "_set_stage", stage)
    child_pid = None
    retained_pipe = None
    try:
        resource.start({})
        assert ready.wait(3)
        child_pid = int((tmp_path / "tts" / "_tmp" / entry.key / "child.pid").read_text())
        child = psutil.Process(child_pid)
        assert processes[0].wait(timeout=3) == 0
        retained_pipe = os.dup(processes[0].stdout.fileno())
        resource.cancel({})
        resource._thread.join(3)
        assert not resource._thread.is_alive()
        assert resource.load()["bundleResource"]["taskState"] == "cancelled"
        # EOF proves the TERM-ignoring orphan released its inherited writer.
        assert select.select([retained_pipe], [], [], 3)[0]
        assert os.read(retained_pipe, 1) == b""
        try:
            assert child.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
        assert processes[0].stdout.closed
        assert not (tmp_path / "tts" / "_tmp" / entry.key).exists()
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in processes:
            _bundle._terminate(process)
        resource.close()
        if retained_pipe is not None:
            os.close(retained_pipe)
