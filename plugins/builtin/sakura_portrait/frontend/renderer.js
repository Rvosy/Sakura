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
      let failureCode = "PORTRAIT_DECODE_FAILED";
      try {
        const image = await decodedImage(source);
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        if (immediate || currentKey === null) {
          failureCode = "PORTRAIT_COMMIT_FAILED";
          const commitResult = await commit({ key, source, image });
          if (requestToken !== token || generation !== generationId) {
            return Object.freeze({ applied: false, key, staleGeneration: true });
          }
          if (commitResult === false) return Object.freeze({ applied: false, key });
          currentKey = key;
          notifyVisualReady();
          return Object.freeze({ applied: true, key, recoveredUnknownKey: !known });
        }
        failureCode = "PORTRAIT_PREPARE_FAILED";
        let prepared = preview({ key, source, image });
        if (prepared && typeof prepared.then === "function") prepared = await prepared;
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        if (prepared === false) { cancelPreview(); return Object.freeze({ applied: false, key }); }
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
            const complete = (result) => {
              if (requestToken !== token || generation !== generationId) {
                return resolve(Object.freeze({ applied: false, key, staleGeneration: true }));
              }
              if (result === false) { cancelPreview(); return resolve(Object.freeze({ applied: false, key })); }
              currentKey = key;
              resolve(Object.freeze({ applied: true, key, recoveredUnknownKey: !known }));
            };
            const failed = (error) => {
              if (requestToken !== token || generation !== generationId) {
                return resolve(Object.freeze({ applied: false, key, staleGeneration: true }));
              }
              cancelPreview();
              reportError({ code: "PORTRAIT_COMMIT_FAILED", requestedKey: key, error });
              resolve(Object.freeze({ applied: false, key, failed: true }));
            };
            try {
              const commitResult = commit({ key, source, image });
              if (commitResult && typeof commitResult.then === "function") {
                commitResult.then(complete).catch(failed);
              } else {
                complete(commitResult);
              }
            } catch (error) {
              failed(error);
            }
          }, Math.max(0, transitionMs));
        });
      } catch (error) {
        if (requestToken !== token || generation !== generationId) {
          return Object.freeze({ applied: false, key, staleGeneration: true });
        }
        if (failureCode !== "PORTRAIT_DECODE_FAILED") cancelPreview();
        reportError({ code: failureCode, requestedKey: key, error });
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
      } catch (error) {
        if (generation !== generationId) {
          return Object.freeze({ loaded: false, key, staleGeneration: true });
        }
        reportError({ code: "PORTRAIT_DECODE_FAILED", requestedKey: key, error });
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

const STYLES = ".portrait-frame {\n  position: relative;\n  width: 100%;\n  height: 100%;\n  overflow: visible;\n  background: transparent;\n}\n\n.portrait-image {\n  position: absolute;\n  inset: 0;\n  display: block;\n  width: 100%;\n  height: 100%;\n  object-fit: contain;\n  object-position: center bottom;\n  background: transparent;\n  border: 0;\n  outline: 0;\n  -webkit-user-drag: none;\n  -webkit-user-select: none;\n  user-select: none;\n}\n\n/*\n * An image element without a source is still laid out at the full portrait\n * size by WebView2.  When it has alt text, WebView2 paints the native broken\n * image frame (a bright rectangle) even though the element itself is meant to\n * be transparent.  This is visible during core startup and for the spare\n * cross-fade layer after its source is cleared.  Keep empty layers out of the\n * paint tree until a decoded source has been committed.\n */\n.portrait-image:not([src]) { visibility: hidden; }\n\n.portrait-image--current {\n  opacity: 1;\n}\n\n.portrait-image--next {\n  opacity: 0;\n}\n\n.is-transitioning .portrait-image--current {\n  animation: portrait-current-fade-out 250ms ease forwards;\n}\n\n.is-transitioning .portrait-image--next {\n  animation: portrait-next-fade-in 250ms ease 50ms forwards;\n}\n\n@keyframes portrait-current-fade-out {\n  from { opacity: 1; }\n  to { opacity: 0; }\n}\n\n@keyframes portrait-next-fade-in {\n  from { opacity: 0; }\n  to { opacity: 1; }\n}\n\n";

export function mount({ container, resource, host, signal }) {
  const root = container.attachShadow({ mode: "open" });
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(STYLES);
  root.adoptedStyleSheets = [sheet];
  const frame = document.createElement("div");
  frame.className = "portrait-frame";
  const current = document.createElement("img");
  const next = document.createElement("img");
  current.className = "portrait-image portrait-image--current";
  next.className = "portrait-image portrait-image--next";
  for (const image of [current, next]) { image.draggable = false; image.alt = ""; }
  frame.append(current, next);
  root.append(frame);
  let loadError;
  const assets = resource.assets;
  const data = resource.data;
  let transition = false;
  let generation = 1;
  const resetPreview = () => { transition = false; frame.classList.remove("is-transitioning"); next.removeAttribute("src"); };
  const controller = createPortraitController({
    assets, defaultKey: data.defaultKey,
    loadImage: (source) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = async () => {
        try {
          await image.decode();
          const key = Object.keys(assets).find((key) => assets[key] === source);
          const expected = data.metadata[key];
          if (image.naturalWidth !== expected.width || image.naturalHeight !== expected.height) throw new Error("PORTRAIT_DIMENSION_MISMATCH");
          resolve({ width: image.naturalWidth, height: image.naturalHeight });
        } catch (error) { reject(error); }
      };
      image.onerror = () => reject(new Error("PORTRAIT_LOAD_FAILED"));
      image.src = source;
    }),
    preview: async ({ key, source }) => {
      const previewGeneration = generation;
      const prepared = await host.prepareSurface({ assetKey: key });
      if (signal.aborted || previewGeneration !== generation || prepared === false) return false;
      transition = true;
      next.src = source;
      frame.classList.remove("is-transitioning");
      void frame.offsetWidth;
      frame.classList.add("is-transitioning");
      return true;
    },
    cancelPreview: () => { resetPreview(); host.cancelSurface?.(); },
    commit: async ({ key, source, image }) => {
      const commitGeneration = generation;
      if (signal.aborted) return false;
      const applied = await host.setSurface({ assetKey: key, width: image.width, height: image.height });
      if (signal.aborted || commitGeneration !== generation || applied === false) return false;
      current.src = source;
      frame.classList.remove("is-transitioning");
      next.removeAttribute("src");
      if (transition) {
        transition = false;
        try { await host.finishSurface(); }
        catch (error) { host.reportError("PORTRAIT_SURFACE_FINISH_FAILED", error); }
      }
      return true;
    },
    showFallback: () => host.unavailable("PORTRAIT_DECODE_FAILED"),
    reportError: ({ code, error }) => { loadError = error; host.reportError(code, error); },
  });
  controller.beginGeneration(String(generation));
  const ready = controller.show(data.defaultKey, { immediate: true, generation: String(generation) }).then((result) => {
    if (!result.applied && !signal.aborted) throw loadError || new Error("PORTRAIT_DECODE_FAILED");
  });
  return {
    ready,
    applyState(state, context) {
      if (signal.aborted || context.signal.aborted) return;
      return controller.show(state?.key || data.defaultKey, { generation: String(generation) });
    },
    perform() {},
    cancel() { controller.beginGeneration(String(++generation)); resetPreview(); },
    destroy() { controller.dispose(); container.replaceChildren(); },
  };
}
