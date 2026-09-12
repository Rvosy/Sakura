"""Resource-only .visual transactions; legacy .char components remain readable."""
from __future__ import annotations

import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

from app.config.character_archive import ARCHIVE_FORMAT, CharacterArchiveError, _safe_archive_path, _is_zip_symlink, _validate_zip_resource_limits, _read_manifest
from app.config.character_resources import CharacterVisualResource
from app.config.plugin_requirements import parse_requirements
from app.plugins.visuals import relative_resource_path, resolve_resource_path


def _check(cancel_check):
    if cancel_check is not None:
        cancel_check()


def export_visual_archive(package: Path, resource: CharacterVisualResource, projection: dict, destination: Path, *, cancel_check=None, commit_started=None):
    _check(cancel_check)
    entry = relative_resource_path(projection["entry"])
    data = json.dumps(projection["data"], ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(data) > 256 * 1024:
        raise CharacterArchiveError("表现资源配置过大。")
    assets = projection["assets"]
    root = resolve_resource_path(package, resource.root)
    sources = {}
    for relative in assets.values():
        path = resolve_resource_path(package, relative_resource_path(relative))
        target = path.relative_to(root).as_posix()
        relative_resource_path(target)
        if target == entry:
            raise CharacterArchiveError("表现入口与资源文件冲突。")
        sources[target] = path
    manifest = {"format": ARCHIVE_FORMAT, "version": 2, "kind": "resource", "resource": {"type": resource.type, "entry": entry}}
    requirements = parse_requirements(projection.get("pluginRequirements", list(resource.plugin_requirements)))
    if any(item["kind"] != "visual" or item["type"] != resource.type for item in requirements):
        raise CharacterArchiveError("形态插件需求与资源类型不匹配。")
    if requirements:
        manifest["resource"]["pluginRequirements"] = requirements
    if resource.name:
        manifest["resource"]["name"] = resource.name
    destination = Path(destination).with_suffix(".visual")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            archive.writestr(f"resource/{entry}", data)
            for target, source in sources.items():
                _check(cancel_check)
                with source.open("rb") as reader, archive.open(f"resource/{target}", "w", force_zip64=True) as writer:
                    while chunk := reader.read(1024 * 1024):
                        _check(cancel_check)
                        writer.write(chunk)
        _check(cancel_check)
        _check(commit_started)
        temporary.replace(destination)
    finally:
        primary = sys.exception()
        try:
            temporary.unlink(missing_ok=True)
        except OSError as recovery:
            if primary is None:
                raise
            primary.recovery_error = recovery
    return destination


def import_visual_archive(archive_path: Path, package: Path, *, cancel_check=None, commit_started=None) -> CharacterVisualResource:
    _check(cancel_check)
    package = package.resolve()
    resource_id = "visual-" + uuid.uuid4().hex[:12]
    resource_root = f"visuals/{resource_id}"
    target = (package / resource_root).resolve()
    if not target.is_relative_to(package):
        raise CharacterArchiveError("表现组件目录超出角色包。")
    staging = package / f".visual-import-{uuid.uuid4().hex}"
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _validate_zip_resource_limits(archive, "表现组件", destination=package)
            seen = set()
            for info in archive.infolist():
                relative = _safe_archive_path(info.filename.rstrip("/"), "组件文件")
                normalized = relative.as_posix().casefold()
                if normalized in seen or _is_zip_symlink(info) or (relative.as_posix() != "manifest.json" and (relative.parts[0] != "resource" or (len(relative.parts) < 2 and not info.is_dir()))):
                    raise CharacterArchiveError("表现组件包含无效或重复文件。")
                seen.add(normalized)
            manifest = _read_manifest(archive)
            if manifest.get("format") != ARCHIVE_FORMAT or type(manifest.get("version")) is not int or manifest["version"] != 2 or manifest.get("kind") != "resource":
                raise CharacterArchiveError("请选择表现组件。")
            descriptor = manifest.get("resource", {})
            resource = CharacterVisualResource.from_mapping({"id": resource_id, "type": descriptor.get("type"), "root": resource_root, "entry": descriptor.get("entry"), "name": descriptor.get("name", ""), "pluginRequirements": descriptor.get("pluginRequirements", [])})
            staging.mkdir(exist_ok=False)
            for info in archive.infolist():
                _check(cancel_check)
                relative = _safe_archive_path(info.filename.rstrip("/"), "组件文件")
                if relative.as_posix() == "manifest.json":
                    continue
                path = staging.joinpath(*relative.parts[1:])
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as reader, path.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        _check(cancel_check)
                        writer.write(chunk)
            if not (staging / resource.entry).is_file():
                raise CharacterArchiveError("表现组件缺少入口文件。")
            _check(cancel_check)
            target.parent.mkdir(parents=True, exist_ok=True)
            _check(commit_started)
            staging.rename(target)
            return resource
    finally:
        primary = sys.exception()
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError as recovery:
            if primary is None:
                raise
            primary.recovery_error = recovery
