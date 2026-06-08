from __future__ import annotations

from app.agent.mcp.web_search_server import (
    BingSearchParser,
    search_web,
    _normalize_result_href,
    _validate_public_http_url,
    handle_message,
)


def test_bing_result_href_is_unwrapped() -> None:
    href = (
        "https://www.bing.com/ck/a?u="
        "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9kb2NzP2E9MQ"
    )

    assert _normalize_result_href(href) == "https://example.com/docs?a=1"


def test_bing_search_parser_extracts_result() -> None:
    parser = BingSearchParser()

    parser.feed(
        """
        <html>
          <ol>
            <li class="b_algo">
              <h2><a href="https://example.com">Example</a></h2>
              <div><p>Example snippet</p></div>
            </li>
          </ol>
        </html>
        """
    )

    assert len(parser.results) == 1
    assert parser.results[0].title == "Example"
    assert parser.results[0].url == "https://example.com"
    assert parser.results[0].snippet == "Example snippet"


def test_bing_search_uses_bing_source_and_dedupes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_read_url_text(url: str, max_bytes: int) -> str:
        assert url.startswith("https://www.bing.com/search?")
        assert max_bytes == 512_000
        return """
        <html>
          <li class="b_algo"><h2><a href="https://example.com">One</a></h2><p>First</p></li>
          <li class="b_algo"><h2><a href="https://example.com/">Dup</a></h2><p>Duplicate</p></li>
          <li class="b_algo"><h2><a href="https://www.bing.com/search?q=ad">Bing</a></h2><p>Skip</p></li>
          <li class="b_algo"><h2><a href="https://example.org">Two</a></h2><p>Second</p></li>
        </html>
        """

    monkeypatch.setattr("app.agent.mcp.web_search_server._read_url_text", fake_read_url_text)

    payload = search_web("sakura", max_results=5)

    assert payload["source"] == "Bing"
    assert [item["url"] for item in payload["results"]] == [
        "https://example.com",
        "https://example.org",
    ]


def test_fetch_url_blocks_local_network_addresses() -> None:
    for url in [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://192.168.1.1",
        "file:///C:/Users/test.txt",
    ]:
        try:
            _validate_public_http_url(url)
        except ValueError:
            continue
        raise AssertionError(f"should reject {url}")


def test_tools_list_response_contains_web_search_tools() -> None:
    response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {"web_search", "fetch_url"}
