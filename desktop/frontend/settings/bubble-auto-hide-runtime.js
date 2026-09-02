const clone = (value) => JSON.parse(JSON.stringify(value));
const stable = (value) => JSON.stringify(value);

export function validateBubbleAutoHideValues(values, limits) {
  const limit = limits?.autoHideDelaySeconds;
  if (
    !values
    || typeof values !== "object"
    || Array.isArray(values)
    || typeof values.autoHideEnabled !== "boolean"
    || !Array.isArray(limit)
    || limit.length !== 3
    || !limit.every(Number.isSafeInteger)
    || !Number.isSafeInteger(values.autoHideDelaySeconds)
    || values.autoHideDelaySeconds < limit[0]
    || values.autoHideDelaySeconds > limit[1]
  ) throw new Error("气泡自动隐藏设置格式无效");
  return Object.freeze({
    autoHideEnabled: values.autoHideEnabled,
    autoHideDelaySeconds: values.autoHideDelaySeconds,
  });
}

export function validateBubbleAutoHideSnapshot(snapshot) {
  if (
    snapshot?.schemaVersion !== 1
    || !Number.isSafeInteger(snapshot.windowGeneration)
    || snapshot.windowGeneration < 1
  ) throw new Error("不支持的气泡自动隐藏设置响应");
  return Object.freeze({
    ...snapshot,
    values: validateBubbleAutoHideValues(snapshot.values, snapshot.limits),
  });
}

export function createBubbleAutoHideSettingsController({ document, invoke, onDirty }) {
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let disposed = false;
  const enabled = document.getElementById("bubbleAutoHide");
  const delay = document.getElementById("bubbleAutoHideDelay");

  function fill(values) {
    const limit = snapshot.limits.autoHideDelaySeconds;
    enabled.checked = values.autoHideEnabled;
    delay.min = String(limit[0]);
    delay.max = String(limit[1]);
    delay.value = String(values.autoHideDelaySeconds);
    delay.disabled = !values.autoHideEnabled;
  }

  function read() {
    return validateBubbleAutoHideValues({
      autoHideEnabled: enabled.checked,
      autoHideDelaySeconds: Number.parseInt(delay.value, 10),
    }, snapshot.limits);
  }

  function changed() {
    if (disposed) return;
    try {
      draft = read();
      delay.disabled = !draft.autoHideEnabled;
    } finally {
      onDirty();
    }
  }

  return Object.freeze({
    initialize(input) {
      snapshot = validateBubbleAutoHideSnapshot(input);
      baseline = clone(snapshot.values);
      draft = clone(baseline);
      fill(draft);
      enabled.addEventListener("change", changed);
      delay.addEventListener("input", changed);
      onDirty();
    },
    isDirty: () => Boolean(baseline && stable(draft) !== stable(baseline)),
    async save() {
      if (!snapshot) throw new Error("气泡自动隐藏设置尚未加载");
      draft = read();
      const result = await invoke("settings_bubble_auto_hide_save", {
        windowGeneration: snapshot.windowGeneration,
        values: clone(draft),
      });
      baseline = clone(validateBubbleAutoHideValues(result, snapshot.limits));
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
