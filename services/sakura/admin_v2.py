from fastapi import APIRouter, Request, HTTPException
from pydantic import ValidationError
from admin import _require_admin_host
from queries import Filters, TABLES, page, quality, groups

router = APIRouter(include_in_schema=False)


def filters(request):
    values = dict(request.query_params)
    for key in ("cursor", "limit"):
        values.pop(key, None)
    if "includeTest" in values:
        values["includeTest"] = values["includeTest"] == "true"
    try:
        return Filters.model_validate(values)
    except ValidationError:
        raise HTTPException(400, "INVALID_FILTER")


@router.get("/admin/api/v2/records/{kind}")
def records(request: Request, kind: str):
    _require_admin_host(request)
    if kind not in TABLES:
        raise HTTPException(404, "NOT_FOUND")
    try:
        limit = int(request.query_params.get("limit", 100))
        if not 1 <= limit <= 200:
            raise ValueError()
        return page(kind, filters(request), limit, request.query_params.get("cursor"))
    except ValueError:
        raise HTTPException(400, "INVALID_PAGINATION")


@router.get("/admin/api/v2/groups")
def problem_groups(request: Request):
    _require_admin_host(request)
    try:
        limit = int(request.query_params.get("limit", 100))
        offset = int(request.query_params.get("cursor", 0))
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError()
        return groups(filters(request), limit, offset)
    except ValueError:
        raise HTTPException(400, "INVALID_PAGINATION")


@router.get("/admin/api/v2/quality")
def data_quality(request: Request):
    _require_admin_host(request)
    from health_counters import snapshot
    from queries import client_quality

    f = filters(request)
    return {
        "items": quality(filters=f),
        "server": snapshot(),
        "client": client_quality(f),
    }


@router.get("/admin/api/v2/timeline")
def operation_timeline(request: Request):
    _require_admin_host(request)
    from queries import timeline

    f = filters(request)
    if not f.installation or not f.run:
        raise HTTPException(400, "RUN_REQUIRED")
    try:
        limit = int(request.query_params.get("limit", 100))
        offset = int(request.query_params.get("cursor", 0))
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError()
        return timeline(f, limit, offset)
    except ValueError:
        raise HTTPException(400, "INVALID_PAGINATION")
