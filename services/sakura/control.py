"""Release drafts and private publication history for a single maintainer."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

import releases
from http_input import _read_json_limited

ROOT = Path(os.environ.get("SAKURA_RELEASE_ROOT", "/www/wwwroot/Sakura/service/v1"))
DATABASE = Path(os.environ.get("SAKURA_CONSOLE_DB", "/var/lib/sakura-console/console.db"))
LOCK = Path(os.environ.get("SAKURA_RELEASE_LOCK", "/var/lib/sakura-console/publish.lock"))
router = APIRouter(prefix="/admin/api/control", include_in_schema=False)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    db = sqlite3.connect(DATABASE, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        with db:
            yield db
    finally:
        db.close()


def initialize():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY, version TEXT NOT NULL, created_at TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT, published_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, source TEXT NOT NULL,
            action TEXT NOT NULL, version TEXT, state TEXT NOT NULL, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS ci_imports (
            version TEXT PRIMARY KEY, payload TEXT NOT NULL, draft_id TEXT NOT NULL
        );
        """)
        db.execute("UPDATE drafts SET state='interrupted', error='发布进程中断，请核对线上状态后重新发布。' WHERE state='publishing'")
    DATABASE.chmod(0o600)


def read_live():
    result = {}
    for name, key in (("releases.json", "release"), ("latest.json", "updater")):
        try:
            path = ROOT / name
            result[key] = releases.decode(path.read_bytes())
            if key == "updater":
                result["fileUpdatedAt"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                result["revision"] = str(path.stat().st_mtime_ns)
        except (OSError, ValueError):
            result[key] = None
    try:
        releases.build_payload(result["release"], result["updater"])
        result["consistent"] = True
    except ValueError:
        result["consistent"] = False
    return result


def fetch_json(url, limit=512 * 1024):
    request = UrlRequest(url, headers={"Accept": "application/json", "User-Agent": "Sakura-Console"})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(limit + 1)
    except HTTPError as error:
        raise ValueError(f"GitHub 请求失败（HTTP {error.code}）。") from error
    except (URLError, TimeoutError) as error:
        raise ValueError("无法连接 GitHub，稍后可重新读取。") from error
    if len(raw) > limit:
        raise ValueError("远程版本资料超过大小限制。")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("远程版本资料格式无效。")
    return value


def import_draft(version):
    releases.version_key(version)
    remote = fetch_json(f"https://api.github.com/repos/Rvosy/Sakura/releases/tags/v{version}")
    if remote.get("draft") or remote.get("prerelease") or remote.get("tag_name") != f"v{version}":
        raise ValueError("只允许导入已发布的稳定版本。")
    prefix = f"https://github.com/Rvosy/Sakura/releases/download/v{version}/"
    assets = {a.get("name"): a for a in remote.get("assets", [])}
    expected = ["latest.json", *(f"Sakura-{version}-{suffix}" for suffix in (
        "windows-x64-setup.exe", "windows-x64-portable.zip", "macos-arm64.dmg", "macos-arm64.app.tar.gz"))]
    for name in expected:
        if name not in assets or assets[name].get("browser_download_url") != prefix + name:
            raise ValueError("该版本的正式下载文件尚未齐全。")
    updater = fetch_json(prefix + "latest.json", releases.MAX_PAYLOAD_BYTES)
    metadata = {
        "schema": 1, "latest": version, "minimumSupported": None, "urgent": False,
        "releaseUrl": f"https://github.com/Rvosy/Sakura/releases/tag/v{version}",
        "publishedAt": remote.get("published_at"),
        "downloads": {key: releases.asset_url(version, suffix) for key, suffix in {
            "windowsX64Setup": "windows-x64-setup.exe", "windowsX64Portable": "windows-x64-portable.zip", "macosArm64Dmg": "macos-arm64.dmg",
        }.items()},
        "updaterManifestUrl": releases.SERVICE_ENDPOINT,
    }
    payload = releases.build_payload(metadata, updater)
    return store_draft(payload)


def import_ci_payload(raw):
    envelope = releases.decode(raw)
    if set(envelope) != {"schema", "release", "updater"} or type(envelope["schema"]) is not int or envelope["schema"] != 1:
        raise ValueError("SERVICE_FIELDS_INVALID")
    payload = releases.build_payload(envelope["release"], envelope["updater"])
    return store_draft(payload, source="ci")


def store_draft(payload, source="console"):
    version = payload["release"]["latest"]
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if source == "ci":
            existing = db.execute("SELECT payload,draft_id FROM ci_imports WHERE version=?", (version,)).fetchone()
            if existing:
                if json.loads(existing["payload"]) != payload:
                    raise ValueError("该版本的 CI 资料已变化，请在后台重新导入并核对。")
                return draft(existing["draft_id"])
        if db.execute("SELECT count(*) FROM drafts WHERE state NOT IN ('published','discarded')").fetchone()[0] >= 30:
            raise ValueError("待处理版本已达到 30 个，请先处理已有记录。")
        identity = str(uuid4())
        db.execute("INSERT INTO drafts VALUES (?,?,?,?,?,NULL,NULL)", (identity, version, now(), json.dumps(payload, ensure_ascii=False), "ready"))
        db.execute("INSERT INTO operations (occurred_at,source,action,version,state) VALUES (?,?,?,?,?)", (now(), source, "import", version, "ready"))
        if source == "ci":
            db.execute("INSERT INTO ci_imports VALUES (?,?,?)", (version, json.dumps(payload, ensure_ascii=False), identity))
    return draft(identity)


def draft(identity):
    with connection() as db:
        row = db.execute("SELECT * FROM drafts WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise ValueError("找不到待发布版本。")
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    return result


def _save_draft(identity, changes):
    item = draft(identity)
    if item["state"] == "published":
        raise ValueError("已发布记录不能修改，请重新导入该版本。")
    if set(changes) != {"notes", "urgent", "minimumSupported", "expectedPayload"}:
        raise ValueError("编辑字段无效。")
    if item["payload"] != changes["expectedPayload"]:
        raise ValueError("草稿已被其他页面修改，请重新打开。")
    payload = item["payload"]
    payload["release"]["urgent"] = changes["urgent"]
    payload["release"]["minimumSupported"] = changes["minimumSupported"]
    payload["updater"]["notes"] = changes["notes"]
    releases.build_payload(payload["release"], payload["updater"])
    with connection() as db:
        cursor = db.execute("UPDATE drafts SET payload=?,state='ready',error=NULL WHERE id=? AND state IN ('ready','failed','interrupted')", (json.dumps(payload, ensure_ascii=False), identity))
        if not cursor.rowcount:
            raise ValueError("版本状态已变化，请刷新后重试。")
    return draft(identity)


def save_draft(identity, changes):
    with publication_lock():
        return _save_draft(identity, changes)


@contextmanager
def publication_lock():
    # Serialize edits and publication. CI only inserts drafts through SQLite.
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def publish_draft(identity, expected_revision, expected_payload):
    with publication_lock():
        item = draft(identity)
        if item["state"] == "published":
            return item
        if item["payload"] != expected_payload:
            raise ValueError("草稿已被其他页面修改，请重新打开。")
        live = read_live()
        if live.get("revision") != expected_revision:
            raise ValueError("线上版本已变化，请刷新并核对后再发布。")
        if item["state"] not in {"ready", "failed", "interrupted"}:
            raise ValueError("该版本正在发布。")
        with connection() as db:
            db.execute("UPDATE drafts SET state='publishing',error=NULL WHERE id=?", (identity,))
        try:
            releases.publish(json.dumps(item["payload"], ensure_ascii=False).encode(), ROOT)
        except (ValueError, OSError) as error:
            reason = str(error) if isinstance(error, ValueError) else "写入清单失败，请检查服务端存储状态。"
            with connection() as db:
                db.execute("UPDATE drafts SET state='failed',error=? WHERE id=?", (reason, identity))
                db.execute("INSERT INTO operations (occurred_at,source,action,version,state,detail) VALUES (?,?,?,?,?,?)", (now(), "console", "publish", item["version"], "failed", reason))
            raise ValueError(reason) from error
        with connection() as db:
            db.execute("UPDATE drafts SET state='published',published_at=? WHERE id=?", (now(), identity))
            db.execute("INSERT INTO operations (occurred_at,source,action,version,state) VALUES (?,?,?,?,?)", (now(), "console", "publish", item["version"], "published"))
    return draft(identity)


@router.get("/status")
def status():
    with publication_lock():
        live = read_live()
    with connection() as db:
        items = [dict(row) for row in db.execute("SELECT id,version,created_at,state,error,published_at FROM drafts ORDER BY CASE WHEN state IN ('published','discarded') THEN 1 ELSE 0 END, created_at DESC LIMIT 30")]
        operations = [dict(row) for row in db.execute("SELECT * FROM operations ORDER BY id DESC LIMIT 50")]
    return {"live": live, "drafts": items, "operations": operations, "endpoint": releases.SERVICE_ENDPOINT}


@router.get("/drafts/{identity}")
def get_draft(identity: str):
    try:
        return draft(identity)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/drafts")
async def create_draft(request: Request):
    body = await _read_json_limited(request, 1024)
    if not isinstance(body, dict) or set(body) != {"version"}:
        raise HTTPException(400, "版本输入无效。")
    try:
        return await run_in_threadpool(import_draft, body["version"])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/drafts/{identity}/save")
async def edit_draft(identity: str, request: Request):
    body = await _read_json_limited(request, 192 * 1024)
    if not isinstance(body, dict):
        raise HTTPException(400, "编辑内容无效。")
    try:
        return await run_in_threadpool(save_draft, identity, body)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/drafts/{identity}/publish")
async def publish_version(identity: str, request: Request):
    body = await _read_json_limited(request, 192 * 1024)
    if not isinstance(body, dict) or set(body) != {"expectedRevision", "expectedPayload"}:
        raise HTTPException(400, "发布请求无效。")
    try:
        return await run_in_threadpool(publish_draft, identity, body["expectedRevision"], body["expectedPayload"])
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/drafts/{identity}/discard")
def discard_draft(identity: str):
    with publication_lock(), connection() as db:
        item = draft(identity)
        if item["state"] not in {"ready", "failed", "interrupted"}:
            raise HTTPException(409, "当前状态不能丢弃。")
        db.execute("UPDATE drafts SET state='discarded' WHERE id=?", (identity,))
        db.execute("INSERT INTO operations (occurred_at,source,action,version,state) VALUES (?,?,?,?,?)", (now(), "console", "discard", item["version"], "discarded"))
    return {"state": "discarded"}
