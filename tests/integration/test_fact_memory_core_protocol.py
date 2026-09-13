from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.runtime_roots import RuntimeRoots
from tests.integration.test_core_host_real_chat_integration import (
    REPO_ROOT,
    _configure_app_root,
    _hello,
    _start_host,
    _stop,
)
from tests.integration.test_executor_core_protocol import _CorePeer
from tests.integration.test_wp_4_01_memory_capability import _install_official_mem0
from tools.release.package_optional_plugin import build


PLUGIN_ID = "fact_memory"
RULE = "对照规则：回复保持简洁。"
ORIGINAL_FACT = "验收项目交付时间是周五下午三点。"
UPDATED_FACT = "验收项目交付时间改为周六上午十点。"
SEARCH_MESSAGE = "请调用便签搜索工具查找验收项目。"


@contextmanager
def _provider():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            messages = body["messages"]
            user_index = max(index for index, message in enumerate(messages) if message["role"] == "user")
            search_requested = SEARCH_MESSAGE in str(messages[user_index]["content"])
            search_completed = any(message["role"] == "tool" for message in messages[user_index + 1:])
            if search_requested and not search_completed:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "fact-search-call",
                        "type": "function",
                        "function": {
                            "name": "fact_memory_search",
                            "arguments": json.dumps({"query": "验收项目", "limit": 5}),
                        },
                    }],
                }
            else:
                message = {
                    "role": "assistant",
                    # Keep fixture replies free of facts, so later assertions
                    # distinguish new Context from facts echoed into history.
                    "content": json.dumps({"segments": [{"ja": "確認しました。", "zh": "检查完成。"}]}),
                }
            response = json.dumps({"choices": [{"message": message}]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="fact-memory-provider")
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@contextmanager
def _core(user: Path, distribution: Path, *, model_configured=True):
    process = _start_host(user, distribution_root=distribution)
    children = []
    try:
        peer = _CorePeer(process)
        peer.exchange(_hello(["transport.concurrent-router", "assistant.plugins-v1", "assistant.tools-v1"]))
        peer.request("core.initialize")
        peer.readiness("ready" if model_configured else "setup_required")
        yield peer
    finally:
        if process.poll() is None:
            children = psutil.Process(process.pid).children(recursive=True)
        _stop(process)
        assert process.returncode == 0
        assert all(not child.is_running() for child in children)


def _plugin(peer: _CorePeer):
    return next(item for item in peer.request("plugins.settings.get")["plugins"] if item["pluginId"] == PLUGIN_ID)


def _enable(peer: _CorePeer, enabled: bool):
    snapshot = peer.request("plugins.settings.get")
    plugin = next(item for item in snapshot["plugins"] if item["pluginId"] == PLUGIN_ID)
    result = peer.request("plugins.enabled.set", {
        "revision": snapshot["revision"], "installId": plugin["installId"], "enabled": enabled,
    })
    assert result["applicationState"] == "applied"
    assert _plugin(peer)["state"] == ("active" if enabled else "disabled")


def _collection(peer: _CorePeer, operation: str, **values):
    return peer.request(f"plugins.collection.{operation}", {
        "pluginId": PLUGIN_ID, "sectionId": PLUGIN_ID, "collectionId": "facts", **values,
    })


def _query(peer: _CorePeer, search=""):
    return _collection(peer, "query", cursor=None, limit=25, search=search, filters={})


def _prompt_context(request):
    return "\n".join(
        str(message["content"]) for message in request["messages"]
        if message["role"] in {"system", "developer"}
    )


def test_install_manage_recall_disable_and_restart_memory_through_real_core(tmp_path: Path):
    archive = tmp_path / "fact_memory.sakplugin.zip"
    build(REPO_ROOT / "plugins/optional/fact_memory", archive)
    with _provider() as (port, requests):
        user = _configure_app_root(tmp_path, port)
        distribution = tmp_path / "distribution"
        _install_official_mem0(distribution)
        roots = RuntimeRoots(distribution, user)
        desired = PluginDesiredStateStore(user)
        desired.set("sakura.memory.mem0", False)
        LocalPluginInstaller(roots).install(REPO_ROOT / "plugins/optional/context_rules", "folder")
        desired.set("context_rules", True)
        rule_config = user / "data/plugins/context_rules/config.json"
        rule_config.parent.mkdir(parents=True, exist_ok=True)
        rule_config.write_text(json.dumps({"content": RULE}), encoding="utf-8")
        mem0_config = user / "data/plugins/sakura.memory.mem0/config.json"
        mem0_config.parent.mkdir(parents=True, exist_ok=True)
        mem0_config.write_text('{"triggerTurns": 9}', encoding="utf-8")
        mem0_data = user / "data/memory/core_profiles.json"
        mem0_data.parent.mkdir(parents=True, exist_ok=True)
        mem0_data.write_text('{"sakura": "existing memory fixture"}', encoding="utf-8")
        protected = {path: path.read_bytes() for path in [
            user / "config/api.yaml", user / "config/system_config.yaml", user / "config/characters.yaml",
            mem0_config, mem0_data, rule_config,
        ]}

        with _core(user, distribution) as peer:
            snapshot = peer.request("plugins.settings.get")
            peer.request("plugins.install", {
                "revision": snapshot["revision"], "sourceKind": "zip", "sourcePath": str(archive),
            })
            assert _plugin(peer)["state"] == "disabled"
            prior_pids = {child.pid for child in psutil.Process(peer.process.pid).children()}
            _enable(peer, True)
            fact_process = next(child for child in psutil.Process(peer.process.pid).children() if child.pid not in prior_pids)
            assert len({os.getpid(), peer.process.pid, fact_process.pid}) == 3
            section = next(item for item in _plugin(peer)["sections"] if item["sectionId"] == PLUGIN_ID)
            assert section["surface"] is None
            assert [item["collectionId"] for item in section["collections"]] == ["facts"]
            assert _query(peer)["items"] == []
            item = _collection(peer, "create", values={"content": ORIGINAL_FACT, "keywords": "验收项目"})
            item_id = item["itemId"]
            assert _query(peer, "周五")["items"][0]["itemId"] == item_id

            peer.chat("memory-recall", "验收项目什么时候交付？")
            peer.completed("memory-recall")
            assert ORIGINAL_FACT in _prompt_context(requests[-1])
            assert RULE in _prompt_context(requests[-1])
            assert any(tool["function"]["name"] == "fact_memory_search" for tool in requests[-1]["tools"])

            _collection(peer, "update", itemId=item_id, values={"content": UPDATED_FACT, "keywords": "验收项目"})
            peer.chat("memory-updated", "再看一下验收项目。")
            peer.completed("memory-updated")
            assert UPDATED_FACT in _prompt_context(requests[-1])
            assert ORIGINAL_FACT not in _prompt_context(requests[-1])

            _enable(peer, False)
            assert not fact_process.is_running()
            peer.chat("memory-disabled", "验收项目还有补充吗？")
            peer.completed("memory-disabled")
            assert UPDATED_FACT not in _prompt_context(requests[-1])
            assert RULE in _prompt_context(requests[-1])
            assert not any(tool["function"]["name"] == "fact_memory_search" for tool in requests[-1].get("tools", []))
            _enable(peer, True)
            assert _query(peer)["items"][0]["values"]["content"] == UPDATED_FACT

        # A real Core restart must reuse the plugin's own durable records.
        with _core(user, distribution) as peer:
            assert _query(peer)["items"][0]["itemId"] == item_id
            start = len(requests)
            peer.chat("memory-search-tool", SEARCH_MESSAGE)
            peer.completed("memory-search-tool")
            assert len(requests) == start + 2, requests[-1]["messages"][-3:]
            tool_message = next(message for message in requests[-1]["messages"] if message["role"] == "tool")
            assert UPDATED_FACT in tool_message["content"]
            assert item_id in tool_message["content"]

            assert _collection(peer, "delete", itemId=item_id) == {"deleted": True}
            assert _query(peer)["items"] == []
            peer.chat("memory-deleted", "验收项目便签还有吗？")
            peer.completed("memory-deleted")
            assert UPDATED_FACT not in _prompt_context(requests[-1])
            assert RULE in _prompt_context(requests[-1])

        assert all(path.read_bytes() == before for path, before in protected.items())


def test_memory_collection_works_without_a_model_and_never_initializes_one(tmp_path: Path):
    user = _configure_app_root(tmp_path, 1)
    (user / "config/api.yaml").unlink()
    distribution = tmp_path / "distribution"
    distribution.mkdir()
    result = LocalPluginInstaller(RuntimeRoots(distribution, user)).install(
        REPO_ROOT / "plugins/optional/fact_memory", "folder",
    )
    PluginDesiredStateStore(user).set(result.plugin_id, True)
    with _core(user, distribution, model_configured=False) as peer:
        assert _plugin(peer)["state"] == "active"
        item = _collection(peer, "create", values={"content": ORIGINAL_FACT, "keywords": "验收项目"})
        assert _query(peer)["items"][0]["itemId"] == item["itemId"]
        assert peer.request("core.snapshot")["readiness"] == "setup_required"
    assert not (user / "config/api.yaml").exists()
