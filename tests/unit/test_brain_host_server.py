from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from app.brain_host.protocol import FrameDecoder, encode_frame


ROOT = Path(__file__).resolve().parents[2]


def _request(sequence: int, method: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": 1,
        "kind": "request",
        "id": f"req-{sequence}",
        "session_id": "session-test",
        "sequence": sequence,
        "method": method,
        "deadline_ms": 30_000,
        "payload": payload,
    }


class _Registry:
    def __init__(self) -> None:
        self.stop_calls: list[int] = []

    def stop_all(self, timeout_ms: int = 1_000) -> None:
        self.stop_calls.append(timeout_ms)


def _fake_context(base_dir: Path) -> SimpleNamespace:
    registry = _Registry()
    return SimpleNamespace(
        base_dir=base_dir,
        character_profile=SimpleNamespace(
            id="demo",
            display_name="Demo",
            initial_message="hello",
            default_portrait_path=base_dir / "characters" / "demo" / "portrait.png",
            reply_tones=("neutral",),
            portrait_choices=("neutral",),
            expression_portraits={
                "neutral": base_dir / "characters" / "demo" / "portrait.png"
            },
        ),
        settings=SimpleNamespace(
            base_url="https://api.example.com/v1",
            model="test-model",
            timeout_seconds=30,
        ),
        startup_initializing=True,
        tool_registry=SimpleNamespace(all=lambda: [object(), object()]),
        mcp_tool_provider=None,
        plugin_manager=SimpleNamespace(results=[]),
        tts_provider=SimpleNamespace(service_ready=False, close=lambda: None),
        resource_registry=registry,
    )


def test_brain_host_config_reads_controlled_environment(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostConfig

    config = BrainHostConfig.from_environment(
        {
            "SAKURA_BASE_DIR": str(tmp_path),
            "SAKURA_SESSION_ID": "session-test",
            "SAKURA_SESSION_CREDENTIAL": "credential-test",
            "SAKURA_PROTOCOL_VERSION": "1",
        }
    )

    assert config.base_dir == tmp_path.resolve()
    assert config.session_id == "session-test"
    assert config.session_credential == "credential-test"
    assert config.protocol_version == 1


def test_application_initializes_context_and_returns_json_startup_dto(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostApplication, BrainHostConfig

    context = _fake_context(tmp_path)
    app = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-test", "credential-test", 1),
        context_builder=lambda base_dir: context,
    )

    startup = app.initialize()

    assert startup is not None
    assert startup["state"] == "ready"
    assert startup["base_dir"] == str(tmp_path.resolve())
    assert startup["character"]["id"] == "demo"
    assert startup["character"]["initial_message"] == "hello"
    assert startup["character"]["portraits"] == {
        "default": "characters/demo/portrait.png",
        "expressions": {"neutral": "characters/demo/portrait.png"},
    }
    assert startup["theme"]["primary_color"].startswith("#")
    assert startup["layout"]["portrait_scale_percent"] == 100
    assert startup["subtitle"]["language"] == "zh"
    assert startup["runtime"]["tool_count"] == 2
    json.dumps(startup, ensure_ascii=False)


def test_system_hello_health_and_shutdown(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostApplication, BrainHostConfig

    context = _fake_context(tmp_path)
    app = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-test", "credential-test", 1),
        context_builder=lambda _base_dir: context,
    )
    app.initialize()

    hello = app.handle_request(
        "system.hello",
        {"protocol": 1, "session_credential": "credential-test"},
    )
    health = app.handle_request("system.health", {})
    shutdown = app.handle_request("system.shutdown", {})

    assert hello["protocol"] == 1
    assert hello["session_id"] == "session-test"
    assert hello["backend_state"] == "ready"
    assert health["ready"] is True
    assert health["character_id"] == "demo"
    assert shutdown == {"state": "stopped"}
    assert context.resource_registry.stop_calls == [1_000]


def test_wrong_session_credential_returns_stable_error(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostApplication, BrainHostConfig
    from app.brain_host.errors import BrainHostError

    app = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-test", "credential-test", 1),
        context_builder=lambda base_dir: _fake_context(base_dir),
    )
    app.initialize()

    try:
        app.handle_request(
            "system.hello",
            {"protocol": 1, "session_credential": "wrong"},
        )
    except BrainHostError as exc:
        assert exc.code == "AUTHENTICATION_FAILED"
        assert exc.retryable is False
        assert exc.to_dict()["details"] == {}
    else:  # pragma: no cover
        raise AssertionError("wrong credential should fail")


def test_initialization_failure_is_exposed_as_stable_health_state(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostApplication, BrainHostConfig

    def fail(_base_dir: Path) -> object:
        raise RuntimeError("broken config")

    app = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-test", "credential-test", 1),
        context_builder=fail,
    )

    assert app.initialize() is None
    health = app.handle_request("system.health", {})

    assert health["state"] == "failed"
    assert health["ready"] is False
    assert health["error"]["code"] == "BACKEND_INITIALIZATION_FAILED"
    assert "broken config" not in health["error"]["message"]


def test_server_writes_only_framed_protocol_responses(tmp_path: Path) -> None:
    from app.brain_host.application import BrainHostApplication, BrainHostConfig
    from app.brain_host.server import BrainHostServer
    from app.brain_host.transport import FramedTransport

    app = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-test", "credential-test", 1),
        context_builder=lambda base_dir: _fake_context(base_dir),
    )
    app.initialize()
    wire = b"".join(
        [
            encode_frame(
                _request(
                    1,
                    "system.hello",
                    {"protocol": 1, "session_credential": "credential-test"},
                )
            ),
            encode_frame(_request(2, "system.health", {})),
            encode_frame(_request(3, "system.shutdown", {})),
        ]
    )
    output = io.BytesIO()
    server = BrainHostServer(app, FramedTransport(io.BytesIO(wire), output))

    server.serve_forever()

    decoder = FrameDecoder()
    responses = decoder.feed(output.getvalue())
    decoder.finish()
    assert [response["id"] for response in responses] == ["req-1", "req-2", "req-3"]
    assert all(response["kind"] == "response" for response in responses)
    assert responses[-1]["payload"] == {"state": "stopped"}


def test_brain_host_import_path_does_not_load_qt_or_app_ui() -> None:
    code = """
import json
import sys
import app.brain_host.__main__
blocked = sorted(
    name for name in sys.modules
    if name == 'PySide6' or name.startswith('PySide6.')
    or name == 'app.ui' or name.startswith('app.ui.')
)
print(json.dumps(blocked))
"""

    result = subprocess.run(
        [str(ROOT / "runtime" / "python.exe"), "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout) == []


def test_real_headless_context_preserves_character_and_config_loading(tmp_path: Path) -> None:
    _write_startup_root(tmp_path)
    code = """
import json
import os
import sys
from pathlib import Path
os.environ['SAKURA_HEADLESS'] = '1'
from app.core.bootstrap import build_initial_app_context
context = build_initial_app_context(Path(os.environ['TEST_BASE_DIR']))
blocked = sorted(
    name for name in sys.modules
    if name == 'PySide6' or name.startswith('PySide6.')
    or name == 'app.ui' or name.startswith('app.ui.')
)
print(json.dumps({
    'character_id': context.character_profile.id,
    'model': context.settings.model,
    'blocked': blocked,
}))
"""
    environment = os.environ.copy()
    environment["TEST_BASE_DIR"] = str(tmp_path)
    environment["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [str(ROOT / "runtime" / "python.exe"), "-c", code],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)

    assert payload == {"character_id": "demo", "model": "test-model", "blocked": []}


def test_python_module_entrypoint_serves_framed_system_requests(tmp_path: Path) -> None:
    _write_startup_root(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "SAKURA_BASE_DIR": str(tmp_path),
            "SAKURA_SESSION_ID": "session-test",
            "SAKURA_SESSION_CREDENTIAL": "credential-test",
            "SAKURA_PROTOCOL_VERSION": "1",
        }
    )
    wire = b"".join(
        [
            encode_frame(
                _request(
                    1,
                    "system.hello",
                    {"protocol": 1, "session_credential": "credential-test"},
                )
            ),
            encode_frame(_request(2, "system.health", {})),
            encode_frame(_request(3, "system.shutdown", {})),
        ]
    )

    process = subprocess.Popen(
        [str(ROOT / "runtime" / "python.exe"), "-m", "app.brain_host"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(wire, timeout=30)

    decoder = FrameDecoder()
    responses = decoder.feed(stdout)
    decoder.finish()
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert len(responses) == 3
    assert responses[0]["payload"]["backend_state"] == "ready"
    assert responses[1]["payload"]["character_id"] == "demo"
    assert responses[2]["payload"] == {"state": "stopped"}


def _write_startup_root(root: Path) -> None:
    config_dir = root / "data" / "config"
    character_dir = root / "characters" / "demo"
    config_dir.mkdir(parents=True)
    character_dir.mkdir(parents=True)
    (config_dir / "api.yaml").write_text(
        """
llm:
  base_url: https://api.example.com/v1
  api_key: test-key
  model: test-model
tts:
  provider: none
  enabled: false
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "characters.yaml").write_text(
        "current_character_id: demo\n",
        encoding="utf-8",
    )
    (config_dir / "system_config.yaml").write_text(
        "ui:\n  portrait_scale_percent: 125\n",
        encoding="utf-8",
    )
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"not a real image")
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            }
        ),
        encoding="utf-8",
    )
