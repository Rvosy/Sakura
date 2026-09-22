import importlib.util
import os
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "site_deploy", Path(__file__).resolve().parents[1] / "site/deploy_site.py"
)
site_deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_deploy)


def test_website_sync_preserves_live_release_data_and_site_configuration(tmp_path):
    source, destination = tmp_path / "dist", tmp_path / "www"
    source.mkdir()
    destination.mkdir()
    (source / "index.html").write_text("new website")
    (destination / "index.html").write_text("old website")
    for path in [source / "index.html", destination / "index.html"]:
        os.utime(path, (1700000000, 1700000000))
    (destination / "obsolete.html").write_text("old page")
    protected = {
        "service/v1/latest.json": '{"version":"1.1.0"}',
        ".well-known/acme-challenge/token": "certificate challenge",
        ".user.ini": "server configuration",
        ".htaccess": "server rules",
    }
    for name, content in protected.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        unwanted = source / name
        unwanted.parent.mkdir(parents=True, exist_ok=True)
        unwanted.write_text("must not replace live state")
    (destination / "service/v1/releases.json").write_text("live release metadata")

    site_deploy.sync(source, destination)
    assert (destination / "index.html").read_text() == "old website"
    assert (destination / "obsolete.html").exists()
    site_deploy.sync(source, destination, apply=True)
    assert (destination / "index.html").read_text() == "new website"
    assert not (destination / "obsolete.html").exists()
    assert (destination / "service/v1/releases.json").read_text() == "live release metadata"
    for name, content in protected.items():
        assert (destination / name).read_text() == content
