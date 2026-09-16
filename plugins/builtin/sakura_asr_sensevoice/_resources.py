from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from sakura_http import urlopen_direct_for_loopback as urlopen_current_proxy

VERSION = "sensevoice-2024-07-17-int8-silero-9e2449e1"
_BASE = "https://www.modelscope.cn/models/gomodels/sherpa/resolve/590473aaa26eed19b270a424cb641972ae56482d/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/"
FILES = (
    ("model.int8.onnx", _BASE + "model.int8.onnx", 239233841),
    ("tokens.txt", _BASE + "tokens.txt", 315894),
    ("silero_vad.onnx", "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx", 643854),
)


class Cancelled(Exception):
    pass


def log_event(logger, level, event, message, **fields):
    """Logging is optional and must never alter model or audio ownership."""
    try:
        if "request_id" in fields and (not isinstance(fields["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", fields["request_id"])):
            fields["request_id"] = None
        getattr(logger, level)(message, fields={"event": event, "provider_id": "sakura.asr.sensevoice", **fields})
    except Exception:
        pass


class ModelResources:
    def __init__(self, root: Path, logger=None):
        self.logger = logger
        self.root = Path(root).resolve()
        self.path = self.root / VERSION
        # A new plugin scope starts only after its predecessor has exited.
        # Interrupted downloads may remain, but rollback backups are user data.
        if self.root.is_dir():
            for directory in self.root.iterdir():
                if (re.fullmatch(r"\.install-[0-9a-f]{32}", directory.name)
                        and directory.is_dir() and not directory.is_symlink()
                        and not directory.is_junction()
                        and directory.resolve().parent == self.root):
                    shutil.rmtree(directory, ignore_errors=True)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.thread = None
        self.closed = False
        self.state = "idle"
        self.downloaded = 0
        self.error = ""
        self.invalid = False

    def ready(self):
        if self.invalid:
            return False
        try:
            marker = json.loads((self.path / "complete.json").read_text("utf-8"))
            return isinstance(marker, dict) and marker.get("version") == VERSION and all((self.path / name).is_file() and not (self.path / name).is_symlink() and (self.path / name).stat().st_size == size for name, _, size in FILES)
        except (OSError, ValueError):
            return False

    def verify(self, check=lambda: None):
        try:
            check()
            valid = self.ready()
        except OSError:
            valid = False
        if not valid:
            self.mark_invalid()
            raise ValueError("ASR_MODEL_INVALID")

    def mark_invalid(self):
        with self.lock:
            self.invalid = True
            self.state, self.error = "failed", "ASR_MODEL_INVALID"

    def start(self, _values=None):
        with self.lock:
            if self.closed:
                raise ValueError("ASR_PROVIDER_UNAVAILABLE")
            if self.thread and self.thread.is_alive():
                return {"values": self.load()}
            self.cancelled.clear()
            self.state, self.error, self.downloaded = "running", "", 0
            self.thread = threading.Thread(target=self._install, name="sensevoice-install", daemon=True)
            log_event(self.logger, "info", "asr.model.install.started", "正在安装语音模型")
            self.thread.start()
        return {"values": self.load()}

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def _install(self):
        staging = self.root / (".install-" + uuid.uuid4().hex)
        backup = self.root / (".previous-" + uuid.uuid4().hex)
        published = False
        started_at = time.monotonic()
        try:
            if self.ready():
                try:
                    self.verify(self.check)
                except ValueError:
                    with self.lock:
                        self.state, self.error = "running", ""
                else:
                    with self.lock:
                        self.state = "succeeded"
                    return
            staging.mkdir(parents=True)
            for name, url, size in FILES:
                self.check()
                target = staging / name
                received = 0
                request = urllib.request.Request(url, headers={"User-Agent": "Sakura-ASR/1"})
                with urlopen_current_proxy(request, timeout=20) as source, target.open("wb") as output:
                    while chunk := source.read(256 * 1024):
                        self.check()
                        received += len(chunk)
                        if received > size:
                            raise ValueError("ASR_DOWNLOAD_SIZE_MISMATCH")
                        output.write(chunk)
                        with self.lock:
                            self.downloaded += len(chunk)
                if received != size:
                    raise ValueError("ASR_DOWNLOAD_SIZE_MISMATCH")
            self.check()
            (staging / "complete.json").write_text(json.dumps({"version": VERSION}), encoding="utf-8")
            # Publish only a complete validated set; retain the old set on failure.
            if self.path.exists():
                os.replace(self.path, backup)
            try:
                os.replace(staging, self.path)
            except Exception:
                if backup.exists():
                    try:
                        os.replace(backup, self.path)
                    except Exception as error:
                        raise ValueError("ASR_MODEL_RESTORE_FAILED") from error
                raise
            published = True
            with self.lock:
                self.state, self.invalid, self.error = "succeeded", False, ""
        except Cancelled:
            with self.lock:
                self.state = "cancelled"
        except Exception as error:
            with self.lock:
                self.state = "failed"
                self.error = str(error) if str(error) in {"ASR_DOWNLOAD_SIZE_MISMATCH", "ASR_MODEL_RESTORE_FAILED"} else "ASR_DOWNLOAD_FAILED"
        finally:
            # These are internally generated children of the private model root.
            # On a failed rollback, the backup is the only remaining old set.
            # Leave it intact for manual recovery or a later explicit repair.
            for directory in ((staging, backup) if published else (staging,)):
                if directory.parent.resolve() == self.root and directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)
            log_event(self.logger, "error" if self.state == "failed" else "info", "asr.model.install." + self.state, {"succeeded": "语音模型安装完成", "failed": "语音模型安装失败", "cancelled": "语音模型安装已取消"}.get(self.state, "语音模型安装已结束"), duration_ms=round((time.monotonic() - started_at) * 1000), **({"error_code": self.error} if self.state == "failed" else {}))

    def cancel(self, _values=None):
        self.cancelled.set()
        return {"values": self.load()}

    def load(self):
        with self.lock:
            ready = self.ready()
            active = self.state == "running"
            actions = ["cancelModels"] if active else ["retryModels"] if self.state in ("failed", "cancelled") else [] if ready else ["installModels"]
            return {"models": {"applicability": "required", "subtitle": "240 MB", "ready": ready,
                    "taskState": self.state, "message": {"running": "正在下载模型", "failed": "模型安装失败", "cancelled": "已取消"}.get(self.state, "已安装" if ready else "尚未安装"),
                    "detail": {"ASR_MODEL_INVALID": "模型校验或加载失败，请点击重试重新安装。", "ASR_MODEL_RESTORE_FAILED": "模型替换与回退均失败，原模型备份已保留。请检查目录权限后重试。"}.get(self.error, self.error),
                    "progress": min(100, int(self.downloaded * 100 / sum(f[2] for f in FILES))) if active else None,
                    "availableActionIds": actions}}

    def descriptor(self):
        return {"sectionId": "models", "title": "SenseVoice", "order": 100,
                "fields": [{"key": "models", "label": "本地识别模型", "type": "resource", "default": self.load()["models"], "actionIds": ["installModels", "retryModels", "cancelModels"]}],
                "actions": [{"actionId": "installModels", "label": "安装"}, {"actionId": "retryModels", "label": "重试"}, {"actionId": "cancelModels", "label": "取消"}]}

    def close(self):
        self.closed = True
        self.cancelled.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
