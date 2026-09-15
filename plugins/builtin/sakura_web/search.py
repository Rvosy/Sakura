from __future__ import annotations

import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse

try:
    from . import web
except ImportError:
    import web

PROVIDERS = {"baidu": "百度", "bing": "Bing", "tavily": "Tavily"}
DEFAULTS = {"provider": "baidu", "tavily_api_key": "", "tavily_depth": "basic"}


def configuration(values):
    result = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    if result["provider"] == "google":
        result["provider"] = "baidu"
    if result["provider"] not in PROVIDERS:
        raise ValueError("请选择有效的查询服务。")
    if result["tavily_depth"] not in {"basic", "advanced"}:
        raise ValueError("请选择有效的搜索深度。")
    key = result["tavily_api_key"]
    if not isinstance(key, str) or len(key) > 512 or any(c in key for c in '\r\n'):
        raise ValueError("API Key 格式无效。")
    result["tavily_api_key"] = key.strip()
    return result


def search(query, max_results, values):
    result = _search(query, max_results, values)
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _search(query, max_results, values):
    config = configuration(values)
    provider = config["provider"]
    if provider == "bing":
        return web.search_web(query, max_results)
    if provider == "tavily":
        return _tavily(query, max_results, config)
    raw = web._read_url_text("https://www.baidu.com/s?" + urlencode({"wd": query}), max_bytes=2_000_000)
    parser = SearchPageParser(provider)
    parser.feed(raw)
    results = web._dedupe_results(parser.results)[:max_results]
    if not results:
        raise web.WebError("WEB_SEARCH_RESPONSE_INVALID", "搜索服务未返回可识别的结果，可能需要验证或页面格式已变化。")
    return {"query": query, "source": PROVIDERS[provider], "results": [vars(item) for item in results]}


class SearchPageParser(HTMLParser):
    """Read Baidu result headings and their nested links."""

    def __init__(self, provider):
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.results = []
        self.link = ""
        self.heading = None
        self.title = []
        self.href = ""
        self.skip = 0
        self.div_depth = 0
        self.result_depth = None
        self.result_start = 0
        self.result_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.skip += 1
        if self.skip:
            return
        if tag == "div":
            self.div_depth += 1
            if self.result_depth is None and "c-container" in attrs.get("class", "").split():
                self.result_depth = self.div_depth
                self.result_start = len(self.results)
                self.result_text = []
        if tag == "a":
            self.link = attrs.get("href", "")
            if self.heading:
                self.href = self.link
        if tag == "h3":
            self.heading = tag
            self.title = []
            self.href = self.link

    def handle_data(self, data):
        if self.result_depth is not None and not self.skip:
            self.result_text.append(data)
        if self.heading and not self.skip:
            self.title.append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        if tag == "div" and not self.skip:
            if self.result_depth == self.div_depth:
                snippet = web._normalize_space(" ".join(self.result_text))
                for index in range(self.result_start, len(self.results)):
                    item = self.results[index]
                    self.results[index] = web.SearchResult(item.title, item.url, snippet.removeprefix(item.title).strip()[:600])
                self.result_depth = None
            self.div_depth = max(0, self.div_depth - 1)
        if tag == self.heading:
            title = web._normalize_space("".join(self.title))
            href = self.href
            href = urljoin("https://www.baidu.com", href)
            parsed = urlparse(href)
            if title and parsed.scheme in {"http", "https"} and parsed.hostname:
                if not web._is_blocked_host(parsed.hostname) and parsed.path != "/baidu.php":
                    self.results.append(web.SearchResult(title[:500], href[:8192]))
            self.heading = None
        if tag == "a":
            self.link = ""


def _tavily_request(endpoint, parameters, config):
    import httpx

    key = config["tavily_api_key"]
    if not key:
        raise web.WebError("WEB_API_KEY_MISSING", "请先填写 Tavily API Key。")
    url = "https://api.tavily.com/" + endpoint
    try:
        with httpx.Client(proxy=web.proxy_for_url(url), trust_env=False, timeout=web.DEFAULT_TIMEOUT_SECONDS) as client:
            with client.stream("POST", url, headers={"Authorization": "Bearer " + key}, json=parameters) as response:
                if response.status_code in {401, 403}:
                    raise web.WebError("WEB_AUTH_ERROR", "Tavily 认证失败，请检查 API Key。")
                if response.status_code in {429, 432, 433}:
                    raise web.WebError("WEB_RATE_LIMIT", "Tavily 请求受限，请检查额度或稍后再试。")
                if response.status_code != 200:
                    raise web.WebError("WEB_HTTP_ERROR", f"Tavily 返回 HTTP {response.status_code}。")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > 2_000_000:
                        raise web.WebError("WEB_SEARCH_RESPONSE_INVALID", "Tavily 响应过大。")
                    body.extend(chunk)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError()
                return payload
    except httpx.TimeoutException:
        raise web.WebError("WEB_TIMEOUT", "Tavily 请求超时。") from None
    except httpx.HTTPError:
        raise web.WebError("WEB_NETWORK_ERROR", "无法连接 Tavily。") from None
    except ValueError:
        raise web.WebError("WEB_SEARCH_RESPONSE_INVALID", "Tavily 返回了无效的响应。") from None


def _tavily(query, max_results, config):
    payload = _tavily_request("search", {
        "query": query, "max_results": max_results, "search_depth": config["tavily_depth"],
        "include_answer": False, "include_raw_content": False, "include_published_date": True,
    }, config)
    try:
        items = payload["results"]
        if not isinstance(items, list):
            raise TypeError()
        results = []
        for item in items[:max_results]:
            if not all(isinstance(item.get(k), str) for k in ("title", "url", "content")):
                raise TypeError()
            web._validate_public_http_url(item["url"])
            result = {"title": item["title"][:500], "url": item["url"][:8192], "snippet": item["content"][:6000]}
            if len(item["content"]) > 6000:
                result["truncated"] = True
            published = item.get("published_date")
            if isinstance(published, str) and published.strip():
                result["published_date"] = published[:120]
            results.append(result)
        return {"query": query, "source": "Tavily", "results": results}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise web.WebError("WEB_SEARCH_RESPONSE_INVALID", "Tavily 返回了无效的搜索结果。") from None


def fetch(url, max_chars, values):
    config = configuration(values)
    if config["provider"] != "tavily":
        result = web.fetch_url(url, max_chars)
    else:
        url = web._validate_public_http_url(url)
        payload = _tavily_request("extract", {
            "urls": [url], "extract_depth": config["tavily_depth"], "format": "text",
        }, config)
        items = payload.get("results")
        if isinstance(items, list) and not items and payload.get("failed_results"):
            raise web.WebError("WEB_EXTRACT_FAILED", "Tavily 未能读取该网页。")
        try:
            if not isinstance(items, list) or len(items) != 1:
                raise ValueError()
            item = items[0]
            final_url = web._validate_public_http_url(item["url"])
            text = item["raw_content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
            title = item.get("title") or ""
            if not isinstance(title, str):
                raise ValueError()
            result = {"url": final_url, "content_type": "text/plain", "title": title[:500],
                      "text": text[:max_chars], "truncated": len(text) > max_chars, "links": [], "source": "Tavily"}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise web.WebError("WEB_EXTRACT_RESPONSE_INVALID", "Tavily 返回了无效的网页正文。") from None
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    return result
