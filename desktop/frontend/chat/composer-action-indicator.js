export function createComposerActionIndicator({ icon, sendLayer, prefersReducedMotion = () => false } = {}) {
  if (!icon) throw new Error("composer action indicator requires its icon element");
  icon.style.transformOrigin = "center";

  let animation = null;
  let departure = null;
  let wasBusy = false;

  function stop() {
    animation?.cancel();
    animation = null;
    departure?.cancel();
    departure = null;
    icon.style.transform = "";
  }

  return Object.freeze({
    setBusy(busy) {
      const entering = busy && !wasBusy;
      wasBusy = Boolean(busy);
      if (!busy || prefersReducedMotion()) {
        stop();
        return;
      }
      if (entering && typeof sendLayer?.animate === "function") {
        departure = sendLayer.animate([
          { transform: "translate(0, 0) scale(1)", opacity: 1 },
          { transform: "translateX(-2px) scale(.94)", opacity: 1, offset: .2 },
          { transform: "translateX(15px) scale(.7)", opacity: 0 },
        ], { duration: 280, easing: "cubic-bezier(.22, .8, .28, 1)" });
      }
      if (animation || typeof icon.animate !== "function") return;
      animation = icon.animate(
        [{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }],
        { duration: 820, iterations: Infinity, easing: "linear" },
      );
    },
    dispose() { wasBusy = false; stop(); },
  });
}
