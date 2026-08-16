export const WAITING_INDICATOR_INTERVAL_MS = 360;
export const WAITING_INDICATOR_FRAMES = Object.freeze([".", "..", "...", "....", ".....", "......", "....."]);

export function createWaitingIndicator({
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  reducedMotion = false,
  onFrame = () => {},
} = {}) {
  let generation = 0;
  let timer = null;
  let running = false;
  let frameIndex = 0;

  function clearActiveTimer() {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function schedule(token) {
    if (!running || reducedMotion || token !== generation) return;
    timer = setTimer(() => {
      timer = null;
      if (!running || token !== generation) return;
      frameIndex = (frameIndex + 1) % WAITING_INDICATOR_FRAMES.length;
      onFrame(WAITING_INDICATOR_FRAMES[frameIndex]);
      schedule(token);
    }, WAITING_INDICATOR_INTERVAL_MS);
  }

  function stop() {
    generation += 1;
    running = false;
    clearActiveTimer();
  }

  return Object.freeze({
    start() {
      generation += 1;
      clearActiveTimer();
      running = true;
      frameIndex = 0;
      onFrame(reducedMotion ? "..." : WAITING_INDICATOR_FRAMES[frameIndex]);
      schedule(generation);
    },
    stop,
    stopWhenSettled(gate) {
      const token = generation;
      return Promise.resolve(gate).finally(() => {
        if (running && token === generation) stop();
      });
    },
    active() {
      return running;
    },
    dispose() {
      stop();
    },
  });
}
