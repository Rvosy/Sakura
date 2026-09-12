// A real non-image renderer used to exercise the shared plugin contract.
export function mount({ container, resource, host, signal }) {
  const doc = container.ownerDocument;
  const model = doc.createElement("div");
  model.textContent = "◒";
  model.setAttribute("aria-label", "数值模型");
  Object.assign(model.style, { fontSize: "180px", color: "#d16b84", textAlign: "center", transformOrigin: "center bottom", userSelect: "none" });
  container.append(model);
  let action = null;
  let angle = 0;
  const ready = host.setSurface({ width: 320, height: 420 });
  return {
    ready,
    applyState(state, context) {
      if (signal.aborted || context.signal.aborted) return;
      if (!Number.isFinite(state.angle) || state.angle < -30 || state.angle > resource.data.maxAngle) throw new Error("NUMERIC_ANGLE_INVALID");
      angle = state.angle;
      model.style.transform = `rotate(${angle}deg)`;
      model.dataset.angle = String(angle);
    },
    async perform(value, context) {
      if (!value.wave || signal.aborted || context.signal.aborted) return;
      model.dataset.waves = String(Number(model.dataset.waves || 0) + 1);
      action = model.animate([{ transform: `rotate(${angle}deg)` }, { transform: `rotate(${angle - 15}deg)` }, { transform: `rotate(${angle}deg)` }], { duration: 400 });
      const active = action;
      const abort = () => active.cancel();
      context.signal.addEventListener("abort", abort, { once: true });
      try { await active.finished; } catch { /* cancellation is expected */ }
      finally { context.signal.removeEventListener("abort", abort); if (action === active) action = null; }
    },
    cancel() { action?.cancel(); action = null; },
    destroy() { action?.cancel(); container.replaceChildren(); },
  };
}
