"""用隔离历史库验证全文搜索、结果定位和迟到请求的处理。"""

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
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            root = Path(directory)
            store = TimelineStore(StoragePaths(root).timeline_database())
            store.initialize()
            base = datetime(2026, 10, 8, tzinfo=timezone.utc)

            def entry(entry_id, created, text, character="sakura"):
                return NewTimelineEntry(entry_id, entry_id, character, TimelineKind.HUMAN, "chat", created.isoformat(), {"text": text})

            store.append_many([
                entry("older", base - timedelta(days=1), "散步之前的聊天"),
                *[entry(f"match-{i}", base + timedelta(minutes=i), f"春天的散步计划 {i}" +
                        ("，沿路慢慢记录风景" * 40 + "，预算 100%_" if i == 0 else "，<img src=x>" if i == 50 else "")) for i in range(55)],
                entry("context", base + timedelta(minutes=30, seconds=30), "明天下雨，记得带伞"),
                NewTimelineEntry("reply", "reply", "sakura", TimelineKind.ASSISTANT, "chat", (base + timedelta(hours=2)).isoformat(),
                                 {"segments": [{"text": "桜を見に行こう", "translation": "春天一起去赏花吧", "tone": "中性",
                                                "portrait": "neutral", "suppressTts": False}]}),
                *[entry(f"latest-{i}", base + timedelta(days=12, minutes=i), f"最近的聊天 {i}") for i in range(60)],
                entry("other", base, "其他角色的春天", "other"),
            ])
            current = ["sakura"]
            boundary = HistoryBoundary("generation", "credential", root, character_provider=lambda: current[0])

            def invoke(_source, command, args=None):
                if command == "history_bootstrap":
                    return {"coreGenerationId": "generation", "characterId": current[0],
                            "assistantName": "Sakura" if current[0] == "sakura" else "另一个角色"}
                if command == "history_page":
                    request = args["request"]
                    result = boundary.handle({
                        "protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": "history",
                        "generationId": "generation", "generationCredential": "credential", "name": "ui.history.page",
                        "payload": {"expectedCharacterId": request["characterId"], "limit": 50,
                                    **{key: request.get(key) for key in ("beforeCursor", "afterCursor", "fromDate", "fromEntryId", "query")}},
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
                    if (command === 'history_page' && args.request.query
                        && (args.request.query === '延迟查询' || window.holdNextSearch)) {
                      window.holdNextSearch = false;
                      await new Promise(resolve => { window.releaseSearch = resolve; });
                    }
                    if (command === 'history_page' && args.request.fromEntryId && window.holdEntry) {
                      await new Promise(resolve => { window.releaseEntry = resolve; });
                    }
                    return result;
                  }}
                };
            """)
            page.goto(f"http://127.0.0.1:{server.server_port}/desktop/frontend/history/")
            rows = page.locator("#history-list .history-entry")
            input = page.get_by_role("searchbox", name="搜索聊天内容", exact=True)
            panel = page.get_by_role("region", name="聊天记录搜索结果", exact=True)
            results = panel.locator(".history-search-result")
            trigger = page.get_by_role("button", name="搜索聊天记录", exact=True)
            expect(rows.last).to_contain_text("最近的聊天 59")
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-reading.png"))
            page.evaluate("""() => {
              window.searchHeights = [];
              const sample = () => {
                searchHeights.push(document.querySelector('#history-search').getBoundingClientRect().height);
                if (searchHeights.length < 30) requestAnimationFrame(sample);
              };
              requestAnimationFrame(sample);
            }""")
            trigger.click()
            expect(input).to_be_visible()
            expect(input).not_to_be_focused()
            input.click()
            expect(input).to_be_focused()
            heights = page.evaluate("searchHeights")
            assert any(0 < height < max(heights) for height in heights), heights
            expect(page.locator(".history-search-field")).to_have_css("box-shadow", "none")
            trigger.click()
            expect(input).not_to_be_visible()
            page.keyboard.press("Control+f")
            expect(input).to_be_focused()

            def search(query):
                input.fill(query)
                input.press("Enter")

            # 连续提交同一个关键词，较慢的旧请求不能重复追加结果。
            page.evaluate("window.holdNextSearch = true")
            search("春天")
            page.wait_for_function("typeof window.releaseSearch === 'function'")
            input.press("Enter")
            expect(results).to_have_count(50)
            page.evaluate("""async () => {
              window.releaseSearch(); window.releaseSearch = null;
              await new Promise(requestAnimationFrame);
            }""")
            expect(results).to_have_count(50)
            expect(page.locator("#history-search-summary")).to_contain_text("56 条结果")
            expect(results.first).to_contain_text("春天一起去赏花吧")
            expect(results.first.locator("mark")).to_have_text("春天")
            selection_color = input.evaluate("el => getComputedStyle(el, '::selection').backgroundColor")
            expect(results.first.locator("mark")).to_have_css("background-color", selection_color)
            expect(panel.get_by_text("<img src=x>", exact=False)).to_be_visible()
            expect(panel.locator("img")).to_have_count(0)
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-search-results.png"))
            page.get_by_role("button", name="加载更多结果", exact=True).click()
            expect(results).to_have_count(56)
            ids = results.evaluate_all("items => items.map(item => item.dataset.entryId)")
            assert len(set(ids)) == 56
            # 定位到具体消息，同时能继续读到不匹配关键词的上下文。
            page.evaluate("window.holdEntry = true")
            panel.locator('[data-entry-id="match-30"]').dblclick()
            page.wait_for_function("typeof window.releaseEntry === 'function'")
            page.evaluate("window.holdEntry = false; window.releaseEntry()")
            expect(panel).not_to_be_visible()
            expect(rows.first).to_have_attribute("data-entry-id", "match-30")
            expect(page.locator('#history-list [data-entry-id="context"]')).to_contain_text("记得带伞")
            expect(rows.first).to_be_focused()
            # 定位提示使用主题主色，自动结束后不妨碍继续阅读。
            bubble = rows.first.locator(".entry-bubble")
            assert bubble.evaluate("el => getComputedStyle(el, '::before').backgroundColor") == "rgb(75, 154, 196)"
            page.screenshot(path=str(ROOT / "temp/history-search-context.png"))
            page.evaluate("async () => (await import('../core/theme.js')).applyTheme({primary: '#357a59'})")
            assert bubble.evaluate("el => getComputedStyle(el, '::before').backgroundColor") == "rgb(53, 122, 89)"
            page.wait_for_function("() => getComputedStyle(document.querySelector('#history-list .entry-bubble'), '::before').content === 'none'")
            expect(bubble).to_have_css("box-shadow", "none")
            page.get_by_role("button", name="加载更早记录", exact=True).click()
            expect(rows.first).to_have_attribute("data-entry-id", "older")
            page.locator("#history-scroll").evaluate("el => el.scrollTop = el.scrollHeight")
            expect(rows.last).to_contain_text("最近的聊天 59")
            page.get_by_role("button", name="返回结果", exact=True).click()
            expect(results).to_have_count(56)
            assert panel.evaluate("el => el.scrollTop") > 0
            new_selection_color = input.evaluate("el => getComputedStyle(el, '::selection').backgroundColor")
            assert new_selection_color != selection_color
            expect(results.first.locator("mark")).to_have_css("background-color", new_selection_color)
            panel.locator('[data-entry-id="reply"]').click()
            expect(rows.first).to_have_attribute("data-entry-id", "reply")
            page.keyboard.press("Escape")
            expect(input).not_to_be_visible()
            assert rows.first.locator(".entry-bubble").evaluate("el => getComputedStyle(el, '::before').content") == "none"
            page.locator("#history-scroll").evaluate("el => el.scrollTop = el.scrollHeight")
            expect(rows.last).to_contain_text("最近的聊天 59")
            page.evaluate("async () => (await import('../core/theme.js')).applyTheme()")

            trigger.click()
            search("%_")
            expect(results).to_have_count(1)
            expect(results.first).to_contain_text("100%_")
            input.fill("")
            expect(panel).not_to_be_visible()
            expect(input).to_be_visible()
            search("桜")
            expect(results).to_have_count(1)
            expect(results.first).to_contain_text("桜を見に行こう")
            search("不存在的内容")
            expect(page.locator("#history-search-summary")).to_contain_text("没有找到匹配")
            expect(results).to_have_count(0)

            search("延迟查询")
            page.wait_for_function("typeof window.releaseSearch === 'function'")
            search("%_")
            expect(results).to_have_count(1)
            page.evaluate("window.releaseSearch(); window.releaseSearch = null")
            expect(results.first).to_contain_text("100%_")
            page.set_viewport_size({"width": 480, "height": 440})
            page.screenshot(animations="disabled", path=str(ROOT / "temp/history-search-small.png"))

            search("延迟查询")
            page.wait_for_function("typeof window.releaseSearch === 'function'")
            trigger.click()
            page.evaluate("window.releaseSearch(); window.releaseSearch = null")
            expect(panel).not_to_be_visible()
            expect(rows.last).to_contain_text("最近的聊天 59")

            trigger.click()
            search("延迟查询")
            page.wait_for_function("typeof window.releaseSearch === 'function'")
            current[0] = "other"
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload: {reset: true, ready: true}})")
            page.evaluate("window.releaseSearch(); window.releaseSearch = null")
            expect(input).not_to_be_visible()
            expect(rows).to_have_count(1)
            expect(rows.first).to_contain_text("其他角色的春天")
            expect(page.locator("#history-character")).to_have_text("另一个角色")
            trigger.click()
            search("春天")
            expect(results).to_have_count(1)
            expect(results.first).to_contain_text("其他角色的春天")
            page.keyboard.press("Escape")
            page.emulate_media(reduced_motion="reduce")
            trigger.click()
            expect(input).to_be_visible()
            assert page.locator("#history-search").evaluate("el => el.getAnimations().length") == 0
            search("春天")
            results.first.click()
            expect(rows.first).to_be_focused()
            input.click()
            assert rows.first.locator(".entry-bubble").evaluate("el => getComputedStyle(el, '::before').content") == "none"
            assert errors == [], errors
            browser.close()
        print("PASS: search motion, focus, theme colors, temporary highlight, paging, context and stale requests")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
