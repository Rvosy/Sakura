from pathlib import Path

from tools.cleanup import run_cleanup


def test_cleanup_preview_does_not_create_storage(tmp_path: Path) -> None:
    assert run_cleanup(tmp_path, apply=False, out=lambda _: None) == []
    assert list(tmp_path.iterdir()) == []


def test_cleanup_preview_and_apply_keep_user_data_and_plugin_backups(tmp_path: Path) -> None:
    orphan = tmp_path / "app/__pycache__/removed.cpython-312.pyc"
    kept = {
        tmp_path / "plugins/migration-backups/previous/plugin.py": "plugin backup",
        tmp_path / "data/chat_history/timeline.sqlite3": "chat history",
        tmp_path / "characters/role.char": "character",
        tmp_path / "config/system_config.yaml": "configuration",
        tmp_path / "app/kept.py": "source code",
    }
    for path, contents in {orphan: "bytecode", **kept}.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    preview = run_cleanup(tmp_path, apply=False, out=lambda _: None)

    assert {item.path for item in preview} == {orphan.parent}
    assert orphan.read_text(encoding="utf-8") == "bytecode"
    assert {path: path.read_text(encoding="utf-8") for path in kept} == kept

    run_cleanup(tmp_path, apply=True, out=lambda _: None)

    assert not orphan.parent.exists()
    assert {path: path.read_text(encoding="utf-8") for path in kept} == kept
