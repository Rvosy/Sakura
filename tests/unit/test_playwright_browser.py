from __future__ import annotations

import socket
import time
from concurrent.futures import Future

import pytest

from plugins.playwright_browser import browser


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/secret.txt",
        "data:text/plain,secret",
        "javascript:alert(1)",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data",
        "http://user:pass@example.com",
    ],
)
def test_validate_public_url_rejects_local_and_unsafe_targets(url: str) -> None:
    with pytest.raises(ValueError):
        browser._validate_public_url(url)


def test_validate_public_url_checks_all_dns_results(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        browser.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )

    with pytest.raises(ValueError, match="私网"):
        browser._validate_public_url("https://example.test")


def test_validate_public_url_accepts_public_dns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        browser.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )

    browser._validate_public_url("https://example.test/path")


def test_request_guard_aborts_private_redirect(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    class Route:
        def abort(self, reason: str) -> None:
            calls.append(f"abort:{reason}")

        def continue_(self) -> None:
            calls.append("continue")

    request = type("Request", (), {"url": "http://127.0.0.1/admin"})()
    browser._guard_browser_request(Route(), request)

    assert calls == ["abort:blockedbyclient"]


def test_shutdown_does_not_wait_forever_for_stuck_browser_thread(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pending: Future[object] = Future()

    class StuckRunner:
        def submit(self, _func):  # type: ignore[no-untyped-def]
            return pending

        def shutdown(self, *, cancel_futures: bool = True) -> None:
            assert cancel_futures

    monkeypatch.setattr(browser, "_bg_executor", StuckRunner())
    monkeypatch.setattr(browser, "_browser_thread_id", None)
    monkeypatch.setattr(browser, "_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    started = time.monotonic()

    browser.shutdown_browser()

    assert time.monotonic() - started < 0.2
