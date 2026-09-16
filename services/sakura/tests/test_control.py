import json
import sys
from pathlib import Path
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control, releases, exports
from console_app import app as console
from app import app as ingestion


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "DATABASE", tmp_path / "console.db")
    monkeypatch.setattr(control, "LOCK", tmp_path / "publish.lock")
    monkeypatch.setattr(control, "ROOT", tmp_path / "public")
    monkeypatch.setattr(exports, "ROOT", tmp_path / "exports")
    control.ROOT.mkdir()
    control.initialize()
    version = "1.2.0"
    prefix = f"https://github.com/Rvosy/Sakura/releases/download/v{version}/"
    suffixes = ["windows-x64-setup.exe", "windows-x64-portable.zip", "macos-arm64.dmg", "macos-arm64.app.tar.gz"]
    remote = {"tag_name": "v1.2.0", "published_at": "2026-09-13T00:00:00Z", "assets": [
        {"name": name, "browser_download_url": prefix + name}
        for name in ["latest.json", *[f"Sakura-{version}-{s}" for s in suffixes]]
    ]}
    updater = {"version": version, "notes": "发行说明", "pub_date": remote["published_at"],
        "platforms": {key: {"url": releases.asset_url(version, suffix), "signature": "upstream-signature"} for key, suffix in
            [("windows-x86_64", suffixes[0]), ("darwin-aarch64", suffixes[3])]},
        "portable": {"windows-x86_64": {"url": releases.asset_url(version, suffixes[1])}}}
    monkeypatch.setattr(control, "fetch_json", lambda url, *args: deepcopy(updater if url.endswith("latest.json") else remote))
    return remote, updater


def test_import_edit_publish_and_idempotency(environment):
    item = control.import_draft("1.2.0")
    assert list(control.ROOT.iterdir()) == []
    old = deepcopy(item["payload"])
    item = control.save_draft(item["id"], {"notes": "修复启动", "urgent": True, "minimumSupported": "1.1.0", "expectedPayload": old})
    assert list(control.ROOT.iterdir()) == []
    with pytest.raises(ValueError, match="草稿"):
        control.publish_draft(item["id"], None, old)
    with pytest.raises(ValueError, match="草稿"):
        control.save_draft(item["id"], {"notes": "过期页面", "urgent": False, "minimumSupported": None, "expectedPayload": old})
    published = control.publish_draft(item["id"], None, item["payload"])
    assert published["state"] == "published"
    live = control.read_live()
    assert live["consistent"] and live["updater"]["notes"] == "修复启动"
    assert live["updater"]["platforms"] == environment[1]["platforms"]
    assert control.publish_draft(item["id"], None, item["payload"])["published_at"] == published["published_at"]
    assert len([op for op in control.status()["operations"] if op["action"] == "publish"]) == 1


def test_stale_live_and_failed_write(environment, monkeypatch):
    item = control.import_draft("1.2.0")
    releases.publish(json.dumps(item["payload"]).encode(), control.ROOT)
    before = (control.ROOT / "latest.json").read_bytes()
    with pytest.raises(ValueError, match="线上版本"):
        control.publish_draft(item["id"], None, item["payload"])
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(releases, "publish", fail)
    with pytest.raises(ValueError, match="写入清单失败"):
        control.publish_draft(item["id"], control.read_live()["revision"], item["payload"])
    assert control.draft(item["id"])["state"] == "failed"
    assert control.status()["operations"][0]["state"] == "failed"
    assert (control.ROOT / "latest.json").read_bytes() == before


def test_ci_import_is_private_idempotent_and_preserves_owner_edits(environment):
    payload = control.import_draft("1.2.0")["payload"]
    raw = json.dumps(payload).encode()
    imported = control.import_ci_payload(raw)
    assert imported["state"] == "ready"
    assert list(control.ROOT.iterdir()) == []
    edited = control.save_draft(imported["id"], {"notes": "维护者修改", "urgent": True, "minimumSupported": None, "expectedPayload": imported["payload"]})
    assert control.import_ci_payload(raw) == edited
    changed = deepcopy(payload)
    changed["updater"]["notes"] = "CI 新资料"
    with pytest.raises(ValueError, match="CI 资料已变化"):
        control.import_ci_payload(json.dumps(changed).encode())
    assert control.draft(imported["id"]) == edited
    assert list(control.ROOT.iterdir()) == []
    published = control.publish_draft(edited["id"], None, edited["payload"])
    before = (control.ROOT / "latest.json").read_bytes()
    assert control.import_ci_payload(raw) == published
    assert (control.ROOT / "latest.json").read_bytes() == before
    assert len([op for op in control.status()["operations"] if op["source"] == "ci"]) == 1


def test_ci_rejects_incomplete_and_invalid_payload_without_drafts(environment):
    payload = control.import_draft("1.2.0")["payload"]
    before = control.status()["drafts"]
    invalid = deepcopy(payload)
    invalid["updater"]["platforms"]["windows-x86_64"]["signature"] = ""
    for body in [payload["release"], invalid, {**payload, "destination": "/tmp/other"}]:
        with pytest.raises(ValueError):
            control.import_ci_payload(json.dumps(body).encode())
    assert control.status()["drafts"] == before
    assert list(control.ROOT.iterdir()) == []


def test_incomplete_import_and_invalid_live(environment):
    environment[0]["assets"].pop()
    with pytest.raises(ValueError, match="齐全"):
        control.import_draft("1.2.0")
    assert control.status()["drafts"] == []
    (control.ROOT / "latest.json").write_text('{"version":"1.2.0"}')
    (control.ROOT / "releases.json").write_text('{"latest":"1.2.0"}')
    assert not control.read_live()["consistent"]


def test_private_routes_origin_boundary_and_discard(environment):
    # Ingestion router has no management surface even with a forged admin Host.
    public = TestClient(ingestion, base_url="https://adm.sakura.cialloo.cn")
    assert public.get("/admin/api/control/status").status_code == 404
    assert public.get("/admin/api/overview").status_code == 404
    with TestClient(console, base_url="https://adm.sakura.cialloo.cn") as private:
        assert private.get("/admin/api/control/status").status_code == 200
        assert private.get("/admin/api/control/status", headers={"Host": "api.sakura.cialloo.cn"}).status_code == 404
        assert private.post("/v2/errors", json={}, headers={"Origin": "https://adm.sakura.cialloo.cn"}).status_code == 404
        assert private.post("/admin/api/control/drafts", json={"version": "1.2.0"}).status_code == 403
        headers = {"Origin": "https://adm.sakura.cialloo.cn"}
        assert private.post("/admin/api/control/drafts", content="{}", headers=headers).status_code == 415
        created = private.post("/admin/api/control/drafts", json={"version": "1.2.0"}, headers=headers)
        assert created.status_code == 200
        first = created.json()
        changed = private.post(f"/admin/api/control/drafts/{first['id']}/save", json={
            "notes": "已核对" * 2000, "urgent": False, "minimumSupported": None, "expectedPayload": first["payload"]
        }, headers=headers)
        assert changed.status_code == 200
        published = private.post(f"/admin/api/control/drafts/{first['id']}/publish", json={
            "expectedRevision": None, "expectedPayload": changed.json()["payload"]
        }, headers=headers)
        assert published.status_code == 200
        assert published.json()["state"] == "published"
        created = private.post("/admin/api/control/drafts", json={"version": "1.2.0"}, headers=headers)
        identity = created.json()["id"]
        assert private.post(f"/admin/api/control/drafts/{identity}/discard", json={}, headers=headers).status_code == 200
        assert control.draft(identity)["state"] == "discarded"
        with pytest.raises(ValueError):
            control.publish_draft(identity, None, created.json()["payload"])
