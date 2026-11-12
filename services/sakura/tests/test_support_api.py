import uuid

import pytest

from test_diagnostics import client, report


@pytest.mark.parametrize("path", [
    "/support/errors", "/support/groups",
    f"/support/reports/{uuid.UUID(int=1)}",
    f"/support/reports/{uuid.UUID(int=1)}/timeline",
])
def test_all_queries_require_configured_token(client, monkeypatch, path):
    monkeypatch.delenv("SAKURA_SUPPORT_TOKEN", raising=False)
    assert client.get(path, headers={"Authorization": "Bearer "}).status_code == 401
    monkeypatch.setenv("SAKURA_SUPPORT_TOKEN", "test-token")
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_support_auth_and_evidence(client, monkeypatch):
    monkeypatch.setenv("SAKURA_SUPPORT_TOKEN", "test-token")
    payload = report(schema=3, fingerprintVersion=3, stack=[], breadcrumbs=[],
                     evidence={"diagnostic": "原始错误 WinError 32", "exception_stack": "func line 123"})
    assert client.post("/v3/errors", json=payload).status_code == 202
    headers = {"Authorization": "Bearer test-token"}
    rows = client.get("/support/errors", params={"q": "WinError 32"}, headers=headers).json()
    assert rows["total"] == 1
    assert "evidence" not in rows["items"][0]
    assert "report" not in rows["items"][0]
    detail = client.get(f"/support/reports/{payload['reportId']}", headers=headers).json()
    assert detail["evidence"] == payload["evidence"]
    assert detail["report"] == payload
    assert client.get(f"/support/reports/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.post("/support/errors", json={}, headers=headers).status_code == 405


def test_timeline_uses_both_installation_and_run(client, monkeypatch):
    monkeypatch.setenv("SAKURA_SUPPORT_TOKEN", "test-token")
    first = report()
    second = report(installationId=str(uuid.uuid4()))
    third = report(runId="different-run")
    same_run = report()
    for payload in (first, second, third, same_run):
        assert client.post("/v2/errors", json=payload).status_code == 202
    headers = {"Authorization": "Bearer test-token"}
    path = f"/support/reports/{first['reportId']}/timeline"
    result = client.get(path, params={"limit": 1}, headers=headers).json()
    assert result["total"] == 2
    assert result["hasMore"] is True
    tail = client.get(path, params={"limit": 1, "cursor": result["nextCursor"]}, headers=headers).json()
    assert tail["hasMore"] is False
    items = result["items"] + tail["items"]
    assert {item["report_id"] for item in items} == {first["reportId"], same_run["reportId"]}
    assert all("report" not in item and "evidence" not in item for item in items)
    rows = client.get("/support/errors?limit=1", headers=headers).json()
    next_page = client.get("/support/errors", params={"limit": 1, "cursor": rows["nextCursor"]}, headers=headers).json()
    assert rows["items"][0]["report_id"] != next_page["items"][0]["report_id"]


def test_filters_and_group_pagination(client, monkeypatch):
    monkeypatch.setenv("SAKURA_SUPPORT_TOKEN", "test-token")
    headers = {"Authorization": "Bearer test-token"}
    for index, (platform, version) in enumerate([
        ("windows", "1.1.0"), ("windows", "1.1.0"),
        ("macos", "1.1.0"), ("windows", "1.2.0"),
    ]):
        payload = report(system={"platform": platform},
                         app={"version": version, "build": "abcdef", "channel": "stable"})
        payload["error"]["fingerprint"] = f"filter-{index}"
        assert client.post("/v2/errors", json=payload).status_code == 202

    params = {"q": "filter-", "platform": "windows", "version": "1.1.0", "limit": 1}
    for path, field in [("/support/errors", "report_id"), ("/support/groups", "fingerprint")]:
        first = client.get(path, params=params, headers=headers).json()
        assert first["total"] == 2
        assert first["hasMore"] is True
        second = client.get(path, params={**params, "cursor": first["nextCursor"]}, headers=headers).json()
        assert second["hasMore"] is False
        assert second["nextCursor"] is None
        assert first["items"][0][field] != second["items"][0][field]
        assert client.get(path, params={"platform": "qq"}, headers=headers).status_code == 400
    assert client.get("/support/errors", params={"cursor": "broken"}, headers=headers).status_code == 400
