from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from app.sensory.models import SensorySource
from app.storage.paths import StoragePaths


AUDIO_MODEL_MANIFEST_ENV = "SAKURA_AUDIO_MODEL_MANIFEST"
AUDIO_MODEL_MANIFEST_FILENAMES = (
    "audio_model_manifest.json",
    "llama_cpp_audio_model_manifest.json",
)


def audio_model_manifest_paths(base_dir: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get(AUDIO_MODEL_MANIFEST_ENV, "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    if base_dir is not None:
        model_dir = StoragePaths(base_dir).sensory_models_cache_dir
        candidates.extend(model_dir / filename for filename in AUDIO_MODEL_MANIFEST_FILENAMES)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_llama_cpp_audio_model_manifest_entry(
    base_dir: Path,
    *,
    source: SensorySource,
    repo_id: str,
    include_patterns: tuple[str, ...],
) -> dict[str, Any]:
    manifest_path, payload = _load_first_manifest(base_dir)
    if manifest_path is None:
        return {}
    entries = payload.get("models")
    if not isinstance(entries, list):
        raise RuntimeError(f"音频模型 manifest 必须包含 models 列表：{manifest_path}")
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry_source = str(raw_entry.get("source") or "").strip().lower()
        entry_repo = str(raw_entry.get("repo_id") or raw_entry.get("model") or "").strip()
        if entry_source != source.value or entry_repo != repo_id:
            continue
        files = _manifest_files(raw_entry, manifest_path.parent, include_patterns)
        return {
            "manifest_path": str(manifest_path),
            "source": source.value,
            "repo_id": repo_id,
            "files": files,
        }
    return {}


def copy_llama_cpp_audio_model_from_manifest(
    base_dir: Path,
    *,
    source: SensorySource,
    repo_id: str,
    include_patterns: tuple[str, ...],
    local_dir: Path,
) -> dict[str, Any]:
    entry = find_llama_cpp_audio_model_manifest_entry(
        base_dir,
        source=source,
        repo_id=repo_id,
        include_patterns=include_patterns,
    )
    if not entry:
        return {}
    target = Path(local_dir)
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for file_info in entry["files"]:
        filename = _safe_relative_filename(str(file_info["filename"]))
        source_path = Path(str(file_info["path"]))
        destination = target / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        _verify_local_file(source_path, file_info)
        part_path = destination.with_name(f"{destination.name}.part")
        try:
            shutil.copyfile(source_path, part_path)
            part_path.replace(destination)
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(f"复制音频模型文件失败：{source_path}：{exc}") from exc
        copied.append(filename)
    return {
        "repo_id": repo_id,
        "local_dir": str(target),
        "include_patterns": list(include_patterns),
        "download_method": "local_manifest",
        "manifest_path": str(entry["manifest_path"]),
        "copied_files": copied,
        "message": f"copied {len(copied)} file(s) from local audio model manifest",
    }


def validate_llama_cpp_audio_model_manifest(
    base_dir: Path,
    *,
    manifest_path: Path | None = None,
    required_sources: tuple[SensorySource, ...] = (),
) -> dict[str, Any]:
    path, payload = _load_manifest_for_validation(base_dir, manifest_path)
    issues: list[str] = []
    entries = payload.get("models")
    if not isinstance(entries, list):
        issues.append("音频模型 manifest 必须包含 models 列表")
        entries = []
    checked_models: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            issues.append(f"models[{index}] 不是 JSON 对象")
            continue
        checked_models.append(_check_manifest_entry(raw_entry, path.parent, issues, index))

    required = tuple(required_sources)
    for source in required:
        expected = _expected_recommended_model(source)
        if expected is None:
            issues.append(f"{source.value} 没有内置推荐音频模型")
            continue
        match = next(
            (
                model
                for model in checked_models
                if model.get("source") == source.value and model.get("repo_id") == expected["repo_id"]
            ),
            None,
        )
        if match is None:
            issues.append(f"缺少 {source.value} 推荐模型：{expected['repo_id']}")
            continue
        filenames = [Path(str(file_info.get("filename") or "")).name for file_info in match.get("files", [])]
        for pattern in expected["include_patterns"]:
            if not any(fnmatch.fnmatch(filename, pattern) for filename in filenames):
                issues.append(f"{source.value} 推荐模型缺少文件：{pattern}")

    return {
        "ok": not issues,
        "manifest_path": str(path),
        "issues": issues,
        "required_sources": [source.value for source in required],
        "model_count": len(checked_models),
        "models": checked_models,
    }


def _load_first_manifest(base_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    env_path = os.environ.get(AUDIO_MODEL_MANIFEST_ENV, "").strip()
    explicit_manifest = Path(env_path).expanduser() if env_path else None
    for path in audio_model_manifest_paths(base_dir):
        if not path.is_file():
            if explicit_manifest is not None and path == explicit_manifest:
                raise RuntimeError(f"音频模型 manifest 不存在：{path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取音频模型 manifest：{path}：{exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"音频模型 manifest 必须是 JSON 对象：{path}")
        return path, payload
    return None, {}


def _load_manifest_for_validation(
    base_dir: Path,
    manifest_path: Path | None,
) -> tuple[Path, dict[str, Any]]:
    if manifest_path is not None:
        path = Path(manifest_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"音频模型 manifest 不存在：{path}")
        return path, _read_manifest_payload(path)
    path, payload = _load_first_manifest(base_dir)
    if path is None:
        default_path = StoragePaths(base_dir).sensory_models_cache_dir / AUDIO_MODEL_MANIFEST_FILENAMES[0]
        raise RuntimeError(f"音频模型 manifest 不存在：{default_path}")
    return path, payload


def _read_manifest_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取音频模型 manifest：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"音频模型 manifest 必须是 JSON 对象：{path}")
    return payload


def _manifest_files(
    entry: dict[str, Any],
    manifest_dir: Path,
    include_patterns: tuple[str, ...],
) -> list[dict[str, Any]]:
    raw_files = entry.get("files")
    if not isinstance(raw_files, list):
        raise RuntimeError("音频模型 manifest 条目必须包含 files 列表。")
    files: list[dict[str, Any]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        filename = _safe_relative_filename(
            str(raw_file.get("filename") or Path(str(raw_file.get("url") or raw_file.get("path") or "")).name)
        )
        if include_patterns and not any(fnmatch.fnmatch(Path(filename).name, pattern) for pattern in include_patterns):
            continue
        path = _resolve_manifest_file_path(
            str(raw_file.get("url") or raw_file.get("path") or ""),
            manifest_dir,
        )
        files.append(
            {
                "filename": filename,
                "path": str(path),
                "size_bytes": _positive_int(raw_file.get("size_bytes")),
                "sha256": str(raw_file.get("sha256") or "").strip().lower(),
            }
        )
    filenames = [Path(file_info["filename"]).name for file_info in files]
    missing_patterns = [
        pattern
        for pattern in include_patterns
        if not any(fnmatch.fnmatch(filename, pattern) for filename in filenames)
    ]
    if missing_patterns:
        raise RuntimeError(f"音频模型 manifest 缺少推荐文件：{', '.join(missing_patterns)}")
    if not files:
        raise RuntimeError("音频模型 manifest 没有可用文件。")
    return files


def _check_manifest_entry(
    entry: dict[str, Any],
    manifest_dir: Path,
    issues: list[str],
    index: int,
) -> dict[str, Any]:
    source = str(entry.get("source") or "").strip().lower()
    repo_id = str(entry.get("repo_id") or entry.get("model") or "").strip()
    raw_files = entry.get("files")
    if not source:
        issues.append(f"models[{index}] 缺少 source")
    if not repo_id:
        issues.append(f"models[{index}] 缺少 repo_id")
    if not isinstance(raw_files, list) or not raw_files:
        issues.append(f"models[{index}] 缺少 files")
        raw_files = []
    checked_files: list[dict[str, Any]] = []
    for file_index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, dict):
            issues.append(f"models[{index}].files[{file_index}] 不是 JSON 对象")
            continue
        try:
            filename = _safe_relative_filename(
                str(raw_file.get("filename") or Path(str(raw_file.get("url") or raw_file.get("path") or "")).name)
            )
            path = _resolve_manifest_file_path(
                str(raw_file.get("url") or raw_file.get("path") or ""),
                manifest_dir,
            )
            file_info = {
                "filename": filename,
                "path": str(path),
                "size_bytes": _positive_int(raw_file.get("size_bytes")),
                "sha256": str(raw_file.get("sha256") or "").strip().lower(),
            }
            _verify_local_file(path, file_info)
        except RuntimeError as exc:
            issues.append(f"models[{index}].files[{file_index}] {exc}")
            checked_files.append(
                {
                    "filename": str(raw_file.get("filename") or ""),
                    "path": str(raw_file.get("url") or raw_file.get("path") or ""),
                    "ok": False,
                    "message": str(exc),
                }
            )
            continue
        checked_files.append({**file_info, "ok": True})
    return {
        "source": source,
        "repo_id": repo_id,
        "files": checked_files,
    }


def _expected_recommended_model(source: SensorySource) -> dict[str, Any] | None:
    from app.sensory.audio_models import llama_cpp_audio_model_repo_id, recommended_llama_cpp_audio_model

    recommendation = recommended_llama_cpp_audio_model(source)
    if recommendation is None:
        return None
    return {
        "repo_id": llama_cpp_audio_model_repo_id(recommendation.model),
        "include_patterns": list(recommendation.include_patterns),
    }


def _resolve_manifest_file_path(value: str, manifest_dir: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError("音频模型 manifest 文件缺少路径。")
    parsed = urlparse(raw)
    if parsed.scheme == "file":
        return Path(url2pathname(parsed.path)).expanduser()
    if parsed.scheme:
        raise RuntimeError("音频模型 manifest 仅支持本地文件路径或 file:// URI。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = manifest_dir / path
    return path


def _verify_local_file(path: Path, file_info: dict[str, Any]) -> None:
    if not path.is_file():
        raise RuntimeError(f"音频模型文件不存在：{path}")
    expected_size = int(file_info.get("size_bytes") or 0)
    if expected_size > 0 and path.stat().st_size != expected_size:
        raise RuntimeError(f"音频模型文件大小不匹配：{path}")
    expected_sha256 = str(file_info.get("sha256") or "").strip().lower()
    if expected_sha256 and _sha256_file(path) != expected_sha256:
        raise RuntimeError(f"音频模型文件 sha256 不匹配：{path}")


def _safe_relative_filename(value: str) -> str:
    filename = str(value or "").strip()
    path = Path(filename)
    if not filename or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"音频模型文件名不安全：{filename}")
    return filename


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
