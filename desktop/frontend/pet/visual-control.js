// Routing belongs to the host; state and action semantics belong to plugins.
export function normalizeVisualControl(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || value.version !== 1 || !/^[0-9a-f]{32}$/.test(value.bindingId || "")
    || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value.resourceId || "")
    || !(Object.hasOwn(value, "state") || Object.hasOwn(value, "actions"))
    || (Object.hasOwn(value, "actions") && (!Array.isArray(value.actions)))) return null;
  return structuredClone(value);
}
