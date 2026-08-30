const TOOL_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const ICONS = new Set([
  "camera", "folder", "globe", "link", "note", "settings", "sparkles", "terminal",
]);
const MAX_VISIBLE_TOOLS = 4;

function exactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

export function validateComposerToolsSnapshot(value) {
  if (!exactKeys(value, ["schemaVersion", "coreGenerationId", "tools"])
      || value.schemaVersion !== 1
      || typeof value.coreGenerationId !== "string"
      || !value.coreGenerationId
      || !Array.isArray(value.tools)
      || value.tools.length > 64) {
    throw new Error("COMPOSER_TOOLS_RESPONSE_INVALID");
  }
  const ids = new Set();
  const tools = value.tools.map((tool) => {
    if (!exactKeys(tool, ["id", "pluginId", "toolId", "label", "description", "icon", "order"])
        || typeof tool.id !== "string"
        || !TOOL_ID.test(tool.id)
        || typeof tool.pluginId !== "string"
        || !SEGMENT.test(tool.pluginId)
        || typeof tool.toolId !== "string"
        || !SEGMENT.test(tool.toolId)
        || tool.id !== `${tool.pluginId}:${tool.toolId}`
        || typeof tool.label !== "string"
        || !tool.label
        || tool.label.length > 40
        || typeof tool.description !== "string"
        || tool.description.length > 120
        || !ICONS.has(tool.icon)
        || typeof tool.order !== "number"
        || !Number.isFinite(tool.order)
        || ids.has(tool.id)) {
      throw new Error("COMPOSER_TOOLS_RESPONSE_INVALID");
    }
    ids.add(tool.id);
    return Object.freeze({ ...tool });
  });
  return Object.freeze({
    schemaVersion: 1,
    coreGenerationId: value.coreGenerationId,
    tools: Object.freeze(tools),
  });
}

const ICON_PATHS = Object.freeze({
  camera: [["path", { d: "M4 8.5h3l1.6-2h6.8l1.6 2h3v9.5H4V8.5Z" }], ["circle", { cx: "12", cy: "13", r: "3" }]],
  folder: [["path", { d: "M3.5 7.5h6l1.7 2H20v8.5H3.5V7.5Z" }]],
  globe: [["circle", { cx: "12", cy: "12", r: "8" }], ["path", { d: "M4 12h16M12 4c2.2 2.3 3.2 5 3.2 8S14.2 17.7 12 20M12 4c-2.2 2.3-3.2 5-3.2 8s1 5.7 3.2 8" }]],
  link: [["path", { d: "m9.5 14.5 5-5M8 16H6.7a3.7 3.7 0 0 1 0-7.4H10M14 8h3.3a3.7 3.7 0 0 1 0 7.4H14" }]],
  note: [["path", { d: "M6 3.5h9l3 3V20H6V3.5ZM15 3.5V7h3M9 11h6M9 15h5" }]],
  settings: [["circle", { cx: "12", cy: "12", r: "3" }], ["path", { d: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" }]],
  sparkles: [["path", { d: "m12 3 1.3 4.1L17 9l-3.7 1.9L12 15l-1.3-4.1L7 9l3.7-1.9L12 3ZM18.5 14l.7 2.1L21 17l-1.8.9-.7 2.1-.7-2.1L16 17l1.8-.9.7-2.1Z" }]],
  terminal: [["path", { d: "m5 7 4 4-4 4M11 16h7" }]],
});

function appendIcon(document, target, icon) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("focusable", "false");
  for (const [tag, attributes] of ICON_PATHS[icon]) {
    const shape = document.createElementNS(namespace, tag);
    for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, value);
    svg.append(shape);
  }
  target.append(svg);
}

function toolButton(document, tool, activate) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "composer-tool-dock__item";
  button.dataset.pluginTool = "true";
  button.dataset.toolId = tool.id;
  button.setAttribute("role", "menuitem");
  button.tabIndex = -1;
  button.title = tool.description || tool.label;

  const icon = document.createElement("span");
  icon.className = "composer-tool-dock__icon";
  icon.dataset.toolIcon = tool.icon;
  icon.setAttribute("aria-hidden", "true");
  appendIcon(document, icon, tool.icon);

  const copy = document.createElement("span");
  copy.className = "composer-tool-dock__copy";
  const label = document.createElement("strong");
  label.textContent = tool.label;
  const description = document.createElement("small");
  description.textContent = tool.description || "插件扩展";
  copy.append(label, description);
  button.append(icon, copy);
  button.addEventListener("click", () => { void activate(tool, button); });
  return button;
}

export function createComposerToolRegistry({
  list,
  invoke,
  beforeActivate = async () => {},
  afterActivate = async () => {},
  onError = () => {},
} = {}) {
  if (!list || typeof invoke !== "function") {
    throw new Error("composer tool registry requires complete dependencies");
  }
  const document = list.ownerDocument;
  let disposed = false;
  let revision = 0;

  function syncScrollableState() {
    const toolCount = list.querySelectorAll?.(".composer-tool-dock__item").length || 0;
    list.dataset.scrollable = toolCount > MAX_VISIBLE_TOOLS ? "true" : "false";
  }

  function clearPluginTools() {
    for (const item of list.querySelectorAll?.("[data-plugin-tool='true']") || []) item.remove();
    syncScrollableState();
  }

  async function activate(tool, button) {
    if (disposed || button.disabled) return false;
    button.disabled = true;
    try {
      await beforeActivate();
      await invoke("composer_tool_invoke", { toolId: tool.id });
      await afterActivate(tool);
      return true;
    } catch {
      onError(`“${tool.label}”暂时无法运行，请重试。`);
      return false;
    } finally {
      button.disabled = false;
    }
  }

  async function refresh() {
    const current = ++revision;
    try {
      const snapshot = validateComposerToolsSnapshot(await invoke("composer_tools_get"));
      if (disposed || current !== revision) return false;
      clearPluginTools();
      for (const tool of snapshot.tools) list.append(toolButton(document, tool, activate));
      syncScrollableState();
      return true;
    } catch {
      if (!disposed && current === revision) clearPluginTools();
      return false;
    }
  }

  return Object.freeze({
    refresh,
    invalidate() {
      revision += 1;
      clearPluginTools();
    },
    dispose() {
      disposed = true;
      revision += 1;
      clearPluginTools();
    },
  });
}
