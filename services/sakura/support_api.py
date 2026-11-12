"""机器人使用的错误日志查询接口，共用现有查询层。"""

import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from admin_v2 import filters
from queries import Filters, groups, page, timeline


def require_support_token(request: Request):
    token = os.environ.get("SAKURA_SUPPORT_TOKEN", "")
    if not token or not hmac.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + token
    ):
        raise HTTPException(401, "SUPPORT_TOKEN_REQUIRED")


router = APIRouter(prefix="/support", dependencies=[Depends(require_support_token)])


def error_summary(row):
    return {key: value for key, value in row.items() if key not in {"report", "evidence", "stack", "breadcrumbs", "details"}}


@router.get("/errors")
def errors(request: Request, limit: int = Query(20, ge=1, le=200), cursor: str | None = None):
    try:
        result = page("errors", filters(request), limit, cursor)
        result["items"] = [error_summary(row) for row in result["items"]]
        return result
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/groups")
def error_groups(request: Request, limit: int = Query(20, ge=1, le=200), cursor: int = Query(0, ge=0)):
    return groups(filters(request), limit, cursor)


def find_report(report_id: UUID):
    rows = page("errors", Filters(report=str(report_id)), limit=1)["items"]
    if not rows:
        raise HTTPException(404, "REPORT_NOT_FOUND")
    return rows[0]


@router.get("/reports/{report_id}")
def report(report_id: UUID):
    return find_report(report_id)


@router.get("/reports/{report_id}/timeline")
def report_timeline(report_id: UUID, limit: int = Query(100, ge=1, le=200), cursor: int = Query(0, ge=0)):
    row = find_report(report_id)
    result = timeline(Filters(installation=row["installation_id"], run=row["run_id"]), limit, cursor)
    result["items"] = [error_summary(item) if item["kind"] == "errors" else item for item in result["items"]]
    return result
