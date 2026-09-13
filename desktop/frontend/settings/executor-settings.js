const STATES = new Set(["initializing", "pending", "ready", "unavailable"]);

export function validateExecutorSnapshot(value) {
  if (
    value?.schema_version !== 1
    || typeof value.selected_service_key !== "string"
    || !(value.applied_service_key === null || typeof value.applied_service_key === "string")
    || !STATES.has(value.state)
    || typeof value.reason_code !== "string"
    || !Array.isArray(value.candidates)
    || !value.candidates.some((item) => item?.serviceKey === "")
  ) throw new Error("互动方式设置响应无效");
  const seen = new Set();
  for (const item of value.candidates) {
    if (!item || typeof item.serviceKey !== "string" || typeof item.displayName !== "string"
      || !item.displayName.trim() || typeof item.pluginId !== "string" || seen.has(item.serviceKey)) {
      throw new Error("互动方式列表无效");
    }
    seen.add(item.serviceKey);
  }
  return value;
}

export function createExecutorSettingsController({
  document, invoke, onDirty = () => {}, onError = () => {},
  enhanceSelect = () => {}, refreshSelect = () => {},
  setTimer = (callback, delay) => setTimeout(callback, delay),
  clearTimer = (timer) => clearTimeout(timer),
}) {
  const select = document.getElementById("chatExecutor");
  const status = document.getElementById("chatExecutorStatus");
  const retry = document.getElementById("chatExecutorRetry");
  let snapshot = null;
  let draft = "";
  let disposed = false;
  let revision = 0;
  let timer = null;
  let saving = false;

  function scheduleRefresh() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    if (!disposed && ["pending", "initializing"].includes(snapshot?.state)) {
      timer = setTimer(() => {
        timer = null;
        void refreshCurrent().catch((error) => onError(String(error)));
      }, 1000);
    }
  }

  function render() {
    const candidates = [...snapshot.candidates];
    if (!candidates.some((item) => item.serviceKey === draft)) {
      candidates.push({ serviceKey: draft, displayName: "已选插件不可用", unavailable: true });
    }
    select.replaceChildren(...candidates.map((item) => {
      const option = document.createElement("option");
      option.value = item.serviceKey;
      option.textContent = item.displayName;
      option.disabled = item.unavailable === true;
      return option;
    }));
    select.value = draft;
    refreshSelect(select);
    const applied = snapshot.candidates.find((item) => item.serviceKey === snapshot.applied_service_key);
    const problem = {
      PROVIDER_SETUP_REQUIRED: "请配置默认 Assistant 使用的模型。",
      EXECUTOR_UNAVAILABLE: "所选插件不可用，请检查是否已启用。",
      EXECUTOR_NOT_READY: "所选插件尚未准备好。",
      EXECUTOR_BINDING_EXPIRED: "所选插件已停止或重载。",
      CHARACTER_REQUIRED: "请先选择角色。",
    }[snapshot.reason_code] || "所选互动方式暂不可用，请查看运行日志。";
    status.textContent = draft !== snapshot.selected_service_key ? "保存后切换。"
      : snapshot.state === "pending" ? `本轮结束后切换${applied ? `，当前使用${applied.displayName}` : ""}。`
      : snapshot.state === "initializing" ? "正在准备所选互动方式…"
      : snapshot.state === "unavailable" ? problem
      : "已生效";
    status.hidden = false;
    retry.hidden = snapshot.state !== "unavailable" || draft !== snapshot.selected_service_key;
    retry.disabled = saving || !snapshot.candidates.some((item) => item.serviceKey === draft);
    document.getElementById("executorSettings").hidden = false;
  }

  async function refreshCurrent({ preserveDraft = true, expectedGenerationId = null } = {}) {
    const token = ++revision;
    const next = validateExecutorSnapshot(await invoke("settings_executor_get"));
    if (disposed || token !== revision) return;
    if (!Number.isSafeInteger(next.window_generation) || next.window_generation < 1
      || typeof next.core_generation_id !== "string" || !next.core_generation_id
      || (expectedGenerationId && expectedGenerationId !== next.core_generation_id)) {
      throw new Error("互动方式设置身份无效");
    }
    const keepDraft = preserveDraft && snapshot && draft !== snapshot.selected_service_key;
    snapshot = next;
    if (!keepDraft) draft = next.selected_service_key;
    render();
    scheduleRefresh();
    onDirty();
  }

  function changed() {
    draft = select.value;
    render();
    onDirty();
  }

  async function save() {
    if (!snapshot) throw new Error("互动方式设置尚未加载");
    if (saving) throw new Error("互动方式正在保存");
    saving = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
    const token = ++revision;
    try {
      const saved = validateExecutorSnapshot(await invoke("settings_executor_save", {
        windowGeneration: snapshot.window_generation,
        coreGenerationId: snapshot.core_generation_id,
        serviceKey: draft,
      }));
      if (disposed || token !== revision) return saved;
      snapshot = { ...snapshot, ...saved };
      draft = saved.selected_service_key;
      onDirty();
      return saved;
    } finally {
      saving = false;
      if (!disposed) {
        render();
        scheduleRefresh();
      }
    }
  }

  function retrySelected() {
    if (disposed || saving) return;
    const pending = save().catch((error) => onError(String(error)));
    retry.disabled = true;
    return pending;
  }

  return Object.freeze({
    async initialize() {
      select.addEventListener("change", changed);
      retry.addEventListener("click", retrySelected);
      enhanceSelect(select);
      await refreshCurrent();
    },
    refreshCurrent,
    isDirty: () => Boolean(snapshot && draft !== snapshot.selected_service_key),
    save,
    discard() {
      if (!snapshot) return;
      draft = snapshot.selected_service_key;
      render();
      onDirty();
    },
    async rebindIdentity(generationId) { await refreshCurrent({ expectedGenerationId: generationId }); },
    dispose() {
      disposed = true;
      revision += 1;
      if (timer !== null) clearTimer(timer);
      select.removeEventListener("change", changed);
      retry.removeEventListener("click", retrySelected);
    },
  });
}
