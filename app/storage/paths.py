"""app/storage/paths.py — 统一存储路径管理。

所有 data/ 下的路径由本模块统一生成，避免各处手写 base_dir / "data" / ...。
涉及"标识符拼文件名"的路径一律经过 sanitize_file_stem 防御非法形态。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Windows 保留设备名：以这些名字开头并紧跟扩展名的文件同样不可用，统一防御
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Windows 文件名非法字符 + 控制字符（其余平台一并防御，保证跨平台一致）
_INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 文件名主干最大长度：为扩展名、目录前缀和 Windows MAX_PATH 留余量
_MAX_STEM_LENGTH = 80


def sanitize_file_stem(stem: str) -> str:
    """把任意标识符（角色 ID、插件 ID 等）净化为安全的文件名主干。

    兼容性约束：对现网已存在的合法 ID 必须恒等输出，否则会改变历史数据
    文件的映射、表现为"升级后数据丢失"。因此只处理确定非法/危险的形态：
    - 非法字符与控制字符 → "_"
    - Windows 保留设备名（CON/NUL/COM1 等，含 "CON.xxx" 形态）→ 前缀 "_"
    - 空白串 → "_"
    - 超长 → 截断 + 内容短哈希，避免不同长 ID 截断后撞名
    注意：不处理尾部点/空格——拼接扩展名后文件名合法，强行去除反而会
    破坏 "xxx." 形态 ID 与既有数据文件的对应关系。
    """
    cleaned = _INVALID_FILE_CHARS.sub("_", str(stem))
    if not cleaned.strip():
        return "_"
    head = cleaned.split(".", 1)[0].strip().upper()
    if head in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > _MAX_STEM_LENGTH:
        digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[:_MAX_STEM_LENGTH]}-{digest}"
    return cleaned


class StoragePaths:
    """统一生成 Sakura 的存储路径。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._data = self.base_dir / "data"

    # ---- 配置 ----
    @property
    def config_dir(self) -> Path:
        return self._data / "config"

    def api_config(self) -> Path:
        return self.config_dir / "api.yaml"

    def system_config(self) -> Path:
        return self.config_dir / "system_config.yaml"

    def characters_config(self) -> Path:
        return self.config_dir / "characters.yaml"

    def mcp_config(self) -> Path:
        return self.config_dir / "mcp.yaml"

    def plugins_config(self) -> Path:
        return self.config_dir / "plugins.yaml"

    # ---- 聊天历史 ----
    @property
    def chat_history_dir(self) -> Path:
        return self._data / "chat_history"

    def chat_history_for(self, character_id: str) -> Path:
        return self.chat_history_dir / f"{sanitize_file_stem(character_id)}.jsonl"

    def legacy_chat_history(self) -> Path:
        return self._data / "chat_history.jsonl"

    # ---- 运行时事件 ----
    @property
    def runtime_events_dir(self) -> Path:
        return self._data / "runtime_events"

    def runtime_events_for(self, character_id: str) -> Path:
        return self.runtime_events_dir / f"{sanitize_file_stem(character_id)}.jsonl"

    # ---- 视觉观察 ----
    @property
    def visual_observations_dir(self) -> Path:
        return self._data / "visual_observations"

    def visual_observations_for(self, character_id: str) -> Path:
        return self.visual_observations_dir / f"{sanitize_file_stem(character_id)}.jsonl"

    # ---- 记忆 ----
    @property
    def memory_dir(self) -> Path:
        return self._data / "memory"

    def memory_store(self) -> Path:
        return self._data / "memory.json"

    def memory_core_profiles(self) -> Path:
        return self.memory_dir / "core_profiles.json"

    def memory_curation_state(self) -> Path:
        return self._data / "memory_curation_state.json"

    def screen_awareness_state(self) -> Path:
        return self._data / "screen_awareness_state.json"

    # ---- 提醒 ----
    def reminders_store(self) -> Path:
        return self._data / "reminders.json"

    # ---- 待办 ----
    def tasks_store(self) -> Path:
        return self._data / "tasks.json"

    # ---- 笔记 ----
    @property
    def notes_dir(self) -> Path:
        return self._data / "notes"

    # ---- 缓存 ----
    @property
    def cache_dir(self) -> Path:
        return self._data / "cache"

    @property
    def tts_cache_dir(self) -> Path:
        return self.cache_dir / "tts"

    # ---- 日志 ----
    @property
    def logs_dir(self) -> Path:
        return self._data / "logs"

    def runtime_log_file(self) -> Path:
        return self.logs_dir / "sakura-runtime.log"

    def crash_log_file(self) -> Path:
        # faulthandler/未捕获异常的崩溃留痕；原生段错误不会进 runtime 日志,单列一份。
        return self.logs_dir / "sakura-crash.log"

    def tts_service_log(self, provider: str) -> Path:
        safe_provider = re.sub(r"[^A-Za-z0-9_.-]+", "-", provider.strip().lower()) or "tts"
        return self.logs_dir / f"{safe_provider}-service.log"

    # ---- TTS 整合包 ----
    @property
    def tts_bundles_dir(self) -> Path:
        return self._data / "tts_bundles"

    @property
    def tts_bundles_installed_dir(self) -> Path:
        return self.tts_bundles_dir / "installed"

    def tts_bundle_installed_for(self, bundle_key: str) -> Path:
        return self.tts_bundles_installed_dir / sanitize_file_stem(bundle_key)

    @property
    def tts_bundles_downloads_dir(self) -> Path:
        return self.tts_bundles_dir / "downloads"

    def tts_bundle_onnx_for(self, character_id: str) -> Path:
        return self.tts_bundles_dir / "onnx" / sanitize_file_stem(character_id)

    # ---- 角色工坊 ----
    @property
    def character_studio_dir(self) -> Path:
        return self._data / "character_studio"

    @property
    def character_studio_drafts_dir(self) -> Path:
        return self.character_studio_dir / "drafts"

    @property
    def character_studio_backups_dir(self) -> Path:
        return self.character_studio_dir / "backups"

    # ---- 插件数据 ----
    @property
    def plugins_data_dir(self) -> Path:
        return self._data / "plugins"

    def plugin_data_for(self, plugin_id: str) -> Path:
        return self.plugins_data_dir / sanitize_file_stem(plugin_id)

    # ---- 迁移备份 ----
    @property
    def migration_backup_dir(self) -> Path:
        return self._data / "migration_backup"

    # ---- 单实例锁 ----
    def instance_lock(self) -> Path:
        return self._data / "sakura.lock"

    def qdrant_lock(self) -> Path:
        """qdrant 内部锁文件位置；仅用于自检报告残留，不由 Sakura 管理。"""
        return self.memory_dir / "qdrant" / ".lock"

    # ---- 辅助 ----
    def ensure_dirs(self) -> None:
        """确保所有存储目录存在。"""
        for d in [
            self.config_dir,
            self.chat_history_dir,
            self.runtime_events_dir,
            self.visual_observations_dir,
            self.memory_dir,
            self.notes_dir,
            self.tts_cache_dir,
            self.logs_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
