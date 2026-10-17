"""验证设置页的滑块和数值输入。"""
import functools
import http.server
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = r"""
window.nativeCalls = [];
const themeTokens = {primary:'#4b9ac4',primaryHover:'#3b83aa',accent:'#e36c96',text:'#27445a',
  secondaryText:'#54768b',mutedText:'#7d99a9',pageBackground:'#f8fcfe',panelBackground:'#eaf5fa',
  inputBackground:'#ffffff',bubbleBackground:'#e3f1f7',border:'#accfde'};
const limits = {portraitScalePercent:[50,150,100],controlPanelWidth:[420,860,640],bubbleMaxHeight:[96,400,128],
  controlPanelVerticalOffset:[-400,400,0],inputBarOffset:[0,400,0],speechFontSize:[10,24,19],
  nameFontSize:[10,20,13],inputFontSize:[12,20,15]};
const values = {...Object.fromEntries(Object.entries(limits).map(([k,v])=>[k,v[2]])),
  themeTokens,visualEffectMode:'solid',bubbleAutoExpand:false};
const presentation = {generationId:'g',characterId:'sakura',displayName:'Sakura',themeTokens,portraitKeys:[]};
window.__TAURI__ = {core:{invoke:async(command,args)=>{
  window.nativeCalls.push({command,args});
  if(command==='runtime_lifecycle_snapshot') return {
    supervisor:{state:'running',generationId:'g',generationNumber:1},
    snapshot:{generationId:'g',readiness:'ready'},
    characterPresentation:presentation};
  if(command==='settings_plugins_get') return {state:'ready',plugins:[]};
  if(command==='settings_capability_manifest') {
    const sections={};
    for(const name of ['character','appearance']) {
      const features=Object.fromEntries([...document.querySelectorAll('#page-'+name+' [data-settings-feature]')].map(c=>[c.dataset.settingsFeature,'available']));
      sections[name]={status:'available',features};
    }
    sections.character.features['character.manage']='available';
    return {schemaVersion:1,windowGeneration:1,sections,unavailableReasons:{}};
  }
  if(command==='settings_characters_get') return {schemaVersion:1,revision:1,currentCharacterId:'sakura',
    characters:[{id:'sakura',displayName:'Sakura',hasVoice:false,hasExportableVoice:false}]};
  if(command==='settings_character_visuals_get') return {schemaVersion:1,characterId:'sakura',resources:[],defaultResourceId:null,preferenceResourceId:null};
  if(command==='settings_character_appearance_get') {
    return {schemaVersion:1,windowGeneration:1,presentation,limits,appearance:{schemaVersion:1,coreGenerationId:'g',characterId:'sakura',values}};
  }
  if(command==='settings_asr_get') return {providers:[],preferences:{}};
  if(command==='settings_voice_cache_get') return {directory:'',maxMegabytes:64,idleFill:false,limits:[32,20480]};
  if(command==='interaction_latency_diagnostics_enabled') return false;
  return {};
}},event:{listen:async()=>()=>{}},window:{getCurrentWindow:()=>({onCloseRequested:async()=>()=>{}})}};
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check_slider_value_editing(page, errors):
    slider = page.locator("#controlPanelWidth")
    output = page.locator("output[for='controlPanelWidth']")
    editor = page.locator(".slider-value-editor")
    slider.evaluate("element => { window.sliderInputs = []; element.addEventListener('input', () => window.sliderInputs.push(element.value)); }")

    for draft, key, expected, emitted in [
        ("720", "Enter", "720", ["720"]),
        ("680", "Escape", "720", ["720"]),
        ("700", "Tab", "700", ["720", "700"]),
        ("9999", "Enter", "860", ["720", "700", "860"]),
    ]:
        output.click()
        expect(editor).to_be_focused()
        editor.fill(draft)
        editor.press(key)
        expect(editor).to_have_count(0)
        expect(slider).to_have_value(expected)
        expect(output).to_have_text(expected)
        assert page.evaluate("window.sliderInputs") == emitted
        assert not errors, errors


def run():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("SAKURA_BROWSER_CHANNEL") or ("msedge" if sys.platform == "win32" else None))
            page = browser.new_page(viewport={"width": 960, "height": 760})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(BRIDGE)
            page.goto(f"http://127.0.0.1:{server.server_port}/desktop/frontend/settings/index.html")
            expect(page.locator("#portraitScale")).to_be_enabled()
            check_slider_value_editing(page, errors)
            expect(page.locator('.sakura-error-dialog')).to_have_count(0)

            browser.close()
            print("PASS: numeric Enter/Escape/blur, bounds and narrow layout")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
