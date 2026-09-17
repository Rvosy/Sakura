from __future__ import annotations

import json

import httpx
import pytest

from plugins.builtin.sakura_web import search, web
from plugins.builtin.sakura_web.plugin import WebPlugin, _execute_handler


@pytest.mark.parametrize('provider,markup,title,url', [
    ('baidu', '<div><h3><a href="https://www.baidu.com/link?url=fixture"><em>作品</em>介绍</a></h3></div>', '作品介绍', 'https://www.baidu.com/link?url=fixture'),
])
def test_html_service_result_contract(monkeypatch, provider, markup, title, url):
    requests = []
    def read(target, max_bytes):
        requests.append(target)
        return markup
    monkeypatch.setattr(web, '_read_url_text', read)
    result = search.search('作品 角色', 3, {'provider': provider})
    assert result['results'] == [{'title': title, 'url': url, 'snippet': ''}]
    assert ('wd=' if provider == 'baidu' else 'q=') in requests[0]


@pytest.mark.parametrize('provider', ['baidu'])
def test_verification_page_is_not_empty_search(monkeypatch, provider):
    monkeypatch.setattr(web, '_read_url_text', lambda *a, **k: '<title>请完成验证</title>')
    with pytest.raises(web.WebError, match='未返回可识别'):
        search.search('query', 5, {'provider': provider})


def tavily_transport(monkeypatch, status, payload):
    requests = []
    client = httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(status, stream=httpx.ByteStream(json.dumps(payload).encode()))
    def make_client(**kwargs):
        return client(transport=httpx.MockTransport(respond), trust_env=False)
    monkeypatch.setattr(httpx, 'Client', make_client)
    return requests


def test_tavily_authorization_depth_and_result_mapping(monkeypatch):
    requests = tavily_transport(monkeypatch, 200, {'results': [{'title': 'Title', 'url': 'https://example.com/', 'content': 'Summary'}]})
    result = search.search('作品', 3, {'provider': 'tavily', 'tavily_api_key': 'test-key', 'tavily_depth': 'advanced'})
    assert result['results'] == [{'title': 'Title', 'url': 'https://example.com/', 'snippet': 'Summary'}]
    assert requests[0].headers['Authorization'] == 'Bearer test-key'
    assert str(requests[0].url) == 'https://api.tavily.com/search'
    assert json.loads(requests[0].content) == {'query': '作品', 'max_results': 3, 'search_depth': 'advanced', 'include_answer': False, 'include_raw_content': False, 'include_published_date': True}


@pytest.mark.parametrize('status,payload,code', [
    (401, {'error': 'test-key'}, 'WEB_AUTH_ERROR'),
    (429, {'error': 'test-key'}, 'WEB_RATE_LIMIT'),
    (500, {'error': 'test-key'}, 'WEB_HTTP_ERROR'),
    (200, {'bad': 'test-key'}, 'WEB_SEARCH_RESPONSE_INVALID'),
    (200, {'results': [None]}, 'WEB_SEARCH_RESPONSE_INVALID'),
])
def test_tavily_errors_do_not_echo_response_or_key(monkeypatch, status, payload, code):
    tavily_transport(monkeypatch, status, payload)
    result = _execute_handler('web_search', lambda: {'provider': 'tavily', 'tavily_api_key': 'test-key'})({'query': 'query'})
    assert result['reasonCode'] == code
    assert 'test-key' not in str(result)


def test_missing_tavily_key_does_not_send_request(monkeypatch):
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('must not connect'))
    result = _execute_handler('web_search', lambda: {'provider': 'tavily'})({'query': 'query'})
    assert result['reasonCode'] == 'WEB_API_KEY_MISSING'


def test_draft_test_does_not_save_configuration(monkeypatch):
    plugin = WebPlugin()
    seen = []
    def run(query, count, config):
        seen.append(config)
        return {'results': [{'title': 'Result', 'url': 'https://example.com', 'snippet': 'Summary'}]}
    monkeypatch.setattr(search, 'search', run)
    result = plugin.test_search({'provider': 'bing', 'test_query': 'query'})
    plugin.test_thread.join(2)
    assert seen[0]['provider'] == 'bing'
    assert not plugin.test_thread.is_alive()
    assert result == {}
    assert 'Result' in plugin.test_result
    # No context exists: a draft test must not access or update persistent config.


def test_background_search_returns_before_network_and_ignores_closed_worker(monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(query, count, config):
        calls.append(query)
        entered.set()
        assert release.wait(2)
        return {'results': [{'title': 'Late result', 'url': 'https://example.com'}]}
    monkeypatch.setattr(search, 'search', run)
    plugin = WebPlugin()
    try:
        assert plugin.test_search({'provider': 'bing', 'test_query': 'first'}) == {}
        assert entered.wait(1)
        assert plugin.test_status['state'] == 'working'
        assert plugin.test_search({'provider': 'bing', 'test_query': 'duplicate'}) == {}
        plugin.close()
    finally:
        release.set()
        plugin.test_thread.join(2)
    assert calls == ['first']
    assert plugin.test_result == ''



def test_removed_google_selection_falls_back_without_losing_tavily_key():
    result = search.configuration({'provider': 'google', 'tavily_api_key': 'fixture-key', 'tavily_depth': 'advanced'})
    assert result == {'provider': 'baidu', 'tavily_api_key': 'fixture-key', 'tavily_depth': 'advanced'}
    assert set(search.PROVIDERS) == {'baidu', 'bing', 'tavily'}


def test_baidu_summary_comes_from_result_not_sidebar(monkeypatch):
    monkeypatch.setattr(web, '_read_url_text', lambda *a, **k: '<aside>无关热搜</aside><div class="result c-container"><h3><a href="https://example.com">作品</a></h3><div>女主角有白色长发。</div></div>')
    result = search.search('作品', 5, {'provider': 'baidu'})
    assert result['results'][0]['snippet'] == '女主角有白色长发。'
    assert result['retrieved_at']
    assert 'published_date' not in result['results'][0]


def test_tavily_extract_routes_fetch_tool_and_marks_truncation(monkeypatch):
    requests = tavily_transport(monkeypatch, 200, {'results': [{'url': 'https://example.com/character', 'raw_content': '角色正文' * 500}]})
    result = _execute_handler('fetch_url', lambda: {'provider': 'tavily', 'tavily_api_key': 'test-key', 'tavily_depth': 'advanced'})({'url': 'https://example.com/character', 'max_chars': 500})
    assert not result.get('isError')
    assert result['source'] == 'Tavily'
    assert len(result['text']) == 500 and result['truncated']
    assert result['retrieved_at']
    assert str(requests[0].url) == 'https://api.tavily.com/extract'
    assert json.loads(requests[0].content) == {'urls': ['https://example.com/character'], 'extract_depth': 'advanced', 'format': 'text'}


@pytest.mark.parametrize('payload,code', [
    ({'results': [], 'failed_results': [{'url': 'https://example.com', 'error': 'test-key'}]}, 'WEB_EXTRACT_FAILED'),
    ({'results': [{'url': 'https://example.com', 'raw_content': None}]}, 'WEB_EXTRACT_RESPONSE_INVALID'),
])
def test_tavily_extract_failures_are_not_empty_success(monkeypatch, payload, code):
    tavily_transport(monkeypatch, 200, payload)
    result = _execute_handler('fetch_url', lambda: {'provider': 'tavily', 'tavily_api_key': 'test-key'})({'url': 'https://example.com'})
    assert result['reasonCode'] == code
    assert 'test-key' not in str(result)


def test_tavily_extract_rejects_local_address_before_api_request(monkeypatch):
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('must not connect'))
    result = _execute_handler('fetch_url', lambda: {'provider': 'tavily', 'tavily_api_key': 'test-key'})({'url': 'http://127.0.0.1/'})
    assert result['reasonCode'] == 'WEB_INVALID_REQUEST'


def test_search_metadata_and_fuller_content_reach_model_tool_message(monkeypatch):
    from sakura_assistant.agent.runtime import _build_tool_role_message, NativeToolCall
    from app.plugin_sdk.sakura_tools import ToolExecutionResult
    content = '来源内容' * 1200
    tavily_transport(monkeypatch, 200, {'results': [{'title': '作品', 'url': 'https://example.com', 'content': content, 'published_date': '2026-04-24'}]})
    result = search.search('作品', 1, {'provider': 'tavily', 'tavily_api_key': 'test-key'})
    assert result['results'][0]['published_date'] == '2026-04-24'
    assert result['results'][0]['snippet'] == content
    call = NativeToolCall(id='search-1', name='web__web_search', arguments={'query': '作品'})
    message = _build_tool_role_message(call, ToolExecutionResult(tool_name='web__web_search', success=True, content=result))
    assert message['role'] == 'tool' and message['tool_call_id'] == 'search-1'
    assert content in message['content']
    assert result['retrieved_at'] in message['content']
