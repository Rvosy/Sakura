"""用隔离历史库检查图片读取、折叠、预览和角色切换。"""

import base64
import functools
import http.server
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

from playwright.sync_api import expect, sync_playwright
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.core_host.history import HistoryBoundary
from app.storage.history_images import HistoryImages
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
            images = HistoryImages(store.path)
            def picture(width, height, background, title):
                buffer = BytesIO()
                with Image.new("RGB", (width, height), background) as raster:
                    draw = ImageDraw.Draw(raster)
                    draw.rounded_rectangle((24, 24, min(width - 24, 280), 64), radius=8, fill="#ffffff")
                    draw.text((36, 36), title, fill="#27445a")
                    draw.rounded_rectangle((24, 90, width * .8, height * .6), radius=12, fill="#eaf5fa")
                    for y, end in ((130, 190), (160, 230), (190, 150)):
                        draw.line((40, y, end, y), fill="#54768b", width=8)
                    raster.save(buffer, "PNG")
                return SimpleNamespace(data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
                                       width=width, height=height)
            saved = images.save([picture(6000, 4000, "#ffffff", "Desktop screenshot"),
                                 picture(360, 1100, "#efdbdf", "Long image"),
                                 picture(600, 600, "#d9e7cb", "Square image"),
                                 picture(900, 600, "#f3dfbd", "Landscape image"),
                                 picture(600, 900, "#d9d1eb", "Portrait image")])
            observed = images.save([picture(960, 540, "#ffffff", "Screen observation")])
            now = "2026-11-13T12:00:00+08:00"
            for index in range(30):
                attachments = saved * 8 if index == 0 else [saved[1]]
                store.append(NewTimelineEntry(f"many-{index}", f"many-{index}", "sakura", TimelineKind.OBSERVATION,
                    "manual_screen", now, {"text": "你分享了 1 张屏幕截图。",
                    "visual": {"imageCount": len(attachments), "images": attachments}}))
            for id, origin, text, payload in [
                ("old", "manual_screen", "你分享了 1 张屏幕截图。", {"visual": {"imageCount": 1}}),
                ("automatic-many", "host", "屏幕上同时打开了几个窗口。", {"sourcePluginId": "screen",
                    "visual": {"imageCount": 3, "images": saved[:3]}}),
                ("manual", "manual_screen", "你分享了 5 张屏幕截图。", {"visual": {"imageCount": 5, "images": saved}}),
                ("automatic", "host", "正在阅读浏览器里的文章。", {"sourcePluginId": "screen",
                    "visual": {"imageCount": 1, "images": observed}}),
            ]:
                store.append(NewTimelineEntry(id, id, "sakura", TimelineKind.OBSERVATION, origin, now,
                                               {"text": text, **payload}))
            current = ["sakura"]
            boundary = HistoryBoundary("generation", "credential", root, character_provider=lambda: current[0])
            reads = []
            def invoke(_source, command, args=None):
                if command == "history_bootstrap":
                    return {"coreGenerationId": "generation", "characterId": current[0], "assistantName": "Sakura"}
                if command not in {"history_page", "history_image"}:
                    return None
                request = args["request"]
                payload = {"expectedCharacterId": request["characterId"]}
                if command == "history_image":
                    payload.update(entryId=request["entryId"], imageId=request["imageId"], variant=request["variant"])
                    reads.append((request["entryId"], request["variant"], request["imageId"]))
                else:
                    payload.update(limit=50)
                result = boundary.handle({"generationId": "generation", "generationCredential": "credential",
                    "id": "request", "name": "ui.history.image" if command == "history_image" else "ui.history.page",
                    "payload": payload})
                if not result["ok"]:
                    raise RuntimeError(result["error"])
                if command == "history_image":
                    return list(Path(result["payload"]["path"]).read_bytes())
                return result["payload"]
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or "msedge")
            recording = {"record_video_dir": str(ROOT / "temp/image-album-video"),
                         "record_video_size": {"width": 620, "height": 680}} if os.environ.get("SAKURA_RECORD_IMAGES") else {}
            page = browser.new_page(viewport={"width": 620, "height": 680}, reduced_motion="reduce", **recording)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_binding("nativeHistory", invoke)
            page.add_init_script("""
              window.historyEvents = new Map();
              window.holdImage = true;
              window.liveImageUrls = new Set();
              window.activeImageReads = 0;
              const createUrl = URL.createObjectURL.bind(URL), revokeUrl = URL.revokeObjectURL.bind(URL);
              URL.createObjectURL = blob => {
                const url = createUrl(blob);
                if (blob.type.startsWith('image/')) {
                  liveImageUrls.add(url);
                }
                return url;
              };
              URL.revokeObjectURL = url => { liveImageUrls.delete(url); revokeUrl(url); };
              window.__TAURI__ = {
                event: {listen: async (name, callback) => { historyEvents.set(name, callback); return () => {}; }},
                core: {invoke: async (command, args) => {
                  if (command === 'plugin_frontend_module') return {source: `
                    export function createTtsController() {
                      return {start() {}, dispose() {}, cancel() {}, setInputCaptureActive() {}};
                    }`};
                  if (command !== 'history_image') return await nativeHistory(command, args);
                  if (args.request.imageId === window.failImageId && args.request.variant !== 'thumbnail') {
                    window.failedImageReads = (window.failedImageReads || 0) + 1;
                    throw new Error('IMAGE_READ_DENIED: fixture preview');
                  }
                  window.activeImageReads += 1;
                  try {
                    if (window.activeImageReads > 4) throw new Error('ROUTER_QUEUE_FULL');
                    const result = await nativeHistory(command, args);
                    if (window.holdImage) {
                      await new Promise(resolve => { (window.imageWaiters ||= []).push(resolve); });
                    }
                    return result;
                  } finally { window.activeImageReads -= 1; }

                }}
              };
            """)
            page.goto(f"http://127.0.0.1:{server.server_port}/desktop/frontend/history/")
            manual = page.locator('[data-entry-id="manual"]')
            automatic = page.locator('article[data-entry-id="automatic"]')
            expect(manual.locator(".history-image-thumbnail img")).to_have_count(5)
            page.wait_for_function("window.imageWaiters?.length > 0")
            loading_height = manual.locator(".image-album").bounding_box()["height"]
            loading_scroll = page.locator("#history-scroll").evaluate("el => el.scrollTop")
            page.evaluate("window.holdImage = false; window.imageWaiters.forEach(resolve => resolve())")
            expect(manual.locator("img").first).to_be_visible()
            assert manual.locator(".image-album").bounding_box()["height"] == loading_height
            assert page.locator("#history-scroll").evaluate("el => el.scrollTop") == loading_scroll
            assert manual.locator(".history-image-gallery").evaluate("""el => {
              const image = el.getBoundingClientRect(), row = el.closest('article').getBoundingClientRect();
              return (image.left + image.right) / 2 > (row.left + row.right) / 2;
            }""")
            expect(manual.locator(".entry-bubble")).to_have_count(0)
            expect(page.locator('[data-entry-id="old"]')).to_have_count(0)
            expect(page.get_by_text("你分享了", exact=False)).to_have_count(0)
            page.wait_for_function("[...document.querySelectorAll('.history-image-thumbnail img')].every(img => img.naturalWidth > 0 && !img.hidden)")
            assert any(entry == "automatic" and variant == "thumbnail" for entry, variant, _ in reads)
            assert all(variant == "thumbnail" for _, variant, _ in reads)
            assert len(reads) == 78
            expect(automatic.locator("details")).not_to_have_attribute("open", "")
            closed_height = automatic.locator("details").bounding_box()["height"]
            automatic.locator("summary").click()
            heights = automatic.locator("details").evaluate("""async el => {
              const heights = [], start = performance.now();
              while (performance.now() - start < 400) {
                await new Promise(requestAnimationFrame);
                heights.push(el.getBoundingClientRect().height);
              }
              return heights;
            }""")
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            assert any(closed_height + 1 < height < heights[-1] - 1 for height in heights), heights
            expect(automatic.locator(".screen-observation-description")).to_have_text("正在阅读浏览器里的文章。")
            expect(automatic.get_by_role("button", name="展开", exact=False)).to_have_count(0)
            auto_image = automatic.locator(".history-image-gallery").bounding_box()
            auto_column = automatic.locator(".entry-column").bounding_box()
            assert abs(auto_image["x"] + auto_image["width"] / 2 - auto_column["x"] - auto_column["width"] / 2) < 1
            auto_description = automatic.locator(".screen-observation-description").bounding_box()
            assert auto_description["y"] + auto_description["height"] <= auto_image["y"]
            assert automatic.locator("img").evaluate("el => el.closest('details.entry-bubble') !== null")
            expect(automatic.locator("img")).to_be_visible()
            assert any(entry == "automatic" and variant == "thumbnail" for entry, variant, _ in reads)
            assert page.evaluate("liveImageUrls.size") == 78
            automatic.scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT / "temp/history-images-light.png"), animations="disabled")
            assert manual.locator(".image-album-card").evaluate_all("cards => cards.every(card => Math.abs(card.clientWidth / card.clientHeight - 3 / 4) < .01)"), "collapsed thumbnails must share the same portrait aspect ratio"
            # 展开保留比例；快速反向切换不丢卡片或留下运动中的资源。
            album = manual.locator(".image-album")
            manual.get_by_role("button", name="展开 5 张图片").click()
            expect(album.locator(".image-album-card")).to_have_count(5)
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            assert album.locator(".image-album-card").evaluate_all("""cards => cards.every(card => {
              const image = card.querySelector('img'), bounds = card.getBoundingClientRect();
              if (!image.naturalWidth) return true;
              const rendered = image.getBoundingClientRect();
              return Math.abs(bounds.width / bounds.height - image.naturalWidth / image.naturalHeight) < .01
                && Math.abs(bounds.width - rendered.width) < 1 && Math.abs(bounds.height - rendered.height) < 1;
            })""")
            manual.scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT / "temp/history-images-expanded.png"))
            first_image = manual.locator("img").first
            first_image.evaluate("el => window.retainedThumbnail = el")
            page.set_viewport_size({"width": 600, "height": 680})
            expect(manual.get_by_role("button", name="收起图片")).to_be_visible()
            page.set_viewport_size({"width": 620, "height": 680})
            page.wait_for_function("document.querySelector('#history-list').clientWidth > 500")
            assert first_image.evaluate("el => el === window.retainedThumbnail"), "resizing must keep loaded image nodes"
            # 动画进行中反向，首张图应从当前画面接续，而不是跳回起点。
            continuity = album.locator(".image-album-toggle").evaluate("""async el => {
              el.click();
              await new Promise(resolve => setTimeout(resolve, 110));
              const card = el.closest('.image-album').querySelector('.image-album-card');
              const before = card.getBoundingClientRect();
              el.click();
              const after = card.getBoundingClientRect();
              el.click();
              return Math.abs(before.left - after.left) + Math.abs(before.width - after.width);
            }""")
            assert continuity < 2, continuity
            expect(album.locator(".image-album-card:not([hidden])")).to_have_count(5)
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(album.get_by_role("button")).to_have_count(2)
            front = manual.locator('[aria-label="预览图片 1"]')
            front.scroll_into_view_if_needed()
            stack_height = album.bounding_box()["height"]
            box = front.bounding_box()
            travel = box["width"]
            x, y = box["x"] + box["width"] * .8, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - travel * .2, y, steps=6)
            short_x = front.bounding_box()["x"]
            page.mouse.move(x - travel * .6, y, steps=10)
            pivot_x = front.bounding_box()["x"]
            assert pivot_x < short_x, "before the turn point the photo follows the pointer outward"
            page.mouse.move(x - travel * .75, y, steps=6)
            inserting_x = front.bounding_box()["x"]
            assert inserting_x > pivot_x, "continued leftward drag must insert the photo to the right"
            held_x = front.bounding_box()["x"]
            # 停住超过原动画时长，图片仍须停在手势控制的位置。
            page.wait_for_timeout(500)
            assert abs(front.bounding_box()["x"] - held_x) < 1, "holding the pointer must not finish insertion automatically"
            page.mouse.move(x - travel * 1.05, y, steps=8)
            assert front.bounding_box()["x"] > inserting_x, "insertion must remain controlled over a longer drag"
            page.screenshot(path=str(ROOT / "temp/history-images-drag-behind.png"))
            page.mouse.move(x - travel * .75, y, steps=8)
            assert abs(front.bounding_box()["x"] - inserting_x) < 1, "reverse drag must retrace the insertion"
            page.mouse.move(x - travel * .2, y, steps=10)
            assert abs(front.bounding_box()["x"] - short_x) < 1
            page.mouse.up()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(front).to_have_attribute("aria-hidden", "false")
            box = front.bounding_box()
            x, y = box["x"] + box["width"] * .8, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - travel * .9, y, steps=12)
            page.mouse.up()
            expect(manual.get_by_role("button", name="预览图片 2", exact=True)).to_be_visible()
            expect(page.locator(".history-image-viewer")).not_to_be_visible()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            assert front.evaluate("el => !el.hidden && getComputedStyle(el).opacity === '1'"), "turned photo must remain in the stack"
            assert abs(album.bounding_box()["height"] - stack_height) < 1, "portrait and landscape images must share a stable stack height"
            assert page.locator('#history-scroll').evaluate("el => el.scrollWidth <= el.clientWidth"), "album motion must not widen the history viewport"
            manual.get_by_role("button", name="预览图片 2", exact=True).focus()
            page.keyboard.press("ArrowLeft")
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            def check_edge_drag(card, direction):
                page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
                box = card.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + direction * box["width"] * .7, y, steps=10)
                shift = card.bounding_box()["x"] - box["x"]
                assert 0 < shift * direction < box["width"] * .15, "edge drag must only nudge the current image"
                expect(card).to_have_attribute("aria-hidden", "false")
                page.mouse.up()
                page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
                assert abs(card.bounding_box()["x"] - box["x"]) < 1
                expect(page.locator(".history-image-viewer")).not_to_be_visible()

            thumbnail = manual.get_by_role("button", name="预览图片 1")
            check_edge_drag(thumbnail, 1)
            thumbnail.click()
            viewer = page.get_by_role("dialog", name="图片预览", exact=True)
            def settle_viewer():
                page.wait_for_function("""() => document.querySelector('.history-image-viewer')
                    .getAnimations({subtree: true}).every(animation => animation.playState !== 'running')""")
            expect(viewer).to_be_visible()
            expect(viewer.locator(".image-viewer-slide:not([aria-hidden]) img")).to_be_visible()
            assert any(entry == "manual" and variant == "preview" for entry, variant, _ in reads)
            assert ("manual", "original", thumbnail.get_attribute("data-image-id")) not in reads
            assert viewer.locator(".image-viewer-slide:not([aria-hidden]) img").evaluate("el => el.naturalWidth") == 2560
            viewer.get_by_role("button", name="查看原图").click()
            page.wait_for_function("document.querySelector('.history-image-viewer img').naturalWidth === 6000")
            scroll_before = page.locator("#history-scroll").evaluate("el => el.scrollTop")
            width = viewer.locator(".image-viewer-slide:not([aria-hidden]) img").evaluate("el => el.getBoundingClientRect().width")
            viewer.get_by_role("button", name="放大图片").click()
            settle_viewer()
            assert viewer.locator(".image-viewer-slide:not([aria-hidden]) img").evaluate("el => el.getBoundingClientRect().width") > width
            area = viewer.locator(".image-viewer-viewport")
            box = area.bounding_box()
            image = viewer.locator(".image-viewer-slide:not([aria-hidden]) img")
            viewer.get_by_role("button", name="实际尺寸", exact=True).click()
            settle_viewer()
            assert abs(image.bounding_box()["width"] - 6000) < 1
            # 放大后图片可触达窗口四边，悬浮工具栏周围仍可拖动图片。
            for x, y in [(2, 100), (618, 100), (310, 2), (100, 678)]:
                assert image.evaluate("(el, point) => document.elementFromPoint(...point) === el", [x, y])
            edge_y = image.bounding_box()["y"]
            page.mouse.move(100, 620)
            page.mouse.down()
            page.mouse.move(100, 550)
            page.mouse.up()
            assert image.bounding_box()["y"] < edge_y - 60
            page.screenshot(path=str(ROOT / "temp/history-image-preview-edge.png"), animations="disabled")
            viewer.get_by_role("button", name="放大图片").click()
            settle_viewer()
            assert image.bounding_box()["width"] > 6000
            viewer.get_by_role("button", name="适应窗口", exact=True).click()
            settle_viewer()
            fitted = image.bounding_box()
            # 裸滚轮围绕指针放大，指针下的图片坐标保持不变。
            px, py = box["x"] + box["width"] * .6, box["y"] + box["height"] * .55
            point = ((px - fitted["x"]) / fitted["width"], (py - fitted["y"]) / fitted["height"])
            page.mouse.move(px, py)
            page.mouse.wheel(0, -300)
            page.wait_for_function("""width => document.querySelector('.history-image-viewer img')
                .getBoundingClientRect().width > width""", arg=fitted["width"])
            zoomed = image.bounding_box()
            assert abs((px - zoomed["x"]) / zoomed["width"] - point[0]) < .005
            assert abs((py - zoomed["y"]) / zoomed["height"] - point[1]) < .005
            assert page.locator("#history-scroll").evaluate("el => el.scrollTop") == scroll_before
            viewer.get_by_role("button", name="适应窗口", exact=True).click()
            settle_viewer()
            page.mouse.move(px, py)
            page.mouse.wheel(0, 200)
            page.wait_for_function("""width => document.querySelector('.history-image-viewer img')
                .getBoundingClientRect().width < width""", arg=fitted["width"])
            expect(viewer).to_be_visible()
            viewer.get_by_role("button", name="适应窗口", exact=True).click()
            settle_viewer()
            image.dblclick()
            settle_viewer()
            assert abs(image.bounding_box()["width"] - 6000) < 1
            image.dblclick(position={"x": 3000, "y": 2000})
            settle_viewer()
            assert abs(image.bounding_box()["width"] - fitted["width"]) < 1
            page.screenshot(path=str(ROOT / "temp/history-image-preview-light.png"), animations="disabled")
            # 连续滚轮从正在显示的尺寸续接，而不是跳到终点或重新从旧尺寸开始。
            motion = image.evaluate("""async el => {
              const area = el.closest('.image-viewer-viewport');
              const bounds = area.getBoundingClientRect();
              const wheel = () => area.dispatchEvent(new WheelEvent('wheel', {
                deltaY: -120, clientX: bounds.width / 2, clientY: bounds.height / 2,
                bubbles: true, cancelable: true
              }));
              const width = () => el.getBoundingClientRect().width;
              const start = width();
              wheel();
              const immediate = width();
              await new Promise(requestAnimationFrame);
              await new Promise(requestAnimationFrame);
              const middle = width();
              wheel();
              return {start, immediate, middle, continued: width()};
            }""")
            assert abs(motion["immediate"] - motion["start"]) < 1
            assert motion["start"] < motion["middle"] < motion["start"] * 1.272
            assert abs(motion["continued"] - motion["middle"]) < 1
            settle_viewer()
            assert image.bounding_box()["width"] > motion["middle"]
            viewer.get_by_role("button", name="适应窗口", exact=True).click()
            settle_viewer()
            second_id = manual.locator('.history-image-thumbnail').nth(1).get_attribute("data-image-id")
            second_reads = sum(image_id == second_id and variant != "thumbnail" for _, variant, image_id in reads)
            page.keyboard.press("ArrowRight")
            expect(viewer.locator(".image-viewer-slide:not([aria-hidden]) img")).to_have_attribute("alt", "图片 2")
            expect(viewer.locator(".image-viewer-slide:not([aria-hidden]) img")).to_be_visible()
            movement = viewer.evaluate("""el => {
              const slides = el.querySelectorAll('.image-viewer-slide');
              for (const slide of slides) for (const animation of slide.getAnimations()) {
                animation.pause();
                animation.currentTime = animation.effect.getTiming().duration / 2;
              }
              return [...slides].map(slide => ({old: slide.hasAttribute('aria-hidden'),
                x: slide.getBoundingClientRect().x}));
            }""")
            assert any(slide["old"] and slide["x"] < 0 for slide in movement)
            assert any(not slide["old"] and slide["x"] > 0 for slide in movement)
            page.screenshot(path=str(ROOT / "temp/history-image-slide.png"))
            viewer.evaluate("el => el.getAnimations({subtree:true}).forEach(animation => animation.play())")
            settle_viewer()
            assert sum(image_id == second_id and variant != "thumbnail" for _, variant, image_id in reads) == second_reads
            viewer.get_by_role("button", name="放大图片").click()
            settle_viewer()
            viewer.get_by_role("button", name="放大图片").click()
            settle_viewer()
            area = viewer.locator(".image-viewer-viewport")
            box = area.bounding_box()
            top = image.bounding_box()["y"]
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 - 60)
            page.mouse.up()
            assert image.bounding_box()["y"] < top - 50
            released = image.bounding_box()["y"]
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 + 40)
            assert abs(image.bounding_box()["y"] - released) < 1
            page.screenshot(path=str(ROOT / "temp/history-image-preview.png"), animations="disabled")
            page.evaluate("window.holdImage = true; window.imageWaiters = []")
            page.keyboard.press("ArrowRight")
            expect(image).to_have_attribute("alt", "图片 3")
            settle_viewer()
            page.wait_for_function("window.imageWaiters.length > 0")
            page.keyboard.press("ArrowRight")
            expect(image).to_have_attribute("alt", "图片 3")
            expect(image).to_be_visible()
            expect(viewer.locator(".image-viewer-count")).to_have_text("3 / 5")
            expect(viewer.get_by_role("status")).not_to_be_visible()
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.evaluate("window.holdImage = false; window.imageWaiters.forEach(resolve => resolve())")
            page.wait_for_function("window.activeImageReads === 0")
            settle_viewer()
            expect(image).to_have_attribute("alt", "图片 3")
            expect(viewer.locator(".image-viewer-count")).to_have_text("3 / 5")
            fifth_id = manual.locator('.history-image-thumbnail').nth(4).get_attribute("data-image-id")
            page.evaluate("id => { window.failImageId = id; window.failedImageReads = 0; }", fifth_id)
            page.keyboard.press("ArrowRight")
            expect(image).to_have_attribute("alt", "图片 4")
            page.wait_for_function("window.failedImageReads > 0")
            expect(page.locator('.sakura-error-dialog')).not_to_be_visible()
            page.keyboard.press("ArrowRight")
            failure = page.get_by_role("dialog", name="无法读取图片", exact=True)
            expect(failure).to_be_visible()
            expect(failure.get_by_label("错误详情", exact=True)).to_contain_text("IMAGE_READ_DENIED: fixture preview")
            expect(image).to_have_attribute("alt", "图片 4")
            failure.get_by_role("button", name="关闭", exact=True).click()
            page.evaluate("window.failImageId = null")
            page.keyboard.press("ArrowRight")
            expect(image).to_have_attribute("alt", "图片 5")
            page.keyboard.press("Escape")
            expect(viewer).not_to_be_visible()
            expect(thumbnail).to_be_focused()
            assert page.locator("#history-scroll").evaluate("el => el.scrollTop") == scroll_before
            page.wait_for_function("liveImageUrls.size === document.querySelectorAll('.history-image-thumbnail img[src]').length")
            observation_album = page.locator('article[data-entry-id="automatic-many"]')
            observation_album.scroll_into_view_if_needed()
            observation_album.locator("summary").click()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(observation_album.locator(".screen-observation-description")).to_have_text("屏幕上同时打开了几个窗口。")
            expect(observation_album.get_by_role("button", name="展开", exact=False)).to_have_count(0)
            observation_front = observation_album.get_by_role("button", name="预览图片 1", exact=True)
            box = observation_front.bounding_box()
            page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * .8 - box["width"] * .9, box["y"] + box["height"] / 2, steps=12)
            page.mouse.up()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            observation_next = observation_album.get_by_role("button", name="预览图片 2", exact=True)
            expect(observation_next).to_be_visible()
            box = observation_next.bounding_box()
            page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * .2 + box["width"] * .9, box["y"] + box["height"] / 2, steps=12)
            expect(observation_next).to_be_visible()
            assert page.locator('#history-scroll').evaluate("el => el.scrollWidth <= el.clientWidth")
            page.screenshot(path=str(ROOT / "temp/history-images-drag-right.png"))
            page.mouse.up()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(observation_front).to_be_visible()
            page.screenshot(path=str(ROOT / "temp/history-screen-observation-album.png"))
            page.emulate_media(reduced_motion="reduce")
            observation_front.focus()
            page.keyboard.press("ArrowRight")
            expect(observation_next).to_be_focused()
            assert observation_album.locator(".image-album").evaluate("el => el.getAnimations({subtree:true}).some(a => a.playState === 'running')")
            page.keyboard.press("ArrowLeft")
            expect(observation_front).to_be_focused()
            page.emulate_media(reduced_motion="no-preference")
            page.locator('[data-entry-id="many-0"]').scroll_into_view_if_needed()
            large_album = page.locator('[data-entry-id="many-0"] .image-album')
            expect(large_album.locator("img")).to_have_count(40)
            expect(large_album.locator("img").first).to_be_visible()
            loaded_before = len(reads)
            large_album.get_by_role("button", name="展开 40 张图片").click()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(large_album.locator("img")).to_have_count(40)
            assert len(reads) == loaded_before, "expansion must reuse preloaded thumbnails"
            assert large_album.locator("img").evaluate_all("images => images.every(img => img.naturalWidth > 0 && !img.hidden)")
            assert page.evaluate("liveImageUrls.size") == 78
            large_album.get_by_role("button", name="收起图片").click()
            expect(large_album.locator("img")).to_have_count(40)
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            assert manual.locator("img").first.get_attribute("src")
            large_album.get_by_role("button", name="展开 40 张图片").click()
            assert large_album.locator("img").evaluate_all("images => images.every(img => img.naturalWidth > 0 && !img.hidden)")
            assert len(reads) == loaded_before, "collapse and re-expansion must not reload thumbnails"
            large_album.get_by_role("button", name="收起图片").click()
            assert page.evaluate("liveImageUrls.size") == 78
            manual.scroll_into_view_if_needed()
            expect(manual.locator("img").first).to_be_visible()
            page.set_viewport_size({"width": 480, "height": 440})
            page.evaluate("""() => {
              const theme = {'--page-background':'#17232b','--panel-background':'#20303b',
                '--input-background':'#233642','--text':'#e1edf4','--secondary-text':'#b1c8d6',
                '--muted-text':'#91a8b8','--border':'#486171'};
              for (const [key, value] of Object.entries(theme)) document.documentElement.style.setProperty(key, value);
            }""")
            automatic.scroll_into_view_if_needed()
            expect(automatic.locator("img")).to_be_visible()
            page.screenshot(path=str(ROOT / "temp/history-images-dark-small.png"), animations="disabled")
            manual.get_by_role("button", name="预览图片 1", exact=True).focus()
            page.keyboard.press("ArrowRight")
            manual.get_by_role("button", name="预览图片 2", exact=True).click()
            expect(viewer.locator(".image-viewer-slide:not([aria-hidden]) img")).to_be_visible()
            page.screenshot(path=str(ROOT / "temp/history-image-preview-small.png"), animations="disabled")
            viewer.get_by_role("button", name="适应窗口", exact=True).click()
            settle_viewer()
            page.set_viewport_size({"width": 900, "height": 720})
            page.wait_for_function("""() => {
              const area = document.querySelector('.image-viewer-viewport').getBoundingClientRect();
              const image = document.querySelector('.history-image-viewer img').getBoundingClientRect();
              return Math.abs(image.height - area.height) < 1 && image.width <= area.width;
            }""")
            page.screenshot(path=str(ROOT / "temp/history-image-preview-dark.png"), animations="disabled")
            page.set_viewport_size({"width": 480, "height": 440})
            viewer.evaluate("el => { window.previewClosed = new Promise(resolve => el.addEventListener('close', resolve, {once:true})); }")
            viewer.get_by_role("button", name="关闭图片预览").click()
            page.evaluate("window.previewClosed")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            expect(manual.get_by_role("button", name="预览图片 1", exact=True)).to_be_focused()
            for _ in range(5):
                page.keyboard.press("ArrowRight")
            expect(manual.get_by_role("button", name="预览图片 5", exact=True)).to_be_focused()
            check_edge_drag(manual.get_by_role("button", name="预览图片 5", exact=True), -1)
            for image in saved[3:]:
                images.path(image).unlink()
            page.locator("#refresh").evaluate("button => button.click()")
            expect(manual.locator(".image-album-card")).to_have_count(3)
            expect(manual.get_by_role("button", name="预览图片 3", exact=True)).to_be_visible()
            store.append(NewTimelineEntry("arriving", "arriving", "sakura", TimelineKind.OBSERVATION,
                "host", now, {"text": "新收到的屏幕观察。", "sourcePluginId": "screen",
                "visual": {"imageCount": 1, "images": observed}}))
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload:{}})")
            arriving = page.locator('article[data-entry-id="arriving"]')
            expect(arriving).to_be_visible()
            assert arriving.evaluate("el => el.getAnimations().some(a => a.animationName === 'entry-arrive')")
            assert not automatic.evaluate("el => el.getAnimations().some(a => a.animationName === 'entry-arrive')")
            current[0] = "other"
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload:{reset:true,ready:true}})")
            expect(viewer).not_to_be_visible()
            expect(page.locator(".history-entry")).to_have_count(0)
            page.wait_for_function("liveImageUrls.size === 0")
            current[0] = "sakura"
            page.evaluate("window.holdImage = true; window.imageWaiters = []; historyEvents.get('sakura://history-refresh-requested')({payload:{reset:true,ready:true}})")
            page.wait_for_function("window.imageWaiters?.length > 0")
            current[0] = "other"
            page.evaluate("historyEvents.get('sakura://history-refresh-requested')({payload:{reset:true,ready:true}})")
            page.evaluate("window.holdImage = false; window.imageWaiters.forEach(resolve => resolve())")
            expect(page.locator(".history-entry")).to_have_count(0)
            page.wait_for_function("liveImageUrls.size === 0")
            assert errors == [], errors
            browser.close()
        print("PASS: 40-image album, pointer-controlled insertion and bounded navigation, interrupted motion, aspect ratio, eager thumbnails, screen observation captions, zoom, focus, themes and stale image isolation")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
