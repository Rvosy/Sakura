from __future__ import annotations

import io
import json

from app.agent.trace import AgentTraceRecorder
from app.core_host import runtime_logging
from app.core_host.runtime_logging import TELEMETRY_BRIDGE_PREFIX, install_runtime_logging
from app.llm import api_client
from app.llm.api_client import ApiRequestError, ApiSettings, OpenAICompatibleClient


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
    client = OpenAICompatibleClient(
        ApiSettings(
            "https://provider.invalid/v1",
            SENTINELS["api_key"],
            SENTINELS["model"],
        ),
        agent_trace_recorder=recorder,
    )
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


def test_unhandled_error_bridge_keeps_only_safe_stack_and_type() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    client = OpenAICompatibleClient(ApiSettings("", "", ""))
    try:
        try:
            client._ensure_chat_config(SENTINELS["exception"])  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - synthetic process-boundary error
            bridge.emit_unhandled("CORE_UNHANDLED_ERROR", error)
    finally:
        bridge.close()

    error_payload = next(item["error"] for item in _telemetry_payloads(stream) if "error" in item)
    assert isinstance(error_payload, dict)
    assert error_payload["exceptionType"] == "ApiConfigError"
    assert all(not str(frame.get("file", "")).startswith("/") for frame in error_payload["stack"])
    encoded = json.dumps(error_payload, sort_keys=True)
    assert SENTINELS["exception"] not in encoded
    assert SENTINELS["absolute_path"] not in encoded


def test_failed_model_metric_keeps_estimate_and_unknown_usage(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    recorder = AgentTraceRecorder(tmp_path)
    client = OpenAICompatibleClient(
        ApiSettings("https://provider.invalid/v1", SENTINELS["api_key"], SENTINELS["model"]),
        agent_trace_recorder=recorder,
    )
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


def test_compatibility_fallback_uses_existing_model_call_sequence(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    recorder = AgentTraceRecorder(tmp_path)
    client = OpenAICompatibleClient(
        ApiSettings("https://provider.invalid/v1", "key", "gpt-5-mini"),
        agent_trace_recorder=recorder,
    )
    attempts = 0

    def post(payload, cancel_checker=None):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ApiRequestError("response_format is not supported")
        assert "response_format" not in payload
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", post)
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
    assert [metric["modelCall"] for metric in metrics] == [1, 2]
    assert [metric["outcome"] for metric in metrics] == ["failed", "success"]


def test_telemetry_bridge_failure_does_not_change_model_result(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    recorder = AgentTraceRecorder(tmp_path)
    client = OpenAICompatibleClient(
        ApiSettings("https://provider.invalid/v1", "key", "gpt-5-mini"),
        agent_trace_recorder=recorder,
    )
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


def test_plugin_source_and_deadline_survive_host_and_core_bridge(monkeypatch):
    from app.core_host import plugin_host_services

    captured = []
    monkeypatch.setattr(
        plugin_host_services, "log_event", lambda *a, **k: captured.append(a[2])
    )
    service = plugin_host_services._DiagnosticsHostService()
    service.call(
        "emit",
        [
            "sakura.tts.gpt-sovits",
            {
                "event": "tts.synthesis.failed",
                "severity": "warning",
                "attributes": {
                    "reason_code": "TTS_HTTP_TIMEOUT",
                    "source_file": "plugins/builtin/sakura_gpt_sovits/_support.py",
                    "source_line": 690,
                    "timeout_ms": 60000,
                    "elapsed_ms": 60200,
                    "stage": "synthesis_http",
                    "child_exited": False,
                },
            },
        ],
    )
    safe = runtime_logging._safe_attributes(captured[0])
    assert safe["source_file"] == "plugins/builtin/sakura_gpt_sovits/_support.py"
    assert safe["timeout_ms"] == 60000 and safe["child_exited"] is False


def test_model_failures_classify_http_timeout_and_wrapped_causes():
    import urllib.error

    for status, domain in [
        (401, "authentication"),
        (429, "rate_limit"),
        (503, "provider"),
    ]:
        error = ApiRequestError("PRIVATE_EXCEPTION_MESSAGE")
        error.__cause__ = urllib.error.HTTPError(
            "https://PRIVATE_URL", status, "PRIVATE_BODY", {}, None
        )
        result = api_client._request_failure(error)
        assert result["faultDomain"] == domain and result["httpStatus"] == status
        assert "PRIVATE" not in json.dumps(result)
    timeout = TimeoutError("PRIVATE_EXCEPTION_MESSAGE")
    assert api_client._request_failure(timeout)["stage"] == "unknown"
    timeout.sakura_request_stage = "read"
    assert api_client._request_failure(timeout)["reasonCode"] == "MODEL_READ_TIMEOUT"


def test_real_mem0_logging_boundary_projects_only_controlled_recall_event(monkeypatch):
    from app.core_host import plugin_host_services
    from app.plugins.host_services import HOST_CALLER

    records = []
    monkeypatch.setattr(
        plugin_host_services, "log_event", lambda *a, **k: records.append((a, k))
    )
    monkeypatch.setattr(plugin_host_services, "log_message", lambda *a, **k: None)
    token = HOST_CALLER.set("sakura.memory.mem0")
    try:
        plugin_host_services._LoggingHostService().call(
            "emit",
            [
                [
                    {
                        "severity": "info",
                        "message": "PRIVATE_MEMORY_BODY",
                        "fields": {
                            "event": "memory.recall.finished",
                            "elapsed_ms": 31,
                            "selected": 2,
                            "content": "PRIVATE_CHAT_BODY",
                        },
                    }
                ],
                0,
            ],
        )
    finally:
        HOST_CALLER.reset(token)
    assert records[0][1]["event"] == "memory.recall.finished"
    assert records[0][0][2]["selected"] == 2
    assert "PRIVATE" not in json.dumps(records)
