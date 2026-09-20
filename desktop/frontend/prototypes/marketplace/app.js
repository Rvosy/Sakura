import { categories, createPlugins, recommended } from "./data.js";
import { iconMarkup as icon } from "../../core/icons.js";
import { enhanceSelect, refreshSelect, closeSelects } from "../../settings/select-control.js";

const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const review = parent.marketReview;
const nativeState = review.state;
let plugins = createPlugins();
let view = "installed", category = "全部", selected = null;
let pendingAction = null;
const tasks = new Map(), timers = new Map(), failedOnce = new Set();
const mode = () => review.scenario;
const mark = (p) => `<span class="plugin-mark ${p.color}">${icon(p.icon)}</span>`;
const example = (p) => p.example ? '<span class="example-label">构想示例</span>' : "";
const updating = (p) => p.installed && recommended(p) && p.installed !== recommended(p).number;

function syncInstalled() {
  for (const p of plugins) {
    const local = nativeState.plugins.find(item => item.pluginId === p.id);
    p.installed = local?.version;
    p.enabled = local?.enabled;
    if (local) {
      p.name = local.name; p.author = local.author; p.description = local.description;
      p.kind = { extension: "功能扩展", provider: "功能引擎", infrastructure: "系统组件" }[local.presentation.kind];
    }
  }
}
async function publishInstall(p) {
  let local = nativeState.plugins.find(item => item.pluginId === p.id);
  if (!local) {
    local = { installId: "pi_user_" + Array.from(new TextEncoder().encode(p.id), n => n.toString(16).padStart(2, "0")).join(""),
      pluginId: p.id, name: p.name, author: p.author, description: p.description,
      enabled: false, required: false, supported: true, source: "user", canUninstall: true,
      provides: [], requires: [], missingServices: [], state: "disabled", reasonCode: "PLUGIN_DISABLED", sections: [],
      presentation: {kind: "extension", category: {工具: "tools", 连接: "connectivity", 表现: "visual"}[p.category] || "other", icon: p.icon} };
    nativeState.plugins.push(local);
  }
  local.version = p.installed;
  nativeState.revision++;
  await window.marketHost.refreshCurrent();
}

function actionButton(p) {
  return `<button class="card-action detail" data-action="detail" data-id="${escape(p.id)}" aria-label="详情 ${escape(p.name)}">详情</button>`;
}
function taskHint(p) {
  const task = tasks.get(p.id);
  if (task?.state === "running") return " · 正在安装";
  if (task?.state === "failed") return " · 下载失败";
  if (updating(p)) return " · 可更新";
  return p.installed ? " · 已安装" : "";
}
function versionHint(p) {
  const next = recommended(p);
  if (!next) return "需要 Plugin API 5";
  if (updating(p)) return `${p.installed} → ${next.number}`;
  if (p.versions[0] !== next) return `兼容版本 ${next.number}`;
  return `v${next.number}`;
}
function card(p) {
  return `<article class="market-card">
    <div class="card-identity">${mark(p)}<div><h2 class="card-name"><button class="card-open" data-action="detail" data-id="${escape(p.id)}">${escape(p.name)}</button></h2><div class="card-author">${escape(p.author)}${example(p)}</div></div></div>
    <p class="card-description">${escape(p.description)}</p>
    <div class="card-meta"><span class="category-tag">${p.category}</span><span>${p.kind}</span></div>
    <div class="card-bottom"><span class="version-hint">${escape(versionHint(p))}${taskHint(p)}</span>${actionButton(p)}</div>
  </article>`;
}

function render() {
  const activeElement = document.activeElement;
  const focusAction = activeElement?.closest("#catalog [data-action]");
  const focusTarget = focusAction && { id: focusAction.dataset.id, action: focusAction.dataset.action };
  syncInstalled();
  const market = view === "market";
  $("market-surface").hidden = !market;
  $("pluginTotal").hidden = true;
  $("page-plugins").dataset.marketVisible = String(market);
  $("page-plugins").querySelector(".plugin-toolbar").hidden = market;
  $("page-plugins").querySelector(".plugin-workbench").hidden = market;
  document.querySelectorAll("[data-view]").forEach(tab => {
    const active = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  $("content").setAttribute("aria-labelledby", "market-tab");
  document.querySelector(".compatibility").hidden = view !== "market";
  $("refresh").hidden = view !== "market";
  $("categories").innerHTML = categories.map(c => `<button aria-pressed="${category === c}" data-category="${c}">${c}</button>`).join("");
  const cached = view === "market" && mode() === "cached";
  $("notice").hidden = !cached;
  $("notice").innerHTML = cached ? '<span>离线 · 显示缓存目录</span><button class="plain" data-reconnect>重新连接</button>' : "";
  $("catalog-state").textContent = mode() === "cached" ? "缓存目录 · 2026-09-18 14:30" : mode() === "unavailable" ? "尚无可用目录" : "目录已更新 · 刚刚";
  const query = $("search").value.trim().toLocaleLowerCase();
  let matches = plugins.filter(p => (!$("compatible").checked || recommended(p))
    && (category === "全部" || category === p.category)
    && (!query || [p.id, p.name, p.author, p.description].join(" ").toLocaleLowerCase().includes(query)));
  if ($("sort").value === "name") matches.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  if ($("sort").value === "updated") matches.sort((a, b) => b.versions[0].date.localeCompare(a.versions[0].date));
  const unavailable = view === "market" && mode() === "unavailable";
  $("result-count").textContent = unavailable ? "目录未加载" : `${matches.length} 个${view === "installed" ? "已安装插件" : "插件"}`;
  $("sort").disabled = unavailable;
  refreshSelect($("sort"));
  if (unavailable) {
    $("catalog").innerHTML = `<div class="empty">${icon("cloud")}<h3>无法连接市场</h3><button data-reconnect>重新连接</button></div>`;
  } else if (!matches.length) {
    $("catalog").innerHTML = `<div class="empty">${icon("search")}<h3>没有匹配的插件</h3><button class="secondary-button" data-clear>清除筛选</button></div>`;
  } else {
    $("catalog").innerHTML = matches.map(card).join("");
  }
  if (focusTarget) {
    const buttons = [...$("catalog").querySelectorAll("[data-action]")];
    (buttons.find(b => b.dataset.id === focusTarget.id && b.dataset.action === focusTarget.action)
      || buttons.find(b => b.dataset.id === focusTarget.id))?.focus({ preventScroll: true });
  }
}

function renderDetail() {
  const p = plugins.find(p => p.id === selected);
  if (!p) return;
  const next = recommended(p), task = tasks.get(p.id);
  let compatibility = "";
  if (!next) compatibility = '<div class="compat-note warning">需要 Plugin API 5，当前为 4</div>';
  else if (next !== p.versions[0]) compatibility = `<div class="compat-note">${p.versions[0].number} 不兼容，推荐 ${next.number}</div>`;
  const taskMarkup = task?.state === "running"
    ? `<div class="task-state" role="status"><span class="task-label">${task.progress < 75 ? "正在下载" : "正在安装"} · ${task.progress}%</span><div class="resource-progress" role="progressbar" aria-label="安装进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${task.progress}"><span style="width:${task.progress}%"></span></div></div>`
    : task?.state === "failed" ? `<div class="task-state task-error" role="alert">${escape(task.error)}</div>` : "";
  $("detail").innerHTML = `<div class="drawer-top"><span>插件详情</span><button class="icon-button" data-close aria-label="关闭插件详情">${icon("x")}</button></div>
    <div class="drawer-scroll"><div class="drawer-identity">${mark(p)}<div><h2 id="detail-title">${escape(p.name)}</h2><div class="card-author">${escape(p.author)}${example(p)}</div></div></div>
    <p class="detail-summary">${escape(p.description)}</p>
    <dl class="detail-facts"><div><dt>分类</dt><dd>${p.category} · ${p.kind}</dd></div><div><dt>推荐版本</dt><dd>${next ? next.number : "暂无兼容版本"}</dd></div><div><dt>插件包</dt><dd>${next?.size || p.versions[0].size}</dd></div><div><dt>本地状态</dt><dd>${p.installed ? `${p.installed} · ${p.enabled ? "已启用" : "未启用"}` : "未安装"}</dd></div></dl>
    ${compatibility}<section class="detail-section"><h3>介绍</h3><p>${escape(p.body)}</p><h3>使用前需了解</h3><p>${escape(p.consequence)}</p><h3>版本记录</h3>
    ${p.versions.map(v => `<div class="version-row"><div class="version-title"><strong>${v.number}</strong>${v === next ? '<span class="version-label">当前推荐</span>' : ""}${v.yanked ? '<span class="version-label warning">已撤回</span>' : v.prerelease ? '<span class="version-label warning">预发布</span>' : v.api !== 4 ? '<span class="version-label warning">不兼容</span>' : ""}<time>${v.date}</time></div><p>${escape(v.yanked || v.notes)}</p><span class="version-hint">Plugin API ${v.api}</span></div>`).join("")}
    </section><div class="plugin-id">${escape(p.id)}</div></div>
    <div class="drawer-bottom">${taskMarkup}<div class="drawer-actions"><span class="version-hint">${escape(versionHint(p))}</span><div>${task?.state === "running" ? '<button class="secondary-button" data-cancel-task>取消安装</button>' : task?.state === "failed" ? '<button data-retry>重试</button>' : !next ? '<button disabled>暂无兼容版本</button>' : updating(p) ? `<button data-install>更新至 ${next.number}</button>` : p.installed ? '<button class="secondary-button" data-manage>在已安装中管理</button>' : '<button data-install>安装插件</button>'}</div></div></div>`;
}
function openDetail(id) {
  closeSelects();
  selected = id;
  renderDetail();
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
  $("detail").querySelector(".drawer-scroll").scrollTop = 0;
}
function refreshDetail() {
  if (!$("detail-dialog").open) return;
  const scroll = $("detail").querySelector(".drawer-scroll")?.scrollTop || 0;
  const focus = document.activeElement;
  const focusAttribute = focus?.closest("#detail") ? [...focus.attributes].find(a => a.name.startsWith("data-"))?.name : null;
  renderDetail();
  $("detail").querySelector(".drawer-scroll").scrollTop = scroll;
  if (focusAttribute) ($("detail").querySelector(`[${focusAttribute}]`) || $("detail").querySelector(".drawer-actions button"))?.focus({ preventScroll: true });
}
function confirmAction(title, paragraphs, label, callback) {
  $("action-title").textContent = title;
  $("action-body").replaceChildren(...paragraphs.map(text => {
    const p = document.createElement("p"); p.textContent = text; return p;
  }));
  $("action-confirm").textContent = label;
  pendingAction = callback;
  $("action-dialog").returnValue = "";
  $("action-dialog").showModal();
}
function install(id) {
  const p = plugins.find(p => p.id === id);
  if (!recommended(p) || tasks.get(id)?.state === "running") return;
  startTask(id);
}
function startTask(id) {
  const p = plugins.find(p => p.id === id);
  if (!recommended(p) || tasks.get(id)?.state === "running") return;
  const fail = ["cached", "unavailable"].includes(mode()) || (mode() === "download-error" && !failedOnce.has(id));
  const task = { state: "running", progress: 0 };
  tasks.set(id, task);
  render(); refreshDetail();
  const timer = setInterval(async () => {
    task.progress += 10;
    if (fail && task.progress === 40) {
      task.state = "failed";
      task.error = mode() === "download-error" ? "下载中断" : "无法连接下载服务";
      failedOnce.add(id);
      clearInterval(timer); timers.delete(id);
    } else if (task.progress >= 100) {
      p.installed = recommended(p).number;
      p.enabled ??= false;
      clearInterval(timer); timers.delete(id);
      await publishInstall(p);
      tasks.delete(id);
      toast("已安装");
    }
    if (task.state !== "running" || task.progress >= 100) {
      render(); refreshDetail();
    } else if (selected === id && $("detail-dialog").open) {
      const progress = $("detail").querySelector(".resource-progress");
      const label = $("detail").querySelector(".task-label");
      if (progress) {
        progress.setAttribute("aria-valuenow", String(task.progress));
        progress.firstElementChild.style.width = `${task.progress}%`;
      }
      if (label) label.textContent = `${task.progress < 75 ? "正在下载" : "正在安装"} · ${task.progress}%`;
    }
  }, 240);
  timers.set(id, timer);
}
function cancelTask() {
  clearInterval(timers.get(selected)); timers.delete(selected); tasks.delete(selected);
  render(); refreshDetail(); toast("已取消安装");
}
function toast(text) {
  window.marketNotify(text);
}
function clearFilters() {
  category = "全部"; $("search").value = ""; $("compatible").checked = true; render();
}
function setView(nextView) {
  closeSelects();
  view = nextView; clearFilters(); $("content").scrollTop = 0;
}
function reconnect() {
  review.reconnect(); render(); toast("已连接");
}

document.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.view) setView(button.dataset.view);
  if (button.dataset.category) {
    category = button.dataset.category; render();
    [...$("categories").children].find(b => b.dataset.category === category)?.focus();
    $("content").scrollTop = 0;
  }
  if (button.dataset.action === "detail") openDetail(button.dataset.id);
  if (button.hasAttribute("data-close")) $("detail-dialog").close();
  if (button.hasAttribute("data-install")) install(selected);
  if (button.hasAttribute("data-retry")) startTask(selected);
  if (button.hasAttribute("data-cancel-task")) cancelTask();
  if (button.hasAttribute("data-manage")) {
    const local = nativeState.plugins.find(p => p.pluginId === selected);
    $("detail-dialog").close(); setView("installed");
    if (local) window.marketHost.openPlugin(local.installId);
  }
  if (button.hasAttribute("data-clear")) { clearFilters(); $("search").focus(); }
  if (button.hasAttribute("data-reconnect")) reconnect();

});
$("action-dialog").addEventListener("close", () => {
  const callback = pendingAction; pendingAction = null;
  if ($("action-dialog").returnValue === "confirm") callback?.();
});
$("detail-dialog").addEventListener("click", event => {
  if (event.target === $("detail-dialog")) $("detail-dialog").close();
});
document.addEventListener("keydown", event => {
  if (view === "market" && event.key === "/" && !event.ctrlKey && !event.metaKey && !$("detail-dialog").open && !$("action-dialog").open && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
    event.preventDefault(); $("search").focus();
  }
  if (event.target.matches("[data-view]") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    setView(event.key === "Home" ? "installed" : event.key === "End" ? "market" : view === "market" ? "installed" : "market");
    document.querySelector(`[data-view="${view}"]`).focus();
  }
});
$("search").oninput = render;
$("compatible").onchange = render;
$("sort").onchange = render;
$("refresh").onclick = () => {
  if (["cached", "unavailable"].includes(mode())) toast("无法连接市场");
  else { render(); toast("已刷新"); }
};
window.marketDemo = {
  scenarioChanged() { setView("market"); refreshDetail(); },
  sync() { render(); refreshDetail(); },
  info(title, text) { confirmAction(title, [text], "知道了", () => {}); },
};
enhanceSelect($("sort"));
render();
