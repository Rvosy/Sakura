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
            content_type='text/javascript', body=''))
        page.goto(url + '/settings/index.html', wait_until='networkidle')
        help = page.locator('[aria-label="气泡高度说明"]')
        tip = page.locator('#sakura-tooltip')
        before = page.locator('#bubbleHeight').bounding_box()
        help.hover()
        expect(tip).to_be_visible()
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
        # 动态说明按纯文本显示，关闭后恢复原有的辅助说明。
        help.evaluate('el => el.setAttribute("aria-describedby", "existing-help")')
        help.click()
        help.evaluate('el => el.dataset.tooltip = "<b>动态说明</b>"')
        expect(tip).to_have_text('<b>动态说明</b>')
        assert tip.locator('b').count() == 0
        page.screenshot(path=str(output / 'settings-dynamic-tooltip.png'))
        page.keyboard.press('Escape')
        assert help.get_attribute('aria-describedby') == 'existing-help'
        # 弹窗内的提示保持键盘关闭和焦点行为。
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

        help.hover()
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
        help.hover()
        expect(tip).to_be_visible()
        page.evaluate('document.dispatchEvent(new Event("scroll"))')
        expect(tip).to_be_hidden()
        assert not errors, errors
        browser.close()
        print(f'PASS: settings help, dynamic text, modal and edges. Screenshots: {output}')
finally:
    server.shutdown()
    server.server_close()
