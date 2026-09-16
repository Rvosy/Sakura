from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


SAFE_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
STABLE_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"
EVENT_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"
ERROR_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{0,127}$"


def _validate_uuid_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("must be a UUID string") from exc
    canonical = str(parsed)
    if canonical != value.lower():
        raise ValueError("must use canonical UUID form")
    return canonical


def _validate_relative_file(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a relative file string")
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or "://" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError("must be repo-relative or module-relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("must be repo-relative or module-relative")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("contains an unsafe path segment")
    return value


UUIDText = Annotated[
    str,
    StringConstraints(strict=True, min_length=36, max_length=36),
    AfterValidator(_validate_uuid_text),
]
SafeToken = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=128, pattern=SAFE_TOKEN_PATTERN
    ),
]
VersionText = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=64, pattern=VERSION_PATTERN
    ),
]
StableName = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=128, pattern=STABLE_NAME_PATTERN
    ),
]
EventName = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=EVENT_PATTERN),
]
ErrorCode = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=128, pattern=ERROR_CODE_PATTERN
    ),
]
RelativeFile = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=240),
    AfterValidator(_validate_relative_file),
]


_PRIVATE_MARKERS = (
    "PRIVATE_CHAT_",
    "PRIVATE_PROMPT_",
    "PRIVATE_KEY_",
    "PRIVATE_MEMORY_",
    "PRIVATE_TOOL_ARGS_",
    "PRIVATE_EXCEPTION_MESSAGE_",
    "PRIVATE_MODEL_ID_",
)
_ABSOLUTE_PATH_RE = re.compile(r"(?:^|\s)(?:/Users/|/home/|[A-Za-z]:[\\/]|\\\\)")
_URL_RE = re.compile(r"(?i)\b(?:https?|file)://")
_API_KEY_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _assert_privacy_safe(value: Any) -> None:
    for item in _iter_strings(value):
        if (
            any(marker in item for marker in _PRIVATE_MARKERS)
            or _ABSOLUTE_PATH_RE.search(item)
            or _URL_RE.search(item)
            or _API_KEY_RE.search(item)
        ):
            raise ValueError("contains content excluded by the privacy boundary")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrivacyCheckedModel(StrictModel):
    @model_validator(mode="after")
    def enforce_privacy_boundary(self):
        _assert_privacy_safe(self.model_dump(mode="python"))
        return self


class AppInfo(StrictModel):
    version: VersionText
    build: SafeToken | None = None
    channel: Literal["stable", "prerelease", "development"] | None = None


class SystemInfo(StrictModel):
    platform: Literal["windows", "macos", "linux"]
    os_version: VersionText | None = Field(default=None, alias="osVersion")
    arch: Literal["x86", "x86_64", "arm", "arm64", "aarch64", "unknown"] | None = None
    webview_version: VersionText | None = Field(default=None, alias="webviewVersion")


class ErrorInfo(StrictModel):
    component: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=64, pattern=EVENT_PATTERN
        ),
    ]
    event: EventName
    code: ErrorCode
    severity: Literal["warning", "error", "critical"] | None = None
    location: SafeToken | None = None
    exception_type: StableName | None = Field(default=None, alias="exceptionType")
    fingerprint: SafeToken | None = None


class ErrorContext(StrictModel):
    install_kind: Literal["fresh", "upgrade", "legacy_import", "unknown"] = Field(
        alias="installKind"
    )
    upgraded_from: VersionText | None = Field(default=None, alias="upgradedFrom")

    @model_validator(mode="after")
    def validate_upgrade_context(self):
        if self.install_kind != "upgrade" and self.upgraded_from is not None:
            raise ValueError("upgradedFrom is only valid for upgrade")
        return self


class StackFrame(StrictModel):
    module: StableName | None = None
    function: StableName | None = None
    file: RelativeFile | None = None
    line: Annotated[int, Field(strict=True, ge=1, le=10_000_000)] | None = None

    @model_validator(mode="after")
    def require_frame_identity(self):
        if self.module is None and self.function is None and self.file is None:
            raise ValueError("stack frame has no safe identity")
        if self.line is not None and self.file is None:
            raise ValueError("line requires file")
        return self


class Breadcrumb(StrictModel):
    offset_ms: Annotated[int, Field(strict=True, ge=-86_400_000, le=0)] = Field(
        alias="offsetMs"
    )
    source: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=32, pattern=EVENT_PATTERN
        ),
    ]
    severity: Literal["debug", "info", "warning", "error", "critical"]
    channel: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=32, pattern=EVENT_PATTERN
        ),
    ]
    event: EventName
    code: ErrorCode | None = None
    outcome: Literal["success", "failed", "cancelled", "degraded", "skipped"] | None = (
        None
    )
    elapsed_ms: Annotated[int, Field(strict=True, ge=0, le=86_400_000)] | None = Field(
        default=None,
        alias="elapsedMs",
    )


class ErrorReport(PrivacyCheckedModel):
    schema_version: Literal[1] = Field(alias="schema")
    report_id: UUIDText = Field(alias="reportId")
    installation_id: UUIDText = Field(alias="installationId")
    run_id: SafeToken | None = Field(default=None, alias="runId")
    operation_id: SafeToken | None = Field(default=None, alias="operationId")
    app: AppInfo
    system: SystemInfo
    error: ErrorInfo
    context: ErrorContext | None = None
    stack: Annotated[list[StackFrame], Field(max_length=16)] = Field(
        default_factory=list
    )
    breadcrumbs: Annotated[list[Breadcrumb], Field(max_length=40)] = Field(
        default_factory=list
    )


TelemetryEventName = Literal[
    "app.started",
    "app.ready",
    "migration.completed",
    "migration.failed",
    "feature.used",
]
FeatureName = Literal["chat", "tts", "memory", "tools", "plugins"]


class TelemetryEventItem(StrictModel):
    installation_id: UUIDText = Field(alias="installationId")
    run_id: SafeToken | None = Field(default=None, alias="runId")
    app_version: VersionText = Field(alias="appVersion")
    platform: Literal["windows", "macos", "linux"]
    os_version: VersionText | None = Field(default=None, alias="osVersion")
    arch: Literal["x86", "x86_64", "arm", "arm64", "aarch64", "unknown"] | None = None
    event: TelemetryEventName
    feature: FeatureName | None = None
    duration_ms: Annotated[int, Field(strict=True, ge=0, le=604_800_000)] | None = (
        Field(
            default=None,
            alias="durationMs",
        )
    )
    from_version: VersionText | None = Field(default=None, alias="fromVersion")
    to_version: VersionText | None = Field(default=None, alias="toVersion")
    error_code: ErrorCode | None = Field(default=None, alias="errorCode")

    @model_validator(mode="after")
    def validate_event_fields(self):
        populated = {
            "feature": self.feature is not None,
            "duration": self.duration_ms is not None,
            "from": self.from_version is not None,
            "to": self.to_version is not None,
            "error": self.error_code is not None,
        }
        if self.event == "app.started" and any(populated.values()):
            raise ValueError("app.started does not accept event-specific fields")
        if self.event == "app.ready" and any(
            populated[key] for key in ("feature", "from", "to", "error")
        ):
            raise ValueError("app.ready only accepts durationMs")
        if self.event in {"migration.completed", "migration.failed"}:
            if populated["feature"]:
                raise ValueError("migration events do not accept feature")
            if self.event == "migration.completed" and populated["error"]:
                raise ValueError("migration.completed does not accept errorCode")
        if self.event == "feature.used":
            if self.feature is None:
                raise ValueError("feature.used requires feature")
            if any(populated[key] for key in ("duration", "from", "to", "error")):
                raise ValueError("feature.used only accepts feature")
        return self


class TelemetryEventBatch(PrivacyCheckedModel):
    schema_version: Literal[1] = Field(alias="schema")
    items: Annotated[list[TelemetryEventItem], Field(min_length=1, max_length=10)]


Purpose = Literal[
    "agent_step",
    "final_reply",
    "reply_repair",
    "screen_observation",
    "proactive_reply",
    "background_agent",
    "memory_curation",
    "memory_curation_repair",
]

TokenCount = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
SmallCount = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class ProviderUsage(StrictModel):
    prompt_tokens: TokenCount | None = Field(default=None, alias="promptTokens")
    completion_tokens: TokenCount | None = Field(default=None, alias="completionTokens")
    total_tokens: TokenCount | None = Field(default=None, alias="totalTokens")
    input_tokens: TokenCount | None = Field(default=None, alias="inputTokens")
    output_tokens: TokenCount | None = Field(default=None, alias="outputTokens")
    cached_input_tokens: TokenCount | None = Field(
        default=None, alias="cachedInputTokens"
    )
    reasoning_tokens: TokenCount | None = Field(default=None, alias="reasoningTokens")


class SakuraEstimate(StrictModel):
    request_tokens: TokenCount | None = Field(default=None, alias="requestTokens")
    history_tokens: TokenCount | None = Field(default=None, alias="historyTokens")
    memory_tokens: TokenCount | None = Field(default=None, alias="memoryTokens")
    dynamic_context_tokens: TokenCount | None = Field(
        default=None, alias="dynamicContextTokens"
    )
    tool_schema_tokens: TokenCount | None = Field(
        default=None, alias="toolSchemaTokens"
    )
    history_messages: SmallCount | None = Field(default=None, alias="historyMessages")
    memories: SmallCount | None = None
    tool_count: SmallCount | None = Field(default=None, alias="toolCount")


class ModelCallItem(StrictModel):
    installation_id: UUIDText = Field(alias="installationId")
    run_id: SafeToken | None = Field(default=None, alias="runId")
    operation_id: SafeToken | None = Field(default=None, alias="operationId")
    app_version: VersionText = Field(alias="appVersion")
    model_call: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] = Field(
        alias="modelCall"
    )
    purpose: Purpose
    model_family: Literal[
        "openai", "anthropic", "gemini", "deepseek", "custom", "unknown"
    ] = Field(alias="modelFamily")
    outcome: Literal["success", "failed", "cancelled"]
    error_code: ErrorCode | None = Field(default=None, alias="errorCode")
    latency_ms: Annotated[int, Field(strict=True, ge=0, le=604_800_000)] | None = Field(
        default=None,
        alias="latencyMs",
    )
    context_window_tokens: TokenCount | None = Field(
        default=None, alias="contextWindowTokens"
    )
    context_window_source: Literal["provider", "configured", "fallback", "unknown"] = (
        Field(alias="contextWindowSource")
    )
    usage: ProviderUsage | None = None
    estimate: SakuraEstimate | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "success" and self.error_code is not None:
            raise ValueError("successful calls do not accept errorCode")
        return self


class ModelCallBatch(PrivacyCheckedModel):
    schema_version: Literal[1] = Field(alias="schema")
    items: Annotated[list[ModelCallItem], Field(min_length=1, max_length=10)]
