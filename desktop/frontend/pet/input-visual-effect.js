const FALLBACK_NOTICE = "输入栏视觉效果不可用，已回退为纯色。请右键桌宠打开“运行日志”查看原因。";

export function createInputPresentationQueue({ apply, isCurrent }) {
  if (typeof apply !== "function" || typeof isCurrent !== "function") {
    throw new Error("input presentation queue requires apply and revision callbacks");
  }
  let queue = Promise.resolve();

  return Object.freeze({
    schedule(presented, revision) {
      const operation = queue.then(async () => {
        if (!isCurrent(revision)) return false;
        await apply(Boolean(presented));
        return true;
      });
      queue = operation.catch(() => {});
      return operation;
    },
  });
}

export function inputVisualEffectFallbackNotice(values, status) {
  const requestedMode = values?.visualEffectMode || "solid";
  const effectiveMode = status?.effectiveMode || "solid";
  if (requestedMode === "solid" || effectiveMode !== "solid") return "";
  return FALLBACK_NOTICE;
}
