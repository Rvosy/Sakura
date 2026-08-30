from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from plugins.optional.playwright_browser import browser, plugin as playwright_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "optional" / "playwright_browser"
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


def _fake_playwright_wheel(parent: Path) -> Path:
    wheel = parent / "playwright-1.40.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("playwright/__init__.py", "")
        archive.writestr(
            "playwright-1.40.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: playwright\nVersion: 1.40.0\n",
        )
        archive.writestr(
            "playwright-1.40.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: sakura-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("playwright-1.40.0.dist-info/RECORD", "")
    return wheel


def test_optional_playwright_installs_through_user_entry_and_runs_in_v4(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost
    from app.plugins.installer import LocalPluginInstaller
    from app.plugins.inventory import PluginDesiredStateStore
    from app.storage.runtime_roots import RuntimeRoots

    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    (distribution / "plugins/builtin").mkdir(parents=True)
    user.mkdir()
    roots = RuntimeRoots(distribution, user)
    source = tmp_path / "source"
    shutil.copytree(SOURCE_PLUGIN_ROOT, source)
    source_browser = source / "browser.py"
    source_browser.write_text(
        source_browser.read_text(encoding="utf-8")
        + "\n\ndef get_text(selector=\"body\"):\n"
        + "    import time\n"
        + "    time.sleep(3.2)\n"
        + "    return \"slow:\" + selector\n",
        encoding="utf-8",
    )
    wheel = _fake_playwright_wheel(tmp_path)
    (source / "requirements.txt").write_text(str(wheel.resolve()) + "\n", encoding="utf-8")
    installed = LocalPluginInstaller(roots).install(source.resolve(), "folder")
    PluginDesiredStateStore(user).set(installed.plugin_id, True)

    registry = ToolRegistry()
    application = PluginApplicationHost(roots, "generation-playwright-v4", registry)
    try:
        application.start()
        record = next(
            item
            for item in application.public_snapshot()["plugins"]
            if item["pluginId"] == PLUGIN_ID
        )
        assert record["source"] == "user"
        assert record["state"] == "active"
        assert record["supported"] is True
        assert {item.name for item in registry.all()} == TOOL_NAMES

        started_at = time.monotonic()
        slow_result = registry.execute("playwright_get_text", {"selector": "main"})
        elapsed = time.monotonic() - started_at
        assert elapsed >= 3.0
        assert slow_result.success is True
        assert slow_result.content == "slow:main"

        settings = next(
            item
            for item in application.settings_snapshot()["plugins"]
            if item["pluginId"] == PLUGIN_ID
        )["sections"]
        assert settings[0]["sectionId"] == PLUGIN_ID
        assert application.settings_save(
            PLUGIN_ID,
            PLUGIN_ID,
            {"headless": True},
        ) == {
            "saved": True,
            "applicationState": "applied",
            "reasonCode": "READY",
        }

        disabled = application.set_enabled(installed.install_id, False)
        disabled_record = next(
            item for item in disabled["plugins"] if item["pluginId"] == PLUGIN_ID
        )
        assert disabled_record["state"] == "disabled"
        assert registry.all() == []
    finally:
        application.close()


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


def test_config_is_applied_in_place() -> None:
    config = _SetupConfig({"headless": True})
    context = _SetupContext(config)
    playwright_plugin.PlaywrightBrowserPlugin().setup(context)
    try:
        assert browser._config_loader is not None
        assert browser._config_loader().headless is True

        _descriptor, callbacks = context.settings.registrations[0]
        assert callbacks["save"]({"headless": False}) == ["applied"]
        assert callbacks["load"]() == {"headless": False}
        assert browser._config_loader().headless is False
    finally:
        for cleanup in reversed(context.cleanups):
            cleanup()

    reloaded = _SetupContext(config)
    playwright_plugin.PlaywrightBrowserPlugin().setup(reloaded)
    try:
        assert browser._config_loader is not None
        assert browser._config_loader().headless is False
    finally:
        for cleanup in reversed(reloaded.cleanups):
            cleanup()


def test_system_browser_order_is_platform_specific() -> None:
    assert browser._system_browser_channels("win32") == ("msedge", "chrome")
    assert browser._system_browser_channels("darwin") == ("chrome", "msedge")


def test_system_browser_missing_has_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Chromium:
        def launch(self, *, channel: str, headless: bool) -> object:
            calls.append(channel)
            assert headless is True
            raise RuntimeError("not installed")

    monkeypatch.setattr(browser, "_system_browser_channels", lambda: ("chrome", "msedge"))
    with pytest.raises(browser.BrowserRuntimeMissing) as caught:
        browser._launch_system_browser(type("Playwright", (), {"chromium": Chromium()})(), True)

    assert caught.value.code == "BROWSER_RUNTIME_MISSING"
    assert calls == ["chrome", "msedge"]


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
