import { createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createFakeChatCore } from "./chat/fake-chat-core.js";
import { applyTheme } from "./core/theme.js";
import { createBubbleScroll } from "./pet/bubble-scroll.js";
import { loadCurrentCharacterPresentation, portraitSequence } from "./pet/character-presentation.js";
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
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
let contentScale = 1;
let currentHitRegions = null;
let renderedPortrait = null;
let disposed = false;
let presentationUnavailable = false;

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
const layoutController = createLayoutController({
  computeLayout: () => productLayout,
  applyNativeLayout: ({ revision }) => invoke("apply_pet_layout", { state: PRODUCT_LAYOUT_STATE, revision }),
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    applyPetLayout(stage, layout, contentScale);
    currentHitRegions = computeHitRegions(layout);
    document.body.dataset.shellState = "product-ready";
    inputFocus.setPresentation(PRODUCT_LAYOUT_STATE);
  },
});
await layoutController.transition(PRODUCT_LAYOUT_STATE, "fixed-product-shell");

let characterPresentation;
try {
  characterPresentation = await loadCurrentCharacterPresentation({ invoke });
} catch {
  presentationUnavailable = true;
  document.body.dataset.shellState = "presentation-failed";
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

const portraits = portraitSequence(characterPresentation);
applyTheme(characterPresentation.themeTokens);
characterName.textContent = characterPresentation.displayName;
input.placeholder = `和${characterPresentation.displayName}说点什么……`;
portraitFallbackName.textContent = characterPresentation.displayName;
portrait.setAttribute("aria-label", `${characterPresentation.displayName} 的立绘，可拖动窗口`);
portraitCurrent.alt = `${characterPresentation.displayName} 立绘`;
if (!presentationUnavailable) clearRecoverableError();

const expectedByUrl = new Map(
  characterPresentation.portraitKeys.map((key) => [
    characterPresentation.portraitResourceUrls[key],
    characterPresentation.portraitMetadata[key],
  ]),
);

function loadImage(source) {
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

const portraitController = createPortraitController({
  assets: characterPresentation.portraitResourceUrls,
  defaultKey: characterPresentation.defaultPortraitKey,
  loadImage,
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  preview: ({ source }) => {
    portraitNext.src = source;
    portrait.classList.add("is-transitioning");
  },
  commit: ({ source }) => {
    portraitCurrent.src = source;
    portraitNext.removeAttribute("src");
    portrait.classList.remove("is-transitioning");
    portraitFallback.hidden = true;
    if (!presentationUnavailable) clearRecoverableError();
  },
  showFallback: () => {
    portrait.classList.remove("is-transitioning");
    portraitFallback.hidden = false;
  },
  reportError: ({ code }) => {
    if (!presentationUnavailable) {
      showRecoverableError(code === "PORTRAIT_KEY_UNKNOWN" ? "表情映射无效，已恢复默认立绘。" : "立绘解码失败，仍可继续输入。");
    }
  },
});

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
      inPortrait: Boolean(event.target.closest("#portrait")),
    })
  ) {
    return;
  }
  event.preventDefault();
  try {
    await invoke("show_pet_context_menu", {
      surfaceX: point[0],
      surfaceY: point[1],
      popupX: event.clientX,
      popupY: event.clientY,
    });
  } catch {
    showRecoverableError("桌宠菜单暂时无法打开，请稍后重试。");
  }
});

window.__TAURI__?.event?.listen?.("sakura://product-menu-error", () => {
  showRecoverableError("设置窗口暂时无法打开，请稍后重试。");
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
  typewriter.dispose();
  bubbleScroll.dispose();
  portraitController.dispose();
  fakeCore.dispose();
}

document.querySelector("#close-window").addEventListener("click", () => {
  dispose();
  invoke("close_pet_window");
});
window.addEventListener("beforeunload", dispose, { once: true });

portraitController.beginGeneration("fake-generation-1");
await portraitController.show(characterPresentation.defaultPortraitKey, {
  immediate: true,
  generation: "fake-generation-1",
});
render(presentation.current());
await enableAcceptanceEntry();
fakeCore.start();
