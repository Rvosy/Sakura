import { installSettingsSidebar } from "../../settings/sidebar.js";
import { enhanceSelect, refreshSelect, closeSelects } from "../../settings/select-control.js";
import { createIcon, installClickIconMotion } from "../../core/icons.js";
import { createErrorDialog } from "../../core/error-dialog.js";
import { applyRuntimeThemeTokens } from "../../core/theme-runtime.js";
import { createScenarioData, createCodexModels, REMOTE_DISCOVERY_CATALOG } from "./data.js";
import { createRedesignedProviderEditor } from "./provider-redesign.js";

const host = parent.__PROVIDER_REDESIGN_DEMO__;

installClickIconMotion(document);
let sidebarCollapsed = false;
installSettingsSidebar({
  document,
  storage: {
    getItem: () => String(sidebarCollapsed),
    setItem: (_key, value) => { sidebarCollapsed = value === "true"; },
  },
});

// 应用当前主题色并暴露给外层控制条实时切换
applyRuntimeThemeTokens(host.themeTokens, document.documentElement);
window.__DEMO_APPLY_THEME__ = (tokens) => applyRuntimeThemeTokens(tokens, document.documentElement);

// 激活“模型服务”单页与 Windows 内嵌标题栏外观
document.body.classList.add("has-custom-titlebar");
const windowControls = document.getElementById("windowControls");
if (windowControls) windowControls.hidden = false;

const maxBtn = document.getElementById("windowMaximize");
window.__DEMO_SYNC_WINDOW__ = (maximized) => {
  if (maxBtn) maxBtn.dataset.maximized = String(Boolean(maximized));
};
document.getElementById("windowMinimize")?.addEventListener("click", () => {
  notify("Demo 中已模拟最小化触发。", "info");
});
maxBtn?.addEventListener("click", () => {
  const maximized = host.toggleMaximize();
  window.__DEMO_SYNC_WINDOW__(maximized);
});
document.getElementById("windowClose")?.addEventListener("click", () => requestClose());

// 聚焦模型服务页
document.querySelectorAll(".settings-page").forEach((page) => {
  const active = page.id === "page-providers";
  page.hidden = !active;
  page.classList.toggle("is-active", active);
});
document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
  const active = btn.dataset.page === "providers";
  btn.classList.toggle("is-active", active);
  if (active) btn.setAttribute("aria-current", "page");
  else btn.removeAttribute("aria-current");
  btn.addEventListener("click", () => {
    if (btn.dataset.page !== "providers") {
      notify("当前 Demo 仅聚焦展示「模型服务」页面。", "info");
    }
  });
});

document.getElementById("pageTitle").textContent = "模型服务";
document.getElementById("pageIcon").innerHTML = '<span class="sakura-icon icon-cloud"></span>';
document.querySelector(".page-scroll")?.classList.add("is-admin-active");
document.getElementById("sidebarVersion").textContent = "Sakura 1.4.0 · Demo";

const errorTextEl = document.getElementById("errorText");
const toastStack = document.getElementById("toastStack");
const saveButton = document.getElementById("saveButton");
const applyButton = document.getElementById("applyButton");
const cancelButton = document.getElementById("cancelButton");
const warningsEl = document.getElementById("modelSelectionWarnings");
const surfaceEl = document.getElementById("modelProviderSurface");
const errorDialog = createErrorDialog({ document });

let slotAlertBtn = null;
let slotPopoverOpen = false;

if (host.mode !== "original") {
  slotAlertBtn = document.createElement("button");
  slotAlertBtn.type = "button";
  slotAlertBtn.className = "provider-slot-alert";
  slotAlertBtn.hidden = true;
  slotAlertBtn.setAttribute("aria-expanded", "false");
  slotAlertBtn.setAttribute("aria-controls", "modelSelectionWarnings");
  slotAlertBtn.addEventListener("click", () => {
    slotPopoverOpen = !slotPopoverOpen;
    slotAlertBtn.setAttribute("aria-expanded", String(slotPopoverOpen));
    if (warningsEl) warningsEl.hidden = !slotPopoverOpen;
  });
  const pageHead = document.querySelector(".page-head");
  if (pageHead && windowControls) {
    pageHead.insertBefore(slotAlertBtn, windowControls);
  }
  document.addEventListener("pointerdown", (event) => {
    if (!slotPopoverOpen) return;
    if (warningsEl?.contains(event.target) || slotAlertBtn.contains(event.target)) return;
    slotPopoverOpen = false;
    slotAlertBtn.setAttribute("aria-expanded", "false");
    if (warningsEl) warningsEl.hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && slotPopoverOpen) {
      slotPopoverOpen = false;
      slotAlertBtn.setAttribute("aria-expanded", "false");
      if (warningsEl) warningsEl.hidden = true;
    }
  });
}

function setError(error, title = "操作失败") {
  if (!error || (typeof error === "string" && !error.trim())) {
    errorTextEl.replaceChildren();
    return;
  }
  const message = typeof error === "string" ? error : (error.message || title);
  const status = document.createElement("span");
  status.textContent = message;
  const details = document.createElement("button");
  details.type = "button";
  details.className = "settings-error-details";
  details.textContent = "查看错误详情";
  const show = () => errorDialog.show({ title, message, error });
  details.addEventListener("click", show);
  errorTextEl.replaceChildren(status, details);
  show();
}

function notify(message, type = "info") {
  if (type === "error") {
    setError(message);
    return;
  }
  setError("");
  const toast = document.createElement("div");
  toast.className = `toast is-${type}`;
  toast.setAttribute("role", "status");
  toast.textContent = message;
  toastStack.append(toast);
  const remove = () => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 220);
  };
  window.setTimeout(remove, 2600);
  toast.addEventListener("click", remove);
}

function confirmAction(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("div");
    dialog.className = "confirm-dialog";
    const h2 = document.createElement("h2");
    h2.textContent = "确认操作";
    const p = document.createElement("p");
    p.textContent = message;
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = "取消";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "primary-button";
    ok.textContent = "确定";
    const finish = (value) => {
      overlay.remove();
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(false));
    ok.addEventListener("click", () => finish(true));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) finish(false); });
    actions.append(cancel, ok);
    dialog.append(h2, p, actions);
    overlay.append(dialog);
    document.body.append(overlay);
  });
}

// ---------- 内存态模型服务与探针模拟 ----------
const scenarioData = createScenarioData(host.scenario);
let savedConnections = structuredClone(scenarioData.connections);
let draftConnections = structuredClone(scenarioData.connections);
let codexProfiles = structuredClone(scenarioData.codexProfiles);
const modelSlots = structuredClone(scenarioData.modelSlots);

let probeStatus = {};
let probeTimer = null;
const abortController = new AbortController();

function syncDirty() {
  const dirty = JSON.stringify(draftConnections) !== JSON.stringify(savedConnections);
  document.body.classList.toggle("is-dirty", dirty);
  saveButton.classList.toggle("has-changes", dirty);
  return dirty;
}

function renderSlotWarnings() {
  if (!warningsEl) return;
  warningsEl.replaceChildren();
  const broken = [];
  for (const slot of modelSlots) {
    const sel = slot.selection;
    if (!sel?.serviceKey) continue;
    if (sel.serviceKey === "sakura.model.openai_compatible") {
      const profile = draftConnections.find((c) => c.id === sel.profileId);
      if (!profile) {
        broken.push({
          key: `${slot.identity}:missing-provider:${sel.profileId}`,
          label: slot.label,
          message: `${slot.label}“${sel.modelId}”所属的连接已删除，请重新选择模型。`,
        });
      } else if (!profile.models.includes(sel.modelId)) {
        broken.push({
          key: `${slot.identity}:missing-model:${sel.profileId}:${sel.modelId}`,
          label: slot.label,
          message: `${slot.label}“${sel.modelId}”已从“${profile.alias}”的模型列表移除。请重新添加该模型，或到「模型」页改选其他模型。`,
        });
      }
    } else if (sel.serviceKey === "sakura.model.codex") {
      const profile = codexProfiles.find((c) => c.profileId === sel.profileId);
      if (!profile || !profile.models.some((m) => m.modelId === sel.modelId)) {
        broken.push({
          key: `${slot.identity}:missing-codex:${sel.profileId}:${sel.modelId}`,
          label: slot.label,
          message: `${slot.label}“${sel.modelId}”当前不可用，请重新同步或改选模型。`,
        });
      }
    }
  }

  for (const item of broken) {
    const row = document.createElement("div");
    row.className = "model-selection-warning";
    const p = document.createElement("p");
    p.textContent = item.message;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary-button compact-button";
    btn.textContent = "前往模型页";
    btn.addEventListener("click", () => {
      if (slotAlertBtn) {
        slotPopoverOpen = false;
        slotAlertBtn.setAttribute("aria-expanded", "false");
        warningsEl.hidden = true;
      }
      notify("当前 Demo 聚焦「模型服务」页；正式设置中此处会跳转到「模型」页并高亮该用途。", "info");
    });
    row.append(p, btn);
    warningsEl.append(row);
  }

  if (host.mode === "original" || !slotAlertBtn) {
    warningsEl.hidden = !broken.length;
    return;
  }

  if (!broken.length) {
    slotPopoverOpen = false;
    slotAlertBtn.hidden = true;
    slotAlertBtn.setAttribute("aria-expanded", "false");
    warningsEl.hidden = true;
    return;
  }

  slotAlertBtn.hidden = false;
  slotAlertBtn.replaceChildren(
    createIcon(document, "triangle-alert"),
    document.createTextNode(broken.length === 1 ? `${broken[0].label}不可用` : `${broken.length} 项模型不可用`),
  );
  slotAlertBtn.setAttribute("aria-expanded", String(slotPopoverOpen));
  warningsEl.hidden = !slotPopoverOpen;
}

function resolveDiscoveryHost(baseUrl) {
  try {
    return new URL(baseUrl).host;
  } catch {
    return "";
  }
}

async function simulateProbe(operation, values) {
  const hostName = resolveDiscoveryHost(values.base_url);
  if (!values.base_url || !/^https?:\/\//i.test(values.base_url)) {
    throw new Error("BASE_URL_INVALID|API 地址格式无效，请以 http:// 或 https:// 开头。");
  }
  if (values.base_url.includes("fail")) {
    throw new Error("MODEL_CONNECTION_FAILED|无法连接模型服务：连接被远端拒绝（示例模拟故障）。");
  }
  if (operation === "list_models") {
    const list = REMOTE_DISCOVERY_CATALOG[hostName] || REMOTE_DISCOVERY_CATALOG.default;
    return { models: structuredClone(list) };
  }
  if (values.modelId?.includes("Deprecated")) {
    throw new Error(`MODEL_NOT_FOUND|服务商返回 404：模型 ${values.modelId} 不存在或已下线。`);
  }
  return { ok: true };
}

// 模拟 Codex 控制器（同时兼容原版 account.js 与重构版）
let codexTimer = null;
const codexPending = new Map();
const codexListeners = new Set();

function notifyCodexChanged() {
  for (const fn of codexListeners) fn();
  renderSlotWarnings();
}

const codexApi = {
  listServices() {
    return codexProfiles.map((p) => ({
      id: `sakura.model.codex:${p.profileId}`,
      profileId: p.profileId,
      label: p.label,
      iconName: "openai",
      createdAt: p.createdAt,
      account: p.account,
      usage: p.usage,
      configured: Boolean(p.account.loggedIn && p.models.length),
      models: p.models.map((m) => m.modelId),
      rawModels: p.models,
      operationResult: p.operationResult || {},
      pendingOperation: codexPending.get(p.profileId)?.operation || null,
      description: p.account.loggedIn ? (p.account.email || "已登录 ChatGPT") : "登录 ChatGPT 账号",
    }));
  },
  async add(createdAt) {
    const idx = codexProfiles.length + 1;
    const profile = {
      profileId: `codex-${Date.now()}`,
      label: idx === 1 ? "OpenAI Official" : `OpenAI Official ${idx}`,
      createdAt: createdAt || Date.now(),
      account: { loggedIn: false, email: "", plan: "" },
      models: [],
      usage: null,
      operationResult: {},
    };
    codexProfiles.push(profile);
    notifyCodexChanged();
    return `sakura.model.codex:${profile.profileId}`;
  },
  async rename(profileId, label) {
    const p = codexProfiles.find((item) => item.profileId === profileId);
    if (p) p.label = label;
    notifyCodexChanged();
  },
  async logout(profileId) {
    const p = codexProfiles.find((item) => item.profileId === profileId);
    if (p) {
      p.account = { loggedIn: false, email: "", plan: "" };
      p.usage = null;
      p.operationResult = {};
    }
    notifyCodexChanged();
  },
  async remove(profileId) {
    codexProfiles = codexProfiles.filter((item) => item.profileId !== profileId);
    codexPending.delete(profileId);
    notifyCodexChanged();
  },
  async cancel(profileId) {
    codexPending.delete(profileId);
    const p = codexProfiles.find((item) => item.profileId === profileId);
    if (p) p.operationResult = {};
    window.clearTimeout(codexTimer);
    notifyCodexChanged();
  },
  async start(profileId, operation, requestId = "") {
    const p = codexProfiles.find((item) => item.profileId === profileId);
    if (!p) return;
    codexPending.set(profileId, { operation });
    if (operation === "login") {
      p.operationResult = { requestId, operation: "login", state: "running", userCode: "SKR4-9X2M" };
      notifyCodexChanged();
      codexTimer = window.setTimeout(() => {
        if (!codexPending.has(profileId)) return;
        codexPending.delete(profileId);
        p.account = { loggedIn: true, email: "sakura.demo@example.com", plan: "Plus" };
        p.models = createCodexModels();
        const nowSec = Math.floor(Date.now() / 1000);
        p.usage = {
          updatedAt: nowSec,
          windows: [
            { label: "5 小时窗口", windowSeconds: 18000, usedPercent: 12.0, resetsAt: nowSec + 14400 },
            { label: "每周额度", windowSeconds: 604800, usedPercent: 28.5, resetsAt: nowSec + 432000 },
          ],
        };
        p.operationResult = { requestId, operation: "login", state: "completed", message: "登录成功，已同步全部模型。" };
        notify("已登录 ChatGPT 并同步模型列表。", "success");
        notifyCodexChanged();
      }, 1800);
      return;
    }
    p.operationResult = { requestId, operation, state: "running", message: "正在请求…" };
    notifyCodexChanged();
    await new Promise((r) => window.setTimeout(r, 700));
    if (!codexPending.has(profileId)) return;
    codexPending.delete(profileId);
    if (operation === "sync_models") {
      p.models = createCodexModels();
      p.operationResult = { requestId, operation, state: "completed", message: `已同步 ${p.models.length} 个可用模型。` };
      notify(`已同步 ${p.models.length} 个模型。`, "success");
    } else if (operation === "usage") {
      const nowSec = Math.floor(Date.now() / 1000);
      p.usage = {
        updatedAt: nowSec,
        windows: [
          { label: "5 小时窗口", windowSeconds: 18000, usedPercent: 18.4, resetsAt: nowSec + 11400 },
          { label: "每周额度", windowSeconds: 604800, usedPercent: 34.0, resetsAt: nowSec + 412000 },
        ],
      };
      p.operationResult = { requestId, operation, state: "completed", message: "额度已更新。" };
    }
    notifyCodexChanged();
  },
  async testModel(profileId, modelId) {
    const p = codexProfiles.find((item) => item.profileId === profileId);
    if (!p) throw new Error("连接不存在。");
    await new Promise((r) => window.setTimeout(r, 1200));
    return { elapsedMs: 1180, reply: "OK" };
  },
};

let activeEditor = null;
let originalCodexMount = null;

if (host.mode === "original") {
  // 现行原版对照：动态加载真实 plugins/builtin/sakura_model_openai_compatible 与 sakura_codex 前端模块
  const [{ mount: mountOriginalConnections }, { mount: mountOriginalCodex }] = await Promise.all([
    import("../../../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js"),
    import("../../../../plugins/optional/sakura_codex/frontend/account.js"),
  ]).catch(() => {
    throw new Error("「现行原版」对照需要从仓库根目录启动 HTTP 服务（例如 runtime\\python.exe -m http.server 8771）。");
  });
  originalCodexMount = mountOriginalCodex({
    document,
    window,
    signal: abortController.signal,
    isCurrent: () => true,
    read: (key) => (key === "profiles" ? codexProfiles : undefined),
    createIcon,
    enhanceSelect,
    refreshSelect,
    closeSelects,
    onError: setError,
    onCatalogChanged: () => {
      activeEditor?.updateContributions?.();
      renderSlotWarnings();
    },
    async action(actionId, values = {}) {
      const req = values.operationRequest || {};
      if (actionId === "add") {
        await codexApi.add(req.createdAt);
      } else if (actionId === "rename") {
        await codexApi.rename(req.profileId, req.label);
      } else if (actionId === "logout") {
        await codexApi.logout(req.profileId);
      } else if (actionId === "remove") {
        await codexApi.remove(req.profileId);
      } else if (actionId === "start") {
        if (req.operation === "test_connection") {
          const p = codexProfiles.find((item) => item.profileId === req.profileId);
          if (p) {
            p.operationResult = { requestId: req.requestId, operation: "test_connection", state: "running", modelId: req.modelId };
            window.setTimeout(() => {
              p.operationResult = { requestId: req.requestId, operation: "test_connection", state: "completed", modelId: req.modelId, reply: "OK", elapsedMs: 620 };
            }, 600);
          }
        } else {
          void codexApi.start(req.profileId, req.operation, req.requestId);
        }
      } else if (actionId === "cancel") {
        await codexApi.cancel(req.profileId);
      }
      return { values: { profiles: structuredClone(codexProfiles) } };
    },
  });

  activeEditor = mountOriginalConnections({
    document,
    window,
    signal: abortController.signal,
    isCurrent: () => true,
    createIcon,
    notify,
    onError: setError,
    onCatalogChanged: () => {
      syncDirty();
      renderSlotWarnings();
    },
    readSection: () => 60,
    read: (key) => (key === "connections" ? draftConnections : undefined),
    write: (key, value) => {
      if (key === "connections") {
        draftConnections = value;
        syncDirty();
        renderSlotWarnings();
      }
    },
    modelServices: () => originalCodexMount.contributions().modelServices,
    modelServicePresets: () => originalCodexMount.contributions().modelServicePresets,
    async action(actionId, values = {}) {
      const req = values.probeRequest || {};
      if (actionId === "probe") {
        probeStatus = { requestId: req.requestId, state: "running" };
        window.clearTimeout(probeTimer);
        probeTimer = window.setTimeout(async () => {
          try {
            const result = await simulateProbe(req.operation, req);
            probeStatus = {
              requestId: req.requestId,
              state: "completed",
              models: (result.models || []).map((m) => (typeof m === "string" ? { modelId: m } : { modelId: m.modelId })),
            };
          } catch (error) {
            const [code, msg] = String(error.message).split("|");
            probeStatus = { requestId: req.requestId, state: "failed", code: code || "MODEL_PROBE_FAILED", message: msg || error.message };
          }
        }, 550);
      } else if (actionId === "cancelProbe") {
        window.clearTimeout(probeTimer);
        probeStatus = {};
      }
      return { values: { probeResult: probeStatus } };
    },
  });
  surfaceEl.replaceChildren(activeEditor.element);
  activeEditor.update();
} else {
  // 重构方案
  let probeTicket = 0;
  let cancelledTicket = 0;
  activeEditor = createRedesignedProviderEditor({
    document,
    window,
    notify,
    onError: setError,
    confirmAction,
    getModelSlots: () => modelSlots,
    codexController: codexApi,
    read: () => draftConnections,
    write: (value) => {
      draftConnections = value;
      syncDirty();
      renderSlotWarnings();
    },
    onCatalogChanged: () => {
      syncDirty();
      renderSlotWarnings();
    },
    async cancel() {
      cancelledTicket = probeTicket;
    },
    async probe(operation, values) {
      const ticket = ++probeTicket;
      await new Promise((r) => window.setTimeout(r, operation === "test_connection" ? 1200 : 550));
      if (ticket <= cancelledTicket) throw new Error("MODEL_PROBE_CANCELLED");
      return simulateProbe(operation, values);
    },
  });
  codexListeners.add(() => activeEditor?.updateContributions?.());
  surfaceEl.replaceChildren(activeEditor.element);
}

function validateBeforeSave() {
  const seen = new Set();
  for (const conn of draftConnections) {
    let validUrl = false;
    try {
      const parsed = new URL(conn.base_url);
      validUrl = ["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password && !parsed.search && !parsed.hash;
    } catch {
      validUrl = false;
    }
    if (!conn.alias?.trim() || !validUrl || seen.has(conn.id)) {
      throw new Error(`模型服务“${conn.alias || conn.id}”的名称或 API 地址无效，请检查后重试。`);
    }
    seen.add(conn.id);
  }
}

function applyDrafts(closeAfter = false) {
  try {
    setError("");
    validateBeforeSave();
    savedConnections = structuredClone(draftConnections.map((item) => {
      if (host.mode === "original") {
        return {
          ...item,
          configured: item.credential_action === "replace"
            ? true
            : item.credential_action === "clear"
              ? false
              : item.configured,
          api_key: "",
          credential_action: "keep",
        };
      }
      const hasKey = Boolean(String(item.api_key || "").trim());
      return {
        ...item,
        configured: hasKey,
        credential_action: hasKey ? "replace" : "clear",
      };
    }));
    draftConnections = structuredClone(savedConnections);
    activeEditor?.update?.();
    syncDirty();
    renderSlotWarnings();
    notify(closeAfter ? "已保存模型服务配置。" : "已应用模型服务配置。", "success");
    if (closeAfter) host.close();
  } catch (error) {
    setError(error, "无法保存配置");
  }
}

async function requestClose() {
  if (!syncDirty()) {
    host.close();
    return;
  }
  const discard = await confirmAction("当前有未保存的模型服务改动，是否放弃改动并关闭？");
  if (discard) host.close();
}

applyButton.addEventListener("click", () => applyDrafts(false));
saveButton.addEventListener("click", () => applyDrafts(true));
cancelButton.addEventListener("click", () => void requestClose());

renderSlotWarnings();
syncDirty();
host.ready();
