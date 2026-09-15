from __future__ import annotations

import json
import io
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.release.gitcode_mirror import MirrorError, download_url, rewrite_manifest, split_repo
from tools.release import gitcode_mirror


def _manifest() -> dict[str, object]:
    return {
        "version": "1.0.3",
        "notes": "same notes",
        "pub_date": "2026-09-01T00:00:00Z",
        "platforms": {
            "windows-x86_64": {
                "signature": "windows-signature",
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-windows-x64-setup.exe",
            },
            "darwin-aarch64": {
                "signature": "mac-signature",
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-macos-arm64.app.tar.gz",
            },
        },
        "portable": {
            "windows-x86_64": {
                "url": "https://github.com/Rvosy/Sakura/releases/download/v1.0.3/Sakura-1.0.3-windows-x64-portable.zip",
            }
        },
    }


def test_gitcode_manifest_changes_only_download_urls(tmp_path: Path) -> None:
    source = tmp_path / "latest.json"
    destination = tmp_path / "gitcode-latest.json"
    original = _manifest()
    source.write_text(json.dumps(original), encoding="utf-8")

    rewrite_manifest(source, destination, repository="Rvosy/Sakura", tag="v1.0.3")

    mirrored = json.loads(destination.read_text(encoding="utf-8"))
    assert mirrored["version"] == original["version"]
    assert mirrored["notes"] == original["notes"]
    assert mirrored["pub_date"] == original["pub_date"]
    assert mirrored["platforms"]["windows-x86_64"]["signature"] == "windows-signature"
    assert mirrored["platforms"]["darwin-aarch64"]["signature"] == "mac-signature"
    assert set(mirrored["portable"]["windows-x86_64"]) == {"url"}
    assert mirrored["platforms"]["windows-x86_64"]["url"] == (
        "https://api.gitcode.com/api/v5/repos/Rvosy/Sakura/releases/v1.0.3/"
        "attach_files/Sakura-1.0.3-windows-x64-setup.exe/download"
    )
    assert mirrored["portable"]["windows-x86_64"]["url"] == (
        "https://api.gitcode.com/api/v5/repos/Rvosy/Sakura/releases/v1.0.3/"
        "attach_files/Sakura-1.0.3-windows-x64-portable.zip/download"
    )


def test_gitcode_download_url_encodes_asset_name() -> None:
    assert download_url("owner", "repo", "v1.0.3", "Sakura test.zip") == (
        "https://api.gitcode.com/api/v5/repos/owner/repo/releases/v1.0.3/"
        "attach_files/Sakura%20test.zip/download"
    )


def test_gitcode_repository_requires_owner_and_repo() -> None:
    assert split_repo("Rvosy/Sakura") == ("Rvosy", "Sakura")
    with pytest.raises(MirrorError, match="GITCODE_REPOSITORY_INVALID"):
        split_repo("Sakura")


def test_missing_gitcode_commit_stops_before_creating_release(tmp_path, monkeypatch) -> None:
    (tmp_path / "latest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    request = Mock(return_value=None)
    monkeypatch.setattr(gitcode_mirror, "request_json", request)

    with pytest.raises(MirrorError, match="GITCODE_SOURCE_COMMIT_MISSING"):
        gitcode_mirror.mirror("Rvosy/Sakura", "v1.0.3", tmp_path, "secret", "a" * 40)

    assert [call.args[0] for call in request.call_args_list] == ["GET", "GET"]
    assert [call.args[4] for call in request.call_args_list] == [
        "/commits/v1.0.3", "/commits/" + "a" * 40,
    ]


@pytest.mark.parametrize("responses,expected", [
    ([{"sha": "a" * 40}], None),
    ([None, {"sha": "a" * 40}], None),
    ([{"sha": "b" * 40}], "GITCODE_TAG_TARGET_MISMATCH"),
    ([None, {"sha": "b" * 40}], "GITCODE_TARGET_COMMIT_MISMATCH"),
])
def test_gitcode_target_must_match_release_commit(monkeypatch, responses, expected) -> None:
    request = Mock(side_effect=responses)
    monkeypatch.setattr(gitcode_mirror, "request_json", request)
    if expected:
        with pytest.raises(MirrorError, match=expected):
            gitcode_mirror.verify_target("Rvosy", "Sakura", "secret", "v1.0.3", "a" * 40)
    else:
        gitcode_mirror.verify_target("Rvosy", "Sakura", "secret", "v1.0.3", "a" * 40)
    assert request.call_count == len(responses)


@pytest.mark.parametrize("body,expected", [
    ({"error_code": 400, "error_message": "Commit missing", "trace_id": "trace-123"},
     "error_message=Commit missing"),
    ({"error_message": "bad secret+value secret%2Bvalue https://upload.example/?signature=private\n::error::", "access_token": "private"},
     "error_message=bad [redacted] [redacted] [url redacted] ::error::"),
    ("<html>upstream unavailable</html>", "GITCODE_API_HTTP_400"),
])
def test_api_failure_preserves_safe_diagnostics(monkeypatch, body, expected) -> None:
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    failure = urllib.error.HTTPError("https://api.example/?access_token=secret+value", 400,
                                    "Bad Request", {}, io.BytesIO(raw))
    monkeypatch.setattr(gitcode_mirror.urllib.request, "urlopen", Mock(side_effect=failure))
    with pytest.raises(MirrorError) as caught:
        gitcode_mirror.request_json("POST", "Rvosy", "Sakura", "secret+value", "/releases")
    message = str(caught.value)
    assert expected in message
    assert "secret" not in message and "private" not in message
    assert "https://" not in message and "\n" not in message


def test_code_sync_pushes_only_release_tag_without_credentials_in_arguments(monkeypatch) -> None:
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(gitcode_mirror.subprocess, "run", run)
    gitcode_mirror.sync_source("Rvosy/Sakura", "mirror-test-v1.1.2-1", "a" * 40, "private-token")
    args = run.call_args.args[0]
    assert args == ["git", "push", "--porcelain", "https://gitcode.com/Rvosy/Sakura.git",
                    "a" * 40 + ":refs/tags/mirror-test-v1.1.2-1"]
    assert "Authorization: Basic " in run.call_args.kwargs["env"]["GIT_CONFIG_VALUE_1"]


@pytest.mark.parametrize("message,allow,missing", [
    ("No latest release found", True, True),
    ("No latest release found", False, False),
    ("Permission denied", True, False),
])
def test_latest_baseline_accepts_only_documented_empty_state(monkeypatch, message, allow, missing) -> None:
    failure = urllib.error.HTTPError("https://api.example", 400, "Bad Request", {},
                                    io.BytesIO(json.dumps({"error_message": message}).encode()))
    monkeypatch.setattr(gitcode_mirror.urllib.request, "urlopen", Mock(side_effect=failure))
    if missing:
        assert gitcode_mirror.request_json("GET", "Rvosy", "Sakura", "secret", "/releases/latest", allow_missing_latest=allow) is None
    else:
        with pytest.raises(MirrorError, match="GITCODE_API_HTTP_400"):
            gitcode_mirror.request_json("GET", "Rvosy", "Sakura", "secret", "/releases/latest", allow_missing_latest=allow)


@pytest.mark.parametrize("actual,expected", [(b"original", None),
    (b"origina", "TRUNCATED"), (b"changed!", "CONTENT_MISMATCH"),
    (b"original-extra", "CONTENT_MISMATCH")])
def test_public_download_compares_actual_bytes(tmp_path, monkeypatch, actual, expected) -> None:
    path = tmp_path / "asset.zip"
    path.write_bytes(b"original")
    monkeypatch.setattr(gitcode_mirror.urllib.request, "urlopen", Mock(return_value=io.BytesIO(actual)))
    if expected:
        with pytest.raises(MirrorError, match=expected):
            gitcode_mirror.verify_download("Rvosy/Sakura", "mirror-test-one", path)
    else:
        gitcode_mirror.verify_download("Rvosy/Sakura", "mirror-test-one", path)


@pytest.mark.parametrize("fail_download", [False, True])
def test_rehearsal_uploads_real_assets_and_never_promotes_latest(tmp_path, monkeypatch, fail_download) -> None:
    (tmp_path / "latest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (tmp_path / "asset.zip").write_bytes(b"package")
    remote = None
    calls = []
    uploaded = []
    def request(method, owner, repo, token, suffix, **kwargs):
        nonlocal remote
        calls.append((method, suffix, kwargs))
        if suffix.startswith("/commits/"):
            return {"sha": "a" * 40}
        if suffix == "/releases/latest":
            return {"tag_name": "v1.0.1"}
        if method == "POST":
            assert kwargs["payload"]["release_status"] == "pre"
            remote = {"target_commitish": "a" * 40, "assets": [], "release_status": "pre"}
        return remote
    def upload(owner, repo, token, tag, path):
        uploaded.append(path.name)
        remote["assets"].append({"name": path.name})
    monkeypatch.setattr(gitcode_mirror, "request_json", request)
    monkeypatch.setattr(gitcode_mirror, "upload", upload)
    verify = Mock(side_effect=MirrorError("download failed") if fail_download else None)
    monkeypatch.setattr(gitcode_mirror, "verify_download", verify)
    if fail_download:
        with pytest.raises(MirrorError, match="download failed"):
            gitcode_mirror.mirror("Rvosy/Sakura", "mirror-test-one", tmp_path, "secret", "a" * 40, rehearsal=True)
    else:
        gitcode_mirror.mirror("Rvosy/Sakura", "mirror-test-one", tmp_path, "secret", "a" * 40, rehearsal=True)
        assert verify.call_count == 2
    assert uploaded == ["asset.zip", "latest.json"]
    assert all(method != "PATCH" for method, _, _ in calls)
