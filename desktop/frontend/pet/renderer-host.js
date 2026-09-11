import { normalizeVisualControl } from "./visual-control.js";

export function createRendererHost({ container, loadModule = (url) => import(url), services = {}, onUnavailable = () => {}, onError = () => {}, timeoutMs = 10000 }) {
  let binding = null;
  let instance = null;
  let lifetime = null;
  let operation = null;
  let ready = Promise.resolve(false);
  let epoch = 0;
  let staged = null;
  const report = (code) => onUnavailable(code);
  const bounded = (promise) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("VISUAL_RENDERER_TIMEOUT")), timeoutMs);
    Promise.resolve(promise).then(resolve, reject).finally(() => clearTimeout(timer));
  });
  function cancel(reason = "interrupted") {
    const hadOperation = operation !== null;
    operation?.abort.abort();
    operation = null;
    if (hadOperation) services.cancelSurface?.();
    try { if (hadOperation) Promise.resolve(instance?.cancel?.({ reason })).catch(() => {}); } catch { /* display remains optional */ }
  }
  function freeze(reason = "unbound") {
    epoch += 1;
    cancel(reason);
    lifetime?.abort();
    lifetime = null;
    staged?.remove();
    staged = null;
    try { Promise.resolve(instance?.cancel?.({ reason })).catch(() => {}); } catch { /* frozen display is optional */ }
    binding = null;
    ready = Promise.resolve(false);
  }
  function clear(reason = "unbound") {
    freeze(reason);
    try { Promise.resolve(instance?.destroy?.()).catch(() => {}); } catch { /* detach even after plugin failure */ }
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
      try {
        const module = await bounded(loadModule(target.renderer));
        if (signal.aborted) return false;
        if (typeof module.mount !== "function") throw new Error("VISUAL_RENDERER_INVALID");
        const pending = Promise.resolve(module.mount({ container: surface, resource: structuredClone(target), host: guarded, signal }));
        pending.then((late) => { if (signal.aborted) late?.destroy?.(); }, () => {}).catch(() => {});
        candidate = await bounded(pending);
        if (signal.aborted) return false;
        if (!candidate || typeof candidate.applyState !== "function" || typeof candidate.destroy !== "function") throw new Error("VISUAL_RENDERER_INVALID");
        let retired = false;
        const retire = () => {
          if (retired) return;
          retired = true;
          try { Promise.resolve(candidate.destroy()).catch(() => {}); } catch { /* always detach */ }
        };
        signal.addEventListener("abort", retire, { once: true });
        await bounded(candidate.ready);
        if (signal.aborted) return false;
        signal.removeEventListener("abort", retire);
        try { Promise.resolve(instance?.destroy?.()).catch(() => {}); } catch { /* replacement is ready */ }
        instance = candidate;
        surface.className = "visual-renderer-surface";
        container.replaceChildren(surface);
        staged = null;
        return true;
      } catch (error) {
        if (epoch === current) { clear("renderer_failed"); report(String(error?.message || "VISUAL_RENDERER_FAILED")); }
        return false;
      }
    })();
    return ready;
  }
  function begin(operationId) {
    if (!operationId || operation?.id === operationId) return;
    cancel("operation_changed");
    operation = { id: operationId, seen: new Set(), abort: new AbortController() };
  }
  async function play(control, operationId, segmentIndex) {
    const value = normalizeVisualControl(control);
    const current = operation;
    const target = binding;
    if (!value || !target || !current || current.id !== operationId || !Number.isSafeInteger(segmentIndex) || segmentIndex < 0
      || value.bindingId !== target.bindingId || value.resourceId !== target.resourceId || current.seen.has(segmentIndex)) return false;
    current.seen.add(segmentIndex);
    if (!await ready || current !== operation || current.abort.signal.aborted || target !== binding) return false;
    const context = { operationId, segmentIndex, signal: current.abort.signal };
    try {
      if (Object.hasOwn(value, "state")) await instance.applyState(value.state, context);
      if (current !== operation || current.abort.signal.aborted || target !== binding) return false;
      for (const action of value.actions || []) {
        if (current.abort.signal.aborted || current !== operation) return false;
        await instance.perform?.(action, context);
      }
      return true;
    } catch {
      if (current === operation && !current.abort.signal.aborted && target === binding) onError("VISUAL_CONTROL_EXECUTION_FAILED");
      return false;
    }
  }
  return Object.freeze({ bind, begin, play, cancel, freeze, clear, destroy: () => clear("destroyed"), current: () => binding });
}
