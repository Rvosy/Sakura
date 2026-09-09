"""Real HTML/CSS tooltip regression journey; only window bootstrap is mocked.
Run with bundled Python: desktop/frontend/tests/tooltip.journey.py
"""
import argparse
import functools
import http.server
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright, expect


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


frontend = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--channel', default=None, help='Use an installed browser, e.g. msedge')
args = parser.parse_args()
server = http.server.ThreadingHTTPServer(
    ('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(frontend)))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{server.server_port}'
output = Path(tempfile.mkdtemp(prefix='sakura-tooltip-'))
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        page = browser.new_page(viewport={'width': 1008, 'height': 850}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/settings/settings.js', lambda route: route.fulfill(
            content_type='text/javascript', body='''
              import { enhanceSelect } from './select-control.js';
              enhanceSelect(document.querySelector('#screenResolution'));
              document.querySelector('#characterSelect').innerHTML = '<option>N.A.V.I.</option>';
            '''))
        page.goto(url + '/settings/index.html', wait_until='networkidle')
        help = page.locator('[aria-label="气泡高度说明"]')
        tip = page.locator('#sakura-tooltip')
        before = page.locator('#bubbleHeight').bounding_box()
        help.hover()
        expect(tip).to_be_visible()
        expect(tip).to_contain_text('最低高度')
        assert not help.get_attribute('title')
        assert page.locator('#bubbleHeight').bounding_box() == before, 'help must not move the slider'
        page.screenshot(path=str(output / 'settings-light.png'))
        # Hover stays available while moving into the explanation.
        tip.hover()
        expect(tip).to_be_visible()
        page.mouse.move(5, 5)
        expect(tip).to_be_hidden()
        # Click pins until explicit dismissal; keyboard focus exposes the description.
        help.click()
        page.mouse.move(5, 5)
        expect(tip).to_be_visible()
        help.click()
        expect(tip).to_be_hidden()
        help.focus()
        page.keyboard.press('Tab')
        page.keyboard.press('Shift+Tab')
        expect(tip).to_be_visible()
        assert 'sakura-tooltip' in help.get_attribute('aria-describedby')
        page.keyboard.press('Escape')
        expect(tip).to_be_hidden()
        assert help.get_attribute('aria-describedby') is None
        # Live colors and dynamic text use the same tooltip without stale labels or HTML.
        help.evaluate('el => el.setAttribute("aria-describedby", "existing-help")')
        help.click()
        page.evaluate('''() => {
          const root = document.documentElement;
          root.style.setProperty('--sakura-panel-bg', '#202630');
          root.style.setProperty('--sakura-text', '#e0e8f2');
          root.style.setProperty('--sakura-border', '#596d88');
        }''')
        expect(tip).to_have_css('background-color', 'rgb(32, 38, 48)')
        expect(tip).to_have_css('color', 'rgb(224, 232, 242)')
        help.evaluate('el => el.dataset.tooltip = "<b>动态说明</b>"')
        expect(tip).to_have_text('<b>动态说明</b>')
        assert tip.locator('b').count() == 0
        page.screenshot(path=str(output / 'settings-dark-tooltip.png'))
        page.keyboard.press('Escape')
        assert help.get_attribute('aria-describedby') == 'existing-help'
        # The themed select receives its source explanation and help works in modal dialogs.
        page.evaluate('''() => {
          document.querySelector('#page-character').classList.remove('is-active');
          document.querySelector('#page-interaction').classList.add('is-active');
        }''')
        select = page.locator('#screenResolution').locator('..').locator('button')
        select.hover()
        expect(tip).to_be_visible()
        expect(tip).to_contain_text('不放大截图')
        page.evaluate('''() => {
          const dialog = document.createElement('dialog');
          dialog.innerHTML = '<button class="setting-help" data-tooltip="弹窗说明">?</button>';
          document.body.append(dialog); dialog.showModal();
        }''')
        page.locator('dialog[open] button').focus()
        expect(tip).to_have_text('弹窗说明')
        expect(tip).to_be_visible()
        assert tip.evaluate('el => !!el.closest("dialog")')
        page.keyboard.press('Escape')
        expect(tip).to_be_hidden()
        assert page.locator('dialog[open]').count() == 1, 'first Escape dismisses help, not dialog'
        page.evaluate('document.querySelector("dialog[open]").remove()')

        select.hover()
        expect(tip).to_be_visible()
        # Edge positioning, disabled controls, removal and scroll dismissal.
        page.evaluate('''() => {
          const button = document.createElement('button');
          button.id = 'edge-help'; button.disabled = true;
          button.dataset.tooltip = '不可用原因 '.repeat(20);
          button.textContent = '?';
          button.style.cssText = 'position:fixed;bottom:0;right:0;width:24px;height:24px';
          document.body.append(button);
        }''')
        page.locator('#edge-help').hover(force=True)
        expect(tip).to_be_visible()
        box = tip.bounding_box()
        assert box['x'] >= 0 and box['y'] >= 0
        assert box['x'] + box['width'] <= 1008 and box['y'] + box['height'] <= 850
        page.evaluate('document.querySelector("#edge-help").remove()')
        expect(tip).to_be_hidden()
        select.hover()
        expect(tip).to_be_visible()
        page.evaluate('document.dispatchEvent(new Event("scroll"))')
        expect(tip).to_be_hidden()
        # Main UI deliberately has no hover help, including while ASR changes state.
        boot = (frontend / 'tests/fixtures/composer-motion-boot.js').read_text(encoding='utf-8')
        page.route('**/app.js', lambda route: route.fulfill(content_type='text/javascript', body=boot))
        page.goto(url, wait_until='networkidle')
        for selector in ['#composer-attachment', '#voice-mic', '#composer-send']:
            control = page.locator(selector)
            control.hover()
            page.wait_for_timeout(550)
            expect(page.locator('[role="tooltip"]:visible')).to_have_count(0)
            assert control.get_attribute('title') is None
            assert control.get_attribute('aria-label')
        for state in ['recording', 'recognizing', 'idle']:
            page.evaluate('(state) => motionJourney.view.setState(state)', state)
            page.locator('#voice-mic').hover()
            page.wait_for_timeout(550)
            expect(page.locator('[role="tooltip"]:visible')).to_have_count(0)
            assert page.locator('#voice-mic').get_attribute('title') is None
        # Expose the production screenshot item without invoking a real capture.
        page.evaluate('''() => {
          const dock = document.querySelector('#composer-tool-dock');
          dock.hidden = false;
          dock.dataset.open = 'true';
          dock.style.cssText = 'display:block;opacity:1;visibility:visible;position:fixed;top:700px;left:180px';
        }''')
        page.locator('#capture-screen').hover()
        page.wait_for_timeout(550)
        expect(page.locator('[role="tooltip"]:visible')).to_have_count(0)
        assert page.locator('[title], [data-tooltip]').count() == 0
        page.screenshot(path=str(output / 'main-without-tooltip.png'))
        assert not errors, errors
        browser.close()
        print(f'PASS: settings help, themes, select, modal and edges; no main UI hover help. Screenshots: {output}')
finally:
    server.shutdown()
    server.server_close()
