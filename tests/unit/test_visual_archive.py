import json
import zipfile

import pytest

from app.config.character_archive import ARCHIVE_FORMAT, CharacterArchiveError
from app.config.character_resources import CharacterVisualResource
from app.config.visual_archive import export_visual_archive, import_visual_archive


def component(path, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": ARCHIVE_FORMAT, "version": 2, "kind": "resource", "resource": {"type": "example.numeric@1", "entry": "resource.json"}}))
        archive.writestr("resource/resource.json", '{"Private_Key": 12}')
        if extra:
            archive.writestr(*extra)
    return path


@pytest.mark.parametrize("name", ["../escape", "resource/../../escape", "resource/RESOURCE.JSON"])
def test_invalid_component_never_changes_destination(tmp_path, name):
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(CharacterArchiveError):
        import_visual_archive(component(tmp_path / "bad.char", (name, "bad")), package)
    assert list(package.iterdir()) == []


def test_import_cancel_at_commit_rolls_back_staging(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    def cancel():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        import_visual_archive(component(tmp_path / "valid.char"), package, commit_started=cancel)
    assert not any(path.is_file() for path in package.rglob("*"))
    assert not list(package.glob(".visual-import-*"))


def test_export_cancel_preserves_existing_destination(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    output = tmp_path / "existing.char"
    output.write_bytes(b"existing archive")
    def cancel():
        raise RuntimeError("cancelled")
    resource = CharacterVisualResource("numeric", "example.numeric@1", ".", "resource.json")
    with pytest.raises(RuntimeError, match="cancelled"):
        export_visual_archive(package, resource, {"entry": "resource.json", "data": {}, "assets": {}}, output, commit_started=cancel)
    assert output.read_bytes() == b"existing archive"
    assert not list(tmp_path.glob("*.partial"))


def test_import_rejects_parent_link_outside_package(tmp_path):
    package, outside = tmp_path / "package", tmp_path / "outside"
    package.mkdir()
    outside.mkdir()
    try:
        (package / "visuals").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 会话没有创建符号链接的权限")
    with pytest.raises(CharacterArchiveError):
        import_visual_archive(component(tmp_path / "valid.char"), package)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["", "日常形态"])
def test_component_roundtrip_keeps_name_and_private_data(tmp_path, name):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    resource = CharacterVisualResource("numeric", "example.numeric@1", ".", "resource.json", name)
    data = {"Private_Key": 12}
    output = export_visual_archive(source, resource, {"entry": "resource.json", "data": data, "assets": {}}, tmp_path / "shape.char")
    imported = import_visual_archive(output, target)
    assert imported.name == name
    assert imported.id != resource.id
    assert imported.type == resource.type
    assert json.loads((target / imported.root / imported.entry).read_text(encoding="utf-8")) == data
