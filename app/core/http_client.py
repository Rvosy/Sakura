"""Small urllib helpers shared by local and remote HTTP clients."""

from __future__ import annotations

import socket
import threading
import urllib.request
from collections.abc import Callable
from typing import Any

from app.core.cancellation import CancelChecker, check_cancelled
from app.plugin_sdk.sakura_http import is_loopback_url, urlopen_direct_for_loopback

_CANCEL_POLL_SECONDS = 0.05
_READ_CHUNK_SIZE = 64 * 1024


def read_url_cancellable(
    opener: Callable[..., Any],
    request: str | urllib.request.Request,
    *,
    timeout: float,
    cancel_checker: CancelChecker | None = None,
) -> tuple[bytes, int | None]:
    """在 daemon I/O 线程读取响应，允许调用方取消并关闭活动响应。"""
    if cancel_checker is None:
        phase = "request"
        try:
            with opener(request, timeout=timeout) as response:
                phase = "read"
                return response.read(), getattr(response, "status", None)
        except Exception as error:
            error.sakura_request_stage = phase
            raise

    done = threading.Event()
    abort = threading.Event()
    state: dict[str, Any] = {}
    state_lock = threading.Lock()

    def run() -> None:
        chunks: list[bytes] = []
        phase = "request"
        try:
            with opener(request, timeout=timeout) as response:
                with state_lock:
                    state["response"] = response
                state["status"] = getattr(response, "status", None)
                phase = "read"
                while not abort.is_set():
                    chunk = response.read(_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    chunks.append(chunk)
                if not abort.is_set():
                    state["body"] = b"".join(chunks)
        except BaseException as exc:  # noqa: BLE001 - 原样回传 urllib/socket 异常
            if not abort.is_set():
                exc.sakura_request_stage = phase
                state["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, name="sakura-http-read", daemon=True).start()
    try:
        while not done.wait(_CANCEL_POLL_SECONDS):
            check_cancelled(cancel_checker)
        check_cancelled(cancel_checker)
    except BaseException:
        abort.set()
        with state_lock:
            response = state.get("response")
        _abort_response(response)
        raise
    error = state.get("error")
    if isinstance(error, BaseException):
        raise error
    return bytes(state.get("body", b"")), state.get("status")


def _abort_response(response: Any) -> None:
    """Abort a concurrent urllib read without waiting on its buffered-reader lock."""
    if response is None:
        return
    file_object = getattr(response, "fp", None)
    raw = getattr(file_object, "raw", None)
    active_socket = getattr(raw, "_sock", None)
    if isinstance(active_socket, socket.socket):
        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            active_socket.close()
        except OSError:
            pass

    close = getattr(response, "close", None)
    if not callable(close):
        return

    def close_response() -> None:
        try:
            close()
        except OSError:
            pass

    threading.Thread(
        target=close_response,
        name="sakura-http-close",
        daemon=True,
    ).start()
