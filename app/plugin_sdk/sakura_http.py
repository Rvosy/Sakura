"""HTTP helpers available to isolated plugins without Core dependencies."""
from __future__ import annotations

import copy
import ipaddress
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse


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

    Remote URLs rebuild their proxy handler for every attempt so system proxy
    changes take effect without restarting the process.
    """

    # ProxyHandler mutates Request.host/tunnel state. A retry must start with
    # the caller's original destination, including when the proxy is disabled.
    if isinstance(url, urllib.request.Request):
        url = copy.copy(url)
        url.headers = dict(url.headers)
        url.unredirected_hdrs = dict(url.unredirected_hdrs)
    return _urlopen_with_current_proxy(url, data=data, timeout=timeout)


def _urlopen_with_current_proxy(
    url: str | urllib.request.Request,
    data: bytes | None = None,
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
):
    """Open a remote URL with the proxy configuration visible for this attempt."""

    opener = urllib.request.build_opener(_CurrentProxyHandler())
    if data is None:
        return opener.open(url, timeout=timeout)
    return opener.open(url, data=data, timeout=timeout)


class _CurrentProxyHandler(urllib.request.ProxyHandler):
    def __init__(self):
        # Install handlers even when the first request has no proxy, so a
        # redirect can observe a newly enabled proxy too.
        super().__init__({})

    def http_open(self, request):
        # A redirect can change proxy or become direct. Never forward the
        # previous proxy's credentials to the new hop.
        request.remove_header("Proxy-authorization")
        proxy = proxy_for_url(request.full_url)
        if proxy:
            return self.proxy_open(request, proxy, request.type)
        return None

    https_open = http_open


def proxy_for_url(url: str) -> str | None:
    """Resolve a proxy for one new connection, retaining system bypass rules."""
    parsed = urlparse(url)
    if is_loopback_url(url) or urllib.request.proxy_bypass(parsed.netloc):
        return None
    proxies = urllib.request.getproxies()
    value = proxies.get(parsed.scheme) or proxies.get("all")
    if value and "://" not in value:
        value = "http://" + value
    return value
