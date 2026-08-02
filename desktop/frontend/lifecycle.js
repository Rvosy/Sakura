const STATUSES = new Set([
  "startup",
  "initializing",
  "ready",
  "setup_required",
  "degraded",
  "failed",
  "core_crashed",
  "restarting",
  "rehydrating",
]);

const STATUS_CODES = Object.freeze({
  startup: "SHELL_STARTUP",
  initializing: "CORE_INITIALIZING",
  ready: "CORE_READY",
  setup_required: "CORE_SETUP_REQUIRED",
  degraded: "CORE_DEGRADED",
  failed: "CORE_FAILED",
  core_crashed: "CORE_CRASHED",
  restarting: "CORE_RESTARTING",
  rehydrating: "CORE_REHYDRATING",
});

export const LIFECYCLE_COPY = Object.freeze({
  startup: ["startup", "Sakura 正在启动"],
  initializing: ["initializing", "Core 正在初始化"],
  ready: ["ready", "Sakura 已就绪"],
  setup_required: ["setup_required", "需要完成基础设置"],
  degraded: ["degraded", "Sakura 以受限状态运行"],
  failed: ["failed", "Core 启动失败"],
  core_crashed: ["Core crashed", "Core 已意外退出"],
  restarting: ["restarting", "Core 正在安全重启"],
  rehydrating: ["rehydrating", "Core 已恢复，正在还原桌宠状态"],
});

const CRASH_FAILURES = new Set(["unexpected_exit", "connection_lost"]);

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
  } else if (supervisor.state === "restarting" || supervisor.restartPending) {
    status = "restarting";
  } else if (supervisor.state === "stopping" || supervisor.state === "exited") {
    status = CRASH_FAILURES.has(supervisor.lastFailure) ? "core_crashed" : "failed";
  } else if (supervisor.state === "failed") {
    status = CRASH_FAILURES.has(supervisor.lastFailure) ? "core_crashed" : "failed";
  } else {
    status = "startup";
  }

  const [label, headline] = LIFECYCLE_COPY[status];
  return Object.freeze({
    status,
    label,
    headline,
    code: STATUS_CODES[status],
    canRetry: status === "failed" || (
      status === "core_crashed"
      && supervisor.state === "failed"
      && !supervisor.restartPending
    ),
    canExit: true,
  });
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
