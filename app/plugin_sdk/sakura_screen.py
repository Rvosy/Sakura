"""Controlled screenshots with cleanup across uncertain RPC responses."""

from __future__ import annotations

import threading
import uuid


class ScreenError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ScreenClient:
    def __init__(self, service, *, capture_timeout=10.0, cleanup_timeout=3.0):
        self._service = service
        self._capture_timeout = capture_timeout
        self._cleanup_timeout = cleanup_timeout
        self._lock = threading.RLock()
        self._capture_lock = threading.Lock()
        self._pending = set()
        self._closed = False

    def _cleanup(self, operation_id):
        response = self._service.invoke("release_capture", operation_id,
                                        timeout_seconds=self._cleanup_timeout)
        if not isinstance(response, dict) or not isinstance(response.get("released"), bool):
            raise ScreenError("SCREEN_CAPTURE_CLEANUP_INVALID")
        with self._lock:
            self._pending.discard(operation_id)

    def _cleanup_pending(self):
        with self._lock:
            operations = tuple(self._pending)
        for operation_id in operations:
            try:
                self._cleanup(operation_id)
            except Exception as error:
                raise ScreenError("SCREEN_CAPTURE_CLEANUP_PENDING") from error

    def capture(self, request):
        if not self._capture_lock.acquire(blocking=False):
            raise ScreenError("SCREEN_CAPTURE_BUSY")
        try:
            self._cleanup_pending()
            with self._lock:
                if self._closed:
                    raise ScreenError("SCREEN_CLIENT_CLOSED")
                operation_id = uuid.uuid4().hex
                self._pending.add(operation_id)
            try:
                response = self._service.invoke("capture", {**request, "operationId": operation_id},
                                                timeout_seconds=self._capture_timeout)
                with self._lock:
                    if self._closed:
                        raise ScreenError("SCREEN_CLIENT_CLOSED")
                    self._pending.discard(operation_id)
                return response
            except BaseException as error:
                try:
                    self._cleanup(operation_id)
                except Exception as cleanup_error:
                    # Keep the unknown operation until a confirmed cleanup;
                    # the next capture may not accumulate another resource.
                    error.recovery_error = cleanup_error
                raise
        finally:
            self._capture_lock.release()

    def release(self, resource_id):
        return self._service.release(resource_id)

    def close(self):
        with self._lock:
            self._closed = True
        self._cleanup_pending()


__all__ = ["ScreenClient", "ScreenError"]
