"""Verify settings navigation fits the window and remains accessible without a scrollbar."""

import functools
import http.server
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = """
import { installSettingsSidebar } from './sidebar.js';
import { installSettingsWindowControls } from './window-controls.js';
import { waitForRuntimeFonts } from '../core/font-loader.js';
installSettingsSidebar({document, storage:localStorage});
await installSettingsWindowControls({document, useCustomTitlebar:true, onClose:()=>{}, onError:error=>{throw error;},
  appWindow:{setDecorations:async()=>{},onResized:async()=>()=>{},isMaximized:async()=>false}}).ready;
document.querySelector('[data-page="open-help"]').hidden = false;
document.getElementById('sidebarVersion').textContent = 'v1.4.0';
await waitForRuntimeFonts();
document.body.dataset.ready = 'true';
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "desktop/frontend")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = Path(tempfile.mkdtemp(prefix="sakura-settings-sidebar-"))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", ignore_default_args=["--hide-scrollbars"])
            page = browser.new_page(reduced_motion="reduce")
            page.route("**/settings/settings.js", lambda route: route.fulfill(body=SCRIPT, content_type="text/javascript"))
            for width, height in [(1200, 800), (900, 720), (1000, 640)]:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/")
                expect(page.locator("body")).to_have_attribute("data-ready", "true")
                navigation = page.locator("#settingsNavigation")
                assert navigation.evaluate("el => el.scrollHeight <= el.clientHeight"), (width, height, navigation.evaluate("el => [el.scrollHeight, el.clientHeight]"))
                assert navigation.evaluate("el => getComputedStyle(el, '::-webkit-scrollbar').display") == "none"
                items = navigation.locator(".nav-item:visible")
                assert items.count() == 13
                for item in items.all():
                    expect(item).to_be_in_viewport(ratio=1)
                expect(page.locator(".nav-footer")).to_be_in_viewport(ratio=1)
                expect(page.locator("#saveButton")).to_be_in_viewport(ratio=1)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"{width}x{height}.png"))
                page.locator("#controlPanelWidth").evaluate("el => el.value = '73'")
                page.get_by_role("button", name="收起侧栏", exact=True).click()
                expect(page.get_by_role("button", name="展开侧栏", exact=True)).to_have_attribute("aria-expanded", "false")
                expect(page.locator("#controlPanelWidth")).to_have_value("73")
                for item in items.all():
                    expect(item).to_be_in_viewport(ratio=1)
                page.get_by_role("button", name="展开侧栏", exact=True).click()
                expect(page.locator("#controlPanelWidth")).to_have_value("73")

            # Plugin-contributed navigation may exceed even the largest window.
            page.evaluate("""() => {
                const group = document.querySelector('#navgrp-system').parentElement;
                for(let i=0;i<12;i++) {
                    const item = document.querySelector('[data-page="about"]').cloneNode(true);
                    item.dataset.page = 'fixture-'+i;
                    item.querySelector('.nav-item-label').textContent = '插件 '+i;
                    group.append(item);
                }
            }""")
            assert navigation.evaluate("el => el.scrollHeight > el.clientHeight")
            assert navigation.evaluate("el => getComputedStyle(el, '::-webkit-scrollbar').display") == "none"
            navigation.hover()
            page.mouse.wheel(0, 500)
            page.wait_for_function("document.querySelector('#settingsNavigation').scrollTop > 0")
            navigation.locator(".nav-item").last.focus()
            expect(navigation.locator(".nav-item").last).to_be_in_viewport(ratio=1)
            page.keyboard.press("Shift+Tab")
            expect(navigation.locator(".nav-item").nth(-2)).to_be_focused()
            expect(navigation.locator(".nav-item").nth(-2)).to_be_in_viewport(ratio=1)
            browser.close()
        print(f"Settings sidebar passed: default/minimum/short windows, collapse, preserved input and plugin overflow access. Screenshots: {output}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
