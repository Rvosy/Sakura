from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.config.character_archive import (
    ARCHIVE_FORMAT,
    ARCHIVE_VERSION,
    VOICE_ARCHIVE_FORMAT,
    VOICE_ARCHIVE_VERSION,
    read_character_archive_id,
)
from app.config.character_loader import CharacterRegistry
from app.config.seed_characters import (
    discover_seed_pack_directories,
    discover_seed_roots,
    import_seed_characters,
)
from app.config.settings_service import AppSettingsService


def test_discover_seed_roots_accepts_canonical_and_typo_names(tmp_path: Path) -> None:
    (tmp_path / "base_characters").mkdir()
    (tmp_path / "base_charaters").mkdir()
    (tmp_path / "other").mkdir()

    roots = discover_seed_roots(tmp_path)

    assert [path.name for path in roots] == ["base_characters", "base_charaters"]


def test_discover_pack_directories_requires_char_files_in_the_same_folder(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "base_characters"
    valid = seed / "Navi"
    nested = seed / "角色包" / "Sakura"
    notes = seed / "角色包"
    json_only = seed / "夜乃桜"
    valid.mkdir(parents=True)
    nested.mkdir(parents=True)
    json_only.mkdir()
    _write_character_archive(valid / "navi.card.char", "navi")
    _write_character_archive(nested / "sakura.char", "sakura")
    (notes / "readme.txt").write_text("notes", encoding="utf-8")
    (json_only / "tavern.json").write_text("{}", encoding="utf-8")

    packs = discover_seed_pack_directories(seed)

    assert {path.name for path in packs} == {"Navi", "Sakura"}


def test_import_seed_characters_loads_char_and_voice_into_settings(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    pack = distribution / "base_characters" / "Navi"
    pack.mkdir(parents=True)
    _write_character_archive(pack / "navi.card.char", "navi", display_name="N.A.V.I")
    _write_voice_archive(pack / "navi.voice")
    issues: list[tuple[object, ...]] = []

    imported = import_seed_characters(
        distribution,
        user,
        issue_sink=lambda *args: issues.append(args),
    )

    assert len(imported) == 1
    assert imported[0].character_id == "navi"
    assert imported[0].imported_voice is True
    profile = CharacterRegistry(user).get("navi")
    assert profile.display_name == "N.A.V.I"
    assert profile.voice is not None
    assert AppSettingsService(user).load_current_character_id(CharacterRegistry(user)) == "navi"
    assert any(
        args[2].get("reason_code") == "SEED_CHARACTER_IMPORTED" for args in issues
    )


def test_split_seed_pack_replaces_combined_archive_with_the_same_identity(
    tmp_path: Path,
) -> None:
    from app.config.character_archive import import_character_archive

    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    old = distribution / "base_characters" / "夜乃桜"
    newer = distribution / "base_characters" / "角色包" / "夜乃樱"
    old.mkdir(parents=True)
    newer.mkdir(parents=True)
    _write_character_archive(old / "sakura.char", "sakura", display_name="夜乃桜")
    _write_character_archive(newer / "Sakura.card.char", "Sakura", display_name="夜乃桜")
    _write_voice_archive(newer / "Sakura.voice")
    import_character_archive(old / "sakura.char", user)
    AppSettingsService(user).save_current_character_id(CharacterRegistry(user), "sakura")
    assert list(CharacterRegistry(user).profiles) == ["sakura"]

    imported = import_seed_characters(distribution, user, issue_sink=_silent)

    assert [item.character_id for item in imported] == ["Sakura"]
    assert imported[0].imported_voice is True
    registry = CharacterRegistry(user)
    assert list(registry.profiles) == ["Sakura"]
    assert registry.get("Sakura").display_name == "夜乃桜"
    assert registry.get("Sakura").voice is not None
    assert AppSettingsService(user).load_current_character_id(registry) == "Sakura"


def test_split_seed_pack_wins_over_a_combined_copy_in_the_same_tree(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    old = distribution / "base_characters" / "夜乃桜"
    newer = distribution / "base_characters" / "角色包" / "夜乃樱"
    old.mkdir(parents=True)
    newer.mkdir(parents=True)
    _write_character_archive(old / "sakura.char", "sakura", display_name="夜乃桜")
    _write_character_archive(newer / "Sakura.card.char", "Sakura", display_name="夜乃桜")
    _write_voice_archive(newer / "Sakura.voice")

    imported = import_seed_characters(distribution, user, issue_sink=_silent)

    assert [item.character_id for item in imported] == ["Sakura"]
    assert list(CharacterRegistry(user).profiles) == ["Sakura"]


def test_import_seed_characters_is_idempotent_and_skips_duplicate_ids(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    first = distribution / "base_characters" / "Sakura"
    duplicate = distribution / "base_characters" / "SakuraCopy"
    first.mkdir(parents=True)
    duplicate.mkdir()
    _write_character_archive(first / "sakura.char", "sakura")
    _write_character_archive(duplicate / "sakura.char", "sakura")

    first_pass = import_seed_characters(distribution, user, issue_sink=_silent)
    second_pass = import_seed_characters(distribution, user, issue_sink=_silent)

    assert [item.character_id for item in first_pass] == ["sakura"]
    assert second_pass == ()
    assert list(CharacterRegistry(user).profiles) == ["sakura"]


def test_import_seed_characters_attaches_voice_left_behind_on_a_later_scan(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    pack = distribution / "base_charaters" / "Navi"
    pack.mkdir(parents=True)
    _write_character_archive(pack / "navi.card.char", "navi")

    import_seed_characters(distribution, user, issue_sink=_silent)
    assert CharacterRegistry(user).get("navi").voice is None

    _write_voice_archive(pack / "navi.voice")
    imported = import_seed_characters(distribution, user, issue_sink=_silent)

    assert len(imported) == 1
    assert imported[0].imported_voice is True
    assert CharacterRegistry(user).get("navi").voice is not None


def test_import_prefers_card_archive_when_a_voice_pack_is_present(tmp_path: Path) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    pack = distribution / "base_characters" / "Hero"
    pack.mkdir(parents=True)
    _write_character_archive(pack / "hero.char", "fullpack")
    _write_character_archive(pack / "hero.card.char", "cardpack")
    _write_voice_archive(pack / "hero.voice")

    imported = import_seed_characters(distribution, user, issue_sink=_silent)

    assert [item.character_id for item in imported] == ["cardpack"]
    assert "fullpack" not in CharacterRegistry(user).profiles


def test_invalid_seed_pack_does_not_block_siblings(tmp_path: Path) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    seed = distribution / "base_characters"
    broken = seed / "Broken"
    valid = seed / "Valid"
    broken.mkdir(parents=True)
    valid.mkdir()
    (broken / "broken.char").write_text("not a zip", encoding="utf-8")
    _write_character_archive(valid / "valid.char", "valid")
    issues: list[tuple[object, ...]] = []

    imported = import_seed_characters(
        distribution,
        user,
        issue_sink=lambda *args: issues.append(args),
    )

    assert [item.character_id for item in imported] == ["valid"]
    assert any(
        args[2].get("reason_code") == "SEED_CHARACTER_ARCHIVE_INVALID" for args in issues
    )


def test_missing_seed_folder_is_a_noop(tmp_path: Path) -> None:
    assert import_seed_characters(tmp_path / "repo", tmp_path / "user", issue_sink=_silent) == ()
    assert CharacterRegistry(tmp_path / "user").all() == []


def test_read_character_archive_id_does_not_require_extracting_resources(
    tmp_path: Path,
) -> None:
    archive = _write_character_archive(tmp_path / "demo.char", "demo")
    assert read_character_archive_id(archive) == "demo"


def test_existing_current_character_is_not_replaced(tmp_path: Path) -> None:
    distribution = tmp_path / "repo"
    user = tmp_path / "user"
    installed = user / "characters" / "keep"
    installed.mkdir(parents=True)
    (installed / "card.md").write_text("prompt", encoding="utf-8")
    (installed / "portrait.png").write_bytes(b"png")
    (installed / "character.json").write_text(
        json.dumps(
            {
                "id": "keep",
                "display_name": "Keep",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    AppSettingsService(user).save_current_character_id(CharacterRegistry(user), "keep")
    pack = distribution / "base_characters" / "New"
    pack.mkdir(parents=True)
    _write_character_archive(pack / "new.char", "new")

    import_seed_characters(distribution, user, issue_sink=_silent)

    assert AppSettingsService(user).load_current_character_id(CharacterRegistry(user)) == "keep"
    assert set(CharacterRegistry(user).profiles) == {"keep", "new"}


def _write_character_archive(
    path: Path,
    character_id: str,
    *,
    display_name: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": character_id,
                        "display_name": display_name or character_id,
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                    },
                }
            ),
        )
        archive.writestr("character/card.md", "system prompt")
        archive.writestr("character/portrait.png", b"portrait")
    return path


def _write_voice_archive(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": VOICE_ARCHIVE_FORMAT,
                    "version": VOICE_ARCHIVE_VERSION,
                    "voice": {
                        "tone_refs": "voice/refs/ref.txt",
                        "gpt_model": "voice/models/gpt.ckpt",
                        "sovits_model": "voice/models/sovits.pth",
                        "ref_lang": "ja",
                        "text_lang": "ja",
                    },
                },
                ensure_ascii=False,
            ),
        )
        archive.writestr("voice/models/gpt.ckpt", b"gpt")
        archive.writestr("voice/models/sovits.pth", b"sovits")
        archive.writestr("voice/refs/tone_refs/neutral.wav", b"wav")
        archive.writestr(
            "voice/refs/ref.txt",
            "voice/refs/tone_refs/neutral.wav|JA|hello|中性\n",
        )
    return path


def _silent(*_args: object) -> None:
    return None
