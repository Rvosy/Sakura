from __future__ import annotations

import json
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.config.character_loader import (
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    character_theme_from_mapping,
    character_theme_to_mapping,
)
from app.storage.atomic import rename_with_retry, replace_with_retry


ARCHIVE_FORMAT = "sakura.character.archive"
ARCHIVE_VERSION = 1
ARCHIVE_MANIFEST = "manifest.json"
ARCHIVE_CHARACTER_ROOT = PurePosixPath("character")
VOICE_ARCHIVE_FORMAT = "sakura.character.voice"
VOICE_ARCHIVE_VERSION = 1
VOICE_ARCHIVE_ROOT = PurePosixPath("voice")

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SAFE_CHARACTER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class CharacterArchiveError(RuntimeError):
    """Sakura 角色归档包格式错误或导入导出失败。"""


@dataclass(frozen=True)
class CharacterArchiveImportResult:
    """角色归档导入后的结果。"""

    profile: CharacterProfile
    character_id: str
    display_name: str
    package_dir: Path


@dataclass(frozen=True)
class CharacterVoiceArchiveImportResult:
    """TTS 语音包导入后的结果。"""

    profile: CharacterProfile
    character_id: str
    display_name: str
    package_dir: Path


def import_character_archive(path: Path, base_dir: Path) -> CharacterArchiveImportResult:
    """导入 Sakura 自有 .char 角色归档包。"""

    archive_path = Path(path)
    if not archive_path.exists():
        raise FileNotFoundError(f"角色包不存在：{archive_path}")

    characters_dir = base_dir / "characters"
    characters_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            _validate_zip_members(zf)
            manifest = _read_manifest(zf)
            character_data = _validated_character_data(manifest)

            original_id = _required_character_id(character_data, "character.id")
            display_name = _required_text(character_data, "display_name", "character.display_name")
            target_id = _unique_character_id(original_id, characters_dir)
            target_name = _unique_display_name(display_name, characters_dir)
            target_dir = characters_dir / target_id

            temp_root = characters_dir / f"char_import_{uuid.uuid4().hex}"
            try:
                temp_root.mkdir(parents=True, exist_ok=False)
                extract_dir = temp_root / "extract"
                staging_dir = temp_root / "package"
                zf.extractall(extract_dir)

                source_character_dir = extract_dir / ARCHIVE_CHARACTER_ROOT.as_posix()
                if not source_character_dir.is_dir():
                    raise CharacterArchiveError("角色包缺少 character/ 资源目录。")

                shutil.copytree(source_character_dir, staging_dir)
                normalized_character = _normalized_import_character_data(
                    character_data,
                    character_id=target_id,
                    display_name=target_name,
                    package_dir=staging_dir,
                )
                _write_character_manifest(staging_dir, normalized_character)

                moved = False
                try:
                    # 刚解压完的目录在 Windows 上可能被杀毒/索引器瞬时锁定，带重试改名
                    rename_with_retry(staging_dir, target_dir)
                    moved = True
                    profile = CharacterRegistry(base_dir).get(target_id)
                except Exception:
                    if moved and target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    raise
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
    except zipfile.BadZipFile as exc:
        raise CharacterArchiveError("不是有效的 Sakura .char ZIP 包。") from exc

    return CharacterArchiveImportResult(
        profile=profile,
        character_id=profile.id,
        display_name=profile.display_name,
        package_dir=profile.package_dir,
    )


def import_character_voice_archive(
    path: Path,
    base_dir: Path,
    character_id: str,
) -> CharacterVoiceArchiveImportResult:
    """导入 Sakura .voice TTS 模型包到指定角色。"""

    archive_path = Path(path)
    if not archive_path.exists():
        raise FileNotFoundError(f"语音包不存在：{archive_path}")

    try:
        registry = CharacterRegistry(base_dir)
        target_profile = registry.get(character_id)
    except CharacterConfigError as exc:
        raise CharacterArchiveError(f"目标角色不存在：{character_id}") from exc

    characters_dir = base_dir / "characters"
    target_dir = target_profile.package_dir
    target_voice_dir = target_dir / "voice"
    manifest_path = target_dir / "character.json"

    try:
        original_manifest = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CharacterArchiveError(f"角色清单无法读取：{manifest_path}") from exc

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            _validate_voice_zip_members(zf)
            manifest = _read_manifest(zf)
            voice_data = _validated_voice_data(manifest)
            normalized_voice = _normalized_voice_archive(voice_data)

            temp_root = characters_dir / f"voice_import_{uuid.uuid4().hex}"
            backup_voice_dir = temp_root / "backup_voice"
            try:
                temp_root.mkdir(parents=True, exist_ok=False)
                extract_dir = temp_root / "extract"
                staging_package_dir = temp_root / "package"
                zf.extractall(extract_dir)

                source_voice_dir = extract_dir / VOICE_ARCHIVE_ROOT.as_posix()
                if not source_voice_dir.is_dir():
                    raise CharacterArchiveError("语音包缺少 voice/ 资源目录。")

                shutil.copytree(source_voice_dir, staging_package_dir / "voice")
                _validate_voice_referenced_files(staging_package_dir, normalized_voice)

                if target_voice_dir.exists():
                    rename_with_retry(target_voice_dir, backup_voice_dir)
                moved = False
                try:
                    shutil.copytree(staging_package_dir / "voice", target_voice_dir)
                    moved = True
                    _write_character_voice_manifest(target_dir, normalized_voice)
                    profile = CharacterRegistry(base_dir).get(character_id)
                except Exception:
                    if moved and target_voice_dir.exists():
                        shutil.rmtree(target_voice_dir, ignore_errors=True)
                    if backup_voice_dir.exists():
                        rename_with_retry(backup_voice_dir, target_voice_dir)
                    manifest_path.write_text(original_manifest, encoding="utf-8")
                    raise
                else:
                    if backup_voice_dir.exists():
                        shutil.rmtree(backup_voice_dir, ignore_errors=True)
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
    except zipfile.BadZipFile as exc:
        raise CharacterArchiveError("不是有效的 Sakura .voice ZIP 包。") from exc

    return CharacterVoiceArchiveImportResult(
        profile=profile,
        character_id=profile.id,
        display_name=profile.display_name,
        package_dir=profile.package_dir,
    )


def export_character_archive(profile: CharacterProfile, output_path: Path, *, include_voice: bool = True) -> None:
    """导出 Sakura 角色包为自有 .char 归档。"""

    destination = Path(output_path)
    if destination.suffix.lower() != ".char":
        destination = destination.with_suffix(".char")
    destination.parent.mkdir(parents=True, exist_ok=True)

    package_files = [
        path
        for path in profile.package_dir.rglob("*")
        if (
            path.is_file()
            and _resolved(path) != _resolved(destination)
            and path.name != "character.json"
            and (include_voice or not _is_voice_package_file(profile.package_dir, path))
        )
    ]
    package_archive_names = {
        _archive_path_for_package_file(profile.package_dir, path).as_posix()
        for path in package_files
    }
    external_paths: dict[Path, PurePosixPath] = {}

    def archive_path_for_resource(path: Path | None, kind: str) -> str | None:
        if path is None:
            return None
        archive_path = _archive_path_for_profile_resource(
            profile.package_dir,
            path,
            kind=kind,
            package_archive_names=package_archive_names,
            external_paths=external_paths,
        )
        return archive_path.as_posix()

    card_archive_path = archive_path_for_resource(profile.card_path, "card")
    default_portrait_archive_path = archive_path_for_resource(profile.default_portrait_path, "portrait")
    expression_archive_paths = {
        label: archive_path_for_resource(path, "portrait")
        for label, path in profile.expression_portraits.items()
    }
    character_manifest: dict[str, Any] = {
        "id": profile.id,
        "display_name": profile.display_name,
        "initial_message": profile.initial_message,
        "card": card_archive_path,
        "portrait": {
            "default": default_portrait_archive_path,
            "expressions": expression_archive_paths,
        },
        "reply": {"tones": [*profile.reply_tones]},
        "theme": character_theme_to_mapping(
            profile.theme_settings,
            source=profile.theme_source,
        ),
    }
    if include_voice and profile.voice is not None:
        character_manifest["voice"] = {
            "gpt_model": archive_path_for_resource(profile.voice.gpt_model_path, "voice/models"),
            "sovits_model": archive_path_for_resource(profile.voice.sovits_model_path, "voice/models"),
            "tone_refs": archive_path_for_resource(profile.voice.tone_ref_path, "voice/refs"),
            "ref_lang": profile.voice.ref_lang,
            "text_lang": profile.voice.text_lang,
        }

    archive_manifest = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "character": character_manifest,
    }

    temp_output = destination.with_name(f".{destination.name}.tmp")
    try:
        with zipfile.ZipFile(temp_output, "w", zipfile.ZIP_DEFLATED) as zf:
            written: set[str] = set()
            for source in package_files:
                _write_zip_file(
                    zf,
                    source,
                    _archive_path_for_package_file(profile.package_dir, source),
                    written,
                )
            for source, archive_path in external_paths.items():
                _write_zip_file(zf, source, archive_path, written)
            zf.writestr(
                PurePosixPath(ARCHIVE_CHARACTER_ROOT.as_posix(), "character.json").as_posix(),
                json.dumps(_package_character_data(character_manifest), ensure_ascii=False, indent=2),
            )
            zf.writestr(
                ARCHIVE_MANIFEST,
                json.dumps(archive_manifest, ensure_ascii=False, indent=2),
            )
        replace_with_retry(temp_output, destination)
    finally:
        temp_output.unlink(missing_ok=True)


def export_character_voice_archive(profile: CharacterProfile, output_path: Path) -> None:
    """导出单独的 Sakura .voice TTS 模型包。"""

    if profile.voice is None:
        raise CharacterArchiveError("当前角色没有可导出的语音包。")

    destination = Path(output_path)
    if destination.suffix.lower() != ".voice":
        destination = destination.with_suffix(".voice")
    destination.parent.mkdir(parents=True, exist_ok=True)

    voice_files = [
        path
        for path in (profile.package_dir / VOICE_ARCHIVE_ROOT.as_posix()).rglob("*")
        if path.is_file() and _resolved(path) != _resolved(destination)
    ]
    voice_archive_names = {
        _voice_archive_path_for_package_file(profile.package_dir, path).as_posix()
        for path in voice_files
    }
    external_paths: dict[Path, PurePosixPath] = {}

    def archive_path_for_resource(path: Path | None, kind: str) -> str | None:
        if path is None:
            return None
        archive_path = _voice_archive_path_for_profile_resource(
            profile.package_dir,
            path,
            kind=kind,
            voice_archive_names=voice_archive_names,
            external_paths=external_paths,
        )
        return archive_path.as_posix()

    voice_manifest: dict[str, Any] = {
        "tone_refs": archive_path_for_resource(profile.voice.tone_ref_path, "voice/refs"),
        "ref_lang": profile.voice.ref_lang,
        "text_lang": profile.voice.text_lang,
    }
    gpt_model = archive_path_for_resource(profile.voice.gpt_model_path, "voice/models")
    sovits_model = archive_path_for_resource(profile.voice.sovits_model_path, "voice/models")
    if gpt_model is not None:
        voice_manifest["gpt_model"] = gpt_model
    if sovits_model is not None:
        voice_manifest["sovits_model"] = sovits_model

    archive_manifest = {
        "format": VOICE_ARCHIVE_FORMAT,
        "version": VOICE_ARCHIVE_VERSION,
        "voice": voice_manifest,
    }

    temp_output = destination.with_name(f".{destination.name}.tmp")
    try:
        with zipfile.ZipFile(temp_output, "w", zipfile.ZIP_DEFLATED) as zf:
            written: set[str] = set()
            for source in voice_files:
                _write_zip_file(zf, source, _voice_archive_path_for_package_file(profile.package_dir, source), written)
            for source, archive_path in external_paths.items():
                _write_zip_file(zf, source, archive_path, written)
            zf.writestr(
                ARCHIVE_MANIFEST,
                json.dumps(archive_manifest, ensure_ascii=False, indent=2),
            )
        replace_with_retry(temp_output, destination)
    finally:
        temp_output.unlink(missing_ok=True)


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    found_manifest = False
    for info in zf.infolist():
        member = str(info.filename or "").replace("\\", "/").rstrip("/")
        if not member:
            raise CharacterArchiveError("角色包包含空 ZIP 成员名。")
        rel = _safe_archive_path(member, "zip member")
        if _is_zip_symlink(info):
            raise CharacterArchiveError(f"角色包不允许包含符号链接：{member}")
        if rel == PurePosixPath(ARCHIVE_MANIFEST):
            found_manifest = True
            continue
        if rel.parts[0] != ARCHIVE_CHARACTER_ROOT.as_posix():
            raise CharacterArchiveError(f"角色包资源必须位于 character/ 下：{member}")
    if not found_manifest:
        raise CharacterArchiveError("角色包缺少 manifest.json。")


def _validate_voice_zip_members(zf: zipfile.ZipFile) -> None:
    found_manifest = False
    for info in zf.infolist():
        member = str(info.filename or "").replace("\\", "/").rstrip("/")
        if not member:
            raise CharacterArchiveError("语音包包含空 ZIP 成员名。")
        rel = _safe_archive_path(member, "zip member")
        if _is_zip_symlink(info):
            raise CharacterArchiveError(f"语音包不允许包含符号链接：{member}")
        if rel == PurePosixPath(ARCHIVE_MANIFEST):
            found_manifest = True
            continue
        if rel.parts[0] != VOICE_ARCHIVE_ROOT.as_posix():
            raise CharacterArchiveError(f"语音包资源必须位于 voice/ 下：{member}")
    if not found_manifest:
        raise CharacterArchiveError("语音包缺少 manifest.json。")


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = zf.read(ARCHIVE_MANIFEST)
    except KeyError as exc:
        raise CharacterArchiveError("角色包缺少 manifest.json。") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CharacterArchiveError("manifest.json 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(data, dict):
        raise CharacterArchiveError("manifest.json 必须是 JSON 对象。")
    return data


def _validated_character_data(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise CharacterArchiveError("不支持的角色包格式。")
    if manifest.get("version") != ARCHIVE_VERSION:
        raise CharacterArchiveError("不支持的角色包版本。")
    character_data = manifest.get("character")
    if not isinstance(character_data, dict):
        raise CharacterArchiveError("manifest.json 缺少 character 对象。")
    return dict(character_data)


def _validated_voice_data(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("format") != VOICE_ARCHIVE_FORMAT:
        raise CharacterArchiveError("不支持的语音包格式。")
    if manifest.get("version") != VOICE_ARCHIVE_VERSION:
        raise CharacterArchiveError("不支持的语音包版本。")
    voice_data = manifest.get("voice")
    if not isinstance(voice_data, dict):
        raise CharacterArchiveError("manifest.json 缺少 voice 对象。")
    return dict(voice_data)


def _normalized_import_character_data(
    character_data: dict[str, Any],
    *,
    character_id: str,
    display_name: str,
    package_dir: Path,
) -> dict[str, Any]:
    card = _package_path_text(_required_archive_resource(character_data, "card", "character.card"))
    portrait_data = _required_mapping(character_data, "portrait", "character.portrait")
    default_portrait = _package_path_text(
        _required_archive_resource(portrait_data, "default", "character.portrait.default")
    )
    expressions = _normalized_expressions(portrait_data.get("expressions", {}))

    normalized: dict[str, Any] = {
        "id": character_id,
        "display_name": display_name,
        "initial_message": _optional_text(character_data, "initial_message", "……起動した。用事があるなら、呼んで。"),
        "card": card,
        "portrait": {
            "default": default_portrait,
            "expressions": expressions,
        },
        "theme": _normalized_theme(character_data.get("theme")),
    }

    reply_data = character_data.get("reply")
    tones = _normalized_reply_tones(reply_data)
    if tones:
        normalized["reply"] = {"tones": tones}

    voice_data = character_data.get("voice")
    if voice_data is not None:
        normalized["voice"] = _normalized_voice(voice_data)

    _validate_referenced_files(package_dir, normalized)
    return normalized


def _normalized_expressions(raw_expressions: Any) -> dict[str, str]:
    if raw_expressions is None:
        return {}
    if not isinstance(raw_expressions, dict):
        raise CharacterArchiveError("character.portrait.expressions 必须是对象。")
    expressions: dict[str, str] = {}
    for label, path_text in raw_expressions.items():
        if not isinstance(label, str) or not label.strip():
            raise CharacterArchiveError("character.portrait.expressions 的标签必须是非空字符串。")
        expressions[label.strip()] = _package_path_text(
            _archive_resource_path(path_text, f"character.portrait.expressions.{label}")
        )
    return expressions


def _normalized_reply_tones(reply_data: Any) -> list[str]:
    if not isinstance(reply_data, dict):
        return []
    raw_tones = reply_data.get("tones")
    if not isinstance(raw_tones, list):
        return []
    return [tone.strip() for tone in raw_tones if isinstance(tone, str) and tone.strip()]


def _normalized_theme(theme_data: Any) -> dict[str, object]:
    theme_settings, theme_source, _missing = character_theme_from_mapping(theme_data)
    return character_theme_to_mapping(theme_settings, source=theme_source)


def _normalized_voice(voice_data: Any) -> dict[str, str]:
    if not isinstance(voice_data, dict):
        raise CharacterArchiveError("character.voice 必须是对象。")
    normalized: dict[str, str] = {
        "tone_refs": _package_path_text(
            _required_archive_resource(voice_data, "tone_refs", "character.voice.tone_refs")
        ),
        "ref_lang": _optional_text(voice_data, "ref_lang", "ja"),
        "text_lang": _optional_text(voice_data, "text_lang", "ja"),
    }
    for key in ("gpt_model", "sovits_model"):
        value = voice_data.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = _package_path_text(_archive_resource_path(value, f"character.voice.{key}"))
    return normalized


def _normalized_voice_archive(voice_data: Any) -> dict[str, str]:
    if not isinstance(voice_data, dict):
        raise CharacterArchiveError("voice 必须是对象。")
    normalized: dict[str, str] = {
        "tone_refs": _voice_package_path_text(
            _required_voice_resource(voice_data, "tone_refs", "voice.tone_refs")
        ),
        "ref_lang": _optional_text(voice_data, "ref_lang", "ja"),
        "text_lang": _optional_text(voice_data, "text_lang", "ja"),
    }
    for key in ("gpt_model", "sovits_model"):
        value = voice_data.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = _voice_package_path_text(_voice_resource_path(value, f"voice.{key}"))
    return normalized


def _validate_referenced_files(package_dir: Path, character_data: dict[str, Any]) -> None:
    paths = [
        ("角色卡", character_data["card"]),
        ("默认立绘", character_data["portrait"]["default"]),
    ]
    for label, path_text in character_data["portrait"].get("expressions", {}).items():
        paths.append((f"{label} 表情立绘", path_text))
    voice_data = character_data.get("voice")
    if isinstance(voice_data, dict):
        paths.append(("语气参考表", voice_data["tone_refs"]))
        for key, label in (("gpt_model", "GPT 模型"), ("sovits_model", "SoVITS 模型")):
            if key in voice_data:
                paths.append((label, voice_data[key]))
    for label, path_text in paths:
        path = package_dir / _safe_package_path(path_text, label)
        if not path.is_file():
            raise CharacterArchiveError(f"{label}不存在：{path}")


def _validate_voice_referenced_files(package_dir: Path, voice_data: dict[str, str]) -> None:
    paths = [("语气参考表", voice_data["tone_refs"])]
    for key, label in (("gpt_model", "GPT 模型"), ("sovits_model", "SoVITS 模型")):
        if key in voice_data:
            paths.append((label, voice_data[key]))
    for label, path_text in paths:
        path = package_dir / _safe_package_path(path_text, label)
        if not path.is_file():
            raise CharacterArchiveError(f"{label}不存在：{path}")


def _write_character_manifest(package_dir: Path, character_data: dict[str, Any]) -> None:
    (package_dir / "character.json").write_text(
        json.dumps(character_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_character_voice_manifest(package_dir: Path, voice_data: dict[str, str]) -> None:
    manifest_path = package_dir / "character.json"
    try:
        character_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterArchiveError(f"角色清单无法读取：{manifest_path}") from exc
    if not isinstance(character_data, dict):
        raise CharacterArchiveError(f"角色清单必须是 JSON 对象：{manifest_path}")
    character_data["voice"] = voice_data
    _write_character_manifest(package_dir, character_data)


def _package_character_data(character_manifest: dict[str, Any]) -> dict[str, Any]:
    portrait = _required_mapping(character_manifest, "portrait", "character.portrait")
    package_data: dict[str, Any] = {
        "id": _required_text(character_manifest, "id", "character.id"),
        "display_name": _required_text(character_manifest, "display_name", "character.display_name"),
        "initial_message": _optional_text(character_manifest, "initial_message", ""),
        "card": _package_path_text(_archive_resource_path(character_manifest.get("card"), "character.card")),
        "portrait": {
            "default": _package_path_text(
                _archive_resource_path(portrait.get("default"), "character.portrait.default")
            ),
            "expressions": {
                label: _package_path_text(_archive_resource_path(path_text, f"character.portrait.expressions.{label}"))
                for label, path_text in portrait.get("expressions", {}).items()
            },
        },
        "reply": character_manifest.get("reply", {}),
        "theme": character_manifest.get("theme", {}),
    }
    voice_data = character_manifest.get("voice")
    if isinstance(voice_data, dict):
        package_data["voice"] = _package_voice_data(voice_data)
    return package_data


def _package_voice_data(voice_data: dict[str, Any]) -> dict[str, str]:
    package_voice: dict[str, str] = {
        "tone_refs": _package_path_text(_archive_resource_path(voice_data.get("tone_refs"), "character.voice.tone_refs")),
        "ref_lang": _optional_text(voice_data, "ref_lang", "ja"),
        "text_lang": _optional_text(voice_data, "text_lang", "ja"),
    }
    for key in ("gpt_model", "sovits_model"):
        value = voice_data.get(key)
        if isinstance(value, str) and value.strip():
            package_voice[key] = _package_path_text(_archive_resource_path(value, f"character.voice.{key}"))
    return package_voice


def _required_mapping(data: dict[str, Any], key: str, field_name: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise CharacterArchiveError(f"{field_name} 必须是对象。")
    return dict(value)


def _required_archive_resource(data: dict[str, Any], key: str, field_name: str) -> PurePosixPath:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    return _archive_resource_path(value, field_name)


def _required_voice_resource(data: dict[str, Any], key: str, field_name: str) -> PurePosixPath:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    return _voice_resource_path(value, field_name)


def _archive_resource_path(value: Any, field_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    rel = _safe_archive_path(value.strip(), field_name)
    if rel.parts[0] != ARCHIVE_CHARACTER_ROOT.as_posix() or len(rel.parts) < 2:
        raise CharacterArchiveError(f"{field_name} 必须位于 character/ 下。")
    return rel


def _voice_resource_path(value: Any, field_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    rel = _safe_archive_path(value.strip(), field_name)
    if rel.parts[0] != VOICE_ARCHIVE_ROOT.as_posix() or len(rel.parts) < 2:
        raise CharacterArchiveError(f"{field_name} 必须位于 voice/ 下。")
    return rel


def _safe_archive_path(value: str, field_name: str) -> PurePosixPath:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw:
        raise CharacterArchiveError(f"{field_name} 不能为空。")
    if "\x00" in raw or raw.startswith("/") or _WINDOWS_DRIVE_RE.match(raw):
        raise CharacterArchiveError(f"{field_name} 必须是安全的相对路径：{value!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise CharacterArchiveError(f"{field_name} 包含不安全路径片段：{value!r}")
    return PurePosixPath(*parts)


def _safe_package_path(value: str, field_name: str) -> Path:
    rel = _safe_archive_path(value, field_name)
    if rel.parts and rel.parts[0] == ARCHIVE_CHARACTER_ROOT.as_posix():
        raise CharacterArchiveError(f"{field_name} 应为角色包内相对路径，不应包含 character/ 前缀。")
    return Path(*rel.parts)


def _package_path_text(archive_path: PurePosixPath) -> str:
    if archive_path.parts[0] != ARCHIVE_CHARACTER_ROOT.as_posix() or len(archive_path.parts) < 2:
        raise CharacterArchiveError(f"归档路径必须位于 character/ 下：{archive_path}")
    return PurePosixPath(*archive_path.parts[1:]).as_posix()


def _voice_package_path_text(archive_path: PurePosixPath) -> str:
    if archive_path.parts[0] != VOICE_ARCHIVE_ROOT.as_posix() or len(archive_path.parts) < 2:
        raise CharacterArchiveError(f"语音归档路径必须位于 voice/ 下：{archive_path}")
    return archive_path.as_posix()


def _required_character_id(data: dict[str, Any], field_name: str) -> str:
    value = data.get("id")
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    character_id = value.strip()
    if (
        "\x00" in character_id
        or "/" in character_id
        or "\\" in character_id
        or character_id in (".", "..")
        or _WINDOWS_DRIVE_RE.match(character_id)
        or not _SAFE_CHARACTER_ID_RE.match(character_id)
    ):
        raise CharacterArchiveError(f"{field_name} 只能包含字母、数字、下划线、点和横线。")
    return character_id


def _required_text(data: dict[str, Any], key: str, field_name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CharacterArchiveError(f"{field_name} 必须是非空字符串。")
    return value.strip()


def _optional_text(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _unique_character_id(character_id: str, characters_dir: Path) -> str:
    used = {path.name for path in characters_dir.iterdir() if path.is_dir()}
    if character_id not in used:
        return character_id
    index = 1
    while f"{character_id}_{index}" in used:
        index += 1
    return f"{character_id}_{index}"


def _unique_display_name(display_name: str, characters_dir: Path) -> str:
    used = _existing_display_names(characters_dir)
    if display_name not in used:
        return display_name
    index = 1
    while f"{display_name}（{index}）" in used:
        index += 1
    return f"{display_name}（{index}）"


def _existing_display_names(characters_dir: Path) -> set[str]:
    names: set[str] = set()
    for manifest_path in characters_dir.glob("*/character.json"):
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            name = raw.get("display_name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return names


def _archive_path_for_package_file(package_dir: Path, source: Path) -> PurePosixPath:
    rel = _resolved(source).relative_to(_resolved(package_dir))
    archive_path = PurePosixPath(ARCHIVE_CHARACTER_ROOT.as_posix(), *rel.parts)
    _safe_archive_path(archive_path.as_posix(), "archive path")
    return archive_path


def _voice_archive_path_for_package_file(package_dir: Path, source: Path) -> PurePosixPath:
    rel = _resolved(source).relative_to(_resolved(package_dir))
    archive_path = PurePosixPath(*rel.parts)
    _voice_resource_path(archive_path.as_posix(), "voice archive path")
    return archive_path


def _is_voice_package_file(package_dir: Path, source: Path) -> bool:
    try:
        rel = _resolved(source).relative_to(_resolved(package_dir))
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == VOICE_ARCHIVE_ROOT.as_posix()


def _archive_path_for_profile_resource(
    package_dir: Path,
    source: Path,
    *,
    kind: str,
    package_archive_names: set[str],
    external_paths: dict[Path, PurePosixPath],
) -> PurePosixPath:
    resolved_source = _resolved(source)
    package_root = _resolved(package_dir)
    try:
        resolved_source.relative_to(package_root)
    except ValueError:
        if not resolved_source.is_file():
            raise CharacterArchiveError(f"角色资源不存在：{source}")
        if resolved_source in external_paths:
            return external_paths[resolved_source]
        archive_path = _next_external_archive_path(
            resolved_source.name,
            kind=kind,
            used=package_archive_names | {path.as_posix() for path in external_paths.values()},
        )
        external_paths[resolved_source] = archive_path
        return archive_path
    return _archive_path_for_package_file(package_dir, resolved_source)


def _voice_archive_path_for_profile_resource(
    package_dir: Path,
    source: Path,
    *,
    kind: str,
    voice_archive_names: set[str],
    external_paths: dict[Path, PurePosixPath],
) -> PurePosixPath:
    resolved_source = _resolved(source)
    package_root = _resolved(package_dir)
    try:
        rel = resolved_source.relative_to(package_root)
    except ValueError:
        if not resolved_source.is_file():
            raise CharacterArchiveError(f"语音资源不存在：{source}")
        if resolved_source in external_paths:
            return external_paths[resolved_source]
        archive_path = _next_voice_external_archive_path(
            resolved_source.name,
            kind=kind,
            used=voice_archive_names | {path.as_posix() for path in external_paths.values()},
        )
        external_paths[resolved_source] = archive_path
        return archive_path
    if not rel.parts or rel.parts[0] != VOICE_ARCHIVE_ROOT.as_posix():
        if not resolved_source.is_file():
            raise CharacterArchiveError(f"语音资源不存在：{source}")
        if resolved_source in external_paths:
            return external_paths[resolved_source]
        archive_path = _next_voice_external_archive_path(
            resolved_source.name,
            kind=kind,
            used=voice_archive_names | {path.as_posix() for path in external_paths.values()},
        )
        external_paths[resolved_source] = archive_path
        return archive_path
    return _voice_archive_path_for_package_file(package_dir, resolved_source)


def _next_external_archive_path(filename: str, *, kind: str, used: set[str]) -> PurePosixPath:
    safe_name = _safe_filename(filename)
    base = PurePosixPath(ARCHIVE_CHARACTER_ROOT.as_posix(), kind, safe_name)
    if base.as_posix() not in used:
        return base
    stem = Path(safe_name).stem or "resource"
    suffix = Path(safe_name).suffix
    index = 1
    while True:
        candidate = PurePosixPath(
            ARCHIVE_CHARACTER_ROOT.as_posix(),
            kind,
            f"{stem}_{index}{suffix}",
        )
        if candidate.as_posix() not in used:
            return candidate
        index += 1


def _next_voice_external_archive_path(filename: str, *, kind: str, used: set[str]) -> PurePosixPath:
    safe_name = _safe_filename(filename)
    base = PurePosixPath(*_safe_archive_path(kind, "voice kind").parts, safe_name)
    _voice_resource_path(base.as_posix(), "voice external archive path")
    if base.as_posix() not in used:
        return base
    stem = Path(safe_name).stem or "resource"
    suffix = Path(safe_name).suffix
    index = 1
    while True:
        candidate = PurePosixPath(
            *_safe_archive_path(kind, "voice kind").parts,
            f"{stem}_{index}{suffix}",
        )
        _voice_resource_path(candidate.as_posix(), "voice external archive path")
        if candidate.as_posix() not in used:
            return candidate
        index += 1


def _safe_filename(filename: str) -> str:
    raw = str(filename or "").replace("\\", "/").strip()
    if not raw or "/" in raw or raw in (".", "..") or "\x00" in raw or _WINDOWS_DRIVE_RE.match(raw):
        raise CharacterArchiveError(f"资源文件名不安全：{filename!r}")
    return raw


def _write_zip_file(
    zf: zipfile.ZipFile,
    source: Path,
    archive_path: PurePosixPath,
    written: set[str],
) -> None:
    archive_name = archive_path.as_posix()
    _safe_archive_path(archive_name, "archive path")
    if archive_name in written:
        return
    if not source.is_file():
        raise CharacterArchiveError(f"角色资源不存在：{source}")
    zf.write(source, archive_name)
    written.add(archive_name)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _resolved(path: Path) -> Path:
    return path.resolve()
