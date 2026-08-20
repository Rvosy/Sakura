const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion", "revision", "state", "reasonCode", "plugins", "windowGeneration", "coreGenerationId",
]);
const PLUGIN_KEYS = Object.freeze([
  "installId", "pluginId", "name", "version", "author", "description", "enabled", "required", "supported",
  "source", "canUninstall", "state", "reasonCode", "provides", "requires", "optional",
  "missingServices", "conflicts", "sections",
]);
const STATES = new Set([
  "disabled", "starting", "ready", "degraded", "stopping", "stopped",
  "waiting", "active", "failed", "conflict",
]);
const IDENTIFIER = /^[A-Za-z0-9_.-]{1,64}$/;
const SERVICE_KEY = /^[A-Za-z0-9_.-]{1,200}$/;
const INSTALL_ID = /^pi_[0-9a-f]{24}$/;
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
  const diagnosticLists = ["provides", "requires", "optional", "missingServices", "conflicts"];
  if (!exactKeys(plugin, PLUGIN_KEYS) || !INSTALL_ID.test(plugin.installId)
      || !(plugin.pluginId === null || IDENTIFIER.test(plugin.pluginId))
      || typeof plugin.name !== "string" || !plugin.name || plugin.name.length > 120
      || typeof plugin.version !== "string" || !plugin.version || plugin.version.length > 64
      || typeof plugin.author !== "string" || plugin.author.length > 120
      || typeof plugin.description !== "string" || plugin.description.length > 500
      || typeof plugin.enabled !== "boolean" || typeof plugin.required !== "boolean"
      || typeof plugin.supported !== "boolean" || !STATES.has(plugin.state)
      || !["bundled", "user"].includes(plugin.source) || typeof plugin.canUninstall !== "boolean"
      || (plugin.source === "user" && plugin.required) || plugin.canUninstall !== (plugin.source === "user")
      || !REASON.test(plugin.reasonCode)
      || diagnosticLists.some((key) => !Array.isArray(plugin[key]) || plugin[key].length > 64
        || plugin[key].some((item) => !SERVICE_KEY.test(item)))
      || !Array.isArray(plugin.sections) || plugin.sections.length > 16
      || plugin.sections.some((section) => !validateSection(section))) {
    throw new Error("invalid plugin settings item");
  }
  return Object.freeze({ ...plugin,
    ...Object.fromEntries(diagnosticLists.map((key) => [key, Object.freeze([...plugin[key]])])),
    sections: Object.freeze(clone(plugin.sections)) });
}

function validateSection(section) {
  const keys = ["sectionId", "title", "surface", "reasonCode", "fields", "values", "actions", "collections"];
  const legacyKeys = keys.filter((key) => key !== "surface");
  return (exactKeys(section, keys) || exactKeys(section, legacyKeys)) && IDENTIFIER.test(section.sectionId)
    && typeof section.title === "string" && section.title.length > 0 && section.title.length <= 120
    && (section.surface === undefined || section.surface === null || IDENTIFIER.test(section.surface))
    && REASON.test(section.reasonCode) && Array.isArray(section.fields) && section.fields.length <= 32
    && section.values && typeof section.values === "object"
    && !Array.isArray(section.values) && Array.isArray(section.actions) && section.actions.length <= 16
    && Array.isArray(section.collections) && section.collections.length <= 4
    && boundedJson(section, 131_072);
}

function validateCollection(collection) {
  const keys = ["collectionId", "title", "description", "columns", "fields", "filters", "searchable",
    "pageSize", "canCreate", "canUpdate", "canDelete", "deleteConfirmation"];
  return exactKeys(collection, keys) && IDENTIFIER.test(collection.collectionId)
    && typeof collection.title === "string" && collection.title.length > 0 && collection.title.length <= 120
    && typeof collection.description === "string" && collection.description.length <= 240
    && Array.isArray(collection.columns) && collection.columns.length > 0 && collection.columns.length <= 12
    && collection.columns.every((column) => exactKeys(column, ["key", "label", "type", "maxLength"])
      && IDENTIFIER.test(column.key) && typeof column.label === "string" && column.label.length > 0
      && column.label.length <= 120 && ["string", "number", "boolean", "datetime"].includes(column.type)
      && (column.maxLength === null || (column.type === "string" && Number.isSafeInteger(column.maxLength)
        && column.maxLength >= 1 && column.maxLength <= 16_384)))
    && Array.isArray(collection.fields) && collection.fields.length <= 16
    && collection.fields.every(validateCollectionField)
    && Array.isArray(collection.filters) && collection.filters.length <= 8
    && collection.filters.every((filter) => exactKeys(filter, ["key", "label", "options"])
      && IDENTIFIER.test(filter.key) && typeof filter.label === "string" && filter.label.length > 0
      && filter.label.length <= 120 && Array.isArray(filter.options) && filter.options.length > 0
      && filter.options.length <= 64 && filter.options.every(validateOption))
    && typeof collection.searchable === "boolean" && Number.isSafeInteger(collection.pageSize)
    && collection.pageSize >= 1 && collection.pageSize <= 100
    && ["canCreate", "canUpdate", "canDelete"].every((key) => typeof collection[key] === "boolean")
    && typeof collection.deleteConfirmation === "string" && collection.deleteConfirmation.length <= 240;
}

function validateOption(option) {
  return exactKeys(option, ["label", "value"])
    && typeof option.label === "string" && option.label.length > 0 && option.label.length <= 120
    && ["string", "number", "boolean"].includes(typeof option.value);
}

function validateCollectionField(field) {
  const keys = ["key", "label", "type", "default", "description", "options", "minimum", "maximum",
    "step", "maxLength", "required", "readonly", "copyable", "restartRequired"];
  return exactKeys(field, keys) && IDENTIFIER.test(field.key)
    && typeof field.label === "string" && field.label.length > 0 && field.label.length <= 120
    && ["string", "password", "boolean", "integer", "number", "select", "readonly"].includes(field.type)
    && typeof field.description === "string" && field.description.length <= 240
    && Array.isArray(field.options) && field.options.length <= 64 && field.options.every(validateOption)
    && (field.maxLength === null || (["string", "password", "readonly"].includes(field.type)
      && Number.isSafeInteger(field.maxLength) && field.maxLength >= 1 && field.maxLength <= 16_384))
    && ["required", "readonly", "copyable", "restartRequired"].every((key) => typeof field[key] === "boolean")
    && boundedJson(field, 16_384);
}

function validateField(field) {
  const keys = ["key", "label", "type", "default", "description", "options", "minimum", "maximum",
    "step", "maxLength", "required", "readonly", "copyable", "restartRequired", "value"];
  return exactKeys(field, keys) && IDENTIFIER.test(field.key)
    && typeof field.label === "string" && field.label.length > 0 && field.label.length <= 120
    && ["string", "password", "boolean", "integer", "number", "select", "readonly"].includes(field.type)
    && typeof field.description === "string" && field.description.length <= 240
    && Array.isArray(field.options) && field.options.length <= 64
    && field.options.every(validateOption)
    && (field.maxLength === null || (["string", "password", "readonly"].includes(field.type)
      && Number.isSafeInteger(field.maxLength) && field.maxLength >= 1 && field.maxLength <= 16_384))
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

function validateManagementSnapshot(input) {
  const action = input?.managementAction;
  const extra = action === "enabled_changed"
    ? ["managementAction", "installId", "pluginId", "desiredSaved", "applicationState", "applicationReasonCode"]
    : ["managementAction", "installId", "pluginId"];
  if (!input || typeof input !== "object" || Array.isArray(input)
      || !["installed", "uninstalled", "enabled_changed"].includes(action)
      || !INSTALL_ID.test(input.installId || "")
      || !(input.pluginId === null || IDENTIFIER.test(input.pluginId || ""))
      || (action === "installed" && input.pluginId === null)
      || !exactKeys(input, [...SNAPSHOT_KEYS, ...extra])
      || (action === "enabled_changed" && (input.desiredSaved !== true
        || !["applied", "recovered", "degraded"].includes(input.applicationState)
        || !REASON.test(input.applicationReasonCode || "")))) {
    throw new Error("PLUGIN_MANAGEMENT_RESPONSE_INVALID");
  }
  const snapshot = validatePluginSnapshot(Object.fromEntries(
    SNAPSHOT_KEYS.map((key) => [key, input[key]]),
  ));
  return Object.freeze({ ...snapshot, ...Object.fromEntries(extra.map((key) => [key, input[key]])) });
}

function transitionError(error) {
  const message = String(error?.message || error || "");
  return ["SETTINGS_CORE_GENERATION_MISMATCH", "SETTINGS_CORE_UNAVAILABLE", "CORE_RESTART", "CORE_GENERATION"]
    .some((code) => message.includes(code));
}

function uncertainManagementError(error) {
  const message = String(error?.message || error || "");
  return transitionError(error) || [
    "CONFIG_REVISION_CONFLICT", "REQUEST_DEADLINE_EXCEEDED", "SETTINGS_REQUEST_ABORTED",
    "TRANSPORT_", "PLUGIN_INSTALL_ROLLBACK_FAILED", "PLUGIN_INSTALL_RECOVERY_FAILED",
    "PLUGIN_UNINSTALL_ROLLBACK_FAILED", "PLUGIN_UNINSTALL_RECOVERY_FAILED",
    "PLUGIN_UNINSTALL_CLEANUP_FAILED",
  ].some((code) => message.includes(code));
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

function collectionDescriptor(current, pluginId, sectionId, collectionId) {
  const plugin = current?.plugins.find((item) => item.pluginId === pluginId);
  const section = plugin?.sections.find((item) => item.sectionId === sectionId);
  const collection = section?.collections.find((item) => item.collectionId === collectionId);
  if (!collection) throw new Error("PLUGIN_COLLECTION_INVALID");
  return collection;
}

function collectionRequest(current, input) {
  const { operation, pluginId, sectionId, collectionId } = input;
  if (![pluginId, sectionId, collectionId].every((value) => IDENTIFIER.test(value || ""))) {
    throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
  }
  const collection = collectionDescriptor(current, pluginId, sectionId, collectionId);
  let payload;
  if (operation === "query") {
    const cursor = input.cursor ?? null;
    const limit = input.limit ?? collection.pageSize;
    const search = input.search ?? "";
    const filters = input.filters ?? {};
    const filterSpecs = new Map(collection.filters.map((item) => [item.key, item]));
    if ((cursor !== null && (typeof cursor !== "string" || cursor.length > 256))
        || !Number.isSafeInteger(limit) || limit < 1 || limit > 100
        || typeof search !== "string" || search.length > 200 || (search && !collection.searchable)
        || !filters || typeof filters !== "object" || Array.isArray(filters)
        || Object.entries(filters).some(([key, value]) => !filterSpecs.get(key)?.options
          .some((option) => option.value === value))) {
      throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
    }
    payload = { cursor, limit, search, filters: clone(filters) };
  } else if (["create", "update"].includes(operation)) {
    if (operation === "create" && !collection.canCreate) throw new Error("PLUGIN_COLLECTION_OPERATION_UNAVAILABLE");
    if (operation === "update" && !collection.canUpdate) throw new Error("PLUGIN_COLLECTION_OPERATION_UNAVAILABLE");
    const values = input.values;
    const fields = new Map(collection.fields.map((field) => [field.key, field]));
    if (!values || typeof values !== "object" || Array.isArray(values)
        || Object.keys(values).some((key) => !fields.has(key) || fields.get(key).readonly)) {
      throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
    }
    payload = { values: clone(values) };
    if (operation === "update") {
      if (typeof input.itemId !== "string" || !input.itemId || input.itemId.length > 200) {
        throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
      }
      payload = { itemId: input.itemId, ...payload };
    }
  } else if (operation === "delete") {
    if (!collection.canDelete || typeof input.itemId !== "string" || !input.itemId
        || input.itemId.length > 200) throw new Error("PLUGIN_COLLECTION_OPERATION_UNAVAILABLE");
    payload = { itemId: input.itemId };
  } else {
    throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
  }
  if (!boundedJson(payload)) throw new Error("PLUGIN_COLLECTION_REQUEST_INVALID");
  return { operation, pluginId, sectionId, collectionId, payload };
}

function validateCollectionResult(operation, result) {
  const itemValid = (item) => exactKeys(item, ["itemId", "values"])
    && typeof item.itemId === "string" && item.itemId.length > 0 && item.itemId.length <= 200
    && item.values && typeof item.values === "object" && !Array.isArray(item.values)
    && Object.values(item.values).every((value) => value === null || ["string", "number", "boolean"].includes(typeof value))
    && boundedJson(item, 131_072);
  const valid = operation === "query"
    ? exactKeys(result, ["items", "nextCursor", "total"]) && Array.isArray(result.items)
      && result.items.length <= 100 && result.items.every(itemValid)
      && (result.nextCursor === null || (typeof result.nextCursor === "string" && result.nextCursor.length <= 256))
      && (result.total === null || (Number.isSafeInteger(result.total) && result.total >= 0))
    : ["create", "update"].includes(operation) ? itemValid(result)
      : operation === "delete" && exactKeys(result, ["deleted"]) && typeof result.deleted === "boolean";
  if (!valid || !boundedJson(result, 262_144)) throw new Error("PLUGIN_COLLECTION_RESPONSE_INVALID");
  return clone(result);
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

  async function bindCurrent({ preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validatePluginSnapshot(await invoke("settings_plugins_get"));
          if (!current || JSON.stringify(next) !== JSON.stringify(current)) {
            initialize(next, { preserveDraft });
          }
          return next;
        } catch (error) { lastError = error; }
        await wait(100);
      }
      throw new Error(`PLUGIN_SETTINGS_REFRESH_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
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
        let applicationState = "applied";
        let applicationReasonCode = "READY";
        let changePlan = "applied";
        for (const [pluginId, enabled] of Object.entries(settings.enabledById)) {
          const plugin = current.plugins.find((item) => item.pluginId === pluginId);
          if (!plugin) throw new Error("PLUGIN_ENABLED_REQUEST_INVALID");
          const result = validateManagementSnapshot(await invoke("settings_plugins_enabled_set", {
            windowGeneration: current.windowGeneration,
            coreGenerationId: previousGeneration,
            revision: current.revision,
            installId: plugin.installId,
            enabled,
          }));
          current = Object.freeze(Object.fromEntries(SNAPSHOT_KEYS.map((key) => [key, result[key]])));
          if (result.applicationState === "degraded") {
            applicationState = "error";
            applicationReasonCode = result.applicationReasonCode;
          } else if (result.applicationState === "recovered" && applicationState === "applied") {
            applicationReasonCode = result.applicationReasonCode;
          }
        }
        for (const [pluginId, sections] of Object.entries(settings.settingsById)) {
          for (const [sectionId, values] of Object.entries(sections)) {
            const result = await invoke("settings_plugins_save", {
              windowGeneration: current.windowGeneration,
              coreGenerationId: previousGeneration,
              pluginId,
              sectionId,
              values,
            });
            if (result?.saved !== true || result.pluginId !== pluginId || result.sectionId !== sectionId
                || !["applied", "plugin_reload_required"].includes(result.changePlan)
                || !["applied", "restart_required", "error"].includes(result.applicationState)
                || !REASON.test(result.applicationReasonCode || "")) {
              throw new Error("PLUGIN_SETTINGS_CHANGE_PLAN_INVALID");
            }
            if (result.applicationState === "error") applicationState = "error";
            else if (result.applicationState === "restart_required" && applicationState === "applied") {
              applicationState = "restart_required";
            }
            if (result.applicationState !== "applied") {
              changePlan = "plugin_reload_required";
              applicationReasonCode = result.applicationReasonCode;
            }
          }
        }
        const next = await bindCurrent({ preserveDraft: false });
        if (applicationState === "restart_required") {
          throw new Error("PLUGIN_CONFIG_SAVED_RELOAD_REQUIRED");
        }
        if (applicationState === "error") {
          throw new Error("PLUGIN_CONFIG_SAVED_APPLY_FAILED");
        }
        return Object.freeze({
          ...next,
          changePlan,
          applicationState,
          applicationReasonCode,
        });
      } catch (error) {
        if (transitionError(error)) await bindCurrent({ preserveDraft: true });
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
        await bindCurrent({ preserveDraft: false });
      }
      return clone(result);
    },
    async install(sourceKind) {
      if (!current || !["zip", "folder"].includes(sourceKind)) {
        throw new Error("PLUGIN_INSTALL_REQUEST_INVALID");
      }
      try {
        const result = await invoke("settings_plugins_install", {
          windowGeneration: current.windowGeneration,
          coreGenerationId: current.coreGenerationId,
          revision: current.revision,
          sourceKind,
        });
        if (exactKeys(result, ["cancelled"]) && result.cancelled === true) return null;
        const next = validateManagementSnapshot(result);
        if (next.managementAction !== "installed"
            || !next.plugins.some((plugin) => plugin.installId === next.installId
              && plugin.pluginId === next.pluginId
              && plugin.source === "user" && plugin.canUninstall && !plugin.enabled)) {
          throw new Error("PLUGIN_MANAGEMENT_RESPONSE_INVALID");
        }
        initialize(Object.fromEntries(SNAPSHOT_KEYS.map((key) => [key, next[key]])), {
          preserveDraft: true,
        });
        return next;
      } catch (error) {
        if (uncertainManagementError(error)) {
          try { await bindCurrent({ preserveDraft: true }); } catch { /* keep the management error */ }
        }
        throw error;
      }
    },
    async uninstall(installId) {
      const plugin = current?.plugins.find((item) => item.installId === installId);
      if (!current || !INSTALL_ID.test(installId || "") || !plugin?.canUninstall
          || plugin.source !== "user") {
        throw new Error("PLUGIN_UNINSTALL_REQUEST_INVALID");
      }
      try {
        const result = validateManagementSnapshot(await invoke("settings_plugins_uninstall", {
          windowGeneration: current.windowGeneration,
          coreGenerationId: current.coreGenerationId,
          revision: current.revision,
          installId,
        }));
        if (result.managementAction !== "uninstalled" || result.installId !== installId
            || result.plugins.some((item) => item.installId === installId)) {
          throw new Error("PLUGIN_MANAGEMENT_RESPONSE_INVALID");
        }
        initialize(Object.fromEntries(SNAPSHOT_KEYS.map((key) => [key, result[key]])), {
          preserveDraft: true,
        });
        return result;
      } catch (error) {
        if (uncertainManagementError(error)) {
          try { await bindCurrent({ preserveDraft: true }); } catch { /* keep the management error */ }
        }
        throw error;
      }
    },
    async collection(input) {
      if (!current) throw new Error("Plugin settings are not initialized");
      const request = collectionRequest(current, input);
      const result = await invoke("settings_plugins_collection", {
        windowGeneration: current.windowGeneration,
        coreGenerationId: current.coreGenerationId,
        ...request,
      });
      return validateCollectionResult(request.operation, result);
    },
    async refreshCurrent() { return bindCurrent({ preserveDraft: true }); },
    discard() { if (current) applySnapshot(current, { preserveDraft: false, draft: null }); onDirty(); },
    dispose() { disposed = true; current = null; rebindPromise = null; },
  });
}
