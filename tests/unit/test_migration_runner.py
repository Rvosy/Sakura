"""tests/unit/test_migration_runner.py — 版本化迁移框架测试。

覆盖：
- config_version 读写与待办步骤计算
- 全链路迁移（构造真实旧版 data 形态回放）
- 幂等性（连跑多次结果一致、不重复迁移）
- 失败注入（失败步骤版本号不前进、原文件未动、后续步骤不执行）
- v0→v1：.env 导入 + 归档、legacy 聊天历史拆分
- v1→v2：尾点变体合并的安全边界（唯一注册角色才合并）
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.config.migration_runner import (
    CONFIG_VERSION_KEY,
    CURRENT_CONFIG_VERSION,
    MigrationContext,
    MigrationReport,
    MigrationResult,
    MigrationRunner,
    MigrationStep,
)
from app.config.yaml_config import load_yaml_mapping
from app.storage.paths import StoragePaths


def test_data_migration_failure_message_keeps_step_error() -> None:
    from main import _format_data_migration_failure

    report = MigrationReport(
        from_version=1,
        to_version=1,
        results=(MigrationResult(name="v1_to_v2", status="failed", error="WinError 5"),),
    )

    message = _format_data_migration_failure(report)

    assert "处理建议" in message
    assert "data/logs/sakura-runtime.log" in message
    assert "诊断信息（截图时请保留）：\nv1_to_v2: WinError 5" in message


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_migration_runner"


def _make_base(name: str) -> Path:
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _write_character(base: Path, character_id: str, dir_name: str | None = None) -> None:
    # 目录名与 id 分离：Windows 会静默剥离目录尾点，"N.A.V.I." 和 "N.A.V.I"
    # 目录会撞到一起；注册 id 实际来自 character.json 内的 id 字段
    manifest_dir = base / "characters" / (dir_name or character_id.rstrip(". ") or "char")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "character.json").write_text(
        json.dumps({"id": character_id, "display_name": character_id}),
        encoding="utf-8",
    )


def _jsonl_line(timestamp: str, text: str) -> str:
    return json.dumps({"timestamp": timestamp, "content": text}, ensure_ascii=False) + "\n"


class TestRunnerProtocol:
    def test_fresh_dir_version_zero_and_pending_all(self) -> None:
        base = _make_base("fresh")
        runner = MigrationRunner(base)
        assert runner.current_version() == 0
        assert [s.version for s in runner.pending()] == [1, 2, 3]

    def test_run_advances_version_to_current(self) -> None:
        base = _make_base("advance")
        report = MigrationRunner(base).run()
        assert not report.failed
        assert report.to_version == CURRENT_CONFIG_VERSION
        data = load_yaml_mapping(StoragePaths(base).system_config())
        assert data[CONFIG_VERSION_KEY] == CURRENT_CONFIG_VERSION

    def test_run_is_idempotent(self) -> None:
        base = _make_base("idem")
        MigrationRunner(base).run()
        report = MigrationRunner(base).run()
        assert not report.results  # 第二次无待办
        assert MigrationRunner(base).current_version() == CURRENT_CONFIG_VERSION

    def test_failed_step_stops_and_keeps_version(self) -> None:
        base = _make_base("fail")

        def boom(_context: MigrationContext) -> None:
            raise RuntimeError("simulated failure")

        executed: list[str] = []

        def later(_context: MigrationContext) -> None:
            executed.append("later")

        steps = [
            MigrationStep(version=1, name="boom", description="", apply=boom),
            MigrationStep(version=2, name="later", description="", apply=later),
        ]
        runner = MigrationRunner(base, steps=steps)
        report = runner.run()
        assert report.failed
        assert report.results[0].status == "failed"
        assert executed == []  # 失败后不执行后续步骤
        assert runner.current_version() == 0  # 版本号不前进

    def test_retry_after_failure_succeeds(self) -> None:
        base = _make_base("retry")
        attempts: list[int] = []

        def flaky(_context: MigrationContext) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")

        steps = [MigrationStep(version=1, name="flaky", description="", apply=flaky)]
        assert MigrationRunner(base, steps=steps).run().failed
        assert not MigrationRunner(base, steps=steps).run().failed
        assert MigrationRunner(base, steps=steps).current_version() == 1


class TestV0ToV1:
    def test_env_imported_archived_and_backed_up(self) -> None:
        base = _make_base("env")
        (base / ".env").write_text(
            "BASE_URL=https://example.test/v1\n"
            "MODEL=test-model\n"
            "GPT_SOVITS_REF_AUDIO_PATH=ref/old.ogg\n",  # 未映射键：应记录跳过而非报错
            encoding="utf-8",
        )
        report = MigrationRunner(base).run()
        assert not report.failed
        api = load_yaml_mapping(StoragePaths(base).api_config())
        assert api["llm"]["base_url"] == "https://example.test/v1"
        assert api["llm"]["model"] == "test-model"
        # .env 归档：原文件消失，.migrated 保留
        assert not (base / ".env").exists()
        assert (base / ".env.migrated").is_file()
        # 备份目录存有 .env 快照
        backups = list(StoragePaths(base).migration_backup_dir.rglob(".env"))
        assert backups

    def test_rerun_after_env_archived_skips(self) -> None:
        base = _make_base("env_rerun")
        (base / ".env").write_text("MODEL=m1\n", encoding="utf-8")
        MigrationRunner(base).run()
        # 用户事后改了 YAML；归档后的重跑不得用旧 .env 覆盖
        from app.config.yaml_config import save_yaml_mapping

        api_path = StoragePaths(base).api_config()
        data = load_yaml_mapping(api_path)
        data["llm"]["model"] = "user-changed"
        save_yaml_mapping(api_path, data)
        MigrationRunner(base).run()
        assert load_yaml_mapping(api_path)["llm"]["model"] == "user-changed"

    def test_legacy_chat_history_split(self) -> None:
        base = _make_base("legacy_history")
        from app.config.character_loader import DEFAULT_CHARACTER_ID

        paths = StoragePaths(base)
        legacy = paths.legacy_chat_history()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(_jsonl_line("2026-01-01T00:00:00", "旧对话"), encoding="utf-8")
        report = MigrationRunner(base).run()
        assert not report.failed
        target = paths.chat_history_for(DEFAULT_CHARACTER_ID)
        assert target.is_file()
        assert "旧对话" in target.read_text(encoding="utf-8")
        assert not legacy.exists()
        assert legacy.with_name(legacy.name + ".migrated").is_file()

    def test_legacy_history_does_not_overwrite_existing_target(self) -> None:
        base = _make_base("legacy_no_overwrite")
        from app.config.character_loader import DEFAULT_CHARACTER_ID

        paths = StoragePaths(base)
        legacy = paths.legacy_chat_history()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(_jsonl_line("2026-01-01T00:00:00", "legacy"), encoding="utf-8")
        target = paths.chat_history_for(DEFAULT_CHARACTER_ID)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_jsonl_line("2026-02-01T00:00:00", "existing"), encoding="utf-8")
        MigrationRunner(base).run()
        assert "existing" in target.read_text(encoding="utf-8")
        assert "legacy" not in target.read_text(encoding="utf-8")


class TestV1ToV2:
    def test_merges_unregistered_variant_into_registered(self) -> None:
        base = _make_base("merge")
        _write_character(base, "N.A.V.I.")
        paths = StoragePaths(base)
        paths.chat_history_dir.mkdir(parents=True, exist_ok=True)
        canonical = paths.chat_history_dir / "N.A.V.I..jsonl"
        variant = paths.chat_history_dir / "N.A.V.I.jsonl"
        canonical.write_text(_jsonl_line("2026-03-01T00:00:00", "新数据"), encoding="utf-8")
        variant.write_text(_jsonl_line("2026-01-01T00:00:00", "老数据"), encoding="utf-8")

        report = MigrationRunner(base).run()
        assert not report.failed
        merged = canonical.read_text(encoding="utf-8").splitlines()
        assert len(merged) == 2
        # 时间戳归并：老数据在前
        assert "老数据" in merged[0]
        assert "新数据" in merged[1]
        assert not variant.exists()
        assert variant.with_name(variant.name + ".migrated").is_file()

    def test_variant_backups_keep_source_directories(self) -> None:
        base = _make_base("merge_backup_dirs")
        _write_character(base, "N.A.V.I.")
        paths = StoragePaths(base)
        for directory in (paths.chat_history_dir, paths.runtime_events_dir):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "N.A.V.I..jsonl").write_text(
                _jsonl_line("2026-03-01T00:00:00", f"{directory.name}-new"),
                encoding="utf-8",
            )
            (directory / "N.A.V.I.jsonl").write_text(
                _jsonl_line("2026-01-01T00:00:00", f"{directory.name}-old"),
                encoding="utf-8",
            )

        report = MigrationRunner(base).run()

        assert not report.failed
        backed_up = {
            path.relative_to(paths.migration_backup_dir).as_posix()
            for path in paths.migration_backup_dir.rglob("N.A.V.I.jsonl")
        }
        assert any("/chat_history/N.A.V.I.jsonl" in path for path in backed_up)
        assert any("/runtime_events/N.A.V.I.jsonl" in path for path in backed_up)

    def test_two_registered_characters_not_merged(self) -> None:
        base = _make_base("two_registered")
        _write_character(base, "N.A.V.I.", dir_name="navi_dot")
        _write_character(base, "N.A.V.I", dir_name="navi")
        paths = StoragePaths(base)
        paths.chat_history_dir.mkdir(parents=True, exist_ok=True)
        a = paths.chat_history_dir / "N.A.V.I..jsonl"
        b = paths.chat_history_dir / "N.A.V.I.jsonl"
        a.write_text(_jsonl_line("2026-03-01T00:00:00", "A"), encoding="utf-8")
        b.write_text(_jsonl_line("2026-01-01T00:00:00", "B"), encoding="utf-8")
        MigrationRunner(base).run()
        # 两个都是注册角色：必须原样保留
        assert a.is_file() and b.is_file()
        assert "A" in a.read_text(encoding="utf-8")
        assert "B" in b.read_text(encoding="utf-8")

    def test_orphan_variants_not_merged(self) -> None:
        base = _make_base("orphans")
        paths = StoragePaths(base)
        paths.chat_history_dir.mkdir(parents=True, exist_ok=True)
        a = paths.chat_history_dir / "ghost..jsonl"
        b = paths.chat_history_dir / "ghost.jsonl"
        a.write_text(_jsonl_line("2026-03-01T00:00:00", "A"), encoding="utf-8")
        b.write_text(_jsonl_line("2026-01-01T00:00:00", "B"), encoding="utf-8")
        MigrationRunner(base).run()
        assert a.is_file() and b.is_file()

    def test_merge_is_idempotent(self) -> None:
        base = _make_base("merge_idem")
        _write_character(base, "N.A.V.I.")
        paths = StoragePaths(base)
        paths.chat_history_dir.mkdir(parents=True, exist_ok=True)
        canonical = paths.chat_history_dir / "N.A.V.I..jsonl"
        variant = paths.chat_history_dir / "N.A.V.I.jsonl"
        canonical.write_text(_jsonl_line("2026-03-01T00:00:00", "新"), encoding="utf-8")
        variant.write_text(_jsonl_line("2026-01-01T00:00:00", "老"), encoding="utf-8")
        MigrationRunner(base).run()
        first = canonical.read_text(encoding="utf-8")
        MigrationRunner(base).run()
        assert canonical.read_text(encoding="utf-8") == first

    def test_unparseable_lines_appended_in_order(self) -> None:
        base = _make_base("unparseable")
        _write_character(base, "chara")
        paths = StoragePaths(base)
        paths.chat_history_dir.mkdir(parents=True, exist_ok=True)
        canonical = paths.chat_history_dir / "chara.jsonl"
        variant = paths.chat_history_dir / "chara..jsonl"
        canonical.write_text("not-json-line\n", encoding="utf-8")
        variant.write_text(_jsonl_line("2026-01-01T00:00:00", "ok"), encoding="utf-8")
        MigrationRunner(base).run()
        lines = canonical.read_text(encoding="utf-8").splitlines()
        # 含无法解析行：放弃排序、保序拼接（canonical 在前）
        assert lines[0] == "not-json-line"
        assert "ok" in lines[1]


class TestV2ToV3:
    def test_copies_proactive_care_to_screen_awareness(self) -> None:
        base = _make_base("screen_awareness_migration")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 2,
                "proactive_care": {
                    "enabled": True,
                    "screen_context_enabled": True,
                    "check_interval_minutes": 5,
                    "cooldown_minutes": 8,
                    "screen_context_batch_limit": 4,
                },
            },
        )

        report = MigrationRunner(base).run()

        assert not report.failed
        system = load_yaml_mapping(paths.system_config())
        assert system["screen_awareness"]["check_interval_minutes"] == 5
        assert system["screen_awareness"]["cooldown_minutes"] == 8
        assert system["proactive_care"]["screen_context_batch_limit"] == 4

    def test_existing_screen_awareness_is_not_overwritten(self) -> None:
        base = _make_base("screen_awareness_existing")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 2,
                "proactive_care": {"check_interval_minutes": 5},
                "screen_awareness": {"check_interval_minutes": 11},
            },
        )

        MigrationRunner(base).run()

        system = load_yaml_mapping(paths.system_config())
        assert system["screen_awareness"]["check_interval_minutes"] == 11


class TestFullReplay:
    def test_old_install_replay_twice(self) -> None:
        """构造完整旧版 data 形态，连跑两遍验证端到端幂等。"""
        base = _make_base("replay")
        _write_character(base, "sakura")
        (base / ".env").write_text("MODEL=replay-model\nTTS_ENABLED=true\n", encoding="utf-8")
        paths = StoragePaths(base)
        legacy = paths.legacy_chat_history()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(_jsonl_line("2026-01-01T00:00:00", "legacy"), encoding="utf-8")

        first = MigrationRunner(base).run()
        assert not first.failed
        second = MigrationRunner(base).run()
        assert not second.failed
        assert not second.results

        api = load_yaml_mapping(paths.api_config())
        assert api["llm"]["model"] == "replay-model"
        assert load_yaml_mapping(paths.system_config())[CONFIG_VERSION_KEY] == CURRENT_CONFIG_VERSION
