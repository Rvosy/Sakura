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
                    reads.append((request["entryId"], request["variant"]))
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
            assert ("automatic", "thumbnail") in reads
            assert all(variant == "thumbnail" for _, variant in reads)
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
            assert ("automatic", "thumbnail") in reads
            assert page.evaluate("liveImageUrls.size") == 78
            automatic.scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT / "temp/history-images-light.png"), animations="disabled")
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
            page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] / 2)
            page.mouse.down()
            rear = manual.locator('[aria-label="预览图片 3"]')
            rear_transform = rear.evaluate("el => getComputedStyle(el).transform")
            page.mouse.move(box["x"] + box["width"] * .6, box["y"] + box["height"] / 2, steps=6)
            front_above_next = """el => {
              const next = el.parentElement.querySelector('[aria-label="预览图片 2"]');
              return Number(getComputedStyle(el).zIndex) > Number(getComputedStyle(next).zIndex);
            }"""
            assert front.evaluate(front_above_next), "short drag must keep the current photo in front"
            page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2, steps=10)
            assert rear.evaluate("el => getComputedStyle(el).transform") == rear_transform, "rear photos must not enlarge together"
            assert front.evaluate("el => new DOMMatrix(getComputedStyle(el).transform).e") < album.evaluate("el => el.clientWidth") * .2, "left drag must travel beyond half a photo"
            assert not front.evaluate(front_above_next), "crossing the threshold must tuck the photo behind before release"
            assert front.evaluate("el => !el.hidden && getComputedStyle(el).opacity === '1'")
            page.screenshot(path=str(ROOT / "temp/history-images-drag-behind.png"))
            # 拖回阈值内时恢复前景，松手不翻页。
            page.mouse.move(box["x"] + box["width"] * .7, box["y"] + box["height"] / 2, steps=10)
            assert front.evaluate(front_above_next)
            page.mouse.up()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            expect(front).to_have_attribute("aria-hidden", "false")
            box = front.bounding_box()
            page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2, steps=10)
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
            thumbnail = manual.get_by_role("button", name="预览图片 1")
            thumbnail.click()
            viewer = page.get_by_role("dialog", name="图片预览", exact=True)
            expect(viewer).to_be_visible()
            expect(viewer.locator("img")).to_be_visible()
            assert ("manual", "preview") in reads
            assert ("manual", "original") not in reads
            assert viewer.locator("img").evaluate("el => el.naturalWidth") == 2560
            viewer.get_by_role("button", name="查看原图").click()
            page.wait_for_function("document.querySelector('.history-image-viewer img').naturalWidth === 6000")
            scroll_before = page.locator("#history-scroll").evaluate("el => el.scrollTop")
            width = viewer.locator("img").evaluate("el => el.getBoundingClientRect().width")
            viewer.get_by_role("button", name="放大图片").click()
            assert viewer.locator("img").evaluate("el => el.getBoundingClientRect().width") > width
            page.keyboard.press("ArrowRight")
            expect(viewer.locator("img")).to_have_attribute("alt", "图片 2")
            expect(viewer.locator("img")).to_be_visible()
            viewer.get_by_role("button", name="放大图片").click()
            viewer.get_by_role("button", name="放大图片").click()
            area = viewer.locator(".image-viewer-viewport")
            box = area.bounding_box()
            top = area.evaluate("el => el.scrollTop")
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 - 60)
            page.mouse.up()
            assert area.evaluate("el => el.scrollTop") > top
            page.screenshot(path=str(ROOT / "temp/history-image-preview.png"), animations="disabled")
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
            page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2, steps=10)
            page.mouse.up()
            page.wait_for_function("document.getAnimations().every(animation => animation.playState !== 'running')")
            observation_next = observation_album.get_by_role("button", name="预览图片 2", exact=True)
            expect(observation_next).to_be_visible()
            box = observation_next.bounding_box()
            page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2)
            page.mouse.down()
            origin_x = observation_next.evaluate("el => new DOMMatrix(getComputedStyle(el).transform).e")
            page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] / 2, steps=10)
            assert observation_next.evaluate("el => new DOMMatrix(getComputedStyle(el).transform).e") - origin_x > box["width"] * .5
            assert observation_next.evaluate("el => Number(getComputedStyle(el).zIndex)") < observation_album.locator('[aria-label="预览图片 1"]').evaluate("el => Number(getComputedStyle(el).zIndex)")
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
            expect(viewer.locator("img")).to_be_visible()
            page.screenshot(path=str(ROOT / "temp/history-image-preview-small.png"), animations="disabled")
            viewer.get_by_role("button", name="关闭图片预览").click()
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            expect(manual.get_by_role("button", name="预览图片 5", exact=True)).to_be_focused()
            for image in saved[3:]:
                images.path(image).unlink()
            page.locator("#refresh").evaluate("button => button.click()")
            expect(manual.locator(".image-album-card")).to_have_count(3)
            expect(manual.get_by_role("button", name="预览图片 2", exact=True)).to_be_visible()
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
        print("PASS: 40-image album, drag depth and reversal, interrupted motion, aspect ratio, eager thumbnails, screen observation captions, zoom, focus, themes and stale image isolation")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
