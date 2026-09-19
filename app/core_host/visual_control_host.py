"""Ordinary plugins submit controls through the current visual binding."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass

from app.core_host.screen_host import caller_identity
from app.core_host.visual_host import VisualHostError
from app.plugin_sdk.sakura_visual_control import validate_visual_control

HOST_VISUAL_SERVICE = "sakura.host.visual"
_RECEIPT_LIMIT = 128


@dataclass
class _Receipt:
    owner: tuple[str, str]
    target: dict
    status: str = "accepted"
    emitted: bool = False
    claimed: bool = False
    error_code: str = ""


class HostVisualService:
    def __init__(self, *, binding_provider: Callable, emit_callback: Callable,
                 is_idle: Callable, select_callback: Callable, commit_scope: Callable | None = None) -> None:
        self._binding_provider = binding_provider
        self._emit = emit_callback
        self._is_idle = is_idle
        self._select = select_callback
        self._commit_scope = commit_scope or (lambda owner, commit: commit())
        self._lock = threading.RLock()
        self._receipts: dict[str, _Receipt] = {}
        self._closed = False
        self._activity_revision = 0
        self._owner_revisions: dict[str, int] = {}

    def _binding(self):
        value = self._binding_provider()
        if self._closed or value is None or value[1] is None:
            raise VisualHostError("VISUAL_NOT_BOUND")
        character_id, binding = value
        presentation = binding.presentation()
        target = {"characterId": character_id, "bindingId": presentation["bindingId"],
                  "resourceId": presentation["resourceId"]}
        return target, binding, presentation

    def _matches(self, target):
        try:
            return self._binding()[0] == target
        except VisualHostError:
            return False

    def current(self) -> dict:
        self.invalidate_target()
        try:
            target, binding, presentation = self._binding()
        except VisualHostError as error:
            return {"target": None, "reasonCode": error.code}
        description = binding.description
        return {"target": target, "providerId": presentation["providerId"], "type": presentation["type"],
                "prompt": description["prompt"], "outputSchema": deepcopy(description["outputSchema"])}

    def apply(self, request: Mapping) -> dict:
        owner = caller_identity()
        with self._lock:
            activity_revision = self._activity_revision
            owner_revision = self._owner_revisions.get(owner[0], 0)
        if not isinstance(request, Mapping) or set(request) != {"target", "control"}:
            raise VisualHostError("VISUAL_CONTROL_INVALID")
        target, binding, _ = self._binding()
        if request["target"] != target:
            return {"accepted": False, "reasonCode": "VISUAL_BINDING_EXPIRED"}
        if not self._is_idle():
            return {"accepted": False, "reasonCode": "VISUAL_BUSY"}
        receipt = _Receipt(owner, target)
        request_id = "visual-" + secrets.token_hex(16)
        with self._lock:
            if len(self._receipts) >= _RECEIPT_LIMIT:
                return {"accepted": False, "reasonCode": "VISUAL_RECEIPT_LIMIT"}
            if self._closed:
                return {"accepted": False, "reasonCode": "VISUAL_NOT_BOUND"}
            if (activity_revision != self._activity_revision
                    or owner_revision != self._owner_revisions.get(owner[0], 0)):
                return {"accepted": False, "reasonCode": "VISUAL_BINDING_EXPIRED"}
            self._commit_scope(owner, lambda: self._receipts.__setitem__(request_id, receipt))
        # Provider work may finish after its caller, target or generation expires.
        try:
            parsed = binding.parse_control(request["control"])
        except Exception:
            with self._lock:
                self._receipts.pop(request_id, None)
            raise
        idle = self._is_idle()
        with self._lock:
            if (self._receipts.get(request_id) is not receipt or receipt.status != "accepted"
                    or not self._matches(target)):
                self._cancel(request_id, receipt)
                return {"accepted": False, "reasonCode": "VISUAL_BINDING_EXPIRED"}
            if parsed.control is None or not idle:
                self._receipts.pop(request_id, None)
                return {"accepted": False, "reasonCode": (parsed.reason_code if parsed.reason_code != "READY" else "VISUAL_CONTROL_INVALID") if parsed.control is None else "VISUAL_BUSY"}
            try:
                control = validate_visual_control(parsed.control)
                receipt.emitted = True
                self._emit("host.visual.apply", {"requestId": request_id, "target": target, "control": control})
            except Exception:
                self._receipts.pop(request_id, None)
                raise
            return {"accepted": True, "requestId": request_id, "status": receipt.status}

    def claim(self, request_id: str, target: Mapping) -> dict:
        """Trusted desktop admission immediately before playing a received control."""
        idle = self._is_idle()
        with self._lock:
            receipt = self._receipts.get(request_id)
            if (receipt is None or receipt.target != target or receipt.status != "accepted"
                    or not receipt.emitted or receipt.claimed):
                return {"accepted": False}
            if not self._matches(target) or not idle:
                self._cancel(request_id, receipt)
                return {"accepted": False}
            receipt.claimed = True
            return {"accepted": True}

    def complete(self, payload: Mapping) -> dict:
        if (not isinstance(payload, Mapping) or not {"requestId", "target", "status"}.issubset(payload)
                or set(payload) - {"requestId", "target", "status", "errorCode"}
                or payload["status"] not in {"displayed", "failed"}):
            raise VisualHostError("VISUAL_RECEIPT_INVALID")
        with self._lock:
            receipt = self._receipts.get(payload["requestId"])
            if receipt is None or receipt.target != payload["target"] or receipt.status != "accepted":
                return {"accepted": False}
            if not self._matches(receipt.target):
                self._cancel(payload["requestId"], receipt)
                return {"accepted": False}
            if payload["status"] == "displayed" and not receipt.claimed:
                return {"accepted": False}
            receipt.status = payload["status"]
            receipt.error_code = str(payload.get("errorCode", ""))[:128]
            return {"accepted": True}

    def _owned(self, request_id: str) -> _Receipt:
        receipt = self._receipts.get(request_id)
        if receipt is None or receipt.owner != caller_identity():
            raise VisualHostError("VISUAL_RECEIPT_UNAUTHORIZED")
        return receipt

    def status(self, request_id: str) -> dict:
        with self._lock:
            receipt = self._owned(request_id)
            if not self._matches(receipt.target):
                self._cancel(request_id, receipt)
            return {"requestId": request_id, "target": dict(receipt.target), "status": receipt.status,
                    **({"reasonCode": receipt.error_code} if receipt.error_code else {})}

    def release(self, request_id: str) -> dict:
        with self._lock:
            receipt = self._owned(request_id)
            self._cancel(request_id, receipt)
            self._receipts.pop(request_id, None)
            return {"released": True}

    def _cancel(self, request_id: str, receipt: _Receipt) -> None:
        if receipt.status in {"cancelled", "failed"}:
            return
        receipt.status = "cancelled"
        if receipt.emitted:
            self._emit("host.visual.cancel", {"requestId": request_id, "target": dict(receipt.target)})

    def select(self, request: Mapping) -> dict:
        caller_identity()
        if (not isinstance(request, Mapping) or set(request) != {"target", "resourceId"}
                or not isinstance(request["resourceId"], str) or not request["resourceId"]):
            raise VisualHostError("VISUAL_RESOURCE_INVALID")
        if not self._matches(request["target"]):
            return {"accepted": False, "reasonCode": "VISUAL_BINDING_EXPIRED"}
        if not self._is_idle():
            return {"accepted": False, "reasonCode": "VISUAL_BUSY"}
        # The settings owner must repeat the target check under its save lock.
        result = self._select(dict(request["target"]), request["resourceId"])
        self.invalidate_target()
        return result

    def invalidate_target(self) -> None:
        with self._lock:
            for request_id, receipt in tuple(self._receipts.items()):
                if not self._matches(receipt.target):
                    self._cancel(request_id, receipt)

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            self._owner_revisions[plugin_id] = self._owner_revisions.get(plugin_id, 0) + 1
            for request_id, receipt in tuple(self._receipts.items()):
                if receipt.owner[0] == plugin_id:
                    self._cancel(request_id, receipt)
                    self._receipts.pop(request_id, None)

    def invalidate_activity(self) -> None:
        """Revoke controls when the desktop detaches without retiring the binding."""
        with self._lock:
            self._activity_revision += 1
            for request_id, receipt in tuple(self._receipts.items()):
                self._cancel(request_id, receipt)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for request_id, receipt in tuple(self._receipts.items()):
                self._cancel(request_id, receipt)
            self._receipts.clear()
