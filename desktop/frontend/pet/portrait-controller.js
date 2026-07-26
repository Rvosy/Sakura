export const PORTRAIT_ASSETS = Object.freeze({
  idle: "./assets/sakura-idle.svg",
  calm: "./assets/sakura-idle.svg",
  thinking: "./assets/sakura-thinking.svg",
  bright: "./assets/sakura-smile.svg",
  smile: "./assets/sakura-smile.svg",
  concerned: "./assets/sakura-concerned.svg",
});

export function createPortraitController({
  assets = PORTRAIT_ASSETS,
  loadImage,
  preview = () => {},
  commit = () => {},
  showFallback = () => {},
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  transitionMs = 180,
  reducedMotion = false,
} = {}) {
  if (typeof loadImage !== "function") throw new Error("portrait loadImage is required");
  let token = 0;
  let timer = null;
  let pendingResolve = null;
  let currentKey = null;

  function clearTransition() {
    if (timer != null) clearTimer(timer);
    timer = null;
    if (pendingResolve) pendingResolve(Object.freeze({ applied: false, key: null }));
    pendingResolve = null;
  }

  return Object.freeze({
    async show(requestedKey, { immediate = false } = {}) {
      const key = Object.hasOwn(assets, requestedKey) ? requestedKey : "idle";
      const source = assets[key];
      const requestToken = ++token;
      clearTransition();
      try {
        const image = await loadImage(source);
        if (requestToken !== token) return Object.freeze({ applied: false, key });
        if (immediate || reducedMotion || currentKey === null || currentKey === key) {
          currentKey = key;
          commit({ key, source, image });
          return Object.freeze({ applied: true, key });
        }
        preview({ key, source, image });
        return await new Promise((resolve) => {
          pendingResolve = resolve;
          timer = setTimer(() => {
            timer = null;
            pendingResolve = null;
            if (requestToken !== token) return resolve(Object.freeze({ applied: false, key }));
            currentKey = key;
            commit({ key, source, image });
            resolve(Object.freeze({ applied: true, key }));
          }, Math.max(0, transitionMs));
        });
      } catch {
        if (requestToken !== token) return Object.freeze({ applied: false, key });
        showFallback({ key, source });
        return Object.freeze({ applied: false, key, failed: true });
      }
    },
    dispose() {
      token += 1;
      clearTransition();
    },
    current() {
      return currentKey;
    },
  });
}
