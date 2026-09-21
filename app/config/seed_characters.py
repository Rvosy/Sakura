"""Import developer seed character packs sitting next to the repository.

``base_characters`` (and the historical typo ``base_charaters``) may contain
subdirectories that already hold Sakura ``.char`` archives. Core scans those
directories on startup, including nested folders such as ``角色包/``. A folder
qualifies only when the needed ``.char`` / ``.card.char`` files sit directly in
that directory.

After 0.9.5, portraits and voice are separate. When both a combined ``.char`` and
a split ``.card.char`` + ``.voice`` pack describe the same character, the split
pack wins. This is not a packaged default character: official releases still
ship empty ``user_root/characters``.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config.character_archive import (
    CharacterArchiveError,
    import_character_archive,
    import_character_voice_archive,
    read_character_archive_identity,
)
from app.config.character_loader import CharacterConfigError, CharacterProfile, CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.core.runtime_log import log_event


IssueSink = Callable[[str, str, dict[str, object]], None]

SEED_DIRECTORY_NAMES = ("base_characters", "base_charaters")


@dataclass(frozen=True)
class SeedCharacterImport:
    source_dir: Path
    character_id: str
    display_name: str
    imported_voice: bool


@dataclass(frozen=True)
class SeedPackCandidate:
    directory: Path
    archive: Path
    voice: Path | None
    character_id: str
    display_name: str
    score: int


def discover_seed_roots(distribution_root: Path) -> tuple[Path, ...]:
    """Return existing seed folders under the Core distribution root."""

    root = Path(distribution_root)
    found: list[Path] = []
    seen: set[Path] = set()
    for name in SEED_DIRECTORY_NAMES:
        candidate = root / name
        if not _is_real_directory(candidate):
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(candidate)
    return tuple(found)


def discover_seed_pack_directories(seed_root: Path) -> tuple[Path, ...]:
    """Find directories that themselves contain a ``.char`` file."""

    root = Path(seed_root)
    if not _is_real_directory(root):
        return ()
    packs: list[Path] = []

    def visit(directory: Path) -> None:
        if _direct_files_with_suffix(directory, ".char"):
            packs.append(directory)
            return
        for child in _child_directories(directory):
            visit(child)

    visit(root)
    return tuple(packs)


def import_seed_characters(
    distribution_root: Path,
    user_root: Path,
    *,
    issue_sink: IssueSink = log_event,
) -> tuple[SeedCharacterImport, ...]:
    """Import missing current-format seed packs into ``user_root/characters``.

    Combined pre-0.9.5 ``.char`` copies lose to a split card+voice pack with the
    same logical identity. Already-installed IDs stay in place unless they are
    that outdated identity of a split pack being imported.
    """

    user = Path(user_root)
    imported: list[SeedCharacterImport] = []
    seen_ids: set[str] = set()
    settings = AppSettingsService(user)
    current_was = settings.load_current_character_id(CharacterRegistry(user))
    select_after: str | None = None

    for candidate in _preferred_seed_packs(distribution_root, issue_sink):
        result, replaced_current = _import_candidate(
            candidate,
            user,
            seen_ids=seen_ids,
            current_character_id=current_was,
            issue_sink=issue_sink,
        )
        if result is None:
            continue
        seen_ids.add(result.character_id.casefold())
        imported.append(result)
        if replaced_current:
            select_after = result.character_id

    if select_after is not None:
        _select_character(user, select_after, issue_sink)
    elif imported:
        registry = CharacterRegistry(user)
        if current_was is None or current_was not in registry.profiles:
            _select_character(user, imported[0].character_id, issue_sink)
    return tuple(imported)


def _preferred_seed_packs(
    distribution_root: Path,
    issue_sink: IssueSink,
) -> tuple[SeedPackCandidate, ...]:
    winners: dict[str, SeedPackCandidate] = {}
    name_to_id: dict[str, str] = {}
    for seed_root in discover_seed_roots(distribution_root):
        for pack_dir in discover_seed_pack_directories(seed_root):
            candidate = _candidate_from_directory(pack_dir, issue_sink)
            if candidate is None:
                continue
            identity = candidate.character_id.casefold()
            display_key = candidate.display_name.casefold()
            existing_identity = name_to_id.get(display_key, identity)
            current = winners.get(existing_identity)
            if current is not None and current.score >= candidate.score:
                continue
            if current is not None and existing_identity != identity:
                winners.pop(existing_identity, None)
            winners[identity] = candidate
            if display_key:
                name_to_id[display_key] = identity
    return tuple(winners.values())


def _candidate_from_directory(
    pack_dir: Path,
    issue_sink: IssueSink,
) -> SeedPackCandidate | None:
    char_files = _direct_files_with_suffix(pack_dir, ".char")
    if not char_files:
        return None
    voice_files = _direct_files_with_suffix(pack_dir, ".voice")
    archive = _select_character_archive(char_files, voice_files)
    try:
        character_id, display_name = read_character_archive_identity(archive)
    except (OSError, CharacterArchiveError, UnicodeDecodeError, ValueError) as error:
        _report(
            issue_sink,
            "种子角色包无法读取",
            "SEED_CHARACTER_ARCHIVE_INVALID",
            pack_dir,
            error,
        )
        return None
    score = 0
    if voice_files:
        score += 4
    if _is_card_archive_name(archive):
        score += 2
    return SeedPackCandidate(
        directory=pack_dir,
        archive=archive,
        voice=voice_files[0] if voice_files else None,
        character_id=character_id,
        display_name=display_name,
        score=score,
    )


def _import_candidate(
    candidate: SeedPackCandidate,
    user_root: Path,
    *,
    seen_ids: set[str],
    current_character_id: str | None,
    issue_sink: IssueSink,
) -> tuple[SeedCharacterImport | None, bool]:
    identity = candidate.character_id.casefold()
    if identity in seen_ids:
        return None, False
    registry = CharacterRegistry(user_root)
    existing = _installed_for_candidate(registry, candidate)
    replaced_current = False
    if existing is not None:
        if existing.id != candidate.character_id and candidate.score >= 2:
            replaced_current = current_character_id == existing.id
            try:
                _remove_installed_character(user_root, existing)
            except (OSError, CharacterConfigError, ValueError) as error:
                _report(
                    issue_sink,
                    "过期种子角色无法替换",
                    "SEED_CHARACTER_REPLACE_FAILED",
                    candidate.directory,
                    error,
                )
                return None, False
            existing = None
        else:
            if candidate.voice is None or existing.voice is not None:
                return None, False
            imported_voice = _import_voice(
                candidate.voice,
                user_root,
                existing.id,
                candidate.directory,
                issue_sink,
            )
            if not imported_voice:
                return None, False
            return (
                SeedCharacterImport(
                    source_dir=candidate.directory,
                    character_id=existing.id,
                    display_name=existing.display_name,
                    imported_voice=True,
                ),
                False,
            )

    try:
        character = import_character_archive(candidate.archive, user_root)
    except (OSError, CharacterArchiveError, CharacterConfigError, ValueError) as error:
        _report(
            issue_sink,
            "种子角色包导入失败",
            "SEED_CHARACTER_IMPORT_FAILED",
            candidate.directory,
            error,
        )
        return None, False

    imported_voice = False
    if candidate.voice is not None:
        imported_voice = _import_voice(
            candidate.voice,
            user_root,
            character.character_id,
            candidate.directory,
            issue_sink,
        )

    issue_sink(
        "Character",
        "已导入种子角色包",
        {
            "reason_code": "SEED_CHARACTER_IMPORTED",
            "character_id": character.character_id,
            "source_dir": str(candidate.directory),
            "imported_voice": imported_voice,
        },
    )
    return (
        SeedCharacterImport(
            source_dir=candidate.directory,
            character_id=character.character_id,
            display_name=character.display_name,
            imported_voice=imported_voice,
        ),
        replaced_current,
    )


def _installed_for_candidate(
    registry: CharacterRegistry,
    candidate: SeedPackCandidate,
) -> CharacterProfile | None:
    if candidate.character_id in registry.profiles:
        return registry.profiles[candidate.character_id]
    identity = candidate.character_id.casefold()
    display = candidate.display_name.casefold()
    for profile in registry.profiles.values():
        if profile.id.casefold() == identity:
            return profile
        if display and profile.display_name.casefold() == display:
            return profile
    return None


def _remove_installed_character(user_root: Path, profile: CharacterProfile) -> None:
    settings = AppSettingsService(user_root)
    data = load_yaml_mapping(settings.characters_config_path)
    if data.get("current_character_id") == profile.id:
        data["current_character_id"] = ""
    selections = data.get("visual_selections")
    if isinstance(selections, dict):
        selections.pop(profile.id, None)
        data["visual_selections"] = selections
    save_yaml_mapping(settings.characters_config_path, data)
    characters_dir = (user_root / "characters").resolve()
    package_dir = profile.package_dir.resolve()
    if package_dir.parent != characters_dir:
        raise CharacterConfigError(f"角色目录不在 characters 下：{package_dir}")
    shutil.rmtree(package_dir)


def _import_voice(
    archive: Path,
    user_root: Path,
    character_id: str,
    pack_dir: Path,
    issue_sink: IssueSink,
) -> bool:
    try:
        import_character_voice_archive(archive, user_root, character_id)
    except (OSError, CharacterArchiveError, CharacterConfigError, ValueError) as error:
        _report(
            issue_sink,
            "种子语音包导入失败",
            "SEED_CHARACTER_VOICE_IMPORT_FAILED",
            pack_dir,
            error,
        )
        return False
    return True


def _select_character(
    user_root: Path,
    character_id: str,
    issue_sink: IssueSink,
) -> None:
    settings = AppSettingsService(user_root)
    try:
        settings.save_current_character_id(CharacterRegistry(user_root), character_id)
    except (OSError, CharacterConfigError, ValueError) as error:
        _report(
            issue_sink,
            "种子角色无法设为当前角色",
            "SEED_CHARACTER_SELECT_FAILED",
            user_root,
            error,
        )


def _select_character_archive(char_files: list[Path], voice_files: list[Path]) -> Path:
    if len(char_files) == 1:
        return char_files[0]
    card_packs = [path for path in char_files if _is_card_archive_name(path)]
    if voice_files and card_packs:
        return card_packs[0]
    full_packs = [path for path in char_files if not _is_card_archive_name(path)]
    if full_packs:
        return full_packs[0]
    return char_files[0]


def _is_card_archive_name(path: Path) -> bool:
    return path.name.lower().endswith(".card.char")


def _direct_files_with_suffix(directory: Path, suffix: str) -> list[Path]:
    wanted = suffix.lower()
    files: list[Path] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for path in entries:
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() == wanted:
            files.append(path)
    return files


def _child_directories(directory: Path) -> list[Path]:
    children: list[Path] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for path in entries:
        if _is_real_directory(path):
            children.append(path)
    return children


def _is_real_directory(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _report(
    issue_sink: IssueSink,
    message: str,
    reason_code: str,
    source: Path,
    error: BaseException,
) -> None:
    issue_sink(
        "Character",
        message,
        {
            "reason_code": reason_code,
            "source_dir": str(source),
            "error": str(error),
        },
    )
