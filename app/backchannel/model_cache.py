from __future__ import annotations

import os
import shutil
import stat
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# 接话意图分类用的句向量底座(probe 头骑在它上面)。常量定义在此(bge 缓存的归属地),
# 不再从 embedding_classifier 导入——后者将随零样本原型方案一并移除。
DEFAULT_BACKCHANNEL_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

BACKCHANNEL_MODEL_CACHE_NAME = "models--" + DEFAULT_BACKCHANNEL_EMBEDDING_MODEL.replace("/", "--")
DEFAULT_HUGGINGFACE_ENDPOINT = "https://huggingface.co"


class BackchannelModelImportError(RuntimeError):
    """接话意图模型归档包格式错误或导入失败。"""


@dataclass(frozen=True)
class BackchannelModelImportResult:
    model_name: str
    cache_folder: Path
    model_dir: Path
    snapshot_count: int


def backchannel_model_cached(base_dir: Path) -> bool:
    return _backchannel_model_cache_folder(base_dir) is not None


def backchannel_model_cache_kwargs(base_dir: Path) -> dict[str, object]:
    cache_folder = _backchannel_model_cache_folder(base_dir)
    if cache_folder is not None:
        return {"cache_folder": str(cache_folder), "local_files_only": True}
    return {"cache_folder": str(_project_hf_cache_folder(base_dir)), "local_files_only": True}


def backchannel_model_endpoint() -> str:
    return (os.environ.get("HF_ENDPOINT") or DEFAULT_HUGGINGFACE_ENDPOINT).strip()


def download_backchannel_model(base_dir: Path) -> BackchannelModelImportResult:
    """下载接话 embedding 模型到 Sakura 管理的 HuggingFace cache。"""

    destination_root = _project_hf_cache_folder(base_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        _download_hf_snapshot(DEFAULT_BACKCHANNEL_EMBEDDING_MODEL, destination_root)
    except BackchannelModelImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BackchannelModelImportError(
            "接话模型在线安装失败，请检查 HuggingFace 访问、网络或代理后重试。"
            f"\n\n原始错误：{exc}"
        ) from exc

    model_dir = destination_root / BACKCHANNEL_MODEL_CACHE_NAME
    snapshot_dir = model_dir / "snapshots"
    if not _hub_snapshot_has_model_weights(snapshot_dir):
        raise BackchannelModelImportError(
            "接话模型下载后仍不完整：snapshots/ 下未找到 model.safetensors 或 pytorch_model.bin。"
        )
    snapshot_count = sum(1 for child in snapshot_dir.iterdir() if child.is_dir())
    return BackchannelModelImportResult(
        model_name=DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=model_dir,
        snapshot_count=snapshot_count,
    )


def import_backchannel_model_archive(
    archive_path: Path,
    base_dir: Path,
) -> BackchannelModelImportResult:
    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(f"接话模型包不存在：{archive}")

    destination_root = _project_hf_cache_folder(base_dir)
    destination_model_dir = destination_root / BACKCHANNEL_MODEL_CACHE_NAME
    destination_root.mkdir(parents=True, exist_ok=True)
    temp_root = destination_root / f".backchannel_model_import_{int(time.time() * 1000)}_{threading.get_ident()}"
    staging_model_dir = temp_root / BACKCHANNEL_MODEL_CACHE_NAME
    backup_model_dir = destination_root / f".{BACKCHANNEL_MODEL_CACHE_NAME}.backup"
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            model_prefix = _validate_model_zip_members(zf)
            temp_root.mkdir(parents=True, exist_ok=False)
            _extract_model_zip(zf, model_prefix, staging_model_dir)
            snapshot_dir = staging_model_dir / "snapshots"
            if not _hub_snapshot_has_model_weights(snapshot_dir):
                raise BackchannelModelImportError(
                    "接话模型包不完整：snapshots/ 下未找到 model.safetensors 或 pytorch_model.bin。"
                )

        if backup_model_dir.exists():
            shutil.rmtree(backup_model_dir, ignore_errors=True)
        if destination_model_dir.exists():
            destination_model_dir.rename(backup_model_dir)
        moved = False
        try:
            shutil.move(str(staging_model_dir), str(destination_model_dir))
            moved = True
            if backup_model_dir.exists():
                shutil.rmtree(backup_model_dir, ignore_errors=True)
        except Exception:
            if moved and destination_model_dir.exists():
                shutil.rmtree(destination_model_dir, ignore_errors=True)
            if backup_model_dir.exists() and not destination_model_dir.exists():
                backup_model_dir.rename(destination_model_dir)
            raise
    except zipfile.BadZipFile as exc:
        raise BackchannelModelImportError("不是有效的接话模型 ZIP 包。") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    snapshot_count = sum(
        1
        for child in (destination_model_dir / "snapshots").iterdir()
        if child.is_dir()
    )
    return BackchannelModelImportResult(
        model_name=DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
        cache_folder=destination_root,
        model_dir=destination_model_dir,
        snapshot_count=snapshot_count,
    )


def _backchannel_model_cache_folder(base_dir: Path) -> Path | None:
    for root in _cache_candidates(base_dir):
        snapshot_dir = root / BACKCHANNEL_MODEL_CACHE_NAME / "snapshots"
        if _hub_snapshot_has_model_weights(snapshot_dir):
            return root
    return None


def _cache_candidates(base_dir: Path) -> list[Path]:
    candidates: list[Path] = []

    def add(path: Path) -> None:
        candidate = path.expanduser()
        if candidate not in candidates:
            candidates.append(candidate)

    cache_root = (
        os.environ.get("SENTENCE_TRANSFORMERS_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("TRANSFORMERS_CACHE")
    )
    if cache_root:
        cache_path = Path(cache_root)
        add(cache_path)
        add(cache_path / "hub")
    add(Path(base_dir) / "runtime" / "hf-cache" / "hub")
    hf_home = (os.environ.get("HF_HOME") or "").strip()
    default_hf_home = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    add(default_hf_home / "hub")
    return candidates


def _project_hf_cache_folder(base_dir: Path) -> Path:
    return Path(base_dir) / "runtime" / "hf-cache" / "hub"


def _download_hf_snapshot(repo_id: str, cache_folder: Path) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise BackchannelModelImportError(
            "缺少 huggingface_hub 依赖，无法在线安装接话模型。"
        ) from exc
    return str(
        snapshot_download(
            repo_id=repo_id,
            cache_dir=str(cache_folder),
            endpoint=backchannel_model_endpoint(),
            local_files_only=False,
        )
    )


def _validate_model_zip_members(zf: zipfile.ZipFile) -> PurePosixPath:
    paths: list[PurePosixPath] = []
    file_paths: list[PurePosixPath] = []
    for info in zf.infolist():
        rel = _safe_zip_member_path(info)
        paths.append(rel)
        if not info.is_dir():
            file_paths.append(rel)
    if not file_paths:
        raise BackchannelModelImportError("接话模型包为空。")

    prefixes = [
        PurePosixPath(BACKCHANNEL_MODEL_CACHE_NAME),
        PurePosixPath("hub", BACKCHANNEL_MODEL_CACHE_NAME),
        PurePosixPath("hf-cache", "hub", BACKCHANNEL_MODEL_CACHE_NAME),
    ]
    for prefix in prefixes:
        if not any(_zip_path_is_under(path, prefix) for path in file_paths):
            continue
        allowed_parents = set(prefix.parents)
        for path in paths:
            if path == PurePosixPath(".") or path in allowed_parents:
                continue
            if not _zip_path_is_under(path, prefix):
                raise BackchannelModelImportError(
                    "接话模型包只能包含 "
                    f"{BACKCHANNEL_MODEL_CACHE_NAME} 模型缓存目录。"
                )
        return prefix
    if any(path.parts[0] == "snapshots" for path in file_paths):
        allowed_root_parts = {"blobs", "refs", "snapshots", ".no_exist"}
        for path in paths:
            if path.parts[0] not in allowed_root_parts:
                raise BackchannelModelImportError(
                    "接话模型包根目录只能包含 blobs/、refs/、snapshots/ 或 .no_exist/。"
                )
        return PurePosixPath(".")
    raise BackchannelModelImportError(
        f"接话模型包缺少 {BACKCHANNEL_MODEL_CACHE_NAME} 目录。"
    )


def _safe_zip_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    member = str(info.filename or "").replace("\\", "/").rstrip("/")
    if not member:
        raise BackchannelModelImportError("接话模型包包含空 ZIP 成员名。")
    if _is_zip_symlink(info):
        raise BackchannelModelImportError(f"接话模型包不允许包含符号链接：{member}")
    if "\x00" in member or member.startswith("/"):
        raise BackchannelModelImportError(f"ZIP 成员必须是安全的相对路径：{member!r}")
    parts = member.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BackchannelModelImportError(f"ZIP 成员包含不安全路径片段：{member!r}")
    return PurePosixPath(*parts)


def _zip_path_is_under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    if prefix == PurePosixPath("."):
        return True
    return path == prefix or path.is_relative_to(prefix)


def _extract_model_zip(
    zf: zipfile.ZipFile,
    model_prefix: PurePosixPath,
    destination_model_dir: Path,
) -> None:
    destination_model_dir.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        rel = _safe_zip_member_path(info)
        if not _zip_path_is_under(rel, model_prefix) or rel == model_prefix:
            continue
        prefix_length = 0 if model_prefix == PurePosixPath(".") else len(model_prefix.parts)
        target_rel = PurePosixPath(*rel.parts[prefix_length:])
        target = destination_model_dir.joinpath(*target_rel.parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _hub_snapshot_has_model_weights(snapshot_dir: Path) -> bool:
    if not snapshot_dir.is_dir():
        return False
    weight_filenames = {
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    }
    for revision_dir in snapshot_dir.iterdir():
        if revision_dir.is_dir() and any((revision_dir / name).is_file() for name in weight_filenames):
            return True
    return False
