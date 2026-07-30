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
  const modelSlots = assertObject(snapshot.model_slots, "invalid model slots");
  for (const slot of ["chat", "vision_chat"]) {
    const value = assertObject(modelSlots[slot], `invalid ${slot} model slot`);
    if (typeof value.profile_id !== "string" || typeof value.model !== "string") {
      throw new Error(`invalid ${slot} model slot`);
    }
  }
  const settings = assertObject(snapshot.settings, "invalid model settings");
  return Object.freeze({
    ...snapshot,
    providers: Object.freeze(providers),
    model_slots: Object.freeze({
      chat: Object.freeze({ ...modelSlots.chat }),
      vision_chat: Object.freeze({ ...modelSlots.vision_chat }),
    }),
    settings: Object.freeze({ ...settings }),
  });
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
    const previousCoreGeneration = snapshot.core_generation_id;
    const result = await invoke("settings_provider_model_save", {
      windowGeneration: snapshot.window_generation,
      coreGenerationId: snapshot.core_generation_id,
      draft,
    });
    if (result?.change_plan === "core_restart_required") {
      await rebindAfterRestart(previousCoreGeneration);
    } else {
      await initialize(await invoke("settings_provider_model_get"));
    }
    return result;
  }

  async function rebindAfterRestart(previousCoreGeneration) {
    const deadline = Date.now() + 10_000;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const next = validateProviderModelSnapshot(await invoke("settings_provider_model_get"));
        if (next.core_generation_id !== previousCoreGeneration) {
          await initialize(next);
          return;
        }
      } catch (error) {
        lastError = error;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
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

  return Object.freeze({
    initialize,
    save,
    isDirty,
    listModels: (profile) => probe("list_models", profile),
    testConnection: (profile) => probe("test_connection", profile),
    cancelOperations,
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
