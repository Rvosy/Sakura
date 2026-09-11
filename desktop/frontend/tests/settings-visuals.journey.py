"""Production settings markup and character feature against a real Core boundary.

Only native transport/lifecycle and unrelated settings sections are simulated.
"""
import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from app.core_host.character_settings import CharacterSettingsBoundary

spec = importlib.util.spec_from_file_location("fixture", ROOT / "tests/integration/test_visual_plugin_boundary.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="sakura-settings-visuals-") as temporary, fixture.numeric_application(Path(temporary)) as (application, package, resource), sync_playwright() as playwright:
            path = package / "character.json"
            manifest = json.loads(path.read_text())
            manifest["visuals"]["resources"][0]["name"] = "日常形态"
            manifest["visuals"]["resources"].append({**resource.to_mapping(), "id": "numeric-2", "name": "另一套形态"})
            path.write_text(json.dumps(manifest), encoding="utf-8")
            original = path.read_bytes()
            boundary = CharacterSettingsBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
            boundary.select("character")
            generation = 1
            visual_reads = 0
            def invoke(command, params=None):
                nonlocal generation, visual_reads
                params = params or {}
                if command == "settings_characters_get": return boundary.snapshot()
                if command == "settings_character_visuals_get":
                    visual_reads += 1
                    return boundary.visual_snapshot(params["characterId"])
                if command == "settings_character_select":
                    result = boundary.select(params["characterId"], params.get("visualSelections"))
                    previous = f"g{generation}"
                    restart = result["changePlan"] == "core_restart_required"
                    if restart:
                        generation += 1
                        application.bind_character_presentation(params["characterId"])
                    return {"schemaVersion": 1, "snapshot": result["snapshot"], "targetCharacterId": params["characterId"], "previousCoreGenerationId": previous, "restartState": "requested" if restart else "not_required"}
                if command == "runtime_lifecycle_snapshot":
                    return {"supervisor": {"generationId": f"g{generation}", "generationNumber": generation}, "snapshot": {"generationId": f"g{generation}", "readiness": "ready"}, "characterPresentation": {"generationId": f"g{generation}", "characterId": "character"}}
                if command == "settings_character_visual_preview":
                    return {"schemaVersion": 1, "windowGeneration": 1, "revision": params["revision"], "presentation": {"generationId": f"g{generation}", "characterId": "character"}, "appearance": {"coreGenerationId": f"g{generation}", "characterId": "character", "values": {"themeTokens": {key: "#112233" for key in ["primary", "primaryHover", "accent", "text", "secondaryText", "mutedText", "pageBackground", "panelBackground", "inputBackground", "bubbleBackground", "border"]}}}}
                raise ValueError(command)
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None))
            page = browser.new_page(viewport={"width": 1100, "height": 850})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_function("nativeInvoke", invoke)
            production = (ROOT / "desktop/frontend/settings/settings.js").read_text(encoding="utf-8")
            busy_function = production.split("function setSubmissionBusy(busy) {", 1)[1].split("\nasync function closeSettingsWindow", 1)[0]
            script = """
              import {createCharacterSettingsFeature} from './character-settings.js';
              import {enhanceSelect,refreshSelect} from './select-control.js';
              let feature,submissionBusy=false;
              const submissionDisabledStates=new Map();
              const fields=Object.fromEntries(['applyButton','saveButton','cancelButton'].map(id=>[id,document.getElementById(id)]));
              const dirty=()=>{fields.applyButton.disabled=submissionBusy||!feature?.isDirty();fields.saveButton.disabled=submissionBusy||!feature?.isDirty();};
              function refreshDirty(){dirty();}
              /* PRODUCTION_BUSY_FUNCTION */
              feature=createCharacterSettingsFeature({document,window,invoke:window.nativeInvoke,onDirty:dirty,onError:message=>{document.getElementById('characterArchiveHint').textContent=message;},notify:()=>{},enhanceSelect,refreshSelect,hasCharacterDrafts:()=>false,isSubmitting:()=>submissionBusy,applyPreviewTheme:()=>{},rebindSettings:async()=>{},clearCharacterState:()=>{},renderMemorySurface:()=>{},openPlugin:(id,configure)=>{window.pluginRequest={id,configure};}});
              const runtimeCharacterFeature=feature;
              window.feature=feature;
              await feature.initialize(); feature.prepareControls();
              document.getElementById('applyButton').onclick=async()=>{setSubmissionBusy(true);try{await feature.commit();}finally{setSubmissionBusy(false);}};
              window.discard=async()=>{await feature.discard();dirty();};
              dirty(); document.body.dataset.ready='true';
            """.replace("/* PRODUCTION_BUSY_FUNCTION */", "function setSubmissionBusy(busy) {" + busy_function)
            page.route("**/settings/settings.js", lambda route: route.fulfill(content_type="text/javascript", body=script))
            page.goto(origin + "/desktop/frontend/settings/")
            expect(page.get_by_role("combobox", name="显示方式", exact=True)).to_be_enabled()
            expect(page.locator("#visualSelect")).to_have_value("numeric-1")
            expect(page.locator("#visualSelect option")).to_have_text(["日常形态", "另一套形态"])
            before = visual_reads
            page.evaluate("() => { for (let i=0; i<20; i++) window.dispatchEvent(new Event('focus')); }")
            expect(page.get_by_role("combobox", name="显示方式", exact=True)).to_be_enabled()
            assert visual_reads == before + 1
            page.get_by_role("combobox", name="显示方式", exact=True).click()
            page.get_by_role("option", name="另一套形态", exact=True).click()
            expect(page.locator("#applyButton")).to_be_enabled()
            assert boundary.visual_snapshot("character")["preferenceResourceId"] is None
            page.evaluate("window.discard()")
            expect(page.locator("#visualSelect")).to_have_value("numeric-1")
            page.get_by_role("combobox", name="显示方式", exact=True).click()
            page.get_by_role("option", name="另一套形态", exact=True).click()
            page.locator("#applyButton").click()
            expect(page.locator("#applyButton")).to_be_disabled()
            assert application.visual_presentation()["visual"]["resourceId"] == "numeric-2"
            expect(page.get_by_role("combobox", name="显示方式", exact=True)).to_be_enabled()
            expect(page.locator("#visualConfigure")).to_be_enabled()
            page.get_by_role("combobox", name="显示方式", exact=True).click()
            page.get_by_role("option", name="日常形态", exact=True).click()
            page.locator("#applyButton").click()
            expect(page.locator("#applyButton")).to_be_disabled()
            assert application.visual_presentation()["visual"]["resourceId"] == "numeric-1"
            page.reload()
            expect(page.locator("#visualSelect")).to_have_value("numeric-1")
            page.locator("#visualConfigure").click()
            assert page.evaluate("window.pluginRequest.configure") is True
            install_id = boundary.visual_snapshot("character")["resources"][0]["installId"]
            application.set_enabled(install_id, False)
            page.evaluate("window.feature.onPageChanged('character')")
            expect(page.locator("#visualStatus")).to_be_visible()
            expect(page.locator("#visualConfigure")).to_be_disabled()
            page.locator("#visualPluginAction").click()
            assert page.evaluate("window.pluginRequest") == {"id": install_id, "configure": False}
            application.set_enabled(install_id, True)
            page.evaluate("window.feature.onPageChanged('character')")
            expect(page.locator("#visualStatus")).to_be_hidden()
            output = ROOT / "temp/visual-ui/settings.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output), full_page=True)
            assert path.read_bytes() == original
            # Same-character workshop publication refreshes the list without discarding a pending choice.
            page.get_by_role("combobox", name="显示方式", exact=True).click()
            page.get_by_role("option", name="另一套形态", exact=True).click()
            manifest["visuals"]["resources"].append({**resource.to_mapping(), "id": "numeric-3", "name": "新形态"})
            path.write_text(json.dumps(manifest), encoding="utf-8")
            page.evaluate("window.feature.refreshCatalog({})")
            expect(page.locator("#visualSelect option")).to_have_count(3)
            expect(page.locator("#visualSelect")).to_have_value("numeric-2")
            assert not errors, errors
            browser.close()
            print("PASS: settings selection, discard, apply, rebind, reopen and plugin status; package unchanged")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__": run()
