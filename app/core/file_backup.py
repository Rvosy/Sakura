"""app/core/file_backup.py — 系统文件备份、完整性校验与自动修复。

在首次启动时为除 characters/、runtime/ 以外的所有文件创建带 SHA-256 哈希的基线备份；
在每次启动前对比当前文件与基线清单的差异，若发现文件被修改或丢失，
则提示用户确认后自动执行修复。

备份根目录默认位于项目目录之外的同盘隐藏目录（见 _get_default_backup_root），
避免随项目目录整体删除而被连带误删。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.runtime_log import log_event

# ---------------------------------------------------------------------------
# 排除规则：这些目录下的文件不参与备份 / 对比
# ---------------------------------------------------------------------------
_EXCLUDED_TOP_LEVEL_DIRS = frozenset({
    "characters",
    "__pycache__",
    "runtime",
})

_EXCLUDED_PATH_PREFIXES = (
    "data/logs/",
    "data/runtime_events/",
    "data/cache/",
    "data/chat_history/",
    "data/memory/",
    "data/visual_observations/",
    "data/notes/",
    "data/tts_bundles/",
    "data/plugins/",
    "data/character_studio/",
    "data/migration_backup/",
    "data/system_backup/",
    "data/chat_history.jsonl",
    "data/memory_curation_state.json",
    "data/screen_awareness_state.json",
    "data/reminders.json",
    "data/tasks.json",
    "data/sakura.lock",
)

_EXCLUDED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".crash",
)

_MANIFEST_FILE = "backup_manifest.json"
_BACKUP_DATA_DIR = "backup_data"

# ---------------------------------------------------------------------------
# 性能优化常量
# ---------------------------------------------------------------------------
# 目录遍历结果全局缓存：60 秒内复用，避免重复 rglob 全量扫描
_SCAN_CACHE_TTL = 60.0
_SCAN_CACHE: dict[Path, tuple[float, list[Path]]] = {}
_SCAN_CACHE_LOCK = threading.Lock()

# 并发哈希工作线程数：多核并行计算 SHA-256
_HASH_WORKERS = min(32, (os.cpu_count() or 4) + 4)

# ACL 保护（Windows）：拒绝 Everyone 删除自身及子项，防止误删。
# DE=删除，DC=删除子项（防借父目录 DELETE_CHILD 绕过），高级权限以
# 逗号分隔；(OI)(CI) 使 ACE 继承到全部子对象。经实测仅拒绝删除，
# 读写与列举不受影响。
_ACL_DENY_DELETE = "Everyone:(OI)(CI)(DE,DC)"
# 解除保护时用"仅继承"版本替换：父级 ACE 不消失，子对象已继承的
# deny 不会被系统回收，深度保护可跨启动持久；但根目录本身恢复可删。
_ACL_DENY_DELETE_INHERIT_ONLY = "Everyone:(IO)(OI)(CI)(DE,DC)"


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _should_exclude(relative_path: str) -> bool:
    normalized = _normalize_rel(relative_path)

    for prefix in _EXCLUDED_PATH_PREFIXES:
        if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
            return True

    first_segment = normalized.split("/", 1)[0]
    if first_segment in _EXCLUDED_TOP_LEVEL_DIRS:
        return True

    for suffix in _EXCLUDED_SUFFIXES:
        if normalized.endswith(suffix):
            return True

    return False


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _get_default_backup_root(base_dir: Path) -> Path:
    """返回同盘符、项目目录之外的隐藏备份根目录。

    形如：D:\\.sakura_system_backup\\{project_name_hash}
    这样：
    - 不在项目目录内，随项目整体删除不会连带丢失
    - 同盘符，跨卷复制性能最佳
    - 以点开头 + 设置隐藏/系统属性，极难被误删
    """
    base_dir = base_dir.resolve()
    drive = base_dir.anchor  # e.g. "D:\\"（含尾分隔符）
    # 取项目文件路径做短哈希，避免同盘多项目冲突
    project_hash = hashlib.sha1(str(base_dir).encode()).hexdigest()[:8]
    hidden_root = Path(drive) / f".sakura_system_backup_{project_hash}"
    # Windows 下设置隐藏+系统属性
    if sys.platform == "win32":
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            FILE_ATTRIBUTE_SYSTEM = 0x04
            ctypes.windll.kernel32.SetFileAttributesW(str(hidden_root), FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
        except Exception:
            pass
    return hidden_root


class FileBackup:
    """管理系统文件的基线备份、完整性校验与自动修复（始终启用，无开关）。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self._backup_root = _get_default_backup_root(self.base_dir)
        self._manifest_path = self._backup_root / _MANIFEST_FILE
        self._backup_data_dir = self._backup_root / _BACKUP_DATA_DIR
        log_event("FileBackup", "初始化", {
            "backup_root": str(self._backup_root),
            "manifest": str(self._manifest_path),
        })

    # ------------------------------------------------------------------
    # 状态判断
    # ------------------------------------------------------------------

    def is_first_run(self) -> bool:
        """若基线清单不存在则视为首次启动。"""
        return not self._manifest_path.exists()

    @property
    def backup_root(self) -> Path:
        """备份根目录（默认位于项目目录之外的同盘隐藏目录）。"""
        return self._backup_root

    # ------------------------------------------------------------------
    # 备份创建
    # ------------------------------------------------------------------

    def create_backup(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> int:
        """扫描所有非排除文件，写入基线清单并复制到安全位置。

        - 目录遍历结果复用全局缓存（60 秒内不重复扫描）；
        - SHA-256 哈希与复制在多个工作线程中并行执行；
        - 清单同时记录每个文件的 mtime_ns 与 size，供后续元数据快筛。
        on_progress(completed, total, rel) 在每完成一个文件后回调；
        返回备份成功的文件总数。
        """
        log_event("FileBackup", "开始创建基线备份", {
            "backup_root": str(self._backup_root),
            "workers": _HASH_WORKERS,
        })
        self._backup_root.mkdir(parents=True, exist_ok=True)

        files = list(self._iter_eligible_files())
        total = len(files)
        file_hashes: dict[str, str] = {}
        file_meta: dict[str, dict[str, int]] = {}
        file_count = 0
        lock = threading.Lock()

        def _process(file_path: Path) -> tuple[str, str, int, int]:
            rel = _normalize_rel(str(file_path.relative_to(self.base_dir)))
            st = file_path.stat()
            digest = _compute_sha256(file_path)
            dest = self._backup_data_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest)
            return rel, digest, st.st_mtime_ns, st.st_size

        with ThreadPoolExecutor(max_workers=_HASH_WORKERS) as executor:
            futures = {executor.submit(_process, fp): fp for fp in files}
            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    rel, digest, mtime_ns, size = future.result()
                except Exception as exc:
                    log_event("FileBackup", "备份单个文件失败，已跳过", {
                        "file": str(file_path),
                        "error": str(exc),
                    })
                    continue
                with lock:
                    file_hashes[rel] = digest
                    file_meta[rel] = {"mtime_ns": mtime_ns, "size": size}
                    file_count += 1
                    if on_progress is not None:
                        on_progress(file_count, total, rel)

        manifest: dict[str, Any] = {
            "version": 2,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "file_count": file_count,
            "file_hashes": file_hashes,
            "file_meta": file_meta,
        }

        self._manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log_event("FileBackup", "基线备份创建完成", {
            "file_count": file_count,
            "total_files": total,
            "manifest_path": str(self._manifest_path),
        })
        return file_count

    # ------------------------------------------------------------------
    # 完整性检查
    # ------------------------------------------------------------------

    def check_integrity(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[bool, list[str], list[str]]:
        """对比当前文件与基线清单。

        返回 ``(is_intact, modified_files, missing_files)``。
        首次启动（无清单）直接返回 ``(True, [], [])``。

        增速策略：
        1. 目录遍历结果复用全局缓存（60 秒内不重复 rglob）；
        2. 先比 mtime_ns + size，未变则跳过 SHA-256（旧版清单无元数据时
           回退为全量哈希，保证兼容）；
        3. 需要哈希的文件由多线程并行计算。

        进度回调：遍历阶段每比对 1000 个文件回调一次
        ``on_progress(visited, total, None)``（total 为清单文件总数），
        哈希阶段每完成一个文件回调一次
        ``on_progress(done, total_hash, rel)``（total 为待哈希文件总数），
        检查结束前再补一次 100% 完成回调；日志据此实时输出百分比进度。
        """
        log_event("FileBackup", "开始完整性检查", {"manifest": str(self._manifest_path)})
        if not self._manifest_path.exists():
            log_event("FileBackup", "首次运行，无基线清单，跳过检查")
            return True, [], []

        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        saved_hashes: dict[str, str] = manifest.get("file_hashes", {})
        saved_meta: dict[str, dict[str, int]] = manifest.get("file_meta", {})
        total_tracked = len(saved_hashes)

        modified: list[str] = []
        missing: list[str] = []
        current_rel = set()
        checked = 0
        meta_unchanged = 0
        needs_hash: list[tuple[str, Path]] = []
        files = list(self._iter_eligible_files())

        # 遍历阶段：实时上报已比对文件数，避免长扫描无反馈。
        # 上报步长自适应：约 20 段，最多每 1000 个一次（小目录也有进度）
        visited = 0
        last_report = 0
        report_step = max(1, min(1000, total_tracked // 20))
        for file_path in files:
            visited += 1
            if (
                on_progress is not None
                and visited - last_report >= report_step
            ):
                last_report = visited
                on_progress(min(visited, total_tracked), total_tracked, None)

            rel = _normalize_rel(str(file_path.relative_to(self.base_dir)))
            current_rel.add(rel)

            if rel in saved_hashes:
                try:
                    st = file_path.stat()
                except FileNotFoundError:
                    missing.append(rel)
                except OSError as exc:
                    log_event("FileBackup", "文件状态读取失败，视为异常", {
                        "file": rel,
                        "error": str(exc),
                    })
                    modified.append(rel)
                else:
                    checked += 1
                    meta = saved_meta.get(rel)
                    if (
                        meta is not None
                        and meta.get("size") == st.st_size
                        and meta.get("mtime_ns") == st.st_mtime_ns
                    ):
                        meta_unchanged += 1
                    else:
                        needs_hash.append((rel, file_path))

        # 哈希阶段：每完成一个文件实时上报进度（百分比 + 待哈希文件总数）
        if needs_hash:
            done = 0
            total_hash = len(needs_hash)
            with ThreadPoolExecutor(max_workers=_HASH_WORKERS) as executor:
                futures = {
                    executor.submit(_compute_sha256, fp): rel
                    for rel, fp in needs_hash
                }
                for future in as_completed(futures):
                    rel = futures[future]
                    try:
                        digest = future.result()
                    except Exception as exc:
                        log_event("FileBackup", "文件哈希失败，视为异常", {
                            "file": rel,
                            "error": str(exc),
                        })
                        modified.append(rel)
                    else:
                        if digest != saved_hashes[rel]:
                            modified.append(rel)
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total_hash, rel)

        for saved in saved_hashes:
            if saved not in current_rel:
                missing.append(saved)

        # 检查结束前补一次 100% 完成回调（配合日志侧 completed == total 的节流）
        if on_progress is not None:
            on_progress(total_tracked, total_tracked, None)

        is_intact = not modified and not missing
        log_event("FileBackup", "完整性检查完成", {
            "tracked_files": total_tracked,
            "checked_files": checked,
            "meta_unchanged": meta_unchanged,
            "hashed_files": len(needs_hash),
            "modified": len(modified),
            "missing": len(missing),
            "intact": is_intact,
        })
        if not is_intact:
            log_event("FileBackup", "检测到文件异常", {
                "modified_files": modified[:10],
                "missing_files": missing[:10],
            })

        return is_intact, modified, missing

    # ------------------------------------------------------------------
    # 自动修复
    # ------------------------------------------------------------------

    def repair_all_modified(self) -> tuple[list[str], list[str]]:
        """根据基线清单修复所有被修改 / 丢失的文件（无需用户确认，直接执行）。

        返回 ``(repaired, failed)``。
        """
        _, modified, missing = self.check_integrity()
        affected = modified + missing
        if not affected:
            log_event("FileBackup", "无需修复", {})
            return [], []

        log_event("FileBackup", "开始自动修复", {"affected_count": len(affected)})
        repaired: list[str] = []
        failed: list[str] = []

        for rel in affected:
            try:
                self._restore_single(rel)
                repaired.append(rel)
            except OSError as exc:
                failed.append(f"{rel}: {exc}")

        log_event("FileBackup", "自动修复完成", {
            "repaired": len(repaired),
            "failed": len(failed),
        })
        if failed:
            log_event("FileBackup", "部分文件修复失败", {"failures": failed[:10]})

        return repaired, failed

    def _restore_single(self, rel: str) -> None:
        src = self._backup_data_dir / rel
        dst = self.base_dir / rel
        if not src.exists():
            raise FileNotFoundError(f"备份中未找到文件: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _count_eligible_files(self) -> int:
        return sum(1 for _ in self._iter_eligible_files())

    def _iter_eligible_files(self):
        """遍历所有符合条件的文件；结果全局缓存 60 秒，避免重复扫描。

        用 os.walk 并在遍历时剪枝：整目录排除项（characters/、runtime/、
        __pycache__/、data 下的排除子目录等）直接不进入。实测 rglob 会
        遍历全部 11.3 万个文件（其中 5.4 万个是被排除项），剪枝后只扫
        约 6.5 万个，检查耗时大幅下降。
        """
        now = time.monotonic()
        with _SCAN_CACHE_LOCK:
            cached = _SCAN_CACHE.get(self.base_dir)
            if cached is not None and now - cached[0] < _SCAN_CACHE_TTL:
                yield from cached[1]
                return

        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.base_dir):
            rel_dir = os.path.relpath(dirpath, self.base_dir)
            dirnames[:] = [
                d for d in dirnames
                if not _should_exclude(_normalize_rel(os.path.join(rel_dir, d)))
            ]
            for name in filenames:
                rel = _normalize_rel(os.path.join(rel_dir, name))
                if _should_exclude(rel):
                    continue
                files.append(Path(dirpath) / name)

        with _SCAN_CACHE_LOCK:
            _SCAN_CACHE[self.base_dir] = (now, files)
        yield from files

    # ------------------------------------------------------------------
    # ACL 保护（Windows）：拒绝删除，防止误删；启动时设置后全程保持
    # ------------------------------------------------------------------

    def protect_backup_root(self) -> None:
        """为备份根目录设置 ACL：拒绝 Everyone 删除自身及子项。

        目录不存在时先创建（保证首次创建的备份子项直接继承 deny）。
        重复调用安全：先清理旧 deny 再设置，避免 ACE 累积。
        启动流程全程保持该保护：检查/修复/重建基线都只读或覆盖写入
        备份，从不删除文件，而 deny 仅拒绝删除，因此无需解除。
        """
        if sys.platform != "win32":
            return
        try:
            self._backup_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log_event("FileBackup", "创建备份目录失败", {
                "path": str(self._backup_root),
                "error": str(exc),
            })
            return
        self._run_icacls(["/remove:d", "Everyone"], "清理旧ACL")
        self._run_icacls(["/deny", _ACL_DENY_DELETE], "设置ACL保护")

    def unprotect_backup_root(self) -> None:
        """解除备份根目录保护（工具方法，启动流程不再调用）。

        用"仅继承"版本 deny 替换根目录 deny：根目录本身恢复可删，
        但子对象继承的 deny 保留，深度保护不出现空窗。
        仅用于需要手动清理/删除备份目录的场景（如卸载前）。
        """
        if sys.platform != "win32":
            return
        if not self._backup_root.exists():
            return
        self._run_icacls(["/remove:d", "Everyone"], "清理旧ACL(解除)")
        self._run_icacls(["/deny", _ACL_DENY_DELETE_INHERIT_ONLY], "设置ACL继承保护")

    def _run_icacls(self, args: list[str], action: str) -> None:
        try:
            result = subprocess.run(
                ["icacls", str(self._backup_root), *args],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="replace",
                timeout=60,
            )
            ok = result.returncode == 0
            output = (result.stdout or result.stderr).strip()[:200]
            log_event("FileBackup", action, {
                "ok": ok,
                "output": output,
            })
        except Exception as exc:
            log_event("FileBackup", f"{action}失败", {
                "error": str(exc),
            })
