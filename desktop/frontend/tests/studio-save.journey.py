"""Save/reload regression with desktop CSP and the real bounded Core router."""
import asyncio
from concurrent.futures import Future
import functools
import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright, expect
from app.agent.tools import ToolRegistry
from app.core_host.character_studio import CharacterStudioBoundary, CHARACTER_STUDIO_REQUEST_NAMES
from app.core_host.router import ConcurrentHostRouter
from app.core_host.plugin_application import PluginApplicationHost
from app.storage.runtime_roots import RuntimeRoots


class BrowserRouter:
    def __init__(self, boundary):
        self.boundary = boundary
        self.input = queue.Queue()
        self.pending = {}
        self.errors = []
        self.router = ConcurrentHostRouter(None, self, object(), fixture_handler=self.handle,
            fixture_names=CHARACTER_STUDIO_REQUEST_NAMES, read_frame_fn=lambda _: self.input.get())
        self.thread = threading.Thread(target=self.router.run)
        self.thread.start()

    def handle(self, request):
        return self.boundary.handle(request)

    def send(self, message):
        if "error" in message: self.errors.append(message["error"])
        self.pending.pop(message["id"]).set_result(message)

    async def request(self, name, payload):
        request_id = uuid.uuid4().hex
        future = Future()
        self.pending[request_id] = future
        self.input.put({"protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": request_id,
            "generationId": "g", "generationCredential": "c", "name": name, "payload": payload,
            "deadlineMs": 30000, "priority": "interactive"})
        result = await asyncio.wait_for(asyncio.wrap_future(future), 35)
        if "error" in result: raise RuntimeError(result["error"]["code"])
        return result["payload"]

    def close(self):
        self.input.put(None)
        self.thread.join(5)
        assert not self.thread.is_alive()
        assert self.router.fatal_error is None


async def run():
    previews = {}
    asset_requests = {}
    csp = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["app"]["security"]["csp"]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args): pass
        def end_headers(self):
            self.send_header("Content-Security-Policy", csp)
            super().end_headers()
        def do_GET(self):
            prefix, _, relative = self.path.rpartition("/")
            if prefix in previews:
                asset_requests[self.path] = asset_requests.get(self.path, 0) + 1
                data = (previews[prefix] / bytes.fromhex(relative).decode()).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else: super().do_GET()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="sakura-studio-save-") as temporary:
            root = Path(temporary)
            distribution, user = root / "distribution", root / "user"
            shutil.copytree(ROOT / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
            package = user / "characters/sample"
            (package / "portraits").mkdir(parents=True)
            expressions = {f"表情 {index}": f"portraits/{index}.png" for index in range(40)}
            for path in expressions.values():
                shutil.copyfile(ROOT / "desktop/frontend/prototypes/asr/assets/navi.png", package / path)
            (package / "card.md").write_text("示例角色的人设", encoding="utf-8")
            (package / "second.json").write_text(json.dumps({"default": "portraits/1.png", "expressions": expressions}), encoding="utf-8")
            (package / "character.json").write_text(json.dumps({"id": "sample", "display_name": "示例角色", "card": "card.md",
                "portrait": {"default": "portraits/0.png", "expressions": expressions},
                "visuals": {"default": "portrait-default", "resources": [
                    {"id": "portrait-default", "name": "立绘1", "type": "sakura.visual.portrait@1", "root": ".", "entry": "character.json"},
                    {"id": "second", "name": "立绘2", "type": "sakura.visual.portrait@1", "root": ".", "entry": "second.json"},
                ]}}), encoding="utf-8")
            application = PluginApplicationHost(RuntimeRoots(distribution, user), "g", ToolRegistry())
            application.start()
            bridge = BrowserRouter(CharacterStudioBoundary("g", "c", user, plugin_application_provider=lambda: application))
            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None))
                    page = await browser.new_page(viewport={"width": 1274, "height": 820})
                    errors, premature = [], []
                    state = {"restarting": False, "mode": "late", "publish_count": 0, "fail_catalog": False, "catalog_error": None, "opens": 0}
                    publication = asyncio.Event()
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    async def wait_images():
                        for _ in range(150):
                            if await page.evaluate("!document.getElementById('expressionList').inert && [...document.querySelectorAll('.expression-thumbnail img')].length === 40 && [...document.querySelectorAll('.expression-thumbnail img')].every(image=>image.naturalWidth>0)"):
                                return
                            await asyncio.sleep(0.1)
                        raise AssertionError({"message": "Portrait thumbnails did not load", "images": await page.evaluate("[...document.querySelectorAll('.expression-thumbnail img')].map(i=>({src:i.src,width:i.naturalWidth}))"), "error": await page.locator("#errorText").inner_text(), "pageErrors": errors})

                    async def ready():
                        state["restarting"] = False
                        await page.evaluate("window.emitNative('sakura://studio-runtime-reload',{state:'ready',generationId:'next'})")

                    async def invoke(command, params=None):
                        params = params or {}
                        if command == "show_studio": return None
                        if command == "studio_bootstrap": return await bridge.request("studio.bootstrap", {"initialCharacterId": "sample"})
                        if command != "studio_request": raise ValueError(command)
                        method = params["method"]
                        assert method != "studio.visual.asset", "Images must not enter the Core request queue"
                        if method == "studio.visual.catalog" and state["catalog_error"]:
                            error, state["catalog_error"] = state["catalog_error"], None
                            raise RuntimeError(error)
                        if method == "studio.visual.catalog" and state["fail_catalog"] and state["publish_count"] == 3:
                            state["fail_catalog"] = False
                            raise RuntimeError("ROUTER_QUEUE_FULL")
                        if state["restarting"]:
                            premature.append(method)
                            raise RuntimeError("STUDIO_CORE_UNAVAILABLE")
                        result = await bridge.request(method, params["params"])
                        if method == "studio.visual.open":
                            state["opens"] += 1
                            result["presentation"]["visual"]["editor"] = origin + "/plugins/builtin/sakura_portrait/frontend/editor.js"
                            path = "/preview/" + uuid.uuid4().hex
                            previews[path] = Path(result.pop("assetRootPath"))
                            result["assetBaseUrl"] = origin + path + "/"
                        if method == "studio.visual.previews":
                            for item in result["items"]:
                                source = item.pop("sourcePath", None)
                                item["previewUrl"] = None
                                if source:
                                    source = Path(source)
                                    path = "/preview/" + uuid.uuid4().hex
                                    previews[path] = source.parent
                                    item["previewUrl"] = origin + path + "/" + source.name.encode().hex()
                        if method == "studio.character.publish":
                            state["publish_count"] += 1
                            state["restarting"] = True
                            result["runtimeReload"] = "requested"
                            if state["mode"] == "early": await ready()
                            publication.set()
                        return result

                    await page.expose_function("nativeInvoke", invoke)
                    await page.add_init_script("""
                      window.cspViolations = [];
                      document.addEventListener('securitypolicyviolation', event => window.cspViolations.push(event.violatedDirective));
                      const listeners = new Map();
                      window.emitNative = (name, payload) => listeners.get(name)?.({payload});
                      window.__TAURI__ = {core:{invoke:window.nativeInvoke},event:{listen:async(name, callback)=>{
                        listeners.set(name, callback); return ()=>listeners.delete(name);
                      }}};
                    """)
                    await page.goto(origin + "/desktop/frontend/studio/")
                    await expect(page.locator("#displayName")).to_have_value("示例角色")
                    await page.get_by_role("button", name="角色形态", exact=True).click()
                    rows = page.locator(".portrait-plugin .expression-row")
                    await expect(rows).to_have_count(40)
                    await wait_images()
                    await expect(page.locator("#visualName")).to_have_value("立绘1")
                    cards = page.locator(".form-card")
                    for _ in range(100):
                        if await page.evaluate("[...document.querySelectorAll('.form-card-cover img')].filter(image => image.naturalWidth > 0).length === 2"): break
                        await asyncio.sleep(0.05)
                    assert await page.evaluate("[...document.querySelectorAll('.form-card-cover img')].filter(image => image.naturalWidth > 0).length === 2")
                    assert state["opens"] == 1, "Unselected covers must not require mounting their editors"
                    cover_sources = await page.evaluate("[...document.querySelectorAll('.form-card-cover img')].map(image => new URL(image.src).pathname)")
                    cover_requests = {source: asset_requests.get(source, 0) for source in cover_sources}
                    await page.evaluate("""() => {
                      window.coverNodes = [...document.querySelectorAll('.form-card-cover img')];
                      window.blankCoverFrames = 0;
                      function check() {
                        const images = [...document.querySelectorAll('.form-card-cover img')];
                        if (images.length !== 2 || images.some(image => !image.naturalWidth)) window.blankCoverFrames++;
                        requestAnimationFrame(check);
                      }
                      requestAnimationFrame(check);
                    }""")
                    for index in [1, 0, 1, 0]:
                        await cards.nth(index).click()
                        await expect(page.locator("#visualName")).to_have_value(f"立绘{index + 1}")
                        await wait_images()
                        assert await page.evaluate("window.coverNodes.every((image, index) => image === document.querySelectorAll('.form-card-cover img')[index])")
                    assert {source: asset_requests.get(source, 0) for source in cover_sources} == cover_requests
                    opens = state["opens"]
                    await page.evaluate("window.firstPortrait = document.querySelector('.expression-thumbnail img')")
                    await cards.first.click()
                    await page.get_by_role("radio", name="默认立绘", exact=True).nth(1).check()
                    assert state["opens"] == opens
                    assert await page.evaluate("window.firstPortrait === document.querySelector('.expression-thumbnail img') && window.firstPortrait.naturalWidth > 0")
                    state["catalog_error"] = "REQUEST_DEADLINE_EXCEEDED"
                    for _ in range(30):
                        if state["catalog_error"] is None: break
                        await asyncio.sleep(0.1)
                    assert state["catalog_error"] is None
                    await expect(rows).to_have_count(40)
                    await expect(page.locator("#errorText")).to_be_empty()
                    # Observe actual painted frames across saves, including the
                    # native restart wait and image decoding in the new editor.
                    await page.evaluate("""() => {
                      window.blankFrames = 0;
                      function check() {
                        const images = [...document.querySelectorAll('.expression-thumbnail img')];
                        if (!images.some(image => image.naturalWidth > 0 && getComputedStyle(image).visibility !== 'hidden')) window.blankFrames++;
                        window.frameCheck = requestAnimationFrame(check);
                      }
                      window.frameCheck = requestAnimationFrame(check);
                    }""")
                    for index, mode in enumerate(["late", "early"]):
                        state["mode"] = mode
                        publication.clear()
                        await page.locator("#visualName").fill(f"日常立绘 {index + 1}")
                        await page.locator("#saveButton").click()
                        if mode == "late":
                            await expect(rows).to_have_count(40)
                            await asyncio.wait_for(publication.wait(), 10)
                            await asyncio.sleep(0.1)
                            assert state["publish_count"] == 1
                            assert not premature, premature
                            await expect(page.locator("#saveButton")).to_be_disabled()
                            await ready()
                        await expect(page.locator("#saveButton")).to_be_enabled()
                        await expect(rows).to_have_count(40)
                        await wait_images()
                        await expect(page.locator("#errorText")).to_be_empty()
                    # A catalog refresh failure occurs after the file transaction;
                    # the committed document and clean baseline must still apply.
                    state["fail_catalog"] = True
                    await page.locator("#visualName").fill("保存后刷新")
                    await page.locator("#saveButton").click()
                    await expect(page.locator("#saveButton")).to_be_enabled()
                    await expect(page.locator("#errorText")).to_contain_text("角色已保存")
                    await expect(page.locator("#discardDraftButton")).to_be_disabled()
                    await cards.first.click()
                    await expect(rows).to_have_count(40)
                    await wait_images()
                    await expect(page.locator("#errorText")).to_be_empty()
                    for width in [1274, 820, 680]:
                        await page.set_viewport_size({"width": width, "height": 820})
                        assert await page.evaluate("""() => {
                          const panel = document.querySelector('.page-scroll');
                          return panel.scrollWidth <= panel.clientWidth && [...document.querySelectorAll('.expression-thumbnail img')].every(image => {
                            const imageBox = image.getBoundingClientRect(), button = image.parentElement.getBoundingClientRect();
                            return imageBox.width <= button.width && imageBox.height <= button.height;
                          });
                        }"""), width
                    await page.set_viewport_size({"width": 1274, "height": 820})
                    output = ROOT / "temp/visual-ui"
                    output.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(output / "studio-saved.png"))
                    assert await page.evaluate("document.adoptedStyleSheets.length") == 1, await page.evaluate("document.adoptedStyleSheets.length")
                    assert not await page.evaluate("window.cspViolations")
                    assert not bridge.errors, bridge.errors
                    assert not premature, premature
                    assert not errors, errors
                    assert await page.evaluate("window.blankFrames") == 0
                    assert await page.evaluate("window.blankCoverFrames") == 0
                    saved = json.loads((package / "character.json").read_text(encoding="utf-8"))
                    assert saved["visuals"]["resources"][0]["name"] == "保存后刷新"
                    await browser.close()
                    print("PASS: both covers load before selection, unchanged cover nodes/requests across four switches, zero blank cover/save frames, desktop CSP, 40 portraits, no image RPC, transient catalog timeout, early/late reload events, responsive layout")
            finally:
                bridge.close()
                application.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__": asyncio.run(run())
