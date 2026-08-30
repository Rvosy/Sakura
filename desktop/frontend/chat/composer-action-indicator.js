export function createComposerActionIndicator({ svg, shape, prefersReducedMotion = () => false } = {}) {
  if (!svg || !shape) throw new Error("composer action indicator requires its existing SVG elements");

  shape.setAttribute("x", "4.5");
  shape.setAttribute("y", "4.5");
  shape.setAttribute("width", "15");
  shape.setAttribute("height", "15");
  shape.setAttribute("rx", "7.5");
  Object.assign(shape.style, {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.2",
    strokeLinecap: "round",
    strokeDasharray: "34 14",
  });
  svg.style.transformOrigin = "center";

  let animation = null;

  function stop() {
    animation?.cancel();
    animation = null;
    svg.style.transform = "";
  }

  return Object.freeze({
    setBusy(busy) {
      if (!busy) {
        stop();
        return;
      }
      if (animation || prefersReducedMotion() || typeof svg.animate !== "function") return;
      animation = svg.animate(
        [{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }],
        { duration: 820, iterations: Infinity, easing: "linear" },
      );
    },
    dispose: stop,
  });
}
