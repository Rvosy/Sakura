import { composerPlaceholder, createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createTtsController } from "./audio/tts-controller.js";
import { createAsrController } from "./audio/asr-controller.js";
import { createAsrWaveform } from "./audio/asr-waveform.js";
import { createAsrPresentation } from "./audio/asr-presentation.js";
import { createAsrAvailability } from "./audio/asr-availability.js";
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
import { createControlSurfaceTransactions } from "./pet/control-surface-transactions.js";
import {
  loadCurrentCharacterPresentation,
  validateCharacterPresentation,
} from "./pet/character-presentation.js";
import { isNewCharacterGeneration, rebindCharacterPresentation } from "./pet/character-generation.js";
import { PetContextMenu } from "./pet/context_menu.js";
import {
  classifyPointerHit,
  clearTextSelection,
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
  shouldRevealBubbleAfterNativeDrag,
  startNativePetDragWithRevisionRecovery,
} from "./pet/native-drag.js";
import {
  applyBootstrapPetLayout,
  applyPetLayout,
  computePetLayout,
  normalizeLayoutAdjustments,
  PRODUCT_LAYOUT_STATE,
  samePetSurfaceGeometry,
  validateLayoutContract,
} from "./pet/layout.js";
import {
  createCharacterVisualPreviewSessionController,
} from "./pet/character-visual-preview.js";
import { inferTextLanguage, renderMultilingualText } from "./pet/multilingual-text.js";
import { createRendererHost } from "./pet/renderer-host.js";
import { applyVisualSurfaceAppearance } from "./pet/visual-surface.js";
import {
  createSurfaceHoverTracker,
  createSurfaceVisibilityController,
  SURFACE_VISIBILITY_FADE_MS,
  waitForSurfaceFadeCompletion,
  createSurfaceHoverProbe,
} from "./pet/surface-visibility.js";
import { createTypewriter, selectSegmentText } from "./pet/typewriter.js";
import { isChatReadyLifecycle } from "./lifecycle.js";

const MANUAL_SCREENSHOT_DEFAULT_TEXT = "请根据我框选的截图继续对话。";
const LAYOUT_DEGRADED_NOTICE = "窗口布局异常，已临时重置。";

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
const portrait = document.querySelector("#portrait");
const visualContainer = document.querySelector("#visual-renderer");
let currentSurface = { width: 320, height: 480, assetKey: null, assetId: null };
const portraitFallback = document.querySelector("#portrait-fallback");
const portraitFallbackName = document.querySelector("#portrait-fallback-name");
const contextMenuElement = document.querySelector("#pet-context-menu");
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
const POINTER_INTERACTIVE_SELECTOR = "[data-interactive], [data-selectable-text]";
let contentScale = 1;
let activeBounds = [0, 0, 900, 1112];
let activeSurfaceRevision = 0;
let activePortraitAnchor = null;
let currentHitRegions = null;
let currentPortraitSourceSize = null;
let renderedPortrait = null;
let disposed = false;
let asrController = null;
let draftVersion = 0;
let presentationUnavailable = false;
let layoutDegraded = false;
let activeAppearance = null;
const appEventUnlisteners = [];
const surfaceVisibility = { bubbleVisible: true, inputVisible: true };
const surfaceVisibilityRevision = { bubble: 0, input: 0 };
let surfaceVisibilityController = null;
let surfaceHoverTracker = null;
let surfaceHoverProbe = null;
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
const composerActionIndicator = createComposerActionIndicator({ button: send });

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
  delete presentationError.dataset.asrError;
  presentationError.textContent = String(message || "角色表现暂时不可用");
  presentationError.hidden = false;
}

function clearRecoverableError() {
  delete presentationError.dataset.asrError;
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
          durationMs: COMPOSER_MOTION_DURATION_MS,
          stagingHeight: composerStagingHeight({
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
          durationMs: BUBBLE_MOTION_DURATION_MS,
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
    currentHitRegions = applyVisualSurfaceAppearance(stage, layout, currentSurface, activeAppearance?.portraitScalePercent ?? 100);
  },
  commitLayout: (layout, result, metadata = {}) => {
    stage.dataset.nativeViewport = String(result.backendMode === "macos_cursor_router");
    contentScale = result.contentScale;
    activeBounds = result.activeBounds;
    activeSurfaceRevision = result.revision;
    activePortraitAnchor = result.portraitAnchor;
    productLayout = layout;
    applyPetLayout(stage, layout, contentScale, activeBounds);
    interactionLatencyTrace.mark("layout.native-css-commit", metadata.interactionTrace);
    scheduleInteractionPaintProbe("layout", metadata.interactionTrace);
    currentHitRegions = applyVisualSurfaceAppearance(stage, layout, currentSurface, activeAppearance?.portraitScalePercent ?? 100);
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
  activePortraitAnchor = diagnostics.globalAnchor;
  currentHitRegions = applyVisualSurfaceAppearance(stage, productLayout, currentSurface, activeAppearance?.portraitScalePercent ?? 100);
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
} catch (error) {
  if (!sessionBlockedAtStartup) runtimeDiagnostics.reportError(error, { command: "visual_startup", code: "VISUAL_STARTUP_FAILED" });
  presentationUnavailable = true;
  if (!sessionBlockedAtStartup) showRecoverableError("角色加载失败，请重启 Sakura 后再试。");
  characterPresentation = Object.freeze({
    generationId: "unavailable",
    characterId: "unavailable",
    displayName: "当前角色",
    initialMessage: "当前角色表现暂时不可用。",
    themeTokens: Object.freeze({}),
    schemaVersion: 2, visual: null, visualReasonCode: "VISUAL_NOT_BOUND",
  });
}

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
let visualScalePercent = activeAppearance.portraitScalePercent;
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
if (!presentationUnavailable && !inputVisualEffectFallbackActive) clearRecoverableError();

let portraitHitRevision = 0;
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
let layoutSurfaceReady = null;
let layoutNativePrepared = false;
let layoutFrameRevision = 0;
let layoutPreviewEnding = false;
let layoutPreviewEnd = null;
let layoutGestureTrace = null;
let layoutPreviewTimer = null;
let layoutPreviewRevision = initialLayoutRevision;
let controlSurfaceGlassPreviewPending = null;
let controlSurfaceGlassPreviewRunning = false;
let controlSurfaceGlassPreviewDrain = Promise.resolve();
let controlSurfaceGlassPreviewKey = "";
const LAYOUT_PREVIEW_SETTLE_MS = 120;
const runControlSurfaceTransaction = createControlSurfaceTransactions({
  isCurrent: (revision) => revision === layoutPreviewRevision,
  isDisposed: () => disposed,
  commit: commitSurfaceApplication,
});

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
  layoutSurfaceReady = null;
  layoutNativePrepared = false;
  layoutPreviewEnding = false;
  layoutPreviewEnd = null;
  stage.dataset.layoutPreview = "active";
  layoutGestureReady = Promise.resolve()
    .then(() => screenAttachment.close())
    .then(() => tracedInteractionInvoke(
      "begin_control_surface_preview",
      { revision },
      traceContext,
      "layout.begin-preview",
    ))
    .then((requiresPreparation) => {
      if (disposed || revision !== layoutPreviewRevision) return null;
      return Object.freeze({ revision, trace: traceContext, requiresPreparation: requiresPreparation === true });
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

async function prepareLayoutPreviewSurface(preview) {
  if (!preview || disposed || preview.revision !== layoutPreviewRevision) return false;
  if (!preview.requiresPreparation) return true;
  if (!layoutSurfaceReady) {
    layoutSurfaceReady = runControlSurfaceTransaction(
      preview.revision,
      () => invoke("prepare_control_surface_preview", { revision: preview.revision }),
    ).then((surface) => {
      if (!surface || disposed || preview.revision !== layoutPreviewRevision) return false;
      layoutNativePrepared = true;
      return true;
    }).catch(() => {
      if (!disposed && preview.revision === layoutPreviewRevision) {
        layoutSurfaceReady = null;
        showRecoverableError("桌宠布局预览准备失败；再次调整可重试。");
      }
      return false;
    });
  }
  return layoutSurfaceReady;
}

function endLayoutPreviewSession(revision, ready, traceContext = null) {
  if (disposed || revision !== layoutPreviewRevision || !layoutPreviewSessionActive) {
    return Promise.resolve();
  }
  if (layoutPreviewEnd) return layoutPreviewEnd;
  layoutPreviewEnding = true;
  layoutPreviewEnd = finishLayoutPreviewSession(revision, ready, traceContext).finally(() => {
    if (revision === layoutPreviewRevision) {
      layoutPreviewEnd = null;
      layoutPreviewEnding = false;
    }
  });
  return layoutPreviewEnd;
}

async function finishLayoutPreviewSession(revision, ready, traceContext) {
  const preview = await ready;
  if (!preview || disposed || preview.revision !== revision || revision !== layoutPreviewRevision) return;
  if (layoutSurfaceReady) await layoutSurfaceReady;
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
  await runControlSurfaceTransaction(
    revision,
    () => tracedInteractionInvoke(
      "end_control_surface_preview",
      { revision },
      traceContext,
      "layout.end-preview",
    ),
  );
  if (revision === layoutPreviewRevision) {
    layoutPreviewSessionActive = false;
    layoutNativePrepared = false;
    delete stage.dataset.layoutPreview;
  }
}

async function settleLayoutPreview(revision) {
  layoutPreviewTimer = null;
  try {
    await endLayoutPreviewSession(revision, layoutGestureReady);
  } catch {
    showRecoverableError("桌宠裁剪区域恢复失败；再次调整布局可重试。");
    return;
  }
}

async function previewLayoutAppearance() {
  const { revision, ready } = beginLayoutPreviewSession();
  const preview = await ready;
  if (!preview || !await prepareLayoutPreviewSurface(preview)) {
    if (revision === layoutPreviewRevision) adaptiveSurface.invalidate();
    return;
  }
  if (disposed || revision !== layoutPreviewRevision) return;
  adaptiveSurface.invalidate({ visualPreview: true, deferNative: layoutNativePrepared });
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
  currentPortraitSourceSize = [currentSurface.width, currentSurface.height];
  currentHitRegions = applyVisualSurfaceAppearance(stage, productLayout, currentSurface, portraitScalePercent);
  interactionLatencyTrace.mark("portrait.css-commit", traceContext);
  scheduleInteractionPaintProbe("portrait", traceContext);
}

function commitSurfaceApplication(surface) {
  if (surface.backendMode) {
    stage.dataset.nativeViewport = String(surface.backendMode === "macos_cursor_router");
  }
  const geometryUnchanged = samePetSurfaceGeometry(contentScale, activeBounds, surface);
  contentScale = surface.contentScale;
  activeBounds = surface.activeBounds;
  activeSurfaceRevision = surface.revision;
  activePortraitAnchor = surface.portraitAnchor ?? activePortraitAnchor;
  if (geometryUnchanged) return;
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
    surface = currentSurface,
  } = {},
) {
  return tracedInteractionInvoke(
    "activate_portrait_hit_test",
    {
      portraitKey: key,
      revision,
      portraitScalePercent,
      ...(portraitResourceId || surface.assetId ? { portraitResourceId: portraitResourceId || surface.assetId } : { surfaceSize: [surface.width, surface.height] }),
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

function reportVisualError(code, error, stage) {
  runtimeDiagnostics.reportError(error || code, { command: "visual_control", code,
    stage: typeof stage === "string" ? stage : "visual.control" });
}

function visualUnavailable(code, error, stage) {
  portraitFallback.hidden = false;
  currentSurface = { width: 320, height: 480, assetKey: null, assetId: null };
  renderedPortrait = "";
  void activatePortraitHitTest("").then(() => syncPortraitAppearance("")).catch(() => {});
  showRecoverableError("角色表现暂不可用，你仍可以继续聊天。", { autoHide: true });
  // Core already recorded failed binds. Here only the renderer owns an exception.
  if (error) runtimeDiagnostics.reportError(error, { command: "visual_renderer", code,
    stage: typeof stage === "string" ? stage : "visual.renderer" });
}

const rendererHost = createRendererHost({
  container: visualContainer,
  onUnavailable: visualUnavailable,
  onError: reportVisualError,
  services: {
    unavailable: visualUnavailable,
    reportError: reportVisualError,
    cancelSurface() {
      void activatePortraitHitTest(currentSurface.assetKey || "", ++portraitHitRevision, null, { portraitScalePercent: visualScalePercent }).catch(() => {});
    },
    async prepareSurface({ assetKey }, { signal }) {
      const revision = ++portraitHitRevision;
      const surface = await runPortraitSurfaceMutation(() => invoke("prepare_portrait_transition", { portraitKey: assetKey, revision }));
      if (signal.aborted || revision !== portraitHitRevision) return false;
      if (surface) commitSurfaceApplication(surface);
      return true;
    },
    async setSurface({ assetKey = null, width, height }, { signal, operationSignal, visual }) {
      if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 || width > 8192 || height > 8192) throw new Error("SURFACE_SIZE_INVALID");
      const url = assetKey ? visual.assets[assetKey] : null;
      if (assetKey && !url) throw new Error("VISUAL_ASSET_UNKNOWN");
      const surface = { width, height, assetKey, assetId: url ? url.split("/").at(-1) : null };
      const revision = ++portraitHitRevision;
      const applied = await runPortraitSurfaceMutation(() => {
        if (signal.aborted || operationSignal?.aborted || revision !== portraitHitRevision) return false;
        return activatePortraitHitTest(assetKey || "", revision, null, { surface, portraitScalePercent: visualScalePercent });
      });
      if (signal.aborted || operationSignal?.aborted || revision !== portraitHitRevision || !applied) return false;
      currentSurface = surface;
      renderedPortrait = assetKey || "";
      syncPortraitAppearance(renderedPortrait, characterPresentation, visualScalePercent);
      portraitFallback.hidden = true;
      return true;
    },
    async finishSurface(_context) {
      const revision = portraitHitRevision;
      await waitForPortraitPaint();
      if (revision !== portraitHitRevision || _context.signal.aborted) return false;
      await runPortraitSurfaceMutation(() => invoke("commit_portrait_transition", { revision }));
      return true;
    },
  },
});

let presentation = createChatPresentationReducer({
  initialMessage: characterPresentation.initialMessage,
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
    setTimer: (callback, delay) => window.setTimeout(callback, delay),
    clearTimer: (handle) => window.clearTimeout(handle),
    requestFrame: (callback) => window.requestAnimationFrame(callback),
  });
}

function surfaceFadeDuration() {
  return SURFACE_VISIBILITY_FADE_MS;
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
  // Surface suspension during a pet drag is presentation-only; the window and ASR context live on.
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
  readDeferNative: () => layoutPreviewSessionActive && layoutNativePrepared,
  readVisibility: () => ({ ...surfaceVisibility }),
});

const composerToolRegistry = createComposerToolRegistry({
  list: composerToolList,
  invoke,
  beforeActivate: () => screenAttachment.close(),
  onError: (message) => showRecoverableError(message, { autoHide: true }),
});

function inputIsPinned() {
  return asrController?.active() === true
    || inputFocus.snapshot().inputFocused
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
    onError: () => showRecoverableError("桌宠控件暂时无法更新。", { autoHide: true }),
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
  surfaceHoverProbe = createSurfaceHoverProbe({
    readHover: () => invoke("pet_surface_hovered"),
    onHoverChange: (active) => {
      if (active) surfaceHoverTracker.enter("native-surface");
      else surfaceHoverTracker.leave("native-surface");
    },
  });
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

const voiceMic = document.querySelector("#voice-mic");
const voiceStatus = document.querySelector("#voice-status");
const voiceRecording = document.querySelector("#voice-recording");
const asrPresentation = createAsrPresentation({
  composer, input, button: voiceMic, status: voiceStatus, recording: voiceRecording,
});
const waveform = createAsrWaveform({ canvas: document.querySelector("#voice-waveform"), window });
asrController = createAsrController({
  invoke,
  listen: (eventName, handler) => window.__TAURI__.event.listen(eventName, handler),
  readContext: () => `${characterPresentation.generationId}:${characterPresentation.characterId}`,
  readDraft: () => ({
    value: input.value, version: draftVersion,
    selectionStart: input.selectionStart, selectionEnd: input.selectionEnd,
    selectionDirection: input.selectionDirection,
  }),
  writeDraft: ({ value, caret }) => {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus({ preventScroll: true });
    input.setSelectionRange(caret, caret);
    asrPresentation.complete();
  },
  restoreSelection: (saved) => {
    input.focus({ preventScroll: true });
    if (saved) input.setSelectionRange(saved.selectionStart, saved.selectionEnd, saved.selectionDirection);
  },
  onState: ({ state }) => {
    if (state === "preparing" && presentationError.dataset.asrError === "true") clearRecoverableError();
    const busy = state !== "idle";
    const waiting = state === "preparing" || state === "recognizing";
    asrPresentation.setState(state);
    attachmentToggle.dataset.action = busy ? "cancel" : "tools";
    attachmentToggle.setAttribute("aria-haspopup", busy ? "false" : "menu");
    if (busy) void screenAttachment.close();
    screenAttachment.refreshControls();
    voiceMic.disabled = waiting || presentationUnavailable;
    ttsController.setInputCaptureActive(state === "preparing" || state === "recording");
    if (state === "recording") waveform.start();
    else waveform.stop();
    render(presentation.current());
    adaptiveSurface.invalidate();
    surfaceVisibilityController?.setInputPinned(inputIsPinned());
  },
  onLevel: (level) => waveform.push(level),
  onError: (message) => {
    showRecoverableError(message);
    presentationError.dataset.asrError = "true";
    const copy = document.createElement("span");
    copy.textContent = message;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "重试";
    retry.dataset.interactive = "true";
    retry.addEventListener("click", () => {
      if (asrAvailability.enabled() && !presentationUnavailable) void asrController.start();
    });
    const settings = document.createElement("button");
    settings.type = "button";
    settings.textContent = "打开设置";
    settings.dataset.interactive = "true";
    settings.addEventListener("click", () => {
      void invoke("activate_pet_context_menu_action", { actionId: "sakura.settings.open" })
        .catch(() => showRecoverableError("设置暂时无法打开，请重试。"));
    });
    presentationError.replaceChildren(copy, retry, settings);
  },
});
await asrController.connect();
const asrAvailability = createAsrAvailability({
  invoke,
  onChange: (enabled) => {
    if (!enabled) {
      void asrController.cancel();
      asrPresentation.reset();
    }
    voiceMic.hidden = !enabled;
    composer.dataset.asrEnabled = String(enabled);
    adaptiveSurface.invalidate();
  },
});
await asrAvailability.start();
voiceMic.addEventListener("click", () => {
  if (!asrAvailability.enabled()) return;
  if (asrController.state() === "recording") void asrController.stop();
  else if (!asrController.active()) void asrController.start();
});
attachmentToggle.addEventListener("click", () => {
  if (asrController.active()) void asrController.cancel();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !asrController.active()) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  void asrController.cancel();
}, true);

const typewriter = createTypewriter({
  intervalMs: chatTiming.subtitleTypingIntervalMs,
  segmentPauseMs: chatTiming.replySegmentPauseMs,
  language: subtitleLanguage,
  onStart: () => bubbleScroll.beginReply(),
  onText: (text, bubbleUpdate) => {
    const result = presentation.setTypingText(text);
    if (result.applied) render(result.state, bubbleUpdate);
  },
  onSegment: (segment, index) => {
    const state = presentation.current();
    if (state.phase === "typing" && state.segments[index] === segment) {
      const subtitleReady = ttsController.beforeSegment(segment, index, {
        onStarted: () => {
          if (presentation.current().operationId !== state.operationId) return;
          const result = presentation.setTypingSegment(segment, index);
          if (result.applied) {
            void rendererHost.play(segment.control, state.operationId, index);
            void render(result.state);
          }
        },
      });
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
  onFrame: (frame) => {
    const result = presentation.setWaitingText(frame);
    if (result.applied) render(result.state);
  },
});

function render(state, bubbleUpdate = {}) {
  surfaceVisibilityController?.setPhase(state.phase);
  let bubbleCommitted = false;
  const commitBubble = () => {
    if (bubbleCommitted) return;
    bubbleCommitted = true;
    bubbleScroll.updateText(state.bubbleText, bubbleUpdate);
    adaptiveSurface.schedule();
  };
  chatPhase.textContent = phaseLabels[state.phase] || "在线";
  if (!characterVisualPreviewActive) {
    commitBubble();
  }
  input.placeholder = composerPlaceholder(characterPresentation.displayName, state.phase);
  send.dataset.action = state.canCancel ? "cancel" : state.canRetry ? "retry" : "send";
  const actionLabel = state.canCancel ? "停止回复" : state.canRetry ? "重试连接" : "发送消息";
  send.setAttribute("aria-label", actionLabel);
  composerActionIndicator.setBusy(state.canCancel);
  input.disabled = presentationUnavailable;
  send.disabled = asrController?.active() === true || presentationUnavailable || state.silentInteraction || (
    !state.canRetry
    && !isChatReadyLifecycle(state.lifecycle)
  );
  replyHistoryPrevious.disabled = !state.canReviewPrevious;
  replyHistoryNext.disabled = !state.canReviewNext;
  document.body.dataset.chatState = state.phase;
  stage.dataset.chatState = state.phase;
  return Promise.resolve({ applied: true });
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
  if (event.type === "lifecycle" && isNewCharacterGeneration(before.generationId, event.generationId)) {
    void asrController?.cancel({ restore: false });
    asrPresentation.reset();
    composerActionIndicator.reset();
    void asrAvailability.refresh();
    ttsController.cancel();
    screenAttachment.invalidate();
    screenAwareness.generationChanged(event.generationId);
    updateAnnouncement.generationChanged();
    composerToolRegistry.invalidate();
    rendererHost.freeze("generation_changed");
  }
  if (event.type === "lifecycle" && event.generationId === characterPresentation.generationId && event.revision !== before.revision) void rebindCoreGeneration(event.generationId, { refresh: true });
  const result = presentation.reduce(event);
  if (!result.applied) return;
  if (event.type === "chat.started") rendererHost.begin(event.operationId);
  if (["chat.failed", "chat.cancelled"].includes(event.type) || (event.type === "lifecycle" && !isChatReadyLifecycle(event.status))) rendererHost.cancel("interrupted");
  const waitingForFirstSegment = event.type === "chat.completed" && result.state.phase === "typing";
  if (before.phase === "thinking" && result.state.phase !== "thinking" && !waitingForFirstSegment) {
    waitingIndicator.stop();
  }
  if (before.phase === "typing" && result.state.phase !== "typing") {
    typewriter.cancel(result.state.bubbleText);
  }
  render(result.state);
  if (event.type === "chat.completed" && before.canCancel) composerActionIndicator.complete();
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
    rendererHost.begin(event.operationId);
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
      && !screenAttachment.busy()
      && !asrController?.active();
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
      && !asrController?.active()
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
  if (asrController?.active()) return;
  const state = presentation.current();
  if (presentationUnavailable || chatClient.isBusy() || state.canCancel || !isChatReadyLifecycle(state.lifecycle)) return;
  updateAnnouncement.noteActivity();
  screenAwareness.noteManualSend();
  typewriter.cancel("");
  ttsController.cancel();
  rendererHost.cancel("interrupted");
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
    const initialPortraitAnchor = activePortraitAnchor;
    const bubbleWasHidden = surfaceVisibilityController?.snapshot().bubbleVisible === false;
    interactionLatencyTrace.mark("pet-drag.pointerdown", dragTrace, { event });
    clearTextSelection(window.getSelection?.());
    event.preventDefault();
    dragRegion.classList.add("is-native-dragging");
    surfaceVisibilityController?.setSuspended(true);
    try {
      const dragResult = await startNativePetDragWithRevisionRecovery({
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
      if (shouldRevealBubbleAfterNativeDrag({
        bubbleWasHidden,
        initialAnchor: initialPortraitAnchor,
        result: dragResult,
      })) {
        surfaceVisibilityController?.activatePet();
      }
      if (dragResult?.portraitAnchor) activePortraitAnchor = dragResult.portraitAnchor;
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
      viewport: stage.dataset.nativeViewport === "true" ? {
        x: activeBounds[0] * contentScale,
        y: activeBounds[1] * contentScale,
        width: activeBounds[2] * contentScale,
        height: activeBounds[3] * contentScale,
      } : null,
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

async function rebindCoreGeneration(generationId, { refresh = false } = {}) {
  if (!refresh && generationId === characterPresentation.generationId) return true;
  if (disposed || !generationId || coreRebindTarget) return false;
  const revision = ++coreRebindRevision;
  coreRebindTarget = generationId;
  try {
    const next = await loadCurrentCharacterPresentation({ invoke, expectedGenerationId: generationId });
    if (disposed || revision !== coreRebindRevision) return false;
    if (next.generationId === characterPresentation.generationId && next.characterId === characterPresentation.characterId
      && next.visual?.bindingId === characterPresentation.visual?.bindingId && next.visualReasonCode === characterPresentation.visualReasonCode
      && (!next.visual || rendererHost.current()?.bindingId === next.visual.bindingId)) return true;
    characterVisualPreviewSessions.invalidate();
    characterVisualPreviewActive = false;
    ++portraitHitRevision;
    const rebound = rebindCharacterPresentation({ currentCharacterId: characterPresentation.characterId, nextPresentation: next, currentReducer: presentation });
    if (rebound.characterChanged || next.generationId !== characterPresentation.generationId) {
      waitingIndicator.stop(); typewriter.cancel(""); ttsController.cancel();
    }
    presentation = rebound.reducer;
    pendingCharacterGreeting = rebound.greetingPending;
    characterPresentation = next;
    try { activeAppearance = validateAppearancePublication(await invoke("current_character_appearance"), next); } catch { /* retain valid appearance */ }
    if (disposed || revision !== coreRebindRevision) return false;
    visualScalePercent = activeAppearance.portraitScalePercent;
    await rendererHost.bind(next);
    if (disposed || revision !== coreRebindRevision) return false;
    presentationUnavailable = false;
    characterName.textContent = next.displayName;
    portraitFallbackName.textContent = next.displayName;
    portrait.setAttribute("aria-label", `${next.displayName}，可拖动窗口`);
    applyTheme(activeAppearance.themeTokens);
    applyAppearanceVariables(activeAppearance);
    adaptiveSurface.invalidate();
    render(presentation.current());
    return true;
  } catch (error) {
    if (!disposed && revision === coreRebindRevision) {
      runtimeDiagnostics.reportError(error, { command: "visual_rebind", code: "VISUAL_REBIND_FAILED" });
      showRecoverableError("角色资源加载失败，请稍后重试。");
    }
    return false;
  }
  finally { if (revision === coreRebindRevision) coreRebindTarget = ""; }
}

await listenAppEvent("sakura://character-visual-preview", async (event) => {
  try {
    const publication = event?.payload;
    const token = characterVisualPreviewSessions.begin(publication);
    if (!token) return;
    const next = validateCharacterPresentation(publication.presentation);
    const appearance = validateAppearancePublication(publication.appearance, next);
    if (next.generationId !== token.coreGenerationId) return;
    await screenAttachment.close();
    if (!characterVisualPreviewSessions.isCurrent(token)) return;
    ++portraitHitRevision;
    characterVisualPreviewActive = true;
    visualScalePercent = appearance.portraitScalePercent;
    await rendererHost.bind(next);
    if (!characterVisualPreviewSessions.isCurrent(token)) return;
    bubbleScroll.updateText(next.initialMessage, { forceEnd: true });
    applyTheme(appearance.themeTokens);
    if (next.characterId === characterPresentation.characterId) {
      visualScalePercent = activeAppearance.portraitScalePercent;
      await rendererHost.bind(characterPresentation);
      characterVisualPreviewActive = false;
      render(presentation.current());
    }
  } catch (error) {
    runtimeDiagnostics.reportError(error, { command: "visual_preview", code: "VISUAL_PREVIEW_FAILED" });
    showRecoverableError("角色预览失败。");
  }
});

await listenAppEvent("sakura://control-surface-frame", async (event) => {
  if (!layoutGestureActive || !event.payload || typeof event.payload !== "object") return;
  const frameTrace = event.payload.trace || layoutGestureTrace;
  if (frameTrace) layoutGestureTrace = frameTrace;
  interactionLatencyTrace.mark("layout.frame-event-received", frameTrace);
  const normalized = normalizeLayoutAdjustments(contract, event.payload);
  if (Object.entries(normalized).some(([field, value]) => event.payload[field] !== value)) return;
  const frameRevision = ++layoutFrameRevision;
  appearanceMutationGuard.supersede();
  surfaceVisibilityController?.previewBubble();
  let deferNative = event.payload.deferNative === true;
  // Windows owns a resident backing envelope, so its first visual frame must not wait for the
  // native guard IPC. The guard only expands the current region and normally lands before paint;
  // non-Windows platforms still require native readiness before moving DOM geometry.
  if (!deferNative) {
    const ready = await layoutGestureReady;
    if (!ready || ready.revision !== layoutPreviewRevision) return;
    if (event.payload.prepareNative === true) {
      if (!await prepareLayoutPreviewSurface(ready)) return;
      deferNative = true;
    }
  }
  if (!layoutGestureActive || disposed || frameRevision !== layoutFrameRevision) return;
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
    if (!layoutPreviewSessionActive || layoutPreviewEnding) {
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
      && (layoutGestureActive || layoutPreviewSessionActive);
    const mutationRevision = appearanceMutationGuard.begin();
    layoutFrameRevision += 1;
    // Event callbacks are ordered, but their asynchronous preparation is not. Publish the values
    // before waiting so a newer slider frame can supersede them without a late full-object write.
    activeAppearance = nextAppearance;
    visualScalePercent = nextAppearance.portraitScalePercent;
    if (changes.layout || changes.fonts || changes.theme) {
      surfaceVisibilityController?.previewBubble();
    }
    if (layoutPreviewAtPublication) {
      // Settings flushes its latest lightweight frame before this full publication. Fold the
      // reliable values into the same gesture now; never let its async continuation start a
      // second 120 ms preview after the matching gesture-end event has already arrived.
      const ready = await layoutGestureReady;
      if (!ready || !await prepareLayoutPreviewSurface(ready)
        || !appearanceMutationGuard.isCurrent(mutationRevision)) return;
      adaptiveSurface.invalidate({
        visualPreview: true,
        deferNative: layoutPreviewSessionActive && ready.revision === layoutPreviewRevision,
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
      const key = currentSurface.assetKey || "";
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
  if (settingsAppearanceActive) return;
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
  const key = currentSurface.assetKey || "";
  if (preview.deferredNative) {
    const deferredSurface = preview.deferredSurface;
    if (deferredSurface) {
      if (!deferredSurface.ready) {
        deferredSurface.ready = runPortraitSurfaceMutation(async () => {
          if (
            disposed
            || !portraitScaleGestureActive
            || ready !== portraitScaleGestureReady
          ) return null;
          // Publish the prepared geometry on the first real value change; pointer-down itself
          // leaves the native crop and the current portrait unchanged.
          commitSurfaceApplication(deferredSurface.application);
          const revision = ++portraitHitRevision;
          const nativeFrameTrace = interactionLatencyTrace.atRevision(frameTrace, revision);
          return activatePortraitHitTest(key, revision, nativeFrameTrace, {
            portraitScalePercent,
            reportError: false,
          });
        });
      }
      try {
        await deferredSurface.ready;
      } catch {
        deferredSurface.ready = null;
        return;
      }
      if (
        disposed
        || !portraitScaleGestureActive
        || ready !== portraitScaleGestureReady
      ) return;
    }
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
        const precommitOnFirstFrame = preview.precommitOnFirstFrame === true;
        if (surface && !precommitOnFirstFrame && !disposed && revision === portraitHitRevision) {
          commitSurfaceApplication(surface);
        }
        return Object.freeze({
          revision,
          deferredNative: preview.deferredNative === true,
          deferredHitRegions: preview.deferredHitRegions === true,
          deferredSurface: surface && precommitOnFirstFrame
            ? {
                application: surface,
                ready: null,
              }
            : null,
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
    if (!preview) return;
    if (disposed || ready !== portraitScaleGestureReady || revision !== portraitHitRevision) return;
    if (preview.deferredSurface?.ready) {
      await preview.deferredSurface.ready.catch(() => null);
    }
    if (disposed || ready !== portraitScaleGestureReady || revision !== portraitHitRevision) return;
    const key = currentSurface.assetKey || "";
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
  draftVersion += 1;
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
  if (asrController?.active()) return;
  const state = presentation.current();
  if (state.canCancel) void chatClient.cancel(state.operationId);
  else if (state.canRetry) {
    invoke("retry_core").catch(() => showRecoverableError("重连失败，请稍后重试。"));
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
document.addEventListener("visibilitychange", () => {
  const visible = document.visibilityState === "visible";
  if (!visible) void asrController?.cancel({ restore: false });
  inputFocus.handleVisibility(visible);
});

function dispose() {
  if (disposed) return;
  disposed = true;
  asrController?.dispose();
  asrAvailability.dispose();
  waveform.stop();
  composerActionIndicator.dispose();
  asrPresentation.dispose();
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
  surfaceHoverProbe?.dispose();
  surfaceHoverTracker?.dispose();
  surfaceVisibilityController?.dispose();
  rendererHost.destroy();
  chatClient.dispose();
  contextMenu.dispose();
  composerToolRegistry.dispose();
  screenAwareness.dispose();
  updateAnnouncement.dispose();
  runtimeDiagnostics.dispose();
}

window.addEventListener("beforeunload", dispose, { once: true });

++portraitHitRevision;
await rendererHost.bind(characterPresentation);
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
