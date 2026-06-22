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
from app.ui.theme import ThemeSettings, theme_colors_to_mapping
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
    assert '<select id="character" disabled>' in html
    assert "page.scrollTop = page.scrollHeight" in html
    assert "function scrollChatToBottom()" in html
    assert "scrollChatToBottom();" in html[html.index("async function loadHistory()") : html.index("function readImage")]


def test_mobile_page_refreshes_history_before_submit_message() -> None:
    html = mobile_server._mobile_html("secret")
    submit_handler = html[html.index("form.addEventListener('submit'") : html.index("loadCharacters().catch")]

    assert submit_handler.index("await loadHistory();") < submit_handler.index("addMessage('user'")


def test_mobile_page_writes_theme_variables() -> None:
    theme = ThemeSettings(
        primary_color="#112233",
        primary_hover_color="#223344",
        accent_color="#334455",
        text_color="#445566",
        secondary_text_color="#556677",
        muted_text_color="#667788",
        page_background_color="#778899",
        panel_background_color="#8899aa",
        input_background_color="#99aabb",
        bubble_background_color="#aabbcc",
        border_color="#bbccdd",
    )

    html = mobile_server._mobile_html("secret", theme_colors_to_mapping(theme))

    assert "--primary-color: #112233;" in html
    assert "--page-background-color: #778899;" in html
    assert "--panel-background-color: #8899aa;" in html
    assert "--input-background-color: #99aabb;" in html
    assert "--bubble-background-color: #aabbcc;" in html
    assert "--border-color: #bbccdd;" in html
    assert "background: var(--page-background-color); color: var(--text-color);" in html
    assert "background: var(--history-panel-background-color);" in html
    assert "background: var(--assistant-bubble-background-color);" in html
    assert "background: var(--user-bubble-background-color);" in html
    assert "button { border: 0; border-radius: 8px; background: var(--primary-color);" in html


def test_mobile_page_uses_current_character_name_for_labels() -> None:
    html = mobile_server._mobile_html("secret")

    assert '<h1 id="title">手机端聊天</h1>' in html
    assert "let assistantName = '角色';" in html
    assert "title.textContent = assistantName;" in html
    assert "role === 'user' ? '你' : assistantName" in html
    assert "function thinkingText() { return assistantName + ' 正在思考...'; }" in html
    assert "她正在思考" not in html
    assert "meta.textContent = role === 'user' ? '你' : 'Sakura';" not in html


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


def test_mobile_chat_only_exposes_current_character() -> None:
    base_dir = _runtime_root("current_only")
    current = CharacterProfile(
        id="current",
        display_name="Current",
        package_dir=base_dir,
        card_path=base_dir / "current.yaml",
        initial_message="",
        default_portrait_path=base_dir / "current.png",
    )
    target = CharacterProfile(
        id="target",
        display_name="Target",
        package_dir=base_dir,
        card_path=base_dir / "target.yaml",
        initial_message="",
        default_portrait_path=base_dir / "target.png",
    )

    class Registry:
        def get(self, character_id: str) -> CharacterProfile:
            return target if character_id == target.id else current

        def all(self) -> list[CharacterProfile]:
            return [current, target]

    host = SimpleNamespace(
        character_profile=current,
        character_registry=Registry(),
    )
    bridge = MobileChatBridge(host)

    assert bridge.characters() == [
        {
            "id": "current",
            "name": "Current",
            "initial_message": "",
            "current": "true",
        }
    ]

    with pytest.raises(ValueError, match="桌面当前角色"):
        bridge.history("target")


def _runtime_root(name: str) -> Path:
    path = TEST_RUNTIME_ROOT / name / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path
