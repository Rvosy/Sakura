import { composerPlaceholder, createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createTtsController } from "./audio/tts-controller.js";
import { createComposerActionIndicator } from "./chat/composer-action-indicator.js";
import { createComposerToolRegistry } from "./chat/composer-tool-dock.js";
import { createRealChatClient } from "./chat/real-chat-client.js";
import { createScreenAttachmentController } from "./chat/screen-attachment-controller.js";
import { createScreenAwarenessController } from "./chat/screen-awareness-controller.js";
import { createUpdateAnnouncementController } from "./chat/update-announcement-controller.js";
import { createWaitingIndicator } from "./chat/waiting-indicator.js";
import { waitForRuntimeFonts } from "./core/font-loader.js";
import { installDevtoolsShortcutGuard } from "./core/devtools-guard.js";
import { createInteractionLatencyTracer } from "./core/interaction-latency.js";
import { createRuntimeDiagnostics } from "./core/runtime-diagnostics.js";
import { applyTheme } from "./core/theme.js";
import {
  appearanceChanges,
  applyAppearanceVariables,
  constrainedPortraitScale,
  createAppearanceMutationGuard,
  validateAppearancePublication,
} from "./pet/appearance.js";
import {
  BUBBLE_MOTION_DURATION_MS,
  COMPOSER_MOTION_DURATION_MS,
  composerStagingHeight,
  createAdaptiveControlSurface,
} from "./pet/adaptive-control-surface.js";
import { createBubbleScroll } from "./pet/bubble-scroll.js";
import {
  loadCurrentCharacterPresentation,
  portraitSequence,
  validateCharacterPresentation,
} from "./pet/character-presentation.js";
import { rebindCharacterPresentation } from "./pet/character-generation.js";
import { PetContextMenu } from "./pet/context_menu.js";
import {
  classifyPointerHit,
  clearTextSelection,
  computeHitRegions,
  shouldOpenProductMenu,
  shouldStartNativeDrag,
} from "./pet/hit-regions.js";
import { createInputFocusController } from "./pet/input-focus.js";
import {
  createInputPresentationQueue,
  inputVisualEffectFallbackNotice,
} from "./pet/input-visual-effect.js";
import {
  createLayoutController,
  runInitialLayoutWithBootstrapRecovery,
} from "./pet/layout-controller.js";
import {
  isNativePetDragPointRejected,
  startNativePetDragWithRevisionRecovery,
} from "./pet/native-drag.js";
import {
  applyBootstrapPetLayout,
  applyPetLayout,
  computePetLayout,
  normalizeLayoutAdjustments,
  PRODUCT_LAYOUT_STATE,
  validateLayoutContract,
} from "./pet/layout.js";
import {
  createCharacterVisualPreviewSessionController,
  restoreCommittedCharacterVisual,
} from "./pet/character-visual-preview.js";
import { inferTextLanguage, renderMultilingualText } from "./pet/multilingual-text.js";
import { createPortraitController } from "./pet/portrait-controller.js";
import {
  createSurfaceHoverTracker,
  createSurfaceVisibilityController,
  SURFACE_VISIBILITY_FADE_MS,
  waitForSurfaceFadeCompletion,
} from "./pet/surface-visibility.js";
import { createTypewriter, selectSegmentText } from "./pet/typewriter.js";
import { isChatReadyLifecycle } from "./lifecycle.js";

const MANUAL_SCREENSHOT_DEFAULT_TEXT = "请根据我框选的截图继续对话。";
const LAYOUT_DEGRADED_NOTICE = "窗口布局已恢复到安全模式；后续布局成功后会自动恢复。";

installDevtoolsShortcutGuard();

const nativeInvoke = window.__TAURI__.core.invoke;
const runtimeDiagnostics = createRuntimeDiagnostics({ invoke: nativeInvoke });
const invoke = runtimeDiagnostics.invoke;
const interactionLatencyEnabled = await invoke("interaction_latency_diagnostics_enabled")
  .catch(() => false);
const interactionLatencyTrace = createInteractionLatencyTracer({
  source: "main",
  invoke,
  enabled: interactionLatencyEnabled,
});
const appearanceMutationGuard = createAppearanceMutationGuard();
const inputVisualEffect = await invoke("input_visual_effect_status").catch(() => ({
  initialized: false,
  effectiveMode: "solid",
  outcome: "unavailable",
  errorCode: "INPUT_VISUAL_EFFECT_STATUS_UNAVAILABLE",
}));
document.documentElement.dataset.inputVisualEffect = inputVisualEffect.effectiveMode || "solid";

function tracedInteractionInvoke(command, args, context, stage) {
  if (interactionLatencyTrace.enabled && context) {
    return interactionLatencyTrace.tracedInvoke(command, args, context, stage);
  }
  return invoke(command, args);
}

let interactionPaintProbeFrame = null;
let interactionPaintProbe = null;
function scheduleInteractionPaintProbe(kind, context) {
  if (!interactionLatencyTrace.enabled || !context) return;
  interactionPaintProbe = { kind, context };
  if (interactionPaintProbeFrame !== null) return;
  interactionPaintProbeFrame = window.requestAnimationFrame(() => {
    const first = interactionPaintProbe;
    interactionLatencyTrace.mark(`${first.kind}.paint-raf`, first.context);
    interactionPaintProbeFrame = window.requestAnimationFrame(() => {
      interactionPaintProbeFrame = null;
      const latest = interactionPaintProbe;
      interactionLatencyTrace.mark(`${latest.kind}.paint-opportunity`, latest.context);
    });
  });
}

const stage = document.querySelector("#pet-stage");
const chatBubble = document.querySelector("#chat-bubble");
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
const attachmentToggle = document.querySelector("#composer-attachment");
const attachmentList = document.querySelector("#composer-attachments");
const attachmentMenu = document.querySelector("#composer-tool-dock");
const composerToolList = document.querySelector("#composer-tool-list");
const captureScreen = document.querySelector("#capture-screen");
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
let activeBounds = [0, 0, 900, 1112];
let activeSurfaceRevision = 0;
let currentHitRegions = null;
let currentPortraitSourceSize = null;
let renderedPortrait = null;
let disposed = false;
let presentationUnavailable = false;
let layoutDegraded = false;
let activeAppearance = null;
const appEventUnlisteners = [];
const surfaceVisibility = { bubbleVisible: true, inputVisible: true };
const surfaceVisibilityRevision = { bubble: 0, input: 0 };
let surfaceVisibilityController = null;
let surfaceHoverTracker = null;
let surfaceVisibilityCommitQueue = Promise.resolve();
chatBubble.dataset.surfaceVisible = "true";
composer.dataset.surfaceVisible = "true";

async function initialSessionBlocker() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const publication = await invoke("runtime_lifecycle_snapshot");
      const readiness = publication?.snapshot?.readiness;
      if (["ready", "degraded"].includes(readiness)) return false;
      if (["setup_required", "failed"].includes(readiness)) return true;
    } catch {
      // Core startup can briefly publish without a complete Snapshot.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  return false;
}

const sessionBlockedAtStartup = await initialSessionBlocker();
const composerActionIndicator = createComposerActionIndicator({
  svg: cancelIcon,
  shape: cancelShape,
  prefersReducedMotion: () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
});

let lastInputVisualEffectFallback = "";
let inputVisualEffectFallbackActive = false;
let activeInputVisualEffectFallbackNotice = "";

async function applyInputVisualEffect(values) {
  const status = await invoke("apply_input_visual_effect", { values }).catch(() => ({
    initialized: false,
    effectiveMode: "solid",
    outcome: "degraded",
    errorCode: "INPUT_VISUAL_EFFECT_APPLY_FAILED",
  }));
  document.documentElement.dataset.inputVisualEffect = ["gaussian_blur", "liquid_glass"]
    .includes(status.effectiveMode)
    ? status.effectiveMode
    : "solid";
  const notice = inputVisualEffectFallbackNotice(values, status);
  const previousNotice = activeInputVisualEffectFallbackNotice;
  inputVisualEffectFallbackActive = Boolean(notice);
  activeInputVisualEffectFallbackNotice = notice;
  const fallbackKey = notice
    ? `${values?.visualEffectMode || "unknown"}:${status.errorCode || status.outcome || "unknown"}`
    : "";
  if (notice && fallbackKey !== lastInputVisualEffectFallback) {
    lastInputVisualEffectFallback = fallbackKey;
    showRecoverableError(notice);
  } else if (!notice) {
    lastInputVisualEffectFallback = "";
    if (previousNotice && presentationError.textContent === previousNotice) {
      clearRecoverableError();
    }
  }
  return status;
}

const contextMenu = new PetContextMenu({
  menu: contextMenuElement,
  invoke,
  onError: (message) => showRecoverableError(message),
  beforeSurfaceResize: () => {
    inputFocus.dismissFocus();
    input.blur();
  },
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

function currentSurfaceOffset() {
  const x = Number(stage.dataset.surfaceX);
  const y = Number(stage.dataset.surfaceY);
  return [Number.isFinite(x) ? x : activeBounds[0], Number.isFinite(y) ? y : activeBounds[1]];
}

function canonicalPointerPoint(event) {
  const [surfaceX, surfaceY] = currentSurfaceOffset();
  return [event.clientX / contentScale + surfaceX, event.clientY / contentScale + surfaceY];
}

let screenAttachment;
const inputFocus = createInputFocusController({
  focusInput: () => window.requestAnimationFrame(() => input.focus({ preventScroll: true })),
  readText: () => input.value,
  emptySubmissionText: () => (
    screenAttachment?.attachmentId() ? MANUAL_SCREENSHOT_DEFAULT_TEXT : ""
  ),
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
    request.visibility,
  ),
  applyNativeLayout: ({ revision, layout, interactionTrace: traceContext }) => tracedInteractionInvoke(
    "apply_pet_layout",
    {
      state: PRODUCT_LAYOUT_STATE,
      revision,
      controlSurface: {
        bubbleRect: layout.bubbleRect,
        inputRect: layout.inputRect,
        controlsRect: layout.controlsRect,
        bubbleVisible: layout.bubbleVisible,
        inputVisible: layout.inputVisible,
      },
      inputTransition: productLayout?.inputRect?.[1] === layout.inputRect[1]
        && productLayout?.inputRect?.[2] === layout.inputRect[2]
        && productLayout?.inputRect?.[3] !== layout.inputRect[3]
        ? {
          durationMs: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? 0
            : COMPOSER_MOTION_DURATION_MS,
          stagingHeight: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? null
            : composerStagingHeight({
              beforeHeight: productLayout.inputRect[3],
              afterHeight: layout.inputRect[3],
              baseHeight: contract.controlPanel.inputBaseHeight,
              toolbarHeight: contract.controlPanel.inputToolbarHeight,
              expandedGap: contract.controlPanel.inputExpandedGap,
            }),
        }
        : null,
      bubbleAutoExpand: activeAppearance?.bubbleAutoExpand === true,
      bubbleTransition: activeAppearance?.bubbleAutoExpand === true
        && productLayout?.inputRect?.join(",") === layout.inputRect.join(",")
        && productLayout?.bubbleRect?.[0] === layout.bubbleRect[0]
        && productLayout?.bubbleRect?.[2] === layout.bubbleRect[2]
        && productLayout?.bubbleRect?.[1] + productLayout?.bubbleRect?.[3]
          === layout.bubbleRect[1] + layout.bubbleRect[3]
        && productLayout?.bubbleRect?.[3] !== layout.bubbleRect[3]
        ? {
          durationMs: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? 0
            : BUBBLE_MOTION_DURATION_MS,
          stagingHeight: null,
        }
        : null,
    },
    traceContext,
    "layout.apply-native",
  ),
  // Explicit settings gestures paint inside the already-stable Windows backing envelope before
  // the final native region commit. Ordinary adaptive layout still commits through Rust first.
  previewLayout: (layout, metadata = {}) => {
    productLayout = layout;
    applyPetLayout(stage, layout, contentScale, activeBounds);
    if (metadata.deferNative === true) {
      scheduleControlSurfaceGlassPreview(layoutPreviewRevision, layout);
    }
    interactionLatencyTrace.mark("layout.css-commit", metadata.interactionTrace);
    scheduleInteractionPaintProbe("layout", metadata.interactionTrace);
    currentHitRegions = computeHitRegions(layout, {
      portraitSourceSize: currentPortraitSourceSize,
      portraitScalePercent: activeAppearance?.portraitScalePercent ?? 100,
    });
  },
  commitLayout: (layout, result, metadata = {}) => {
    contentScale = result.contentScale;
    activeBounds = result.activeBounds;
    activeSurfaceRevision = result.revision;
    productLayout = layout;
    applyPetLayout(stage, layout, contentScale, activeBounds);
    interactionLatencyTrace.mark("layout.native-css-commit", metadata.interactionTrace);
    scheduleInteractionPaintProbe("layout", metadata.interactionTrace);
    currentHitRegions = computeHitRegions(layout, {
      portraitSourceSize: currentPortraitSourceSize,
      portraitScalePercent: activeAppearance?.portraitScalePercent ?? 100,
    });
    if (!layoutInitialized) {
      layoutInitialized = true;
      inputFocus.setPresentation(PRODUCT_LAYOUT_STATE);
    }
    if (layoutDegraded) {
      layoutDegraded = false;
      if (presentationError.textContent === LAYOUT_DEGRADED_NOTICE) clearRecoverableError();
    }
  },
});
const initialLayout = await runInitialLayoutWithBootstrapRecovery({
  transition: () => layoutController.transition(PRODUCT_LAYOUT_STATE, "fixed-product-shell"),
  readBootstrapDiagnostics: () => invoke("current_pet_surface_diagnostics"),
  restoreBootstrap: (diagnostics) => applyBootstrapPetLayout(stage, productLayout, diagnostics),
});
if (initialLayout.degraded) {
  const { bootstrap, diagnostics } = initialLayout;
  contentScale = bootstrap.contentScale;
  activeBounds = [...bootstrap.activeBounds];
  activeSurfaceRevision = bootstrap.revision;
  currentHitRegions = computeHitRegions(productLayout, {
    portraitSourceSize: currentPortraitSourceSize,
    portraitScalePercent: activeAppearance?.portraitScalePercent ?? 100,
  });
  layoutInitialized = true;
  inputFocus.setPresentation(PRODUCT_LAYOUT_STATE);
  layoutDegraded = true;
  showRecoverableError(LAYOUT_DEGRADED_NOTICE);
  const work = diagnostics.physicalWorkArea || {};
  runtimeDiagnostics.record({
    level: "warn",
    event: "webview.command.failed",
    command: "apply_pet_layout",
    outcome: "failed",
    code: "PET_LAYOUT_BOOTSTRAP_RECOVERED",
    revision: bootstrap.revision,
    diagnostic: [
      `work=${work.width || 0}x${work.height || 0}`,
      `dpi=${Number(diagnostics.dpiScale || 0).toFixed(3)}`,
      `fit=${(diagnostics.visibleFitBounds || []).join("x")}`,
      `backing=${(diagnostics.residentBackingBounds || []).join("x")}`,
    ].join(";"),
  });
}

let characterPresentation;
try {
  characterPresentation = await loadCurrentCharacterPresentation({
    invoke,
    attempts: sessionBlockedAtStartup ? 1 : 160,
  });
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
  bubbleAutoExpand: false,
  controlPanelVerticalOffset: 0,
  inputBarOffset: 0,
  speechFontSize: 19,
  nameFontSize: 13,
  inputFontSize: 15,
  visualEffectMode: "gaussian_blur",
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
let characterVisualPreviewActive = false;
const characterVisualPreviewSessions = createCharacterVisualPreviewSessionController({
  currentCoreGenerationId: () => characterPresentation.generationId,
  blocked: () => disposed || Boolean(coreRebindTarget),
});
applyTheme(activeAppearance.themeTokens);
applyAppearanceVariables(activeAppearance);
await applyInputVisualEffect(activeAppearance);
characterName.textContent = characterPresentation.displayName;
input.placeholder = composerPlaceholder(characterPresentation.displayName, "ready");
portraitFallbackName.textContent = characterPresentation.displayName;
portrait.setAttribute("aria-label", `${characterPresentation.displayName} 的立绘，可拖动窗口`);
portraitCurrent.alt = `${characterPresentation.displayName} 立绘`;
if (!presentationUnavailable && !inputVisualEffectFallbackActive) clearRecoverableError();

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
let portraitTransitionPending = false;
let portraitSurfaceMutationDepth = 0;
let portraitScaleGestureActive = false;
let portraitScaleGestureReady = Promise.resolve(null);
let portraitScaleGestureTrace = null;
let portraitScaleHitFrameRunning = false;
let pendingPortraitScaleHitFrame = null;
let layoutGestureActive = false;
let layoutPreviewSessionActive = false;
let settingsAppearanceActive = false;
let layoutGestureReady = Promise.resolve(null);
let layoutGestureTrace = null;
let layoutPreviewTimer = null;
let layoutPreviewRevision = initialLayoutRevision;
let controlSurfaceGlassPreviewPending = null;
let controlSurfaceGlassPreviewRunning = false;
let controlSurfaceGlassPreviewDrain = Promise.resolve();
let controlSurfaceGlassPreviewKey = "";
const LAYOUT_PREVIEW_SETTLE_MS = 120;

function controlSurfaceFromLayout(layout) {
  return Object.freeze({
    bubbleRect: layout.bubbleRect,
    inputRect: layout.inputRect,
    controlsRect: layout.controlsRect,
    bubbleVisible: layout.bubbleVisible,
    inputVisible: layout.inputVisible,
  });
}

async function drainControlSurfaceGlassPreviews() {
  if (controlSurfaceGlassPreviewRunning) return;
  controlSurfaceGlassPreviewRunning = true;
  try {
    while (controlSurfaceGlassPreviewPending) {
      const candidate = controlSurfaceGlassPreviewPending;
      controlSurfaceGlassPreviewPending = null;
      if (candidate.previewRevision !== layoutPreviewRevision) continue;
      const ready = await layoutGestureReady;
      if (
        !ready
        || ready.revision !== candidate.previewRevision
        || candidate.previewRevision !== layoutPreviewRevision
      ) continue;
      try {
        await invoke("preview_pet_control_surface", candidate);
      } catch {
        // Lightweight glass frames are latest-wins; the final full layout remains authoritative.
      }
    }
  } finally {
    controlSurfaceGlassPreviewRunning = false;
  }
}

function scheduleControlSurfaceGlassPreview(previewRevision, layout) {
  const controlSurface = controlSurfaceFromLayout(layout);
  const key = JSON.stringify(controlSurface);
  if (key === controlSurfaceGlassPreviewKey) return;
  controlSurfaceGlassPreviewKey = key;
  controlSurfaceGlassPreviewPending = Object.freeze({
    previewRevision,
    controlSurface,
  });
  if (controlSurfaceGlassPreviewRunning) return;
  controlSurfaceGlassPreviewDrain = drainControlSurfaceGlassPreviews();
}

async function flushControlSurfaceGlassPreviews() {
  await controlSurfaceGlassPreviewDrain;
}

function gestureEventPayload(payload) {
  if (typeof payload === "boolean") return Object.freeze({ active: payload, trace: null });
  if (!payload || typeof payload !== "object" || typeof payload.active !== "boolean") {
    return Object.freeze({ active: null, trace: null });
  }
  return Object.freeze({ active: payload.active, trace: payload.trace || null });
}

function portraitFrameEventPayload(payload) {
  if (Number.isSafeInteger(payload)) {
    return Object.freeze({ portraitScalePercent: payload, trace: null });
  }
  if (!payload || typeof payload !== "object") {
    return Object.freeze({ portraitScalePercent: null, trace: null });
  }
  return Object.freeze({
    portraitScalePercent: payload.portraitScalePercent,
    trace: payload.trace || null,
  });
}

function cancelLayoutPreviewTimer() {
  if (layoutPreviewTimer !== null) window.clearTimeout(layoutPreviewTimer);
  layoutPreviewTimer = null;
}

function beginLayoutPreviewSession(traceContext = null) {
  const revision = ++layoutPreviewRevision;
  cancelLayoutPreviewTimer();
  layoutPreviewSessionActive = true;
  stage.dataset.layoutPreview = "active";
  layoutGestureReady = Promise.resolve()
    .then(() => screenAttachment.close())
    .then(() => tracedInteractionInvoke(
      "begin_control_surface_preview",
      { revision },
      traceContext,
      "layout.begin-preview",
    ))
    .then(() => {
      if (disposed || revision !== layoutPreviewRevision) return null;
      return Object.freeze({ revision, trace: traceContext });
    })
    .catch(() => {
      if (!disposed && revision === layoutPreviewRevision) {
        layoutPreviewSessionActive = false;
        delete stage.dataset.layoutPreview;
        showRecoverableError("桌宠布局实时预览暂时不可用。");
      }
      return null;
    });
  return Object.freeze({ revision, ready: layoutGestureReady });
}

async function endLayoutPreviewSession(revision, ready, traceContext = null) {
  const preview = await ready;
  if (!preview || disposed || preview.revision !== revision || revision !== layoutPreviewRevision) return;
  await flushControlSurfaceGlassPreviews();
  if (disposed || revision !== layoutPreviewRevision || layoutGestureActive) return;
  adaptiveSurface.invalidate({
    visualPreview: true,
    forceNative: true,
    interactionTrace: traceContext,
  });
  await adaptiveSurface.flush({
    visualPreview: true,
    forceNative: true,
    interactionTrace: traceContext,
  });
  if (disposed || revision !== layoutPreviewRevision || layoutGestureActive) return;
  await tracedInteractionInvoke(
    "end_control_surface_preview",
    { revision },
    traceContext,
    "layout.end-preview",
  );
  if (revision === layoutPreviewRevision) {
    layoutPreviewSessionActive = false;
    delete stage.dataset.layoutPreview;
  }
}

async function settleLayoutPreview(revision) {
  layoutPreviewTimer = null;
  await adaptiveSurface.flush();
  if (disposed || revision !== layoutPreviewRevision) return;
  try {
    await invoke("end_control_surface_preview", { revision });
  } catch {
    showRecoverableError("桌宠裁剪区域恢复失败；再次调整布局可重试。");
    return;
  }
  if (revision === layoutPreviewRevision) {
    layoutPreviewSessionActive = false;
    delete stage.dataset.layoutPreview;
  }
}

async function previewLayoutAppearance() {
  const { revision, ready } = beginLayoutPreviewSession();
  if (!await ready) {
    if (revision === layoutPreviewRevision) adaptiveSurface.invalidate();
    return;
  }
  adaptiveSurface.invalidate({ visualPreview: true });
  layoutPreviewTimer = window.setTimeout(
    () => void settleLayoutPreview(revision),
    LAYOUT_PREVIEW_SETTLE_MS,
  );
}

function syncPortraitAppearance(
  key,
  presentation = characterPresentation,
  portraitScalePercent = activeAppearance.portraitScalePercent,
  traceContext = null,
) {
  const metadata = presentation.portraitMetadata[key]
    || presentation.portraitMetadata[presentation.defaultPortraitKey];
  const portraitSourceSize = [metadata.width, metadata.height];
  currentPortraitSourceSize = portraitSourceSize;
  const scale = constrainedPortraitScale({
    requestedPercent: portraitScalePercent,
    sourceSize: portraitSourceSize,
    portraitRect: productLayout.portraitRect,
    windowSize: productLayout.windowSize,
  });
  stage.style.setProperty("--portrait-render-scale", String(scale));
  interactionLatencyTrace.mark("portrait.css-commit", traceContext);
  scheduleInteractionPaintProbe("portrait", traceContext);
  currentHitRegions = computeHitRegions(productLayout, {
    portraitSourceSize,
    portraitScalePercent,
  });
}

function commitSurfaceApplication(surface) {
  contentScale = surface.contentScale;
  activeBounds = surface.activeBounds;
  activeSurfaceRevision = surface.revision;
  applyPetLayout(stage, productLayout, contentScale, activeBounds);
}

async function runPortraitSurfaceMutation(mutation) {
  portraitSurfaceMutationDepth += 1;
  try {
    await contextMenu.dismissForSurfaceTransition();
    return await mutation();
  } finally {
    portraitSurfaceMutationDepth = Math.max(0, portraitSurfaceMutationDepth - 1);
  }
}

function waitForPortraitPaint() {
  // requestAnimationFrame callbacks run before their frame is painted. Yield a second
  // frame so the new image, stage offset, and cross-fade have crossed one compositor
  // paint before the native transition transaction is allowed to finish.
  return new Promise((resolve) => window.requestAnimationFrame(
    () => window.requestAnimationFrame(resolve),
  ));
}

function activatePortraitHitTest(
  key,
  revision = ++portraitHitRevision,
  traceContext = null,
  {
    portraitScalePercent = activeAppearance.portraitScalePercent,
    portraitResourceId = null,
    reportError = true,
  } = {},
) {
  return tracedInteractionInvoke(
    "activate_portrait_hit_test",
    {
      portraitKey: key,
      revision,
      portraitScalePercent,
      ...(portraitResourceId ? { portraitResourceId } : {}),
    },
    traceContext,
    "portrait.activate-hit-test",
  ).then((surface) => {
    if (!surface || revision !== portraitHitRevision) return null;
    commitSurfaceApplication(surface);
    return surface;
  }).catch((error) => {
    if (reportError) {
      showRecoverableError("桌宠透明区域穿透暂时不可用。", { autoHide: true });
    }
    throw error;
  });
}

async function drainPortraitScaleHitFrames() {
  if (portraitScaleHitFrameRunning) return;
  portraitScaleHitFrameRunning = true;
  while (pendingPortraitScaleHitFrame) {
    const frame = pendingPortraitScaleHitFrame;
    pendingPortraitScaleHitFrame = null;
    if (
      disposed
      || !portraitScaleGestureActive
      || frame.ready !== portraitScaleGestureReady
    ) continue;
    const revision = ++portraitHitRevision;
    const traceContext = interactionLatencyTrace.atRevision(frame.trace, revision);
    try {
      await activatePortraitHitTest(frame.key, revision, traceContext, {
        portraitScalePercent: frame.portraitScalePercent,
        reportError: false,
      });
    } catch {
      // The stable macOS envelope is already visible. A newer queued frame or the reliable
      // gesture-end transaction replaces this transient hit-router update.
    }
  }
  portraitScaleHitFrameRunning = false;
}

function enqueuePortraitScaleHitFrame(key, portraitScalePercent, trace, ready) {
  pendingPortraitScaleHitFrame = { key, portraitScalePercent, trace, ready };
  void drainPortraitScaleHitFrames();
}

async function previewPortraitScale(key) {
  await runPortraitSurfaceMutation(async () => {
    const revision = ++portraitHitRevision;
    const preview = await invoke("begin_portrait_scale_preview", { revision });
    if (!preview || revision !== portraitHitRevision) return;
    if (preview.application) commitSurfaceApplication(preview.application);
    await activatePortraitHitTest(key, revision);
    if (revision !== portraitHitRevision) return;
    syncPortraitAppearance(key);
  });
}

function buildPortraitController(boundPresentation, {
  preserveFrameOnFailure = false,
  getPortraitScalePercent = () => activeAppearance.portraitScalePercent,
} = {}) {
  const expectedByUrl = expectedPortraitsByUrl(boundPresentation);
  return createPortraitController({
    assets: boundPresentation.portraitResourceUrls,
    defaultKey: boundPresentation.defaultPortraitKey,
    loadImage: (source) => loadImage(source, expectedByUrl),
    preview: async ({ key, source }) => {
      const revision = ++portraitHitRevision;
      const surface = await runPortraitSurfaceMutation(
        () => invoke("prepare_portrait_transition", { portraitKey: key, revision }),
      );
      if (!surface) return;
      portraitTransitionPending = true;
      commitSurfaceApplication(surface);
      portrait.classList.remove("is-transitioning");
      portraitNext.src = source;
      void portrait.offsetWidth;
      portrait.classList.add("is-transitioning");
    },
    cancelPreview: () => {
      portraitTransitionPending = false;
      portrait.classList.remove("is-transitioning");
      portraitNext.removeAttribute("src");
    },
    commit: async ({ key, source }) => {
      const revision = ++portraitHitRevision;
      const portraitScalePercent = getPortraitScalePercent();
      const surface = await runPortraitSurfaceMutation(
        () => activatePortraitHitTest(key, revision, null, {
          portraitScalePercent,
        }),
      );
      if (!surface || revision !== portraitHitRevision) return;
      portraitCurrent.src = source;
      portrait.classList.remove("is-transitioning");
      portraitNext.removeAttribute("src");
      portraitFallback.hidden = true;
      syncPortraitAppearance(key, boundPresentation, portraitScalePercent);
      const transitionPending = portraitTransitionPending;
      portraitTransitionPending = false;
      if (transitionPending) {
        await waitForPortraitPaint();
        if (revision !== portraitHitRevision) return;
        await runPortraitSurfaceMutation(
          () => invoke("commit_portrait_transition", { revision }),
        );
      }
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

let presentation = createChatPresentationReducer({
  initialMessage: characterPresentation.initialMessage,
  defaultPortraitKey: portraits.default,
  thinkingPortraitKey: portraits.thinking,
  concernedPortraitKey: portraits.concerned,
});
let pendingCharacterGreeting = false;
const bubbleScroll = createBubbleScroll({ viewport: bubbleCopy, renderText: renderMultilingualText });

function surfaceVisibilityKey(kind) {
  if (kind === "bubble") return "bubbleVisible";
  if (kind === "input") return "inputVisible";
  throw new Error("unknown pet surface visibility kind");
}

function surfaceVisibilityElement(kind) {
  return kind === "bubble" ? chatBubble : composer;
}

function waitForSurfaceFade(element) {
  return waitForSurfaceFadeCompletion(element, {
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    setTimer: (callback, delay) => window.setTimeout(callback, delay),
    clearTimer: (handle) => window.clearTimeout(handle),
    requestFrame: (callback) => window.requestAnimationFrame(callback),
  });
}

function surfaceFadeDuration() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? 0
    : SURFACE_VISIBILITY_FADE_MS;
}

const nativeInputPresentationQueue = createInputPresentationQueue({
  isCurrent: (revision) => surfaceVisibilityRevision.input === revision,
  apply: (presented) => invoke("set_pet_input_surface_presented", {
    presented,
    durationMs: surfaceFadeDuration(),
  }),
});

async function setNativeInputPresented(presented, revision) {
  return nativeInputPresentationQueue.schedule(presented, revision);
}

async function commitSurfaceVisibility(kind, key, visible, revision) {
  if (surfaceVisibilityRevision[kind] !== revision) return;
  const previous = surfaceVisibility[key];
  surfaceVisibility[key] = visible;
  adaptiveSurface.invalidate();
  let result = await adaptiveSurface.flush();
  if (!result?.applied && !result?.unchanged && !result?.disposed && !result?.failed) {
    adaptiveSurface.invalidate();
    result = await adaptiveSurface.flush();
  }
  if (result?.disposed) return;
  if (result?.failed || (!result?.applied && !result?.unchanged)) {
    surfaceVisibility[key] = previous;
    if (kind === "input") await setNativeInputPresented(previous, revision);
    if (surfaceVisibilityRevision[kind] === revision) {
      surfaceVisibilityElement(kind).dataset.surfaceVisible = previous ? "true" : "false";
    }
    throw new Error("PET_SURFACE_VISIBILITY_COMMIT_FAILED");
  }
  if (surfaceVisibilityRevision[kind] === revision) {
    const element = surfaceVisibilityElement(kind);
    const nativePresentation = kind === "input" && visible
      ? setNativeInputPresented(true, revision)
      : Promise.resolve();
    element.dataset.surfaceVisible = visible ? "true" : "false";
    await nativePresentation;
    if (surfaceVisibilityRevision[kind] !== revision) return;
  }
}

async function applySurfaceVisibility(kind, visible) {
  const key = surfaceVisibilityKey(kind);
  const next = Boolean(visible);
  const revision = ++surfaceVisibilityRevision[kind];
  if (!next) {
    const element = surfaceVisibilityElement(kind);
    const nativePresentation = kind === "input"
      ? setNativeInputPresented(false, revision)
      : Promise.resolve();
    element.dataset.surfaceVisible = "false";
    try {
      await nativePresentation;
    } catch (error) {
      if (surfaceVisibilityRevision[kind] === revision) {
        element.dataset.surfaceVisible = "true";
      }
      throw error;
    }
    if (surfaceVisibilityRevision[kind] !== revision) return;
    await waitForSurfaceFade(element);
    if (surfaceVisibilityRevision[kind] !== revision) return;
  }
  const commit = surfaceVisibilityCommitQueue.then(
    () => commitSurfaceVisibility(kind, key, next, revision),
  );
  surfaceVisibilityCommitQueue = commit.catch(() => {});
  await commit;
}

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
  startNativeExpansion: ({ targetHeight, stagingHeight, durationMs, startAtUnixMs }) => invoke(
    "start_pet_input_expansion",
    { targetHeight, stagingHeight, durationMs, startAtUnixMs },
  ),
  startNativeTransition: (revision, startAtUnixMs) => invoke(
    "start_pet_input_transition",
    { revision, startAtUnixMs },
  ),
  startNativeBubbleTransition: (revision, startAtUnixMs) => invoke(
    "start_pet_bubble_transition",
    { revision, startAtUnixMs },
  ),
  readAdjustments: () => ({
    controlPanelWidth: activeAppearance.controlPanelWidth,
    bubbleMaxHeight: activeAppearance.bubbleMaxHeight,
    controlPanelVerticalOffset: activeAppearance.controlPanelVerticalOffset,
    inputBarOffset: activeAppearance.inputBarOffset,
  }),
  readBubbleAutoExpand: () => activeAppearance.bubbleAutoExpand,
  readVisibility: () => ({ ...surfaceVisibility }),
});

const composerToolRegistry = createComposerToolRegistry({
  list: composerToolList,
  invoke,
  beforeActivate: () => screenAttachment.close(),
  onError: (message) => showRecoverableError(message, { autoHide: true }),
});

function inputIsPinned() {
  return inputFocus.snapshot().inputFocused
    || input.value.length > 0
    || screenAttachment?.busy() === true;
}

screenAttachment = createScreenAttachmentController({
  composer,
  toggle: attachmentToggle,
  menu: attachmentMenu,
  captureItem: captureScreen,
  attachmentList,
  invoke,
  onError: (message) => showRecoverableError(message, { autoHide: true }),
  onAttachmentsChanged: () => adaptiveSurface.invalidate(),
  onStateChanged: () => surfaceVisibilityController?.setInputPinned(inputIsPinned()),
  beforeOpen: () => composerToolRegistry.refresh(),
  surfaceAnchor: () => "below",
  measureSurface: () => {
    const count = Math.max(1, composerToolList.querySelectorAll(".composer-tool-dock__item").length);
    const [x, y, , height] = productLayout.inputRect;
    return [x, y + height + 12, 216, Math.min(4, count) * 24 + 8];
  },
  openSurface: (rect) => invoke("set_pet_tool_dock_surface", { rect }),
  closeSurface: () => invoke("set_pet_tool_dock_surface", { rect: null }),
});

const phaseLabels = Object.freeze({
  booting: "正在准备",
  ready: "在线",
  thinking: "正在思考",
  typing: "正在回复",
  settled: "在线",
  error: "回复失败",
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

let bubbleAutoHideSettings = Object.freeze({
  autoHideEnabled: true,
  autoHideDelaySeconds: 5,
});
const surfaceVisibilityCapabilities = await invoke("pet_surface_visibility_capabilities")
  .catch(() => ({ bubbleAutoHide: false, inputHoverReveal: false }));
if (surfaceVisibilityCapabilities.bubbleAutoHide && surfaceVisibilityCapabilities.inputHoverReveal) {
  try {
    const persistedBubbleSettings = await invoke("current_bubble_auto_hide");
    if (
      typeof persistedBubbleSettings?.autoHideEnabled === "boolean"
      && Number.isSafeInteger(persistedBubbleSettings?.autoHideDelaySeconds)
    ) bubbleAutoHideSettings = Object.freeze(persistedBubbleSettings);
  } catch {
    // The 0.9.x defaults remain usable when neither Runtime v2 nor legacy settings can be read.
  }

  surfaceVisibilityController = createSurfaceVisibilityController({
    settings: bubbleAutoHideSettings,
    onVisibilityChange: applySurfaceVisibility,
    onError: () => showRecoverableError("桌宠控件显隐更新失败；下次交互会重试。", { autoHide: true }),
  });
  surfaceHoverTracker = createSurfaceHoverTracker({
    onHoverChange: (active) => surfaceVisibilityController.setHoverActive(active),
  });
  for (const [name, element] of [
    ["portrait", portrait],
    ["bubble", chatBubble],
    ["input", composer],
  ]) {
    element.addEventListener("pointerenter", () => surfaceHoverTracker.enter(name));
    element.addEventListener("pointerleave", () => surfaceHoverTracker.leave(name));
  }
  surfaceVisibilityController.setInputPinned(inputIsPinned());
}

const ttsController = createTtsController({
  invoke,
  listen: (eventName, handler) => window.__TAURI__.event.listen(eventName, handler),
  onDiagnostic: (code) => runtimeDiagnostics.record({
    level: "warn",
    event: "webview.tts.degraded",
    outcome: "failed",
    code,
  }),
});
await ttsController.start();

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
    const state = presentation.current();
    if (state.phase === "typing" && state.segments[index] === segment) {
      // Decode the current segment portrait before requesting TTS and keep preparation
      // off-screen, so the visible transition can start at playback-start.
      const portraitReady = portraitController.preload(
        segment.portrait || state.portrait,
        { generation: state.generationId },
      );
      const nextPortrait = state.segments[index + 1]?.portrait;
      if (nextPortrait) {
        void portraitController.preload(nextPortrait, { generation: state.generationId });
      }
      // TTS playback-start is the shared segment boundary. The started hook launches the
      // portrait transition, then typewriter begins the first glyph
      // when the same gate resolves. Portrait commit itself remains asynchronous and native-safe.
      const subtitleReady = portraitReady.then(() => ttsController.beforeSegment(segment, index, {
        onStarted: () => {
          const result = presentation.setTypingSegment(segment, index);
          if (result.applied) void render(result.state);
        },
      }));
      return index === 0
        ? waitingIndicator.stopWhenSettled(subtitleReady)
        : subtitleReady;
    }
    return undefined;
  },
  onSegmentComplete: (_segment, index) => ttsController.afterSegment(index),
  onComplete: () => {
    const result = presentation.finishTyping();
    if (result.applied) render(result.state);
  },
});

const waitingIndicator = createWaitingIndicator({
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  onFrame: (frame) => {
    const result = presentation.setWaitingText(frame);
    if (result.applied) render(result.state);
  },
});

function render(state, bubbleUpdate = {}, { syncBubbleWithPortrait = false } = {}) {
  surfaceVisibilityController?.setPhase(state.phase);
  const portraitChanged = renderedPortrait !== state.portrait;
  let bubbleCommitted = false;
  const commitBubble = () => {
    if (bubbleCommitted) return;
    bubbleCommitted = true;
    bubbleScroll.updateText(state.bubbleText, bubbleUpdate);
    adaptiveSurface.schedule();
  };
  chatPhase.textContent = phaseLabels[state.phase] || "在线";
  if (!characterVisualPreviewActive && (!syncBubbleWithPortrait || !portraitChanged)) {
    commitBubble();
  }
  input.placeholder = composerPlaceholder(characterPresentation.displayName, state.phase);
  send.dataset.action = state.canCancel ? "cancel" : state.canRetry ? "retry" : "send";
  const actionLabel = state.canCancel ? "停止回复" : state.canRetry ? "重试连接" : "发送消息";
  send.setAttribute("aria-label", actionLabel);
  send.title = actionLabel;
  composerActionIndicator.setBusy(state.canCancel);
  input.disabled = presentationUnavailable;
  send.disabled = presentationUnavailable || state.silentInteraction || (
    !state.canRetry
    && !isChatReadyLifecycle(state.lifecycle)
  );
  replyHistoryPrevious.disabled = !state.canReviewPrevious;
  replyHistoryNext.disabled = !state.canReviewNext;
  document.body.dataset.chatState = state.phase;
  stage.dataset.chatState = state.phase;
  if (portraitChanged) {
    renderedPortrait = state.portrait;
    if (characterVisualPreviewActive) {
      return Promise.resolve({ applied: false, key: state.portrait, visualPreview: true });
    }
    return portraitController.show(state.portrait, {
      immediate: portraitCurrent.getAttribute("src") === null,
      generation: state.generationId,
      onVisualReady: commitBubble,
    });
  }
  return Promise.resolve({ applied: false, key: state.portrait });
}

function handleCoreEvent(event) {
  updateAnnouncement.handleChatEvent(event);
  if (["chat.completed", "chat.failed", "chat.cancelled"].includes(event.type)) {
    runtimeDiagnostics.record({
      level: event.type === "chat.failed" ? "warn" : "info",
      event: "webview.chat.terminal",
      outcome: event.type === "chat.completed" ? "completed" : (
        event.type === "chat.cancelled" ? "cancelled" : "failed"
      ),
      operationId: event.operationId,
    });
  }
  const before = presentation.current();
  if (event.type === "lifecycle" && event.generationId !== before.generationId) {
    ttsController.cancel();
    screenAttachment.invalidate();
    screenAwareness.generationChanged(event.generationId);
    updateAnnouncement.generationChanged();
    composerToolRegistry.invalidate();
    portraitController.beginGeneration(event.generationId);
    renderedPortrait = null;
  }
  const result = presentation.reduce(event);
  if (!result.applied) return;
  const waitingForFirstSegment = event.type === "chat.completed" && result.state.phase === "typing";
  if (before.phase === "thinking" && result.state.phase !== "thinking" && !waitingForFirstSegment) {
    waitingIndicator.stop();
  }
  if (before.phase === "typing" && result.state.phase !== "typing") {
    typewriter.cancel(result.state.bubbleText);
  }
  render(result.state);
  if (
    event.type === "lifecycle"
    && pendingCharacterGreeting
    && isChatReadyLifecycle(result.state.lifecycle)
  ) {
    pendingCharacterGreeting = false;
    const greeting = presentation.beginGreeting();
    if (greeting.applied) {
      render(greeting.state);
      typewriter.start(greeting.state.segments);
    }
  }
  if (event.type === "chat.started" && result.state.phase === "thinking") waitingIndicator.start();
  if (event.type === "chat.started" && result.state.phase === "thinking") ttsController.cancel();
  if (event.type === "chat.completed" && result.state.phase === "typing") {
    ttsController.beginReply(event.operationId, result.state.segments);
    typewriter.start(result.state.segments);
  }
}

const chatClient = createRealChatClient({
  invoke,
  listen: (eventName, handler) => window.__TAURI__.event.listen(eventName, handler),
  onEvent: handleCoreEvent,
  initialPreparedGenerationId: characterPresentation.generationId,
  prepareGeneration: ({ generationId }) => rebindCoreGeneration(generationId),
});

const updateAnnouncement = createUpdateAnnouncementController({
  check: () => invoke("startup_update_check"),
  announce: () => chatClient.announceUpdate(),
  isIdle: () => {
    const state = presentation.current();
    return !presentationUnavailable
      && isChatReadyLifecycle(state.lifecycle)
      && !chatClient.isBusy()
      && !state.canCancel
      && !waitingIndicator.active()
      && !typewriter.isActive()
      && input.value === ""
      && stage.dataset.composing !== "true"
      && !screenAttachment.busy();
  },
  onDiagnostic: (event, details) => runtimeDiagnostics.record({
    level: event.endsWith("failed") ? "warn" : "info",
    event,
    outcome: event.endsWith("failed") ? "failed" : "completed",
    ...details,
  }),
});

const screenAwareness = createScreenAwarenessController({
  invoke,
  send: (payload) => chatClient.send({ ...payload, presentation: "silent" }),
  generationId: () => presentation.current().generationId,
  isIdle: () => {
    const state = presentation.current();
    return !presentationUnavailable
      && isChatReadyLifecycle(state.lifecycle)
      && !chatClient.isBusy()
      && !state.canCancel
      && !waitingIndicator.active()
      && !typewriter.isActive()
      && input.value === ""
      && stage.dataset.composing !== "true"
      && !screenAttachment.busy()
      && !updateAnnouncement.isPending();
  },
  onDiagnostic: (event, details) => runtimeDiagnostics.record({
    level: event.endsWith("failed") ? "warn" : "info",
    event,
    outcome: event.endsWith("failed") ? "failed" : "completed",
    ...details,
  }),
});

async function submitMessage({ text }) {
  const state = presentation.current();
  if (presentationUnavailable || chatClient.isBusy() || state.canCancel || !isChatReadyLifecycle(state.lifecycle)) return;
  updateAnnouncement.noteActivity();
  screenAwareness.noteManualSend();
  typewriter.cancel("");
  ttsController.cancel();
  const submittedDraft = input.value;
  const submittedAttachmentId = screenAttachment.attachmentId();
  if (submittedAttachmentId) screenAttachment.setSubmitting(true);
  try {
    const response = await chatClient.send({
      message: text,
      attachmentId: submittedAttachmentId,
    });
    runtimeDiagnostics.record({
      level: "info",
      event: "webview.chat.send",
      outcome: "completed",
      operationId: response.operationId,
    });
    if (input.value === submittedDraft) {
      input.value = "";
      input.lang = "zh-CN";
      adaptiveSurface.resetInput();
      surfaceVisibilityController?.setInputPinned(inputIsPinned());
    }
    screenAttachment.markSent(submittedAttachmentId);
  } catch {
    if (submittedAttachmentId) screenAttachment.setSubmitting(false);
    showRecoverableError("消息暂时无法发送，请稍后重试。");
  }
}

for (const eventName of ["dragstart", "selectstart"]) {
  portrait.addEventListener(eventName, (event) => {
    event.preventDefault();
  }, true);
}

for (const dragRegion of dragRegions) {
  dragRegion.addEventListener("pointerdown", async (event) => {
    if (event.button === 0) surfaceVisibilityController?.activatePet();
    if (!currentHitRegions) return;
    const point = canonicalPointerPoint(event);
    const hitKind = classifyPointerHit({
      model: currentHitRegions,
      point,
      interactiveTarget: isInteractivePointerEvent(event),
    });
    if (!shouldStartNativeDrag({ hitKind, button: event.button, isPrimary: event.isPrimary })) return;
    const dragGesture = interactionLatencyTrace.createGesture("pet-drag");
    const dragTrace = interactionLatencyTrace.atRevision(dragGesture, activeSurfaceRevision);
    const pointerClientPoint = [event.clientX, event.clientY];
    interactionLatencyTrace.mark("pet-drag.pointerdown", dragTrace, { event });
    clearTextSelection(window.getSelection?.());
    event.preventDefault();
    dragRegion.classList.add("is-native-dragging");
    surfaceVisibilityController?.setSuspended(true);
    try {
      await startNativePetDragWithRevisionRecovery({
        revision: activeSurfaceRevision,
        point,
        start: ({ revision, point: nextPoint }) => tracedInteractionInvoke(
          "start_pet_drag",
          {
            revision,
            surfaceX: nextPoint[0],
            surfaceY: nextPoint[1],
          },
          dragTrace,
          "pet-drag.start-native",
        ),
        readSurfaceDiagnostics: () => invoke("current_pet_surface_diagnostics"),
        syncSurface: (diagnostics) => {
          const nextContentScale = Number(diagnostics?.contentScale);
          const nextBounds = diagnostics?.logicalBounds;
          if (
            !Number.isFinite(nextContentScale)
            || nextContentScale <= 0
            || !Array.isArray(nextBounds)
            || nextBounds.length !== 4
            || nextBounds.some((value) => !Number.isSafeInteger(value) || value < 0)
          ) {
            throw new Error("PET_SURFACE_DIAGNOSTICS_INVALID");
          }
          commitSurfaceApplication({
            contentScale: nextContentScale,
            activeBounds: nextBounds,
            revision: diagnostics.revision,
          });
        },
        getPoint: () => {
          const [surfaceX, surfaceY] = currentSurfaceOffset();
          return [
            pointerClientPoint[0] / contentScale + surfaceX,
            pointerClientPoint[1] / contentScale + surfaceY,
          ];
        },
      });
    } catch (error) {
      if (isNativePetDragPointRejected(error)) return;
      showRecoverableError("窗口拖动暂时不可用。");
    } finally {
      dragRegion.classList.remove("is-native-dragging");
      surfaceVisibilityController?.setSuspended(false);
      void interactionLatencyTrace.flush();
    }
  });
}

document.addEventListener("contextmenu", async (event) => {
  if (contextMenu.contains(event.target)) {
    event.preventDefault();
    return;
  }
  if (!currentHitRegions) return;
  if (portraitSurfaceMutationDepth > 0 || portraitScaleGestureActive) {
    event.preventDefault();
    return;
  }
  const point = canonicalPointerPoint(event);
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
    await screenAttachment.close();
    if (portraitSurfaceMutationDepth > 0 || portraitScaleGestureActive) return;
    const manifest = await invoke("open_pet_context_menu", {
      surfaceX: point[0],
      surfaceY: point[1],
    });
    if (portraitSurfaceMutationDepth > 0 || portraitScaleGestureActive) {
      await contextMenu.dismissForSurfaceTransition();
      return;
    }
    await contextMenu.openAt(event.clientX, event.clientY, manifest, {
      focusFirst: !event.pointerType && event.button === 0,
      surfaceOffset: currentSurfaceOffset(),
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
  characterVisualPreviewSessions.invalidate();
  characterVisualPreviewActive = false;
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

    candidateController = buildPortraitController(nextPresentation, {
      preserveFrameOnFailure: true,
      getPortraitScalePercent: () => nextAppearance.portraitScalePercent,
    });
    const visualGeneration = generationId;
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
    characterVisualPreviewActive = false;
    const changes = appearanceChanges(activeAppearance, nextAppearance);
    const presentationRebind = rebindCharacterPresentation({
      currentCharacterId: characterPresentation.characterId,
      nextPresentation,
      currentReducer: presentation,
    });
    if (presentationRebind.characterChanged) {
      waitingIndicator.stop();
      typewriter.cancel("");
      ttsController.cancel();
    }
    presentation = presentationRebind.reducer;
    pendingCharacterGreeting = presentationRebind.greetingPending;
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
    if (changes.theme || changes.visualEffect) await applyInputVisualEffect(activeAppearance);
    if (changes.layout || changes.fonts) adaptiveSurface.invalidate();
    syncPortraitAppearance(renderedPortrait, nextPresentation);
    previousController.dispose();
    if (!inputVisualEffectFallbackActive) clearRecoverableError();
    render(presentation.current());
    return true;
  } catch {
    candidateController?.dispose();
    if (!disposed && revision === coreRebindRevision) {
      showRecoverableError("桌宠资源加载失败；当前画面将继续保留。请稍后重试。");
    }
    return false;
  } finally {
    if (revision === coreRebindRevision) coreRebindTarget = "";
  }
}

await listenAppEvent("sakura://character-visual-preview", async (event) => {
  try {
    const publication = event?.payload;
    const previewToken = characterVisualPreviewSessions.begin(publication);
    if (!previewToken) return;
    const previewRevision = previewToken.revision;
    const previewWindowGeneration = previewToken.windowGeneration;
    const previewCoreGeneration = previewToken.coreGenerationId;
    const previewPresentation = validateCharacterPresentation(publication.presentation);
    if (previewPresentation.generationId !== previewCoreGeneration) return;
    const previewAppearance = validateAppearancePublication(
      publication.appearance,
      previewPresentation,
    );
    const restoringCurrent = previewPresentation.characterId === characterPresentation.characterId;
    const key = restoringCurrent && previewPresentation.portraitKeys.includes(renderedPortrait)
      ? renderedPortrait
      : previewPresentation.defaultPortraitKey;
    const source = previewPresentation.portraitResourceUrls[key];
    await loadImage(source, expectedPortraitsByUrl(previewPresentation));
    if (
      !characterVisualPreviewSessions.isCurrent(previewToken)
    ) return;
    await screenAttachment.close();
    if (
      !characterVisualPreviewSessions.isCurrent(previewToken)
    ) return;
    const nativeRevision = ++portraitHitRevision;
    const preview = await runPortraitSurfaceMutation(
      () => invoke("begin_portrait_scale_preview", { revision: nativeRevision }),
    );
    if (
      !characterVisualPreviewSessions.isCurrent(previewToken)
      || nativeRevision !== portraitHitRevision
    ) return;
    const hitRevision = ++portraitHitRevision;
    const previewSurface = await runPortraitSurfaceMutation(
      () => activatePortraitHitTest(key, hitRevision, null, {
        portraitScalePercent: previewAppearance.portraitScalePercent,
        portraitResourceId: previewPresentation.portraitResourceIds[key],
      }),
    );
    if (
      !characterVisualPreviewSessions.isCurrent(previewToken)
      || hitRevision !== portraitHitRevision
    ) return;
    portraitController.beginGeneration(
      `visual-preview:${previewWindowGeneration}:${previewRevision}`,
    );
    characterVisualPreviewActive = true;
    if (preview?.application) commitSurfaceApplication(preview.application);
    if (previewSurface) commitSurfaceApplication(previewSurface);
    portrait.classList.remove("is-transitioning");
    portraitNext.removeAttribute("src");
    portraitCurrent.src = source;
    portraitFallback.hidden = true;
    bubbleScroll.updateText(previewPresentation.initialMessage, { forceEnd: true });
    adaptiveSurface.schedule();
    syncPortraitAppearance(
      key,
      previewPresentation,
      previewAppearance.portraitScalePercent,
    );
    applyTheme(previewAppearance.themeTokens);
    await applyInputVisualEffect({
      ...activeAppearance,
      themeTokens: previewAppearance.themeTokens,
    });
    if (
      !characterVisualPreviewSessions.isCurrent(previewToken)
    ) return;
    if (restoringCurrent) {
      syncPortraitAppearance(key, characterPresentation, activeAppearance.portraitScalePercent);
      portraitController.beginGeneration(characterPresentation.generationId);
      characterVisualPreviewActive = false;
      await restoreCommittedCharacterVisual({
        currentState: () => presentation.current(),
        resetRenderedPortrait: () => { renderedPortrait = null; },
        render: (state) => render(state, { forceEnd: true }),
      });
      if (!characterVisualPreviewSessions.isCurrent(previewToken)) return;
    }
  } catch {
    showRecoverableError("角色视觉预览失败；已保留当前角色画面。");
  }
});

await listenAppEvent("sakura://control-surface-frame", async (event) => {
  if (!layoutGestureActive || !event.payload || typeof event.payload !== "object") return;
  const frameTrace = event.payload.trace || layoutGestureTrace;
  if (frameTrace) layoutGestureTrace = frameTrace;
  interactionLatencyTrace.mark("layout.frame-event-received", frameTrace);
  const normalized = normalizeLayoutAdjustments(contract, event.payload);
  if (Object.entries(normalized).some(([field, value]) => event.payload[field] !== value)) return;
  appearanceMutationGuard.supersede();
  surfaceVisibilityController?.previewBubble();
  const deferNative = event.payload.deferNative === true;
  // Native region relaxation may take longer than a slider frame on a cold WebView2 surface.
  // Paint inside the already-stable backing envelope immediately; the settings session restores
  // one precise region after all slider gestures are finished.
  if (!deferNative) {
    const ready = await layoutGestureReady;
    if (!ready || ready.revision !== layoutPreviewRevision) return;
  }
  if (!layoutGestureActive || disposed) return;
  activeAppearance = Object.freeze({ ...activeAppearance, ...normalized });
  stage.dataset.layoutPreview = "active";
  adaptiveSurface.invalidate({
    visualPreview: true,
    deferNative,
    interactionTrace: frameTrace,
  });
});

await listenAppEvent("sakura://control-surface-gesture", async (event) => {
  const publication = gestureEventPayload(event.payload);
  if (publication.active === null) return;
  const sourceTrace = publication.trace || layoutGestureTrace;
  interactionLatencyTrace.mark("layout.gesture-event-received", sourceTrace);
  if (publication.active === true) {
    appearanceMutationGuard.supersede();
    surfaceVisibilityController?.previewBubble();
    layoutGestureTrace = sourceTrace;
    layoutGestureActive = true;
    // The settings appearance session already owns one relaxed Windows region. Keep its revision
    // stable across width/height sliders so switching controls cannot trigger a precise-region
    // rebuild between two pointer gestures.
    if (!settingsAppearanceActive || !layoutPreviewSessionActive) {
      const nextRevision = layoutPreviewRevision + 1;
      const beginTrace = interactionLatencyTrace.atRevision(sourceTrace, nextRevision);
      beginLayoutPreviewSession(beginTrace);
    }
    return;
  }

  layoutGestureActive = false;
  const revision = layoutPreviewRevision;
  const endTrace = interactionLatencyTrace.atRevision(sourceTrace, revision);
  layoutGestureTrace = sourceTrace;
  const ready = layoutGestureReady;
  if (settingsAppearanceActive) {
    // The reliable appearance event and the lightweight frame are both latest-wins. Native bounds
    // and the expensive precise mask are committed once when the settings window closes.
    void flushControlSurfaceGlassPreviews().then(() => interactionLatencyTrace.flush());
    return;
  }
  void endLayoutPreviewSession(revision, ready, endTrace).then(() => {
    void interactionLatencyTrace.flush();
  }).catch(() => {
    if (!disposed && revision === layoutPreviewRevision) {
      showRecoverableError("桌宠裁剪区域恢复失败；再次调整布局可重试。");
    }
  });
});

await listenAppEvent("sakura://character-appearance-changed", async (event) => {
  try {
    if (characterVisualPreviewActive) return;
    const nextAppearance = validateAppearancePublication(event.payload, characterPresentation);
    const changes = appearanceChanges(activeAppearance, nextAppearance);
    const layoutPreviewAtPublication = changes.layout
      && (layoutGestureActive || settingsAppearanceActive);
    const mutationRevision = appearanceMutationGuard.begin();
    // Event callbacks are ordered, but their asynchronous preparation is not. Publish the values
    // before waiting so a newer slider frame can supersede them without a late full-object write.
    activeAppearance = nextAppearance;
    if (changes.layout || changes.fonts || changes.theme) {
      surfaceVisibilityController?.previewBubble();
    }
    if (layoutPreviewAtPublication) {
      // Settings flushes its latest lightweight frame before this full publication. Fold the
      // reliable values into the same gesture now; never let its async continuation start a
      // second 120 ms preview after the matching gesture-end event has already arrived.
      adaptiveSurface.invalidate({
        visualPreview: true,
        deferNative: true,
        interactionTrace: layoutGestureTrace,
      });
    }
    if (changes.fonts || changes.portrait || (changes.layout && !layoutPreviewAtPublication)) {
      await screenAttachment.close();
    }
    if (!appearanceMutationGuard.isCurrent(mutationRevision)) return;
    if (changes.theme) applyTheme(activeAppearance.themeTokens);
    if (changes.fonts) applyAppearanceVariables(activeAppearance);
    if (changes.theme || changes.visualEffect) await applyInputVisualEffect(activeAppearance);
    if (!appearanceMutationGuard.isCurrent(mutationRevision)) return;
    if (changes.layout) {
      if (!layoutPreviewAtPublication) await previewLayoutAppearance();
    }
    else if (changes.fonts) adaptiveSurface.invalidate();
    if (changes.portrait) {
      const key = renderedPortrait && characterPresentation.portraitMetadata[renderedPortrait]
        ? renderedPortrait
        : characterPresentation.defaultPortraitKey;
      if (portraitScaleGestureActive) {
        const preview = await portraitScaleGestureReady;
        if (disposed || !preview) return;
        if (!preview.deferredNative) {
          const revision = ++portraitHitRevision;
          const frameTrace = interactionLatencyTrace.atRevision(
            portraitScaleGestureTrace,
            revision,
          );
          await activatePortraitHitTest(key, revision, frameTrace);
          if (!disposed) syncPortraitAppearance(
            key,
            characterPresentation,
            activeAppearance.portraitScalePercent,
            frameTrace,
          );
        } else {
          const frameTrace = portraitScaleGestureTrace;
          syncPortraitAppearance(
            key,
            characterPresentation,
            activeAppearance.portraitScalePercent,
            frameTrace,
          );
          if (!preview.deferredHitRegions) {
            enqueuePortraitScaleHitFrame(
              key,
              activeAppearance.portraitScalePercent,
              frameTrace,
              portraitScaleGestureReady,
            );
          }
        }
      } else {
        await previewPortraitScale(key);
      }
    }
  } catch {
    // Old generation, forged fields, and stale callbacks are ignored deterministically.
  }
});

await listenAppEvent("sakura://settings-appearance-active", (event) => {
  if (typeof event?.payload !== "boolean") return;
  settingsAppearanceActive = event.payload;
  surfaceVisibilityController?.setSettingsAppearanceActive(settingsAppearanceActive);
  if (settingsAppearanceActive) {
    if (!layoutPreviewSessionActive) beginLayoutPreviewSession();
    return;
  }
  layoutGestureActive = false;
  if (!layoutPreviewSessionActive) return;
  const revision = layoutPreviewRevision;
  const ready = layoutGestureReady;
  void endLayoutPreviewSession(revision, ready).catch(() => {
    if (!disposed && revision === layoutPreviewRevision) {
      showRecoverableError("桌宠裁剪区域恢复失败；再次打开设置可重试。");
    }
  });
});

await listenAppEvent("sakura://portrait-scale-frame", async (event) => {
  const publication = portraitFrameEventPayload(event.payload);
  const portraitScalePercent = publication.portraitScalePercent;
  const frameTrace = publication.trace || portraitScaleGestureTrace;
  if (frameTrace) portraitScaleGestureTrace = frameTrace;
  interactionLatencyTrace.mark("portrait.frame-event-received", frameTrace);
  if (
    !portraitScaleGestureActive
    || !Number.isSafeInteger(portraitScalePercent)
    || portraitScalePercent < 50
    || portraitScalePercent > 150
  ) return;
  const ready = portraitScaleGestureReady;
  const preview = await ready;
  if (
    !preview
    || disposed
    || !portraitScaleGestureActive
    || ready !== portraitScaleGestureReady
  ) return;
  const key = renderedPortrait && characterPresentation.portraitMetadata[renderedPortrait]
    ? renderedPortrait
    : characterPresentation.defaultPortraitKey;
  if (preview.deferredNative) {
    syncPortraitAppearance(key, characterPresentation, portraitScalePercent, frameTrace);
    if (!preview.deferredHitRegions) {
      enqueuePortraitScaleHitFrame(key, portraitScalePercent, frameTrace, ready);
    }
    return;
  }
});

await listenAppEvent("sakura://portrait-scale-gesture", async (event) => {
  const publication = gestureEventPayload(event.payload);
  if (publication.active === null) return;
  const sourceTrace = publication.trace || portraitScaleGestureTrace;
  interactionLatencyTrace.mark("portrait.gesture-event-received", sourceTrace);
  if (publication.active === true) {
    await screenAttachment.close();
    pendingPortraitScaleHitFrame = null;
    portraitScaleGestureTrace = sourceTrace;
    portraitScaleGestureActive = true;
    const revision = ++portraitHitRevision;
    const beginTrace = interactionLatencyTrace.atRevision(sourceTrace, revision);
    portraitScaleGestureReady = runPortraitSurfaceMutation(() => tracedInteractionInvoke(
      "begin_portrait_scale_preview",
      { revision },
      beginTrace,
      "portrait.begin-preview",
    ))
      .then((preview) => {
        if (!preview) return null;
        const surface = preview.application;
        if (surface && !disposed && revision === portraitHitRevision) {
          commitSurfaceApplication(surface);
        }
        return Object.freeze({
          revision,
          deferredNative: preview.deferredNative === true,
          deferredHitRegions: preview.deferredHitRegions === true,
          trace: beginTrace,
        });
      })
      .catch(() => {
        if (!disposed && revision === portraitHitRevision) {
          showRecoverableError("桌宠缩放预览暂时不可用。", { autoHide: true });
        }
        return null;
      });
    return;
  }

  portraitScaleGestureActive = false;
  pendingPortraitScaleHitFrame = null;
  const revision = ++portraitHitRevision;
  const endTrace = interactionLatencyTrace.atRevision(sourceTrace, revision);
  portraitScaleGestureTrace = sourceTrace;
  const ready = portraitScaleGestureReady;
  void ready.then(async (preview) => {
    // Appearance events are emitted before the gesture-end event. Yield once so their synchronous
    // publication update wins even when both callbacks were released by the same native command.
    await Promise.resolve();
    if (!preview || disposed || ready !== portraitScaleGestureReady || revision !== portraitHitRevision) return;
    const key = renderedPortrait && characterPresentation.portraitMetadata[renderedPortrait]
      ? renderedPortrait
      : characterPresentation.defaultPortraitKey;
    await runPortraitSurfaceMutation(
      () => activatePortraitHitTest(key, revision, endTrace),
    );
    if (!disposed && revision === portraitHitRevision) {
      syncPortraitAppearance(
        key,
        characterPresentation,
        activeAppearance.portraitScalePercent,
        endTrace,
      );
    }
    void interactionLatencyTrace.flush();
  }).catch(() => {
    if (!disposed && revision === portraitHitRevision) {
      showRecoverableError("桌宠裁剪区域恢复失败；再次调整缩放可重试。", { autoHide: true });
    }
  });
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

await listenAppEvent("sakura://bubble-auto-hide-changed", (event) => {
  const values = event?.payload;
  if (
    typeof values?.autoHideEnabled !== "boolean"
    || !Number.isSafeInteger(values?.autoHideDelaySeconds)
  ) return;
  bubbleAutoHideSettings = Object.freeze(values);
  surfaceVisibilityController?.setSettings(values);
});

await listenAppEvent("sakura://screen-attachment", (event) => {
  if (screenAttachment.handleAttached(event?.payload)) clearRecoverableError();
});
await listenAppEvent("sakura://screen-capture-cancelled", () => {
  screenAttachment.handleCancelled();
});
await listenAppEvent("sakura://screen-capture-error", (event) => {
  screenAttachment.handleError(event?.payload?.message);
});
await listenAppEvent("sakura://screen-awareness-settings", (event) => {
  try {
    screenAwareness.applySettings(event?.payload);
  } catch {
    // Persisted settings remain authoritative and will be loaded on the next startup.
  }
});
await listenAppEvent("sakura://update-preferences-changed", (event) => {
  updateAnnouncement.applyPreferences(event?.payload);
});
input.addEventListener("compositionstart", (event) => {
  updateAnnouncement.noteActivity();
  inputFocus.handleCompositionStart(event.data);
  stage.dataset.composing = "true";
  adaptiveSurface.setComposing(true);
});
input.addEventListener("compositionupdate", (event) => inputFocus.handleCompositionUpdate(event.data));
input.addEventListener("compositionend", (event) => {
  inputFocus.handleCompositionEnd(event.data);
  stage.dataset.composing = "false";
  adaptiveSurface.setComposing(false);
});
input.addEventListener("input", () => {
  updateAnnouncement.noteActivity();
  screenAwareness.noteActivity();
  input.lang = inferTextLanguage(input.value);
  adaptiveSurface.schedule();
  surfaceVisibilityController?.setInputPinned(inputIsPinned());
});
input.addEventListener("focus", () => {
  if (screenAttachment.isOpen()) void screenAttachment.close();
  inputFocus.handleInputFocus();
  surfaceVisibilityController?.setInputPinned(true);
});
input.addEventListener("blur", () => {
  inputFocus.handleInputBlur();
  surfaceVisibilityController?.setInputPinned(inputIsPinned());
});
document.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || screenAttachment.contains(event.target)) return;
  screenAttachment.close();
  inputFocus.dismissFocus();
  input.blur();
}, true);
input.addEventListener("keydown", (event) => {
  updateAnnouncement.noteActivity();
  screenAwareness.noteActivity();
  if (event.key === "Escape" && screenAttachment.isOpen()) {
    event.preventDefault();
    screenAttachment.close({ focus: true });
    return;
  }
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
  if (result.applied) {
    render(
      result.state,
      { reason: "history", forceEnd: true },
      { syncBubbleWithPortrait: true },
    );
  }
}
replyHistoryPrevious.addEventListener("click", () => reviewReplyBy(-1));
replyHistoryNext.addEventListener("click", () => reviewReplyBy(1));
window.addEventListener("focus", () => inputFocus.handleWindowFocus());
window.addEventListener("blur", () => {
  inputFocus.handleWindowBlur();
  input.blur();
  surfaceVisibilityController?.setInputPinned(inputIsPinned());
});
document.addEventListener("visibilitychange", () => inputFocus.handleVisibility(document.visibilityState === "visible"));

function dispose() {
  if (disposed) return;
  disposed = true;
  composerActionIndicator.dispose();
  coreRebindRevision += 1;
  coreRebindTarget = "";
  layoutPreviewRevision += 1;
  portraitHitRevision += 1;
  portraitScaleGestureActive = false;
  layoutGestureActive = false;
  layoutPreviewSessionActive = false;
  settingsAppearanceActive = false;
  if (interactionPaintProbeFrame !== null) window.cancelAnimationFrame(interactionPaintProbeFrame);
  interactionPaintProbeFrame = null;
  interactionPaintProbe = null;
  cancelLayoutPreviewTimer();
  for (const unlisten of appEventUnlisteners.splice(0)) {
    try {
      Promise.resolve(unlisten()).catch(() => {});
    } catch {
      // The native host may already be gone during WebView teardown.
    }
  }
  typewriter.dispose();
  ttsController.dispose();
  waitingIndicator.dispose();
  bubbleScroll.dispose();
  adaptiveSurface.dispose();
  surfaceHoverTracker?.dispose();
  surfaceVisibilityController?.dispose();
  portraitController.dispose();
  chatClient.dispose();
  contextMenu.dispose();
  composerToolRegistry.dispose();
  screenAwareness.dispose();
  updateAnnouncement.dispose();
  runtimeDiagnostics.dispose();
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
surfaceVisibilityController?.start(presentation.current().phase);
render(presentation.current());
await chatClient.start();
try {
  const snapshot = await invoke("settings_screen_awareness_get");
  screenAwareness.applySettings(snapshot?.settings);
  screenAwareness.start();
} catch (error) {
  runtimeDiagnostics.record({
    level: "warn",
    event: "screen_awareness.settings.unavailable",
    outcome: "failed",
    code: String(error || "SCREEN_AWARENESS_SETTINGS_UNAVAILABLE").split("|")[0],
  });
}
await waitForRuntimeFonts();
await adaptiveSurface.refresh();
document.body.dataset.shellState = presentationUnavailable ? "presentation-failed" : "product-ready";
await invoke("reveal_pet_window");
runtimeDiagnostics.markReady();
if (!presentationUnavailable) {
  const greeting = presentation.beginGreeting();
  if (greeting.applied) {
    render(greeting.state);
    typewriter.start(greeting.state.segments);
  }
}
updateAnnouncement.start();
