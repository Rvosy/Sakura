"""Check shared scrollbar rendering and input across production window styles."""

import functools
import http.server
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
SURFACES = {
    "styles.css": ["bubble-copy", "composer textarea", "composer-attachment-strip", "composer-tool-dock__list"],
    "settings/styles.css": ["nav-list", "page-scroll", "plugin-dialog-body", "record-list", "record-editor", "custom-select__menu is-open", "memory-record-list"],
    "settings/plugin-marketplace.css": ["market-content", "drawer-scroll"],
    "studio/styles.css": ["studio-visual-dialog"],
    "onboarding/styles.css": ["macos-open-help-view"],
    "history/styles.css": ["history-scroll", "data-transfer-preview"],
    "runtime-log/styles.css": ["log-scroll", "custom-select__menu is-open"],
    "error-dialog/styles.css": ["sakura-error-details"],
    "whats-new/styles.css": ["updates-content"],
    "capture/capture.css": ["capture-scroll"],
    "studio/color-picker.css": ["color-picker-scroll"],
}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "desktop/frontend")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            # Headless Chromium otherwise hides the bars, making drag checks ineffective.
            browser = playwright.chromium.launch(channel="msedge", ignore_default_args=["--hide-scrollbars"])
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.route("**/scrollbar-fixture", lambda route: route.fulfill(body="<!doctype html><html></html>", content_type="text/html"))
            for border, primary in [("#accfde", "#4b9ac4"), ("#465c73", "#8dc8e9")]:
                reference = None
                for sheet, classes in SURFACES.items():
                    for name in classes:
                        page.goto(origin + "/scrollbar-fixture")
                        links = [sheet, "core/data-transfer.css"]
                        if sheet == "settings/plugin-marketplace.css":
                            links.insert(0, "settings/styles.css")
                        content = f'<div id="probe" tabindex="0" class="{name}" open><div style="width:700px;height:1000px">Scroll content</div></div>'
                        if name == "composer textarea":
                            content = '<div class="composer"><textarea id="probe" wrap="off">' + ("Long input " * 30 + "\n") * 50 + '</textarea></div>'
                        page.set_content(''.join(f'<link rel="stylesheet" href="{origin}/{link}">' for link in links) + f'''
                            <style>
                            :root {{ --primary:{primary}; --border:{border}; --sakura-primary:{primary}; --sakura-border:{border}; }}
                            #probe {{ position:fixed; inset:30px auto auto 30px; display:block; visibility:visible;
                                opacity:1; transform:none; margin:0; padding:0; border:0; width:300px; height:220px;
                                min-height:0; max-height:none; max-width:none; overflow:auto; resize:none; scroll-behavior:auto; }}
                            </style>{content}''')
                        page.wait_for_function("getComputedStyle(document.querySelector('#probe'), '::-webkit-scrollbar').width === '6px'")
                        probe = page.locator("#probe")
                        appearance = probe.evaluate('''el => {
                            const bar = getComputedStyle(el, '::-webkit-scrollbar');
                            const thumb = getComputedStyle(el, '::-webkit-scrollbar-thumb');
                            const button = getComputedStyle(el, '::-webkit-scrollbar-button');
                            return { width:bar.width, height:bar.height, color:thumb.backgroundColor,
                                radius:thumb.borderRadius, border:thumb.borderWidth,
                                button:button.display, buttonWidth:button.width, buttonHeight:button.height,
                                nativeWidth:getComputedStyle(el).scrollbarWidth, nativeColor:getComputedStyle(el).scrollbarColor };
                        }''')
                        assert appearance["button"] == "none", (sheet, name, appearance)
                        assert appearance["buttonWidth"] == appearance["buttonHeight"] == "0px"
                        assert appearance["width"] == appearance["height"] == "6px"
                        assert appearance["nativeWidth"] == appearance["nativeColor"] == "auto"
                        if reference is None:
                            reference = appearance
                        assert appearance == reference, (sheet, name, appearance, reference)
                        box = probe.bounding_box()
                        page.mouse.move(box["x"] + box["width"] - 3, box["y"] + 15)
                        page.mouse.down()
                        page.mouse.move(box["x"] + box["width"] - 3, box["y"] + 150, steps=12)
                        page.mouse.up()
                        try:
                            page.wait_for_function("document.querySelector('#probe').scrollTop > 100")
                        except Exception:
                            print("Thumb drag failed:", sheet, name, probe.evaluate("el => ({top:el.scrollTop,left:el.scrollLeft, height:el.clientHeight, scrollHeight:el.scrollHeight})"))
                            output = Path(tempfile.mkdtemp(prefix="sakura-scrollbars-")) / "drag-failure.png"
                            page.screenshot(path=str(output))
                            print(f"Failure screenshot: {output}")
                            raise
                        probe.hover()
                        page.mouse.wheel(200, 240)
                        page.wait_for_function("document.querySelector('#probe').scrollTop > 0 && document.querySelector('#probe').scrollLeft > 0")
            browser.close()
        print(f"Unified scrollbars passed: {sum(map(len, SURFACES.values()))} surfaces, light/dark themes, wheel and thumb dragging.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
