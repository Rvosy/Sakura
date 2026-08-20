from __future__ import annotations

import base64
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from plugins.playwright_browser import browser, plugin as playwright_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "playwright_browser"
PLUGIN_ID = "playwright_browser"
TOOL_NAMES = {
    "playwright_navigate",
    "playwright_get_text",
    "playwright_search_web",
    "playwright_screenshot",
    "playwright_click",
    "playwright_fill",
    "playwright_evaluate",
}


@pytest.fixture(autouse=True)
def _reset_browser_runtime() -> None:
    browser.shutdown_browser()
    browser.set_config_loader(None)
    browser._use_bg_thread = True
    yield
    browser.shutdown_browser()
    browser.set_config_loader(None)
    browser._use_bg_thread = True


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(SOURCE_PLUGIN_ROOT, root / "plugins" / PLUGIN_ID)
    return root


def _plugin(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in snapshot["plugins"] if item["pluginId"] == PLUGIN_ID)


class _Runtime:
    def set_prompt_patches(self, _values: object) -> None:
        pass

    def set_context_providers(self, _values: object) -> None:
        pass


class _SetupConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = dict(values)
        self.handlers: list[Any] = []

    def get(self) -> dict[str, Any]:
        return dict(self.values)

    def update(self, values: dict[str, Any]) -> list[str]:
        self.values.update(values)
        return [handler(dict(self.values)) for handler in self.handlers]

    def on_change(self, handler: Any) -> None:
        self.handlers.append(handler)


class _RegistrationSink:
    def __init__(self) -> None:
        self.registrations: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def register(self, *args: Any, **kwargs: Any) -> None:
        self.registrations.append((args, kwargs))


class _SetupContext:
    def __init__(self, config: _SetupConfig) -> None:
        self.config = config
        self.tools = _RegistrationSink()
        self.settings = _RegistrationSink()
        self.artifacts = object()
        self.cleanups: list[Any] = []

    def get(self, service_key: str) -> object:
        return {
            "sakura.host.tools": self.tools,
            "sakura.host.settings": self.settings,
            "sakura.host.artifacts": self.artifacts,
        }[service_key]

    def effect(self, cleanup: Any) -> None:
        self.cleanups.append(cleanup)


def test_restart_required_config_is_not_applied_before_reload() -> None:
    config = _SetupConfig({"browser_type": "firefox", "headless": True})
    context = _SetupContext(config)
    playwright_plugin.PlaywrightBrowserPlugin().setup(context)
    try:
        assert browser._config_loader is not None
        assert browser._config_loader().browser_type == "firefox"
        assert browser._config_loader().headless is True

        _descriptor, callbacks = context.settings.registrations[0]
        assert callbacks["save"](
            {"browser_type": "chromium", "headless": False}
        ) == ["restart_required"]
        assert callbacks["load"]() == {
            "browser_type": "chromium",
            "headless": False,
        }
        assert browser._config_loader().browser_type == "firefox"
        assert browser._config_loader().headless is True
    finally:
        for cleanup in reversed(context.cleanups):
            cleanup()

    reloaded = _SetupContext(config)
    playwright_plugin.PlaywrightBrowserPlugin().setup(reloaded)
    try:
        assert browser._config_loader is not None
        assert browser._config_loader().browser_type == "chromium"
        assert browser._config_loader().headless is False
    finally:
        for cleanup in reversed(reloaded.cleanups):
            cleanup()


def test_bundled_playwright_uses_v3_tools_settings_and_private_config(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path)
    legacy_config = root / "plugins" / PLUGIN_ID / "config.json"
    legacy_text = '{"headless": true, "browser_type": "firefox"}'
    legacy_config.write_text(legacy_text, encoding="utf-8")
    user_config = root / "data" / "plugins" / PLUGIN_ID / "config.json"
    registry = ToolRegistry()
    worker = PluginWorkerClient(root, "generation-playwright-v3")
    worker.configure_host_services(registry, _Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        plugin = _plugin(snapshot)
        assert plugin["apiVersion"] == 3
        assert plugin["state"] == "active"
        assert plugin["requires"] == [
            "sakura.host.tools",
            "sakura.host.settings",
            "sakura.host.artifacts",
        ]
        assert plugin["effectCount"] > 0
        assert {item.name for item in registry.all()} == TOOL_NAMES

        settings = _plugin(worker.settings_snapshot())["sections"]
        assert len(settings) == 1
        assert settings[0]["sectionId"] == PLUGIN_ID
        assert settings[0]["values"] == {
            "browser_type": "firefox",
            "headless": True,
        }

        invalid = registry.execute(
            "playwright_navigate",
            {"url": "file:///private/browser-secret.html"},
        )
        assert invalid.success is False
        assert invalid.reason_code == "PLUGIN_CALLBACK_DATA_INVALID"
        assert "browser-secret" not in repr(invalid)
        assert "file:///" not in repr(invalid)

        saved = worker.settings_save(
            PLUGIN_ID,
            PLUGIN_ID,
            {"browser_type": "chromium", "headless": False},
        )
        assert saved == {
            "saved": True,
            "applicationState": "restart_required",
            "reasonCode": "CONFIG_RELOAD_REQUIRED",
        }
        assert json.loads(user_config.read_text(encoding="utf-8")) == {
            "browser_type": "chromium",
            "headless": False,
        }
        assert legacy_config.read_text(encoding="utf-8") == legacy_text

        old_token = worker._token
        assert worker.settings_action(PLUGIN_ID, PLUGIN_ID, "sakura.reload", {}) == {
            "message": "插件已重新加载。"
        }
        assert worker._token == old_token
        assert _plugin(worker.refresh_status())["state"] == "active"
        assert {item.name for item in registry.all()} == TOOL_NAMES
        assert _plugin(worker.settings_snapshot())["sections"][0]["values"] == {
            "browser_type": "chromium",
            "headless": False,
        }

        disabled = worker.set_plugin_enabled(PLUGIN_ID, False)
        assert _plugin(disabled)["state"] == "disabled"
        assert _plugin(disabled)["effectCount"] == 0
        assert registry.all() == []
        assert worker._host_services is not None
        assert worker._host_services.settings_count == 0

        enabled = worker.set_plugin_enabled(PLUGIN_ID, True)
        assert _plugin(enabled)["state"] == "active"
        assert _plugin(enabled)["effectCount"] > 0
        assert {item.name for item in registry.all()} == TOOL_NAMES
    finally:
        worker.close()


class _CloseProbe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StopProbe:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _install_browser_probes() -> tuple[object, list[_CloseProbe], _StopProbe]:
    runner = browser._ensure_executor()
    resources = [_CloseProbe(), _CloseProbe(), _CloseProbe()]
    playwright = _StopProbe()
    browser._page, browser._context, browser._browser = resources
    browser._playwright = playwright
    return runner, resources, playwright


class _HostBridge:
    def __init__(self, *, fail_settings: bool = False) -> None:
        self.fail_settings = fail_settings
        self.live: dict[str, tuple[str, list[Any]]] = {}
        self.handles: list[str] = []
        self.failed_runner: object | None = None
        self.failed_resources: list[_CloseProbe] = []
        self.failed_playwright: _StopProbe | None = None
        self._sequence = 0

    def __call__(self, service_key: str, method: str, args: list[Any]) -> object:
        if method == "register":
            if service_key == "sakura.host.settings" and self.fail_settings:
                (
                    self.failed_runner,
                    self.failed_resources,
                    self.failed_playwright,
                ) = _install_browser_probes()
                raise RuntimeError("private setup sentinel")
            self._sequence += 1
            registration_id = f"registration-{self._sequence}"
            self.live[registration_id] = (service_key, args)
            if service_key == "sakura.host.tools":
                self.handles.append(str(args[1]))
            return {"registrationId": registration_id}
        if method == "unregister":
            return {"removed": self.live.pop(str(args[0]), None) is not None}
        raise AssertionError((service_key, method))


def _manager(root: Path, bridge: _HostBridge):
    from app.plugins.discovery import PluginDiscovery
    from app.plugins.kernel import PluginKernelManager

    specs = PluginDiscovery(root).discover()
    return PluginKernelManager(
        root,
        specs,
        host_service_keys=(
            "sakura.host.tools",
            "sakura.host.settings",
            "sakura.host.artifacts",
        ),
        host_call=bridge,
    )


def test_playwright_disable_reload_invalidates_callbacks_and_joins_executor(
    tmp_path: Path,
) -> None:
    from app.plugins.kernel import PluginKernelError

    root = _assistant_root(tmp_path)
    bridge = _HostBridge()
    manager = _manager(root, bridge)
    try:
        assert _plugin(manager.snapshot())["state"] == "active"
        first_handles = tuple(bridge.handles)
        runner, resources, playwright = _install_browser_probes()

        disabled = manager.set_enabled(PLUGIN_ID, False)
        assert _plugin(disabled)["state"] == "disabled"
        assert _plugin(disabled)["effectCount"] == 0
        assert bridge.live == {}
        assert all(item.closed for item in resources)
        assert playwright.stopped is True
        assert runner._thread.is_alive() is False
        assert browser._config_loader is None
        for handle in first_handles:
            with pytest.raises(PluginKernelError) as raised:
                manager.invoke_callback(handle, "tools.handler", [{}])
            assert raised.value.code == "CALLBACK_INVALID"

        enabled = manager.set_enabled(PLUGIN_ID, True)
        assert _plugin(enabled)["state"] == "active"
        second_handles = tuple(bridge.handles[len(first_handles):])
        assert second_handles
        assert set(first_handles).isdisjoint(second_handles)

        reloaded = manager.reload(PLUGIN_ID)
        assert _plugin(reloaded)["state"] == "active"
        assert bridge.live
        for handle in second_handles:
            with pytest.raises(PluginKernelError) as raised:
                manager.invoke_callback(handle, "tools.handler", [{}])
            assert raised.value.code == "CALLBACK_INVALID"
    finally:
        manager.close()
    assert bridge.live == {}
    assert browser._config_loader is None


def test_playwright_setup_commit_failure_rolls_back_host_and_browser_resources(
    tmp_path: Path,
) -> None:
    root = _assistant_root(tmp_path)
    bridge = _HostBridge(fail_settings=True)
    manager = _manager(root, bridge)
    try:
        plugin = _plugin(manager.snapshot())
        assert plugin["state"] == "failed"
        assert plugin["effectCount"] == 0
        assert bridge.live == {}
        assert manager.callbacks.count == 0
        assert bridge.failed_runner is not None
        assert bridge.failed_runner._thread.is_alive() is False
        assert all(item.closed for item in bridge.failed_resources)
        assert bridge.failed_playwright is not None
        assert bridge.failed_playwright.stopped is True
        assert browser._config_loader is None
    finally:
        manager.close()


def _install_fake_playwright(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    package = root / "playwright"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sync_api.py").write_text(
        """
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PID_FILE = Path(os.environ["SAKURA_TEST_PLAYWRIGHT_PID_FILE"])
HANG_FILE = Path(os.environ["SAKURA_TEST_PLAYWRIGHT_HANG_FILE"])


class Page:
    url = "about:blank"

    def is_closed(self):
        return False

    def screenshot(self, *, path, **_kwargs):
        Path(path).write_bytes(b"\\xff\\xd8" + b"x" * 60_000 + b"\\xff\\xd9")

    def inner_text(self, _selector):
        return "页面" * 80_000

    def evaluate(self, _code):
        return {"payload": "脚本" * 80_000}

    def title(self):
        return "Fake Browser"

    def close(self):
        return None


class Context:
    def route(self, _pattern, _handler):
        return None

    def new_page(self):
        return Page()

    def close(self):
        return None


class Browser:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
        )
        PID_FILE.write_text(str(self.process.pid), encoding="utf-8")

    def new_context(self):
        return Context()

    def close(self):
        while HANG_FILE.exists():
            time.sleep(0.05)
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)


class Launcher:
    def launch(self, **_kwargs):
        return Browser()


class Playwright:
    def __init__(self):
        self.chromium = Launcher()
        self.firefox = Launcher()
        self.webkit = Launcher()

    def stop(self):
        return None


class Starter:
    def start(self):
        return Playwright()


def sync_playwright():
    return Starter()
""".strip(),
        encoding="utf-8",
    )
    pid_file = root / "playwright-child.pid"
    hang_file = root / "playwright-close-hang"
    previous = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(item for item in (str(root), previous) if item),
    )
    monkeypatch.setenv("SAKURA_TEST_PLAYWRIGHT_PID_FILE", str(pid_file))
    monkeypatch.setenv("SAKURA_TEST_PLAYWRIGHT_HANG_FILE", str(hang_file))
    return pid_file, hang_file


def _pid_running(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_pid_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while _pid_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_running(pid)


def _read_pid(path: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            return int(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("fake Playwright child PID was not published")


def test_real_worker_consumes_screenshot_artifact_and_recovers_hung_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path)
    (root / "plugins" / PLUGIN_ID / "config.json").write_text(
        '{"headless": true, "browser_type": "chromium"}',
        encoding="utf-8",
    )
    pid_file, hang_file = _install_fake_playwright(root, monkeypatch)
    registry = ToolRegistry()
    worker = PluginWorkerClient(root, "generation-playwright-resources", call_timeout=0.5)
    worker.configure_host_services(registry, _Runtime())
    owned_pids: set[int] = set()
    try:
        worker.start()
        assert _plugin(worker.wait_until_loaded(timeout=5))["state"] == "active"

        screenshot = registry.execute("playwright_screenshot", {"full_page": True})
        assert screenshot.success is True
        image = screenshot.content["artifact"]
        assert image["type"] == "image"
        assert image["mimeType"] == "image/jpeg"
        assert len(base64.b64decode(image["data"])) == 60_004
        assert screenshot.content["content"] == {
            "url": "about:blank",
            "title": "Fake Browser",
        }
        assert worker._host_services is not None
        assert worker._host_services.artifact_count == 0
        first_pid = _read_pid(pid_file)
        owned_pids.add(first_pid)
        assert _pid_running(first_pid)

        text = registry.execute("playwright_get_text", {"selector": "body"})
        evaluated = registry.execute("playwright_evaluate", {"js_code": "large()"})
        assert text.success and "truncated" in text.content
        assert len(json.dumps(text.content, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
        assert evaluated.success and evaluated.content["truncated"] is True
        assert len(json.dumps(evaluated.content, ensure_ascii=False).encode("utf-8")) < 48 * 1024

        token = worker._token
        assert _plugin(worker.reload_plugin(PLUGIN_ID))["state"] == "active"
        assert worker._token == token
        _wait_pid_gone(first_pid)

        assert registry.execute("playwright_screenshot", {}).success is True
        second_pid = _read_pid(pid_file)
        owned_pids.add(second_pid)
        assert second_pid != first_pid and _pid_running(second_pid)
        assert _plugin(worker.set_plugin_enabled(PLUGIN_ID, False))["effectCount"] == 0
        _wait_pid_gone(second_pid)

        assert _plugin(worker.set_plugin_enabled(PLUGIN_ID, True))["state"] == "active"
        assert registry.execute("playwright_screenshot", {}).success is True
        third_pid = _read_pid(pid_file)
        owned_pids.add(third_pid)
        assert _pid_running(third_pid)
        hang_file.touch()
        old_token = worker._token
        rebuilt = worker.reload_plugin(PLUGIN_ID)
        assert worker._token != old_token
        assert _plugin(rebuilt)["state"] == "active"
        _wait_pid_gone(third_pid)
        hang_file.unlink()
        assert {item.name for item in registry.all()} == TOOL_NAMES

        assert registry.execute("playwright_screenshot", {}).success is True
        fourth_pid = _read_pid(pid_file)
        owned_pids.add(fourth_pid)
        assert _pid_running(fourth_pid)
    finally:
        hang_file.unlink(missing_ok=True)
        worker.close()
        for pid in owned_pids:
            if _pid_running(pid):
                psutil.Process(pid).kill()
            _wait_pid_gone(pid)


def test_tool_artifact_descriptor_mismatch_releases_committed_payload(tmp_path: Path) -> None:
    from app.core_host.plugin_artifacts import PluginArtifactStore
    from app.core_host.plugin_host_services import HostServiceError, _ArtifactsHostService

    store = PluginArtifactStore(tmp_path, "generation-artifact-failure")
    allocated = store.allocate(PLUGIN_ID, {"mediaType": "image/jpeg", "suffix": ".jpg"})
    Path(allocated["path"]).write_bytes(b"image")
    descriptor = store.commit(PLUGIN_ID, allocated["artifactId"])
    service = _ArtifactsHostService(store)
    with pytest.raises(HostServiceError) as raised:
        service.consume_tool_result(
            {
                "content": {},
                "artifact": {**descriptor, "byteLength": descriptor["byteLength"] + 1},
            }
        )
    assert raised.value.code == "TOOL_ARTIFACT_INVALID"
    assert store.count == 0


def test_generic_plugin_bridge_has_no_playwright_implementation_branch() -> None:
    generic_files = (
        REPOSITORY_ROOT / "app" / "core_host" / "plugin_worker.py",
        REPOSITORY_ROOT / "app" / "core_host" / "plugin_worker_runtime.py",
        REPOSITORY_ROOT / "app" / "core_host" / "plugin_host_services.py",
        REPOSITORY_ROOT / "app" / "plugins" / "kernel.py",
        REPOSITORY_ROOT / "app" / "plugins" / "host_services.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in generic_files)
    assert "playwright_browser" not in source
    assert "PLAYWRIGHT_" not in source
