"""Bounded JSON bodies shared by ingestion and private admin actions."""
import json
from fastapi import HTTPException, Request

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
