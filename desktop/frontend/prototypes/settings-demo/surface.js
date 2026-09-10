import { enhanceSelect, refreshSelect } from "../../settings/select-control.js";
import { visualPlugin } from "./data.js";

export function createSurface(win, state, version) {
  const doc = win.document;
  let api = null,
    control = null,
    status = null,
    config = null;
  const drafts = {};
  const $ = (id) => doc.getElementById(id);
  const current = () =>
    state.characters.find(
      (c) => c.id === ($("characterSelect")?.value || state.current),
    ) || state.characters[0];
  const choice = (c = current()) =>
    drafts[c.id] ?? state.visuals[c.id] ?? "inherit";
  const resource = (c = current()) =>
    c.visuals.find(
      (v) => v.id === (choice(c) === "inherit" ? c.defaultVisual : choice(c)),
    );
  const dependency = (r = resource()) =>
    state.plugins.find((p) => p.pluginId === r?.pluginId);
  const isDirty = () =>
    version !== "original" &&
    Object.keys(drafts).some(
      (id) => drafts[id] !== (state.visuals[id] ?? "inherit"),
    );
  function dialog(title, body, buttons = [{ label: "关闭" }]) {
    const el = doc.createElement("dialog");
    el.className = "demo-dialog";
    el.setAttribute("aria-label", title);
    const h = doc.createElement("h2");
    h.textContent = title;
    el.append(h);
    if (typeof body === "string") {
      const p = doc.createElement("p");
      p.textContent = body;
      el.append(p);
    } else el.append(body);
    const footer = doc.createElement("footer");
    for (const item of buttons) {
      const b = doc.createElement("button");
      b.type = "button";
      b.className = item.primary ? "primary-button" : "secondary-button";
      b.textContent = item.label;
      b.onclick = async () => {
        if (item.action && (await item.action(el)) === false) return;
        el.close();
      };
      footer.append(b);
    }
    el.append(footer);
    doc.body.append(el);
    el.addEventListener("close", () => el.remove(), { once: true });
    el.addEventListener("click", (e) => {
      if (e.target === el) {
        const b = el.getBoundingClientRect();
        if (
          e.clientX < b.left ||
          e.clientX > b.right ||
          e.clientY < b.top ||
          e.clientY > b.bottom
        )
          el.close();
      }
    });
    el.showModal();
    return el;
  }
  function info(title, text) {
    dialog(title, text);
  }
  function render() {
    if (!control) return;
    const c = current(),
      selected = choice(c);
    control.replaceChildren();
    const add = (value, label) => {
      const o = doc.createElement("option");
      o.value = value;
      o.textContent = label;
      control.append(o);
    };
    add(
      "inherit",
      c.visuals.length === 1
        ? c.visuals[0].label
        : "跟随角色包（" +
            (c.visuals.find((v) => v.id === c.defaultVisual)?.label || "立绘") +
            "）",
    );
    if (c.visuals.length > 1) c.visuals.forEach((v) => add(v.id, v.label));
    control.value = selected;
    refreshSelect(control);
    const r = resource(),
      p = dependency(r);
    status.hidden = !!p?.enabled;
    $("visualStatusText").textContent = !p
      ? "使用" + (r?.label || "此表现") + "需要安装对应插件。"
      : p.enabled
        ? ""
        : p.name + "插件尚未启用。";
    $("visualPluginAction").textContent = p ? "启用插件" : "查看插件";
    config.disabled = !p?.enabled;
    config.textContent = "设置";
    const scaleLabel = doc.querySelector(
      'label[for="portraitScale"] .setting-title',
    );
    if (scaleLabel)
      scaleLabel.textContent = r?.type === "portrait" ? "立绘大小" : "角色大小";
  }
  async function ensurePlugin() {
    const r = resource(),
      p = dependency(r);
    const name =
      r.type === "live2d" ? "Live2D" : r.type === "vrm" ? "3D 模型" : "立绘";
    const body = doc.createElement("div");
    const s = doc.createElement("div");
    s.className = "demo-plugin-state";
    s.textContent = p ? "已安装 · 尚未启用" : "尚未安装";
    body.append(s);
    const desc = doc.createElement("p");
    desc.textContent = name + "插件用于显示这个角色包中的" + name + "资源。";
    body.append(desc);
    dialog(name + "插件", body, [
      { label: "取消" },
      {
        label: p ? "启用" : "安装并启用",
        primary: true,
        action: async (el) => {
          const b = el.querySelector("footer button:last-child");
          b.disabled = true;
          b.textContent = p ? "正在启用…" : "正在安装…";
          await new Promise((r) => setTimeout(r, 450));
          if (p) {
            p.enabled = true;
            p.state = "active";
            p.reasonCode = "ACTIVE";
          } else state.plugins.push(visualPlugin(r.type));
          state.revision++;
          await api?.refreshPlugins();
          render();
          api?.notify(name + "插件已就绪（演示）", "success");
          api?.refreshDirty();
        },
      },
    ]);
  }
  function configure() {
    const p = dependency();
    if (!p) return;
    // Reuse the real plugin settings dialog and staged save semantics.
    api.showPage("plugins");
    const card = doc.querySelector(
      '.plugin-card[data-plugin-install-id="' + p.installId + '"]',
    );
    card?.click();
    doc.querySelector("#pluginDetail .plugin-configure")?.click();
  }
  function studio(characterId) {
    const c = state.characters.find((c) => c.id === characterId) || current();
    info(
      "角色工坊",
      "正式程序会在这里打开“" +
        c.name +
        "”的角色工坊。本次演示用于评审设置界面，工坊页面留待后续。",
    );
  }
  function attach(next) {
    api = next;
    if (version === "original") return;
    const row = doc.createElement("div");
    row.className = "setting-row";
    row.id = "visualSetting";
    row.innerHTML =
      '<label class="setting-row-text" for="visualSelect"><span class="setting-title">表现形式</span></label><div class="character-select-controls visual-choice-controls"><select id="visualSelect"></select><button id="visualConfigure" class="secondary-button" type="button">设置</button></div>';
    $("characterSelect").closest(".setting-row").after(row);
    status = doc.createElement("div");
    status.className = "visual-status-row";
    status.id = "visualStatus";
    status.hidden = true;
    status.innerHTML =
      '<span id="visualStatusText" role="status"></span><button id="visualPluginAction" class="secondary-button" type="button">查看插件</button>';
    row.after(status);
    control = $("visualSelect");
    config = $("visualConfigure");
    render();
    enhanceSelect(control);
    control.addEventListener("change", () => {
      drafts[current().id] = control.value;
      render();
      api.refreshDirty();
    });
    $("characterSelect").addEventListener("change", () => {
      queueMicrotask(render);
    });
    $("visualPluginAction").onclick = ensurePlugin;
    config.onclick = configure;
    win.addEventListener("focus", render);
  }
  async function validate() {
    if (version === "original") return;
    const p = dependency();
    if (!p?.enabled)
      throw Error(
        p
          ? "请先启用" + p.name + "插件，或选择其他表现形式。"
          : "请先安装所需插件，或选择其他表现形式。",
      );
  }
  async function save() {
    if (version === "original") return;
    for (const [id, value] of Object.entries(drafts)) state.visuals[id] = value;
    Object.keys(drafts).forEach((id) => delete drafts[id]);
    render();
    api?.refreshDirty();
  }
  function discard() {
    Object.keys(drafts).forEach((id) => delete drafts[id]);
    render();
  }
  return { attach, render, isDirty, validate, save, discard, info, studio };
}
