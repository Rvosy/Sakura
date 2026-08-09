const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion",
  "runtimeLimits",
  "confirmationPolicy",
  "windowGeneration",
  "coreGenerationId",
]);
const LIMIT_KEYS = Object.freeze([
  "maxAgentStepsPerTurn",
  "maxToolCallsPerStep",
  "maxToolCallsPerTurn",
]);
const LIMITS = Object.freeze({
  maxAgentStepsPerTurn: [1, 12],
  maxToolCallsPerStep: [1, 10],
  maxToolCallsPerTurn: [1, 30],
});

function exactKeys(value, keys) {
  return Boolean(
    value
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key)),
  );
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stable(value) {
  return JSON.stringify(value);
}

function validateValues(input) {
  if (!exactKeys(input.runtimeLimits, LIMIT_KEYS)) throw new Error("invalid Tools runtime limits");
  for (const key of LIMIT_KEYS) {
    const value = input.runtimeLimits[key];
    const [minimum, maximum] = LIMITS[key];
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
      throw new Error(`invalid Tools runtime limit: ${key}`);
    }
  }
  if (input.runtimeLimits.maxToolCallsPerTurn < input.runtimeLimits.maxToolCallsPerStep) {
    throw new Error("整轮工具数不能小于每步工具数");
  }
  if (!["risk_based", "confirm_writes"].includes(input.confirmationPolicy)) {
    throw new Error("invalid Tools confirmation policy");
  }
}

export function validateToolsSnapshot(input) {
  if (!exactKeys(input, SNAPSHOT_KEYS) || input.schemaVersion !== 1) {
    throw new Error("invalid Tools settings snapshot");
  }
  if (!Number.isSafeInteger(input.windowGeneration) || input.windowGeneration < 1) {
    throw new Error("invalid Tools settings window generation");
  }
  if (typeof input.coreGenerationId !== "string" || !input.coreGenerationId) {
    throw new Error("invalid Tools Core generation");
  }
  validateValues(input);
  return Object.freeze({
    ...input,
    runtimeLimits: Object.freeze({ ...input.runtimeLimits }),
  });
}

function transitionError(error) {
  const message = String(error?.message || error || "");
  return [
    "SETTINGS_CORE_GENERATION_MISMATCH",
    "SETTINGS_CORE_UNAVAILABLE",
    "CORE_RESTART",
    "CORE_GENERATION",
  ].some((code) => message.includes(code));
}

export function createToolsController({
  document,
  invoke,
  onDirty,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  const controls = {
    maxAgentStepsPerTurn: document.getElementById("agentSteps"),
    maxToolCallsPerStep: document.getElementById("toolCallsPerStep"),
    maxToolCallsPerTurn: document.getElementById("toolCallsPerTurn"),
    confirmationPolicy: document.getElementById("toolConfirmationPolicy"),
  };
  let snapshot = null;
  let baseline = null;
  let disposed = false;
  let rebindPromise = null;

  function read() {
    const runtimeLimits = {};
    for (const key of LIMIT_KEYS) {
      const value = Number.parseInt(controls[key].value, 10);
      runtimeLimits[key] = value;
    }
    const settings = {
      runtimeLimits,
      confirmationPolicy: controls.confirmationPolicy.value,
    };
    validateValues(settings);
    return settings;
  }

  function fill(settings) {
    for (const key of LIMIT_KEYS) {
      const [minimum, maximum] = LIMITS[key];
      controls[key].min = String(minimum);
      controls[key].max = String(maximum);
      controls[key].value = String(settings.runtimeLimits[key]);
    }
    controls.maxToolCallsPerTurn.min = String(settings.runtimeLimits.maxToolCallsPerStep);
    controls.confirmationPolicy.value = settings.confirmationPolicy;
  }

  function initialize(input, { preserveDraft = false } = {}) {
    let draft = null;
    if (preserveDraft && snapshot) {
      try { draft = read(); } catch { draft = null; }
    }
    snapshot = validateToolsSnapshot(input);
    baseline = {
      runtimeLimits: clone(snapshot.runtimeLimits),
      confirmationPolicy: snapshot.confirmationPolicy,
    };
    fill(draft || baseline);
    onDirty();
  }

  async function bindCurrent(previousGeneration, { requireChange, preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validateToolsSnapshot(await invoke("settings_tools_get"));
          if (!requireChange || next.coreGenerationId !== previousGeneration) {
            initialize(next, { preserveDraft });
            return next;
          }
        } catch (error) {
          lastError = error;
        }
        await wait(100);
      }
      throw new Error(`TOOLS_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  for (const key of LIMIT_KEYS) {
    controls[key].addEventListener("input", () => {
      if (key === "maxToolCallsPerStep") {
        controls.maxToolCallsPerTurn.min = controls.maxToolCallsPerStep.value;
      }
      onDirty();
    });
  }
  controls.confirmationPolicy.addEventListener("change", onDirty);

  return Object.freeze({
    initialize,
    isDirty() {
      try { return Boolean(baseline && stable(read()) !== stable(baseline)); } catch { return true; }
    },
    async save() {
      if (!snapshot) throw new Error("Tools settings are not initialized");
      const settings = read();
      const previousGeneration = snapshot.coreGenerationId;
      try {
        const result = await invoke("settings_tools_save", {
          windowGeneration: snapshot.windowGeneration,
          coreGenerationId: previousGeneration,
          settings,
        });
        if (result?.changePlan !== "core_restart_required") {
          throw new Error("TOOLS_SETTINGS_CHANGE_PLAN_INVALID");
        }
      } catch (error) {
        if (transitionError(error)) {
          await bindCurrent(previousGeneration, { requireChange: false, preserveDraft: true });
        }
        throw error;
      }
      return bindCurrent(previousGeneration, { requireChange: true, preserveDraft: false });
    },
    async refreshCurrent() {
      return bindCurrent(snapshot?.coreGenerationId || "", { requireChange: false, preserveDraft: true });
    },
    discard() {
      if (baseline) fill(baseline);
      onDirty();
    },
    dispose() {
      disposed = true;
      snapshot = null;
      rebindPromise = null;
    },
  });
}
