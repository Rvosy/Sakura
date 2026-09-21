from types import SimpleNamespace
import threading
import pytest

from app.core_host.server import HostConfig, ReadinessController
from app.storage.runtime_roots import RuntimeRoots
from app.config.character_loader import CharacterProfile
from app.core_host.assistant_adapter import AssistantSession, ReadinessResult, project_current_character_summary
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugin_sdk.sakura_assistant_contract import RuntimeLoopSettings
from app.plugin_sdk.sakura_tools import ToolRegistry


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


def _session(tmp_path, role):
    character = CharacterProfile(role, role, tmp_path, tmp_path / f"{role}.md", f"hello {role}", reply_tones=[])
    return AssistantSession(character, object(), RuntimeLoopSettings(), "test", system_prompt=f"prompt {role}")


@pytest.fixture
def published_application(tmp_path):
    controller = _controller(tmp_path)
    application = PluginRuntimeApplication(controller._config.roots, "generation", ToolRegistry(), specs=())
    session = _session(tmp_path, "alpha")
    application.bind_session(session)
    controller._plugin_application = application
    controller._session = session
    controller._current_character_summary = project_current_character_summary(session.character)
    controller._current_character_presentation = controller._project_presentation(application.visual_presentation())
    controller._worker = threading.Thread(target=lambda: None)
    controller._worker.start()
    controller._worker.join()
    controller._initializer = SimpleNamespace(close=lambda: None)
    try:
        yield controller, application
    finally:
        controller.close()


@pytest.mark.parametrize("first_failure", [None, "exception", "result"])
def test_switch_publishes_summary_and_final_visual_together(published_application, tmp_path, monkeypatch, first_failure):
    controller, application = published_application
    original = controller.published_session()
    replacement = _session(tmp_path, "beta")
    attempts = []

    def initialize(_):
        attempts.append(True)
        if len(attempts) == 1 and first_failure == "exception":
            raise RuntimeError("isolated initialization failure")
        if len(attempts) == 1 and first_failure == "result":
            return ReadinessResult("failed", "ASSISTANT_INITIALIZATION_FAILED", "fixture", False, None)
        return ReadinessResult("ready", "READY", "fixture", False,
            project_current_character_summary(replacement.character), _presentation("beta"), replacement)

    controller._initializer.initialize = initialize
    before = controller.minimal_snapshot(None)
    if first_failure:
        with pytest.raises(RuntimeError):
            controller.switch_character_session()
        assert controller.published_session() is original
        assert controller.minimal_snapshot(None) == before

    during, errors = [], []
    entered, release = threading.Event(), threading.Event()
    prepare = application.prepare_character

    def prepare_candidate(*args, **kwargs):
        candidate = prepare(*args, **kwargs)
        # Provider description and candidate projection are the preparation
        # boundary. bind_session now only commits this already-prepared value.
        assert not controller._lock.locked()
        during.append(controller.minimal_snapshot(None))
        entered.set()
        assert release.wait(3)
        return candidate

    monkeypatch.setattr(application, "prepare_character", prepare_candidate)

    def switch():
        try:
            controller.switch_character_session()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=switch)
    worker.start()
    try:
        assert entered.wait(3)
        # A real reader on another thread can poll while preparation is blocked.
        assert controller.minimal_snapshot(None) == before
        assert controller.published_session() is original
        assert application._session is original
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert during == [before]
    after = controller.minimal_snapshot(None)
    assert after["generationId"] == before["generationId"]
    assert after["currentCharacterSummary"]["id"] == "beta"
    assert after["characterPresentation"] == controller._project_presentation(application.visual_presentation())
    assert after["characterPresentation"]["characterId"] == "beta"
    assert controller.published_session() is application._session is replacement
    assert after["revision"] == before["revision"] + 1


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


@pytest.mark.parametrize("close_during_prepare", [False, True])
def test_same_character_save_keeps_complete_snapshot_while_projection_is_prepared(published_application, monkeypatch, close_during_prepare):
    from dataclasses import replace
    from app.config.character_loader import CharacterRegistry
    from app.config.settings_service import AppSettingsService
    from app.plugin_sdk.sakura_cancellation import OperationCancelled

    controller, application = published_application
    session = controller.published_session()
    before = controller.minimal_snapshot(None)
    entered, release = threading.Event(), threading.Event()
    character = replace(session.character, display_name="New name", initial_message="New greeting")
    new_summary = project_current_character_summary(character)
    monkeypatch.setattr(CharacterRegistry, "get", lambda *_: character)
    monkeypatch.setattr(AppSettingsService, "load_current_character_id", lambda *_: "alpha")
    monkeypatch.setattr("app.config.character_loader.load_character_system_prompt", lambda _: "new prompt")
    prepared, closed_candidates = [], []
    prepare = application.prepare_character

    def prepare_candidate(*args, **kwargs):
        candidate = prepare(*args, **kwargs)
        prepared.append(candidate)
        close = candidate.close

        def close_candidate():
            closed_candidates.append(candidate)
            close()

        monkeypatch.setattr(candidate, "close", close_candidate)
        assert not controller._lock.locked()
        entered.set()
        assert release.wait(3)
        return candidate

    monkeypatch.setattr(application, "prepare_character", prepare_candidate)
    errors = []
    def save():
        try:
            controller.apply_character_configuration()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=save)
    worker.start()
    try:
        assert entered.wait(3)
        snapshot = controller.minimal_snapshot(None)
        assert snapshot == before
        assert controller.published_session() is application._session is session
        if close_during_prepare:
            controller.close()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    if close_during_prepare:
        assert len(errors) == 1 and isinstance(errors[0], OperationCancelled)
        assert closed_candidates == prepared
        assert controller.published_session() is None
        assert application._session is None
        assert controller._revision == before["revision"]
        return
    assert not errors
    assert not closed_candidates
    snapshot = controller.minimal_snapshot(None)
    assert snapshot["currentCharacterSummary"] == new_summary
    assert snapshot["characterPresentation"] == controller._project_presentation(prepared[0].presentation)
    assert snapshot["revision"] == before["revision"] + 1
    assert controller.published_session() is application._session
    assert controller.published_session().system_prompt == "new prompt"
