from __future__ import annotations

import urllib.request
import threading
import time

import pytest

from app.core import http_client
from app.core.cancellation import CancellationToken, OperationCancelled


def test_is_loopback_url_detects_local_hosts() -> None:
    assert http_client.is_loopback_url("http://127.0.0.1:9880/tts")
    assert http_client.is_loopback_url("http://127.12.34.56:9880/tts")
    assert http_client.is_loopback_url("http://localhost:9880/tts")
    assert http_client.is_loopback_url("http://[::1]:9880/tts")
    assert not http_client.is_loopback_url("https://api.example.com/v1")
    assert not http_client.is_loopback_url("http://192.168.1.20:9880/tts")


def test_urlopen_direct_for_loopback_uses_proxyless_opener(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[object, int]] = []

    class FakeOpener:
        def open(self, url, timeout):  # type: ignore[no-untyped-def]
            calls.append((url, timeout))
            return "local-response"

    def fail_standard_urlopen(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("loopback requests must bypass urllib's standard opener")

    monkeypatch.setattr(http_client, "_LOOPBACK_PROXY_BYPASS_OPENER", FakeOpener())
    monkeypatch.setattr(http_client.urllib.request, "urlopen", fail_standard_urlopen)

    request = urllib.request.Request("http://localhost:9880/tts", data=b"{}", method="POST")

    assert http_client.urlopen_direct_for_loopback(request, timeout=7) == "local-response"
    assert calls == [(request, 7)]


def test_urlopen_direct_for_loopback_keeps_standard_opener_for_remote(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    class FakeOpener:
        def open(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("remote requests should keep urllib's standard opener")

    def fake_standard_urlopen(url, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["timeout"] = timeout
        return "remote-response"

    monkeypatch.setattr(http_client, "_LOOPBACK_PROXY_BYPASS_OPENER", FakeOpener())
    monkeypatch.setattr(http_client.urllib.request, "urlopen", fake_standard_urlopen)

    request = urllib.request.Request("https://api.example.com/v1/models", method="GET")

    assert http_client.urlopen_direct_for_loopback(request, timeout=11) == "remote-response"
    assert captured == {"url": request, "timeout": 11}


def test_read_url_cancellable_returns_without_waiting_for_blocked_read() -> None:
    token = CancellationToken()
    entered = threading.Event()
    released = threading.Event()

    class BlockingResponse:
        status = 200

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        def read(self, _size=-1):  # type: ignore[no-untyped-def]
            entered.set()
            released.wait(5)
            return b""

        def close(self) -> None:
            released.set()

    threading.Thread(target=lambda: (entered.wait(1), token.cancel()), daemon=True).start()
    started = time.monotonic()
    with pytest.raises(OperationCancelled):
        http_client.read_url_cancellable(
            lambda *_args, **_kwargs: BlockingResponse(),
            "https://example.test",
            timeout=60,
            cancel_checker=token.throw_if_cancelled,
        )

    assert time.monotonic() - started < 1
