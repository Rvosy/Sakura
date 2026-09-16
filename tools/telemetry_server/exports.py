"""Single bounded export worker. No public static files or durable job queue."""

import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import ValidationError
from admin import _require_admin_host
from export_bundle import export_bundle, ExportLimitError
from queries import Filters

router = APIRouter(include_in_schema=False)
ROOT = Path(
    os.environ.get("SAKURA_TELEMETRY_EXPORT_ROOT", "/var/lib/sakura-telemetry/exports")
)
LOCK = threading.Lock()
JOBS = {}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telemetry-export")
STOP = threading.Event()


def cleanup(startup=False):
    if ROOT.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise RuntimeError("EXPORT_ROOT_MUST_BE_PRIVATE")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    with LOCK:
        for key, job in list(JOBS.items()):
            if (
                job["status"] != "running"
                and not job.get("readers", 0)
                and time.time() - job.get("completed", job["created"]) > 3600
            ):
                JOBS.pop(key, None)
                shutil.rmtree(ROOT / key, ignore_errors=True)
        if startup:
            for p in ROOT.iterdir():
                if p.is_dir() and _uuid(p.name):
                    shutil.rmtree(p)


def _uuid(value):
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def start_cleanup():
    cleanup(True)
    STOP.clear()

    def run():
        while not STOP.wait(60):
            cleanup()

    threading.Thread(target=run, name="telemetry-export-cleanup", daemon=True).start()


def generate(key, filters):
    code = None
    try:
        root = ROOT / key
        root.mkdir(mode=0o700)
        export_bundle(root / "analysis.zip", filters, cancel_event=STOP)
    except ExportLimitError as e:
        code = str(e)
    except RuntimeError as error:
        code = "EXPORT_BUSY" if str(error) == "EXPORT_BUSY" else "EXPORT_FAILED"
    except Exception:
        code = "EXPORT_FAILED"
    with LOCK:
        JOBS[key].update(
            status="failed" if code else "ready", error=code, completed=time.time()
        )
    if code:
        shutil.rmtree(ROOT / key, ignore_errors=True)


@router.post("/admin/api/v2/exports", status_code=202)
async def create(request: Request):
    _require_admin_host(request)
    from app import _read_json_limited

    try:
        filters = Filters.model_validate(await _read_json_limited(request, 8192))
    except ValidationError:
        raise HTTPException(400, "INVALID_FILTER")
    await run_in_threadpool(cleanup)
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise HTTPException(409, "EXPORT_BUSY")
        key = str(uuid.uuid4())
        JOBS[key] = {
            "id": key,
            "created": time.time(),
            "status": "running",
            "error": None,
        }
    POOL.submit(generate, key, filters)
    return {"id": key, "status": "running"}


@router.get("/admin/api/v2/exports/{key}")
def status(request: Request, key: str):
    _require_admin_host(request)
    with LOCK:
        job = dict(JOBS.get(key, {}))
    if not job:
        raise HTTPException(404, "EXPORT_NOT_FOUND")
    return job


class DownloadResponse(FileResponse):
    def __init__(self, key, path):
        self.key = key
        super().__init__(
            path,
            filename="sakura-telemetry-analysis.zip",
            media_type="application/zip",
            headers={"Cache-Control": "no-store"},
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with LOCK:
                if self.key in JOBS:
                    JOBS[self.key]["readers"] -= 1


@router.get("/admin/api/v2/exports/{key}/download")
def download(request: Request, key: str):
    _require_admin_host(request)
    with LOCK:
        job = JOBS.get(key)
        if not job:
            raise HTTPException(404, "EXPORT_NOT_FOUND")
        if job["status"] != "ready":
            raise HTTPException(409, "EXPORT_NOT_READY")
        job["readers"] = job.get("readers", 0) + 1
    return DownloadResponse(key, ROOT / key / "analysis.zip")
