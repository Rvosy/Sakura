import { createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createFakeChatCore } from "./chat/fake-chat-core.js";
import { applyTheme } from "./core/theme.js";
import {
  appearanceChanges,
  applyAppearanceVariables,
  constrainedPortraitScale,
  validateAppearancePublication,
} from "./pet/appearance.js";
import { createBubbleScroll } from "./pet/bubble-scroll.js";
import { loadCurrentCharacterPresentation, portraitSequence } from "./pet/character-presentation.js";
import { PetContextMenu } from "./pet/context_menu.js";
import {
  classifyPointerHit,
  computeHitRegions,
  shouldOpenProductMenu,
  shouldStartNativeDrag,
} from "./pet/hit-regions.js";
import { createInputFocusController } from "./pet/input-focus.js";
import { createLayoutController } from "./pet/layout-controller.js";
import { applyPetLayout, computePetLayout, PRODUCT_LAYOUT_STATE, validateLayoutContract } from "./pet/layout.js";
import { inferTextLanguage, renderMultilingualText } from "./pet/multilingual-text.js";
import { createPortraitController } from "./pet/portrait-controller.js";
import { createTypewriter } from "./pet/typewriter.js";

const invoke = window.__TAURI__.core.invoke;
const stage = document.querySelector("#pet-stage");
const bubbleCopy = document.querySelector("#bubble-copy");
const chatPhase = document.querySelector("#chat-phase");
const characterName = document.querySelector("#character-name");
const presentationError = document.querySelector("#presentation-error");
const typewriterSkip = document.querySelector("#typewriter-skip");
const composer = document.querySelector("#composer");
const input = document.querySelector("#composer-input");
const send = document.querySelector("#composer-send");
const portrait = document.querySelector("#portrait");
const portraitCurrent = document.querySelector("#portrait-current");
const portraitNext = document.querySelector("#portrait-next");
const portraitFallback = document.querySelector("#portrait-fallback");
const portraitFallbackName = document.querySelector("#portrait-fallback-name");
const contextMenuElement = document.querySelector("#pet-context-menu");
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
let contentScale = 1;
let currentHitRegions = null;
let renderedPortrait = null;
let disposed = false;
let presentationUnavailable = false;
const appEventUnlisteners = [];

const contextMenu = new PetContextMenu({
  menu: contextMenuElement,
  invoke,
  onError: (message) => showRecoverableError(message),
});

async function listenAppEvent(eventName, handler) {
  const eventApi = window.__TAURI__?.event;
  if (typeof eventApi?.listen !== "function") throw new Error("TAURI_EVENT_LISTENER_UNAVAILABLE");
  const unlisten = await eventApi.listen(eventName, handler);
  if (typeof unlisten === "function") appEventUnlisteners.push(unlisten);
}

function showRecoverableError(message) {
  presentationError.textContent = String(message || "角色表现暂时不可用");
  presentationError.hidden = false;
}

function clearRecoverableError() {
  presentationError.hidden = true;
  presentationError.textContent = "";
}

const inputFocus = createInputFocusController({
  focusInput: () => window.requestAnimationFrame(() => input.focus({ preventScroll: true })),
  readText: () => input.value,
  localSubmit: submitMessage,
});

const contractResponse = await fetch("./pet/layout-contract.json", { cache: "no-store" });
if (!contractResponse.ok) throw new Error("failed to load pet layout contract");
const contract = validateLayoutContract(await contractResponse.json());
const productLayout = computePetLayout(contract, PRODUCT_LAYOUT_STATE);
const initialLayoutRevision = await invoke("current_pet_layout_revision");
const layoutController = createLayoutController({
  initialRevision: initialLayoutRevision,
  computeLayout: () => productLayout,
  applyNativeLayout: ({ revision }) => invoke("apply_pet_layout", { state: PRODUCT_LAYOUT_STATE, revision }),
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    applyPetLayout(stage, layout, contentScale);
    currentHitRegions = computeHitRegions(layout);
    inputFocus.setPresentation(PRODUCT_LAYOUT_STATE);
  },
});
await layoutController.transition(PRODUCT_LAYOUT_STATE, "fixed-product-shell");

let characterPresentation;
try {
  characterPresentation = await loadCurrentCharacterPresentation({ invoke });
} catch {
  presentationUnavailable = true;
  showRecoverableError("当前角色表现加载失败；关闭并重新启动后可重试。");
  characterPresentation = Object.freeze({
    generationId: "unavailable",
    characterId: "unavailable",
    displayName: "当前角色",
    initialMessage: "当前角色表现暂时不可用。",
    themeTokens: Object.freeze({}),
    defaultPortraitKey: "__default__",
    portraitKeys: Object.freeze(["__default__"]),
    portraitResourceUrls: Object.freeze({
      __default__: "http://sakura-character.localhost/v1/00/character-v1-00-portrait-00",
    }),
    portraitMetadata: Object.freeze({
      __default__: Object.freeze({ width: 1, height: 1, byteLength: 1 }),
    }),
  });
}

let portraits = portraitSequence(characterPresentation);
let activeAppearance = Object.freeze({
  portraitScalePercent: 100,
  speechFontSize: 19,
  nameFontSize: 13,
  inputFontSize: 15,
  buttonFontSize: 15,
  themeTokens: characterPresentation.themeTokens,
});
try {
  activeAppearance = validateAppearancePublication(
    await invoke("current_character_appearance"),
    characterPresentation,
  );
} catch {
  // Package theme/default sizes remain a complete safe baseline.
}
applyTheme(activeAppearance.themeTokens);
applyAppearanceVariables(activeAppearance);
characterName.textContent = characterPresentation.displayName;
input.placeholder = `和${characterPresentation.displayName}说点什么……`;
portraitFallbackName.textContent = characterPresentation.displayName;
portrait.setAttribute("aria-label", `${characterPresentation.displayName} 的立绘，可拖动窗口`);
portraitCurrent.alt = `${characterPresentation.displayName} 立绘`;
if (!presentationUnavailable) clearRecoverableError();

function expectedPortraitsByUrl(presentation) {
  return new Map(
    presentation.portraitKeys.map((key) => [
      presentation.portraitResourceUrls[key],
      presentation.portraitMetadata[key],
    ]),
  );
}

function loadImage(source, expectedByUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.addEventListener(
      "load",
      async () => {
        try {
          if (typeof image.decode === "function") await image.decode();
          const expected = expectedByUrl.get(source);
          if (!expected || image.naturalWidth !== expected.width || image.naturalHeight !== expected.height) {
            throw new Error("PORTRAIT_DIMENSION_MISMATCH");
          }
          resolve(Object.freeze({ width: image.naturalWidth, height: image.naturalHeight }));
        } catch (error) {
          reject(error);
        }
      },
      { once: true },
    );
    image.addEventListener("error", () => reject(new Error("PORTRAIT_LOAD_FAILED")), { once: true });
    image.src = source;
  });
}

let portraitHitRevision = 0;
let portraitHitTimer = null;
const PORTRAIT_HIT_SETTLE_MS = 90;

function cancelPortraitHitTimer() {
  if (portraitHitTimer === null) return;
  window.clearTimeout(portraitHitTimer);
  portraitHitTimer = null;
}

function syncPortraitAppearance(key, presentation = characterPresentation) {
  const metadata = presentation.portraitMetadata[key]
    || presentation.portraitMetadata[presentation.defaultPortraitKey];
  const scale = constrainedPortraitScale({
    requestedPercent: activeAppearance.portraitScalePercent,
    sourceSize: [metadata.width, metadata.height],
    portraitRect: productLayout.portraitRect,
    windowSize: productLayout.windowSize,
  });
  stage.style.setProperty("--portrait-render-scale", String(scale));
}

function activatePortraitHitTest(key, revision = ++portraitHitRevision) {
  cancelPortraitHitTimer();
  invoke("activate_portrait_hit_test", {
    portraitKey: key,
    revision,
    portraitScalePercent: activeAppearance.portraitScalePercent,
  }).catch(() => {
    showRecoverableError("桌宠透明区域穿透暂时不可用。", { autoHide: true });
  });
}

function schedulePortraitHitTest(key, revision) {
  cancelPortraitHitTimer();
  portraitHitTimer = window.setTimeout(() => {
    portraitHitTimer = null;
    activatePortraitHitTest(key, revision);
  }, PORTRAIT_HIT_SETTLE_MS);
}

async function previewPortraitScale(key) {
  cancelPortraitHitTimer();
  const revision = ++portraitHitRevision;
  await invoke("begin_portrait_scale_preview", { revision });
  if (revision !== portraitHitRevision) return;
  syncPortraitAppearance(key);
  schedulePortraitHitTest(key, revision);
}

function buildPortraitController(boundPresentation, { preserveFrameOnFailure = false } = {}) {
  const expectedByUrl = expectedPortraitsByUrl(boundPresentation);
  return createPortraitController({
    assets: boundPresentation.portraitResourceUrls,
    defaultKey: boundPresentation.defaultPortraitKey,
    loadImage: (source) => loadImage(source, expectedByUrl),
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    preview: ({ source }) => {
      portraitNext.src = source;
      portrait.classList.add("is-transitioning");
    },
    commit: ({ key, source }) => {
      portraitCurrent.src = source;
      portraitNext.removeAttribute("src");
      portrait.classList.remove("is-transitioning");
      portraitFallback.hidden = true;
      syncPortraitAppearance(key, boundPresentation);
      activatePortraitHitTest(key);
      if (!presentationUnavailable) clearRecoverableError();
    },
    showFallback: () => {
      if (preserveFrameOnFailure) return;
      portrait.classList.remove("is-transitioning");
      portraitFallback.hidden = false;
    },
    reportError: ({ code }) => {
      if (!presentationUnavailable) {
        showRecoverableError(code === "PORTRAIT_KEY_UNKNOWN" ? "表情映射无效，已恢复默认立绘。" : "立绘解码失败，仍可继续输入。");
      }
    },
  });
}

let portraitController = buildPortraitController(characterPresentation);

const presentation = createChatPresentationReducer({
  initialMessage: characterPresentation.initialMessage,
  defaultPortraitKey: portraits.default,
  thinkingPortraitKey: portraits.thinking,
  concernedPortraitKey: portraits.concerned,
});
const fakeCore = createFakeChatCore({ portraits });
const bubbleScroll = createBubbleScroll({ viewport: bubbleCopy, renderText: renderMultilingualText });

const phaseLabels = Object.freeze({
  booting: "正在准备",
  ready: "在线",
  thinking: "正在思考",
  typing: "正在回复",
  settled: "在线",
  error: "回复失败",
  reconnecting: "正在重连",
});

const typewriter = createTypewriter({
  onStart: () => bubbleScroll.beginReply(),
  onText: (text, bubbleUpdate) => {
    const result = presentation.setTypingText(text);
    if (result.applied) render(result.state, bubbleUpdate);
  },
  onSegment: (segment) => {
    const result = presentation.setTypingSegment(segment);
    if (result.applied) render(result.state);
  },
  onComplete: () => {
    const result = presentation.finishTyping();
    if (result.applied) render(result.state);
  },
});

function render(state, bubbleUpdate = {}) {
  chatPhase.textContent = phaseLabels[state.phase] || "在线";
  bubbleScroll.updateText(state.bubbleText, bubbleUpdate);
  typewriterSkip.hidden = !state.canSkip;
  send.dataset.action = state.canCancel ? "cancel" : "send";
  send.textContent = state.canCancel ? "取消" : "发送";
  input.disabled = presentationUnavailable;
  send.disabled = presentationUnavailable || state.lifecycle !== "ready" || state.phase === "reconnecting";
  document.body.dataset.chatState = state.phase;
  stage.dataset.chatState = state.phase;
  if (renderedPortrait !== state.portrait) {
    renderedPortrait = state.portrait;
    portraitController.show(state.portrait, {
      immediate: portraitCurrent.getAttribute("src") === null,
      generation: state.generationId,
    });
  }
}

function handleCoreEvent(event) {
  const before = presentation.current();
  if (event.type === "lifecycle" && event.generationId !== before.generationId) {
    portraitController.beginGeneration(event.generationId);
    renderedPortrait = null;
  }
  const result = presentation.reduce(event);
  if (!result.applied) return;
  if (result.state.phase === "reconnecting" || (before.phase === "typing" && result.state.phase !== "typing")) {
    typewriter.cancel(result.state.bubbleText);
  }
  render(result.state);
  if (event.type === "chat.completed" && result.state.phase === "typing") typewriter.start(result.state.segments);
}

fakeCore.subscribe(handleCoreEvent);

function submitMessage({ text }) {
  const state = presentation.current();
  if (presentationUnavailable || state.canCancel || state.lifecycle !== "ready") return;
  typewriter.cancel("");
  input.value = "";
  input.lang = "zh-CN";
  try {
    fakeCore.send({ message: text });
  } catch {
    showRecoverableError("消息暂时无法发送，请稍后重试。");
  }
}

for (const dragRegion of dragRegions) {
  dragRegion.addEventListener("pointerdown", async (event) => {
    if (!currentHitRegions) return;
    const hitKind = classifyPointerHit({
      model: currentHitRegions,
      point: [event.clientX / contentScale, event.clientY / contentScale],
      interactiveTarget: Boolean(event.target.closest("[data-interactive]")),
    });
    if (!shouldStartNativeDrag({ hitKind, button: event.button, isPrimary: event.isPrimary })) return;
    event.preventDefault();
    try {
      await invoke("start_pet_drag");
    } catch {
      showRecoverableError("窗口拖动暂时不可用。");
    }
  });
}

document.addEventListener("contextmenu", async (event) => {
  if (contextMenu.contains(event.target)) {
    event.preventDefault();
    return;
  }
  if (!currentHitRegions) return;
  const point = [event.clientX / contentScale, event.clientY / contentScale];
  const hitKind = classifyPointerHit({
    model: currentHitRegions,
    point,
    interactiveTarget: Boolean(event.target.closest("[data-interactive]")),
  });
  if (
    !shouldOpenProductMenu({
      hitKind,
      button: event.button,
    })
  ) {
    if (contextMenu.isOpen()) {
      event.preventDefault();
      contextMenu.close().catch(() => {});
    }
    return;
  }
  event.preventDefault();
  try {
    const manifest = await invoke("open_pet_context_menu", {
      surfaceX: point[0],
      surfaceY: point[1],
    });
    contextMenu.openAt(event.clientX, event.clientY, manifest);
  } catch {
    contextMenu.hide();
    invoke("close_pet_context_menu").catch(() => {});
    showRecoverableError("桌宠菜单暂时无法打开，请稍后重试。");
  }
});

await listenAppEvent("sakura://product-menu-error", () => {
  showRecoverableError("设置窗口暂时无法打开，请稍后重试。");
});

let coreRebindRevision = 0;
let coreRebindTarget = "";

async function rebindCoreGeneration(generationId) {
  if (
    disposed
    || !generationId
    || generationId === characterPresentation.generationId
    || generationId === coreRebindTarget
  ) return false;

  const revision = ++coreRebindRevision;
  coreRebindTarget = generationId;
  let candidateController = null;
  try {
    const nextPresentation = await loadCurrentCharacterPresentation({
      invoke,
      expectedGenerationId: generationId,
    });
    if (disposed || revision !== coreRebindRevision) return false;

    const visiblePortrait = renderedPortrait && nextPresentation.portraitKeys.includes(renderedPortrait)
      ? renderedPortrait
      : nextPresentation.defaultPortraitKey;
    const expectedByUrl = expectedPortraitsByUrl(nextPresentation);

    // Keep the decoded old frame on screen until the replacement resource is ready.
    await loadImage(nextPresentation.portraitResourceUrls[visiblePortrait], expectedByUrl);
    if (disposed || revision !== coreRebindRevision) return false;

    let nextAppearance = activeAppearance;
    try {
      nextAppearance = validateAppearancePublication(
        await invoke("current_character_appearance"),
        nextPresentation,
      );
    } catch {
      // Retain the last valid visual settings; a later appearance publication can update them.
    }

    candidateController = buildPortraitController(nextPresentation, { preserveFrameOnFailure: true });
    const visualGeneration = presentation.current().generationId || generationId;
    candidateController.beginGeneration(visualGeneration);
    const shown = await candidateController.show(visiblePortrait, {
      immediate: true,
      generation: visualGeneration,
    });
    if (!shown.applied) throw new Error("CORE_GENERATION_PORTRAIT_REBIND_FAILED");
    if (disposed || revision !== coreRebindRevision) {
      candidateController.dispose();
      return false;
    }

    const previousController = portraitController;
    const changes = appearanceChanges(activeAppearance, nextAppearance);
    characterPresentation = nextPresentation;
    portraits = portraitSequence(nextPresentation);
    activeAppearance = nextAppearance;
    portraitController = candidateController;
    candidateController = null;
    renderedPortrait = shown.key;
    presentationUnavailable = false;

    characterName.textContent = nextPresentation.displayName;
    input.placeholder = `和${nextPresentation.displayName}说点什么……`;
    portraitFallbackName.textContent = nextPresentation.displayName;
    portrait.setAttribute("aria-label", `${nextPresentation.displayName} 的立绘，可拖动窗口`);
    portraitCurrent.alt = `${nextPresentation.displayName} 立绘`;
    if (changes.theme) applyTheme(activeAppearance.themeTokens);
    if (changes.fonts) applyAppearanceVariables(activeAppearance);
    syncPortraitAppearance(renderedPortrait, nextPresentation);
    previousController.dispose();
    clearRecoverableError();
    render(presentation.current());
    return true;
  } catch {
    candidateController?.dispose();
    if (!disposed && revision === coreRebindRevision) {
      showRecoverableError("桌宠资源重新连接失败；当前画面将继续保留。请稍后重试。");
    }
    return false;
  } finally {
    if (revision === coreRebindRevision) coreRebindTarget = "";
  }
}

await listenAppEvent("sakura://core-generation-changed", (event) => {
  const generationId = event?.payload?.generationId;
  if (
    disposed
    || typeof generationId !== "string"
    || !generationId
    || generationId === characterPresentation.generationId
  ) return;
  void rebindCoreGeneration(generationId);
});

await listenAppEvent("sakura://character-appearance-changed", async (event) => {
  try {
    const nextAppearance = validateAppearancePublication(event.payload, characterPresentation);
    const changes = appearanceChanges(activeAppearance, nextAppearance);
    activeAppearance = nextAppearance;
    if (changes.theme) applyTheme(activeAppearance.themeTokens);
    if (changes.fonts) applyAppearanceVariables(activeAppearance);
    if (changes.portrait) {
      const key = renderedPortrait && characterPresentation.portraitMetadata[renderedPortrait]
        ? renderedPortrait
        : characterPresentation.defaultPortraitKey;
      await previewPortraitScale(key);
    }
  } catch {
    // Old generation, forged fields, and stale callbacks are ignored deterministically.
  }
});

input.addEventListener("compositionstart", (event) => {
  inputFocus.handleCompositionStart(event.data);
  stage.dataset.composing = "true";
});
input.addEventListener("compositionupdate", (event) => inputFocus.handleCompositionUpdate(event.data));
input.addEventListener("compositionend", (event) => {
  inputFocus.handleCompositionEnd(event.data);
  stage.dataset.composing = "false";
});
input.addEventListener("input", () => {
  input.lang = inferTextLanguage(input.value);
});
input.addEventListener("focus", () => inputFocus.handleInputFocus());
input.addEventListener("blur", () => inputFocus.handleInputBlur());
input.addEventListener("keydown", (event) => {
  const result = inputFocus.handleKeyDown(event);
  if (result.handled) event.preventDefault();
});
composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const state = presentation.current();
  if (state.canCancel) fakeCore.cancel(state.operationId);
  else inputFocus.submit("button");
});
typewriterSkip.addEventListener("click", () => typewriter.skip());

window.addEventListener("focus", () => inputFocus.handleWindowFocus());
window.addEventListener("blur", () => inputFocus.handleWindowBlur());
document.addEventListener("visibilitychange", () => inputFocus.handleVisibility(document.visibilityState === "visible"));

async function enableAcceptanceEntry() {
  if (!(await invoke("wp_3_03_acceptance_enabled"))) return;
  const entry = document.querySelector("#acceptance-entry");
  entry.hidden = false;
  entry.setAttribute("aria-hidden", "false");
  entry.addEventListener("click", (event) => {
    const scenario = event.target.closest("[data-scenario]")?.dataset.scenario;
    if (!scenario) return;
    input.value = scenario;
    inputFocus.submit("acceptance");
  });
}

function dispose() {
  if (disposed) return;
  disposed = true;
  coreRebindRevision += 1;
  coreRebindTarget = "";
  if (portraitHitTimer !== null) {
    window.clearTimeout(portraitHitTimer);
    portraitHitTimer = null;
  }
  for (const unlisten of appEventUnlisteners.splice(0)) {
    try {
      Promise.resolve(unlisten()).catch(() => {});
    } catch {
      // The native host may already be gone during WebView teardown.
    }
  }
  typewriter.dispose();
  bubbleScroll.dispose();
  portraitController.dispose();
  fakeCore.dispose();
  contextMenu.dispose();
}

document.querySelector("#close-window").addEventListener("click", async () => {
  try {
    await invoke("close_pet_window");
  } catch {
    showRecoverableError("Sakura 暂时无法退出，请稍后重试。");
  }
});
window.addEventListener("beforeunload", dispose, { once: true });

portraitController.beginGeneration("fake-generation-1");
renderedPortrait = characterPresentation.defaultPortraitKey;
if (presentationUnavailable) {
  portraitFallback.hidden = false;
  syncPortraitAppearance(characterPresentation.defaultPortraitKey);
} else {
  await portraitController.show(characterPresentation.defaultPortraitKey, {
    immediate: true,
    generation: "fake-generation-1",
  });
}
render(presentation.current());
await enableAcceptanceEntry();
fakeCore.start();
document.body.dataset.shellState = presentationUnavailable ? "presentation-failed" : "product-ready";
await invoke("reveal_pet_window");
