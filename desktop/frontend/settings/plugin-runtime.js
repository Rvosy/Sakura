const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion", "revision", "state", "reasonCode", "plugins", "windowGeneration", "coreGenerationId",
]);
const IDENTIFIER = /^[A-Za-z0-9_.-]{1,64}$/;
const INSTALL_ID = /^pi_(?:user|bundled)_(?:[0-9a-f]{2}){1,1024}$/;
const REASON = /^[A-Z0-9_]{1,64}$/;

function exactKeys(value, keys) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key)));
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function boundedJson(value, maximum = 65_536) {
  try { return JSON.stringify(value).length <= maximum; } catch { return false; }
}

function isObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

// Display declarations and values have already been projected by Core. Only
// check the shape needed to render them and the identities used by commands.
function validatePlugin(plugin) {
  if (!isObject(plugin) || !INSTALL_ID.test(plugin.installId)
      || !(plugin.pluginId === null || IDENTIFIER.test(plugin.pluginId))
      || !["name", "version", "author", "description", "source", "state", "reasonCode"]
        .every((key) => typeof plugin[key] === "string")
      || !["enabled", "required", "supported", "canUninstall"]
        .every((key) => typeof plugin[key] === "boolean")
      || !["provides", "requires", "missingServices"].every((key) => Array.isArray(plugin[key])
        && plugin[key].every((item) => typeof item === "string"))
      || !Array.isArray(plugin.sections) || !plugin.sections.every(validateSection)) {
    throw new Error("invalid plugin settings item");
  }
  return Object.freeze({ ...plugin, sections: Object.freeze(clone(plugin.sections)) });
}

function validateSection(section) {
  return isObject(section) && typeof section.sectionId === "string"
    && typeof section.title === "string" && isObject(section.values)
    && Array.isArray(section.fields) && section.fields.every((field) => isObject(field)
      && typeof field.key === "string" && typeof field.type === "string"
      && Array.isArray(field.options) && Array.isArray(field.actionIds))
    && Array.isArray(section.actions) && section.actions.every(isObject)
    && Array.isArray(section.collections) && section.collections.every((collection) => isObject(collection)
      && ["columns", "fields", "filters"].every((key) => Array.isArray(collection[key])));
}

export function validatePluginSnapshot(input) {
  if (!isObject(input) || input.schemaVersion !== 1
      || !/^[0-9a-f]{16}$/.test(input.revision) || typeof input.state !== "string"
      || typeof input.reasonCode !== "string" || !Array.isArray(input.plugins)
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
        || !["applied", "error"].includes(input.applicationState)
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
    if (!field.readonly && !["readonly", "status", "resource"].includes(field.type)) projected[key] = value;
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
  let refreshPromise = null;

  function initialize(input, { preserveDraft = false, keepGlobalCollectionDrafts = false } = {}) {
    const preserved = preserveDraft && current ? clone(readDraft()) : null;
    current = validatePluginSnapshot(input);
    applySnapshot(current, { preserveDraft, draft: preserved, keepGlobalCollectionDrafts });
    onDirty();
  }

  async function bindCurrent({ preserveDraft, keepGlobalCollectionDrafts = false }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      if (refreshPromise) {
        try { await refreshPromise; } catch { /* the bounded rebind below owns recovery */ }
      }
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validatePluginSnapshot(await invoke("settings_plugins_get"));
          if (!current || JSON.stringify(next) !== JSON.stringify(current)) {
            initialize(next, { preserveDraft, keepGlobalCollectionDrafts });
          }
          return next;
        } catch (error) { lastError = error; }
        await wait(100);
      }
      throw new Error(`PLUGIN_SETTINGS_REFRESH_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  async function refreshCurrent() {
    if (rebindPromise) return rebindPromise;
    if (refreshPromise) return refreshPromise;
    const previous = current;
    refreshPromise = (async () => {
      const next = validatePluginSnapshot(await invoke("settings_plugins_get"));
      // A read started before a save must not replace its committed snapshot.
      if (!disposed && current === previous && (!current || JSON.stringify(next) !== JSON.stringify(current))) {
        initialize(next, { preserveDraft: true });
      }
      return next;
    })().finally(() => { refreshPromise = null; });
    return refreshPromise;
  }

  return Object.freeze({
    initialize,
    snapshot: () => current,
    draft: () => clone(readDraft()),
    isDirty() {
      return Boolean(current && JSON.stringify(readDraft()) !== JSON.stringify({ enabledById: {}, settingsById: {} }));
    },
    async save({ keepGlobalCollectionDrafts = false } = {}) {
      if (!current) throw new Error("Plugin settings are not initialized");
      const settings = editableDraft(current, clone(readDraft()));
      const previousGeneration = current.coreGenerationId;
      let hasDetailedSettings = false;
      let enableResultPending = false;
      try {
        for (const [pluginId, enabled] of Object.entries(settings.enabledById)) {
          const plugin = current.plugins.find((item) => item.pluginId === pluginId);
          if (!plugin) throw new Error("PLUGIN_ENABLED_REQUEST_INVALID");
          enableResultPending = true;
          const result = validateManagementSnapshot(await invoke("settings_plugins_enabled_set", {
            windowGeneration: current.windowGeneration,
            coreGenerationId: previousGeneration,
            revision: current.revision,
            installId: plugin.installId,
            enabled,
          }));
          if (result.managementAction !== "enabled_changed" || result.installId !== plugin.installId
              || result.pluginId !== pluginId) throw new Error("PLUGIN_MANAGEMENT_RESPONSE_INVALID");
          current = Object.freeze(Object.fromEntries(SNAPSHOT_KEYS.map((key) => [key, result[key]])));
          enableResultPending = false;
          if (result.applicationState === "error") {
            initialize(current, { preserveDraft: true });
            const error = new Error(`开关已保存，但${plugin.name}${enabled ? "启动" : "停止"}失败。请查看插件状态。`);
            error.code = result.applicationReasonCode;
            throw error;
          }
        }
        for (const [pluginId, sections] of Object.entries(settings.settingsById)) {
          for (const [sectionId, values] of Object.entries(sections)) {
            hasDetailedSettings = true;
            const result = await invoke("settings_plugins_save", {
              windowGeneration: current.windowGeneration,
              coreGenerationId: previousGeneration,
              pluginId,
              sectionId,
              values,
            });
            if (result?.saved !== true || result.pluginId !== pluginId || result.sectionId !== sectionId
                || result.changePlan !== "applied" || result.applicationState !== "applied"
                || result.applicationReasonCode !== "READY") {
              throw new Error("PLUGIN_SETTINGS_CHANGE_PLAN_INVALID");
            }
          }
        }
        let next;
        if (hasDetailedSettings) {
          next = await bindCurrent({ preserveDraft: false, keepGlobalCollectionDrafts });
        } else {
          next = current;
          initialize(next, { preserveDraft: false, keepGlobalCollectionDrafts });
        }
        return Object.freeze({
          ...next,
          changePlan: "applied",
          applicationState: "applied",
          applicationReasonCode: "READY",
        });
      } catch (error) {
        if (enableResultPending || uncertainManagementError(error)) {
          // The desired state may already be saved even when the reply is lost.
          try { await bindCurrent({ preserveDraft: true }); } catch { /* keep the original save error */ }
        }
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
    refreshCurrent,
    discard() { if (current) applySnapshot(current, { preserveDraft: false, draft: null }); onDirty(); },
    dispose() { disposed = true; current = null; rebindPromise = null; refreshPromise = null; },
  });
}
