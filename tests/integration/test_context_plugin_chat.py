from __future__ import annotations

import io
import json
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.plugin_sdk.sakura_tools import ToolRegistry
from app.config.character_loader import CharacterProfile
from app.core_host.assistant_adapter import AssistantSession, BoundAssistant
from app.plugin_sdk.sakura_assistant_contract import RuntimeLoopSettings
from app.plugins.dependencies import PluginDependencyRoots
import shutil
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
from app.plugin_sdk.sakura_context import ContextRequest
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore


GENERATION_ID = "context-plugin-chat"
GENERATION_CREDENTIAL = "54" * 16
PLUGIN_ID = "fixture.context.rules"
SERVICE_KEY = "fixture.context.inspection"
RULE = "Use Japanese for this interaction and correct grammar before answering."
REFERENCE = "The practice text is about a fictional trip to Kyoto."
LARGE_OPTIONAL = "x" * 9000
LARGE_REQUIRED = "  Required beginning\n" + "r" * 8194 + "\nRequired ending  "


def _write_plugin(distribution: Path) -> None:
    root = distribution / "plugins" / "builtin" / PLUGIN_ID
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        f"api: 4\nid: {PLUGIN_ID}\nname: Context fixture\nversion: 1.0.0\n"
        f"entry: plugin:Plugin\nprovides: [{SERVICE_KEY}]\n"
        "requires: [sakura.host.context, sakura.host.tools, sakura.host.artifacts]\n",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        f'''
import os

class Plugin:
    def setup(self, context):
        host = context.get("sakura.host.context")
        self.capabilities = host.describe()
        self.calls = 0
        self.recent_messages = []
        self.context = context
        self.remove_image = context.get("sakura.host.tools").register({{"name": "fixture_image", "description": "Read fixture image.", "parameters": {{"type": "object", "properties": {{}}}}}}, self.image)
        host.register(
            {{
                "providerId": "fixture.context.rules.contribution",
                "scope": "turn",
                "failurePolicy": "abort",
            }},
            self.contribute,
        )
        context.provide("{SERVICE_KEY}", self, exports=("inspect",))

    def image(self, arguments):
        from pathlib import Path
        artifacts = self.context.get("sakura.host.artifacts")
        value = artifacts.allocate({{"mediaType": "image/png", "suffix": ".png"}})
        Path(value["path"]).write_bytes(b"x" * 1300000)
        return {{"content": "fixture screen", "artifact": artifacts.commit(value["artifactId"])}}

    def inspect(self):
        return {{"pid": os.getpid(), "capabilities": self.capabilities, "calls": self.calls, "recentMessages": self.recent_messages}}

    def contribute(self, request):
        self.calls += 1
        self.recent_messages = request["recent_messages"]
        if request["current_input"] == "invoke expired fixture":
            self.remove_image()
        if request["current_input"] == "fail required contribution":
            raise RuntimeError("private callback detail must not escape")
        if request["current_input"] == "transport large context":
            return [
                {{"id": f"optional-{{index}}", "content": (
                    {LARGE_OPTIONAL!r} if index == 0 else f"Optional content {{index}}"
                ), "budgetHint": 4096}}
                for index in range(20)
            ] + [
                {{"id": f"required-{{index}}", "required": True, "content": (
                    {LARGE_REQUIRED!r} if index == 0 else f"Required content {{index}}"
                )}}
                for index in range(20)
            ]
        return [
            {{"id": "practice-rule", "required": True,
              "content": {RULE!r}, "budgetHint": 512}},
            {{"id": "practice-reference", "content": {REFERENCE!r}, "budgetHint": 512}},
        ]
''',
        encoding="utf-8",
    )


@dataclass
class _ChatFixture:
    application: PluginApplicationHost
    boundary: RealChatBoundary
    timeline: TimelineStore
    requests: list[dict[str, Any]]
    events: list[dict[str, Any]]
    session: AssistantSession

    def send(self, operation_id: str, message: str) -> dict[str, Any]:
        request = {
            "id": operation_id,
            "kind": "request",
            "name": "chat.send",
            "generationId": GENERATION_ID,
            "generationCredential": GENERATION_CREDENTIAL,
            "payload": {"operationId": operation_id, "message": message},
        }
        self.boundary.reserve_send(request)
        self.boundary.handle_send(request)
        terminal = self.events[-1]
        assert terminal["payload"]["operationId"] == operation_id
        return terminal


@pytest.fixture
def chat(tmp_path: Path, request: pytest.FixtureRequest, monkeypatch, assistant_dependencies) -> Iterator[_ChatFixture]:
    # Each test owns both the plugin distribution and all writable runtime data.
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    _write_plugin(distribution)
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins" / "builtin" / "sakura_assistant", distribution / "plugins" / "builtin" / "sakura_assistant", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_model_openai_compatible", distribution / "plugins/builtin/sakura_model_openai_compatible", ignore=shutil.ignore_patterns("__pycache__"))
    original_root = PluginDependencyRoots.verified_root
    monkeypatch.setattr(PluginDependencyRoots, "verified_root", lambda self, plugin_id, *args, **kwargs: assistant_dependencies if plugin_id == "sakura.model.openai_compatible" else original_root(self, plugin_id, *args, **kwargs))
    requests: list[dict[str, Any]] = []
    reject_noninitial_system = getattr(request, "param", False)

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler contract
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if reject_noninitial_system and any(
                message["role"] == "system" for message in requests[-1]["messages"][1:]
            ):
                body = b'{"error":{"message":"System message must be at the beginning."}}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            content = json.dumps(
                {"segments": [{"ja": "一緒に練習しましょう。", "zh": "一起练习吧。"}]},
                ensure_ascii=False,
            )
            response_message = {"role": "assistant", "content": content}
            if any(message.get("content") in ("invoke image fixture", "invoke expired fixture") for message in requests[-1]["messages"]) and not any(message.get("role") == "tool" for message in requests[-1]["messages"]):
                response_message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-image", "type": "function", "function": {"name": "fixture_image", "arguments": "{}"}}]}
            body = json.dumps({"choices": [{"message": response_message}]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    provider.daemon_threads = True
    worker = threading.Thread(target=provider.serve_forever, name="context-chat-local-provider")
    worker.start()
    registry = ToolRegistry()
    application = PluginApplicationHost(RuntimeRoots(distribution, user), GENERATION_ID, registry)
    provider_config = {"profiles": [{"profileId": "fixture", "base_url": f"http://127.0.0.1:{provider.server_port}/v1", "api_key": "LOCAL_TEST_KEY", "timeout_seconds": 5, "models": ["fixture-model", "fixture-vision"]}]}
    from app.storage.paths import StoragePaths
    provider_path = StoragePaths(user).plugin_data_for("sakura.model.openai_compatible") / "config.json"
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    provider_path.write_text(json.dumps(provider_config), encoding="utf-8")
    application._host_services._model_slots._active_resolver = lambda: {"chat": {"serviceKey": "sakura.model.openai_compatible", "profileId": "fixture", "modelId": "fixture-model"}, "vision_chat": None}
    (user / "card.md").write_text("You are Sakura, a friendly language partner.", encoding="utf-8")
    session = AssistantSession(CharacterProfile("fixture", "Fixture", user, user / "card.md", ""), None, RuntimeLoopSettings(), "test")
    session.model_slots = application._host_services._model_slots._active_resolver()
    timeline = TimelineStore(user / "data" / "chat_history" / "timeline.sqlite3")
    timeline.initialize()
    events: list[dict[str, Any]] = []
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        user,
        session_provider=lambda: session,
        plugin_application_provider=lambda: application,
        timeline_store=timeline,
        event_publisher=events.append,
    )
    try:
        application.start()
        assert application.wait_until_loaded(timeout=5)
        assert any(item["pluginId"] == "sakura.assistant.default" and item["state"] == "active" for item in application.public_snapshot()["plugins"]), json.dumps(application.public_snapshot(), ensure_ascii=False)
        session.assistant = BoundAssistant(application, application.service_identity("sakura.assistant"))
        readiness = session.assistant.prepare(session.descriptor())
        assert readiness["state"] == "ready", readiness
        session.model_bindings = readiness["modelBindings"]
        application.bind_session(session)
        assert application.wait_until_bound(timeout=5)
        snapshot = application.public_snapshot()
        assert {item["pluginId"]: item["state"] for item in snapshot["plugins"]} == {PLUGIN_ID: "active", "sakura.assistant.default": "active", "sakura.model.openai_compatible": "active"}, snapshot
        inspection = application.call_service(SERVICE_KEY, "inspect")
        assert inspection["pid"] != os.getpid()
        assert inspection["capabilities"] == {
            "schemaVersion": 2,
            "scopes": ["step", "turn"],
            "failurePolicies": ["skip", "abort"],
        }
        yield _ChatFixture(application, boundary, timeline, requests, events, session)
    finally:
        boundary.close()
        application.close()
        provider.shutdown()
        provider.server_close()
        worker.join(5)
        assert not worker.is_alive()


def test_real_plugin_content_reaches_provider_and_disabling_removes_it(
    chat: _ChatFixture,
) -> None:
    terminal = chat.send("practice", "Let us practice Japanese.")
    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 1
    messages = chat.requests[0]["messages"]
    rule_message = next(message for message in messages if RULE in str(message["content"]))
    assert rule_message["role"] in {"system", "developer"}
    fragments = {
        fragment_id.rsplit(".", 1)[-1]: content
        for fragment_id, content in re.findall(
            r'<context\b[^>]*\bid="([^"]+)"[^>]*>\s*(.*?)\s*</context>',
            rule_message["content"],
            re.DOTALL,
        )
    }
    assert fragments["practice-rule"] == RULE
    assert fragments["practice-reference"] == REFERENCE
    assert 'kind=' not in rule_message["content"]
    assert 'trust=' not in rule_message["content"]
    assert any("You are Sakura" in str(message["content"]) for message in messages)
    assert chat.application.call_service(SERVICE_KEY, "inspect")["calls"] == 1
    entries = chat.timeline.read_all("fixture")
    assert [entry.kind for entry in entries] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
    assert entries[0].turn_id == entries[1].turn_id
    assert entries[1].payload["segments"] == terminal["payload"]["reply"]["segments"]

    record = next(item for item in chat.application.public_snapshot()["plugins"] if item["pluginId"] == PLUGIN_ID)
    disabled = chat.application.set_enabled(record["installId"], False)
    assert disabled["applicationState"] == "applied"
    terminal = chat.send("ordinary", "Tell me about cherry blossoms.")
    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 2
    next_request = json.dumps(chat.requests[-1], ensure_ascii=False)
    assert RULE not in next_request
    assert REFERENCE not in next_request
    assert len(chat.timeline.read_all("fixture")) == 4


def test_real_plugin_content_is_limited_by_token_budget_without_extra_count_or_char_caps(
    chat: _ChatFixture,
) -> None:
    from dataclasses import asdict
    catalog = chat.application.call_service("sakura.host.context", "catalog")
    transported = chat.application.call_service("sakura.host.context", "collect", catalog[0]["registrationId"], asdict(ContextRequest(current_input="transport large context")))
    assert len(transported) == 40
    assert transported[0]["content"] == LARGE_OPTIONAL
    assert transported[20]["content"] == LARGE_REQUIRED

    terminal = chat.send("large-context", "transport large context")

    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 1
    context = next(
        message["content"] for message in chat.requests[0]["messages"]
        if "Required beginning" in str(message["content"])
    )
    fragments = {
        fragment_id.rsplit(".", 1)[-1]: content
        for fragment_id, content in re.findall(
            r'<context\b[^>]*\bid="([^"]+)"[^>]*>\n(.*?)\n</context>',
            context, re.DOTALL,
        )
    }
    assert fragments["optional-0"] == LARGE_OPTIONAL
    assert {key for key in fragments if key.startswith("optional-")} == {
        f"optional-{index}" for index in range(20)
    }
    assert fragments["required-0"] == LARGE_REQUIRED
    assert {key for key in fragments if key.startswith("required-")} == {
        f"required-{index}" for index in range(20)
    }


def test_required_plugin_callback_failure_stops_before_provider_and_assistant_history(
    chat: _ChatFixture,
) -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        terminal = chat.send("required-failed", "fail required contribution")
    finally:
        bridge.close()
    assert terminal["name"] == "chat.failed", terminal
    assert terminal["payload"]["error"]["code"] == "CONTEXT_CONTRIBUTION_FAILED"
    assert "private callback detail" not in json.dumps(terminal)
    assert chat.requests == []
    assert [entry.kind for entry in chat.timeline.read_all("fixture")] == [TimelineKind.HUMAN]
    assert [event["name"] for event in chat.events] == ["chat.started", "chat.failed"]
    records = [
        json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
        for line in stream.getvalue().splitlines()
        if line.startswith(CORE_BRIDGE_PREFIX)
    ]
    failure = next(record for record in records if record["event"] == "chat.request.failed")
    assert failure["attributes"]["reason_code"] == "CONTEXT_CONTRIBUTION_FAILED"
    assert failure["attributes"]["provider_id"] == "fixture.context.rules.contribution"
    assert failure["attributes"]["plugin_id"] == PLUGIN_ID
    assert "private callback detail" in failure["attributes"]["diagnostic"]


@pytest.mark.parametrize("chat", [True], indirect=True)
def test_plugin_context_and_proactive_history_survive_system_role_fallback(chat: _ChatFixture) -> None:
    previous = "Previous proactive greeting"
    chat.timeline.append_many([NewTimelineEntry(
        entry_id="proactive-entry", turn_id="proactive-turn", character_id="fixture",
        kind=TimelineKind.ASSISTANT, origin="proactive",
        created_at=datetime.now().astimezone().isoformat(),
        payload={"segments": [{"text": previous, "translation": "", "tone": "", "portrait": "", "suppressTts": True}]},
    )])

    terminal = chat.send("compatibility", "Let us practice Japanese.")

    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 2
    for text in (RULE, REFERENCE, previous):
        before = next(item for item in chat.requests[0]["messages"] if text in str(item["content"]))
        after = next(item for item in chat.requests[1]["messages"] if text in str(item["content"]))
        assert before["role"] == "system"
        assert after["role"] == "user"
        assert before["content"] in after["content"]
    assert sum(item["role"] == "system" for item in chat.requests[1]["messages"]) == 1
    assert chat.application.call_service(SERVICE_KEY, "inspect")["calls"] == 1
    assert [entry.kind for entry in chat.timeline.read_all("fixture")] == [
        TimelineKind.ASSISTANT, TimelineKind.HUMAN, TimelineKind.ASSISTANT,
    ]


def test_default_assistant_consumes_large_tool_image_without_rpc_expansion(chat):
    terminal = chat.send("large-tool-image", "invoke image fixture")
    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 2
    content = json.dumps(chat.requests[-1]["messages"])
    assert "data:image/png;base64," in content
    assert len(content) > 1_700_000
    assert chat.application._host_services.artifact_count == 0


def test_expired_tool_registration_returns_a_tool_failure_and_the_turn_can_continue(chat):
    terminal = chat.send("expired-tool", "invoke expired fixture")
    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 2
    tool_response = next(message for message in chat.requests[-1]["messages"] if message["role"] == "tool")
    assert "TOOL_REGISTRATION_EXPIRED" in tool_response["content"]
    assert chat.application._host_services.artifact_count == 0


def test_default_assistant_routes_large_attached_image_to_the_vision_slot(chat):
    import base64

    slots = chat.session.model_slots
    slots["vision_chat"] = {**slots["chat"], "modelId": "fixture-vision"}
    chat.session.model_bindings = chat.session.assistant.prepare(chat.session.descriptor())["modelBindings"]
    assert chat.send("ordinary-text", "hello")["name"] == "chat.completed"
    image = "data:image/png;base64," + base64.b64encode(b"image" * 260_000).decode("ascii")

    response = chat.boundary.run_host_message("Describe this image.", image)

    assert response["reply"] == "一起练习吧。"
    assert [item["model"] for item in chat.requests] == ["fixture-model", "fixture-vision"]
    images = [part["image_url"]["url"] for item in chat.requests[-1]["messages"]
              if isinstance(item["content"], list) for part in item["content"]
              if part.get("type") == "image_url"]
    assert images == [image]
    assert chat.application._host_services.artifact_count == 0
    assert "base64" not in json.dumps([entry.payload for entry in chat.timeline.read_all("fixture")])


def test_context_contributors_receive_recent_history_from_the_fixed_turn_snapshot(chat):
    assert chat.send("first", "Earlier language practice")["name"] == "chat.completed"
    assert chat.send("second", "Continue the practice")["name"] == "chat.completed"
    inspection = chat.application.call_service(SERVICE_KEY, "inspect")
    recent = inspection["recentMessages"]
    assert any(item["role"] == "user" and "Earlier language practice" in item["content"] for item in recent)
    assert any(item["role"] == "assistant" and "一緒に練習しましょう" in item["content"] for item in recent)
    assert recent[-1]["role"] == "user"
    assert recent[-1]["content"] == "Continue the practice"


def test_default_assistant_keeps_committed_model_settings_until_session_replacement(chat):
    assert chat.send("before-settings-save", "hello")["name"] == "chat.completed"
    previous = chat.session.model_slots["chat"]
    # Saving a new configuration is not the same as applying it to the active
    # session. A failed or busy application keeps both this turn and the next
    # turn on the published settings snapshot.
    chat.application._host_services._model_slots._active_resolver = lambda: {
        "chat": {**previous, "modelId": "unapplied-model"}, "vision_chat": None,
    }
    assert chat.send("after-failed-settings-apply", "continue")["name"] == "chat.completed"
    assert [item["model"] for item in chat.requests] == ["fixture-model", "fixture-model"]
    log = (chat.session.character.package_dir / "data/logs/sakura-agent-trace.log").read_text(encoding="utf-8")
    assert re.findall(r"追踪编号\s*:\s*(\d+)", log) == ["1", "1", "2", "2"]
