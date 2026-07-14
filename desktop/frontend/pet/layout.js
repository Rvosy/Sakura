export const layoutDefaults = Object.freeze({
  controlPanelWidth: 640,
  bubbleHeight: 128,
  controlPanelVerticalOffset: 0,
  inputBarOffset: 0,
});

export const MIN_CONTROL_PANEL_WIDTH = 420;
export const MAX_CONTROL_PANEL_WIDTH = 860;
export const MIN_BUBBLE_HEIGHT = 96;
export const MAX_BUBBLE_HEIGHT = 260;
const MIN_VERTICAL_OFFSET = -200;
const MAX_VERTICAL_OFFSET = 200;
const MAX_INPUT_BAR_OFFSET = 200;
const INPUT_BAR_HEIGHT = 52;
const CONTROL_PANEL_GAP = 10;
const PORTRAIT_BOTTOM_PAD = 62;
const PORTRAIT_TOP_PAD = 8;
const STAGE_WIDTH_PANEL_PAD = 96;
const BUBBLE_INNER_PAD = 32;
const MIN_STAGE_HEIGHT = 420;
const BUBBLE_BOTTOM_ABOVE_PORTRAIT = 84;

function clampInteger(value, minimum, maximum, fallback) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}

export function computePetLayout(options) {
  const portraitWidth = Math.max(0, Math.trunc(Number(options.portraitWidth) || 0));
  const portraitHeight = Math.max(0, Math.trunc(Number(options.portraitHeight) || 0));
  const controlPanelWidth = clampInteger(
    options.controlPanelWidth,
    MIN_CONTROL_PANEL_WIDTH,
    MAX_CONTROL_PANEL_WIDTH,
    layoutDefaults.controlPanelWidth,
  );
  const bubbleHeight = clampInteger(
    options.bubbleHeight,
    MIN_BUBBLE_HEIGHT,
    MAX_BUBBLE_HEIGHT,
    layoutDefaults.bubbleHeight,
  );
  const controlPanelVerticalOffset = clampInteger(
    options.controlPanelVerticalOffset,
    MIN_VERTICAL_OFFSET,
    MAX_VERTICAL_OFFSET,
    layoutDefaults.controlPanelVerticalOffset,
  );
  const inputBarOffset = clampInteger(
    options.inputBarOffset,
    0,
    MAX_INPUT_BAR_OFFSET,
    layoutDefaults.inputBarOffset,
  );
  const windowWidth = Math.max(portraitWidth, controlPanelWidth) + STAGE_WIDTH_PANEL_PAD;
  const bubbleWidth = Math.min(
    controlPanelWidth,
    Math.max(MIN_CONTROL_PANEL_WIDTH, windowWidth - BUBBLE_INNER_PAD),
  );
  const portraitTop = -portraitHeight;
  const bubbleBottom = -BUBBLE_BOTTOM_ABOVE_PORTRAIT - controlPanelVerticalOffset;
  const bubbleTop = bubbleBottom - bubbleHeight;
  const inputTop = bubbleBottom + CONTROL_PANEL_GAP + inputBarOffset;
  const inputBottom = inputTop + INPUT_BAR_HEIGHT;
  const topExtent = Math.min(portraitTop, bubbleTop);
  const bottomGap = Math.max(PORTRAIT_BOTTOM_PAD, inputBottom);
  const contentHeight = bottomGap - topExtent + PORTRAIT_TOP_PAD;
  const windowHeight = Math.max(MIN_STAGE_HEIGHT, Math.round(contentHeight));
  const portraitBottom = windowHeight - Math.round(bottomGap);
  const localY = (frameY) => Math.round(portraitBottom + frameY);

  return {
    windowSize: [windowWidth, windowHeight],
    portraitRect: [
      Math.floor((windowWidth - portraitWidth) / 2),
      localY(portraitTop),
      portraitWidth,
      portraitHeight,
    ],
    bubbleRect: [
      Math.floor((windowWidth - bubbleWidth) / 2),
      localY(bubbleTop),
      bubbleWidth,
      bubbleHeight,
    ],
    inputRect: [
      Math.floor((windowWidth - bubbleWidth) / 2),
      localY(inputTop),
      bubbleWidth,
      INPUT_BAR_HEIGHT,
    ],
    portraitAnchor: [Math.floor(windowWidth / 2), portraitBottom],
  };
}

export function applyLayoutVariables(layout, root = document.documentElement) {
  const mappings = [
    ["window", [0, 0, ...layout.windowSize]],
    ["portrait", layout.portraitRect],
    ["bubble", layout.bubbleRect],
    ["input", layout.inputRect],
  ];
  for (const [name, rect] of mappings) {
    const [x, y, width, height] = rect;
    root.style.setProperty(`--${name}-x`, `${x}px`);
    root.style.setProperty(`--${name}-y`, `${y}px`);
    root.style.setProperty(`--${name}-width`, `${width}px`);
    root.style.setProperty(`--${name}-height`, `${height}px`);
  }
}
