from __future__ import annotations

import os
import shutil
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from .support import StoragePaths
except ImportError:
    from support import StoragePaths


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_EMBEDDING_DIMS = 512
DEFAULT_EMBEDDING_ARTIFACT_REPO = "Qdrant/bge-small-zh-v1.5"
DEFAULT_EMBEDDING_ARTIFACT_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
DEFAULT_EMBEDDING_MODEL_CACHE_NAME = "models--Qdrant--bge-small-zh-v1.5"
DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES = (
    "model_optimized.onnx", "config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
)


class MemoryModelImportError(RuntimeError):
    def __init__(self, message: str, *, code: str = "DOWNLOAD_FAILED") -> None:
        super().__init__(message)
        self.code = code


class MemoryModelTaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingModelImportResult:
    model_name: str
    cache_folder: Path
    model_dir: Path
    snapshot_count: int = 1


def _cache_root(base_dir: Path | None, cache_dir: Path | None) -> Path:
    return Path(cache_dir) if cache_dir is not None else StoragePaths(Path(base_dir or ".")).memory_cache_dir


def _validate_fastembed_snapshot_artifacts(snapshot: Path) -> None:
    missing = [name for name in DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES
               if not (snapshot / name).is_file() or (snapshot / name).stat().st_size == 0]
    if missing:
        raise MemoryModelImportError(f"模型文件缺失：{', '.join(missing)}", code="DOWNLOAD_INCOMPLETE")


def _embedding_model_snapshot(
    model_name: str = DEFAULT_EMBEDDING_MODEL, base_dir: Path | None = None,
    *, cache_dir: Path | None = None,
) -> Path | None:
    if model_name != DEFAULT_EMBEDDING_MODEL:
        return None
    roots = [_cache_root(base_dir, cache_dir)]
    if os.environ.get("FASTEMBED_CACHE_PATH"):
        roots.insert(0, Path(os.environ["FASTEMBED_CACHE_PATH"]))
    for root in roots:
        snapshot = root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME / "snapshots" / DEFAULT_EMBEDDING_ARTIFACT_REVISION
        if all((snapshot / name).is_file() and (snapshot / name).stat().st_size
               for name in DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES):
            return snapshot
    return None


def _embedding_model_cached(model_name: str, base_dir: Path | None = None, *, cache_dir: Path | None = None) -> bool:
    return _embedding_model_snapshot(model_name, base_dir, cache_dir=cache_dir) is not None


def embedding_endpoint() -> str:
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def download_embedding_model(
    base_dir: Path | None = None, *, cache_dir: Path | None = None,
    progress: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> EmbeddingModelImportResult:
    """只在用户发起下载时联网；完整下载后再替换资源目录。"""
    from sakura_http import urlopen_direct_for_loopback

    root = _cache_root(base_dir, cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    destination = model_dir / "snapshots" / DEFAULT_EMBEDDING_ARTIFACT_REVISION

    def report(stage: str, percent: int) -> None:
        if cancel is not None and cancel.is_set():
            raise MemoryModelTaskCancelled("模型下载已取消。")
        if progress:
            progress(stage, percent)

    report("connecting", 0)
    with tempfile.TemporaryDirectory(prefix=".bge-download-", dir=root) as staging:
        snapshot = Path(staging) / "snapshot"
        snapshot.mkdir()
        for index, filename in enumerate(DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES):
            url = f"{embedding_endpoint()}/{DEFAULT_EMBEDDING_ARTIFACT_REPO}/resolve/{DEFAULT_EMBEDDING_ARTIFACT_REVISION}/{filename}"
            request = urllib.request.Request(url, headers={"User-Agent": "Sakura-Memory/2"})
            with urlopen_direct_for_loopback(request, timeout=30) as response, (snapshot / filename).open("wb") as handle:
                size = int(response.headers.get("Content-Length", 0))
                received = 0
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    received += len(chunk)
                    report("downloading", int(95 * (index + (received / size if size else 0)) /
                                              len(DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES)))
                if size and received != size:
                    raise MemoryModelImportError(f"{filename} 下载不完整。", code="DOWNLOAD_INCOMPLETE")
        _validate_fastembed_snapshot_artifacts(snapshot)
        report("installing", 95)
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = Path(staging) / "previous"
        if destination.exists():
            destination.rename(backup)
        try:
            snapshot.rename(destination)
        except OSError:
            if backup.exists():
                backup.rename(destination)
            raise
    report("completed", 100)
    return EmbeddingModelImportResult(DEFAULT_EMBEDDING_MODEL, root, model_dir)
