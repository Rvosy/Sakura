"""tests/unit/test_config.py — 配置系统测试。

覆盖：
- 空配置时生成默认值
- 无效值 fallback
- 保存后格式稳定
"""


from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from app.config.defaults import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SUBTITLE_LANGUAGE,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
)
from app.config.migrations import _parse_dotenv, _coerce_type, migrate_env_to_yaml
from app.config.models import ApiSettings, DebugLogSettings
from app.config.yaml_config import load_yaml_mapping


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_config"


def _make_test_dir(name: str) -> Path:
    """创建继承仓库 ACL 的唯一测试目录，避免 tempfile 在 Windows 沙箱中丢权限。"""
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_settings_stack_has_no_runtime_proactive_care_names() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_files = (
        "app/config/settings_service.py",
        "app/config/defaults.py",
        "app/core/app_context.py",
        "app/ui/tauri_settings.py",
        "tools/settings-tauri/frontend/settings.js",
        "app/ui/history_window.py",
        "main.py",
    )
    for relative in runtime_files:
        source = (root / relative).read_text(encoding="utf-8")
        assert "proactive_care" not in source
        assert "proactive_" not in source


def test_runtime_has_no_proactive_care_compatibility_path() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "app/agent/proactive_care.py").exists()

    excluded = {
        root / "app/config/migration_runner.py",
        root / "app/config/migrations.py",
        root / "app/agent/runtime.py",
    }
    checked = [root / "main.py"]
    checked.extend(path for path in (root / "app").rglob("*.py") if path not in excluded)
    checked.extend((root / "plugins").rglob("*.py"))
    forbidden = (
        "LEGACY_PROACTIVE_EVENT_TYPE",
        "proactive_check",
        "ProactiveCare",
        "PROACTIVE_",
        "_check_proactive_care",
        "proactive_care_settings",
        "proactive_care_timer",
        "proactive_screen_contexts",
        "proactive_context",
        "proactive_mode",
        "build_proactive",
        "agent.proactive",
        "proactive_tool_loop",
    )
    for path in checked:
        source = path.read_text(encoding="utf-8")
        assert "proactive" not in source.lower()
        for name in forbidden:
            assert name not in source

    runtime_source = (root / "app/agent/runtime.py").read_text(encoding="utf-8")
    for name in forbidden:
        assert name not in runtime_source
    assert 'raise ValueError(f"不支持的主动事件类型：{event.type}")' in runtime_source
    assert "proactive" not in runtime_source.lower()


class TestApiSettings:
    """ApiSettings 模型"""

    def test_defaults(self) -> None:
        s = ApiSettings()
        assert DEFAULT_BASE_URL == ""
        assert DEFAULT_MODEL == ""
        assert DEFAULT_TEXT_MODEL == ""
        assert DEFAULT_VISION_MODEL == ""
        assert s.base_url == DEFAULT_BASE_URL
        assert s.model == DEFAULT_MODEL
        assert s.timeout_seconds == 60
        assert s.api_key == ""

    def test_custom(self) -> None:
        s = ApiSettings(base_url="https://custom.api", model="gpt-5", timeout_seconds=30)
        assert s.base_url == "https://custom.api"
        assert s.model == "gpt-5"
        assert s.timeout_seconds == 30


class TestDebugLogSettings:
    """DebugLogSettings 模型"""

    def test_defaults(self) -> None:
        s = DebugLogSettings()
        assert s.enabled is True
        assert s.body_enabled is False
        assert s.file_enabled is True
        assert s.profile == "info"

    def test_enabled(self) -> None:
        s = DebugLogSettings(enabled=True)
        assert s.enabled is True


class TestDotenvParsing:
    """.env 解析"""

    def test_parse_simple(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("BASE_URL=https://api.example.com\nAPI_KEY=sk-test123\n")
            path = Path(f.name)
        try:
            result = _parse_dotenv(path)
            assert result["BASE_URL"] == "https://api.example.com"
            assert result["API_KEY"] == "sk-test123"
        finally:
            path.unlink()

    def test_parse_empty(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("# comment only\n")
            path = Path(f.name)
        try:
            result = _parse_dotenv(path)
            assert result == {}
        finally:
            path.unlink()

    def test_parse_quotes_and_export_prefix(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('export API_KEY="sk-test"\nMODEL=\'demo model\'\n')
            path = Path(f.name)
        try:
            result = _parse_dotenv(path)
            assert result == {"API_KEY": "sk-test", "MODEL": "demo model"}
        finally:
            path.unlink()

    def test_parse_rejects_unclosed_quote(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('API_KEY="broken\n')
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="引号未闭合"):
                _parse_dotenv(path)
        finally:
            path.unlink()

    def test_parse_missing_file(self) -> None:
        result = _parse_dotenv(Path("/nonexistent/.env"))
        assert result == {}


class TestTypeCoercion:
    """类型转换"""

    def test_bool_true(self) -> None:
        for v in ["true", "True", "TRUE", "1", "yes", "on"]:
            assert _coerce_type(v) is True, f"Failed for {v}"

    def test_bool_false(self) -> None:
        for v in ["false", "False", "FALSE", "0", "no", "off"]:
            assert _coerce_type(v) is False, f"Failed for {v}"

    def test_int(self) -> None:
        assert _coerce_type("42") == 42
        assert _coerce_type("0") is False  # "0" is false, not 0

    def test_string(self) -> None:
        assert _coerce_type("hello") == "hello"


class TestMigration:
    """.env → YAML 迁移"""

    def test_migrate_basic(self) -> None:
        base = _make_test_dir("migrate_basic")
        try:
            config_dir = base / "data" / "config"
            config_dir.mkdir(parents=True)

            # 创建 .env
            env_path = base / ".env"
            env_path.write_text("BASE_URL=https://custom.api\nAPI_KEY=sk-migrated\n")

            api_yaml = config_dir / "api.yaml"
            api_yaml.write_text("llm:\n  base_url: https://default.api\n")

            system_yaml = config_dir / "system_config.yaml"
            system_yaml.write_text("ui:\n  subtitle_language: ja\n")

            result = migrate_env_to_yaml(env_path, api_yaml, system_yaml)
            assert "BASE_URL" in result["migrated"]
            assert "API_KEY" in result["migrated"]
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_migrate_proactive_env_keys_to_screen_awareness(self) -> None:
        base = _make_test_dir("migrate_proactive_env")
        try:
            config_dir = base / "data" / "config"
            config_dir.mkdir(parents=True)
            env_path = base / ".env"
            env_path.write_text(
                "PROACTIVE_CARE_ENABLED=false\n"
                "PROACTIVE_SCREEN_CONTEXT_ENABLED=true\n"
                "PROACTIVE_CHECK_INTERVAL_MINUTES=5\n"
                "PROACTIVE_COOLDOWN_MINUTES=17\n",
                encoding="utf-8",
            )
            api_yaml = config_dir / "api.yaml"
            api_yaml.write_text("llm: {}\n", encoding="utf-8")
            system_yaml = config_dir / "system_config.yaml"
            system_yaml.write_text("{}\n", encoding="utf-8")

            migrate_env_to_yaml(env_path, api_yaml, system_yaml)
            system = load_yaml_mapping(system_yaml)

            assert system["screen_awareness"]["enabled"] is False
            assert system["screen_awareness"]["screen_context_enabled"] is True
            assert system["screen_awareness"]["check_interval_minutes"] == 5
            assert system["screen_awareness"]["cooldown_minutes"] == 17
            assert "proactive_care" not in system
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_migrate_missing_env(self) -> None:
        base = _make_test_dir("migrate_missing_env")
        try:
            config_dir = base / "data" / "config"
            config_dir.mkdir(parents=True)
            api_yaml = config_dir / "api.yaml"
            api_yaml.write_text("llm: {}\n")
            system_yaml = config_dir / "system_config.yaml"
            system_yaml.write_text("{}\n")
            result = migrate_env_to_yaml(base / "missing.env", api_yaml, system_yaml)
            assert result["errors"]
        finally:
            shutil.rmtree(base, ignore_errors=True)
