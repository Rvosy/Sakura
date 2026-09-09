import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_release


def test_deployment_package_contains_size_inventory(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "dashboard/dist").mkdir(parents=True)
    (source / "dashboard/dist/index.html").write_text("<html></html>")
    (source / "app.py").write_text("pass\n")
    (source / "requirements.txt").write_text("")
    (source / "README.md").write_text("server\n")
    monkeypatch.setattr(package_release, "__file__", str(source / "package_release.py"))
    destination = tmp_path / "release.tar.gz"
    result = package_release.package(destination)
    assert set(result) == {"archive", "bytes", "files"}
    assert result["bytes"] == destination.stat().st_size
    with tarfile.open(destination) as archive:
        manifest = json.load(archive.extractfile("release-manifest.json"))
        assert manifest["format"] == 3
        assert manifest["productionDeployed"] is False
        for name, info in manifest["files"].items():
            assert info == {"bytes": archive.getmember(name).size}
