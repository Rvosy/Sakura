export const PRODUCT_LAYOUT_STATE = "product";
const MAX_PLACEHOLDER_TEXT = 4096;

function copyRect(rect) {
  return rect.map((value) => Number(value));
}

function validateRect(rect, windowSize, label) {
  if (!Array.isArray(rect) || rect.length !== 4 || rect.some((value) => !Number.isFinite(value))) {
    throw new Error(`invalid ${label}`);
  }
  const [x, y, width, height] = rect;
  if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > windowSize[0] || y + height > windowSize[1]) {
    throw new Error(`${label} escapes native window bounds`);
  }
}

export function validateLayoutContract(contract) {
  const layout = contract?.states?.[PRODUCT_LAYOUT_STATE];
  if (
    contract?.schemaVersion !== 1 ||
    !layout ||
    !Array.isArray(contract.viewport?.windowSize) ||
    !Array.isArray(contract.viewport?.portraitAnchor)
  ) {
    throw new Error("unsupported pet layout contract");
  }
  const windowSize = contract.viewport.windowSize;
  const portraitAnchor = contract.viewport.portraitAnchor;
  if (
    windowSize.length !== 2 ||
    portraitAnchor.length !== 2 ||
    [...windowSize, ...portraitAnchor].some((value) => !Number.isFinite(value)) ||
    windowSize.some((value) => value <= 0 || value > 1200) ||
    layout.windowSize.length !== 2 ||
    layout.windowSize.some((value, index) => value !== windowSize[index]) ||
    layout.portraitAnchor.some((value, index) => value !== portraitAnchor[index])
  ) {
    throw new Error("invalid fixed product viewport");
  }
  validateRect(layout.portraitRect, windowSize, "product.portraitRect");
  validateRect(layout.bubbleRect, windowSize, "product.bubbleRect");
  validateRect(layout.inputRect, windowSize, "product.inputRect");
  validateRect(layout.controlsRect, windowSize, "product.controlsRect");
  const [x, y, width, height] = layout.portraitRect;
  if (portraitAnchor[0] !== x + width / 2 || portraitAnchor[1] !== y + height) {
    throw new Error("product portrait anchor mismatch");
  }
  return contract;
}

export function computePetLayout(contract, state = PRODUCT_LAYOUT_STATE, placeholderText = "") {
  validateLayoutContract(contract);
  if (state !== PRODUCT_LAYOUT_STATE) throw new Error(`unknown pet state: ${state}`);
  const source = contract.states[PRODUCT_LAYOUT_STATE];
  return Object.freeze({
    contractVersion: contract.schemaVersion,
    state: PRODUCT_LAYOUT_STATE,
    windowSize: copyRect(contract.viewport.windowSize),
    activeWindowSize: copyRect(contract.viewport.windowSize),
    activeOffset: Object.freeze([0, 0]),
    portraitRect: copyRect(source.portraitRect),
    bubbleRect: copyRect(source.bubbleRect),
    inputRect: copyRect(source.inputRect),
    controlsRect: copyRect(source.controlsRect),
    portraitAnchor: copyRect(contract.viewport.portraitAnchor),
    placeholderText: String(placeholderText).slice(0, MAX_PLACEHOLDER_TEXT),
  });
}

function setRect(root, name, rect) {
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
  setRect(root, "controls", layout.controlsRect);
  root.dataset.layoutState = PRODUCT_LAYOUT_STATE;
}
