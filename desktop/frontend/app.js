import { createChatPresentationReducer } from "./chat/chat-presentation.js";
import { createFakeChatCore } from "./chat/fake-chat-core.js";
import { applyTheme } from "./core/theme.js";
import { createBubbleAutoHide } from "./pet/bubble-auto-hide.js";
import { classifyHitPoint, computeHitRegions, shouldStartNativeDrag } from "./pet/hit-regions.js";
import { createInputFocusController } from "./pet/input-focus.js";
import { createLayoutController } from "./pet/layout-controller.js";
import { applyPetLayout, computePetLayout, validateLayoutContract } from "./pet/layout.js";
import { createPortraitController } from "./pet/portrait-controller.js";
import { createTypewriter } from "./pet/typewriter.js";

const invoke = window.__TAURI__.core.invoke;
const stage = document.querySelector("#pet-stage");
const bubble = document.querySelector("#chat-bubble");
const bubbleCopy = document.querySelector("#bubble-copy");
const chatPhase = document.querySelector("#chat-phase");
const typewriterSkip = document.querySelector("#typewriter-skip");
const composer = document.querySelector("#composer");
const input = document.querySelector("#composer-input");
const send = document.querySelector("#composer-send");
const composerToggle = document.querySelector("#composer-toggle");
const portrait = document.querySelector("#portrait");
const portraitCurrent = document.querySelector("#portrait-current");
const portraitNext = document.querySelector("#portrait-next");
const portraitFallback = document.querySelector("#portrait-fallback");
const readout = document.querySelector("#geometry-readout");
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
let contentScale = 1;
let currentHitRegions = null;
let composerOpen = false;
let renderedPortrait = null;
let currentTheme = "blossom";
let disposed = false;

const contractResponse = await fetch("./pet/layout-contract.json", { cache: "no-store" });
if (!contractResponse.ok) throw new Error("failed to load pet layout contract");
const contract = validateLayoutContract(await contractResponse.json());

const layoutController = createLayoutController({
  computeLayout: (state, text) => computePetLayout(contract, state, text),
  applyNativeLayout: ({ state, revision }) => invoke("apply_pet_layout", { state, revision }),
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    applyPetLayout(stage, layout, contentScale);
    currentHitRegions = computeHitRegions(layout);
    readout.value = `${layout.state} · ${result.physicalPlacement.width}×${result.physicalPlacement.height} · ${Math.round(result.scaleFactor * 100)}%`;
    document.body.dataset.shellState = "fake-chat-ready";
    inputFocus.setPresentation(layout.state);
  },
});

async function transition(state) {
  try {
    return await layoutController.transition(state, bubbleCopy.textContent);
  } catch {
    readout.value = "LAYOUT_APPLY_FAILED";
    return { applied: false };
  }
}

function loadImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.addEventListener(
      "load",
      async () => {
        try {
          if (typeof image.decode === "function") await image.decode();
        } catch {
          // A successful load is sufficient when this WebView cannot decode asynchronously.
        }
        resolve(Object.freeze({ width: image.naturalWidth, height: image.naturalHeight }));
      },
      { once: true },
    );
    image.addEventListener("error", reject, { once: true });
    image.src = source;
  });
}

const portraitController = createPortraitController({
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
  },
  showFallback: () => {
    portrait.classList.remove("is-transitioning");
    portraitFallback.hidden = false;
  },
});

const presentation = createChatPresentationReducer();
const fakeCore = createFakeChatCore();

const bubbleAutoHide = createBubbleAutoHide({
  onHidden: () => {
    bubble.classList.add("is-auto-hidden");
    transition("idle");
  },
  onShown: () => bubble.classList.remove("is-auto-hidden"),
});

function desiredLayout(state) {
  if (bubbleAutoHide.snapshot().hidden) return "idle";
  if (!composerOpen) return "bubble";
  const expectedLength = state.segments.reduce((total, segment) => total + segment.text.length, 0);
  return expectedLength > 220 ? "expanded" : "composer";
}

const phaseLabels = Object.freeze({
  booting: "BOOT",
  ready: "READY",
  thinking: "THINKING",
  typing: "REPLY",
  settled: "DONE",
  error: "ERROR",
  cancelled: "CANCELLED",
  reconnecting: "RECONNECT",
});

function render(state, { updateLayout = true } = {}) {
  chatPhase.textContent = phaseLabels[state.phase] || "IDLE";
  bubbleCopy.textContent = state.bubbleText;
  typewriterSkip.hidden = !state.canSkip;
  send.dataset.action = state.canCancel ? "cancel" : "send";
  send.textContent = state.canCancel ? "取消" : "发送";
  send.disabled = state.lifecycle !== "ready" || state.phase === "reconnecting";
  composerToggle.setAttribute("aria-pressed", String(composerOpen));
  document.body.dataset.chatState = state.phase;
  stage.dataset.chatState = state.phase;

  if (state.phase === "settled") bubbleAutoHide.notifySettled();
  else if (["thinking", "typing", "reconnecting", "booting"].includes(state.phase)) bubbleAutoHide.notifyBusy();

  if (renderedPortrait !== state.portrait) {
    renderedPortrait = state.portrait;
    portraitController.show(state.portrait, { immediate: portraitCurrent.getAttribute("src") === null });
  }
  return updateLayout ? transition(desiredLayout(state)) : Promise.resolve({ applied: false });
}

const typewriter = createTypewriter({
  onText: (text) => {
    const result = presentation.setTypingText(text);
    if (result.applied) render(result.state, { updateLayout: false });
  },
  onSegment: (segment) => {
    const result = presentation.setTypingSegment(segment);
    if (result.applied) render(result.state, { updateLayout: false });
  },
  onComplete: () => {
    const result = presentation.finishTyping();
    if (result.applied) render(result.state);
  },
});

function handleCoreEvent(event) {
  const before = presentation.current();
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
  if (state.canCancel || state.lifecycle !== "ready") return;
  typewriter.cancel("");
  bubbleAutoHide.show();
  composerOpen = true;
  input.value = "";
  try {
    fakeCore.send({ message: text });
  } catch (error) {
    readout.value = String(error?.message || "FAKE_CHAT_SEND_FAILED");
  }
}

const inputFocus = createInputFocusController({
  focusInput: () => window.requestAnimationFrame(() => input.focus({ preventScroll: true })),
  readText: () => input.value,
  localSubmit: submitMessage,
});

for (const dragRegion of dragRegions) {
  dragRegion.addEventListener("pointerdown", async (event) => {
    if (!currentHitRegions) return;
    const point = [event.clientX / contentScale, event.clientY / contentScale];
    const hitKind = event.target.closest("[data-interactive]") ? "interactive" : classifyHitPoint(currentHitRegions, point);
    if (!shouldStartNativeDrag({ hitKind, button: event.button, isPrimary: event.isPrimary })) return;
    event.preventDefault();
    try {
      const result = await invoke("start_pet_drag");
      if (result) readout.value = `${result.state} · anchor ${result.portraitAnchor.x},${result.portraitAnchor.y}`;
    } catch {
      readout.value = "WINDOW_DRAG_FAILED";
    }
  });
}

input.addEventListener("compositionstart", (event) => {
  inputFocus.handleCompositionStart(event.data);
  stage.dataset.composing = "true";
});
input.addEventListener("compositionupdate", (event) => inputFocus.handleCompositionUpdate(event.data));
input.addEventListener("compositionend", (event) => {
  inputFocus.handleCompositionEnd(event.data);
  stage.dataset.composing = "false";
});
input.addEventListener("focus", () => inputFocus.handleInputFocus());
input.addEventListener("blur", () => inputFocus.handleInputBlur());
input.addEventListener("keydown", (event) => {
  const result = inputFocus.handleKeyDown(event);
  if (result.handled) event.preventDefault();
});
send.addEventListener("click", () => {
  const state = presentation.current();
  if (state.canCancel) fakeCore.cancel(state.operationId);
  else inputFocus.submit("button");
});

typewriterSkip.addEventListener("click", () => typewriter.skip());
bubble.addEventListener("pointerenter", () => bubbleAutoHide.setHovered(true));
bubble.addEventListener("pointerleave", () => bubbleAutoHide.setHovered(false));
bubble.addEventListener("dblclick", async () => {
  composerOpen = true;
  bubbleAutoHide.show();
  bubbleAutoHide.configure(false);
  await render(presentation.current());
  window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
});
portrait.addEventListener("dblclick", async () => {
  composerOpen = true;
  bubbleAutoHide.show();
  bubbleAutoHide.configure(false);
  await render(presentation.current());
  window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
});

composerToggle.addEventListener("click", async () => {
  composerOpen = !composerOpen;
  bubbleAutoHide.show();
  bubbleAutoHide.configure(!composerOpen);
  await render(presentation.current());
  if (composerOpen) window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
});

document.querySelector("#theme-toggle").addEventListener("click", () => {
  currentTheme = applyTheme(currentTheme === "blossom" ? "moon" : "blossom");
});
document.querySelector("#fake-restart").addEventListener("click", () => fakeCore.restart());

window.addEventListener("focus", () => inputFocus.handleWindowFocus());
window.addEventListener("blur", () => inputFocus.handleWindowBlur());
document.addEventListener("visibilitychange", () => inputFocus.handleVisibility(document.visibilityState === "visible"));

document.querySelector("#visibility-probe").addEventListener("click", async () => {
  inputFocus.handleVisibility(false);
  try {
    await invoke("probe_pet_visibility");
  } catch {
    readout.value = "VISIBILITY_PROBE_FAILED";
  } finally {
    inputFocus.handleVisibility(true);
  }
});

function dispose() {
  if (disposed) return;
  disposed = true;
  typewriter.dispose();
  bubbleAutoHide.dispose();
  portraitController.dispose();
  fakeCore.dispose();
}

document.querySelector("#close-window").addEventListener("click", () => {
  dispose();
  invoke("close_pet_window");
});
window.addEventListener("beforeunload", dispose, { once: true });

applyTheme(currentTheme);
await portraitController.show("idle", { immediate: true });
await transition("bubble");
fakeCore.start();
