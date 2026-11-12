"""Exercise the production update summary with an isolated native bridge."""
import functools
import http.server
import json
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    entries = json.loads((ROOT / "desktop/frontend/data/whats-new.json").read_text(encoding="utf-8"))["versions"]
    latest = entries[0]["version"]
    snapshot = {"version": latest, "seenVersion": entries[1]["version"], "entries": entries[:1], "themeTokens": {}}
    calls = []
    fail_close = False
    fail_bootstrap = False
    def invoke(command, args=None):
        calls.append((command, args))
        if command == "whats_new_bootstrap":
            if fail_bootstrap:
                raise RuntimeError("fixture: unreadable ui.json")
            return snapshot
        if command == "close_whats_new" and fail_close:
            raise RuntimeError("fixture: disk is read-only")
        return None
    output = Path(os.environ.get("SAKURA_VISUAL_OUTPUT", ROOT / "temp/whats-new"))
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 520, "height": 380}, device_scale_factor=2)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_function("nativeInvoke", invoke)
            page.add_init_script("""
                window.nativeEvents = {};
                window.__TAURI__ = {
                    core: { invoke: (command, args) => window.nativeInvoke(command, args) },
                    event: { listen: async (event, callback) => {
                        window.nativeEvents[event] = callback;
                        return () => delete window.nativeEvents[event];
                    } }
                };
            """)
            url = origin + "/desktop/frontend/whats-new/?startup=1"
            page.goto(url)
            expect(page.get_by_role("heading", name=f"已更新至 v{latest}")).to_be_visible()
            expect(page.locator(".version li")).to_have_count(len(entries[0]["items"]))
            expect(page.locator("#close")).not_to_be_focused()
            page.evaluate("document.fonts.ready")
            assert not any(command == "close_whats_new" for command, _ in calls)
            page.screenshot(path=str(output / "light.png"))
            expect(page.get_by_role("button", name=f"查看 v{latest} 详细更新")).to_have_count(0)
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            expect(page.locator("#close")).to_be_focused()
            page.get_by_role("button", name="完整更新日志").click()
            page.wait_for_function("true")
            assert ("whats_new_open_details", {"version": None}) in calls
            def drag_text(locator):
                page.evaluate("getSelection().removeAllRanges()")
                box = locator.bounding_box()
                page.mouse.move(box["x"] + 1, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] - 1, box["y"] + box["height"] / 2, steps=12)
                page.mouse.up()
                return page.evaluate("getSelection().toString()")
            selected = drag_text(page.locator(".update-text").first)
            assert selected and selected in entries[0]["items"][0]["text"]
            page.screenshot(path=str(output / "selection-light.png"))
            for selector in ["#title", ".version h2", ".kind"]:
                assert drag_text(page.locator(selector).first) == "", selector
            fail_close = True
            page.get_by_role("button", name="知道了").click()
            expect(page.locator("dialog")).to_be_visible()
            expect(page.locator("dialog pre")).to_contain_text("fixture: disk is read-only")
            page.locator("dialog").press("Escape")
            fail_close = False
            page.evaluate("window.nativeEvents['sakura://whats-new-close-requested']()")
            assert calls[-1] == ("close_whats_new", {"acknowledge": True})

            snapshot["themeTokens"] = {"primary": "#8dc8e9", "primaryHover": "#aedcf4", "text": "#e2ecf5", "secondaryText": "#a9bcd1", "pageBackground": "#1c2532", "panelBackground": "#243142", "inputBackground": "#18212c", "border": "#465c73"}
            page.goto(url)
            expect(page.locator("#close")).to_be_visible()
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(output / "dark.png"))
            assert drag_text(page.locator(".update-text").first)
            page.screenshot(path=str(output / "selection-dark.png"))
            snapshot["entries"] = [{"version": f"1.3.{v}", "items": entries[0]["items"]} for v in range(6, 1, -1)]
            page.set_viewport_size({"width": 340, "height": 400})
            page.goto(url)
            expect(page.locator(".version")).to_have_count(3)
            page.get_by_role("button", name="查看 v1.3.6 详细更新").click()
            page.wait_for_function("true")
            assert ("whats_new_open_details", {"version": "1.3.6"}) in calls
            page.locator("#earlier").click()
            expect(page.locator(".version")).to_have_count(5)
            expect(page.locator("#close")).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "narrow.png"))
            page.locator("#earlier").click()
            expect(page.locator(".version")).to_have_count(3)
            page.goto(origin + "/desktop/frontend/whats-new/")
            expect(page.get_by_role("heading", name="近期更新")).to_be_visible()
            fail_bootstrap = True
            page.reload()
            expect(page.locator("dialog")).to_be_visible()
            expect(page.locator("dialog pre")).to_contain_text("fixture: unreadable ui.json")
            page.locator("dialog").press("Escape")
            page.locator("#close").click()
            page.wait_for_function("true")
            assert calls[-1] == ("close_whats_new", {"acknowledge": False})

            # The review page also loads the production document and script.
            page.set_viewport_size({"width": 1000, "height": 760})
            page.goto(origin + "/desktop/frontend/prototypes/whats-new/")
            preview = page.frame_locator("#preview")
            expect(preview.get_by_role("heading", name=f"已更新至 v{latest}")).to_be_visible()
            def preview_height():
                return page.locator("#preview").bounding_box()["height"]

            def assert_content_fits():
                assert preview.locator("#content").evaluate("el => el.scrollHeight <= el.clientHeight + 1")
                expect(preview.locator(".update-text").last).to_be_in_viewport()
                expect(preview.locator("#close")).to_be_in_viewport()

            assert_content_fits()
            single_height = preview_height()
            assert single_height < 620
            page.screenshot(path=str(output / "adaptive-single.png"))
            page.locator("#theme").select_option("dark")
            expect(preview.locator("#close")).to_be_visible()
            page.locator("#mode").select_option("manual")
            expect(preview.get_by_role("heading", name="近期更新")).to_be_visible()
            preview.locator("#close").click()
            expect(page.locator("#preview")).to_be_hidden()
            page.locator("#reopen").click()
            expect(preview.get_by_role("heading", name="近期更新")).to_be_visible()
            page.locator("#scenario").select_option("multiple")
            expect(preview.locator(".version")).to_have_count(3)
            expect(page.locator("#preview")).to_have_css("height", "620px")
            page.screenshot(path=str(output / "multi-top.png"))
            preview.get_by_role("button", name=f"展开更早的 {len(entries) - 3} 个版本").click()
            expect(preview.locator(".version")).to_have_count(len(entries))
            expect(preview.get_by_role("heading", name="近期更新")).to_be_in_viewport()
            expect(preview.locator("#close")).to_be_in_viewport()
            page.screenshot(path=str(output / "multi-expanded.png"))
            preview.get_by_role("button", name="收起更早版本").scroll_into_view_if_needed()
            expect(preview.get_by_role("heading", name="近期更新")).to_be_in_viewport()
            expect(preview.locator("#close")).to_be_in_viewport()
            page.screenshot(path=str(output / "multi-bottom.png"))
            preview.get_by_role("button", name="收起更早版本").click()
            expect(preview.locator(".version")).to_have_count(3)

            # Vary real release entries to cover short content and two-version overflow.
            fixture_entries = entries[:2]
            page.route("**/data/whats-new.json", lambda route: route.fulfill(json={"versions": fixture_entries}))
            preview_url = origin + "/desktop/frontend/prototypes/whats-new/?scenario=multiple&mode=manual"
            page.goto(preview_url)
            expect(preview.locator(".version")).to_have_count(2)
            expect(page.locator("#preview")).to_have_css("height", "620px")
            assert preview.locator("#content").evaluate("el => el.scrollHeight > el.clientHeight")
            expect(preview.locator("#close")).to_be_in_viewport()
            page.screenshot(path=str(output / "adaptive-two.png"))

            fixture_entries = [{**entries[0], "items": entries[0]["items"][:1]}]
            page.goto(preview_url)
            expect(preview.locator(".version li")).to_have_count(1)
            expect(preview.locator("#close")).to_be_visible()
            assert_content_fits()
            assert preview_height() < single_height
            page.screenshot(path=str(output / "adaptive-short.png"))

            fixture_entries = [{**entry, "items": entry["items"][:1]} for entry in entries[:4]]
            page.goto(preview_url)
            expect(preview.locator(".version")).to_have_count(3)
            expect(preview.locator("#close")).to_be_visible()
            collapsed_height = preview_height()
            assert_content_fits()
            preview.locator("#earlier").click()
            expect(preview.locator(".version")).to_have_count(4)
            page.wait_for_function("height => document.querySelector('#preview').getBoundingClientRect().height > height", arg=collapsed_height)
            assert preview_height() <= 620
            expect(preview.locator("#close")).to_be_in_viewport()
            preview.locator("#earlier").click()
            page.wait_for_function("height => document.querySelector('#preview').getBoundingClientRect().height === height", arg=collapsed_height)
            assert_content_fits()
            assert not errors, errors
            browser.close()
        print(f"Update summary journey passed; screenshots: {output}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
