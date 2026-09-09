// One native update carries both overlays so closing help cannot erase the tool dock.
export function createPetOverlaySurfaces(invoke) {
  let rect = null;
  let tooltipRect = null;
  let pending = Promise.resolve();
  function update() {
    const payload = { rect, tooltipRect };
    const result = pending.then(() => invoke("set_pet_tool_dock_surface", payload));
    pending = result.catch(() => {});
    return result;
  }
  return {
    setToolDock(value) { rect = value; return update(); },
    setTooltip(value) { tooltipRect = value; return update(); },
  };
}
