const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion",
  "enabled",
  "windowGeneration",
  "coreGenerationId",
]);

function exactKeys(value, keys) {
  return Boolean(
    value
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key)),
  );
}

export function validateAgentTraceSnapshot(input) {
  if (!exactKeys(input, SNAPSHOT_KEYS) || input.schemaVersion !== 1) {
    throw new Error("invalid Agent trace settings snapshot");
  }
  if (typeof input.enabled !== "boolean") throw new Error("invalid Agent trace enabled value");
  if (!Number.isSafeInteger(input.windowGeneration) || input.windowGeneration < 1) {
    throw new Error("invalid Agent trace window generation");
  }
  if (typeof input.coreGenerationId !== "string" || !input.coreGenerationId) {
    throw new Error("invalid Agent trace Core generation");
  }
  return Object.freeze({ ...input });
}

export function createAgentTraceController({
  document,
  invoke,
  onDirty,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  const control = document.getElementById("agentTraceEnabled");
  let snapshot = null;
  let baseline = true;
  let disposed = false;
  let rebindPromise = null;

  control.addEventListener("change", onDirty);

  function initialize(input, { preserveDraft = false } = {}) {
    const draft = preserveDraft && snapshot ? control.checked : null;
    snapshot = validateAgentTraceSnapshot(input);
    baseline = snapshot.enabled;
    control.checked = draft ?? baseline;
    onDirty();
  }

  async function bindCurrent(previousGeneration, { requireChange, preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validateAgentTraceSnapshot(await invoke("settings_agent_trace_get"));
          if (!requireChange || next.coreGenerationId !== previousGeneration) {
            initialize(next, { preserveDraft });
            return next;
          }
        } catch (error) {
          lastError = error;
        }
        await wait(100);
      }
      throw new Error(`AGENT_TRACE_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  return Object.freeze({
    initialize,
    isDirty() { return Boolean(snapshot && control.checked !== baseline); },
    async save() {
      if (!snapshot) throw new Error("Agent trace settings are not initialized");
      const previousGeneration = snapshot.coreGenerationId;
      const result = await invoke("settings_agent_trace_save", {
        windowGeneration: snapshot.windowGeneration,
        coreGenerationId: previousGeneration,
        settings: { enabled: control.checked },
      });
      if (result?.changePlan !== "core_restart_required") {
        throw new Error("AGENT_TRACE_SETTINGS_CHANGE_PLAN_INVALID");
      }
      return bindCurrent(previousGeneration, { requireChange: true, preserveDraft: false });
    },
    refreshCurrent() {
      return bindCurrent(snapshot?.coreGenerationId || "", { requireChange: false, preserveDraft: true });
    },
    discard() {
      control.checked = baseline;
      onDirty();
    },
    dispose() {
      disposed = true;
      snapshot = null;
      rebindPromise = null;
    },
  });
}
