"""Exercise Spine archives in the real Studio and RendererHost with isolated data.

Optional --components points at a tools.spine_preview prepared catalog.
"""
import argparse
import functools
import http.server
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from app.agent.tools import ToolRegistry
from app.config.character_resources import CharacterVisualResource
from app.core_host.character_studio import CharacterStudioBoundary
from app.core_host.plugin_application import PluginApplicationHost
from app.storage.runtime_roots import RuntimeRoots
from tools.spine_preview import export_components, prepare


class Handler(http.server.SimpleHTTPRequestHandler):
    assets = {}
    csp = json.loads((ROOT / 'desktop/src-tauri/tauri.conf.json').read_text(encoding="utf-8"))['app']['security']['csp']
    def log_message(self, *_args): pass
    def end_headers(self):
        self.send_header('Content-Security-Policy', self.csp)
        super().end_headers()
    def do_GET(self):
        prefix, _, key = self.path.rpartition('/')
        if prefix not in self.assets:
            return super().do_GET()
        path = self.assets[prefix] / bytes.fromhex(key).decode()
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
        self.end_headers()
        self.wfile.write(data)


def fixture(root):
    source = root / 'source'
    source.mkdir()
    shutil.copyfile(ROOT / 'desktop/frontend/prototypes/asr/assets/navi.png', source / 'texture.png')
    from PIL import Image
    with Image.open(source / 'texture.png') as image:
        width, height = image.size
    attachment = {'type': 'region', 'path': 'body', 'width': width, 'height': height}
    data = {'skeleton': {'spine': '3.6.53', 'width': width, 'height': height}, 'bones': [{'name': 'root'}],
            'slots': [{'name': 'body', 'bone': 'root', 'attachment': 'body'}],
            'skins': {name: {'body': {'body': attachment}} for name in ['normal', 'smile']},
            'animations': {'idle': {'bones': {'root': {'rotate': [{'time': 0, 'angle': -2}, {'time': 1, 'angle': 2}, {'time': 2, 'angle': -2}]}}}}}
    (source / 'skeleton.json').write_text(json.dumps(data), encoding="utf-8")
    (source / 'skeleton.atlas').write_text(f'texture.png\nsize: {width},{height}\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nbody\n  rotate: false\n  xy: 0,0\n  size: {width},{height}\n  orig: {width},{height}\n  offset: 0,0\n  index: -1\n', encoding="utf-8")
    catalog = prepare(source, root / 'components')
    entry = root / 'components' / catalog['models'][0]['resource']['root'] / 'spine-resource.json'
    config = json.loads(entry.read_text(encoding="utf-8"))
    config['skinLabels'] = {'normal': '放松', 'smile': '友好'}
    entry.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return root / 'components'


def choose_themed_option(page, label, value, screenshot=None):
    select = page.locator(f'.spine-editor select[aria-label="{label}"]')
    name = select.locator(f'option[value="{value}"]').text_content()
    page.get_by_role('combobox', name=label, exact=True).click()
    menu = page.get_by_role('listbox', name=label, exact=True)
    expect(menu).to_be_visible()
    if screenshot:
        page.screenshot(path=str(screenshot), animations='disabled')
    menu.get_by_role('option', name=name, exact=True).click()
    expect(menu).to_have_count(0)


def verify_render_resolution(page):
    # CSS transforms do not trigger ResizeObserver. Change only an ancestor's
    # scale, then change only DPR, as when the desktop pet crosses monitors.
    page.set_viewport_size({'width': 640, 'height': 640})
    session = page.context.new_cdp_session(page)
    page.evaluate("alphaRenderer.setPaused(false)")
    for dpr, scale in [(1, 1.5), (1.25, 1.5), (3, 1.5), (3, 0.75)]:
        session.send('Emulation.setDeviceMetricsOverride', {
            'width': 640, 'height': 640, 'deviceScaleFactor': dpr, 'mobile': False,
        })
        result = page.evaluate("""async ({scale}) => {
            document.body.style.transform = `scale(${scale})`;
            document.body.style.transformOrigin = 'top left';
            const canvas = document.querySelector('canvas');
            // Wait for rendering, not for an arbitrary wall-clock delay.
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const rect = canvas.getBoundingClientRect(), gl = canvas.getContext('webgl');
            return {actual: [gl.drawingBufferWidth, gl.drawingBufferHeight],
                expected: [Math.ceil(rect.width * devicePixelRatio * 2), Math.ceil(rect.height * devicePixelRatio * 2)],
                logical: [canvas.clientWidth, canvas.clientHeight]};
        }""", {'scale': scale})
        assert result['actual'] == result['expected'], (dpr, scale, result)
        assert result['logical'] == [160, 160], result
    session.detach()


def verify_alpha_compositing(browser, origin, root):
    from PIL import Image
    from io import BytesIO
    page = browser.new_page(viewport={'width': 160, 'height': 160}, device_scale_factor=1)
    samples = []
    for pma in [False, True]:
        package = root / ('pma' if pma else 'straight'); package.mkdir()
        Image.new('RGBA', (8, 8), (100, 50, 25, 128) if pma else (200, 100, 50, 128)).save(package / 'texture.png')
        attachment = {'type': 'region', 'path': 'body', 'width': 100, 'height': 100}
        skeleton = {'skeleton': {'spine': '3.6.53'}, 'bones': [{'name': 'root'}],
                    'slots': [{'name': name, 'bone': 'root', 'attachment': 'body'} for name in ['back', 'front']],
                    'skins': {'default': {name: {'body': attachment} for name in ['back', 'front']}}, 'animations': {'idle': {}}}
        (package / 'skeleton.json').write_text(json.dumps(skeleton), encoding="utf-8")
        (package / 'skeleton.atlas').write_text('texture.png\nsize: 8,8\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nbody\n  rotate: false\n  xy: 0,0\n  size: 8,8\n  orig: 8,8\n  offset: 0,0\n  index: -1\n', encoding="utf-8")
        prefix = '/compositing/' + package.name; Handler.assets[prefix] = package
        assets = {name: origin + prefix + '/' + name.encode().hex() for name in ['texture.png', 'skeleton.json', 'skeleton.atlas']}
        page.goto(origin + '/desktop/frontend/prototypes/asr/')
        page.evaluate("""async ({assets,pma}) => {
            document.body.replaceChildren(); Object.assign(document.body.style,{margin:'0',background:'#19262a'});
            const container=document.createElement('div');Object.assign(container.style,{width:'160px',height:'160px'});document.body.append(container);
            const {createRenderer}=await import('/plugins/builtin/sakura_spine/renderer.mjs');
            window.alphaRenderer=await createRenderer({container,rendererData:{runtimeVersion:'3.6.53',textures:{'texture.png':'texture.png'},
              config:{skeleton:'skeleton.json',atlas:'skeleton.atlas',defaultSkin:'default',defaultAnimation:'idle',speed:1,premultipliedAlpha:pma}},
              bindingId:'alpha',resourceId:'alpha',signal:new AbortController().signal,resolveAssetUrl:path=>assets[path]});
            alphaRenderer.setPaused(true);
        }""", {'assets': assets, 'pma': pma})
        sample = Image.open(BytesIO(page.locator('canvas').screenshot())).convert('RGB').getpixel((80, 80))
        # Two 50% opaque layers: correct source-over alpha is 75%, not alpha squared.
        expected = (157, 85, 48)
        assert all(abs(actual-wanted) <= 3 for actual,wanted in zip(sample, expected)), (pma, sample)
        samples.append(sample)
        if pma:
            verify_render_resolution(page)
        page.evaluate('alphaRenderer.dispose()')
    assert all(abs(a-b) <= 2 for a,b in zip(*samples)), samples
    page.close()


def run(components=None):
    real_components = components is not None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    output = ROOT / ('artifacts/spine-room/qa' if components else 'temp/spine-qa')
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='sakura-spine-journey-') as temporary, sync_playwright() as playwright:
            root = Path(temporary)
            components = Path(components).resolve() if components else fixture(root)
            archives = export_components(components, root / 'archives')
            catalog = json.loads((components / 'catalog.json').read_text(encoding="utf-8"))
            roots = RuntimeRoots(root / 'distribution', root / 'user')
            shutil.copytree(ROOT / 'plugins/builtin/sakura_portrait', roots.distribution_root / 'plugins/builtin/sakura_portrait')
            package = roots.user_root / 'characters/sample'
            package.mkdir(parents=True)
            shutil.copyfile(ROOT / 'desktop/frontend/prototypes/asr/assets/navi.png', package / 'default.png')
            (package / 'card.md').write_text('隔离验证角色', encoding="utf-8")
            (package / 'character.json').write_text(json.dumps({'id': 'sample', 'display_name': 'Spine 验证',
                'card': 'card.md', 'portrait': {'default': 'default.png'}}), encoding="utf-8")
            shutil.copytree(ROOT / 'plugins/builtin/sakura_spine', roots.distribution_root / 'plugins/builtin/sakura_spine')
            application = PluginApplicationHost(roots, 'spine-browser', ToolRegistry())
            application.start()
            try:
                boundary = CharacterStudioBoundary('spine-browser', 'credential', roots.user_root, plugin_application_provider=lambda: application)
                chosen = archives[0]
                def invoke(command, params=None):
                    params = params or {}
                    if command == 'studio_bootstrap': return boundary._dispatch('studio.bootstrap', {'initialCharacterId': 'sample'})
                    if command in {'show_studio', 'close_character_studio', 'report_runtime_diagnostic'}: return None
                    if command == 'studio_choose_source': return str(chosen)
                    if command != 'studio_request': raise ValueError(command)
                    result = boundary._dispatch(params['method'], params['params'])
                    if params['method'] in ('studio.visual.open', 'studio.visual.thumbnail'):
                        visual = result['presentation']['visual']
                        plugin = 'builtin/sakura_spine/editor.mjs' if visual['providerId'] == 'sakura.visual.spine' else 'builtin/sakura_portrait/frontend/editor.js'
                        visual['editor'] = origin + '/plugins/' + plugin
                        prefix = '/preview/' + visual['bindingId']
                        Handler.assets[prefix] = Path(result.pop('assetRootPath'))
                        result['assetBaseUrl'] = origin + prefix + '/'
                    if params['method'] == 'studio.visual.previews':
                        for item in result['items']:
                            path = item.pop('sourcePath', None)
                            item['previewUrl'] = None
                            if path:
                                path = Path(path); prefix = '/cover/' + item['resourceId']
                                Handler.assets[prefix] = path.parent
                                item['previewUrl'] = origin + prefix + '/' + path.name.encode().hex()
                    return result
                browser = playwright.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL') or ('msedge' if sys.platform == 'win32' else None))
                verify_alpha_compositing(browser, origin, root)
                page = browser.new_page(viewport={'width': 1274, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if 'Content Security Policy' in msg.text else None)
                page.expose_function('nativeInvoke', invoke)
                page.add_init_script("window.thumbnailRequests=0; window.__TAURI__={core:{invoke:(command,params)=>{if(params?.method==='studio.visual.thumbnail') window.thumbnailRequests++; return window.nativeInvoke(command,params);}},event:{listen:async()=>()=>{}}};")
                page.goto(origin + '/desktop/frontend/studio/')
                expect(page.locator('#displayName')).to_have_value('Spine 验证')
                page.get_by_role('button', name='角色形态', exact=True).click()
                for index, archive in enumerate(archives):
                    chosen = archive
                    page.get_by_role('button', name='导入形态', exact=True).click()
                    name = catalog['models'][index]['name']
                    page.locator('.form-card').filter(has_text=name).click()
                    speed = page.get_by_role('slider', name='播放速度', exact=True)
                    expect(speed).to_be_visible(timeout=20000)
                    expect(page.locator('canvas.spine-canvas')).to_be_visible()
                    if index == 0:
                        preview = page.locator('.spine-studio-preview')
                        canvas = preview.locator('canvas')
                        zoom = page.get_by_role('slider', name='预览缩放', exact=True)
                        original_box = canvas.bounding_box()
                        zoom.fill('200')
                        assert abs(canvas.bounding_box()['width'] - original_box['width'] * 2) < 1
                        page.locator('.spine-choices button[value="smile"]').click()
                        expect(zoom).to_have_value('200')
                        box = preview.bounding_box()
                        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                        before_drag = canvas.bounding_box()
                        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 40, y + 150, steps=4); page.mouse.up()
                        assert abs(canvas.bounding_box()['x'] - before_drag['x'] - 40) < 1
                        assert abs(canvas.bounding_box()['y'] - before_drag['y'] - 150) < 1
                        page.mouse.wheel(0, -180)
                        page.wait_for_function("""Number(document.querySelector('[aria-label="预览缩放"]').value) > 200""")
                        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x, y + 160, steps=4); page.mouse.up()
                        zoomed_box = canvas.bounding_box()
                        for skin in ['normal', 'smile']:
                            page.locator(f'.spine-choices button[value="{skin}"]').click()
                            assert canvas.bounding_box() == zoomed_box
                        page.screenshot(path=str(output / 'studio-inline-zoom.png'))
                        page.get_by_role('button', name='重置预览', exact=True).click()
                        expect(zoom).to_have_value('100')
                        assert abs(canvas.bounding_box()['width'] - original_box['width']) < 1
                    page.locator('.spine-choices button[value="smile"]').click()
                    speed.fill('1.5')
                    source_resource = catalog['models'][index]['resource']
                    source_config = json.loads((components / source_resource['root'] / source_resource['entry']).read_text(encoding="utf-8"))
                    default_skin = page.locator('.spine-editor select[aria-label="默认表情"]')
                    expect(default_skin).to_have_value(source_config['defaultSkin'])
                    skin_name = page.get_by_role('textbox', name='当前表情名称', exact=True)
                    expect(skin_name).to_have_value(source_config.get('skinLabels', {}).get('smile', 'smile'))
                    skin_name.fill('开心')
                    expect(page.get_by_role('button', name='开心', exact=True)).to_be_visible()
                    page.locator('.spine-choices button[value="normal"]').click()
                    expect(skin_name).to_have_value(source_config.get('skinLabels', {}).get('normal', 'normal'))
                    skin_name.fill('认真')
                    expect(page.get_by_role('button', name='认真', exact=True)).to_be_visible()
                    page.locator('.spine-choices button[value="smile"]').click()
                    expect(skin_name).to_have_value('开心')
                    expect(default_skin).to_have_value(source_config['defaultSkin'])
                    expect(default_skin.locator('option[value="smile"]')).to_have_text('开心')
                    expect(default_skin.locator('option[value="normal"]')).to_have_text('认真')
                    choose_themed_option(page, '默认表情', 'smile', output / 'studio-default-menu.png')
                    expect(page.get_by_role('combobox', name='默认表情', exact=True)).to_have_text('开心')
                    alpha = page.locator('.spine-editor select[aria-label="贴图透明方式"]')
                    original_alpha = str(source_config['premultipliedAlpha']).lower()
                    expect(alpha).to_have_value(original_alpha)
                    if 'selectableSkins' in source_config:
                        expect(page.get_by_role('button', name='default', exact=True)).to_have_count(0)
                    # Recreate the renderer and retain the rest of the draft.
                    # The synthetic fixture saves a changed encoding to test persistence.
                    saved_alpha = original_alpha if real_components else 'true'
                    for value in (['false' if original_alpha == 'true' else 'true', saved_alpha] if real_components else [saved_alpha]):
                        previous = page.locator('canvas.spine-canvas').element_handle()
                        choose_themed_option(page, '贴图透明方式', value, output / 'studio-alpha-menu.png')
                        page.wait_for_function('(canvas) => !canvas.isConnected', arg=previous)
                        expect(alpha).to_have_value(value)
                        expect(speed).to_have_value('1.5')
                        expect(skin_name).to_have_value('开心')
                        expect(page.locator('.spine-choices button[value="smile"]')).to_have_attribute('aria-pressed', 'true')
                    # Previewing another expression must not change the saved default.
                    page.locator('.spine-choices button[value="normal"]').click()
                    expect(skin_name).to_have_value('认真')
                    expect(default_skin).to_have_value('smile')
                    page.locator('#saveButton').click()
                    expect(page.locator('#saveButton')).to_be_enabled(timeout=20000)
                    page.reload()
                    expect(page.locator('#displayName')).to_have_value('Spine 验证')
                    page.get_by_role('button', name='角色形态', exact=True).click()
                    page.locator('.form-card').filter(has_text=name).click()
                    expect(speed).to_have_value('1.5', timeout=20000)
                    expect(speed).to_be_visible()
                    expect(speed).to_be_enabled()
                    expect(alpha).to_have_value(saved_alpha)
                    expect(default_skin).to_have_value('smile')
                    expect(skin_name).to_have_value('开心')
                    page.wait_for_function("() => !document.querySelector('#expressionList').inert")
                    expect(page.locator('.spine-choices button[value="smile"]')).to_have_attribute('aria-pressed', 'true')
                    page.locator('canvas.spine-canvas').scroll_into_view_if_needed()
                    page.screenshot(path=str(output / f'room-{index + 1}-studio.png'), animations='disabled')
                    if index == 0:
                        page.evaluate("""() => {
                          const tokens = {'primary':'#2d9b70','primary-hover':'#3db884','text':'#e4efeb','secondary-text':'#aac9bc',
                            'muted-text':'#83a396','page-bg':'#131e20','panel-bg':'#182728','input-bg':'#1c3030','border':'#345553'};
                          for(const [name,value] of Object.entries(tokens)) document.documentElement.style.setProperty('--sakura-'+name,value);
                        }""")
                        page.screenshot(path=str(output / 'studio-dark.png'), animations='disabled')
                        page.set_viewport_size({'width':680,'height':900})
                        skin_name.scroll_into_view_if_needed()
                        page.screenshot(path=str(output / 'studio-dark-narrow.png'), animations='disabled')
                        assert page.evaluate("document.querySelector('.page-scroll').scrollWidth <= document.querySelector('.page-scroll').clientWidth")
                        page.set_viewport_size({'width':1274,'height':900})
                        page.evaluate("document.documentElement.removeAttribute('style')")
                    saved = json.loads((package / 'character.json').read_text(encoding="utf-8"))
                    raw_resource = next(item for item in saved['visuals']['resources'] if item.get('name') == name)
                    resource = CharacterVisualResource.from_mapping(raw_resource)
                    from app.config.character_loader import _load_profile
                    projection = application.application.export_visual_resource(_load_profile(package / 'character.json'), resource)
                    assert projection['pluginRequirements'][0]['plugins'][0]['id'] == 'sakura.visual.spine'
                    assert projection['data']['defaultSkin'] == 'smile' and projection['data']['speed'] == 1.5
                    assert projection['data']['premultipliedAlpha'] == (saved_alpha == 'true')
                    assert projection['data'].get('selectableSkins') == source_config.get('selectableSkins')
                    assert projection['data']['skinLabels']['smile'] == '开心'
                    assert projection['data']['skinLabels']['normal'] == '认真'
                    assert len(projection['assets']) >= 3
                    binding = application.application.visuals.bind('sample', package, resource)
                    assert json.loads(binding.description['prompt'].split('\n', 1)[1])['skinLabels']['smile'] == '开心'
                    visual = binding.presentation()
                    visual['renderer'] = origin + '/plugins/builtin/sakura_spine/renderer.mjs'
                    prefix = '/runtime/' + binding.id; Handler.assets[prefix] = package
                    visual['assets'] = {key: origin + prefix + '/' + path.encode().hex() for key, path in visual['assets'].items()}
                    control = binding.parse_control({'version': 1, 'resourceId': resource.id, 'payload': {'skin': 'normal'}}).control
                    assert control
                    smile_control = binding.parse_control({'version': 1, 'resourceId': resource.id, 'payload': {'skin': 'smile'}}).control
                    runtime = browser.new_page(viewport={'width': 600, 'height': 700})
                    runtime.on('pageerror', lambda error: errors.append(str(error)))
                    runtime.goto(origin + '/desktop/frontend/prototypes/asr/')
                    result = runtime.evaluate('''async ({visual, control, smileControl}) => {
                      document.body.replaceChildren();
                      const container = document.createElement('div'); container.style.width='500px'; container.style.height='600px'; document.body.append(container);
                      const {createRendererHost} = await import('/desktop/frontend/pet/renderer-host.js');
                      const failures=[];
                      const plugin = await import(visual.renderer);
                      window.spineHost=createRendererHost({container,loadModule:async()=>({mount:async options => {
                        window.spineInstance = await plugin.mount(options); return spineInstance;
                      }}),services:{setSurface:async size=>{window.surface=size;return true}, unavailable:(...v)=>failures.push(v)},onUnavailable:(...v)=>failures.push(v),onError:(...v)=>failures.push(v)});
                      if(!await spineHost.bind({visual})) throw Error(JSON.stringify(failures));
                      const first={},last={},inherited={};
                      spineHost.begin('reply'); const played=await spineHost.play(control,'reply',0,first);
                      const duplicate=await spineHost.play(control,'reply',0,first);
                      await spineHost.play(smileControl,'reply',1,last);
                      await spineHost.play(undefined,'reply',2,inherited);
                      const reviewed=[];
                      for(const item of [first,last,inherited,first]) {
                        if(!await spineHost.review(item)) throw Error('review rejected');
                        reviewed.push(spineInstance.snapshotState());
                      }
                      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                      return {played,duplicate,failures,reviewed,surface:window.surface,canvas:!!container.querySelector('canvas')};
                    }''', {'visual': visual, 'control': control, 'smileControl': smile_control})
                    assert result['played'] and not result['duplicate'] and result['canvas'] and not result['failures'], result
                    assert [state['skin'] for state in result['reviewed']] == ['normal', 'smile', 'smile', 'normal']
                    assert all(state['speed'] == 1.5 and state['animation'] == 'idle' for state in result['reviewed'])
                    runtime.screenshot(path=str(output / f'room-{index + 1}-renderer.png'))
                    before = runtime.locator('canvas').screenshot()
                    runtime.evaluate('''async () => { for(let i=0;i<20;i++) await new Promise(requestAnimationFrame); }''')
                    assert runtime.locator('canvas').screenshot() != before, 'idle animation did not advance'
                    runtime.evaluate("spineHost.freeze(); if(!document.querySelector('canvas')) throw Error('frozen surface removed'); spineHost.destroy(); if(document.querySelector('canvas')) throw Error('canvas leaked');")
                    runtime.close()
                page.set_viewport_size({'width': 680, 'height': 900})
                page.locator('canvas.spine-canvas').scroll_into_view_if_needed()
                assert page.evaluate("document.querySelector('.page-scroll').scrollWidth <= document.querySelector('.page-scroll').clientWidth")
                page.screenshot(path=str(output / 'studio-narrow.png'), animations='disabled')
                page.set_viewport_size({'width': 1274, 'height': 900})
                chosen = components / catalog['models'][0]['resource']['root']
                page.get_by_role('button', name='添加形态', exact=True).click()
                dialog = page.get_by_role('dialog', name='添加形态', exact=True)
                dialog.get_by_role('button', name='Spine', exact=True).click()
                dialog.get_by_role('textbox', name='形态名称', exact=True).fill('目录导入')
                dialog.get_by_role('button', name='添加', exact=True).click()
                page.get_by_role('button', name='导入模型目录', exact=True).click()
                speed = page.get_by_role('slider', name='播放速度', exact=True)
                expect(speed).to_be_visible(timeout=20000)
                expect(speed).to_have_value('1')
                page.locator('#saveButton').click()
                expect(page.locator('#saveButton')).to_be_enabled(timeout=20000)
                saved = json.loads((package / 'character.json').read_text(encoding="utf-8"))
                imported = next(item for item in saved['visuals']['resources'] if item.get('name') == '目录导入')
                config = json.loads((package / imported['root'] / imported['entry']).read_text(encoding="utf-8"))
                original = json.loads((chosen / catalog['models'][0]['resource']['entry']).read_text(encoding="utf-8"))
                assert json.loads((package / imported['root'] / config['skeleton']).read_text(encoding="utf-8")) == json.loads(
                    (chosen / original['skeleton']).read_text(encoding="utf-8"))
                # Saving reopens the visual editor; finish that transition before closing the bridge.
                page.wait_for_function("() => !document.querySelector('#expressionList').inert")
                expect(page.locator('canvas.spine-canvas')).to_be_visible(timeout=20000)
                # Reopening must populate unselected model cards too, without
                # mounting an editor or a permanent WebGL canvas for each card.
                page.reload()
                page.get_by_role('button', name='角色形态', exact=True).click()
                page.wait_for_function("""() => {
                    const cards = [...document.querySelectorAll('.form-card')];
                    return cards.length >= 3 && cards.every(card => card.querySelector('.form-card-cover img')?.naturalWidth > 0);
                }""")
                assert saved['visuals']['default'] == 'portrait-default'
                # Import preserves the portrait default; explicitly open a Spine editor.
                page.locator('.form-card').filter(has_text='目录导入').click()
                page.wait_for_function("() => !document.querySelector('#expressionList').inert")
                expect(page.locator('.spine-studio-preview canvas')).to_be_visible()
                assert page.locator('canvas.spine-canvas').count() == 1
                page.screenshot(path=str(output / 'studio-thumbnails.png'), animations='disabled')
                page.evaluate("window.coverNodes = [...document.querySelectorAll('.form-card-cover img')]; window.coverSources = coverNodes.map(img => img.src); window.thumbnailRequestsBeforeSave = thumbnailRequests")
                zoom = page.get_by_role('slider', name='预览缩放', exact=True)
                zoom.fill('270')
                preview = page.locator('.spine-studio-preview')
                preview.scroll_into_view_if_needed()
                box = preview.bounding_box()
                x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 30, y + 70, steps=3); page.mouse.up()
                view_before_save = preview.locator('canvas').evaluate('canvas => canvas.style.transform')
                page.locator('#saveButton').click()
                expect(page.locator('#saveButton')).to_be_enabled(timeout=20000)
                page.wait_for_function("() => !document.querySelector('#expressionList').inert")
                assert page.evaluate("coverNodes.every((img,i) => img.isConnected && img.src === coverSources[i])")
                expect(zoom).to_have_value('270')
                assert preview.locator('canvas').evaluate('canvas => canvas.style.transform') == view_before_save
                assert page.evaluate("thumbnailRequests === thumbnailRequestsBeforeSave")
                # Editor-owned catches must forward both preview failures to the host.
                page.evaluate("""async () => {
                  const {createEditor}=await import('/plugins/builtin/sakura_spine/editor.mjs');
                  const container=document.createElement('div'); document.body.append(container);
                  window.editorFailures=[];
                  window.errorEditor=createEditor({container,rendererData:{config:{defaultSkin:'normal',defaultAnimation:'idle',speed:1,premultipliedAlpha:false},skins:['normal'],animations:['idle']},
                    onPreview:async()=>{throw Error('SPINE_PREVIEW_TEST');},
                    onRenderingChange:async()=>{throw Error('SPINE_RENDERING_TEST');},
                    onError:(error,stage)=>editorFailures.push({message:error.message,stage})});
                  container.querySelector('.spine-choices button').click();
                  container.querySelector('select[aria-label="贴图透明方式"]').dispatchEvent(new Event('change'));
                }""")
                page.wait_for_function("editorFailures.length === 2")
                assert page.evaluate("editorFailures.map(item=>item.stage).sort()") == ['studio.spine.preview', 'studio.spine.rendering']
                page.evaluate("errorEditor.dispose()")
                assert not errors, errors
                browser.close()
                print(f'PASS: {len(archives)} Spine components: real Studio import/edit/save/reopen + RendererHost lifecycle under desktop CSP')
            finally:
                application.close()
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--components', type=Path)
    run(parser.parse_args().components)
