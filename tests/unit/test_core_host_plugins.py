from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.character_loader import CharacterProfile
from app.storage.runtime_roots import RuntimeRoots


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_4_04"


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(FIXTURE_ROOT / "plugins", root / "plugins" / "builtin")
    (root / "plugins" / "builtin" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config").mkdir()
    return root


def _wait_until(predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before the deadline")


def test_plugin_reads_history_range_for_current_character(tmp_path: Path) -> None:
    from app.core_host.plugin_application import PluginApplicationHost
    from app.plugin_sdk.sakura_tools import ToolRegistry
    from app.storage.paths import StoragePaths
    from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore

    root = tmp_path / "plugins" / "builtin" / "history_reader"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "api: 4\nid: history.reader\nname: 历史读取\nversion: 1.0.0\nentry: plugin:Plugin\n"
        "provides: [fixture.history]\nrequires: [sakura.host.timeline]\n", encoding="utf-8",
    )
    (root / "plugin.py").write_text('''
class Plugin:
    def setup(self, context):
        self.timeline = context.get("sakura.host.timeline")
        context.provide("fixture.history", self, exports=("read",))

    def read(self, request):
        return self.timeline.read_range(request)
''', encoding="utf-8")
    timeline = TimelineStore(StoragePaths(tmp_path).timeline_database())
    timeline.initialize()
    for identity, character, date in (
        ("selected", "sakura", "2026-10-02"),
        ("outside-range", "sakura", "2026-10-01"),
        ("other-character", "other", "2026-10-02"),
    ):
        timeline.append(NewTimelineEntry(identity, identity, character, TimelineKind.HUMAN,
            "chat", date + "T12:00:00Z", {"text": identity}))
    application = PluginApplicationHost(tmp_path, "history-range", ToolRegistry())
    try:
        application.start()
        assert application.wait_until_loaded(timeout=5)
        for character, expected in (("sakura", "selected"), ("other", "other-character")):
            application.bind_session(SimpleNamespace(
                assistant=object(), visual_binding=None,
                character=CharacterProfile(character, character, tmp_path, tmp_path / "card.md", ""),
            ))
            page = application.call_service("fixture.history", "read", {
                "startDate": "2026-10-02", "endDate": "2026-10-02",
            })
            assert [entry["entryId"] for entry in page["entries"]] == [expected]
            assert page["total"] == 1 and not page["hasMore"]
    finally:
        application.close()


def test_assistant_failure_keeps_plugin_application_manageable(tmp_path: Path) -> None:
    from app.core_host.server import HostConfig, ReadinessController

    class FailingInitializer:
        def initialize(self, _cancel) -> object:
            raise RuntimeError("assistant fixture failure")

        def close(self) -> None:
            pass

    root = _assistant_root(tmp_path)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(root, root), "generation-plugin-application", "a" * 32),
        initializer_factory=lambda _root, _tools: FailingInitializer(),
    )
    controller.enable_plugins()
    try:
        controller.begin({})
        _wait_until(lambda: controller.readiness() == "failed")
        application = controller.published_plugin_application()
        assert application is not None
        application.wait_until_loaded(timeout=5)

        snapshot = application.settings_snapshot()
        fixture = next(
            item for item in snapshot["plugins"] if item["pluginId"] == "fixture_plugin"
        )
        assert fixture["state"] == "active"
        assert application.settings_save(
            "fixture_plugin",
            "general",
            {"label": "available-without-assistant"},
        ) == {
            "saved": True,
            "applicationState": "applied",
            "reasonCode": "READY",
        }

        disabled = application.set_enabled(fixture["installId"], False)
        disabled_fixture = next(
            item for item in disabled["plugins"] if item["pluginId"] == "fixture_plugin"
        )
        assert disabled_fixture["state"] == "disabled"
        assert disabled["applicationState"] == "applied"
        assert controller.published_session() is None
    finally:
        controller.close()


@pytest.mark.parametrize("binding_fails", [False, True])
def test_session_is_published_only_after_application_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_fails: bool,
) -> None:
    from app.core_host import plugin_application
    from app.core_host.assistant_adapter import ReadinessResult
    from app.core_host.server import HostConfig, ReadinessController

    entered = threading.Event()
    release = threading.Event()
    retired = []
    published = []
    speech_boundaries = []
    session = SimpleNamespace()

    class Initializer:
        def initialize(self, _cancel):
            return ReadinessResult(
                state="ready", code="READY", message="", retryable=False,
                current_character_summary=None, current_character_presentation=None,
                session=session,
            )

        def retire_session(self):
            retired.append(True)

        def close(self):
            release.set()

    class Application:
        unbound = 0

        def bind_tts_boundary(self, boundary):
            speech_boundaries.append(boundary)

        def start_character_presentation(self):
            return None

        def start_assistant(self):
            pass

        def start(self):
            pass

        def bind_session(self, _session):
            entered.set()
            assert release.wait(3)
            if binding_fails:
                raise RuntimeError("binding failed")

        def unbind_session(self):
            self.unbound += 1

        def close(self):
            pass

    application = Application()
    monkeypatch.setattr(plugin_application, "PluginApplicationHost", lambda *_, **_kwargs: application)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "session-binding-test", "a" * 32),
        initializer_factory=lambda *_: Initializer(),
    )
    controller.set_session_published_callback(lambda: published.append(controller.published_session()))
    first_tts = object()
    controller.bind_tts_boundary(first_tts)
    controller.enable_plugins()
    try:
        controller.begin({})
        assert entered.wait(2)
        assert speech_boundaries == [first_tts]
        assert controller.published_session() is None
        assert controller.readiness() == "initializing"
        assert published == []
        release.set()
        controller._worker.join(2)
        assert not controller._worker.is_alive()
        if binding_fails:
            assert controller.published_session() is None
            assert controller.snapshot()["components"]["assistant"]["code"] == "SESSION_BIND_FAILED"
            assert retired == [True]
            assert application.unbound == 1
            assert published == []
        else:
            assert controller.published_session() is session
            assert retired == []
            assert published == [session]
            replacement_tts = object()
            controller.bind_tts_boundary(replacement_tts)
            assert speech_boundaries == [first_tts, replacement_tts]
    finally:
        release.set()
        controller.close()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_plugin_start_failure_closes_unpublished_application_resources(
    tmp_path: Path, monkeypatch, cleanup_fails: bool,
) -> None:
    from app.core_host import plugin_application
    from app.core_host.server import HostConfig, ReadinessController

    closed: list[str] = []

    class PluginApplication:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start_character_presentation(self):
            raise RuntimeError("plugin start failed")

        def close(self) -> None:
            closed.append("plugins")
            if cleanup_fails:
                raise RuntimeError("plugin cleanup failed")

    monkeypatch.setattr(plugin_application, "PluginApplicationHost", PluginApplication)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "generation-failed-start", "a" * 32),
    )
    controller.enable_plugins()
    controller.begin({})
    controller._worker.join(2)
    assert not controller._worker.is_alive()
    assert controller.readiness() == "failed"
    assert controller.published_plugin_application() is None
    assert closed == ["plugins"]
    if cleanup_fails:
        with pytest.raises(RuntimeError, match="plugin cleanup failed"):
            controller.close()
    else:
        controller.close()
    controller.close()
    assert closed == ["plugins"]


@pytest.mark.parametrize("phase", ["visual", "assistant", "optional"])
def test_shutdown_stops_starting_plugin_application_before_joining_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    from app.core_host import plugin_application
    from app.core_host.assistant_adapter import ReadinessResult
    from app.core_host.server import HostConfig, ReadinessController

    started = threading.Event()
    stopped = threading.Event()
    calls: list[str] = []
    failures = []
    monkeypatch.setattr("app.core_host.assistant_adapter.report_assistant_failure",
                        lambda error, **kwargs: failures.append(error))

    class Application:
        def enter(self, stage):
            calls.append(stage)
            if phase == stage:
                started.set()
                assert stopped.wait(3)
                calls.append("start_returned")
                if phase == "visual":
                    from app.core_host.visual_host import VisualHostError
                    raise VisualHostError("VISUAL_BINDING_EXPIRED")

        def start_character_presentation(self):
            self.enter("visual")

        def start_assistant(self):
            self.enter("assistant")

        def start(self):
            self.enter("optional")

        def close(self) -> None:
            calls.append("close")
            stopped.set()

    application = Application()
    def create_initializer(*_):
        assert phase == "optional", "shutdown must not initialize Assistant"
        return SimpleNamespace(
            initialize=lambda _: ReadinessResult("setup_required", "CHARACTER_REQUIRED", "", False, None),
            close=lambda: None,
        )
    monkeypatch.setattr(plugin_application, "PluginApplicationHost", lambda *_, **_kwargs: application)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "shutdown-starting-plugin", "a" * 32),
        initializer_factory=create_initializer,
    )
    controller.enable_plugins()
    try:
        controller.begin({})
        assert started.wait(2)
        assert controller.published_plugin_application() is (None if phase == "visual" else application)
        controller.close()
        assert not controller._worker.is_alive()
        assert not failures
        assert controller.published_plugin_application() is None
        controller.close()
        expected = ["visual", "assistant", "optional"][:["visual", "assistant", "optional"].index(phase) + 1]
        assert calls == [*expected, "close", "start_returned"]
    finally:
        stopped.set()
        controller._worker.join(3)
        controller.close()
