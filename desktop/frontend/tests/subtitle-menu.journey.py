"""Exercise the shipping submenu in Chromium; mock only the native menu commands."""
import functools
import http.server
import os
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright

frontend = Path(__file__).resolve().parents[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


boot = """
import { PetContextMenu, PRODUCT_MENU_ACTIONS } from './pet/context_menu.js';
import { createTypewriter } from './pet/typewriter.js';
import { renderSubtitleText } from './pet/multilingual-text.js';
import { createBubbleScroll } from './pet/bubble-scroll.js';
document.body.style.background = '#edf3f6';
const preview = document.querySelector('#bubble-copy');
const scroll = createBubbleScroll({viewport: preview, renderText: renderSubtitleText});
const timers = [];
const writer = createTypewriter({
  language: 'bilingual',
  setTimer(callback) { timers.push(callback); return callback; },
  clearTimer(callback) { const index = timers.indexOf(callback); if (index >= 0) timers.splice(index, 1); },
  onText(text, update) { scroll.updateText(text, update); },
});
window.startSubtitle = (segment) => writer.start([segment]);
window.tickSubtitle = () => timers.shift()?.();
window.finishSubtitle = () => { while (timers.length) timers.shift()(); };
window.cancelSubtitle = () => writer.cancel('已取消当前回复。');
window.setSubtitleLanguage = (language) => writer.updateLanguage(language);
startSubtitle({text: 'おかえりなさい。', translation: '欢迎回来。'});
window.bilingualLines = () => {
  const lines = [...preview.querySelectorAll('.subtitle-line')];
  return lines.map(line => line.getBoundingClientRect().y);
};
let selected = PRODUCT_MENU_ACTIONS.subtitleZh;
window.commits = [];
window.menuErrors = [];
const controller = new PetContextMenu({
  menu: document.querySelector('#pet-context-menu'),
  invoke: async (command, payload) => {
    commits.push({command, ...payload});
    if (command === 'activate_pet_context_menu_action') {
      if (window.failSave) throw new Error('save failed');
      selected = payload.actionId;
      writer.updateLanguage(payload.actionId.split('.').at(-1));
    }
  },
  onError: (...error) => menuErrors.push(error),
});
window.openMenu = (x, y, options = {}) => controller.openAt(x, y, {
  schemaVersion: 1, availableActions: Object.values(PRODUCT_MENU_ACTIONS),
  checkedActions: [selected],
}, options);
"""

server = http.server.ThreadingHTTPServer(
    ('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(frontend)))
threading.Thread(target=server.serve_forever, daemon=True).start()
output = Path(tempfile.mkdtemp(prefix='sakura-subtitle-menu-'))
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL', 'msedge'))
        page = browser.new_page(viewport={'width': 900, 'height': 700})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/app.js', lambda route: route.fulfill(content_type='text/javascript', body=boot))
        page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
        page.wait_for_function('Boolean(window.openMenu)')
        page.add_style_tag(content='''
          #pet-stage { visibility: visible; --stage-width: 900px; --stage-height: 700px; }
          #pet-stage > :not(#chat-bubble) { display: none; }
          #chat-bubble { left: 120px; top: 35px; width: 350px; height: 120px; }
        ''')
        page.evaluate('tickSubtitle()')
        expect(page.locator('.subtitle-line--zh')).to_have_text('欢')
        expect(page.locator('.subtitle-line--ja')).to_have_text('お')
        page.evaluate("setSubtitleLanguage('bilingual_ja'); tickSubtitle()")
        expect(page.locator('.subtitle-line--primary')).to_have_attribute('lang', 'ja-JP')
        expect(page.locator('.subtitle-line--primary')).to_have_text('お')
        expect(page.locator('.subtitle-line--secondary')).to_have_attribute('lang', 'zh-CN')
        expect(page.locator('.subtitle-line--secondary')).to_have_text('欢')
        page.evaluate("setSubtitleLanguage('bilingual'); tickSubtitle()")
        lines = page.evaluate('bilingualLines()')
        assert len(lines) == 2 and lines[1] > lines[0], lines
        expect(page.locator('#bubble-copy')).to_be_visible()
        page.evaluate('finishSubtitle()')
        expect(page.locator('.subtitle-line--zh')).to_have_text('欢迎回来。')
        expect(page.locator('.subtitle-line--ja')).to_have_text('おかえりなさい。')
        page.evaluate("startSubtitle({translation: '这是一段较长的中文，用来验证窄气泡中两种语言分别换行。\\n中文的第二段。', text: '日本語の長い文章も、中国語の文章と混ざらずに表示されます。\\n日本語の第二段。'})")
        page.evaluate('for (let index = 0; index < 24; index++) tickSubtitle()')
        first_pair = page.locator('.subtitle-pair').first.inner_text()
        page.evaluate('finishSubtitle()')
        assert page.locator('.subtitle-pair').first.inner_text() == first_pair
        def assert_interleaved():
            rows = page.locator('#bubble-copy .subtitle-pair').count()
            assert rows >= 3, rows
            lines = page.locator('#bubble-copy .subtitle-line').evaluate_all('''elements => elements.map(el => ({
              lang: el.lang, text: el.textContent, y: el.getBoundingClientRect().y,
              height: el.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(el).lineHeight),
            }))''')
            for index, line in enumerate(lines):
                assert line['lang'] == ('zh-CN' if index % 2 == 0 else 'ja-JP'), lines
                assert line['height'] <= line['lineHeight'] + 1, line
                if index:
                    assert line['y'] >= lines[index - 1]['y'] + lines[index - 1]['height'] - 1, lines
            assert ''.join(line['text'] for line in lines[::2]) == '这是一段较长的中文，用来验证窄气泡中两种语言分别换行。中文的第二段。'
            assert ''.join(line['text'] for line in lines[1::2]) == '日本語の長い文章も、中国語の文章と混ざらずに表示されます。日本語の第二段。'
            return rows

        previous_rows = assert_interleaved()
        page.locator('#chat-bubble').evaluate("el => el.style.width = '260px'")
        page.wait_for_function('(count) => document.querySelectorAll("#bubble-copy .subtitle-pair").length > count', arg=previous_rows)
        assert_interleaved()
        page.locator('#chat-bubble').evaluate("el => { el.style.width = '350px'; el.style.height = '370px'; }")
        page.wait_for_function('(count) => document.querySelectorAll("#bubble-copy .subtitle-pair").length === count', arg=previous_rows)
        page.locator('#bubble-copy').evaluate('el => el.scrollTop = 0')
        page.screenshot(path=str(output / 'bilingual-interleaved.png'), clip={'x': 100, 'y': 20, 'width': 400, 'height': 400})
        page.locator('#chat-bubble').evaluate("el => el.style.height = '120px'")
        page.evaluate('cancelSubtitle()')
        expect(page.locator('.subtitle-line')).to_have_count(0)
        expect(page.locator('#bubble-copy')).to_have_text('已取消当前回复。')
        page.evaluate("startSubtitle({text: 'おかえりなさい。', translation: '欢迎回来。'}); finishSubtitle()")
        trigger = page.locator('[data-menu-submenu]')
        submenu = page.locator('#pet-language-menu')
        chinese = submenu.get_by_role('menuitemradio', name='中文', exact=True)
        japanese = submenu.get_by_role('menuitemradio', name='日文', exact=True)
        bilingual = submenu.get_by_role('menuitemradio', name='双语1', exact=True)
        bilingual_ja = submenu.get_by_role('menuitemradio', name='双语2', exact=True)

        # Moving through the gap must not dismiss the submenu; the native crop includes it.
        for x, side in [(120, 'right'), (860, 'left')]:
            page.evaluate('(x) => openMenu(x, 180)', x)
            trigger.hover()
            expect(submenu).to_be_visible()
            expect(submenu).to_have_attribute('data-side', side)
            target = bilingual.bounding_box()
            page.mouse.move(target['x'] + target['width'] / 2, target['y'] + target['height'] / 2, steps=15)
            expect(submenu).to_be_visible()
            rect = page.evaluate('commits.filter(c => c.command === "set_pet_context_menu_surface").at(-1).rect')
            box = submenu.bounding_box()
            assert box['x'] >= rect[0] - 1 and box['x'] + box['width'] <= rect[0] + rect[2] + 1
            assert box['y'] >= rect[1] - 1 and box['y'] + box['height'] <= rect[1] + rect[3] + 1
            assert box['x'] >= 0 and box['x'] + box['width'] <= 900
            bilingual.click()
            expect(page.locator('#pet-context-menu')).to_be_hidden()

        page.evaluate('openMenu(120, 180, {focusFirst: true})')
        page.keyboard.press('ArrowDown')
        expect(trigger).to_be_focused()
        page.keyboard.press('ArrowRight')
        expect(bilingual).to_be_focused()
        expect(bilingual).to_have_attribute('aria-checked', 'true')
        page.keyboard.press('ArrowUp')
        expect(japanese).to_be_focused()
        page.keyboard.press('Enter')
        page.evaluate('openMenu(120, 180, {focusFirst: true})')
        trigger.click()
        expect(japanese).to_have_attribute('aria-checked', 'true')
        expect(chinese).to_have_attribute('aria-checked', 'false')
        page.keyboard.press('Escape')
        expect(submenu).to_be_hidden()
        expect(trigger).to_be_focused()
        expect(page.locator('#pet-context-menu')).to_be_visible()
        page.keyboard.press('ArrowRight')
        page.keyboard.press('ArrowLeft')
        expect(trigger).to_be_focused()

        # A rejected save must preserve the committed selection when the menu reopens.
        page.evaluate('window.failSave = true')
        trigger.click()
        chinese.click()
        page.wait_for_function('menuErrors.length === 1')
        page.evaluate('openMenu(120, 180)')
        trigger.hover()
        expect(japanese).to_have_attribute('aria-checked', 'true')
        page.evaluate('window.failSave = false')
        bilingual.click()
        page.evaluate('openMenu(120, 180)')
        trigger.hover()
        expect(bilingual).to_have_attribute('aria-checked', 'true')
        page.screenshot(path=str(output / 'display-language.png'), clip={'x': 100, 'y': 20, 'width': 400, 'height': 425})
        bilingual_ja.click()
        page.evaluate('openMenu(120, 180)')
        trigger.hover()
        expect(bilingual_ja).to_have_attribute('aria-checked', 'true')
        expect(bilingual).to_have_attribute('aria-checked', 'false')
        page.evaluate("startSubtitle({text: 'おかえりなさい。', translation: '欢迎回来。'}); finishSubtitle()")
        expect(page.locator('.subtitle-line--primary')).to_have_attribute('lang', 'ja-JP')
        expect(page.locator('.subtitle-line--secondary')).to_have_attribute('lang', 'zh-CN')
        page.screenshot(path=str(output / 'display-language-dual2.png'), clip={'x': 100, 'y': 20, 'width': 400, 'height': 425})
        page.keyboard.press('Escape')
        page.keyboard.press('Escape')
        expect(page.locator('#pet-context-menu')).to_be_hidden()
        assert errors == [], errors
        page.locator('#chat-bubble').evaluate("el => el.style.height = '370px'")
        page.evaluate("startSubtitle({translation: '欢迎回来。今天过得怎么样？如果有开心的事情，或者想聊聊的烦恼，都可以慢慢告诉我。', text: 'おかえりなさい。今日はどんな一日でしたか？うれしかったことも、気になっていることも、ゆっくり聞かせてください。'}); finishSubtitle()")
        page.locator('#bubble-copy').evaluate('el => el.scrollTop = 0')
        page.screenshot(path=str(output / 'bilingual-interleaved.png'), clip={'x': 100, 'y': 20, 'width': 400, 'height': 400})

        history = browser.new_page(viewport={'width': 700, 'height': 700})
        history.on('pageerror', lambda error: errors.append(str(error)))
        history.add_init_script('''
          window.historyEvents = new Map();
          window.__TAURI__ = {
            event: {listen: async (name, callback) => { historyEvents.set(name, callback); return () => {}; }},
            core: {invoke: async (command) => {
              if (command === 'history_bootstrap') return {subtitleLanguage: 'bilingual', coreGenerationId: 'test', characterId: 'test'};
              if (command === 'history_page') return {schemaVersion: 1, coreGenerationId: 'test', characterId: 'test',
                totalCount: 1, hasMore: false, beforeCursor: null, entries: [{entryId: 'test', turnId: 'test',
                  kind: 'assistant', origin: 'chat', createdAt: '2026-09-22T12:00:00Z', payload: {segments: [{
                    translation: '欢迎回来。今天过得怎么样？如果有开心的事情，或者想聊聊的烦恼，都可以慢慢告诉我。',
                    text: 'おかえりなさい。今日はどんな一日でしたか？うれしかったことも、気になっていることも、ゆっくり聞かせてください。',
                  }]}}]};
            }}
          };
        ''')
        history.goto(f'http://127.0.0.1:{server.server_port}/history/', wait_until='networkidle')
        history.wait_for_function('document.querySelectorAll(".subtitle-pair").length > 1')
        history_rows = history.locator('.subtitle-pair').count()
        history.set_viewport_size({'width': 460, 'height': 700})
        history.wait_for_function('(rows) => document.querySelectorAll(".subtitle-pair").length > rows', arg=history_rows)
        languages = history.locator('.subtitle-line').evaluate_all('elements => elements.map(el => el.lang)')
        assert languages == ['zh-CN', 'ja-JP'] * (len(languages) // 2), languages
        history.evaluate('historyEvents.get("sakura://subtitle-language-changed")({payload: "zh"})')
        expect(history.locator('.subtitle-pair')).to_have_count(0)
        expect(history.locator('.entry-bubble')).to_contain_text('欢迎回来。')
        history.evaluate('historyEvents.get("sakura://subtitle-language-changed")({payload: "bilingual"})')
        history.wait_for_function('document.querySelectorAll(".subtitle-pair").length > 1')
        history.evaluate('historyEvents.get("sakura://subtitle-language-changed")({payload: "bilingual_ja"})')
        expect(history.locator('.subtitle-line--primary').first).to_have_attribute('lang', 'ja-JP')
        expect(history.locator('.subtitle-line--secondary').first).to_have_attribute('lang', 'zh-CN')
        languages = history.locator('.subtitle-line').evaluate_all('elements => elements.map(el => el.lang)')
        assert languages == ['ja-JP', 'zh-CN'] * (len(languages) // 2), languages
        assert errors == [], errors
        browser.close()
    print(f'PASS: mouse, keyboard, selection, failed save, native crop; screenshot: {output}')
finally:
    server.shutdown()
    server.server_close()
