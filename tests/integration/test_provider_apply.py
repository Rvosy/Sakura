"""Real settings IPC reloads the provider and prepares the next chat session."""

import shutil
import threading
from http.server import ThreadingHTTPServer

from tests.integration import test_core_host_real_chat_integration as fixture
from tests.integration.test_core_host_real_chat_integration import _isolated_assistant_distribution


def test_collection_save_keeps_old_connection_and_apply_publishes_new_chat_binding(tmp_path):
    original, original_worker = fixture._start_provider("complete")

    class Replacement(fixture._ProviderHandler):
        requests = []

    replacement = ThreadingHTTPServer(("127.0.0.1", 0), Replacement)
    replacement.daemon_threads = True
    replacement_worker = threading.Thread(target=replacement.serve_forever)
    replacement_worker.start()
    process = None
    try:
        root = fixture._configure_app_root(tmp_path, original.server_port)
        character = root / "characters" / "sakura"
        shutil.copyfile(fixture.REPO_ROOT / "desktop/frontend/prototypes/asr/assets/navi.png", character / "portraits/neutral.png")
        manifest = character / "character.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("portraits/neutral.txt", "portraits/neutral.png"), encoding="utf-8")
        legacy = (root / "config/api.yaml").read_bytes()
        process = fixture._start_host(root)
        fixture._wait_ready(process, ["transport.concurrent-router", "assistant.tools-v1", "assistant.plugins-v1"])
        identity = {"pluginId": "sakura.model.openai_compatible", "sectionId": "connections", "collectionId": "profiles"}
        queried = fixture._exchange(process, fixture._request("profiles", "plugins.collection.query", {
            **identity, "cursor": None, "limit": 25, "search": "", "filters": {},
        }))
        assert queried["ok"], queried
        item, = queried["payload"]["items"]
        values = {key: value for key, value in item["values"].items() if key != "configured"}
        values["base_url"] = f"http://127.0.0.1:{replacement.server_port}/v1"
        saved = fixture._exchange(process, fixture._request("save-profile", "plugins.collection.update", {
            **identity, "itemId": item["itemId"], "values": values,
        }))
        assert saved["ok"], saved
        assert saved["payload"]["applicationState"] == "restart_required"

        def chat(operation):
            fixture._send(process, fixture._request(operation, "chat.send", {"operationId": operation, "message": "你好"}))
            frames = [fixture._read(process), fixture._read(process), fixture._read(process)]
            assert any(frame.get("name") == "chat.completed" for frame in frames), frames

        chat("before-apply")
        assert len(fixture._ProviderHandler.requests) == 1
        assert Replacement.requests == []
        applied = fixture._exchange(process, fixture._request("apply-profile", "plugins.settings.action", {
            "pluginId": identity["pluginId"], "sectionId": identity["sectionId"],
            "actionId": "applyProfiles", "values": {"profileId": "fixture", "modelId": "fixture-model"},
        }))
        assert applied["ok"], applied
        assert applied["payload"]["applicationState"] == "applied"
        chat("after-apply")
        assert len(fixture._ProviderHandler.requests) == 1
        assert len(Replacement.requests) == 1
        assert (root / "config/api.yaml").read_bytes() == legacy
    finally:
        if process is not None:
            fixture._stop(process)
        fixture._stop_provider(original, original_worker)
        fixture._stop_provider(replacement, replacement_worker)
