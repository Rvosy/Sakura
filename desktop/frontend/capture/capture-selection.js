export function normalizedSelection(start, current) {
  if (!start || !current) return null;
  const x = Math.min(start.x, current.x);
  const y = Math.min(start.y, current.y);
  return Object.freeze({
    x,
    y,
    width: Math.abs(current.x - start.x),
    height: Math.abs(current.y - start.y),
  });
}

export function selectionAccepted(selection, minimum = 8) {
  return Boolean(
    selection
    && Number.isFinite(selection.x)
    && Number.isFinite(selection.y)
    && selection.width >= minimum
    && selection.height >= minimum
  );
}
