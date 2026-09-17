from __future__ import annotations

import json
import shutil
from pathlib import Path
from threading import Event

import pytest

from app.plugin_sdk.sakura_tools import ToolRegistry
from app.config.core_config_reader import CoreConfigReader
from app.core_host.assistant_adapter import AssistantAdapter
from app.core_host.protocol import response
from app.core_host.server import HostConfig
from app.storage.runtime_roots import RuntimeRoots


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_3_01" / "ready"
)
PLANTED_API_KEY = "PLANTED_API_KEY_DO_NOT_LEAK"
PLANTED_ENDPOINT = "https://planted-endpoint.invalid/private-v1"
PLANTED_MODEL = "planted-private-model"
PLANTED_PROMPT = "PLANTED_PRIVATE_SYSTEM_PROMPT"
PLANTED_CREDENTIAL = "abcdeffedcba0123456789abcdef0123"


def _fresh_secret_root(tmp_path: Path) -> Path:
    root = tmp_path / "private-absolute-app-root"
    shutil.copytree(FIXTURE_ROOT, root)
    (root / "config" / "api.yaml").write_text(
        f"""\
api_profiles:
  - id: planted
    alias: Planted
    base_url: {PLANTED_ENDPOINT}
    api_key: {PLANTED_API_KEY}
    models:
      - name: {PLANTED_MODEL}
model_slots:
  chat:
    profile_id: planted
    model: {PLANTED_MODEL}
""",
        encoding="utf-8",
    )
    (root / "characters" / "sakura" / "card.md").write_text(
        PLANTED_PROMPT,
        encoding="utf-8",
    )
    return root


class _AssistantApplication:
    def active_models(self):
        return {}

    def service_identity(self, _key):
        return {"pluginId": "fixture-assistant", "scopeId": "fixture-scope"}

    def call_bound_service(self, _key, _identity, method, *_args):
        assert method == "prepare"
        return {"state": "ready", "code": "READY", "message": "ready", "retryable": False}


def _adapter(root):
    adapter = AssistantAdapter(root)
    adapter.bind_application(_AssistantApplication())
    return adapter


def test_readiness_initialization_retains_uncaught_producer_error(tmp_path, monkeypatch):
    from app.core_host.server import ReadinessController

    errors = []
    monkeypatch.setattr("app.core_host.assistant_adapter.log_event",
        lambda _channel, _message, attributes, **kwargs: errors.append((attributes, kwargs)))

    class Initializer:
        def initialize(self, _cancel):
            try:
                raise OSError("fixture filesystem unavailable")
            except OSError as cause:
                raise RuntimeError("fixture initialization failed") from cause

        def close(self):
            pass

    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "diagnostic-generation", "a" * 32),
        initializer_factory=lambda *_args: Initializer(),
    )
    try:
        controller.begin({})
        controller._worker.join(2)
        assert controller.readiness() == "failed"
        attributes, event = errors[-1]
        assert event["event"] == "assistant.initialization.failed"
        assert attributes["stage"] == "assistant_initializer"
        assert "fixture filesystem unavailable" in str(attributes)
        assert "initialize" in attributes["exception_stack"]
    finally:
        controller.close()


def test_host_config_repr_excludes_generation_credential() -> None:
    config = HostConfig(
        roots=RuntimeRoots(
            Path("/isolated/not-read/secret-repr"),
            Path("/isolated/not-read/secret-repr"),
        ),
        generation_id="generation",
        generation_credential=PLANTED_CREDENTIAL,
    )

    assert PLANTED_CREDENTIAL not in repr(config)


def test_reader_and_readiness_repr_hide_provider_secrets(tmp_path: Path) -> None:
    root = _fresh_secret_root(tmp_path)
    read_result = CoreConfigReader().read(root)
    readiness = _adapter(root).initialize(Event())

    for output in (repr(read_result), repr(readiness)):
        assert PLANTED_API_KEY not in output
        assert PLANTED_ENDPOINT not in output
        assert PLANTED_MODEL not in output
        assert PLANTED_PROMPT not in output
        assert str(root) not in output


def test_public_projection_contains_no_private_provider_prompt_or_paths(tmp_path: Path) -> None:
    root = _fresh_secret_root(tmp_path)
    readiness = _adapter(root).initialize(Event())
    assert readiness.current_character_summary is not None
    serialized = json.dumps(readiness.current_character_summary, ensure_ascii=False)

    assert set(readiness.current_character_summary) == {
        "id",
        "displayName",
        "initialMessage",
        "replyTones",
        "portraitChoices",
    }
    for secret in (
        PLANTED_API_KEY,
        PLANTED_ENDPOINT,
        PLANTED_MODEL,
        PLANTED_PROMPT,
        PLANTED_CREDENTIAL,
        str(root),
    ):
        assert secret not in serialized


def test_sanitized_character_issue_and_failure_surfaces_do_not_leak(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _fresh_secret_root(tmp_path)
    private_package = root / "characters" / "private-broken"
    private_package.mkdir()
    (private_package / "character.json").write_text(
        json.dumps(
            {
                "id": "private-broken",
                "display_name": PLANTED_ENDPOINT,
                "initial_message": PLANTED_API_KEY,
                "card": PLANTED_PROMPT,
            }
        ),
        encoding="utf-8",
    )

    readiness = _adapter(root).initialize(Event())
    observed = "\n".join((repr(readiness), readiness.message, capsys.readouterr().err))

    for secret in (
        PLANTED_API_KEY,
        PLANTED_ENDPOINT,
        PLANTED_MODEL,
        PLANTED_PROMPT,
        PLANTED_CREDENTIAL,
        str(root),
    ):
        assert secret not in observed


def test_protocol_keeps_generation_credential_out_of_public_payload() -> None:
    envelope = response(
        {"id": "request", "name": "system.health"},
        generation_id="generation",
        generation_credential=PLANTED_CREDENTIAL,
        payload={"readiness": "ready"},
    )
    assert envelope["generationCredential"] == PLANTED_CREDENTIAL
    assert PLANTED_CREDENTIAL not in json.dumps(envelope["payload"])


def test_initialization_keeps_original_failure_without_closing_shared_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host import assistant_adapter

    root = _fresh_secret_root(tmp_path)
    recorded = []
    closed = []

    class Application(_AssistantApplication):
        def call_bound_service(self, *_args):
            try:
                raise OSError(f"fixture model configuration failed: {PLANTED_API_KEY}")
            except OSError as cause:
                raise RuntimeError("assistant prepare failed") from cause

        def close(self):
            closed.append(True)

    monkeypatch.setattr(assistant_adapter, "log_event", lambda *args, **kwargs: recorded.append((args, kwargs)))
    adapter = AssistantAdapter(root)
    adapter.bind_application(Application())
    result = adapter.initialize(Event())
    adapter.close()

    assert result.state == "failed"
    assert result.code == "ASSISTANT_INITIALIZATION_FAILED"
    assert closed == []
    failure = recorded[0][0][2]
    assert failure["stage"] == "assistant_service"
    assert failure["error_type"] == "RuntimeError"
    assert failure["cause_type"] == "OSError"
    assert "fixture model configuration failed" in failure["diagnostic"]
    assert "call_bound_service" in failure["exception_stack"]
    assert PLANTED_API_KEY not in repr(recorded)
    assert "fixture" not in result.message
