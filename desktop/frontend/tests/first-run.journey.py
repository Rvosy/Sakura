"""Exercise welcome and settings pages with an isolated native bridge."""
import functools
import http.server
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = r"""
window.fixtureListeners = {};
window.__TAURI__ = {
  core: { invoke: async (command, args) => {
    if (command === 'first_run_start_core') await window.startGate;
    return window.firstRunCommand(command, args);
  } },
  event: { listen: async (name, callback) => {
    fixtureListeners[name] = callback;
    return () => { delete fixtureListeners[name]; };
  } }
};
"""
SETTINGS_BOOTSTRAP = r"""
import { createBridge } from '/desktop/frontend/prototypes/settings-demo/bridge.js';
import { createState } from '/desktop/frontend/prototypes/settings-demo/data.js';
createBridge(window, createState('portrait'), {version: 'proposed'});
const appWindow = window.__TAURI__.window.getCurrentWindow();
Object.assign(appWindow, {
  setDecorations: async () => {}, onResized: async () => () => {},
  isMaximized: async () => false, minimize: async () => {}, toggleMaximize: async () => {},
});
window.__TAURI__.window.getCurrentWindow = () => appWindow;
const invoke = window.__TAURI__.core.invoke;
window.__TAURI__.core.invoke = (command, args) => {
  if (command.startsWith('first_run_')) return window.firstRunCommand(command, args);
  if (command === 'settings_character_visuals_get') return Promise.resolve({
    schemaVersion: 1, characterId: args.characterId, resources: [], defaultResourceId: null, preferenceResourceId: null,
  });
  if (command === 'settings_voice_cache_get') return Promise.resolve({
    directory: '', maxMegabytes: 64, idleFill: false, limits: [32, 20480],
  });
  if (command === 'settings_provider_model_get') return Promise.resolve({
    schema_version: 2, window_generation: 1, core_generation_id: 'demo-1', providers: [],
    model_slots: [{identity: 'core:chat', label: '对话模型', required: true,
      selection: {serviceKey: '', profileId: '', modelId: ''}}],
  });
  return invoke(command, args);
};
await import('/desktop/frontend/settings/settings.js');
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run():
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    state = {"completed": False, "fail_start": True, "macos": False}
    calls = []

    def invoke(command, args=None):
        calls.append((command, args))
        if command == "first_run_guide_get":
            return {"completed": state["completed"]}
        if command == "first_run_start_core" and state["fail_start"]:
            state["fail_start"] = False
            raise RuntimeError("fixture Core could not open its configuration")
        if command == "first_run_guide_complete":
            state["completed"] = True
        if command == "settings_capability_manifest":
            return {"sections": {"open-help": {"status": "available" if state["macos"] else "unavailable"}}}
        return None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge" if os.name == "nt" else None, headless=True
            )
            page = browser.new_page(viewport={"width": 980, "height": 720})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.expose_function("firstRunCommand", invoke)
            page.add_init_script(BRIDGE)
            settings_html = (ROOT / "desktop/frontend/settings/index.html").read_text(encoding="utf-8")
            page.route("**/settings/index.html*", lambda route: route.fulfill(
                content_type="text/html", body=settings_html.replace(
                    'src="./settings.js"', 'src="/first-run-settings.js"'
                )
            ))
            page.route("**/first-run-settings.js", lambda route: route.fulfill(
                content_type="text/javascript", body=SETTINGS_BOOTSTRAP
            ))
            welcome = f"{origin}/desktop/frontend/onboarding/index.html"
            page.goto(welcome)
            print("Checking welcome and Core startup", flush=True)
            start = page.get_by_role("button", name="开始使用", exact=True)
            expect(start).to_be_focused()
            assert not any(command == "first_run_start_core" for command, _ in calls)
            page.evaluate("document.fonts.ready")
            (ROOT / "temp").mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT / "temp/first-start.png"), animations="disabled")
            page.evaluate("() => { window.startGate = new Promise(resolve => { window.finishStart = resolve; }); }")
            start.click()
            expect(start).to_be_disabled()
            expect(page.get_by_role("status")).to_contain_text("正在启动")
            assert page.url == welcome
            page.evaluate("window.finishStart()")
            error_dialog = page.get_by_role("dialog", name="操作失败")
            expect(error_dialog).to_be_visible()
            error_dialog.get_by_text("错误详情", exact=True).click()
            expect(error_dialog.locator("pre")).to_contain_text("fixture Core could not open its configuration")
            error_dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(start).to_be_enabled()
            start.click()

            print("Checking configuration guide", flush=True)
            expect(page).to_have_url(f"{origin}/desktop/frontend/settings/index.html?guide=first-run")
            title = page.locator("#firstRunGuideTitle")
            expect(title).to_have_text("导入角色")
            assert not page.locator("dialog[open]").count(), page.locator("dialog[open]").inner_text()
            guide = page.locator(".first-run-guide")
            guide.get_by_role("button", name="下一步").click()
            expect(title).to_have_text("添加模型服务")
            guide.get_by_role("button", name="下一步").click()
            expect(title).to_have_text("选择模型")
            guide.get_by_role("button", name="完成", exact=True).click()
            expect(guide).to_be_hidden()
            assert state["completed"] is True
            expect(page).to_have_url(f"{origin}/desktop/frontend/settings/index.html")
            page.locator('.nav-item[data-page="system"]').click()
            open_data = page.get_by_role("button", name="打开数据目录", exact=True)
            expect(open_data).to_be_enabled()
            page.screenshot(path=str(ROOT / "temp/first-run-system.png"), animations="disabled")

            starts = sum(command == "first_run_start_core" for command, _ in calls)
            print("Checking returning users and macOS help", flush=True)
            page.goto(welcome)
            expect(page).to_have_url(f"{origin}/desktop/frontend/settings/index.html")
            expect(page.locator(".first-run-guide")).to_be_hidden()
            expect(page.locator("#characterImportButton")).to_be_enabled()
            expect(page.locator("#characterExportButton")).to_be_enabled()
            assert sum(command == "first_run_start_core" for command, _ in calls) == starts

            state.update(completed=False, macos=True)
            page.goto(welcome)
            page.get_by_role("button", name="应用打开遇到问题？", exact=True).click()
            help_view = page.locator("#macosOpenHelpView")
            expect(help_view).to_be_visible()
            help_view.get_by_role("button", name="打开系统设置", exact=True).click()
            expect(page.locator("#macosOpenHelpStatus")).to_contain_text("系统设置已打开")
            help_view.get_by_role("button", name="返回", exact=True).click()
            expect(start).to_be_visible()
            page.evaluate("fixtureListeners['sakura://settings-close-requested']({})")
            page.wait_for_function("!document.querySelector('dialog[open]')")
            assert state["completed"] is False
            assert ("resolve_settings_close", {"discard": True}) in calls
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("PASS: welcome, Core startup retry, configuration guide, returning users, and macOS help")


if __name__ == "__main__":
    run()
