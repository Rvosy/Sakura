"""用真实统计查询检查设置页的读取、范围切换和图表交互。"""
import functools
import http.server
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
from datetime import datetime, timedelta

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.core_host.usage_statistics import UsageStatisticsBoundary
from app.storage.usage import UsageStore
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineStore, NewTimelineEntry, TimelineKind

BRIDGE = runpy.run_path(str(Path(__file__).with_name("settings-controls.journey.py")))["BRIDGE"].replace(
    "['character','appearance']", "['character','appearance','statistics']")
BRIDGE += """
window.__TAURI__.window.getCurrentWindow = () => ({
  onCloseRequested: async () => () => {}, onResized: async () => () => {},
  setDecorations: async () => {}, isMaximized: async () => false,
  minimize: async () => {}, toggleMaximize: async () => {}, startDragging: async () => {}
});
const baseInvoke = window.__TAURI__.core.invoke;
window.__pendingUsage = [];
window.__TAURI__.core.invoke = (command, args) => {
  if (command !== 'settings_usage_statistics_get') return baseInvoke(command, args);
  if (window.__holdUsage) return new Promise(resolve => window.__pendingUsage.push(() => window.queryUsage(args).then(resolve)));
  return window.queryUsage(args);
};
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="sakura-usage-") as temporary, sync_playwright() as playwright:
            root = Path(temporary)
            store = UsageStore(root)
            today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            timeline = TimelineStore(StoragePaths(root).timeline_database())
            timeline.initialize()
            for index, tokens in enumerate([18000, 42000, 26000, 0, 53000, 98000, 82000, 34000, 60000, 124000]):
                timestamp = (today - timedelta(days=9-index)).isoformat()
                store.record({"recordId": str(index), "timestamp": timestamp, "model": "示例模型",
                    "category": "memory_curation" if index % 3 == 0 else "conversation",
                    "usage": {"input_tokens": tokens, "output_tokens": 18000, "cached_input_tokens": 8000, "reasoning_tokens": 3000}},
                    plugin_id="assistant", plugin_name="Sakura Assistant")
                timeline.append(NewTimelineEntry(str(index), str(index), "sakura", TimelineKind.HUMAN, "chat", timestamp, {"text": "hello"}))
            store.record({"recordId": "external", "model": "摘要模型", "category": "summary", "categoryLabel": "摘要生成",
                "timestamp": today.isoformat(),
                "usage": {"total_tokens": 7000}}, plugin_id="summary", plugin_name="摘要插件")
            boundary = UsageStatisticsBoundary("g", "c", root)
            def query(payload):
                result = boundary.handle({"generationId": "g", "generationCredential": "c", "id": "usage",
                    "name": "usage.statistics.get", "payload": payload})
                assert result["ok"], result
                return result["payload"]
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or "msedge")
            page = browser.new_page(viewport={"width": 1200, "height": 900}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_function("queryUsage", query)
            page.add_init_script(BRIDGE)
            output = ROOT / "temp/usage-statistics-preview"
            output.mkdir(parents=True, exist_ok=True)
            page.goto(f"http://127.0.0.1:{server.server_port}/desktop/frontend/settings/index.html")
            try:
                page.locator('[data-page="statistics"]').click(timeout=3000)
            except Exception:
                print(page.locator(".sakura-error-dialog").all_text_contents())
                print(errors)
                raise
            section = page.locator("#page-statistics")
            def slider_centers():
                return page.evaluate("""() => {
                  const bottom = document.querySelector('.usage-chart').getBoundingClientRect().bottom;
                  window.sliderHandles = [...document.querySelectorAll('.usage-chart svg path')].filter(element => {
                    const box = element.getBoundingClientRect();
                    return box.top > bottom - 30 && box.width > 0 && box.width < box.height;
                  });
                  return sliderHandles.map(element => {
                    const box = element.getBoundingClientRect();
                    return {x: box.x + box.width / 2, y: box.y + box.height / 2};
                  });
                }""")
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("724,000")
            expect(section.locator(".usage-chart svg")).to_be_visible()
            expect(section.locator(".usage-table")).to_contain_text("记忆整理")
            section.get_by_role("button", name="插件", exact=True).click()
            expect(section.locator(".usage-table")).to_contain_text("摘要插件")
            section.get_by_role("button", name="模型", exact=True).click()
            expect(section.locator(".usage-table")).to_contain_text("摘要模型")
            section.get_by_role("button", name="功能", exact=True).click()

            section.locator(".date-range-trigger").click()
            dates = page.get_by_role("dialog", name="选择日期范围")
            page.screenshot(path=str(output / "calendar.png"), animations="disabled")
            dates.get_by_role("button", name="当天", exact=True).click()
            dates.get_by_role("button", name="确定", exact=True).click()
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("149,000")
            section.get_by_role("button", name="柱状", exact=True).click()
            chart = section.locator(".usage-chart")
            box = chart.bounding_box()
            expect(chart).to_contain_text("00:00")
            page.mouse.move(box["x"] + 54 + (box["width"] - 74) / 48, box["y"] + 100)
            expect(section.locator(".usage-tooltip")).to_contain_text("149,000")
            expect(section.locator(".usage-tooltip")).to_contain_text("124,000")
            expect(section.locator(".usage-tooltip")).to_contain_text("00:00")
            page.screenshot(path=str(output / "hourly.png"), animations="disabled")
            section.get_by_role("button", name="对话", exact=True).click()
            expect(section.locator(".usage-card-turns .usage-value")).to_have_text("1")
            section.get_by_role("button", name="Token", exact=True).click()
            section.locator(".date-range-trigger").click()
            dates.get_by_role("button", name="近 30 天", exact=True).click()
            dates.get_by_role("button", name="确定", exact=True).click()
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("724,000")
            section.get_by_role("button", name="曲线", exact=True).click()
            page.mouse.move(10, 10)

            # 拖动缩放手柄后刷新，图表与数值留在原位，缩放范围不重置。
            box = chart.bounding_box()
            handles = slider_centers()
            page.mouse.move(handles[0]["x"], handles[0]["y"])
            page.mouse.down()
            page.mouse.move(box["x"] + 180, box["y"] + box["height"] - 16, steps=12)
            page.mouse.up()
            page.evaluate("""async () => {
              const echarts = await import('/desktop/frontend/vendor/echarts/echarts.esm.min.js');
              window.usageChart = echarts.getInstanceByDom(document.querySelector('.usage-chart'));
            }""")
            zoom = page.evaluate("usageChart.getOption().dataZoom[0].start")
            assert zoom > 0
            page.mouse.move(box["x"] + box["width"] - 70, box["y"] + 120)
            page.screenshot(path=str(output / "zoom-hover.png"), animations="disabled")
            page.evaluate("window.__holdUsage = true")
            before = chart.bounding_box()
            section.get_by_role("button", name="刷新统计", exact=True).click()
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("724,000")
            assert chart.bounding_box() == before
            expect(section.get_by_role("button", name="刷新统计", exact=True)).to_be_disabled()
            page.evaluate("window.__pendingUsage.shift()()")
            expect(section.get_by_role("button", name="刷新统计", exact=True)).to_be_enabled()
            assert chart.bounding_box() == before
            after_zoom = page.evaluate("usageChart.getOption().dataZoom[0].start")
            assert after_zoom == zoom, (zoom, after_zoom)
            page.screenshot(path=str(output / "refreshed.png"), animations="disabled")

            # 已离开页面的旧请求不能覆盖再次进入后的结果。
            section.get_by_role("button", name="刷新统计", exact=True).click()
            page.locator('[data-page="appearance"]').click()
            page.evaluate("window.__holdUsage = false")
            page.locator('[data-page="statistics"]').click()
            expect(section.get_by_role("button", name="刷新统计", exact=True)).to_be_enabled()
            page.evaluate("window.__pendingUsage.shift()()")
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("724,000")
            point_visible = page.evaluate("""async () => {
              const echarts = await import('/desktop/frontend/vendor/echarts/echarts.esm.min.js');
              const element = document.querySelector('.usage-chart');
              const chart = echarts.getInstanceByDom(element), option = chart.getOption();
              const last = option.series[0].data.length - 1, value = option.series[0].data[last];
              chart.dispatchAction({type: 'dataZoom', startValue: last, endValue: last});
              await new Promise(requestAnimationFrame);
              const [x, y] = chart.convertToPixel({seriesIndex: 0}, [last, value]);
              const box = element.getBoundingClientRect();
              const point = document.elementFromPoint(box.x + x, box.y + y);
              window.usageChart = chart;
              return point?.closest('svg') && ['path', 'circle'].includes(point.tagName)
                && getComputedStyle(point).fill !== 'none';
            }""")
            page.screenshot(path=str(output / "single-point.png"), animations="disabled")
            assert point_visible, "缩放到一个时间点时，应保留可见的数据标记"
            page.evaluate("usageChart.dispatchAction({type: 'dataZoom', start: 0, end: 100})")
            page.screenshot(path=str(output / "light.png"), animations="disabled")
            # 两端手柄拖到边界时保持左右顺序，滑条不显示日期文字。
            for side in (0, 1):
                page.evaluate("usageChart.dispatchAction({type: 'dataZoom', start: 0, end: 100})")
                box = chart.bounding_box()
                handles = slider_centers()
                page.mouse.move(handles[side]["x"], handles[side]["y"])
                page.mouse.down()
                page.mouse.move(handles[1-side]["x"] + (20 if side == 0 else -20), handles[side]["y"], steps=16)
                page.screenshot(path=str(output / f"slider-edge-{side}.png"), animations="disabled")
                assert page.evaluate("sliderHandles[0].getBoundingClientRect().x <= sliderHandles[1].getBoundingClientRect().x"), "滑条手柄不能互相越过"
                assert page.evaluate("""() => {
                  const element = document.querySelector('.usage-chart'), bottom = element.getBoundingClientRect().bottom;
                  return [...element.querySelectorAll('svg text')].every(text =>
                    text.getBoundingClientRect().bottom < bottom - 28 || !text.getBoundingClientRect().height);
                }"""), "滑条两端不显示日期文字"
                page.mouse.up()
            page.evaluate("usageChart.dispatchAction({type: 'dataZoom', start: 0, end: 100})")
            section.get_by_role("button", name="柱状", exact=True).click()
            page.screenshot(path=str(output / "bars.png"), animations="disabled")
            page.set_viewport_size({"width": 900, "height": 760})
            page.evaluate("""() => {
              const values = {'primary':'#79b8dc','accent':'#ed93b4','text':'#e1edf5','secondary-text':'#aac3d3',
                'muted-text':'#8ba5b6','page-bg':'#15232d','panel-bg':'#1c303d','input-bg':'#223a49','border':'#405b6d'};
              for (const [key,value] of Object.entries(values)) document.documentElement.style.setProperty('--sakura-'+key,value);
            }""")
            page.screenshot(path=str(output / "dark-narrow.png"), animations="disabled")
            section.locator(".usage-table").scroll_into_view_if_needed()
            page.screenshot(path=str(output / "dark-details.png"), animations="disabled")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.evaluate("document.querySelector('.page-scroll').scrollTop = 0")
            section.locator(".date-range-trigger").click()
            page.screenshot(path=str(output / "calendar-dark.png"), animations="disabled")
            dates.get_by_role("button", name="全部", exact=True).click()
            dates.get_by_role("button", name="确定", exact=True).click()
            expect(section.locator(".usage-card-tokens .usage-value")).to_have_text("724,000")

            page.emulate_media(reduced_motion="no-preference")
            page.locator('[data-page="appearance"]').click()
            page.locator('[data-page="statistics"]').click()
            expect(section.locator(".usage-chart svg")).to_be_visible()
            animated = page.evaluate("""async () => {
              const echarts = await import('/desktop/frontend/vendor/echarts/echarts.esm.min.js');
              const element = document.querySelector('.usage-chart');
              const chart = echarts.getInstanceByDom(element), frames = new Set();
              do {
                frames.add([...element.querySelectorAll('svg path')].map(path => path.getAttribute('d')).join('|'));
                await new Promise(requestAnimationFrame);
              } while (!chart.getZr().animation.isFinished());
              return frames.size > 1;
            }""")
            assert animated, "柱状图应从零展开，而不是直接跳到最终高度"
            assert not errors, errors
            browser.close()
            print("PASS: 日期范围、曲线与柱状、悬浮详情、分类维度、刷新与切页；浅色与深色窄窗口截图已生成。")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
