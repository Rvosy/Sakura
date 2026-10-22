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
    if (filled && filled !== 3) return { type: "incomplete", slotId: slot.id, label: slot.label,
      message: `${slot.label}的选择尚未完成，请选择模型服务和模型。` };
    if (!filled) {
      if (slot.required) return { type: "required", slotId: slot.id, label: slot.label, message: `请为${slot.label}选择模型。` };
      continue;
    }
    const provider = providers?.find(item => item.serviceKey === ref.serviceKey);
    const profile = provider?.profiles?.find(item => item.profileId === ref.profileId);
    if (!profile?.models?.some(item => item.modelId === ref.modelId)) {
      if (slot.selection && parts.every((value, index) => value === slot.selection[["serviceKey", "profileId", "modelId"][index]])) continue;
      const target = `${slot.label}“${ref.modelId}”`;
      const message = !provider
        ? `${target}所属的模型服务当前不可用。请恢复该服务，或重新选择模型。`
        : !profile
          ? `${target}所属的连接“${ref.profileId}”已不在模型服务中。请恢复连接，或重新选择模型。`
          : `${target}已从“${profile.label || ref.profileId}”的模型列表移除。请重新添加该模型，或选择其他模型。`;
      return { type: "reference", slotId: slot.id, label: slot.label, message };
    }
  }
  return null;
}

export class ModelSelectionError extends Error {
  constructor(issue) {
    super(issue.message);
    this.issue = issue;
  }
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
    const next = await invoke("settings_provider_model_get");
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
    if (issue) throw new ModelSelectionError(issue);
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
    let refreshError = null;
    try { await refreshCurrent(); } catch (error) { refreshError = error; }
    if (result.save_state === "partial") {
      if (!refreshError) {
        const saved = new Set(result.saved_slots || []);
        const pending = { model_slots: Object.fromEntries(snapshot.model_slots.map(slot => [slot.identity,
          saved.has(slot.identity) ? slot.selection : draft.model_slots[slot.identity] || slot.selection])) };
        applySnapshot(snapshot, { draft: pending }); onDirty();
      }
      const message = `部分模型设置已保存；${result.failed_slot?.identity || "未知槽位"} 保存失败，未完成的修改已保留。${refreshError ? "当前状态刷新失败，请重新打开设置后确认。" : ""}`;
      throw new AggregateError([result.failed_slot, ...(refreshError ? [refreshError] : [])], message);
    }
    if (refreshError) throw refreshError;
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
