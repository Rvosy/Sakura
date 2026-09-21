import { createIcon } from "../core/icons.js";

export function createConnectionEditor({ document, window, read, write, probe, cancel,
  onError: setError, notify, onCatalogChanged = () => {} }) {
  let disposed = false;
  let apiView = { profiles: [] };
  let lastValue = "";
  const overlays = new Set();
  const modelDiscoveries = new Map();
  const root = document.createElement("div"); root.className = "admin-page connection-editor";
  const node = (tag, className) => { const n = document.createElement(tag); n.className = className; return n; };
  const fields = {
    providerStatusStrip: node("div", "admin-strip"), providerSearch: node("input", "admin-search"),
    addProviderButton: node("button", "secondary-button"), providerList: node("div", "admin-list"),
    providerDetail: node("aside", "admin-detail"),
  };
  fields.providerSearch.type = "text"; fields.providerSearch.placeholder = "搜索模型服务、API 地址";
  fields.addProviderButton.type = "button"; fields.addProviderButton.textContent = "添加模型服务";
  const toolbar = node("div", "admin-toolbar"); toolbar.append(fields.providerSearch, fields.addProviderButton);
  const workbench = node("div", "admin-workbench"); workbench.append(fields.providerList, fields.providerDetail);
  root.append(fields.providerStatusStrip, toolbar, workbench);
  const controller = { listModels: (v) => probe("list_models", v), testConnection: (v) => probe("test_connection", v) };
  const markInvalid = (input, invalid) => { if (input) input.classList.toggle("is-invalid", invalid); };
  const refreshModelSlots = () => {};
  function refreshDirty() {
    const value = structuredClone(providerState.profiles);
    lastValue = JSON.stringify(value); write(value); onCatalogChanged();
  }
  function runtimeProbeProfile(profile, model) {
    return { profileId: profile.id, base_url: profile.base_url.trim(), modelId: model,
      timeout_seconds: profile.timeout_seconds || 60,
      credential: { action: profile.credential_action || "keep", value: profile.credential_action === "replace" ? profile.api_key.trim() : "" } };
  }
  function renderStrip(container, items) {
    container.textContent = "";
    for (const item of items) { const n = node("span", "status-chip"); n.textContent = `${item.label} ${item.value}`; container.append(n); }
  }
  function makeProfileId() {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID();
    }
    return `profile-${Date.now()}`;
  }

  // 模型服务页改为状态驱动的主从结构：providerState.profiles 是唯一数据源，
  // 「模型服务」页与「模型」页的槽位都从它派生。
  const providerState = { profiles: [], selectedId: "", search: "" };
  const PROVIDER_FIELD_PLACEHOLDERS = {
    base_url: "通常以 /v1 结尾",
    api_key: "填写 API Key",
  };

  // 内置预设：选中即预填 API 地址 与图标，其余走「自定义」。
  const PROVIDER_PRESETS = [
    {
      key: "deepseek",
      label: "DeepSeek",
      base_url: "https://api.deepseek.com/v1",
      host: "api.deepseek.com",
      iconUrl: "./assets/providers/deepseek.svg",
    },
    {
      key: "google",
      label: "Google 官方",
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
      host: "generativelanguage.googleapis.com",
      iconUrl: "./assets/providers/google.svg",
    },
  ];

  function initializeProviderState() {
    invalidateModelDiscoveries();
    providerState.profiles = (apiView.profiles || []).map((profile) => ({
      id: profile.id || makeProfileId(),
      alias: profile.alias || profile.id || "模型服务",
      base_url: profile.base_url || "",
      api_key: profile.api_key || "",
      configured: Boolean(profile.configured),
      credential_action: profile.credential_action || (profile.configured ? "keep" : "keep"),
      timeout_seconds: profile.timeout_seconds,
      models: Array.isArray(profile.models) ? profile.models.map(String) : [],
    }));
    providerState.selectedId = providerState.profiles[0]?.id || "";
  }

  function providerHost(url) {
    const text = String(url || "").trim();
    if (!text) {
      return "";
    }
    try {
      return new URL(text).host;
    } catch {
      return text.replace(/^https?:\/\//, "").split("/")[0];
    }
  }

  function presetForProfile(profile) {
    const host = providerHost(profile.base_url);
    const alias = String(profile.alias || "").toLowerCase();
    return (
      PROVIDER_PRESETS.find((preset) => preset.host === host || preset.label.toLowerCase() === alias)
      || null
    );
  }

  function filteredProviders() {
    const query = providerState.search.trim().toLowerCase();
    if (!query) {
      return providerState.profiles;
    }
    return providerState.profiles.filter((profile) =>
      [profile.alias, profile.base_url, ...(profile.models || [])]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }

  function renderProviderPage() {
    if (disposed) return;
    renderProviderStatus();
    renderProviderList();
    renderProviderDetail();
  }

  function renderProviderStatus() {
    const items = providerState.profiles;
    const configured = items.filter(
      (profile) => (profile.base_url || "").trim()
        && ((profile.api_key || "").trim() || (profile.configured && profile.credential_action !== "clear")),
    ).length;
    const totalModels = items.reduce((sum, profile) => sum + (profile.models || []).length, 0);
    renderStrip(fields.providerStatusStrip, [
      { label: "模型服务", value: items.length },
      { label: "已配置", value: configured },
      { label: "模型", value: totalModels },
    ]);
  }

  // 填充头像：优先用图标资源（如 DeepSeek SVG），其次 emoji，最后名称首字母。
  function applyAvatar(avatar, { iconUrl, icon, initial } = {}) {
    avatar.textContent = "";
    avatar.classList.remove("is-initial");
    if (iconUrl) {
      const img = document.createElement("img");
      img.className = "provider-avatar-img";
      img.src = iconUrl;
      img.alt = "";
      avatar.append(img);
    } else if (icon) {
      avatar.textContent = icon;
    } else {
      avatar.classList.add("is-initial");
      avatar.textContent = (initial || "?").trim().charAt(0).toUpperCase() || "?";
    }
  }

  function providerAvatar(profile) {
    const avatar = document.createElement("span");
    avatar.className = "provider-avatar";
    const preset = presetForProfile(profile);
    applyAvatar(avatar, {
      iconUrl: preset?.iconUrl,
      icon: preset?.icon,
      initial: profile.alias || "?",
    });
    return avatar;
  }

  function renderProviderList() {
    fields.providerList.textContent = "";
    const profiles = filteredProviders();
    if (!profiles.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      if (providerState.profiles.length) {
        empty.textContent = "没有匹配的模型服务。";
      } else {
        const text = document.createElement("p");
        text.className = "empty-state-text";
        text.textContent = "尚未添加模型服务";
        const cta = document.createElement("button");
        cta.type = "button";
        cta.className = "primary-button";
        cta.textContent = "添加模型服务";
        cta.addEventListener("click", openAddProviderChooser);
        empty.append(text, cta);
      }
      fields.providerList.append(empty);
      return;
    }
    profiles.forEach((profile) => {
      const card = document.createElement("div");
      card.className = "provider-card";
      card.classList.toggle("is-selected", profile.id === providerState.selectedId);
      card.addEventListener("click", () => {
        providerState.selectedId = profile.id;
        renderProviderPage();
      });
      const body = document.createElement("div");
      body.className = "provider-card-body";
      const title = document.createElement("strong");
      title.textContent = profile.alias || profile.id;
      const meta = document.createElement("span");
      meta.className = "card-meta";
      meta.textContent = providerHost(profile.base_url) || "未设置 API 地址";
      body.append(title, meta);
      const count = document.createElement("span");
      count.className = "provider-count";
      count.textContent = `${(profile.models || []).length} 个模型`;
      card.append(providerAvatar(profile), body, count);
      fields.providerList.append(card);
    });
  }

  function renderProviderDetail() {
    const detail = fields.providerDetail;
    detail.textContent = "";
    const profile = providerState.profiles.find((item) => item.id === providerState.selectedId);
    if (!profile) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "请选择模型服务";
      detail.append(empty);
      return;
    }
    const title = document.createElement("h2");
    title.textContent = profile.alias || profile.id;
    detail.append(
      title,
      providerField(profile, "alias", "名称", "text"),
      providerField(profile, "base_url", "API 地址", "text"),
      providerField(profile, "api_key", "API Key", "password"),
      renderProviderModels(profile),
    );
    const actions = document.createElement("div");
    actions.className = "detail-actions";
    const testButton = document.createElement("button");
    testButton.type = "button";
    testButton.className = "secondary-button";
    testButton.textContent = "测试连接";
    testButton.addEventListener("click", () => testProvider(profile, testButton));
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "danger-button";
    removeButton.textContent = "删除模型服务";
    removeButton.addEventListener("click", () => removeProvider(profile));
    if (profile.configured) {
      const clearButton = document.createElement("button");
      clearButton.type = "button";
      clearButton.className = "secondary-button";
      clearButton.textContent = profile.credential_action === "clear" ? "保存后清除" : "清除凭据";
      clearButton.addEventListener("click", () => {
        invalidateModelDiscovery(profile); void cancel();
        profile.api_key = "";
        profile.credential_action = "clear";
        profile.configured = false;
        renderProviderPage();
        refreshDirty();
      });
      actions.append(testButton, clearButton, removeButton);
    } else {
      actions.append(testButton, removeButton);
    }
    detail.append(actions);
  }

  function providerField(profile, key, label, type) {
    const row = document.createElement("div");
    row.className = "form-row";
    const labelEl = document.createElement("label");
    labelEl.textContent = label;
    const input = document.createElement("input");
    input.type = type === "password" ? "password" : "text";
    input.className = "wide-input";
    input.dataset.providerField = key;
    input.value = profile[key] || "";
    input.placeholder = PROVIDER_FIELD_PLACEHOLDERS[key] || "";
    if (key === "api_key" && profile.configured) {
      input.placeholder = "留空保留原密钥";
    }
    input.addEventListener("input", () => {
      if (["base_url", "api_key"].includes(key)) { invalidateModelDiscovery(profile); void cancel(); }
      profile[key] = input.value;
      if (key === "api_key") {
        profile.credential_action = input.value.trim() ? "replace" : (profile.configured ? "keep" : "clear");
      }
      if (input.value.trim()) {
        markInvalid(input, false);
      }
      if (key === "alias" || key === "base_url") {
        // 仅刷新左侧卡片与标题，避免重渲详情导致输入框失焦。
        renderProviderStatus();
        renderProviderList();
        if (key === "alias") {
          const heading = fields.providerDetail.querySelector("h2");
          if (heading) {
            heading.textContent = input.value.trim() || profile.id;
          }
        }
      } else if (key === "api_key") {
        renderProviderStatus();
      }
      refreshDirty();
    });
    row.append(labelEl, input);
    return row;
  }

  function renderProviderModels(profile) {
    const section = document.createElement("div");
    section.className = "provider-models";
    const head = document.createElement("div");
    head.className = "provider-models-head";
    const heading = document.createElement("h3");
    heading.textContent = "模型";
    const detectButton = document.createElement("button");
    detectButton.type = "button";
    detectButton.className = "secondary-button compact-button";
    detectButton.textContent = "获取模型列表";
    detectButton.addEventListener("click", () => autoDetectModels(profile, detectButton));
    head.append(heading, detectButton);
    section.append(head);

    const list = document.createElement("div");
    list.className = "model-chip-list";
    if (!(profile.models || []).length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "尚未添加模型";
      list.append(empty);
    } else {
      profile.models.forEach((model) => {
        const chip = document.createElement("span");
        chip.className = "model-chip";
        const name = document.createElement("span");
        name.textContent = model;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "model-chip-remove";
        remove.setAttribute("aria-label", `移除 ${model}`);
        remove.append(createIcon(document, "x"));
        remove.addEventListener("click", () => {
          profile.models = profile.models.filter((item) => item !== model);
          renderProviderPage();
          refreshModelSlots();
          refreshDirty();
        });
        chip.append(name, remove);
        list.append(chip);
      });
    }
    section.append(list);

    const addRow = document.createElement("div");
    addRow.className = "model-add-row";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "wide-input";
    input.placeholder = "手动添加模型 ID";
    const addButton = document.createElement("button");
    addButton.type = "button";
    addButton.className = "secondary-button compact-button";
    addButton.textContent = "添加";
    const commit = () => {
      const value = input.value.trim();
      if (!value) {
        return;
      }
      const added = addModelsToProfile(profile, [value]);
      input.value = "";
      setError(added ? "" : "该模型已存在。");
    };
    addButton.addEventListener("click", commit);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      }
    });
    addRow.append(input, addButton);
    section.append(addRow);
    return section;
  }

  function addModelsToProfile(profile, models) {
    if (!Array.isArray(profile.models)) {
      profile.models = [];
    }
    const existing = new Set(profile.models);
    let added = 0;
    models.forEach((model) => {
      const name = String(model || "").trim();
      if (name && !existing.has(name)) {
        existing.add(name);
        profile.models.push(name);
        added += 1;
      }
    });
    if (added) {
      renderProviderPage();
      refreshModelSlots();
      refreshDirty();
    }
    return added;
  }

  function providerDetailInput(key) {
    return fields.providerDetail.querySelector(`[data-provider-field="${key}"]`);
  }

  function clearProbeError() {
    fields.providerDetail.querySelector(".provider-probe-error")?.remove();
    setError("");
  }

  function reportProbeError(profile, error, fallback) {
    if (disposed || providerState.selectedId !== profile.id
        || !providerState.profiles.includes(profile)) return;
    clearProbeError();
    const diagnostic = String(error);
    const code = diagnostic.match(/(?:^|Error: )([A-Z][A-Z0-9_]+)(?:\||:|$)/)?.[1];
    const httpStatus = diagnostic.match(/API HTTP (\d{3}):/)?.[1];
    setError(httpStatus === "403" ? "服务拒绝访问。" : {
      AUTHENTICATION_FAILED: "验证失败，请检查 API Key。",
      MODEL_AUTHENTICATION_FAILED: "验证失败，请检查 API Key。",
      PROVIDER_ACCESS_FORBIDDEN: "服务拒绝访问。",
      PROVIDER_TIMEOUT: "请求超时。",
      MODEL_READ_TIMEOUT: "请求超时。",
      MODEL_CONNECTION_TIMEOUT: "请求超时。",
      MODEL_REQUEST_TIMEOUT: "请求超时。",
      MODEL_CONNECTION_FAILED: "无法连接模型服务。",
      MODEL_RATE_LIMITED: "请求过于频繁，请稍后重试。",
    }[code] || fallback);
    const details = document.createElement("details");
    details.className = "provider-probe-error";
    const summary = document.createElement("summary");
    summary.textContent = "错误详情";
    const content = document.createElement("p");
    content.textContent = diagnostic;
    details.append(summary, content);
    fields.providerDetail.append(details);
  }

  async function autoDetectModels(profile, button) {
    if (disposed || !providerState.profiles.includes(profile)) return;
    clearProbeError();
    const baseUrl = (profile.base_url || "").trim();
    const apiKey = (profile.api_key || "").trim();
    if (!baseUrl) {
      markInvalid(providerDetailInput("base_url"), true);
      setError("请先填写 API 地址。");
      return;
    }
    if (!apiKey && !(profile.configured && profile.credential_action === "keep")) {
      markInvalid(providerDetailInput("api_key"), true);
      setError("请先填写 API Key。");
      return;
    }
    invalidateModelDiscovery(profile);
    const original = button.textContent;
    const discovery = {
      resetButton() {
        button.disabled = false;
        button.textContent = original;
      },
    };
    modelDiscoveries.set(profile, discovery);
    const isCurrent = () => !disposed
      && providerState.profiles.includes(profile)
      && modelDiscoveries.get(profile) === discovery;
    button.disabled = true;
    button.textContent = "正在获取…";
    try {
      const result = await controller.listModels(runtimeProbeProfile(profile, ""));
      if (!isCurrent()) return;
      const models = Array.isArray(result?.models) ? result.models : [];
      if (!models.length) {
        notify("未获取到模型列表", "info");
        return;
      }
      discovery.closePicker = openModelPicker(profile, models, isCurrent);
    } catch (error) {
      if (isCurrent()) reportProbeError(profile, error, "获取模型列表失败。");
    } finally {
      if (isCurrent()) discovery.resetButton();
    }
  }

  async function testProvider(profile, button) {
    if (disposed || !providerState.profiles.includes(profile)) return;
    clearProbeError();
    const baseUrl = (profile.base_url || "").trim();
    const apiKey = (profile.api_key || "").trim();
    const model = (profile.models || [])[0];
    if (!baseUrl || (!apiKey && !(profile.configured && profile.credential_action === "keep"))) {
      markInvalid(providerDetailInput("base_url"), !baseUrl);
      markInvalid(providerDetailInput("api_key"), !apiKey);
      setError("请先填写 API 地址和 API Key。");
      return;
    }
    if (!model) {
      setError("请先添加至少一个模型再测试。");
      return;
    }
    const original = button.textContent;
    const tested = JSON.stringify(runtimeProbeProfile(profile, model));
    const isCurrent = () => !disposed && providerState.profiles.includes(profile) && tested === JSON.stringify(runtimeProbeProfile(profile, model));
    button.disabled = true;
    button.textContent = "测试中…";
    try {
      await controller.testConnection(runtimeProbeProfile(profile, model));
      if (isCurrent()) notify(`${model} 测试通过`, "success");
    } catch (error) {
      if (isCurrent()) reportProbeError(profile, error, "连接失败。");
    } finally {
      if (!disposed) {
        button.disabled = false;
        button.textContent = original;
      }
    }
  }

  function removeProvider(profile) {
    invalidateModelDiscovery(profile); void cancel();
    providerState.profiles = providerState.profiles.filter((item) => item.id !== profile.id);
    if (providerState.selectedId === profile.id) {
      providerState.selectedId = providerState.profiles[0]?.id || "";
    }
    renderProviderPage();
    refreshModelSlots();
    refreshDirty();
  }

  function addProvider(preset) {
    const profile = {
      id: makeProfileId(),
      alias: preset?.label || "新模型服务",
      base_url: preset?.base_url || "",
      api_key: "",
      configured: false,
      credential_action: "keep",
      models: [],
    };
    providerState.profiles.push(profile);
    providerState.selectedId = profile.id;
    providerState.search = "";
    if (fields.providerSearch) {
      fields.providerSearch.value = "";
    }
    renderProviderPage();
    refreshModelSlots();
    refreshDirty();
  }

  function makeModalButton(text, className, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.addEventListener("click", handler);
    return button;
  }

  function openAddProviderChooser() {
    if (disposed || !apiView) return;
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("div");
    dialog.className = "confirm-dialog provider-add-dialog";
    const heading = document.createElement("h2");
    heading.textContent = "添加模型服务";
    const grid = document.createElement("div");
    grid.className = "provider-preset-grid";
    const close = () => {
      overlays.delete(overlay);
      overlay.remove();
    };
    PROVIDER_PRESETS.forEach((preset) => {
      const option = makeModalButton("", "provider-preset-option", () => {
        addProvider(preset);
        close();
      });
      const icon = document.createElement("span");
      icon.className = "provider-avatar";
      applyAvatar(icon, { iconUrl: preset.iconUrl, icon: preset.icon, initial: preset.label });
      const label = document.createElement("span");
      label.textContent = preset.label;
      option.append(icon, label);
      grid.append(option);
    });
    const custom = makeModalButton("", "provider-preset-option", () => {
      addProvider(null);
      close();
    });
    const customIcon = document.createElement("span");
    customIcon.className = "provider-avatar is-initial";
    customIcon.append(createIcon(document, "plus"));
    const customLabel = document.createElement("span");
    customLabel.textContent = "自定义";
    custom.append(customIcon, customLabel);
    grid.append(custom);
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    actions.append(makeModalButton("取消", "secondary-button", close));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close();
      }
    });
    dialog.append(heading, grid, actions);
    overlay.append(dialog);
    overlays.add(overlay);
    document.body.append(overlay);
  }

  function invalidateModelDiscovery(profile) {
    const discovery = modelDiscoveries.get(profile);
    modelDiscoveries.delete(profile);
    discovery?.resetButton();
    discovery?.closePicker?.();
  }

  function invalidateModelDiscoveries() {
    for (const profile of modelDiscoveries.keys()) invalidateModelDiscovery(profile);
  }

  function openModelPicker(profile, models, isCurrent) {
    if (!isCurrent()) return;
    const existing = new Set(profile.models || []);
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("div");
    dialog.className = "confirm-dialog model-picker-dialog";
    const heading = document.createElement("h2");
    heading.textContent = `获取到 ${models.length} 个模型`;
    const toolbar = document.createElement("div");
    toolbar.className = "model-picker-toolbar";
    const body = document.createElement("div");
    body.className = "model-picker-list";
    const checks = models.map((model) => {
      const item = document.createElement("label");
      item.className = "check-control model-picker-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = model;
      checkbox.checked = !existing.has(model);
      const text = document.createElement("span");
      text.textContent = existing.has(model) ? `${model}（已添加）` : model;
      item.append(checkbox, text);
      body.append(item);
      return checkbox;
    });
    const setAll = (predicate) => checks.forEach((checkbox) => {
      checkbox.checked = predicate(checkbox);
    });
    toolbar.append(
      makeModalButton("全选", "secondary-button compact-button", () => setAll(() => true)),
      makeModalButton("只选新增", "secondary-button compact-button", () =>
        setAll((checkbox) => !existing.has(checkbox.value)),
      ),
      makeModalButton("全不选", "secondary-button compact-button", () => setAll(() => false)),
    );
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const close = () => {
      overlays.delete(overlay);
      overlay.remove();
    };
    actions.append(
      makeModalButton("取消", "secondary-button", close),
      makeModalButton("添加", "primary-button", () => {
        if (!isCurrent() || !overlays.has(overlay)) {
          close();
          return;
        }
        const chosen = checks.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
        const added = addModelsToProfile(profile, chosen);
        close();
        notify(added ? `已添加 ${added} 个模型。` : "没有新增模型。", added ? "success" : "info");
      }),
    );
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close();
      }
    });
    dialog.append(heading, toolbar, body, actions);
    overlay.append(dialog);
    overlays.add(overlay);
    document.body.append(overlay);
    return close;
  }


  fields.providerSearch.addEventListener("input", () => { providerState.search = fields.providerSearch.value; renderProviderList(); });
  fields.addProviderButton.addEventListener("click", openAddProviderChooser);
  const escape = (event) => { if (event.key === "Escape" && overlays.size) { for (const item of overlays) item.remove(); overlays.clear(); } };
  document.addEventListener("keydown", escape);
  return {
    element: root,
    update() {
      const value = read() || [];
      if (JSON.stringify(value) === lastValue) return;
      const selected = providerState.selectedId;
      apiView = { profiles: value }; initializeProviderState();
      if (providerState.profiles.some(p => p.id === selected)) providerState.selectedId = selected;
      lastValue = JSON.stringify(value); renderProviderPage();
    },
    dispose() {
      if (disposed) return; disposed = true; void cancel();
      invalidateModelDiscoveries(); overlays.forEach(o => o.remove()); overlays.clear();
      document.removeEventListener("keydown", escape); root.remove();
    },
  };
}
