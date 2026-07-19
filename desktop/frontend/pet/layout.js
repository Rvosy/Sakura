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

function translateRect(rect, offset) {
  if (rect === null) return null;
  return [rect[0] + offset[0], rect[1] + offset[1], rect[2], rect[3]];
}

export function validateLayoutContract(contract) {
  if (
    contract?.schemaVersion !== 1 ||
    typeof contract.states !== "object" ||
    !Array.isArray(contract.viewport?.windowSize) ||
    !Array.isArray(contract.viewport?.portraitAnchor)
  ) {
    throw new Error("unsupported pet layout contract");
  }
  const [viewportWidth, viewportHeight] = contract.viewport.windowSize;
  const [viewportAnchorX, viewportAnchorY] = contract.viewport.portraitAnchor;
  if (
    ![viewportWidth, viewportHeight, viewportAnchorX, viewportAnchorY].every(Number.isFinite) ||
    viewportWidth <= 0 ||
    viewportHeight <= 0 ||
    viewportWidth > 1200 ||
    viewportHeight > 1200 ||
    viewportAnchorX < 0 ||
    viewportAnchorX > viewportWidth ||
    viewportAnchorY < 0 ||
    viewportAnchorY > viewportHeight
  ) {
    throw new Error("invalid native viewport envelope");
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
  const activeOffset = [
    contract.viewport.portraitAnchor[0] - source.portraitAnchor[0],
    contract.viewport.portraitAnchor[1] - source.portraitAnchor[1],
  ];
  return Object.freeze({
    contractVersion: contract.schemaVersion,
    state,
    windowSize: copyRect(contract.viewport.windowSize),
    activeWindowSize: copyRect(source.windowSize),
    activeOffset,
    portraitRect: translateRect(source.portraitRect, activeOffset),
    bubbleRect: translateRect(source.bubbleRect, activeOffset),
    inputRect: translateRect(source.inputRect, activeOffset),
    portraitAnchor: copyRect(contract.viewport.portraitAnchor),
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
  root.style.setProperty("--rail-x", `${layout.activeOffset[0] + 8}px`);
  setRect(root, "portrait", layout.portraitRect);
  setRect(root, "bubble", layout.bubbleRect);
  setRect(root, "input", layout.inputRect);
  root.dataset.presentationState = layout.state;
}
