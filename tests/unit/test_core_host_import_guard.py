from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.config.character_loader import (
    THEME_SOURCE_COMPAT_DEFAULT,
    THEME_SOURCE_PACKAGE,
    CharacterRegistry,
)
from app.config.models import ApiConfigProfile, ApiSettings as ConfigApiSettings
from app.llm.api_client import ApiSettings as ClientApiSettings
from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings


REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PREFIXES = (
    "PySide6",
    "app.ui",
    "app.agent",
    "app.brain_host",
    "app.plugins",
    "app.voice",
)


def test_minimal_core_host_import_graph_has_no_qt_or_domain_modules() -> None:
    probe = """
import json
import sys
import app.core_host.__main__
import app.core_host.protocol
import app.core_host.server
forbidden = sorted(
    name for name in sys.modules
    if name.startswith((
        'PySide6', 'app.ui', 'app.agent', 'app.brain_host', 'app.plugins', 'app.voice'
    ))
)
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    imported = json.loads(completed.stdout)
    assert imported == []
    assert all(
        not name.startswith(FORBIDDEN_PREFIXES) for name in imported
    )


def test_approved_session_construction_is_qt_and_optional_domain_free() -> None:
    probe = """
import json
import sys
from pathlib import Path

from app.config.character_loader import CharacterProfile
from app.llm.api_client import ApiSettings, ChatCompletionTurn, OpenAICompatibleClient
from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.core.chat_pipeline import ChatPipeline

class DisabledMemory:
    def search_memory(self, _arguments, *, wait=False):
        return {'status': 'disabled', 'memories': []}

class Client(OpenAICompatibleClient):
    def __init__(self):
        super().__init__(ApiSettings('https://example.invalid', 'secret', 'model'))
        self.last_runtime_context = ''
    def complete_with_tools(self, _system_prompt, _messages, **_kwargs):
        self.last_runtime_context = _kwargs.get('runtime_context', '')
        return ChatCompletionTurn(
            content='{"segments":[{"ja":"続けるよ。","zh":"继续吧。"}]}',
            tool_calls=[],
            message={'role': 'assistant', 'content': 'ok'},
        )

class History:
    def load(self):
        from app.storage.chat_history import ChatHistoryEntry
        return [
            ChatHistoryEntry(created_at='2026-07-25T10:00:00+08:00', role='user', content='继续旧计划'),
            ChatHistoryEntry(created_at='2026-07-25T10:00:01+08:00', role='assistant', content='好的'),
        ]

profile = CharacterProfile(
    id='sakura',
    display_name='Sakura',
    package_dir=Path('.'),
    card_path=Path('card.md'),
    initial_message='hello',
    default_portrait_path=Path('portrait.png'),
)
runtime = AgentRuntime(
    Client(),
    'system prompt',
    tools=ToolRegistry([]),
    memory=DisabledMemory(),
    history_store=History(),
)
pipeline = ChatPipeline(runtime)

forbidden_exact = {
    'app.ui.window_backdrop',
    'app.agent.memory',
    'app.storage.chat_history',
    'app.storage.visual_observation',
    'app.agent.context_orchestrator',
    'app.agent.session_state_context',
}
def forbidden():
    return sorted(
        name for name in sys.modules
        if name.startswith(('PySide6', 'app.plugins', 'app.voice', 'app.agent.mcp', 'app.agent.screen'))
        or name in forbidden_exact
        or (name.startswith('app.ui.') and name != 'app.ui.theme')
    )

def qt_or_plugin_forbidden():
    return sorted(
        name for name in sys.modules
        if name.startswith(('PySide6', 'app.plugins'))
    )

before = forbidden()
result = pipeline.run_user_message([{'role': 'user', 'content': '本轮问题'}])
after = sorted(
    name for name in ('app.agent.context_orchestrator', 'app.agent.session_state_context', 'app.storage.chat_history')
    if name in sys.modules
)
print(json.dumps({
    'before': before,
    'after': after,
    'after_qt_or_plugin_forbidden': qt_or_plugin_forbidden(),
    'reply': result.reply.text,
    'runtime_context': runtime.api_client.last_runtime_context,
    'theme_type': type(profile.theme_settings).__name__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["before"] == []
    assert result["after"] == [
        "app.agent.context_orchestrator",
        "app.agent.session_state_context",
        "app.storage.chat_history",
    ]
    assert result["after_qt_or_plugin_forbidden"] == []
    assert result["reply"] == "続けるよ。"
    assert "最近会话状态" in result["runtime_context"]
    assert "继续旧计划" in result["runtime_context"]
    assert result["theme_type"] == "ThemeSettings"


def test_core_first_theme_loading_does_not_poison_normal_ui_imports() -> None:
    probe = """
import json
import sys
from pathlib import Path

from app.config.character_loader import CharacterProfile

profile = CharacterProfile(
    id='sakura',
    display_name='Sakura',
    package_dir=Path('.'),
    card_path=Path('card.md'),
    initial_message='hello',
    default_portrait_path=Path('portrait.png'),
)
core_forbidden = sorted(
    name for name in sys.modules
    if name.startswith(('PySide6', 'app.plugins', 'app.voice'))
    or (name.startswith('app.ui.') and name != 'app.ui.theme')
)
core_ui_loaded = 'app.ui' in sys.modules
core_theme_loaded = 'app.ui.theme' in sys.modules
app_has_ui = hasattr(sys.modules['app'], 'ui')

import app.ui.theme as theme
from app.ui.window_backdrop import VisualEffectMode

print(json.dumps({
    'core_forbidden': core_forbidden,
    'core_ui_loaded': core_ui_loaded,
    'core_theme_loaded': core_theme_loaded,
    'app_has_ui': app_has_ui,
    'theme_color': theme.ThemeSettings(primary_color='ABCDEF').normalized().primary_color,
    'profile_color': profile.theme_settings.normalized().primary_color,
    'effect': VisualEffectMode.validate('solid'),
    'same_type': type(profile.theme_settings) is theme.ThemeSettings,
    'isinstance': isinstance(profile.theme_settings, theme.ThemeSettings),
    'equal_default': profile.theme_settings == theme.DEFAULT_THEME_SETTINGS,
    'same_module': type(profile.theme_settings).__module__ == theme.ThemeSettings.__module__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result == {
        "core_forbidden": [],
        "core_ui_loaded": False,
        "core_theme_loaded": False,
        "app_has_ui": False,
        "theme_color": "#abcdef",
        "profile_color": DEFAULT_THEME_SETTINGS.primary_color,
        "effect": "solid",
        "same_type": True,
        "isinstance": True,
        "equal_default": True,
        "same_module": True,
    }


def test_concurrent_core_first_theme_parsing_uses_canonical_ui_type() -> None:
    probe = """
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from app.config.character_loader import CharacterProfile

barrier = Barrier(16)
def build(index):
    barrier.wait()
    profile = CharacterProfile(
        id=f'character-{index}',
        display_name='Sakura',
        package_dir=Path('.'),
        card_path=Path('card.md'),
        initial_message='hello',
        default_portrait_path=Path('portrait.png'),
    )
    return profile.theme_settings

with ThreadPoolExecutor(max_workers=16) as pool:
    settings = list(pool.map(build, range(16)))

core_forbidden = sorted(
    name for name in sys.modules
    if name.startswith(('PySide6', 'app.ui', 'app.plugins', 'app.voice'))
)
private_theme_modules = sorted(
    name for name in sys.modules
    if name.startswith('_sakura_core_ui_theme')
)

import app.ui.theme as theme

print(json.dumps({
    'core_forbidden': core_forbidden,
    'private_theme_modules': private_theme_modules,
    'one_type': len({id(type(item)) for item in settings}) == 1,
    'same_type': all(type(item) is theme.ThemeSettings for item in settings),
    'isinstance': all(isinstance(item, theme.ThemeSettings) for item in settings),
    'equal_default': all(item == theme.DEFAULT_THEME_SETTINGS for item in settings),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result == {
        "core_forbidden": [],
        "private_theme_modules": [],
        "one_type": True,
        "same_type": True,
        "isinstance": True,
        "equal_default": True,
    }


def _write_character(root: Path, character_id: str, theme: dict[str, object] | None) -> None:
    package_dir = root / "characters" / character_id
    package_dir.mkdir(parents=True)
    (package_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (package_dir / "portrait.png").write_bytes(b"portrait")
    manifest: dict[str, object] = {
        "id": character_id,
        "display_name": character_id.title(),
        "card": "card.md",
        "portrait": {"default": "portrait.png"},
    }
    if theme is not None:
        manifest["theme"] = theme
    (package_dir / "character.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )


def test_character_manifest_theme_semantics_remain_legacy_equivalent(tmp_path: Path) -> None:
    _write_character(tmp_path, "legacy", None)
    _write_character(
        tmp_path,
        "themed",
        {
            "source": THEME_SOURCE_PACKAGE,
            "primary_color": "ABCDEF",
            "accent_color": "not-a-color",
            "ai_enabled": "true",
            "visual_effect_mode": "solid",
        },
    )

    registry = CharacterRegistry(tmp_path)
    legacy = registry.get("legacy")
    themed = registry.get("themed")

    assert legacy.theme_settings == DEFAULT_THEME_SETTINGS
    assert legacy.theme_settings.normalized() == DEFAULT_THEME_SETTINGS
    assert legacy.theme_source == THEME_SOURCE_COMPAT_DEFAULT
    assert themed.theme_settings == ThemeSettings(primary_color="#abcdef")
    assert themed.theme_settings.normalized() == themed.theme_settings
    assert themed.theme_source == THEME_SOURCE_PACKAGE


def test_character_registry_accepts_injected_issue_sink(tmp_path: Path) -> None:
    _write_character(tmp_path, "valid", None)
    broken_dir = tmp_path / "characters" / "broken"
    broken_dir.mkdir(parents=True)
    (broken_dir / "character.json").write_text("{}", encoding="utf-8")
    issues: list[tuple[str, str, dict[str, object]]] = []

    registry = CharacterRegistry(
        tmp_path,
        issue_sink=lambda scope, message, details: issues.append((scope, message, details)),
    )

    assert registry.get("valid").id == "valid"
    assert len(registry.load_errors) == 1
    assert len(issues) == 1
    assert issues[0][0:2] == ("Character", "跳过损坏或不安全的角色包")


def test_visual_effect_and_lazy_agent_public_imports_remain_compatible() -> None:
    from app.agent import AgentRuntime, ToolRegistry
    from app.agent.runtime import AgentRuntime as RuntimeImplementation
    from app.agent.tools import ToolRegistry as RegistryImplementation
    from app.ui.window_backdrop import VisualEffectMode

    assert AgentRuntime is RuntimeImplementation
    assert ToolRegistry is RegistryImplementation
    assert VisualEffectMode.validate("invalid") == VisualEffectMode.DEFAULT


def test_api_key_fields_are_excluded_from_repr_without_changing_equality() -> None:
    secret = "sk-secret-value"
    assert secret not in repr(ClientApiSettings("https://example.invalid", secret, "model"))
    assert secret not in repr(ConfigApiSettings(api_key=secret))
    assert secret not in repr(ApiConfigProfile("id", "alias", "https://example.invalid", secret))
    assert ApiConfigProfile("id", "alias", "https://example.invalid") == ApiConfigProfile(
        "id", "alias", "https://example.invalid", ""
    )
