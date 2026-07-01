"""Small urllib helpers shared by local and remote HTTP clients."""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse

_LOOPBACK_PROXY_BYPASS_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def _request_url(url: str | urllib.request.Request) -> str:
    full_url = getattr(url, "full_url", None)
    if isinstance(full_url, str):
        return full_url
    return str(url)
