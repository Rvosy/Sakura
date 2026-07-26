export const PRODUCT_LAYOUT_STATE = "product";
const MAX_PLACEHOLDER_TEXT = 4096;
const ADJUSTMENT_KEYS = Object.freeze([
  "controlPanelWidth",
  "bubbleHeight",
  "verticalOffset",
  "inputBarOffset",
]);

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

function validateAdjustmentRange(range, label) {
  if (
    !range ||
    !Number.isFinite(range.default) ||
    !Number.isFinite(range.minimum) ||
    !Number.isFinite(range.maximum) ||
    range.minimum > range.default ||
    range.default > range.maximum
  ) {
    throw new Error(`invalid ${label}`);
  }
}

function normalizedInteger(value, range) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return range.default;
  return Math.max(range.minimum, Math.min(range.maximum, Math.round(parsed)));
}

export function normalizeLayoutAdjustments(contract, adjustments = {}) {
  const panel = contract?.controlPanel;
  if (!panel) throw new Error("missing control panel layout contract");
  return Object.freeze(Object.fromEntries(ADJUSTMENT_KEYS.map((key) => [
    key,
    normalizedInteger(adjustments?.[key], panel[key]),
  ])));
}

export function validateLayoutContract(contract) {
  const layout = contract?.states?.[PRODUCT_LAYOUT_STATE];
  const panel = contract?.controlPanel;
  if (
    contract?.schemaVersion !== 1 ||
    !layout ||
    !panel ||
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
  for (const key of ADJUSTMENT_KEYS) validateAdjustmentRange(panel[key], `controlPanel.${key}`);
  for (const [key, value] of Object.entries({
    centerX: panel.centerX,
    bubbleBottom: panel.bubbleBottom,
    inputGap: panel.inputGap,
    inputHeight: panel.inputHeight,
  })) {
    if (!Number.isFinite(value)) throw new Error(`invalid controlPanel.${key}`);
  }
  const [x, y, width, height] = layout.portraitRect;
  if (portraitAnchor[0] !== x + width / 2 || portraitAnchor[1] !== y + height) {
    throw new Error("product portrait anchor mismatch");
  }
  const defaults = normalizeLayoutAdjustments(contract);
  const defaultBubble = computeControlPanelRects(contract, defaults);
  for (const key of ["bubbleRect", "inputRect", "controlsRect"]) {
    if (defaultBubble[key].some((value, index) => value !== layout[key][index])) {
      throw new Error(`product ${key} does not match control panel defaults`);
    }
  }
  return contract;
}

function computeControlPanelRects(contract, adjustments) {
  const panel = contract.controlPanel;
  const width = adjustments.controlPanelWidth;
  const bubbleHeight = adjustments.bubbleHeight;
  const x = Math.round(panel.centerX - width / 2);
  const bubbleBottom = panel.bubbleBottom - adjustments.verticalOffset;
  const bubbleRect = [x, bubbleBottom - bubbleHeight, width, bubbleHeight];
  const inputRect = [
    x,
    bubbleBottom + panel.inputGap + adjustments.inputBarOffset,
    width,
    panel.inputHeight,
  ];
  const controlsRect = [x + width - 40, bubbleRect[1] + 10, 30, 30];
  return Object.freeze({ bubbleRect, inputRect, controlsRect });
}

export function computePetLayout(
  contract,
  state = PRODUCT_LAYOUT_STATE,
  placeholderText = "",
  layoutAdjustments = {},
) {
  validateLayoutContract(contract);
  if (state !== PRODUCT_LAYOUT_STATE) throw new Error(`unknown pet state: ${state}`);
  const source = contract.states[PRODUCT_LAYOUT_STATE];
  const adjustments = normalizeLayoutAdjustments(contract, layoutAdjustments);
  const controlPanel = computeControlPanelRects(contract, adjustments);
  validateRect(controlPanel.bubbleRect, source.windowSize, "adjusted bubbleRect");
  validateRect(controlPanel.inputRect, source.windowSize, "adjusted inputRect");
  validateRect(controlPanel.controlsRect, source.windowSize, "adjusted controlsRect");
  return Object.freeze({
    contractVersion: contract.schemaVersion,
    state: PRODUCT_LAYOUT_STATE,
    windowSize: copyRect(contract.viewport.windowSize),
    activeWindowSize: copyRect(contract.viewport.windowSize),
    activeOffset: Object.freeze([0, 0]),
    portraitRect: copyRect(source.portraitRect),
    bubbleRect: copyRect(controlPanel.bubbleRect),
    inputRect: copyRect(controlPanel.inputRect),
    controlsRect: copyRect(controlPanel.controlsRect),
    portraitAnchor: copyRect(contract.viewport.portraitAnchor),
    layoutAdjustments: adjustments,
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
