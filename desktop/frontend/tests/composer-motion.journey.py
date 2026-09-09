"""Opt-in browser journey against shipping HTML/CSS/controllers, with only the host mocked.
Run with bundled Python and Playwright: composer-motion.journey.py [--browser chromium|webkit]
"""
import argparse
import functools
import http.server
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--browser', choices=['chromium', 'webkit'], default='chromium')
parser.add_argument('--channel', default=None, help='Use an installed browser, e.g. msedge')
args = parser.parse_args()
frontend = Path(__file__).resolve().parents[1]
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(frontend)))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{server.server_port}'
boot = (frontend / 'tests/fixtures/composer-motion-boot.js').read_text(encoding='utf-8')
output = Path(tempfile.mkdtemp(prefix=f'sakura-motion-{args.browser}-'))
try:
    with sync_playwright() as p:
        browser = getattr(p, args.browser).launch(channel=args.channel)
        page = browser.new_page(viewport={'width': 900, 'height': 800}, reduced_motion='reduce')
        failures = []
        page.on('pageerror', lambda error: (failures.append(str(error)), print('PAGE ERROR:', error, flush=True)))
        page.route('**/app.js', lambda route: route.fulfill(content_type='text/javascript', body=boot))
        page.goto(url, wait_until='networkidle')
        page.wait_for_function('Boolean(window.motionJourney)')
        assert page.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
        icon = page.locator('#voice-mic .sakura-morph-icon')
        page.locator('#composer-input').fill('已有草稿')
        page.evaluate('motionJourney.start()')
        before = icon.locator('path').get_attribute('d')
        page.wait_for_timeout(110)
        assert icon.locator('path').get_attribute('d') != before, 'real path morph runs under system reduce'
        page.wait_for_timeout(350)
        rotation = icon.locator('svg').evaluate('(el) => getComputedStyle(el).transform')
        page.wait_for_timeout(130)
        assert rotation != icon.locator('svg').evaluate('(el) => getComputedStyle(el).transform')
        assert page.locator('#composer-input').evaluate('(el) => el.inert && el.readOnly')
        page.evaluate('motionJourney.ready()')
        page.wait_for_function('motionJourney.state() === "recording"')
        page.wait_for_timeout(550)
        assert icon.get_attribute('data-icon') == 'stop'
        page.evaluate('motionJourney.level(.8)')
        page.wait_for_timeout(180)
        canvas = page.locator('#voice-waveform').evaluate('(el) => el.toDataURL()')
        page.evaluate('motionJourney.level(.3)')
        page.wait_for_timeout(180)
        assert canvas != page.locator('#voice-waveform').evaluate('(el) => el.toDataURL()')
        page.screenshot(path=str(output / 'recording.png'))
        page.evaluate('motionJourney.stop()')
        page.evaluate('motionJourney.poll({state:"succeeded", text:"识别文字"})')
        assert page.locator('#composer-input').input_value() == '已有草稿 识别文字'
        assert icon.get_attribute('data-icon') == 'check'
        assert not page.locator('#composer-input').evaluate('(el) => el.inert')
        page.wait_for_timeout(650)
        assert icon.get_attribute('data-icon') == 'mic'
        page.evaluate('motionJourney.start()')
        page.evaluate('motionJourney.cancel()')
        page.evaluate('motionJourney.ready()')
        page.wait_for_timeout(550)
        assert icon.get_attribute('data-icon') == 'mic'
        assert page.evaluate('motionJourney.writes.length') == 1
        # Failed recognition also returns to mic; no success feedback or extra draft insertion.
        page.evaluate('motionJourney.start()'); page.evaluate('motionJourney.ready()')
        page.wait_for_function('motionJourney.state() === "recording"')
        page.evaluate('motionJourney.stop()'); page.evaluate('motionJourney.poll({state:"failed", errorCode:"ASR_NO_SPEECH"})')
        assert icon.get_attribute('data-icon') == 'mic'
        assert page.evaluate('motionJourney.errors.length === 1 && motionJourney.writes.length === 1')
        page.locator('#composer-input').fill('第一行\n第二行\n第三行')
        page.wait_for_timeout(300)
        form = page.locator('#composer').bounding_box()
        for selector in ['#voice-mic', '#composer-send']:
            rect = page.locator(selector).bounding_box()
            assert form['y'] <= rect['y'] and rect['y'] + rect['height'] <= form['y'] + form['height'] + 1
        page.screenshot(path=str(output / 'expanded.png'))
        # Voice feedback occupies the bottom toolbar without collapsing or covering the draft.
        draft = page.locator('#composer-input').bounding_box()
        page.evaluate('motionJourney.start()')
        page.wait_for_timeout(300)
        assert page.locator('#composer').bounding_box() == form
        assert page.locator('#composer-input').bounding_box() == draft
        assert page.locator('#composer-input').evaluate('(el) => getComputedStyle(el).opacity === "1"')
        for phase, selector in [('preparing', '#voice-status'), ('recording', '#voice-recording'), ('recognizing', '#voice-status')]:
            if phase == 'recording':
                page.evaluate('motionJourney.ready()')
                page.wait_for_function('motionJourney.state() === "recording"')
                for level in [.2, .4, .7, .3, .5, .75, .25]:
                    page.evaluate('(level) => motionJourney.level(level)', level)
                    page.wait_for_timeout(85)
            elif phase == 'recognizing':
                page.evaluate('motionJourney.stop()')
            page.wait_for_timeout(300)
            feedback = page.locator(selector).bounding_box()
            stop = page.locator('#voice-mic').bounding_box()
            assert feedback['y'] >= draft['y'] + draft['height'], 'voice feedback must stay below text'
            assert abs(feedback['y'] + feedback['height'] / 2 - stop['y'] - stop['height'] / 2) <= 2
            assert feedback['x'] + feedback['width'] <= stop['x'], 'waveform must not overlap stop'
            assert page.locator('#composer').bounding_box() == form
            page.screenshot(path=str(output / f'expanded-{phase}.png'))
        page.evaluate('motionJourney.cancel()')
        page.wait_for_timeout(300)
        assert page.locator('#composer-input').input_value() == '第一行\n第二行\n第三行'
        page.locator('#composer-send').click()
        send = page.locator('#composer-send .sakura-morph-icon')
        assert send.get_attribute('data-icon') == 'loader-circle'
        page.mouse.move(10, 10); page.locator('#composer-send').hover()
        assert send.get_attribute('data-icon') == 'x'
        page.evaluate('motionJourney.send.setBusy(false)')
        assert send.get_attribute('data-icon') == 'send-horizontal'
        page.evaluate('motionJourney.send.complete()')
        assert send.get_attribute('data-icon') == 'check'
        page.evaluate('motionJourney.send.setBusy(true)')
        page.wait_for_timeout(750)
        assert send.get_attribute('data-icon') == 'loader-circle'
        page.evaluate('motionJourney.startText()')
        page.wait_for_timeout(100)
        assert page.evaluate('motionJourney.subtitles.at(-1).length > 0 && motionJourney.subtitles.at(-1).length < 9')
        page.wait_for_timeout(420)
        assert page.evaluate('new Set(motionJourney.waitingFrames).size > 1')
        page.evaluate('motionJourney.dispose()')
        page.wait_for_timeout(1000)
        assert page.evaluate('document.getAnimations().filter(a => a.playState === "running").length') == 0
        assert not failures, failures
        # Verify authored animation rules across the other shipping surfaces, not CSS source text.
        for stylesheet, markup, selector in [
            ('core/icons.css', '<button><span class="sakura-icon icon-settings"></span></button>', '.icon-settings'),
            ('settings/styles.css', '<div class="plugin-detail-enter">Details</div>', '.plugin-detail-enter'),
            ('history/styles.css', '<div class="history-shell" data-loading="true"><span class="history-status">Loading</span></div>', '.history-status'),
            ('runtime-log/styles.css', '<div class="log-record is-new">Record</div>', '.log-record'),
            ('onboarding/styles.css', '<p class="progress-copy is-updating">Progress</p>', '.progress-copy'),
        ]:
            page.goto(url + '/index.html', wait_until='networkidle')
            page.evaluate('motionJourney.dispose()')
            page.set_content(f'<link rel="stylesheet" href="{url}/{stylesheet}">{markup}')
            page.wait_for_load_state('networkidle')
            if stylesheet == 'core/icons.css':
                page.locator('button').hover()
            assert page.locator(selector).evaluate('''async el => {
                const animation = el.getAnimations()[0];
                if (!animation) return false;
                animation.currentTime = 0;
                animation.play();
                const sample = () => { const style = getComputedStyle(el); return `${style.opacity}:${style.transform}`; };
                const before = sample();
                await new Promise(resolve => setTimeout(resolve, 60));
                return animation.playState === 'running' && before !== sample();
            }'''), stylesheet
        browser.close()
        print(f'PASS {args.browser}: real morph/spin/waveform under reduced motion; ASR success/cancel/failure; draft safety; send interruption; subtitle/waiting motion; disposal; page animations. Screenshots: {output}')
finally:
    server.shutdown()
    server.server_close()
