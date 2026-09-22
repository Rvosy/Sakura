"""Production settings startup with a controlled native migration boundary."""
import functools
import http.server
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = r"""
window.migrationPhase = 'running';
window.pluginsLoading = true;
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
    snapshot:{generationId:'g',readiness:window.migrationPhase==='running'?'initializing':'ready',
      pluginMigration:{state:window.migrationPhase,completed:window.migrationPhase==='completed'?6:2,total:6,pluginId:'sakura.memory.mem0'}},
    characterPresentation:window.migrationPhase==='running'?null:presentation};
  if(command==='settings_plugins_get') return {state:window.pluginsLoading?'starting':'ready',plugins:[]};
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
    if(window.migrationPhase==='running') throw new Error('CHARACTER_PRESENTATION_NOT_READY');
    return {schemaVersion:1,windowGeneration:1,presentation,limits,appearance:{schemaVersion:1,coreGenerationId:'g',characterId:'sakura',values}};
  }
  if(command==='settings_asr_get') return {providers:[],preferences:{}};
  if(command==='settings_restart_after_migration') {window.migrationPhase='running';return;}
  if(command==='interaction_latency_diagnostics_enabled') return false;
  return {};
}},event:{listen:async()=>()=>{}},window:{getCurrentWindow:()=>({onCloseRequested:async()=>()=>{}})}};
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


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
            banner = page.locator(".migration-status")
            expect(banner).to_contain_text("2/6")
            page.wait_for_function("window.nativeCalls.some(c=>c.command==='reveal_settings_window')")
            expect(banner.get_by_role("progressbar")).to_have_attribute("max", "6")
            expect(banner.get_by_role("progressbar")).to_have_attribute("value", "2")
            expect(page.locator(".page-scroll")).to_be_hidden()
            assert page.locator(".nav-card").evaluate("element => element.inert")
            assert not page.evaluate("window.nativeCalls.some(c=>c.command==='settings_asr_get' || c.command==='settings_voice_get')")
            output = ROOT / "temp/visual-ui"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / "migration-running.png"), animations="disabled")
            page.evaluate("window.migrationPhase='completed'")
            expect(banner).to_contain_text("正在启动")
            expect(page.locator(".page-scroll")).to_be_hidden()
            assert not page.evaluate("window.nativeCalls.some(c=>c.command==='settings_asr_get')")
            page.evaluate("window.pluginsLoading=false")
            expect(page.locator("#portraitScale")).to_be_enabled()
            expect(banner).to_be_hidden()
            assert not page.locator(".nav-card").evaluate("element => element.inert")
            assert page.evaluate("window.nativeCalls.filter(c=>c.command==='reveal_settings_window').length") == 1
            assert page.evaluate("window.nativeCalls.findIndex(c=>c.command==='reveal_settings_window') < window.nativeCalls.findIndex(c=>c.command==='settings_asr_get')")

            page.reload()
            expect(banner).to_contain_text("2/6")
            page.evaluate("window.pluginsLoading=false")
            page.evaluate("window.migrationPhase='failed'")
            expect(banner.get_by_role("button", name="重启核心")).to_be_visible()
            expect(page.locator("#portraitScale")).to_be_enabled()
            expect(page.locator("#controlPanelWidth")).to_be_enabled()
            banner.get_by_role("button", name="重启核心").click()
            expect(banner.get_by_role("progressbar")).to_be_visible()
            page.evaluate("window.migrationPhase='completed'")
            expect(page.locator("#portraitScale")).to_be_enabled()
            expect(page.locator("#controlPanelWidth")).to_be_enabled()
            page.locator("#controlPanelWidth").focus()
            page.keyboard.press("ArrowRight")
            page.wait_for_function("window.nativeCalls.some(c=>c.command==='settings_character_appearance_layout_frame' || c.command==='settings_character_appearance_preview')")
            expect(banner).to_be_hidden()
            expect(banner.get_by_role("button", name="重启核心")).to_be_hidden()
            page.screenshot(path=str(output / "migration-completed.png"), animations="disabled")
            page.set_viewport_size({"width": 640, "height": 720})
            page.evaluate("window.migrationPhase='failed'")
            expect(banner.get_by_role("button", name="重启核心")).to_be_visible()
            assert banner.bounding_box()["width"] <= 640
            page.screenshot(path=str(output / "migration-failed-narrow.png"), animations="disabled")
            assert not errors, errors
            browser.close()
            print("PASS: visible migration before settings requests, plugin startup, recovery, restart and narrow layout")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
