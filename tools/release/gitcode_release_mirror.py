#!/usr/bin/env python3
"""Mirror a finalized GitHub Release payload to GitCode.

The GitHub Release remains the source of truth. This tool uploads the exact
release assets to GitCode and rewrites only artifact URLs inside latest.json so
GitCode has an independent updater manifest for the same signed bytes.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_ROOT = "https://api.gitcode.com/api/v5"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
UPLOAD_CONFIRM_ATTEMPTS = 20
UPLOAD_CONFIRM_DELAY_SECONDS = 1.0


class GitCodeMirrorError(RuntimeError):
    pass


def split_repository(value: str) -> tuple[str, str]:
    value = value.strip()
    if not REPOSITORY_PATTERN.fullmatch(value):
        raise GitCodeMirrorError("GITCODE_REPOSITORY_INVALID")
    owner, repo = value.split("/", 1)
    return owner, repo


def validate_tag(tag: str) -> str:
    tag = tag.strip()
    if not TAG_PATTERN.fullmatch(tag):
        raise GitCodeMirrorError("GITCODE_TAG_INVALID")
    return tag


def release_asset_download_url(owner: str, repo: str, tag: str, file_name: str) -> str:
    segments = [owner, repo, tag, file_name]
    encoded = [urllib.parse.quote(value, safe="") for value in segments]
    return (
        f"{API_ROOT}/repos/{encoded[0]}/{encoded[1]}/releases/{encoded[2]}"
        f"/attach_files/{encoded[3]}/download"
    )


def _source_file_name(url: object) -> str:
    if not isinstance(url, str) or not url.startswith("https://") or any(character.isspace() for character in url):
        raise GitCodeMirrorError("UPDATER_SOURCE_URL_INVALID")
    parsed = urllib.parse.urlsplit(url)
    name = urllib.parse.unquote(Path(parsed.path).name)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise GitCodeMirrorError("UPDATER_SOURCE_FILE_INVALID")
    return name


def rewrite_updater_manifest(
    manifest: dict[str, Any],
    *,
    owner: str,
    repo: str,
    tag: str,
) -> dict[str, Any]:
    rewritten = copy.deepcopy(manifest)
    platforms = rewritten.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise GitCodeMirrorError("UPDATER_PLATFORM_SET_INVALID")
    for entry in platforms.values():
        if not isinstance(entry, dict):
            raise GitCodeMirrorError("UPDATER_PLATFORM_ENTRY_INVALID")
        file_name = _source_file_name(entry.get("url"))
        entry["url"] = release_asset_download_url(owner, repo, tag, file_name)

    portable = rewritten.get("portable")
    if portable is not None:
        if not isinstance(portable, dict):
            raise GitCodeMirrorError("UPDATER_PORTABLE_SET_INVALID")
        for entry in portable.values():
            if not isinstance(entry, dict):
                raise GitCodeMirrorError("UPDATER_PORTABLE_ENTRY_INVALID")
            file_name = _source_file_name(entry.get("url"))
            entry["url"] = release_asset_download_url(owner, repo, tag, file_name)
    return rewritten


def build_gitcode_manifest(source: Path, destination: Path, *, repository: str, tag: str) -> None:
    owner, repo = split_repository(repository)
    tag = validate_tag(tag)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitCodeMirrorError("UPDATER_MANIFEST_INVALID") from exc
    if not isinstance(raw, dict):
        raise GitCodeMirrorError("UPDATER_MANIFEST_INVALID")
    rewritten = rewrite_updater_manifest(raw, owner=owner, repo=repo, tag=tag)
    destination.write_text(
        json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class GitCodeClient:
    def __init__(self, *, repository: str, token: str, timeout: float = 30.0) -> None:
        self.owner, self.repo = split_repository(repository)
        token = token.strip()
        if not token:
            raise GitCodeMirrorError("GITCODE_ACCESS_TOKEN_MISSING")
        self._token = token
        self._timeout = timeout

    def _api_path(self, suffix: str) -> str:
        return (
            f"/repos/{urllib.parse.quote(self.owner, safe='')}/"
            f"{urllib.parse.quote(self.repo, safe='')}{suffix}"
        )

    def _request_json(
        self,
        method: str,
        suffix: str,
        *,
        query: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        query_values = dict(query or {})
        query_values["access_token"] = self._token
        path = self._api_path(suffix)
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(query_values)}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise GitCodeMirrorError(f"GITCODE_API_HTTP_{exc.code}: {method} {path}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitCodeMirrorError(f"GITCODE_API_UNAVAILABLE: {method} {path}") from exc
        if not body:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitCodeMirrorError(f"GITCODE_API_RESPONSE_INVALID: {method} {path}") from exc
        if not isinstance(value, dict):
            raise GitCodeMirrorError(f"GITCODE_API_RESPONSE_INVALID: {method} {path}")
        return value

    def release(self, tag: str) -> dict[str, Any] | None:
        tag = validate_tag(tag)
        return self._request_json(
            "GET",
            f"/releases/tags/{urllib.parse.quote(tag, safe='')}",
            allow_not_found=True,
        )

    def latest_release(self) -> dict[str, Any] | None:
        return self._request_json(
            "GET",
            "/releases/latest",
            query={"type": "latest"},
            allow_not_found=True,
        )

    def create_release(
        self,
        *,
        tag: str,
        name: str,
        body: str,
        target_commitish: str,
        latest: bool,
    ) -> dict[str, Any]:
        tag = validate_tag(tag)
        created = self._request_json(
            "POST",
            "/releases",
            payload={
                "tag_name": tag,
                "name": name,
                "body": body,
                "target_commitish": target_commitish,
                "release_status": "latest" if latest else "pre",
            },
        )
        assert created is not None
        return created

    def delete_asset(self, *, tag: str, asset_id: int) -> None:
        tag = validate_tag(tag)
        self._request_json(
            "DELETE",
            f"/releases/{urllib.parse.quote(tag, safe='')}/attach_files/{asset_id}",
        )

    def upload_file(self, *, tag: str, path: Path) -> None:
        tag = validate_tag(tag)
        path = path.resolve(strict=True)
        upload = self._request_json(
            "GET",
            f"/releases/{urllib.parse.quote(tag, safe='')}/upload_url",
            query={"file_name": path.name},
        )
        assert upload is not None
        url = upload.get("url")
        headers = upload.get("headers")
        if not isinstance(url, str) or not url.startswith("https://") or not isinstance(headers, dict):
            raise GitCodeMirrorError("GITCODE_UPLOAD_DESCRIPTOR_INVALID")
        upload_headers: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise GitCodeMirrorError("GITCODE_UPLOAD_DESCRIPTOR_INVALID")
            upload_headers[key] = value
        self._put_file(url, upload_headers, path)
        self._wait_for_asset(tag=tag, file_name=path.name)

    def _put_file(self, url: str, headers: dict[str, str], path: Path) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise GitCodeMirrorError("GITCODE_UPLOAD_URL_INVALID")
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=self._timeout)
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
            response = connection.getresponse()
            response.read()
            if response.status < 200 or response.status >= 300:
                raise GitCodeMirrorError(f"GITCODE_UPLOAD_HTTP_{response.status}: {path.name}")
        except (OSError, http.client.HTTPException) as exc:
            raise GitCodeMirrorError(f"GITCODE_UPLOAD_FAILED: {path.name}") from exc
        finally:
            connection.close()

    def _wait_for_asset(self, *, tag: str, file_name: str) -> None:
        for _ in range(UPLOAD_CONFIRM_ATTEMPTS):
            release = self.release(tag)
            if release is not None and file_name in _asset_map(release):
                return
            time.sleep(UPLOAD_CONFIRM_DELAY_SECONDS)
        raise GitCodeMirrorError(f"GITCODE_ASSET_NOT_VISIBLE: {file_name}")


def _asset_map(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if isinstance(name, str) and name:
            result[name] = asset
    return result


def _remove_existing_asset(client: GitCodeClient, *, tag: str, file_name: str) -> None:
    release = client.release(tag)
    if release is None:
        return
    asset = _asset_map(release).get(file_name)
    if asset is None:
        return
    asset_id = asset.get("id")
    if not isinstance(asset_id, int):
        raise GitCodeMirrorError(f"GITCODE_ASSET_REPLACE_UNSUPPORTED: {file_name}")
    client.delete_asset(tag=tag, asset_id=asset_id)


def mirror_release(
    *,
    repository: str,
    tag: str,
    assets_dir: Path,
    token: str,
    target_commitish: str,
    release_name: str | None = None,
    release_body: str = "Sakura GitCode mirror",
    latest: bool = True,
) -> None:
    owner, repo = split_repository(repository)
    tag = validate_tag(tag)
    assets_dir = assets_dir.resolve(strict=True)
    source_manifest = assets_dir / "latest.json"
    if not source_manifest.is_file():
        raise GitCodeMirrorError("UPDATER_MANIFEST_MISSING")

    target_commitish = target_commitish.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", target_commitish):
        raise GitCodeMirrorError("GITCODE_TARGET_COMMIT_INVALID")

    client = GitCodeClient(repository=repository, token=token)
    release = client.release(tag)
    if release is None:
        client.create_release(
            tag=tag,
            name=release_name or tag,
            body=release_body,
            target_commitish=target_commitish,
            latest=latest,
        )
    else:
        release_target = release.get("target_commitish")
        if isinstance(release_target, str) and release_target and release_target != target_commitish:
            raise GitCodeMirrorError("GITCODE_RELEASE_TARGET_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="sakura-gitcode-") as temporary:
        mirror_manifest = Path(temporary) / "latest.json"
        build_gitcode_manifest(
            source_manifest,
            mirror_manifest,
            repository=repository,
            tag=tag,
        )
        files = sorted(
            (path for path in assets_dir.iterdir() if path.is_file() and path.name != "latest.json"),
            key=lambda path: path.name,
        )
        if not files:
            raise GitCodeMirrorError("GITCODE_RELEASE_ASSETS_EMPTY")
        files.append(mirror_manifest)

        for path in files:
            _remove_existing_asset(client, tag=tag, file_name=path.name)
            client.upload_file(tag=tag, path=path)

        expected = {path.name for path in files}
        release = client.release(tag)
        if release is None or not expected.issubset(_asset_map(release)):
            raise GitCodeMirrorError("GITCODE_RELEASE_ASSET_SET_INCOMPLETE")
        if latest:
            latest_release = client.latest_release()
            if latest_release is None or latest_release.get("tag_name") != tag:
                raise GitCodeMirrorError("GITCODE_LATEST_RELEASE_MISMATCH")

    print(f"GitCode release mirror published: https://gitcode.com/{owner}/{repo}/releases/tag/{tag}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, help="GitCode repository as owner/repo")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--target-commitish", required=True)
    parser.add_argument("--token", default=os.environ.get("GITCODE_ACCESS_TOKEN", ""))
    parser.add_argument("--name")
    parser.add_argument("--body", default="Sakura GitCode mirror")
    parser.add_argument("--prerelease", action="store_true")
    args = parser.parse_args()
    mirror_release(
        repository=args.repository,
        tag=args.tag,
        assets_dir=args.assets_dir,
        token=args.token,
        target_commitish=args.target_commitish,
        release_name=args.name,
        release_body=args.body,
        latest=not args.prerelease,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
