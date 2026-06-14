"""app/config/app_version.py — 应用版本标记与升级检测。

读取根目录 VERSION 文件（发布流程以它生成包名），与
system_config.yaml 中记录的 app_version 比对：不一致说明刚发生
覆盖升级（或降级），记录结构化日志后更新标记。

该标记让"用户是从哪个版本升上来的"在日志中可见，
是排查覆盖升级类问题（旧文件残留、配置不兼容）的第一线索。
"""

from __future__ import annotations

from pathlib import Path

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.core.debug_log import debug_log
from app.storage.paths import StoragePaths

APP_VERSION_KEY = "app_version"


def read_app_version(base_dir: Path) -> str:
    """读取 VERSION 文件第一行；缺失或不可读返回空串。"""
    try:
        text = (Path(base_dir) / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return ""
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    return first_line.lstrip("v")


def record_app_version(base_dir: Path) -> tuple[str, str]:
    """比对并更新 app_version 标记；返回 (之前记录的版本, 当前版本)。"""
    current = read_app_version(base_dir)
    if not current:
        return "", ""
    config_path = StoragePaths(base_dir).system_config()
    try:
        data = load_yaml_mapping(config_path)
    except ValueError:
        # 配置损坏时不在这里处理（自检/迁移负责），跳过版本标记
        return "", current
    previous = str(data.get(APP_VERSION_KEY, "") or "")
    if previous == current:
        return previous, current
    if previous:
        debug_log(
            "Startup",
            "app.upgraded",
            {"from_version": previous, "to_version": current},
        )
    else:
        debug_log("Startup", "app.version_recorded", {"version": current})
    data[APP_VERSION_KEY] = current
    try:
        save_yaml_mapping(config_path, data)
    except OSError as exc:
        debug_log("Startup", "app_version 标记写入失败", {"error": str(exc)})
    return previous, current
