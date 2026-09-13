from types import SimpleNamespace
import threading

from app.core_host.server import HostConfig, ReadinessController
from app.storage.runtime_roots import RuntimeRoots


def _summary(role):
    return dict(id=role, displayName=role, initialMessage=f"hello {role}", replyTones=[], portraitChoices=[])


def _presentation(role):
    return dict(schemaVersion=2, generationId="generation", characterId=role,
                displayName=role, initialMessage=f"hello {role}", themeTokens={},
                visual=None, visualReasonCode="VISUAL_RESOURCE_MISSING")


def _controller(tmp_path):
    controller = ReadinessController(HostConfig(RuntimeRoots(tmp_path, tmp_path), "generation", "a" * 32))
    controller._readiness = "ready"
    controller._revision = 2
    controller._current_character_summary = _summary("alpha")
    controller._current_character_presentation = _presentation("alpha")
    return controller


def test_switch_publishes_summary_and_final_visual_together(tmp_path, monkeypatch):
    from app.config.core_config_reader import CoreConfigReader
    from app.config.settings_service import AppSettingsService

    controller = _controller(tmp_path)
    during = []
    visual = _presentation("alpha")

    def bind(*_args):
        nonlocal visual
        visual = _presentation("beta")
        # Force a snapshot at the exact boundary between visual preparation and
        # Session publication, as the native lifecycle poll can do in production.
        during.append(controller.minimal_snapshot(None))

    application = SimpleNamespace(
        application=SimpleNamespace(set_current_character=lambda _: None),
        unbind_session=lambda: None, bind_session=bind,
        bind_character_presentation=bind, visual_presentation=lambda: visual,
    )
    controller._plugin_application = application
    controller._worker = object()
    controller._initializer = SimpleNamespace(
        retire_session=lambda: None,
        initialize=lambda _: SimpleNamespace(
            state="ready", code="READY", retryable=False, session=object(),
            current_character_summary=_summary("beta"),
            current_character_presentation=_presentation("beta"),
        ),
    )
    monkeypatch.setattr(AppSettingsService, "load_current_character_id", lambda *_: "beta")
    monkeypatch.setattr(CoreConfigReader, "read", lambda *_: SimpleNamespace(
        config_problem=None, provider_selection=object(),
    ))
    before = controller.minimal_snapshot(None)
    controller.switch_character_session()
    assert during and all(snapshot == before for snapshot in during)
    after = controller.minimal_snapshot(None)
    assert after["generationId"] == before["generationId"]
    assert after["currentCharacterSummary"]["id"] == "beta"
    assert after["characterPresentation"] == _presentation("beta")
    assert after["revision"] > before["revision"]


def test_late_visual_refresh_cannot_overwrite_new_session(tmp_path):
    controller = _controller(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def project():
        entered.set()
        assert release.wait(3)
        return _presentation("alpha")

    controller._plugin_application = SimpleNamespace(visual_presentation=project)
    worker = threading.Thread(target=controller._refresh_visual_presentation)
    worker.start()
    try:
        assert entered.wait(3)
        with controller._lock:
            controller._current_character_summary = _summary("beta")
            controller._current_character_presentation = _presentation("beta")
            controller._revision += 1
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert controller._current_character_presentation == _presentation("beta")


def test_visual_refresh_never_mixes_character_identity_with_session(tmp_path):
    controller = _controller(tmp_path)
    controller._plugin_application = SimpleNamespace(visual_presentation=lambda: _presentation("beta"))
    snapshot = controller.minimal_snapshot(None)
    assert snapshot["currentCharacterSummary"]["id"] == snapshot["characterPresentation"]["characterId"]
