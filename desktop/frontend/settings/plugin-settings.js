import { createIcon } from "../core/icons.js";
import { animatedBrainMarkup } from "../core/animated-icons.js";
import { createPluginController } from "./plugin-runtime.js";
import * as pluginPresentation from "./plugin-presentation.js";
import { countCharacterScopedCollectionDrafts } from "./character-switch-runtime.js";

export function createPluginSettingsFeature({
  document,
  window,
  invoke,
  onDirty: refreshDirty,
  onError: setError,
  notify,
  confirmAction,
  enhanceSelect,
  refreshSelect,
  closeSelects,
  focusSelect,
  replayMotion,
  getVoiceController,
  getAsrController = () => null,
  removeOverlayAfterExit,
  showPage,
  isMemoryTransitioning,
  hasPendingCharacterSelection,
}) {
  const fields = {
    pluginSearch: document.getElementById("pluginSearch"),
    pluginInstallMenuRoot: document.getElementById("pluginInstallMenuRoot"),
    pluginInstallMenuButton: document.getElementById("pluginInstallMenuButton"),
    pluginInstallMenu: document.getElementById("pluginInstallMenu"),
    pluginInstallZipButton: document.getElementById("pluginInstallZipButton"),
    pluginInstallFolderButton: document.getElementById("pluginInstallFolderButton"),
    pluginList: document.getElementById("pluginList"),
    pluginDetail: document.getElementById("pluginDetail"),
    pluginTotal: document.getElementById("pluginTotal"),
    pluginRoleTabs: document.getElementById("pluginRoleTabs"),
    aboutComponentsSummary: document.getElementById("aboutComponentsSummary"),
    aboutComponentsRefresh: document.getElementById("aboutComponentsRefresh"),
    aboutComponentsState: document.getElementById("aboutComponentsState"),
    aboutComponentsList: document.getElementById("aboutComponentsList"),
    memorySurface: document.getElementById("memorySurface"),
    pages: {
      memory: document.getElementById("page-memory"),
      plugins: document.getElementById("page-plugins"),
      about: document.getElementById("page-about"),
    },
  };

  const pluginState = {
    selectedId: "",
    role: "all",
    enabledById: {},
    initialEnabledById: {},
    settingsValues: {},
    initialSettingsValues: {},
    actionBusyKey: "",
    managementBusy: false,
  };
  let pluginSettingsDialog = null;
  const pluginCollectionState = new Map();

  function hasCollectionDrafts() {
    return Array.from(pluginCollectionState.values()).some((state) => Boolean(state.editor));
  }
  let pluginActivityRefreshTimer = null;
  let pluginActivityRefreshInFlight = false;
  let aboutComponentsReadError = "";

  let pluginView = { items: [] };
  const timers = new Set();
  const listeners = [];

  function setTimer(callback, milliseconds) {
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      callback();
    }, milliseconds);
    timers.add(timer);
    return timer;
  }

  function clearTimer(timer) {
    window.clearTimeout(timer);
    timers.delete(timer);
  }

  function listen(target, event, callback) {
    if (!target) return;
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }

  function resourceStatusLabel(status, ready = false) {
    if (status === "not_required") {
      return "无需";
    }
    if (status === "running" || status === "queued") {
      return "处理中";
    }
    if (status === "succeeded") {
      return "已完成";
    }
    if (status === "failed") {
      return "失败";
    }
    if (status === "cancelled") {
      return "可继续";
    }
    return ready ? "已就绪" : "缺失";
  }

  function resourceStatusClass(status, ready = false) {
    if (status === "not_required") {
      return "ready";
    }
    if (status === "running" || status === "queued") {
      return "working";
    }
    if (status === "succeeded" || ready) {
      return "ready";
    }
    if (status === "failed") {
      return "error";
    }
    if (status === "cancelled") {
      return "warning";
    }
    return "neutral";
  }

  function renderResourceCard(container, model) {
    if (!container) {
      return;
    }
    container.textContent = "";
    container.classList.toggle("is-muted", Boolean(model.muted));
    container.classList.toggle("is-running", model.status === "running" || model.status === "queued");
    const head = document.createElement("div");
    head.className = "resource-card__head";
    const titleWrap = document.createElement("div");
    titleWrap.className = "resource-card__title-wrap";
    const title = document.createElement("strong");
    title.textContent = model.title;
    const subtitle = document.createElement("span");
    subtitle.textContent = model.subtitle || "";
    titleWrap.append(title);
    if (model.subtitle) titleWrap.append(subtitle);
    const statusLabel = model.statusLabel || resourceStatusLabel(model.status, model.ready);
    const status = renderSemanticStatus({
      state: model.statusTone || resourceStatusClass(model.status, model.ready),
      label: statusLabel,
    }, "resource-card__status");
    head.append(titleWrap, status);

    const body = document.createElement("div");
    body.className = "resource-card__body";
    if (model.message && model.message.trim() !== statusLabel.trim()) {
      const message = document.createElement("p");
      message.className = "resource-message";
      message.textContent = model.message;
      body.append(message);
    }
    if (model.detail) {
      const detail = document.createElement("p");
      detail.className = "resource-detail";
      detail.textContent = model.detail;
      body.append(detail);
    }
    if (model.progressVisible) {
      const progress = document.createElement("div");
      progress.className = "resource-progress";
      progress.setAttribute("role", "progressbar");
      progress.setAttribute("aria-label", model.progressLabel || `${model.title}处理进度`);
      const bar = document.createElement("span");
      if (Number.isFinite(model.progress)) {
        const progressValue = Math.max(0, Math.min(100, Number(model.progress)));
        progress.setAttribute("aria-valuemin", "0");
        progress.setAttribute("aria-valuemax", "100");
        progress.setAttribute("aria-valuenow", String(progressValue));
        bar.style.width = `${progressValue}%`;
      } else {
        progress.classList.add("is-indeterminate");
      }
      progress.append(bar);
      body.append(progress);
    }
    if (Array.isArray(model.meta) && model.meta.length) {
      const meta = document.createElement("dl");
      meta.className = "resource-meta";
      model.meta.forEach(([label, value]) => {
        if (!value) {
          return;
        }
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = value;
        meta.append(dt, dd);
      });
      body.append(meta);
    }

    const actions = document.createElement("div");
    actions.className = "resource-actions";
    (model.actions || []).forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = action.primary ? "" : action.danger ? "danger-button" : "secondary-button";
      button.textContent = action.busy ? `${action.label}…` : action.label;
      if (action.icon) button.append(createIcon(document, action.icon));
      button.disabled = Boolean(action.disabled);
      if (action.focusKey) button.dataset.aboutActionKey = action.focusKey;
      if (action.resourceKey) button.dataset.aboutResourceKey = action.resourceKey;
      button.addEventListener("click", action.onClick);
      actions.append(button);
    });
    if (actions.childNodes.length) {
      body.append(actions);
    }
    container.append(head);
    if (body.childNodes.length) container.append(body);
  }



  function compactText(value, max = 110) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text.length <= max) {
      return text;
    }
    return `${text.slice(0, max - 1)}…`;
  }

  function clonePlain(value) {
    return JSON.parse(JSON.stringify(value || {}));
  }

  function plainEqual(left, right) {
    return JSON.stringify(left || {}) === JSON.stringify(right || {});
  }

  function pluginSettingsSections(plugin) {
    return Array.isArray(plugin?.settings) ? plugin.settings : [];
  }

  function pluginSectionValues(pluginId, sectionId) {
    pluginState.settingsValues[pluginId] = pluginState.settingsValues[pluginId] || {};
    pluginState.settingsValues[pluginId][sectionId] = pluginState.settingsValues[pluginId][sectionId] || {};
    return pluginState.settingsValues[pluginId][sectionId];
  }

  function pluginFieldValue(plugin, section, field) {
    const values = pluginSectionValues(plugin.id, section.section_id);
    if (!Object.prototype.hasOwnProperty.call(values, field.key)) {
      values[field.key] = field.value ?? field.default ?? "";
    }
    return values[field.key];
  }

  function pluginFieldEditable(field) {
    return !field.readonly && !["readonly", "status", "resource"].includes(field.type);
  }

  function setPluginFieldValue(plugin, section, field, value) {
    const values = pluginSectionValues(plugin.id, section.section_id);
    values[field.key] = value;
    refreshDirty();
  }

  function initializePluginState() {
    const previouslySelectedId = pluginState.selectedId;
    pluginState.enabledById = {};
    pluginState.initialEnabledById = {};
    pluginState.settingsValues = {};
    pluginState.initialSettingsValues = {};
    (pluginView?.items || []).forEach((plugin) => {
      pluginState.enabledById[plugin.id] = Boolean(plugin.enabled || plugin.required);
      pluginState.initialEnabledById[plugin.id] = Boolean(plugin.enabled || plugin.required);
      pluginState.settingsValues[plugin.id] = {};
      pluginSettingsSections(plugin).forEach((section) => {
        pluginState.settingsValues[plugin.id][section.section_id] = clonePlain(section.values);
      });
      pluginState.initialSettingsValues[plugin.id] = clonePlain(pluginState.settingsValues[plugin.id]);
    });
    pluginState.selectedId = pluginView?.items?.some((item) => item.id === previouslySelectedId)
      ? previouslySelectedId
      : pluginView?.items?.[0]?.id || "";
  }

  function projectPluginActivity(plugin) {
    const settings = pluginSettingsSections(plugin).map((section) => ({
      ...section,
      values: pluginState.settingsValues[plugin?.id]?.[section.section_id] || section.values,
    }));
    return pluginPresentation?.projectPluginActivity?.({ ...plugin, settings }) || {
      state: "neutral",
      label: "",
      message: "",
      hasRunningResource: false,
      isTransient: false,
    };
  }

  function pluginStatusCopy(plugin) {
    const plugins = pluginView?.items || [];
    const unavailable = (plugin.missing_services || []).map((serviceKey) => (
      pluginPresentation.presentPluginComponent(serviceKey, plugins)
    ));
    return pluginPresentation.presentPluginStatus({
      state: plugin.state,
      reasonCode: plugin.reason_code,
      unavailable,
    });
  }

  function pluginStatusIsStarting(plugin) {
    return plugin?.reason_code === "PLUGIN_APPLICATION_NOT_READY";
  }

  function pluginHasExceptionalStatus(plugin) {
    if (pluginStatusIsStarting(plugin)) return false;
    const status = pluginStatusCopy(plugin);
    return Boolean(status.message || status.diagnostic);
  }

  function pluginInstallMenuItems() {
    return [fields.pluginInstallZipButton, fields.pluginInstallFolderButton]
      .filter((item) => item && !item.disabled);
  }

  function setPluginInstallMenuOpen(open, { focusItem = false, restoreFocus = false } = {}) {
    const nextOpen = Boolean(open && !fields.pluginInstallMenuButton.disabled);
    fields.pluginInstallMenu.hidden = !nextOpen;
    fields.pluginInstallMenuButton.setAttribute("aria-expanded", String(nextOpen));
    fields.pluginInstallMenuRoot.classList.toggle("is-open", nextOpen);
    if (nextOpen && focusItem) {
      pluginInstallMenuItems()[0]?.focus();
    } else if (!nextOpen && restoreFocus && !fields.pluginInstallMenuButton.disabled) {
      fields.pluginInstallMenuButton.focus();
    }
  }

  function movePluginInstallMenuFocus(direction) {
    const items = pluginInstallMenuItems();
    if (!items.length) return;
    const current = items.indexOf(document.activeElement);
    const next = current < 0 ? 0 : (current + direction + items.length) % items.length;
    items[next].focus();
  }

  function filteredPlugins() {
    return pluginPresentation.filterPluginCatalog(pluginView.items || [], pluginFilters());
  }

  function pluginDisplayName(plugin) {
    return `${plugin.name || plugin.plugin_id || plugin.id}（${plugin.plugin_id || plugin.id}）`;
  }

  function syncPluginEnableSwitches() {
    fields.pluginDetail.querySelectorAll(".plugin-enable-switch input[data-plugin-install-id]")
      .forEach((toggle) => {
        const plugin = (pluginView?.items || [])
          .find((item) => item.id === toggle.dataset.pluginInstallId);
        if (plugin) toggle.checked = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
      });
  }

  async function setPluginEnabled(plugin, enabled) {
    if (pluginState.managementBusy) return;
    const plugins = pluginView?.items || [];
    if (enabled) {
      const dependencies = pluginPresentation.disabledRequiredPluginProviders(
        plugin,
        plugins,
        pluginState.enabledById,
      );
      if (dependencies.length) {
        const confirmed = await confirmAction(
          `“${plugin.name || plugin.id}”还需要以下插件。要一起启用吗？`,
          {
            title: "启用所需插件",
            confirmText: "一起启用",
            details: dependencies.map(pluginDisplayName),
          },
        );
        if (!confirmed) {
          syncPluginEnableSwitches();
          return;
        }
        dependencies.forEach((dependency) => {
          pluginState.enabledById[dependency.id] = true;
        });
      }
    } else {
      const dependents = pluginPresentation.enabledPluginDependents(
        plugin,
        plugins,
        pluginState.enabledById,
      );
      if (dependents.length) {
        const confirmed = await confirmAction(
          `以下插件正在依赖“${plugin.name || plugin.id}”。停用后，它们将无法使用。`,
          {
            title: "停用依赖插件",
            confirmText: "仍要停用",
            danger: true,
            details: dependents.map(pluginDisplayName),
          },
        );
        if (!confirmed) {
          syncPluginEnableSwitches();
          return;
        }
      }
    }
    pluginState.enabledById[plugin.id] = plugin.required ? true : Boolean(enabled);
    syncPluginEnableSwitches();
    const label = fields.pluginDetail.querySelector(".plugin-enable-label");
    if (label) label.textContent = pluginState.enabledById[plugin.id] ? "已启用" : "已停用";
    refreshDirty();
  }

  function renderPluginList() {
    const all = pluginView.items || [];
    const filtered = filteredPlugins();
    const plugins = Object.keys(pluginPresentation.pluginKinds).flatMap((kind) => (
      filtered.filter((plugin) => pluginPresentation.pluginMetadata(plugin).kind === kind)
    ));
    const oldCards = new Map([...fields.pluginList.querySelectorAll('.plugin-card')].map((card) => [card.dataset.pluginInstallId, card]));
    const focusedId = document.activeElement?.closest('.plugin-card')?.dataset.pluginInstallId;
    fields.pluginList.textContent = '';
    fields.pluginRoleTabs.textContent = '';
    fields.pluginTotal.textContent = String(all.length);
    const roles = { all: '全部', ...pluginPresentation.pluginKinds };
    Object.entries(roles).forEach(([key, label]) => {
      const count = key === 'all' ? all.length : all.filter((plugin) => pluginPresentation.pluginMetadata(plugin).kind === key).length;
      const tab = pluginNode('button', 'plugin-role-tab', `${label} ${count}`);
      tab.type = 'button'; tab.dataset.pluginRole = key;
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(pluginState.role === key));
      tab.setAttribute('aria-controls', 'pluginList'); tab.tabIndex = pluginState.role === key ? 0 : -1;
      tab.addEventListener('click', () => {
        pluginState.role = key;
        pluginState.selectedId = '';
        renderPluginPage();
        fields.pluginList.scrollTop = 0;
        fields.pluginDetail.scrollTop = 0;
        fields.pluginRoleTabs.querySelector(`[data-plugin-role="${key}"]`)?.focus();
      });
      tab.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const keys = Object.keys(roles); const index = keys.indexOf(key);
        fields.pluginRoleTabs.children[event.key === 'Home' ? 0 : event.key === 'End' ? keys.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + keys.length) % keys.length].click();
      });
      fields.pluginRoleTabs.append(tab);
    });
    if (!plugins.some((plugin) => plugin.id === pluginState.selectedId)) pluginState.selectedId = plugins[0]?.id || '';
    for (const [kind, label] of Object.entries(pluginPresentation.pluginKinds)) {
      const entries = plugins.filter((plugin) => pluginPresentation.pluginMetadata(plugin).kind === kind);
      if (!entries.length) continue;
      const group = pluginNode('section', `plugin-group kind-${kind}`);
      const header = pluginNode('h3', 'plugin-group-heading');
      header.id = `plugin-group-${kind}`;
      group.setAttribute('aria-labelledby', header.id);
      const badge = pluginNode('span', 'plugin-group-label');
      badge.append(createIcon(document, { extension: 'puzzle', provider: 'audio-lines', infrastructure: 'layers' }[kind]),
        pluginNode('span', '', label), pluginNode('span', 'plugin-group-count', entries.length));
      header.append(badge);
      const problems = pluginPresentation.filterPluginCatalog(entries, { status: 'problem' }).length;
      if (kind === 'infrastructure' && problems) header.append(pluginNode('span', 'plugin-system-problems', ` · ${problems} 个需要处理`));
      group.append(header);
      for (const plugin of entries) {
        const metadata = pluginPresentation.pluginMetadata(plugin);
        const activity = pluginLiveStatus(plugin);
        const selected = plugin.id === pluginState.selectedId;
        const signature = JSON.stringify([plugin.name, plugin.description, plugin.source, metadata, activity, selected]);
        let card = oldCards.get(plugin.id);
        if (!card || card.dataset.signature !== signature) {
          card = pluginNode('button', `plugin-card${selected ? ' is-selected' : ''}`);
          card.type = 'button'; card.dataset.pluginInstallId = plugin.id; card.dataset.signature = signature;
          card.setAttribute('aria-pressed', String(selected));
          const main = pluginNode('span', 'plugin-card-main');
          const titleLine = pluginNode('span', 'plugin-card-title-line');
          const title = pluginNode('strong', 'plugin-card-title', plugin.name || plugin.plugin_id || plugin.id);
          title.dataset.tooltip = title.textContent;
          const live = renderSemanticStatus(activity);
          live.dataset.tooltip = activity.label;
          live.setAttribute('aria-label', `运行状态：${activity.label}`);
          titleLine.append(title, live);
          main.append(titleLine);
          if (plugin.description) main.append(pluginNode('span', 'card-desc', plugin.description));
          card.append(pluginIcon(plugin), main);
          card.addEventListener('click', () => selectManagedPlugin(plugin.id));
        }
        group.append(card);
      }
      fields.pluginList.append(group);
    }
    if (!plugins.length) fields.pluginList.append(pluginNode('p', 'empty-state', '没有找到符合条件的插件。'));
    if (focusedId) fields.pluginList.querySelector(`[data-plugin-install-id="${CSS.escape(focusedId)}"]`)?.focus({ preventScroll: true });
  }

  function renderSemanticStatus(value, className = "") {
    const state = ["neutral", "ready", "working", "warning", "error"].includes(value?.state)
      ? value.state : "neutral";
    const status = document.createElement("span");
    status.className = `semantic-status is-${state} ${className}`.trim();
    const dot = document.createElement("span");
    dot.className = "semantic-status__dot";
    dot.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = String(value?.label || "状态未知");
    status.append(dot, label);
    return status;
  }

  function pluginResourceStatus(value) {
    if (value.applicability === "not_required") return { state: "ready", label: "无需安装" };
    if (value.applicability === "unsupported") return { state: "warning", label: "不支持一键安装" };
    if (value.taskState === "queued") return { state: "working", label: "等待下载" };
    if (value.taskState === "running") return { state: "working", label: "下载中" };
    if (value.taskState === "failed") {
      return value.ready
        ? { state: "warning", label: "更新失败" }
        : { state: "error", label: "下载失败" };
    }
    if (value.taskState === "cancelled") return { state: "warning", label: "已取消" };
    if (value.ready) return { state: "ready", label: "已安装" };
    return { state: "neutral", label: "未安装" };
  }

  function pluginResourceControl(plugin, section, field, value, options = {}) {
    const container = document.createElement("div");
    container.className = "resource-card plugin-resource-card";
    container.setAttribute("role", "group");
    container.setAttribute("aria-label", field.label || field.key);
    const available = new Set(value.availableActionIds || []);
    const resourceKey = `${plugin.id}:${section.section_id}:${field.key}`;
    container.dataset.aboutResourceKey = resourceKey;
    container.tabIndex = -1;
    const actionModels = options.overview ? [{
      label: "前往下载设置",
      icon: "arrow-right",
      disabled: pluginState.managementBusy,
      focusKey: `overview:${resourceKey}`,
      resourceKey,
      onClick: () => {
        showPage("plugins");
        selectManagedPlugin(plugin.id, { reveal: true });
        openPluginSettingsDialog(plugin, { sectionId: section.section_id, fieldKey: field.key });
      },
    }] : (section.actions || [])
      .filter((action) => available.has(action.action_id))
      .map((action, index) => {
        const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
        return {
          label: action.label || action.action_id,
          danger: Boolean(action.danger),
          primary: index === 0 && !value.ready
            && !["queued", "running"].includes(value.taskState),
          disabled: pluginState.managementBusy || Boolean(pluginState.actionBusyKey),
          busy: pluginState.actionBusyKey === busyKey,
          focusKey: options.focusActions ? busyKey : "",
          resourceKey: options.focusActions ? resourceKey : "",
          onClick: () => runPluginSettingsAction(
            plugin,
            section,
            action,
            options.focusActions ? resourceKey : "",
          ),
        };
      });
    const progressVisible = ["queued", "running"].includes(value.taskState);
    const status = pluginResourceStatus(value);
    const detail = [
      value.detail,
      progressVisible && Number.isSafeInteger(value.progress) ? `${value.progress}%` : "",
    ].filter(Boolean).join(" · ");
    renderResourceCard(container, {
      title: field.label || field.key,
      subtitle: [options.owner, value.subtitle].filter(Boolean).join(" · "),
      status: value.taskState,
      ready: Boolean(value.ready),
      statusLabel: status.label,
      statusTone: status.state,
      message: value.message || field.description || "",
      detail,
      progressVisible,
      progress: value.progress,
      progressLabel: `${field.label || field.key}下载进度`,
      actions: actionModels,
    });
    return container;
  }

  function aboutComponentContributions() {
    return pluginPresentation.pluginResourceContributions(pluginView.items || []);
  }

  function aboutComponentsRunning() {
    return aboutComponentContributions().some(({ value }) => (
      ["queued", "running"].includes(value.taskState)
    ));
  }

  function renderAboutComponents({ restoreResourceKey = "" } = {}) {
    if (!fields.aboutComponentsList) return;
    const focusedKey = document.activeElement?.dataset?.aboutActionKey || "";
    const focusedResourceKey = document.activeElement?.dataset?.aboutResourceKey || restoreResourceKey;
    const contributions = aboutComponentContributions();
    const ready = contributions.filter(({ value }) => (
      (value.ready && value.applicability === "required") || value.applicability === "not_required"
    )).length;
    const pluginCount = new Set(contributions.map(({ plugin }) => plugin.id)).size;
    const unsupported = contributions.filter(({ value }) => value.applicability === "unsupported").length;
    fields.aboutComponentsSummary.textContent = contributions.length
      ? `${ready}/${contributions.length} 已就绪 · 来自 ${pluginCount} 个已启用插件${unsupported ? ` · ${unsupported} 项当前平台不支持` : ""}`
      : "启用插件尚未注册本地组件";
    fields.aboutComponentsRefresh.disabled = pluginActivityRefreshInFlight;
    fields.aboutComponentsState.textContent = aboutComponentsReadError;
    const snapshot = runtimePluginController?.snapshot?.();
    if (!aboutComponentsReadError && snapshot && ["starting", "waiting"].includes(snapshot.state)) {
      fields.aboutComponentsState.textContent = "插件正在启动…";
    }
    fields.aboutComponentsList.textContent = "";
    contributions.forEach(({ plugin, section, field, value }) => {
      fields.aboutComponentsList.append(pluginResourceControl(
        plugin,
        section,
        field,
        value,
        { owner: plugin.name || plugin.plugin_id, overview: true },
      ));
    });
    if (focusedKey) {
      const action = fields.aboutComponentsList.querySelector(
        `[data-about-action-key="${CSS.escape(focusedKey)}"]`,
      );
      const fallback = focusedResourceKey ? fields.aboutComponentsList.querySelector(
        `[data-about-resource-key="${CSS.escape(focusedResourceKey)}"]`,
      ) : null;
      (action || fallback)?.focus({ preventScroll: true });
    }
    schedulePluginActivityRefresh();
  }

  function pluginSettingControl(plugin, section, field) {
    const value = pluginFieldValue(plugin, section, field);
    if (field.type === "status") {
      const control = document.createElement("div");
      control.className = "plugin-status-control";
      control.append(renderSemanticStatus(value));
      if (value?.message && value.state !== "ready" && value.state !== "neutral") {
        const message = document.createElement("p");
        message.className = "plugin-status-message";
        message.textContent = value.message;
        control.append(message);
      }
      return control;
    }
    if (field.type === "resource") {
      return pluginResourceControl(plugin, section, field, value || {});
    }
    if (field.readonly || field.type === "readonly") {
      const row = document.createElement("div");
      row.className = "plugin-readonly-control";
      const output = document.createElement("output");
      output.className = "plugin-readonly-output";
      output.textContent = Array.isArray(value) ? value.join(" ; ") : String(value ?? "");
      row.append(output);
      if (field.copyable) {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "secondary-button compact-button";
        copy.textContent = "复制";
        copy.addEventListener("click", async () => {
          await navigator.clipboard.writeText(output.textContent || "");
          copy.textContent = "已复制";
          setTimer(() => {
            copy.textContent = "复制";
          }, 1200);
        });
        row.append(copy);
      }
      return row;
    }
    if (field.type === "boolean") {
      const label = document.createElement("label");
      label.className = "check-control";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      input.addEventListener("change", () => setPluginFieldValue(plugin, section, field, input.checked));
      const text = document.createElement("span");
      text.textContent = field.label || field.key;
      label.append(input, text);
      return label;
    }
    if (field.type === "select") {
      const select = document.createElement("select");
      select.required = Boolean(field.required);
      (field.options || []).forEach((option) => {
        const item = document.createElement("option");
        item.value = String(option.value);
        item.textContent = option.label || String(option.value);
        select.append(item);
      });
      select.value = String(value ?? field.default ?? "");
      select.addEventListener("change", () => {
        const selected = (field.options || []).find((option) => String(option.value) === select.value);
        setPluginFieldValue(plugin, section, field, selected ? selected.value : select.value);
      });
      setTimer(() => enhanceSelect(select), 0);
      return select;
    }
    const input = document.createElement("input");
    input.type = field.type === "integer" || field.type === "number" ? "number" : field.type === "password" ? "password" : "text";
    input.required = Boolean(field.required);
    if (Number.isSafeInteger(field.maxLength)) input.maxLength = field.maxLength;
    if (field.minimum !== undefined) {
      input.min = String(field.minimum);
    }
    if (field.maximum !== undefined) {
      input.max = String(field.maximum);
    }
    if (field.step !== undefined) {
      input.step = String(field.step);
    } else if (field.type === "integer") {
      input.step = "1";
    }
    input.value = String(value ?? "");
    input.addEventListener("input", () => {
      if (field.type === "integer") {
        setPluginFieldValue(plugin, section, field, Number.parseInt(input.value, 10));
      } else if (field.type === "number") {
        setPluginFieldValue(plugin, section, field, Number.parseFloat(input.value));
      } else {
        setPluginFieldValue(plugin, section, field, input.value);
      }
    });
    return input;
  }

  function pluginCollectionKey(plugin, section, collection) {
    return `${plugin.id}:${section.section_id}:${collection.collection_id}`;
  }

  function pluginCollectionRuntimeState(plugin, section, collection) {
    const key = pluginCollectionKey(plugin, section, collection);
    if (!pluginCollectionState.has(key)) {
      pluginCollectionState.set(key, {
        surface: section.surface,
        items: [], nextCursor: null, total: null, search: "", filters: {},
        loading: false, loaded: false, error: "", editor: null, editorError: "",
        selectedItemId: "", searchTimer: null, queryRevision: 0, queryPending: false,
        queryPendingRender: false, operation: "", motion: null,
      });
    }
    return pluginCollectionState.get(key);
  }

  async function queryPluginCollection(
    plugin,
    section,
    collection,
    { append = false, render = true } = {},
  ) {
    if (!pluginView?.items.includes(plugin)) return;
    if (section.surface === "memory" && (
      isMemoryTransitioning() || hasPendingCharacterSelection()
    )) return;
    if (section.surface === "memory" && memoryActivityBlocksCollection(projectPluginActivity(plugin))) return;
    const state = pluginCollectionRuntimeState(plugin, section, collection);
    if (!runtimePluginController) return;
    if (state.loading) {
      state.queryPending = true;
      state.queryPendingRender ||= render;
      return;
    }
    const collectionKey = pluginCollectionKey(plugin, section, collection);
    const queryRevision = state.queryRevision;
    const querySearch = state.search;
    const queryFilters = clonePlain(state.filters);
    state.loading = true;
    state.error = "";
    if (render && !state.loaded) {
      if (section.surface === "memory") renderMemorySurface();
      else renderPluginPage();
    }
    try {
      const result = await runtimePluginController.collection({
        operation: "query",
        pluginId: plugin.plugin_id,
        sectionId: section.section_id,
        collectionId: collection.collection_id,
        cursor: append ? state.nextCursor : null,
        limit: collection.page_size,
        search: querySearch,
        filters: queryFilters,
      });
      if (pluginCollectionState.get(collectionKey) !== state) return;
      if (section.surface === "memory" && (isMemoryTransitioning())) return;
      if (queryRevision !== state.queryRevision) {
        state.queryPending = true;
        return;
      }
      state.items = append ? [...state.items, ...result.items] : result.items;
      state.nextCursor = result.nextCursor;
      state.total = result.total;
      state.loaded = true;
    } catch (error) {
      if (pluginCollectionState.get(collectionKey) !== state) return;
      if (queryRevision === state.queryRevision) state.error = String(error);
      else state.queryPending = true;
    } finally {
      if (pluginCollectionState.get(collectionKey) !== state) return;
      state.loading = false;
      if (section.surface === "memory" && (isMemoryTransitioning())) {
        state.queryPending = false;
        state.queryPendingRender = false;
        return;
      }
      if (state.queryPending) {
        const pendingRender = state.queryPendingRender;
        state.queryPending = false;
        state.queryPendingRender = false;
        setTimer(() => queryPluginCollection(
          plugin,
          section,
          collection,
          { render: pendingRender },
        ), 0);
        return;
      }
      if (render && section.surface === "memory") {
        const active = document.activeElement;
        const restoreFocus = active?.classList.contains("memory-search-input")
          && active.dataset.collectionKey === collectionKey;
        const selectionStart = restoreFocus ? active.selectionStart : null;
        const selectionEnd = restoreFocus ? active.selectionEnd : null;
        renderMemorySurface();
        if (restoreFocus) {
          setTimer(() => {
            const input = Array.from(fields.memorySurface.querySelectorAll(".memory-search-input"))
              .find((element) => element.dataset.collectionKey === collectionKey);
            input?.focus();
            if (input && selectionStart !== null && selectionEnd !== null) {
              input.setSelectionRange(selectionStart, selectionEnd);
            }
          }, 0);
        }
      } else if (render) {
        renderPluginPage();
      }
    }
  }

  function pluginCollectionFieldControl(field, value, onChange) {
    if (field.type === "boolean") {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      input.disabled = Boolean(field.readonly);
      input.addEventListener("change", () => onChange(input.checked));
      return input;
    }
    if (field.type === "select") {
      const select = document.createElement("select");
      (field.options || []).forEach((option) => {
        const item = document.createElement("option");
        item.value = String(option.value);
        item.textContent = option.label;
        select.append(item);
      });
      select.value = String(value ?? "");
      select.disabled = Boolean(field.readonly);
      select.addEventListener("change", () => {
        const option = (field.options || []).find((item) => String(item.value) === select.value);
        onChange(option ? option.value : select.value);
      });
      setTimer(() => enhanceSelect(select), 0);
      return select;
    }
    const input = field.type === "string" && !field.readonly
      ? document.createElement("textarea")
      : document.createElement("input");
    if (input.tagName === "INPUT") {
      input.type = ["integer", "number"].includes(field.type)
        ? "number" : field.type === "password" ? "password" : "text";
      if (Number.isSafeInteger(field.maxLength)) input.maxLength = field.maxLength;
    }
    input.value = String(value ?? "");
    input.disabled = Boolean(field.readonly);
    if (typeof field.minimum === "number") input.min = String(field.minimum);
    if (typeof field.maximum === "number") input.max = String(field.maximum);
    if (typeof field.step === "number") input.step = String(field.step);
    input.addEventListener("input", () => {
      if (field.type === "integer") onChange(Number.parseInt(input.value, 10));
      else if (field.type === "number") onChange(Number.parseFloat(input.value));
      else onChange(input.value);
    });
    return input;
  }

  async function mutatePluginCollection(plugin, section, collection, operation) {
    if (!pluginView.items.includes(plugin)) return;
    const state = pluginCollectionRuntimeState(plugin, section, collection);
    const collectionKey = pluginCollectionKey(plugin, section, collection);
    const isCurrent = () => pluginCollectionState.get(collectionKey) === state;
    const memorySurface = section.surface === "memory";
    if (!runtimePluginController || state.loading || !state.editor) return;
    if (memorySurface && (isMemoryTransitioning() || hasPendingCharacterSelection())) return;
    const editor = state.editor;
    if (operation !== "delete") {
      const invalid = (collection.fields || []).find((field) => {
        const value = state.editor.values[field.key];
        return field.required && (value === null || value === undefined || String(value).trim() === "");
      });
      if (invalid) {
        state.editorError = `请填写“${invalid.label}”。`;
        if (!memorySurface || !syncMemoryEditorPortalState(state)) {
          renderPluginPage();
          renderMemorySurface();
        }
        return;
      }
    }
    if (operation === "delete") {
      const confirmed = await confirmAction(collection.delete_confirmation, {
        title: "删除记忆",
        confirmText: "删除",
        cancelText: "保留",
        danger: true,
      });
      if (!confirmed || !isCurrent() || state.editor !== editor) return;
    }
    const editorItemId = state.editor.itemId;
    state.loading = true;
    state.operation = operation;
    state.error = "";
    state.editorError = "";
    if (memorySurface) {
      syncMemoryEditorPortalState(state);
    } else {
      renderPluginPage();
      renderMemorySurface();
    }
    let completed = false;
    try {
      let result;
      if (operation === "delete") {
        result = await runtimePluginController.collection({
          operation,
          pluginId: plugin.plugin_id,
          sectionId: section.section_id,
          collectionId: collection.collection_id,
          itemId: editorItemId,
        });
      } else {
        result = await runtimePluginController.collection({
          operation,
          pluginId: plugin.plugin_id,
          sectionId: section.section_id,
          collectionId: collection.collection_id,
          ...(operation === "update" ? { itemId: editorItemId } : {}),
          values: clonePlain(state.editor.values),
        });
      }
      if (!isCurrent()) return;
      const affectedItemId = operation === "delete" ? editorItemId : result.itemId;
      state.editor = null;
      state.selectedItemId = operation === "delete" ? "" : affectedItemId;
      state.loading = false;
      if (memorySurface) {
        applyMemoryCollectionMutationResult(state, collection, operation, result, affectedItemId);
        await queryPluginCollection(plugin, section, collection, { render: false });
        if (!isCurrent()) return;
        await dismissMemoryEditorPortal();
        if (!isCurrent()) return;
        if (operation === "delete") await animateMemoryRecordRemoval(affectedItemId);
        else state.motion = { kind: operation, itemId: affectedItemId };
        if (!isCurrent()) return;
        renderMemorySurface();
        completed = true;
        notify(operation === "delete" ? "记忆已删除。" : operation === "create" ? "记忆已新增。" : "记忆已更新。", "success");
      } else {
        state.loaded = false;
        await queryPluginCollection(plugin, section, collection);
        completed = true;
      }
    } catch (error) {
      if (!isCurrent()) return;
      state.error = String(error);
    } finally {
      if (!isCurrent()) return;
      state.loading = false;
      state.operation = "";
      if (memorySurface) {
        if (!completed && !syncMemoryEditorPortalState(state)) renderMemorySurface();
      } else {
        renderPluginPage();
        renderMemorySurface();
      }
      refreshDirty();
    }
  }

  function renderPluginCollection(plugin, section, collection) {
    const state = pluginCollectionRuntimeState(plugin, section, collection);
    const block = document.createElement("div");
    block.className = "plugin-collection";
    block.dataset.pluginCollection = collection.collection_id;
    const header = document.createElement("div");
    header.className = "plugin-collection-head";
    const heading = document.createElement("h4");
    heading.textContent = collection.title;
    header.append(heading);
    if (collection.can_create) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "secondary-button";
      add.textContent = "新增";
      add.addEventListener("click", () => {
        state.editor = {
          itemId: null,
          values: Object.fromEntries((collection.fields || [])
            .filter(pluginFieldEditable)
            .map((field) => [field.key, field.default])),
        };
        refreshDirty();
        renderPluginPage();
        renderMemorySurface();
      });
      header.append(add);
    }
    block.append(header);
    if (collection.description) {
      const description = document.createElement("p");
      description.className = "hint";
      description.textContent = collection.description;
      block.append(description);
    }
    const toolbar = document.createElement("div");
    toolbar.className = "plugin-collection-toolbar";
    if (collection.searchable) {
      const search = document.createElement("input");
      search.type = "search";
      search.placeholder = "搜索";
      search.value = state.search;
      search.addEventListener("change", () => {
        state.search = search.value.trim();
        queryPluginCollection(plugin, section, collection);
      });
      toolbar.append(search);
    }
    (collection.filters || []).forEach((filter) => {
      const select = document.createElement("select");
      const all = document.createElement("option");
      all.value = "";
      all.textContent = `全部${filter.label}`;
      select.append(all);
      filter.options.forEach((option) => {
        const item = document.createElement("option");
        item.value = String(option.value);
        item.textContent = option.label;
        select.append(item);
      });
      select.value = Object.hasOwn(state.filters, filter.key) ? String(state.filters[filter.key]) : "";
      select.addEventListener("change", () => {
        const selected = filter.options.find((option) => String(option.value) === select.value);
        if (selected) state.filters[filter.key] = selected.value;
        else delete state.filters[filter.key];
        queryPluginCollection(plugin, section, collection);
      });
      toolbar.append(select);
      setTimer(() => enhanceSelect(select), 0);
    });
    if (toolbar.children.length) block.append(toolbar);
    if (state.error) {
      const error = document.createElement("p");
      error.className = "error";
      error.textContent = state.error;
      block.append(error);
    }
    if (state.loading && !state.loaded) {
      const loading = document.createElement("p");
      loading.className = "page-note";
      loading.textContent = "正在加载…";
      block.append(loading);
    } else if (state.loaded && !state.items.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "暂无数据。";
      block.append(empty);
    } else if (state.items.length) {
      const table = document.createElement("table");
      table.className = "plugin-collection-table";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      collection.columns.forEach((column) => {
        const cell = document.createElement("th");
        cell.textContent = column.label;
        headRow.append(cell);
      });
      head.append(headRow);
      const body = document.createElement("tbody");
      state.items.forEach((item) => {
        const row = document.createElement("tr");
        collection.columns.forEach((column) => {
          const cell = document.createElement("td");
          const value = item.values[column.key];
          cell.textContent = column.type === "boolean" ? (value ? "是" : "否") : String(value ?? "");
          row.append(cell);
        });
        if (collection.can_update || collection.can_delete) {
          row.tabIndex = 0;
          row.addEventListener("click", () => {
            state.editor = {
              itemId: item.itemId,
              values: Object.fromEntries((collection.fields || [])
                .filter(pluginFieldEditable)
                .map((field) => [field.key, item.values[field.key] ?? field.default])),
            };
            refreshDirty();
            renderPluginPage();
            renderMemorySurface();
          });
        }
        body.append(row);
      });
      table.append(head, body);
      const scroll = document.createElement("div");
      scroll.className = "plugin-collection-scroll";
      scroll.append(table);
      block.append(scroll);
    }
    if (state.nextCursor) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "secondary-button";
      more.textContent = state.loading ? "加载中…" : "加载更多";
      more.disabled = state.loading;
      more.addEventListener("click", () => queryPluginCollection(plugin, section, collection, { append: true }));
      block.append(more);
    }
    if (state.editor) {
      const editor = document.createElement("div");
      editor.className = "plugin-collection-editor";
      (collection.fields || []).forEach((field) => {
        const row = document.createElement("div");
        row.className = "form-row";
        const label = document.createElement("label");
        label.textContent = field.label;
        const control = pluginCollectionFieldControl(
          field,
          state.editor.values[field.key] ?? field.default,
          (value) => { state.editor.values[field.key] = value; },
        );
        row.append(label, control);
        editor.append(row);
      });
      const actions = document.createElement("div");
      actions.className = "plugin-setting-actions";
      const save = document.createElement("button");
      save.type = "button";
      save.className = "secondary-button";
      save.textContent = state.editor.itemId ? "更新" : "创建";
      save.disabled = state.loading || (state.editor.itemId ? !collection.can_update : !collection.can_create);
      save.addEventListener("click", () => mutatePluginCollection(
        plugin, section, collection, state.editor.itemId ? "update" : "create",
      ));
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary-button";
      cancel.textContent = "取消";
      cancel.addEventListener("click", () => {
        state.editor = null;
        state.editorError = "";
        refreshDirty();
        renderPluginPage();
        renderMemorySurface();
      });
      actions.append(save, cancel);
      if (state.editor.itemId && collection.can_delete) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "danger-button";
        remove.textContent = "删除";
        remove.addEventListener("click", () => mutatePluginCollection(plugin, section, collection, "delete"));
        actions.append(remove);
      }
      editor.append(actions);
      block.append(editor);
    }
    if (!state.loaded && !state.loading && !state.error) {
      setTimer(() => queryPluginCollection(plugin, section, collection), 0);
    }
    return block;
  }

  function renderPluginSettings(plugin) {
    const allSections = pluginSettingsSections(plugin);
    const knownSurfaces = new Set(["memory", "voice"]);
    const sections = allSections.filter((section) => !knownSurfaces.has(section.surface));
    const container = document.createElement("div");
    container.className = "plugin-settings";
    if (!sections.length) {
      return container;
    }
    sections.forEach((section) => {
      const block = document.createElement("section");
      block.className = "plugin-settings-section";
      block.dataset.pluginSection = section.section_id;
      const header = document.createElement("div");
      header.className = "plugin-settings-section-head";
      const heading = document.createElement("h3");
      heading.textContent = section.title || section.section_id;
      header.append(heading);
      const headerStatusField = (section.fields || []).find(
        (field) => field.type === "status" && field.placement === "section_header",
      );
      if (headerStatusField) {
        const statusValue = pluginFieldValue(plugin, section, headerStatusField);
        const status = renderSemanticStatus(statusValue, "plugin-section-status");
        status.dataset.pluginLiveField = headerStatusField.key;
        header.append(status);
      }
      block.append(header);
      if (headerStatusField) {
        const status = pluginFieldValue(plugin, section, headerStatusField);
        const message = document.createElement("p");
        message.className = "plugin-status-message is-section-message";
        message.dataset.pluginStatusMessage = headerStatusField.key;
        message.textContent = status?.message || "";
        message.hidden = !status?.message || ["ready", "neutral"].includes(status.state);
        block.append(message);
      }
      if (section.error || (section.reason_code && section.reason_code !== "READY")) {
        const error = document.createElement("p");
        error.className = "error";
        const stableError = typeof section.error === "string"
          && /^[A-Z0-9_]{1,64}$/.test(section.error)
          ? section.error
          : "";
        const presentation = pluginPresentation.presentPluginReason(
          stableError || section.reason_code,
        );
        error.textContent = section.error && !stableError
          ? section.error
          : [presentation?.message, presentation?.diagnostic].filter(Boolean).join(" ");
        block.append(error);
      }
      const advanced = pluginNode("details", "voice-advanced-settings");
      advanced.append(pluginNode("summary", "", "高级设置"));
      const advancedBody = pluginNode("div", "plugin-settings-advanced-body"); advanced.append(advancedBody);
      const inputs = new Map();
      const conditional = [];
      (section.fields || []).filter((field) => field !== headerStatusField).forEach((field) => {
        const row = document.createElement("div");
        row.className = field.type === "resource" ? "plugin-resource-row" : "form-row";
        const control = pluginSettingControl(plugin, section, field);
        if (!pluginFieldEditable(field)) control.dataset.pluginLiveField = field.key;
        if (field.type !== "boolean" && field.description) {
          control.dataset.tooltip = field.description;
        }
        if (field.type === "resource") {
          row.append(control);
        } else {
          const label = document.createElement("label");
          label.textContent = field.label || field.key;
          const input = control.matches("input,select,output") ? control : control.querySelector("input,select,output");
          if (input) {
            input.id = `plugin-field-${plugin.id}-${section.section_id}-${field.key}`;
            label.htmlFor = input.id;
            inputs.set(field.key, input);
            if (field.enabledWhen) conditional.push({ field, input, row });
          }
          if (field.type === "boolean") row.append(control);
          else row.append(label, control);
          if (field.description) row.append(pluginNode("small", "hint", field.description));
        }
        (field.placement === "advanced" ? advancedBody : block).append(row);
      });
      if ((section.fields || []).some((field) => field.restart_required)) {
        block.append(pluginNode("p", "hint", "应用后重启插件。"));
      }
      const syncAvailability = () => {
        for (const { field, input, row } of conditional) {
          input.disabled = String(inputs.get(field.enabledWhen.field)?.value) !== field.enabledWhen.equals;
          row.classList.toggle("is-disabled", input.disabled); refreshSelect(input);
        }
      };
      block.addEventListener("input", syncAvailability); block.addEventListener("change", syncAvailability);
      syncAvailability();
      if (advancedBody.childElementCount) block.append(advanced);
      const embeddedActionIds = new Set(
        (section.fields || [])
          .filter((field) => field.type === "resource")
          .flatMap((field) => field.actionIds || []),
      );
      const standaloneActions = (section.actions || []).filter(
        (action) => !embeddedActionIds.has(action.action_id),
      );
      if (standaloneActions.length) {
        const actions = document.createElement("div");
        actions.className = "plugin-setting-actions";
        standaloneActions.forEach((action) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = action.danger ? "danger-button" : "secondary-button";
          button.textContent = action.label || action.action_id;
          const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
          button.dataset.pluginActionKey = busyKey;
          button.disabled = pluginState.managementBusy || pluginState.actionBusyKey === busyKey;
          button.addEventListener("click", () => runPluginSettingsAction(plugin, section, action));
          actions.append(button);
        });
        block.append(actions);
      }
      (section.collections || []).forEach((collection) => {
        block.append(renderPluginCollection(plugin, section, collection));
      });
      container.append(block);
    });
    return container;
  }

  function memorySurfaceIsTransitioning() {
    const snapshot = runtimePluginController?.snapshot();
    if (!snapshot) return false;
    if (["starting", "waiting"].includes(snapshot.state)) return true;
    if (snapshot.plugins.some((plugin) => plugin.enabled
        && ["starting", "waiting"].includes(plugin.state))) return true;
    return (pluginView?.items || []).some((plugin) => (
      pluginSettingsSections(plugin).some((section) => section.surface === "memory")
      && projectPluginActivity(plugin).state === "working"
    ));
  }

  function selectedPluginHasTransientActivity() {
    const plugin = (pluginView?.items || []).find((item) => item.id === pluginState.selectedId);
    return Boolean(plugin && projectPluginActivity(plugin).isTransient);
  }

  function pluginActivityPageVisible() {
    return fields.pages.memory.classList.contains("is-active")
      || fields.pages.plugins.classList.contains("is-active")
      || fields.pages.about.classList.contains("is-active");
  }

  function visiblePluginActivityIsTransient() {
    const snapshot = runtimePluginController?.snapshot();
    if (["starting", "waiting"].includes(snapshot?.state)) return pluginActivityPageVisible();
    if (fields.pages.memory.classList.contains("is-active")) return memorySurfaceIsTransitioning();
    if (fields.pages.plugins.classList.contains("is-active")) return selectedPluginHasTransientActivity();
    if (fields.pages.about.classList.contains("is-active")) return aboutComponentsRunning();
    return false;
  }

  function clearPluginActivityRefresh() {
    clearTimer(pluginActivityRefreshTimer);
    pluginActivityRefreshTimer = null;
  }

  function schedulePluginActivityRefresh() {
    clearPluginActivityRefresh();
    if (!runtimePluginController || !visiblePluginActivityIsTransient()) return;
    pluginActivityRefreshTimer = setTimer(refreshPluginActivityCurrent, 1200);
  }

  async function refreshPluginActivityCurrent() {
    if (pluginActivityRefreshInFlight || !runtimePluginController || !pluginActivityPageVisible()) return;
    pluginActivityRefreshInFlight = true;
    try {
      await runtimePluginController.refreshCurrent();
      aboutComponentsReadError = "";
    } catch {
      aboutComponentsReadError = "暂时无法读取组件状态，请稍后刷新。";
      renderAboutComponents();
    } finally {
      pluginActivityRefreshInFlight = false;
      renderAboutComponents();
    }
  }

  function memoryCollectionOptionLabel(collection, key, value) {
    const options = collection.filters?.find((filter) => filter.key === key)?.options
      || collection.fields?.find((field) => field.key === key)?.options
      || [];
    return options.find((option) => option.value === value)?.label || String(value || "未分类");
  }

  function formatMemoryTimestamp(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  function memoryEditorValues(collection, item = null) {
    return Object.fromEntries((collection.fields || [])
      .filter(pluginFieldEditable)
      .map((field) => [field.key, item?.values?.[field.key] ?? field.default ?? ""]));
  }

  function clearMemoryEditorPortal() {
    document.querySelectorAll(".memory-editor-overlay").forEach((overlay) => overlay.remove());
    document.querySelector(".settings-shell")?.removeAttribute("inert");
  }

  async function dismissMemoryEditorPortal() {
    const overlays = Array.from(document.querySelectorAll(".memory-editor-overlay"));
    if (!overlays.length) {
      document.querySelector(".settings-shell")?.removeAttribute("inert");
      return;
    }
    await Promise.all(overlays.map((overlay) => removeOverlayAfterExit(overlay)));
    if (!document.querySelector(".memory-editor-overlay")) {
      document.querySelector(".settings-shell")?.removeAttribute("inert");
    }
  }

  function mountMemoryEditorPortal(overlay) {
    clearMemoryEditorPortal();
    document.querySelector(".settings-shell")?.setAttribute("inert", "");
    document.body.append(overlay);
  }

  function syncMemoryEditorPortalState(state) {
    const overlay = document.querySelector(".memory-editor-overlay");
    const dialog = overlay?.querySelector(".memory-record-dialog");
    if (!overlay || !dialog) return false;
    const busy = Boolean(state.loading && state.operation);
    dialog.classList.toggle("is-busy", busy);
    dialog.setAttribute("aria-busy", String(busy));
    overlay.querySelectorAll("input, textarea, select, button").forEach((control) => {
      if (busy) {
        if (!control.hasAttribute("data-memory-disabled-before")) {
          control.dataset.memoryDisabledBefore = String(control.disabled);
        }
        control.disabled = true;
      } else if (control.hasAttribute("data-memory-disabled-before")) {
        control.disabled = control.dataset.memoryDisabledBefore === "true";
        delete control.dataset.memoryDisabledBefore;
      }
    });
    dialog.querySelectorAll("[data-memory-action]").forEach((action) => {
      const actionName = action.dataset.memoryAction;
      const isWorkingAction = busy && (
        (state.operation === "delete" && actionName === "delete")
        || (state.operation !== "delete" && actionName === "save")
      );
      action.classList.toggle("is-working", isWorkingAction);
      if (isWorkingAction) {
        action.textContent = state.operation === "delete"
          ? "删除中…" : state.operation === "create" ? "新增中…" : "保存中…";
      } else if (action.dataset.idleLabel) {
        action.textContent = action.dataset.idleLabel;
      }
    });
    const errorMessage = state.editorError || state.error;
    let error = dialog.querySelector(".memory-dialog-error");
    if (errorMessage) {
      if (!error) {
        error = document.createElement("p");
        error.className = "memory-dialog-error";
        error.setAttribute("role", "alert");
        dialog.insertBefore(error, dialog.querySelector(".memory-dialog-actions"));
      }
      error.textContent = errorMessage;
    } else {
      error?.remove();
    }
    return true;
  }

  function applyMemoryCollectionMutationResult(state, collection, operation, result, itemId) {
    if (operation === "delete") {
      state.items = state.items.filter((item) => item.itemId !== itemId);
      if (state.total !== null) state.total = Math.max(0, state.total - 1);
    } else {
      const item = result;
      const existingIndex = state.items.findIndex((entry) => entry.itemId === item.itemId);
      if (existingIndex >= 0) {
        state.items.splice(existingIndex, 1, item);
      } else {
        state.items.unshift(item);
        if (state.total !== null) state.total += 1;
        if (state.items.length > collection.page_size) state.items.length = collection.page_size;
      }
    }
    state.loaded = true;
  }

  function memoryRecordCardById(itemId) {
    return Array.from(fields.memorySurface.querySelectorAll(".memory-record-card"))
      .find((card) => card.dataset.itemId === itemId) || null;
  }

  async function animateMemoryRecordRemoval(itemId) {
    const card = memoryRecordCardById(itemId);
    if (!card) return;
    card.style.setProperty("--memory-record-height", `${card.getBoundingClientRect().height}px`);
    card.classList.add("is-removing");
    await new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimer(fallbackTimer);
        card.removeEventListener("animationend", onAnimationEnd);
        resolve();
      };
      const onAnimationEnd = (event) => {
        if (event.target === card) finish();
      };
      const fallbackTimer = setTimer(finish, 360);
      card.addEventListener("animationend", onAnimationEnd);
    });
  }

  function openMemoryCollectionEditor(plugin, section, collection, item = null) {
    const state = pluginCollectionRuntimeState(plugin, section, collection);
    state.editor = {
      itemId: item?.itemId || null,
      values: memoryEditorValues(collection, item),
    };
    state.editorError = "";
    state.selectedItemId = item?.itemId || "";
    refreshDirty();
    renderMemorySurface();
    setTimer(() => document.querySelector(
      ".memory-editor-overlay .memory-record-dialog textarea, .memory-editor-overlay .memory-record-dialog input",
    )?.focus(), 0);
  }

  function renderMemoryEditor(plugin, section, collection, state) {
    const overlay = document.createElement("div");
    overlay.className = "memory-editor-overlay";
    const dialog = document.createElement("section");
    dialog.className = "memory-record-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "memoryRecordDialogTitle");

    const head = document.createElement("header");
    head.className = "memory-dialog-head";
    const headingGroup = document.createElement("div");
    const heading = document.createElement("h2");
    heading.id = "memoryRecordDialogTitle";
    heading.textContent = state.editor.itemId ? "编辑记忆" : "新增记忆";
    headingGroup.append(heading);
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "memory-dialog-close";
    closeButton.setAttribute("aria-label", "关闭编辑器");
    closeButton.append(createIcon(document, "x"));
    head.append(headingGroup, closeButton);

    const form = document.createElement("div");
    form.className = "memory-dialog-form";
    (collection.fields || []).filter(pluginFieldEditable).forEach((field) => {
      const group = document.createElement("label");
      group.className = `memory-dialog-field${field.key === "content" ? " is-content" : ""}`;
      const label = document.createElement("span");
      label.className = "memory-dialog-label";
      label.textContent = field.required ? `${field.label} *` : field.label;
      let control;
      if (field.type === "select") {
        control = document.createElement("select");
        (field.options || []).forEach((option) => {
          const element = document.createElement("option");
          element.value = String(option.value);
          element.textContent = option.label;
          control.append(element);
        });
        control.value = String(state.editor.values[field.key] ?? field.default ?? "");
        control.addEventListener("change", () => {
          const option = (field.options || []).find((item) => String(item.value) === control.value);
          state.editor.values[field.key] = option ? option.value : control.value;
        });
        setTimer(() => enhanceSelect(control), 0);
      } else if (field.key === "content") {
        control = document.createElement("textarea");
        control.rows = 7;
        if (Number.isSafeInteger(field.maxLength)) control.maxLength = field.maxLength;
        control.value = String(state.editor.values[field.key] ?? "");
        control.placeholder = "例如：喜欢简洁的回答";
        control.addEventListener("input", () => {
          state.editor.values[field.key] = control.value;
          const counter = group.querySelector(".memory-character-count");
          if (counter) counter.textContent = `${control.value.length} / ${field.maxLength}`;
        });
      } else {
        control = document.createElement("input");
        control.type = ["integer", "number"].includes(field.type) ? "number" : "text";
        if (typeof field.minimum === "number") control.min = String(field.minimum);
        if (typeof field.maximum === "number") control.max = String(field.maximum);
        if (typeof field.step === "number") control.step = String(field.step);
        if (Number.isSafeInteger(field.maxLength)) control.maxLength = field.maxLength;
        control.value = String(state.editor.values[field.key] ?? "");
        control.addEventListener("input", () => {
          state.editor.values[field.key] = ["integer", "number"].includes(field.type)
            ? Number(control.value) : control.value;
        });
      }
      group.append(label, control);
      if (field.key === "content" && Number.isSafeInteger(field.maxLength)) {
        const counter = document.createElement("span");
        counter.className = "memory-character-count";
        counter.textContent = `${String(state.editor.values[field.key] ?? "").length} / ${field.maxLength}`;
        group.append(counter);
      } else if (field.description) {
        const description = document.createElement("small");
        description.textContent = field.description;
        group.append(description);
      }
      form.append(group);
    });

    const footer = document.createElement("footer");
    footer.className = "memory-dialog-actions";
    const utilityActions = document.createElement("div");
    if (state.editor.itemId && collection.can_delete) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger-button";
      remove.textContent = "删除记忆";
      remove.dataset.memoryAction = "delete";
      remove.dataset.idleLabel = "删除记忆";
      remove.disabled = state.loading;
      remove.addEventListener("click", () => mutatePluginCollection(plugin, section, collection, "delete"));
      utilityActions.append(remove);
    }
    const primaryActions = document.createElement("div");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = "取消";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = state.editor.itemId ? "保存修改" : "新增记忆";
    save.dataset.memoryAction = "save";
    save.dataset.idleLabel = save.textContent;
    save.disabled = state.loading || (state.editor.itemId ? !collection.can_update : !collection.can_create);
    save.addEventListener("click", () => mutatePluginCollection(
      plugin, section, collection, state.editor.itemId ? "update" : "create",
    ));
    primaryActions.append(cancel, save);
    footer.append(utilityActions, primaryActions);

    const close = async () => {
      if (state.loading) return;
      state.editor = null;
      state.editorError = "";
      refreshDirty();
      await dismissMemoryEditorPortal();
      renderMemorySurface();
    };
    cancel.addEventListener("click", () => { void close(); });
    closeButton.addEventListener("click", () => { void close(); });
    overlay.addEventListener("click", (event) => { if (event.target === overlay) void close(); });
    dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") void close(); });
    dialog.append(head, form);
    if (state.editorError || state.error) {
      const error = document.createElement("p");
      error.className = "memory-dialog-error";
      error.setAttribute("role", "alert");
      error.textContent = state.editorError || state.error;
      dialog.append(error);
    }
    dialog.append(footer);
    overlay.append(dialog);
    return overlay;
  }

  function createMemoryPreparingState() {
    const loading = document.createElement("div");
    loading.className = "memory-surface-state memory-preparing-state is-loading";
    loading.setAttribute("role", "status");
    loading.setAttribute("aria-live", "polite");
    loading.setAttribute("aria-busy", "true");
    loading.innerHTML = `
      <span class="memory-preparing-icon" aria-hidden="true">${animatedBrainMarkup()}</span>
      <strong class="memory-preparing-label">正在准备长期记忆</strong>
    `;
    return loading;
  }

  function memoryActivityBlocksCollection(activity) {
    return ["working", "warning", "error", "disabled", "failed"].includes(activity?.state);
  }

  function memoryActivityNeedsNotice(activity) {
    return ["warning", "error", "disabled", "failed"].includes(activity?.state);
  }

  function createMemoryActivityNotice(plugin, activity) {
    const pluginFailure = activity.state === "failed" ? pluginStatusCopy(plugin) : null;
    const notice = document.createElement("div");
    notice.className = `memory-surface-state memory-activity-notice is-${activity.state}`;
    notice.setAttribute("role", ["error", "failed"].includes(activity.state) ? "alert" : "status");
    if (activity.state === "warning") notice.setAttribute("aria-live", "polite");
    const heading = document.createElement("strong");
    heading.textContent = pluginFailure?.label || activity.label || (
      activity.state === "warning" ? "长期记忆功能受限" : "长期记忆暂不可用"
    );
    const message = document.createElement("p");
    message.textContent = activity.message || pluginFailure?.message || "";
    message.hidden = !message.textContent || message.textContent === heading.textContent;
    const actions = document.createElement("div");
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary-button";
    link.textContent = "前往插件页";
    link.addEventListener("click", () => {
      pluginState.selectedId = plugin.id;
      showPage("plugins");
      renderPluginPage();
    });
    actions.append(link);
    notice.append(heading, message, actions);
    return notice;
  }

  function renderMemoryPreparingArchive() {
    const archive = document.createElement("section");
    archive.className = "memory-archive is-preparing";
    const head = document.createElement("header");
    head.className = "memory-archive-head";
    const titleGroup = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = "记忆条目";
    titleGroup.append(title);
    const headActions = document.createElement("div");
    headActions.className = "memory-archive-head-actions";
    const count = document.createElement("span");
    count.className = "memory-result-count";
    count.textContent = "正在初始化";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "memory-add-button";
    add.append(createIcon(document, "plus"), "新增记忆");
    add.disabled = true;
    headActions.append(count, add);
    head.append(titleGroup, headActions);

    const toolbar = document.createElement("div");
    toolbar.className = "memory-archive-toolbar";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "memory-search-input";
    search.setAttribute("aria-label", "搜索记忆");
    search.placeholder = "搜索内容、分类或来源";
    search.disabled = true;
    const layer = document.createElement("select");
    layer.setAttribute("aria-label", "分层");
    layer.disabled = true;
    const allLayers = document.createElement("option");
    allLayers.textContent = "全部分层";
    layer.append(allLayers);
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "memory-refresh-button";
    refresh.textContent = "刷新";
    refresh.disabled = true;
    toolbar.append(search, layer, refresh);
    setTimer(() => enhanceSelect(layer), 0);

    const body = document.createElement("div");
    body.className = "memory-archive-list is-preparing";
    body.append(createMemoryPreparingState());
    archive.append(head, toolbar, body);
    return archive;
  }

  function renderMemoryCollection(plugin, section, collection) {
    const state = pluginCollectionRuntimeState(plugin, section, collection);
    const activity = projectPluginActivity(plugin);
    const initializing = activity.state === "working";
    const activityUnavailable = memoryActivityNeedsNotice(activity);
    const activityControlsDisabled = initializing || activityUnavailable;
    const motion = state.motion;
    const archive = document.createElement("section");
    archive.className = "memory-archive";
    archive.classList.toggle("is-preparing", initializing);

    const head = document.createElement("header");
    head.className = "memory-archive-head";
    const titleGroup = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = collection.title || section.title;
    titleGroup.append(title);
    const headActions = document.createElement("div");
    headActions.className = "memory-archive-head-actions";
    const count = document.createElement("span");
    count.className = "memory-result-count";
    count.textContent = initializing
      ? "正在初始化"
      : activityUnavailable
        ? activity.label || "暂不可用"
        : state.loaded ? `${state.total ?? state.items.length} 条记忆` : "正在读取";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "memory-add-button";
    add.append(createIcon(document, "plus"), "新增记忆");
    add.disabled = activityControlsDisabled || state.loading || !collection.can_create;
    add.addEventListener("click", () => openMemoryCollectionEditor(plugin, section, collection));
    headActions.append(count, add);
    head.append(titleGroup, headActions);

    const toolbar = document.createElement("div");
    toolbar.className = "memory-archive-toolbar";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "memory-search-input";
    search.dataset.collectionKey = pluginCollectionKey(plugin, section, collection);
    search.setAttribute("aria-label", "搜索记忆");
    search.placeholder = "搜索内容、分类或来源";
    search.value = state.search;
    search.disabled = activityControlsDisabled;
    search.addEventListener("input", () => {
      state.search = search.value.trim();
      state.queryRevision += 1;
      clearTimer(state.searchTimer);
      state.searchTimer = setTimer(() => queryPluginCollection(plugin, section, collection), 220);
    });
    toolbar.append(search);
    (collection.filters || []).forEach((filter) => {
      const select = document.createElement("select");
      select.setAttribute("aria-label", filter.label);
      const all = document.createElement("option");
      all.value = "";
      all.textContent = `全部${filter.label}`;
      select.append(all);
      filter.options.forEach((option) => {
        const item = document.createElement("option");
        item.value = String(option.value);
        item.textContent = option.label;
        select.append(item);
      });
      select.value = Object.hasOwn(state.filters, filter.key) ? String(state.filters[filter.key]) : "";
      select.disabled = activityControlsDisabled;
      select.addEventListener("change", () => {
        const option = filter.options.find((item) => String(item.value) === select.value);
        if (option) state.filters[filter.key] = option.value;
        else delete state.filters[filter.key];
        state.queryRevision += 1;
        queryPluginCollection(plugin, section, collection);
      });
      toolbar.append(select);
      setTimer(() => enhanceSelect(select), 0);
    });
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "memory-refresh-button";
    refresh.textContent = state.loading ? "刷新中…" : "刷新";
    refresh.disabled = activityControlsDisabled || state.loading;
    refresh.addEventListener("click", () => queryPluginCollection(plugin, section, collection));
    toolbar.append(refresh);

    const body = document.createElement("div");
    body.className = "memory-archive-list";
    if (initializing) {
      body.classList.add("is-preparing");
      body.append(createMemoryPreparingState());
    } else if (activityUnavailable) {
      body.classList.add("has-activity-notice");
      body.append(createMemoryActivityNotice(plugin, activity));
    } else if (state.error && !state.editor) {
      const error = document.createElement("p");
      error.className = "memory-surface-error";
      error.textContent = state.error;
      body.append(error);
    }
    if (!initializing && !activityUnavailable && state.loading && !state.loaded) {
      const loading = document.createElement("div");
      loading.className = "memory-surface-state is-loading";
      loading.innerHTML = '<span class="memory-state-orbit sakura-icon icon-loader-circle" aria-hidden="true"></span><strong>正在加载记忆</strong>';
      body.append(loading);
    } else if (!initializing && !activityUnavailable && state.loaded && !state.items.length) {
      const empty = document.createElement("div");
      empty.className = "memory-surface-state";
      const mark = document.createElement("span");
      mark.className = "memory-empty-mark";
      mark.append(createIcon(document, "brain"));
      const heading = document.createElement("strong");
      heading.textContent = state.search || Object.keys(state.filters).length ? "无匹配记忆" : "暂无记忆";
      empty.append(mark, heading);
      body.append(empty);
    } else if (!initializing && !activityUnavailable) {
      state.items.forEach((item) => {
        const values = item.values || {};
        const card = document.createElement("article");
        card.className = "memory-record-card";
        card.dataset.itemId = item.itemId;
        card.tabIndex = 0;
        card.classList.toggle("is-selected", state.selectedItemId === item.itemId);
        if (motion?.itemId === item.itemId) {
          card.classList.add(motion.kind === "create" ? "is-entering" : "is-updated");
        }
        card.setAttribute("aria-label", `记忆：${String(values.content || "空内容").slice(0, 80)}`);
        card.addEventListener("click", () => {
          state.selectedItemId = item.itemId;
          fields.memorySurface.querySelectorAll(".memory-record-card.is-selected")
            .forEach((element) => element.classList.remove("is-selected"));
          card.classList.add("is-selected");
        });
        card.addEventListener("dblclick", () => openMemoryCollectionEditor(plugin, section, collection, item));
        card.addEventListener("keydown", (event) => {
          if (event.key === "Enter") openMemoryCollectionEditor(plugin, section, collection, item);
        });
        const main = document.createElement("div");
        main.className = "memory-record-main";
        const content = document.createElement("p");
        content.className = "memory-record-content";
        content.textContent = String(values.content || "（空记忆）");
        const meta = document.createElement("div");
        meta.className = "memory-record-meta";
        [
          memoryCollectionOptionLabel(collection, "layer", values.layer),
          values.category || "未分类",
          values.source || "未知来源",
          formatMemoryTimestamp(values.updatedAt),
        ].filter(Boolean).forEach((text, index) => {
          const itemMeta = document.createElement("span");
          itemMeta.className = index === 0 ? "memory-layer-chip" : "";
          itemMeta.textContent = text;
          meta.append(itemMeta);
        });
        main.append(content, meta);
        const aside = document.createElement("div");
        aside.className = "memory-record-aside";
        const scores = document.createElement("div");
        scores.className = "memory-score-row";
        [["重要", values.importance], ["置信", values.confidence]].forEach(([label, value]) => {
          const score = document.createElement("span");
          score.textContent = `${label} ${Math.round(Number(value ?? 0) * 100)}`;
          scores.append(score);
        });
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "memory-card-edit";
        edit.textContent = "编辑";
        edit.addEventListener("click", (event) => {
          event.stopPropagation();
          openMemoryCollectionEditor(plugin, section, collection, item);
        });
        aside.append(scores, edit);
        card.append(main, aside);
        body.append(card);
      });
    }
    state.motion = null;
    if (!activityControlsDisabled && state.nextCursor) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "secondary-button memory-load-more";
      more.textContent = state.loading ? "加载中…" : "加载更多";
      more.disabled = state.loading;
      more.addEventListener("click", () => queryPluginCollection(plugin, section, collection, { append: true }));
      body.append(more);
    }
    archive.append(head, toolbar, body);
    // 编辑器属于整个设置窗口，而不是记忆页。挂到 body 可避开页面切换动画建立的
    // containing block，确保 fixed 遮罩覆盖导航、内容和底栏。
    if (!activityControlsDisabled && state.editor) {
      mountMemoryEditorPortal(renderMemoryEditor(plugin, section, collection, state));
      syncMemoryEditorPortalState(state);
    }
    if (!activityControlsDisabled && !state.loaded && !state.loading && !state.error) {
      setTimer(() => queryPluginCollection(plugin, section, collection), 0);
    }
    return archive;
  }

  function renderMemorySurface() {
    if (!fields.memorySurface) return;
    clearMemoryEditorPortal();
    fields.memorySurface.textContent = "";
    if (isMemoryTransitioning()) {
      const switching = document.createElement("div");
      switching.className = "memory-surface-state";
      switching.setAttribute("role", "status");
      switching.textContent = "正在切换角色…";
      fields.memorySurface.append(switching);
      return;
    }
    const contributions = [];
    (pluginView?.items || []).forEach((plugin) => {
      pluginSettingsSections(plugin)
        .filter((section) => section.surface === "memory")
        .forEach((section) => contributions.push({ plugin, section }));
    });
    if (!contributions.length) {
      if (memorySurfaceIsTransitioning()) {
        fields.memorySurface.append(renderMemoryPreparingArchive());
        schedulePluginActivityRefresh();
        return;
      }
      const empty = document.createElement("div");
      empty.className = "memory-surface-state memory-surface-unavailable";
      const mark = document.createElement("span");
      mark.className = "memory-empty-mark";
      mark.append(createIcon(document, "brain"));
      const heading = document.createElement("strong");
      heading.textContent = "记忆管理暂不可用";
      const message = document.createElement("p");
      message.textContent = "请确认记忆插件已安装并启用。";
      const actions = document.createElement("div");
      const refresh = document.createElement("button");
      refresh.type = "button";
      refresh.className = "secondary-button";
      refresh.textContent = "重新检查";
      refresh.disabled = pluginActivityRefreshInFlight;
      refresh.addEventListener("click", refreshPluginActivityCurrent);
      const link = document.createElement("button");
      link.type = "button";
      link.className = "secondary-button";
      link.textContent = "前往插件页";
      link.addEventListener("click", () => showPage("plugins"));
      actions.append(refresh, link);
      empty.append(mark, heading, message, actions);
      fields.memorySurface.append(empty);
      schedulePluginActivityRefresh();
      return;
    }
    contributions.forEach(({ plugin, section }) => {
      (section.collections || []).forEach((collection) => {
        fields.memorySurface.append(renderMemoryCollection(plugin, section, collection));
      });
    });
    schedulePluginActivityRefresh();
  }

  async function runPluginSettingsAction(plugin, section, action, focusResourceKey = "") {
    if (pluginState.managementBusy) return;
    if (pluginState.actionBusyKey) return;
    if (pluginSettingsDialog?.installId === plugin.id) {
      pluginSettingsDialog.error.textContent = "";
      pluginSettingsDialog.error.hidden = true;
    }
    const restoreAboutResourceKey = focusResourceKey
      || document.activeElement?.dataset?.aboutResourceKey
      || "";
    const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
    pluginState.actionBusyKey = busyKey;
    renderPluginPage();
    renderAboutComponents();
    setError("");
    try {
      const result = await runtimePluginController.action({
        pluginId: plugin.plugin_id,
        sectionId: section.section_id,
        actionId: action.action_id,
        values: clonePlain(editablePluginSectionValues(
          section,
          pluginSectionValues(plugin.id, section.section_id),
        )),
      });
      if (result && typeof result.values === "object" && result.values !== null) {
        pluginState.settingsValues[plugin.id][section.section_id] = {
          ...pluginState.settingsValues[plugin.id][section.section_id],
          ...result.values,
        };
        refreshDirty();
      }
      if (result && result.message) {
        notify(String(result.message), "success");
      }
      if (runtimePluginController) {
        await runtimePluginController.refreshCurrent();
      }
    } catch (error) {
      setError(String(error));
      if (pluginSettingsDialog?.installId === plugin.id) {
        pluginSettingsDialog.error.textContent = String(error);
        pluginSettingsDialog.error.hidden = false;
      }
    } finally {
      pluginState.actionBusyKey = "";
      renderPluginPage();
      renderAboutComponents({ restoreResourceKey: restoreAboutResourceKey });
      if (restoreAboutResourceKey && pluginSettingsDialog?.installId === plugin.id) {
        pluginSettingsDialog.general.querySelector(`[data-about-resource-key="${CSS.escape(restoreAboutResourceKey)}"]:not(:disabled)`)?.focus({ preventScroll: true });
      }
    }
  }

  function renderPluginDetail() {
    const plugin = (pluginView.items || []).find((item) => item.id === pluginState.selectedId);
    const previousId = fields.pluginDetail.dataset.pluginId;
    const previousToggle = fields.pluginDetail.querySelector('.plugin-enable-switch');
    fields.pluginDetail.textContent = '';
    fields.pluginDetail.dataset.pluginId = plugin?.id || '';
    if (!plugin) {
      fields.pluginDetail.append(pluginNode('p', 'empty-state', '选择一个插件查看详情。'));
      return;
    }
    const metadata = pluginPresentation.pluginMetadata(plugin);
    const status = pluginLiveStatus(plugin);
    const heading = pluginNode('header', 'plugin-detail-heading');
    const identity = pluginNode('div', 'plugin-detail-identity');
    const title = pluginNode('div', 'plugin-detail-title');
    title.append(pluginNode('h2', '', plugin.name || plugin.id), pluginNode('p', 'plugin-detail-tags', `${pluginPresentation.pluginCategories[metadata.category]} · ${plugin.source === 'user' ? '用户安装' : '内置'}`));
    identity.append(pluginIcon(plugin), title);
    const aside = pluginNode('div', 'plugin-detail-aside');
    if (pluginSettingsSections(plugin).some((section) => section.surface !== 'memory')
        || getVoiceController()?.hasPluginSections(plugin.plugin_id)
        || getAsrController()?.hasPluginControls(plugin.plugin_id)) {
      const configure = pluginNode('button', 'secondary-button plugin-configure', '插件设置');
      configure.prepend(createIcon(document, 'settings'));
      configure.type = 'button'; configure.setAttribute('aria-haspopup', 'dialog');
      configure.addEventListener('click', () => openPluginSettingsDialog(plugin)); aside.append(configure);
    }
    heading.append(identity);
    if (aside.childElementCount) heading.append(aside);
    fields.pluginDetail.append(heading);
    if (plugin.description) fields.pluginDetail.append(pluginNode('p', 'detail-desc', plugin.description));
    if (status.message || status.diagnostic) {
      const notice = pluginNode('div', 'plugin-health-notice');
      if (status.message) notice.append(pluginNode('p', '', status.message));
      if (status.diagnostic) notice.append(pluginNode('p', 'plugin-health-notice__diagnostic', status.diagnostic));
      fields.pluginDetail.append(notice);
    }
    const enableRow = pluginNode('section', 'plugin-enable-row');
    const enableCopy = pluginNode('div', '');
    enableCopy.append(pluginNode('h3', '', '启用插件'));
    if (plugin.required) enableCopy.append(pluginNode('p', '', 'Sakura 必需组件'));
    const enabled = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
    const enableControls = pluginNode('div', 'plugin-enable-controls');
    enableControls.append(pluginNode('span', 'plugin-enable-label', enabled ? '已启用' : '已停用'));
    const switchLabel = previousId === plugin.id && previousToggle ? previousToggle : pluginNode('label', 'plugin-enable-switch');
    if (!switchLabel.firstChild) {
      const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.setAttribute('role', 'switch');
      toggle.dataset.pluginInstallId = plugin.id; toggle.setAttribute('aria-label', `启用 ${plugin.name || plugin.id}`);
      toggle.addEventListener('change', () => {
        const current = pluginView.items.find((item) => item.id === plugin.id);
        if (current) void setPluginEnabled(current, toggle.checked);
      });
      const track = pluginNode('span', 'plugin-enable-switch__track'); track.setAttribute('aria-hidden', 'true'); switchLabel.append(toggle, track);
    }
    const toggle = switchLabel.querySelector('input'); toggle.checked = enabled;
    toggle.disabled = Boolean(plugin.required || pluginState.managementBusy || !plugin.plugin_id || plugin.reason_code === 'PLUGIN_ID_CONFLICT' || !plugin.supported);
    enableControls.append(switchLabel); enableRow.append(enableCopy, enableControls); fields.pluginDetail.append(enableRow);
    const providers = pluginPresentation.requiredPluginProviders(plugin, pluginView.items);
    if (providers.length) {
      const section = pluginNode('section', 'plugin-dependencies'); section.append(pluginNode('h3', '', `依赖组件 ${providers.length}`));
      for (const provider of providers) {
        const dependency = pluginNode('button', 'plugin-dependency'); dependency.type = 'button';
        dependency.append(pluginNode('span', '', provider.name), renderSemanticStatus(pluginLiveStatus(provider)));
        dependency.addEventListener('click', () => selectManagedPlugin(provider.id, { reveal: true })); section.append(dependency);
      }
      fields.pluginDetail.append(section);
    }
    const meta = pluginNode('dl', 'detail-meta');
    for (const [label, value] of [['版本', plugin.version], ['作者', plugin.author || '未知'], ['安装来源', plugin.source === 'user' ? '用户安装' : 'Sakura 内置'], ['插件角色', pluginPresentation.pluginKinds[metadata.kind]]]) {
      meta.append(pluginNode('dt', '', label), pluginNode('dd', '', value));
    }
    fields.pluginDetail.append(meta);
    const technical = pluginNode('details', 'plugin-technical'); technical.append(pluginNode('summary', '', '技术信息'));
    for (const [label, values] of [['插件 ID', [plugin.plugin_id || plugin.id]], ['提供的服务', plugin.provides || []], ['依赖的服务', plugin.requires || []]]) {
      if (values.length) technical.append(pluginNode('p', '', `${label}：${values.join('、')}`));
    }
    fields.pluginDetail.append(technical);
    if (plugin.can_uninstall) {
      const uninstall = pluginNode('button', 'danger-button', pluginState.managementBusy ? '卸载中…' : '卸载插件');
      uninstall.type = 'button'; uninstall.disabled = pluginState.managementBusy;
      uninstall.addEventListener('click', () => uninstallLocalPlugin(plugin));
      fields.pluginDetail.append(uninstall);
    }
    if (previousId !== plugin.id) replayMotion(fields.pluginDetail, 'plugin-detail-enter');
  }

  function renderPluginPage() {
    fields.pluginInstallMenuButton.disabled = pluginState.managementBusy || !runtimePluginController;
    fields.pluginInstallZipButton.disabled = pluginState.managementBusy || !runtimePluginController;
    fields.pluginInstallFolderButton.disabled = pluginState.managementBusy || !runtimePluginController;
    if (fields.pluginInstallMenuButton.disabled) setPluginInstallMenuOpen(false);
    renderPluginList();
    renderPluginDetail();
    syncPluginSettingsDialog();
    schedulePluginActivityRefresh();
  }

  async function installLocalPlugin(sourceKind) {
    if (!runtimePluginController || pluginState.managementBusy) return;
    let installedId = '';
    pluginState.managementBusy = true;
    setError("");
    renderPluginPage();
    try {
      const result = await runtimePluginController.install(sourceKind);
      if (!result) return;
      installedId = result.installId;
      notify("插件已安装。", "success");
    } catch (error) {
      setError(String(error));
    } finally {
      pluginState.managementBusy = false;
      if (installedId) selectManagedPlugin(installedId, { reveal: true });
      else renderPluginPage();
    }
  }

  async function uninstallLocalPlugin(plugin) {
    if (!runtimePluginController || pluginState.managementBusy || !plugin?.can_uninstall) return;
    const confirmed = await confirmAction(
      `卸载“${plugin.name || plugin.id}”？插件设置和数据会保留。`,
      { title: "卸载插件", confirmText: "卸载", cancelText: "取消", danger: true },
    );
    if (!confirmed) return;
    pluginState.managementBusy = true;
    setError("");
    renderPluginPage();
    try {
      await runtimePluginController.uninstall(plugin.install_id);
      notify("插件已卸载，设置和数据已保留。", "success");
    } catch (error) {
      setError(String(error));
    } finally {
      pluginState.managementBusy = false;
      renderPluginPage();
    }
  }

  function editablePluginSectionValues(section, values) {
    return Object.fromEntries((section.fields || [])
      .filter((field) => pluginFieldEditable(field) && Object.hasOwn(values, field.key))
      .map((field) => [field.key, values[field.key]]));
  }

  function collectPluginSettings() {
    const enabledById = {};
    const settingsById = {};
    (pluginView?.items || []).forEach((plugin) => {
      const enabled = plugin.required ? true : Boolean(pluginState.enabledById[plugin.id]);
      if (enabled !== pluginState.initialEnabledById[plugin.id]) {
        if (plugin.plugin_id) enabledById[plugin.plugin_id] = enabled;
      }
      const sections = pluginSettingsSections(plugin);
      if (sections.length) {
        sections.forEach((section) => {
          const values = clonePlain(editablePluginSectionValues(
            section,
            pluginSectionValues(plugin.id, section.section_id),
          ));
          const initial = editablePluginSectionValues(
            section,
            pluginState.initialSettingsValues[plugin.id]?.[section.section_id] || {},
          );
          if (!plainEqual(values, initial)) {
            if (!plugin.plugin_id) return;
            settingsById[plugin.plugin_id] = settingsById[plugin.plugin_id] || {};
            settingsById[plugin.plugin_id][section.section_id] = values;
          }
        });
      }
    });
    return { enabled_by_id: enabledById, settings_by_id: settingsById };
  }

  function runtimePluginDraft() {
    const legacy = collectPluginSettings();
    return { enabledById: legacy.enabled_by_id, settingsById: legacy.settings_by_id };
  }

  function applyRuntimePluginSnapshot(snapshot, { preserveDraft = false, draft = null } = {}) {
    void getAsrController()?.refresh({ preserveDraft: true });
    pluginView = {
      permission_labels: pluginView?.permission_labels || {},
      items: snapshot.plugins.map((plugin) => ({
        id: plugin.installId,
        install_id: plugin.installId,
        plugin_id: plugin.pluginId,
        name: plugin.name,
        version: plugin.version,
        author: plugin.author,
        description: plugin.description,
        presentation: plugin.presentation,
        enabled: plugin.enabled,
        required: plugin.required,
        supported: plugin.supported,
        source: plugin.source,
        can_uninstall: plugin.canUninstall,
        provides: clonePlain(plugin.provides),
        requires: clonePlain(plugin.requires),
        missing_services: clonePlain(plugin.missingServices),
        state: plugin.state,
        reason_code: plugin.reasonCode,
        settings: plugin.sections.map((section) => ({
          section_id: section.sectionId,
          title: section.title,
          surface: section.surface,
          reason_code: section.reasonCode,
          fields: (section.fields || []).map((field) => ({
            ...field,
            restart_required: field.restartRequired,
          })),
          values: clonePlain(section.values),
          actions: (section.actions || []).map((action) => ({
            action_id: action.actionId,
            label: action.label,
            description: action.description,
            danger: action.danger,
          })),
          collections: (section.collections || []).map((collection) => ({
            collection_id: collection.collectionId,
            title: collection.title,
            description: collection.description,
            columns: clonePlain(collection.columns),
            fields: (collection.fields || []).map((field) => ({
              ...field,
              restart_required: field.restartRequired,
            })),
            filters: clonePlain(collection.filters),
            searchable: collection.searchable,
            page_size: collection.pageSize,
            can_create: collection.canCreate,
            can_update: collection.canUpdate,
            can_delete: collection.canDelete,
            delete_confirmation: collection.deleteConfirmation,
          })),
        })),
      })),
    };
    const previousCollections = new Map(pluginCollectionState);
    previousCollections.forEach((state) => clearTimer(state.searchTimer));
    pluginCollectionState.clear();
    if (preserveDraft) {
      for (const plugin of pluginView.items) {
        for (const section of plugin.settings) {
          for (const collection of section.collections) {
            const key = pluginCollectionKey(plugin, section, collection);
            const previous = previousCollections.get(key);
            if (!previous) continue;
            // A new state object detaches old queries while keeping the editor and filters.
            const current = pluginCollectionRuntimeState(plugin, section, collection);
            current.editor = previous.editor ? clonePlain(previous.editor) : null;
            current.editorError = previous.editorError;
            current.selectedItemId = previous.selectedItemId;
            current.search = previous.search;
            current.filters = clonePlain(previous.filters);
          }
        }
      }
    }
    initializePluginState();
    if (preserveDraft && draft) {
      Object.entries(draft.enabledById || {}).forEach(([id, enabled]) => {
        const plugin = pluginView.items.find((item) => item.plugin_id === id);
        if (plugin && Object.hasOwn(pluginState.enabledById, plugin.id)) {
          pluginState.enabledById[plugin.id] = Boolean(enabled);
        }
      });
      Object.entries(draft.settingsById || {}).forEach(([id, sections]) => {
        const plugin = pluginView.items.find((item) => item.plugin_id === id);
        if (!plugin || !pluginState.settingsValues[plugin.id]) return;
        Object.entries(sections || {}).forEach(([sectionId, values]) => {
          if (pluginState.settingsValues[plugin.id][sectionId]) {
            pluginState.settingsValues[plugin.id][sectionId] = clonePlain(values);
          }
        });
      });
    }
    renderPluginPage();
    renderMemorySurface();
    renderAboutComponents();
  }

  const runtimePluginController = createPluginController({
    invoke,
    applySnapshot: applyRuntimePluginSnapshot,
    readDraft: runtimePluginDraft,
    onDirty: refreshDirty,
  });

  listen(fields.aboutComponentsRefresh, "click", () => {
    aboutComponentsReadError = "";
    void refreshPluginActivityCurrent();
  });
  listen(fields.pluginSearch, "input", () => {
    pluginState.selectedId = "";
    renderPluginPage();
    fields.pluginList.scrollTop = 0;
  });
  listen(document, "keydown", (event) => {
    if (!fields.pages.plugins.classList.contains("is-active") || pluginSettingsDialog) return;
    if (event.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) {
      event.preventDefault();
      fields.pluginSearch.focus();
    }
  });
  listen(fields.pluginInstallMenuButton, "click", () => {
    setPluginInstallMenuOpen(fields.pluginInstallMenu.hidden);
  });
  listen(fields.pluginInstallMenuButton, "keydown", (event) => {
    if (event.key === "Escape" && !fields.pluginInstallMenu.hidden) {
      event.preventDefault();
      setPluginInstallMenuOpen(false, { restoreFocus: true });
    } else if (["ArrowDown", "Enter", " "].includes(event.key) && fields.pluginInstallMenu.hidden) {
      event.preventDefault();
      setPluginInstallMenuOpen(true, { focusItem: true });
    }
  });
  listen(fields.pluginInstallMenu, "keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setPluginInstallMenuOpen(false, { restoreFocus: true });
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      movePluginInstallMenuFocus(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      movePluginInstallMenuFocus(-1);
    } else if (event.key === "Home") {
      event.preventDefault();
      pluginInstallMenuItems()[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      pluginInstallMenuItems().at(-1)?.focus();
    }
  });
  listen(document, "pointerdown", (event) => {
    if (!fields.pluginInstallMenu.hidden && !fields.pluginInstallMenuRoot.contains(event.target)) {
      setPluginInstallMenuOpen(false);
    }
  });
  listen(document, "focusin", (event) => {
    if (!fields.pluginInstallMenu.hidden && !fields.pluginInstallMenuRoot.contains(event.target)) {
      setPluginInstallMenuOpen(false);
    }
  });
  listen(fields.pluginInstallZipButton, "click", () => {
    setPluginInstallMenuOpen(false);
    installLocalPlugin("zip");
  });
  listen(fields.pluginInstallFolderButton, "click", () => {
    setPluginInstallMenuOpen(false);
    installLocalPlugin("folder");
  });

  function pluginFilters() {
    return { query: fields.pluginSearch.value, kind: pluginState.role };
  }

  function pluginNode(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function pluginIcon(plugin) {
    const { category } = pluginPresentation.pluginMetadata(plugin);
    const icon = pluginNode('span', `plugin-avatar category-${category}`);
    icon.setAttribute('aria-hidden', 'true');
    icon.append(createIcon(document, pluginPresentation.pluginIconName(plugin)));
    return icon;
  }

  function pluginLiveStatus(plugin) {
    const activity = projectPluginActivity(plugin);
    const status = pluginStatusCopy(plugin);
    return {
      state: plugin.state === 'disabled' ? 'neutral' : pluginHasExceptionalStatus(plugin) ? 'error'
        : activity.state === 'failed' ? 'error' : activity.state === 'disabled' ? 'neutral'
          : activity.state === 'neutral' && !activity.label && plugin.state === 'active' ? 'ready' : activity.state,
      label: activity.label || status.label,
      message: activity.message || status.message,
      diagnostic: status.diagnostic,
    };
  }

  function clearPluginFilters() {
    pluginState.role = 'all';
    fields.pluginSearch.value = '';
  }

  function selectManagedPlugin(id, { reveal = false } = {}) {
    const plugin = pluginView.items.find((item) => item.id === id);
    if (!plugin) return;
    if (reveal) clearPluginFilters();
    pluginState.selectedId = id;
    renderPluginPage();
    fields.pluginDetail.scrollTop = 0;
    const card = fields.pluginList.querySelector(`[data-plugin-install-id="${CSS.escape(id)}"]`);
    card?.focus({ preventScroll: true });
    if (reveal) card?.scrollIntoView({ block: 'nearest' });
  }

  function pluginDialogSchema(plugin) {
    return JSON.stringify(pluginSettingsSections(plugin).filter((section) => section.surface !== 'memory')
      .map((section) => [section.section_id, section.surface, section.fields.map((field) => [field.key, field.type, field.readonly])]));
  }

  function syncPluginSettingsDialog() {
    const editor = pluginSettingsDialog;
    if (!editor || editor.closing) return;
    const plugin = pluginView.items.find((item) => item.id === editor.installId);
    if (!plugin || runtimePluginController?.snapshot()?.coreGenerationId !== editor.generation
        || pluginDialogSchema(plugin) !== editor.schema) {
      void editor.close(false, false);
      notify('插件状态已变化，请重新打开设置。', 'info');
      return;
    }
    const focused = document.activeElement;
    const focusKey = focused?.dataset?.aboutActionKey;
    const focusedResourceKey = focused?.dataset?.aboutResourceKey;
    for (const current of editor.general.querySelectorAll('[data-plugin-live-field]')) {
      const sectionId = current.closest('[data-plugin-section]')?.dataset.pluginSection;
      const section = pluginSettingsSections(plugin).find((section) => section.section_id === sectionId);
      const field = section?.fields.find((field) => field.key === current.dataset.pluginLiveField);
      if (!field) continue;
      const value = pluginFieldValue(plugin, section, field);
      const signature = JSON.stringify([value, pluginState.actionBusyKey, pluginState.managementBusy]);
      if (current.dataset.liveSignature === signature) continue;
      const next = field.placement === 'section_header' ? renderSemanticStatus(value, 'plugin-section-status')
        : field.type === 'resource' ? pluginResourceControl(plugin, section, field, value || {}, { focusActions: true })
          : pluginSettingControl(plugin, section, field);
      next.dataset.pluginLiveField = field.key; next.dataset.liveSignature = signature;
      if (field.placement === 'section_header') {
        const message = current.closest('[data-plugin-section]')?.querySelector('[data-plugin-status-message]');
        if (message) {
          message.textContent = value?.message || '';
          message.hidden = !value?.message || ['ready', 'neutral'].includes(value.state);
        }
      }
      const inputId = current.id || current.querySelector('input,select,output')?.id;
      const nextInput = next.matches('input,select,output') ? next : next.querySelector('input,select,output');
      if (inputId && nextInput) nextInput.id = inputId;
      current.replaceWith(next);
    }
    for (const button of editor.general.querySelectorAll('[data-plugin-action-key]')) {
      button.disabled = pluginState.managementBusy || Boolean(pluginState.actionBusyKey);
    }
    for (const current of editor.general.querySelectorAll('[data-plugin-collection]')) {
      const sectionId = current.closest('[data-plugin-section]')?.dataset.pluginSection;
      const section = pluginSettingsSections(plugin).find((item) => item.section_id === sectionId);
      const collection = section?.collections.find((item) => item.collection_id === current.dataset.pluginCollection);
      if (!collection) continue;
      // Collection actions already own their drafts and save timing; keep their existing renderer.
      closeSelects(current);
      current.replaceWith(renderPluginCollection(plugin, section, collection));
    }
    if (focusKey) editor.general.querySelector(`[data-about-action-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    else if (focusedResourceKey) editor.general.querySelector(`[data-about-resource-key="${CSS.escape(focusedResourceKey)}"]`)?.focus({ preventScroll: true });
  }

  function openPluginSettingsDialog(plugin, { sectionId = "", fieldKey = "" } = {}) {
    if (pluginSettingsDialog) return;
    closeSelects();
    setPluginInstallMenuOpen(false);
    const dialog = pluginNode('dialog', 'plugin-settings-dialog');
    dialog.dataset.submissionLock = '';
    const form = document.createElement('form'); form.noValidate = true;
    const header = pluginNode('header', 'plugin-dialog-header');
    const identity = pluginNode('div', 'plugin-detail-identity');
    const title = pluginNode('div', '');
    title.append(pluginNode('p', 'plugin-dialog-eyebrow', '插件设置'), pluginNode('h2', '', plugin.name));
    title.lastElementChild.id = 'pluginSettingsDialogTitle'; dialog.setAttribute('aria-labelledby', 'pluginSettingsDialogTitle');
    identity.append(pluginIcon(plugin), title);
    const close = pluginNode('button', 'plugin-dialog-close'); close.append(createIcon(document, 'x')); close.type = 'button'; close.setAttribute('aria-label', '关闭插件设置');
    header.append(identity, close);
    const body = pluginNode('div', 'plugin-dialog-body');
    const general = renderPluginSettings(plugin); const voice = pluginNode('div', 'plugin-dialog-voice');
    const asr = pluginNode('div', 'plugin-dialog-asr');
    body.append(general, voice, asr);
    const error = pluginNode('p', 'error plugin-dialog-error'); error.hidden = true; error.setAttribute('role', 'alert');
    const footer = pluginNode('footer', 'plugin-dialog-footer');
    footer.append(pluginNode('span', '', '应用设置后生效'));
    const actions = pluginNode('div', '');
    const cancel = pluginNode('button', 'secondary-button', '取消'); cancel.type = 'button';
    const done = pluginNode('button', '', '完成'); done.type = 'submit'; actions.append(cancel, done); footer.append(actions);
    form.append(header, body, error, footer); dialog.append(form);
    const editor = {
      dialog, general, voice, asr, installId: plugin.id, generation: runtimePluginController?.snapshot()?.coreGenerationId,
      schema: pluginDialogSchema(plugin), error, closing: false,
      initial: Object.fromEntries(pluginSettingsSections(plugin).filter((section) => !['memory', 'voice'].includes(section.surface))
        .map((section) => [section.section_id, clonePlain(editablePluginSectionValues(section, pluginSectionValues(plugin.id, section.section_id)))])),
      voiceDraft: getVoiceController()?.pluginDraft(plugin.plugin_id),
      asrDraft: getAsrController()?.hasPluginControls(plugin.plugin_id) ? getAsrController()?.pluginDraft() : null,
      async close(accept = false, restore = true) {
        if (editor.closing) return;
        editor.closing = true; closeSelects(dialog);
        void getAsrController()?.cancelTest();
        dialog.inert = true;
        if (!accept && restore && runtimePluginController?.snapshot()?.coreGenerationId === editor.generation) {
          for (const [sectionId, values] of Object.entries(editor.initial)) {
            const current = pluginState.settingsValues[plugin.id]?.[sectionId];
            if (current) Object.assign(current, values);
          }
          if (editor.voiceDraft) getVoiceController()?.restorePluginDraft(editor.voiceDraft);
          if (editor.asrDraft) getAsrController()?.restorePluginDraft(editor.asrDraft);
        }
        dialog.classList.add('is-closing');
        await Promise.allSettled(dialog.getAnimations().map((animation) => animation.finished));
        if (pluginSettingsDialog !== editor) return;
        getVoiceController()?.unmountPluginSections();
        getAsrController()?.unmountPluginControls();
        dialog.close(); dialog.remove(); pluginSettingsDialog = null;
        refreshDirty(); renderPluginPage();
        fields.pluginDetail.querySelector('.plugin-configure')?.focus({ preventScroll: true });
      },
    };
    pluginSettingsDialog = editor;
    document.body.append(dialog);
    getVoiceController()?.mountPluginSections(plugin.plugin_id, voice);
    getAsrController()?.mountPluginControls(plugin.plugin_id, asr);
    general.querySelectorAll('select').forEach(enhanceSelect);
    close.addEventListener('click', () => void editor.close()); cancel.addEventListener('click', () => void editor.close());
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); void editor.close(); });
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const invalid = form.querySelector('input:invalid,select:invalid');
      if (invalid) {
        const advanced = invalid.closest('details'); if (advanced) advanced.open = true;
        focusSelect(invalid); if (invalid.tagName !== 'SELECT') invalid.reportValidity();
        return;
      }
      void editor.close(true);
    });
    dialog.showModal(); syncPluginSettingsDialog();
    const target = sectionId && fieldKey ? body.querySelector(
      `[data-plugin-section="${CSS.escape(sectionId)}"] [data-plugin-live-field="${CSS.escape(fieldKey)}"], #${CSS.escape(`voice-field-${plugin.plugin_id}-${sectionId}-${fieldKey}`)}`,
    ) : null;
    if (target) {
      const advanced = target.closest('details'); if (advanced) advanced.open = true;
      target.scrollIntoView({ block: 'center' });
      const control = target.querySelector('button:not(:disabled)') || target;
      if (control === target) control.tabIndex = -1;
      control.focus({ preventScroll: true });
    } else {
      focusSelect(body.querySelector('input:not(:disabled),select:not(:disabled)') || done);
    }
  }

  return Object.freeze({
    initialize: runtimePluginController.initialize,
    openPlugin(installId, configure = false) {
      const plugin = pluginView.items.find(item => item.id === installId);
      showPage("plugins");
      if (!plugin) return;
      pluginState.selectedId = installId;
      renderPluginPage();
      if (configure && pluginSettingsSections(plugin).some(section => section.surface !== "memory")) openPluginSettingsDialog(plugin);
    },
    isDirty: () => runtimePluginController.isDirty() || hasCollectionDrafts(),
    hasCollectionDrafts,
    async save() {
      if (hasCollectionDrafts()) {
        throw new Error("请先保存或还原正在编辑的集合记录，再保存设置。");
      }
      return runtimePluginController.save();
    },
    refreshCurrent: runtimePluginController.refreshCurrent,
    discard: runtimePluginController.discard,
    characterDraftCount: () => countCharacterScopedCollectionDrafts(pluginCollectionState.values()),
    renderMemorySurface,
    dialogElement: () => pluginSettingsDialog?.dialog,
    onVoiceSectionsRendered() {
      if (pluginSettingsDialog && !pluginSettingsDialog.voice.childElementCount) {
        const plugin = pluginView.items.find((item) => item.id === pluginSettingsDialog.installId);
        if (plugin) getVoiceController()?.mountPluginSections(plugin.plugin_id, pluginSettingsDialog.voice);
      }
      renderPluginPage();
    },
    onPageChanged(page) {
      clearPluginActivityRefresh();
      if (page === "plugins" || page === "about") schedulePluginActivityRefresh();
      if (page === "memory") renderMemorySurface();
    },
    clearCharacterState() {
      pluginCollectionState.forEach((state) => {
        clearTimer(state.searchTimer);
        state.queryRevision += 1;
        state.queryPending = false;
        state.queryPendingRender = false;
        state.editor = null;
        state.editorError = "";
      });
      clearMemoryEditorPortal();
      pluginCollectionState.clear();
      renderMemorySurface();
      refreshDirty();
    },
    dispose() {
      const editor = pluginSettingsDialog;
      pluginSettingsDialog = null;
      if (editor) {
        editor.closing = true;
        closeSelects(editor.dialog);
        getVoiceController()?.unmountPluginSections();
        getAsrController()?.unmountPluginControls();
        editor.dialog.close();
        editor.dialog.remove();
      }
      clearPluginActivityRefresh();
      for (const timer of timers) window.clearTimeout(timer);
      timers.clear();
      for (const removeListener of listeners) removeListener();
      listeners.length = 0;
      pluginCollectionState.clear();
      pluginView = { items: [] };
      clearMemoryEditorPortal();
      runtimePluginController.dispose();
    },
  });
}
