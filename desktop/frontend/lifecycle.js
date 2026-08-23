const STATUSES = new Set([
  "startup",
  "initializing",
  "ready",
  "setup_required",
  "degraded",
  "failed",
  "rehydrating",
]);

const STATUS_CODES = Object.freeze({
  startup: "SHELL_STARTUP",
  initializing: "CORE_INITIALIZING",
  ready: "CORE_READY",
  setup_required: "CORE_SETUP_REQUIRED",
  degraded: "CORE_DEGRADED",
  failed: "CORE_FAILED",
  rehydrating: "CORE_REHYDRATING",
});

export const LIFECYCLE_COPY = Object.freeze({
  startup: ["startup", "Sakura 正在启动"],
  initializing: ["initializing", "Core 正在初始化"],
  ready: ["ready", "Sakura 已就绪"],
  setup_required: ["setup_required", "需要完成基础设置"],
  degraded: ["degraded", "Sakura 以受限状态运行"],
  failed: ["failed", "Core 启动失败"],
  rehydrating: ["rehydrating", "Core 已恢复，正在还原桌宠状态"],
});

function readinessForCurrentGeneration(supervisor, snapshot) {
  if (!snapshot || snapshot.generationId !== supervisor.generationId) return null;
  return snapshot.readiness;
}

export function projectLifecycle({ supervisor, snapshot }) {
  let status;
  if (supervisor.state === "running") {
    const readiness = readinessForCurrentGeneration(supervisor, snapshot);
    status =
      readiness === "ready" ||
      readiness === "setup_required" ||
      readiness === "degraded" ||
      readiness === "failed"
        ? readiness
        : "initializing";
  } else if (supervisor.state === "failed" || supervisor.failure) {
    status = "failed";
  } else {
    status = "startup";
  }

  const [label, defaultHeadline] = LIFECYCLE_COPY[status];
  const failure = safeFailure(supervisor.failure);
  return Object.freeze({
    status,
    label,
    headline: failure?.message || defaultHeadline,
    code: STATUS_CODES[status],
    failure,
    canRetry: supervisor.state === "failed",
    canExit: true,
  });
}

function safeFailure(value) {
  if (
    !value
    || typeof value.code !== "string"
    || !/^[a-z0-9_]{1,64}$/.test(value.code)
    || typeof value.message !== "string"
  ) return null;
  const message = value.message.trim();
  if (!message || message.length > 160) return null;
  return Object.freeze({ code: value.code, message });
}

function safeVersion(value) {
  return typeof value === "string" && /^\d+(?:\.\d+){1,2}(?:[-+][A-Za-z0-9.-]+)?$/.test(value)
    ? value
    : "unavailable";
}

export function sanitizeDiagnostics(input) {
  const status = STATUSES.has(input?.status) ? input.status : "failed";
  return Object.freeze({
    status,
    code: STATUS_CODES[status],
    desktopVersion: safeVersion(input?.desktopVersion),
    coreVersion: safeVersion(input?.coreVersion),
    protocolVersion: safeVersion(input?.protocolVersion),
    logLocation: "Sakura application logs",
  });
}

export function createLifecycleReducer() {
  let accepted = null;

  return Object.freeze({
    reduce(event) {
      if (!event || !Number.isSafeInteger(event.generationNumber) || event.generationNumber < 0)
        return { applied: false };
      if (!Number.isSafeInteger(event.revision) || event.revision < 0) return { applied: false };
      if (accepted) {
        if (event.generationNumber < accepted.generationNumber) return { applied: false };
        if (
          event.generationNumber === accepted.generationNumber &&
          event.generationId !== accepted.generationId
        )
          return { applied: false };
        if (
          event.generationNumber === accepted.generationNumber &&
          event.revision < accepted.revision
        )
          return { applied: false };
      }
      if (!event.view || !STATUSES.has(event.view.status)) return { applied: false };
      accepted = Object.freeze({
        generationId: event.generationId ?? null,
        generationNumber: event.generationNumber,
        revision: event.revision,
        ...event.view,
      });
      return { applied: true, value: accepted };
    },
    current() {
      return accepted;
    },
  });
}
