"""app/core/instance.py — 单实例锁。

多个 Sakura 实例并发运行会同时写聊天历史 JSONL、配置 YAML，并争抢
qdrant 记忆库的内部锁，造成数据损坏或记忆库不可用，因此启动时强制单实例。

基于 QLockFile：
- 锁文件内记录 PID/主机/应用名；持有进程已不存在（崩溃残留）时
  QLockFile 自动判定为 stale 并允许接管，无需用户手动删锁
- 锁对象存活期间持有锁，进程退出（含异常退出后的 stale 判定）即释放
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile

from app.core.debug_log import debug_log
from app.storage.paths import StoragePaths

# tryLock 等待时长：拿不到锁说明确有活动实例，无需久等
_LOCK_TRY_TIMEOUT_MS = 100


class SingleInstanceGuard:
    """进程级单实例锁；acquire 成功后需保持对象存活到进程结束。"""

    def __init__(self, base_dir: Path) -> None:
        lock_path = StoragePaths(base_dir).instance_lock()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = lock_path
        self._lock = QLockFile(str(lock_path))

    def acquire(self) -> bool:
        """尝试获取锁；失败返回 False（通常表示已有实例在运行）。"""
        acquired = self._lock.tryLock(_LOCK_TRY_TIMEOUT_MS)
        if acquired:
            debug_log("Instance", "单实例锁已获取", {"path": str(self._lock_path)})
            return True
        error = self._lock.error()
        holder = self._holder_info()
        debug_log(
            "Instance",
            "单实例锁获取失败",
            {"path": str(self._lock_path), "error": str(error), "holder": holder},
        )
        return False

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()
            debug_log("Instance", "单实例锁已释放", {"path": str(self._lock_path)})

    def _holder_info(self) -> dict:
        """读取当前持锁方信息，用于日志与用户提示。

        PySide6 的 getLockInfo() 返回 (pid, hostname, appname) 三元组，
        读取失败时抛异常或返回空值，这里统一兜底为空字典。
        """
        try:
            pid, hostname, appname = self._lock.getLockInfo()
        except (TypeError, ValueError):
            return {}
        if not pid:
            return {}
        return {"pid": int(pid), "hostname": hostname, "appname": appname}

    def holder_description(self) -> str:
        """生成用户可读的持锁方描述。"""
        info = self._holder_info()
        if not info:
            return "另一个 Sakura 实例"
        return f"另一个 Sakura 实例（进程 {info.get('pid', '?')}）"
