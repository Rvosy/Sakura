"""Sakura .plugin 插件包导入/导出。"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.plugins.models import KNOWN_PLUGIN_PERMISSIONS, SUPPORTED_API_VERSIONS


PLUGIN_ARCHIVE_SUFFIX = ".plugin"

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ZIP_SYMLINK_MODE = 0o120000
_ZIP_FILE_TYPE_MASK = 0o170000

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    "__pycache__",
    "cache",
    "data",
    "model_cache",
    "models",
    "runtime",
    "temp",
    "tmp",
}
_EXCLUDED_FILE_NAMES = {".DS_Store", "Thumbs.db"}
_EXCLUDED_SUFFIXES = {".log", ".pyc", ".pyo", ".tmp"}


class PluginArchiveError(RuntimeError):
    """插件包格式错误或导入/导出失败。"""


@dataclass(frozen=True)
class PluginArchiveManifest:
    plugin_id: str
    name: str
    version: str
    api_version: int
    entry: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class PluginArchiveImportResult:
    plugin_id: str
    name: str
    version: str
    plugin_dir: Path


@dataclass(frozen=True)
class PluginArchiveExportResult:
    plugin_id: str
    name: str
    version: str
    archive_path: Path
    file_count: int


def import_plugin_archive(archive_path: Path, base_dir: Path) -> PluginArchiveImportResult:
    """导入 .plugin 插件包到 ``plugins/<id>``。

    目标插件已存在时抛出 ``PluginArchiveError``，避免静默覆盖。导入过程先写入
    ``plugins`` 下的临时目录，完整校验通过后再移动到最终目录。
    """

    archive_path = Path(archive_path)
    _ensure_plugin_suffix(archive_path)
    plugins_dir = Path(base_dir) / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = _safe_members(archive)
            manifest = _manifest_from_archive(archive, members)
            target_dir = plugins_dir / manifest.plugin_id
            if target_dir.exists():
                raise PluginArchiveError(f"插件已存在：{manifest.plugin_id}")
            _validate_entry_member(manifest, members)
            temp_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{manifest.plugin_id}.importing-",
                    dir=plugins_dir,
                )
            )
            try:
                _extract_members(archive, members, temp_dir)
                _validate_extracted_manifest(temp_dir, manifest)
                target_dir.parent.mkdir(parents=True, exist_ok=True)
                temp_dir.replace(target_dir)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
    except zipfile.BadZipFile as exc:
        raise PluginArchiveError("不是有效的 .plugin 插件包。") from exc
    except OSError as exc:
        raise PluginArchiveError(f"插件导入失败：{exc}") from exc

    return PluginArchiveImportResult(
        plugin_id=manifest.plugin_id,
        name=manifest.name,
        version=manifest.version,
        plugin_dir=target_dir,
    )


def export_plugin_archive(plugin_dir: Path, output_path: Path) -> PluginArchiveExportResult:
    """把插件安装目录内容导出为 .plugin 包。

    默认排除运行时数据、模型缓存、临时目录、缓存目录和 Python 字节码文件。
    """

    plugin_dir = Path(plugin_dir)
    output_path = _with_plugin_suffix(Path(output_path))
    if not plugin_dir.is_dir():
        raise PluginArchiveError(f"插件目录不存在：{plugin_dir}")
    if _path_is_inside(output_path.resolve(), plugin_dir.resolve()):
        raise PluginArchiveError("导出目标不能位于插件目录内部。")

    manifest_path = plugin_dir / "plugin.yaml"
    manifest = _manifest_from_mapping(_load_manifest_file(manifest_path))
    _validate_entry_path(plugin_dir, manifest)

    files = _exportable_files(plugin_dir)
    if not files:
        raise PluginArchiveError("插件目录没有可导出的文件。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(plugin_dir).as_posix())
    except OSError as exc:
        raise PluginArchiveError(f"插件导出失败：{exc}") from exc

    return PluginArchiveExportResult(
        plugin_id=manifest.plugin_id,
        name=manifest.name,
        version=manifest.version,
        archive_path=output_path,
        file_count=len(files),
    )


def _ensure_plugin_suffix(path: Path) -> None:
    if path.suffix.lower() != PLUGIN_ARCHIVE_SUFFIX:
        raise PluginArchiveError("插件包文件后缀必须是 .plugin。")


def _with_plugin_suffix(path: Path) -> Path:
    if path.suffix.lower() == PLUGIN_ARCHIVE_SUFFIX:
        return path
    return path.with_suffix(PLUGIN_ARCHIVE_SUFFIX)


def _load_manifest_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PluginArchiveError("插件目录缺少 plugin.yaml。")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginArchiveError(f"plugin.yaml 读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise PluginArchiveError("plugin.yaml 必须是 YAML 对象。")
    return raw


def _safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        name = _normalize_zip_name(info.filename)
        if not name:
            continue
        if _zip_member_is_symlink(info):
            raise PluginArchiveError(f"插件包不允许包含符号链接：{info.filename}")
        members[name] = info
    if not members:
        raise PluginArchiveError("插件包为空。")
    return members


def _manifest_from_archive(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> PluginArchiveManifest:
    info = members.get("plugin.yaml")
    if info is None or info.is_dir():
        raise PluginArchiveError("插件包根目录缺少 plugin.yaml。")
    try:
        raw = yaml.safe_load(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, KeyError) as exc:
        raise PluginArchiveError(f"plugin.yaml 读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise PluginArchiveError("plugin.yaml 必须是 YAML 对象。")
    return _manifest_from_mapping(raw)


def _manifest_from_mapping(raw: dict[str, Any]) -> PluginArchiveManifest:
    plugin_id = _string_value(raw.get("id") or raw.get("plugin_id"))
    api_version = _int_value(raw.get("api_version"), 0)
    entry = _string_value(raw.get("entry"))
    permissions = _permissions_value(raw.get("permissions"))
    if not plugin_id:
        raise PluginArchiveError("plugin.yaml 缺少 id。")
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        raise PluginArchiveError("插件 id 只能包含字母、数字、下划线和短横线，且长度不超过 64。")
    if api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_API_VERSIONS))
        raise PluginArchiveError(f"插件 API 版本不支持：{api_version}（当前支持 {supported}）。")
    _validate_entry(entry)
    if not permissions:
        raise PluginArchiveError("plugin.yaml 缺少 permissions 声明。")
    unknown = sorted(set(permissions) - KNOWN_PLUGIN_PERMISSIONS)
    if unknown:
        raise PluginArchiveError(f"插件声明了未知权限：{', '.join(unknown)}。")
    return PluginArchiveManifest(
        plugin_id=plugin_id,
        name=_string_value(raw.get("name")) or plugin_id,
        version=_string_value(raw.get("version")) or "0.0.0",
        api_version=api_version,
        entry=entry,
        permissions=permissions,
    )


def _validate_entry(entry: str) -> None:
    module_name, separator, class_name = entry.partition(":")
    if separator != ":" or not _MODULE_RE.fullmatch(module_name) or not _CLASS_RE.fullmatch(class_name):
        raise PluginArchiveError("entry 必须是相对插件目录的 module:ClassName 格式。")


def _validate_entry_member(
    manifest: PluginArchiveManifest,
    members: dict[str, zipfile.ZipInfo],
) -> None:
    module_name = manifest.entry.partition(":")[0]
    entry_name = PurePosixPath(*module_name.split(".")).with_suffix(".py").as_posix()
    if entry_name not in members or members[entry_name].is_dir():
        raise PluginArchiveError(f"插件包缺少入口文件：{entry_name}")


def _validate_entry_path(plugin_dir: Path, manifest: PluginArchiveManifest) -> None:
    module_name = manifest.entry.partition(":")[0]
    entry_path = plugin_dir.joinpath(*module_name.split(".")).with_suffix(".py")
    if not entry_path.is_file():
        raise PluginArchiveError(f"插件目录缺少入口文件：{entry_path.relative_to(plugin_dir)}")


def _validate_extracted_manifest(temp_dir: Path, expected: PluginArchiveManifest) -> None:
    actual = _manifest_from_mapping(_load_manifest_file(temp_dir / "plugin.yaml"))
    if actual != expected:
        raise PluginArchiveError("插件包解压后的 plugin.yaml 与校验结果不一致。")


def _extract_members(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    temp_dir: Path,
) -> None:
    temp_root = temp_dir.resolve()
    for name, info in members.items():
        target = (temp_dir / PurePosixPath(name)).resolve()
        if target != temp_root and temp_root not in target.parents:
            raise PluginArchiveError(f"ZIP 成员越界：{name!r}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def _normalize_zip_name(name: str) -> str:
    if "\x00" in name:
        raise PluginArchiveError("ZIP 成员名不能包含空字符。")
    normalized = name.replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
        raise PluginArchiveError(f"ZIP 成员必须是安全的相对路径：{name!r}")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PluginArchiveError(f"ZIP 成员包含不安全路径片段：{name!r}")
    return path.as_posix()


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & _ZIP_FILE_TYPE_MASK) == _ZIP_SYMLINK_MODE


def _exportable_files(plugin_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(plugin_dir.rglob("*")):
        relative_parts = path.relative_to(plugin_dir).parts
        if any(part in _EXCLUDED_DIR_NAMES for part in relative_parts[:-1]):
            continue
        if path.is_dir():
            continue
        if _excluded_export_file(path):
            continue
        files.append(path)
    return files


def _excluded_export_file(path: Path) -> bool:
    if path.name in _EXCLUDED_FILE_NAMES:
        return True
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return True
    if path.parent.name in _EXCLUDED_DIR_NAMES:
        return True
    return False


def _path_is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _string_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _permissions_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    permissions: list[str] = []
    for item in value:
        text = _string_value(item)
        if text:
            permissions.append(text)
    return tuple(dict.fromkeys(permissions))
