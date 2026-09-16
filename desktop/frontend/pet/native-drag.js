const PET_DRAG_ERROR_CODES = Object.freeze([
  "PET_DRAG_REVISION_STALE",
  "PET_DRAG_POINT_REJECTED",
]);

function errorText(error) {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    for (const value of [error.code, error.message, error.error]) {
      if (typeof value === "string" && value) return value;
    }
  }
  return String(error ?? "");
}

export function nativePetDragErrorCode(error) {
  const text = errorText(error);
  return PET_DRAG_ERROR_CODES.find((code) => text.includes(code)) || text;
}

export function isNativePetDragPointRejected(error) {
  return nativePetDragErrorCode(error) === "PET_DRAG_POINT_REJECTED";
}

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

export async function startNativePetDragWithRevisionRecovery({
  start,
  readSurfaceDiagnostics,
  syncSurface,
  getPoint = null,
  revision,
  point,
}) {
  try {
    return await start({ revision, point });
  } catch (error) {
    if (nativePetDragErrorCode(error) !== "PET_DRAG_REVISION_STALE") throw error;
    let diagnostics;
    try {
      diagnostics = await readSurfaceDiagnostics();
      if (!Number.isSafeInteger(diagnostics?.revision) || diagnostics.revision < 0) {
        throw new Error("PET_SURFACE_DIAGNOSTICS_INVALID");
      }
      await syncSurface(diagnostics);
    } catch {
      throw error;
    }
    return start({
      revision: diagnostics.revision,
      point: typeof getPoint === "function" ? getPoint() : point,
    });
  }
}
