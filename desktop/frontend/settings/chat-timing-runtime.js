const FIELDS = Object.freeze({
  subtitleTypingIntervalMs: "subtitleTypingInterval",
  replySegmentPauseMs: "replySegmentPause",
});

const clone = (value) => JSON.parse(JSON.stringify(value));
const stable = (value) => JSON.stringify(value);

export function validateChatTimingValues(values, limits) {
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    throw new Error("聊天表现时间设置格式无效");
  }
  const normalized = {};
  for (const field of Object.keys(FIELDS)) {
    const limit = limits?.[field];
    const value = values[field];
    if (
      !Array.isArray(limit)
      || limit.length !== 3
      || !limit.every(Number.isSafeInteger)
      || !Number.isSafeInteger(value)
      || value < limit[0]
      || value > limit[1]
    ) throw new Error(`聊天表现时间字段超出允许范围：${field}`);
    normalized[field] = value;
  }
  return Object.freeze(normalized);
}

export function validateChatTimingSnapshot(snapshot) {
  if (
    snapshot?.schemaVersion !== 1
    || !Number.isSafeInteger(snapshot.windowGeneration)
    || snapshot.windowGeneration < 1
  ) throw new Error("不支持的聊天表现时间设置响应");
  return Object.freeze({
    ...snapshot,
    values: validateChatTimingValues(snapshot.values, snapshot.limits),
  });
}

export function createChatTimingController({ document, invoke, onDirty }) {
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let disposed = false;

  function fill(values) {
    for (const [field, id] of Object.entries(FIELDS)) {
      const input = document.getElementById(id);
      const limit = snapshot.limits[field];
      input.min = String(limit[0]);
      input.max = String(limit[1]);
      input.value = String(values[field]);
    }
  }

  function read() {
    return validateChatTimingValues(
      Object.fromEntries(Object.entries(FIELDS).map(([field, id]) => [
        field,
        Number.parseInt(document.getElementById(id).value, 10),
      ])),
      snapshot.limits,
    );
  }

  function changed() {
    if (disposed) return;
    try {
      draft = read();
    } finally {
      onDirty();
    }
  }

  return Object.freeze({
    initialize(input) {
      snapshot = validateChatTimingSnapshot(input);
      baseline = clone(snapshot.values);
      draft = clone(baseline);
      fill(draft);
      for (const id of Object.values(FIELDS)) {
        document.getElementById(id).addEventListener("input", changed);
      }
      onDirty();
    },
    isDirty: () => Boolean(baseline && stable(draft) !== stable(baseline)),
    async save() {
      if (!snapshot) throw new Error("聊天表现时间设置尚未加载");
      draft = read();
      const result = await invoke("settings_chat_presentation_timing_save", {
        windowGeneration: snapshot.windowGeneration,
        values: clone(draft),
      });
      baseline = clone(validateChatTimingValues(result, snapshot.limits));
      draft = clone(baseline);
      fill(draft);
      onDirty();
      return result;
    },
    discard() {
      if (!baseline) return;
      draft = clone(baseline);
      fill(draft);
      onDirty();
    },
    dispose() {
      disposed = true;
    },
  });
}
