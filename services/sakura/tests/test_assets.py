"""Check the current admin router's bundled assets without a production DB."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import admin


def test_assets_and_host_isolation(monkeypatch):
    monkeypatch.setattr(admin, "STATIC_ROOT", ROOT / "dashboard/src")
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        for name, mime in [("sakura-icon.png", "image/png"), ("sakura-ui.woff2", "font/woff2")]:
            path = "/admin/assets/" + name
            response = client.get(path, headers={"Host": "admin.cialloo.cn"})
            assert response.status_code == 200
            assert response.headers["content-type"] == mime
            assert response.content == (admin.STATIC_ROOT / "assets" / name).read_bytes()
            assert client.get(path, headers={"Host": "telemetry.cialloo.cn"}).status_code == 404
            assert client.post(path, headers={"Host": "admin.cialloo.cn"}).status_code == 405
        for path in ["telemetry.db", "unknown.png", "%2e%2e%2fadmin.py"]:
            assert client.get("/admin/assets/" + path, headers={"Host": "admin.cialloo.cn"}).status_code == 404
