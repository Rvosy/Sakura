from __future__ import annotations

import base64
import json
from pathlib import Path
import queue
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config.character_loader import CharacterRegistry
from app.config.model_references import ModelReferenceRepository
from app.config.settings_service import AppSettingsService
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.theme_generation import ThemeGenerationBoundary
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.dependencies import PluginDependencyRoots
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from sakura_assistant.theme import THEME_FIELDS


COLORS = {key: f"#{index + 1:02x}5577" for index, key in enumerate(THEME_FIELDS)}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=")


def request(name, operation_id, **payload):
    return {"id": uuid.uuid4().hex, "name": name, "generationId": "theme-test",
            "generationCredential": "credential", "payload": {"operationId": operation_id, **payload}}


def test_cancel_queued_generation_before_loading_character_or_model(tmp_path):
    boundary = ThemeGenerationBoundary("theme-test", "credential", tmp_path, application_provider=lambda: None)
    operation_id = uuid.uuid4().hex
    queued = request("theme.generate", operation_id, characterId="alpha")
    boundary.reserve(queued)
    assert boundary.handle(request("theme.cancel", operation_id))["payload"]["cancelled"]
    assert boundary.handle(queued)["error"]["code"] == "OPERATION_CANCELLED"


@pytest.fixture
def theme_runtime(tmp_path, monkeypatch, assistant_dependencies):
    root = Path(__file__).resolve().parents[2]
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    for name in ("sakura_assistant", "sakura_portrait", "sakura_model_openai_compatible"):
        shutil.copytree(root / "plugins/builtin" / name, distribution / "plugins/builtin" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(root / "VERSION", distribution / "VERSION")
    for identity in ("alpha", "beta"):
        package = user / "characters" / identity
        package.mkdir(parents=True)
        (package / "card.md").write_text(identity, encoding="utf-8")
        (package / "portrait.png").write_bytes(PNG)
        (package / "character.json").write_text(json.dumps({
            "id": identity, "display_name": identity, "card": "card.md",
            "portrait": {"default": "portrait.png", "expressions": {}},
        }), encoding="utf-8")
    original = PluginDependencyRoots.verified_root
    monkeypatch.setattr(PluginDependencyRoots, "verified_root", lambda self, plugin_id, *args, **kwargs:
                        assistant_dependencies if plugin_id in {"sakura.assistant.default", "sakura.model.openai_compatible"}
                        else original(self, plugin_id, *args, **kwargs))
    received, gate = queue.Queue(), threading.Event()
    gate.set()
    output = {"content": json.dumps(COLORS), "finish": "stop"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.put(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            gate.wait()
            payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": output["content"]},
                                                "finish_reason": output["finish"]}]}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = "sakura.model.openai_compatible"
    config = StoragePaths(user).plugin_data_for(service) / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"profiles": [{"profileId": "fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "api_key": "", "models": ["chat", "vision"]}]}), encoding="utf-8")
    references = {slot: {"serviceKey": service, "profileId": "fixture", "modelId": model}
                  for slot, model in (("chat", "chat"), ("vision_chat", "vision"))}
    ModelReferenceRepository(user).save(references)
    AppSettingsService(user).save_current_character_id(CharacterRegistry(user), "alpha")
    app = PluginApplicationHost(RuntimeRoots(distribution, user), "theme-test", ToolRegistry())
    boundary = ThemeGenerationBoundary("theme-test", "credential", user, application_provider=lambda: app)
    try:
        app.start()
        assert app.wait_until_loaded(timeout=10)
        yield boundary, app, user, received, gate, output
    finally:
        gate.set()
        boundary.close()
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_real_plugins_generate_each_character_without_saving_theme(theme_runtime):
    boundary, app, user, received, _, output = theme_runtime
    for character in ("alpha", "beta"):
        AppSettingsService(user).save_current_character_id(CharacterRegistry(user), character)
        before = (user / "config/characters.yaml").read_bytes()
        if character == "beta":
            references = ModelReferenceRepository(user).load()
            references["vision_chat"] = {}
            ModelReferenceRepository(user).save(references)
        result = boundary.handle(request("theme.generate", uuid.uuid4().hex, characterId=character))
        assert result["ok"], result
        assert result["payload"]["characterId"] == character
        assert result["payload"]["themeTokens"] == {field: COLORS[key] for key, field in THEME_FIELDS.items()}
        sent = received.get(timeout=2)
        assert sent["model"] == ("vision" if character == "alpha" else "chat")
        assert base64.b64decode(sent["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]) == PNG
        assert sent["response_format"]["type"] == "json_schema"
        assert (user / "config/characters.yaml").read_bytes() == before
    assert not list(StoragePaths(user).plugin_artifacts_generation_dir("theme-test").rglob("payload*"))


def test_invalid_or_truncated_model_output_is_not_accepted(theme_runtime):
    boundary, _, _, received, _, output = theme_runtime
    for content, finish in [(json.dumps({**COLORS, "text_color": "invalid"}), "stop"),
                            (json.dumps(COLORS), "length")]:
        output.update(content=content, finish=finish)
        result = boundary.handle(request("theme.generate", uuid.uuid4().hex, characterId="alpha"))
        assert not result["ok"]
        assert "themeTokens" not in result.get("payload", {})
        assert result["error"]["details"]["diagnostic"]
        received.get(timeout=2)


def test_cancel_releases_running_generation_and_does_not_deliver_colors(theme_runtime):
    boundary, _, _, received, gate, _ = theme_runtime
    gate.clear()
    operation_id = uuid.uuid4().hex
    result = []
    worker = threading.Thread(target=lambda: result.append(boundary.handle(
        request("theme.generate", operation_id, characterId="alpha"))))
    worker.start()
    try:
        received.get(timeout=10)
        assert boundary.handle(request("theme.cancel", operation_id))["payload"]["cancelled"]
        worker.join(5)
        assert not worker.is_alive()
        assert result[0]["error"]["code"] == "OPERATION_CANCELLED"
    finally:
        gate.set()
        worker.join(5)
