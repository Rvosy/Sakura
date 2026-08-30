import { isChatReadyLifecycle } from "../lifecycle.js";

const LIFECYCLE_COPY = Object.freeze({
  startup: ["正在启动", "正在启动"],
  initializing: ["正在准备", "正在准备会话"],
  ready: ["在线", "可以开始对话"],
  setup_required: ["需要设置", "请先完成聊天供应商设置"],
  degraded: ["受限", "聊天服务当前处于受限状态"],
  failed: ["不可用", "会话启动失败"],
  rehydrating: ["正在恢复", "正在恢复桌宠状态"],
});

function freezeState(value) {
  const historyLength = value.replyHistorySegments.length;
  const reviewEnabled = ["settled", "error"].includes(value.phase) && historyLength > 1;
  const historyIndex = Number.isInteger(value.replyHistoryIndex) ? value.replyHistoryIndex : -1;
  return Object.freeze({
    ...value,
    reviewingHistory: Boolean(value.showingReplyHistorySegment)
      && reviewEnabled
      && historyIndex >= 0
      && historyIndex < historyLength - 1,
    canReviewPrevious: reviewEnabled && historyIndex > 0,
    canReviewNext: reviewEnabled && historyIndex >= 0 && historyIndex < historyLength - 1,
  });
}

function initialState(defaultPortraitKey) {
  return freezeState({
    generationId: null,
    generationNumber: 0,
    revision: 0,
    lifecycle: "startup",
    lifecycleLabel: "startup",
    lifecycleHeadline: "Sakura 正在启动",
    phase: "booting",
    operationId: null,
    bubbleText: "",
    segments: Object.freeze([]),
    replyHistorySegments: Object.freeze([]),
    replyHistoryIndex: -1,
    currentReplyHistoryStart: -1,
    showingReplyHistorySegment: false,
    reviewingHistory: false,
    canReviewPrevious: false,
    canReviewNext: false,
    error: null,
    portrait: defaultPortraitKey,
    canCancel: false,
    canRetry: false,
    silentInteraction: false,
  });
}

export function composerPlaceholder(displayName, phase) {
  const name = String(displayName || "当前角色");
  return phase === "thinking" ? `${name}正在思考中…` : `和${name}说点什么……`;
}

function normalizedSegments(reply) {
  if (!Array.isArray(reply?.segments)) return Object.freeze([]);
  return Object.freeze(
    reply.segments
      .filter((segment) => segment && typeof segment === "object")
      .map((segment) =>
        Object.freeze({
          text: typeof segment.text === "string" ? segment.text : "",
          translation: typeof segment.translation === "string" ? segment.translation : "",
          tone: typeof segment.tone === "string" ? segment.tone : "calm",
          portrait: typeof segment.portrait === "string" ? segment.portrait : "idle",
          suppressTts: segment.suppressTts === true,
        }),
      ),
  );
}

export function createChatPresentationReducer({ initialMessage, defaultPortraitKey, thinkingPortraitKey, concernedPortraitKey } = {}) {
  if (!initialMessage || !defaultPortraitKey) throw new Error("character presentation is required");
  const concernedPortrait = concernedPortraitKey || defaultPortraitKey;
  let state = initialState(defaultPortraitKey);
  let hasReachedReady = false;
  let greetingStarted = false;

  function interruptedReplyState(value) {
    const historyEnd = value.currentReplyHistoryStart >= 0
      ? value.currentReplyHistoryStart
      : value.replyHistorySegments.length;
    const replyHistorySegments = Object.freeze(value.replyHistorySegments.slice(0, historyEnd));
    return {
      phase: "error",
      operationId: null,
      bubbleText: "连接中断，本次回复已停止。",
      segments: Object.freeze([]),
      replyHistorySegments,
      replyHistoryIndex: replyHistorySegments.length - 1,
      currentReplyHistoryStart: -1,
      showingReplyHistorySegment: false,
      error: Object.freeze({ code: "CHAT_INTERRUPTED", retryable: true }),
      canCancel: false,
      silentInteraction: false,
    };
  }

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
        const establishedPresentation = hasReachedReady;
        const initialStartup = !establishedPresentation && ["startup", "initializing"].includes(event.status);
        const [lifecycleLabel, defaultLifecycleHeadline] = LIFECYCLE_COPY[event.status];
        const lifecycleHeadline = event.status === "failed"
          && typeof event.failure?.message === "string"
          && event.failure.message
          ? event.failure.message
          : defaultLifecycleHeadline;
        const chatReady = isChatReadyLifecycle(event.status);
        const activeReplyInterrupted = establishedPresentation
          && ["thinking", "typing"].includes(state.phase)
          && Boolean(state.operationId)
          && (generationChanged || !chatReady);
        const preserveVisualState = establishedPresentation && (generationChanged || !chatReady);
        const preserveInteraction = chatReady
          && !generationChanged
          && Boolean(state.operationId)
          && (["thinking", "typing"].includes(state.phase) || state.silentInteraction);
        const preserveGreeting = greetingStarted
          && state.phase === "typing"
          && !state.operationId
          && (["startup", "initializing"].includes(event.status) || chatReady);
        const preserved = {
          ...state,
          generationId: event.generationId,
          generationNumber: event.generationNumber,
          revision: event.revision,
          lifecycle: event.status,
          lifecycleLabel,
          lifecycleHeadline,
          phase: activeReplyInterrupted
            ? "error"
            : preserveGreeting
            ? state.phase
            : preserveVisualState
            ? (["thinking", "typing"].includes(state.phase) ? "settled" : state.phase)
            : chatReady
              ? (state.phase === "booting" ? "ready" : state.phase)
              : "booting",
          operationId: preserveInteraction ? state.operationId : null,
          bubbleText: activeReplyInterrupted
            ? "连接中断，本次回复已停止。"
            : preserveVisualState || preserveGreeting
            ? state.bubbleText
            : chatReady || initialStartup
              ? state.bubbleText
              : event.status === "failed" && typeof event.failure?.message === "string"
                ? event.failure.message
                : "正在准备会话……",
          segments: preserveVisualState || preserveGreeting || chatReady ? state.segments : Object.freeze([]),
          replyHistorySegments: state.replyHistorySegments,
          replyHistoryIndex: state.replyHistoryIndex,
          currentReplyHistoryStart: state.currentReplyHistoryStart,
          showingReplyHistorySegment: state.showingReplyHistorySegment,
          error: activeReplyInterrupted
            ? Object.freeze({ code: "CHAT_INTERRUPTED", retryable: true })
            : state.error,
          portrait: preserveVisualState || chatReady || initialStartup ? state.portrait : concernedPortrait,
          canCancel: preserveInteraction && state.canCancel,
          silentInteraction: preserveInteraction && state.silentInteraction,
          canRetry: Boolean(event.canRetry),
        };
        state = freezeState(activeReplyInterrupted
          ? { ...preserved, ...interruptedReplyState(state) }
          : preserved);
        if (chatReady) hasReachedReady = true;
        return result(true);
      }

      if (event.generationNumber !== state.generationNumber || event.generationId !== state.generationId)
        return result(false);
      if (event.type === "chat.started") {
        if (!isChatReadyLifecycle(state.lifecycle) || !event.operationId || state.operationId) return result(false);
        if (event.presentation === "silent") {
          state = freezeState({
            ...state,
            operationId: event.operationId,
            silentInteraction: true,
            canCancel: false,
          });
          return result(true);
        }
        state = freezeState({
          ...state,
          phase: "thinking",
          operationId: event.operationId,
          bubbleText: ".",
          segments: Object.freeze([]),
          currentReplyHistoryStart: -1,
          showingReplyHistorySegment: false,
          error: null,
          portrait: state.portrait,
          canCancel: true,
          silentInteraction: false,
        });
        return result(true);
      }

      if (!event.operationId || event.operationId !== state.operationId) return result(false);
      if (event.type === "chat.completed" && (state.phase === "thinking" || state.silentInteraction)) {
        const segments = normalizedSegments(event.reply);
        if (!segments.length) return result(false);
        const currentReplyHistoryStart = state.replyHistorySegments.length;
        const replyHistorySegments = Object.freeze([...state.replyHistorySegments, ...segments]);
        state = freezeState({
          ...state,
          phase: "typing",
          segments,
          replyHistorySegments,
          replyHistoryIndex: currentReplyHistoryStart,
          currentReplyHistoryStart,
          showingReplyHistorySegment: false,
          bubbleText: state.bubbleText,
          portrait: state.portrait,
          canCancel: false,
          silentInteraction: false,
        });
        return result(true);
      }
      if (state.silentInteraction && ["chat.failed", "chat.cancelled"].includes(event.type)) {
        state = freezeState({
          ...state,
          operationId: null,
          silentInteraction: false,
          canCancel: false,
        });
        return result(true);
      }
      if (event.type === "chat.failed" && state.phase === "thinking") {
        const message = typeof event.error?.message === "string" ? event.error.message : "暂时无法完成回复。";
        state = freezeState({
          ...state,
          phase: "error",
          operationId: null,
          bubbleText: message,
          segments: Object.freeze([]),
          showingReplyHistorySegment: false,
          error: Object.freeze({ code: String(event.error?.code || "CHAT_FAILED"), retryable: Boolean(event.error?.retryable) }),
          portrait: state.portrait,
          canCancel: false,
          silentInteraction: false,
        });
        return result(true);
      }
      if (event.type === "chat.cancelled" && state.phase === "thinking") {
        state = freezeState({
          ...state,
          phase: "settled",
          operationId: null,
          bubbleText: event.reason === "core_restart" ? "旧回复已随连接关闭。" : "已取消当前回复。",
          segments: Object.freeze([]),
          showingReplyHistorySegment: false,
          error: null,
          portrait: state.portrait,
          canCancel: false,
          silentInteraction: false,
        });
        return result(true);
      }
      return result(false);
    },
    setTypingText(text) {
      if (state.phase !== "typing") return result(false);
      state = freezeState({ ...state, bubbleText: String(text ?? "") });
      return result(true);
    },
    setWaitingText(text) {
      if (!["thinking", "typing"].includes(state.phase)) return result(false);
      state = freezeState({ ...state, bubbleText: String(text ?? "") });
      return result(true);
    },
    setTypingSegment(segment, index = 0) {
      if (state.phase !== "typing") return result(false);
      const historyIndex = state.currentReplyHistoryStart >= 0 && Number.isInteger(index)
        ? state.currentReplyHistoryStart + index
        : state.replyHistoryIndex;
      state = freezeState({
        ...state,
        portrait: segment?.portrait || state.portrait,
        replyHistoryIndex: historyIndex,
        showingReplyHistorySegment: state.currentReplyHistoryStart >= 0,
      });
      return result(true);
    },
    refreshVisibleReply(text) {
      if (!["settled", "error"].includes(state.phase) || !state.showingReplyHistorySegment) return result(false);
      const segment = state.replyHistorySegments[state.replyHistoryIndex];
      if (!segment) return result(false);
      state = freezeState({ ...state, bubbleText: String(text ?? "") });
      return result(true);
    },
    reviewReplyAt(index, text) {
      if (!["settled", "error"].includes(state.phase) || !Number.isInteger(index)) return result(false);
      const segment = state.replyHistorySegments[index];
      if (!segment) return result(false);
      state = freezeState({
        ...state,
        bubbleText: String(text ?? ""),
        portrait: segment.portrait || state.portrait,
        replyHistoryIndex: index,
        showingReplyHistorySegment: true,
      });
      return result(true);
    },
    beginGreeting() {
      if (greetingStarted || !initialMessage || !["ready", "booting"].includes(state.phase)) return result(false);
      greetingStarted = true;
      state = freezeState({
        ...state,
        phase: "typing",
        bubbleText: "",
        showingReplyHistorySegment: false,
        segments: Object.freeze([Object.freeze({
          text: initialMessage,
          translation: "",
          tone: "calm",
          portrait: state.portrait,
          suppressTts: true,
        })]),
      });
      return result(true);
    },
    finishTyping() {
      if (state.phase !== "typing") return result(false);
      state = freezeState({ ...state, phase: "settled", operationId: null });
      return result(true);
    },
    current() {
      return state;
    },
  });
}
