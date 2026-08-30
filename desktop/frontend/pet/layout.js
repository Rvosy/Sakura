export const PRODUCT_LAYOUT_STATE = "product";
const MAX_PLACEHOLDER_TEXT = 4096;
const ADJUSTMENT_KEYS = Object.freeze([
  "controlPanelWidth",
  "bubbleMaxHeight",
  "controlPanelVerticalOffset",
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
    !range
    || !Number.isFinite(range.default)
    || !Number.isFinite(range.minimum)
    || !Number.isFinite(range.maximum)
    || range.minimum > range.default
    || range.default > range.maximum
  ) {
    throw new Error(`invalid ${label}`);
  }
}

function normalizedInteger(value, range) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return range.default;
  return Math.max(range.minimum, Math.min(range.maximum, Math.round(parsed)));
}

function normalizedMeasurement(value, minimum, maximum, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.round(parsed)));
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
    contract?.schemaVersion !== 1
    || !layout
    || !panel
    || !Array.isArray(contract.viewport?.windowSize)
    || !Array.isArray(contract.viewport?.contentScaleSize)
    || !Array.isArray(contract.viewport?.portraitAnchor)
  ) {
    throw new Error("unsupported pet layout contract");
  }
  const windowSize = contract.viewport.windowSize;
  const contentScaleSize = contract.viewport.contentScaleSize;
  const portraitAnchor = contract.viewport.portraitAnchor;
  if (
    windowSize.length !== 2
    || contentScaleSize.length !== 2
    || portraitAnchor.length !== 2
    || [...windowSize, ...contentScaleSize, ...portraitAnchor].some((value) => !Number.isFinite(value))
    || windowSize.some((value) => value <= 0)
    || contentScaleSize.some((value, index) => value <= 0 || value > windowSize[index])
    || windowSize[0] > 1200
    || windowSize[1] > 1600
    || layout.windowSize.length !== 2
    || layout.windowSize.some((value, index) => value !== windowSize[index])
    || layout.portraitAnchor.some((value, index) => value !== portraitAnchor[index])
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
    bubbleMinHeight: panel.bubbleMinHeight,
    inputBaseHeight: panel.inputBaseHeight,
    inputMaxHeight: panel.inputMaxHeight,
    inputExpandedMinRows: panel.inputExpandedMinRows,
    inputMaxRows: panel.inputMaxRows,
    inputToolbarHeight: panel.inputToolbarHeight,
    inputExpandedGap: panel.inputExpandedGap,
  })) {
    if (!Number.isFinite(value)) throw new Error(`invalid controlPanel.${key}`);
  }
  if (
    panel.bubbleMinHeight <= 0
    || panel.bubbleMinHeight > panel.bubbleMaxHeight.minimum
    || panel.inputBaseHeight <= 0
    || panel.inputBaseHeight > panel.inputMaxHeight
    || !Number.isSafeInteger(panel.inputExpandedMinRows)
    || panel.inputExpandedMinRows < 1
    || !Number.isSafeInteger(panel.inputMaxRows)
    || panel.inputMaxRows < panel.inputExpandedMinRows
    || panel.inputMaxRows > 8
    || panel.inputToolbarHeight <= 0
    || panel.inputExpandedGap < 0
  ) {
    throw new Error("invalid adaptive control panel bounds");
  }
  const [x, y, width, height] = layout.portraitRect;
  if (portraitAnchor[0] !== x + width / 2 || portraitAnchor[1] !== y + height) {
    throw new Error("product portrait anchor mismatch");
  }
  const defaults = normalizeLayoutAdjustments(contract);
  const defaultPanel = computeControlPanelRects(contract, defaults, {
    bubbleHeight: defaults.bubbleMaxHeight,
    inputHeight: panel.inputBaseHeight,
  });
  for (const key of ["bubbleRect", "inputRect", "controlsRect"]) {
    if (defaultPanel[key].some((value, index) => value !== layout[key][index])) {
      throw new Error(`product ${key} does not match control panel defaults`);
    }
  }
  return contract;
}

function computeControlPanelRects(contract, adjustments, measurements = {}) {
  const panel = contract.controlPanel;
  const width = adjustments.controlPanelWidth;
  const bubbleHeight = normalizedMeasurement(
    measurements.bubbleHeight,
    panel.bubbleMinHeight,
    adjustments.bubbleMaxHeight,
    adjustments.bubbleMaxHeight,
  );
  const inputHeight = normalizedMeasurement(
    measurements.inputHeight,
    panel.inputBaseHeight,
    panel.inputMaxHeight,
    panel.inputBaseHeight,
  );
  const x = Math.round(panel.centerX - width / 2);
  const referenceBubbleBottom = panel.bubbleBottom - adjustments.controlPanelVerticalOffset;
  const requestedInputTop = referenceBubbleBottom + panel.inputGap + adjustments.inputBarOffset;
  // Reserve the maximum composer height at every settings position. Content can then grow
  // downward without moving the bubble; only an explicit layout setting may reposition both.
  const reservedOverflow = Math.max(
    0,
    requestedInputTop + panel.inputMaxHeight - contract.viewport.windowSize[1],
  );
  const inputTop = requestedInputTop - reservedOverflow;
  const bubbleBottom = referenceBubbleBottom - reservedOverflow;
  const bubbleRect = [x, bubbleBottom - bubbleHeight, width, bubbleHeight];
  const inputRect = [x, inputTop, width, inputHeight];
  const controlsRect = [x + width - 40, bubbleRect[1] + 10, 30, 30];
  return Object.freeze({ bubbleRect, inputRect, controlsRect });
}

export function computePetLayout(
  contract,
  state = PRODUCT_LAYOUT_STATE,
  placeholderText = "",
  layoutAdjustments = {},
  measurements = {},
) {
  validateLayoutContract(contract);
  if (state !== PRODUCT_LAYOUT_STATE) throw new Error(`unknown pet state: ${state}`);
  const source = contract.states[PRODUCT_LAYOUT_STATE];
  const adjustments = normalizeLayoutAdjustments(contract, layoutAdjustments);
  const controlPanel = computeControlPanelRects(contract, adjustments, measurements);
  validateRect(controlPanel.bubbleRect, source.windowSize, "adjusted bubbleRect");
  validateRect(controlPanel.inputRect, source.windowSize, "adjusted inputRect");
  validateRect(controlPanel.controlsRect, source.windowSize, "adjusted controlsRect");
  const visibleRects = [
    [source.portraitRect, 2],
    [controlPanel.bubbleRect, 2],
    [controlPanel.inputRect, 4],
    [controlPanel.controlsRect, 4],
  ];
  const left = Math.max(0, Math.min(...visibleRects.map(([rect, outset]) => rect[0] - outset)));
  const top = Math.max(0, Math.min(...visibleRects.map(([rect, outset]) => rect[1] - outset)));
  const right = Math.min(source.windowSize[0], Math.max(...visibleRects.map(([rect, outset]) => rect[0] + rect[2] + outset)));
  const bottom = Math.min(source.windowSize[1], Math.max(...visibleRects.map(([rect, outset]) => rect[1] + rect[3] + outset)));
  return Object.freeze({
    contractVersion: contract.schemaVersion,
    state: PRODUCT_LAYOUT_STATE,
    windowSize: copyRect(contract.viewport.windowSize),
    activeWindowSize: Object.freeze([right - left, bottom - top]),
    activeOffset: Object.freeze([left, top]),
    portraitRect: copyRect(source.portraitRect),
    bubbleRect: copyRect(controlPanel.bubbleRect),
    inputRect: copyRect(controlPanel.inputRect),
    controlsRect: copyRect(controlPanel.controlsRect),
    portraitAnchor: copyRect(contract.viewport.portraitAnchor),
    layoutAdjustments: adjustments,
    measurements: Object.freeze({
      bubbleHeight: controlPanel.bubbleRect[3],
      inputHeight: controlPanel.inputRect[3],
    }),
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

export function applyControlPanelWidth(root, contract, adjustments = {}) {
  const normalized = normalizeLayoutAdjustments(contract, adjustments);
  const width = normalized.controlPanelWidth;
  const x = Math.round(contract.controlPanel.centerX - width / 2);
  for (const name of ["bubble", "input"]) {
    root.style.setProperty(`--${name}-x`, `${x}px`);
    root.style.setProperty(`--${name}-width`, `${width}px`);
  }
  return normalized;
}

export function applyPetLayout(root, layout, contentScale, activeBounds = null) {
  const [windowWidth, windowHeight] = layout.windowSize;
  const [activeX, activeY] = activeBounds ?? [layout.activeOffset[0], layout.activeOffset[1]];
  root.style.setProperty("--stage-width", `${windowWidth}px`);
  root.style.setProperty("--stage-height", `${windowHeight}px`);
  root.style.setProperty("--content-scale", String(contentScale));
  root.style.left = `${-activeX * contentScale}px`;
  root.style.top = `${-activeY * contentScale}px`;
  root.dataset.surfaceX = String(activeX);
  root.dataset.surfaceY = String(activeY);
  setRect(root, "portrait", layout.portraitRect);
  setRect(root, "bubble", layout.bubbleRect);
  setRect(root, "input", layout.inputRect);
  setRect(root, "controls", layout.controlsRect);
  root.dataset.layoutState = PRODUCT_LAYOUT_STATE;
}
