import os
import time
from pathlib import Path

from tools.cleanup import run_cleanup


def test_cleanup_preview_does_not_create_storage(tmp_path: Path) -> None:
    assert run_cleanup(tmp_path, apply=False, out=lambda _: None) == []
    assert list(tmp_path.iterdir()) == []


def test_cleanup_preview_and_apply_keep_user_data_and_current_backups(tmp_path: Path) -> None:
    orphan = tmp_path / "app/__pycache__/removed.cpython-312.pyc"
    expired = tmp_path / "data/migration_backup/expired/notes.json"
    kept = {
        tmp_path / "data/migration_backup/recent/notes.json": "recent backup",
        tmp_path / ".legacy-import-backup-pending/data/notes/note.json": "pending import",
        tmp_path / "plugins/migration-backups/previous/plugin.py": "plugin backup",
        tmp_path / "data/chat_history/timeline.sqlite3": "chat history",
        tmp_path / "characters/role.char": "character",
        tmp_path / "config/system_config.yaml": "configuration",
        tmp_path / "app/kept.py": "source code",
    }
    for path, contents in {orphan: "bytecode", expired: "old backup", **kept}.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    old = time.time() - 31 * 86400
    for path in (expired.parent, tmp_path / ".legacy-import-backup-pending", tmp_path / "plugins/migration-backups/previous"):
        os.utime(path, (old, old))

    preview = run_cleanup(tmp_path, apply=False, out=lambda _: None)

    assert {item.path for item in preview} == {orphan.parent, expired.parent}
    assert orphan.read_text(encoding="utf-8") == "bytecode"
    assert expired.read_text(encoding="utf-8") == "old backup"
    assert {path: path.read_text(encoding="utf-8") for path in kept} == kept

    run_cleanup(tmp_path, apply=True, out=lambda _: None)

    assert not orphan.parent.exists()
    assert not expired.parent.exists()
    assert {path: path.read_text(encoding="utf-8") for path in kept} == kept
