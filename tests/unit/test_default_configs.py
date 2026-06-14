"""tests/unit/test_default_configs.py — 默认配置生成与版本标记测试。

覆盖：
- 缺失时生成默认 mcp.yaml/plugins.yaml，且内容可被对应加载器解析
- 已存在的用户配置绝不被覆盖（覆盖升级安全的核心断言）
- app_version 首次记录 / 升级检测 / 无变化不写
- 覆盖升级模拟：新包不含 data/，解压后用户配置原样保留
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config.app_version import APP_VERSION_KEY, read_app_version, record_app_version
from app.config.default_configs import ensure_default_configs
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.storage.paths import StoragePaths


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_default_configs"


def _make_base(name: str) -> Path:
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


class TestEnsureDefaultConfigs:
    def test_creates_missing_files(self) -> None:
        base = _make_base("create")
        created = ensure_default_configs(base)
        assert sorted(created) == ["mcp.yaml", "plugins.yaml"]
        paths = StoragePaths(base)
        assert paths.mcp_config().is_file()
        assert paths.plugins_config().is_file()

    def test_generated_mcp_yaml_is_loadable(self) -> None:
        base = _make_base("mcp_loadable")
        ensure_default_configs(base)
        from app.agent.mcp.config import load_mcp_config

        config = load_mcp_config(StoragePaths(base).mcp_config())
        assert config.enabled
        names = [server.name for server in config.servers]
        assert "web" in names

    def test_generated_plugins_yaml_is_loadable(self) -> None:
        base = _make_base("plugins_loadable")
        ensure_default_configs(base)
        import yaml

        data = yaml.safe_load(StoragePaths(base).plugins_config().read_text(encoding="utf-8"))
        ids = [entry["id"] for entry in data]
        assert "playwright_browser" in ids

    def test_existing_user_config_never_overwritten(self) -> None:
        base = _make_base("no_overwrite")
        paths = StoragePaths(base)
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        user_mcp = "enabled: false\nservers: {}\n"  # 用户禁用了 MCP
        paths.mcp_config().write_text(user_mcp, encoding="utf-8")
        created = ensure_default_configs(base)
        assert "mcp.yaml" not in created
        assert paths.mcp_config().read_text(encoding="utf-8") == user_mcp

    def test_idempotent(self) -> None:
        base = _make_base("idem")
        first = ensure_default_configs(base)
        second = ensure_default_configs(base)
        assert first and not second


class TestAppVersion:
    def test_read_version_strips_v_prefix(self) -> None:
        base = _make_base("read")
        (base / "VERSION").write_text("v1.2.3\n", encoding="utf-8")
        assert read_app_version(base) == "1.2.3"

    def test_missing_version_file(self) -> None:
        base = _make_base("missing")
        assert read_app_version(base) == ""
        assert record_app_version(base) == ("", "")

    def test_first_record(self) -> None:
        base = _make_base("first")
        (base / "VERSION").write_text("0.9.7-dev\n", encoding="utf-8")
        previous, current = record_app_version(base)
        assert previous == ""
        assert current == "0.9.7-dev"
        data = load_yaml_mapping(StoragePaths(base).system_config())
        assert data[APP_VERSION_KEY] == "0.9.7-dev"

    def test_upgrade_detected_and_updated(self) -> None:
        base = _make_base("upgrade")
        (base / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        config_path = StoragePaths(base).system_config()
        save_yaml_mapping(config_path, {APP_VERSION_KEY: "0.9.7"})
        previous, current = record_app_version(base)
        assert previous == "0.9.7"
        assert current == "1.0.0"
        assert load_yaml_mapping(config_path)[APP_VERSION_KEY] == "1.0.0"

    def test_same_version_no_rewrite(self) -> None:
        base = _make_base("same")
        (base / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        config_path = StoragePaths(base).system_config()
        save_yaml_mapping(config_path, {APP_VERSION_KEY: "1.0.0", "ui": {"x": 1}})
        record_app_version(base)
        data = load_yaml_mapping(config_path)
        assert data[APP_VERSION_KEY] == "1.0.0"
        assert data["ui"] == {"x": 1}


class TestOverwriteUpgradeSimulation:
    """覆盖升级模拟：新发布包不含 data/，解压（复制程序文件）后用户数据原样。"""

    def test_user_data_survives_overwrite_upgrade(self) -> None:
        base = _make_base("upgrade_sim")
        paths = StoragePaths(base)

        # 1) 旧版安装：用户改过 MCP 配置 / 插件启停 / API 配置，有聊天历史
        ensure_default_configs(base)
        user_mcp = "enabled: false\nservers: {}\n"
        paths.mcp_config().write_text(user_mcp, encoding="utf-8")
        user_plugins = "- id: playwright_browser\n  enabled: false\n"
        paths.plugins_config().write_text(user_plugins, encoding="utf-8")
        save_yaml_mapping(paths.api_config(), {"llm": {"model": "user-model"}})
        history = paths.chat_history_for("sakura")
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text('{"content": "珍贵的聊天记录"}\n', encoding="utf-8")
        (base / "VERSION").write_text("0.9.7\n", encoding="utf-8")
        record_app_version(base)

        # 2) 模拟覆盖解压新包：程序文件被替换，data/ 不在包里因此原样保留
        (base / "main.py").write_text("# new version code", encoding="utf-8")
        (base / "VERSION").write_text("1.0.0\n", encoding="utf-8")

        # 3) 新版启动序列：生成缺省配置（已存在则跳过）→ 记录版本
        created = ensure_default_configs(base)
        previous, current = record_app_version(base)

        # 4) 断言：用户的一切原样，升级被正确检测
        assert created == []
        assert paths.mcp_config().read_text(encoding="utf-8") == user_mcp
        assert paths.plugins_config().read_text(encoding="utf-8") == user_plugins
        assert load_yaml_mapping(paths.api_config())["llm"]["model"] == "user-model"
        assert "珍贵的聊天记录" in history.read_text(encoding="utf-8")
        assert (previous, current) == ("0.9.7", "1.0.0")
