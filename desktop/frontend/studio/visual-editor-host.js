// Private editor data never passes through common snake/camel-case conversion.
export function createVisualEditorHost({ container, loadModule = (url) => import(url), onChange, importFiles, assetUrl, onPreview = () => {}, onError = () => {}, timeoutMs = 10000 }) {
  let instance = null;
  let lifetime = null;
  let revision = 0;
  let staged = null;
  let viewKey = null;
  const views = new Map();
  const bounded = (promise) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("VISUAL_EDITOR_TIMEOUT")), timeoutMs);
    Promise.resolve(promise).then(resolve, reject).finally(() => clearTimeout(timer));
  });
  function cleanup(target) {
    const failed = error => { if (error?.name !== "AbortError") onError(error, "studio.visual.destroy"); };
    try { Promise.resolve(target?.destroy?.()).catch(failed); } catch (error) { failed(error); }
  }
  function freeze() {
    if (viewKey && typeof instance?.snapshotView === 'function') {
      try { views.set(viewKey, structuredClone(instance.snapshotView())); }
      catch (error) { onError(error, 'studio.visual.view.snapshot'); }
    }
    revision += 1;
    lifetime?.abort();
    staged?.remove();
    staged = null;
    container.inert = true;
  }
  function clear() {
    freeze();
    cleanup(instance);
    instance = null;
    viewKey = null;
    views.clear();
    container.replaceChildren();
  }
  async function open(descriptor) {
    freeze();
    const current = revision;
    const nextViewKey = JSON.stringify([descriptor.presentation.visual.providerId, descriptor.presentation.visual.resourceId]);
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
    let stage = "studio.visual.module";
    try {
    const module = await bounded(loadModule(descriptor.presentation.visual.editor));
    if (signal.aborted) return false;
    if (typeof module.mountEditor !== "function") throw new Error("VISUAL_EDITOR_INVALID");
    stage = "studio.visual.mount";
    const pending = Promise.resolve(module.mountEditor({ container: target, data: structuredClone(descriptor.data), signal, host: {
      changed(data) { if (!signal.aborted && revision === current) onChange(structuredClone(data)); },
      error(error, stage = "studio.visual.editor") { if (!signal.aborted) onError(error, stage); },
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
        } catch (error) {
          if (!signal.aborted && revision === current && preview === previewRevision) onError(error, "studio.visual.preview");
        }
      },
    } }));
    pending.then((late) => { if (signal.aborted) cleanup(late); }, () => {});
    const candidate = await bounded(pending);
    if (signal.aborted) return false;
    if (!candidate || typeof candidate.collect !== "function" || typeof candidate.validate !== "function" || typeof candidate.destroy !== "function") throw new Error("VISUAL_EDITOR_INVALID");
    let retired = false;
    const retire = () => {
      if (retired) return;
      retired = true;
      cleanup(candidate);
    };
    signal.addEventListener("abort", retire, { once: true });
    stage = "studio.visual.ready";
    await bounded(candidate.ready);
    if (signal.aborted) return false;
    if (views.has(nextViewKey) && typeof candidate.restoreView === 'function') {
      try { candidate.restoreView(structuredClone(views.get(nextViewKey))); }
      catch (error) { onError(error, 'studio.visual.view.restore'); }
    }
    signal.removeEventListener("abort", retire);
    cleanup(instance);
    instance = candidate;
    viewKey = nextViewKey;
    target.className = target.className.replace("visual-editor-staging", "").trim();
    container.replaceChildren(target);
    container.inert = false;
    staged = null;
    return true;
    } catch (error) {
      if (current !== revision) return false;
      clear();
      onError(error, stage);
      throw error;
    }
  }
  return { open, freeze, clear, validate: () => instance?.validate?.() ?? true, collect: () => instance?.collect?.() };
}

function validateThumbnail(url) {
  if (typeof url !== 'string' || !url.startsWith('data:image/png;base64,') || url.length > 2 * 1024 * 1024) throw new Error('VISUAL_PREVIEW_INVALID');
  return url;
}

export async function renderVisualThumbnail(descriptor, signal, doc = document) {
  const abort = new AbortController();
  const cancel = () => abort.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, 10000);
  const container = doc.createElement('div');
  Object.assign(container.style, { position: 'fixed', left: '-10000px', width: '192px', height: '256px', visibility: 'hidden', pointerEvents: 'none' });
  doc.body.append(container);
  try {
    if (signal.aborted) return null;
    const module = await import(descriptor.presentation.visual.editor);
    if (abort.signal.aborted || typeof module.renderThumbnail !== 'function') return null;
    const url = await module.renderThumbnail({ container, data: structuredClone(descriptor.data), signal: abort.signal,
      host: { assetUrl: path => descriptor.assetBaseUrl + Array.from(new TextEncoder().encode(path), byte => byte.toString(16).padStart(2, '0')).join('') } });
    return abort.signal.aborted || url === null ? null : validateThumbnail(url);
  } finally {
    abort.abort(); clearTimeout(timer); signal.removeEventListener('abort', cancel); container.remove();
  }
}
