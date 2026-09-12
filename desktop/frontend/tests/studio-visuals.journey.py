"""Real Studio/portrait editor journey and visual QA using isolated packages."""
import base64
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from app.agent.tools import ToolRegistry
from app.core_host.character_studio import CharacterStudioBoundary
from app.core_host.plugin_application import PluginApplicationHost
from app.storage.runtime_roots import RuntimeRoots


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    assets = {}
    def log_message(self, *_args): pass
    def do_GET(self):
        prefix, _, relative = self.path.rpartition("/")
        if prefix not in self.assets:
            return super().do_GET()
        data = (self.assets[prefix] / bytes.fromhex(relative).decode()).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        self.wfile.write(data)


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="sakura-studio-visuals-") as temporary, sync_playwright() as playwright:
            root = Path(temporary)
            distribution, user = root / "distribution", root / "user"
            shutil.copytree(ROOT / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
            package = user / "characters/sample"
            (package / "portraits").mkdir(parents=True)
            for name in ["neutral", "happy"]:
                shutil.copyfile(ROOT / "desktop/frontend/prototypes/asr/assets/navi.png", package / f"portraits/{name}.png")
            source = root / "additional.png"
            shutil.copyfile(package / "portraits/neutral.png", source)
            (package / "card.md").write_text("示例角色的人设", encoding="utf-8")
            (package / "character.json").write_text(json.dumps({"id": "sample", "display_name": "示例角色", "card": "card.md", "portrait": {"default": "portraits/neutral.png", "expressions": {"默认": "portraits/happy.png"}}}), encoding="utf-8")
            application = PluginApplicationHost(RuntimeRoots(distribution, user), "g", ToolRegistry())
            application.start()
            try:
                boundary = CharacterStudioBoundary("g", "c", user, plugin_application_provider=lambda: application)
                def invoke(command, params=None):
                    params = params or {}
                    if command == "studio_bootstrap": return boundary._dispatch("studio.bootstrap", {"initialCharacterId": "sample"})
                    if command in {"show_studio", "close_character_studio"}: return None
                    if command == "studio_choose_source": return [str(source)] if params.get("multiple") else str(source)
                    if command != "studio_request": raise ValueError(command)
                    result = boundary._dispatch(params["method"], params["params"])
                    if params["method"] == "studio.visual.open":
                        result["presentation"]["visual"]["editor"] = origin + "/plugins/builtin/sakura_portrait/frontend/editor.js"
                        prefix = "/preview/" + result["presentation"]["visual"]["bindingId"]
                        QuietHandler.assets[prefix] = Path(result.pop("assetRootPath"))
                        result["assetBaseUrl"] = origin + prefix + "/"
                    if params["method"] == "studio.visual.previews":
                        for item in result["items"]:
                            cover_source = item.pop("sourcePath", None)
                            item["previewUrl"] = None
                            if cover_source:
                                cover_source = Path(cover_source)
                                prefix = "/cover/" + item["resourceId"]
                                QuietHandler.assets[prefix] = cover_source.parent
                                item["previewUrl"] = origin + prefix + "/" + cover_source.name.encode().hex()
                    return result
                browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None))
                page = browser.new_page(viewport={"width": 1274, "height": 820})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.expose_function("nativeInvoke", invoke)
                page.add_init_script("""
                  window.__discardRequests = 0;
                  window.confirm = () => { throw Error('Native confirm is unavailable in this WebView'); };
                  window.__TAURI__={core:{invoke:async (command,params)=>{
                    if(params?.method==='studio.draft.discard') window.__discardRequests++;
                    const result=await window.nativeInvoke(command,params);
                    if(params?.method==='studio.draft.save' && window.__holdDraftSave) {
                      window.__holdDraftSave=false;
                      await new Promise(resolve=>{window.__releaseDraftSave=resolve;});
                    }
                    return result;
                  }},event:{listen:async()=>()=>{}}};
                """)
                page.goto(origin + "/desktop/frontend/studio/")
                expect(page.locator("#displayName")).to_have_value("示例角色")
                output = ROOT / "temp/visual-ui"
                output.mkdir(parents=True, exist_ok=True)
                # The host may not implement window.confirm. Cancellation and
                # confirmation must both work via the visible Studio dialog.
                page.locator("#displayName").fill("保留本次编辑")
                page.get_by_role("button", name="放弃修改", exact=True).click()
                discard = page.get_by_role("dialog", name="放弃修改", exact=True)
                expect(discard).to_be_visible()
                discard.get_by_role("button", name="继续编辑", exact=True).click()
                expect(page.locator("#displayName")).to_have_value("保留本次编辑")
                page.evaluate("window.__holdDraftSave=true")
                page.locator("#displayName").fill("自动保存中的修改")
                page.wait_for_function("typeof window.__releaseDraftSave === 'function'")
                page.locator("#displayName").fill("尚未自动保存的修改")
                page.get_by_role("button", name="放弃修改", exact=True).click()
                expect(discard).to_be_visible()
                page.screenshot(path=str(output / "studio-discard.png"), animations="disabled")
                discard.get_by_role("button", name="放弃修改", exact=True).click()
                # Cross the pending autosave timer while the older write is held.
                page.wait_for_timeout(750)
                assert page.evaluate("window.__discardRequests") == 0
                page.evaluate("window.__releaseDraftSave()")
                expect(page.locator("#displayName")).to_have_value("示例角色")
                expect(page.get_by_role("button", name="放弃修改", exact=True)).to_be_disabled()
                page.reload()
                expect(page.locator("#displayName")).to_have_value("示例角色")
                expect(page.locator("#errorText")).to_be_empty()
                page.get_by_role("button", name="角色形态", exact=True).click()
                expect(page.locator(".portrait-plugin .expression-row")).to_have_count(2)
                page.wait_for_function("[...document.querySelectorAll('.expression-thumbnail img')].every(image=>image.naturalWidth>0)")
                # A legacy expression named 默认 may refer to a different PNG
                # than the default image. Edit only the generated row, publish
                # and reopen, retaining the original label and both paths.
                labels = page.get_by_role("textbox", name="表情标签", exact=True)
                assert len(set(labels.evaluate_all("items => items.map(item => item.value)"))) == 2
                expect(labels.nth(1)).to_have_value("默认")
                default_label = labels.first.input_value() + "（补充）"
                labels.first.fill(default_label)
                page.locator("#visualName").fill("兼容立绘")
                page.locator("#saveButton").click()
                expect(page.locator("#saveButton")).to_be_enabled()
                saved = json.loads((package / "character.json").read_text(encoding="utf-8"))
                resource = saved["visuals"]["resources"][0]
                portrait_data = json.loads((package / resource["root"] / resource["entry"]).read_text(encoding="utf-8"))
                assert "expressionRows" not in portrait_data
                assert portrait_data["default"] == "portraits/neutral.png"
                assert portrait_data["expressions"]["默认"] == "portraits/happy.png"
                page.reload()
                expect(page.locator("#displayName")).to_have_value("示例角色")
                page.get_by_role("button", name="角色形态", exact=True).click()
                expect(labels).to_have_count(2)
                expect(labels.first).to_have_value(default_label)
                expect(labels.nth(1)).to_have_value("默认")
                page.locator("#visualName").fill("第一次改名")
                page.wait_for_function("!document.body.classList.contains('is-dirty')")
                page.locator("#visualName").fill("日常立绘")
                page.wait_for_function("!document.body.classList.contains('is-dirty')")
                expect(page.locator(".form-card strong")).to_have_text("日常立绘")
                labels = page.get_by_role("textbox", name="表情标签", exact=True)
                for unfinished in ["", default_label]:
                    labels.nth(1).fill(unfinished)
                    page.wait_for_function("!document.body.classList.contains('is-dirty')")
                    page.locator(".form-card").filter(has_text="日常立绘").click()
                    expect(labels).to_have_count(2)
                    expect(labels.nth(1)).to_have_value(unfinished)
                page.reload()
                expect(page.locator("#displayName")).to_have_value("示例角色")
                page.get_by_role("button", name="角色形态", exact=True).click()
                expect(labels).to_have_count(2)
                expect(labels.nth(1)).to_have_value(default_label)
                page.locator("#saveButton").click()
                expect(page.locator("#errorText")).to_contain_text("表情标签")
                labels.nth(1).fill("开心")
                page.get_by_role("textbox", name="表情标签", exact=True).first.fill("平静")
                page.locator(".expression-thumbnail").first.click()
                expect(page.get_by_role("dialog", name="平静", exact=True)).to_be_visible()
                page.get_by_role("dialog").get_by_role("button", name="关闭", exact=True).click()
                page.get_by_role("button", name="添加形态", exact=True).click()
                add = page.get_by_role("dialog", name="添加形态", exact=True)
                add.get_by_role("textbox", name="形态名称", exact=True).fill("第二套立绘")
                output = ROOT / "temp/visual-ui"
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "studio-add.png"), animations="disabled")
                add.get_by_role("button", name="添加", exact=True).click()
                expect(page.locator("#visualName")).to_have_value("第二套立绘")
                page.get_by_role("button", name="添加图片", exact=True).click()
                expect(page.locator(".portrait-plugin .expression-row")).to_have_count(1)
                page.get_by_role("button", name="设为默认", exact=True).click()
                page.locator("#saveButton").click()
                expect(page.locator("#saveButton")).to_be_enabled()
                page.reload()
                expect(page.locator("#displayName")).to_have_value("示例角色")
                page.get_by_role("button", name="角色形态", exact=True).click()
                expect(page.locator("#visualName")).to_have_value("第二套立绘")
                expect(page.locator("#defaultVisualButton")).to_be_disabled()
                page.screenshot(path=str(output / "studio-default-form.png"), animations="disabled")
                page.locator(".form-card").filter(has_text="日常立绘").click()
                expect(page.locator(".portrait-plugin .expression-row")).to_have_count(2)
                expect(page.get_by_role("textbox", name="表情标签", exact=True).first).to_have_value("平静")
                page.wait_for_function("[...document.querySelectorAll('.expression-thumbnail img')].every(image=>image.naturalWidth>0)")
                page.screenshot(path=str(output / "studio.png"), animations="disabled")
                assert "sakura.visual.portrait@1" not in page.locator("#page-portrait").inner_text()
                for width in [820, 680]:
                    page.set_viewport_size({"width": width, "height": 800})
                    assert page.evaluate("document.querySelector('.page-scroll').scrollWidth <= document.querySelector('.page-scroll').clientWidth"), width
                page.screenshot(path=str(output / "studio-narrow.png"), animations="disabled")
                saved = json.loads((package / "character.json").read_text(encoding="utf-8"))
                resources = saved["visuals"]["resources"]
                assert [item["name"] for item in resources] == ["日常立绘", "第二套立绘"]
                assert saved["visuals"]["default"] == resources[1]["id"]
                portrait_data = json.loads((package / resources[0]["root"] / resources[0]["entry"]).read_text(encoding="utf-8"))
                assert "expressionRows" not in portrait_data
                assert set(portrait_data["expressions"]) == {"平静", "开心"}
                source = root / "missing.visual"
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("manifest.json", json.dumps({"format": "sakura.character.archive", "version": 2, "kind": "resource", "resource": {"type": "fixture.missing@1", "entry": "resource.json", "name": "缺少插件的形态", "pluginRequirements": [{"kind": "visual", "type": "fixture.missing@1", "plugins": [{"id": "fixture.visual", "name": "示例形态插件"}]}]}}))
                    archive.writestr("resource/resource.json", '{"private":true}')
                page.get_by_role("button", name="导入形态", exact=True).click()
                page.locator(".form-card").filter(has_text="缺少插件的形态").click()
                expect(page.locator("#expressionList")).to_contain_text("尚未安装支持此形态的插件")
                expect(page.locator("#errorText")).to_be_empty()
                page.locator("#saveButton").click()
                expect(page.locator("#saveButton")).to_be_enabled()
                saved = json.loads((package / "character.json").read_text())
                missing = next(item for item in saved["visuals"]["resources"] if item["type"] == "fixture.missing@1")
                assert saved["visuals"]["default"] == missing["id"]
                assert json.loads((package / missing["root"] / missing["entry"]).read_text()) == {"private": True}
                page.get_by_role("button", name="基础信息", exact=True).click()
                expect(page.locator("#pluginRequirementsList")).to_contain_text("示例形态插件")
                expect(page.locator("#pluginRequirementsList")).to_contain_text("未安装")
                expect(page.locator(".plugin-requirement").filter(has_text="立绘").locator(".plugin-requirement-status")).to_have_text("已启用")
                page.set_viewport_size({"width": 1200, "height": 800})
                page.locator("#pluginRequirementsList").scroll_into_view_if_needed()
                page.screenshot(path=str(output / "studio-requirements.png"), animations="disabled")
                page.evaluate("""() => {
                  const tokens = {'primary':'#2d9b70','primary-hover':'#3db884','text':'#e4efeb','secondary-text':'#aac9bc',
                    'muted-text':'#83a396','page-bg':'#131e20','panel-bg':'#182728','input-bg':'#1c3030','border':'#345553'};
                  for(const [name,value] of Object.entries(tokens)) document.documentElement.style.setProperty('--sakura-'+name,value);
                }""")
                page.screenshot(path=str(output / "studio-requirements-dark.png"), animations="disabled")
                page.set_viewport_size({"width": 680, "height": 800})
                page.locator("#pluginRequirementsList").scroll_into_view_if_needed()
                page.screenshot(path=str(output / "studio-requirements-narrow.png"), animations="disabled")
                assert page.evaluate("document.querySelector('.page-scroll').scrollWidth <= document.querySelector('.page-scroll').clientWidth")
                assert saved["pluginRequirements"][-1]["plugins"][0]["id"] == "fixture.visual"
                assert not errors, errors
                browser.close()
                print("PASS: Studio cards/name/default/add -> portrait preview/edit/import -> save/reopen; visual QA at 1274, 820 and 680 px")
            finally:
                application.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__": run()
