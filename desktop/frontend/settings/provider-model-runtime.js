export function validateProviderModelSnapshot(input) {
  return Object.freeze(structuredClone(input));
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
    dispose() {
      cancelOperations().catch(onError);
      snapshot = null;
      baseline = null;
    },
  });
}
