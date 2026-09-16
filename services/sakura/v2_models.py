"""Versioned, body-free diagnostic wire contract shared by ingestion and exports."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from models import (
    StrictModel,
    PrivacyCheckedModel,
    ErrorReport,
    TelemetryEventItem,
    ModelCallItem,
    SafeToken,
    ErrorCode,
    RelativeFile,
    SmallCount,
)

Millis = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]


class DiagnosticContext(StrictModel):
    build_id: SafeToken = Field(alias="buildId")
    environment: Literal["production", "development", "acceptance"]
    generation: SafeToken | None = None
    occurred_ms: Millis = Field(alias="occurredMs")


class DiagnosticDetail(StrictModel):
    severity: Literal["warning", "error", "critical"] | None = None
    impact: Literal["unavailable", "degraded", "diagnostic"] | None = None
    stage: SafeToken | None = None
    reason_code: ErrorCode | None = Field(default=None, alias="reasonCode")
    cause_type: SafeToken | None = Field(default=None, alias="causeType")
    file: RelativeFile | None = None
    line: Annotated[int, Field(ge=1, le=10_000_000)] | None = None
    column: Annotated[int, Field(ge=1, le=10_000_000)] | None = None
    surface: SafeToken | None = None
    timeout_ms: Millis | None = Field(default=None, alias="timeoutMs")
    elapsed_ms: Millis | None = Field(default=None, alias="elapsedMs")
    exit_code: Annotated[int, Field(ge=-(2**31), le=2**32 - 1)] | None = Field(
        default=None, alias="exitCode"
    )
    child_exited: bool | None = Field(default=None, alias="childExited")
    probe_outcome: (
        Literal[
            "ready",
            "timeout",
            "spawn_failed",
            "exited",
            "invalid_output",
            "unavailable",
        ]
        | None
    ) = Field(default=None, alias="probeOutcome")
    primary_code: ErrorCode | None = Field(default=None, alias="primaryCode")
    recovery_code: ErrorCode | None = Field(default=None, alias="recoveryCode")
    recovery_outcome: Literal["success", "failed", "skipped", "unknown"] | None = Field(
        default=None, alias="recoveryOutcome"
    )
    source_exists: bool | None = Field(default=None, alias="sourceExists")
    staged_exists: bool | None = Field(default=None, alias="stagedExists")
    backup_exists: bool | None = Field(default=None, alias="backupExists")
    outcome: Literal["success", "failed", "cancelled", "degraded", "skipped"] | None = (
        None
    )
    fingerprint: SafeToken | None = None
    occurrence_count: Millis | None = Field(default=None, alias="occurrenceCount")
    dropped: Millis | None = None
    failed: Millis | None = None
    rejected: Millis | None = None
    repair_reason: SafeToken | None = Field(default=None, alias="repairReason")
    repair_outcome: (
        Literal["valid", "invalid", "request_failed", "cancelled"] | None
    ) = Field(default=None, alias="repairOutcome")

    @model_validator(mode="after")
    def location_requires_file(self):
        if (self.line is not None or self.column is not None) and self.file is None:
            raise ValueError("location requires safe file")
        return self


class RequestDiagnostic(StrictModel):
    fault_domain: (
        Literal[
            "authentication",
            "rate_limit",
            "provider",
            "transport",
            "protocol",
            "context",
            "compatibility",
            "cancelled",
            "unknown",
        ]
        | None
    ) = Field(default=None, alias="faultDomain")
    reason_code: ErrorCode | None = Field(default=None, alias="reasonCode")
    http_status: Annotated[int, Field(ge=100, le=599)] | None = Field(
        default=None, alias="httpStatus"
    )
    stage: (
        Literal["connect", "read", "decode", "request", "response", "unknown"] | None
    ) = None
    attempt_count: SmallCount | None = Field(default=None, alias="attemptCount")
    compatibility_fallback: (
        Literal["response_format", "temperature", "runtime_context_role"] | None
    ) = Field(default=None, alias="compatibilityFallback")


class ErrorReportV2(ErrorReport):
    schema_version: Literal[2] = Field(alias="schema")
    diagnostics: DiagnosticContext
    details: DiagnosticDetail
    fingerprint_version: Literal[2] = Field(alias="fingerprintVersion")

    @model_validator(mode="after")
    def require_classification(self):
        if self.details.severity is None or self.details.impact is None:
            raise ValueError("v2 error requires severity and impact")
        return self


class EventItemV2(TelemetryEventItem):
    event: Literal[
        "app.started",
        "app.ready",
        "migration.completed",
        "migration.failed",
        "feature.used",
        "shell.ready",
        "core.ready",
        "chat.ready",
        "chat.finished",
        "tts.finished",
        "migration.recovery",
        "reply.repair.finished",
        "diagnostics.summary",
        "error.repeated",
    ]
    operation_id: SafeToken | None = Field(default=None, alias="operationId")
    diagnostics: DiagnosticContext
    details: DiagnosticDetail = Field(default_factory=DiagnosticDetail)


class EventBatchV2(PrivacyCheckedModel):
    schema_version: Literal[2] = Field(alias="schema")
    items: Annotated[list[EventItemV2], Field(min_length=1, max_length=10)]


class ModelItemV2(ModelCallItem):
    diagnostics: DiagnosticContext
    request: RequestDiagnostic = Field(default_factory=RequestDiagnostic)


class ModelBatchV2(PrivacyCheckedModel):
    schema_version: Literal[2] = Field(alias="schema")
    items: Annotated[list[ModelItemV2], Field(min_length=1, max_length=10)]
