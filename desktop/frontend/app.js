import { composerPlaceholder, createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createComposerActionIndicator } from "./chat/composer-action-indicator.js";
import { createRealChatClient } from "./chat/real-chat-client.js";
import { createWaitingIndicator } from "./chat/waiting-indicator.js";
import { waitForRuntimeFonts } from "./core/font-loader.js";
import { applyTheme } from "./core/theme.js";
import {
  appearanceChanges,
  applyAppearanceVariables,
  constrainedPortraitScale,
  validateAppearancePublication,
} from "./pet/appearance.js";
import { createAdaptiveControlSurface } from "./pet/adaptive-control-surface.js";
import { createBubbleScroll } from "./pet/bubble-scroll.js";
import { loadCurrentCharacterPresentation, portraitSequence } from "./pet/character-presentation.js";
import { PetContextMenu } from "./pet/context_menu.js";
import {
  classifyPointerHit,
  clearTextSelection,
  computeHitRegions,
  shouldOpenProductMenu,
  shouldStartNativeDrag,
} from "./pet/hit-regions.js";
import { createInputFocusController } from "./pet/input-focus.js";
import { createLayoutController } from "./pet/layout-controller.js";
import { applyPetLayout, computePetLayout, PRODUCT_LAYOUT_STATE, validateLayoutContract } from "./pet/layout.js";
import { inferTextLanguage, renderMultilingualText } from "./pet/multilingual-text.js";
import { createPortraitController } from "./pet/portrait-controller.js";
import { createTypewriter, selectSegmentText } from "./pet/typewriter.js";

const invoke = window.__TAURI__.core.invoke;
const stage = document.querySelector("#pet-stage");
const bubbleCopy = document.querySelector("#bubble-copy");
const bubbleBody = document.querySelector(".reply-body");
const replyHistoryPrevious = document.querySelector("#reply-history-previous");
const replyHistoryNext = document.querySelector("#reply-history-next");
const bubbleHeader = document.querySelector(".bubble-header");
const chatPhase = document.querySelector("#chat-phase");
const characterName = document.querySelector("#character-name");
const presentationError = document.querySelector("#presentation-error");
const composer = document.querySelector("#composer");
const input = document.querySelector("#composer-input");
const send = document.querySelector("#composer-send");
const cancelIcon = send.querySelector(".composer-action-icon--cancel svg");
const cancelShape = cancelIcon.querySelector("rect");
const portrait = document.querySelector("#portrait");
const portraitCurrent = document.querySelector("#portrait-current");
const portraitNext = document.querySelector("#portrait-next");
const portraitFallback = document.querySelector("#portrait-fallback");
const portraitFallbackName = document.querySelector("#portrait-fallback-name");
const contextMenuElement = document.querySelector("#pet-context-menu");
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
const POINTER_INTERACTIVE_SELECTOR = "[data-interactive], [data-selectable-text]";
let contentScale = 1;
let activeBounds = [0, 0, 900, 996];
let activeSurfaceRevision = 0;
let currentHitRegions = null;
let currentPortraitSourceSize = null;
let renderedPortrait = null;
let disposed = false;
let presentationUnavailable = false;
let activeAppearance = null;
const appEventUnlisteners = [];
const composerActionIndicator = createComposerActionIndicator({
  svg: cancelIcon,
  shape: cancelShape,
  prefersReducedMotion: () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
});

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

function isBubbleScrollbarHit(event) {
  const viewport = event.target.closest?.(".bubble-copy");
  if (!viewport || viewport.scrollHeight <= viewport.clientHeight) return false;
  const bounds = viewport.getBoundingClientRect();
  const renderedScale = viewport.offsetWidth > 0 ? bounds.width / viewport.offsetWidth : 1;
  const scrollbarWidth = Math.max(viewport.offsetWidth - viewport.clientWidth, 10) * renderedScale;
  return event.clientX >= bounds.right - scrollbarWidth;
}

function isInteractivePointerEvent(event) {
  return Boolean(event.target.closest?.(POINTER_INTERACTIVE_SELECTOR)) || isBubbleScrollbarHit(event);
}

const inputFocus = createInputFocusController({
  focusInput: () => window.requestAnimationFrame(() => input.focus({ preventScroll: true })),
  readText: () => input.value,
  localSubmit: submitMessage,
});

const contractResponse = await fetch("./pet/layout-contract.json", { cache: "no-store" });
if (!contractResponse.ok) throw new Error("failed to load pet layout contract");
const contract = validateLayoutContract(await contractResponse.json());
let productLayout = computePetLayout(contract, PRODUCT_LAYOUT_STATE);
const initialLayoutRevision = await invoke("current_pet_layout_revision");
let layoutInitialized = false;
const layoutController = createLayoutController({
  initialRevision: initialLayoutRevision,
  computeLayout: (_state, _placeholder, request = {}) => computePetLayout(
    contract,
    PRODUCT_LAYOUT_STATE,
    "",
    request.adjustments,
    request.measurements,
  ),
  applyNativeLayout: ({ revision, layout }) => invoke("apply_pet_layout", {
    state: PRODUCT_LAYOUT_STATE,
    revision,
    controlSurface: {
      bubbleRect: layout.bubbleRect,
      inputRect: layout.inputRect,
      controlsRect: layout.controlsRect,
    },
  }),
  // Geometry is painted only after Rust commits the matching native bounds and input region.
  previewLayout: () => {},
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    activeBounds = result.activeBounds;
    activeSurfaceRevision = result.revision;
    productLayout = layout;
    applyPetLayout(stage, layout, contentScale, activeBounds);
    currentHitRegions = computeHitRegions(layout, {
      portraitSourceSize: currentPortraitSourceSize,
      portraitScalePercent: activeAppearance?.portraitScalePercent ?? 100,
    });
    if (!layoutInitialized) {
      layoutInitialized = true;
      inputFocus.setPresentation(PRODUCT_LAYOUT_STATE);
    }
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
activeAppearance = Object.freeze({
  portraitScalePercent: 100,
  controlPanelWidth: 640,
  bubbleMaxHeight: 128,
  controlPanelVerticalOffset: 0,
  inputBarOffset: 0,
  speechFontSize: 19,
  nameFontSize: 13,
  inputFontSize: 15,
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
input.placeholder = composerPlaceholder(characterPresentation.displayName, "ready");
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
let layoutPreviewTimer = null;
let layoutPreviewRevision = initialLayoutRevision;
const LAYOUT_PREVIEW_SETTLE_MS = 120;

function cancelLayoutPreviewTimer() {
  if (layoutPreviewTimer !== null) window.clearTimeout(layoutPreviewTimer);
  layoutPreviewTimer = null;
}

async function settleLayoutPreview(revision) {
  layoutPreviewTimer = null;
  await adaptiveSurface.settle();
  if (disposed || revision !== layoutPreviewRevision) return;
  try {
    await invoke("end_control_surface_preview", { revision });
  } catch {
    showRecoverableError("桌宠裁剪区域恢复失败；再次调整布局可重试。");
    return;
  }
  if (revision === layoutPreviewRevision) delete stage.dataset.layoutPreview;
}

async function previewLayoutAppearance() {
  const revision = ++layoutPreviewRevision;
  cancelLayoutPreviewTimer();
  try {
    await invoke("begin_control_surface_preview", { revision });
  } catch {
    if (revision === layoutPreviewRevision) {
      adaptiveSurface.invalidate();
      showRecoverableError("桌宠布局实时预览暂时不可用。");
    }
    return;
  }
  if (disposed || revision !== layoutPreviewRevision) return;
  stage.dataset.layoutPreview = "active";
  adaptiveSurface.invalidate({ visualPreview: true });
  layoutPreviewTimer = window.setTimeout(
    () => void settleLayoutPreview(revision),
    LAYOUT_PREVIEW_SETTLE_MS,
  );
}

function syncPortraitAppearance(key, presentation = characterPresentation) {
  const metadata = presentation.portraitMetadata[key]
    || presentation.portraitMetadata[presentation.defaultPortraitKey];
  const portraitSourceSize = [metadata.width, metadata.height];
  currentPortraitSourceSize = portraitSourceSize;
  const scale = constrainedPortraitScale({
    requestedPercent: activeAppearance.portraitScalePercent,
    sourceSize: portraitSourceSize,
    portraitRect: productLayout.portraitRect,
    windowSize: productLayout.windowSize,
  });
  stage.style.setProperty("--portrait-render-scale", String(scale));
  currentHitRegions = computeHitRegions(productLayout, {
    portraitSourceSize,
    portraitScalePercent: activeAppearance.portraitScalePercent,
  });
}

function commitSurfaceApplication(surface) {
  contentScale = surface.contentScale;
  activeBounds = surface.activeBounds;
  activeSurfaceRevision = surface.revision;
  applyPetLayout(stage, productLayout, contentScale, activeBounds);
}

function activatePortraitHitTest(key, revision = ++portraitHitRevision) {
  return invoke("activate_portrait_hit_test", {
    portraitKey: key,
    revision,
    portraitScalePercent: activeAppearance.portraitScalePercent,
  }).then((surface) => {
    commitSurfaceApplication(surface);
    return surface;
  }).catch((error) => {
    showRecoverableError("桌宠透明区域穿透暂时不可用。", { autoHide: true });
    throw error;
  });
}

async function previewPortraitScale(key) {
  const revision = ++portraitHitRevision;
  await invoke("begin_portrait_scale_preview", { revision });
  await activatePortraitHitTest(key, revision);
  if (revision !== portraitHitRevision) return;
  syncPortraitAppearance(key);
}

function buildPortraitController(boundPresentation, { preserveFrameOnFailure = false } = {}) {
  const expectedByUrl = expectedPortraitsByUrl(boundPresentation);
  return createPortraitController({
    assets: boundPresentation.portraitResourceUrls,
    defaultKey: boundPresentation.defaultPortraitKey,
    loadImage: (source) => loadImage(source, expectedByUrl),
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    preview: async ({ key, source }) => {
      const revision = ++portraitHitRevision;
      const surface = await invoke("prepare_portrait_transition", { portraitKey: key, revision });
      if (!surface) return;
      commitSurfaceApplication(surface);
      portrait.classList.remove("is-transitioning");
      portraitNext.src = source;
      void portrait.offsetWidth;
      portrait.classList.add("is-transitioning");
    },
    cancelPreview: () => {
      portrait.classList.remove("is-transitioning");
      portraitNext.removeAttribute("src");
    },
    commit: async ({ key, source }) => {
      await activatePortraitHitTest(key);
      portraitCurrent.src = source;
      portrait.classList.remove("is-transitioning");
      portraitNext.removeAttribute("src");
      portraitFallback.hidden = true;
      syncPortraitAppearance(key, boundPresentation);
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
const bubbleScroll = createBubbleScroll({ viewport: bubbleCopy, renderText: renderMultilingualText });
const adaptiveSurface = createAdaptiveControlSurface({
  root: stage,
  bubble: document.querySelector("#chat-bubble"),
  bubbleHeader,
  bubbleBody,
  bubbleCopy,
  composer,
  input,
  contract,
  layoutController,
  readAdjustments: () => ({
    controlPanelWidth: activeAppearance.controlPanelWidth,
    bubbleMaxHeight: activeAppearance.bubbleMaxHeight,
    controlPanelVerticalOffset: activeAppearance.controlPanelVerticalOffset,
    inputBarOffset: activeAppearance.inputBarOffset,
  }),
});

const phaseLabels = Object.freeze({
  booting: "正在准备",
  ready: "在线",
  thinking: "正在思考",
  typing: "正在回复",
  settled: "在线",
  error: "回复失败",
  reconnecting: "正在重连",
});

let chatTiming = Object.freeze({
  subtitleTypingIntervalMs: 28,
  replySegmentPauseMs: 160,
});
try {
  const persistedTiming = await invoke("current_chat_presentation_timing");
  if (
    Number.isSafeInteger(persistedTiming?.subtitleTypingIntervalMs)
    && Number.isSafeInteger(persistedTiming?.replySegmentPauseMs)
  ) chatTiming = Object.freeze(persistedTiming);
} catch {
  // Defaults remain valid when the isolated ui.json timing slice cannot be read.
}

let subtitleLanguage = "zh";
try {
  const persistedLanguage = await invoke("current_subtitle_language");
  if (persistedLanguage === "ja") subtitleLanguage = "ja";
} catch {
  // Chinese remains the fail-safe default when the isolated setting cannot be read.
}

const typewriter = createTypewriter({
  intervalMs: chatTiming.subtitleTypingIntervalMs,
  segmentPauseMs: chatTiming.replySegmentPauseMs,
  language: subtitleLanguage,
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  onStart: () => bubbleScroll.beginReply(),
  onText: (text, bubbleUpdate) => {
    const result = presentation.setTypingText(text);
    if (result.applied) render(result.state, bubbleUpdate);
  },
  onSegment: (segment, index) => {
    const result = presentation.setTypingSegment(segment, index);
    if (result.applied) {
      const nextPortrait = result.state.segments[index + 1]?.portrait;
      if (nextPortrait) {
        void portraitController.preload(nextPortrait, { generation: result.state.generationId });
      }
      return render(result.state);
    }
    return undefined;
  },
  onComplete: () => {
    const result = presentation.finishTyping();
    if (result.applied) render(result.state);
  },
});

const waitingIndicator = createWaitingIndicator({
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  onFrame: (frame) => {
    const result = presentation.setThinkingText(frame);
    if (result.applied) render(result.state);
  },
});

function render(state, bubbleUpdate = {}) {
  chatPhase.textContent = phaseLabels[state.phase] || "在线";
  bubbleScroll.updateText(state.bubbleText, bubbleUpdate);
  input.placeholder = composerPlaceholder(characterPresentation.displayName, state.phase);
  send.dataset.action = state.canCancel ? "cancel" : state.canRetry ? "retry" : "send";
  const actionLabel = state.canCancel ? "停止回复" : state.canRetry ? "重试连接" : "发送消息";
  send.setAttribute("aria-label", actionLabel);
  send.title = actionLabel;
  composerActionIndicator.setBusy(state.canCancel);
  input.disabled = presentationUnavailable;
  send.disabled = presentationUnavailable || (
    !state.canRetry
    && (state.lifecycle !== "ready" || state.phase === "reconnecting")
  );
  replyHistoryPrevious.disabled = !state.canReviewPrevious;
  replyHistoryNext.disabled = !state.canReviewNext;
  document.body.dataset.chatState = state.phase;
  stage.dataset.chatState = state.phase;
  adaptiveSurface.schedule();
  if (renderedPortrait !== state.portrait) {
    renderedPortrait = state.portrait;
    return portraitController.show(state.portrait, {
      immediate: portraitCurrent.getAttribute("src") === null,
      generation: state.generationId,
    });
  }
  return Promise.resolve({ applied: false, key: state.portrait });
}

function handleCoreEvent(event) {
  const before = presentation.current();
  if (event.type === "lifecycle" && event.generationId !== before.generationId) {
    portraitController.beginGeneration(event.generationId);
    renderedPortrait = null;
  }
  const result = presentation.reduce(event);
  if (!result.applied) return;
  if (before.phase === "thinking" && result.state.phase !== "thinking") waitingIndicator.stop();
  if (result.state.phase === "reconnecting" || (before.phase === "typing" && result.state.phase !== "typing")) {
    typewriter.cancel(result.state.bubbleText);
  }
  render(result.state);
  if (event.type === "chat.started" && result.state.phase === "thinking") waitingIndicator.start();
  if (event.type === "chat.completed" && result.state.phase === "typing") typewriter.start(result.state.segments);
}

const chatClient = createRealChatClient({
  invoke,
  listen: (eventName, handler) => window.__TAURI__.event.listen(eventName, handler),
  onEvent: handleCoreEvent,
  initialPreparedGenerationId: characterPresentation.generationId,
  prepareGeneration: ({ generationId }) => rebindCoreGeneration(generationId),
});

async function submitMessage({ text }) {
  const state = presentation.current();
  if (presentationUnavailable || state.canCancel || state.lifecycle !== "ready") return;
  typewriter.cancel("");
  const submittedDraft = input.value;
  try {
    await chatClient.send({ message: text });
    if (input.value === submittedDraft) {
      input.value = "";
      input.lang = "zh-CN";
      adaptiveSurface.resetInput();
    }
  } catch {
    showRecoverableError("消息暂时无法发送，请稍后重试。");
  }
}

for (const dragRegion of dragRegions) {
  dragRegion.addEventListener("pointerdown", async (event) => {
    if (!currentHitRegions) return;
    const hitKind = classifyPointerHit({
      model: currentHitRegions,
      point: [event.clientX / contentScale + activeBounds[0], event.clientY / contentScale + activeBounds[1]],
      interactiveTarget: isInteractivePointerEvent(event),
    });
    if (!shouldStartNativeDrag({ hitKind, button: event.button, isPrimary: event.isPrimary })) return;
    clearTextSelection(window.getSelection?.());
    event.preventDefault();
    try {
      await invoke("start_pet_drag", {
        revision: activeSurfaceRevision,
        surfaceX: event.clientX / contentScale + activeBounds[0],
        surfaceY: event.clientY / contentScale + activeBounds[1],
      });
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
  const point = [event.clientX / contentScale + activeBounds[0], event.clientY / contentScale + activeBounds[1]];
  const hitKind = classifyPointerHit({
    model: currentHitRegions,
    point,
    interactiveTarget: isInteractivePointerEvent(event),
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
    await contextMenu.openAt(event.clientX, event.clientY, manifest, {
      focusFirst: !event.pointerType && event.button === 0,
      surfaceOffset: activeBounds,
      contentScale,
    });
  } catch {
    contextMenu.hide();
    invoke("close_pet_context_menu").catch(() => {});
    showRecoverableError("桌宠菜单暂时无法打开，请稍后重试。");
  }
});

await listenAppEvent("sakura://product-menu-error", () => {
  showRecoverableError("桌宠菜单操作失败，请稍后重试。");
});

await listenAppEvent("sakura://subtitle-language-changed", (event) => {
  const language = event?.payload === "ja" ? "ja" : event?.payload === "zh" ? "zh" : null;
  if (!language) return;
  subtitleLanguage = language;
  const wasTyping = typewriter.isActive();
  typewriter.updateLanguage(language);
  if (!wasTyping) {
    const state = presentation.current();
    const segment = state.replyHistorySegments[state.replyHistoryIndex];
    if (!segment) return;
    const refreshed = presentation.refreshVisibleReply(selectSegmentText(segment, language));
    if (refreshed.applied) render(refreshed.state, { reason: "language", forceEnd: true });
  }
});

let coreRebindRevision = 0;
let coreRebindTarget = "";

async function rebindCoreGeneration(generationId) {
  if (generationId === characterPresentation.generationId) return true;
  if (
    disposed
    || !generationId
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
    if (changes.layout || changes.fonts) adaptiveSurface.invalidate();
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

await listenAppEvent("sakura://character-appearance-changed", async (event) => {
  try {
    const nextAppearance = validateAppearancePublication(event.payload, characterPresentation);
    const changes = appearanceChanges(activeAppearance, nextAppearance);
    activeAppearance = nextAppearance;
    if (changes.theme) applyTheme(activeAppearance.themeTokens);
    if (changes.fonts) applyAppearanceVariables(activeAppearance);
    if (changes.layout) await previewLayoutAppearance();
    else if (changes.fonts) adaptiveSurface.invalidate();
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

await listenAppEvent("sakura://chat-presentation-timing-changed", (event) => {
  const values = event?.payload;
  if (
    !Number.isSafeInteger(values?.subtitleTypingIntervalMs)
    || !Number.isSafeInteger(values?.replySegmentPauseMs)
  ) return;
  chatTiming = Object.freeze(values);
  typewriter.updateTiming({
    intervalMs: values.subtitleTypingIntervalMs,
    segmentPauseMs: values.replySegmentPauseMs,
  });
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
  adaptiveSurface.schedule();
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
  if (state.canCancel) void chatClient.cancel(state.operationId);
  else if (state.canRetry) {
    invoke("retry_core").catch(() => showRecoverableError("Core 重试请求失败，请稍后再试。"));
  }
  else inputFocus.submit("button");
});
function reviewReplyBy(offset) {
  const state = presentation.current();
  const targetIndex = state.replyHistoryIndex + offset;
  const segment = state.replyHistorySegments[targetIndex];
  if (!segment) return;
  const result = presentation.reviewReplyAt(targetIndex, selectSegmentText(segment, subtitleLanguage));
  if (result.applied) render(result.state, { reason: "history", forceEnd: true });
}
replyHistoryPrevious.addEventListener("click", () => reviewReplyBy(-1));
replyHistoryNext.addEventListener("click", () => reviewReplyBy(1));
window.addEventListener("focus", () => inputFocus.handleWindowFocus());
window.addEventListener("blur", () => inputFocus.handleWindowBlur());
document.addEventListener("visibilitychange", () => inputFocus.handleVisibility(document.visibilityState === "visible"));

function dispose() {
  if (disposed) return;
  disposed = true;
  composerActionIndicator.dispose();
  coreRebindRevision += 1;
  coreRebindTarget = "";
  layoutPreviewRevision += 1;
  cancelLayoutPreviewTimer();
  for (const unlisten of appEventUnlisteners.splice(0)) {
    try {
      Promise.resolve(unlisten()).catch(() => {});
    } catch {
      // The native host may already be gone during WebView teardown.
    }
  }
  typewriter.dispose();
  waitingIndicator.dispose();
  bubbleScroll.dispose();
  adaptiveSurface.dispose();
  portraitController.dispose();
  chatClient.dispose();
  contextMenu.dispose();
}

window.addEventListener("beforeunload", dispose, { once: true });

portraitController.beginGeneration(characterPresentation.generationId);
renderedPortrait = characterPresentation.defaultPortraitKey;
if (presentationUnavailable) {
  portraitFallback.hidden = false;
  syncPortraitAppearance(characterPresentation.defaultPortraitKey);
} else {
  await portraitController.show(characterPresentation.defaultPortraitKey, {
    immediate: true,
    generation: characterPresentation.generationId,
  });
}
render(presentation.current());
await chatClient.start();
await waitForRuntimeFonts();
await adaptiveSurface.refresh();
document.body.dataset.shellState = presentationUnavailable ? "presentation-failed" : "product-ready";
await invoke("reveal_pet_window");
if (!presentationUnavailable) {
  const greeting = presentation.beginGreeting();
  if (greeting.applied) {
    render(greeting.state);
    typewriter.start(greeting.state.segments);
  }
}
