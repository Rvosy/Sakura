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
    assert len(line) > runtime_logging.CORE_BRIDGE_MAX_LINE_BYTES
    assert len(line) <= runtime_logging.TELEMETRY_BRIDGE_MAX_LINE_BYTES
