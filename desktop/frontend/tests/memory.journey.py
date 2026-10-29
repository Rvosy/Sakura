"""用真实三表存储和宿主接口验证画像、关系、约定与经历的编辑和召回。"""
import functools
import http.server
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "app/plugin_sdk")]
from playwright.sync_api import expect, sync_playwright
from app.core_host.plugin_host_services import _SettingsHostService
from plugins.optional.sakura_mem0.plugin import SakuraMem0Runtime, PLUGIN_ID

SCRIPT = r"""
import { createPluginSettingsFeature } from './plugin-settings.js';
import { enhanceSelect, refreshSelect, closeSelects, focusSelect } from './select-control.js';
window.errors = [];
function showPage(id) {
  document.querySelectorAll('.settings-page').forEach(p => {
    p.classList.toggle('is-active', p.id === 'page-' + id); p.hidden = p.id !== 'page-' + id;
  });
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('is-active', b.dataset.page === id));
  document.getElementById('pageTitle').textContent = '记忆';
  window.feature?.onPageChanged(id);
}
window.feature = createPluginSettingsFeature({document, window, invoke: window.nativeInvoke,
  onDirty() {}, onError(error) { if (error) window.errors.push(String(error)); }, notify() {},
  confirmAction: async () => true, enhanceSelect, refreshSelect, closeSelects, focusSelect,
  replayMotion() {}, removeOverlayAfterExit: async e => e.remove(), showPage,
  isCharacterTransitioning: () => false, hasPendingCharacterSelection: () => false });
feature.initialize(await window.nativeInvoke('settings_plugins_get'));
showPage('memory'); document.body.dataset.ready = 'true';
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def settings_bridge(runtime):
    callbacks = {}

    def handle(callback):
        key = 'cb_' + uuid.uuid4().hex
        callbacks[key] = callback
        return key

    service = _SettingsHostService(lambda key, _shape, *args: callbacks[key](*args))
    for descriptor, load, save, actions in (
        (runtime.memory_management_descriptor(), runtime.load_management_settings, None,
         {'updateNow': runtime.update_now}),
        (runtime.memory_optimization_descriptor(), runtime.load_optimization_settings, None,
         {'mergeDuplicates': runtime.merge_duplicates}),
        (runtime.memory_index_descriptor(), runtime.load_index_settings, None,
         {'rebuildIndex': runtime.rebuild_index}),
        (runtime.text_import_descriptor(), lambda: {}, lambda _values: {'applicationState': 'applied'},
         {key: (lambda values, action=key: runtime.text_import_action(action, values))
          for key in ('extract', 'status', 'save', 'cancel')}),
        (runtime.backfill_descriptor(), runtime.load_backfill_settings, runtime.save_backfill_settings,
         {'backfill': runtime.start_backfill, 'cancelBackfill': runtime.cancel_backfill}),
    ):
        service.call('register', [PLUGIN_ID, descriptor, {
            'load': handle(load), 'save': handle(save) if save else None,
            'actions': {key: handle(action) for key, action in actions.items()},
        }])
        service.register_surface(PLUGIN_ID, descriptor['sectionId'], 'memory')
    for collection_id in ('profile', 'agreements', 'experiences', 'knowledge', 'memories'):
        service.register_collection(PLUGIN_ID, 'memory_management', runtime.memory_collection_descriptor(collection_id), {
            'query': handle(lambda request, cid=collection_id: runtime.query_collection(request, collection_id=cid)),
            'create': handle(runtime.create_collection_item), 'update': handle(runtime.update_collection_item),
            'delete': handle(runtime.delete_collection_item),
            **({'transfer': handle(runtime.transfer_collection)} if collection_id == 'memories' else {}),
        })

    def invoke(_source, command, params=None):
        if command == 'settings_plugins_get':
            return {'schemaVersion': 1, 'state': 'ready', 'reasonCode': 'READY', 'windowGeneration': 1,
                    'coreGenerationId': 'memory-journey', 'characterId': 'sakura', 'plugins': [{
                        'installId': 'pi_bundled_73616b7572615f6d656d30', 'pluginId': PLUGIN_ID,
                        'name': '长期记忆', 'version': '2.0.0', 'enabled': True, 'required': False,
                        'supported': True, 'source': 'bundled', 'canUninstall': False,
                        'provides': [], 'requires': [], 'missingServices': [], 'state': 'active',
                        'reasonCode': 'ACTIVE', 'sections': service.sections_for_plugin(PLUGIN_ID),
                    }]}
        if command == 'settings_plugins_collection':
            return service.collection(params['operation'], params['pluginId'], params['sectionId'],
                                      params['collectionId'], params['payload'])
        if command == 'settings_plugins_action':
            return service.action(params['pluginId'], params['sectionId'], params['actionId'], params['values'])[1]
        raise ValueError(command)

    return invoke


def run():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = ROOT / 'temp/memory-page-qa'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='sakura-memory-') as directory, sync_playwright() as playwright:
            release_text_model = threading.Event()
            class TextModel:
                def complete(self, request, **_options):
                    assert release_text_model.wait(3)
                    document = json.loads(request['messages'][1]['content'])['messages'][0]
                    return {'message': {'content': json.dumps({'operations': [
                        {'op': 'add', 'subject': '用户', 'subject_kind': 'person', 'predicate': '职业',
                         'content': '做软件开发', 'layer': 'core_profile',
                         'evidence': [{'entry_id': document['entry_id'], 'quote': '我是软件开发者。'}]},
                        {'op': 'add', 'subject': '小雨', 'subject_kind': 'person', 'predicate': '兴趣',
                         'content': '喜欢园艺', 'layer': 'semantic',
                         'evidence': [{'entry_id': document['entry_id'], 'quote': '小雨喜欢园艺。'}]},
                    ]}, ensure_ascii=False)}, 'finishReason': 'stop'}

                def close(self):
                    pass

            runtime = SakuraMem0Runtime(Path(directory), 'sakura', model_client_factory=lambda _: TextModel(),
                model_resolver=lambda _: {'serviceKey': 'model', 'profileId': 'p', 'modelId': 'm'})
            try:
                store = runtime._boundary.memory_store
                facts = [
                    ('用户', 'person', '称呼', '叫我小林就好。', 'core_profile'),
                    ('用户', 'person', '饮食偏好', '我不喝咖啡，平时喜欢喝茶。', 'core_profile'),
                    ('用户', 'person', '兴趣', '周末喜欢散步和拍照。', 'core_profile'),
                    ('小樱', 'person', '职业', '小樱是插画师。', 'semantic'),
                    ('Sakura', 'project', '进展', '正在改造桌宠的记忆功能。', 'semantic'),
                    ('用户', 'person', '交流方式', '我说累了的时候，先听我说，不用马上给建议。', 'procedural'),
                    ('第一次一起看日出', 'event', '经过', '周末我们聊起了海边的日出，约好下次还要一起看。', 'episodic'),
                ]
                for subject, kind, predicate, content, layer in facts:
                    store.create_memory({'subject': subject, 'subject_kind': kind, 'predicate': predicate,
                                         'content': content, 'layer': layer, 'source': 'manual', 'pinned': False})
                store.create_memory({'subject': '用户', 'subject_kind': 'person', 'predicate': '朋友',
                                     'target': '小樱', 'target_kind': 'person', 'content': '小樱是认识多年的朋友。'})
                browser = playwright.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL') or 'msedge')
                page = browser.new_page(viewport={'width': 1100, 'height': 820}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.expose_binding('nativeInvoke', settings_bridge(runtime))
                page.route('**/settings/settings.js', lambda route: route.fulfill(content_type='text/javascript', body=SCRIPT))
                page.goto(f'http://127.0.0.1:{server.server_port}/desktop/frontend/settings/')
                page.wait_for_selector('body[data-ready="true"]')
                expect(page.locator('.memory-entity-group').filter(has_text='关于你')).to_contain_text('我不喝咖啡')
                page.screenshot(animations='disabled', path=str(output / 'profile.png'))
                page.locator('.memory-record-card').filter(has_text='饮食偏好').get_by_role('button').click()
                dialog = page.get_by_role('dialog')
                page.screenshot(animations='disabled', path=str(output / 'editor.png'))
                dialog.get_by_role('textbox', name='内容', exact=False).fill('我喜欢喝绿茶，不喝咖啡。')
                dialog.get_by_role('button', name='保存修改').click()
                expect(dialog).to_have_count(0)
                assert store.search_memory({'subject': '用户', 'predicate': '饮食偏好'})['memories'][0]['content'] == '我喜欢喝绿茶，不喝咖啡。'
                context = runtime.context({'character_id': 'sakura', 'current_input': '今天怎么样'})
                assert any('绿茶' in item['content'] for item in context)
                assert any('先听我说' in item['content'] for item in context)

                page.get_by_role('tab', name='人与事', exact=True).click()
                expect(page.locator('.memory-entity-group').filter(has_text='小樱')).to_contain_text('插画师')
                expect(page.locator('.memory-entity-group').filter(has_text='Sakura')).to_contain_text('桌宠的记忆功能')
                page.get_by_role('button', name='新增记忆', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_role('textbox', name='内容', exact=False).fill('小雨是一起做项目的同事。')
                dialog.get_by_role('textbox', name=re.compile('^关于谁或什么')).fill('用户')
                dialog.get_by_role('textbox', name=re.compile('^信息项或联系')).fill('同事')
                dialog.get_by_role('textbox', name=re.compile('^另一方')).fill('小雨')
                dialog.get_by_role('button', name='新增记忆', exact=True).click()
                expect(dialog).to_have_count(0)
                relation, = store.search_memory({'subject': '用户', 'target': '小雨'})['memories']
                assert relation['predicate'] == '同事'
                page.screenshot(animations='disabled', path=str(output / 'knowledge.png'))
                page.get_by_role('tab', name='相处约定', exact=True).click()
                expect(page.locator('.memory-record-card')).to_contain_text('先听我说')
                page.get_by_role('tab', name='共同经历', exact=True).click()
                expect(page.locator('.memory-record-card')).to_contain_text('第一次一起看日出')
                page.screenshot(animations='disabled', path=str(output / 'experiences.png'))
                page.get_by_role('button', name='高级设置', exact=True).click()
                expect(page.locator('.memory-maintenance')).to_contain_text('修复记忆检索')
                text_import = page.locator('.memory-text-import')
                try:
                    expect(text_import).to_be_visible()
                except AssertionError:
                    page.screenshot(animations='disabled', path=str(output / 'text-import-error.png'))
                    print(page.locator('.memory-maintenance').inner_text())
                    print(errors, page.evaluate('window.errors'))
                    raise
                text_import.get_by_role('textbox', name='要提取的文本').fill('我是软件开发者。小雨喜欢园艺。')
                page.get_by_role('tab', name='个人画像', exact=True).click()
                page.get_by_role('button', name='高级设置', exact=True).click()
                expect(text_import.get_by_role('textbox', name='要提取的文本')).to_have_value('我是软件开发者。小雨喜欢园艺。')
                text_import.get_by_role('button', name='提取候选', exact=True).click()
                page.evaluate('feature.renderMemorySurface()')
                release_text_model.set()
                preview = page.locator('.memory-text-import__candidate')
                expect(preview).to_have_count(2)
                assert not store.search_memory({'predicate': '职业', 'subject': '用户'})['memories']
                preview.nth(0).get_by_role('textbox', name='记忆内容').fill('我做桌面软件开发。')
                preview.nth(1).get_by_role('checkbox').uncheck()
                preview.nth(0).scroll_into_view_if_needed()
                page.screenshot(animations='disabled', path=str(output / 'text-import.png'))
                page.set_viewport_size({'width': 760, 'height': 680})
                assert page.locator('.page-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                page.set_viewport_size({'width': 1100, 'height': 820})
                text_import.get_by_role('button', name='保存所选记忆', exact=True).click()
                expect(text_import.get_by_role('status')).to_have_text('已保存 1 条记忆。')
                page.screenshot(animations='disabled', path=str(output / 'advanced.png'))
                page.get_by_role('tab', name='个人画像', exact=True).click()
                expect(page.locator('.memory-record-card').filter(has_text='职业')).to_contain_text('我做桌面软件开发。')
                imported = store.search_memory({'predicate': '职业', 'subject': '用户'})['memories']
                assert len(imported) == 1 and imported[0]['evidence'][0]['role'] == 'document'
                assert not store.search_memory({'predicate': '兴趣', 'subject': '小雨'})['memories']
                page.set_viewport_size({'width': 760, 'height': 680})
                page.screenshot(animations='disabled', path=str(output / 'profile-narrow.png'))
                assert page.locator('.page-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                assert errors == [], errors
                assert page.evaluate('window.errors') == []
                browser.close()
            finally:
                release_text_model.set()
                runtime.close()
    finally:
        server.shutdown()
        server.server_close()
    print('Memory browser journey passed')


if __name__ == '__main__':
    run()
