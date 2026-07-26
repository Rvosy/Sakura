export function createPortraitController({
  assets,
  defaultKey,
  loadImage,
  preview = () => {},
  commit = () => {},
  showFallback = () => {},
  reportError = () => {},
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  transitionMs = 180,
  reducedMotion = false,
} = {}) {
  if (!assets || typeof assets !== "object" || !Object.hasOwn(assets, defaultKey)) {
    throw new Error("portrait assets and defaultKey are required");
  }
  if (typeof loadImage !== "function") throw new Error("portrait loadImage is required");
  let token = 0;
  let timer = null;
  let pendingResolve = null;
  let currentKey = null;
  let generationId = null;

  function clearTransition() {
    if (timer != null) clearTimer(timer);
    timer = null;
    if (pendingResolve) pendingResolve(Object.freeze({ applied: false, key: null }));
    pendingResolve = null;
  }

  function beginGeneration(nextGenerationId) {
    const normalized = String(nextGenerationId || "");
    if (!normalized || normalized === generationId) return false;
    generationId = normalized;
    token += 1;
    clearTransition();
    return true;
  }

  return Object.freeze({
    beginGeneration,
    async show(requestedKey, { immediate = false, generation = generationId } = {}) {
      if (!generation || generation !== generationId) {
        return Object.freeze({ applied: false, key: null, staleGeneration: true });
      }
      const known = Object.hasOwn(assets, requestedKey);
      const key = known ? requestedKey : defaultKey;
      if (!known) reportError({ code: "PORTRAIT_KEY_UNKNOWN", requestedKey, fallbackKey: key });
      const source = assets[key];
      const requestToken = ++token;
      clearTransition();
      try {
        const image = await loadImage(source);
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        if (immediate || reducedMotion || currentKey === null || currentKey === key) {
          currentKey = key;
          commit({ key, source, image });
          return Object.freeze({ applied: true, key, recoveredUnknownKey: !known });
        }
        preview({ key, source, image });
        return await new Promise((resolve) => {
          pendingResolve = resolve;
          timer = setTimer(() => {
            timer = null;
            pendingResolve = null;
            if (requestToken !== token || generation !== generationId) {
              return resolve(Object.freeze({ applied: false, key, staleGeneration: true }));
            }
            currentKey = key;
            commit({ key, source, image });
            resolve(Object.freeze({ applied: true, key, recoveredUnknownKey: !known }));
          }, Math.max(0, transitionMs));
        });
      } catch {
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        reportError({ code: "PORTRAIT_DECODE_FAILED", requestedKey: key });
        showFallback({ key, source });
        return Object.freeze({ applied: false, key, failed: true });
      }
    },
    dispose() {
      generationId = null;
      token += 1;
      clearTransition();
    },
    current() {
      return currentKey;
    },
  });
}
