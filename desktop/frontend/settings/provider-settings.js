import { createIcon } from "../core/icons.js";
import {
  createProviderModelController,
  findProviderModelSelectionIssue,
} from "./provider-model-runtime.js";

export function createProviderSettingsFeature({
  document,
  window,
  invoke,
  onDirty: refreshDirty,
  onError: setError,
  notify,
  showPage,
  enhanceSelect,
  refreshSelect,
  markInvalid,
  setControlDisabled,
  setNumericBounds,
}) {
  const fields = {
    providerStatusStrip: document.getElementById("providerStatusStrip"),
    providerSearch: document.getElementById("providerSearch"),
    addProviderButton: document.getElementById("addProviderButton"),
    providerList: document.getElementById("providerList"),
    providerDetail: document.getElementById("providerDetail"),
    modelSlots: document.getElementById("modelSlots"),
    contextWindowTokens: document.getElementById("contextWindowTokens"),
    apiTimeout: document.getElementById("apiTimeout"),
    apiTemperature: document.getElementById("apiTemperature"),
    apiTopPEnabled: document.getElementById("apiTopPEnabled"),
    apiTopP: document.getElementById("apiTopP"),
    apiMaxTokensEnabled: document.getElementById("apiMaxTokensEnabled"),
    apiMaxTokens: document.getElementById("apiMaxTokens"),
  };
  const limits = {
    api_timeout_seconds: [1, 300],
    api_temperature: [0, 2],
    api_top_p: [0, 1],
    api_max_tokens: [1, 1000000],
  };
  let apiView = null;
  let disposed = false;
  const overlays = new Set();
  const modelDiscoveries = new Map();
  const listeners = [];
  const controller = createProviderModelController({
    invoke,
    readDraft: collectRuntimeProviderModelDraft,
    applySnapshot: applyRuntimeProviderModelSnapshot,
    onDirty: () => { if (!disposed) refreshDirty(); },
    onError: setError,
  });

  function clampInt(value, bounds) {
    const number = Number.parseInt(value, 10);
    if (!Number.isFinite(number)) {
      return bounds[0];
    }
    return Math.min(bounds[1], Math.max(bounds[0], number));
  }

  function clampFloat(value, bounds) {
    const number = Number.parseFloat(value);
    if (!Number.isFinite(number)) {
      return bounds[0];
    }
    return Math.min(bounds[1], Math.max(bounds[0], number));
  }

  function syncApiAdvancedState() {
    setControlDisabled(fields.apiTopP, !fields.apiTopPEnabled.checked, { row: false });
    setControlDisabled(fields.apiMaxTokens, !fields.apiMaxTokensEnabled.checked, { row: false });
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
  const inheritedSlotManualSelections = {};
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
    setError({
      AUTHENTICATION_FAILED: "验证失败，请检查 API Key。",
      PROVIDER_ACCESS_FORBIDDEN: "服务拒绝访问。",
      PROVIDER_TIMEOUT: "请求超时。",
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
    button.disabled = true;
    button.textContent = "测试中…";
    try {
      await controller.testConnection(runtimeProbeProfile(profile, model));
      if (!disposed && providerState.profiles.includes(profile)) notify(`${model} 测试通过`, "success");
    } catch (error) {
      reportProbeError(profile, error, "连接失败。");
    } finally {
      if (!disposed) {
        button.disabled = false;
        button.textContent = original;
      }
    }
  }

  function removeProvider(profile) {
    invalidateModelDiscovery(profile);
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

  function modelSlotElements(slot) {
    return {
      inheritInput: fields.modelSlots.querySelector(`[data-slot-inherit="${slot}"]`),
      profileSelect: fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`),
      modelSelect: fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`),
      contextWindowInput: slot === "core:chat" ? fields.contextWindowTokens : null,
    };
  }

  function readSlotSelection(slot) {
    const { profileSelect, modelSelect, contextWindowInput } = modelSlotElements(slot);
    const selection = {
      profile_id: profileSelect?.value || "",
      model: modelSelect?.value || "",
    };
    if (contextWindowInput) {
      const value = contextWindowInput.value.trim();
      selection.context_window_tokens = value ? Number.parseInt(value, 10) : null;
    }
    return selection;
  }

  function setSlotSelection(slot, selection, { preserveMissing = true } = {}) {
    const { profileSelect, modelSelect, contextWindowInput } = modelSlotElements(slot);
    if (!profileSelect || !modelSelect) {
      return;
    }
    const profileId = selection?.profile_id || "";
    if (profileId && Array.from(profileSelect.options).some((option) => option.value === profileId)) {
      profileSelect.value = profileId;
      refreshSelect(profileSelect);
    }
    syncModelOptions(slot, selection?.model || "", { preserveMissing });
    if (contextWindowInput) {
      contextWindowInput.value = selection?.context_window_tokens ?? "";
    }
  }

  function inheritedSlotSourceSelection(slot) {
    if (slot === "core:chat") {
      return null;
    }
    const chat = readSlotSelection("core:chat");
    return chat.profile_id && chat.model ? chat : null;
  }

  function syncInheritedSlotDisplays() {
    apiView.slot_fields.forEach((slot) => {
      const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`);
      if (inheritInput?.checked) {
        syncSlotInheritState(slot.id);
      }
    });
  }

  function handleSlotInheritChange(slot) {
    const { inheritInput } = modelSlotElements(slot);
    if (inheritInput?.checked) {
      const current = readSlotSelection(slot);
      if (current.profile_id && current.model) {
        inheritedSlotManualSelections[slot] = current;
      }
    } else if (inheritedSlotManualSelections[slot]) {
      setSlotSelection(slot, inheritedSlotManualSelections[slot], { preserveMissing: true });
      delete inheritedSlotManualSelections[slot];
    }
    syncSlotInheritState(slot);
    refreshDirty();
  }

  function renderModelSlots(selection, { preserveMissing = true } = {}) {
    if (disposed) return;
    fields.modelSlots.textContent = "";
    apiView.slot_fields.forEach((slot) => {
      const row = document.createElement("div");
      row.className = "form-row model-slot-row";
      row.dataset.slot = slot.id;
      const label = document.createElement("label");
      label.textContent = slot.label;
      const controls = document.createElement("div");
      controls.className = "slot-controls";
      const profileSelect = document.createElement("select");
      profileSelect.dataset.slotProfile = slot.id;
      const modelSelect = document.createElement("select");
      modelSelect.dataset.slotModel = slot.id;
      const contextWindowInput = slot.id === "core:chat" ? fields.contextWindowTokens : null;
      if (slot.allow_inherit) {
        row.classList.add("has-inherit");
        const inheritLabel = document.createElement("label");
        inheritLabel.className = "check-control slot-inherit";
        const inheritInput = document.createElement("input");
        inheritInput.type = "checkbox";
        inheritInput.dataset.slotInherit = slot.id;
        const inheritText = document.createElement("span");
        inheritText.textContent = "继承";
        inheritLabel.append(inheritInput, inheritText);
        controls.append(inheritLabel);
        inheritInput.addEventListener("change", () => handleSlotInheritChange(slot.id));
      }
      controls.append(profileSelect, modelSelect);
      const text = document.createElement("span");
      text.className = "setting-row-text";
      const title = document.createElement("span");
      title.className = "setting-title";
      title.textContent = slot.label;
      const description = document.createElement("span");
      description.className = "setting-desc";
      description.textContent = slot.description || "";
      text.append(title, description);
      row.append(text, controls);
      fields.modelSlots.append(row);
      enhanceSelect(profileSelect);
      enhanceSelect(modelSelect);
      profileSelect.addEventListener("change", () => {
        syncModelOptions(slot.id, "", { preserveMissing: false });
        if (slot.id === "core:chat") {
          syncInheritedSlotDisplays();
        }
        refreshDirty();
      });
      modelSelect.addEventListener("change", () => {
        if (slot.id === "core:chat") {
          syncInheritedSlotDisplays();
        }
        refreshDirty();
      });
      const selected = selection?.slots?.[slot.id] || { profile_id: "", model: "" };
      const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`);
      if (inheritInput) {
        inheritInput.checked = !selected.profile_id || !selected.model;
      }
      fillProfileOptions(profileSelect, selected.profile_id, slot.required);
      syncModelOptions(slot.id, selected.model, { preserveMissing });
      if (contextWindowInput) {
        contextWindowInput.value = selected.context_window_tokens ?? "";
      }
      syncSlotInheritState(slot.id);
    });
  }

  function fillProfileOptions(select, selectedId, required) {
    const profiles = providerState.profiles;
    select.textContent = "";
    if (!required) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "不启用";
      select.append(empty);
    }
    profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = profile.alias || profile.id;
      select.append(option);
    });
    const ids = profiles.map((profile) => profile.id);
    if (selectedId && !ids.includes(selectedId)) {
      const missing = document.createElement("option");
      missing.value = selectedId;
      missing.textContent = `${selectedId}（原选择不可用）`;
      select.append(missing);
    }
    let value = ids.includes(selectedId) ? selectedId : "";
    if (selectedId && !ids.includes(selectedId)) value = selectedId;
    if (!value && required && profiles[0]) {
      value = profiles[0].id;
    }
    select.value = value;
    refreshSelect(select);
  }

  function syncModelOptions(slot, selectedModel, { preserveMissing = selectedModel !== undefined } = {}) {
    const profileSelect = fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`);
    const modelSelect = fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`);
    const profile = providerState.profiles.find((item) => item.id === profileSelect.value);
    const models = profile?.models || [];
    const current = selectedModel ?? "";
    modelSelect.textContent = "";
    if (!profileSelect.value) {
      refreshSelect(modelSelect);
      return;
    }
    const resolved = resolveModelOptions(models, current, preserveMissing);
    resolved.options.forEach((model) => {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = models.includes(model) ? model : `${model}（原选择不可用）`;
      modelSelect.append(option);
    });
    modelSelect.value = resolved.value;
    refreshSelect(modelSelect);
  }

  function resolveModelOptions(models, selectedModel, preserveMissing) {
    const options = [...models];
    const current = String(selectedModel || "");
    if (preserveMissing && current && !options.includes(current)) {
      options.push(current);
    }
    const value = options.includes(current) ? current : options[0] || "";
    return { options, value };
  }

  function syncSlotInheritState(slot) {
    const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot}"]`);
    const inherited = Boolean(inheritInput?.checked);
    const profileSelect = fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`);
    const modelSelect = fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`);
    if (inherited) {
      const inheritedSelection = inheritedSlotSourceSelection(slot);
      if (inheritedSelection) {
        setSlotSelection(slot, inheritedSelection, { preserveMissing: true });
      }
    }
    if (profileSelect) {
      setControlDisabled(profileSelect, inherited, { row: false });
    }
    if (modelSelect) {
      setControlDisabled(modelSelect, inherited, { row: false });
    }
    fields.modelSlots
      .querySelector(`[data-slot="${slot}"]`)
      ?.classList.toggle("is-inherited", inherited);
  }

  function refreshModelSlots() {
    renderModelSlots(collectModelSelection(), { preserveMissing: false });
  }

  function collectModelSelection() {
    const slots = {};
    apiView.slot_fields.forEach((slot) => {
      const inherited = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`)?.checked;
      const selection = readSlotSelection(slot.id);
      slots[slot.id] = inherited
        ? { profile_id: "", model: "" }
        : selection;
    });
    return { slots };
  }

  function renderStrip(container, items) {
    container.textContent = "";
    items.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "status-chip";
      chip.textContent = `${item.label} ${item.value}`;
      container.append(chip);
    });
  }

  function normalizedProviderProfiles() {
    return providerState.profiles.map((profile) => ({
      id: profile.id,
      alias: (profile.alias || "").trim() || profile.id,
      base_url: (profile.base_url || "").trim(),
      models: (profile.models || []).map((model) => String(model).trim()).filter(Boolean),
    }));
  }

  function providerDisplayName(profile) {
    return profile.alias || profile.id || "未命名模型服务";
  }

  function focusProviderValidation(profile, field) {
    providerState.selectedId = profile.id;
    providerState.search = "";
    if (fields.providerSearch) {
      fields.providerSearch.value = "";
    }
    showPage("providers");
    renderProviderPage();
    markInvalid(providerDetailInput(field), true);
  }

  function validateApiSettingsBeforeSubmit() {
    const profiles = normalizedProviderProfiles();
    if (!profiles.length) {
      showPage("providers");
      setError("请至少添加一个 API 模型服务。");
      return false;
    }
    const missingBaseUrl = profiles.find((profile) => !profile.base_url);
    if (missingBaseUrl) {
      focusProviderValidation(missingBaseUrl, "base_url");
      setError(`模型服务「${providerDisplayName(missingBaseUrl)}」缺少 API 地址。`);
      return false;
    }
    const selection = collectModelSelection();
    const issue = findProviderModelSelectionIssue({
      providers: profiles,
      modelSlots: selection.slots,
      slotFields: apiView.slot_fields,
    });
    if (!issue) {
      return true;
    }
    showPage("model");
    refreshModelSlots();
    if (issue.type === "incomplete") {
      setError(`${issue.label}必须同时选择模型服务和模型。`);
    } else if (issue.type === "required") {
      setError(`请选择可用的${issue.label}。`);
    } else {
      setError(`${issue.label}引用的模型服务或模型已不可用，请重新选择。`);
    }
    return false;
  }

  function collectModelSettings() {
    const temperature = clampFloat(fields.apiTemperature.value, limits.api_temperature);
    const initialTemperature = apiView.settings.temperature;
    return {
      timeout_seconds: clampInt(fields.apiTimeout.value, limits.api_timeout_seconds),
      temperature:
        initialTemperature === null && Math.abs(temperature - 0.8) < 0.005
          ? null
          : temperature,
      top_p: fields.apiTopPEnabled.checked
        ? clampFloat(fields.apiTopP.value, limits.api_top_p)
        : null,
      max_tokens: fields.apiMaxTokensEnabled.checked
        ? clampInt(fields.apiMaxTokens.value, limits.api_max_tokens)
        : null,
    };
  }

  function runtimeCredential(profile) {
    const value = (profile.api_key || "").trim();
    let action = profile.credential_action || (profile.configured ? "keep" : "clear");
    if (value) action = "replace";
    if (action === "keep" && !profile.configured) action = "clear";
    return { action, value: action === "replace" ? value : "" };
  }

  function runtimeProbeProfile(profile, model) {
    return {
      profile_id: profile.id,
      base_url: (profile.base_url || "").trim(),
      model: String(model || "").trim(),
      timeout_seconds: clampInt(fields.apiTimeout.value || 15, [1, 60]),
      credential: runtimeCredential(profile),
    };
  }

  function collectRuntimeProviderModelDraft() {
    return {
      providers: providerState.profiles.map((profile) => ({
        id: profile.id,
        alias: (profile.alias || "").trim() || profile.id,
        base_url: (profile.base_url || "").trim(),
        models: (profile.models || []).map((model) => String(model).trim()).filter(Boolean),
        credential: runtimeCredential(profile),
      })),
      model_slots: collectModelSelection().slots,
      settings: collectModelSettings(),
    };
  }

  function applyRuntimeProviderModelSnapshot(snapshot) {
    if (disposed) return;
    apiView = {
      profiles: snapshot.providers.map((profile) => ({
        ...profile,
        api_key: "",
        credential_action: profile.configured ? "keep" : "clear",
      })),
      settings: snapshot.settings,
      slot_fields: snapshot.model_slots.map((slot) => ({
        id: slot.identity,
        label: slot.label,
        description: slot.description,
        required: slot.required,
        allow_inherit: slot.identity !== "core:chat" && !slot.required,
        owner_type: slot.ownerType,
        owner_id: slot.ownerId,
        reason_code: slot.reasonCode,
      })),
      model_selection: {
        slots: Object.fromEntries(snapshot.model_slots.map((slot) => [slot.identity, slot.selection])),
      },
    };
    initializeProviderState();
    renderProviderPage();
    renderModelSlots(apiView.model_selection);
    setNumericBounds(fields.contextWindowTokens, [4_096, 2_000_000]);
    setNumericBounds(fields.apiTimeout, limits.api_timeout_seconds);
    setNumericBounds(fields.apiMaxTokens, limits.api_max_tokens);
    fields.apiTimeout.value = snapshot.settings.timeout_seconds;
    fields.apiTemperature.value = snapshot.settings.temperature ?? 0.8;
    fields.apiTopPEnabled.checked = snapshot.settings.top_p !== null;
    fields.apiTopP.value = snapshot.settings.top_p ?? 1;
    fields.apiMaxTokensEnabled.checked = snapshot.settings.max_tokens !== null;
    fields.apiMaxTokens.value = snapshot.settings.max_tokens ?? 2048;
    syncApiAdvancedState();
  }

  function listen(element, event, handler) {
    element.addEventListener(event, handler);
    listeners.push(() => element.removeEventListener(event, handler));
  }

  listen(fields.addProviderButton, "click", openAddProviderChooser);
  listen(fields.providerSearch, "input", () => {
    providerState.search = fields.providerSearch.value;
    renderProviderList();
  });
  listen(fields.apiTopPEnabled, "change", syncApiAdvancedState);
  listen(fields.apiMaxTokensEnabled, "change", syncApiAdvancedState);
  for (const field of [
    fields.contextWindowTokens, fields.apiTimeout, fields.apiTemperature,
    fields.apiTopPEnabled, fields.apiTopP, fields.apiMaxTokensEnabled, fields.apiMaxTokens,
  ]) {
    listen(field, "input", refreshDirty);
    listen(field, "change", refreshDirty);
  }

  return Object.freeze({
    initialize: () => controller.refreshCurrent(),
    async save() {
      if (!validateApiSettingsBeforeSubmit()) throw new Error("模型服务或模型设置未通过校验。");
      return controller.save();
    },
    isDirty: () => !disposed && controller.isDirty(),
    refreshCurrent: controller.refreshCurrent,
    rebindIdentity(coreGenerationId) {
      controller.rebindIdentity(coreGenerationId);
      invalidateModelDiscoveries();
    },
    cancelOperations: controller.cancelOperations,
    hasModelSettings: (pluginId) => (apiView?.slot_fields || [])
      .some((slot) => slot.owner_id === pluginId),
    onPageChanged(page) {
      // Provider edits can change the choices and labels on the model page.
      if (!disposed && apiView && page === "model") refreshModelSlots();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      invalidateModelDiscoveries();
      listeners.splice(0).forEach((remove) => remove());
      overlays.forEach((overlay) => overlay.remove());
      overlays.clear();
      controller.dispose();
    },
  });
}
