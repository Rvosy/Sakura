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

export function createProviderModelController({ invoke, readDraft, applySnapshot, onDirty }) {
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

  async function refreshCurrent() {
    const revision = ++refreshRevision;
    const next = validateProviderModelSnapshot(await invoke("settings_provider_model_get"));
    if (disposed || revision !== refreshRevision) return next;
    await initialize(next);
    return next;
  }

  async function save() {
    if (!snapshot || disposed) throw new Error("模型设置尚未就绪。");
    const draft = readDraft();
    const issue = findProviderModelSelectionIssue({ providers: snapshot.providers, modelSlots: draft.model_slots,
      slotFields: snapshot.model_slots.map(slot => ({ id: slot.identity, label: slot.label, required: slot.required, selection: slot.selection })) });
    if (issue) throw new Error(`${issue.label}未通过校验，请重新选择模型。`);
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
      throw new Error(`部分模型设置已保存；${result.failed_slot?.identity || "未知槽位"} 保存失败，页面已刷新为实际状态。`);
    }
    return result;
  }

  return Object.freeze({ initialize, save, isDirty, refreshCurrent,
    rebindIdentity(coreGenerationId) {
      if (!snapshot || typeof coreGenerationId !== "string" || !coreGenerationId) throw new Error("invalid settings core generation");
      refreshRevision++;
      snapshot = Object.freeze({ ...snapshot, core_generation_id: coreGenerationId });
    },
    dispose() { disposed = true; refreshRevision++; snapshot = baseline = null; },
  });
}
