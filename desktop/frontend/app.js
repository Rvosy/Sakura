import { applyPetLayout, computePetLayout, validateLayoutContract } from "./pet/layout.js";
import { createLayoutController } from "./pet/layout-controller.js";
import {
  classifyHitPoint,
  computeHitRegions,
  shouldStartNativeDrag,
} from "./pet/hit-regions.js";
import { createInputFocusController } from "./pet/input-focus.js";

const invoke = window.__TAURI__.core.invoke;
const stage = document.querySelector("#pet-stage");
const readout = document.querySelector("#geometry-readout");
const stateButtons = [...document.querySelectorAll("[data-state]")];
const dragRegions = [...document.querySelectorAll("[data-drag-region]")];
const input = document.querySelector("#composer-input");
const send = document.querySelector("#composer-send");
let contentScale = 1;
let currentHitRegions = null;
let nativeDragPending = false;
let nativeDragCommitInFlight = false;

const contractResponse = await fetch("./pet/layout-contract.json", { cache: "no-store" });
if (!contractResponse.ok) throw new Error("failed to load pet layout contract");
const contract = validateLayoutContract(await contractResponse.json());

const inputFocus = createInputFocusController({
  focusInput: () => {
    window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
  },
  readText: () => input.value,
  localSubmit: ({ text, source }) => {
    document.querySelector("#bubble-copy").textContent = `本地技术反馈（${source}）：${text}`;
    readout.value = `local only · ${text.length} chars`;
  },
});

const controller = createLayoutController({
  computeLayout: (state, text) => computePetLayout(contract, state, text),
  applyNativeLayout: ({ state, revision }) =>
    invoke("apply_pet_layout", { state, revision }),
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    applyPetLayout(stage, layout, contentScale);
    currentHitRegions = computeHitRegions(layout);
    readout.value = `${layout.state} · ${result.physicalPlacement.width}×${result.physicalPlacement.height} · ${Math.round(result.scaleFactor * 100)}%`;
    for (const button of stateButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.state === layout.state));
    }
    document.body.dataset.shellState = "pet-geometry-ready";
    inputFocus.setPresentation(layout.state);
  },
});

async function transition(state) {
  try {
    await controller.transition(state, document.querySelector("#bubble-copy").textContent);
  } catch (error) {
    readout.value = `layout error: ${String(error)}`;
  }
}

for (const button of stateButtons) {
  button.addEventListener("click", () => transition(button.dataset.state));
}

for (const dragRegion of dragRegions) {
  dragRegion.addEventListener("pointerdown", async (event) => {
    if (!currentHitRegions) return;
    const point = [event.clientX / contentScale, event.clientY / contentScale];
    const hitKind = event.target.closest("[data-interactive]")
      ? "interactive"
      : classifyHitPoint(currentHitRegions, point);
    if (!shouldStartNativeDrag({ hitKind, button: event.button, isPrimary: event.isPrimary })) return;
    event.preventDefault();
    nativeDragPending = true;
    try {
      const result = await invoke("start_pet_drag");
      if (result) {
        nativeDragPending = false;
        readout.value = `${result.state} · anchor ${result.portraitAnchor.x},${result.portraitAnchor.y}`;
      }
    } catch (error) {
      nativeDragPending = false;
      readout.value = `drag error: ${String(error)}`;
    }
  });
}

window.__TAURI__.event
  .listen("tauri://move", async ({ payload }) => {
    if (!nativeDragPending || nativeDragCommitInFlight) return;
    nativeDragCommitInFlight = true;
    try {
      const result = await invoke("commit_pet_drag", { position: payload });
      readout.value = `${result.state} · anchor ${result.portraitAnchor.x},${result.portraitAnchor.y}`;
    } catch (error) {
      readout.value = `drag error: ${String(error)}`;
    } finally {
      nativeDragPending = false;
      nativeDragCommitInFlight = false;
    }
  })
  .catch((error) => {
    readout.value = `drag event error: ${String(error)}`;
  });

input.addEventListener("compositionstart", (event) => {
  inputFocus.handleCompositionStart(event.data);
  stage.dataset.composing = "true";
});
input.addEventListener("compositionupdate", (event) => {
  inputFocus.handleCompositionUpdate(event.data);
  readout.value = `composition · ${String(event.data || "")}`;
});
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
send.addEventListener("click", () => inputFocus.submit("button"));

window.addEventListener("focus", () => inputFocus.handleWindowFocus());
window.addEventListener("blur", () => inputFocus.handleWindowBlur());
document.addEventListener("visibilitychange", () =>
  inputFocus.handleVisibility(document.visibilityState === "visible"),
);

document.querySelector("#visibility-probe").addEventListener("click", async () => {
  inputFocus.handleVisibility(false);
  await invoke("set_pet_visible", { visible: false });
  window.setTimeout(async () => {
    await invoke("set_pet_visible", { visible: true });
    inputFocus.handleVisibility(true);
  }, 220);
});

document.querySelector("#close-window").addEventListener("click", () => invoke("close_pet_window"));

await transition("idle");
