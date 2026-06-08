from __future__ import annotations

import html
import json
import sys
import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


SERVER_NAME = "sakura-web-search"
SERVER_VERSION = "0.1.0"
DEFAULT_TIMEOUT_SECONDS = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


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


def main() -> int:
    try:
        _run_fastmcp_server()
        return 0
    except ImportError:
        # 测试环境或未安装 mcp 时保留轻量 JSON-RPC fallback，正式运行应使用 FastMCP。
        pass

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle_message(message)
        except Exception as exc:  # MCP Server 不能因为单条坏消息退出。
            response = _error_response(None, -32603, f"内部错误：{exc}")
        if response is not None:
            _write_message(response)
    return 0


def _run_fastmcp_server() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME, log_level="ERROR")

    @mcp.tool(
        name="web_search",
        description="搜索公开网页，并返回标题、链接和简短摘要。适合查询最新信息、资料来源和网页入口。",
        structured_output=True,
    )
    def web_search_tool(query: str, max_results: int = 5) -> dict[str, Any]:
        """搜索公开网页。"""

        return search_web(
            query=query,
            max_results=_clamp_int(max_results, default=5, minimum=1, maximum=10),
        )

    @mcp.tool(
        name="fetch_url",
        description="读取一个公开 http/https 网页，抽取标题、正文文本和页面链接。",
        structured_output=True,
    )
    def fetch_url_tool(url: str, max_chars: int = 6000) -> dict[str, Any]:
        """读取公开网页正文。"""

        return fetch_url(
            url=url,
            max_chars=_clamp_int(max_chars, default=6000, minimum=500, maximum=20000),
        )

    mcp.run("stdio")


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if request_id is None:
        return None
    if method == "initialize":
        requested_version = str(params.get("protocolVersion") or "2024-11-05")
        return _result_response(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result_response(request_id, {})
    if method == "tools/list":
        return _result_response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        return _handle_tool_call(request_id, params)
    if method == "resources/list":
        return _result_response(request_id, {"resources": []})
    if method == "prompts/list":
        return _result_response(request_id, {"prompts": []})
    return _error_response(request_id, -32601, f"不支持的方法：{method}")


def _handle_tool_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name") or "")
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    try:
        if name == "web_search":
            payload = search_web(
                query=_required_string(arguments, "query"),
                max_results=_clamp_int(arguments.get("max_results"), default=5, minimum=1, maximum=10),
            )
        elif name == "fetch_url":
            payload = fetch_url(
                url=_required_string(arguments, "url"),
                max_chars=_clamp_int(arguments.get("max_chars"), default=6000, minimum=500, maximum=20000),
            )
        else:
            return _error_response(request_id, -32602, f"未知工具：{name}")
    except Exception as exc:
        return _result_response(
            request_id,
            {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            },
        )
    return _tool_result_response(request_id, payload)


def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空。")

    url = "https://www.bing.com/search?" + urlencode({"q": query})
    html_text = _read_url_text(url, max_bytes=512_000)
    parser = BingSearchParser()
    parser.feed(html_text)
    results = _dedupe_results(parser.results)[:max_results]
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
    raw_text, content_type, final_url = _read_url_text_with_metadata(
        normalized_url,
        max_bytes=max(256_000, min(max_chars * 8, 1_500_000)),
    )
    if "html" in content_type.lower():
        parser = PageTextParser()
        parser.feed(raw_text)
        text = _normalize_space(parser.text)
        title = _normalize_space(parser.title)
        links = parser.links[:30]
    else:
        text = _normalize_space(raw_text)
        title = ""
        links = []
    return {
        "url": final_url,
        "content_type": content_type,
        "title": title,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if tag == "li" and "b_algo" in classes:
            self._result_depth = 1
            self._snippet_parts = []
            return
        if self._result_depth:
            self._result_depth += 1
        if self._result_depth and tag == "a":
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
        if tag == "a" and self._in_title_link:
            title = _normalize_space("".join(self._active_text))
            if title and _looks_like_result_url(self._active_href):
                self.results.append(SearchResult(title=title, url=self._active_href))
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
            if href.startswith(("http://", "https://")):
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
                self.links.append({"text": text[:120], "url": self._active_link})
            self._active_link = None
            self._active_link_text = []


def _read_url_text(url: str, max_bytes: int) -> str:
    text, _content_type, _final_url = _read_url_text_with_metadata(url, max_bytes)
    return text


def _read_url_text_with_metadata(url: str, max_bytes: int) -> tuple[str, str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,text/plain"})
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(max_bytes + 1)
            final_url = response.geturl()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络请求失败：{exc.reason}") from exc

    charset = _charset_from_content_type(content_type)
    if len(body) > max_bytes:
        body = body[:max_bytes]
    try:
        return body.decode(charset, errors="replace"), content_type, final_url
    except LookupError:
        return body.decode("utf-8", errors="replace"), content_type, final_url


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
    host = parsed.hostname or ""
    if _is_blocked_host(host):
        raise ValueError("出于安全考虑，不允许读取本机或私有网络地址。")
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


def _tool_result_response(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return _result_response(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": False,
        },
    )


def _result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
