export function normalizeVisualSettings(value, characterId) {
  const ids = new Set();
  const text = (v, limit) => typeof v === "string" && v.length > 0 && v.length <= limit;
  const fail = () => { throw new Error("CHARACTER_VISUAL_SETTINGS_INVALID"); };
  if (value?.schemaVersion !== 1 || value.characterId !== characterId || !Array.isArray(value.resources) || value.resources.length > 32 || Object.keys(value).length !== 5) fail();
  for (const item of value.resources) {
    if (!text(item?.id, 128) || ids.has(item.id) || !text(item.name, 256) || !text(item.reasonCode, 128)
      || (item.providerId !== null && !text(item.providerId, 128)) || (item.installId !== null && !text(item.installId, 1024)) || Object.keys(item).length !== 5) fail();
    ids.add(item.id);
  }
  if ([value.defaultResourceId, value.preferenceResourceId].some(id => id !== null && !ids.has(id))) fail();
  return structuredClone(value);
}
