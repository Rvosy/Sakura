"""Windows/browser settings journey with real plugins and isolated configuration.

Native window transport is replaced; plugin load/save/actions and model HTTP probes
run through the production Core boundary. Never touches the user's live settings.
"""
import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.core_host.provider_settings import ProviderSettingsBoundary
from app.config.model_references import ModelReferenceRepository, OPENAI_SERVICE
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.runtime_roots import RuntimeRoots, DistributionPaths

SCRIPT = r"""
import { createPluginSettingsFeature } from './plugin-settings.js';
import { createProviderSettingsFeature } from './provider-settings.js';
import { enhanceSelect, refreshSelect, closeSelects, focusSelect } from './select-control.js';
let feature, models;
function showPage(id) {
  document.querySelectorAll('.settings-page').forEach(p=>{p.classList.toggle('is-active',p.id==='page-'+id);p.hidden=p.id!=='page-'+id;});
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('is-active',b.dataset.page===id));
  document.getElementById('pageTitle').textContent = document.querySelector(`[data-page="${id}"] .nav-item-label`)?.textContent || id;
  feature?.onPageChanged(id);
}
function dirty(){document.getElementById('applyButton').disabled=!(feature?.isDirty()||models?.isDirty());}
feature=createPluginSettingsFeature({document,window,invoke:window.nativeInvoke,onDirty:dirty,onError:message=>{window.lastError=message;},notify:()=>{},confirmAction:async()=>true,enhanceSelect,refreshSelect,closeSelects,focusSelect,replayMotion:()=>{},getVoiceController:()=>null,removeOverlayAfterExit:async e=>e.remove(),showPage,isCharacterTransitioning:()=>false,hasPendingCharacterSelection:()=>false,onModelCatalogChanged:()=>models?.refreshChoices()});
models=createProviderSettingsFeature({document,invoke:window.nativeInvoke,onDirty:dirty,onError:e=>{throw e;},enhanceSelect,getProviderCatalog:()=>feature.providerCatalog()});
feature.initialize(await window.nativeInvoke('settings_plugins_get')); await models.initialize();
window.feature=feature; window.models=models; window.showPage=showPage;
document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>showPage(b.dataset.page));
const runtimePluginController=feature,runtimeProviderFeature=models;
const runtimeAsrController=null,runtimeAppearanceController=null,runtimeChatTimingController=null,runtimeBubbleAutoHideController=null,runtimeAutostartController=null,runtimeToolsController=null,runtimeVoiceController=null,runtimeCharacterFeature=null;
const refreshRuntimeVoiceCurrent=async()=>{};
/* SAVE_SETTINGS */
document.getElementById('applyButton').onclick=async()=>{try {await saveRuntimeSettings();dirty();}catch(e){window.lastError=String(e);}};
showPage('providers');document.body.dataset.ready='true';
"""


def run():
    requests = []
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path.endswith('/models'):
                requests.append((self.path, self.headers.get('Authorization')))
                body = json.dumps({'data': [{'id': 'fixture-model'}, {'id': 'discovered-model'}, {'id': 'unchecked-model'}]}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
            else: super().do_GET()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    output = ROOT / 'temp/settings-contributions-qa'; output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='sakura-contributions-') as temporary, sync_playwright() as pw:
            roots = RuntimeRoots(Path(temporary)/'distribution', Path(temporary)/'user')
            roots.user_root.mkdir()
            for name in ('sakura_assistant', 'sakura_model_openai_compatible', 'sakura_screen_awareness'):
                shutil.copytree(ROOT/'plugins/builtin'/name, roots.distribution_root/'plugins/builtin'/name, ignore=shutil.ignore_patterns('__pycache__'))
            config = roots.user_root/'data/plugins'/OPENAI_SERVICE/'config.json'; config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'profiles':[{'profileId':'fixture','label':'测试服务','base_url':origin+'/v1','api_key':'fixture-saved-key','timeout_seconds':60,'models':[{'modelId':'fixture-model','contextWindowTokens':96000}]}]}))
            ModelReferenceRepository(roots.user_root).save({'chat':{'serviceKey':OPENAI_SERVICE,'profileId':'fixture','modelId':'fixture-model'},'vision_chat':{'serviceKey':'','profileId':'','modelId':''}})
            deps = DistributionPaths(roots.distribution_root).plugin_dependency_root_for(OPENAI_SERVICE)
            deps.mkdir(parents=True)
            for name in ("openai", "httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi", "pydantic", "pydantic_core", "typing_extensions", "annotated_types", "typing_inspection", "jiter", "distro", "tqdm", "socksio", "colorama"):
                spec=importlib.util.find_spec(name)
                if spec is None: continue
                source=Path(next(iter(spec.submodule_search_locations))) if spec.submodule_search_locations else Path(spec.origin)
                if source.is_dir(): shutil.copytree(source,deps/source.name,ignore=shutil.ignore_patterns('__pycache__'))
                else: shutil.copy2(source,deps/source.name)
            (deps/'.sakura-dependencies.json').write_text(json.dumps({'schemaVersion':1,'kind':'requirements.txt','python':f'{sys.version_info.major}.{sys.version_info.minor}'}))
            app = PluginApplicationHost(roots, 'journey', ToolRegistry())
            try:
                app.start(); assert app.wait_until_loaded(timeout=10)
                boundary = PluginSettingsBoundary('journey', 'a'*32, roots, application_provider=lambda:app)
                # Same neutral model boundary used by the desktop transport.
                model_boundary = ProviderSettingsBoundary('journey', 'a'*32, roots.user_root, plugin_application_provider=lambda:app)
                writes = []
                def invoke(command, params=None):
                    params = params or {}
                    if command == 'settings_plugins_get':
                        value=boundary.snapshot(); return {**value,'windowGeneration':1,'coreGenerationId':'journey'}
                    if command == 'settings_plugins_action': return boundary.action(params)
                    if command == 'settings_plugins_save': writes.append(params); return boundary.save(params)
                    if command == 'settings_provider_model_get': return {**model_boundary._snapshot(),'window_generation':1,'core_generation_id':'journey'}
                    if command == 'settings_provider_model_save': return model_boundary._save(params['draft'])
                    raise ValueError(command)
                snapshot=invoke('settings_plugins_get')
                assert all(p['state']=='active' for p in snapshot['plugins']), [(p['pluginId'],p['reasonCode']) for p in snapshot['plugins']]
                browser=pw.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL') or ('msedge' if sys.platform=='win32' else None))
                page=browser.new_page(viewport={'width':1100,'height':850}); errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.expose_function('nativeInvoke',invoke)
                production=(ROOT/'desktop/frontend/settings/settings.js').read_text(encoding='utf-8')
                save_source='async function saveRuntimeSettings('+production.split('async function saveRuntimeSettings(',1)[1].split('\nfunction collectThemeSettings',1)[0]
                page.route('**/settings/settings.js',lambda route:route.fulfill(content_type='text/javascript',body=SCRIPT.replace('/* SAVE_SETTINGS */', save_source)))
                page.goto(origin+'/desktop/frontend/settings/');page.wait_for_selector('body[data-ready="true"]')
                expect(page.locator('[data-provider-field="api_key"]')).to_have_value('')
                expect(page.locator('[data-provider-field="api_key"]')).to_have_attribute('placeholder','留空保留原密钥')
                page.screenshot(animations="disabled", path=str(output/'providers.png'))
                original=config.read_bytes()
                page.locator('[data-provider-field="base_url"]').fill(origin+'/draft/v1')
                page.locator('[data-provider-field="api_key"]').fill('fixture-draft-key')
                page.get_by_role('button',name='获取模型列表',exact=True).click()
                expect(page.locator('.model-picker-dialog')).to_be_visible()
                page.screenshot(animations="disabled", path=str(output/'discovery.png'))
                assert config.read_bytes()==original and not writes
                assert requests[-1]==('/draft/v1/models','Bearer fixture-draft-key')
                # A same-Core reload withdraws the old picker and its probe scope,
                # while preserving connection edits for the replacement component.
                app.reload_plugin(OPENAI_SERVICE)
                page.evaluate('feature.refreshCurrent()')
                expect(page.locator('.model-picker-dialog')).to_have_count(0)
                expect(page.locator('[data-provider-field="api_key"]')).to_have_value('fixture-draft-key')
                expect(page.locator('[data-provider-field="base_url"]')).to_have_value(origin+'/draft/v1')
                page.get_by_role('button',name='获取模型列表',exact=True).click()
                expect(page.locator('.model-picker-dialog')).to_be_visible()
                page.locator('.model-picker-dialog input[value="unchecked-model"]').uncheck()
                page.locator('.model-picker-dialog').get_by_role('button',name='添加',exact=True).click()
                expect(page.locator('.model-chip').filter(has_text='discovered-model')).to_be_visible()
                assert config.read_bytes()==original and not writes
                page.get_by_placeholder('手动添加模型 ID').fill('manual-model')
                page.locator('.admin-detail').get_by_role('button',name='添加',exact=True).click()
                expect(page.locator('.model-chip').filter(has_text='manual-model')).to_be_visible()
                page.evaluate("showPage('model')")
                page.locator('#page-model summary').click()
                expect(page.locator('[data-plugin-field="contextWindowTokens"]')).to_have_value('96000')
                expect(page.locator('[data-plugin-field="top_p"]')).to_have_value('1')
                expect(page.locator('[data-plugin-field="max_tokens"]')).to_have_value('2048')
                expect(page.locator('[data-slot-inherit="core:vision_chat"]')).to_be_checked()
                page.screenshot(animations="disabled", path=str(output/'model.png'))
                page.evaluate("showPage('interaction')")
                expect(page.get_by_text('最短搭话间隔',exact=True)).to_be_visible()
                page.screenshot(animations="disabled", path=str(output/'interaction.png'))
                page.locator('[data-plugin-field="checkIntervalMinutes"]').fill('25')
                page.locator('#applyButton').click()
                expect(page.locator('#applyButton')).to_be_disabled()
                assert not page.evaluate('window.lastError'), page.evaluate('window.lastError')
                saved=json.loads(config.read_text(encoding='utf-8'))['profiles'][0]
                assert saved['api_key']=='fixture-draft-key' and saved['base_url']==origin+'/draft/v1'
                assert [m['modelId'] for m in saved['models']]==['fixture-model','discovered-model','manual-model']
                assert saved['models'][0]['contextWindowTokens']==96000
                assert not errors, errors
                page.evaluate('feature.dispose();models.dispose()');browser.close()
            finally: app.close()
    finally: server.shutdown(); server.server_close()
    print('Settings contribution journey passed; screenshots:', output)

if __name__=='__main__': run()
