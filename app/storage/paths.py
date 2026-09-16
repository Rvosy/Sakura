"""Runtime v2 user-owned storage paths.

Only mutable user data belongs here. Distribution resources are resolved by
``DistributionPaths`` and must never be written through this class.
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


def user_facing_path(value: str | Path) -> str:
    """Return a normal Windows spelling for a path displayed or persisted for users.

    ``Path.resolve()`` on Windows may return a Win32 verbatim path (``\\\\?\\``).
    That prefix is an implementation detail for filesystem APIs: it is confusing in
    the settings UI and should not become the spelling saved in user configuration.
    POSIX paths are returned unchanged.
    """

    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def sanitize_file_stem(stem: str) -> str:
    """把任意标识符（角色 ID、插件 ID 等）净化为安全的文件名主干。

    合法 ID 必须恒等输出，以保持当前 ID 与存储文件的稳定映射。
    因此只处理确定非法/危险的形态：
    - 非法字符与控制字符 → "_"
    - Windows 保留设备名（CON/NUL/COM1 等，含 "CON.xxx" 形态）→ 前缀 "_"
    - 空白串 → "_"
    - 超长 → 截断 + 内容短哈希，避免不同长 ID 截断后撞名
    注意：不处理尾部点/空格，拼接扩展名后文件名仍然合法。
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


def sanitize_directory_component(component: str) -> str:
    """Return a deterministic identifier that is safe as a directory name.

    ``sanitize_file_stem`` intentionally preserves trailing dots and spaces
    because existing callers append a file extension.  A directory has no
    suffix to make those characters addressable on Windows, so new
    directory-backed stores must additionally encode that edge case.  The
    hash prevents ``character`` and ``character.`` from sharing a directory.
    """
    original = str(component)
    cleaned = sanitize_file_stem(original)
    trimmed = cleaned.rstrip(" .")
    if trimmed == cleaned:
        return cleaned
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
    prefix = trimmed or "_"
    return f"{prefix[:_MAX_STEM_LENGTH]}-{digest}"


class StoragePaths:
    """统一生成 Sakura 的存储路径。"""

    def __init__(self, user_root: Path) -> None:
        self.user_root = Path(user_root)
        self._data = self.user_root / "data"

    # ---- 配置 ----
    @property
    def config_dir(self) -> Path:
        return self.user_root / "config"

    def storage_config(self) -> Path:
        return self.config_dir / "storage.json"

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

    @property
    def user_plugins_dir(self) -> Path:
        """User-installed plugin code, separate from plugin-owned runtime data."""

        return self.user_root / "plugins" / "user"

    @property
    def characters_dir(self) -> Path:
        return self.user_root / "characters"

    # ---- 聊天历史 ----
    @property
    def chat_history_dir(self) -> Path:
        return self._data / "chat_history"

    def chat_history_for(self, character_id: str) -> Path:
        return self.chat_history_dir / f"{sanitize_file_stem(character_id)}.jsonl"

    def timeline_database(self) -> Path:
        return self.chat_history_dir / "timeline.sqlite3"

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

    @property
    def runtime_v2_tts_cache_dir(self) -> Path:
        return self.tts_cache_dir / "runtime-v2"

    def runtime_v2_tts_generation_dir(self, generation_id: str) -> Path:
        return self.runtime_v2_tts_cache_dir / sanitize_file_stem(generation_id)

    @property
    def plugin_artifacts_cache_dir(self) -> Path:
        return self.cache_dir / "plugin-artifacts"

    def plugin_artifacts_generation_dir(self, generation_id: str) -> Path:
        return self.plugin_artifacts_cache_dir / sanitize_directory_component(generation_id)

    # ---- 持久语音 ----
    @property
    def voice_recordings_dir(self) -> Path:
        return self._data / "voice" / "recordings"

    def voice_recordings_for(self, character_id: str) -> Path:
        return self.voice_recordings_dir / sanitize_directory_component(character_id)

    # ---- 日志 ----
    @property
    def logs_dir(self) -> Path:
        return self._data / "logs"

    def runtime_log_file(self) -> Path:
        return self.logs_dir / "sakura-runtime.log"

    def crash_log_file(self) -> Path:
        # faulthandler/未捕获异常的崩溃留痕；原生段错误不会进 runtime 日志,单列一份。
        return self.logs_dir / "sakura-crash.log"

    # ---- TTS 整合包 ----
    @property
    def tts_bundles_dir(self) -> Path:
        from app.storage.tts_storage import TtsStorage

        return TtsStorage(self.user_root).snapshot(create_default=False).tts_root

    @property
    def tts_bundles_installed_dir(self) -> Path:
        return self.tts_bundles_dir

    def tts_bundle_installed_for(self, bundle_key: str) -> Path:
        return self.tts_bundles_installed_dir / sanitize_file_stem(bundle_key)

    @property
    def tts_bundles_downloads_dir(self) -> Path:
        return self.tts_bundles_dir / "_downloads"

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

    @property
    def plugin_dependency_roots_dir(self) -> Path:
        return self._data / "plugin-runtime" / "dependencies"

    def plugin_dependency_root_for(self, plugin_id: str) -> Path:
        return self.plugin_dependency_roots_dir / sanitize_directory_component(plugin_id)

    @property
    def uv_dir(self) -> Path:
        return self._data / "uv"

    @property
    def uv_cache_dir(self) -> Path:
        return self.uv_dir / "cache"

    @property
    def uv_tool_dir(self) -> Path:
        return self.uv_dir / "tools"

    @property
    def uv_tool_bin_dir(self) -> Path:
        return self.uv_dir / "bin"

    # ---- 单实例锁 ----
    def instance_lock(self) -> Path:
        return self._data / "sakura.lock"

    # ---- 辅助 ----
    def ensure_dirs(self) -> None:
        """确保所有存储目录存在。"""
        from app.storage.tts_storage import TtsStorage

        # A disconnected custom TTS volume is an explicit degraded state.  It
        # must not block the rest of the user layout or create a second TTS
        # installation under the default path.
        TtsStorage(self.user_root).snapshot(create_default=True)
        for d in [
            self.config_dir,
            self.characters_dir,
            self.chat_history_dir,
            self.runtime_events_dir,
            self.visual_observations_dir,
            self.notes_dir,
            self.tts_cache_dir,
            self.voice_recordings_dir,
            self.logs_dir,
            self.user_plugins_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
