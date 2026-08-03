const STATUSES = new Set(["ready", "loading", "degraded", "read_only", "failed", "stopped"]);
const LAYERS = Object.freeze([
  { id: "core_profile", label: "常驻档案" },
  { id: "semantic", label: "长期事实" },
  { id: "episodic", label: "事件总结" },
  { id: "procedural", label: "协作规则" },
  { id: "session", label: "当前任务" },
]);

const clone = (value) => JSON.parse(JSON.stringify(value));
const stable = (value) => JSON.stringify(value);
const GENERATION_TRANSITION_CODES = [
  "GENERATION_INVALIDATED",
  "SETTINGS_CORE_GENERATION_MISMATCH",
  "SETTINGS_CORE_UNAVAILABLE",
  "MEMORY_OPERATION_INVALIDATED",
  "Router closed",
];
const RETRYABLE_MEMORY_READ_CODES = [
  ...GENERATION_TRANSITION_CODES,
  "SETTINGS_TRANSPORT_UNAVAILABLE",
  "REQUEST_DEADLINE_EXCEEDED",
  "MEMORY_REBIND_FAILED",
  "MEMORY_CORE_RESTART_NOT_READY",
];

function object(value, message) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message);
  return value;
}

function codedError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function validateIdentity(snapshot) {
  if (!Number.isSafeInteger(snapshot.windowGeneration) || snapshot.windowGeneration < 1) {
    throw new Error("invalid Memory window generation");
  }
  if (typeof snapshot.coreGenerationId !== "string" || !snapshot.coreGenerationId) {
    throw new Error("invalid Memory Core generation");
  }
}

export function validateMemorySnapshot(input) {
  const snapshot = object(input, "invalid Memory snapshot");
  if (snapshot.schemaVersion !== 1 || !STATUSES.has(snapshot.status)) {
    throw new Error("unsupported Memory snapshot");
  }
  validateIdentity(snapshot);
  const curation = object(snapshot.curation, "invalid Memory curation settings");
  if (
    !Number.isSafeInteger(curation.triggerTurns)
    || curation.triggerTurns < 1
    || curation.triggerTurns > 50
    || !Number.isSafeInteger(curation.backfillLimit)
    || curation.backfillLimit < 1
  ) throw new Error("invalid Memory curation limits");
  const slot = object(snapshot.curationModelSlot, "invalid Memory curation model slot");
  if (typeof slot.profileId !== "string" || typeof slot.model !== "string") {
    throw new Error("invalid Memory curation model slot");
  }
  const providers = Array.isArray(snapshot.providerChoices) ? snapshot.providerChoices : [];
  for (const provider of providers) {
    object(provider, "invalid Memory provider choice");
    if (
      typeof provider.id !== "string"
      || typeof provider.alias !== "string"
      || !Array.isArray(provider.models)
      || provider.models.some((model) => typeof model !== "string")
    ) throw new Error("invalid Memory provider choice");
  }
  const embedding = object(snapshot.embedding, "invalid Memory embedding state");
  if (
    typeof embedding.model !== "string"
    || embedding.dimensions !== 384
    || typeof embedding.installed !== "boolean"
  ) throw new Error("invalid Memory embedding state");
  return Object.freeze(clone(snapshot));
}

export function normalizeMemoryRecord(input) {
  const record = object(input, "invalid Memory record");
  if (
    typeof record.id !== "string" || !record.id
    || typeof record.content !== "string"
    || !LAYERS.some((layer) => layer.id === record.layer)
    || typeof record.scope !== "string" || !record.scope
  ) throw new Error("invalid Memory record");
  return Object.freeze({
    ...record,
    created_at: String(record.createdAt || ""),
    updated_at: String(record.updatedAt || ""),
    last_accessed_at: String(record.lastAccessedAt || ""),
  });
}

export function isMemoryGenerationTransitionError(error) {
  const message = String(error?.message || error || "");
  return GENERATION_TRANSITION_CODES.some((code) => message.includes(code));
}

export function isRetryableMemoryReadError(error) {
  const identity = `${String(error?.code || "")} ${String(error?.message || error || "")}`;
  return RETRYABLE_MEMORY_READ_CODES.some((code) => identity.includes(code));
}

function isSafePreDispatchIdentityError(error) {
  const message = String(error?.message || error || "");
  return message.includes("SETTINGS_CORE_GENERATION_MISMATCH")
    || message.includes("SETTINGS_CORE_UNAVAILABLE");
}

export function createMemoryController({
  document,
  invoke,
  listen = null,
  applySnapshot,
  onDirty,
  onError,
  onModelEvent = () => {},
  onRebindState = () => {},
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  let snapshot = null;
  let baseline = null;
  let disposed = false;
  let unlistenModel = null;
  let rebindPromise = null;
  let rebinding = false;
  let generationRevision = 0;

  const provider = () => document.getElementById("memoryCurationProvider");
  const model = () => document.getElementById("memoryCurationModel");
  const trigger = () => document.getElementById("memoryTriggerTurns");

  function readSettings() {
    if (!snapshot) return null;
    const triggerTurns = Number.parseInt(trigger().value, 10);
    if (!Number.isSafeInteger(triggerTurns) || triggerTurns < 1 || triggerTurns > 50) {
      throw new Error("自动整理频率必须为 1–50 轮");
    }
    return {
      triggerTurns,
      curationModelSlot: {
        profileId: provider().value,
        model: model().value,
      },
    };
  }

  function refillModels(selected = "") {
    const choice = snapshot.providerChoices.find((item) => item.id === provider().value);
    model().textContent = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = choice ? "选择模型" : "未启用自动整理";
    model().append(empty);
    for (const name of choice?.models || []) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      model().append(option);
    }
    model().value = selected;
  }

  function settingsFromSnapshot() {
    return {
      triggerTurns: snapshot.curation.triggerTurns,
      curationModelSlot: {
        profileId: snapshot.curationModelSlot.profileId,
        model: snapshot.curationModelSlot.model,
      },
    };
  }

  function fillSettings(settings = settingsFromSnapshot()) {
    trigger().min = "1";
    trigger().max = "50";
    trigger().value = String(settings.triggerTurns);
    provider().textContent = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "不启用自动整理";
    provider().append(empty);
    for (const choice of snapshot.providerChoices) {
      const option = document.createElement("option");
      option.value = choice.id;
      option.textContent = choice.alias;
      provider().append(option);
    }
    provider().value = settings.curationModelSlot.profileId;
    refillModels(settings.curationModelSlot.model);
  }

  async function initialize(input, { preserveEditor = true, preserveSettings = false } = {}) {
    let settingsDraft = null;
    if (preserveSettings && snapshot) {
      try {
        settingsDraft = clone(readSettings());
      } catch {
        settingsDraft = null;
      }
    }
    snapshot = validateMemorySnapshot(input);
    generationRevision += 1;
    if (listen && !unlistenModel) {
      unlistenModel = await listen("sakura://memory-model-event", ({ payload }) => {
        receiveModelEvent(payload);
      });
    }
    fillSettings(settingsFromSnapshot());
    baseline = clone(readSettings());
    if (settingsDraft) fillSettings(settingsDraft);
    applySnapshot(snapshot, { layers: LAYERS, preserveEditor });
    onDirty();
  }

  function receiveModelEvent(input) {
    if (disposed || !snapshot || !input || typeof input !== "object") return;
    const task = snapshot.embedding.task;
    if (
      !task
      || input.generationId !== snapshot.coreGenerationId
      || input.windowGeneration !== snapshot.windowGeneration
      || input.taskId !== task.taskId
      || !Number.isSafeInteger(input.progress)
      || input.progress < 0
      || input.progress > 100
      || ![
        "memory.model.started",
        "memory.model.progress",
        "memory.model.completed",
        "memory.model.failed",
        "memory.model.cancelled",
      ].includes(input.type)
    ) return;
    const terminal = [
      "memory.model.completed",
      "memory.model.failed",
      "memory.model.cancelled",
    ].includes(input.type);
    const nextTask = Object.freeze({
      ...task,
      accepted: !terminal,
      status: input.type.replace("memory.model.", ""),
      stage: String(input.stage || ""),
      progress: input.progress,
      error: input.error || null,
    });
    snapshot = Object.freeze({
      ...snapshot,
      embedding: Object.freeze({
        ...snapshot.embedding,
        installed: input.type === "memory.model.completed" || snapshot.embedding.installed,
        task: nextTask,
      }),
    });
    onModelEvent(nextTask);
  }

  async function call(command, args = {}) {
    if (!snapshot) throw new Error("Memory settings are not initialized");
    if (rebindPromise) await rebindPromise;
    if (!snapshot || disposed) throw new Error("Memory settings are not initialized");
    const boundGeneration = snapshot.coreGenerationId;
    const boundRevision = generationRevision;
    const result = await invoke(command, {
      windowGeneration: snapshot.windowGeneration,
      coreGenerationId: boundGeneration,
      ...args,
    });
    if (
      !snapshot
      || snapshot.coreGenerationId !== boundGeneration
      || generationRevision !== boundRevision
    ) {
      throw new Error("MEMORY_OPERATION_INVALIDATED");
    }
    return result;
  }

  async function bindCurrent(previousGeneration, requireGenerationChange) {
    if (rebindPromise) return rebindPromise;
    const previous = previousGeneration || snapshot?.coreGenerationId || "";
    const deadline = Date.now() + 10_000;
    rebinding = true;
    onRebindState(true);
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validateMemorySnapshot(await invoke("settings_memory_get"));
          if (!requireGenerationChange || !previous || next.coreGenerationId !== previous) {
            await initialize(next, { preserveEditor: true, preserveSettings: true });
            return next;
          }
        } catch (error) {
          lastError = error;
        }
        await wait(100);
      }
      throw new Error(`MEMORY_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => {
      rebindPromise = null;
      rebinding = false;
      onRebindState(false);
    });
    return rebindPromise;
  }

  const rebind = (previousGeneration) => bindCurrent(previousGeneration, true);

  async function write(command, args) {
    const previous = snapshot?.coreGenerationId || "";
    try {
      return await call(command, args);
    } catch (error) {
      if (isSafePreDispatchIdentityError(error)) {
        try {
          await rebind(previous);
          return await call(command, args);
        } catch (retryError) {
          error = retryError;
        }
      }
      if (rebindPromise || isMemoryGenerationTransitionError(error)) {
        try {
          await rebind(previous);
        } catch {
          throw codedError(
            "MEMORY_REBIND_FAILED",
            "记忆连接暂未恢复，请保留当前草稿并稍后重试。",
          );
        }
        throw codedError(
          "MEMORY_WRITE_OUTCOME_UNCERTAIN",
          "Core 已恢复；已安全刷新记忆列表，请确认后重试本次操作。",
        );
      }
      throw error;
    }
  }

  provider().addEventListener("change", () => {
    refillModels();
    onDirty();
  });
  model().addEventListener("change", onDirty);
  trigger().addEventListener("input", onDirty);

  return Object.freeze({
    initialize,
    isDirty() {
      try {
        return Boolean(baseline && stable(readSettings()) !== stable(baseline));
      } catch {
        return true;
      }
    },
    async search({ query, limit, layer = "" }) {
      const previous = snapshot?.coreGenerationId || "";
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const result = await call("settings_memory_search", { query, limit, layer: layer || null });
          return {
            ...result,
            memories: Array.isArray(result.memories) ? result.memories.map(normalizeMemoryRecord) : [],
          };
        } catch (error) {
          if (attempt > 0 || (!rebindPromise && !isMemoryGenerationTransitionError(error))) throw error;
          await rebind(previous);
        }
      }
      throw new Error("记忆连接暂未恢复，请稍后刷新。");
    },
    upsert: (memory) => write("settings_memory_upsert", { memory }),
    delete: (id) => write("settings_memory_delete", { id }),
    async save() {
      const previous = snapshot.coreGenerationId;
      const result = await call("settings_memory_save", { settings: readSettings() });
      if (result?.changePlan === "core_restart_required") {
        await rebind(previous);
      } else {
        baseline = clone(readSettings());
        onDirty();
      }
      return result;
    },
    refreshCurrent: () => bindCurrent(snapshot?.coreGenerationId || "", false),
    isRebinding: () => rebinding,
    async downloadModel() {
      const result = await call("settings_memory_model_download");
      snapshot = Object.freeze({
        ...snapshot,
        embedding: Object.freeze({ ...snapshot.embedding, task: result }),
      });
      return result;
    },
    async importModel() {
      const result = await call("settings_memory_model_import");
      if (result?.accepted) {
        snapshot = Object.freeze({
          ...snapshot,
          embedding: Object.freeze({ ...snapshot.embedding, task: result }),
        });
      }
      return result;
    },
    async cancelModel() {
      const task = snapshot?.embedding?.task;
      if (!task?.accepted || !task.taskHandle) return false;
      const result = await call("settings_memory_model_cancel", { taskHandle: task.taskHandle });
      return Boolean(result?.accepted);
    },
    embedding: () => snapshot?.embedding || null,
    status: () => snapshot?.status || "unavailable",
    discard() {
      if (!baseline || !snapshot) return;
      snapshot = Object.freeze({
        ...snapshot,
        curation: Object.freeze({ ...snapshot.curation, triggerTurns: baseline.triggerTurns }),
        curationModelSlot: Object.freeze({ ...baseline.curationModelSlot }),
      });
      fillSettings();
      onDirty();
    },
    dispose() {
      disposed = true;
      const task = snapshot?.embedding?.task;
      if (task?.accepted && task.taskHandle) {
        call("settings_memory_model_cancel", { taskHandle: task.taskHandle }).catch(() => {});
      }
      if (typeof unlistenModel === "function") unlistenModel();
      unlistenModel = null;
      rebindPromise = null;
      rebinding = false;
      snapshot = null;
      baseline = null;
    },
    reportError: onError,
  });
}
