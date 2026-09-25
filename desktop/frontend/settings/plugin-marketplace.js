import { errorText } from "../core/error-display.js";
import { iconMarkup as icon } from "../core/icons.js";
import { enhanceSelect, refreshSelect, closeSelects } from "./select-control.js";
import { marketplaceMarkup } from "./plugin-marketplace-view.js";
import { recommended, hasUpdate as updating, canInstall, createCatalogLoader } from "./plugin-marketplace-runtime.js";
import { documentationUrl, renderPluginReadme } from "./plugin-readme.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const categories = ["全部", "工具", "语音", "记忆", "连接", "表现"];
const submissionUrl = "https://github.com/Rvosy/Sakura-Registry/issues/new?template=submit-plugin.yml";

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
  const submitDialog = $("market-submit-dialog");
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
  let catalogState = "unconfigured", updatedAt = "", disposed = false, sourceName = "", catalogError = "", refreshing = false;
  const tasks = new Map(), documents = new Map(), listeners = [];
  const documentKey = p => { const version = recommended(p) || p.versions.find(v => v.package && !v.yanked); return JSON.stringify([p.id, p.repository, version?.number, version?.commit]); };
  const mark = p => `<span class="plugin-mark ${["blue", "pink", "gold", "green", "violet"].includes(p.color) ? p.color : "blue"}">${icon(p.icon || "puzzle")}</span>`;
  const example = p => p.example ? '<span class="example-label">构想示例</span>' : "";
  function listen(target, event, callback) {
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }
  function syncInstalled() {
    const installed = host.installedPlugins();
    for (const p of plugins) {
      const local = installed.find(item => item.pluginId === p.id && !item.reasonCode?.startsWith("PLUGIN_MIGRATION_"));
      p.installed = local?.version; p.enabled = local?.enabled;
      p.updateBlocked = local?.source === "bundled" ? "内置插件随应用更新"
        : local?.reasonCode === "PLUGIN_ID_CONFLICT" ? "请先在已安装列表中卸载冲突的插件副本。" : "";
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
      <div class="card-meta"><span class="category-tag">${escape(p.category === "表现" ? "角色表现" : p.category)}</span></div>
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
    const cached = view === "market" && catalogState === "cached" && catalogError;
    $("notice").hidden = !cached;
    $("notice").innerHTML = cached ? '<span>暂未获取最新目录，当前显示本地缓存</span><button class="plain" data-reconnect>重试</button>' : "";
    $("catalog-state").textContent = ["ready", "cached"].includes(catalogState) ? `${refreshing ? "正在检查更新" : catalogState === "cached" ? "本地目录" : "目录已更新"}${updatedAt ? " · " + updatedAt : ""}` : "";
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
    $("refresh").disabled = refreshing || ["loading", "restoring"].includes(catalogState) || !source;
    $("search").disabled = unavailable;
    $("compatible").disabled = unavailable;
    $("hide-installed").disabled = unavailable;
    $("categories").querySelectorAll("button").forEach(button => button.disabled = unavailable);
    refreshSelect($("sort"));
    if (catalogState === "restoring") {
      $("catalog").innerHTML = "";
    } else if (unavailable) {
      const message = {unconfigured: "市场暂未开放", loading: sourceName ? `正在通过 ${sourceName} 加载` : "正在加载", error: catalogError || "无法连接市场"}[catalogState];
      $("result-count").textContent = "";
      $("catalog").innerHTML = `<div class="empty">${icon(catalogState === "error" ? "cloud" : "puzzle")}<h3 role="status">${escape(message)}</h3>${catalogState === "error" ? '<button data-reconnect>重试</button>' : ""}</div>`;
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
    const doc = documents.get(documentKey(p));
    const projectUrl = documentationUrl(p.repository);
    const links = source?.openUrl ? `${projectUrl ? `<a href="${escape(projectUrl)}" data-document-link>${icon("globe")}项目主页</a>` : ""}${doc?.url ? `<a href="${escape(doc.url)}" data-document-link>${icon("file-text")}查看原文</a>` : ""}` : "";
    const readme = doc?.state === "ready" ? `<section class="detail-readme" aria-label="项目说明"><div class="detail-readme-heading"><h3>项目说明</h3>${doc.previous ? `<span>v${escape(doc.version)}</span>` : ""}</div><div class="plugin-readme">${doc.html}</div>${doc.refreshFailed ? `<div class="detail-document-state">${escape(doc.error)}<button class="plain" data-retry-document>重试</button></div>` : ""}</section>`
      : doc?.state === "loading" ? '<p class="detail-document-state" role="status">正在加载项目说明…</p>'
      : doc?.state === "failed" ? `<div class="detail-document-state" role="status">${escape(doc.error)}<button class="plain" data-retry-document>重试</button></div>` : "";
    const description = p.description?.trim() || "";
    const body = p.body?.trim() || "";
    const versionRow = v => `<div class="version-row"><div class="version-title"><strong>v${escape(v.number)}</strong>${v.yanked ? '<span class="version-label warning">已撤回</span>' : v.prerelease ? '<span class="version-label warning">预发布</span>' : v.compatible === false ? '<span class="version-label warning">不兼容</span>' : ""}${v.date ? `<time>${escape(v.date)}</time>` : ""}</div>${v.notes?.trim() ? `<p>${escape(v.notes)}</p>` : ""}${v.yanked || v.reason ? `<p>${escape(v.yanked || v.reason)}</p>` : ""}</div>`;
    const recent = next?.notes?.trim() ? next : null;
    const history = p.versions.filter(v => v !== recent && (v.notes?.trim() || v.yanked || v.reason));
    const apiVersion = next || p.versions.find(v => v.api != null);
    const status = p.installed ? `${p.enabled ? "已启用" : "已停用"} · v${p.installed}` : next ? `v${next.number}` : "未安装";
    const manage = '<button class="secondary-button" data-manage>管理插件</button>';
    let action;
    if (task?.state === "running") action = `<button class="secondary-button" data-cancel-task ${task.phase === "installing" || task.cancelling ? "disabled" : ""}>${task.cancelling && task.phase !== "installing" ? "正在取消" : "取消安装"}</button>`;
    else if (task?.state === "failed") action = '<button data-retry>重试</button>';
    else if (updating(p)) action = `${!canInstall(p, source) ? manage : ""}<button data-install ${canInstall(p, source) ? "" : "disabled"}>更新至 ${escape(next.number)}</button>`;
    else if (p.installed) action = `${manage}${canInstall(p, source) ? '<button data-install>重新安装</button>' : ""}`;
    else action = `<button data-install ${canInstall(p, source) ? "" : "disabled"}>${next ? "安装插件" : "暂无兼容版本"}</button>`;
    let compatibility = "";
    if (!next) compatibility = `<div class="compat-note warning">${escape(p.compatibilityReason || '暂无兼容版本')}</div>`;
    else if (p.updateBlocked) compatibility = `<div class="compat-note">${escape(p.updateBlocked)}</div>`;
    else if (p.compatibilityReason) compatibility = `<div class="compat-note">${escape(p.compatibilityReason)}</div>`;
    else if (updating(p) && p.enabled) compatibility = '<div class="compat-note">更新时会短暂停止插件，完成后自动恢复启用。</div>';
    const taskMarkup = task?.state === "running"
      ? `<div class="task-state" role="status"><span class="task-label">${task.phase === "installing" ? task.reinstall ? "正在重新安装" : task.update ? "正在更新" : "正在安装" : "正在下载"}${task.source ? ` · ${escape(task.source)}` : ""} · ${Math.round(task.progress)}%</span><div class="resource-progress" role="progressbar" aria-label="安装进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${task.progress}"><span style="width:${task.progress}%"></span></div></div>`
      : task?.state === "failed" ? `<div class="task-state task-error" role="alert">${escape(task.error)}</div>` : "";
    $("detail").innerHTML = `<div class="drawer-top"><span>插件详情</span><button class="icon-button" data-close aria-label="关闭插件详情">${icon("x")}</button></div>
      <div class="drawer-scroll"><div class="drawer-identity">${mark(p)}<div><h2 id="detail-title">${escape(p.name)}</h2><div class="detail-byline">${escape([p.author, p.category === "表现" ? "角色表现" : p.category].filter(Boolean).join(" · "))}${example(p)}</div></div></div>
      ${description ? `<p class="detail-summary">${escape(description)}</p>` : ""}
      ${links ? `<div class="detail-links">${links}</div>` : ""}
      ${readme}
      ${body && body !== description ? `<section class="detail-section"><h3>介绍</h3><p>${escape(body)}</p></section>` : ""}
      ${p.consequence?.trim() ? `<section class="detail-section"><h3>使用前需了解</h3><p>${escape(p.consequence)}</p></section>` : ""}
      ${recent ? `<section class="detail-section"><h3>最近更新</h3>${versionRow(recent)}</section>` : ""}
      ${history.length ? `<details class="detail-disclosure" data-disclosure="history"><summary data-history>历史版本</summary><div class="detail-section">${history.map(versionRow).join("")}</div></details>` : ""}
      <details class="detail-disclosure" data-disclosure="technical"><summary data-technical>技术信息</summary><dl class="detail-technical"><div><dt>插件标识</dt><dd><code>${escape(p.id)}</code></dd></div>${apiVersion?.api != null ? `<div><dt>v${escape(apiVersion.number)} 接口</dt><dd>Plugin API ${escape(apiVersion.api)}</dd></div>` : ""}${next?.size ? `<div><dt>下载大小</dt><dd>${escape(next.size)}</dd></div>` : ""}</dl></details></div>
      <div class="drawer-bottom">${compatibility}${taskMarkup}<div class="drawer-actions"><span class="detail-status">${escape(status)}</span><div class="detail-buttons">${action}</div></div></div>`;
  }
  function openDetail(id) {
    closeFilters(); closeSelects();
    selected = id;
    renderDetail();
    if (!$("detail-dialog").open) $("detail-dialog").showModal();
    $("detail").querySelector(".drawer-scroll").scrollTop = 0;
    void loadDocument(plugins.find(p => p.id === id));
  }
  async function loadDocument(p, retry = false) {
    if (disposed || !source?.readme || !p?.repository) return;
    const key = documentKey(p);
    if (!retry && documents.has(key)) return;
    documents.get(key)?.abort.abort();
    const previous = [...documents.values()].reverse().find(doc => doc.id === p.id && doc.repository === p.repository && doc.state === "ready");
    const entry = { ...previous, id: p.id, repository: p.repository, previous: Boolean(previous), refreshFailed: false,
      state: previous ? "ready" : "loading", abort: new AbortController() };
    documents.set(key, entry);
    const apply = result => {
      const url = documentationUrl(result.url);
      if (!url || !result.markdown?.trim()) throw new Error("项目未提供说明。");
      entry.html = renderPluginReadme(result.markdown, url); entry.url = url; entry.state = "ready";
      entry.previous = Boolean(result.previous); entry.version = result.version || (recommended(p) || p.versions.find(v => v.package && !v.yanked))?.number;
    };
    try {
      const cached = source.peekReadme?.(p);
      if (cached) {
        apply(cached);
        if (selected === p.id) refreshDetail();
        if (!cached.previous) return;
      }
      if (selected === p.id) refreshDetail();
      const result = await source.readme(p, { signal: entry.abort.signal });
      if (disposed || entry.abort.signal.aborted || documents.get(key) !== entry) return;
      apply(result);
    } catch (error) {
      entry.error = errorText(error);
      if (disposed || entry.abort.signal.aborted || documents.get(key) !== entry) return;
      if (entry.state === "ready") entry.refreshFailed = true;
      else entry.state = "failed";
    }
    if (selected === p.id) refreshDetail();
  }
  let prefetching = false;
  async function prefetchDocuments() {
    if (!source?.prefetchReadmes || prefetching || disposed) return;
    prefetching = true;
    try {
      const worker = async () => {
        while (!disposed) {
          const next = plugins.find(p => p.repository && !documents.has(documentKey(p)));
          if (!next) return;
          await loadDocument(next);
        }
      };
      await Promise.all([worker(), worker()]);
    } finally { prefetching = false; }
  }
  function refreshDetail() {
    if (!$("detail-dialog").open) return;
    const scroll = $("detail").querySelector(".drawer-scroll")?.scrollTop || 0;
    const focus = document.activeElement;
    const expanded = [...$("detail").querySelectorAll("details[open][data-disclosure]")].map(el => el.dataset.disclosure);
    const focusAttribute = focus?.closest("#detail") ? [...focus.attributes].find(a => a.name.startsWith("data-"))?.name : null;
    renderDetail();
    for (const name of expanded) $("detail").querySelector(`[data-disclosure="${name}"]`)?.setAttribute("open", "");
    $("detail").querySelector(".drawer-scroll").scrollTop = scroll;
    if (focusAttribute) ($("detail").querySelector(`[${focusAttribute}]`) || $("detail").querySelector(".drawer-actions button"))?.focus({ preventScroll: true });
  }

  const loader = createCatalogLoader(source, result => {
    catalogState = result.state;
    refreshing = Boolean(result.refreshing);
    sourceName = result.sourceName || ""; catalogError = result.error || "";
    if (result.plugins) plugins = result.plugins;
    updatedAt = result.updatedAt || "";
    for (const p of plugins) {
      const cached = source?.peekReadme?.(p);
      if (cached && !cached.previous) void loadDocument(p);
    }
    render(); refreshDetail();
    if ($("detail-dialog").open) void loadDocument(plugins.find(p => p.id === selected));
    void prefetchDocuments();
  });
  async function startTask(id) {
    const p = plugins.find(p => p.id === id);
    const installed = tasks.get(id)?.installed;
    if ((!installed && !canInstall(p, source)) || tasks.get(id)?.state === "running") return;
    const abort = new AbortController();
    const task = { state: "running", progress: installed ? 100 : 0, phase: installed ? "installing" : "downloading", abort, installed, update: Boolean(p.installed), reinstall: Boolean(p.installed && !updating(p)) };
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
      tasks.delete(id); notify(task.reinstall ? "已重新安装" : task.update ? "已更新" : "已安装", "success");
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
    const link = event.target.closest("[data-document-link]");
    if (link) {
      event.preventDefault();
      const url = documentationUrl(link.getAttribute("href"));
      if (url && source?.openUrl) void Promise.resolve().then(() => source.openUrl(url)).catch(error => notify(errorText(error), "error"));
      return;
    }
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
    if (button.hasAttribute("data-retry-document")) void loadDocument(plugins.find(p => p.id === selected), true);
    if (button.hasAttribute("data-manage")) {
      const local = host.installedPlugins().find(p => p.pluginId === selected);
      $("detail-dialog").close(); setView("installed");
      if (local) host.openPlugin(local.installId);
    }
    if (button.hasAttribute("data-clear")) { clearFilters(); $("search").focus(); }
    if (button.hasAttribute("data-reconnect")) void loader.load();
  }
  listen(page, "click", handleClick); listen($("detail-dialog"), "click", handleClick);
  listen($("market-submit"), "click", () => {
    closeFilters(); closeSelects(); submitDialog.showModal();
  });
  $("market-submit-issue").disabled = !source?.openUrl;
  listen($("market-submit-issue"), "click", () => {
    void Promise.resolve().then(() => source.openUrl(submissionUrl)).catch(error => notify(errorText(error), "error"));
  });
  listen(submitDialog, "click", event => {
    if (event.target.closest("[data-close-submit]")) submitDialog.close();
    else if (event.target === submitDialog) {
      const rect = submitDialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) submitDialog.close();
    }
  });
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
    onPageChanged(pageName) { if (pageName !== "plugins") { closeFilters(); closeSelects($("market-surface")); $("detail-dialog").close(); submitDialog.close(); } },
    dispose() {
      disposed = true; loader.dispose();
      for (const task of tasks.values()) task.abort.abort();
      for (const entry of documents.values()) entry.abort.abort();
      listeners.forEach(remove => remove()); closeFilters(); closeSelects($("market-surface"));
      filterPanel.remove(); $("market-sources").remove(); $("refresh").remove();
      submitDialog.close(); submitDialog.remove();
      $("detail-dialog").close(); $("detail-dialog").remove(); $("market-surface").remove(); tabs.remove();
    },
  };
}
