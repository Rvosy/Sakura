// Private editor data never passes through common snake/camel-case conversion.
export function createVisualEditorHost({ container, loadModule = (url) => import(url), onChange, importFiles, assetUrl, onPreview = () => {}, onError = () => {}, timeoutMs = 10000 }) {
  let instance = null;
  let lifetime = null;
  let revision = 0;
  let staged = null;
  const bounded = (promise) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("VISUAL_EDITOR_TIMEOUT")), timeoutMs);
    Promise.resolve(promise).then(resolve, reject).finally(() => clearTimeout(timer));
  });
  function freeze() {
    revision += 1;
    lifetime?.abort();
    staged?.remove();
    staged = null;
    container.inert = true;
  }
  function clear() {
    freeze();
    try { Promise.resolve(instance?.destroy?.()).catch(() => {}); } catch { /* always detach old controls */ }
    instance = null;
    container.replaceChildren();
  }
  async function open(descriptor) {
    freeze();
    const current = revision;
    lifetime = new AbortController();
    const signal = lifetime.signal;
    let previewRevision = 0;
    const pendingAssets = new Map();
    function loadAsset(path) {
      if (signal.aborted) return Promise.resolve(null);
      if (pendingAssets.has(path)) return pendingAssets.get(path);
      const pending = Promise.resolve().then(async () => {
        if (signal.aborted) return null;
        // The native host grants one resource root at open. No per-file IPC,
        // router slot or draft mutation lock is needed to resolve an asset URL.
        const url = descriptor.assetBaseUrl
          ? descriptor.assetBaseUrl + Array.from(new TextEncoder().encode(path), byte => byte.toString(16).padStart(2, "0")).join("")
          : await assetUrl(path);
        return signal.aborted ? null : url;
      });
      pendingAssets.set(path, pending);
      pending.catch(() => pendingAssets.delete(path));
      return pending;
    }
    const target = container.ownerDocument.createElement("div");
    target.className = instance ? "visual-editor-staging" : "";
    staged = target;
    container.append(target);
    try {
    const module = await bounded(loadModule(descriptor.presentation.visual.editor));
    if (signal.aborted) return false;
    if (typeof module.mountEditor !== "function") throw new Error("VISUAL_EDITOR_INVALID");
    const pending = Promise.resolve(module.mountEditor({ container: target, data: structuredClone(descriptor.data), signal, host: {
      changed(data) { if (!signal.aborted && revision === current) onChange(structuredClone(data)); },
      error(error) { if (!signal.aborted) onError(String(error?.message || error)); },
      async importFiles(options = {}) {
        if (signal.aborted) return [];
        const files = await importFiles(options, { signal });
        return signal.aborted ? [] : files;
      },
      assetUrl: loadAsset,
      async previewImage(path) {
        if (signal.aborted) return;
        const preview = ++previewRevision;
        try {
          const url = path ? await loadAsset(path) : null;
          if (!signal.aborted && revision === current && preview === previewRevision) onPreview(url, descriptor.presentation.visual.resourceId, path || null);
        } catch { /* A missing optional thumbnail does not block editing. */ }
      },
    } }));
    pending.then((late) => { if (signal.aborted) late?.destroy?.(); }, () => {}).catch(() => {});
    const candidate = await bounded(pending);
    if (signal.aborted) return false;
    if (!candidate || typeof candidate.collect !== "function" || typeof candidate.validate !== "function" || typeof candidate.destroy !== "function") throw new Error("VISUAL_EDITOR_INVALID");
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
    target.className = target.className.replace("visual-editor-staging", "").trim();
    container.replaceChildren(target);
    container.inert = false;
    staged = null;
    return true;
    } catch (error) {
      if (current !== revision) return false;
      clear();
      throw error;
    }
  }
  return { open, freeze, clear, validate: () => instance?.validate?.() ?? true, collect: () => instance?.collect?.() };
}
