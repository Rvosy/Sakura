export function mount(context) {
  const { document, read, action, signal, isCurrent, onCatalogChanged } = context;
  const root = document.createElement("div"); root.hidden = true;
  const style = new document.defaultView.CSSStyleSheet();
  style.replaceSync(`
    .codex-account-card { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--hairline); border-radius: var(--radius-sm); }
    .codex-account-card h3 { margin: 0; font-size: 14px; }
    .codex-card-heading, .codex-quota-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .codex-quota-windows { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; }
    .codex-quota-window { display: grid; gap: 6px; font-size: 13px; }
    .codex-quota-heading strong { color: var(--sakura-primary); font-variant-numeric: tabular-nums; }
    .codex-quota-window progress { appearance: none; width: 100%; height: 6px; border: 0; border-radius: 99px; overflow: hidden; background: var(--tint-soft); }
    .codex-quota-window progress::-webkit-progress-bar { background: var(--tint-soft); }
    .codex-quota-window progress::-webkit-progress-value { background: var(--sakura-primary); border-radius: 99px; }
    .codex-quota-window progress::-moz-progress-bar { background: var(--sakura-primary); }
    .codex-test-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
    .codex-test-controls > select, .codex-test-controls > .custom-select { flex: 1 1 180px; min-width: 0; }
    .codex-operation-message, .codex-test-reply { margin: 0; max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
    .codex-test-reply { padding: 12px; background: var(--tint-faint); border-radius: var(--radius-sm); font-size: 13px; }
    .codex-copy-details { justify-self: start; }
    .codex-feedback { display: grid; gap: 8px; min-width: 0; }
  `);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, style];
  const panels = new Map();
  let snapshot = "";
  let disposed = false;
  const current = () => !disposed && !signal.aborted && isCurrent();
  const serviceId = profileId => `sakura.model.codex:${profileId}`;
  const catalogValue = profiles => JSON.stringify(profiles.map(({ profileId, label, createdAt, account, models }) =>
    ({ profileId, label, createdAt, account, models })));

  function apply(result) {
    if (!current() || !result.values?.profiles) return;
    const profiles = result.values.profiles;
    const catalogChanged = catalogValue(profiles) !== catalogValue([...panels.values()].map(panel => panel.value()));
    for (const [id, panel] of panels) {
      if (!profiles.some(profile => profile.profileId === id)) { panel.dispose(); panels.delete(id); }
    }
    for (const profile of profiles) {
      if (panels.has(profile.profileId)) panels.get(profile.profileId).update(profile);
      else panels.set(profile.profileId, createAccountPanel(context, profile, apply));
    }
    if (catalogChanged) onCatalogChanged();
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
    dispose() {
      disposed = true;
      for (const panel of panels.values()) panel.dispose();
      document.adoptedStyleSheets = document.adoptedStyleSheets.filter(sheet => sheet !== style);
      root.remove();
    },
  };
}

function createAccountPanel(context, initial, apply) {
  const { document, window, action, signal, isCurrent, onError, createIcon,
    enhanceSelect, refreshSelect, closeSelects } = context;
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
  const status = node("p", "detail-desc codex-operation-message"); status.setAttribute("role", "status");
  const reply = node("pre", "codex-test-reply");
  const feedback = node("div", "codex-feedback");
  const generalFeedback = node("div");
  const codeSection = node("div", "provider-login");
  const codeRow = node("div", "provider-login-code");
  const loginCode = node("code"); loginCode.setAttribute("aria-label", "登录代码");
  const actions = node("div", "detail-actions");
  const usageCard = node("section", "codex-account-card");
  usageCard.setAttribute("aria-label", "账号额度");
  const usageHeading = node("div", "codex-card-heading");
  const usageWindows = node("div", "codex-quota-windows");
  const usageUpdated = node("p", "detail-desc codex-usage-updated");
  const testCard = node("section", "codex-account-card");
  const testControls = node("div", "codex-test-controls");
  const testHint = node("p", "detail-desc", "发送一条简短请求，会使用账号额度，不更改对话设置。");
  const testModel = node("select"); testModel.setAttribute("aria-label", "测试使用的模型");
  let state = initial;
  let pending = null;
  let revision = 0;
  let busy = false;
  let disposed = false;
  let modelSnapshot = "";
  let usageSnapshot = "";
  const messages = { login: "正在申请登录代码…", sync_models: "正在获取全部可用模型…", usage: "正在获取账号额度…", test_connection: "正在测试模型…" };
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
  const refreshModels = button("刷新模型", () => pending?.operation === "sync_models" ? cancel() : start("sync_models"));
  const refreshUsage = button("刷新额度", () => pending?.operation === "usage" ? cancel() : start("usage"), "secondary-button compact-button");
  const testModelButton = button("测试模型", () => pending?.operation === "test_connection" ? cancel() : start("test_connection", { modelId: testModel.value }));
  const copyDetails = button("复制详情", async () => {
    const ticket = revision;
    await window.navigator.clipboard.writeText([status.textContent, reply.textContent].filter(Boolean).join("\n\n"));
    if (current() && ticket === revision) copyDetails.textContent = "已复制";
  }, "secondary-button compact-button codex-copy-details");
  const open = button("打开登录页面", () => invoke("openLogin"));
  const copy = button("复制代码", async () => {
    const code = loginCode.textContent;
    await window.navigator.clipboard.writeText(code);
    if (current() && loginCode.textContent === code) copy.textContent = "已复制";
  }, "secondary-button compact-button");
  copy.setAttribute("aria-label", "复制代码");
  testModel.addEventListener("change", () => {
    if (pending?.operation === "test_connection") {
      void cancel().catch(report);
    } else if (state.operationResult?.operation === "test_connection") {
      state = { ...state, operationResult: {} }; render();
    }
  });
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
  actions.append(login, refreshModels, logout, remove);
  usageHeading.append(node("h3", "", "账号额度"), refreshUsage);
  usageCard.append(usageHeading, usageWindows, usageUpdated);
  testControls.append(testModel, testModelButton);
  testCard.append(node("h3", "", "测试模型"), testControls, testHint);
  feedback.append(status, reply, copyDetails);
  generalFeedback.append(feedback);
  // 进度和登录代码出现在操作区下方，按钮在状态切换时保持原位。
  panel.append(heading, nameRow, accountLabel, hint, actions, generalFeedback, codeSection, usageCard, testCard);
  enhanceSelect(testModel);

  const dateText = timestamp => new Date(timestamp * 1000).toLocaleString("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
  function windowLabel(seconds, index) {
    if (!seconds) return `额度窗口 ${index + 1}`;
    if (seconds % 86400 === 0) return `${seconds / 86400} 天`;
    if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
    return `${Math.round(seconds / 60)} 分钟`;
  }

  function renderUsage() {
    const usage = state.usage;
    const next = JSON.stringify(usage);
    if (usageSnapshot === next) return;
    usageSnapshot = next;
    usageWindows.replaceChildren();
    if (!usage?.windows.length) {
      usageWindows.append(node("p", "detail-desc", usage ? "服务未返回额度窗口。" : "尚未获取额度。"));
    }
    for (const [index, item] of (usage?.windows || []).entries()) {
      const row = node("div", "codex-quota-window");
      const heading = node("div", "codex-quota-heading");
      const label = [item.label, windowLabel(item.windowSeconds, index)].filter(Boolean).join(" · ");
      const remaining = item.usedPercent == null ? null : Math.max(0, Math.min(100, 100 - item.usedPercent));
      heading.append(node("span", "", label), node("strong", "", remaining == null ? "未提供用量" : `剩余 ${Number(remaining.toFixed(1))}%`));
      row.append(heading);
      if (remaining != null) {
        const meter = node("progress"); meter.max = 100; meter.value = remaining;
        meter.setAttribute("aria-label", label + "剩余额度"); row.append(meter);
      }
      row.append(node("span", "detail-desc", item.resetsAt ? `${dateText(item.resetsAt)} 重置` : "未提供重置时间"));
      usageWindows.append(row);
    }
    usageUpdated.textContent = usage ? `最近更新：${dateText(usage.updatedAt)}` : "";
    usageUpdated.hidden = !usage;
  }

  function render() {
    const loggedIn = state.account.loggedIn;
    const result = state.operationResult || {};
    const blockingOperation = pending?.operation === "usage" ? null : pending?.operation;
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
    const timing = result.operation === "test_connection" && result.elapsedMs != null ? `耗时 ${(result.elapsedMs / 1000).toFixed(2)} 秒` : "";
    status.textContent = [result.modelId, result.message || (pending ? messages[pending.operation] : ""), timing].filter(Boolean).join(" · ");
    status.hidden = !status.textContent || Boolean(pending && result.userCode) || result.operation === "usage" && result.state === "completed";
    status.classList.toggle("error", result.state === "failed");
    reply.textContent = result.reply || "";
    reply.hidden = !reply.textContent;
    copyDetails.hidden = result.state !== "failed" && !(result.operation === "test_connection" && result.state === "completed");
    const operation = pending?.operation || result.operation;
    const target = operation === "usage" ? usageCard : operation === "test_connection" ? testCard : generalFeedback;
    if (feedback.parentElement !== target) target.insertBefore(feedback, target === usageCard ? usageWindows : target === testCard ? testHint : null);
    feedback.hidden = status.hidden && reply.hidden && copyDetails.hidden;
    generalFeedback.hidden = target !== generalFeedback || feedback.hidden;
    login.hidden = Boolean(loggedIn) && pending?.operation !== "login";
    login.disabled = busy;
    login.textContent = pending ? (pending.operation === "login" && !loggedIn ? "取消登录" : "取消") : "登录 ChatGPT";
    logout.hidden = !loggedIn;
    logout.disabled = busy || Boolean(blockingOperation);
    remove.disabled = busy;
    refreshModels.hidden = !loggedIn;
    refreshModels.textContent = pending?.operation === "sync_models" ? "取消刷新" : "刷新模型";
    refreshUsage.textContent = pending?.operation === "usage" ? "取消刷新" : "刷新额度";
    testModelButton.textContent = pending?.operation === "test_connection" ? "取消测试" : "测试模型";
    refreshModels.disabled = busy || Boolean(blockingOperation && blockingOperation !== "sync_models");
    refreshUsage.disabled = busy || Boolean(blockingOperation);
    testModelButton.disabled = busy || !state.models.length || Boolean(blockingOperation && blockingOperation !== "test_connection");
    usageCard.hidden = testCard.hidden = !loggedIn;
    const nextModels = JSON.stringify(state.models);
    if (modelSnapshot !== nextModels) {
      modelSnapshot = nextModels;
      const selected = testModel.value;
      testModel.replaceChildren();
      for (const model of state.models) {
        const option = node("option", "", model.label || model.modelId); option.value = model.modelId; testModel.append(option);
      }
      if (!state.models.length) testModel.append(node("option", "", "暂无可用模型"));
      testModel.value = state.models.some(model => model.modelId === selected) ? selected : state.models[0]?.modelId || "";
    }
    testModel.disabled = !state.models.length;
    refreshSelect(testModel);
    renderUsage();
  }

  async function mutate(id) {
    if (pending) void cancel().catch(report);
    const ticket = ++revision;
    busy = true; render();
    try {
      const result = await invoke(id);
      if (current() && ticket === revision) apply(result);
    } finally { busy = false; if (current()) render(); }
  }

  async function cancel() {
    closeSelects(panel);
    const previous = pending;
    pending = null; revision++;
    state = { ...state, operationResult: {} };
    if (current()) render();
    if (previous) await invoke("cancel", previous);
  }

  async function start(operation, values = {}) {
    if (busy || pending && pending.operation !== "usage") return;
    if (pending) void cancel().catch(report);
    const request = { ...values, operation, requestId: window.crypto.randomUUID() };
    pending = request;
    const ticket = ++revision;
    let opened = false;
    let terminal = false;
    let loggedIn = false;
    copyDetails.textContent = "复制详情";
    state = { ...state, operationResult: { operation, message: messages[operation] } };
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
          loggedIn = operation === "login" && value.state === "completed";
          if (terminal) break;
        }
        await new Promise(resolve => window.setTimeout(resolve, 250));
        if (!current() || ticket !== revision) return;
        result = await invoke("status", request);
      }
    } catch (error) {
      if (current() && ticket === revision) {
        state = { ...state, operationResult: { ...state.operationResult, operation, state: "failed", message: error.message } };
        if (operation === "login" || operation === "sync_models") report(error);
      }
    } finally {
      if (pending === request) {
        pending = null;
        if (current()) {
          render();
          if (operation === "test_connection" && terminal) feedback.scrollIntoView({ block: "nearest" });
        }
        if (!terminal) await invoke("cancel", request).catch(report);
        if (loggedIn && current() && ticket === revision) await start("usage");
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
      if (current() && ticket === revision) {
        apply(result);
        if (state.account.loggedIn && !state.usage) await start("usage");
      }
    },
    dispose() { disposed = true; void cancel().catch(report); panel.remove(); },
  };
}
