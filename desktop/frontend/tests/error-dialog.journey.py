"""Exercise the production error dialog with an isolated native-window bridge."""
import functools
import http.server
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = r"""
window.fixture = {
  calls: [], listeners: {},
  snapshot: {
    title: '语音合成失败', message: '模型加载失败', themeTokens: {},
    details: 'TTS_SYNTHESIS_FAILED|voice|model|FileNotFoundError: C:\\角色\\voice.ckpt\n'
      + '异常链：PluginApiError: synthesis failed\nCaused by: FileNotFoundError: voice.ckpt\n\n'
      + '调用栈：Traceback (most recent call last):\n'
      + '  File "C:\\Sakura\\provider.py", line 42, in load_model\n'.repeat(400)
      + 'FileNotFoundError: ROOT_CAUSE_AT_END\nAuthorization: Bearer fixture-private-key\n'
      + '<img src=x onerror="window.injected=true">'
  }
};
window.__TAURI__ = {
  core: { invoke: async command => {
    fixture.calls.push(command);
    if (command === 'error_dialog_bootstrap') return fixture.snapshot;
  } },
  event: { listen: async (name, callback) => {
    fixture.listeners[name] = callback;
    return () => { delete fixture.listeners[name]; };
  } }
};
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check_details(page):
    dialog = page.get_by_role('dialog')
    diagnostic = dialog.locator('pre')
    expect(dialog).to_be_visible()
    expect(diagnostic).to_be_visible()
    expect(diagnostic).to_contain_text('ROOT_CAUSE_AT_END')
    expect(diagnostic).not_to_contain_text('fixture-private-key')
    assert diagnostic.inner_text().startswith('FileNotFoundError: C:')
    assert diagnostic.locator('img').count() == 0
    assert page.evaluate('window.injected') is None
    assert diagnostic.evaluate('el => [el, el.parentElement].some(area => area.scrollHeight > area.clientHeight && getComputedStyle(area).overflowY === "auto")')
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth')
    expect(dialog.get_by_role('button', name='关闭', exact=True)).to_be_in_viewport()
    return dialog, diagnostic


def run():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('SAKURA_BROWSER_CHANNEL') or ('msedge' if sys.platform == 'win32' else None))
            context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(BRIDGE)
            page.set_viewport_size({'width': 880, 'height': 640})
            page.goto(f'http://127.0.0.1:{server.server_port}/desktop/frontend/error-dialog/index.html')
            page.wait_for_function("fixture.calls.includes('reveal_error_dialog')")
            for width, height in [(880, 640), (360, 640), (360, 320)]:
                page.set_viewport_size({'width': width, 'height': height})
                dialog, diagnostic = check_details(page)
                dialog.get_by_role('button', name='复制详情', exact=True).click()
                expect(dialog.get_by_role('status')).to_have_text('已复制')
                assert page.evaluate('navigator.clipboard.readText()') == diagnostic.inner_text()
                output = os.environ.get('SAKURA_ERROR_DIALOG_SCREENSHOT_DIR')
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(output) / f'error-dialog-{width}x{height}.png'))

            # A refresh of the same error respects manual disclosure; a new error expands.
            dialog.locator('summary').click()
            expect(diagnostic).not_to_be_visible()
            page.evaluate("fixture.listeners['sakura://error-dialog-updated']()")
            expect(diagnostic).not_to_be_visible()
            page.evaluate("fixture.snapshot.details += '\\nRecoveryError: rollback failed'; fixture.listeners['sakura://error-dialog-updated']()")
            expect(diagnostic).to_be_visible()
            expect(diagnostic).to_contain_text('RecoveryError: rollback failed')
            original = diagnostic.inner_text()
            page.evaluate("() => { navigator.clipboard.writeText = async () => { throw new Error('clipboard denied'); }; }")
            dialog.get_by_role('button', name='复制详情', exact=True).click()
            expect(dialog.get_by_role('status')).to_contain_text('clipboard denied')
            assert diagnostic.inner_text() == original
            page.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            page.wait_for_function("fixture.calls.includes('close_error_dialog')")

            # The same presenter also runs inside settings/history/studio pages.
            await_inline = """async () => {
              document.querySelector('link[rel=stylesheet]').href = '../settings/styles.css';
              const { createErrorDialog } = await import('../core/error-dialog.js');
              fixture.inline = createErrorDialog({document});
              fixture.inline.show({title: fixture.snapshot.title, message: fixture.snapshot.message,
                error: fixture.snapshot.details});
            }"""
            page.set_viewport_size({'width': 880, 'height': 640})
            page.evaluate(await_inline)
            page.wait_for_function("[...document.styleSheets].some(sheet => sheet.href?.endsWith('/settings/styles.css'))")
            for width in [880, 360]:
                page.set_viewport_size({'width': width, 'height': 640})
                check_details(page)
                if output:
                    page.screenshot(path=str(Path(output) / f'error-dialog-inline-{width}.png'))
            page.get_by_role('dialog').get_by_role('button', name='关闭', exact=True).click()
            expect(page.get_by_role('dialog')).not_to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print('Error dialog: expanded diagnostics, full copy, disclosure, native and inline layouts passed.')


if __name__ == '__main__':
    run()
