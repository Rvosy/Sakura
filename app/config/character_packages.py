"""Character package filesystem naming and compatibility repair."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.runtime_log import log_event
from app.storage.atomic import atomic_write_text, rename_with_retry
from app.storage.paths import sanitize_directory_component, sanitize_file_stem


IssueSink = Callable[[str, str, dict[str, object]], None]


@dataclass(frozen=True)
class CharacterPackageRepair:
    source_name: str
    target_name: str
    character_id: str
    repaired_voice_extension: bool


@dataclass(frozen=True)
class _PackageEntry:
    name: str
    path: Path
    manifest: Path
    data: dict[str, Any] | None


def allocate_character_installation(
    characters_dir: Path,
    requested_id: str,
) -> tuple[str, Path]:
    """Allocate a logical ID and a portable physical package directory."""

    root = Path(characters_dir)
    entries = _package_entries(root)
    used_ids = {
        str(entry.data.get("id")).strip()
        for entry in entries
        if isinstance(entry.data, dict)
        and isinstance(entry.data.get("id"), str)
        and str(entry.data.get("id")).strip()
    }
    used_names = {_portable_name_key(entry.name) for entry in entries}
    candidate = _unique_character_id(requested_id, used_ids)
    while True:
        directory_name = sanitize_directory_component(candidate)
        if _portable_name_key(directory_name) not in used_names:
            return candidate, root / directory_name
        used_ids.add(candidate)
        candidate = _unique_character_id(requested_id, used_ids)


def ensure_legacy_voice_extensions(
    manifest: dict[str, Any],
    package_dir: Path,
) -> bool:
    """Upgrade a pre-Plugin-Runtime ``voice`` block without overriding extensions."""

    raw_extensions = manifest.get("extensions")
    if raw_extensions is not None and not isinstance(raw_extensions, dict):
        return False
    extensions = dict(raw_extensions or {})
    voice = manifest.get("voice")
    if not isinstance(voice, Mapping):
        voice = {}
    tone_refs = voice.get("tone_refs")
    existing_gpt = extensions.get("sakura.tts.gpt-sovits")
    if not isinstance(tone_refs, str) or not tone_refs.strip():
        if not isinstance(existing_gpt, Mapping) or not existing_gpt:
            return False
        tone_refs = "voice/refs/ref.txt"

    common: dict[str, object] = {
        "toneRefs": tone_refs.strip(),
        "refLang": str(voice.get("ref_lang") or "ja"),
    }
    gpt_provider = {
        **common,
        "textLang": str(voice.get("text_lang") or "ja"),
    }
    for source_key, target_key in (
        ("gpt_model", "gptModel"),
        ("sovits_model", "sovitsModel"),
    ):
        value = voice.get(source_key)
        if isinstance(value, str) and value.strip():
            gpt_provider[target_key] = value.strip()
    defaults = {
        "sakura.tts": {
            "enabled": True,
            "provider": "sakura.tts.gpt-sovits",
        },
        "sakura.tts.gpt-sovits": gpt_provider,
        # Genie inherits shared resources at read time. Copying model paths here
        # would turn them into overrides that become stale after Studio edits.
        "sakura.tts.genie": {},
    }
    for plugin_id, default in defaults.items():
        extensions.setdefault(plugin_id, default)
    if extensions == raw_extensions:
        return False
    manifest["extensions"] = extensions
    return True


def repair_character_packages(
    base_dir: Path,
    *,
    issue_sink: IssueSink = log_event,
) -> tuple[CharacterPackageRepair, ...]:
    """Repair Windows-unaddressable package names and legacy voice manifests.

    The migration is intentionally narrow: only valid character manifests are
    moved, logical IDs are preserved unless another accessible package already
    owns that ID, and every manifest rewrite keeps a backup.
    """

    characters_dir = Path(base_dir) / "characters"
    if not characters_dir.is_dir():
        return ()

    repairs: list[CharacterPackageRepair] = []
    if os.name == "nt":
        repairs.extend(_repair_windows_directory_names(characters_dir, issue_sink))

    moved_targets = {repair.target_name for repair in repairs}
    for entry in _package_entries(characters_dir):
        if entry.data is None:
            continue
        changed = ensure_legacy_voice_extensions(entry.data, entry.path)
        if not changed:
            continue
        try:
            atomic_write_text(
                entry.manifest,
                json.dumps(entry.data, ensure_ascii=False, indent=2) + "\n",
                backup=True,
            )
        except Exception as error:
            _report_repair_failure(issue_sink, entry.name, error, "voice_extension")
            continue
        if entry.name not in moved_targets:
            repairs.append(
                CharacterPackageRepair(
                    source_name=entry.name,
                    target_name=entry.name,
                    character_id=str(entry.data.get("id") or ""),
                    repaired_voice_extension=True,
                )
            )
        issue_sink(
            "Character",
            "已补齐旧角色语音插件配置",
            {
                "character_id": str(entry.data.get("id") or ""),
                "reason_code": "CHARACTER_LEGACY_VOICE_UPGRADED",
            },
        )
    return tuple(repairs)


def _repair_windows_directory_names(
    characters_dir: Path,
    issue_sink: IssueSink,
) -> list[CharacterPackageRepair]:
    entries = _package_entries(characters_dir)
    unsafe = [entry for entry in entries if entry.name.rstrip(" .") != entry.name]
    safe = [entry for entry in entries if entry not in unsafe]
    used_ids = {
        str(entry.data.get("id")).strip()
        for entry in safe
        if isinstance(entry.data, dict)
        and isinstance(entry.data.get("id"), str)
        and str(entry.data.get("id")).strip()
    }
    used_names = {_portable_name_key(entry.name) for entry in safe}
    repairs: list[CharacterPackageRepair] = []

    for entry in unsafe:
        data = entry.data
        if not isinstance(data, dict):
            continue
        raw_id = data.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        original_id = raw_id.strip()
        character_id = _unique_character_id(original_id, used_ids)
        while True:
            target_name = sanitize_directory_component(character_id)
            if _portable_name_key(target_name) not in used_names:
                break
            used_ids.add(character_id)
            character_id = _unique_character_id(original_id, used_ids)

        target = characters_dir / target_name
        voice_changed = ensure_legacy_voice_extensions(data, target)
        id_changed = character_id != original_id
        if id_changed:
            data["id"] = character_id
        try:
            rename_with_retry(entry.path, target)
            if id_changed or voice_changed:
                try:
                    atomic_write_text(
                        target / "character.json",
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        backup=True,
                    )
                except Exception:
                    rename_with_retry(target, entry.path)
                    raise
        except Exception as error:
            _report_repair_failure(issue_sink, entry.name, error, "directory_name")
            continue

        used_ids.add(character_id)
        used_names.add(_portable_name_key(target_name))
        repairs.append(
            CharacterPackageRepair(
                source_name=entry.name,
                target_name=target_name,
                character_id=character_id,
                repaired_voice_extension=voice_changed,
            )
        )
        issue_sink(
            "Character",
            "已修复 Windows 无法访问的角色目录",
            {
                "source_name": entry.name,
                "target_name": target_name,
                "character_id": character_id,
                "reason_code": "CHARACTER_DIRECTORY_REPAIRED",
            },
        )
    return repairs


def _package_entries(characters_dir: Path) -> list[_PackageEntry]:
    root = Path(characters_dir)
    if not root.is_dir():
        return []
    entries: list[_PackageEntry] = []
    try:
        with os.scandir(root) as iterator:
            scanned = sorted(iterator, key=lambda item: item.name.casefold())
    except OSError:
        return []
    for item in scanned:
        try:
            if not item.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        package_path = _entry_path(root, item.name)
        manifest = package_path / "character.json"
        data: dict[str, Any] | None = None
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        entries.append(_PackageEntry(item.name, package_path, manifest, data))
    return entries


def _entry_path(root: Path, name: str) -> Path:
    path = root / name
    if os.name != "nt" or name.rstrip(" .") == name:
        return path
    return Path(_windows_verbatim_path(path))


def _windows_verbatim_path(path: Path) -> str:
    text = str(Path(path).absolute())
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _unique_character_id(requested_id: str, used_ids: set[str]) -> str:
    used_casefold = {value.casefold() for value in used_ids}
    used_storage = {sanitize_file_stem(value).casefold() for value in used_ids}
    candidate = requested_id
    index = 1
    while (
        candidate.casefold() in used_casefold
        or sanitize_file_stem(candidate).casefold() in used_storage
    ):
        candidate = f"{requested_id}_{index}"
        index += 1
    return candidate


def _portable_name_key(name: str) -> str:
    return str(name).rstrip(" .").casefold()


def _report_repair_failure(
    issue_sink: IssueSink,
    directory_name: str,
    error: Exception,
    stage: str,
) -> None:
    issue_sink(
        "Character",
        "角色包兼容修复失败",
        {
            "directory_name": directory_name,
            "stage": stage,
            "error_type": type(error).__name__,
            "winerror": getattr(error, "winerror", None),
            "reason_code": "CHARACTER_PACKAGE_REPAIR_FAILED",
        },
    )
