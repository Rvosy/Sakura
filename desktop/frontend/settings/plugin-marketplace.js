import { iconMarkup as icon } from "../core/icons.js";
import { enhanceSelect, refreshSelect, closeSelects } from "./select-control.js";
import { marketplaceMarkup } from "./plugin-marketplace-view.js";
import { recommended, hasUpdate as updating, canInstall, createCatalogLoader } from "./plugin-marketplace-runtime.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const categories = ["全部", "工具", "语音", "记忆", "连接", "表现"];

// Source returns a display DTO. Compatibility and recommended version are resolved upstream.
// No registry URL, sample catalog, or simulated task is used by the production default.
export function createPluginMarketplace({ document, host, notify, source = null, openSources = null }) {
  const $ = id => document.getElementById(id);
  const page = $("page-plugins");
  const template = document.createElement("template");
  template.innerHTML = marketplaceMarkup;
  page.append(template.content.querySelector("#market-surface"));
  document.body.append(template.content);
  $("pluginInstallMenu").append($("market-sources"), $("refresh"));
  const filterPanel = $("market-filter-panel");
  function closeFilters() {
    closeSelects(filterPanel);
    if (filterPanel.matches(":popover-open")) filterPanel.hidePopover();
  }
  const tabs = document.createElement("div");
  tabs.id = "market-tabs"; tabs.className = "plugin-role-tabs";
  tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "插件页面");
  tabs.innerHTML = '<button class="plugin-role-tab" id="installed-tab" role="tab" aria-selected="true" aria-controls="pluginList" data-view="installed">已安装</button><button class="plugin-role-tab" id="market-tab" role="tab" aria-selected="false" aria-controls="market-surface" tabindex="-1" data-view="market">市场</button>';
  page.querySelector(".plugin-page-heading > div").after(tabs);
  let plugins = [], view = "installed", category = "全部", selected = null;
  let catalogState = "unconfigured", updatedAt = "", disposed = false, sourceName = "", catalogError = "";
  const tasks = new Map(), listeners = [];
  const mark = p => `<span class="plugin-mark ${["blue", "pink", "gold", "green", "violet"].includes(p.color) ? p.color : "blue"}">${icon(p.icon || "puzzle")}</span>`;
  const example = p => p.example ? '<span class="example-label">构想示例</span>' : "";
  function listen(target, event, callback) {
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }
  function syncInstalled() {
    const installed = host.installedPlugins();
    for (const p of plugins) {
      const local = installed.find(item => item.pluginId === p.id);
      p.installed = local?.version; p.enabled = local?.enabled;
      p.updateBlocked = local?.source === "bundled" ? "内置插件随应用更新" : local?.enabled ? "请先停用插件再更新" : "";
    }
  }
  function actionButton(p) {
    return `<button class="card-action detail" data-action="detail" data-id="${escape(p.id)}" aria-label="详情 ${escape(p.name)}">详情</button>`;
  }
  function taskHint(p) {
    const task = tasks.get(p.id);
    if (task?.state === "running") return " · 正在安装";
    if (task?.state === "failed") return task.installed ? " · 待刷新" : " · 安装失败";
    if (updating(p)) return " · 可更新";
    return p.installed ? " · 已安装" : "";
  }
  function versionHint(p) {
    const next = recommended(p);
    if (!next) return p.compatibilityReason || "暂无兼容版本";
    if (updating(p)) return `${p.installed} → ${next.number}`;
    if (p.versions[0] !== next) return `兼容版本 ${next.number}`;
    return `v${next.number}`;
  }
  function card(p) {
    return `<article class="market-card">
      <div class="card-identity">${mark(p)}<div><h2 class="card-name"><button class="card-open" data-action="detail" data-id="${escape(p.id)}">${escape(p.name)}</button></h2><div class="card-author">${escape(p.author)}${example(p)}</div></div></div>
      <p class="card-description">${escape(p.description)}</p>
      <div class="card-meta"><span class="category-tag">${escape(p.category)}</span><span>${escape(p.kind)}</span></div>
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
    if ($("pluginTotal")) $("pluginTotal").hidden = true;
    $("page-plugins").dataset.marketVisible = String(market);
    $("page-plugins").querySelector(".plugin-toolbar").hidden = market;
    $("page-plugins").querySelector(".plugin-workbench").hidden = market;
    document.querySelectorAll("[data-view]").forEach(tab => {
      const active = tab.dataset.view === view;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    $("content").setAttribute("aria-labelledby", "market-tab");
    $("refresh").hidden = view !== "market";
    $("categories").innerHTML = categories.map(c => `<button aria-pressed="${category === c}" data-category="${c}">${c}</button>`).join("");
    const cached = view === "market" && catalogState === "cached";
    $("notice").hidden = !cached;
    $("notice").innerHTML = cached ? '<span>离线 · 显示缓存目录</span><button class="plain" data-reconnect>重新连接</button>' : "";
    $("catalog-state").textContent = ["ready", "cached"].includes(catalogState) ? `${catalogState === "cached" ? "缓存目录" : "目录已更新"}${updatedAt ? " · " + updatedAt : ""}` : "";
    const query = $("search").value.trim().toLocaleLowerCase();
    let matches = plugins.filter(p => (!$("compatible").checked || recommended(p))
      && (!$("hide-installed").checked || !p.installed || updating(p))
      && (category === "全部" || category === p.category)
      && (!query || [p.id, p.name, p.author, p.description].join(" ").toLocaleLowerCase().includes(query)));
    if ($("sort").value === "name") matches.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
    if ($("sort").value === "updated") matches.sort((a, b) => (b.versions[0]?.date || "").localeCompare(a.versions[0]?.date || ""));
    const unavailable = !["ready", "cached"].includes(catalogState);
    $("market-surface").querySelector(".market-foot").hidden = unavailable;
    $("result-count").textContent = unavailable ? "目录未加载" : `${matches.length} 个${view === "installed" ? "已安装插件" : "插件"}`;
    $("market-filter").disabled = unavailable;
    $("market-filter").textContent = !$("compatible").checked || $("hide-installed").checked || $("sort").value !== "default" ? "筛选 · 已调整" : "筛选";
    $("sort").disabled = unavailable;
    $("refresh").disabled = catalogState === "loading" || !source;
    $("search").disabled = unavailable;
    $("compatible").disabled = unavailable;
    $("hide-installed").disabled = unavailable;
    $("categories").querySelectorAll("button").forEach(button => button.disabled = unavailable);
    refreshSelect($("sort"));
    if (unavailable) {
      const message = {unconfigured: "市场暂未开放", loading: sourceName ? `正在通过 ${sourceName} 加载` : "正在加载", error: catalogError || "无法连接市场"}[catalogState];
      $("result-count").textContent = "";
      $("catalog").innerHTML = `<div class="empty">${icon(catalogState === "error" ? "cloud" : "puzzle")}<h3 role="status">${message}</h3>${catalogState === "error" ? '<button data-reconnect>重试</button>' : ""}</div>`;
    } else if (!matches.length) {
      $("catalog").innerHTML = `<div class="empty">${icon("search")}<h3>${plugins.length ? "没有匹配的插件" : "暂无已收录插件"}</h3>${plugins.length ? '<button class="secondary-button" data-clear>清除筛选</button>' : ""}</div>`;
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
    if (!p) { $("detail-dialog").close(); return; }
    const next = recommended(p), task = tasks.get(p.id);
    let compatibility = "";
    if (!next) compatibility = `<div class="compat-note warning">${escape(p.compatibilityReason || '暂无兼容版本')}</div>`;
    else if (updating(p) && p.updateBlocked) compatibility = `<div class="compat-note">${escape(p.updateBlocked)}</div>`;
    else if (p.compatibilityReason) compatibility = `<div class="compat-note">${escape(p.compatibilityReason)}</div>`;
    const taskMarkup = task?.state === "running"
      ? `<div class="task-state" role="status"><span class="task-label">${task.phase === "installing" ? "正在安装" : "正在下载"}${task.source ? ` · ${escape(task.source)}` : ""} · ${Math.round(task.progress)}%</span><div class="resource-progress" role="progressbar" aria-label="安装进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${task.progress}"><span style="width:${task.progress}%"></span></div></div>`
      : task?.state === "failed" ? `<div class="task-state task-error" role="alert">${escape(task.error)}</div>` : "";
    $("detail").innerHTML = `<div class="drawer-top"><span>插件详情</span><button class="icon-button" data-close aria-label="关闭插件详情">${icon("x")}</button></div>
      <div class="drawer-scroll"><div class="drawer-identity">${mark(p)}<div><h2 id="detail-title">${escape(p.name)}</h2><div class="card-author">${escape(p.author)}${example(p)}</div></div></div>
      <p class="detail-summary">${escape(p.description)}</p>
      <dl class="detail-facts"><div><dt>分类</dt><dd>${escape(p.category)} · ${escape(p.kind)}</dd></div><div><dt>推荐版本</dt><dd>${next ? escape(next.number) : "暂无兼容版本"}</dd></div><div><dt>插件包</dt><dd>${escape(next?.size || p.versions[0]?.size || "—")}</dd></div><div><dt>本地状态</dt><dd>${p.installed ? `${escape(p.installed)} · ${p.enabled ? "已启用" : "未启用"}` : "未安装"}</dd></div></dl>
      ${compatibility}<section class="detail-section"><h3>介绍</h3><p>${escape(p.body || p.description)}</p>${p.consequence ? `<h3>使用前需了解</h3><p>${escape(p.consequence)}</p>` : ""}<h3>版本记录</h3>
      ${p.versions.map(v => `<div class="version-row"><div class="version-title"><strong>${escape(v.number)}</strong>${v === next ? '<span class="version-label">当前推荐</span>' : ""}${v.yanked ? '<span class="version-label warning">已撤回</span>' : v.prerelease ? '<span class="version-label warning">预发布</span>' : v.compatible === false ? '<span class="version-label warning">不兼容</span>' : ""}<time>${escape(v.date || "")}</time></div><p>${escape(v.yanked || v.notes)}</p><span class="version-hint">Plugin API ${escape(v.api ?? "—")}</span></div>`).join("")}
      </section><div class="plugin-id">${escape(p.id)}</div></div>
      <div class="drawer-bottom">${taskMarkup}<div class="drawer-actions"><span class="version-hint">${escape(versionHint(p))}</span><div>${task?.state === "running" ? `<button class="secondary-button" data-cancel-task ${task.phase === "installing" || task.cancelling ? "disabled" : ""}>${task.cancelling && task.phase !== "installing" ? "正在取消" : "取消安装"}</button>` : task?.state === "failed" ? '<button data-retry>重试</button>' : !next ? '<button disabled>暂无兼容版本</button>' : updating(p) ? `<button data-install ${source?.canUpdate && source?.install && !p.updateBlocked ? '' : 'disabled'}>更新至 ${escape(next.number)}</button>` : p.installed ? '<button class="secondary-button" data-manage>管理插件</button>' : `<button data-install ${source?.install ? '' : 'disabled'}>安装插件</button>`}</div></div></div>`;
  }
  function openDetail(id) {
    closeFilters(); closeSelects();
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

  const loader = createCatalogLoader(source, result => {
    catalogState = result.state;
    sourceName = result.sourceName || ""; catalogError = result.error || "";
    if (result.plugins) plugins = result.plugins;
    updatedAt = result.updatedAt || "";
    render(); refreshDetail();
  });
  async function startTask(id) {
    const p = plugins.find(p => p.id === id);
    const installed = tasks.get(id)?.installed;
    if ((!installed && !canInstall(p, source)) || tasks.get(id)?.state === "running") return;
    const abort = new AbortController();
    const task = { state: "running", progress: installed ? 100 : 0, phase: installed ? "installing" : "downloading", abort, installed };
    tasks.set(id, task); render(); refreshDetail();
    try {
      if (!task.installed) await source.install(p, { signal: abort.signal, onProgress(progress, phase = "downloading", sourceName = "") {
        if (disposed || tasks.get(id) !== task) return;
        task.progress = Math.max(0, Math.min(100, Number(progress) || 0)); task.phase = phase; task.source = sourceName;
        if (selected === id) refreshDetail();
      } });
      if (disposed || tasks.get(id) !== task) return;
      task.installed = true;
      task.phase = "installing";
      await host.refreshCurrent();
      if (disposed) return;
      tasks.delete(id); notify("已安装", "success");
    } catch (error) {
      if (disposed || tasks.get(id) !== task) return;
      if (abort.signal.aborted && task.phase !== "installing") { tasks.delete(id); notify("已取消安装"); }
      else { task.state = "failed"; task.error = task.installed ? "列表刷新失败" : String(error.message || error); }
    }
    if (!disposed) { render(); refreshDetail(); }
  }
  function cancelTask() {
    const task = tasks.get(selected);
    if (!task || task.phase === "installing") return;
    task.cancelling = true; task.abort.abort();
    render(); refreshDetail();
  }
  function clearFilters() { category = "全部"; $("search").value = ""; $("compatible").checked = true; $("hide-installed").checked = false; $("sort").value = "default"; render(); }
  function setView(nextView, { load = true } = {}) {
    if (disposed) return;
    closeFilters(); closeSelects(); view = nextView;
    render(); $("content").scrollTop = 0;
    if (load && view === "market" && catalogState === "unconfigured" && source) void loader.load();
  }
  function handleClick(event) {
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
    if (button.hasAttribute("data-install") || button.hasAttribute("data-retry")) void startTask(selected);
    if (button.hasAttribute("data-cancel-task")) cancelTask();
    if (button.hasAttribute("data-manage")) {
      const local = host.installedPlugins().find(p => p.pluginId === selected);
      $("detail-dialog").close(); setView("installed");
      if (local) host.openPlugin(local.installId);
    }
    if (button.hasAttribute("data-clear")) { clearFilters(); $("search").focus(); }
    if (button.hasAttribute("data-reconnect")) void loader.load();
  }
  listen(page, "click", handleClick); listen($("detail-dialog"), "click", handleClick);
  listen($("detail-dialog"), "click", event => {
    if (event.target === $("detail-dialog")) {
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close();
    }
  });
  listen(page, "keydown", event => {
    if (!page.classList.contains("is-active")) return;
    if (view === "market" && event.key === "/" && !event.ctrlKey && !event.metaKey && !$("detail-dialog").open && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && !event.target.isContentEditable) {
      event.preventDefault(); $("search").focus();
    }
    if (event.target.matches("[data-view]") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      setView(event.key === "Home" ? "installed" : event.key === "End" ? "market" : view === "market" ? "installed" : "market");
      tabs.querySelector(`[data-view="${view}"]`).focus();
    }
  });
  listen($("search"), "input", render); listen($("compatible"), "change", render);
  listen($("hide-installed"), "change", render);
  listen($("sort"), "change", render); listen($("refresh"), "click", () => void loader.load());
  if (openSources) listen($("market-sources"), "click", () => void openSources());
  else $("market-sources").hidden = true;
  listen(filterPanel, "beforetoggle", event => {
    if (event.newState !== "open") { closeSelects(filterPanel); return; }
    const bounds = $("market-filter").getBoundingClientRect();
    filterPanel.style.top = `${bounds.bottom + 8}px`;
    filterPanel.style.left = `${Math.max(12, Math.min(bounds.right - 240, document.defaultView.innerWidth - 252))}px`;
  });
  listen(filterPanel, "toggle", () => $("market-filter").setAttribute("aria-expanded", String(filterPanel.matches(":popover-open"))));
  listen(document.defaultView, "resize", closeFilters);
  enhanceSelect($("sort")); render();
  return {
    setView,
    refresh: () => loader.load(),
    sync() { if (!disposed) { render(); refreshDetail(); } },
    onPageChanged(pageName) { if (pageName !== "plugins") { closeFilters(); closeSelects($("market-surface")); $("detail-dialog").close(); } },
    dispose() {
      disposed = true; loader.dispose();
      for (const task of tasks.values()) task.abort.abort();
      listeners.forEach(remove => remove()); closeFilters(); closeSelects($("market-surface"));
      filterPanel.remove(); $("market-sources").remove(); $("refresh").remove();
      $("detail-dialog").close(); $("detail-dialog").remove(); $("market-surface").remove(); tabs.remove();
    },
  };
}
