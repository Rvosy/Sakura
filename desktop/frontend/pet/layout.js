export const PRESENTATION_STATES = Object.freeze([
  "idle",
  "bubble",
  "composer",
  "expanded",
]);

const MAX_PLACEHOLDER_TEXT = 4096;

function copyRect(rect) {
  return rect === null ? null : rect.map((value) => Number(value));
}

export function validateLayoutContract(contract) {
  if (contract?.schemaVersion !== 1 || typeof contract.states !== "object") {
    throw new Error("unsupported pet layout contract");
  }

  for (const state of PRESENTATION_STATES) {
    const layout = contract.states[state];
    if (!layout) throw new Error(`missing layout state: ${state}`);
    for (const key of ["windowSize", "portraitRect", "portraitAnchor"]) {
      if (!Array.isArray(layout[key]) || layout[key].some((value) => !Number.isFinite(value))) {
        throw new Error(`invalid ${state}.${key}`);
      }
    }
    if (layout.windowSize.some((value) => value <= 0 || value > 1200)) {
      throw new Error(`unsafe native window size for ${state}`);
    }
    const [windowWidth, windowHeight] = layout.windowSize;
    const [portraitX, portraitY, portraitWidth, portraitHeight] = layout.portraitRect;
    const [anchorX, anchorY] = layout.portraitAnchor;
    if (
      portraitX < 0 ||
      portraitY < 0 ||
      portraitX + portraitWidth > windowWidth ||
      portraitY + portraitHeight > windowHeight ||
      anchorX !== portraitX + portraitWidth / 2 ||
      anchorY !== portraitY + portraitHeight
    ) {
      throw new Error(`portrait anchor mismatch for ${state}`);
    }
    for (const key of ["bubbleRect", "inputRect"]) {
      const rect = layout[key];
      if (rect === null) continue;
      if (!Array.isArray(rect) || rect.length !== 4 || rect.some((value) => !Number.isFinite(value))) {
        throw new Error(`invalid ${state}.${key}`);
      }
      const [x, y, width, height] = rect;
      if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > windowWidth || y + height > windowHeight) {
        throw new Error(`${state}.${key} escapes native window bounds`);
      }
    }
  }
  return contract;
}

export function computePetLayout(contract, state, placeholderText = "") {
  validateLayoutContract(contract);
  if (!PRESENTATION_STATES.includes(state)) throw new Error(`unknown pet state: ${state}`);

  const source = contract.states[state];
  return Object.freeze({
    contractVersion: contract.schemaVersion,
    state,
    windowSize: copyRect(source.windowSize),
    portraitRect: copyRect(source.portraitRect),
    bubbleRect: copyRect(source.bubbleRect),
    inputRect: copyRect(source.inputRect),
    portraitAnchor: copyRect(source.portraitAnchor),
    placeholderText: String(placeholderText).slice(0, MAX_PLACEHOLDER_TEXT),
  });
}

function setRect(root, name, rect) {
  if (rect === null) return;
  const [x, y, width, height] = rect;
  root.style.setProperty(`--${name}-x`, `${x}px`);
  root.style.setProperty(`--${name}-y`, `${y}px`);
  root.style.setProperty(`--${name}-width`, `${width}px`);
  root.style.setProperty(`--${name}-height`, `${height}px`);
}

export function applyPetLayout(root, layout, contentScale) {
  const [windowWidth, windowHeight] = layout.windowSize;
  root.style.setProperty("--stage-width", `${windowWidth}px`);
  root.style.setProperty("--stage-height", `${windowHeight}px`);
  root.style.setProperty("--content-scale", String(contentScale));
  setRect(root, "portrait", layout.portraitRect);
  setRect(root, "bubble", layout.bubbleRect);
  setRect(root, "input", layout.inputRect);
  root.dataset.presentationState = layout.state;
}
