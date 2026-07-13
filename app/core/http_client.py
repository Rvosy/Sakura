"""Small urllib helpers shared by local and remote HTTP clients."""

from __future__ import annotations

import ipaddress
import socket
import threading
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.core.cancellation import CancelChecker, check_cancelled

_LOOPBACK_PROXY_BYPASS_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_CANCEL_POLL_SECONDS = 0.05
_READ_CHUNK_SIZE = 64 * 1024


def is_loopback_url(url: str) -> bool:
    """Return True when *url* targets this machine's loopback interface."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False

    normalized_host = host.rstrip(".").casefold()
    if normalized_host == "localhost":
        return True

    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


def urlopen_direct_for_loopback(
    url: str | urllib.request.Request,
    data: bytes | None = None,
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
):
    """Open loopback URLs without urllib's environment/system proxy handlers.

    Remote URLs still use urllib.request.urlopen so user-configured proxies keep
    working for normal API and download traffic.
    """

    if is_loopback_url(_request_url(url)):
        if data is None:
            return _LOOPBACK_PROXY_BYPASS_OPENER.open(url, timeout=timeout)
        return _LOOPBACK_PROXY_BYPASS_OPENER.open(url, data=data, timeout=timeout)
    if data is None:
        return urllib.request.urlopen(url, timeout=timeout)
    return urllib.request.urlopen(url, data=data, timeout=timeout)


def read_url_cancellable(
    opener: Callable[..., Any],
    request: str | urllib.request.Request,
    *,
    timeout: float,
    cancel_checker: CancelChecker | None = None,
) -> tuple[bytes, int | None]:
    """在 daemon I/O 线程读取响应，允许调用方取消并关闭活动响应。"""
    if cancel_checker is None:
        with opener(request, timeout=timeout) as response:
            return response.read(), getattr(response, "status", None)

    done = threading.Event()
    abort = threading.Event()
    state: dict[str, Any] = {}
    state_lock = threading.Lock()

    def run() -> None:
        chunks: list[bytes] = []
        try:
            with opener(request, timeout=timeout) as response:
                with state_lock:
                    state["response"] = response
                state["status"] = getattr(response, "status", None)
                while not abort.is_set():
                    chunk = response.read(_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    chunks.append(chunk)
                if not abort.is_set():
                    state["body"] = b"".join(chunks)
        except BaseException as exc:  # noqa: BLE001 - 原样回传 urllib/socket 异常
            if not abort.is_set():
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
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass
        raise
    error = state.get("error")
    if isinstance(error, BaseException):
        raise error
    return bytes(state.get("body", b"")), state.get("status")


def _request_url(url: str | urllib.request.Request) -> str:
    full_url = getattr(url, "full_url", None)
    if isinstance(full_url, str):
        return full_url
    return str(url)
