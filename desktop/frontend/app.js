import { applyPetLayout, computePetLayout, validateLayoutContract } from "./pet/layout.js";
import { createLayoutController } from "./pet/layout-controller.js";

const invoke = window.__TAURI__.core.invoke;
const stage = document.querySelector("#pet-stage");
const readout = document.querySelector("#geometry-readout");
const stateButtons = [...document.querySelectorAll("[data-state]")];
let contentScale = 1;

const contractResponse = await fetch("./pet/layout-contract.json", { cache: "no-store" });
if (!contractResponse.ok) throw new Error("failed to load pet layout contract");
const contract = validateLayoutContract(await contractResponse.json());

const controller = createLayoutController({
  computeLayout: (state, text) => computePetLayout(contract, state, text),
  applyNativeLayout: ({ state, revision }) =>
    invoke("apply_pet_layout", { state, revision }),
  commitLayout: (layout, result) => {
    contentScale = result.contentScale;
    applyPetLayout(stage, layout, contentScale);
    readout.value = `${layout.state} · ${result.physicalPlacement.width}×${result.physicalPlacement.height} · ${Math.round(result.scaleFactor * 100)}%`;
    for (const button of stateButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.state === layout.state));
    }
    document.body.dataset.shellState = "pet-geometry-ready";
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

document.querySelector("#visibility-probe").addEventListener("click", async () => {
  await invoke("set_pet_visible", { visible: false });
  window.setTimeout(() => invoke("set_pet_visible", { visible: true }), 220);
});

document.querySelector("#close-window").addEventListener("click", () => invoke("close_pet_window"));

await transition("idle");
