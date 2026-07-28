import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const index = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const fakeCore = readFileSync(new URL("../chat/fake-chat-core.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const nativeInteraction = readFileSync(new URL("../../src-tauri/src/window_interaction.rs", import.meta.url), "utf8");
const nativeMain = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const nativeProductShell = readFileSync(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");
const nativeWindowBackend = readFileSync(new URL("../../src-tauri/src/platform/window_backend.rs", import.meta.url), "utf8");
const cargoManifest = readFileSync(new URL("../../src-tauri/Cargo.toml", import.meta.url), "utf8");
const tauriConfig = JSON.parse(readFileSync(new URL("../../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const tauriCapability = JSON.parse(
  readFileSync(new URL("../../src-tauri/capabilities/default.json", import.meta.url), "utf8"),
);
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

test("the pet window stays hidden until the native surface and first character frame are ready", () => {
  const mainWindow = tauriConfig.app.windows.find((window) => window.label === "main");
  assert.equal(mainWindow.decorations, false);
  assert.equal(mainWindow.transparent, true);
  assert.equal(mainWindow.shadow, false);
  assert.equal(mainWindow.visible, false);
  assert.doesNotMatch(nativeMain, /set_title\(""\)/);
  assert.match(nativeMain, /\.setup\(\|app\|[\s\S]*?prepare_initial_pet_window\(&window\)/);
  const nativeSurface = nativeMain.match(/fn apply_native_pet_surface[\s\S]*?\r?\n}\r?\n\r?\nfn prepare_initial_pet_window/)?.[0] || "";
  const prepareIndex = nativeSurface.indexOf(".prepare_window(window)");
  const boundsIndex = nativeSurface.indexOf(".apply_bounds(window");
  const regionsIndex = nativeSurface.indexOf("apply_native_interaction_region(");
  assert.ok(prepareIndex >= 0);
  assert.ok(prepareIndex < boundsIndex && boundsIndex < regionsIndex);
  assert.doesNotMatch(nativeSurface, /\.show\(\)/);
  assert.match(styles, /body\[data-shell-state="loading"\] \.pet-stage\s*\{[^}]*visibility:\s*hidden/);
  const reveal = nativeMain.match(/fn reveal_pet_window[\s\S]*?\r?\n}/)?.[0] || "";
  assert.match(reveal, /PET_LAYOUT_NOT_READY/);
  assert.match(reveal, /window[\s\S]*?\.show\(\)/);
  const presentationIndex = app.indexOf("await loadCurrentCharacterPresentation({ invoke })");
  const portraitIndex = app.lastIndexOf("await portraitController.show(characterPresentation.defaultPortraitKey");
  const readyIndex = app.lastIndexOf("document.body.dataset.shellState =");
  const revealIndex = app.lastIndexOf('await invoke("reveal_pet_window")');
  assert.ok(presentationIndex >= 0 && presentationIndex < portraitIndex);
  assert.ok(portraitIndex < readyIndex && readyIndex < revealIndex);
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

test("appearance publications can reach the pet through the least-privilege event capability", () => {
  assert.deepEqual(tauriCapability.windows, ["main", "settings"]);
  assert.deepEqual(tauriCapability.permissions, [
    "core:event:allow-listen",
    "core:event:allow-unlisten",
  ]);
  assert.match(app, /await listenAppEvent\("sakura:\/\/character-appearance-changed"/);
  assert.match(app, /appEventUnlisteners\.splice\(0\)/);
});

test("font previews never enter the portrait alpha-mask update path", () => {
  assert.match(app, /const changes = appearanceChanges\(activeAppearance, nextAppearance\)/);
  assert.match(app, /if \(changes\.fonts\) applyAppearanceVariables\(activeAppearance\)/);
});

test("portrait previews relax the stale native clip before scaling and rebuild it only after settling", () => {
  assert.match(app, /const PORTRAIT_HIT_SETTLE_MS = 90/);
  assert.match(app, /async function previewPortraitScale\(key\)[\s\S]*?await invoke\("begin_portrait_scale_preview"[\s\S]*?syncPortraitAppearance\(key\)[\s\S]*?schedulePortraitHitTest\(key, revision\)/);
  assert.match(app, /function schedulePortraitHitTest\(key, revision\)[\s\S]*?window\.setTimeout/);
  assert.match(app, /if \(changes\.portrait\) \{[\s\S]*?await previewPortraitScale\(key\)/);
  assert.match(app, /commit: \(\{ key, source \}\)[\s\S]*?activatePortraitHitTest\(key\)/);
  const nativePreview = nativeMain.match(/fn begin_portrait_scale_preview[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativePreview, /restore_full_hit_region/);
  assert.match(nativePreview, /portrait_hit_relaxed = true/);
  const nativePortraitUpdate = nativeMain.match(/fn activate_portrait_hit_test[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativePortraitUpdate, /let cache_matches =/);
  assert.match(nativePortraitUpdate, /if !cache_matches \{[\s\S]*?active_portrait_alpha_mask/);
  assert.match(nativePortraitUpdate, /portrait_hit_relaxed = false/);
});

test("the one-line composer keeps its text optically centered across configured font sizes", () => {
  const composerInput = styles.match(/\.composer textarea\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(composerInput, /padding:\s*8px 15px 5px/);
  assert.match(composerInput, /overflow-y:\s*hidden/);
  assert.match(composerInput, /line-height:\s*1\.25/);
});

test("product menu is native-owned and the settings window is a decorated singleton", () => {
  assert.match(app, /invoke\("show_pet_context_menu"/);
  assert.doesNotMatch(index, /context-menu|menu-popover/);
  assert.match(nativeMain, /ProductMenuAction::from_id/);
  assert.match(nativeMain, /show_or_focus_settings/);
  assert.match(nativeMain, /SETTINGS_WINDOW_LABEL/);
});

test("the native tray keeps a hidden pet recoverable through the shared visibility action", () => {
  assert.match(cargoManifest, /"image-png"/);
  assert.match(cargoManifest, /"tray-icon"/);
  assert.match(nativeProductShell, /include_bytes!\("\.\.\/icons\/icon\.png"\)/);
  assert.match(nativeProductShell, /TrayIconBuilder::with_id\(PRODUCT_TRAY_ID\)/);
  assert.match(nativeProductShell, /\.tooltip\("Sakura"\)/);
  assert.match(nativeProductShell, /\.show_menu_on_left_click\(false\)/);
  assert.match(nativeProductShell, /MENU_TOGGLE_PET[\s\S]*?MENU_OPEN_SETTINGS[\s\S]*?MENU_EXIT_APP/);

  const visibilityToggle = nativeMain.match(/fn toggle_pet_visibility[\s\S]*?\n}/)?.[0] || "";
  assert.match(visibilityToggle, /window\.hide\(\)/);
  assert.match(visibilityToggle, /window\.show\(\)/);
  assert.match(visibilityToggle, /sync_product_tray_visibility\(app, false\)/);
  assert.match(visibilityToggle, /sync_product_tray_visibility\(app, true\)/);
  assert.match(visibilityToggle, /window\.set_focus\(\)/);
  assert.equal(nativeMain.match(/toggle_pet_visibility\(app\)/g)?.length, 2);
  assert.match(nativeMain, /reveal_pet_window[\s\S]*?sync_product_tray_visibility\(window\.app_handle\(\), true\)/);

  const trayEvents = nativeMain.match(/\.on_tray_icon_event\([\s\S]*?\.on_window_event/)?.[0] || "";
  assert.match(trayEvents, /TrayIconEvent::Click/);
  assert.match(trayEvents, /MouseButton::Left/);
  assert.match(trayEvents, /MouseButtonState::Up/);
});

test("closing the pet always coordinates whole-app exit with the settings window", () => {
  const closeHandler = app.match(/#close-window[\s\S]*?beforeunload/)?.[0] || "";
  assert.match(closeHandler, /await invoke\("close_pet_window"\)/);
  assert.doesNotMatch(closeHandler, /dispose\(\)[\s\S]*?invoke\("close_pet_window"\)/);
  assert.match(app, /beforeunload", dispose/);

  const nativeWindowEvents = nativeMain.match(/\.on_window_event\([\s\S]*?\.invoke_handler/)?.[0] || "";
  assert.match(nativeWindowEvents, /window\.label\(\) == "main"/);
  assert.match(nativeWindowEvents, /CloseRequested[\s\S]*?api\.prevent_close\(\)[\s\S]*?request_app_exit/);
});

test("confirmed settings close destroys the window before ending its appearance session", () => {
  const resolveClose = nativeMain.match(/fn resolve_settings_exit[\s\S]*?\n}/)?.[0] || "";
  assert.match(resolveClose, /window\.destroy\(\)/);
  assert.doesNotMatch(resolveClose, /appearance\.close_session/);
  const nativeWindowEvents = nativeMain.match(/\.on_window_event\([\s\S]*?\.invoke_handler/)?.[0] || "";
  assert.match(nativeWindowEvents, /WindowEvent::Destroyed[\s\S]*?appearance\.close_session\(\)/);
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
