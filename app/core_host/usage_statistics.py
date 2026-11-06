"""设置统计页的只读查询入口。"""
import hmac

from app.core.diagnostics import exception_diagnostics
from app.core.runtime_log import log_event
from app.core_host.protocol import response
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineStore
from app.storage.usage import UsageStore


class UsageStatisticsBoundary:
    def __init__(self, generation_id, generation_credential, user_root):
        self.generation_id = generation_id
        self.credential = generation_credential
        self.usage = UsageStore(user_root)
        self.timeline = TimelineStore(StoragePaths(user_root).timeline_database())

    def handle(self, request):
        if request.get("generationId") != self.generation_id or not hmac.compare_digest(request.get("generationCredential", ""), self.credential):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            payload = request["payload"]
            result = self.usage.snapshot(payload.get("period", "30d"), start_date=payload.get("startDate"), end_date=payload.get("endDate"))
            result["conversation"] = self.timeline.usage_summary(result["start"], result["end"], hourly=result["granularity"] == "hour")
            return response(request, generation_id=self.generation_id,
                            generation_credential=self.credential, payload=result)
        except Exception as error:
            log_event("Usage", "使用统计读取失败", exception_diagnostics(error,
                reason_code="USAGE_READ_FAILED", stage="statistics"), event="usage.read.failed", severity="warning")
            return response(request, generation_id=self.generation_id, generation_credential=self.credential,
                            error={"code": "USAGE_READ_FAILED", "message": str(error), "retryable": True})
