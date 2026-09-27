import { createMorphIcon } from "../core/morph-icon.js";

export function createPlaybackActionIndicator({ button, createIcon = createMorphIcon }) {
  const icon = createIcon({ element: button.querySelector(".sakura-morph-icon"), initial: "volume-2" });
  let state = "idle";
  let disposed = false;
  function render() {
    const preparing = state === "preparing";
    icon.set(preparing ? "loader-circle" : state === "playing" ? "stop" : "volume-2", preparing);
    button.dataset.playbackState = state;
    button.setAttribute("aria-label", state === "idle" ? "朗读这条回复" : "停止朗读");
    button.setAttribute("aria-busy", String(preparing));
  }
  render();
  return Object.freeze({
    setState(next) {
      if (disposed || state === next) return;
      state = next;
      render();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      icon.dispose();
    },
  });
}
