from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.actions import AgentResult
from app.agent.runtime_limits import RuntimeLoopSettings
from app.agent.tools import Tool, ToolRegistry
from app.config.character_loader import CharacterProfile
from app.core.mobile_chat_bridge import MobileChatBridge, MobileChatBusyError
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.chat_history import ChatHistoryEntry
from plugins.sakura_mobile import server as mobile_server


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_RUNTIME_ROOT = PROJECT_ROOT / "temp" / "test_sakura_mobile"


def test_mobile_default_config_is_lan_ready_but_disabled() -> None:
    config = json.loads((PROJECT_ROOT / "plugins" / "sakura_mobile" / "config.json").read_text(encoding="utf-8"))

    assert config["enabled"] is False
    assert config["host"] == "0.0.0.0"


def test_mobile_access_urls_include_local_and_lan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mobile_server, "local_ipv4_addresses", lambda: ["192.168.1.23"])

    urls = mobile_server.mobile_access_urls("0.0.0.0", 8765, "secret")

    assert urls["local_url"] == "http://127.0.0.1:8765/?token=secret"
    assert urls["lan_urls"] == ["http://192.168.1.23:8765/?token=secret"]


def test_mobile_page_scrolls_to_bottom_after_history_load() -> None:
    html = mobile_server._mobile_html("secret")

    assert "grid-template-rows: auto minmax(0, 1fr) auto" in html
    assert "#chat { min-height: 0;" in html
    assert "page.scrollTop = page.scrollHeight" in html
    assert "function scrollChatToBottom()" in html
    assert "scrollChatToBottom();" in html[html.index("async function loadHistory()") : html.index("function readImage")]


def test_mobile_chat_busy_returns_409() -> None:
    class BusyBackend:
        def characters(self) -> list[dict[str, str]]:
            return []

        def history(self, _character_id: str, *, limit: int = 50) -> list[dict[str, str]]:
            return []

        def chat(self, _character_id: str, _text: str, _image_data_url: str = "") -> dict[str, object]:
            raise MobileChatBusyError("Sakura 正忙，请稍后再试。")

    base_dir = _runtime_root("busy")
    server = mobile_server.run_mobile_server(
        base_dir,
        BusyBackend(),
        host="127.0.0.1",
        port=0,
        token="secret",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chat?token=secret",
            data=b'{"text":"hello"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=5)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 409
        assert payload == {"ok": False, "busy": True, "error": "Sakura 正忙，请稍后再试。"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mobile_session_keeps_empty_tool_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.mobile_chat_bridge as bridge_module
    base_dir = _runtime_root("empty_tools")

    class FakeRuntime:
        def __init__(self, *args: object, tools: ToolRegistry, **kwargs: object) -> None:
            self.tools = tools
            self.memory = kwargs["memory"]
            self.api_client = object()
            self.system_prompt = ""

        def set_autonomous_screen_observation_enabled(self, _enabled: bool) -> None:
            pass

    profile = CharacterProfile(
        id="demo",
        display_name="Demo",
        package_dir=base_dir,
        card_path=base_dir / "character.yaml",
        initial_message="",
        default_portrait_path=base_dir / "portrait.png",
    )

    class Registry:
        def get(self, _character_id: str) -> CharacterProfile:
            return profile

        def all(self) -> list[CharacterProfile]:
            return [profile]

    class Memory:
        scope_id = "host"

        def scoped(self, _scope_id: str) -> "Memory":
            raise AssertionError("mobile chat must reuse the host memory store")

    monkeypatch.setattr(bridge_module, "AgentRuntime", FakeRuntime)
    monkeypatch.setattr(bridge_module, "OpenAICompatibleClient", lambda _settings: object())
    monkeypatch.setattr(bridge_module, "load_character_system_prompt", lambda _profile: "")

    host_tools = ToolRegistry(
        [
            Tool(
                name="host_tool",
                description="host only",
                parameters={},
                handler=lambda _args: None,
            )
        ]
    )
    memory = Memory()
    host = SimpleNamespace(
        character_profile=profile,
        character_registry=Registry(),
        _create_history_store=lambda _profile: object(),
        memory_store=memory,
        api_client=SimpleNamespace(settings=object()),
        agent_runtime=SimpleNamespace(
            prompt_patches=[],
            context_providers=[],
            runtime_loop_settings=RuntimeLoopSettings(),
        ),
        base_dir=base_dir,
        tool_registry=host_tools,
    )

    session = MobileChatBridge(host)._session("demo")

    assert session.runtime.tools.get("host_tool") is None
    assert session.runtime.memory is memory


def test_mobile_chat_returns_without_inline_memory_curation(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.mobile_chat_bridge as bridge_module
    base_dir = _runtime_root("no_inline_curation")

    class FakeRuntime:
        def __init__(self, *args: object, tools: ToolRegistry, **kwargs: object) -> None:
            self.tools = tools
            self.api_client = SimpleNamespace(update_settings=lambda _settings: None)
            self.system_prompt = ""

        def set_autonomous_screen_observation_enabled(self, _enabled: bool) -> None:
            pass

        def set_prompt_patches(self, _patches: list[object]) -> None:
            pass

        def set_context_providers(self, _providers: list[object]) -> None:
            pass

        def handle_user_message(self, _messages: list[object]) -> AgentResult:
            return AgentResult(ChatReply([ChatSegment("返事。", translation="回复。")]))

    class HistoryStore:
        def __init__(self) -> None:
            self.entries: list[ChatHistoryEntry] = []

        def append(
            self,
            role: str,
            content: str,
            translation: str = "",
            tone: str = "",
            portrait: str = "",
            _debug: dict | None = None,
        ) -> None:
            self.entries.append(ChatHistoryEntry("now", role, content, translation, tone, portrait))

        def load(self) -> list[ChatHistoryEntry]:
            return list(self.entries)

    profile = CharacterProfile(
        id="demo",
        display_name="Demo",
        package_dir=base_dir,
        card_path=base_dir / "character.yaml",
        initial_message="",
        default_portrait_path=base_dir / "portrait.png",
    )

    class Registry:
        def get(self, _character_id: str) -> CharacterProfile:
            return profile

        def all(self) -> list[CharacterProfile]:
            return [profile]

    class Memory:
        scope_id = "host"

        def scoped(self, _scope_id: str) -> "Memory":
            raise AssertionError("mobile chat must reuse the host memory store")

        def set_scope(self, scope_id: str) -> None:
            self.scope_id = scope_id

    class Signal:
        payload: dict[str, object] | None = None

        def emit(self, payload: dict[str, object]) -> None:
            self.payload = payload

    history_store = HistoryStore()
    completed = Signal()
    monkeypatch.setattr(bridge_module, "AgentRuntime", FakeRuntime)
    monkeypatch.setattr(bridge_module, "OpenAICompatibleClient", lambda _settings: object())
    monkeypatch.setattr(bridge_module, "load_character_system_prompt", lambda _profile: "")
    host = SimpleNamespace(
        character_profile=profile,
        character_registry=Registry(),
        _create_history_store=lambda _profile: history_store,
        memory_store=Memory(),
        api_client=SimpleNamespace(settings=object()),
        agent_runtime=SimpleNamespace(
            prompt_patches=[],
            context_providers=[],
            runtime_loop_settings=RuntimeLoopSettings(),
        ),
        base_dir=base_dir,
        mobile_chat_completed=completed,
    )

    result = MobileChatBridge(host).execute_chat("demo", "hello")

    assert result["reply"] == "回复。"
    assert completed.payload is not None
    assert [entry.role for entry in history_store.load()] == ["user", "assistant"]


def _runtime_root(name: str) -> Path:
    path = TEST_RUNTIME_ROOT / name / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path
