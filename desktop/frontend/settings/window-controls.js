export function installSettingsWindowControls({ document, appWindow, useCustomTitlebar, onClose, onError }) {
  const controls = document.getElementById("windowControls");
  const maximize = document.getElementById("windowMaximize");
  const listeners = [];
  let disposed = false;
  let unlistenResize;

  function listen(target, event, action) {
    const listener = (input) => Promise.resolve(action(input)).catch(onError);
    target.addEventListener(event, listener);
    listeners.push(() => target.removeEventListener(event, listener));
  }

  async function syncMaximized() {
    const maximized = await appWindow.isMaximized();
    if (disposed) return;
    maximize.dataset.maximized = String(maximized);
    const label = maximized ? "还原" : "最大化";
    maximize.setAttribute("aria-label", label);
  }

  async function toggleMaximized() {
    await appWindow.toggleMaximize();
    await syncMaximized();
  }

  const ready = (async () => {
    if (!useCustomTitlebar) return;
    await appWindow.setDecorations(false);
    if (disposed) return;
    listen(document.getElementById("windowMinimize"), "click", () => appWindow.minimize());
    listen(maximize, "click", toggleMaximized);
    listen(document.getElementById("windowClose"), "click", onClose);
    document.querySelectorAll("[data-window-drag]").forEach(region => {
      listen(region, "mousedown", event => {
        if (event.button !== 0 || event.target.closest("button, a, input, select, textarea")) return;
        return event.detail === 2 ? toggleMaximized() : appWindow.startDragging();
      });
    });
    unlistenResize = await appWindow.onResized(() => syncMaximized().catch(onError));
    if (disposed) { unlistenResize(); return; }
    await syncMaximized();
    if (disposed) return;
    controls.hidden = false;
    document.body.classList.add("has-custom-titlebar");
  })().catch(onError);

  return {
    ready,
    dispose() {
      disposed = true;
      listeners.forEach(remove => remove());
      unlistenResize?.();
    },
  };
}
