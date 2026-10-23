"""在隔离宿主中验证 Codex 登录、配置与公共模型调用。"""
from pathlib import Path
import shutil
import time

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.runtime_v4 import PluginRuntimeError
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.runtime_roots import RuntimeRoots


SERVICE = "sakura.model.codex"
FOUNDATION = "sakura.model.openai_compatible"
ROOT = Path(__file__).resolve().parents[2]
NETWORK_FIXTURE = '''
import base64
import json
import time
import httpx
import oauth
import transport
from urllib.parse import parse_qs

device_number = 0

def handler(request):
    global device_number
    path = request.url.path
    if path.endswith('/usercode'):
        device_number += 1
        return httpx.Response(200, json={'device_auth_id':f'device-{device_number}', 'user_code':'ABCD-EFGH', 'interval':1})
    if path.endswith('/deviceauth/token'):
        return httpx.Response(200, json={'authorization_code':json.loads(request.content)['device_auth_id'], 'code_verifier':'fixture-verifier'})
    if path.endswith('/oauth/token'):
        account = parse_qs(request.content.decode())['code'][0]
        claims = {'exp':time.time()+3600, 'https://api.openai.com/auth':{'chatgpt_account_id':account}}
        access = 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'
        return httpx.Response(200, json={'access_token':access, 'refresh_token':'private-refresh'})
    if path.endswith('/models'):
        return httpx.Response(200, json={'models':[{'slug':'fixture-model', 'display_name':'Fixture', 'context_window':200000, 'input_modalities':['text','image']}]})
    body = json.loads(request.content)
    account = request.headers['chatgpt-account-id']
    assert account in {'device-1', 'device-2'}
    assert body['input'][0]['content'][1]['image_url'].startswith('data:image/png')
    result = {'type':'response.completed', 'response':{'id':'resp-1', 'status':'completed', 'output':[
        {'type':'message','role':'assistant','content':[{'type':'output_text','text':'fixture reply '+account}]}],
        'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}}}
    return httpx.Response(200, headers={'content-type':'text/event-stream'}, text='data: '+json.dumps(result)+'\\n\\n')

def client(url, timeout):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
oauth.http_client = transport.http_client = client
'''


def test_login_discovery_and_model_request_through_real_plugin_host(tmp_path, monkeypatch, assistant_dependencies):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    roots.user_root.mkdir()
    shutil.copytree(ROOT / "plugins/builtin/sakura_model_openai_compatible",
                    roots.distribution_root / "plugins/builtin/sakura_model_openai_compatible",
                    ignore=shutil.ignore_patterns("__pycache__"))
    provider = roots.distribution_root / "plugins/builtin/codex"
    shutil.copytree(ROOT / "plugins/optional/sakura_codex", provider, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = provider / "plugin.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"), encoding="utf-8")
    (provider / "fixture_network.py").write_text(NETWORK_FIXTURE, encoding="utf-8")
    with (provider / "plugin.py").open("a", encoding="utf-8") as file:
        file.write("\nimport fixture_network\n")
    consumer = roots.distribution_root / "plugins/builtin/consumer"
    consumer.mkdir()
    (consumer / "plugin.yaml").write_text("api: 4\nid: fixture.consumer\nname: Consumer\nentry: plugin:Consumer\nenabled: true\nprovides: [fixture.consumer]\nrequires: [sakura.host.artifacts]\n", encoding="utf-8")
    (consumer / "plugin.py").write_text('''
from sakura_model import ModelClient
class Consumer:
    def setup(self, context):
        self.context = context
        context.provide('fixture.consumer', self, exports=('complete',))
    def complete(self, profile_id):
        client = ModelClient(self.context, {'serviceKey':'sakura.model.codex','profileId':profile_id,'modelId':'fixture-model'})
        try:
            return client.complete({'messages':[{'role':'user','content':[{'type':'text','text':'look'},{'type':'image','dataUrl':'data:image/png;base64,aGVsbG8='}]}]})
        finally:
            client.close()
''', encoding="utf-8")
    original = PluginDependencyRoots.verified_root
    monkeypatch.setattr(PluginDependencyRoots, "verified_root", lambda self, plugin_id, *args, **kwargs:
                        assistant_dependencies if plugin_id in {SERVICE, FOUNDATION} else original(self, plugin_id, *args, **kwargs))
    app = PluginApplicationHost(roots, "codex-test", ToolRegistry())
    def catalog():
        return next(provider for provider in app.model_catalog() if provider["serviceKey"] == SERVICE)

    def profile_values(result, identity):
        return next(p for p in result["values"]["profiles"] if p["profileId"] == identity)

    def operation(name, identity):
        request = {"operationRequest": {"profileId": identity, "operation": name, "requestId": name}}
        result = profile_values(app.settings_action(SERVICE, "account", "start", request), identity)
        deadline = time.monotonic() + 5
        while result["operationResult"]["state"] == "running":
            assert time.monotonic() < deadline
            time.sleep(.02)
            result = profile_values(app.settings_action(SERVICE, "account", "status", request), identity)
        assert result["operationResult"]["state"] == "completed", result
        return result
    try:
        app.start()
        assert app.wait_until_loaded(timeout=5)
        failed = [item for item in app.public_snapshot()["plugins"] if item["state"] != "active"]
        assert not failed, "\n".join(str(item) for item in failed)
        assert catalog()["profiles"] == []
        first = app.settings_action(SERVICE, "account", "add", {})["values"]["profiles"][-1]["profileId"]
        second = app.settings_action(SERVICE, "account", "add", {})["values"]["profiles"][-1]["profileId"]
        assert first != second
        logged_in = operation("login", first)
        assert logged_in["account"]["loggedIn"]
        assert "private-refresh" not in repr(app.settings_snapshot())
        assert [model["modelId"] for model in logged_in["models"]] == [
            model["modelId"] for model in catalog()["profiles"][0]["models"]]
        assert catalog()["profiles"][0]["models"][0]["modelId"] == "fixture-model"
        response = app.call_service("fixture.consumer", "complete", first)
        assert response["message"]["content"] == "fixture reply device-1"
        assert response["usage"]["total_tokens"] == 12
        operation("login", second)
        assert app.call_service("fixture.consumer", "complete", second)["message"]["content"] == "fixture reply device-2"
        app.settings_action(SERVICE, "account", "remove", {"operationRequest": {"profileId": first}})
        assert app.call_service("fixture.consumer", "complete", second)["message"]["content"] == "fixture reply device-2"

        # 基础插件停用时扩展随之失效，恢复后继续使用已保存的账号和目录。
        app.set_plugin_enabled(FOUNDATION, False)
        assert all(provider["serviceKey"] != SERVICE for provider in app.model_catalog())
        with pytest.raises(PluginRuntimeError):
            app.call_service("fixture.consumer", "complete", second)
        app.set_plugin_enabled(FOUNDATION, True)
        app.set_plugin_enabled(SERVICE, True)
        assert profile_values(app.settings_action(SERVICE, "account", "status", {}), second)["account"]["loggedIn"]
        assert catalog()["profiles"][0]["models"][0]["modelId"] == "fixture-model"
        assert app.call_service("fixture.consumer", "complete", second)["message"]["content"] == "fixture reply device-2"

        app.settings_action(SERVICE, "account", "logout", {"operationRequest": {"profileId": second}})
        with pytest.raises(PluginRuntimeError, match="请先登录 ChatGPT"):
            app.call_service("fixture.consumer", "complete", second)
        assert catalog()["profiles"][0]["models"][0]["modelId"] == "fixture-model"
    finally:
        app.close()
