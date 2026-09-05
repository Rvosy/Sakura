/* This prototype never invokes Tauri, fetches runtime data, or persists settings. */
(() => {
  "use strict";
  const { categories, kinds, notebookPage, createScenario } = window.PLUGIN_DEMO;
  const paths = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    chevronRight: '<path d="m9 6 6 6-6 6"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.5"/><path d="m16 16 4 4"/>',
    filter: '<path d="M4 7h16M7 12h10M10 17h4"/><circle cx="8" cy="7" r="2" fill="white"/><circle cx="16" cy="12" r="2" fill="white"/>',
    shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
    warning: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.1"/>',
    phone: '<rect x="6" y="2.5" width="12" height="19" rx="3"/><path d="M10 5h4M11 18h2"/>',
    memory: '<path d="M12 5C9 0 3 4 5 9c-4 2-3 7 1 8 0 4 6 5 6 0V5Zm0 0c3-5 9-1 7 4 4 2 3 7-1 8 0 4-6 5-6 0"/><path d="M5 9h3m-2 8 3-3m10-5h-3m2 8-3-3"/>',
    wave: '<path d="M3 10v4m4-8v12m5-16v20m5-16v12m4-8v4"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    cloud: '<path d="M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13 0 5 5 0 0 1 0 10H6Z"/>',
    tool: '<path d="M14 6a5 5 0 0 0-6 6L3 17a3 3 0 0 0 4 4l5-5a5 5 0 0 0 6-6l-3 3-4-4 3-3Z"/>',
    sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
    puzzle: '<path d="M9 4H4v5a3 3 0 1 1 0 6v5h5a3 3 0 1 1 6 0h5v-5a3 3 0 1 1 0-6V4h-5a3 3 0 1 0-6 0Z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    palette: '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="8" r="1"/><circle cx="15" cy="7" r="1"/><circle cx="7" cy="14" r="1"/><path d="M21 12h-6a3 3 0 0 0 0 6v3"/>',
    settings: '<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3H9Z"/><circle cx="12" cy="12" r="3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
  };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.puzzle}</svg>`;
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);
  let plugins = createScenario("near");
  let catalog = structuredClone(plugins);
  const state = { scenario: "near", role: "all", query: "", category: "all", status: "all", source: "all", selected: "sakura.tts.gpt-sovits", collapsed: { infrastructure: true }, pageSettings: {} };
  let configuringId = null;
  let toastTimer;
  let dialogAction;
  const sourceLabel = (plugin) => plugin.source === "bundled" ? "内置" : "用户安装";
  const find = (id) => plugins.find((p) => p.id === id);
  const known = (id) => find(id) || catalog.find((p) => p.id === id);
  const selected = () => find(state.selected);

  function effectiveStatus(plugin, visited = new Set()) {
    if (!plugin.enabled) return { state: "disabled", label: "已禁用", message: "此插件已停用。启用后才能使用它提供的能力。" };
    if (visited.has(plugin.id)) return { state: "warning", label: "依赖异常", message: "依赖组件无法使用。" };
    const next = new Set([...visited, plugin.id]);
    const unavailable = plugin.dependencies.filter((id) => {
      const dependency = find(id);
      return !dependency || ["disabled", "error", "warning"].includes(effectiveStatus(dependency, next).state);
    });
    if (unavailable.length) return { state: "warning", label: "缺少可用依赖", message: `所需组件 ${unavailable.map((id) => known(id)?.name || id).join("、")} 当前不可用。请先检查依赖组件。` };
    return { state: plugin.state, label: plugin.stateLabel, message: plugin.message };
  }
  const isProblem = (plugin) => ["error", "warning"].includes(effectiveStatus(plugin).state);
  const statusHTML = (plugin) => { const status = effectiveStatus(plugin); return `<span class="status is-${status.state}">${escape(status.label)}</span>`; };
  function filtered(ignoreRole = false) {
    return plugins.filter((p) => (ignoreRole || state.role === "all" || p.kind === state.role)
      && (state.category === "all" || p.category === state.category)
      && (state.source === "all" || p.source === state.source)
      && (state.status === "all" || (state.status === "problem" ? isProblem(p) : !p.enabled))
      && (!state.query || [p.name, p.id, p.author, p.description, p.displayAlias].join(" ").toLocaleLowerCase().includes(state.query.toLocaleLowerCase())));
  }
  function dependsOn(plugin, target, visited = new Set()) {
    if (visited.has(plugin.id)) return false;
    const next = new Set([...visited, plugin.id]);
    return plugin.dependencies.some((id) => id === target || (find(id) && dependsOn(find(id), target, next)));
  }
  const dependents = (plugin) => plugins.filter((p) => p.id !== plugin.id && dependsOn(p, plugin.id));
  const activeDependents = (plugin) => dependents(plugin).filter((p) => p.enabled);
  function hydrateIcons() { document.querySelectorAll("[data-icon]").forEach((node) => { node.innerHTML = icon(node.dataset.icon); }); }

  function renderNavigation() {
    const groups = [
      ["角色", [["角色与布局", "user"], ["外观", "palette"]]],
      ["智能", [["供应商", "cloud"], ["模型", "chip"], ["语音", "wave"], ["记忆", "memory"]]],
      ["行为", [["交互", "sparkles"], ["工具", "tool"]]],
      ["系统", [["插件", "puzzle"], ["系统", "settings"], ["关于", "info"]]],
    ];
    $("navigation").innerHTML = groups.map(([group, items]) => `<div class="nav-group"><h2>${group}</h2>${items.map(([label, glyph]) => `<button class="nav-button ${label === "插件" ? "is-active" : ""}" type="button" data-navigation="${label}" aria-label="${label}" ${label === "插件" ? 'aria-current="page"' : ""}>${icon(glyph)}<span>${label}</span></button>`).join("")}</div>`).join("");
  }
  function renderToolbar() {
    const matches = filtered(true);
    $("role-tabs").innerHTML = Object.entries({ all: "全部", ...kinds }).map(([key, label]) => `<button class="role-tab" id="role-${key}" role="tab" aria-controls="plugin-list" aria-selected="${state.role === key}" tabindex="${state.role === key ? 0 : -1}" data-role="${key}">${label}<span>${key === "all" ? matches.length : matches.filter((p) => p.kind === key).length}</span></button>`).join("");
    const issues = plugins.filter(isProblem).length;
    $("problem-shortcut").innerHTML = `${icon("warning")}<span>${issues} 个需要处理</span>`;
    $("problem-shortcut").classList.toggle("is-active", state.status === "problem");
    $("problem-shortcut").setAttribute("aria-pressed", String(state.status === "problem"));
    $("total-count").textContent = plugins.length;
    $("footer-summary").textContent = `${plugins.filter((p) => p.enabled).length} 个已启用 · ${plugins.length} 个已安装`;
    const labels = { category: categories[state.category], status: state.status === "problem" ? "有问题" : "已禁用", source: state.source === "bundled" ? "内置" : "用户安装" };
    const active = ["category", "status", "source"].filter((key) => state[key] !== "all");
    $("filter-count").hidden = !active.length;
    $("filter-count").textContent = active.length;
    const clearable = [...active];
    if (state.query) clearable.unshift("query");
    if (state.role !== "all") clearable.unshift("role");
    $("active-filters").hidden = !clearable.length;
    $("active-filters").innerHTML = clearable.map((key) => `<button class="filter-chip" type="button" data-clear="${key}" aria-label="移除${escape(key === "query" ? state.query : key === "role" ? kinds[state.role] : labels[key])}筛选">${escape(key === "query" ? `搜索：${state.query}` : key === "role" ? kinds[state.role] : labels[key])}${icon("close")}</button>`).join("") + '<button class="text-button" data-clear="all">清空筛选</button>';
  }
  function renderList() {
    const matches = filtered();
    $("system-dock").hidden = true;
    $("system-dock").textContent = "";
    $("results-count").textContent = `${matches.length} 个插件`;
    if (!matches.length) {
      $("plugin-list").innerHTML = `<div class="empty">${icon("search")}<h2>没有匹配的插件</h2><p>试试其他关键词，或减少筛选条件。</p><button class="button" data-clear="all">清空筛选</button></div>`;
      return;
    }
    $("plugin-list").innerHTML = Object.entries(kinds).map(([kind, label]) => {
      const items = matches.filter((p) => p.kind === kind);
      if (!items.length) return "";
      const issues = items.filter(isProblem).length;
      const collapsed = Boolean(state.collapsed[kind]);
      const heading = `<button class="group-header" type="button" data-collapse="${kind}" aria-expanded="${!collapsed}" aria-controls="group-${kind}">${icon("chevronDown")}<strong>${label}</strong><span class="group-count">${items.length}</span><span class="group-rule"></span>${issues ? `<span class="group-alert">${icon("warning")}${issues} 项异常</span>` : ""}</button>`;
      const docked = kind === "infrastructure" && collapsed && state.role === "all";
      if (docked) { $("system-dock").hidden = false; $("system-dock").innerHTML = heading; }
      return `<section class="group">${docked ? "" : heading}<div id="group-${kind}" ${collapsed ? "hidden" : ""}>${items.map((p) => `<button class="plugin-row ${p.id === state.selected ? "is-selected" : ""}" data-select="${escape(p.id)}" type="button" aria-pressed="${p.id === state.selected}"><span class="plugin-icon category-${p.category}">${icon(p.icon)}</span><span class="row-content"><span class="row-heading"><strong>${escape(p.name)}</strong>${p.planned || p.sample ? `<span class="mini-source">${p.planned ? "规划示例" : "生态示例"}</span>` : ""}</span><span class="row-description">${escape(p.description)}</span><span class="row-footer"><span class="row-subline">${categories[p.category]}<span class="separator">·</span>${sourceLabel(p)}</span>${statusHTML(p)}</span></span></button>`).join("")}</div></section>`;
    }).join("");
  }
  function dependencyHTML(id) {
    const dependency = find(id);
    if (!dependency) return `<div class="dependency-link">${icon("layers")}<span class="dependency-name">${escape(known(id)?.name || id)}<small>尚未安装</small></span><span class="status is-warning">不可用</span></div>`;
    return `<button class="dependency-link" type="button" data-jump="${escape(id)}">${icon(dependency.icon)}<span class="dependency-name">${escape(dependency.name)}<small>${kinds[dependency.kind]} · ${categories[dependency.category]}</small></span>${statusHTML(dependency)}${icon("chevronRight")}</button>`;
  }
  function renderOverview(plugin) {
    const uses = dependents(plugin);
    return `<div class="enabled-row"><div><h3 class="section-label">启用插件</h3><p>允许 Sakura 使用此插件的能力</p></div><div class="switch-wrap"><span>${plugin.enabled ? "已启用" : "已禁用"}</span><button class="switch" type="button" role="switch" aria-label="启用 ${escape(plugin.name)}" aria-checked="${plugin.enabled}" data-toggle="${escape(plugin.id)}"></button></div></div>
      ${plugin.settingsPage ? `<button class="config-link" type="button" data-configure="${escape(plugin.id)}" aria-haspopup="dialog"><span>${icon("settings")}${escape(plugin.settingsPage.title || "插件设置")}</span><span class="config-link-caption">打开窗口${icon("chevronRight")}</span></button>` : ""}
      ${plugin.capabilities.length ? `<section class="detail-section"><h3>提供的能力</h3><div class="capabilities">${plugin.capabilities.map((name) => `<span class="capability">${escape(name)}</span>`).join("")}</div></section>` : ""}
      ${plugin.dependencies.length ? `<section class="detail-section"><h3>依赖组件<span class="muted">${plugin.dependencies.length}</span></h3>${plugin.dependencies.map(dependencyHTML).join("")}</section>` : ""}
      ${uses.length || plugin.kind === "infrastructure" ? `<section class="detail-section"><h3>被以下插件依赖<span class="muted">${uses.length}</span></h3>${uses.length ? uses.map((p) => dependencyHTML(p.id)).join("") : '<p class="muted" style="font-size:11px">目前没有插件依赖此组件。</p>'}</section>` : ""}
      <dl class="meta"><div><dt>版本</dt><dd>v${escape(plugin.version)}</dd></div><div><dt>作者</dt><dd>${escape(plugin.author)}</dd></div><div><dt>安装来源</dt><dd>${plugin.source === "bundled" ? "Sakura 内置" : "用户安装"}</dd></div><div><dt>插件角色</dt><dd>${kinds[plugin.kind]}</dd></div></dl>
      <details class="technical"><summary>技术信息</summary><dl><dt>插件 ID</dt><dd><code>${escape(plugin.id)}</code></dd>${plugin.provides.length ? `<dt>提供的服务</dt><dd>${plugin.provides.map((key) => `<code>${escape(key)}</code>`).join("")}</dd>` : ""}${plugin.hostServices.length ? `<dt>宿主能力</dt><dd>${plugin.hostServices.map((key) => `<code>${escape(key)}</code>`).join("")}</dd>` : ""}${plugin.reason ? `<dt>演示诊断代码</dt><dd><code>${escape(plugin.reason)}</code></dd>` : ""}</dl></details>
      ${plugin.source === "user" ? `<div class="detail-actions"><button class="button quiet-danger" type="button" data-uninstall="${escape(plugin.id)}">卸载插件</button></div>` : ""}`;
  }
  function renderDetail() {
    const plugin = selected();
    if (!plugin) { $("plugin-detail").innerHTML = `<div class="empty">${icon("puzzle")}<h2>${filtered().length ? "选择一个插件" : "没有可查看的插件"}</h2><p>${filtered().length ? "展开左侧分组，选择一个插件查看详情。" : "调整左侧筛选后，选择一个插件查看详情。"}</p></div>`; return; }
    const status = effectiveStatus(plugin);
    const notice = ["error", "warning"].includes(status.state) && status.message
      ? `<div class="health-notice is-${status.state}">${icon("warning")}<p>${escape(status.message)}</p></div>` : "";
    $("plugin-detail").innerHTML = `<header class="detail-header"><div class="detail-topline"><span class="plugin-icon detail-icon category-${plugin.category}">${icon(plugin.icon)}</span><div class="detail-title"><h2>${escape(plugin.name)}</h2><div class="detail-badges"><span>${categories[plugin.category]}</span><span>${sourceLabel(plugin)}</span>${plugin.planned || plugin.sample ? `<span class="planned">${plugin.planned ? "规划示例" : "生态示例"}</span>` : ""}</div><div class="detail-live-status" aria-label="运行状态">${statusHTML(plugin)}</div></div></div><p class="detail-description">${escape(plugin.description)}${plugin.displayAlias ? ` <span class="muted">${escape(plugin.displayAlias)}</span>` : ""}</p>${notice}</header><div id="detail-content">${renderOverview(plugin)}</div>`;
  }
  function render() {
    const matches = filtered();
    if (!matches.some((p) => p.id === state.selected)) {
      state.selected = matches.find((p) => !state.collapsed[p.kind])?.id || null;

      $("plugin-detail").scrollTop = 0;
    }
    renderToolbar(); renderList(); renderDetail();
    revealSelection();
  }
  function revealSelection() {
    const row = [...document.querySelectorAll("[data-select]")].find((node) => node.dataset.select === state.selected);
    if (!row || !row.getClientRects().length) return;
    const list = $("plugin-list");
    const bounds = list.getBoundingClientRect();
    const item = row.getBoundingClientRect();
    if (item.top < bounds.top) list.scrollTop -= bounds.top - item.top;
    else if (item.bottom > bounds.bottom) list.scrollTop += item.bottom - bounds.bottom;
  }
  function syncFilters() {
    $("search").value = state.query;
    for (const key of ["category", "status", "source"]) $(key).value = state[key];
  }
  function expandMatches() {
    if (state.query || state.status === "problem" || state.role !== "all" || state.category !== "all" || state.source !== "all") filtered().forEach((p) => { state.collapsed[p.kind] = false; });
  }
  function clearFilters(key = "all") {
    if (key === "all") { for (const field of ["role", "category", "status", "source"]) state[field] = "all"; state.query = ""; }
    else state[key] = key === "query" ? "" : "all";
    syncFilters(); expandMatches(); render();
  }
  function selectPlugin(id, jump = false) {
    if (!find(id)) return;
    if (jump) { for (const key of ["role", "category", "status", "source"]) state[key] = "all"; state.query = ""; syncFilters(); }
    state.selected = id;
    state.collapsed[find(id).kind] = false;
    render();
    $("plugin-detail").scrollTop = 0;
    const row = [...document.querySelectorAll("[data-select]")].find((node) => node.dataset.select === id);
    row?.focus({ preventScroll: true });
    if (jump) row?.scrollIntoView({ block: "nearest" });
    if (window.innerWidth <= 620 && !jump) $("plugin-detail").scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  function toast(message) { window.clearTimeout(toastTimer); $("toast").textContent = message; $("toast").hidden = false; toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, 3200); }
  function closePopover(name) { $(name === "filter" ? "filter-panel" : "install-menu").hidden = true; $(`${name}-trigger`).setAttribute("aria-expanded", "false"); }
  function openDialog({ title, body, confirm = "知道了", danger = false, action = null }) {
    closePopover("filter"); closePopover("install");
    $("dialog-title").textContent = title;
    $("dialog-body").innerHTML = body;
    $("dialog-icon").innerHTML = icon(danger ? "warning" : "info");
    $("dialog-icon").classList.toggle("is-danger", danger);
    $("dialog-confirm").textContent = confirm;
    $("dialog-confirm").className = `button ${danger ? "danger" : "primary"}`;
    $("dialog-cancel").hidden = !action;
    dialogAction = action;
    $("dialog").returnValue = "";
    $("dialog").showModal();
    (action ? $("dialog-cancel") : $("dialog-confirm")).focus();
  }
  const demoNote = '<p class="dialog-note">这是布局演示。操作仅改变本页样例，不会影响 Sakura。</p>';
  function togglePlugin(plugin) {
    const turnOn = !plugin.enabled;
    const impacted = activeDependents(plugin);
    const apply = () => { plugin.enabled = turnOn; render(); document.querySelector("[data-toggle]")?.focus({ preventScroll: true }); toast(`已模拟${turnOn ? "启用" : "停用"} ${plugin.name}`); };
    if (!turnOn && impacted.length) openDialog({ title: `停用“${plugin.name}”？`, body: `<p>以下 ${impacted.length} 个已启用插件依赖此组件，停用后将无法使用相关能力：</p><ul>${impacted.map((p) => `<li>${escape(p.name)}</li>`).join("")}</ul><p>它们的启用开关会保留，运行状态将显示依赖不可用。</p>${demoNote}`, confirm: "模拟停用", danger: true, action: apply });
    else apply();
  }
  function uninstallPlugin(plugin) {
    const impacted = activeDependents(plugin);
    openDialog({ title: `卸载“${plugin.name}”？`, body: `<p>将从当前演示列表移除此插件。重置演示可恢复。</p>${impacted.length ? `<p>以下插件的依赖将不可用：</p><ul>${impacted.map((p) => `<li>${escape(p.name)}</li>`).join("")}</ul>` : ""}${demoNote}`, confirm: "模拟卸载", danger: true, action: () => { plugins = plugins.filter((p) => p.id !== plugin.id); render(); $("search").focus(); toast("已从演示列表移除插件"); } });
  }
  function showDestination(domain) {
    const label = { model: "模型", voice: "语音", memory: "记忆" }[domain];
    const text = { model: "在模型页选择当前模型，管理模型下载或 API 连接。", voice: "在语音页选择语音引擎、角色音色，并配置服务。", memory: "在记忆页管理记忆内容、检索方式与相关设置。" }[domain];
    openDialog({ title: `前往${label}设置`, body: `<p>${text}</p><div class="destination">智能 &nbsp; / &nbsp; ${label}${selected() ? ` &nbsp; / &nbsp; ${escape(selected().name)}` : ""}</div><p>此原型只展示插件管理页，这里用于确认配置入口的位置。</p>` });
  }
  function configurationGroups(plugin) { return plugin.settingsPage?.sections || []; }
  function configField(field, values) {
    const value = values[field.key] ?? field.value;
    const id = `config-${field.key}`;
    const bounds = ["min", "max", "step"].filter((key) => field[key] !== undefined).map((key) => `${key}="${field[key]}"`).join(" ");
    const common = `id="${id}" name="${field.key}" ${field.optional ? "" : "required"}`;
    let control;
    if (field.type === "select") control = `<select ${common}>${field.options.map(([key, label]) => `<option value="${escape(key)}" ${String(value) === key ? "selected" : ""}>${escape(label)}</option>`).join("")}</select>`;
    else if (field.type === "textarea") control = `<textarea ${common}>${escape(value)}</textarea>`;
    else if (field.type === "checkbox") return `<label class="config-checkbox${field.wide ? " is-wide" : ""}"><span><strong>${escape(field.label)}</strong>${field.hint ? `<small>${escape(field.hint)}</small>` : ""}</span><input type="checkbox" id="${id}" name="${field.key}" ${value ? "checked" : ""}></label>`;
    else if (field.type === "range") control = `<div class="config-range"><input type="range" ${common} ${bounds} value="${escape(value)}" data-unit="${escape(field.unit || "")}" style="--range-progress:${(Number(value) - field.min) / (field.max - field.min) * 100}%"><output for="${id}">${Number(value).toFixed(2)}${escape(field.unit || "")}</output></div>`;
    else control = `<input type="${field.type}" ${common} ${bounds} value="${escape(value)}" placeholder="${escape(field.placeholder || "")}" ${field.type === "password" ? 'autocomplete="new-password"' : ""}>`;
    return `<label class="config-field${field.wide ? " is-wide" : ""}" ${field.when ? `data-config-when="${field.when.key}" data-config-value="${field.when.value}"` : ""}><span>${escape(field.label)}</span>${control}${field.hint ? `<small>${escape(field.hint)}</small>` : ""}</label>`;
  }
  function syncConfigFields() {
    $("config-body").querySelectorAll("[data-config-when]").forEach((row) => {
      const show = $("config-form").elements.namedItem(row.dataset.configWhen)?.value === row.dataset.configValue;
      row.hidden = !show;
      row.querySelectorAll("input,select,textarea").forEach((input) => { input.disabled = !show; });
    });
  }
  function openConfiguration(plugin) {
    if (!plugin?.settingsPage) return;
    closePopover("filter"); closePopover("install");
    configuringId = plugin.id;
    const values = state.pageSettings[plugin.id] || {};
    $("config-identity").innerHTML = `<span class="plugin-icon detail-icon category-${plugin.category}">${icon(plugin.icon)}</span><div><p class="config-eyebrow">${escape(plugin.settingsPage.title || "插件设置")}</p><h2 id="config-title">${escape(plugin.name)}</h2><p id="config-description">${escape(plugin.settingsPage.description || "调整此插件的详细设置")}</p></div>`;
    $("config-body").innerHTML = configurationGroups(plugin).map((group) => {
      const fields = `<div class="config-grid">${group.fields.map((field) => configField(field, values)).join("")}</div>`;
      return group.advanced ? `<details class="config-advanced"><summary>${escape(group.title)}<span>${escape(group.description || "更多选项")}</span></summary>${fields}</details>` : `<fieldset class="config-section"><legend>${escape(group.title)}</legend>${fields}</fieldset>`;
    }).join("");
    syncConfigFields();
    $("config-dialog").showModal();
    $("config-body").scrollTop = 0;
    $("config-body").querySelector("input:not(:disabled),select:not(:disabled),textarea:not(:disabled)")?.focus({ preventScroll: true });
  }
  function saveConfiguration(event) {
    event.preventDefault();
    const plugin = find(configuringId);
    if (!plugin) return;
    const invalid = $("config-form").querySelector("input:invalid,select:invalid,textarea:invalid");
    if (invalid) {
      const advanced = invalid.closest("details");
      if (advanced) advanced.open = true;
      invalid.reportValidity(); invalid.focus(); return;
    }
    const values = { ...state.pageSettings[plugin.id] };
    configurationGroups(plugin).flatMap((group) => group.fields).forEach((field) => {
      const input = $("config-form").elements.namedItem(field.key);
      // Keep conditional fields in the draft without validating inactive controls.
      values[field.key] = field.type === "checkbox" ? input.checked : ["range", "number"].includes(field.type) ? Number(input.value) : input.value;
    });
    state.pageSettings[plugin.id] = values;
    $("config-dialog").close();
    toast(`${plugin.name} 的演示设置已保存`);
  }
  function resetScenario() {
    plugins = createScenario(state.scenario); catalog = structuredClone(plugins);
    Object.assign(state, { role: "all", query: "", category: "all", status: "all", source: "all", selected: "sakura.tts.gpt-sovits", collapsed: { infrastructure: true }, pageSettings: {} });
    closePopover("filter"); closePopover("install"); syncFilters(); render();
    $("plugin-list").scrollTop = 0; revealSelection(); $("plugin-detail").scrollTop = 0;
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!event.target.closest(".filter-wrap")) closePopover("filter");
    if (!event.target.closest(".install-wrap")) closePopover("install");
    if (!target) return;
    const data = target.dataset;
    if (data.role) { state.role = data.role; expandMatches(); render(); $(`role-${data.role}`).focus(); }
    else if (data.clear) { clearFilters(data.clear); $("search").focus(); }
    else if (data.collapse) {
      state.collapsed[data.collapse] = !state.collapsed[data.collapse];
      if (state.collapsed[data.collapse] && selected()?.kind === data.collapse) state.selected = null;
      else if (!state.collapsed[data.collapse] && data.collapse === "infrastructure") { state.selected = filtered().find((p) => p.kind === "infrastructure")?.id || null;  }
      render(); document.querySelector(`[data-collapse="${data.collapse}"]`)?.focus();
    }
    else if (data.select) selectPlugin(data.select);
    else if (data.jump) selectPlugin(data.jump, true);
    else if (data.toggle && find(data.toggle)) togglePlugin(find(data.toggle));
    else if (data.configure) openConfiguration(find(data.configure));
    else if (target.hasAttribute("data-config-close")) $("config-dialog").close();
    else if (data.uninstall && find(data.uninstall)) uninstallPlugin(find(data.uninstall));
    else if (data.navigation) {
      if (data.navigation === "插件") return;
      const domain = Object.keys(categories).find((key) => categories[key] === data.navigation);
      if (["model", "voice", "memory"].includes(domain)) showDestination(domain);
      else openDialog({ title: `${data.navigation}设置`, body: `<p>这里保留现有设置导航作为布局参照。本次原型只实现插件管理页。</p>` });
    }
    else if (data.install) openDialog({ title: `从${data.install}安装`, body: `<p>正式页面会在这里选择本地${data.install}。演示中将添加一个“Notebook Companion”样例插件。</p><p class="dialog-note">插件拥有与 Sakura 相同的本机权限，仅安装你信任的插件。</p>${demoNote}`, confirm: "添加示例插件", action: () => {
      let count = 1; while (find(`demo.installed.${count}`)) count++;
      const plugin = { id: `demo.installed.${count}`, name: count === 1 ? "Notebook Companion" : `Notebook Companion ${count}`, kind: "extension", category: "tools", author: "Demo Studio", description: "把对话片段整理到笔记中。", source: "user", version: "1.0.0", enabled: false, state: "ready", stateLabel: "运行正常", message: "", dependencies: [], provides: [], hostServices: [], capabilities: ["笔记整理"], icon: "tool", sample: true, settingsPage: structuredClone(notebookPage) };
      plugins.push(plugin); catalog.push(structuredClone(plugin)); selectPlugin(plugin.id, true); toast("已添加示例插件，尚未启用");
    } });
  });
  $("search").addEventListener("input", () => { state.query = $("search").value.trim(); expandMatches(); render(); });
  for (const key of ["category", "status", "source"]) $(key).addEventListener("change", () => { state[key] = $(key).value; expandMatches(); render(); });
  for (const name of ["filter", "install"]) $(`${name}-trigger`).addEventListener("click", () => {
    const panel = $(name === "filter" ? "filter-panel" : "install-menu");
    const open = panel.hidden; closePopover(name === "filter" ? "install" : "filter");
    panel.hidden = !open; $(`${name}-trigger`).setAttribute("aria-expanded", String(open));
    if (open) (name === "filter" ? $("category") : panel.querySelector("button"))?.focus();
  });
  $("clear-popover").addEventListener("click", () => { for (const key of ["category", "status", "source"]) state[key] = "all"; syncFilters(); render(); });
  $("problem-shortcut").addEventListener("click", () => { state.status = state.status === "problem" ? "all" : "problem"; syncFilters(); expandMatches(); render(); });
  $("scenario").addEventListener("change", () => { state.scenario = $("scenario").value; resetScenario(); });
  $("reset").addEventListener("click", () => { resetScenario(); toast("当前场景已恢复初始状态"); });
  $("dialog").addEventListener("close", () => { const action = dialogAction; dialogAction = null; if ($("dialog").returnValue === "confirm") action?.(); });
  $("config-form").addEventListener("submit", saveConfiguration);
  $("config-form").addEventListener("change", syncConfigFields);
  $("config-form").addEventListener("input", (event) => {
    if (event.target.type === "range") {
      const input = event.target;
      input.nextElementSibling.value = `${Number(input.value).toFixed(2)}${input.dataset.unit}`;
      input.style.setProperty("--range-progress", `${(Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100}%`);
    }
  });
  $("config-dialog").addEventListener("close", () => { configuringId = null; });
  document.addEventListener("keydown", (event) => {
    const editing = /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable;
    const modalOpen = $("dialog").open || $("config-dialog").open;
    if (event.key === "/" && !editing && !modalOpen) { event.preventDefault(); $("search").focus(); }
    if (event.key === "Escape" && !modalOpen) {
      if (!$("filter-panel").hidden) { closePopover("filter"); $("filter-trigger").focus(); }
      if (!$("install-menu").hidden) { closePopover("install"); $("install-trigger").focus(); }
    }
    const tab = event.target.closest('[role="tab"]');
    if (tab && ["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
      const current = tabs.indexOf(tab);
      const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[index].click();
    }
    if (event.target.closest("#install-menu") && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const items = [...$("install-menu").querySelectorAll("button")];
      const current = items.indexOf(document.activeElement);
      const index = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[index].focus();
    }
  });
  $("category").insertAdjacentHTML("beforeend", Object.entries(categories).map(([key, label]) => `<option value="${key}">${label}</option>`).join(""));
  hydrateIcons(); renderNavigation(); render();
})();
