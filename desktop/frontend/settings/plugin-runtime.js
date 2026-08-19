const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion", "revision", "state", "reasonCode", "plugins", "windowGeneration", "coreGenerationId",
]);
const PLUGIN_KEYS = Object.freeze([
  "pluginId", "name", "version", "author", "description", "enabled", "required", "supported",
  "state", "reasonCode", "permissions", "unavailable", "sections",
]);
const STATES = new Set([
  "disabled", "starting", "ready", "degraded", "stopping", "stopped",
  "waiting", "active", "failed", "conflict",
]);
const IDENTIFIER = /^[A-Za-z0-9_.-]{1,64}$/;
const REASON = /^[A-Z0-9_]{1,64}$/;

function exactKeys(value, keys) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key)));
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function boundedJson(value, maximum = 65_536) {
  try { return JSON.stringify(value).length <= maximum; } catch { return false; }
}

function validatePlugin(plugin) {
  if (!exactKeys(plugin, PLUGIN_KEYS) || !IDENTIFIER.test(plugin.pluginId)
      || typeof plugin.name !== "string" || !plugin.name || plugin.name.length > 120
      || typeof plugin.version !== "string" || !plugin.version || plugin.version.length > 64
      || typeof plugin.author !== "string" || plugin.author.length > 120
      || typeof plugin.description !== "string" || plugin.description.length > 500
      || typeof plugin.enabled !== "boolean" || typeof plugin.required !== "boolean"
      || typeof plugin.supported !== "boolean" || !STATES.has(plugin.state)
      || !REASON.test(plugin.reasonCode)
      || !Array.isArray(plugin.permissions) || plugin.permissions.length > 32
      || plugin.permissions.some((item) => !IDENTIFIER.test(item))
      || !Array.isArray(plugin.unavailable) || plugin.unavailable.length > 16
      || !Array.isArray(plugin.sections) || plugin.sections.length > 16
      || plugin.sections.some((section) => !validateSection(section))) {
    throw new Error("invalid plugin settings item");
  }
  return Object.freeze({ ...plugin, permissions: Object.freeze([...plugin.permissions]),
    unavailable: Object.freeze([...plugin.unavailable]), sections: Object.freeze(clone(plugin.sections)) });
}

function validateSection(section) {
  const keys = ["sectionId", "title", "reasonCode", "fields", "values", "actions"];
  return exactKeys(section, keys) && IDENTIFIER.test(section.sectionId)
    && typeof section.title === "string" && section.title.length > 0 && section.title.length <= 120
    && REASON.test(section.reasonCode) && Array.isArray(section.fields) && section.fields.length <= 32
    && section.fields.every(validateField) && section.values && typeof section.values === "object"
    && !Array.isArray(section.values) && Array.isArray(section.actions) && section.actions.length <= 16
    && section.actions.every(validateAction);
}

function validateField(field) {
  const keys = ["key", "label", "type", "default", "description", "options", "minimum", "maximum",
    "step", "required", "readonly", "copyable", "restartRequired", "value"];
  return exactKeys(field, keys) && IDENTIFIER.test(field.key)
    && typeof field.label === "string" && field.label.length > 0 && field.label.length <= 120
    && ["string", "password", "boolean", "integer", "number", "select", "readonly"].includes(field.type)
    && typeof field.description === "string" && field.description.length <= 240
    && Array.isArray(field.options) && field.options.length <= 64
    && field.options.every((option) => exactKeys(option, ["label", "value"])
      && typeof option.label === "string" && option.label.length > 0 && option.label.length <= 120
      && ["string", "number", "boolean"].includes(typeof option.value))
    && ["required", "readonly", "copyable", "restartRequired"].every((key) => typeof field[key] === "boolean")
    && boundedJson(field, 16_384);
}

function validateAction(action) {
  return exactKeys(action, ["actionId", "label", "description", "danger"])
    && IDENTIFIER.test(action.actionId) && typeof action.label === "string" && action.label.length > 0
    && action.label.length <= 120 && typeof action.description === "string" && action.description.length <= 240
    && action.danger === false;
}

export function validatePluginSnapshot(input) {
  if (!exactKeys(input, SNAPSHOT_KEYS) || input.schemaVersion !== 1
      || !/^[0-9a-f]{16}$/.test(input.revision) || !STATES.has(input.state)
      || !REASON.test(input.reasonCode) || !Array.isArray(input.plugins) || input.plugins.length > 64
      || !Number.isSafeInteger(input.windowGeneration) || input.windowGeneration < 1
      || typeof input.coreGenerationId !== "string" || !input.coreGenerationId) {
    throw new Error("invalid plugin settings snapshot");
  }
  return Object.freeze({ ...input, plugins: Object.freeze(input.plugins.map(validatePlugin)) });
}

function transitionError(error) {
  const message = String(error?.message || error || "");
  return ["SETTINGS_CORE_GENERATION_MISMATCH", "SETTINGS_CORE_UNAVAILABLE", "CORE_RESTART", "CORE_GENERATION"]
    .some((code) => message.includes(code));
}

function editableValues(current, pluginId, sectionId, values) {
  const plugin = current?.plugins.find((item) => item.pluginId === pluginId);
  const section = plugin?.sections.find((item) => item.sectionId === sectionId);
  if (!section) throw new Error("PLUGIN_SETTINGS_SECTION_INVALID");
  const fields = new Map(section.fields.map((field) => [field.key, field]));
  const projected = {};
  for (const [key, value] of Object.entries(values)) {
    const field = fields.get(key);
    if (!field) throw new Error("PLUGIN_SETTINGS_VALUES_INVALID");
    if (!field.readonly && field.type !== "readonly") projected[key] = value;
  }
  return projected;
}

function editableDraft(current, draft) {
  const projected = { enabledById: clone(draft.enabledById || {}), settingsById: {} };
  for (const [pluginId, sections] of Object.entries(draft.settingsById || {})) {
    for (const [sectionId, values] of Object.entries(sections || {})) {
      const editable = editableValues(current, pluginId, sectionId, values || {});
      if (Object.keys(editable).length) {
        projected.settingsById[pluginId] ||= {};
        projected.settingsById[pluginId][sectionId] = editable;
      }
    }
  }
  return projected;
}

export function createPluginController({ invoke, applySnapshot, readDraft, onDirty,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)) }) {
  let current = null;
  let disposed = false;
  let rebindPromise = null;

  function initialize(input, { preserveDraft = false } = {}) {
    const preserved = preserveDraft && current ? clone(readDraft()) : null;
    current = validatePluginSnapshot(input);
    applySnapshot(current, { preserveDraft, draft: preserved });
    onDirty();
  }

  async function bindCurrent(previousGeneration, { requireChange, preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validatePluginSnapshot(await invoke("settings_plugins_get"));
          if (!requireChange || next.coreGenerationId !== previousGeneration) {
            initialize(next, { preserveDraft });
            return next;
          }
        } catch (error) { lastError = error; }
        await wait(100);
      }
      throw new Error(`PLUGIN_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  return Object.freeze({
    initialize,
    snapshot: () => current,
    draft: () => clone(readDraft()),
    isDirty() {
      return Boolean(current && JSON.stringify(readDraft()) !== JSON.stringify({ enabledById: {}, settingsById: {} }));
    },
    async save() {
      if (!current) throw new Error("Plugin settings are not initialized");
      const settings = editableDraft(current, clone(readDraft()));
      const previousGeneration = current.coreGenerationId;
      try {
        const result = await invoke("settings_plugins_save", {
          windowGeneration: current.windowGeneration,
          coreGenerationId: previousGeneration,
          revision: current.revision,
          settings,
        });
        if (!["applied", "plugin_reload_required", "core_restart_required"].includes(result?.changePlan)
            || !["applied", "restart_required", "error"].includes(result?.applicationState)
            || !REASON.test(result?.applicationReasonCode || "")) {
          throw new Error("PLUGIN_SETTINGS_CHANGE_PLAN_INVALID");
        }
        const next = await bindCurrent(previousGeneration, {
          requireChange: result.changePlan === "core_restart_required",
          preserveDraft: false,
        });
        if (result.changePlan !== "core_restart_required"
            && result.applicationState === "restart_required") {
          throw new Error("PLUGIN_CONFIG_SAVED_RELOAD_REQUIRED");
        }
        if (result.changePlan !== "core_restart_required" && result.applicationState === "error") {
          throw new Error("PLUGIN_CONFIG_SAVED_APPLY_FAILED");
        }
        return Object.freeze({
          ...next,
          changePlan: result.changePlan,
          applicationState: result.applicationState,
          applicationReasonCode: result.applicationReasonCode,
        });
      } catch (error) {
        if (transitionError(error)) await bindCurrent(previousGeneration, { requireChange: false, preserveDraft: true });
        throw error;
      }
    },
    async action({ pluginId, sectionId, actionId, values }) {
      if (!current) throw new Error("Plugin settings are not initialized");
      if (!IDENTIFIER.test(pluginId) || !IDENTIFIER.test(sectionId) || !IDENTIFIER.test(actionId)
          || !values || typeof values !== "object" || Array.isArray(values) || !boundedJson(values)) {
        throw new Error("PLUGIN_SETTINGS_ACTION_INVALID");
      }
      const result = await invoke("settings_plugins_action", {
        windowGeneration: current.windowGeneration, coreGenerationId: current.coreGenerationId,
        pluginId, sectionId, actionId, values: editableValues(current, pluginId, sectionId, clone(values)),
      });
      if (!result || typeof result !== "object" || Array.isArray(result)
          || Object.keys(result).some((key) => !["values", "message"].includes(key))
          || (Object.hasOwn(result, "values") && (!result.values || typeof result.values !== "object"
            || Array.isArray(result.values)))
          || (Object.hasOwn(result, "message") && (typeof result.message !== "string"
            || result.message.length > 240)) || !boundedJson(result)) {
        throw new Error("PLUGIN_SETTINGS_ACTION_RESPONSE_INVALID");
      }
      if (actionId === "sakura.reload") {
        await bindCurrent(current.coreGenerationId, { requireChange: false, preserveDraft: false });
      }
      return clone(result);
    },
    async refreshCurrent() { return bindCurrent(current?.coreGenerationId || "", { requireChange: false, preserveDraft: true }); },
    discard() { if (current) applySnapshot(current, { preserveDraft: false, draft: null }); onDirty(); },
    dispose() { disposed = true; current = null; rebindPromise = null; },
  });
}
