from __future__ import annotations

import html
import http.client
import socket
import ssl
import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse


DEFAULT_TIMEOUT_SECONDS = 12
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class WebError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "搜索公开网页，并返回标题、链接和简短摘要。适合查询最新信息、资料来源和网页入口。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回多少条结果，范围 1-10。",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch_url",
        "description": "读取一个公开 http/https 网页，抽取标题、正文文本和页面链接。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要读取的公开网页 URL，仅支持 http 或 https。",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "正文最多返回多少字符，范围 500-20000。",
                    "minimum": 500,
                    "maximum": 20000,
                    "default": 6000,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]



def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空。")

    url = "https://www.bing.com/search?" + urlencode({"q": query})
    html_text = _read_url_text(url, max_bytes=512_000)
    parser = BingSearchParser()
    parser.feed(html_text)
    results = _dedupe_results(parser.results)[:max_results]
    if not results and not parser.no_results:
        raise WebError("WEB_SEARCH_RESPONSE_INVALID", "搜索服务未返回可识别的结果，可能需要验证或页面格式已变化。")
    return {
        "query": query,
        "source": "Bing",
        "results": [
            {"title": item.title, "url": item.url, "snippet": item.snippet}
            for item in results
        ],
    }


def fetch_url(url: str, max_chars: int = 6000) -> dict[str, Any]:
    normalized_url = _validate_public_http_url(url)
    raw_text, content_type, final_url, response_truncated = _read_url_text_with_metadata(
        normalized_url,
        max_bytes=max(256_000, min(max_chars * 8, 1_500_000)),
    )
    if "html" in content_type.lower():
        parser = PageTextParser()
        parser.feed(raw_text)
        text = _normalize_space(parser.text)
        title = _normalize_space(parser.title)
        links = [
            {"text": item["text"], "url": urljoin(final_url, item["url"])}
            for item in parser.links[:30]
            if urlparse(urljoin(final_url, item["url"])).scheme in {"http", "https"}
        ]
    else:
        if not (content_type.lower().startswith("text/") or "json" in content_type.lower() or "xml" in content_type.lower()):
            raise WebError("WEB_CONTENT_UNSUPPORTED", "该链接不是可读取的网页或文本。")
        text = _normalize_space(raw_text)
        title = ""
        links = []
    return {
        "url": final_url,
        "content_type": content_type,
        "title": title,
        "text": text[:max_chars],
        "truncated": response_truncated or len(text) > max_chars,
        "links": links,
    }


class BingSearchParser(HTMLParser):
    """解析 Bing 搜索结果页中自然搜索结果的标题、链接和摘要。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._result_depth = 0
        self._in_title_link = False
        self._in_snippet = False
        self._active_href = ""
        self._active_text: list[str] = []
        self._snippet_parts: list[str] = []
        self.no_results = False
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if "b_no" in classes:
            self.no_results = True
        if tag == "li" and "b_algo" in classes:
            self._result_depth = 1
            self._snippet_parts = []
            return
        if self._result_depth and tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._result_depth += 1
        if self._result_depth and tag == "h2":
            self._in_heading = True
        if self._result_depth and self._in_heading and tag == "a":
            href = _normalize_result_href(attrs_map.get("href", ""))
            if href:
                self._active_href = href
                self._active_text = []
                self._in_title_link = True
        elif self._result_depth and tag == "p":
            self._in_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title_link and self._active_href:
            self._active_text.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_heading = False
        if tag == "a" and self._in_title_link:
            title = _normalize_space("".join(self._active_text))
            if title and _looks_like_result_url(self._active_href):
                self.results.append(SearchResult(title=title[:500], url=self._active_href[:8192]))
            self._active_href = ""
            self._active_text = []
            self._in_title_link = False
        elif tag == "p" and self._in_snippet:
            snippet = _normalize_space("".join(self._snippet_parts))
            if snippet and self.results:
                previous = self.results[-1]
                if not previous.snippet and snippet != previous.title:
                    self.results[-1] = SearchResult(
                        title=previous.title,
                        url=previous.url,
                        snippet=snippet[:300],
                    )
            self._in_snippet = False
            self._snippet_parts = []
        if self._result_depth:
            self._result_depth -= 1

class PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[dict[str, str]] = []
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._active_link: str | None = None
        self._active_link_text: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self._text_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = attrs_map.get("href", "")
            if href and urlparse(href).scheme in {"", "http", "https"}:
                self._active_link = href
                self._active_link_text = []
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        if self._active_link is not None:
            self._active_link_text.append(data)
        stripped = data.strip()
        if stripped:
            self._text_parts.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
            self.title = "".join(self._title_parts)
        elif tag == "a" and self._active_link is not None:
            text = _normalize_space("".join(self._active_link_text))
            if text:
                self.links.append({"text": text[:120], "url": self._active_link[:8192]})
            self._active_link = None
            self._active_link_text = []


def _read_url_text(url: str, max_bytes: int) -> str:
    text, _content_type, _final_url, _truncated = _read_url_text_with_metadata(url, max_bytes)
    return text


def _read_url_text_with_metadata(url: str, max_bytes: int) -> tuple[str, str, str, bool]:
    current_url = _validate_public_http_url(url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        status, reason, headers, body = _request_public_url_once(current_url, max_bytes + 1)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("Location", "").strip()
            if not location:
                raise WebError("WEB_REDIRECT_INVALID", f"HTTP {status}: 重定向缺少 Location。")
            if redirect_count >= MAX_REDIRECTS:
                raise WebError("WEB_REDIRECT_LIMIT", "网页重定向次数过多。")
            current_url = _validate_public_http_url(urljoin(current_url, location))
            continue
        if status >= 400:
            raise WebError("WEB_HTTP_ERROR", f"网站返回 HTTP {status}。")
        content_type = headers.get("Content-Type", "")
        final_url = current_url
        break
    else:  # pragma: no cover - 循环上界保护
        raise WebError("WEB_REDIRECT_LIMIT", "网页重定向次数过多。")

    charset = _charset_from_content_type(content_type)
    truncated = len(body) > max_bytes
    if truncated:
        body = body[:max_bytes]
    try:
        return body.decode(charset, errors="replace"), content_type, final_url, truncated
    except LookupError:
        return body.decode("utf-8", errors="replace"), content_type, final_url, truncated


def _request_public_url_once(
    url: str,
    max_bytes: int,
) -> tuple[int, str, http.client.HTTPMessage, bytes]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_public_addresses(host, port)
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,text/plain",
        "Host": host if parsed.port is None else f"{host}:{port}",
        "Connection": "close",
    }
    last_error: OSError | None = None
    for address in addresses:
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _PinnedHTTPSConnection(host, port, address, timeout=DEFAULT_TIMEOUT_SECONDS)
        else:
            connection = _PinnedHTTPConnection(host, port, address, timeout=DEFAULT_TIMEOUT_SECONDS)
        try:
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            body = response.read(max_bytes)
            return response.status, response.reason, response.headers, body
        except OSError as exc:
            last_error = exc
        finally:
            connection.close()
    if isinstance(last_error, TimeoutError):
        raise WebError("WEB_TIMEOUT", "网页请求超时。")
    raise WebError("WEB_NETWORK_ERROR", "无法连接目标网站。")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, **kwargs: Any) -> None:
        super().__init__(host, port, **kwargs)
        self._validated_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, **kwargs: Any) -> None:
        super().__init__(host, port, context=ssl.create_default_context(), **kwargs)
        self._validated_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError("WEB_DNS_ERROR", "目标网站域名解析失败。") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    if not addresses or any(not ip_address(address).is_global for address in addresses):
        raise ValueError("出于安全考虑，不允许读取本机或私有网络地址。")
    return addresses


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip()
    return "utf-8"


def _normalize_result_href(href: str) -> str:
    href = html.unescape(href.strip())
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.bing.com" + href
    parsed = urlparse(href)
    if _is_bing_host(parsed.netloc) and parsed.path.startswith("/ck/"):
        target = _decode_bing_redirect_target(parsed)
        if target:
            href = target
    return href


def _looks_like_result_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    return not _is_bing_host(host)


def _is_bing_host(host: str) -> bool:
    normalized = host.lower()
    return normalized == "bing.com" or normalized.endswith(".bing.com")


def _decode_bing_redirect_target(parsed_url: Any) -> str:
    raw_target = parse_qs(parsed_url.query).get("u", [""])[0]
    if not raw_target:
        return ""
    raw_target = unquote(raw_target)
    if raw_target.startswith(("http://", "https://")):
        return raw_target
    encoded = raw_target[2:] if raw_target.startswith("a1") else raw_target
    padding = "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""
    return decoded if decoded.startswith(("http://", "https://")) else ""


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for item in results:
        key = item.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _validate_public_http_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url 必须是完整的 http 或 https 地址。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url 不允许包含用户名或密码。")
    host = parsed.hostname or ""
    if _is_blocked_host(host):
        raise ValueError("出于安全考虑，不允许读取本机或私有网络地址。")
    _resolve_public_addresses(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    return url


def _is_blocked_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized in {"localhost"} or normalized.endswith(".localhost"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串。")
    return value


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("数值参数必须是整数。")
    if value < minimum or value > maximum:
        raise ValueError(f"数值参数必须在 {minimum}-{maximum} 之间。")
    return value


def _normalize_space(value: str) -> str:
    lines = [" ".join(line.split()) for line in html.unescape(value).splitlines()]
    return "\n".join(line for line in lines if line)
