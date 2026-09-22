from __future__ import annotations
from unittest.mock import MagicMock

import io
import json
from types import SimpleNamespace
from functools import partial

import pytest

from sakura_assistant.agent.trace import AgentTraceRecorder
from app.core_host import runtime_logging
from app.core_host.runtime_logging import TELEMETRY_BRIDGE_PREFIX, install_runtime_logging
from sakura_assistant.llm import api_client
from sakura_assistant import diagnostics
from app.core_host.plugin_host_services import _LoggingHostService
from app.plugins.host_services import HOST_CALLER
from sakura_assistant.llm.api_client import ApiRequestError, DialogueSettings, AssistantModelClient


SENTINELS = {
    "chat": "SENTINEL_CHAT_BODY_7a83",
    "prompt": "SENTINEL_PROMPT_6d22",
    "memory": "SENTINEL_MEMORY_91cb",
    "tool_args": "SENTINEL_TOOL_ARGS_1e02",
    "tool_result": "SENTINEL_TOOL_RESULT_b4f1",
    "api_key": "sk-SENTINEL_API_KEY_d830",
    "absolute_path": "/Users/private/SENTINEL_PATH_249a.txt",
    "exception": "SENTINEL_EXCEPTION_MESSAGE_c991",
    "model": "private-custom-model-SENTINEL_MODEL_ae77",
    "agent_trace": "SENTINEL_AGENT_TRACE_f815",
}


@pytest.fixture(autouse=True)
def assistant_metric_host_bridge(monkeypatch):
    host = _LoggingHostService()

    def emit(severity, message, *, fields):
        token = HOST_CALLER.set("sakura.assistant.default")
        try:
            return host.call("emit", [[{"severity": severity, "message": message, "fields": fields}], 0])
        finally:
            HOST_CALLER.reset(token)

    def model_call(candidate):
        return emit("debug", "模型请求已结束", fields={"event": "model.call.metric", "modelCall": candidate})

    monkeypatch.setattr(diagnostics, "_logger", SimpleNamespace(model_call=model_call,
        **{severity: partial(emit, severity) for severity in ("debug", "info", "warning", "error")}))


def _telemetry_payloads(stream: io.BytesIO) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix(TELEMETRY_BRIDGE_PREFIX))
        for line in stream.getvalue().splitlines()
        if line.startswith(TELEMETRY_BRIDGE_PREFIX)
    ]


def test_model_metric_bridge_is_body_free_and_projects_custom_model(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    recorder = AgentTraceRecorder(tmp_path)
    client = AssistantModelClient(
        DialogueSettings(model=SENTINELS["model"]),
        agent_trace_recorder=recorder,
     model_client=MagicMock())
    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, cancel_checker=None: {
            "choices": [{"message": {"content": SENTINELS["tool_result"]}}],
            "usage": {"prompt_tokens": 19, "completion_tokens": 3, "total_tokens": 22},
        },
    )
    try:
        with recorder.operation("telemetry-privacy", finalize_external=True):
            client.complete_raw(
                SENTINELS["prompt"],
                [{"role": "user", "content": " ".join(SENTINELS.values())}],
            )
    finally:
        bridge.close()

    payloads = _telemetry_payloads(stream)
    assert len(payloads) == 1
    metric = payloads[0]["modelCall"]
    assert isinstance(metric, dict)
    assert metric["modelFamily"] == "custom"
    assert metric["usage"]["promptTokens"] == 19
    encoded = json.dumps(payloads, sort_keys=True)
    for sentinel in SENTINELS.values():
        assert sentinel not in encoded


def test_unhandled_error_bridge_preserves_original_error_and_stack() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    client = AssistantModelClient(DialogueSettings(model=""), model_client=MagicMock())
    try:
        try:
            raise api_client.ApiConfigError(SENTINELS["exception"])
        except Exception as error:  # noqa: BLE001 - synthetic process-boundary error
            bridge.emit_unhandled("CORE_UNHANDLED_ERROR", error)
    finally:
        bridge.close()

    error_payload = next(item["error"] for item in _telemetry_payloads(stream) if "error" in item)
    assert isinstance(error_payload, dict)
    assert error_payload["exceptionType"] == "ApiConfigError"
    assert all(not str(frame.get("file", "")).startswith("/") for frame in error_payload["stack"])
    encoded = json.dumps(error_payload, sort_keys=True)
    assert SENTINELS["exception"] in error_payload["evidence"]["diagnostic"]
    assert SENTINELS["absolute_path"] not in encoded


def test_failed_model_metric_keeps_estimate_and_unknown_usage(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    recorder = AgentTraceRecorder(tmp_path)
    client = AssistantModelClient(
        DialogueSettings(model=SENTINELS["model"]),
        agent_trace_recorder=recorder,
     model_client=MagicMock())
    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, cancel_checker=None: (_ for _ in ()).throw(
            ApiRequestError(SENTINELS["exception"])
        ),
    )
    try:
        with recorder.operation("telemetry-failure", finalize_external=True):
            try:
                client.complete_raw("system", [{"role": "user", "content": SENTINELS["chat"]}])
            except ApiRequestError:
                pass
    finally:
        bridge.close()

    metric = _telemetry_payloads(stream)[0]["modelCall"]
    assert isinstance(metric, dict)
    assert metric["outcome"] == "failed"
    assert metric["errorCode"] == "MODEL_REQUEST_FAILED"
    assert metric["usage"] is None
    assert isinstance(metric["estimate"], dict)
    assert SENTINELS["exception"] not in json.dumps(metric, sort_keys=True)


def test_provider_compatibility_is_reported_in_one_semantic_model_call(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    recorder = AgentTraceRecorder(tmp_path)
    client = AssistantModelClient(
        DialogueSettings(model="gpt-5-mini"),
        agent_trace_recorder=recorder,
     model_client=MagicMock())
    client._model_client.complete.return_value = {"message": {"content": "ok"},
        "diagnostics": {"attemptCount": 2, "compatibilityFallbacks": ["response_format"]}}
    try:
        with recorder.operation("telemetry-fallback", finalize_external=True):
            client.complete_raw(
                "return json",
                [{"role": "user", "content": "hello"}],
                response_format={"type": "json_object"},
            )
    finally:
        bridge.close()

    metrics = [payload["modelCall"] for payload in _telemetry_payloads(stream)]
    assert [metric["modelCall"] for metric in metrics] == [1]
    assert [metric["outcome"] for metric in metrics] == ["success"]
    assert metrics[0]["request"]["attemptCount"] == 2


def test_telemetry_bridge_failure_does_not_change_model_result(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    recorder = AgentTraceRecorder(tmp_path)
    client = AssistantModelClient(
        DialogueSettings(model="gpt-5-mini"),
        agent_trace_recorder=recorder,
     model_client=MagicMock())
    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, cancel_checker=None: {
            "choices": [{"message": {"content": "ok"}}],
        },
    )
    monkeypatch.setattr(
        api_client,
        "submit_telemetry_model_call",
        lambda _candidate: (_ for _ in ()).throw(RuntimeError("bridge unavailable")),
    )

    with recorder.operation("telemetry-isolation", finalize_external=True):
        response = client.complete_raw("system", [{"role": "user", "content": "hello"}])

    assert response == "ok"


def test_safe_stack_keeps_sixteen_frames_within_local_bridge_limit() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)

    def fail(depth: int) -> None:
        if depth:
            fail(depth - 1)
        raise RuntimeError(SENTINELS["exception"])

    try:
        try:
            fail(20)
        except RuntimeError as error:
            bridge.emit_unhandled("CORE_UNHANDLED_ERROR", error)
    finally:
        bridge.close()

    error_payload = next(item["error"] for item in _telemetry_payloads(stream) if "error" in item)
    assert isinstance(error_payload, dict)
    assert len(error_payload["stack"]) == 16

    long_candidate = {
        "schema": 1,
        "component": "core",
        "event": "core.error.unhandled",
        "code": "CORE_UNHANDLED_ERROR",
        "operationId": None,
        "exceptionType": "RuntimeError",
        "stack": [
            {
                "function": "f" * 128,
                "file": f"app/{'m' * 220}{index}.py",
                "line": index + 1,
            }
            for index in range(16)
        ],
    }
    line = runtime_logging._encode_telemetry_record("error", long_candidate)  # noqa: SLF001
    assert line is not None
    assert len(line) > 4096  # Telemetry has its own budget, independent of local diagnostic records.
    assert len(line) <= runtime_logging.TELEMETRY_BRIDGE_MAX_LINE_BYTES


def test_plugin_source_and_deadline_survive_host_and_core_bridge():
    from app.core_host import plugin_host_services
    from app.plugins.host_services import HOST_CALLER

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    token = HOST_CALLER.set("third.party.voice")
    try:
        plugin_host_services._DiagnosticsHostService().call("emit", ["spoofed", {
            "event": "voice.render.failed",
            "severity": "warning",
            "attributes": {
                "source_file": "voice/engine.py", "source_line": 690,
                "timeout_ms": 60000, "child_exited": False,
                "diagnostic": "weights missing token=fixture-secret",
            },
        }])
    finally:
        HOST_CALLER.reset(token)
        bridge.close()
    records = [json.loads(line.removeprefix(runtime_logging.CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines()
               if line.startswith(runtime_logging.CORE_BRIDGE_PREFIX)]
    assert len(records) == 1
    record = records[0]
    assert record["plugin_id"] == "third.party.voice"
    assert record["custom"] is True
    assert record["attributes"]["event"] == "voice.render.failed"
    assert record["attributes"]["source_file"] == "voice/engine.py"
    assert record["attributes"]["timeout_ms"] == 60000
    assert record["attributes"]["child_exited"] is False
    assert "weights missing" in record["attributes"]["diagnostic"]
    assert "fixture-secret" not in json.dumps(record)


def test_model_failures_classify_provider_diagnostics_and_wrapped_causes():
    from sakura_model import ModelError
    for status, domain in [(401, "authentication"), (429, "rate_limit"), (503, "provider")]:
        error = ApiRequestError("PRIVATE_EXCEPTION_MESSAGE")
        error.__cause__ = ModelError("MODEL_HTTP_FAILED", "PRIVATE_BODY", diagnostics={"httpStatus": status, "faultDomain": domain})
        result = api_client._request_failure(error)
        assert result["faultDomain"] == domain and result["httpStatus"] == status
        assert "PRIVATE" not in json.dumps(result)
    timeout = ModelError("MODEL_REQUEST_TIMEOUT", "PRIVATE", diagnostics={"faultDomain": "transport"})
    assert api_client._request_failure(timeout)["reasonCode"] == "MODEL_REQUEST_TIMEOUT"


def test_mem0_logs_once_through_the_same_plugin_pipeline():
    from app.core_host import plugin_host_services
    from app.plugins.host_services import HOST_CALLER

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    token = HOST_CALLER.set("sakura.memory.mem0")
    try:
        plugin_host_services._LoggingHostService().call("emit", [[{
            "severity": "info", "message": "记忆检索完成",
            "fields": {"event": "memory.recall.finished", "elapsed_ms": 31,
                       "selected": 2, "content": "PRIVATE_CHAT_BODY"},
        }], 0])
    finally:
        HOST_CALLER.reset(token)
        bridge.close()
    records = [json.loads(line.removeprefix(runtime_logging.CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines()
               if line.startswith(runtime_logging.CORE_BRIDGE_PREFIX)]
    assert len(records) == 1
    assert records[0]["plugin_id"] == "sakura.memory.mem0"
    assert records[0]["custom"] is True
    assert records[0]["attributes"]["event"] == "memory.recall.finished"
    assert records[0]["attributes"]["selected"] == 2
    assert "PRIVATE_CHAT_BODY" not in json.dumps(records)
    assert _telemetry_payloads(stream) == []
