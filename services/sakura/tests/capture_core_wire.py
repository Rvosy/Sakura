"""Run with Sakura bundled Python: create real Core bridge acceptance records."""

import io, json, sys, tempfile, urllib.error
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo))
from app.core_host.runtime_logging import (
    install_runtime_logging,
    TELEMETRY_BRIDGE_PREFIX,
)
from app.core_host.server import WriterError, ResponseWriter
from app.agent.trace import AgentTraceRecorder
from app.llm.api_client import OpenAICompatibleClient, ApiSettings, ApiRequestError

stream = io.BytesIO()
bridge = install_runtime_logging(stream)
try:

    class BrokenPipe(io.BytesIO):
        def write(self, data):
            raise BrokenPipeError("PRIVATE_EXCEPTION_MESSAGE")

    writer = ResponseWriter(BrokenPipe())
    try:
        writer.send({"type": "response", "id": "acceptance", "ok": True})
    except WriterError as error:
        bridge.emit_unhandled("CORE_HOST_TRANSPORT_ERROR", error)
    finally:
        try:
            writer.close()
        except WriterError:
            pass
    with tempfile.TemporaryDirectory() as trace:
        recorder = AgentTraceRecorder(Path(trace))
        client = OpenAICompatibleClient(
            ApiSettings(
                "https://example.invalid/v1", "sk-PRIVATE_KEY_VALUE", "custom-model"
            ),
            agent_trace_recorder=recorder,
        )

        def fail(payload, **kwargs):
            try:
                raise urllib.error.HTTPError(
                    "https://PRIVATE_URL", 401, "PRIVATE_BODY", {}, None
                )
            except urllib.error.HTTPError as cause:
                raise ApiRequestError("PRIVATE_EXCEPTION_MESSAGE") from cause

        client._post_chat_completions = fail
        try:
            with recorder.operation("acceptance", finalize_external=True):
                client.complete_raw(
                    "PRIVATE_PROMPT", [{"role": "user", "content": "PRIVATE_CHAT_BODY"}]
                )
        except ApiRequestError:
            pass
finally:
    bridge.close()
rows = [
    json.loads(line.removeprefix(TELEMETRY_BRIDGE_PREFIX))
    for line in stream.getvalue().splitlines()
    if line.startswith(TELEMETRY_BRIDGE_PREFIX)
]
assert len(rows) == 2
encoded = "\n".join(json.dumps(row) for row in rows)
assert "PRIVATE" not in encoded
Path(sys.argv[1]).write_text(encoded + "\n")
