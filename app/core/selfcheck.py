"""app/core/selfcheck.py — 启动权限与环境自检。

启动早期运行，提前发现"数据目录不可写、配置无法保存、缓存目录异常"
这类会让用户在使用中途才撞上的环境问题：
- 唯一硬失败（fatal）：数据目录不可写——所有持久化都依赖它，继续运行只会静默丢数据
- 其余全部降级为警告（warning）：写入结构化日志，不阻断启动

自检本身绝不能让启动崩溃：每项检查都包裹异常，检查器失败按警告记录。
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.debug_log import debug_log
from app.storage.paths import StoragePaths

SEVERITY_FATAL = "fatal"
SEVERITY_WARNING = "warning"

# 磁盘剩余空间低于此值时告警（TTS 模型/日志/记忆库都需要空间）
_LOW_DISK_THRESHOLD_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class SelfCheckIssue:
    """单条自检问题；message 面向用户，data 面向日志定位。"""

    key: str
    severity: str
    message: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SelfCheckReport:
    issues: tuple[SelfCheckIssue, ...] = ()

    @property
    def fatal_issues(self) -> tuple[SelfCheckIssue, ...]:
        return tuple(i for i in self.issues if i.severity == SEVERITY_FATAL)

    @property
    def warning_issues(self) -> tuple[SelfCheckIssue, ...]:
        return tuple(i for i in self.issues if i.severity == SEVERITY_WARNING)

    @property
    def ok(self) -> bool:
        return not self.issues

    def fatal_message(self) -> str:
        """汇总 fatal 问题为用户可读的多行文案。"""
        return "\n".join(i.message for i in self.fatal_issues)


def run_startup_self_check(base_dir: Path) -> SelfCheckReport:
    """执行启动自检并把结果写入结构化日志，返回报告。"""

    paths = StoragePaths(base_dir)
    issues: list[SelfCheckIssue] = []

    issues.extend(_check_dir_writable(paths.base_dir / "data", "data_dir", fatal=True))
    issues.extend(_check_dir_writable(paths.config_dir, "config_dir", fatal=False))
    issues.extend(_check_dir_writable(paths.tts_cache_dir, "tts_cache_dir", fatal=False))
    issues.extend(_check_dir_writable(paths.logs_dir, "logs_dir", fatal=False))
    issues.extend(_check_config_files_accessible(paths))
    issues.extend(_check_stale_qdrant_lock(paths))
    issues.extend(_check_disk_space(paths.base_dir))

    report = SelfCheckReport(issues=tuple(issues))
    debug_log(
        "SelfCheck",
        "启动自检完成",
        {
            "fatal": [i.key for i in report.fatal_issues],
            "warnings": [i.key for i in report.warning_issues],
        },
    )
    for issue in report.issues:
        debug_log(
            "SelfCheck",
            f"selfcheck.{issue.key}",
            {"severity": issue.severity, "message": issue.message, **issue.data},
        )
    return report


def _check_dir_writable(directory: Path, key: str, *, fatal: bool) -> list[SelfCheckIssue]:
    """检查目录可创建且可写（通过真实写入探针文件验证，而非只看属性）。"""
    severity = SEVERITY_FATAL if fatal else SEVERITY_WARNING
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".sakura_probe_{uuid.uuid4().hex}.tmp"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [
            SelfCheckIssue(
                key=f"{key}_not_writable",
                severity=severity,
                message=(
                    f"目录无法写入：{directory}\n"
                    "请检查 Sakura 所在目录的权限，或将 Sakura 移动到有写入权限的位置"
                    "（例如不要放在 C:\\Program Files 或需要管理员权限的目录下）。"
                ),
                data={"path": str(directory), "error": str(exc)},
            )
        ]
    return []


def _check_config_files_accessible(paths: StoragePaths) -> list[SelfCheckIssue]:
    """已存在的配置文件必须可读写；不存在的属正常（首次运行）。"""
    issues: list[SelfCheckIssue] = []
    for config_path in (
        paths.api_config(),
        paths.system_config(),
        paths.characters_config(),
        paths.mcp_config(),
        paths.plugins_config(),
    ):
        try:
            if not config_path.exists():
                continue
            with config_path.open("r+", encoding="utf-8"):
                pass
        except OSError as exc:
            issues.append(
                SelfCheckIssue(
                    key="config_file_not_accessible",
                    severity=SEVERITY_WARNING,
                    message=f"配置文件无法读写：{config_path.name}，设置可能无法保存。",
                    data={"path": str(config_path), "error": str(exc)},
                )
            )
    return issues


def _check_stale_qdrant_lock(paths: StoragePaths) -> list[SelfCheckIssue]:
    """报告 qdrant 残留锁。

    qdrant 锁由其内部管理，Sakura 不直接删除；单实例锁（app/core/instance.py）
    保证正常运行时不会双进程抢锁，这里只在锁存在时留痕，便于定位
    "上次异常退出后记忆库无法加载"一类问题。
    """
    try:
        lock_path = paths.qdrant_lock()
        if lock_path.exists():
            return [
                SelfCheckIssue(
                    key="qdrant_lock_present",
                    severity=SEVERITY_WARNING,
                    message="检测到记忆库锁文件残留（上次可能未正常退出）。",
                    data={"path": str(lock_path)},
                )
            ]
    except OSError:
        pass
    return []


def _check_disk_space(base_dir: Path) -> list[SelfCheckIssue]:
    try:
        usage = shutil.disk_usage(base_dir)
    except OSError as exc:
        return [
            SelfCheckIssue(
                key="disk_usage_unavailable",
                severity=SEVERITY_WARNING,
                message="无法读取磁盘剩余空间。",
                data={"path": str(base_dir), "error": str(exc)},
            )
        ]
    if usage.free < _LOW_DISK_THRESHOLD_BYTES:
        return [
            SelfCheckIssue(
                key="disk_space_low",
                severity=SEVERITY_WARNING,
                message=(
                    f"磁盘剩余空间不足（{usage.free // (1024 * 1024)} MB），"
                    "TTS 缓存、日志与记忆库可能无法正常写入。"
                ),
                data={"free_bytes": usage.free, "total_bytes": usage.total},
            )
        ]
    return []
