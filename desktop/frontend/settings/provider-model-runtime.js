const SENSITIVE_KEY = /(password|api.?key|credential$|secret|(^|_)token($|_))/i;

function assertObject(value, message) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message);
  return value;
}

function assertNoSensitiveKeys(value) {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (SENSITIVE_KEY.test(key)) throw new Error("provider snapshot contains a sensitive field");
    assertNoSensitiveKeys(child);
  }
}

export function validateProviderModelSnapshot(input) {
  const snapshot = assertObject(input, "invalid provider settings snapshot");
  assertNoSensitiveKeys(snapshot);
  if (snapshot.schema_version !== 1) throw new Error("unsupported provider settings schema");
  if (!Number.isSafeInteger(snapshot.window_generation) || snapshot.window_generation < 1) {
    throw new Error("invalid settings window generation");
  }
  if (typeof snapshot.core_generation_id !== "string" || !snapshot.core_generation_id) {
    throw new Error("invalid settings core generation");
  }
  if (!Array.isArray(snapshot.providers)) throw new Error("invalid providers");
  const ids = new Set();
  const providers = snapshot.providers.map((provider) => {
    assertObject(provider, "invalid provider");
    if (
      typeof provider.id !== "string" || !provider.id
      || ids.has(provider.id)
      || typeof provider.alias !== "string"
      || typeof provider.base_url !== "string"
      || typeof provider.configured !== "boolean"
      || !Array.isArray(provider.models)
      || provider.models.some((model) => typeof model !== "string" || !model)
    ) throw new Error("invalid provider");
    ids.add(provider.id);
    return Object.freeze({ ...provider, models: Object.freeze([...provider.models]) });
  });
  if (!Array.isArray(snapshot.model_slots) || snapshot.model_slots.length < 2) {
    throw new Error("invalid model slots");
  }
  const identities = new Set();
  const modelSlots = snapshot.model_slots.map((slot) => {
    assertObject(slot, "invalid model slot");
    const selection = assertObject(slot.selection, "invalid model slot selection");
    const contextWindow = selection.context_window_tokens;
    if (
      typeof slot.identity !== "string" || !slot.identity || identities.has(slot.identity)
      || !["core", "plugin"].includes(slot.ownerType)
      || typeof slot.ownerId !== "string" || !slot.ownerId
      || typeof slot.slotId !== "string" || !slot.slotId
      || typeof slot.label !== "string" || !slot.label
      || typeof slot.description !== "string"
      || slot.modelKind !== "chat_completion"
      || typeof slot.required !== "boolean"
      || typeof slot.order !== "number"
      || typeof slot.reasonCode !== "string"
      || typeof selection.profile_id !== "string" || typeof selection.model !== "string"
      || (
        contextWindow !== undefined
        && contextWindow !== null
        && (!Number.isSafeInteger(contextWindow) || contextWindow < 4096 || contextWindow > 2000000)
      )
    ) throw new Error("invalid model slot");
    identities.add(slot.identity);
    return Object.freeze({ ...slot, selection: Object.freeze({ ...selection }) });
  });
  const settings = assertObject(snapshot.settings, "invalid model settings");
  return Object.freeze({
    ...snapshot,
    providers: Object.freeze(providers),
    model_slots: Object.freeze(modelSlots),
    settings: Object.freeze({ ...settings }),
  });
}

export function findProviderModelSelectionIssue({ providers, modelSlots, slotFields }) {
  const providersById = new Map(
    (Array.isArray(providers) ? providers : []).map((provider) => [provider.id, provider]),
  );
  for (const slot of Array.isArray(slotFields) ? slotFields : []) {
    const selection = modelSlots?.[slot.id] || {};
    const profileId = typeof selection.profile_id === "string" ? selection.profile_id : "";
    const model = typeof selection.model === "string" ? selection.model : "";
    if (Boolean(profileId) !== Boolean(model)) {
      return Object.freeze({ type: "incomplete", slotId: slot.id, label: slot.label });
    }
    if (!profileId) {
      if (slot.required) {
        return Object.freeze({ type: "required", slotId: slot.id, label: slot.label });
      }
      continue;
    }
    const provider = providersById.get(profileId);
    if (!provider || !Array.isArray(provider.models) || !provider.models.includes(model)) {
      return Object.freeze({ type: "reference", slotId: slot.id, label: slot.label });
    }
  }
  return null;
}

export function createProviderModelController({ invoke, readDraft, applySnapshot, onDirty, onError }) {
  let snapshot = null;
  let baseline = null;
  const operations = new Set();

  const operationId = () => globalThis.crypto?.randomUUID?.() || `provider-${Date.now()}-${Math.random()}`;
  const currentDraft = () => readDraft();
  const isDirty = () => Boolean(snapshot && baseline !== JSON.stringify(currentDraft()));

  async function initialize(raw) {
    snapshot = validateProviderModelSnapshot(raw);
    applySnapshot(snapshot);
    baseline = JSON.stringify(currentDraft());
    onDirty();
  }

  async function save() {
    if (!snapshot) throw new Error("provider settings are not initialized");
    const draft = currentDraft();
    const result = await invoke("settings_provider_model_save", {
      windowGeneration: snapshot.window_generation,
      coreGenerationId: snapshot.core_generation_id,
      draft,
    });
    if (result?.change_plan !== "applied") throw new Error("PROVIDER_SETTINGS_CHANGE_PLAN_INVALID");
    await initialize(await invoke("settings_provider_model_get"));
    if (result?.save_state === "partial") {
      const failed = result.failed_slot?.identity || "未知槽位";
      throw new Error(`部分模型设置已保存；${failed} 保存失败，页面已刷新为实际状态。`);
    }
    return result;
  }

  async function probe(kind, profile) {
    if (!snapshot) throw new Error("provider settings are not initialized");
    const id = operationId();
    operations.add(id);
    try {
      return await invoke("settings_provider_model_probe", {
        windowGeneration: snapshot.window_generation,
        coreGenerationId: snapshot.core_generation_id,
        operationId: id,
        kind,
        profile,
      });
    } finally {
      operations.delete(id);
    }
  }

  async function cancelOperations() {
    const pending = [...operations];
    if (!snapshot) return;
    await Promise.allSettled(pending.map((id) => invoke("settings_provider_model_cancel", {
      windowGeneration: snapshot.window_generation,
      coreGenerationId: snapshot.core_generation_id,
      operationId: id,
    })));
  }

  async function refreshCurrent() {
    const next = validateProviderModelSnapshot(await invoke("settings_provider_model_get"));
    await initialize(next);
    return next;
  }

  return Object.freeze({
    initialize,
    save,
    isDirty,
    listModels: (profile) => probe("list_models", profile),
    testConnection: (profile) => probe("test_connection", profile),
    cancelOperations,
    refreshCurrent,
    rebindIdentity(coreGenerationId) {
      if (!snapshot || typeof coreGenerationId !== "string" || !coreGenerationId) {
        throw new Error("invalid settings core generation");
      }
      snapshot = Object.freeze({ ...snapshot, core_generation_id: coreGenerationId });
    },
    rebase() {
      baseline = JSON.stringify(currentDraft());
      onDirty();
    },
    dispose() {
      cancelOperations().catch(onError);
      snapshot = null;
      baseline = null;
    },
  });
}
