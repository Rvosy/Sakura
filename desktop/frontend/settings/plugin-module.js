import { importPluginModule } from "../core/plugin-module-loader.js";

// Installed plugins supply a self-contained ES module through their settings descriptor.
export function createPluginModule({ document, source, context, onError, onReady = () => {},
  importSource = importPluginModule }) {
  const element = document.createElement("div");
  element.className = "plugin-module";
  const lifetime = new AbortController();
  let component = null;
  let failure = null;
  let disposed = false;
  const ready = Promise.resolve().then(() => importSource(source)).then(async module => {
    if (disposed) return;
    if (typeof module.mount !== "function") throw new Error("SETTINGS_MODULE_MOUNT_MISSING");
    const mounted = await module.mount({ ...context, signal: lifetime.signal });
    if (disposed) { mounted?.dispose?.(); return; }
    if (!mounted?.element || typeof mounted.dispose !== "function") {
      mounted?.dispose?.();
      throw new Error("SETTINGS_MODULE_COMPONENT_INVALID");
    }
    component = mounted;
    element.replaceChildren(component.element);
    component.update?.();
    onReady();
  }).catch(error => {
    if (disposed) return;
    failure = error;
    lifetime.abort();
    component?.dispose(); component = null;
    element.textContent = "插件设置加载失败。";
    onError(error);
  });
  return { element, ready,
    update() { component?.update?.(); },
    validate() {
      if (failure) throw failure;
      if (!component) throw new Error("插件设置仍在加载。");
      component.validate?.();
    },
    contributions: () => component?.contributions?.(),
    cancel: () => component?.cancel?.(),
    dispose() {
      if (disposed) return;
      disposed = true; lifetime.abort(); component?.dispose(); component = null; element.remove();
    },
  };
}
