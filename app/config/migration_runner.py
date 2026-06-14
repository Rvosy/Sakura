"""app/config/migration_runner.py — 配置与数据版本化迁移框架。

版本标记存放在 data/config/system_config.yaml 的顶层 config_version 键，
与配置同卷同生命周期；缺失视为版本 0（旧版安装或全新安装）。

执行协议（每个步骤）：
1. migration.<name>.started 日志
2. 将该步骤声明的关联文件备份到 data/migration_backup/<时间戳>_<name>/
3. 执行 apply（步骤必须幂等：中断后重跑不得损坏数据）
4. 成功后才把 config_version 推进到该步骤版本（标记后置：失败不前进，下次启动重试）
5. migration.<name>.completed / failed 日志

失败处理：当前步骤失败即停止后续步骤，原文件保持原位；
迁移失败不阻断应用启动，应用按旧数据形态继续工作。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.core.debug_log import debug_log
from app.storage.atomic import atomic_write_text, rename_with_retry
from app.storage.paths import StoragePaths

CONFIG_VERSION_KEY = "config_version"
# 当前代码期望的数据形态版本；新增迁移步骤时同步 +1
CURRENT_CONFIG_VERSION = 3


@dataclass
class MigrationContext:
    """传给迁移步骤的执行环境。"""

    base_dir: Path
    paths: StoragePaths
    backup_dir: Path

    def backup_file(self, path: Path) -> None:
        """把文件备份到本步骤目录；源不存在时忽略。

        base_dir 内文件按相对路径保留目录结构，避免不同数据目录里的同名
        文件互相覆盖；外部文件回退为按文件名备份。
        """
        source = Path(path)
        if not source.is_file():
            return
        try:
            relative_path = source.resolve().relative_to(self.base_dir.resolve())
        except (OSError, ValueError):
            relative_path = Path(source.name)
        target = self.backup_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


@dataclass(frozen=True)
class MigrationStep:
    """一个迁移步骤；apply 成功返回后 config_version 推进到 version。"""

    version: int
    name: str
    description: str
    apply: Callable[[MigrationContext], None]


@dataclass(frozen=True)
class MigrationResult:
    name: str
    status: str  # completed | failed | skipped
    error: str = ""


@dataclass(frozen=True)
class MigrationReport:
    from_version: int
    to_version: int
    results: tuple[MigrationResult, ...] = ()

    @property
    def failed(self) -> bool:
        return any(r.status == "failed" for r in self.results)


class MigrationRunner:
    def __init__(self, base_dir: Path, steps: list[MigrationStep] | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.paths = StoragePaths(self.base_dir)
        self.steps = sorted(steps if steps is not None else ALL_MIGRATIONS, key=lambda s: s.version)

    def current_version(self) -> int:
        try:
            data = load_yaml_mapping(self.paths.system_config())
        except ValueError:
            # 配置损坏时不猜版本：按 0 处理会重跑迁移，幂等步骤可承受
            return 0
        try:
            return int(data.get(CONFIG_VERSION_KEY, 0))
        except (TypeError, ValueError):
            return 0

    def pending(self) -> list[MigrationStep]:
        current = self.current_version()
        return [s for s in self.steps if s.version > current]

    def run(self) -> MigrationReport:
        """执行全部待办迁移；任一步骤失败即停止后续步骤。"""
        from_version = self.current_version()
        results: list[MigrationResult] = []
        for step in self.pending():
            debug_log("Migration", f"migration.{step.name}.started", {"to_version": step.version})
            backup_dir = (
                self.paths.migration_backup_dir
                / f"{time.strftime('%Y%m%d-%H%M%S')}_{step.name}"
            )
            context = MigrationContext(
                base_dir=self.base_dir,
                paths=self.paths,
                backup_dir=backup_dir,
            )
            try:
                step.apply(context)
                self._write_version(step.version)
            except Exception as exc:  # noqa: BLE001 - 迁移失败不允许炸掉启动
                debug_log(
                    "Migration",
                    f"migration.{step.name}.failed",
                    {"error": str(exc), "backup_dir": str(backup_dir)},
                )
                results.append(MigrationResult(name=step.name, status="failed", error=str(exc)))
                break
            debug_log(
                "Migration",
                f"migration.{step.name}.completed",
                {"version": step.version, "backup_dir": str(backup_dir)},
            )
            results.append(MigrationResult(name=step.name, status="completed"))
        return MigrationReport(
            from_version=from_version,
            to_version=self.current_version(),
            results=tuple(results),
        )

    def _write_version(self, version: int) -> None:
        data = load_yaml_mapping(self.paths.system_config())
        data[CONFIG_VERSION_KEY] = int(version)
        save_yaml_mapping(self.paths.system_config(), data)


# ---------------------------------------------------------------------------
# v0 → v1：接通 .env → YAML 迁移 + 旧版单文件聊天历史拆分
# ---------------------------------------------------------------------------

# 迁移完成后 .env 的改名目标；保留在原位便于用户回查，重跑时自动跳过
_ENV_MIGRATED_SUFFIX = ".migrated"


def _migrate_v0_to_v1(context: MigrationContext) -> None:
    _migrate_dotenv(context)
    _migrate_legacy_single_chat_history(context)


def _migrate_dotenv(context: MigrationContext) -> None:
    """把根目录残留 .env 的配置导入 YAML，并把 .env 改名归档。

    幂等性：成功后 .env 改名为 .env.migrated，重跑时文件不存在直接跳过。
    YAML 中已有的键会被 .env 值覆盖——该步骤只会在 config_version=0
    （从未迁移过）时执行一次，且执行前已备份两个 YAML。
    """
    from app.config.migrations import migrate_env_to_yaml

    env_path = context.base_dir / ".env"
    if not env_path.is_file():
        debug_log("Migration", "migration.v0_to_v1.env.skipped", {"reason": "no .env"})
        return
    context.backup_file(env_path)
    context.backup_file(context.paths.api_config())
    context.backup_file(context.paths.system_config())
    context.backup_file(context.paths.characters_config())
    result = migrate_env_to_yaml(
        env_path,
        context.paths.api_config(),
        context.paths.system_config(),
    )
    # 已知未映射键（如 GPT_SOVITS_REF_AUDIO_PATH，参考音频现由角色包接管）：
    # 显式记录跳过，不静默丢弃
    migrated = set(result.get("migrated", []))
    skipped_keys = [
        key
        for key in _parse_env_keys(env_path)
        if key not in migrated
    ]
    rename_with_retry(env_path, env_path.with_name(env_path.name + _ENV_MIGRATED_SUFFIX))
    debug_log(
        "Migration",
        "migration.v0_to_v1.env.applied",
        {"migrated": sorted(migrated), "skipped": skipped_keys, "errors": result.get("errors", [])},
    )


def _parse_env_keys(env_path: Path) -> list[str]:
    keys: list[str] = []
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            keys.append(line.partition("=")[0].strip())
    except OSError:
        return []
    return keys


def _migrate_legacy_single_chat_history(context: MigrationContext) -> None:
    """旧版单文件 data/chat_history.jsonl → data/chat_history/<默认角色>.jsonl。

    只迁默认角色、目标存在则跳过；成功后旧文件归档备份，不再每次启动判断。
    """
    from app.config.character_loader import DEFAULT_CHARACTER_ID

    legacy_path = context.paths.legacy_chat_history()
    if not legacy_path.is_file():
        return
    target = context.paths.chat_history_for(DEFAULT_CHARACTER_ID)
    context.backup_file(legacy_path)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, target)
        debug_log(
            "Migration",
            "migration.v0_to_v1.legacy_history.applied",
            {"target": str(target)},
        )
    # 归档旧文件（已备份 + 已拆分/目标已存在），避免每次启动重复判断
    rename_with_retry(legacy_path, legacy_path.with_name(legacy_path.name + _ENV_MIGRATED_SUFFIX))


# ---------------------------------------------------------------------------
# v1 → v2：合并角色数据文件的歧义变体（如 "N.A.V.I." 与 "N.A.V.I"）
# ---------------------------------------------------------------------------


def _migrate_v1_to_v2(context: MigrationContext) -> None:
    """合并按角色拆分的 JSONL 中"语义同名"的变体文件。

    历史上角色 ID 变更（尾点差异）会产生两份数据文件（实测存在
    N.A.V.I..jsonl 与 N.A.V.I.jsonl 并存）。合并规则刻意保守：
    - 仅当两个 stem 在去除尾点后相同，且其中只有一个对应已注册角色时，
      才把未注册变体并入注册变体（按 JSONL 行时间戳归并，无法解析的行保序追加）
    - 两个都对应注册角色（真的是两个角色）或都不对应：不动，仅记日志
    """
    registered_ids = _load_registered_character_ids(context.base_dir)
    for directory in (
        context.paths.chat_history_dir,
        context.paths.runtime_events_dir,
        context.paths.visual_observations_dir,
    ):
        _merge_variant_files_in_dir(context, directory, registered_ids)


def _load_registered_character_ids(base_dir: Path) -> set[str]:
    """读取 characters/ 下注册角色 ID；读取失败返回空集合（迁移按"不动"处理）。"""
    ids: set[str] = set()
    characters_dir = base_dir / "characters"
    if not characters_dir.is_dir():
        return ids
    for manifest in characters_dir.glob("*/character.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        character_id = str(data.get("id", "")).strip()
        if character_id:
            ids.add(character_id)
    return ids


def _merge_variant_files_in_dir(
    context: MigrationContext,
    directory: Path,
    registered_ids: set[str],
) -> None:
    if not directory.is_dir():
        return
    files = sorted(directory.glob("*.jsonl"))
    by_normalized: dict[str, list[Path]] = {}
    for file in files:
        normalized = file.name[: -len(".jsonl")].rstrip(".")
        by_normalized.setdefault(normalized, []).append(file)

    for normalized, group in by_normalized.items():
        if len(group) < 2:
            continue
        stems = [f.name[: -len(".jsonl")] for f in group]
        registered = [s for s in stems if s in registered_ids]
        if len(registered) != 1:
            debug_log(
                "Migration",
                "migration.v1_to_v2.variant.skipped",
                {
                    "dir": directory.name,
                    "stems": stems,
                    "registered": registered,
                    "reason": "无法唯一确定规范角色，保持原状",
                },
            )
            continue
        canonical_stem = registered[0]
        canonical = directory / f"{canonical_stem}.jsonl"
        variants = [f for f in group if f != canonical]
        for variant in variants:
            context.backup_file(canonical)
            context.backup_file(variant)
            _merge_jsonl(variant, canonical)
            rename_with_retry(variant, variant.with_name(variant.name + _ENV_MIGRATED_SUFFIX))
            debug_log(
                "Migration",
                "migration.v1_to_v2.variant.merged",
                {"dir": directory.name, "variant": variant.name, "into": canonical.name},
            )


def _merge_jsonl(source: Path, target: Path) -> None:
    """把 source 的行并入 target：能解析出时间戳则整体按时间归并，否则保序拼接。"""
    source_lines = _read_jsonl_lines(source)
    target_lines = _read_jsonl_lines(target)
    merged = target_lines + source_lines
    timestamps = [_line_timestamp(line) for line in merged]
    if all(ts is not None for ts in timestamps):
        merged = [line for _, line in sorted(zip(timestamps, merged), key=lambda p: p[0])]
    target.parent.mkdir(parents=True, exist_ok=True)
    # 行级合并后整体重写；调用方已对 target 做过备份
    atomic_write_text(target, "".join(merged), encoding="utf-8", backup=False)


def _read_jsonl_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return [line + "\n" for line in text.splitlines() if line.strip()]


def _line_timestamp(line: str) -> str | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    value = data.get("timestamp") or data.get("time") or data.get("created_at")
    return str(value) if value else None


# ---------------------------------------------------------------------------
# v2 → v3：旧主动配置迁移为主动屏幕感知配置
# ---------------------------------------------------------------------------


def _migrate_v2_to_v3(context: MigrationContext) -> None:
    """复制旧 proactive_care 配置到 screen_awareness；旧段保留用于回滚。"""
    system_path = context.paths.system_config()
    data = load_yaml_mapping(system_path)
    if "screen_awareness" in data:
        debug_log(
            "Migration",
            "migration.v2_to_v3.screen_awareness.skipped",
            {"reason": "screen_awareness 已存在"},
        )
        return
    proactive = data.get("proactive_care")
    if not isinstance(proactive, dict):
        proactive = {}
    context.backup_file(system_path)
    data["screen_awareness"] = dict(proactive)
    save_yaml_mapping(system_path, data)
    debug_log(
        "Migration",
        "migration.v2_to_v3.screen_awareness.applied",
        {"copied_keys": sorted(data["screen_awareness"].keys())},
    )


ALL_MIGRATIONS: list[MigrationStep] = [
    MigrationStep(
        version=1,
        name="v0_to_v1",
        description=".env 配置导入 YAML；旧版单文件聊天历史拆分归档",
        apply=_migrate_v0_to_v1,
    ),
    MigrationStep(
        version=2,
        name="v1_to_v2",
        description="合并角色 JSONL 数据文件的尾点歧义变体",
        apply=_migrate_v1_to_v2,
    ),
    MigrationStep(
        version=3,
        name="v2_to_v3",
        description="旧主动配置迁移为主动屏幕感知配置",
        apply=_migrate_v2_to_v3,
    ),
]
