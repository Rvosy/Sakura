from __future__ import annotations

import io
import json
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
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
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineKind, TimelineStore


GENERATION_ID = "context-plugin-chat"
GENERATION_CREDENTIAL = "54" * 16
PLUGIN_ID = "fixture.context.rules"
SERVICE_KEY = "fixture.context.inspection"
RULE = "Use Japanese for this interaction and correct grammar before answering."
REFERENCE = "The practice text is about a fictional trip to Kyoto."


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
        return [
            {{"id": "practice-rule", "kind": "instruction", "required": True,
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
def chat(tmp_path: Path) -> Iterator[_ChatFixture]:
    # Each test owns both the plugin distribution and all writable runtime data.
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    _write_plugin(distribution)
    requests: list[dict[str, Any]] = []

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler contract
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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
            "schemaVersion": 1,
            "fragmentKinds": ["data", "instruction"],
            "scopes": ["step", "turn"],
            "failurePolicies": ["skip", "abort"],
        }
        yield _ChatFixture(application, boundary, timeline, requests, events)
    finally:
        boundary.close()
        application.close()
        provider.shutdown()
        provider.server_close()
        worker.join(5)
        assert not worker.is_alive()


def test_real_plugin_rules_and_data_reach_provider_and_disabling_removes_them(
    chat: _ChatFixture,
) -> None:
    terminal = chat.send("practice", "Let us practice Japanese.")
    assert terminal["name"] == "chat.completed", terminal
    assert len(chat.requests) == 1
    messages = chat.requests[0]["messages"]
    rule_message = next(message for message in messages if RULE in str(message["content"]))
    assert rule_message["role"] in {"system", "developer"}
    fragments = {
        kind: content
        for kind, content in re.findall(
            r'<context\b[^>]*\bkind="(instruction|data)">\s*(.*?)\s*</context>',
            rule_message["content"],
            re.DOTALL,
        )
    }
    assert fragments["instruction"] == RULE
    assert fragments["data"] == REFERENCE
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
