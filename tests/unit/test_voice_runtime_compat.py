from pathlib import Path

from app.storage.paths import user_facing_path


def test_user_facing_path_removes_windows_verbatim_prefixes() -> None:
    assert user_facing_path(Path(r"\\?\D:\Project\sakura\tts\g50")) == (
        r"D:\Project\sakura\tts\g50"
    )
    assert user_facing_path(r"\\?\UNC\server\share\tts") == r"\\server\share\tts"
    assert user_facing_path(r"D:\Project\sakura\tts\cpu") == (
        r"D:\Project\sakura\tts\cpu"
    )
    assert user_facing_path("/Users/sakura/Library/Application Support/Sakura") == (
        "/Users/sakura/Library/Application Support/Sakura"
    )
