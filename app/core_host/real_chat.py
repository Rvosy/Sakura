"""Generation-scoped headless real chat boundary for Runtime v2."""

from __future__ import annotations

import hmac
import re
import secrets
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from app.plugin_sdk.sakura_provider_errors import provider_http_status, public_provider_http_message

from .protocol import event, response

if TYPE_CHECKING:
    from app.plugin_sdk.sakura_cancellation import CancellationToken
    from app.storage.timeline import NewTimelineEntry, TimelineEntry, TimelineStore


REAL_CHAT_EXECUTION_LIMIT = 1
CHAT_CLOSE_TIMEOUT_SECONDS = 3.0
MANUAL_SCREEN_ATTACHMENT_LIMIT = 6
HOST_CHAT_COMPLETED_EVENT = "sakura.host.chat.completed"


class RealChatRejection(ValueError):
    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _new_cancellation_token() -> CancellationToken:
    from app.plugin_sdk.sakura_cancellation import CancellationToken

    return CancellationToken()


@dataclass(frozen=True)
class ChatTurnInput:
    """Decoded input shared by desktop, Mobile and host-initiated turns."""

    operation_id: str
    message: str = ""
    event: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ChatOutcome:
    terminal: str
    payload: Mapping[str, Any]


@dataclass
class _Execution:
    turn: ChatTurnInput
    session: object = field(repr=False)
    cancel: CancellationToken = field(default_factory=_new_cancellation_token)
    started: bool = False
    cancel_requested: bool = False
    terminal: str | None = None
    completion_claimed: bool = False
    screen_attachment: _ScreenAttachment | None = None

    @property
    def operation_id(self) -> str:
        return self.turn.operation_id


@dataclass(frozen=True)
class _ScreenAttachment:
    attachment_id: str
    observations: tuple[Any, ...]
    item_ids: tuple[str, ...]
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
            try:
                self._timeline = _prepare_runtime_timeline(self._app_root)
            except Exception as exc:
                self._timeline = None
                self._timeline_error = exc
        self._segment_authorizer = segment_authorizer
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._executions: dict[str, _Execution] = {}
        self._pending_screen_attachment: _ScreenAttachment | None = None
        self._screen_session_id = secrets.token_hex(16)
        self._revision = 0
        self._closed = False
        self._switching_character = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if self._event_publisher is not None:
                raise RuntimeError("chat event publisher is already configured")
            self._event_publisher = publisher

    def reserve_send(self, request: Mapping[str, Any]) -> None:
        payload = self._validate_send(request)
        self._reserve_turn(
            ChatTurnInput(str(request["id"]), payload.get("message", ""), payload.get("event")),
            attachment_id=payload.get("attachmentId"),
        )

    def _reserve_turn(
        self,
        turn: ChatTurnInput,
        *,
        attachment_id: str | None = None,
        screen_attachment: _ScreenAttachment | None = None,
        expected_character_id: str | None = None,
    ) -> None:
        operation_id = turn.operation_id
        with self._changed:
            if self._switching_character:
                raise RealChatRejection("CHARACTER_SWITCH_IN_PROGRESS", "角色正在切换", retryable=True)
            if self._closed:
                raise RealChatRejection("GENERATION_INVALIDATED", "chat generation is closing")
            session = self._session_provider()
            if session is None:
                raise RealChatRejection("ASSISTANT_NOT_READY", "Assistant is not ready")
            if expected_character_id is not None and str(session.character.id) != expected_character_id:
                raise RealChatRejection("MOBILE_CHARACTER_NOT_CURRENT", "角色已切换，请重新选择角色。")
            if operation_id in self._executions:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            if len(self._executions) >= REAL_CHAT_EXECUTION_LIMIT:
                raise RealChatRejection(
                    "CHAT_EXECUTION_LIMIT_EXCEEDED",
                    "another chat interaction is active",
                    retryable=True,
                )
            if attachment_id is not None:
                pending = self._pending_screen_attachment
                if pending is None or pending.attachment_id != attachment_id:
                    raise RealChatRejection(
                        "SCREEN_ATTACHMENT_NOT_FOUND",
                        "screen attachment is stale or unavailable",
                    )
                screen_attachment = pending
            is_screen_event = turn.event is not None and turn.event.get("type") == "screen_observation"
            if is_screen_event != (screen_attachment is not None and screen_attachment.source == "screen_awareness"):
                raise RealChatRejection("INVALID_CHAT_PAYLOAD", "scheduled screen input requires its explicit event and attachment")
            if attachment_id is not None:
                self._pending_screen_attachment = None
            self._executions[operation_id] = _Execution(
                turn,
                session=session,
                screen_attachment=screen_attachment,
            )
            self._revision += 1
            self._changed.notify_all()

    def apply_runtime_update(self, update: Callable[[], None]) -> None:
        """Apply at the settings operation; a later chat never repairs a save."""

        with self._changed:
            if self._closed:
                raise RealChatRejection(
                    "GENERATION_INVALIDATED", "chat generation is closing"
                )
            if self._executions or self._switching_character:
                raise RealChatRejection("RUNTIME_UPDATE_BUSY", "当前对话尚未结束，设置尚未应用。", retryable=True)
            update()

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

        operation_id = str(request["id"])
        started = threading.Event()
        kickoff_errors: list[BaseException] = []

        def run() -> None:
            try:
                from app.core.interaction import interaction_context

                with interaction_context(operation_id):
                    self.handle_send(request, _on_started=started.set)
            except BaseException as error:  # noqa: BLE001 - owned generation worker
                try:
                    if not started.is_set():
                        kickoff_errors.append(error)
                    else:
                        code, _, _ = _classify_error(error)
                        _safe_diagnostic(error, code=code, stage="worker", operation_id=operation_id)
                finally:
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
        """IPC adapter for an input already accepted by reserve_send."""

        operation_id = str(request["id"])
        self._run_turn(
            operation_id,
            emit=lambda name, value: self._publish(request, name, value),
            on_started=_on_started,
        )
        return self._accepted_send_response(request, operation_id)

    def run_turn(
        self, turn: ChatTurnInput, *, screen_attachment: _ScreenAttachment | None = None,
    ) -> ChatOutcome:
        """Run the same business operation without an IPC envelope or GUI."""

        from app.core.interaction import interaction_context

        self._reserve_turn(turn, screen_attachment=screen_attachment)
        with interaction_context(turn.operation_id):
            return self._run_turn(turn.operation_id)

    def _run_turn(
        self,
        operation_id: str,
        *,
        emit: Callable[[str, Mapping[str, Any]], None] | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> ChatOutcome:
        started_at = monotonic()
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is None:
                raise RealChatRejection("CHAT_RESERVATION_MISSING", "chat request was not reserved")
            if execution.started:
                raise RealChatRejection("DUPLICATE_CHAT_IDENTITY", "chat identity is already in use")
            execution.started = True
            screen_attachment = execution.screen_attachment
            self._changed.notify_all()

        if emit is not None:
            try:
                emit("chat.started", {"operationId": operation_id})
            except BaseException:  # noqa: BLE001 - transport owner will terminate the generation
                self._drop_execution(operation_id)
                raise
        if on_started is not None:
            on_started()
        history_status = "saved"
        assistant_committed = False
        terminal = "chat.failed"
        terminal_payload: dict[str, Any]
        assistant = None
        assistant_invoked = False
        completed_fact: dict[str, Any] | None = None
        plugin_application: object | None = None
        stage = "prepare"
        try:
            from app.storage.timeline import NewTimelineEntry, TimelineKind

            execution.cancel.throw_if_cancelled()
            session = execution.session
            character = getattr(session, "character")
            assistant = session.assistant
            visual_binding = session.visual_binding
            session_descriptor = session.descriptor()
            stage = "timeline_read"
            timeline = self._timeline
            if timeline is None:
                history_status = "degraded"
                raise _BoundaryFailure(
                    "TIMELINE_DATABASE_INVALID",
                    "Chat history database is unavailable",
                    False,
                ) from self._timeline_error
            stage = "input_prepare"
            proactive_event = execution.turn.event
            is_update_event = proactive_event is not None and proactive_event.get("type") == "update_available"
            is_screen_event = proactive_event is not None and proactive_event.get("type") == "screen_observation"
            message = execution.turn.message
            plugin_application = (
                self._plugin_application_provider()
                if self._plugin_application_provider is not None
                else None
            )
            if plugin_application is not None and not is_update_event and not is_screen_event:
                try:
                    getattr(plugin_application, "emit_event")(
                        "message.user",
                        {"role": "user", "characters": len(message)},
                    )
                except Exception:
                    pass
            input_entries: list[NewTimelineEntry] = []
            turn_id = uuid.uuid4().hex
            created_at = _now_iso()
            history_now = datetime.now().astimezone()
            stage = "timeline_read"
            try:
                history_cursor = timeline.latest_cursor(str(character.id))
            except Exception as error:
                raise _BoundaryFailure("TIMELINE_READ_FAILED", "Chat history could not be read", False) from error
            stage = "input_prepare"
            if not is_update_event:
                if screen_attachment is None or screen_attachment.source != "screen_awareness":
                    input_entries.append(NewTimelineEntry(entry_id=uuid.uuid4().hex, turn_id=turn_id,
                        character_id=str(character.id), kind=TimelineKind.HUMAN, origin="chat", created_at=created_at,
                        payload={"text": message}))
                if screen_attachment is not None:
                    visual = {"imageCount": len(screen_attachment.observations),
                              "capturedAt": screen_attachment.observations[0].captured_at}
                    if screen_attachment.visual_id is not None:
                        visual["visualId"] = screen_attachment.visual_id
                    scheduled = screen_attachment.source == "screen_awareness"
                    input_entries.append(NewTimelineEntry(entry_id=uuid.uuid4().hex, turn_id=turn_id,
                        character_id=str(character.id), kind=TimelineKind.OBSERVATION,
                        origin="scheduled_screen" if scheduled else "manual_screen", created_at=created_at,
                        payload={"text": "刚才留意了一下屏幕状态。" if scheduled else f"你分享了 {len(screen_attachment.observations)} 张屏幕截图。", "visual": visual}))
                stage = "timeline_write"
                try:
                    execution.cancel.throw_if_cancelled()
                    timeline.append_many(input_entries)
                except Exception as error:
                    history_status = "degraded"
                    raise _BoundaryFailure("TIMELINE_WRITE_FAILED", "Chat input could not be saved", False) from error
            execution.cancel.throw_if_cancelled()
            stage = "assistant"
            assistant_invoked = True
            result = assistant.run_turn({"operationId": operation_id, "session": session_descriptor,
                "turnId": turn_id, "message": message, "event": dict(proactive_event) if proactive_event else None,
                "historyCursor": history_cursor, "historyNow": history_now.isoformat(),
                "attachment": asdict(screen_attachment) if screen_attachment is not None else None,
                "entryIds": [entry.entry_id for entry in input_entries],
                "humanEntryId": next((entry.entry_id for entry in input_entries if entry.kind is TimelineKind.HUMAN), ""),
                "observationEntryIds": [entry.entry_id for entry in input_entries if entry.kind is TimelineKind.OBSERVATION]},
                cancel_checker=execution.cancel.throw_if_cancelled)
            stage = "reply_processing"
            from app.core_host.assistant_adapter import AssistantFailure, apply_visual_reply
            result.reply = apply_visual_reply(result.reply, visual_binding)
            execution.cancel.throw_if_cancelled()
            allowed_action_types = {"tool_call", "event"} if is_update_event else {"tool_call"}
            if any(
                getattr(action, "type", "") not in allowed_action_types
                for action in getattr(result, "actions", [])
            ):
                raise _BoundaryFailure(
                    "UNEXPECTED_CHAT_ACTION", "Assistant 返回了不支持的动作。", False,
                )
            if plugin_application is not None:
                try:
                    reply_text = str(getattr(getattr(result, "reply", None), "speech", ""))
                    getattr(plugin_application, "emit_event")(
                        "message.ai",
                        {"role": "assistant", "characters": len(reply_text)},
                    )
                except Exception:
                    pass
            execution.cancel.throw_if_cancelled()
            segments = _project_reply(getattr(result, "reply", None))
            semantic_observation_entry = None
            if (
                screen_attachment is not None
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
            stage = "segment_authorization"
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
            stage = "reply_processing"
            if is_update_event and not authorized_segments:
                raise _BoundaryFailure(
                    "UPDATE_ANNOUNCEMENT_EMPTY",
                    "Update announcement was empty",
                    True,
                )
            if authorized_segments:
                execution.cancel.throw_if_cancelled()
                stage = "timeline_write"
                try:
                    assistant_entry = NewTimelineEntry(
                        entry_id=assistant_entry_id,
                        turn_id=turn_id,
                        character_id=str(character.id),
                        kind=TimelineKind.ASSISTANT,
                        origin=(
                            "proactive"
                            if is_update_event
                            or (
                                screen_attachment is not None
                                and screen_attachment.source == "screen_awareness"
                            )
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
                except AssistantFailure:
                    raise
                except Exception as exc:
                    if _is_operation_cancelled(exc):
                        raise
                    history_status = "degraded"
                    raise _BoundaryFailure(
                        "TIMELINE_WRITE_FAILED", "Assistant reply could not be saved", False
                    ) from exc
            else:
                self._commit_assistant_and_claim(execution, timeline, [])
            terminal = "chat.completed"
            terminal_payload = {
                "operationId": operation_id,
                "reply": {"segments": segments},
                "historyStatus": history_status,
            }
            if assistant_committed:
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
        except BaseException as error:  # noqa: BLE001 - sanitize at the process boundary
            if _is_operation_cancelled(error):
                terminal = "chat.cancelled"
                terminal_payload = {
                    "operationId": operation_id,
                    "historyStatus": history_status,
                }
            else:
                code, message, retryable = _classify_error(error)
                _safe_diagnostic(error, code=code, stage=stage, operation_id=operation_id)
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
        if resolved_terminal is not None:
            from app.core.runtime_log import log_event
            finished_attributes: dict[str, Any] = {
                "operation_id": operation_id,
                "outcome": {"chat.completed": "success", "chat.cancelled": "cancelled"}.get(resolved_terminal, "failed"),
                "elapsed_ms": int((monotonic() - started_at) * 1000),
            }
            if resolved_terminal == "chat.failed":
                finished_attributes.update(
                    reason_code=terminal_payload["error"]["code"],
                    stage=stage,
                )
            log_event("Chat", "对话已结束", finished_attributes, event="chat.finished", severity="info")
        try:
            if resolved_terminal == "chat.completed":
                if plugin_application is not None and completed_fact is not None:
                    try:
                        getattr(plugin_application, "emit_event")(
                            HOST_CHAT_COMPLETED_EVENT,
                            completed_fact,
                        )
                    except Exception:
                        # The terminal was atomically claimed before best-effort
                        # plugin delivery; a late cancel can no longer win.
                        pass
            finish_trace = getattr(assistant, "release", None)
            if assistant_invoked and callable(finish_trace):
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
        except BaseException:
            self._drop_execution(operation_id)
            raise

        # A new send may arrive as soon as the terminal reaches the Shell. Keep
        # publication and release atomic without locking the domain callbacks.
        with self._changed:
            try:
                if resolved_terminal is not None:
                    if resolved_terminal == "chat.cancelled" and terminal != "chat.cancelled":
                        terminal_payload = {
                            "operationId": operation_id,
                            "historyStatus": history_status,
                        }
                    if emit is not None:
                        emit(resolved_terminal, terminal_payload)
                return ChatOutcome(resolved_terminal or terminal, terminal_payload)
            finally:
                self._drop_execution(operation_id)

    def reserve_host_message(
        self,
        message: str,
        image_data_url: str = "",
        *,
        operation_id: str | None = None,
        expected_character_id: str | None = None,
    ) -> str:
        """Accept the host turn before returning its asynchronous job identity."""

        clean_message = str(message).strip()
        clean_image = str(image_data_url).strip()
        if not clean_message and not clean_image:
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat message is empty")
        if clean_image and not clean_image.startswith("data:image/"):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat image is invalid")
        operation_id = operation_id or f"mobile-{uuid.uuid4().hex}"
        if re.fullmatch(r"mobile-[0-9a-f]{32}", operation_id) is None:
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat operation is invalid")
        attachment = None
        if clean_image:
            from app.plugin_sdk.sakura_assistant_contract import ScreenObservation
            from app.storage.visual_observation import generate_visual_observation_id

            attachment = _ScreenAttachment(
                attachment_id=f"screen-{secrets.token_hex(16)}",
                observations=(
                    ScreenObservation(
                        data_url=clean_image,
                        width=0,
                        height=0,
                        captured_at=_now_iso(),
                        screen_name="mobile",
                    ),
                ),
                item_ids=(f"shot-{secrets.token_hex(16)}",),
                source="manual",
                visual_id=generate_visual_observation_id(),
            )
        self._reserve_turn(
            ChatTurnInput(operation_id, clean_message or "请看这张图片。"),
            screen_attachment=attachment,
            expected_character_id=expected_character_id,
        )
        return operation_id

    def run_reserved_host_message(self, operation_id: str) -> dict[str, Any]:
        from app.core.interaction import interaction_context

        with interaction_context(operation_id):
            outcome = self._run_turn(operation_id)
        result = outcome.payload
        if outcome.terminal != "chat.completed":
            error = result.get("error")
            code = ("OPERATION_CANCELLED" if outcome.terminal == "chat.cancelled" else
                    str(error.get("code")) if isinstance(error, Mapping) else "CHAT_FAILED")
            message_text = (
                str(error.get("message"))
                if isinstance(error, Mapping)
                else "chat failed"
            )
            raise RealChatRejection(code, message_text)
        reply = result.get("reply")
        raw_segments = reply.get("segments") if isinstance(reply, Mapping) else None
        if not isinstance(raw_segments, list):
            raise RealChatRejection("INVALID_CHAT_REPLY", "chat reply was invalid")
        segments = [
            {
                "content": str(segment.get("translation") or segment.get("text") or ""),
                "raw_content": str(segment.get("text") or ""),
                "translation": str(segment.get("translation") or ""),
                "tone": str(segment.get("tone") or ""),
                "portrait": str(segment.get("portrait") or ""),
            }
            for segment in raw_segments
            if isinstance(segment, Mapping)
        ]
        return {
            "reply": "\n".join(item["content"] for item in segments),
            "reply_raw": "\n".join(item["raw_content"] for item in segments),
            "segments": segments,
            "actions": [],
        }

    def run_host_message(
        self, message: str, image_data_url: str = "", *,
        operation_id: str | None = None, expected_character_id: str | None = None,
    ) -> dict[str, Any]:
        operation_id = self.reserve_host_message(
            message, image_data_url, operation_id=operation_id,
            expected_character_id=expected_character_id,
        )
        return self.run_reserved_host_message(operation_id)

    def abandon_host_message(self, operation_id: str) -> None:
        """Release a reservation when its host worker could not be started."""
        with self._changed:
            execution = self._executions.get(operation_id)
            if execution is not None and not execution.started:
                self._drop_execution(operation_id)

    def cancel_host_message(self, operation_id: str) -> bool:
        """Cancel one Core-owned Host lane operation without constructing transport DTOs."""

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
        return accepted

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

    def handle_screen_session(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("payload") != {}:
            raise ValueError("screen.session payload is invalid")
        with self._lock:
            self._check_screen_session(self._screen_session_id)
            session_id = self._screen_session_id
        return response(request, generation_id=self._generation_id,
                        generation_credential=self._generation_credential,
                        protocol_minor=2, payload={"sessionId": session_id})

    def _check_screen_session(self, session_id: object) -> None:
        # Called under the chat lock both before reading a resource and before
        # publishing it. The token changes even when A is selected again.
        if self._closed or self._switching_character or session_id != self._screen_session_id:
            raise LookupError("SCREEN_SESSION_STALE")

    def handle_screen_attach(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"resource", "sessionId"}:
            raise ValueError("screen.attach payload is invalid")
        from app.core_host.screen_capture import consume_screen_resource
        from app.storage.visual_observation import generate_visual_observation_id

        with self._lock:
            self._check_screen_session(payload["sessionId"])
        observation = consume_screen_resource(
            payload["resource"], generation_id=self._generation_id
        )
        item_id = f"shot-{secrets.token_hex(16)}"
        with self._lock:
            self._check_screen_session(payload["sessionId"])
            pending = self._pending_screen_attachment
            if pending is None:
                attachment = _ScreenAttachment(
                    attachment_id=f"screen-{secrets.token_hex(16)}",
                    observations=(observation,),
                    item_ids=(item_id,),
                    source="manual",
                    visual_id=generate_visual_observation_id(),
                )
            else:
                if pending.source != "manual":
                    raise LookupError("another screen attachment is pending")
                if len(pending.observations) >= MANUAL_SCREEN_ATTACHMENT_LIMIT:
                    raise LookupError("manual screen attachment limit exceeded")
                attachment = replace(
                    pending,
                    observations=(*pending.observations, observation),
                    item_ids=(*pending.item_ids, item_id),
                )
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
                "itemId": item_id,
                "width": observation.width,
                "height": observation.height,
                "count": len(attachment.observations),
            },
        )

    def handle_screen_attach_batch(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"resources", "sessionId"}:
            raise ValueError("screen.attachBatch payload is invalid")
        resources = payload.get("resources")
        if not isinstance(resources, list) or not 1 <= len(resources) <= 20:
            raise ValueError("screen.attachBatch resources count is invalid")
        if any(not isinstance(resource, Mapping) for resource in resources):
            raise ValueError("screen.attachBatch resource is invalid")
        from app.core_host.screen_capture import consume_screen_resource

        with self._lock:
            self._check_screen_session(payload["sessionId"])
        observations = tuple(
            consume_screen_resource(resource, generation_id=self._generation_id)
            for resource in resources
        )
        attachment = _ScreenAttachment(
            attachment_id=f"screen-{secrets.token_hex(16)}",
            observations=observations,
            item_ids=(),
            source="screen_awareness",
        )
        with self._lock:
            self._check_screen_session(payload["sessionId"])
            if self._pending_screen_attachment is not None:
                raise LookupError("another screen attachment is pending")
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

    def handle_screen_remove(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"attachmentId", "itemId"}:
            raise ValueError("screen.remove payload is invalid")
        attachment_id = payload.get("attachmentId")
        item_id = payload.get("itemId")
        if (
            not isinstance(attachment_id, str)
            or re.fullmatch(r"screen-[0-9a-f]{32}", attachment_id) is None
            or not isinstance(item_id, str)
            or re.fullmatch(r"shot-[0-9a-f]{32}", item_id) is None
        ):
            raise ValueError("screen.remove identity is invalid")
        with self._lock:
            pending = self._pending_screen_attachment
            accepted = bool(
                pending is not None
                and pending.source == "manual"
                and pending.attachment_id == attachment_id
                and item_id in pending.item_ids
            )
            count = 0
            if accepted:
                assert pending is not None
                index = pending.item_ids.index(item_id)
                observations = pending.observations[:index] + pending.observations[index + 1 :]
                item_ids = pending.item_ids[:index] + pending.item_ids[index + 1 :]
                count = len(observations)
                self._pending_screen_attachment = (
                    replace(pending, observations=observations, item_ids=item_ids)
                    if observations
                    else None
                )
                self._revision += 1
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload={
                "accepted": accepted,
                "attachmentId": attachment_id,
                "itemId": item_id,
                "count": count,
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

    @contextmanager
    def suspend_for_character_change(self):
        deadline = monotonic() + CHAT_CLOSE_TIMEOUT_SECONDS
        with self._changed:
            self._switching_character = True
            self._screen_session_id = secrets.token_hex(16)
            self._pending_screen_attachment = None
            self.cancel_all()
            try:
                while self._executions and monotonic() < deadline:
                    self._changed.wait(timeout=max(0.0, deadline - monotonic()))
                if self._executions:
                    raise RuntimeError("CHARACTER_SWITCH_CHAT_BUSY")
            except BaseException:
                self._switching_character = False
                raise
        try:
            yield
        finally:
            with self._changed:
                self._switching_character = False

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
        def commit() -> None:
            if len(entries) == 1:
                timeline.append(entries[0])
            elif entries:
                timeline.append_many(entries)
            execution.completion_claimed = True

        # Match settings application: chat admission lock precedes the service
        # binding lock. Invalidation cannot slip between validation and append.
        with self._changed:
            if execution.cancel_requested or execution.cancel.is_cancelled():
                execution.cancel.throw_if_cancelled()
            execution.session.assistant.commit_result(commit)

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
        if not isinstance(payload, Mapping):
            raise RealChatRejection(
                "INVALID_CHAT_PAYLOAD",
                "chat.send payload must be an object",
            )
        if payload.get("operationId") != request.get("id"):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat identity is invalid")
        if set(payload) == {"operationId", "event"}:
            self._validate_update_event(payload.get("event"))
            return payload
        if set(payload) == {"operationId", "event", "attachmentId"} and payload["event"] == {"type": "screen_observation"}:
            attachment_id = payload["attachmentId"]
            if not isinstance(attachment_id, str) or re.fullmatch(r"screen-[0-9a-f]{32}", attachment_id) is None:
                raise RealChatRejection("INVALID_CHAT_PAYLOAD", "screen attachment identity is invalid")
            return payload
        if not {"message", "operationId"}.issubset(payload) or not set(payload).issubset(
            {"message", "operationId", "attachmentId"}
        ):
            raise RealChatRejection(
                "INVALID_CHAT_PAYLOAD",
                "chat.send payload shape is invalid",
            )
        message = payload.get("message")
        if (
            not isinstance(message, str)
            or not message.strip()
        ):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "chat message is invalid")
        attachment_id = payload.get("attachmentId")
        if attachment_id is not None and (
            not isinstance(attachment_id, str)
            or re.fullmatch(r"screen-[0-9a-f]{32}", attachment_id) is None
        ):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "screen attachment identity is invalid")
        return payload

    @staticmethod
    def _validate_update_event(value: Any) -> None:
        if not isinstance(value, Mapping) or set(value) != {"type", "payload"}:
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "update event is invalid")
        if value.get("type") != "update_available":
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "update event type is invalid")
        payload = value.get("payload")
        expected = {"currentVersion", "version", "notes", "pubDate", "mode"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "update event payload is invalid")
        version_pattern = re.compile(
            r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
        )
        for key in ("currentVersion", "version"):
            version = payload.get(key)
            if (
                not isinstance(version, str)
                or len(version) > 64
                or not version_pattern.fullmatch(version)
            ):
                raise RealChatRejection("INVALID_CHAT_PAYLOAD", "update version is invalid")
        if payload.get("mode") not in {"installed", "portable"}:
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "update mode is invalid")
        for key, limit in (("notes", 4000), ("pubDate", 64)):
            text = payload.get(key)
            if text is not None and (
                not isinstance(text, str)
                or len(text) > limit
                or any(character in text for character in ("\x00", "\r"))
            ):
                raise RealChatRejection("INVALID_CHAT_PAYLOAD", f"update {key} is invalid")
        pub_date = payload.get("pubDate")
        if isinstance(pub_date, str):
            try:
                parsed_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RealChatRejection(
                    "INVALID_CHAT_PAYLOAD", "update pubDate is invalid"
                ) from exc
            if parsed_date.tzinfo is None:
                raise RealChatRejection(
                    "INVALID_CHAT_PAYLOAD", "update pubDate is invalid"
                )


class _BoundaryFailure(RuntimeError):
    def __init__(self, code: str, public_message: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _prepare_runtime_timeline(app_root: Path) -> TimelineStore:
    from app.storage.paths import StoragePaths
    from app.storage.timeline import TimelineStore

    paths = StoragePaths(app_root)
    store = TimelineStore(paths.timeline_database())
    store.initialize()
    return store


def _project_reply(reply: object) -> list[dict[str, object]]:
    from app.core.runtime_log import log_event
    from app.plugin_sdk.sakura_visual_control import validate_visual_control
    raw_segments = getattr(reply, "segments", None)
    if not isinstance(raw_segments, list):
        raise _BoundaryFailure("INVALID_CHAT_REPLY", "Assistant reply was invalid", False)
    projected: list[dict[str, object]] = []
    for segment_index, segment in enumerate(raw_segments):
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
        control = getattr(segment, "control", None)
        if control is not None:
            try:
                projected[-1]["control"] = validate_visual_control(control)
            except ValueError:
                log_event("Visual", "表现控制无效，保留文字回复", {
                    "reason_code": "VISUAL_CONTROL_INVALID", "stage": "visual.reply.project",
                    "segment_index": segment_index,
                }, event="visual.control.failed", severity="warning")
    # Optional controls must not make a valid text reply exceed Timeline's
    # record limit. Prefer dropping visual data to losing the completed turn.
    import json
    from app.storage.timeline import MAX_PAYLOAD_BYTES
    if len(json.dumps({"segments": projected}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        removed = sum("control" in segment for segment in projected)
        for segment in projected:
            segment.pop("control", None)
        if removed:
            log_event("Visual", "表现控制超过回复大小限制，保留文字回复", {
                "reason_code": "VISUAL_CONTROL_TOO_LARGE", "stage": "visual.reply.project",
                "segment_count": removed, "limit_bytes": MAX_PAYLOAD_BYTES,
            }, event="visual.control.failed", severity="warning")
    return projected


def _classify_error(error: BaseException) -> tuple[str, str, bool]:
    if isinstance(error, _BoundaryFailure):
        return error.code, error.public_message, error.retryable
    from app.core_host.assistant_adapter import AssistantFailure
    if isinstance(error, AssistantFailure):
        return error.code, error.public_message, error.retryable
    from app.plugin_sdk.sakura_model import ApiConfigError, ApiRequestError

    if isinstance(error, ApiConfigError):
        return "PROVIDER_CONFIGURATION_INVALID", "Provider configuration is invalid", False
    if isinstance(error, ApiRequestError):
        text = str(error).lower()
        status = provider_http_status(error)
        if status is not None:
            retryable = status == 429 or status >= 500
            return (
                "PROVIDER_REQUEST_FAILED",
                public_provider_http_message(error, status),
                retryable,
            )
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
                "模型服务响应格式无效：返回内容不是有效 JSON。"
                if "格式无法解析" in text or "invalid json" in text
                else "模型服务响应格式无效：回复结构不符合协议。"
            )
            return "PROVIDER_RESPONSE_INVALID", message, False
        return "PROVIDER_REQUEST_FAILED", "Provider request failed", True
    return "CHAT_EXECUTION_FAILED", "Chat execution failed", False


def _safe_diagnostic(error: BaseException, *, code: str, stage: str, operation_id: str) -> None:
    try:
        from app.core.runtime_log import external_runtime_sink_active, log_event, diagnostic_attributes

        if external_runtime_sink_active():
            attributes: dict[str, Any] = {
                "operation_id": operation_id,
                "code": code,
                "reason_code": code,
                "error_type": type(error).__name__,
                **diagnostic_attributes(error, reason_code=code, stage=stage),
                **getattr(error, "log_attributes", {}),
            }
            if (status := provider_http_status(error)) is not None:
                attributes["http_status"] = status
            log_event(
                "Chat",
                "对话请求失败",
                attributes,
                event="chat.request.failed",
                severity="error",
                verbosity=0,
            )
            return
    except Exception:
        pass
    try:
        print(f"Real chat failed: {type(error).__name__}", file=sys.stderr)
    except Exception:
        pass


def _is_operation_cancelled(error: BaseException) -> bool:
    from app.plugin_sdk.sakura_cancellation import OperationCancelled

    return isinstance(error, OperationCancelled)


__all__ = [
    "HOST_CHAT_COMPLETED_EVENT",
    "REAL_CHAT_EXECUTION_LIMIT",
    "RealChatBoundary",
    "RealChatRejection",
]
