export function createSessionBootstrapLoader({ fetchBootstrap, applyBootstrap }) {
  let loadedSessionGeneration = null;
  let loadingSessionGeneration = null;
  let loadingPromise = null;
  let epoch = 0;

  async function load(brain, { force = false } = {}) {
    const sessionGeneration = brain?.sessionGeneration;
    if (!brain?.acceptingRequests || sessionGeneration == null) return null;
    if (!force && sessionGeneration === loadedSessionGeneration) return null;

    if (loadingPromise && sessionGeneration === loadingSessionGeneration) {
      if (!force) return loadingPromise;
      const waitingEpoch = epoch;
      await loadingPromise.catch(() => null);
      if (waitingEpoch !== epoch) return null;
      return load(brain, { force: true });
    }

    const loadingEpoch = epoch;
    let task;
    task = (async () => {
      const bootstrap = await fetchBootstrap();
      if (loadingEpoch !== epoch) return null;
      loadedSessionGeneration = sessionGeneration;
      applyBootstrap(bootstrap);
      return bootstrap;
    })();
    loadingPromise = task;
    loadingSessionGeneration = sessionGeneration;
    try {
      return await task;
    } finally {
      if (loadingPromise === task) {
        loadingPromise = null;
        loadingSessionGeneration = null;
      }
    }
  }

  function reset() {
    epoch += 1;
    loadedSessionGeneration = null;
    loadingSessionGeneration = null;
    loadingPromise = null;
  }

  return { load, reset };
}
