"""用真实文件和历史存储验证导入预览、重复导入及 TXT 导出。"""
import functools
import http.server
import json
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


def date_part(page, label, part):
    return page.get_by_role("textbox", name=f"{label}：{part}", exact=True)


def expect_date(page, label, value):
    for part, expected in zip(("年", "月", "日"), value.split("-")):
        expect(date_part(page, label, part)).to_have_value(expected)


def enter_date(page, label, value):
    for part, text in zip(("年", "月", "日"), value.split("-")):
        date_part(page, label, part).fill(text)
    date_part(page, label, "日").press("Tab")


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
            def choose(_source, _command, request):
                assert request["defaultName"] == f"history_sakura.{request['format']}"
                return str(root / request["defaultName"])
            page.expose_binding("nativeChoose", choose)
            page.route("**/transfer-test", lambda route: route.fulfill(content_type="text/html", body='''
                <html><head><link rel="stylesheet" href="/desktop/frontend/history/styles.css">
                <link rel="stylesheet" href="/desktop/frontend/core/data-transfer.css"></head><body></body></html>'''))
            page.goto(f"http://127.0.0.1:{server.server_port}/transfer-test")
            page.evaluate("""async () => {
                const {createDataTransferMenu} = await import('/desktop/frontend/core/data-transfer.js');
                const {installClickIconMotion} = await import('/desktop/frontend/core/icons.js');
                installClickIconMotion(document);
                document.body.dataset.iconMotion = 'click';
                window.currentRole = 'sakura';
                document.body.innerHTML = `<main class="history-shell"><header class="history-head">
                  <div class="history-heading"><h1>聊天记录</h1><p class="history-character">Sakura</p></div>
                  <div class="history-head-actions"></div></header><div class="history-toolbar"></div>
                  <div class="history-content"><section class="history-scroll">
                  <p class="entry-bubble entry-bubble-human">多行内容<br>用户：这仍是同一条消息</p></section></div></main>`;
                document.querySelector('.history-head-actions').append(createDataTransferMenu({document, history: true,
                    invoke: window.nativeChoose, characterId: () => window.currentRole,
                    transfer: async request => {
                      if (request.action === 'range' && window.rangeGate) await window.rangeGate;
                      return window.nativeTransfer(request);
                    }}));
            }""")
            menu = page.get_by_role("button", name="聊天记录更多操作")
            menu.focus()
            menu.press("ArrowDown")
            expect(page.get_by_role("menuitem", name="导出聊天记录…")).to_be_focused()
            page.screenshot(path=str(ROOT / "temp/data-transfer-menu.png"))
            page.get_by_role("menuitem", name="导出聊天记录…").press("Enter")
            dialog = page.locator(".data-transfer-dialog")
            page.screenshot(animations="disabled", path=str(ROOT / "temp/data-transfer-export.png"))
            range_button = page.locator(".date-range-trigger")
            backdrop_frames = page.evaluate("""async () => {
                const parent = document.querySelector('.data-transfer-dialog');
                const before = getComputedStyle(parent, '::backdrop').backgroundColor;
                document.querySelector('.date-range-trigger').click();
                const frames = [];
                const start = performance.now();
                while (performance.now() - start < 240) {
                  await new Promise(requestAnimationFrame);
                  const backdrop = getComputedStyle(parent, '::backdrop');
                  frames.push([backdrop.backgroundColor === before, backdrop.visibility]);
                }
                return frames;
            }""")
            assert all(stable and visibility == "visible" for stable, visibility in backdrop_frames)
            dates = page.get_by_role("dialog", name="选择日期范围")
            today = page.evaluate("""() => {
                const d = new Date();
                return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            }""")
            expect_date(page, "开始日期", today)
            expect_date(page, "结束日期", today)
            frames = page.evaluate("""async () => {
                window.rangeGate = new Promise(resolve => { window.releaseRange = resolve; });
                const panel = document.querySelector('.date-range-panel');
                const frames = [];
                panel.querySelector('[data-days="0"]').click();
                panel.querySelector('[data-field="startDate"] input').focus();
                const start = performance.now();
                while (performance.now() - start < 350) {
                  await new Promise(requestAnimationFrame);
                  const rect = panel.getBoundingClientRect();
                  frames.push([rect.y, rect.height]);
                  if (performance.now() - start > 120) window.releaseRange();
                }
                window.rangeGate = null;
                return frames;
            }""")
            assert max(y for y, _ in frames) - min(y for y, _ in frames) < 1
            assert max(h for _, h in frames) - min(h for _, h in frames) < 1
            expect_date(page, "开始日期", "2026-01-01")
            expect_date(page, "结束日期", "2026-01-01")
            dates.get_by_role("button", name="确定", exact=True).click()
            page.get_by_role("button", name="选择保存位置").click()
            expect(page.locator("[data-result]")).to_contain_text("已导出 1 条")
            assert (root / "history_sakura.json").exists()
            page.get_by_role("radio", name="迁移与恢复").focus()
            page.keyboard.press("ArrowDown")
            expect(page.get_by_role("radio", name="阅读与分享")).to_be_checked()
            page.get_by_role("button", name="选择保存位置").click()
            expect(page.locator("[data-result]")).to_contain_text("已导出 1 条")
            assert "用户：这仍是同一条消息" in (root / "history_sakura.txt").read_text(encoding="utf-8")
            range_button.click()
            dates.get_by_role("button", name="近 7 天", exact=True).click()
            expect(date_part(page, "开始日期", "年")).not_to_have_value("")
            page.evaluate("() => { window.rangeGate = new Promise(resolve => { window.releaseRange = resolve; }); }")
            dates.get_by_role("button", name="全部", exact=True).click()
            year = date_part(page, "开始日期", "年")
            month = date_part(page, "开始日期", "月")
            day = date_part(page, "开始日期", "日")
            year.focus()
            year.press_sequentially("20")
            page.evaluate("window.releaseRange(); window.rangeGate = null")
            expect(dates.get_by_role("button", name="全部", exact=True)).not_to_have_attribute("aria-busy", "true")
            expect(year).to_have_value("20")
            year.press_sequentially("26")
            expect(month).to_be_focused()
            month.press_sequentially("13")
            expect(month).to_have_value("12")
            expect(day).to_be_focused()
            for digit in "23456789":
                month.focus()
                month.press_sequentially(digit)
                expect(month).to_have_value(f"0{digit}")
                expect(day).to_be_focused()
            enter_date(page, "开始日期", "2024-02-29")
            expect_date(page, "开始日期", "2024-02-29")
            year.focus()
            year.press_sequentially("2026")
            expect_date(page, "开始日期", "2026-02-28")
            day.focus()
            day.press_sequentially("30")
            expect(day).to_have_value("28")
            expect(dates.get_by_role("status")).to_have_text("")
            day.press("Backspace")
            day.press("Tab")
            expect(day).to_have_value("28")
            year.focus()
            year.evaluate("""e => {
                const clipboardData = new DataTransfer();
                clipboardData.setData('text', '2024/02/29');
                e.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
            }""")
            expect_date(page, "开始日期", "2024-02-29")
            month.focus()
            month.press("ArrowUp")
            expect_date(page, "开始日期", "2024-03-29")
            for preset in ("当天", "近 7 天", "近 14 天", "近 30 天"):
                dates.get_by_role("button", name=preset, exact=True).click()
                enter_date(page, "开始日期", "2026-01-02")
                expect_date(page, "开始日期", "2026-01-02")
                expect(dates.get_by_role("button", name=preset, exact=True)).to_have_attribute("aria-pressed", "false")
            settled_background = """async e => {
                    await Promise.all(e.getAnimations().map(animation => animation.finished));
                    return getComputedStyle(e).backgroundColor;
                }"""
            for date in ("2026年1月2日", "2026年1月10日"):
                selected_day = dates.get_by_role("button", name=date, exact=True)
                background = selected_day.evaluate(settled_background)
                selected_day.hover()
                assert selected_day.evaluate(settled_background) == background
            enter_date(page, "结束日期", "2026-01-01")
            dates.get_by_role("button", name="确定", exact=True).click()
            expect(dates.get_by_role("status")).to_contain_text("结束日期不能早于")
            dates.get_by_role("button", name="2026年1月31日", exact=True).focus()
            page.keyboard.press("ArrowRight")
            expect(dates.get_by_role("button", name="2026年2月1日", exact=True)).to_be_focused()
            dates.get_by_role("button", name="上个月", exact=True).click()
            dates.get_by_role("button", name="2026年1月3日", exact=True).click()
            dates.get_by_role("button", name="确定", exact=True).click()
            expect(range_button).to_contain_text("2026/01/02 至 2026/01/03")
            range_button.click()
            enter_date(page, "开始日期", "2026-01-01")
            dates.get_by_role("button", name="取消", exact=True).click()
            expect(range_button).to_contain_text("2026/01/02 至 2026/01/03")
            page.get_by_role("button", name="选择保存位置").click()
            expect(page.locator("[data-result]")).to_contain_text("已导出 0 条")
            range_button.click()
            page.set_viewport_size({"width": 1426, "height": 1149})
            page.screenshot(animations="disabled", path=str(ROOT / "temp/data-transfer-calendar-large.png"))
            bounds = dates.bounding_box()
            assert abs(bounds["x"] + bounds["width"] / 2 - 713) < 1
            assert abs(bounds["y"] + bounds["height"] / 2 - 574.5) < 1
            confirm = dates.get_by_role("button", name="确定", exact=True)
            confirm.hover()
            # 主按钮悬停保持主题主色，输入焦点只显示在外层日期卡片。
            expected_hover = confirm.evaluate("e => getComputedStyle(e).getPropertyValue('--transfer-primary-hover').trim()")
            page.wait_for_function("""color => {
                const b = document.querySelector('[data-apply]');
                const probe = document.createElement('span'); probe.style.color = color; b.append(probe);
                const expected = getComputedStyle(probe).color; probe.remove();
                return getComputedStyle(b).backgroundColor === expected;
            }""", arg=expected_hover)
            date_part(page, "开始日期", "年").focus()
            assert date_part(page, "开始日期", "年").evaluate("e => getComputedStyle(e).outlineStyle") == "none"
            assert date_part(page, "开始日期", "年").evaluate("e => getComputedStyle(e).boxShadow") == "none"
            page.set_viewport_size({"width": 620, "height": 680})
            date_part(page, "开始日期", "年").evaluate("e => e.select()")
            page.screenshot(animations="disabled", path=str(ROOT / "temp/data-transfer-date-selection.png"))
            dates.locator("h2").click()
            page.screenshot(animations="disabled", path=str(ROOT / "temp/data-transfer-calendar.png"))
            page.set_viewport_size({"width": 360, "height": 560})
            dates.get_by_role("button", name="取消", exact=True).click()
            range_button.click()
            page.screenshot(animations="disabled", path=str(ROOT / "temp/data-transfer-narrow.png"))
            assert dates.evaluate("e => e.scrollWidth <= e.clientWidth")
            dates.get_by_role("button", name="取消", exact=True).click()
            page.emulate_media(reduced_motion="reduce")
            range_button.click()
            assert dates.evaluate("e => e.getAnimations({subtree: true}).length") == 0
            dates.get_by_role("button", name="取消", exact=True).click()
            page.emulate_media(reduced_motion="no-preference")
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(menu).to_be_focused()
            page.set_viewport_size({"width": 620, "height": 680})
            target = root / "target"
            target_store = TimelineStore(StoragePaths(target).timeline_database())
            target_store.initialize()
            boundary = HistoryBoundary("generation", "credential", target,
                character_provider=lambda: "sakura")
            menu.click()
            page.get_by_role("menuitem", name="导入聊天记录…").click()
            page.get_by_role("button", name="选择文件").click()
            expect(page.locator("[data-counts] > div").first).to_contain_text("可新增1")
            assert target_store.read_all("sakura") == []
            page.get_by_role("button", name="更换文件").click()
            expect(page.get_by_role("button", name="确认导入")).to_be_enabled()
            page.get_by_role("button", name="确认导入").click()
            expect(page.locator("[data-result]")).to_contain_text("已导入 1 条")
            assert len(target_store.read_all("sakura")) == 1
            page.get_by_role("button", name="选择文件").click()
            expect(page.locator("[data-counts] > div").nth(1)).to_contain_text("重复跳过1")
            page.screenshot(path=str(ROOT / "temp/data-transfer-ui.png"))
            page.evaluate("window.currentRole = 'other'")
            page.get_by_role("button", name="确认导入").click()
            expect(page.locator("[data-result]")).to_contain_text("角色或插件已变化")
            assert calls == ["range", "export", "export", "range", "export", "preview", "preview", "import", "preview"]
            dialog.get_by_role("button", name="关闭", exact=True).click()
            # 关闭后到达的导入结果不能刷新已离开的界面。
            page.evaluate("""async () => {
                const {createDataTransferMenu} = await import('/desktop/frontend/core/data-transfer.js');
                document.body.replaceChildren(createDataTransferMenu({document,
                  characterId: () => 'sakura', invoke: async () => 'memory.json',
                  transfer: request => request.action === 'preview'
                    ? Promise.resolve({sourceCharacterId: 'sakura', added: 1, identical: 0, conflicts: 0})
                    : new Promise(resolve => { window.finishImport = resolve; }),
                  onImported: () => { window.importRefreshed = true; }}));
            }""")
            page.get_by_role("button", name="记忆更多操作").click()
            page.get_by_role("menuitem", name="导入记忆…").click()
            page.get_by_role("button", name="选择文件").click()
            page.get_by_role("button", name="确认导入").click()
            page.get_by_role("button", name="关闭", exact=True).click()
            page.evaluate("window.finishImport({sourceCharacterId: 'sakura', added: 1, identical: 0, conflicts: 0})")
            assert not page.evaluate("Boolean(window.importRefreshed)")
            run_memory_surface(page, server.server_port)
            assert not errors, errors
            browser.close()
            print("PASS: export, date range, import preview, duplicates, lifecycle and Memory page transfer")
    finally:
        server.shutdown()
        server.server_close()


def run_memory_surface(page, port):
    """从正式记忆页走通菜单和插件传输请求，避免入口只挂在占位界面。"""
    snapshot = {
        "schemaVersion": 1, "state": "ready", "reasonCode": "READY",
        "windowGeneration": 7, "coreGenerationId": "generation",
        "plugins": [{
            "installId": "memory-fixture", "pluginId": "memory-fixture", "name": "记忆",
            "version": "1.0.0", "enabled": True, "required": False, "supported": True,
            "source": "bundled", "canUninstall": False, "provides": [], "requires": [],
            "missingServices": [], "state": "active", "reasonCode": "ACTIVE",
            "sections": [{
                "sectionId": "archive", "title": "记忆", "surface": "memory", "reasonCode": "READY",
                "fields": [], "values": {}, "actions": [], "collections": [{
                    "collectionId": "entries", "scope": "character", "title": "记忆条目",
                    "columns": [{"key": "content", "label": "内容", "type": "string"}],
                    "fields": [], "filters": [], "searchable": True, "pageSize": 20,
                    "canCreate": True, "canUpdate": True, "canDelete": True, "canTransfer": True,
                }],
            }],
        }],
    }
    requests = []
    records = [{"itemId": "one", "values": {"content": "用户喜欢简洁的界面。"}}]

    def invoke(_source, command, args=None):
        if command == "data_transfer_choose":
            assert args["defaultName"] == f"memory_sakura.{args['format']}"
            return "memory.json"
        if command == "settings_plugins_get":
            return snapshot
        if command == "settings_plugins_collection":
            if args["operation"] == "query":
                return {"items": records, "total": len(records), "nextCursor": None}
            requests.append(args)
            action = args["payload"]["action"]
            if action == "export":
                return {"exported": len(records)}
            if action == "import":
                records.append({"itemId": "two", "values": {"content": "导入的记忆。"}})
            return {"sourceCharacterId": "sakura", "added": 1, "identical": 0, "conflicts": 0}
        raise AssertionError(command)

    page.expose_binding("memoryInvoke", invoke)
    script = """
      import {createPluginSettingsFeature} from './plugin-settings.js';
      import {installClickIconMotion} from '../core/icons.js';
      import {enhanceSelect, refreshSelect, closeSelects, focusSelect} from './select-control.js';
      installClickIconMotion(document);
      const feature = createPluginSettingsFeature({document, window, invoke: window.memoryInvoke,
        onDirty() {}, onError(error) { if (error) throw error; }, notify() {}, confirmAction: async () => true,
        enhanceSelect, refreshSelect, closeSelects, focusSelect, replayMotion() {},
        removeOverlayAfterExit: async e => e.remove(), showPage() {},
        isCharacterTransitioning: () => false, hasPendingCharacterSelection: () => false,
        currentCharacterId: () => 'sakura'});
      document.querySelectorAll('.settings-page').forEach(e => {
        e.classList.toggle('is-active', e.id === 'page-memory'); e.hidden = e.id !== 'page-memory';
      });
      document.querySelectorAll('.nav-item').forEach(e => e.classList.toggle('is-active', e.dataset.page === 'memory'));
      document.getElementById('pageTitle').textContent = '记忆';
      feature.initialize(SNAPSHOT);
      feature.onPageChanged('memory');
      window.feature = feature;
    """.replace("SNAPSHOT", json.dumps(snapshot, ensure_ascii=False))
    page.route("**/settings/settings.js", lambda route: route.fulfill(content_type="text/javascript", body=script))
    page.set_viewport_size({"width": 1100, "height": 820})
    page.goto(f"http://127.0.0.1:{port}/desktop/frontend/settings/")
    menu = page.get_by_role("button", name="记忆更多操作")
    expect(menu).to_be_enabled()
    menu.click()
    page.get_by_role("menuitem", name="导出记忆…").click()
    page.get_by_role("button", name="选择保存位置").click()
    expect(page.locator("[data-result]")).to_contain_text("已导出 1 条")
    page.get_by_role("dialog").get_by_role("button", name="关闭", exact=True).click()
    menu.click()
    page.get_by_role("menuitem", name="导入记忆…").click()
    page.get_by_role("button", name="选择文件").click()
    page.get_by_role("button", name="确认导入").click()
    expect(page.locator("[data-result]")).to_contain_text("已导入 1 条")
    page.get_by_role("dialog").get_by_role("button", name="关闭", exact=True).click()
    expect(page.locator(".memory-record-card")).to_have_count(2)
    menu.click()
    page.screenshot(path=str(ROOT / "temp/data-transfer-memory.png"))
    assert [r["payload"]["action"] for r in requests] == ["export", "preview", "import"]
    assert all(r["pluginId"] == "memory-fixture" and r["collectionId"] == "entries"
               and r["payload"]["expectedCharacterId"] == "sakura" for r in requests)
    page.evaluate("window.feature.dispose()")


if __name__ == "__main__":
    run()
