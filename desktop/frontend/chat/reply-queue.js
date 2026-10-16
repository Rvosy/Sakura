// 模型请求立即进行，排队的回复等当前字幕和语音结束后再呈现。
export function createReplyQueue({ isPresenting, publish, onDiscard = () => {} }) {
  let pending = [];

  function clear() {
    for (const event of pending) if (event.type === "chat.completed") onDiscard(event);
    pending = [];
  }

  function flush() {
    while (pending.length && !isPresenting()) publish(pending.shift());
  }

  return Object.freeze({
    receive(event) {
      if (event.type === "lifecycle") {
        if (!["ready", "degraded"].includes(event.status)
            || pending.some(item => item.generationId !== event.generationId)) clear();
        publish(event);
      } else if (event.presentation === "queued") {
        pending.push(event);
        flush();
      } else {
        if (event.type === "chat.started") clear();
        publish(event);
      }
    },
    flush,
    clear,
  });
}
