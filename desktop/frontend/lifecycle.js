import { errorText } from './core/error-display.js';
const STATUS_CODES = Object.freeze({
  startup: "SHELL_STARTUP",
  initializing: "CORE_INITIALIZING",
  ready: "CORE_READY",
  setup_required: "CORE_SETUP_REQUIRED",
  degraded: "CORE_DEGRADED",
  failed: "CORE_FAILED",
  rehydrating: "CORE_REHYDRATING",
});

const CHAT_READY_STATUSES = new Set(["ready", "degraded"]);

export function isChatReadyLifecycle(status) {
  return CHAT_READY_STATUSES.has(status);
}

export const LIFECYCLE_COPY = Object.freeze({
  startup: ["startup", "Sakura 正在启动"],
  initializing: ["initializing", "正在准备 Sakura"],
  ready: ["ready", "Sakura 已就绪"],
  setup_required: ["setup_required", "需要完成基础设置"],
  degraded: ["degraded", "部分功能暂不可用"],
  failed: ["failed", "Sakura 启动失败"],
  rehydrating: ["rehydrating", "正在恢复桌宠"],
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
  if (!message) return null;
  return Object.freeze({ code: value.code, message: errorText(value.message) });
}
