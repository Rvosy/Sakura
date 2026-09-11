export function mountEditor({ container, data, host, signal }) {
  const config = { maxAngle: 20, ...structuredClone(data) };
  const label = container.ownerDocument.createElement("label");
  label.textContent = "最大倾斜角度";
  const input = container.ownerDocument.createElement("input");
  input.type = "number"; input.min = "0"; input.max = "90"; input.value = String(config.maxAngle);
  label.append(input); container.append(label);
  input.oninput = () => { if (!signal.aborted) { config.maxAngle = Number(input.value); host.changed(config); } };
  return { collect: () => structuredClone(config), validate: () => Number.isFinite(config.maxAngle) && config.maxAngle >= 0 && config.maxAngle <= 90, destroy: () => container.replaceChildren() };
}
