"""Original diagnostic evidence. Legacy token/privacy rules only govern v1/v2."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from models import (
    StrictModel, UUIDText, SafeToken, AppInfo, SystemInfo, ErrorInfo,
    ErrorContext, StackFrame, Breadcrumb,
)
from v2_models import DiagnosticContext, DiagnosticDetail

DiagnosticText = Annotated[str, StringConstraints(strict=True, max_length=32768)]


class ErrorInfoV3(ErrorInfo):
    component: SafeToken


class BreadcrumbV3(Breadcrumb):
    diagnostic: DiagnosticText | None = None


class ErrorDetailV3(DiagnosticDetail):
    severity: Literal["warning", "error", "critical"]
    impact: Literal["unavailable", "degraded", "diagnostic"]


class ErrorReportV3(StrictModel):
    schema_version: Literal[3] = Field(alias="schema")
    fingerprint_version: Literal[3] = Field(alias="fingerprintVersion")
    report_id: UUIDText = Field(alias="reportId")
    installation_id: UUIDText = Field(alias="installationId")
    run_id: SafeToken = Field(alias="runId")
    operation_id: SafeToken | None = Field(default=None, alias="operationId")
    app: AppInfo
    system: SystemInfo
    error: ErrorInfoV3
    context: ErrorContext | None = None
    diagnostics: DiagnosticContext
    details: ErrorDetailV3
    stack: Annotated[list[StackFrame], Field(max_length=16)] = Field(default_factory=list)
    breadcrumbs: Annotated[list[BreadcrumbV3], Field(max_length=40)] = Field(default_factory=list)
    # Scalar context only; full requests, chat histories and locals are not collected.
    # URLs, paths, Unicode and exception text are valid diagnostic data.
    evidence: Annotated[
        dict[Annotated[str, StringConstraints(max_length=64)], DiagnosticText | int | Annotated[float, Field(allow_inf_nan=False)] | bool],
        Field(max_length=40),
    ]
