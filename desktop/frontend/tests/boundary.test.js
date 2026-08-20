import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const index = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const layoutSource = readFileSync(new URL("../pet/layout.js", import.meta.url), "utf8");
const adaptiveSurface = readFileSync(new URL("../pet/adaptive-control-surface.js", import.meta.url), "utf8");
const layoutContract = JSON.parse(readFileSync(new URL("../pet/layout-contract.json", import.meta.url), "utf8"));
const contextMenu = readFileSync(new URL("../pet/context_menu.js", import.meta.url), "utf8");
const nativeDrag = readFileSync(new URL("../pet/native-drag.js", import.meta.url), "utf8");
const fakeCore = readFileSync(new URL("../chat/fake-chat-core.js", import.meta.url), "utf8");
const realChat = readFileSync(new URL("../chat/real-chat-client.js", import.meta.url), "utf8");
const multilingualText = readFileSync(new URL("../pet/multilingual-text.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const settingsStyles = readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");
const settingsAppearance = readFileSync(new URL("../settings/appearance-runtime.js", import.meta.url), "utf8");
const petAppearance = readFileSync(new URL("../pet/appearance.js", import.meta.url), "utf8");
const settingsIndex = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
const settingsScript = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
const settingsTools = readFileSync(new URL("../settings/tools-runtime.js", import.meta.url), "utf8");
const nativeInteraction = readFileSync(new URL("../../src-tauri/src/window_interaction.rs", import.meta.url), "utf8");
const nativeMain = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const nativeProductShell = readFileSync(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");
const nativeWindowBackend = readFileSync(new URL("../../src-tauri/src/platform/window_backend.rs", import.meta.url), "utf8");
const cargoManifest = readFileSync(new URL("../../src-tauri/Cargo.toml", import.meta.url), "utf8");
const windowsClickthroughAcceptance = readFileSync(
  new URL("../../tests/windows_transparent_clickthrough_acceptance.ps1", import.meta.url),
  "utf8",
);
const tauriConfig = JSON.parse(readFileSync(new URL("../../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const tauriCapability = JSON.parse(
  readFileSync(new URL("../../src-tauri/capabilities/default.json", import.meta.url), "utf8"),
);
const legacySettingsConfig = JSON.parse(
  readFileSync(new URL("../../../tools/settings-tauri/src-tauri/tauri.conf.json", import.meta.url), "utf8"),
);

test("Memory is a permanent plugin-provided CRUD surface while model slots stay unified", () => {
  assert.match(settingsIndex, /id="page-memory"[\s\S]*?id="memorySurface"/);
  assert.match(settingsIndex, /id="modelSlots"/);
  assert.match(settingsIndex, /id="memoryModelSettings" hidden/);
  assert.match(settingsScript, /filter\(\(section\) => section\.surface === "memory"\)/);
  assert.match(settingsScript, /记忆管理暂不可用/);
  assert.match(settingsScript, /前往插件页/);
  assert.match(settingsScript, /addEventListener\("dblclick", \(\) => openMemoryCollectionEditor/);
  assert.match(settingsScript, /memory-card-edit/);
  assert.match(settingsScript, /search\.className = "memory-search-input"/);
  assert.doesNotMatch(settingsScript, /searchLabel\.textContent = "⌕"/);
  assert.match(settingsScript, /queryRevision !== state\.queryRevision[\s\S]*?state\.queryPending = true/);
  assert.match(settingsScript, /restoreFocus[\s\S]*?setSelectionRange/);
  assert.match(settingsScript, /refreshMemorySurfaceCurrent[\s\S]*?runtimePluginController\.refreshCurrent/);
  assert.doesNotMatch(settingsScript.match(/async function refreshMemorySurfaceCurrent\(\)[\s\S]*?\n\}/)?.[0] || "", /renderMemorySurface\(\)/);
  assert.match(settingsScript, /无需关闭设置，初始化完成后这里会自动更新/);
  assert.match(settingsStyles, /\.memory-archive[\s\S]*?var\(--sakura-/);
  assert.match(settingsStyles, /\.memory-record-dialog/);
  assert.match(settingsScript, /mountMemoryEditorPortal\(renderMemoryEditor/);
  assert.match(settingsScript, /setAttribute\("inert", ""\)[\s\S]*?document\.body\.append\(overlay\)/);
  assert.match(settingsScript, /removeAttribute\("inert"\)/);
  assert.doesNotMatch(
    settingsScript.match(/function renderMemoryCollection[\s\S]*?return archive;/)?.[0] || "",
    /archive\.append\(renderMemoryEditor/,
  );
  assert.match(
    settingsStyles.match(/\.memory-dialog-field input,[\s\S]*?\n\}/)?.[0] || "",
    /max-width:\s*none/,
  );
  assert.match(settingsStyles, /\.memory-dialog-field > \.custom-select[\s\S]*?max-width:\s*none/);
  assert.doesNotMatch(settingsStyles.match(/\/\* ---------- 记忆档案 ---------- \*\/[\s\S]*?button:focus-visible/)?.[0] || "", /linear-gradient|memory-record-rail|backdrop-filter/);
  assert.doesNotMatch(settingsStyles.match(/\.memory-record-content\s*\{[\s\S]*?\n\}/)?.[0] || "", /line-clamp|overflow:\s*hidden/);
  assert.match(
    settingsStyles.match(/\.memory-archive-list\s*\{[\s\S]*?\n\}/)?.[0] || "",
    /grid-auto-rows:\s*max-content/,
  );
  const memoryRecordCardStyles = settingsStyles.match(/\.memory-record-card\s*\{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(memoryRecordCardStyles, /border:\s*1px solid var\(--sakura-border\)/);
  assert.match(memoryRecordCardStyles, /overflow:\s*hidden/);
  const memoryResultCountStyles = settingsStyles.match(/\.memory-result-count\s*\{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(memoryResultCountStyles, /display:\s*inline-flex/);
  assert.match(memoryResultCountStyles, /align-items:\s*center/);
  assert.match(memoryResultCountStyles, /min-height:\s*40px/);
  assert.match(settingsStyles, /\.memory-record-aside\s*\{[^}]*grid-template-rows:\s*auto 1fr[^}]*align-self:\s*stretch/s);
  assert.match(settingsStyles, /\.memory-card-edit\s*\{[^}]*align-self:\s*end/s);
  assert.match(settingsStyles, /\.custom-select__menu\s*\{[^}]*z-index:\s*2200/s);
  assert.match(settingsStyles, /\.memory-editor-overlay\s*\{[^}]*z-index:\s*1900/s);
  assert.match(settingsScript, /section\.surface === "memory" \? "前往记忆页管理"/);
  assert.match(settingsScript, /slot\.owner_id === plugin\.plugin_id[\s\S]*?前往模型页设置/);
});

test("plugin settings submit only editable declared fields", () => {
  assert.match(settingsScript, /function editablePluginSectionValues\(section, values\)/);
  assert.match(settingsScript, /!field\.readonly && field\.type !== "readonly"/);
  assert.match(settingsScript, /runPluginSettingsAction[\s\S]*editablePluginSectionValues/);
  assert.match(settingsScript, /collectPluginSettings[\s\S]*editablePluginSectionValues/);
});

function declarationBlock(selector, requiredDeclaration = null) {
  const blocks = [...styles.matchAll(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`, "g"))].map((match) => match[1]);
  return requiredDeclaration ? blocks.find((block) => block.includes(requiredDeclaration)) || "" : blocks.at(-1) || "";
}

test("markup exposes fixed product chat, portrait, status, and accessible controls", () => {
  for (const id of ["chat-bubble", "bubble-copy", "reply-history-previous", "reply-history-next", "composer-input", "composer-send", "portrait-current"])
    assert.match(index, new RegExp(`id="${id}"`), id);
  assert.doesNotMatch(index, /id="typewriter-skip"|id="close-window"|立即显示/);
  assert.doesNotMatch(app, /typewriterSkip|typewriter\.skip\(/);
  assert.doesNotMatch(app, /querySelector\("#close-window"\)/);
  assert.match(index, /aria-live="polite"/);
  assert.match(index, /maxlength="4096"/);
  assert.doesNotMatch(index, /id="bubble-copy"[^>]*data-interactive/);
  assert.match(index, /id="acceptance-entry"[^>]*hidden[^>]*aria-hidden="true"/);
  for (const forbidden of ["state-rail", "FAKE CORE", "geometry-readout", "theme-button", "composer-toggle", "visibility-probe"])
    assert.equal(index.includes(forbidden), false, forbidden);
});

test("rounded WebView surfaces preserve the native clip contract without external effects", () => {
  assert.doesNotMatch(styles, /filter\s*:\s*drop-shadow/i);
  assert.doesNotMatch(styles, /\.portrait-frame::after/);
  for (const [selector, radius] of [["bubble", 22], ["composer", 28]]) {
    const block = declarationBlock(selector, "border-radius");
    assert.match(block, new RegExp(`border-radius:\\s*${radius}px`), selector);
    assert.match(block, /box-shadow:\s*none/, `${selector} must not paint outside its native hit region`);
  }
  const thinkingBubble = styles.match(/body\[data-chat-state="thinking"\] \.bubble\s*\{([^}]*)\}/)?.[1] || "";
  const focusedComposer = styles.match(/\.composer:focus-within\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(thinkingBubble, /box-shadow\s*:/);
  assert.doesNotMatch(focusedComposer, /box-shadow\s*:/);
  for (const selector of ["pet-stage", "portrait", "portrait-frame", "portrait-image"]) {
    const block = declarationBlock(selector, "background");
    assert.match(block, /background:\s*transparent/, selector);
  }
  assert.match(nativeInteraction, /const BUBBLE_CORNER_RADIUS: u32 = 22;/);
  assert.match(nativeInteraction, /const INPUT_CORNER_RADIUS: u32 = 28;/);
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
  assert.match(nativeMain, /\.setup\((?:move\s+)?\|app\|[\s\S]*?prepare_initial_pet_window\(&window\)/);
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
  const nativeRevisionIndex = app.indexOf('await invoke("current_pet_layout_revision")');
  const layoutIndex = app.indexOf("await layoutController.transition(PRODUCT_LAYOUT_STATE");
  const presentationIndex = app.indexOf("await loadCurrentCharacterPresentation({ invoke })");
  const portraitIndex = app.lastIndexOf("await portraitController.show(characterPresentation.defaultPortraitKey");
  const readyIndex = app.lastIndexOf("document.body.dataset.shellState =");
  const revealIndex = app.lastIndexOf('await invoke("reveal_pet_window")');
  const greetingIndex = app.lastIndexOf("presentation.beginGreeting()");
  assert.ok(nativeRevisionIndex >= 0 && nativeRevisionIndex < layoutIndex);
  assert.ok(presentationIndex >= 0 && presentationIndex < portraitIndex);
  assert.ok(portraitIndex < readyIndex && readyIndex < revealIndex);
  assert.ok(revealIndex < greetingIndex);
  assert.match(app, /if \(presentationUnavailable\)[\s\S]*?portraitFallback\.hidden = false;[\s\S]*?else \{[\s\S]*?await portraitController\.show/);
  assert.match(app, /initialPreparedGenerationId: characterPresentation\.generationId,[\s\S]*?prepareGeneration: \(\{ generationId \}\) => rebindCoreGeneration\(generationId\)/);
  assert.match(app, /generationId === characterPresentation\.generationId\) return true;/);
  assert.doesNotMatch(app, /window\.location\.reload\(\)/);
  assert.match(app, /Keep the decoded old frame on screen until the replacement resource is ready/);
  assert.match(nativeWindowBackend, /WS_CAPTION/);
  assert.match(nativeWindowBackend, /SWP_FRAMECHANGED/);
  assert.match(nativeWindowBackend, /if style_changed \{[\s\S]*?SWP_FRAMECHANGED/);
  assert.match(nativeWindowBackend, /GetWindowLongW/);
  assert.match(nativeWindowBackend, /native frame bits survived style refresh/);
});

test("TTS playback-start is the shared boundary for portrait transition and subtitle typing", () => {
  const segmentStart = app.indexOf("onSegment: (segment, index) =>");
  const segmentEnd = app.indexOf("onSegmentComplete:", segmentStart);
  assert.ok(segmentStart >= 0 && segmentEnd > segmentStart);
  const onSegment = app.slice(segmentStart, segmentEnd);
  assert.match(onSegment, /const portraitReady = portraitController\.preload\(\s*result\.state\.portrait/);
  assert.match(onSegment, /const subtitleReady = portraitReady\.then\(\(\) => ttsController\.beforeSegment/);
  assert.match(onSegment, /portraitReady\.then\(\(\) => ttsController\.beforeSegment\(segment, index, \{[\s\S]*?onStarted:[\s\S]*?void render\(result\.state\)/);
  assert.match(onSegment, /waitingIndicator\.stopWhenSettled\(subtitleReady\)/);
  assert.match(onSegment, /: subtitleReady;/);
  assert.doesNotMatch(onSegment, /Promise\.all\(/);
  assert.doesNotMatch(onSegment, /void render\(result\.state\);\s*const subtitleReady/);
});

test("empty portrait layers stay hidden instead of painting WebView2 broken-image frames", () => {
  assert.match(styles, /\.portrait-image:not\(\[src\]\)\s*\{[^}]*visibility:\s*hidden/);
  const imageBlock = declarationBlock("portrait-image");
  assert.match(imageBlock, /display:\s*block/);
  assert.match(imageBlock, /border:\s*0/);
});

test("portrait layers cannot be selected or dragged while text selection stays themed", () => {
  for (const id of ["portrait-current", "portrait-next"])
    assert.match(index, new RegExp(`id="${id}"[^>]*draggable="false"`), id);

  const bodyBlock = styles.match(/html,\s*body\s*\{([^}]*)\}/)?.[1] || "";
  const imageBlock = declarationBlock("portrait-image");
  const bubbleCopyBlock = declarationBlock("bubble-copy");
  const composerInputBlock = styles.match(/\.composer textarea\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(bodyBlock, /-webkit-user-select:\s*none/);
  assert.match(imageBlock, /-webkit-user-drag:\s*none/);
  assert.match(imageBlock, /-webkit-user-select:\s*none/);
  assert.match(imageBlock, /user-select:\s*none/);
  assert.match(bubbleCopyBlock, /-webkit-user-select:\s*text/);
  assert.match(composerInputBlock, /-webkit-user-select:\s*text/);
  assert.match(app, /for \(const eventName of \["dragstart", "selectstart"\]\)[\s\S]*?portrait\.addEventListener\(eventName,[\s\S]*?event\.preventDefault\(\)[\s\S]*?true/);
  assert.match(styles, /\.bubble-copy::selection,\s*\.bubble-copy \*::selection,\s*\.composer textarea::selection\s*\{[^}]*color:\s*var\(--text\)[^}]*background:\s*color-mix\(in srgb, var\(--primary\), transparent 72%\)/s);
});

test("bubble typography uses language-owned families and only real product weights", () => {
  const bubbleCopyBlock = styles.match(/\.bubble-copy\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(bubbleCopyBlock, /Yu Mincho|SimSun|(^|[^-])\bserif\b/);
  assert.match(bubbleCopyBlock, /font-weight:\s*var\(--font-weight-regular\)/);
  assert.match(styles, /\.bubble-copy \[lang\|="zh"\]/);
  assert.match(styles, /\.bubble-copy \[lang\|="ja"\]/);
  assert.match(styles, /\.bubble-copy \[lang\|="ko"\]/);
  assert.match(styles, /\.bubble-copy \[lang="en"\]/);
  assert.match(styles, /--font-zh:\s*"Sakura Noto Sans SC",\s*var\(--font-zh-system\)/);
  assert.match(styles, /--font-ja:\s*"Sakura Noto Sans JP",\s*var\(--font-ja-system\)/);
  assert.doesNotMatch(styles, /font-weight:\s*(?:650|800|900)\b/);
  assert.match(app, /renderMultilingualText/);
  assert.match(app, /input\.lang = inferTextLanguage\(input\.value\)/);
  assert.match(index, /id="composer-input"[\s\S]*?lang="zh-CN"/);
});

test("the character name size controls the reply text vertical offset", () => {
  const headerBlock = declarationBlock("bubble-header");
  const nameBlock = styles.match(/\.identity strong\s*\{([^}]*)\}/)?.[1] || "";
  const replyBodyBlock = declarationBlock("reply-body");
  assert.doesNotMatch(headerBlock, /min-height:\s*20px/);
  assert.match(nameBlock, /font-size:\s*var\(--name-font-size\)/);
  assert.match(nameBlock, /line-height:\s*1\.5/);
  assert.match(replyBodyBlock, /margin-top:\s*calc\(var\(--name-font-size\) \* \.6\)/);
  assert.doesNotMatch(replyBodyBlock, /margin-top:\s*8px/);
});

test("portrait cross-fade uses the legacy overlap without a second CSS transition", () => {
  assert.doesNotMatch(styles, /\.portrait-image--current\s*\{[^}]*transition:/s);
  assert.doesNotMatch(styles, /\.portrait-image--next\s*\{[^}]*transition:/s);
  assert.match(styles, /@keyframes portrait-current-fade-out[\s\S]*?opacity:\s*1[\s\S]*?opacity:\s*0/);
  assert.match(styles, /@keyframes portrait-next-fade-in[\s\S]*?opacity:\s*0[\s\S]*?opacity:\s*1/);
  assert.match(styles, /portrait-next-fade-in\s+250ms[^;]*50ms/);
});

test("Chinese subtitle menu item is an enabled checked action", () => {
  const actionIndex = index.indexOf('data-menu-action="sakura.chat.subtitle.toggle"');
  const subtitleItem = actionIndex < 0
    ? ""
    : index.slice(index.lastIndexOf("<button", actionIndex), index.indexOf("</button>", actionIndex) + 9);
  assert.match(subtitleItem, /data-menu-action="sakura\.chat\.subtitle\.toggle"/);
  assert.match(subtitleItem, /role="menuitemcheckbox"/);
  assert.match(subtitleItem, /aria-disabled="false"/);
  assert.doesNotMatch(subtitleItem, /data-menu-unavailable|\sdisabled(?:\s|>)/);
});

test("runtime typography assigns weight by semantic role", () => {
  const petBlock = (selector) => styles.match(new RegExp(`^${selector}\\s*\\{([^}]*)\\}`, "m"))?.[1] || "";
  const settingsBlock = (selector) => settingsStyles.match(new RegExp(`^${selector}\\s*\\{([^}]*)\\}`, "m"))?.[1] || "";

  assert.match(petBlock("\\.identity strong"), /font-weight:\s*var\(--font-weight-bold\)/);
  assert.match(petBlock("\\.reply-history-button"), /font-weight:\s*var\(--font-weight-medium\)/);
  assert.match(petBlock("\\.composer textarea"), /font-weight:\s*var\(--font-weight-regular\)/);
  assert.match(petBlock("\\.composer #composer-send"), /place-items:\s*center/);
  assert.match(petBlock("#composer-attachment"), /place-items:\s*center/);
  assert.match(petBlock("\\.pet-context-menu button"), /font-weight:\s*var\(--font-weight-regular\)/);

  assert.match(settingsBlock("\\.nav-item"), /font-weight:\s*var\(--font-weight-regular\)/);
  assert.match(settingsBlock("\\.nav-item\\.is-active"), /font-weight:\s*var\(--font-weight-semibold\)/);
  assert.match(settingsBlock("button"), /font-weight:\s*var\(--font-weight-medium\)/);
  assert.match(settingsBlock("\\.page-title"), /font-weight:\s*var\(--font-weight-bold\)/);
  assert.match(settingsBlock("\\.setting-title"), /font-weight:\s*var\(--font-weight-medium\)/);
  assert.doesNotMatch(settingsStyles, /font-weight:\s*(?:650|800|900)\b/);
});

test("screenshot attachment menu is keyboard reachable and exposes only the current action", () => {
  const attachmentIndex = index.indexOf('id="composer-attachment"');
  const toggle = attachmentIndex < 0 ? "" : index.slice(
    index.lastIndexOf("<button", attachmentIndex),
    index.indexOf("</button>", attachmentIndex) + 9,
  );
  const menu = index.match(/<div id="composer-attachment-menu"[\s\S]*?<\/div>/)?.[0] || "";
  assert.match(toggle, /aria-haspopup="menu"/);
  assert.doesNotMatch(toggle, /tabindex="-1"/);
  assert.match(menu, /id="capture-screen"/);
  assert.equal((menu.match(/role="menuitem"/g) || []).length, 1);
  const placeholderStyle = styles.match(/\.composer textarea::placeholder\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(placeholderStyle, /var\(--text\)/);
  assert.doesNotMatch(placeholderStyle, /transparent/);
});

test("reply selection keeps copy support and uses the active character theme", () => {
  const bubbleCopyBlock = styles.match(/\.bubble-copy\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(bubbleCopyBlock, /user-select:\s*text/);
  assert.match(styles, /\.bubble-copy::selection,\s*\.bubble-copy \*::selection,\s*\.composer textarea::selection\s*\{[^}]*color:\s*var\(--text\)[^}]*background:\s*color-mix\(in srgb, var\(--primary\), transparent 72%\)/s);
});

test("bubble whitespace is draggable while rendered text and the scrollbar remain interactive", () => {
  assert.match(multilingualText, /span\.dataset\.selectableText\s*=\s*"true"/);
  assert.match(app, /POINTER_INTERACTIVE_SELECTOR\s*=\s*"\[data-interactive\], \[data-selectable-text\]"/);
  assert.match(app, /scrollHeight\s*<=\s*viewport\.clientHeight/);
  assert.match(index, /id="chat-bubble"[^>]*data-drag-region="true"/);
  assert.match(styles, /\.bubble\s*\{[^}]*cursor:\s*grab/s);
  assert.match(styles, /\.bubble:active,\s*\.bubble\.is-native-dragging\s*\{\s*cursor:\s*grabbing/);
  assert.match(styles, /\.bubble \[data-selectable-text\]\s*\{\s*cursor:\s*text/);
  assert.match(styles, /\.reply-history-nav\s*\{[^}]*cursor:\s*default/);
  assert.match(app, /shouldStartNativeDrag[\s\S]*?clearTextSelection\(window\.getSelection\?\.\(\)\)[\s\S]*?tracedInteractionInvoke\([\s\S]*?"start_pet_drag",/);
  assert.match(app, /startNativePetDragWithRevisionRecovery/);
  assert.match(app, /readSurfaceDiagnostics:[\s\S]*?current_pet_surface_diagnostics/);
  assert.match(app, /isNativePetDragPointRejected\(error\)/);
  assert.match(nativeDrag, /PET_DRAG_REVISION_STALE/);
  assert.match(nativeDrag, /PET_DRAG_POINT_REJECTED/);
});

test("WP-3-04 product chat uses only the narrow Tauri bridge while Fake Core remains isolated", () => {
  for (const forbidden of ["chat_send", "chat_cancel", "fetch(", "localStorage", "sessionStorage", "characters/", "data/"])
    assert.equal(fakeCore.includes(forbidden), false, forbidden);
  assert.equal(app.includes('invoke("chat_'), false);
  assert.match(app, /createRealChatClient/);
  assert.match(realChat, /const payload = attachmentId \? \{ message, attachmentId \} : \{ message \}/);
  assert.match(realChat, /invoke\("chat_send", \{ payload \}\)/);
  assert.match(realChat, /invoke\("chat_cancel"/);
  assert.match(realChat, /invoke\("runtime_lifecycle_snapshot"\)/);
  for (const forbidden of ["fetch(", "localStorage", "sessionStorage", "characters/", "data/", "apiKey", "credential"])
    assert.equal(realChat.includes(forbidden), false, forbidden);
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

test("plugin settings reserve a track for every header row before the scrollable workbench", () => {
  assert.match(
    settingsStyles,
    /#page-plugins\s*\{[^}]*grid-template-rows:\s*auto auto auto minmax\(0, 1fr\)/s,
  );
});

test("appearance publications can reach the pet through the least-privilege event capability", () => {
  assert.deepEqual(tauriCapability.windows, ["main", "settings", "capture-*"]);
  assert.deepEqual(tauriCapability.permissions, [
    "core:event:allow-listen",
    "core:event:allow-unlisten",
  ]);
  assert.match(app, /await listenAppEvent\("sakura:\/\/character-appearance-changed"/);
  assert.match(app, /appEventUnlisteners\.splice\(0\)/);
});

test("input glass is scoped to the composer and the appearance publication is v3", () => {
  assert.match(styles, /:root\[data-input-visual-effect="gaussian_blur"\] \.composer\s*,/);
  assert.match(styles, /:root\[data-input-visual-effect="liquid_glass"\] \.composer\s*\{/);
  assert.match(settingsAppearance, /"liquid_glass"/);
  assert.match(petAppearance, /"liquid_glass"/);
  assert.doesNotMatch(styles, /data-windows-glass-poc|data-input-visual-effect[^\n]*\.bubble/);
  assert.match(settingsAppearance, /publication\.schemaVersion !== 3/);
  assert.match(petAppearance, /publication\?\.schemaVersion !== 3/);
  assert.match(settingsIndex, /id="visualEffectMode" data-settings-feature="appearance\.input_visual_effect"/);
  assert.match(styles, /data-input-visual-effect="liquid_glass"\] \.composer\s*\{\s*background: transparent;/);
  assert.match(styles, /data-input-visual-effect="liquid_glass"\] \.composer\s*\{[\s\S]*?backdrop-filter: blur\(2px\) saturate\(1\.12\) contrast\(1\.04\);/);
  assert.match(styles, /data-input-visual-effect="liquid_glass"\] \.composer:focus-within\s*\{\s*border-color: transparent;/);
  assert.match(app, /document\.addEventListener\("pointerdown",[\s\S]*?inputFocus\.dismissFocus\(\);[\s\S]*?input\.blur\(\);[\s\S]*?\}, true\);/);
  assert.match(contextMenu, /beforeSurfaceResize\(\);[\s\S]*?invoke\("set_pet_context_menu_surface"/);
  assert.match(app, /beforeSurfaceResize:[\s\S]*?inputFocus\.dismissFocus\(\);[\s\S]*?input\.blur\(\);/);
});

test("font previews never enter the portrait alpha-mask update path", () => {
  assert.match(app, /const changes = appearanceChanges\(activeAppearance, nextAppearance\)/);
  assert.match(app, /if \(changes\.fonts\) applyAppearanceVariables\(activeAppearance\)/);
});

test("portrait slider ticks keep every supported native envelope stable while precise routing catches up", () => {
  assert.doesNotMatch(app, /PORTRAIT_HIT_SETTLE_MS|schedulePortraitHitTest/);
  assert.match(app, /async function previewPortraitScale\(key\)[\s\S]*?await invoke\("begin_portrait_scale_preview"[\s\S]*?await activatePortraitHitTest\(key, revision\)[\s\S]*?syncPortraitAppearance\(key\)/);
  assert.match(app, /preview: async \(\{ key, source \}\)[\s\S]*?invoke\("prepare_portrait_transition"[\s\S]*?portraitNext\.src = source/);
  assert.match(app, /if \(changes\.portrait\) \{[\s\S]*?if \(portraitScaleGestureActive\) \{[\s\S]*?syncPortraitAppearance\([\s\S]*?frameTrace,[\s\S]*?else \{[\s\S]*?await previewPortraitScale\(key\)/);
  assert.match(settingsAppearance, /settings_character_appearance_scale_frame/);
  assert.match(settingsAppearance, /schedulePortraitScaleFrame\(draft\.portraitScalePercent, context\)/);
  assert.match(app, /listenAppEvent\("sakura:\/\/portrait-scale-frame"[\s\S]*?portrait\.frame-event-received[\s\S]*?syncPortraitAppearance\(key, characterPresentation, portraitScalePercent, frameTrace\)/);
  assert.match(app, /function activatePortraitHitTest\([\s\S]*?portraitScalePercent = activeAppearance\.portraitScalePercent[\s\S]*?portraitScalePercent,/);
  const frameListenerStart = app.indexOf('listenAppEvent("sakura://portrait-scale-frame"');
  const gestureListenerStart = app.indexOf('listenAppEvent("sakura://portrait-scale-gesture"');
  const frameListener = app.slice(frameListenerStart, gestureListenerStart);
  assert.match(frameListener, /if \(preview\.deferredNative\) \{[\s\S]*?syncPortraitAppearance\(key, characterPresentation, portraitScalePercent, frameTrace\)[\s\S]*?if \(!preview\.deferredHitRegions\)[\s\S]*?enqueuePortraitScaleHitFrame[\s\S]*?return;/);
  assert.doesNotMatch(frameListener, /Linux has no stable backing envelope/);
  assert.doesNotMatch(frameListener, /nativeFrameTrace/);
  assert.doesNotMatch(frameListener, /await activatePortraitHitTest[\s\S]*?syncPortraitAppearance/);
  assert.match(nativeMain, /fn settings_character_appearance_scale_frame[\s\S]*?PORTRAIT_SCALE_OUT_OF_RANGE[\s\S]*?"sakura:\/\/portrait-scale-frame"/);
  assert.match(app, /commit: async \(\{ key, source \}\)[\s\S]*?const revision = \+\+portraitHitRevision[\s\S]*?await activatePortraitHitTest\(key, revision\)/);
  assert.match(app, /portraitTransitionPending[\s\S]*?waitForPortraitPaint\(\)[\s\S]*?invoke\("commit_portrait_transition", \{ revision \}\)/);
  assert.match(app, /function waitForPortraitPaint\(\)[\s\S]*?requestAnimationFrame\([\s\S]*?requestAnimationFrame\(resolve\)/);
  const nativePreview = nativeMain.match(/fn begin_portrait_scale_preview[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativePreview, /cfg!\(windows\) && !geometry\.portrait_scale_preview_active[\s\S]*?compute_pet_window_layout\([\s\S]*?true,[\s\S]*?\.relax_hit_regions\(&window\)[\s\S]*?apply_native_pet_surface_bounds_transaction/);
  assert.match(nativePreview, /cfg!\(target_os = "macos"\) && !geometry\.portrait_scale_preview_active[\s\S]*?compute_pet_window_layout\([\s\S]*?true,[\s\S]*?apply_native_pet_surface_transaction/);
  assert.doesNotMatch(nativePreview.match(/if cfg!\(target_os = "macos"\)[\s\S]*?\n        \}/)?.[0] || "", /relax_hit_regions/);
  assert.match(nativePreview, /cfg!\(target_os = "linux"\)[\s\S]*?geometry\.portrait_scale_gesture_active[\s\S]*?!geometry\.portrait_scale_preview_active[\s\S]*?compute_pet_window_layout\([\s\S]*?true,[\s\S]*?apply_native_pet_surface_transaction/);
  assert.match(nativePreview, /Ok\(Some\(PortraitScalePreview \{[\s\S]*?application: preview_application,[\s\S]*?deferred_native: defers_native_portrait_scale_frames\(\),[\s\S]*?deferred_hit_regions: defers_portrait_scale_hit_region_frames\(\)/);
  const nativePortraitUpdate = nativeMain.match(/fn activate_portrait_hit_test[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativePortraitUpdate, /let cache_matches =/);
  assert.match(nativePortraitUpdate, /if !cache_matches \{[\s\S]*?active_portrait_alpha_mask/);
  assert.match(nativePortraitUpdate, /compute_pet_window_layout\([\s\S]*?portrait_scale_percent/);
  assert.match(nativePortraitUpdate, /let stabilize_portrait_scale = geometry\.stabilizes_portrait_scale_bounds\(\)/);
  assert.match(nativePortraitUpdate, /let defer_precise_hit_regions = geometry\.defers_precise_portrait_scale_hit_regions\(\)/);
  assert.match(nativePortraitUpdate, /if defer_precise_hit_regions \{[\s\S]*?build_native_interaction_regions\([\s\S]*?apply_native_pet_surface_bounds_transaction\(/);
  assert.match(nativePortraitUpdate, /let defer_portrait_transition_native =\s*cfg!\(target_os = "macos"\) && geometry\.portrait_transition_active/);
  assert.match(nativePortraitUpdate, /else if defer_portrait_transition_native \{[\s\S]*?build_native_interaction_regions\(/);
  assert.match(nativePortraitUpdate, /portrait_transition_pending = Some\(PendingPortraitTransition/);
  assert.match(nativePortraitUpdate, /else \{[\s\S]*?apply_native_pet_surface_transaction\(/);
  assert.match(nativePortraitUpdate, /return Ok\(None\)/);
  assert.match(nativePortraitUpdate, /portrait_anchor = Some\(application\.portrait_anchor\)/);
  assert.match(nativePortraitUpdate, /portrait_hit_relaxed = defer_precise_hit_regions/);
  assert.match(nativeMain, /fn defers_precise_portrait_scale_hit_regions[\s\S]*?portrait_scale_preview_active[\s\S]*?portrait_scale_gesture_active[\s\S]*?portrait_hit_relaxed/);
  assert.match(nativeMain, /fn defers_native_portrait_scale_frames\(\)[\s\S]*?stable_bounds_during_gesture/);
  assert.match(nativeMain, /fn defers_portrait_scale_hit_region_frames\(\)[\s\S]*?precise_hit_regions_during_gesture/);
  const nativeTransition = nativeMain.match(/fn prepare_portrait_transition[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativeTransition, /union_surface_bounds/);
  assert.match(nativeTransition, /old_bounds[\s\S]*?logical_scale_stable_surface_bounds_with_control_surface[\s\S]*?new_bounds[\s\S]*?logical_scale_stable_surface_bounds_with_control_surface/);
  assert.doesNotMatch(nativeTransition, /let application = if cfg!\(target_os = "macos"\)/);
  assert.match(nativeTransition, /extra_native_rectangles/);
  assert.match(nativeTransition, /let geometry_unchanged = previous_application[\s\S]*?same_surface_geometry/);
  assert.match(nativeTransition, /if cfg!\(target_os = "macos"\) && geometry_unchanged \{\s*apply_precise_hit_regions/);
  assert.match(nativeTransition, /if !geometry_unchanged \{[\s\S]*?glass\.update_control_surface/);
  assert.match(nativeMain, /fn commit_portrait_transition[\s\S]*?apply_precise_hit_regions\(&window, &pending\.hit_regions\)/);
  assert.match(nativeMain, /activate_portrait_hit_test,[\s\S]*?commit_portrait_transition,[\s\S]*?settle_portrait_scale_surface/);
});

test("portrait scaling opens one stable envelope and restores one exact region on release", () => {
  const applyLayout = layoutSource.match(/export function applyPetLayout[\s\S]*?\n}/)?.[0] || "";
  assert.match(applyLayout, /activeBounds/);
  assert.match(applyLayout, /style\.left/);
  assert.match(applyLayout, /dataset\.surfaceX/);
  assert.match(app, /function currentSurfaceOffset/);
  const nativeTransaction = nativeMain.match(/fn apply_native_pet_surface_transaction[\s\S]*?\n}/)?.[0] || "";
  assert.match(nativeMain, /logical_scale_stable_surface_bounds_with_control_surface/);
  assert.match(nativeMain, /fn uses_resident_stable_surface_bounds[\s\S]*?resident_stable_bounds[\s\S]*?portrait_alpha_mask_available \|\| control_surface_available/);
  assert.match(nativeMain, /platform::PlatformTarget::MacOsArm64[\s\S]*?resident_stable_bounds: false/);
  assert.doesNotMatch(nativeMain, /resident_portrait_alpha_mask = if cfg!\(target_os = "macos"\)/);
  assert.doesNotMatch(nativeMain, /The macOS root frame is resident/);
  assert.match(nativeTransaction, /let geometry_unchanged =/);
  assert.match(nativeTransaction, /if !geometry_unchanged \{[\s\S]*?precommit_webview_surface\(window, application\)[\s\S]*?\.apply_bounds\(window/);
  assert.ok(nativeTransaction.indexOf("precommit_webview_surface(window, application)") >= 0);
  assert.ok(nativeTransaction.indexOf("precommit_webview_surface(window, application)") < nativeTransaction.indexOf(".apply_bounds(window"));
  assert.match(nativeWindowBackend, /fn apply_bounds/);
  assert.match(nativeInteraction, /pub envelope: \[u32; 2\]/);
  const macSnapshot = nativeInteraction.match(/struct MacHitRouterSnapshot \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(macSnapshot, /rectangles: Vec<PhysicalHitRect>/);
  assert.doesNotMatch(macSnapshot, /relaxed/);
  assert.match(nativeInteraction, /fn mac_hit_router_contains\([\s\S]*?rectangles\.iter\(\)[\s\S]*?rect\.contains/);
  assert.doesNotMatch(nativeInteraction, /snapshot\.relaxed/);
  assert.match(nativeWindowBackend, /macOS requires precise cursor routing during scale preview/);
  assert.match(nativeWindowBackend, /fn macos_atomic_frame[\s\S]*?setFrame_display\(frame, false\)/);
  const macBounds = nativeWindowBackend.match(/#\[cfg\(target_os = "macos"\)\]\s*\{\s*macos_atomic_frame\(window, placement\)[\s\S]*?\n        \}/)?.[0] || "";
  assert.match(macBounds, /macos_atomic_frame/);
  assert.doesNotMatch(macBounds, /set_size|set_position/);
  const linuxBounds = nativeWindowBackend.match(/#\[cfg\(target_os = "linux"\)\]\s*\{[\s\S]*?\n        \}/)?.[0] || "";
  assert.match(linuxBounds, /linux_bounds_request/);
  assert.match(nativeWindowBackend, /LinuxBoundsRequest::X11MoveResize[\s\S]*?gtk_window\.window\(\)\.is_none\(\)[\s\S]*?gtk_window\.realize\(\)[\s\S]*?move_resize/);
  assert.match(nativeWindowBackend, /LinuxBoundsRequest::WaylandResizeOnly[\s\S]*?gtk_window\.resize/);
  assert.doesNotMatch(linuxBounds, /\.set_size|\.set_position/);
  assert.match(nativeInteraction, /linux_cairo_rectangle_for_physical_hit/);
  assert.doesNotMatch(nativeInteraction, /inner_size\(\)/);
  assert.match(app, /if \(!surface \|\| revision !== portraitHitRevision\) return null/);
  assert.doesNotMatch(app, /PORTRAIT_SURFACE_SETTLE_MS|settlePortraitScaleSurface|schedulePortraitSurfaceSettle/);
  assert.match(settingsAppearance, /pointerdown", beginPortraitScaleGesture/);
  assert.match(settingsAppearance, /pointerup"[\s\S]*?finishPortraitScaleGesture/);
  assert.match(settingsAppearance, /window\.addEventListener\?\.\("blur", finishPortraitScaleGesture\)/);
  assert.match(settingsAppearance, /settings_character_appearance_scale_gesture/);
  assert.match(app, /listenAppEvent\("sakura:\/\/portrait-scale-gesture"[\s\S]*?tracedInteractionInvoke\([\s\S]*?"begin_portrait_scale_preview"[\s\S]*?activatePortraitHitTest\(key, revision, endTrace\)/);
  assert.match(declarationBlock("portrait-image"), /will-change:\s*transform/);
  const nativePortraitUpdate = nativeMain.match(/fn activate_portrait_hit_test[\s\S]*?\n\}/)?.[0] || "";
  assert.match(nativePortraitUpdate, /let stabilize_portrait_scale = geometry\.stabilizes_portrait_scale_bounds\(\)[\s\S]*?compute_pet_window_layout\([\s\S]*?stabilize_portrait_scale,[\s\S]*?portrait_scale_preview_active = stabilize_portrait_scale/);
  assert.match(nativeTransaction, /previous_region_relaxed[\s\S]*?if !previous_region_relaxed/);
  assert.match(nativeMain, /activate_portrait_hit_test,[\s\S]*?settle_portrait_scale_surface,/);
  assert.match(nativeMain, /settings_character_appearance_preview,[\s\S]*?settings_character_appearance_scale_gesture,[\s\S]*?settings_character_appearance_scale_frame,[\s\S]*?settings_character_appearance_layout_gesture,[\s\S]*?settings_character_appearance_layout_frame,/);
});

test("portrait scale frames wait for the stable envelope and keep macOS hit routing current", () => {
  const frameListenerStart = app.indexOf('listenAppEvent("sakura://portrait-scale-frame"');
  const gestureListenerStart = app.indexOf('listenAppEvent("sakura://portrait-scale-gesture"');
  assert.ok(frameListenerStart >= 0);
  assert.ok(gestureListenerStart > frameListenerStart);
  const frameListener = app.slice(frameListenerStart, gestureListenerStart);
  assert.match(frameListener, /async \(event\)/);
  assert.match(frameListener, /const ready = portraitScaleGestureReady/);
  assert.match(frameListener, /const preview = await ready/);
  assert.match(frameListener, /!preview[\s\S]*?!portraitScaleGestureActive/);
  assert.match(frameListener, /if \(preview\.deferredNative\) \{[\s\S]*?syncPortraitAppearance[\s\S]*?if \(!preview\.deferredHitRegions\)[\s\S]*?enqueuePortraitScaleHitFrame/);
  assert.match(app, /async function drainPortraitScaleHitFrames\(\)[\s\S]*?while \(pendingPortraitScaleHitFrame\)[\s\S]*?pendingPortraitScaleHitFrame = null[\s\S]*?frame\.ready !== portraitScaleGestureReady[\s\S]*?const revision = \+\+portraitHitRevision/);
  assert.match(app, /function enqueuePortraitScaleHitFrame[\s\S]*?pendingPortraitScaleHitFrame = \{ key, portraitScalePercent, trace, ready \}/);
  assert.match(app, /publication\.active === false|portraitScaleGestureActive = false[\s\S]*?pendingPortraitScaleHitFrame = null[\s\S]*?const revision = \+\+portraitHitRevision[\s\S]*?activatePortraitHitTest\(key, revision, endTrace\)/);
  assert.ok(frameListener.indexOf("await ready") < frameListener.indexOf("syncPortraitAppearance"));
  const gestureListener = app.slice(gestureListenerStart);
  assert.match(gestureListener, /\.then\(\(preview\) => \{[\s\S]*?if \(!preview\) return null;[\s\S]*?deferredNative: preview\.deferredNative === true,[\s\S]*?deferredHitRegions: preview\.deferredHitRegions === true/);
});

test("the adaptive composer uses semantic line metrics instead of pixel baseline offsets", () => {
  const composerInput = styles.match(/\.composer textarea\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(composerInput, /padding:\s*8px 0/);
  assert.match(composerInput, /overflow-y:\s*hidden/);
  assert.match(composerInput, /line-height:\s*1\.5/);
  assert.doesNotMatch(composerInput, /padding:\s*\d+px\s+\d+px\s+\d+px/);
  assert.match(app, /createAdaptiveControlSurface/);
  assert.equal(layoutContract.schemaVersion, 5);
  assert.equal(layoutContract.controlPanel.inputExpandedMinRows, 1);
  assert.equal(layoutContract.controlPanel.inputMaxRows, 3);
  assert.equal(layoutContract.controlPanel.inputToolbarHeight, 40);
  assert.match(styles, /#composer-attachment\[aria-expanded="true"\] svg\s*\{\s*transform:\s*rotate\(45deg\)/);
  assert.match(styles, /\.composer-attachment-menu\s*\{[^}]*position:\s*absolute[^}]*bottom:\s*5px/s);
  assert.match(app, /inputTransition:[\s\S]*?durationMs:[\s\S]*?COMPOSER_MOTION_DURATION_MS/);
  assert.match(app, /stagingHeight:[\s\S]*?composerStagingHeight/);
  assert.match(styles, /\.composer\[data-input-motion="staging"\][\s\S]*?grid-template-rows:\s*minmax\(0, 1fr\)/);
  assert.match(styles, /\.composer\s*\{[\s\S]*?backdrop-filter:\s*none/);
  assert.match(styles, /data-input-visual-effect="gaussian_blur"[\s\S]*?backdrop-filter:\s*blur\(8px\)/);
  assert.match(adaptiveSurface, /composer\.dataset\.inputMotion = "staging"[\s\S]*?requestFrame\(launch\)/);
  assert.match(adaptiveSurface, /function schedule\(\)[\s\S]*?stageImmediateExpansion\(\)[\s\S]*?requestFrame/);
  assert.match(nativeMain, /let prepare_input_transition =[\s\S]*?is_animated_input_contraction/);
  assert.match(app, /startNativeExpansion:[\s\S]*?start_pet_input_expansion/);
  assert.match(nativeMain, /fn start_pet_input_expansion[\s\S]*?relax_native_hit_regions/);
});

test("adaptive control geometry keeps contraction inside the native transition envelope", () => {
  const bubble = declarationBlock(".bubble");
  const composer = declarationBlock(".composer");
  assert.doesNotMatch(bubble, /transition:[^;]*(?:top|height)/s);
  assert.doesNotMatch(composer, /transition:[^;]*(?:top|height)/s);
  assert.match(adaptiveSurface, /direction !== "stable"[\s\S]*?composer\.animate/);
  assert.match(app, /startNativeTransition:[\s\S]*?start_pet_input_transition/);
  assert.match(adaptiveSurface, /startNativeTransition\(nativeTransition\.revision\)[\s\S]*?composer\.animate/);
  assert.match(nativeMain, /prepare_input_transition[\s\S]*?PendingInputSurfaceTransition/);
  assert.match(nativeMain, /fn start_pet_input_transition[\s\S]*?schedule_input_contraction_region_commit/);
  assert.match(app, /const bubbleBody = document\.querySelector\("\.reply-body"\)/);
  assert.match(app, /bubbleBody,/);
});

test("overflowing composer text uses a quiet theme-owned scrollbar", () => {
  assert.match(styles, /\.composer textarea\[data-overflow="true"\]\s*\{\s*overflow-y:\s*auto/);
  assert.match(styles, /\.composer textarea\s*\{[^}]*scrollbar-color:\s*color-mix\(in srgb, var\(--primary\) 46%, transparent\) transparent[^}]*scrollbar-gutter:\s*stable[^}]*scrollbar-width:\s*thin/s);
  assert.match(styles, /\.composer textarea::\-webkit-scrollbar\s*\{[^}]*width:\s*6px/s);
  assert.match(styles, /\.composer textarea::\-webkit-scrollbar-track,\s*\.composer textarea::\-webkit-scrollbar-corner\s*\{[^}]*background:\s*transparent/s);
  assert.match(styles, /\.composer textarea::\-webkit-scrollbar-thumb\s*\{[^}]*min-height:\s*28px[^}]*border-radius:\s*999px[^}]*background:\s*color-mix\(in srgb, var\(--primary\) 46%, transparent\)/s);
  assert.match(styles, /\.composer textarea::\-webkit-scrollbar-button\s*\{[^}]*display:\s*none[^}]*width:\s*0[^}]*height:\s*0/s);
});

test("the composer action is an accessible local SVG send, stop, and recovery control", () => {
  assert.match(index, /id="composer-send"[^>]*data-action="send"[^>]*aria-label="发送消息"/);
  assert.match(index, /composer-action-icon--send[\s\S]*?<svg[\s\S]*?<path/);
  assert.match(index, /composer-action-icon--cancel[\s\S]*?<svg[\s\S]*?<rect/);
  assert.match(app, /send\.dataset\.action = state\.canCancel \? "cancel" : state\.canRetry \? "retry" : "send"/);
  assert.match(app, /const actionLabel = state\.canCancel \? "停止回复" : state\.canRetry \? "重试连接" : "发送消息"/);
  assert.match(app, /send\.setAttribute\("aria-label", actionLabel\);[\s\S]*?send\.title = actionLabel/);
  assert.match(app, /else if \(state\.canRetry\)[\s\S]*?invoke\("retry_core"\)/);
  assert.doesNotMatch(styles, /--button-font-size/);
});

test("adaptive geometry settles after fonts and before the hidden window is revealed", () => {
  const fontsIndex = app.lastIndexOf("await waitForRuntimeFonts()");
  const adaptiveIndex = app.lastIndexOf("await adaptiveSurface.refresh()");
  const readyIndex = app.lastIndexOf("document.body.dataset.shellState =");
  const revealIndex = app.lastIndexOf('await invoke("reveal_pet_window")');
  assert.ok(fontsIndex >= 0 && fontsIndex < adaptiveIndex);
  assert.ok(adaptiveIndex < readyIndex && readyIndex < revealIndex);
});

test("product menu presentation is themed in the WebView while Rust owns capabilities and actions", () => {
  assert.match(index, /id="pet-context-menu"[^>]*role="menu"/);
  assert.equal((index.match(/data-menu-unavailable/g) || []).length, 4);
  assert.equal((index.match(/role="menuitemcheckbox"/g) || []).length, 3);
  for (const label of ["隐藏至托盘", "显示中文字幕", "完整访问权限", "保持置顶", "历史记录", "运行日志 \/ 诊断", "设置", "退出"])
    assert.match(index, new RegExp(label));
  assert.match(app, /invoke\("open_pet_context_menu"/);
  assert.match(contextMenu, /invoke\("close_pet_context_menu"/);
  assert.match(contextMenu, /invoke\("activate_pet_context_menu_action"/);
  assert.match(contextMenu, /ArrowDown/);
  assert.match(contextMenu, /ArrowUp/);
  assert.match(contextMenu, /Home/);
  assert.match(contextMenu, /End/);
  assert.match(nativeMain, /ProductMenuAction::from_id/);
  assert.match(nativeMain, /set_pet_context_menu_surface/);
  assert.match(nativeMain, /expand_surface_bounds_for_overlay/);
  assert.match(nativeMain, /expand_application_preserving_anchor/);
  assert.match(nativeMain, /apply_native_pet_surface_bounds_transaction_preserving_top_left/);
  assert.match(nativeMain, /rollback_pet_surface_with_bounds_mode/);
  const menuSurfaceSetter = nativeMain.match(/fn set_pet_context_menu_surface[\s\S]*?\n}/)?.[0] || "";
  const menuSurfaceCloser = nativeMain.match(/fn close_pet_context_menu_surface[\s\S]*?\n}/)?.[0] || "";
  assert.match(menuSurfaceSetter, /apply_native_pet_surface_bounds_transaction_preserving_top_left/);
  assert.match(menuSurfaceSetter, /rollback_pet_surface_with_bounds_mode/);
  assert.match(menuSurfaceCloser, /apply_native_pet_surface_bounds_transaction_preserving_top_left/);
  assert.match(menuSurfaceCloser, /rollback_pet_surface_with_bounds_mode/);
  assert.match(nativeMain, /context_menu_base_application/);
  assert.match(nativeMain, /context_menu_base_hit_regions/);
  assert.match(nativeMain, /geometry\.active_bounds = Some\(application\.active_bounds\)/);
  assert.doesNotMatch(nativeMain, /restore_full_hit_region/);
  assert.match(nativeMain, /context_menu_open/);
  assert.match(nativeProductShell, /ProductMenuCapabilityManifest/);
  assert.doesNotMatch(nativeProductShell, /popup_menu_at/);
  assert.match(nativeMain, /show_or_focus_settings/);
  const webviewMenuDispatch =
    nativeMain.match(/fn dispatch_webview_product_menu_action[\s\S]*?\n}/)?.[0] || "";
  assert.match(webviewMenuDispatch, /std::thread::Builder::new/);
  assert.match(webviewMenuDispatch, /run_on_main_thread/);
  assert.match(webviewMenuDispatch, /emit_product_menu_error/);
  const webviewMenuCommand =
    nativeMain.match(/fn activate_pet_context_menu_action[\s\S]*?\n}/)?.[0] || "";
  assert.match(webviewMenuCommand, /dispatch_webview_product_menu_action/);
  assert.match(nativeMain, /SETTINGS_WINDOW_LABEL/);
  for (const token of [
    "min-width: 226px",
    "border-radius: 14px",
    "inset 0 1px 0",
    "inset 0 0 0 1px",
    "min-height: 30px",
    "border-radius: 8px",
    "var(--input-background)",
    "var(--panel-background)",
    "var(--muted-text)",
    "pet-context-menu-in 110ms",
  ]) assert.ok(styles.includes(token), token);
  const contextMenuStyle = styles.match(/\.pet-context-menu\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(contextMenuStyle, /backdrop-filter/);
  assert.match(contextMenuStyle, /box-shadow:\s*inset[\s\S]*?,\s*inset/);
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
  assert.match(visibilityToggle, /set_visible\(&window, true\)/);
  assert.match(visibilityToggle, /reapply_current_pet_hit_region\(&window\)/);
  assert.match(visibilityToggle, /sync_product_tray_visibility\(app, false\)/);
  assert.match(visibilityToggle, /sync_product_tray_visibility\(app, true\)/);
  assert.match(nativeWindowBackend, /fn set_visible\([\s\S]*?window[\s\S]*?\.show\(\)[\s\S]*?\.set_focus\(\)/);
  assert.equal(nativeMain.match(/toggle_pet_visibility\(app\)/g)?.length, 2);
  assert.match(nativeMain, /reveal_pet_window[\s\S]*?sync_product_tray_visibility\(window\.app_handle\(\), true\)/);

  const trayEvents = nativeMain.match(/\.on_tray_icon_event\([\s\S]*?\.on_window_event/)?.[0] || "";
  assert.match(trayEvents, /TrayIconEvent::Click/);
  assert.match(trayEvents, /MouseButton::Left/);
  assert.match(trayEvents, /MouseButtonState::Up/);
});

test("the bubble has no close action while native close still coordinates whole-app exit", () => {
  assert.doesNotMatch(index, /close-action|id="close-window"/);
  assert.doesNotMatch(app, /close_pet_window/);
  assert.match(app, /beforeunload", dispose/);

  const nativeWindowEvents = nativeMain.match(/\.on_window_event\([\s\S]*?\.invoke_handler/)?.[0] || "";
  assert.match(nativeWindowEvents, /window\.label\(\) == "main"/);
  assert.match(nativeWindowEvents, /CloseRequested[\s\S]*?api\.prevent_close\(\)[\s\S]*?request_app_exit/);
});

test("reply navigation is centered across the bubble without a connecting rail", () => {
  const nav = index.match(/<nav id="reply-history-nav"[\s\S]*?<\/nav>/)?.[0] || "";
  assert.match(nav, /aria-label="回复记录"/);
  assert.match(nav, /id="reply-history-previous"[\s\S]*aria-label="上一条回复"/);
  assert.match(nav, /id="reply-history-next"[\s\S]*aria-label="下一条回复"/);
  assert.equal((nav.match(/<svg/g) || []).length, 2);
  assert.match(styles, /\.reply-body\s*\{[^}]*position:\s*static[^}]*padding-right:\s*40px/s);
  assert.match(styles, /\.reply-history-nav\s*\{[^}]*position:\s*absolute[^}]*top:\s*50%[^}]*right:\s*20px[^}]*width:\s*30px[^}]*height:\s*72px[^}]*display:\s*grid[^}]*grid-template-rows:\s*30px 30px[^}]*row-gap:\s*12px[^}]*transform:\s*translateY\(-50%\)/s);
  assert.doesNotMatch(styles, /\.reply-history-nav::before\s*\{/);
  assert.match(styles, /\.reply-history-button:disabled\s*\{[^}]*opacity:/s);
  assert.match(app, /replyHistoryPrevious\.addEventListener\("click",\s*\(\)\s*=>\s*reviewReplyBy\(-1\)\)/);
  assert.match(app, /replyHistoryNext\.addEventListener\("click",\s*\(\)\s*=>\s*reviewReplyBy\(1\)\)/);
  assert.match(app, /presentation\.reviewReplyAt\(targetIndex,\s*selectSegmentText\(segment,\s*subtitleLanguage\)\)/);
  assert.match(app, /reason:\s*"history"[\s\S]*?syncBubbleWithPortrait:\s*true/);
});

test("settled subtitle changes synchronously repaint the currently reviewed segment", () => {
  const listener = app.match(/sakura:\/\/subtitle-language-changed[\s\S]*?let coreRebindRevision/)?.[0] || "";
  assert.match(listener, /typewriter\.updateLanguage\(language\)/);
  assert.match(listener, /state\.replyHistorySegments\[state\.replyHistoryIndex\]/);
  assert.match(listener, /presentation\.refreshVisibleReply\(selectSegmentText\(segment,\s*language\)\)/);
  assert.match(listener, /render\(refreshed\.state/);
});

test("pointer-open menus do not paint the first action as persistently focused", () => {
  assert.match(app, /focusFirst:\s*!event\.pointerType\s*&&\s*event\.button\s*===\s*0/);
  assert.match(styles, /\.pet-context-menu\.is-keyboard-open button:not\(:disabled\):focus-visible/);
  assert.doesNotMatch(styles, /button:not\(:disabled\):hover,\s*\n\.pet-context-menu button:not\(:disabled\):focus-visible/);
});

test("confirmed settings close destroys the window before ending its appearance session", () => {
  const resolveClose = nativeMain.match(/fn resolve_settings_exit[\s\S]*?\n}/)?.[0] || "";
  assert.match(resolveClose, /window\.destroy\(\)/);
  assert.doesNotMatch(resolveClose, /appearance\.close_session/);
  const nativeWindowEvents = nativeMain.match(/\.on_window_event\([\s\S]*?\.invoke_handler/)?.[0] || "";
  assert.match(nativeWindowEvents, /WindowEvent::Destroyed[\s\S]*?appearance\.close_session\(\)/);
});

test("Tools settings stay feature-scoped and confirmation remains outside the WebView", () => {
  assert.match(settingsTools, /settings_tools_save/);
  assert.match(settingsTools, /windowGeneration/);
  assert.match(settingsTools, /coreGenerationId/);
  assert.doesNotMatch(settingsTools, /actionId|tool\.confirm|tool\.reject/);
  assert.match(nativeProductShell, /tools\.runtime_limits/);
  assert.match(nativeProductShell, /tools\.confirmation_policy/);
  assert.match(nativeProductShell, /tools\.desktop_mcp[\s\S]*?unavailable/);
});

test("portrait click-through is tightened after the decoded contain size is known", () => {
  assert.match(app, /tracedInteractionInvoke\([\s\S]*?"activate_portrait_hit_test"/);
  assert.match(nativeMain, /fn activate_portrait_hit_test/);
  assert.match(nativeInteraction, /logical_hit_regions_with_portrait_size/);
  assert.match(nativeInteraction, /contained_portrait_rect/);
  assert.match(nativeMain, /active_portrait_alpha_mask/);
  assert.match(nativeInteraction, /alpha_hit_rectangles/);
  assert.match(nativeInteraction, /portrait_alpha_mask/);
});

test("Windows drag is borderless, alpha-clipped, and independent of the caption move loop", () => {
  assert.match(nativeInteraction, /SetWindowRgn\(hwnd, Some\(combined\)/);
  assert.match(nativeInteraction, /SetWindowRgn\(hwnd, Some\(combined\), false\)[\s\S]*?InvalidateRect\(Some\(hwnd\), None, false\)/);
  assert.match(nativeInteraction, /SetWindowSubclass/);
  assert.match(nativeWindowBackend, /DWMWA_NCRENDERING_POLICY/);
  assert.match(nativeWindowBackend, /DWMNCRP_DISABLED/);
  assert.match(nativeInteraction, /CreateRectRgn/);
  assert.match(nativeInteraction, /CombineRgn\(Some\(combined\), Some\(combined\), Some\(part\), RGN_OR\)/);
  assert.doesNotMatch(nativeInteraction, /WS_EX_TRANSPARENT/);
  assert.doesNotMatch(nativeInteraction, /HTTRANSPARENT/);
  assert.doesNotMatch(nativeInteraction, /GWLP_WNDPROC/);
  assert.match(nativeInteraction, /WM_NCCALCSIZE/);
  assert.match(nativeInteraction, /WM_NCPAINT/);
  assert.match(nativeInteraction, /GetAsyncKeyState/);
  assert.match(nativeInteraction, /SetWindowPos/);
  assert.match(nativeInteraction, /if \[x, y\] != last_window_origin\s*\{[\s\S]*?SetWindowPos/);
  assert.doesNotMatch(nativeInteraction, /SendMessageW|WM_NCLBUTTONDOWN/);
  assert.match(nativeMain, /async fn start_pet_drag[\s\S]*?spawn_blocking[\s\S]*?start_pet_drag_blocking/);
  assert.match(app, /classList\.add\("is-native-dragging"\)[\s\S]*?classList\.remove\("is-native-dragging"\)/);
  assert.match(styles, /\.portrait\.is-native-dragging\s*\{\s*cursor:\s*grabbing/);
  const dragBackend = nativeWindowBackend.slice(
    nativeWindowBackend.indexOf("fn start_drag("),
    nativeWindowBackend.indexOf("fn set_visible("),
  );
  assert.doesNotMatch(dragBackend, /self\.prepare_window\(window\)/);
  assert.equal(dragBackend.match(/enforce_native_borderless_window\(window\)\?/g)?.length, 2);
  assert.ok(dragBackend.indexOf("enforce_native_borderless_window(window)?") < dragBackend.indexOf("start_native_drag"));
  assert.ok(dragBackend.lastIndexOf("enforce_native_borderless_window(window)?") > dragBackend.indexOf("start_native_drag"));
});

test("Windows transparent click-through acceptance follows the dynamic native region", () => {
  assert.match(windowsClickthroughAcceptance, /GetWindowRgn\(\$petHandle, \$region\)/);
  assert.match(windowsClickthroughAcceptance, /Get-RegionCandidatePoints[\s\S]*?-Inside \$false/);
  assert.match(windowsClickthroughAcceptance, /for \(\$index = 0; \$index -lt 20; \$index\+\+\)/);
  assert.match(windowsClickthroughAcceptance, /TransparentClicksDeliveredToBackground/);
  assert.match(windowsClickthroughAcceptance, /TransparentPointRejectedDrag/);
  assert.match(windowsClickthroughAcceptance, /VisibleAlphaPointStartedDrag/);
  assert.match(windowsClickthroughAcceptance, /WorkAreaTop/);
  assert.doesNotMatch(windowsClickthroughAcceptance, /Round\(20 \* \$scale\)|Round\(480 \* \$scale\)/);
});
