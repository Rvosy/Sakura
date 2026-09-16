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

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.config.character_loader import CharacterProfile
from app.core.chat_pipeline import ChatPipeline
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.llm.prompts.types import ContextRequest
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
        "requires: [sakura.host.context]\n",
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
        host.register(
            {{
                "providerId": "fixture.context.rules.contribution",
                "scope": "turn",
                "failurePolicy": "abort",
            }},
            self.contribute,
        )
        context.provide("{SERVICE_KEY}", self, exports=("inspect",))

    def inspect(self):
        return {{"pid": os.getpid(), "capabilities": self.capabilities, "calls": self.calls}}

    def contribute(self, request):
        self.calls += 1
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
    runtime: AgentRuntime

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
def chat(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[_ChatFixture]:
    # Each test owns both the plugin distribution and all writable runtime data.
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    _write_plugin(distribution)
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
            body = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": content}}]},
                ensure_ascii=False,
            ).encode("utf-8")
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
    client = OpenAICompatibleClient(
        ApiSettings(
            f"http://127.0.0.1:{provider.server_port}/v1",
            "LOCAL_TEST_KEY",
            "fixture-model",
            timeout_seconds=5,
        ),
        retry_requests=False,
    )
    runtime = AgentRuntime(
        client,
        "You are Sakura, a friendly language partner.",
        tools=registry,
        character_id="fixture",
        character_name="Fixture",
        strict_provider_errors=True,
    )
    runtime.autonomous_screen_observation_enabled = False
    session = SimpleNamespace(
        character=CharacterProfile("fixture", "Fixture", user, user / "card.md", ""),
        runtime=runtime,
        pipeline=ChatPipeline(runtime, finalize_trace_operations=False),
    )
    application = PluginApplicationHost(RuntimeRoots(distribution, user), GENERATION_ID, registry)
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
        assert application.application.wait_until_loaded(timeout=5)
        application.bind_session(session)
        assert application.application.wait_until_bound(timeout=5)
        snapshot = application.public_snapshot()
        assert [(item["pluginId"], item["state"]) for item in snapshot["plugins"]] == [
            (PLUGIN_ID, "active")
        ], snapshot
        inspection = application.call_service(SERVICE_KEY, "inspect")
        assert inspection["pid"] != os.getpid()
        assert inspection["capabilities"] == {
            "schemaVersion": 2,
            "scopes": ["step", "turn"],
            "failurePolicies": ["skip", "abort"],
        }
        yield _ChatFixture(application, boundary, timeline, requests, events, runtime)
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

    record = chat.application.public_snapshot()["plugins"][0]
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
    provider = chat.runtime.context_providers[0]
    transported = provider.build_context(ContextRequest(current_input="transport large context"))
    assert len(transported) == 40
    assert transported[0].content == LARGE_OPTIONAL
    assert transported[20].content == LARGE_REQUIRED

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
