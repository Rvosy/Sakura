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

function object(value, message) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message);
  return value;
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

export function createMemoryController({
  document,
  invoke,
  listen = null,
  applySnapshot,
  onDirty,
  onError,
  onModelEvent = () => {},
}) {
  let snapshot = null;
  let baseline = null;
  let disposed = false;
  let unlistenModel = null;

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

  function fillSettings() {
    trigger().min = "1";
    trigger().max = "50";
    trigger().value = String(snapshot.curation.triggerTurns);
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
    provider().value = snapshot.curationModelSlot.profileId;
    refillModels(snapshot.curationModelSlot.model);
  }

  async function initialize(input, { preserveEditor = true } = {}) {
    snapshot = validateMemorySnapshot(input);
    if (listen && !unlistenModel) {
      unlistenModel = await listen("sakura://memory-model-event", ({ payload }) => {
        receiveModelEvent(payload);
      });
    }
    fillSettings();
    baseline = clone(readSettings());
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
    return invoke(command, {
      windowGeneration: snapshot.windowGeneration,
      coreGenerationId: snapshot.coreGenerationId,
      ...args,
    });
  }

  async function rebind(previousGeneration) {
    const deadline = Date.now() + 10_000;
    let lastError = null;
    while (!disposed && Date.now() < deadline) {
      try {
        const next = validateMemorySnapshot(await invoke("settings_memory_get"));
        if (next.coreGenerationId !== previousGeneration) {
          await initialize(next, { preserveEditor: true });
          return;
        }
      } catch (error) {
        lastError = error;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`MEMORY_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
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
      const result = await call("settings_memory_search", { query, limit, layer: layer || null });
      return {
        ...result,
        memories: Array.isArray(result.memories) ? result.memories.map(normalizeMemoryRecord) : [],
      };
    },
    upsert: (memory) => call("settings_memory_upsert", { memory }),
    delete: (id) => call("settings_memory_delete", { id }),
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
      snapshot = null;
      baseline = null;
    },
    reportError: onError,
  });
}
