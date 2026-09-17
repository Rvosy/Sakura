"""Private console process. Never mounted on the public ingestion listener."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from admin import router as admin_router, ADMIN_HOSTS
from admin_v2 import router as diagnostics_router
from exports import router as exports_router, start_cleanup, STOP
from control import router as control_router, initialize


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(initialize)
    await run_in_threadpool(start_cleanup)
    yield
    STOP.set()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def private_boundary(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    if host not in ADMIN_HOSTS:
        return JSONResponse({"detail": "NOT_FOUND"}, status_code=404)
    if request.method not in {"GET", "HEAD"}:
        if request.headers.get("origin") != f"https://{host}":
            return JSONResponse({"detail": "ORIGIN_FORBIDDEN"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"detail": "JSON_REQUIRED"}, status_code=415)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(admin_router)
app.include_router(diagnostics_router)
app.include_router(exports_router)
app.include_router(control_router)
