#!/usr/bin/env python3
"""Mirror one finalized GitHub Release to GitCode without rebuilding artifacts."""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_ROOT = "https://api.gitcode.com/api/v5"
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")


class MirrorError(RuntimeError):
    pass


def split_repo(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(TOKEN_RE.fullmatch(part) for part in parts):
        raise MirrorError("GITCODE_REPOSITORY_INVALID")
    return parts[0], parts[1]


def safe_tag(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 128 or not TOKEN_RE.fullmatch(value):
        raise MirrorError("GITCODE_TAG_INVALID")
    return value


def api_path(owner: str, repo: str, suffix: str) -> str:
    return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}{suffix}"


def error_detail(raw: bytes, token: str) -> str:
    """Retain API diagnostics without echoing credentials or signed URLs."""
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    details = []
    for key in ("error_code", "error_code_name", "error_message", "message", "trace_id"):
        item = value.get(key)
        if not isinstance(item, (str, int)):
            continue
        clean = str(item)
        if token:
            for secret in (token, urllib.parse.quote(token, safe=""), urllib.parse.quote_plus(token)):
                clean = clean.replace(secret, "[redacted]")
        clean = re.sub(r"https?://\S+", "[url redacted]", clean)
        clean = re.sub(r"[\x00-\x1f\x7f]", " ", clean)
        details.append(f"{key}={' '.join(clean.split())[:400]}")
    return "; ".join(details)


def request_json(
    method: str,
    owner: str,
    repo: str,
    token: str,
    suffix: str,
    *,
    query: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
    allow_404: bool = False,
    allow_missing_latest: bool = False,
) -> dict[str, Any] | None:
    params = dict(query or {})
    params["access_token"] = token
    url = f"{API_ROOT}{api_path(owner, repo, suffix)}?{urllib.parse.urlencode(params)}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        error_body = exc.read(8192)
        if allow_missing_latest and exc.code == 400 and method == "GET" and suffix == "/releases/latest":
            try:
                error = json.loads(error_body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                error = None
            if isinstance(error, dict) and error.get("error_message") == "No latest release found":
                return None
        detail = error_detail(error_body, token)
        message = f"GITCODE_API_HTTP_{exc.code}: {method} {suffix}"
        raise MirrorError(f"{message}; {detail}" if detail else message) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MirrorError(f"GITCODE_API_UNAVAILABLE: {method} {suffix}") from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MirrorError(f"GITCODE_API_RESPONSE_INVALID: {method} {suffix}") from exc
    if not isinstance(value, dict):
        raise MirrorError(f"GITCODE_API_RESPONSE_INVALID: {method} {suffix}")
    return value


def release(owner: str, repo: str, token: str, tag: str) -> dict[str, Any] | None:
    return request_json(
        "GET",
        owner,
        repo,
        token,
        f"/releases/tags/{urllib.parse.quote(tag, safe='')}",
        allow_404=True,
    )


def verify_target(owner: str, repo: str, token: str, tag: str, sha: str) -> None:
    tag_commit = request_json(
        "GET", owner, repo, token,
        f"/commits/{urllib.parse.quote(tag, safe='')}", allow_404=True,
    )
    if tag_commit is not None:
        if tag_commit.get("sha") != sha:
            raise MirrorError("GITCODE_TAG_TARGET_MISMATCH: refusing to reuse a different commit")
        return
    commit = request_json("GET", owner, repo, token, f"/commits/{sha}", allow_404=True)
    if commit is None:
        raise MirrorError(
            f"GITCODE_SOURCE_COMMIT_MISSING: {sha}; "
            "sync the GitHub release commit and tag to GitCode before mirroring"
        )
    if commit.get("sha") != sha:
        raise MirrorError("GITCODE_TARGET_COMMIT_MISMATCH")


def sync_source(repository: str, tag: str, sha: str, token: str) -> None:
    """Push just the release commit and tag, without overwriting other refs."""
    owner, _ = split_repo(repository)
    tag = safe_tag(tag)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise MirrorError("GITCODE_TARGET_COMMIT_INVALID")
    username = os.environ.get("GITCODE_USERNAME") or owner
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://gitcode.com/.extraHeader",
        "GIT_CONFIG_VALUE_1": f"Authorization: Basic {auth}",
    })
    result = subprocess.run(
        ["git", "push", "--porcelain", f"https://gitcode.com/{repository}.git",
         f"{sha}:refs/tags/{tag}"],
        env=env, capture_output=True, text=True, timeout=1200,
    )
    if result.returncode:
        detail = result.stderr.replace(token, "[redacted]").replace(auth, "[redacted]")
        raise MirrorError(f"GITCODE_SOURCE_SYNC_FAILED: {detail[-2000:]}")
    print(f"Code synchronized: {tag} -> {sha}", flush=True)


def verify_download(repository: str, tag: str, path: Path) -> None:
    owner, repo = split_repo(repository)
    url = download_url(owner, repo, tag, path.name)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, path.open("rb") as local:
            while chunk := response.read(1024 * 1024):
                if local.read(len(chunk)) != chunk:
                    raise MirrorError(f"GITCODE_DOWNLOAD_CONTENT_MISMATCH: {path.name}")
            if local.read(1):
                raise MirrorError(f"GITCODE_DOWNLOAD_TRUNCATED: {path.name}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MirrorError(f"GITCODE_DOWNLOAD_FAILED: {path.name}; {type(exc).__name__}") from None
    print(f"Public download verified: {path.name} ({path.stat().st_size} bytes)", flush=True)


def asset_names(value: dict[str, Any] | None) -> set[str]:
    if not value:
        return set()
    assets = value.get("assets")
    if not isinstance(assets, list):
        return set()
    return {
        asset["name"]
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }


def download_url(owner: str, repo: str, tag: str, filename: str) -> str:
    quoted = [urllib.parse.quote(part, safe="") for part in (owner, repo, tag, filename)]
    return (
        f"{API_ROOT}/repos/{quoted[0]}/{quoted[1]}/releases/{quoted[2]}"
        f"/attach_files/{quoted[3]}/download"
    )


def source_filename(url: object) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise MirrorError("UPDATER_SOURCE_URL_INVALID")
    name = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise MirrorError("UPDATER_SOURCE_FILE_INVALID")
    return name


def rewrite_manifest(source: Path, destination: Path, *, repository: str, tag: str) -> None:
    owner, repo = split_repo(repository)
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MirrorError("UPDATER_MANIFEST_INVALID") from exc
    if not isinstance(manifest, dict):
        raise MirrorError("UPDATER_MANIFEST_INVALID")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise MirrorError("UPDATER_PLATFORM_SET_INVALID")
    for entry in platforms.values():
        if not isinstance(entry, dict):
            raise MirrorError("UPDATER_PLATFORM_ENTRY_INVALID")
        entry["url"] = download_url(owner, repo, tag, source_filename(entry.get("url")))
    portable = manifest.get("portable")
    if portable is not None:
        if not isinstance(portable, dict):
            raise MirrorError("UPDATER_PORTABLE_SET_INVALID")
        for entry in portable.values():
            if not isinstance(entry, dict):
                raise MirrorError("UPDATER_PORTABLE_ENTRY_INVALID")
            entry["url"] = download_url(owner, repo, tag, source_filename(entry.get("url")))
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def put_file(url: str, headers: dict[str, str], path: Path) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MirrorError("GITCODE_UPLOAD_URL_INVALID")
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        parsed.port or 443,
        timeout=60,
    )
    sent = 0
    started = time.monotonic()
    print(f"Uploading {path.name}: {path.stat().st_size} bytes to {parsed.hostname}", flush=True)
    try:
        connection.putrequest("PUT", target)
        lowered = {key.lower() for key in headers}
        for key, value in headers.items():
            connection.putheader(key, value)
        if "content-length" not in lowered:
            connection.putheader("Content-Length", str(path.stat().st_size))
        connection.endheaders()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                connection.send(chunk)
                sent += len(chunk)
                if sent % (16 * 1024 * 1024) == 0:
                    print(f"Upload progress: {path.name} {sent} bytes, {time.monotonic() - started:.0f}s", flush=True)
        response = connection.getresponse()
        response.read()
        if not 200 <= response.status < 300:
            raise MirrorError(f"GITCODE_UPLOAD_HTTP_{response.status}: {path.name}")
    except (OSError, http.client.HTTPException) as exc:
        raise MirrorError(
            f"GITCODE_UPLOAD_FAILED: {path.name}; sent={sent}; "
            f"elapsed={time.monotonic() - started:.0f}s; {type(exc).__name__}"
        ) from None
    finally:
        connection.close()


def upload(owner: str, repo: str, token: str, tag: str, path: Path) -> None:
    descriptor = request_json(
        "GET",
        owner,
        repo,
        token,
        f"/releases/{urllib.parse.quote(tag, safe='')}/upload_url",
        query={"file_name": path.name},
    )
    if descriptor is None:
        raise MirrorError("GITCODE_UPLOAD_DESCRIPTOR_INVALID")
    url, raw_headers = descriptor.get("url"), descriptor.get("headers")
    if not isinstance(url, str) or not isinstance(raw_headers, dict):
        raise MirrorError("GITCODE_UPLOAD_DESCRIPTOR_INVALID")
    headers: dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise MirrorError("GITCODE_UPLOAD_DESCRIPTOR_INVALID")
        headers[key] = value
    put_file(url, headers, path)
    for _ in range(20):
        if path.name in asset_names(release(owner, repo, token, tag)):
            return
        time.sleep(1)
    raise MirrorError(f"GITCODE_ASSET_NOT_VISIBLE: {path.name}")


def mirror(
    repository: str,
    tag: str,
    assets_dir: Path,
    token: str,
    target_commitish: str,
    *,
    rehearsal: bool = False,
    sync_code: bool = False,
) -> None:
    owner, repo = split_repo(repository)
    tag = safe_tag(tag)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", target_commitish):
        raise MirrorError("GITCODE_TARGET_COMMIT_INVALID")
    assets_dir = assets_dir.resolve(strict=True)
    source = assets_dir / "latest.json"
    if not source.is_file():
        raise MirrorError("UPDATER_MANIFEST_MISSING")
    if rehearsal and not tag.startswith("mirror-test-"):
        raise MirrorError("GITCODE_REHEARSAL_TAG_INVALID")

    if sync_code:
        sync_source(repository, tag, target_commitish, token)
    verify_target(owner, repo, token, tag, target_commitish)
    latest_before = None
    if rehearsal:
        latest_before = request_json(
            "GET", owner, repo, token, "/releases/latest", query={"type": "latest"}, allow_404=True,
            allow_missing_latest=True,
        )
    current = release(owner, repo, token, tag)
    if current is None:
        request_json(
            "POST",
            owner,
            repo,
            token,
            "/releases",
            payload={
                "tag_name": tag,
                "name": tag,
                "body": "Sakura GitCode mirror",
                "target_commitish": target_commitish,
                "release_status": "pre",
            },
        )
    else:
        if rehearsal and current.get("release_status") != "pre":
            raise MirrorError("GITCODE_REHEARSAL_RELEASE_NOT_PRE")
        target = current.get("target_commitish")
        if isinstance(target, str) and target and target != target_commitish:
            raise MirrorError("GITCODE_RELEASE_TARGET_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="sakura-gitcode-") as temp:
        mirror_manifest = Path(temp) / "latest.json"
        rewrite_manifest(source, mirror_manifest, repository=repository, tag=tag)
        files = sorted(
            (
                path
                for path in assets_dir.iterdir()
                if path.is_file() and path.name != "latest.json"
            ),
            key=lambda path: path.name,
        )
        if not files:
            raise MirrorError("GITCODE_RELEASE_ASSETS_EMPTY")
        files.append(mirror_manifest)  # latest.json is intentionally uploaded last.

        existing = asset_names(release(owner, repo, token, tag))
        for path in files:
            if path.name not in existing:
                upload(owner, repo, token, tag, path)
                existing.add(path.name)
        expected = {path.name for path in files}
        if not expected.issubset(asset_names(release(owner, repo, token, tag))):
            raise MirrorError("GITCODE_RELEASE_ASSET_SET_INCOMPLETE")

        for path in files:
            verify_download(repository, tag, path)
        if rehearsal:
            latest_after = request_json(
                "GET", owner, repo, token, "/releases/latest", query={"type": "latest"}, allow_404=True,
                allow_missing_latest=True,
            )
            if (latest_after or {}).get("tag_name") != (latest_before or {}).get("tag_name"):
                raise MirrorError("GITCODE_REHEARSAL_CHANGED_LATEST")
            print(f"Rehearsal passed (pre-release retained): https://gitcode.com/{owner}/{repo}/releases/tag/{tag}")
            return

        # Only after all files exist do we expose this Release as GitCode's latest.
        request_json(
            "PATCH",
            owner,
            repo,
            token,
            f"/releases/{urllib.parse.quote(tag, safe='')}",
            payload={
                "name": tag,
                "body": "Sakura GitCode mirror",
                "release_status": "latest",
            },
        )
        latest = request_json(
            "GET",
            owner,
            repo,
            token,
            "/releases/latest",
            query={"type": "latest"},
        )
        if latest is None or latest.get("tag_name") != tag:
            raise MirrorError("GITCODE_LATEST_RELEASE_MISMATCH")

    print(
        f"GitCode release mirror published: "
        f"https://gitcode.com/{owner}/{repo}/releases/tag/{tag}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--target-commitish", required=True)
    parser.add_argument("--rehearsal", action="store_true")
    parser.add_argument("--sync-code", action="store_true")
    parser.add_argument(
        "--token",
        default=os.environ.get("GITCODE_ACCESS_TOKEN", ""),
    )
    args = parser.parse_args()
    if not args.token.strip():
        raise MirrorError("GITCODE_ACCESS_TOKEN_MISSING")
    mirror(
        args.repository,
        args.tag,
        args.assets_dir,
        args.token.strip(),
        args.target_commitish.strip(),
        rehearsal=args.rehearsal,
        sync_code=args.sync_code,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
