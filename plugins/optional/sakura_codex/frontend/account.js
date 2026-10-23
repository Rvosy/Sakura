export function mount(context) {
  const { document, read, action, signal, isCurrent, onCatalogChanged } = context;
  const root = document.createElement("div"); root.hidden = true;
  const panels = new Map();
  let snapshot = "";
  let disposed = false;
  const current = () => !disposed && !signal.aborted && isCurrent();
  const serviceId = profileId => `sakura.model.codex:${profileId}`;

  function apply(result) {
    if (!current() || !result.values?.profiles) return;
    const profiles = result.values.profiles;
    for (const [id, panel] of panels) {
      if (!profiles.some(profile => profile.profileId === id)) { panel.dispose(); panels.delete(id); }
    }
    for (const profile of profiles) {
      if (panels.has(profile.profileId)) panels.get(profile.profileId).update(profile);
      else panels.set(profile.profileId, createAccountPanel(context, profile, apply));
    }
    onCatalogChanged();
  }

  function update() {
    const profiles = read("profiles") || [];
    const next = JSON.stringify(profiles);
    if (snapshot !== next) { snapshot = next; apply({ values: { profiles } }); }
  }
  update();
  return {
    element: root, update,
    cancel: () => Promise.all([...panels.values()].map(panel => panel.cancel())),
    contributions: () => ({
      modelServicePresets: [{ id: "sakura.model.codex", label: "OpenAI Official", iconName: "openai",
        async add({ createdAt }) {
          const result = await action("add", { operationRequest: { createdAt } });
          if (!current()) return;
          apply(result);
          return serviceId(result.values.profiles.at(-1).profileId);
        },
      }],
      modelServices: [...panels.values()].map(panel => {
        const profile = panel.value();
        return { id: serviceId(profile.profileId), label: profile.label, iconName: "openai", createdAt: profile.createdAt,
          description: profile.account.loggedIn ? profile.account.email || "已登录 ChatGPT" : "登录 ChatGPT 账号",
          configured: Boolean(profile.account.loggedIn && profile.models.length), models: profile.models.map(model => model.modelId),
          element: panel.element, cancel: panel.cancel, select: panel.select };
      }),
      modelCatalog: [{ serviceKey: "sakura.model.codex", label: "OpenAI Official",
        profiles: [...panels.values()].map(panel => {
          const { profileId, label, models } = panel.value();
          return { profileId, label, models };
        }) }],
    }),
    dispose() { disposed = true; for (const panel of panels.values()) panel.dispose(); root.remove(); },
  };
}

function createAccountPanel(context, initial, apply) {
  const { document, window, action, signal, isCurrent, onError, onCatalogChanged, createIcon } = context;
  const node = (tag, className = "", text = "") => {
    const element = document.createElement(tag);
    element.className = className; element.textContent = text; return element;
  };
  const panel = node("div", "provider-account");
  const heading = node("h2", "provider-account-title");
  const title = node("span"); heading.append(createIcon(document, "openai"), title);
  const nameRow = node("div", "form-row");
  const label = node("label", "", "名称");
  const name = node("input", "wide-input"); name.type = "text";
  name.id = `codex-name-${initial.profileId}`; label.htmlFor = name.id;
  name.value = initial.label;
  nameRow.append(label, name);
  const accountLabel = node("p", "provider-account-identity");
  const hint = node("p", "detail-desc");
  const status = node("p", "detail-desc"); status.setAttribute("role", "status");
  const codeSection = node("div", "provider-login");
  const codeRow = node("div", "provider-login-code");
  const loginCode = node("code"); loginCode.setAttribute("aria-label", "登录代码");
  const actions = node("div", "detail-actions");
  let state = initial;
  let pending = null;
  let revision = 0;
  let busy = false;
  let disposed = false;
  const current = () => !disposed && !signal.aborted && isCurrent();
  const report = error => { if (current()) onError(error); };
  const invoke = (id, values = {}) => action(id, { operationRequest: { profileId: state.profileId, ...values } });
  const button = (label, callback, className = "secondary-button") => {
    const element = node("button", className, label); element.type = "button";
    element.addEventListener("click", () => Promise.resolve().then(callback).catch(report));
    return element;
  };
  const login = button("登录 ChatGPT", () => pending ? cancel() : start("login"), "primary-button");
  const logout = button("退出登录", () => mutate("logout"));
  const remove = button("删除模型服务", async () => { await cancel(); await mutate("remove"); }, "danger-button");
  const retry = button("重试获取模型", () => start("sync_models"));
  const open = button("打开登录页面", () => invoke("openLogin"));
  const copy = button("复制代码", async () => {
    const code = loginCode.textContent;
    await window.navigator.clipboard.writeText(code);
    if (current() && loginCode.textContent === code) copy.textContent = "已复制";
  }, "secondary-button compact-button");
  copy.setAttribute("aria-label", "复制代码");
  name.addEventListener("change", async () => {
    const value = name.value;
    const ticket = revision;
    try {
      const result = await invoke("rename", { label: value });
      if (current() && ticket === revision && name.value === value) apply(result);
    } catch (error) { report(error); }
  });
  codeRow.append(loginCode, copy);
  codeSection.append(node("p", "detail-desc", "复制代码，在浏览器中完成登录。"), codeRow, open);
  actions.append(login, logout, retry, remove);
  // 进度和登录代码出现在操作区下方，按钮在状态切换时保持原位。
  panel.append(heading, nameRow, accountLabel, hint, actions, status, codeSection);

  function render() {
    const loggedIn = state.account.loggedIn;
    const result = state.operationResult || {};
    panel.setAttribute("aria-busy", String(Boolean(pending) || busy));
    title.textContent = state.label;
    accountLabel.hidden = !loggedIn;
    accountLabel.textContent = [state.account.email || "已登录 ChatGPT", state.account.plan].filter(Boolean).join(" · ");
    hint.textContent = loggedIn
      ? (state.models.length ? "已自动添加 " + state.models.length + " 个模型。到“模型”页选择对话模型即可使用。" : "登录已完成，模型同步后即可在“模型”页选用。")
      : "登录后自动添加全部可用模型，再到“模型”页选择对话模型。";
    codeSection.hidden = !pending || !result.userCode;
    const code = result.userCode || "";
    if (loginCode.textContent !== code) copy.textContent = "复制代码";
    loginCode.textContent = code;
    status.textContent = result.message || "";
    status.hidden = !status.textContent || Boolean(pending && result.userCode) || result.state === "completed" && state.models.length > 0;
    login.hidden = Boolean(loggedIn) && !pending;
    login.disabled = busy;
    login.textContent = pending ? (pending.operation === "login" && !loggedIn ? "取消登录" : "取消") : "登录 ChatGPT";
    logout.hidden = !loggedIn || Boolean(pending);
    logout.disabled = busy;
    remove.disabled = busy;
    retry.hidden = !loggedIn || Boolean(pending) || (state.models.length > 0 && result.state !== "failed");
    retry.disabled = busy;
  }

  async function mutate(id) {
    const ticket = ++revision;
    busy = true; render();
    try {
      const result = await invoke(id);
      if (current() && ticket === revision) apply(result);
    } finally { busy = false; if (current()) render(); }
  }

  async function cancel() {
    const previous = pending;
    pending = null; revision++;
    state = { ...state, operationResult: {} };
    if (current()) render();
    if (previous) await invoke("cancel", previous);
  }

  async function start(operation) {
    if (pending || busy) return;
    const request = { operation, requestId: window.crypto.randomUUID() };
    pending = request;
    const ticket = ++revision;
    let opened = false;
    let terminal = false;
    state = { ...state, operationResult: { message: operation === "login" ? "正在申请登录代码…" : "正在获取全部可用模型…" } };
    render();
    try {
      let result = await invoke("start", request);
      while (current() && ticket === revision) {
        apply(result);
        const value = state.operationResult;
        if (value?.requestId === request.requestId) {
          if (value.userCode && !opened) {
            opened = true;
            void invoke("openLogin").catch(error => { if (ticket === revision) report(error); });
          }
          terminal = ["failed", "completed", "cancelled"].includes(value.state);
          if (value.state === "failed") throw new Error(value.message, { cause: value });
          if (terminal) break;
        }
        await new Promise(resolve => window.setTimeout(resolve, 250));
        if (!current() || ticket !== revision) return;
        result = await invoke("status", request);
      }
    } catch (error) {
      if (current() && ticket === revision) {
        state = { ...state, operationResult: { state: "failed", message: error.message } };
        report(error);
      }
    } finally {
      if (pending === request) {
        pending = null;
        if (current()) { render(); onCatalogChanged(); }
        if (!terminal) await invoke("cancel", request).catch(report);
      }
    }
  }

  render();
  return {
    element: panel, cancel, value: () => state,
    update(value) {
      if (name.value === state.label) name.value = value.label;
      state = value; render();
    },
    async select() {
      const ticket = ++revision;
      const result = await invoke("status");
      if (current() && ticket === revision) apply(result);
    },
    dispose() { disposed = true; void cancel().catch(report); panel.remove(); },
  };
}
