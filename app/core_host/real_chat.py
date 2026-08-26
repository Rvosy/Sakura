"""Generation-scoped headless real chat boundary for Runtime v2."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sys
import threading
import urllib.error
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from .chat_fixture import CHAT_CLOSE_TIMEOUT_SECONDS, CHAT_MESSAGE_LIMIT
from .protocol import event, response

if TYPE_CHECKING:
    from app.core.cancellation import CancellationToken
    from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore
    from app.storage.timeline import NewTimelineEntry, TimelineEntry, TimelineStore


REAL_CHAT_EXECUTION_LIMIT = 1
HOST_CHAT_COMPLETED_EVENT = "sakura.host.chat.completed"
RECENT_PROACTIVE_LIMIT = 3
RECENT_PROACTIVE_TTL_SECONDS = 60 * 60
RECENT_PROACTIVE_UTTERANCE_CHARS = 2000


class RealChatRejection(ValueError):
    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _new_cancellation_token() -> CancellationToken:
    from app.core.cancellation import CancellationToken

    return CancellationToken()


@dataclass
class _Execution:
    operation_id: str
    cancel: CancellationToken = field(default_factory=_new_cancellation_token)
    started: bool = False
    cancel_requested: bool = False
    terminal: str | None = None
    completion_claimed: bool = False
    screen_attachment: _ScreenAttachment | None = None


@dataclass(frozen=True)
class _ScreenAttachment:
    attachment_id: str
    observations: tuple[Any, ...]
    source: str
    visual_id: str | None = None


class RealChatBoundary:
    """Own operation arbitration, real Pipeline calls and best-effort history."""

    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
        plugin_application_provider: Callable[[], object | None] | None = None,
        event_publisher: Callable[[dict[str, Any]], None] | None = None,
        timeline_store: TimelineStore | None = None,
        segment_authorizer: Callable[..., bool | None] | None = None,
    ) -> None:
        if not generation_id.strip() or not generation_credential.strip():
            raise ValueError("real chat generation identity must not be empty")
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._session_provider = session_provider
        self._plugin_application_provider = plugin_application_provider
        self._event_publisher = event_publisher
        self._timeline_error: Exception | None = None
        if timeline_store is not None:
            self._timeline = timeline_store
        else:
            activated_before = _timeline_activation_marker_present(self._app_root)
            try:
                self._timeline = _prepare_runtime_timeline(self._app_root)
            except Exception as exc:
                self._timeline = None
                if activated_before or _timeline_activation_marker_present(self._app_root):
                    self._timeline_error = exc
                else:
                    _log_timeline_migration_failure(exc)
        self._segment_authorizer = segment_authorizer
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._executions: dict[str, _Execution] = {}
        self._pending_screen_attachment: _ScreenAttachment | None = None
        self._pending_runtime_updates: dict[str, Callable[[], None]] = {}
        self._revision = 0
        self._closed = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if self._event_publisher is not None:
                raise RuntimeError("chat event publisher is already configured")
            self._event_publisher = publisher

    def reserve_send(self, request: Mapping[str, Any]) -> None:
        payload = self._validate_send(request)
        operation_id = str(request["id"])
        with self._changed:
            if self._closed:
                raise RealChatRejection("GENERATION_INVALIDATED", "chat generation is closing")
            if self._session_provider() is None:
                raise RealChatRejection("ASSISTANT_NOT_READY", "Assistant is not ready")
            if operation_id in self._executions:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            if len(self._executions) >= REAL_CHAT_EXECUTION_LIMIT:
                raise RealChatRejection(
                    "CHAT_EXECUTION_LIMIT_EXCEEDED",
                    "another chat interaction is active",
                    retryable=True,
                )
            self._apply_pending_runtime_updates_locked()
            attachment_id = payload.get("attachmentId")
            screen_attachment = None
            if attachment_id is not None:
                pending = self._pending_screen_attachment
                if pending is None or pending.attachment_id != attachment_id:
                    raise RealChatRejection(
                        "SCREEN_ATTACHMENT_NOT_FOUND",
                        "screen attachment is stale or unavailable",
                    )
                screen_attachment = pending
                self._pending_screen_attachment = None
            self._executions[operation_id] = _Execution(
                operation_id,
                screen_attachment=screen_attachment,
            )
            self._revision += 1
            self._changed.notify_all()

    def schedule_runtime_update(self, key: str, update: Callable[[], None]) -> None:
        """Apply now when idle, otherwise keep only the latest boundary update."""

        if not key or not callable(update):
            raise ValueError("runtime update is invalid")
        with self._changed:
            if self._closed:
                raise RealChatRejection(
                    "GENERATION_INVALIDATED", "chat generation is closing"
                )
            self._pending_runtime_updates[key] = update
            if not self._executions:
                self._apply_pending_runtime_updates_locked()

    def _apply_pending_runtime_updates_locked(self) -> None:
        pending = self._pending_runtime_updates
        self._pending_runtime_updates = {}
        for key in sorted(pending):
            try:
                pending[key]()
            except Exception:
                # Persisted configuration remains authoritative.  Preserve the
                # newest update for the next operation boundary and surface the
                # immediate failure to the settings/chat caller.
                self._pending_runtime_updates[key] = pending[key]
                raise

    def abandon_send(self, request: Mapping[str, Any]) -> None:
        operation_id = str(request.get("id", ""))
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is not None and not execution.started:
                self._executions.pop(operation_id, None)
                if (
                    execution.screen_attachment is not None
                    and self._pending_screen_attachment is None
                ):
                    self._pending_screen_attachment = execution.screen_attachment
                self._revision += 1
                self._changed.notify_all()

    def start_send(self, request: dict[str, Any]) -> dict[str, Any]:
        """Acknowledge an accepted chat without waiting for Provider completion."""

        self._validate_send(request)
        operation_id = str(request["id"])
        started = threading.Event()
        kickoff_errors: list[BaseException] = []

        def run() -> None:
            try:
                from app.core.interaction import interaction_context

                with interaction_context(operation_id):
                    self.handle_send(request, _on_started=started.set)
            except BaseException as error:  # noqa: BLE001 - owned generation worker
                if not started.is_set():
                    kickoff_errors.append(error)
                else:
                    _safe_diagnostic(error)
                self._drop_execution(operation_id)
            finally:
                started.set()

        worker = threading.Thread(
            target=run,
            name=f"sakura-real-chat-{operation_id}",
        )
        try:
            worker.start()
        except BaseException:
            self._drop_execution(operation_id)
            raise
        if not started.wait(CHAT_CLOSE_TIMEOUT_SECONDS):
            self.cancel_all()
            raise RealChatRejection(
                "CHAT_START_TIMEOUT",
                "chat start acknowledgement timed out",
            )
        if kickoff_errors:
            raise kickoff_errors[0]
        return self._accepted_send_response(request, operation_id)

    def handle_send(
        self,
        request: dict[str, Any],
        *,
        _on_started: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        payload = self._validate_send(request)
        operation_id = str(request["id"])
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None:
                raise RealChatRejection("CHAT_RESERVATION_MISSING", "chat request was not reserved")
            if execution.started:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            execution.started = True
            screen_attachment = execution.screen_attachment
            self._changed.notify_all()

        try:
            self._publish(request, "chat.started", {"operationId": operation_id})
        except BaseException:  # noqa: BLE001 - transport owner will terminate the generation
            self._drop_execution(operation_id)
            raise
        if _on_started is not None:
            _on_started()
        history_status = "saved"
        assistant_committed = False
        terminal = "chat.failed"
        terminal_payload: dict[str, Any]
        runtime = None
        completed_fact: dict[str, Any] | None = None
        plugin_worker: object | None = None
        try:
            from app.core.runtime_log import suppress_runtime_logs
            from app.agent.trace import traced_message
            from app.storage.timeline import NewTimelineEntry, TimelineKind

            execution.cancel.throw_if_cancelled()
            session = self._session_provider()
            if session is None:
                raise _BoundaryFailure("ASSISTANT_NOT_READY", "Assistant is not ready", False)
            character = getattr(session, "character")
            runtime = getattr(session, "runtime", None)
            wait_dependencies = getattr(session, "wait_prompt_dependencies", None)
            if callable(wait_dependencies):
                from app.core.runtime_log import log_event

                dependency_results = wait_dependencies(
                    cancel_checker=execution.cancel.throw_if_cancelled
                )
                for dependency in dependency_results:
                    ready = bool(dependency.get("ready"))
                    log_event(
                        "Context",
                        "Prompt 依赖已就绪" if ready else "Prompt 依赖未就绪，继续降级对话",
                        dependency,
                        severity="info" if ready else "warning",
                        verbosity=1 if ready else 0,
                    )
            timeline = self._timeline
            legacy_history = None
            if timeline is None:
                if self._timeline_error is not None:
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "TIMELINE_DATABASE_INVALID",
                        "Activated chat history is unavailable",
                        False,
                    ) from self._timeline_error
                from app.storage.chat_history import ChatHistoryStore
                from app.storage.paths import StoragePaths

                legacy_history = ChatHistoryStore(
                    StoragePaths(self._app_root).chat_history_for(str(character.id)),
                    str(character.display_name),
                )
                try:
                    legacy_history.assert_compatible_append()
                except Exception as exc:
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "HISTORY_COMPATIBILITY_READ_ONLY",
                        "Chat history is read-only because existing data is incompatible",
                        False,
                    ) from exc
            message = str(payload["message"])
            plugin_worker = (
                self._plugin_application_provider()
                if self._plugin_application_provider is not None
                else getattr(session, "plugin_worker", None)
            )
            if plugin_worker is not None:
                try:
                    getattr(plugin_worker, "emit_event")(
                        "message.user",
                        {"role": "user", "characters": len(message)},
                    )
                except Exception:
                    pass
            if timeline is not None:
                try:
                    history_projection = assemble_recent_turns(
                        timeline.read_all(str(character.id))
                    )
                    recent_messages = _messages_from_turn_projection(history_projection)
                except Exception as exc:
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "TIMELINE_READ_FAILED", "Chat history could not be read", False
                    ) from exc
            else:
                assert legacy_history is not None
                try:
                    history_projection = _turns_from_legacy(legacy_history.load())
                    recent_messages = _messages_from_turn_projection(history_projection)
                except Exception:
                    history_projection = _TurnProjection((), ())
                    recent_messages = []
                    history_status = "degraded"
            request_user_message: dict[str, Any] = {"role": "user", "content": message}
            recorded_message = message
            visual_observation_jobs = []
            input_entries: list[NewTimelineEntry] = []
            turn_id = uuid.uuid4().hex
            created_at = _now_iso()
            if screen_attachment is None or screen_attachment.source != "screen_awareness":
                input_entries.append(
                    NewTimelineEntry(
                        entry_id=uuid.uuid4().hex,
                        turn_id=turn_id,
                        character_id=str(character.id),
                        kind=TimelineKind.HUMAN,
                        origin="chat",
                        created_at=created_at,
                        payload={"text": message},
                    )
                )
            if screen_attachment is not None:
                from app.agent.screen_observation import (
                    build_screen_observation_batch_user_message,
                    build_screen_observation_user_message,
                )

                if screen_attachment.source == "screen_awareness":
                    request_user_message = build_screen_observation_batch_user_message(
                        message, screen_attachment.observations
                    )
                    observation_text = (
                        f"定时屏幕观察已提交给对话模型，共 "
                        f"{len(screen_attachment.observations)} 张截图。"
                    )
                    recorded_message = (
                        f"{message.rstrip()}\n"
                        f"[已附加 {len(screen_attachment.observations)} 张定时屏幕截图]"
                    )
                else:
                    from app.agent.screen_observation import append_manual_observation_marker
                    from app.storage.visual_observation import VisualObservationJob

                    observation = screen_attachment.observations[0]
                    request_user_message = build_screen_observation_user_message(
                        message, observation
                    )
                    observation_text = "用户手动选择的屏幕截图已提交给对话模型。"
                    recorded_message = append_manual_observation_marker(
                        message,
                        observation,
                        screen_attachment.visual_id,
                    )
                    visual_observation_jobs.append(
                        VisualObservationJob(
                            id=str(screen_attachment.visual_id),
                            source="manual_screenshot",
                            user_text=message,
                            observation=observation,
                        )
                    )
                first_observation = screen_attachment.observations[0]
                visual: dict[str, Any] = {
                    "imageCount": len(screen_attachment.observations),
                    "capturedAt": str(getattr(first_observation, "captured_at")),
                }
                if screen_attachment.visual_id is not None:
                    visual["visualId"] = screen_attachment.visual_id
                input_entries.append(
                    NewTimelineEntry(
                        entry_id=uuid.uuid4().hex,
                        turn_id=turn_id,
                        character_id=str(character.id),
                        kind=TimelineKind.OBSERVATION,
                        origin=(
                            "scheduled_screen"
                            if screen_attachment.source == "screen_awareness"
                            else "manual_screen"
                        ),
                        created_at=created_at,
                        payload={"text": observation_text, "visual": visual},
                    )
                )
            request_user_message = traced_message(
                request_user_message,
                "observation_input" if screen_attachment is not None else "user_input",
                turn_id=turn_id,
                entry_ids=tuple(entry.entry_id for entry in input_entries),
                human_entry_id=next(
                    (
                        entry.entry_id
                        for entry in input_entries
                        if entry.kind is TimelineKind.HUMAN
                    ),
                    "",
                ),
                observation_entry_ids=tuple(
                    entry.entry_id
                    for entry in input_entries
                    if entry.kind is TimelineKind.OBSERVATION
                ),
                history_drops=history_projection.dropped,
            )
            messages = [*recent_messages, request_user_message]
            try:
                execution.cancel.throw_if_cancelled()
                if timeline is not None:
                    timeline.append_many(input_entries)
                else:
                    assert legacy_history is not None
                    legacy_history.append("user", recorded_message)
            except Exception as exc:
                history_status = "degraded"
                raise _BoundaryFailure(
                    "TIMELINE_WRITE_FAILED" if timeline is not None else "HISTORY_WRITE_FAILED",
                    "Chat input could not be saved",
                    False,
                ) from exc

            execution.cancel.throw_if_cancelled()
            with suppress_runtime_logs():
                pipeline_kwargs: dict[str, Any] = {
                    "cancel_checker": execution.cancel.throw_if_cancelled,
                }
                if visual_observation_jobs:
                    pipeline_kwargs["visual_observation_jobs"] = visual_observation_jobs
                result = getattr(session, "pipeline").run_user_message(
                    messages,
                    **pipeline_kwargs,
                )
            execution.cancel.throw_if_cancelled()
            unsupported = [
                action
                for action in getattr(result, "actions", [])
                if getattr(action, "type", "") != "tool_call"
            ]
            if unsupported:
                raise _BoundaryFailure(
                    "UNEXPECTED_CHAT_ACTION",
                    "Assistant returned an unsupported action",
                    False,
                )
            if plugin_worker is not None:
                try:
                    reply_text = str(getattr(getattr(result, "reply", None), "speech", ""))
                    getattr(plugin_worker, "emit_event")(
                        "message.ai",
                        {"role": "assistant", "characters": len(reply_text)},
                    )
                except Exception:
                    pass
            execution.cancel.throw_if_cancelled()
            segments = _project_reply(getattr(result, "reply", None))
            semantic_observation_entry = None
            if (
                timeline is not None
                and screen_attachment is not None
                and screen_attachment.source == "screen_awareness"
            ):
                from app.storage.visual_observation import sanitize_timeline_visual_summary

                semantic_observation = sanitize_timeline_visual_summary(
                    getattr(result, "visual_observation", None) or {}
                )
                if semantic_observation is not None:
                    first_observation = screen_attachment.observations[0]
                    semantic_observation_entry = NewTimelineEntry(
                        entry_id=uuid.uuid4().hex,
                        turn_id=turn_id,
                        character_id=str(character.id),
                        kind=TimelineKind.OBSERVATION,
                        origin="scheduled_screen",
                        created_at=_now_iso(),
                        payload={
                            "text": semantic_observation["text"],
                            "visual": {
                                "imageCount": len(screen_attachment.observations),
                                "capturedAt": str(getattr(first_observation, "captured_at")),
                                "analysisStatus": "succeeded",
                                "confidence": semantic_observation["confidence"],
                                "sensitiveRedacted": semantic_observation[
                                    "sensitive_redacted"
                                ],
                            },
                        },
                    )
            assistant_entry_id = uuid.uuid4().hex
            authorized_segments: list[tuple[int, dict[str, Any]]] = []
            for segment_index, segment in enumerate(segments):
                if not segment["text"].strip():
                    continue
                execution.cancel.throw_if_cancelled()
                if self._segment_authorizer is not None:
                    tts_authorized = self._segment_authorizer(
                        operation_id=operation_id,
                        segment_index=segment_index,
                        text=segment["text"],
                        tone=segment["tone"],
                        portrait=segment["portrait"],
                        character_id=str(character.id),
                        history_entry_id=assistant_entry_id,
                    )
                    if tts_authorized is False:
                        segment["suppressTts"] = True
                authorized_segments.append((segment_index, segment))
            if authorized_segments and timeline is not None:
                execution.cancel.throw_if_cancelled()
                try:
                    assistant_entry = NewTimelineEntry(
                        entry_id=assistant_entry_id,
                        turn_id=turn_id,
                        character_id=str(character.id),
                        kind=TimelineKind.ASSISTANT,
                        origin=(
                            "proactive"
                            if screen_attachment is not None
                            and screen_attachment.source == "screen_awareness"
                            else "chat"
                        ),
                        created_at=_now_iso(),
                        payload={"segments": segments},
                    )
                    self._commit_assistant_and_claim(
                        execution,
                        timeline,
                        [
                            *(
                                [semantic_observation_entry]
                                if semantic_observation_entry is not None
                                else []
                            ),
                            assistant_entry,
                        ],
                    )
                    assistant_committed = True
                except Exception as exc:
                    if _is_operation_cancelled(exc):
                        raise
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "TIMELINE_WRITE_FAILED", "Assistant reply could not be saved", False
                    ) from exc
            elif authorized_segments:
                assert legacy_history is not None
                try:
                    self._commit_legacy_assistant_and_claim(
                        execution,
                        legacy_history,
                        authorized_segments,
                        assistant_entry_id,
                    )
                    assistant_committed = True
                except Exception as exc:
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "HISTORY_WRITE_FAILED", "Assistant reply could not be saved", False
                    ) from exc
            terminal = "chat.completed"
            terminal_payload = {
                "operationId": operation_id,
                "reply": {"segments": segments},
                "historyStatus": history_status,
            }
            if assistant_committed and timeline is not None:
                try:
                    completed_fact = {
                        "characterId": str(character.id),
                        "turnId": turn_id,
                        "cursor": timeline.latest_cursor(str(character.id)),
                    }
                except Exception:
                    # Timeline is already committed and the completion terminal
                    # is claimed; cursor notification remains best-effort.
                    completed_fact = None
            elif assistant_committed and legacy_history is not None:
                completed_fact = {
                    "characterId": str(character.id),
                    "legacyHistory": True,
                }
        except BaseException as error:  # noqa: BLE001 - sanitize at the process boundary
            if _is_operation_cancelled(error):
                terminal = "chat.cancelled"
                terminal_payload = {
                    "operationId": operation_id,
                    "historyStatus": history_status,
                }
            else:
                _safe_diagnostic(error)
                code, message, retryable = _classify_error(error)
                terminal_payload = {
                    "operationId": operation_id,
                    "error": {
                        "code": code,
                        "message": message,
                        "retryable": retryable,
                        "details": {},
                    },
                    "historyStatus": history_status,
                }

        resolved_terminal = self._finish(operation_id, terminal)
        try:
            if resolved_terminal == "chat.completed":
                if plugin_worker is not None and completed_fact is not None:
                    try:
                        getattr(plugin_worker, "emit_event")(
                            HOST_CHAT_COMPLETED_EVENT,
                            completed_fact,
                        )
                    except Exception:
                        # The terminal was atomically claimed before best-effort
                        # plugin delivery; a late cancel can no longer win.
                        pass
            finish_trace = getattr(runtime, "finish_trace_operation", None)
            if callable(finish_trace):
                try:
                    finish_trace(
                        operation_id,
                        status={
                            "chat.completed": "completed",
                            "chat.cancelled": "cancelled",
                        }.get(resolved_terminal or terminal, "failed"),
                    )
                except Exception:
                    pass
            if resolved_terminal is not None:
                if resolved_terminal == "chat.cancelled" and terminal != "chat.cancelled":
                    terminal_payload = {
                        "operationId": operation_id,
                        "historyStatus": history_status,
                    }
                self._publish(request, resolved_terminal, terminal_payload)
            return self._accepted_send_response(request, operation_id)
        finally:
            # Keep the execution registered until its terminal event has been
            # acknowledged so generation shutdown can drain detached workers.
            self._drop_execution(operation_id)

    def _accepted_send_response(
        self,
        request: Mapping[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": True, "operationId": operation_id},
        )

    def handle_cancel(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"operationId"}:
            raise ValueError("chat.cancel payload must contain only operationId")
        operation_id = payload.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("chat.cancel operationId is invalid")
        with self._lock:
            execution = self._executions.get(operation_id)
            accepted = bool(
                execution is not None
                and execution.terminal is None
                and not execution.completion_claimed
                and not execution.cancel_requested
                and not self._closed
            )
            if accepted:
                assert execution is not None
                execution.cancel_requested = True
                execution.cancel.cancel()
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": accepted, "operationId": operation_id},
        )

    def handle_screen_attach(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"resource"}:
            raise ValueError("screen.attach payload is invalid")
        from app.core_host.screen_capture import consume_screen_resource
        from app.storage.visual_observation import generate_visual_observation_id

        observation = consume_screen_resource(
            payload["resource"], generation_id=self._generation_id
        )
        attachment = _ScreenAttachment(
            attachment_id=f"screen-{secrets.token_hex(16)}",
            observations=(observation,),
            source="manual",
            visual_id=generate_visual_observation_id(),
        )
        with self._lock:
            if self._closed:
                raise LookupError("screen attachment generation is closing")
            self._pending_screen_attachment = attachment
            self._revision += 1
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={
                "attached": True,
                "attachmentId": attachment.attachment_id,
                "width": observation.width,
                "height": observation.height,
            },
        )

    def handle_screen_attach_batch(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"resources"}:
            raise ValueError("screen.attachBatch payload is invalid")
        resources = payload.get("resources")
        if not isinstance(resources, list) or not 1 <= len(resources) <= 20:
            raise ValueError("screen.attachBatch resources count is invalid")
        if any(not isinstance(resource, Mapping) for resource in resources):
            raise ValueError("screen.attachBatch resource is invalid")
        from app.core_host.screen_capture import consume_screen_resource

        observations = tuple(
            consume_screen_resource(resource, generation_id=self._generation_id)
            for resource in resources
        )
        attachment = _ScreenAttachment(
            attachment_id=f"screen-{secrets.token_hex(16)}",
            observations=observations,
            source="screen_awareness",
        )
        with self._lock:
            if self._closed:
                raise LookupError("screen attachment generation is closing")
            self._pending_screen_attachment = attachment
            self._revision += 1
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={
                "attached": True,
                "attachmentId": attachment.attachment_id,
                "count": len(observations),
            },
        )

    def handle_screen_release(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"attachmentId"}:
            raise ValueError("screen.release payload is invalid")
        attachment_id = payload.get("attachmentId")
        if (
            not isinstance(attachment_id, str)
            or re.fullmatch(r"screen-[0-9a-f]{32}", attachment_id) is None
        ):
            raise ValueError("screen.release attachmentId is invalid")
        with self._lock:
            accepted = bool(
                self._pending_screen_attachment is not None
                and self._pending_screen_attachment.attachment_id == attachment_id
            )
            if accepted:
                self._pending_screen_attachment = None
                self._revision += 1
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={"accepted": accepted, "attachmentId": attachment_id},
        )

    def snapshot_fields(
        self,
        readiness: str,
        current_character_summary: Mapping[str, Any] | None,
        *,
        base_revision: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            revision = base_revision + self._revision
            active = next(iter(self._executions.values()), None)
            interaction = (
                {
                    "operationId": active.operation_id,
                    "state": "cancelling" if active.cancel_requested else "started",
                }
                if active is not None
                else None
            )
        return {
            "generationId": self._generation_id,
            "revision": revision,
            "readiness": readiness,
            "currentCharacterSummary": (
                dict(current_character_summary) if current_character_summary is not None else None
            ),
            "activeInteractionSummary": interaction,
        }

    def cancel_all(self) -> None:
        with self._lock:
            for execution in self._executions.values():
                if execution.completion_claimed:
                    continue
                execution.cancel_requested = True
                execution.cancel.cancel()

    def close(self) -> None:
        deadline = monotonic() + CHAT_CLOSE_TIMEOUT_SECONDS
        with self._changed:
            if not self._closed:
                self._closed = True
                self._pending_screen_attachment = None
                for execution in self._executions.values():
                    if execution.completion_claimed:
                        continue
                    execution.cancel_requested = True
                    execution.cancel.cancel()
            while self._executions and monotonic() < deadline:
                self._changed.wait(timeout=max(0.0, deadline - monotonic()))
            if self._executions:
                raise RuntimeError("CHAT_CLOSE_TIMEOUT")

    def _finish(self, operation_id: str, terminal: str) -> str | None:
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None or execution.terminal is not None:
                return None
            if execution.cancel.is_cancelled() and terminal != "chat.cancelled":
                terminal = "chat.cancelled"
            execution.terminal = terminal
            return terminal

    def _commit_assistant_and_claim(
        self,
        execution: _Execution,
        timeline: TimelineStore,
        entries: Sequence[NewTimelineEntry],
    ) -> None:
        with self._changed:
            if execution.cancel_requested or execution.cancel.is_cancelled():
                execution.cancel.throw_if_cancelled()
            if len(entries) == 1:
                timeline.append(entries[0])
            else:
                timeline.append_many(entries)
            execution.completion_claimed = True

    def _commit_legacy_assistant_and_claim(
        self,
        execution: _Execution,
        history: ChatHistoryStore,
        segments: list[tuple[int, dict[str, Any]]],
        entry_id: str,
    ) -> None:
        with self._changed:
            if execution.cancel_requested or execution.cancel.is_cancelled():
                execution.cancel.throw_if_cancelled()
            for _segment_index, segment in segments:
                history.append(
                    "assistant",
                    segment["text"],
                    segment["translation"],
                    segment["tone"],
                    segment["portrait"],
                    entry_id=entry_id,
                )
            execution.completion_claimed = True

    def _drop_execution(self, operation_id: str) -> None:
        with self._changed:
            if self._executions.pop(operation_id, None) is not None:
                self._revision += 1
                self._changed.notify_all()

    def _publish(self, request: Mapping[str, Any], name: str, payload: Mapping[str, Any]) -> None:
        publisher = self._event_publisher
        if publisher is not None:
            publisher(
                event(
                    request,
                    generation_id=self._generation_id,
                    generation_credential=self._generation_credential,
                    name=name,
                    payload=payload,
                )
            )

    def _validate_send(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        # The Router deliberately does not understand generation credentials;
        # the product boundary therefore repeats the transport checks before
        # touching the published Assistant session.
        if request.get("generationId") != self._generation_id:
            raise RealChatRejection("GENERATION_MISMATCH", "chat request is stale")
        supplied_credential = request.get("generationCredential")
        if not isinstance(supplied_credential, str) or not hmac.compare_digest(
            supplied_credential,
            self._generation_credential,
        ):
            raise RealChatRejection(
                "GENERATION_CREDENTIAL_MISMATCH",
                "chat generation credential is invalid",
            )
        if request.get("kind") != "request" or request.get("name") != "chat.send":
            raise RealChatRejection("INVALID_CHAT_REQUEST", "chat request envelope is invalid")
        payload = request.get("payload")
        if (
            not isinstance(payload, Mapping)
            or not {"message", "operationId"}.issubset(payload)
            or not set(payload).issubset({"message", "operationId", "attachmentId"})
        ):
            raise RealChatRejection(
                "INVALID_CHAT_PAYLOAD",
                "chat.send payload must contain message, operationId, and optional attachmentId",
            )
        message = payload.get("message")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message.encode("utf-8")) > CHAT_MESSAGE_LIMIT
        ):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat message is invalid")
        if payload.get("operationId") != request.get("id"):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat identity is invalid")
        attachment_id = payload.get("attachmentId")
        if attachment_id is not None and (
            not isinstance(attachment_id, str)
            or re.fullmatch(r"screen-[0-9a-f]{32}", attachment_id) is None
        ):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "screen attachment identity is invalid")
        return payload


class _BoundaryFailure(RuntimeError):
    def __init__(self, code: str, public_message: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


@dataclass(frozen=True)
class _ProjectedTurn:
    turn_id: str
    messages: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class _TurnProjection:
    turns: tuple[_ProjectedTurn, ...]
    dropped: tuple[tuple[str, str], ...]
    recent_proactive: tuple[_ProjectedTurn, ...] = ()


def assemble_recent_turns(entries: list[TimelineEntry]) -> _TurnProjection:
    grouped: dict[str, list[TimelineEntry]] = {}
    for entry in sorted(entries, key=lambda item: item.seq):
        grouped.setdefault(entry.turn_id, []).append(entry)
    turns: list[_ProjectedTurn] = []
    dropped: list[tuple[str, str]] = []
    proactive_candidates: list[tuple[datetime, _ProjectedTurn]] = []
    for turn_id, turn_entries in grouped.items():
        kinds = [entry.kind.value for entry in turn_entries]
        if "human" not in kinds:
            reason = (
                "observation_only"
                if "observation" in kinds
                else "system_only"
                if kinds and set(kinds) == {"system"}
                else "incomplete"
            )
            dropped.append((turn_id, reason))
            if "assistant" in kinds and any(
                entry.origin == "proactive" for entry in turn_entries
            ):
                assistant = next(
                    (entry for entry in reversed(turn_entries) if entry.kind.value == "assistant"),
                    None,
                )
                if assistant is not None:
                    created = None
                    try:
                        text = "\n".join(
                            segment["text"]
                            for segment in assistant.payload["segments"]
                            if isinstance(segment, Mapping)
                            and isinstance(segment.get("text"), str)
                            and segment["text"].strip()
                        ).strip()
                        created = datetime.fromisoformat(
                            assistant.created_at.replace("Z", "+00:00")
                        )
                    except (KeyError, TypeError, ValueError):
                        text = ""
                    text = text[:RECENT_PROACTIVE_UTTERANCE_CHARS].rstrip()
                    if text and created is not None and created.tzinfo is not None:
                        proactive_candidates.append(
                            (
                                created,
                                _ProjectedTurn(
                                    turn_id=turn_id,
                                    messages=({"role": "assistant", "content": text},),
                                ),
                            )
                        )
            continue
        if (
            kinds.count("human") != 1
            or kinds.count("assistant") > 1
            or kinds[0] != "human"
            or ("assistant" in kinds and kinds[-1] != "assistant")
        ):
            dropped.append((turn_id, "corrupt_or_empty"))
            continue
        try:
            messages: list[dict[str, str]] = []
            for entry in turn_entries:
                if entry.kind.value == "human":
                    text = entry.payload["text"]
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("empty")
                    messages.append({"role": "user", "content": text})
                elif entry.kind.value in {"observation", "system"}:
                    text = entry.payload["text"]
                    if not isinstance(text, str):
                        raise TypeError("invalid")
                    if text.strip():
                        messages.append(
                            {"role": "system", "content": f"[Host fact] {text}"}
                        )
                elif entry.kind.value == "assistant":
                    segments = entry.payload["segments"]
                    if not isinstance(segments, list):
                        raise TypeError("invalid")
                    text = "\n".join(
                        segment["text"]
                        for segment in segments
                        if isinstance(segment, Mapping)
                        and isinstance(segment.get("text"), str)
                        and segment["text"].strip()
                    )
                    if not text:
                        raise ValueError("empty")
                    messages.append({"role": "assistant", "content": text})
                else:
                    raise TypeError("invalid")
        except (KeyError, TypeError, ValueError):
            dropped.append((turn_id, "corrupt_or_empty"))
            continue
        turns.append(
            _ProjectedTurn(
                turn_id=turn_id,
                messages=tuple(messages),
            )
        )
    cutoff = datetime.now().astimezone().timestamp() - RECENT_PROACTIVE_TTL_SECONDS
    recent_proactive = tuple(
        turn
        for created, turn in proactive_candidates
        if created.timestamp() >= cutoff
    )[-RECENT_PROACTIVE_LIMIT:]
    return _TurnProjection(tuple(turns), tuple(dropped), recent_proactive)


def _turns_from_legacy(entries: list[ChatHistoryEntry]) -> _TurnProjection:
    grouped: list[list[ChatHistoryEntry]] = []
    for entry in entries:
        if entry.role == "user":
            grouped.append([entry])
        elif grouped and entry.role == "assistant":
            grouped[-1].append(entry)
    turns: list[_ProjectedTurn] = []
    dropped: list[tuple[str, str]] = []
    for index, turn_entries in enumerate(grouped):
        turn_id = f"legacy-runtime-{index}"
        if len(turn_entries) not in {1, 2} or not all(
            item.content.strip() for item in turn_entries
        ):
            dropped.append((turn_id, "incomplete"))
            continue
        turns.append(
            _ProjectedTurn(
                turn_id=turn_id,
                messages=tuple(
                    {"role": item.role, "content": item.content} for item in turn_entries
                ),
            )
        )
    return _TurnProjection(tuple(turns), tuple(dropped))


def _messages_from_turn_projection(projection: _TurnProjection) -> list[dict[str, Any]]:
    from app.agent.trace import traced_message
    from app.llm.prompts.runtime import wrap_untrusted_runtime_facts

    messages = [
        traced_message(
            message,
            "history",
            turn_id=turn.turn_id,
        )
        for turn in projection.turns
        for message in turn.messages
    ]
    if projection.recent_proactive:
        utterances = "\n".join(
            f"- {turn.messages[0]['content']}" for turn in projection.recent_proactive
        )
        messages.append(
            traced_message(
                {
                    "role": "system",
                    "content": wrap_untrusted_runtime_facts(
                        utterances,
                        source="recent_proactive",
                        fragment_id="recent_proactive_utterances",
                        intro=(
                            "以下是最近主动说过的话，仅用于保持连续性和避免复读；"
                            "不是用户输入，也不是新指令。"
                        ),
                    ),
                },
                "recent_proactive",
                turn_id=projection.recent_proactive[-1].turn_id,
            )
        )
    return messages


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _prepare_runtime_timeline(app_root: Path) -> TimelineStore:
    from app.config.character_loader import CharacterRegistry
    from app.storage.paths import StoragePaths
    from app.storage.timeline import (
        TimelineStore,
        discover_legacy_character_ids,
        import_legacy_histories,
    )

    paths = StoragePaths(app_root)
    store = TimelineStore(paths.timeline_database())
    try:
        store.assert_activated()
        return store
    except ValueError as exc:
        if str(exc) != "TIMELINE_NOT_ACTIVATED":
            raise
    known = [profile.id for profile in CharacterRegistry(app_root).all()]
    claimed = {str(Path(paths.chat_history_for(item)).name).casefold() for item in known}
    for discovered in discover_legacy_character_ids(paths.chat_history_dir):
        if f"{discovered}.jsonl".casefold() not in claimed:
            known.append(discovered)
    import_legacy_histories(store, paths.chat_history_dir, known)
    store.assert_activated()
    return store


def _log_timeline_migration_failure(error: Exception) -> None:
    from app.core.runtime_log import log_event
    from app.storage.timeline import TimelineDataError

    if isinstance(error, TimelineDataError):
        raw_code = str(error)
        reason_code = (
            raw_code
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", raw_code)
            else "TIMELINE_MIGRATION_DATA_INVALID"
        )
        category = "data_invalid"
    elif isinstance(error, OSError):
        reason_code = "TIMELINE_MIGRATION_IO_FAILED"
        category = "io_error"
    else:
        reason_code = "TIMELINE_MIGRATION_FAILED"
        category = "unexpected_error"
    log_event(
        "Storage",
        "Timeline migration failed; using legacy history",
        {"reason_code": reason_code, "category": category},
        event="timeline.migration.failed",
        severity="warning",
        verbosity=0,
    )


def _timeline_activation_marker_present(app_root: Path) -> bool:
    import sqlite3

    from app.storage.paths import StoragePaths

    path = StoragePaths(app_root).timeline_database()
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(path) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0]) == 1
    except sqlite3.DatabaseError:
        # An unreadable pre-existing Timeline must not silently fork new chat
        # writes back into preserved legacy JSONL.
        return True


def _project_reply(reply: object) -> list[dict[str, object]]:
    raw_segments = getattr(reply, "segments", None)
    if not isinstance(raw_segments, list):
        raise _BoundaryFailure("INVALID_CHAT_REPLY", "Assistant reply was invalid", False)
    projected: list[dict[str, object]] = []
    for segment in raw_segments:
        values = (
            getattr(segment, "text", None),
            getattr(segment, "translation", None),
            getattr(segment, "tone", None),
            getattr(segment, "portrait", None),
            getattr(segment, "suppress_tts", None),
        )
        if not all(isinstance(value, str) for value in values[:4]) or not isinstance(values[4], bool):
            raise _BoundaryFailure("INVALID_CHAT_REPLY", "Assistant reply was invalid", False)
        projected.append(
            {
                "text": values[0],
                "translation": values[1],
                "tone": values[2],
                "portrait": values[3],
                "suppressTts": values[4],
            }
        )
    return projected


def _classify_error(error: BaseException) -> tuple[str, str, bool]:
    if isinstance(error, _BoundaryFailure):
        return error.code, error.public_message, error.retryable
    from app.llm.prompts.runtime import ContextWindowExceededError

    if isinstance(error, ContextWindowExceededError):
        return (
            "CONTEXT_WINDOW_EXCEEDED",
            "Current request exceeds the configured model context window",
            False,
        )
    from app.llm.api_client import ApiConfigError, ApiRequestError

    if isinstance(error, ApiConfigError):
        return "PROVIDER_CONFIGURATION_INVALID", "Provider configuration is invalid", False
    if isinstance(error, ApiRequestError):
        text = str(error).lower()
        cause: BaseException | None = error
        while cause is not None:
            if isinstance(cause, urllib.error.HTTPError):
                retryable = cause.code == 429 or cause.code >= 500
                return (
                    "PROVIDER_REQUEST_FAILED",
                    _public_provider_http_message(error, cause.code),
                    retryable,
                )
            cause = cause.__cause__
        response_invalid = any(
            marker in text
            for marker in (
                "格式无法解析",
                "invalid json",
                "remained invalid",
                "missing",
                "empty response",
            )
        )
        if response_invalid:
            message = (
                "供应商响应格式无效：返回内容不是有效 JSON。"
                if "格式无法解析" in text or "invalid json" in text
                else "供应商响应格式无效：回复结构不符合协议。"
            )
            return "PROVIDER_RESPONSE_INVALID", message, False
        return "PROVIDER_REQUEST_FAILED", "Provider request failed", True
    return "CHAT_EXECUTION_FAILED", "Chat execution failed", False


_PROVIDER_PUBLIC_FIELDS = ("message", "code", "type", "status")
_PROVIDER_DIAGNOSTIC_LIMIT = 360
_PROVIDER_SENSITIVE_PATTERNS = (
    re.compile(r"\bPRIVATE_[A-Z0-9_]+\b"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|authorization|bearer|token|secret|password|credential)\b"
        r"\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
    re.compile(r"https?://[^\s\])}>,;]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z]:\\[^\s\])}>,;]+"),
    re.compile(r"(?<![\w:])/(?:[^/\s]+/)+[^/\s\])}>,;]+"),
)


def _public_provider_http_message(error: BaseException, status_code: int) -> str:
    payload = _provider_error_payload(str(error), status_code)
    if payload is None:
        return f"API HTTP {status_code}: 供应商请求失败。"

    raw_error = payload.get("error")
    public_source = raw_error if isinstance(raw_error, Mapping) else payload
    public_values: dict[str, str] = {}
    for field in _PROVIDER_PUBLIC_FIELDS:
        value = public_source.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        sanitized = _sanitize_provider_diagnostic(str(value))
        if sanitized:
            public_values[field] = sanitized

    message = public_values.pop("message", "")
    metadata = "; ".join(
        f"{field}: {public_values[field]}"
        for field in _PROVIDER_PUBLIC_FIELDS[1:]
        if field in public_values
    )
    if message and metadata:
        return f"API HTTP {status_code}: {message} ({metadata})"
    if message:
        return f"API HTTP {status_code}: {message}"
    if metadata:
        return f"API HTTP {status_code}: {metadata}"
    return f"API HTTP {status_code}: 供应商请求失败。"


def _provider_error_payload(error_text: str, status_code: int) -> Mapping[str, Any] | None:
    raw_marker = "\n原始响应："
    if raw_marker in error_text:
        candidate = error_text.rsplit(raw_marker, 1)[1].strip()
    else:
        prefix = f"API HTTP {status_code}:"
        candidate = error_text.split(prefix, 1)[1].strip() if prefix in error_text else ""
    if not candidate.startswith("{"):
        return None
    try:
        decoded = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _sanitize_provider_diagnostic(value: str) -> str:
    sanitized = " ".join(value.split())
    for pattern in _PROVIDER_SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    if len(sanitized) > _PROVIDER_DIAGNOSTIC_LIMIT:
        sanitized = sanitized[: _PROVIDER_DIAGNOSTIC_LIMIT - 1].rstrip() + "…"
    return sanitized


def _safe_diagnostic(error: BaseException) -> None:
    try:
        print(f"Real chat failed: {type(error).__name__}", file=sys.stderr)
    except Exception:
        pass


def _is_operation_cancelled(error: BaseException) -> bool:
    from app.core.cancellation import OperationCancelled

    return isinstance(error, OperationCancelled)


__all__ = [
    "HOST_CHAT_COMPLETED_EVENT",
    "REAL_CHAT_EXECUTION_LIMIT",
    "RealChatBoundary",
    "RealChatRejection",
    "assemble_recent_turns",
]
