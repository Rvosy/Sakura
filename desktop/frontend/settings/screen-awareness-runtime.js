const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion",
  "settings",
  "windowGeneration",
  "coreGenerationId",
]);
const SETTINGS_KEYS = Object.freeze([
  "enabled",
  "checkIntervalMinutes",
  "cooldownMinutes",
  "batchLimit",
  "resolution",
]);
const RESOLUTIONS = new Set(["fullscreen", "720p", "1080p", "2160p"]);

function exactKeys(value, keys) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key)));
}

function validateSettings(settings) {
  if (!exactKeys(settings, SETTINGS_KEYS) || typeof settings.enabled !== "boolean") {
    throw new Error("invalid screen awareness settings");
  }
  for (const [key, maximum] of [["checkIntervalMinutes", 120], ["cooldownMinutes", 120], ["batchLimit", 20]]) {
    const value = settings[key];
    if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
      throw new Error(`invalid screen awareness setting: ${key}`);
    }
  }
  if (!RESOLUTIONS.has(settings.resolution)) throw new Error("invalid screen awareness resolution");
  return settings;
}

export function validateScreenAwarenessSnapshot(input) {
  if (!exactKeys(input, SNAPSHOT_KEYS) || input.schemaVersion !== 1) {
    throw new Error("invalid screen awareness snapshot");
  }
  if (!Number.isSafeInteger(input.windowGeneration) || input.windowGeneration < 1) {
    throw new Error("invalid screen awareness window generation");
  }
  if (typeof input.coreGenerationId !== "string" || !input.coreGenerationId) {
    throw new Error("invalid screen awareness Core generation");
  }
  validateSettings(input.settings);
  return Object.freeze({ ...input, settings: Object.freeze({ ...input.settings }) });
}

export function createScreenAwarenessSettingsController({
  document,
  invoke,
  enhanceSelect = () => {},
  refreshSelect = () => {},
  onDirty,
}) {
  const controls = {
    enabled: document.getElementById("enabled"),
    checkIntervalMinutes: document.getElementById("checkInterval"),
    cooldownMinutes: document.getElementById("cooldown"),
    batchLimit: document.getElementById("batchLimit"),
    resolution: document.getElementById("screenResolution"),
  };
  let snapshot = null;
  let baseline = null;

  enhanceSelect(controls.resolution);

  function syncEnabled() {
    for (const key of ["checkIntervalMinutes", "cooldownMinutes", "batchLimit", "resolution"]) {
      controls[key].disabled = !controls.enabled.checked;
    }
    refreshSelect(controls.resolution);
  }

  function read() {
    return validateSettings({
      enabled: controls.enabled.checked,
      checkIntervalMinutes: Number.parseInt(controls.checkIntervalMinutes.value, 10),
      cooldownMinutes: Number.parseInt(controls.cooldownMinutes.value, 10),
      batchLimit: Number.parseInt(controls.batchLimit.value, 10),
      resolution: controls.resolution.value,
    });
  }

  function fill(settings) {
    controls.enabled.checked = settings.enabled;
    controls.checkIntervalMinutes.min = "1";
    controls.checkIntervalMinutes.max = "120";
    controls.checkIntervalMinutes.value = String(settings.checkIntervalMinutes);
    controls.cooldownMinutes.min = "1";
    controls.cooldownMinutes.max = "120";
    controls.cooldownMinutes.value = String(settings.cooldownMinutes);
    controls.batchLimit.min = "1";
    controls.batchLimit.max = "20";
    controls.batchLimit.value = String(settings.batchLimit);
    controls.resolution.value = settings.resolution;
    syncEnabled();
  }

  function initialize(input) {
    snapshot = validateScreenAwarenessSnapshot(input);
    baseline = { ...snapshot.settings };
    fill(baseline);
    onDirty();
  }

  for (const control of Object.values(controls)) {
    control.addEventListener("input", onDirty);
    control.addEventListener("change", () => {
      syncEnabled();
      onDirty();
    });
  }

  return Object.freeze({
    initialize,
    isDirty() {
      try {
        const current = read();
        return Boolean(baseline && SETTINGS_KEYS.some((key) => current[key] !== baseline[key]));
      }
      catch { return true; }
    },
    async save() {
      if (!snapshot) throw new Error("screen awareness settings are not initialized");
      const result = await invoke("settings_screen_awareness_save", {
        windowGeneration: snapshot.windowGeneration,
        coreGenerationId: snapshot.coreGenerationId,
        settings: read(),
      });
      initialize(result);
      return result;
    },
    discard() {
      if (baseline) fill(baseline);
      onDirty();
    },
    dispose() {
      snapshot = null;
      baseline = null;
    },
  });
}
