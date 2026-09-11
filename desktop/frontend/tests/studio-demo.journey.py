"""Opt-in browser regression against the real Studio controller and the Demo bridge."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


frontend = Path(__file__).resolve().parents[1]
server = http.server.ThreadingHTTPServer(
    ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(frontend))
)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_port}/prototypes/studio-demo/")
        expect(page.locator("#loading")).to_be_hidden()
        frame = page.frame_locator("#settings")
        # Closing a focused option used to re-enter closeMenu through focusout.
        for name in ["樱", "N.A.V.I.", "樱", "N.A.V.I."]:
            frame.locator(".studio-character-controls .custom-select__trigger").click()
            frame.get_by_role("option", name=f"{name}，已添加", exact=True).click()
            expect(frame.locator("#displayName")).to_have_value(name)
            expect(page.locator("#loading")).to_be_hidden()
        frame.get_by_role("button", name="角色形态", exact=True).click()
        frame.get_by_role("button", name="添加形态", exact=True).click()
        dialog = frame.get_by_role("dialog", name="添加形态")
        dialog.get_by_role("textbox", name="形态名称").fill("第二套立绘")
        dialog.get_by_role("button", name="添加", exact=True).click()
        frame.get_by_role("button", name="添加图片", exact=True).click()
        frame.get_by_role("button", name="使用示例资源", exact=True).click()
        expect(frame.locator("#expressionList .expression-row")).to_have_count(1)
        frame.get_by_role("button", name="保存", exact=True).click()
        expect(frame.get_by_role("button", name="放弃修改", exact=True)).to_be_disabled()
        page.get_by_role("button", name="设置预览", exact=True).click()
        expect(page.locator("#loading")).to_be_hidden()
        options = frame.locator("#visualSelect option")
        expect(options).to_have_text(["日常立绘", "第二套立绘"])
        assert not errors, errors
        browser.close()
finally:
    server.shutdown()
    server.server_close()
