"""在聊天记录页面选择日期，用隔离的历史库验证定位、翻页与角色切换。"""

from datetime import datetime, timedelta, timezone
import functools
import http.server
import os
from pathlib import Path
import sys
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.core_host.history import HistoryBoundary
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    (ROOT / "temp").mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            root = Path(directory)
            store = TimelineStore(StoragePaths(root).timeline_database())
            store.initialize()
            for prefix, character, start, count in [
                ("older", "sakura", datetime(2026, 9, 3, 9), 1),
                ("selected", "sakura", datetime(2026, 10, 8), 55),
                ("latest", "sakura", datetime(2026, 10, 20, 12), 60),
                ("other", "other", datetime(2026, 10, 9, 12), 1),
            ]:
                store.append_many([
                    NewTimelineEntry(f"{prefix}-{i}", f"{prefix}-{i}", character, TimelineKind.HUMAN,
                                     "chat", (start + timedelta(minutes=i)).astimezone(timezone.utc).isoformat(),
                                     {"text": f"{prefix} 记录 {i}"})
                    for i in range(count)
                ])
            current = ["sakura"]
            boundary = HistoryBoundary("generation", "credential", root, character_provider=lambda: current[0])

            def invoke(_source, command, args=None):
                if command == "history_bootstrap":
                    return {"coreGenerationId": "generation", "characterId": current[0], "assistantName": "Sakura"}
                if command == "history_dates":
                    result = boundary.handle({
                        "protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": "dates",
                        "generationId": "generation", "generationCredential": "credential", "name": "ui.history.dates",
                        "payload": args["payload"],
                    })
                    if not result["ok"]:
                        raise RuntimeError(result["error"])
                    return result["payload"]
                if command == "history_page":
                    request = args["request"]
                    result = boundary.handle({
                        "protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": "history",
                        "generationId": "generation", "generationCredential": "credential", "name": "ui.history.page",
                        "payload": {"expectedCharacterId": request["characterId"], "limit": 50,
                                    "beforeCursor": request.get("beforeCursor"),
                                    "afterCursor": request.get("afterCursor"), "fromDate": request.get("fromDate")},
                    })
                    if not result["ok"]:
                        raise RuntimeError(result["error"])
                    return result["payload"]

            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or "msedge")
            page = browser.new_page(viewport={"width": 620, "height": 680})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_binding("nativeHistory", invoke)
            page.add_init_script("""
                window.historyEvents = new Map();
                window.__TAURI__ = {
                  event: {listen: async (name, callback) => { historyEvents.set(name, callback); return () => {}; }},
                  core: {invoke: async (command, args) => {
                    if (command === 'plugin_frontend_module') return {source: `
                      export function createTtsController() {
                        return {start() {}, dispose() {}, cancel() {}, setInputCaptureActive() {}};
                      }`};
                    const result = await nativeHistory(command, args);
                    if (command === 'history_page' && args.request.fromDate && window.holdDate) {
                      await new Promise(resolve => { window.releaseDate = resolve; });
                    }
                    return result;
                  }}
                };
            """)
            page.goto(f"http://127.0.0.1:{server.server_port}/desktop/frontend/history/")
            rows = page.locator(".history-entry")
            jump = page.get_by_role("button", name="跳转到日期", exact=True)
            dates = page.get_by_role("dialog", name="跳转到日期", exact=True)
            more = page.get_by_role("button", name="聊天记录更多操作", exact=True)
            refresh = page.get_by_role("menuitem", name="刷新记录", exact=True)
            latest = page.get_by_role("button", name="回到最新", exact=True)
            expect(rows).to_have_count(50)
            expect(rows.last).to_contain_text("latest 记录 59")
            expect(jump).to_be_enabled()

            jump.click()
            expect(dates.get_by_role("button", name="2026年10月8日", exact=True)).to_have_accessible_description("有聊天记录")
            expect(dates.get_by_role("button", name="2026年10月20日", exact=True)).to_have_accessible_description("有聊天记录")
            expect(dates.get_by_role("button", name="2026年10月18日", exact=True)).to_have_accessible_description("")
            expect(dates.get_by_role("button", name="2026年10月9日", exact=True)).to_have_accessible_description("")
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-date-markers.png"))
            dates.locator("[data-month]").click()
            expect(dates.get_by_role("button", name="2026年10月8日", exact=True)).to_be_visible()
            assert dates.locator(".date-range-days").evaluate("el => el.getAnimations().length") == 0
            page.keyboard.press("Escape")
            expect(rows.last).to_contain_text("latest 记录 59")
            expect(jump).to_be_focused()

            jump.click()
            expect(dates).to_be_visible()
            jump.click()
            expect(dates).not_to_be_visible()
            expect(jump).to_be_focused()
            page.keyboard.press("Enter")
            expect(dates).to_be_visible()
            jump.focus()
            page.keyboard.press("Space")
            expect(dates).not_to_be_visible()

            jump.click()
            days = dates.locator(".date-range-days button")
            expect(days).to_have_count(35)
            expect(days.last).to_have_attribute("data-date", "2026-10-31")
            for _ in range(2):
                dates.get_by_role("button", name="上个月", exact=True).click()
            expect(days).to_have_count(42)
            expect(dates.get_by_role("button", name="2026年8月31日", exact=True)).to_be_visible()
            for _ in range(6):
                dates.get_by_role("button", name="上个月", exact=True).click()
            expect(days).to_have_count(28)
            expect(days.first).to_have_attribute("data-date", "2026-02-01")
            expect(days.last).to_have_attribute("data-date", "2026-02-28")
            page.keyboard.press("Escape")

            jump.click()
            page.mouse.click(15, 15)
            expect(dates).not_to_be_visible()
            expect(rows.last).to_contain_text("latest 记录 59")

            jump.click()
            dates.get_by_role("button", name="2026年10月8日", exact=True).click()
            expect(rows).to_have_count(50)
            expect(rows.first).to_contain_text("selected 记录 0")
            expect(rows.last).to_contain_text("selected 记录 49")
            page.wait_for_function("""() => {
                const scroll = document.querySelector('#history-scroll');
                const first = document.querySelector('.history-entry');
                return Math.abs(first.getBoundingClientRect().top - scroll.getBoundingClientRect().top
                    - scroll.clientTop - parseFloat(getComputedStyle(scroll).paddingTop)) < 2;
            }""")
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-date-result.png"))

            scroll = page.locator("#history-scroll")
            previous_position = scroll.evaluate("""el => {
                el.scrollTop = el.scrollHeight;
                return document.querySelector('[data-entry-id="selected-49"]').getBoundingClientRect().top;
            }""")
            expect(rows).to_have_count(100)
            expect(rows.last).to_contain_text("latest 记录 44")
            assert abs(page.locator('[data-entry-id="selected-49"]').evaluate(
                "el => el.getBoundingClientRect().top") - previous_position) < 2
            scroll.evaluate("el => el.scrollTop = el.scrollHeight")
            expect(rows).to_have_count(115)
            expect(rows.last).to_contain_text("latest 记录 59")

            scroll.evaluate("el => el.scrollTop = 0")
            expect(rows).to_have_count(116)
            expect(rows.first).to_contain_text("older 记录 0")
            ids = rows.evaluate_all("rows => rows.map(row => row.dataset.entryId)")
            assert len(set(ids)) == 116

            jump.click()
            dates.get_by_role("button", name="下个月", exact=True).click()
            dates.get_by_role("button", name="2026年10月8日", exact=True).click()
            expect(rows.first).to_contain_text("selected 记录 0")
            page.get_by_role("button", name="加载更早记录", exact=True).click()
            expect(rows).to_have_count(51)
            expect(rows.first).to_contain_text("older 记录 0")
            page.get_by_role("button", name="加载后续记录", exact=True).click()
            expect(rows).to_have_count(101)
            expect(rows.last).to_contain_text("latest 记录 44")

            expect(latest).to_be_visible()
            latest.click()
            expect(rows.last).to_contain_text("latest 记录 59")
            expect(latest).not_to_be_visible()
            scroll_top = page.locator("#history-scroll").evaluate("el => el.scrollTop")
            jump.click()
            dates.get_by_role("button", name="2026年10月18日", exact=True).click()
            expect(page.locator("#history-status")).to_contain_text("没有聊天记录")
            expect(rows).to_have_count(50)
            expect(rows.last).to_contain_text("latest 记录 59")
            assert abs(page.locator("#history-scroll").evaluate("el => el.scrollTop") - scroll_top) < 2

            jump.click()
            dates.get_by_role("button", name="上个月", exact=True).click()
            expect(dates.get_by_role("button", name="2026年9月3日", exact=True)).to_have_accessible_description("有聊天记录")
            dates.get_by_role("button", name="2026年9月3日", exact=True).focus()
            page.keyboard.press("ArrowRight")
            expect(dates.get_by_role("button", name="2026年9月4日", exact=True)).to_be_focused()
            page.keyboard.press("ArrowLeft")
            page.set_viewport_size({"width": 480, "height": 440})
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-date-calendar-small.png"))
            page.keyboard.press("Enter")
            expect(rows).to_have_count(50)
            expect(rows.first).to_contain_text("older 记录 0")

            page.set_viewport_size({"width": 620, "height": 680})
            more.click()
            refresh.click()
            expect(rows.last).to_contain_text("latest 记录 59")
            page.evaluate("window.holdDate = true")
            jump.click()
            dates.get_by_role("button", name="2026年10月8日", exact=True).click()
            page.wait_for_function("typeof window.releaseDate === 'function'")
            expect(jump).to_be_disabled()
            current[0] = "other"
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload: {reset: true, ready: true}})")
            expect(rows).to_have_count(0)
            page.evaluate("window.holdDate = false; window.releaseDate()")
            expect(rows).to_have_count(1)
            expect(rows.first).to_contain_text("other 记录 0")

            jump.click()
            expect(dates.get_by_role("button", name="2026年10月9日", exact=True)).to_have_accessible_description("有聊天记录")
            expect(dates.get_by_role("button", name="2026年10月8日", exact=True)).to_have_accessible_description("")
            current[0] = "sakura"
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload: {reset: true, ready: true}})")
            expect(dates).not_to_be_visible()
            expect(rows.last).to_contain_text("latest 记录 59")

            page.evaluate("window.holdDate = true; window.releaseDate = null")
            jump.click()
            dates.get_by_role("button", name="2026年10月8日", exact=True).click()
            page.wait_for_function("typeof window.releaseDate === 'function'")
            more.click()
            page.get_by_role("menuitem", name="关闭窗口", exact=True).click()
            page.evaluate("""async () => {
                window.dispatchEvent(new PageTransitionEvent('pagehide'));
                window.holdDate = false;
                window.releaseDate();
                await new Promise(requestAnimationFrame);
            }""")
            expect(rows.last).to_contain_text("latest 记录 59")
            assert errors == [], errors
            browser.close()
        print("PASS: day-start jump, bidirectional scrolling, calendar, empty dates, refresh, character switch and close")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
