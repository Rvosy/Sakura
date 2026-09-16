from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import TypeVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from v3_models import ErrorReportV3
from v2_models import ErrorReportV2, EventBatchV2, ModelBatchV2
from v2_db import initialize_v2, insert_v2

from db import (
    database_is_healthy,
    initialize_database,
    insert_error,
    insert_events,
    insert_model_calls,
)
from admin import router as admin_router
from models import ErrorReport, ModelCallBatch, TelemetryEventBatch


LOGGER = logging.getLogger("sakura_telemetry")
ModelT = TypeVar("ModelT", bound=BaseModel)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await run_in_threadpool(initialize_database)
        await run_in_threadpool(initialize_v2)
    except sqlite3.Error:
        LOGGER.error("SQLite initialization failed")
    from exports import start_cleanup, STOP

    await run_in_threadpool(start_cleanup)
    yield
    STOP.set()


app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.include_router(admin_router)


def _reject(status_code: int, code: str) -> None:
    from health_counters import increment

    increment("storageFailed" if code == "STORAGE_UNAVAILABLE" else "rejected")
    raise HTTPException(status_code=status_code, detail=code)


async def _read_json_limited(request: Request, limit: int) -> object:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        _reject(415, "UNSUPPORTED_MEDIA_TYPE")

    content_encoding = (
        request.headers.get("content-encoding", "identity").strip().lower()
    )
    if content_encoding not in {"", "identity"}:
        _reject(415, "UNSUPPORTED_CONTENT_ENCODING")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            announced_size = int(content_length, 10)
        except ValueError:
            _reject(400, "INVALID_CONTENT_LENGTH")
        if announced_size < 0:
            _reject(400, "INVALID_CONTENT_LENGTH")
        if announced_size > limit:
            _reject(413, "PAYLOAD_TOO_LARGE")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            _reject(413, "PAYLOAD_TOO_LARGE")
        body.extend(chunk)

    if not body:
        _reject(400, "INVALID_JSON")
    try:
        text = body.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject(400, "INVALID_JSON")


async def _parse_model(request: Request, limit: int, model: type[ModelT]) -> ModelT:
    payload = await _read_json_limited(request, limit)
    try:
        return model.model_validate(payload)
    except ValidationError:
        _reject(400, "INVALID_PAYLOAD")


@app.get("/health")
async def health() -> JSONResponse:
    try:
        healthy = await run_in_threadpool(database_is_healthy)
    except sqlite3.Error:
        healthy = False
    if not healthy:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "service": "sakura-telemetry"},
        )
    return JSONResponse(content={"ok": True, "service": "sakura-telemetry"})


@app.post("/v1/errors", status_code=202)
async def post_error(request: Request) -> dict[str, bool | str]:
    report = await _parse_model(request, 32 * 1024, ErrorReport)
    try:
        received_at = await run_in_threadpool(insert_error, report)
    except sqlite3.Error:
        _reject(503, "STORAGE_UNAVAILABLE")
    return {"ok": True, "receivedAt": received_at}


@app.post("/v1/events", status_code=202)
async def post_events(request: Request) -> dict[str, int | bool | str]:
    batch = await _parse_model(request, 8 * 1024, TelemetryEventBatch)
    try:
        accepted, received_at = await run_in_threadpool(insert_events, batch)
    except sqlite3.Error:
        _reject(503, "STORAGE_UNAVAILABLE")
    return {"ok": True, "accepted": accepted, "receivedAt": received_at}


@app.post("/v1/model-calls", status_code=202)
async def post_model_calls(request: Request) -> dict[str, int | bool | str]:
    batch = await _parse_model(request, 16 * 1024, ModelCallBatch)
    try:
        accepted, received_at = await run_in_threadpool(insert_model_calls, batch)
    except sqlite3.Error:
        _reject(503, "STORAGE_UNAVAILABLE")
    return {"ok": True, "accepted": accepted, "receivedAt": received_at}


async def _ingest_v2(request, kind, model, limit):
    payload = await _parse_model(request, limit, model)
    try:
        return await run_in_threadpool(insert_v2, kind, payload)
    except sqlite3.Error:
        _reject(503, "STORAGE_UNAVAILABLE")


@app.post("/v2/errors", status_code=202)
async def post_error_v2(request: Request):
    return await _ingest_v2(request, "errors", ErrorReportV2, 32 * 1024)


@app.post("/v3/errors", status_code=202)
async def post_error_v3(request: Request):
    return await _ingest_v2(request, "errors", ErrorReportV3, 128 * 1024)


@app.post("/v2/events", status_code=202)
async def post_events_v2(request: Request):
    return await _ingest_v2(request, "events", EventBatchV2, 8 * 1024)


@app.post("/v2/model-calls", status_code=202)
async def post_model_calls_v2(request: Request):
    return await _ingest_v2(request, "model-calls", ModelBatchV2, 16 * 1024)


from admin_v2 import router as admin_v2_router

app.include_router(admin_v2_router)

from exports import router as export_router

app.include_router(export_router)
