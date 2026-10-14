function validAnchor(value) {
  return value
    && typeof value === "object"
    && Number.isSafeInteger(value.x)
    && Number.isSafeInteger(value.y);
}

export function shouldRevealBubbleAfterNativeDrag({ bubbleWasHidden, initialAnchor, result }) {
  if (!bubbleWasHidden) return false;
  const finalAnchor = result?.portraitAnchor;
  if (!validAnchor(initialAnchor) || !validAnchor(finalAnchor)) return true;
  return initialAnchor.x === finalAnchor.x && initialAnchor.y === finalAnchor.y;
}
