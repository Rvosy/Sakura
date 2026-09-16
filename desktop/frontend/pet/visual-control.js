// The host checks routing and size; state and action semantics belong to plugins.
export function normalizeVisualControl(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some((key) => !["version", "resourceId", "bindingId", "state", "actions"].includes(key))
    || value.version !== 1 || !/^[0-9a-f]{32}$/.test(value.bindingId || "")
    || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value.resourceId || "")
    || !(Object.hasOwn(value, "state") || Object.hasOwn(value, "actions"))
    || (Object.hasOwn(value, "actions") && (!Array.isArray(value.actions) || value.actions.length > 32))) return null;
  try {
    const encoded = JSON.stringify(value, (_key, item) => {
      if (typeof item === "number" && !Number.isFinite(item)) throw new Error("invalid number");
      return item;
    });
    return new TextEncoder().encode(encoded).length <= 65536 ? JSON.parse(encoded) : null;
  } catch { return null; }
}
