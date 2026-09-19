"""Host admission for ordinary plugins initiating current-session interactions."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.core_host.real_chat import RealChatRejection
from app.core_host.screen_host import ScreenHost, ScreenHostError, caller_identity

HOST_CHAT_SERVICE = "sakura.host.chat"


@dataclass
class _ActiveTurn:
    owner: tuple[str, str]
    boundary: object
    metadata: dict
    publication_lock: object = field(default_factory=threading.Lock)
    revoked: bool = False
    started: bool = False
    terminal: bool = False


class ChatHost:
    def __init__(self, *, boundary_provider: Callable, screen_host: ScreenHost,
                 emit_callback: Callable, commit_scope: Callable | None = None) -> None:
        self._boundary_provider = boundary_provider
        self._screen = screen_host
        self._emit = emit_callback
        self._commit_scope = commit_scope or (lambda owner, commit: commit())
        self._lock = threading.RLock()
        self._ui: dict = {"sessionId": None, "idle": False, "activityRevision": 0}
        self._active: dict[str, _ActiveTurn] = {}
        self._closed = False
        self._session_epoch = 0
        self._owner_revisions: dict[str, int] = {}

    def set_ui_state(self, facts: Mapping) -> dict:
        if (not isinstance(facts, Mapping) or set(facts) != {"sessionId", "idle", "activityRevision"}
                or not isinstance(facts["sessionId"], str) or not isinstance(facts["idle"], bool)
                or type(facts["activityRevision"]) is not int or facts["activityRevision"] < 0):
            raise RealChatRejection("CHAT_UI_STATE_INVALID", "互动状态无效")
        boundary = self._boundary_provider()
        with self._lock:
            epoch = self._session_epoch
        state = boundary.current_host_state() if boundary is not None else {}
        with self._lock:
            if self._closed or epoch != self._session_epoch or facts["sessionId"] != state.get("sessionId"):
                return {"accepted": False}
            if (self._ui["sessionId"] == facts["sessionId"]
                    and self._ui["activityRevision"] > facts["activityRevision"]):
                return {"accepted": False}
            self._ui = dict(facts)
            return {"accepted": True}

    def current(self) -> dict:
        boundary = self._boundary_provider()
        state = boundary.current_host_state() if boundary is not None else {
            "sessionId": None, "characterId": None, "idle": False, "interactionRevision": 0}
        with self._lock:
            return {**state, "idle": bool(not self._closed and state["idle"] and self._ui["idle"]
                                         and state["sessionId"] == self._ui["sessionId"]),
                    "activityRevision": self._ui["activityRevision"]}

    def commit_idle(self, expected: Mapping, commit: Callable):
        """Keep a short preference save atomic with the desktop's activity facts.

        The caller separately owns RealChat's idle update reservation. Never
        consult that boundary while holding this lock.
        """
        with self._lock:
            if (self._closed or not expected.get("idle") or not self._ui["idle"]
                    or self._ui["sessionId"] != expected.get("sessionId")
                    or self._ui["activityRevision"] != expected.get("activityRevision")):
                raise RealChatRejection("CHAT_BUSY", "当前互动状态已变化")
            return commit()

    def submit(self, request: Mapping) -> dict:
        owner = caller_identity()
        if (not isinstance(request, Mapping) or set(request) != {"sessionId", "message", "resources"}
                or not isinstance(request["message"], str) or not request["message"].strip()
                or len(request["message"]) > 32768 or not isinstance(request["resources"], list)):
            raise RealChatRejection("INVALID_CHAT_PAYLOAD", "主动互动输入无效")
        with self._lock:
            epoch = self._session_epoch
            owner_revision = self._owner_revisions.get(owner[0], 0)
        state = self.current()
        if not state["sessionId"] or state["sessionId"] != request["sessionId"]:
            return {"accepted": False, "reasonCode": "CHAT_SESSION_STALE"}
        if not state["idle"]:
            return {"accepted": False, "reasonCode": "CHAT_BUSY"}
        boundary = self._boundary_provider()
        try:
            observations = self._screen.take_resources(owner, request["sessionId"], request["resources"])
            operation_id = boundary.reserve_plugin_message(owner[0], request["sessionId"], request["message"], observations)
        except (RealChatRejection, ScreenHostError) as error:
            return {"accepted": False, "reasonCode": error.code}
        metadata = {"operationId": operation_id, "sessionId": request["sessionId"],
                    "characterId": state["characterId"], "sourcePluginId": owner[0], "presentation": "silent"}
        active = _ActiveTurn(owner, boundary, metadata)
        try:
            with self._lock:
                stale = (self._closed or epoch != self._session_epoch
                         or owner_revision != self._owner_revisions.get(owner[0], 0)
                         or self._ui["sessionId"] != request["sessionId"] or not self._ui["idle"]
                         or self._ui["activityRevision"] != state["activityRevision"])
                if not stale:
                    self._commit_scope(owner, lambda: self._active.__setitem__(operation_id, active))
        except Exception:
            boundary.abandon_host_message(operation_id)
            raise
        if stale:
            boundary.abandon_host_message(operation_id)
            return {"accepted": False, "reasonCode": "CHAT_ADMISSION_EXPIRED"}

        def publish(name: str, payload: Mapping) -> None:
            # This callback runs under the boundary's terminal lock. Never take
            # the admission lock here, or call back into the boundary while
            # holding the publication lock from the revocation path.
            with active.publication_lock:
                if active.revoked or active.terminal:
                    return
                active.started = True
                active.terminal = name != "chat.started"
                self._emit("host." + name, {**dict(payload), **metadata})

        def run() -> None:
            try:
                boundary.run_reserved_plugin_message(operation_id, publish)
            finally:
                with self._lock:
                    self._active.pop(operation_id, None)

        worker = threading.Thread(target=run, name="sakura-plugin-chat-" + operation_id[-8:], daemon=True)
        try:
            worker.start()
        except Exception:
            with self._lock:
                self._active.pop(operation_id, None)
            boundary.abandon_host_message(operation_id)
            raise RealChatRejection("CHAT_START_FAILED", "互动未能启动")
        return {"accepted": True, "operationId": operation_id}

    def cancel(self, operation_id: str) -> dict:
        owner = caller_identity()
        with self._lock:
            active = self._active.get(operation_id)
            if active is None:
                return {"accepted": False}
            if active.owner != owner:
                raise RealChatRejection("CHAT_OPERATION_UNAUTHORIZED", "互动不属于当前插件")
            boundary = active.boundary
        return {"accepted": boundary.cancel_host_message(operation_id)}

    def _revoke(self, operations) -> None:
        for operation_id, active in operations:
            with active.publication_lock:
                active.revoked = True
                if active.started and not active.terminal:
                    active.terminal = True
                    self._emit("host.chat.cancelled", dict(active.metadata))
            active.boundary.cancel_host_message(operation_id)

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            self._owner_revisions[plugin_id] = self._owner_revisions.get(plugin_id, 0) + 1
            active = [(key, value) for key, value in self._active.items() if value.owner[0] == plugin_id]
        self._revoke(active)

    def invalidate_session(self) -> None:
        with self._lock:
            self._session_epoch += 1
            self._ui = {"sessionId": None, "idle": False, "activityRevision": 0}
            active = tuple(self._active.items())
        self._revoke(active)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.invalidate_session()
