import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const index = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const fakeCore = readFileSync(new URL("../chat/fake-chat-core.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const nativeInteraction = readFileSync(new URL("../../src-tauri/src/window_interaction.rs", import.meta.url), "utf8");
const nativeMain = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const nativeWindowBackend = readFileSync(new URL("../../src-tauri/src/platform/window_backend.rs", import.meta.url), "utf8");
const tauriConfig = JSON.parse(readFileSync(new URL("../../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const legacySettingsConfig = JSON.parse(
  readFileSync(new URL("../../../tools/settings-tauri/src-tauri/tauri.conf.json", import.meta.url), "utf8"),
);

function declarationBlock(selector, requiredDeclaration = null) {
  const blocks = [...styles.matchAll(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`, "g"))].map((match) => match[1]);
  return requiredDeclaration ? blocks.find((block) => block.includes(requiredDeclaration)) || "" : blocks.at(-1) || "";
}

test("markup exposes fixed product chat, portrait, status, and accessible controls", () => {
  for (const id of ["chat-bubble", "bubble-copy", "composer-input", "composer-send", "typewriter-skip", "portrait-current", "close-window"])
    assert.match(index, new RegExp(`id="${id}"`), id);
  assert.match(index, /aria-live="polite"/);
  assert.match(index, /maxlength="4096"/);
  assert.match(index, /id="bubble-copy"[^>]*data-interactive="true"/);
  assert.match(index, /id="acceptance-entry"[^>]*hidden[^>]*aria-hidden="true"/);
  for (const forbidden of ["state-rail", "FAKE CORE", "geometry-readout", "theme-button", "composer-toggle", "visibility-probe"])
    assert.equal(index.includes(forbidden), false, forbidden);
});

test("rounded WebView surfaces preserve the native clip contract without external effects", () => {
  assert.doesNotMatch(styles, /filter\s*:\s*drop-shadow/i);
  assert.doesNotMatch(styles, /\.portrait-frame::after/);
  for (const [selector, radius] of [["bubble", 20], ["composer", 26]]) {
    const block = declarationBlock(selector, "border-radius");
    assert.match(block, new RegExp(`border-radius:\\s*${radius}px`), selector);
  }
  for (const selector of ["pet-stage", "portrait", "portrait-frame", "portrait-image"]) {
    const block = declarationBlock(selector, "background");
    assert.match(block, /background:\s*transparent/, selector);
  }
  assert.match(nativeInteraction, /const BUBBLE_CORNER_RADIUS: u32 = 20;/);
  assert.match(nativeInteraction, /const INPUT_CORNER_RADIUS: u32 = 26;/);
  assert.match(nativeInteraction, /const CONTROLS_CORNER_RADIUS: u32 = 15;/);
  assert.match(nativeInteraction, /const NATIVE_ANTIALIAS_BLEED_LOGICAL_PX: f64 = 2\.0;/);
  assert.match(nativeInteraction, /portrait_rect,[\s\S]*?0,[\s\S]*?\)\?/);
});

test("the pet window stays hidden until the native borderless surface and regions are ready", () => {
  const mainWindow = tauriConfig.app.windows.find((window) => window.label === "main");
  assert.equal(mainWindow.decorations, false);
  assert.equal(mainWindow.transparent, true);
  assert.equal(mainWindow.shadow, false);
  assert.equal(mainWindow.visible, false);
  assert.doesNotMatch(nativeMain, /set_title\(""\)/);
  assert.match(nativeMain, /\.setup\(\|app\|[\s\S]*?prepare_initial_pet_window\(&window\)/);
  const nativeSurface = nativeMain.match(/fn apply_native_pet_surface[\s\S]*?\n}\n\nfn prepare_initial_pet_window/)?.[0] || "";
  const prepareIndex = nativeSurface.indexOf(".prepare_window(window)");
  const boundsIndex = nativeSurface.indexOf(".apply_bounds(window");
  const regionsIndex = nativeSurface.indexOf("apply_native_interaction_region(window");
  const showIndex = nativeSurface.indexOf(".show()");
  assert.ok(prepareIndex >= 0);
  assert.ok(prepareIndex < boundsIndex && boundsIndex < regionsIndex && regionsIndex < showIndex);
  assert.match(nativeWindowBackend, /WS_CAPTION/);
  assert.match(nativeWindowBackend, /SWP_FRAMECHANGED/);
  assert.match(nativeWindowBackend, /GetWindowLongW/);
  assert.match(nativeWindowBackend, /native frame bits survived style refresh/);
});

test("empty portrait layers stay hidden instead of painting WebView2 broken-image frames", () => {
  assert.match(styles, /\.portrait-image:not\(\[src\]\)\s*\{[^}]*visibility:\s*hidden/);
  const imageBlock = declarationBlock("portrait-image");
  assert.match(imageBlock, /display:\s*block/);
  assert.match(imageBlock, /border:\s*0/);
});

test("bubble typography uses language-owned families and only real product weights", () => {
  const bubbleCopyBlock = styles.match(/\.bubble-copy\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(bubbleCopyBlock, /Yu Mincho|SimSun|(^|[^-])\bserif\b/);
  assert.match(bubbleCopyBlock, /font-weight:\s*400/);
  assert.match(styles, /\.bubble-copy \[lang\|="zh"\]/);
  assert.match(styles, /\.bubble-copy \[lang\|="ja"\]/);
  assert.match(styles, /\.bubble-copy \[lang\|="ko"\]/);
  assert.match(styles, /\.bubble-copy \[lang="en"\]/);
  assert.match(styles, /--font-zh:\s*"Microsoft YaHei UI",\s*"Microsoft YaHei"/);
  assert.match(styles, /--font-ja:\s*Meiryo,\s*"Meiryo UI"/);
  assert.doesNotMatch(styles, /font-weight:\s*(?:500|650)\b/);
  assert.match(app, /renderMultilingualText/);
  assert.match(app, /input\.lang = inferTextLanguage\(input\.value\)/);
  assert.match(index, /id="composer-input"[\s\S]*?lang="zh-CN"/);
});

test("WP-3-03 presentation never invokes the real chat Gateway or reads network and product data", () => {
  for (const forbidden of ["chat_send", "chat_cancel", "fetch(", "localStorage", "sessionStorage", "characters/", "data/"])
    assert.equal(fakeCore.includes(forbidden), false, forbidden);
  assert.equal(app.includes('invoke("chat_'), false);
  assert.equal(app.includes("runtime_lifecycle_snapshot"), false);
  assert.equal(app.includes("window.setInterval"), false);
});

test("CSP admits only controlled character URLs and keeps network/media sources closed", () => {
  const csp = tauriConfig.app.security.csp;
  assert.match(csp, /img-src 'self'/);
  assert.match(csp, /http:\/\/sakura-character\.localhost/);
  assert.match(csp, /sakura-character:/);
  assert.match(csp, /connect-src 'self' ipc: http:\/\/ipc\.localhost/);
  assert.match(csp, /media-src 'none'/);
  assert.equal(csp.includes("data:"), false);
  assert.equal(csp.includes("https:"), false);
});

test("the runtime and legacy host consume one canonical settings frontend", () => {
  assert.equal(legacySettingsConfig.build.frontendDist, "../../../desktop/frontend/settings");
  assert.match(nativeMain, /frontend\/settings\/index\.html/);
  assert.match(nativeMain, /frontend\/settings\/settings\.js/);
  assert.match(nativeMain, /frontend\/settings\/capability-shell\.js/);
});

test("product menu is native-owned and the settings window is a decorated singleton", () => {
  assert.match(app, /invoke\("show_pet_context_menu"/);
  assert.doesNotMatch(index, /context-menu|menu-popover/);
  assert.match(nativeMain, /ProductMenuAction::from_id/);
  assert.match(nativeMain, /show_or_focus_settings/);
  assert.match(nativeMain, /SETTINGS_WINDOW_LABEL/);
});

test("portrait click-through is tightened after the decoded contain size is known", () => {
  assert.match(app, /invoke\("activate_portrait_hit_test"/);
  assert.match(nativeMain, /fn activate_portrait_hit_test/);
  assert.match(nativeInteraction, /logical_hit_regions_with_portrait_size/);
  assert.match(nativeInteraction, /contained_portrait_rect/);
  assert.match(nativeMain, /active_portrait_alpha_mask/);
  assert.match(nativeInteraction, /alpha_hit_rectangles/);
  assert.match(nativeInteraction, /portrait_alpha_mask/);
});

test("Windows drag reasserts the borderless invariant without weakening native click-through", () => {
  assert.match(nativeInteraction, /SetWindowRgn\(hwnd, Some/);
  assert.match(nativeInteraction, /SetWindowSubclass/);
  assert.doesNotMatch(nativeInteraction, /GWLP_WNDPROC/);
  assert.match(nativeInteraction, /WM_NCCALCSIZE/);
  assert.match(nativeInteraction, /WM_NCPAINT/);
  const dragBackend = nativeWindowBackend.slice(
    nativeWindowBackend.indexOf("fn start_drag("),
    nativeWindowBackend.indexOf("fn set_visible("),
  );
  assert.equal(dragBackend.match(/self\.prepare_window\(window\)\?/g)?.length, 2);
  assert.ok(dragBackend.indexOf("self.prepare_window(window)?") < dragBackend.indexOf("start_native_drag"));
  assert.ok(dragBackend.lastIndexOf("self.prepare_window(window)?") > dragBackend.indexOf("start_native_drag"));
});
