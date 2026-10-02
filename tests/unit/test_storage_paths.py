"""持久化文件名兼容、路径隔离与实际文件读写。"""
from pathlib import Path

import pytest

from app.storage.paths import StoragePaths, sanitize_file_stem


@pytest.mark.parametrize(("identity", "expected"), [
    ("角色-中文_01", "角色-中文_01"),
    ("N.A.V.I.", "N.A.V.I."),
    ("a/b\\c:d", "a_b_c_d"),
    ("a\x00b\x1fc", "a_b_c"),
    ("CON.backup", "_CON.backup"),
    ("  ", "_"),
])
def test_persisted_filename_mapping(identity: str, expected: str) -> None:
    assert sanitize_file_stem(identity) == expected


def test_existing_character_history_is_still_readable(tmp_path: Path) -> None:
    legacy = tmp_path / "data/chat_history/N.A.V.I..jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("既有历史", encoding="utf-8")
    assert StoragePaths(tmp_path).chat_history_for("N.A.V.I.").read_text(encoding="utf-8") == "既有历史"


def test_long_ids_do_not_overwrite_each_other(tmp_path: Path) -> None:
    paths = StoragePaths(tmp_path)
    identities = ["a" * 300, "a" * 299 + "b"]
    for identity in identities:
        target = paths.chat_history_for(identity)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(identity, encoding="utf-8")
    for identity in identities:
        assert paths.chat_history_for(identity).read_text(encoding="utf-8") == identity


def test_voice_directories_distinguish_trailing_dot_ids(tmp_path: Path) -> None:
    paths = StoragePaths(tmp_path)
    identities = ["N.A.V.I.", "N.A.V.I"]
    for identity in identities:
        directory = paths.voice_recordings_for(identity)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "recording.wav").write_bytes(identity.encode())
    for identity in identities:
        assert (paths.voice_recordings_for(identity) / "recording.wav").read_bytes() == identity.encode()


def test_plugin_data_stays_inside_its_directory(tmp_path: Path) -> None:
    paths = StoragePaths(tmp_path)
    target = paths.plugin_data_for("../another/plugin")
    target.mkdir(parents=True)
    assert target.resolve().parent == paths.plugins_data_dir.resolve()
