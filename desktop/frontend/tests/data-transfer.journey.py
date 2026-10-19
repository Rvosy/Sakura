"""用真实文件和历史存储验证导入预览、重复导入及 TXT 导出。"""
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
from app.storage.timeline import TimelineStore, NewTimelineEntry, TimelineKind


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as directory, sync_playwright() as playwright:
            root = Path(directory)
            store = TimelineStore(StoragePaths(root).timeline_database())
            store.initialize()
            store.append(NewTimelineEntry("entry", "turn", "sakura", TimelineKind.HUMAN, "chat",
                "2026-01-01T10:00:00+08:00", {"text": "多行内容\n用户：这仍是同一条消息"}))
            boundary = HistoryBoundary("generation", "credential", root,
                character_provider=lambda: "sakura")
            calls = []
            def transfer(_source, request):
                calls.append(request["action"])
                request["expectedGenerationId"] = "generation"
                return boundary._transfer(request)
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or "msedge")
            page = browser.new_page(viewport={"width": 620, "height": 680})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_binding("nativeTransfer", transfer)
            page.expose_binding("nativeChoose", lambda _source, _command, request:
                str(root / ("history.txt" if request["format"] == "txt" else "history.json")))
            page.route("**/transfer-test", lambda route: route.fulfill(content_type="text/html", body='''
                <html><head><link rel="stylesheet" href="/desktop/frontend/history/styles.css">
                <link rel="stylesheet" href="/desktop/frontend/core/data-transfer.css"></head><body></body></html>'''))
            page.goto(f"http://127.0.0.1:{server.server_port}/transfer-test")
            page.evaluate("""async () => {
                const {createDataTransferButtons} = await import('/desktop/frontend/core/data-transfer.js');
                window.currentRole = 'sakura';
                document.body.append(createDataTransferButtons({document, history: true,
                    invoke: window.nativeChoose, characterId: () => window.currentRole,
                    transfer: window.nativeTransfer}));
            }""")
            page.get_by_role("button", name="导出", exact=True).click()
            page.get_by_role("button", name="选择保存位置").click()
            expect(page.locator("[data-result]")).to_contain_text("已导出 1 条")
            assert (root / "history.json").exists()
            page.get_by_label("导出格式").select_option("txt")
            page.get_by_role("button", name="选择保存位置").click()
            expect(page.locator("[data-result]")).to_contain_text("已导出 1 条")
            assert "用户：这仍是同一条消息" in (root / "history.txt").read_text(encoding="utf-8")
            page.get_by_role("button", name="关闭", exact=True).click()
            target = root / "target"
            target_store = TimelineStore(StoragePaths(target).timeline_database())
            target_store.initialize()
            boundary = HistoryBoundary("generation", "credential", target,
                character_provider=lambda: "sakura")
            page.get_by_role("button", name="导入", exact=True).click()
            page.get_by_role("button", name="选择文件").click()
            expect(page.locator("[data-result]")).to_contain_text("可新增 1 条")
            assert target_store.read_all("sakura") == []
            page.get_by_role("button", name="确认导入").click()
            expect(page.locator("[data-result]")).to_contain_text("已导入 1 条")
            assert len(target_store.read_all("sakura")) == 1
            page.get_by_role("button", name="选择文件").click()
            expect(page.locator("[data-result]")).to_contain_text("重复 1 条")
            page.screenshot(path=str(ROOT / "temp/data-transfer-ui.png"))
            page.evaluate("window.currentRole = 'other'")
            page.get_by_role("button", name="确认导入").click()
            expect(page.locator("[data-result]")).to_contain_text("角色或插件已变化")
            assert calls == ["export", "export", "preview", "import", "preview"]
            assert not errors, errors
            browser.close()
            print("PASS: JSON/TXT export, import preview, duplicate import and role change")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
