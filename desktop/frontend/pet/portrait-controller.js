export function createPortraitController({
  assets,
  defaultKey,
  loadImage,
  preview = () => {},
  cancelPreview = () => {},
  commit = () => {},
  showFallback = () => {},
  reportError = () => {},
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  transitionMs = 300,
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
  let previewActive = false;
  const imageCache = new Map();

  function decodedImage(source) {
    if (imageCache.has(source)) return imageCache.get(source);
    let pending;
    try {
      pending = Promise.resolve(loadImage(source));
    } catch (error) {
      pending = Promise.reject(error);
    }
    imageCache.set(source, pending);
    pending.catch(() => imageCache.delete(source));
    return pending;
  }

  function clearTransition() {
    if (timer != null) clearTimer(timer);
    timer = null;
    if (pendingResolve) pendingResolve(Object.freeze({ applied: false, key: null }));
    pendingResolve = null;
    if (previewActive) {
      previewActive = false;
      cancelPreview();
    }
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
    async show(requestedKey, {
      immediate = false,
      generation = generationId,
      onVisualReady = () => {},
    } = {}) {
      if (!generation || generation !== generationId) {
        return Object.freeze({ applied: false, key: null, staleGeneration: true });
      }
      let visualReadyNotified = false;
      const notifyVisualReady = () => {
        if (visualReadyNotified) return;
        visualReadyNotified = true;
        onVisualReady();
      };
      const known = Object.hasOwn(assets, requestedKey);
      const key = known ? requestedKey : defaultKey;
      if (!known) reportError({ code: "PORTRAIT_KEY_UNKNOWN", requestedKey, fallbackKey: key });
      const source = assets[key];
      if (currentKey === key) {
        token += 1;
        clearTransition();
        notifyVisualReady();
        return Object.freeze({ applied: true, key, unchanged: true, recoveredUnknownKey: !known });
      }
      const requestToken = ++token;
      clearTransition();
      try {
        const image = await decodedImage(source);
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        if (immediate || reducedMotion || currentKey === null) {
          const commitResult = commit({ key, source, image });
          if (commitResult && typeof commitResult.then === "function") await commitResult;
          if (requestToken !== token || generation !== generationId) {
            return Object.freeze({ applied: false, key, staleGeneration: true });
          }
          currentKey = key;
          notifyVisualReady();
          return Object.freeze({ applied: true, key, recoveredUnknownKey: !known });
        }
        const previewResult = preview({ key, source, image });
        if (previewResult && typeof previewResult.then === "function") await previewResult;
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        notifyVisualReady();
        previewActive = true;
        return await new Promise((resolve) => {
          pendingResolve = resolve;
          timer = setTimer(() => {
            timer = null;
            pendingResolve = null;
            if (requestToken !== token || generation !== generationId) {
              return resolve(Object.freeze({ applied: false, key, staleGeneration: true }));
            }
            previewActive = false;
            const complete = () => {
              if (requestToken !== token || generation !== generationId) {
                return resolve(Object.freeze({ applied: false, key, staleGeneration: true }));
              }
              currentKey = key;
              resolve(Object.freeze({ applied: true, key, recoveredUnknownKey: !known }));
            };
            try {
              const commitResult = commit({ key, source, image });
              if (commitResult && typeof commitResult.then === "function") {
                commitResult.then(complete).catch(() => {
                  reportError({ code: "PORTRAIT_COMMIT_FAILED", requestedKey: key });
                  resolve(Object.freeze({ applied: false, key, failed: true }));
                });
              } else {
                complete();
              }
            } catch {
              reportError({ code: "PORTRAIT_COMMIT_FAILED", requestedKey: key });
              resolve(Object.freeze({ applied: false, key, failed: true }));
            }
          }, Math.max(0, transitionMs));
        });
      } catch {
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        reportError({ code: "PORTRAIT_DECODE_FAILED", requestedKey: key });
        if (currentKey === null) showFallback({ key, source });
        notifyVisualReady();
        return Object.freeze({ applied: false, key, failed: true });
      }
    },
    async preload(requestedKey, { generation = generationId } = {}) {
      if (!generation || generation !== generationId) {
        return Object.freeze({ loaded: false, key: null, staleGeneration: true });
      }
      const known = Object.hasOwn(assets, requestedKey);
      const key = known ? requestedKey : defaultKey;
      if (!known) reportError({ code: "PORTRAIT_KEY_UNKNOWN", requestedKey, fallbackKey: key });
      try {
        await decodedImage(assets[key]);
        if (generation !== generationId) {
          return Object.freeze({ loaded: false, key, staleGeneration: true });
        }
        return Object.freeze({ loaded: true, key, recoveredUnknownKey: !known });
      } catch {
        if (generation !== generationId) {
          return Object.freeze({ loaded: false, key, staleGeneration: true });
        }
        reportError({ code: "PORTRAIT_DECODE_FAILED", requestedKey: key });
        return Object.freeze({ loaded: false, key, failed: true });
      }
    },
    dispose() {
      generationId = null;
      token += 1;
      clearTransition();
      imageCache.clear();
    },
    current() {
      return currentKey;
    },
  });
}
