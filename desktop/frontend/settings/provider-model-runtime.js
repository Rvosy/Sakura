export function validateProviderModelSnapshot(input) {
  if (!input || input.schema_version !== 2 || !Array.isArray(input.providers) || !Array.isArray(input.model_slots)) {
    throw new Error("MODEL_SETTINGS_SNAPSHOT_INVALID");
  }
  return Object.freeze(structuredClone(input));
}

export function findProviderModelSelectionIssue({ providers, modelSlots, slotFields }) {
  for (const slot of slotFields || []) {
    const ref = modelSlots?.[slot.id] || {};
    const parts = [ref.serviceKey, ref.profileId, ref.modelId];
    const filled = parts.filter(value => typeof value === "string" && value).length;
    if (filled && filled !== 3) return { type: "incomplete", slotId: slot.id, label: slot.label };
    if (!filled) {
      if (slot.required) return { type: "required", slotId: slot.id, label: slot.label };
      continue;
    }
    const provider = providers?.find(item => item.serviceKey === ref.serviceKey);
    const profile = provider?.profiles?.find(item => item.profileId === ref.profileId);
    if (!profile?.models?.some(item => item.modelId === ref.modelId)) {
      if (slot.selection && parts.every((value, index) => value === slot.selection[["serviceKey", "profileId", "modelId"][index]])) continue;
      return { type: "reference", slotId: slot.id, label: slot.label };
    }
  }
  return null;
}

export function createProviderModelController({ invoke, readDraft, applySnapshot, onDirty, readProviders }) {
  let snapshot = null;
  let baseline = null;
  let disposed = false;
  let refreshRevision = 0;
  const isDirty = () => Boolean(snapshot && baseline !== JSON.stringify(readDraft()));

  async function initialize(raw) {
    const value = validateProviderModelSnapshot(raw);
    if (disposed) return;
    snapshot = value;
    applySnapshot(snapshot);
    baseline = JSON.stringify(readDraft());
    onDirty();
  }

  async function refreshCurrent({ preserveDraft = false } = {}) {
    const revision = ++refreshRevision;
    const next = validateProviderModelSnapshot(await invoke("settings_provider_model_get"));
    if (disposed || revision !== refreshRevision) return next;
    const preserved = preserveDraft && isDirty() ? structuredClone(readDraft()) : null;
    await initialize(next);
    if (preserved) { applySnapshot(next, { draft: preserved }); onDirty(); }
    return next;
  }

  function validate() {
    if (!snapshot || disposed) throw new Error("模型设置尚未就绪。");
    const draft = readDraft();
    const issue = findProviderModelSelectionIssue({ providers: readProviders ? readProviders() : snapshot.providers, modelSlots: draft.model_slots,
      slotFields: snapshot.model_slots.map(slot => ({ id: slot.identity, label: slot.label, required: slot.required, selection: slot.selection })) });
    if (issue) throw new Error(`${issue.label}未通过校验，请重新选择模型。`);
    return draft;
  }

  async function save() {
    const draft = validate();
    const identity = snapshot.core_generation_id;
    const result = await invoke("settings_provider_model_save", {
      windowGeneration: snapshot.window_generation,
      coreGenerationId: snapshot.core_generation_id,
      draft,
    });
    if (disposed || snapshot.core_generation_id !== identity) throw new Error("模型设置会话已变化，请重新保存。");
    if (result?.change_plan !== "applied") throw new Error("PROVIDER_SETTINGS_CHANGE_PLAN_INVALID");
    await refreshCurrent();
    if (result.save_state === "partial") {
      const saved = new Set(result.saved_slots || []);
      const pending = { model_slots: Object.fromEntries(snapshot.model_slots.map(slot => [slot.identity,
        saved.has(slot.identity) ? slot.selection : draft.model_slots[slot.identity] || slot.selection])) };
      applySnapshot(snapshot, { draft: pending }); onDirty();
      throw new Error(`部分模型设置已保存；${result.failed_slot?.identity || "未知槽位"} 保存失败，未完成的修改已保留。`);
    }
    return result;
  }

  return Object.freeze({ initialize, save, validate, isDirty, refreshCurrent,
    rebindIdentity(coreGenerationId) {
      if (!snapshot || typeof coreGenerationId !== "string" || !coreGenerationId) throw new Error("invalid settings core generation");
      refreshRevision++;
      snapshot = Object.freeze({ ...snapshot, core_generation_id: coreGenerationId });
    },
    dispose() { disposed = true; refreshRevision++; snapshot = baseline = null; },
  });
}
