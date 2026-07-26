import { FAKE_CORE_INITIAL_MESSAGE } from "./fake-chat-core.js";

const LIFECYCLE_COPY = Object.freeze({
  startup: ["startup", "Sakura 正在启动"],
  initializing: ["initializing", "Fake Core 正在初始化"],
  ready: ["ready", "Fake Core 已就绪"],
  failed: ["failed", "Fake Core 启动失败"],
  core_crashed: ["Core crashed", "Fake Core 已断开"],
  restarting: ["restarting", "Fake Core 正在重连"],
});

function initialState() {
  return Object.freeze({
    generationId: null,
    generationNumber: 0,
    revision: 0,
    lifecycle: "startup",
    lifecycleLabel: "startup",
    lifecycleHeadline: "Sakura 正在启动",
    phase: "booting",
    operationId: null,
    bubbleText: "正在准备本地测试会话……",
    segments: Object.freeze([]),
    error: null,
    portrait: "idle",
    canCancel: false,
    canSkip: false,
  });
}

function normalizedSegments(reply) {
  if (!Array.isArray(reply?.segments)) return Object.freeze([]);
  return Object.freeze(
    reply.segments
      .filter((segment) => segment && typeof segment.text === "string" && segment.text.length > 0)
      .map((segment) =>
        Object.freeze({
          text: segment.text,
          translation: typeof segment.translation === "string" ? segment.translation : "",
          tone: typeof segment.tone === "string" ? segment.tone : "calm",
          portrait: typeof segment.portrait === "string" ? segment.portrait : "idle",
          suppressTts: segment.suppressTts !== false,
        }),
      ),
  );
}

export function createChatPresentationReducer() {
  let state = initialState();

  function acceptGeneration(event) {
    if (!Number.isSafeInteger(event?.generationNumber) || event.generationNumber < 1) return false;
    if (event.generationNumber < state.generationNumber) return false;
    if (event.generationNumber === state.generationNumber && state.generationId && event.generationId !== state.generationId)
      return false;
    return true;
  }

  function result(applied) {
    return Object.freeze({ applied, state });
  }

  return Object.freeze({
    reduce(event) {
      if (!event || !acceptGeneration(event)) return result(false);
      if (event.type === "lifecycle") {
        if (!Object.hasOwn(LIFECYCLE_COPY, event.status)) return result(false);
        if (!Number.isSafeInteger(event.revision) || event.revision < 0) return result(false);
        if (event.generationNumber === state.generationNumber && event.revision < state.revision) return result(false);
        const generationChanged = event.generationNumber > state.generationNumber;
        const [lifecycleLabel, lifecycleHeadline] = LIFECYCLE_COPY[event.status];
        const ready = event.status === "ready";
        state = Object.freeze({
          ...state,
          generationId: event.generationId,
          generationNumber: event.generationNumber,
          revision: event.revision,
          lifecycle: event.status,
          lifecycleLabel,
          lifecycleHeadline,
          phase: ready ? (generationChanged || ["booting", "reconnecting"].includes(state.phase) ? "ready" : state.phase) : ["core_crashed", "restarting"].includes(event.status) ? "reconnecting" : "booting",
          operationId: ready ? state.operationId : null,
          bubbleText: ready
            ? generationChanged || ["booting", "reconnecting"].includes(state.phase)
              ? FAKE_CORE_INITIAL_MESSAGE
              : state.bubbleText
            : event.status === "core_crashed"
              ? "连接已断开，正在回收旧回复……"
              : event.status === "restarting"
                ? "正在建立新的本地测试会话……"
                : "正在准备本地测试会话……",
          segments: ready ? state.segments : Object.freeze([]),
          error: null,
          portrait: ready ? state.portrait : "concerned",
          canCancel: false,
          canSkip: false,
        });
        return result(true);
      }

      if (event.generationNumber !== state.generationNumber || event.generationId !== state.generationId)
        return result(false);
      if (event.type === "chat.started") {
        if (state.lifecycle !== "ready" || !event.operationId || state.canCancel) return result(false);
        state = Object.freeze({
          ...state,
          phase: "thinking",
          operationId: event.operationId,
          bubbleText: "正在组织完整回复……",
          segments: Object.freeze([]),
          error: null,
          portrait: "thinking",
          canCancel: true,
          canSkip: false,
        });
        return result(true);
      }

      if (!event.operationId || event.operationId !== state.operationId) return result(false);
      if (event.type === "chat.completed" && state.phase === "thinking") {
        const segments = normalizedSegments(event.reply);
        if (!segments.length) return result(false);
        state = Object.freeze({
          ...state,
          phase: "typing",
          segments,
          bubbleText: "",
          portrait: segments[0].portrait,
          canCancel: false,
          canSkip: true,
        });
        return result(true);
      }
      if (event.type === "chat.failed" && state.phase === "thinking") {
        const message = typeof event.error?.message === "string" ? event.error.message : "Fake Core 请求失败。";
        state = Object.freeze({
          ...state,
          phase: "error",
          operationId: null,
          bubbleText: message,
          segments: Object.freeze([]),
          error: Object.freeze({ code: String(event.error?.code || "FAKE_CORE_FAILED"), retryable: Boolean(event.error?.retryable) }),
          portrait: "concerned",
          canCancel: false,
          canSkip: false,
        });
        return result(true);
      }
      if (event.type === "chat.cancelled" && state.phase === "thinking") {
        state = Object.freeze({
          ...state,
          phase: "cancelled",
          operationId: null,
          bubbleText: event.reason === "core_restart" ? "旧回复已随连接关闭。" : "已取消当前回复。",
          segments: Object.freeze([]),
          error: null,
          portrait: "idle",
          canCancel: false,
          canSkip: false,
        });
        return result(true);
      }
      return result(false);
    },
    setTypingText(text) {
      if (state.phase !== "typing") return result(false);
      state = Object.freeze({ ...state, bubbleText: String(text ?? "") });
      return result(true);
    },
    setTypingSegment(segment) {
      if (state.phase !== "typing") return result(false);
      state = Object.freeze({ ...state, portrait: segment?.portrait || segment?.tone || "idle" });
      return result(true);
    },
    finishTyping() {
      if (state.phase !== "typing") return result(false);
      state = Object.freeze({ ...state, phase: "settled", operationId: null, canSkip: false });
      return result(true);
    },
    current() {
      return state;
    },
  });
}
