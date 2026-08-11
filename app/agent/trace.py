from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, TYPE_CHECKING

from app.core.interaction import get_interaction_id
from app.llm.prompts.runtime import estimate_prompt_tokens

if TYPE_CHECKING:
    from app.llm.prompts.types import ContextSnapshot, PromptInspection


TRACE_PROVENANCE_KEY = "_sakura_trace_provenance"
TRACE_FILE_NAME = "sakura-agent-trace.log"
TRACE_STAGING_DIR = ".agent-trace-staging"
TRACE_MAX_FILE_BYTES = 32 * 1024 * 1024
TRACE_MAX_TOTAL_BYTES = 512 * 1024 * 1024
TRACE_RETENTION_DAYS = 30
TRACE_TEXT_VALUE_MAX_BYTES = 1024 * 1024
TRACE_DISPLAY_COLUMNS = 100
TRACE_DOCUMENT_SEPARATOR = "\n\n\n"

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|password|passwd|secret|credential|"
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|(^|[_-])token($|[_-]))"
)
_INLINE_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|password|passwd|secret|credential|"
    r"access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*([^\s,;]+)"
)
_AUTHORIZATION_BEARER_RE = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*bearer\s+[^\s,;]+"
)
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[a-z0-9.+-]+/[a-z0-9.+-]+)?(?:;[^,]*)?;base64,(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class AgentTraceSettings:
    enabled: bool = True


def normalize_agent_trace_settings(value: object) -> AgentTraceSettings:
    """Normalize the small trace settings section without importing UI settings."""

    raw = value.get("enabled") if isinstance(value, Mapping) else None
    if raw is None:
        enabled = True
    elif isinstance(raw, bool):
        enabled = raw
    else:
        normalized = str(raw).strip().lower()
        if normalized in {"0", "false", "no", "off", "disabled"}:
            enabled = False
        elif normalized in {"1", "true", "yes", "on", "enabled"}:
            enabled = True
        else:
            enabled = True
    return AgentTraceSettings(enabled=enabled)


@dataclass(frozen=True)
class MessageProvenance:
    kind: str
    runtime_items: tuple[dict[str, Any], ...] = ()
    operation_id: str = ""


@dataclass(frozen=True)
class PromptTraceMetadata:
    purpose: str = "agent_step"
    inspection: PromptInspection | None = None
    snapshot: ContextSnapshot | None = None


@dataclass
class TraceCall:
    operation_id: str
    trace: int
    model_call: int
    purpose: str
    model: str
    auto_operation: bool = False
    reply_index: int | None = None


@dataclass
class _TraceOperation:
    operation_id: str
    trace: int
    staging_path: Path
    next_model_call: int = 1
    documents: list[dict[str, Any]] = field(default_factory=list)


_BOUND_OPERATION: ContextVar[str] = ContextVar("sakura_agent_trace_operation", default="")


def traced_message(message: Mapping[str, Any], kind: str, **details: Any) -> dict[str, Any]:
    output = dict(message)
    output[TRACE_PROVENANCE_KEY] = MessageProvenance(kind=kind, **details)
    return output


def message_provenance(message: Mapping[str, Any]) -> MessageProvenance | None:
    value = message.get(TRACE_PROVENANCE_KEY)
    return value if isinstance(value, MessageProvenance) else None


def strip_message_provenance(message: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in message.items() if key != TRACE_PROVENANCE_KEY}


class AgentTraceRecorder:
    """Best-effort local Prompt/Agent trace with per-operation staging."""

    def __init__(
        self,
        app_root: Path,
        settings: AgentTraceSettings | None = None,
        *,
        max_file_bytes: int = TRACE_MAX_FILE_BYTES,
        max_total_bytes: int = TRACE_MAX_TOTAL_BYTES,
        retention_days: int = TRACE_RETENTION_DAYS,
        now: Any | None = None,
    ) -> None:
        self.app_root = Path(app_root)
        self.settings = settings or AgentTraceSettings()
        self.log_dir = self.app_root / "data" / "logs"
        self.path = self.log_dir / TRACE_FILE_NAME
        self.staging_dir = self.log_dir / TRACE_STAGING_DIR
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.retention_days = max(1, int(retention_days))
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = threading.RLock()
        self._commit_lock = threading.Lock()
        self._operations: dict[str, _TraceOperation] = {}
        self._next_trace = 1
        self._known_secrets: list[str] = []
        self._active_date: date | None = None
        if self.settings.enabled:
            self._recover_staging_best_effort()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def add_secret(self, secret: str) -> None:
        value = str(secret or "")
        if len(value) < 6:
            return
        with self._lock:
            if value not in self._known_secrets:
                self._known_secrets.append(value)
                self._known_secrets.sort(key=len, reverse=True)

    @contextmanager
    def operation(self, operation_id: str = "", *, finalize_external: bool = False) -> Iterator[str]:
        if not self.enabled:
            yield ""
            return
        external = (operation_id or get_interaction_id() or _BOUND_OPERATION.get()).strip()
        owns = not external
        resolved = external or f"local-{uuid.uuid4().hex}"
        token = _BOUND_OPERATION.set(resolved)
        try:
            yield resolved
        except BaseException:
            if owns or finalize_external:
                self.finish_operation(resolved, status="failed")
            raise
        else:
            if owns or finalize_external:
                self.finish_operation(resolved, status="completed")
        finally:
            _BOUND_OPERATION.reset(token)

    def start_model_call(
        self,
        *,
        model: str,
        payload: Mapping[str, Any],
        prompt_provenance: Sequence[MessageProvenance | None],
        metadata: PromptTraceMetadata | None = None,
    ) -> TraceCall | None:
        if not self.enabled:
            return None
        operation_id = (_BOUND_OPERATION.get() or get_interaction_id()).strip()
        auto_operation = not operation_id
        operation_id = operation_id or f"api-{uuid.uuid4().hex}"
        try:
            with self._lock:
                operation = self._ensure_operation(operation_id)
                call = TraceCall(
                    operation_id=operation_id,
                    trace=operation.trace,
                    model_call=operation.next_model_call,
                    purpose=(metadata.purpose if metadata else "agent_step"),
                    model=str(model or payload.get("model") or ""),
                    auto_operation=auto_operation,
                )
                operation.next_model_call += 1
                document = self._request_document(
                    call,
                    payload,
                    prompt_provenance,
                    metadata,
                )
                self._append_staging(operation, document)
                return call
        except Exception:
            return None

    def record_model_reply(
        self,
        call: TraceCall | None,
        *,
        raw_message: Mapping[str, Any],
        usage: Mapping[str, Any] | None = None,
        parsed_tool_calls: Sequence[Any] = (),
        pseudo_tool_calls: bool = False,
    ) -> None:
        if call is None or not self.enabled:
            return
        try:
            with self._lock:
                operation = self._operations.get(call.operation_id)
                if operation is None:
                    return
                document = self._reply_document(
                    call,
                    raw_message,
                    usage=usage,
                    parsed_tool_calls=parsed_tool_calls,
                    pseudo_tool_calls=pseudo_tool_calls,
                )
                call.reply_index = len(operation.documents)
                self._append_staging(operation, document)
            if call.auto_operation:
                self.finish_operation(call.operation_id, status="completed")
        except Exception:
            return

    def record_effective_reply(
        self,
        call: TraceCall | None,
        effective_reply: Mapping[str, Any],
        changes: Sequence[str],
    ) -> None:
        if call is None or call.reply_index is None or not changes or not self.enabled:
            return
        try:
            with self._lock:
                operation = self._operations.get(call.operation_id)
                if operation is None or call.reply_index >= len(operation.documents):
                    return
                document = operation.documents[call.reply_index]
                processing = document.setdefault("processing", {})
                processing["effective_reply_changed"] = True
                document["effective_reply"] = _sanitize_trace_value(
                    dict(effective_reply), self._known_secrets, structured=True
                )
                document["changes"] = [str(item) for item in changes if str(item)]
                self._rewrite_staging(operation)
        except Exception:
            return

    def mark_repair_requested(self, call: TraceCall | None, reason: str) -> None:
        if call is None or call.reply_index is None or not self.enabled:
            return
        try:
            with self._lock:
                operation = self._operations.get(call.operation_id)
                if operation is None or call.reply_index >= len(operation.documents):
                    return
                processing = operation.documents[call.reply_index].setdefault("processing", {})
                processing["repair_requested"] = True
                processing["repair_reason"] = _sanitize_text(str(reason), self._known_secrets)
                self._rewrite_staging(operation)
        except Exception:
            return

    def finish_operation(self, operation_id: str = "", *, status: str = "completed") -> bool:
        if not self.enabled:
            return True
        resolved = (operation_id or _BOUND_OPERATION.get() or get_interaction_id()).strip()
        if not resolved:
            return True
        with self._lock:
            operation = self._operations.get(resolved)
            if operation is None:
                return True
            if status != "completed":
                for document in operation.documents:
                    document.setdefault("status", status)
                self._rewrite_staging(operation)
            documents = [dict(document) for document in operation.documents]
        try:
            with self._commit_lock:
                self._commit_documents(documents)
        except Exception:
            return False
        with self._lock:
            self._operations.pop(resolved, None)
        try:
            operation.staging_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def _ensure_operation(self, operation_id: str) -> _TraceOperation:
        existing = self._operations.get(operation_id)
        if existing is not None:
            return existing
        trace = self._next_trace
        self._next_trace += 1
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        safe_name = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        path = self.staging_dir / f"{trace:08d}-{safe_name}.stage"
        path.touch(exist_ok=True)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        operation = _TraceOperation(operation_id=operation_id, trace=trace, staging_path=path)
        self._operations[operation_id] = operation
        return operation

    def _append_staging(self, operation: _TraceOperation, document: dict[str, Any]) -> None:
        safe = _sanitize_trace_value(document, self._known_secrets, structured=True)
        if not isinstance(safe, dict):
            return
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n"
        with operation.staging_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
        operation.documents.append(safe)

    def _rewrite_staging(self, operation: _TraceOperation) -> None:
        temporary = operation.staging_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for document in operation.documents:
                handle.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(operation.staging_path)

    def _request_document(
        self,
        call: TraceCall,
        payload: Mapping[str, Any],
        prompt_provenance: Sequence[MessageProvenance | None],
        metadata: PromptTraceMetadata | None,
    ) -> dict[str, Any]:
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        prompt: list[dict[str, Any]] = []
        history_messages = 0
        history_tokens = 0
        memory_count = 0
        memory_tokens = 0
        dynamic_tokens = 0
        history_group: list[dict[str, Any]] = []
        history_group_chars = 0
        history_group_tokens = 0

        def flush_history_group() -> None:
            nonlocal history_group, history_group_chars, history_group_tokens
            if not history_group:
                return
            prompt.append(
                {
                    "history": {
                        "messages": len(history_group),
                        "chars": history_group_chars,
                        "estimated_tokens": history_group_tokens,
                        "items": history_group,
                    }
                }
            )
            history_group = []
            history_group_chars = 0
            history_group_tokens = 0

        for index, raw_message in enumerate(messages):
            if not isinstance(raw_message, Mapping):
                continue
            provenance = prompt_provenance[index] if index < len(prompt_provenance) else None
            kind = provenance.kind if provenance else _fallback_message_kind(index, raw_message, messages)
            if kind == "history":
                value = _message_trace_value(raw_message, self._known_secrets)
                history_group.append(_compact_history_item(raw_message, self._known_secrets))
                chars = int(value.get("chars", 0))
                tokens = int(value.get("estimated_tokens", 0))
                history_group_chars += chars
                history_group_tokens += tokens
                history_messages += 1
                history_tokens += tokens
                continue
            flush_history_group()
            if kind == "system_prompt":
                part = self._system_prompt_part(raw_message, metadata)
                if provenance and provenance.runtime_items:
                    part["system_prompt"]["appended_runtime_context"] = {
                        "items": _sanitize_trace_value(
                            provenance.runtime_items,
                            self._known_secrets,
                            structured=True,
                        )
                    }
                    counts = _context_counts(provenance.runtime_items)
                    memory_count += counts[0]
                    memory_tokens += counts[1]
                    dynamic_tokens += counts[2]
                prompt.append(part)
                continue
            if kind == "runtime_context":
                items = list(provenance.runtime_items if provenance else ())
                counts = _context_counts(items)
                memory_count += counts[0]
                memory_tokens += counts[1]
                dynamic_tokens += counts[2]
                prompt.append(
                    {
                        "runtime_context": {
                            "role": raw_message.get("role"),
                            "items": _sanitize_trace_value(
                                items,
                                self._known_secrets,
                                structured=True,
                            ),
                        }
                    }
                )
                continue
            value = _message_trace_value(raw_message, self._known_secrets)
            prompt.append({kind: value})
        flush_history_group()

        tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
        schema_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"), default=str)
        tool_tokens = estimate_prompt_tokens(schema_text)
        tool_definitions = [
            _tool_definition_summary(item, self._known_secrets) for item in tools
        ]
        parameters = {
            key: _sanitize_trace_value(value, self._known_secrets, structured=True)
            for key, value in payload.items()
            if key not in {"model", "messages", "tools"}
        }
        dropped = _dropped_context(metadata.snapshot if metadata else None)
        system_tokens = sum(
            estimate_prompt_tokens(str(message.get("content", "")))
            for message in messages
            if isinstance(message, Mapping) and message.get("role") == "system"
        )
        request_tokens = (
            system_tokens
            + sum(
                estimate_prompt_tokens(_message_content_text(message.get("content")))
                for message in messages
                if isinstance(message, Mapping) and message.get("role") != "system"
            )
            + tool_tokens
        )
        return {
            "type": "request",
            "trace": call.trace,
            "model_call": call.model_call,
            "purpose": call.purpose,
            "time": self._now().isoformat(timespec="seconds"),
            "model": call.model,
            "summary": {
                "history_messages": history_messages,
                "history_estimated_tokens": history_tokens,
                "memories": memory_count,
                "memory_estimated_tokens": memory_tokens,
                "dynamic_context_estimated_tokens": dynamic_tokens,
                "tool_schema_estimated_tokens": tool_tokens,
                "request_estimated_tokens": request_tokens,
            },
            "prompt": prompt,
            "tools": {
                "count": len(tools),
                "schema_chars": len(schema_text),
                "estimated_tokens": tool_tokens,
                "definitions": tool_definitions,
            },
            "parameters": parameters,
            "dropped_context": _sanitize_trace_value(
                dropped,
                self._known_secrets,
                structured=True,
            ),
        }

    def _system_prompt_part(
        self,
        message: Mapping[str, Any],
        metadata: PromptTraceMetadata | None,
    ) -> dict[str, Any]:
        content = _message_content_text(message.get("content"))
        sections: list[dict[str, Any]] = []
        inspection = metadata.inspection if metadata else None
        if inspection is not None:
            for section in inspection.sections:
                if section.included and section.cache_scope == "static":
                    sections.append({"id": section.section_id, "chars": section.chars})
        if not sections:
            sections.append({"id": "static.system", "chars": len(content)})
        return {
            "system_prompt": {
                "role": str(message.get("role") or "system"),
                "chars": len(content),
                "sections": sections,
            }
        }

    def _reply_document(
        self,
        call: TraceCall,
        raw_message: Mapping[str, Any],
        *,
        usage: Mapping[str, Any] | None,
        parsed_tool_calls: Sequence[Any],
        pseudo_tool_calls: bool,
    ) -> dict[str, Any]:
        content_value = raw_message.get("content")
        content = "" if content_value is None else str(content_value).strip()
        raw_bytes = content.encode("utf-8")
        document: dict[str, Any] = {
            "type": "reply",
            "trace": call.trace,
            "model_call": call.model_call,
            "purpose": call.purpose,
            "time": self._now().isoformat(timespec="seconds"),
        }
        parse_status = "empty"
        if content:
            try:
                model_output = json.loads(content)
            except json.JSONDecodeError:
                document["raw_text"] = _free_text_value(content, self._known_secrets)
                parse_status = "invalid_json" if _looks_structured(content) else "text"
            else:
                document["model_output"] = _sanitize_trace_value(
                    model_output, self._known_secrets, structured=True
                )
                parse_status = "valid"
        else:
            document["raw_text"] = []
        document["raw_chars"] = len(content)
        document["raw_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        tool_calls = [_tool_call_value(item) for item in parsed_tool_calls]
        document["tool_calls"] = _sanitize_trace_value(
            tool_calls, self._known_secrets, structured=True
        )
        document["usage"] = _usage_value(usage)
        document["processing"] = {
            "parse_status": parse_status,
            "repair_requested": False,
            "effective_reply_changed": False,
            **({"tool_call_source": "pseudo"} if pseudo_tool_calls and tool_calls else {}),
        }
        return document

    def _commit_documents(self, documents: Sequence[Mapping[str, Any]]) -> None:
        if not documents:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        block = TRACE_DOCUMENT_SEPARATOR.join(
            _pretty_trace_document(document) for document in documents
        ) + "\n"
        now = self._now()
        self._rotate_if_needed(now.date(), len(block.encode("utf-8")))
        needs_separator = self.path.exists() and self.path.stat().st_size > 0
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            if needs_separator:
                handle.write("\n\n")
            handle.write(block)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._active_date = now.date()
        self._apply_retention(now)

    def _rotate_if_needed(self, current_date: date, pending_bytes: int) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._active_date = current_date
            return
        active_date = self._active_date or datetime.fromtimestamp(
            self.path.stat().st_mtime
        ).astimezone().date()
        if active_date == current_date and self.path.stat().st_size + pending_bytes <= self.max_file_bytes:
            return
        sequence = 1
        while True:
            target = self.log_dir / f"sakura-agent-trace.{active_date.isoformat()}.{sequence}.log"
            if not target.exists():
                self.path.replace(target)
                break
            sequence += 1
        self._active_date = current_date

    def _apply_retention(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self.retention_days)
        archives = sorted(
            self.log_dir.glob("sakura-agent-trace.*.log"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
        )
        for archive in list(archives):
            try:
                modified = datetime.fromtimestamp(archive.stat().st_mtime).astimezone()
                if modified < cutoff:
                    archive.unlink()
                    archives.remove(archive)
            except OSError:
                continue
        files = [path for path in [*archives, self.path] if path.exists()]
        total = sum(path.stat().st_size for path in files)
        for archive in archives:
            if total <= self.max_total_bytes:
                break
            try:
                size = archive.stat().st_size
                archive.unlink()
                total -= size
            except OSError:
                continue

    def _recover_staging_best_effort(self) -> None:
        if not self.staging_dir.exists():
            return
        for staging in sorted(self.staging_dir.glob("*.stage")):
            try:
                documents = [
                    json.loads(line)
                    for line in staging.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if not documents or not all(isinstance(item, dict) for item in documents):
                    raise ValueError("empty staging")
                for document in documents:
                    document["status"] = "interrupted"
                with self._commit_lock:
                    self._commit_documents(documents)
                staging.unlink(missing_ok=True)
                self._next_trace = max(
                    self._next_trace,
                    max(int(item.get("trace", 0)) for item in documents) + 1,
                )
            except Exception:
                try:
                    staging.replace(staging.with_suffix(".corrupt"))
                except OSError:
                    pass


def _fallback_message_kind(
    index: int,
    message: Mapping[str, Any],
    messages: Sequence[Any],
) -> str:
    role = str(message.get("role") or "")
    if index == 0 and role == "system":
        return "system_prompt"
    if role == "tool":
        return "tool_result"
    if role == "assistant" and message.get("tool_calls"):
        return "assistant_tool_call"
    if role == "user":
        later_user = any(
            isinstance(item, Mapping) and item.get("role") == "user"
            for item in messages[index + 1 :]
        )
        return "history" if later_user else "user_input"
    return "history"


def _message_trace_value(message: Mapping[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    clean = strip_message_provenance(message)
    content = clean.pop("content", "")
    text = _message_content_text(content)
    output: dict[str, Any] = {
        "role": str(message.get("role") or ""),
        "content": (
            _free_text_value(text, secrets)
            if isinstance(content, str)
            else _sanitize_trace_value(content, secrets, structured=True)
        ),
        "chars": len(text),
        "estimated_tokens": estimate_prompt_tokens(text),
    }
    for key, value in clean.items():
        if key != "role":
            output[key] = _sanitize_trace_value(value, secrets, structured=True)
    return output


def _compact_history_item(message: Mapping[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    clean = strip_message_provenance(message)
    content = clean.pop("content", "")
    output: dict[str, Any] = {
        "role": str(clean.pop("role", "")),
        "content": (
            _compact_free_text_value(content, secrets)
            if isinstance(content, str)
            else _sanitize_trace_value(content, secrets, structured=True)
        ),
    }
    for key, value in clean.items():
        output[key] = _sanitize_trace_value(value, secrets, structured=True)
    return output


def _compact_free_text_value(text: str, secrets: Sequence[str]) -> Any:
    value = _free_text_value(text, secrets)
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _tool_definition_summary(item: Any, secrets: Sequence[str]) -> dict[str, Any]:
    encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str)
    function = item.get("function") if isinstance(item, Mapping) else None
    raw_name = function.get("name") if isinstance(function, Mapping) else ""
    return {
        "name": _sanitize_text(str(raw_name or "<unnamed>"), secrets),
        "schema_chars": len(encoded),
        "estimated_tokens": estimate_prompt_tokens(encoded),
    }


def _pretty_trace_document(document: Mapping[str, Any]) -> str:
    """Render readable JSON while keeping repetitive tool summaries to one line."""

    def render(value: Any, *, level: int, path: tuple[str, ...]) -> list[str]:
        indent = "  " * level
        if isinstance(value, Mapping):
            if not value:
                return [f"{indent}{{}}"]
            lines = [f"{indent}{{"]
            items = list(value.items())
            for index, (key, child) in enumerate(items):
                encoded_key = json.dumps(str(key), ensure_ascii=False)
                child_path = (*path, str(key))
                child_lines = render(child, level=level + 1, path=child_path)
                child_lines[0] = f'{"  " * (level + 1)}{encoded_key}: {child_lines[0].lstrip()}'
                if index < len(items) - 1:
                    child_lines[-1] += ","
                lines.extend(child_lines)
            lines.append(f"{indent}}}")
            return lines
        if isinstance(value, list):
            if not value:
                return [f"{indent}[]"]
            lines = [f"{indent}["]
            for index, child in enumerate(value):
                if path == ("tools", "definitions") and isinstance(child, Mapping):
                    child_lines = [
                        f"{'  ' * (level + 1)}{json.dumps(child, ensure_ascii=False)}"
                    ]
                else:
                    child_lines = render(child, level=level + 1, path=(*path, "[]"))
                if index < len(value) - 1:
                    child_lines[-1] += ","
                lines.extend(child_lines)
            lines.append(f"{indent}]")
            return lines
        return [f"{indent}{json.dumps(value, ensure_ascii=False)}"]

    return "\n".join(render(document, level=0, path=()))


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, default=str)
    parts: list[str] = []
    for item in content:
        if isinstance(item, Mapping) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _context_items(snapshot: ContextSnapshot | None) -> tuple[dict[str, Any], ...]:
    if snapshot is None:
        return ()
    items: list[dict[str, Any]] = []
    for decision in snapshot.selected:
        fragment = decision.fragment
        source = fragment.source
        kind = (
            "memory"
            if source == "memory"
            else "plugin"
            if source.startswith("plugin:")
            else "session"
            if source == "session"
            else "runtime"
        )
        value: dict[str, Any] = {
            "id": fragment.fragment_id,
            "content": _free_text_value(fragment.content, ()),
            "estimated_tokens": decision.estimated_tokens,
        }
        metadata = dict(getattr(fragment, "metadata", {}) or {})
        if kind == "memory":
            for key in ("score", "source"):
                if key in metadata and metadata[key] is not None:
                    value[key] = metadata[key]
        items.append({kind: value})
    return tuple(items)


def prompt_metadata_with_context(metadata: PromptTraceMetadata | None) -> tuple[dict[str, Any], ...]:
    return _context_items(metadata.snapshot if metadata else None)


def _context_counts(items: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    memories = 0
    memory_tokens = 0
    dynamic_tokens = 0
    for item in items:
        if not isinstance(item, Mapping) or not item:
            continue
        kind, value = next(iter(item.items()))
        if not isinstance(value, Mapping):
            continue
        tokens = int(value.get("estimated_tokens") or 0)
        dynamic_tokens += tokens
        if kind == "memory":
            memories += 1
            memory_tokens += tokens
    return memories, memory_tokens, dynamic_tokens


def _dropped_context(snapshot: ContextSnapshot | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    output: list[dict[str, Any]] = []
    for decision in snapshot.dropped:
        fragment = decision.fragment
        output.append(
            {
                "id": fragment.fragment_id,
                "source": fragment.source,
                "chars": len(fragment.content),
                "estimated_tokens": decision.estimated_tokens,
                "reason": decision.drop_reason,
            }
        )
    return output


def _tool_call_value(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    return {
        "id": str(getattr(item, "id", "")),
        "type": "function",
        "name": str(getattr(item, "name", "")),
        "arguments": getattr(item, "arguments", {}),
        **(
            {"arguments_error": str(getattr(item, "arguments_error"))}
            if getattr(item, "arguments_error", "")
            else {}
        ),
    }


def _usage_value(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(usage, Mapping):
        return {}
    return {
        key: value
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
        )
        if (value := usage.get(key)) is not None
    }


def _free_text_value(text: str, secrets: Sequence[str]) -> list[str] | dict[str, Any]:
    sanitized = _sanitize_text(text, secrets)
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= TRACE_TEXT_VALUE_MAX_BYTES:
        return _wrap_display_lines(sanitized)
    head = sanitized[:262_144]
    tail = sanitized[-262_144:]
    return {
        "head": _wrap_display_lines(head),
        "tail": _wrap_display_lines(tail),
        "chars": len(sanitized),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "truncated": True,
    }


def _wrap_display_lines(text: str, width: int = TRACE_DISPLAY_COLUMNS) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for source_line in text.splitlines() or [text]:
        remaining = source_line
        if not remaining:
            lines.append("")
            continue
        while len(remaining) > width:
            split_at = remaining.rfind(" ", 0, width + 1)
            if split_at <= 0:
                split_at = width
            lines.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip(" ")
        lines.append(remaining)
    return lines


def _sanitize_trace_value(value: Any, secrets: Sequence[str], *, structured: bool) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {
            "type": "binary",
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, str):
        data_url = _DATA_URL_RE.match(value)
        if data_url:
            encoded_body = data_url.group("body").encode("ascii", errors="ignore")
            try:
                body = base64.b64decode(encoded_body, validate=True)
            except (ValueError, TypeError):
                body = encoded_body
            return {
                "type": "binary",
                "mime": data_url.group("mime") or "application/octet-stream",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        sanitized = _sanitize_text(value, secrets)
        if len(sanitized.encode("utf-8")) > TRACE_TEXT_VALUE_MAX_BYTES:
            truncated = _free_text_value(sanitized, ())
            return json.dumps(truncated, ensure_ascii=False) if structured else truncated
        return sanitized
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY_RE.search(key):
                output[key] = "[REDACTED]"
            else:
                output[key] = _sanitize_trace_value(child, secrets, structured=structured)
        return output
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_trace_value(item, secrets, structured=structured) for item in value]
    return _sanitize_text(str(value), secrets)


def _sanitize_text(text: str, secrets: Sequence[str]) -> str:
    output = str(text)
    for secret in secrets:
        if secret:
            output = output.replace(secret, "[REDACTED]")
    output = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", output)
    output = _AUTHORIZATION_BEARER_RE.sub("Authorization=[REDACTED]", output)
    output = _INLINE_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", output)
    return output


def _looks_structured(content: str) -> bool:
    stripped = content.lstrip()
    return stripped.startswith(("{", "[", "```json")) or '"segments"' in content


__all__ = [
    "AgentTraceRecorder",
    "AgentTraceSettings",
    "MessageProvenance",
    "PromptTraceMetadata",
    "TRACE_PROVENANCE_KEY",
    "TraceCall",
    "message_provenance",
    "normalize_agent_trace_settings",
    "prompt_metadata_with_context",
    "strip_message_provenance",
    "traced_message",
]
