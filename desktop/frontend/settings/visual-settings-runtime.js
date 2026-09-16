export function normalizeVisualSettings(value, characterId) {
  if (value.characterId !== characterId) throw new Error("CHARACTER_VISUAL_SETTINGS_INVALID");
  return structuredClone(value);
}
