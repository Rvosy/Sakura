import { normalizeVisualControl } from "./visual-control.js";

export function createRendererHost({ container, loadModule = (url) => import(url), services = {}, onUnavailable = () => {}, onError = () => {}, timeoutMs = 10000 }) {
  let binding = null;
  let instance = null;
  let lifetime = null;
  let operation = null;
  let ready = Promise.resolve(false);
  let epoch = 0;
  let staged = null;
  let history = new WeakMap();
  let latestState = null;
  let reviewing = false;
  let reviewSequence = 0;
  const report = (code, error, stage) => onUnavailable(code, error, stage);
  function cleanup(target, method, ...args) {
    const failed = error => { if (error?.name !== "AbortError") onError("VISUAL_RENDERER_CLEANUP_FAILED", error, `visual.renderer.${method}`); };
    try { Promise.resolve(target?.[method]?.(...args)).catch(failed); } catch (error) { failed(error); }
  }
  const bounded = (promise) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("VISUAL_RENDERER_TIMEOUT")), timeoutMs);
    Promise.resolve(promise).then(resolve, reject).finally(() => clearTimeout(timer));
  });
  function cancel(reason = "interrupted", { restoreSurface = true } = {}) {
    const hadOperation = operation !== null;
    operation?.abort.abort();
    operation = null;
    if (hadOperation && restoreSurface) services.cancelSurface?.();
    if (hadOperation) cleanup(instance, "cancel", { reason });
  }
  function freeze(reason = "unbound") {
    epoch += 1;
    history = new WeakMap();
    latestState = null;
    reviewing = false;
    const hadOperation = operation !== null;
    lifetime?.abort();
    lifetime = null;
    // Keep the committed native surface until the replacement is ready. Its old
    // resource may already be revoked when a binding or Core generation changes.
    cancel(reason, { restoreSurface: false });
    staged?.remove();
    staged = null;
    if (!hadOperation) cleanup(instance, "cancel", { reason });
    binding = null;
    ready = Promise.resolve(false);
  }
  function clear(reason = "unbound") {
    freeze(reason);
    cleanup(instance, "destroy");
    instance = null;
    binding = null;
    container.replaceChildren();
  }
  async function bind(presentation) {
    freeze("binding_changed");
    const current = epoch;
    binding = presentation?.visual || null;
    if (!binding) {
      if (presentation?.visualReasonCode === "VISUAL_NOT_BOUND") return false;
      clear();
      report(presentation?.visualReasonCode || "VISUAL_NOT_BOUND");
      return false;
    }
    lifetime = new AbortController();
    const signal = lifetime.signal;
    const target = binding;
    const surface = container.ownerDocument.createElement("div");
    surface.className = "visual-renderer-surface visual-renderer-staging";
    staged = surface;
    container.append(surface);
    const guarded = Object.fromEntries(Object.entries(services).map(([name, method]) => [name, (...args) => {
      if (signal.aborted || epoch !== current) return Promise.resolve(false);
      return method(...args, { signal, operationSignal: operation?.abort.signal, visual: target });
    }]));
    ready = (async () => {
      let candidate;
      let stage = "visual.renderer.module";
      try {
        const module = await bounded(loadModule(target.renderer));
        if (signal.aborted) return false;
        if (typeof module.mount !== "function") throw new Error("VISUAL_RENDERER_INVALID");
        stage = "visual.renderer.mount";
        const pending = Promise.resolve(module.mount({ container: surface, resource: structuredClone(target), host: guarded, signal }));
        pending.then((late) => { if (signal.aborted) cleanup(late, "destroy"); }, () => {});
        candidate = await bounded(pending);
        if (signal.aborted) return false;
        if (!candidate || typeof candidate.applyState !== "function" || typeof candidate.destroy !== "function") throw new Error("VISUAL_RENDERER_INVALID");
        let retired = false;
        const retire = () => {
          if (retired) return;
          retired = true;
          cleanup(candidate, "destroy");
        };
        signal.addEventListener("abort", retire, { once: true });
        stage = "visual.renderer.ready";
        await bounded(candidate.ready);
        if (signal.aborted) return false;
        signal.removeEventListener("abort", retire);
        cleanup(instance, "destroy");
        instance = candidate;
        surface.className = "visual-renderer-surface";
        container.replaceChildren(surface);
        staged = null;
        return true;
      } catch (error) {
        if (epoch === current) { clear("renderer_failed"); report("VISUAL_RENDERER_FAILED", error, stage); }
        return false;
      }
    })();
    return ready;
  }
  function begin(operationId) {
    if (!operationId || operation?.id === operationId) return;
    cancel("operation_changed");
    const current = { id: operationId, seen: new Set(), abort: new AbortController() };
    operation = current;
    const resume = reviewing ? latestState : null;
    reviewing = false;
    current.restored = !resume ? ready : (async () => {
      if (!await ready || current !== operation || current.abort.signal.aborted) return false;
      if (resume) await instance.applyState(structuredClone(resume.state), {
        operationId, segmentIndex: -1, signal: current.abort.signal,
      });
      return current === operation && !current.abort.signal.aborted;
    })().catch(error => {
      if (current === operation && !current.abort.signal.aborted) onError("VISUAL_CONTROL_EXECUTION_FAILED", error, "visual.history.resume");
      return false;
    });
  }
  function remember(historyKey, target) {
    if (typeof instance.snapshotState !== "function") return;
    try {
      const state = instance.snapshotState();
      if (state === undefined) return;
      const snapshot = normalizeVisualControl({ version: 1, bindingId: target.bindingId,
        resourceId: target.resourceId, state });
      if (!snapshot) return;
      latestState = snapshot;
      if (historyKey && typeof historyKey === "object") history.set(historyKey, snapshot);
    } catch (error) {
      onError("VISUAL_STATE_SNAPSHOT_FAILED", error, "visual.history.snapshot");
    }
  }

  async function review(historyKey) {
    const snapshot = history.get(historyKey);
    const target = binding;
    cancel("history");
    if (!snapshot || !target || snapshot.bindingId !== target.bindingId || snapshot.resourceId !== target.resourceId) return false;
    const current = { id: `history-${++reviewSequence}`, abort: new AbortController() };
    operation = current;
    reviewing = true;
    if (!await ready || current !== operation || current.abort.signal.aborted || target !== binding) return false;
    try {
      await instance.applyState(structuredClone(snapshot.state), {
        operationId: current.id, segmentIndex: -1, signal: current.abort.signal,
      });
      return current === operation && !current.abort.signal.aborted && target === binding;
    } catch (error) {
      if (current === operation && !current.abort.signal.aborted && target === binding) onError("VISUAL_CONTROL_EXECUTION_FAILED", error, "visual.history.review");
      return false;
    }
  }
  async function play(control, operationId, segmentIndex, historyKey) {
    const value = normalizeVisualControl(control);
    const current = operation;
    const target = binding;
    if ((control != null && !value) || !target || !current || current.id !== operationId || !Number.isSafeInteger(segmentIndex) || segmentIndex < 0
      || (value && (value.bindingId !== target.bindingId || value.resourceId !== target.resourceId)) || !current.seen || current.seen.has(segmentIndex)) return false;
    current.seen.add(segmentIndex);
    if (!await current.restored || current !== operation || current.abort.signal.aborted || target !== binding) return false;
    const context = { operationId, segmentIndex, signal: current.abort.signal };
    try {
      if (value && Object.hasOwn(value, "state")) await instance.applyState(value.state, context);
      if (current !== operation || current.abort.signal.aborted || target !== binding) return false;
      remember(historyKey, target);
      for (const action of value?.actions || []) {
        if (current.abort.signal.aborted || current !== operation) return false;
        await instance.perform?.(action, context);
      }
      return true;
    } catch (error) {
      if (current === operation && !current.abort.signal.aborted && target === binding) onError("VISUAL_CONTROL_EXECUTION_FAILED", error, "visual.control.execute");
      return false;
    }
  }
  return Object.freeze({ bind, begin, play, review, cancel, freeze, clear, destroy: () => clear("destroyed"), current: () => binding });
}
