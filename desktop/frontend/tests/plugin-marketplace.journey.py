"""Marketplace details against the production renderer with an isolated catalog/host."""
import functools
import http.server
import json
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/desktop/frontend/settings/styles.css"></head>
<body><section id="page-plugins" class="is-active">
<div class="plugin-page-heading"><div></div></div><div id="pluginInstallMenu"></div>
<div class="plugin-toolbar"></div><div class="plugin-workbench"></div></section>
<script type="module">
import { createPluginMarketplace } from '/desktop/frontend/settings/plugin-marketplace.js';
import { catalogPlugins } from '/desktop/frontend/settings/plugin-marketplace-source.js';
const release = version => ({version, manifest:{id:'sakura.visual.spine', name:'Spine',
  author:'Sakura', api:4, description:'Spine 3.6 角色的表情、动画和编辑。',
  presentation:{category:'visual', kind:'provider'}}, package:{url:'https://example.test/plugin.zip'}});
const catalog = {schema_version:1, plugins:[{id:'sakura.visual.spine',repository:'https://github.com/Rvosy/Sakura-Spine',
  versions:[release('0.2.7'), release('0.2.6')]}]};
const fixture = window.fixture = {
  plugins:catalogPlugins(catalog,{api:4, services:[]}),
  local:[{pluginId:'sakura.visual.spine',installId:'local-spine',version:'0.2.7',enabled:true,source:'user'}],
  opened:[], installs:[]
};
const host = { installedPlugins:()=>fixture.local, openPlugin:id=>fixture.opened.push(id),
  async refreshCurrent(){ const enabled = fixture.local[0]?.enabled ?? false;
    fixture.local = [{pluginId:'sakura.visual.spine',installId:'local-spine',
    version:'0.2.7',enabled,source:'user'}]; } };
fixture.source = { canUpdate:true, async load(){return {state:'ready',plugins:fixture.plugins};},
  async readme(){ return {markdown:__README__,url:'https://github.com/Rvosy/Sakura-Spine/blob/e245e6710e7f7baf18711cb7c8c62ae8dd3ea2a3/README.md'}; },
  openUrl(url){fixture.lastUrl=url;},
  async install(plugin,{onProgress}) {
    fixture.installs.push(plugin.id); fixture.progress = onProgress;
    await new Promise(resolve=>{fixture.finish=resolve;});
  }
};
fixture.market = createPluginMarketplace({document, host, notify(){},source:fixture.source});
fixture.market.setView('market',{load:false});
await fixture.market.refresh();
</script></body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/marketplace-test":
            readme_path = os.environ.get('SAKURA_MARKETPLACE_README')
            readme = Path(readme_path).read_text(encoding='utf-8') if readme_path else '# 使用方式\n\n1. 在角色工坊导入形态。\n2. 选择默认表情。\n\n## 资源范围\n\n需要准备骨骼、图集与贴图。\n\n[项目资料](LICENSE)'
            content = HTML.replace('__README__', json.dumps(readme)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            super().do_GET()


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None))
            page = browser.new_page(viewport={"width": 803, "height": 843}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/marketplace-test")
            upload = page.get_by_role('button', name='上传插件', exact=True)
            expect(upload).to_be_visible()
            upload.click()
            submission = page.get_by_role('dialog', name='上传插件', exact=True)
            expect(submission).to_be_visible()
            submission.get_by_role('button', name='打开 Issue 页面', exact=True).click()
            assert page.evaluate('fixture.lastUrl') == 'https://github.com/Rvosy/Sakura-Registry/issues/new?template=submit-plugin.yml'
            output = os.environ.get("SAKURA_MARKETPLACE_SCREENSHOT_DIR")
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                submission.screenshot(path=str(Path(output) / "plugin-upload.png"))
            page.keyboard.press('Escape')
            expect(submission).not_to_be_visible()
            expect(upload).to_be_focused()
            page.set_viewport_size({"width": 360, "height": 640})
            upload.click()
            assert submission.evaluate('el => el.scrollWidth <= el.clientWidth')
            expect(submission.get_by_role('button', name='打开 Issue 页面', exact=True)).to_be_in_viewport()
            submission.get_by_role('button', name='关闭', exact=True).click()
            expect(upload).to_be_focused()
            page.set_viewport_size({"width": 803, "height": 843})
            if output:
                page.screenshot(path=str(Path(output) / "plugin-market-upload-entry.png"))
            page.locator('.card-open').click()
            dialog = page.locator('#detail-dialog')
            expect(dialog).to_be_visible()
            expect(dialog.locator('[data-manage]')).to_be_enabled()
            expect(dialog.locator('[data-technical]')).to_be_visible()
            # Catalog descriptions are not duplicated into a second body; absent notes stay absent.
            assert dialog.locator('.detail-summary').count() == 1
            assert dialog.locator('.detail-section, [data-history]').count() == 0
            expect(dialog.locator('.plugin-readme')).to_be_visible()
            dialog.locator('.detail-links a').first.click()
            assert page.evaluate('fixture.lastUrl') == 'https://github.com/Rvosy/Sakura-Spine'
            assert not dialog.locator('[data-disclosure="technical"]').evaluate('el => el.open')
            output = os.environ.get("SAKURA_MARKETPLACE_SCREENSHOT_DIR")
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                dialog.screenshot(path=str(Path(output) / "plugin-detail.png"))

            # A host sync must not close expanded details or steal keyboard focus.
            dialog.locator('[data-technical]').click()
            page.evaluate('fixture.market.sync()')
            expect(dialog.locator('[data-technical]')).to_be_focused()
            assert dialog.locator('[data-disclosure="technical"]').evaluate('el => el.open')
            page.evaluate('fixture.savedLocal = fixture.local; fixture.local = []; fixture.market.sync()')
            expect(dialog.locator('[data-install]')).to_be_enabled()
            assert dialog.locator('[data-manage]').count() == 0
            page.evaluate("""fixture.local = [{pluginId:'sakura.visual.spine',source:'bundled',
              version:'0.0.0',state:'failed',reasonCode:'PLUGIN_MIGRATION_SOURCE_MISSING'}]; fixture.market.sync()""")
            expect(dialog.locator('[data-install]')).to_be_enabled()
            assert dialog.locator('[data-manage]').count() == 0
            page.evaluate('fixture.local = fixture.savedLocal; fixture.market.sync()')
            # An unavailable catalog version still permits managing the installed plugin.
            page.evaluate("async () => { fixture.plugins[0].recommendedVersion = undefined; await fixture.market.refresh(); }")
            assert dialog.locator('[data-install]').count() == 0
            dialog.locator('[data-manage]').click()
            expect(dialog).not_to_be_visible()
            assert page.evaluate('fixture.opened') == ['local-spine']

            # Enabled plugins update directly; the UI never asks users to disable them first.
            page.evaluate("async () => { fixture.plugins[0].recommendedVersion='0.2.7'; fixture.local[0].version='0.2.6'; await fixture.market.refresh(); fixture.market.setView('market'); }")
            page.locator('.card-open').click()
            expect(dialog.locator('[data-install]')).to_be_enabled()
            assert page.evaluate('fixture.installs') == []
            # Updating also leaves a management route when the source lacks update support.
            page.evaluate("fixture.source.canUpdate=false; fixture.market.sync()")
            expect(dialog.locator('[data-install]')).to_be_disabled()
            expect(dialog.locator('[data-manage]')).to_be_enabled()
            page.evaluate("fixture.source.canUpdate=true; fixture.market.sync()")

            # Long external notes render as text, scroll within the dialog, and keep actions reachable.
            page.evaluate("""async () => {
              const p = fixture.plugins[0];
              p.name = '很长的插件名称'.repeat(6);
              p.versions[0].notes = '<img src=x onerror=alert(1)>\\n' + '更新说明\\n'.repeat(70);
              p.versions[1].yanked = '已撤回：测试原因';
              await fixture.market.refresh();
            }""")
            assert dialog.locator('.version-row img').count() == 0
            for width in [803, 360]:
                page.set_viewport_size({"width": width, "height": 640})
                assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth')
                assert dialog.locator('.drawer-scroll').evaluate('el => el.scrollHeight > el.clientHeight')
                button = dialog.locator('[data-install]').bounding_box()
                assert 0 <= button['y'] and button['y'] + button['height'] <= 640
            dialog.locator('[data-history]').click()
            assert dialog.locator('[data-disclosure="history"]').evaluate('el => el.open')
            dialog.locator('[data-install]').click()
            page.wait_for_function('fixture.progress !== undefined')
            page.evaluate("fixture.progress(50, 'downloading'); fixture.market.sync()")
            expect(dialog.locator('[role="progressbar"]')).to_have_attribute('aria-valuenow', '50')
            assert dialog.locator('[data-disclosure="history"]').evaluate('el => el.open')
            page.evaluate('fixture.finish()')
            expect(dialog.locator('[data-manage]')).to_be_enabled()
            assert page.evaluate('fixture.installs') == ['sakura.visual.spine']

            # Documentation errors and late responses never block management or replace a newer README.
            page.evaluate("""async () => {
              fixture.source.readme = async () => { throw Error('offline'); };
              fixture.plugins[0].versions[0].commit = 'failed';
              await fixture.market.refresh();
            }""")
            expect(dialog.locator('[data-retry-document]')).to_be_visible()
            expect(dialog.locator('.plugin-readme')).to_be_visible()
            expect(dialog.locator('[data-manage]')).to_be_enabled()
            page.evaluate("""() => {
              fixture.pendingDocs = [];
              fixture.source.readme = () => new Promise(resolve => fixture.pendingDocs.push(resolve));
            }""")
            dialog.locator('[data-retry-document]').click()
            page.wait_for_function('fixture.pendingDocs.length === 1')
            page.evaluate("async () => { fixture.plugins[0].versions[0].commit='new'; await fixture.market.refresh(); }")
            page.wait_for_function('fixture.pendingDocs.length === 2')
            page.evaluate("fixture.pendingDocs[1]({markdown:'New instructions',url:'https://example.com/new/README.md'})")
            expect(dialog.locator('.plugin-readme')).to_have_text('New instructions')
            page.evaluate("fixture.pendingDocs[0]({markdown:'Stale instructions',url:'https://example.com/old/README.md'})")
            expect(dialog.locator('.plugin-readme')).to_have_text('New instructions')
            # Reopening renders the native disk snapshot while the registry request is pending.
            page.evaluate("""async () => {
              fixture.market.dispose();
              const {createPluginMarketplace} = await import('/desktop/frontend/settings/plugin-marketplace.js');
              const {createMarketplaceSource} = await import('/desktop/frontend/settings/plugin-marketplace-source.js');
              const release = {version:'1.0.0',commit:'a'.repeat(40),yanked:false,
                manifest:{id:'cached',version:'1.0.0',api:4,name:'Cached plugin'},
                package:{url:'https://example.test/package.zip',size:10}};
              fixture.disk = {catalog:{schema_version:1,plugins:[{id:'cached',repository:'https://github.com/a/b',versions:[release]}]},
                context:{api:4,services:[]},readmes:[{id:'cached',repository:'https://github.com/a/b',version:'1.0.0',commit:release.commit,
                  document:{markdown:'Cached instructions',url:'https://github.com/a/b/blob/'+release.commit+'/README.md'}}]};
              fixture.readmeCalls=0;
              fixture.source = createMarketplaceSource({Channel:class {},host:{},invoke:async (command,args)=>{
                if(command === 'settings_marketplace_readme') {fixture.readmeCalls++; throw Error('unexpected download');}
                args.progress.onmessage(fixture.disk);
                args.progress.onmessage({source:'slow network'});
                return new Promise((resolve,reject)=>{fixture.completeRefresh=resolve;fixture.failRefresh=reject;});
              }});
              fixture.market=createPluginMarketplace({document,host:{installedPlugins:()=>[]},notify(){},source:fixture.source});
              fixture.market.setView('market',{load:false});
              fixture.refresh=fixture.market.refresh();
            }""")
            expect(page.locator('.card-open')).to_have_text('Cached plugin')
            expect(page.locator('#search')).to_be_enabled()
            expect(page.locator('#notice')).not_to_be_visible()
            page.locator('.card-open').click()
            expect(dialog.locator('.plugin-readme')).to_have_text('Cached instructions')
            assert page.evaluate('fixture.readmeCalls') == 0
            assert dialog.locator('.detail-document-state').count() == 0
            dialog.locator('[data-technical]').click()
            page.evaluate("async () => {fixture.failRefresh(Error('offline'));await fixture.refresh;}")
            expect(dialog.locator('.plugin-readme')).to_have_text('Cached instructions')
            assert dialog.locator('[data-disclosure="technical"]').evaluate('el=>el.open')
            expect(dialog.locator('[data-technical]')).to_be_focused()
            expect(page.locator('#notice')).to_be_visible()
            page.evaluate("fixture.refresh=fixture.market.refresh();void 0")
            page.evaluate("""async () => {
              const next=structuredClone(fixture.disk);next.catalog.plugins[0].versions[0].manifest.name='Updated plugin';
              fixture.completeRefresh(next);await fixture.refresh;
            }""")
            expect(dialog.locator('#detail-title')).to_have_text('Updated plugin')
            expect(dialog.locator('.plugin-readme')).to_have_text('Cached instructions')
            assert page.evaluate('fixture.readmeCalls') == 0
            assert not errors, errors
            browser.close()
            print('PASS: marketplace management, update guards, disclosure refresh, safe notes and responsive dialog')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    run()
