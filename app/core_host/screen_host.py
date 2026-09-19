"""Plugin-scoped access to the shell's controlled screen resources."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.core_host.screen_capture import consume_screen_resource
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE

HOST_SCREEN_SERVICE = "sakura.host.screen"
_RESOLUTIONS = {"fullscreen", "720p", "1080p", "2160p"}
_RESOURCE_LIMIT = 20
_MEMORY_LIMIT = 128 * 1024 * 1024


class ScreenHostError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def caller_identity() -> tuple[str, str]:
    owner, scope = HOST_CALLER.get(), HOST_CALLER_SCOPE.get()
    if not owner or not scope:
        raise ScreenHostError("PLUGIN_CALLER_INVALID")
    return owner, scope


@dataclass
class _Capture:
    owner: tuple[str, str]
    session_id: str
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: str = ""


@dataclass
class _Resource:
    owner: tuple[str, str]
    session_id: str
    observation: object


class ScreenHost:
    def __init__(self, generation_id: str, *, session_provider: Callable,
                 emit_callback: Callable, resource_consumer: Callable = consume_screen_resource,
                 capture_timeout: float = 8.0, commit_scope: Callable | None = None) -> None:
        self._generation_id = generation_id
        self._session_provider = session_provider
        self._emit = emit_callback
        self._consume = resource_consumer
        self._timeout = capture_timeout
        self._commit_scope = commit_scope or (lambda owner, commit: commit())
        self._lock = threading.RLock()
        self._pending: dict[str, _Capture] = {}
        self._resources: dict[str, _Resource] = {}
        self._closed = False
        self._session_epoch = 0
        self._owner_revisions: dict[str, int] = {}

    def _session_snapshot(self, session_id: object) -> int:
        with self._lock:
            epoch = self._session_epoch
        # The provider can take the chat boundary lock. Session publication may
        # hold that lock while invalidating this host, so never nest it here.
        if not session_id or session_id != self._session_provider():
            raise ScreenHostError("SCREEN_SESSION_STALE")
        return epoch

    def _check_epoch(self, epoch: int) -> None:
        if self._closed or epoch != self._session_epoch:
            raise ScreenHostError("SCREEN_SESSION_STALE")

    def capture(self, request: Mapping) -> dict:
        owner = caller_identity()
        if (not isinstance(request, Mapping) or set(request) != {"sessionId", "resolution"}
                or request.get("resolution") not in _RESOLUTIONS):
            raise ScreenHostError("SCREEN_CAPTURE_REQUEST_INVALID")
        session_id = request["sessionId"]
        request_id = "capture-" + secrets.token_hex(16)
        pending = _Capture(owner, session_id)
        delivered = False
        with self._lock:
            owner_revision = self._owner_revisions.get(owner[0], 0)
        epoch = self._session_snapshot(session_id)
        with self._lock:
            self._check_epoch(epoch)
            if owner_revision != self._owner_revisions.get(owner[0], 0):
                raise ScreenHostError("SCREEN_CAPTURE_CANCELLED")
            if any(item.owner == owner for item in self._pending.values()):
                raise ScreenHostError("SCREEN_CAPTURE_BUSY")
            if sum(item.owner == owner for item in self._resources.values()) >= _RESOURCE_LIMIT:
                raise ScreenHostError("SCREEN_RESOURCE_LIMIT")
            self._commit_scope(owner, lambda: self._pending.__setitem__(request_id, pending))
        try:
            with self._lock:
                if self._pending.get(request_id) is not pending:
                    raise ScreenHostError("SCREEN_CAPTURE_CANCELLED")
                self._emit("host.screen.capture", {"requestId": request_id, **dict(request)})
            if not pending.done.wait(self._timeout):
                raise ScreenHostError("SCREEN_CAPTURE_TIMEOUT")
            if pending.error:
                raise ScreenHostError(pending.error)
            current_epoch = self._session_snapshot(session_id)
            with self._lock:
                self._check_epoch(current_epoch)
                if pending.result is None:
                    raise ScreenHostError("SCREEN_CAPTURE_CANCELLED")
                if pending.result["resourceId"] not in self._resources:
                    raise ScreenHostError("SCREEN_CAPTURE_CANCELLED")
                delivered = True
                return dict(pending.result)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
                if not delivered and pending.result is not None:
                    self._resources.pop(pending.result["resourceId"], None)

    def complete(self, payload: Mapping) -> dict:
        """Receive only authenticated shell replies, including late replies for cleanup."""
        if not isinstance(payload, Mapping) or not isinstance(payload.get("requestId"), str):
            raise ScreenHostError("SCREEN_CAPTURE_RESULT_INVALID")
        observation, failure = None, ""
        resource = payload.get("resource")
        if resource is not None:
            try:
                observation = self._consume(resource, generation_id=self._generation_id)
            except Exception as error:
                failure = str(error) if str(error).startswith("SCREEN_") else "SCREEN_RESOURCE_INVALID"
        else:
            error = payload.get("error")
            failure = str(error.get("code", "SCREEN_CAPTURE_FAILED")) if isinstance(error, Mapping) else "SCREEN_CAPTURE_FAILED"
        with self._lock:
            pending = self._pending.get(payload["requestId"])
        if pending is None or pending.done.is_set():
            return {"accepted": False}
        try:
            epoch = self._session_snapshot(pending.session_id)
        except ScreenHostError as error:
            failure, epoch = error.code, -1
        with self._lock:
            if self._pending.get(payload["requestId"]) is not pending or pending.done.is_set():
                return {"accepted": False}
            try:
                self._check_epoch(epoch)
                if payload.get("sessionId") != pending.session_id:
                    raise ScreenHostError("SCREEN_SESSION_STALE")
                if failure:
                    raise ScreenHostError(failure)
                owned = [item for item in self._resources.values() if item.owner == pending.owner]
                if len(owned) >= _RESOURCE_LIMIT or sum(len(item.observation.data_url) for item in owned) + len(observation.data_url) > _MEMORY_LIMIT:
                    raise ScreenHostError("SCREEN_RESOURCE_LIMIT")
                resource_id = "image-" + secrets.token_hex(16)
                self._resources[resource_id] = _Resource(pending.owner, pending.session_id, observation)
                pending.result = {"resourceId": resource_id, "sessionId": pending.session_id,
                                  "width": observation.width, "height": observation.height,
                                  "capturedAt": observation.captured_at, "screenName": observation.screen_name}
            except ScreenHostError as error:
                pending.error = error.code
            finally:
                pending.done.set()
            return {"accepted": not bool(pending.error)}

    def release(self, resource_id: str) -> dict:
        owner = caller_identity()
        with self._lock:
            item = self._resources.get(resource_id)
            if item is None:
                return {"released": False}
            if item.owner != owner:
                raise ScreenHostError("SCREEN_RESOURCE_UNAUTHORIZED")
            self._resources.pop(resource_id)
            return {"released": True}

    def take_resources(self, owner: tuple[str, str], session_id: str, ids: Sequence[str]) -> tuple:
        epoch = self._session_snapshot(session_id)
        with self._lock:
            self._check_epoch(epoch)
            if (not isinstance(ids, list) or len(ids) > _RESOURCE_LIMIT
                    or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)):
                raise ScreenHostError("SCREEN_RESOURCES_INVALID")
            items = [self._resources.get(value) for value in ids]
            if any(item is None or item.owner != owner or item.session_id != session_id for item in items):
                raise ScreenHostError("SCREEN_RESOURCE_UNAUTHORIZED")
            for value in ids:
                self._resources.pop(value)
            return tuple(item.observation for item in items)

    def _revoke(self, owner_id: str | None) -> None:
        with self._lock:
            for key, pending in tuple(self._pending.items()):
                if owner_id is None or pending.owner[0] == owner_id:
                    self._pending.pop(key)
                    pending.error = "SCREEN_CAPTURE_CANCELLED"
                    pending.done.set()
            self._resources = {key: item for key, item in self._resources.items()
                               if owner_id is not None and item.owner[0] != owner_id}

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            self._owner_revisions[plugin_id] = self._owner_revisions.get(plugin_id, 0) + 1
            self._revoke(plugin_id)

    def invalidate_session(self) -> None:
        with self._lock:
            self._session_epoch += 1
            self._revoke(None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._revoke(None)


def migrate_legacy_screen_settings(user_root) -> None:
    """Seed plugin config before its process starts; legacy YAML remains read-only."""
    import json
    import os

    from app.config.settings_service import AppSettingsService
    from app.storage.paths import StoragePaths

    target = StoragePaths(user_root).plugin_data_for("sakura.screen_awareness") / "config.json"
    try:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        # Leave the private file to PluginConfig: its failure belongs to this
        # optional plugin, not construction of Core and the management surface.
        return
    if not isinstance(existing, dict):
        return
    keys = {"enabled", "checkIntervalMinutes", "cooldownMinutes", "batchLimit", "resolution"}
    if keys.issubset(existing):
        return
    old = AppSettingsService(user_root).load_screen_awareness_settings().normalized()
    values = {"enabled": old.allows_screen_context(), "checkIntervalMinutes": old.check_interval_minutes,
              "cooldownMinutes": old.cooldown_minutes, "batchLimit": old.screen_context_batch_limit,
              "resolution": old.screen_context_resolution, **existing}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".config-" + secrets.token_hex(8) + ".tmp")
    try:
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
