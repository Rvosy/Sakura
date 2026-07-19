const HIT_KINDS = Object.freeze(["interactive", "drag", "neutral"]);

function copyRect(rect, name, windowSize) {
  if (!Array.isArray(rect) || rect.length !== 4 || rect.some((value) => !Number.isFinite(value))) {
    throw new Error(`invalid ${name}`);
  }
  const [x, y, width, height] = rect;
  const [windowWidth, windowHeight] = windowSize;
  if (
    x < 0 ||
    y < 0 ||
    width <= 0 ||
    height <= 0 ||
    x + width > windowWidth ||
    y + height > windowHeight
  ) {
    throw new Error(`${name} escapes native window envelope`);
  }
  return Object.freeze(rect.map(Number));
}

function optionalRect(rect, name, windowSize) {
  return rect === null ? null : copyRect(rect, name, windowSize);
}

export function computeHitRegions(layout) {
  if (!Array.isArray(layout?.windowSize) || layout.windowSize.length !== 2) {
    throw new Error("invalid windowSize");
  }
  const portrait = copyRect(layout.portraitRect, "portraitRect", layout.windowSize);
  const bubble = optionalRect(layout.bubbleRect, "bubbleRect", layout.windowSize);
  const input = optionalRect(layout.inputRect, "inputRect", layout.windowSize);
  const controls = copyRect(layout.controlsRect, "controlsRect", layout.windowSize);
  return Object.freeze({
    state: layout.state,
    interactive: Object.freeze([input, controls].filter(Boolean)),
    drag: Object.freeze([portrait, bubble].filter(Boolean)),
    neutral: Object.freeze([]),
  });
}

function contains(rect, point) {
  const [x, y, width, height] = rect;
  return point[0] >= x && point[0] < x + width && point[1] >= y && point[1] < y + height;
}

export function classifyHitPoint(model, point) {
  if (!Array.isArray(point) || point.length !== 2 || point.some((value) => !Number.isFinite(value))) {
    throw new Error("invalid hit point");
  }
  for (const kind of HIT_KINDS) {
    if (model[kind].some((rect) => contains(rect, point))) return kind;
  }
  return "transparent";
}

export function shouldStartNativeDrag({ hitKind, button, isPrimary }) {
  return hitKind === "drag" && button === 0 && isPrimary === true;
}
