const DEFAULT_DELAYS = Object.freeze({ boot: 80, normal: 420, slow: 8000, restart: 520 });

const LONG_REPLY =
  "夜色把桌面压得很安静，消息仍然应该待在它自己的边界里。这里是一段用于检查长文本布局的确定性回复：中文、English、かな、数字 0123456789 与一个不会自然换行的标识 SakuraRuntimeV2PresentationBoundaryMustRemainInsideTheWindow。继续向下阅读时，气泡可以滚动，立绘、输入框和窗口锚点都不应跳动。动画只负责表现，不应夺走键盘焦点，也不应阻止取消、拖动或关闭。最后一段用于确认末尾仍可见：如果你读到了这里，长文本边界工作正常。";

function cleanMessage(value) {
  return String(value ?? "").trim().slice(0, 4096);
}

function scenarioFor(message) {
  const command = message.split(/\s+/, 1)[0].toLowerCase();
  return new Set(["/slow", "/error", "/long", "/multi", "/restart"]).has(command)
    ? command.slice(1)
    : "normal";
}

function normalReply(message, portraits) {
  const visible = message.replace(/^\/slow\s*/i, "").trim() || "这是一条慢响应测试。";
  return [
    {
      text: `收到。这里先用确定性本地回复呈现这条消息：${visible.slice(0, 180)}`,
      translation: "",
      tone: "calm",
      portrait: portraits.default,
      suppressTts: true,
    },
  ];
}

function replyFor(scenario, message, portraits) {
  if (scenario === "long") {
    return [{ text: LONG_REPLY, translation: "", tone: "calm", portrait: portraits.default, suppressTts: true }];
  }
  if (scenario === "multi") {
    const sequence = portraits.multi.length ? portraits.multi : [portraits.default];
    return [
      { text: "第一段：我正在确认消息边界。", translation: "", tone: "calm", portrait: sequence[0], suppressTts: true },
      { text: "第二段：立绘会随这一段切换，但输入仍然可用。", translation: "", tone: "bright", portrait: sequence[1 % sequence.length], suppressTts: true },
      { text: "第三段：完整回复结束，且没有使用 token streaming。", translation: "", tone: "calm", portrait: sequence[2 % sequence.length], suppressTts: true },
    ];
  }
  return normalReply(message, portraits);
}

export function createFakeChatCore({
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  delays = {},
  portraits,
} = {}) {
  if (!portraits?.default || !Array.isArray(portraits?.multi)) {
    throw new Error("Fake Core requires caller-owned portrait keys");
  }
  const timing = { ...DEFAULT_DELAYS, ...delays };
  const listeners = new Set();
  const timers = new Set();
  const operations = new Map();
  let generationNumber = 1;
  let generationId = "fake-generation-1";
  let operationNumber = 0;
  let revision = 0;
  let lifecycleStatus = "startup";
  let disposed = false;

  function emit(event) {
    if (disposed) return;
    const publication = Object.freeze({ generationId, generationNumber, ...event });
    for (const listener of listeners) listener(publication);
  }

  function schedule(callback, delay) {
    let timer = null;
    timer = setTimer(() => {
      timers.delete(timer);
      if (!disposed) callback();
    }, delay);
    timers.add(timer);
    return timer;
  }

  function publishLifecycle(status) {
    lifecycleStatus = status;
    revision += 1;
    emit({ type: "lifecycle", status, revision });
  }

  function terminal(operation, type, payload = {}) {
    if (!operations.has(operation.operationId) || operation.generationId !== generationId) return false;
    if (operation.timer != null) {
      clearTimer(operation.timer);
      timers.delete(operation.timer);
    }
    operations.delete(operation.operationId);
    emit({ type, operationId: operation.operationId, ...payload });
    return true;
  }

  function beginRestart() {
    const active = [...operations.values()];
    for (const operation of active) terminal(operation, "chat.cancelled", { reason: "core_restart" });
    publishLifecycle("core_crashed");
    schedule(() => {
      generationNumber += 1;
      generationId = `fake-generation-${generationNumber}`;
      publishLifecycle("restarting");
      schedule(() => publishLifecycle("ready"), timing.boot);
    }, timing.restart);
  }

  return Object.freeze({
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start() {
      if (disposed) return;
      publishLifecycle("startup");
      schedule(() => publishLifecycle("initializing"), 0);
      schedule(() => publishLifecycle("ready"), timing.boot);
    },
    send({ message }) {
      const text = cleanMessage(message);
      if (!text) throw new Error("EMPTY_MESSAGE");
      if (lifecycleStatus !== "ready") throw new Error("FAKE_CORE_NOT_READY");
      const operationId = `fake-operation-${++operationNumber}`;
      const operation = { operationId, generationId, timer: null };
      operations.set(operationId, operation);
      emit({ type: "chat.started", operationId });
      const scenario = scenarioFor(text);
      if (scenario === "restart") {
        operation.timer = schedule(beginRestart, 160);
      } else if (scenario === "error") {
        operation.timer = schedule(
          () =>
            terminal(operation, "chat.failed", {
              error: {
                code: "FAKE_PROVIDER_UNREACHABLE",
                message: "暂时无法完成回复。你可以直接重新发送。",
                retryable: true,
                details: {},
              },
            }),
          timing.normal,
        );
      } else {
        operation.timer = schedule(
          () => terminal(operation, "chat.completed", { reply: { segments: replyFor(scenario, text, portraits) } }),
          scenario === "slow" ? timing.slow : timing.normal,
        );
      }
      return Object.freeze({ operationId, generationId, generationNumber });
    },
    cancel(operationId) {
      const operation = operations.get(String(operationId ?? ""));
      const accepted = operation ? terminal(operation, "chat.cancelled", { reason: "user" }) : false;
      return Object.freeze({ accepted, operationId: String(operationId ?? "") });
    },
    restart() {
      if (!disposed && !["core_crashed", "restarting"].includes(lifecycleStatus)) beginRestart();
    },
    snapshot() {
      return Object.freeze({ generationId, generationNumber, lifecycleStatus, activeOperations: operations.size });
    },
    dispose() {
      disposed = true;
      for (const timer of timers) clearTimer(timer);
      timers.clear();
      operations.clear();
      listeners.clear();
    },
  });
}
