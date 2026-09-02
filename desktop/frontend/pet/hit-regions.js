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

function containedPortraitRect(target, sourceSize) {
  if (sourceSize === null) return target;
  if (
    !Array.isArray(sourceSize)
    || sourceSize.length !== 2
    || sourceSize.some((value) => !Number.isFinite(value) || value <= 0)
  ) {
    throw new Error("invalid portraitSourceSize");
  }
  const [x, y, targetWidth, targetHeight] = target;
  const [sourceWidth, sourceHeight] = sourceSize;
  const contain = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  const width = Math.min(targetWidth, Math.ceil(sourceWidth * contain));
  const height = Math.min(targetHeight, Math.ceil(sourceHeight * contain));
  return [x + Math.floor((targetWidth - width) / 2), y + targetHeight - height, width, height];
}

function transformedPortraitRect(target, scalePercent, windowSize) {
  if (!Number.isSafeInteger(scalePercent) || scalePercent < 50 || scalePercent > 150) {
    throw new Error("invalid portraitScalePercent");
  }
  const [x, y, baseWidth, baseHeight] = target;
  const [windowWidth] = windowSize;
  const centerX = x + baseWidth / 2;
  const bottom = y + baseHeight;
  const maxWidth = 2 * Math.min(centerX, windowWidth - centerX);
  const effectiveScale = Math.min(scalePercent / 100, maxWidth / baseWidth, bottom / baseHeight);
  const width = Math.ceil(baseWidth * effectiveScale);
  const height = Math.ceil(baseHeight * effectiveScale);
  return [Math.floor(centerX - width / 2), Math.floor(bottom - height), width, height];
}

export function computeHitRegions(
  layout,
  { portraitSourceSize = null, portraitScalePercent = 100 } = {},
) {
  if (!Array.isArray(layout?.windowSize) || layout.windowSize.length !== 2) {
    throw new Error("invalid windowSize");
  }
  const portraitSlot = copyRect(layout.portraitRect, "portraitRect", layout.windowSize);
  const portrait = copyRect(
    transformedPortraitRect(
      containedPortraitRect(portraitSlot, portraitSourceSize),
      portraitScalePercent,
      layout.windowSize,
    ),
    "transformedPortraitRect",
    layout.windowSize,
  );
  const bubble = layout.bubbleVisible === false
    ? null
    : optionalRect(layout.bubbleRect, "bubbleRect", layout.windowSize);
  const input = layout.inputVisible === false
    ? null
    : optionalRect(layout.inputRect, "inputRect", layout.windowSize);
  const controls = layout.bubbleVisible === false
    ? null
    : copyRect(layout.controlsRect, "controlsRect", layout.windowSize);
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

export function clearTextSelection(selection) {
  if (
    !selection
    || selection.rangeCount === 0
    || selection.isCollapsed === true
    || typeof selection.removeAllRanges !== "function"
  ) {
    return false;
  }
  selection.removeAllRanges();
  return true;
}

export function shouldOpenProductMenu({ hitKind, button }) {
  return HIT_KINDS.includes(hitKind) && button === 2;
}

export function classifyPointerHit({ model, point, interactiveTarget = false }) {
  return interactiveTarget ? "interactive" : classifyHitPoint(model, point);
}
