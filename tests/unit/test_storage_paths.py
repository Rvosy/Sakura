"""tests/unit/test_storage_paths.py — StoragePaths 与文件名净化测试。

覆盖：
- sanitize_file_stem 对现有合法 ID 的恒等性（升级兼容的硬约束）
- 非法字符 / Windows 保留名 / 空串 / 超长 ID 的防御
- StoragePaths 路径映射快照（防止重构改变既有数据文件位置）
- ensure_dirs 创建全部目录
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.storage.paths import StoragePaths, sanitize_file_stem


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_storage_paths"


def _make_test_dir(name: str) -> Path:
    """创建继承仓库 ACL 的唯一测试目录，避免 tempfile 在 Windows 沙箱中丢权限。"""
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


class TestSanitizeFileStem:
    """sanitize_file_stem 净化规则"""

    @pytest.mark.parametrize(
        "stem",
        [
            "sakura",
            "sakura1",
            "Katan",
            # 尾点 ID 必须恒等输出：拼接 .jsonl 后文件名合法，
            # 改动会破坏现网 "N.A.V.I..jsonl" 等既有历史文件的映射
            "N.A.V.I.",
            "N.A.V.I",
            "角色-中文_01",
            "default",
        ],
    )
    def test_legal_ids_are_identity(self, stem: str) -> None:
        assert sanitize_file_stem(stem) == stem

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a/b", "a_b"),
            ("a\\b", "a_b"),
            ("a:b", "a_b"),
            ('a"b', "a_b"),
            ("a<b>c", "a_b_c"),
            ("a|b?c*d", "a_b_c_d"),
            ("a\x00b\x1fc", "a_b_c"),
        ],
    )
    def test_invalid_chars_replaced(self, raw: str, expected: str) -> None:
        assert sanitize_file_stem(raw) == expected

    @pytest.mark.parametrize("name", ["CON", "con", "Nul", "COM1", "LPT9", "PRN"])
    def test_windows_reserved_names_prefixed(self, name: str) -> None:
        sanitized = sanitize_file_stem(name)
        assert sanitized == f"_{name}"

    def test_reserved_name_with_extension_prefixed(self) -> None:
        # Windows 把 "CON.txt" 同样视为设备名
        assert sanitize_file_stem("CON.backup") == "_CON.backup"

    @pytest.mark.parametrize("raw", ["", "   ", "\t"])
    def test_blank_becomes_underscore(self, raw: str) -> None:
        assert sanitize_file_stem(raw) == "_"

    def test_long_id_truncated_with_hash(self) -> None:
        long_a = "a" * 300
        long_b = "a" * 299 + "b"
        out_a = sanitize_file_stem(long_a)
        out_b = sanitize_file_stem(long_b)
        # 截断后仍需可区分，且总长可控（80 截断 + "-" + 8 位哈希）
        assert out_a != out_b
        assert len(out_a) <= 89
        assert len(out_b) <= 89

    def test_long_id_is_deterministic(self) -> None:
        long_id = "角" * 200
        assert sanitize_file_stem(long_id) == sanitize_file_stem(long_id)


class TestStoragePathsSnapshot:
    """路径映射快照：这些断言锁定既有数据文件位置，重构不得改变"""

    def setup_method(self) -> None:
        self.base = Path("D:/fake_base") if Path("D:/").exists() else Path("/fake_base")
        self.paths = StoragePaths(self.base)
        self.data = self.base / "data"

    def test_existing_mappings_unchanged(self) -> None:
        assert self.paths.config_dir == self.data / "config"
        assert self.paths.api_config() == self.data / "config" / "api.yaml"
        assert self.paths.system_config() == self.data / "config" / "system_config.yaml"
        assert self.paths.characters_config() == self.data / "config" / "characters.yaml"
        assert self.paths.mcp_config() == self.data / "config" / "mcp.yaml"
        assert self.paths.plugins_config() == self.data / "config" / "plugins.yaml"
        assert self.paths.chat_history_for("sakura") == self.data / "chat_history" / "sakura.jsonl"
        assert self.paths.legacy_chat_history() == self.data / "chat_history.jsonl"
        assert self.paths.memory_store() == self.data / "memory.json"
        assert self.paths.memory_curation_state() == self.data / "memory_curation_state.json"
        assert self.paths.reminders_store() == self.data / "reminders.json"
        assert self.paths.tasks_store() == self.data / "tasks.json"
        assert self.paths.notes_dir == self.data / "notes"

    def test_trailing_dot_character_id_mapping_unchanged(self) -> None:
        # 现网存在 "N.A.V.I." 形态角色，历史文件为 N.A.V.I..jsonl，映射不得改变
        assert (
            self.paths.chat_history_for("N.A.V.I.")
            == self.data / "chat_history" / "N.A.V.I..jsonl"
        )
        assert (
            self.paths.visual_observations_for("N.A.V.I.")
            == self.data / "visual_observations" / "N.A.V.I..jsonl"
        )
        assert (
            self.paths.runtime_events_for("N.A.V.I.")
            == self.data / "runtime_events" / "N.A.V.I..jsonl"
        )

    def test_new_directories(self) -> None:
        assert self.paths.cache_dir == self.data / "cache"
        assert self.paths.tts_cache_dir == self.data / "cache" / "tts"
        assert self.paths.logs_dir == self.data / "logs"
        assert self.paths.runtime_log_file() == self.data / "logs" / "sakura-runtime.log"
        assert self.paths.tts_bundles_dir == self.data / "tts_bundles"
        assert (
            self.paths.tts_bundles_installed_dir == self.data / "tts_bundles" / "installed"
        )
        assert (
            self.paths.tts_bundles_downloads_dir == self.data / "tts_bundles" / "downloads"
        )
        assert self.paths.plugins_data_dir == self.data / "plugins"
        assert self.paths.migration_backup_dir == self.data / "migration_backup"

    def test_tts_bundle_paths(self) -> None:
        assert (
            self.paths.tts_bundle_installed_for("gpt_sovits_windows")
            == self.data / "tts_bundles" / "installed" / "gpt_sovits_windows"
        )
        assert (
            self.paths.tts_bundle_onnx_for("sakura")
            == self.data / "tts_bundles" / "onnx" / "sakura"
        )

    def test_tts_service_log_normalizes_provider(self) -> None:
        # 与旧 tts._local_tts_service_log_path 的规则保持一致：小写 + 非法段折叠为 "-"
        assert (
            self.paths.tts_service_log("custom GPT-SoVITS")
            == self.data / "logs" / "custom-gpt-sovits-service.log"
        )
        assert self.paths.tts_service_log("  ") == self.data / "logs" / "tts-service.log"

    def test_plugin_data_for_sanitizes(self) -> None:
        assert (
            self.paths.plugin_data_for("my/évil:plugin")
            == self.data / "plugins" / "my_évil_plugin"
        )


class TestEnsureDirs:
    def test_creates_all_directories(self) -> None:
        base = _make_test_dir("ensure_dirs")
        paths = StoragePaths(base)
        paths.ensure_dirs()
        for d in [
            paths.config_dir,
            paths.chat_history_dir,
            paths.runtime_events_dir,
            paths.visual_observations_dir,
            paths.memory_dir,
            paths.notes_dir,
            paths.tts_cache_dir,
            paths.logs_dir,
        ]:
            assert d.is_dir()

    def test_idempotent(self) -> None:
        base = _make_test_dir("ensure_dirs_idem")
        paths = StoragePaths(base)
        paths.ensure_dirs()
        paths.ensure_dirs()
        assert paths.config_dir.is_dir()


class TestLongPath:
    """Windows 长路径防御：超长 ID 截断后整体路径长度可控"""

    def test_long_character_id_path_stays_bounded(self) -> None:
        base = _make_test_dir("long_path")
        paths = StoragePaths(base)
        long_id = "very-long-character-identifier-" * 12  # 384 字符
        target = paths.chat_history_for(long_id)
        # 主干被压到 89 字符以内，目录部分不超过 base + 固定段
        assert len(target.name) <= 89 + len(".jsonl")
        # 实际可创建（驱动器根附近的 base 下不会触发 MAX_PATH）
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        assert target.read_text(encoding="utf-8") == "ok"
